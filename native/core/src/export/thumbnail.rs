//! .pman のサムネイル（`project_io._thumbnail_png`・`pma_image_io.thumbnail_png`）。
//!
//! Python は今のコマの合成（白地、`MainWindow.crop_image`）を長辺 512 以下へ
//! `QImage.scaled(…, KeepAspectRatio, SmoothTransformation)` で縮め、Qt で PNG にする。
//! ここでは Qt の縮小（qimagescale.cpp の qSmoothScaleImage、乗算済み ARGB32 の
//! 面積平均）を同じ整数式で行い、Qt の PNG 書き出しと同じくストレートへ戻してから
//! [`encode_png`] で符号化する。画素は Qt と一致する（PNG の圧縮は Qt と違うので
//! バイト列は一致しない）。

use crate::compose::{self, Background, DisplayOptions, PremulImage};
use crate::document::model::Document;
use crate::project::archive::DEFAULT_DOTS_PER_METER;
use crate::project::png::encode_png;
use crate::Error;

/// サムネイルの長辺の上限（`project_io.THUMBNAIL_SIZE`）。
pub const THUMBNAIL_SIZE: usize = 512;

/// `QSize(width, height).scaled(max, max, KeepAspectRatio)` に 1 以上の丸めを掛けた寸法。
pub fn keep_aspect_size(width: usize, height: usize, max: usize) -> (usize, usize) {
    if width == 0 || height == 0 {
        return (max.max(1), max.max(1));
    }
    let (w, h) = (width as i64, height as i64);
    let m = max as i64;
    let rw = m * w / h;
    let (nw, nh) = if rw <= m { (rw, m) } else { (m, m * h / w) };
    (nw.max(1) as usize, nh.max(1) as usize)
}

fn calc_points(s: usize, d: usize) -> Vec<usize> {
    let (s, d) = (s as i64, d as i64);
    let up = d >= s;
    let mut val: i64 = if up { 0x8000 * s / d - 0x8000 } else { 0 };
    let inc = (s << 16) / d;
    (0..d)
        .map(|_| {
            let p = (val >> 16).max(0) as usize;
            val += inc;
            p
        })
        .collect()
}

fn calc_apoints(s: usize, d: usize, up: bool) -> Vec<u32> {
    let (s, d) = (s as i64, d as i64);
    let inc = (s << 16) / d;
    if up {
        let mut val: i64 = 0x8000 * s / d - 0x8000;
        (0..d)
            .map(|_| {
                let pos = val >> 16;
                let p = if pos < 0 || pos >= s - 1 {
                    0
                } else {
                    ((val >> 8) - ((val >> 8) & 0xffff_ff00)) as u32
                };
                val += inc;
                p
            })
            .collect()
    } else {
        let mut val: i64 = 0;
        let cp = ((d << 14) + s - 1) / s;
        (0..d)
            .map(|_| {
                let ap = ((0x10000 - (val & 0xffff)) * cp) >> 16;
                val += inc;
                (ap | (cp << 16)) as u32
            })
            .collect()
    }
}

type Sum = [u32; 4];

/// qt_qimageScaleAARGBA_helper：pix から step 刻みで重み付きの和（14bit の重み）。
fn helper(src: &[u8], mut index: usize, xyap: u32, cxy: u32, step: usize) -> Sum {
    let channel = |i: usize| -> Sum {
        let p = &src[i * 4..i * 4 + 4];
        [p[0] as u32, p[1] as u32, p[2] as u32, p[3] as u32]
    };
    let mut sum = channel(index).map(|c| c.wrapping_mul(xyap));
    let mut j = (1u32 << 14) as i64 - xyap as i64;
    while j > cxy as i64 {
        index += step;
        let p = channel(index);
        for k in 0..4 {
            sum[k] = sum[k].wrapping_add(p[k].wrapping_mul(cxy));
        }
        j -= cxy as i64;
    }
    index += step;
    let p = channel(index);
    for k in 0..4 {
        sum[k] = sum[k].wrapping_add(p[k].wrapping_mul(j as u32));
    }
    sum
}

fn mix(a: Sum, b: Sum, weight: u32) -> Sum {
    let mut out = [0; 4];
    for k in 0..4 {
        out[k] = (a[k]
            .wrapping_mul(256 - weight)
            .wrapping_add(b[k].wrapping_mul(weight)))
            >> 8;
    }
    out
}

/// qimagescale.cpp の INTERPOLATE_PIXEL_256（乗算済みの2画素の補間）。
fn interpolate_256(x: [u8; 4], a: u32, y: [u8; 4], b: u32) -> [u8; 4] {
    let pack = |p: [u8; 4]| u32::from_be_bytes([p[3], p[0], p[1], p[2]]);
    let (x, y) = (pack(x), pack(y));
    let mut t = (x & 0x00ff_00ff)
        .wrapping_mul(a)
        .wrapping_add((y & 0x00ff_00ff).wrapping_mul(b));
    t >>= 8;
    t &= 0x00ff_00ff;
    let mut u = ((x >> 8) & 0x00ff_00ff)
        .wrapping_mul(a)
        .wrapping_add(((y >> 8) & 0x00ff_00ff).wrapping_mul(b));
    u &= 0xff00_ff00;
    let v = u | t;
    let [a, r, g, b] = v.to_be_bytes();
    [r, g, b, a]
}

/// Qt の `qSmoothScaleImage`（乗算済み・アルファあり）。src は乗算済み RGBA。
pub fn smooth_scale(src: &PremulImage, dw: usize, dh: usize) -> Result<PremulImage, Error> {
    let (sw, sh) = (src.width(), src.height());
    if dw == 0 || dh == 0 || sw == 0 || sh == 0 {
        return Err(Error::Image("縮小の寸法"));
    }
    let xup = dw >= sw;
    let yup = dh >= sh;
    let xpoints = calc_points(sw, dw);
    let ypoints = calc_points(sh, dh);
    let xapoints = calc_apoints(sw, dw, xup);
    let yapoints = calc_apoints(sh, dh, yup);
    let pixels = src.pixels();
    let pixel = |i: usize| -> [u8; 4] { pixels[i * 4..i * 4 + 4].try_into().unwrap() };
    let mut out = vec![0u8; dw * dh * 4];
    for y in 0..dh {
        let row = ypoints[y] * sw;
        for x in 0..dw {
            let start = row + xpoints[x];
            let value: [u8; 4] = match (xup, yup) {
                (true, true) => {
                    let (xap, yap) = (xapoints[x], yapoints[y]);
                    if yap > 0 {
                        if xap > 0 {
                            interpolate_4(
                                pixel(start),
                                pixel(start + 1),
                                pixel(start + sw),
                                pixel(start + sw + 1),
                                xap,
                                yap,
                            )
                        } else {
                            interpolate_256(pixel(start), 256 - yap, pixel(start + sw), yap)
                        }
                    } else if xap > 0 {
                        interpolate_256(pixel(start), 256 - xap, pixel(start + 1), xap)
                    } else {
                        pixel(start)
                    }
                }
                (true, false) => {
                    let cy = yapoints[y] >> 16;
                    let yap = yapoints[y] & 0xffff;
                    let mut sum = helper(pixels, start, yap, cy, sw);
                    let xap = xapoints[x];
                    if xap > 0 {
                        sum = mix(sum, helper(pixels, start + 1, yap, cy, sw), xap);
                    }
                    sum.map(|c| (c >> 14) as u8)
                }
                (false, true) => {
                    let cx = xapoints[x] >> 16;
                    let xap = xapoints[x] & 0xffff;
                    let mut sum = helper(pixels, start, xap, cx, 1);
                    let yap = yapoints[y];
                    if yap > 0 {
                        sum = mix(sum, helper(pixels, start + sw, xap, cx, 1), yap);
                    }
                    sum.map(|c| (c >> 14) as u8)
                }
                (false, false) => {
                    let cy = yapoints[y] >> 16;
                    let yap = yapoints[y] & 0xffff;
                    let cx = xapoints[x] >> 16;
                    let xap = xapoints[x] & 0xffff;
                    let mut index = start;
                    let mut sum =
                        helper(pixels, index, xap, cx, 1).map(|c| (c >> 4).wrapping_mul(yap));
                    let mut j = (1i64 << 14) - yap as i64;
                    while j > cy as i64 {
                        index += sw;
                        let part = helper(pixels, index, xap, cx, 1);
                        for k in 0..4 {
                            sum[k] = sum[k].wrapping_add((part[k] >> 4).wrapping_mul(cy));
                        }
                        j -= cy as i64;
                    }
                    index += sw;
                    let part = helper(pixels, index, xap, cx, 1);
                    for k in 0..4 {
                        sum[k] = sum[k].wrapping_add((part[k] >> 4).wrapping_mul(j as u32));
                    }
                    sum.map(|c| (c >> 24) as u8)
                }
            };
            out[(y * dw + x) * 4..(y * dw + x) * 4 + 4].copy_from_slice(&value);
        }
    }
    PremulImage::from_premultiplied(dw, dh, out)
}

/// qdrawhelper の interpolate_4_pixels（NEON 版：上下を先に補間する）。
fn interpolate_4(
    tl: [u8; 4],
    tr: [u8; 4],
    bl: [u8; 4],
    br: [u8; 4],
    distx: u32,
    disty: u32,
) -> [u8; 4] {
    let mut out = [0u8; 4];
    for k in 0..4 {
        let left = (tl[k] as u32 * (256 - disty) + bl[k] as u32 * disty) as u16 >> 8;
        let right = (tr[k] as u32 * (256 - disty) + br[k] as u32 * disty) as u16 >> 8;
        out[k] = ((left as u32 * (256 - distx) + right as u32 * distx) as u16 >> 8) as u8;
    }
    out
}

/// 乗算済みの画素を Qt の PNG 書き出しと同じくストレートへ戻す（aarch64 の Qt 6、
/// convertARGBFromARGB32PM_neon：行の4画素ずつは NEON の逆数近似、残りは整数表）。
pub fn qt_unpremultiply(image: &PremulImage) -> Vec<u8> {
    let width = image.width();
    let groups = width / 4 * 4;
    let mut out = image.pixels().to_vec();
    for row in out.chunks_mut(width * 4) {
        for (x, pixel) in row.chunks_exact_mut(4).enumerate() {
            let alpha = u32::from(pixel[3]);
            if alpha == 255 {
                continue;
            }
            for channel in &mut pixel[..3] {
                let p = u32::from(*channel);
                *channel = if alpha == 0 {
                    0
                } else if x < groups {
                    let fixed = (p as f32 * neon_factor(alpha) * 2.0) as u32;
                    ((fixed + 1) >> 1).min(255) as u8
                } else {
                    ((p * (0x00ff_00ff / alpha) + 0x8000) >> 16).min(255) as u8
                };
            }
            if alpha == 0 {
                pixel.fill(0);
            }
        }
    }
    out
}

fn recip_estimate(x: f32) -> f32 {
    let bits = x.to_bits();
    let exponent = (bits >> 23) & 0xff;
    let scaled = 256 | ((bits & 0x7f_ffff) >> 15);
    let a = scaled * 2 + 1;
    let b = (1 << 19) / a;
    let r = b.div_ceil(2);
    f32::from_bits(((253 - exponent) << 23) | ((r & 0xff) << 15))
}

fn neon_factor(alpha: u32) -> f32 {
    let a = alpha as f32;
    let estimate = recip_estimate(a);
    let step = (2.0 - f64::from(a) * f64::from(estimate)) as f32;
    step * (estimate * 255.0)
}

/// 合成した画像を長辺 max 以下へ縮める（`thumbnail_png` の縮小。小さければそのまま）。
pub fn thumbnail_image(image: &PremulImage, max: usize) -> Result<PremulImage, Error> {
    let (width, height) = (image.width(), image.height());
    if width.max(height) <= max {
        return Ok(image.clone());
    }
    let (dw, dh) = keep_aspect_size(width, height, max);
    if (dw, dh) == (width, height) {
        return Ok(image.clone());
    }
    smooth_scale(image, dw, dh)
}

/// 乗算済みの画像のサムネイルの PNG（`thumbnail_png(image, max)`）。
pub fn thumbnail_png_of(image: &PremulImage, max: usize) -> Result<Vec<u8>, Error> {
    let small = thumbnail_image(image, max)?;
    let rgba = qt_unpremultiply(&small);
    encode_png(
        small.width(),
        small.height(),
        &rgba,
        Some((DEFAULT_DOTS_PER_METER, DEFAULT_DOTS_PER_METER)),
    )
}

/// 文書のコマ frame の .pman のサムネイル（白地の合成を長辺 512 以下、
/// `project_io._thumbnail_png(window.crop_image(current_frame))`）。
pub fn project_thumbnail(
    document: &Document,
    frame: usize,
    options: &DisplayOptions,
) -> Result<Vec<u8>, Error> {
    let image = compose::composite(document, frame, options, Background::White)?;
    thumbnail_png_of(&image, THUMBNAIL_SIZE)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sizes_follow_qsize_scaled() {
        assert_eq!(keep_aspect_size(1280, 720, 512), (512, 288));
        assert_eq!(keep_aspect_size(720, 1280, 512), (288, 512));
        assert_eq!(keep_aspect_size(2000, 1, 512), (512, 1));
        assert_eq!(keep_aspect_size(1000, 1000, 512), (512, 512));
    }

    #[test]
    fn flat_colors_stay_flat() {
        let image = PremulImage::filled(1000, 600, [30, 60, 90, 255]).unwrap();
        let small = thumbnail_image(&image, 512).unwrap();
        assert_eq!((small.width(), small.height()), (512, 307));
        assert!(small
            .pixels()
            .chunks_exact(4)
            .all(|p| p == [30, 60, 90, 255]));
        let tall = PremulImage::filled(600, 1, [10, 20, 30, 255]).unwrap();
        let small = thumbnail_image(&tall, 512).unwrap();
        assert_eq!((small.width(), small.height()), (512, 1));
        assert!(small
            .pixels()
            .chunks_exact(4)
            .all(|p| p == [10, 20, 30, 255]));
    }

    #[test]
    fn small_images_are_kept() {
        let image = PremulImage::filled(10, 5, [0, 0, 0, 0]).unwrap();
        let png = thumbnail_png_of(&image, 512).unwrap();
        let decoded = crate::project::png::decode_png(&png, 1 << 20).unwrap();
        assert_eq!((decoded.width, decoded.height), (10, 5));
    }
}
