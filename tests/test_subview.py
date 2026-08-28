from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator.subview import SUBVIEW_EXTENSIONS, SubViewWidget


def _app():
    return QApplication.instance() or QApplication([])


def _write_image(path, color):
    image = QImage(4, 3, QImage.Format.Format_ARGB32)
    image.fill(color)
    assert image.save(str(path))


def test_folder_loads_supported_images_and_steps_in_name_order(tmp_path):
    app = _app()
    _write_image(tmp_path / "b.png", QColor("blue"))
    _write_image(tmp_path / "a.png", QColor("red"))
    (tmp_path / "note.txt").write_text("not an image", encoding="utf-8")
    view = SubViewWidget()
    try:
        assert view.load_path(tmp_path)
        assert view._path is not None
        assert view._path.name == "a.png"
        assert len(view._files) == 2
        view.step(1)
        assert view._path is not None
        assert view._path.name == "b.png"
        assert view._image.pixelColor(0, 0) == QColor("blue")
    finally:
        view.close()
        app.processEvents()


def test_supported_formats_are_limited_to_requested_reference_types():
    assert SUBVIEW_EXTENSIONS == {".png", ".jpg", ".jpeg", ".tga", ".psd"}


def test_click_picks_color_from_linked_image(tmp_path):
    app = _app()
    path = tmp_path / "sample.png"
    _write_image(path, QColor(12, 34, 56))
    view = SubViewWidget()
    picked = []
    view.colorPicked.connect(picked.append)
    try:
        assert view.load_path(path)
        view.resize(400, 300)
        view.show()
        app.processEvents()
        rect = view.viewer._image_rect()
        view.viewer._pick_color(QPointF(rect.center()))
        assert picked == [QColor(12, 34, 56)]
    finally:
        view.close()
        app.processEvents()
