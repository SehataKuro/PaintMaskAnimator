//! 旧形式取り込み前の描画操作。

use super::changes::{Change, ChangeSet};
use super::frame_commands::held_reference;
use super::graph::check_bank_reference;
use super::model::{Cell, CellNumber, Document, Position, Reference, Track, TrackId};
use crate::{image::RgbaImage, Error};

fn unsupported() -> Error {
    Error::Invalid("配置前の描画を現在の文書で表せない".into())
}

fn has_content(slot: &Position) -> bool {
    matches!(slot.reference, Reference::Number(_)) || slot.compatibility.unnumbered_content
}

impl Document {
    fn staging_position(
        &self,
        frame: usize,
        layer: usize,
    ) -> Result<(TrackId, &Track, &Position), Error> {
        let fid = self.frame_order.get(frame).ok_or_else(unsupported)?;
        let tid = *self.track_order.get(layer).ok_or_else(unsupported)?;
        let track = self.tracks.get(&tid).ok_or_else(unsupported)?;
        let slot = self
            .sheet
            .get(fid)
            .and_then(|row| row.positions.get(&tid))
            .ok_or_else(unsupported)?;
        Ok((tid, track, slot))
    }

    /// 代入前の Cell の画像を置き換え、内容の印、番号の順に設定する。
    /// 表せない所属や番号では文書を変えずにエラーを返す。
    pub fn stage_numbered_drawing(
        &mut self,
        frame: usize,
        layer: usize,
        image: RgbaImage,
        content: bool,
        number: Option<CellNumber>,
    ) -> Result<(ChangeSet, usize), Error> {
        let (tid, track, old) = self.staging_position(frame, layer)?;
        let compat = &old.compatibility;
        let bank = old.number().filter(|_| old.references_bank());
        if compat.local_share.is_some()
            || compat.local_bank
            || (content && old.reference == Reference::Blank)
            || (bank.is_some() && has_content(old) && !content)
        {
            return Err(unsupported());
        }
        let final_number = if content { number } else { old.number() };
        let mut slot = old.clone();
        if let Some(number) = final_number.filter(|_| content) {
            slot.reference = Reference::Number(number);
        } else if old.reference != Reference::Blank {
            slot.reference = Reference::Empty;
        }
        slot.compatibility.unnumbered_content = content && final_number.is_none();
        slot.compatibility.retained_number = final_number.filter(|_| !content);
        let delta = [has_content(old) != content, old.number() != final_number]
            .into_iter()
            .filter(|changed| *changed)
            .count();
        if delta != 0 {
            slot.compatibility.dense_import = None;
        }
        if content {
            slot.compatibility.content_origin = None;
        }
        check_bank_reference(&slot, track)?;
        if let Some(number) = bank {
            let cell = self
                .tracks
                .get_mut(&tid)
                .and_then(|track| track.cells.get_mut(&number))
                .ok_or_else(unsupported)?;
            cell.image = image;
        } else if let Some(cell) = &mut slot.compatibility.local {
            cell.image = image;
        } else {
            slot.compatibility.local = Some(Cell {
                image,
                initial_image: None,
                cell_name: None,
            });
        }
        *self.position_mut(frame, tid) = slot;
        Ok((
            vec![Change::DrawingPixels {
                layer_index: layer,
                number: final_number,
                frame: Some(frame),
                rects: vec![],
            }],
            delta,
        ))
    }

    /// 同じ列のバンク参照を複製する。配置待ちの所属は複製した位置が持つ。
    /// 局所 Cell の共有が生じる場合は文書を変えずにエラーを返す。
    pub fn stage_drawing_reference(
        &mut self,
        frame: usize,
        layer: usize,
        source: usize,
        blank: RgbaImage,
    ) -> Result<(ChangeSet, usize), Error> {
        let (tid, track, target) = self.staging_position(frame, layer)?;
        let (_, _, slot) = self.staging_position(source, layer)?;
        if target.compatibility.local_share.is_some()
            || !slot.references_bank()
            || slot.compatibility.local.is_some()
            || slot.compatibility.local_share.is_some()
        {
            return Err(unsupported());
        }
        check_bank_reference(slot, track)?;
        let stop = frame.checked_add(1).ok_or_else(unsupported)?;
        // clone_slot の初期代入と、clone_reference が空キー開始を外す代入。
        let delta = [
            has_content(slot),
            slot.reference == Reference::Blank,
            slot.blank_start,
            slot.number().is_some(),
            slot.pending,
            slot.blank_start,
        ]
        .into_iter()
        .filter(|changed| *changed)
        .count();
        let mut copy = held_reference(slot.clone(), &track.settings, blank);
        copy.compatibility.pending_detached = true;
        if let Some(legacy) = &mut copy.compatibility.legacy {
            legacy.pending = false;
        }
        if has_content(slot) || slot.reference == Reference::Blank {
            copy.compatibility.content_origin = None;
        }
        *self.position_mut(frame, tid) = copy;
        Ok((
            vec![Change::SheetSpans {
                layer_index: layer,
                start: frame,
                stop,
            }],
            delta,
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document::v2;
    use crate::project::payload::decode_v2_plan;
    use serde_json::json;

    fn fixture() -> Document {
        let payload = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1, 1],
            "tracks": [{"cells": [{"number": 1, "image": "a.png"}], "sheet": [1, null, null]}]
        });
        let plan = decode_v2_plan(&payload, &["a.png".into()]).unwrap();
        v2::from_plan(&payload, &plan, &[RgbaImage::blank(1, 1).unwrap()]).unwrap()
    }

    #[test]
    fn updates_the_original_artwork_before_setting_the_number() {
        let mut doc = fixture();
        let image = RgbaImage::from_rgba(1, 1, &[12, 34, 56, 255]).unwrap();
        let number = CellNumber::new(1).unwrap();
        let (changes, delta) = doc
            .stage_numbered_drawing(1, 0, image, true, Some(number))
            .unwrap();
        assert_eq!(delta, 2);
        assert_eq!(
            changes,
            [Change::DrawingPixels {
                layer_index: 0,
                number: Some(number),
                frame: Some(1),
                rects: vec![]
            }]
        );
        let (_, track, slot) = doc.staging_position(1, 0).unwrap();
        assert_eq!(slot.reference, Reference::Number(number));
        assert_eq!(
            slot.compatibility.local.as_ref().unwrap().image.rgba(),
            &[12, 34, 56, 255]
        );
        assert_eq!(track.cells[&number].image.rgba(), &[0; 4]);
    }

    #[test]
    fn clones_the_reference_with_a_detached_pending_intent() {
        let mut doc = fixture();
        let track = doc.track_order[0];
        doc.position_mut(0, track).pending = true;
        let (changes, delta) = doc
            .stage_drawing_reference(1, 0, 0, RgbaImage::blank(1, 1).unwrap())
            .unwrap();
        assert_eq!(delta, 3);
        assert_eq!(
            changes,
            [Change::SheetSpans {
                layer_index: 0,
                start: 1,
                stop: 2
            }]
        );
        let (_, _, slot) = doc.staging_position(1, 0).unwrap();
        assert!(slot.pending && slot.compatibility.pending_detached);
        assert!(!slot.compatibility.legacy.as_ref().unwrap().pending);
        assert_eq!(doc.reinstall_legacy_frames().unwrap().revisions, 2);
        let (_, _, slot) = doc.staging_position(1, 0).unwrap();
        assert!(slot.pending && !slot.compatibility.pending_detached);
    }

    #[test]
    fn unrepresentable_edits_leave_the_document_intact() {
        let mut doc = fixture();
        let (before, images) = doc.to_graph();
        let number = CellNumber::new(17).unwrap();
        let image = RgbaImage::from_rgba(1, 1, &[12, 34, 56, 255]).unwrap();
        assert!(doc
            .stage_numbered_drawing(0, 0, image, true, Some(number))
            .is_err());
        assert!(doc
            .stage_drawing_reference(0, 0, 1, RgbaImage::blank(1, 1).unwrap())
            .is_err());
        let (after, actual) = doc.to_graph();
        assert_eq!(before, after);
        assert_eq!(
            images.iter().map(RgbaImage::generation).collect::<Vec<_>>(),
            actual.iter().map(RgbaImage::generation).collect::<Vec<_>>()
        );
    }
}
