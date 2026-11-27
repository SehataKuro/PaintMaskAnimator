//! 補間キーと中割りの取り付け、再編集、解除。

use std::collections::BTreeSet;
use std::ops::Range;

use serde_json::Value;

use super::changes::{Change, ChangeSet};
use super::frame_commands::held_reference;
use super::model::{Cell, CellNumber, Document, Reference, TrackId};
use crate::{image::RgbaImage, Error};

fn unsupported() -> Error {
    Error::Invalid("補間操作を同じ形で適用できません".into())
}

fn span(layer_index: usize, range: Range<usize>) -> Change {
    Change::SheetSpans {
        layer_index,
        start: range.start,
        stop: range.end,
    }
}

impl Document {
    fn tween_range(
        &self,
        column: usize,
        layer: usize,
        length: usize,
    ) -> Result<Option<(TrackId, Range<usize>)>, Error> {
        let Some(&track) = self.track_order.get(layer) else {
            return Ok(None);
        };
        if column >= self.frame_order.len() || length == 0 {
            return Ok(None);
        }
        let end = column
            .checked_add(length)
            .ok_or(Error::TimelineSizeOverflow)?;
        Ok(Some((track, column..end)))
    }

    /// 中割りの番号を、既存の配置とバンクの最大番号の次から割り当てる。
    pub fn tween_numbers(&self, layer: usize, count: usize) -> Result<Vec<CellNumber>, Error> {
        let track = *self.track_order.get(layer).ok_or_else(unsupported)?;
        let last = self
            .frame_order
            .iter()
            .filter_map(|fid| self.sheet[fid].positions[&track].number())
            .chain(self.tracks[&track].cells.keys().copied())
            .max()
            .map_or(0, CellNumber::get);
        (1..=count)
            .map(|offset| {
                let offset = i64::try_from(offset).map_err(|_| Error::TimelineSizeOverflow)?;
                CellNumber::new(
                    last.checked_add(offset)
                        .ok_or(Error::TimelineSizeOverflow)?,
                )
            })
            .collect()
    }

    fn tween_key_image(
        &mut self,
        column: usize,
        track: TrackId,
        image: RgbaImage,
    ) -> Result<(), Error> {
        let position = &self.sheet[&self.frame_order[column]].positions[&track];
        if position.compatibility.local_share.is_some() {
            return Err(unsupported());
        }
        if let Some(number) = position.number().filter(|_| position.references_bank()) {
            self.tracks
                .get_mut(&track)
                .and_then(|track| track.cells.get_mut(&number))
                .ok_or_else(unsupported)?
                .image = image;
        } else {
            let cell = self
                .position_mut(column, track)
                .compatibility
                .local
                .get_or_insert_with(|| Cell {
                    image: image.clone(),
                    initial_image: None,
                    cell_name: None,
                    is_genga: false,
                });
            cell.image = image;
        }
        Ok(())
    }

    /// キーの画像と設定を差し替え、中割りを入力順に作る。通知と世代の増分を返す。
    /// 同じ形で適用できないときは文書を変更せずエラーを返す。
    pub fn install_tween(
        &mut self,
        column: usize,
        layer: usize,
        images: &[RgbaImage],
        spec: Value,
        numbers: &[CellNumber],
    ) -> Result<(ChangeSet, usize), Error> {
        let Some((track, range)) = self.tween_range(column, layer, images.len())? else {
            return Ok((Vec::new(), 0));
        };
        let member = spec
            .get("id")
            .and_then(Value::as_str)
            .ok_or_else(unsupported)?;
        let reverse = spec
            .get("reverse")
            .and_then(Value::as_bool)
            .ok_or_else(unsupported)?;
        if numbers.len() != images.len() - 1 {
            return Err(unsupported());
        }
        let key = &self.sheet[&self.frame_order[column]].positions[&track];
        let key_number = key.number();
        let members: BTreeSet<_> = numbers.iter().copied().collect();
        if members.len() != numbers.len()
            || key_number.is_some_and(|number| !key.references_bank() || members.contains(&number))
            || self.frame_order.iter().any(|fid| {
                let slot = &self.sheet[fid].positions[&track];
                slot.compatibility.local_bank && slot.number().is_some_and(|n| members.contains(&n))
            })
        {
            return Err(unsupported());
        }
        let mut edited = self.clone();
        let mut changes = edited.ensure_frame_count(range.end)?;
        edited.tween_key_image(column, track, images[0].clone())?;
        let key = edited.position_mut(column, track);
        let mut revisions = usize::from(
            !matches!(key.reference, Reference::Number(_)) && !key.compatibility.unnumbered_content,
        ) + usize::from(key.reference == Reference::Blank);
        key.reference = key_number.map_or(Reference::Empty, Reference::Number);
        key.compatibility.retained_number = None;
        key.compatibility.unnumbered_content = key_number.is_none();
        key.compatibility.content_origin = None;
        if revisions != 0 {
            key.compatibility.dense_import = None;
        }
        key.tween = Some(spec.clone());
        key.tween_member = None;
        for ((index, image), &number) in
            ((column..range.end).skip(1).zip(&images[1..])).zip(numbers)
        {
            let previous = &edited.sheet[&edited.frame_order[index]].positions[&track];
            let compat = &previous.compatibility;
            if compat.local_share.is_some() {
                return Err(unsupported());
            }
            let blank = previous.reference == Reference::Blank;
            let delta = usize::from(previous.number() != Some(number))
                + usize::from(
                    !matches!(previous.reference, Reference::Number(_))
                        && !compat.unnumbered_content,
                )
                + usize::from(previous.blank_start != blank)
                + usize::from(blank)
                + usize::from(previous.pending);
            // create_drawing は今の位置の絵（previous）から初期画像と原画の印を引き継ぐ。
            let (initial_image, is_genga) =
                if let Some(old) = previous.number().filter(|_| previous.references_bank()) {
                    let cell = edited.tracks[&track]
                        .cells
                        .get(&old)
                        .ok_or_else(unsupported)?;
                    (cell.initial_image.clone(), cell.is_genga)
                } else {
                    compat.local.as_ref().map_or((None, false), |cell| {
                        (cell.initial_image.clone(), cell.is_genga)
                    })
                };
            edited
                .tracks
                .get_mut(&track)
                .ok_or_else(unsupported)?
                .cells
                .insert(
                    number,
                    Cell {
                        image: image.clone(),
                        initial_image,
                        cell_name: None,
                        is_genga,
                    },
                );
            let slot = edited.position_mut(index, track);
            slot.reference = Reference::Number(number);
            slot.blank_start = blank;
            slot.pending = false;
            slot.tween = None;
            slot.tween_member = Some(member.to_owned());
            let compat = &mut slot.compatibility;
            compat.local = None;
            compat.local_bank = false;
            compat.retained_number = None;
            compat.unnumbered_content = false;
            compat.content_origin = None;
            if delta != 0 {
                compat.dense_import = None;
            }
            revisions = revisions
                .checked_add(delta)
                .ok_or(Error::TimelineSizeOverflow)?;
        }
        let cursor = if reverse { column } else { range.end - 1 };
        changes.extend([
            span(layer, range),
            Change::Cursor {
                frame: cursor,
                notify: true,
            },
            Change::Selection { layer_index: layer },
        ]);
        *self = edited;
        Ok((changes, revisions))
    }

    /// キーの画像を差し替え、中割りをキーの参照で置き換える。
    /// 同じ形で複製できないときは文書を変更せずエラーを返す。
    pub fn reopen_tween(
        &mut self,
        column: usize,
        layer: usize,
        length: usize,
        image: RgbaImage,
    ) -> Result<(ChangeSet, usize), Error> {
        let Some((track, range)) = self.tween_range(column, layer, length)? else {
            return Ok((Vec::new(), 0));
        };
        if range.end > self.frame_order.len() {
            return Ok((Vec::new(), 0));
        }
        let head = &self.sheet[&self.frame_order[column]].positions[&track];
        if length > 1 && (!head.references_bank() || head.compatibility.local.is_some()) {
            return Err(unsupported());
        }
        let delta = usize::from(head.number().is_some())
            + usize::from(
                head.reference != Reference::Empty || head.compatibility.unnumbered_content,
            )
            + 2 * usize::from(head.blank_start)
            + usize::from(head.pending);
        let revisions = (length - 1)
            .checked_mul(delta)
            .ok_or(Error::TimelineSizeOverflow)?;
        let mut edited = self.clone();
        edited.tween_key_image(column, track, image)?;
        let head = edited.position_mut(column, track);
        head.tween = None;
        let mut held = head.clone();
        held.compatibility.pending_detached = true;
        held = held_reference(held, &self.tracks[&track].settings, self.blank_artwork()?);
        if let Some(legacy) = &mut held.compatibility.legacy {
            legacy.pending = false;
        }
        for index in range.clone().skip(1) {
            if self.sheet[&self.frame_order[index]].positions[&track]
                .compatibility
                .local_share
                .is_some()
            {
                return Err(unsupported());
            }
            *edited.position_mut(index, track) = held.clone();
        }
        *self = edited;
        Ok((vec![span(layer, range)], revisions))
    }

    /// 画像と参照を変えず、補間キーと中割りの所属だけを外す。
    pub fn release_tween(
        &mut self,
        column: usize,
        layer: usize,
        length: usize,
    ) -> Result<ChangeSet, Error> {
        let Some((track, range)) = self.tween_range(column, layer, length)? else {
            return Ok(Vec::new());
        };
        if range.end > self.frame_order.len() {
            return Ok(Vec::new());
        }
        self.position_mut(column, track).tween = None;
        for index in range.clone().skip(1) {
            self.position_mut(index, track).tween_member = None;
        }
        Ok(vec![span(layer, range)])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document::v2;
    use crate::project::payload::decode_v2_plan;
    use serde_json::json;

    #[test]
    fn tween_lifecycle_and_atomic_errors() {
        let payload = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1],
            "tracks": [{"cells": [], "sheet": [null]}],
        });
        let plan = decode_v2_plan(&payload, &[]).unwrap();
        let mut doc = v2::from_plan(&payload, &plan, &[]).unwrap();
        let track = doc.track_order[0];
        let key_number = CellNumber::new(1).unwrap();
        let blank = RgbaImage::blank(1, 1).unwrap();
        doc.tracks.get_mut(&track).unwrap().cells.insert(
            key_number,
            Cell {
                image: blank.clone(),
                initial_image: None,
                cell_name: Some("key".into()),
                is_genga: false,
            },
        );
        doc.position_mut(0, track).reference = Reference::Number(key_number);
        let images = vec![blank; 3];
        let numbers = doc.tween_numbers(0, 2).unwrap();
        let spec = json!({"id": "a", "reverse": false});
        assert!(doc
            .install_tween(0, 0, &images, spec.clone(), &[key_number, numbers[0]])
            .is_err());
        assert_eq!(doc.frame_order.len(), 1);
        let (changes, revision) = doc.install_tween(0, 0, &images, spec, &numbers).unwrap();
        assert!(matches!(
            changes[0],
            Change::FrameStructure { after: 3, .. }
        ));
        assert_eq!(changes[1], span(0, 0..3));
        assert_eq!(
            changes[2],
            Change::Cursor {
                frame: 2,
                notify: true
            }
        );
        assert_eq!(changes[3], Change::Selection { layer_index: 0 });
        assert_eq!(revision, 4);
        assert_eq!(
            doc.position_mut(1, track).tween_member.as_deref(),
            Some("a")
        );
        assert_eq!(doc.release_tween(0, 0, 3).unwrap(), vec![span(0, 0..3)]);
        assert!(doc.position_mut(1, track).tween_member.is_none());
        assert_eq!(
            doc.reopen_tween(0, 0, 3, images[0].clone()).unwrap(),
            (vec![span(0, 0..3)], 4)
        );
        assert_eq!(doc.position_mut(2, track).number(), Some(key_number));
        assert_eq!(doc.tracks[&track].cells.len(), 3);
        assert_eq!(
            doc.tracks[&track].cells[&key_number].cell_name.as_deref(),
            Some("key")
        );

        // キーを作業用の文書で変更したあとに中割りを拒否しても、
        // キーの画像や中割りのバンクは元の文書に残る。
        doc.position_mut(2, track).compatibility.local_share = Some(1);
        let before = format!("{doc:?}");
        let new_numbers = doc.tween_numbers(0, 2).unwrap();
        let spec = json!({"id": "b", "reverse": true});
        let replacement = RgbaImage::from_rgba(1, 1, &[255, 0, 0, 255]).unwrap();
        let new_images = vec![replacement.clone(); 3];
        assert!(doc
            .install_tween(0, 0, &new_images, spec, &new_numbers)
            .is_err());
        assert_eq!(format!("{doc:?}"), before);
        assert!(doc.reopen_tween(0, 0, 3, replacement).is_err());
        assert_eq!(format!("{doc:?}"), before);
    }
}
