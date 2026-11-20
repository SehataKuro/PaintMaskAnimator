//! 行優先のストレート RGBA8888 画像。

use crate::Error;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc,
};

const CHANNELS: usize = 4;
static LAST_GENERATION: AtomicU64 = AtomicU64::new(0);

fn next_generation() -> Result<u64, Error> {
    LAST_GENERATION
        .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |previous| {
            previous.checked_add(1)
        })
        .map(|previous| previous + 1)
        .map_err(|_| Error::ImageGenerationExhausted)
}

fn byte_len(width: usize, height: usize) -> Result<usize, Error> {
    width
        .checked_mul(height)
        .and_then(|area| area.checked_mul(CHANNELS))
        .filter(|&len| len <= isize::MAX as usize)
        .ok_or(Error::Image("画素バッファの寸法が上限を超えています"))
}

fn reserve_pixels(len: usize) -> Result<Vec<u8>, Error> {
    let mut pixels = Vec::new();
    pixels
        .try_reserve_exact(len)
        .map_err(|_| Error::Image("画素バッファを確保できません"))?;
    Ok(pixels)
}

fn clone_pixels(source: &[u8]) -> Result<Vec<u8>, Error> {
    let mut pixels = reserve_pixels(source.len())?;
    pixels.extend_from_slice(source);
    Ok(pixels)
}

/// 左上を原点とする、画素単位の矩形。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    /// 左端の x 座標。
    pub x: usize,
    /// 上端の y 座標。
    pub y: usize,
    /// 矩形の幅。
    pub width: usize,
    /// 矩形の高さ。
    pub height: usize,
}

/// NumPy の C 連続な `uint8[h, w, 4]` と同じ順序の画像。
///
/// 複製は画素と世代を共有する。書き込むときに共有を分離する。
/// 幅か高さが 0 なら、指定された寸法を保ち、画素は空とする。
#[derive(Clone, Debug)]
pub struct RgbaImage {
    width: usize,
    height: usize,
    pixels: Arc<Vec<u8>>,
    generation: u64,
}

impl RgbaImage {
    /// 全チャンネルが 0 の画像を返す。NumPy の `zeros((h, w, 4), uint8)` に相当する。
    pub fn blank(width: usize, height: usize) -> Result<Self, Error> {
        let len = byte_len(width, height)?;
        let mut pixels = reserve_pixels(len)?;
        pixels.resize(len, 0);
        Self::new(width, height, pixels)
    }

    /// 行優先の RGBA バイト列を複製して画像を返す。入力長は `width * height * 4`。
    pub fn from_rgba(width: usize, height: usize, pixels: &[u8]) -> Result<Self, Error> {
        if pixels.len() != byte_len(width, height)? {
            return Err(Error::Image("画像と画素バッファの長さが一致しません"));
        }
        Self::new(width, height, clone_pixels(pixels)?)
    }

    fn new(width: usize, height: usize, pixels: Vec<u8>) -> Result<Self, Error> {
        Ok(Self {
            width,
            height,
            pixels: Arc::new(pixels),
            generation: next_generation()?,
        })
    }

    /// 画像の幅を画素数で返す。
    pub fn width(&self) -> usize {
        self.width
    }

    /// 画像の高さを画素数で返す。
    pub fn height(&self) -> usize {
        self.height
    }

    /// ストレート RGBA8888 の全画素を行優先の読み取り専用スライスで返す。
    pub fn rgba(&self) -> &[u8] {
        &self.pixels
    }

    /// 画素の状態を識別するプロセス内で再利用されない世代を返す。
    pub fn generation(&self) -> u64 {
        self.generation
    }

    /// 範囲内の矩形を連続した RGBA バイト列で返す。
    /// NumPy の `rgba[y:y+h, x:x+w].tobytes()` に相当する。空矩形の結果は空。
    pub fn copy_rgba_rect(&self, rect: Rect) -> Result<Vec<u8>, Error> {
        let len = self.rect_len(rect)?;
        let mut pixels = reserve_pixels(len)?;
        if len != 0 {
            let row_len = rect.width * CHANNELS;
            for y in rect.y..rect.y + rect.height {
                let start = (y * self.width + rect.x) * CHANNELS;
                pixels.extend_from_slice(&self.pixels[start..start + row_len]);
            }
        }
        Ok(pixels)
    }

    /// 範囲内の矩形を連続した RGBA バイト列で上書きする。
    /// NumPy の矩形代入に相当する。空矩形には空の入力を渡し、世代も維持する。
    /// 失敗時は画素と世代を維持する。
    pub fn write_rgba(&mut self, rect: Rect, pixels: &[u8]) -> Result<(), Error> {
        let len = self.rect_len(rect)?;
        if pixels.len() != len {
            return Err(Error::Image("矩形と画素バッファの長さが一致しません"));
        }
        if len == 0 {
            return Ok(());
        }

        let generation = next_generation()?;
        let full_image = len == self.pixels.len();
        if let Some(destination) = Arc::get_mut(&mut self.pixels) {
            Self::write_rows(destination, self.width, rect, pixels, full_image);
        } else if full_image {
            self.pixels = Arc::new(clone_pixels(pixels)?);
        } else {
            let mut destination = clone_pixels(&self.pixels)?;
            Self::write_rows(&mut destination, self.width, rect, pixels, false);
            self.pixels = Arc::new(destination);
        }
        self.generation = generation;
        Ok(())
    }

    fn rect_len(&self, rect: Rect) -> Result<usize, Error> {
        if rect
            .x
            .checked_add(rect.width)
            .is_none_or(|x| x > self.width)
            || rect
                .y
                .checked_add(rect.height)
                .is_none_or(|y| y > self.height)
        {
            return Err(Error::Image("矩形が画像の範囲外です"));
        }
        byte_len(rect.width, rect.height)
    }

    fn write_rows(
        destination: &mut [u8],
        width: usize,
        rect: Rect,
        pixels: &[u8],
        full_image: bool,
    ) {
        if full_image {
            destination.copy_from_slice(pixels);
            return;
        }

        // 非空の矩形と画像のバイト長は検証済みなので、行のオフセットも範囲内。
        let row_len = rect.width * CHANNELS;
        for (y, row) in (rect.y..rect.y + rect.height).zip(pixels.chunks_exact(row_len)) {
            let start = (y * width + rect.x) * CHANNELS;
            destination[start..start + row_len].copy_from_slice(row);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rect(x: usize, y: usize, width: usize, height: usize) -> Rect {
        Rect {
            x,
            y,
            width,
            height,
        }
    }

    #[test]
    fn rgba_bytes_and_rect_are_row_major() {
        let pixels: Vec<u8> = (0..24).collect();
        let image = RgbaImage::from_rgba(3, 2, &pixels).unwrap();
        assert_eq!((image.width(), image.height()), (3, 2));
        assert_eq!(image.rgba(), pixels);
        assert_eq!(
            image.copy_rgba_rect(rect(1, 0, 2, 2)).unwrap(),
            [&pixels[4..12], &pixels[16..24]].concat()
        );
    }

    #[test]
    fn zero_edges_and_empty_rects_preserve_dimensions_and_generation() {
        for (width, height) in [(0, 0), (0, 3), (3, 0), (usize::MAX, 0)] {
            let mut image = RgbaImage::blank(width, height).unwrap();
            let generation = image.generation();
            assert_eq!((image.width(), image.height()), (width, height));
            assert!(image.rgba().is_empty());
            let full = rect(0, 0, width, height);
            assert!(image.copy_rgba_rect(full).unwrap().is_empty());
            image.write_rgba(full, &[]).unwrap();
            assert_eq!(image.generation(), generation);
            assert!(RgbaImage::from_rgba(width, height, &[]).is_ok());
        }
    }

    #[test]
    fn rejects_invalid_sizes_without_mutating() {
        assert!(RgbaImage::blank(usize::MAX, 2).is_err());
        assert!(RgbaImage::blank(usize::MAX, 1).is_err());
        assert!(RgbaImage::blank(isize::MAX as usize / 4 + 1, 1).is_err());
        assert!(RgbaImage::from_rgba(1, 1, &[0; 3]).is_err());

        let mut image = RgbaImage::blank(2, 3).unwrap();
        let generation = image.generation();
        for invalid in [
            rect(1, 0, 2, 1),
            rect(0, 2, 1, 2),
            rect(usize::MAX, 0, 1, 1),
            rect(0, usize::MAX, 1, 1),
            rect(3, 0, 0, 1),
        ] {
            assert!(image.copy_rgba_rect(invalid).is_err());
            assert!(image.write_rgba(invalid, &[]).is_err());
        }
        assert!(image.write_rgba(rect(0, 0, 1, 2), &[8; 7]).is_err());
        assert!(image.write_rgba(rect(2, 0, 0, 3), &[8]).is_err());
        image.write_rgba(rect(2, 0, 0, 3), &[]).unwrap();
        assert_eq!(image.rgba(), &[0; 24]);
        assert_eq!(image.generation(), generation);
    }

    #[test]
    fn writes_large_patches_with_and_without_sharing() {
        for side in [64, 512] {
            let mut image = RgbaImage::blank(side + 4, side + 2).unwrap();
            let mut snapshot = image.clone();
            let original = image.generation();
            assert!(Arc::ptr_eq(&image.pixels, &snapshot.pixels));
            assert_eq!(snapshot.generation(), original);

            let patch = rect(1, 2, side, side);
            let pixels: Vec<u8> = (0..side * side * 4)
                .map(|index| (index % 251) as u8)
                .collect();
            image.write_rgba(patch, &pixels).unwrap();
            assert_eq!(image.copy_rgba_rect(patch).unwrap(), pixels);
            for (y, row) in image.rgba().chunks_exact((side + 4) * 4).enumerate() {
                if y < 2 {
                    assert!(row.iter().all(|&value| value == 0));
                } else {
                    assert_eq!(row[..4], [0; 4]);
                    assert_eq!(row[(side + 1) * 4..], [0; 12]);
                }
            }
            assert!(snapshot.rgba().iter().all(|&byte| byte == 0));
            assert_eq!(snapshot.generation(), original);
            assert_ne!(image.generation(), original);

            snapshot.write_rgba(patch, &pixels).unwrap();
            assert_ne!(snapshot.generation(), image.generation());
            assert_eq!(snapshot.rgba(), image.rgba());
        }
    }

    #[test]
    fn replaces_full_image_and_never_reuses_generations() {
        let mut image = RgbaImage::blank(2, 3).unwrap();
        let old = image.clone();
        let generation = old.generation();
        let full = rect(0, 0, 2, 3);
        image.write_rgba(full, &[10; 24]).unwrap();
        assert_eq!(old.rgba(), &[0; 24]);
        assert_eq!(image.rgba(), &[10; 24]);
        let replaced = image.generation();
        assert!(replaced > generation);

        image.write_rgba(full, &[15; 24]).unwrap();
        assert_eq!(image.rgba(), &[15; 24]);
        let last = image.generation();
        assert!(last > replaced);
        drop((image, old));
        assert!(RgbaImage::blank(2, 3).unwrap().generation() > last);
    }
}
