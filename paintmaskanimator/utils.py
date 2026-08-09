from .common import *  # noqa: F401,F403
from . import constants
from .logging_setup import get_logger

log = get_logger(__name__)


def workspace_size():
    return constants.CANVAS_WIDTH + OUTSIDE_MARGIN * 2, constants.CANVAS_HEIGHT + OUTSIDE_MARGIN * 2


def disable_windows_ink_feedback(*widgets):
    """Windows Inkの波紋・長押し・タップ視覚効果を無効化する。"""
    if sys.platform != "win32":
        return

    try:
        import ctypes
        from ctypes import wintypes

        function = (
            ctypes.windll.user32.SetWindowFeedbackSetting
        )
        function.argtypes = [
            wintypes.HWND,
            ctypes.c_uint,
            wintypes.DWORD,
            ctypes.c_uint,
            ctypes.c_void_p,
        ]
        function.restype = wintypes.BOOL

        # FWFS_OVERRIDE
        flags = 0x00000001
        disabled = wintypes.BOOL(False)

        # 1～11:
        # タッチ接触表示、ペン樽表示、タップ、ダブルタップ、
        # 長押し、右タップ、タッチ系表示、PressAndTap。
        feedback_types = range(1, 12)

        for widget in widgets:
            if widget is None:
                continue
            try:
                handle = wintypes.HWND(
                    int(widget.winId())
                )
            except (RuntimeError, ValueError, TypeError) as exc:
                # winId() fails for a not-yet-realized or destroyed widget.
                log.debug("winId() unavailable for widget: %s", exc)
                continue

            for feedback_type in feedback_types:
                try:
                    function(
                        handle,
                        int(feedback_type),
                        flags,
                        ctypes.sizeof(disabled),
                        ctypes.byref(disabled),
                    )
                except OSError as exc:
                    log.debug(
                        "SetWindowFeedbackSetting(%s) failed: %s",
                        feedback_type,
                        exc,
                    )
    except (OSError, AttributeError, ImportError) as exc:
        # Windowsのバージョンや環境が未対応でも起動は継続する。
        log.debug("Windows Ink feedback tweak unavailable: %s", exc)


def blank_image(fill=Qt.GlobalColor.transparent):
    w, h = workspace_size()
    im = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    im.fill(fill)
    return im


def paper_image():
    """White paper only inside the real canvas; outside stays transparent/dark."""
    im = blank_image()
    p = QPainter(im)
    p.fillRect(OUTSIDE_MARGIN, OUTSIDE_MARGIN, constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT, QColor("white"))
    p.end()
    return im


def checker_pixmap(width=48, height=28, cell=7):
    pm = QPixmap(width, height)
    p = QPainter(pm)
    for y in range(0, height, cell):
        for x in range(0, width, cell):
            c = QColor(235,235,235) if ((x//cell)+(y//cell)) % 2 == 0 else QColor(165,165,165)
            p.fillRect(x, y, cell, cell, c)
    p.end()
    return pm


def _sample_screen_color(global_position):
    """Return the opaque screen color at a global position."""
    screen = QApplication.screenAt(global_position) or QApplication.primaryScreen()
    if screen is None:
        return None
    geometry = screen.geometry()
    pixmap = screen.grabWindow(
        0,
        global_position.x() - geometry.x(),
        global_position.y() - geometry.y(),
        1,
        1,
    )
    image = pixmap.toImage()
    if image.isNull():
        return None
    color = image.pixelColor(0, 0)
    color.setAlpha(255)
    return color


class _ScreenColorDragMixin:
    """Mouse, pen and touch drag support for the screen eyedropper."""

    def _init_screen_color_drag(self):
        self._screen_pick_active = False
        self._screen_pick_button = Qt.MouseButton.NoButton
        self._screen_pick_press_global = None
        self._left_drag_candidate = False
        self._screen_pick_cursor_pushed = False
        self._touch_pick_candidate = False
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)

    @staticmethod
    def _event_global_position(event):
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        if hasattr(event, "points"):
            points = event.points()
            if points:
                return points[0].globalPosition().toPoint()
        return None

    def _emit_screen_color(self, color):
        if hasattr(self, "colorPicked"):
            self.colorPicked.emit(color)
        elif hasattr(self, "screenColorPicked"):
            self.screenColorPicked.emit(color)

    def _begin_screen_pick(self, button=Qt.MouseButton.LeftButton):
        self._screen_pick_active = True
        self._screen_pick_button = button
        self._left_drag_candidate = False
        self._touch_pick_candidate = False
        try:
            self.grabMouse()
        except RuntimeError as exc:
            log.debug("grabMouse() failed: %s", exc)
        QApplication.setOverrideCursor(Qt.CursorShape.CrossCursor)
        self._screen_pick_cursor_pushed = True
        try:
            self.setDown(False)
        except RuntimeError as exc:
            log.debug("setDown(False) failed: %s", exc)

    def _cancel_screen_pick_cursor(self):
        try:
            self.releaseMouse()
        except RuntimeError as exc:
            log.debug("releaseMouse() failed: %s", exc)
        if self._screen_pick_cursor_pushed:
            QApplication.restoreOverrideCursor()
            self._screen_pick_cursor_pushed = False

    def _finish_screen_pick(self, global_position):
        self._screen_pick_active = False
        self._screen_pick_button = Qt.MouseButton.NoButton
        self._left_drag_candidate = False
        self._touch_pick_candidate = False
        self._screen_pick_press_global = None
        self._cancel_screen_pick_cursor()
        color = _sample_screen_color(global_position)
        if color is not None:
            self._emit_screen_color(color)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._screen_pick_press_global = event.globalPosition().toPoint()
            self._begin_screen_pick(Qt.MouseButton.RightButton)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._screen_pick_press_global = event.globalPosition().toPoint()
            self._left_drag_candidate = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._screen_pick_active:
            event.accept()
            return
        if (
            self._left_drag_candidate
            and self._screen_pick_press_global is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            distance = (
                event.globalPosition().toPoint() - self._screen_pick_press_global
            ).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._begin_screen_pick(Qt.MouseButton.LeftButton)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            self._screen_pick_active
            and event.button() == self._screen_pick_button
        ):
            self._finish_screen_pick(event.globalPosition().toPoint())
            event.accept()
            return
        self._left_drag_candidate = False
        self._screen_pick_press_global = None
        super().mouseReleaseEvent(event)

    def event(self, event):
        event_type = event.type()
        tablet_press = getattr(QEvent.Type, "TabletPress", None)
        tablet_move = getattr(QEvent.Type, "TabletMove", None)
        tablet_release = getattr(QEvent.Type, "TabletRelease", None)
        touch_begin = getattr(QEvent.Type, "TouchBegin", None)
        touch_update = getattr(QEvent.Type, "TouchUpdate", None)
        touch_end = getattr(QEvent.Type, "TouchEnd", None)
        touch_cancel = getattr(QEvent.Type, "TouchCancel", None)

        if event_type in (tablet_press, touch_begin):
            position = self._event_global_position(event)
            if position is not None:
                self._screen_pick_press_global = position
                self._touch_pick_candidate = True
                event.accept()
                return True

        if event_type in (tablet_move, touch_update):
            position = self._event_global_position(event)
            if position is not None:
                if self._screen_pick_active:
                    event.accept()
                    return True
                if (
                    self._touch_pick_candidate
                    and self._screen_pick_press_global is not None
                    and (position - self._screen_pick_press_global).manhattanLength()
                    >= max(4, QApplication.startDragDistance())
                ):
                    self._begin_screen_pick(Qt.MouseButton.LeftButton)
                    event.accept()
                    return True

        if event_type in (tablet_release, touch_end):
            position = self._event_global_position(event)
            if self._screen_pick_active and position is not None:
                self._finish_screen_pick(position)
                event.accept()
                return True
            if self._touch_pick_candidate:
                self._touch_pick_candidate = False
                self._screen_pick_press_global = None
                # A tap keeps the button's ordinary click behavior.
                QTimer.singleShot(0, self.click)
                event.accept()
                return True

        if event_type == touch_cancel:
            self._screen_pick_active = False
            self._touch_pick_candidate = False
            self._screen_pick_press_global = None
            self._cancel_screen_pick_cursor()
            event.accept()
            return True

        return super().event(event)


def natural_path_key(path):
    import re
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", Path(path).name)]

