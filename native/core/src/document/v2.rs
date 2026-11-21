use std::collections::BTreeMap;

use serde_json::{json, Value};

use super::compatibility::{
    ArchiveHint, DenseImport, LegacyPosition, LegacySlot, PositionCompatibility, PositionGuard,
    ResolvedSource,
};
use super::model::{
    layer_name, Cell, CellNumber, Document, Frame, FrameId, Position, Reference, Track, TrackId,
    TrackSettings,
};
use crate::{image::RgbaImage, project::payload::V2Plan, Error};

fn invalid() -> Error {
    Error::Invalid("不正な v2 文書の読込計画".into())
}

fn array(value: &Value) -> Result<&[Value], Error> {
    value.as_array().map(Vec::as_slice).ok_or_else(invalid)
}

fn index(value: &Value) -> Result<usize, Error> {
    value
        .as_u64()
        .and_then(|n| usize::try_from(n).ok())
        .ok_or_else(invalid)
}

fn flag(value: &Value) -> Result<bool, Error> {
    value.as_bool().ok_or_else(invalid)
}

fn number(value: &Value) -> Result<CellNumber, Error> {
    CellNumber::new(value.as_i64().ok_or_else(invalid)?)
}

fn optional<T>(
    value: &Value,
    parse: impl FnOnce(&Value) -> Result<T, Error>,
) -> Result<Option<T>, Error> {
    if value.is_null() {
        Ok(None)
    } else {
        parse(value).map(Some)
    }
}

fn reference(value: &Value) -> Result<Reference, Error> {
    match value[0].as_str() {
        Some("CELL") => Ok(Reference::Number(number(&value[1])?)),
        Some("BLANK") => Ok(Reference::Blank),
        Some("EMPTY") => Ok(Reference::Empty),
        _ => Err(invalid()),
    }
}

fn string(value: &Value) -> Option<String> {
    value.as_str().map(str::to_owned)
}

const TWEEN_POINTS: [&str; 3] = ["start_points", "final_points", "mesh_reference_points"];

fn tween_int(value: &Value) -> Option<Value> {
    match value {
        Value::Number(n) if n.is_i64() || n.is_u64() => Some(value.clone()),
        Value::Number(n) => {
            let n = n.as_f64()?.trunc();
            if n >= i64::MIN as f64 && n < -(i64::MIN as f64) {
                Some(json!(n as i64))
            } else {
                None
            }
        }
        Value::Bool(b) => Some(json!(i64::from(*b))),
        Value::String(s) => s.trim().parse::<i64>().ok().map(|n| json!(n)),
        _ => None,
    }
}

fn tween(value: &Value) -> Option<Value> {
    let fields = value.as_object()?;
    let id = match fields.get("id")? {
        Value::String(id) => id.clone(),
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        other => other.to_string(),
    };
    let length = tween_int(fields.get("length")?)?;
    if id.is_empty() || length.as_f64()? < 2.0 {
        return None;
    }
    let mode = if value["mode"] == "mesh" {
        "mesh"
    } else {
        "free"
    };
    let reverse = match &value["reverse"] {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    };
    let mut result = json!({
        "id": id, "length": length, "mode": mode, "reverse": reverse,
        "mesh_cols": tween_int(fields.get("mesh_cols").unwrap_or(&json!(4)))?,
        "mesh_rows": tween_int(fields.get("mesh_rows").unwrap_or(&json!(4)))?,
    });
    for key in TWEEN_POINTS {
        let points = fields
            .get(key)
            .map_or(Some(&[][..]), |v| v.as_array().map(Vec::as_slice))?;
        if !points.iter().all(|point| {
            point.as_array().is_some_and(|xy| {
                xy.len() == 2 && xy.iter().all(|v| v.is_number() || v.is_boolean())
            })
        }) {
            return None;
        }
        result[key] = json!(points);
    }
    Some(result)
}

/// Python の project_v2._decode_payload に対応し、検証済み v2 計画を文書にする。
/// 画像は plan.images の順で渡す。PNG の展開と旧白消しは呼び出し元で済ませる。
pub fn from_plan(payload: &Value, plan: &V2Plan, images: &[RgbaImage]) -> Result<Document, Error> {
    let data = &plan.data;
    let width = index(&data["width"])?;
    let height = index(&data["height"])?;
    if width == 0
        || height == 0
        || images.len() != plan.images.len()
        || images
            .iter()
            .any(|image| (image.width(), image.height()) != (width, height))
    {
        return Err(invalid());
    }
    let mut blank = None;
    let mut artwork = |values: &Value| -> Result<Cell, Error> {
        let get = |value: &Value| -> Result<RgbaImage, Error> {
            images.get(index(value)?).cloned().ok_or_else(invalid)
        };
        let image = match optional(&values[0], get)? {
            Some(image) => image,
            None => {
                if blank.is_none() {
                    blank = Some(RgbaImage::blank(width, height)?);
                }
                blank.clone().ok_or_else(invalid)?
            }
        };
        Ok(Cell {
            image,
            initial_image: optional(&values[1], get)?,
            cell_name: string(&values[2]),
        })
    };
    let mut sheet = BTreeMap::new();
    let mut frame_order = Vec::new();
    for (row, duration) in array(&data["durations"])?.iter().enumerate() {
        let id = FrameId(row as u64);
        frame_order.push(id);
        sheet.insert(
            id,
            Frame {
                duration: duration.as_i64().filter(|d| *d > 0).ok_or_else(invalid)?,
                positions: BTreeMap::new(),
            },
        );
    }
    let current_frame = *frame_order
        .get(index(&data["current_frame"])?)
        .ok_or_else(invalid)?;
    let mut tracks = BTreeMap::new();
    let mut track_order = Vec::new();
    for (column, track) in array(&data["tracks"])?.iter().enumerate() {
        let id = TrackId(column as u64);
        let original = &payload["tracks"][column];
        let display = &track["display"];
        let settings = TrackSettings {
            name: string(&display[0]).unwrap_or_else(|| layer_name(column)),
            visible: flag(&display[1])?,
            opacity: display[2].as_f64().ok_or_else(invalid)?,
            alpha_locked: false,
            is_paper: flag(&display[3])?,
            is_draft: flag(&display[4])?,
            color_filter_enabled: flag(&display[5])?,
            color_filter_rgb: optional(&display[6], |rgb| {
                let component = |i: usize| u8::try_from(index(&rgb[i])?).map_err(|_| invalid());
                Ok([component(0)?, component(1)?, component(2)?])
            })?,
        };
        let mut cells = BTreeMap::new();
        for entry in array(&track["cells"])? {
            if cells
                .insert(number(&entry[0])?, artwork(&entry[1])?)
                .is_some()
            {
                return Err(invalid());
            }
        }
        let mut legacy_archive_hints = BTreeMap::new();
        for entry in array(&track["hints"])? {
            let hint = original["drawing_states"]
                .get(index(&entry[5])?)
                .ok_or_else(invalid)?;
            legacy_archive_hints.insert(
                number(&entry[0])?,
                ArchiveHint {
                    has_content: flag(&entry[1])?,
                    is_blank_key: flag(&entry[2])?,
                    exposure: index(&entry[3])?,
                    pending: flag(&entry[4])?,
                    tween: tween(&hint["tween"]),
                    tween_member: string(&hint["tween_member"]),
                },
            );
        }
        let slots = array(&track["slots"])?;
        if slots.len() != frame_order.len() {
            return Err(invalid());
        }
        for (row, slot) in slots.iter().enumerate() {
            let raw = optional(&slot[15], |v| {
                original["positions"].get(index(v)?).ok_or_else(invalid)
            })?
            .unwrap_or(&Value::Null);
            let raw_number = optional(&slot[2], number)?;
            let content = flag(&slot[0])?;
            let blank_key = flag(&slot[1])?;
            let pending = flag(&slot[3])?;
            let reference = match (content, raw_number, blank_key) {
                (true, Some(n), false) => Reference::Number(n),
                (_, _, true) => Reference::Blank,
                _ => Reference::Empty,
            };
            let retained_number = if matches!(reference, Reference::Number(_)) {
                None
            } else {
                raw_number
            };
            let local = optional(&slot[5], &mut artwork)?;
            let legacy_content = slot[6] == "CELL";
            let legacy_blank = slot[6] == "BLANK";
            let blank_start = flag(&slot[4])?;
            let tween = tween(&raw["tween"]);
            let tween_member = string(&raw["tween_member"]);
            let mut position = Position {
                reference,
                blank_start,
                pending,
                compatibility: PositionCompatibility {
                    retained_number,
                    local,
                    unnumbered_content: content && raw_number.is_none(),
                    legacy: Some(LegacyPosition {
                        legacy: Some(LegacySlot {
                            settings: settings.clone(),
                            state: ArchiveHint {
                                has_content: legacy_content,
                                is_blank_key: legacy_blank,
                                exposure: index(&slot[8])?,
                                pending,
                                tween: tween.clone(),
                                tween_member: tween_member.clone(),
                            },
                            number: optional(&slot[7], number)?,
                            artwork: None,
                        }),
                        key_start: legacy_content || legacy_blank,
                        blank_start,
                        pending,
                        input_length: optional(&slot[9], index)?,
                    }),
                    length_imported: optional(&slot[10], flag)?,
                    ..PositionCompatibility::default()
                },
                tween,
                tween_member,
            };
            let guard = PositionGuard::capture(&position);
            position.compatibility.dense_import = Some(DenseImport {
                guard,
                sheet: self::reference(&slot[11])?,
                resolved: self::reference(&slot[12])?,
            });
            if flag(&slot[13])? {
                let source_column = optional(&slot[14], index)?;
                if source_column.is_some_and(|source| source >= slots.len()) {
                    return Err(invalid());
                }
                position.compatibility.resolved_source = Some(ResolvedSource {
                    guard,
                    column: row,
                    frame_count: slots.len(),
                    source_column,
                });
            }
            sheet
                .get_mut(&frame_order[row])
                .ok_or_else(invalid)?
                .positions
                .insert(id, position);
        }
        track_order.push(id);
        tracks.insert(
            id,
            Track {
                settings,
                cells,
                legacy_archive_hints,
            },
        );
    }
    let active_layer = *track_order
        .get(index(&data["active_layer_index"])?)
        .ok_or_else(invalid)?;
    Ok(Document {
        next_frame_id: frame_order.len() as u64,
        next_track_id: track_order.len() as u64,
        blank: None,
        width,
        height,
        frame_order,
        track_order,
        sheet,
        tracks,
        current_frame,
        active_layer,
    })
}

/// v2 の PNG エントリ。画像はコアの COW ハンドル。
#[derive(Clone, Debug)]
pub struct PngEntry {
    /// ZIP に入れる相対パス。
    pub path: String,
    /// PNG に符号化する画像。
    pub image: RgbaImage,
}

/// Python の build_payload に対応する保存用の文書投影。
#[derive(Debug)]
pub struct Projection {
    /// project.json の値。
    pub payload: Value,
    /// PNG エントリをアーカイブに書く順の一覧。
    pub pngs: Vec<PngEntry>,
}

fn sheet_value(reference: Reference) -> Value {
    match reference {
        Reference::Number(n) => json!(n.get()),
        Reference::Blank => json!("BLANK"),
        Reference::Empty => Value::Null,
    }
}

fn kind(reference: Reference) -> &'static str {
    match reference {
        Reference::Number(_) => "CELL",
        Reference::Blank => "BLANK",
        Reference::Empty => "EMPTY",
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
struct Key {
    column: usize,
    reference: Reference,
    unnumbered: bool,
    length: usize,
}

impl Key {
    fn json(self) -> Value {
        json!({
            "column": self.column,
            "kind": if self.unnumbered { "CELL" } else { kind(self.reference) },
            "number": self.reference_number(), "length": self.length,
        })
    }

    fn reference_number(self) -> Option<i64> {
        match self.reference {
            Reference::Number(n) => Some(n.get()),
            _ => None,
        }
    }
}

fn image_entry(cell: &Cell, path: String, pngs: &mut Vec<PngEntry>) -> Value {
    let initial = cell
        .initial_image
        .as_ref()
        .map(|_| format!("initial/{path}"));
    let entry = json!({"image": path, "initial": initial, "cell_name": cell.cell_name});
    pngs.push(PngEntry {
        path,
        image: cell.image.clone(),
    });
    if let Some((path, image)) = initial.zip(cell.initial_image.as_ref()) {
        pngs.push(PngEntry {
            path,
            image: image.clone(),
        });
    }
    entry
}

fn source_artwork<'a>(track: &'a Track, position: &'a Position) -> Option<&'a RgbaImage> {
    if position.pending || matches!(position.reference, Reference::Number(_)) {
        if let Some(number) = position.number() {
            return track.cells.get(&number).map(|cell| &cell.image);
        }
    }
    position
        .compatibility
        .local
        .as_ref()
        .map(|cell| &cell.image)
}

fn compatibility(
    track: &Track,
    slots: &[&Position],
    references: &[Reference],
    projected: &[super::v2_placement::Placement],
    resolved: &[i64],
) -> Value {
    let mut ordinary = Vec::new();
    let mut column = 0;
    while column < slots.len() {
        let reference = references[column];
        let mut end = column + 1;
        while end < slots.len()
            && references[end] == reference
            && !(reference == Reference::Blank && slots[end].blank_start)
        {
            end += 1;
        }
        if reference != Reference::Empty && !slots[column].pending {
            ordinary.push(Key {
                column,
                reference,
                unnumbered: false,
                length: end - column,
            });
        }
        column = end;
    }
    let keys: Vec<_> = projected
        .iter()
        .enumerate()
        .filter_map(|(column, state)| {
            let reference = state.reference();
            (!state.pending && !slots[column].pending && (state.content || state.blank)).then_some(
                Key {
                    column,
                    reference,
                    unnumbered: state.content && state.number.is_none(),
                    length: state.length.max(1),
                },
            )
        })
        .collect();
    let mut result = json!({});
    if keys != ordinary {
        result["placement_keys"] = Value::Array(keys.iter().map(|key| key.json()).collect());
    }

    let normal: BTreeMap<_, _> = ordinary
        .iter()
        .map(|key| (key.column, key.length))
        .collect();
    let lengths: BTreeMap<_, _> = keys.iter().map(|key| (key.column, key.length)).collect();
    let mut overrides = Vec::new();
    let mut sources: BTreeMap<_, Vec<_>> = BTreeMap::new();
    for (column, (slot, reference)) in slots.iter().zip(references).enumerate() {
        let source_number = if slot.pending {
            slot.number()
        } else if let Reference::Number(number) = reference {
            Some(*number)
        } else {
            None
        };
        if let Some(number) = source_number {
            sources.entry(number).or_default().push(column);
        }
        let original = slot
            .compatibility
            .dense_import
            .filter(|import| import.guard.matches(slot))
            .filter(|_| slot.compatibility.length_imported.unwrap_or(true))
            .and(slot.compatibility.legacy.as_ref())
            .and_then(|old| {
                let raw = &old.legacy.as_ref()?.state;
                (!raw.pending && (raw.has_content || raw.is_blank_key))
                    .then_some(old.input_length.unwrap_or(raw.exposure).max(1))
            });
        if original.is_none() && !lengths.contains_key(&column) {
            continue;
        }
        let length = lengths.get(&column).copied().unwrap_or(1);
        let normal = normal.get(&column).copied().unwrap_or(1);
        if length == normal && original.is_none_or(|length| length == normal) {
            continue;
        }
        let mut entry = json!({"column": column, "length": length, "guard": {
            "kind": kind(*reference), "number": sheet_value(*reference).as_i64(),
            "pending": slot.pending, "blank_start": slot.blank_start,
            "imported": original.is_some(),
        }});
        if let Some(length) = original {
            entry["input_length"] = json!(length);
        }
        overrides.push(entry);
    }
    if !overrides.is_empty() {
        result["length_overrides"] = json!(overrides);
    }

    let mut run_start = 0;
    let mut differences = Vec::new();
    for (column, (&reference, slot)) in references.iter().zip(slots).enumerate() {
        if column == 0 || reference != references[column - 1] {
            run_start = column;
        }
        let ordinary = if let Reference::Number(number) = reference {
            (Some(number.get()), Some(run_start))
        } else {
            (None, None)
        };
        let mut source = usize::try_from(resolved[column]).ok();
        let value = if let Reference::Number(number) = slot.reference_for(true) {
            if let Some((old_source, candidates)) = source.zip(sources.get(&number)) {
                if candidates.binary_search(&old_source).is_err() {
                    let original = source_artwork(track, slots[old_source]);
                    let bank = track.cells.get(&number).map(|cell| &cell.image);
                    let same = match (original, bank) {
                        (Some(a), Some(b)) => {
                            a.generation() == b.generation() || a.rgba() == b.rgba()
                        }
                        (None, Some(b)) => b.rgba().iter().all(|byte| *byte == 0),
                        _ => false,
                    };
                    if same {
                        let index = candidates.partition_point(|&column| column <= old_source);
                        source = Some(candidates[index.saturating_sub(1)]);
                    }
                }
            }
            Some(number.get())
        } else {
            None
        };
        if (value, source) != ordinary {
            differences.push(json!({"column": column, "value": value, "source_column": source}));
        }
    }
    if !differences.is_empty() {
        result["resolved_overrides"] = json!(differences);
    }
    result
}

/// Python の project_v2.build_payload と同じ値と画像割り当てを作る。
/// metadata は保存時のスナップショットで、選択を含む文書外メタデータを保持する。
pub fn project(document: &Document, metadata: &Value) -> Result<Projection, Error> {
    let (width, height) = document.size();
    let mut payload = metadata.clone();
    let object = payload.as_object_mut().ok_or_else(invalid)?;
    for key in ["frames", "sequence_archive", "_loaded_sequence_archive"] {
        object.remove(key);
    }
    payload["format"] = json!("PaintMaskAnimatorProject");
    payload["format_version"] = json!(2);
    payload["mask_format"] = json!(2);
    payload["canvas"] = json!({"width": width, "height": height});
    payload["frame_durations"] = json!(document
        .frame_order
        .iter()
        .map(|id| document.sheet[id].duration)
        .collect::<Vec<_>>());

    let columns: Vec<Vec<_>> = document
        .track_order
        .iter()
        .map(|track| {
            document
                .frame_order
                .iter()
                .map(|frame| &document.sheet[frame].positions[track])
                .collect()
        })
        .collect();
    let projected = super::v2_placement::project(&columns)?;
    let (stride, resolved) = document.resolve_sources()?;
    let mut pngs = Vec::new();
    let mut tracks = Vec::new();
    for (li, id) in document.track_order.iter().enumerate() {
        let track = &document.tracks[id];
        let settings = &track.settings;
        let mut cells = Vec::new();
        for (number, cell) in &track.cells {
            let number = number.get();
            let mut entry = image_entry(
                cell,
                format!("layers/layer_{li:04}/cell_{number:06}.png"),
                &mut pngs,
            );
            entry["number"] = json!(number);
            cells.push(entry);
        }
        let hints: Vec<_> = track.legacy_archive_hints.iter().map(|(number, hint)| json!({
            "number": number.get(), "has_content": hint.has_content, "is_blank_key": hint.is_blank_key,
            "length": hint.exposure, "pending": hint.pending, "tween": hint.tween,
            "tween_member": hint.tween_member,
        })).collect();
        let slots = &columns[li];
        let references: Vec<_> = slots.iter().map(|slot| slot.reference_for(false)).collect();
        let sheet: Vec<_> = references.iter().copied().map(sheet_value).collect();
        let mut positions = Vec::new();
        let mut pending = Vec::new();
        let mut locals = Vec::new();
        for (column, slot) in slots.iter().enumerate() {
            if slot.blank_start || slot.tween.is_some() || slot.tween_member.is_some() {
                let mut entry = json!({"column": column});
                if slot.blank_start {
                    entry["blank_start"] = json!(true);
                }
                if let Some(tween) = &slot.tween {
                    entry["tween"] = tween.clone();
                }
                if let Some(member) = &slot.tween_member {
                    entry["tween_member"] = json!(member);
                }
                positions.push(entry);
            }
            if slot.pending {
                pending.push(json!({
                    "column": column, "number": slot.number().map(CellNumber::get),
                    "kind": if slot.compatibility.unnumbered_content { "CELL" } else { kind(slot.reference) },
                    "length": projected[li][column].length.max(1),
                }));
            }
            if slot.number().is_none()
                || (!slot.pending && !matches!(slot.reference, Reference::Number(_)))
            {
                let mut entry = json!({"column": column, "retained_number": slot.number().map(CellNumber::get)});
                if let Some(cell) = &slot.compatibility.local {
                    if cell.initial_image.is_some()
                        || cell.cell_name.is_some()
                        || cell.image.width() == 0
                        || cell.image.height() == 0
                        || cell.image.rgba().iter().any(|byte| *byte != 0)
                    {
                        let artwork = image_entry(
                            cell,
                            format!("compatibility/layer_{li:04}/position_{column:06}.png"),
                            &mut pngs,
                        );
                        for key in ["image", "initial", "cell_name"] {
                            entry[key] = artwork[key].clone();
                        }
                    }
                }
                locals.push(entry);
            }
        }
        let sources: Vec<_> = resolved.iter().skip(li).step_by(stride).copied().collect();
        let mut compat = compatibility(track, slots, &references, &projected[li], &sources);
        if !locals.is_empty() {
            compat["local_states"] = json!(locals);
        }
        let mut data = json!({
            "name": settings.name, "visible": settings.visible, "opacity": settings.opacity,
            "is_paper": settings.is_paper, "is_draft": settings.is_draft,
            "color_filter_enabled": settings.color_filter_enabled, "color_filter_rgb": settings.color_filter_rgb,
            "cells": cells, "sheet": sheet, "positions": positions, "pending": pending, "drawing_states": hints,
        });
        if compat.as_object().is_some_and(|fields| !fields.is_empty()) {
            data["compatibility"] = compat;
        }
        tracks.push(data);
    }
    payload["tracks"] = json!(tracks);
    Ok(Projection { payload, pngs })
}

/// 比較入口で使う JSON テキストを v2 の値に変換する。
pub fn parse_json(text: &str) -> Result<Value, Error> {
    serde_json::from_str(text).map_err(|error| Error::Invalid(error.to_string()))
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use super::*;
    use crate::document::changes::{Change, FrameOperation};
    use crate::project::{payload::decode_v2_plan, png::decode_png, zip::ZipReader};

    fn golden(bytes: &[u8]) -> (Document, Value) {
        let mut zip = ZipReader::open(Cursor::new(bytes)).unwrap();
        let entries = zip.entries().to_vec();
        let names: Vec<_> = entries.iter().map(|entry| entry.name.clone()).collect();
        let mut read = |name: &str| {
            let i = names.iter().position(|path| path == name).unwrap();
            let mut result = vec![0; entries[i].file_size as usize];
            zip.read_into(i, &mut result).unwrap();
            result
        };
        let input: Value = serde_json::from_slice(&read("project.json")).unwrap();
        let plan = decode_v2_plan(&input, &names).unwrap();
        let images: Vec<_> = plan
            .images
            .iter()
            .map(|image| {
                let png = decode_png(&read(image[0].as_str().unwrap()), 1_000_000).unwrap();
                RgbaImage::from_rgba(png.width, png.height, &png.pixels).unwrap()
            })
            .collect();
        let document = from_plan(&input, &plan, &images).unwrap();
        let result = project(&document, &input).unwrap();
        assert_eq!(result.payload, input);
        assert_eq!(
            result
                .pngs
                .iter()
                .map(|png| png.path.as_str())
                .collect::<Vec<_>>(),
            names
                .iter()
                .filter(|path| path.ends_with(".png"))
                .map(String::as_str)
                .collect::<Vec<_>>(),
        );
        for png in &result.pngs {
            let original = decode_png(&read(&png.path), 1_000_000).unwrap();
            assert_eq!(original.pixels.as_slice(), png.image.rgba(), "{}", png.path);
        }
        for id in document.track_order() {
            assert!(!document.track(*id).unwrap().settings.alpha_locked);
        }
        (document, input)
    }

    // tests/test_v2_golden.py:test_v2_golden
    #[test]
    fn v2_golden_roundtrips() {
        golden(include_bytes!(
            "../../../../tests/fixtures/projects/v2/basic-bank-sheet.pman"
        ));
        golden(include_bytes!(
            "../../../../tests/fixtures/projects/v2/paper-draft.pman"
        ));
        golden(include_bytes!(
            "../../../../tests/fixtures/projects/v2/pending-compat.pman"
        ));
        golden(include_bytes!(
            "../../../../tests/fixtures/projects/v2/tween-initial-display.pman"
        ));
    }

    // tests/test_project_v2.py:test_v2_compatibility_placement_matches_legacy_algorithm
    // tests/test_pending_slots.py:test_set_mode_places_only_pending_and_preserves_selection_and_undo
    #[test]
    fn pending_golden_preserves_resolve_and_display_until_placement() {
        let (mut document, input) = golden(include_bytes!(
            "../../../../tests/fixtures/projects/v2/pending-compat.pman"
        ));
        let (_, sources) = document.resolve_sources().unwrap();
        assert_eq!(&sources[12..18], &[4, -1, 4, 4, -1, 4]);
        for (layer, track) in document.track_order().iter().enumerate() {
            assert_eq!(
                document.placed_numbers(*track),
                [CellNumber::new(1).unwrap()]
            );
            assert_eq!(input["tracks"][layer]["sheet"][4], Value::Null);
        }
        assert_eq!(
            input["tracks"][0]["compatibility"]["length_overrides"][0]["input_length"],
            8
        );
        let selection = document.selection();
        assert_eq!(
            document.place_pending_entries().unwrap(),
            [
                Change::FrameStructure {
                    operation: FrameOperation::Trim,
                    before: 7,
                    after: 4,
                    start: 4
                },
                Change::SheetSpans {
                    layer_index: 0,
                    start: 0,
                    stop: 7
                },
                Change::SheetSpans {
                    layer_index: 2,
                    start: 0,
                    stop: 7
                },
                Change::Cursor {
                    frame: 0,
                    notify: true
                },
            ]
        );
        assert_eq!(document.selection(), selection);
        let projected = project(&document, &input).unwrap();
        let tracks = projected.payload["tracks"].as_array().unwrap();
        assert_eq!(tracks[0]["sheet"], json!([1, 2, 2, 2]));
        assert_eq!(tracks[1]["sheet"], json!([1, 1, 1, 1]));
        assert_eq!(tracks[2]["sheet"], json!([1, 1, 1, 9]));
        assert!(tracks.iter().all(|track| track["pending"] == json!([])));
        assert_eq!(
            document.resolve_sources().unwrap().1,
            [0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 3]
        );
    }

    // tests/test_project_v2.py:test_handwritten_v2_bank_initial_defaults_and_no_number_compaction
    #[test]
    fn v2_preserves_holes_and_missing_initial() {
        let input = json!({"canvas":{"width":1,"height":1},"frame_durations":[1,2,1],
            "tracks":[{"cells":[{"number":4,"image":"a.png"},{"number":1,"image":"b.png"}],
                "sheet":[4,null,4]}]});
        let plan = decode_v2_plan(&input, &[]).unwrap();
        let images = vec![RgbaImage::blank(1, 1).unwrap(); 2];
        let document = from_plan(&input, &plan, &images).unwrap();
        let result = project(&document, &json!({})).unwrap();
        let track = &result.payload["tracks"][0];
        assert_eq!(track["sheet"], json!([4, null, 4]));
        assert_eq!(track["cells"][0]["number"], 1);
        assert_eq!(track["cells"][1]["number"], 4);
        assert!(track["cells"][1]["initial"].is_null());
        assert_eq!(document.resolve_sources().unwrap(), (1, vec![0, -1, 2]));
    }

    // tests/test_project_v2_ui.py:test_signature_keeps_ui_exclusions
    #[test]
    fn alpha_lock_is_ignored_on_load_and_omitted_when_projecting() {
        use crate::document::commands::TrackProperty;

        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1],
            "tracks": [{"alpha_locked": true, "cells": [], "sheet": [null, null]}],
        });
        let plan = decode_v2_plan(&input, &[]).unwrap();
        let mut doc = from_plan(&input, &plan, &[]).unwrap();
        let track = doc.track_order()[0];
        assert!(!doc.track(track).unwrap().settings.alpha_locked);
        assert_eq!(
            doc.set_track_properties(
                &[0],
                &[
                    TrackProperty::Opacity(0.5),
                    TrackProperty::AlphaLocked(true)
                ],
                false,
            )
            .unwrap(),
            [Change::TrackProperties {
                indices: vec![0],
                fields: vec!["opacity", "alpha_locked"]
            }],
        );
        assert!(doc.track(track).unwrap().settings.alpha_locked);
        let projection = project(&doc, &input).unwrap();
        let track = &projection.payload["tracks"][0];
        assert_eq!(track["opacity"], 0.5);
        assert!(track.get("alpha_locked").is_none());
    }

    // tests/test_project_v2.py:test_v2_separate_input_length_clone_and_guard
    #[test]
    fn input_length_survives_a_clone_until_its_position_guard_expires() {
        let (document, input) = golden(include_bytes!(
            "../../../../tests/fixtures/projects/v2/pending-compat.pman"
        ));
        let slot = &input["tracks"][0]["compatibility"]["length_overrides"][0];
        let column = slot["column"].as_u64().unwrap() as usize;
        let length = slot["input_length"].as_u64().unwrap();
        let track = document.track_order()[0];
        let input_length = |doc: &Document| {
            let projected = project(doc, &input).unwrap();
            projected.payload["tracks"][0]["compatibility"]["length_overrides"]
                .as_array()
                .into_iter()
                .flatten()
                .find(|entry| entry["column"] == column)
                .and_then(|entry| entry["input_length"].as_u64())
        };
        for remove_marker in [false, true] {
            let mut cloned = document.clone();
            assert_eq!(input_length(&cloned), Some(length));
            let position = cloned.position_mut(column, track);
            if remove_marker {
                position.compatibility.dense_import = None;
            } else {
                position.blank_start = !position.blank_start;
            }
            assert_eq!(input_length(&cloned), None);
        }
        assert_eq!(input_length(&document), Some(length));
    }
}
