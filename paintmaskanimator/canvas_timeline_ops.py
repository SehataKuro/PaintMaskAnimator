"""Timeline structure editing for PaintCanvas: layers, frames and sequence numbers.

Split out of ``canvas.py`` as a mixin. These methods change the *shape* of the
document rather than pixels: adding/deleting layers and frames, shifting and
moving timeline cells, exposure blocks, and the sequence-number bookkeeping that
keeps numbered (imported) images consistent with the cells that reference them.
They run against a live ``PaintCanvas``.
"""
from dataclasses import replace
from .document import replace_slot, release_slots
from .document import mark_pending, clear_pending
from PySide6.QtGui import QImage
from ._canvas_members import CanvasMembers
from .cell_numbering import normalization_mapping
from .document import (
    is_pending,
    clear_slot, archive_hint, apply_number_mapping, apply_numbered_images, copy_drawing_reference,
    import_drawing_reference,
)
from .models import Cell, Frame, Layer, make_frame, next_layer_name
from .timeline import TimelineWidget
from .utils import blank_image
from .logging_setup import get_logger
from .undo_entries import (
    LayerInsertUndo,
    LayerRemoveUndo,
)

log = get_logger(__name__)


class TimelineStructureMixin(CanvasMembers):
    def set_layer_visibility(self, li, on):
        if li < 0:
            return
        changed = False
        for f in self.frames:
            if li < len(f.layers) and f.layers[li].visible != bool(on):
                f.layers[li].visible = bool(on)
                changed = True
        if changed:
            # Onion signatures include visibility. Keep images for the other
            # layers (and for switching back); no pixels changed here.
            self._playback_frame_cache.clear()
            self.update()

    def set_layer_opacity(self, li, opacity):
        """表示用のレイヤー不透明度。画像内のRGBA値は変更しない。"""
        if li < 0:
            return
        opacity = max(0.0, min(1.0, float(opacity)))
        changed = False
        for frame in self.frames:
            if li < len(frame.layers) and frame.layers[li].opacity != opacity:
                frame.layers[li].opacity = opacity
                changed = True
        if changed:
            self._playback_frame_cache.clear()
            self.update()

    def add_layer(self):
        # 全フレーム・全画像の文書スナップショットを作らず、
        # 追加レイヤーだけをUndo対象にして大量コマ時の待ち時間を抑える。
        old_active = int(self.active_layer_index)
        insert_index = len(self.layers)
        name = next_layer_name(layer.name for layer in self.layers)
        self.push_undo(LayerRemoveUndo(insert_index, old_active))

        # QImageの暗黙共有を使い、空画像バッファをコマ数分確保しない。
        shared_blank = blank_image()
        owner = None
        for frame in self.frames:
            added = Layer(
                name,
                shared_blank.copy(),
                visible=True,
                opacity=1.0,
            )
            # 足したレイヤーも、全コマで1つの Track を共有する。
            if owner is None:
                owner = added
            else:
                added.share_track_with(owner)
            frame.layers.append(added)
        owner._track.connected = True
        self._document.sync_columns()
        self.active_layer_index = insert_index
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
    def delete_layer(self):
        if len(self.layers) <= 1:
            return
        index = int(self.active_layer_index)
        target_active = max(0, index - 1)
        stored_layers = self._document.capture_column(index)
        self.push_undo(LayerInsertUndo(index, stored_layers, target_active))
        for frame in self.frames:
            release_slots([frame.layers[index]])
            frame.layers.pop(index)
        self._document.sync_columns()
        self.active_layer_index = target_active
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
    @staticmethod
    def _clear_timeline_layer_cell(layer):
        clear_slot(layer)

    def timeline_block_at(self, column, layer_index):
        if not self.frames:
            return None
        column = int(column)
        layer_index = int(layer_index)
        if not (0 <= column < len(self.frames)):
            return None
        kind, start, exposure = TimelineWidget.timeline_span_at(
            self.frames,
            layer_index,
            column,
        )
        if kind not in ("content", "blank") or start is None:
            return None
        return kind, int(start), max(1, int(exposure))

    def _shift_timeline_layer_right(
        self,
        layer_index,
        start_column,
        amount,
        *,
        extend_containing=False,
    ):
        """指定レイヤーの明示コマを右へ移動し、時間の隙間を作る。"""
        if not self.frames:
            return

        layer_index = int(layer_index)
        start_column = max(0, int(start_column))
        amount = max(1, int(amount))
        if not (
            0 <= layer_index
            < len(self.frames[0].layers)
        ):
            return

        containing = self.timeline_block_at(start_column, layer_index)
        slots = [frame.layers[layer_index].clone_slot() for frame in self.frames]
        # Serialization residue follows the old explicit-position copy policy:
        # hidden hold payload stays in place unless a moved key overwrites it.
        from .legacy_v1 import V1Position, snapshot_layer
        positions = [slot.__dict__.get("_v1_position", V1Position(snapshot_layer(slot)))
                     for slot in slots]
        starts = [i for i, slot in enumerate(slots) if i >= start_column and (
            slot.sequence_only or (self.timeline_block_at(i, layer_index) is not None
                                  and self.timeline_block_at(i, layer_index)[1] == i))]
        saved_positions = dict(enumerate(positions))
        for i in reversed(starts):
            empty = slots[i].clone_slot()
            clear_slot(empty)
            saved_positions[i] = V1Position(snapshot_layer(empty))
            saved_positions[i + amount] = positions[i]
        # Trailing empty positions do not force another document extension.
        occupied = [i for i in range(start_column, len(slots))
                    if slots[i].has_content or slots[i].is_blank_key or slots[i].sequence_only]
        self._ensure_frame_count(max([start_column + amount, len(slots)]
                                     + [i + amount + 1 for i in occupied]))
        for column in range(len(self.frames) - 1, start_column - 1, -1):
            source = column - amount
            if source >= start_column and source < len(slots):
                replace_slot(self.frames[column], layer_index, slots[source])
            else:
                self._clear_timeline_layer_cell(self.frames[column].layers[layer_index])
        if containing is not None and containing[1] < start_column and not extend_containing:
            _kind, left, length = containing
            end = left + length
            for column in range(start_column, min(end + amount, len(self.frames))):
                if column < end:
                    replace_slot(self.frames[column], layer_index, slots[column].clone_slot())
                else:
                    clear_slot(self.frames[column].layers[layer_index])
        for column in range(start_column, len(self.frames)):
            target = self.frames[column].layers[layer_index]
            position = saved_positions.get(column)
            if position is None:
                empty = target.clone_slot()
                clear_slot(empty)
                position = V1Position(snapshot_layer(empty))
            target.__dict__["_v1_position"] = position

    def _shift_timeline_layer_left(self, layer_index, start_column, amount=1):
        if not self.frames:
            return
        start_column, amount = max(0, int(start_column)), max(1, int(amount))
        slots = [frame.layers[layer_index].clone_slot() for frame in self.frames]
        for column in range(max(0, start_column - amount), len(slots)):
            source = column + amount
            if source < len(slots):
                replace_slot(self.frames[column], layer_index, slots[source])
            else:
                self._clear_timeline_layer_cell(self.frames[column].layers[layer_index])

    def _trim_unused_trailing_frames(self):
        """全レイヤーで未使用になった末尾の時間列を取り除く。"""
        while len(self.frames) > 1:
            last_column = len(self.frames) - 1
            if any(
                self.timeline_block_at(last_column, layer_index)
                is not None
                for layer_index in range(len(self.frames[0].layers))
            ):
                break
            release_slots(self.frames[-1].layers)
            self.frames.pop()


    def add_frame(self, dup):
        # 互換用。空追加は選択中コマの直後へ○を挿入する。
        if not dup:
            return self.create_blank_key(
                self.current_frame,
                self.active_layer_index,
            )

        self.push_doc_undo()
        source_frame = self.frames[self.current_frame]
        new_frame = Frame([layer.clone_slot() for layer in source_frame.layers], source_frame.duration)
        at = self.current_frame + 1
        for layer in new_frame.layers:
            layer.attach_track(layer._track)
        held = [self.timeline_block_at(self.current_frame, li)
                for li in range(len(source_frame.layers))]
        self.frames.insert(at, new_frame)
        for li, block in enumerate(held):
            if block is not None and block[1] < self.current_frame:
                clear_slot(new_frame.layers[li])
                end = block[1] + block[2]
                for column in range(at, min(end + 1, len(self.frames))):
                    if column < end:
                        replace_slot(self.frames[column], li, source_frame.layers[li].clone_reference())
                    else:
                        clear_slot(self.frames[column].layers[li])
        self.current_frame = at
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def delete_frame(self):
        """選択レイヤーの表示コマを1つ削除する。"""
        if not self.frames:
            return
        layer_index = int(self.active_layer_index)
        current = int(self.current_frame)
        block = self.timeline_block_at(current, layer_index)

        self.push_doc_undo()
        if block is not None:
            _kind, start, exposure = block
            layer = self.frames[start].layers[layer_index]
            if layer.has_content and layer.sequence_number is not None:
                # Detached legacy frames must join the bank before clearing
                # their last position, or the subsequent UI install loses it.
                if not layer._track.connected:
                    self.install_legacy_frames()
                self._document.put_archive_hint(
                    layer._track, int(layer.sequence_number),
                    replace(archive_hint(layer), exposure=exposure),
                )
            self._shift_timeline_layer_left(layer_index, current + 1)
            self.current_frame = min(current, len(self.frames) - 1)
        elif len(self.frames) > 1:
            # ほかのレイヤーの時間列は動かさず、選択レイヤーだけを詰める。
            self._shift_timeline_layer_left(
                layer_index,
                current + 1,
            )
            self.current_frame = current
        else:
            if self.undo_stack:
                self.undo_stack.pop()
            return

        self._trim_unused_trailing_frames()
        self.current_frame = min(self.current_frame, len(self.frames) - 1)

        self._cell_structure_dirty = True
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()

    def previous_frame(self):
        if not self.frames:
            return
        self.current_frame = max(0, self.current_frame - 1)
        self.selectionChanged.emit()
        self.update()

    def next_frame(self):
        if not self.frames:
            return
        next_column = self.current_frame + 1
        old_count = len(self.frames)
        self._ensure_frame_count(next_column + 1)
        self.current_frame = next_column
        # 追加されたセルは未使用のまま。○の空キーフレームにはしない。
        if len(self.frames) != old_count:
            self._cell_structure_dirty = True
            self.changed.emit()
        self.selectionChanged.emit()
        self.update()

    def timeline_step_columns(self, layer_index=None):
        """このレイヤーで有効なコマ開始位置一覧を返す。

        内容キーフレーム（●）だけでなく、空フレーム（○）も
        1つのコマ開始位置として扱う。
        """
        if not self.frames:
            return []
        if layer_index is None:
            layer_index = self.active_layer_index
        layer_index = int(layer_index)

        from .timeline_metadata import reference_for
        columns = []
        previous = None
        for column, frame in enumerate(self.frames):
            if not 0 <= layer_index < len(frame.layers):
                previous = None
                continue
            layer = frame.layers[layer_index]
            reference = reference_for(layer)
            starts = reference != previous or (reference.kind == "BLANK" and layer.blank_start)
            if reference.kind != "EMPTY" and starts and not is_pending(layer):
                columns.append(column)
            previous = reference
        return columns


    def set_duration(self, d):
        block = self.timeline_block_at(self.current_frame, self.active_layer_index)
        if block is not None:
            _kind, start, length = block
            duration = max(1, int(d))
            self._ensure_frame_count(start + duration)
            source = self.frames[start].layers[self.active_layer_index]
            for column in range(start + 1, start + duration):
                self.frames[column].layers[self.active_layer_index] = source.clone_reference()
            for column in range(start + duration, start + length):
                self._clear_timeline_layer_cell(self.frames[column].layers[self.active_layer_index])
        self.changed.emit()

    def current_exposure(self):
        return self.current_frame


    def resolve_exposure_block(self, column, layer_index):
        """●ーーーー｜を1つのキーフレーム露出ブロックとして返す。"""
        if not self.frames:
            return None
        column = max(0, min(int(column), len(self.frames) - 1))
        layer_index = int(layer_index)
        key_column = self.resolve_key_frame(column, layer_index)
        if key_column is None:
            return None
        if not (
            0 <= key_column < len(self.frames)
            and 0 <= layer_index
            < len(self.frames[key_column].layers)
        ):
            return None
        exposure = self.timeline_block_at(key_column, layer_index)[2]
        end_column = min(
            len(self.frames) - 1,
            key_column + exposure - 1,
        )
        return (
            int(key_column),
            int(end_column),
            int(exposure),
        )

    def sequence_number_normalization(self, layer_index):
        """正規化したときの {旧番号: 新番号} を、データを変えずに返す。"""
        return normalization_mapping(
            self.frames, {}, layer_index
        )

    def normalize_sequence_numbers(self, layer_index=None):
        """シートの登場順で絵番号を正規化し、連番にも反映する。"""
        if not self.frames:
            return
        if layer_index is None:
            layer_indices = range(len(self.frames[0].layers))
        else:
            layer_indices = (int(layer_index),)
        for target_layer_index in layer_indices:
            mapping = self.sequence_number_normalization(target_layer_index)
            apply_number_mapping(
                self.frames, {}, target_layer_index, mapping,
                unnumbered_start=len(mapping) + 1, normalize_archive_keys=True,
            )

    def sequence_entry_columns(self, layer_index):
        """絵番号ごとの代表セル位置を番号順で返す。"""
        entries = {}
        for column, frame in enumerate(self.frames):
            if not (0 <= layer_index < len(frame.layers)):
                continue
            layer = frame.layers[layer_index]
            number = layer.sequence_number
            if (
                number is not None
                and int(number) >= 1
                and (layer.has_content or layer.sequence_only)
            ):
                number = int(number)
                if number not in entries or layer.sequence_only:
                    entries[number] = column
        return [entries[number] for number in sorted(entries)]

    def insert_sequence_blank(self, layer_index, after_number):
        """選択番号の直後へ空画像番号を挿入し、後続番号を送る。"""
        layer_index = int(layer_index)
        entries = self.sequence_entry_columns(layer_index)
        track = self.frames[0].layers[layer_index]._track
        insert_number = (
            1 if not entries and not track.cells else max(1, int(after_number) + 1)
        )
        self.push_doc_undo()
        mapping = {n: n + 1 for n in track.cells if n >= insert_number}
        apply_number_mapping(
            self.frames, {}, layer_index, mapping)
        track.cells[insert_number] = Cell(blank_image())
        new_column = len(self.frames)
        self._ensure_frame_count(new_column + 1)
        target = self.frames[new_column].layers[layer_index]
        target.image = blank_image()
        target.has_content = False
        target.is_blank_key = True
        target.sequence_number = insert_number
        mark_pending(target)
        if target._track.connected:
            target.__dict__.pop("_cell", None)
        target.blank_start = True
        self.current_frame = new_column
        self.active_layer_index = layer_index
        self._cell_structure_dirty = True
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def move_sequence_image(self, layer_index, first_number, second_number):
        """連番画像を差し込み移動し、間の画像を1コマずつ送る。"""
        first_number = int(first_number)
        second_number = int(second_number)
        if first_number == second_number:
            return False
        columns = self.sequence_entry_columns(int(layer_index))
        by_number = {
            int(self.frames[column].layers[layer_index].sequence_number):
            self.frames[column].layers[layer_index]
            for column in columns
        }
        track = self.frames[0].layers[layer_index]._track
        for number in track.cells:
            if number not in by_number:
                _, by_number[number] = self._resolve_numbered_target(0, layer_index, number)
        if first_number not in by_number or second_number not in by_number:
            return False
        self.push_doc_undo()
        low, high = sorted((first_number, second_number))
        ordered_numbers = list(range(low, high + 1))
        snapshots = {}
        for number in ordered_numbers:
            if number not in by_number:
                return False
            layer = by_number[number]
            snapshots[number] = (
                layer.image.copy(), bool(layer.has_content),
                bool(layer.is_blank_key), layer.cell_name,
                QImage(layer.initial_image) if layer.initial_image is not None else None
            )
        if first_number < second_number:
            source_for_number = {
                number: number + 1 for number in range(first_number, second_number)
            }
        else:
            source_for_number = {
                number: number - 1 for number in range(second_number + 1, first_number + 1)
            }
        source_for_number[second_number] = first_number
        apply_numbered_images(self.frames, layer_index, source_for_number, snapshots)
        self.changed.emit()
        self.update()
        return True

    def link_layer_tracks(self):
        """同じレイヤーのセルを、1つの ``Track``（表示の設定）へ結ぶ。

        先頭コマのセルが持つ設定を、そのレイヤーの設定とする。レイヤーの
        追加・削除・並べ替えや、読み込み・Undo の復元のあとに呼ぶ
        （Issue #31 Phase 2）。
        """
        if not self.frames:
            return
        if any(not layer._track.connected for frame in self.frames for layer in frame.layers):
            self._document.sync_columns()
            self.install_legacy_frames()
            return
        self._document.sync_columns()
        for frame in self.frames:
            for li, layer in enumerate(frame.layers):
                track = self._document.tracks[li]
                if layer._track is not track:
                    if layer.has_content or layer.sequence_only:
                        track.cells.setdefault(int(layer.sequence_number), layer._cell)
                    layer.attach_track(track)
                    if layer.has_content or layer.sequence_only:
                        layer.__dict__.pop("_cell", None)

    def delete_sequence_entry(self, layer_index, number):
        """連番画像を削除し、シート側の参照セルを未使用へ戻す。"""
        number = int(number)
        self.push_doc_undo()
        track = self.frames[0].layers[int(layer_index)]._track
        found = number in track.cells
        for frame in self.frames:
            layer = frame.layers[int(layer_index)]
            if layer.sequence_number == number:
                self._clear_timeline_layer_cell(layer)
                found = True
        self._document.remove_archive_hint(self.frames[0].layers[int(layer_index)]._track, number)
        track.cells.pop(number, None)
        if not found:
            self.undo_stack.pop()
            return False
        self._trim_unused_trailing_frames()
        self.current_frame = min(self.current_frame, len(self.frames) - 1)
        self._cell_structure_dirty = True
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def apply_sequence_only_entries(self):
        """連番で追加した番号をシートの保持区間、または末尾へ反映する。"""
        pending = []
        for column, frame in enumerate(self.frames):
            for layer_index, layer in enumerate(frame.layers):
                if is_pending(layer) and layer.sequence_number is not None:
                    pending.append((int(layer.sequence_number), layer_index, column, layer.clone()))
        for number, layer_index, source_column, copied in sorted(pending):
            # Placement used sparse explicit keys, including clipped v1 lengths.
            # Keep that boundary policy separate from the dense operation span.
            from .document import project_v1_state
            projected, _ = project_v1_state(self.frames)
            target_exposure = 1
            previous = None
            for column, frame in enumerate(self.frames):
                layer = frame.layers[layer_index]
                if (
                    not is_pending(layer)
                    and layer.sequence_number == number - 1
                    and (layer.has_content or layer.is_blank_key)
                ):
                    previous = (column, layer)
                    break
            target_column = None
            if previous is not None:
                start, previous_layer = previous
                exposure = self.timeline_block_at(start, layer_index)[2]
                position = previous_layer.__dict__.get("_v1_position")
                imported = previous_layer.__dict__.get("_dense_import")
                signature = (previous_layer.has_content, previous_layer.is_blank_key,
                             previous_layer.sequence_number, previous_layer.sequence_only,
                             previous_layer.blank_start)
                next_column = start + exposure
                following = (self.frames[next_column].layers[layer_index]
                             if next_column < len(self.frames) else None)
                if (position is not None and position.legacy is not None
                        and imported is not None and imported[0] == signature
                        and following is not None and following.has_content
                        and not is_pending(following)):
                    exposure = max(exposure, int(position.legacy.exposure))
                if exposure > 1:
                    # 保持区間の長さは変えず、前半を既存番号、後半を
                    # 追加番号へ分配する。
                    target_column = start + max(1, exposure // 2)
                    target_exposure = start + exposure - target_column
                    from dataclasses import replace
                    from .legacy_v1 import V1Position, snapshot_layer
                    previous_layer.__dict__["_v1_position"] = V1Position(
                        replace(snapshot_layer(position.legacy if position is not None
                                               and position.legacy is not None else previous_layer),
                                exposure=target_column - start), key_start=True,
                        blank_start=previous_layer.blank_start)
            if target_column is None:
                visible_columns = [
                    column
                    for column, frame in enumerate(projected)
                    if any(
                        (layer.has_content or layer.is_blank_key)
                        and not layer.sequence_only
                        for layer in frame.layers
                    )
                ]
                target_column = (max(visible_columns) + 1) if visible_columns else 0
            self._ensure_frame_count(target_column + 1)
            target = self.frames[target_column].layers[layer_index]
            occupied = self.timeline_block_at(target_column, layer_index)
            collided = False
            if (target.has_content or target.is_blank_key) and (
                    occupied is None or occupied[1] == target_column):
                collided = True
                target_column = len(self.frames)
                self._ensure_frame_count(target_column + 1)
                target = self.frames[target_column].layers[layer_index]
            if (previous is None and occupied is not None
                    and occupied[1] < target_column):
                # A new explicit key cuts the previous hold, including its tail.
                for index in range(target_column + target_exposure, occupied[1] + occupied[2]):
                    self._clear_timeline_layer_cell(self.frames[index].layers[layer_index])
            # Placing another pending of the same number can split a previous
            # placement; keep that old v1 boundary without splitting dense spans.
            from .legacy_v1 import V1Position, snapshot_layer
            tail_position = (self.frames[target_column + target_exposure].layers[layer_index]
                             if target_column + target_exposure < len(self.frames) else None)
            if tail_position is not None and tail_position.has_content:
                tail_position.__dict__["_v1_position"] = V1Position(
                    snapshot_layer(tail_position), key_start=True)
            copy_drawing_reference(target, copied)
            clear_pending(target)
            from dataclasses import replace
            target.__dict__["_v1_position"] = V1Position(
                replace(snapshot_layer(target), exposure=target_exposure), key_start=True)
            dense_end = target_column + (1 if collided else target_exposure)
            self._ensure_frame_count(dense_end)
            for index in range(target_column + 1, dense_end):
                replace_slot(self.frames[index], layer_index, target.clone_reference())
            target.visible = copied.visible
            target.opacity = copied.opacity
            self._clear_timeline_layer_cell(
                self.frames[source_column].layers[layer_index]
            )
        if pending:
            self._trim_unused_trailing_frames()
            self.current_frame = min(self.current_frame, len(self.frames) - 1)
            self._cell_structure_dirty = True
            self.changed.emit()
            self.selectionChanged.emit()
            self.update()


    def flip_active_layer(self, horizontal=True):
        if self.active_layer.is_paper:
            return
        self.ensure_editable_key()
        self.push_layer_undo()
        self.active_layer.image = self.active_layer.image.mirrored(horizontal, not horizontal)
        self.active_layer.has_content = True
        self.cellChanged.emit(self.current_frame, self.active_layer_index)
        self.update()

    def move_timeline_cell(self, source_frame, source_layer, destination_frame, destination_layer):
        source_frame, source_layer = int(source_frame), int(source_layer)
        destination_frame, destination_layer = int(destination_frame), int(destination_layer)
        if not (0 <= source_frame < len(self.frames)) or destination_frame < 0:
            return False
        if not (0 <= source_layer < len(self.frames[source_frame].layers)
                and 0 <= destination_layer < len(self.frames[0].layers)):
            return False
        block = self.timeline_block_at(source_frame, source_layer)
        if block is None:
            return False
        kind, source_frame, source_span = block
        source_end = source_frame + source_span
        moving = [self.frames[index].layers[source_layer].clone_slot()
                  for index in range(source_frame, source_end)]
        previous = (self.timeline_block_at(source_frame - 1, source_layer)
                    if source_frame else None)
        tail = (self.timeline_block_at(source_end, source_layer)
                if source_end < len(self.frames) else None)
        tail_blank = ((tail[1], tail[1] + tail[2])
                      if kind == "content" and source_layer == destination_layer
                      and tail is not None and tail[0] == "blank" else None)
        # Freeze all source references before publishing any modifications.
        self.push_doc_undo()
        for index in range(source_frame, source_end):
            self._clear_timeline_layer_cell(self.frames[index].layers[source_layer])
        if previous is not None and previous[1] + previous[2] == source_frame:
            fill_end = source_end
            if source_layer == destination_layer and destination_frame > source_frame:
                fill_end = max(fill_end, destination_frame)
                if tail is not None:
                    fill_end = min(fill_end, tail[1])
            self._ensure_frame_count(fill_end)
            template = self.frames[previous[1]].layers[source_layer]
            for index in range(source_frame, fill_end):
                replace_slot(self.frames[index], source_layer, template.clone_reference())
        self._ensure_frame_count(destination_frame + source_span)
        occupied = self.timeline_block_at(destination_frame, destination_layer)
        if occupied is not None:
            _kind, start, length = occupied
            if start == destination_frame and start != source_frame:
                self._shift_timeline_layer_right(destination_layer, destination_frame, 1)
            elif start < destination_frame:
                # The old key was truncated at the drop position; its tail
                # must not reappear beyond the moved drawing.
                for index in range(destination_frame, start + length):
                    self._clear_timeline_layer_cell(self.frames[index].layers[destination_layer])
        end = destination_frame + source_span
        # Determine displaced circles before replacing their visible positions.
        if kind == "content":
            self._follow_blanks(destination_layer, destination_frame, end, tail_blank)
        for offset, copied in enumerate(moving):
            index = destination_frame + offset
            target = self.frames[index].layers[destination_layer]
            if source_layer != destination_layer:
                import_drawing_reference(
                    self.frames, {}, index, destination_layer, copied,
                    preferred_frame=destination_frame,
                    content=kind == "content", blank=kind == "blank")
            else:
                replace_slot(self.frames[index], destination_layer, copied)
                target = self.frames[index].layers[destination_layer]
            target.blank_start = kind == "blank" and offset == 0
        # Moving a member removes its membership at the destination, while
        # untouched members keep their original position metadata.
        self.frames[destination_frame].layers[destination_layer].tween = None
        self.frames[destination_frame].layers[destination_layer].tween_member = None
        self.current_frame = destination_frame
        self.active_layer_index = destination_layer
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def _follow_blanks(self, layer_index, start, end, tail_blank=None):
        """Move displaced blank coverage to the end of a moved drawing."""
        spans = {}
        for column in range(start + 1, min(end, len(self.frames))):
            block = self.timeline_block_at(column, layer_index)
            if block is not None and block[0] == "blank":
                spans[block[1]] = block[1] + block[2]
        if tail_blank is not None:
            spans[tail_blank[0]] = tail_blank[1]
        if not spans:
            return
        blank_end = max(spans.values())
        for left, right in spans.items():
            for column in range(left, min(right, len(self.frames))):
                self._clear_timeline_layer_cell(self.frames[column].layers[layer_index])
        if blank_end <= end:
            return
        next_key = next((column for column in range(end, len(self.frames))
                         if self.timeline_block_at(column, layer_index) is not None), None)
        if next_key is not None:
            blank_end = min(blank_end, next_key)
        self._ensure_frame_count(blank_end)
        for column in range(end, blank_end):
            blank = self.frames[column].layers[layer_index]
            self._clear_timeline_layer_cell(blank)
            blank.is_blank_key = True
            blank.blank_start = column == end


    def _ensure_frame_count(self, count):
        if count <= len(self.frames):
            return
        names = [layer.name for layer in self.frames[0].layers]
        template = self.frames[0].layers
        while len(self.frames) < count:
            frame = make_frame(names)
            for index, layer in enumerate(frame.layers):
                # 表示の設定はレイヤーに1つ。写すのではなく同じ Track を指す
                # （Issue #31 Phase 2）。
                layer.share_track_with(template[index])
            self.frames.append(frame)
