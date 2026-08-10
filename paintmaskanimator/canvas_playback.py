"""Playback frame compositing/caching for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods build the frame->key map
used during playback, advance the playback position, and composite/cache the
image shown for each played-back frame. They run against a live ``PaintCanvas``
instance.
"""
from .common import *  # noqa: F401,F403
from ._canvas_members import CanvasMembers
from .logging_setup import get_logger

log = get_logger(__name__)


class PlaybackMixin(CanvasMembers):
    def _build_playback_key_map(self):
        """各フレームで表示するキーフレームを事前解決する。"""
        frame_count = len(self.frames)
        if frame_count <= 0:
            self._playback_resolved_keys = []
            return

        layer_count = max(
            (len(frame.layers) for frame in self.frames),
            default=0,
        )
        resolved = [
            [None] * layer_count
            for _ in range(frame_count)
        ]

        for layer_index in range(layer_count):
            current_key = None
            exposure_end = -1
            for frame_index in range(frame_count):
                if layer_index >= len(
                    self.frames[frame_index].layers
                ):
                    current_key = None
                    exposure_end = -1
                    continue

                layer = self.frames[
                    frame_index
                ].layers[layer_index]
                if layer.has_content:
                    current_key = frame_index
                    exposure_end = (
                        frame_index
                        + max(1, int(layer.exposure))
                    )
                elif frame_index >= exposure_end:
                    current_key = None

                resolved[frame_index][layer_index] = current_key

        self._playback_resolved_keys = resolved

    def set_playback_active(self, active):
        self._playback_active = bool(active)
        self._playback_frame_cache.clear()
        if self._playback_active:
            self._build_playback_key_map()
        else:
            self._playback_resolved_keys = []
        self.update()

    def playback_advance(self, steps=1):
        """UI全体を再構築せず、再生フレームだけを進める。"""
        if not self.frames:
            return
        self.current_frame = (
            self.current_frame + max(1, int(steps))
        ) % len(self.frames)
        self.update()

    def _playback_cache_dimensions(self, target_rect):
        source_width = max(1, int(round(target_rect.width())))
        source_height = max(1, int(round(target_rect.height())))

        # 大画像でも再生用キャッシュは表示領域程度までに制限する。
        max_width = max(480, min(1920, int(self.width() * 1.25)))
        max_height = max(320, min(1080, int(self.height() * 1.25)))
        scale = min(
            1.0,
            max_width / float(source_width),
            max_height / float(source_height),
        )
        return (
            max(1, int(round(source_width * scale))),
            max(1, int(round(source_height * scale))),
        )

    def playback_frame_image(self, frame_index, target_rect):
        if not (0 <= frame_index < len(self.frames)):
            return None

        cache_width, cache_height = (
            self._playback_cache_dimensions(target_rect)
        )
        cache_key = (
            int(frame_index),
            int(cache_width),
            int(cache_height),
            int(self.active_layer_index),
            bool(self.flip_horizontal),
            bool(self.silhouette_non_background),
        )
        cached = self._playback_frame_cache.get(cache_key)
        if cached is not None:
            # 最近使ったフレームを末尾へ移す簡易LRU。
            self._playback_frame_cache.pop(cache_key, None)
            self._playback_frame_cache[cache_key] = cached
            return cached

        image = QImage(
            cache_width,
            cache_height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            False,
        )
        destination = QRectF(
            0.0,
            0.0,
            float(cache_width),
            float(cache_height),
        )

        layer_count = len(self.frames[frame_index].layers)
        resolved_row = (
            self._playback_resolved_keys[frame_index]
            if frame_index < len(self._playback_resolved_keys)
            else []
        )
        for layer_index in range(layer_count):
            key_frame = (
                resolved_row[layer_index]
                if layer_index < len(resolved_row)
                else self.resolve_key_frame(
                    frame_index, layer_index
                )
            )
            if key_frame is None:
                continue

            layer = self.frames[
                key_frame
            ].layers[layer_index]
            if not layer.visible:
                continue

            draw_image = self._display_layer_image(
                layer, layer_index
            )
            if self.flip_horizontal:
                draw_image = draw_image.mirrored(True, False)
            painter.setOpacity(float(layer.opacity))
            painter.drawImage(destination, draw_image)

        painter.end()

        self._playback_frame_cache[cache_key] = image
        while (
            len(self._playback_frame_cache)
            > self._playback_cache_limit
        ):
            oldest_key = next(iter(self._playback_frame_cache))
            self._playback_frame_cache.pop(oldest_key, None)
        return image
