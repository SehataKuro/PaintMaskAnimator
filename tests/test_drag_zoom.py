"""一時ズーム（Ctrl/⌥＋Space＋ドラッグ）は CLIP STUDIO PAINT と同じ操作にする。

右へドラッグで拡大・左で縮小し、カーソルは虫眼鏡になる。
"""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from paintmaskanimator.canvas import PaintCanvas
from paintmaskanimator.utils import drag_zoom_factor, magnifier_cursor


def _app():
    return QApplication.instance() or QApplication([])


def _mouse(widget, kind, x, y, buttons):
    pos = QPointF(x, y)
    event = QMouseEvent(
        kind, pos, widget.mapToGlobal(pos),
        Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def test_drag_zoom_factor_is_right_to_zoom_in():
    assert drag_zoom_factor(50) > 1.0
    assert drag_zoom_factor(-50) < 1.0
    assert drag_zoom_factor(0) == 1.0


def test_canvas_hold_zoom_drags_horizontally_like_clip_studio():
    app = _app()
    canvas = PaintCanvas()
    try:
        canvas.resize(400, 300)
        canvas.zoom = 1.0
        canvas.temp_tool = "zoom"
        left = Qt.MouseButton.LeftButton
        _mouse(canvas, QMouseEvent.Type.MouseButtonPress, 200, 150, left)
        assert (
            canvas.cursor().pixmap().cacheKey()
            == magnifier_cursor().pixmap().cacheKey()
        )
        # 縦の移動ではズームしない。
        _mouse(canvas, QMouseEvent.Type.MouseMove, 200, 100, left)
        assert abs(canvas.zoom - 1.0) < 1e-9
        _mouse(canvas, QMouseEvent.Type.MouseMove, 250, 100, left)
        zoomed_in = canvas.zoom
        assert zoomed_in > 1.0
        _mouse(canvas, QMouseEvent.Type.MouseMove, 150, 100, left)
        assert canvas.zoom < zoomed_in
        _mouse(
            canvas, QMouseEvent.Type.MouseButtonRelease, 150, 100,
            Qt.MouseButton.NoButton,
        )
    finally:
        canvas.close()
        app.processEvents()
