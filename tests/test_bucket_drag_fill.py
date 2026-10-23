"""バケツのドラッグ塗り：なぞった軌跡が通った囲まれた部分を、それぞれ塗る。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from paintmaskanimator.main_window import MainWindow

BLUE = QColor("#3355ff")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    qapp.processEvents()
    try:
        yield window
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def three_boxes(window):
    """白地に、横に並んだ3つの黒枠（内側は 6〜13 px 四方）を描く。"""
    canvas = window.canvas
    canvas.ensure_editable_key()
    image = canvas.active_layer.image
    image.fill(QColor("white"))
    black = QColor("black")
    for left in (40, 80, 120):
        for i in range(left, left + 20):
            for y in (40, 59):
                image.setPixelColor(i, y, black)
        for y in range(40, 60):
            for x in (left, left + 19):
                image.setPixelColor(x, y, black)
    canvas.active_layer.has_content = True
    canvas.set_tool("bucket")
    canvas.color_mode = "main"
    canvas.main_color = BLUE
    window.tools.bucket_all_frames.setChecked(False)
    return image


def drag(canvas, points):
    widget_points = [canvas.canvas_to_widget(QPointF(x, y)).toPoint() for x, y in points]
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=widget_points[0])
    for point in widget_points[1:]:
        QTest.mouseMove(canvas, point)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=widget_points[-1])


def pixel(window, x, y):
    return window.canvas.active_layer.image.pixelColor(x, y)


def test_dragging_fills_every_enclosed_area_it_crosses(window):
    three_boxes(window)
    canvas = window.canvas
    canvas.zoom = 4.0
    canvas.center_canvas()
    depth = len(canvas.undo_stack)

    # 1つ目の枠の中から、外の背景と線を横切って3つ目の枠の中までなぞる。
    drag(canvas, [(50, 50), (70, 50), (90, 50), (110, 50), (130, 50)])

    for inside_x in (50, 90, 130):
        assert pixel(window, inside_x, 50) == BLUE
    assert pixel(window, 40, 50) == QColor("black"), "線は塗らない"
    assert pixel(window, 70, 50) == QColor("white"), "囲まれていない外の背景は塗らない"
    assert len(canvas.undo_stack) == depth + 1, "1回のドラッグは Undo 1回"

    canvas.undo()
    for inside_x in (50, 90, 130):
        assert pixel(window, inside_x, 50) == QColor("white")


def test_a_plain_click_still_fills_one_area(window):
    three_boxes(window)
    canvas = window.canvas
    canvas.zoom = 4.0
    canvas.center_canvas()
    drag(canvas, [(50, 50)])
    assert pixel(window, 50, 50) == BLUE
    assert pixel(window, 90, 50) == QColor("white")
    canvas.undo()
    assert pixel(window, 50, 50) == QColor("white")


def test_clicking_the_open_background_still_fills_it(window):
    """最初のクリックはこれまでどおり。開いた背景をクリックすれば塗れる。"""
    three_boxes(window)
    canvas = window.canvas
    canvas.zoom = 4.0
    canvas.center_canvas()
    drag(canvas, [(70, 50), (90, 50)])
    assert pixel(window, 70, 50) == BLUE
    assert pixel(window, 90, 50) == BLUE, "なぞった先の囲まれた部分も塗る"


def test_skewer_mode_keeps_click_only(window):
    three_boxes(window)
    canvas = window.canvas
    canvas.zoom = 4.0
    canvas.center_canvas()
    window.tools.bucket_all_frames.setChecked(True)
    drag(canvas, [(50, 50), (90, 50)])
    assert pixel(window, 50, 50) == BLUE
    assert pixel(window, 90, 50) == QColor("white"), "串刺し塗りではなぞりで塗らない"


def test_dragging_fills_areas_closed_by_the_frame_edge(window):
    """フレームの端まで線を引いて閉じた部分も、なぞれば塗る（外の背景は塗らない）。"""
    from PySide6.QtGui import QPainter, QPen

    canvas = window.canvas
    canvas.ensure_editable_key()
    image = canvas.active_layer.image
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setPen(QPen(QColor("black"), 3))
    for left in (40, 140, 240):  # 上端に3つの「山」。上はフレームの端で閉じる
        painter.drawLine(left, 0, left + 40, 60)
        painter.drawLine(left + 40, 60, left + 80, 0)
    painter.end()
    canvas.active_layer.has_content = True
    canvas.set_tool("bucket")
    canvas.color_mode = "main"
    canvas.main_color = BLUE
    window.tools.bucket_all_frames.setChecked(False)
    canvas.zoom = 2.0
    canvas.center_canvas()

    drag(canvas, [(80, 10), (130, 30), (180, 10), (230, 30), (280, 10)])

    for x in (80, 180, 280):
        assert pixel(window, x, 10) == BLUE, "端で閉じた部分は塗る"
    assert pixel(window, 130, 200) == QColor("white"), "外の背景は塗らない"
