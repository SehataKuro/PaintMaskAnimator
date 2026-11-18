use pma_core::{color_stats, Error};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

fn value_error(error: Error) -> PyErr {
    PyValueError::new_err(error.to_string())
}

#[pyfunction]
pub fn image_alpha_statistics(
    py: Python<'_>,
    rgba: &[u8],
    width: usize,
    height: usize,
) -> PyResult<(usize, usize, usize)> {
    py.detach(|| color_stats::image_alpha_statistics(rgba, width, height))
        .map_err(value_error)
}

#[pyfunction]
pub fn binarize_alpha_for_pixel_art<'py>(
    py: Python<'py>,
    rgba: &[u8],
    width: usize,
    height: usize,
    threshold: u8,
) -> PyResult<Bound<'py, PyBytes>> {
    let result = py
        .detach(|| color_stats::binarize_alpha_for_pixel_art(rgba, width, height, threshold))
        .map_err(value_error)?;
    Ok(PyBytes::new(py, &result))
}

#[pyfunction]
pub fn opaque_rgb_color_count(
    py: Python<'_>,
    rgba: &[u8],
    width: usize,
    height: usize,
) -> PyResult<usize> {
    py.detach(|| color_stats::opaque_rgb_color_count(rgba, width, height))
        .map_err(value_error)
}

#[pyfunction]
pub fn border_background(py: Python<'_>, border: &[u8]) -> PyResult<Option<(u8, u8, u8)>> {
    py.detach(|| color_stats::border_background(border))
        .map(|result| result.map(|[r, g, b]| (r, g, b)))
        .map_err(value_error)
}

#[pyfunction(signature = (rgba, width, height, background=None))]
pub fn estimate_mixed_boundary_pixels(
    py: Python<'_>,
    rgba: &[u8],
    width: usize,
    height: usize,
    background: Option<(u8, u8, u8)>,
) -> PyResult<usize> {
    py.detach(|| {
        color_stats::estimate_mixed_boundary_pixels(
            rgba,
            width,
            height,
            background.map(|(r, g, b)| [r, g, b]),
        )
    })
    .map_err(value_error)
}

#[pyfunction]
pub fn snap_overlay_to_exact_rgbs<'py>(
    py: Python<'py>,
    rgba: &[u8],
    width: usize,
    height: usize,
    mask: &[u8],
    palette: &[u8],
) -> PyResult<Bound<'py, PyBytes>> {
    let result = py
        .detach(|| color_stats::snap_overlay_to_exact_rgbs(rgba, width, height, mask, palette))
        .map_err(value_error)?;
    Ok(PyBytes::new(py, &result))
}
