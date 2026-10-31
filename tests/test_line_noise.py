"""線周りの色ノイズ除去（Issue #19）。"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import line_noise  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def opaque(height, width, rgb=(0, 0, 0)):
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[:, :, :3] = rgb
    pixels[:, :, 3] = 255
    return pixels


def test_an_isolated_dot_in_a_line_is_voted_away():
    pixels = opaque(9, 9, (0, 0, 0))
    pixels[4, 4, :3] = (255, 255, 0)

    cleaned, changed = line_noise.remove_line_noise(pixels)

    assert changed == 1
    assert tuple(cleaned[4, 4, :3]) == (0, 0, 0)


def test_colour_areas_are_left_alone():
    """意図した色面を壊さない。ふちの画素は同じ色の隣を必ず持つ。"""
    pixels = opaque(20, 20, (200, 200, 200))
    pixels[:, 10:, :3] = (20, 20, 20)

    cleaned, changed = line_noise.remove_line_noise(pixels)

    assert changed == 0
    assert np.array_equal(cleaned, pixels)


def test_dots_far_from_any_line_are_left_alone():
    """線の近くに限る。明るい色面の中のドットには触れない。"""
    pixels = opaque(15, 15, (240, 240, 240))
    pixels[7, 7, :3] = (255, 255, 0)

    _cleaned, changed = line_noise.remove_line_noise(pixels)

    assert changed == 0


def test_the_line_itself_is_never_repainted():
    pixels = opaque(11, 11, (250, 250, 250))
    pixels[5, :, :3] = (0, 0, 0)

    cleaned, _changed = line_noise.remove_line_noise(pixels)

    assert np.all(cleaned[5, :, :3] == 0)


def test_transparent_neighbours_do_not_erase_the_dot():
    pixels = np.zeros((9, 9, 4), dtype=np.uint8)
    pixels[3:6, 3:6, :3] = 0
    pixels[3:6, 3:6, 3] = 255
    pixels[4, 4, :3] = (255, 255, 0)

    cleaned, _changed = line_noise.remove_line_noise(pixels)

    assert cleaned[4, 4, 3] == 255


def test_the_tool_offers_the_mode_and_applies_it(window):
    layer = window.canvas.frames[0].layers[0]
    for y in range(10, 19):
        for x in range(10, 19):
            layer.image.setPixelColor(x, y, QColor(0, 0, 0))
    layer.image.setPixelColor(14, 14, QColor(255, 255, 0))
    layer.has_content = True

    assert window.tools.dust_mode.findData("line_noise") >= 0
    window.tools.dust_mode.setCurrentIndex(
        window.tools.dust_mode.findData("line_noise")
    )

    window.line_ops.remove_dust_fill_surrounding()

    assert window.canvas.frames[0].layers[0].image.pixelColor(14, 14) == QColor(
        0, 0, 0
    )
