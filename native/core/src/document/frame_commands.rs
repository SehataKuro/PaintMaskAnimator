use std::ops::Range;

use super::changes::{Change, ChangeSet, FrameOperation};
use super::compatibility::{ArchiveHint, LegacyPosition, LegacySlot};
use super::model::{Cell, CellNumber, Document, Position, Reference, TrackId};
use super::timeline::SHEET_EMPTY;
use crate::{image::RgbaImage, Error};

/// Python の apply_sheet_remap に渡す一列分の参照列と素材バンク。
pub struct RemapColumn<'a> {
    /// 対象列の表示インデックス。
    pub layer_index: usize,
    /// 対象範囲の番号列。None は空キー。
    pub states: &'a [Option<CellNumber>],
    /// 1 番からの素材順。None は素材の欠番。
    pub bank: &'a [Option<RgbaImage>],
}

fn invalid() -> Error {
    Error::Invalid("不正なコマまたは列の指定".into())
}

pub(super) fn archive_hint(position: &Position, exposure: usize) -> ArchiveHint {
    ArchiveHint {
        has_content: matches!(position.reference, Reference::Number(_)),
        is_blank_key: position.reference == Reference::Blank,
        exposure,
        pending: position.pending,
        tween: position.tween.clone(),
        tween_member: position.tween_member.clone(),
    }
}

// clone_reference は旧位置の絵を空にする。
pub(super) fn held_reference(
    mut held: Position,
    settings: &super::model::TrackSettings,
    blank: RgbaImage,
) -> Position {
    held.tween = None;
    held.tween_member = None;
    held.blank_start = false;
    // Python の clone_reference は _dense_import を外し、_v2_resolve_source は残す。
    held.compatibility.dense_import = None;
    held.compatibility.legacy = Some(LegacyPosition {
        legacy: Some(LegacySlot {
            settings: settings.clone(),
            state: archive_hint(&Position::default(), 1),
            number: None,
            // Python の clone_reference は旧形式の記録の画像を空画像にする。
            artwork: Some(Cell {
                image: blank,
                initial_image: None,
                cell_name: None,
            }),
        }),
        key_start: false,
        blank_start: false,
        pending: held.pending,
        input_length: None,
    });
    held
}

impl Document {
    pub(super) fn display_spans(
        &self,
        column: usize,
        layers: &[usize],
    ) -> Result<Vec<Option<Range<usize>>>, Error> {
        if layers.iter().any(|&index| index >= self.track_order.len()) {
            return Err(invalid());
        }
        if column >= self.frame_order.len() {
            return Ok(vec![None; layers.len()]);
        }
        let (stride, entries) = self.sheet_entries()?;
        Ok(layers
            .iter()
            .map(|&layer| {
                let entry = &entries[column * stride + layer];
                if entry.kind == SHEET_EMPTY {
                    return None;
                }
                let stop = entries
                    .iter()
                    .skip(column * stride + layer)
                    .step_by(stride)
                    .take_while(|next| next.key == entry.key)
                    .count()
                    + column;
                Some(entry.key as usize..stop)
            })
            .collect())
    }

    pub(super) fn position_mut(&mut self, column: usize, track: TrackId) -> &mut Position {
        // コマと列の表示範囲は呼び出し元で検証し、すべての行は全列を持つ。
        self.sheet
            .get_mut(&self.frame_order[column])
            .and_then(|row| row.positions.get_mut(&track))
            .expect("コマと列が検証済み")
    }

    pub(super) fn cleared_position(
        &self,
        column: usize,
        track: TrackId,
    ) -> Result<Position, Error> {
        let prior = &self.sheet[&self.frame_order[column]].positions[&track];
        let legacy = prior
            .compatibility
            .legacy
            .as_ref()
            .and_then(|position| position.legacy.as_ref());
        let artwork = if let Some(legacy) = legacy.filter(|legacy| !legacy.state.has_content) {
            legacy.artwork.as_ref()
        } else if let Reference::Number(number) = prior.reference {
            self.tracks[&track].cells.get(&number)
        } else if let Some(number) = prior.number().filter(|_| prior.pending) {
            self.tracks[&track].cells.get(&number)
        } else {
            prior.compatibility.local.as_ref()
        };
        // Python の clear_slot は detach_cell で局所の Cell を必ず作り、画像を空にする
        // （初期画像は元の絵のものを残す）。位置をその場で書き換えるので、clear_slot が
        // 外さない印（_pending_detached・_legacy_missing・_v2_length_imported・
        // _v2_resolve_source）は残す。
        let mut cleared = Position::default();
        let kept = &prior.compatibility;
        cleared.compatibility.pending_detached = kept.pending_detached;
        cleared.compatibility.legacy_missing = kept.legacy_missing;
        cleared.compatibility.length_imported = kept.length_imported;
        cleared.compatibility.resolved_source = kept.resolved_source;
        cleared.compatibility.local = Some(Cell {
            image: self.blank_artwork()?,
            initial_image: artwork.and_then(|cell| cell.initial_image.clone()),
            cell_name: None,
        });
        Ok(cleared)
    }

    /// Python の advance_frame と同様に、次のコマへ移動して必要なら伸長する。
    pub fn advance_frame(&mut self) -> Result<ChangeSet, Error> {
        let column = self
            .frame_order
            .iter()
            .position(|&id| id == self.current_frame)
            .ok_or_else(invalid)?
            + 1;
        let mut changes = self.ensure_frame_count(column + 1)?;
        self.current_frame = self.frame_order[column];
        changes.push(Change::Cursor {
            frame: column,
            notify: true,
        });
        Ok(changes)
    }

    /// Python の duplicate_frame と同様に、コマと尺を複製し共有バンクを維持する。
    pub fn duplicate_frame(&mut self, column: usize) -> Result<ChangeSet, Error> {
        let before = self.frame_order.len();
        let source = *self.frame_order.get(column).ok_or_else(invalid)?;
        let layers: Vec<_> = (0..self.track_order.len()).collect();
        let spans = self.display_spans(column, &layers)?;
        let mut edited = self.clone();
        let mut row = self.sheet[&source].clone();
        // Python の clone_slot は局所の Cell を作らず元の位置と共有し、attach_track が
        // 配置待ちの所属を列へ戻す。共有の組が無ければ元と複製で新しい組を作る。
        let mut next_share = edited.next_local_share();
        for (track, position) in row.positions.iter_mut() {
            position.compatibility.pending_detached = false;
            if position.compatibility.local.is_some()
                && position.compatibility.local_share.is_none()
            {
                position.compatibility.local_share = Some(next_share);
                edited
                    .sheet
                    .get_mut(&source)
                    .and_then(|source| source.positions.get_mut(track))
                    .expect("複製元の位置")
                    .compatibility
                    .local_share = Some(next_share);
                next_share += 1;
            }
        }
        edited.ensure_frame_count(before + 1)?;
        // 直前の伸長で追加した行を挿入位置へ移す。
        let id = edited.frame_order.pop().expect("追加済み");
        let at = column + 1;
        edited.frame_order.insert(at, id);
        edited.sheet.insert(id, row.clone());
        for (track, span) in self.track_order.clone().into_iter().zip(spans) {
            if let Some(span) = span.filter(|span| span.start < column) {
                let source = &row.positions[&track];
                let held = held_reference(
                    source.clone(),
                    &self.tracks[&track].settings,
                    self.blank_artwork()?,
                );
                for target in at..span.end {
                    *edited.position_mut(target, track) = held.clone();
                }
                *edited.position_mut(span.end, track) = edited.cleared_position(span.end, track)?;
            }
        }
        edited.drop_single_shares();
        edited.current_frame = id;
        *self = edited;
        Ok(vec![
            Change::FrameStructure {
                operation: FrameOperation::Duplicate,
                before,
                after: before + 1,
                start: at,
            },
            Change::Cursor {
                frame: at,
                notify: true,
            },
        ])
    }

    /// Python の blank_image() と同じ空画像（雛形があれば画素・世代・札を共有する）。
    pub(super) fn blank_artwork(&self) -> Result<RgbaImage, Error> {
        match &self.blank {
            Some(image) if image.width() == self.width && image.height() == self.height => {
                Ok(image.clone())
            }
            _ => RgbaImage::blank(self.width, self.height),
        }
    }

    /// 位置が一つだけになった共有の組を外す（Python では共有していない Cell）。
    pub(super) fn drop_single_shares(&mut self) {
        let mut counts = std::collections::BTreeMap::new();
        for position in self.sheet.values().flat_map(|row| row.positions.values()) {
            if let Some(share) = position.compatibility.local_share {
                *counts.entry(share).or_insert(0usize) += 1;
            }
        }
        for position in self
            .sheet
            .values_mut()
            .flat_map(|row| row.positions.values_mut())
        {
            if let Some(share) = position.compatibility.local_share {
                if counts[&share] == 1 {
                    position.compatibility.local_share = None;
                }
            }
        }
    }

    /// まだ使われていない局所の共有の組の番号。
    pub(super) fn next_local_share(&self) -> u64 {
        self.sheet
            .values()
            .flat_map(|row| row.positions.values())
            .filter_map(|position| position.compatibility.local_share)
            .max()
            .map_or(0, |share| share + 1)
    }

    pub(super) fn trim_unused_tail(&mut self) -> Result<usize, Error> {
        let (stride, entries) = self.sheet_entries()?;
        let after = entries
            .chunks_exact(stride)
            .rposition(|row| row.iter().any(|entry| entry.kind != SHEET_EMPTY))
            .map_or(1, |index| index + 1);
        for id in self.frame_order.drain(after..) {
            self.sheet.remove(&id);
        }
        Ok(after)
    }

    pub(super) fn shift_positions_right(
        &mut self,
        column: usize,
        layer_index: usize,
        amount: usize,
    ) -> Result<(), Error> {
        let track = self.track_order[layer_index];
        let (stride, entries) = self.sheet_entries()?;
        let original: Vec<_> = self.frame_order[column..]
            .iter()
            .map(|id| self.sheet[id].positions[&track].clone())
            .collect();
        let last = original.iter().rposition(|slot| {
            slot.reference != Reference::Empty
                || slot.pending
                || slot.compatibility.unnumbered_content
        });
        let occupied_end = last.map_or(column, |last| column + last + 1);
        let required = occupied_end
            .checked_add(amount)
            .ok_or(Error::TimelineSizeOverflow)?;
        self.ensure_frame_count(required)?;
        let mut legacy: Vec<_> = original
            .iter()
            .map(|slot| slot.compatibility.legacy.clone())
            .chain(std::iter::repeat_n(None, amount))
            .collect();
        for offset in (0..original.len()).rev() {
            let index = column + offset;
            let entry = &entries[index * stride + layer_index];
            if original[offset].pending
                || (entry.kind != SHEET_EMPTY && entry.key as usize == index)
            {
                legacy[offset + amount] = original[offset].compatibility.legacy.clone();
                // 移動元には、移動前のキーを消去した時点の初期画像を残す。
                let cleared = self.cleared_position(index, track)?;
                legacy[offset] = Some(LegacyPosition {
                    legacy: Some(LegacySlot {
                        settings: self.tracks[&track].settings.clone(),
                        state: archive_hint(&cleared, 1),
                        number: None,
                        artwork: cleared.compatibility.local,
                    }),
                    key_start: false,
                    blank_start: false,
                    pending: false,
                    input_length: None,
                });
            }
        }
        for (offset, legacy) in legacy
            .into_iter()
            .enumerate()
            .take(self.frame_order.len() - column)
        {
            let mut position = match offset
                .checked_sub(amount)
                .and_then(|index| original.get(index))
            {
                Some(source) => source.clone(),
                None => self.cleared_position(column + offset, track)?,
            };
            position.compatibility.legacy = legacy;
            position.compatibility.resolved_source = None;
            *self.position_mut(column + offset, track) = position;
        }
        Ok(())
    }

    /// Python の insert_track_frame と同様に、列の途中に一コマ挿入する。
    pub fn insert_track_frame(
        &mut self,
        column: usize,
        layer_index: usize,
    ) -> Result<ChangeSet, Error> {
        let before = self.frame_order.len();
        if column >= before {
            return Err(invalid());
        }
        let span = self.display_spans(column, &[layer_index])?[0].clone();
        let track = self.track_order[layer_index];
        let mut edited = self.clone();
        let held = span.as_ref().filter(|span| span.start < column).map(|_| {
            let mut held = self.sheet[&self.frame_order[column]].positions[&track].clone();
            held.tween = None;
            held.tween_member = None;
            held.blank_start = false;
            held.compatibility.dense_import = None;
            held.compatibility.resolved_source = None;
            held
        });
        edited.ensure_frame_count(before + 1)?;
        edited.shift_positions_right(column, layer_index, 1)?;
        if let Some(mut held) = held {
            let target = edited.position_mut(column, track);
            held.compatibility.legacy = target.compatibility.legacy.take();
            *target = held;
        }
        let after = edited.frame_order.len();
        let changes = vec![
            Change::FrameStructure {
                operation: FrameOperation::Extend,
                before,
                after,
                start: before,
            },
            Change::SheetSpans {
                layer_index,
                start: span.map_or(column, |span| span.start),
                stop: after,
            },
        ];
        *self = edited;
        Ok(changes)
    }

    /// Python の delete_frame と同様に、一列のコマを詰めて全列で未使用の末尾を切る。
    pub fn delete_frame(&mut self, column: usize, layer_index: usize) -> Result<ChangeSet, Error> {
        let before = self.frame_order.len();
        if column >= before {
            return Err(invalid());
        }
        let span = self.display_spans(column, &[layer_index])?.pop().flatten();
        if span.is_none() && before == 1 {
            return Ok(Vec::new());
        }
        // 尺の再解決に失敗しても元の文書を保持する。画像は COW で共有する。
        let mut edited = self.clone();
        let track = edited.track_order[layer_index];
        let start = span.as_ref().map_or(column, |span| span.start);
        if let Some(span) = span {
            let position = &edited.sheet[&edited.frame_order[span.start]].positions[&track];
            if let Reference::Number(number) = position.reference {
                let hint = archive_hint(position, span.len());
                edited
                    .tracks
                    .get_mut(&track)
                    .ok_or_else(invalid)?
                    .legacy_archive_hints
                    .insert(number, hint);
            }
        }
        let last = edited.cleared_position(before - 1, track)?;
        let shifted: Vec<_> = edited.frame_order[column + 1..]
            .iter()
            .map(|frame| edited.sheet[frame].positions[&track].clone())
            .chain(std::iter::once(last))
            .collect();
        for (offset, pos) in shifted.into_iter().enumerate() {
            *edited.position_mut(column + offset, track) = pos;
        }
        if start < column {
            let updated = edited.display_spans(start, &[layer_index])?.pop().flatten();
            let position = edited.position_mut(start, track);
            if let Some((span, legacy)) = updated.zip(
                position
                    .compatibility
                    .legacy
                    .as_mut()
                    .and_then(|pos| pos.legacy.as_mut()),
            ) {
                legacy.state.exposure = span.len();
            }
        }
        let after = edited.trim_unused_tail()?;
        let cursor = column.min(after - 1);
        edited.current_frame = edited.frame_order[cursor];
        let mut changes = Vec::new();
        if after != before {
            changes.push(Change::FrameStructure {
                operation: FrameOperation::Trim,
                before,
                after,
                start: after,
            });
        }
        changes.push(Change::SheetSpans {
            layer_index,
            start,
            stop: before,
        });
        changes.push(Change::Cursor {
            frame: cursor,
            notify: true,
        });
        *self = edited;
        Ok(changes)
    }

    /// Python の apply_sheet_remap に対応し、既存の絵を保ったまま範囲の参照を置換する。
    pub fn apply_sheet_remap(
        &mut self,
        range: Range<usize>,
        columns: &[RemapColumn<'_>],
        template_frame: usize,
    ) -> Result<ChangeSet, Error> {
        let before = self.frame_order.len();
        if range.is_empty() || template_frame >= before.max(range.end) || columns.is_empty() {
            return Err(invalid());
        }
        for column in columns {
            if column.layer_index >= self.track_order.len() || column.states.len() != range.len() {
                return Err(invalid());
            }
            for number in column.states.iter().flatten() {
                let index = usize::try_from(number.get() - 1).map_err(|_| invalid())?;
                if column.bank.get(index).and_then(Option::as_ref).is_none() {
                    return Err(invalid());
                }
            }
        }
        let mut edited = self.clone();
        let mut changes = edited.ensure_frame_count(range.end)?;
        for column in columns {
            let layer_index = column.layer_index;
            let track = edited.track_order[layer_index];
            let span_start = edited.display_spans(range.start, &[layer_index])?[0]
                .as_ref()
                .map_or(range.start, |span| span.start);
            let span_stop = edited.display_spans(range.end - 1, &[layer_index])?[0]
                .as_ref()
                .map_or(range.end, |span| span.end);
            let settings = edited.tracks[&track].settings.clone();
            let empty = Position::default();
            for (offset, &state) in column.states.iter().enumerate() {
                let run_start = offset == 0 || column.states[offset - 1] != state;
                let mut position = Position {
                    reference: state.map_or(Reference::Blank, Reference::Number),
                    blank_start: state.is_none() && run_start,
                    ..Position::default()
                };
                let cleared = edited.cleared_position(range.start + offset, track)?;
                if state.is_none() {
                    position.compatibility.local = cleared.compatibility.local.clone();
                }
                let artwork = match state.filter(|_| run_start) {
                    Some(number) => {
                        let bank = &edited.tracks[&track].cells;
                        if let Some(cell) = bank.get(&number) {
                            Some(cell.clone())
                        } else {
                            let index = usize::try_from(number.get() - 1).map_err(|_| invalid())?;
                            column.bank[index].as_ref().map(|image| Cell {
                                image: image.clone(),
                                initial_image: None,
                                cell_name: None,
                            })
                        }
                    }
                    None => cleared.compatibility.local,
                };
                position.compatibility.legacy = Some(LegacyPosition {
                    legacy: Some(LegacySlot {
                        settings: settings.clone(),
                        state: archive_hint(if run_start { &position } else { &empty }, 1),
                        number: state.filter(|_| run_start),
                        artwork,
                    }),
                    key_start: run_start,
                    blank_start: position.blank_start,
                    pending: false,
                    input_length: None,
                });
                *edited.position_mut(range.start + offset, track) = position;
            }
            let placed: std::collections::BTreeSet<_> = edited
                .frame_order
                .iter()
                .filter_map(|id| {
                    let position = &edited.sheet[id].positions[&track];
                    match position.reference {
                        Reference::Number(number) => Some(number),
                        _ => None,
                    }
                })
                .collect();
            let bank = edited.tracks.get_mut(&track).ok_or_else(invalid)?;
            for (index, image) in column.bank.iter().enumerate() {
                let Some(image) = image else { continue };
                let number = CellNumber::new(i64::try_from(index + 1).map_err(|_| invalid())?)?;
                let is_placed = placed.contains(&number);
                if is_placed || !bank.legacy_archive_hints.contains_key(&number) {
                    bank.cells.entry(number).or_insert_with(|| Cell {
                        image: image.clone(),
                        initial_image: None,
                        cell_name: None,
                    });
                }
                if is_placed {
                    bank.legacy_archive_hints.remove(&number);
                } else {
                    bank.legacy_archive_hints
                        .entry(number)
                        .or_insert_with(|| ArchiveHint {
                            has_content: true,
                            ..archive_hint(&Position::default(), 1)
                        });
                }
            }
            changes.push(Change::SheetSpans {
                layer_index,
                start: span_start,
                stop: span_stop,
            });
        }
        edited.current_frame = edited.frame_order[range.start];
        let layer_index = columns[0].layer_index;
        edited.active_layer = edited.track_order[layer_index];
        changes.push(Change::Cursor {
            frame: range.start,
            notify: true,
        });
        changes.push(Change::Selection { layer_index });
        *self = edited;
        Ok(changes)
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::document::v2;
    use crate::project::payload::decode_v2_plan;

    // tests/test_track_cells_chains.py:test_load_duplicate_split_paint_move_paste_delete_undo_save_reload
    // tests/test_core_document_compare.py:test_legacy_unnumbered_duplicate_and_split
    #[test]
    fn duplicating_and_splitting_a_normalized_legacy_drawing_keeps_reserved_numbers() {
        let input = json!({
            "canvas": {"width": 4, "height": 3},
            "frame_durations": [1, 1, 1, 1],
            "tracks": [
                {"cells": [
                    {"number": 8, "image": "8.png"},
                    {"number": 10, "image": "10.png"},
                    {"number": 11, "image": "11.png", "initial": "initial.png", "cell_name": "legacy"},
                ], "sheet": [11, 11, 11, "BLANK"]},
                {"cells": [], "sheet": [null, null, null, null]},
            ],
        });
        let names = ["8.png", "10.png", "11.png", "initial.png"].map(str::to_owned);
        let plan = decode_v2_plan(&input, &names).unwrap();
        let mut images = vec![RgbaImage::blank(4, 3).unwrap(); plan.images.len()];
        let pixel = [18, 52, 86, 255];
        for (entry, image) in plan.images.iter().zip(&mut images) {
            if entry[0] == "11.png" {
                image
                    .write_rgba(
                        crate::image::Rect {
                            x: 0,
                            y: 0,
                            width: 1,
                            height: 1,
                        },
                        &pixel,
                    )
                    .unwrap();
            }
        }
        let mut doc = v2::from_plan(&input, &plan, &images).unwrap();
        let track = doc.track_order()[0];
        let old = CellNumber::new(11).unwrap();
        let new = CellNumber::new(12).unwrap();
        assert_eq!(doc.placed_numbers(track), [old]);
        doc.duplicate_frame(0).unwrap();
        assert_eq!(doc.display_spans(1, &[0]).unwrap(), [Some(0..4)]);
        assert_eq!(
            doc.split_sheet_clip(2, 0).unwrap(),
            [Change::SheetSpans {
                layer_index: 0,
                start: 0,
                stop: 4
            }],
        );
        let bank = &doc.track(track).unwrap().cells;
        assert_eq!(
            bank.keys().map(|n| n.get()).collect::<Vec<_>>(),
            [8, 10, 11, 12]
        );
        assert_eq!(bank[&new].cell_name, bank[&old].cell_name);
        assert_eq!(
            bank[&new].initial_image.as_ref().unwrap().generation(),
            bank[&old].initial_image.as_ref().unwrap().generation()
        );
        assert_eq!(&bank[&new].image.rgba()[..4], &pixel);
        assert_eq!(bank[&new].image.generation(), bank[&old].image.generation());
        assert_eq!(doc.display_spans(2, &[0]).unwrap(), [Some(2..4)]);
    }

    fn document() -> Document {
        let payload = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1],
            "tracks": [
                {"cells": [{"number": 7, "image": "7.png"}], "sheet": [7, null]},
                {"cells": [], "sheet": [null, null]},
            ],
        });
        let plan = decode_v2_plan(&payload, &["7.png".into()]).unwrap();
        v2::from_plan(&payload, &plan, &[RgbaImage::blank(1, 1).unwrap()]).unwrap()
    }

    // tests/test_document_frame_ops.py:test_duplicate_and_delete_preserve_bank
    #[test]
    fn duplicate_and_delete_preserve_bank() {
        let mut doc = document();
        assert_eq!(
            doc.duplicate_frame(0).unwrap(),
            [
                Change::FrameStructure {
                    operation: FrameOperation::Duplicate,
                    before: 2,
                    after: 3,
                    start: 1
                },
                Change::Cursor {
                    frame: 1,
                    notify: true
                },
            ]
        );
        assert_eq!(
            doc.delete_frame(0, 0).unwrap(),
            [
                Change::FrameStructure {
                    operation: FrameOperation::Trim,
                    before: 3,
                    after: 1,
                    start: 1
                },
                Change::SheetSpans {
                    layer_index: 0,
                    start: 0,
                    stop: 3
                },
                Change::Cursor {
                    frame: 0,
                    notify: true
                },
            ]
        );
        doc.delete_frame(0, 0).unwrap();
        assert_eq!(doc.resolve_sources().unwrap().1, [-1, -1]);
        let track = doc.track_order()[0];
        assert_eq!(
            doc.bank_numbers(track)
                .unwrap()
                .map(CellNumber::get)
                .collect::<Vec<_>>(),
            [7]
        );
        assert!(doc.delete_frame(0, 0).unwrap().is_empty());
    }

    // tests/test_document_frame_ops.py:test_move_selection_deduplicates_holds_and_rejects_invalid_destinations
    #[test]
    fn moving_from_a_hold_moves_the_entire_span_and_retains_the_bank() {
        let mut doc = document();
        doc.duplicate_frame(0).unwrap();
        let before = v2::project(&doc, &json!({})).unwrap().payload;
        assert!(doc.move_sheet_cell((1, 0), (1, 2)).is_err());
        assert_eq!(v2::project(&doc, &json!({})).unwrap().payload, before);
        assert_eq!(
            doc.move_sheet_cell((1, 0), (1, 1)).unwrap(),
            [
                Change::SheetSpans {
                    layer_index: 1,
                    start: 1,
                    stop: 3
                },
                Change::SheetSpans {
                    layer_index: 0,
                    start: 0,
                    stop: 2
                },
                Change::Cursor {
                    frame: 1,
                    notify: true
                },
                Change::Selection { layer_index: 1 },
            ]
        );
        let saved = v2::project(&doc, &json!({})).unwrap();
        assert_eq!(
            saved.payload["tracks"][0]["sheet"],
            json!([null, null, null])
        );
        assert_eq!(saved.payload["tracks"][1]["sheet"], json!([null, 7, 7]));
        assert_eq!(doc.next_drawing_number(0).unwrap().get(), 8);
    }

    // tests/test_dense_timeline_edits.py:test_insert_hold_delete_and_undo
    #[test]
    fn inserting_and_deleting_within_a_hold_changes_its_visible_length() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1, 1, 1],
            "tracks": [
                {"cells": [{"number": 1, "image": "1.png"}], "sheet": [1, 1, 1, null]},
                {"cells": [{"number": 2, "image": "2.png"}], "sheet": [2, 2, 2, 2]},
            ],
        });
        let names = ["1.png", "2.png"].map(str::to_owned);
        let plan = decode_v2_plan(&input, &names).unwrap();
        let images = vec![RgbaImage::blank(1, 1).unwrap(); plan.images.len()];
        let mut doc = v2::from_plan(&input, &plan, &images).unwrap();
        assert_eq!(doc.display_spans(1, &[0]).unwrap(), [Some(0..3)]);
        doc.insert_track_frame(1, 0).unwrap();
        assert_eq!(doc.display_spans(1, &[0]).unwrap(), [Some(0..4)]);
        doc.delete_frame(1, 0).unwrap();
        assert_eq!(doc.display_spans(1, &[0]).unwrap(), [Some(0..3)]);
        for number in 1..=2 {
            let track = doc.track_order()[number - 1];
            assert_eq!(
                doc.placed_numbers(track),
                [CellNumber::new(number as i64).unwrap()]
            );
        }
    }

    // tests/test_dense_timeline_edits.py:test_insert_blank_middle_preserves_start
    // tests/test_dense_document_boundary.py:test_adjacent_content_merges_but_blank_starts_remain
    #[test]
    fn inserting_inside_a_blank_does_not_move_its_start_or_merge_the_next_blank() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1, 1, 1],
            "tracks": [{
                "cells": [],
                "sheet": ["BLANK", "BLANK", "BLANK", "BLANK"],
                "positions": [{"column": 0, "blank_start": true}, {"column": 3, "blank_start": true}],
            }],
        });
        let plan = decode_v2_plan(&input, &[]).unwrap();
        let mut doc = v2::from_plan(&input, &plan, &[]).unwrap();
        doc.insert_track_frame(1, 0).unwrap();
        let (_, entries) = doc.sheet_entries().unwrap();
        assert_eq!(
            entries.iter().map(|entry| entry.key).collect::<Vec<_>>(),
            [0, 0, 0, 0, 4]
        );
        assert_eq!(
            v2::project(&doc, &input).unwrap().payload["tracks"][0]["positions"],
            json!([{"column": 0, "blank_start": true}, {"column": 4, "blank_start": true}])
        );
    }

    // Python Document._keep_unplaced_in_archive は、既存ヒントがあれば素材を保管し直さない。
    // tests/test_time_remap_archive.py:test_applying_a_remap_uses_archived_numbers_and_keeps_unused_ones
    #[test]
    fn remap_only_fills_a_hinted_number_when_it_is_used() {
        let mut doc = document();
        let track = doc.track_order[0];
        let number = CellNumber::new(1).unwrap();
        let bank = [Some(RgbaImage::blank(1, 1).unwrap())];
        doc.tracks
            .get_mut(&track)
            .unwrap()
            .legacy_archive_hints
            .insert(number, archive_hint(&Position::default(), 2));
        doc.apply_sheet_remap(
            1..2,
            &[RemapColumn {
                layer_index: 0,
                states: &[None],
                bank: &bank,
            }],
            1,
        )
        .unwrap();
        assert!(!doc.tracks[&track].cells.contains_key(&number));
        assert_eq!(doc.tracks[&track].legacy_archive_hints[&number].exposure, 2);
        doc.apply_sheet_remap(
            1..2,
            &[RemapColumn {
                layer_index: 0,
                states: &[Some(number)],
                bank: &bank,
            }],
            1,
        )
        .unwrap();
        assert!(doc.tracks[&track].cells.contains_key(&number));
        assert!(!doc.tracks[&track]
            .legacy_archive_hints
            .contains_key(&number));
    }

    // tests/test_core_document_compare.py:test_invalid_clear_does_not_change_document
    #[test]
    fn invalid_clear_rolls_back_the_comparison_batch() {
        let mut doc = document();
        let before = v2::project(&doc, &json!({})).unwrap();
        let selection = doc.selection();
        let resolved = doc.resolve_sources().unwrap();
        let operations = json!([["ensure_frame_count", 4], ["clear_sheet_span", 0, 1, 1]]);
        assert!(crate::document::comparison::apply_operations(&mut doc, &operations).is_err());
        let after = v2::project(&doc, &json!({})).unwrap();
        assert_eq!(after.payload, before.payload);
        assert_eq!(after.pngs.len(), before.pngs.len());
        for (after, before) in after.pngs.iter().zip(&before.pngs) {
            assert_eq!(after.path, before.path);
            assert_eq!(after.image.generation(), before.image.generation());
        }
        assert_eq!(doc.selection(), selection);
        assert_eq!(doc.resolve_sources().unwrap(), resolved);
    }

    // tests/test_document_frame_ops.py:test_remap_validates_every_column_before_growing_or_changing_the_sheet
    // tests/test_document_frame_ops.py:test_remap_accepts_a_template_frame_past_the_end_inside_the_range
    #[test]
    fn remap_is_atomic_and_keeps_existing_artwork() {
        let mut doc = document();
        let image = RgbaImage::blank(1, 1).unwrap();
        let bank = [Some(image.clone())];
        let one = CellNumber::new(1).unwrap();
        let two = CellNumber::new(2).unwrap();
        let before = v2::project(&doc, &json!({})).unwrap().payload;
        let good_states = [Some(one), Some(one), None];
        let good = || RemapColumn {
            layer_index: 0,
            states: &good_states,
            bank: &bank,
        };
        let states = [Some(two), None, None];
        let invalid = RemapColumn {
            layer_index: 1,
            states: &states,
            bank: &bank,
        };
        assert!(doc.apply_sheet_remap(0..3, &[good(), invalid], 2).is_err());
        assert_eq!(doc.frame_order().len(), 2);
        assert_eq!(v2::project(&doc, &json!({})).unwrap().payload, before);
        let mut sources = vec![None; 7];
        sources[0] = Some(image);
        sources[6] = Some(RgbaImage::from_rgba(1, 1, &[9, 3, 1, 255]).unwrap());
        let original = doc.track(doc.track_order()[0]).unwrap().cells[&CellNumber::new(7).unwrap()]
            .image
            .generation();
        assert_eq!(
            doc.apply_sheet_remap(
                0..3,
                &[RemapColumn {
                    bank: &sources,
                    ..good()
                }],
                2
            )
            .unwrap(),
            [
                Change::FrameStructure {
                    operation: FrameOperation::Extend,
                    before: 2,
                    after: 3,
                    start: 2
                },
                Change::SheetSpans {
                    layer_index: 0,
                    start: 0,
                    stop: 3
                },
                Change::Cursor {
                    frame: 0,
                    notify: true
                },
                Change::Selection { layer_index: 0 },
            ]
        );
        let id = doc.track_order()[0];
        assert_eq!(doc.placed_numbers(id), [one]);
        assert_eq!(
            doc.track(id).unwrap().cells[&CellNumber::new(7).unwrap()]
                .image
                .generation(),
            original
        );
        assert_eq!(doc.resolve_sources().unwrap().1, [0, -1, 0, -1, -1, -1]);
    }
}
