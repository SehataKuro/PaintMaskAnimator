"""見本との違い・塗り間違いのチェック。``window.reference_check`` が持つ。

アクティブレイヤーの今のセルを見本にして、ほかのセルについて

* 見本にあってそのセルに無い色、見本に無いのにそのセルにある色
  （:func:`reference_check.compare_colors`）
* 前後のセルの同じ部分と色が違う部分（:func:`reference_check.find_mismatches`）

を一覧にする。一覧の行を選ぶと、そのコマへ移ってその場所を選択範囲にする。
直すのは人の仕事で、ここは何も書き換えない。

A collaborator rather than a mixin -- see main_window_export.py for why.
"""
from typing import TYPE_CHECKING, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap, QIcon
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from . import reference_check
from .frame_scope import FrameScope, resolve_cells
from .i18n import tr
from .imaging import qimage_rgba_array
from .progress import close_counter, create_counter, update_counter

if TYPE_CHECKING:
    from .main_window import MainWindow

#: 一覧に載せる1件：(コマ, レイヤー, 見つかったもの)。
Entry = Tuple[int, int, reference_check.Finding]


def _cell_pixels(image):
    """セル画像を RGBA の ``(高さ, 幅, 4)`` 配列にする。"""
    return np.ascontiguousarray(qimage_rgba_array(image))


def _color_label(rgb) -> str:
    if rgb is None:
        return tr("透明")
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _swatch(rgb) -> QIcon:
    pixmap = QPixmap(14, 14)
    if rgb is None:
        pixmap.fill(Qt.GlobalColor.transparent)
    else:
        pixmap.fill(QColor(*rgb))
    return QIcon(pixmap)


def _split_reference_only(entries: List[Entry], compared: int):
    """半分を超えるセルに無い見本の色は、見本だけの印（色指定のメモなど）とみなす。

    セルごとに並べると一覧が埋まるので、1行の要約へまとめる。
    """
    missing = {}
    for _frame, _layer, finding in entries:
        if finding.kind == reference_check.KIND_MISSING:
            missing[finding.rgb] = missing.get(finding.rgb, 0) + 1
    common = {rgb for rgb, count in missing.items() if count * 2 > compared}
    kept = [
        entry for entry in entries
        if not (entry[2].kind == reference_check.KIND_MISSING and entry[2].rgb in common)
    ]
    summary = [(rgb, missing[rgb], compared) for rgb in sorted(common)]
    return kept, summary


class ReferenceCheckDialog(QDialog):
    """チェック結果の一覧。モードレスで、開いたまま絵を直せる。"""

    def __init__(self, controller: "ReferenceCheckController", entries: List[Entry],
                 reference_frame: int, title: Optional[str] = None,
                 intro: Optional[str] = None, reference_only=None):
        super().__init__(controller.window)
        self.controller = controller
        self.entries = entries
        self.setWindowTitle(title or tr("見本との違いチェック"))
        self.setModal(False)
        self.resize(520, 420)
        layout = QVBoxLayout(self)
        intro_label = QLabel(intro or tr(
            "見本：{frame}コマ目。行を選ぶと、そのコマへ移って場所を選択します。"
        ).format(frame=reference_frame + 1))
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)
        if reference_only is None:
            reference_only = controller.reference_only
        if reference_only:
            colors = tr("、").join(
                tr("{color}（{missing}/{compared}セルに無し）").format(
                    color=_color_label(rgb), missing=missing, compared=compared,
                )
                for rgb, missing, compared in reference_only
            )
            note = QLabel(tr("見本だけにある色：{colors}").format(colors=colors))
            note.setWordWrap(True)
            layout.addWidget(note)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("コマ"), tr("内容"), tr("色"), tr("画素数")])
        self.tree.setRootIsDecorated(False)
        for row, (frame_index, _layer, finding) in enumerate(entries):
            item = QTreeWidgetItem([
                str(frame_index + 1),
                self._describe(finding),
                _color_label(finding.rgb),
                f"{finding.pixels:,}",
            ])
            item.setIcon(2, _swatch(finding.rgb))
            item.setData(0, Qt.ItemDataRole.UserRole, row)
            self.tree.addTopLevelItem(item)
        for column in range(4):
            self.tree.resizeColumnToContents(column)
        self.tree.currentItemChanged.connect(self._on_current)
        layout.addWidget(self.tree)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    @staticmethod
    def _describe(finding) -> str:
        if finding.kind == reference_check.KIND_MISSING:
            return tr("見本の色がありません")
        if finding.kind == reference_check.KIND_EXTRA:
            return tr("見本に無い色があります")
        if finding.kind == reference_check.KIND_UNPAINTED:
            return tr("塗り残し（決めきれず塗っていません）")
        if finding.kind == reference_check.KIND_NEW_PART:
            return tr("新しく出てきた部分（手本に無い形なので塗っていません）")
        if finding.kind == reference_check.KIND_UNDECIDED_LINE:
            return tr("色を決められなかった色トレス線（線のまま残しています）")
        if finding.kind == reference_check.KIND_SUSPECT:
            return tr("塗り間違いかも：前後のセルでは {color}").format(
                color=_color_label(finding.expected)
            )
        return tr("前後のコマでは {color}").format(color=_color_label(finding.expected))

    def _on_current(self, item, _previous=None):
        if item is None:
            return
        row = int(item.data(0, Qt.ItemDataRole.UserRole))
        self.controller.show_entry(self.entries[row])


class ReferenceCheckController:
    """Owned by ``MainWindow`` as ``window.reference_check``."""

    def __init__(self, window: "MainWindow"):
        self.window = window
        self.dialog: Optional[ReferenceCheckDialog] = None
        #: 見本にはあるが、ほとんどのセルに無い色：(色, 無いセルの数, 比べたセルの数)。
        self.reference_only: List[Tuple[Tuple[int, int, int], int, int]] = []

    def _cells(self) -> List[Tuple[int, int]]:
        canvas = self.window.canvas
        cells = resolve_cells(canvas, FrameScope.all_frames(canvas))
        return [
            (frame, layer) for frame, layer in cells
            if canvas.frames[frame].layers[layer].has_content
        ]

    def run(self) -> None:
        """今のセルを見本にして、アクティブレイヤーの全セルを調べる。"""
        canvas = self.window.canvas
        status = self.window.statusBar()
        layer_index = int(canvas.active_layer_index)
        reference_frame = canvas.resolve_key_frame(int(canvas.current_frame), layer_index)
        cells = self._cells()
        if reference_frame is None or (reference_frame, layer_index) not in cells:
            status.showMessage(tr("見本にするセル（塗ってあるコマ）を表示してから実行してください。"), 3200)
            return
        if len(cells) < 2:
            status.showMessage(tr("比べるセルがありません。"), 2800)
            return
        entries = self.collect(cells, (reference_frame, layer_index))
        if entries is None:
            status.showMessage(tr("チェックを中止しました。"), 2400)
            return
        if not entries and not self.reference_only:
            status.showMessage(tr("見本との違い・塗り間違いは見つかりませんでした。"), 3200)
            return
        self.show_list(entries, reference_frame)
        status.showMessage(tr("{count}件の候補が見つかりました。").format(count=len(entries)), 3200)

    def show_list(self, entries: List[Entry], reference_frame: int,
                  title: Optional[str] = None, intro: Optional[str] = None,
                  reference_only=None) -> None:
        """一覧を開く（前の一覧は閉じる）。自動塗りの「確かめたい所」の一覧にも使う。"""
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
        self.dialog = ReferenceCheckDialog(
            self, entries, reference_frame, title=title, intro=intro,
            reference_only=reference_only,
        )
        self.dialog.show()

    def collect(self, cells, reference) -> Optional[List[Entry]]:
        """候補を集める。中止されたら ``None``。"""
        canvas = self.window.canvas
        title = tr("見本との違いチェック")
        total = len(cells) * 2
        dialog = create_counter(self.window, title, total, cancellable=True)
        try:
            pixels = {}
            for done, (frame, layer) in enumerate(cells):
                if dialog.wasCanceled():
                    return None
                update_counter(dialog, done, total, tr("色を数えています"))
                pixels[(frame, layer)] = _cell_pixels(canvas.frames[frame].layers[layer].image)
            entries: List[Entry] = []
            reference_pixels = pixels[reference]
            for cell in cells:
                if cell == reference:
                    continue
                for finding in reference_check.compare_colors(reference_pixels, pixels[cell]):
                    entries.append((cell[0], cell[1], finding))
            entries, self.reference_only = _split_reference_only(entries, len(cells) - 1)
            # 前後のセルとの比べ合い。部分分けは重いので、3つ分だけ持っておく。
            regions = {}
            for position in range(1, len(cells) - 1):
                if dialog.wasCanceled():
                    return None
                update_counter(dialog, len(cells) + position, total, tr("前後のコマと比べています"))
                window = cells[position - 1:position + 2]
                for key in list(regions):
                    if key not in window:
                        del regions[key]
                for key in window:
                    if key not in regions:
                        regions[key] = reference_check.CellRegions(pixels[key])
                previous, current, following = (regions[key] for key in window)
                for finding in reference_check.find_mismatches(previous, current, following):
                    entries.append((window[1][0], window[1][1], finding))
            entries.sort(key=lambda entry: (entry[0], entry[2].kind))
            return entries
        finally:
            close_counter(dialog)

    def show_entry(self, entry: Entry) -> None:
        """そのコマへ移り、見つかった場所を選択範囲にする。"""
        frame, layer, finding = entry
        canvas = self.window.canvas
        canvas.current_frame = int(frame)
        canvas.active_layer_index = int(layer)
        canvas.selectionChanged.emit()
        mask = self._finding_mask(canvas, frame, layer, finding)
        if mask is not None:
            canvas.set_selection_mask(mask)
        else:
            canvas.clear_selection()
        canvas.update()

    @staticmethod
    def _finding_mask(canvas, frame, layer, finding):
        if finding.bounds is None:
            return None
        x0, y0, x1, y1 = finding.bounds
        image = canvas.frames[frame].layers[layer].image
        mask = np.zeros((image.height(), image.width()), dtype=bool)
        if finding.mask is not None:
            mask[y0:y1, x0:x1] = finding.mask
        else:
            # 色そのものの画素を選ぶ（見本に無い色）。
            pixels = _cell_pixels(image)[y0:y1, x0:x1]
            mask[y0:y1, x0:x1] = reference_check.color_mask(pixels, finding.rgb)
        return mask
