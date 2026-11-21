use super::changes::{Change, ChangeSet, FrameOperation};
use super::frame_commands::held_reference;
use super::model::{Cell, CellNumber, Document, FrameId, Position, Reference, TrackId};
use crate::Error;

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
            let held = held_reference(head.clone(), settings, blank);
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
        let (column, layer_index) = destination;
        let (source_column, source_layer) = source;
        let source_track = *self.track_order.get(source_layer).ok_or_else(invalid)?;
        let track = *self.track_order.get(layer_index).ok_or_else(invalid)?;
        let source_id = self.frame_order.get(source_column).ok_or_else(invalid)?;
        if length == 0 || column >= self.frame_order.len() {
            return Err(invalid());
        }
        let source = &self.sheet[source_id].positions[&source_track];
        let Reference::Number(number) = source.reference else {
            return Err(invalid());
        };
        let incoming = self.tracks[&source_track]
            .cells
            .get(&number)
            .ok_or_else(invalid)?;
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
