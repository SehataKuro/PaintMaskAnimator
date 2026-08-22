"""シナリオ: 選択して動かす。

「線を引く → 矩形選択で囲む → 自由変形で移動 → 確定」まで通し、
絵が元の位置から消えて移動先に現れることを確認する。
選択・変形は壊れても気付きにくいわりに被害が大きいので、
ドキュメント上の画素で直接確かめる。
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

pytestmark = pytest.mark.e2e

SHIFT = 120.0


def test_selected_area_moves_and_commits(app):
    center_x, center_y = app.canvas_center()
    app.select_tool("brush")
    app.set_main_color(QColor(0, 0, 0))
    app.stroke([(center_x - 30, center_y), (center_x + 30, center_y)])
    drawn = app.pixel((center_x, center_y))
    assert drawn.alpha() > 0

    app.select_rect((center_x - 60, center_y - 40), (center_x + 60, center_y + 40))
    assert app.canvas.selection_polygon

    app.start_transform("free")
    assert app.canvas.transform_active

    app.drag_transform((center_x, center_y), (center_x + SHIFT, center_y))
    app.commit_transform()

    assert not app.canvas.transform_active
    assert app.pixel((center_x + SHIFT, center_y)).alpha() > 0
    assert app.pixel((center_x, center_y)).alpha() == 0
    assert not app.messages.of_level("critical")


def test_cancelled_transform_leaves_the_drawing_untouched(app):
    center_x, center_y = app.canvas_center()
    app.select_tool("brush")
    app.set_main_color(QColor(0, 0, 0))
    app.stroke([(center_x - 30, center_y), (center_x + 30, center_y)])
    drawn = app.pixel((center_x, center_y))

    app.select_rect((center_x - 60, center_y - 40), (center_x + 60, center_y + 40))
    app.start_transform("free")
    app.drag_transform((center_x, center_y), (center_x + SHIFT, center_y))
    app.window.cancel_transform_or_tween()
    app.process_events()

    assert not app.canvas.transform_active
    assert app.pixel((center_x, center_y)) == drawn
    assert app.pixel((center_x + SHIFT, center_y)).alpha() == 0
