"""Background sampling stays semantic even with a custom view colour."""
import pytest
from PySide6.QtCore import QPointF, QRect
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from paintmaskanimator import preferences
from paintmaskanimator.canvas import PaintCanvas


@pytest.fixture
def canvas():
    app = QApplication.instance() or QApplication([])
    widget = PaintCanvas()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_custom_background_sample_selects_background(canvas):
    for layer in canvas.frames[0].layers:
        layer.image.fill(QColor(0, 0, 0, 0))
    canvas.transparent_display_color = QColor("#ff00ff")
    canvas.main_color = QColor("#123456")
    sampled = []
    canvas.colorSampled.connect(sampled.append)
    canvas.sample_color(QPointF(10, 10))
    assert canvas.color_mode == "transparent"
    assert canvas.main_color == QColor("#123456")
    assert sampled == [QColor("white")]


def test_checker_only_matches_configured_background(canvas):
    preferences.set_background_checker_enabled(True)
    preferences.set_background_checker_color("#ff00ff")

    def render():
        image = QImage(32, 32, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        canvas.paint_view_background(painter, QRect(0, 0, 32, 32))
        painter.end()
        return image

    canvas.transparent_display_color = QColor("#ff00ff")
    image = render()
    assert image.pixelColor(2, 2) != image.pixelColor(10, 2)
    canvas.transparent_display_color = QColor("#ffffff")
    image = render()
    assert image.pixelColor(2, 2) == image.pixelColor(10, 2) == QColor("white")
