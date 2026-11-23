//! 比較テストが文書操作列を渡すための入口。

use super::changes::{Change, FrameOperation, TrackOperation};
use super::commands::{PreparedTrack, TrackProperty};
use super::drawing_commands::{DrawingImageGroup, DrawingTarget};
use super::frame_commands::RemapColumn;
use super::graph::optional_cell_value;
use super::model::{Cell, CellNumber, Document};
use super::position_commands::SheetMove;
use crate::{
    image::{Rect, RgbaImage},
    Error,
};
use serde_json::{json, Value};

fn invalid() -> Error {
    Error::Invalid("比較操作の引数が不正です".into())
}

fn index(value: &Value) -> Result<usize, Error> {
    value
        .as_u64()
        .and_then(|n| usize::try_from(n).ok())
        .ok_or_else(invalid)
}

fn indices(value: &Value) -> Result<Vec<usize>, Error> {
    value
        .as_array()
        .ok_or_else(invalid)?
        .iter()
        .map(index)
        .collect()
}

fn number(value: &Value) -> Result<CellNumber, Error> {
    CellNumber::new(value.as_i64().ok_or_else(invalid)?)
}

fn prepared_track(document: &Document, layer: usize) -> Result<PreparedTrack, Error> {
    let id = *document.track_order.get(layer).ok_or_else(invalid)?;
    Ok(PreparedTrack {
        track: document.tracks[&id].clone(),
        positions: document
            .frame_order
            .iter()
            .map(|frame| document.sheet[frame].positions[&id].clone())
            .collect(),
    })
}

fn bytes(value: &Value) -> Result<Vec<u8>, Error> {
    value
        .as_array()
        .ok_or_else(invalid)?
        .iter()
        .map(|value| u8::try_from(index(value)?).map_err(|_| invalid()))
        .collect()
}

fn notice(change: Change) -> Value {
    match change {
        Change::TrackStructure {
            operation,
            before,
            after,
        } => {
            let operation = match operation {
                TrackOperation::Insert => "insert",
                TrackOperation::Remove => "remove",
                TrackOperation::Duplicate => "duplicate",
                TrackOperation::Merge => "merge",
                TrackOperation::Move => "move",
            };
            json!({"kind": "TrackStructure", "operation": operation, "before": before, "after": after})
        }
        Change::TrackProperties { indices, fields } => {
            json!({"kind": "TrackProperties", "indices": indices, "fields": fields})
        }
        Change::Selection { layer_index } => {
            json!({"kind": "Selection", "layer_index": layer_index})
        }
        Change::FrameStructure {
            operation,
            before,
            after,
            start,
        } => {
            let operation = match operation {
                FrameOperation::Extend => "extend",
                FrameOperation::Duplicate => "duplicate",
                FrameOperation::Trim => "trim",
                FrameOperation::Restore => "restore",
            };
            json!({"kind": "FrameStructure", "operation": operation, "before": before, "after": after, "start": start})
        }
        Change::SheetSpans {
            layer_index,
            start,
            stop,
        } => {
            json!({"kind": "SheetSpans", "layer_index": layer_index, "start": start, "stop": stop})
        }
        Change::Cursor { frame, notify } => {
            json!({"kind": "Cursor", "frame": frame, "notify": notify})
        }
        Change::DrawingAttributes {
            layer_index,
            number,
            frame,
            fields,
        } => {
            json!({"kind": "DrawingAttributes", "layer_index": layer_index, "number": number.map(CellNumber::get), "frame": frame, "fields": fields})
        }
        Change::DrawingPixels {
            layer_index,
            number,
            frame,
            rects,
        } => {
            let rects: Vec<_> = rects
                .iter()
                .map(|r| [r.x, r.y, r.width, r.height])
                .collect();
            json!({"kind": "DrawingPixels", "layer_index": layer_index, "number": number.map(CellNumber::get), "frame": frame, "rects": rects})
        }
    }
}

/// 比較用 JSON 操作列を適用し、Python ChangeSet と同じ種類名とフィールドを発行順で返す。
/// 途中に不正な操作があれば文書全体を元の状態に保つ。
pub fn apply_operations(document: &mut Document, operations: &Value) -> Result<String, Error> {
    let mut edited = document.clone();
    let mut changes: Vec<Vec<Value>> = Vec::new();
    for operation in operations.as_array().ok_or_else(invalid)? {
        let args = operation.as_array().ok_or_else(invalid)?;
        let result = match args.as_slice() {
            [name] if name == "add_track" => edited.add_track(),
            [name] if name == "place_pending_entries" => edited.place_pending_entries(),
            [name] if name == "advance_frame" => edited.advance_frame(),
            [name, frame, layer] if name == "delete_frame" => {
                edited.delete_frame(index(frame)?, index(layer)?)
            }
            [name, frame, layer] if name == "delete_track_frame" => {
                edited.delete_track_frame(index(frame)?, index(layer)?)
            }
            [name, frame, layer] if name == "insert_track_frame" => {
                edited.insert_track_frame(index(frame)?, index(layer)?)
            }
            [name, at, source, active] if name == "insert_track" => {
                let prepared = prepared_track(&edited, index(source)?)?;
                edited.insert_track(index(at)?, prepared, Some(index(active)?))
            }
            [name, layers] if name == "remove_tracks" => {
                edited.remove_tracks(&indices(layers)?, None)
            }
            [name, layers, active] if name == "remove_tracks" => {
                edited.remove_tracks(&indices(layers)?, Some(index(active)?))
            }
            [name, layer, label] if name == "duplicate_tracks" => edited
                .duplicate_tracks(&[(index(layer)?, label.as_str().ok_or_else(invalid)?.into())]),
            // 複数の列を一度に複製する形（Python の duplicate_tracks({列: 名前, ...})）。
            [name, pairs] if name == "duplicate_tracks" => {
                let names = pairs
                    .as_array()
                    .ok_or_else(invalid)?
                    .iter()
                    .map(|pair| {
                        let [layer, label] = pair.as_array().ok_or_else(invalid)?.as_slice() else {
                            return Err(invalid());
                        };
                        Ok((
                            index(layer)?,
                            label.as_str().ok_or_else(invalid)?.to_owned(),
                        ))
                    })
                    .collect::<Result<Vec<_>, Error>>()?;
                edited.duplicate_tracks(&names)
            }
            [name, layers, source] if name == "merge_tracks" => {
                let prepared = prepared_track(&edited, index(source)?)?;
                edited.merge_tracks(&indices(layers)?, prepared)
            }
            [name, layers, visible, opacity, label] if name == "set_track_properties" => edited
                .set_track_properties(
                    &indices(layers)?,
                    &[
                        TrackProperty::Visible(visible.as_bool().ok_or_else(invalid)?),
                        TrackProperty::Opacity(opacity.as_f64().ok_or_else(invalid)?),
                        TrackProperty::Name(label.as_str().ok_or_else(invalid)?.into()),
                    ],
                    false,
                ),
            [name, source, source_layer, frame, layer, length] if name == "paste_numbered_cell" => {
                edited.paste_numbered_cell(
                    (index(source)?, index(source_layer)?),
                    (index(frame)?, index(layer)?),
                    index(length)?,
                )
            }
            // 文書の外の絵（クリップボードの写し）を貼る形。初期画像は無し。
            [name, frame, layer, length, n, width, height, rgba, cell_name]
                if name == "paste_sheet_cell" =>
            {
                let incoming = Cell {
                    image: RgbaImage::from_rgba(index(width)?, index(height)?, &bytes(rgba)?)?,
                    initial_image: None,
                    cell_name: cell_name.as_str().map(Into::into),
                };
                edited.paste_sheet_cell(
                    (index(frame)?, index(layer)?),
                    number(n)?,
                    &incoming,
                    index(length)?,
                )
            }
            [name, frame, layer, n] if name == "recall_sheet_number" => {
                edited.recall_sheet_number(index(frame)?, index(layer)?, number(n)?)
            }
            [name, layer, start, stop] if name == "clear_sheet_span" => {
                edited.clear_sheet_span(index(layer)?, index(start)?..index(stop)?)
            }
            [name, layer, n] if name == "delete_sequence_entry" => {
                edited.delete_sequence_entry(index(layer)?, number(n)?)
            }
            [name, layers] if name == "normalize_sheet_numbers" => {
                edited.normalize_sheet_numbers(&indices(layers)?)
            }
            [name, layer, first, second] if name == "move_sequence_image" => {
                edited.move_sequence_image(index(layer)?, number(first)?, number(second)?)
            }
            [name, layer, n] if name == "insert_sequence_blank" => {
                edited.insert_sequence_blank(index(layer)?, n.as_i64().ok_or_else(invalid)?)
            }
            [name, frame, layer, content] if name == "set_drawing_content" => edited
                .set_drawing_content(
                    index(frame)?,
                    index(layer)?,
                    content.as_bool().ok_or_else(invalid)?,
                ),
            [name, frame, layer, width, height, rgba] if name == "replace_drawing_pixels" => edited
                .replace_drawing_pixels(
                    index(layer)?,
                    DrawingTarget::Frame(index(frame)?),
                    RgbaImage::from_rgba(index(width)?, index(height)?, &bytes(rgba)?)?,
                ),
            [name, count] if name == "ensure_frame_count" => {
                edited.ensure_frame_count(index(count)?)
            }
            [name, column] if name == "duplicate_frame" => edited.duplicate_frame(index(column)?),
            [name, column, layer] if name == "new_sheet_number" => {
                edited.new_sheet_number(index(column)?, index(layer)?)
            }
            [name, column, layer] if name == "new_sheet_cell" => {
                edited.new_sheet_cell(index(column)?, index(layer)?)
            }
            [name, column, layer] if name == "create_blank_key" => {
                edited.create_blank_key(index(column)?, index(layer)?)
            }
            [name, column, layer] if name == "clear_initial_image" => edited.set_initial_images(
                &[(index(layer)?, DrawingTarget::Frame(index(column)?), None)],
                false,
            ),
            [name, source_frame, source_layer, target_frame, target_layer]
                if name == "move_sheet_cell" =>
            {
                edited.move_sheet_cell(
                    (index(source_frame)?, index(source_layer)?),
                    (index(target_frame)?, index(target_layer)?),
                )
            }
            [name, source, destination, layer] if name == "copy_sheet_cell" => {
                edited.copy_sheet_cell(index(source)?, index(destination)?, index(layer)?)
            }
            [name, column, layer] if name == "merge_sheet_clip" => {
                edited.merge_sheet_clip(index(column)?, index(layer)?)
            }
            [name, column, layer, duration] if name == "set_sheet_duration" => {
                edited.set_sheet_duration(index(column)?, index(layer)?, index(duration)?)
            }
            [name, layer, edge, key, old_end, new_start, new_end]
                if name == "resize_sheet_exposure" =>
            {
                edited.resize_sheet_exposure(
                    index(layer)?,
                    match edge.as_str() {
                        Some("left") => true,
                        Some("right") => false,
                        _ => return Err(invalid()),
                    },
                    index(key)?,
                    index(old_end)?,
                    index(new_start)?,
                    index(new_end)?,
                )
            }
            [name, order] if name == "move_tracks" => edited.move_tracks(
                &order
                    .as_array()
                    .ok_or_else(invalid)?
                    .iter()
                    .map(index)
                    .collect::<Result<Vec<_>, _>>()?,
            ),
            [name, movements, cursor, layer] if name == "move_sheet_cells" => {
                let moves = movements
                    .as_array()
                    .ok_or_else(invalid)?
                    .iter()
                    .map(|movement| {
                        let [source, source_layer, target, target_layer] =
                            movement.as_array().ok_or_else(invalid)?.as_slice()
                        else {
                            return Err(invalid());
                        };
                        Ok(SheetMove {
                            source: (index(source)?, index(source_layer)?),
                            destination: (index(target)?, index(target_layer)?),
                        })
                    })
                    .collect::<Result<Vec<_>, Error>>()?;
                edited.move_sheet_cells(&moves, index(cursor)?, index(layer)?)
            }
            [name, frame, layer, width, height, rgba, content]
                if name == "replace_drawing_image" =>
            {
                edited.replace_drawing_image(
                    index(frame)?,
                    index(layer)?,
                    RgbaImage::from_rgba(index(width)?, index(height)?, &bytes(rgba)?)?,
                    content.as_bool().ok_or_else(invalid)?,
                )
            }
            [name, column, layer] if name == "split_sheet_clip" => {
                edited.split_sheet_clip(index(column)?, index(layer)?)
            }
            [name, groups] if name == "replace_drawing_images" => {
                let mut parsed = Vec::new();
                for group in groups.as_array().ok_or_else(invalid)? {
                    let [positions, width, height, rgba] =
                        group.as_array().ok_or_else(invalid)?.as_slice()
                    else {
                        return Err(invalid());
                    };
                    let positions = positions
                        .as_array()
                        .ok_or_else(invalid)?
                        .iter()
                        .map(|position| {
                            let [frame, layer] =
                                position.as_array().ok_or_else(invalid)?.as_slice()
                            else {
                                return Err(invalid());
                            };
                            Ok((index(frame)?, index(layer)?))
                        })
                        .collect::<Result<Vec<_>, Error>>()?;
                    parsed.push((
                        positions,
                        RgbaImage::from_rgba(index(width)?, index(height)?, &bytes(rgba)?)?,
                    ));
                }
                let groups = parsed
                    .iter()
                    .map(|(positions, image)| DrawingImageGroup { positions, image })
                    .collect::<Vec<_>>();
                edited.replace_drawing_images(&groups)
            }
            [name, layer, frame, x, y, rgba, content] if name == "write_drawing_patch" => edited
                .write_drawing_patch_with_content(
                    index(frame)?,
                    index(layer)?,
                    Rect {
                        x: index(x)?,
                        y: index(y)?,
                        width: 1,
                        height: 1,
                    },
                    &bytes(rgba)?,
                    content.as_bool().ok_or_else(invalid)?,
                ),
            [name, start, layer, states, sources] if name == "remap" => {
                let start = index(start)?;
                let layer_index = index(layer)?;
                let states = states
                    .as_array()
                    .ok_or_else(invalid)?
                    .iter()
                    .map(|state| {
                        state
                            .as_i64()
                            .map(CellNumber::new)
                            .transpose()
                            .and_then(|number| {
                                if number.is_none() && !state.is_null() {
                                    return Err(invalid());
                                }
                                Ok(number)
                            })
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                let bank = sources
                    .as_array()
                    .ok_or_else(invalid)?
                    .iter()
                    .map(|entry| {
                        if entry.is_null() {
                            return Ok(None);
                        }
                        let [width, height, rgba] =
                            entry.as_array().ok_or_else(invalid)?.as_slice()
                        else {
                            return Err(invalid());
                        };
                        Ok(Some(RgbaImage::from_rgba(
                            index(width)?,
                            index(height)?,
                            &bytes(rgba)?,
                        )?))
                    })
                    .collect::<Result<Vec<_>, Error>>()?;
                let end = start.checked_add(states.len()).ok_or_else(invalid)?;
                edited.apply_sheet_remap(
                    start..end,
                    &[RemapColumn {
                        layer_index,
                        states: &states,
                        bank: &bank,
                    }],
                    start,
                )
            }
            [name, layer, frame, x, y, rgba] if name == "write_drawing_patch" => edited
                .write_drawing_patch(
                    index(layer)?,
                    DrawingTarget::Frame(index(frame)?),
                    Rect {
                        x: index(x)?,
                        y: index(y)?,
                        width: 1,
                        height: 1,
                    },
                    &bytes(rgba)?,
                ),
            _ => return Err(invalid()),
        }?;
        changes.push(result.into_iter().map(notice).collect());
    }
    *document = edited;
    Ok(json!(changes).to_string())
}

/// 別の文書 `source` の列 `source_layer` を、`document` の列 `at` へ insert_track で入れる。
///
/// Python の restore_inserted_track（外した列の写しを戻す）用。`source` は戻す列だけを
/// 持つ文書で、コマ数は `document` と同じであること。結果は apply_operations と同じ形の
/// JSON（操作一つ分）。失敗時は文書を変えない。
pub fn insert_track_from(
    document: &mut Document,
    source: &Document,
    source_layer: usize,
    at: usize,
    active: usize,
) -> Result<String, Error> {
    let prepared = prepared_track(source, source_layer)?;
    let result = document.insert_track(at, prepared, Some(active))?;
    let changes: Vec<Value> = result.into_iter().map(notice).collect();
    Ok(json!([changes]).to_string())
}

/// 番号 `number` の絵（`cell` は to_graph のバンクの一枚と同じ JSON、画像は `images` の
/// 添字）を、`document` の `column`・`layer` に Document::paste_sheet_cell で貼る。
///
/// Python の paste_sheet_cell（クリップボードの写しを貼る）用。結果は apply_operations と
/// 同じ形の JSON（操作一つ分）。失敗時は文書を変えない。
pub fn paste_sheet_cell(
    document: &mut Document,
    destination: (usize, usize),
    number: i64,
    cell: &Value,
    images: &[RgbaImage],
    length: usize,
) -> Result<String, Error> {
    let incoming = optional_cell_value(cell, images)?.ok_or_else(invalid)?;
    let number = CellNumber::new(number)?;
    let result = document.paste_sheet_cell(destination, number, &incoming, length)?;
    let changes: Vec<Value> = result.into_iter().map(notice).collect();
    Ok(json!([changes]).to_string())
}

/// 内容の無い位置に描き始める（make_empty_position_editable）。block は
/// (空キーか, 先頭, 長さ)。apply_operations と同じ形の JSON を返す。失敗時は文書を変えない。
pub fn make_empty_position_editable(
    document: &mut Document,
    position: (usize, usize),
    number: i64,
    block: Option<(bool, usize, usize)>,
    image: RgbaImage,
) -> Result<String, Error> {
    let number = CellNumber::new(number)?;
    let result =
        document.make_empty_position_editable(position.0, position.1, number, block, image)?;
    let changes: Vec<Value> = result.into_iter().map(notice).collect();
    Ok(json!([changes]).to_string())
}
