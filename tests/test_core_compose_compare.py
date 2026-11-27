"""Compare pma-core's canvas compositing (compose) with the Python/Qt canvas.

同じ文書・コマを、今のキャンバス（QPainter）と pma-core の compose で描いて画素を比べる。
比較は乗算済みの RGBA（Qt の ARGB32_Premultiplied と同じ値）で行う。

Qt の画像変換（乗算済み → ストレート）は SIMD 版（NEON・SSE4）が浮動小数の逆数の
近似で割る。文書の画素は Qt で正規化済みなので Rust はその値をそのまま使い、Qt と
一致する（macOS arm64 ではこのファイルの全件がバイト単位で一致する）。ただし置換・
表示プレビューで色を変えた半透明の画素や、オニオンの合成結果をストレートへ戻すときは
Rust がスカラー版で割るので、CPU によっては ±1 違いうる。その比較だけ ±1 まで許す。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock

import numpy as np
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from paintmaskanimator import constants, display_shrink, preferences
from paintmaskanimator.canvas import PaintCanvas, _display_qimage
from paintmaskanimator.document_live import _live_of
from paintmaskanimator.models import Frame, Layer
from paintmaskanimator.pma_image import PmaImage
import pma_native as native

WIDTH, HEIGHT = 37, 23
PALETTE = [(255, 255, 255), (20, 20, 20), (230, 40, 60), (40, 200, 90), (30, 90, 220), (250, 210, 40)]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _image(rng, opaque=0.5, transparent=0.35, colors=PALETTE):
    """使用色の画素を主にした、半透明の画素も混ぜた絵。"""
    choice = rng.integers(0, len(colors), (HEIGHT, WIDTH))
    rgb = np.asarray(colors, dtype=np.uint8)[choice]
    roll = rng.random((HEIGHT, WIDTH))
    alpha = np.where(roll < transparent, 0, np.where(roll < transparent + opaque, 255,
                                                       rng.integers(1, 255, (HEIGHT, WIDTH))))
    rgba = np.concatenate([rgb, alpha[..., None].astype(np.uint8)], axis=2)
    image = PmaImage.blank(WIDTH, HEIGHT)
    image.write_rgba(np.ascontiguousarray(rgba))
    return image


def _layer(name, image, number):
    if number is None:
        return Layer(name, PmaImage.blank(WIDTH, HEIGHT))
    return Layer(name, image, has_content=True, sequence_number=number)


@pytest.fixture
def canvas(qapp, monkeypatch):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", WIDTH)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", HEIGHT)
    rng = np.random.default_rng(96)
    paper = _image(rng, opaque=1.0, transparent=0.0, colors=[(255, 255, 255)])
    paints = [_image(rng) for _ in range(3)]
    shades = [_image(rng, opaque=0.2, transparent=0.3) for _ in range(2)]
    draft = _image(rng, opaque=0.3)
    # 用紙・塗り（保持あり）・半透明（未使用あり）・下書きの4列、5コマ。
    numbers = [
        (1, 1, 1, 1),
        (1, 1, None, 1),
        (1, 2, 2, None),
        (1, 2, 1, 1),
        (1, 3, None, 1),
    ]
    frames = []
    for paint_number, shade_number in [(row[1], row[2]) for row in numbers]:
        frames.append(Frame([
            _layer("Paper", paper, 1),
            _layer("Paint", paints[paint_number - 1], paint_number),
            _layer("Shade", shades[(shade_number or 1) - 1], shade_number),
            _layer("Draft", draft, 1 if paint_number != 2 else None),
        ]))
    canvas = PaintCanvas()
    canvas.frames = frames
    layers = canvas.frames[0].layers
    layers[0].is_paper = True
    layers[2].opacity = 0.6
    layers[3].is_draft = True
    layers[3].opacity = 0.35
    canvas.current_frame = 2
    canvas.active_layer_index = 1
    try:
        yield canvas
    finally:
        canvas.deleteLater()


def _core(canvas):
    return _live_of(canvas._document)["state"].core


def _qt_pixels(image):
    """QImage を乗算済みの RGBA（R, G, B, A の順）の配列にする。"""
    image = _display_qimage(image).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    width, height = image.width(), image.height()
    rows = np.frombuffer(image.constBits(), np.uint8).reshape(height, image.bytesPerLine())
    return rows[:, :width * 4].reshape(height, width, 4)[..., [2, 1, 0, 3]].copy()


def _rust_pixels(result):
    width, height, data = result[-3:]
    return np.frombuffer(data, np.uint8).reshape(height, width, 4)


def _assert_same(actual, expected, *, tolerant=False):
    """画素を比べる。tolerant なら SIMD の割り算の違いの ±1 まで許す（docstring 参照）。"""
    assert actual.shape == expected.shape
    if not tolerant:
        np.testing.assert_array_equal(actual, expected)
        return
    # 色の判定が変わる（表示・非表示が入れ替わる）ほどの違いは許さない。
    assert np.abs(actual.astype(int) - expected.astype(int)).max(initial=0) <= 1


def _options(canvas):
    remap = getattr(canvas, "preview_color_remap", None) or {}
    visible = canvas.visible_color_rgbs
    return {
        "active_layer": canvas.active_layer_index,
        "visible_colors": None if visible is None else sorted(visible),
        "color_remap": sorted(remap.items()),
        "silhouette": bool(canvas.silhouette_non_background),
    }


FILTERS = {
    "none": {},
    "visible": {"visible_color_rgbs": {PALETTE[2], PALETTE[3], PALETTE[1]}},
    "remap": {"preview_color_remap": {PALETTE[2]: PALETTE[4], PALETTE[4]: PALETTE[5]}},
    "visible_remap": {"visible_color_rgbs": {PALETTE[2], PALETTE[4]},
                      "preview_color_remap": {PALETTE[2]: (1, 2, 3)}},
    "silhouette": {"silhouette_non_background": True},
    "silhouette_visible": {"silhouette_non_background": True,
                           "visible_color_rgbs": {PALETTE[3]}},
}


def _apply_filter(canvas, name):
    for key, value in FILTERS[name].items():
        setattr(canvas, key, value)
    if name in ("visible", "visible_remap"):
        # 下の列（アクティブでない）の色フィルタも混ぜる。
        canvas.frames[0].layers[2].color_filter_enabled = True
        canvas.frames[0].layers[2].color_filter_rgb = PALETTE[3]


@pytest.mark.parametrize("white", [True, False])
@pytest.mark.parametrize("name", list(FILTERS))
def test_composite_matches_canvas(canvas, name, white):
    _apply_filter(canvas, name)
    core = _core(canvas)
    tolerant = name not in ("none", "silhouette")
    for active in (1, 2, 3):
        canvas.active_layer_index = active
        for frame in range(len(canvas.frames)):
            expected = _qt_pixels(canvas.composite(frame, white))
            actual = _rust_pixels(native.compose_frame(
                core, frame, _options(canvas), "white" if white else "transparent"))
            _assert_same(actual, expected, tolerant=tolerant)


def test_composite_rect_matches_a_crop(canvas):
    _apply_filter(canvas, "visible_remap")
    core = _core(canvas)
    expected = _qt_pixels(canvas.composite(3, True))
    for rect in [(0, 0, WIDTH, HEIGHT), (3, 4, 10, 7), (WIDTH - 1, HEIGHT - 1, 1, 1), (5, 2, 0, 3)]:
        x, y, width, height = rect
        actual = _rust_pixels(native.compose_frame(core, 3, _options(canvas), "white", rect))
        _assert_same(actual, expected[y:y + height, x:x + width], tolerant=True)
    with pytest.raises(ValueError):
        native.compose_frame(core, 3, _options(canvas), "white", (WIDTH - 2, 0, 3, 1))


def test_hidden_layers_and_opacity_follow_the_document(canvas):
    canvas.frames[0].layers[1].visible = False
    canvas.frames[0].layers[0].opacity = 0.25
    core = _core(canvas)
    for frame in range(len(canvas.frames)):
        expected = _qt_pixels(canvas.composite(frame, False))
        actual = _rust_pixels(native.compose_frame(core, frame, _options(canvas)))
        _assert_same(actual, expected)
    assert native.compose_layer(core, 0, 1, _options(canvas)) is None


@pytest.mark.parametrize("name", list(FILTERS))
def test_layer_images_match_display_layer_image(canvas, name):
    _apply_filter(canvas, name)
    core = _core(canvas)
    tolerant = name not in ("none", "silhouette")
    for frame in range(len(canvas.frames)):
        for index in range(4):
            key = canvas.resolve_key_frame(frame, index)
            result = native.compose_layer(core, frame, index, _options(canvas))
            if key is None:
                assert result is None
                continue
            assert result is not None
            layer = canvas.frames[key].layers[index]
            expected = _qt_pixels(canvas._display_layer_image(layer, index))
            assert result[:3] == (index, key, layer.opacity)
            _assert_same(_rust_pixels(result), expected, tolerant=tolerant)


def _view(canvas):
    preview = canvas.view_preview
    return {
        "show_initial_state": bool(canvas.show_initial_state),
        "exposure": preview.exposure, "channel": preview.channel,
        "map_colors": preview.map_colors, "palette": list(preview.palette),
    }


def _canvas_view_layer(canvas, frame, index):
    """draw_frame_direct がレイヤー index に描く画像（縮小前）。"""
    key = canvas.resolve_key_frame(frame, index)
    if key is None:
        return None
    layer = canvas.frames[key].layers[index]
    if not layer.visible:
        return None
    draw_image, source_key = canvas._model_display_source(layer, index)
    image = canvas.view_layer_image(layer, index, draw_image, source_key=source_key)
    return _display_qimage(image)


PREVIEWS = [
    {},
    {"exposure": 1.5},
    {"exposure": -2.0},
    {"channel": "red"},
    {"channel": "alpha"},
    {"exposure": 0.7, "channel": "blue"},
    {"map_colors": True},
    {"map_colors": True, "exposure": 1.0},
]


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize("preview", PREVIEWS)
def test_view_layers_match_draw_frame_direct_sources(canvas, preview, initial):
    _apply_filter(canvas, "visible")
    if initial:
        rng = np.random.default_rng(5)
        canvas.frames[0].layers[1].initial_image = _image(rng)
        canvas.show_initial_state = True
    canvas.set_view_preview(**preview)
    core = _core(canvas)
    mapping = {}
    for frame in range(len(canvas.frames)):
        actual = {
            entry[0]: entry
            for entry in native.compose_view_layers(
                core, frame, _options(canvas), _view(canvas), mapping)
        }
        for index in range(4):
            expected = _canvas_view_layer(canvas, frame, index)
            if expected is None:
                assert index not in actual
                continue
            result = actual[index]
            assert result[3:5] == (0, 0)
            _assert_same(_rust_pixels(result), _qt_pixels(expected), tolerant=True)
    if preview.get("map_colors"):
        # 隣接色分けの対応はキャンバスの対応と同じ割り当てになる。
        assert mapping == canvas._view_map_color_mappings[canvas.view_preview.palette]


def _canvas_display(canvas, frame, width, height, background, onion_draws=()):
    """draw_frame_direct（とオニオン）を width × height の画像に描く。"""
    result = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(background[0], background[1], background[2]))
    painter = QPainter(result)
    target = QRectF(0, 0, width, height)
    canvas.draw_frame_direct(painter, target, frame)
    for onion_frame, opacity, color, enabled in onion_draws:
        onion = canvas._tinted_onion_image(
            onion_frame, color, enabled, canvas.onion_selected_colors_only,
            target_size=(width, height))
        if canvas.flip_horizontal or canvas.flip_vertical:
            onion = onion.mirrored(canvas.flip_horizontal, canvas.flip_vertical)
        painter.setOpacity(opacity)
        painter.drawImage(target, onion)
    painter.end()
    return result


@pytest.mark.parametrize("flips", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("mode", display_shrink.MODES)
@pytest.mark.parametrize("size", [(WIDTH, HEIGHT), (18, 11), (9, 5), (25, 20), (1, 1)])
def test_display_frame_matches_draw_frame_direct(canvas, monkeypatch, size, mode, flips):
    monkeypatch.setattr(preferences, "display_shrink_mode", lambda: mode)
    canvas.flip_horizontal, canvas.flip_vertical = flips
    canvas.set_view_preview(exposure=0.5)
    _apply_filter(canvas, "visible")
    core = _core(canvas)
    background = (200, 210, 220)
    for frame in range(len(canvas.frames)):
        expected = _qt_pixels(_canvas_display(canvas, frame, size[0], size[1], background))
        actual = _rust_pixels(native.compose_display(
            core, frame, size[0], size[1], _options(canvas), _view(canvas), {}, background, mode,
            flips[0], flips[1]))
        _assert_same(actual, expected, tolerant=True)


@pytest.mark.parametrize("flips", [(False, False), (False, True)])
@pytest.mark.parametrize("mode", display_shrink.MODES)
def test_scale_for_display_matches_display_image_for_rect(canvas, monkeypatch, mode, flips):
    monkeypatch.setattr(preferences, "display_shrink_mode", lambda: mode)
    canvas.flip_horizontal, canvas.flip_vertical = flips
    rng = np.random.default_rng(3)
    for width, height in [(37, 23), (64, 7), (5, 40), (100, 60)]:
        rgba = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
        rgba[..., 3] = np.where(rng.random((height, width)) < 0.5, 255, rgba[..., 3])
        source = PmaImage.blank(width, height)
        source.write_rgba(rgba)
        premul = _qt_pixels(source)
        for target in [(1, 1), (width // 2, height // 3 + 1), (width - 1, height), (3, 2)]:
            expected = _qt_pixels(canvas._display_image_for_rect(
                source, QRectF(0, 0, *target)))
            actual = _rust_pixels(native.compose_scale_for_display(
                width, height, premul.tobytes(), *target, mode, *flips))
            _assert_same(actual, expected, tolerant=mode == display_shrink.MODE_KEEP_LINES)


def _onion(canvas):
    def side(prefix):
        return {
            "count": getattr(canvas, f"onion_{prefix}_count"),
            "opacity": getattr(canvas, f"onion_{prefix}_opacity"),
            "color": getattr(canvas, f"onion_{prefix}_color").getRgb()[:3],
            "color_enabled": getattr(canvas, f"onion_{prefix}_color_enabled"),
            "levels": list(getattr(canvas, f"onion_{prefix}_levels")),
        }

    return {
        "previous": side("previous"), "next": side("next"),
        "all_layers": canvas.onion_all_layers,
        "selected_colors_only": canvas.onion_selected_colors_only,
        "selected_colors": sorted(canvas.selected_used_color_rgbs),
    }


def _canvas_onion_draws(canvas, width, height):
    """_draw_onion_range が描くオニオン [(コマ, 不透明度, 色, 着色)] を描く順に集める。"""
    draws = []
    original = canvas._tinted_onion_image

    def record(frame, color, enabled=True, selected=False, target_size=None):
        draws.append([frame, None, QColor(color), enabled])
        return original(frame, color, enabled, selected, target_size=target_size)

    painter = MagicMock()
    painter.setOpacity.side_effect = lambda value: draws and draws[-1][1] is None and (
        draws[-1].__setitem__(1, value))
    canvas._tinted_onion_image = record
    try:
        for direction, prefix in ((-1, "previous"), (1, "next")):
            canvas._draw_onion_range(
                painter, QRectF(0, 0, width, height), direction,
                getattr(canvas, f"onion_{prefix}_count"),
                getattr(canvas, f"onion_{prefix}_opacity"),
                getattr(canvas, f"onion_{prefix}_color"),
                getattr(canvas, f"onion_{prefix}_color_enabled"),
                canvas.onion_selected_colors_only,
            )
    finally:
        del canvas._tinted_onion_image
    return [tuple(draw) for draw in draws]


ONIONS = [
    {},
    {"onion_all_layers": False},
    {"onion_previous_count": 3, "onion_next_count": 2, "onion_previous_levels": [80, 40],
     "onion_next_color_enabled": True, "onion_previous_color_enabled": True},
    {"onion_selected_colors_only": True, "selected_used_color_rgbs": {PALETTE[2], PALETTE[3]},
     "onion_next_color_enabled": True, "onion_next_opacity": 0.9},
    {"onion_all_layers": False, "onion_previous_opacity": 0.0, "onion_next_count": 4},
]


@pytest.mark.parametrize("current", [0, 2, 4])
@pytest.mark.parametrize("settings", ONIONS)
def test_onion_plan_and_images_match_canvas(canvas, settings, current):
    for key, value in settings.items():
        setattr(canvas, key, value)
    _apply_filter(canvas, "visible")
    canvas.current_frame = current
    core = _core(canvas)
    expected = _canvas_onion_draws(canvas, WIDTH, HEIGHT)
    draws = native.compose_onion_draws(core, current, _onion(canvas), _options(canvas))
    assert [(frame, opacity) for frame, _, _, opacity in draws] == [
        (frame, opacity) for frame, opacity, _, _ in expected]
    for direction in (-1, 1):
        count = canvas.onion_previous_count if direction < 0 else canvas.onion_next_count
        indices = canvas._onion_frame_indices(direction, count)
        drawn = [(frame, distance) for frame, side, distance, _ in draws if side == direction]
        # 濃さ 0 の絵は省くので、描く絵は近い順の一覧の一部。
        assert all(indices[distance - 1] == frame for frame, distance in drawn)
    selected = sorted(canvas.selected_used_color_rgbs) if canvas.onion_selected_colors_only else None
    for frame, _, color, enabled in expected:
        rgb = color.getRgb()[:3]
        for target in (None, (WIDTH, HEIGHT), (17, 9)):
            reference = canvas._tinted_onion_image(
                frame, color, enabled, canvas.onion_selected_colors_only, target_size=target)
            actual = native.compose_tinted_onion(
                core, frame, rgb, enabled, selected, canvas.onion_all_layers, target,
                _options(canvas))
            _assert_same(_rust_pixels(actual), _qt_pixels(reference), tolerant=True)


@pytest.mark.parametrize("size", [(WIDTH, HEIGHT), (20, 12)])
def test_display_frame_with_onion_matches_paint_order(canvas, size):
    canvas.onion_previous_count = 2
    canvas.onion_next_color_enabled = True
    canvas.current_frame = 2
    canvas.flip_horizontal = True
    core = _core(canvas)
    width, height = size
    expected_draws = _canvas_onion_draws(canvas, width, height)
    expected = _qt_pixels(_canvas_display(canvas, 2, width, height, (255, 255, 255), expected_draws))
    actual = _rust_pixels(native.compose_display(
        core, 2, width, height, _options(canvas), _view(canvas), None, "white", "nearest", True, False,
        _onion(canvas)))
    _assert_same(actual, expected, tolerant=True)
