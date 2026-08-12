from .common import *  # noqa: F401,F403
from typing import Any
from . import theme
from .utils import _ScreenColorDragMixin
from .logging_setup import get_logger

log = get_logger(__name__)


class TransformLineThicknessDialog(QDialog):
    """TPクオリティ変形の実線太さをライブ調整する。"""

    def __init__(self, value=96, parent=None):
        super().__init__(parent)
        self.setWindowTitle("太さを調整")
        self.setModal(True)
        self.resize(380, 130)

        layout = QVBoxLayout(self)
        note = QLabel(
            "選択中の複数色を実線として残す太さをまとめて調整します。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        row = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, 254)
        self.slider.setInvertedAppearance(True)
        self.slider.setValue(max(1, min(254, int(value))))
        self.value_label = QLabel(f"{255 - self.slider.value()}")
        self.value_label.setFixedWidth(42)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.slider.valueChanged.connect(
            lambda current: self.value_label.setText(str(255 - current))
        )
        row.addWidget(self.slider, 1)
        row.addWidget(self.value_label)
        layout.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self):
        return int(self.slider.value())



class CanvasSizeDialog(QDialog):
    def __init__(self, width, height, title, parent=None):
        super().__init__(parent); self.setWindowTitle(title)
        l = QFormLayout(self)
        self.w = QSpinBox(); self.w.setRange(64, 8192); self.w.setValue(width)
        self.h = QSpinBox(); self.h.setRange(64, 8192); self.h.setValue(height)
        l.addRow("幅", self.w); l.addRow("高さ", self.h)
        b = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        b.accepted.connect(self.accept); b.rejected.connect(self.reject); l.addRow(b)
    def values(self): return self.w.value(), self.h.value()


class HoldShortcutEdit(QPushButton):
    """押している間だけ有効な操作用。修飾キー単独も登録できるキー入力欄。"""

    def __init__(self, shortcut_text="", parent=None):
        super().__init__(parent)
        self._shortcut_text = str(shortcut_text or "")
        self._waiting = False
        self.setMinimumWidth(180)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._begin_capture)
        self._refresh_text()

    def _begin_capture(self):
        self._waiting = True
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.setText("キーを押してください…")

    def shortcutText(self):
        return self._shortcut_text

    def setShortcutText(self, text):
        self._shortcut_text = str(text or "")
        self._waiting = False
        self._refresh_text()

    def _refresh_text(self):
        self.setText(self._shortcut_text or "未設定")
        self.setToolTip(
            "クリックしてキーを入力。BackspaceまたはDeleteで解除。"
        )

    @staticmethod
    def _key_name(event):
        key_map = {
            Qt.Key.Key_Control: "Ctrl",
            Qt.Key.Key_Shift: "Shift",
            Qt.Key.Key_Alt: "Alt",
            Qt.Key.Key_Meta: "Meta",
            Qt.Key.Key_Space: "Space",
        }
        if event.key() in key_map:
            return key_map[event.key()]
        return QKeySequence(int(event.key())).toString(
            QKeySequence.SequenceFormat.PortableText
        )

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.setShortcutText("")
            event.accept()
            return
        key_name = self._key_name(event)
        if not key_name:
            super().keyPressEvent(event)
            return
        tokens = []
        modifiers = event.modifiers()
        for flag, name in (
            (Qt.KeyboardModifier.ControlModifier, "Ctrl"),
            (Qt.KeyboardModifier.ShiftModifier, "Shift"),
            (Qt.KeyboardModifier.AltModifier, "Alt"),
            (Qt.KeyboardModifier.MetaModifier, "Meta"),
        ):
            if modifiers & flag:
                tokens.append(name)
        if key_name not in tokens:
            tokens.append(key_name)
        self.setShortcutText("+".join(tokens))
        event.accept()

    def focusOutEvent(self, event):
        self._waiting = False
        self._refresh_text()
        super().focusOutEvent(event)


class ShortcutDialog(QDialog):
    def __init__(self, categories, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ショートカット設定")
        self.resize(700, 620)
        self.categories = categories
        layout = QVBoxLayout(self)
        note = QLabel(
            "ショートカット欄を選び、実際のキーまたはキーの組み合わせを押してください。"
            "文字列入力ではなくキー入力として認識します。"
            "「キャンバス操作」では修飾キー単独も登録できます。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tabs = QTabWidget()
        self.editors = []
        for title, actions in categories:
            page = QWidget()
            form = QFormLayout(page)
            page_editors = []
            for name, action in actions:
                if bool(action.property("holdOperation")):
                    stored = action.property("holdShortcutText")
                    editor = HoldShortcutEdit(
                        "" if stored is None else str(stored)
                    )
                else:
                    editor = QKeySequenceEdit(action.shortcut())
                    editor.setClearButtonEnabled(True)
                form.addRow(name, editor)
                page_editors.append((name, action, editor))
            form.addRow(QWidget())
            self.tabs.addTab(page, title)
            self.editors.extend(page_editors)
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self):
        used = {}
        duplicates = []
        pending = []
        for name, action, editor in self.editors:
            if isinstance(editor, HoldShortcutEdit):
                normalized = editor.shortcutText().strip()
                sequence = QKeySequence(normalized)
                is_hold = True
            else:
                sequence = editor.keySequence()
                normalized = sequence.toString(
                    QKeySequence.SequenceFormat.PortableText
                )
                is_hold = False
            if normalized:
                if normalized in used:
                    duplicates.append((normalized, used[normalized], name))
                else:
                    used[normalized] = name
            pending.append((action, sequence, is_hold, normalized))
        if duplicates:
            details = "\n".join(
                f"{shortcut}: {first} / {second}"
                for shortcut, first, second in duplicates[:12]
            )
            QMessageBox.warning(
                self,
                "ショートカットの重複",
                "同じショートカットが複数の機能に登録されています。\n\n" + details,
            )
            return
        for action, sequence, is_hold, normalized in pending:
            if is_hold:
                action.setProperty("holdShortcutText", normalized)
            action.setShortcut(sequence)
        self.accept()


class SwatchEyedropButton(_ScreenColorDragMixin, QPushButton):
    colorPicked = Signal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_screen_color_drag()


class BrushSizeSpinBox(QDoubleSpinBox):
    """▲▼で0.5ずつ変更するブラシ／ラインサイズ表示。"""

    pressureRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0.5, 400.0)
        self.setDecimals(1)
        self.setSingleStep(0.5)
        self.setReadOnly(False)
        self.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.UpDownArrows
        )
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pressure_popup_enabled = True
        editor = self.lineEdit()
        editor.setReadOnly(True)
        editor.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._refresh_tooltip()

    def _refresh_tooltip(self):
        self.setToolTip(
            "▲▼をクリック：0.5ずつ変更"
            + (
                "／数値部分をクリック：筆圧設定"
                if self._pressure_popup_enabled else ""
            )
        )

    def setPressurePopupEnabled(self, enabled):
        self._pressure_popup_enabled = bool(enabled)
        self._refresh_tooltip()

    def _clear_number_selection(self):
        editor = self.lineEdit()
        if editor is None:
            return
        editor.deselect()
        editor.setCursorPosition(len(editor.text()))
        editor.clearFocus()
        self.clearFocus()

    def stepBy(self, steps):
        # ▲▼で変更した後、数値全体が青く選択される状態を残さない。
        super().stepBy(steps)
        QTimer.singleShot(0, self._clear_number_selection)

    def mouseReleaseEvent(self, event):
        if (
            self._pressure_popup_enabled
            and event.button() == Qt.MouseButton.LeftButton
            and self.lineEdit().geometry().contains(
                event.position().toPoint()
            )
        ):
            self.pressureRequested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        QTimer.singleShot(0, self._clear_number_selection)

    def wheelEvent(self, event):
        event.ignore()



class SliderValueSpinBox(QDoubleSpinBox):
    """RGB/HSVスライダーと連動する、編集可能な小型数値欄。"""

    def __init__(self, slider, scale=1.0, step=1.0, parent=None):
        super().__init__(parent)
        self.slider = slider
        self.scale = max(1e-9, float(scale))
        self._ascii_step = float(step)

        self.setDecimals(
            1 if abs(float(step) - round(float(step))) > 1e-9 else 0
        )
        self.setSingleStep(float(step))
        self.setRange(
            float(slider.minimum()) / self.scale,
            float(slider.maximum()) / self.scale,
        )
        self.setReadOnly(False)
        self.setKeyboardTracking(True)
        self.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # 0～255の数字と小型▲▼だけが収まる幅に圧縮する。
        self.setFixedWidth(48)
        self.setFixedHeight(22)
        self.setStyleSheet(
            "QDoubleSpinBox{font-size:10px;padding-left:1px;"
            "padding-right:10px;"
            "selection-background-color:palette(highlight);"
            "selection-color:palette(highlighted-text);}"
        )

        editor = self.lineEdit()
        editor.setReadOnly(False)
        editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        editor.setMaxLength(5 if self.decimals() else 3)
        editor.setAlignment(Qt.AlignmentFlag.AlignRight)
        editor.setTextMargins(0, 0, 9, 0)
        editor.setToolTip(
            "半角数字だけ入力できます。"
            if self.decimals() == 0
            else "半角数字と小数点だけ入力できます。"
        )

        # 標準の四角いスピンボタンを使わず、文字の▲▼を直接表示する。
        self._up_button = QToolButton(self)
        self._down_button = QToolButton(self)
        for button, label in (
            (self._up_button, "▲"),
            (self._down_button, "▼"),
        ):
            button.setText(label)
            button.setAutoRepeat(True)
            button.setAutoRepeatDelay(350)
            button.setAutoRepeatInterval(70)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                "QToolButton{border:none;background:transparent;"
                "padding:0px;margin:0px;font-size:7px;}"
                "QToolButton:hover{background:palette(alternate-base);}"
                "QToolButton:pressed{background:palette(highlight);"
                "color:palette(highlighted-text);}"
            )

        self._up_button.clicked.connect(
            self._step_up_without_selection
        )
        self._down_button.clicked.connect(
            self._step_down_without_selection
        )

        self.setToolTip(
            f"▲▼をクリック：{float(step):g}ずつ変更／"
            + (
                "半角整数を直接入力できます。"
                if self.decimals() == 0
                else "半角数値を直接入力できます。"
            )
        )
        self.valueChanged.connect(self._spin_changed)
        slider.valueChanged.connect(self._slider_changed)
        self._slider_changed(slider.value())

    def _clear_number_selection(self):
        editor = self.lineEdit()
        if editor is None:
            return
        editor.deselect()
        editor.setCursorPosition(len(editor.text()))

        # ▲▼ボタンにフォーカスを残さず、編集欄の青い選択も解除する。
        self._up_button.clearFocus()
        self._down_button.clearFocus()

    def _step_up_without_selection(self):
        self.stepUp()
        QTimer.singleShot(0, self._clear_number_selection)

    def _step_down_without_selection(self):
        self.stepDown()
        QTimer.singleShot(0, self._clear_number_selection)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        button_width = 9
        half_height = max(8, self.height() // 2)
        x = self.width() - button_width - 1
        self._up_button.setGeometry(
            x, 1, button_width, half_height - 1
        )
        self._down_button.setGeometry(
            x,
            half_height,
            button_width,
            self.height() - half_height - 1,
        )
        self._up_button.raise_()
        self._down_button.raise_()

    def validate(self, input_text, position):
        """全角文字・英字・記号を拒否し、ASCII数値だけ許可する。"""
        text = str(input_text)
        if text == "":
            return (
                QValidator.State.Intermediate,
                text,
                position,
            )

        allowed_characters = (
            "0123456789."
            if self.decimals() > 0
            else "0123456789"
        )
        if any(
            character not in allowed_characters
            for character in text
        ):
            return (
                QValidator.State.Invalid,
                text,
                position,
            )
        if text.count(".") > 1:
            return (
                QValidator.State.Invalid,
                text,
                position,
            )

        if "." in text:
            integer_part, fraction_part = text.split(".", 1)
            if len(fraction_part) > self.decimals():
                return (
                    QValidator.State.Invalid,
                    text,
                    position,
                )
            if integer_part == "":
                return (
                    QValidator.State.Intermediate,
                    text,
                    position,
                )

        try:
            value = float(text)
        except ValueError:
            return (
                QValidator.State.Intermediate,
                text,
                position,
            )

        if value < self.minimum() or value > self.maximum():
            return (
                QValidator.State.Invalid,
                text,
                position,
            )

        state = (
            QValidator.State.Intermediate
            if text.endswith(".")
            else QValidator.State.Acceptable
        )
        return (state, text, position)

    def valueFromText(self, text):
        try:
            return float(str(text))
        except ValueError:
            return float(self.value())

    def textFromValue(self, value):
        if self.decimals() > 0:
            return f"{float(value):.1f}"
        return str(int(round(float(value))))

    def _spin_changed(self, value):
        slider_value = int(round(float(value) * self.scale))
        slider_value = max(
            self.slider.minimum(),
            min(self.slider.maximum(), slider_value),
        )
        if self.slider.value() != slider_value:
            self.slider.setValue(slider_value)

    def _slider_changed(self, value):
        self.blockSignals(True)
        self.setValue(float(value) / self.scale)
        self.blockSignals(False)

    def setDisplayValue(self, value):
        self.blockSignals(True)
        self.setValue(float(value))
        self.blockSignals(False)

    def wheelEvent(self, event):
        event.ignore()



class ClickableValueLabel(QLabel):
    """数値表示をクリックした時だけ設定ポップアップを開くラベル。"""

    clicked = Signal(QPoint)

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class LineTaperCurvePopup(QDialog):
    """ラインの入り／抜きカーブをその場で調整するポップアップ。"""

    curveChanged = Signal(int)

    def __init__(self, title, value, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumWidth(260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        layout.addWidget(QLabel(f"<b>{title}</b>"))
        self.value_label = QLabel()
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.value_label)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(20, 400)
        self.slider.setValue(max(20, min(400, int(value))))
        self.slider.valueChanged.connect(self._value_changed)
        layout.addWidget(self.slider)
        note = QLabel("小さいほど緩やか、大きいほど先端付近で急に変化します。")
        note.setWordWrap(True)
        note.setStyleSheet("font-size:10px;")
        layout.addWidget(note)
        self._value_changed(self.slider.value())

    def _value_changed(self, value):
        self.value_label.setText(f"{value / 100:.2f}")
        self.curveChanged.emit(int(value))


class TweenCommandPopup(QDialog):
    """自由変形／メッシュ変形トゥイーン中の操作パネル。"""

    commitRequested = Signal()
    cancelRequested = Signal()
    rotateLeftRequested = Signal()
    rotateRightRequested = Signal()
    reverseChanged = Signal(bool)

    def __init__(
        self,
        mode="free",
        mesh_cols=4,
        mesh_rows=4,
        parent=None,
        reverse=False,
    ):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint,
        )
        self.mode = "mesh" if mode == "mesh" else "free"
        mode_name = "メッシュ変形" if self.mode == "mesh" else "自由変形"
        self.setWindowTitle(f"長方形選択：{mode_name}トゥイーン")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setModal(False)
        self.setMinimumWidth(260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        if self.mode == "mesh":
            description = (
                f"<b>メッシュ変形トゥイーン</b><br>"
                f"横{int(mesh_cols)}×縦{int(mesh_rows)}の格子点を操作して、"
                "最後のコマの形を指定してください。"
            )
        else:
            description = (
                "<b>自由変形トゥイーン</b><br>"
                "白い点や枠を操作して、最後のコマの形を指定してください。"
            )
        label = QLabel(description)
        label.setWordWrap(True)
        layout.addWidget(label)

        self.reverse_generation = QCheckBox(
            "逆生成（◆ーーーー│）"
        )
        self.reverse_generation.setChecked(
            bool(reverse)
        )
        self.reverse_generation.setToolTip(
            "ON：操作中の変形形状をキーフレーム側へ置き、"
            "元の初期形状をラストコマ側へ置きます。"
        )
        layout.addWidget(self.reverse_generation)

        self.direction_note = QLabel()
        self.direction_note.setWordWrap(True)
        self.direction_note.setStyleSheet(
            "font-size:10px;"
        )
        layout.addWidget(self.direction_note)
        self.reverse_generation.toggled.connect(
            self._sync_direction_note
        )
        self.reverse_generation.toggled.connect(
            self.reverseChanged
        )
        self._sync_direction_note(
            self.reverse_generation.isChecked()
        )

        rotate_row = QHBoxLayout()
        rotate_left = QPushButton("左へ90°")
        rotate_right = QPushButton("右へ90°")
        rotate_left.clicked.connect(self.rotateLeftRequested)
        rotate_right.clicked.connect(self.rotateRightRequested)
        rotate_row.addWidget(rotate_left)
        rotate_row.addWidget(rotate_right)
        layout.addLayout(rotate_row)

        commit = QPushButton(f"{mode_name}を確定してトゥイーン作成")
        cancel = QPushButton("キャンセル")
        commit.clicked.connect(self.commitRequested)
        cancel.clicked.connect(self.cancelRequested)
        layout.addWidget(commit)
        layout.addWidget(cancel)

    def _sync_direction_note(self, reverse):
        if reverse:
            self.direction_note.setText(
                "逆生成：現在操作している変形形状を"
                "キーフレーム側の◆へ配置し、"
                "ラストコマ側を元の初期形状にします。"
            )
        else:
            self.direction_note.setText(
                "通常生成：キーフレーム側を元の初期形状、"
                "ラストコマ側を現在操作している変形形状にします。"
            )


class HSVColorWheel(QWidget):
    colorChanged = Signal(QColor)
    modeChanged = Signal(str)
    hueModeChanged = Signal(str)

    #: 内側のピッカー形状と色相ピッカーの形状は独立して選択できる。
    MODES = ("HSV", "HLS")
    HUE_MODES = ("RING", "BAR")
    DEFAULT_MODE = "HSV"
    DEFAULT_HUE_MODE = "RING"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor("black")
        self._mode = self.DEFAULT_MODE
        self._hue_mode = self.DEFAULT_HUE_MODE
        self._cache_key = None
        self._cache_image = QImage()
        self._drag_part = None
        self.color_code_edit = QLineEdit(self._color.name().upper(), self)
        self.color_code_edit.setFixedSize(72, 22)
        self.color_code_edit.setMaxLength(7)
        self.color_code_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.color_code_edit.setToolTip(
            "カラーコードを入力して Enter で色を変更"
        )
        self.color_code_edit.setStyleSheet(
            "QLineEdit{font-size:10px;padding:1px 3px;}"
        )
        self.color_code_edit.editingFinished.connect(self._apply_color_code)
        self.color_copy_button = QPushButton("コピー", self)
        self.color_copy_button.setFixedSize(44, 22)
        self.color_copy_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.color_copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.color_copy_button.setToolTip("現在のカラーコードをコピー")
        self.color_copy_button.setStyleSheet(
            "QPushButton{font-size:9px;padding:1px 2px;}"
        )
        self.color_copy_button.clicked.connect(self._copy_color_code)
        self.setMinimumSize(160, 180)
        self.setMaximumHeight(220)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._update_tooltip()

    def mode(self):
        return self._mode

    def setMode(self, mode):
        mode = str(mode).upper()
        # 以前の一体型モード名も読み替えられるようにする。
        if mode == "HSV_RING":
            self.setHueMode("RING")
            mode = "HSV"
        if mode not in self.MODES or mode == self._mode:
            return
        self._mode = mode
        self._cache_key = None
        self._update_tooltip()
        self.update()
        self.modeChanged.emit(self._mode)

    def hueMode(self):
        return self._hue_mode

    def setHueMode(self, mode):
        mode = str(mode).upper()
        if mode not in self.HUE_MODES or mode == self._hue_mode:
            return
        self._hue_mode = mode
        self._cache_key = None
        self._update_tooltip()
        self.update()
        self.hueModeChanged.emit(self._hue_mode)

    def _update_tooltip(self):
        hue_picker = "外側の色相リング" if self._hue_mode == "RING" else "上の色相バー"
        inner_picker = (
            "内側の三角で輝度(L)と彩度(S)"
            if self._mode == "HLS" else
            "内側の四角で彩度と明度"
        )
        self.setToolTip(f"{hue_picker}と、{inner_picker}を選択します。")

    def setColor(self, color):
        color = QColor(color)
        if not color.isValid():
            return
        self._color = color
        self._refresh_color_code()
        self.update()

    def _refresh_color_code(self):
        self.color_code_edit.setText(self._color.name().upper())

    def _apply_color_code(self):
        text = self.color_code_edit.text().strip()
        if text and not text.startswith("#"):
            text = "#" + text
        color = QColor(text)
        if not color.isValid() or len(text) not in (4, 7):
            self._refresh_color_code()
            return
        self._color = color
        self._cache_key = None
        self._refresh_color_code()
        self.update()
        self.colorChanged.emit(QColor(self._color))

    def _copy_color_code(self):
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._color.name().upper())

    def _wheel_geometry(self):
        if self._hue_mode == "RING":
            available_height = max(1.0, self.height() - 28.0)
            diameter = min(
                max(1.0, self.width() - 4.0),
                max(1.0, available_height - 2.0),
            )
            outer = QRectF(
                (self.width() - diameter) / 2.0,
                2.0 + (available_height - diameter) / 2.0,
                diameter,
                diameter,
            )
            outer_radius = diameter / 2.0
            ring_width = max(12.0, min(22.0, outer_radius * 0.20))
            inner_radius = max(1.0, outer_radius - ring_width - 3.0)
            square_side = inner_radius * math.sqrt(2.0)
            square = QRectF(
                outer.center().x() - square_side / 2.0,
                outer.center().y() - square_side / 2.0,
                square_side,
                square_side,
            )
            return outer, square
        hue_bar = QRectF(
            4.0,
            3.0,
            max(24.0, self.width() - 8.0),
            18.0,
        )
        available_width = max(1.0, self.width() - 8.0)
        available_height = max(1.0, self.height() - 54.0)
        square_side = min(available_width, available_height)
        square = QRectF(
            (self.width() - square_side) / 2.0,
            25.0,
            square_side,
            square_side,
        )
        return hue_bar, square

    def _ring_metrics(self):
        outer, _square = self._wheel_geometry()
        outer_radius = outer.width() / 2.0
        ring_width = max(12.0, min(22.0, outer_radius * 0.20))
        return outer.center(), outer_radius, outer_radius - ring_width

    @staticmethod
    def _ring_hue(center, position):
        angle = math.atan2(
            position.x() - center.x(),
            center.y() - position.y(),
        )
        return int(round((math.degrees(angle) % 360.0) * 359.0 / 360.0))

    def _triangle_vertices(self):
        """HLS三角形の頂点。上=純色、左下=黒、右下=白。"""
        _hue_bar, square = self._wheel_geometry()
        top = QPointF(square.center().x(), square.top())
        bottom_left = QPointF(square.left(), square.bottom())
        bottom_right = QPointF(square.right(), square.bottom())
        return top, bottom_left, bottom_right

    @staticmethod
    def _barycentric(point, top, bottom_left, bottom_right):
        denom = (
            (bottom_left.y() - bottom_right.y()) * (top.x() - bottom_right.x())
            + (bottom_right.x() - bottom_left.x()) * (top.y() - bottom_right.y())
        )
        if abs(denom) < 1e-9:
            return 0.0, 0.0, 1.0
        w_top = (
            (bottom_left.y() - bottom_right.y()) * (point.x() - bottom_right.x())
            + (bottom_right.x() - bottom_left.x()) * (point.y() - bottom_right.y())
        ) / denom
        w_left = (
            (bottom_right.y() - top.y()) * (point.x() - bottom_right.x())
            + (top.x() - bottom_right.x()) * (point.y() - bottom_right.y())
        ) / denom
        w_right = 1.0 - w_top - w_left
        return w_top, w_left, w_right

    def _triangle_color(self, w_top, w_left, w_right):
        """頂点重みから色を合成する（黒は寄与なし）。"""
        hue = max(0, self._color.hsvHue())
        pure = QColor.fromHsv(hue, 255, 255)
        red = w_top * pure.red() + w_right * 255.0
        green = w_top * pure.green() + w_right * 255.0
        blue = w_top * pure.blue() + w_right * 255.0
        clamp = lambda v: max(0, min(255, int(round(v))))
        return QColor(clamp(red), clamp(green), clamp(blue))

    def _triangle_point_for_color(self):
        """現在色に対応する三角形内の座標を最小二乗で求める。"""
        top, bottom_left, bottom_right = self._triangle_vertices()
        hue = max(0, self._color.hsvHue())
        pure = QColor.fromHsv(hue, 255, 255)
        hr, hg, hb = pure.red(), pure.green(), pure.blue()
        cr, cg, cb = self._color.red(), self._color.green(), self._color.blue()
        s_aa = hr * hr + hg * hg + hb * hb
        s_ac = (hr + hg + hb) * 255.0
        s_cc = 3.0 * 255.0 * 255.0
        b1 = hr * cr + hg * cg + hb * cb
        b2 = 255.0 * (cr + cg + cb)
        det = s_aa * s_cc - s_ac * s_ac
        if abs(det) < 1e-6:
            w_top, w_right = 0.0, 1.0
        else:
            w_top = (b1 * s_cc - b2 * s_ac) / det
            w_right = (s_aa * b2 - s_ac * b1) / det
        w_top = max(0.0, w_top)
        w_right = max(0.0, w_right)
        total = w_top + w_right
        if total > 1.0:
            w_top /= total
            w_right /= total
        w_left = 1.0 - w_top - w_right
        return QPointF(
            w_top * top.x() + w_left * bottom_left.x() + w_right * bottom_right.x(),
            w_top * top.y() + w_left * bottom_left.y() + w_right * bottom_right.y(),
        )

    def resizeEvent(self, event):
        bottom = max(0, self.height() - self.color_code_edit.height() - 2)
        copy_x = max(0, self.width() - self.color_copy_button.width() - 2)
        self.color_copy_button.move(copy_x, bottom)
        self.color_code_edit.move(
            max(0, copy_x - self.color_code_edit.width() - 2),
            bottom,
        )
        self._cache_key = None
        super().resizeEvent(event)

    def _wheel_image(self):
        hue_bar, square = self._wheel_geometry()
        width, height = self.width(), self.height()
        hue = max(0, self._color.hsvHue())
        cache_key = (width, height, hue, self._mode, self._hue_mode)
        if self._cache_key == cache_key:
            return self._cache_image
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        if self._mode == "HLS":
            top, bottom_left, bottom_right = self._triangle_vertices()
        if self._hue_mode == "RING":
            ring_center, ring_outer, ring_inner = self._ring_metrics()
        for y in range(height):
            for x in range(width):
                point = QPointF(x + 0.5, y + 0.5)
                if self._hue_mode == "RING":
                    distance = math.hypot(
                        point.x() - ring_center.x(),
                        point.y() - ring_center.y(),
                    )
                    if ring_inner <= distance <= ring_outer:
                        image.setPixelColor(
                            x, y, QColor.fromHsv(
                                self._ring_hue(ring_center, point), 255, 255
                            )
                        )
                        continue
                elif hue_bar.contains(point):
                    bar_hue = int(round(
                        359.0
                        * (x + 0.5 - hue_bar.left())
                        / hue_bar.width()
                    ))
                    image.setPixelColor(
                        x, y, QColor.fromHsv(bar_hue, 255, 255)
                    )
                    continue
                if self._mode == "HLS":
                    if not square.contains(x + 0.5, y + 0.5):
                        continue
                    w_top, w_left, w_right = self._barycentric(
                        QPointF(x + 0.5, y + 0.5),
                        top,
                        bottom_left,
                        bottom_right,
                    )
                    if w_top < 0 or w_left < 0 or w_right < 0:
                        continue
                    image.setPixelColor(
                        x, y, self._triangle_color(w_top, w_left, w_right)
                    )
                elif square.contains(point):
                    saturation = int(round(
                        255.0 * (x + 0.5 - square.left()) / square.width()
                    ))
                    value = int(round(
                        255.0 * (square.bottom() - (y + 0.5)) / square.height()
                    ))
                    image.setPixelColor(
                        x,
                        y,
                        QColor.fromHsv(
                            hue,
                            max(0, min(255, saturation)),
                            max(0, min(255, value)),
                        ),
                    )
        self._cache_key = cache_key
        self._cache_image = image
        return image

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.drawImage(0, 0, self._wheel_image())
        hue_bar, square = self._wheel_geometry()
        hue = max(0, self._color.hsvHue())
        hue_x = (
            hue_bar.left()
            + hue_bar.width() * hue / 359.0
        )
        if self._mode == "HLS":
            marker_point = self._triangle_point_for_color()
        else:
            marker_point = QPointF(
                square.left()
                + square.width() * self._color.hsvSaturation() / 255.0,
                square.bottom()
                - square.height() * self._color.value() / 255.0,
            )
        painter.setPen(QPen(QColor("#555555"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._hue_mode == "RING":
            ring_center, ring_outer, ring_inner = self._ring_metrics()
            painter.drawEllipse(hue_bar)
            painter.drawEllipse(ring_center, ring_inner, ring_inner)
        else:
            painter.drawRect(hue_bar.adjusted(-1, -1, 1, 1))
        if self._mode == "HLS":
            top, bottom_left, bottom_right = self._triangle_vertices()
            painter.drawPolygon(QPolygonF([top, bottom_left, bottom_right]))
        else:
            painter.drawRect(square.adjusted(-1, -1, 1, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("white"), 2))
        if self._hue_mode == "RING":
            angle = math.radians(hue)
            marker_radius = (ring_outer + ring_inner) / 2.0  # pyright: ignore[reportOperatorIssue]
            hue_point = QPointF(
                ring_center.x() + math.sin(angle) * marker_radius,
                ring_center.y() - math.cos(angle) * marker_radius,
            )
            painter.drawEllipse(hue_point, 4, 4)
        else:
            painter.drawRect(QRectF(
                hue_x - 3.0,
                hue_bar.top() - 2.0,
                6.0,
                hue_bar.height() + 4.0,
            ))
        painter.drawEllipse(marker_point, 5, 5)
        painter.setPen(QPen(QColor("black"), 1))
        if self._hue_mode == "RING":
            painter.drawEllipse(hue_point, 5, 5)
        else:
            painter.drawRect(QRectF(
                hue_x - 4.0,
                hue_bar.top() - 3.0,
                8.0,
                hue_bar.height() + 6.0,
            ))
        painter.drawEllipse(marker_point, 6, 6)
        if self._hue_mode != "RING":
            painter.setPen(QColor("#333333"))
            painter.drawText(
                QRectF(0, 4, 18, 22),
                Qt.AlignmentFlag.AlignCenter,
                "H",
            )

    def _part_at(self, position):
        hue_bar, square = self._wheel_geometry()
        if self._hue_mode == "RING":
            center, outer_radius, inner_radius = self._ring_metrics()
            distance = math.hypot(
                position.x() - center.x(), position.y() - center.y()
            )
            if inner_radius - 3.0 <= distance <= outer_radius + 3.0:
                return "hue"
            if square.contains(position):
                return "sv"
            return None
        if hue_bar.adjusted(-3, -4, 3, 4).contains(position):
            return "hue"
        if square.contains(position):
            return "sv"
        return None

    def _select_triangle(self, position):
        top, bottom_left, bottom_right = self._triangle_vertices()
        w_top, w_left, w_right = self._barycentric(
            position, top, bottom_left, bottom_right
        )
        # 三角形の外をドラッグしても近い辺へ射影する。
        w_top = max(0.0, w_top)
        w_left = max(0.0, w_left)
        w_right = max(0.0, w_right)
        total = w_top + w_left + w_right
        if total <= 1e-9:
            return
        w_top, w_left, w_right = w_top / total, w_left / total, w_right / total
        self._color = self._triangle_color(w_top, w_left, w_right)
        self._refresh_color_code()
        self.update()
        self.colorChanged.emit(QColor(self._color))

    def _select_at(self, position, part=None):
        if not self.isEnabled():
            return
        hue_bar, square = self._wheel_geometry()
        part = part or self._part_at(position)
        if part == "sv" and self._mode == "HLS":
            self._select_triangle(position)
            return
        if part == "hue":
            if self._hue_mode == "RING":
                hue = self._ring_hue(hue_bar.center(), position)
            else:
                hue = int(round(
                    359.0
                    * (position.x() - hue_bar.left())
                    / hue_bar.width()
                ))
            hue = max(0, min(359, hue))
            saturation = self._color.hsvSaturation()
            value = self._color.value() or 255
        elif part == "sv":
            hue = max(0, self._color.hsvHue())
            saturation = int(round(
                255.0
                * (position.x() - square.left())
                / square.width()
            ))
            value = int(round(
                255.0
                * (square.bottom() - position.y())
                / square.height()
            ))
            saturation = max(0, min(255, saturation))
            value = max(0, min(255, value))
        else:
            return
        self._color = QColor.fromHsv(hue, saturation, value)
        if part == "hue":
            self._cache_key = None
        self._refresh_color_code()
        self.update()
        self.colorChanged.emit(QColor(self._color))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_part = self._part_at(event.position())
            if self._drag_part is not None:
                self._select_at(event.position(), self._drag_part)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            if self._drag_part is not None:
                self._select_at(event.position(), self._drag_part)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_part = None
        super().mouseReleaseEvent(event)


class TimeRemapPasteDialog(QDialog):
    """コピー情報をタイムシート表へ貼り付けて確認する画面。"""

    def __init__(self, initial_text="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("タイムリマップをタイムシートへ貼り付け")
        self.resize(760, 650)
        self._parsed_preview = None
        self._parsed_source: dict[str, Any] | None = None
        self._parsed_raw_text = None
        self._included_rows = []
        self._column_layer_bindings = {}
        self._updating_preview_items = False
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        header = QHBoxLayout()
        title = QLabel("<b>読み込みデータ</b>")
        paste_button = QPushButton(
            "クリップボードをタイムシートへ貼付"
        )
        xdts_button = QPushButton("XDTSを読み込む…")
        clear_button = QPushButton("クリア")
        paste_button.clicked.connect(self._paste_clipboard)
        xdts_button.clicked.connect(self._load_xdts_file)
        clear_button.clicked.connect(self.text_clear)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(xdts_button)
        header.addWidget(paste_button)
        header.addWidget(clear_button)
        layout.addLayout(header)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "After Effects／ToeiDigitalTimeSheetのコピー情報を貼り付けるか、"
            "XDTSファイルを読み込みます。XDTSはこの画面へドロップできます。"
        )
        self.text_edit.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.NoWrap
        )
        self.text_edit.setMaximumHeight(150)
        # ファイルドロップはダイアログ全体で一貫して処理する。
        self.text_edit.setAcceptDrops(False)
        layout.addWidget(self.text_edit)

        sheet_header = QHBoxLayout()
        sheet_title = QLabel("<b>タイムシートプレビュー</b>")
        self.preview_status = QLabel("データ待機中")
        self.preview_status.setAlignment(
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter
        )
        sheet_header.addWidget(sheet_title)
        sheet_header.addStretch(1)
        sheet_header.addWidget(self.preview_status)
        layout.addLayout(sheet_header)

        self.preview_table = QTableWidget(0, 2)
        self.preview_table.setHorizontalHeaderLabels(["使用", "F"])
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.preview_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setShowGrid(True)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self.preview_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Fixed
        )
        self.preview_table.setColumnWidth(0, 48)
        self.preview_table.setColumnWidth(1, 60)
        self.preview_table.horizontalHeader().setMinimumHeight(58)
        self.preview_table.horizontalHeader().setSectionsClickable(True)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.horizontalHeader().sectionClicked.connect(
            self._preview_header_clicked
        )
        self.preview_table.verticalHeader().setDefaultSectionSize(22)
        self.preview_table.setAcceptDrops(False)
        self.preview_table.viewport().setAcceptDrops(False)
        c = theme.palette()
        self.preview_table.setStyleSheet(
            "QTableWidget{gridline-color:%s;background:%s;"
            "alternate-background-color:%s;}"
            "QHeaderView::section{background:%s;padding:3px;"
            "border:1px solid %s;}"
            "QTableWidget::item:selected{background:%s;color:%s;}"
            % (c["border"], c["surface"], c["surface_alt"], c["surface_alt"],
               c["border"], c["accent"], c["accent_text"])
        )
        self.preview_table.itemChanged.connect(
            self._preview_item_changed
        )
        layout.addWidget(self.preview_table, 1)

        filter_row = QHBoxLayout()
        exclude_button = QPushButton("選択行を除外")
        include_button = QPushButton("選択行を使用")
        include_all_button = QPushButton("全て使用")
        exclude_button.clicked.connect(
            lambda: self._set_selected_rows_included(False)
        )
        include_button.clicked.connect(
            lambda: self._set_selected_rows_included(True)
        )
        include_all_button.clicked.connect(self._include_all_rows)
        filter_row.addWidget(exclude_button)
        filter_row.addWidget(include_button)
        filter_row.addWidget(include_all_button)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        note = QLabel(
            "ACTION・CELL・CAMをXDTSの列ごとに表示します。"
            "CELL名をクリックするとPaintMaskAnimatorのレイヤーへ紐づけできます。"
            "「使用」を外した行は取り除き、後続を詰めて反映します。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size:10px;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(
            QDialogButtonBox.StandardButton.Ok
        ).setText("このタイムシートをタイムラインへ反映")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(140)
        self._preview_timer.timeout.connect(
            self._update_timesheet_preview
        )
        self.text_edit.textChanged.connect(
            self._preview_timer.start
        )
        self.text_edit.setPlainText(str(initial_text or ""))
        QTimer.singleShot(0, self._update_timesheet_preview)

    def _paste_clipboard(self):
        self.text_edit.setPlainText(
            QApplication.clipboard().text()
        )
        self.text_edit.setFocus()

    @staticmethod
    def _dropped_xdts_path(mime_data):
        if mime_data is None or not mime_data.hasUrls():
            return None
        for url in mime_data.urls():
            path = str(url.toLocalFile() or "")
            if Path(path).suffix.lower() in (".xdts", ".xtds"):
                return path
        return None

    def dragEnterEvent(self, event):
        if self._dropped_xdts_path(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._dropped_xdts_path(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        path = self._dropped_xdts_path(event.mimeData())
        if path:
            self._load_xdts_path(path)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _load_xdts_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "XDTSタイムシートを読み込む",
            "",
            "XDTSタイムシート (*.xdts *.xtds);;すべてのファイル (*)",
        )
        if not path:
            return
        self._load_xdts_path(path)

    def _load_xdts_path(self, path):
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(
                self, "XDTS読み込み", f"読み込めませんでした。\n\n{exc}"
            )
            return False
        self.text_edit.setPlainText(text)
        self.text_edit.setFocus()
        self._update_timesheet_preview()
        return True

    def text_clear(self):
        self.text_edit.clear()
        self.preview_table.setRowCount(0)
        self.preview_table.setColumnCount(2)
        self.preview_table.setHorizontalHeaderLabels(["使用", "F"])
        self._parsed_preview = None
        self._parsed_source = None
        self._parsed_raw_text = None
        self._included_rows.clear()
        self._column_layer_bindings.clear()
        self.preview_status.setText("データ待機中")
        self.preview_status.setStyleSheet("color:palette(placeholder-text);")

    def _parser_owner(self):
        owner = self.parent()
        return owner if owner is not None else None

    @staticmethod
    def _list_value(value):
        """Normalize an untrusted parser field to a plain list."""
        return list(value) if isinstance(value, (list, tuple)) else []

    @classmethod
    def _mapping_list(cls, value):
        """Keep only mapping-shaped entries from an untrusted parser field."""
        return [dict(item) for item in cls._list_value(value) if isinstance(item, dict)]

    def _sheet_columns(self):
        if self._parsed_source is None:
            return []
        columns = self._mapping_list(self._parsed_source.get("sheet_columns"))
        if columns:
            return columns
        tracks = self._mapping_list(self._parsed_source.get("tracks"))
        if tracks:
            return [
                dict(
                    track,
                    uid=f"cell:{int(track.get('track_no', index))}",
                    field_id=0,
                    group="CELL",
                    bindable=True,
                    display_values=[
                        "×" if state is None else str(int(state))
                        for state in self._list_value(track.get("states"))
                    ],
                )
                for index, track in enumerate(tracks)
            ]
        states = self._list_value(self._parsed_source.get("states"))
        if not states:
            return []
        return [{
            "uid": "cell:0",
            "field_id": 0,
            "track_no": 0,
            "group": "CELL",
            "name": str(self._parsed_source.get("track_name", "セル")),
            "states": states,
            "display_values": [
                "×" if state is None else str(int(state))
                for state in states
            ],
            "bindable": True,
            "blank_label_count": int(
                self._parsed_source.get("blank_label_count", 0)
            ),
        }]

    @staticmethod
    def _column_uid(column):
        return str(column.get("uid", ""))

    def _available_layers(self):
        owner = self._parser_owner()
        canvas = getattr(owner, "canvas", None)
        if canvas is None:
            return []
        frames = getattr(canvas, "frames", [])
        if not frames:
            return []
        frame_index = max(
            0, min(int(getattr(canvas, "current_frame", 0)), len(frames) - 1)
        )
        return [
            (index, str(layer.name))
            for index, layer in enumerate(frames[frame_index].layers)
        ]

    def _initialize_layer_bindings(self):
        self._column_layer_bindings.clear()
        layers = self._available_layers()
        bindable = [
            column for column in self._sheet_columns()
            if bool(column.get("bindable", False))
        ]
        used_layers = set()
        for column in bindable:
            name = str(column.get("name", "")).strip().casefold()
            match = next(
                (
                    index for index, layer_name in layers
                    if index not in used_layers
                    and layer_name.strip().casefold() == name
                ),
                None,
            )
            if match is not None:
                self._column_layer_bindings[
                    self._column_uid(column)
                ] = match
                used_layers.add(match)
        if not self._column_layer_bindings and bindable and layers:
            owner = self._parser_owner()
            canvas = getattr(owner, "canvas", None)
            active = int(getattr(canvas, "active_layer_index", 0))
            if any(index == active for index, _name in layers):
                self._column_layer_bindings[
                    self._column_uid(bindable[0])
                ] = active

    def _save_current_included_rows(self):
        if self.preview_table.rowCount() <= 0:
            return
        self._included_rows = []
        for row in range(self.preview_table.rowCount()):
            item = self.preview_table.item(row, 0)
            self._included_rows.append(
                item is not None
                and item.checkState() == Qt.CheckState.Checked
            )

    def _set_column_layer_binding(self, uid, layer_index):
        uid = str(uid)
        if layer_index is None:
            self._column_layer_bindings.pop(uid, None)
        else:
            layer_index = int(layer_index)
            for other_uid, other_index in list(
                self._column_layer_bindings.items()
            ):
                if other_uid != uid and other_index == layer_index:
                    self._column_layer_bindings.pop(other_uid, None)
            self._column_layer_bindings[uid] = layer_index
        self._update_preview_headers()
        self._update_preview_status()

    def _preview_header_clicked(self, section):
        column_index = int(section) - 2
        columns = self._sheet_columns()
        if not (0 <= column_index < len(columns)):
            return
        column = columns[column_index]
        if not bool(column.get("bindable", False)):
            return
        uid = self._column_uid(column)
        current = self._column_layer_bindings.get(uid)
        menu = QMenu(self)
        title = menu.addAction(
            f"「{column.get('name', 'セル')}」の紐づけ先"
        )
        title.setEnabled(False)
        menu.addSeparator()
        clear_action = menu.addAction("紐づけを解除（読み込まない）")
        clear_action.setCheckable(True)
        clear_action.setChecked(current is None)
        clear_action.triggered.connect(
            lambda _checked=False, key=uid:
            self._set_column_layer_binding(key, None)
        )
        layers = self._available_layers()
        if layers:
            menu.addSeparator()
        for layer_index, layer_name in layers:
            action = menu.addAction(layer_name)
            action.setCheckable(True)
            action.setChecked(current == layer_index)
            action.triggered.connect(
                lambda _checked=False, key=uid, index=layer_index:
                self._set_column_layer_binding(key, index)
            )
        if not layers:
            unavailable = menu.addAction("紐づけ可能なレイヤーがありません")
            unavailable.setEnabled(False)
        menu.exec(QCursor.pos())

    def _update_preview_headers(self):
        columns = self._sheet_columns()
        layers = dict(self._available_layers())
        labels = ["使用", "F"]
        for column in columns:
            group = str(column.get("group", "CELL"))
            name = str(column.get("name", ""))
            uid = self._column_uid(column)
            layer_index = self._column_layer_bindings.get(uid)
            if bool(column.get("bindable", False)):
                linked = layers.get(layer_index) if layer_index is not None else None
                link_text = f"→ {linked}" if linked else "クリックで紐づけ"
                labels.append(f"{group}\n{name}\n{link_text}")
            else:
                labels.append(f"{group}\n{name}")
        self.preview_table.setHorizontalHeaderLabels(labels)
        colors = {
            "ACTION": QColor("#e5edf7"),
            "CELL": QColor("#f7dddd"),
            "CAM": QColor("#dff3df"),
        }
        for index, column in enumerate(columns, 2):
            item = self.preview_table.horizontalHeaderItem(index)
            if item is None:
                continue
            group = str(column.get("group", "CELL"))
            item.setBackground(colors.get(group, QColor("#e7ecef")))
            if bool(column.get("bindable", False)):
                item.setToolTip(
                    "クリックして読み込み先レイヤーを選択します。"
                )

    def _set_selected_rows_included(self, included):
        rows = sorted({index.row() for index in self.preview_table.selectedIndexes()})
        if not rows:
            row = self.preview_table.currentRow()
            rows = [row] if row >= 0 else []
        state = Qt.CheckState.Checked if included else Qt.CheckState.Unchecked
        for row in rows:
            item = self.preview_table.item(row, 0)
            if item is not None:
                item.setCheckState(state)

    def _include_all_rows(self):
        for row in range(self.preview_table.rowCount()):
            item = self.preview_table.item(row, 0)
            if item is not None:
                item.setCheckState(Qt.CheckState.Checked)

    def _preview_item_changed(self, item):
        if self._updating_preview_items or item.column() != 0:
            return
        self._save_current_included_rows()
        self._update_preview_status()

    def _update_preview_status(self):
        source = self._parsed_source
        if source is None:
            return
        columns = self._sheet_columns()
        if not columns:
            return
        duration = max(
            [len(self._list_value(column.get("display_values"))) for column in columns]
            + [len(self._included_rows)]
        )
        included = list(self._included_rows)
        if len(included) != duration:
            included = [True] * duration
        used_count = sum(bool(value) for value in included)
        linked_count = sum(
            1 for column in columns
            if self._column_uid(column) in self._column_layer_bindings
        )
        format_name = str(
            source.get("format", "タイムリマップ")
        )
        self.preview_status.setText(
            f"{format_name}／使用 {used_count}／"
            f"除外 {max(0, duration - used_count)}／"
            f"レイヤー紐づけ {linked_count}"
        )
        self.preview_status.setStyleSheet("color:#176b42;")

    def _populate_preview_table(self):
        source = self._parsed_source
        if source is None:
            return
        columns = self._sheet_columns()
        if not columns:
            return
        duration = max(
            len(self._list_value(column.get("display_values"))) for column in columns
        )
        start_frame = int(source.get("start_frame", 0))
        if len(self._included_rows) != duration:
            self._included_rows = [True] * duration

        self._updating_preview_items = True
        self.preview_table.setUpdatesEnabled(False)
        self.preview_table.blockSignals(True)
        try:
            self.preview_table.setColumnCount(2 + len(columns))
            self.preview_table.setRowCount(duration)
            self.preview_table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.ResizeMode.Fixed
            )
            self.preview_table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.Fixed
            )
            for column_index in range(2, 2 + len(columns)):
                self.preview_table.horizontalHeader().setSectionResizeMode(
                    column_index, QHeaderView.ResizeMode.Interactive
                )
                self.preview_table.setColumnWidth(column_index, 112)
            self._update_preview_headers()
            for row in range(duration):
                use_item = QTableWidgetItem()
                use_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                use_item.setCheckState(
                    Qt.CheckState.Checked
                    if self._included_rows[row]
                    else Qt.CheckState.Unchecked
                )
                frame_item = QTableWidgetItem(str(start_frame + row + 1))
                for item in (use_item, frame_item):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.preview_table.setItem(row, 0, use_item)
                self.preview_table.setItem(row, 1, frame_item)
                for offset, column in enumerate(columns, 2):
                    values = self._list_value(column.get("display_values"))
                    value = values[row] if row < len(values) else ""
                    cell_item = QTableWidgetItem(str(value))
                    cell_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if str(value) in ("×", "○", "●"):
                        cell_item.setForeground(QColor("#777777"))
                    self.preview_table.setItem(row, offset, cell_item)
                if not self._included_rows[row]:
                    for column_index in range(1, 2 + len(columns)):
                        item = self.preview_table.item(row, column_index)
                        if item is not None:
                            item.setForeground(QColor("#999999"))
                            item.setBackground(QColor("#eeeeee"))
        finally:
            self.preview_table.blockSignals(False)
            self.preview_table.setUpdatesEnabled(True)
            self._updating_preview_items = False
        self._update_preview_status()

    def _update_timesheet_preview(self):
        raw_text = self.text_edit.toPlainText()
        if not raw_text.strip():
            self.text_clear()
            return
        if (
            self._parsed_source is not None
            and raw_text == self._parsed_raw_text
        ):
            return

        owner = self._parser_owner()
        parser = getattr(owner, "parse_time_remap_text", None)
        if not callable(parser):
            self.preview_status.setText("解析機能を取得できません")
            self.preview_status.setStyleSheet("color:#b00020;")
            return

        try:
            parsed = parser(raw_text)
            if not isinstance(parsed, dict):
                raise TypeError("タイムリマップの解析結果は辞書である必要があります。")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            # Malformed clipboard/timesheet data is expected here; show the
            # first line of the error in the preview status and log details.
            log.info("time-remap preview parse failed: %s", exc)
            self._parsed_preview = None
            self._parsed_source = None
            self._parsed_raw_text = None
            self._included_rows.clear()
            self._column_layer_bindings.clear()
            self.preview_table.setRowCount(0)
            message = str(exc).splitlines()[0]
            self.preview_status.setText(message)
            self.preview_status.setStyleSheet("color:#b00020;")
            return
        self._parsed_source = parsed
        self._parsed_raw_text = raw_text
        self._parsed_preview = parsed
        columns = self._sheet_columns()
        duration = max(
            [len(self._list_value(column.get("display_values"))) for column in columns]
            + [0]
        )
        self._included_rows = [True] * duration
        self._initialize_layer_bindings()
        self._populate_preview_table()

    def parsed_result(self):
        if self._parsed_source is None:
            return None
        self._save_current_included_rows()
        columns = self._sheet_columns()
        if not columns:
            return None
        if not any(self._included_rows):
            return None
        result = dict(self._parsed_source)
        filtered_columns = []
        for column in columns:
            filtered_column = dict(column)
            states = self._list_value(column.get("states"))
            values = self._list_value(column.get("display_values"))
            if states:
                filtered_column["states"] = [
                    state for state, keep in zip(states, self._included_rows)
                    if keep
                ]
            filtered_column["display_values"] = [
                value for value, keep in zip(values, self._included_rows)
                if keep
            ]
            filtered_columns.append(filtered_column)
        result["sheet_columns"] = filtered_columns
        result["layer_bindings"] = dict(self._column_layer_bindings)
        primary = next(
            (
                column for column in filtered_columns
                if self._column_uid(column) in self._column_layer_bindings
                and column.get("states")
            ),
            next(
                (
                    column for column in filtered_columns
                    if column.get("states")
                ),
                None,
            ),
        )
        if primary is not None:
            result["states"] = list(primary.get("states", []))
            result["blank_label_count"] = int(
                primary.get("blank_label_count", 0)
            )
            result["track_name"] = str(primary.get("name", "セル欄"))
        start_frame = int(self._parsed_source.get("start_frame", 0))
        result["start_frame"] = start_frame
        used_count = sum(bool(value) for value in self._included_rows)
        result["end_frame"] = start_frame + used_count - 1
        return result

    def _accept_if_valid(self):
        self._update_timesheet_preview()
        self._parsed_preview = self.parsed_result()
        if self._parsed_preview is None:
            QMessageBox.warning(
                self,
                "タイムリマップ",
                "有効なタイムシートを貼り付け、使用する行を残してください。",
            )
            return
        if (
            self._parsed_preview.get("format", "").startswith("XDTS")
            and not self._parsed_preview.get("layer_bindings")
        ):
            QMessageBox.warning(
                self,
                "XDTS読み込み",
                "読み込むCELL名をクリックし、紐づけ先レイヤーを選択してください。",
            )
            return
        self.accept()
