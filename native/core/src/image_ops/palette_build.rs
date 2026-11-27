//! 2 値化の共通パレットを作る（`color_ops.build_color_reduction_palette`）。
//!
//! 手本の画素の取り出し・色相でまとめる処理・色の選び方は既存の Rust（`palette`）を使い、
//! その間をつなぐ Python 側の計算（間引き・優先色・色相の特徴量）をここで行う。
//! 特徴量は NumPy の単精度と同じ式で求め、cos・sin も NumPy の SIMD 版
//! （Cody-Waite の範囲縮小と多項式）と同じ値にする。
use crate::image::RgbaImage;
use crate::palette;
use crate::Error;
use std::collections::BTreeMap;

fn numpy_sincos(x: f32, cos: bool) -> f32 {
    let two_over_pi = f32::from_bits(0x3f22_f983);
    let pio2_high = -f32::from_bits(0x3fc9_0fd8);
    let pio2_med = -f32::from_bits(0x34a8_885a);
    let pio2_low = -f32::from_bits(0x27c2_34c4);
    let magic = f32::from_bits(0x4b40_0000);
    // この範囲の外（と NaN）は libm に任せる（NumPy もスカラー版で求め直す）。
    let limit = f32::from_bits(if cos { 0x478b_9a08 } else { 0x47e5_5dff });
    if x.is_nan() || x.abs() > limit {
        return if cos { x.cos() } else { x.sin() };
    }
    let quadrant = x * two_over_pi + magic - magic;
    let r = quadrant.mul_add(pio2_high, x);
    let r = quadrant.mul_add(pio2_med, r);
    let r = quadrant.mul_add(pio2_low, r);
    let r2 = r * r;
    let c = f32::from_bits(0x37cc_730b).mul_add(r2, -f32::from_bits(0x3ab6_036e));
    let c = c.mul_add(r2, f32::from_bits(0x3d2a_aa9e));
    let c = c.mul_add(r2, -0.5);
    let c = c.mul_add(r2, 1.0);
    let s = f32::from_bits(0x363e_9dde).mul_add(r2, -f32::from_bits(0x3950_35dd));
    let s = s.mul_add(r2, f32::from_bits(0x3c08_88cd));
    let s = s.mul_add(r2, -f32::from_bits(0x3e2a_aaab));
    let s = s.mul_add(r2, 0.0);
    let s = s.mul_add(r, r);
    let mut iq = quadrant.round_ties_even() as i32;
    if cos {
        iq += 1;
    }
    let value = if iq & 1 == 0 { s } else { c };
    if iq & 2 == 2 {
        0.0 - value
    } else {
        value
    }
}

/// NumPy の単精度 `np.remainder`（除数は正）。
fn remainder(a: f32, b: f32) -> f32 {
    let m = a % b;
    if m != 0.0 && m < 0.0 {
        m + b
    } else if m == 0.0 {
        0.0
    } else {
        m
    }
}

/// `colors.rgb_hsv_features` の色相（0〜1）・彩度・明度（単精度）。
fn hue_saturation_value(rgb: [u8; 3]) -> (f32, f32, f32) {
    let v = rgb.map(|c| c as f32 / 255.0);
    let maximum = v[0].max(v[1]).max(v[2]);
    let minimum = v[0].min(v[1]).min(v[2]);
    let delta = maximum - minimum;
    let saturation = delta / maximum.max(1e-6);
    let hue = if delta <= 1e-6 {
        0.0
    } else if v[0] >= v[1] && v[0] >= v[2] {
        remainder((v[1] - v[2]) / delta, 6.0)
    } else if v[1] >= v[2] {
        (v[2] - v[0]) / delta + 2.0
    } else {
        (v[0] - v[1]) / delta + 4.0
    };
    (remainder(hue / 6.0, 1.0), saturation, maximum)
}

/// `colors.rgb_hsv_features`：近い色相を近いベクトルへ写した特徴量。
pub fn rgb_hsv_features(rgb: [u8; 3]) -> [f32; 4] {
    let (hue, saturation, maximum) = hue_saturation_value(rgb);
    let angle = hue * (2.0 * std::f64::consts::PI) as f32;
    let strength = saturation.powf(0.72);
    [
        numpy_sincos(angle, true) * strength * 2.35,
        numpy_sincos(angle, false) * strength * 2.35,
        saturation * 0.45,
        maximum * 0.52,
    ]
}

/// `colors.priority_palette_colors_from_samples`：少面積でも残したい黒・主要色相を、
/// 手本の実在色から `maximum` 色まで選ぶ（支持画素の多い順）。
pub fn priority_palette_colors(sample: &[[u8; 3]], maximum: usize) -> Vec<[u8; 3]> {
    if sample.is_empty() || maximum == 0 {
        return Vec::new();
    }
    let mut counts: BTreeMap<[u8; 3], usize> = BTreeMap::new();
    for &rgb in sample {
        *counts.entry(rgb).or_default() += 1;
    }
    let colours: Vec<([u8; 3], usize, f32, f32, f32)> = counts
        .into_iter()
        .map(|(rgb, count)| {
            let (hue, saturation, value) = hue_saturation_value(rgb);
            (rgb, count, hue, saturation, value)
        })
        .collect();
    let total = sample.len() as f64;
    let minimum_support = 2usize.max((total * 0.00005).ceil() as usize);
    let chromatic_minimum_support = 8usize.max((total * 0.001).ceil() as usize);
    let targets: [([i32; 3], Option<f32>); 6] = [
        ([0, 0, 0], None),
        ([255, 0, 0], Some(0.0)),
        ([0, 0, 255], Some((2.0f64 / 3.0) as f32)),
        ([0, 255, 0], Some((1.0f64 / 3.0) as f32)),
        ([255, 0, 255], Some((5.0f64 / 6.0) as f32)),
        ([255, 255, 0], Some((1.0f64 / 6.0) as f32)),
    ];
    let mut chosen: Vec<(usize, usize, [u8; 3])> = Vec::new();
    for (group, &(target, target_hue)) in targets.iter().enumerate() {
        let members: Vec<&([u8; 3], usize, f32, f32, f32)> = colours
            .iter()
            .filter(|&&(_, _, hue, saturation, value)| match target_hue {
                None => value <= 0.24,
                Some(target_hue) => {
                    let mut distance = (hue - target_hue).abs();
                    distance = distance.min(1.0 - distance);
                    let tolerance = if group == 3 {
                        (1.0f64 / 8.0) as f32
                    } else {
                        (1.0f64 / 12.0) as f32
                    };
                    saturation >= 0.38 && value >= 0.18 && distance <= tolerance
                }
            })
            .collect();
        let support: usize = members.iter().map(|m| m.1).sum();
        let required = if group == 0 {
            minimum_support
        } else {
            chromatic_minimum_support
        };
        if support < required {
            continue;
        }
        let mut best: Option<(f64, [u8; 3])> = None;
        for &&(rgb, count, ..) in &members {
            let c = rgb.map(i32::from);
            let score = if group == 0 {
                // 黒線は面積が小さくても、実在する最暗色を選ぶ。
                let luminance =
                    f64::from(c[0]) * 0.2126 + f64::from(c[1]) * 0.7152 + f64::from(c[2]) * 0.0722;
                let chroma = c.iter().max().unwrap() - c.iter().min().unwrap();
                luminance + f64::from(chroma) * 0.20
            } else {
                let distance: i32 = (0..3)
                    .map(|i| (c[i] - target[i]) * (c[i] - target[i]))
                    .sum();
                let bonus = 1.0 + 0.30 * (count as f64).ln_1p();
                f64::from(distance) / bonus
            };
            if best.is_none_or(|(best_score, _)| score < best_score) {
                best = Some((score, rgb));
            }
        }
        if let Some((_, rgb)) = best {
            chosen.push((support, group, rgb));
        }
    }
    chosen.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(&b.1)));
    chosen
        .into_iter()
        .take(maximum)
        .map(|(_, _, rgb)| rgb)
        .collect()
}

fn flatten(colours: &[[u8; 3]]) -> Vec<u8> {
    colours.concat()
}

fn rgb_triples(bytes: &[u8]) -> Vec<[u8; 3]> {
    bytes.chunks_exact(3).map(|c| [c[0], c[1], c[2]]).collect()
}

/// `color_ops._hue_cluster_palette_from_samples`：色相を主軸に、近い色を同じ面へまとめる。
pub fn hue_cluster_palette(sample: &[[u8; 3]], color_count: usize) -> Result<Vec<[u8; 3]>, Error> {
    let color_count = color_count.max(1);
    let (colours, weights) = palette::palette_color_counts(&flatten(sample))?;
    let colours = rgb_triples(&colours);
    if colours.len() <= color_count {
        return Ok(colours);
    }
    let features: Vec<[f32; 4]> = colours.iter().map(|&rgb| rgb_hsv_features(rgb)).collect();
    let weights: Vec<f64> = weights.into_iter().map(|w| w as f64).collect();
    let log_weights: Vec<f64> = weights.iter().map(|w| w.ln_1p()).collect();
    palette::hue_cluster_palette(
        &flatten(&colours),
        &features,
        &weights,
        &log_weights,
        color_count,
    )
    .map(|bytes| rgb_triples(&bytes))
}

/// 共通パレットの設定（`build_color_reduction_palette` の引数）。
#[derive(Clone, Copy, Debug)]
pub struct PaletteOptions {
    /// 色数（2〜100 に丸める）。
    pub color_count: usize,
    /// 半透明を色として数える不透明度のしきい値（1〜254 に丸める）。
    pub alpha_threshold: i64,
    /// 手本の画素の上限（ダイアログのプレビューは 131072、既定は 262144）。
    pub max_samples: usize,
    /// 背景を不透明な色として扱うか（ライン抽出では常に真）。
    pub opaque_background: bool,
    /// 背景色。`opaque_background` のとき 1 色を背景に取っておく。
    pub background: Option<[u8; 3]>,
    /// ライン抽出か（偽なら色面）。
    pub line: bool,
}

/// `numpy.linspace(0, len - 1, count, dtype=int64)`。
fn linspace_indices(len: usize, count: usize) -> Vec<usize> {
    let stop = (len - 1) as f64;
    if count == 1 {
        return vec![0];
    }
    let step = stop / (count - 1) as f64;
    let mut out: Vec<usize> = (0..count)
        .map(|i| {
            let value = if step == 0.0 {
                i as f64 / (count - 1) as f64 * stop
            } else {
                i as f64 * step
            };
            value as usize
        })
        .collect();
    out[count - 1] = len - 1;
    out
}

/// 1 枚目と代表フレームの画素から共通パレットを作る（Issue #10）。
///
/// `images[0]` が 1 枚目、残りが代表フレーム（どれもトーンカーブをかけたあと）。
pub fn build_color_reduction_palette(
    images: &[&RgbaImage],
    options: &PaletteOptions,
) -> Result<Vec<[u8; 3]>, Error> {
    let color_count = options.color_count.clamp(2, 100);
    let threshold = options.alpha_threshold.clamp(1, 254) as u8;
    let background = options.background.filter(|_| options.opaque_background);
    let mut training: Vec<[u8; 3]> = Vec::new();
    for image in images {
        if image.rgba().is_empty() {
            continue;
        }
        let samples = palette::palette_training_samples(
            image.rgba(),
            image.width(),
            image.height(),
            threshold,
            options.opaque_background,
            background,
        )?;
        training.extend(rgb_triples(&samples));
    }
    if training.is_empty() {
        return Ok(vec![[0, 0, 0]]);
    }
    if training.len() > options.max_samples {
        training = linspace_indices(training.len(), options.max_samples)
            .into_iter()
            .map(|index| training[index])
            .collect();
    }
    let foreground = if background.is_some() {
        color_count - 1
    } else {
        color_count
    }
    .max(1);
    let priority = if options.line {
        priority_palette_colors(&training, foreground)
    } else {
        priority_palette_colors(&training, if foreground >= 6 { foreground } else { 0 })
    };
    let clustered = hue_cluster_palette(&training, foreground)?;
    palette::select_palette_colors(
        &flatten(&training),
        &flatten(&priority),
        &flatten(&clustered),
        color_count,
        options.line,
        background,
    )
    .map(|bytes| rgb_triples(&bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sincos_is_close_to_libm() {
        for i in 0..2000 {
            let x = i as f32 * 0.00314159;
            assert!((numpy_sincos(x, true) - x.cos()).abs() < 1e-6);
            assert!((numpy_sincos(x, false) - x.sin()).abs() < 1e-6);
        }
        assert_eq!(numpy_sincos(0.0, true), 1.0);
    }

    #[test]
    fn priority_picks_black_and_red() {
        let mut sample = vec![[255, 255, 255]; 50];
        sample.extend([[10, 10, 12]; 5]);
        sample.extend([[250, 5, 5]; 20]);
        let picked = priority_palette_colors(&sample, 6);
        assert_eq!(picked, [[250, 5, 5], [10, 10, 12]]);
        assert!(priority_palette_colors(&sample, 0).is_empty());
    }

    #[test]
    fn linspace_matches_numpy_ends() {
        assert_eq!(linspace_indices(10, 4), [0, 3, 6, 9]);
        assert_eq!(linspace_indices(5, 1), [0]);
    }
}
