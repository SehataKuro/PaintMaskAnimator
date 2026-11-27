//! 配置待ちの番号をシートへ配置する。

use std::collections::BTreeSet;

use super::changes::{Change, ChangeSet, FrameOperation};
use super::compatibility::LegacyPosition;
use super::frame_commands::held_reference;
use super::model::{Cell, CellNumber, Document, FrameId, Position, Reference, TrackId};
use super::v2_placement;
use crate::Error;

type PositionKey = (FrameId, TrackId);

/// 配置の通知と、Python の窓口の更新に必要な位置。
#[derive(Default)]
pub struct PendingPlacement {
    /// Python と同じ順序の通知。
    pub changes: ChangeSet,
    /// Layer.timeline_revision の増分。
    pub revisions: usize,
    /// 値が変わった位置。
    pub changed: BTreeSet<PositionKey>,
    /// Layer を入れ替えた既存位置。
    pub replaced: BTreeSet<PositionKey>,
    /// Layer を保って局所の Cell を外した既存位置。
    pub cleared: BTreeSet<PositionKey>,
    /// 取り除く直前の末尾を含む文書。
    pub before_trim: Option<Document>,
}

fn unsupported(reason: &str) -> Error {
    Error::Invalid(format!("place_pending_entries: {reason}"))
}

fn has_content(slot: &Position) -> bool {
    matches!(slot.reference, Reference::Number(_)) || slot.compatibility.unnumbered_content
}

fn is_key(slot: &Position) -> bool {
    has_content(slot) || slot.reference == Reference::Blank
}

fn check_local(slot: &Position) -> Result<(), Error> {
    if slot.compatibility.local_share.is_some() {
        return Err(unsupported("局所の絵を共有する位置"));
    }
    Ok(())
}

fn clone_pending_revisions(slot: &Position) -> usize {
    // Layer の初期代入で番号と配置待ちを設定し、接続後の新しいバンクに札を登録する。
    usize::from(is_key(slot)) + usize::from(slot.blank_start) + 3
}

fn clear_revisions(slot: &Position, blank: bool) -> usize {
    usize::from(slot.number().is_some())
        + usize::from(has_content(slot))
        + usize::from((slot.reference == Reference::Blank) != blank)
        + usize::from(slot.blank_start != blank)
        + usize::from(slot.pending)
}

struct PlacementEdit {
    document: Document,
    original_next_id: u64,
    result: PendingPlacement,
}

impl PlacementEdit {
    fn slot(&self, column: usize, track: TrackId) -> &Position {
        &self.document.sheet[&self.document.frame_order[column]].positions[&track]
    }

    fn clear(&mut self, column: usize, track: TrackId) -> Result<(), Error> {
        let slot = self.slot(column, track);
        check_local(slot)?;
        self.result.revisions += clear_revisions(slot, false);
        let cleared = self.document.cleared_position(column, track)?;
        let key = (self.document.frame_order[column], track);
        if key.0 .0 < self.original_next_id && !self.result.replaced.contains(&key) {
            self.result.cleared.insert(key);
        }
        self.result.changed.insert(key);
        *self.document.position_mut(column, track) = cleared;
        Ok(())
    }

    fn write_legacy(&mut self, column: usize, track: TrackId, legacy: LegacyPosition) {
        let key = (self.document.frame_order[column], track);
        self.result.changed.insert(key);
        self.document
            .position_mut(column, track)
            .compatibility
            .legacy = Some(legacy);
    }

    fn snapshot(&self, column: usize, track: TrackId, exposure: usize) -> LegacyPosition {
        // snapshot_layer(slot) は現在の値を採る。既存の旧位置は参照しない。
        let mut slot = self.slot(column, track).clone();
        slot.compatibility.legacy = None;
        let mut old = self.document.legacy_snapshot(&slot, track);
        if let Some(legacy) = &mut old.legacy {
            legacy.state.exposure = exposure;
        }
        old.key_start = true;
        old
    }

    fn visible_end(&self) -> Result<usize, Error> {
        let columns: Vec<Vec<_>> = self
            .document
            .track_order
            .iter()
            .map(|&track| {
                (0..self.document.frame_order.len())
                    .map(|column| self.slot(column, track))
                    .collect()
            })
            .collect();
        Ok(v2_placement::project(&columns)?
            .iter()
            .filter_map(|slots| {
                slots
                    .iter()
                    .rposition(|slot| !slot.pending && (slot.content || slot.blank))
            })
            .max()
            .map_or(0, |column| column + 1))
    }

    fn place(
        &mut self,
        number: CellNumber,
        layer: usize,
        source: usize,
        copied: &Position,
    ) -> Result<(), Error> {
        let track = self.document.track_order[layer];
        let count = self.document.frame_order.len();
        let visible_end = self.visible_end()?;
        let previous = (0..count).find(|&column| {
            let slot = self.slot(column, track);
            !slot.pending
                && is_key(slot)
                && slot.number().is_some_and(|n| n.get() == number.get() - 1)
        });
        let mut target = None;
        let mut exposure = 1;
        if let Some(start) = previous {
            let slot = self.slot(start, track);
            let (_, _, mut length) = self
                .document
                .timeline_block_at(start, layer)
                .ok_or_else(|| unsupported("直前番号の保持がない"))?;
            let old = slot
                .compatibility
                .legacy
                .as_ref()
                .and_then(|old| old.legacy.as_ref());
            if let Some(imported) = old.filter(|_| {
                slot.compatibility
                    .dense_import
                    .is_some_and(|import| import.guard.matches(slot))
                    && start + length < count
                    && has_content(self.slot(start + length, track))
                    && !self.slot(start + length, track).pending
            }) {
                length = length.max(imported.state.exposure);
            }
            if length > 1 {
                let split = length / 2;
                target = Some(
                    start
                        .checked_add(split)
                        .ok_or(Error::TimelineSizeOverflow)?,
                );
                exposure = length - split;
                let mut boundary = if let Some(old) = old {
                    let mut boundary = self.snapshot(start, track, split);
                    boundary.legacy = Some(old.clone());
                    boundary.legacy.as_mut().expect("旧位置あり").state.exposure = split;
                    boundary
                } else {
                    self.snapshot(start, track, split)
                };
                boundary.blank_start = slot.blank_start;
                self.write_legacy(start, track, boundary);
            }
        }
        let mut target = target.unwrap_or(visible_end);
        self.document
            .ensure_frame_count(target.checked_add(1).ok_or(Error::TimelineSizeOverflow)?)?;
        let occupied = self.document.timeline_block_at(target, layer);
        let collided = is_key(self.slot(target, track))
            && occupied.is_none_or(|(_, start, _)| start == target);
        if collided {
            target = self.document.frame_order.len();
            self.document.ensure_frame_count(target + 1)?;
        }
        let tail = target
            .checked_add(exposure)
            .ok_or(Error::TimelineSizeOverflow)?;
        if let Some((_, start, length)) =
            occupied.filter(|(_, start, _)| previous.is_none() && *start < target)
        {
            for column in tail..start + length {
                self.clear(column, track)?;
            }
        }
        if tail < self.document.frame_order.len() && has_content(self.slot(tail, track)) {
            let boundary = self.snapshot(tail, track, 1);
            self.write_legacy(tail, track, boundary);
        }

        self.install_target(target, track, number, copied, exposure)?;
        let end = if collided { target + 1 } else { tail };
        self.document.ensure_frame_count(end)?;
        if end > target + 1 {
            // 番号付きの内容はバンクの絵を参照する。空キー等は先頭と局所の Cell を共有する。
            if !self.slot(target, track).references_bank() {
                let share = self
                    .document
                    .sheet
                    .values()
                    .flat_map(|row| row.positions.values())
                    .filter_map(|position| position.compatibility.local_share)
                    .max()
                    .map_or(Some(0), |share| share.checked_add(1))
                    .ok_or_else(|| unsupported("局所の絵の共有 ID が上限"))?;
                self.document
                    .position_mut(target, track)
                    .compatibility
                    .local_share = Some(share);
            }
            let head = self.slot(target, track);
            // clone_slot はメタデータを初期値から代入し、clone_reference は空キー開始を外す。
            let clone_revisions = usize::from(head.number().is_some())
                + usize::from(is_key(head))
                + 2 * usize::from(head.blank_start);
            let held = held_reference(
                head.clone(),
                &self.document.tracks[&track].settings,
                self.document.blank_artwork()?,
            );
            for column in target + 1..end {
                self.replace(column, track, held.clone(), clone_revisions)?;
            }
        }
        self.clear(source, track)
    }

    fn install_target(
        &mut self,
        column: usize,
        track: TrackId,
        number: CellNumber,
        copied: &Position,
        exposure: usize,
    ) -> Result<(), Error> {
        let prior = self.slot(column, track);
        check_local(prior)?;
        let blank = copied.reference == Reference::Blank;
        let content = has_content(copied);
        self.result.revisions += clear_revisions(prior, blank)
            + clone_pending_revisions(copied)
            + 3 // 番号、配置待ちの設定と解除
            + usize::from(content) + usize::from(copied.blank_start != blank);
        let mut placed = Position {
            reference: copied.reference,
            blank_start: copied.blank_start,
            compatibility: self.document.cleared_position(column, track)?.compatibility,
            ..Position::default()
        };
        placed.compatibility.retained_number = (!content).then_some(number);
        placed.compatibility.local = if content {
            None
        } else {
            // copy_drawing_reference は局所の絵を捨てる。札を解いた後の snapshot_layer が
            // 空の Cell を作り、バンクに登録済みの絵はそのまま残る。
            Some(Cell {
                image: self.document.blank_artwork()?,
                initial_image: None,
                cell_name: None,
                is_genga: false,
            })
        };
        let key = (self.document.frame_order[column], track);
        if key.0 .0 < self.original_next_id && !self.result.replaced.contains(&key) {
            self.result.cleared.insert(key);
        }
        self.result.changed.insert(key);
        *self.document.position_mut(column, track) = placed;
        let boundary = self.snapshot(column, track, exposure);
        self.write_legacy(column, track, boundary);
        Ok(())
    }

    fn replace(
        &mut self,
        column: usize,
        track: TrackId,
        mut held: Position,
        clone_revisions: usize,
    ) -> Result<(), Error> {
        let prior = self.slot(column, track);
        check_local(prior)?;
        let pending = prior.pending;
        let key = (self.document.frame_order[column], track);
        if key.0 .0 < self.original_next_id && !self.result.replaced.contains(&key) {
            if self.result.changed.contains(&key) {
                return Err(unsupported("変更後に外れる位置"));
            }
            self.result.replaced.insert(key);
        }
        self.result.revisions += usize::from(pending) + clone_revisions;
        held.compatibility.pending_detached = false;
        self.result.changed.insert(key);
        *self.document.position_mut(column, track) = held;
        Ok(())
    }

    fn trim(&mut self) -> usize {
        let mut after = self.document.frame_order.len();
        let tracks = self.document.track_order.clone();
        while after > 1
            && (0..tracks.len())
                .all(|layer| self.document.timeline_block_at(after - 1, layer).is_none())
        {
            after -= 1;
            for &track in &tracks {
                let slot = self.document.position_mut(after, track);
                if slot.pending {
                    self.result.revisions += 1;
                    slot.pending = false;
                    slot.compatibility.dense_import = None;
                }
            }
        }
        if after < self.document.frame_order.len() {
            self.result.before_trim = Some(self.document.clone());
            for id in self.document.frame_order.drain(after..) {
                self.document.sheet.remove(&id);
            }
        }
        after
    }
}

impl Document {
    /// 配置待ちを置いて未使用末尾を詰め、通知を返す。カーソルも通知の位置まで収める。
    pub fn place_pending_entries(&mut self) -> Result<ChangeSet, Error> {
        let cursor = self
            .frame_order
            .iter()
            .position(|&id| id == self.current_frame)
            .ok_or_else(|| unsupported("現在コマがない"))?;
        let result = self.place_pending_entries_with_updates(cursor)?;
        if let Some(Change::Cursor { frame, .. }) = result.changes.last() {
            self.current_frame = self.frame_order[*frame];
        }
        Ok(result.changes)
    }

    /// 番号、列、元のコマの順で配置待ちの絵をシートへ置く。既存のバンクの絵を保持し、
    /// 移動先の保持と旧キー境界を更新する。表せない位置があれば文書を変えずエラーを返す。
    pub fn place_pending_entries_with_updates(
        &mut self,
        cursor: usize,
    ) -> Result<PendingPlacement, Error> {
        let mut pending = Vec::new();
        for (column, frame) in self.frame_order.iter().enumerate() {
            for (layer, track) in self.track_order.iter().enumerate() {
                let slot = &self.sheet[frame].positions[track];
                if let Some(number) = slot.number().filter(|_| slot.pending) {
                    if !self.tracks[track].cells.contains_key(&number) {
                        return Err(unsupported("配置待ちの絵がバンクにない"));
                    }
                    pending.push((number, layer, column, slot));
                }
            }
        }
        if pending.is_empty() {
            return Ok(PendingPlacement::default());
        }
        if self
            .blank
            .as_ref()
            .is_some_and(|blank| (blank.width(), blank.height()) != self.size())
        {
            return Err(unsupported("現在の作業領域と文書の大きさが異なる"));
        }
        pending.sort_unstable_by_key(|&(number, layer, column, _)| (number, layer, column));
        let before = self.frame_order.len();
        let mut edit = PlacementEdit {
            document: self.clone(),
            original_next_id: self.next_frame_id,
            result: PendingPlacement::default(),
        };
        // project_v1_state の snapshot_layer は絵の無い局所位置に空の Cell を作る。
        for (column, &frame) in self.frame_order.iter().enumerate() {
            for &track in &self.track_order {
                let slot = &self.sheet[&frame].positions[&track];
                let unnumbered_legacy = slot
                    .compatibility
                    .legacy
                    .as_ref()
                    .and_then(|old| old.legacy.as_ref())
                    .is_some_and(|old| old.state.has_content && old.number.is_none());
                if slot.compatibility.unnumbered_content || unnumbered_legacy {
                    return Err(unsupported("番号のない内容を含む旧形式投影"));
                }
                if !slot.references_bank() && slot.compatibility.local.is_none() {
                    let blank = edit.document.blank_artwork()?;
                    edit.document
                        .position_mut(column, track)
                        .compatibility
                        .local = Some(Cell {
                        image: blank,
                        initial_image: None,
                        cell_name: None,
                        is_genga: false,
                    });
                    edit.result.changed.insert((frame, track));
                }
            }
        }
        let layers: BTreeSet<_> = pending.iter().map(|entry| entry.1).collect();
        for (number, layer, source, copied) in pending {
            // 最初のループの clone()。copy_drawing_reference 内の clone() は別に数える。
            edit.result.revisions += clone_pending_revisions(copied);
            edit.place(number, layer, source, copied)?;
        }
        let after = edit.trim();
        if after != before {
            edit.result.changes.push(Change::FrameStructure {
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
        edit.result
            .changes
            .extend(layers.into_iter().map(|layer_index| Change::SheetSpans {
                layer_index,
                start: 0,
                stop: before.max(after),
            }));
        edit.result.changes.push(Change::Cursor {
            frame: cursor.min(after - 1),
            notify: true,
        });
        if !edit
            .document
            .sheet
            .contains_key(&edit.document.current_frame)
        {
            edit.document.current_frame = edit.document.frame_order[after - 1];
        }
        *self = edit.document;
        Ok(edit.result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document::v2;
    use crate::image::RgbaImage;
    use crate::project::payload::decode_v2_plan;
    use serde_json::json;

    fn document() -> Document {
        let payload = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1, 1, 1, 1],
            "tracks": [{
                "cells": [{"number": 1, "image": "1.png"}, {"number": 2, "image": "2.png"}],
                "sheet": [1, 1, 1, 1, null]
            }]
        });
        let plan = decode_v2_plan(&payload, &["1.png".into(), "2.png".into()]).unwrap();
        let images = [
            RgbaImage::blank(1, 1).unwrap(),
            RgbaImage::from_rgba(1, 1, &[1, 2, 3, 255]).unwrap(),
        ];
        let mut doc = v2::from_plan(&payload, &plan, &images).unwrap();
        let track = doc.track_order[0];
        let pending = doc.position_mut(4, track);
        pending.reference = Reference::Number(CellNumber::new(2).unwrap());
        pending.pending = true;
        pending.compatibility.dense_import = None;
        doc
    }

    #[test]
    fn splits_a_hold_and_keeps_the_bank_and_the_removed_row() {
        let mut doc = document();
        let track = doc.track_order[0];
        let number = CellNumber::new(2).unwrap();
        let bank = doc.tracks[&track].cells[&number].image.generation();
        let ids = doc.frame_order.clone();
        let result = doc.place_pending_entries_with_updates(4).unwrap();
        assert_eq!(
            result.changes,
            vec![
                Change::FrameStructure {
                    operation: FrameOperation::Trim,
                    before: 5,
                    after: 4,
                    start: 4
                },
                Change::SheetSpans {
                    layer_index: 0,
                    start: 0,
                    stop: 5
                },
                Change::Cursor {
                    frame: 3,
                    notify: true
                },
            ]
        );
        assert_eq!(result.revisions, 19);
        assert_eq!(result.replaced, BTreeSet::from([(ids[3], track)]));
        assert_eq!(
            result.cleared,
            BTreeSet::from([(ids[2], track), (ids[4], track)])
        );
        assert_eq!(doc.tracks[&track].cells[&number].image.generation(), bank);
        assert_eq!(doc.timeline_block_at(3, 0), Some((false, 2, 2)));
        let removed = &result.before_trim.unwrap().sheet[&ids[4]].positions[&track];
        assert!(!removed.pending);
        assert_eq!(removed.number(), None);
        assert!(removed.compatibility.local.is_some());
    }

    #[test]
    fn a_shared_local_cell_rejects_the_whole_edit() {
        let mut doc = document();
        let track = doc.track_order[0];
        doc.position_mut(4, track).compatibility.local_share = Some(4);
        let before = doc.to_graph();
        assert!(doc.place_pending_entries_with_updates(0).is_err());
        assert_eq!(doc.to_graph().0, before.0);
    }
}
