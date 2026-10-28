"""Onion-skin rendering for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods build the tinted onion
images for neighbouring frames, resolve which frames to show, and draw the
onion range beneath the active frame. They run against a live ``PaintCanvas``
instance and are called from its ``paintEvent``.
"""
import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from ._canvas_members import CanvasMembers
from . import imaging, native
from .utils import blank_image
from .logging_setup import get_logger

log = get_logger(__name__)


class OnionRenderMixin(CanvasMembers):
    def _cache_onion_image(self, key, image):
        # A 4K onion is ~32 MiB. A count-only limit of 48 could retain
        # over 1.5 GiB; bound bytes as well as entries.
        budget = 128 * 1024 * 1024
        size = image.sizeInBytes()
        if size > budget:
            return image
        used = sum(value.sizeInBytes() for value in self._onion_cache.values())
        while self._onion_cache and (used + size > budget or len(self._onion_cache) >= 48):
            oldest = self._onion_cache.pop(next(iter(self._onion_cache)))
            used -= oldest.sizeInBytes()
        self._onion_cache[key] = image
        return image

    def _tinted_onion_image(
        self, frame_index, color, color_enabled=True, selected_colors_only=False,
        target_size=None,
    ):
        signature = []
        for layer_index in range(len(self.frames[frame_index].layers)):
            if not self.onion_all_layers and layer_index != self.active_layer_index:
                continue
            key_frame = self.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                signature.append(None)
                continue
            layer = self.frames[key_frame].layers[layer_index]
            signature.append((
                layer_index, key_frame, int(layer.image.cacheKey()), bool(layer.visible),
                float(layer.opacity),
                bool(layer.color_filter_enabled),
                tuple(layer.color_filter_rgb) if layer.color_filter_rgb is not None else None,
                bool(layer.is_paper), bool(layer.is_draft),
            ))
        visible_signature = (
            None if self.visible_color_rgbs is None
            else tuple(sorted(self.visible_color_rgbs))
        )
        selected_signature = tuple(sorted(self.selected_used_color_rgbs))
        cache_key = (
            int(QColor(color).rgba()), bool(color_enabled),
            bool(selected_colors_only), bool(self.onion_all_layers),
            int(self.active_layer_index), selected_signature,
            tuple(signature), bool(self.silhouette_non_background),
            visible_signature,
            tuple(sorted((self.preview_color_remap or {}).items())),
            target_size,
        )
        cached = self._onion_cache.pop(cache_key, None)
        if cached is not None:
            self._onion_cache[cache_key] = cached
            return cached

        if target_size is not None:
            # Only the on-screen onion needs this preview. Keep full-resolution
            # composite/export paths intact and account for device pixel ratio
            # at the call site. Use nearest sampling, as the canvas does.
            width, height = target_size
            image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            destination = QRectF(0, 0, width, height)
            for layer_index in range(len(self.frames[frame_index].layers)):
                if not self.onion_all_layers and layer_index != self.active_layer_index:
                    continue
                key_frame = self.resolve_key_frame(frame_index, layer_index)
                if key_frame is None:
                    continue
                layer = self.frames[key_frame].layers[layer_index]
                if layer.visible and layer.opacity > 0.0:
                    source = self._display_layer_image(layer, layer_index)
                    if source is not None:
                        painter.setOpacity(max(0.0, min(1.0, float(layer.opacity))))
                        painter.drawImage(destination, source)
            painter.end()
        elif self.onion_all_layers:
            image = self.composite(frame_index, False)
        else:
            image = blank_image()
            key_frame = self.resolve_key_frame(frame_index, self.active_layer_index)
            if key_frame is not None:
                layer = self.frames[key_frame].layers[self.active_layer_index]
                if layer.visible:
                    painter = QPainter(image)
                    painter.setOpacity(max(0.0, min(1.0, float(layer.opacity))))
                    painter.drawImage(
                        0,
                        0,
                        self._display_layer_image(
                            layer,
                            self.active_layer_index,
                        ),
                    )
                    painter.end()
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = image.width(), image.height()
        ptr = imaging.qimage_buffer(image)
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, image.bytesPerLine())
        )
        rgba = rows[:, :width * 4].reshape((height, width, 4))
        tint_color = QColor(color)
        native_rgba = native.tint_onion(
            rgba, (tint_color.red(), tint_color.green(), tint_color.blue()),
            color_enabled,
            self.selected_used_color_rgbs if selected_colors_only else None,
        )
        result = QImage(
            native_rgba.data, width, height, native_rgba.strides[0],
            QImage.Format.Format_RGBA8888,
        ).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        return self._cache_onion_image(cache_key, result)

    def onion_shift_values(self, direction):
        if int(direction) < 0:
            return (
                float(self.onion_previous_shift_x),
                float(self.onion_previous_shift_y),
                float(self.onion_previous_rotation),
            )
        return (
            float(self.onion_next_shift_x),
            float(self.onion_next_shift_y),
            float(self.onion_next_rotation),
        )

    def onion_transform_values(self, direction):
        shift_x, shift_y, rotation = self.onion_shift_values(
            direction
        )
        scale = (
            float(self.onion_previous_scale)
            if int(direction) < 0
            else float(self.onion_next_scale)
        )
        return (
            shift_x,
            shift_y,
            rotation,
            max(0.01, scale / 100.0),
        )

    def _onion_frame_identity(self, frame_index):
        """指定位置で表示される絵を、保持フレームと区別せず識別する。"""
        frame_index = int(frame_index)
        if not (0 <= frame_index < len(self.frames)):
            return None

        if not self.onion_all_layers:
            layer_index = int(self.active_layer_index)
            key_frame = self.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                return None
            layer = self.frames[key_frame].layers[layer_index]
            return (layer_index, int(key_frame)) if layer.visible else None

        identity = []
        layer_count = len(self.frames[frame_index].layers)
        for layer_index in range(layer_count):
            key_frame = self.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                continue
            layer = self.frames[key_frame].layers[layer_index]
            if layer.visible:
                identity.append((layer_index, int(key_frame)))
        return tuple(identity) if identity else None

    def _onion_frame_indices(self, direction, count):
        """空セルと同じ絵の保持区間を飛ばし、前後の実画像位置を返す。"""
        direction = -1 if int(direction) < 0 else 1
        count = max(0, int(count))
        if count <= 0:
            return []

        indices = []
        previous_identity = self._onion_frame_identity(
            self.current_frame
        )
        frame_index = int(self.current_frame) + direction
        while 0 <= frame_index < len(self.frames) and len(indices) < count:
            identity = self._onion_frame_identity(frame_index)
            if identity is not None and identity != previous_identity:
                indices.append(frame_index)
                previous_identity = identity
            frame_index += direction
        return indices

    def _draw_onion_range(
        self, painter, work_rect, direction, count, opacity, color,
        color_enabled=True, selected_colors_only=False
    ):
        count = max(0, int(count))
        shift_x, shift_y, local_rotation, local_scale = (
            self.onion_transform_values(direction)
        )
        levels = (
            list(self.onion_previous_levels)
            if int(direction) < 0
            else list(self.onion_next_levels)
        )
        transform_center = work_rect.center()
        outline_rect = QRectF(self.canvas_rect()).adjusted(
            -0.5, -0.5, 0.5, 0.5
        )
        frame_indices = self._onion_frame_indices(direction, count)
        target_size = self._playback_cache_dimensions(work_rect)
        # Local onion zoom can enlarge the preview independently of the canvas.
        # Never reduce below the canvas resolution or exceed source resolution.
        preview_scale = max(1.0, local_scale * float(self.onion_tu_tb_scale))
        if preview_scale > 1.0:
            from .utils import workspace_size
            source_width, source_height = workspace_size()
            target_size = (
                min(source_width, max(1, round(target_size[0] * preview_scale))),
                min(source_height, max(1, round(target_size[1] * preview_scale))),
            )

        # 遠い絵から描き、近い絵を手前へ重ねる。
        for distance, frame_index in reversed(list(enumerate(
            frame_indices,
            start=1,
        ))):

            if distance - 1 < len(levels):
                level = max(
                    0.0,
                    min(1.0, float(levels[distance - 1]) / 100.0),
                )
            else:
                level = max(
                    0.0,
                    min(
                        1.0,
                        1.0
                        - ((distance - 1) / max(1, count)) * 0.55,
                    ),
                )
            draw_opacity = max(
                0.0,
                min(1.0, float(opacity) * level),
            )

            if draw_opacity > 0.0:
                onion = self._tinted_onion_image(
                    frame_index, color, color_enabled, selected_colors_only,
                    target_size=target_size,
                )
                if self.flip_horizontal or self.flip_vertical:
                    onion = onion.mirrored(self.flip_horizontal, self.flip_vertical)
                painter.save()
                painter.translate(
                    transform_center.x()
                    + shift_x * float(self.zoom),
                    transform_center.y()
                    + shift_y * float(self.zoom),
                )
                painter.rotate(local_rotation)
                painter.scale(local_scale, local_scale)
                painter.translate(
                    -transform_center.x(),
                    -transform_center.y(),
                )
                painter.setOpacity(draw_opacity)
                painter.drawImage(work_rect, onion)
                painter.restore()

        if frame_indices:
            # 外周は全体濃度・コマ別濃度・色適用設定とは分離し、
            # 常に100%不透明度の1px線で表示する。
            painter.save()
            painter.translate(
                transform_center.x()
                + shift_x * float(self.zoom),
                transform_center.y()
                + shift_y * float(self.zoom),
            )
            painter.rotate(local_rotation)
            painter.scale(local_scale, local_scale)
            painter.translate(
                -transform_center.x(),
                -transform_center.y(),
            )
            painter.setOpacity(1.0)
            outline_color = QColor(color)
            outline_color.setAlpha(255)
            outline_pen = QPen(outline_color, 1.0)
            outline_pen.setCosmetic(True)
            outline_pen.setStyle(Qt.PenStyle.SolidLine)
            painter.setPen(outline_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(outline_rect)
            painter.restore()
