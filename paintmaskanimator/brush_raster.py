"""Shared non-antialiased brush geometry for painting and cursor previews."""
import math
import sys
from functools import lru_cache

import numpy as np
from PySide6.QtCore import QPointF, QRect
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtCore import Qt

from . import native

# ARGB32 は 32bit の整数として並ぶので、アルファのバイトの位置は CPU のバイト順で決まる。
_ALPHA_OFFSET = 3 if sys.byteorder == "little" else 0


def paint_segment(painter, start, end, color, width):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    # Subpixel cosmetic Qt pens lower alpha even with AA disabled. Pixel brushes
    # always cover at least one fully opaque pixel.
    painter.setPen(QPen(color, max(1.0, width), Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if start == end:
        # Qt's degenerate floating-point line can discard a click entirely.
        painter.drawPoint(start)
    else:
        painter.drawLine(start, end)


def stamp_mask(center, width):
    margin = math.ceil(width / 2) + 3
    origin_x, origin_y = math.floor(center.x()) - margin, math.floor(center.y()) - margin
    size = margin * 2 + 1
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    local = center - QPointF(origin_x, origin_y)
    paint_segment(painter, local, local, QColor("white"), width)
    painter.end()
    return image, QRect(origin_x, origin_y, size, size)


def stamp_outline(center, width):
    # Integer translation does not change raster coverage. Cache the exact
    # subpixel phase; rounding it would change the preview at fractional sizes.
    x, y = math.floor(center.x()), math.floor(center.y())
    path = QPainterPath(_local_outline(center.x() - x, center.y() - y, width))
    path.translate(x, y)
    return path


@lru_cache(maxsize=64)
def _local_outline(phase_x, phase_y, width):
    image, rect = stamp_mask(QPointF(phase_x, phase_y), width)
    path = QPainterPath()
    for y, left, right in _alpha_runs(image):
        path.addRect(float(rect.x() + left), float(rect.y() + y), float(right - left), 1.0)
    return path.simplified()


def _alpha_runs(image):
    return native.alpha_runs(
        image.constBits(), image.width(), image.height(), image.bytesPerLine(), _ALPHA_OFFSET
    )


def _alpha_runs_numpy(image):
    """``_alpha_runs`` の元の NumPy 版。突き合わせの試験で使う。"""
    alpha = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
    alpha = alpha[:, _ALPHA_OFFSET::4][:, :image.width()]
    runs = []
    for y, row in enumerate(alpha):
        edges = np.flatnonzero(np.diff(np.r_[False, row != 0, False]))
        runs.extend((y, int(left), int(right)) for left, right in zip(edges[::2], edges[1::2]))
    return runs

