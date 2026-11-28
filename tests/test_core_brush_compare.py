"""Rust のブラシ（pma_core::brush）を、Qt の画面の経路と画素で突き合わせる。

同じ入力の並びを Python のキャンバス（QPainter・非 AA）と Rust の BrushStroke に通し、
層の画素・Undo の範囲・塗った色が一致することを確かめる。図形の塗りと線も QPainter と比べる。
"""
import math
import os
import random
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import pma_native  # noqa: E402
from paintmaskanimator import constants  # noqa: E402
from paintmaskanimator.brush_raster import paint_segment, stamp_mask  # noqa: E402
from paintmaskanimator.canvas import PaintCanvas  # noqa: E402
from paintmaskanimator.canvas_brush_stabilizer import BrushStabilizerMixin  # noqa: E402
from paintmaskanimator.pressure import _pressure_bezier_at, _pressure_bezier_segments  # noqa: E402

WIDTH, HEIGHT = 72, 60
PALETTE = [(255, 0, 0), (0, 128, 255), (20, 200, 60), (255, 255, 255)]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _alpha(image: QImage) -> np.ndarray:
    w, h = image.width(), image.height()
    rows = np.frombuffer(image.constBits(), np.uint8).reshape(h, image.bytesPerLine())
    return rows[:, : w * 4].reshape(h, w, 4)[:, :, 3] != 0


def _bits(data: bytes, width: int, height: int) -> np.ndarray:
    return np.frombuffer(data, np.uint8).reshape(height, width) != 0


def _qt_painter(image: QImage, width: float) -> QPainter:
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(QPen(QColor("white"), width, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    return painter


def _blank(width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    return image


def _random_point(rng, width, height, spread=8.0):
    return (rng.uniform(-spread, width + spread), rng.uniform(-spread, height + spread))


# --- 1 区間と筆先 -------------------------------------------------------------

def test_segments_match_qpainter(app):
    rng = random.Random(1)
    for _ in range(3000):
        w, h = rng.randint(4, 48), rng.randint(4, 48)
        width = rng.choice([0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.5, 8.0, 17.0, rng.uniform(0.5, 30.0)])
        a = _random_point(rng, w, h)
        kind = rng.random()
        if kind < 0.2:
            b = a
        elif kind < 0.5:
            b = (a[0] + rng.uniform(-3, 3), a[1] + rng.uniform(-3, 3))
        else:
            b = _random_point(rng, w, h)
        if rng.random() < 0.3:
            a = (round(a[0] * 2) / 2, round(a[1] * 2) / 2)
            b = (round(b[0] * 2) / 2, round(b[1] * 2) / 2)
        image = _blank(w, h)
        painter = QPainter(image)
        paint_segment(painter, QPointF(*a), QPointF(*b), QColor("white"), width)
        painter.end()
        actual = _bits(pma_native.brush_segment_mask(a, b, width, w, h), w, h)
        assert np.array_equal(actual, _alpha(image)), (a, b, width, w, h)


@pytest.mark.parametrize("size", [0.5, 1, 1.5, 2, 3, 5, 8, 13.5])
@pytest.mark.parametrize("phase", [0, 0.25, 0.5, 0.75, 0.3])
def test_stamp_matches_pixel_cursor(app, size, phase):
    center = (20 + phase, 31 + phase / 2)
    mask, rect = stamp_mask(QPointF(*center), size)
    x, y, side, data = pma_native.brush_stamp_mask(center, size)
    assert (x, y, side, side) == (rect.x(), rect.y(), rect.width(), rect.height())
    assert np.array_equal(_bits(data, side, side), _alpha(mask))


# --- 筆圧と手ぶれ補正 ---------------------------------------------------------

def test_pressure_curves_match_python():
    rng = random.Random(2)
    for _ in range(300):
        points = [(rng.uniform(-0.1, 1.1), rng.uniform(-0.1, 1.1)) for _ in range(rng.randint(0, 6))]
        if rng.random() < 0.2 and points:
            points.append((points[0][0] + 1e-7, rng.random()))
        expected = [[tuple(p) for p in segment] for segment in _pressure_bezier_segments(points)]
        assert [[tuple(p) for p in segment] for segment in pma_native.pressure_bezier_segments(points)] == expected
        for value in [-0.5, 0.0, 0.001, rng.random(), rng.random(), 0.5, 1.0, 1.5]:
            assert pma_native.pressure_bezier_at(points, value) == _pressure_bezier_at(points, value)


class _Timer:
    def __init__(self):
        self.active = False

    def isActive(self):
        return self.active

    def start(self):
        self.active = True

    def stop(self):
        self.active = False


class _Stabilizer(BrushStabilizerMixin):
    def __init__(self, strength, zoom):
        self.brush_stabilizer_strength = strength
        self.zoom = zoom
        self.last_canvas = None
        self._last_raw_canvas = None
        self._stabilized_canvas = None
        self._brush_stabilizer_history = []
        self._pressure_input_history = []
        self._last_brush_pressure = 1.0
        self._brush_follow_settle = 0.0
        self._brush_follow_timer = _Timer()
        self.drawing = True
        self.lines = []

    def draw_line(self, start, end, pressure):
        self.lines.append((start.x(), start.y(), end.x(), end.y(), pressure))

    def effective_tool(self):
        return "brush"


def _python_stabilizer(strength, zoom, events):
    s = _Stabilizer(strength, zoom)
    pen = False
    for kind, x, y, value in events:
        p = QPointF(x, y)
        if kind == "press":
            pen = value is not None
            s._reset_brush_stabilizer(p)
            s._start_brush_follow_timer()
            s.last_canvas = QPointF(p)
            pressure = 1.0
            if pen:
                pressure = s._smooth_brush_pressure(max(.001, value))
                s._last_brush_pressure = pressure
            s.draw_line(p, p, pressure)
        elif kind == "move":
            pressure = 1.0
            if pen:
                pressure = s._smooth_brush_pressure(max(.001, value))
                s._last_brush_pressure = pressure
            s._draw_stabilized_brush_to(p, pressure)
        elif kind == "tick":
            if s._brush_follow_timer.isActive():
                s._advance_stabilized_brush()
        else:
            s._finish_stabilized_brush(p, max(0.001, float(s._last_brush_pressure)) if pen else 1.0)
            s._brush_follow_timer.stop()
    return s.lines


def _random_events(rng, pen, count, width=WIDTH, height=HEIGHT):
    x, y = rng.uniform(4, width - 4), rng.uniform(4, height - 4)
    pressure = rng.uniform(0.05, 1.0) if pen else None
    events = [("press", x, y, pressure)]
    for _ in range(count):
        if rng.random() < 0.25:
            events.append(("tick", x, y, None))
            continue
        if rng.random() < 0.1:
            x, y = rng.uniform(-15, width + 15), rng.uniform(-15, height + 15)
        else:
            x += rng.uniform(-6, 6)
            y += rng.uniform(-6, 6)
        if pen:
            pressure = min(1.0, max(0.0, pressure + rng.uniform(-0.3, 0.3)))
        events.append(("move", x, y, pressure))
    events.append(("release", x, y, pressure))
    return events


@pytest.mark.parametrize("strength", [0, 1, 7, 40, 150, 300])
@pytest.mark.parametrize("zoom", [1.0, 0.25, 3.5])
def test_stabilizer_matches_python(strength, zoom):
    rng = random.Random(strength * 31 + int(zoom * 4))
    for pen in (False, True):
        events = _random_events(rng, pen, 40)
        got = pma_native.stabilizer_segments(strength, zoom, events)
        want = _python_stabilizer(strength, zoom, events)
        if sys.version_info >= (3, 12):
            assert got == want
        else:
            # 手ぶれ補正は重みつきの平均を sum() で取る。Python 3.12 から sum() は浮動小数を
            # 誤差を補って足すようになり、Rust はそれに合わせてある。3.11 では最後の桁がずれる。
            assert len(got) == len(want)
            assert [c for s in got for c in s] == pytest.approx([c for s in want for c in s], rel=1e-12, abs=1e-12)


# --- ストローク全体 ----------------------------------------------------------

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
    widget.set_tool("brush")
    yield widget
    widget._brush_follow_timer.stop()
    widget.close()
    app.processEvents()


def _fill_layer(canvas, rng):
    rgba = np.zeros((HEIGHT, WIDTH, 4), np.uint8)
    for _ in range(14):
        x0, y0 = rng.randrange(WIDTH), rng.randrange(HEIGHT)
        x1, y1 = x0 + rng.randint(3, 30), y0 + rng.randint(3, 30)
        color = PALETTE[rng.randrange(len(PALETTE))]
        rgba[y0:y1, x0:x1] = (*color, 255)
    rgba[rng.randrange(HEIGHT), :] = (10, 10, 10, 128)
    canvas.active_layer.image.write_rgba(rgba)
    return pma_native.RgbaImage(WIDTH, HEIGHT, canvas.active_layer.image.rgba().tobytes())


def _python_stroke(canvas, events):
    """canvas_input_events のマウス・ペンの押す／動かす／離すと同じ呼び出しをする。"""
    pen = False
    dirty = None
    for kind, x, y, value in events:
        p = QPointF(x, y)
        if kind == "press":
            pen = value is not None
            canvas._commit_brush_stroke()
            canvas._brush_started_with_content = bool(canvas.active_layer.has_content)
            canvas.ensure_editable_key()
            canvas._begin_opaque_brush_stroke()
            canvas.drawing = True
            canvas._reset_brush_stabilizer(p)
            canvas._start_brush_follow_timer()
            canvas.last_canvas = QPointF(p)
            pressure = 1.0
            if pen:
                pressure = canvas._smooth_brush_pressure(max(.001, value))
                canvas._last_brush_pressure = pressure
            canvas.draw_line(p, p, pressure)
        elif kind == "move":
            pressure = 1.0
            if pen:
                pressure = canvas._smooth_brush_pressure(max(.001, value))
                canvas._last_brush_pressure = pressure
            canvas._draw_stabilized_brush_to(p, pressure)
        elif kind == "tick":
            if canvas._brush_follow_timer.isActive():
                canvas._advance_stabilized_brush()
        else:
            if canvas._stroke_work_image is not None and canvas.drawing and canvas.last_canvas is not None:
                canvas._finish_stabilized_brush(
                    p, max(0.001, float(canvas._last_brush_pressure)) if pen else 1.0,
                )
            dirty = canvas._stroke_dirty_rect
            dirty = None if dirty.isEmpty() else (dirty.x(), dirty.y(), dirty.width(), dirty.height())
            canvas._commit_brush_stroke()
    return dirty


def _rust_stroke(layer, events, **settings):
    stroke = None
    pen = False
    for kind, x, y, value in events:
        if kind == "press":
            pen = value is not None
            stroke, _ = pma_native.BrushStroke.begin(layer, x, y, value, **settings)
            continue
        assert stroke is not None
        if kind == "move":
            stroke.move_to(x, y, value)
        elif kind == "tick":
            stroke.tick()
        else:
            return stroke.finish(x, y, value if pen else None)
    raise AssertionError("no release")


def _settings_from(canvas, *, eraser=False):
    color = canvas.paint_source_color()
    settings = {
        "size": float(canvas.pen_size),
        "color": (color.red(), color.green(), color.blue()),
        "eraser": eraser,
        "pressure_enabled": bool(canvas.pressure_enabled),
        "pressure_min": float(canvas.pressure_min),
        "pressure_max": float(canvas.pressure_max),
        "pressure_points": [tuple(p) for p in (canvas.pressure_curve_points or [])],
        "pressure_exponent": float(canvas.pressure_curve),
        "stabilizer": int(canvas.brush_stabilizer_strength),
        "zoom": float(canvas.zoom),
    }
    if not canvas.mask_all_enabled:
        selected = canvas.selected_mask_colors()
        settings["mask_colors"] = sorted(selected)
        settings["mask_background"] = canvas.background_mask_rgb in selected
    if canvas.selection_polygon:
        if canvas.selection_mask_override is not None:
            settings["selection_mask"] = np.asarray(
                canvas.selection_mask_override, dtype=np.uint8,
            ).tobytes()
        else:
            settings["selection_polygon"] = [(p.x(), p.y()) for p in canvas.selection_polygon]
    return settings


CASES = [
    {"name": "mouse", "pen": False},
    {"name": "pen", "pen": True},
    {"name": "pen_curve", "pen": True, "curve": [[0.0, 0.0], [0.3, 0.7], [0.8, 0.2], [1.0, 1.0]],
     "pressure_min": 0.05, "pressure_max": 2.5},
    {"name": "pen_exponent", "pen": True, "curve": [], "exponent": 2.2},
    {"name": "pressure_off", "pen": True, "pressure_off": True},
    {"name": "eraser", "pen": False, "eraser": True},
    {"name": "stabilizer", "pen": True, "stabilizer": 60},
    {"name": "stabilizer_strong_zoom", "pen": False, "stabilizer": 250, "zoom": 0.4},
    {"name": "mask_colors", "pen": False, "mask": [(255, 0, 0), (20, 200, 60)]},
    {"name": "mask_background", "pen": True, "mask": [(255, 255, 255), (0, 128, 255)]},
    {"name": "eraser_mask", "pen": False, "eraser": True, "mask": [(0, 128, 255)]},
    {"name": "selection_polygon", "pen": False, "polygon": True},
    {"name": "selection_mask", "pen": True, "selection_mask": True},
    {"name": "selection_rect", "pen": False, "polygon": [(10, 5), (50, 5), (50, 40), (10, 40)]},
    {"name": "selection_rect_fraction", "pen": False,
     "polygon": [(10.5, 5.25), (50.5, 5.25), (50.5, 40.75), (10.5, 40.75)]},
    {"name": "thin", "pen": True, "size": 0.75},
    {"name": "wide", "pen": False, "size": 23.5},
]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
@pytest.mark.parametrize("seed", range(6))
def test_stroke_matches_canvas(canvas, case, seed):
    rng = random.Random(sum(map(ord, case["name"])) + seed * 7919)
    layer = _fill_layer(canvas, rng)
    canvas.pen_size = case.get("size", rng.choice([1, 2, 3.5, 6, 9]))
    canvas.main_color = QColor(*rng.choice([(200, 30, 90), (1, 2, 3), (90, 90, 250)]))
    canvas.color_mode = "main"
    if case.get("eraser"):
        canvas.main_color = QColor(255, 255, 255)
    canvas.pressure_enabled = not case.get("pressure_off", False)
    if "curve" in case:
        canvas.pressure_curve_points = case["curve"]
    canvas.pressure_curve = case.get("exponent", 1.0)
    canvas.pressure_min = case.get("pressure_min", 0.1)
    canvas.pressure_max = case.get("pressure_max", 1.0)
    canvas.set_brush_stabilizer(case.get("stabilizer", 0))
    canvas.zoom = case.get("zoom", 1.0)
    if "mask" in case:
        canvas.mask_all_enabled = False
        canvas.mask_color_rgbs = set(case["mask"])
    if case.get("polygon"):
        polygon = case["polygon"] if isinstance(case["polygon"], list) else [
            (5.5, 3.2), (60.7, 10.0), (40.0, 55.5), (30.0, 20.0), (8.0, 50.0),
        ]
        canvas.selection_polygon = [QPointF(*point) for point in polygon]
    if case.get("selection_mask"):
        selection = np.zeros((HEIGHT, WIDTH), bool)
        selection[5:40, 10:50] = True
        selection[20:30, 20:30] = False
        canvas.selection_polygon = [QPointF(0, 0), QPointF(1, 0), QPointF(1, 1)]
        canvas.selection_mask_override = selection

    events = _random_events(rng, case["pen"], 50)
    settings = _settings_from(canvas, eraser=bool(case.get("eraser")))
    before = canvas.active_layer.image.rgba().copy()
    dirty = _python_stroke(canvas, events)
    expected = canvas.active_layer.image.rgba()

    image, undo, colors = _rust_stroke(layer, events, **settings)
    actual = np.frombuffer(image.rgba_bytes(), np.uint8).reshape(HEIGHT, WIDTH, 4)
    differing = int(np.any(actual != expected, axis=2).sum())
    assert differing == 0, f"{differing} pixels differ"
    if dirty is None:
        assert undo is None
    else:
        assert undo is not None
        rect, old, new = undo
        assert rect == dirty
        x, y, w, h = rect
        assert old == before[y:y + h, x:x + w].tobytes()
        assert new == expected[y:y + h, x:x + w].tobytes()
    painted = not np.array_equal(before, expected)
    if case["name"] in ("mouse", "pen", "wide", "stabilizer"):
        assert painted
    source = settings["color"]
    assert colors == ([source] if painted and source != (255, 255, 255) else [])


def test_preview_image_tracks_each_segment(canvas):
    rng = random.Random(5)
    layer = _fill_layer(canvas, rng)
    stroke, rects = pma_native.BrushStroke.begin(layer, 10.0, 10.0, size=4.0, color=(9, 8, 7))
    assert len(rects) == 1
    rects = stroke.move_to(30.0, 12.0)
    preview = np.frombuffer(stroke.image().rgba_bytes(), np.uint8).reshape(HEIGHT, WIDTH, 4)
    assert tuple(preview[11, 20]) == (9, 8, 7, 255)
    dirty = stroke.dirty_rect
    assert dirty is not None
    x, _, w, _ = dirty
    assert x <= 10 and x + w >= 30
    assert all(rx >= 0 and ry >= 0 for rx, ry, _, _ in rects)


def test_advance_time_runs_follow_ticks(canvas):
    layer = pma_native.RgbaImage.blank(WIDTH, HEIGHT)
    stroke, _ = pma_native.BrushStroke.begin(layer, 10.0, 10.0, 0.0, size=3.0, color=(1, 1, 1),
                                             stabilizer=200)
    stroke.move_to(50.0, 10.0, None, 5.0)
    lagging = stroke.dirty_rect
    assert lagging is not None and lagging[0] + lagging[2] < 50
    stroke.advance_time(2000.0)
    caught_up = stroke.dirty_rect
    assert caught_up is not None and caught_up[0] + caught_up[2] > lagging[0] + lagging[2]


# --- 図形の塗りと線 -----------------------------------------------------------

def _qt_fill_polygon(points, w, h, winding=False):
    image = _blank(w, h)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("white"))
    rule = Qt.FillRule.WindingFill if winding else Qt.FillRule.OddEvenFill
    painter.drawPolygon(QPolygonF([QPointF(*p) for p in points]), rule)
    painter.end()
    return _alpha(image)


def test_lasso_polygons_match_qpainter(app):
    rng = random.Random(3)
    for index in range(800):
        w, h = rng.randint(5, 70), rng.randint(5, 70)
        count = rng.randint(3, 25)
        if index % 3 == 0:
            cx, cy, r = w / 2, h / 2, rng.uniform(2, max(w, h))
            points = [(cx + math.cos(t) * r * rng.uniform(0.4, 1), cy + math.sin(t) * r * rng.uniform(0.4, 1))
                      for t in sorted(rng.uniform(0, 2 * math.pi) for _ in range(count))]
        else:
            points = [_random_point(rng, w, h, 10) for _ in range(count)]
        if rng.random() < 0.3:
            points = [(round(x * 2) / 2, round(y * 2) / 2) for x, y in points]
        winding = rng.random() < 0.3
        actual = _bits(pma_native.fill_polygon_mask(points, w, h, winding), w, h)
        assert np.array_equal(actual, _qt_fill_polygon(points, w, h, winding)), (points, w, h)


def _qt_path(elements):
    path = QPainterPath()
    for element in elements:
        kind = int(element[0])
        if kind == 0:
            path.moveTo(element[1], element[2])
        elif kind == 1:
            path.lineTo(element[1], element[2])
        elif kind == 2:
            path.cubicTo(*element[1:])
        else:
            path.closeSubpath()
    return path


def test_ellipse_fill_matches_qpainter(app):
    rng = random.Random(4)
    for _ in range(400):
        w, h = rng.randint(5, 70), rng.randint(5, 70)
        rect = (rng.uniform(-5, w), rng.uniform(-5, h), rng.uniform(0.5, w + 5), rng.uniform(0.5, h + 5))
        if rng.random() < 0.3:
            rect = (float(round(rect[0])), float(round(rect[1])), float(round(rect[2])), float(round(rect[3])))
        qt = QPainterPath()
        qt.addEllipse(QRectF(*rect))
        ours = pma_native.ellipse_path(rect)
        for index in range(qt.elementCount()):
            element = qt.elementAt(index)
            assert (element.x, element.y) == pytest.approx(tuple(ours_point(ours, index)), abs=0, rel=0)
        image = _blank(w, h)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("white"))
        painter.drawPath(qt)
        painter.end()
        actual = _bits(pma_native.fill_path_mask(ours, w, h), w, h)
        assert np.array_equal(actual, _alpha(image)), rect


def ours_point(elements, index):
    """Qt の要素番号（曲線は 3 要素）に合わせて、経路の点を取り出す。"""
    flat = []
    for element in elements:
        values = element[1:]
        flat.extend(zip(values[::2], values[1::2]))
    return flat[index]


def test_shape_outlines_match_qpainter(app):
    rng = random.Random(6)
    for index in range(600):
        w, h = rng.randint(8, 70), rng.randint(8, 70)
        corners = rng.randint(3, 9)
        cx, cy = rng.uniform(0, w), rng.uniform(0, h)
        rx, ry = rng.uniform(1, w), rng.uniform(1, h)
        points = [(cx + math.cos(-math.pi / 2 + 2 * math.pi * i / corners) * rx,
                   cy + math.sin(-math.pi / 2 + 2 * math.pi * i / corners) * ry) for i in range(corners)]
        if index % 4 == 0:
            points = [(cx - rx, cy - ry), (cx + rx, cy - ry), (cx + rx, cy + ry), (cx - rx, cy + ry)]
        closed = index % 5 != 0
        elements = [[0.0, *points[0]]] + [[1.0, *p] for p in points[1:]] + ([[3.0]] if closed else [])
        pen = rng.choice([1.5, 2.0, 3.0, 4.5, 7.0, 12.0, rng.uniform(1.01, 20.0)])
        image = _blank(w, h)
        painter = _qt_painter(image, pen)
        painter.drawPath(_qt_path(elements))
        painter.end()
        actual = pma_native.stroke_path_mask(elements, pen, w, h)
        assert actual is not None
        assert np.array_equal(_bits(actual, w, h), _alpha(image)), (elements, pen, w, h)


@pytest.mark.parametrize("pen", [0.5, 1.0, 2.0, 5.5])
def test_straight_line_tool_matches_qpainter(app, pen):
    rng = random.Random(int(pen * 10))
    for _ in range(300):
        w, h = rng.randint(8, 70), rng.randint(8, 70)
        a, b = _random_point(rng, w, h), _random_point(rng, w, h)
        path = QPainterPath(QPointF(*a))
        path.lineTo(QPointF(*b))
        image = _blank(w, h)
        painter = _qt_painter(image, pen)
        painter.drawPath(path)
        painter.end()
        actual = pma_native.stroke_path_mask([[0.0, *a], [1.0, *b]], pen, w, h)
        assert actual is not None
        assert np.array_equal(_bits(actual, w, h), _alpha(image)), (a, b, pen, w, h)
