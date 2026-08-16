"""色置換が一括処理ランナー経由で動くことのテスト。"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.undo_entries import ScopeBatchUndo


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    win = MainWindow()
    win.canvas._ensure_frame_count(3)
    for frame in win.canvas.frames:
        layer = frame.layers[0]
        layer.image.fill(QColor(255, 255, 255, 255))
        layer.image.setPixelColor(5, 5, QColor(10, 20, 30, 255))
        layer.has_content = True
        layer.exposure = 1
    yield win
    win.close()
    win.deleteLater()


def _pixel(window, frame_index):
    return window.canvas.frames[frame_index].layers[0].image.pixelColor(5, 5)


def test_replacement_applies_to_every_frame_and_undoes_once(window):
    applied = window.apply_palette_replacements(
        {(10, 20, 30): (200, 100, 0)}
    )

    assert applied
    for frame_index in range(len(window.canvas.frames)):
        assert _pixel(window, frame_index) == QColor(200, 100, 0, 255)

    entry = window.canvas.undo_stack[-1]
    assert isinstance(entry, ScopeBatchUndo)
    assert entry.history_label == "色置換"

    window.canvas.undo()
    for frame_index in range(len(window.canvas.frames)):
        assert _pixel(window, frame_index) == QColor(10, 20, 30, 255)


def test_replacement_reports_when_color_is_absent(window):
    assert not window.apply_palette_replacements(
        {(1, 2, 3): (4, 5, 6)}
    )
