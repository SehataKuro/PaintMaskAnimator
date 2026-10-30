//! PaintMaskAnimator の重い画像計算を Rust で行う部品。
//!
//! 方針（docs/design-principles.md「将来は Rust へ置き換えていく」）に沿って、
//! 画面や Qt に触れない計算と状態管理を置く。Python 側の numpy 版と
//! 同じ結果になることを、Python のテストで突き合わせる。
//!
//! マスクは 1 画素 1 バイト（0 か 1）の行優先の並びでやり取りする。
//! numpy の bool 配列をそのまま `tobytes()` した形と同じ。

use pyo3::prelude::*;
use pyo3::types::PyBytes;

mod auto_paint;
mod clip;
mod display;
mod line_flow;
mod thin;
mod timeline;
mod mask_transform;
mod palette_labels;
mod map_colors;

/// 4 近傍で、`seeds` からつながる `passable` の領域を求める（行ごとのランで辿る）。
///
/// `passable` の外にある種・範囲外の種は無視する。
pub fn flood_fill(passable: &[u8], width: usize, height: usize, seeds: &[(i64, i64)]) -> Vec<u8> {
    let mut region = vec![0u8; width * height];
    if width == 0 || height == 0 {
        return region;
    }
    let mut stack: Vec<(usize, usize)> = Vec::new();
    for &(x, y) in seeds {
        if x < 0 || y < 0 || x as usize >= width || y as usize >= height {
            continue;
        }
        let (x, y) = (x as usize, y as usize);
        if passable[y * width + x] != 0 {
            stack.push((x, y));
        }
    }
    while let Some((x, y)) = stack.pop() {
        let row = y * width;
        if region[row + x] != 0 {
            continue;
        }
        // 左右に広げてランを求める。
        let mut left = x;
        while left > 0 && passable[row + left - 1] != 0 && region[row + left - 1] == 0 {
            left -= 1;
        }
        let mut right = x;
        while right + 1 < width && passable[row + right + 1] != 0 && region[row + right + 1] == 0 {
            right += 1;
        }
        for cell in &mut region[row + left..=row + right] {
            *cell = 1;
        }
        // 上下の行で、このランと列が重なるランの始点を積む。
        for next_y in [y.wrapping_sub(1), y + 1] {
            if next_y >= height {
                continue;
            }
            let next_pass = &passable[next_y * width + left..=next_y * width + right];
            let next_done = &region[next_y * width + left..=next_y * width + right];
            let mut cx = 0;
            let span = next_pass.len();
            while cx < span {
                // まだ塗っていない通れる画素を探し、そのランの頭だけを積む。
                match next_pass[cx..]
                    .iter()
                    .zip(&next_done[cx..])
                    .position(|(p, d)| *p != 0 && *d == 0)
                {
                    None => break,
                    Some(offset) => {
                        cx += offset;
                        stack.push((left + cx, next_y));
                        // 同じランの続きは積まない（積んだ点から左右に広がる）。
                        cx += next_pass[cx..]
                            .iter()
                            .position(|p| *p == 0)
                            .unwrap_or(span - cx);
                    }
                }
            }
        }
    }
    region
}

/// 行ごとに 64 画素ずつ詰めたビット列。x 番目の画素は `x / 64` 語目の `x % 64` ビット。
struct BitRows {
    words: Vec<u64>,
    per_row: usize,
    width: usize,
    height: usize,
}

impl BitRows {
    fn pack(mask: &[u8], width: usize, height: usize) -> Self {
        let per_row = width.div_ceil(64).max(1);
        let mut words = vec![0u64; per_row * height];
        for y in 0..height {
            let row = &mask[y * width..(y + 1) * width];
            let dst = &mut words[y * per_row..(y + 1) * per_row];
            // 分岐なしで 64 画素ずつ詰める（コンパイラがまとめて処理できる形）。
            for (word, chunk) in dst.iter_mut().zip(row.chunks(64)) {
                let mut bits = 0u64;
                for (i, value) in chunk.iter().enumerate() {
                    bits |= ((*value != 0) as u64) << i;
                }
                *word = bits;
            }
        }
        BitRows {
            words,
            per_row,
            width,
            height,
        }
    }

    fn unpack(&self) -> Vec<u8> {
        let mut out = vec![0u8; self.width * self.height];
        for y in 0..self.height {
            let src = &self.words[y * self.per_row..(y + 1) * self.per_row];
            let dst = &mut out[y * self.width..(y + 1) * self.width];
            for (word, chunk) in src.iter().zip(dst.chunks_mut(64)) {
                for (i, cell) in chunk.iter_mut().enumerate() {
                    *cell = ((word >> i) & 1) as u8;
                }
            }
        }
        out
    }

    /// 最後の語の、幅より右（範囲外）のビットを 0 にしておく。
    fn tail_mask(&self) -> u64 {
        let used = self.width - (self.per_row - 1) * 64;
        if used >= 64 {
            u64::MAX
        } else {
            (1u64 << used) - 1
        }
    }

    /// 横に半径 1 の最大（`maximum`）／最小を取る。範囲外は 0 として扱う。
    fn horizontal(&mut self, maximum: bool) {
        let tail = self.tail_mask();
        let n = self.per_row;
        for y in 0..self.height {
            let row = &mut self.words[y * n..(y + 1) * n];
            let original: Vec<u64> = row.to_vec();
            for i in 0..n {
                let here = original[i];
                // x-1 の画素を x へ（左隣）、x+1 の画素を x へ（右隣）。
                let from_left = (here << 1) | if i > 0 { original[i - 1] >> 63 } else { 0 };
                let from_right = (here >> 1) | if i + 1 < n { original[i + 1] << 63 } else { 0 };
                let value = if maximum {
                    here | from_left | from_right
                } else {
                    here & from_left & from_right
                };
                row[i] = if i + 1 == n { value & tail } else { value };
            }
        }
    }

    /// 縦に半径 1 の最大／最小を取る。範囲外は 0 として扱う。
    fn vertical(&mut self, maximum: bool) {
        let n = self.per_row;
        let original = self.words.clone();
        for y in 0..self.height {
            for i in 0..n {
                let here = original[y * n + i];
                let up = if y > 0 { original[(y - 1) * n + i] } else { 0 };
                let down = if y + 1 < self.height {
                    original[(y + 1) * n + i]
                } else {
                    0
                };
                self.words[y * n + i] = if maximum {
                    up | here | down
                } else {
                    up & here & down
                };
            }
        }
    }
}

/// 8 近傍で `iterations` 回膨張させる（Python の `bucket_fill.dilate` と同じ）。
pub fn dilate(mask: &[u8], width: usize, height: usize, iterations: usize) -> Vec<u8> {
    if width == 0 || height == 0 {
        return vec![0u8; width * height];
    }
    let mut bits = BitRows::pack(mask, width, height);
    for _ in 0..iterations {
        bits.horizontal(true);
        bits.vertical(true);
    }
    bits.unpack()
}

/// 8 近傍で `iterations` 回収縮させる（Python の `bucket_fill.erode` と同じ。範囲外は偽）。
pub fn erode(mask: &[u8], width: usize, height: usize, iterations: usize) -> Vec<u8> {
    if width == 0 || height == 0 {
        return vec![0u8; width * height];
    }
    let mut bits = BitRows::pack(mask, width, height);
    for _ in 0..iterations {
        bits.horizontal(false);
        bits.vertical(false);
    }
    bits.unpack()
}

/// 隣り合う画素の値が同じなら同じ領域とみなし、4 近傍でつながった領域に
/// 1 から始まる番号を付ける。`codes` が `INVALID_CODE` の画素は番号 0。
///
/// 番号は、各領域の最初の画素（行優先の順）が現れた順に振る。numpy 版
/// （`view_preview.label_regions`）と同じ番号になる。
pub const INVALID_CODE: u32 = u32::MAX;

pub fn label_regions(codes: &[u32], width: usize, height: usize) -> (Vec<u32>, u32) {
    let total = width * height;
    let mut parent: Vec<usize> = (0..total).collect();
    fn find(parent: &mut [usize], mut index: usize) -> usize {
        while parent[index] != index {
            parent[index] = parent[parent[index]];
            index = parent[index];
        }
        index
    }
    fn union(parent: &mut [usize], a: usize, b: usize) {
        let (ra, rb) = (find(parent, a), find(parent, b));
        // 小さい添字（先に現れた画素）を根にする。
        if ra < rb {
            parent[rb] = ra;
        } else if rb < ra {
            parent[ra] = rb;
        }
    }
    for y in 0..height {
        for x in 0..width {
            let index = y * width + x;
            let code = codes[index];
            if code == INVALID_CODE {
                continue;
            }
            if x > 0 && codes[index - 1] == code {
                union(&mut parent, index, index - 1);
            }
            if y > 0 && codes[index - width] == code {
                union(&mut parent, index, index - width);
            }
        }
    }
    let mut labels = vec![0u32; total];
    let mut count = 0u32;
    for index in 0..total {
        if codes[index] == INVALID_CODE {
            continue;
        }
        let root = find(&mut parent, index);
        if root == index {
            count += 1;
            labels[index] = count;
        } else {
            // 根は自分より前の画素なので、もう番号が付いている。
            labels[index] = labels[root];
        }
    }
    (labels, count)
}

fn check_size(data: &[u8], width: usize, height: usize) -> PyResult<()> {
    if width.checked_mul(height) != Some(data.len()) {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "mask has {} bytes, expected {} x {}",
            data.len(),
            width,
            height
        )));
    }
    Ok(())
}

#[pyfunction(name = "flood_fill")]
fn py_flood_fill<'py>(
    py: Python<'py>,
    passable: &[u8],
    width: usize,
    height: usize,
    seeds: Vec<(i64, i64)>,
) -> PyResult<Bound<'py, PyBytes>> {
    check_size(passable, width, height)?;
    let region = py.detach(|| flood_fill(passable, width, height, &seeds));
    Ok(PyBytes::new(py, &region))
}

#[pyfunction(name = "dilate")]
fn py_dilate<'py>(
    py: Python<'py>,
    mask: &[u8],
    width: usize,
    height: usize,
    iterations: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    check_size(mask, width, height)?;
    let result = py.detach(|| dilate(mask, width, height, iterations));
    Ok(PyBytes::new(py, &result))
}

#[pyfunction(name = "erode")]
fn py_erode<'py>(
    py: Python<'py>,
    mask: &[u8],
    width: usize,
    height: usize,
    iterations: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    check_size(mask, width, height)?;
    let result = py.detach(|| erode(mask, width, height, iterations));
    Ok(PyBytes::new(py, &result))
}

#[pyfunction(name = "label_regions")]
fn py_label_regions<'py>(
    py: Python<'py>,
    codes: &[u8],
    width: usize,
    height: usize,
) -> PyResult<(Bound<'py, PyBytes>, u32)> {
    check_size(codes, width * 4, height)?;
    let values: Vec<u32> = codes
        .chunks_exact(4)
        .map(|chunk| u32::from_ne_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]))
        .collect();
    let (labels, count) = py.detach(|| label_regions(&values, width, height));
    let bytes: Vec<u8> = labels
        .iter()
        .flat_map(|value| value.to_ne_bytes())
        .collect();
    Ok((PyBytes::new(py, &bytes), count))
}

#[pymodule]
fn pma_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<clip::ClipAnimationReader>()?;
    module.add_class::<timeline::TimelineCell>()?;
    module.add_function(wrap_pyfunction!(timeline::resolve_timeline, module)?)?;
    module.add_function(wrap_pyfunction!(display::resolve_exposures, module)?)?;
    module.add_function(wrap_pyfunction!(display::tint_onion, module)?)?;
    module.add_function(wrap_pyfunction!(display::filter_palette, module)?)?;
    module.add_class::<auto_paint::GuideSampler>()?;
    module.add_function(wrap_pyfunction!(auto_paint::label_runs, module)?)?;
    module.add_function(wrap_pyfunction!(auto_paint::connect_runs, module)?)?;
    module.add_function(wrap_pyfunction!(auto_paint::refine_face, module)?)?;
    module.add_function(wrap_pyfunction!(mask_transform::py_render_quad_labels, module)?)?;
    module.add_function(wrap_pyfunction!(mask_transform::py_render_mesh_labels, module)?)?;
    module.add_function(wrap_pyfunction!(palette_labels::py_surface_guided_palette_labels, module)?)?;
    module.add_function(wrap_pyfunction!(map_colors::py_map_known_colors, module)?)?;
    module.add_function(wrap_pyfunction!(map_colors::py_region_neighbors, module)?)?;
    module.add_function(wrap_pyfunction!(py_unique_colors, module)?)?;
    module.add_function(wrap_pyfunction!(py_flood_fill, module)?)?;
    module.add_function(wrap_pyfunction!(py_dilate, module)?)?;
    module.add_function(wrap_pyfunction!(py_erode, module)?)?;
    module.add_function(wrap_pyfunction!(py_label_regions, module)?)?;
    module.add_function(wrap_pyfunction!(py_line_flow_scores, module)?)?;
    module.add_function(wrap_pyfunction!(py_thin, module)?)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

/// Bound palette memory even for photographic layers. Keep the same smallest
/// sorted colors as numpy.unique(...)[..limit], without sorting every pixel.
#[pyfunction(name = "unique_colors")]
fn py_unique_colors(py: Python<'_>, pixels: &[u8], limit: usize) -> PyResult<Vec<u32>> {
    if !pixels.len().is_multiple_of(4) {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "expected packed u32 pixels",
        ));
    }
    py.detach(|| {
        if limit == 0 {
            return Ok(Vec::new());
        }
        // RGB has a fixed 24-bit domain: a 2 MiB bitmap avoids a tree lookup
        // for every repeated pixel in flat animation artwork.
        let mut seen = vec![0u64; (1 << 24) / 64];
        for chunk in pixels.chunks_exact(4) {
            let value = u32::from_ne_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
            if value > 0xffffff {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "expected 24-bit RGB",
                ));
            }
            seen[value as usize / 64] |= 1u64 << (value % 64);
        }
        let mut colors = Vec::new();
        for (index, mut word) in seen.into_iter().enumerate() {
            while word != 0 {
                colors.push((index * 64 + word.trailing_zeros() as usize) as u32);
                if colors.len() == limit {
                    return Ok(colors);
                }
                word &= word - 1;
            }
        }
        Ok(colors)
    })
}

#[pyfunction(name = "thin")]
fn py_thin<'py>(
    py: Python<'py>,
    mask: &[u8],
    width: usize,
    height: usize,
    iterations: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    check_size(mask, width, height)?;
    // 細線化では上下左右に1画素ずつの余白を作る。
    if width
        .checked_add(2)
        .and_then(|w| height.checked_add(2).and_then(|h| w.checked_mul(h)))
        .is_none()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "mask dimensions are too large",
        ));
    }
    let result = py.detach(|| thin::thin(mask, width, height, iterations));
    Ok(PyBytes::new(py, &result))
}

#[pyfunction(name = "line_flow_scores")]
#[allow(clippy::too_many_arguments)]
fn py_line_flow_scores<'py>(
    py: Python<'py>,
    target: &[u8],
    guide: &[u8],
    width: usize,
    height: usize,
    block: usize,
    base_y: &[u8],
    base_x: &[u8],
    reach: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    check_size(target, width, height)?;
    check_size(guide, width, height)?;
    if block == 0
        || block > 32
        || width.checked_rem(block) != Some(0)
        || height.checked_rem(block) != Some(0)
        || reach > 128
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "invalid flow grid or search reach",
        ));
    }
    let blocks = (width / block) * (height / block);
    check_size(base_y, blocks, 8)?;
    check_size(base_x, blocks, 8)?;
    let side = 2 * reach + 1;
    if blocks.checked_mul(side * side * 4).is_none() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "flow grid is too large",
        ));
    }
    let decode = |data: &[u8]| -> Vec<i64> {
        data.chunks_exact(8)
            .map(|chunk| i64::from_ne_bytes(chunk.try_into().unwrap()))
            .collect()
    };
    let base_y = decode(base_y);
    let base_x = decode(base_x);
    let bytes: Vec<u8> = py.detach(|| {
        line_flow::score_shifts(
            target,
            guide,
            (width, height),
            block,
            &base_y,
            &base_x,
            reach,
        )
        .iter()
        .flat_map(|value| value.to_ne_bytes())
        .collect()
    });
    Ok(PyBytes::new(py, &bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn labels_follow_first_appearance() {
        let x = INVALID_CODE;
        // 3x3。左上の 1 と右下の 1 はつながっていないので別の領域。
        let codes = [1, 1, x, 2, x, 1, 2, 2, 1];
        let (labels, count) = label_regions(&codes, 3, 3);
        assert_eq!(count, 3);
        assert_eq!(labels, vec![1, 1, 0, 2, 0, 3, 2, 2, 3]);
    }

    #[test]
    fn flood_stops_at_walls() {
        // 5x3、真ん中の列が壁。
        let passable = [1, 1, 0, 1, 1, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1];
        let region = flood_fill(&passable, 5, 3, &[(0, 0)]);
        assert_eq!(region, vec![1, 1, 0, 0, 0, 1, 1, 0, 0, 0, 1, 1, 0, 0, 0]);
    }

    #[test]
    fn flood_ignores_bad_seeds() {
        let passable = [0, 1, 1, 1];
        assert_eq!(flood_fill(&passable, 2, 2, &[(0, 0), (-1, 5)]), vec![0; 4]);
    }

    #[test]
    fn dilate_and_erode_use_eight_neighbours() {
        let mut mask = vec![0u8; 25];
        mask[12] = 1; // 5x5 の中央
        let grown = dilate(&mask, 5, 5, 1);
        assert_eq!(grown.iter().filter(|v| **v != 0).count(), 9);
        assert_eq!(erode(&grown, 5, 5, 1), mask);
    }
}
