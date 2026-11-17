//! 画素をパレットのどの色に割り当てるかを決める（色削減の下ごしらえ）。
//!
//! Python の `color_ops._surface_guided_palette_labels` と同じ結果を返す。
//! まず色相を近さに写した特徴量で最近傍の色を選び、平坦な色面だけを確定させる。
//! 残った混合境界は、隣の確定した色のうち近いものを繰り返し広げて埋める。
//! そうすると2色の混合帯が中間で二分され、境界に中間色が残らない。
//!
//! 特徴量の計算は numpy 版と同じ順序・同じ単精度で行う。

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

/// `colors.rgb_hsv_features` と同じ、色相を近い向きのベクトルへ写した特徴量。
/// 距離の二乗で色の近さを測る。
pub type Feature = [f32; 4];

/// `rgb` 1 画素の特徴量。`colors.rgb_hsv_features` と同じ式。
pub fn feature(rgb: [u8; 3]) -> Feature {
    let values = [
        rgb[0] as f32 / 255.0,
        rgb[1] as f32 / 255.0,
        rgb[2] as f32 / 255.0,
    ];
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

/// 連続した画素範囲の最近傍番号と距離を出力先へ書き込む（戻り値なし）。
/// `color_ops._surface_guided_palette_labels_numpy` の最近傍判定と一致する。
/// 各画素スライスは同じ範囲と長さで、`labels` は -1、`distances` は無限大で初期化する。
/// 非表示画素は変更せず、同距離では先のパレット色を選ぶ。
pub fn nearest_colors_into(
    features: &[Feature],
    visible: &[u8],
    palette: &[Feature],
    labels: &mut [i16],
    distances: &mut [f32],
) {
    for (index, (label, found)) in labels.iter_mut().zip(distances.iter_mut()).enumerate() {
        if visible[index] == 0 {
            continue;
        }
        let pixel = &features[index];
        // 同じ隔たりなら先に出てくる色が勝つ（numpy の argmin と同じ）。
        for (candidate, color) in palette.iter().enumerate() {
            let here = distance(pixel, color);
            if here < *found {
                *found = here;
                *label = candidate as i16;
            }
        }
    }
}

/// `color_ops._surface_guided_palette_labels_numpy` と同じ最近傍番号と距離を返す。
/// 非表示画素と空パレットは番号 -1、距離は無限大になる。
pub fn nearest_colors(
    features: &[Feature],
    visible: &[u8],
    palette: &[Feature],
) -> (Vec<i16>, Vec<f32>) {
    let mut labels = vec![UNRESOLVED; features.len()];
    let mut distances = vec![f32::INFINITY; features.len()];
    nearest_colors_into(features, visible, palette, &mut labels, &mut distances);
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
                if y > 0 {
                    previous[index - width]
                } else {
                    UNRESOLVED
                },
                if y + 1 < height {
                    previous[index + width]
                } else {
                    UNRESOLVED
                },
                if x > 0 {
                    previous[index - 1]
                } else {
                    UNRESOLVED
                },
                if x + 1 < width {
                    previous[index + 1]
                } else {
                    UNRESOLVED
                },
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

/// 色面を種にして、画素ごとのパレット番号（未確定は -1）を返す。
/// `color_ops._surface_guided_palette_labels_numpy` と同じ順序で判定する。
pub fn surface_guided_labels(
    rgb: &[u8],
    width: usize,
    height: usize,
    visible: &[u8],
    palette: &[u8],
    spatial_refinement: bool,
) -> Vec<i16> {
    let palette_features: Vec<Feature> = palette
        .chunks_exact(3)
        .map(|color| feature([color[0], color[1], color[2]]))
        .collect();
    let features: Vec<Feature> = rgb
        .chunks_exact(3)
        .map(|pixel| feature([pixel[0], pixel[1], pixel[2]]))
        .collect();
    let (nearest, nearest_distance) = nearest_colors(&features, visible, &palette_features);
    if !spatial_refinement || palette_features.is_empty() {
        return nearest;
    }

    refine_labels(
        rgb,
        width,
        height,
        visible,
        &features,
        &palette_features,
        &nearest,
        &nearest_distance,
    )
}

/// 最近傍の結果から色面を広げたパレット番号（未確定は -1）を返す。
/// `color_ops._surface_guided_palette_labels_numpy` の平坦画素の確定・境界の精緻化と一致する。
/// 各画素バッファは `width * height` 画素分、パレットは空でないことを前提とする。
#[allow(clippy::too_many_arguments)]
pub fn refine_labels(
    rgb: &[u8],
    width: usize,
    height: usize,
    visible: &[u8],
    features: &[Feature],
    palette_features: &[Feature],
    nearest: &[i16],
    nearest_distance: &[f32],
) -> Vec<i16> {
    let total = width * height;
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
        if !previous
            .iter()
            .enumerate()
            .any(|(index, label)| *label < 0 && visible[index] != 0)
        {
            break;
        }
        if !spread_once(
            &mut labels,
            &previous,
            features,
            visible,
            palette_features,
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
        for color in [
            [255, 0, 0],
            [255, 255, 0],
            [0, 255, 0],
            [0, 255, 255],
            [0, 0, 255],
        ] {
            let found = feature(color);
            let radius = (found[0] * found[0] + found[1] * found[1]).sqrt();
            assert!((radius - 2.35).abs() < 1e-5, "{color:?} -> {radius}");
        }
    }

    #[test]
    fn exact_colors_take_their_own_palette_entry() {
        let rgb = primaries();
        let labels = surface_guided_labels(&rgb, 3, 1, &[1, 1, 1], &primaries(), true);
        assert_eq!(labels, vec![0, 1, 2]);
    }

    #[test]
    fn hidden_pixels_stay_unresolved() {
        let rgb = primaries();
        let labels = surface_guided_labels(&rgb, 3, 1, &[1, 0, 1], &primaries(), true);
        assert_eq!(labels, vec![0, UNRESOLVED, 2]);
    }

    #[test]
    fn a_mixed_edge_splits_between_its_two_surfaces() {
        // 赤と青の色面の間に、両方を混ぜた帯を 2 画素置く。
        let mut rgb = Vec::new();
        for value in [
            [255, 0, 0],
            [255, 0, 0],
            [170, 0, 85],
            [85, 0, 170],
            [0, 0, 255],
            [0, 0, 255],
        ] {
            rgb.extend_from_slice(&value);
        }
        let labels = surface_guided_labels(&rgb, 6, 1, &[1; 6], &primaries(), true);
        // 混合帯は中間で二分され、中間色のままの画素は残らない。
        assert_eq!(labels, vec![0, 0, 0, 2, 2, 2]);
    }

    #[test]
    fn without_refinement_every_pixel_takes_its_nearest_color() {
        let mut rgb = Vec::new();
        for value in [[255, 0, 0], [170, 0, 85], [85, 0, 170], [0, 0, 255]] {
            rgb.extend_from_slice(&value);
        }
        let labels = surface_guided_labels(&rgb, 4, 1, &[1; 4], &primaries(), false);
        assert_eq!(labels, vec![0, 0, 2, 2]);
    }
}
