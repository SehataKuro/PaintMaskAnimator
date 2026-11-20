"""Brush-stroke bookkeeping for PaintCanvas.

Brush stamps blend into a stroke-local working image. Adjacent-color preview
keeps a stroke-local display image and updates only touched pixels. The mixin
also captures the pre-stroke tiles for ``LayerTilesUndo`` and warms up the brush
runtime.
"""
import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from ._canvas_members import CanvasMembers
from .pma_image import as_qimage
from . import imaging
from .logging_setup import get_logger
from .undo_entries import LayerTilesUndo

log = get_logger(__name__)


class StrokeDisplayMixin(CanvasMembers):
    def _begin_opaque_brush_stroke(self):
        """Start tile undo tracking and, when needed, an incremental preview."""
        self._commit_brush_stroke()
        target = self.active_layer.image
        self._stroke_target = target
        # Read metadata through a tiny independent crop. A non-unit DPR needs
        # the original painter coordinate system, so keep a full image then.
        work = target.read_qimage(0, 0, 1, 1)
        self._stroke_work_image = work
        self._stroke_work_rect = QRect()
        if work.devicePixelRatio() != 1 or self.view_preview.map_colors:
            self._expand_stroke_work(QRect(0, 0, target.width, target.height))
        self._stroke_before_tiles = {}
        self._stroke_dirty_rect = QRect()
        self._stroke_undo_frame = int(self.current_frame)
        self._stroke_undo_layer = int(self.active_layer_index)
        self._stroke_prev_has_content = bool(self.active_layer.has_content)
        self._brush_blended_colors = set()
        self._stroke_view_preview = None
        self._stroke_view_dirty = QRect()
        if self.view_preview.map_colors and self._layer_uses_raw_display(
            self.active_layer, self.active_layer_index,
        ):
            preview = self.view_layer_image(self.active_layer, self.active_layer_index, target)
            self._stroke_view_preview = (
                self.active_layer, self.view_preview.key(),
                as_qimage(preview).copy(),
            )

    def _expand_stroke_work(self, rect):
        """Read the expanded crop and carry over the pixels already painted."""
        previous = self._stroke_work_rect
        if previous.contains(rect):
            return self._stroke_work_image
        work = self._stroke_target.read_qimage(*rect.getRect())
        if not previous.isEmpty():
            painter = QPainter(work)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.drawImage(previous.topLeft() - rect.topLeft(), self._stroke_work_image)
            painter.end()
        self._stroke_work_image = work
        self._stroke_work_rect = rect
        return work

    def _grow_stroke_work(self, rect, bounds):
        previous = self._stroke_work_rect
        if previous.contains(rect):
            return
        expanded = previous.united(rect)
        # Leave headroom on advancing edges to amortize copies during motion.
        x_margin = max(64, previous.width() // 2)
        y_margin = max(64, previous.height() // 2)
        expanded.adjust(
            -x_margin if previous.isEmpty() or rect.left() < previous.left() else 0,
            -y_margin if previous.isEmpty() or rect.top() < previous.top() else 0,
            x_margin if previous.isEmpty() or rect.right() > previous.right() else 0,
            y_margin if previous.isEmpty() or rect.bottom() > previous.bottom() else 0,
        )
        self._expand_stroke_work(expanded.intersected(bounds))

    def _stroke_before_region(self, rect):
        """Assemble a small immutable pre-stroke crop from saved tiles."""
        rect = QRect(rect)
        tiles = self._stroke_before_tiles
        if tiles is None or rect.isEmpty():
            return QImage()
        result = QImage(
            rect.width(),
            rect.height(),
            self._stroke_work_image.format(),
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
        work = self._stroke_work_image
        if work is None:
            return
        self._stop_brush_follow_timer()
        self._reset_brush_stabilizer()
        self.drawing = False
        frame, index = self._stroke_undo_frame, self._stroke_undo_layer
        layer = self.frames[frame].layers[index]
        dirty = self._stroke_dirty_rect
        colors = tuple(self._brush_blended_colors)
        entry = None
        if not dirty.isEmpty():
            local = dirty.translated(-self._stroke_work_rect.topLeft())
            patch = work if local == work.rect() else work.copy(local)
            self._stroke_target.write_qimage(patch, dirty.x(), dirty.y())
            layer.has_content = True
            entry = LayerTilesUndo(
                frame, index, tuple(self._stroke_before_tiles.values()),
                self._stroke_prev_has_content,
                layer_name=layer.name, number=layer.sequence_number,
            )
        self._stroke_target = None
        self._stroke_work_image = None
        self._stroke_work_rect = QRect()
        self._stroke_before_tiles = None
        self._stroke_dirty_rect = None
        self._stroke_view_preview = None
        self._stroke_view_dirty = QRect()
        self._brush_blended_colors = set()
        self._emit_actual_paint_colors(colors)
        if entry is not None:
            self.push_undo(entry)

    def _commit_brush_stroke(self):
        """Finish an input stroke and notify the canvas observers."""
        if self._stroke_work_image is None:
            return
        frame, index = self._stroke_undo_frame, self._stroke_undo_layer
        self._finish_opaque_brush_stroke()
        layer = self.frames[frame].layers[index]
        self._cell_structure_dirty = (
            not self._brush_started_with_content and bool(layer.has_content)
        )
        self.cellChanged.emit(frame, index)
        self.update()

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
