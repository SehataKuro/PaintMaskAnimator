//! 色マスクの変形（逆写像＋面積被覆）のうち、平面四角形（拡大縮小・回転・
//! 自由変形）の経路。
//!
//! Python の `mask_transform.render_labels_numpy` と同じ結果を返す。出力画素
//! ごとに `subsamples * subsamples` 個の probe を元画像へ逆写像し、もっとも多く
//! 取った番号がその画素を取る（同数なら小さい番号が勝つ）。帯ごとの大きな
//! 一時配列を作らないので、使う記憶領域は出力の分だけで済む。

use std::thread;

use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// probe の逆写像が使えないときに読む番号（元画像の外と同じ扱い）。
const OUTSIDE: u16 = 0;

/// 逆写像の 3x3（出力座標 → 元座標）。
pub struct Homography(pub [f64; 9]);

impl Homography {
    /// 出力座標 `(x, y)` の元座標。射影の分母が 0 に近ければ `None`。
    #[inline]
    fn map(&self, x: f64, y: f64) -> Option<(f64, f64)> {
        let m = &self.0;
        let denominator = m[6] * x + m[7] * y + m[8];
        if denominator.abs() <= 1e-12 {
            return None;
        }
        Some((
            (m[0] * x + m[1] * y + m[2]) / denominator,
            (m[3] * x + m[4] * y + m[5]) / denominator,
        ))
    }
}

/// 出力の 1 行分を解いて、番号と被覆を書き込む。
#[allow(clippy::too_many_arguments)]
fn render_row(
    labels: &[u16],
    (source_width, source_height): (usize, usize),
    matrix: &Homography,
    out_width: usize,
    row: usize,
    subsamples: usize,
    coverage_labels: &[u16],
    out_labels: &mut [u16],
    out_coverage: &mut [&mut [u8]],
) {
    let probes = subsamples * subsamples;
    let step = 1.0 / subsamples as f64;
    // probe は画素の内側に半歩入った位置にある。
    let offsets: Vec<f64> = (0..subsamples).map(|i| (i as f64 + 0.5) * step).collect();
    // probe ごとの番号。画素をまたいで作り直さない。
    let mut sampled = vec![OUTSIDE; probes];
    // 番号と、その番号を取った probe の数。probes は小さいので素直に数える。
    let mut tally: Vec<(u16, usize)> = Vec::with_capacity(probes);

    for x in 0..out_width {
        for (sy, offset_y) in offsets.iter().enumerate() {
            let probe_y = row as f64 + offset_y;
            for (sx, offset_x) in offsets.iter().enumerate() {
                let probe_x = x as f64 + offset_x;
                sampled[sy * subsamples + sx] = match matrix.map(probe_x, probe_y) {
                    None => OUTSIDE,
                    Some((source_x, source_y)) => {
                        let fx = source_x.floor();
                        let fy = source_y.floor();
                        // 元画像の外を読む probe は「何もない」として数える。
                        if fx < 0.0
                            || fy < 0.0
                            || fx >= source_width as f64
                            || fy >= source_height as f64
                        {
                            OUTSIDE
                        } else {
                            labels[fy as usize * source_width + fx as usize]
                        }
                    }
                };
            }
        }

        // 境目でない画素（probe が全部同じ）は数えずに決める。
        let first = sampled[0];
        let uniform = sampled.iter().all(|value| *value == first);
        let winner = if uniform {
            first
        } else {
            tally.clear();
            for value in &sampled {
                match tally.iter_mut().find(|(label, _)| label == value) {
                    Some((_, count)) => *count += 1,
                    None => tally.push((*value, 1)),
                }
            }
            // 同数なら小さい番号が勝つ（番号は優先順位の並び）。
            tally
                .iter()
                .copied()
                .reduce(|best, here| match here.1.cmp(&best.1) {
                    std::cmp::Ordering::Greater => here,
                    std::cmp::Ordering::Equal if here.0 < best.0 => here,
                    _ => best,
                })
                .map(|(label, _)| label)
                .unwrap_or(OUTSIDE)
        };
        out_labels[x] = winner;

        for (slot, label) in coverage_labels.iter().enumerate() {
            let hits = if uniform {
                if first == *label { probes } else { 0 }
            } else {
                sampled.iter().filter(|value| *value == label).count()
            };
            out_coverage[slot][x] = ((hits * 255) / probes) as u8;
        }
    }
}

/// 平面四角形の逆写像で `labels` を `out_width x out_height` に描く。
///
/// 戻り値は `(番号, coverage_labels ごとの被覆 0-255)`。
#[allow(clippy::too_many_arguments)]
pub fn render_quad(
    labels: &[u16],
    source_size: (usize, usize),
    matrix: &Homography,
    out_width: usize,
    out_height: usize,
    subsamples: usize,
    coverage_labels: &[u16],
    threads: usize,
) -> (Vec<u16>, Vec<Vec<u8>>) {
    let mut out_labels = vec![OUTSIDE; out_width * out_height];
    let mut out_coverage: Vec<Vec<u8>> =
        coverage_labels.iter().map(|_| vec![0u8; out_width * out_height]).collect();
    if out_width == 0 || out_height == 0 {
        return (out_labels, out_coverage);
    }

    // 行は互いに影響しないので、行の束に分けて並べて解く。
    let threads = threads.clamp(1, out_height);
    let band = out_height.div_ceil(threads);
    let mut label_bands: Vec<&mut [u16]> = out_labels.chunks_mut(band * out_width).collect();
    // 被覆は番号ごとに 1 面あるので、束ごとに切れ端を集めて渡す。
    let mut coverage_bands: Vec<Vec<&mut [u8]>> = out_coverage
        .iter_mut()
        .map(|buffer| buffer.chunks_mut(band * out_width).collect())
        .collect();

    thread::scope(|scope| {
        for (index, label_band) in label_bands.iter_mut().enumerate() {
            let coverage_band: Vec<&mut [u8]> = coverage_bands
                .iter_mut()
                .map(|bands| std::mem::take(&mut bands[index]))
                .collect();
            scope.spawn(move || {
                let mut coverage_band = coverage_band;
                let y0 = index * band;
                for row in 0..label_band.len() / out_width {
                    let span = row * out_width..(row + 1) * out_width;
                    let mut coverage_row: Vec<&mut [u8]> = coverage_band
                        .iter_mut()
                        .map(|buffer| &mut buffer[span.clone()])
                        .collect();
                    render_row(
                        labels,
                        source_size,
                        matrix,
                        out_width,
                        y0 + row,
                        subsamples,
                        coverage_labels,
                        &mut label_band[span],
                        &mut coverage_row,
                    );
                }
            });
        }
    });

    (out_labels, out_coverage)
}

#[pyfunction(name = "render_quad_labels")]
#[allow(clippy::too_many_arguments)]
pub fn py_render_quad_labels<'py>(
    py: Python<'py>,
    labels: &[u8],
    source_width: usize,
    source_height: usize,
    matrix: [f64; 9],
    out_width: usize,
    out_height: usize,
    subsamples: usize,
    coverage_labels: Vec<u16>,
) -> PyResult<(Bound<'py, PyBytes>, Vec<Bound<'py, PyBytes>>)> {
    let invalid = |message: &str| Err(pyo3::exceptions::PyValueError::new_err(message.to_string()));
    if source_width.checked_mul(source_height).map(|total| total * 2) != Some(labels.len()) {
        return invalid("label image size does not match width x height");
    }
    if subsamples == 0 || subsamples > 16 {
        return invalid("subsamples must be between 1 and 16");
    }
    if out_width == 0 || out_height == 0 {
        return invalid("output size must not be empty");
    }
    if out_width.checked_mul(out_height).and_then(|total| total.checked_mul(2)).is_none() {
        return invalid("output size is too large");
    }
    let values: Vec<u16> = labels
        .chunks_exact(2)
        .map(|chunk| u16::from_ne_bytes([chunk[0], chunk[1]]))
        .collect();
    let threads = thread::available_parallelism().map(|value| value.get()).unwrap_or(1);
    let (result, coverage) = py.detach(|| {
        render_quad(
            &values,
            (source_width, source_height),
            &Homography(matrix),
            out_width,
            out_height,
            subsamples,
            &coverage_labels,
            threads,
        )
    });
    let bytes: Vec<u8> = result.iter().flat_map(|value| value.to_ne_bytes()).collect();
    Ok((
        PyBytes::new(py, &bytes),
        coverage.iter().map(|buffer| PyBytes::new(py, buffer)).collect(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 恒等変換。probe は画素の中に並ぶので、そのまま写る。
    fn identity() -> Homography {
        Homography([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0])
    }

    #[test]
    fn identity_keeps_labels_and_full_coverage() {
        let labels = vec![0u16, 1, 2, 0];
        let (result, coverage) = render_quad(&labels, (2, 2), &identity(), 2, 2, 4, &[1, 2], 1);
        assert_eq!(result, labels);
        assert_eq!(coverage[0], vec![0, 255, 0, 0]);
        assert_eq!(coverage[1], vec![0, 0, 255, 0]);
    }

    #[test]
    fn outside_probes_read_nothing() {
        let labels = vec![1u16];
        let (result, _coverage) = render_quad(&labels, (1, 1), &identity(), 3, 1, 2, &[], 1);
        assert_eq!(result, vec![1, 0, 0]);
    }

    #[test]
    fn half_coverage_splits_a_doubled_pixel() {
        // 横に 2 倍へ伸ばすと、元の 1 画素が出力の 2 画素を埋める。
        let labels = vec![1u16, 2];
        let matrix = Homography([0.5, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]);
        let (result, coverage) = render_quad(&labels, (2, 1), &matrix, 4, 1, 4, &[2], 1);
        assert_eq!(result, vec![1, 1, 2, 2]);
        assert_eq!(coverage[0], vec![0, 0, 255, 255]);
    }

    #[test]
    fn ties_go_to_the_smaller_label() {
        // 2 画素を 1 画素に縮める。probe が 2 つずつに分かれて同数になる。
        let labels = vec![2u16, 1];
        let matrix = Homography([2.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]);
        let (result, _coverage) = render_quad(&labels, (2, 1), &matrix, 1, 1, 2, &[], 1);
        assert_eq!(result, vec![1]);
    }

    #[test]
    fn the_projective_horizon_reads_nothing() {
        // 分母が 0 になる行を含む行列。無限にせず「何もない」として扱う。
        let labels = vec![1u16; 4];
        let matrix = Homography([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, -0.5]);
        let (result, _coverage) = render_quad(&labels, (2, 2), &matrix, 2, 2, 1, &[], 1);
        assert_eq!(result[0], 0);
    }

    #[test]
    fn threads_do_not_change_the_result() {
        let labels: Vec<u16> = (0..64u16).map(|value| value % 5).collect();
        let matrix = Homography([0.37, 0.11, 1.5, -0.2, 0.44, 2.0, 0.0, 0.0, 1.0]);
        let single = render_quad(&labels, (8, 8), &matrix, 17, 13, 4, &[1, 3], 1);
        let many = render_quad(&labels, (8, 8), &matrix, 17, 13, 4, &[1, 3], 6);
        assert_eq!(single, many);
    }
}
