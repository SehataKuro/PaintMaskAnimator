//! 減色パレットの構築と適用。

use crate::{
    color_stats::rgba_pixels,
    colors::{local_color_variation_rgb, sorted_color_counts},
    tone_curve::tone_curve_lut,
    Error,
};

fn rgb_colors(bytes: &[u8]) -> Result<&[[u8; 3]], Error> {
    let (colors, tail) = bytes.as_chunks::<3>();
    if !tail.is_empty() {
        return Err(Error::Invalid("expected packed RGB colors".into()));
    }
    Ok(colors)
}

/// `color_ops._hue_cluster_palette_from_samples_numpy` の np.unique と同じ順序で色と頻度を返す。
pub fn palette_color_counts(sample: &[u8]) -> Result<(Vec<u8>, Vec<usize>), Error> {
    let counts = sorted_color_counts(rgb_colors(sample)?);
    let mut colors = Vec::with_capacity(counts.len() * 3);
    let mut weights = Vec::with_capacity(counts.len());
    for entry in counts {
        colors.extend(entry.rgb);
        weights.push(entry.count);
    }
    Ok((colors, weights))
}

fn feature_distance(left: [f32; 4], right: [f32; 4]) -> f32 {
    let [a, b, c, d] = std::array::from_fn(|i| left[i] - right[i]);
    ((a * a + b * b) + c * c) + d * d
}

fn better_min(candidate: f32, best: f32) -> bool {
    candidate < best || (candidate.is_nan() && !best.is_nan())
}

fn better_max(candidate: f64, best: f64) -> bool {
    candidate > best || (candidate.is_nan() && !best.is_nan())
}

/// `color_ops._hue_cluster_palette_from_samples_numpy` と同じ順序で、実在 RGB の代表色を返す。
/// 特徴量と頻度の log1p は NumPy で計算した値を受け取る。
pub fn hue_cluster_palette(
    colors: &[u8],
    features: &[[f32; 4]],
    weights: &[f64],
    log_weights: &[f64],
    color_count: usize,
) -> Result<Vec<u8>, Error> {
    let rgb = rgb_colors(colors)?;
    let n = rgb.len();
    if color_count == 0
        || features.len() != n
        || weights.len() != n
        || log_weights.len() != n
        || weights
            .iter()
            .any(|weight| !weight.is_finite() || *weight <= 0.0)
    {
        return Err(Error::Invalid("invalid palette cluster inputs".into()));
    }
    if n <= color_count {
        return Ok(colors.to_vec());
    }

    let first = (1..n).fold(
        0,
        |best, i| if weights[i] > weights[best] { i } else { best },
    );
    let mut centers = vec![features[first]];
    let mut minimum: Vec<_> = features
        .iter()
        .map(|&f| feature_distance(f, centers[0]))
        .collect();
    while centers.len() < color_count {
        let score = |i: usize| f64::from(minimum[i]) * (1.0 + log_weights[i]);
        let next = (1..n).fold(0, |best, i| {
            if better_max(score(i), score(best)) {
                i
            } else {
                best
            }
        });
        centers.push(features[next]);
        for (min, &feature) in minimum.iter_mut().zip(features) {
            let distance = feature_distance(feature, features[next]);
            if better_min(distance, *min) {
                *min = distance;
            }
        }
    }

    let mut labels = vec![0; n];
    let mut members = vec![Vec::new(); color_count];
    for iteration in 0..12 {
        for group in &mut members {
            group.clear();
        }
        let mut changed = false;
        let mut farthest = 0;
        let mut farthest_distance = f32::NEG_INFINITY;
        for (i, &feature) in features.iter().enumerate() {
            let mut best = 0;
            let mut distance = feature_distance(feature, centers[0]);
            for (j, &center) in centers.iter().enumerate().skip(1) {
                let candidate = feature_distance(feature, center);
                if better_min(candidate, distance) {
                    best = j;
                    distance = candidate;
                }
            }
            if better_max(f64::from(distance), f64::from(farthest_distance)) {
                farthest = i;
                farthest_distance = distance;
            }
            changed |= labels[i] != best;
            labels[i] = best;
            members[best].push(i);
        }
        if iteration > 0 && !changed {
            break;
        }
        for (center, group) in centers.iter_mut().zip(&members) {
            if group.is_empty() {
                *center = features[farthest];
            } else {
                *center = weighted_mean(features, weights, group).map(|value| value as f32);
            }
        }
    }

    let mut palette = Vec::with_capacity(color_count * 3);
    for (&center, group) in centers.iter().zip(&members) {
        let Some(&first) = group.first() else {
            continue;
        };
        let average = weighted_mean(rgb, weights, group);
        let score = |i: usize| {
            let [r, g, b] = std::array::from_fn(|j| f64::from(rgb[i][j]) - average[j]);
            let rgb_distance = ((r * r + g * g) + b * b) / (255.0 * 255.0 * 3.0);
            let feature_distance = f64::from(feature_distance(features[i], center));
            (feature_distance + rgb_distance * 0.08) / (1.0 + 0.12 * log_weights[i])
        };
        let mut best = first;
        let mut best_score = score(best);
        for &i in &group[1..] {
            let candidate = score(i);
            if candidate < best_score || (candidate.is_nan() && !best_score.is_nan()) {
                best = i;
                best_score = candidate;
            }
        }
        palette.extend(rgb[best]);
    }
    Ok(palette)
}

fn weighted_mean<T: Copy + Into<f64>, const N: usize>(
    values: &[[T; N]],
    weights: &[f64],
    members: &[usize],
) -> [f64; N] {
    let mut sum = [0.0; N];
    let mut weight_sum = 0.0;
    for &i in members {
        let weight = weights[i];
        weight_sum += weight;
        for (output, value) in sum.iter_mut().zip(values[i]) {
            *output += weight * value.into();
        }
    }
    sum.map(|value| value / weight_sum)
}

fn rgb_distance(left: &[u8], right: [u8; 3]) -> i32 {
    let diff = |i: usize| i32::from(i16::from(left[i]) - i16::from(right[i]));
    let (r, g, b) = (diff(0), diff(1), diff(2));
    r * r + g * g + b * b
}

/// `color_ops._palette_training_samples_numpy` と同じ行優先順で手本の RGB を返す。
pub fn palette_training_samples(
    rgba: &[u8],
    width: usize,
    height: usize,
    threshold: u8,
    opaque: bool,
    background: Option<[u8; 3]>,
) -> Result<Vec<u8>, Error> {
    let pixels = rgba_pixels(rgba, width, height)?;
    let visible = |pixel: &[u8; 4]| opaque || pixel[3] >= threshold;
    if !opaque && !pixels.iter().any(visible) {
        return Ok(Vec::new());
    }
    let background = background.filter(|_| opaque);
    let distant =
        |pixel: &[u8; 4], limit| background.is_none_or(|bg| rgb_distance(pixel, bg) > limit);
    let variation = local_color_variation_rgb(pixels, width);
    let mut samples = Vec::new();
    for (pixel, change) in pixels.iter().zip(variation) {
        if change <= 12 && visible(pixel) && distant(pixel, 18 * 18 * 3) {
            samples.extend_from_slice(&pixel[..3]);
        }
    }
    if samples.len() < 32 * 3 {
        samples.clear();
        for pixel in pixels {
            if visible(pixel) && distant(pixel, 26 * 26 * 3) {
                samples.extend_from_slice(&pixel[..3]);
            }
        }
    }
    if samples.is_empty() {
        for pixel in pixels.iter().filter(|pixel| visible(pixel)) {
            samples.extend_from_slice(&pixel[..3]);
        }
    }
    Ok(samples)
}

/// `color_ops.build_color_reduction_palette_numpy` と同じ順序で候補を選別する。
pub fn select_palette_colors(
    training: &[u8],
    priority: &[u8],
    clustered: &[u8],
    color_count: usize,
    line: bool,
    background: Option<[u8; 3]>,
) -> Result<Vec<u8>, Error> {
    let training = rgb_colors(training)?;
    let priority = rgb_colors(priority)?;
    let clustered = rgb_colors(clustered)?;
    if color_count < 2 {
        return Err(Error::Invalid(
            "expected at least two palette entries".into(),
        ));
    }

    let foreground_count = color_count - usize::from(background.is_some());
    let mut foreground = Vec::new();
    let required = 8.max((training.len() as f64 * 0.001).ceil() as usize);
    for &color in priority.iter().chain(clustered) {
        if line {
            let max = *color.iter().max().unwrap_or(&0);
            let min = *color.iter().min().unwrap_or(&0);
            let saturation = f64::from(max - min) / f64::from(max.max(1));
            if saturation >= 0.25
                && training
                    .iter()
                    .filter(|pixel| rgb_distance(*pixel, color) <= 55 * 55 * 3)
                    .take(required)
                    .count()
                    < required
            {
                continue;
            }
        }
        if foreground
            .iter()
            .any(|&existing| rgb_distance(&color, existing) <= 10 * 10 * 3)
        {
            continue;
        }
        foreground.push(color);
        if foreground.len() >= foreground_count {
            break;
        }
    }

    Ok(background
        .into_iter()
        .chain(
            foreground
                .into_iter()
                .filter(|color| Some(*color) != background),
        )
        .take(color_count)
        .flatten()
        .collect())
}

#[derive(Clone, Copy)]
struct LineFeature {
    rgb: [f32; 3],
    luma: f32,
    saturation: f32,
}

impl LineFeature {
    fn new(pixel: &[u8]) -> Self {
        let rgb = std::array::from_fn(|i| f32::from(pixel[i]) / 255.0);
        let [r, g, b] = rgb;
        let luma = (r * 0.2126 + g * 0.7152) + b * 0.0722;
        let value = r.max(g).max(b);
        let saturation = (value - r.min(g).min(b)) / value.max(1e-6);
        Self {
            rgb,
            luma,
            saturation,
        }
    }

    fn distance(self, target: Self) -> f32 {
        let [r, g, b] = std::array::from_fn(|i| self.rgb[i] - target.rgb[i]);
        let rgb_distance = (r * r + g * g) + b * b;
        let luma = self.luma - target.luma;
        let excess = ((target.saturation - self.saturation) - 0.12).max(0.0);
        (rgb_distance + (luma * luma) * 2.4) + (excess * excess) * 4.0
    }
}

fn line_labels<const N: usize>(
    pixels: &[[u8; N]],
    width: usize,
    visible: impl Fn(usize) -> bool,
    palette: &[[u8; 3]],
) -> Vec<i16> {
    let targets: Vec<_> = palette.iter().map(|rgb| LineFeature::new(rgb)).collect();
    let darkest = (1..targets.len()).fold(0, |best, i| {
        if targets[i].luma < targets[best].luma {
            i
        } else {
            best
        }
    });
    let darkest_luma = f64::from(targets[darkest].luma);
    let dark_cutoff = 0.20_f64.max(darkest_luma + 0.12) as f32;
    let variation = (darkest_luma <= 0.32).then(|| local_color_variation_rgb(pixels, width));
    let mut previous = None;
    let mut labels = Vec::with_capacity(pixels.len());
    for (i, pixel) in pixels.iter().enumerate() {
        if !visible(i) {
            labels.push(-1);
            continue;
        }
        let rgb = [pixel[0], pixel[1], pixel[2]];
        let (luma, label) = match previous {
            Some((color, luma, label)) if color == rgb => (luma, label),
            _ => {
                let feature = LineFeature::new(&rgb);
                let mut best = 0;
                let mut distance = feature.distance(targets[0]);
                for (j, &target) in targets.iter().enumerate().skip(1) {
                    let candidate = feature.distance(target);
                    if candidate < distance {
                        distance = candidate;
                        best = j;
                    }
                }
                previous = Some((rgb, feature.luma, best));
                (feature.luma, best)
            }
        };
        let dark_line = variation
            .as_ref()
            .is_some_and(|changes| luma <= dark_cutoff && (changes[i] >= 28 || luma <= 0.10));
        labels.push(if dark_line {
            darkest as i16
        } else {
            label as i16
        });
    }
    labels
}

fn check_label_palette(palette: &[u8]) -> Result<&[[u8; 3]], Error> {
    let colors = rgb_colors(palette)?;
    if colors.is_empty() || colors.len() > i16::MAX as usize {
        return Err(Error::Invalid(
            "invalid palette size for int16 labels".into(),
        ));
    }
    Ok(colors)
}

/// `color_ops._line_palette_labels_numpy` と同じ int16 のパレット番号を返す。
pub fn line_palette_labels(
    rgb: &[u8],
    width: usize,
    height: usize,
    visible: &[u8],
    palette: &[u8],
) -> Result<Vec<i16>, Error> {
    let pixels = rgb_colors(rgb)?;
    if width.checked_mul(height) != Some(pixels.len()) || visible.len() != pixels.len() {
        return Err(Error::Invalid("RGB and mask dimensions must agree".into()));
    }
    Ok(line_labels(
        pixels,
        width,
        |i| visible[i] != 0,
        check_label_palette(palette)?,
    ))
}

/// `color_ops.apply_color_reduction_palette_numpy` と同じ減色画素を RGBA8888 に書く。
/// 色面モードでは既存のラベル付けを呼び、線画モードでは RGBA から直接ラベルを作る。
#[allow(clippy::too_many_arguments)]
pub fn apply_color_reduction_palette(
    rgba: &[u8],
    width: usize,
    height: usize,
    palette: &[u8],
    threshold: u8,
    opaque: bool,
    background: Option<[u8; 3]>,
    points: Option<Vec<(f64, f64)>>,
    surface: Option<impl FnOnce(&[u8], &[u8]) -> Vec<i16>>,
    output: &mut [u8],
) -> Result<(), Error> {
    let pixels = rgba_pixels(rgba, width, height)?;
    let colors = check_label_palette(palette)?;
    if output.len() != rgba.len() {
        return Err(Error::Invalid("expected equally sized RGBA output".into()));
    }
    let visible = |i: usize| opaque || pixels[i][3] >= threshold;
    let labels = if let Some(surface) = surface {
        let rgb: Vec<_> = pixels.iter().flat_map(|&[r, g, b, _]| [r, g, b]).collect();
        let mask: Vec<_> = (0..pixels.len()).map(|i| u8::from(visible(i))).collect();
        surface(&rgb, &mask)
    } else {
        line_labels(pixels, width, visible, colors)
    };
    if labels.len() != pixels.len() {
        return Err(Error::Invalid(
            "label dimensions must match the image".into(),
        ));
    }
    let lut = points.map(tone_curve_lut);
    let (output, _) = output.as_chunks_mut::<4>();
    for (i, (&label, dest)) in labels.iter().zip(output).enumerate() {
        let valid = visible(i) && label >= 0;
        let [r, g, b] = if valid {
            *colors
                .get(label as usize)
                .ok_or_else(|| Error::Invalid("palette label out of range".into()))?
        } else if opaque {
            background.unwrap_or([0; 3])
        } else {
            [0; 3]
        };
        let pixel = [r, g, b, if opaque || valid { 255 } else { 0 }];
        *dest = match &lut {
            Some(lut) => crate::tone_curve::tone_curve_pixel(pixel, lut, background),
            None => pixel,
        };
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sorted_counts_and_first_ties() {
        let (colors, counts) = palette_color_counts(&[200, 0, 0, 0, 0, 0, 200, 0, 0]).unwrap();
        assert_eq!(colors, [0, 0, 0, 200, 0, 0]);
        assert_eq!(counts, [1, 2]);
        let features = [[0.0; 4], [0.0; 4]];
        assert_eq!(
            hue_cluster_palette(&colors, &features, &[1., 1.], &[0.5, 0.5], 1).unwrap(),
            [0, 0, 0],
        );
        assert!(better_min(f32::NAN, 0.0));
        assert!(!better_max(f64::NAN, f64::NAN));
    }

    #[test]
    fn training_restores_visible_pixels_and_respects_threshold() {
        let rgba = [100, 100, 100, 255, 9, 10, 11, 127, 110, 110, 110, 128];
        assert_eq!(
            palette_training_samples(&rgba, 3, 1, 128, false, None).unwrap(),
            [100, 100, 100, 110, 110, 110],
        );
        assert_eq!(
            palette_training_samples(&rgba, 3, 1, 128, true, Some([100; 3])).unwrap(),
            [9, 10, 11],
        );
        assert!(palette_training_samples(&rgba, 2, 2, 128, false, None).is_err());
    }

    #[test]
    fn candidates_keep_priority_and_reserved_background_order() {
        assert_eq!(
            select_palette_colors(
                &[0; 24],
                &[255, 255, 255, 255, 0, 0],
                &[248, 248, 248, 0, 0, 0],
                4,
                true,
                Some([255; 3]),
            )
            .unwrap(),
            [255, 255, 255, 0, 0, 0],
        );
    }

    #[test]
    fn line_and_fused_rgba() {
        let palette = [0, 0, 0, 255, 255, 255, 255, 255, 255];
        let rgba = [0, 0, 0, 255, 255, 255, 255, 128, 0, 0, 0, 127];
        assert_eq!(
            line_palette_labels(
                &[0, 0, 0, 255, 255, 255, 0, 0, 0],
                1,
                3,
                &[1, 1, 0],
                &palette
            )
            .unwrap(),
            [0, 1, -1],
        );
        let mut output = [0; 12];
        apply_color_reduction_palette(
            &rgba,
            1,
            3,
            &palette,
            128,
            false,
            None,
            None,
            None::<fn(&[u8], &[u8]) -> Vec<i16>>,
            &mut output,
        )
        .unwrap();
        assert_eq!(output, [0, 0, 0, 255, 255, 255, 255, 255, 0, 0, 0, 0]);
    }
}
