//! 隣接色分け（表示プレビュー）の重い部分。
//!
//! Python の `view_preview` と同じ結果を返す。
//!
//! * [`map_known_colors`] は、元の色から仮の色への対応が全部そろっているときに、
//!   画素をその色で塗り直す。描いている最中はこの経路しか通らないので、
//!   numpy のように色を集計（`np.unique`）せず、1 回の走査で済ませる。
//! * [`region_neighbors`] は、`gap` 画素以内で向かい合う領域の組を集める。
//!   numpy 版は向きと距離の組み合わせごとに画像全体を比べていた（既定で16回）。

use std::collections::HashSet;

use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// 擬似透明として扱う白。領域に数えない。
const TRANSPARENT_CODE: u32 = 0xff_ffff;

/// 1 画素の RGB をまとめた値。
#[inline]
fn code_of(pixel: &[u8]) -> u32 {
    (pixel[0] as u32) << 16 | (pixel[1] as u32) << 8 | pixel[2] as u32
}

/// 仮の色を塗る対象か。不透明で、擬似透明でも主線（黒に近い色）でもない画素。
#[inline]
fn is_surface(pixel: &[u8], line_max: u8) -> bool {
    if pixel[3] == 0 {
        return false;
    }
    if pixel[0] <= line_max && pixel[1] <= line_max && pixel[2] <= line_max {
        return false;
    }
    code_of(pixel) != TRANSPARENT_CODE
}

/// 元の色から仮の色の番号への対応表。元の色の昇順に並ぶ。
pub struct ColorMapping<'a> {
    pub codes: &'a [u32],
    pub indices: &'a [u8],
}

impl ColorMapping<'_> {
    /// `code` に割り当てられた仮の色の番号。対応がなければ `None`。
    #[inline]
    fn index_of(&self, code: u32) -> Option<u8> {
        // 色数は数百までなので、二分探索が表を作るより軽い。
        self.codes.binary_search(&code).ok().map(|slot| self.indices[slot])
    }
}

/// 対応表どおりに `rgba` を塗り直す。対応のない色があれば偽を返す。
///
/// 対応がそろっているかどうかを塗る前に確かめるので、偽のときは画素が
/// 1 つも変わっていない（呼び出し側が領域から割り当て直す）。
pub fn map_known_colors(
    rgba: &mut [u8],
    mapping: &ColorMapping,
    palette: &[[u8; 3]],
    line_max: u8,
) -> bool {
    // 先に全部の色を引き当てておく。途中で足りないと分かったとき、
    // 塗りかけの絵を残さないため。
    let mut resolved: Vec<u8> = Vec::with_capacity(rgba.len() / 4);
    for pixel in rgba.chunks_exact(4) {
        if !is_surface(pixel, line_max) {
            // 塗らない画素の目印。番号としては使わない。
            resolved.push(u8::MAX);
            continue;
        }
        match mapping.index_of(code_of(pixel)) {
            Some(index) if (index as usize) < palette.len() => resolved.push(index),
            _ => return false,
        }
    }
    for (pixel, index) in rgba.chunks_exact_mut(4).zip(resolved) {
        if index == u8::MAX {
            continue;
        }
        pixel[..3].copy_from_slice(&palette[index as usize]);
    }
    true
}

/// `gap` 画素以内で向かい合う領域の組 `(小さい番号, 大きい番号)`。
///
/// 番号の小さい順、同じならもう一方の小さい順に並べて返す（numpy 版と同じ）。
pub fn region_neighbors(
    labels: &[i32],
    width: usize,
    height: usize,
    gap: usize,
) -> Vec<(i64, i64)> {
    let mut pairs: HashSet<(i32, i32)> = HashSet::new();
    let gap = gap.max(1);
    for distance in 1..=gap {
        // numpy 版と同じ4つの向き。残りの向きは組にすると同じものになる。
        for (dy, dx) in [(0i64, distance as i64), (distance as i64, 0),
                         (distance as i64, distance as i64), (distance as i64, -(distance as i64))] {
            if dy as usize >= height || dx.unsigned_abs() as usize >= width {
                continue;
            }
            for y in 0..height - dy as usize {
                let other_y = y + dy as usize;
                // ずらした先が画像に収まる範囲だけを見る。
                let (from_x, to_x) = if dx >= 0 {
                    (0, width - dx as usize)
                } else {
                    (dx.unsigned_abs() as usize, width)
                };
                for x in from_x..to_x {
                    let here = labels[y * width + x];
                    if here <= 0 {
                        continue;
                    }
                    let there = labels[other_y * width + (x as i64 + dx) as usize];
                    if there <= 0 || there == here {
                        continue;
                    }
                    pairs.insert((here.min(there), here.max(there)));
                }
            }
        }
    }
    let mut found: Vec<(i64, i64)> =
        pairs.into_iter().map(|(low, high)| (low as i64, high as i64)).collect();
    found.sort_unstable();
    found
}

fn invalid(message: &str) -> PyErr {
    pyo3::exceptions::PyValueError::new_err(message.to_string())
}

/// 配布版は abi3（限定API）で作るので、numpy の配列をその場で書き換える
/// バッファ protocol は使えない。画素はバイト列で受け取って返す。
#[pyfunction(name = "map_known_colors")]
pub fn py_map_known_colors<'py>(
    py: Python<'py>,
    rgba: &[u8],
    codes: Vec<u32>,
    indices: Vec<u8>,
    palette: Vec<(u8, u8, u8)>,
    line_max: u8,
) -> PyResult<Option<Bound<'py, PyBytes>>> {
    if !rgba.len().is_multiple_of(4) {
        return Err(invalid("expected packed rgba pixels"));
    }
    if codes.len() != indices.len() {
        return Err(invalid("the color mapping needs one index per color"));
    }
    if codes.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(invalid("the color mapping must be sorted and distinct"));
    }
    if palette.is_empty() {
        return Err(invalid("the palette must not be empty"));
    }
    let palette: Vec<[u8; 3]> = palette.iter().map(|(r, g, b)| [*r, *g, *b]).collect();
    let mapping = ColorMapping { codes: &codes, indices: &indices };
    let mut pixels = rgba.to_vec();
    let known =
        py.detach(|| map_known_colors(&mut pixels, &mapping, &palette, line_max));
    Ok(if known { Some(PyBytes::new(py, &pixels)) } else { None })
}

#[pyfunction(name = "region_neighbors")]
pub fn py_region_neighbors(
    py: Python<'_>,
    labels: &[u8],
    width: usize,
    height: usize,
    gap: usize,
) -> PyResult<Vec<(i64, i64)>> {
    if width.checked_mul(height).map(|total| total * 4) != Some(labels.len()) {
        return Err(invalid("label size does not match width x height"));
    }
    if gap > 64 {
        return Err(invalid("the neighbour gap is too large"));
    }
    let values: Vec<i32> = labels
        .chunks_exact(4)
        .map(|chunk| i32::from_ne_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]))
        .collect();
    Ok(py.detach(|| region_neighbors(&values, width, height, gap)))
}

#[cfg(test)]
mod tests {
    use super::*;

    const RED: [u8; 3] = [230, 25, 75];
    const GREEN: [u8; 3] = [60, 180, 75];

    fn rgba(pixels: &[[u8; 4]]) -> Vec<u8> {
        pixels.iter().flatten().copied().collect()
    }

    #[test]
    fn known_colors_are_painted_from_the_palette() {
        let mut pixels = rgba(&[[200, 100, 100, 255], [100, 200, 100, 255]]);
        let mapping = ColorMapping {
            // 元の色の昇順に並べた対応表。
            codes: &[0x64c864, 0xc86464],
            indices: &[1, 0],
        };
        assert!(map_known_colors(&mut pixels, &mapping, &[RED, GREEN], 48));
        assert_eq!(&pixels[0..3], &RED);
        assert_eq!(&pixels[4..7], &GREEN);
        // アルファはそのまま。
        assert_eq!((pixels[3], pixels[7]), (255, 255));
    }

    #[test]
    fn lines_transparency_and_holes_keep_their_color() {
        let pixels = rgba(&[
            [10, 10, 10, 255],       // 主線
            [255, 255, 255, 255],    // 擬似透明の白
            [200, 100, 100, 0],      // 透明
        ]);
        let mapping = ColorMapping { codes: &[0xc86464], indices: &[0] };
        let mut painted = pixels.clone();
        assert!(map_known_colors(&mut painted, &mapping, &[RED], 48));
        assert_eq!(painted, pixels);
    }

    #[test]
    fn an_unknown_color_leaves_every_pixel_alone() {
        let mut pixels = rgba(&[[200, 100, 100, 255], [1, 2, 3, 255]]);
        let before = pixels.clone();
        let mapping = ColorMapping { codes: &[0x010203], indices: &[0] };
        // 0xc86464 の対応がないので、割り当て直しに回す。
        assert!(!map_known_colors(&mut pixels, &mapping, &[RED], 48));
        // 塗りかけの絵は残さない。
        assert_eq!(pixels, before);
    }

    #[test]
    fn touching_regions_are_reported_once_in_order() {
        // 3x1。1 と 2 が隣り合う。
        let labels = vec![1i32, 2, 2];
        assert_eq!(region_neighbors(&labels, 3, 1, 1), vec![(1, 2)]);
    }

    #[test]
    fn regions_across_a_line_meet_within_the_gap() {
        // 1 . . 2（間は番号 0 の主線）。gap 1 では届かず、3 で届く。
        let labels = vec![1i32, 0, 0, 2];
        assert!(region_neighbors(&labels, 4, 1, 1).is_empty());
        assert_eq!(region_neighbors(&labels, 4, 1, 3), vec![(1, 2)]);
    }

    #[test]
    fn diagonal_neighbours_count_too() {
        // 2x2 の対角。斜めの向きで見つかる。
        let labels = vec![1i32, 0, 0, 2];
        assert_eq!(region_neighbors(&labels, 2, 2, 1), vec![(1, 2)]);
    }

    #[test]
    fn every_pair_is_sorted_low_to_high() {
        // 3 と 1 が隣り合う並び。組は (1, 3) に正される。
        let labels = vec![3i32, 1];
        assert_eq!(region_neighbors(&labels, 2, 1, 1), vec![(1, 3)]);
    }
}
