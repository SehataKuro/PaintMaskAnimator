"""Compare pma-core's bucket tools (tools::bucket / tools::skewer) with the Python canvas.

同じ文書・設定・クリック（なぞり）を、今のキャンバス（``flood_fill``・``begin_bucket_drag``
〜``finish_bucket_drag``・串刺し塗り）と pma-core の tools へ渡し、塗ったあとの文書
（全セルの画素と、位置の絵の有無などのグラフ）が同じになることを確かめる。

Rust 側は、Python が塗る直前の文書の写し（``_CoreDocument.copy``）に塗る。設定は
キャンバスが実際に使った ``bucket_options()`` をそのまま辞書にして渡す（ツールパネルからの
読み出しは画面の役目なので、ここでは比べない）。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses
import json

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator import bucket_fill, constants, imaging
from paintmaskanimator.document import create_drawing
from paintmaskanimator.document_live import _live_of
from paintmaskanimator.main_window import MainWindow
import pma_native as native

WIDTH, HEIGHT = 96, 64
BLUE = QColor("#3355ff")
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)
RED = (230, 20, 20, 255)
SKIN = (240, 200, 170, 255)
SUB = (255, 0, 0)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(constants, "CANVAS_WIDTH", WIDTH)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", HEIGHT)
    window = MainWindow()
    canvas = window.canvas
    canvas.set_tool("bucket")
    canvas.color_mode = "main"
    canvas.main_color = BLUE
    window.tools.bucket_all_frames.setChecked(False)
    try:
        yield window
    finally:
        window.deleteLater()
        qapp.processEvents()


def _box(rgba, x0, y0, x1, y1, color=BLACK, gap=None):
    """(x0, y0)〜(x1, y1)（両端を含む）の枠。gap は上の辺に空ける (開始, 幅)。"""
    rgba[y0, x0:x1 + 1] = color
    rgba[y1, x0:x1 + 1] = color
    rgba[y0:y1 + 1, x0] = color
    rgba[y0:y1 + 1, x1] = color
    if gap is not None:
        start, size = gap
        rgba[y0, start:start + size] = WHITE


def _scene(seed=0, shift=0):
    """白地に、閉じた枠・隙間のある枠・斜線の島・色の面・半透明・透明・端へ伸びる線の絵。"""
    rng = np.random.default_rng(seed)
    rgba = np.zeros((HEIGHT, WIDTH, 4), np.uint8)
    rgba[:] = WHITE
    _box(rgba, 3 + shift, 3, 20 + shift, 18)
    # 枠の中の赤い斜線（囲まれた島）。
    for i in range(6):
        rgba[8 + i, 8 + shift + i] = RED
    _box(rgba, 26, 3, 44, 20, gap=(33, 2))
    # 色を塗った面と、その中の含む色（サブカラー）の部分。
    _box(rgba, 50, 3, 70, 20)
    rgba[4:20, 51:70] = SKIN
    rgba[10:14, 56:62] = (*SUB, 255)
    # 角で接する細い隙間（塗り残し拾い）。
    _box(rgba, 74, 3, 92, 20)
    rgba[11, 75:92] = BLACK
    rgba[11, 83] = WHITE
    # 半透明と透明。
    rgba[26:40, 4:24, 3] = rng.integers(1, 255, (14, 20))
    rgba[26:40, 4:24, :3] = (40, 120, 200)
    rgba[42:60, 4:24] = (0, 0, 0, 0)
    _box(rgba, 8, 46, 18, 56)
    # 端の手前で途切れた線（線の延長）。
    rgba[30:HEIGHT - 4, 40] = BLACK
    rgba[30, 30:WIDTH - 5] = BLACK
    rgba[45:HEIGHT, 70] = BLACK
    # まばらな点と、黒に近い濃い線。
    for x, y in rng.integers((30, 34), (WIDTH - 2, HEIGHT - 2), (25, 2)):
        rgba[y, x] = (30, 30, 30, 255)
    return rgba


def _install(window, frame, rgba, number):
    canvas = window.canvas
    layer = canvas.frames[frame].layers[canvas.active_layer_index]
    create_drawing(layer, imaging.rgba_array_to_qimage(np.ascontiguousarray(rgba)), number=number)
    return layer


def _core(window):
    """キャンバスの文書が持つ pma-core の文書。素のグラフに降りていれば Rust に戻す。"""
    document = window.canvas._document
    assert document._promote(), "文書を pma-core で表せない"
    return _live_of(document)["state"].core


def _capture_colors(window):
    """キャンバスが使用色へ知らせた RGB を集める。"""
    captured = []
    window.canvas._emit_actual_paint_colors = (
        lambda colors, maximum=64: captured.append(tuple(tuple(c) for c in colors)))
    return captured


def _settings(window):
    canvas = window.canvas
    values = dataclasses.asdict(canvas.bucket_options())
    color = canvas.paint_source_color()
    values["color"] = (color.red(), color.green(), color.blue())
    values["opacity"] = float(getattr(canvas, "fill_opacity", 1.0))
    return values


def _selection(window):
    mask = window.canvas.selection_mask_bool(WIDTH, HEIGHT)
    return None if mask is None else np.ascontiguousarray(mask, dtype=np.uint8).tobytes()


def _assert_same(expected, actual):
    """文書のグラフ（位置の内容の印など）と全画像の画素が同じ。"""
    expected_graph, expected_images = expected.to_graph()
    actual_graph, actual_images = actual.to_graph()
    assert json.loads(actual_graph) == json.loads(expected_graph)
    assert len(actual_images) == len(expected_images)
    for index, (a, e) in enumerate(zip(actual_images, expected_images)):
        if a.rgba_bytes() != e.rgba_bytes():
            got = np.frombuffer(a.rgba_bytes(), np.uint8).reshape(a.height, a.width, 4)
            want = np.frombuffer(e.rgba_bytes(), np.uint8).reshape(e.height, e.width, 4)
            ys, xs = np.nonzero(np.any(got != want, axis=2))
            raise AssertionError(
                f"画像 {index} が {len(xs)} 画素違う（最初は {xs[0]}, {ys[0]}: "
                f"Rust {got[ys[0], xs[0]]} / Python {want[ys[0], xs[0]]}）"
            )


# 押す点：各部分の中・線の上・外・端・キャンバスの外・小数の座標。
POINTS = [
    (10.5, 10.2), (12, 12), (3, 3), (35, 10), (60, 6), (58, 12), (80, 7), (80, 16),
    (10, 30), (15, 35), (12, 50), (5, 44), (50, 40), (90, 60), (1, 1), (-0.5, 5),
    (-2, 5), (WIDTH + 1, 5), (95.9, 63.9), (40, 40), (11, 14),
]

SETTINGS = {
    "default": {},
    "not_adjacent": {"bucket_adjacent": False},
    "close_gap": {"bucket_close_gap": True, "bucket_gap_width": 2},
    "no_extend": {"bucket_extend_lines": False},
    "extend_short": {"bucket_extend_length": 8},
    "require_closed": {"bucket_require_closed": True},
    "no_island_fill": {"bucket_island_fill": False},
    "wide_island_fill": {"bucket_island_fill_width": 3},
    "include_sub": {"bucket_include_sub": True},
    "island_replace": {"bucket_island_replace": True},
    **{f"target_{name}": {"bucket_target_color": name} for name in bucket_fill.TARGET_COLORS},
}


def _apply_tools(window, values):
    tools = window.tools
    for name, value in values.items():
        widget = getattr(tools, name)
        if isinstance(value, bool):
            widget.setChecked(value)
        elif isinstance(value, str):
            widget.setCurrentIndex(widget.findData(value))
        else:
            widget.setValue(value)


def _prepare_single(window, values):
    canvas = window.canvas
    _install(window, 0, _scene(), 1)
    canvas.ensure_editable_key()
    _apply_tools(window, values)
    if values.get("bucket_island_replace"):
        # 赤い斜線を緑へ塗り替える置換色。
        canvas.window().palette.replacements = {(230, 20, 20): QColor(0, 160, 60)}


def _click_both(window, point):
    canvas = window.canvas
    rust = _core(window).copy()
    settings = _settings(window)
    selection = _selection(window)
    depth = len(canvas.undo_stack)
    captured = _capture_colors(window)
    canvas.flood_fill(QPointF(*point))
    reason, rect, colors = native.bucket_fill_at(
        rust, canvas.current_frame, canvas.active_layer_index, settings,
        float(point[0]), float(point[1]), selection,
    )
    _assert_same(_core(window), rust)
    pushed = len(canvas.undo_stack) > depth
    assert pushed == (reason == "ok")
    if pushed:
        undo = canvas.undo_stack[-1].rect
        assert rect == (undo.x(), undo.y(), undo.width(), undo.height())
        assert captured == ([tuple(colors)] if colors else [])
    return reason


@pytest.mark.parametrize("name", sorted(SETTINGS))
def test_click_matches_python(window, name):
    _prepare_single(window, SETTINGS[name])
    reasons = {_click_both(window, point) for point in POINTS}
    assert "ok" in reasons


@pytest.mark.parametrize("opacity", [0.5, 0.33, 0.999])
def test_partial_opacity_matches_python(window, opacity):
    _prepare_single(window, {})
    window.canvas.fill_opacity = opacity
    for point in POINTS[:12]:
        _click_both(window, point)


def test_white_bucket_erases_like_python(window):
    _prepare_single(window, {})
    window.canvas.main_color = QColor("white")
    for point in POINTS[:8]:
        _click_both(window, point)


def test_selection_limits_the_fill_like_python(window):
    _prepare_single(window, {})
    canvas = window.canvas
    mask = np.zeros((HEIGHT, WIDTH), bool)
    mask[5:40, 6:60] = True
    canvas.selection_polygon = [QPointF(6, 5), QPointF(60, 5), QPointF(60, 40), QPointF(6, 40)]
    canvas.selection_mask_override = mask
    reasons = {_click_both(window, point) for point in POINTS}
    assert "outside_selection" in reasons


@pytest.mark.parametrize("reference", ["canvas", "initial"])
def test_reference_layers_match_python(window, reference):
    canvas = window.canvas
    _install(window, 0, _scene(), 1)
    layer = canvas.active_layer
    layer.initial_image = layer.image.to_qimage().copy()
    # 初期画像を残してから枠の中を仕切る。
    with_line = _scene()
    with_line[10, 4:20] = BLACK
    _install(window, 0, with_line, 1)
    canvas.active_layer.initial_image = imaging.rgba_array_to_qimage(_scene())
    canvas.add_layer()
    canvas.ensure_editable_key()
    _apply_tools(window, {"bucket_reference": reference})
    for point in POINTS:
        _click_both(window, point)
    if reference == "canvas":
        expected = native.bucket_reference_pixels(
            _core(window), canvas.current_frame, canvas.active_layer_index, "canvas")
        assert len(expected) == WIDTH * HEIGHT * 4


PATHS = [
    [(10, 10), (35, 10), (60, 6), (80, 7), (80, 16)],
    [(12, 50), (12, 30), (40, 40), (90, 60)],
    [(1, 1), (10.5, 10.5), (12.25, 14.75), (35.5, 12.5)],
    [(-3, 5), (30, 5), (30, 25)],
    [(58, 12), (60, 6), (62, 15)],
    # 半透明の所をなぞる（読み回しの画素が Python と同じであること）。
    [(10, 30), (22, 38), (6, 27), (20, 31)],
]


def _drag_both(window, path):
    canvas = window.canvas
    rust = _core(window).copy()
    settings = _settings(window)
    frame, layer = canvas.current_frame, canvas.active_layer_index
    depth = len(canvas.undo_stack)
    canvas.begin_bucket_drag(QPointF(*path[0]))
    for point in path[1:]:
        canvas.continue_bucket_drag(QPointF(*point))
    canvas.finish_bucket_drag()
    begun = native._BucketDrag.begin(
        rust, frame, layer, settings, float(path[0][0]), float(path[0][1]), _selection(window))
    undo = None
    if begun is not None:
        drag, _step = begun
        for point in path[1:]:
            drag.extend(rust, float(point[0]), float(point[1]))
        undo = drag.finish(rust)
    _assert_same(_core(window), rust)
    pushed = len(canvas.undo_stack) > depth
    assert pushed == (undo is not None)
    if undo is not None:
        entry = canvas.undo_stack[-1]
        rect = entry.rect
        assert undo[2] == (rect.x(), rect.y(), rect.width(), rect.height())


@pytest.mark.parametrize("name", ["default", "require_closed", "target_surrounded_by_black",
                                  "close_gap", "not_adjacent", "include_sub", "target_all",
                                  "target_transparent", "target_faint_as_transparent",
                                  "island_replace"])
def test_drag_matches_python(window, name):
    _prepare_single(window, SETTINGS[name])
    for path in PATHS:
        _drag_both(window, path)


@pytest.mark.parametrize("reference", ["canvas", "initial"])
def test_drag_with_reference_and_opacity_matches_python(window, reference):
    canvas = window.canvas
    _install(window, 0, _scene(), 1)
    canvas.active_layer.initial_image = imaging.rgba_array_to_qimage(_scene(seed=3, shift=2))
    canvas.add_layer()
    canvas.ensure_editable_key()
    _apply_tools(window, {"bucket_reference": reference})
    canvas.fill_opacity = 0.6
    for path in PATHS:
        _drag_both(window, path)


def _noisy_scene(seed):
    """線のまわりに半透明のにじみ（アンチエイリアスのような画素）を散らした絵。"""
    rng = np.random.default_rng(seed)
    rgba = _scene(seed)
    near = rng.random((HEIGHT, WIDTH)) < 0.15
    rgba[near, 3] = rng.integers(1, 255, int(near.sum()))
    rgba[near, :3] = rng.integers(0, 256, (int(near.sum()), 3))
    return rgba


@pytest.mark.parametrize("seed", range(4))
def test_random_scenes_match_python(window, seed):
    rng = np.random.default_rng(100 + seed)
    canvas = window.canvas
    _install(window, 0, _noisy_scene(seed), 1)
    canvas.ensure_editable_key()
    names = sorted(SETTINGS)
    _apply_tools(window, SETTINGS[names[int(rng.integers(len(names)))]])
    canvas.fill_opacity = float(rng.choice([1.0, 0.5, 0.75]))
    for _ in range(12):
        point = tuple(float(v) for v in rng.uniform((-1, -1), (WIDTH + 1, HEIGHT + 1)))
        _click_both(window, point)
    path = [tuple(float(v) for v in rng.uniform((0, 0), (WIDTH, HEIGHT))) for _ in range(5)]
    _drag_both(window, path)


def _prepare_frames(window, count, scenes):
    canvas = window.canvas
    canvas._ensure_frame_count(count)
    for frame, rgba in enumerate(scenes):
        _install(window, frame, rgba, frame + 1)
    window.tools.bucket_all_frames.setChecked(True)


def _skewer_both(window, point, actions=()):
    """串刺し塗りを Python と Rust で同じように進める。actions は指定待ちで順に行う操作。"""
    canvas = window.canvas
    rust = _core(window).copy()
    settings = _settings(window)
    selection = _selection(window)
    frame, layer = canvas.current_frame, canvas.active_layer_index
    assert native.skewer_cells(rust, layer) == [
        (f, layer) for f in sorted({canvas.resolve_key_frame(i, layer)
                                    for i in range(len(canvas.frames))} - {None})]
    canvas.flood_fill(QPoint(int(point[0]), int(point[1])))
    kind, session, _reason = native._SkewerFill.start(
        rust, frame, layer, settings, float(point[0]), float(point[1]), selection)
    pended = []
    if session is not None:
        status = session.advance(rust)
        for action in actions:
            if status[0] != "pending":
                break
            assert canvas.skewer_fill_pending()
            cell = status[1]
            assert cell is not None
            assert canvas.current_frame == cell[0], "同じコマで止まる"
            assert canvas.skewer_fill_has_guess() == session.has_guess()
            pended.append(status[1])
            guide = session.guide()
            assert guide is not None and guide[2] == session.has_guess()
            if action == "skip":
                canvas.skip_skewer_fill_frame()
                event = session.skip_frame(rust)
            elif action == "skip_direction":
                canvas.skip_skewer_fill_direction()
                event = session.skip_direction(rust)
            elif action == "guess":
                canvas.accept_skewer_guess()
                event = session.accept_guess(rust)
            else:
                shown = canvas.current_frame
                canvas.flood_fill(QPoint(*action))
                event = session.resume_click(rust, shown, *map(float, action))
            if event[0] == "advanced":
                status = event[1]
        if status[0] == "pending":
            canvas.abort_skewer_fill()
        else:
            assert not canvas.skewer_fill_pending()
            applied = session.apply(rust)
            assert applied[0] == "done" and applied[1] is not None
            assert canvas.current_frame == applied[1][3], "塗り始めのコマへ戻す"
    # 待つコマ・元のコマへの移動は画面の役目。文書の選択はキャンバスに合わせて比べる。
    rust.set_selection_index(canvas.current_frame, canvas.active_layer_index)
    _assert_same(_core(window), rust)
    return kind, pended


def test_skewer_fill_tracks_a_moving_shape_like_python(window):
    _prepare_frames(window, 4, [_scene(seed=i, shift=2 * i) for i in range(4)])
    window.canvas.current_frame = 1
    assert _skewer_both(window, (8, 14), ("skip",)) == ("started", [])


@pytest.mark.parametrize("actions", [
    ("skip",), ("skip_direction",), ("guess",), ((30, 30),), ((60, 6), "skip"),
])
def test_skewer_fill_waits_like_python(window, actions):
    scenes = [_scene(seed=0)]
    for shift in (14, 30):
        moved = np.zeros_like(scenes[0])
        moved[:] = WHITE
        _box(moved, 3 + shift, 3, 30 + shift, 40)
        scenes.append(moved)
    _prepare_frames(window, 3, scenes)
    kind, pended = _skewer_both(window, (12, 12), actions)
    assert kind == "started"
    assert pended, "自信のないコマで止まる"


@pytest.mark.parametrize("name", ["not_adjacent", "target_surrounded_by_black",
                                  "island_replace", "require_closed"])
def test_skewer_fill_settings_match_python(window, name):
    _prepare_frames(window, 3, [_scene(seed=i, shift=i) for i in range(3)])
    _apply_tools(window, SETTINGS[name])
    if name == "island_replace":
        window.palette.replacements = {(230, 20, 20): QColor(0, 160, 60)}
    for point in [(10, 10), (60, 6), (1, 1)]:
        _skewer_both(window, point)


def test_skewer_fill_with_opacity_and_held_cells_matches_python(window):
    _prepare_frames(window, 3, [_scene(seed=i, shift=i) for i in range(3)])
    canvas = window.canvas
    canvas.fill_opacity = 0.4
    from dense_fixtures import hold
    hold(canvas, 0, 2)
    canvas.current_frame = 1
    _skewer_both(window, (10, 10))


def test_replace_color_request_matches_python(window):
    _prepare_single(window, {})
    canvas = window.canvas
    replaced = []
    window.used_color.apply_palette_replacements = (
        lambda mapping, operation="replace": replaced.append(dict(mapping)) or True)
    points = [((3, 3), True), ((60, 6), False), ((10, 30), False), ((12, 12), True),
              ((30, 33), False)]
    for (point, line), accept in zip(points, [True, False, True, True, False]):
        kind, request = native.bucket_replace_color_request(
            _core(window), canvas.current_frame, canvas.active_layer_index,
            (0x33, 0x55, 0xff), *point)
        asked = []
        replaced.clear()
        canvas.replace_color_at(
            QPoint(*point),
            confirm_line=lambda source, asked=asked, accept=accept: asked.append(source) or accept)
        assert kind == "ok"
        assert request is not None
        assert request[2] == line
        assert asked == ([request[0]] if line else [])
        expected = [] if line and not accept else [{request[0]: request[1]}]
        assert replaced == expected
    for point, refusal in [((5, 44), "transparent"), ((-1, 3), "out_of_bounds")]:
        kind, _ = native.bucket_replace_color_request(
            _core(window), canvas.current_frame, canvas.active_layer_index,
            (0x33, 0x55, 0xff), *point)
        assert kind == refusal
        replaced.clear()
        canvas.replace_color_at(QPoint(*point))
        assert replaced == []
    canvas.main_color = QColor(255, 255, 255)
    kind, _ = native.bucket_replace_color_request(
        _core(window), canvas.current_frame, canvas.active_layer_index, (255, 255, 255), 1, 1)
    assert kind == "same_color"
