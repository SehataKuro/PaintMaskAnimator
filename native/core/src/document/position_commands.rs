use super::changes::{Change, ChangeSet, FrameOperation};
use super::frame_commands::held_reference;
use super::model::{Cell, CellNumber, Document, FrameId, Position, Reference, TrackId};
use crate::{image::RgbaImage, Error};
use std::sync::Arc;

/// 複数区間移動の始点と移動先。各組は（コマ、列）の表示インデックス。
#[derive(Clone, Copy, Debug)]
pub struct SheetMove {
    /// 移動区間内のコマと列。
    pub source: (usize, usize),
    /// 移動後の区間先頭と列。
    pub destination: (usize, usize),
}

fn invalid() -> Error {
    Error::Invalid("位置または絵番号が不正です".into())
}

impl Document {
    /// Python の move_sheet_cells に対応し、全移動元を先に空け、入力順に区間を配置する。
    pub fn move_sheet_cells(
        &mut self,
        moves: &[SheetMove],
        cursor: usize,
        active_layer: usize,
    ) -> Result<ChangeSet, Error> {
        if moves.is_empty() {
            return Ok(Vec::new());
        }
        let selected = *self.track_order.get(active_layer).ok_or_else(invalid)?;
        let mut prepared = Vec::new();
        let mut spans = Vec::new();
        let mut count = self.frame_order.len();
        for movement in moves {
            let (source, source_layer) = movement.source;
            let (destination, layer) = movement.destination;
            let source_span = self.display_spans(source, &[source_layer])?[0]
                .clone()
                .ok_or_else(invalid)?;
            let end = destination
                .checked_add(source_span.len())
                .ok_or(Error::TimelineSizeOverflow)?;
            let edge_spans = [
                self.display_spans(destination, &[layer])?[0].clone(),
                self.display_spans(end - 1, &[layer])?[0].clone(),
            ];
            let source_id = self.track_order[source_layer];
            let destination_id = self.track_order[layer];
            let position =
                self.sheet[&self.frame_order[source_span.start]].positions[&source_id].clone();
            spans.extend([
                Change::SheetSpans {
                    layer_index: source_layer,
                    start: source_span.start,
                    stop: source_span.end,
                },
                Change::SheetSpans {
                    layer_index: layer,
                    start: edge_spans[0]
                        .as_ref()
                        .map_or(destination, |span| span.start),
                    stop: edge_spans[1].as_ref().map_or(end, |span| span.end),
                },
            ]);
            prepared.push((
                source_id,
                source_span,
                destination_id,
                destination..end,
                position,
            ));
            count = count.max(end);
        }
        let mut edited = self.clone();
        for (track, source, ..) in &prepared {
            for column in source.clone() {
                *edited.position_mut(column, *track) = edited.cleared_position(column, *track)?;
            }
        }
        let mut changes = edited.ensure_frame_count(count)?;
        for (_, _, track, target, _) in &prepared {
            for column in target.clone() {
                let reference = edited.position_mut(column, *track).reference;
                if reference != Reference::Empty {
                    *edited.position_mut(column, *track) =
                        edited.cleared_position(column, *track)?;
                }
            }
        }
        let preferred = edited.frame_order.get(cursor).copied();
        for (source, _, track, target, original) in prepared {
            let reference = original.reference_for(false);
            if let Reference::Number(number) = reference {
                if track != source {
                    let incoming = self.tracks[&source]
                        .cells
                        .get(&number)
                        .ok_or_else(invalid)?;
                    let cell =
                        edited.imported_cell(track, number, target.start, incoming, preferred);
                    edited
                        .tracks
                        .get_mut(&track)
                        .ok_or_else(invalid)?
                        .cells
                        .insert(number, cell);
                }
            }
            let head = Position {
                reference,
                blank_start: reference == Reference::Blank,
                compatibility: super::compatibility::PositionCompatibility {
                    retained_number: original.compatibility.retained_number,
                    local: original.compatibility.local,
                    ..Default::default()
                },
                ..Position::default()
            };
            let blank = self.blank_artwork()?;
            let source_settings = &self.tracks[&source].settings;
            let settings = &mut edited.tracks.get_mut(&track).ok_or_else(invalid)?.settings;
            settings.visible = source_settings.visible;
            settings.opacity = source_settings.opacity;
            settings.alpha_locked = source_settings.alpha_locked;
            settings.color_filter_enabled = source_settings.color_filter_enabled;
            settings.color_filter_rgb = source_settings.color_filter_rgb;
            let mut held = held_reference(head.clone(), settings, blank);
            // Python の clone_reference（clone_slot）は配置待ちの所属を位置自身に持たせる。
            held.compatibility.pending_detached = true;
            *edited.position_mut(target.start, track) = head;
            for column in target.start + 1..target.end {
                *edited.position_mut(column, track) = held.clone();
            }
            edited.set_placement_length(target.start, track, 1, false);
        }
        edited.current_frame = edited.frame_order[cursor.min(count - 1)];
        edited.active_layer = selected;
        changes.extend(spans);
        changes.extend([
            Change::Cursor {
                frame: cursor,
                notify: true,
            },
            Change::Selection {
                layer_index: active_layer,
            },
        ]);
        *self = edited;
        Ok(changes)
    }

    fn imported_cell(
        &self,
        track: TrackId,
        number: CellNumber,
        column: usize,
        incoming: &Cell,
        preferred: Option<FrameId>,
    ) -> Cell {
        let bank = &self.tracks[&track];
        let existing = bank.cells.get(&number);
        let mut candidates = self
            .frame_order
            .iter()
            .enumerate()
            .filter_map(|(index, id)| {
                if index == column {
                    Some((*id, incoming))
                } else if self.sheet[id].positions[&track].reference == Reference::Number(number) {
                    existing.map(|cell| (*id, cell))
                } else {
                    None
                }
            })
            .peekable();
        // 全呼び出し元は配置先を作成してから競合を照合するため、候補には必ず incoming が入る。
        let first = candidates.peek().expect("貼付先を含む").1;
        let mut owner = first;
        let mut initial_image = None;
        let mut cell_name = None;
        for (id, cell) in candidates {
            if Some(id) == preferred {
                owner = cell;
            }
            initial_image = initial_image.or_else(|| cell.initial_image.clone());
            cell_name = cell_name.or_else(|| cell.cell_name.clone());
        }
        if let Some(archived) = existing.filter(|_| bank.legacy_archive_hints.contains_key(&number))
        {
            initial_image = initial_image.or_else(|| archived.initial_image.clone());
            cell_name = cell_name.or_else(|| archived.cell_name.clone());
        }
        Cell {
            image: owner.image.clone(),
            initial_image,
            cell_name,
        }
    }

    /// 別位置の番号付き絵を貼り付け、画素と属性の競合を Python と同じ順で解決する。
    pub fn paste_numbered_cell(
        &mut self,
        source: (usize, usize),
        destination: (usize, usize),
        length: usize,
    ) -> Result<ChangeSet, Error> {
        let (source_column, source_layer) = source;
        let source_track = *self.track_order.get(source_layer).ok_or_else(invalid)?;
        let source_id = self.frame_order.get(source_column).ok_or_else(invalid)?;
        let source = &self.sheet[source_id].positions[&source_track];
        let Reference::Number(number) = source.reference else {
            return Err(invalid());
        };
        let incoming = self.tracks[&source_track]
            .cells
            .get(&number)
            .ok_or_else(invalid)?
            .clone();
        self.paste_sheet_cell(destination, number, &incoming, length)
    }

    /// Python の paste_sheet_cell と同様に、番号 `number` の絵 `incoming`（クリップボードの
    /// 写しなど、文書の外の絵でもよい）を `destination` に貼り、後ろへ `length` コマまで保持を
    /// 入れる（後ろのキーの頭に当たったらそこで止める。足りなければコマを延ばす）。
    ///
    /// 画素と属性の競合は Python と同じ順で解決する（現在のコマが同じ番号の内容を持てば
    /// その絵の画素、無ければ最初の位置の画素。初期画像・セル名は並びで最初の値）。
    /// 貼り先の頭は新しい位置として作る（blank_start・旧入力の印・配置待ちの印は引き継が
    /// ない）ので、そうした印を持つ絵や貼り先は呼び元が今の手順へ回す前提。
    pub fn paste_sheet_cell(
        &mut self,
        destination: (usize, usize),
        number: CellNumber,
        incoming: &Cell,
        length: usize,
    ) -> Result<ChangeSet, Error> {
        let (column, layer_index) = destination;
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        if length == 0 || column >= self.frame_order.len() {
            return Err(invalid());
        }
        let cell = self.imported_cell(track, number, column, incoming, Some(self.current_frame));
        let end = column
            .checked_add(length)
            .ok_or(Error::TimelineSizeOverflow)?;
        let affected = self.display_spans(column, &[layer_index])?[0]
            .clone()
            .unwrap_or(column..column + 1);
        let mut edited = self.clone();
        let mut changes = edited.ensure_frame_count(end)?;
        edited
            .tracks
            .get_mut(&track)
            .ok_or_else(invalid)?
            .cells
            .insert(number, cell);
        let position = Position {
            reference: Reference::Number(number),
            ..Position::default()
        };
        let held = held_reference(
            position.clone(),
            &edited.tracks[&track].settings,
            self.blank_artwork()?,
        );
        *edited.position_mut(column, track) = position;
        edited.set_placement_length(column, track, 1, false);
        let mut stop = column + 1;
        while stop < end {
            if edited.display_spans(stop, &[layer_index])?[0]
                .as_ref()
                .is_some_and(|span| span.start == stop)
            {
                break;
            }
            *edited.position_mut(stop, track) = held.clone();
            stop += 1;
        }
        changes.push(Change::SheetSpans {
            layer_index,
            start: affected.start,
            stop: stop.max(affected.end),
        });
        *self = edited;
        Ok(changes)
    }

    /// Python の move_sheet_cell と同じく、選んだ区間を別位置へ移し元の絵をバンクに残す。
    pub fn move_sheet_cell(
        &mut self,
        source: (usize, usize),
        destination: (usize, usize),
    ) -> Result<ChangeSet, Error> {
        let (source_column, source_layer) = source;
        let (column, layer_index) = destination;
        let span = self.display_spans(source_column, &[source_layer])?[0]
            .clone()
            .ok_or_else(invalid)?;
        let exclusive_end = column
            .checked_add(span.len())
            .ok_or(Error::TimelineSizeOverflow)?;
        let source_track = self.track_order[source_layer];
        let destination_span = self.display_spans(column, &[layer_index])?[0]
            .clone()
            .unwrap_or(column..column + 1);
        let track = self.track_order[layer_index];
        let previous = if span.start > 0 {
            self.display_spans(span.start - 1, &[source_layer])?[0].clone()
        } else {
            None
        };
        let tail = self.display_spans(span.end, &[source_layer])?[0].clone();
        let moving: Vec<_> = self.frame_order[span.clone()]
            .iter()
            .map(|id| self.sheet[id].positions[&source_track].clone())
            .collect();
        let reference = moving[0].reference_for(false);
        let content = matches!(reference, Reference::Number(_));
        let tail_blank = tail.clone().filter(|tail| {
            content
                && track == source_track
                && self.sheet[&self.frame_order[tail.start]].positions[&track].reference_for(false)
                    == Reference::Blank
        });
        let mut edited = self.clone();
        let before = edited.frame_order.len();
        for index in span.clone() {
            *edited.position_mut(index, source_track) =
                edited.cleared_position(index, source_track)?;
        }
        if let Some(previous) = previous
            .as_ref()
            .filter(|previous| previous.end == span.start)
        {
            let mut fill_end = span.end;
            if source_track == track && column > span.start {
                fill_end = fill_end.max(column);
                if let Some(tail) = &tail {
                    fill_end = fill_end.min(tail.start);
                }
            }
            edited.ensure_frame_count(fill_end)?;
            let prior = edited.position_mut(previous.start, source_track).clone();
            let held = held_reference(
                prior,
                &edited.tracks[&source_track].settings,
                self.blank_artwork()?,
            );
            for index in span.start..fill_end {
                *edited.position_mut(index, source_track) = held.clone();
            }
        }

        edited.ensure_frame_count(exclusive_end)?;
        if let Some(occupied) = edited.display_spans(column, &[layer_index])?[0].clone() {
            if occupied.start == column && occupied.start != span.start {
                edited.shift_positions_right(column, layer_index, 1)?;
            } else if occupied.start < column {
                for index in column..occupied.end {
                    *edited.position_mut(index, track) = edited.cleared_position(index, track)?;
                }
            }
        }
        if content {
            edited.follow_displaced_blanks(column, exclusive_end, layer_index, tail_blank)?;
        }
        for (offset, mut position) in moving.into_iter().enumerate() {
            let index = column + offset;
            if track != source_track {
                position = Position {
                    reference,
                    ..Position::default()
                };
                if let Reference::Number(number) = reference {
                    let incoming = self.tracks[&source_track]
                        .cells
                        .get(&number)
                        .ok_or_else(invalid)?;
                    let cell = edited.imported_cell(
                        track,
                        number,
                        index,
                        incoming,
                        Some(edited.frame_order[column]),
                    );
                    edited
                        .tracks
                        .get_mut(&track)
                        .ok_or_else(invalid)?
                        .cells
                        .insert(number, cell);
                }
            }
            position.blank_start = reference == Reference::Blank && offset == 0;
            if offset == 0 {
                position.tween = None;
                position.tween_member = None;
            }
            *edited.position_mut(index, track) = position;
            if track != source_track {
                edited.set_placement_length(index, track, 1, false);
            }
        }
        let after = edited.frame_order.len();
        let mut changes = Vec::new();
        if before < after {
            changes.push(Change::FrameStructure {
                operation: FrameOperation::Extend,
                before,
                after,
                start: before,
            });
        }
        let source_start = previous.map_or(span.start, |previous| previous.start);
        if track == source_track {
            changes.push(Change::SheetSpans {
                layer_index,
                start: source_start.min(destination_span.start),
                stop: after,
            });
        } else {
            changes.extend([
                Change::SheetSpans {
                    layer_index,
                    start: destination_span.start,
                    stop: after.max(destination_span.end),
                },
                Change::SheetSpans {
                    layer_index: source_layer,
                    start: source_start,
                    stop: span.end,
                },
            ]);
        }
        edited.current_frame = edited.frame_order[column];
        edited.active_layer = track;
        changes.extend([
            Change::Cursor {
                frame: column,
                notify: true,
            },
            Change::Selection { layer_index },
        ]);
        *self = edited;
        Ok(changes)
    }

    /// Python の copy_sheet_cell と同様に、`source_column` を含む絵の保持を同じ列の
    /// `destination_column` へ写す（移動先の頭を絵の参照にし、後ろへ保持の参照を入れる）。
    /// 絵の保持でなければ何もしない。番号の無い絵の保持はまだ扱わず、エラーを返す。
    /// 移動先の頭は新しい位置として作る（移動先の旧入力の印や配置待ちの印は引き継がない）ので、
    /// そうした印を持つ移動先や、dense_import を持つ元の頭は呼び元が今の手順へ回す前提。
    pub fn copy_sheet_cell(
        &mut self,
        source_column: usize,
        destination_column: usize,
        layer_index: usize,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        let Some(span) = self.display_spans(source_column, &[layer_index])?[0].clone() else {
            return Ok(Vec::new());
        };
        let original = self.sheet[&self.frame_order[span.start]].positions[&track].clone();
        let before = self.frame_order.len();
        let length = span.len();
        let end = destination_column
            .checked_add(length)
            .ok_or(Error::TimelineSizeOverflow)?;
        let bounds = |column: usize| -> Result<Option<std::ops::Range<usize>>, Error> {
            if column < before {
                Ok(self.display_spans(column, &[layer_index])?[0].clone())
            } else {
                Ok(None)
            }
        };
        let start = bounds(destination_column)?.map_or(destination_column, |span| span.start);
        let stop = bounds(end - 1)?.map_or(end, |span| span.end);
        // 番号の無い絵（Python は写した局所の絵を新しい番号でバンクに入れる）は扱わない。
        let number = match original.reference_for(false) {
            Reference::Number(number) => number,
            _ if original.compatibility.unnumbered_content => return Err(invalid()),
            _ => return Ok(Vec::new()),
        };
        let mut edited = self.clone();
        edited.ensure_frame_count(end)?;
        let head = Position {
            reference: Reference::Number(number),
            blank_start: original.blank_start,
            ..Position::default()
        };
        // Python は移動先の頭の clone_reference() を直接入れる（配置待ちの所属は写しが持つ）。
        let mut held = held_reference(
            head.clone(),
            &edited.tracks[&track].settings,
            self.blank_artwork()?,
        );
        held.compatibility.pending_detached = true;
        *edited.position_mut(destination_column, track) = head;
        for index in destination_column + 1..end {
            *edited.position_mut(index, track) = held.clone();
        }
        edited.set_placement_length(destination_column, track, 1, false);
        let after = edited.frame_order.len();
        let mut changes = Vec::new();
        if after > before {
            changes.push(Change::FrameStructure {
                operation: FrameOperation::Extend,
                before,
                after,
                start: before,
            });
        }
        changes.extend([
            Change::SheetSpans {
                layer_index,
                start,
                stop,
            },
            Change::Cursor {
                frame: destination_column,
                notify: true,
            },
            Change::Selection { layer_index },
        ]);
        *self = edited;
        Ok(changes)
    }

    /// Python の merge_sheet_clip と同様に、`column` を含む保持を前の保持へつなぐ。
    ///
    /// 保持の位置を前の保持の頭の写し（中割りの印・blank_start を外し、旧保存の尺は結合した
    /// 範囲を越えない）で入れ直す。保持の外の位置や前に保持が無いときは何もしない。
    pub fn merge_sheet_clip(
        &mut self,
        column: usize,
        layer_index: usize,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        let Some(span) = self.display_spans(column, &[layer_index])?[0].clone() else {
            return Ok(Vec::new());
        };
        let Some(start) = span.start.checked_sub(1) else {
            return Ok(Vec::new());
        };
        let Some(previous) = self.display_spans(start, &[layer_index])?[0].clone() else {
            return Ok(Vec::new());
        };
        let mut source = self.sheet[&self.frame_order[previous.start]].positions[&track].clone();
        let mut edited = self.clone();
        // Python は前の保持の頭の clone_slot() を入れる（局所の Cell は頭と共有する）。
        if source.compatibility.local.is_some() {
            let share = source
                .compatibility
                .local_share
                .unwrap_or_else(|| edited.next_local_share());
            source.compatibility.local_share = Some(share);
            edited
                .position_mut(previous.start, track)
                .compatibility
                .local_share = Some(share);
        }
        // Python の clone_slot() は内容・空キーの印を代入し直す（真なら _content_origin が
        // 落ちる）。続く blank_start = False の代入は、値が変われば _dense_import を落とす。
        let keyed = matches!(source.reference, Reference::Number(_) | Reference::Blank)
            || source.compatibility.unnumbered_content;
        for index in span.clone() {
            let mut slot = source.clone();
            slot.tween = None;
            slot.tween_member = None;
            if source.blank_start {
                slot.compatibility.dense_import = None;
            }
            if keyed {
                slot.compatibility.content_origin = None;
            }
            slot.blank_start = false;
            // replace_slot の attach_track が配置待ちの所属を列へ戻す。
            slot.compatibility.pending_detached = false;
            if let Some(legacy) = slot
                .compatibility
                .legacy
                .as_mut()
                .and_then(|position| position.legacy.as_mut())
            {
                // 写した保存のキーが結合した範囲を越えないようにする。
                legacy.state.exposure = legacy.state.exposure.min(span.end - index);
            }
            *edited.position_mut(index, track) = slot;
        }
        // 入れ直した位置が抜けて一つになった共有の組を外す（Python の Cell はもう共有しない）。
        edited.drop_single_shares();
        *self = edited;
        Ok(vec![Change::SheetSpans {
            layer_index,
            start: previous.start,
            stop: span.end,
        }])
    }

    /// Python の set_sheet_duration と同様に、`column` を含む保持の長さを `duration` にする。
    ///
    /// 伸ばした位置（後ろのキーも上書きする）にはキーの保持の参照を入れ、縮めた位置を消す。
    /// 保持の外の位置なら何もしない。
    pub fn set_sheet_duration(
        &mut self,
        column: usize,
        layer_index: usize,
        duration: usize,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        let Some(span) = self.display_spans(column, &[layer_index])?[0].clone() else {
            return Ok(Vec::new());
        };
        let duration = duration.max(1);
        let (start, length) = (span.start, span.len());
        let before = self.frame_order.len();
        let end = start
            .checked_add(duration)
            .ok_or(Error::TimelineSizeOverflow)?;
        let mut edited = self.clone();
        edited.ensure_frame_count(end)?;
        // 上書きする位置・消す位置が共有の組から抜け、残りが一つになった組は共有でなくなる。
        let mut shares = std::collections::BTreeSet::new();
        for index in start + 1..end.max(start + length) {
            shares.extend(edited.position_mut(index, track).compatibility.local_share);
        }
        if duration > 1 {
            // Python は保持の頭の clone_reference() を直接入れる（replace_slot を通さないので
            // 配置待ちの所属は写しが持ち、局所の Cell は保持の頭と共有する）。
            let source = edited.position_mut(start, track).clone();
            let mut held = held_reference(
                source,
                &edited.tracks[&track].settings,
                self.blank_artwork()?,
            );
            held.compatibility.pending_detached = true;
            if held.compatibility.local.is_some() {
                let share = edited.position_mut(start, track).compatibility.local_share;
                let share = share.unwrap_or_else(|| edited.next_local_share());
                edited.position_mut(start, track).compatibility.local_share = Some(share);
                held.compatibility.local_share = Some(share);
            }
            for index in start + 1..end {
                *edited.position_mut(index, track) = held.clone();
            }
        }
        for index in end..start + length {
            *edited.position_mut(index, track) = edited.cleared_position(index, track)?;
        }
        for share in shares {
            // 組の位置が一つだけなら外す（その行だけをその場で写す）。
            let mut members = edited.sheet.iter().flat_map(|(&frame, row)| {
                row.positions
                    .iter()
                    .filter(|(_, position)| position.compatibility.local_share == Some(share))
                    .map(move |(&track, _)| (frame, track))
            });
            if let (Some((frame, track)), None) = (members.next(), members.next()) {
                let row = edited.sheet.get_mut(&frame).expect("組の位置の行");
                Arc::make_mut(row)
                    .positions
                    .get_mut(&track)
                    .expect("組の位置")
                    .compatibility
                    .local_share = None;
            }
        }
        let after = edited.frame_order.len();
        let mut changes = Vec::new();
        if after > before {
            changes.push(Change::FrameStructure {
                operation: FrameOperation::Extend,
                before,
                after,
                start: before,
            });
        }
        changes.push(Change::SheetSpans {
            layer_index,
            start,
            stop: start + length.max(duration),
        });
        *self = edited;
        Ok(changes)
    }

    /// Python の resize_sheet_exposure と同様に、キーの保持の長さを左端または右端で変える。
    ///
    /// `key_column`〜`old_end` が今の保持、`new_start`〜`new_end` が変えたあとの保持。左端では
    /// 新しい範囲の外を消し、範囲の位置をキーの保持の参照で入れ直す。右端では後ろのキーに
    /// かかる分だけ後ろを右へ送り、伸ばした位置を保持の参照にし、縮めた位置を消す。
    pub fn resize_sheet_exposure(
        &mut self,
        layer_index: usize,
        left: bool,
        key_column: usize,
        old_end: usize,
        new_start: usize,
        new_end: usize,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        let before = self.frame_order.len();
        if key_column > old_end || old_end >= before || new_start > new_end {
            return Err(invalid());
        }
        let source = self.sheet[&self.frame_order[key_column]].positions[&track].clone();
        let blank = self.blank_artwork()?;
        let mut edited = self.clone();
        let mut stop = old_end.max(new_end) + 1;
        let cursor;
        if left {
            if new_end != old_end {
                return Err(invalid());
            }
            for column in key_column.min(new_start)..=old_end {
                let position = if column < new_start {
                    edited.cleared_position(column, track)?
                } else {
                    let mut held = held_reference(
                        source.clone(),
                        &edited.tracks[&track].settings,
                        blank.clone(),
                    );
                    held.blank_start =
                        source.reference_for(false) == Reference::Blank && column == new_start;
                    held
                };
                *edited.position_mut(column, track) = position;
            }
            cursor = new_start;
        } else {
            if new_start != key_column {
                return Err(invalid());
            }
            if let Some(following) = edited.step_column_after(track, key_column) {
                if new_end >= following {
                    let shift = new_end + 1 - following;
                    let count = edited.frame_order.len();
                    edited.shift_positions_right(following, layer_index, shift)?;
                    edited.ensure_frame_count(
                        count
                            .checked_add(shift)
                            .ok_or(Error::TimelineSizeOverflow)?,
                    )?;
                    stop = edited.frame_order.len();
                }
            }
            edited
                .ensure_frame_count(new_end.checked_add(1).ok_or(Error::TimelineSizeOverflow)?)?;
            for column in key_column + 1..=old_end.max(new_end) {
                if column > new_end {
                    *edited.position_mut(column, track) = edited.cleared_position(column, track)?;
                } else if column > old_end {
                    *edited.position_mut(column, track) = held_reference(
                        source.clone(),
                        &edited.tracks[&track].settings,
                        blank.clone(),
                    );
                }
            }
            cursor = key_column;
        }
        let after = edited.frame_order.len();
        let mut changes = Vec::new();
        if after > before {
            changes.push(Change::FrameStructure {
                operation: FrameOperation::Extend,
                before,
                after,
                start: before,
            });
        }
        changes.extend([
            Change::SheetSpans {
                layer_index,
                start: key_column.min(new_start),
                stop,
            },
            Change::Cursor {
                frame: cursor,
                notify: true,
            },
            Change::Selection { layer_index },
        ]);
        edited.current_frame = edited.frame_order[cursor];
        edited.active_layer = track;
        *self = edited;
        Ok(changes)
    }

    /// Python の timeline_step_columns のうち、`after` より後ろの最初のキーの列。
    fn step_column_after(&self, track: TrackId, after: usize) -> Option<usize> {
        let mut previous = None;
        for (column, id) in self.frame_order.iter().enumerate() {
            let position = &self.sheet[id].positions[&track];
            let reference = position.reference_for(false);
            let starts = previous != Some(reference)
                || (reference == Reference::Blank && position.blank_start);
            if column > after && reference != Reference::Empty && starts && !position.pending {
                return Some(column);
            }
            previous = Some(reference);
        }
        None
    }

    fn follow_displaced_blanks(
        &mut self,
        column: usize,
        end: usize,
        layer_index: usize,
        tail_blank: Option<std::ops::Range<usize>>,
    ) -> Result<(), Error> {
        let track = self.track_order[layer_index];
        let mut blanks = std::collections::BTreeMap::new();
        for index in column + 1..end {
            if self.position_mut(index, track).reference_for(false) != Reference::Blank {
                continue;
            }
            if let Some(span) = self.display_spans(index, &[layer_index])?[0].clone() {
                blanks.insert(span.start, span.end);
            }
        }
        if let Some(tail) = tail_blank {
            blanks.insert(tail.start, tail.end);
        }
        let mut blank_end = end;
        for (start, stop) in blanks {
            blank_end = blank_end.max(stop);
            for index in start..stop {
                *self.position_mut(index, track) = self.cleared_position(index, track)?;
            }
        }
        for index in end..blank_end {
            if self.display_spans(index, &[layer_index])?[0].is_some() {
                break;
            }
            *self.position_mut(index, track) = Position {
                reference: Reference::Blank,
                blank_start: index == end,
                ..self.cleared_position(index, track)?
            };
        }
        Ok(())
    }

    /// Python の recall_sheet_number に対応し、バンクにある番号を指定のコマへ置く。
    pub fn recall_sheet_number(
        &mut self,
        column: usize,
        layer_index: usize,
        number: CellNumber,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        if !self.tracks[&track].cells.contains_key(&number) {
            return Err(invalid());
        }
        // Python の連番欄は同番号の配置待ちを表示中の絵より優先する。
        let mut source = None;
        for frame in &self.frame_order {
            let position = &self.sheet[frame].positions[&track];
            let content = position.reference == Reference::Number(number);
            if position.number() == Some(number)
                && (position.pending || content)
                && (source.is_none() || position.pending)
            {
                source = Some(content);
            }
        }
        let content = source.unwrap_or_else(|| {
            self.tracks[&track]
                .legacy_archive_hints
                .get(&number)
                .is_none_or(|hint| hint.has_content)
        });
        let end = column.checked_add(1).ok_or(Error::TimelineSizeOverflow)?;
        let span = self
            .display_spans(column, &[layer_index])?
            .pop()
            .flatten()
            .unwrap_or(column..end);
        let mut changes = self.ensure_frame_count(end)?;
        let position = self.position_mut(column, track);
        position.reference = if content {
            Reference::Number(number)
        } else {
            Reference::Blank
        };
        position.pending = false;
        position.blank_start = !content;
        position.compatibility.retained_number = (!content).then_some(number);
        position.compatibility.unnumbered_content = false;
        position.compatibility.local = None;
        position.compatibility.dense_import = None;
        position.compatibility.resolved_source = None;
        self.current_frame = self.frame_order[column];
        self.active_layer = track;
        changes.extend([
            Change::SheetSpans {
                layer_index,
                start: span.start,
                stop: span.end,
            },
            Change::Cursor {
                frame: column,
                notify: true,
            },
            Change::Selection { layer_index },
        ]);
        Ok(changes)
    }

    /// Python の split_sheet_clip と同様に、保持区間の後半を新番号へ切り離す。
    pub fn split_sheet_clip(
        &mut self,
        column: usize,
        layer_index: usize,
    ) -> Result<ChangeSet, Error> {
        let span = self.display_spans(column, &[layer_index])?[0]
            .clone()
            .filter(|span| span.start < column)
            .ok_or_else(invalid)?;
        let track = self.track_order[layer_index];
        let head = &self.sheet[&self.frame_order[span.start]].positions[&track];
        let artwork = head
            .number()
            .and_then(|number| self.tracks[&track].cells.get(&number))
            .or(head.compatibility.local.as_ref());
        let cell = match artwork {
            Some(cell) => cell.clone(),
            None => Cell {
                image: crate::image::RgbaImage::blank(self.width, self.height)?,
                initial_image: None,
                cell_name: None,
            },
        };
        let number = self.next_drawing_number(layer_index)?;
        self.tracks
            .get_mut(&track)
            .ok_or_else(invalid)?
            .cells
            .insert(number, cell);
        for index in column..span.end {
            let position = self.position_mut(index, track);
            position.reference = Reference::Number(number);
            position.blank_start = false;
            position.pending = false;
            position.tween = None;
            position.tween_member = None;
            position.compatibility.retained_number = None;
            position.compatibility.local = None;
            position.compatibility.unnumbered_content = false;
        }
        Ok(vec![Change::SheetSpans {
            layer_index,
            start: span.start,
            stop: span.end,
        }])
    }

    /// Python の new_sheet_cell と同様に、指定位置に末尾採番した空画像を置く。
    pub fn new_sheet_cell(
        &mut self,
        column: usize,
        layer_index: usize,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        let frame = *self.frame_order.get(column).ok_or_else(invalid)?;
        let span = self.display_spans(column, &[layer_index])?[0]
            .clone()
            .unwrap_or(column..column + 1);
        let number = self.next_drawing_number(layer_index)?;
        let cleared = self.cleared_position(column, track)?;
        let cell = match cleared.compatibility.local {
            Some(cell) => cell,
            None => Cell {
                image: crate::image::RgbaImage::blank(self.width, self.height)?,
                initial_image: None,
                cell_name: None,
            },
        };
        self.tracks
            .get_mut(&track)
            .ok_or_else(invalid)?
            .cells
            .insert(number, cell);
        *self.position_mut(column, track) = Position {
            reference: Reference::Number(number),
            ..Position::default()
        };
        self.current_frame = frame;
        self.active_layer = track;
        Ok(vec![
            Change::SheetSpans {
                layer_index,
                start: span.start,
                stop: span.end,
            },
            Change::Cursor {
                frame: column,
                notify: true,
            },
            Change::Selection { layer_index },
        ])
    }

    /// Python の new_sheet_number と同様に、選択コマだけを新しい番号の絵として独立させる。
    pub fn new_sheet_number(
        &mut self,
        column: usize,
        layer_index: usize,
    ) -> Result<ChangeSet, Error> {
        let span = self
            .display_spans(column, &[layer_index])?
            .pop()
            .flatten()
            .ok_or_else(invalid)?;
        let track = self.track_order[layer_index];
        let source = &self.sheet[&self.frame_order[span.start]].positions[&track];
        let Reference::Number(source_number) = source.reference else {
            return Err(invalid());
        };
        let cell = self.tracks[&track]
            .cells
            .get(&source_number)
            .ok_or_else(invalid)?;
        let new_cell = Cell {
            cell_name: None,
            ..cell.clone()
        };
        // Python は元の位置を clone するので旧位置の記録は残る。取り込み時の密な参照は、
        // 番号が変わる（sequence_number の付け替え）ので捨てられる。
        let legacy = source.compatibility.legacy.clone();
        let number = self.next_drawing_number(layer_index)?;
        self.tracks
            .get_mut(&track)
            .ok_or_else(invalid)?
            .cells
            .insert(number, new_cell);
        let mut position = Position {
            reference: Reference::Number(number),
            ..Position::default()
        };
        position.compatibility.legacy = legacy;
        *self.position_mut(column, track) = position;
        self.current_frame = self.frame_order[column];
        self.active_layer = track;
        Ok(vec![
            Change::SheetSpans {
                layer_index,
                start: span.start,
                stop: span.end,
            },
            Change::Cursor {
                frame: column,
                notify: true,
            },
            Change::Selection { layer_index },
        ])
    }

    /// Python の make_position_editable のうち、内容の無い位置に描き始める場合と同様に、
    /// column・layer_index の位置を新しい番号 number の絵にする。
    ///
    /// 新しい絵の画像 image は呼び元が今の手順と同じ作り方で渡す（区間の先頭の絵の写し・
    /// 空画像・位置の局所の絵の画像）。初期画像とセル名は位置の局所の絵（共有の組なら組の
    /// 先頭の位置の値）から引き継ぐ。
    /// block は呼び元が求めた保持の範囲（空キーか, 先頭, 長さ）。空キーの範囲なら、その位置の
    /// 後ろから範囲の終わりまでの位置も同じ番号を指す（局所の絵はそのまま）。書き換えた位置は
    /// 取り込み時の記録と、内容を外したときの番号（content_origin）を捨てる（Python の
    /// Layer.__setattr__ と同じ）。その位置は局所の絵の共有の組から外れ、位置が一つだけに
    /// なった組は外す。内容のある位置・配置待ち・すでにある番号は Err。
    pub fn make_empty_position_editable(
        &mut self,
        column: usize,
        layer_index: usize,
        number: CellNumber,
        block: Option<(bool, usize, usize)>,
        image: RgbaImage,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        if column >= self.frame_order.len() || self.tracks[&track].cells.contains_key(&number) {
            return Err(invalid());
        }
        let (start, stop) = match block {
            Some((_, start, length)) => (start, start.checked_add(length).ok_or_else(invalid)?),
            None => (column, column + 1),
        };
        if !(start <= column && column < stop && stop <= self.frame_order.len()) {
            return Err(invalid());
        }
        let followers = match block {
            Some((true, _, _)) => column + 1..stop,
            _ => column + 1..column + 1,
        };
        for index in column..followers.end {
            let position = &self.sheet[&self.frame_order[index]].positions[&track];
            if matches!(position.reference, Reference::Number(_))
                || position.compatibility.unnumbered_content
                || position.pending
            {
                return Err(invalid());
            }
        }
        let editable = |position: &mut Position| {
            position.reference = Reference::Number(number);
            position.blank_start = false;
            position.compatibility.retained_number = None;
            position.compatibility.unnumbered_content = false;
            position.compatibility.dense_import = None;
            position.compatibility.content_origin = None;
        };
        // 共有の組の絵は組の先頭（表示順で最初の位置）の値が正本（Python では組で一つの Cell）。
        let leader = self.sheet[&self.frame_order[column]].positions[&track]
            .compatibility
            .local_share
            .and_then(|share| {
                self.frame_order.iter().find_map(|id| {
                    let other = &self.sheet[id].positions[&track];
                    (other.compatibility.local_share == Some(share))
                        .then(|| other.compatibility.local.clone())
                        .flatten()
                })
            });
        let position = self.position_mut(column, track);
        // Python の detach_cell は位置の絵を新しい Cell にする（共有の組からも外れる）。
        let local = position.compatibility.local.take();
        let local = leader.or(local);
        position.compatibility.local_share = None;
        editable(position);
        for index in followers {
            editable(self.position_mut(index, track));
        }
        self.drop_single_shares();
        let (initial_image, cell_name) =
            local.map_or((None, None), |cell| (cell.initial_image, cell.cell_name));
        self.tracks
            .get_mut(&track)
            .ok_or_else(invalid)?
            .cells
            .insert(
                number,
                Cell {
                    image,
                    initial_image,
                    cell_name,
                },
            );
        Ok(vec![Change::SheetSpans {
            layer_index,
            start,
            stop,
        }])
    }

    /// Python の create_blank_key と同様に、区間の途中を空キーで分けるか、区間の後ろに空キーを挿入する。
    pub fn create_blank_key(
        &mut self,
        column: usize,
        layer_index: usize,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        let mut edited = self.clone();
        let before = edited.frame_order.len();
        edited.ensure_frame_count(column.checked_add(1).ok_or(Error::TimelineSizeOverflow)?)?;
        let span = edited.display_spans(column, &[layer_index])?[0].clone();
        let (start, head, end, shifted) = match span {
            Some(span) if column == span.start => {
                let end = span
                    .end
                    .checked_add(span.len())
                    .ok_or(Error::TimelineSizeOverflow)?;
                edited.shift_positions_right(span.end, layer_index, span.len())?;
                (span.start, span.end, end, true)
            }
            Some(span) => (span.start, column, span.end, false),
            None => {
                let previous = (0..column).rev().find(|&index| {
                    let position = &edited.sheet[&edited.frame_order[index]].positions[&track];
                    position.reference != Reference::Empty
                        || position.compatibility.unnumbered_content
                });
                if let Some(previous) = previous {
                    // Python は previous.clone_reference() を直接入れる（replace_slot を通さない
                    // ので配置待ちの所属は写しが持ち、局所の Cell は previous と共有する）。
                    let prior = edited.position_mut(previous, track).clone();
                    let mut held = held_reference(
                        prior,
                        &edited.tracks[&track].settings,
                        self.blank_artwork()?,
                    );
                    held.compatibility.pending_detached = true;
                    if previous + 1 < column && held.compatibility.local.is_some() {
                        let share = edited
                            .position_mut(previous, track)
                            .compatibility
                            .local_share;
                        let share = share.unwrap_or_else(|| edited.next_local_share());
                        edited
                            .position_mut(previous, track)
                            .compatibility
                            .local_share = Some(share);
                        held.compatibility.local_share = Some(share);
                    }
                    for index in previous + 1..column {
                        *edited.position_mut(index, track) = held.clone();
                    }
                }
                (previous.unwrap_or(column), column, column + 1, false)
            }
        };
        let prior = edited.position_mut(head, track).clone();
        let retained_number = prior.number();
        // create_blank_key は旧位置の初期画像ではなく、detach_cell した絵を使う。
        let artwork = if let Some(number) = retained_number
            .filter(|_| prior.pending || matches!(prior.reference, Reference::Number(_)))
        {
            edited.tracks[&track].cells.get(&number)
        } else {
            prior.compatibility.local.as_ref()
        };
        // Python は detach_cell で今の絵の写しを局所の Cell にし、画像だけ空にする（初期画像と
        // セル名は残る）。今の絵が無ければ空の Cell。
        let local = Some(Cell {
            image: edited.blank_artwork()?,
            initial_image: artwork.and_then(|cell| cell.initial_image.clone()),
            cell_name: artwork.and_then(|cell| cell.cell_name.clone()),
        });
        // 今の手順は空キーの位置をその場で書き換えるので、clear_pending・detach_cell が外さない
        // 印（_pending_detached・_legacy_missing・_v2_length_imported・_v2_resolve_source）は残る。
        let kept = &prior.compatibility;
        let blank = Position {
            reference: Reference::Blank,
            tween: prior.tween,
            tween_member: prior.tween_member,
            compatibility: super::compatibility::PositionCompatibility {
                retained_number,
                local,
                pending_detached: kept.pending_detached,
                legacy_missing: kept.legacy_missing,
                length_imported: kept.length_imported,
                resolved_source: kept.resolved_source,
                ..Default::default()
            },
            ..Position::default()
        };
        // Python は空キーの位置の clone_reference() を直接入れる（配置待ちの所属は写しが持ち、
        // 局所の Cell は空キーの位置と共有する）。
        let mut held = held_reference(
            blank.clone(),
            &edited.tracks[&track].settings,
            self.blank_artwork()?,
        );
        held.compatibility.pending_detached = true;
        let mut head_position = Position {
            blank_start: true,
            ..blank
        };
        if head + 1 < end {
            let share = edited.next_local_share();
            head_position.compatibility.local_share = Some(share);
            held.compatibility.local_share = Some(share);
        }
        *edited.position_mut(head, track) = head_position;
        for index in head + 1..end {
            *edited.position_mut(index, track) = held.clone();
        }
        edited.drop_single_shares();
        let after = edited.frame_order.len();
        let mut changes = Vec::new();
        if after > before {
            changes.push(Change::FrameStructure {
                operation: FrameOperation::Extend,
                before,
                after,
                start: before,
            });
        }
        edited.current_frame = edited.frame_order[head];
        edited.active_layer = track;
        changes.extend([
            Change::SheetSpans {
                layer_index,
                start,
                stop: if shifted { after } else { end },
            },
            Change::Cursor {
                frame: head,
                notify: true,
            },
            Change::Selection { layer_index },
        ]);
        *self = edited;
        Ok(changes)
    }

    /// Python の clear_sheet_span に対応し、絵を保持したまま参照を外す。
    pub fn clear_sheet_span(
        &mut self,
        layer_index: usize,
        range: std::ops::Range<usize>,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        if range.is_empty() || range.end > self.frame_order.len() {
            return Err(invalid());
        }
        let span = self
            .display_spans(range.start, &[layer_index])?
            .pop()
            .flatten();
        let (start, stop) = span.map_or((range.start, range.end), |span| {
            (span.start, span.end.max(range.end))
        });
        let replacements = range
            .clone()
            .map(|column| self.cleared_position(column, track))
            .collect::<Result<Vec<_>, _>>()?;
        for (column, position) in range.zip(replacements) {
            *self.position_mut(column, track) = position;
        }
        Ok(vec![Change::SheetSpans {
            layer_index,
            start,
            stop,
        }])
    }

    /// Python の delete_sequence_entry に対応し、バンクとすべての参照から絵を取り除く。
    pub fn delete_sequence_entry(
        &mut self,
        layer_index: usize,
        number: CellNumber,
    ) -> Result<ChangeSet, Error> {
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        let mut edited = self.clone();
        let before = edited.frame_order.len();
        let cursor = edited
            .frame_order
            .iter()
            .position(|&id| id == edited.current_frame)
            .ok_or_else(invalid)?;
        let mut found = edited.tracks[&track].cells.contains_key(&number);
        for column in 0..before {
            if edited.position_mut(column, track).number() == Some(number) {
                *edited.position_mut(column, track) = edited.cleared_position(column, track)?;
                found = true;
            }
        }
        let bank = edited.tracks.get_mut(&track).ok_or_else(invalid)?;
        bank.legacy_archive_hints.remove(&number);
        bank.cells.remove(&number);
        if !found {
            return Ok(Vec::new());
        }
        let after = edited.trim_unused_tail()?;
        let frame = cursor.min(after - 1);
        edited.current_frame = edited.frame_order[frame];
        let mut changes = Vec::new();
        if before != after {
            changes.push(Change::FrameStructure {
                operation: FrameOperation::Trim,
                before,
                after,
                start: after,
            });
        }
        changes.extend([
            Change::SheetSpans {
                layer_index,
                start: 0,
                stop: before,
            },
            Change::Cursor {
                frame,
                notify: true,
            },
        ]);
        *self = edited;
        Ok(changes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document::v2;
    use crate::image::RgbaImage;
    use crate::project::payload::decode_v2_plan;
    use serde_json::json;

    // tests/test_document_live_fast_paths.py:test_make_position_editable_on_empty_positions_uses_core_command
    #[test]
    fn drawing_inside_a_blank_hold_numbers_the_rest_of_the_hold() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1, 1, 1],
            "tracks": [{
                "cells": [{"number": 1, "image": "1.png"}],
                "sheet": [1, "BLANK", null, null],
            }],
        });
        let plan = decode_v2_plan(&input, &["1.png".into()]).unwrap();
        let mut doc = v2::from_plan(&input, &plan, &[RgbaImage::blank(1, 1).unwrap()]).unwrap();
        let number = CellNumber::new(2).unwrap();
        let image = RgbaImage::blank(1, 1).unwrap();
        // 内容のある位置・すでにある番号は扱わない（文書は変えない）。
        assert!(doc
            .make_empty_position_editable(0, 0, number, Some((false, 0, 1)), image.clone())
            .is_err());
        assert!(doc
            .make_empty_position_editable(2, 0, CellNumber::new(1).unwrap(), None, image.clone())
            .is_err());
        let changes = doc
            .make_empty_position_editable(2, 0, number, Some((true, 1, 3)), image)
            .unwrap();
        assert_eq!(
            changes,
            vec![Change::SheetSpans {
                layer_index: 0,
                start: 1,
                stop: 4
            }]
        );
        assert_eq!(
            v2::project(&doc, &input).unwrap().payload["tracks"][0]["sheet"],
            json!([1, "BLANK", 2, 2])
        );
        assert!(doc.tracks[&doc.track_order[0]].cells.contains_key(&number));
    }

    // tests/test_core_document_compare.py:test_move_sheet_cells
    #[test]
    fn moving_several_spans_clears_sources_before_placement_and_is_atomic() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1, 1],
            "tracks": [{
                "cells": [{"number": 1, "image": "1.png"}],
                "sheet": [1, "BLANK", null],
            }],
        });
        let plan = decode_v2_plan(&input, &["1.png".into()]).unwrap();
        let mut doc = v2::from_plan(&input, &plan, &[RgbaImage::blank(1, 1).unwrap()]).unwrap();
        let initial = v2::project(&doc, &input).unwrap().payload;
        let moves = [
            SheetMove {
                source: (0, 0),
                destination: (1, 0),
            },
            SheetMove {
                source: (1, 0),
                destination: (2, 0),
            },
        ];
        let invalid = [
            moves[0],
            SheetMove {
                destination: (usize::MAX, 0),
                ..moves[1]
            },
        ];
        assert!(doc.move_sheet_cells(&invalid, 1, 0).is_err());
        assert_eq!(v2::project(&doc, &input).unwrap().payload, initial);
        let changes = doc.move_sheet_cells(&moves, 1, 0).unwrap();
        assert_eq!(changes.len(), 6);
        assert_eq!(
            changes[0],
            Change::SheetSpans {
                layer_index: 0,
                start: 0,
                stop: 1
            }
        );
        assert_eq!(
            changes[3],
            Change::SheetSpans {
                layer_index: 0,
                start: 2,
                stop: 3
            }
        );
        assert_eq!(
            v2::project(&doc, &input).unwrap().payload["tracks"][0]["sheet"],
            json!([null, 1, "BLANK"])
        );
    }

    // tests/test_core_document_compare.py:test_multiple_moves_with_number_collision
    #[test]
    fn batch_collision_preserves_destination_pixels_and_fills_missing_attributes() {
        let input = json!({
            "canvas": {"width": 4, "height": 3},
            "frame_durations": [1, 1, 1, 1],
            "tracks": [
                {"cells": [{"number": 3, "image": "red.png", "initial": "yellow.png",
                            "cell_name": "incoming"}], "sheet": [3, null, 3, null]},
                {"cells": [{"number": 3, "image": "blue.png", "cell_name": "existing"}],
                 "sheet": [3, null, null, null]},
            ],
        });
        let names = ["red.png", "yellow.png", "blue.png"].map(str::to_owned);
        let plan = decode_v2_plan(&input, &names).unwrap();
        let images: Vec<_> = plan
            .images
            .iter()
            .map(|entry| {
                let pixel = match entry[0].as_str().unwrap() {
                    "red.png" => [255, 0, 0, 255],
                    "yellow.png" => [255, 255, 0, 255],
                    "blue.png" => [0, 0, 255, 255],
                    path => panic!("unexpected image {path}"),
                };
                RgbaImage::from_rgba(4, 3, &pixel.repeat(12)).unwrap()
            })
            .collect();
        let mut doc = v2::from_plan(&input, &plan, &images).unwrap();
        let moves = [
            SheetMove {
                source: (0, 0),
                destination: (1, 1),
            },
            SheetMove {
                source: (2, 0),
                destination: (3, 1),
            },
        ];
        assert_eq!(
            doc.move_sheet_cells(&moves, 0, 1).unwrap(),
            [
                Change::SheetSpans {
                    layer_index: 0,
                    start: 0,
                    stop: 1
                },
                Change::SheetSpans {
                    layer_index: 1,
                    start: 1,
                    stop: 2
                },
                Change::SheetSpans {
                    layer_index: 0,
                    start: 2,
                    stop: 3
                },
                Change::SheetSpans {
                    layer_index: 1,
                    start: 3,
                    stop: 4
                },
                Change::Cursor {
                    frame: 0,
                    notify: true
                },
                Change::Selection { layer_index: 1 },
            ]
        );
        let number = CellNumber::new(3).unwrap();
        let destination = &doc.track(doc.track_order()[1]).unwrap().cells[&number];
        assert_eq!(&destination.image.rgba()[..4], &[0, 0, 255, 255]);
        assert_eq!(destination.cell_name.as_deref(), Some("existing"));
        assert_eq!(
            &destination.initial_image.as_ref().unwrap().rgba()[..4],
            &[255, 255, 0, 255]
        );
        let projected = v2::project(&doc, &input).unwrap();
        assert_eq!(
            projected.payload["tracks"][0]["sheet"],
            json!([null, null, null, null])
        );
        assert_eq!(
            projected.payload["tracks"][1]["sheet"],
            json!([3, 3, null, 3])
        );
    }

    // tests/test_dense_timeline_edits.py:test_blank_creation_splits_hold_and_undo
    // tests/test_dense_timeline_edits.py:test_blank_creation_at_start_inserts_full_blank_span
    #[test]
    fn inserting_blank_at_a_key_keeps_the_drawing_and_splits_only_at_a_hold() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1, 1, 1],
            "tracks": [{
                "cells": [{"number": 1, "image": "1.png"}],
                "sheet": [1, 1, 1, null],
            }],
        });
        let plan = decode_v2_plan(&input, &["1.png".into()]).unwrap();
        let image = RgbaImage::blank(1, 1).unwrap();
        let doc = v2::from_plan(&input, &plan, &[image]).unwrap();

        let mut at_key = doc.clone();
        let changes = at_key.create_blank_key(0, 0).unwrap();
        assert!(matches!(changes[0], Change::FrameStructure { .. }));
        let result = v2::project(&at_key, &input).unwrap();
        assert_eq!(
            result.payload["tracks"][0]["sheet"],
            json!([1, 1, 1, "BLANK", "BLANK", "BLANK"])
        );
        assert_eq!(at_key.display_spans(5, &[0]).unwrap(), [Some(3..6)]);

        let mut inside_hold = doc;
        let changes = inside_hold.create_blank_key(1, 0).unwrap();
        assert!(matches!(changes[0], Change::SheetSpans { .. }));
        let result = v2::project(&inside_hold, &input).unwrap();
        assert_eq!(
            result.payload["tracks"][0]["sheet"],
            json!([1, "BLANK", "BLANK", null])
        );
        assert_eq!(inside_hold.display_spans(2, &[0]).unwrap(), [Some(1..3)]);
        assert!(inside_hold.create_blank_key(usize::MAX, 0).is_err());
        assert_eq!(
            v2::project(&inside_hold, &input).unwrap().payload,
            result.payload
        );
    }

    // tests/test_track_cells.py:test_painting_retained_slot_allocates_tail_and_keeps_rescued_drawing
    // tests/test_undo_by_number.py:test_paint_unused_paper_cell_assigns_number
    #[test]
    fn fresh_sheet_cells_keep_paper_settings_and_skip_reserved_numbers() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1],
            "tracks": [
                {"is_paper": true, "cells": [], "sheet": [null]},
                {"cells": [{"number": 12, "image": "12.png"}], "sheet": ["BLANK"]},
            ],
        });
        let plan = decode_v2_plan(&input, &["12.png".into()]).unwrap();
        let rescued = [18, 52, 86, 255];
        let image = RgbaImage::from_rgba(1, 1, &rescued).unwrap();
        let mut doc = v2::from_plan(&input, &plan, &[image]).unwrap();
        doc.new_sheet_cell(0, 0).unwrap();
        doc.new_sheet_cell(0, 1).unwrap();
        let paper = doc.track_order()[0];
        let drawing = doc.track_order()[1];
        assert!(doc.track(paper).unwrap().settings.is_paper);
        assert_eq!(doc.placed_numbers(paper), [CellNumber::new(1).unwrap()]);
        assert_eq!(doc.placed_numbers(drawing), [CellNumber::new(13).unwrap()]);
        assert_eq!(
            doc.track(drawing).unwrap().cells[&CellNumber::new(12).unwrap()]
                .image
                .rgba(),
            rescued
        );
    }

    // tests/test_track_cells.py:test_last_reference_survives_save_reload_and_recall
    // tests/test_track_cells.py:test_sequence_delete_removes_all_references_and_bank_with_undo
    #[test]
    fn cleared_references_keep_artwork_until_the_number_is_deleted() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1, 1],
            "tracks": [{
                "cells": [{"number": 3, "image": "3.png", "cell_name": "key"}],
                "sheet": [3, "BLANK", 3],
            }],
        });
        let plan = decode_v2_plan(&input, &["3.png".into()]).unwrap();
        let pixels = [17, 34, 51, 255];
        let image = RgbaImage::from_rgba(1, 1, &pixels).unwrap();
        let mut doc = v2::from_plan(&input, &plan, &[image]).unwrap();
        let id = doc.track_order()[0];
        let number = CellNumber::new(3).unwrap();
        doc.clear_sheet_span(0, 0..1).unwrap();
        doc.clear_sheet_span(0, 2..3).unwrap();
        assert!(doc.placed_numbers(id).is_empty());
        let saved = v2::project(&doc, &input).unwrap();
        assert_eq!(saved.payload["tracks"][0]["cells"][0]["number"], 3);
        doc.recall_sheet_number(2, 0, number).unwrap();
        assert_eq!(doc.placed_numbers(id), [number]);
        assert_eq!(doc.track(id).unwrap().cells[&number].image.rgba(), pixels);
        assert_eq!(
            doc.track(id).unwrap().cells[&number].cell_name.as_deref(),
            Some("key")
        );
        doc.delete_sequence_entry(0, number).unwrap();
        assert!(!doc.track(id).unwrap().cells.contains_key(&number));
        assert!(doc.placed_numbers(id).is_empty());
    }

    // tests/test_track_cells.py:test_cross_track_collision_keeps_legacy_pixels_and_attribute_priority
    #[test]
    fn numbered_paste_uses_preferred_pixels_and_first_sheet_attributes() {
        let input = json!({
            "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 1],
            "tracks": [
                {"cells": [{"number": 1, "image": "source.png"}], "sheet": [1, null]},
                {"cells": [{"number": 1, "image": "destination.png", "initial": "initial.png",
                            "cell_name": "destination-first"}], "sheet": [1, null]},
            ],
        });
        let paths = [
            "source.png".into(),
            "destination.png".into(),
            "initial.png".into(),
        ];
        let plan = decode_v2_plan(&input, &paths).unwrap();
        let source = [170, 187, 204, 255];
        let destination = [153, 136, 119, 255];
        let initial = [68, 85, 102, 255];
        let images = plan
            .images
            .iter()
            .map(|image| {
                let rgba = match image[0].as_str().unwrap() {
                    "source.png" => source,
                    "destination.png" => destination,
                    "initial.png" => initial,
                    path => panic!("unexpected path: {path}"),
                };
                RgbaImage::from_rgba(1, 1, &rgba).unwrap()
            })
            .collect::<Vec<_>>();
        let baseline = v2::from_plan(&input, &plan, &images).unwrap();
        let one = CellNumber::new(1).unwrap();
        for (preferred, expected) in [(0, destination), (1, source)] {
            let mut doc = baseline.clone();
            doc.current_frame = doc.frame_order[preferred];
            let target = doc.track_order[1];
            let before = v2::project(&doc, &input).unwrap();
            assert!(doc.paste_numbered_cell((0, 0), (1, 1), usize::MAX).is_err());
            assert_eq!(v2::project(&doc, &input).unwrap().payload, before.payload);
            assert_eq!(doc.tracks[&target].cells[&one].image.rgba(), destination);
            assert_eq!(
                doc.paste_numbered_cell((0, 0), (1, 1), 1).unwrap(),
                [Change::SheetSpans {
                    layer_index: 1,
                    start: 1,
                    stop: 2
                }]
            );
            let cell = &doc.tracks[&target].cells[&one];
            assert_eq!(cell.image.rgba(), expected);
            assert_eq!(cell.cell_name.as_deref(), Some("destination-first"));
            assert_eq!(cell.initial_image.as_ref().unwrap().rgba(), initial);
            assert_eq!(
                doc.tracks[&doc.track_order[0]].cells[&one].image.rgba(),
                source
            );
        }

        // tests/test_cell_attributes.py:test_archive_fallback
        // 保管にしかない初期画像と、シート初出の名前を別々に選ぶ。
        let mut doc = baseline;
        let source_id = doc.track_order[0];
        let target = doc.track_order[1];
        doc.tracks
            .get_mut(&source_id)
            .unwrap()
            .cells
            .get_mut(&one)
            .unwrap()
            .cell_name = Some("sheet-name".into());
        let hint = super::super::compatibility::ArchiveHint {
            has_content: true,
            is_blank_key: false,
            exposure: 1,
            pending: false,
            tween: None,
            tween_member: None,
        };
        doc.tracks
            .get_mut(&target)
            .unwrap()
            .legacy_archive_hints
            .insert(one, hint);
        *doc.position_mut(0, target) = Position::default();
        doc.paste_numbered_cell((0, 0), (1, 1), 1).unwrap();
        let cell = &doc.tracks[&target].cells[&one];
        assert_eq!(cell.image.rgba(), source);
        assert_eq!(cell.cell_name.as_deref(), Some("sheet-name"));
        assert_eq!(cell.initial_image.as_ref().unwrap().rgba(), initial);
    }
}
