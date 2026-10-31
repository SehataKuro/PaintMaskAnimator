"""使用色の色相・彩度・明度調整（Issue #22）。

使用色パネルで選んだ色をまとめてずらす。スライダーを動かすあいだはキャンバス
の表示だけを差し替えて見せ（``canvas.preview_color_remap``）、OKで初めて全コマ
へ適用する。元の絵は確定するまで変わらない。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
)

from .i18n import tr

#: スライダーの範囲。色相は一周、彩度・明度は±100%。
HUE_RANGE = (-180, 180)
SATURATION_RANGE = (-100, 100)
VALUE_RANGE = (-100, 100)


def shifted_color(rgb, hue, saturation, value):
    """``rgb`` を HSV 空間でずらした RGB タプルを返す。

    :param hue: 色相の回転（度）。一周を超えたぶんは回り込む。
    :param saturation: 彩度の増減（％）。
    :param value: 明度の増減（％）。
    """
    color = QColor(*(int(channel) for channel in rgb[:3]))
    current_hue = color.hue()
    # 無彩色（hue が -1）は色相を持たないので、回しても色は付かない。
    new_hue = (
        -1 if current_hue < 0
        else int((current_hue + int(hue)) % 360)
    )
    new_saturation = max(0, min(255, color.saturation() + round(saturation * 2.55)))
    new_value = max(0, min(255, color.value() + round(value * 2.55)))
    shifted = QColor.fromHsv(
        new_hue if new_hue >= 0 else 0,
        0 if new_hue < 0 else new_saturation,
        new_value,
    )
    return shifted.red(), shifted.green(), shifted.blue()


def shifted_mapping(rgbs, hue, saturation, value):
    """``{元の RGB: ずらした RGB}``。変わらない色と白は含めない。"""
    mapping = {}
    for rgb in rgbs:
        source = tuple(int(channel) for channel in rgb[:3])
        if source == (255, 255, 255):
            continue
        destination = shifted_color(source, hue, saturation, value)
        if destination != source:
            mapping[source] = destination
    return mapping


class HsvAdjustDialog(QDialog):
    """色相・彩度・明度のスライダー。動かすたび ``previewChanged`` を出す。"""

    #: ``{元の RGB: ずらした RGB}``。
    previewChanged = Signal(object)

    def __init__(self, rgbs, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("色相・彩度・明度"))
        self.setModal(True)
        self.resize(420, 190)
        self._rgbs = [tuple(int(channel) for channel in rgb[:3]) for rgb in rgbs]

        layout = QVBoxLayout(self)
        note = QLabel(
            tr("選択中の{count}色をまとめて調整します。"
               "OKで、選択レイヤーのすべてのコマへ適用します。").format(
                count=len(self._rgbs)
            )
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.hue = self._add_slider(layout, tr("色相"), HUE_RANGE, "°")
        self.saturation = self._add_slider(
            layout, tr("彩度"), SATURATION_RANGE, "%"
        )
        self.value = self._add_slider(layout, tr("明度"), VALUE_RANGE, "%")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_slider(self, layout, label, value_range, suffix):
        row = QHBoxLayout()
        caption = QLabel(label)
        caption.setFixedWidth(42)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(*value_range)
        slider.setValue(0)
        readout = QLabel(f"0{suffix}")
        readout.setFixedWidth(52)
        readout.setAlignment(Qt.AlignmentFlag.AlignRight)
        slider.valueChanged.connect(
            lambda current: readout.setText(f"{current:+d}{suffix}")
        )
        slider.valueChanged.connect(self._emit_preview)
        row.addWidget(caption)
        row.addWidget(slider, 1)
        row.addWidget(readout)
        layout.addLayout(row)
        return slider

    def _emit_preview(self):
        self.previewChanged.emit(self.mapping())

    def mapping(self):
        return shifted_mapping(
            self._rgbs,
            self.hue.value(),
            self.saturation.value(),
            self.value.value(),
        )
