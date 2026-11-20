//! タイムラインの Python バインディング。
use pma_core::{document::timeline, Error};
use pyo3::panic::PanicException;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

#[pyclass(get_all, set_all, module = "pma_native")]
#[derive(Default)]
pub struct TimelineCell {
    pub has_content: bool,
    pub is_blank_key: bool,
    pub blank_start: bool,
    pub sequence_number: Option<i64>,
    pub sequence_only: bool,
}

#[pymethods]
impl TimelineCell {
    #[new]
    fn new() -> Self {
        Self::default()
    }
}

/// Increment when the Python timeline metadata contract changes.
pub const TIMELINE_API_VERSION: u32 = 2;

#[pyclass(get_all, module = "pma_native")]
pub struct SheetEntry {
    pub kind: u8,
    pub number: Option<i64>,
    pub key: i64,
}

fn snapshot_rows(
    py: Python<'_>,
    rows: &[Vec<Py<TimelineCell>>],
) -> Vec<Vec<timeline::TimelineCell>> {
    rows.iter()
        .map(|row| {
            row.iter()
                .map(|cell| {
                    let cell = cell.borrow(py);
                    timeline::TimelineCell {
                        has_content: cell.has_content,
                        is_blank_key: cell.is_blank_key,
                        blank_start: cell.blank_start,
                        sequence_number: cell.sequence_number,
                        sequence_only: cell.sequence_only,
                    }
                })
                .collect()
        })
        .collect()
}

fn timeline_error(error: Error) -> PyErr {
    // Before the checked sizing, overflowing the result Vec raised PanicException.
    PanicException::new_err(error.to_string())
}

#[pyfunction]
pub fn sheet_entries(
    py: Python<'_>,
    rows: Vec<Vec<Py<TimelineCell>>>,
) -> PyResult<Vec<Vec<SheetEntry>>> {
    let rows = snapshot_rows(py, &rows);
    py.detach(|| {
        let (layers, entries) = timeline::sheet(&rows).map_err(timeline_error)?;
        if layers == 0 {
            return Ok((0..rows.len()).map(|_| Vec::new()).collect());
        }
        Ok(entries
            .chunks_exact(layers)
            .map(|row| {
                row.iter()
                    .map(|entry| SheetEntry {
                        kind: entry.kind,
                        number: entry.number,
                        key: entry.key,
                    })
                    .collect()
            })
            .collect())
    })
}

#[pyfunction]
pub fn resolve_timeline<'py>(
    py: Python<'py>,
    rows: Vec<Vec<Py<TimelineCell>>>,
) -> PyResult<Bound<'py, PyBytes>> {
    let rows = snapshot_rows(py, &rows);
    let bytes = py.detach(|| {
        let (_, keys) = timeline::resolve(&rows).map_err(timeline_error)?;
        Ok::<_, PyErr>(
            keys.into_iter()
                .flat_map(i64::to_ne_bytes)
                .collect::<Vec<_>>(),
        )
    })?;
    Ok(PyBytes::new(py, &bytes))
}
