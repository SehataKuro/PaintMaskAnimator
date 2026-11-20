"""Timeline structure editing for PaintCanvas: layers, frames and sequence numbers.

Split out of ``canvas.py`` as a mixin. These methods change the *shape* of the
document rather than pixels: adding/deleting layers and frames, shifting and
moving timeline cells, exposure blocks, and the sequence-number bookkeeping that
keeps numbered (imported) images consistent with the cells that reference them.
They run against a live ``PaintCanvas``.
"""
from .canvas_changes import publish_duration_change, publish_frame_changes, publish_track_changes
from .pma_image import as_qimage
from ._canvas_members import CanvasMembers
from .cell_numbering import normalization_mapping
from .document import (
    clear_slot,
)
from .logging_setup import get_logger
from .undo_entries import (
    LayerInsertUndo,
    LayerRemoveUndo,
)

log = get_logger(__name__)


class TimelineStructureMixin(CanvasMembers):
    def set_layer_visibility(self, li, on):
        changes = self._document.set_track_properties((li,), visible=bool(on))
        publish_track_changes(self, changes)

    def set_layer_opacity(self, li, opacity):
        """表示用のレイヤー不透明度。画像内のRGBA値は変更しない。"""
        if li < 0:
            return
        opacity = max(0.0, min(1.0, float(opacity)))
        changes = self._document.set_track_properties((li,), opacity=opacity)
        publish_track_changes(self, changes)

    def add_layer(self):
        self._commit_brush_stroke()
        # 追加列だけを Undo 対象にし、全コマの画像スナップショットを作らない。
        self.push_undo(LayerRemoveUndo(len(self.layers), int(self.active_layer_index)))
        publish_track_changes(self, self._document.add_track())

    def delete_layer(self):
        if len(self.layers) <= 1:
            return
        self._commit_brush_stroke()
        index = int(self.active_layer_index)
        target_active = max(0, index - 1)
        stored_layers = self._document.capture_column(index)
        self.push_undo(LayerInsertUndo(index, stored_layers, target_active))
        changes = self._document.remove_tracks((index,), active=target_active)
        publish_track_changes(self, changes)

    @staticmethod
    def _clear_timeline_layer_cell(layer):
        clear_slot(layer)

    def timeline_block_at(self, column, layer_index):
        return self._document.timeline_block_at(column, layer_index)

    def _shift_timeline_layer_right(self, layer_index, start_column, amount, *, extend_containing=False):
        self._document._shift_timeline_layer_right(
            layer_index, start_column, amount, extend_containing=extend_containing,
        )

    def _shift_timeline_layer_left(self, layer_index, start_column, amount=1):
        self._document._shift_timeline_layer_left(layer_index, start_column, amount)

    def _trim_unused_trailing_frames(self):
        self._document._trim_unused_trailing_frames()

    def add_frame(self, dup):
        if not dup:
            return self.create_blank_key(self.current_frame, self.active_layer_index)
        self.push_doc_undo()
        changes = self._document.duplicate_frame(self.current_frame)
        publish_frame_changes(self, changes)
        return bool(changes)

    def delete_frame(self):
        """選択レイヤーの表示コマを1つ削除する。"""
        if not self.frames:
            return
        self.push_doc_undo()
        changes = self._document.delete_frame(int(self.current_frame), int(self.active_layer_index))
        if not changes:
            if self.undo_stack:
                self.undo_stack.pop()
            return
        publish_frame_changes(self, changes)

    def previous_frame(self):
        if not self.frames:
            return
        self.current_frame = max(0, self.current_frame - 1)
        self.selectionChanged.emit()
        self.update()

    def next_frame(self):
        publish_frame_changes(self, self._document.advance_frame())

    def timeline_step_columns(self, layer_index=None):
        layer_index = self.active_layer_index if layer_index is None else int(layer_index)
        return self._document.timeline_step_columns(layer_index)

    def set_duration(self, d):
        changes = self._document.set_sheet_duration(self.current_frame, self.active_layer_index, d)
        publish_duration_change(self, changes)

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
        block = self.timeline_block_at(key_column, layer_index)
        if block is None:
            return None
        exposure = block[2]
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
        indices = range(len(self.frames[0].layers)) if layer_index is None and self.frames else ()
        if layer_index is not None:
            indices = (int(layer_index),)
        return self._document.normalize_sheet_numbers(indices)

    def sequence_entry_columns(self, layer_index):
        return self._document.sequence_entry_columns(layer_index)

    def insert_sequence_blank(self, layer_index, after_number):
        layer_index = int(layer_index)
        if not self.frames or not 0 <= layer_index < len(self.frames[0].layers):
            return False
        self.push_doc_undo()
        changes = self._document.insert_sequence_blank(layer_index, after_number)
        publish_frame_changes(self, changes, clear_onion=False)
        return bool(changes)

    def move_sequence_image(self, layer_index, first_number, second_number):
        layer_index, first_number, second_number = map(int, (layer_index, first_number, second_number))
        sources = self._document.sequence_move_sources(
            layer_index, first_number, second_number, sheet_mode=self.timeline_mode == "sheet",
        )
        if sources is None:
            return False
        self.push_doc_undo()
        changes = self._document.move_sequence_image(layer_index, first_number, second_number, sources)
        publish_frame_changes(self, changes, clear_onion=False, mark_dirty=False)
        return bool(changes)

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
                    number = layer.sequence_number
                    if number is not None and (layer.has_content or layer.sequence_only):
                        track.cells.setdefault(int(number), layer._cell)
                    layer.attach_track(track)
                    if layer.has_content or layer.sequence_only:
                        layer.__dict__.pop("_cell", None)

    def delete_sequence_entry(self, layer_index, number):
        if not self.frames or not 0 <= int(layer_index) < len(self.frames[0].layers):
            return False
        self.push_doc_undo()
        changes = self._document.delete_sequence_entry(layer_index, number)
        if not changes:
            self.undo_stack.pop()
            return False
        publish_frame_changes(self, changes, clear_onion=False)
        return True

    def apply_sequence_only_entries(self):
        self._commit_brush_stroke()
        changes = self._document.place_pending_entries()
        publish_frame_changes(self, changes, clear_onion=False)

    def flip_active_layer(self, horizontal=True):
        if self.active_layer.is_paper:
            return
        self.ensure_editable_key()
        self.push_layer_undo()
        self.active_layer.image = as_qimage(self.active_layer.image).mirrored(
            horizontal, not horizontal
        )
        self.active_layer.has_content = True
        self.cellChanged.emit(self.current_frame, self.active_layer_index)
        self.update()

    def move_timeline_cell(self, source_frame, source_layer, destination_frame, destination_layer):
        prepared = self._document.prepare_sheet_move(
            source_frame, source_layer, destination_frame, destination_layer,
        )
        if prepared is None:
            return False
        self.push_doc_undo()
        changes = self._document.move_sheet_cell(prepared)
        publish_frame_changes(self, changes, mark_dirty=False)
        return bool(changes)

    def _ensure_frame_count(self, count):
        return self._document.ensure_frame_count(count)
