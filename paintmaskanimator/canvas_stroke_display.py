"""Stroke-display buffer (fast in-progress brush preview) for PaintCanvas.

Split out of ``canvas.py`` as a mixin. These methods maintain the lightweight
overlay buffer that shows an in-progress opaque brush stroke without
re-transforming the whole layer each stamp, plus the warm-up/pre-warm paths
that avoid first-stroke jank. They run against a live ``PaintCanvas`` instance.
"""
from .common import *  # noqa: F401,F403
from ._canvas_members import CanvasMembers
from .logging_setup import get_logger

log = get_logger(__name__)


class StrokeDisplayMixin(CanvasMembers):
    def _begin_opaque_brush_stroke(self):
        """Start a stroke without allocating or copying a full-layer image."""
        self._stroke_before_tiles = {}
        self._stroke_dirty_rect = QRect()
        self._stroke_undo_frame = int(self.current_frame)
        self._stroke_undo_layer = int(self.active_layer_index)
        self._stroke_prev_has_content = bool(self.active_layer.has_content)
        self._brush_blend_base_image = None
        self._brush_blended_colors = set()
        # この値はUIの不透明度だけから取得する。
        # タブレット筆圧値は絶対に掛けない。
        self._brush_stroke_opacity = (
            self.paint_opacity_value()
        )
        self._init_stroke_display()

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
            self.undo_stack.append((
                "layer_tiles",
                self._stroke_undo_frame,
                self._stroke_undo_layer,
                tiles,
                self._stroke_prev_has_content,
            ))
            self.undo_stack = self.undo_stack[-MAX_UNDO:]
            self.redo_stack.clear()
        self._stroke_before_tiles = None
        self._stroke_dirty_rect = None
        self._brush_blend_base_image = None
        self._brush_blended_colors = set()
        self._brush_stroke_opacity = (
            self.paint_opacity_value()
        )
        self._finish_stroke_display()

    def _stroke_display_eligible(self):
        """True when the incremental display buffer can represent the layer.

        Only when the active layer is a normal (non-paper) layer with no
        palette/colour filter and silhouette mode off — otherwise the display
        image is not a plain white-to-transparent transform of ``layer.image``
        and the slower full-image path stays correct.
        """
        layer = self.active_layer
        if layer is None or getattr(layer, "is_paper", False):
            return False
        if self.silhouette_non_background:
            return False
        if getattr(self, "visible_color_rgbs", None):
            return False
        if (
            getattr(layer, "color_filter_enabled", False)
            and layer.color_filter_rgb is not None
        ):
            return False
        return True

    def prewarm_blank_stroke_display(self):
        """Prepare a new blank layer's display buffer before the first press."""
        if not self._stroke_display_eligible():
            return
        layer = self.active_layer
        if layer.has_content or layer.image is None or layer.image.isNull():
            return
        key = self._pseudo_transparency_key(layer.image)
        if key in self._pseudo_transparency_cache:
            return
        display = QImage(
            layer.image.size(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        display.fill(Qt.GlobalColor.transparent)
        if len(self._pseudo_transparency_cache) >= 96:
            self._pseudo_transparency_cache.pop(
                next(iter(self._pseudo_transparency_cache))
            )
        self._pseudo_transparency_cache[key] = display

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
        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
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

    def _init_stroke_display(self):
        """Move the cached display image into stroke ownership without copying.

        The per-stamp cost then becomes proportional to the brush footprint
        and a warm-cache stroke start is independent of canvas size.
        """
        self._stroke_display_image = None
        self._stroke_display_layer_index = -1
        if not self._stroke_display_eligible():
            return
        layer = self.active_layer
        cache_key = self._pseudo_transparency_key(layer.image)
        base = self._pseudo_transparency_cache.pop(cache_key, None)
        if base is None:
            if (
                not layer.has_content
                or self._editable_key_was_blank
            ):
                # A brand-new canvas can receive a press before its first idle
                # repaint.  Its layer is known to be entirely transparent, so
                # avoid scanning and converting the whole workspace on the
                # first stroke.  The buffer is patched incrementally below as
                # stamps are written to the real layer image.
                base = QImage(
                    layer.image.size(),
                    QImage.Format.Format_ARGB32_Premultiplied,
                )
                base.fill(Qt.GlobalColor.transparent)
            else:
                # Usually the idle repaint has already populated this cache.
                # Keep a correctness fallback for presses arriving before the
                # first paint on a layer that already contains pixels.
                base = self._pseudo_transparent_display_image(layer.image)
                self._pseudo_transparency_cache.pop(cache_key, None)
        if base is None or base.isNull():
            return
        self._stroke_display_image = base
        self._stroke_display_layer_index = self.active_layer_index

    def _patch_stroke_display(self, canvas_rect):
        """Re-apply the white→transparent transform for one stamped region."""
        buffer = self._stroke_display_image
        if buffer is None:
            return
        self._patch_pseudo_transparent_display(
            buffer,
            self.active_layer.image,
            canvas_rect,
        )

    def _finish_stroke_display(self):
        """Hand the fully-patched buffer to the display cache, then release it.

        Seeding the cache under the layer's current key means the first repaint
        after the stroke is a cache hit instead of another full re-transform.
        """
        buffer = self._stroke_display_image
        self._stroke_display_image = None
        self._stroke_display_layer_index = -1
        if buffer is None:
            return
        layer = self.active_layer
        if layer is None or layer.image is None or layer.image.isNull():
            return
        self._cache_pseudo_transparent_display(layer.image, buffer)
