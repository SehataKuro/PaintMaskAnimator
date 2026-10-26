"""「前のセルの色で自動塗り」の結合テスト。"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.undo_entries import ScopedBatchUndo

from test_bucket_all_frames import outlined_cell


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


def paint_inside(window, frame_index, origin, size, color):
    image = window.canvas.frames[frame_index].layers[0].image
    x0, y0 = origin
    for y in range(y0 + 1, y0 + size - 1):
        for x in range(x0 + 1, x0 + size - 1):
            image.setPixelColor(x, y, QColor(color))


def test_fills_the_current_cell_from_the_previous_one_with_one_undo(window):
    canvas = window.canvas
    canvas._ensure_frame_count(2)
    outlined_cell(window, 0, origin=(4, 4), size=20)
    paint_inside(window, 0, (4, 4), 20, "#e03030")
    outlined_cell(window, 1, origin=(9, 6), size=20)  # 少し動いた同じ図形
    canvas.current_frame = 1
    depth = len(canvas.undo_stack)

    window.a_auto_fill.trigger()

    image = canvas.frames[1].layers[0].image
    assert image.pixelColor(18, 15) == QColor("#e03030")
    assert image.pixelColor(9, 6) == QColor("black"), "線は塗らない"
    assert image.pixelColor(1, 1) == QColor("white"), "外の背景は塗らない"
    assert len(canvas.undo_stack) == depth + 1
    assert isinstance(canvas.undo_stack[-1], ScopedBatchUndo)
    assert canvas.frames[0].layers[0].image.pixelColor(10, 10) == QColor("#e03030")

    canvas.undo()
    assert canvas.frames[1].layers[0].image.pixelColor(18, 15) == QColor("white")


def test_reports_when_there_is_no_previous_cell(window):
    outlined_cell(window, 0)
    messages = []
    window.statusBar().messageChanged.connect(messages.append)

    window.a_auto_fill.trigger()

    assert any("前のセル" in message for message in messages)
