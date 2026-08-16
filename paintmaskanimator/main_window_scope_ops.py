"""一括処理ランナー（Issue #9）と MainWindow の UI を繋ぐ層。

``frame_scope`` 側は UI を知らない純粋な実行器なので、進捗ダイアログ・待機
カーソル・キャンセル・キャッシュ破棄といった「画面側の後始末」はここが引き
受ける。各操作（ゴミ取り・色置換・変形…）は
:meth:`ScopeOpsMixin.run_over_scope` を呼ぶだけでよく、全コマ対応を自前で
書き直す必要がなくなる。
"""
from .common import *  # noqa: F401,F403
from ._main_window_members import MainWindowMembers
from .frame_scope import FrameScope, apply_over_scope
from .logging_setup import get_logger

log = get_logger(__name__)


class ScopeOpsMixin(MainWindowMembers):
    def current_frame_scope(self, all_frames, *, layers=None, selection_only=False):
        """「すべてのコマ」チェックボックスの状態からスコープを組み立てる。

        適用範囲の指定はいずれ1箇所へ統一する（#9 の受け入れ条件）。その移行を
        1関数に閉じておくため、各呼び出し元はこの入口だけを使う。
        """
        return FrameScope.choose(
            self.canvas,
            bool(all_frames),
            layers=layers,
            selection_only=selection_only,
        )

    def run_over_scope(
        self,
        scope,
        op,
        *,
        label,
        progress_title=None,
        progress_label=None,
        count_pixels=None,
        cancellable=False,
    ):
        """スコープ内の各セルへ ``op`` を適用し、進捗表示と後始末まで行う。

        戻り値は :class:`~.frame_scope.ScopeResult`。キャンセルされた場合は
        ドキュメントが変更されていないことがランナー側で保証されている。
        """
        title = progress_title or label
        dialog_state = {"dialog": None, "total": 0}

        def report(done, total, cell):
            if dialog_state["dialog"] is None:
                dialog_state["total"] = max(1, int(total))
                dialog_state["dialog"] = self.create_progress_counter(
                    title,
                    dialog_state["total"],
                    progress_label or f"{label}の対象コマを確認しています",
                    cancellable=bool(cancellable),
                )
            frame_index = int(cell[0])
            self.update_progress_counter(
                dialog_state["dialog"],
                done,
                dialog_state["total"],
                f"コマ {frame_index + 1} に{label}を適用しています",
            )

        def cancel_requested():
            dialog = dialog_state["dialog"]
            return bool(dialog is not None and dialog.wasCanceled())

        should_cancel = cancel_requested if cancellable else None

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = apply_over_scope(
                self.canvas,
                scope,
                op,
                label=label,
                progress=report,
                should_cancel=should_cancel,
                count_pixels=count_pixels,
            )
        finally:
            QApplication.restoreOverrideCursor()
            self.close_progress_counter(dialog_state["dialog"])

        if result.changed:
            self.invalidate_scope_caches()
            for frame_index, layer_index in result.changed_cell_list:
                self.canvas.cellChanged.emit(int(frame_index), int(layer_index))
            self.canvas.changed.emit()
            self.canvas.update()
        return result

    def invalidate_scope_caches(self):
        """画像実体を差し替えたときに捨てる表示用キャッシュをまとめる。

        一括処理は ``layer.image`` を新しいオブジェクトへ差し替えるため、画像を
        キーにした派生キャッシュを残すと表示/非表示の切替で旧画像が復活する。
        破棄漏れが各所で再発しないよう、対象の一覧はここだけに置く。
        """
        canvas = self.canvas
        for cache_name in (
            "_color_filter_cache",
            "_color_index_cache",
            "_pseudo_transparency_cache",
            "_silhouette_cache",
            "_onion_cache",
            "_playback_frame_cache",
        ):
            cache = getattr(canvas, cache_name, None)
            if cache is not None:
                cache.clear()
