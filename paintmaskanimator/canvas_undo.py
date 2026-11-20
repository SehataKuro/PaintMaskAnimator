"""Undo/redo stacks for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods push document / layer /
region snapshots onto the undo stack and apply them on undo/redo. They run
against a live ``PaintCanvas`` instance and its ``_document`` + stacks.
"""
from .canvas_changes import (
    finish_restored_batch, publish_drawing_cells, publish_initial_images,
    publish_restored_document, publish_restored_drawing, publish_restored_exposure,
    publish_track_changes,
)
from PySide6.QtCore import QRect, QPointF
from .constants import MAX_UNDO, MAX_UNDO_BYTES
from ._canvas_members import CanvasMembers
from .pma_image import snapshot_image
from .logging_setup import get_logger
from .undo_entries import (
    TransformUndo,
    DocUndo,
    ExposureUndo,
    InitialStateUndo,
    LayerBatchUndo,
    LayerInsertUndo,
    LayerRegionUndo,
    LayerRemoveUndo,
    LayerTilesUndo,
    LayerUndo,
    PaletteStateUndo,
    ScopedBatchUndo,
    history_label_for,
)

log = get_logger(__name__)

__all__ = [
    "HistoryBranch",
    "MAX_HISTORY_BRANCHES",
    "UndoMixin",
    "history_label_for",
    "set_undo_limits",
    "trim_undo_stack",
]

#: 保持する分岐の最大本数。古いものから捨てる。
MAX_HISTORY_BRANCHES = 20


class HistoryBranch:
    """Undo後に新しい編集をして捨てられた「もう一つの未来」1本分。

    ``position`` は分岐点（そのRedo列が適用できる状態）の undo_stack 長さ。
    ``entries`` は捨てられた redo_stack のコピーで、末尾が分岐点の直後に
    進む1手。ヒストリーパネルはこれをブランチとして表示し、選んだら
    ``UndoMixin.switch_history_branch`` でその未来へ戻せる。
    """

    __slots__ = ("position", "entries", "label")

    def __init__(self, position, entries, label):
        self.position = int(position)
        self.entries = list(entries)
        self.label = str(label)

    def __repr__(self):  # pragma: no cover - デバッグ用
        return (
            f"HistoryBranch(position={self.position}, "
            f"steps={len(self.entries)}, label={self.label!r})"
        )


def set_undo_limits(max_entries, max_bytes):
    """Change the limits :func:`trim_undo_stack` enforces (from Preferences).

    Existing stacks are trimmed on their next push, not immediately.
    """
    global MAX_UNDO, MAX_UNDO_BYTES
    MAX_UNDO = max(1, int(max_entries))
    MAX_UNDO_BYTES = max(1, int(max_bytes))


def trim_undo_stack(stack):
    """Drop the oldest entries until the stack fits both undo limits.

    Bounded by entry count *and* retained image bytes, whichever binds first;
    at least one entry always survives so a single huge edit stays undoable.
    """
    if len(stack) > MAX_UNDO:
        del stack[:-MAX_UNDO]
    total = sum(entry.nbytes for entry in stack)
    while len(stack) > 1 and total > MAX_UNDO_BYTES:
        total -= stack[0].nbytes
        del stack[0]
    return stack


class UndoMixin(CanvasMembers):
    def document_snapshot(self): return self._document.snapshot()

    def push_undo(self, entry):
        """Push one undo entry, trim to the limits, and branch off redo.

        Every producer goes through here so the trim/branch pair cannot be
        forgotten at a new call site. Undoしてから新しく編集した場合、捨てる
        はずだったRedo列はブランチとして残し、あとから選び直せるようにする。
        """
        before = len(self.undo_stack)
        self.undo_stack.append(entry)
        trim_undo_stack(self.undo_stack)
        removed = before + 1 - len(self.undo_stack)
        if removed > 0:
            self._shift_history_branches(-removed)
        if self.redo_stack:
            # 分岐点は「今の編集を積む直前の状態」＝新エントリ1件手前。
            self._archive_redo_branch(len(self.undo_stack) - 1)
            self.redo_stack.clear()
        return entry

    @property
    def history_branches(self):
        """捨てられた未来（ブランチ）の一覧。初回アクセスで作る。"""
        branches = getattr(self, "_history_branches", None)
        if branches is None:
            branches = []
            self._history_branches = branches
        return branches

    def clear_history_branches(self):
        self.history_branches.clear()

    def _shift_history_branches(self, delta):
        """undo_stackの古い側が削られた分だけ分岐点をずらす（範囲外は破棄）。"""
        branches = self.history_branches
        shifted = []
        for branch in branches:
            branch.position += int(delta)
            if branch.position >= 0:
                shifted.append(branch)
        branches[:] = shifted

    def _archive_redo_branch(self, position):
        """現在のredo_stackを1本のブランチとして退避する。"""
        entries = list(self.redo_stack)
        if not entries:
            return None
        branch = HistoryBranch(
            max(0, int(position)),
            entries,
            history_label_for(entries[-1]),
        )
        branches = self.history_branches
        branches.append(branch)
        if len(branches) > MAX_HISTORY_BRANCHES:
            del branches[:-MAX_HISTORY_BRANCHES]
        return branch

    def switch_history_branch(self, branch):
        """分岐点まで巻き戻し、そのブランチの未来をRedo列として採用する。

        ``branch`` は :class:`HistoryBranch` か ``history_branches`` の添字。
        いま辿っていた未来は入れ替わりにブランチとして保存されるので、
        行ったり来たりできる。
        """
        self._commit_brush_stroke()
        branches = self.history_branches
        if isinstance(branch, int):
            if not (0 <= branch < len(branches)):
                return False
            branch = branches[branch]
        if branch not in branches:
            return False
        position = int(branch.position)
        if position > len(self.undo_stack):
            # 履歴が縮んで到達できなくなったブランチは捨てる。
            branches.remove(branch)
            return False
        for _ in range(len(self.undo_stack) - position):
            if not self.undo_stack:
                break
            self.undo()
        branches.remove(branch)
        # 巻き戻しで redo_stack に溜まった「いま辿っていた未来」を保存する。
        self._archive_redo_branch(len(self.undo_stack))
        self.redo_stack[:] = branch.entries
        self._invalidate_display_caches()
        return True

    def _invalidate_display_caches(self):
        """分岐復元後に表示の派生キャッシュを作り直す。"""
        self._color_filter_cache.clear()
        self._color_index_cache.clear()
        self._silhouette_cache.clear()
        self._onion_cache.clear()
        self._view_preview_cache.clear()
        self._playback_frame_cache.clear()
        scaled = getattr(self, "_display_image_cache", None)
        if scaled is not None:
            scaled.clear()
            self._display_image_cache_bytes = 0
        self.update()

    def push_doc_undo(self):
        self._commit_brush_stroke()
        self.push_undo(DocUndo(self.document_snapshot()))

    def push_layer_undo(self):
        layer = self.active_layer
        self.push_undo(LayerUndo(
            int(self.current_frame),
            int(self.active_layer_index),
            snapshot_image(layer.image),
            bool(layer.has_content),
            layer_name=layer.name, number=layer.sequence_number,
        ))

    def push_layer_region_undo(self, rect):
        """Store only the part of the active layer an operation can change."""
        entry = self._capture_layer_region_undo(rect)
        if entry is None:
            return False
        self.push_undo(entry)
        return True

    def _capture_layer_region_undo(self, rect):
        layer = self.active_layer
        image = layer.image
        rect = QRect(rect).intersected(QRect(0, 0, image.width, image.height))
        if rect.isEmpty():
            return None
        return LayerRegionUndo(
            int(self.current_frame),
            int(self.active_layer_index),
            rect,
            image.read_qimage(*rect.getRect()),
            bool(layer.has_content),
            layer_name=layer.name, number=layer.sequence_number,
        )

    def apply_undo_entry(self, e):
        if isinstance(e, TransformUndo):
            self.transform_points = [QPointF(x, y) for x, y in e.points]
            self.transform_mesh_cols = self._active_mesh_cols = e.cols
            self.transform_mesh_rows = self._active_mesh_rows = e.rows
            self.transform_mesh_grid = e.cols
            self.transform_mesh_reference_points = [QPointF(x, y) for x, y in e.reference_points]
            self.transform_drag_kind = None
            self._invalidate_tp_preview_cache()
            self.selectionChanged.emit()
        elif isinstance(e, InitialStateUndo):
            changes = self._document.set_initial_images(e.cells, e.archived, restoring=True)
            publish_initial_images(self, changes, restoring=True)
            return
        elif isinstance(e, (LayerUndo, LayerRegionUndo, LayerTilesUndo)):
            patches = None
            if not isinstance(e, LayerUndo):
                source = e.tiles if isinstance(e, LayerTilesUndo) else ((e.rect, e.image),)
                patches = tuple((rect.getRect(), image) for rect, image in source)
            changes = self._document.restore_drawing(
                e.frame, e.layer, e.number, getattr(e, "image", None), e.has_content,
                sheet=self.timeline_mode == "sheet", patches=patches,
            )
            publish_restored_drawing(self, changes)
            return
        elif isinstance(e, PaletteStateUndo):
            # 使用色パネルの並び順・親子プレビュー・表示/マスクを復元する。
            palette = getattr(self, "_palette", None)
            if palette is not None:
                palette.restore_history_state(e.state)
        elif isinstance(e, ExposureUndo):
            publish_restored_exposure(self, self._document.restore_exposure(e))
            return
        elif isinstance(e, DocUndo):
            publish_restored_document(self, self._document.restore_snapshot(e.snapshot))
            return
        elif isinstance(e, (LayerBatchUndo, ScopedBatchUndo)):
            scoped = isinstance(e, ScopedBatchUndo)
            if not scoped:
                self.active_layer_index = e.layer
            cells = e.cells if scoped else (
                (fi, e.layer, img, hc, number) for fi, img, hc, number in e.cells
            )
            for fi, li, img, hc, number in cells:
                for changes in self._document.restore_batch_drawing(
                    fi, li, img, hc, number, sheet=self.timeline_mode == "sheet",
                ):
                    publish_drawing_cells(self, changes)
            finish_restored_batch(self, scoped=scoped, first_layer=e.cells[0][1] if scoped and e.cells else None)
            return
        elif isinstance(e, LayerRemoveUndo):
            publish_track_changes(self, self._document.restore_removed_track(e))
            return
        elif isinstance(e, LayerInsertUndo):
            publish_track_changes(self, self._document.restore_inserted_track(e))
            return
        else:
            log.warning("unknown undo entry type: %r", type(e).__name__)
            return
        self.update()

    def undo(self):
        self._commit_brush_stroke()
        if self.transform_active and (not self.undo_stack or not isinstance(self.undo_stack[-1], TransformUndo)):
            self.cancel_selection_transform()
            return
        while self.undo_stack:
            entry = self.undo_stack.pop()
            current = self.current_state_for(entry)
            if current is None:
                # タイムラインの削除・正規化後に残った、既に存在しない
                # セルの古い履歴は安全に破棄する。
                continue
            self.redo_stack.append(current)
            self.apply_undo_entry(entry)
            return

    def redo(self):
        self._commit_brush_stroke()
        if self.transform_active and (not self.redo_stack or not isinstance(self.redo_stack[-1], TransformUndo)):
            return
        while self.redo_stack:
            entry = self.redo_stack.pop()
            current = self.current_state_for(entry)
            if current is None:
                continue
            self.undo_stack.append(current)
            self.apply_undo_entry(entry)
            return
