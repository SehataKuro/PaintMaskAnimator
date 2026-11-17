use std::thread;

use pma_core::mask_transform::{
    mesh_band_renderer, quad_band_renderer, BandOutput, Homography, LabelImage, Mesh, OUTSIDE,
};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// 出力を `band_height` 行ずつの帯に分け、`render` を並べて呼ぶ。
///
/// 帯の区切りは `band_height` の倍数に揃える。メッシュ変形の計算は帯の先頭の
/// 行を原点に使うので、区切りがずれると結果も変わる。
fn render_in_bands<F>(
    out_width: usize,
    out_height: usize,
    coverage_labels: &[u16],
    band_height: usize,
    threads: usize,
    render: F,
) -> (Vec<u16>, Vec<Vec<u8>>)
where
    F: Fn(BandOutput) + Send + Sync,
{
    let mut out_labels = vec![OUTSIDE; out_width * out_height];
    let mut out_coverage: Vec<Vec<u8>> = coverage_labels
        .iter()
        .map(|_| vec![0u8; out_width * out_height])
        .collect();
    if out_width == 0 || out_height == 0 {
        return (out_labels, out_coverage);
    }

    // 帯は互いに影響しないので、帯をいくつかまとめた塊ごとに並べて解く。
    let band_height = band_height.clamp(1, out_height);
    let bands = out_height.div_ceil(band_height);
    let chunk_bands = bands.div_ceil(threads.max(1));
    let chunk_rows = chunk_bands * band_height;
    let mut label_chunks: Vec<&mut [u16]> = out_labels.chunks_mut(chunk_rows * out_width).collect();
    // 被覆は番号ごとに 1 面あるので、塊ごとに切れ端を集めて渡す。
    let mut coverage_chunks: Vec<Vec<&mut [u8]>> = out_coverage
        .iter_mut()
        .map(|buffer| buffer.chunks_mut(chunk_rows * out_width).collect())
        .collect();

    thread::scope(|scope| {
        for (index, label_chunk) in label_chunks.iter_mut().enumerate() {
            let coverage_chunk: Vec<&mut [u8]> = coverage_chunks
                .iter_mut()
                .map(|chunks| std::mem::take(&mut chunks[index]))
                .collect();
            let render = &render;
            scope.spawn(move || {
                pma_core::mask_transform::render_chunk(
                    out_width,
                    band_height,
                    index * chunk_rows,
                    label_chunk,
                    coverage_chunk,
                    render,
                );
            });
        }
    });

    (out_labels, out_coverage)
}

/// 平面四角形の逆写像で `image` を `out_width x out_height` に描く。
///
/// 戻り値は `(番号, coverage_labels ごとの被覆 0-255)`。
fn render_quad_bands(
    image: &LabelImage,
    matrix: &Homography,
    out_width: usize,
    out_height: usize,
    subsamples: usize,
    coverage_labels: &[u16],
    threads: usize,
) -> (Vec<u16>, Vec<Vec<u8>>) {
    let render = quad_band_renderer(image, matrix, out_width, subsamples, coverage_labels);
    render_in_bands(out_width, out_height, coverage_labels, 1, threads, render)
}

/// メッシュの逆写像で `image` を `out_width x out_height` に描く。
///
/// `band_height` は一度に解く行数。probe の番号を帯の分だけ持つので、
/// 使う記憶領域の上限になる。
#[allow(clippy::too_many_arguments)]
fn render_mesh_bands(
    image: &LabelImage,
    mesh: &Mesh,
    out_width: usize,
    out_height: usize,
    subsamples: usize,
    band_height: usize,
    coverage_labels: &[u16],
    threads: usize,
) -> (Vec<u16>, Vec<Vec<u8>>) {
    let render = mesh_band_renderer(image, mesh, out_width, subsamples, coverage_labels);
    render_in_bands(
        out_width,
        out_height,
        coverage_labels,
        band_height,
        threads,
        render,
    )
}

fn check_inputs(
    labels: &[u8],
    source_width: usize,
    source_height: usize,
    out_width: usize,
    out_height: usize,
    subsamples: usize,
) -> PyResult<Vec<u16>> {
    let invalid = |message: &str| pyo3::exceptions::PyValueError::new_err(message.to_string());
    if source_width
        .checked_mul(source_height)
        .map(|total| total * 2)
        != Some(labels.len())
    {
        return Err(invalid("label image size does not match width x height"));
    }
    if subsamples == 0 || subsamples > 16 {
        return Err(invalid("subsamples must be between 1 and 16"));
    }
    if out_width == 0 || out_height == 0 {
        return Err(invalid("output size must not be empty"));
    }
    if out_width
        .checked_mul(out_height)
        .and_then(|total| total.checked_mul(2 * subsamples * subsamples))
        .is_none()
    {
        return Err(invalid("output size is too large"));
    }
    Ok(labels
        .chunks_exact(2)
        .map(|chunk| u16::from_ne_bytes([chunk[0], chunk[1]]))
        .collect())
}

fn threads() -> usize {
    thread::available_parallelism()
        .map(|value| value.get())
        .unwrap_or(1)
}

fn to_python<'py>(
    py: Python<'py>,
    result: (Vec<u16>, Vec<Vec<u8>>),
) -> (Bound<'py, PyBytes>, Vec<Bound<'py, PyBytes>>) {
    let (labels, coverage) = result;
    let bytes: Vec<u8> = labels
        .iter()
        .flat_map(|value| value.to_ne_bytes())
        .collect();
    (
        PyBytes::new(py, &bytes),
        coverage
            .iter()
            .map(|buffer| PyBytes::new(py, buffer))
            .collect(),
    )
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
    let values = check_inputs(
        labels,
        source_width,
        source_height,
        out_width,
        out_height,
        subsamples,
    )?;
    let threads = threads();
    let result = py.detach(|| {
        let image = LabelImage {
            labels: &values,
            width: source_width,
            height: source_height,
        };
        render_quad_bands(
            &image,
            &Homography(matrix),
            out_width,
            out_height,
            subsamples,
            &coverage_labels,
            threads,
        )
    });
    Ok(to_python(py, result))
}

#[pyfunction(name = "render_mesh_labels")]
#[allow(clippy::too_many_arguments)]
pub fn py_render_mesh_labels<'py>(
    py: Python<'py>,
    labels: &[u8],
    source_width: usize,
    source_height: usize,
    points: Vec<(f64, f64)>,
    dense_cols: usize,
    dense_rows: usize,
    out_width: usize,
    out_height: usize,
    subsamples: usize,
    band_rows: usize,
    coverage_labels: Vec<u16>,
) -> PyResult<(Bound<'py, PyBytes>, Vec<Bound<'py, PyBytes>>)> {
    let values = check_inputs(
        labels,
        source_width,
        source_height,
        out_width,
        out_height,
        subsamples,
    )?;
    if dense_cols < 2 || dense_rows < 2 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "the mesh needs at least 2x2 points",
        ));
    }
    if dense_cols.checked_mul(dense_rows) != Some(points.len()) {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "mesh point count does not match cols x rows",
        ));
    }
    let threads = threads();
    let result = py.detach(|| {
        let image = LabelImage {
            labels: &values,
            width: source_width,
            height: source_height,
        };
        let mesh = Mesh {
            points: &points,
            cols: dense_cols,
            rows: dense_rows,
        };
        render_mesh_bands(
            &image,
            &mesh,
            out_width,
            out_height,
            subsamples,
            band_rows.max(1),
            &coverage_labels,
            threads,
        )
    });
    Ok(to_python(py, result))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn image<'a>(labels: &'a [u16], width: usize, height: usize) -> LabelImage<'a> {
        LabelImage {
            labels,
            width,
            height,
        }
    }

    fn core_render_quad(
        labels: &[u16],
        size: (usize, usize),
        matrix: &Homography,
        out: (usize, usize),
        subsamples: usize,
        coverage_labels: &[u16],
    ) -> (Vec<u16>, Vec<Vec<u8>>) {
        pma_core::mask_transform::render_quad(
            &image(labels, size.0, size.1),
            matrix,
            out.0,
            out.1,
            subsamples,
            coverage_labels,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn core_render_mesh(
        labels: &[u16],
        size: (usize, usize),
        points: &[(f64, f64)],
        grid: (usize, usize),
        out: (usize, usize),
        subsamples: usize,
        band_rows: usize,
        coverage_labels: &[u16],
    ) -> (Vec<u16>, Vec<Vec<u8>>) {
        pma_core::mask_transform::render_mesh(
            &image(labels, size.0, size.1),
            &Mesh {
                points,
                cols: grid.0,
                rows: grid.1,
            },
            out.0,
            out.1,
            subsamples,
            band_rows,
            coverage_labels,
        )
    }

    fn render_quad(
        labels: &[u16],
        size: (usize, usize),
        matrix: &Homography,
        out: (usize, usize),
        subsamples: usize,
        coverage_labels: &[u16],
        threads: usize,
    ) -> (Vec<u16>, Vec<Vec<u8>>) {
        render_quad_bands(
            &image(labels, size.0, size.1),
            matrix,
            out.0,
            out.1,
            subsamples,
            coverage_labels,
            threads,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn render_mesh(
        labels: &[u16],
        size: (usize, usize),
        points: &[(f64, f64)],
        grid: (usize, usize),
        out: (usize, usize),
        subsamples: usize,
        band_rows: usize,
        coverage_labels: &[u16],
        threads: usize,
    ) -> (Vec<u16>, Vec<Vec<u8>>) {
        render_mesh_bands(
            &image(labels, size.0, size.1),
            &Mesh {
                points,
                cols: grid.0,
                rows: grid.1,
            },
            out.0,
            out.1,
            subsamples,
            band_rows,
            coverage_labels,
            threads,
        )
    }

    #[test]
    fn threads_do_not_change_the_quad_result() {
        let labels: Vec<u16> = (0..64u16).map(|value| value % 5).collect();
        let matrix = Homography([0.37, 0.11, 1.5, -0.2, 0.44, 2.0, 0.0, 0.0, 1.0]);
        let single = core_render_quad(&labels, (8, 8), &matrix, (17, 13), 4, &[1, 3]);
        let many = render_quad(&labels, (8, 8), &matrix, (17, 13), 4, &[1, 3], 6);
        assert_eq!(single, many);
    }

    #[test]
    fn mesh_bands_and_threads_do_not_change_the_result() {
        let labels: Vec<u16> = (0..64u16).map(|value| value % 4).collect();
        // 3x3 の格子を曲げる（真ん中を持ち上げる）。
        let points = vec![
            (0.0, 0.0),
            (6.0, -1.0),
            (13.0, 1.0),
            (1.0, 7.0),
            (7.0, 5.0),
            (12.0, 8.0),
            (0.5, 13.0),
            (6.0, 14.0),
            (13.0, 12.0),
        ];
        let banded = core_render_mesh(&labels, (8, 8), &points, (3, 3), (15, 15), 4, 4, &[1, 2]);
        let threaded = render_mesh(&labels, (8, 8), &points, (3, 3), (15, 15), 4, 4, &[1, 2], 5);
        assert_eq!(banded, threaded);
    }
}
