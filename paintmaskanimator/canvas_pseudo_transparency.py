"""Pseudo-transparent (checkerboard) display caching for PaintCanvas.

Split out of ``canvas.py`` as a mixin. These methods detect the pseudo-
transparent colour, build and cache the checkerboard display image used to show
it, and patch that cache for in-progress strokes. They run against a live
``PaintCanvas`` instance.
"""
from .common import *  # noqa: F401,F403
from . import colors
from .logging_setup import get_logger

log = get_logger(__name__)


class PseudoTransparencyMixin:
    @staticmethod
    def is_pseudo_transparent_color(*args, **kwargs):
        return colors.is_pseudo_transparent_color(*args, **kwargs)

    @staticmethod
    def _patch_pseudo_transparent_display(buffer, image, canvas_rect):
        """Update one region of an existing pseudo-transparent display."""
        x1 = max(0, int(canvas_rect.left()))
        y1 = max(0, int(canvas_rect.top()))
        x2 = min(image.width(), int(canvas_rect.left() + canvas_rect.width()))
        y2 = min(image.height(), int(canvas_rect.top() + canvas_rect.height()))
        w, h = x2 - x1, y2 - y1
        if w <= 0 or h <= 0:
            return
        sub = image.copy(x1, y1, w, h).convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        ptr = sub.bits()
        try:
            ptr.setsize(sub.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (h, sub.bytesPerLine())
        )
        pixels = rows[:, : w * 4].reshape((h, w, 4))
        alpha = pixels[:, :, 3]
        present = alpha > 0
        white = (
            present
            & (pixels[:, :, 0] == 255)
            & (pixels[:, :, 1] == 255)
            & (pixels[:, :, 2] == 255)
        )
        alpha[white] = 0
        alpha[present & ~white] = 255
        patch = sub.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        painter = QPainter(buffer)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Source
        )
        painter.drawImage(x1, y1, patch)
        painter.end()

    def _cache_pseudo_transparent_display(self, image, display):
        if display is None or display.isNull():
            return
        key = self._pseudo_transparency_key(image)
        if len(self._pseudo_transparency_cache) >= 96:
            self._pseudo_transparency_cache.pop(
                next(iter(self._pseudo_transparency_cache))
            )
        self._pseudo_transparency_cache[key] = display

    def _pseudo_transparency_key(self, image):
        """Return the cache key shared by idle and in-stroke display paths."""
        try:
            return (
                int(image.cacheKey()),
                image.width(),
                image.height(),
            )
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.debug("cacheKey() unavailable, using id() fallback: %s", exc)
            return (id(image), image.width(), image.height())

    def _pseudo_transparent_display_image(self, image):
        """#FFFFFFを表示上だけ透明化し、他の可視画素はα255で表示する。"""
        if image is None or image.isNull():
            return image
        key = self._pseudo_transparency_key(image)

        cached = self._pseudo_transparency_cache.get(key)
        if cached is not None:
            return cached

        rgba = image.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        width, height = rgba.width(), rgba.height()
        if width <= 0 or height <= 0:
            return rgba

        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(
            ptr,
            dtype=np.uint8,
        ).reshape((height, rgba.bytesPerLine()))
        pixels = rows[:, :width * 4].reshape(
            (height, width, 4)
        )
        alpha = pixels[:, :, 3]
        present = alpha > 0
        white = (
            present
            & (pixels[:, :, 0] == 255)
            & (pixels[:, :, 1] == 255)
            & (pixels[:, :, 2] == 255)
        )
        alpha[white] = 0
        alpha[present & ~white] = 255

        result = rgba.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        if len(self._pseudo_transparency_cache) >= 96:
            self._pseudo_transparency_cache.pop(
                next(iter(self._pseudo_transparency_cache))
            )
        self._pseudo_transparency_cache[key] = result
        return result
