//! タイムラインの画面（Tauri）が使う判断。
//!
//! Qt の画面（`paintmaskanimator/timeline.py`・`main_window_timeline_ops.py`・
//! `canvas_key_frame.py`・`canvas_playback.py`・`tween_groups.py` など）で Python だけが
//! 持っていた判断を、文書から読む形で移したもの。画面はここで決めた値を描き、
//! 操作は既存の文書の命令（commands.rs・position_commands.rs など）へ渡す。
//!
//! 連番表示の列（#108）は、Python の二か所の食い違い（文書は番号ごとに最初の列、
//! タイムラインは最後の列）をやめ、`sequence_entry_columns` の一つの規則にそろえる。

use std::collections::{BTreeMap, BTreeSet};

use super::changes::{Change, ChangeSet};
use super::frame_commands::archive_hint;
use super::model::{layer_name, Cell, CellNumber, Document, Position, Reference, TrackId};
use super::position_commands::SheetMove;
use super::timeline::{SHEET_BLANK, SHEET_EMPTY};
use crate::Error;

fn invalid() -> Error {
    Error::Invalid("タイムラインの位置が不正です".into())
}

/// Python の Layer.has_content（位置の素のメタデータ）。
fn has_content(position: &Position) -> bool {
    matches!(position.reference, Reference::Number(_)) || position.compatibility.unnumbered_content
}

/// 確定したトゥイーンの帯（Python の tween_groups の一項目）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TweenGroup {
    /// キーのコマ。
    pub start: usize,
    /// 帯の長さ（2 以上）。
    pub length: usize,
    /// 逆生成か。
    pub reverse: bool,
}

/// セルとセルの間の空き（Python の timeline_gaps の一項目）。データ上は未使用だが、
/// 書き出しでは空セルと同じ × になるので、画面でも空セルとして見せる。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TimelineGap {
    /// 空きの先頭のコマ。
    pub start: usize,
    /// 空きの長さ。
    pub length: usize,
    /// 先頭に × を描くか（直前が空キーなら、その続きとして描かない）。
    pub cross: bool,
}

/// 連番表示の一つの番号。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SequenceEntry {
    /// 絵番号。表示の列は番号 - 1。
    pub number: CellNumber,
    /// 番号を代表するシートのコマ（`sequence_entry_columns` と同じ規則）。シートに無い絵は None。
    pub column: Option<usize>,
    /// 絵を持つか。false は連番の空き番号（点線の箱）。
    pub content: bool,
}

/// キーの保持の長さを変える範囲（Python の prepare_exposure_resize の結果）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ExposureResize {
    /// 今の保持の先頭。
    pub key_column: usize,
    /// 今の保持の最後。
    pub old_end: usize,
    /// 変えたあとの先頭。
    pub new_start: usize,
    /// 変えたあとの最後。
    pub new_end: usize,
}

/// 原画のあいだに中割りを入れられない理由（Python の insert_inbetweens の文言の元）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InbetweenError {
    /// 原画の印のあるセルではない。
    NotGenga,
    /// このあとに次の原画が無い。
    NoNextGenga,
    /// 原画と次の原画のあいだに、ほかのセルがある。
    Occupied,
    /// 枚数が足りないか、コマ数が足りない。`span` は原画のあいだのコマ数。
    TooMany {
        /// 原画のあいだのコマ数。
        span: usize,
    },
}

impl Document {
    fn track_at(&self, layer: usize) -> Option<TrackId> {
        self.track_order.get(layer).copied()
    }

    fn position_at(&self, column: usize, track: TrackId) -> &Position {
        &self.sheet[&self.frame_order[column]].positions[&track]
    }

    /// 位置が指す絵（番号付きならバンク、番号なしの絵なら位置の局所の絵）。
    pub fn position_cell(&self, column: usize, layer: usize) -> Option<&Cell> {
        let track = self.track_at(layer)?;
        if column >= self.frame_order.len() {
            return None;
        }
        let position = self.position_at(column, track);
        match position.reference {
            Reference::Number(number) => self.tracks[&track].cells.get(&number),
            _ if position.compatibility.unnumbered_content => position.compatibility.local.as_ref(),
            _ => None,
        }
    }

    /// Python の timeline_step_columns：区切りの先頭（キー・空キーの頭）のコマを並べる。
    /// 配置待ちの位置は含めない。
    pub fn timeline_step_columns(&self, layer: usize) -> Vec<usize> {
        let Some(track) = self.track_at(layer) else {
            return Vec::new();
        };
        let mut columns = Vec::new();
        let mut previous = None;
        for column in 0..self.frame_order.len() {
            let position = self.position_at(column, track);
            let reference = position.reference_for(false);
            let starts = previous != Some(reference)
                || (reference == Reference::Blank && position.blank_start);
            if reference != Reference::Empty && starts && !position.pending {
                columns.push(column);
            }
            previous = Some(reference);
        }
        columns
    }

    /// Python の previous_key_frame：current より前の最後の区切り。
    pub fn previous_key_column(&self, layer: usize, current: usize) -> Option<usize> {
        self.timeline_step_columns(layer)
            .into_iter()
            .rev()
            .find(|&column| column < current)
    }

    /// Python の next_key_frame：current より後ろの最初の区切り。
    pub fn next_key_column(&self, layer: usize, current: usize) -> Option<usize> {
        self.timeline_step_columns(layer)
            .into_iter()
            .find(|&column| column > current)
    }

    /// Python の tween_groups.group_length：key が崩れていないトゥイーンの先頭なら長さ。
    fn tween_group_length(&self, track: TrackId, key: usize) -> Option<usize> {
        let position = self.position_at(key, track);
        let spec = position.tween.as_ref()?;
        if !has_content(position) {
            return None;
        }
        let length = spec.get("length").and_then(|v| v.as_i64()).unwrap_or(0);
        let id = spec.get("id").and_then(|v| v.as_str());
        let length = usize::try_from(length).ok().filter(|&n| n >= 2)?;
        if key + length > self.frame_order.len() {
            return None;
        }
        (1..length)
            .all(|offset| {
                let member = self.position_at(key + offset, track);
                member.tween_member.as_deref() == id && id.is_some() && has_content(member)
            })
            .then_some(length)
    }

    /// Python の tween_groups：確定したトゥイーンの帯をコマ順に返す。
    pub fn tween_groups(&self, layer: usize) -> Vec<TweenGroup> {
        let Some(track) = self.track_at(layer) else {
            return Vec::new();
        };
        let mut groups = Vec::new();
        let mut column = 0;
        while column < self.frame_order.len() {
            if let Some(length) = self.tween_group_length(track, column) {
                let reverse = self
                    .position_at(column, track)
                    .tween
                    .as_ref()
                    .and_then(|spec| spec.get("reverse"))
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false);
                groups.push(TweenGroup {
                    start: column,
                    length,
                    reverse,
                });
                column += length;
            } else {
                column += 1;
            }
        }
        groups
    }

    /// Python の timeline_gaps：最初から最後のセルまでの、セルとセルの間の空き。
    pub fn timeline_gaps(&self, layer: usize) -> Vec<TimelineGap> {
        let Some(track) = self.track_at(layer) else {
            return Vec::new();
        };
        let kinds: Vec<u8> = self
            .track_sheet_entries(track)
            .into_iter()
            .map(|entry| entry.kind)
            .collect();
        let Some(last) = kinds.iter().rposition(|&kind| kind != SHEET_EMPTY) else {
            return Vec::new();
        };
        let mut gaps = Vec::new();
        let mut column = 0;
        while column < last {
            if kinds[column] != SHEET_EMPTY {
                column += 1;
                continue;
            }
            let start = column;
            while column < last && kinds[column] == SHEET_EMPTY {
                column += 1;
            }
            gaps.push(TimelineGap {
                start,
                length: column - start,
                cross: !(start > 0 && kinds[start - 1] == SHEET_BLANK),
            });
        }
        gaps
    }

    /// Python の sequence_entry_columns：絵番号ごとの代表のコマを番号順に返す。
    ///
    /// 番号を持ち、絵があるか配置待ちの位置のうち、番号ごとに最初のコマ。配置待ちの位置が
    /// あればそちら（後ろの配置待ちほど優先）。
    pub fn sequence_entry_columns(&self, layer: usize) -> Vec<(CellNumber, usize)> {
        let Some(track) = self.track_at(layer) else {
            return Vec::new();
        };
        let mut entries = BTreeMap::new();
        for column in 0..self.frame_order.len() {
            let position = self.position_at(column, track);
            let Some(number) = position.number() else {
                continue;
            };
            if position.pending || (has_content(position) && !entries.contains_key(&number)) {
                entries.insert(number, column);
            }
        }
        entries.into_iter().collect()
    }

    /// 連番表示の一行：シートに置いた番号とバンクだけの番号を、番号順に返す。
    pub fn sequence_entries(&self, layer: usize) -> Vec<SequenceEntry> {
        let Some(track) = self.track_at(layer) else {
            return Vec::new();
        };
        let placed: BTreeMap<_, _> = self.sequence_entry_columns(layer).into_iter().collect();
        let bank = &self.tracks[&track];
        let numbers: BTreeSet<CellNumber> = placed
            .keys()
            .copied()
            .chain(bank.cells.keys().copied())
            .collect();
        numbers
            .into_iter()
            .map(|number| match placed.get(&number) {
                Some(&column) => SequenceEntry {
                    number,
                    column: Some(column),
                    content: has_content(self.position_at(column, track)),
                },
                None => SequenceEntry {
                    number,
                    column: None,
                    // Python の DrawingTarget.has_content：連番保管の札があればその値、無ければ絵。
                    content: bank
                        .legacy_archive_hints
                        .get(&number)
                        .is_none_or(|hint| hint.has_content),
                },
            })
            .collect()
    }

    /// 連番表示の列の数（Python の refresh の real_frame_count）。どの列でも最大の番号。
    pub fn sequence_frame_count(&self) -> usize {
        let mut count = 1;
        for layer in 0..self.track_order.len() {
            for entry in self.sequence_entries(layer) {
                count = count.max(entry.number.get() as usize);
            }
        }
        count
    }

    /// 連番表示で、コマ `column` の列 `layer` を表す表示の列（番号 - 1）。#108：
    /// 列の一致を探さず、位置の番号から直接求める（保持中のコマでも、同じ番号が
    /// 複数のコマにあっても、その番号の列になる）。番号の無い位置は None。
    pub fn sequence_display_column(&self, layer: usize, column: usize) -> Option<usize> {
        let track = self.track_at(layer)?;
        if column >= self.frame_order.len() {
            return None;
        }
        let number = self.position_at(column, track).number()?;
        usize::try_from(number.get() - 1).ok()
    }

    /// 連番表示で番号 `number` を選んだときのコマ（Python の select_exposure の連番の分岐）。
    pub fn sequence_number_column(&self, layer: usize, number: i64) -> Option<usize> {
        self.sequence_entry_columns(layer)
            .into_iter()
            .find(|(n, _)| n.get() == number)
            .map(|(_, column)| column)
    }

    /// Python の _navigate_sequence_number：絵番号順に step だけ進めたコマ。
    pub fn sequence_step(
        &self,
        layer: usize,
        current: usize,
        step: i64,
        wrap: bool,
    ) -> Option<usize> {
        let entries = self.sequence_entry_columns(layer);
        if entries.is_empty() || step == 0 {
            return None;
        }
        let current_number = self
            .track_at(layer)
            .filter(|_| current < self.frame_order.len())
            .and_then(|track| self.position_at(current, track).number());
        let len = entries.len() as i64;
        let index = entries
            .iter()
            .position(|(number, _)| Some(*number) == current_number)
            .map_or(if step > 0 { -1 } else { len }, |i| i as i64);
        let target = index.saturating_add(step.signum());
        let target = if wrap {
            target.rem_euclid(len)
        } else {
            target.clamp(0, len - 1)
        };
        Some(entries[target as usize].1)
    }

    /// Python の set_mode：表示を切り替えたとき、選んでいた番号の絵のある最初のコマ。
    /// 連番からシートへ移るときは、配置待ちの位置を選ばない。
    pub fn mode_switch_column(
        &self,
        layer: usize,
        current: usize,
        to_sequence: bool,
    ) -> Option<usize> {
        let track = self.track_at(layer)?;
        if current >= self.frame_order.len() {
            return None;
        }
        let selected = self.position_at(current, track).number()?;
        (0..self.frame_order.len()).find(|&column| {
            let position = self.position_at(column, track);
            position.number() == Some(selected)
                && (has_content(position) || position.reference == Reference::Blank)
                && (to_sequence || !position.pending)
        })
    }

    /// 配置待ちの位置があるか（連番からシートへ移るときに配置する）。
    pub fn has_pending_positions(&self) -> bool {
        self.sheet
            .values()
            .any(|row| row.positions.values().any(|position| position.pending))
    }

    /// Python の prepare_exposure_resize：キーの保持を左端・右端で `boundary` まで動かした範囲。
    /// 左端は前の区切りの保持の終わりより前へは伸ばさない。変わらなければ None。
    pub fn prepare_exposure_resize(
        &self,
        layer: usize,
        key_column: usize,
        boundary: usize,
        left: bool,
    ) -> Option<ExposureResize> {
        let (_, key_column, exposure) = self.timeline_block_at(key_column, layer)?;
        let old_end = key_column + exposure - 1;
        let (mut new_start, mut new_end) = (key_column, old_end);
        if left {
            let minimum = self
                .timeline_step_columns(layer)
                .into_iter()
                .rev()
                .find(|&column| column < key_column)
                .and_then(|previous| self.timeline_block_at(previous, layer))
                .map_or(0, |(_, start, length)| start + length);
            new_start = minimum.max(boundary.min(old_end));
        } else {
            new_end = key_column.max(boundary);
        }
        ((new_start, new_end) != (key_column, old_end)).then_some(ExposureResize {
            key_column,
            old_end,
            new_start,
            new_end,
        })
    }

    /// Python の prepare_sheet_moves：選んだ位置（列, コマ）を含む保持を、列とコマを
    /// ずらした移動にする。同じ保持は一つにまとめ、列・先頭の順に並べる。
    /// 移動先が範囲外（先頭が 0 より前、列が無い）なら None。動かす保持が無ければ空。
    pub fn prepare_sheet_moves(
        &self,
        positions: &[(usize, usize)],
        layer_delta: isize,
        column_delta: isize,
    ) -> Option<Vec<SheetMove>> {
        let count = self.track_order.len();
        let mut blocks = BTreeSet::new();
        for &(layer, column) in positions {
            if layer >= count {
                continue;
            }
            if let Some((_, start, _)) = self.timeline_block_at(column, layer) {
                blocks.insert((layer, start));
            }
        }
        blocks
            .into_iter()
            .map(|(layer, start)| {
                let target_layer = layer.checked_add_signed(layer_delta)?;
                let target_start = start.checked_add_signed(column_delta)?;
                (target_layer < count).then_some(SheetMove {
                    source: (start, layer),
                    destination: (target_start, target_layer),
                })
            })
            .collect()
    }

    /// Python の normalization_mapping のうち、番号が変わる組（旧番号, 新番号）。
    /// シートに登場する順に 1, 2, 3… を振り、シートで使っていない番号はその後ろへ番号順に並べる。
    pub fn normalization_changes(&self, layer: usize) -> Vec<(CellNumber, CellNumber)> {
        let Some(track) = self.track_at(layer) else {
            return Vec::new();
        };
        let mut sheet_numbers: Vec<CellNumber> = Vec::new();
        let mut all: BTreeSet<CellNumber> = self.tracks[&track].cells.keys().copied().collect();
        for column in 0..self.frame_order.len() {
            let position = self.position_at(column, track);
            let Some(number) = position.number() else {
                continue;
            };
            all.insert(number);
            if has_content(position) && !position.pending && !sheet_numbers.contains(&number) {
                sheet_numbers.push(number);
            }
        }
        let rest: Vec<_> = all
            .into_iter()
            .filter(|number| !sheet_numbers.contains(number))
            .collect();
        sheet_numbers
            .into_iter()
            .chain(rest)
            .enumerate()
            .filter_map(|(index, old)| {
                let new = CellNumber::new(index as i64 + 1).ok()?;
                (old != new).then_some((old, new))
            })
            .collect()
    }

    /// Python の toggle_genga：`column` を含む絵の保持の絵に、原画の印（#24）を付ける／外す。
    /// 印は絵に付くので、同じ番号のコマすべてに効く。付けたあとの状態と変更を返す。
    pub fn toggle_genga(
        &mut self,
        column: usize,
        layer: usize,
    ) -> Result<(bool, ChangeSet), Error> {
        let track = self.track_at(layer).ok_or_else(invalid)?;
        let (blank, start, _) = self.timeline_block_at(column, layer).ok_or_else(invalid)?;
        if blank {
            return Err(invalid());
        }
        let position = self.position_at(start, track).clone();
        let (cell, number, frame) = match position.reference {
            Reference::Number(number) => (
                self.tracks
                    .get_mut(&track)
                    .and_then(|bank| bank.cells.get_mut(&number)),
                Some(number),
                None,
            ),
            _ if position.compatibility.unnumbered_content => {
                let row = self
                    .sheet
                    .get_mut(&self.frame_order[start])
                    .ok_or_else(invalid)?;
                let cell = std::sync::Arc::make_mut(row)
                    .positions
                    .get_mut(&track)
                    .and_then(|position| position.compatibility.local.as_mut());
                (cell, None, Some(start))
            }
            _ => (None, None, None),
        };
        let cell = cell.ok_or_else(invalid)?;
        cell.is_genga = !cell.is_genga;
        let state = cell.is_genga;
        Ok((
            state,
            vec![Change::DrawingAttributes {
                layer_index: layer,
                number,
                frame,
                fields: vec!["is_genga"],
            }],
        ))
    }

    fn block_is_genga(&self, column: usize, layer: usize) -> bool {
        self.position_cell(column, layer)
            .is_some_and(|cell| cell.is_genga)
    }

    /// Python の _next_genga_start：`after` より後ろで、原画の印のある絵の保持が始まるコマ。
    pub fn next_genga_start(&self, layer: usize, after: usize) -> Option<usize> {
        let mut column = after + 1;
        while column < self.frame_order.len() {
            let Some((blank, start, length)) = self.timeline_block_at(column, layer) else {
                column += 1;
                continue;
            };
            if !blank && start == column && self.block_is_genga(column, layer) {
                return Some(column);
            }
            column = start + length.max(1);
        }
        None
    }

    /// Python の insert_inbetweens の判断：原画から次の原画までを `count + 1` 等分し、
    /// 空キー（○）を置くコマを返す。置くのは呼び元が create_blank_key で行う。
    pub fn inbetween_columns(
        &self,
        column: usize,
        layer: usize,
        count: usize,
    ) -> Result<Vec<usize>, InbetweenError> {
        let (blank, start, exposure) = self
            .timeline_block_at(column, layer)
            .ok_or(InbetweenError::NotGenga)?;
        if blank || !self.block_is_genga(start, layer) {
            return Err(InbetweenError::NotGenga);
        }
        let following = self
            .next_genga_start(layer, start)
            .ok_or(InbetweenError::NoNextGenga)?;
        if start + exposure.max(1) != following {
            return Err(InbetweenError::Occupied);
        }
        let span = following - start;
        if count < 1 || span < count + 1 {
            return Err(InbetweenError::TooMany { span });
        }
        let columns: BTreeSet<_> = (1..=count)
            .map(|k| start + (k * span) / (count + 1))
            .collect();
        Ok(columns.into_iter().collect())
    }

    /// Python の delete_frame（タイムラインの削除ボタン）：現在コマを含む保持の先頭の一コマを
    /// 未使用に戻す。番号付きの絵なら、旧形式の連番保管の札を残す（clear_sheet_span(archive=True)）。
    pub fn clear_sheet_key_archived(
        &mut self,
        layer: usize,
        column: usize,
    ) -> Result<ChangeSet, Error> {
        let track = self.track_at(layer).ok_or_else(invalid)?;
        let (_, start, _) = self.timeline_block_at(column, layer).ok_or_else(invalid)?;
        let position = self.position_at(start, track).clone();
        let mut edited = self.clone();
        if let (true, Some(number)) = (has_content(&position), position.number()) {
            let exposure = position
                .compatibility
                .legacy
                .as_ref()
                .and_then(|legacy| legacy.legacy.as_ref())
                .map_or(1, |slot| slot.state.exposure);
            edited
                .tracks
                .get_mut(&track)
                .ok_or_else(invalid)?
                .legacy_archive_hints
                .insert(number, archive_hint(&position, exposure));
        }
        let changes = edited.clear_sheet_span(layer, start..start + 1)?;
        *self = edited;
        Ok(changes)
    }
}

/// Python の default_layer_name：名前を空にしたときの名前（A, B, …, Z, AA, …）。
pub fn default_layer_name(index: usize) -> String {
    layer_name(index)
}

/// Python の playback_bounds：再生する範囲（先頭以上・終わり未満）。
/// `end` が None なら最後のコマまで。
pub fn playback_bounds(start: usize, end: Option<usize>, frame_count: usize) -> (usize, usize) {
    let count = frame_count.max(1);
    let start = start.min(count - 1);
    let end = end.map_or(count, |end| end.max(start + 1));
    (start, end.min(count))
}

/// Python の set_playback_active：再生を始めるコマ（範囲の外なら範囲の先頭）。
pub fn playback_start_frame(current: usize, bounds: (usize, usize)) -> usize {
    if (bounds.0..bounds.1).contains(&current) {
        current
    } else {
        bounds.0
    }
}

/// Python の playback_advance：範囲の中で `steps` コマ進めたコマ（終わりで先頭へ戻る）。
pub fn playback_advance(current: usize, steps: usize, bounds: (usize, usize)) -> usize {
    let (start, end) = bounds;
    let span = end.saturating_sub(start).max(1) as i64;
    let offset = current as i64 - start as i64 + steps.max(1) as i64;
    start + offset.rem_euclid(span) as usize
}

/// Python の advance：再生を始めてから `elapsed` 秒のとき、すでに進めた `emitted` コマに
/// 加えて進めるコマ数。遅れたら経過時間に追いつく分だけ進める。
pub fn playback_due_steps(elapsed: f64, fps: u32, emitted: u64) -> u64 {
    let expected = (elapsed.max(0.0) * f64::from(fps.max(1))).floor() as u64;
    expected.saturating_sub(emitted)
}

/// Python の resize_range：再生範囲の端を `column` へ動かしたあとの（先頭, 終わり）。
/// 終わりは「このコマまで」の次（未満）。変わらなければ None。
pub fn playback_range_edge(
    start: usize,
    end: Option<usize>,
    frame_count: usize,
    edge_end: bool,
    column: usize,
) -> Option<(usize, Option<usize>)> {
    let end_value = end.map_or(frame_count, |end| end.max(start + 1));
    if edge_end {
        let target = column.max(start + 1);
        (target != end_value).then_some((start, Some(target)))
    } else {
        let target = column.min(end_value.saturating_sub(1));
        (target != start).then_some((target, Some(end_value)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::image::RgbaImage;
    use serde_json::{json, Value};

    /// 列ごとの記号から文書を作る。記号：数字は番号の絵、`o` は空キーの頭、`-` は空キーの
    /// 続き、`.` は未使用、`pN` は配置待ちの番号 N。最初の列が下（レイヤー 0）。
    fn document(layers: &[&str]) -> Document {
        document_with(layers, |_, _, _| {})
    }

    fn document_with(layers: &[&str], edit: impl Fn(usize, usize, &mut Value)) -> Document {
        let rows: Vec<Vec<&str>> = layers
            .iter()
            .map(|l| l.split_whitespace().collect())
            .collect();
        let frames = rows[0].len();
        let mut tracks = Vec::new();
        for (index, row) in rows.iter().enumerate() {
            let numbers: BTreeSet<i64> = row
                .iter()
                .filter_map(|t| t.trim_start_matches('p').parse().ok())
                .collect();
            let cells: Vec<Value> = numbers
                .iter()
                .map(|n| json!([n, {"image": 0, "initial_image": null, "cell_name": null}]))
                .collect();
            tracks.push(json!({
                "settings": {"name": layer_name(index), "visible": true, "opacity": 1.0,
                             "alpha_locked": false, "is_paper": false, "is_draft": false,
                             "color_filter_enabled": false, "color_filter_rgb": null},
                "cells": cells, "hints": [],
            }));
        }
        let frames: Vec<Value> = (0..frames)
            .map(|column| {
                let positions: Vec<Value> = rows
                    .iter()
                    .enumerate()
                    .map(|(layer, row)| {
                        let token = row[column];
                        let pending = token.starts_with('p');
                        let number: Option<i64> = token.trim_start_matches('p').parse().ok();
                        let mut value = json!({
                            "has_content": number.is_some(),
                            "is_blank_key": token == "o" || token == "-",
                            "blank_start": token == "o",
                            "sequence_number": number,
                            "sequence_only": pending,
                            "tween": null, "tween_member": null,
                        });
                        edit(layer, column, &mut value);
                        value
                    })
                    .collect();
                json!({"duration": 1, "positions": positions})
            })
            .collect();
        let graph = json!({
            "width": 2, "height": 2, "current_frame": 0, "active_layer": 0,
            "tracks": tracks, "frames": frames,
        });
        Document::from_graph(&graph, &[RgbaImage::blank(2, 2).unwrap()]).unwrap()
    }

    fn n(value: i64) -> CellNumber {
        CellNumber::new(value).unwrap()
    }

    #[test]
    fn step_columns_and_key_navigation_follow_python() {
        let doc = document(&["1 1 . 2 o - o 3 p4"]);
        assert_eq!(doc.timeline_step_columns(0), vec![0, 3, 4, 6, 7]);
        assert_eq!(doc.previous_key_column(0, 4), Some(3));
        assert_eq!(doc.previous_key_column(0, 0), None);
        assert_eq!(doc.next_key_column(0, 1), Some(3));
        assert_eq!(doc.next_key_column(0, 7), None);
        assert!(doc.timeline_step_columns(5).is_empty());
    }

    #[test]
    fn gaps_cover_unused_frames_between_cells_only() {
        let doc = document(&[". 1 . . o . 2 . ."]);
        assert_eq!(
            doc.timeline_gaps(0),
            vec![
                TimelineGap {
                    start: 0,
                    length: 1,
                    cross: true
                },
                TimelineGap {
                    start: 2,
                    length: 2,
                    cross: true
                },
                // 空キーの直後の空きは、空キーの続きとして × を描かない。
                TimelineGap {
                    start: 5,
                    length: 1,
                    cross: false
                },
            ]
        );
        assert!(document(&[". . ."]).timeline_gaps(0).is_empty());
    }

    #[test]
    fn tween_groups_need_an_intact_member_run() {
        let spec = json!({"id": "t", "length": 3, "reverse": true});
        let doc = document_with(&["1 2 3 4"], |_, column, value| match column {
            0 => value["tween"] = spec.clone(),
            1 | 2 => value["tween_member"] = json!("t"),
            _ => {}
        });
        assert_eq!(
            doc.tween_groups(0),
            vec![TweenGroup {
                start: 0,
                length: 3,
                reverse: true
            }]
        );
        // 中割りの一つが外れたら、帯として扱わない。
        let broken = document_with(&["1 2 3 4"], |_, column, value| match column {
            0 => value["tween"] = spec.clone(),
            1 => value["tween_member"] = json!("t"),
            _ => {}
        });
        assert!(broken.tween_groups(0).is_empty());
    }

    #[test]
    fn sequence_columns_pick_the_first_column_and_prefer_pending() {
        let doc = document(&["1 2 1 2 p3 3"]);
        assert_eq!(
            doc.sequence_entry_columns(0),
            vec![(n(1), 0), (n(2), 1), (n(3), 4)]
        );
    }

    #[test]
    fn sequence_display_column_uses_the_number_of_the_current_frame() {
        // #108：同じ番号が複数のコマにあり、保持中のコマが現在コマでも、その番号の列になる。
        let doc = document(&["1 1 2 4 4 . 4"]);
        assert_eq!(doc.sequence_display_column(0, 3), Some(3));
        assert_eq!(doc.sequence_display_column(0, 4), Some(3));
        assert_eq!(doc.sequence_display_column(0, 6), Some(3));
        assert_eq!(doc.sequence_display_column(0, 1), Some(0));
        assert_eq!(doc.sequence_display_column(0, 5), None);
        // 番号を選ぶと、代表のコマ（最初のコマ）へ移る。
        assert_eq!(doc.sequence_number_column(0, 4), Some(3));
    }

    #[test]
    fn sequence_entries_include_bank_only_numbers() {
        let mut doc = document(&["1 2 . 3"]);
        // 3 をシートから外す（バンクには残る）。
        doc.clear_sheet_span(0, 3..4).unwrap();
        let entries = doc.sequence_entries(0);
        assert_eq!(
            entries
                .iter()
                .map(|e| (e.number.get(), e.column, e.content))
                .collect::<Vec<_>>(),
            vec![(1, Some(0), true), (2, Some(1), true), (3, None, true)]
        );
        assert_eq!(doc.sequence_frame_count(), 3);
    }

    #[test]
    fn sequence_step_moves_in_number_order() {
        let doc = document(&["2 2 1 3"]);
        assert_eq!(doc.sequence_step(0, 0, 1, false), Some(3));
        assert_eq!(doc.sequence_step(0, 0, -1, false), Some(2));
        assert_eq!(doc.sequence_step(0, 3, 1, false), Some(3));
        assert_eq!(doc.sequence_step(0, 3, 1, true), Some(2));
    }

    #[test]
    fn mode_switch_selects_the_first_frame_of_the_number() {
        let doc = document(&["1 2 2 p2"]);
        assert_eq!(doc.mode_switch_column(0, 2, false), Some(1));
        assert_eq!(doc.mode_switch_column(0, 3, true), Some(1));
        assert!(doc.has_pending_positions());
    }

    #[test]
    fn exposure_resize_limits_the_left_edge_to_the_previous_block() {
        let doc = document(&["1 1 . 2 2 2"]);
        // 左端：前の保持（0〜1）の終わりより前へは伸ばせない。
        assert_eq!(
            doc.prepare_exposure_resize(0, 4, 0, true),
            Some(ExposureResize {
                key_column: 3,
                old_end: 5,
                new_start: 2,
                new_end: 5
            })
        );
        // 右端：伸ばす・縮める。
        assert_eq!(
            doc.prepare_exposure_resize(0, 0, 3, false),
            Some(ExposureResize {
                key_column: 0,
                old_end: 1,
                new_start: 0,
                new_end: 3
            })
        );
        assert_eq!(doc.prepare_exposure_resize(0, 0, 1, false), None);
        assert_eq!(doc.prepare_exposure_resize(0, 2, 5, false), None);
    }

    #[test]
    fn sheet_moves_merge_selected_cells_of_one_block() {
        let doc = document(&["1 1 2 .", "3 . . ."]);
        let moves = doc
            .prepare_sheet_moves(&[(0, 0), (0, 1), (1, 0), (0, 3)], 0, 1)
            .unwrap();
        assert_eq!(
            moves
                .iter()
                .map(|m| (m.source, m.destination))
                .collect::<Vec<_>>(),
            vec![((0, 0), (1, 0)), ((0, 1), (1, 1))]
        );
        assert!(doc.prepare_sheet_moves(&[(0, 0)], 0, -1).is_none());
        assert!(doc.prepare_sheet_moves(&[(1, 0)], 1, 0).is_none());
        // 移動を当てると、1番は 1 コマ目から始まる。
        let mut moved = doc.clone();
        moved.move_sheet_cells(&moves, 1, 0).unwrap();
        assert_eq!(moved.timeline_block_at(1, 0), Some((false, 1, 2)));
    }

    #[test]
    fn normalization_lists_numbers_out_of_sheet_order() {
        let doc = document(&["3 1 2"]);
        assert_eq!(
            doc.normalization_changes(0),
            vec![(n(3), n(1)), (n(1), n(2)), (n(2), n(3))]
        );
        assert!(document(&["1 2 2 3"]).normalization_changes(0).is_empty());
    }

    #[test]
    fn genga_marks_follow_the_drawing_and_inbetweens_split_the_span() {
        let mut doc = document(&["1 1 1 1 1 1 2 2"]);
        let (state, changes) = doc.toggle_genga(2, 0).unwrap();
        assert!(state);
        assert_eq!(
            changes,
            vec![Change::DrawingAttributes {
                layer_index: 0,
                number: Some(n(1)),
                frame: None,
                fields: vec!["is_genga"],
            }]
        );
        assert_eq!(
            doc.inbetween_columns(0, 0, 1),
            Err(InbetweenError::NoNextGenga)
        );
        doc.toggle_genga(6, 0).unwrap();
        assert_eq!(doc.next_genga_start(0, 0), Some(6));
        assert_eq!(doc.inbetween_columns(0, 0, 1), Ok(vec![3]));
        assert_eq!(doc.inbetween_columns(0, 0, 2), Ok(vec![2, 4]));
        assert_eq!(
            doc.inbetween_columns(0, 0, 6),
            Err(InbetweenError::TooMany { span: 6 })
        );
        assert_eq!(
            doc.inbetween_columns(6, 0, 1),
            Err(InbetweenError::NoNextGenga)
        );
        // 外すと元に戻る。
        let (state, _) = doc.toggle_genga(0, 0).unwrap();
        assert!(!state);
        assert_eq!(
            doc.inbetween_columns(0, 0, 1),
            Err(InbetweenError::NotGenga)
        );
        assert!(doc.toggle_genga(0, 3).is_err());
    }

    #[test]
    fn inbetweens_refuse_cells_between_the_drawings() {
        let mut doc = document(&["1 1 2 3 3"]);
        doc.toggle_genga(0, 0).unwrap();
        doc.toggle_genga(3, 0).unwrap();
        assert_eq!(
            doc.inbetween_columns(0, 0, 1),
            Err(InbetweenError::Occupied)
        );
    }

    #[test]
    fn archived_clear_keeps_the_drawing_and_records_a_hint() {
        let mut doc = document(&["1 1 2"]);
        doc.clear_sheet_key_archived(0, 1).unwrap();
        let track = doc.track_order[0];
        assert!(doc.tracks[&track].legacy_archive_hints.contains_key(&n(1)));
        assert!(doc.tracks[&track].cells.contains_key(&n(1)));
        assert_eq!(doc.timeline_block_at(0, 0), None);
        assert!(doc.clear_sheet_key_archived(0, 0).is_err());
    }

    #[test]
    fn playback_bounds_and_advance_wrap_within_the_range() {
        assert_eq!(playback_bounds(0, None, 10), (0, 10));
        assert_eq!(playback_bounds(3, Some(6), 10), (3, 6));
        assert_eq!(playback_bounds(3, Some(20), 10), (3, 10));
        assert_eq!(playback_bounds(12, None, 10), (9, 10));
        assert_eq!(playback_start_frame(1, (3, 6)), 3);
        assert_eq!(playback_start_frame(4, (3, 6)), 4);
        assert_eq!(playback_advance(5, 1, (3, 6)), 3);
        assert_eq!(playback_advance(3, 4, (3, 6)), 4);
        assert_eq!(playback_advance(0, 1, (3, 6)), 4);
        assert_eq!(playback_due_steps(0.5, 24, 10), 2);
        assert_eq!(playback_due_steps(0.1, 24, 10), 0);
    }

    #[test]
    fn range_edges_follow_python_resize_range() {
        assert_eq!(
            playback_range_edge(0, None, 10, true, 6),
            Some((0, Some(6)))
        );
        assert_eq!(playback_range_edge(0, None, 10, true, 10), None);
        assert_eq!(
            playback_range_edge(4, None, 10, true, 2),
            Some((4, Some(5)))
        );
        assert_eq!(
            playback_range_edge(0, Some(6), 10, false, 3),
            Some((3, Some(6)))
        );
        assert_eq!(
            playback_range_edge(0, Some(6), 10, false, 9),
            Some((5, Some(6)))
        );
        assert_eq!(playback_range_edge(0, Some(6), 10, false, 0), None);
        assert_eq!(default_layer_name(27), "AB");
    }
}
