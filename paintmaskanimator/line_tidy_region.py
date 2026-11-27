"""ゴミ取りツールの「線整え」で、キャンバスをなぞった範囲を画素のマスクにする。

ブラシはなぞった線を太さ分ふくらませた帯、投げ縄は囲んだ多角形の中。
"""
from __future__ import annotations

from typing import Iterable, Tuple

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF

from . import imaging

SHAPE_BRUSH = "brush"
SHAPE_LASSO = "lasso"


def region_mask(
    points: Iterable[Tuple[float, float]], shape: str, width: int, height: int,
    brush_width: float = 24.0,
) -> np.ndarray:
    """``points``（キャンバス座標）から ``height×width`` の真偽マスクを作る。"""
    pts = [QPointF(float(x), float(y)) for x, y in points]
    if width <= 0 or height <= 0 or not pts:
        return np.zeros((max(0, height), max(0, width)), dtype=bool)
    if shape == SHAPE_LASSO and len(pts) < 3:
        return np.zeros((height, width), dtype=bool)
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    white = QColor(255, 255, 255, 255)
    if shape == SHAPE_LASSO:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(white)
        painter.drawPolygon(QPolygonF(pts))
    else:
        pen = QPen(white, max(1.0, float(brush_width)))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if len(pts) == 1:
            painter.drawPoint(pts[0])
        else:
            path = QPainterPath(pts[0])
            for point in pts[1:]:
                path.lineTo(point)
            painter.drawPath(path)
    painter.end()
    return imaging.qimage_rgba_array(image)[:, :, 3] > 0
