"""シナリオ: 描く → 保存 → 開き直す。

「起動したユーザーがブラシで線を引き、名前を付けて保存し、開き直しても絵が残っている」
というアプリの背骨に当たる一連の流れを、実マウスイベントで通す。
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

pytestmark = pytest.mark.e2e


def test_brush_stroke_marks_the_layer(app):
    center_x, center_y = app.canvas_center()
    app.select_tool("brush")
    app.set_main_color(QColor(0, 0, 0))

    app.stroke([(center_x - 40, center_y), (center_x + 40, center_y)])

    assert app.canvas.layers[0].has_content
    assert app.pixel((center_x, center_y)).alpha() > 0


def test_stroke_survives_save_and_reopen(app, tmp_path):
    center_x, center_y = app.canvas_center()
    app.select_tool("brush")
    app.set_main_color(QColor(0, 0, 0))
    app.stroke([(center_x - 40, center_y), (center_x + 40, center_y)])
    drawn = app.pixel((center_x, center_y))

    path = tmp_path / "scenario.pman"
    assert app.save_project_as(path)
    assert path.exists()

    app.new_document()
    assert app.pixel((center_x, center_y)).alpha() == 0

    app.open_project(path)

    assert app.pixel((center_x, center_y)) == drawn
