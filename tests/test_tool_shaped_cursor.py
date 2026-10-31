"""ツールごとに形の違うカーソルを出す（Issue #45）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import utils  # noqa: E402
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


@pytest.mark.parametrize("tool", utils.TOOL_CURSOR_NAMES)
def test_every_shaped_tool_has_a_bitmap_cursor(qapp, tool):
    cursor = utils.tool_cursor(tool)
    assert cursor is not None
    assert not cursor.pixmap().isNull()
    assert cursor.hotSpot().x() == 6 and cursor.hotSpot().y() == 6


def test_tools_with_their_own_cursor_are_left_alone(qapp):
    for tool in ("brush", "eyedropper", "hand", "zoom"):
        assert utils.tool_cursor(tool) is None


def test_selecting_a_tool_changes_the_canvas_cursor(window):
    window.canvas.set_tool("bucket")
    bucket = window.canvas.cursor().pixmap().toImage()
    window.canvas.set_tool("dust")
    dust = window.canvas.cursor().pixmap().toImage()

    assert not bucket.isNull() and not dust.isNull()
    assert bucket != dust


def test_the_hand_tool_keeps_its_own_shape(window):
    window.canvas.set_tool("hand")
    assert window.canvas.cursor().shape() in (
        Qt.CursorShape.OpenHandCursor,
        Qt.CursorShape.ClosedHandCursor,
    )
