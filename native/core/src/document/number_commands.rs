use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

use super::changes::{Change, ChangeSet, FrameOperation};
use super::compatibility::{LegacyPosition, LegacySlot};
use super::frame_commands::{archive_hint, held_reference};
use super::model::{Cell, CellNumber, Document, Position, Reference, TrackId};
use crate::{image::RgbaImage, Error};

fn invalid() -> Error {
    Error::Invalid("列またはセル番号の指定が不正です".into())
}

impl Document {
    fn apply_number_mapping(&mut self, track: TrackId, mapping: &BTreeMap<CellNumber, CellNumber>) {
        let mapped = |number: CellNumber| mapping.get(&number).copied().unwrap_or(number);
        // 呼び出し元で表示順から引いた列は、バンクとすべての行に存在する。
        let bank = self.tracks.get_mut(&track).unwrap();
        bank.cells = std::mem::take(&mut bank.cells)
            .into_iter()
            .map(|(number, cell)| (mapped(number), cell))
            .collect();
        bank.legacy_archive_hints = std::mem::take(&mut bank.legacy_archive_hints)
            .into_iter()
            .map(|(number, hint)| (mapped(number), hint))
            .collect();
        for row in self.sheet.values_mut() {
            let position = Arc::make_mut(row).positions.get_mut(&track).unwrap();
            if let Reference::Number(number) = &mut position.reference {
                *number = mapped(*number);
            }
            position.compatibility.retained_number =
                position.compatibility.retained_number.map(mapped);
            if let Some(legacy) = position
                .compatibility
                .legacy
                .as_mut()
                .and_then(|pos| pos.legacy.as_mut())
            {
                legacy.number = legacy.number.map(mapped);
            }
            // 取り込み時の署名は更新しない。変更された番号はライブ位置を使用する。
        }
    }

    /// Python の normalize_sheet_numbers と同様に、シート初出順の後に未配置番号を詰める。
    pub fn normalize_sheet_numbers(&mut self, layers: &[usize]) -> Result<ChangeSet, Error> {
        if layers.iter().any(|&layer| layer >= self.track_order.len()) {
            return Err(invalid());
        }
        let mut edited = self.clone();
        let mut changes = Vec::new();
        for &layer_index in layers {
            let track = edited.track_order[layer_index];
            let mut all: BTreeSet<_> = edited.tracks[&track].cells.keys().copied().collect();
            let mut placed = Vec::new();
            let mut seen = BTreeSet::new();
            for id in &edited.frame_order {
                let position = &edited.sheet[id].positions[&track];
                if let Some(number) = position.number() {
                    all.insert(number);
                    if !position.pending
                        && matches!(position.reference, Reference::Number(_))
                        && seen.insert(number)
                    {
                        placed.push(number);
                    }
                }
            }
            let mapping = placed
                .into_iter()
                .chain(all.difference(&seen).copied())
                .enumerate()
                .map(|(index, old)| {
                    Ok((
                        old,
                        CellNumber::new(i64::try_from(index + 1).map_err(|_| invalid())?)?,
                    ))
                })
                .collect::<Result<BTreeMap<_, _>, Error>>()?;
            edited.apply_number_mapping(track, &mapping);
            let mut last = mapping.len();
            for frame in 0..edited.frame_order.len() {
                let position = &edited.sheet[&edited.frame_order[frame]].positions[&track];
                if position.pending
                    || !position.compatibility.unnumbered_content
                    || position.number().is_some()
                {
                    continue;
                }
                last = last.checked_add(1).ok_or_else(invalid)?;
                let number = CellNumber::new(i64::try_from(last).map_err(|_| invalid())?)?;
                let position = edited.position_mut(frame, track);
                let local = position.compatibility.local.take();
                position.reference = Reference::Number(number);
                position.compatibility.unnumbered_content = false;
                position.compatibility.dense_import = None;
                position.compatibility.resolved_source = None;
                let cell = match local {
                    Some(cell) => cell,
                    None => Cell {
                        image: RgbaImage::blank(edited.width, edited.height)?,
                        initial_image: None,
                        cell_name: None,
                    },
                };
                edited
                    .tracks
                    .get_mut(&track)
                    .ok_or_else(invalid)?
                    .cells
                    .insert(number, cell);
            }

            changes.push(Change::SheetSpans {
                layer_index,
                start: 0,
                stop: edited.frame_order.len(),
            });
        }
        *self = edited;
        Ok(changes)
    }

    /// Python の insert_sequence_blank 同様に、後続番号を送り配置待ちの空キーを追加する。
    pub fn insert_sequence_blank(
        &mut self,
        layer_index: usize,
        after_number: i64,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        let bank = &self.tracks[&track];
        let number = CellNumber::new(if bank.cells.is_empty() {
            1
        } else {
            after_number.checked_add(1).ok_or_else(invalid)?.max(1)
        })?;
        let mapping = bank
            .cells
            .range(number..)
            .map(|(&old, _)| {
                Ok((
                    old,
                    CellNumber::new(old.get().checked_add(1).ok_or_else(invalid)?)?,
                ))
            })
            .collect::<Result<BTreeMap<_, _>, Error>>()?;
        let image = RgbaImage::blank(self.width, self.height)?;
        let column = self.frame_order.len();
        let mut changes = self.ensure_frame_count(column + 1)?;
        self.apply_number_mapping(track, &mapping);
        // ensure_frame_count は列を変更しない。
        self.tracks.get_mut(&track).unwrap().cells.insert(
            number,
            Cell {
                image,
                initial_image: None,
                cell_name: None,
            },
        );
        let mut position = Position {
            reference: Reference::Blank,
            blank_start: true,
            pending: true,
            ..Position::default()
        };
        position.compatibility.retained_number = Some(number);
        *self.position_mut(column, track) = position;
        self.current_frame = self.frame_order[column];
        self.active_layer = track;
        changes.extend([
            Change::SheetSpans {
                layer_index,
                start: 0,
                stop: column + 1,
            },
            Change::Cursor {
                frame: column,
                notify: true,
            },
            Change::Selection { layer_index },
        ]);
        Ok(changes)
    }

    /// Python の move_sequence_image と同様に、列 layer_index の番号 first の絵を番号 second へ
    /// 動かし、その間の番号の絵を一つずつ詰める。
    ///
    /// 変わるのはバンクの絵の番号だけで、シートの位置（参照する番号・印）と旧連番保管の札は
    /// 変えない（位置の has_content・is_blank_key を書き換える形は呼び元で弾く前提）。
    /// first と second が同じ、列が範囲外、間の番号がバンクに無い場合はエラーで文書を変えない。
    pub fn move_sequence_image(
        &mut self,
        layer_index: usize,
        first: CellNumber,
        second: CellNumber,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        if first == second {
            return Err(invalid());
        }
        let (low, high) = (first.min(second).get(), first.max(second).get());
        let bank = &self.tracks[&track].cells;
        let numbers = (low..=high)
            .map(CellNumber::new)
            .collect::<Result<Vec<_>, Error>>()?;
        if numbers.iter().any(|number| !bank.contains_key(number)) {
            return Err(invalid());
        }
        // 番号 n の新しい絵は source(n) の絵（Python の source_for_number）。
        let step = if first < second { 1 } else { -1 };
        let source = |number: CellNumber| -> Result<CellNumber, Error> {
            if number == second {
                Ok(first)
            } else {
                CellNumber::new(number.get() + step)
            }
        };
        let mut moved = Vec::with_capacity(numbers.len());
        for &number in &numbers {
            moved.push((number, bank[&source(number)?].clone()));
        }
        let cells = &mut self.tracks.get_mut(&track).unwrap().cells;
        cells.extend(moved);
        Ok(vec![Change::SheetSpans {
            layer_index,
            start: 0,
            stop: self.frame_order.len(),
        }])
    }
}

impl Document {
    pub(super) fn set_placement_length(
        &mut self,
        column: usize,
        track: TrackId,
        length: usize,
        preserve_legacy: bool,
    ) {
        let settings = self.tracks[&track].settings.clone();
        let position = &self.sheet[&self.frame_order[column]].positions[&track];
        let artwork = match position.reference {
            Reference::Number(number) => self.tracks[&track].cells.get(&number),
            _ => position.compatibility.local.as_ref(),
        }
        .cloned();
        let position = self.position_mut(column, track);
        let prior = position.compatibility.legacy.take();
        let mut legacy = prior
            .and_then(|prior| prior.legacy)
            .filter(|_| preserve_legacy)
            .unwrap_or_else(|| LegacySlot {
                settings,
                state: archive_hint(position, length),
                number: position.number(),
                artwork,
            });
        legacy.state.exposure = length;
        position.compatibility.legacy = Some(LegacyPosition {
            legacy: Some(legacy),
            key_start: true,
            blank_start: position.blank_start,
            pending: position.pending,
            input_length: None,
        });
    }

    /// 配置待ちの番号を番号・列・コマ順に配置し、未使用の末尾を詰める。
    pub fn place_pending_entries(&mut self) -> Result<ChangeSet, Error> {
        let mut pending = Vec::new();
        for (column, id) in self.frame_order.iter().enumerate() {
            for (layer, track) in self.track_order.iter().enumerate() {
                let position = &self.sheet[id].positions[track];
                if let Some(number) = position.number().filter(|_| position.pending) {
                    pending.push((number, layer, column, position.clone()));
                }
            }
        }
        if pending.is_empty() {
            return Ok(Vec::new());
        }
        pending.sort_by_key(|&(number, layer, column, _)| (number, layer, column));
        let mut edited = self.clone();
        let before = edited.frame_order.len();
        let mut layers = BTreeSet::new();
        for (number, layer, source, copied) in pending {
            edited.place_pending_entry(number, layer, source, copied)?;
            layers.insert(layer);
        }
        let cursor = edited
            .frame_order
            .iter()
            .position(|&id| id == edited.current_frame)
            .ok_or_else(invalid)?;
        let after = edited.trim_unused_tail()?;
        let frame = cursor.min(after - 1);
        edited.current_frame = edited.frame_order[frame];
        let mut changes = Vec::new();
        if before != after {
            changes.push(Change::FrameStructure {
                operation: if after > before {
                    FrameOperation::Extend
                } else {
                    FrameOperation::Trim
                },
                before,
                after,
                start: before.min(after),
            });
        }
        changes.extend(layers.into_iter().map(|layer_index| Change::SheetSpans {
            layer_index,
            start: 0,
            stop: before.max(after),
        }));
        changes.push(Change::Cursor {
            frame,
            notify: true,
        });
        *self = edited;
        Ok(changes)
    }

    fn place_pending_entry(
        &mut self,
        number: CellNumber,
        layer: usize,
        source: usize,
        copied: Position,
    ) -> Result<(), Error> {
        let track = self.track_order[layer];
        let previous = self.frame_order.iter().position(|id| {
            let slot = &self.sheet[id].positions[&track];
            !slot.pending
                && slot.number().is_some_and(|n| n.get() == number.get() - 1)
                && slot.reference != Reference::Empty
        });
        let columns: Vec<Vec<_>> = self
            .track_order
            .iter()
            .map(|track| {
                self.frame_order
                    .iter()
                    .map(|id| &self.sheet[id].positions[track])
                    .collect()
            })
            .collect();
        let projected = super::v2_placement::project(&columns)?;
        let mut destination = None;
        let mut length = 1;
        if let Some(start) = previous {
            let span = self.display_spans(start, &[layer])?[0]
                .clone()
                .ok_or_else(invalid)?;
            let slot = &self.sheet[&self.frame_order[start]].positions[&track];
            let mut exposure = span.len();
            let following_content = self.frame_order.get(start + exposure).is_some_and(|id| {
                let following = &self.sheet[id].positions[&track];
                matches!(following.reference, Reference::Number(_)) && !following.pending
            });
            if following_content
                && slot
                    .compatibility
                    .dense_import
                    .as_ref()
                    .is_some_and(|import| import.guard.matches(slot))
            {
                if let Some(legacy) = slot
                    .compatibility
                    .legacy
                    .as_ref()
                    .and_then(|position| position.legacy.as_ref())
                {
                    exposure = exposure.max(legacy.state.exposure);
                }
            }
            if exposure > 1 {
                let left = exposure / 2;
                destination = Some(start.checked_add(left).ok_or(Error::TimelineSizeOverflow)?);
                length = exposure - left;
                self.set_placement_length(start, track, left, true);
            }
        }
        let mut target = destination.unwrap_or_else(|| {
            projected
                .iter()
                .filter_map(|column| {
                    column.iter().rposition(|position| {
                        !position.pending && (position.content || position.blank)
                    })
                })
                .max()
                .map_or(0, |index| index + 1)
        });
        self.ensure_frame_count(target.checked_add(1).ok_or(Error::TimelineSizeOverflow)?)?;
        let occupied = self.display_spans(target, &[layer])?[0].clone();
        let collided = self.position_mut(target, track).reference != Reference::Empty
            && occupied.as_ref().is_none_or(|span| span.start == target);
        if collided {
            target = self.frame_order.len();
        }
        let end = target
            .checked_add(length)
            .ok_or(Error::TimelineSizeOverflow)?;
        if let Some(span) = occupied.filter(|span| previous.is_none() && span.start < target) {
            for column in end..span.end {
                *self.position_mut(column, track) = self.cleared_position(column, track)?;
            }
        }
        if end < self.frame_order.len()
            && matches!(
                self.position_mut(end, track).reference,
                Reference::Number(_)
            )
        {
            self.set_placement_length(end, track, 1, false);
        }
        let dense_end = if collided { target + 1 } else { end };
        self.ensure_frame_count(dense_end)?;
        let mut position = Position {
            reference: copied.reference,
            blank_start: copied.blank_start,
            ..Position::default()
        };
        position.compatibility.retained_number = copied.compatibility.retained_number;
        // 番号付き配置待ちはバンクを参照する。配置後の空キーや番号だけの
        // 位置は新しい局所セルになり、配置待ちの局所画像を引き継がない。
        let held = held_reference(
            position.clone(),
            &self.tracks[&track].settings,
            self.blank_artwork()?,
        );
        *self.position_mut(target, track) = position;
        self.set_placement_length(target, track, length, false);
        for column in target + 1..dense_end {
            *self.position_mut(column, track) = held.clone();
        }
        *self.position_mut(source, track) = self.cleared_position(source, track)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document::v2;
    use crate::project::payload::decode_v2_plan;
    use serde_json::json;

    // tests/test_normalize_numbers.py:test_mapping_follows_first_appearance_and_keeps_reuse
    // tests/test_core_document_compare.py:test_review_normalize_unnumbered
    #[test]
    fn normalization_adds_unnumbered_artwork_after_existing_and_unplaced_numbers() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1],
            "tracks": [{
                "cells": [{"number": 1, "image": "1.png"}, {"number": 9, "image": "9.png"}],
                "sheet": [1, null],
            }],
        });
        let plan = decode_v2_plan(&input, &["1.png".into(), "9.png".into()]).unwrap();
        let images = vec![RgbaImage::blank(1, 1).unwrap(); plan.images.len()];
        let mut doc = v2::from_plan(&input, &plan, &images).unwrap();
        let track = doc.track_order[0];
        let initial = RgbaImage::from_rgba(1, 1, &[6, 9, 17, 255]).unwrap();
        doc.replace_drawing_image(1, 0, initial.clone(), true)
            .unwrap();
        doc.position_mut(1, track)
            .compatibility
            .local
            .as_mut()
            .unwrap()
            .initial_image = Some(initial.clone());
        doc.normalize_sheet_numbers(&[0]).unwrap();
        let three = CellNumber::new(3).unwrap();
        assert_eq!(
            doc.position_mut(1, track).reference,
            Reference::Number(three)
        );
        assert!(doc.position_mut(1, track).compatibility.local.is_none());
        assert_eq!(
            doc.tracks[&track].cells[&three].image.generation(),
            initial.generation()
        );
        assert_eq!(
            doc.tracks[&track].cells[&three]
                .initial_image
                .as_ref()
                .unwrap()
                .generation(),
            initial.generation()
        );
    }

    // tests/test_document_live_fast_paths.py:test_move_sequence_image_uses_core_command_and_matches_python
    #[test]
    fn moving_a_sequence_image_rotates_only_the_bank() {
        let cells =
            [1, 2, 3, 4].map(|number| json!({"number": number, "image": format!("{number}.png")}));
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1, 1, 1],
            "tracks": [{"cells": cells, "sheet": [1, 2, 3, 4]}],
        });
        let names: Vec<String> = (1..=4).map(|n| format!("{n}.png")).collect();
        let plan = decode_v2_plan(&input, &names).unwrap();
        let images: Vec<_> = (0..plan.images.len())
            .map(|_| RgbaImage::blank(1, 1).unwrap())
            .collect();
        let mut doc = v2::from_plan(&input, &plan, &images).unwrap();
        let track = doc.track_order[0];
        let generations = |doc: &Document| {
            (1..=4)
                .map(|n| {
                    doc.tracks[&track].cells[&CellNumber::new(n).unwrap()]
                        .image
                        .generation()
                })
                .collect::<Vec<_>>()
        };
        let before = generations(&doc);
        let references = |doc: &Document| {
            doc.frame_order
                .iter()
                .map(|id| doc.sheet[id].positions[&track].reference)
                .collect::<Vec<_>>()
        };
        let sheet = references(&doc);
        let n = |value| CellNumber::new(value).unwrap();
        assert_eq!(
            doc.move_sequence_image(0, n(1), n(3)).unwrap(),
            [Change::SheetSpans {
                layer_index: 0,
                start: 0,
                stop: 4
            }]
        );
        assert_eq!(
            generations(&doc),
            [before[1], before[2], before[0], before[3]]
        );
        assert_eq!(references(&doc), sheet);
        doc.move_sequence_image(0, n(3), n(1)).unwrap();
        assert_eq!(generations(&doc), before);
        // 同じ番号・バンクに無い番号・範囲外の列はエラーで変えない。
        let kept = doc.clone();
        assert!(doc.move_sequence_image(0, n(2), n(2)).is_err());
        assert!(doc.move_sequence_image(0, n(3), n(5)).is_err());
        assert!(doc.move_sequence_image(1, n(1), n(2)).is_err());
        assert_eq!(generations(&doc), generations(&kept));
    }

    // tests/test_normalize_numbers.py:test_unused_and_archived_numbers_go_last
    #[test]
    fn bank_and_sheet_have_distinct_orders_until_explicit_normalization() {
        let cells = [3, 4, 11, 12].map(|number| json!({"number": number, "image": "cell.png"}));
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1, 1, 1],
            "tracks": [{
                "cells": cells,
                "sheet": [3, 11, 4, 3],
            }],
        });
        let plan = decode_v2_plan(&input, &["cell.png".into()]).unwrap();
        let images = vec![RgbaImage::blank(1, 1).unwrap(); plan.images.len()];
        let mut doc = v2::from_plan(&input, &plan, &images).unwrap();
        let track = doc.track_order[0];
        assert_eq!(
            doc.bank_numbers(track)
                .unwrap()
                .map(CellNumber::get)
                .collect::<Vec<_>>(),
            [3, 4, 11, 12]
        );
        assert_eq!(
            doc.placed_numbers(track)
                .into_iter()
                .map(CellNumber::get)
                .collect::<Vec<_>>(),
            [3, 11, 4]
        );
        assert_eq!(doc.next_drawing_number(0).unwrap().get(), 13);
        assert_eq!(
            doc.normalize_sheet_numbers(&[0]).unwrap(),
            [Change::SheetSpans {
                layer_index: 0,
                start: 0,
                stop: 4
            }]
        );
        assert_eq!(
            v2::project(&doc, &input).unwrap().payload["tracks"][0]["sheet"],
            json!([1, 2, 3, 1])
        );
        assert_eq!(
            doc.bank_numbers(track)
                .unwrap()
                .map(CellNumber::get)
                .collect::<Vec<_>>(),
            [1, 2, 3, 4]
        );
    }
}
