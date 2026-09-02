import sys
from pathlib import Path
from typing import Any
from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget
from .constants import OUTSIDE_MARGIN
from typing import TYPE_CHECKING
from . import constants
from .logging_setup import get_logger

log = get_logger(__name__)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _DragBase
else:
    _DragBase = object


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


def blank_image(fill: Any = Qt.GlobalColor.transparent):
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


class ScreenColorLoupe(QWidget):
    """Click-through screen magnifier shared by every eyedropper path."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(112, 136)
        self._sample = QImage()
        self._before = QColor()
        self._after = QColor()

    def show_at(self, global_position, before_color=None):
        screen = QApplication.screenAt(global_position) or QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.geometry()
        radius = 6
        pixmap = screen.grabWindow(
            0,
            global_position.x() - geometry.x() - radius,
            global_position.y() - geometry.y() - radius,
            radius * 2 + 1,
            radius * 2 + 1,
        )
        self._sample = pixmap.toImage()
        self._before = QColor(before_color) if before_color is not None else QColor()
        self._after = _sample_screen_color(global_position) or QColor()
        x = global_position.x() + 24
        if x + self.width() > geometry.right():
            x = global_position.x() - self.width() - 24
        y = global_position.y() - self.height() // 2
        y = max(geometry.top(), min(geometry.bottom() - self.height() + 1, y))
        self.move(x, y)
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        circle = QRectF(8, 4, 96, 96)
        path = QPainterPath()
        path.addEllipse(circle)
        painter.setClipPath(path)
        painter.fillRect(circle, QColor("#20242a"))
        if not self._sample.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawImage(circle, self._sample)
        painter.setClipping(False)
        painter.setPen(QPen(QColor("white"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(circle)
        center = circle.center()
        painter.drawLine(QPointF(center.x() - 8, center.y()), QPointF(center.x() + 8, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - 8), QPointF(center.x(), center.y() + 8))
        before_rect = QRectF(12, 106, 42, 22)
        after_rect = QRectF(58, 106, 42, 22)
        painter.fillRect(before_rect, self._before if self._before.isValid() else QColor("#000000"))
        painter.fillRect(after_rect, self._after if self._after.isValid() else QColor("#000000"))
        painter.setPen(QPen(QColor("white"), 1))
        painter.drawRect(before_rect)
        painter.drawRect(after_rect)
        painter.drawText(before_rect, Qt.AlignmentFlag.AlignCenter, "前")
        painter.drawText(after_rect, Qt.AlignmentFlag.AlignCenter, "後")


def show_screen_color_loupe(owner, global_position, before_color=None):
    loupe = getattr(owner, "_screen_color_loupe", None)
    if loupe is None:
        loupe = ScreenColorLoupe()
        owner._screen_color_loupe = loupe
    loupe.show_at(global_position, before_color)


def hide_screen_color_loupe(owner):
    loupe = getattr(owner, "_screen_color_loupe", None)
    if loupe is not None:
        loupe.hide()


class ScreenColorPickerOverlay(QWidget):
    """Transparent, desktop-wide input layer for persistent screen picking.

    Unlike ``QWidget.grabMouse()``, this remains an actual top-level window
    over every screen, so pointer movement and the confirming click continue
    to arrive while the cursor is over another application.
    """

    colorPicked = Signal(QColor)
    canceled = Signal()

    def __init__(self, before_color=None):
        super().__init__(None)
        self._before_color = (
            QColor(before_color) if before_color is not None else QColor()
        )
        self._left_pressed = False
        self._finished = False
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        # A fully transparent layered window can be treated as click-through
        # by the Windows compositor. A nearly invisible normal tool window is
        # still hit-testable and is hidden before the actual pixel capture.
        self.setWindowOpacity(0.01)
        self.setStyleSheet("background:#000000;")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)

    @staticmethod
    def virtual_desktop_geometry():
        screens = QApplication.screens()
        if not screens:
            return QRect()
        geometry = QRect(screens[0].geometry())
        for screen in screens[1:]:
            geometry = geometry.united(screen.geometry())
        return geometry

    def start(self):
        geometry = self.virtual_desktop_geometry()
        if geometry.isEmpty():
            self.cancel()
            return
        self.setGeometry(geometry)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        show_screen_color_loupe(self, QCursor.pos(), self._before_color)

    def mouseMoveEvent(self, event):
        show_screen_color_loupe(
            self,
            event.globalPosition().toPoint(),
            self._before_color,
        )
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._left_pressed = True
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._left_pressed:
            self._left_pressed = False
            self.finish(event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.cancel()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
            event.accept()
            return
        super().keyPressEvent(event)

    def finish(self, global_position):
        if self._finished:
            return
        self._finished = True
        hide_screen_color_loupe(self)
        # Remove our top-level windows from the composed desktop before the
        # pixel capture so the sampled value is the underlying application's
        # exact colour.
        self.hide()
        QApplication.processEvents()
        color = _sample_screen_color(global_position)
        self.close()
        if color is not None:
            self.colorPicked.emit(color)
        else:
            self.canceled.emit()
        self.deleteLater()

    def cancel(self):
        if self._finished:
            return
        self._finished = True
        hide_screen_color_loupe(self)
        self.close()
        self.canceled.emit()
        self.deleteLater()

    def closeEvent(self, event):
        hide_screen_color_loupe(self)
        super().closeEvent(event)


class _ScreenColorDragMixin(_DragBase):
    """Mouse, pen and touch drag support for the screen eyedropper.

    At runtime this mixes into a concrete ``QAbstractButton`` subclass, so the
    Qt widget/button API and the ``colorPicked``/``screenColorPicked`` signals
    are all present. The ``TYPE_CHECKING`` declarations below tell pyright about
    the members the concrete host provides (see ``_canvas_members`` for the same
    pattern), so accessing them here is not flagged as an unknown attribute.
    """

    if TYPE_CHECKING:
        colorPicked: Signal
        screenColorPicked: Signal
        # QAbstractButton members not present on the QWidget type-check base.
        click: Any
        setDown: Any

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
        position = self._screen_pick_press_global or QCursor.pos()
        show_screen_color_loupe(self, position)
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
        hide_screen_color_loupe(self)

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
            show_screen_color_loupe(self, event.globalPosition().toPoint())
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
                    show_screen_color_loupe(self, position)
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
