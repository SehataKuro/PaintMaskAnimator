"""串刺し塗り（バケツの全コマ一括適用）を PaintCanvas に足す mixin。

串刺し塗りは、クリックした図形を選択レイヤーの各コマで追いかけて塗る。
コマごとに絵は動くので、同じ座標を種にし続けると途中で図形の外へ出て背景を
塗り始める。そこで現在コマから前後へ1コマずつ進み、直前のコマで塗った領域と
重なる図形を選び直す（:func:`bucket_fill.track_fill_region`）。

追跡の信用度が下がったコマでは勝手に塗らずに止まり、そのコマを表示して
ユーザーに塗る場所をクリックしてもらう（Esc でそのコマを飛ばす）。
すべてのコマが決まってから一度に塗るので、Undo は1回で全コマ戻る。

既存のセルにだけ塗る。保持セルを新しいキーに分けたり、空セルに絵を
作ったりはしない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from . import bucket_fill, frame_scope, imaging
from ._canvas_members import CanvasMembers
from .i18n import tr
from .logging_setup import get_logger
from .progress import close_counter, create_counter, update_counter

log = get_logger(__name__)

# 追跡の重なり（IoU）がこれ未満のコマは、自動では塗らずにユーザーに指定してもらう。
TRACK_CONFIDENT_IOU = 0.5

# 手動指定を待っているコマで、前のコマの塗り範囲を示す色。
_GUIDE_RGBA = (255, 64, 160, 90)
# 追跡が「たぶんここ」と推測した範囲を示す色（Enter で採用できる）。
_GUESS_RGBA = (40, 140, 255, 110)

Cell = Tuple[int, int]


@dataclass
class SkewerFillSession:
    """串刺し塗り1回分の途中経過。手動指定で止まっている間も保持する。"""

    scope: Any
    reference: Optional[Tuple[int, int, int]]
    options: bucket_fill.BucketOptions
    selection_mask: Optional[np.ndarray]
    replacement: Any
    # 塗り始めたときに表示していたコマ。終わったらここへ戻す。
    origin_frame: int
    # これから見るセル。前方向と後ろ方向の2本。
    queues: List[List[Cell]]
    # 方向ごとの「直前に塗れたコマの領域」。
    previous: List[np.ndarray]
    # 方向ごとの「直前に塗れたコマの画素」。コマ間のずれの推定に使う。
    previous_pixels: List[Optional[np.ndarray]]
    # 走査対象のセル一覧。途中でタイムラインが変わっていないかの確認用。
    cells: List[Cell]
    direction: int = 0
    # ``id(image)`` → ``(外接矩形, 切り出したマスク)``。
    regions: Dict[int, Tuple[Tuple[int, int, int, int], np.ndarray]] = field(
        default_factory=dict
    )
    # ``id(image)`` → ``(image, 計画したときの cacheKey)``。途中で描き換えられて
    # いないかの確認用。
    images: Dict[int, Tuple[QImage, int]] = field(default_factory=dict)
    skipped: int = 0
    # 手動指定を待っているセル ``(frame, layer)``。
    pending: Optional[Cell] = None
    # 手動指定を待つコマに重ねる、推測（無ければ前のコマの塗り範囲）。
    guide: Optional[QImage] = None
    # 自信はないが追跡が推測した範囲と、そのコマの画素。Enter で採用する。
    guess: Optional[bucket_fill.FillResult] = None
    guess_pixels: Optional[np.ndarray] = None


class SkewerFillMixin(CanvasMembers):
    _skewer_session: Optional[SkewerFillSession] = None

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def flood_fill_over_frames(self, p):
        """串刺し塗りのクリック。手動指定待ちなら、その指定として扱う。"""
        if self._skewer_session is not None:
            self._skewer_resume_with_click(p)
            return
        start_x, start_y = int(p.x()), int(p.y())
        if not self._seed_in_bounds(start_x, start_y):
            return
        window: Any = self.window()
        scope_controller = getattr(window, "scope", None)
        if scope_controller is None:
            return
        scope = scope_controller.current_frame(True, selection_only=False)
        cells = frame_scope.resolve_cells(self, scope)
        layer_index = int(self.active_layer_index)
        key_frame = self.resolve_key_frame(self.current_frame, layer_index)
        if key_frame is None or (key_frame, layer_index) not in cells:
            # 空セルには塗らない（新しいセルを作らない）。
            self.status_message.emit(
                tr("串刺し塗り：このコマには塗れる絵がありません。")
            )
            return
        pivot_image = self.frames[key_frame].layers[layer_index].image

        options = self.bucket_options()
        selection_mask = self.selection_mask_bool(
            pivot_image.width(), pivot_image.height()
        )
        _converted, pixels = self._layer_fill_pixels(pivot_image)
        start_result = bucket_fill.compute_fill_region(
            pixels, start_x, start_y, options, selection_mask
        )
        if not start_result.ok:
            message = self._bucket_failure_message(start_result.reason)
            self.status_message.emit(
                message or tr("串刺し塗りの対象になるコマがありませんでした。")
            )
            return

        pivot = cells.index((key_frame, layer_index))
        session = SkewerFillSession(
            scope=scope,
            reference=bucket_fill.seed_category(pixels, start_x, start_y),
            options=options,
            selection_mask=selection_mask,
            replacement=self.paint_source_color(),
            origin_frame=int(self.current_frame),
            queues=[cells[pivot + 1:], list(reversed(cells[:pivot]))],
            previous=[start_result.mask, start_result.mask],
            previous_pixels=[pixels.copy()] * 2,
            cells=list(cells),
        )
        self._skewer_remember(session, pivot_image, start_result)
        self._skewer_session = session
        self._skewer_advance()

    def skewer_fill_pending(self):
        """手動指定を待っているか。"""
        return bool(
            self._skewer_session is not None
            and self._skewer_session.pending is not None
        )

    def skip_skewer_fill_frame(self):
        """手動指定を待っているコマを塗らずに飛ばし、続きを追いかける。"""
        session = self._skewer_session
        if session is None or session.pending is None:
            return False
        if not self._skewer_session_valid(session):
            self.abort_skewer_fill(tr("絵が変わったため、串刺し塗りを取り消しました。"))
            return True
        session.skipped += 1
        self._skewer_clear_pending(session)
        self._skewer_advance()
        return True

    def abort_skewer_fill(self, message=None):
        """途中の串刺し塗りを、何も塗らずに取り消す。"""
        if self._skewer_session is None:
            return
        self._skewer_session = None
        self.update()
        self.status_message.emit(message or tr("串刺し塗りを取り消しました。"))

    # ------------------------------------------------------------------
    # 追跡
    # ------------------------------------------------------------------
    def _skewer_advance(self):
        """次のコマから追跡を続ける。信用度が低いコマに来たら止まって待つ。"""
        session = self._skewer_session
        if session is None:
            return
        total = sum(len(queue) for queue in session.queues)
        dialog = (
            create_counter(self, tr("串刺し塗り"), total, cancellable=True)
            if total
            else None
        )
        done = 0
        try:
            while session.direction < len(session.queues):
                queue = session.queues[session.direction]
                while queue:
                    if dialog is not None and dialog.wasCanceled():
                        self.abort_skewer_fill(tr("串刺し塗りを中止しました。"))
                        return
                    frame_index, layer_index = queue[0]
                    update_counter(
                        dialog, done, total,
                        tr("コマ {value} の塗る範囲を探しています").format(
                            value=frame_index + 1
                        ),
                    )
                    done += 1
                    image = self.frames[frame_index].layers[layer_index].image
                    if id(image) in session.regions:
                        queue.pop(0)
                        continue
                    result, pixels = self._skewer_locate(session, image)
                    if result is not None and result.ok and (
                        result.score is None
                        or result.score >= TRACK_CONFIDENT_IOU
                    ):
                        self._skewer_remember(session, image, result)
                        session.previous[session.direction] = result.mask
                        session.previous_pixels[session.direction] = pixels
                        queue.pop(0)
                        continue
                    if result is None:
                        # 追跡しない設定（「隣接」OFF）で塗れなかったコマは飛ばす。
                        session.skipped += 1
                        queue.pop(0)
                        continue
                    # 自信がないので、このコマはユーザーに指定してもらう。
                    # 推測があれば見せて、Enter だけで採用できるようにする。
                    queue.pop(0)
                    if result.ok:
                        session.guess = result
                        session.guess_pixels = pixels
                    self._skewer_wait_for(session, (frame_index, layer_index))
                    return
                session.direction += 1
        finally:
            close_counter(dialog)
        self._skewer_apply()

    def _skewer_locate(self, session, image):
        """追跡で塗る範囲を探す。``(結果, そのコマの画素)`` を返す。

        結果が ``None`` なのは「追跡しない設定で塗れなかった」とき。
        """
        _converted, view = self._layer_fill_pixels(image)
        # 次のコマとのずれの推定に使うので、画像に縛られない複製にしておく。
        pixels = view.copy()
        previous = session.previous[session.direction]
        if pixels.shape[:2] != previous.shape:
            return None, pixels
        if session.options.adjacent:
            return bucket_fill.track_fill_region(
                pixels, previous, session.reference, session.options,
                session.selection_mask,
                previous_pixels=session.previous_pixels[session.direction],
            ), pixels
        # 「隣接」OFF はレイヤー内の同色を一括で塗るので、位置を追う必要がない。
        reference = session.reference
        if reference is None:
            target = bucket_fill.background_mask(pixels)
        else:
            target = np.all(pixels[:, :, :3] == reference, axis=2)
            target &= pixels[:, :, 3] > 0
        points = np.argwhere(target)
        if not points.size:
            return None, pixels
        start_y, start_x = (int(value) for value in points[0])
        result = bucket_fill.compute_fill_region(
            pixels, start_x, start_y, session.options, session.selection_mask
        )
        return (result if result.ok else None), pixels

    def _skewer_remember(self, session, image, result):
        assert result.bounds is not None  # ok なら必ず外接矩形がある
        x0, y0, x1, y1 = result.bounds
        session.regions[id(image)] = (
            result.bounds, result.mask[y0:y1, x0:x1].copy()
        )
        session.images[id(image)] = (image, int(image.cacheKey()))

    def _skewer_wait_for(self, session, cell):
        frame_index, layer_index = cell
        session.pending = cell
        if session.guess is not None:
            session.guide = self._skewer_guide_image(session.guess.mask, _GUESS_RGBA)
        else:
            session.guide = self._skewer_guide_image(
                session.previous[session.direction]
            )
        self.current_frame = int(frame_index)
        self.active_layer_index = int(layer_index)
        self.selectionChanged.emit()
        self.update()
        self.status_message.emit(self._skewer_pending_message(session))

    @staticmethod
    def _skewer_pending_message(session):
        assert session.pending is not None
        if session.guess is not None:
            return tr(
                "串刺し塗り：コマ {value} は自信がありません。推測した範囲を青で"
                "表示しています（Enter：この範囲で塗る／クリック：塗る場所を指定"
                "／Esc：このコマを飛ばす）。"
            ).format(value=session.pending[0] + 1)
        return tr(
            "串刺し塗り：コマ {value} は追いかけきれませんでした。"
            "塗る場所をクリックしてください（Esc：このコマを飛ばす）。"
        ).format(value=session.pending[0] + 1)

    def skewer_fill_has_guess(self):
        session = self._skewer_session
        return bool(session is not None and session.pending is not None and session.guess)

    def accept_skewer_guess(self):
        """手動指定を待っているコマで、追跡の推測をそのまま採用する（Enter）。"""
        session = self._skewer_session
        if session is None or session.pending is None or session.guess is None:
            return False
        if not self._skewer_session_valid(session):
            self.abort_skewer_fill(tr("絵が変わったため、串刺し塗りを取り消しました。"))
            return True
        frame_index, layer_index = session.pending
        image = self.frames[frame_index].layers[layer_index].image
        self._skewer_remember(session, image, session.guess)
        session.previous[session.direction] = session.guess.mask
        session.previous_pixels[session.direction] = session.guess_pixels
        self._skewer_clear_pending(session)
        self._skewer_advance()
        return True

    @staticmethod
    def _skewer_clear_pending(session):
        session.pending = None
        session.guide = None
        session.guess = None
        session.guess_pixels = None

    def _skewer_resume_with_click(self, p):
        session = self._skewer_session
        assert session is not None
        if not self._skewer_session_valid(session):
            self.abort_skewer_fill(tr("絵が変わったため、串刺し塗りを取り消しました。"))
            return
        if session.pending is None:
            return
        frame_index, layer_index = session.pending
        pending_image = self.frames[frame_index].layers[layer_index].image
        key_frame = self.resolve_key_frame(self.current_frame, layer_index)
        if (
            key_frame is None
            or self.frames[key_frame].layers[layer_index].image is not pending_image
        ):
            # 別のコマを見ているときは、待っているコマへ戻す。
            self._skewer_wait_for(session, session.pending)
            return
        start_x, start_y = int(p.x()), int(p.y())
        if not self._seed_in_bounds(start_x, start_y, pending_image):
            return
        _converted, view = self._layer_fill_pixels(pending_image)
        pixels = view.copy()
        result = bucket_fill.compute_fill_region(
            pixels, start_x, start_y, session.options, session.selection_mask
        )
        if not result.ok:
            message = self._bucket_failure_message(result.reason)
            if message:
                self.status_message.emit(message)
            return
        self._skewer_remember(session, pending_image, result)
        session.previous[session.direction] = result.mask
        session.previous_pixels[session.direction] = pixels
        self._skewer_clear_pending(session)
        self._skewer_advance()

    def _skewer_session_valid(self, session):
        """待っている間にタイムラインや絵が変わっていないか。"""
        try:
            cells = frame_scope.resolve_cells(self, session.scope)
        except (IndexError, AttributeError):
            return False
        if cells != session.cells:
            return False
        for image, cache_key in session.images.values():
            if int(image.cacheKey()) != cache_key:
                return False
        live = {
            id(self.frames[frame_index].layers[layer_index].image)
            for frame_index, layer_index in cells
        }
        return all(image_id in live for image_id in session.images)

    # ------------------------------------------------------------------
    # 書き込み
    # ------------------------------------------------------------------
    def _skewer_apply(self):
        session = self._skewer_session
        self._skewer_session = None
        if session is None:
            return
        window: Any = self.window()
        scope_controller = getattr(window, "scope", None)
        if scope_controller is None or not self._skewer_session_valid(session):
            self.update()
            self.status_message.emit(
                tr("絵が変わったため、串刺し塗りを取り消しました。")
            )
            return
        written_colors = set()

        def op(context):
            planned = session.regions.get(id(context.layer.image))
            if planned is None:
                return None
            bounds, mask = planned
            image, pixels = self._layer_fill_pixels(context.layer.image)
            x0, y0, x1, y1 = bounds
            written_colors.update(self._fill_patch(
                pixels[y0:y1, x0:x1], mask, session.replacement
            ))
            return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

        if 0 <= session.origin_frame < len(self.frames):
            self.current_frame = session.origin_frame
            self.selectionChanged.emit()
        result = scope_controller.run_over(
            session.scope,
            op,
            label=tr("串刺し塗り"),
            cancellable=True,
        )
        self.update()
        if result.cancelled:
            self.status_message.emit(tr("串刺し塗りを中止しました。"))
            return
        if not result.changed:
            self.status_message.emit(
                tr("串刺し塗りの対象になるコマがありませんでした。")
            )
            return
        if written_colors:
            self._emit_actual_paint_colors(tuple(sorted(written_colors)))
        if session.skipped:
            self.status_message.emit(
                tr("串刺し塗り：{filled} コマを塗り、{skipped} コマは対象外でした。").format(
                    filled=result.changed_cells, skipped=session.skipped
                )
            )
        else:
            self.status_message.emit(
                tr("串刺し塗り：{filled} コマを塗りました。").format(
                    filled=result.changed_cells
                )
            )

    # ------------------------------------------------------------------
    # 表示
    # ------------------------------------------------------------------
    @staticmethod
    def _skewer_guide_image(mask, color=_GUIDE_RGBA):
        rgba = np.zeros(mask.shape + (4,), dtype=np.uint8)
        rgba[mask] = color
        return imaging.rgba_array_to_qimage(rgba)

    def paint_skewer_guide(self, painter: QPainter, work_rect):
        """手動指定を待つコマに、前のコマの塗り範囲を重ねる（キャンバス座標系）。"""
        session = self._skewer_session
        if session is None or session.pending is None or session.guide is None:
            return
        painter.drawImage(work_rect, session.guide)

    def paint_skewer_banner(self, painter: QPainter):
        """手動指定を待っている間、何をすればよいかをキャンバス上部に出す。"""
        session = self._skewer_session
        if session is None or session.pending is None:
            return
        text = self._skewer_pending_message(session)
        metrics = painter.fontMetrics()
        margin = 8
        width = min(self.width() - margin * 2, metrics.horizontalAdvance(text) + 20)
        rect = QRectF(margin, margin, max(40, width), metrics.height() + 12)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 20, 210))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(
            rect.adjusted(10, 0, -10, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(rect.width()) - 20),
        )
        painter.restore()


__all__ = ["SkewerFillMixin", "SkewerFillSession", "TRACK_CONFIDENT_IOU"]
