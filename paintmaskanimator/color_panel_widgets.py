"""The row widgets that make up the used-color panel.

Split out of ``color_panel.py``: the source-colour swatch button, the three
checkbox variants (visibility / mask / selection) with their custom painting and
sweep-drag behaviour, and the swatch row itself (``ColorSelectionArea``) with its
drag-and-drop handling. ``UsedColorPanel`` composes these; they know nothing
about the panel beyond the signals they emit.
"""
from PySide6.QtCore import QByteArray, QPoint, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from .utils import ScreenColorPickerOverlay, _ScreenColorDragMixin
from .logging_setup import get_logger

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QGraphicsOpacityEffect

log = get_logger(__name__)

# 使用色をドラッグ＆ドロップで統合するときに使う専用MIME形式。
USED_COLOR_MIME = "application/x-pma-used-color"


class ColorCategoryHeader(QWidget):
    """使用色カテゴリーの折りたたみ・表示・ドロップ用ヘッダー。"""

    visibilityChanged = Signal(bool)
    collapsedChanged = Signal(bool)
    colorDropped = Signal(object)
    contextMenuRequested = Signal(QPoint)

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self._name = str(name)
        self._drop_active = False
        self.setAcceptDrops(True)
        self.setObjectName("usedColorCategoryHeader")
        self.setMinimumHeight(32)
        self.setStyleSheet(
            "QWidget#usedColorCategoryHeader{"
            "background-color:palette(midlight);"
            "border:2px solid palette(mid);border-radius:4px;}"
            "QWidget#usedColorCategoryHeader:hover{"
            "border-color:palette(highlight);}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 7, 2)
        layout.setSpacing(4)
        self.collapse_button = QToolButton(self)
        self.collapse_button.setCheckable(True)
        self.collapse_button.setChecked(False)
        self.collapse_button.setText("▼")
        self.collapse_button.setToolTip("フォルダーを折りたたむ")
        self.visibility_check = QCheckBox(self)
        self.visibility_check.setTristate(True)
        self.visibility_check.setToolTip("フォルダー内の色を一括表示／非表示")
        self.name_label = QLabel(self._name, self)
        self.name_label.setStyleSheet("font-weight:700;")
        layout.addWidget(self.collapse_button)
        layout.addWidget(self.visibility_check)
        layout.addWidget(self.name_label, 1)
        self.collapse_button.toggled.connect(self._on_collapsed)
        self.visibility_check.clicked.connect(self.visibilityChanged.emit)

    def name(self):
        return self._name

    def setName(self, name):
        self._name = str(name)
        self.name_label.setText(self._name)

    def setCollapsed(self, collapsed):
        self.collapse_button.blockSignals(True)
        self.collapse_button.setChecked(bool(collapsed))
        self.collapse_button.blockSignals(False)
        self._sync_collapse_text(bool(collapsed))

    def _sync_collapse_text(self, collapsed):
        self.collapse_button.setText("▶" if collapsed else "▼")
        self.collapse_button.setToolTip(
            "フォルダーを展開" if collapsed else "フォルダーを折りたたむ"
        )

    def _on_collapsed(self, collapsed):
        self._sync_collapse_text(collapsed)
        self.collapsedChanged.emit(bool(collapsed))

    def setVisibilityState(self, values):
        states = [bool(value) for value in values]
        if states and all(states):
            state = Qt.CheckState.Checked
        elif states and any(states):
            state = Qt.CheckState.PartiallyChecked
        else:
            state = Qt.CheckState.Unchecked
        self.visibility_check.blockSignals(True)
        self.visibility_check.setCheckState(state)
        self.visibility_check.blockSignals(False)

    @staticmethod
    def _payload_rgb(mime):
        if not mime.hasFormat(USED_COLOR_MIME):
            return None
        data = bytes(mime.data(USED_COLOR_MIME))
        if len(data) < 3:
            return None
        return (data[0], data[1], data[2])

    def dragEnterEvent(self, event):
        if self._payload_rgb(event.mimeData()) is None:
            event.ignore()
            return
        self._drop_active = True
        self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drop_active = False
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        rgb = self._payload_rgb(event.mimeData())
        self._drop_active = False
        self.update()
        if rgb is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.colorDropped.emit(rgb)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_active:
            painter = QPainter(self)
            painter.setPen(QPen(QColor("#ffca28"), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 3, 3)

    def contextMenuEvent(self, event):
        self.contextMenuRequested.emit(event.globalPos())
        event.accept()


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


class ScreenEyedropButton(_ScreenColorDragMixin, QToolButton):
    """置換色の登録・画面スポイト・編集要求を提供するボタン。"""

    colorPicked = Signal(QColor)
    colorEditorRequested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_screen_color_drag()
        self._screen_picker_overlay = None
        self._right_picker_candidate = False
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.clicked.connect(self._request_color_editor)

    def _request_color_editor(self, _checked=False):
        self.colorEditorRequested.emit(QCursor.pos())

    def _screen_picker_closed(self, overlay):
        if self._screen_picker_overlay is overlay:
            self._screen_picker_overlay = None
            self._screen_pick_active = False

    def _global_color_picked(self, overlay, color):
        self._screen_picker_closed(overlay)
        self.colorPicked.emit(QColor(color))

    def _begin_global_screen_pick(self):
        overlay = self._screen_picker_overlay
        if overlay is not None and overlay.isVisible():
            return
        overlay = ScreenColorPickerOverlay()
        self._screen_picker_overlay = overlay
        self._screen_pick_active = True
        overlay.colorPicked.connect(
            lambda color, current=overlay:
            self._global_color_picked(current, color)
        )
        overlay.canceled.connect(
            lambda current=overlay: self._screen_picker_closed(current)
        )
        overlay.destroyed.connect(
            lambda _obj=None, current=overlay:
            self._screen_picker_closed(current)
        )

        # This method runs from the activating right-button release. Showing
        # the desktop window synchronously can make Windows deliver that same
        # release to the new window and immediately cancel it.
        def start_after_activation_release():
            if self._screen_picker_overlay is overlay:
                overlay.start()

        QTimer.singleShot(0, start_after_activation_release)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._right_picker_candidate = True
            event.accept()
            return
        # A plain left click belongs to the RGB / HSV editor. Bypass the mixin
        # so moving while left is held cannot start the eyedropper.
        QToolButton.mousePressEvent(self, event)

    def mouseMoveEvent(self, event):
        QToolButton.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.MouseButton.RightButton
            and self._right_picker_candidate
        ):
            self._right_picker_candidate = False
            self._begin_global_screen_pick()
            event.accept()
            return
        QToolButton.mouseReleaseEvent(self, event)

    def hideEvent(self, event):
        overlay = self._screen_picker_overlay
        if overlay is not None:
            overlay.cancel()
        QToolButton.hideEvent(self, event)


class ReplacementColorPopup(QDialog):
    """置換色をRGB／HSVスライダーで編集するポップアップ。"""

    colorChanged = Signal(QColor)

    def __init__(self, color, source_rgb=None, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint,
        )
        self._color = QColor(color)
        if not self._color.isValid():
            self._color = QColor("black")
        self._updating = False
        self.setWindowTitle("置換色")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumWidth(280)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 9, 9, 9)
        layout.setSpacing(5)
        title = "置換色"
        if source_rgb is not None:
            title += "  元色 #{:02X}{:02X}{:02X}".format(*source_rgb)
        layout.addWidget(QLabel(f"<b>{title}</b>"))
        self.preview = QLabel()
        self.preview.setFixedHeight(28)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview)
        self.mode = QComboBox()
        self.mode.addItems(["RGB", "HSV"])
        self.mode.currentTextChanged.connect(self._rebuild_sliders)
        layout.addWidget(self.mode)
        self.slider_widget = QWidget()
        self.slider_layout = QFormLayout(self.slider_widget)
        self.slider_layout.setContentsMargins(0, 0, 0, 0)
        self.slider_layout.setSpacing(4)
        layout.addWidget(self.slider_widget)
        self.sliders = []
        self.value_labels = []
        self._rebuild_sliders("RGB")

    def _clear_slider_layout(self):
        while self.slider_layout.rowCount():
            self.slider_layout.removeRow(0)
        self.sliders.clear()
        self.value_labels.clear()

    def _rebuild_sliders(self, mode):
        self._clear_slider_layout()
        specs = (
            [("R", 0, 255), ("G", 0, 255), ("B", 0, 255)]
            if mode == "RGB"
            else [("H", 0, 359), ("S", 0, 255), ("V", 0, 255)]
        )
        for name, minimum, maximum in specs:
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(minimum, maximum)
            label = QLabel("0")
            label.setFixedWidth(34)
            label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            row_layout.addWidget(slider, 1)
            row_layout.addWidget(label)
            slider.valueChanged.connect(
                lambda value, target=label: target.setText(str(value))
            )
            slider.valueChanged.connect(self._sliders_changed)
            self.slider_layout.addRow(name, row)
            self.sliders.append(slider)
            self.value_labels.append(label)
        self._sync_from_color()

    def _sync_from_color(self):
        if self.mode.currentText() == "RGB":
            values = self._color.getRgb()[:3]  # pyright: ignore[reportIndexIssue]
        else:
            hue, saturation, value, _alpha = self._color.getHsv()  # pyright: ignore[reportGeneralTypeIssues]
            values = (max(0, hue), saturation, value)
        self._updating = True
        try:
            for slider, label, value in zip(
                self.sliders, self.value_labels, values
            ):
                slider.setValue(int(value))
                label.setText(str(slider.value()))
        finally:
            self._updating = False
        self._refresh_preview()

    def _sliders_changed(self):
        if self._updating or len(self.sliders) != 3:
            return
        values = [slider.value() for slider in self.sliders]
        self._color = (
            QColor(*values)
            if self.mode.currentText() == "RGB"
            else QColor.fromHsv(*values)
        )
        self._refresh_preview()
        self.colorChanged.emit(QColor(self._color))

    def _refresh_preview(self):
        foreground = "#111" if self._color.lightness() >= 150 else "#fff"
        self.preview.setText(self._color.name(QColor.NameFormat.HexRgb).upper())
        self.preview.setStyleSheet(
            f"background:{self._color.name()};color:{foreground};"
            "border:1px solid #777;padding:3px;"
        )


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
            self.setGraphicsEffect(None)  # pyright: ignore[reportArgumentType]  # None clears the effect
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
