use pma_core::{colors, Error};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

fn value_error(error: Error) -> PyErr {
    match error {
        Error::Invalid(message) => PyValueError::new_err(message),
        other => PyValueError::new_err(other.to_string()),
    }
}

#[pyfunction]
pub fn unique_colors(py: Python<'_>, pixels: &[u8], limit: usize) -> PyResult<Vec<u32>> {
    py.detach(|| {
        // 既存の Python API はネイティブエンディアンの u32 を受け取る。
        #[cfg(target_endian = "big")]
        let little_endian = {
            let mut bytes = pixels.to_vec();
            for pixel in bytes.chunks_exact_mut(4) {
                pixel.reverse();
            }
            bytes
        };
        #[cfg(target_endian = "big")]
        let pixels = &little_endian;
        colors::unique_colors(pixels, limit)
    })
    .map_err(value_error)
}

#[pyfunction]
pub fn local_color_variation<'py>(
    py: Python<'py>,
    rgb: &[u8],
    width: usize,
    height: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    let result = py
        .detach(|| colors::local_color_variation(rgb, width, height))
        .map_err(value_error)?;
    Ok(PyBytes::new(py, &result))
}

#[pyfunction]
pub fn median_cut_palette_from_samples<'py>(
    py: Python<'py>,
    sample: &[u8],
    color_count: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    let result = py
        .detach(|| colors::median_cut_palette_from_samples(sample, color_count))
        .map_err(value_error)?;
    Ok(PyBytes::new(py, result.as_flattened()))
}

#[pyfunction]
pub fn normalize_tone_curve_points(py: Python<'_>, points: Vec<(f64, f64)>) -> Vec<(f64, f64)> {
    py.detach(|| colors::normalize_tone_curve_points(points))
}
