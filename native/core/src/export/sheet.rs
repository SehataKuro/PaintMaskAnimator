//! 文書から書き出す内容を読む（`ExportController` の sheet_tracks・cut_folder_cels・
//! key_sequence・shadow_materials、`TimeRemapController.timeline_states`）。
//!
//! Python の Layer の値は文書の位置から次のように読む（`document::graph` の写しと同じ）。
//! has_content は番号の絵か番号なしの絵、sequence_only は配置待ち、sequence_number は
//! 位置の番号（保持番号を含む）、image はバンクの絵か位置の局所の絵。

use std::collections::BTreeSet;

use crate::document::model::{Document, FrameId, Position, Reference, TrackId};
use crate::document::timeline::{SHEET_BLANK, SHEET_CELL};
use crate::document::timeline_panel::default_layer_name;
use crate::image::RgbaImage;
use crate::Error;

use super::cut_folder::{safe_cell_name, CelSource, SheetFormat, SheetHeader, INVALID_NAME_CHARS};
use super::timesheet::{self, HYPHEN, NULL_CELL};

/// Python の `layer.has_content`。
pub fn has_content(position: &Position) -> bool {
    match position.reference {
        Reference::Number(_) => true,
        Reference::Blank => false,
        Reference::Empty => position.compatibility.unnumbered_content,
    }
}

/// Python の `layer.image`（バンクの絵か位置の局所の絵）。絵がなければ None（空の画像）。
pub fn slot_image(document: &Document, frame: usize, layer: usize) -> Option<&RgbaImage> {
    let (track, position) = slot(document, frame, layer)?;
    let owner = document.track(track)?;
    if position.references_bank() {
        position
            .number()
            .and_then(|number| owner.cells.get(&number))
            .map(|cell| &cell.image)
    } else {
        position
            .compatibility
            .local
            .as_ref()
            .map(|cell| &cell.image)
    }
}

fn slot(document: &Document, frame: usize, layer: usize) -> Option<(TrackId, &Position)> {
    let track = *document.track_order().get(layer)?;
    let frame: FrameId = *document.frame_order().get(frame)?;
    Some((track, document.frame(frame)?.positions.get(&track)?))
}

fn track_name(document: &Document, layer: usize) -> String {
    document
        .track_order()
        .get(layer)
        .and_then(|id| document.track(*id))
        .map(|track| track.settings.name.clone())
        .unwrap_or_default()
}

/// タイムシートの列名とコマごとの指示（`sheet_tracks`）。指示はセル番号か
/// `SYMBOL_HYPHEN`・`SYMBOL_NULL_CELL`。duration は [`Document::sheet_duration`]。
pub fn sheet_tracks(
    document: &Document,
    duration: usize,
) -> Result<(Vec<String>, Vec<Vec<String>>), Error> {
    let (width, entries) = document.sheet_entries()?;
    let frames = document.frame_order().len();
    let layers = document.track_order().len();
    let mut names = Vec::with_capacity(layers);
    let mut tracks = Vec::with_capacity(layers);
    for layer in 0..layers {
        names.push(track_name(document, layer));
        let mut values = Vec::with_capacity(duration);
        for column in 0..duration {
            let entry =
                (column < frames && layer < width).then(|| &entries[column * width + layer]);
            let value = match entry {
                Some(entry) if entry.kind == SHEET_CELL => {
                    if entry.key < 0 {
                        NULL_CELL.to_string()
                    } else if column as i64 == entry.key {
                        entry
                            .number
                            .map_or_else(|| NULL_CELL.to_string(), |n| n.to_string())
                    } else {
                        HYPHEN.to_string()
                    }
                }
                Some(entry) if entry.kind == SHEET_BLANK => if column as i64 == entry.key {
                    NULL_CELL
                } else {
                    HYPHEN
                }
                .to_string(),
                _ => NULL_CELL.to_string(),
            };
            values.push(value);
        }
        tracks.push(values);
    }
    Ok((names, tracks))
}

/// 今のシートのタイムシートの文字列（`sheet_text`）。
pub fn sheet_text(
    document: &Document,
    format: SheetFormat,
    sheet_name: &str,
    header: &SheetHeader,
    playback_range_end: Option<i64>,
) -> Result<String, Error> {
    let duration = document.sheet_duration(playback_range_end)?;
    let (names, tracks) = sheet_tracks(document, duration)?;
    Ok(timesheet::sheet_text(
        format, sheet_name, &names, &tracks, duration, header,
    ))
}

/// 書き出す絵（列とセル番号ごとに1枚、最初に見つけたもの）と、セル番号のない絵の数
/// （`cut_folder_cels`）。
pub fn cut_folder_cels(document: &Document) -> (Vec<CelSource>, usize) {
    let mut cels = Vec::new();
    let mut skipped = 0;
    for (layer, track) in document.track_order().iter().enumerate() {
        let name = safe_cell_name(&track_name(document, layer), layer);
        let mut seen = BTreeSet::new();
        for (frame, id) in document.frame_order().iter().enumerate() {
            let Some(position) = document.frame(*id).and_then(|row| row.positions.get(track))
            else {
                continue;
            };
            if !has_content(position) || position.pending {
                continue;
            }
            let Some(number) = position.number() else {
                skipped += 1;
                continue;
            };
            if seen.insert(number.get()) {
                cels.push(CelSource {
                    layer_index: layer,
                    cell: name.clone(),
                    number: number.get(),
                    frame,
                });
            }
        }
    }
    (cels, skipped)
}

/// 連番書き出しのレイヤーのフォルダー名（重なれば `_2`…、`key_sequence`）。
pub fn key_sequence_layer_names(document: &Document) -> Vec<String> {
    let invalid = format!("{INVALID_NAME_CHARS}/");
    let mut used = BTreeSet::new();
    let mut names = Vec::new();
    for layer in 0..document.track_order().len() {
        let name = track_name(document, layer);
        let fallback = default_layer_name(layer);
        let stripped = super::py::strip(&name);
        let source = if stripped.is_empty() {
            fallback.as_str()
        } else {
            stripped
        };
        let replaced: String = source
            .chars()
            .map(|c| if invalid.contains(c) { '_' } else { c })
            .collect();
        let trimmed = super::py::strip_chars(&replaced, " .");
        let base = if trimmed.is_empty() {
            fallback
        } else {
            trimmed.to_string()
        };
        let mut safe = base.clone();
        let mut suffix = 2;
        while used.contains(&super::py::casefold(&safe)) {
            safe = format!("{base}_{suffix}");
            suffix += 1;
        }
        used.insert(super::py::casefold(&safe));
        names.push(safe);
    }
    names
}

/// 連番書き出しの1枚。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SequenceKey {
    /// レイヤーの表示順の番号。
    pub layer_index: usize,
    /// レイヤーのフォルダー名。
    pub folder: String,
    /// キーのコマ（0 始まり）。
    pub frame: usize,
    /// 尺（コマ数）。
    pub exposure: usize,
    /// ファイル名（拡張子なし、`レイヤー名0001`）。
    pub file_stem: String,
}

/// 連番書き出しのキー（絵のある保持の頭）を、レイヤー順・コマ順に返す（`key_sequence`）。
pub fn key_sequence_keys(document: &Document) -> Vec<SequenceKey> {
    let names = key_sequence_layer_names(document);
    let mut keys = Vec::new();
    for (layer, folder) in names.iter().enumerate() {
        let mut count = 0;
        for frame in 0..document.frame_order().len() {
            let Some((_, position)) = slot(document, frame, layer) else {
                continue;
            };
            if !has_content(position) {
                continue;
            }
            let Some((_, start, length)) = document.timeline_block_at(frame, layer) else {
                continue;
            };
            if start != frame {
                continue;
            }
            count += 1;
            keys.push(SequenceKey {
                layer_index: layer,
                folder: folder.clone(),
                frame,
                exposure: length,
                file_stem: format!("{folder}{count:04}"),
            });
        }
    }
    keys
}

fn csv_field(value: &str) -> String {
    if value.contains([',', '"', '\r', '\n']) {
        format!("\"{}\"", value.replace('"', "\"\""))
    } else {
        value.to_string()
    }
}

/// 連番書き出しの TS.csv のバイト列（UTF-8 の BOM 付き・CRLF、Python の csv.writer）。
pub fn key_sequence_csv(keys: &[SequenceKey]) -> Vec<u8> {
    let mut text = String::from("\u{feff}name,start_frame,end_frame,duration\r\n");
    for key in keys {
        text.push_str(&format!(
            "{},{},{},{}\r\n",
            csv_field(&key.file_stem),
            key.frame + 1,
            key.frame + key.exposure,
            key.exposure
        ));
    }
    text.into_bytes()
}

/// 影素材を作る絵（`shadow_materials` の対象）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ShadowCel {
    /// レイヤー名（空なら既定の名前。整えない）。
    pub layer_name: String,
    /// 絵番号（番号がなければコマ番号 + 1）。
    pub number: i64,
    /// レイヤーの表示順の番号。
    pub layer_index: usize,
    /// 絵のあるコマ。
    pub frame: usize,
}

/// 影素材を作る絵を、レイヤー順・コマ順に返す（同じ番号は1回だけ）。
pub fn shadow_cels(document: &Document) -> Vec<ShadowCel> {
    let mut cels = Vec::new();
    for (layer, track) in document.track_order().iter().enumerate() {
        let name = track_name(document, layer);
        let layer_name = if name.is_empty() {
            default_layer_name(layer)
        } else {
            name
        };
        let mut seen = BTreeSet::new();
        for (frame, id) in document.frame_order().iter().enumerate() {
            let Some(position) = document.frame(*id).and_then(|row| row.positions.get(track))
            else {
                continue;
            };
            if !has_content(position) || position.pending {
                continue;
            }
            let number = position
                .number()
                .map_or(frame as i64 + 1, |number| number.get());
            if seen.insert(number) {
                cels.push(ShadowCel {
                    layer_name: layer_name.clone(),
                    number,
                    layer_index: layer,
                    frame,
                });
            }
        }
    }
    cels
}

/// レイヤー1本の絵番号の並び（`sheet.sheet_numbers`）。空セル・未使用・番号のない絵は None。
pub fn sheet_numbers(document: &Document, layer: usize) -> Result<Vec<Option<i64>>, Error> {
    let (width, entries) = document.sheet_entries()?;
    Ok((0..document.frame_order().len())
        .map(|column| {
            (layer < width)
                .then(|| &entries[column * width + layer])
                .filter(|entry| entry.kind == SHEET_CELL)
                .and_then(|entry| entry.number)
        })
        .collect())
}
