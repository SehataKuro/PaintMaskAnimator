"""シナリオ: 筆圧ペンで描く。

タブレット入力は QTest では合成できないため QTabletEvent を直接投げる。
弱い筆圧の線が強い筆圧の線より細くなる、という筆圧設定の効き目を確認する。
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

pytestmark = pytest.mark.e2e


def _stroke_width(app, x_center: float, y: float, span: int = 60) -> int:
    """線に直交する方向へ走査して、塗られている画素数を数える。"""
    return sum(
        1 for offset in range(-span, span + 1)
        if app.pixel((x_center, y + offset)).alpha() > 0
    )


def test_pen_pressure_changes_the_stroke_width(app):
    center_x, center_y = app.canvas_center()
    app.select_tool("brush")
    app.set_main_color(QColor(0, 0, 0))

    light_y = center_y - 100
    heavy_y = center_y + 100
    app.tablet_stroke(
        [(center_x - 60, light_y), (center_x + 60, light_y)], [0.1, 0.1]
    )
    app.tablet_stroke(
        [(center_x - 60, heavy_y), (center_x + 60, heavy_y)], [1.0, 1.0]
    )

    light = _stroke_width(app, center_x, light_y)
    heavy = _stroke_width(app, center_x, heavy_y)
    assert light > 0, "弱い筆圧で何も描かれていない"
    assert heavy > light, f"筆圧が線幅に効いていない（弱={light} 強={heavy}）"
