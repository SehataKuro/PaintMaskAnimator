//! Timeline metadata and exposure resolution, independent of Qt and image storage.
use pyo3::prelude::*;
use pyo3::types::PyBytes;

#[pyclass(get_all, set_all, module = "pma_native")]
#[derive(Clone, Default)]
pub struct TimelineCell {
    pub has_content: bool,
    pub is_blank_key: bool,
    pub exposure: i64,
    pub sequence_number: Option<i64>,
    pub sequence_only: bool,
    pub cell_name: Option<String>,
}

#[pymethods]
impl TimelineCell {
    #[new]
    fn new() -> Self {
        Self { exposure: 1, ..Self::default() }
    }
}

/// A metadata snapshot: image payloads never cross this boundary.
pub fn resolve(rows: &[Vec<TimelineCell>]) -> (usize, Vec<i64>) {
    let layers = rows.iter().map(Vec::len).max().unwrap_or(0);
    let mut keys = vec![-1; rows.len() * layers];
    for layer in 0..layers {
        let (mut key, mut end) = (-1, 0usize);
        for (frame, row) in rows.iter().enumerate() {
            if let Some(cell) = row.get(layer) {
                if cell.has_content {
                    key = frame as i64;
                    end = frame.saturating_add(cell.exposure.max(1) as usize);
                } else if cell.is_blank_key {
                    key = -1;
                    end = 0;
                }
            }
            if frame < end {
                keys[frame * layers + layer] = key;
            }
        }
    }
    (layers, keys)
}

#[pyfunction]
pub fn resolve_timeline<'py>(
    py: Python<'py>, rows: Vec<Vec<Py<TimelineCell>>>,
) -> Bound<'py, PyBytes> {
    // Snapshot under the GIL, then resolve without Python access. Layer objects
    // remain the sole owners of mutable metadata; this is only a read snapshot.
    let rows: Vec<Vec<_>> = rows.iter().map(|row| {
        row.iter().map(|cell| cell.borrow(py).clone()).collect()
    }).collect();
    let bytes = py.detach(|| {
        resolve(&rows).1.iter().flat_map(|key| key.to_ne_bytes()).collect::<Vec<_>>()
    });
    PyBytes::new(py, &bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blank_missing_and_overlapping_cells() {
        let content = |exposure| TimelineCell {
            has_content: true, exposure, ..TimelineCell::default()
        };
        let rows = vec![
            vec![content(9), content(0)],
            vec![],
            vec![TimelineCell { is_blank_key: true, ..TimelineCell::default() }],
            vec![content(2)],
            vec![content(1)],
            vec![],
        ];
        assert_eq!(resolve(&rows), (2, vec![0, 0, 0, -1, -1, -1, 3, -1, 4, -1, -1, -1]));
        assert_eq!(resolve(&[]), (0, vec![]));
    }
}
