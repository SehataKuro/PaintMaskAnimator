from .common import *  # noqa: F401,F403
from .utils import _ScreenColorDragMixin
from .logging_setup import get_logger

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QDrag

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selection_role = ""
        self._selection_frame_enabled = False
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

    def setSelectionRole(self, role):
        """親は赤枠、子は青枠、未選択は初期版と同じ白枠で描画する。"""
        role = str(role or "")
        if role not in ("parent", "child", ""):
            role = ""
        changed = self._selection_role != role or not self._selection_frame_enabled
        self._selection_role = role
        self._selection_frame_enabled = True
        if changed:
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._selection_frame_enabled:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect().adjusted(1, 1, -2, -2)

        if self._selection_role == "parent":
            frame_color = QColor("#e53935")
            side_width = 8
            horizontal_width = 3
        elif self._selection_role == "child":
            frame_color = QColor("#2979ff")
            side_width = 8
            horizontal_width = 3
        else:
            frame_color = QColor("#ffffff")
            side_width = 2
            horizontal_width = 2

        # 親・子選択時は左右の縦線を太くして視認性を上げる。
        painter.fillRect(
            rect.left(),
            rect.top(),
            side_width,
            rect.height(),
            frame_color,
        )
        painter.fillRect(
            rect.right() - side_width + 1,
            rect.top(),
            side_width,
            rect.height(),
            frame_color,
        )
        painter.fillRect(
            rect.left(),
            rect.top(),
            rect.width(),
            horizontal_width,
            frame_color,
        )
        painter.fillRect(
            rect.left(),
            rect.bottom() - horizontal_width + 1,
            rect.width(),
            horizontal_width,
            frame_color,
        )

        if self._drop_mode == "child":
            # 子化のドロップ先は、太い黄色枠で強調する。
            painter.setPen(QPen(QColor("#ffca28"), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        elif self._drop_mode in ("before", "after"):
            # 並べ替えのドロップ位置を、行の上端／下端の水平線で示す。
            painter.setPen(QPen(QColor("#ffca28"), 3))
            y = 1 if self._drop_mode == "before" else self.height() - 2
            painter.drawLine(2, y, self.width() - 3, y)
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

        # ドラッグ中は運んでいる色をそのままカーソルに付ける。
        pixmap = QPixmap(28, 20)
        pixmap.fill(QColor(red, green, blue))
        painter = QPainter(pixmap)
        painter.setPen(QPen(QColor("#000000"), 1))
        painter.drawRect(0, 0, pixmap.width() - 1, pixmap.height() - 1)
        painter.end()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.DropAction.MoveAction)
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
        self.source_buttons = {}
        self.source_wrappers = {}
        self.row_widgets = {}

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        layout.addWidget(QLabel("<b>使用色</b>"))
        note = QLabel(
            "使用色：ドラッグで並べ替え。色の中央へドロップするとその色の"
            "「子」になり、キャンバス上では親色でプレビュー表示します。"
            "問題なければ［フリーズ］で実画像へ焼き込み。右クリックで解除など。"
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
        visibility_header = QLabel("表示")
        visibility_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        visibility_header.setStyleSheet("font-size:10px;")
        visibility_header.setToolTip("各行の薄い背景セル全体を右クリックして表示メニューを開けます。")
        header.addWidget(visibility_header, 0, 0, Qt.AlignmentFlag.AlignCenter)
        mask_header = QLabel("マスク")
        mask_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mask_header.setStyleSheet("font-size:10px;")
        mask_header.setToolTip("各行の薄い背景セル全体を右クリックしてマスクメニューを開けます。")
        header.addWidget(mask_header, 0, 1, Qt.AlignmentFlag.AlignCenter)
        color_header = QLabel("色（ドラッグで並べ替え／親子付け）")
        color_header.setStyleSheet("font-size:10px;")
        header.addWidget(color_header, 0, 2)
        header.setColumnMinimumWidth(2, 144)
        header.setColumnStretch(2, 1)
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
        button_row.setColumnMinimumWidth(2, 72)
        button_row.setColumnMinimumWidth(3, 72)
        button_row.setColumnStretch(2, 1)
        button_row.setColumnStretch(3, 1)

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
            self.show_all_button,
            self.clear_masks_button,
            self.merge_button,
            self.freeze_button,
        )):
            button.setMinimumWidth(0)
            button_row.addWidget(button, 0, column)
        layout.addLayout(button_row)

    @staticmethod
    def _rgb_key(color):
        qc = QColor(color)
        return (qc.red(), qc.green(), qc.blue())

    @staticmethod
    def _text_color(rgb):
        luminance = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        return "#111111" if luminance >= 150 else "#ffffff"

    def _set_source_button_style(self, rgb):
        button = self.source_buttons.get(rgb)
        wrapper = self.source_wrappers.get(rgb)
        if button is None:
            return

        color = QColor(*rgb)
        text_color = self._text_color(rgb)
        if rgb == self.parent_rgb:
            inner_border = "1px solid #ffffff"
            selection_role = "parent"
            role = "親"
        elif rgb in self.selected_rgbs:
            inner_border = "1px solid #ffffff"
            selection_role = "child"
            role = "子"
        else:
            inner_border = "1px solid #ffffff"
            selection_role = ""
            role = ""

        button.setStyleSheet(
            "QToolButton{"
            f"background:{color.name()};color:{text_color};border:{inner_border};"
            "padding:2px;"
            "}"
            "QToolButton:hover{"
            f"background:{color.name()};color:{text_color};border:{inner_border};"
            "}"
        )
        if wrapper is not None:
            if hasattr(wrapper, "setSelectionRole"):
                wrapper.setSelectionRole(selection_role)
            else:
                # 旧ウィジェットとの互換用。
                fallback = (
                    "#e53935" if selection_role == "parent"
                    else "#2979ff" if selection_role == "child"
                    else "#777777"
                )
                wrapper.setStyleSheet(
                    f"background:{fallback};border:2px solid #ffffff;"
                )

        if rgb == self.background_rgb:
            button.setToolTip(
                "背景色 #FFFFFF。並べ替えや親子付けの対象にはできません。"
            )
        else:
            role_note = f"現在は{role}です。" if role else ""
            group_note = ""
            parent_of_this = self.child_to_parent.get(rgb)
            if parent_of_this is not None:
                group_note = (
                    f"　現在 #{parent_of_this[0]:02X}{parent_of_this[1]:02X}"
                    f"{parent_of_this[2]:02X} の子（プレビュー中）です。"
                )
            button.setToolTip(
                "クリック：統合用の使用色として選択／再クリックで解除。"
                "上へドラッグ＝並べ替え、色の中央へドロップ＝その色の子にして"
                "親色でプレビュー。右クリックで親子解除などのメニュー。"
                + role_note
                + group_note
            )

    def _clear_rows(self):
        self.visibility_checks.clear()
        self.mask_checks.clear()
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
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(8, 3, 8, 3)
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
        row.setColumnMinimumWidth(2, 72)
        row.setColumnMinimumWidth(3, 72)
        row.setColumnStretch(2, 1)
        row.setColumnStretch(3, 1)

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
                "クリック：ON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：この色だけON／右クリック：マスクメニュー"
            )
            if not is_background else
            (
                "クリック：背景マスクON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：背景だけON／右クリック：マスクメニュー"
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
        visible_area = CheckClickArea(visible_check)
        mask_area = CheckClickArea(mask_check)
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
        row.addWidget(visible_area, 0, 0)
        row.addWidget(mask_area, 0, 1)
        # 置換色列を廃止し、色スウォッチが選択列全体を占める。
        row.addWidget(source_wrapper, 0, 2)

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
        self._isolated_rgb = None
        self._pre_isolate_enabled = None
        self._set_checkbox_without_signal(rgb, enabled)
        self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _disable_other_visible_colors(self, rgb):
        """右クリック対象だけを表示し、それ以外をOFFにする。"""
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
        self.mask_rgbs = set(self.mask_checks)
        for rgb in self.mask_checks:
            self._set_mask_checkbox_without_signal(rgb, True)
        self._emit_mask_state()

    def _clear_mask_colors(self):
        """Compatibility helper: turn every mask OFF."""
        self.mask_rgbs.clear()
        for rgb in self.mask_checks:
            self._set_mask_checkbox_without_signal(rgb, False)
        self._emit_mask_state()

    def _isolate_mask_color(self, rgb):
        """Alt+click: enable only the clicked mask."""
        self.mask_rgbs = {rgb}
        for key in self.mask_checks:
            self._set_mask_checkbox_without_signal(key, key == rgb)
        self._emit_mask_state()

    def _disable_other_masks(self, rgb):
        """右クリック対象だけをONにし、それ以外のマスクをOFFにする。"""
        self.mask_rgbs = {rgb}
        for key in self.mask_checks:
            self._set_mask_checkbox_without_signal(key, key == rgb)
        self._emit_mask_state()

    def _set_single_mask_state(self, rgb, enabled):
        self._set_mask_checkbox_without_signal(rgb, enabled)
        if enabled:
            self.mask_rgbs.add(rgb)
        else:
            self.mask_rgbs.discard(rgb)
        self._emit_mask_state()

    def _begin_mask_sweep(self, source_rgb, checked):
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

    def _ordered_non_background_rgbs(self):
        return [
            self._rgb_key(color) for color in self.colors
            if self._rgb_key(color) != self.background_rgb
        ]

    def _select_used_color(self, rgb, modifiers=Qt.KeyboardModifier.NoModifier):
        if rgb == self.background_rgb:
            return
        if modifiers & Qt.KeyboardModifier.ShiftModifier and self._selection_anchor_rgb:
            ordered = self._ordered_non_background_rgbs()
            if rgb in ordered and self._selection_anchor_rgb in ordered:
                start = ordered.index(self._selection_anchor_rgb)
                end = ordered.index(rgb)
                lo, hi = sorted((start, end))
                old = set(self.selected_rgbs)
                for value in ordered[lo:hi + 1]:
                    if value not in self.selected_rgbs:
                        self.selected_rgbs.append(value)
                self.parent_rgb = rgb
                self._refresh_used_color_styles(old | set(self.selected_rgbs))
                self.selectedColorsChanged.emit(set(self.selected_rgbs))
                return
        self._selection_anchor_rgb = rgb
        self._toggle_used_color_selection(rgb)

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
        """子色は親色でプレビュー表示し、インデントとリンク記号を付ける。"""
        button = self.source_buttons.get(rgb)
        wrapper = self.source_wrappers.get(rgb)
        if button is None:
            return
        parent = self.child_to_parent.get(rgb)
        base_text = (
            "背景色 #FFFFFF"
            if rgb == self.background_rgb
            else "#{:02X}{:02X}{:02X}".format(*rgb)
        )
        if parent is not None:
            root = self._group_root(rgb)
            preview = QColor(*root)
            text_color = self._text_color(root)
            button.setText(f"　└ {base_text} → 親色")
            button.setStyleSheet(
                "QToolButton{"
                f"background:{preview.name()};color:{text_color};"
                "border:1px dashed #ffca28;padding:2px;}"
                "QToolButton:hover{"
                f"background:{preview.name()};color:{text_color};"
                "border:1px dashed #ffca28;}"
            )
            if wrapper is not None:
                margin = wrapper.layout()
                if margin is not None:
                    margin.setContentsMargins(22, 3, 8, 3)
        else:
            child_count = sum(
                1 for value in self.child_to_parent.values() if value == rgb
            )
            button.setText(
                base_text if not child_count else f"{base_text}（親・{child_count}）"
            )
            self._set_source_button_style(rgb)
            if wrapper is not None:
                margin = wrapper.layout()
                if margin is not None:
                    margin.setContentsMargins(8, 3, 8, 3)

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
        self._visibility_sweep_touched.clear()
        self._visibility_sweep_changed = False

    def _set_color_visible(self, source_rgb, checked):
        if self._visibility_sweep_active:
            return
        self._isolated_rgb = None
        self._pre_isolate_enabled = None
        self.enabled_colors[source_rgb] = bool(checked)
        self.visibleColorsChanged.emit(self.enabled_rgb_set())
