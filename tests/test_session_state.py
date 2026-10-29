from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator import config
from paintmaskanimator.main_window import MainWindow


def test_close_and_reopen_restores_brush_and_reference(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "reference.png"
    image = QImage(12, 10, QImage.Format.Format_ARGB32)
    image.fill(QColor("red"))
    assert image.save(str(path))
    first = MainWindow()
    first.tools.brush_size_spinbox.setValue(23.5)
    assert first.subview.load_path(path)
    first.subview.zoom_slider.setValue(175)
    first.subview.rotation_slider.setValue(30)
    first.subview._pan = QPointF(15, -22)
    first.subview_dock.toggleView(False)
    first.close()
    second = MainWindow()
    try:
        app.processEvents()
        assert second.tools.brush_size_spinbox.value() == 23.5
        assert second.canvas.pen_size == 23.5
        assert second.subview._path == path
        assert second.subview._zoom == 1.75
        assert second.subview._rotation == 30
        assert second.subview._pan == QPointF(15, -22)
        assert second.subview_dock.isClosed()
    finally:
        second.close()


def test_missing_reference_and_invalid_values_do_not_prevent_startup(tmp_path):
    app = QApplication.instance() or QApplication([])
    config.set_value("last_paint_session", {
        "brush_size": "invalid",
        "subview": {"path": str(tmp_path / "missing.png"), "open": True},
    })
    window = MainWindow()
    try:
        assert window.canvas.pen_size == 8.0
        assert window.subview._image.isNull()
        assert not window.subview_dock.isClosed()
    finally:
        window.close()
        app.processEvents()
