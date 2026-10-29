//! 画素をパレットのどの色に割り当てるかを決める（色削減の下ごしらえ）。
//!
//! Python の `color_ops._surface_guided_palette_labels` と同じ結果を返す。
//! まず色相を近さに写した特徴量で最近傍の色を選び、平坦な色面だけを確定させる。
//! 残った混合境界は、隣の確定した色のうち近いものを繰り返し広げて埋める。
//! そうすると2色の混合帯が中間で二分され、境界に中間色が残らない。
//!
//! 特徴量の計算は numpy 版と同じ順序・同じ単精度で行う。

use std::thread;

use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// まだどの色にも決まっていない画素。
const UNRESOLVED: i16 = -1;

/// 混合境界を埋めるために隣から色を広げる回数（numpy 版と同じ）。
const REFINEMENT_PASSES: usize = 12;

/// 色面の内部とみなす、隣との差（0-255）の上限。
const FLAT_VARIATION: i16 = 12;

/// 色面の内部とみなす、最近傍の色との隔たりの上限。
const FLAT_DISTANCE: f32 = 0.12;

/// 隣を見ずに確定させてよい、ほぼ一致とみなす隔たりの上限。
const EXACT_DISTANCE: f32 = 0.015;

/// 色相を近い向きのベクトルへ写した特徴量。距離の二乗で色の近さを測る。
pub type Feature = [f32; 4];

/// `rgb` 1 画素の特徴量。`colors.rgb_hsv_features` と同じ式。
pub fn feature(rgb: [u8; 3]) -> Feature {
    let values = [rgb[0] as f32 / 255.0, rgb[1] as f32 / 255.0, rgb[2] as f32 / 255.0];
    let maximum = values[0].max(values[1]).max(values[2]);
    let minimum = values[0].min(values[1]).min(values[2]);
    let delta = maximum - minimum;
    let saturation = delta / maximum.max(1e-6);
    // 無彩色は色相を持たないので 0 のまま（彩度も 0 なので向きは効かない）。
    let hue = if delta <= 1e-6 {
        0.0f32
    } else if values[0] >= values[1] && values[0] >= values[2] {
        ((values[1] - values[2]) / delta).rem_euclid(6.0)
    } else if values[1] >= values[2] {
        (values[2] - values[0]) / delta + 2.0
    } else {
        (values[0] - values[1]) / delta + 4.0
    };
    let hue = (hue / 6.0).rem_euclid(1.0);
    let angle = hue * (2.0 * std::f32::consts::PI);
    let strength = saturation.powf(0.72);
    [
        angle.cos() * strength * 2.35,
        angle.sin() * strength * 2.35,
        saturation * 0.45,
        maximum * 0.52,
    ]
}

#[inline]
fn distance(a: &Feature, b: &Feature) -> f32 {
    let mut total = 0.0f32;
    for index in 0..4 {
        let difference = a[index] - b[index];
        total += difference * difference;
    }
    total
}

/// 上下左右との差（チャンネルごとの差の最大）。境目の画素だけ大きくなる。
fn local_variation(rgb: &[u8], width: usize, height: usize) -> Vec<i16> {
    let mut variation = vec![0i16; width * height];
    let channel_gap = |a: usize, b: usize| {
        (0..3)
            .map(|c| (rgb[a * 3 + c] as i16 - rgb[b * 3 + c] as i16).abs())
            .max()
            .unwrap_or(0)
    };
    for y in 0..height {
        for x in 0..width {
            let index = y * width + x;
            // 下と右だけ見れば、相手側にも同じ差を広げられる。
            if y + 1 < height {
                let gap = channel_gap(index, index + width);
                variation[index] = variation[index].max(gap);
                variation[index + width] = variation[index + width].max(gap);
            }
            if x + 1 < width {
                let gap = channel_gap(index, index + 1);
                variation[index] = variation[index].max(gap);
                variation[index + 1] = variation[index + 1].max(gap);
            }
        }
    }
    variation
}

/// 画素ごとに、特徴量がもっとも近いパレットの色とその隔たりを求める。
///
/// `visible` が偽の画素は [`UNRESOLVED`] と [`f32::INFINITY`] のまま残る。
fn nearest_colors(
    features: &[Feature],
    visible: &[u8],
    palette: &[Feature],
    threads: usize,
) -> (Vec<i16>, Vec<f32>) {
    let total = features.len();
    let mut labels = vec![UNRESOLVED; total];
    let mut distances = vec![f32::INFINITY; total];
    let chunk = total.div_ceil(threads.max(1)).max(1);
    thread::scope(|scope| {
        let mut label_rest: &mut [i16] = &mut labels;
        let mut distance_rest: &mut [f32] = &mut distances;
        let mut start = 0;
        while start < total {
            let rows = chunk.min(total - start);
            let (label_part, next_labels) = label_rest.split_at_mut(rows);
            let (distance_part, next_distances) = distance_rest.split_at_mut(rows);
            label_rest = next_labels;
            distance_rest = next_distances;
            let offset = start;
            scope.spawn(move || {
                for (index, (label, found)) in
                    label_part.iter_mut().zip(distance_part.iter_mut()).enumerate()
                {
                    if visible[offset + index] == 0 {
                        continue;
                    }
                    let pixel = &features[offset + index];
                    // 同じ隔たりなら先に出てくる色が勝つ（numpy の argmin と同じ）。
                    for (candidate, color) in palette.iter().enumerate() {
                        let here = distance(pixel, color);
                        if here < *found {
                            *found = here;
                            *label = candidate as i16;
                        }
                    }
                }
            });
            start += rows;
        }
    });
    (labels, distances)
}

/// 未確定の画素を、隣の確定した色のうち近いもので埋める。
///
/// `labels` を 1 回分だけ進め、1 つでも埋まったら真を返す。隣は上・下・左・右の
/// 順に見て、同じ隔たりなら先に見たものが勝つ（numpy 版と同じ）。
fn spread_once(
    labels: &mut [i16],
    previous: &[i16],
    features: &[Feature],
    visible: &[u8],
    palette: &[Feature],
    width: usize,
    height: usize,
) -> bool {
    let mut assigned = false;
    for y in 0..height {
        for x in 0..width {
            let index = y * width + x;
            if visible[index] == 0 || previous[index] >= 0 {
                continue;
            }
            let neighbours = [
                if y > 0 { previous[index - width] } else { UNRESOLVED },
                if y + 1 < height { previous[index + width] } else { UNRESOLVED },
                if x > 0 { previous[index - 1] } else { UNRESOLVED },
                if x + 1 < width { previous[index + 1] } else { UNRESOLVED },
            ];
            let mut best = UNRESOLVED;
            let mut best_distance = f32::INFINITY;
            for candidate in neighbours {
                if candidate < 0 {
                    continue;
                }
                let here = distance(&features[index], &palette[candidate as usize]);
                if here < best_distance {
                    best_distance = here;
                    best = candidate;
                }
            }
            if best >= 0 {
                labels[index] = best;
                assigned = true;
            }
        }
    }
    assigned
}

/// 色面を種にして、画素ごとのパレットの番号（未確定は -1）を決める。
pub fn surface_guided_labels(
    rgb: &[u8],
    width: usize,
    height: usize,
    visible: &[u8],
    palette: &[u8],
    spatial_refinement: bool,
    threads: usize,
) -> Vec<i16> {
    let total = width * height;
    let palette_features: Vec<Feature> =
        palette.chunks_exact(3).map(|color| feature([color[0], color[1], color[2]])).collect();
    let features: Vec<Feature> =
        rgb.chunks_exact(3).map(|pixel| feature([pixel[0], pixel[1], pixel[2]])).collect();
    let (nearest, nearest_distance) =
        nearest_colors(&features, visible, &palette_features, threads);
    if !spatial_refinement || palette_features.is_empty() {
        return nearest;
    }

    // 平坦で、しかも近い色がはっきりしている画素だけを先に確定させる。
    let variation = local_variation(rgb, width, height);
    let mut labels = vec![UNRESOLVED; total];
    for index in 0..total {
        let flat = variation[index] <= FLAT_VARIATION && nearest_distance[index] <= FLAT_DISTANCE;
        // ほぼ一致する色なら、境目でも最初から確定させる。
        if visible[index] != 0 && (flat || nearest_distance[index] <= EXACT_DISTANCE) {
            labels[index] = nearest[index];
        }
    }

    for _pass in 0..REFINEMENT_PASSES {
        // 同じ 1 回の中では、埋める前の色だけを見る。
        let previous = labels.clone();
        if !previous.iter().enumerate().any(|(index, label)| *label < 0 && visible[index] != 0) {
            break;
        }
        if !spread_once(
            &mut labels,
            &previous,
            &features,
            visible,
            &palette_features,
            width,
            height,
        ) {
            break;
        }
    }

    // どの隣からも届かなかった画素は、最近傍の色に戻す。
    for index in 0..total {
        if visible[index] != 0 && labels[index] < 0 {
            labels[index] = nearest[index];
        }
    }
    labels
}

#[pyfunction(name = "surface_guided_palette_labels")]
pub fn py_surface_guided_palette_labels<'py>(
    py: Python<'py>,
    rgb: &[u8],
    width: usize,
    height: usize,
    visible: &[u8],
    palette: &[u8],
    spatial_refinement: bool,
) -> PyResult<Bound<'py, PyBytes>> {
    let invalid = |message: &str| pyo3::exceptions::PyValueError::new_err(message.to_string());
    let total = width.checked_mul(height).ok_or_else(|| invalid("image is too large"))?;
    if total.checked_mul(3) != Some(rgb.len()) {
        return Err(invalid("rgb size does not match width x height"));
    }
    if visible.len() != total {
        return Err(invalid("visible size does not match width x height"));
    }
    if !palette.len().is_multiple_of(3) {
        return Err(invalid("expected packed rgb palette colors"));
    }
    if palette.len() / 3 > i16::MAX as usize {
        return Err(invalid("palette has too many colors"));
    }
    let threads = thread::available_parallelism().map(|value| value.get()).unwrap_or(1);
    let labels = py.detach(|| {
        surface_guided_labels(rgb, width, height, visible, palette, spatial_refinement, threads)
    });
    let bytes: Vec<u8> = labels.iter().flat_map(|value| value.to_ne_bytes()).collect();
    Ok(PyBytes::new(py, &bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 赤・緑・青。色相が 120 度ずつ離れる。
    fn primaries() -> Vec<u8> {
        vec![255, 0, 0, 0, 255, 0, 0, 0, 255]
    }

    #[test]
    fn grey_has_no_hue_but_keeps_its_brightness() {
        let dark = feature([32, 32, 32]);
        let light = feature([200, 200, 200]);
        // 彩度 0 なので色相の 2 軸は消え、明るさだけが残る。
        assert_eq!((dark[0], dark[1], dark[2]), (0.0, 0.0, 0.0));
        assert!(light[3] > dark[3]);
    }

    #[test]
    fn every_hue_lands_within_the_unit_circle() {
        for color in [[255, 0, 0], [255, 255, 0], [0, 255, 0], [0, 255, 255], [0, 0, 255]] {
            let found = feature(color);
            let radius = (found[0] * found[0] + found[1] * found[1]).sqrt();
            assert!((radius - 2.35).abs() < 1e-5, "{color:?} -> {radius}");
        }
    }

    #[test]
    fn exact_colors_take_their_own_palette_entry() {
        let rgb = primaries();
        let labels =
            surface_guided_labels(&rgb, 3, 1, &[1, 1, 1], &primaries(), true, 1);
        assert_eq!(labels, vec![0, 1, 2]);
    }

    #[test]
    fn hidden_pixels_stay_unresolved() {
        let rgb = primaries();
        let labels =
            surface_guided_labels(&rgb, 3, 1, &[1, 0, 1], &primaries(), true, 1);
        assert_eq!(labels, vec![0, UNRESOLVED, 2]);
    }

    #[test]
    fn a_mixed_edge_splits_between_its_two_surfaces() {
        // 赤と青の色面の間に、両方を混ぜた帯を 2 画素置く。
        let mut rgb = Vec::new();
        for value in [
            [255, 0, 0], [255, 0, 0], [170, 0, 85], [85, 0, 170], [0, 0, 255], [0, 0, 255],
        ] {
            rgb.extend_from_slice(&value);
        }
        let labels =
            surface_guided_labels(&rgb, 6, 1, &[1; 6], &primaries(), true, 1);
        // 混合帯は中間で二分され、中間色のままの画素は残らない。
        assert_eq!(labels, vec![0, 0, 0, 2, 2, 2]);
    }

    #[test]
    fn without_refinement_every_pixel_takes_its_nearest_color() {
        let mut rgb = Vec::new();
        for value in [[255, 0, 0], [170, 0, 85], [85, 0, 170], [0, 0, 255]] {
            rgb.extend_from_slice(&value);
        }
        let labels =
            surface_guided_labels(&rgb, 4, 1, &[1; 4], &primaries(), false, 1);
        assert_eq!(labels, vec![0, 0, 2, 2]);
    }

    #[test]
    fn threads_do_not_change_the_result() {
        // 色相を少しずつ変えた帯。最近傍の判定が画素ごとに分かれる。
        let mut rgb = Vec::new();
        for index in 0..256u16 {
            rgb.extend_from_slice(&[(index % 256) as u8, (index * 3 % 256) as u8, 40]);
        }
        let visible = vec![1u8; 256];
        let single = surface_guided_labels(&rgb, 16, 16, &visible, &primaries(), true, 1);
        let many = surface_guided_labels(&rgb, 16, 16, &visible, &primaries(), true, 7);
        assert_eq!(single, many);
    }
}
