"""After Effects 風のカラーピッカー。

OS 標準の色ダイアログ（macOS では NSColorPanel）は、絵を塗る作業で色を
決めるには遠回りだった。ここでは AE のカラーピッカーに寄せて、

- 左に大きな彩度×明度の面、その右に縦の色相バー
- 右上に「新しい色／元の色」の見本（元の色をクリックすると戻る）
- H・S・B と R・G・B の数値欄、16進のカラーコード欄
- 画面スポイト
- OK／キャンセル

を1枚にまとめる。内部では色相を HSV の浮動小数で持つので、彩度や明度を
0 にしても色相を失わない（QColor は無彩色の色相を -1 にしてしまう）。

:meth:`ColorPickerDialog.get_color` は ``QColorDialog.getColor`` と同じ使い方で、
キャンセルされたら無効な ``QColor`` を返す。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .i18n import tr

_FIELD_SIZE = 256


def _clamp(value, low, high):
    return max(low, min(high, value))


def _hsv_of(color):
    """``(色相 0..1、無彩色は -1, 彩度 0..1, 明度 0..1)``。"""
    return color.hsvHueF(), color.hsvSaturationF(), color.valueF()


class _SaturationValueField(QWidget):
    """横が彩度、縦が明度（上ほど明るい）の面。"""

    changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue = 0.0
        self._saturation = 1.0
        self._value = 1.0
        self._cache_hue = None
        self._cache = QImage()
        self.setFixedSize(_FIELD_SIZE, _FIELD_SIZE)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_hsv(self, hue, saturation, value):
        self._hue, self._saturation, self._value = hue, saturation, value
        self.update()

    def _field_image(self):
        hue = round(self._hue, 1)
        if self._cache_hue == hue and not self._cache.isNull():
            return self._cache
        image = QImage(self.width(), self.height(), QImage.Format.Format_RGB32)
        painter = QPainter(image)
        across = QLinearGradient(0, 0, self.width(), 0)
        across.setColorAt(0.0, QColor("white"))
        across.setColorAt(1.0, QColor.fromHsvF((hue % 360.0) / 360.0, 1.0, 1.0))
        painter.fillRect(image.rect(), across)
        down = QLinearGradient(0, 0, 0, self.height())
        down.setColorAt(0.0, QColor(0, 0, 0, 0))
        down.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.fillRect(image.rect(), down)
        painter.end()
        self._cache_hue = hue
        self._cache = image
        return image

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.drawImage(0, 0, self._field_image())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        point = QPointF(
            self._saturation * (self.width() - 1),
            (1.0 - self._value) * (self.height() - 1),
        )
        ring = QColor("black") if self._value > 0.6 and self._saturation < 0.4 else QColor("white")
        painter.setPen(QPen(ring, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(point, 5, 5)

    def _emit_at(self, position):
        saturation = _clamp(position.x() / max(1, self.width() - 1), 0.0, 1.0)
        value = 1.0 - _clamp(position.y() / max(1, self.height() - 1), 0.0, 1.0)
        self._saturation, self._value = saturation, value
        self.update()
        self.changed.emit(saturation, value)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._emit_at(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._emit_at(event.position())


class _HueStrip(QWidget):
    """縦の色相バー。上が 360°、下が 0°（どちらも赤）。"""

    changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue = 0.0
        self.setFixedSize(28, _FIELD_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_hue(self, hue):
        self._hue = hue
        self.update()

    def _bar_rect(self):
        return QRectF(7, 0, self.width() - 14, self.height())

    def paintEvent(self, _event):
        painter = QPainter(self)
        bar = self._bar_rect()
        gradient = QLinearGradient(0, bar.top(), 0, bar.bottom())
        for step in range(7):
            stop = step / 6.0
            gradient.setColorAt(stop, QColor.fromHsvF((1.0 - stop) % 1.0, 1.0, 1.0))
        painter.fillRect(bar, gradient)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        y = (1.0 - self._hue / 360.0) * (self.height() - 1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().windowText())
        for left, direction in ((0.0, 1.0), (float(self.width()), -1.0)):
            painter.drawPolygon([
                QPointF(left, y - 5),
                QPointF(left + 6 * direction, y),
                QPointF(left, y + 5),
            ])

    def _emit_at(self, position):
        hue = (1.0 - _clamp(position.y() / max(1, self.height() - 1), 0.0, 1.0)) * 360.0
        self._hue = hue
        self.update()
        self.changed.emit(hue)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._emit_at(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._emit_at(event.position())


class _Swatch(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(QSize(64, 30))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_color(self, color):
        self.setStyleSheet(
            f"QPushButton{{background:{QColor(color).name()};"
            "border:1px solid #555;border-radius:0;padding:0;}"
        )


class ColorPickerDialog(QDialog):
    """AE 風のカラーピッカー。``colorChanged`` は編集のたびに出る。"""

    colorChanged = Signal(QColor)

    def __init__(self, color=None, parent=None, title=None, preview=None):
        """``preview`` を渡すと「プレビュー」チェックボックスを出す。

        チェックが入っている間は、編集のたびに ``preview(色)`` を呼んで
        その場に反映する。キャンセルしたら ``preview(元の色)`` で戻し、
        チェックを外して OK したときは ``preview(決めた色)`` を1回だけ呼ぶ。
        """
        super().__init__(parent)
        self._preview = preview
        self._previewed = False
        initial = QColor(color) if color is not None else QColor("white")
        if not initial.isValid():
            initial = QColor("white")
        self._original = QColor(initial)
        hue, saturation, value = _hsv_of(initial)
        self._hue = 0.0 if hue < 0 else hue * 360.0
        self._saturation = saturation
        self._value = value
        # RGB で決まった色（数値入力・カラーコード・スポイトなど）はその値を
        # そのまま返す。HSV を経由すると 8bit の値が1ずれることがあるため。
        self._exact = QColor(initial.toRgb())
        self._updating = False
        self._screen_picker = None
        self.setWindowTitle(title or tr("色を選択"))

        self.field = _SaturationValueField()
        self.hue_strip = _HueStrip()
        self.field.changed.connect(self._field_changed)
        self.hue_strip.changed.connect(self._hue_changed)

        self.new_swatch = _Swatch()
        self.new_swatch.setToolTip(tr("新しい色"))
        self.original_swatch = _Swatch()
        self.original_swatch.setToolTip(tr("元の色（クリックで戻す）"))
        self.original_swatch.set_color(self._original)
        self.original_swatch.clicked.connect(
            lambda: self.set_color(self._original)
        )
        swatches = QVBoxLayout()
        swatches.setSpacing(0)
        swatches.addWidget(self.new_swatch)
        swatches.addWidget(self.original_swatch)

        self.eyedropper = QPushButton(tr("スポイト"))
        self.eyedropper.setToolTip(tr("画面上の色を拾う（Esc でやめる）"))
        self.eyedropper.clicked.connect(self._begin_screen_pick)

        top_right = QHBoxLayout()
        top_right.addLayout(swatches)
        top_right.addStretch(1)
        top_right.addWidget(self.eyedropper, 0, Qt.AlignmentFlag.AlignTop)

        self.h_spin = self._spin(0, 360, "°")
        self.s_spin = self._spin(0, 100, "%")
        self.b_spin = self._spin(0, 100, "%")
        self.r_spin = self._spin(0, 255)
        self.g_spin = self._spin(0, 255)
        self.blue_spin = self._spin(0, 255)
        for spin in (self.h_spin, self.s_spin, self.b_spin):
            spin.valueChanged.connect(self._hsb_edited)
        for spin in (self.r_spin, self.g_spin, self.blue_spin):
            spin.valueChanged.connect(self._rgb_edited)
        self.hex_edit = QLineEdit()
        self.hex_edit.setMaxLength(7)
        self.hex_edit.setFixedWidth(84)
        self.hex_edit.setToolTip(tr("カラーコードを入力して Enter"))
        self.hex_edit.editingFinished.connect(self._hex_edited)

        numbers = QGridLayout()
        numbers.setHorizontalSpacing(6)
        numbers.setVerticalSpacing(3)
        for row, (label, spin) in enumerate((
            ("H", self.h_spin), ("S", self.s_spin), ("B", self.b_spin),
        )):
            numbers.addWidget(QLabel(label), row, 0)
            numbers.addWidget(spin, row, 1)
        for row, (label, spin) in enumerate((
            ("R", self.r_spin), ("G", self.g_spin), ("B", self.blue_spin),
        )):
            numbers.addWidget(QLabel(label), row, 2)
            numbers.addWidget(spin, row, 3)
        numbers.addWidget(QLabel("#"), 3, 0)
        numbers.addWidget(self.hex_edit, 3, 1, 1, 3, Qt.AlignmentFlag.AlignLeft)

        side = QVBoxLayout()
        side.addLayout(top_right)
        side.addSpacing(10)
        side.addLayout(numbers)
        side.addStretch(1)

        body = QHBoxLayout()
        body.setSpacing(8)
        body.addWidget(self.field)
        body.addWidget(self.hue_strip)
        body.addSpacing(6)
        body.addLayout(side)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        self.preview_check = QCheckBox(tr("プレビュー"))
        self.preview_check.setToolTip(
            tr("ON：選んでいる色をその場で反映します（キャンセルで元に戻ります）。")
        )
        self.preview_check.setChecked(True)
        self.preview_check.setVisible(preview is not None)
        self.preview_check.toggled.connect(self._preview_toggled)
        bottom = QHBoxLayout()
        bottom.addWidget(self.preview_check)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.addLayout(body)
        layout.addLayout(bottom)
        self._sync_widgets()

    # ------------------------------------------------------------------
    @staticmethod
    def get_color(initial=None, parent=None, title=None, preview=None):
        """``QColorDialog.getColor`` の置き換え。キャンセルなら無効な色を返す。

        ``preview`` は「プレビュー」チェックボックス用（:meth:`__init__` を参照）。
        """
        dialog = ColorPickerDialog(initial, parent, title, preview)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        color = dialog.color()
        dialog.deleteLater()
        return color if accepted else QColor()

    def color(self):
        if self._exact is not None:
            return QColor(self._exact)
        return QColor.fromHsvF(
            (self._hue % 360.0) / 360.0, self._saturation, self._value
        ).toRgb()

    def original_color(self):
        return QColor(self._original)

    def set_color(self, color):
        color = QColor(color)
        if not color.isValid():
            return
        hue, saturation, value = _hsv_of(color)
        if hue >= 0:
            self._hue = hue * 360.0
        self._saturation, self._value = saturation, value
        self._exact = QColor(color.toRgb())
        self._changed()

    # ------------------------------------------------------------------
    @staticmethod
    def _spin(low, high, suffix=""):
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setSuffix(suffix)
        spin.setFixedWidth(68)
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        return spin

    def _field_changed(self, saturation, value):
        self._saturation, self._value = saturation, value
        self._exact = None
        self._changed()

    def _hue_changed(self, hue):
        self._hue = hue
        self._exact = None
        self._changed()

    def _hsb_edited(self):
        if self._updating:
            return
        self._hue = float(self.h_spin.value() % 360)
        self._saturation = self.s_spin.value() / 100.0
        self._value = self.b_spin.value() / 100.0
        self._exact = None
        self._changed(keep=("hsb",))

    def _rgb_edited(self):
        if self._updating:
            return
        color = QColor(self.r_spin.value(), self.g_spin.value(), self.blue_spin.value())
        hue, saturation, value = _hsv_of(color)
        if hue >= 0:
            self._hue = hue * 360.0
        self._saturation, self._value = saturation, value
        self._exact = color
        self._changed(keep=("rgb",))

    def _hex_edited(self):
        text = self.hex_edit.text().strip()
        if not text.startswith("#"):
            text = "#" + text
        color = QColor(text)
        if color.isValid() and len(text) == 7:
            self.set_color(color)
        else:
            self._sync_widgets()

    def _changed(self, keep=()):
        self._sync_widgets(keep)
        self.colorChanged.emit(self.color())
        if self._preview is not None and self.preview_check.isChecked():
            self._apply_preview(self.color())

    def _apply_preview(self, color):
        if self._preview is None:
            return
        self._previewed = True
        self._preview(QColor(color))

    def _preview_toggled(self, checked):
        if self._preview is None:
            return
        if checked:
            self._apply_preview(self.color())
        elif self._previewed:
            # プレビューをやめたら、決めるまでは元の色に戻しておく。
            self._apply_preview(self._original)

    def accept(self):
        if self._preview is not None and (
            self._previewed or not self.preview_check.isChecked()
        ):
            self._apply_preview(self.color())
        super().accept()

    def _sync_widgets(self, keep=()):
        """数値欄・面・見本を今の色に合わせる。``keep`` は入力中の欄。"""
        color = self.color()
        self._updating = True
        try:
            if "hsb" not in keep:
                self.h_spin.setValue(int(round(self._hue)) % 360)
                self.s_spin.setValue(int(round(self._saturation * 100)))
                self.b_spin.setValue(int(round(self._value * 100)))
            if "rgb" not in keep:
                self.r_spin.setValue(color.red())
                self.g_spin.setValue(color.green())
                self.blue_spin.setValue(color.blue())
            self.hex_edit.setText(color.name(QColor.NameFormat.HexRgb).upper())
        finally:
            self._updating = False
        self.field.set_hsv(self._hue, self._saturation, self._value)
        self.hue_strip.set_hue(self._hue)
        self.new_swatch.set_color(color)

    # ------------------------------------------------------------------
    def _begin_screen_pick(self):
        from .utils import ScreenColorPickerOverlay

        if self._screen_picker is not None:
            return
        # モーダルな色選択ダイアログの子にしないと、Qt が入力を遮断する。
        overlay = ScreenColorPickerOverlay(self.color(), parent=self)
        self._screen_picker = overlay
        overlay.colorPicked.connect(self._screen_color_picked)
        overlay.canceled.connect(self._screen_pick_finished)
        overlay.start()

    def _screen_color_picked(self, color):
        self._screen_pick_finished()
        self.set_color(color)

    def _screen_pick_finished(self):
        self._screen_picker = None
        self.raise_()
        self.activateWindow()

    def done(self, result):
        overlay = self._screen_picker
        if overlay is not None:
            self._screen_picker = None
            overlay.cancel()
        super().done(result)

    def reject(self):
        overlay = self._screen_picker
        if overlay is not None:
            self._screen_picker = None
            overlay.cancel()
        if self._preview is not None and self._previewed:
            self._apply_preview(self._original)
        super().reject()
