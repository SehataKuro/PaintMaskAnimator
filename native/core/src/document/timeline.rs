//! 密なコマ列から表示用シートと画像参照を解決する。

use crate::Error;

/// タイムライン上の一枠のメタデータ。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct TimelineCell {
    /// 絵が置かれている。
    pub has_content: bool,
    /// 空キー（○）の区間に含まれる。
    pub is_blank_key: bool,
    /// 空キー区間の開始を明示する。
    pub blank_start: bool,
    /// 絵または枠に保持されている連番。
    pub sequence_number: Option<i64>,
    /// 互換入力の配置待ちを示す。投影に使う値は呼び出し元が決める。
    pub sequence_only: bool,
}

/// 未使用の枠を示す種別。
pub const SHEET_EMPTY: u8 = 0;
/// 空キー（○）を示す種別。
pub const SHEET_BLANK: u8 = 1;
/// 絵を参照する枠を示す種別。
pub const SHEET_CELL: u8 = 2;

/// 表示用の枠を解決した結果。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SheetEntry {
    /// 未使用、空キー、または絵の種別。
    pub kind: u8,
    /// 絵の連番。未使用と空キーでは None。
    pub number: Option<i64>,
    /// 連続区間の開始コマ（0 始まり）。未使用では -1。
    pub key: i64,
}

fn output_len<T>(frames: usize, layers: usize) -> Result<usize, Error> {
    frames
        .checked_mul(layers)
        .filter(|&len| len <= isize::MAX as usize / size_of::<T>())
        .ok_or(Error::TimelineSizeOverflow)
}

/// 表示用の密なコマ列から、列数とコマ優先順のシート項目を返す。
/// 行にない枠は未使用として扱う。配置待ちの表示は呼び出し元で解決する。
/// 出力寸法がバッファの上限を超える場合はエラーを返す。
pub fn sheet(rows: &[Vec<TimelineCell>]) -> Result<(usize, Vec<SheetEntry>), Error> {
    let layers = rows.iter().map(Vec::len).max().unwrap_or(0);
    let empty = SheetEntry {
        kind: SHEET_EMPTY,
        number: None,
        key: -1,
    };
    let mut entries = vec![empty.clone(); output_len::<SheetEntry>(rows.len(), layers)?];
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
    Ok((layers, entries))
}

/// 画像解決用の密なコマ列から、列数とコマ優先順の参照元コマ番号を返す。
/// 空キー・未使用・行にない枠は -1 となり、連続区間を区切る。
/// 旧形式の配置待ちによって、入力は表示用シートと異なることがある。
/// 出力寸法がバッファの上限を超える場合はエラーを返す。
pub fn resolve(rows: &[Vec<TimelineCell>]) -> Result<(usize, Vec<i64>), Error> {
    let layers = rows.iter().map(Vec::len).max().unwrap_or(0);
    let mut keys = vec![-1; output_len::<i64>(rows.len(), layers)?];
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
    Ok((layers, keys))
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
    fn dense_references_and_separate_blank_starts() {
        let rows = vec![
            vec![drawing(1)],
            vec![drawing(1)],
            vec![TimelineCell::default()],
            vec![drawing(1)],
            vec![blank(true)],
            vec![blank(false)],
            vec![blank(true)],
            vec![drawing(2)],
        ];
        let (layers, entries) = sheet(&rows).unwrap();
        assert_eq!(layers, 1);
        assert_eq!(
            entries
                .iter()
                .map(|e| (e.kind, e.number, e.key))
                .collect::<Vec<_>>(),
            vec![
                (SHEET_CELL, Some(1), 0),
                (SHEET_CELL, Some(1), 0),
                (SHEET_EMPTY, None, -1),
                (SHEET_CELL, Some(1), 3),
                (SHEET_BLANK, None, 4),
                (SHEET_BLANK, None, 4),
                (SHEET_BLANK, None, 6),
                (SHEET_CELL, Some(2), 7),
            ]
        );
        assert_eq!(
            resolve(&rows).unwrap(),
            (1, vec![0, 0, -1, 3, -1, -1, -1, 7])
        );
    }

    #[test]
    fn visible_length_comes_from_the_supplied_positions() {
        // Visible projection of the legacy exposure=20, pending=99 fixture.
        let rows = vec![
            vec![drawing(1)],
            vec![drawing(1)],
            vec![drawing(1)],
            vec![blank(true)],
            vec![TimelineCell::default()],
        ];
        let (_, entries) = sheet(&rows).unwrap();
        assert_eq!(
            entries.iter().map(|e| (e.kind, e.key)).collect::<Vec<_>>(),
            vec![
                (SHEET_CELL, 0),
                (SHEET_CELL, 0),
                (SHEET_CELL, 0),
                (SHEET_BLANK, 3),
                (SHEET_EMPTY, -1)
            ]
        );
    }

    #[test]
    fn tween_positions_keep_the_same_drawing_key() {
        // The Python fixture projects a drawing, a tween and a tween member.
        let rows = vec![vec![drawing(1)]; 3];
        assert_eq!(resolve(&rows).unwrap(), (1, vec![0, 0, 0]));
        let (_, entries) = sheet(&rows).unwrap();
        assert!(entries.iter().all(|entry| entry.key == 0));
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
            sheet(&rows)
                .unwrap()
                .1
                .iter()
                .map(|e| e.key)
                .collect::<Vec<_>>(),
            vec![0, 0, 0, 0, 4, 4, 6, 6, 8, 9]
        );
        assert_eq!(
            resolve(&rows).unwrap().1,
            vec![0, 0, 0, 0, -1, -1, -1, -1, 8, 9]
        );
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
            resolve(&rows).unwrap(),
            (2, vec![0, 0, 0, -1, -1, -1, 3, -1, -1, -1])
        );
        let (layers, entries) = sheet(&rows).unwrap();
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
        assert_eq!(resolve(&visible).unwrap().1, vec![0, 0, 0]);
        assert_eq!(resolve(&resolved).unwrap().1, vec![0, 1, -1]);
        // Pending is not reinterpreted as EMPTY or an old exposure key in Rust.
        assert_eq!(sheet(&resolved).unwrap().1[1].number, Some(99));
        assert_eq!(sheet(&visible).unwrap().1[1].number, Some(7));
    }

    #[test]
    fn content_wins_over_blank_flags() {
        let mut cell = drawing(1);
        cell.is_blank_key = true;
        cell.blank_start = true;
        let rows = vec![vec![drawing(1)], vec![cell]];
        assert_eq!(resolve(&rows).unwrap().1, vec![0, 0]);
        assert_eq!(sheet(&rows).unwrap().1[1].kind, SHEET_CELL);
        assert_eq!(sheet(&rows).unwrap().1[1].key, 0);
    }

    #[test]
    fn ragged_rows_do_not_inherit_previous_references() {
        let rows = vec![
            vec![drawing(1), drawing(2)],
            vec![],
            vec![drawing(1)],
            vec![drawing(1), drawing(2)],
        ];
        let expected = vec![0, 0, -1, -1, 2, -1, 2, 3];
        let (layers, entries) = sheet(&rows).unwrap();
        assert_eq!(layers, 2);
        assert_eq!(entries.iter().map(|e| e.key).collect::<Vec<_>>(), expected);
        assert_eq!(resolve(&rows).unwrap(), (2, expected));
    }

    #[test]
    fn oversized_output_is_rejected_before_allocation() {
        assert!(matches!(
            output_len::<SheetEntry>(usize::MAX, 2),
            Err(Error::TimelineSizeOverflow)
        ));
        assert!(matches!(
            output_len::<i64>(isize::MAX as usize / size_of::<i64>() + 1, 1),
            Err(Error::TimelineSizeOverflow)
        ));
    }

    #[test]
    fn empty_input_and_zero_columns() {
        assert_eq!(sheet(&[]).unwrap(), (0, vec![]));
        assert_eq!(sheet(&[vec![], vec![]]).unwrap(), (0, vec![]));
        assert_eq!(resolve(&[]).unwrap(), (0, vec![]));
        assert_eq!(resolve(&[vec![], vec![]]).unwrap(), (0, vec![]));
    }
}
