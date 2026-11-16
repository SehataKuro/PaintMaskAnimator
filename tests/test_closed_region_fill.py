"""線の切れ目を閉じて塗る（Issue #41）。

もとは「閉領域フィル」という別のツールだったが、バケツの「隙間閉じ」「線の延長」と
同じことなのでなくした。古い設定やショートカットから閉領域フィルが来たらバケツにする。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import toolpanel  # noqa: E402
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


def draw_leaky_box(window, left=10, top=10, size=20, gap_at=None):
    """1ドットだけ開いた四角い囲いを黒で描く。"""
    layer = window.canvas.frames[0].layers[0]
    black = QColor(0, 0, 0)
    for offset in range(size):
        layer.image.setPixelColor(left + offset, top, black)
        layer.image.setPixelColor(left + offset, top + size, black)
        layer.image.setPixelColor(left, top + offset, black)
        layer.image.setPixelColor(left + size, top + offset, black)
    if gap_at is not None:
        layer.image.setPixelColor(left + gap_at, top, QColor(0, 0, 0, 0))
    layer.has_content = True
    return layer


def test_the_closed_fill_tool_is_gone_and_falls_back_to_the_bucket(window):
    ids = [tool_id for tool_id, _label in toolpanel.TOOL_DEFINITIONS]
    assert "closed_fill" not in ids
    assert "closed_fill" not in window.tool_actions
    window.tools.select_tool("closed_fill")
    assert window.tools.active_tool == "bucket"
    assert window.canvas.tool == "bucket"


def test_the_bucket_with_gap_closing_holds_a_one_dot_gap(window):
    draw_leaky_box(window, gap_at=7)
    window.tools.bucket_close_gap.setChecked(True)
    window.tools.bucket_extend_lines.setChecked(True)
    window.tools.bucket_island_fill.setChecked(False)
    window.tools.bucket_gap_width.setValue(4)
    window.colors.apply_sampled_color_to_mode("main", QColor("#ff0000"))

    window.canvas.set_tool("bucket")
    window.canvas.flood_fill(QPoint(20, 20))

    image = window.canvas.frames[0].layers[0].image
    assert image.pixelColor(20, 20) == QColor("#ff0000")
    assert image.pixelColor(2, 2) != QColor("#ff0000"), "囲いの外へこぼれない"


def test_the_bucket_leaks_through_the_same_gap_without_gap_closing(window):
    draw_leaky_box(window, gap_at=7)
    window.tools.bucket_close_gap.setChecked(False)
    window.tools.bucket_extend_lines.setChecked(False)
    window.tools.bucket_island_fill.setChecked(False)
    window.colors.apply_sampled_color_to_mode("main", QColor("#00ff00"))

    window.canvas.set_tool("bucket")
    window.canvas.flood_fill(QPoint(20, 20))

    image = window.canvas.frames[0].layers[0].image
    assert image.pixelColor(2, 2) == QColor("#00ff00")
