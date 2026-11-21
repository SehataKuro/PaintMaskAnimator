//! Python の文書グラフ（Frame・Layer・Track・Cell と位置の互換属性）と Document の相互変換。
//!
//! 入出力は JSON の値と画像の列で、画像は列の添字で参照する。Python 側の
//! `paintmaskanimator/document_core.py` が同じ形を組み立て、読み戻す。

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use super::compatibility::{
    ArchiveHint, DenseImport, LegacyPosition, LegacySlot, PositionCompatibility, PositionGuard,
    ResolvedSource,
};
use super::model::{
    Cell, CellNumber, Document, Frame, FrameId, Position, Reference, Track, TrackId, TrackSettings,
};
use crate::{image::RgbaImage, Error};

fn invalid(what: &str) -> Error {
    Error::Invalid(format!("不正な文書グラフ: {what}"))
}

fn field<'a>(value: &'a Value, name: &str) -> &'a Value {
    value.get(name).unwrap_or(&Value::Null)
}

fn flag(value: &Value, name: &str) -> Result<bool, Error> {
    field(value, name).as_bool().ok_or_else(|| invalid(name))
}

/// 省略または null なら false の真偽値。
fn optional_flag(value: &Value, name: &str) -> Result<bool, Error> {
    match field(value, name) {
        Value::Null => Ok(false),
        _ => flag(value, name),
    }
}

fn count(value: &Value, name: &str) -> Result<usize, Error> {
    field(value, name)
        .as_u64()
        .and_then(|n| usize::try_from(n).ok())
        .ok_or_else(|| invalid(name))
}

fn array<'a>(value: &'a Value, name: &str) -> Result<&'a [Value], Error> {
    field(value, name)
        .as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| invalid(name))
}

fn number(value: &Value) -> Result<CellNumber, Error> {
    CellNumber::new(value.as_i64().ok_or_else(|| invalid("番号"))?)
}

fn optional_number(value: &Value) -> Result<Option<CellNumber>, Error> {
    if value.is_null() {
        Ok(None)
    } else {
        number(value).map(Some)
    }
}

fn optional_string(value: &Value) -> Result<Option<String>, Error> {
    match value {
        Value::Null => Ok(None),
        Value::String(text) => Ok(Some(text.clone())),
        _ => Err(invalid("文字列")),
    }
}

fn optional_json(value: &Value) -> Option<Value> {
    (!value.is_null()).then(|| value.clone())
}

fn number_json(number: Option<CellNumber>) -> Value {
    number.map_or(Value::Null, |n| json!(n.get()))
}

struct Images<'a> {
    input: &'a [RgbaImage],
}

impl Images<'_> {
    fn get(&self, value: &Value) -> Result<RgbaImage, Error> {
        let index = value
            .as_u64()
            .and_then(|n| usize::try_from(n).ok())
            .ok_or_else(|| invalid("画像の添字"))?;
        self.input
            .get(index)
            .cloned()
            .ok_or_else(|| invalid("画像の添字"))
    }

    fn optional(&self, value: &Value) -> Result<Option<RgbaImage>, Error> {
        if value.is_null() {
            Ok(None)
        } else {
            self.get(value).map(Some)
        }
    }
}

/// 書き出す画像を、画素の世代と札の組ごとに一度だけ並べる。
#[derive(Default)]
pub(super) struct Output {
    pub(super) images: Vec<RgbaImage>,
    index: BTreeMap<(u64, u32), usize>,
}

impl Output {
    fn image(&mut self, image: &RgbaImage) -> Value {
        let next = self.images.len();
        let index = *self
            .index
            .entry((image.generation(), image.tag()))
            .or_insert(next);
        if index == next {
            self.images.push(image.clone());
        }
        json!(index)
    }

    fn optional(&mut self, image: Option<&RgbaImage>) -> Value {
        image.map_or(Value::Null, |image| self.image(image))
    }

    fn cell(&mut self, cell: &Cell) -> Value {
        json!({
            "image": self.image(&cell.image),
            "initial_image": self.optional(cell.initial_image.as_ref()),
            "cell_name": cell.cell_name,
        })
    }
}

fn cell(value: &Value, images: &Images<'_>) -> Result<Cell, Error> {
    Ok(Cell {
        image: images.get(field(value, "image"))?,
        initial_image: images.optional(field(value, "initial_image"))?,
        cell_name: optional_string(field(value, "cell_name"))?,
    })
}

fn optional_cell(value: &Value, images: &Images<'_>) -> Result<Option<Cell>, Error> {
    if value.is_null() {
        Ok(None)
    } else {
        cell(value, images).map(Some)
    }
}

fn settings(value: &Value) -> Result<TrackSettings, Error> {
    let rgb = match field(value, "color_filter_rgb") {
        Value::Null => None,
        Value::Array(channels) if channels.len() == 3 => {
            let mut rgb = [0u8; 3];
            for (slot, channel) in rgb.iter_mut().zip(channels) {
                *slot = channel
                    .as_u64()
                    .and_then(|n| u8::try_from(n).ok())
                    .ok_or_else(|| invalid("color_filter_rgb"))?;
            }
            Some(rgb)
        }
        _ => return Err(invalid("color_filter_rgb")),
    };
    Ok(TrackSettings {
        name: field(value, "name")
            .as_str()
            .ok_or_else(|| invalid("name"))?
            .to_owned(),
        visible: flag(value, "visible")?,
        opacity: field(value, "opacity")
            .as_f64()
            .ok_or_else(|| invalid("opacity"))?,
        alpha_locked: flag(value, "alpha_locked")?,
        is_paper: flag(value, "is_paper")?,
        is_draft: flag(value, "is_draft")?,
        color_filter_enabled: flag(value, "color_filter_enabled")?,
        color_filter_rgb: rgb,
    })
}

fn settings_json(settings: &TrackSettings) -> Value {
    json!({
        "name": settings.name,
        "visible": settings.visible,
        "opacity": settings.opacity,
        "alpha_locked": settings.alpha_locked,
        "is_paper": settings.is_paper,
        "is_draft": settings.is_draft,
        "color_filter_enabled": settings.color_filter_enabled,
        "color_filter_rgb": settings.color_filter_rgb,
    })
}

fn hint(value: &Value) -> Result<ArchiveHint, Error> {
    Ok(ArchiveHint {
        has_content: flag(value, "has_content")?,
        is_blank_key: flag(value, "is_blank_key")?,
        exposure: count(value, "exposure")?,
        pending: flag(value, "sequence_only")?,
        tween: optional_json(field(value, "tween")),
        tween_member: optional_string(field(value, "tween_member"))?,
    })
}

fn hint_json(hint: &ArchiveHint) -> Value {
    json!({
        "has_content": hint.has_content,
        "is_blank_key": hint.is_blank_key,
        "exposure": hint.exposure,
        "sequence_only": hint.pending,
        "tween": hint.tween,
        "tween_member": hint.tween_member,
    })
}

/// Python の (has_content, is_blank_key, sequence_number) を参照・保持番号・番号なし内容へ写す。
fn reference_state(
    content: bool,
    blank: bool,
    number: Option<CellNumber>,
) -> Result<(Reference, Option<CellNumber>, bool), Error> {
    match (content, blank, number) {
        (true, true, _) => Err(invalid("絵と空キーが同時に立っている位置")),
        (true, false, Some(number)) => Ok((Reference::Number(number), None, false)),
        (true, false, None) => Ok((Reference::Empty, None, true)),
        (false, true, number) => Ok((Reference::Blank, number, false)),
        (false, false, number) => Ok((Reference::Empty, number, false)),
    }
}

/// reference_state の逆。Python の (has_content, is_blank_key) を返す。
fn reference_flags(reference: Reference, unnumbered_content: bool) -> (bool, bool) {
    match reference {
        Reference::Number(_) => (true, false),
        Reference::Blank => (false, true),
        Reference::Empty => (unnumbered_content, false),
    }
}

/// Python の位置署名 [has_content, is_blank_key, sequence_number, sequence_only, blank_start]。
fn guard(value: &Value) -> Result<PositionGuard, Error> {
    let items = value.as_array().ok_or_else(|| invalid("位置署名"))?;
    if items.len() != 5 {
        return Err(invalid("位置署名"));
    }
    let bit = |index: usize| items[index].as_bool().ok_or_else(|| invalid("位置署名"));
    let number = optional_number(&items[2])?;
    let (reference, _, unnumbered_content) = reference_state(bit(0)?, bit(1)?, number)?;
    Ok(PositionGuard {
        reference,
        number,
        unnumbered_content,
        pending: bit(3)?,
        blank_start: bit(4)?,
    })
}

fn guard_json(guard: &PositionGuard) -> Value {
    let (content, blank) = reference_flags(guard.reference, guard.unnumbered_content);
    json!([
        content,
        blank,
        number_json(guard.number),
        guard.pending,
        guard.blank_start
    ])
}

/// Python の DenseReference を [kind, number] で受ける。
fn dense_reference(value: &Value) -> Result<Reference, Error> {
    let items = value.as_array().ok_or_else(|| invalid("参照"))?;
    match (items.first().and_then(Value::as_str), items.get(1)) {
        (Some("CELL"), Some(number_value)) => Ok(Reference::Number(number(number_value)?)),
        (Some("BLANK"), Some(Value::Null)) => Ok(Reference::Blank),
        (Some("EMPTY"), Some(Value::Null)) => Ok(Reference::Empty),
        _ => Err(invalid("参照")),
    }
}

fn dense_reference_json(reference: Reference) -> Value {
    match reference {
        Reference::Number(number) => json!(["CELL", number.get()]),
        Reference::Blank => json!(["BLANK", null]),
        Reference::Empty => json!(["EMPTY", null]),
    }
}

fn legacy_slot(value: &Value, images: &Images<'_>) -> Result<LegacySlot, Error> {
    Ok(LegacySlot {
        settings: settings(field(value, "settings"))?,
        state: hint(field(value, "state"))?,
        number: optional_number(field(value, "sequence_number"))?,
        artwork: optional_cell(field(value, "artwork"), images)?,
    })
}

fn legacy_position(value: &Value, images: &Images<'_>) -> Result<LegacyPosition, Error> {
    let legacy = field(value, "legacy");
    Ok(LegacyPosition {
        legacy: if legacy.is_null() {
            None
        } else {
            Some(legacy_slot(legacy, images)?)
        },
        key_start: flag(value, "key_start")?,
        blank_start: flag(value, "blank_start")?,
        pending: flag(value, "pending")?,
        input_length: match field(value, "input_length") {
            Value::Null => None,
            _ => Some(count(value, "input_length")?),
        },
    })
}

fn legacy_position_json(position: &LegacyPosition, output: &mut Output) -> Value {
    let legacy = position.legacy.as_ref().map(|slot| {
        json!({
            "settings": settings_json(&slot.settings),
            "state": hint_json(&slot.state),
            "sequence_number": number_json(slot.number),
            "artwork": slot.artwork.as_ref().map(|cell| output.cell(cell)),
        })
    });
    json!({
        "legacy": legacy,
        "key_start": position.key_start,
        "blank_start": position.blank_start,
        "pending": position.pending,
        "input_length": position.input_length,
    })
}

fn position(value: &Value, images: &Images<'_>) -> Result<Position, Error> {
    let number = optional_number(field(value, "sequence_number"))?;
    let (reference, retained_number, unnumbered_content) = reference_state(
        flag(value, "has_content")?,
        flag(value, "is_blank_key")?,
        number,
    )?;
    let dense_import = match field(value, "dense_import") {
        Value::Null => None,
        Value::Array(items) if items.len() == 3 => Some(DenseImport {
            guard: guard(&items[0])?,
            sheet: dense_reference(&items[1])?,
            resolved: dense_reference(&items[2])?,
        }),
        _ => return Err(invalid("dense_import")),
    };
    let resolved_source = match field(value, "resolve_source") {
        Value::Null => None,
        Value::Array(items) if items.len() == 4 => Some(ResolvedSource {
            guard: guard(&items[0])?,
            source_column: match &items[1] {
                Value::Null => None,
                source => Some(
                    source
                        .as_u64()
                        .and_then(|n| usize::try_from(n).ok())
                        .ok_or_else(|| invalid("resolve_source"))?,
                ),
            },
            column: items[2]
                .as_u64()
                .and_then(|n| usize::try_from(n).ok())
                .ok_or_else(|| invalid("resolve_source"))?,
            frame_count: items[3]
                .as_u64()
                .and_then(|n| usize::try_from(n).ok())
                .ok_or_else(|| invalid("resolve_source"))?,
        }),
        _ => return Err(invalid("resolve_source")),
    };
    let legacy = match field(value, "v1_position") {
        Value::Null => None,
        position => Some(legacy_position(position, images)?),
    };
    Ok(Position {
        reference,
        blank_start: flag(value, "blank_start")?,
        tween: optional_json(field(value, "tween")),
        tween_member: optional_string(field(value, "tween_member"))?,
        pending: flag(value, "sequence_only")?,
        compatibility: PositionCompatibility {
            retained_number,
            unnumbered_content,
            local: optional_cell(field(value, "local"), images)?,
            legacy,
            dense_import,
            length_imported: match field(value, "length_imported") {
                Value::Null => None,
                _ => Some(flag(value, "length_imported")?),
            },
            resolved_source,
            content_origin: optional_number(field(value, "content_origin"))?,
            legacy_missing: optional_flag(value, "legacy_missing")?,
            pending_detached: optional_flag(value, "pending_detached")?,
            local_bank: optional_flag(value, "local_bank")?,
            local_share: match field(value, "local_share") {
                Value::Null => None,
                share => Some(share.as_u64().ok_or_else(|| invalid("local_share"))?),
            },
        },
    })
}

pub(super) fn position_json(position: &Position, output: &mut Output) -> Value {
    let compatibility = &position.compatibility;
    let (content, blank) = reference_flags(position.reference, compatibility.unnumbered_content);
    let mut result = Map::new();
    result.insert("has_content".into(), json!(content));
    result.insert("is_blank_key".into(), json!(blank));
    result.insert("sequence_number".into(), number_json(position.number()));
    result.insert("sequence_only".into(), json!(position.pending));
    result.insert("blank_start".into(), json!(position.blank_start));
    result.insert(
        "tween".into(),
        position.tween.clone().unwrap_or(Value::Null),
    );
    result.insert("tween_member".into(), json!(position.tween_member));
    result.insert(
        "local".into(),
        compatibility
            .local
            .as_ref()
            .map_or(Value::Null, |cell| output.cell(cell)),
    );
    result.insert(
        "content_origin".into(),
        number_json(compatibility.content_origin),
    );
    result.insert("legacy_missing".into(), json!(compatibility.legacy_missing));
    result.insert(
        "pending_detached".into(),
        json!(compatibility.pending_detached),
    );
    result.insert("local_share".into(), json!(compatibility.local_share));
    result.insert("local_bank".into(), json!(compatibility.local_bank));
    result.insert(
        "v1_position".into(),
        compatibility
            .legacy
            .as_ref()
            .map_or(Value::Null, |legacy| legacy_position_json(legacy, output)),
    );
    result.insert(
        "dense_import".into(),
        compatibility.dense_import.map_or(Value::Null, |import| {
            json!([
                guard_json(&import.guard),
                dense_reference_json(import.sheet),
                dense_reference_json(import.resolved),
            ])
        }),
    );
    result.insert(
        "length_imported".into(),
        json!(compatibility.length_imported),
    );
    result.insert(
        "resolve_source".into(),
        compatibility.resolved_source.map_or(Value::Null, |source| {
            json!([
                guard_json(&source.guard),
                source.source_column,
                source.column,
                source.frame_count,
            ])
        }),
    );
    Value::Object(result)
}

/// 位置がバンクを参照する番号。Python の Layer._cell がバンクを引く条件と同じ。
fn bank_number(position: &Position) -> Option<CellNumber> {
    let number = position.number()?;
    (matches!(position.reference, Reference::Number(_)) || position.pending).then_some(number)
}

/// 位置がバンクにない番号を参照していればエラーにする。
///
/// 局所の絵がバンクの絵そのもの（local_bank）なのに、バンクを参照しない位置もエラー。
pub(super) fn check_bank_reference(position: &Position, track: &Track) -> Result<(), Error> {
    match bank_number(position) {
        Some(number) if !track.cells.contains_key(&number) => {
            Err(invalid("バンクにない番号を参照する位置"))
        }
        None if position.compatibility.local_bank => {
            Err(invalid("バンクを参照しない位置の local_bank"))
        }
        _ => Ok(()),
    }
}

fn track(value: &Value, images: &Images<'_>) -> Result<Track, Error> {
    let mut cells = BTreeMap::new();
    for entry in array(value, "cells")? {
        let number = number(&entry[0])?;
        if cells.insert(number, cell(&entry[1], images)?).is_some() {
            return Err(invalid("重複したセル番号"));
        }
    }
    let mut hints = BTreeMap::new();
    for entry in array(value, "hints")? {
        if hints.insert(number(&entry[0])?, hint(&entry[1])?).is_some() {
            return Err(invalid("重複した連番保管"));
        }
    }
    Ok(Track {
        settings: settings(field(value, "settings"))?,
        cells,
        legacy_archive_hints: hints,
    })
}

/// 列・コマの ID を読む。全項目に "id" があればそれを使い、無ければ 0 から振る。
///
/// 次に振る ID は `next_name` があればそれ、無ければ最大の ID の次。削除した ID を
/// 再利用しないよう、to_graph は次の ID も書き出す。
fn ids<T: Copy + Ord>(
    value: &Value,
    entries: &[Value],
    next_name: &str,
    make: impl Fn(u64) -> T,
) -> Result<(Vec<T>, u64), Error> {
    let given: Vec<_> = entries.iter().map(|entry| entry.get("id")).collect();
    let raw: Vec<u64> = if given.iter().all(Option::is_none) {
        (0..entries.len() as u64).collect()
    } else {
        given
            .iter()
            .map(|id| id.and_then(Value::as_u64).ok_or_else(|| invalid("id")))
            .collect::<Result<_, _>>()?
    };
    let mut unique = raw.clone();
    unique.sort_unstable();
    unique.dedup();
    if unique.len() != raw.len() {
        return Err(invalid("重複した id"));
    }
    let after_max = unique
        .last()
        .map_or(Some(0), |max| max.checked_add(1))
        .ok_or_else(|| invalid("id"))?;
    let next = match field(value, next_name) {
        Value::Null => after_max,
        next => next
            .as_u64()
            .filter(|&next| next >= after_max)
            .ok_or_else(|| invalid(next_name))?,
    };
    Ok((raw.into_iter().map(make).collect(), next))
}

pub(super) fn track_json(id: TrackId, track: &Track, output: &mut Output) -> Value {
    let cells: Vec<_> = track
        .cells
        .iter()
        .map(|(number, cell)| json!([number.get(), output.cell(cell)]))
        .collect();
    let hints: Vec<_> = track
        .legacy_archive_hints
        .iter()
        .map(|(number, hint)| json!([number.get(), hint_json(hint)]))
        .collect();
    json!({
        "id": id.0,
        "settings": settings_json(&track.settings),
        "cells": cells,
        "hints": hints,
    })
}

pub(super) fn position_value(value: &Value, images: &[RgbaImage]) -> Result<Position, Error> {
    position(value, &Images { input: images })
}

pub(super) fn settings_value(value: &Value) -> Result<TrackSettings, Error> {
    settings(value)
}

pub(super) fn optional_cell_value(
    value: &Value,
    images: &[RgbaImage],
) -> Result<Option<Cell>, Error> {
    optional_cell(value, &Images { input: images })
}

pub(super) fn hint_value(value: &Value) -> Result<ArchiveHint, Error> {
    hint(value)
}

impl Document {
    /// Python の文書グラフを JSON と画像列から組み立てる。
    ///
    /// `document_core.graph_payload` の出力を受ける。列とコマは表示順で、各コマは
    /// 列と同じ数の位置を持つ。現在コマと選択列は範囲内でなければならない。
    /// 画像は添字で共有し、同じ添字の画像は画素と世代を共有する。
    /// 位置がバンクにない番号を参照していればエラー。
    pub fn from_graph(value: &Value, images: &[RgbaImage]) -> Result<Self, Error> {
        let images = Images { input: images };
        let width = count(value, "width")?;
        let height = count(value, "height")?;
        let tracks_input = array(value, "tracks")?;
        let frames_input = array(value, "frames")?;
        if tracks_input.is_empty() || frames_input.is_empty() {
            return Err(invalid("列またはコマがありません"));
        }
        let (track_order, next_track_id) = ids(value, tracks_input, "next_track_id", TrackId)?;
        let (frame_order, next_frame_id) = ids(value, frames_input, "next_frame_id", FrameId)?;
        let mut tracks = BTreeMap::new();
        for (&id, input) in track_order.iter().zip(tracks_input) {
            tracks.insert(id, track(input, &images)?);
        }
        let mut sheet = BTreeMap::new();
        for (&id, input) in frame_order.iter().zip(frames_input) {
            let positions_input = array(input, "positions")?;
            if positions_input.len() != track_order.len() {
                return Err(invalid("列の数と位置の数が一致しません"));
            }
            let mut positions = BTreeMap::new();
            for (&track, input) in track_order.iter().zip(positions_input) {
                let position = position(input, &images)?;
                check_bank_reference(&position, &tracks[&track])?;
                positions.insert(track, position);
            }
            let duration = field(input, "duration")
                .as_i64()
                .ok_or_else(|| invalid("duration"))?;
            sheet.insert(
                id,
                Frame {
                    duration,
                    positions,
                },
            );
        }
        let current_frame = *frame_order
            .get(count(value, "current_frame")?)
            .ok_or_else(|| invalid("現在コマ"))?;
        let active_layer = *track_order
            .get(count(value, "active_layer")?)
            .ok_or_else(|| invalid("選択列"))?;
        Ok(Self {
            width,
            height,
            next_frame_id,
            next_track_id,
            blank: None,
            frame_order,
            track_order,
            sheet,
            tracks,
            current_frame,
            active_layer,
        })
    }

    /// from_graph と同じ形の JSON と、添字で参照する画像列を返す。
    ///
    /// 画像は世代と札の組ごとに一度だけ並べるので、画素を共有する絵は同じ添字になる。
    /// 添字は画素の受け渡しを減らすためのもので、絵どうしの同一性ではない（各絵の画像は
    /// 独立した値）。
    pub fn to_graph(&self) -> (Value, Vec<RgbaImage>) {
        let mut output = Output::default();
        let tracks: Vec<_> = self
            .track_order
            .iter()
            .map(|&id| track_json(id, &self.tracks[&id], &mut output))
            .collect();
        let frames: Vec<_> = self
            .frame_order
            .iter()
            .map(|id| {
                let frame = &self.sheet[id];
                let positions: Vec<_> = self
                    .track_order
                    .iter()
                    .map(|track| position_json(&frame.positions[track], &mut output))
                    .collect();
                json!({"id": id.0, "duration": frame.duration, "positions": positions})
            })
            .collect();
        let current = self
            .frame_order
            .iter()
            .position(|&id| id == self.current_frame);
        let active = self
            .track_order
            .iter()
            .position(|&id| id == self.active_layer);
        let value = json!({
            "width": self.width,
            "height": self.height,
            "current_frame": current,
            "active_layer": active,
            "next_frame_id": self.next_frame_id,
            "next_track_id": self.next_track_id,
            "tracks": tracks,
            "frames": frames,
        });
        (value, output.images)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings_value(name: &str) -> Value {
        json!({
            "name": name, "visible": true, "opacity": 0.5, "alpha_locked": false,
            "is_paper": false, "is_draft": true, "color_filter_enabled": true,
            "color_filter_rgb": [1, 2, 3],
        })
    }

    fn slot(content: bool, blank: bool, number: Value) -> Value {
        json!({
            "has_content": content, "is_blank_key": blank, "sequence_number": number,
            "sequence_only": false, "blank_start": blank, "tween": null, "tween_member": null,
            "local": null, "content_origin": null, "v1_position": null, "dense_import": null,
            "length_imported": null, "resolve_source": null, "legacy_missing": false,
            "pending_detached": false, "local_share": null, "local_bank": false,
        })
    }

    fn sample() -> Value {
        let mut held = slot(false, false, json!(3));
        held["local"] = json!({"image": 1, "initial_image": null, "cell_name": "x"});
        held["content_origin"] = json!(3);
        held["legacy_missing"] = json!(true);
        held["local_share"] = json!(4);
        let mut pending = slot(true, false, json!(2));
        pending["sequence_only"] = json!(true);
        pending["pending_detached"] = json!(true);
        pending["local_bank"] = json!(true);
        pending["tween"] = json!({"id": "t", "length": 2});
        pending["dense_import"] =
            json!([[true, false, 2, true, false], ["EMPTY", null], ["CELL", 2]]);
        pending["resolve_source"] = json!([[true, false, 2, true, false], null, 1, 3]);
        let mut legacy = slot(false, true, Value::Null);
        legacy["v1_position"] = json!({
            "legacy": {
                "settings": settings_value("A"),
                "state": {"has_content": false, "is_blank_key": true, "exposure": 2,
                          "sequence_only": false, "tween": null, "tween_member": "m"},
                "sequence_number": null,
                "artwork": {"image": 0, "initial_image": 0, "cell_name": null},
            },
            "key_start": true, "blank_start": true, "pending": false, "input_length": 4,
        });
        legacy["length_imported"] = json!(true);
        json!({
            "width": 2, "height": 1, "current_frame": 2, "active_layer": 0,
            "next_frame_id": 9, "next_track_id": 4,
            "tracks": [{
                "id": 3,
                "settings": settings_value("A"),
                "cells": [[2, {"image": 0, "initial_image": 1, "cell_name": null}]],
                "hints": [[2, {"has_content": true, "is_blank_key": false, "exposure": 1,
                               "sequence_only": true, "tween": null, "tween_member": null}]],
            }],
            "frames": [
                {"id": 5, "duration": 1, "positions": [held]},
                {"id": 0, "duration": 2, "positions": [pending]},
                {"id": 7, "duration": 1, "positions": [legacy]},
                {"id": 2, "duration": 1, "positions": [slot(true, false, Value::Null)]},
            ],
        })
    }

    fn images() -> Vec<RgbaImage> {
        vec![
            RgbaImage::blank(2, 1).unwrap(),
            RgbaImage::from_rgba(2, 1, &[9; 8]).unwrap(),
        ]
    }

    // Python の文書グラフ（隠し属性を含む）は Document を通しても同じ値に戻る。
    #[test]
    fn graph_roundtrips_hidden_position_state_and_shares_images() {
        let input = sample();
        let images = images();
        let document = Document::from_graph(&input, &images).unwrap();
        let (output, shared) = document.to_graph();
        assert_eq!(output, input);
        assert_eq!(shared.len(), 2);
        assert_eq!(shared[0].generation(), images[0].generation());
        assert_eq!(shared[1].generation(), images[1].generation());
        assert_eq!(document.selection(), (FrameId(7), TrackId(3)));
        // 番号なしの内容と保持番号は、表示用の参照と別に保たれる。
        let position = &document.sheet[&FrameId(5)].positions[&TrackId(3)];
        assert_eq!(position.reference, Reference::Empty);
        assert_eq!(position.number().map(CellNumber::get), Some(3));
    }

    // 表せない状態や範囲外の選択は、文書を作らずにエラーにする。
    #[test]
    fn graph_rejects_unrepresentable_input() {
        let images = images();
        let mut both = sample();
        both["frames"][0]["positions"][0] = slot(true, true, json!(1));
        assert!(Document::from_graph(&both, &images).is_err());
        let mut selection = sample();
        selection["current_frame"] = json!(4);
        assert!(Document::from_graph(&selection, &images).is_err());
        let mut ragged = sample();
        ragged["frames"][1]["positions"] = json!([]);
        assert!(Document::from_graph(&ragged, &images).is_err());
        let mut image = sample();
        image["tracks"][0]["cells"][0][1]["image"] = json!(7);
        assert!(Document::from_graph(&image, &images).is_err());
        let mut duplicate = sample();
        duplicate["frames"][1]["id"] = json!(5);
        assert!(Document::from_graph(&duplicate, &images).is_err());
        let mut reused = sample();
        reused["next_frame_id"] = json!(7);
        assert!(Document::from_graph(&reused, &images).is_err());
        // バンクにない番号を参照する位置（内容キー・配置待ち）は受けない。保持番号だけなら受ける。
        let mut dangling = sample();
        dangling["frames"][3]["positions"][0] = slot(true, false, json!(9));
        assert!(Document::from_graph(&dangling, &images).is_err());
        let mut pending = sample();
        pending["frames"][1]["positions"][0]["sequence_number"] = json!(9);
        assert!(Document::from_graph(&pending, &images).is_err());
        let mut stray = sample();
        stray["frames"][3]["positions"][0]["local_bank"] = json!(true);
        assert!(Document::from_graph(&stray, &images).is_err());
        let mut retained = sample();
        retained["frames"][0]["positions"][0]["sequence_number"] = json!(9);
        assert!(Document::from_graph(&retained, &images).is_ok());
    }
}
