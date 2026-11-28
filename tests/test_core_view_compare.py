"""Compare the Tauri canvas view (pma-core compose_view) with the Qt canvas.

0.9.0 の画面（app/src/view.rs）は、キャンバスの見え方（ブラックチェック・隣接色分け・
チャンネル・露光量・初期状態との比較）を pma-core の ``view_composite`` で元の解像度に
重ね、背景には表示プレビューを掛けた色（``view_background_color``）を敷く。それが Qt の
``draw_frame_direct``（画面と同じ大きさ）と同じ画素になるかを確かめる。

画素の比べ方と、±1 を許す理由は tests/test_core_compose_compare.py と同じ。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from paintmaskanimator import view_preview
from tests.test_core_compose_compare import (  # noqa: F401  (fixtures)
    HEIGHT,
    WIDTH,
    _apply_filter,
    _assert_same,
    _canvas_display,
    _core,
    _image,
    _options,
    _qt_pixels,
    _rust_pixels,
    _view,
    canvas,
    qapp,
)
import pma_native as native

PREVIEWS = [
    {},
    {"exposure": 1.2},
    {"exposure": -3.0, "channel": "green"},
    {"channel": "alpha"},
    {"map_colors": True},
    {"map_colors": True, "channel": "red"},
]


def _background(canvas):
    color = canvas.view_background_color()
    return (color.red(), color.green(), color.blue())


@pytest.mark.parametrize("preview", PREVIEWS)
@pytest.mark.parametrize("rgb", [(255, 255, 255), (12, 140, 230), (0, 0, 0)])
def test_view_background_matches_view_background_color(canvas, preview, rgb):
    from PySide6.QtGui import QColor

    canvas.transparent_display_color = QColor(*rgb)
    canvas.set_view_preview(**preview)
    expected = _background(canvas)
    assert native.compose_view_background(rgb, _view(canvas)) == expected
    assert expected == (
        view_preview.background_color(rgb, canvas.view_preview)
        if canvas.view_preview.adjusts_pixels else rgb
    )


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize("preview", PREVIEWS)
@pytest.mark.parametrize("name", ["none", "silhouette", "visible", "silhouette_visible"])
def test_view_composite_matches_draw_frame_direct(canvas, name, preview, initial):
    """画面の大きさが画像と同じとき、view.rs の合成は Qt が画面へ描く画素と同じ。"""
    _apply_filter(canvas, name)
    if initial:
        rng = np.random.default_rng(11)
        canvas.frames[0].layers[1].initial_image = _image(rng)
        canvas.show_initial_state = True
    canvas.set_view_preview(**preview)
    core = _core(canvas)
    background = _background(canvas)
    mapping = {}
    for active in (1, 2):
        canvas.active_layer_index = active
        for frame in range(len(canvas.frames)):
            expected = _qt_pixels(_canvas_display(canvas, frame, WIDTH, HEIGHT, background))
            actual = _rust_pixels(native.compose_view(
                core, frame, _options(canvas), _view(canvas), mapping, background))
            _assert_same(actual, expected, tolerant=True)
            # 線を描いた範囲だけの合成（recompose_rect）は全体を切り出したものと同じ。
            x, y, w, h = 3, 4, 11, 9
            part = _rust_pixels(native.compose_view(
                core, frame, _options(canvas), _view(canvas), mapping, background, (x, y, w, h)))
            _assert_same(part, expected[y:y + h, x:x + w], tolerant=True)
