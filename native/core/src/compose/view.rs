//! 画面の見え方だけを変える表示プレビュー（露光量・チャンネル・隣接色分け）。
//!
//! Python の `view_preview.py` と同じ結果を返す。画素はストレートの RGBA8888。

use std::collections::BTreeMap;

use crate::map_colors::{map_known_colors, region_neighbors, ColorMapping};
use crate::regions::{label_regions, INVALID_CODE};

/// 隣接色分けで、この距離（px）以内にある別の領域は隣り合うとみなす。
pub const MAP_NEIGHBOR_GAP: usize = 4;
/// 隣接色分けで元の色のまま残す「主線」とみなす明るさの上限（各チャンネル）。
pub const MAP_LINE_MAX: u8 = 48;
/// 隣接色分けの既定の仮の色（`view_preview.MAP_PALETTE`）。
pub const MAP_PALETTE: [[u8; 3]; 12] = [
    [230, 25, 75],
    [60, 180, 75],
    [255, 225, 25],
    [0, 130, 200],
    [245, 130, 48],
    [145, 30, 180],
    [70, 240, 240],
    [240, 50, 230],
    [210, 245, 60],
    [250, 190, 212],
    [0, 128, 128],
    [170, 110, 40],
];
/// 露光量（段）の範囲。
pub const EXPOSURE_RANGE: (f64, f64) = (-10.0, 10.0);

/// どのチャンネルを見せるか（`view_preview.CHANNEL_*`）。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Channel {
    /// そのまま。
    #[default]
    Rgb,
    /// 赤をグレーで。
    Red,
    /// 緑をグレーで。
    Green,
    /// 青をグレーで。
    Blue,
    /// 不透明度を白の濃さで。
    Alpha,
}

/// 表示プレビューの設定（`view_preview.ViewPreview`）。既定値はプレビューなし。
#[derive(Clone, Debug, PartialEq)]
pub struct ViewPreview {
    /// 露光量（段）。0 で変えない。
    pub exposure: f64,
    /// どのチャンネルを見せるか。
    pub channel: Channel,
    /// 隣接色分け（アクティブレイヤーだけに掛ける）。
    pub map_colors: bool,
    /// 隣接色分けの仮の色。空なら既定の色。
    pub palette: Vec<[u8; 3]>,
}

impl Default for ViewPreview {
    fn default() -> Self {
        Self {
            exposure: 0.0,
            channel: Channel::Rgb,
            map_colors: false,
            palette: MAP_PALETTE.to_vec(),
        }
    }
}

impl ViewPreview {
    /// 何かのプレビューが有効か。
    pub fn active(&self) -> bool {
        self.adjusts_pixels() || self.map_colors
    }

    /// 露光量かチャンネルで、すべてのレイヤーの色を変えるか。
    pub fn adjusts_pixels(&self) -> bool {
        self.exposure.abs() > 1e-9 || self.channel != Channel::Rgb
    }

    fn palette(&self) -> &[[u8; 3]] {
        if self.palette.is_empty() {
            &MAP_PALETTE
        } else {
            &self.palette
        }
    }
}

/// 0〜255 の値を `2 ** stops` 倍した対応表（`view_preview.exposure_lut`）。
pub fn exposure_lut(stops: f64) -> [u8; 256] {
    let scale = 2.0f64.powf(stops.clamp(EXPOSURE_RANGE.0, EXPOSURE_RANGE.1));
    let mut table = [0u8; 256];
    for (value, slot) in table.iter_mut().enumerate() {
        // numpy の rint は偶数丸め。
        *slot = (value as f64 * scale).round_ties_even().clamp(0.0, 255.0) as u8;
    }
    table
}

/// ストレートの RGBA に露光量とチャンネルを掛ける（`view_preview.adjust_pixels`）。
pub fn adjust_pixels(rgba: &mut [u8], preview: &ViewPreview) {
    let lut = (preview.exposure.abs() > 1e-9).then(|| exposure_lut(preview.exposure));
    for pixel in rgba.chunks_exact_mut(4) {
        if let Some(lut) = &lut {
            for channel in &mut pixel[..3] {
                *channel = lut[*channel as usize];
            }
        }
        let gray = match preview.channel {
            Channel::Rgb => continue,
            Channel::Red => pixel[0],
            Channel::Green => pixel[1],
            Channel::Blue => pixel[2],
            Channel::Alpha => 255,
        };
        pixel[..3].fill(gray);
    }
}

/// キャンバスの背景（擬似透明の表示色）に同じプレビューを掛けた色
/// （`view_preview.background_color`）。
pub fn background_color(rgb: [u8; 3], preview: &ViewPreview) -> [u8; 3] {
    if preview.channel == Channel::Alpha {
        return [0, 0, 0];
    }
    let mut pixel = [rgb[0], rgb[1], rgb[2], 255];
    adjust_pixels(&mut pixel, preview);
    [pixel[0], pixel[1], pixel[2]]
}

/// 隣接色分けで一度割り当てた、元の RGB（0xRRGGBB）から仮の色の番号への対応。
///
/// Python のキャンバスは仮の色の並びごとに1つ持ち、コマやレイヤーをまたいで使い回す
/// （`PaintCanvas._view_map_color_mappings`）。呼び出し側が持ち続けて毎回渡す。
pub type MapColorMapping = BTreeMap<u32, usize>;

fn code_of(pixel: &[u8]) -> u32 {
    (pixel[0] as u32) << 16 | (pixel[1] as u32) << 8 | pixel[2] as u32
}

/// 隣り合う領域が違う色になるよう、ストレートの RGBA を仮の色で塗り分ける
/// （`view_preview.map_color_pixels` に `color_mapping` を渡したときと同じ）。
pub fn map_color_pixels(
    rgba: &mut [u8],
    width: usize,
    height: usize,
    palette: &[[u8; 3]],
    mapping: &mut MapColorMapping,
) {
    let palette = if palette.is_empty() {
        &MAP_PALETTE[..]
    } else {
        palette
    };
    let valid: Vec<bool> = rgba
        .chunks_exact(4)
        .map(|pixel| {
            let line = pixel[..3].iter().all(|&value| value <= MAP_LINE_MAX);
            pixel[3] > 0 && code_of(pixel) != 0xff_ffff && !line
        })
        .collect();
    if !valid.iter().any(|&value| value) {
        return;
    }
    // 割り当て済みの色だけなら、領域を数えずに塗る。
    let codes: Vec<u32> = mapping.keys().copied().collect();
    let indices: Vec<u8> = mapping
        .values()
        .map(|&index| index.min(255) as u8)
        .collect();
    let known = ColorMapping {
        codes: &codes,
        indices: &indices,
    };
    if mapping.values().all(|&index| index < 256)
        && map_known_colors(rgba, &known, palette, MAP_LINE_MAX)
    {
        return;
    }

    let marked: Vec<u32> = rgba
        .chunks_exact(4)
        .zip(&valid)
        .map(|(pixel, &valid)| if valid { code_of(pixel) } else { INVALID_CODE })
        .collect();
    let (labels, count) = label_regions(&marked, width, height);
    let count = count as usize;
    let mut areas = vec![0i64; count + 1];
    let mut region_codes = vec![0u32; count + 1];
    for (index, &label) in labels.iter().enumerate() {
        areas[label as usize] += 1;
        if label != 0 {
            region_codes[label as usize] = marked[index];
        }
    }
    let signed: Vec<i32> = labels.iter().map(|&label| label as i32).collect();
    let neighbors = region_neighbors(&signed, width, height, MAP_NEIGHBOR_GAP);
    // 元の色が同じ領域は同じ組（元の色の昇順に 1 から）。
    let mut unique: Vec<u32> = region_codes[1..].to_vec();
    unique.sort_unstable();
    unique.dedup();
    let group_count = unique.len();
    let groups: Vec<usize> = region_codes
        .iter()
        .enumerate()
        .map(|(label, code)| {
            if label == 0 {
                0
            } else {
                unique.binary_search(code).expect("領域の色は一覧にある") + 1
            }
        })
        .collect();
    let mut adjacency = vec![std::collections::BTreeSet::new(); group_count + 1];
    for (a, b) in neighbors {
        let (ga, gb) = (groups[a as usize], groups[b as usize]);
        if ga != gb {
            adjacency[ga].insert(gb);
            adjacency[gb].insert(ga);
        }
    }
    let mut group_colors: Vec<Option<usize>> = vec![None; group_count + 1];
    for (group, code) in unique.iter().enumerate() {
        group_colors[group + 1] = mapping.get(code).copied();
    }
    let mut group_areas = vec![0i64; group_count + 1];
    for label in 1..=count {
        group_areas[groups[label]] += areas[label];
    }
    // 面積の大きい順（同じなら組の番号順）に、未割り当ての組へ色を決める。
    let mut order: Vec<usize> = (1..=group_count).collect();
    order.sort_by_key(|&group| std::cmp::Reverse(group_areas[group]));
    for group in order {
        if group_colors[group].is_some() {
            continue;
        }
        let mut used = vec![0i64; palette.len()];
        for &other in &adjacency[group] {
            if let Some(color) = group_colors[other] {
                if color < palette.len() {
                    used[color] += 1;
                }
            }
        }
        // 同じ隣接条件なら、過去に使った色の少ない番号を優先する。
        let mut usage = vec![0i64; palette.len()];
        for &value in mapping.values() {
            if value < usage.len() {
                usage[value] += 1;
            }
        }
        let chosen = (0..palette.len())
            .min_by_key(|&index| (used[index], usage[index], index))
            .unwrap_or(0);
        group_colors[group] = Some(chosen);
        mapping.insert(unique[group - 1], chosen);
    }
    for ((pixel, &label), &valid) in rgba.chunks_exact_mut(4).zip(&labels).zip(&valid) {
        if valid {
            let color = group_colors[groups[label as usize]].unwrap_or(0);
            if let Some(rgb) = palette.get(color) {
                pixel[..3].copy_from_slice(rgb);
            }
        }
    }
}

/// レイヤーの表示用の複製に掛ける表示プレビュー（`PaintCanvas.view_layer_image` の
/// 画素の変換）。`map_colors` はそのレイヤーに隣接色分けを掛けるか。
pub fn apply_view_preview(
    rgba: &mut [u8],
    width: usize,
    height: usize,
    preview: &ViewPreview,
    map_colors: bool,
    mapping: &mut MapColorMapping,
) {
    if map_colors {
        map_color_pixels(rgba, width, height, preview.palette(), mapping);
    }
    adjust_pixels(rgba, preview);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exposure_doubles_and_clips() {
        let lut = exposure_lut(1.0);
        assert_eq!(
            (lut[0], lut[10], lut[127], lut[128], lut[255]),
            (0, 20, 254, 255, 255)
        );
        // 0.5 は偶数へ丸める（numpy の rint）。
        let lut = exposure_lut(-1.0);
        assert_eq!((lut[1], lut[3], lut[5]), (0, 2, 2));
    }

    #[test]
    fn channels_show_one_channel_as_gray() {
        let mut pixel = [10, 20, 30, 40];
        let preview = ViewPreview {
            channel: Channel::Green,
            ..ViewPreview::default()
        };
        adjust_pixels(&mut pixel, &preview);
        assert_eq!(pixel, [20, 20, 20, 40]);
        let preview = ViewPreview {
            channel: Channel::Alpha,
            ..ViewPreview::default()
        };
        adjust_pixels(&mut pixel, &preview);
        assert_eq!(pixel, [255, 255, 255, 40]);
        assert_eq!(background_color([255, 255, 255], &preview), [0, 0, 0]);
    }

    #[test]
    fn map_colors_separate_neighbours_and_remember_them() {
        // 2色の面が隣り合う。違う仮の色になり、対応表に残る。
        let mut rgba = Vec::new();
        for x in 0..8 {
            let color = if x < 4 {
                [200, 100, 100, 255]
            } else {
                [201, 100, 100, 255]
            };
            rgba.extend_from_slice(&color);
        }
        let mut mapping = MapColorMapping::new();
        let mut first = rgba.clone();
        map_color_pixels(&mut first, 8, 1, &MAP_PALETTE, &mut mapping);
        assert_ne!(first[0..3], first[28..31]);
        assert_eq!(mapping.len(), 2);
        let mut again = rgba.clone();
        map_color_pixels(&mut again, 8, 1, &MAP_PALETTE, &mut mapping);
        assert_eq!(again, first);
    }
}
