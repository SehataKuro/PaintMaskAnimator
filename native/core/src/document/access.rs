//! Python の文書窓口（Layer・Track・Cell の窓口）が使う、ID で引く読み書き。
//!
//! 値の形は graph.rs の from_graph / to_graph と同じ JSON。窓口は今の Python の
//! 副作用を Python 側で再現し、保存先としてここを使う。

use serde_json::Value;

use super::graph::{self, Output};
use super::model::{Cell, CellNumber, Document, FrameId, Position, Track, TrackId};
use crate::{image::RgbaImage, Error};

fn missing(what: &str) -> Error {
    Error::Invalid(format!("文書にない{what}"))
}

/// 窓口の画像が指す絵。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ArtworkSlot {
    /// 列のバンクにある番号付きの絵。
    Bank(TrackId, CellNumber),
    /// バンクを参照しない位置の局所の絵。
    Local(FrameId, TrackId),
}

/// 絵のどちらの画像か。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ImageField {
    /// 描画中の画像。
    Image,
    /// 初期画像。
    Initial,
}

impl Document {
    /// コマ ID の表示インデックスを返す。
    pub fn frame_index(&self, id: FrameId) -> Option<usize> {
        self.frame_order.iter().position(|&other| other == id)
    }

    /// 列 ID の表示インデックスを返す。
    pub fn track_index(&self, id: TrackId) -> Option<usize> {
        self.track_order.iter().position(|&other| other == id)
    }

    fn position_ref(&self, frame: FrameId, track: TrackId) -> Result<&Position, Error> {
        self.sheet
            .get(&frame)
            .and_then(|row| row.positions.get(&track))
            .ok_or_else(|| missing("位置"))
    }

    fn track_mut(&mut self, track: TrackId) -> Result<&mut Track, Error> {
        self.tracks.get_mut(&track).ok_or_else(|| missing("列"))
    }

    /// 一つの位置を to_graph の位置と同じ JSON と画像列で返す。
    pub fn position_graph(
        &self,
        frame: FrameId,
        track: TrackId,
    ) -> Result<(Value, Vec<RgbaImage>), Error> {
        let mut output = Output::default();
        let value = graph::position_json(self.position_ref(frame, track)?, &mut output);
        Ok((value, output.images))
    }

    /// 一つの位置を from_graph の位置と同じ JSON で置き換える。
    ///
    /// バンクにない番号を参照する値はエラー。失敗時は変えない。
    pub fn set_position_graph(
        &mut self,
        frame: FrameId,
        track: TrackId,
        value: &Value,
        images: &[RgbaImage],
    ) -> Result<(), Error> {
        let position = graph::position_value(value, images)?;
        graph::check_bank_reference(
            &position,
            self.tracks.get(&track).ok_or_else(|| missing("列"))?,
        )?;
        let slot = self
            .sheet
            .get_mut(&frame)
            .and_then(|row| row.positions.get_mut(&track))
            .ok_or_else(|| missing("位置"))?;
        *slot = position;
        Ok(())
    }

    /// Python の restore（clone_frame_graph で写した文書）と同じ値で、写しの文書を戻す。
    ///
    /// clone_frame_graph は位置を clone_slot で写して列へつなぎ直し（配置待ちの所属は列が
    /// 持つ）、バンクを参照する位置の使われない局所の絵は写さない。画素は共有する。
    pub fn restore_from(&mut self, snapshot: &Document) {
        let blank = self.blank.take();
        *self = snapshot.clone();
        self.blank = blank.or_else(|| self.blank.take());
        for position in self
            .sheet
            .values_mut()
            .flat_map(|row| row.positions.values_mut())
        {
            position.compatibility.pending_detached = false;
            if position.references_bank() {
                position.compatibility.local = None;
                position.compatibility.local_share = None;
                position.compatibility.local_bank = false;
            }
        }
        self.drop_single_shares();
    }

    /// 命令が作る空画像の雛形を設定する（Python の blank_model_image() の画像）。
    pub fn set_blank_image(&mut self, image: RgbaImage) {
        self.blank = Some(image);
    }

    /// 指定したコマの行を to_graph のコマと同じ JSON で、コマ・列の並びと次の ID とともに返す。
    pub fn frames_graph(&self, frames: &[FrameId]) -> Result<(Value, Vec<RgbaImage>), Error> {
        let mut output = Output::default();
        let rows = frames
            .iter()
            .map(|id| {
                let row = self.sheet.get(id).ok_or_else(|| missing("コマ"))?;
                let positions: Vec<_> = self
                    .track_order
                    .iter()
                    .map(|track| graph::position_json(&row.positions[track], &mut output))
                    .collect();
                Ok(serde_json::json!({"id": id.0, "duration": row.duration, "positions": positions}))
            })
            .collect::<Result<Vec<_>, Error>>()?;
        let value = serde_json::json!({
            "frame_order": self.frame_order.iter().map(|id| id.0).collect::<Vec<_>>(),
            "track_order": self.track_order.iter().map(|id| id.0).collect::<Vec<_>>(),
            "next_frame_id": self.next_frame_id,
            "next_track_id": self.next_track_id,
            "frames": rows,
        });
        Ok((value, output.images))
    }

    /// 列の設定・バンク・旧連番保管を to_graph の列と同じ JSON と画像列で返す。
    pub fn track_graph(&self, track: TrackId) -> Result<(Value, Vec<RgbaImage>), Error> {
        let mut output = Output::default();
        let value = graph::track_json(
            track,
            self.tracks.get(&track).ok_or_else(|| missing("列"))?,
            &mut output,
        );
        Ok((value, output.images))
    }

    /// 列の表示設定を from_graph の settings と同じ JSON で置き換える。
    pub fn set_track_settings_graph(&mut self, track: TrackId, value: &Value) -> Result<(), Error> {
        let settings = graph::settings_value(value)?;
        self.track_mut(track)?.settings = settings;
        Ok(())
    }

    /// バンクの一枚を置き換える。`value` が null なら取り除く。
    pub fn set_cell_graph(
        &mut self,
        track: TrackId,
        number: CellNumber,
        value: &Value,
        images: &[RgbaImage],
    ) -> Result<(), Error> {
        let cell = graph::optional_cell_value(value, images)?;
        let cells = &mut self.track_mut(track)?.cells;
        match cell {
            Some(cell) => {
                cells.insert(number, cell);
            }
            None => {
                cells.remove(&number);
            }
        }
        Ok(())
    }

    /// 旧連番保管の一件を置き換える。`value` が null なら取り除く。
    pub fn set_hint_graph(
        &mut self,
        track: TrackId,
        number: CellNumber,
        value: &Value,
    ) -> Result<(), Error> {
        let hint = if value.is_null() {
            None
        } else {
            Some(graph::hint_value(value)?)
        };
        let hints = &mut self.track_mut(track)?.legacy_archive_hints;
        match hint {
            Some(hint) => {
                hints.insert(number, hint);
            }
            None => {
                hints.remove(&number);
            }
        }
        Ok(())
    }

    /// 現在コマと選択列を表示インデックスで設定する。範囲外ならエラーにして変えない。
    pub fn set_selection_index(&mut self, frame: usize, track: usize) -> Result<(), Error> {
        let frame = *self.frame_order.get(frame).ok_or_else(|| missing("コマ"))?;
        let track = *self.track_order.get(track).ok_or_else(|| missing("列"))?;
        self.current_frame = frame;
        self.active_layer = track;
        Ok(())
    }

    /// コマの尺を設定する。
    pub fn set_frame_duration(&mut self, frame: FrameId, duration: i64) -> Result<(), Error> {
        self.sheet
            .get_mut(&frame)
            .ok_or_else(|| missing("コマ"))?
            .duration = duration;
        Ok(())
    }

    fn slot_artwork(&self, slot: ArtworkSlot) -> Option<&Cell> {
        match slot {
            ArtworkSlot::Bank(track, number) => self.tracks.get(&track)?.cells.get(&number),
            ArtworkSlot::Local(frame, track) => self
                .sheet
                .get(&frame)?
                .positions
                .get(&track)?
                .compatibility
                .local
                .as_ref(),
        }
    }

    fn slot_artwork_mut(&mut self, slot: ArtworkSlot) -> Option<&mut Cell> {
        match slot {
            ArtworkSlot::Bank(track, number) => self.tracks.get_mut(&track)?.cells.get_mut(&number),
            ArtworkSlot::Local(frame, track) => self
                .sheet
                .get_mut(&frame)?
                .positions
                .get_mut(&track)?
                .compatibility
                .local
                .as_mut(),
        }
    }

    /// 窓口の画像が指す画像を返す。絵または初期画像がなければ None。
    pub fn artwork_image(&self, slot: ArtworkSlot, field: ImageField) -> Option<&RgbaImage> {
        let cell = self.slot_artwork(slot)?;
        match field {
            ImageField::Image => Some(&cell.image),
            ImageField::Initial => cell.initial_image.as_ref(),
        }
    }

    /// 窓口の画像が指す画像を書き換え用に返す。共有されていなければ画素をその場で書ける。
    pub fn artwork_image_mut(
        &mut self,
        slot: ArtworkSlot,
        field: ImageField,
    ) -> Option<&mut RgbaImage> {
        let cell = self.slot_artwork_mut(slot)?;
        match field {
            ImageField::Image => Some(&mut cell.image),
            ImageField::Initial => cell.initial_image.as_mut(),
        }
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::image::Rect;

    fn document() -> (Document, Vec<RgbaImage>) {
        let images = vec![RgbaImage::blank(2, 1).unwrap()];
        let settings = json!({
            "name": "A", "visible": true, "opacity": 1.0, "alpha_locked": false,
            "is_paper": false, "is_draft": false, "color_filter_enabled": false,
            "color_filter_rgb": null,
        });
        let position = json!({
            "has_content": true, "is_blank_key": false, "sequence_number": 1,
            "sequence_only": false, "blank_start": false, "tween": null, "tween_member": null,
            "local": null, "content_origin": null, "v1_position": null, "dense_import": null,
            "length_imported": null, "resolve_source": null, "legacy_missing": false,
        });
        let value = json!({
            "width": 2, "height": 1, "current_frame": 0, "active_layer": 0,
            "tracks": [{"settings": settings, "hints": [],
                        "cells": [[1, {"image": 0, "initial_image": null, "cell_name": null}]]}],
            "frames": [{"duration": 1, "positions": [position.clone()]},
                       {"duration": 1, "positions": [position]}],
        });
        (Document::from_graph(&value, &images).unwrap(), images)
    }

    // 位置・列・バンクの値は ID で読み書きでき、失敗した書き込みは文書を変えない。
    #[test]
    fn positions_tracks_and_cells_are_addressed_by_id() {
        let (mut doc, images) = document();
        let (frame, track) = (FrameId(1), TrackId(0));
        let (mut value, _) = doc.position_graph(frame, track).unwrap();
        value["has_content"] = json!(false);
        value["is_blank_key"] = json!(true);
        doc.set_position_graph(frame, track, &value, &images)
            .unwrap();
        assert_eq!(doc.position_graph(frame, track).unwrap().0, value);
        value["has_content"] = json!(true);
        assert!(doc
            .set_position_graph(frame, track, &value, &images)
            .is_err());
        assert_eq!(
            doc.position_graph(frame, track).unwrap().0["has_content"],
            json!(false)
        );

        let number = CellNumber::new(2).unwrap();
        doc.set_cell_graph(
            track,
            number,
            &json!({"image": 0, "initial_image": 0, "cell_name": "x"}),
            &images,
        )
        .unwrap();
        let (track_value, shared) = doc.track_graph(track).unwrap();
        assert_eq!(track_value["cells"][1][1]["cell_name"], json!("x"));
        assert_eq!(shared.len(), 1);
        doc.set_cell_graph(track, number, &Value::Null, &images)
            .unwrap();
        assert_eq!(
            doc.track_graph(track).unwrap().0["cells"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert!(doc.set_selection_index(2, 0).is_err());
        doc.set_selection_index(1, 0).unwrap();
        assert_eq!(doc.selection(), (FrameId(1), TrackId(0)));
    }

    // バンクの画像に書くと、同じ番号を参照する全位置の絵が変わり、元の画像は変わらない。
    #[test]
    fn artwork_images_write_in_place() {
        let (mut doc, images) = document();
        let slot = ArtworkSlot::Bank(TrackId(0), CellNumber::new(1).unwrap());
        let rect = Rect {
            x: 0,
            y: 0,
            width: 1,
            height: 1,
        };
        doc.artwork_image_mut(slot, ImageField::Image)
            .unwrap()
            .write_rgba(rect, &[1, 2, 3, 4])
            .unwrap();
        assert_eq!(
            &doc.artwork_image(slot, ImageField::Image).unwrap().rgba()[..4],
            &[1, 2, 3, 4]
        );
        assert_eq!(&images[0].rgba()[..4], &[0, 0, 0, 0]);
        assert!(doc.artwork_image(slot, ImageField::Initial).is_none());
        assert!(doc
            .artwork_image(
                ArtworkSlot::Local(FrameId(0), TrackId(0)),
                ImageField::Image
            )
            .is_none());
    }
}
