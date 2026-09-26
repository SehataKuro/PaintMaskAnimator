"""塗りつぶし（バケツ）と投げ縄塗りの不透明度。

100%は選択RGBをそのまま書き込み、100%未満は下地RGB（透明は白）と混色する。
アルファは常に255で、ブラシなど他のツールは100%のまま。
"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _rgba(image):
    im = image.convertToFormat(QImage.Format.Format_RGBA8888)
    rows = np.frombuffer(im.constBits(), dtype=np.uint8).reshape(
        (im.height(), im.bytesPerLine())
    )
    return rows[:, : im.width() * 4].reshape((im.height(), im.width(), 4)).copy()


def _canvas(tool):
    from paintmaskanimator.canvas import PaintCanvas

    canvas = PaintCanvas()
    canvas.active_layer_index = next(
        i for i, layer in enumerate(canvas.frames[canvas.current_frame].layers)
        if not getattr(layer, "is_paper", False)
    )
    canvas.set_tool(tool)
    canvas.ensure_editable_key()
    canvas.color_mode = "main"
    return canvas


def test_bucket_full_opacity_writes_exact_rgb(qapp):
    canvas = _canvas("bucket")
    canvas.main_color = QColor(0, 0, 200)
    canvas.flood_fill(QPointF(10, 10))
    assert _rgba(canvas.active_layer.image)[10, 10].tolist() == [0, 0, 200, 255]


def test_bucket_half_opacity_mixes_with_underlying_color(qapp):
    canvas = _canvas("bucket")
    painter = QPainter(canvas.active_layer.image)
    painter.fillRect(0, 0, 40, 40, QColor(200, 0, 0))
    painter.end()
    canvas.fill_opacity = 0.5
    canvas.main_color = QColor(0, 0, 200)
    canvas.flood_fill(QPointF(10, 10))
    after = _rgba(canvas.active_layer.image)
    assert after[10, 10].tolist() == [100, 0, 100, 255]
    # 透明な下地は白とみなして混色する。
    canvas.flood_fill(QPointF(100, 100))
    after = _rgba(canvas.active_layer.image)
    assert after[100, 100].tolist() == [128, 128, 228, 255]


def test_lasso_fill_half_opacity_mixes_with_underlying_color(qapp):
    canvas = _canvas("lasso_fill")
    painter = QPainter(canvas.active_layer.image)
    painter.fillRect(0, 0, 80, 80, QColor(200, 0, 0))
    painter.end()
    canvas.fill_opacity = 0.5
    canvas.main_color = QColor(0, 0, 200)
    canvas.fill_lasso_polygon([
        QPointF(10, 10), QPointF(60, 10), QPointF(60, 60), QPointF(10, 60),
    ])
    after = _rgba(canvas.active_layer.image)
    assert after[30, 30].tolist() == [100, 0, 100, 255]
    # 範囲外は変わらない。
    assert after[70, 70].tolist() == [200, 0, 0, 255]


def test_tool_panel_shows_opacity_only_for_bucket_and_lasso_fill(qapp):
    from paintmaskanimator.toolpanel import ToolPanel

    panel = ToolPanel()
    try:
        for tool, visible in (
            ("bucket", True), ("lasso_fill", True),
            ("brush", False), ("line", False), ("shape", False),
        ):
            panel.select_tool(tool)
            assert (not panel.fill_opacity_row.isHidden()) == visible, tool
    finally:
        panel.close()
