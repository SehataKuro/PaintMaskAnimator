"""Brush-stroke bookkeeping for PaintCanvas.

Split out of ``canvas.py`` as a mixin. Brush stamps blend directly into the
live layer image (the display path returns ``layer.image`` unchanged), so there
is no separate display buffer. What remains here is the stroke lifecycle: the
per-stroke tile snapshot that backs ``LayerTilesUndo`` and the brush-runtime
warm-up that avoids first-stroke jank. They run against a live ``PaintCanvas``.
"""
import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from ._canvas_members import CanvasMembers
from . import imaging
from .logging_setup import get_logger
from .undo_entries import LayerTilesUndo

log = get_logger(__name__)


class StrokeDisplayMixin(CanvasMembers):
    def _begin_opaque_brush_stroke(self):
        """Start a stroke without allocating or copying a full-layer image."""
        self._stroke_before_tiles = {}
        self._stroke_dirty_rect = QRect()
        self._stroke_undo_frame = int(self.current_frame)
        self._stroke_undo_layer = int(self.active_layer_index)
        self._stroke_prev_has_content = bool(self.active_layer.has_content)
        self._brush_blended_colors = set()

    def _stroke_before_region(self, rect):
        """Assemble a small immutable pre-stroke crop from saved tiles."""
        rect = QRect(rect)
        tiles = self._stroke_before_tiles
        if tiles is None or rect.isEmpty():
            return QImage()
        result = QImage(
            rect.width(),
            rect.height(),
            self.active_layer.image.format(),
        )
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Source
        )
        tile_size = 256
        for tile_y in range(
            rect.top() // tile_size,
            rect.bottom() // tile_size + 1,
        ):
            for tile_x in range(
                rect.left() // tile_size,
                rect.right() // tile_size + 1,
            ):
                saved = tiles.get((tile_x, tile_y))
                if saved is None:
                    continue
                tile_rect, tile_image = saved
                overlap = QRect(tile_rect).intersected(rect)
                if overlap.isEmpty():
                    continue
                source = QRect(
                    overlap.x() - tile_rect.x(),
                    overlap.y() - tile_rect.y(),
                    overlap.width(),
                    overlap.height(),
                )
                painter.drawImage(
                    QPoint(overlap.x() - rect.x(), overlap.y() - rect.y()),
                    tile_image,
                    source,
                )
        painter.end()
        return result

    def _finish_opaque_brush_stroke(self):
        colors = tuple(
            getattr(self, "_brush_blended_colors", set())
        )
        self._emit_actual_paint_colors(colors)
        if (
            self._stroke_before_tiles is not None
            and self._stroke_dirty_rect is not None
            and not self._stroke_dirty_rect.isEmpty()
        ):
            tiles = tuple(
                (QRect(rect), image)
                for rect, image in self._stroke_before_tiles.values()
            )
            self.push_undo(LayerTilesUndo(
                int(self._stroke_undo_frame),
                int(self._stroke_undo_layer),
                tiles,
                bool(self._stroke_prev_has_content),
            ))
        self._stroke_before_tiles = None
        self._stroke_dirty_rect = None
        self._brush_blended_colors = set()

    def warm_up_brush_runtime(self):
        """Prime Qt/numpy brush primitives without touching the document."""
        if self._brush_runtime_warmed:
            return

        scratch = QImage(
            64,
            64,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        scratch.fill(Qt.GlobalColor.transparent)
        painter = QPainter(scratch)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            False,
        )
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver
        )
        painter.setPen(QPen(
            QColor(32, 64, 96, 255),
            8.0,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        ))
        painter.drawLine(QPointF(12, 12), QPointF(52, 44))
        painter.end()

        # Exercise the same format conversion and ndarray view used by the
        # first real stamp.  Keep the scratch image local so no display cache,
        # layer pixels, or undo state is affected.
        rgba = scratch.convertToFormat(QImage.Format.Format_RGBA8888)
        ptr = imaging.qimage_buffer(rgba)
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (rgba.height(), rgba.bytesPerLine())
        )
        _ = rows[:, : rgba.width() * 4].reshape(
            (rgba.height(), rgba.width(), 4)
        )[:, :, 3].max()
        rgba.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        self.pressure_size_scale(1.0)
        self._brush_runtime_warmed = True
