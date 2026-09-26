"""大きなキャンバス向けの最適化が、結果を変えずに効いていることの確認。"""
import numpy as np
import pytest

from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from paintmaskanimator import imaging
from paintmaskanimator.models import Layer


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _reference_region(passable, start):
    """素朴な4近傍の塗りつぶし（検証用）。"""
    height, width = passable.shape
    region = np.zeros_like(passable)
    stack = [start]
    while stack:
        x, y = stack.pop()
        if not (0 <= x < width and 0 <= y < height):
            continue
        if region[y, x] or not passable[y, x]:
            continue
        region[y, x] = True
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    return region


def test_run_based_region_matches_pixel_flood_fill():
    rng = np.random.default_rng(7)
    for _ in range(200):
        height, width = rng.integers(1, 30, 2)
        passable = rng.random((height, width)) < rng.uniform(0.3, 0.8)
        start = (int(rng.integers(0, width)), int(rng.integers(0, height)))
        expected = _reference_region(passable, start)
        actual = imaging.scanline_connected_region(passable, start)
        assert np.array_equal(actual, expected)


def test_packed_masks_match_channel_comparisons():
    rng = np.random.default_rng(3)
    pixels = rng.integers(0, 256, (40, 50, 4), dtype=np.uint8)
    pixels[::4, :, :3] = 255
    pixels[1::6, :, 3] = 0
    pixels[2::5, :, :3] = (10, 20, 30)
    packed = imaging.rgba_packed_view(pixels)
    background = (pixels[:, :, 3] == 0) | np.all(pixels[:, :, :3] == 255, axis=2)
    assert np.array_equal(imaging.background_mask_packed(packed), background)
    same = (pixels[:, :, 3] > 0) & np.all(pixels[:, :, :3] == (10, 20, 30), axis=2)
    assert np.array_equal(imaging.opaque_rgb_mask_packed(packed, (10, 20, 30)), same)
    assert imaging.mask_bounds(np.zeros((4, 4), bool)) is None
    mask = np.zeros((10, 12), bool)
    mask[3:5, 2:9] = True
    assert imaging.mask_bounds(mask) == (2, 3, 9, 5)


def test_layer_clone_shares_pixels_until_written(qapp):
    image = QImage(16, 16, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(10, 20, 30))
    layer = Layer("A", image)
    clone = layer.clone()
    assert clone.image.cacheKey() == layer.image.cacheKey()

    painter = QPainter(clone.image)
    painter.fillRect(0, 0, 4, 4, QColor(200, 0, 0))
    painter.end()
    assert layer.image.pixelColor(1, 1) == QColor(10, 20, 30)

    other = layer.clone()
    pixels = np.frombuffer(imaging.qimage_buffer(other.image), dtype=np.uint8)
    pixels[:] = 0
    assert layer.image.pixelColor(1, 1) == QColor(10, 20, 30)
    assert other.image.pixelColor(1, 1).alpha() == 0


def test_new_document_dialog_allows_10000_square(qapp):
    from paintmaskanimator.widgets import CanvasSizeDialog

    dialog = CanvasSizeDialog(1280, 720, "新規作成")
    try:
        dialog.w.setValue(10000)
        dialog.h.setValue(10000)
        assert dialog.values() == (10000, 10000)
    finally:
        dialog.close()
