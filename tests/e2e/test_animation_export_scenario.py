"""シナリオ: 2コマ描いて連番書き出しする。

「1コマ目を描く → コマを足す → 2コマ目を描く → 連番PNGで書き出す」という
制作の一巡を通し、ドキュメント状態と書き出し成果物の両方を確認する。
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

pytestmark = pytest.mark.e2e


def _draw_dot(app, offset: float) -> None:
    center_x, center_y = app.canvas_center()
    app.stroke([(center_x + offset, center_y), (center_x + offset + 20, center_y)])


def test_two_frame_animation_exports_png_sequence(app, tmp_path):
    app.select_tool("brush")
    app.set_main_color(QColor(0, 0, 0))

    _draw_dot(app, -60)
    app.add_frame(duplicate=True)
    assert app.frame_count == 2
    _draw_dot(app, 60)

    destination = tmp_path / "seq"
    app.export_key_sequence("PNG", destination)

    assert destination.is_dir()
    exported = sorted(destination.rglob("*.png"))
    assert exported, "連番PNGが1枚も書き出されていない"
    assert list(destination.rglob("*.csv")), "タイミングCSVが書き出されていない"
    assert not app.messages.of_level("critical")
    assert "連番書き出し完了" in app.messages.titles()


def test_added_frame_becomes_current(app):
    app.select_tool("brush")
    _draw_dot(app, 0)

    app.add_frame(duplicate=True)

    assert app.canvas.current_frame == 1
    assert app.frame_count == 2
