"""The row widgets that make up the used-color panel.

Split out of ``color_panel.py``: the source-colour swatch button, the three
checkbox variants (visibility / mask / selection) with their custom painting and
sweep-drag behaviour, and the swatch row itself (``ColorSelectionArea``) with its
drag-and-drop handling. ``UsedColorPanel`` composes these; they know nothing
about the panel beyond the signals they emit.
"""
from PySide6.QtCore import QByteArray, QPoint, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from . import icons, theme
from .i18n import tr
from .color_picker_dialog import ColorPickerDialog
from .utils import (
    ScreenColorPickerOverlay,
    _ScreenColorDragMixin,
    show_screen_color_loupe,
)
from .logging_setup import get_logger

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QDialogButtonBox, QGraphicsOpacityEffect

log = get_logger(__name__)

# 使用色をドラッグ＆ドロップで統合するときに使う専用MIME形式。
USED_COLOR_MIME = "application/x-pma-used-color"


class FolderVisibilityButton(QToolButton):
    """フォルダーの一括表示。全部表示＝薄い目、一部＝半分の目、全部非表示＝目に斜線。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = Qt.CheckState.Checked
        self.setAutoRaise(True)
        self.setFixedSize(26, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QToolButton{border:0;background:transparent;}")

    def checkState(self):
        return self._state

    def setCheckState(self, state):
        if state != self._state:
            self._state = state
            self.update()

    def nextCheckState(self):
        # 押したら、全部表示なら全部隠し、それ以外は全部表示にする。
        self._state = (
            Qt.CheckState.Unchecked if self._state == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.nextCheckState()
            self.update()
            self.clicked.emit(self._state == Qt.CheckState.Checked)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        colors = theme.palette()
        hidden = self._state == Qt.CheckState.Unchecked
        color = QColor(colors["accent"] if hidden else colors["text_muted"])
        if self._state == Qt.CheckState.Checked:
            painter.setOpacity(0.55)
        elif self._state == Qt.CheckState.PartiallyChecked:
            painter.setOpacity(0.85)
        _draw_eye_icon(painter, self.rect(), color, not hidden)
        painter.end()


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
        # フォルダーどうしの間は段落のように空け、見出しと中の色を同じ薄い面に置く。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(0)
        self.frame = QWidget(self)
        self.frame.setObjectName("usedColorCategoryHeader")
        self.frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(self.frame)
        self.apply_theme()
        layout = QHBoxLayout(self.frame)
        layout.setContentsMargins(4, 1, 0, 1)
        layout.setSpacing(4)
        self.collapse_button = QToolButton(self.frame)
        self.collapse_button.setCheckable(True)
        self.collapse_button.setChecked(False)
        self.collapse_button.setAutoRaise(True)
        self.collapse_button.setFixedSize(18, 18)
        self.collapse_button.setText("▼")
        self.collapse_button.setStyleSheet(
            "QToolButton{border:0;font-size:8px;color:palette(placeholder-text);}"
        )
        self.collapse_button.setToolTip(tr("フォルダーを折りたたむ"))
        # フォルダーの一括表示は、行の「目」と同じ列に目のボタンで置く。
        self.visibility_check = FolderVisibilityButton(self.frame)
        self.visibility_check.setToolTip(tr("フォルダー内の色を一括表示／非表示"))
        self.name_label = QLabel(self._name, self.frame)
        self.name_label.setStyleSheet("font-size:11px;font-weight:600;")
        layout.addWidget(self.collapse_button)
        layout.addWidget(self.name_label, 1)
        layout.addWidget(self.visibility_check)
        # 右の「保護」「選択」列のぶんを空けて、目を行の目の列にそろえる。
        layout.addSpacing(26 * 2)
        self.collapse_button.toggled.connect(self._on_collapsed)
        self.visibility_check.clicked.connect(self.visibilityChanged.emit)

    def apply_theme(self):
        tint = theme.palette()["surface_alt"]
        self.frame.setStyleSheet(
            "QWidget#usedColorCategoryHeader{"
            f"background-color:{tint};border:0;"
            "border-top-left-radius:6px;border-top-right-radius:6px;}"
        )

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
            tr("フォルダーを展開") if collapsed else tr("フォルダーを折りたたむ")
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
    """タイムラインと同じ16pxの表示切替アイコンを中央に描く。"""
    dpr = painter.device().devicePixelRatioF() if painter.device() else 1.0
    pixmap = icons.pixmap(
        "eye" if is_open else "eye_off", 16, color.name(),
        device_pixel_ratio=dpr,
    )
    painter.drawPixmap(
        int(rect.left() + (rect.width() - 16) / 2),
        int(rect.top() + (rect.height() - 16) / 2),
        pixmap,
    )


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
            self._screen_pick_press_global = event.globalPosition().toPoint()
            event.accept()
            return
        # A plain left click belongs to the RGB / HSV editor. Bypass the mixin
        # so moving while left is held cannot start the eyedropper.
        QToolButton.mousePressEvent(self, event)

    def mouseMoveEvent(self, event):
        if self._screen_pick_active and self._screen_picker_overlay is None:
            show_screen_color_loupe(self, event.globalPosition().toPoint())
            event.accept()
            return
        if (
            self._right_picker_candidate
            and self._screen_pick_press_global is not None
            and event.buttons() & Qt.MouseButton.RightButton
        ):
            distance = (
                event.globalPosition().toPoint() - self._screen_pick_press_global
            ).manhattanLength()
            if distance >= QApplication.startDragDistance():
                # Right-drag picks directly and confirms on release, like the
                # other colour buttons; a plain right click opens the overlay.
                self._right_picker_candidate = False
                self._begin_screen_pick(Qt.MouseButton.RightButton)
                show_screen_color_loupe(self, event.globalPosition().toPoint())
                event.accept()
                return
        QToolButton.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event):
        if (
            self._screen_pick_active
            and self._screen_picker_overlay is None
            and event.button() == self._screen_pick_button
        ):
            self._finish_screen_pick(event.globalPosition().toPoint())
            event.accept()
            return
        if (
            event.button() == Qt.MouseButton.RightButton
            and self._right_picker_candidate
        ):
            self._right_picker_candidate = False
            self._screen_pick_press_global = None
            self._begin_global_screen_pick()
            event.accept()
            return
        QToolButton.mouseReleaseEvent(self, event)

    def hideEvent(self, event):
        overlay = self._screen_picker_overlay
        if overlay is not None:
            overlay.cancel()
        elif self._screen_pick_active:
            self._screen_pick_active = False
            self._screen_pick_button = Qt.MouseButton.NoButton
            self._cancel_screen_pick_cursor()
        self._right_picker_candidate = False
        QToolButton.hideEvent(self, event)


class ReplacementColorPopup(ColorPickerDialog):
    """置換色を編集するカラーピッカー（AE 風）。

    開いたまま他の操作もできるよう、モーダルにはしない。「プレビュー」が
    ON の間は編集をそのまま置換色へ反映し、キャンセルしたら開いたときの色へ戻す。
    """

    def __init__(
        self, color, source_rgb=None, parent=None, preview=None, on_clear=None,
    ):
        title = tr("置換色")
        if source_rgb is not None:
            title = tr("置換色　元色 #{hex}").format(
                hex="{:02X}{:02X}{:02X}".format(*source_rgb)
            )
        super().__init__(color, parent, title, preview)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.clear_button = None
        if on_clear is not None:
            # 置換色を未設定へ戻す（#66）。閉じるのは呼び出し側が行う。
            self.clear_button = self.button_box.addButton(
                tr("外す"), QDialogButtonBox.ButtonRole.ResetRole
            )
            self.clear_button.setToolTip(tr("この色の置換色を未設定に戻します。"))
            self.clear_button.clicked.connect(on_clear)


class ColorVisibilityCheckBox(QCheckBox):
    """固定幅の目のアイコンと、複数行をなぞる表示ON/OFFに対応。"""
    altClicked = Signal()
    sweepStarted = Signal(bool)
    sweepMoved = Signal(QPoint)
    sweepFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sweeping = False
        self._consume_release = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(24, 22)
        self.setStyleSheet(
            "QCheckBox{background:transparent;border:none;padding:0px;}"
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        # 表示中は控えめな目、非表示にした色だけアクセント色の「目に斜線」で目立たせる。
        colors = theme.palette()
        color = QColor(
            colors["text_muted"] if self.isChecked() or not self.isEnabled()
            else colors["accent"]
        )
        if self.isChecked():
            painter.setOpacity(0.55)
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
        self.setFixedSize(24, 22)
        self.setStyleSheet("QCheckBox{background:transparent;border:none;padding:0;}")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def paintEvent(self, event):
        # 描画対象 OFF＝「保護」。保護した色だけアクセント色の鍵で示し、
        # 描画対象 ON（ふだんの状態）は薄い鍵にして目立たせない。
        painter = QPainter(self)
        colors = theme.palette()
        protected = not self.isChecked()
        color = QColor(
            colors["accent"] if protected and self.isEnabled()
            else colors["text_muted"]
        )
        if not protected:
            painter.setOpacity(0.4)
        dpr = painter.device().devicePixelRatioF() if painter.device() else 1.0
        pixmap = icons.pixmap("lock", 15, color.name(),
                              device_pixel_ratio=dpr)
        painter.drawPixmap(
            int((self.width() - 15) / 2), int((self.height() - 15) / 2), pixmap
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
    box.adjust(5.0, 5.0, -5.0, -5.0)

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
        self.setFixedSize(24, 22)
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
        self.setFixedWidth(26)
        self.setFixedHeight(24)
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
        self._last = False
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

    def setSelected(self, selected, last=False):
        """選択中は薄いアクセント色の面、最後に選んだ色だけ太い枠で囲む。"""
        selected = bool(selected)
        last = bool(last) and selected
        changed = (
            self._selected != selected
            or self._last != last
            or not self._selection_frame_enabled
        )
        self._selected = selected
        self._last = last
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

        # 選択中の行は薄いアクセント面。統合先になる「最後に選んだ色」だけ枠で囲む。
        if self._selection_frame_enabled and self._selected:
            accent = self.palette().highlight().color()
            fill = QColor(accent)
            fill.setAlpha(46)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 5.0, 5.0)
            if self._last:
                pen = QPen(accent)
                pen.setWidthF(2.0)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(
                    QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0), 5.0, 5.0
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


class UsedColorModeSwitch(QWidget):
    """パネル左上の「通常｜置き換え」切り替え。置き換え側はアクセント色で示す。"""

    modeChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("usedColorModeSwitch")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self.normal_button = QToolButton(self)
        self.normal_button.setText(tr("通常"))
        self.normal_button.setToolTip(tr("使用色の表示・保護・選択を扱います。"))
        self.replace_button = QToolButton(self)
        self.replace_button.setText(tr("置き換え"))
        self.replace_button.setToolTip(
            tr("色ごとに置き換え後の色を決め、全コマをまとめて置き換えます。")
        )
        for button in (self.normal_button, self.replace_button):
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(button)
        self.normal_button.setChecked(True)
        self.replace_button.toggled.connect(self.modeChanged.emit)
        self.apply_theme()

    def isReplaceMode(self):
        return self.replace_button.isChecked()

    def setReplaceMode(self, replace):
        (self.replace_button if replace else self.normal_button).setChecked(True)

    def apply_theme(self):
        c = theme.palette()
        self.setStyleSheet(
            "QWidget#usedColorModeSwitch{"
            f"background:{c['surface_alt']};border:1px solid {c['border']};"
            "border-radius:6px;}"
            "QToolButton{border:0;border-radius:4px;padding:2px 9px;"
            f"font-size:11px;color:{c['text_muted']};background:transparent;}}"
            f"QToolButton:hover{{color:{c['text']};}}"
            "QToolButton:checked{"
            f"background:{c['surface']};color:{c['text']};font-weight:600;}}"
            "QToolButton#replaceModeButton:checked{"
            f"background:{c['accent']};color:{c['accent_text']};}}"
        )
        self.replace_button.setObjectName("replaceModeButton")


class MergeButton(QPushButton):
    """「統合 → ■ 統合先のコード」を描くボタン。text() は常に「統合」。"""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._target = None

    def setTarget(self, rgb):
        rgb = tuple(rgb) if rgb is not None else None
        if rgb != self._target:
            self._target = rgb
            self.updateGeometry()
            self.update()

    def target(self):
        return self._target

    def sizeHint(self):
        hint = super().sizeHint()
        if self._target is not None:
            hint.setWidth(hint.width() + 78)
        return hint

    def paintEvent(self, event):
        if self._target is None:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        option.icon = QIcon()
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
        rect = self.contentsRect().adjusted(8, 0, -8, 0)
        metrics = self.fontMetrics()
        text_color = self.palette().buttonText().color()
        if not self.isEnabled():
            text_color = self.palette().color(
                self.palette().ColorGroup.Disabled, self.palette().ColorRole.ButtonText
            )
        x = rect.left()
        icon_size = 14
        if not self.icon().isNull():
            self.icon().paint(
                painter, x, rect.center().y() - icon_size // 2, icon_size, icon_size
            )
            x += icon_size + 4
        painter.setPen(text_color)
        label = self.text() + "  →"
        painter.drawText(
            x, rect.top(), metrics.horizontalAdvance(label) + 2, rect.height(),
            Qt.AlignmentFlag.AlignVCenter, label,
        )
        x += metrics.horizontalAdvance(label) + 6
        swatch = QRectF(x, rect.center().y() - 6, 12, 12)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(0, 0, 0, 60), 1))
        painter.setBrush(QColor(*self._target))
        painter.drawRoundedRect(swatch, 3, 3)
        x += 16
        code = "{:02X}{:02X}{:02X}".format(*self._target)
        mono = self.font()
        mono.setFamilies(["Consolas", "Menlo", "monospace"])
        painter.setFont(mono)
        painter.setPen(text_color)
        painter.drawText(
            int(x), rect.top(), rect.right() - int(x), rect.height(),
            Qt.AlignmentFlag.AlignVCenter, code,
        )
        painter.end()
