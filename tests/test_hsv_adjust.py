"""使用色の色相・彩度・明度調整（Issue #22）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from paintmaskanimator import hsv_adjust_dialog  # noqa: E402
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


def paint(window, color, origin=(10, 10), size=4):
    layer = window.canvas.frames[0].layers[0]
    x0, y0 = origin
    for y in range(y0, y0 + size):
        for x in range(x0, x0 + size):
            layer.image.setPixelColor(x, y, QColor(color))
    layer.has_content = True


def test_the_hue_turns_all_the_way_round():
    assert hsv_adjust_dialog.shifted_color((255, 0, 0), 120, 0, 0) == (0, 255, 0)
    assert hsv_adjust_dialog.shifted_color((255, 0, 0), 360, 0, 0) == (255, 0, 0)


def test_greys_stay_grey_however_far_the_hue_turns():
    """無彩色は色相を持たないので、回しても色は付かない。"""
    assert hsv_adjust_dialog.shifted_color((128, 128, 128), 90, 0, 0) == (
        128, 128, 128
    )


def test_brightness_and_saturation_stay_in_range():
    assert hsv_adjust_dialog.shifted_color((255, 0, 0), 0, 0, 100) == (255, 0, 0)
    assert hsv_adjust_dialog.shifted_color((255, 0, 0), 0, 0, -100) == (0, 0, 0)


def test_the_mapping_skips_white_and_unchanged_colours():
    mapping = hsv_adjust_dialog.shifted_mapping(
        [(255, 0, 0), (255, 255, 255)], 0, 0, 0
    )
    assert mapping == {}

    mapping = hsv_adjust_dialog.shifted_mapping(
        [(255, 0, 0), (255, 255, 255)], 120, 0, 0
    )
    assert mapping == {(255, 0, 0): (0, 255, 0)}


def test_several_colours_are_shifted_together():
    mapping = hsv_adjust_dialog.shifted_mapping(
        [(255, 0, 0), (0, 0, 255)], 120, 0, 0
    )
    assert mapping == {(255, 0, 0): (0, 255, 0), (0, 0, 255): (255, 0, 0)}


def test_accepting_applies_to_every_frame(window, monkeypatch):
    window.canvas._ensure_frame_count(3)
    for frame_index in range(3):
        layer = window.canvas.frames[frame_index].layers[0]
        layer.image.setPixelColor(10, 10, QColor(255, 0, 0))
        layer.has_content = True

    def accept(dialog):
        dialog.hue.setValue(120)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        hsv_adjust_dialog.HsvAdjustDialog, "exec", accept, raising=False
    )

    assert window.used_color.adjust_used_color_hsv({(255, 0, 0)})

    for frame in window.canvas.frames:
        assert frame.layers[0].image.pixelColor(10, 10) == QColor(0, 255, 0)


def test_cancelling_leaves_the_image_and_the_preview_alone(window, monkeypatch):
    paint(window, "#ff0000")
    before = window.canvas.frames[0].layers[0].image.copy()

    def cancel(dialog):
        dialog.hue.setValue(120)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(
        hsv_adjust_dialog.HsvAdjustDialog, "exec", cancel, raising=False
    )

    assert not window.used_color.adjust_used_color_hsv({(255, 0, 0)})

    assert window.canvas.frames[0].layers[0].image == before
    assert not window.canvas.preview_color_remap


def test_white_alone_is_refused(window):
    assert not window.used_color.adjust_used_color_hsv({(255, 255, 255)})
