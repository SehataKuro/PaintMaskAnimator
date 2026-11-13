"""Key-frame creation / navigation / resolution for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods create blank keys, step to
the previous/next key, resolve which key backs a given exposure cell, and ensure
the active cell is an editable key before painting. They run against a live
``PaintCanvas`` instance.
"""
from .document import clear_pending
# (nothing was used)
from ._canvas_members import CanvasMembers
from .utils import blank_image
from .logging_setup import get_logger

log = get_logger(__name__)


class KeyFrameMixin(CanvasMembers):
    def create_blank_key(
        self,
        column=None,
        layer_index=None,
    ):
        """○を作成する。

        開始セル（●／○）を選択した場合：
            元のコマを残し、露出末尾の次へ同じ長さの○を挿入する。

        露出途中（ー／│）を選択した場合：
            全体の長さを変えず、選択位置から後半を○へ分割する。
        """
        if column is None:
            column = self.current_frame
        if layer_index is None:
            layer_index = self.active_layer_index

        column = max(0, int(column))
        layer_index = int(layer_index)
        if not self.frames:
            return False
        if not (
            0 <= layer_index
            < len(self.frames[0].layers)
        ):
            return False

        self.push_doc_undo()
        self._ensure_frame_count(column + 1)

        block = self.timeline_block_at(
            column,
            layer_index,
        )
        blank_exposure = 1

        if block is not None:
            _kind, start, exposure = block
            start = int(start)
            exposure = max(1, int(exposure))
            source = self.frames[
                start
            ].layers[layer_index]
            template = source.clone()

            if column == start:
                # ●／○の開始セルを選択：
                # 元のコマを保持し、❘の次へ同じ長さの○を挿入する。
                insertion_column = start + exposure
                blank_exposure = exposure
                self._shift_timeline_layer_right(
                    layer_index,
                    insertion_column,
                    blank_exposure,
                )
                self._ensure_frame_count(
                    insertion_column + blank_exposure
                )
                column = insertion_column
            else:
                # ー／│を選択：
                # 露出全体の長さは変えず、選択位置で前後に分割する。
                #
                # 例：
                # ●ーーーーー│
                #       ↓
                # ●ーー○ーー│
                split_offset = max(
                    1,
                    column - start,
                )
                split_offset = min(
                    split_offset,
                    exposure - 1,
                )
                blank_exposure = max(
                    1,
                    exposure - split_offset,
                )
                column = start + split_offset
                self._ensure_frame_count(
                    column + blank_exposure
                )
        else:
            # 未使用セル上では、その位置へ○を作る。
            # 直前の明示コマがある場合は、○の直前まで露出を伸ばす。
            self._ensure_frame_count(column + 1)
            target = self.frames[
                column
            ].layers[layer_index]
            template = target.clone()

            previous_start = None
            for candidate in range(
                column - 1,
                -1,
                -1,
            ):
                layer = self.frames[
                    candidate
                ].layers[layer_index]
                if (
                    layer.has_content
                    or getattr(
                        layer,
                        "is_blank_key",
                        False,
                    )
                ):
                    previous_start = candidate
                    template = layer.clone()
                    break

            if previous_start is not None:
                previous = self.frames[
                    previous_start
                ].layers[layer_index]
                for index in range(previous_start + 1, column):
                    self.frames[index].layers[layer_index] = previous.clone_reference()

        target = self.frames[
            column
        ].layers[layer_index]
        # The blank position owns local v1 residue; it must not erase a bank Cell.
        target.detach_cell()
        target.has_content = False
        clear_pending(target)
        target.image = blank_image()
        target.visible = bool(template.visible)
        target.opacity = float(template.opacity)
        target.alpha_locked = bool(
            template.alpha_locked
        )
        target.color_filter_enabled = bool(
            template.color_filter_enabled
        )
        target.color_filter_rgb = (
            tuple(template.color_filter_rgb)
            if template.color_filter_rgb
            is not None
            else None
        )
        target.has_content = False
        target.is_blank_key = True
        target.blank_start = True
        target.__dict__.pop("_v1_position", None)
        for index in range(column + 1, column + blank_exposure):
            self.frames[index].layers[layer_index] = target.clone_reference()

        self.current_frame = column
        self.active_layer_index = layer_index
        self._cell_structure_dirty = True
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def previous_key_frame(self):
        layer_index = self.active_layer_index
        keys = self.timeline_step_columns(layer_index)
        previous = [i for i in keys if i < self.current_frame]
        if previous:
            self.current_frame = previous[-1]
        self.selectionChanged.emit()
        self.update()

    def next_key_frame(self):
        layer_index = self.active_layer_index
        keys = self.timeline_step_columns(layer_index)
        following = [i for i in keys if i > self.current_frame]
        if following:
            self.current_frame = following[0]
        self.selectionChanged.emit()
        self.update()

    def select_exposure(self, column, visual_row):
        self.current_frame = max(0, min(int(column), len(self.frames) - 1))
        layer_count = len(self.layers)
        if layer_count:
            visual_row = max(0, min(int(visual_row), layer_count - 1))
            self.active_layer_index = layer_count - 1 - visual_row
        self.selectionChanged.emit()
        self.update()

    def resolve_key_frame(self, column, layer_index):
        if not self.frames:
            return None
        from .models import Layer
        from .timeline_resolution import resolve_exposures

        column = max(0, min(int(column), len(self.frames) - 1))
        signature = (id(self.frames), len(self.frames), Layer.timeline_revision)
        if getattr(self, "_exposure_index_signature", None) != signature:
            self._exposure_index = resolve_exposures(self.frames)
            self._exposure_index_signature = signature
        if not 0 <= layer_index < self._exposure_index.shape[1]:
            return None
        key = int(self._exposure_index[column, layer_index])
        return key if key >= 0 else None

    def _next_drawing_number(self, layer_index):
        existing_numbers = [
            int(seq)
            for frame in self.frames
            if (frame.layers[layer_index].has_content
                and (seq := frame.layers[layer_index].sequence_number) is not None)
        ]
        existing_numbers.extend(self.frames[0].layers[layer_index]._track.cells)
        return max(existing_numbers, default=0) + 1

    def ensure_editable_key(self):
        """保持セルでは露出元を編集し、未使用／○は内容キーへ変換する。"""
        block = self.timeline_block_at(
            self.current_frame, self.active_layer_index,
        )
        if block is not None and self.current_frame > block[1]:
            # 保持区間は同じセル。編集対象とUndoの保存先を露出元へ揃え、
            # 加筆のたびに別のセルや番号が作られるのを防ぐ。
            old = self.active_layer.__dict__.get("_v1_position")
            was_content_key = old is not None and old.key_start and old.legacy is not None and old.legacy.has_content
            self.current_frame = int(block[1])
            if not was_content_key:
                self.selectionChanged.emit()
        layer = self.active_layer
        if layer.has_content:
            if (layer.sequence_number is None and self.timeline_mode == "sheet"
                    and not layer.is_paper and not layer.is_draft):
                number = self._next_drawing_number(self.active_layer_index)
                if layer._track.connected:
                    layer._track.cells[number] = layer._cell
                layer.sequence_number = number
                if layer._track.connected:
                    layer.__dict__.pop("_cell", None)
                self._cell_structure_dirty = True
            if layer.is_blank_key:
                # 連番の空セルへ描画した旧データでは両方のフラグが
                # Trueになり得る。内容キーとして即時修復する。
                layer.is_blank_key = False
                clear_pending(layer)
                self._cell_structure_dirty = True
                return True
            return False

        # Painting during playback can turn an unused/blank cell into a key.
        # Pixel undo is captured after this conversion, so retain the timeline
        # state separately to make the cell creation itself reversible too.
        if self._playback_active:
            self.push_doc_undo()

        current = int(self.current_frame)
        layer_index = int(self.active_layer_index)
        block = self.timeline_block_at(current, layer_index)
        retained_number = layer.sequence_number
        next_number = self._next_drawing_number(layer_index)
        if layer._track.connected:
            # A duplicated empty slot may share its local serialization residue.
            # The new drawing must not turn that other slot's PNG into this one.
            layer.detach_cell()

        if block is not None:
            kind, start, exposure = block
            source_layer = self.frames[start].layers[layer_index]
            copied = source_layer.clone()

            layer.visible = bool(copied.visible)
            layer.opacity = float(copied.opacity)
            layer.alpha_locked = bool(copied.alpha_locked)
            layer.color_filter_enabled = bool(
                copied.color_filter_enabled
            )
            layer.color_filter_rgb = (
                tuple(copied.color_filter_rgb)
                if copied.color_filter_rgb is not None
                else None
            )
            layer.image = (
                copied.image.copy()
                if kind == "content"
                else blank_image()
            )
            layer.blank_start = False
        else:
            # An uncreated cell already owns a transparent image.  Keep it
            # instead of allocating and clearing another full-canvas image at
            # the instant the first stroke begins.
            layer.blank_start = False

        if layer._track.connected:
            # A retained number is serialization residue, never an edit target.
            if retained_number is not None:
                layer.sequence_number = None
                layer.image = blank_image()
            layer._track.cells[next_number] = layer._cell
            layer.sequence_number = next_number
        elif self.timeline_mode == "sheet":
            layer.sequence_number = int(retained_number) if retained_number is not None else next_number
        layer.has_content = True
        if layer._track.connected:
            layer.__dict__.pop("_cell", None)
        layer.is_blank_key = False
        if block is not None and block[0] == "blank":
            for position in range(current + 1, block[1] + block[2]):
                slot = self.frames[position].layers[layer_index]
                slot.sequence_number = layer.sequence_number
                slot.has_content = True
                slot.is_blank_key = False
                slot.blank_start = False
        self._cell_structure_dirty = True
        return True
