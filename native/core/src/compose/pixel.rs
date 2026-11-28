//! Qt の ARGB32_Premultiplied と同じ丸めの画素演算と、乗算済みの画像。
//!
//! 画素は R, G, B, A の順の4バイトで持つ（Qt の ARGB32 はメモリ上 B, G, R, A の順だが、
//! 値は同じ）。各関数は Qt の C 実装（qrgb.h の qPremultiply・qUnpremultiply、
//! qdrawhelper_p.h の BYTE_MUL・blend_pixel）と同じ式で丸める。

use crate::image::{Rect, RgbaImage};
use crate::Error;

/// 乗算済みの RGBA 1画素。
pub type Premul = [u8; 4];

/// `c * a / 255` を Qt の BYTE_MUL・qPremultiply と同じく丸める。
#[inline]
pub(crate) fn mul_255(c: u8, a: u8) -> u8 {
    let t = c as u32 * a as u32;
    ((t + (t >> 8) + 0x80) >> 8) as u8
}

/// qt_inv_premul_factor（`0x00ff00ff / a`）。
const INV_PREMUL: [u32; 256] = {
    let mut table = [0u32; 256];
    let mut alpha = 1;
    while alpha < 256 {
        table[alpha] = 0x00ff_00ff / alpha as u32;
        alpha += 1;
    }
    table
};

/// ストレートの1画素を乗算済みにする。Qt の qPremultiply と同じ値。
#[inline]
pub fn premultiply(pixel: [u8; 4]) -> Premul {
    let alpha = pixel[3];
    [
        mul_255(pixel[0], alpha),
        mul_255(pixel[1], alpha),
        mul_255(pixel[2], alpha),
        alpha,
    ]
}

/// 乗算済みの1画素をストレートに戻す。Qt の qUnpremultiply（スカラー版）と同じ値。
///
/// Qt の画像変換（`convertToFormat(Format_RGBA8888)`）は NEON・SSE4 では4画素ずつ
/// 浮動小数の逆数の近似で割るため、半透明の画素でこの値と ±1 違うことがある。
/// この関数では `premultiply(unpremultiply(p)) == p` が全ての値で成り立つ（テストで確かめる）。
#[inline]
pub fn unpremultiply(pixel: Premul) -> [u8; 4] {
    let alpha = pixel[3];
    match alpha {
        255 => pixel,
        0 => [0; 4],
        _ => {
            let inverse = INV_PREMUL[alpha as usize];
            let channel = |value: u8| ((value as u32 * inverse + 0x8000) >> 16) as u8;
            [
                channel(pixel[0]),
                channel(pixel[1]),
                channel(pixel[2]),
                alpha,
            ]
        }
    }
}

/// QPainter::setOpacity の値から Qt の raster エンジンが持つ `intOpacity`（0〜256）を返す。
pub(crate) fn int_opacity(opacity: f64) -> u32 {
    // NaN は QPainter の qMin(1, qMax(0, x)) を通ると 0 になる（比較が偽のため 0 が残る）。
    let opacity = if opacity.is_nan() {
        0.0
    } else {
        opacity.clamp(0.0, 1.0)
    };
    (opacity * 256.0) as u32
}

/// QPainter::setOpacity の値から、ソースオーバーで掛ける 0〜255 の定数アルファを返す。
///
/// Qt は不透明度を [0, 1] に収め、`(int)(opacity * 256)` を `(x * 255) >> 8` にして掛ける。
/// 不透明度 1 は 255（定数アルファなし）になる。
pub fn const_alpha(opacity: f64) -> u8 {
    let int_opacity = int_opacity(opacity);
    if int_opacity >= 256 {
        255
    } else {
        ((int_opacity * 255) >> 8) as u8
    }
}

/// `dst` の上へ `src` をソースオーバーで重ねる。Qt の blend_pixel と同じ式。
#[inline]
pub(crate) fn blend(dst: &mut [u8], src: Premul, alpha: u8) {
    if src == [0; 4] || alpha == 0 {
        return;
    }
    let source = if alpha == 255 {
        src
    } else {
        [
            mul_255(src[0], alpha),
            mul_255(src[1], alpha),
            mul_255(src[2], alpha),
            mul_255(src[3], alpha),
        ]
    };
    let inverse = 255 - source[3];
    for (channel, value) in dst.iter_mut().zip(source) {
        // 乗算済みの値なら和は 255 を超えない。Qt も桁あふれを考えずに足す。
        *channel = value.wrapping_add(mul_255(*channel, inverse));
    }
}

/// 乗算済み RGBA8888 の画像（Qt の ARGB32_Premultiplied と同じ値）。
///
/// 合成の途中の値と、GPU に渡す表示用の画像に使う。WebGL などの乗算済みの
/// テクスチャへはそのまま渡せる。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PremulImage {
    width: usize,
    height: usize,
    pixels: Vec<u8>,
}

fn byte_len(width: usize, height: usize) -> Result<usize, Error> {
    width
        .checked_mul(height)
        .and_then(|area| area.checked_mul(4))
        .filter(|&len| len <= isize::MAX as usize)
        .ok_or(Error::Image("画素バッファの寸法が上限を超えています"))
}

fn filled_pixels(len: usize, pixel: Premul) -> Result<Vec<u8>, Error> {
    let mut pixels = Vec::new();
    pixels
        .try_reserve_exact(len)
        .map_err(|_| Error::Image("画素バッファを確保できません"))?;
    if pixel == [0; 4] {
        pixels.resize(len, 0);
    } else {
        for _ in 0..len / 4 {
            pixels.extend_from_slice(&pixel);
        }
    }
    Ok(pixels)
}

impl PremulImage {
    /// 1色で塗った画像を返す。`pixel` は乗算済みの値。
    pub fn filled(width: usize, height: usize, pixel: Premul) -> Result<Self, Error> {
        let pixels = filled_pixels(byte_len(width, height)?, pixel)?;
        Ok(Self {
            width,
            height,
            pixels,
        })
    }

    /// 透明な画像を返す。
    pub fn transparent(width: usize, height: usize) -> Result<Self, Error> {
        Self::filled(width, height, [0; 4])
    }

    /// 乗算済みの RGBA バイト列から画像を作る。長さは `width * height * 4`。
    pub fn from_premultiplied(width: usize, height: usize, pixels: Vec<u8>) -> Result<Self, Error> {
        if pixels.len() != byte_len(width, height)? {
            return Err(Error::Image("画像と画素バッファの長さが一致しません"));
        }
        Ok(Self {
            width,
            height,
            pixels,
        })
    }

    /// ストレートの RGBA バイト列を乗算済みにした画像を返す（Qt の RGBA8888 から
    /// ARGB32_Premultiplied への変換と同じ値）。
    pub fn from_straight(width: usize, height: usize, rgba: &[u8]) -> Result<Self, Error> {
        if rgba.len() != byte_len(width, height)? {
            return Err(Error::Image("画像と画素バッファの長さが一致しません"));
        }
        let mut pixels = filled_pixels(rgba.len(), [0; 4])?;
        for (out, pixel) in pixels.chunks_exact_mut(4).zip(rgba.chunks_exact(4)) {
            out.copy_from_slice(&premultiply([pixel[0], pixel[1], pixel[2], pixel[3]]));
        }
        Ok(Self {
            width,
            height,
            pixels,
        })
    }

    /// 文書の画像の矩形を乗算済みにして返す。矩形は画像の範囲内であること。
    pub fn from_image_rect(image: &RgbaImage, rect: Rect) -> Result<Self, Error> {
        let straight = image.copy_rgba_rect(rect)?;
        Self::from_straight(rect.width, rect.height, &straight)
    }

    /// 画像の幅。
    pub fn width(&self) -> usize {
        self.width
    }

    /// 画像の高さ。
    pub fn height(&self) -> usize {
        self.height
    }

    /// 乗算済みの RGBA を行優先で返す。
    pub fn pixels(&self) -> &[u8] {
        &self.pixels
    }

    /// 乗算済みの RGBA を書き換え用に返す。
    pub fn pixels_mut(&mut self) -> &mut [u8] {
        &mut self.pixels
    }

    /// 乗算済みの RGBA のバイト列を取り出す。
    pub fn into_pixels(self) -> Vec<u8> {
        self.pixels
    }

    /// ストレートの RGBA バイト列に戻す（スカラー版の qUnpremultiply と同じ値）。
    pub fn to_straight_bytes(&self) -> Vec<u8> {
        let mut out = self.pixels.clone();
        for pixel in out.chunks_exact_mut(4) {
            let straight = unpremultiply([pixel[0], pixel[1], pixel[2], pixel[3]]);
            pixel.copy_from_slice(&straight);
        }
        out
    }

    /// ストレートの RgbaImage に戻す。
    pub fn to_straight(&self) -> Result<RgbaImage, Error> {
        RgbaImage::from_rgba(self.width, self.height, &self.to_straight_bytes())
    }

    /// 範囲内の矩形を切り出す。
    pub fn crop(&self, rect: Rect) -> Result<Self, Error> {
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
        let mut pixels = filled_pixels(byte_len(rect.width, rect.height)?, [0; 4])?;
        if rect.width != 0 {
            let row = rect.width * 4;
            for (y, out) in (rect.y..rect.y + rect.height).zip(pixels.chunks_exact_mut(row)) {
                let start = (y * self.width + rect.x) * 4;
                out.copy_from_slice(&self.pixels[start..start + row]);
            }
        }
        Ok(Self {
            width: rect.width,
            height: rect.height,
            pixels,
        })
    }

    /// `src` の左上を (`x`, `y`) に置き、不透明度 `opacity` のソースオーバーで重ねる。
    /// QPainter::setOpacity と drawImage(x, y, src) と同じ結果。はみ出す分は描かない。
    pub fn draw(&mut self, src: &PremulImage, x: i64, y: i64, opacity: f64) {
        let alpha = const_alpha(opacity);
        if alpha == 0 {
            return;
        }
        let clip = |offset: i64, size: usize, dst: usize| {
            let start = offset.max(0);
            let end = (offset + size as i64).min(dst as i64);
            (start, end)
        };
        let (x0, x1) = clip(x, src.width, self.width);
        let (y0, y1) = clip(y, src.height, self.height);
        if x0 >= x1 || y0 >= y1 {
            return;
        }
        for dy in y0..y1 {
            let sy = (dy - y) as usize;
            let dst_row = &mut self.pixels[(dy as usize * self.width) * 4..][..self.width * 4];
            let src_row = &src.pixels[(sy * src.width) * 4..][..src.width * 4];
            for dx in x0..x1 {
                let sx = (dx - x) as usize;
                let s = &src_row[sx * 4..sx * 4 + 4];
                blend(
                    &mut dst_row[dx as usize * 4..dx as usize * 4 + 4],
                    [s[0], s[1], s[2], s[3]],
                    alpha,
                );
            }
        }
    }

    /// 左右・上下に反転した画像を返す（QImage::mirrored と同じ）。
    pub fn mirrored(&self, horizontal: bool, vertical: bool) -> Self {
        let mut out = self.clone();
        if !(horizontal || vertical) {
            return out;
        }
        for y in 0..self.height {
            let sy = if vertical { self.height - 1 - y } else { y };
            for x in 0..self.width {
                let sx = if horizontal { self.width - 1 - x } else { x };
                let from = (sy * self.width + sx) * 4;
                let to = (y * self.width + x) * 4;
                out.pixels[to..to + 4].copy_from_slice(&self.pixels[from..from + 4]);
            }
        }
        out
    }

    pub(crate) fn pixel(&self, x: usize, y: usize) -> Premul {
        let at = (y * self.width + x) * 4;
        [
            self.pixels[at],
            self.pixels[at + 1],
            self.pixels[at + 2],
            self.pixels[at + 3],
        ]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn premultiply_round_trips_every_value() {
        // Qt の保証どおり、qPremultiply(qUnpremultiply(p)) == p。
        for alpha in 0..=255u8 {
            for value in 0..=alpha {
                let pixel = [value, value / 2, 0, alpha];
                assert_eq!(premultiply(unpremultiply(pixel)), pixel);
            }
        }
        assert_eq!(premultiply([255, 128, 1, 128]), [128, 64, 1, 128]);
        assert_eq!(unpremultiply([1, 1, 1, 2]), [128, 128, 128, 2]);
        assert_eq!(unpremultiply([9, 9, 9, 0]), [0; 4]);
    }

    #[test]
    fn const_alpha_truncates_like_qpainter() {
        assert_eq!(const_alpha(1.0), 255);
        assert_eq!(const_alpha(2.0), 255);
        assert_eq!(const_alpha(0.0), 0);
        assert_eq!(const_alpha(-1.0), 0);
        // (int)(0.5 * 256) = 128 → (128 * 255) >> 8 = 127。
        assert_eq!(const_alpha(0.5), 127);
        assert_eq!(const_alpha(0.999), 254);
        assert_eq!(const_alpha(1.0 / 256.0), 0);
    }

    #[test]
    fn blend_matches_source_over() {
        let mut dst = [255, 255, 255, 255];
        blend(&mut dst, [0, 0, 128, 128], 255);
        assert_eq!(dst, [127, 127, 255, 255]);
        let mut dst = [255, 255, 255, 255];
        blend(&mut dst, [0, 0, 0, 0], 255);
        assert_eq!(dst, [255; 4]);
        let mut dst = [10, 20, 30, 255];
        blend(&mut dst, [200, 0, 0, 255], 255);
        assert_eq!(dst, [200, 0, 0, 255]);
        let mut dst = [0, 0, 0, 0];
        blend(&mut dst, [200, 0, 0, 255], 127);
        assert_eq!(dst, [100, 0, 0, 127]);
    }

    #[test]
    fn draw_clips_to_destination() {
        let mut dst = PremulImage::transparent(3, 2).unwrap();
        let src = PremulImage::filled(2, 2, [9, 8, 7, 255]).unwrap();
        dst.draw(&src, 2, 1, 1.0);
        assert_eq!(dst.pixel(2, 1), [9, 8, 7, 255]);
        assert_eq!(dst.pixel(1, 1), [0; 4]);
        assert_eq!(dst.pixel(2, 0), [0; 4]);
        dst.draw(&src, -5, 0, 1.0);
        dst.draw(&src, 0, 0, 0.0);
        assert_eq!(dst.pixel(0, 0), [0; 4]);
    }

    #[test]
    fn mirrored_and_crop() {
        let pixels: Vec<u8> = (0..24).collect();
        let image = PremulImage::from_premultiplied(3, 2, pixels).unwrap();
        let flipped = image.mirrored(true, true);
        assert_eq!(flipped.pixel(0, 0), image.pixel(2, 1));
        assert_eq!(flipped.pixel(2, 1), image.pixel(0, 0));
        let crop = image
            .crop(Rect {
                x: 1,
                y: 1,
                width: 2,
                height: 1,
            })
            .unwrap();
        assert_eq!(crop.pixels(), &image.pixels()[16..24]);
        assert!(image
            .crop(Rect {
                x: 2,
                y: 0,
                width: 2,
                height: 1
            })
            .is_err());
    }
}
