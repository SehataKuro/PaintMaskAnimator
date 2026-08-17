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


def test_switch_history_branch_invalidates_display_cache():
    _app()
    canvas = PaintCanvas()
    _paint(canvas, QRect(10, 10, 8, 8), "red")
    canvas.undo()
    _paint(canvas, QRect(40, 40, 8, 8), "blue")

    # 表示キャッシュに何か溜めておく（cacheKey 使い回しによる残像の元）。
    canvas._color_filter_cache[("stale", 1, 1, None, None, None)] = object()
    canvas._color_index_cache[("stale", 1, 1)] = object()
    canvas._silhouette_cache[("stale", 1, 1)] = object()
    canvas._onion_cache[("stale",)] = object()

    assert canvas.switch_history_branch(0) is True
    # 分岐切り替えで表示キャッシュが捨てられ、別分岐の残像が残らない。
    assert canvas._color_filter_cache == {}
    assert canvas._color_index_cache == {}
    assert canvas._silhouette_cache == {}
    assert canvas._onion_cache == {}


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
    main_cells = {(n["col"], n["row"]) for n in nodes if n["kind"] == "main"}
    assert len(branch_nodes) == 1
    # 分岐の先頭ブロックは、分かれた本線ブロックからカーブで枝分かれする。
    assert branch_nodes[0]["curve"] is True
    assert branch_nodes[0]["from"] in main_cells
    # 分岐は本線（列0）とは別の列に置かれる。
    assert branch_nodes[0]["col"] >= 1
    panel.deleteLater()


def test_history_panel_expands_multi_step_branch():
    _app()
    canvas = PaintCanvas()
    # 2手進めてから2手戻り、別の編集をして2手ぶんの分岐を作る。
    _paint(canvas, QRect(10, 10, 8, 8), "red")
    _paint(canvas, QRect(20, 20, 8, 8), "green")
    canvas.undo()
    canvas.undo()
    _paint(canvas, QRect(40, 40, 8, 8), "blue")

    panel = HistoryPanel()
    panel.set_canvas(canvas)

    branch_nodes = [n for n in panel.graph._nodes if n["kind"] == "branch"]
    # 捨てられた未来（赤・緑の2手）が1手ずつのブロックとして展開される。
    assert len(branch_nodes) == 2
    # 同じ分岐は同じ列に縦に並び、2番目以降は縦線（カーブではない）でつながる。
    assert branch_nodes[0]["col"] == branch_nodes[1]["col"]
    assert branch_nodes[0]["curve"] is True
    assert branch_nodes[1]["curve"] is False
    assert branch_nodes[1]["from"] == (
        branch_nodes[0]["col"], branch_nodes[0]["row"]
    )
    assert branch_nodes[1]["row"] == branch_nodes[0]["row"] + 1
    panel.deleteLater()


def test_clicking_branch_block_lands_on_that_state():
    _app()
    canvas = PaintCanvas()
    red_rect = QRect(10, 10, 8, 8)
    green_rect = QRect(20, 20, 8, 8)
    blue_rect = QRect(40, 40, 8, 8)
    # 赤→緑まで進めてから2手戻り、青を描いて赤・緑の2手ぶんの分岐を作る。
    _paint(canvas, red_rect, "red")
    _paint(canvas, green_rect, "green")
    canvas.undo()
    canvas.undo()
    _paint(canvas, blue_rect, "blue")

    panel = HistoryPanel()
    panel.set_canvas(canvas)

    branch_nodes = [n for n in panel.graph._nodes if n["kind"] == "branch"]
    # 分岐点に近い順（赤=1手, 緑=2手）にRedo回数が振られている。
    assert [n["steps"] for n in branch_nodes] == [1, 2]

    # 2番目のブロック（緑）をクリックしたときの処理相当：分岐へ切り替え、
    # steps回Redoして、クリックした緑の状態まで進む。
    green_block = branch_nodes[1]
    assert canvas.switch_history_branch(green_block["value"]) is True
    for _ in range(green_block["steps"]):
        canvas.redo()
    assert canvas.active_layer.image.pixelColor(green_rect.center()) == QColor("green")
    assert canvas.active_layer.image.pixelColor(red_rect.center()) == QColor("red")
    panel.deleteLater()


def test_history_panel_spreads_sibling_branches_into_columns():
    _app()
    canvas = PaintCanvas()
    # 同じ分岐点から3つの未来を作る：戻る→編集、を繰り返す。
    _paint(canvas, QRect(10, 10, 8, 8), "red")
    canvas.undo()
    _paint(canvas, QRect(20, 20, 8, 8), "green")
    canvas.undo()
    _paint(canvas, QRect(30, 30, 8, 8), "blue")

    panel = HistoryPanel()
    panel.set_canvas(canvas)

    branch_nodes = [n for n in panel.graph._nodes if n["kind"] == "branch"]
    # 同じ分岐点から出た2つの分岐が、別々の列に横並びになる。
    assert len(branch_nodes) == 2
    cols = {n["col"] for n in branch_nodes}
    assert len(cols) == 2
    assert all(c >= 1 for c in cols)
    panel.deleteLater()
