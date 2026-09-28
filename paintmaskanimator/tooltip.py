"""Rounded, theme-aware tooltips that replace Qt's rectangular tooltip box.

Qt draws tooltips in a plain rectangular window, so a style sheet cannot give
them rounded corners or a shadow. :class:`ToolTipManager` is installed on the
``QApplication`` and answers ``QEvent.ToolTip`` itself for ordinary widgets,
item views, tab bars and menus, showing :class:`ToolTipPopup` instead. Anything
it does not recognise falls through to Qt's own tooltip (styled by the global
style sheet), so no tooltip is ever lost.

Tooltips written with :func:`titled` get a bold first line (the command name)
above a muted description.
"""
import html
import re

import shiboken6

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QLabel, QMenu, QTabBar, QVBoxLayout, QWidget,
)

from . import theme
from .logging_setup import get_logger

log = get_logger(__name__)

_TITLED = re.compile(r"^<b>(.*?)</b><br>(.*)$", re.DOTALL)
_SHADOW = 8          # px reserved around the panel for the soft shadow
_RADIUS = 8
_MAX_WIDTH = 320
_HIDE_AFTER_MS = 12000


def titled(title, detail):
    """A tooltip whose first line is the command name, shown bold."""
    body = html.escape(str(detail)).replace("\n", "<br>")
    return f"<b>{html.escape(str(title))}</b><br>{body}"


class ToolTipPopup(QWidget):
    """Frameless translucent window drawing a rounded panel with a shadow."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(_SHADOW + 10, _SHADOW + 7, _SHADOW + 10, _SHADOW + 8)
        layout.setSpacing(2)
        self.title = QLabel(self)
        self.body = QLabel(self)
        for label in (self.title, self.body):
            label.setTextFormat(Qt.TextFormat.AutoText)
            label.setWordWrap(True)
            label.setMaximumWidth(_MAX_WIDTH)
            layout.addWidget(label)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self.text = ""

    def set_text(self, text):
        self.text = text
        c = theme.palette()
        match = _TITLED.match(text)
        if match:
            title, body = match.group(1), match.group(2)
        else:
            title, body = "", text
        self.title.setVisible(bool(title))
        self.title.setText(title)
        self.title.setStyleSheet(
            f"color:{c['text']};font-weight:bold;background:transparent;"
        )
        self.body.setText(body)
        self.body.setStyleSheet(
            f"color:{c['text_muted'] if title else c['text']};background:transparent;"
        )
        self.body.setVisible(bool(body))
        # Short tips stay on one line; long ones use the full maximum width
        # before wrapping (a wrapping QLabel otherwise picks a narrow width).
        for label in (self.title, self.body):
            label.setWordWrap(False)
            label.setMinimumWidth(0)
            if label.sizeHint().width() > _MAX_WIDTH:
                label.setWordWrap(True)
                label.setMinimumWidth(_MAX_WIDTH)
        self.adjustSize()
        self.update()

    def show_at(self, global_pos):
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        pos = QPoint(global_pos.x() + 4, global_pos.y() + 14)
        if screen is not None:
            area = screen.availableGeometry()
            if pos.x() + self.width() > area.right():
                pos.setX(max(area.left(), area.right() - self.width()))
            if pos.y() + self.height() > area.bottom():
                # Flip above the cursor rather than covering it.
                pos.setY(global_pos.y() - self.height() - 4)
        # The window includes the shadow margin; place the panel itself at pos.
        self.move(pos - QPoint(_SHADOW, _SHADOW))
        self.show()
        self.raise_()
        self._hide_timer.start(_HIDE_AFTER_MS)

    def paintEvent(self, event):
        c = theme.palette()
        dark = theme.resolved_theme() == "dark"
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        panel = QRectF(self.rect()).adjusted(_SHADOW, _SHADOW - 2, -_SHADOW, -_SHADOW - 2)
        # Soft shadow: a few widening, fading rounded rectangles.
        for step in range(_SHADOW, 0, -1):
            alpha = int((70 if dark else 34) * (1 - step / _SHADOW) ** 2)
            shadow = QPainterPath()
            shadow.addRoundedRect(
                panel.adjusted(-step, -step + 2, step, step + 2),
                _RADIUS + step, _RADIUS + step,
            )
            painter.fillPath(shadow, QColor(0, 0, 0, alpha))
        path = QPainterPath()
        path.addRoundedRect(panel, _RADIUS, _RADIUS)
        background = QColor(c["surface_alt"] if dark else c["surface"])
        background.setAlpha(250)
        painter.fillPath(path, background)
        painter.setPen(QPen(QColor(c["border"]), 1))
        painter.drawPath(path)
        painter.end()


class ToolTipManager(QObject):
    """Application-wide event filter that shows :class:`ToolTipPopup`."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.popup = ToolTipPopup()
        self._source = None
        self._source_key = None

    # -- text lookup -------------------------------------------------------
    @staticmethod
    def _tooltip_for(widget, pos):
        """(text, key) for ``pos`` in ``widget``; text is None if not ours."""
        parent = widget.parentWidget()
        if isinstance(parent, QAbstractItemView) and widget is parent.viewport():
            index = parent.indexAt(pos)
            if not index.isValid():
                return "", None
            text = index.data(Qt.ItemDataRole.ToolTipRole)
            return (str(text) if text else ""), (index.row(), index.column())
        if isinstance(widget, QAbstractItemView):
            return None, None  # headers etc.: let Qt handle them
        if isinstance(widget, QTabBar):
            tab = widget.tabAt(pos)
            return (widget.tabToolTip(tab) if tab >= 0 else ""), tab
        if isinstance(widget, QMenu):
            if not widget.toolTipsVisible():
                return "", None
            action = widget.actionAt(pos)
            if action is None or action.toolTip() == action.text().replace("&", ""):
                return "", None
            return action.toolTip(), id(action)
        return widget.toolTip(), None

    # -- event handling ----------------------------------------------------
    def eventFilter(self, watched, event):
        # Installed on the whole application: a failure here must never break
        # event delivery for the rest of the app, so fall back to Qt's tooltip.
        if not shiboken6.isValid(self.popup):
            # The popup is destroyed with the application on exit.
            self._uninstall()
            return False
        try:
            return self._filter(watched, event)
        except Exception:  # noqa: BLE001 - see comment above
            log.exception("custom tooltip failed; falling back to Qt's tooltip")
            self._uninstall()
            return False

    def _uninstall(self):
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def _filter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.ToolTip and isinstance(watched, QWidget):
            text, key = self._tooltip_for(watched, event.pos())
            if not text:
                # None: not ours (Qt handles it). Empty: Qt passes the event on
                # to the parent widget, which reaches this filter again.
                self.hide()
                return False
            if self.popup.isVisible() and watched is self._source and key == self._source_key:
                return True
            self._source, self._source_key = watched, key
            self.popup.set_text(text)
            self.popup.show_at(event.globalPos())
            return True
        if self.popup.isVisible():
            if kind in (
                QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                QEvent.Type.Wheel, QEvent.Type.KeyPress,
                QEvent.Type.WindowDeactivate, QEvent.Type.ApplicationStateChange,
            ):
                self.hide()
            elif watched is self._source and kind in (QEvent.Type.Leave, QEvent.Type.Hide):
                self.hide()
            elif (
                watched is self._source
                and kind == QEvent.Type.MouseMove
                and self._source_key is not None
            ):
                # Moving to another item or tab: drop the stale tip.
                _text, key = self._tooltip_for(watched, event.position().toPoint())
                if key != self._source_key:
                    self.hide()
        return False

    def hide(self):
        if shiboken6.isValid(self.popup):
            self.popup.hide()
        self._source = None
        self._source_key = None


_manager = None


def install(app=None):
    """Install the custom tooltip handler once for the application."""
    global _manager
    app = app or QApplication.instance()
    if app is None or _manager is not None:
        return _manager
    _manager = ToolTipManager(app)
    app.installEventFilter(_manager)
    return _manager

