use std::collections::{BTreeMap, BTreeSet};
use std::num::NonZeroI64;

use serde_json::Value;

use super::compatibility::{ArchiveHint, PositionCompatibility};
use super::timeline::{self, SheetEntry, TimelineCell};
use crate::{image::RgbaImage, Error};

/// 表示順に依存しない列の識別子。
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct TrackId(pub u64);

/// 表示順に依存しないコマの識別子。
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct FrameId(pub u64);

/// 各列の中で絵を識別する正の整数。
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct CellNumber(NonZeroI64);

impl CellNumber {
    /// Python の TimelineCell が表現できる正の整数を連番にする。
    pub fn new(value: i64) -> Result<Self, Error> {
        NonZeroI64::new(value)
            .filter(|value| value.get() > 0)
            .map(Self)
            .ok_or_else(|| Error::Invalid(format!("不正なセル番号: {value}")))
    }

    /// JSON と既存タイムラインに渡せる整数を返す。
    pub fn get(self) -> i64 {
        self.0.get()
    }
}

/// Python の Cell に対応する一枚の絵。複製時は画素を COW で共有する。
#[derive(Clone, Debug)]
pub struct Cell {
    /// 描画中の画像。
    pub image: RgbaImage,
    /// 復元用の初期画像。
    pub initial_image: Option<RgbaImage>,
    /// 絵の名前。
    pub cell_name: Option<String>,
}

/// Python の Track に対応する列共通の表示設定。
#[derive(Clone, Debug, PartialEq)]
pub struct TrackSettings {
    /// レイヤー名。
    pub name: String,
    /// 表示するか。
    pub visible: bool,
    /// 不透明度。
    pub opacity: f64,
    /// 透明部分を保護するか。
    pub alpha_locked: bool,
    /// 用紙レイヤーか。
    pub is_paper: bool,
    /// 下書きレイヤーか。
    pub is_draft: bool,
    /// 色フィルタを適用するか。
    pub color_filter_enabled: bool,
    /// 色フィルタの RGB。
    pub color_filter_rgb: Option<[u8; 3]>,
}

/// 共通設定と、列内で唯一の番号付きセル保管先。
#[derive(Clone, Debug)]
pub struct Track {
    /// 表示設定。
    pub settings: TrackSettings,
    /// 連番バンク。未配置の絵も保持する。
    pub cells: BTreeMap<CellNumber, Cell>,
    /// 旧連番保管の属性。
    pub legacy_archive_hints: BTreeMap<CellNumber, ArchiveHint>,
}

/// 位置に設定された絵、空キー、または未使用。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Reference {
    /// 番号付きの絵。
    Number(CellNumber),
    /// 空キーの区間。
    Blank,
    /// 未使用。
    #[default]
    Empty,
}

/// Python Layer の位置固有の情報。番号付きの絵は列のバンクから引く。
#[derive(Clone, Debug, Default)]
pub struct Position {
    /// スロットの参照。配置待ちでは表示用参照と異なることがある。
    pub reference: Reference,
    /// 空キー区間の開始。
    pub blank_start: bool,
    /// 補間キー設定。
    pub tween: Option<Value>,
    /// 中割りの所属。
    pub tween_member: Option<String>,
    /// 配置待ちの所属。Python の Track.pending_slots に対応する。
    pub pending: bool,
    /// 旧位置の番号、局所画像、および投影の例外。
    pub compatibility: PositionCompatibility,
}

impl Position {
    /// 表示用または画像解決用の参照を返す。Python の reference_for に対応する。
    pub fn reference_for(&self, resolve: bool) -> Reference {
        if let Some(import) = &self.compatibility.dense_import {
            if import.guard.matches(self) {
                return if resolve {
                    import.resolved
                } else {
                    import.sheet
                };
            }
        }
        if self.pending && !resolve {
            Reference::Empty
        } else {
            self.reference
        }
    }

    /// バンクの絵を参照するか。Python の Layer._cell がバンクの Cell を返す条件
    /// （番号があり、内容キーか配置待ち）。
    pub fn references_bank(&self) -> bool {
        self.number().is_some() && (matches!(self.reference, Reference::Number(_)) || self.pending)
    }

    /// Python の sequence_number に対応する番号を返す。旧未使用枠の保持番号も含む。
    pub fn number(&self) -> Option<CellNumber> {
        match self.reference {
            Reference::Number(number) => Some(number),
            _ => self.compatibility.retained_number,
        }
    }

    /// Python の reference_for(layer)（resolve=False）が返す種別と番号。
    /// 種別は絵なら Some(false)、空キーなら Some(true)、未使用なら None。
    fn block_kind(&self) -> (Option<bool>, Option<CellNumber>) {
        let reference = match &self.compatibility.dense_import {
            Some(import) if import.guard.matches(self) => import.sheet,
            _ if self.pending => Reference::Empty,
            _ => match self.reference {
                // Python の has_content は空キーなら立たず、番号なしの絵なら立つ。
                Reference::Empty if self.compatibility.unnumbered_content => {
                    return (Some(false), self.number());
                }
                reference => reference,
            },
        };
        match reference {
            Reference::Number(number) => (Some(false), Some(number)),
            Reference::Blank => (Some(true), None),
            Reference::Empty => (None, None),
        }
    }

    fn timeline_cell(&self, resolve: bool) -> TimelineCell {
        let reference = self.reference_for(resolve);
        TimelineCell {
            has_content: matches!(reference, Reference::Number(_))
                || (self.compatibility.unnumbered_content && (resolve || !self.pending)),
            is_blank_key: reference == Reference::Blank,
            blank_start: self.blank_start,
            sequence_number: match reference {
                Reference::Number(number) => Some(number.get()),
                _ => None,
            },
            sequence_only: false,
        }
    }
}

/// コマの尺と、列 ID ごとの位置を持つシートの一行。
#[derive(Clone, Debug)]
pub struct Frame {
    /// 再生時のコマの持続時間。
    pub duration: i64,
    /// 列 ID で引く位置。表示順は Document が持つ。
    pub positions: BTreeMap<TrackId, Position>,
}

/// フレーム ID ごとのシート行。
pub type Sheet = BTreeMap<FrameId, Frame>;

/// Python Document に対応する文書。順序と安定 ID を別々に保持する。
#[derive(Clone, Debug)]
pub struct Document {
    pub(super) width: usize,
    pub(super) height: usize,
    pub(super) frame_order: Vec<FrameId>,
    pub(super) track_order: Vec<TrackId>,
    pub(super) sheet: Sheet,
    pub(super) tracks: BTreeMap<TrackId, Track>,
    pub(super) current_frame: FrameId,
    pub(super) active_layer: TrackId,
    pub(super) next_frame_id: u64,
    pub(super) next_track_id: u64,
    /// 命令が作る空画像の雛形（Python の blank_model_image() と同じ画素・世代・札）。
    /// 無いか大きさが違えば、その都度新しい空画像を作る。
    pub(super) blank: Option<RgbaImage>,
}

impl Document {
    /// キャンバスの幅と高さを返す。
    pub fn size(&self) -> (usize, usize) {
        (self.width, self.height)
    }

    /// コマ ID をシートの表示順で返す。
    pub fn frame_order(&self) -> &[FrameId] {
        &self.frame_order
    }

    /// 列 ID をシートの表示順で返す。
    pub fn track_order(&self) -> &[TrackId] {
        &self.track_order
    }

    /// 現在コマと選択列を ID で返す。
    pub fn selection(&self) -> (FrameId, TrackId) {
        (self.current_frame, self.active_layer)
    }

    /// ID に対応する列を返す。
    pub fn track(&self, id: TrackId) -> Option<&Track> {
        self.tracks.get(&id)
    }

    /// ID に対応するシート行を返す。
    pub fn frame(&self, id: FrameId) -> Option<&Frame> {
        self.sheet.get(&id)
    }

    /// Python の番号順の処理に対応し、未配置を含む絵の番号を昇順で返す。
    pub fn bank_numbers(&self, id: TrackId) -> Option<impl Iterator<Item = CellNumber> + '_> {
        Some(self.track(id)?.cells.keys().copied())
    }

    /// normalize_numbers の開始順と同様に、配置済みの番号をシート初出順で返す。
    pub fn placed_numbers(&self, id: TrackId) -> Vec<CellNumber> {
        let mut seen = BTreeSet::new();
        self.frame_order
            .iter()
            .filter_map(
                |frame| match self.sheet[frame].positions.get(&id)?.reference_for(false) {
                    Reference::Number(number) if seen.insert(number) => Some(number),
                    _ => None,
                },
            )
            .collect()
    }

    /// Python の build_xsheet と同じ表示用の項目をコマ優先順で返す。
    pub fn sheet_entries(&self) -> Result<(usize, Vec<SheetEntry>), Error> {
        timeline::sheet(&self.timeline_rows(false))
    }

    /// Python の timeline_block_at（span_at）と同じ、表示位置 column・表示列 track を含む
    /// 保持の範囲を (空キーか, 頭の表示位置, 長さ) で返す。未使用・範囲外なら None。
    ///
    /// 位置の種別は Python の reference_for(resolve=False) と同じ（取り込み時の参照の照合、
    /// 配置待ちは未使用、番号なしの絵は保持番号を番号として持つ）。
    /// 同じ種別・番号の位置が続く間を一つの範囲とし、空キーは空キー開始の位置で切る。
    pub fn timeline_block_at(&self, column: usize, track: usize) -> Option<(bool, usize, usize)> {
        let track = *self.track_order.get(track)?;
        let state = |index: usize| {
            let position = &self.sheet[&self.frame_order[index]].positions[&track];
            let (kind, number) = position.block_kind();
            (kind, number, position.blank_start)
        };
        if column >= self.frame_order.len() {
            return None;
        }
        let (kind, number, _) = state(column);
        let blank = kind?;
        let (mut start, mut end) = (column, column + 1);
        while start > 0 {
            let (previous, previous_number, _) = state(start - 1);
            if (previous, previous_number) != (kind, number) || (blank && state(start).2) {
                break;
            }
            start -= 1;
        }
        while end < self.frame_order.len() {
            let (next, next_number, next_start) = state(end);
            if (next, next_number) != (kind, number) || (blank && next_start) {
                break;
            }
            end += 1;
        }
        Some((blank, start, end - start))
    }

    /// Python の resolve_timeline と同じ参照元の表示インデックスをコマ優先順で返す。
    pub fn resolve_sources(&self) -> Result<(usize, Vec<i64>), Error> {
        let (width, mut sources) = timeline::resolve(&self.timeline_rows(true))?;
        for (row, frame) in self.frame_order.iter().enumerate() {
            for (column, track) in self.track_order.iter().enumerate() {
                let position = &self.sheet[frame].positions[track];
                if let Some(import) = &position.compatibility.resolved_source {
                    if position.compatibility.dense_import.is_some()
                        && import.guard.matches(position)
                        && import.column == row
                        && import.frame_count == self.frame_order.len()
                    {
                        sources[row * width + column] =
                            import.source_column.map_or(-1, |n| n as i64);
                    }
                }
            }
        }
        Ok((width, sources))
    }

    fn timeline_rows(&self, resolve: bool) -> Vec<Vec<TimelineCell>> {
        self.frame_order
            .iter()
            .map(|id| {
                self.track_order
                    .iter()
                    .map(|track| self.sheet[id].positions[track].timeline_cell(resolve))
                    .collect()
            })
            .collect()
    }
}

pub(super) fn layer_name(mut index: usize) -> String {
    let mut chars = Vec::new();
    loop {
        chars.push(char::from(b'A' + (index % 26) as u8));
        index /= 26;
        if index == 0 {
            return chars.into_iter().rev().collect();
        }
        index -= 1;
    }
}
