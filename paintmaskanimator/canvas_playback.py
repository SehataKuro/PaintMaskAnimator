"""Playback frame compositing/caching for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods build the frame->key map
used during playback, advance the playback position, and composite/cache the
image shown for each played-back frame. They run against a live ``PaintCanvas``
instance.
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from ._canvas_members import CanvasMembers
from .utils import workspace_size
from .logging_setup import get_logger

log = get_logger(__name__)


class PlaybackMixin(CanvasMembers):
    def _build_playback_key_map(self):
        """各フレームで表示するキーフレームを事前解決する。"""
        from .timeline_resolution import resolve_exposures

        self._playback_resolved_keys = [
            [int(key) if key >= 0 else None for key in row]
            for row in resolve_exposures(self.frames)
        ]

    def set_playback_active(self, active):
        self._playback_active = bool(active)
        self._playback_frame_cache.clear()
        if self._playback_active:
            start, end = self.playback_bounds()
            if not start <= self.current_frame < end:
                self.current_frame = start
            self._build_playback_key_map()
        else:
            self._playback_resolved_keys = []
        self.update()

    def playback_advance(self, steps=1):
        """UI全体を再構築せず、再生フレームだけを進める。"""
        if not self.frames:
            return
        start, end = self.playback_bounds()
        span = max(1, end - start)
        self.current_frame = start + (
            self.current_frame - start + max(1, int(steps))
        ) % span
        self.update()

    def playback_bounds(self):
        """Return the valid inclusive/exclusive playback range."""
        count = max(1, len(self.frames))
        start = max(0, min(int(self.playback_range_start), count - 1))
        requested_end = self.playback_range_end
        end = count if requested_end is None else max(start + 1, int(requested_end))
        return start, min(end, count)

    def _playback_cache_dimensions(self, target_rect):
        """再生用キャッシュの大きさ（実際の画面の画素数）。

        以前は見えている大きさ（論理ピクセル）で作り、さらに 1920×1080 で
        頭打ちにしていたので、Retina では半分の解像度になり、再生中だけ
        ぼやけていた。画面の倍率を掛けた画素数で作り、元の絵より大きくは
        しない（それ以上は表示のときに拡大すればよい）。
        """
        ratio = max(1.0, float(self.devicePixelRatioF()))
        source_width, source_height = workspace_size()
        width = max(1, int(round(target_rect.width() * ratio)))
        height = max(1, int(round(target_rect.height() * ratio)))
        scale = min(
            1.0,
            source_width / float(width),
            source_height / float(height),
        )
        return (
            max(1, int(round(width * scale))),
            max(1, int(round(height * scale))),
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
            bool(getattr(self, "flip_vertical", False)),
            bool(self.silhouette_non_background),
            # 比較表示の有無で中身が変わるので、今の絵のコマを使い回さない。
            bool(self.show_initial_state),
            self.view_preview.key(),
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

            draw_image = self.view_layer_image(
                layer, layer_index,
                self._layer_display_source(layer, layer_index),
            )
            if self.flip_horizontal or getattr(self, "flip_vertical", False):
                draw_image = draw_image.mirrored(
                    self.flip_horizontal, getattr(self, "flip_vertical", False)
                )
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
