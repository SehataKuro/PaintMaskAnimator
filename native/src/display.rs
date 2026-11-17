//! Display computations without Qt dependencies, reusable by a future Rust UI.
use pma_core::display::{filter_palette_of, tint_onion_of, Rgb};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

#[pyfunction]
pub fn filter_palette<'py>(
    py: Python<'py>,
    rgba: &[u8],
    visible: Option<Vec<Rgb>>,
    legacy: Option<Rgb>,
    remap: Vec<(Rgb, Rgb)>,
) -> PyResult<Bound<'py, PyBytes>> {
    if !rgba.len().is_multiple_of(4) {
        return Err(PyValueError::new_err("expected packed RGBA pixels"));
    }
    let output = py.detach(|| filter_palette_of(rgba, visible, legacy, remap));
    Ok(PyBytes::new(py, &output))
}

#[pyfunction]
pub fn tint_onion<'py>(
    py: Python<'py>,
    rgba: &[u8],
    color: (u8, u8, u8),
    enabled: bool,
    selected: Option<Vec<(u8, u8, u8)>>,
) -> PyResult<Bound<'py, PyBytes>> {
    if !rgba.len().is_multiple_of(4) {
        return Err(PyValueError::new_err("expected packed RGBA pixels"));
    }
    let output = py.detach(|| tint_onion_of(rgba, color, enabled, selected));
    Ok(PyBytes::new(py, &output))
}
