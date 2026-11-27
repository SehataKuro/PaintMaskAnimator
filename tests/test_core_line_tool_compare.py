"""Compare pma-core's line and shape tools with the Python/Qt canvas.

- 曲線の線（2 次曲線の直線ツール・楕円の輪郭）と、1px の折れ線・曲線を QPainter と比べる。
- 直線ツール（入り抜きあり）・図形ツールの確定を PaintCanvas の `commit_line_tool`・
  `commit_shape_tool` と比べる。確定前の見た目（#117）は確定と同じ画素。
"""
import os
import random
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import pma_native  # noqa: E402
from paintmaskanimator import constants  # noqa: E402
from paintmaskanimator.canvas import PaintCanvas  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _alpha(image: QImage) -> np.ndarray:
    w, h = image.width(), image.height()
    rows = np.frombuffer(image.constBits(), np.uint8).reshape(h, image.bytesPerLine())
    return rows[:, : w * 4].reshape(h, w, 4)[:, :, 3] != 0


def _bits(data: bytes, w: int, h: int) -> np.ndarray:
    return np.frombuffer(data, np.uint8).reshape(h, w).astype(bool)


def _qt_stroke(path: QPainterPath, pen_width: float, w: int, h: int) -> np.ndarray:
    image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(QPen(QColor("black"), pen_width, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.end()
    return _alpha(image)


def _elements(path: QPainterPath):
    """QPainterPath を stroke_path_mask の要素の並びにする。"""
    out = []
    index = 0
    while index < path.elementCount():
        e = path.elementAt(index)
        if e.type == QPainterPath.ElementType.MoveToElement:
            out.append([0.0, e.x, e.y])
            index += 1
        elif e.type == QPainterPath.ElementType.LineToElement:
            out.append([1.0, e.x, e.y])
            index += 1
        else:
            c2, end = path.elementAt(index + 1), path.elementAt(index + 2)
            out.append([2.0, e.x, e.y, c2.x, c2.y, end.x, end.y])
            index += 3
    return out


def _point(rng: random.Random, w: int, h: int, margin: float = 6.0):
    return rng.uniform(-margin, w + margin), rng.uniform(-margin, h + margin)


@pytest.mark.parametrize("pen", [1.0, 0.75, 1.5, 2.0, 3.5, 6.0, 11.0])
def test_quad_curves_match_qpainter(app, pen):
    rng = random.Random(int(pen * 100))
    for _ in range(250):
        w, h = rng.randint(8, 80), rng.randint(8, 80)
        a, c, b = _point(rng, w, h), _point(rng, w, h, 20), _point(rng, w, h)
        if rng.random() < 0.1:
            c = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        path = QPainterPath(QPointF(*a))
        path.quadTo(QPointF(*c), QPointF(*b))
        actual = _bits(pma_native.stroke_path_mask(_elements(path), pen, w, h), w, h)
        assert np.array_equal(actual, _qt_stroke(path, pen, w, h)), (a, c, b, pen, w, h)


@pytest.mark.parametrize("pen", [1.0, 0.5, 1.5, 2.0, 4.0, 9.0])
def test_ellipse_outlines_match_qpainter(app, pen):
    rng = random.Random(int(pen * 10) + 7)
    for _ in range(250):
        w, h = rng.randint(8, 80), rng.randint(8, 80)
        x, y = rng.uniform(-5, w), rng.uniform(-5, h)
        rect = QRectF(x, y, rng.uniform(0.5, w), rng.uniform(0.5, h))
        if rng.random() < 0.3:
            rect = QRectF(round(x), round(y), round(rect.width()) or 1, round(rect.height()) or 1)
        path = QPainterPath()
        path.addEllipse(rect)
        actual = _bits(pma_native.stroke_path_mask(_elements(path), pen, w, h), w, h)
        assert np.array_equal(actual, _qt_stroke(path, pen, w, h)), (rect, pen, w, h)


@pytest.mark.parametrize("pen", [1.0, 0.6])
def test_thin_polylines_match_qpainter(app, pen):
    """1px の折れ線（閉じたもの・閉じないもの）。"""
    rng = random.Random(41)
    for index in range(400):
        w, h = rng.randint(6, 60), rng.randint(6, 60)
        points = [_point(rng, w, h) for _ in range(rng.randint(2, 9))]
        if index % 7 == 0:
            points = [(round(x), round(y)) for x, y in points]
        path = QPainterPath(QPointF(*points[0]))
        for p in points[1:]:
            path.lineTo(QPointF(*p))
        if index % 3 == 0:
            path.closeSubpath()
        actual = _bits(pma_native.stroke_path_mask(_elements(path), pen, w, h), w, h)
        assert np.array_equal(actual, _qt_stroke(path, pen, w, h)), (points, index % 3 == 0, w, h)


# --- 直線ツール・図形ツールの確定 ---------------------------------------------

WIDTH, HEIGHT = 72, 56
PALETTE = [(200, 30, 90), (1, 2, 3), (90, 90, 250), (255, 255, 255)]


class _Value:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value

    def isChecked(self):
        return bool(self._value)

    def currentData(self):
        return self._value


class _Panel:
    def __init__(self, **values: Any):
        for name, value in values.items():
            setattr(self, name, _Value(value))


@pytest.fixture
def canvas(app, monkeypatch):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", WIDTH)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", HEIGHT)
    widget = PaintCanvas()
    widget.zoom = 1
    widget.pan = QPointF(0, 0)
    widget.rotation = 0
    widget.active_layer_index = next(
        i for i, layer in enumerate(widget.frames[0].layers) if not layer.is_paper
    )
    yield widget
    widget._brush_follow_timer.stop()
    widget.close()
    app.processEvents()


def _fill_layer(canvas, rng):
    rgba = np.zeros((HEIGHT, WIDTH, 4), np.uint8)
    for _ in range(10):
        x0, y0 = rng.randrange(WIDTH), rng.randrange(HEIGHT)
        x1, y1 = x0 + rng.randint(3, 30), y0 + rng.randint(3, 30)
        rgba[y0:y1, x0:x1] = (*PALETTE[rng.randrange(3)], 255)
    canvas.active_layer.image.write_rgba(rgba)
    return pma_native.RgbaImage(WIDTH, HEIGHT, canvas.active_layer.image.rgba().tobytes())


def _selection(canvas, rng) -> dict[str, Any]:
    """選択範囲を canvas に設定し、同じ選択範囲の Rust への引数を返す。"""
    kind = rng.randrange(4)
    if kind == 0:
        return {}
    if kind in (1, 2):
        polygon = (
            [(5.5, 3.2), (60.7, 10.0), (40.0, 50.5), (30.0, 20.0), (8.0, 45.0)]
            if kind == 1 else [(10, 5), (50, 5), (50, 40), (10, 40)]
        )
        canvas.selection_polygon = [QPointF(*p) for p in polygon]
        return {"selection_polygon": polygon}
    selection = np.zeros((HEIGHT, WIDTH), bool)
    selection[5:40, 10:50] = True
    selection[20:30, 20:30] = False
    canvas.selection_polygon = [QPointF(0, 0), QPointF(1, 0), QPointF(1, 1)]
    canvas.selection_mask_override = selection
    return {"selection_mask": selection.astype(np.uint8).ravel().tolist()}


def _check(canvas, before, result, emitted):
    expected = canvas.active_layer.image.rgba()
    if result is None:
        assert np.array_equal(before, expected) and not emitted
        return expected
    image, undo, colors = result
    actual = np.frombuffer(image.rgba_bytes(), np.uint8).reshape(HEIGHT, WIDTH, 4)
    differing = int(np.any(actual != expected, axis=2).sum())
    assert differing == 0, f"{differing} pixels differ"
    if undo is None:
        assert np.array_equal(before, expected)
    else:
        (x, y, w, h), old, new = undo
        assert old == before[y:y + h, x:x + w].tobytes()
        assert new == expected[y:y + h, x:x + w].tobytes()
    assert [tuple(c) for c in colors] == emitted
    return actual


def _point_in(rng):
    return rng.uniform(-6, WIDTH + 6), rng.uniform(-6, HEIGHT + 6)


def _record_colors(canvas) -> list[tuple[int, int, int]]:
    emitted: list[tuple[int, int, int]] = []
    canvas.colorUsed.connect(lambda color: emitted.append((color.red(), color.green(), color.blue())))
    return emitted


@pytest.mark.parametrize("seed", range(200))
def test_line_tool_commit_matches_canvas(canvas, monkeypatch, seed):
    rng = random.Random(seed * 131 + 5)
    layer = _fill_layer(canvas, rng)
    selection = _selection(canvas, rng)
    canvas.pen_size = rng.choice([0.5, 1, 1.5, 2, 3.5, 6, 9])
    rgb = PALETTE[rng.randrange(len(PALETTE))]
    canvas.main_color = QColor(*rgb)
    canvas.color_mode = "main"
    taper_in = (rng.choice([0, 1, 2, 3, 10]), rng.choice([10, 50, 100, 200])) if rng.random() < 0.6 else None
    taper_out = (rng.choice([0, 1, 2, 3, 10]), rng.choice([10, 50, 100, 200])) if rng.random() < 0.6 else None
    shift_line = rng.random() < 0.15
    panel = _Panel(
        line_taper_in=taper_in is not None, line_taper_in_size=(taper_in or (0, 0))[0],
        line_taper_in_curve=(taper_in or (0, 0))[1], line_taper_out=taper_out is not None,
        line_taper_out_size=(taper_out or (0, 0))[0], line_taper_out_curve=(taper_out or (0, 0))[1],
    )
    monkeypatch.setattr(canvas, "_active_tool_panel", lambda: panel)
    start, end = _point_in(rng), _point_in(rng)
    control = _point_in(rng) if rng.random() < 0.5 else None
    canvas.line_start, canvas.line_end = QPointF(*start), QPointF(*end)
    canvas.line_control = QPointF(*control) if control else None
    canvas.line_curve_stage = 2 if control else 1
    canvas._brush_shift_line = shift_line
    before = canvas.active_layer.image.rgba().copy()
    emitted = _record_colors(canvas)
    canvas.commit_line_tool()

    result = pma_native.line_tool_commit(
        layer, start, end, control, pen_size=canvas.pen_size, color=rgb,
        shift_line=shift_line, taper_in=taper_in, taper_out=taper_out, **selection,
    )
    actual = _check(canvas, before, result, emitted)
    # 確定前の見た目（#117）は確定と同じ画素。
    preview = _bits(pma_native.line_tool_mask(
        start, end, control, WIDTH, HEIGHT, pen_size=canvas.pen_size, shift_line=shift_line,
        taper_in=taper_in, taper_out=taper_out,
    ), WIDTH, HEIGHT)
    if not selection:
        painted = np.any(actual != before, axis=2)
        assert not np.any(painted & ~preview)


@pytest.mark.parametrize("seed", range(300))
def test_shape_tool_commit_matches_canvas(canvas, monkeypatch, seed):
    rng = random.Random(seed * 977 + 3)
    layer = _fill_layer(canvas, rng)
    selection = _selection(canvas, rng)
    main, sub = PALETTE[rng.randrange(len(PALETTE))], PALETTE[rng.randrange(len(PALETTE))]
    canvas.main_color, canvas.sub_color = QColor(*main), QColor(*sub)
    canvas.color_mode = "main"
    split = rng.random() < 0.4
    fill_inside = rng.random() < 0.6
    shape_type = rng.choice(["ellipse", "polygon"])
    corners = rng.choice([3, 4, 4, 5, 6, 8])
    lock = rng.random() < 0.3
    outline = rng.choice([1, 2, 3, 4, 7, 12, 20])
    panel = _Panel(
        shape_sub_outline_main_fill=split, shape_fill_inside=fill_inside,
        shape_outline_width=outline, shape_lock_ratio=lock, shape_type=shape_type,
        shape_corners=corners,
    )
    monkeypatch.setattr(canvas, "_active_tool_panel", lambda: panel)
    start, end = _point_in(rng), _point_in(rng)
    if rng.random() < 0.3:
        start, end = (round(start[0]), round(start[1])), (round(end[0]), round(end[1]))
    canvas.shape_start, canvas.shape_end = QPointF(*start), QPointF(*end)
    before = canvas.active_layer.image.rgba().copy()
    emitted = _record_colors(canvas)
    canvas.commit_shape_tool()

    outline_rgb, fill_rgb = (sub, main) if split else (main, main)
    options: dict[str, Any] = dict(
        shape_type=shape_type, corners=corners, lock_ratio=lock, outline_width=outline,
        fill_inside=fill_inside,
    )
    result = pma_native.shape_tool_commit(
        layer, start, end, outline_color=outline_rgb, fill_color=fill_rgb, **options, **selection,
    )
    actual = _check(canvas, before, result, emitted)
    # 確定前の見た目（#117）は確定で書く画素と同じ（選択範囲は確定のときに切り抜く）。
    preview = pma_native.shape_tool_preview(
        start, end, WIDTH, HEIGHT, outline_color=outline_rgb, fill_color=fill_rgb, **options,
    )
    assert (preview is None) == (result is None)
    if preview is not None and not selection:
        shown = np.frombuffer(preview, np.uint8).reshape(HEIGHT, WIDTH, 4)
        written = shown[:, :, 3] == 255
        erased = written & np.all(shown[:, :, :3] == 255, axis=2)
        expect = np.where(erased[:, :, None], 0, shown)
        assert np.array_equal(actual[written], expect[written])
        assert np.array_equal(actual[~written], before[~written])


def test_empty_shape_returns_none():
    assert pma_native.shape_tool_preview(
        (3, 3), (3, 9), 10, 10, outline_color=(1, 2, 3), fill_color=(1, 2, 3),
    ) is None
