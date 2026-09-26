"""バケツの「サブカラーを含み塗り」は、使用色パネルの選択ではなくサブカラーを含む。"""
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


def _canvas_with_tools(include_sub):
    from paintmaskanimator.canvas import PaintCanvas
    from paintmaskanimator.toolpanel import ToolPanel

    # flood_fill はトップレベルウィンドウの tools からバケツの設定を読む。
    host = ToolPanel()
    host.tools = host
    host.bucket_include_sub.setChecked(include_sub)
    canvas = PaintCanvas()
    canvas.setParent(host)
    canvas.active_layer_index = next(
        i for i, layer in enumerate(canvas.frames[canvas.current_frame].layers)
        if not getattr(layer, "is_paper", False)
    )
    canvas.set_tool("bucket")
    canvas.ensure_editable_key()
    return host, canvas


def _draw_lines(canvas):
    """左右を分ける黒い主線と、左側の領域に接する赤い線を描く。"""
    image = canvas.active_layer.image
    painter = QPainter(image)
    painter.fillRect(60, 0, 4, image.height(), QColor("black"))
    painter.fillRect(20, 0, 3, image.height(), QColor(255, 0, 0))
    painter.end()


@pytest.mark.parametrize("include_sub", [True, False])
def test_include_fill_uses_sub_color(qapp, include_sub):
    host, canvas = _canvas_with_tools(include_sub)
    try:
        _draw_lines(canvas)
        canvas.sub_color = QColor(255, 0, 0)
        canvas.main_color = QColor("#22aa44")
        canvas.color_mode = "main"
        # 使用色パネルの選択は含み塗りに影響しない。
        canvas.selected_used_color_rgbs = {(0, 0, 0)}
        canvas.flood_fill(QPointF(40, 10))
        after = _rgba(canvas.active_layer.image)
        red_line = after[10, 21, :3].tolist()
        black_line = after[10, 61, :3].tolist()
        assert black_line == [0, 0, 0]
        if include_sub:
            assert red_line == [0x22, 0xAA, 0x44]
        else:
            assert red_line == [255, 0, 0]
    finally:
        host.close()
