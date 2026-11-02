"""使用色の「この色を選択範囲にする」をショートカットから呼ぶ（Issue #44）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

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


def paint(window, color, origin=(10, 10), size=3):
    layer = window.canvas.frames[0].layers[0]
    x0, y0 = origin
    for y in range(y0, y0 + size):
        for x in range(x0, x0 + size):
            layer.image.setPixelColor(x, y, QColor(color))
    layer.has_content = True


def test_the_action_is_offered_in_the_shortcut_list(window):
    actions = [action for _, action in window.tool_command_actions]
    assert window.a_focus_used_color in actions


def test_the_shortcut_selects_the_colour_chosen_in_the_palette(window):
    paint(window, "#ff0000")
    window.palette.parent_rgb = (255, 0, 0)

    window.a_focus_used_color.trigger()

    assert window.canvas.selection_mask_override is not None
    assert window.canvas.selection_mask_override.any()


def test_white_is_refused_because_it_is_the_background(window):
    window.palette.parent_rgb = (255, 255, 255)

    window.a_focus_used_color.trigger()

    assert window.canvas.selection_mask_override is None
