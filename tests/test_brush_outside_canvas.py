"""描画中にキャンバスの外へ出ても、戻ったところまで直線で飛ばない。"""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QApplication

from paintmaskanimator import constants
from paintmaskanimator.canvas import PaintCanvas


def _app():
    return QApplication.instance() or QApplication([])


def _mouse(widget, kind, x, y, buttons):
    pos = QPointF(x, y)
    event = QMouseEvent(
        kind, pos, widget.mapToGlobal(pos),
        Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def test_stroke_leaving_the_canvas_is_not_bridged_on_return():
    app = _app()
    canvas = PaintCanvas()
    try:
        canvas.resize(constants.CANVAS_WIDTH + 200, constants.CANVAS_HEIGHT + 200)
        canvas.zoom = 1.0
        canvas.rotation = 0.0
        canvas.pan = QPointF(100, 100)
        canvas.active_layer_index = next(
            i for i, layer in enumerate(canvas.frames[canvas.current_frame].layers)
            if not getattr(layer, "is_paper", False)
        )
        canvas.set_tool("brush")
        canvas.brush_stabilizer_strength = 0
        canvas.pen_size = 4
        canvas.main_color = QColor(255, 0, 0)
        canvas.color_mode = "main"
        left = Qt.MouseButton.LeftButton
        # 上辺の近くから外へ出て、右へ回り込んで戻る。
        _mouse(canvas, QMouseEvent.Type.MouseButtonPress, 120, 120, left)
        _mouse(canvas, QMouseEvent.Type.MouseMove, 120, 40, left)
        _mouse(canvas, QMouseEvent.Type.MouseMove, 300, 40, left)
        _mouse(canvas, QMouseEvent.Type.MouseMove, 300, 120, left)
        _mouse(
            canvas, QMouseEvent.Type.MouseButtonRelease, 300, 120,
            Qt.MouseButton.NoButton,
        )
        image = canvas.active_layer.image
        red = QColor(255, 0, 0).rgb()
        # 出入りした縦線は描かれ、外を通った区間を結ぶ直線（y=20）は描かれない。
        assert image.pixel(20, 5) == red
        assert image.pixel(200, 5) == red
        assert image.pixel(110, 20) != red
    finally:
        canvas.close()
        app.processEvents()
