"""Undo/redo stacks for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods push document / layer /
region snapshots onto the undo stack and apply them on undo/redo. They run
against a live ``PaintCanvas`` instance and its ``_document`` + stacks.
"""
from .common import *  # noqa: F401,F403
from ._canvas_members import CanvasMembers
from . import constants
from .models import Layer
from .utils import blank_image
from .logging_setup import get_logger
from .undo_entries import (
    DocUndo,
    LayerBatchUndo,
    LayerInsertUndo,
    LayerRegionUndo,
    LayerRemoveUndo,
    LayerTilesUndo,
    LayerUndo,
    PaletteStateUndo,
    TweenBatchUndo,
    history_label_for,
)

log = get_logger(__name__)

__all__ = ["UndoMixin", "history_label_for", "trim_undo_stack"]


def trim_undo_stack(stack):
    """Drop the oldest entries until the stack fits both undo limits.

    Bounded by entry count *and* retained image bytes, whichever binds first;
    at least one entry always survives so a single huge edit stays undoable.
    """
    if len(stack) > MAX_UNDO:
        del stack[:-MAX_UNDO]
    total = sum(entry.nbytes for entry in stack)
    while len(stack) > 1 and total > MAX_UNDO_BYTES:
        total -= stack[0].nbytes
        del stack[0]
    return stack


class UndoMixin(CanvasMembers):
    def document_snapshot(self): return self._document.snapshot()

    def push_undo(self, entry):
        """Push one undo entry, trim to the limits, and invalidate redo.

        Every producer goes through here so the trim/clear pair cannot be
        forgotten at a new call site.
        """
        self.undo_stack.append(entry)
        trim_undo_stack(self.undo_stack)
        self.redo_stack.clear()
        return entry

    def push_doc_undo(self):
        self.push_undo(DocUndo(self.document_snapshot()))

    def push_layer_undo(self):
        layer = self.active_layer
        self.push_undo(LayerUndo(
            int(self.current_frame),
            int(self.active_layer_index),
            layer.image.copy(),
            bool(layer.has_content),
        ))

    def push_layer_region_undo(self, rect):
        """Store only the part of the active layer an operation can change."""
        layer = self.active_layer
        rect = QRect(rect).intersected(layer.image.rect())
        if rect.isEmpty():
            return False
        self.push_undo(LayerRegionUndo(
            int(self.current_frame),
            int(self.active_layer_index),
            rect,
            layer.image.copy(rect),
            bool(layer.has_content),
        ))
        return True

    def apply_undo_entry(self, e):
        if isinstance(e, LayerTilesUndo):
            if (
                0 <= e.frame < len(self.frames)
                and 0 <= e.layer < len(self.frames[e.frame].layers)
            ):
                fi, li = e.frame, e.layer
                self.current_frame = fi
                self.active_layer_index = li
                layer = self.frames[fi].layers[li]
                old_display_key = self._pseudo_transparency_key(layer.image)
                display = self._pseudo_transparency_cache.pop(
                    old_display_key,
                    None,
                )
                painter = QPainter(layer.image)
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Source
                )
                for rect, image in e.tiles:
                    painter.drawImage(QRect(rect).topLeft(), image)
                painter.end()
                layer.has_content = bool(e.has_content)
                if display is not None:
                    for rect, _image in e.tiles:
                        self._patch_pseudo_transparent_display(
                            display,
                            layer.image,
                            QRect(rect),
                        )
                    self._cache_pseudo_transparent_display(
                        layer.image,
                        display,
                    )
                self._stroke_display_image = None
                self._stroke_display_layer_index = -1
                self.cellChanged.emit(fi, li)
                self.selectionChanged.emit()
        elif isinstance(e, LayerRegionUndo):
            if (
                0 <= e.frame < len(self.frames)
                and 0 <= e.layer < len(self.frames[e.frame].layers)
            ):
                fi, li = e.frame, e.layer
                self.current_frame = fi
                self.active_layer_index = li
                layer = self.frames[fi].layers[li]
                old_display_key = self._pseudo_transparency_key(layer.image)
                display = self._pseudo_transparency_cache.pop(
                    old_display_key,
                    None,
                )
                painter = QPainter(layer.image)
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Source
                )
                painter.drawImage(QRect(e.rect).topLeft(), e.image)
                painter.end()
                layer.has_content = bool(e.has_content)
                if display is not None:
                    self._patch_pseudo_transparent_display(
                        display,
                        layer.image,
                        QRect(e.rect),
                    )
                    self._cache_pseudo_transparent_display(
                        layer.image,
                        display,
                    )
                self._stroke_display_image = None
                self._stroke_display_layer_index = -1
                self.cellChanged.emit(fi, li)
                self.selectionChanged.emit()
        elif isinstance(e, PaletteStateUndo):
            # 使用色パネルの並び順・親子プレビュー・表示/マスクを復元する。
            palette = getattr(self, "_palette", None)
            if palette is not None:
                palette.restore_history_state(e.state)
        elif isinstance(e, DocUndo):
            fs, cf, al, w, h = e.snapshot
            constants.CANVAS_WIDTH = w
            constants.CANVAS_HEIGHT = h
            # The entry was popped before application and its inverse has
            # already been captured. Transfer the stored snapshot directly;
            # cloning every frame/layer/image again only adds latency.
            self.frames = fs
            self.current_frame = cf
            self.active_layer_index = al
            self.coalesce_numbered_images()
            self.changed.emit()
        elif isinstance(e, LayerBatchUndo):
            li = e.layer
            self.active_layer_index = li
            for fi, img, hc in e.cells:
                if 0 <= fi < len(self.frames) and 0 <= li < len(self.frames[fi].layers):
                    self.frames[fi].layers[li].image = img
                    self.frames[fi].layers[li].has_content = hc
                    self.cellChanged.emit(fi, li)
            self.selectionChanged.emit()
        elif isinstance(e, LayerRemoveUndo):
            index = e.index
            for frame in self.frames:
                if 0 <= index < len(frame.layers) and len(frame.layers) > 1:
                    frame.layers.pop(index)
            self.active_layer_index = max(
                0,
                min(int(e.target_active), len(self.layers) - 1),
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        elif isinstance(e, LayerInsertUndo):
            index = e.index
            stored_layers = e.layers
            for frame_index, frame in enumerate(self.frames):
                if frame_index < len(stored_layers):
                    layer = stored_layers[frame_index]
                else:
                    layer = Layer(
                        f"Layer {index + 1}",
                        blank_image(),
                        visible=True,
                        opacity=1.0,
                    )
                frame.layers.insert(
                    max(0, min(int(index), len(frame.layers))),
                    layer,
                )
            self.active_layer_index = max(
                0,
                min(int(e.target_active), len(self.layers) - 1),
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        elif isinstance(e, TweenBatchUndo):
            li = e.layer
            restore_count = max(1, int(e.restore_count))
            if len(self.frames) < restore_count:
                self._ensure_frame_count(restore_count)
            for fi, image, has_content, exposure in e.cells:
                if (
                    0 <= fi < len(self.frames)
                    and 0 <= li < len(self.frames[fi].layers)
                ):
                    layer = self.frames[fi].layers[li]
                    layer.image = image
                    layer.has_content = bool(has_content)
                    layer.exposure = max(1, int(exposure))
            if len(self.frames) > restore_count:
                del self.frames[restore_count:]
            self.current_frame = max(
                0, min(int(e.start), len(self.frames) - 1)
            )
            self.active_layer_index = max(
                0, min(int(li), len(self.layers) - 1)
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        elif isinstance(e, LayerUndo):
            fi, li = e.frame, e.layer
            self.current_frame = fi
            self.active_layer_index = li
            self.frames[fi].layers[li].image = e.image
            self.frames[fi].layers[li].has_content = e.has_content
            self.cellChanged.emit(fi, li)
            self.selectionChanged.emit()
        else:
            log.warning("unknown undo entry type: %r", type(e).__name__)
            return
        self.update()

    def undo(self):
        while self.undo_stack:
            entry = self.undo_stack.pop()
            current = self.current_state_for(entry)
            if current is None:
                # タイムラインの削除・正規化後に残った、既に存在しない
                # セルの古い履歴は安全に破棄する。
                continue
            self.redo_stack.append(current)
            self.apply_undo_entry(entry)
            return

    def redo(self):
        while self.redo_stack:
            entry = self.redo_stack.pop()
            current = self.current_state_for(entry)
            if current is None:
                continue
            self.undo_stack.append(current)
            self.apply_undo_entry(entry)
            return
