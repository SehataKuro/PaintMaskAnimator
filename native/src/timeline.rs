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
        Self {
            exposure: 1,
            ..Self::default()
        }
    }
}

pub const SHEET_EMPTY: u8 = 0;
pub const SHEET_BLANK: u8 = 1;
pub const SHEET_CELL: u8 = 2;

#[pyclass(get_all, module = "pma_native")]
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SheetEntry {
    pub kind: u8,
    pub number: Option<i64>,
    pub key: i64,
}

/// Read the visible sheet, ignoring sequence-only storage cells.
pub fn sheet(rows: &[Vec<TimelineCell>]) -> (usize, Vec<SheetEntry>) {
    let layers = rows.iter().map(Vec::len).max().unwrap_or(0);
    let empty = SheetEntry {
        kind: SHEET_EMPTY,
        number: None,
        key: -1,
    };
    let mut entries = vec![empty.clone(); rows.len() * layers];
    for layer in 0..layers {
        let mut current = empty.clone();
        let mut end = 0usize;
        for (frame, row) in rows.iter().enumerate() {
            if let Some(cell) = row.get(layer) {
                if !cell.sequence_only && (cell.has_content || cell.is_blank_key) {
                    current = SheetEntry {
                        kind: if cell.has_content {
                            SHEET_CELL
                        } else {
                            SHEET_BLANK
                        },
                        number: if cell.has_content {
                            cell.sequence_number
                        } else {
                            None
                        },
                        key: frame as i64,
                    };
                    end = frame.saturating_add(cell.exposure.max(1) as usize);
                }
            }
            if frame < end {
                entries[frame * layers + layer] = current.clone();
            }
        }
    }
    (layers, entries)
}

#[pyfunction]
pub fn sheet_entries(py: Python<'_>, rows: Vec<Vec<Py<TimelineCell>>>) -> Vec<Vec<SheetEntry>> {
    let rows: Vec<Vec<_>> = rows
        .iter()
        .map(|row| row.iter().map(|cell| cell.borrow(py).clone()).collect())
        .collect();
    py.detach(|| {
        let (layers, entries) = sheet(&rows);
        if layers == 0 {
            return vec![vec![]; rows.len()];
        }
        entries.chunks(layers).map(|row| row.to_vec()).collect()
    })
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
    py: Python<'py>,
    rows: Vec<Vec<Py<TimelineCell>>>,
) -> Bound<'py, PyBytes> {
    // Snapshot under the GIL, then resolve without Python access. Layer objects
    // remain the sole owners of mutable metadata; this is only a read snapshot.
    let rows: Vec<Vec<_>> = rows
        .iter()
        .map(|row| row.iter().map(|cell| cell.borrow(py).clone()).collect())
        .collect();
    let bytes = py.detach(|| {
        resolve(&rows)
            .1
            .iter()
            .flat_map(|key| key.to_ne_bytes())
            .collect::<Vec<_>>()
    });
    PyBytes::new(py, &bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn drawing(exposure: i64, number: Option<i64>) -> TimelineCell {
        TimelineCell {
            has_content: true,
            exposure,
            sequence_number: number,
            ..TimelineCell::default()
        }
    }

    #[test]
    fn sheet_ignores_sequence_only_and_holds_across_missing_layers() {
        let mut archived = drawing(1, Some(99));
        archived.sequence_only = true;
        archived.is_blank_key = true;
        let rows = vec![
            vec![drawing(4, Some(7)), drawing(0, None)],
            vec![archived],
            vec![],
            vec![TimelineCell::default()],
            vec![],
        ];
        let (layers, entries) = sheet(&rows);
        assert_eq!(layers, 2);
        for frame in 0..4 {
            assert_eq!(
                entries[frame * layers],
                SheetEntry {
                    kind: SHEET_CELL,
                    number: Some(7),
                    key: 0
                }
            );
        }
        assert_eq!(
            entries[1],
            SheetEntry {
                kind: SHEET_CELL,
                number: None,
                key: 0
            }
        );
        assert_eq!(entries[3].kind, SHEET_EMPTY);
        assert_eq!(
            entries[8],
            SheetEntry {
                kind: SHEET_EMPTY,
                number: None,
                key: -1
            }
        );
    }

    #[test]
    fn sheet_blank_exposure_and_new_keys_cut_previous_exposure() {
        let rows = vec![
            vec![drawing(20, Some(1))],
            vec![TimelineCell {
                is_blank_key: true,
                exposure: 2,
                sequence_number: Some(99),
                ..TimelineCell::default()
            }],
            vec![],
            vec![],
            vec![drawing(-3, None)],
            vec![],
        ];
        let (_, entries) = sheet(&rows);
        assert_eq!(
            entries.iter().map(|entry| entry.kind).collect::<Vec<_>>(),
            vec![
                SHEET_CELL,
                SHEET_BLANK,
                SHEET_BLANK,
                SHEET_EMPTY,
                SHEET_CELL,
                SHEET_EMPTY
            ]
        );
        assert_eq!(
            entries[1],
            SheetEntry {
                kind: SHEET_BLANK,
                number: None,
                key: 1
            }
        );
        assert_eq!(entries[2], entries[1]);
        assert_eq!(entries[4].number, None);
        let (_, entries) = sheet(&[
            vec![drawing(20, Some(1))],
            vec![drawing(1, Some(2))],
            vec![],
        ]);
        assert_eq!(entries[2].kind, SHEET_EMPTY);
    }

    #[test]
    fn sheet_empty_input() {
        assert_eq!(sheet(&[]), (0, vec![]));
        assert_eq!(sheet(&[vec![], vec![]]), (0, vec![]));
    }

    #[test]
    fn blank_missing_and_overlapping_cells() {
        let content = |exposure| TimelineCell {
            has_content: true,
            exposure,
            ..TimelineCell::default()
        };
        let rows = vec![
            vec![content(9), content(0)],
            vec![],
            vec![TimelineCell {
                is_blank_key: true,
                ..TimelineCell::default()
            }],
            vec![content(2)],
            vec![content(1)],
            vec![],
        ];
        assert_eq!(
            resolve(&rows),
            (2, vec![0, 0, 0, -1, -1, -1, 3, -1, 4, -1, -1, -1])
        );
        assert_eq!(resolve(&[]), (0, vec![]));
    }
}
