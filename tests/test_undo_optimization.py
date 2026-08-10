import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication

from paintmaskanimator.canvas import PaintCanvas


def _app():
    return QApplication.instance() or QApplication([])


def test_region_undo_redo_restores_only_requested_area():
    _app()
    canvas = PaintCanvas()
    rect = QRect(100, 110, 24, 18)
    before = canvas.active_layer.image.copy(rect)
    assert canvas.push_layer_region_undo(rect)

    painter = QPainter(canvas.active_layer.image)
    painter.fillRect(rect, QColor("red"))
    painter.end()
    canvas.active_layer.has_content = True

    assert canvas.undo_stack[-1][0] == "layer_region"
    assert canvas.undo_stack[-1][4].size() == rect.size()
    canvas.undo()
    assert canvas.active_layer.image.copy(rect) == before
    canvas.redo()
    assert canvas.active_layer.image.pixelColor(rect.center()) == QColor("red")


def test_small_bucket_fill_replaces_full_snapshot_with_region():
    _app()
    canvas = PaintCanvas()
    source_rect = QRect(100, 100, 20, 16)
    painter = QPainter(canvas.active_layer.image)
    painter.fillRect(source_rect, QColor("red"))
    painter.end()
    canvas.active_layer.has_content = True
    canvas.main_color = QColor("blue")

    canvas.flood_fill(QPoint(105, 105))

    entry = canvas.undo_stack[-1]
    assert entry[0] == "layer_region"
    assert entry[3] == source_rect
    assert entry[4].size() == source_rect.size()
    canvas.undo()
    assert canvas.active_layer.image.pixelColor(105, 105) == QColor("red")
    canvas.redo()
    assert canvas.active_layer.image.pixelColor(105, 105) == QColor("blue")
