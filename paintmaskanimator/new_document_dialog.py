"""File › New… : canvas size plus an optional layout paper."""
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QSpinBox,
)

from . import layout_paper
from .constants import MAX_IMAGE_DIMENSION
from .i18n import tr


class NewDocumentDialog(QDialog):
    def __init__(self, width, height, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("新規作成"))
        form = QFormLayout(self)

        self.paper = QComboBox()
        self.paper.addItem(tr("なし"), None)
        self._sizes = {}
        for entry in layout_paper.papers():
            size = layout_paper.image_size(entry)
            if size is None:
                continue
            self._sizes[entry["id"]] = size
            self.paper.addItem(f"{entry['name']}（{size[0]}×{size[1]}）", entry["id"])
        index = self.paper.findData(layout_paper.last_used())
        self.paper.setCurrentIndex(max(0, index))
        self.paper.setToolTip(
            tr("選んだ画像を、一番下の下書きレイヤーとして入れます。"
            "用紙は 環境設定 › 新規キャンバス で登録します。")
        )
        self.fit_to_paper = QCheckBox(tr("キャンバスサイズを用紙に合わせる"))
        self.fit_to_paper.setChecked(True)

        # 上限は画像読み込みと同じ MAX_IMAGE_DIMENSION。
        self.w = QSpinBox()
        self.w.setRange(64, MAX_IMAGE_DIMENSION)
        self.w.setValue(width)
        self.h = QSpinBox()
        self.h.setRange(64, MAX_IMAGE_DIMENSION)
        self.h.setValue(height)
        self._manual_size = (width, height)

        form.addRow(tr("レイアウト用紙"), self.paper)
        form.addRow("", self.fit_to_paper)
        form.addRow(tr("幅"), self.w)
        form.addRow(tr("高さ"), self.h)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.paper.currentIndexChanged.connect(self._sync)
        self.fit_to_paper.toggled.connect(self._sync)
        self._sync()

    def _sync(self, *_args):
        paper_size = self._sizes.get(self.paper.currentData())
        self.fit_to_paper.setEnabled(paper_size is not None)
        fitted = paper_size is not None and self.fit_to_paper.isChecked()
        if fitted and paper_size is not None:
            if self.w.isEnabled():
                self._manual_size = (self.w.value(), self.h.value())
            self.w.setValue(paper_size[0])
            self.h.setValue(paper_size[1])
        elif not self.w.isEnabled():
            self.w.setValue(self._manual_size[0])
            self.h.setValue(self._manual_size[1])
        self.w.setEnabled(not fitted)
        self.h.setEnabled(not fitted)

    def values(self):
        return self.w.value(), self.h.value()

    def paper_id(self):
        return self.paper.currentData()
