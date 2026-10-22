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


def _shown_view(tmp_path, app, size=(40, 30)):
    path = tmp_path / "ref.png"
    image = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    assert image.save(str(path))
    view = SubViewWidget()
    assert view.load_path(path)
    view.resize(400, 300)
    view.show()
    app.processEvents()
    return view


def test_right_drag_pans_and_left_click_still_picks(tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    app = _app()
    view = _shown_view(tmp_path, app)
    picked = []
    view.colorPicked.connect(picked.append)
    try:
        viewer = view.viewer
        start = QPoint(200, 150)
        QTest.mousePress(viewer, Qt.MouseButton.RightButton, pos=start)
        QTest.mouseMove(viewer, QPoint(230, 170))
        QTest.mouseRelease(viewer, Qt.MouseButton.RightButton, pos=QPoint(230, 170))
        assert view._pan == QPointF(30, 20), "右ドラッグで画像を動かす"
        assert picked == [], "右ドラッグではスポイトしない"

        center = viewer._image_rect().center().toPoint()
        QTest.mouseClick(viewer, Qt.MouseButton.LeftButton, pos=center)
        assert picked == [QColor("white")], "左クリックはスポイト"
    finally:
        view.close()
        app.processEvents()


def test_wheel_zoom_keeps_the_pixel_under_the_cursor(tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QWheelEvent

    app = _app()
    view = _shown_view(tmp_path, app)
    try:
        viewer = view.viewer
        cursor = QPointF(120, 90)  # 中心からずらした位置
        inverse, _ok = viewer._image_transform().inverted()
        before = inverse.map(cursor)
        event = QWheelEvent(
            cursor, viewer.mapToGlobal(cursor), QPoint(0, 0), QPoint(0, 240),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        QApplication.sendEvent(viewer, event)
        assert view.zoom_slider.value() > 100
        after = viewer._image_transform().map(before)
        assert abs(after.x() - cursor.x()) < 0.5
        assert abs(after.y() - cursor.y()) < 0.5
    finally:
        view.close()
        app.processEvents()
