"""Unit tests for the extracted pure geometry helpers."""
import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from paintmaskanimator import geometry  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def test_normalized_angle_wraps_to_pm180(qapp):
    assert geometry.normalized_angle(0) == 0
    assert geometry.normalized_angle(180) == -180
    assert geometry.normalized_angle(270) == -90
    assert geometry.normalized_angle(-190) == 170
    assert geometry.normalized_angle(360) == 0


def test_rotated_vector_90_degrees(qapp):
    p = geometry.rotated_vector(1, 0, 90)
    assert math.isclose(p.x(), 0, abs_tol=1e-9)
    assert math.isclose(p.y(), 1, abs_tol=1e-9)


def test_rotated_vector_identity(qapp):
    p = geometry.rotated_vector(3, -4, 0)
    assert (round(p.x(), 6), round(p.y(), 6)) == (3, -4)


def test_regular_grid_points_count_and_corners(qapp):
    rect = QRectF(0, 0, 10, 20)
    pts = geometry.regular_grid_points(rect, 3, 2)
    assert len(pts) == 6
    assert (pts[0].x(), pts[0].y()) == (0, 0)
    assert (pts[-1].x(), pts[-1].y()) == (10, 20)


def test_regular_grid_points_min_two(qapp):
    assert len(geometry.regular_grid_points(QRectF(0, 0, 1, 1), 1, 1)) == 4


def test_quad_homography_identity_maps_points(qapp):
    src = [QPointF(0, 0), QPointF(1, 0), QPointF(1, 1), QPointF(0, 1)]
    t = geometry.quad_homography(src, src)
    mapped = t.map(QPointF(0.5, 0.5))
    assert math.isclose(mapped.x(), 0.5, abs_tol=1e-6)
    assert math.isclose(mapped.y(), 0.5, abs_tol=1e-6)


def test_mesh_catmull_endpoints(qapp):
    # at t=0 returns p1, at t=1 returns p2
    assert geometry.mesh_catmull_scalar(0, 10, 20, 30, 0.0) == 10
    assert geometry.mesh_catmull_scalar(0, 10, 20, 30, 1.0) == 20
