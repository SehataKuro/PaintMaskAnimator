"""シナリオ: 露出のあるキーから自由変形トゥイーンを作る。

「3コマ表示のキーを描く → トゥイーンを有効化 → 変形して確定」を通し、
区間内の各コマが画像キーフレームへ展開されることを確認する。
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

pytestmark = pytest.mark.e2e

EXPOSURE = 3


def test_tween_expands_the_exposure_into_key_frames(app):
    center_x, center_y = app.canvas_center()
    app.select_tool("brush")
    app.set_main_color(QColor(0, 0, 0))
    app.stroke([(center_x - 30, center_y), (center_x + 30, center_y)])

    app.set_exposure(frame=0, layer_index=0, exposure=EXPOSURE)
    assert app.frame_count >= EXPOSURE

    app.enable_tween(layer_index=0, key_column=0)
    assert app.canvas.transform_active, app.messages.titles()

    app.drag_transform((center_x, center_y), (center_x + 80, center_y))
    app.commit_transform()

    assert not app.canvas.transform_active
    filled = [
        index for index in range(EXPOSURE)
        if app.canvas.frames[index].layers[0].has_content
    ]
    assert filled == list(range(EXPOSURE)), "区間内のコマが画像キーになっていない"
    assert not app.messages.of_level("critical")


def test_tween_refuses_a_single_frame_key(app):
    center_x, center_y = app.canvas_center()
    app.select_tool("brush")
    app.stroke([(center_x - 30, center_y), (center_x + 30, center_y)])

    app.enable_tween(layer_index=0, key_column=0)

    assert not app.canvas.transform_active
    assert "トゥイーン" in app.messages.titles()
