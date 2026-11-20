"""Read-only Qt images shared by model-backed display paths."""

from collections import OrderedDict
from typing import TYPE_CHECKING

from PySide6.QtGui import QImage

if TYPE_CHECKING:
    from .pma_image import PmaImage

DISPLAY_IMAGE_CACHE_BYTES = 256 * 1024 * 1024


def model_image_key(image: "PmaImage") -> tuple[int, int, int]:
    return image.generation, image.width, image.height


class DisplayImageCache:
    """Keep premultiplied images within a byte budget in LRU order."""

    def __init__(self, budget: int = DISPLAY_IMAGE_CACHE_BYTES):
        self._budget = budget
        self._entries: OrderedDict[tuple[int, int, int], QImage] = OrderedDict()
        self.bytes_used = 0
        self.hits = 0
        self.misses = 0
        self.converted_bytes = 0

    def clear(self) -> None:
        self._entries.clear()
        self.bytes_used = 0

    def forget(self, key: tuple[int, int, int]) -> None:
        image = self._entries.pop(key, None)
        if image is not None:
            self.bytes_used -= image.sizeInBytes()

    def get(self, source: "PmaImage") -> QImage:
        """Return a shared read-only image. Use QImage(result) before editing."""
        key = model_image_key(source)
        image = self._entries.get(key)
        if image is not None:
            self.hits += 1
            self._entries.move_to_end(key)
            return image

        self.misses += 1
        qt_image = source.to_qimage()
        premultiplied = QImage.Format.Format_ARGB32_Premultiplied
        if qt_image.format() == premultiplied:
            image = QImage(qt_image)
        else:
            image = qt_image.convertToFormat(premultiplied)
        size = image.sizeInBytes()
        self.converted_bytes += size
        if 0 < size <= self._budget:
            while self.bytes_used + size > self._budget:
                _, oldest = self._entries.popitem(last=False)
                self.bytes_used -= oldest.sizeInBytes()
            self._entries[key] = image
            self.bytes_used += size
        return image


display_images = DisplayImageCache()
