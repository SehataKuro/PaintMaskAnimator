//! Python entry points for reference-check statistics.
use pma_core::reference_check::{self, Bounds};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyByteArray;

#[pyfunction]
pub fn reference_color_stats(
    py: Python<'_>,
    rgba: &[u8],
    width: usize,
) -> PyResult<Vec<(u32, usize, Bounds)>> {
    let stride = width.checked_mul(4).ok_or_else(invalid_buffers)?;
    if !rgba.len().is_multiple_of(stride) {
        return Err(invalid_buffers());
    }
    Ok(py.detach(|| {
        reference_check::color_stats(rgba, width)
            .into_iter()
            .map(|(value, stats)| (value, stats.size, stats.bounds))
            .collect()
    }))
}

#[pyfunction]
pub fn reference_line_art<'py>(
    py: Python<'py>,
    rgba: &[u8],
    line_max: u8,
) -> PyResult<Bound<'py, PyByteArray>> {
    if !rgba.len().is_multiple_of(4) {
        return Err(invalid_buffers());
    }
    let mask = py.detach(|| reference_check::line_art(rgba, line_max));
    Ok(PyByteArray::new(py, &mask))
}

type RunBuffers<'a> = (&'a [u8], &'a [u8], &'a [u8], &'a [u8]);
type RegionSummary = (usize, Bounds, bool);

fn invalid_buffers() -> PyErr {
    PyValueError::new_err("invalid reference check buffers")
}

#[pyfunction]
pub fn reference_region_stats(
    py: Python<'_>,
    runs: RunBuffers<'_>,
    count: usize,
    width: usize,
    height: usize,
) -> PyResult<Vec<RegionSummary>> {
    let (ys, starts, ends, labels) = runs;
    if !labels.len().is_multiple_of(4)
        || labels.len().checked_mul(2) != Some(ys.len())
        || ys.len() != starts.len()
        || ys.len() != ends.len()
        || count > labels.len() / 4
    {
        return Err(invalid_buffers());
    }
    py.detach(|| {
        let decode = |word: &[u8]| u64::from_ne_bytes(word.try_into().unwrap()) as usize;
        let runs = ys
            .chunks_exact(8)
            .zip(starts.chunks_exact(8))
            .zip(ends.chunks_exact(8))
            .zip(labels.chunks_exact(4))
            .map(|(((y, start), end), label)| {
                (
                    decode(y),
                    decode(start),
                    decode(end),
                    u32::from_ne_bytes(label.try_into().unwrap()) as usize,
                )
            });
        let stats = reference_check::summarize_runs(count, width, height, runs)
            .ok_or_else(invalid_buffers)?;
        Ok(stats
            .into_iter()
            .map(|stats| {
                (
                    stats.size,
                    stats.bounds,
                    reference_check::on_edge(stats.bounds, width, height),
                )
            })
            .collect())
    })
}

#[pyfunction]
pub fn reference_region_under(
    py: Python<'_>,
    labels: &[u8],
    lines: &[u8],
    shape: (usize, usize),
    mask: &[u8],
    bounds: Bounds,
) -> PyResult<(Option<i32>, f64)> {
    let (width, height) = shape;
    let (x0, y0, x1, y1) = bounds;
    if width.checked_mul(height) != Some(lines.len())
        || lines.len().checked_mul(4) != Some(labels.len())
        || x0 > x1
        || y0 > y1
        || x1 > width
        || y1 > height
        || (x1 - x0).checked_mul(y1 - y0) != Some(mask.len())
    {
        return Err(invalid_buffers());
    }
    py.detach(|| reference_check::region_under(labels, lines, width, mask, bounds))
        .map_err(|_| invalid_buffers())
}
