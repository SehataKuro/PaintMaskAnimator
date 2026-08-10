from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator.canvas import PaintCanvas


def test_quality_transform_preserves_art_outside_selection():
    app = QApplication.instance() or QApplication([])
    canvas = PaintCanvas()
    original = QImage(80, 40, QImage.Format.Format_ARGB32_Premultiplied)
    original.fill(QColor(255, 255, 255, 255))
    original.setPixelColor(32, 15, QColor(0, 0, 255, 255))
    preview = QImage(original.size(), original.format())
    preview.fill(QColor(0, 0, 0, 0))
    preview.setPixelColor(32, 15, QColor(255, 0, 0, 255))

    canvas.selection_polygon = [
        QPointF(10, 10), QPointF(20, 10),
        QPointF(20, 20), QPointF(10, 20),
    ]
    merged = canvas._merge_quality_transform(original, preview)

    assert merged.pixelColor(32, 15) == QColor(0, 0, 255, 255)
    assert merged.pixelColor(40, 15).alpha() == 0
    canvas.deleteLater()
    app.processEvents()
