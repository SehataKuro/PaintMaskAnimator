"""シナリオ: 描く → 元に戻す → やり直す。

編集・取り消し・再実行がドキュメントとメニューの活性状態の両方で
一貫していることを確認する。
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

pytestmark = pytest.mark.e2e


def test_undo_and_redo_restore_the_stroke(app):
    center_x, center_y = app.canvas_center()
    app.select_tool("brush")
    app.set_main_color(QColor(0, 0, 0))
    assert app.undo_depth() == 0

    app.stroke([(center_x - 40, center_y), (center_x + 40, center_y)])
    drawn = app.pixel((center_x, center_y))
    assert drawn.alpha() > 0
    assert app.undo_depth() == 1

    app.trigger_action("a_undo")
    assert app.pixel((center_x, center_y)).alpha() == 0
    assert app.undo_depth() == 0 and app.redo_depth() == 1

    app.trigger_action("a_redo")
    assert app.pixel((center_x, center_y)) == drawn
