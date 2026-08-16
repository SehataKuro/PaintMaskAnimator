"""Key-frame creation / navigation / resolution for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods create blank keys, step to
the previous/next key, resolve which key backs a given exposure cell, and ensure
the active cell is an editable key before painting. They run against a live
``PaintCanvas`` instance.
"""
from .common import *  # noqa: F401,F403
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
                source.exposure = max(
                    1,
                    split_offset,
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
                previous.exposure = max(
                    1,
                    column - previous_start,
                )

        target = self.frames[
            column
        ].layers[layer_index]
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
        target.exposure = blank_exposure

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
        elif keys:
            self.current_frame = keys[-1]
        self.selectionChanged.emit()
        self.update()

    def next_key_frame(self):
        layer_index = self.active_layer_index
        keys = self.timeline_step_columns(layer_index)
        following = [i for i in keys if i > self.current_frame]
        if following:
            self.current_frame = following[0]
        elif keys:
            self.current_frame = keys[0]
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
        column = max(0, min(int(column), len(self.frames) - 1))
        for key_col in range(column, -1, -1):
            if layer_index >= len(self.frames[key_col].layers):
                continue
            layer = self.frames[key_col].layers[layer_index]
            if layer.has_content:
                return (
                    key_col
                    if column < key_col + max(1, layer.exposure)
                    else None
                )
            if getattr(layer, "is_blank_key", False):
                return None
        return None

    def ensure_editable_key(self):
        """未使用／○／保持セルを独立した●キーフレームへ変換する。"""
        self._editable_key_was_blank = False
        layer = self.active_layer
        if layer.has_content:
            if layer.sequence_number is not None:
                # 描画開始前から全参照を同じ画像へ結び、ストローク中も
                # 同番号の画像が分離しないようにする。
                self.sync_numbered_image_from_cell(
                    self.current_frame,
                    self.active_layer_index,
                )
            if layer.is_blank_key:
                # 連番の空セルへ描画した旧データでは両方のフラグが
                # Trueになり得る。内容キーとして即時修復する。
                layer.is_blank_key = False
                layer.sequence_only = False
                self._cell_structure_dirty = True
                return True
            return False

        current = int(self.current_frame)
        layer_index = int(self.active_layer_index)
        block = self.timeline_block_at(current, layer_index)
        retained_number = layer.sequence_number

        if block is not None:
            kind, start, exposure = block
            source_layer = self.frames[start].layers[layer_index]
            copied = source_layer.clone()
            old_end = start + exposure - 1

            if current > start:
                source_layer.exposure = max(1, current - start)

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
            self._editable_key_was_blank = kind != "content"
            layer.exposure = max(1, old_end - current + 1)
        else:
            # An uncreated cell already owns a transparent image.  Keep it
            # instead of allocating and clearing another full-canvas image at
            # the instant the first stroke begins.
            self._editable_key_was_blank = True
            layer.exposure = 1

        layer.has_content = True
        layer.is_blank_key = False
        if self.timeline_mode == "sheet":
            if retained_number is not None:
                layer.sequence_number = int(retained_number)
            else:
                existing_numbers = [
                    int(seq)
                    for frame in self.frames
                    if (
                        frame.layers[layer_index].has_content
                        and (seq := frame.layers[layer_index].sequence_number) is not None
                    )
                ]
                layer.sequence_number = max(existing_numbers, default=0) + 1
        self._cell_structure_dirty = True
        return True
