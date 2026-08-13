"""Timeline structure editing for PaintCanvas: layers, frames and sequence numbers.

Split out of ``canvas.py`` as a mixin. These methods change the *shape* of the
document rather than pixels: adding/deleting layers and frames, shifting and
moving timeline cells, exposure blocks, and the sequence-number bookkeeping that
keeps numbered (imported) images consistent with the cells that reference them.
They run against a live ``PaintCanvas``.
"""
from .common import *  # noqa: F401,F403
from ._canvas_members import CanvasMembers
from .models import Layer, make_frame
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
            if li < len(f.layers):
                f.layers[li].visible = bool(on)
                changed = True
        if changed:
            self._onion_cache.clear()
            self.update()

    def set_layer_opacity(self, li, opacity):
        """表示用のレイヤー不透明度。画像内のRGBA値は変更しない。"""
        if li < 0:
            return
        opacity = max(0.0, min(1.0, float(opacity)))
        changed = False
        for frame in self.frames:
            if li < len(frame.layers):
                frame.layers[li].opacity = opacity
                changed = True
        if changed:
            self._onion_cache.clear()
            self.update()

    def add_layer(self):
        # 全フレーム・全画像の文書スナップショットを作らず、
        # 追加レイヤーだけをUndo対象にして大量コマ時の待ち時間を抑える。
        old_active = int(self.active_layer_index)
        insert_index = len(self.layers)
        name = f"Layer {insert_index + 1}"
        self.push_undo(LayerRemoveUndo(insert_index, old_active))

        # QImageの暗黙共有を使い、空画像バッファをコマ数分確保しない。
        shared_blank = blank_image()
        for frame in self.frames:
            frame.layers.append(
                Layer(
                    name,
                    shared_blank.copy(),
                    visible=True,
                    opacity=1.0,
                )
            )
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
        stored_layers = [
            frame.layers[index].clone()
            for frame in self.frames
        ]
        self.push_undo(LayerInsertUndo(index, stored_layers, target_active))
        for frame in self.frames:
            frame.layers.pop(index)
        self.active_layer_index = target_active
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
    @staticmethod
    def _clear_timeline_layer_cell(layer):
        layer.image = blank_image()
        layer.has_content = False
        layer.is_blank_key = False
        layer.exposure = 1
        layer.sequence_number = None
        layer.sequence_only = False

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

        explicit_cells = []
        required_count = start_column + amount
        for column in range(
            start_column,
            len(self.frames),
        ):
            if (
                layer_index
                >= len(self.frames[column].layers)
            ):
                continue
            layer = self.frames[column].layers[
                layer_index
            ]
            if (
                layer.has_content
                or getattr(
                    layer,
                    "is_blank_key",
                    False,
                )
            ):
                copied = layer.clone()
                explicit_cells.append(
                    (column, copied)
                )
                required_count = max(
                    required_count,
                    column
                    + amount
                    + max(1, int(copied.exposure)),
                )

        self._ensure_frame_count(required_count)

        # 先に元の明示コマをすべて未使用へ戻してから配置する。
        # これにより、移動元と移動先が重なっても内容を失わない。
        for column, _copied in explicit_cells:
            self._clear_timeline_layer_cell(
                self.frames[column].layers[
                    layer_index
                ]
            )

        for column, copied in reversed(
            explicit_cells
        ):
            self.frames[
                column + amount
            ].layers[layer_index] = copied

    def _shift_timeline_layer_left(
        self,
        layer_index,
        start_column,
        amount=1,
    ):
        """指定位置以降の明示コマを左へ移動し、削除した時間を詰める。"""
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

        explicit_cells = []
        for column in range(start_column, len(self.frames)):
            layer = self.frames[column].layers[layer_index]
            if (
                layer.has_content
                or getattr(layer, "is_blank_key", False)
            ):
                explicit_cells.append((column, layer.clone()))

        for column, _copied in explicit_cells:
            self._clear_timeline_layer_cell(
                self.frames[column].layers[layer_index]
            )

        for column, copied in explicit_cells:
            target_column = column - amount
            if target_column >= 0:
                self.frames[target_column].layers[layer_index] = copied

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
        new_frame = source_frame.clone()
        at = self.current_frame + 1
        self.frames.insert(at, new_frame)
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
                self._sequence_archive[
                    (layer_index, int(layer.sequence_number))
                ] = layer.clone()
            if exposure > 1:
                layer.exposure = exposure - 1
                self._shift_timeline_layer_left(
                    layer_index,
                    start + exposure,
                )
            else:
                self._clear_timeline_layer_cell(layer)
                self._shift_timeline_layer_left(
                    layer_index,
                    start + 1,
                )
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

        columns = []
        for column in range(len(self.frames)):
            try:
                kind, start, _exposure = TimelineWidget.timeline_span_at(
                    self.frames,
                    layer_index,
                    column,
                )
            except (IndexError, KeyError, TypeError, AttributeError, ValueError) as exc:
                log.debug("timeline_span_at(col=%s) failed: %s", column, exc)
                continue
            if kind in ("content", "blank") and start == column:
                columns.append(column)
        return columns


    def set_duration(self, d):
        layer = self.active_layer
        if not layer.has_content:
            source = self.resolve_key_frame(self.current_frame, self.active_layer_index)
            if source is not None:
                self.frames[source].layers[self.active_layer_index].exposure = max(1, int(d))
            else:
                layer.exposure = max(1, int(d))
        else:
            layer.exposure = max(1, int(d))
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
        key_layer = self.frames[key_column].layers[layer_index]
        exposure = max(1, int(key_layer.exposure))
        end_column = min(
            len(self.frames) - 1,
            key_column + exposure - 1,
        )
        return (
            int(key_column),
            int(end_column),
            int(exposure),
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
            sheet_numbers = []
            for frame in self.frames:
                layer = frame.layers[target_layer_index]
                if (
                    layer.has_content
                    and not layer.sequence_only
                    and layer.sequence_number is not None
                    and int(layer.sequence_number) not in sheet_numbers
                ):
                    sheet_numbers.append(int(layer.sequence_number))
            all_numbers = {
                int(frame.layers[target_layer_index].sequence_number)
                for frame in self.frames
                if frame.layers[target_layer_index].sequence_number is not None
            }
            all_numbers.update(
                int(number)
                for archived_layer, number in self._sequence_archive
                if int(archived_layer) == target_layer_index
            )
            remaining = sorted(all_numbers - set(sheet_numbers))
            ordered = sheet_numbers + remaining
            mapping = {
                old_number: new_number
                for new_number, old_number in enumerate(ordered, 1)
            }
            next_number = len(mapping) + 1
            for frame in self.frames:
                layer = frame.layers[target_layer_index]
                if layer.sequence_number is not None:
                    layer.sequence_number = mapping[int(layer.sequence_number)]
                elif layer.has_content and not layer.sequence_only:
                    layer.sequence_number = next_number
                    next_number += 1
            normalized_archive = {}
            for (archived_layer, old_number), archived in self._sequence_archive.items():
                if int(archived_layer) == target_layer_index:
                    new_number = mapping.get(int(old_number), int(old_number))
                    archived.sequence_number = new_number
                else:
                    new_number = int(old_number)
                normalized_archive[(int(archived_layer), new_number)] = archived
            self._sequence_archive = normalized_archive

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
                and (layer.has_content or layer.is_blank_key)
            ):
                number = int(number)
                if number not in entries or layer.sequence_only:
                    entries[number] = column
        return [entries[number] for number in sorted(entries)]

    def insert_sequence_blank(self, layer_index, after_number):
        """選択番号の直後へ空画像番号を挿入し、後続番号を送る。"""
        layer_index = int(layer_index)
        entries = self.sequence_entry_columns(layer_index)
        insert_number = (
            1 if not entries else max(1, int(after_number) + 1)
        )
        self.push_doc_undo()
        # 同じ番号は同じ画像を指すため、シート上の重複参照も含めて
        # 挿入位置以降を一括で繰り下げる。
        for frame in self.frames:
            layer = frame.layers[layer_index]
            if (
                layer.sequence_number is not None
                and int(layer.sequence_number) >= insert_number
            ):
                layer.sequence_number = int(layer.sequence_number) + 1
        shifted_archive = {}
        for (archived_layer, number), archived in self._sequence_archive.items():
            if archived_layer == layer_index and int(number) >= insert_number:
                archived.sequence_number = int(number) + 1
                number = int(number) + 1
            shifted_archive[(archived_layer, int(number))] = archived
        self._sequence_archive = shifted_archive
        new_column = len(self.frames)
        self._ensure_frame_count(new_column + 1)
        target = self.frames[new_column].layers[layer_index]
        target.image = blank_image()
        target.has_content = False
        target.is_blank_key = True
        target.sequence_number = insert_number
        target.sequence_only = True
        target.exposure = 1
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
            int(self.frames[column].layers[layer_index].sequence_number): column
            for column in columns
        }
        if first_number not in by_number or second_number not in by_number:
            return False
        self.push_doc_undo()
        low, high = sorted((first_number, second_number))
        ordered_numbers = list(range(low, high + 1))
        snapshots = {}
        for number in ordered_numbers:
            if number not in by_number:
                return False
            layer = self.frames[by_number[number]].layers[layer_index]
            snapshots[number] = (
                layer.image.copy(), bool(layer.has_content), bool(layer.is_blank_key)
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
        for frame in self.frames:
            layer = frame.layers[layer_index]
            number = layer.sequence_number
            if number in source_for_number:
                image, has_content, is_blank = snapshots[source_for_number[number]]
                layer.image = image.copy()
                layer.has_content = has_content
                layer.is_blank_key = is_blank
        self.changed.emit()
        self.update()
        return True

    def sync_numbered_image_from_cell(self, frame_index, layer_index):
        """同じレイヤー・同じ絵番号を、1つの画像オブジェクトへ結び直す。"""
        source = self.frames[int(frame_index)].layers[int(layer_index)]
        if source.sequence_number is None or not source.has_content:
            return
        for index, frame in enumerate(self.frames):
            if index == int(frame_index):
                continue
            target = frame.layers[int(layer_index)]
            if target.sequence_number == source.sequence_number:
                # QImageのコピーを配ると、次の描画開始時点で各セルが
                # 別画像へ分離する。同じPythonオブジェクトを共有し、
                # 同じ番号を実体1枚として扱う。
                target.image = source.image
                target.has_content = True
                target.is_blank_key = False
        archived = self._sequence_archive.get((
            int(layer_index), int(source.sequence_number)
        ))
        if archived is not None:
            archived.image = source.image
            archived.has_content = True
            archived.is_blank_key = False

    def coalesce_numbered_images(self):
        """文書内の同一レイヤー・同一番号の画像参照を統合する。"""
        shared = {}
        preferred = int(self.current_frame)
        if 0 <= preferred < len(self.frames):
            for layer_index, layer in enumerate(self.frames[preferred].layers):
                if layer.has_content and layer.sequence_number is not None:
                    shared[(layer_index, int(layer.sequence_number))] = layer.image

        for frame in self.frames:
            for layer_index, layer in enumerate(frame.layers):
                if not layer.has_content or layer.sequence_number is None:
                    continue
                key = (layer_index, int(layer.sequence_number))
                image = shared.setdefault(key, layer.image)
                layer.image = image

        for key, archived in self._sequence_archive.items():
            shared_image = shared.get((int(key[0]), int(key[1])))
            if shared_image is not None:
                archived.image = shared_image

    def delete_sequence_entry(self, layer_index, number):
        """連番画像を削除し、シート側の参照セルを未使用へ戻す。"""
        number = int(number)
        self.push_doc_undo()
        found = False
        for frame in self.frames:
            layer = frame.layers[int(layer_index)]
            if layer.sequence_number == number:
                self._clear_timeline_layer_cell(layer)
                found = True
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
                if layer.sequence_only and layer.sequence_number is not None:
                    pending.append((int(layer.sequence_number), layer_index, column, layer.clone()))
        for number, layer_index, source_column, copied in sorted(pending):
            target_exposure = 1
            previous = None
            for column, frame in enumerate(self.frames):
                layer = frame.layers[layer_index]
                if (
                    not layer.sequence_only
                    and layer.sequence_number == number - 1
                    and (layer.has_content or layer.is_blank_key)
                ):
                    previous = (column, layer)
                    break
            target_column = None
            if previous is not None:
                start, previous_layer = previous
                exposure = max(1, int(previous_layer.exposure))
                if exposure > 1:
                    # 保持区間の長さは変えず、前半を既存番号、後半を
                    # 追加番号へ分配する。
                    target_column = start + max(1, exposure // 2)
                    previous_layer.exposure = target_column - start
                    target_exposure = start + exposure - target_column
            if target_column is None:
                visible_columns = [
                    column
                    for column, frame in enumerate(self.frames)
                    if any(
                        (layer.has_content or layer.is_blank_key)
                        and not layer.sequence_only
                        for layer in frame.layers
                    )
                ]
                target_column = (max(visible_columns) + 1) if visible_columns else 0
            self._ensure_frame_count(target_column + 1)
            target = self.frames[target_column].layers[layer_index]
            if target.has_content or target.is_blank_key:
                target_column = len(self.frames)
                self._ensure_frame_count(target_column + 1)
                target = self.frames[target_column].layers[layer_index]
            target.image = copied.image.copy()
            target.has_content = bool(copied.has_content)
            target.is_blank_key = bool(
                copied.is_blank_key and not copied.has_content
            )
            target.sequence_number = number
            target.sequence_only = False
            target.exposure = max(1, int(target_exposure))
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
        source_frame = int(source_frame)
        destination_frame = int(destination_frame)
        source_layer = int(source_layer)
        destination_layer = int(destination_layer)
        if not (0 <= source_frame < len(self.frames)) or destination_frame < 0:
            return False
        if not (0 <= source_layer < len(self.frames[source_frame].layers)):
            return False
        source = self.frames[source_frame].layers[source_layer]
        source_is_blank = bool(
            getattr(source, "is_blank_key", False)
        )
        if not (source.has_content or source_is_blank):
            return False

        self.push_doc_undo()
        moving = source.clone()
        moving.exposure = max(1, int(source.exposure))

        # 同一レイヤー内で後方へ移動するときは、元セルを抜いた分だけ座標を補正。
        source_span = max(1, int(source.exposure))
        self._clear_timeline_layer_cell(source)

        # 隣接する「●ーー」の直後のキーを移動した場合、抜けた位置は
        # 空セルにせず直前キーの保持区間（ー）として埋める。
        previous_key = None
        for index in range(source_frame - 1, -1, -1):
            candidate = self.frames[index].layers[source_layer]
            if (
                candidate.has_content
                or getattr(candidate, "is_blank_key", False)
            ):
                previous_key = index
                break
        if previous_key is not None:
            previous = self.frames[previous_key].layers[source_layer]
            previous_end = previous_key + max(1, int(previous.exposure))
            if previous_end >= source_frame:
                fill_until = source_frame + source_span
                if (
                    source_layer == destination_layer
                    and destination_frame > source_frame
                ):
                    # キーを右へずらした距離全体を直前キーの保持で埋める。
                    # 移動元の1コマ分だけ延長すると途中が未使用になる。
                    fill_until = max(fill_until, destination_frame)
                previous.exposure = max(
                    int(previous.exposure),
                    fill_until - previous_key,
                )

        self._ensure_frame_count(destination_frame + 1)
        if not (0 <= destination_layer < len(self.frames[destination_frame].layers)):
            return False

        occupying_block = self.timeline_block_at(
            destination_frame,
            destination_layer,
        )
        if occupying_block is not None:
            _occupying_kind, key, _occupying_exposure = (
                occupying_block
            )
            if int(key) != source_frame:
                occupied = self.frames[key].layers[
                    destination_layer
                ]
                if key < destination_frame:
                    # 保持区間上へ落とした場合は落下位置で切る。
                    occupied.exposure = max(
                        1,
                        destination_frame - key,
                    )
                elif key == destination_frame:
                    # 既存の●／○以降を右へ1セル送る。
                    keys = [
                        index
                        for index in range(len(self.frames))
                        if (
                            (
                                self.frames[index]
                                .layers[destination_layer]
                                .has_content
                            )
                            or getattr(
                                self.frames[index]
                                .layers[destination_layer],
                                "is_blank_key",
                                False,
                            )
                        )
                        and index >= destination_frame
                    ]
                    self._ensure_frame_count(
                        len(self.frames) + 1
                    )
                    for index in reversed(keys):
                        target = index + 1
                        self._ensure_frame_count(target + 1)
                        src = self.frames[index].layers[
                            destination_layer
                        ]
                        dst = self.frames[target].layers[
                            destination_layer
                        ]
                        copied = src.clone()
                        dst.image = copied.image.copy()
                        dst.has_content = bool(
                            copied.has_content
                        )
                        dst.is_blank_key = bool(
                            getattr(
                                copied,
                                "is_blank_key",
                                False,
                            )
                        )
                        dst.exposure = int(copied.exposure)
                        dst.visible = bool(copied.visible)
                        dst.opacity = float(copied.opacity)
                        dst.alpha_locked = bool(
                            copied.alpha_locked
                        )
                        dst.color_filter_enabled = bool(
                            copied.color_filter_enabled
                        )
                        dst.color_filter_rgb = (
                            tuple(copied.color_filter_rgb)
                            if copied.color_filter_rgb is not None
                            else None
                        )
                        dst.sequence_number = copied.sequence_number
                        dst.sequence_only = bool(copied.sequence_only)
                        self._clear_timeline_layer_cell(src)

        destination = self.frames[destination_frame].layers[destination_layer]
        destination.image = (
            moving.image.copy()
            if not source_is_blank
            else blank_image()
        )
        destination.has_content = not source_is_blank
        destination.is_blank_key = source_is_blank
        destination.sequence_number = (
            None if source_is_blank else moving.sequence_number
        )
        destination.sequence_only = bool(moving.sequence_only)
        destination.exposure = moving.exposure
        destination.visible = moving.visible
        destination.opacity = moving.opacity
        destination.color_filter_enabled = moving.color_filter_enabled
        destination.color_filter_rgb = (
            tuple(moving.color_filter_rgb)
            if moving.color_filter_rgb is not None else None
        )
        self.current_frame = destination_frame
        self.active_layer_index = destination_layer
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True


    def _ensure_frame_count(self, count):
        if count <= len(self.frames):
            return
        names = [layer.name for layer in self.frames[0].layers]
        template = self.frames[0].layers
        while len(self.frames) < count:
            frame = make_frame(names)
            for index, layer in enumerate(frame.layers):
                layer.visible = template[index].visible
                layer.opacity = template[index].opacity
                layer.alpha_locked = template[index].alpha_locked
            self.frames.append(frame)
