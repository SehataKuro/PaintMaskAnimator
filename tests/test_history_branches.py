"""Undo後の編集で捨てられる未来を、分岐として残せることの確認。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication

from paintmaskanimator.canvas import PaintCanvas
from paintmaskanimator.history_panel import HistoryPanel


def _app():
    return QApplication.instance() or QApplication([])


def _paint(canvas, rect, color):
    """1手ぶんの編集（Undoエントリ付き）を行う。"""
    canvas.push_layer_region_undo(rect)
    painter = QPainter(canvas.active_layer.image)
    painter.fillRect(rect, QColor(color))
    painter.end()
    canvas.active_layer.has_content = True


def test_new_edit_after_undo_keeps_old_future_as_branch():
    _app()
    canvas = PaintCanvas()
    red_rect = QRect(10, 10, 8, 8)
    blue_rect = QRect(40, 40, 8, 8)

    _paint(canvas, red_rect, "red")
    canvas.undo()
    assert canvas.redo_stack

    _paint(canvas, blue_rect, "blue")
    assert canvas.redo_stack == []
    assert len(canvas.history_branches) == 1
    branch = canvas.history_branches[0]
    assert branch.position == len(canvas.undo_stack) - 1
    assert len(branch.entries) == 1


def test_switch_history_branch_restores_the_other_future():
    _app()
    canvas = PaintCanvas()
    red_rect = QRect(10, 10, 8, 8)
    blue_rect = QRect(40, 40, 8, 8)

    _paint(canvas, red_rect, "red")
    canvas.undo()
    _paint(canvas, blue_rect, "blue")
    assert canvas.active_layer.image.pixelColor(blue_rect.center()) == QColor("blue")

    assert canvas.switch_history_branch(0) is True
    # 青の未来が入れ替わりに分岐として残り、赤へ進めるようになる。
    assert len(canvas.history_branches) == 1
    assert canvas.redo_stack
    canvas.redo()
    assert canvas.active_layer.image.pixelColor(red_rect.center()) == QColor("red")

    # 元の（青の）分岐へも戻れる。
    assert canvas.switch_history_branch(0) is True
    canvas.redo()
    assert canvas.active_layer.image.pixelColor(blue_rect.center()) == QColor("blue")


def test_switch_history_branch_rejects_bad_index():
    _app()
    canvas = PaintCanvas()
    assert canvas.switch_history_branch(0) is False
    assert canvas.switch_history_branch(-1) is False


def test_history_panel_shows_branch_under_its_divergence_point():
    _app()
    canvas = PaintCanvas()
    _paint(canvas, QRect(10, 10, 8, 8), "red")
    canvas.undo()
    _paint(canvas, QRect(40, 40, 8, 8), "blue")

    panel = HistoryPanel()
    panel.set_canvas(canvas)

    nodes = panel.graph._nodes
    branch_nodes = [n for n in nodes if n["kind"] == "branch"]
    main_ys = {n["y"] for n in nodes if n["kind"] == "main"}
    assert len(branch_nodes) == 1
    # 分岐ノードは、それが分かれた本線ノードにぶら下がる（親のyに紐づく）。
    assert branch_nodes[0]["parent_y"] in main_ys
    # 分岐は本線より一段インデントされ、本線ノードとは別位置に描かれる。
    assert branch_nodes[0]["lane"] == 1
    panel.deleteLater()
