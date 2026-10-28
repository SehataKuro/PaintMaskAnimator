"""Compact controls used by the canvas preview bar."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QWidget,
)

from . import view_preview
from .i18n import tr


def channel_icon(channel: str, size: int = 22) -> QIcon:
    """Return a small additive-RGB Venn icon, highlighting ``channel``."""
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale * size / 22, scale * size / 22)
    circles = (
        (QColor("#ff2020"), 14.4, 14.0, view_preview.CHANNEL_RED),
        (QColor("#00e82c"), 11.0, 8.0, view_preview.CHANNEL_GREEN),
        (QColor("#2450ff"), 7.6, 14.0, view_preview.CHANNEL_BLUE),
    )
    selected = channel if channel in view_preview.CHANNELS else view_preview.CHANNEL_RGB
    paths = []
    for _color, cx, cy, _value in circles:
        path = QPainterPath()
        path.addEllipse(QRectF(cx - 6, cy - 6, 12, 12))
        paths.append(path)
    if selected == view_preview.CHANNEL_RGB:
        for path, (color, _cx, _cy, _value) in zip(paths, circles):
            painter.fillPath(path, color)
        red, green, blue = paths
        for path, color in (
            (red.intersected(green), "#ffff00"),
            (green.intersected(blue), "#00ffff"),
            (blue.intersected(red), "#ff00ff"),
            (red.intersected(green).intersected(blue), "#ffffff"),
        ):
            painter.fillPath(path, QColor(color))
    elif selected == view_preview.CHANNEL_ALPHA:
        # A white matte on black reads as alpha, without RGB outlines.
        painter.setPen(QPen(QColor("#909090"), 1.0))
        painter.setBrush(QColor("#202020"))
        painter.drawRoundedRect(QRectF(2, 2, 18, 18), 2, 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(5, 5, 12, 12))
    else:
        # Draw inactive outlines first so none cross the selected fill.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#909090"), 1.2))
        for path, (_color, _cx, _cy, value) in zip(paths, circles):
            if value != selected:
                painter.drawPath(path)
        for path, (color, _cx, _cy, value) in zip(paths, circles):
            if value == selected:
                painter.fillPath(path, color)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


class ChannelPreviewButton(QToolButton):
    """Icon-only channel selector with combo-box-compatible helpers."""

    channelChanged = Signal(str)

    def __init__(self, choices, parent=None):
        super().__init__(parent)
        self._choices = tuple(choices)
        self._channel = view_preview.CHANNEL_RGB
        self._menu = QMenu(self)
        self._actions: list[QAction] = []
        for value, label in self._choices:
            action = self._menu.addAction(channel_icon(value), label)
            action.setData(value)
            action.triggered.connect(lambda _checked=False, v=value: self.setChannel(v, True))
            self._actions.append(action)
        self.setMenu(self._menu)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setFixedSize(30, 24)
        self.setIconSize(QSize(20, 20))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tr("表示するチャンネル"))
        self._refresh()

    def setChannel(self, channel: str, emit: bool = False):
        if channel not in view_preview.CHANNELS:
            channel = view_preview.CHANNEL_RGB
        changed = channel != self._channel
        self._channel = channel
        self._refresh()
        if emit and changed:
            self.channelChanged.emit(channel)

    def _refresh(self):
        self.setIcon(channel_icon(self._channel))
        label = next((label for value, label in self._choices if value == self._channel), "RGB")
        self.setAccessibleName(tr("チャンネル") + ": " + label)
        for action in self._actions:
            action.setCheckable(True)
            action.setChecked(action.data() == self._channel)

    # Compatibility with the former QComboBox API used by integrations/tests.
    def currentData(self):
        return self._channel

    def findData(self, value):
        return next((i for i, (item, _label) in enumerate(self._choices) if item == value), -1)

    def setCurrentIndex(self, index: int):
        if 0 <= index < len(self._choices):
            self.setChannel(self._choices[index][0], True)


class _ExposureLink(QLabel):
    clicked = Signal()
    dragged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_pos: QPoint | None = None
        self._dragged = False
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setStyleSheet("QLabel { color: palette(link); text-decoration: underline; padding: 2px 3px; }")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            dx = event.position().x() - self._press_pos.x()
            if abs(dx) >= 3:
                self._dragged = True
                self.dragged.emit(dx / 20.0)
                self._press_pos = event.position().toPoint()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            if not self._dragged:
                self.clicked.emit()
            self._press_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ExposureControl(QWidget):
    """Link-like exposure value: drag to adjust, click to type."""

    valueChanged = Signal(float)

    def __init__(self, minimum: float, maximum: float, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.link = _ExposureLink()
        self.spin = QDoubleSpinBox()
        self.spin.setRange(minimum, maximum)
        self.spin.setSingleStep(0.1)
        self.spin.setDecimals(1)
        self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin.setFixedWidth(62)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.link)
        self.stack.addWidget(self.spin)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)
        self.link.clicked.connect(self.begin_edit)
        self.link.dragged.connect(self._drag)
        self.spin.valueChanged.connect(self.valueChanged)
        self.spin.editingFinished.connect(self.end_edit)
        self.setValue(0.0)

    def value(self) -> float:
        return self.spin.value()

    def setValue(self, value: float):
        self.spin.setValue(value)
        self.link.setText(f"{self.spin.value():+.1f}")

    def _drag(self, delta: float):
        self.setValue(round((self.value() + delta) * 10.0) / 10.0)

    def begin_edit(self):
        self.stack.setCurrentWidget(self.spin)
        self.spin.setFocus(Qt.FocusReason.MouseFocusReason)
        self.spin.selectAll()

    def end_edit(self):
        self.link.setText(f"{self.value():+.1f}")
        self.stack.setCurrentWidget(self.link)

