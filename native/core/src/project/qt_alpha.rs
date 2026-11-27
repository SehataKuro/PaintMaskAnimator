//! Qt の乗算済み形式（ARGB32_Premultiplied）を往復したストレート RGBA。
//!
//! Python 版は PNG を QImage の乗算済み形式にしてから PmaImage にする（RGBA8888 に戻す）。
//! 半透明の画素はこの往復で丸められるため、同じ画素を得るにはこの丸めを再現する。
//!
//! 乗算は Qt の qPremultiply（全環境で同じ整数式）。戻しは aarch64 の Qt 6
//! （convertARGBFromARGB32PM_neon）に合わせる。4 画素ずつは NEON の逆数推定
//! （FRECPE と FRECPS 1 回）と 8.1 固定小数の丸め、行末の 4 未満の画素は
//! qUnpremultiply（0x00ff00ff / a の整数表）で戻す。x86 の Qt（SSE4・AVX2）は
//! 別の近似を使うため、半透明の画素が 1 ずれることがある。

/// Qt の qPremultiply の 1 チャンネル分。
fn premultiply(c: u32, a: u32) -> u32 {
    let t = c * a;
    (t + (t >> 8) + 0x80) >> 8
}

/// ARM の FRECPE（単精度・正の正規数）。
fn recip_estimate(x: f32) -> f32 {
    let bits = x.to_bits();
    let exponent = (bits >> 23) & 0xff;
    let scaled = 256 | ((bits & 0x7f_ffff) >> 15);
    let a = scaled * 2 + 1;
    let b = (1 << 19) / a;
    let r = b.div_ceil(2);
    f32::from_bits(((253 - exponent) << 23) | ((r & 0xff) << 15))
}

/// NEON の 255/a の近似（reciprocal_mul_ps）。FRECPS は融合積和で 1 回だけ丸める。
fn neon_factor(alpha: u32) -> f32 {
    let a = alpha as f32;
    let estimate = recip_estimate(a);
    // 2 - a*e は f64 で正確に求まり、f32 への変換で 1 回だけ丸める。
    let step = (2.0 - f64::from(a) * f64::from(estimate)) as f32;
    step * (estimate * 255.0)
}

struct Tables {
    // [alpha][premultiplied] -> ストレートの値（NEON とスカラー）。
    neon: Vec<u8>,
    scalar: Vec<u8>,
}

fn tables() -> &'static Tables {
    static TABLES: std::sync::OnceLock<Tables> = std::sync::OnceLock::new();
    TABLES.get_or_init(build_tables)
}

fn build_tables() -> Tables {
    let mut neon = vec![0u8; 256 * 256];
    let mut scalar = vec![0u8; 256 * 256];
    for alpha in 0..256u32 {
        let factor = if alpha == 0 { 0.0 } else { neon_factor(alpha) };
        let inverse = if alpha == 0 { 0 } else { 0x00ff_00ff / alpha };
        for p in 0..256u32 {
            let i = (alpha * 256 + p) as usize;
            scalar[i] = match alpha {
                0 => 0,
                255 => p as u8,
                _ => ((p * inverse + 0x8000) >> 16).min(255) as u8,
            };
            neon[i] = if alpha == 0 {
                0
            } else {
                // vcvtq_n_u32_f32(x, 1) は 2 倍して 0 方向へ切り捨て、vrshrn で 1 ビット丸める。
                let fixed = (p as f32 * factor * 2.0) as u32;
                ((fixed + 1) >> 1).min(255) as u8
            };
        }
    }
    Tables { neon, scalar }
}

/// 行優先のストレート RGBA8888 を Qt の乗算済み形式で往復した値に置き換える。
/// width は行の画素数（4 画素ずつの区切りが行ごとに始まる）。
pub fn qt_premultiplied_roundtrip(pixels: &mut [u8], width: usize) {
    if width == 0 {
        return;
    }
    let tables = tables();
    let groups = width / 4 * 4;
    for row in pixels.chunks_mut(width * 4) {
        for (x, pixel) in row.chunks_exact_mut(4).enumerate() {
            let alpha = u32::from(pixel[3]);
            if alpha == 255 {
                continue;
            }
            // NEON は 4 画素がすべて不透明なら写すだけだが、不透明の画素はどちらでも変わらない。
            let table = if x < groups {
                &tables.neon
            } else {
                &tables.scalar
            };
            for channel in &mut pixel[..3] {
                let p = premultiply(u32::from(*channel), alpha);
                *channel = table[(alpha * 256 + p) as usize];
            }
        }
    }
}

/// imaging.white_to_transparent_qimage: 不透明度が 0 でない純白 (#FFFFFF) を透明にする。
/// 変えた画素があれば true。
pub fn white_to_transparent(pixels: &mut [u8]) -> bool {
    let mut changed = false;
    for pixel in pixels.chunks_exact_mut(4) {
        if pixel[3] > 0 && pixel[..3] == [255, 255, 255] {
            pixel.fill(0);
            changed = true;
        }
    }
    changed
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opaque_and_transparent_are_kept() {
        let mut pixels = vec![1, 2, 3, 255, 9, 9, 9, 0, 255, 255, 255, 255, 7, 0, 0, 0];
        qt_premultiplied_roundtrip(&mut pixels, 4);
        assert_eq!(
            pixels,
            [1, 2, 3, 255, 0, 0, 0, 0, 255, 255, 255, 255, 0, 0, 0, 0]
        );
    }

    #[test]
    fn neon_and_scalar_rounding() {
        // a=2 の premultiplied 1 はスカラーで 128、NEON で 127（Qt 6.11 arm64 で確認）。
        let mut pixels = vec![64, 64, 64, 2];
        qt_premultiplied_roundtrip(&mut pixels, 1);
        assert_eq!(&pixels[..3], &[128, 128, 128]);
        let mut pixels = [64, 64, 64, 2].repeat(4);
        qt_premultiplied_roundtrip(&mut pixels, 4);
        assert_eq!(&pixels[..3], &[127, 127, 127]);
        // a=4 の premultiplied 1 は 64（63.75 の丸め）。
        let mut pixels = [64, 64, 64, 4].repeat(5);
        qt_premultiplied_roundtrip(&mut pixels, 5);
        assert_eq!((pixels[0], pixels[16]), (64, 64));
    }

    #[test]
    fn white_migration() {
        let mut pixels = vec![255, 255, 255, 255, 255, 255, 255, 0, 255, 255, 254, 9];
        assert!(white_to_transparent(&mut pixels));
        assert_eq!(pixels, [0, 0, 0, 0, 255, 255, 255, 0, 255, 255, 254, 9]);
        assert!(!white_to_transparent(&mut pixels));
    }
}
