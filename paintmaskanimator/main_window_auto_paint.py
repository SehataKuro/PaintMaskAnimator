"""色分けの線で自動塗り。``window.auto_paint`` が持つ。

黒の主線と原色の赤・緑・青の色トレス線を色分けの境界にして、見本のセル（既定は
タイムラインで選んでいるセル）から前後へ1セルずつ、同じ場所の色で塗り進める。
一通り塗ったら迷った所の少ないセルを見本に加えて塗り直す。アクティブレイヤー
だけを塗り、Undo は1回にまとめる。塗り残しと塗り間違いかもしれない所は、
「見本との違いチェック」と同じ一覧で知らせる。塗る範囲と色を決めるのは
:mod:`auto_paint`、ここは画面と書き込みを受け持つ。

A collaborator rather than a mixin -- see main_window_export.py for why.
"""
from typing import TYPE_CHECKING, List, Optional

import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog

from . import auto_paint
from .frame_scope import FrameScope
from .i18n import tr
from .imaging import qimage_rgba_array
from .main_window_copy_paint import Cell, CopyPaintDialog, cell_label
from .progress import close_counter, create_counter, update_counter

if TYPE_CHECKING:
    from .main_window import MainWindow


class AutoPaintController:
    """Owned by ``MainWindow`` as ``window.auto_paint``."""

    def __init__(self, window: "MainWindow"):
        self.window = window

    def open(self) -> None:
        """設定画面を出し、「実行」なら塗る。"""
        canvas = self.window.canvas
        status = self.window.statusBar()
        canvas.abort_skewer_fill()
        cells = self.window.copy_paint.cells()
        if len(cells) < 2:
            status.showMessage(tr("塗れるセルが2つ以上ありません。"), 3000)
            return
        layer_index = int(canvas.active_layer_index)
        key = canvas.resolve_key_frame(int(canvas.current_frame), layer_index)
        current = 0
        if key is not None and (key, layer_index) in cells:
            current = cells.index((key, layer_index))
        labels = [
            cell_label(canvas.frames[frame].layers[layer], frame) for frame, layer in cells
        ]
        layer_name = canvas.frames[cells[0][0]].layers[layer_index].name
        dialog = CopyPaintDialog(
            self.window, layer_name, cells, labels, current,
            title=tr("色分けの線で自動塗り"),
            note=tr(
                "黒の主線と、赤・緑・青の色トレス線を色分けの境界にして、見本から隣の"
                "セルへ順に、同じ場所の色で塗ります。色トレス線も、見本で塗ってあれば"
                "その色に、線のまま残してあれば残します。一通り塗ったら、迷った所の"
                "少ないセルを見本に加えて自動で塗り直します。ほかのレイヤーには触れません。"
            ),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.run(cells, cells.index(dialog.chosen()))

    def run(self, cells: List[Cell], reference_index: int) -> bool:
        """``cells[reference_index]`` を見本に塗る。何か塗れたら真。"""
        canvas = self.window.canvas
        status = self.window.statusBar()
        pixels = [
            np.ascontiguousarray(qimage_rgba_array(canvas.frames[frame].layers[layer].image))
            for frame, layer in cells
        ]
        plan = self._plan(pixels, reference_index)
        if plan is None:
            status.showMessage(tr("自動塗りを中止しました。"), 2400)
            return False
        targets = {cells[index]: paint for index, paint in plan.cells.items() if paint.paints}
        if not targets:
            status.showMessage(tr("自動塗り：塗るところはありませんでした。"), 4000)
            return False
        written = set()

        def op(context):
            paint = targets.get((context.frame, context.layer_index))
            if paint is None:
                return None
            image, view = canvas._layer_fill_pixels(context.layer.image)
            for rgb, (x0, y0, x1, y1), mask in paint.paints:
                patch = view[y0:y1, x0:x1]
                patch[mask, :3] = rgb
                patch[mask, 3] = 255
                written.add(rgb)
            return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

        scope = FrameScope.cells(sorted({frame for frame, _layer in targets}), (cells[0][1],))
        result = self.window.scope.run_over(
            scope, op, label=tr("色分けの線で自動塗り"), cancellable=True,
        )
        canvas.update()
        if result.cancelled:
            status.showMessage(tr("自動塗りを中止しました。"), 2400)
            return False
        if written:
            canvas._emit_actual_paint_colors(tuple(sorted(written)))
        entries = self.notes(cells, plan)
        message = tr("自動塗り：{cells}セルを塗りました。").format(cells=result.changed_cells)
        if plan.rounds > 1:
            message += tr("見本を{added}セルに増やして{rounds}回塗り直しました。").format(
                added=len(plan.references), rounds=plan.rounds - 1,
            )
        if entries:
            message += tr("確かめたい所が{count}か所あります。").format(count=len(entries))
        status.showMessage(message, 8000)
        if entries:
            self.window.reference_check.show_list(
                entries, cells[reference_index][0], title=tr("自動塗りの確かめたい所"),
                intro=tr(
                    "塗り残し（場所や色を決めきれなかった部分）と、前後のセルと違う色で"
                    "塗ってあった部分（塗り替えていません）です。行を選ぶと、そのコマへ移って"
                    "場所を選択します。"
                ),
                reference_only=[],
            )
        return bool(result.changed)

    @staticmethod
    def notes(cells: List[Cell], plan: auto_paint.AutoPlan):
        """一覧に載せる ``(コマ, レイヤー, 見つかったもの)`` の並び。"""
        entries = []
        for index in sorted(plan.cells):
            frame, layer = cells[index]
            for finding in plan.cells[index].notes:
                entries.append((frame, layer, finding))
        return entries

    def _plan(self, pixels, reference_index) -> Optional[auto_paint.AutoPlan]:
        dialog = create_counter(self.window, tr("色分けの線で自動塗り"), 1, cancellable=True)

        def progress(done, total):
            if dialog.wasCanceled():
                return False
            dialog.setMaximum(max(1, total))
            update_counter(dialog, done, max(1, total), tr("セルを順に塗っています"))
            return True

        try:
            return auto_paint.plan_auto_paint(pixels, reference_index, progress=progress)
        finally:
            close_counter(dialog)
