import os
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import constants, utils  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def test_workspace_and_generated_images(qapp, monkeypatch):
    monkeypatch.setattr(utils, "OUTSIDE_MARGIN", 2)
    width, height = utils.workspace_size()
    assert width == constants.CANVAS_WIDTH + utils.OUTSIDE_MARGIN * 2
    assert height == constants.CANVAS_HEIGHT + utils.OUTSIDE_MARGIN * 2

    blank = utils.blank_image(QColor("red"))
    assert (blank.width(), blank.height()) == (width, height)
    assert blank.pixelColor(0, 0) == QColor("red")

    paper = utils.paper_image()
    assert paper.pixelColor(0, 0).alpha() == 0
    assert paper.pixelColor(utils.OUTSIDE_MARGIN, utils.OUTSIDE_MARGIN) == QColor("white")


def test_checker_pixmap_alternates_cells(qapp):
    image = utils.checker_pixmap(14, 14, 7).toImage()
    assert image.pixelColor(1, 1) == QColor(235, 235, 235)
    assert image.pixelColor(8, 1) == QColor(165, 165, 165)
    assert image.pixelColor(8, 8) == QColor(235, 235, 235)


def test_natural_path_key_sorts_numbers_naturally():
    paths = ["folder/frame10.png", "folder/frame2.png", "folder/Frame1.png"]
    assert sorted(paths, key=utils.natural_path_key) == [
        "folder/Frame1.png",
        "folder/frame2.png",
        "folder/frame10.png",
    ]


class _Signal:
    def __init__(self):
        self.values = []

    def emit(self, value):
        self.values.append(value)


class _Base:
    def __init__(self):
        self.base_calls = []

    def mousePressEvent(self, event):
        self.base_calls.append(("press", event))

    def mouseMoveEvent(self, event):
        self.base_calls.append(("move", event))

    def mouseReleaseEvent(self, event):
        self.base_calls.append(("release", event))

    def event(self, event):
        self.base_calls.append(("event", event))
        return False


class _Picker(utils._ScreenColorDragMixin, _Base):
    # テスト用の _Signal で本物の Signal を置き換える。
    colorPicked: Any

    def __init__(self):
        _Base.__init__(self)
        self.colorPicked = _Signal()
        self.attributes = []
        self.grabbed = self.released = self.clicked = False
        self.down_values = []
        self._init_screen_color_drag()

    def setAttribute(self, attribute, enabled):
        self.attributes.append((attribute, enabled))

    def grabMouse(self):
        self.grabbed = True

    def releaseMouse(self):
        self.released = True

    def setDown(self, value):
        self.down_values.append(value)

    def click(self):
        self.clicked = True


class _Position:
    def __init__(self, point):
        self.point = point

    def toPoint(self):
        return self.point


class _MouseEvent:
    def __init__(self, button, point, buttons=Qt.MouseButton.NoButton):
        self._button = button
        self._point = point
        self._buttons = buttons
        self.accepted = False

    def button(self):
        return self._button

    def buttons(self):
        return self._buttons

    def globalPosition(self):
        return _Position(self._point)

    def accept(self):
        self.accepted = True


def test_right_mouse_drag_picks_color(qapp, monkeypatch):
    picker = _Picker()
    loupe_positions = []
    monkeypatch.setattr(utils, "_sample_screen_color", lambda _point: QColor("blue"))
    monkeypatch.setattr(
        utils, "show_screen_color_loupe",
        lambda _owner, point, _before=None: loupe_positions.append(point),
    )
    monkeypatch.setattr(
        utils, "hide_screen_color_loupe",
        lambda _owner: loupe_positions.append("hidden"),
    )
    monkeypatch.setattr(QApplication, "setOverrideCursor", lambda *_: None)
    monkeypatch.setattr(QApplication, "restoreOverrideCursor", lambda: None)

    press = _MouseEvent(Qt.MouseButton.RightButton, QPoint(10, 20))
    picker.mousePressEvent(press)
    assert press.accepted and picker._screen_pick_active and picker.grabbed

    move = _MouseEvent(Qt.MouseButton.NoButton, QPoint(12, 22))
    picker.mouseMoveEvent(move)
    assert move.accepted
    assert loupe_positions[:2] == [QPoint(10, 20), QPoint(12, 22)]

    release = _MouseEvent(Qt.MouseButton.RightButton, QPoint(30, 40))
    picker.mouseReleaseEvent(release)
    assert release.accepted and picker.released
    assert picker.colorPicked.values == [QColor("blue")]
    assert not picker._screen_pick_active
    assert loupe_positions[-1] == "hidden"


def test_left_click_delegates_but_drag_starts_picker(qapp, monkeypatch):
    picker = _Picker()
    monkeypatch.setattr(QApplication, "startDragDistance", lambda: 5)
    monkeypatch.setattr(QApplication, "setOverrideCursor", lambda *_: None)

    press = _MouseEvent(Qt.MouseButton.LeftButton, QPoint(0, 0))
    picker.mousePressEvent(press)
    assert picker.base_calls[-1][0] == "press"

    move = _MouseEvent(Qt.MouseButton.NoButton, QPoint(10, 0), Qt.MouseButton.LeftButton)
    picker.mouseMoveEvent(move)
    assert move.accepted and picker._screen_pick_active

    other_release = _MouseEvent(Qt.MouseButton.RightButton, QPoint(10, 0))
    picker.mouseReleaseEvent(other_release)
    assert picker.base_calls[-1][0] == "release"


class _GenericEvent:
    def __init__(self, event_type, point=None, points=False):
        self._type = event_type
        self._point = point
        self._points = points
        self.accepted = False

    def type(self):
        return self._type

    def globalPosition(self):
        return _Position(self._point)

    def points(self):
        return [self] if self._points else []

    def accept(self):
        self.accepted = True


def test_event_position_supports_mouse_touch_and_empty_points():
    assert utils._ScreenColorDragMixin._event_global_position(
        _GenericEvent(None, QPoint(1, 2))
    ) == QPoint(1, 2)

    class TouchOnly:
        def points(self):
            return [_GenericEvent(None, QPoint(3, 4))]

    assert utils._ScreenColorDragMixin._event_global_position(TouchOnly()) == QPoint(3, 4)

    class EmptyTouch:
        def points(self):
            return []

    assert utils._ScreenColorDragMixin._event_global_position(EmptyTouch()) is None


def test_touch_tap_schedules_click_and_cancel_resets(qapp, monkeypatch):
    picker = _Picker()
    callbacks = []
    monkeypatch.setattr(utils.QTimer, "singleShot", lambda _delay, callback: callbacks.append(callback))

    begin = _GenericEvent(utils.QEvent.Type.TouchBegin, QPoint(5, 5))
    assert picker.event(begin) is True
    end = _GenericEvent(utils.QEvent.Type.TouchEnd, QPoint(5, 5))
    assert picker.event(end) is True
    callbacks[0]()
    assert picker.clicked

    picker._screen_pick_active = True
    picker._touch_pick_candidate = True
    cancel = _GenericEvent(utils.QEvent.Type.TouchCancel)
    assert picker.event(cancel) is True
    assert not picker._screen_pick_active and not picker._touch_pick_candidate


def test_sample_screen_color_handles_missing_screen(qapp, monkeypatch):
    monkeypatch.setattr(QApplication, "screenAt", lambda _point: None)
    monkeypatch.setattr(QApplication, "primaryScreen", lambda: None)
    assert utils._sample_screen_color(QPoint(1, 1)) is None


def test_desktop_picker_overlay_spans_screens_and_picks_on_left_click(
    qapp, monkeypatch
):
    overlay = utils.ScreenColorPickerOverlay()
    picked = []
    hidden = []
    overlay.colorPicked.connect(picked.append)
    monkeypatch.setattr(
        utils, "_sample_screen_color", lambda _point: QColor("blue")
    )
    monkeypatch.setattr(
        utils, "hide_screen_color_loupe", lambda owner: hidden.append(owner)
    )

    geometry = overlay.virtual_desktop_geometry()
    assert not geometry.isEmpty()
    assert all(geometry.contains(screen.geometry()) for screen in qapp.screens())
    assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    press = _MouseEvent(Qt.MouseButton.LeftButton, QPoint(20, 30))
    release = _MouseEvent(Qt.MouseButton.LeftButton, QPoint(20, 30))
    overlay.mousePressEvent(press)
    overlay.mouseReleaseEvent(release)

    assert press.accepted and release.accepted
    assert picked == [QColor("blue")]
    assert hidden


def test_disable_windows_ink_is_noop_off_windows(monkeypatch):
    monkeypatch.setattr(utils.sys, "platform", "linux")
    utils.disable_windows_ink_feedback(object())
