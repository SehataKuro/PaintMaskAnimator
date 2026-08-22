"""シナリオ: 2色で描いて、使用色パネルと表示フィルターを使う。

「色を塗る → 使用色パネルに出る → 特定の色だけ表示する」という
色まわりの一巡を通す。使用色の認識はレイヤー画像の走査に依存するため、
実際に描いた画素から拾えていることを確認する。
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

pytestmark = pytest.mark.e2e

RED = (255, 0, 0)
BLUE = (0, 0, 255)


def _paint(app, rgb, offset):
    center_x, center_y = app.canvas_center()
    app.set_main_color(QColor(*rgb))
    app.stroke([(center_x + offset, center_y), (center_x + offset + 30, center_y)])


def test_painted_colors_appear_in_the_used_color_panel(app):
    app.select_tool("brush")
    _paint(app, RED, -80)
    _paint(app, BLUE, 40)

    app.refresh_used_colors()

    used = app.used_color_rgbs()
    assert RED in used
    assert BLUE in used


def test_visible_color_filter_hides_the_other_color(app):
    app.select_tool("brush")
    _paint(app, RED, -80)
    _paint(app, BLUE, 40)
    app.refresh_used_colors()

    app.show_only_colors({RED})

    assert app.canvas.visible_color_rgbs == {RED}

    # 全色を戻すとフィルター自体が無効化される（描画は一切変えない）。
    app.show_only_colors(app.used_color_rgbs())
    assert app.canvas.visible_color_rgbs is None
