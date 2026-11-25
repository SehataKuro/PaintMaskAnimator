"""今の Qt 版のブラシの重さを、egui／Tauri の試作と同じ形で測る（試作の比較用）。

1920×1080 のキャンバスで、egui の --bench と同じ線（200 区間 × 20 本）を描く。
- 区間：マウスの移動の事件を1つ処理する時間（作業画像への描画と update の予約）
- 描画：repaint()（paintEvent を同期で呼ぶ）1回の時間＝1フレームの CPU 時間
使い方：QT_QPA_PLATFORM=offscreen .venv/bin/python prototypes/bench/qt_brush_latency.py
"""
import math
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import constants  # noqa: E402

constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = 1920, 1080
from paintmaskanimator.canvas import PaintCanvas  # noqa: E402


def mouse(canvas, kind, point):
    buttons = Qt.MouseButton.NoButton if kind == QMouseEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
    QApplication.sendEvent(canvas, QMouseEvent(kind, point, point, Qt.MouseButton.LeftButton, buttons,
                                               Qt.KeyboardModifier.NoModifier))


def pct(values, p):
    v = sorted(values)
    return v[round((len(v) - 1) * p)]


def main():
    app = QApplication.instance() or QApplication([])
    canvas = PaintCanvas()
    canvas.resize(1000, 600)
    canvas.show()
    app.processEvents()
    canvas.active_layer_index = next(i for i, layer in enumerate(canvas.frames[0].layers) if not layer.is_paper)
    canvas.set_tool("brush")
    canvas.set_pen_size(12)
    canvas.main_color = QColor(40, 40, 40)
    canvas.pressure_enabled = False
    app.processEvents()
    print(f"キャンバス {canvas.frames[0].layers[0].image.width}×{canvas.frames[0].layers[0].image.height}"
          f"、表示の倍率 {canvas.zoom:.3f}")
    # 画面の座標で、egui の --bench と同じ形の線をなぞる（キャンバスの座標へは widget が直す）。
    to_widget = lambda x, y: canvas.canvas_to_widget(QPointF(x, y)) if hasattr(canvas, "canvas_to_widget") else None
    segment, paint = [], []
    for stroke in range(20):
        points = []
        for i in range(200):
            t = i / 200
            x, y = 100 + 1700 * t, 200 + 40 * stroke + 120 * math.sin(t * 12)
            p = to_widget(x, y)
            points.append(p if p is not None else QPointF(x * canvas.zoom, y * canvas.zoom))
        mouse(canvas, QMouseEvent.Type.MouseButtonPress, points[0])
        for p in points[1:]:
            t0 = time.perf_counter()
            mouse(canvas, QMouseEvent.Type.MouseMove, p)
            t1 = time.perf_counter()
            canvas.repaint()
            t2 = time.perf_counter()
            segment.append((t1 - t0) * 1e6)
            paint.append((t2 - t1) * 1e3)
        mouse(canvas, QMouseEvent.Type.MouseButtonRelease, points[-1])
        app.processEvents()
    print(f"Qt 版のブラシ（区間 {len(segment)} 件）：区間 中央値 {pct(segment, .5):.0f}µs／95% {pct(segment, .95):.0f}µs"
          f"　描画（1フレーム）中央値 {pct(paint, .5):.2f}ms／95% {pct(paint, .95):.2f}ms")


if __name__ == "__main__":
    main()
