from .common import *  # noqa: F401,F403
from .utils import _ScreenColorDragMixin
from .logging_setup import get_logger

from contextlib import contextmanager

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QGraphicsOpacityEffect

log = get_logger(__name__)

# 使用色をドラッグ＆ドロップで統合するときに使う専用MIME形式。
USED_COLOR_MIME = "application/x-pma-used-color"


def _draw_eye_icon(painter, rect, color, is_open):
    """モダンなアウトライン風の目アイコンを描く。開＝表示、閉＝非表示。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # アイコン領域を正方形へ寄せ、上下左右に少し余白を取る。
    size = min(rect.width(), rect.height())
    box = QRectF(0, 0, size, size)
    box.moveCenter(QPointF(rect.center()))
    box.adjust(3.0, 5.0, -3.0, -5.0)

    pen = QPen(color)
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    cx = box.center().x()
    cy = box.center().y()
    half_w = box.width() / 2.0
    lid = box.height() / 2.0

    # 上まぶた・下まぶたを2本の対称な弧で描くアーモンド形。
    outline = QPainterPath()
    outline.moveTo(cx - half_w, cy)
    outline.quadTo(cx, cy - lid, cx + half_w, cy)
    outline.quadTo(cx, cy + lid, cx - half_w, cy)
    painter.drawPath(outline)

    if is_open:
        # 瞳孔は塗りつぶしの円で表現する。
        pupil_r = min(half_w, lid) * 0.55
        painter.setBrush(color)
        painter.drawEllipse(QPointF(cx, cy), pupil_r, pupil_r)
    else:
        # 非表示時は斜線を重ねて「閉じている」ことを示す。
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(
            QPointF(box.left(), box.bottom()),
            QPointF(box.right(), box.top()),
        )
    painter.restore()


class SourceColorButton(_ScreenColorDragMixin, QToolButton):
    screenColorPicked = Signal(QColor)
    contextMenuRequested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_screen_color_drag()
        self._right_click_candidate = False
        self._right_click_press_global = None
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._right_click_candidate = True
            self._right_click_press_global = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._right_click_candidate
            and self._right_click_press_global is not None
            and event.buttons() & Qt.MouseButton.RightButton
        ):
            distance = (
                event.globalPosition().toPoint() - self._right_click_press_global
            ).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._right_click_candidate = False
                self._screen_pick_press_global = self._right_click_press_global
                self._begin_screen_pick(Qt.MouseButton.RightButton)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.MouseButton.RightButton
            and self._right_click_candidate
            and not self._screen_pick_active
        ):
            self._right_click_candidate = False
            global_pos = event.globalPosition().toPoint()
            self._right_click_press_global = None
            self.contextMenuRequested.emit(global_pos)
            event.accept()
            return
        self._right_click_candidate = False
        self._right_click_press_global = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self.contextMenuRequested.emit(event.globalPos())
        event.accept()


class ColorVisibilityCheckBox(QCheckBox):
    """固定幅 [●]/[-] と、複数行をなぞる表示ON/OFFに対応。"""
    altClicked = Signal()
    sweepStarted = Signal(bool)
    sweepMoved = Signal(QPoint)
    sweepFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sweeping = False
        self._consume_release = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(30, 24)
        self.setStyleSheet(
            "QCheckBox{background:transparent;border:none;padding:0px;}"
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.isChecked():
            color = (
                self.palette().text().color()
                if self.isEnabled() else self.palette().mid().color()
            )
        else:
            # 非表示の目は控えめなグレーで、状態差を色でも伝える。
            color = self.palette().mid().color()
        _draw_eye_icon(painter, self.rect(), color, self.isChecked())
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self._consume_release = True
            self.altClicked.emit()
            event.accept()
            return

        self._consume_release = False
        self._sweeping = True
        try:
            self.grabMouse()
        except RuntimeError as exc:
            log.debug("grabMouse() failed: %s", exc)
        self.sweepStarted.emit(not self.isChecked())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._sweeping:
            self.sweepMoved.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._consume_release and event.button() == Qt.MouseButton.LeftButton:
            self._consume_release = False
            event.accept()
            return
        if self._sweeping and event.button() == Qt.MouseButton.LeftButton:
            self._sweeping = False
            try:
                self.releaseMouse()
            except RuntimeError as exc:
                log.debug("releaseMouse() failed: %s", exc)
            self.sweepFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)




class MaskColorCheckBox(QCheckBox):
    """使用色マスク専用。クリックと上下スライドのON/OFFに対応。"""
    altClicked = Signal()
    sweepStarted = Signal(bool)
    sweepMoved = Signal(QPoint)
    sweepFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sweeping = False
        self._consume_release = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(30, 24)
        self.setStyleSheet("QCheckBox{background:transparent;border:none;padding:0;}")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        color = (
            self.palette().text().color()
            if self.isEnabled() else self.palette().mid().color()
        )
        painter.setPen(color)
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            "[●]" if self.isChecked() else "[-]",
        )
        painter.end()

    def nextCheckState(self):
        self.setChecked(not self.isChecked())
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self._consume_release = True
            self.altClicked.emit()
            event.accept()
            return
        self._consume_release = False
        self._sweeping = True
        try:
            self.grabMouse()
        except RuntimeError as exc:
            log.debug("grabMouse() failed: %s", exc)
        self.sweepStarted.emit(not self.isChecked())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._sweeping:
            self.sweepMoved.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._consume_release and event.button() == Qt.MouseButton.LeftButton:
            self._consume_release = False
            event.accept()
            return
        if self._sweeping and event.button() == Qt.MouseButton.LeftButton:
            self._sweeping = False
            try:
                self.releaseMouse()
            except RuntimeError as exc:
                log.debug("releaseMouse() failed: %s", exc)
            self.sweepFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _draw_check_glyph(painter, rect, color, is_checked):
    """モダンな角丸チェックボックスを描く。ON＝アクセント塗り＋チェック。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    size = min(rect.width(), rect.height())
    box = QRectF(0, 0, size, size)
    box.moveCenter(QPointF(rect.center()))
    box.adjust(6.0, 6.0, -6.0, -6.0)

    if is_checked:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(box, 3.0, 3.0)
        # 白いチェックマーク。
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(box.left() + box.width() * 0.24, box.top() + box.height() * 0.52)
        path.lineTo(box.left() + box.width() * 0.44, box.top() + box.height() * 0.72)
        path.lineTo(box.left() + box.width() * 0.78, box.top() + box.height() * 0.28)
        painter.drawPath(path)
    else:
        pen = QPen(color)
        pen.setWidthF(1.4)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, 3.0, 3.0)
    painter.restore()


class ColorSelectionCheckBox(QCheckBox):
    """行頭の「選択」チェック。クリックと上下スイープの一括ON/OFFに対応。"""
    altClicked = Signal()
    sweepStarted = Signal(bool)
    sweepMoved = Signal(QPoint)
    sweepFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sweeping = False
        self._consume_release = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(30, 24)
        self.setStyleSheet(
            "QCheckBox{background:transparent;border:none;padding:0px;}"
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        color = (
            self.palette().highlight().color()
            if self.isChecked() else self.palette().mid().color()
        )
        _draw_check_glyph(painter, self.rect(), color, self.isChecked())
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self._consume_release = True
            self.altClicked.emit()
            event.accept()
            return
        self._consume_release = False
        self._sweeping = True
        try:
            self.grabMouse()
        except RuntimeError as exc:
            log.debug("grabMouse() failed: %s", exc)
        self.sweepStarted.emit(not self.isChecked())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._sweeping:
            self.sweepMoved.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._consume_release and event.button() == Qt.MouseButton.LeftButton:
            self._consume_release = False
            event.accept()
            return
        if self._sweeping and event.button() == Qt.MouseButton.LeftButton:
            self._sweeping = False
            try:
                self.releaseMouse()
            except RuntimeError as exc:
                log.debug("releaseMouse() failed: %s", exc)
            self.sweepFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CheckClickArea(QWidget):
    """薄い判定領域全体でチェックを操作できるラッパー。"""

    def __init__(self, checkbox, parent=None):
        super().__init__(parent)
        self.checkbox = checkbox
        self._sweeping = False
        self.setFixedWidth(32)
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "CheckClickArea{background:transparent;border:none;}"
            "CheckClickArea:hover{background:palette(alternate-base);border:none;}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(checkbox)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                self.checkbox.altClicked.emit()
            elif self.checkbox.isEnabled() and hasattr(self.checkbox, "sweepStarted"):
                self._sweeping = True
                try:
                    self.grabMouse()
                except RuntimeError as exc:
                    log.debug("grabMouse() failed: %s", exc)
                self.checkbox.sweepStarted.emit(not self.checkbox.isChecked())
            elif self.checkbox.isEnabled():
                self.checkbox.setChecked(not self.checkbox.isChecked())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._sweeping and event.buttons() & Qt.MouseButton.LeftButton:
            self.checkbox.sweepMoved.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._sweeping and event.button() == Qt.MouseButton.LeftButton:
            self._sweeping = False
            try:
                self.releaseMouse()
            except RuntimeError as exc:
                log.debug("releaseMouse() failed: %s", exc)
            self.checkbox.sweepFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ColorSelectionArea(QWidget):
    """使用色行／使用色枠。クリック、Shift選択、他色へのドラッグ統合に対応。"""

    clicked = Signal(object)
    dragStarted = Signal()
    # (運んできた色rgb, モード) モード: "before" / "child" / "after"
    colorDropped = Signal(object, str)
    contextMenuRequested = Signal(QPoint)
    hoverChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected = False
        self._selection_frame_enabled = False
        # 親子グループを示す左端の縦ライン色（None＝グループなし）。
        self._group_line_color = None
        self._rgb = None
        self._is_background = False
        self._press_pos = None
        self._press_modifiers = Qt.KeyboardModifier.NoModifier
        self._drag_started = False
        self._drop_mode = None
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

    def setColorKey(self, rgb, is_background=False):
        self._rgb = rgb
        self._is_background = bool(is_background)

    def _mode_for_pos(self, y):
        """行内のY位置から、並べ替え(before/after)か子化(child)かを決める。"""
        height = max(1, self.height())
        ratio = min(1.0, max(0.0, y / height))
        if ratio < 0.28:
            return "before"
        if ratio > 0.72:
            return "after"
        return "child"

    def setSelected(self, selected):
        """選択状態を、色に依存しない単一アクセント枠で表す。"""
        selected = bool(selected)
        changed = self._selected != selected or not self._selection_frame_enabled
        self._selected = selected
        self._selection_frame_enabled = True
        if changed:
            self.update()

    def setGroupLine(self, color):
        """親子グループを示す左端の縦ライン色を設定する（None＝非表示）。"""
        new_color = QColor(color) if color is not None else None
        old = self._group_line_color
        same = (
            (old is None and new_color is None)
            or (old is not None and new_color is not None and old.rgb() == new_color.rgb())
        )
        self._group_line_color = new_color
        if not same:
            self.update()

    def enterEvent(self, event):
        self.hoverChanged.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hoverChanged.emit(False)
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 親子グループを、色を触らずに左端の縦ラインで束ねて示す。
        if self._group_line_color is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._group_line_color)
            painter.drawRoundedRect(QRectF(1.0, 1.0, 3.0, self.height() - 2.0), 1.5, 1.5)

        # 選択は親子で色分けせず、テーマのアクセント色による細い角丸枠に統一。
        if self._selection_frame_enabled and self._selected:
            accent = self.palette().highlight().color()
            pen = QPen(accent)
            pen.setWidthF(2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), 4.0, 4.0
            )

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if self._drop_mode == "child":
            # 子化のドロップ先は、半透明の塗り＋太い黄色枠で強調する。
            fill = QColor("#ffca28")
            fill.setAlpha(60)
            painter.fillRect(self.rect().adjusted(1, 1, -2, -2), fill)
            painter.setPen(QPen(QColor("#ffca28"), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        elif self._drop_mode in ("before", "after"):
            # 並べ替えのドロップ位置を、太い挿入バー＋左右の丸で明示する。
            bar_color = QColor("#ffca28")
            y = 2 if self._drop_mode == "before" else self.height() - 3
            painter.setPen(QPen(bar_color, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(6, y, self.width() - 7, y)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bar_color)
            painter.drawEllipse(QPointF(6, y), 3.5, 3.5)
            painter.drawEllipse(QPointF(self.width() - 7, y), 3.5, 3.5)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._press_modifiers = event.modifiers()
            self._drag_started = False
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.contextMenuRequested.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._press_pos is not None
            and not self._drag_started
            and event.buttons() & Qt.MouseButton.LeftButton
            and self._rgb is not None
            and not self._is_background
        ):
            distance = (
                event.position().toPoint() - self._press_pos
            ).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._start_reorder_drag()
                event.accept()
                return
        super().mouseMoveEvent(event)

    def _start_reorder_drag(self):
        if self._rgb is None:
            return
        self._drag_started = True
        self.dragStarted.emit()
        red, green, blue = self._rgb
        mime = QMimeData()
        mime.setData(
            USED_COLOR_MIME,
            QByteArray(bytes((red, green, blue))),
        )
        drag = QDrag(self)
        drag.setMimeData(mime)

        # ドラッグ中は運んでいる色を、少し浮き上がったカードとしてカーソルに付ける。
        scale = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        pw, ph = 40, 28
        pixmap = QPixmap(int(pw * scale), int(ph * scale))
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 影で「持ち上げている」感を出す。
        shadow = QColor(0, 0, 0, 70)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawRoundedRect(QRectF(3, 4, pw - 5, ph - 5), 4, 4)
        # 運んでいる色本体。
        painter.setBrush(QColor(red, green, blue))
        painter.setPen(QPen(QColor("#ffffff"), 1.5))
        painter.drawRoundedRect(QRectF(1, 1, pw - 6, ph - 7), 4, 4)
        painter.end()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(int((pw - 6) / 2), int((ph - 7) / 2)))

        # ドラッグ元の行を半透明にして、掴んでいることが分かるようにする。
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.4)
        self.setGraphicsEffect(effect)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.setGraphicsEffect(None)
        self._press_pos = None

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            was_drag = self._drag_started
            self._press_pos = None
            self._drag_started = False
            if not was_drag:
                # ドラッグにならなければ、通常の選択トグルとして扱う。
                self.clicked.emit(self._press_modifiers)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _payload_rgb(self, mime):
        if not mime.hasFormat(USED_COLOR_MIME):
            return None
        data = bytes(mime.data(USED_COLOR_MIME))
        if len(data) < 3:
            return None
        return (data[0], data[1], data[2])

    def _accepts(self, event):
        payload = self._payload_rgb(event.mimeData())
        # 背景行はドロップ先にしない。自分自身へのドロップも無視する。
        return payload is not None and payload != self._rgb and not self._is_background

    def dragEnterEvent(self, event):
        if not self._accepts(event):
            event.ignore()
            return
        event.acceptProposedAction()
        self._drop_mode = self._mode_for_pos(event.position().toPoint().y())
        self.update()

    def dragMoveEvent(self, event):
        if not self._accepts(event):
            event.ignore()
            return
        event.acceptProposedAction()
        mode = self._mode_for_pos(event.position().toPoint().y())
        if mode != self._drop_mode:
            self._drop_mode = mode
            self.update()

    def dragLeaveEvent(self, event):
        if self._drop_mode is not None:
            self._drop_mode = None
            self.update()

    def dropEvent(self, event):
        mode = self._drop_mode or self._mode_for_pos(
            event.position().toPoint().y()
        )
        self._drop_mode = None
        self.update()
        if not self._accepts(event):
            event.ignore()
            return
        event.acceptProposedAction()
        self.colorDropped.emit(self._payload_rgb(event.mimeData()), mode)

    def contextMenuEvent(self, event):
        self.contextMenuRequested.emit(event.globalPos())
        event.accept()


class UsedColorPanel(QWidget):
    mainColorRequested = Signal(QColor)
    isolateColorClicked = Signal(QColor)
    clearIsolateRequested = Signal()
    sourceScreenColorPicked = Signal(QColor)
    mergeColorsRequested = Signal(object, object)
    # 親子グループの非破壊プレビュー更新（{子rgb: 親rgb}）。
    previewGroupsChanged = Signal(object)
    # プレビュー中の親子を実ピクセルへ焼き込む要求（{子rgb: 親rgb}）。
    freezeGroupsRequested = Signal(object)
    deleteColorsRequested = Signal(object)
    adjustLineThicknessRequested = Signal(object)
    focusColorRequested = Signal(object)
    maskColorsChanged = Signal(object)
    selectedColorsChanged = Signal(object)
    visibleColorsChanged = Signal(object)
    # 並べ替え・親子・表示/マスクの変更をUndo履歴へ積む要求。
    # (変更前スナップショット, 履歴ラベル) を渡す。
    historyStatePush = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setMaximumWidth(16777215)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setMinimumHeight(0)
        self.background_rgb = (255, 255, 255)
        self.colors = []
        self.enabled_colors = {self.background_rgb: True}
        self.mask_rgbs = {self.background_rgb}
        self._mask_all_mode = True

        # 親子グループ（非破壊）。{子rgb: 親rgb}。親自身は含めない。
        # ドラッグで子付けし、キャンバス上では子を親色として描画する。
        self.child_to_parent = {}

        # Used-color selection is separate from the drawing mask.
        # The most recently selected color is the parent; the others are children.
        self.selected_rgbs = []
        self.parent_rgb = None
        self._selection_anchor_rgb = None

        self.visibility_checks = {}
        self.mask_checks = {}
        self.selection_checks = {}
        self.source_buttons = {}
        self.source_wrappers = {}
        self.row_widgets = {}

        # ホバー中の色（HEXはホバー時のみ表示して色面積を最大化する）。
        self._hovered_rgb = None

        self._selection_sweep_active = False
        self._selection_sweep_state = True
        self._selection_sweep_touched = set()
        self._selection_sweep_changed = False

        self._visibility_sweep_active = False
        self._visibility_sweep_state = True
        self._visibility_sweep_touched = set()
        self._visibility_sweep_changed = False
        self._mask_sweep_active = False
        self._mask_sweep_state = True
        self._mask_sweep_touched = set()
        self._mask_sweep_changed = False
        self._isolated_rgb = None
        self._pre_isolate_enabled = None
        self._mask_alt_rgb = None
        self._pre_mask_alt = None

        # Undo履歴用。復元中は再記録を止め、スウィープ中は開始時状態を保持する。
        self._history_suspended = False
        self._sweep_history_before = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        layout.addWidget(QLabel("<b>使用色</b>"))
        note = QLabel(
            "使用色：クリックで選択（Shift＝範囲／Ctrl＝追加）。"
            "ドラッグで並べ替え、色の中央へドロップ＝その色の「子」にして"
            "親色でプレビュー表示。親子付け／解除はドラッグと右クリックのみ。"
            "問題なければ［フリーズ］で実画像へ焼き込みます。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.count_label = QLabel("0色")
        layout.addWidget(self.count_label)

        header_widget = QWidget()
        header = QGridLayout(header_widget)
        scrollbar_width = self.style().pixelMetric(
            QStyle.PixelMetric.PM_ScrollBarExtent
        )
        header.setContentsMargins(1, 0, 1 + scrollbar_width, 0)
        header.setHorizontalSpacing(0)
        header.setColumnMinimumWidth(0, 32)
        header.setColumnMinimumWidth(1, 32)
        header.setColumnMinimumWidth(2, 32)
        selection_header = QLabel("選択")
        selection_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        selection_header.setStyleSheet("font-size:10px;")
        selection_header.setToolTip(
            "クリック：この色だけ選択／Shift＋クリック：範囲選択／"
            "Ctrl＋クリック：追加・解除。チェックと選択は連動します。"
        )
        header.addWidget(selection_header, 0, 0, Qt.AlignmentFlag.AlignCenter)
        visibility_header = QLabel("表示")
        visibility_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        visibility_header.setStyleSheet("font-size:10px;")
        visibility_header.setToolTip("各行の薄い背景セル全体を右クリックして表示メニューを開けます。")
        header.addWidget(visibility_header, 0, 1, Qt.AlignmentFlag.AlignCenter)
        mask_header = QLabel("描画対象")
        mask_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mask_header.setStyleSheet("font-size:10px;")
        mask_header.setToolTip(
            "ONにした色の上へ描けます。各行の薄い背景セル全体を"
            "右クリックして描画対象メニューを開けます。"
        )
        header.addWidget(mask_header, 0, 2, Qt.AlignmentFlag.AlignCenter)
        color_header = QLabel("色（ドラッグで並べ替え／親子付け）")
        color_header.setStyleSheet("font-size:10px;")
        header.addWidget(color_header, 0, 3)
        header.setColumnMinimumWidth(3, 144)
        header.setColumnStretch(3, 1)
        layout.addWidget(header_widget)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(0)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.content = QWidget()
        self.rows = QVBoxLayout(self.content)
        self.rows.setContentsMargins(2, 2, 2, 2)
        self.rows.setSpacing(1)
        self.rows.addStretch(1)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

        # 見出しと同じ4列に配置し、各ボタンの意味と位置を揃える。
        button_row = QGridLayout()
        button_row.setHorizontalSpacing(0)
        button_row.setContentsMargins(1, 0, 1 + scrollbar_width, 0)
        button_row.setColumnMinimumWidth(0, 32)
        button_row.setColumnMinimumWidth(1, 32)
        button_row.setColumnMinimumWidth(2, 32)
        button_row.setColumnStretch(3, 1)

        self.clear_selection_button = QPushButton("選択")
        self.clear_selection_button.setToolTip("選択をすべて解除します。")
        self.clear_selection_button.clicked.connect(self._clear_used_color_selection)
        self.clear_selection_button.setFixedWidth(32)
        self.clear_selection_button.setStyleSheet("font-size:9px;padding:0px;")

        self.show_all_button = QPushButton("全表示")
        self.show_all_button.setToolTip(
            "非表示にした使用色と背景色をすべて表示します。"
        )
        self.show_all_button.clicked.connect(self._show_all_colors)
        self.show_all_button.setFixedWidth(32)
        self.show_all_button.setStyleSheet("font-size:9px;padding:0px;")

        self.clear_masks_button = QPushButton("全体")
        self.clear_masks_button.setToolTip(
            "すべての使用色と背景をマスクON（描画可能）にします。"
        )
        self.clear_masks_button.clicked.connect(self._set_all_masks_on)
        self.clear_masks_button.setFixedWidth(32)
        self.clear_masks_button.setStyleSheet("font-size:9px;padding:0px;")

        self.merge_button = QPushButton("統合")
        self.merge_button.setToolTip(
            "選択した子の色を、最後に選択した親の色へ統合します。"
        )
        self.merge_button.clicked.connect(self._emit_merge)
        self.merge_button.setMinimumWidth(72)
        self.merge_button.setMaximumWidth(16777215)
        self.merge_button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.merge_button.setStyleSheet("font-size:10px;padding:1px;")

        self.freeze_button = QPushButton("フリーズ")
        self.freeze_button.setToolTip(
            "プレビュー中の親子（子→親の塗り替え）を、実際の画像へ焼き込みます。"
            "焼き込むと親子は解除され、Undoで元に戻せます。"
        )
        self.freeze_button.clicked.connect(self._emit_freeze)
        self.freeze_button.setEnabled(False)
        self.freeze_button.setMinimumWidth(72)
        self.freeze_button.setMaximumWidth(16777215)
        self.freeze_button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.freeze_button.setStyleSheet("font-size:10px;padding:1px;")

        for column, button in enumerate((
            self.clear_selection_button,
            self.show_all_button,
            self.clear_masks_button,
        )):
            button.setMinimumWidth(0)
            button_row.addWidget(button, 0, column)
        # 統合・フリーズは色スウォッチ列（col3）に横並びで置く。
        action_buttons = QHBoxLayout()
        action_buttons.setContentsMargins(0, 0, 0, 0)
        action_buttons.setSpacing(2)
        for button in (self.merge_button, self.freeze_button):
            button.setMinimumWidth(0)
            action_buttons.addWidget(button)
        button_row.addLayout(action_buttons, 0, 3)
        layout.addLayout(button_row)

    @staticmethod
    def _rgb_key(color):
        qc = QColor(color)
        return (qc.red(), qc.green(), qc.blue())

    @staticmethod
    def _text_color(rgb):
        luminance = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        return "#111111" if luminance >= 150 else "#ffffff"

    def _group_line_color(self, rgb):
        """rgb が親子グループに属していれば、束ねる縦ライン色（ルート親色）を返す。"""
        if rgb in self.child_to_parent:
            root = self._group_root(rgb)
            return QColor(*root)
        if any(parent == rgb for parent in self.child_to_parent.values()):
            return QColor(*rgb)
        return None

    def _sync_selection_check(self, rgb):
        checkbox = self.selection_checks.get(rgb)
        if checkbox is None:
            return
        selected = rgb in self.selected_rgbs
        if checkbox.isChecked() != selected:
            checkbox.blockSignals(True)
            checkbox.setChecked(selected)
            checkbox.blockSignals(False)
        else:
            checkbox.update()

    def _apply_swatch_text(self, rgb):
        """色面積を最大化するため、HEXはホバー時のみ表示する。"""
        button = self.source_buttons.get(rgb)
        if button is None:
            return
        is_background = rgb == self.background_rgb
        is_child = rgb in self.child_to_parent
        hovered = rgb == self._hovered_rgb
        hex_text = (
            "背景色 #FFFFFF" if is_background
            else "#{:02X}{:02X}{:02X}".format(*rgb)
        )
        if is_child:
            # 子は階層記号（└）を常に残し、HEXはホバー時のみ。
            button.setText(f"　└ {hex_text}" if hovered else "　└")
            return
        if hovered:
            button.setText(hex_text)
            return
        child_count = sum(
            1 for parent in self.child_to_parent.values() if parent == rgb
        )
        button.setText(f"（親・{child_count}）" if child_count else "")

    def _set_source_button_style(self, rgb):
        button = self.source_buttons.get(rgb)
        wrapper = self.source_wrappers.get(rgb)
        if button is None:
            return

        selected = rgb in self.selected_rgbs
        if wrapper is not None:
            wrapper.setSelected(selected)
            wrapper.setGroupLine(self._group_line_color(rgb))
        self._sync_selection_check(rgb)

        # 子色のボタン見た目（元色＋親色ボーダー）は _refresh_group_display が持つ。
        if rgb not in self.child_to_parent:
            color = QColor(*rgb)
            text_color = self._text_color(rgb)
            button.setStyleSheet(
                "QToolButton{"
                f"background:{color.name()};color:{text_color};"
                "border:1px solid rgba(255,255,255,0.55);padding:2px;"
                "}"
                "QToolButton:hover{"
                f"background:{color.name()};color:{text_color};"
                "border:1px solid rgba(255,255,255,0.55);"
                "}"
            )
            self._apply_swatch_text(rgb)

        if rgb == self.background_rgb:
            button.setToolTip(
                "背景色 #FFFFFF。並べ替えや親子付けの対象にはできません。"
            )
        else:
            group_note = ""
            parent_of_this = self.child_to_parent.get(rgb)
            if parent_of_this is not None:
                group_note = (
                    f"　現在 #{parent_of_this[0]:02X}{parent_of_this[1]:02X}"
                    f"{parent_of_this[2]:02X} の子（プレビュー中）です。"
                )
            button.setToolTip(
                "クリック：この色だけ選択／Shift＋クリック：範囲選択／"
                "Ctrl＋クリック：選択に追加・解除。"
                "ドラッグで並べ替え、色の中央へドロップ＝その色の子にして"
                "親色でプレビュー。親子付け／解除はドラッグと右クリックのみ。"
                + group_note
            )

    def _on_swatch_hover(self, rgb, hovered):
        if hovered:
            previous = self._hovered_rgb
            self._hovered_rgb = rgb
            if previous is not None and previous != rgb:
                self._apply_swatch_text(previous)
        elif self._hovered_rgb == rgb:
            self._hovered_rgb = None
        self._apply_swatch_text(rgb)

    def _clear_rows(self):
        self.visibility_checks.clear()
        self.mask_checks.clear()
        self.selection_checks.clear()
        self.source_buttons.clear()
        self.source_wrappers.clear()
        self.row_widgets.clear()
        while self.rows.count() > 1:
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _remove_color_row(self, rgb):
        """Remove only one row so palette updates do not rebuild every widget."""
        widget = self.row_widgets.pop(rgb, None)
        if widget is not None:
            self.rows.removeWidget(widget)
            widget.deleteLater()
        self.visibility_checks.pop(rgb, None)
        self.mask_checks.pop(rgb, None)
        self.selection_checks.pop(rgb, None)
        self.source_buttons.pop(rgb, None)
        self.source_wrappers.pop(rgb, None)

    def _make_source_wrapper(self, source, source_rgb):
        is_background = source_rgb == self.background_rgb
        wrapper = ColorSelectionArea()
        wrapper.setColorKey(source_rgb, is_background)
        if not is_background:
            wrapper.setCursor(Qt.CursorShape.OpenHandCursor)
        wrapper.clicked.connect(
            lambda modifiers, rgb=source_rgb:
            self._select_used_color(rgb, modifiers)
        )
        wrapper.colorDropped.connect(
            lambda dragged_rgb, mode, target_rgb=source_rgb:
            self._handle_color_drop(dragged_rgb, target_rgb, mode)
        )
        wrapper.contextMenuRequested.connect(
            lambda global_pos, rgb=source_rgb:
            self._show_used_color_context_menu(rgb, global_pos)
        )
        wrapper.hoverChanged.connect(
            lambda hovered, rgb=source_rgb: self._on_swatch_hover(rgb, hovered)
        )
        wrapper_layout = QHBoxLayout(wrapper)
        # 右余白は0にしてスウォッチを選択列いっぱいへ広げる。
        wrapper_layout.setContentsMargins(2, 3, 0, 3)
        wrapper_layout.setSpacing(0)
        wrapper_layout.addWidget(source)
        self.source_wrappers[source_rgb] = wrapper
        return wrapper

    def _append_color_row(self, color):
        qc = QColor(color)
        source_rgb = self._rgb_key(qc)
        is_background = source_rgb == self.background_rgb

        # 行全体では選択操作を受けない。
        # 「選択」列の source_wrapper だけが親子選択メニューを担当する。
        row_widget = QWidget()
        row = QGridLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setHorizontalSpacing(0)
        row.setColumnMinimumWidth(0, 32)
        row.setColumnMinimumWidth(1, 32)
        row.setColumnMinimumWidth(2, 32)
        row.setColumnMinimumWidth(3, 72)
        row.setColumnStretch(3, 1)

        selection_check = ColorSelectionCheckBox()
        selection_check.setChecked(source_rgb in self.selected_rgbs)
        selection_check.setEnabled(not is_background)
        selection_check.setToolTip(
            "背景色は選択できません。"
            if is_background else
            (
                "クリック：この色だけ選択／Shift＋クリック：範囲選択／"
                "Ctrl＋クリック：追加・解除／上下になぞる：一括選択／"
                "Alt＋クリック：この色だけ選択"
            )
        )
        if not is_background:
            selection_check.toggled.connect(
                lambda checked, rgb=source_rgb: self._on_selection_check_toggled(rgb, checked)
            )
            selection_check.altClicked.connect(
                lambda rgb=source_rgb: self._select_single_used_color(rgb)
            )
            selection_check.sweepStarted.connect(
                lambda checked, rgb=source_rgb: self._begin_selection_sweep(rgb, checked)
            )
            selection_check.sweepMoved.connect(self._move_selection_sweep)
            selection_check.sweepFinished.connect(self._end_selection_sweep)
        self.selection_checks[source_rgb] = selection_check

        visible_check = ColorVisibilityCheckBox()
        visible_check.setChecked(
            self.enabled_colors.get(source_rgb, True)
        )
        visible_check.setToolTip(
            (
                "クリック：背景表示ON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：背景だけ表示／右クリック：表示メニュー"
            )
            if is_background else
            (
                "クリック：表示ON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：この色だけ表示／右クリック：表示メニュー"
            )
        )
        visible_check.toggled.connect(
            lambda checked, rgb=source_rgb: self._set_color_visible(rgb, checked)
        )
        visible_check.altClicked.connect(
            lambda rgb=source_rgb: self._isolate_visible_color(rgb)
        )
        visible_check.sweepStarted.connect(
            lambda checked, rgb=source_rgb: self._begin_visibility_sweep(rgb, checked)
        )
        visible_check.sweepMoved.connect(self._move_visibility_sweep)
        visible_check.sweepFinished.connect(self._end_visibility_sweep)
        self.visibility_checks[source_rgb] = visible_check

        if self._mask_all_mode:
            self.mask_rgbs.add(source_rgb)
        mask_check = MaskColorCheckBox()
        mask_check.setChecked(source_rgb in self.mask_rgbs)
        mask_check.setToolTip(
            (
                "描画対象：ONにするとこの色の上へ描けます。"
                "クリック：ON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：この色だけON／右クリック：描画対象メニュー"
            )
            if not is_background else
            (
                "描画対象：ONにすると背景の上へ描けます。"
                "クリック：ON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：背景だけON／右クリック：描画対象メニュー"
            )
        )
        mask_check.toggled.connect(
            lambda checked, rgb=source_rgb: self._set_mask_color(rgb, checked)
        )
        mask_check.altClicked.connect(
            lambda rgb=source_rgb: self._isolate_mask_color(rgb)
        )
        mask_check.sweepStarted.connect(
            lambda checked, rgb=source_rgb: self._begin_mask_sweep(rgb, checked)
        )
        mask_check.sweepMoved.connect(self._move_mask_sweep)
        mask_check.sweepFinished.connect(self._end_mask_sweep)
        self.mask_checks[source_rgb] = mask_check

        source = SourceColorButton()
        source.setFixedHeight(26)
        source.setMinimumWidth(72)
        source.setText(
            "背景色 #FFFFFF"
            if is_background
            else qc.name(QColor.NameFormat.HexRgb).upper()
        )
        # 「選択」列のマウス操作はすべて外側の ColorSelectionArea が担当する。
        # これにより、ドラッグはスポイトではなく選択ON/OFFの連続切替になる。
        source.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.source_buttons[source_rgb] = source
        source_wrapper = self._make_source_wrapper(source, source_rgb)
        source_wrapper.setMinimumWidth(72)
        source_wrapper.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        source.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._set_source_button_style(source_rgb)

        # チェックボックスだけでなく、薄いグレーのセル全体をクリック領域にする。
        selection_area = CheckClickArea(selection_check)
        visible_area = CheckClickArea(visible_check)
        mask_area = CheckClickArea(mask_check)
        for widget in (selection_check, selection_area):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget, rgb=source_rgb:
                self._show_used_color_context_menu(rgb, w.mapToGlobal(pos))
            )
        for widget in (visible_check, visible_area):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget, rgb=source_rgb:
                self._show_visibility_context_menu(rgb, w.mapToGlobal(pos))
            )
        for widget in (mask_check, mask_area):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget, rgb=source_rgb:
                self._show_mask_context_menu(rgb, w.mapToGlobal(pos))
            )
        row.addWidget(selection_area, 0, 0)
        row.addWidget(visible_area, 0, 1)
        row.addWidget(mask_area, 0, 2)
        # 置換色列を廃止し、色スウォッチが色列全体を占める。
        row.addWidget(source_wrapper, 0, 3)

        self.row_widgets[source_rgb] = row_widget
        # 末尾（ストレッチの手前）へ追加する。並び順は _reapply_row_order で整える。
        self.rows.insertWidget(max(0, self.rows.count() - 1), row_widget)
        self._refresh_group_display(source_rgb)

    def _normalized_colors(self, colors):
        seen = set()
        result = []
        for color in colors:
            qc = QColor(color)
            key = self._rgb_key(qc)
            if key not in seen:
                seen.add(key)
                result.append(qc)
        return result

    def set_colors(self, colors):
        normalized = [
            color for color in self._normalized_colors(colors)
            if self._rgb_key(color) != self.background_rgb
        ]
        incoming_by_key = {
            self._rgb_key(color): QColor(color) for color in normalized
        }
        incoming_keys = {self.background_rgb, *incoming_by_key.keys()}
        current_keys = {self._rgb_key(color) for color in self.colors}
        added = incoming_keys - current_keys
        removed = current_keys - incoming_keys
        palette_changed = bool(added or removed)

        old_mask_rgbs = set(self.mask_rgbs)
        old_selected = list(self.selected_rgbs)
        old_group_mapping = self._group_mapping()

        # 消えた色だけを削除する。全行再構築と色順の並べ替えを避ける。
        for rgb in tuple(removed):
            if rgb == self.background_rgb:
                continue
            self._remove_color_row(rgb)
            self.enabled_colors.pop(rgb, None)
            self.mask_rgbs.discard(rgb)
            # 消えた色が絡む親子プレビューは破棄する。
            self._drop_group_links_for(rgb)

        self.selected_rgbs = [
            rgb for rgb in self.selected_rgbs
            if rgb in incoming_keys and rgb != self.background_rgb
        ]
        self.parent_rgb = self.selected_rgbs[-1] if self.selected_rgbs else None

        # 既存の並びを維持し、新色だけ末尾へ追加する。
        retained = [
            QColor(color) for color in self.colors
            if self._rgb_key(color) in incoming_keys
            and self._rgb_key(color) != self.background_rgb
        ]
        self.colors = [QColor(*self.background_rgb), *retained]

        if self.background_rgb not in self.source_buttons:
            self.enabled_colors[self.background_rgb] = True
            self._append_color_row(QColor(*self.background_rgb))

        existing = {self._rgb_key(color) for color in self.colors}
        for color in normalized:
            rgb = self._rgb_key(color)
            if rgb in existing:
                continue
            self.colors.append(QColor(color))
            self.enabled_colors[rgb] = True
            self.mask_rgbs.add(rgb)
            self._append_color_row(color)
            existing.add(rgb)

        self.enabled_colors = {
            rgb: self.enabled_colors.get(rgb, True)
            for rgb in incoming_keys
        }
        for rgb in set(old_selected) | set(self.selected_rgbs):
            if rgb in self.source_buttons:
                self._set_source_button_style(rgb)

        # 消えた色で親子が壊れた場合に備え、正規化と表示更新を行う。
        self._normalize_groups()
        self._reorder_children_under_parents()
        self._reapply_row_order()
        self._refresh_all_group_displays()
        new_group_mapping = self._group_mapping()
        if new_group_mapping != old_group_mapping:
            self._emit_preview()
        self.count_label.setText(f"{len(self.colors)}色")
        if self._isolated_rgb not in incoming_keys:
            self._isolated_rgb = None
            self._pre_isolate_enabled = None

        self._mask_all_mode = self.all_masks_enabled()
        if old_mask_rgbs != self.mask_rgbs:
            self.maskColorsChanged.emit(set(self.mask_rgbs))
        if old_selected != self.selected_rgbs:
            self.selectedColorsChanged.emit(set(self.selected_rgbs))
        if palette_changed:
            self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def add_color(self, color):
        """Add a newly drawn color immediately, before a whole-image scan."""
        qc = QColor(color)
        if not qc.isValid() or qc.alpha() == 0:
            return
        key = self._rgb_key(qc)
        if key in self.source_buttons:
            return
        self.colors.append(qc)
        self.enabled_colors[key] = True
        self.mask_rgbs.add(key)
        self._mask_all_mode = True
        self._append_color_row(qc)
        self.count_label.setText(f"{len(self.colors)}色")
        self._emit_mask_state()
        self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def enabled_rgb_set(self):
        enabled = {
            rgb for rgb, is_enabled in self.enabled_colors.items() if is_enabled
        }
        return enabled

    def selected_rgb_set(self):
        return set(self.selected_rgbs)

    def select_matching_color(self, color):
        """スポイト色と完全一致する使用色を、単独の親色として選択する。"""
        qc = QColor(color)
        rgb = self._rgb_key(qc)
        if (
            not qc.isValid()
            or rgb == self.background_rgb
            or rgb not in self.source_buttons
        ):
            self._clear_used_color_selection()
            return False

        old = set(self.selected_rgbs)
        changed = self.selected_rgbs != [rgb] or self.parent_rgb != rgb
        self.selected_rgbs = [rgb]
        self.parent_rgb = rgb
        self._selection_anchor_rgb = rgb
        self._refresh_used_color_styles(old | {rgb})
        row_widget = self.row_widgets.get(rgb)
        if row_widget is not None:
            self.scroll.ensureWidgetVisible(row_widget)
        if changed:
            self.selectedColorsChanged.emit({rgb})
        return True

    def _show_all_colors(self):
        with self._history_edit("全表示"):
            self._isolated_rgb = None
            self._pre_isolate_enabled = None
            for rgb in self.visibility_checks:
                self._set_checkbox_without_signal(rgb, True)
            self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def all_masks_enabled(self):
        keys = set(self.mask_checks)
        return bool(keys) and keys.issubset(self.mask_rgbs)

    def _emit_mask_state(self):
        self._mask_all_mode = self.all_masks_enabled()
        self.maskColorsChanged.emit(set(self.mask_rgbs))

    def _set_visibility_state(self, rgb, enabled):
        with self._history_edit("表示の切り替え"):
            self._isolated_rgb = None
            self._pre_isolate_enabled = None
            self._set_checkbox_without_signal(rgb, enabled)
            self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _disable_other_visible_colors(self, rgb):
        """右クリック対象だけを表示し、それ以外をOFFにする。"""
        with self._history_edit("対象以外を非表示"):
            self._isolated_rgb = None
            self._pre_isolate_enabled = None
            for key in self.visibility_checks:
                self._set_checkbox_without_signal(
                    key,
                    key == rgb,
                )
            self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _show_visibility_context_menu(self, rgb, global_position):
        menu = QMenu(self)
        action_on = menu.addAction("ON")
        action_off = menu.addAction("OFF")
        menu.addSeparator()
        action_others = menu.addAction("対象以外をOFF")
        action_all = menu.addAction("全表示")
        chosen = menu.exec(global_position)
        if chosen is action_on:
            self._set_visibility_state(rgb, True)
        elif chosen is action_off:
            self._set_visibility_state(rgb, False)
        elif chosen is action_others:
            self._disable_other_visible_colors(rgb)
        elif chosen is action_all:
            self._show_all_colors()

    def _set_mask_color(self, rgb, checked):
        if self._mask_sweep_active:
            if checked:
                self.mask_rgbs.add(rgb)
            else:
                self.mask_rgbs.discard(rgb)
            self._emit_mask_state()
            return
        with self._history_edit("マスクの切り替え"):
            if checked:
                self.mask_rgbs.add(rgb)
            else:
                self.mask_rgbs.discard(rgb)
            self._emit_mask_state()

    def _set_mask_checkbox_without_signal(self, rgb, checked):
        checkbox = self.mask_checks.get(rgb)
        if checkbox is not None and checkbox.isChecked() != bool(checked):
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(checked))
            checkbox.blockSignals(False)

    def _set_all_masks_on(self):
        with self._history_edit("マスクを全体ON"):
            self.mask_rgbs = set(self.mask_checks)
            for rgb in self.mask_checks:
                self._set_mask_checkbox_without_signal(rgb, True)
            self._emit_mask_state()

    def _clear_mask_colors(self):
        """Compatibility helper: turn every mask OFF."""
        with self._history_edit("マスクを全体OFF"):
            self.mask_rgbs.clear()
            for rgb in self.mask_checks:
                self._set_mask_checkbox_without_signal(rgb, False)
            self._emit_mask_state()

    def _isolate_mask_color(self, rgb):
        """Alt+click: enable only the clicked mask."""
        with self._history_edit("この色だけマスクON"):
            self.mask_rgbs = {rgb}
            for key in self.mask_checks:
                self._set_mask_checkbox_without_signal(key, key == rgb)
            self._emit_mask_state()

    def _disable_other_masks(self, rgb):
        """右クリック対象だけをONにし、それ以外のマスクをOFFにする。"""
        with self._history_edit("対象以外のマスクOFF"):
            self.mask_rgbs = {rgb}
            for key in self.mask_checks:
                self._set_mask_checkbox_without_signal(key, key == rgb)
            self._emit_mask_state()

    def _set_single_mask_state(self, rgb, enabled):
        with self._history_edit("マスクの切り替え"):
            self._set_mask_checkbox_without_signal(rgb, enabled)
            if enabled:
                self.mask_rgbs.add(rgb)
            else:
                self.mask_rgbs.discard(rgb)
            self._emit_mask_state()

    def _begin_mask_sweep(self, source_rgb, checked):
        self._sweep_history_before = self.capture_history_state()
        self._mask_sweep_active = True
        self._mask_sweep_state = bool(checked)
        self._mask_sweep_touched = {source_rgb}
        old = source_rgb in self.mask_rgbs
        self._set_mask_checkbox_without_signal(source_rgb, checked)
        if checked:
            self.mask_rgbs.add(source_rgb)
        else:
            self.mask_rgbs.discard(source_rgb)
        self._mask_sweep_changed = old != bool(checked)

    def _mask_checkbox_rgb_at_global(self, global_position):
        for rgb, checkbox in self.mask_checks.items():
            area = checkbox.parentWidget()
            target = area if isinstance(area, CheckClickArea) else checkbox
            if target.rect().contains(target.mapFromGlobal(global_position)):
                return rgb
        return None

    def _move_mask_sweep(self, global_position):
        if not self._mask_sweep_active:
            return
        rgb = self._mask_checkbox_rgb_at_global(global_position)
        if rgb is None or rgb in self._mask_sweep_touched:
            return
        self._mask_sweep_touched.add(rgb)
        old = rgb in self.mask_rgbs
        self._set_mask_checkbox_without_signal(rgb, self._mask_sweep_state)
        if self._mask_sweep_state:
            self.mask_rgbs.add(rgb)
        else:
            self.mask_rgbs.discard(rgb)
        self._mask_sweep_changed |= old != self._mask_sweep_state

    def _end_mask_sweep(self):
        if not self._mask_sweep_active:
            return
        self._mask_sweep_active = False
        if self._mask_sweep_changed:
            self._emit_mask_state()
            self._record_history("マスクの一括切り替え", self._sweep_history_before)
        self._sweep_history_before = None
        self._mask_sweep_touched.clear()
        self._mask_sweep_changed = False

    def _show_mask_context_menu(self, rgb, global_position):
        menu = QMenu(self)
        action_on = menu.addAction("ON")
        action_off = menu.addAction("OFF")
        menu.addSeparator()
        action_others = menu.addAction("対象以外をOFF")
        action_all = menu.addAction("全表示")
        chosen = menu.exec(global_position)
        if chosen is action_on:
            self._set_single_mask_state(rgb, True)
        elif chosen is action_off:
            self._set_single_mask_state(rgb, False)
        elif chosen is action_others:
            self._disable_other_masks(rgb)
        elif chosen is action_all:
            self._set_all_masks_on()


    def _refresh_used_color_styles(self, affected=None):
        keys = set(self.source_buttons) if affected is None else set(affected)
        for key in keys:
            if key in self.source_buttons:
                self._set_source_button_style(key)

    def _set_used_color_parent(self, rgb):
        if rgb == self.background_rgb:
            return
        old = set(self.selected_rgbs)
        self.selected_rgbs = [value for value in self.selected_rgbs if value != rgb]
        self.selected_rgbs.append(rgb)
        self.parent_rgb = rgb
        self._refresh_used_color_styles(old | set(self.selected_rgbs))
        self.selectedColorsChanged.emit(set(self.selected_rgbs))

    def _previous_selected_parent(self, rgb):
        """現在の色より前に選択された、直近の色を親候補として返す。"""
        for value in reversed(self.selected_rgbs):
            if value != rgb and value != self.background_rgb:
                return value
        return None

    def _set_used_color_child(self, rgb):
        if rgb == self.background_rgb:
            return False
        parent = self._previous_selected_parent(rgb)
        if parent is None:
            return False
        old = set(self.selected_rgbs)
        values = [
            value for value in self.selected_rgbs
            if value not in (rgb, parent)
        ]
        # 子を先、親を最後に置く。最後に選ばれたものが親という既存仕様を保つ。
        values.extend([rgb, parent])
        self.selected_rgbs = values
        self.parent_rgb = parent
        self._selection_anchor_rgb = rgb
        self._refresh_used_color_styles(old | set(values))
        self.selectedColorsChanged.emit(set(values))
        return True

    def _clear_used_color_selection(self):
        old = set(self.selected_rgbs)
        changed = bool(old) or self.parent_rgb is not None
        self.selected_rgbs = []
        self.parent_rgb = None
        self._selection_anchor_rgb = None
        self._refresh_used_color_styles(old)
        if changed:
            self.selectedColorsChanged.emit(set())

    def _show_used_color_context_menu(self, rgb, global_position):
        menu = QMenu(self)
        action_main_color = menu.addAction("メインカラーにする")
        menu.addSeparator()
        if rgb == self.background_rgb:
            chosen = menu.exec(global_position)
            if chosen is action_main_color:
                self.mainColorRequested.emit(QColor(*rgb))
            return
        action_parent = menu.addAction("親を選択")
        action_child = menu.addAction("子を選択")
        # 色一覧で一つ前ではなく、操作上で一つ前に選択された色を親にする。
        action_child.setEnabled(self._previous_selected_parent(rgb) is not None)
        action_merge_parent = menu.addAction("親と統合")
        parent = (
            tuple(self.parent_rgb)
            if self.parent_rgb is not None
            else None
        )
        if parent is None or parent == self.background_rgb:
            merge_sources = set()
        else:
            # 右クリックした色に関係なく、現在選択されている
            # 親色・子色のすべてを親色へ統合する。
            merge_sources = {
                tuple(value)
                for value in self.selected_rgbs
                if tuple(value) != self.background_rgb
            }
        action_merge_parent.setEnabled(
            parent is not None
            and parent in merge_sources
            and len(merge_sources) >= 2
        )
        action_merge_parent.setToolTip(
            "現在選択されているすべての色を、親色へ統合します。"
        )

        selected_line_colors = {
            tuple(value) for value in self.selected_rgbs
            if tuple(value) != self.background_rgb
        }

        # 右クリックした色が選択中なら、親・子を含む選択色全体を削除する。
        # 未選択の色を右クリックした場合は、その色だけを削除対象にする。
        clicked_rgb = tuple(rgb)
        delete_sources = (
            set(selected_line_colors)
            if clicked_rgb in selected_line_colors
            else {clicked_rgb}
        )
        delete_sources.discard(self.background_rgb)

        action_delete = menu.addAction("削除")
        action_delete.setEnabled(bool(delete_sources))
        action_delete.setToolTip(
            "選択した使用色を #FFFFFF へ統合します。"
        )

        action_thickness = menu.addAction("太さを調整")
        main_window = self.window()
        canvas = getattr(main_window, "canvas", None)
        tween_running = bool(
            getattr(canvas, "tween_pending", None)
        )
        action_thickness.setEnabled(
            not tween_running
            and parent is not None
            and tuple(rgb) == parent
            and parent in selected_line_colors
        )
        action_thickness.setToolTip(
            "選択中の親色・子色をまとめて調整します。"
            if not tween_running
            else "トゥイーン中は線の太さを変更できません。"
        )

        action_focus = menu.addAction("対象に注視")
        menu.addSeparator()

        # 親子グループ（プレビュー）関連。
        clicked_rgb = tuple(rgb)
        in_group = (
            clicked_rgb in self.child_to_parent
            or any(p == clicked_rgb for p in self.child_to_parent.values())
        )
        action_ungroup = menu.addAction("親子を解除")
        action_ungroup.setEnabled(in_group)
        action_ungroup.setToolTip("この色に関わる親子プレビューを解除します。")
        action_ungroup_all = menu.addAction("親子をすべて解除")
        action_ungroup_all.setEnabled(bool(self.child_to_parent))
        action_freeze = menu.addAction("親子をフリーズ（焼き込み）")
        action_freeze.setEnabled(bool(self.child_to_parent))
        action_freeze.setToolTip("プレビュー中の子→親の塗り替えを実画像へ確定します。")
        menu.addSeparator()
        action_clear = menu.addAction("全選択解除")

        chosen = menu.exec(global_position)
        if chosen is action_main_color:
            self.mainColorRequested.emit(QColor(*rgb))
        elif chosen is action_parent:
            self._set_used_color_parent(rgb)
        elif chosen is action_child:
            self._set_used_color_child(rgb)
        elif chosen is action_merge_parent:
            self.mergeColorsRequested.emit(
                parent,
                set(merge_sources),
            )
        elif chosen is action_delete:
            self.deleteColorsRequested.emit(
                set(delete_sources)
            )
        elif chosen is action_thickness:
            self.adjustLineThicknessRequested.emit(
                set(selected_line_colors)
            )
        elif chosen is action_focus:
            self._set_used_color_parent(rgb)
            self.focusColorRequested.emit(tuple(rgb))
        elif chosen is action_ungroup:
            with self._history_edit("親子を解除"):
                if self._drop_group_links_for(clicked_rgb):
                    self._normalize_groups()
                    self._refresh_all_group_displays()
                    self._emit_preview()
        elif chosen is action_ungroup_all:
            self._clear_groups()
        elif chosen is action_freeze:
            self._emit_freeze()
        elif chosen is action_clear:
            self._clear_used_color_selection()

    # ------------------------------------------------------------------
    # Undo履歴（並べ替え・親子・表示/マスク）
    # ------------------------------------------------------------------
    def capture_history_state(self):
        """Undo/Redoで復元するパネル状態のスナップショットを作る。"""
        return {
            "order": tuple(self._rgb_key(color) for color in self.colors),
            "groups": dict(self.child_to_parent),
            "enabled": dict(self.enabled_colors),
            "mask": set(self.mask_rgbs),
            "selected": list(self.selected_rgbs),
            "parent": self.parent_rgb,
        }

    @staticmethod
    def _history_significant(snapshot):
        """変更検知に使う、順序・親子・表示・マスクだけの部分を取り出す。"""
        return (
            tuple(snapshot.get("order", ())),
            tuple(sorted(snapshot.get("groups", {}).items())),
            tuple(sorted(snapshot.get("enabled", {}).items())),
            tuple(sorted(snapshot.get("mask", set()))),
        )

    def restore_history_state(self, snapshot):
        """スナップショットへパネルを戻し、キャンバス側の再描画信号も出す。"""
        if not snapshot:
            return
        self._history_suspended = True
        try:
            existing = {self._rgb_key(color) for color in self.colors}
            by_key = {self._rgb_key(color): color for color in self.colors}

            # 並び順を復元する（背景は必ず先頭。未知色は末尾へ回す）。
            ordered = []
            if self.background_rgb in existing:
                ordered.append(self.background_rgb)
            for rgb in snapshot.get("order", ()):
                if rgb in existing and rgb not in ordered:
                    ordered.append(rgb)
            for rgb in (self._rgb_key(color) for color in self.colors):
                if rgb not in ordered:
                    ordered.append(rgb)
            self.colors = [by_key[rgb] for rgb in ordered if rgb in by_key]
            self._reapply_row_order()

            # 親子プレビューを復元する。
            self.child_to_parent = {
                child: parent
                for child, parent in snapshot.get("groups", {}).items()
                if child in existing and parent in existing
            }
            self._normalize_groups()
            self._reorder_children_under_parents()
            self._refresh_all_group_displays()

            # 表示状態を復元する。
            enabled = snapshot.get("enabled", {})
            self._isolated_rgb = None
            self._pre_isolate_enabled = None
            for rgb in self.visibility_checks:
                self._set_checkbox_without_signal(rgb, enabled.get(rgb, True))

            # マスク状態を復元する。
            mask = snapshot.get("mask", set())
            self.mask_rgbs = {
                rgb for rgb in mask
                if rgb in existing or rgb == self.background_rgb
            }
            for rgb in self.mask_checks:
                self._set_mask_checkbox_without_signal(rgb, rgb in self.mask_rgbs)
            self._mask_all_mode = self.all_masks_enabled()

            # 選択状態も戻す（履歴の見た目を揃えるため）。
            self.selected_rgbs = [
                rgb for rgb in snapshot.get("selected", []) if rgb in existing
            ]
            parent = snapshot.get("parent")
            self.parent_rgb = (
                parent if parent in existing
                else (self.selected_rgbs[-1] if self.selected_rgbs else None)
            )
            self._refresh_used_color_styles()
        finally:
            self._history_suspended = False

        # キャンバス側へ最新状態を伝える。
        self._emit_preview()
        self.maskColorsChanged.emit(set(self.mask_rgbs))
        self.selectedColorsChanged.emit(set(self.selected_rgbs))
        self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _record_history(self, label, before):
        """変更前後を比べ、意味のある差があればUndo履歴へ積む。"""
        if self._history_suspended or before is None:
            return
        after = self.capture_history_state()
        if self._history_significant(before) == self._history_significant(after):
            return
        self.historyStatePush.emit(before, label)

    @contextmanager
    def _history_edit(self, label):
        """with で囲んだ範囲の変更を1件のUndoエントリにまとめる。"""
        if self._history_suspended:
            yield
            return
        before = self.capture_history_state()
        yield
        self._record_history(label, before)

    def _ordered_non_background_rgbs(self):
        return [
            self._rgb_key(color) for color in self.colors
            if self._rgb_key(color) != self.background_rgb
        ]

    def _select_used_color(self, rgb, modifiers=Qt.KeyboardModifier.NoModifier):
        """クリック＝単独選択／Shift＝範囲選択／Ctrl＝個別トグル。"""
        if rgb == self.background_rgb:
            return
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)

        if shift and self._selection_anchor_rgb:
            ordered = self._ordered_non_background_rgbs()
            if rgb in ordered and self._selection_anchor_rgb in ordered:
                start = ordered.index(self._selection_anchor_rgb)
                end = ordered.index(rgb)
                lo, hi = sorted((start, end))
                old = set(self.selected_rgbs)
                # 直前選択からクリック色までを範囲としてまとめ選択する。
                self.selected_rgbs = list(ordered[lo:hi + 1])
                self.parent_rgb = rgb
                self._refresh_used_color_styles(old | set(self.selected_rgbs))
                self.selectedColorsChanged.emit(set(self.selected_rgbs))
                return

        if ctrl:
            # 個別に追加／解除。
            self._selection_anchor_rgb = rgb
            self._toggle_used_color_selection(rgb)
            return

        # 修飾なしクリックは、その色だけを単独選択する。
        self._selection_anchor_rgb = rgb
        self._select_single_used_color(rgb)

    def _select_single_used_color(self, rgb):
        if rgb == self.background_rgb:
            return
        old = set(self.selected_rgbs)
        self.selected_rgbs = [rgb]
        self.parent_rgb = rgb
        self._selection_anchor_rgb = rgb
        self._refresh_used_color_styles(old | {rgb})
        self.selectedColorsChanged.emit({rgb})

    # ------------------------------------------------------------------
    # 「選択」チェックボックス（クリック連動・上下スイープ一括選択）
    # ------------------------------------------------------------------
    def _apply_selection_state(self, rgb, selected):
        """1色分の選択状態を更新する（emitは呼び出し側で行う）。"""
        if rgb == self.background_rgb:
            return
        was = rgb in self.selected_rgbs
        if bool(selected) == was:
            return
        if selected:
            self.selected_rgbs.append(rgb)
        else:
            self.selected_rgbs.remove(rgb)
        self.parent_rgb = self.selected_rgbs[-1] if self.selected_rgbs else None
        self._selection_anchor_rgb = rgb
        self._set_source_button_style(rgb)
        self._selection_sweep_changed = True

    def _on_selection_check_toggled(self, rgb, checked):
        if self._selection_sweep_active:
            return
        self._selection_sweep_changed = False
        self._apply_selection_state(rgb, checked)
        if self._selection_sweep_changed:
            self.selectedColorsChanged.emit(set(self.selected_rgbs))
        self._selection_sweep_changed = False

    def _selection_check_rgb_at_global(self, global_position):
        for rgb, checkbox in self.selection_checks.items():
            area = checkbox.parentWidget()
            target = area if isinstance(area, CheckClickArea) else checkbox
            if target.rect().contains(target.mapFromGlobal(global_position)):
                return rgb
        return None

    def _begin_selection_sweep(self, source_rgb, checked):
        self._selection_sweep_active = True
        self._selection_sweep_state = bool(checked)
        self._selection_sweep_touched = {source_rgb}
        self._selection_sweep_changed = False
        self._apply_selection_state(source_rgb, checked)

    def _move_selection_sweep(self, global_position):
        if not self._selection_sweep_active:
            return
        rgb = self._selection_check_rgb_at_global(global_position)
        if (
            rgb is None
            or rgb in self._selection_sweep_touched
            or rgb == self.background_rgb
        ):
            return
        self._selection_sweep_touched.add(rgb)
        self._apply_selection_state(rgb, self._selection_sweep_state)

    def _end_selection_sweep(self):
        if not self._selection_sweep_active:
            return
        self._selection_sweep_active = False
        if self._selection_sweep_changed:
            self.selectedColorsChanged.emit(set(self.selected_rgbs))
        self._selection_sweep_touched.clear()
        self._selection_sweep_changed = False

    # ------------------------------------------------------------------
    # ドラッグ＆ドロップによる並べ替え・親子付け（非破壊プレビュー）
    # ------------------------------------------------------------------
    def _color_index(self, rgb):
        for index, color in enumerate(self.colors):
            if self._rgb_key(color) == rgb:
                return index
        return None

    def _group_root(self, rgb):
        """親子チェーンをたどって最終的な親（ルート色）を返す。"""
        seen = set()
        current = rgb
        while current in self.child_to_parent and current not in seen:
            seen.add(current)
            current = self.child_to_parent[current]
        return current

    def _drop_group_links_for(self, rgb):
        """指定色が親でも子でも、その親子リンクをすべて解除する。"""
        changed = self.child_to_parent.pop(rgb, None) is not None
        for child in [c for c, p in self.child_to_parent.items() if p == rgb]:
            self.child_to_parent.pop(child, None)
            changed = True
        return changed

    def _handle_color_drop(self, dragged_rgb, target_rgb, mode):
        dragged_rgb = tuple(dragged_rgb)
        target_rgb = tuple(target_rgb)
        if dragged_rgb == target_rgb or dragged_rgb == self.background_rgb:
            return
        if target_rgb == self.background_rgb:
            return
        label = "親子付け" if mode == "child" else "使用色の並べ替え"
        with self._history_edit(label):
            if mode == "child":
                self._make_child_of(dragged_rgb, target_rgb)
            else:
                self._reorder_color(dragged_rgb, target_rgb, mode)

    def _make_child_of(self, child_rgb, parent_rgb):
        """child_rgb を parent_rgb の子にして、親色プレビューを更新する。"""
        # 循環を避ける。ドロップ先が自分の子孫なら親子化しない。
        probe = parent_rgb
        guard = 0
        while probe in self.child_to_parent and guard < len(self.child_to_parent) + 1:
            if probe == child_rgb:
                return
            probe = self.child_to_parent[probe]
            guard += 1
        # 親自身が誰かの子なら、実際のルートへ束ねる（単層に正規化）。
        root = self._group_root(parent_rgb)
        if root == child_rgb:
            return
        # child_rgb にぶら下がっていた子は、まとめて新しいルートへ移す。
        for grandchild in [c for c, p in self.child_to_parent.items() if p == child_rgb]:
            self.child_to_parent[grandchild] = root
        self.child_to_parent[child_rgb] = root
        self._normalize_groups()
        self._reorder_children_under_parents()
        self._refresh_all_group_displays()
        self._emit_preview()

    def _normalize_groups(self):
        """全リンクをルート直付けに正規化し、背景・自己参照を除去する。"""
        cleaned = {}
        for child, parent in self.child_to_parent.items():
            if child == self.background_rgb or child == parent:
                continue
            root = self._group_root(parent)
            if root == child or root == self.background_rgb:
                continue
            cleaned[child] = root
        self.child_to_parent = cleaned

    def _reorder_color(self, dragged_rgb, target_rgb, mode):
        """dragged_rgb を target_rgb の前／後ろへ移動する（背景は先頭固定）。"""
        drag_index = self._color_index(dragged_rgb)
        if drag_index is None:
            return
        moved = self.colors.pop(drag_index)
        target_index = self._color_index(target_rgb)
        if target_index is None:
            self.colors.insert(drag_index, moved)
            return
        if mode == "after":
            target_index += 1
        # 背景色（先頭）より前には入れない。
        target_index = max(1, target_index)
        self.colors.insert(target_index, moved)
        self._reapply_row_order()

    def _reorder_children_under_parents(self):
        """子色を、その親色（ルート）の直後へまとめて並べ替える。"""
        if not self.child_to_parent:
            return
        key_to_color = {self._rgb_key(color): color for color in self.colors}
        order = [self._rgb_key(color) for color in self.colors]
        child_set = set(self.child_to_parent)

        # 各ルート親ごとに、現在の並び順を保ったまま子をぶら下げる。
        children_by_root = {}
        for child in order:
            if child in child_set:
                children_by_root.setdefault(
                    self._group_root(child), []
                ).append(child)

        result = []
        for rgb in order:
            if rgb in child_set:
                continue  # 親の直後にまとめて置くのでここでは飛ばす。
            result.append(rgb)
            result.extend(children_by_root.get(rgb, []))
        # 親が見つからない子は末尾へ回して取りこぼしを防ぐ。
        for child in order:
            if child in child_set and child not in result:
                result.append(child)

        self.colors = [
            key_to_color[rgb] for rgb in result if rgb in key_to_color
        ]
        self._reapply_row_order()

    def _reapply_row_order(self):
        """self.colors の順序どおりに行ウィジェットを並べ替える。"""
        for position, color in enumerate(self.colors):
            rgb = self._rgb_key(color)
            widget = self.row_widgets.get(rgb)
            if widget is None:
                continue
            self.rows.removeWidget(widget)
            self.rows.insertWidget(position, widget)

    def _refresh_all_group_displays(self):
        for rgb in list(self.source_buttons):
            self._refresh_group_display(rgb)
        has_groups = bool(self.child_to_parent)
        self.freeze_button.setEnabled(has_groups)

    def _refresh_group_display(self, rgb):
        """子色は元色のまま表示し、ボーダーを親色にして階層を示す。"""
        button = self.source_buttons.get(rgb)
        wrapper = self.source_wrappers.get(rgb)
        if button is None:
            return
        parent = self.child_to_parent.get(rgb)
        if parent is not None:
            # 元色は保持し、縁取り（ボーダー）だけを親色にする。
            root = self._group_root(rgb)
            parent_color = QColor(*root)
            own = QColor(*rgb)
            text_color = self._text_color(rgb)
            button.setStyleSheet(
                "QToolButton{"
                f"background:{own.name()};color:{text_color};"
                f"border:2px solid {parent_color.name()};padding:2px;}}"
                "QToolButton:hover{"
                f"background:{own.name()};color:{text_color};"
                f"border:2px solid {parent_color.name()};}}"
            )
            if wrapper is not None:
                margin = wrapper.layout()
                if margin is not None:
                    # 行頭インデントで階層を示す（右余白は0でスウォッチを広げる）。
                    margin.setContentsMargins(20, 3, 0, 3)
        else:
            if wrapper is not None:
                margin = wrapper.layout()
                if margin is not None:
                    margin.setContentsMargins(2, 3, 0, 3)
        # 選択枠・グループ縦ライン・チェック同期・HEX表示・ツールチップを更新。
        self._set_source_button_style(rgb)
        self._apply_swatch_text(rgb)

    def _group_mapping(self):
        """{子rgb: ルート親rgb} を返す（プレビュー／フリーズ共通）。"""
        return {
            child: self._group_root(child)
            for child in self.child_to_parent
        }

    def _emit_preview(self):
        self.previewGroupsChanged.emit(self._group_mapping())

    def _clear_groups(self):
        if not self.child_to_parent:
            return
        with self._history_edit("親子をすべて解除"):
            self.child_to_parent = {}
            self._refresh_all_group_displays()
            self._emit_preview()

    def on_groups_frozen(self):
        """フリーズ確定後：プレビューを解除する（実ピクセルは親色に確定済み）。"""
        self.child_to_parent = {}
        self._refresh_all_group_displays()
        self.previewGroupsChanged.emit({})

    def _emit_freeze(self):
        mapping = self._group_mapping()
        if not mapping:
            window = self.window()
            if hasattr(window, "statusBar"):
                window.statusBar().showMessage(
                    "フリーズする親子（プレビュー）がありません。"
                    "色を別の色の中へドロップして親子を作成してください。",
                    2800,
                )
            return
        self.freezeGroupsRequested.emit(mapping)

    def _toggle_used_color_selection(self, rgb):
        if rgb == self.background_rgb:
            return
        affected = set(self.selected_rgbs)
        if rgb in self.selected_rgbs:
            self.selected_rgbs.remove(rgb)
        else:
            self.selected_rgbs.append(rgb)
        self.parent_rgb = self.selected_rgbs[-1] if self.selected_rgbs else None
        affected.update(self.selected_rgbs)
        for key in affected:
            self._set_source_button_style(key)
        self.selectedColorsChanged.emit(set(self.selected_rgbs))

    def _retain_parent_selection(self):
        old = set(self.selected_rgbs)
        self.selected_rgbs = [self.parent_rgb] if self.parent_rgb is not None else []
        for rgb in old | set(self.selected_rgbs):
            self._set_source_button_style(rgb)
        self.selectedColorsChanged.emit(set(self.selected_rgbs))

    def _emit_merge(self):
        if self.parent_rgb is None or len(self.selected_rgbs) < 2:
            window = self.window()
            if hasattr(window, "statusBar"):
                window.statusBar().showMessage(
                    "統合する使用色を2色以上選択してください。最後に選んだ色が親です。",
                    2800,
                )
            return
        selected = set(self.selected_rgbs)
        self.mergeColorsRequested.emit(tuple(self.parent_rgb), selected)

    def _set_checkbox_without_signal(self, rgb, enabled):
        checkbox = self.visibility_checks.get(rgb)
        self.enabled_colors[rgb] = bool(enabled)
        if checkbox is not None and checkbox.isChecked() != bool(enabled):
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(enabled))
            checkbox.blockSignals(False)

    def _isolate_visible_color(self, source_rgb):
        with self._history_edit("この色だけ表示"):
            if (
                self._isolated_rgb == source_rgb
                and self._pre_isolate_enabled is not None
            ):
                restore = dict(self._pre_isolate_enabled)
                self._isolated_rgb = None
                self._pre_isolate_enabled = None
                for rgb in self.visibility_checks:
                    self._set_checkbox_without_signal(
                        rgb,
                        restore.get(rgb, True),
                    )
            else:
                self._pre_isolate_enabled = dict(self.enabled_colors)
                self._isolated_rgb = source_rgb
                for rgb in self.visibility_checks:
                    self._set_checkbox_without_signal(
                        rgb,
                        rgb == source_rgb,
                    )
            self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _begin_visibility_sweep(self, source_rgb, checked):
        self._sweep_history_before = self.capture_history_state()
        self._isolated_rgb = None
        self._pre_isolate_enabled = None
        self._visibility_sweep_active = True
        self._visibility_sweep_state = bool(checked)
        self._visibility_sweep_touched = {source_rgb}
        old = self.enabled_colors.get(source_rgb, True)
        self._set_checkbox_without_signal(source_rgb, checked)
        self._visibility_sweep_changed = old != bool(checked)

    def _checkbox_rgb_at_global(self, global_position):
        for rgb, checkbox in self.visibility_checks.items():
            if checkbox.rect().contains(
                checkbox.mapFromGlobal(global_position)
            ):
                return rgb
        return None

    def _move_visibility_sweep(self, global_position):
        if not self._visibility_sweep_active:
            return
        rgb = self._checkbox_rgb_at_global(global_position)
        if (
            rgb is None
            or rgb in self._visibility_sweep_touched
        ):
            return
        self._visibility_sweep_touched.add(rgb)
        old = self.enabled_colors.get(rgb, True)
        self._set_checkbox_without_signal(
            rgb, self._visibility_sweep_state
        )
        self._visibility_sweep_changed |= (
            old != self._visibility_sweep_state
        )

    def _end_visibility_sweep(self):
        if not self._visibility_sweep_active:
            return
        self._visibility_sweep_active = False
        if self._visibility_sweep_changed:
            self.visibleColorsChanged.emit(self.enabled_rgb_set())
            self._record_history("表示の一括切り替え", self._sweep_history_before)
        self._sweep_history_before = None
        self._visibility_sweep_touched.clear()
        self._visibility_sweep_changed = False

    def _set_color_visible(self, source_rgb, checked):
        if self._visibility_sweep_active:
            return
        with self._history_edit("表示の切り替え"):
            self._isolated_rgb = None
            self._pre_isolate_enabled = None
            self.enabled_colors[source_rgb] = bool(checked)
            self.visibleColorsChanged.emit(self.enabled_rgb_set())
