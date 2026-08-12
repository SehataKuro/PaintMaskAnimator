"""Pure geometry / math helpers extracted from PaintCanvas.

Stateless functions over angles, points, grids and homographies. No widget or
document state — trivially unit-testable. PaintCanvas keeps thin delegating
wrappers so its call sites are unchanged.
"""
import math

import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtGui import QTransform


def normalized_angle(angle):
    return ((float(angle) + 180.0) % 360.0) - 180.0


def rotated_vector(x, y, angle_degrees):
    angle = math.radians(float(angle_degrees))
    return QPointF(
        float(x) * math.cos(angle) - float(y) * math.sin(angle),
        float(x) * math.sin(angle) + float(y) * math.cos(angle),
    )


def quad_homography(source_points, target_points):
    matrix = []
    values = []
    for source, target in zip(source_points, target_points):
        x, y = float(source.x()), float(source.y())
        u, v = float(target.x()), float(target.y())
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        values.append(u)
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values.append(v)
    try:
        a, b, c, d, e, f, g, h = np.linalg.solve(
            np.asarray(matrix, dtype=np.float64),
            np.asarray(values, dtype=np.float64),
        )
    except np.linalg.LinAlgError:
        return QTransform()
    return QTransform(a, d, g, b, e, h, c, f, 1.0)


def mesh_catmull_scalar(p0, p1, p2, p3, t):
    """制御点を通過するCatmull-Rom補間。"""
    t = max(0.0, min(1.0, float(t)))
    t2 = t * t
    t3 = t2 * t
    return 0.5 * (
        2.0 * p1
        + (-p0 + p2) * t
        + (2.0*p0 - 5.0*p1 + 4.0*p2 - p3) * t2
        + (-p0 + 3.0*p1 - 3.0*p2 + p3) * t3
    )


def regular_grid_points(rect, cols, rows=None):
    cols = max(2, int(cols))
    rows = max(2, int(rows if rows is not None else cols))
    width = max(0.0, rect.width())
    height = max(0.0, rect.height())
    return [
        QPointF(
            rect.left() + width * gx / (cols - 1),
            rect.top() + height * gy / (rows - 1),
        )
        for gy in range(rows)
        for gx in range(cols)
    ]
