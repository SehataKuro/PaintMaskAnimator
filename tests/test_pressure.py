"""Unit tests for the pressure-curve bezier math and PressureCurveWidget logic.

These functions drive how tablet pressure maps to brush size, so they make a
good regression safety net: they are pure math (the ``_pressure_bezier_*``
helpers) plus a handful of GUI-independent widget behaviours that run fine
under the offscreen Qt platform.
"""
import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from paintmaskanimator import pressure  # noqa: E402
from paintmaskanimator.pressure import (  # noqa: E402
    PressureCurveWidget,
    _cubic_bezier_value,
    _pressure_bezier_at,
    _pressure_bezier_segments,
)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


# --- _cubic_bezier_value -------------------------------------------------


def test_cubic_bezier_endpoints():
    assert _cubic_bezier_value(2.0, 5.0, 7.0, 9.0, 0.0) == 2.0
    assert _cubic_bezier_value(2.0, 5.0, 7.0, 9.0, 1.0) == 9.0


def test_cubic_bezier_straight_line_midpoint():
    # Evenly spaced control points -> linear -> midpoint is the average.
    assert math.isclose(_cubic_bezier_value(0.0, 1.0, 2.0, 3.0, 0.5), 1.5)


# --- _pressure_bezier_segments -------------------------------------------


def test_segments_default_when_too_few_points():
    segs = _pressure_bezier_segments([(0.5, 0.5)])
    assert len(segs) == 1
    p0, _c1, _c2, p3 = segs[0]
    assert p0 == (0.0, 0.0)
    assert p3 == (1.0, 1.0)


def test_segments_clamp_out_of_range_points():
    segs = _pressure_bezier_segments([(-1.0, 2.0), (2.0, -1.0)])
    p0 = segs[0][0]
    p3 = segs[-1][3]
    assert p0 == (0.0, 1.0)
    assert p3 == (1.0, 0.0)


def test_segments_count_matches_anchors():
    segs = _pressure_bezier_segments([(0.0, 0.0), (0.5, 0.8), (1.0, 1.0)])
    assert len(segs) == 2


def test_segments_dedupe_near_equal_x():
    # Two points sharing (nearly) the same x collapse to one -> falls back.
    segs = _pressure_bezier_segments([(0.5, 0.2), (0.5 + 1e-9, 0.9)])
    assert len(segs) == 1


def test_segment_controls_stay_in_x_span_and_unit_y():
    segs = _pressure_bezier_segments([(0.0, 0.0), (0.3, 0.9), (0.7, 0.1), (1.0, 1.0)])
    for p0, c1, c2, p3 in segs:
        for c in (c1, c2):
            assert p0[0] <= c[0] <= p3[0]
            assert 0.0 <= c[1] <= 1.0


# --- _pressure_bezier_at -------------------------------------------------


def test_bezier_at_identity_curve_is_monotonic():
    points = [(0.0, 0.0), (1.0, 1.0)]
    prev = -1.0
    for i in range(21):
        x = i / 20.0
        y = _pressure_bezier_at(points, x)
        assert 0.0 <= y <= 1.0
        assert y >= prev - 1e-9
        prev = y
    assert math.isclose(_pressure_bezier_at(points, 0.5), 0.5, abs_tol=1e-6)


def test_bezier_at_clamps_pressure_domain():
    points = [(0.0, 0.1), (1.0, 0.9)]
    assert _pressure_bezier_at(points, -5.0) == pytest.approx(0.1)
    assert _pressure_bezier_at(points, 5.0) == pytest.approx(0.9)


def test_bezier_at_passes_through_anchor():
    points = [(0.0, 0.0), (0.5, 0.25), (1.0, 1.0)]
    assert _pressure_bezier_at(points, 0.5) == pytest.approx(0.25, abs=1e-3)


def test_bezier_at_flat_curve_returns_constant():
    points = [(0.0, 0.4), (1.0, 0.4)]
    for x in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert _pressure_bezier_at(points, x) == pytest.approx(0.4, abs=1e-6)


# --- PressureCurveWidget --------------------------------------------------


def test_widget_from_exponent_has_three_points(qapp):
    w = PressureCurveWidget(2.0)
    pts = w.points()
    assert len(pts) == 3
    assert pts[0] == [0.0, 0.0]
    assert pts[-1] == [1.0, 1.0]
    # Middle anchor reflects the exponent (0.5 ** 2 == 0.25).
    assert pts[1][1] == pytest.approx(0.25)


def test_widget_from_point_list_sorts_and_pins_ends(qapp):
    w = PressureCurveWidget([(1.0, 1.0), (0.4, 0.7), (0.0, 0.2)])
    pts = w.points()
    xs = [p[0] for p in pts]
    assert xs == sorted(xs)
    assert pts[0][0] == 0.0
    assert pts[-1][0] == 1.0


def test_widget_skips_malformed_points(qapp):
    w = PressureCurveWidget([(0.0, 0.0), ("bad", None), (1.0, 1.0)])
    assert len(w.points()) == 2


def test_widget_exponent_legacy_constant(qapp):
    assert PressureCurveWidget(1.0).exponent() == 1.0


def test_widget_value_point_roundtrip(qapp):
    w = PressureCurveWidget(1.0)
    w.resize(300, 200)
    x, y = 0.3, 0.6
    screen = w._value_to_point(x, y)
    rx, ry = w._point_to_value(screen)
    # x maps through left/width consistently -> exact; y differs by ~1px
    # because Qt's inclusive rect.bottom() is (top + height - 1).
    assert rx == pytest.approx(x, abs=1e-6)
    assert ry == pytest.approx(y, abs=0.02)


def test_widget_nearest_index_finds_anchor(qapp):
    w = PressureCurveWidget(1.0)
    w.resize(300, 200)
    screen = w._value_to_point(*w._points[1])
    assert w._nearest_index(screen) == 1


def test_widget_nearest_index_none_when_far(qapp):
    w = PressureCurveWidget(1.0)
    w.resize(300, 200)
    assert w._nearest_index(QPointF(-500, -500)) is None


def test_module_exposes_logger():
    assert pressure.log is not None
