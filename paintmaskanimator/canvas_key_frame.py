"""Key-frame creation / navigation / resolution for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods create blank keys, step to
the previous/next key, resolve which key backs a given exposure cell, and ensure
the active cell is an editable key before painting. They run against a live
``PaintCanvas`` instance.
"""
from .canvas_changes import publish_editable_cursor, publish_editable_position, publish_frame_changes
# (nothing was used)
from ._canvas_members import CanvasMembers
from .logging_setup import get_logger

log = get_logger(__name__)


class KeyFrameMixin(CanvasMembers):
    def create_blank_key(self, column=None, layer_index=None):
        column = self.current_frame if column is None else max(0, int(column))
        layer_index = self.active_layer_index if layer_index is None else int(layer_index)
        if not self.frames or not 0 <= layer_index < len(self.frames[0].layers):
            return False
        self.push_doc_undo()
        changes = self._document.create_blank_key(column, layer_index)
        publish_frame_changes(self, changes)
        return bool(changes)

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

    def ensure_editable_key(self):
        """保持セルでは露出元を編集し、未使用／○は内容キーへ変換する。"""
        self._commit_brush_stroke()
        cursor = self._document.editable_cursor(self.current_frame, self.active_layer_index)
        publish_editable_cursor(self, cursor)
        layer = self.active_layer
        converted = not layer.has_content or layer.is_blank_key
        if not layer.has_content and self._playback_active:
            # Pixel undo is recorded after conversion, so capture the sheet first.
            self.push_doc_undo()
        changes = self._document.make_position_editable(
            self.current_frame, self.active_layer_index, self.timeline_mode,
        )
        publish_editable_position(self, changes)
        return converted and bool(changes)
