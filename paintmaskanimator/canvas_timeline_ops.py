"""Timeline structure editing for PaintCanvas: layers, frames and sequence numbers.

Split out of ``canvas.py`` as a mixin. These methods change the *shape* of the
document rather than pixels: adding/deleting layers and frames, shifting and
moving timeline cells, exposure blocks, and the sequence-number bookkeeping that
keeps numbered (imported) images consistent with the cells that reference them.
They run against a live ``PaintCanvas``.
"""
from itertools import chain
from PySide6.QtGui import QImage
from ._canvas_members import CanvasMembers
from .cell_numbering import normalization_mapping
from .document import (
    clear_slot, put_archive, apply_number_mapping, apply_numbered_images, copy_drawing_reference,
    import_drawing_reference,
)
from .sheet import shift_slots_left, shift_slots_right
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
        stored_layers, archive = self._document.capture_column(index)
        self.push_undo(LayerInsertUndo(index, stored_layers, target_active, archive))
        for frame in self.frames:
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

        slots = []
        for column, frame in enumerate(self.frames):
            if column < start_column:
                slots.append(None)
                continue
            layer = frame.layers[layer_index] if layer_index < len(frame.layers) else None
            slots.append(
                layer.clone_slot() if layer is not None and (
                    layer.has_content or getattr(layer, "is_blank_key", False)
                ) else None
            )
        shifted = shift_slots_right(slots, start_column, amount)
        required_count = start_column + amount
        for column in range(start_column, len(slots)):
            copied = slots[column]
            if copied is not None:
                required_count = max(
                    required_count,
                    column + amount + max(1, int(copied.exposure)),
                )
        self._ensure_frame_count(required_count)

        # 移動元を先にすべて未使用へ戻し、重なる移動先にも複製を配置する。
        for column in range(start_column, len(slots)):
            if slots[column] is not None:
                self._clear_timeline_layer_cell(self.frames[column].layers[layer_index])
        for column in range(len(shifted) - 1, start_column - 1, -1):
            if shifted[column] is not None:
                self.frames[column].layers[layer_index] = shifted[column]

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

        slots = []
        for column, frame in enumerate(self.frames):
            if column < start_column:
                slots.append(None)
                continue
            layer = frame.layers[layer_index]
            slots.append(
                layer.clone_slot() if layer.has_content or getattr(layer, "is_blank_key", False)
                else None
            )
        shifted = shift_slots_left(slots, start_column, amount)
        for column in range(start_column, len(slots)):
            if slots[column] is not None:
                self._clear_timeline_layer_cell(self.frames[column].layers[layer_index])
        for column, copied in enumerate(shifted):
            if copied is not None:
                self.frames[column].layers[layer_index] = copied

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
        new_frame = Frame([layer.clone_slot() for layer in source_frame.layers], source_frame.duration)
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
                put_archive(
                    self._sequence_archive,
                    (layer_index, int(layer.sequence_number)), layer.clone_slot(),
                )
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
        # A span starts exactly at an explicit content/blank key. Resolving
        # every column backwards makes navigation quadratic on sparse sheets.
        for column, frame in enumerate(self.frames):
            if not 0 <= layer_index < len(frame.layers):
                continue
            layer = frame.layers[layer_index]
            if not layer.sequence_only and (layer.has_content or layer.is_blank_key):
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

    def sequence_number_normalization(self, layer_index):
        """正規化したときの {旧番号: 新番号} を、データを変えずに返す。"""
        return normalization_mapping(
            self.frames, self._sequence_archive, layer_index
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
            self._sequence_archive = apply_number_mapping(
                self.frames, self._sequence_archive, target_layer_index, mapping,
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
        self._sequence_archive = apply_number_mapping(
            self.frames, self._sequence_archive, layer_index, mapping)
        track.cells[insert_number] = Cell(blank_image())
        new_column = len(self.frames)
        self._ensure_frame_count(new_column + 1)
        target = self.frames[new_column].layers[layer_index]
        target.image = blank_image()
        target.has_content = False
        target.is_blank_key = True
        target.sequence_number = insert_number
        target.sequence_only = True
        if target._track.connected:
            target.__dict__.pop("_cell", None)
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

    def _numbered_cell_attributes(self, layer_index=None, number=None):
        """結ぶ前に、各属性の最初の非 None 値をシート順→保管の順で確保する。"""
        values = {}
        entries = (
            ((li, int(layer.sequence_number)), layer)
            for frame in self.frames
            for li, layer in (enumerate(frame.layers) if layer_index is None else
                              ((layer_index, frame.layers[layer_index]),))
            if layer.has_content and layer.sequence_number is not None
            and (number is None or layer.sequence_number == number)
        )
        archive = self._sequence_archive.items()
        if layer_index is not None and number is not None:
            key = (layer_index, number)
            archived = self._sequence_archive.get(key)
            archive = () if archived is None else ((key, archived),)
        for key, layer in chain(entries, archive):
            if layer_index is not None and key[0] != layer_index:
                continue
            if number is not None and key[1] != number:
                continue
            attrs = values.setdefault(key, [None, None])
            for index, name in enumerate(("initial_image", "cell_name")):
                if attrs[index] is None:
                    attrs[index] = getattr(layer, name)
        return values

    def sync_numbered_image_from_cell(self, frame_index, layer_index):
        """同じレイヤー・同じ絵番号を、1枚の絵（``Cell``）へ結び直す。

        結んだあとは、どのセルに書き込んでも同じ絵として扱われる
        （Issue #31 Phase 1）。描く直前に呼んで、ストロークが同じ番号の
        全コマへ出るようにする。
        """
        source = self.frames[int(frame_index)].layers[int(layer_index)]
        if source.sequence_number is None or not source.has_content:
            return
        if source._track.connected:
            return
        key = (int(layer_index), int(source.sequence_number))
        attributes = self._numbered_cell_attributes(*key)[key]
        source.initial_image, source.cell_name = attributes
        for index, frame in enumerate(self.frames):
            if index == int(frame_index):
                continue
            target = frame.layers[int(layer_index)]
            if target.has_content and target.sequence_number == source.sequence_number:
                target.share_cell_with(source)
                target.has_content = True
                target.is_blank_key = False
        archived = self._sequence_archive.get((
            int(layer_index), int(source.sequence_number)
        ))
        if archived is not None:
            archived.share_cell_with(source)
            archived.has_content = True
            archived.is_blank_key = False

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
                    layer.__dict__["_track"] = track
                    if layer.has_content or layer.sequence_only:
                        layer.__dict__.pop("_cell", None)

    def coalesce_numbered_images(self):
        """同一レイヤー・同一番号のセルを、1枚の絵（``Cell``）へ結ぶ。

        読み込み・取り込み・Undo の復元で、同じ番号のセルがそれぞれ別の絵を
        持った状態から呼ぶ。結んだあとは、どのセルへ書き込んでも同じ番号の
        全コマに出る（Issue #31 Phase 1）。
        """
        if self.frames and all(layer._track.connected for frame in self.frames
                               for layer in frame.layers):
            return
        self._document.sync_columns()
        self.install_legacy_frames()

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
        self._sequence_archive.pop((int(layer_index), number), None)
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
            copy_drawing_reference(target, copied)
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
        # セルの直後にくっついた空セル（✗）は、セルと一緒に動かす。
        tail_blank = None
        old_end = source_frame + source_span
        if (
            not source_is_blank
            and source_layer == destination_layer
            and old_end < len(self.frames)
        ):
            tail = self.frames[old_end].layers[source_layer]
            if getattr(tail, "is_blank_key", False) and not tail.has_content:
                tail_blank = (old_end, old_end + max(1, int(tail.exposure)))
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
                        # 画素は変わらないので複製しない（暗黙共有で十分）。
                        copy_drawing_reference(dst, copied)
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
                        self._clear_timeline_layer_cell(src)

        destination = self.frames[destination_frame].layers[destination_layer]
        if source_layer != destination_layer:
            import_drawing_reference(
                self.frames, self._sequence_archive, destination_frame, destination_layer,
                moving, preferred_frame=destination_frame,
                content=not source_is_blank, blank=source_is_blank)
        else:
            copy_drawing_reference(destination, moving, content=not source_is_blank, blank=source_is_blank)
        destination.exposure = moving.exposure
        destination.visible = moving.visible
        destination.opacity = moving.opacity
        destination.color_filter_enabled = moving.color_filter_enabled
        destination.color_filter_rgb = (
            tuple(moving.color_filter_rgb)
            if moving.color_filter_rgb is not None else None
        )
        new_end = destination_frame + max(1, int(moving.exposure))
        if not source_is_blank:
            self._follow_blanks(
                destination_layer, destination_frame, new_end, tail_blank,
            )
        self.current_frame = destination_frame
        self.active_layer_index = destination_layer
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def _follow_blanks(self, layer_index, start, end, tail_blank=None):
        """動かしたセル（start〜end の手前）に合わせて空セルを付け直す。

        重なった空セルはセルの終わりまで押し出し（空セルの終わりは動かさない）、
        元の直後にくっついていた空セルは、セルの新しい終わりから始める。
        """
        blank_end = None
        for column in range(start + 1, min(end, len(self.frames))):
            layer = self.frames[column].layers[layer_index]
            if getattr(layer, "is_blank_key", False) and not layer.has_content:
                blank_end = max(
                    blank_end or 0, column + max(1, int(layer.exposure))
                )
                self._clear_timeline_layer_cell(layer)
        if tail_blank is not None:
            tail_start, tail_end = tail_blank
            if tail_start != end:
                tail = self.frames[tail_start].layers[layer_index]
                if tail_start >= end and getattr(tail, "is_blank_key", False):
                    self._clear_timeline_layer_cell(tail)
                blank_end = max(blank_end or 0, tail_end)
        if blank_end is None or blank_end <= end:
            return
        # 押し出した先から次のキーまでの間だけを空セルにする。
        next_key = next(
            (
                column for column in range(end, len(self.frames))
                if self.frames[column].layers[layer_index].has_content
                or getattr(self.frames[column].layers[layer_index], "is_blank_key", False)
            ),
            None,
        )
        if next_key is not None and next_key == end:
            return
        if next_key is not None:
            blank_end = min(blank_end, next_key)
        self._ensure_frame_count(end + 1)
        blank = self.frames[end].layers[layer_index]
        self._clear_timeline_layer_cell(blank)
        blank.is_blank_key = True
        blank.exposure = max(1, blank_end - end)


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
