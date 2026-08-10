from .common import *  # noqa: F401,F403
from .utils import _ScreenColorDragMixin
from .logging_setup import get_logger

log = get_logger(__name__)


class ScreenEyedropButton(_ScreenColorDragMixin, QToolButton):
    colorPicked = Signal(QColor)
    clearRequested = Signal()  # 旧形式との互換用
    colorEditorRequested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_screen_color_drag()
        self._right_click_candidate = False
        self._right_click_press_global = None
        self.setContextMenuPolicy(
            Qt.ContextMenuPolicy.PreventContextMenu
        )

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
            global_position = event.globalPosition().toPoint()
            self._right_click_press_global = None
            self.colorEditorRequested.emit(global_position)
            event.accept()
            return
        self._right_click_candidate = False
        self._right_click_press_global = None
        super().mouseReleaseEvent(event)



class ReplacementColorPopup(QDialog):
    """置換色をその場で編集する、ライブ反映式のRGB/HSVスライダー。"""

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

        note = QLabel("変更は即時反映されます。右クリックをもう一度行うと閉じます。")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.sliders = []
        self.value_labels = []
        self._rebuild_sliders("RGB")

    def color(self):
        return QColor(self._color)

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
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            row_layout.addWidget(slider, 1)
            row_layout.addWidget(label)
            slider.valueChanged.connect(
                lambda value, value_label=label: value_label.setText(str(value))
            )
            slider.valueChanged.connect(self._sliders_changed)
            self.slider_layout.addRow(name, row)
            self.sliders.append(slider)
            self.value_labels.append(label)
        self._sync_from_color()

    def _sync_from_color(self):
        if len(self.sliders) != 3:
            return
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
                slider.setValue(max(slider.minimum(), min(slider.maximum(), int(value))))
                label.setText(str(slider.value()))
        finally:
            self._updating = False
        self._refresh_preview()

    def _sliders_changed(self):
        if self._updating or len(self.sliders) != 3:
            return
        first, second, third = [slider.value() for slider in self.sliders]
        if self.mode.currentText() == "RGB":
            self._color = QColor(first, second, third)
        else:
            self._color = QColor.fromHsv(first, second, third)
        self._refresh_preview()
        self.colorChanged.emit(QColor(self._color))

    def _refresh_preview(self):
        foreground = "#111" if self._color.lightness() >= 150 else "#fff"
        self.preview.setText(self._color.name(QColor.NameFormat.HexRgb).upper())
        self.preview.setStyleSheet(
            f"background:{self._color.name()};color:{foreground};"
            "border:1px solid #777;padding:3px;"
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
    """使用色行／使用色枠。クリック、Shift選択、ドラッグ選択に対応。"""

    clicked = Signal(object)
    dragStarted = Signal(object)
    dragMoved = Signal(QPoint)
    dragFinished = Signal()
    contextMenuRequested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._select_dragging = False
        self._selection_role = ""
        self._selection_frame_enabled = False
        self.setMouseTracking(True)

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
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._select_dragging = True
            try:
                self.grabMouse()
            except RuntimeError as exc:
                log.debug("grabMouse() failed: %s", exc)
            # ドラッグ対象のON/OFFは、クリックで状態が変わる前に確定する。
            self.dragStarted.emit(event.modifiers())
            self.clicked.emit(event.modifiers())
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.contextMenuRequested.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._select_dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.dragMoved.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._select_dragging and event.button() == Qt.MouseButton.LeftButton:
            self._select_dragging = False
            try:
                self.releaseMouse()
            except RuntimeError as exc:
                log.debug("releaseMouse() failed: %s", exc)
            self.dragFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self.contextMenuRequested.emit(event.globalPos())
        event.accept()


class UsedColorPanel(QWidget):
    mainColorRequested = Signal(QColor)
    isolateColorClicked = Signal(QColor)
    clearIsolateRequested = Signal()
    sourceScreenColorPicked = Signal(QColor)
    applyReplacementRequested = Signal(object)
    mergeColorsRequested = Signal(object, object)
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
        self.replacements = {}
        self.enabled_colors = {self.background_rgb: True}
        self.mask_rgbs = {self.background_rgb}
        self._mask_all_mode = True

        # Used-color selection is separate from the drawing mask.
        # The most recently selected color is the parent; the others are children.
        self.selected_rgbs = []
        self.parent_rgb = None
        self._selection_anchor_rgb = None
        self._selection_drag_active = False
        self._selection_drag_state = True
        self._selection_drag_touched = set()

        self.visibility_checks = {}
        self.mask_checks = {}
        self.source_buttons = {}
        self.source_wrappers = {}
        self.replacement_buttons = {}
        self.row_widgets = {}
        self._replacement_popup = None
        self._replacement_popup_rgb = None

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
            "使用色：左クリックで親・子を選択／再クリックで解除。"
            "右クリックで統合・削除、上下ドラッグで選択を連続ON/OFFします。"
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
        header.addWidget(QLabel("選択"), 0, 2)
        header.addWidget(QLabel("置換色"), 0, 3)
        header.setColumnMinimumWidth(2, 72)
        header.setColumnMinimumWidth(3, 72)
        header.setColumnStretch(2, 1)
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

        self.apply_button = QPushButton("色置換")
        self.apply_button.setToolTip(
            "右側に登録した置換色を、選択中レイヤーのすべてのコマへ適用します。"
        )
        self.apply_button.clicked.connect(self._emit_replacements)
        self.apply_button.setMinimumWidth(72)
        self.apply_button.setMaximumWidth(16777215)
        self.apply_button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.apply_button.setStyleSheet("font-size:10px;padding:1px;")

        for column, button in enumerate((
            self.show_all_button,
            self.clear_masks_button,
            self.merge_button,
            self.apply_button,
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
                "背景色 #FFFFFF。背景色の置換はできません。"
            )
        else:
            role_note = f"現在は{role}です。" if role else ""
            button.setToolTip(
                "クリック：統合用の使用色として選択／再クリックで解除。"
                "最後に選んだ色が親になります。上下ドラッグ：選択を連続ON/OFF。"
                + role_note
            )

    def _set_replacement_button_style(self, source_rgb):
        button = self.replacement_buttons.get(source_rgb)
        if button is None:
            return
        replacement_color = self.replacements.get(source_rgb)
        if replacement_color is None:
            button.setText("未設定")
            button.setStyleSheet(
                "QToolButton{background:#f2f2f2;border:1px dashed #888;}"
                "QToolButton:hover{border:1px dashed #888;}"
            )
        else:
            button.setText(
                replacement_color.name(QColor.NameFormat.HexRgb).upper()
            )
            text_color = self._text_color((
                replacement_color.red(),
                replacement_color.green(),
                replacement_color.blue(),
            ))
            button.setStyleSheet(
                "QToolButton{"
                f"background:{replacement_color.name()};color:{text_color};"
                "border:1px solid #777;}"
                "QToolButton:hover{border:1px solid #777;}"
            )

    def _clear_rows(self):
        self._close_replacement_editor()
        self.visibility_checks.clear()
        self.mask_checks.clear()
        self.source_buttons.clear()
        self.source_wrappers.clear()
        self.replacement_buttons.clear()
        self.row_widgets.clear()
        while self.rows.count() > 1:
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _remove_color_row(self, rgb):
        """Remove only one row so palette updates do not rebuild every widget."""
        if self._replacement_popup_rgb == rgb:
            self._close_replacement_editor()
        widget = self.row_widgets.pop(rgb, None)
        if widget is not None:
            self.rows.removeWidget(widget)
            widget.deleteLater()
        self.visibility_checks.pop(rgb, None)
        self.mask_checks.pop(rgb, None)
        self.source_buttons.pop(rgb, None)
        self.source_wrappers.pop(rgb, None)
        self.replacement_buttons.pop(rgb, None)

    def _make_source_wrapper(self, source, source_rgb):
        wrapper = ColorSelectionArea()
        wrapper.clicked.connect(
            lambda modifiers, rgb=source_rgb:
            self._select_used_color(rgb, modifiers)
        )
        wrapper.dragStarted.connect(
            lambda modifiers, rgb=source_rgb:
            self._begin_used_color_drag(rgb, modifiers)
        )
        wrapper.dragMoved.connect(self._move_used_color_drag)
        wrapper.dragFinished.connect(self._end_used_color_drag)
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

        if is_background:
            # Background does not need a replacement column, so its swatch spans both.
            row.addWidget(source_wrapper, 0, 2, 1, 2)
        else:
            replacement = ScreenEyedropButton()
            replacement.setFixedHeight(26)
            replacement.setMinimumWidth(72)
            replacement.setMaximumWidth(16777215)
            replacement.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )
            replacement.setStyleSheet("font-size:10px;padding:1px;")
            replacement.setToolTip(
                "クリック：現在のメイン／サブ色を登録。"
                "登録済みでもう一度クリック：未設定へ戻す。"
                "左または右へドラッグ：その位置をスポイト。"
                "右クリック：置換色のカラースライダーを開く／もう一度で閉じる。"
            )
            replacement.clicked.connect(
                lambda _=False, rgb=source_rgb: self._toggle_replacement(rgb)
            )
            replacement.colorPicked.connect(
                lambda c, rgb=source_rgb: self._set_replacement_color(rgb, c)
            )
            replacement.colorEditorRequested.connect(
                lambda global_pos, rgb=source_rgb:
                self._toggle_replacement_editor(rgb, global_pos)
            )
            self.replacement_buttons[source_rgb] = replacement
            self._set_replacement_button_style(source_rgb)
            row.addWidget(source_wrapper, 0, 2)
            row.addWidget(replacement, 0, 3)

        self.row_widgets[source_rgb] = row_widget
        self.rows.insertWidget(max(0, self.rows.count() - 1), row_widget)

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

        # 消えた色だけを削除する。全行再構築と色順の並べ替えを避ける。
        for rgb in tuple(removed):
            if rgb == self.background_rgb:
                continue
            self._remove_color_row(rgb)
            self.enabled_colors.pop(rgb, None)
            self.mask_rgbs.discard(rgb)
            self.replacements.pop(rgb, None)

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
        self.replacements = {
            rgb: color for rgb, color in self.replacements.items()
            if rgb in incoming_keys and rgb != self.background_rgb
        }

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

    def _begin_used_color_drag(self, rgb, modifiers):
        if rgb == self.background_rgb:
            return
        self._selection_drag_active = True
        self._selection_drag_touched = {rgb}
        # 未選択から開始＝ONへ、選択済みから開始＝OFFへなぞる。
        self._selection_drag_state = rgb not in self.selected_rgbs

    def _used_color_rgb_at_global(self, global_position):
        # 表示・マスク・置換色列へはみ出しても誤操作しないよう、
        # 「選択」列のラッパー内だけをドラッグ対象にする。
        for rgb, widget in self.source_wrappers.items():
            if rgb == self.background_rgb:
                continue
            if widget.rect().contains(widget.mapFromGlobal(global_position)):
                return rgb
        return None

    def _move_used_color_drag(self, global_position):
        if not self._selection_drag_active:
            return
        rgb = self._used_color_rgb_at_global(global_position)
        if rgb is None or rgb in self._selection_drag_touched:
            return
        self._selection_drag_touched.add(rgb)
        old = set(self.selected_rgbs)
        if self._selection_drag_state:
            if rgb not in self.selected_rgbs:
                self.selected_rgbs.append(rgb)
        elif rgb in self.selected_rgbs:
            self.selected_rgbs.remove(rgb)
        self.parent_rgb = self.selected_rgbs[-1] if self.selected_rgbs else None
        self._refresh_used_color_styles(old | set(self.selected_rgbs))
        self.selectedColorsChanged.emit(set(self.selected_rgbs))

    def _end_used_color_drag(self):
        self._selection_drag_active = False
        self._selection_drag_touched.clear()

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

    def _toggle_replacement(self, source_rgb):
        """未設定なら登録、登録済みなら同じクリックで解除する。"""
        if source_rgb == self.background_rgb:
            return
        if source_rgb in self.replacements:
            self._clear_replacement(source_rgb)
        else:
            self._request_replacement(source_rgb)

    def _close_replacement_editor(self):
        popup = self._replacement_popup
        self._replacement_popup = None
        self._replacement_popup_rgb = None
        if popup is not None:
            popup.close()
            popup.deleteLater()

    def _replacement_popup_destroyed(self, popup):
        if self._replacement_popup is popup:
            self._replacement_popup = None
            self._replacement_popup_rgb = None

    def _toggle_replacement_editor(self, source_rgb, global_position):
        """右クリックで開き、同じ置換色をもう一度右クリックすると閉じる。"""
        if source_rgb == self.background_rgb:
            return
        if (
            self._replacement_popup is not None
            and self._replacement_popup.isVisible()
            and self._replacement_popup_rgb == source_rgb
        ):
            self._close_replacement_editor()
            return

        self._close_replacement_editor()
        color = self.replacements.get(source_rgb)
        if color is None:
            window = self.window()
            if hasattr(window, "canvas"):
                canvas = window.canvas
                color = (
                    canvas.sub_color
                    if canvas.color_mode == "sub"
                    else canvas.main_color
                )
            else:
                color = QColor(*source_rgb)
            # ポップアップを開いた時点で編集対象として登録する。
            self._set_replacement_color(source_rgb, color)

        popup = ReplacementColorPopup(
            QColor(color),
            source_rgb,
            self,
        )
        self._replacement_popup = popup
        self._replacement_popup_rgb = source_rgb
        popup.colorChanged.connect(
            lambda changed, rgb=source_rgb:
            self._set_replacement_color(rgb, changed)
        )
        popup.destroyed.connect(
            lambda _obj=None, current=popup:
            self._replacement_popup_destroyed(current)
        )

        popup.adjustSize()
        screen = QApplication.screenAt(global_position) or QApplication.primaryScreen()
        position = QPoint(global_position)
        if screen is not None:
            available = screen.availableGeometry()
            width = popup.sizeHint().width()
            height = popup.sizeHint().height()
            position.setX(
                max(available.left(), min(position.x(), available.right() - width + 1))
            )
            position.setY(
                max(available.top(), min(position.y(), available.bottom() - height + 1))
            )
        popup.move(position)
        popup.show()
        popup.raise_()
        popup.activateWindow()

    def _request_replacement(self, source_rgb):
        if source_rgb == self.background_rgb:
            return
        window = self.window()
        if not hasattr(window, "canvas"):
            return
        canvas = window.canvas
        color = (
            canvas.sub_color
            if canvas.color_mode == "sub"
            else canvas.main_color
        )
        self.replacements[source_rgb] = QColor(color)
        self._set_replacement_button_style(source_rgb)

    def register_replacements(self, mapping):
        registered = 0
        for source_rgb, target_rgb in mapping.items():
            source_rgb = tuple(int(value) for value in source_rgb[:3])
            target_rgb = tuple(int(value) for value in target_rgb[:3])
            if (
                source_rgb == self.background_rgb
                or source_rgb not in self.replacement_buttons
            ):
                continue
            self.replacements[source_rgb] = QColor(*target_rgb)
            self._set_replacement_button_style(source_rgb)
            registered += 1
        return registered

    def _set_replacement_color(self, source_rgb, color):
        if source_rgb == self.background_rgb:
            return
        self.replacements[source_rgb] = QColor(color)
        self._set_replacement_button_style(source_rgb)

    def _clear_replacement(self, source_rgb):
        """登録済みの置換色を、同じ左クリックでもう一度押して解除する。"""
        if source_rgb == self.background_rgb:
            return
        if self._replacement_popup_rgb == source_rgb:
            self._close_replacement_editor()
        self.replacements.pop(source_rgb, None)
        self._set_replacement_button_style(source_rgb)

    def _emit_replacements(self):
        mapping = {
            tuple(source): (color.red(), color.green(), color.blue())
            for source, color in self.replacements.items()
            if tuple(source) != self.background_rgb
        }
        self.applyReplacementRequested.emit(mapping)
