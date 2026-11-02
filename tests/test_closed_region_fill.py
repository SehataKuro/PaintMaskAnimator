"""閉領域フィルツール（Issue #41）。

バケツでは1ドットの隙間からこぼれてしまう囲いを、隙間を閉じて塗る。
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


def test_the_tool_is_offered_next_to_the_bucket(window):
    ids = [tool_id for tool_id, _label in toolpanel.TOOL_DEFINITIONS]
    assert ids.index("closed_fill") == ids.index("bucket") + 1
    assert toolpanel.tool_label("closed_fill") == "閉領域フィル"
    assert "closed_fill" in window.tool_actions


def test_it_always_closes_gaps_whatever_the_bucket_is_set_to(window):
    window.tools.bucket_close_gap.setChecked(False)
    window.tools.bucket_island_fill.setChecked(True)

    window.canvas.set_tool("bucket")
    assert not window.canvas.bucket_options().close_gap

    window.canvas.set_tool("closed_fill")
    options = window.canvas.bucket_options()
    assert options.close_gap
    assert options.extend_lines
    # 塗り残し拾いは必要以上に広がるので、こちらでは使わない。
    assert options.island_max_width == 0


def test_it_fills_a_box_that_leaks_through_a_one_dot_gap(window):
    draw_leaky_box(window, gap_at=7)
    window.tools.bucket_gap_width.setValue(4)
    window.colors.apply_sampled_color_to_mode("main", QColor("#ff0000"))

    window.canvas.set_tool("closed_fill")
    window.canvas.flood_fill(QPoint(20, 20))

    image = window.canvas.frames[0].layers[0].image
    # 囲いの中は塗られ、外へはこぼれない。
    assert image.pixelColor(20, 20) == QColor("#ff0000")
    assert image.pixelColor(2, 2) != QColor("#ff0000")


def test_the_bucket_leaks_through_the_same_gap_without_its_aids(window):
    """道具を分けた理由：バケツは設定しだいでこぼれる。

    閉領域フィルは同じ設定でもこぼれない（上のテスト）。
    """
    draw_leaky_box(window, gap_at=7)
    window.tools.bucket_close_gap.setChecked(False)
    window.tools.bucket_extend_lines.setChecked(False)
    window.tools.bucket_island_fill.setChecked(False)
    window.colors.apply_sampled_color_to_mode("main", QColor("#00ff00"))

    window.canvas.set_tool("bucket")
    window.canvas.flood_fill(QPoint(20, 20))

    image = window.canvas.frames[0].layers[0].image
    assert image.pixelColor(2, 2) == QColor("#00ff00")


def test_the_closed_fill_holds_the_same_gap(window):
    draw_leaky_box(window, gap_at=7)
    window.tools.bucket_close_gap.setChecked(False)
    window.tools.bucket_extend_lines.setChecked(False)
    window.tools.bucket_island_fill.setChecked(False)
    window.colors.apply_sampled_color_to_mode("main", QColor("#0000ff"))

    window.canvas.set_tool("closed_fill")
    window.canvas.flood_fill(QPoint(20, 20))

    image = window.canvas.frames[0].layers[0].image
    assert image.pixelColor(20, 20) == QColor("#0000ff")
    assert image.pixelColor(2, 2) != QColor("#0000ff")
