//! Dense timeline references, independent of Qt and image storage.
use pyo3::prelude::*;
use pyo3::types::PyBytes;

#[pyclass(get_all, set_all, module = "pma_native")]
#[derive(Clone, Default)]
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

/// Read a dense visible snapshot. Missing positions are EMPTY, never holds.
/// Pending visibility is resolved by the caller before crossing this boundary.
pub fn sheet(rows: &[Vec<TimelineCell>]) -> (usize, Vec<SheetEntry>) {
    let layers = rows.iter().map(Vec::len).max().unwrap_or(0);
    let empty = SheetEntry {
        kind: SHEET_EMPTY,
        number: None,
        key: -1,
    };
    let mut entries = vec![empty.clone(); rows.len() * layers];
    for layer in 0..layers {
        let mut previous = empty.clone();
        for (frame, row) in rows.iter().enumerate() {
            let current = match row.get(layer) {
                Some(cell) if cell.has_content => SheetEntry {
                    kind: SHEET_CELL,
                    number: cell.sequence_number,
                    key: if previous.kind == SHEET_CELL && previous.number == cell.sequence_number {
                        previous.key
                    } else {
                        frame as i64
                    },
                },
                Some(cell) if cell.is_blank_key => SheetEntry {
                    kind: SHEET_BLANK,
                    number: None,
                    key: if previous.kind == SHEET_BLANK && !cell.blank_start {
                        previous.key
                    } else {
                        frame as i64
                    },
                },
                _ => empty.clone(),
            };
            entries[frame * layers + layer] = current.clone();
            previous = current;
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

/// Resolve a dense image-reference snapshot, which can differ from the sheet
/// snapshot for legacy pending positions. No implicit hold crosses an empty slot.
pub fn resolve(rows: &[Vec<TimelineCell>]) -> (usize, Vec<i64>) {
    let layers = rows.iter().map(Vec::len).max().unwrap_or(0);
    let mut keys = vec![-1; rows.len() * layers];
    for layer in 0..layers {
        let mut previous_number = None;
        let mut key = -1;
        for (frame, row) in rows.iter().enumerate() {
            match row.get(layer) {
                Some(cell) if cell.has_content => {
                    if key == -1 || previous_number != cell.sequence_number {
                        key = frame as i64;
                    }
                    previous_number = cell.sequence_number;
                    keys[frame * layers + layer] = key;
                }
                _ => {
                    previous_number = None;
                    key = -1;
                }
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
    // Snapshot under the GIL; no images or Python access in the computation.
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

    fn drawing(number: i64) -> TimelineCell {
        TimelineCell {
            has_content: true,
            sequence_number: Some(number),
            ..TimelineCell::default()
        }
    }
    fn blank(start: bool) -> TimelineCell {
        // Retained numbers do not turn a blank into a drawing.
        TimelineCell {
            is_blank_key: true,
            blank_start: start,
            sequence_number: Some(99),
            ..TimelineCell::default()
        }
    }

    #[test]
    fn dense_runs_stop_at_empty_blank_and_different_numbers() {
        let rows = vec![
            vec![drawing(1)],
            vec![drawing(1)],
            vec![TimelineCell::default()],
            vec![drawing(1)],
            vec![blank(true)],
            vec![blank(false)],
            vec![drawing(2)],
        ];
        let (_, entries) = sheet(&rows);
        assert_eq!(
            entries.iter().map(|e| e.kind).collect::<Vec<_>>(),
            vec![
                SHEET_CELL,
                SHEET_CELL,
                SHEET_EMPTY,
                SHEET_CELL,
                SHEET_BLANK,
                SHEET_BLANK,
                SHEET_CELL
            ]
        );
        assert_eq!(
            entries.iter().map(|e| e.key).collect::<Vec<_>>(),
            vec![0, 0, -1, 3, 4, 4, 6]
        );
        assert_eq!(resolve(&rows), (1, vec![0, 0, -1, 3, -1, -1, 6]));
        assert_eq!(entries[4].number, None);
        assert_eq!(entries[6].number, Some(2));
    }

    #[test]
    fn adjacent_same_numbers_merge_but_blank_starts_remain_separate() {
        let rows = vec![
            vec![drawing(1)],
            vec![drawing(1)],
            vec![drawing(1)],
            vec![drawing(1)],
            vec![blank(true)],
            vec![blank(false)],
            vec![blank(true)],
            vec![blank(false)],
            vec![drawing(2)],
            vec![drawing(3)],
        ];
        assert_eq!(
            sheet(&rows).1.iter().map(|e| e.key).collect::<Vec<_>>(),
            vec![0, 0, 0, 0, 4, 4, 6, 6, 8, 9]
        );
        assert_eq!(resolve(&rows).1, vec![0, 0, 0, 0, -1, -1, -1, -1, 8, 9]);
    }

    #[test]
    fn ragged_rows_and_retained_empty_numbers_never_hold() {
        let rows = vec![
            vec![drawing(7), drawing(8)],
            vec![drawing(7)],
            vec![],
            vec![
                drawing(7),
                TimelineCell {
                    sequence_number: Some(8),
                    ..TimelineCell::default()
                },
            ],
            vec![blank(false), blank(false)],
        ];
        assert_eq!(
            resolve(&rows),
            (2, vec![0, 0, 0, -1, -1, -1, 3, -1, -1, -1])
        );
        let (layers, entries) = sheet(&rows);
        assert_eq!(layers, 2);
        assert_eq!(entries[4].kind, SHEET_EMPTY);
        assert_eq!(entries[7].kind, SHEET_EMPTY);
        assert_eq!(entries[8].key, 4);
        assert_eq!(entries[9].key, 4);
    }

    #[test]
    fn pending_visibility_is_supplied_as_two_independent_snapshots() {
        let visible = vec![vec![drawing(7)], vec![drawing(7)], vec![drawing(7)]];
        let mut pending = drawing(99);
        pending.sequence_only = true;
        let resolved = vec![
            vec![drawing(7)],
            vec![pending],
            vec![TimelineCell::default()],
        ];
        assert_eq!(resolve(&visible).1, vec![0, 0, 0]);
        assert_eq!(resolve(&resolved).1, vec![0, 1, -1]);
        // Pending is not reinterpreted as EMPTY or an old exposure key in Rust.
        assert_eq!(sheet(&resolved).1[1].number, Some(99));
        assert_eq!(sheet(&visible).1[1].number, Some(7));
    }

    #[test]
    fn content_wins_over_blank_flags() {
        let mut cell = drawing(1);
        cell.is_blank_key = true;
        cell.blank_start = true;
        let rows = vec![vec![drawing(1)], vec![cell]];
        assert_eq!(resolve(&rows).1, vec![0, 0]);
        assert_eq!(sheet(&rows).1[1].kind, SHEET_CELL);
        assert_eq!(sheet(&rows).1[1].key, 0);
    }

    #[test]
    fn empty_input_and_zero_columns() {
        assert_eq!(sheet(&[]), (0, vec![]));
        assert_eq!(sheet(&[vec![], vec![]]), (0, vec![]));
        assert_eq!(resolve(&[]), (0, vec![]));
        assert_eq!(resolve(&[vec![], vec![]]), (0, vec![]));
    }
}
