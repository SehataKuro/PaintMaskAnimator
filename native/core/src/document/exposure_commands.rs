//! コマの長さ変更の Undo（Python の restore_exposure）で、列の位置を記録の値に戻す。

use std::collections::BTreeMap;
use std::sync::Arc;

use serde_json::Value;

use super::compatibility::{DenseImport, LegacyPosition};
use super::model::{Cell, CellNumber, Document, FrameId, Reference};
use crate::Error;

/// ExposureUndo の一つの位置の記録（Python の references の一要素）。
///
/// Python の位置の見え方（has_content・is_blank_key・番号など）で持つ。
#[derive(Clone, Debug)]
pub struct ExposureReference {
    /// 絵の印。
    pub has_content: bool,
    /// 空キーの印。
    pub is_blank_key: bool,
    /// 空キー区間の開始。
    pub blank_start: bool,
    /// 連番の番号（空キー・未使用に残る番号を含む）。
    pub number: Option<CellNumber>,
    /// 配置待ち（Python の sequence_only）。
    pub pending: bool,
    /// 補間キー設定。
    pub tween: Option<Value>,
    /// 中割りの所属。
    pub tween_member: Option<String>,
    /// 取り込み時の密な参照（Python の _dense_import）。
    pub dense_import: Option<DenseImport>,
    /// 原入力の旧位置（Python の _v1_position）。
    pub legacy: Option<LegacyPosition>,
}

/// restore_exposure_positions の結果。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ExposureRestore {
    /// Python の Layer.timeline_revision を進める回数（今の手順の代入で進む回数と同じ）。
    pub revisions: usize,
    /// 値が変わったかもしれない位置のコマ ID（表示順）。
    pub frames: Vec<FrameId>,
    /// 局所の絵をバンクの絵の写しに入れ替えた位置のコマ ID（表示順）。
    pub replaced: Vec<FrameId>,
}

/// Python の Layer から見た一つの位置の値。代入の副作用をこの上で再現する。
struct SlotView {
    has_content: bool,
    is_blank_key: bool,
    number: Option<CellNumber>,
    pending: bool,
    blank_start: bool,
    local: Option<Cell>,
    content_origin: Option<CellNumber>,
    dense_import: Option<DenseImport>,
    revisions: usize,
    replaced: bool,
}

impl SlotView {
    /// Layer.__setattr__ が timeline_revision を進め _dense_import を捨てる代入の共通部分。
    fn changed(&mut self, changed: bool) {
        if changed {
            self.revisions += 1;
            self.dense_import = None;
        }
    }

    /// `slot.has_content = value`。
    fn set_has_content(&mut self, value: bool, bank: &BTreeMap<CellNumber, Cell>) {
        if !value && self.has_content {
            // 内容を外すとき、番号の絵がバンクにあれば局所の絵をその写しに入れ替え、
            // 外す前の番号を _content_origin に残す。
            if let Some(cell) = self.number.and_then(|number| bank.get(&number)) {
                self.local = Some(cell.clone());
                self.replaced = true;
                self.content_origin = self.number;
            }
        }
        if value {
            self.content_origin = None;
        }
        let changed = self.has_content != value;
        self.has_content = value;
        self.changed(changed);
    }

    /// `slot.is_blank_key = value`。
    fn set_blank_key(&mut self, value: bool) {
        if value {
            self.content_origin = None;
        }
        let changed = self.is_blank_key != value;
        self.is_blank_key = value;
        self.changed(changed);
    }

    /// `slot.sequence_number = value`。
    fn set_number(&mut self, value: Option<CellNumber>) {
        let changed = self.number != value;
        self.number = value;
        self.changed(changed);
    }

    /// `slot.blank_start = value`。
    fn set_blank_start(&mut self, value: bool) {
        let changed = self.blank_start != value;
        self.blank_start = value;
        self.changed(changed);
    }

    /// `slot.set_pending(value)`。
    fn set_pending(&mut self, value: bool) {
        let changed = self.pending != value;
        self.pending = value;
        self.changed(changed);
    }
}

fn unrepresentable(what: &str) -> Error {
    Error::Invalid(format!("restore_exposure: {what}"))
}

impl Document {
    /// Python の restore_exposure と同じく、コマ数を frame_count にそろえてから（末尾にコマを
    /// 足すか、末尾のコマを取り除く）、列 layer_index の column から後ろの位置を references の
    /// 値に戻す。
    ///
    /// 今の手順（document.restore_exposure_slot）は位置ごとに has_content を外してから
    /// 番号・has_content・空キー・空キー開始・配置待ち・補間・取り込みの参照・旧位置を代入する。
    /// 代入の副作用（局所の絵の入れ替え、_content_origin と _dense_import の出し入れ、
    /// timeline_revision）も同じにする。局所の絵を共有する位置・バンクの絵を局所に持つ位置、
    /// 戻した値が Rust で表せない位置（絵と空キーが同時、バンクにない番号の絵）があれば
    /// エラーで、文書は変えない。
    pub fn restore_exposure_positions(
        &mut self,
        layer_index: usize,
        column: usize,
        frame_count: usize,
        references: &[ExposureReference],
    ) -> Result<ExposureRestore, Error> {
        let track = *self
            .track_order
            .get(layer_index)
            .ok_or_else(|| unrepresentable("列"))?;
        if frame_count == 0 || column > frame_count {
            return Err(unrepresentable("コマ"));
        }
        if frame_count > self.frame_order.len() {
            // 今の手順は先に ensure_frame_count で末尾にコマを足す（新しい位置は空画像の局所の絵）。
            let mut edited = self.clone();
            edited.ensure_frame_count(frame_count)?;
            let result =
                edited.restore_exposure_positions(layer_index, column, frame_count, references)?;
            *self = edited;
            return Ok(result);
        }
        let bank = &self.tracks[&track].cells;
        let mut restored = Vec::new();
        let mut result = ExposureRestore::default();
        let kept = &self.frame_order[column..frame_count];
        for (&frame, reference) in kept.iter().zip(references) {
            let position = &self.sheet[&frame].positions[&track];
            let compatibility = &position.compatibility;
            if compatibility.local_share.is_some() || compatibility.local_bank {
                return Err(unrepresentable("局所の絵を共有する位置"));
            }
            let (has_content, is_blank_key) = match position.reference {
                Reference::Number(_) => (true, false),
                Reference::Blank => (false, true),
                Reference::Empty => (compatibility.unnumbered_content, false),
            };
            let mut view = SlotView {
                has_content,
                is_blank_key,
                number: position.number(),
                pending: position.pending,
                blank_start: position.blank_start,
                local: compatibility.local.clone(),
                content_origin: compatibility.content_origin,
                dense_import: compatibility.dense_import,
                revisions: 0,
                replaced: false,
            };
            view.set_has_content(false, bank);
            view.set_number(reference.number);
            view.set_has_content(reference.has_content, bank);
            view.set_blank_key(reference.is_blank_key);
            view.set_blank_start(reference.blank_start);
            view.set_pending(reference.pending);
            let (reference_value, retained_number, unnumbered_content) =
                match (view.has_content, view.is_blank_key, view.number) {
                    (true, true, _) => return Err(unrepresentable("絵と空キーが同時の位置")),
                    (true, false, Some(number)) => (Reference::Number(number), None, false),
                    (true, false, None) => (Reference::Empty, None, true),
                    (false, true, number) => (Reference::Blank, number, false),
                    (false, false, number) => (Reference::Empty, number, false),
                };
            let banked = view.number.filter(|_| view.has_content || view.pending);
            if banked.is_some_and(|number| !bank.contains_key(&number)) {
                return Err(unrepresentable("バンクにない番号を参照する位置"));
            }
            let changed = view.revisions > 0
                || view.replaced
                || view.content_origin != compatibility.content_origin
                || position.tween.as_ref() != reference.tween.as_ref()
                || position.tween_member != reference.tween_member
                || compatibility.dense_import.is_some()
                || reference.dense_import.is_some()
                || compatibility.legacy.is_some()
                || reference.legacy.is_some();
            result.revisions += view.revisions;
            if changed {
                result.frames.push(frame);
            }
            if view.replaced {
                result.replaced.push(frame);
            }
            let mut position = position.clone();
            position.reference = reference_value;
            position.blank_start = view.blank_start;
            position.pending = view.pending;
            position.tween.clone_from(&reference.tween);
            position.tween_member.clone_from(&reference.tween_member);
            let compatibility = &mut position.compatibility;
            compatibility.retained_number = retained_number;
            compatibility.unnumbered_content = unnumbered_content;
            compatibility.local = view.local;
            compatibility.content_origin = view.content_origin;
            compatibility.dense_import = reference.dense_import;
            compatibility.legacy.clone_from(&reference.legacy);
            restored.push((frame, position));
        }
        // 今の手順は位置を戻す前に末尾のコマを取り除く（取り除くコマの位置は戻さない）。
        let current = self
            .frame_order
            .iter()
            .position(|&id| id == self.current_frame);
        for id in self.frame_order.drain(frame_count..) {
            self.sheet.remove(&id);
        }
        if current.is_some_and(|index| index >= frame_count) {
            self.current_frame = self.frame_order[frame_count - 1];
        }
        for (frame, position) in restored {
            let row = self.sheet.get_mut(&frame).expect("表示中のコマ");
            *Arc::make_mut(row)
                .positions
                .get_mut(&track)
                .expect("全列の位置") = position;
        }
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::image::RgbaImage;
    use serde_json::json;

    fn document() -> Document {
        // 列 A：0〜1 が 1番、2 が空キー、3 が未使用（番号 2 を残す）。バンクは 1・2。
        let slot = |content: bool, blank: bool, number: Value| {
            json!({
                "has_content": content, "is_blank_key": blank, "sequence_number": number,
                "sequence_only": false, "blank_start": false, "tween": null,
                "tween_member": null, "local": null, "local_share": null, "local_bank": false,
                "content_origin": null, "legacy_missing": false, "pending_detached": false,
                "v1_position": null, "dense_import": null, "length_imported": null,
                "resolve_source": null,
            })
        };
        let cell = |image: usize| json!({"image": image, "initial_image": null, "cell_name": null});
        let graph = json!({
            "width": 2, "height": 2, "current_frame": 0, "active_layer": 0,
            "tracks": [{"settings": {"name": "A", "visible": true, "opacity": 1.0,
                                     "is_paper": false, "is_draft": false,
                                     "alpha_locked": false, "color_filter_enabled": false,
                                     "color_filter_rgb": null},
                        "cells": [[1, cell(0)], [2, cell(1)]], "hints": []}],
            "frames": [
                {"duration": 1, "positions": [slot(true, false, json!(1))]},
                {"duration": 1, "positions": [slot(true, false, json!(1))]},
                {"duration": 1, "positions": [slot(false, true, Value::Null)]},
                {"duration": 1, "positions": [slot(false, false, json!(2))]},
            ],
        });
        let images = vec![
            RgbaImage::from_rgba(2, 2, &[1; 16]).unwrap(),
            RgbaImage::from_rgba(2, 2, &[2; 16]).unwrap(),
        ];
        Document::from_graph(&graph, &images).unwrap()
    }

    fn reference(content: bool, blank: bool, number: Option<i64>) -> ExposureReference {
        ExposureReference {
            has_content: content,
            is_blank_key: blank,
            blank_start: false,
            number: number.map(|n| CellNumber::new(n).unwrap()),
            pending: false,
            tween: None,
            tween_member: None,
            dense_import: None,
            legacy: None,
        }
    }

    fn position(doc: &Document, column: usize) -> super::super::model::Position {
        let track = doc.track_order[0];
        doc.sheet[&doc.frame_order[column]].positions[&track].clone()
    }

    #[test]
    fn restoring_the_same_values_replaces_the_unused_local_copy_like_python() {
        // tests/test_document_live_fast_paths.py:test_restore_exposure_runs_in_place
        let mut doc = document();
        let references = [
            reference(true, false, Some(1)),
            reference(false, true, None),
            reference(false, false, Some(2)),
        ];
        let result = doc
            .restore_exposure_positions(0, 1, 4, &references)
            .unwrap();
        // 絵の位置は has_content を外して立て直す（2回）。ほかの位置は値が同じ。
        assert_eq!(result.revisions, 2);
        assert_eq!(result.frames, vec![doc.frame_order[1]]);
        assert_eq!(result.replaced, vec![doc.frame_order[1]]);
        let restored = position(&doc, 1);
        assert_eq!(
            restored.reference,
            Reference::Number(CellNumber::new(1).unwrap())
        );
        assert!(restored.compatibility.content_origin.is_none());
        assert!(restored.compatibility.local.is_some());
        assert!(position(&doc, 0).compatibility.local.is_none());
    }

    #[test]
    fn restoring_a_drawing_as_empty_keeps_the_former_number() {
        let mut doc = document();
        let result = doc
            .restore_exposure_positions(0, 1, 4, &[reference(false, false, Some(1))])
            .unwrap();
        assert_eq!(result.revisions, 1);
        let restored = position(&doc, 1);
        assert_eq!(restored.reference, Reference::Empty);
        assert_eq!(restored.number(), CellNumber::new(1).ok());
        assert_eq!(
            restored.compatibility.content_origin,
            CellNumber::new(1).ok()
        );
    }

    #[test]
    fn restoring_a_shorter_sheet_removes_the_tail_frames_first() {
        let mut doc = document();
        doc.set_selection_index(3, 0).unwrap();
        let tail = doc.frame_order[3];
        let result = doc
            .restore_exposure_positions(
                0,
                2,
                3,
                &[
                    reference(false, true, None),
                    reference(true, false, Some(1)),
                ],
            )
            .unwrap();
        // 取り除いたコマの位置は戻さない（記録の残りは使わない）。
        assert_eq!(result.revisions, 0);
        assert!(result.frames.is_empty());
        assert_eq!(doc.frame_order.len(), 3);
        assert!(!doc.sheet.contains_key(&tail));
        assert_eq!(doc.current_frame, doc.frame_order[2]);
    }

    #[test]
    fn restoring_a_longer_sheet_adds_frames_before_restoring_positions() {
        let mut doc = document();
        let references = [reference(false, false, None), reference(false, true, None)];
        let result = doc
            .restore_exposure_positions(0, 4, 6, &references)
            .unwrap();
        assert_eq!(doc.frame_order.len(), 6);
        assert_eq!(position(&doc, 5).reference, Reference::Blank);
        assert_eq!(result.frames, vec![doc.frame_order[5]]);
    }

    #[test]
    fn unrepresentable_values_leave_the_document_unchanged() {
        let mut doc = document();
        let before = position(&doc, 2).reference;
        let references = [reference(false, true, None), reference(true, true, Some(1))];
        assert!(doc
            .restore_exposure_positions(0, 2, 4, &references)
            .is_err());
        assert_eq!(position(&doc, 2).reference, before);
        assert!(doc
            .restore_exposure_positions(0, 0, 4, &[reference(true, false, Some(9))])
            .is_err());
        assert!(doc.restore_exposure_positions(1, 0, 4, &[]).is_err());
        assert!(doc
            .restore_exposure_positions(0, 0, 6, &[reference(true, true, Some(1))])
            .is_err());
        assert_eq!(doc.frame_order.len(), 4);
    }
}
