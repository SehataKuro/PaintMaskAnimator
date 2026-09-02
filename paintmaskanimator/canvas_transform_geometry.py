"""Free-transform geometry for PaintCanvas: handles, drag state and preview.

Split out of ``canvas.py`` as a mixin, and a companion to
``canvas_transform_mask.py`` (which owns the mask side). These methods answer the
geometric questions of a transform: where the handles and rotation grip are, what
polygon the transform currently covers, how a drag updates the corner points, and
how the source image is projected -- including the reduced-size proxy used while
dragging. They run against a live ``PaintCanvas``.
"""
import math
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPolygonF
from .constants import TP_MASK_PROXY_MAX_DIMENSION
from ._canvas_members import CanvasMembers
from . import geometry
from .logging_setup import get_logger

log = get_logger(__name__)


class TransformGeometryMixin(CanvasMembers):
    @staticmethod
    def _regular_grid_points(rect, cols, rows=None):
        return geometry.regular_grid_points(rect, cols, rows)


    def nearest_transform_handle(self, point):
        if not self.transform_active or not self.transform_points:
            return -1
        radius = 14 / max(self.zoom, 0.01)
        distances = [
            (handle.x() - point.x()) ** 2 + (handle.y() - point.y()) ** 2
            for handle in self.transform_points
        ]
        index = int(np.argmin(distances))
        return index if distances[index] <= radius * radius else -1

    def transform_center(self):
        if not self.transform_points:
            return QPointF()
        return QPointF(
            sum(point.x() for point in self.transform_points) / len(self.transform_points),
            sum(point.y() for point in self.transform_points) / len(self.transform_points),
        )

    def transform_rotation_handle(self):
        if not self.transform_points:
            return None
        if self.transform_mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            if len(self.transform_points) < cols:
                return None
            p0, p1 = self.transform_points[0], self.transform_points[cols - 1]
        elif len(self.transform_points) == 4:
            p0, p1 = self.transform_points[0], self.transform_points[1]
        else:
            return None
        midpoint = QPointF((p0.x() + p1.x()) / 2.0, (p0.y() + p1.y()) / 2.0)
        center = self.transform_center()
        dx, dy = midpoint.x() - center.x(), midpoint.y() - center.y()
        length = math.hypot(dx, dy) or 1.0
        distance = 36.0 / max(self.zoom, 0.01)
        return QPointF(
            midpoint.x() + dx / length * distance,
            midpoint.y() + dy / length * distance,
        )

    def transform_outer_polygon(self):
        if not self.transform_points:
            return QPolygonF()
        if self.transform_mode != "mesh":
            return QPolygonF(self.transform_points[:4])
        cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
        rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
        points = self.transform_points
        if len(points) != cols * rows:
            return QPolygonF()
        subdivisions = 8
        outline = []

        for step in range((cols - 1) * subdivisions + 1):
            outline.append(
                self._mesh_curve_point(
                    points, cols, rows,
                    step / float(subdivisions), 0.0,
                )
            )
        for step in range(1, (rows - 1) * subdivisions + 1):
            outline.append(
                self._mesh_curve_point(
                    points, cols, rows,
                    float(cols - 1),
                    step / float(subdivisions),
                )
            )
        for step in range(
            (cols - 1) * subdivisions - 1, -1, -1
        ):
            outline.append(
                self._mesh_curve_point(
                    points, cols, rows,
                    step / float(subdivisions),
                    float(rows - 1),
                )
            )
        for step in range(
            (rows - 1) * subdivisions - 1, 0, -1
        ):
            outline.append(
                self._mesh_curve_point(
                    points, cols, rows,
                    0.0,
                    step / float(subdivisions),
                )
            )
        return QPolygonF(outline)


    def begin_transform_drag(self, point):
        if not self.transform_active:
            return False
        rotation_handle = self.transform_rotation_handle()
        radius = 16 / max(self.zoom, 0.01)
        if rotation_handle is not None:
            if (
                (rotation_handle.x() - point.x()) ** 2
                + (rotation_handle.y() - point.y()) ** 2
                <= radius * radius
            ):
                self.transform_drag_kind = ("rotate", -1)
                self.transform_drag_start = QPointF(point)
                self.transform_drag_points = [QPointF(p) for p in self.transform_points]
                return True
        handle = self.nearest_transform_handle(point)
        if handle >= 0:
            self.transform_drag_kind = ("handle", handle)
            self.transform_drag_start = QPointF(point)
            self.transform_drag_points = [QPointF(p) for p in self.transform_points]
            self.transform_handle = handle
            return True
        if self.transform_outer_polygon().containsPoint(
            point, Qt.FillRule.OddEvenFill
        ):
            self.transform_drag_kind = ("move", -1)
            self.transform_drag_start = QPointF(point)
            self.transform_drag_points = [QPointF(p) for p in self.transform_points]
            return True
        self.transform_drag_kind = None
        return False

    def update_transform_drag(self, point):
        if not self.transform_drag_kind:
            return
        kind, index = self.transform_drag_kind
        start_points = self.transform_drag_points
        if kind == "move":
            delta = point - self.transform_drag_start
            self.transform_points = [p + delta for p in start_points]
        elif kind == "rotate":
            center = QPointF(
                sum(p.x() for p in start_points) / len(start_points),
                sum(p.y() for p in start_points) / len(start_points),
            )
            a0 = math.atan2(
                self.transform_drag_start.y() - center.y(),
                self.transform_drag_start.x() - center.x(),
            )
            a1 = math.atan2(point.y() - center.y(), point.x() - center.x())
            angle = a1 - a0
            cs, sn = math.cos(angle), math.sin(angle)
            self.transform_points = [
                QPointF(
                    center.x() + (p.x() - center.x()) * cs - (p.y() - center.y()) * sn,
                    center.y() + (p.x() - center.x()) * sn + (p.y() - center.y()) * cs,
                )
                for p in start_points
            ]
        elif kind == "handle":
            if self.transform_mode in ("free", "mesh"):
                self.transform_points[index] = QPointF(point)
            else:
                opposite_index = (index + 2) % 4
                opposite = start_points[opposite_index]
                original = start_points[index] - opposite
                current = point - opposite
                denominator = original.x() ** 2 + original.y() ** 2
                scale = (
                    (current.x() * original.x() + current.y() * original.y())
                    / denominator
                    if denominator > 1e-8 else 1.0
                )
                if abs(scale) < 0.02:
                    scale = 0.02 if scale >= 0 else -0.02
                self.transform_points = [
                    opposite + (p - opposite) * scale
                    for p in start_points
                ]
        self._invalidate_tp_preview_cache()
        self.update()

    def end_transform_drag(self):
        self.transform_drag_kind = None
        self.transform_handle = -1
        self.transform_drag_points = []
        if self.transform_quality_active:
            self.request_quality_preview_counter(
                "変形後のクオリティプレビューを生成しています"
            )
        self.update()


    @staticmethod
    def _quad_homography(source_points, target_points):
        return geometry.quad_homography(source_points, target_points)


    # Pure QImage/NumPy/PIL conversions now live in imaging.py; these wrappers
    # keep the existing call sites (self._.../cls._...) working unchanged.
    def _project_transform_source(
        self, source, target_width, target_height, smooth=False
    ):
        canvas_rect = QRectF(0, 0, target_width, target_height)
        if self.transform_mode == "mesh":
            return (
                self._mesh_preview_image(
                    source, target_width, target_height, smooth=smooth
                ),
                canvas_rect,
            )
        if len(self.transform_points) != 4:
            return None, canvas_rect
        width, height = source.width(), source.height()
        if width <= 0 or height <= 0:
            return None, canvas_rect
        source_quad = [
            QPointF(0, 0), QPointF(width, 0),
            QPointF(width, height), QPointF(0, height),
        ]
        transform = self._quad_homography(source_quad, self.transform_points)
        preview = QImage(
            target_width,
            target_height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        preview.fill(Qt.GlobalColor.transparent)
        painter = QPainter(preview)
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform, False
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setTransform(transform)
        painter.drawImage(0, 0, source)
        painter.end()
        return preview, canvas_rect

    def _transform_is_reducing(self, source=None):
        """変形のどこかに縮小があり、補間が必要かを返す。"""
        source = source if source is not None else self.transform_source
        if source is None or source.isNull() or not self.transform_points:
            return False

        if self.transform_mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
            reference = self._mesh_reference_grid(cols, rows)
            if (
                len(self.transform_points) != cols * rows
                or len(reference) != cols * rows
            ):
                return False
            pairs = []
            for row in range(rows):
                for col in range(cols - 1):
                    index = row * cols + col
                    pairs.append((index, index + 1))
            for row in range(rows - 1):
                for col in range(cols):
                    index = row * cols + col
                    pairs.append((index, index + cols))
            for first, second in pairs:
                current = math.hypot(
                    self.transform_points[second].x()
                    - self.transform_points[first].x(),
                    self.transform_points[second].y()
                    - self.transform_points[first].y(),
                )
                original = math.hypot(
                    reference[second].x() - reference[first].x(),
                    reference[second].y() - reference[first].y(),
                )
                if original > 1e-8 and current < original * 0.9999:
                    return True
            return False

        if len(self.transform_points) != 4:
            return False
        source_width = max(1.0, float(source.width()))
        source_height = max(1.0, float(source.height()))
        p0, p1, p2, p3 = self.transform_points[:4]
        horizontal_edges = (
            math.hypot(p1.x() - p0.x(), p1.y() - p0.y()),
            math.hypot(p2.x() - p3.x(), p2.y() - p3.y()),
        )
        vertical_edges = (
            math.hypot(p3.x() - p0.x(), p3.y() - p0.y()),
            math.hypot(p2.x() - p1.x(), p2.y() - p1.y()),
        )
        return (
            min(horizontal_edges) < source_width * 0.9999
            or min(vertical_edges) < source_height * 0.9999
        )


    def _proxy_transform_preview_image(
        self,
        source,
        target_width,
        target_height,
        quality,
        progress_callback=None,
    ):
        """大画像の表示用変形を縮小座標で生成し、UIの負荷を抑える。"""
        scale = min(
            1.0,
            TP_MASK_PROXY_MAX_DIMENSION
            / float(max(1, target_width, target_height)),
        )
        proxy_width = max(1, int(round(target_width * scale)))
        proxy_height = max(1, int(round(target_height * scale)))
        scale_x = proxy_width / float(max(1, target_width))
        scale_y = proxy_height / float(max(1, target_height))
        source_width = max(1, int(round(source.width() * scale_x)))
        source_height = max(1, int(round(source.height() * scale_y)))
        proxy_source_key = (
            int(source.cacheKey()),
            source_width,
            source_height,
        )
        if (
            self._tp_proxy_source_key != proxy_source_key
            or self._tp_proxy_source_image is None
            or self._tp_proxy_source_image.isNull()
        ):
            self._tp_proxy_source_image = source.scaled(
                source_width,
                source_height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._tp_proxy_source_key = proxy_source_key

        proxy_source = self._tp_proxy_source_image
        original_source = self.transform_source
        original_points = self.transform_points
        original_source_rect = self.transform_source_rect
        original_proxy_rendering = self._tp_proxy_rendering
        self.transform_source = proxy_source
        self.transform_points = [
            QPointF(point.x() * scale_x, point.y() * scale_y)
            for point in original_points
        ]
        if original_source_rect is not None:
            self.transform_source_rect = QRectF(
                original_source_rect.x() * scale_x,
                original_source_rect.y() * scale_y,
                original_source_rect.width() * scale_x,
                original_source_rect.height() * scale_y,
            )
        self._tp_proxy_rendering = True
        try:
            if quality:
                return self._tp_mask_preview_image(
                    proxy_source,
                    proxy_width,
                    proxy_height,
                    progress_callback=progress_callback,
                )
            return self._project_transform_source(
                proxy_source,
                proxy_width,
                proxy_height,
                smooth=False,
            )
        finally:
            self.transform_source = original_source
            self.transform_points = original_points
            self.transform_source_rect = original_source_rect
            self._tp_proxy_rendering = original_proxy_rendering

    def transform_preview_image(
        self,
        source_image=None,
        target_width=None,
        target_height=None,
        quality=None,
        preview_only=False,
        progress_callback=None,
    ):
        source = source_image if source_image is not None else self.transform_source
        if not self.transform_active or source is None:
            return None, QRectF()
        target_width = int(
            target_width if target_width is not None else self.active_layer.image.width()
        )
        target_height = int(
            target_height if target_height is not None else self.active_layer.image.height()
        )
        if quality is None:
            quality = bool(getattr(self, "transform_quality_active", False))
        if (
            preview_only
            and self._tp_uses_proxy(target_width, target_height)
        ):
            return self._proxy_transform_preview_image(
                source,
                target_width,
                target_height,
                bool(quality),
                progress_callback=progress_callback,
            )
        if quality:
            return self._tp_mask_preview_image(
                source,
                target_width,
                target_height,
                progress_callback=progress_callback,
            )
        return self._project_transform_source(
            source,
            target_width,
            target_height,
            smooth=False,
        )
