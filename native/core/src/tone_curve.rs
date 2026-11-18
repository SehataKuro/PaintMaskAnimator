//! トーンカーブの補間と RGBA8888 への適用。

use crate::{color_stats::rgba_pixels, colors::normalize_tone_curve_points, Error};

fn endpoint_tangent(h0: f64, h1: f64, s0: f64, s1: f64) -> f64 {
    let tangent = ((2.0 * h0 + h1) * s0 - h0 * s1) / (h0 + h1).max(1e-12);
    if tangent * s0 <= 0.0 {
        0.0
    } else if s0 * s1 < 0.0 && tangent.abs() > (3.0 * s0).abs() {
        3.0 * s0
    } else {
        tangent
    }
}

fn clip_unit(value: f64) -> f64 {
    value.clamp(0.0, 1.0)
}

/// `color_ops.tone_curve_samples_numpy` と同じ順序で補間した f64 のサンプルを返す。
pub fn tone_curve_samples(points: Vec<(f64, f64)>, values: &[f64]) -> Vec<f64> {
    let points = normalize_tone_curve_points(points);
    if points.len() == 1 {
        return vec![points[0].1; values.len()];
    }
    if points.len() == 2 {
        let [(x0, y0), (x1, y1)] = [points[0], points[1]];
        let slope = (y1 - y0) / (x1 - x0);
        return values
            .iter()
            .map(|&value| {
                clip_unit(if value.is_nan() {
                    value
                } else if value <= x0 {
                    y0
                } else if value >= x1 {
                    y1
                } else {
                    slope * (value - x0) + y0
                })
            })
            .collect();
    }

    let intervals: Vec<_> = points.windows(2).map(|p| p[1].0 - p[0].0).collect();
    let slopes: Vec<_> = points
        .windows(2)
        .zip(&intervals)
        .map(|(p, &h)| (p[1].1 - p[0].1) / h.max(1e-12))
        .collect();
    let last = points.len() - 1;
    let mut tangents = vec![0.0; points.len()];
    tangents[0] = endpoint_tangent(intervals[0], intervals[1], slopes[0], slopes[1]);
    tangents[last] = endpoint_tangent(
        intervals[last - 1],
        intervals[last - 2],
        slopes[last - 1],
        slopes[last - 2],
    );
    for i in 1..last {
        let (left, right) = (slopes[i - 1], slopes[i]);
        if left == 0.0 || right == 0.0 || left * right <= 0.0 {
            continue;
        }
        let left_weight = 2.0 * intervals[i] + intervals[i - 1];
        let right_weight = intervals[i] + 2.0 * intervals[i - 1];
        tangents[i] = (left_weight + right_weight) / (left_weight / left + right_weight / right);
    }

    values
        .iter()
        .map(|&value| {
            // NumPy は NaN を末尾の後に置く。補間しても NaN なので、
            // 符号とペイロードを CPU の NaN 伝播規則に任せず quiet bit だけ立てる。
            if value.is_nan() {
                return f64::from_bits(value.to_bits() | (1 << 51));
            }
            let i = points
                .partition_point(|&(x, _)| x <= value)
                .saturating_sub(1)
                .min(last - 1);
            let (x0, y0) = points[i];
            let (x1, y1) = points[i + 1];
            let interval = (x1 - x0).max(1e-12);
            let t = clip_unit((value - x0) / interval);
            let t2 = t * t;
            let t3 = t2 * t;
            clip_unit(
                (2.0 * t3 - 3.0 * t2 + 1.0) * y0
                    + (t3 - 2.0 * t2 + t) * interval * tangents[i]
                    + (-2.0 * t3 + 3.0 * t2) * y1
                    + (t3 - t2) * interval * tangents[i + 1],
            )
        })
        .collect()
}

/// `color_ops.tone_curve_lut_numpy` と同じ 256 段階の RGB 変換表を返す。
pub fn tone_curve_lut(points: Vec<(f64, f64)>) -> [u8; 256] {
    let inputs: [f64; 256] = std::array::from_fn(|i| i as f64 / 255.0);
    let samples = tone_curve_samples(points, &inputs);
    std::array::from_fn(|i| (samples[i] * 255.0).round_ties_even().clamp(0.0, 255.0) as u8)
}

/// `color_ops.apply_tone_curve_numpy` と同じ RGB とアルファを出力用 RGBA8888 に書く。
/// 背景に近いかどうかは変換前の RGB で判定する。
pub fn apply_tone_curve(
    rgba: &[u8],
    width: usize,
    height: usize,
    points: Vec<(f64, f64)>,
    background: Option<[u8; 3]>,
    output: &mut [u8],
) -> Result<(), Error> {
    let pixels = rgba_pixels(rgba, width, height)?;
    if output.len() != rgba.len() {
        return Err(Error::Invalid("expected equally sized RGBA output".into()));
    }
    let lut = tone_curve_lut(points);
    let (output, _) = output.as_chunks_mut::<4>();
    if let Some([br, bg, bb]) = background {
        for (&[r, g, b, a], dest) in pixels.iter().zip(output) {
            let dr = i32::from(i16::from(r) - i16::from(br));
            let dg = i32::from(i16::from(g) - i16::from(bg));
            let db = i32::from(i16::from(b) - i16::from(bb));
            *dest = if dr * dr + dg * dg + db * db <= 75 {
                [br, bg, bb, a]
            } else {
                [lut[r as usize], lut[g as usize], lut[b as usize], a]
            };
        }
    } else {
        for (&[r, g, b, a], dest) in pixels.iter().zip(output) {
            *dest = [lut[r as usize], lut[g as usize], lut[b as usize], a];
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn samples_clip_and_keep_endpoints() {
        let inputs = [f64::NEG_INFINITY, -0.0, 0.5, f64::INFINITY, f64::NAN];
        for points in [vec![], vec![(0.5, 0.25)]] {
            let middle = points.first().map_or(0.5, |p| p.1);
            let samples = tone_curve_samples(points, &inputs);
            assert_eq!(&samples[..4], &[0.0, 0.0, middle, 1.0]);
            assert_eq!(samples[1].to_bits(), 0);
            assert!(samples[4].is_nan());
        }
        assert_eq!(tone_curve_lut(vec![]), std::array::from_fn(|i| i as u8));
    }

    #[test]
    fn background_threshold_and_alpha() {
        let rgba = [105, 105, 105, 127, 106, 106, 102, 255];
        let mut output = [0; 8];
        let points = vec![(0.5, 0.0)];
        let lut = tone_curve_lut(points.clone());
        apply_tone_curve(&rgba, 2, 1, points, Some([100; 3]), &mut output).unwrap();
        assert_eq!(
            output,
            [100, 100, 100, 127, lut[106], lut[106], lut[102], 255]
        );
        assert!(apply_tone_curve(&rgba, 1, 1, vec![], None, &mut output).is_err());
    }
}
