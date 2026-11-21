use std::collections::BTreeSet;

use super::changes::{Change, ChangeSet, FrameOperation, TrackOperation};
use super::model::{
    layer_name, Cell, CellNumber, Document, Frame, FrameId, Position, Reference, Track, TrackId,
    TrackSettings,
};
use crate::Error;

/// Python Document.set_track_properties が受け取る列属性の更新。
#[derive(Clone, Debug, PartialEq)]
pub enum TrackProperty {
    /// レイヤー名。
    Name(String),
    /// 表示の有無。
    Visible(bool),
    /// 不透明度。
    Opacity(f64),
    /// 透明部分の保護。
    AlphaLocked(bool),
    /// 用紙属性。
    IsPaper(bool),
    /// 下書き属性。
    IsDraft(bool),
    /// 色フィルタの有効化。
    ColorFilterEnabled(bool),
    /// 色フィルタの RGB。
    ColorFilterRgb(Option<[u8; 3]>),
}

impl TrackProperty {
    fn apply(&self, settings: &mut TrackSettings) -> (&'static str, bool) {
        fn replace<T: Clone + PartialEq>(target: &mut T, value: &T) -> bool {
            if target == value {
                return false;
            }
            *target = value.clone();
            true
        }
        match self {
            Self::Name(value) => ("name", replace(&mut settings.name, value)),
            Self::Visible(value) => ("visible", replace(&mut settings.visible, value)),
            Self::Opacity(value) => ("opacity", replace(&mut settings.opacity, value)),
            Self::AlphaLocked(value) => {
                ("alpha_locked", replace(&mut settings.alpha_locked, value))
            }
            Self::IsPaper(value) => ("is_paper", replace(&mut settings.is_paper, value)),
            Self::IsDraft(value) => ("is_draft", replace(&mut settings.is_draft, value)),
            Self::ColorFilterEnabled(value) => (
                "color_filter_enabled",
                replace(&mut settings.color_filter_enabled, value),
            ),
            Self::ColorFilterRgb(value) => (
                "color_filter_rgb",
                replace(&mut settings.color_filter_rgb, value),
            ),
        }
    }
}

/// 列の挿入・統合用に呼び出し側が準備した列とシート参照。
pub struct PreparedTrack {
    /// 列の設定と絵のバンク。
    pub track: Track,
    /// コマ表示順の位置。
    pub positions: Vec<Position>,
}

// 自動名は ASCII 英字のみ。ASCII 英字列に casefold される文字だけを写す。
fn fold_default_name(name: &str) -> Option<String> {
    let mut folded = String::new();
    for ch in name
        .trim_matches(|ch: char| ch.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&ch))
        .chars()
    {
        match ch {
            'A'..='Z' | 'a'..='z' => folded.push(ch.to_ascii_uppercase()),
            'ß' | 'ẞ' => folded.push_str("SS"),
            'ſ' => folded.push('S'),
            'K' => folded.push('K'),
            'ﬀ' => folded.push_str("FF"),
            'ﬁ' => folded.push_str("FI"),
            'ﬂ' => folded.push_str("FL"),
            'ﬃ' => folded.push_str("FFI"),
            'ﬄ' => folded.push_str("FFL"),
            'ﬅ' | 'ﬆ' => folded.push_str("ST"),
            _ => return None,
        }
    }
    Some(folded)
}

fn invalid(message: &str) -> Error {
    Error::Invalid(message.into())
}

impl Document {
    /// Python の next_drawing_number と同様に、未配置の絵も含めた末尾の次を返す。
    pub fn next_drawing_number(&self, layer_index: usize) -> Result<CellNumber, Error> {
        let id = *self
            .track_order
            .get(layer_index)
            .ok_or_else(|| invalid("不正な列番号"))?;
        let max = self.tracks[&id]
            .cells
            .last_key_value()
            .map_or(0, |(number, _)| number.get());
        CellNumber::new(
            max.checked_add(1)
                .ok_or_else(|| invalid("セル番号の上限"))?,
        )
    }

    /// Python の move_tracks と同じ順列を適用し、構造変更と選択の順で通知する。
    pub fn move_tracks(&mut self, order: &[usize]) -> Result<ChangeSet, Error> {
        let count = self.track_order.len();
        if order.len() != count
            || !order
                .iter()
                .copied()
                .collect::<BTreeSet<_>>()
                .into_iter()
                .eq(0..count)
        {
            return Err(invalid("列順の指定が順列ではありません"));
        }
        if order.iter().copied().eq(0..count) {
            return Ok(Vec::new());
        }
        let reordered: Vec<_> = order.iter().map(|&index| self.track_order[index]).collect();
        let active = reordered
            .iter()
            .position(|id| *id == self.active_layer)
            .ok_or_else(|| invalid("選択列がありません"))?;
        self.track_order = reordered;
        Ok(vec![
            Change::TrackStructure {
                operation: TrackOperation::Move,
                before: order.to_vec(),
                after: (0..count).collect(),
            },
            Change::Selection {
                layer_index: active,
            },
        ])
    }

    /// Python の remove_tracks と同様に列を削除し、残った列を選択する。
    pub fn remove_tracks(
        &mut self,
        indices: &[usize],
        active: Option<usize>,
    ) -> Result<ChangeSet, Error> {
        let indices: BTreeSet<_> = indices.iter().copied().collect();
        let count = self.track_order.len();
        if indices.is_empty() {
            return Ok(Vec::new());
        }
        if indices.len() >= count || indices.last().is_some_and(|&index| index >= count) {
            return Err(invalid("削除する列が不正です"));
        }
        let remaining = count - indices.len();
        let active =
            active.unwrap_or_else(|| indices.first().copied().unwrap_or(0).min(remaining - 1));
        if active >= remaining {
            return Err(invalid("削除後の選択列が不正です"));
        }
        for &index in indices.iter().rev() {
            let id = self.track_order.remove(index);
            self.tracks.remove(&id);
            for frame in self.sheet.values_mut() {
                frame.positions.remove(&id);
            }
        }
        self.active_layer = self.track_order[active];
        Ok(vec![
            Change::TrackStructure {
                operation: TrackOperation::Remove,
                before: indices.into_iter().collect(),
                after: Vec::new(),
            },
            Change::Selection {
                layer_index: active,
            },
        ])
    }

    /// Python の set_track_properties と同じ順序で列設定を更新して通知する。
    pub fn set_track_properties(
        &mut self,
        indices: &[usize],
        settings: &[TrackProperty],
        notify_unchanged: bool,
    ) -> Result<ChangeSet, Error> {
        if indices.iter().any(|&index| index >= self.track_order.len()) {
            return Err(invalid("不正な列番号"));
        }
        if settings
            .iter()
            .any(|setting| matches!(setting, TrackProperty::Opacity(value) if !value.is_finite()))
        {
            return Err(invalid("不正な不透明度"));
        }
        let mut seen = BTreeSet::new();
        let mut changes = Vec::new();
        for &index in indices.iter().filter(|&&index| seen.insert(index)) {
            let track = self
                .tracks
                .get_mut(&self.track_order[index])
                .expect("列順には有効な列 ID だけが入る");
            let fields: Vec<_> = settings
                .iter()
                .filter_map(|setting| {
                    let (field, changed) = setting.apply(&mut track.settings);
                    (changed || notify_unchanged).then_some(field)
                })
                .collect();
            if !fields.is_empty() {
                changes.push(Change::TrackProperties {
                    indices: vec![index],
                    fields,
                });
            }
        }
        Ok(changes)
    }

    /// Python の add_track と同様に空の列を末尾に追加して選択する。
    pub fn add_track(&mut self) -> Result<ChangeSet, Error> {
        let index = self.track_order.len();
        let used: BTreeSet<_> = self
            .tracks
            .values()
            .filter_map(|track| fold_default_name(&track.settings.name))
            .collect();
        let name = (0..=index)
            .map(layer_name)
            .find(|name| !used.contains(name))
            .ok_or_else(|| invalid("空き列名を選択できません"))?;
        let next = self
            .next_track_id
            .checked_add(1)
            .ok_or_else(|| invalid("列 ID の上限"))?;
        let id = TrackId(self.next_track_id);
        let track = Track {
            settings: TrackSettings {
                name,
                visible: true,
                opacity: 1.0,
                alpha_locked: false,
                is_paper: false,
                is_draft: false,
                color_filter_enabled: false,
                color_filter_rgb: None,
            },
            cells: Default::default(),
            legacy_archive_hints: Default::default(),
        };
        for frame in self.sheet.values_mut() {
            frame.positions.insert(id, Position::default());
        }
        self.next_track_id = next;
        self.track_order.push(id);
        self.tracks.insert(id, track);
        self.active_layer = id;
        Ok(vec![
            Change::TrackStructure {
                operation: TrackOperation::Insert,
                before: Vec::new(),
                after: vec![index],
            },
            Change::Selection { layer_index: index },
        ])
    }

    fn check_prepared_track(&self, prepared: &PreparedTrack) -> Result<(), Error> {
        if prepared.positions.len() != self.frame_order.len()
            || !prepared.track.settings.opacity.is_finite()
        {
            return Err(invalid("準備済みの列のコマ数または設定が不正です"));
        }
        for position in &prepared.positions {
            if position
                .compatibility
                .resolved_source
                .is_some_and(|source| {
                    source
                        .source_column
                        .is_some_and(|column| column >= source.frame_count)
                })
            {
                return Err(invalid("準備済みの列に範囲外の解決元があります"));
            }
            let references = [
                position.reference,
                position.reference_for(false),
                position.reference_for(true),
            ];
            if references.iter().any(|reference| {
                matches!(reference,
                Reference::Number(number) if !prepared.track.cells.contains_key(number))
            }) {
                return Err(invalid("準備済みの列に参照先の絵がありません"));
            }
        }
        Ok(())
    }

    fn install_track(&mut self, index: usize, prepared: PreparedTrack) {
        let id = TrackId(self.next_track_id);
        self.next_track_id += 1;
        self.track_order.insert(index, id);
        self.tracks.insert(id, prepared.track);
        for (id_frame, position) in self.frame_order.iter().zip(prepared.positions) {
            // コマ順の各 ID に対応する行は常にシートに保持する。
            self.sheet
                .get_mut(id_frame)
                .unwrap()
                .positions
                .insert(id, position);
        }
    }

    /// Python の insert_track に対応し、正規化済みの列を挿入する。
    pub fn insert_track(
        &mut self,
        index: usize,
        prepared: PreparedTrack,
        active: Option<usize>,
    ) -> Result<ChangeSet, Error> {
        let count = self.track_order.len();
        if index > count || active.is_some_and(|active| active > count) {
            return Err(invalid("挿入する列または選択列が不正です"));
        }
        self.next_track_id
            .checked_add(1)
            .ok_or_else(|| invalid("列 ID の上限"))?;
        self.check_prepared_track(&prepared)?;
        self.install_track(index, prepared);
        let mut changes = vec![Change::TrackStructure {
            operation: TrackOperation::Insert,
            before: Vec::new(),
            after: vec![index],
        }];
        if let Some(active) = active {
            self.active_layer = self.track_order[active];
            changes.push(Change::Selection {
                layer_index: active,
            });
        }
        Ok(changes)
    }

    /// Python の duplicate_tracks と同様に、元の各列の直後に独立したバンクを複製する。
    pub fn duplicate_tracks(&mut self, names: &[(usize, String)]) -> Result<ChangeSet, Error> {
        if names.is_empty() {
            return Ok(Vec::new());
        }
        let mut sorted: Vec<_> = names.iter().collect();
        sorted.sort_by_key(|(index, _)| *index);
        if sorted
            .last()
            .is_some_and(|(index, _)| *index >= self.track_order.len())
            || sorted.windows(2).any(|pair| pair[0].0 == pair[1].0)
        {
            return Err(invalid("複製する列が不正です"));
        }
        self.next_track_id
            .checked_add(names.len() as u64)
            .ok_or_else(|| invalid("列 ID の上限"))?;
        for (index, name) in sorted.iter().rev().copied() {
            let source = self.track_order[*index];
            let mut track = self.tracks[&source].clone();
            track.settings.name.clone_from(name);
            let positions = self
                .frame_order
                .iter()
                .map(|frame| self.sheet[frame].positions[&source].clone())
                .collect();
            self.install_track(index + 1, PreparedTrack { track, positions });
        }
        let before: Vec<_> = sorted.into_iter().map(|(index, _)| *index).collect();
        let after: Vec<_> = before
            .iter()
            .enumerate()
            .map(|(offset, index)| index + offset + 1)
            .collect();
        // 空入力は先に返しており、before の各列に複製先が一つある。
        let layer_index = *after.last().unwrap();
        self.active_layer = self.track_order[layer_index];
        Ok(vec![
            Change::TrackStructure {
                operation: TrackOperation::Duplicate,
                before,
                after,
            },
            Change::Selection { layer_index },
        ])
    }

    /// Python の merge_tracks に対応し、連続列を正規化済みの合成列で置き換える。
    pub fn merge_tracks(
        &mut self,
        indices: &[usize],
        prepared: PreparedTrack,
    ) -> Result<ChangeSet, Error> {
        let [start, .., end] = indices else {
            return Err(invalid("統合には複数の列が必要です"));
        };
        if *end >= self.track_order.len() || !indices.iter().copied().eq(*start..=*end) {
            return Err(invalid("統合する列が連続していません"));
        }
        self.next_track_id
            .checked_add(1)
            .ok_or_else(|| invalid("列 ID の上限"))?;
        self.check_prepared_track(&prepared)?;
        for id in self.track_order.drain(*start..=*end) {
            self.tracks.remove(&id);
            for frame in self.sheet.values_mut() {
                frame.positions.remove(&id);
            }
        }
        self.install_track(*start, prepared);
        self.active_layer = self.track_order[*start];
        Ok(vec![
            Change::TrackStructure {
                operation: TrackOperation::Merge,
                before: indices.to_vec(),
                after: vec![*start],
            },
            Change::Selection {
                layer_index: *start,
            },
        ])
    }

    /// Python の ensure_frame_count と同様に、未使用のコマで末尾を延ばす。
    pub fn ensure_frame_count(&mut self, count: usize) -> Result<ChangeSet, Error> {
        let before = self.frame_order.len();
        if count <= before {
            return Ok(Vec::new());
        }
        let added = u64::try_from(count - before).map_err(|_| Error::TimelineSizeOverflow)?;
        let next = self
            .next_frame_id
            .checked_add(added)
            .ok_or(Error::TimelineSizeOverflow)?;
        self.frame_order
            .try_reserve(count - before)
            .map_err(|_| Error::TimelineSizeOverflow)?;
        // Python の make_frame は Layer(name, blank_model_image()) を作るので、新しい位置は
        // 空画像の局所の Cell を持つ。
        let mut empty = Position::default();
        empty.compatibility.local = Some(Cell {
            image: self.blank_artwork()?,
            initial_image: None,
            cell_name: None,
        });
        let blank = Frame {
            duration: 1,
            positions: self
                .track_order
                .iter()
                .map(|&id| (id, empty.clone()))
                .collect(),
        };
        for number in self.next_frame_id..next {
            let id = FrameId(number);
            self.sheet.insert(id, blank.clone());
            self.frame_order.push(id);
        }
        self.next_frame_id = next;
        Ok(vec![Change::FrameStructure {
            operation: FrameOperation::Extend,
            before,
            after: count,
            start: before,
        }])
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::document::v2;
    use crate::image::RgbaImage;
    use crate::project::payload::decode_v2_plan;

    fn document() -> Document {
        let payload = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1],
            "tracks": [{"cells": [], "sheet": [null]}],
        });
        let plan = decode_v2_plan(&payload, &[]).unwrap();
        v2::from_plan(&payload, &plan, &[]).unwrap()
    }

    // tests/test_document_track_ops.py:test_invalid_commands_leave_columns_and_settings_untouched
    // tests/test_document_track_ops.py:test_track_structure_events_and_ownership
    #[test]
    fn track_selection_and_invalid_commands_are_atomic() {
        let mut doc = document();
        let original = doc.track_order()[0];
        assert!(matches!(
            doc.add_track().unwrap().as_slice(),
            [
                Change::TrackStructure {
                    operation: TrackOperation::Insert,
                    ..
                },
                Change::Selection { layer_index: 1 }
            ]
        ));
        let added = doc.track_order()[1];
        let before = v2::project(&doc, &json!({})).unwrap().payload;
        let frame = doc.selection().0;
        assert!(doc.move_tracks(&[1, 1]).is_err());
        assert!(doc.remove_tracks(&[0], Some(1)).is_err());
        assert!(doc
            .set_track_properties(&[0, 2], &[TrackProperty::Visible(false)], false)
            .is_err());
        assert_eq!(v2::project(&doc, &json!({})).unwrap().payload, before);
        assert_eq!(doc.track_order(), &[original, added]);
        assert_eq!(doc.selection(), (frame, added));
        assert!(matches!(
            doc.move_tracks(&[1, 0]).unwrap().as_slice(),
            [
                Change::TrackStructure {
                    operation: TrackOperation::Move,
                    ..
                },
                Change::Selection { layer_index: 0 }
            ]
        ));
        assert_eq!(doc.track_order(), &[added, original]);
        assert_eq!(doc.selection(), (frame, added));
        doc.remove_tracks(&[0], None).unwrap();
        doc.add_track().unwrap();
        assert_ne!(doc.track_order()[1], added);
    }

    #[test]
    fn prepared_tracks_reject_an_out_of_bounds_resolution_source_atomically() {
        use crate::document::compatibility::{DenseImport, PositionGuard, ResolvedSource};

        let mut doc = document();
        let (frame, track) = doc.selection();
        let before = v2::project(&doc, &json!({})).unwrap().payload;
        let mut position = Position::default();
        let guard = PositionGuard::capture(&position);
        position.compatibility.dense_import = Some(DenseImport {
            guard,
            sheet: Reference::Empty,
            resolved: Reference::Empty,
        });
        position.compatibility.resolved_source = Some(ResolvedSource {
            guard,
            column: 0,
            frame_count: 1,
            source_column: Some(1),
        });
        let malformed = PreparedTrack {
            track: doc.tracks[&track].clone(),
            positions: vec![position],
        };
        assert!(doc.insert_track(1, malformed, Some(1)).is_err());
        assert_eq!(doc.selection(), (frame, track));
        assert_eq!(doc.track_order(), &[track]);
        assert_eq!(v2::project(&doc, &json!({})).unwrap().payload, before);
    }

    // tests/test_new_number_here.py:test_new_number_includes_archived_numbers_from_the_same_layer
    #[test]
    fn allocation_includes_unplaced_numbers_and_reports_overflow() {
        let mut doc = document();
        let id = doc.track_order()[0];
        let track = doc.tracks.get_mut(&id).unwrap();
        let cell = crate::document::model::Cell {
            image: RgbaImage::blank(1, 1).unwrap(),
            initial_image: None,
            cell_name: None,
        };
        track
            .cells
            .insert(CellNumber::new(12).unwrap(), cell.clone());
        assert_eq!(doc.next_drawing_number(0).unwrap().get(), 13);
        doc.tracks
            .get_mut(&id)
            .unwrap()
            .cells
            .insert(CellNumber::new(i64::MAX).unwrap(), cell);
        assert!(doc.next_drawing_number(0).is_err());
        assert!(CellNumber::new(0).is_err());
        assert!(CellNumber::new(-1).is_err());
    }
}
