//! 線・色のメニューの操作（ゴミ取り・線整え・色置換など）を、1 枚の層の画素にかける。
//!
//! どの入口も、ストレート RGBA の [`RgbaImage`]（Qt の乗算済み形式で往復した値）と
//! 操作の設定、必要なら選択範囲のマスクを受け取り、変えた画素と変わった範囲（Undo 用）を
//! 返す。結果は Python 版が層に書き戻した画素（`PmaImage` の正規化を通した値）と一致する。
//! どのセルにかけるか（今のコマ／すべてのコマ・同じ絵番号を 1 回）は呼び出し側が決める。
pub mod antialias;
pub mod color_focus;
pub mod color_replace;
pub mod despeckle;
pub mod dust;
pub mod hsv;
pub mod line_split;
pub mod line_tidy;
pub mod palette_build;
pub mod reduce;
pub mod soft_blur;

use crate::image::{Rect, RgbaImage};
use crate::project::qt_alpha::qt_premultiplied_roundtrip;
use crate::Error;

/// 画素を書き換えた結果。
#[derive(Clone, Debug)]
pub struct PixelEdit {
    /// 書き換えたあとの画像（`PmaImage` と同じ正規化を通した値）。
    pub image: RgbaImage,
    /// 入力と画素が違う範囲を囲む矩形。違いが無ければ `None`。
    pub changed: Option<Rect>,
    /// Python 版が数える「変えた画素数」（状態表示に使う）。
    pub pixels: usize,
}

/// 画像と同じ大きさとは限らない、行優先の真偽マスク（0 以外が真）。
#[derive(Clone, Copy, Debug)]
pub struct MaskRef<'a> {
    /// `width * height` 個の値。
    pub data: &'a [u8],
    /// マスクの幅。
    pub width: usize,
    /// マスクの高さ。
    pub height: usize,
}

impl MaskRef<'_> {
    /// 範囲外は偽。
    pub fn get(&self, x: usize, y: usize) -> bool {
        x < self.width && y < self.height && self.data[y * self.width + x] != 0
    }

    /// 大きさが `width`×`height` と同じか。
    pub fn same_size(&self, width: usize, height: usize) -> bool {
        self.width == width && self.height == height && self.data.len() == width * height
    }

    fn check(&self) -> Result<(), Error> {
        if self.width.checked_mul(self.height) != Some(self.data.len()) {
            return Err(Error::Image("マスクの寸法と長さが一致しません"));
        }
        Ok(())
    }
}

/// `(r << 16) | (g << 8) | b`。
pub(crate) fn pack(rgb: &[u8]) -> u32 {
    (u32::from(rgb[0]) << 16) | (u32::from(rgb[1]) << 8) | u32::from(rgb[2])
}

pub(crate) fn unpack(value: u32) -> [u8; 3] {
    [(value >> 16) as u8, (value >> 8) as u8, value as u8]
}

/// 入力と出力の画素が違う範囲。
pub fn changed_rect(before: &[u8], after: &[u8], width: usize) -> Option<Rect> {
    if width == 0 || before.len() != after.len() {
        return None;
    }
    let (mut x0, mut y0, mut x1, mut y1) = (usize::MAX, usize::MAX, 0, 0);
    for (index, (a, b)) in before
        .chunks_exact(4)
        .zip(after.chunks_exact(4))
        .enumerate()
    {
        if a != b {
            let (x, y) = (index % width, index / width);
            x0 = x0.min(x);
            y0 = y0.min(y);
            x1 = x1.max(x);
            y1 = y1.max(y);
        }
    }
    (x0 != usize::MAX).then(|| Rect {
        x: x0,
        y: y0,
        width: x1 - x0 + 1,
        height: y1 - y0 + 1,
    })
}

/// 書き換えた画素を `PmaImage` と同じく乗算済み形式で往復し、結果にまとめる。
pub(crate) fn finish(
    before: &RgbaImage,
    mut pixels: Vec<u8>,
    count: usize,
) -> Result<PixelEdit, Error> {
    qt_premultiplied_roundtrip(&mut pixels, before.width());
    let changed = changed_rect(before.rgba(), &pixels, before.width());
    let image =
        RgbaImage::from_rgba(before.width(), before.height(), &pixels)?.with_tag(before.tag());
    Ok(PixelEdit {
        image,
        changed,
        pixels: count,
    })
}

/// 外周の画素のうち `passable` なもの（上の行・下の行・左の列・右の列の順、`(x, y)`）。
/// Python 版の塗りつぶしの種と同じ並び。
pub(crate) fn border_seeds(
    passable: impl Fn(usize) -> bool,
    width: usize,
    height: usize,
) -> Vec<(i64, i64)> {
    let mut seeds = Vec::new();
    let mut push = |x: usize, y: usize| {
        if passable(y * width + x) {
            seeds.push((x as i64, y as i64));
        }
    };
    (0..width).for_each(|x| push(x, 0));
    if height > 1 {
        (0..width).for_each(|x| push(x, height - 1));
    }
    if width > 1 {
        (0..height).for_each(|y| push(0, y));
        (0..height).for_each(|y| push(width - 1, y));
    }
    seeds
}

/// 4 近傍でつながった真の画素の塊。各塊の画素の添字（行優先）と、外周に触れるかを返す。
pub(crate) fn components4(mask: &[bool], width: usize, height: usize) -> Vec<(Vec<usize>, bool)> {
    let mut pending = mask.to_vec();
    let mut out = Vec::new();
    let mut stack = Vec::new();
    for start in 0..mask.len() {
        if !pending[start] {
            continue;
        }
        pending[start] = false;
        stack.push(start);
        let mut component = Vec::new();
        let mut edge = false;
        while let Some(index) = stack.pop() {
            component.push(index);
            let (x, y) = (index % width, index / width);
            if x == 0 || y == 0 || x == width - 1 || y == height - 1 {
                edge = true;
            }
            let mut visit = |next: usize| {
                if pending[next] {
                    pending[next] = false;
                    stack.push(next);
                }
            };
            if x > 0 {
                visit(index - 1);
            }
            if x + 1 < width {
                visit(index + 1);
            }
            if y > 0 {
                visit(index - width);
            }
            if y + 1 < height {
                visit(index + width);
            }
        }
        out.push((component, edge));
    }
    out
}

#[cfg(test)]
pub(crate) mod test_support {
    use crate::image::RgbaImage;

    pub fn image(width: usize, height: usize, pixels: &[[u8; 4]]) -> RgbaImage {
        RgbaImage::from_rgba(width, height, &pixels.concat()).unwrap()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn changed_rect_covers_differences() {
        let before = vec![0u8; 4 * 6];
        let mut after = before.clone();
        assert_eq!(changed_rect(&before, &after, 3), None);
        after[4 + 3] = 255;
        after[4 * 5] = 9;
        assert_eq!(
            changed_rect(&before, &after, 3),
            Some(Rect {
                x: 1,
                y: 0,
                width: 2,
                height: 2
            })
        );
    }

    #[test]
    fn components_split_by_four_neighbours() {
        let mask = [true, false, true, false, true, false, false, false, false];
        let parts = components4(&mask, 3, 3);
        assert_eq!(parts.len(), 3);
        assert_eq!(parts.iter().filter(|(_, edge)| *edge).count(), 2);
    }
}
