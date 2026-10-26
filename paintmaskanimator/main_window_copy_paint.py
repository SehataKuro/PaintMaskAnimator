"""見本から塗り写す。``window.copy_paint`` が持つ。

メニューから実行すると小さな設定画面を出し、見本にするセル（既定はタイムラインで
選んでいるセル）を選んで「実行」すると、同じレイヤーのほかのセルを見本と同じ色で
塗る。ほかのレイヤーには触れない。塗る範囲を決めるのは :mod:`copy_paint`、
ここは画面と書き込み（Undo 1回にまとめる）を受け持つ。

A collaborator rather than a mixin -- see main_window_export.py for why.
"""
from typing import TYPE_CHECKING, List, Optional, Tuple

import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout,
)

from . import copy_paint
from .frame_scope import FrameScope, resolve_cells
from .i18n import tr
from .imaging import qimage_rgba_array
from .progress import close_counter, create_counter, update_counter

if TYPE_CHECKING:
    from .main_window import MainWindow

Cell = Tuple[int, int]


def cell_label(layer, frame_index) -> str:
    """設定画面の一覧に出す名前。セル番号があれば添える。"""
    number = getattr(layer, "sequence_number", None)
    if number is None:
        return tr("コマ {frame}").format(frame=frame_index + 1)
    return tr("コマ {frame}（セル {number}）").format(frame=frame_index + 1, number=number)


class CopyPaintDialog(QDialog):
    """見本にするセルを選ぶだけの、小さな設定画面。"""

    def __init__(self, parent, layer_name: str, cells: List[Cell], labels: List[str],
                 current: int, title: Optional[str] = None, note: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle(title or tr("見本から塗り写す"))
        self.cells = cells
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow(tr("レイヤー"), QLabel(layer_name))
        self.reference = QComboBox()
        for label in labels:
            self.reference.addItem(label)
        self.reference.setCurrentIndex(current)
        form.addRow(tr("見本にするセル"), self.reference)
        layout.addLayout(form)
        hint = QLabel(note or tr(
            "見本で塗った色を、このレイヤーのほかのセルの同じ部分へ写します。"
            "ほかのレイヤーには触れません。見つけられなかった部分は塗りません。"
        ))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setText(tr("キャンセル"))
        self.run_button = buttons.addButton(tr("実行"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.run_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self) -> Cell:
        return self.cells[self.reference.currentIndex()]


class CopyPaintController:
    """Owned by ``MainWindow`` as ``window.copy_paint``."""

    def __init__(self, window: "MainWindow"):
        self.window = window

    def cells(self) -> List[Cell]:
        """アクティブレイヤーの、絵のあるセル（保持はキーフレームへ畳む）。"""
        canvas = self.window.canvas
        return [
            (frame, layer) for frame, layer in resolve_cells(canvas, FrameScope.all_frames(canvas))
            if canvas.frames[frame].layers[layer].has_content
        ]

    def open(self) -> None:
        """設定画面を出し、「実行」なら塗り写す。"""
        canvas = self.window.canvas
        status = self.window.statusBar()
        canvas.abort_skewer_fill()
        cells = self.cells()
        if len(cells) < 2:
            status.showMessage(tr("塗り写せるセルが2つ以上ありません。"), 3000)
            return
        layer_index = int(canvas.active_layer_index)
        key = canvas.resolve_key_frame(int(canvas.current_frame), layer_index)
        # 既定の見本は、タイムラインで選んでいるセル。
        current = 0
        if key is not None and (key, layer_index) in cells:
            current = cells.index((key, layer_index))
        labels = [
            cell_label(canvas.frames[frame].layers[layer], frame) for frame, layer in cells
        ]
        layer_name = canvas.frames[cells[0][0]].layers[layer_index].name
        dialog = CopyPaintDialog(self.window, layer_name, cells, labels, current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.run(cells, cells.index(dialog.chosen()))

    def run(self, cells: List[Cell], reference_index: int) -> bool:
        """``cells[reference_index]`` を見本に塗り写す。何か塗れたら真。"""
        canvas = self.window.canvas
        status = self.window.statusBar()
        pixels = [
            np.ascontiguousarray(qimage_rgba_array(canvas.frames[frame].layers[layer].image))
            for frame, layer in cells
        ]
        plan = self._plan(pixels, reference_index)
        if plan is None:
            status.showMessage(tr("塗り写しを中止しました。"), 2400)
            return False
        if not plan.paints:
            status.showMessage(self._summary(plan, 0), 4000)
            return False
        targets = {cells[index]: items for index, items in plan.paints.items()}
        written = set()

        def op(context):
            items = targets.get((context.frame, context.layer_index))
            if not items:
                return None
            image, view = canvas._layer_fill_pixels(context.layer.image)
            for rgb, (x0, y0, x1, y1), mask in items:
                patch = view[y0:y1, x0:x1]
                # 見本と同じ色にそろえるので、バケツの不透明度は使わない。
                patch[mask, :3] = rgb
                patch[mask, 3] = 255
                written.add(rgb)
            return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

        scope = FrameScope.cells(
            sorted({frame for frame, _layer in targets}), (cells[0][1],)
        )
        result = self.window.scope.run_over(
            scope, op, label=tr("見本から塗り写す"), cancellable=True,
        )
        canvas.update()
        if result.cancelled:
            status.showMessage(tr("塗り写しを中止しました。"), 2400)
            return False
        if written:
            canvas._emit_actual_paint_colors(tuple(sorted(written)))
        status.showMessage(self._summary(plan, result.changed_cells), 6000)
        return bool(result.changed)

    def _plan(self, pixels, reference_index) -> Optional[copy_paint.CopyPlan]:
        title = tr("見本から塗り写す")
        dialog = create_counter(self.window, title, 1, cancellable=True)
        options = self.window.canvas.bucket_options()

        def progress(done, total):
            if dialog.wasCanceled():
                return False
            dialog.setMaximum(max(1, total))
            update_counter(dialog, done, max(1, total), tr("塗る範囲を探しています"))
            return True

        try:
            return copy_paint.plan_copy(pixels, reference_index, options, progress=progress)
        finally:
            close_counter(dialog)

    @staticmethod
    def _summary(plan, changed_cells) -> str:
        if not plan.parts:
            return tr("見本にだけある塗りが見つかりませんでした（ほかのセルにもう塗ってあります）。")
        if plan.missed:
            everywhere = sum(
                1 for _rgb, _area, found in plan.found if found >= plan.cells - 1
            )
            return tr(
                "塗り写し：{cells}セルを塗りました。見本の{parts}か所のうち、"
                "すべてのセルで見つかったのは{everywhere}か所です。"
                "見つからなかったセルは塗っていないので、「見本との違いチェック」で確かめてください。"
            ).format(cells=changed_cells, parts=plan.parts, everywhere=everywhere)
        return tr("塗り写し：{cells}セルを塗りました。").format(cells=changed_cells)
