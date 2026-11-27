"""Test setup helpers for editing model images through the rectangle API."""

from contextlib import contextmanager

from PySide6.QtCore import QRect


@contextmanager
def edit_qimage(image, rect=None):
    """Yield an independent patch and write it back on successful exit."""
    if rect is None:
        rect = QRect(0, 0, image.width, image.height)
    patch = image.read_qimage(*rect.getRect())
    yield patch
    image.write_qimage(patch, rect.x(), rect.y())
