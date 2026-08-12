import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF  # noqa: E402

from paintmaskanimator.canvas_brush_stabilizer import BrushStabilizerMixin  # noqa: E402


class _Timer:
    def __init__(self):
        self.active = False
        self.starts = 0
        self.stops = 0

    def isActive(self):
        return self.active

    def start(self):
        self.active = True
        self.starts += 1

    def stop(self):
        self.active = False
        self.stops += 1


class _Stabilizer(BrushStabilizerMixin):
    def __init__(self, strength=0):
        self.brush_stabilizer_strength = strength
        self.zoom = 1.0
        self.last_canvas = None
        self._last_raw_canvas = None
        self._stabilized_canvas = None
        self._brush_stabilizer_history = []
        self._pressure_input_history = []
        self._last_brush_pressure = 0.75
        self._brush_follow_settle = 0.0
        self._brush_follow_timer = _Timer()
        self.drawing = True
        self.tool = "brush"
        self.lines = []

    def draw_line(self, start, end, pressure):
        self.lines.append((QPointF(start), QPointF(end), pressure))

    def effective_tool(self):
        return self.tool


def _xy(point):
    return point.x(), point.y()


@pytest.mark.parametrize(("value", "expected"), [(-5, 0), (42, 42), (999, 300)])
def test_strength_is_clamped(value, expected):
    stabilizer = _Stabilizer()
    stabilizer.set_brush_stabilizer(value)
    assert stabilizer.brush_stabilizer_strength == expected


@pytest.mark.parametrize(("strength", "expected"), [(0, 1), (1, 2), (100, 22), (300, 62)])
def test_window_scales_with_strength(strength, expected):
    assert _Stabilizer(strength)._brush_stabilizer_window() == expected


def test_reset_initializes_or_clears_tracking_state():
    stabilizer = _Stabilizer()
    stabilizer._reset_brush_stabilizer(QPointF(2, 3))
    assert _xy(stabilizer._stabilized_canvas) == (2, 3)
    assert _xy(stabilizer._last_raw_canvas) == (2, 3)
    assert [_xy(point) for point in stabilizer._brush_stabilizer_history] == [(2, 3)]

    stabilizer._reset_brush_stabilizer()
    assert stabilizer._stabilized_canvas is None
    assert stabilizer._last_raw_canvas is None
    assert stabilizer._brush_stabilizer_history == []
    assert stabilizer._pressure_input_history == []


def test_pressure_smoothing_clamps_weights_and_limits_history():
    stabilizer = _Stabilizer()
    assert stabilizer._smooth_brush_pressure(0) == pytest.approx(0.001)
    assert stabilizer._smooth_brush_pressure(2) == pytest.approx((0.001 + 2) / 3)
    for value in range(10):
        stabilizer._smooth_brush_pressure(value / 10)
    assert len(stabilizer._pressure_input_history) == 7
    assert all(0.001 <= value <= 1 for value in stabilizer._pressure_input_history)


def test_unstabilized_point_tracks_raw_input_exactly():
    stabilizer = _Stabilizer(0)
    result = stabilizer._stabilized_brush_point(QPointF(8, 9), settle=2)
    assert _xy(result) == (8, 9)
    assert _xy(stabilizer._last_raw_canvas) == (8, 9)


def test_stabilized_point_uses_weighted_target_and_dead_zone():
    stabilizer = _Stabilizer(100)
    first = stabilizer._stabilized_brush_point(QPointF(0, 0))
    assert _xy(first) == (0, 0)

    # At normal settle the point remains inside the stabilizer dead zone.
    held = stabilizer._stabilized_brush_point(QPointF(3, 0))
    assert _xy(held) == (0, 0)

    # Full settle removes the dead zone and advances to the weighted target.
    settled = stabilizer._stabilized_brush_point(QPointF(6, 0), settle=1)
    assert settled.x() > 0
    assert settled.y() == 0


def test_stabilizer_history_is_limited_to_window():
    stabilizer = _Stabilizer(1)
    for x in range(8):
        stabilizer._stabilized_brush_point(QPointF(x * 100, 0), settle=1)
    assert len(stabilizer._brush_stabilizer_history) == 2


def test_draw_without_stabilizer_draws_one_exact_segment():
    stabilizer = _Stabilizer(0)
    stabilizer.last_canvas = QPointF(1, 1)
    stabilizer._draw_stabilized_brush_to(QPointF(4, 5), 0.4)
    assert len(stabilizer.lines) == 1
    assert _xy(stabilizer.lines[0][0]) == (1, 1)
    assert _xy(stabilizer.lines[0][1]) == (4, 5)
    assert stabilizer.lines[0][2] == 0.4


def test_draw_with_stabilizer_interpolates_long_input():
    stabilizer = _Stabilizer(100)
    stabilizer._reset_brush_stabilizer(QPointF(0, 0))
    stabilizer.last_canvas = QPointF(0, 0)
    stabilizer._draw_stabilized_brush_to(QPointF(100, 0), 0.6)
    assert stabilizer._brush_follow_timer.active
    assert len(stabilizer.lines) > 1
    assert all(line[2] == 0.6 for line in stabilizer.lines)
    assert stabilizer.last_canvas.x() < 100


def test_follow_timer_only_starts_when_needed_and_stop_resets():
    stabilizer = _Stabilizer(0)
    stabilizer._start_brush_follow_timer()
    assert stabilizer._brush_follow_timer.starts == 0

    stabilizer.brush_stabilizer_strength = 10
    stabilizer._start_brush_follow_timer()
    stabilizer._start_brush_follow_timer()
    assert stabilizer._brush_follow_timer.starts == 1
    stabilizer._brush_follow_settle = 0.8
    stabilizer._stop_brush_follow_timer()
    assert stabilizer._brush_follow_timer.stops == 1
    assert stabilizer._brush_follow_settle == 0


@pytest.mark.parametrize(("drawing", "tool"), [(False, "brush"), (True, "eraser")])
def test_advance_stops_when_stroke_is_not_active(drawing, tool):
    stabilizer = _Stabilizer(100)
    stabilizer.drawing = drawing
    stabilizer.tool = tool
    stabilizer._last_raw_canvas = QPointF(5, 0)
    stabilizer.last_canvas = QPointF(0, 0)
    stabilizer._advance_stabilized_brush()
    assert stabilizer._brush_follow_timer.stops == 1


def test_advance_handles_arrival_disabled_and_enabled_modes():
    arrived = _Stabilizer(100)
    arrived._last_raw_canvas = QPointF(0.005, 0)
    arrived.last_canvas = QPointF(0, 0)
    arrived._advance_stabilized_brush()
    assert arrived.lines == []

    direct = _Stabilizer(0)
    direct._last_raw_canvas = QPointF(10, 0)
    direct.last_canvas = QPointF(0, 0)
    direct._advance_stabilized_brush()
    assert _xy(direct.last_canvas) == (10, 0)
    assert len(direct.lines) == 1

    smoothed = _Stabilizer(300)
    smoothed._reset_brush_stabilizer(QPointF(0, 0))
    smoothed._last_raw_canvas = QPointF(100, 0)
    smoothed.last_canvas = QPointF(0, 0)
    smoothed._advance_stabilized_brush()
    assert smoothed._brush_follow_settle == pytest.approx(0.09)
    assert smoothed.last_canvas.x() > 0


def test_finish_settles_and_guarantees_raw_endpoint():
    stabilizer = _Stabilizer(100)
    stabilizer._reset_brush_stabilizer(QPointF(0, 0))
    stabilizer.last_canvas = QPointF(0, 0)
    stabilizer._finish_stabilized_brush(QPointF(50, 20), 0.5)
    assert len(stabilizer.lines) > 1
    assert math.isclose(stabilizer.last_canvas.x(), 50)
    assert math.isclose(stabilizer.last_canvas.y(), 20)


def test_finish_without_stabilizer_returns_after_direct_draw():
    stabilizer = _Stabilizer(0)
    stabilizer.last_canvas = QPointF(0, 0)
    stabilizer._finish_stabilized_brush(QPointF(2, 3), 0.5)
    assert len(stabilizer.lines) == 1
    assert _xy(stabilizer.last_canvas) == (2, 3)
