//! PaintMaskAnimator の重い画像計算を Rust で行う部品。
//!
//! 方針（docs/design-principles.md「将来は Rust へ置き換えていく」）に沿って、
//! 画面や Qt に触れない純粋な関数だけを置く。Python 側の numpy 版と
//! 同じ結果になることを、Python のテストで突き合わせる。
//!
//! マスクは 1 画素 1 バイト（0 か 1）の行優先の並びでやり取りする。
//! numpy の bool 配列をそのまま `tobytes()` した形と同じ。

use pyo3::prelude::*;
use pyo3::types::PyBytes;

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
                        cx += next_pass[cx..].iter().position(|p| *p == 0).unwrap_or(span - cx);
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
        BitRows { words, per_row, width, height }
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
        if used >= 64 { u64::MAX } else { (1u64 << used) - 1 }
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
                let down = if y + 1 < self.height { original[(y + 1) * n + i] } else { 0 };
                self.words[y * n + i] = if maximum { up | here | down } else { up & here & down };
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

fn check_size(data: &[u8], width: usize, height: usize) -> PyResult<()> {
    if data.len() != width * height {
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

#[pymodule]
fn pma_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(py_flood_fill, module)?)?;
    module.add_function(wrap_pyfunction!(py_dilate, module)?)?;
    module.add_function(wrap_pyfunction!(py_erode, module)?)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

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
