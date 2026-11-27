use std::collections::BTreeSet;

use super::changes::{Change, ChangeSet};
use super::model::{Cell, CellNumber, Document, Reference, TrackId};
use crate::image::{Rect, RgbaImage};
use crate::Error;

/// 位置またはバンク上で絵を指定する。Python の描画操作の frame / number に対応する。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DrawingTarget {
    /// コマの表示インデックス。
    Frame(usize),
    /// 未配置を含むバンクの番号。
    Bank(CellNumber),
}

/// Python の一括画像差し替えで同じ画像を渡す位置の組。
pub struct DrawingImageGroup<'a> {
    /// 入力順の (コマ、列)。
    pub positions: &'a [(usize, usize)],
    /// COW で共有する画像。
    pub image: &'a RgbaImage,
}

fn missing() -> Error {
    Error::Invalid("対象の絵がありません".into())
}

#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Artwork {
    Bank(CellNumber),
    Local(usize),
}

impl Document {
    fn artwork_target(
        &self,
        track: TrackId,
        target: DrawingTarget,
    ) -> Result<(Artwork, Option<CellNumber>, Option<usize>), Error> {
        match target {
            DrawingTarget::Bank(number) => Ok((Artwork::Bank(number), Some(number), None)),
            DrawingTarget::Frame(frame) => {
                let id = self.frame_order.get(frame).ok_or_else(missing)?;
                let position = &self.sheet[id].positions[&track];
                let number = position.number();
                let artwork = number
                    .filter(|_| {
                        position.pending || matches!(position.reference, Reference::Number(_))
                    })
                    .map_or(Artwork::Local(frame), Artwork::Bank);
                Ok((artwork, number, Some(frame)))
            }
        }
    }

    fn artwork_mut(&mut self, track: TrackId, target: Artwork) -> Option<&mut Cell> {
        match target {
            Artwork::Bank(number) => self.tracks.get_mut(&track)?.cells.get_mut(&number),
            Artwork::Local(frame) => self.position_mut(frame, track).compatibility.local.as_mut(),
        }
    }

    /// Python の write_drawing_patch と同様に、位置またはバンクの RGBA 矩形を確定する。
    pub fn write_drawing_patch(
        &mut self,
        layer_index: usize,
        target: DrawingTarget,
        rect: Rect,
        rgba: &[u8],
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(missing)?;
        let (artwork, number, frame) = self.artwork_target(track, target)?;
        if let Some(cell) = self.artwork_mut(track, artwork) {
            cell.image.write_rgba(rect, rgba)?;
        } else if let Artwork::Local(frame) = artwork {
            let mut image = RgbaImage::blank(self.width, self.height)?;
            image.write_rgba(rect, rgba)?;
            self.position_mut(frame, track).compatibility.local = Some(Cell {
                image,
                initial_image: None,
                cell_name: None,
                is_genga: false,
            });
        } else {
            return Err(missing());
        }
        Ok(vec![Change::DrawingPixels {
            layer_index,
            number,
            frame,
            rects: vec![rect],
        }])
    }

    /// Python の write_drawing_patch と同じ順で矩形と絵の有無を確定する。
    /// 表現できない位置や無効な矩形はエラーとし、画素も変更しない。
    pub fn write_drawing_patch_with_content(
        &mut self,
        frame: usize,
        layer_index: usize,
        rect: Rect,
        rgba: &[u8],
        has_content: bool,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(missing)?;
        let target = DrawingTarget::Frame(frame);
        let (_, number, _) = self.artwork_target(track, target)?;
        let position = &self.sheet[&self.frame_order[frame]].positions[&track];
        let compatibility = &position.compatibility;
        if position.pending
            || compatibility.local_share.is_some()
            || compatibility.local_bank
            || (has_content && position.reference == Reference::Blank)
            || (!has_content
                && position.blank_start
                && matches!(position.reference, Reference::Number(_)))
        {
            return Err(Error::Invalid(
                "位置の内容の印を同じ形で書き込めません".into(),
            ));
        }
        self.validate_drawing_content(frame, track, number, has_content)?;
        let changes = self.write_drawing_patch(layer_index, target, rect, rgba)?;
        self.update_drawing_content(frame, track, number, has_content)?;
        if has_content
            && self.sheet[&self.frame_order[frame]].positions[&track]
                .compatibility
                .content_origin
                .is_some()
        {
            self.position_mut(frame, track).compatibility.content_origin = None;
        }
        Ok(changes)
    }

    /// Python の replace_drawing_image に対応し、既存の絵の全画素を差し替える。
    /// 位置の参照属性は維持する。
    pub fn replace_drawing_pixels(
        &mut self,
        layer_index: usize,
        target: DrawingTarget,
        image: RgbaImage,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(missing)?;
        let (artwork, number, frame) = self.artwork_target(track, target)?;
        self.artwork_mut(track, artwork).ok_or_else(missing)?.image = image;
        Ok(vec![Change::DrawingPixels {
            layer_index,
            number,
            frame,
            rects: Vec::new(),
        }])
    }

    /// Python の replace_drawing_image と同じ順で、画素と位置の絵の有無を確定する。
    pub fn replace_drawing_image(
        &mut self,
        frame: usize,
        layer_index: usize,
        image: RgbaImage,
        has_content: bool,
    ) -> Result<ChangeSet, Error> {
        let mut edited = self.clone();
        let change = edited.replace_image(frame, layer_index, image, has_content)?;
        *self = edited;
        Ok(vec![change])
    }

    /// Python の replace_drawing_images と同じ順で差し替える。失敗時は全組を取り消す。
    pub fn replace_drawing_images(
        &mut self,
        groups: &[DrawingImageGroup<'_>],
    ) -> Result<ChangeSet, Error> {
        let mut edited = self.clone();
        let mut changes = Vec::new();
        for group in groups {
            for &(frame, layer_index) in group.positions {
                changes.push(edited.replace_image(
                    frame,
                    layer_index,
                    group.image.clone(),
                    true,
                )?);
            }
        }
        *self = edited;
        Ok(changes)
    }

    fn replace_image(
        &mut self,
        frame: usize,
        layer_index: usize,
        image: RgbaImage,
        has_content: bool,
    ) -> Result<Change, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(missing)?;
        let (artwork, number, _) = self.artwork_target(track, DrawingTarget::Frame(frame))?;
        let position = &self.sheet[&self.frame_order[frame]].positions[&track];
        let current = matches!(position.reference, Reference::Number(_))
            || position.compatibility.unnumbered_content;
        if (has_content && matches!(position.reference, Reference::Blank))
            || (!has_content && current && position.blank_start)
            || position.compatibility.local_share.is_some()
            || position.compatibility.local_bank
        {
            return Err(Error::Invalid("画像を差し替えられない位置です".into()));
        }
        if let Some(cell) = self.artwork_mut(track, artwork) {
            cell.image = image;
        } else if let Artwork::Local(_) = artwork {
            self.position_mut(frame, track).compatibility.local = Some(Cell {
                image,
                initial_image: None,
                cell_name: None,
                is_genga: false,
            });
        } else {
            return Err(missing());
        }
        self.update_drawing_content(frame, track, number, has_content)?;
        if has_content {
            self.position_mut(frame, track).compatibility.content_origin = None;
        }
        Ok(Change::DrawingPixels {
            layer_index,
            number,
            frame: Some(frame),
            rects: Vec::new(),
        })
    }

    /// Python の set_drawing_content と同様に、指定位置の絵の有無を更新する。
    pub fn set_drawing_content(
        &mut self,
        frame: usize,
        layer_index: usize,
        has_content: bool,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(missing)?;
        let (_, number, _) = self.artwork_target(track, DrawingTarget::Frame(frame))?;
        self.update_drawing_content(frame, track, number, has_content)?;
        Ok(vec![Change::DrawingAttributes {
            layer_index,
            number,
            frame: Some(frame),
            fields: vec!["has_content"],
        }])
    }

    /// 履歴の復元で位置の内容の印を代入する。Python と同じ値にできない場合は何も変えずエラーにする。
    pub fn restore_position_content(
        &mut self,
        frame: usize,
        layer_index: usize,
        has_content: bool,
    ) -> Result<(), Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(missing)?;
        let (_, number, _) = self.artwork_target(track, DrawingTarget::Frame(frame))?;
        let position = &self.sheet[&self.frame_order[frame]].positions[&track];
        let compatibility = &position.compatibility;
        let current =
            matches!(position.reference, Reference::Number(_)) || compatibility.unnumbered_content;
        if current == has_content {
            if has_content && compatibility.content_origin.is_some() {
                self.position_mut(frame, track).compatibility.content_origin = None;
            }
            return Ok(());
        }
        if position.pending
            || compatibility.local_share.is_some()
            || compatibility.local_bank
            || (has_content && position.reference == Reference::Blank)
            || (!has_content && position.blank_start && number.is_some())
        {
            return Err(Error::Invalid("位置の内容の印を同じ形で戻せません".into()));
        }
        self.update_drawing_content(frame, track, number, has_content)
    }

    fn validate_drawing_content(
        &self,
        frame: usize,
        track: TrackId,
        number: Option<CellNumber>,
        has_content: bool,
    ) -> Result<(), Error> {
        let position = &self.sheet[&self.frame_order[frame]].positions[&track];
        let numbered = matches!(position.reference, Reference::Number(_));
        let changes_content =
            has_content != (numbered || position.compatibility.unnumbered_content);
        if let Some(number) = number.filter(|_| changes_content && (has_content || numbered)) {
            if !self.tracks[&track].cells.contains_key(&number) {
                return Err(missing());
            }
        }
        Ok(())
    }

    fn update_drawing_content(
        &mut self,
        frame: usize,
        track: TrackId,
        number: Option<CellNumber>,
        has_content: bool,
    ) -> Result<(), Error> {
        let position = &self.sheet[&self.frame_order[frame]].positions[&track];
        let numbered = matches!(position.reference, Reference::Number(_));
        if has_content == (numbered || position.compatibility.unnumbered_content) {
            return Ok(());
        }
        let artwork = if let Some(number) = number.filter(|_| has_content || numbered) {
            Some(
                self.tracks[&track]
                    .cells
                    .get(&number)
                    .ok_or_else(missing)?
                    .clone(),
            )
        } else {
            None
        };
        let position = self.position_mut(frame, track);
        position.compatibility.dense_import = None;
        position.compatibility.unnumbered_content = has_content && number.is_none();
        if has_content {
            // Python の Layer.__setattr__ は内容の印を立てるとき _content_origin を外す。
            position.compatibility.content_origin = None;
            if let Some(number) = number {
                position.reference = Reference::Number(number);
            }
        } else if numbered {
            position.reference = if position.blank_start {
                Reference::Blank
            } else {
                Reference::Empty
            };
            position.compatibility.retained_number = number;
            position.compatibility.local = artwork;
            // 内容を外すときは、外す前に参照していた番号を残す（Python の _content_origin）。
            position.compatibility.content_origin = number;
        }
        Ok(())
    }

    /// Python の set_initial_images と同様に指定順で初期画像を設定する。
    pub fn set_initial_images(
        &mut self,
        updates: &[(usize, DrawingTarget, Option<RgbaImage>)],
        restoring: bool,
    ) -> Result<ChangeSet, Error> {
        self.set_initial_images_with_blank(updates, restoring, None)
    }

    /// 位置、保管の順で初期画像を設定する。履歴復元では同じ絵の最初の指定を採用する。
    /// 局所の絵がまだ無ければ、余白を含む Python の空画像を使う。
    /// 局所の共有を含む更新はエラーとし、文書を変更しない。
    pub fn set_initial_images_with_blank(
        &mut self,
        updates: &[(usize, DrawingTarget, Option<RgbaImage>)],
        restoring: bool,
        blank: Option<&RgbaImage>,
    ) -> Result<ChangeSet, Error> {
        let mut edited = self.clone();
        let mut seen = BTreeSet::new();
        let mut changes = Vec::new();
        for (layer_index, target, image) in updates {
            let Some(&track) = edited.track_order.get(*layer_index) else {
                continue;
            };
            let Ok((artwork, number, frame)) = edited.artwork_target(track, *target) else {
                continue;
            };
            if let DrawingTarget::Bank(number) = target {
                let bank = edited.tracks.get(&track).ok_or_else(missing)?;
                if !bank.legacy_archive_hints.contains_key(number)
                    || !bank.cells.contains_key(number)
                {
                    continue;
                }
            }
            if let Artwork::Local(column) = artwork {
                let position = edited
                    .frame_order
                    .get(column)
                    .and_then(|id| edited.sheet.get(id))
                    .and_then(|row| row.positions.get(&track))
                    .ok_or_else(missing)?;
                if position.compatibility.local_share.is_some() || position.compatibility.local_bank
                {
                    return Err(Error::Invalid("共有された局所の絵です".into()));
                }
            }
            if restoring && !seen.insert((track, artwork)) {
                continue;
            }
            if let Some(cell) = edited.artwork_mut(track, artwork) {
                cell.initial_image.clone_from(image);
            } else if let Artwork::Local(column) = artwork {
                let image_to_keep = match blank {
                    Some(blank) => blank.clone(),
                    None => RgbaImage::blank(edited.width, edited.height)?,
                };
                let position = edited.position_mut(column, track);
                position.compatibility.local = Some(Cell {
                    image: image_to_keep,
                    initial_image: image.clone(),
                    cell_name: None,
                    is_genga: false,
                });
            } else {
                return Err(missing());
            }
            changes.push(Change::DrawingAttributes {
                layer_index: *layer_index,
                number,
                frame,
                fields: vec!["initial_image"],
            });
        }
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

    #[test]
    fn initial_images_restore_once_and_reject_shared_locals_atomically() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1, 1],
            "tracks": [{"cells": [{"number": 1, "image": "one.png"}],
                        "sheet": [1, 1, null]}],
        });
        let plan = decode_v2_plan(&input, &["one.png".into()]).unwrap();
        let image = RgbaImage::blank(1, 1).unwrap();
        let mut doc = v2::from_plan(&input, &plan, std::slice::from_ref(&image)).unwrap();
        let number = CellNumber::new(1).unwrap();
        let track = doc.track_order()[0];
        doc.tracks
            .get_mut(&track)
            .unwrap()
            .legacy_archive_hints
            .insert(
                number,
                super::super::compatibility::ArchiveHint {
                    has_content: true,
                    is_blank_key: false,
                    exposure: 1,
                    pending: false,
                    tween: None,
                    tween_member: None,
                },
            );
        let updates = [
            (0, DrawingTarget::Frame(0), Some(image)),
            (0, DrawingTarget::Frame(1), None),
            (0, DrawingTarget::Bank(number), None),
        ];
        assert_eq!(
            doc.set_initial_images(&updates, true).unwrap(),
            [Change::DrawingAttributes {
                layer_index: 0,
                number: Some(number),
                frame: Some(0),
                fields: vec!["initial_image"],
            }]
        );
        assert!(doc.tracks[&track].cells[&number].initial_image.is_some());
        assert_eq!(doc.set_initial_images(&updates, false).unwrap().len(), 3);
        assert!(doc.tracks[&track].cells[&number].initial_image.is_none());

        doc.position_mut(2, track).compatibility.local_share = Some(1);
        let update = (
            0,
            DrawingTarget::Bank(number),
            Some(RgbaImage::blank(1, 1).unwrap()),
        );
        assert!(doc
            .set_initial_images(&[update, (0, DrawingTarget::Frame(2), None)], false)
            .is_err());
        assert!(doc.tracks[&track].cells[&number].initial_image.is_none());
        assert!(doc.position_mut(2, track).compatibility.local.is_none());

        doc.position_mut(2, track).compatibility.local_share = None;
        let blank = RgbaImage::blank(3, 2).unwrap();
        doc.set_initial_images_with_blank(
            &[(0, DrawingTarget::Frame(2), None)],
            false,
            Some(&blank),
        )
        .unwrap();
        let cell = doc
            .position_mut(2, track)
            .compatibility
            .local
            .as_ref()
            .unwrap();
        assert_eq!(cell.image.generation(), blank.generation());
    }

    // tests/test_core_document_compare.py:test_review_patch_and_replace_pixels
    #[test]
    fn a_patch_creates_local_artwork_only_after_validation() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1],
            "tracks": [{"cells": [], "sheet": [null]}],
        });
        let plan = decode_v2_plan(&input, &[]).unwrap();
        let mut doc = v2::from_plan(&input, &plan, &[]).unwrap();
        let track = doc.track_order[0];
        let target = DrawingTarget::Frame(0);
        let rect = Rect {
            x: 0,
            y: 0,
            width: 1,
            height: 1,
        };
        assert!(doc.write_drawing_patch(0, target, rect, &[255]).is_err());
        assert!(doc.position_mut(0, track).compatibility.local.is_none());

        let pixel = [33, 44, 55, 255];
        assert_eq!(
            doc.write_drawing_patch(0, target, rect, &pixel).unwrap(),
            [Change::DrawingPixels {
                layer_index: 0,
                number: None,
                frame: Some(0),
                rects: vec![rect]
            }]
        );
        let position = doc.position_mut(0, track);
        assert_eq!(position.reference, Reference::Empty);
        assert_eq!(
            position.compatibility.local.as_ref().unwrap().image.rgba(),
            pixel
        );
        let original = position
            .compatibility
            .local
            .as_ref()
            .unwrap()
            .image
            .rgba()
            .as_ptr();
        let replacement = [77, 88, 99, 255];
        doc.write_drawing_patch_with_content(0, 0, rect, &replacement, true)
            .unwrap();
        let position = doc.position_mut(0, track);
        let image = &position.compatibility.local.as_ref().unwrap().image;
        assert_eq!(image.rgba().as_ptr(), original);
        assert_eq!(image.rgba(), replacement);
    }

    // tests/test_document_live_fast_paths.py:test_set_drawing_content_uses_core_command
    #[test]
    fn clearing_content_records_the_former_number_and_setting_it_forgets() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1],
            "tracks": [{"cells": [{"number": 3, "image": "3.png"}], "sheet": [3, 3]}],
        });
        let plan = decode_v2_plan(&input, &["3.png".into()]).unwrap();
        let blank = RgbaImage::blank(1, 1).unwrap();
        let mut doc = v2::from_plan(&input, &plan, std::slice::from_ref(&blank)).unwrap();
        let track = doc.track_order()[0];
        doc.set_drawing_content(1, 0, false).unwrap();
        let position = doc.position_mut(1, track);
        assert_eq!(position.reference, Reference::Empty);
        assert_eq!(
            position.compatibility.content_origin.map(CellNumber::get),
            Some(3)
        );
        assert_eq!(position.number().map(CellNumber::get), Some(3));
        assert!(position.compatibility.local.is_some());
        doc.set_drawing_content(1, 0, true).unwrap();
        let position = doc.position_mut(1, track);
        assert!(matches!(position.reference, Reference::Number(_)));
        assert_eq!(position.compatibility.content_origin, None);
    }

    #[test]
    fn restoring_batch_content_copies_the_current_bank_and_rejects_unrepresentable_positions() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1],
            "tracks": [{"cells": [{"number": 3, "image": "3.png"}], "sheet": [3, 3]}],
        });
        let plan = decode_v2_plan(&input, &["3.png".into()]).unwrap();
        let pixels = [3, 5, 7, 255];
        let image = RgbaImage::from_rgba(1, 1, &pixels).unwrap();
        let mut doc = v2::from_plan(&input, &plan, &[image]).unwrap();
        let track = doc.track_order()[0];
        doc.position_mut(1, track).blank_start = true;
        let before = doc.graph_fingerprints();
        assert!(doc.restore_position_content(1, 0, false).is_err());
        assert!(doc.restore_position_content(2, 0, false).is_err());
        assert_eq!(doc.graph_fingerprints(), before);

        doc.restore_position_content(0, 0, false).unwrap();
        let position = doc.position_mut(0, track);
        assert_eq!(position.reference, Reference::Empty);
        assert_eq!(
            position.compatibility.content_origin,
            CellNumber::new(3).ok()
        );
        assert_eq!(
            position.compatibility.local.as_ref().unwrap().image.rgba(),
            pixels
        );
        assert_eq!(doc.graph_fingerprints().positions[1], before.positions[1]);
        doc.restore_position_content(0, 0, true).unwrap();
        let position = doc.position_mut(0, track);
        assert_eq!(
            position.reference,
            Reference::Number(CellNumber::new(3).unwrap())
        );
        assert_eq!(position.compatibility.content_origin, None);
        assert_eq!(
            position.compatibility.local.as_ref().unwrap().image.rgba(),
            pixels
        );
    }

    #[test]
    fn patch_rejects_incompatible_content_before_touching_pixels() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1],
            "tracks": [{"cells": [{"number": 3, "image": "3.png"}], "sheet": [3, null]}],
        });
        let plan = decode_v2_plan(&input, &["3.png".into()]).unwrap();
        let blank = RgbaImage::blank(1, 1).unwrap();
        let mut doc = v2::from_plan(&input, &plan, std::slice::from_ref(&blank)).unwrap();
        let track = doc.track_order()[0];
        doc.position_mut(0, track).blank_start = true;
        doc.position_mut(1, track).reference = Reference::Blank;
        let before = doc.clone();
        let rect = Rect {
            x: 0,
            y: 0,
            width: 1,
            height: 1,
        };
        let pixel = [33, 44, 55, 255];
        assert!(doc
            .write_drawing_patch_with_content(0, 0, rect, &pixel, false)
            .is_err());
        assert!(doc
            .write_drawing_patch_with_content(1, 0, rect, &pixel, true)
            .is_err());
        assert_eq!(doc.graph_fingerprints(), before.graph_fingerprints());
    }

    // tests/test_core_document_compare.py:test_patch_content_and_replace_group
    #[test]
    fn patch_and_image_groups_commit_atomically_in_input_order() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1, 1],
            "tracks": [{"cells": [{"number": 3, "image": "3.png"}], "sheet": [3, 3, 3]}],
        });
        let plan = decode_v2_plan(&input, &["3.png".into()]).unwrap();
        let blank = RgbaImage::blank(1, 1).unwrap();
        let mut doc = v2::from_plan(&input, &plan, std::slice::from_ref(&blank)).unwrap();
        let number = CellNumber::new(3).unwrap();
        let track = doc.track_order()[0];
        let rect = Rect {
            x: 0,
            y: 0,
            width: 1,
            height: 1,
        };
        let pixel = [33, 44, 55, 255];
        assert!(doc
            .write_drawing_patch_with_content(0, 0, rect, &[], false)
            .is_err());
        assert_eq!(doc.display_spans(0, &[0]).unwrap(), [Some(0..3)]);
        assert_eq!(
            doc.track(track).unwrap().cells[&number].image.rgba(),
            blank.rgba()
        );
        assert_eq!(
            doc.write_drawing_patch_with_content(0, 0, rect, &pixel, false)
                .unwrap(),
            [Change::DrawingPixels {
                layer_index: 0,
                number: Some(number),
                frame: Some(0),
                rects: vec![rect]
            }],
        );
        assert_eq!(doc.display_spans(0, &[0]).unwrap(), [None]);
        assert_eq!(doc.track(track).unwrap().cells[&number].image.rgba(), pixel);

        let image = RgbaImage::from_rgba(1, 1, &[20, 100, 30, 255]).unwrap();
        let group = DrawingImageGroup {
            positions: &[(2, 0), (1, 0)],
            image: &image,
        };
        let invalid = DrawingImageGroup {
            positions: &[(3, 0)],
            image: &image,
        };
        assert!(doc.replace_drawing_images(&[group, invalid]).is_err());
        assert_eq!(doc.track(track).unwrap().cells[&number].image.rgba(), pixel);
        let group = DrawingImageGroup {
            positions: &[(2, 0), (1, 0)],
            image: &image,
        };
        assert_eq!(
            doc.replace_drawing_images(&[group]).unwrap(),
            [2, 1].map(|frame| Change::DrawingPixels {
                layer_index: 0,
                number: Some(number),
                frame: Some(frame),
                rects: Vec::new(),
            }),
        );
        assert_eq!(
            doc.track(track).unwrap().cells[&number].image.rgba(),
            image.rgba()
        );
        assert_eq!(doc.display_spans(0, &[0]).unwrap(), [None]);
    }

    // tests/test_core_document_compare.py:test_replace_drawing_image
    // tests/test_document_drawing_ops.py:test_numbered_patch_changes_shared_pixels
    #[test]
    fn replacing_then_clearing_content_keeps_the_committed_pixels_in_the_bank() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1],
            "tracks": [{"cells": [{"number": 3, "image": "3.png"}], "sheet": [3, 3]}],
        });
        let plan = decode_v2_plan(&input, &["3.png".into()]).unwrap();
        let blank = RgbaImage::blank(1, 1).unwrap();
        let mut doc = v2::from_plan(&input, &plan, std::slice::from_ref(&blank)).unwrap();
        let image = RgbaImage::from_rgba(1, 1, &[33, 44, 55, 255]).unwrap();
        let number = CellNumber::new(3).unwrap();
        let track = doc.track_order()[0];
        assert_eq!(
            doc.replace_drawing_image(0, 0, image.clone(), false)
                .unwrap(),
            [Change::DrawingPixels {
                layer_index: 0,
                frame: Some(0),
                number: Some(number),
                rects: Vec::new()
            }]
        );
        assert!(doc.replace_drawing_image(2, 0, blank, true).is_err());
        assert_eq!(
            doc.track(track).unwrap().cells[&number].image.rgba(),
            image.rgba()
        );
        assert_eq!(doc.display_spans(0, &[0]).unwrap(), [None]);
        let slot = doc.position_mut(0, track);
        assert_eq!(slot.number(), Some(number));
        assert_eq!(
            slot.compatibility.local.as_ref().unwrap().image.rgba(),
            image.rgba()
        );
    }

    #[test]
    fn image_replacement_clears_content_origin_and_rejects_blank_keys_atomically() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1],
            "tracks": [{"cells": [{"number": 3, "image": "3.png"}], "sheet": [3, "BLANK"]}],
        });
        let plan = decode_v2_plan(&input, &["3.png".into()]).unwrap();
        let blank = RgbaImage::blank(1, 1).unwrap();
        let mut doc = v2::from_plan(&input, &plan, &[blank]).unwrap();
        let track = doc.track_order()[0];
        let number = CellNumber::new(3).unwrap();
        doc.position_mut(0, track).compatibility.content_origin = Some(number);
        let image = RgbaImage::from_rgba(1, 1, &[33, 44, 55, 255]).unwrap();
        let before = doc.graph_fingerprints();
        let group = DrawingImageGroup {
            positions: &[(0, 0), (1, 0)],
            image: &image,
        };
        assert!(doc.replace_drawing_images(&[group]).is_err());
        assert_eq!(doc.graph_fingerprints(), before);
        assert_eq!(
            doc.position_mut(0, track).compatibility.content_origin,
            Some(number)
        );

        doc.replace_drawing_image(0, 0, image.clone(), true)
            .unwrap();
        assert_eq!(
            doc.position_mut(0, track).compatibility.content_origin,
            None
        );
        assert_eq!(
            doc.track(track).unwrap().cells[&number].image.rgba(),
            image.rgba()
        );
    }

    // tests/test_pending_slots.py:test_temporary_slot_copy_registers_only_on_attach_and_clear_keeps_bank
    // tests/test_cell_attributes.py:test_drawing_does_not_revive_blank_or_unused_with_same_number
    #[test]
    fn pending_blank_addresses_bank_but_retained_number_does_not() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1],
            "tracks": [{
                "cells": [{"number": 3, "image": "3.png"}],
                "sheet": [null, "BLANK"],
                "pending": [{"column": 0, "number": 3, "kind": "BLANK", "length": 1}],
                "compatibility": {"local_states": [{"column": 1, "retained_number": 3}]},
            }],
        });
        let plan = decode_v2_plan(&input, &["3.png".into()]).unwrap();
        let image = RgbaImage::blank(1, 1).unwrap();
        let mut doc = v2::from_plan(&input, &plan, &[image]).unwrap();
        let pixels = [17, 34, 51, 255];
        let rect = Rect {
            x: 0,
            y: 0,
            width: 1,
            height: 1,
        };
        let number = CellNumber::new(3).unwrap();
        assert_eq!(
            doc.write_drawing_patch(0, DrawingTarget::Frame(0), rect, &pixels)
                .unwrap(),
            [Change::DrawingPixels {
                layer_index: 0,
                number: Some(number),
                frame: Some(0),
                rects: vec![rect],
            }]
        );
        doc.set_initial_images(
            &[(
                0,
                DrawingTarget::Frame(1),
                Some(RgbaImage::blank(1, 1).unwrap()),
            )],
            false,
        )
        .unwrap();
        doc.write_drawing_patch(0, DrawingTarget::Frame(1), rect, &[255, 0, 0, 255])
            .unwrap();
        let track = doc.track_order()[0];
        assert_eq!(
            doc.track(track).unwrap().cells[&number].image.rgba(),
            pixels
        );
        assert!(doc.track(track).unwrap().cells[&number]
            .initial_image
            .is_none());
        assert!(doc.placed_numbers(track).is_empty());
    }

    // tests/test_document_drawing_ops.py:test_numbered_patch_changes_shared_pixels
    // tests/test_new_number_here.py:test_middle_preserves_the_original_drawing_and_detaches_the_new_one
    // tests/test_undo_unit_characterization.py:test_repeated_number
    #[test]
    fn a_patch_updates_shared_artwork_and_detaching_keeps_the_original() {
        let payload = json!({
            "canvas": {"width": 5, "height": 4},
            "frame_durations": [1, 1, 1, 1, 1],
            "tracks": [
                {"cells": [{"number": 7, "image": "7.png"}], "sheet": [7, 7, 7, null, 7]},
                {"cells": [{"number": 7, "image": "7.png"}], "sheet": [7, null, null, null, null]},
            ],
        });
        let plan = decode_v2_plan(&payload, &["7.png".into()]).unwrap();
        let images = vec![RgbaImage::blank(5, 4).unwrap(); plan.images.len()];
        let mut doc = v2::from_plan(&payload, &plan, &images).unwrap();
        let source = CellNumber::new(7).unwrap();
        let separate = CellNumber::new(8).unwrap();
        let rect = Rect {
            x: 2,
            y: 1,
            width: 1,
            height: 1,
        };
        assert!(doc
            .write_drawing_patch(4, DrawingTarget::Frame(0), rect, &[255, 17, 0, 255])
            .is_err());
        assert_eq!(
            doc.write_drawing_patch(0, DrawingTarget::Frame(4), rect, &[255, 17, 0, 255])
                .unwrap(),
            [Change::DrawingPixels {
                layer_index: 0,
                number: Some(source),
                frame: Some(4),
                rects: vec![rect]
            }]
        );
        assert_eq!(
            doc.new_sheet_number(1, 0).unwrap(),
            [
                Change::SheetSpans {
                    layer_index: 0,
                    start: 0,
                    stop: 3
                },
                Change::Cursor {
                    frame: 1,
                    notify: true
                },
                Change::Selection { layer_index: 0 },
            ]
        );
        let track = doc.track_order()[0];
        let other = doc.track_order()[1];
        let initial = doc.track(track).unwrap().cells[&source].image.clone();
        assert_eq!(
            doc.track(track).unwrap().cells[&separate]
                .image
                .generation(),
            initial.generation()
        );
        doc.set_initial_images(
            &[(0, DrawingTarget::Frame(1), Some(initial.clone()))],
            false,
        )
        .unwrap();
        assert!(doc
            .write_drawing_patch(0, DrawingTarget::Frame(1), rect, &[])
            .is_err());
        assert_eq!(
            doc.track(track).unwrap().cells[&separate]
                .image
                .generation(),
            initial.generation()
        );
        doc.write_drawing_patch(0, DrawingTarget::Frame(1), rect, &[2, 7, 199, 255])
            .unwrap();
        let bank = &doc.track(track).unwrap().cells;
        assert_eq!(
            bank[&source].image.copy_rgba_rect(rect).unwrap(),
            [255, 17, 0, 255]
        );
        assert_eq!(
            bank[&separate]
                .initial_image
                .as_ref()
                .unwrap()
                .copy_rgba_rect(rect)
                .unwrap(),
            [255, 17, 0, 255]
        );
        assert_eq!(
            bank[&separate].image.copy_rgba_rect(rect).unwrap(),
            [2, 7, 199, 255]
        );
        assert_eq!(
            doc.track(other).unwrap().cells[&source]
                .image
                .copy_rgba_rect(rect)
                .unwrap(),
            [0; 4]
        );
    }
}
