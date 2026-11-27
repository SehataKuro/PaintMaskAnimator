//! 旧形式（format_version 1）の入力を文書にする。
//!
//! Python の `project_io.load_project` が v1 で行う `Document.install_legacy_frames`
//! （document.py）と、`legacy_v1.expand_legacy` に対応する。読み込んだ直後の素の文書を
//! Rust の文書へ移したとき（document_core.graph_payload → from_graph）と同じ値を作る。
//!
//! Python と異なる点:
//! - 番号のない配置待ちの位置は、Python では局所の絵を作り直すときに今の作業領域の大きさの
//!   空画像（blank_model_image）を持つ。Rust はキャンバス（ファイル）の大きさの空画像にする。
//! - Python で Rust の文書へ移せない状態（色フィルタの色が 0〜255 の整数3つでない、
//!   配置待ちの位置が絵と空キーを同時に持つ）は、Python では素の文書のまま開く。Rust は
//!   [`crate::Error::Invalid`] を返す。
use std::collections::BTreeMap;
use std::sync::Arc;

use serde_json::Value;

use super::compatibility::{
    ArchiveHint, DenseImport, LegacyPosition, LegacySlot, PositionCompatibility, PositionGuard,
};
use super::model::{
    Cell, CellNumber, Document, Frame, FrameId, Position, Reference, Track, TrackId, TrackSettings,
};
use crate::{image::RgbaImage, Error};

/// 旧形式の1コマ・1レイヤー分の入力。Python の LegacyLayerV1（raw_v1_slot に渡す値）。
#[derive(Clone, Debug)]
pub struct LegacyLayerInput {
    /// レイヤー名。
    pub name: String,
    /// 絵。
    pub image: RgbaImage,
    /// 表示するか。
    pub visible: bool,
    /// 不透明度（0〜1 に丸めたもの）。
    pub opacity: f64,
    /// 用紙レイヤーか。
    pub is_paper: bool,
    /// 絵として記録されたか。
    pub has_content: bool,
    /// 旧保存の尺（1 以上）。
    pub exposure: usize,
    /// 色フィルタを適用するか。
    pub color_filter_enabled: bool,
    /// 色フィルタの色（Python の tuple(int(v) for v in rgb[:3])）。
    pub color_filter_rgb: Option<Vec<i64>>,
    /// 空キーとして記録されたか。
    pub is_blank_key: bool,
    /// 絵番号。
    pub sequence_number: Option<CellNumber>,
    /// 配置待ちか。
    pub sequence_only: bool,
    /// 下書きレイヤーか。
    pub is_draft: bool,
    /// セル名。
    pub cell_name: Option<String>,
    /// 補間キー設定（sanitize_spec 済み）。
    pub tween: Option<Value>,
    /// 中割りの所属。
    pub tween_member: Option<String>,
    /// 色塗り前の初期状態。
    pub initial_image: Option<RgbaImage>,
}

/// 旧連番保管の1件。Python の _loaded_sequence_archive の (layer_index, number) と値。
#[derive(Clone, Debug)]
pub struct LegacyArchiveInput {
    /// 列の表示インデックス。
    pub layer_index: usize,
    /// 絵番号。
    pub number: CellNumber,
    /// 保管されていた位置（exposure は 1、配置待ちではない）。
    pub layer: LegacyLayerInput,
}

/// 旧形式の1コマ。
#[derive(Clone, Debug)]
pub struct LegacyFrameInput {
    /// 再生時のコマの持続時間。
    pub duration: i64,
    /// 列ごとの入力。全コマで同じ数。
    pub layers: Vec<LegacyLayerInput>,
}

/// install_legacy_frames の途中の位置（Python の素の Layer の状態）。
struct Slot {
    has_content: bool,
    is_blank_key: bool,
    number: Option<CellNumber>,
    pending: bool,
    blank_start: bool,
    /// Layer.__dict__["_cell"]（局所の絵）。
    local: Option<Cell>,
}

/// Python の DenseReference。
#[derive(Clone, Copy, PartialEq, Eq)]
enum Dense {
    Cell(CellNumber),
    Blank,
    Empty,
}

impl Dense {
    fn reference(self) -> Reference {
        match self {
            Self::Cell(number) => Reference::Number(number),
            Self::Blank => Reference::Blank,
            Self::Empty => Reference::Empty,
        }
    }
}

fn invalid(reason: &str) -> Error {
    Error::Invalid(format!("install_legacy_v1: {reason}"))
}

fn settings_of(layer: &LegacyLayerInput) -> Result<TrackSettings, Error> {
    let color_filter_rgb = match &layer.color_filter_rgb {
        None => None,
        Some(rgb) => {
            // document_core._settings は整数3つを求め、Rust の文書は 0〜255 で持つ。
            let rgb: Vec<u8> = rgb
                .iter()
                .map(|&value| u8::try_from(value))
                .collect::<Result<_, _>>()
                .map_err(|_| invalid("色フィルタの色が 0〜255 でない"))?;
            Some(
                <[u8; 3]>::try_from(rgb.as_slice())
                    .map_err(|_| invalid("色フィルタの色が3つでない"))?,
            )
        }
    };
    Ok(TrackSettings {
        name: layer.name.clone(),
        visible: layer.visible,
        opacity: layer.opacity,
        // 旧形式の透明部分の保護は読まない。
        alpha_locked: false,
        is_paper: layer.is_paper,
        is_draft: layer.is_draft,
        color_filter_enabled: layer.color_filter_enabled,
        color_filter_rgb,
    })
}

fn cell_of(layer: &LegacyLayerInput) -> Cell {
    Cell {
        image: layer.image.clone(),
        initial_image: layer.initial_image.clone(),
        cell_name: layer.cell_name.clone(),
        is_genga: false,
    }
}

/// 番号ごとの候補。Python の numbered の値（位置か旧連番保管）。
#[derive(Clone, Copy)]
enum Candidate {
    Slot(usize),
    Archive(usize),
}

/// 番号の候補の一覧へ足す（Python の dict.setdefault(number, []).append）。
fn push(
    numbered: &mut Vec<(CellNumber, Vec<Candidate>)>,
    number: CellNumber,
    candidate: Candidate,
) {
    match numbered.iter_mut().find(|(n, _)| *n == number) {
        Some((_, list)) => list.push(candidate),
        None => numbered.push((number, vec![candidate])),
    }
}

/// legacy_v1.expand_legacy の一列分（resolve で表示用か画像解決用か）。
fn expand(rows: &[LegacySlot], resolve: bool) -> Result<Vec<Dense>, Error> {
    let mut result = vec![Dense::Empty; rows.len()];
    let (mut current, mut end) = (Dense::Empty, 0usize);
    for (column, slot) in rows.iter().enumerate() {
        if resolve || !slot.state.pending {
            let length = slot.state.exposure.max(1);
            if slot.state.has_content {
                let number = slot.number.ok_or_else(|| invalid("番号のない絵"))?;
                current = Dense::Cell(number);
                end = column + length;
            } else if slot.state.is_blank_key {
                current = if resolve { Dense::Empty } else { Dense::Blank };
                end = if resolve { 0 } else { column + length };
            }
        }
        if column < end {
            result[column] = current;
        }
    }
    Ok(result)
}

impl Document {
    /// 旧形式の入力を、Python の install_legacy_frames と同じ手順で文書にする。
    ///
    /// current_frame・active_layer は範囲に丸めた表示インデックス（load_project と同じ）。
    /// 番号のない絵には列の最大の番号の次から番号を振り、旧連番保管はバンクと属性の記録へ
    /// 移す。位置には旧入力のキー境界（v1_position）と取り込み時の参照（dense_import）を残す。
    pub fn install_legacy_v1(
        width: usize,
        height: usize,
        frames: &[LegacyFrameInput],
        archive: &[LegacyArchiveInput],
        current_frame: usize,
        active_layer: usize,
    ) -> Result<Self, Error> {
        let count = frames.first().map_or(0, |frame| frame.layers.len());
        if count == 0 || frames.iter().any(|frame| frame.layers.len() != count) {
            return Err(invalid("コマごとのレイヤー数が一致しない"));
        }
        if current_frame >= frames.len() || active_layer >= count {
            return Err(invalid("選択位置"));
        }
        if archive.iter().any(|entry| entry.layer_index >= count) {
            return Err(invalid("旧連番保管の列"));
        }
        let blank = RgbaImage::blank(width, height)?;
        let mut tracks = BTreeMap::new();
        let mut columns: Vec<Vec<Position>> = Vec::with_capacity(count);
        for li in 0..count {
            let inputs: Vec<&LegacyLayerInput> =
                frames.iter().map(|frame| &frame.layers[li]).collect();
            let archived: Vec<&LegacyArchiveInput> = archive
                .iter()
                .filter(|entry| entry.layer_index == li)
                .collect();
            let mut slots: Vec<Slot> = inputs
                .iter()
                .map(|layer| Slot {
                    has_content: layer.has_content,
                    is_blank_key: layer.is_blank_key,
                    number: layer.sequence_number,
                    pending: layer.sequence_only,
                    blank_start: false,
                    local: Some(cell_of(layer)),
                })
                .collect();

            // 番号ごとの候補（Python の dict と同じく最初に現れた順）。
            let mut numbered: Vec<(CellNumber, Vec<Candidate>)> = Vec::new();
            for (column, slot) in slots.iter().enumerate() {
                if let Some(number) = slot.number {
                    if slot.has_content || slot.pending {
                        push(&mut numbered, number, Candidate::Slot(column));
                    }
                }
            }
            for (index, entry) in archived.iter().enumerate() {
                push(&mut numbered, entry.number, Candidate::Archive(index));
            }
            // 空キー・未使用に保持された番号は、ほかに候補のない番号にだけ使う。
            let mut retained: Vec<(CellNumber, Vec<Candidate>)> = Vec::new();
            for (column, slot) in slots.iter().enumerate() {
                if let Some(number) = slot.number {
                    push(&mut retained, number, Candidate::Slot(column));
                }
            }
            for (number, candidates) in retained {
                if !numbered.iter().any(|(n, _)| *n == number) {
                    numbered.push((number, candidates));
                }
            }

            let content_of = |candidate: Candidate| match candidate {
                Candidate::Slot(column) => slots[column].has_content,
                Candidate::Archive(index) => archived[index].layer.has_content,
            };
            let cell_for = |candidate: Candidate| -> Option<&Cell> {
                match candidate {
                    Candidate::Slot(column) => slots[column].local.as_ref(),
                    Candidate::Archive(_) => None,
                }
            };
            let mut cells: BTreeMap<CellNumber, Cell> = BTreeMap::new();
            for (number, candidates) in &numbered {
                let content: Vec<Candidate> = candidates
                    .iter()
                    .copied()
                    .filter(|&c| content_of(c))
                    .collect();
                let mut owner = content.first().copied().unwrap_or(candidates[0]);
                // 今のコマの絵があれば、それをバンクの絵にする。
                if content
                    .iter()
                    .any(|c| matches!(c, Candidate::Slot(column) if *column == current_frame))
                    && slots[current_frame].number == Some(*number)
                {
                    owner = Candidate::Slot(current_frame);
                }
                let mut sources: Vec<Candidate> = slots
                    .iter()
                    .enumerate()
                    .filter(|(_, slot)| slot.has_content && slot.number == Some(*number))
                    .map(|(column, _)| Candidate::Slot(column))
                    .collect();
                sources.extend(
                    archived
                        .iter()
                        .enumerate()
                        .filter(|(_, entry)| entry.number == *number)
                        .map(|(index, _)| Candidate::Archive(index)),
                );
                if sources.is_empty() {
                    sources.clone_from(candidates);
                }
                let artwork = |candidate: Candidate| -> Result<Cell, Error> {
                    match candidate {
                        Candidate::Slot(_) => cell_for(candidate)
                            .cloned()
                            .ok_or_else(|| invalid("局所の絵がない")),
                        Candidate::Archive(index) => Ok(cell_of(&archived[index].layer)),
                    }
                };
                let mut initial_image = None;
                let mut cell_name = None;
                for &source in &sources {
                    let cell = artwork(source)?;
                    if initial_image.is_none() {
                        initial_image = cell.initial_image;
                    }
                    if cell_name.is_none() {
                        cell_name = cell.cell_name;
                    }
                }
                // Python は絵も配置待ちもない番号（rescued）の画像を複製するが、
                // Rust の画像は値なので同じになる。
                cells.insert(
                    *number,
                    Cell {
                        image: artwork(owner)?.image,
                        initial_image,
                        cell_name,
                        is_genga: false,
                    },
                );
            }
            let mut next = cells.keys().next_back().map_or(0, |n| n.get()) + 1;
            for slot in &mut slots {
                if slot.has_content && slot.number.is_none() {
                    let number = CellNumber::new(next)?;
                    let cell = slot
                        .local
                        .clone()
                        .ok_or_else(|| invalid("局所の絵がない"))?;
                    cells.insert(number, cell);
                    slot.number = Some(number);
                    next += 1;
                }
            }
            for slot in &mut slots {
                if slot.has_content || slot.pending {
                    slot.local = None;
                }
            }
            let mut hints = BTreeMap::new();
            for entry in &archived {
                hints.insert(
                    entry.number,
                    ArchiveHint {
                        has_content: entry.layer.has_content,
                        is_blank_key: entry.layer.is_blank_key,
                        exposure: 1,
                        pending: false,
                        tween: None,
                        tween_member: None,
                    },
                );
            }
            let settings = settings_of(inputs[0])?;

            // 旧入力の写し（snapshot_layer）。列の設定は先頭のコマのもの、絵はバンクか局所。
            let mut rows = Vec::with_capacity(slots.len());
            for (slot, input) in slots.iter_mut().zip(&inputs) {
                let bank = match slot.number {
                    Some(number) if slot.has_content || slot.pending => Some(
                        cells
                            .get(&number)
                            .ok_or_else(|| invalid("バンクにない番号"))?,
                    ),
                    _ => None,
                };
                let artwork = match bank {
                    Some(cell) => cell.clone(),
                    None => slot
                        .local
                        .get_or_insert_with(|| Cell {
                            image: blank.clone(),
                            initial_image: None,
                            cell_name: None,
                            is_genga: false,
                        })
                        .clone(),
                };
                rows.push(LegacySlot {
                    settings: settings.clone(),
                    state: ArchiveHint {
                        has_content: slot.has_content,
                        is_blank_key: slot.is_blank_key,
                        exposure: input.exposure,
                        pending: slot.pending,
                        tween: input.tween.clone(),
                        tween_member: input.tween_member.clone(),
                    },
                    number: slot.number,
                    artwork: Some(artwork),
                });
            }
            let sheet = expand(&rows, false)?;
            let resolved = expand(&rows, true)?;

            let mut column_positions = Vec::with_capacity(slots.len());
            for (column, (mut slot, legacy)) in slots.into_iter().zip(rows).enumerate() {
                let key_start = legacy.state.has_content || legacy.state.is_blank_key;
                let blank_start = legacy.state.is_blank_key && !legacy.state.has_content;
                let pending = legacy.state.pending;
                let reference = sheet[column];
                if !slot.pending {
                    slot.has_content = matches!(reference, Dense::Cell(_));
                    slot.is_blank_key = reference == Dense::Blank;
                    slot.blank_start = blank_start;
                    if let Dense::Cell(number) = reference {
                        slot.number = Some(number);
                        slot.local = None;
                    }
                }
                let input = inputs[column];
                let (reference_state, retained_number) =
                    match (slot.has_content, slot.is_blank_key, slot.number) {
                        (true, true, _) => return Err(invalid("絵と空キーが同時に立っている位置")),
                        (true, false, Some(number)) => (Reference::Number(number), None),
                        (true, false, None) => return Err(invalid("番号のない絵")),
                        (false, true, number) => (Reference::Blank, number),
                        (false, false, number) => (Reference::Empty, number),
                    };
                let guard = PositionGuard {
                    reference: reference_state,
                    number: slot.number,
                    unnumbered_content: false,
                    pending: slot.pending,
                    blank_start: slot.blank_start,
                };
                column_positions.push(Position {
                    reference: reference_state,
                    blank_start: slot.blank_start,
                    tween: input.tween.clone(),
                    tween_member: input.tween_member.clone(),
                    pending: slot.pending,
                    compatibility: PositionCompatibility {
                        retained_number,
                        local: slot.local,
                        legacy: Some(LegacyPosition {
                            legacy: Some(legacy),
                            key_start,
                            blank_start,
                            pending,
                            input_length: None,
                        }),
                        dense_import: Some(DenseImport {
                            guard,
                            sheet: reference.reference(),
                            resolved: resolved[column].reference(),
                        }),
                        ..PositionCompatibility::default()
                    },
                });
            }
            columns.push(column_positions);
            tracks.insert(
                TrackId(li as u64),
                Track {
                    settings,
                    cells,
                    legacy_archive_hints: hints,
                    import_sources: None,
                },
            );
        }

        let frame_order: Vec<FrameId> = (0..frames.len() as u64).map(FrameId).collect();
        let track_order: Vec<TrackId> = (0..count as u64).map(TrackId).collect();
        let mut columns: Vec<_> = columns.into_iter().map(Vec::into_iter).collect();
        let mut sheet = BTreeMap::new();
        for (&id, frame) in frame_order.iter().zip(frames) {
            let positions = track_order
                .iter()
                .zip(&mut columns)
                .map(|(&track, column)| Ok((track, column.next().ok_or_else(|| invalid("位置"))?)))
                .collect::<Result<_, Error>>()?;
            sheet.insert(
                id,
                Arc::new(Frame {
                    duration: frame.duration,
                    positions,
                }),
            );
        }
        Ok(Self {
            width,
            height,
            next_frame_id: frame_order.len() as u64,
            next_track_id: track_order.len() as u64,
            current_frame: frame_order[current_frame],
            active_layer: track_order[active_layer],
            frame_order,
            track_order,
            sheet,
            tracks,
            blank: None,
        })
    }
}
