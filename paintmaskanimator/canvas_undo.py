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

log = get_logger(__name__)


class UndoMixin(CanvasMembers):
    def document_snapshot(self): return self._document.snapshot()

    def push_doc_undo(self): self.undo_stack.append(("doc",self.document_snapshot())); self.undo_stack=self.undo_stack[-MAX_UNDO:]; self.redo_stack.clear()

    def push_layer_undo(self):
        l=self.active_layer; self.undo_stack.append(("layer",self.current_frame,self.active_layer_index,l.image.copy(),l.has_content)); self.undo_stack=self.undo_stack[-MAX_UNDO:]; self.redo_stack.clear()

    def push_layer_region_undo(self, rect):
        """Store only the part of the active layer an operation can change."""
        layer = self.active_layer
        rect = QRect(rect).intersected(layer.image.rect())
        if rect.isEmpty():
            return False
        self.undo_stack.append((
            "layer_region",
            int(self.current_frame),
            int(self.active_layer_index),
            rect,
            layer.image.copy(rect),
            bool(layer.has_content),
        ))
        self.undo_stack = self.undo_stack[-MAX_UNDO:]
        self.redo_stack.clear()
        return True

    def apply_undo_entry(self,e):
        if e[0] == "layer_tiles":
            _, fi, li, tiles, hc = e
            if (
                0 <= int(fi) < len(self.frames)
                and 0 <= int(li) < len(self.frames[int(fi)].layers)
            ):
                fi, li = int(fi), int(li)
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
                for rect, image in tiles:
                    painter.drawImage(QRect(rect).topLeft(), image)
                painter.end()
                layer.has_content = bool(hc)
                if display is not None:
                    for rect, _image in tiles:
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
        elif e[0] == "layer_region":
            _, fi, li, bbox, crop, hc = e
            if (
                0 <= int(fi) < len(self.frames)
                and 0 <= int(li) < len(self.frames[int(fi)].layers)
            ):
                fi, li = int(fi), int(li)
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
                painter.drawImage(QRect(bbox).topLeft(), crop)
                painter.end()
                layer.has_content = bool(hc)
                if display is not None:
                    self._patch_pseudo_transparent_display(
                        display,
                        layer.image,
                        QRect(bbox),
                    )
                    self._cache_pseudo_transparent_display(
                        layer.image,
                        display,
                    )
                self._stroke_display_image = None
                self._stroke_display_layer_index = -1
                self.cellChanged.emit(fi, li)
                self.selectionChanged.emit()
        elif e[0]=="doc":
            _,snap=e
            fs,cf,al,w,h=snap
            constants.CANVAS_WIDTH=w
            constants.CANVAS_HEIGHT=h
            # The entry was popped before application and its inverse has
            # already been captured. Transfer the stored snapshot directly;
            # cloning every frame/layer/image again only adds latency.
            self.frames=fs
            self.current_frame=cf
            self.active_layer_index=al
            self.coalesce_numbered_images()
            self.changed.emit()
        elif e[0]=="layer_batch":
            _, li, cells = e
            self.active_layer_index = li
            for fi, img, hc in cells:
                if 0 <= fi < len(self.frames) and 0 <= li < len(self.frames[fi].layers):
                    self.frames[fi].layers[li].image = img
                    self.frames[fi].layers[li].has_content = hc
                    self.cellChanged.emit(fi, li)
            self.selectionChanged.emit()
        elif e[0] == "layer_remove":
            _, index, target_active = e
            for frame in self.frames:
                if 0 <= index < len(frame.layers) and len(frame.layers) > 1:
                    frame.layers.pop(index)
            self.active_layer_index = max(
                0,
                min(int(target_active), len(self.layers) - 1),
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        elif e[0] == "layer_insert":
            _, index, stored_layers, target_active = e
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
                min(int(target_active), len(self.layers) - 1),
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        elif e[0] == "tween_batch":
            _, li, restore_count, start, end, cells = e
            restore_count = max(1, int(restore_count))
            if len(self.frames) < restore_count:
                self._ensure_frame_count(restore_count)
            for fi, image, has_content, exposure in cells:
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
                0, min(int(start), len(self.frames) - 1)
            )
            self.active_layer_index = max(
                0, min(int(li), len(self.layers) - 1)
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        else:
            _,fi,li,img,hc=e
            self.current_frame=fi
            self.active_layer_index=li
            self.frames[fi].layers[li].image=img
            self.frames[fi].layers[li].has_content=hc
            self.cellChanged.emit(fi,li)
            self.selectionChanged.emit()
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
