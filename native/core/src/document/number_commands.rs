use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

use super::changes::{Change, ChangeSet};
use super::compatibility::{LegacyPosition, LegacySlot};
use super::frame_commands::archive_hint;
use super::model::{Cell, CellNumber, Document, Position, Reference, TrackId};
use crate::{image::RgbaImage, Error};

fn invalid() -> Error {
    Error::Invalid("列またはセル番号の指定が不正です".into())
}

impl Document {
    fn apply_number_mapping(
        &mut self,
        track: TrackId,
        mapping: &BTreeMap<CellNumber, CellNumber>,
        clear_dense_import: bool,
    ) {
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
            if clear_dense_import
                && position
                    .number()
                    .is_some_and(|number| mapping.contains_key(&number))
            {
                position.compatibility.dense_import = None;
            }
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
            edited.apply_number_mapping(track, &mapping, false);
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
    /// 画像を渡すと新しいバンクの Cell に使う。番号が変わる位置の取り込み記録を外す。
    pub fn insert_sequence_blank(
        &mut self,
        layer_index: usize,
        after_number: i64,
        image: Option<RgbaImage>,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        let bank = self.tracks.get(&track).ok_or_else(invalid)?;
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
        let image = match image {
            Some(image) => image,
            None => RgbaImage::blank(self.width, self.height)?,
        };
        let column = self.frame_order.len();
        let stop = column.checked_add(1).ok_or(Error::TimelineSizeOverflow)?;
        let mut changes = self.ensure_frame_count(stop)?;
        self.apply_number_mapping(track, &mapping, true);
        // ensure_frame_count は列を変更しない。
        self.tracks
            .get_mut(&track)
            .ok_or_else(invalid)?
            .cells
            .insert(
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
                stop,
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
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document::compatibility::{DenseImport, PositionGuard};
    use crate::document::v2;
    use crate::project::payload::decode_v2_plan;
    use serde_json::json;

    #[test]
    fn inserting_a_sequence_blank_clears_only_renumbered_dense_imports() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1, 1],
            "tracks": [{
                "cells": [{"number": 1, "image": "1.png"}, {"number": 2, "image": "2.png"}],
                "sheet": [1, 2, 2],
            }],
        });
        let plan = decode_v2_plan(&input, &["1.png".into(), "2.png".into()]).unwrap();
        let images = vec![RgbaImage::blank(1, 1).unwrap(); plan.images.len()];
        let mut doc = v2::from_plan(&input, &plan, &images).unwrap();
        let track = doc.track_order[0];
        for column in 0..3 {
            let position = doc.position_mut(column, track);
            position.compatibility.dense_import = Some(DenseImport {
                guard: PositionGuard::capture(position),
                sheet: Reference::Blank,
                resolved: Reference::Empty,
            });
        }

        let before = doc.to_graph().0;
        assert!(doc.insert_sequence_blank(0, i64::MAX, None).is_err());
        assert_eq!(doc.to_graph().0, before);
        let two = CellNumber::new(2).unwrap();
        let maximum = CellNumber::new(i64::MAX).unwrap();
        let mut overflow = doc.clone();
        let cells = &mut overflow.tracks.get_mut(&track).unwrap().cells;
        let cell = cells.remove(&two).unwrap();
        cells.insert(maximum, cell);
        let before = overflow.to_graph().0;
        assert!(overflow.insert_sequence_blank(0, 1, None).is_err());
        assert_eq!(overflow.to_graph().0, before);

        let three = CellNumber::new(3).unwrap();
        let image = RgbaImage::blank(2, 3).unwrap();
        let generation = image.generation();
        let changes = doc.insert_sequence_blank(0, 1, Some(image)).unwrap();
        assert_eq!(changes.len(), 4);
        assert!(doc
            .position_mut(0, track)
            .compatibility
            .dense_import
            .is_some());
        for column in 1..3 {
            let position = doc.position_mut(column, track);
            assert_eq!(position.number(), Some(three));
            assert!(position.compatibility.dense_import.is_none());
        }
        let pending = doc.position_mut(3, track);
        assert_eq!(pending.number(), Some(two));
        assert!(pending.pending && pending.blank_start);
        assert_eq!(
            doc.tracks[&track].cells[&two].image.generation(),
            generation
        );
    }

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
