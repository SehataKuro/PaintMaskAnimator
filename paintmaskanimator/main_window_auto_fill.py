"""前のセルの色で今のセルを自動で塗る操作; owned by ``MainWindow`` as ``window.auto_fill``.

塗る範囲の計画は :mod:`auto_fill` が UI と切り離して立てる。ここでは参照する
セルを選び、進捗を出し、計画を1回の Undo にまとめて書き込むだけを持つ。

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Tuple

from PySide6.QtGui import QImage

from . import auto_fill
from .i18n import tr
from .logging_setup import get_logger
from .progress import close_counter, create_counter, update_counter

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)


class AutoFillController:
    """Owned by ``MainWindow`` as ``window.auto_fill``."""

    def __init__(self, window: "MainWindow"):
        self.window = window

    def _message(self, text, timeout=4000):
        self.window.statusBar().showMessage(text, timeout)

    def previous_cell(self, column, layer_index) -> Optional[Tuple[int, QImage]]:
        """``column`` より前で、今のセルと別の絵を持つ一番近いセル ``(キー, 画像)``。"""
        canvas = self.window.canvas
        key = canvas.resolve_key_frame(column, layer_index)
        if key is None:
            return None
        current = canvas.frames[key].layers[layer_index].image
        for earlier in range(key - 1, -1, -1):
            other = canvas.resolve_key_frame(earlier, layer_index)
            if other is None:
                continue
            layer = canvas.frames[other].layers[layer_index]
            if layer.image is not current and layer.has_content:
                return other, layer.image
        return None

    def fill_from_previous_cell(self):
        """今のセルのまだ塗っていない部分を、前のセルの塗りを手がかりに塗る。"""
        canvas = self.window.canvas
        layer_index = int(canvas.active_layer_index)
        key = canvas.resolve_key_frame(canvas.current_frame, layer_index)
        if key is None:
            self._message(tr("自動塗り：このコマには塗れる絵がありません。"))
            return
        found = self.previous_cell(canvas.current_frame, layer_index)
        if found is None:
            self._message(tr("自動塗り：手がかりにする前のセルがありません。"))
            return
        _reference_key, reference_image = found
        image = canvas.frames[key].layers[layer_index].image
        if reference_image.size() != image.size():
            self._message(tr("自動塗り：前のセルと大きさが違うため塗れません。"))
            return

        _converted, view = canvas._layer_fill_pixels(reference_image)
        reference_pixels = view.copy()
        _converted, view = canvas._layer_fill_pixels(image)
        pixels = view.copy()
        selection_mask = canvas.selection_mask_bool(image.width(), image.height())

        dialog = create_counter(
            self.window, tr("自動塗り"), 1, cancellable=True
        )

        def progress(done, total):
            if dialog.wasCanceled():
                return False
            if dialog.maximum() != max(1, total):
                dialog.setMaximum(max(1, total))
            update_counter(dialog, done, max(1, total), tr("塗る範囲を探しています"))
            return True

        try:
            plan = auto_fill.plan_auto_fill(
                reference_pixels, pixels, canvas.bucket_options(),
                selection_mask, progress,
            )
        finally:
            close_counter(dialog)
        if plan.cancelled:
            self._message(tr("自動塗りを中止しました。"))
            return
        if not plan.filled:
            self._message(tr("自動塗り：前のセルと対応づけられる領域がありませんでした。"))
            return
        self._apply(plan, image)
        if plan.unmatched:
            self._message(
                tr(
                    "自動塗り：{filled} か所を塗りました。{unmatched} か所は"
                    "対応する領域が見つからないか自信がないため、塗っていません。"
                ).format(filled=plan.filled, unmatched=plan.unmatched),
                6000,
            )
        else:
            self._message(
                tr("自動塗り：{filled} か所を塗りました。").format(filled=plan.filled)
            )

    def _apply(self, plan, image):
        """計画を今のセルへ書き込む（Undo は1回）。"""
        canvas = self.window.canvas
        written = set()

        def op(context):
            if context.layer.image is not image:
                return None
            converted, pixels = canvas._layer_fill_pixels(context.layer.image)
            for rgb, (x0, y0, x1, y1), mask in plan.fills:
                patch = pixels[y0:y1, x0:x1]
                patch[mask, :3] = rgb
                patch[mask, 3] = 255
                written.add(rgb)
            return converted.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

        scope = self.window.scope.current_frame(False)
        self.window.scope.run_over(scope, op, label=tr("自動塗り"))
        canvas.update()
        if written:
            canvas._emit_actual_paint_colors(tuple(sorted(written)))


__all__ = ["AutoFillController"]
