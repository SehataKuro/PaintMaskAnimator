"""Unit tests for the extracted pure image-conversion helpers."""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from paintmaskanimator import imaging  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def test_rgba_roundtrip_preserves_pixels(qapp):
    rgba = np.zeros((4, 5, 4), dtype=np.uint8)
    rgba[0, 0] = (255, 0, 0, 255)
    rgba[1, 2] = (0, 128, 64, 200)
    rgba[3, 4] = (10, 20, 30, 255)
    img = imaging.rgba_array_to_qimage(rgba)
    assert (img.width(), img.height()) == (5, 4)
    back = imaging.qimage_rgba_array(img)
    assert back.shape == (4, 5, 4)
    # opaque pixels survive the ARGB32-premultiplied round trip exactly
    assert tuple(back[0, 0]) == (255, 0, 0, 255)
    assert tuple(back[3, 4]) == (10, 20, 30, 255)


def test_qimage_rgba_array_empty(qapp):
    assert imaging.qimage_rgba_array(QImage()).shape == (0, 0, 4)


def test_rgba_array_to_qimage_rejects_bad_shape(qapp):
    with pytest.raises(ValueError):
        imaging.rgba_array_to_qimage(np.zeros((4, 5, 3), dtype=np.uint8))


def test_gray_array_shape(qapp):
    rgba = np.full((3, 6, 4), 255, dtype=np.uint8)
    img = imaging.rgba_array_to_qimage(rgba)
    gray = imaging.qimage_gray_array(img)
    assert gray.shape == (3, 6)
    assert np.all(gray == 255)


def test_qimage_buffer_exposes_full_storage(qapp):
    image = QImage(7, 3, QImage.Format.Format_RGBA8888)
    buffer = imaging.qimage_buffer(image)
    assert np.frombuffer(buffer, dtype=np.uint8).size >= image.sizeInBytes()


def test_delegators_match_module(qapp):
    """PaintCanvas wrappers must call through to imaging.*"""
    from paintmaskanimator.canvas import PaintCanvas
    rgba = np.full((2, 2, 4), 128, dtype=np.uint8)
    rgba[..., 3] = 255
    via_class = imaging.qimage_rgba_array(
        PaintCanvas._rgba_array_to_qimage(rgba)
    )
    via_module = imaging.qimage_rgba_array(imaging.rgba_array_to_qimage(rgba))
    assert np.array_equal(via_class, via_module)
