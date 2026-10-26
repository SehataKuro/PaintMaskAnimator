"""バケツの参照レイヤー：どの絵を見て塗る範囲を決めるか。

塗る先はいつもアクティブレイヤーのまま、判定だけを別の絵から行えることを見る。
"""
from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator import bucket_fill
from paintmaskanimator.canvas import PaintCanvas
from paintmaskanimator.toolpanel import ToolPanel

BLUE = QColor("#3355ff")
BOX = (40, 40, 59, 59)  # 黒枠の外側の座標（内側は 41〜58）
INSIDE = (50, 50)
OUTSIDE = (100, 100)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def draw_box(image):
    """白地に黒枠を1つ描く。"""
    image.fill(QColor("white"))
    left, top, right, bottom = BOX
    black = QColor("black")
    for x in range(left, right + 1):
        image.setPixelColor(x, top, black)
        image.setPixelColor(x, bottom, black)
    for y in range(top, bottom + 1):
        image.setPixelColor(left, y, black)
        image.setPixelColor(right, y, black)


@pytest.fixture
def canvas(qapp):
    """黒枠を描いた下のレイヤーと、その上の空のレイヤー（塗る先）。"""
    host: Any = ToolPanel()
    host.tools = host
    # 線の延長は既定で入っているが、この試験では関係ないので外しておく。
    host.bucket_extend_lines.setChecked(False)
    canvas = PaintCanvas()
    canvas.setParent(host)
    canvas.active_layer_index = next(
        i for i, layer in enumerate(canvas.frames[canvas.current_frame].layers)
        if not getattr(layer, "is_paper", False)
    )
    canvas.ensure_editable_key()
    draw_box(canvas.active_layer.image)
    canvas.active_layer.has_content = True
    canvas.add_layer()
    canvas.ensure_editable_key()
    canvas.set_tool("bucket")
    canvas.color_mode = "main"
    canvas.main_color = BLUE
    try:
        yield host, canvas
    finally:
        canvas.setParent(None)
        canvas.deleteLater()
        host.deleteLater()
        qapp.processEvents()


def set_reference(host, value):
    index = host.bucket_reference.findData(value)
    assert index >= 0
    host.bucket_reference.setCurrentIndex(index)


def color_at(canvas, point):
    return canvas.active_layer.image.pixelColor(*point)


def test_layer_reference_does_not_see_the_line_layer(canvas):
    """「選択したレイヤーのみ」：空のレイヤーなので枠の外まで塗れる。"""
    host, paint_canvas = canvas
    set_reference(host, bucket_fill.REFERENCE_LAYER)
    paint_canvas.flood_fill(QPointF(*INSIDE))
    assert color_at(paint_canvas, INSIDE) == BLUE
    assert color_at(paint_canvas, OUTSIDE) == BLUE


def test_canvas_reference_stops_at_a_line_on_another_layer(canvas):
    """「キャンバス全体」：下のレイヤーの黒枠で区切られた内側だけを塗る。"""
    host, paint_canvas = canvas
    set_reference(host, bucket_fill.REFERENCE_CANVAS)
    painted = int(paint_canvas.active_layer_index)
    paint_canvas.flood_fill(QPointF(*INSIDE))
    assert color_at(paint_canvas, INSIDE) == BLUE
    assert color_at(paint_canvas, OUTSIDE).alpha() == 0
    # 判定に使っただけで、下のレイヤーには触れない。
    assert painted == paint_canvas.active_layer_index
    lower = paint_canvas.frames[paint_canvas.current_frame].layers[painted - 1]
    assert lower.image.pixelColor(*INSIDE) == QColor("white")


def test_initial_state_reference_ignores_a_later_line(canvas):
    """「色塗り前の初期状態」：あとで引いた仕切り線を見ずに塗る。"""
    host, paint_canvas = canvas
    layer = paint_canvas.active_layer
    draw_box(layer.image)
    layer.has_content = True
    # 塗る前の絵を保持してから、枠の内側を上下に分ける線を引く。
    layer.initial_image = QImage(layer.image)
    for x in range(BOX[0], BOX[2] + 1):
        layer.image.setPixelColor(x, 50, QColor("black"))

    set_reference(host, bucket_fill.REFERENCE_LAYER)
    paint_canvas.flood_fill(QPointF(45, 45))
    assert color_at(paint_canvas, (45, 55)) != BLUE, "仕切り線で止まる"

    draw_box(layer.image)
    for x in range(BOX[0], BOX[2] + 1):
        layer.image.setPixelColor(x, 50, QColor("black"))
    set_reference(host, bucket_fill.REFERENCE_INITIAL)
    paint_canvas.flood_fill(QPointF(45, 45))
    assert color_at(paint_canvas, (45, 45)) == BLUE
    assert color_at(paint_canvas, (45, 55)) == BLUE, "仕切り線を見ない"


def test_tool_panel_reference_reaches_the_options(qapp):
    panel = ToolPanel()
    try:
        options = bucket_fill.BucketOptions.from_tools(panel)
        assert options.reference == bucket_fill.REFERENCE_LAYER
        set_reference(panel, bucket_fill.REFERENCE_CANVAS)
        assert (
            bucket_fill.BucketOptions.from_tools(panel).reference
            == bucket_fill.REFERENCE_CANVAS
        )
    finally:
        panel.deleteLater()
        qapp.processEvents()
