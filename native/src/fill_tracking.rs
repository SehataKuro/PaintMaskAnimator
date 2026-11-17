//! コマ間のずれと色の比較の Python 入口。
use pma_core::fill::tracking;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

fn value_error(error: pma_core::Error) -> PyErr {
    PyValueError::new_err(error.to_string())
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn estimate_shifts(
    py: Python<'_>,
    previous: &[u8],
    current: &[u8],
    region: &[u8],
    width: usize,
    height: usize,
    count: usize,
    margin: Option<i64>,
) -> PyResult<Vec<(i64, i64)>> {
    py.detach(|| tracking::estimate_shifts(previous, current, region, width, height, count, margin))
        .map_err(value_error)
}

#[pyfunction(signature = (previous, current, region, width, height, shift, origin=(0, 0)))]
#[allow(clippy::too_many_arguments)]
pub fn warp_region<'py>(
    py: Python<'py>,
    previous: &[u8],
    current: &[u8],
    region: &[u8],
    width: usize,
    height: usize,
    shift: (i64, i64),
    origin: (i64, i64),
) -> PyResult<Option<Bound<'py, PyBytes>>> {
    let mask = py
        .detach(|| tracking::warp_region(previous, current, region, width, height, shift, origin))
        .map_err(value_error)?;
    Ok(mask.map(|mask| PyBytes::new(py, &mask)))
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn estimate_color_shifts(
    py: Python<'_>,
    previous: &[u8],
    current: &[u8],
    region: &[u8],
    before: (usize, usize),
    after: (usize, usize),
    offset: (i64, i64),
    count: usize,
) -> PyResult<Vec<(i64, i64)>> {
    py.detach(|| {
        tracking::estimate_color_shifts(
            previous, current, region, before.0, before.1, after.0, after.1, offset, count,
        )
    })
    .map_err(value_error)
}

#[pyfunction]
pub fn tracking_region_center(
    py: Python<'_>,
    region: &[u8],
    width: usize,
    height: usize,
) -> PyResult<Option<(i64, i64)>> {
    py.detach(|| tracking::region_center(region, width, height))
        .map_err(value_error)
}

type ContextWindow<'a> = (&'a [u8], &'a [u8], (usize, usize), (i64, i64));

#[pyfunction]
pub fn context_similarity(
    py: Python<'_>,
    before: ContextWindow<'_>,
    after: ContextWindow<'_>,
) -> PyResult<f64> {
    py.detach(|| {
        tracking::context_similarity(
            before.0, before.1, before.2, before.3, after.0, after.1, after.2, after.3,
        )
    })
    .map_err(value_error)
}
