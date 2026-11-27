//! 表示用の縮小（ニアレストと線を残す縮小）。
//!
//! Python の `canvas.PaintCanvas._display_image_for_rect` と
//! `display_shrink.shrink_keeping_lines` に対応する。Qt のニアレストの画素の選び方は
//! 画像の形式で二通りあり、どちらも Qt と同じ固定小数の計算で再現する。
//!
//! * 乗算済みの画像の `QImage::scaled`・`QPainter::drawImage(QRectF, …)` は
//!   qblendfunctions_p.h の `qt_scale_image_32bit` を通る（[`painter_samples`]）。
//! * ストレートの RGBA8888 の `QImage::scaled` は qdrawhelper.cpp の
//!   `fetchTransformed_fetcher` を通る（[`fetch_samples`]）。線を残す縮小の最後の
//!   端数合わせがこちら。

use super::pixel::{blend, const_alpha, int_opacity, unpremultiply, PremulImage};
use crate::Error;

/// Qt の qRound（0.5 は 0 から遠い方へ）。
fn q_round(value: f64) -> i64 {
    if value >= 0.0 {
        (value + 0.5) as i64
    } else {
        (value - 0.5) as i64
    }
}

/// `qt_scale_image_32bit` が出力の各位置に置く元の位置。描かない位置は None。
///
/// `source` は元の長さ、`target` は描く矩形の長さ（原点 0）、`clip` は出力の長さ。
pub(crate) fn painter_samples(source: usize, target: f64, clip: usize) -> Vec<Option<usize>> {
    let scale = source as f64 / target;
    let step = (65536.0 * scale) as i32 as i64;
    // QRectF::toRect（原点 0 なので長さは qRound）と、出力の範囲との共通部分。
    let mut count = q_round(target).clamp(0, clip as i64);
    let start = ((0.5 * scale * 65536.0).ceil() as i64 - 1) as u32 as i64;
    if count > 0 {
        let end = (start + step * (count - 1)) >> 16;
        // 浮動小数の丸めで最後の1列が元の範囲を出るときは、その列を描かない。
        if end < 0 || end >= source as i64 {
            count -= 1;
        }
    }
    let mut samples = vec![None; clip];
    for (index, slot) in samples.iter_mut().enumerate().take(count.max(0) as usize) {
        *slot = Some(((start + step * index as i64) >> 16) as usize);
    }
    samples
}

/// `fetchTransformed_fetcher`（ニアレスト・拡大縮小のみ）が出力の各位置に置く元の位置。
///
/// Qt は `QSpanData::setupMatrix` で 1/65536 だけずらした行列の逆を使う。
pub(crate) fn fetch_samples(source: usize, target: usize) -> Vec<usize> {
    let scale = target as f64 / source as f64;
    let inverse = 1.0 / scale;
    let offset = -((1.0 / 65536.0) * scale) * inverse;
    let step = (inverse * 65536.0) as i32 as i64;
    let first = ((inverse * 0.5 + offset) * 65536.0) as i32 as i64;
    (0..target)
        .map(|index| ((first + step * index as i64) >> 16).clamp(0, source as i64 - 1) as usize)
        .collect()
}

fn fetch_row(source: usize, target: usize, row: usize) -> usize {
    let scale = target as f64 / source as f64;
    let inverse = 1.0 / scale;
    let offset = -((1.0 / 65536.0) * scale) * inverse;
    let fixed = ((inverse * (row as f64 + 0.5) + offset) * 65536.0) as i32 as i64;
    (fixed >> 16).clamp(0, source as i64 - 1) as usize
}

/// 乗算済みの画像の `QImage::scaled(w, h, IgnoreAspectRatio, FastTransformation)`。
pub fn scaled_nearest(
    image: &PremulImage,
    width: usize,
    height: usize,
) -> Result<PremulImage, Error> {
    let (width, height) = (width.max(1), height.max(1));
    if (width, height) == (image.width(), image.height())
        || image.width() == 0
        || image.height() == 0
    {
        return Ok(image.clone());
    }
    // QImage::scaled は倍率 w/ws の行列で描く。描く矩形はその行列で写した元の矩形。
    let target_width = image.width() as f64 * (width as f64 / image.width() as f64);
    let target_height = image.height() as f64 * (height as f64 / image.height() as f64);
    let mut out = PremulImage::transparent(width, height)?;
    draw_samples(
        &mut out,
        image,
        &painter_samples(image.width(), target_width, width),
        &painter_samples(image.height(), target_height, height),
        255,
    );
    Ok(out)
}

/// ストレートの RGBA8888 の画像の `QImage::scaled(…, FastTransformation)` を、乗算済みの
/// 画素で行う。Qt は描画のあいだ乗算済みで扱うので、値はそのまま写る。
pub fn scaled_nearest_straight(
    image: &PremulImage,
    width: usize,
    height: usize,
) -> Result<PremulImage, Error> {
    let (width, height) = (width.max(1), height.max(1));
    if (width, height) == (image.width(), image.height())
        || image.width() == 0
        || image.height() == 0
    {
        return Ok(image.clone());
    }
    let columns = fetch_samples(image.width(), width);
    let mut pixels = Vec::new();
    pixels
        .try_reserve_exact(width * height * 4)
        .map_err(|_| Error::Image("画素バッファを確保できません"))?;
    for y in 0..height {
        let row = fetch_row(image.height(), height, y);
        for &x in &columns {
            pixels.extend_from_slice(&image.pixel(x, row));
        }
    }
    PremulImage::from_premultiplied(width, height, pixels)
}

fn draw_samples(
    dst: &mut PremulImage,
    src: &PremulImage,
    columns: &[Option<usize>],
    rows: &[Option<usize>],
    alpha: u8,
) {
    let width = dst.width();
    for (y, row) in rows.iter().enumerate() {
        let Some(row) = *row else { continue };
        let line = &mut dst.pixels_mut()[y * width * 4..(y + 1) * width * 4];
        for (x, column) in columns.iter().enumerate() {
            if let Some(column) = *column {
                blend(&mut line[x * 4..x * 4 + 4], src.pixel(column, row), alpha);
            }
        }
    }
}

/// `dst` 全体を描く矩形にして `src` を拡大縮小し、不透明度 `opacity` で重ねる。
/// QPainter::drawImage(QRectF(0, 0, w, h), src) のニアレストと同じ。
///
/// Qt は元の画像が 1×1 のとき、その色で矩形を塗る（16bit の色で不透明度を掛けてから
/// 8bit へ丸めるので、画像として重ねたときと丸めが違う）。これも同じにする。
pub fn draw_scaled(dst: &mut PremulImage, src: &PremulImage, opacity: f64) {
    let alpha = const_alpha(opacity);
    if alpha == 0 || src.width() == 0 || src.height() == 0 {
        return;
    }
    if (src.width(), src.height()) == (1, 1) {
        fill_solid(dst, src.pixel(0, 0), int_opacity(opacity));
        return;
    }
    if (src.width(), src.height()) == (dst.width(), dst.height()) {
        dst.draw(src, 0, 0, opacity);
        return;
    }
    let columns = painter_samples(src.width(), dst.width() as f64, dst.width());
    let rows = painter_samples(src.height(), dst.height() as f64, dst.height());
    draw_samples(dst, src, &columns, &rows, alpha);
}

/// qpaintengine_raster.cpp の1画素の画像の塗り（`multiplyAlpha256` と QColor の8bit化）。
fn fill_solid(dst: &mut PremulImage, pixel: [u8; 4], int_opacity: u32) {
    let channel = |value: u8| {
        let wide = (value as u32 * 257 * int_opacity) >> 8;
        let rounded = wide + 128;
        ((rounded - (rounded >> 8)) >> 8) as u8
    };
    let color = pixel.map(channel);
    if (pixel[3] as u32 * 257 * int_opacity) >> 8 == 0 {
        return;
    }
    for target in dst.pixels_mut().chunks_exact_mut(4) {
        blend(target, color, 255);
    }
}

/// 線を残す縮小の途中の画像。比較に使うストレートの RGB の和を画素ごとに持つ。
struct Lined {
    image: PremulImage,
    luma: Vec<u16>,
}

impl Lined {
    fn new(image: &PremulImage, straight: Option<&[u8]>) -> Self {
        let luma_of = |pixel: &[u8]| pixel[0] as u16 + pixel[1] as u16 + pixel[2] as u16;
        let luma = match straight {
            Some(straight) => straight.chunks_exact(4).map(luma_of).collect(),
            None => image
                .pixels()
                .chunks_exact(4)
                .map(|pixel| luma_of(&unpremultiply([pixel[0], pixel[1], pixel[2], pixel[3]])))
                .collect(),
        };
        Self {
            image: image.clone(),
            luma,
        }
    }

    fn at(&self, x: usize, y: usize) -> ([u8; 4], u16) {
        (
            self.image.pixel(x, y),
            self.luma[y * self.image.width() + x],
        )
    }

    /// 縦横を半分にする。2×2 のうち線らしい画素を残す（`halve_keeping_lines`）。
    fn halve(&self) -> Result<Self, Error> {
        let width = self.image.width() - self.image.width() % 2;
        let height = self.image.height() - self.image.height() % 2;
        if width < 2 || height < 2 {
            return Ok(Self {
                image: self.image.clone(),
                luma: self.luma.clone(),
            });
        }
        // 不透明な方、同じ不透明さなら暗い方（同じなら前の方）を選ぶ。
        let pick = |a: ([u8; 4], u16), b: ([u8; 4], u16)| {
            if a.0[3] > b.0[3] || (a.0[3] == b.0[3] && a.1 <= b.1) {
                a
            } else {
                b
            }
        };
        let (half_width, half_height) = (width / 2, height / 2);
        let mut pixels = Vec::new();
        pixels
            .try_reserve_exact(half_width * half_height * 4)
            .map_err(|_| Error::Image("画素バッファを確保できません"))?;
        let mut luma = Vec::with_capacity(half_width * half_height);
        for y in 0..half_height {
            for x in 0..half_width {
                let top = pick(self.at(2 * x, 2 * y), self.at(2 * x + 1, 2 * y));
                let bottom = pick(self.at(2 * x, 2 * y + 1), self.at(2 * x + 1, 2 * y + 1));
                let (pixel, value) = pick(top, bottom);
                pixels.extend_from_slice(&pixel);
                luma.push(value);
            }
        }
        Ok(Self {
            image: PremulImage::from_premultiplied(half_width, half_height, pixels)?,
            luma,
        })
    }
}

/// 縦横を半分にする。2×2 のうち線らしい画素を残す（`display_shrink.halve_keeping_lines`）。
pub fn halve_keeping_lines(image: &PremulImage) -> Result<PremulImage, Error> {
    Ok(Lined::new(image, None).halve()?.image)
}

/// `display_shrink.shrink_keeping_lines` と同じく、線を残したまま `width` × `height` へ縮める。
///
/// 半分ずつ縮めて目標の2倍以内まで寄せ、最後の端数だけニアレストで合わせる。
/// 比較に使うストレートの値はスカラー版の qUnpremultiply で求める（Qt の SIMD 版と
/// 半透明の画素で ±1 違うことがあり、同じ不透明さの暗さの比較が変わりうる）。
pub fn shrink_keeping_lines(
    image: &PremulImage,
    width: usize,
    height: usize,
) -> Result<PremulImage, Error> {
    shrink_keeping_lines_with(image, None, width, height)
}

/// [`shrink_keeping_lines`] の、比較に使うストレートの画素 `straight`（画像と同じ大きさの
/// RGBA）を呼び出し側が渡す形。文書の画素をそのまま渡せば Qt と同じ比較になる。
pub fn shrink_keeping_lines_with(
    image: &PremulImage,
    straight: Option<&[u8]>,
    width: usize,
    height: usize,
) -> Result<PremulImage, Error> {
    let (width, height) = (width.max(1), height.max(1));
    if image.width() <= width && image.height() <= height {
        return Ok(image.clone());
    }
    let straight = straight.filter(|straight| straight.len() == image.pixels().len());
    let mut reduced = Lined::new(image, straight);
    while reduced.image.height() >= height * 2 && reduced.image.width() >= width * 2 {
        let halved = reduced.halve()?;
        if (halved.image.width(), halved.image.height())
            == (reduced.image.width(), reduced.image.height())
        {
            break;
        }
        reduced = halved;
    }
    if (reduced.image.width(), reduced.image.height()) == (width, height) {
        return Ok(reduced.image);
    }
    scaled_nearest_straight(&reduced.image, width, height)
}

/// 縮小のしかた（環境設定の `display_shrink_mode`）。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ShrinkMode {
    /// ニアレストネイバー（`nearest`）。
    #[default]
    Nearest,
    /// 線を残す縮小（`keep_lines`）。
    KeepLines,
}

/// 画面に描く表示用の大きさ。Python の `_display_image_for_rect` の計算
/// （`round` は偶数丸め）と同じく、元の大きさを超えない。
pub fn display_size(
    image: (usize, usize),
    target_width: f64,
    target_height: f64,
    device_pixel_ratio: f64,
) -> (usize, usize) {
    let ratio = device_pixel_ratio.max(1.0);
    let size = |source: usize, target: f64| {
        let pixels = (target * ratio).round_ties_even();
        source.min((pixels as i64).max(1) as usize)
    };
    (size(image.0, target_width), size(image.1, target_height))
}

/// `_display_image_for_rect` の縮小と反転。`width`・`height` は [`display_size`] の値。
pub fn display_image_for_size(
    image: &PremulImage,
    width: usize,
    height: usize,
    mode: ShrinkMode,
    flip_horizontal: bool,
    flip_vertical: bool,
) -> Result<PremulImage, Error> {
    display_image_for_size_with(
        image,
        None,
        width,
        height,
        mode,
        flip_horizontal,
        flip_vertical,
    )
}

/// [`display_image_for_size`] の、線を残す縮小の比較に使うストレートの画素を渡す形。
pub fn display_image_for_size_with(
    image: &PremulImage,
    straight: Option<&[u8]>,
    width: usize,
    height: usize,
    mode: ShrinkMode,
    flip_horizontal: bool,
    flip_vertical: bool,
) -> Result<PremulImage, Error> {
    if image.width() == 0 || image.height() == 0 {
        return Ok(image.clone());
    }
    let resized = if (width, height) == (image.width(), image.height()) {
        image.clone()
    } else {
        match mode {
            ShrinkMode::KeepLines => shrink_keeping_lines_with(image, straight, width, height)?,
            ShrinkMode::Nearest => scaled_nearest(image, width, height)?,
        }
    };
    Ok(resized.mirrored(flip_horizontal, flip_vertical))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn gradient(width: usize, height: usize) -> PremulImage {
        let pixels = (0..width * height)
            .flat_map(|index| [index as u8, (index >> 8) as u8, 0, 255])
            .collect();
        PremulImage::from_premultiplied(width, height, pixels).unwrap()
    }

    #[test]
    fn painter_samples_are_identity_at_one_to_one() {
        let samples = painter_samples(5, 5.0, 5);
        assert_eq!(samples, (0..5).map(Some).collect::<Vec<_>>());
    }

    #[test]
    fn halving_picks_opaque_then_dark() {
        let pixels = [
            [200, 200, 200, 255],
            [10, 10, 10, 255],
            [0, 0, 0, 0],
            [50, 0, 0, 128],
        ]
        .concat();
        let image = PremulImage::from_premultiplied(2, 2, pixels).unwrap();
        assert_eq!(
            halve_keeping_lines(&image).unwrap().pixel(0, 0),
            [10, 10, 10, 255]
        );
    }

    #[test]
    fn shrink_reaches_the_target_size() {
        let image = gradient(37, 19);
        for (width, height) in [(5, 3), (18, 9), (36, 2), (1, 1)] {
            let out = shrink_keeping_lines(&image, width, height).unwrap();
            assert_eq!((out.width(), out.height()), (width, height));
            let out = scaled_nearest(&image, width, height).unwrap();
            assert_eq!((out.width(), out.height()), (width, height));
        }
        assert_eq!(shrink_keeping_lines(&image, 40, 40).unwrap(), image);
    }

    #[test]
    fn display_size_rounds_half_to_even() {
        assert_eq!(display_size((100, 100), 10.5, 11.5, 1.0), (10, 12));
        assert_eq!(display_size((8, 8), 100.0, 0.1, 2.0), (8, 1));
    }
}
