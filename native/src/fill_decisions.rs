//! バケツの島・穴・追跡色の Python 入口。
use pma_core::fill::islands::{self, Island, Replacement};
use pma_core::fill::session::{Bounds, Category};
use pma_core::fill::tracking;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

pub(crate) type IslandOutput<'py> = ((u8, u8, u8), Bounds, Bound<'py, PyBytes>);

pub(crate) fn island_output(py: Python<'_>, islands: Vec<Island>) -> Vec<IslandOutput<'_>> {
    islands
        .into_iter()
        .map(|island| {
            let [r, g, b] = island.color;
            ((r, g, b), island.bounds, PyBytes::new(py, &island.mask))
        })
        .collect()
}

fn value_error(error: pma_core::Error) -> PyErr {
    PyValueError::new_err(error.to_string())
}

#[pyfunction]
pub fn enclosed_by<'py>(
    py: Python<'py>,
    region: &[u8],
    width: usize,
    height: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    let mask = py
        .detach(|| islands::enclosed_by(region, width, height))
        .map_err(value_error)?;
    Ok(PyBytes::new(py, &mask))
}

#[pyfunction]
#[pyo3(signature=(rgba, dimensions, region, bounds, replacements, min_share, selection=None))]
#[allow(clippy::too_many_arguments)]
pub fn enclosed_islands<'py>(
    py: Python<'py>,
    rgba: &[u8],
    dimensions: (usize, usize),
    region: &[u8],
    bounds: Bounds,
    replacements: Vec<Replacement>,
    min_share: f64,
    selection: Option<&[u8]>,
) -> PyResult<Vec<IslandOutput<'py>>> {
    let result = py
        .detach(|| {
            islands::enclosed_islands(
                rgba,
                dimensions,
                region,
                bounds,
                &replacements,
                selection,
                min_share,
            )
        })
        .map_err(value_error)?;
    Ok(island_output(py, result))
}

#[pyfunction]
pub fn guess_target_categories(
    py: Python<'_>,
    rgba: &[u8],
    previous: &[u8],
    before: &[u8],
    width: usize,
    height: usize,
) -> PyResult<Vec<Category>> {
    py.detach(|| tracking::guess_target_categories(rgba, previous, before, width, height))
        .map_err(value_error)
}

#[pyfunction]
pub fn skewer_seed(
    py: Python<'_>,
    rgba: &[u8],
    width: usize,
    height: usize,
    reference: Category,
) -> PyResult<Option<(usize, usize)>> {
    py.detach(|| pma_core::fill::target::skewer_seed(rgba, width, height, reference))
        .map_err(value_error)
}
