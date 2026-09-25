"""macOSトラックパッドのホイールイベントでズームが暴走しないことの確認。

トラックパッドはマウスホイール(1ノッチ=120)よりずっと細かいイベントを大量に
送り、開始/終了時には移動量0のイベントも送る。
"""
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from paintmaskanimator.canvas import PaintCanvas
from paintmaskanimator.subview import SubViewWidget


def _app():
    return QApplication.instance() or QApplication([])


def _wheel(widget, dy, dx=0):
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(
        pos, pos, QPoint(0, 0), QPoint(dx, dy),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate, False,
    )
    QApplication.sendEvent(widget, event)


def test_canvas_mouse_wheel_notch_keeps_115_step():
    app = _app()
    canvas = PaintCanvas()
    try:
        canvas.resize(400, 300)
        canvas.zoom = 1.0
        _wheel(canvas, 120)
        assert abs(canvas.zoom - 1.15) < 1e-6
        _wheel(canvas, -120)
        assert abs(canvas.zoom - 1.0) < 1e-6
    finally:
        canvas.close()
        app.processEvents()


def test_canvas_trackpad_small_deltas_scale_proportionally():
    app = _app()
    canvas = PaintCanvas()
    try:
        canvas.resize(400, 300)
        canvas.zoom = 1.0
        for _ in range(30):
            _wheel(canvas, 4)  # 合計120 = ホイール1ノッチ分
        assert abs(canvas.zoom - 1.15) < 1e-6
    finally:
        canvas.close()
        app.processEvents()


def test_canvas_ignores_zero_vertical_delta():
    app = _app()
    canvas = PaintCanvas()
    try:
        canvas.resize(400, 300)
        canvas.zoom = 1.0
        _wheel(canvas, 0)          # ScrollBegin/End 相当
        _wheel(canvas, 0, dx=40)   # 横スワイプ
        assert canvas.zoom == 1.0
    finally:
        canvas.close()
        app.processEvents()


def test_subview_trackpad_small_deltas_accumulate_past_rounding():
    app = _app()
    view = SubViewWidget()
    try:
        view.resize(300, 300)
        start = view.zoom_slider.value()
        for _ in range(30):
            _wheel(view.viewer, 4)
        # 1イベントでは1%未満でも、持ち越して最終的にホイール1ノッチ分進む。
        assert view.zoom_slider.value() == round(start * 1.15)
    finally:
        view.close()
        app.processEvents()


