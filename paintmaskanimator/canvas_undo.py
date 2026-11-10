"""Undo/redo stacks for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods push document / layer /
region snapshots onto the undo stack and apply them on undo/redo. They run
against a live ``PaintCanvas`` instance and its ``_document`` + stacks.
"""
from copy import deepcopy
from .document import release_slots
from PySide6.QtCore import QRect, QPointF
from PySide6.QtGui import QPainter
from .constants import MAX_UNDO, MAX_UNDO_BYTES
from ._canvas_members import CanvasMembers
from . import constants
from .models import Layer, default_layer_name
from .utils import blank_image
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
        # 分岐を行き来すると多数の QImage が生成・破棄され、QImage.cacheKey() が
        # 使い回されることがある。表示キャッシュはこのキーで引くため、別分岐の
        # 表示（矩形状の描画パッチ）が残って見えることがある。分岐切り替え時は
        # キャッシュを捨て、次の描画で実ピクセルから作り直させる。
        self._invalidate_display_caches()
        return True

    def _invalidate_display_caches(self):
        """QImage.cacheKey() で引く表示派生キャッシュを一括で捨てる。

        分岐を行き来すると QImage が生成・破棄され cacheKey() が使い回されるため、
        別状態の派生画像が残って見えることがある。分岐切り替え時に捨て、次の描画で
        実ピクセルから作り直させる。
        """
        self._color_filter_cache.clear()
        self._color_index_cache.clear()
        self._silhouette_cache.clear()
        self._onion_cache.clear()
        self.update()

    def push_doc_undo(self):
        self.push_undo(DocUndo(self.document_snapshot()))

    def push_layer_undo(self):
        layer = self.active_layer
        self.push_undo(LayerUndo(
            int(self.current_frame),
            int(self.active_layer_index),
            layer.image.copy(),
            bool(layer.has_content),
            layer_name=layer.name, number=layer.sequence_number,
        ))

    def push_layer_region_undo(self, rect):
        """Store only the part of the active layer an operation can change."""
        layer = self.active_layer
        rect = QRect(rect).intersected(layer.image.rect())
        if rect.isEmpty():
            return False
        self.push_undo(LayerRegionUndo(
            int(self.current_frame),
            int(self.active_layer_index),
            rect,
            layer.image.copy(rect),
            bool(layer.has_content),
            layer_name=layer.name, number=layer.sequence_number,
        ))
        return True

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
            restored = set()
            for fi, li, image in e.cells:
                if 0 <= fi < len(self.frames) and 0 <= li < len(self.frames[fi].layers):
                    layer = self.frames[fi].layers[li]
                    if layer.cell_id not in restored:
                        layer.initial_image = image
                        restored.add(layer.cell_id)
            for li, number, image in e.archived:
                if 0 <= li < len(self._document.tracks):
                    track = self._document.tracks[li]
                    cell = track.cells.get(number) if number in track.legacy_archive_hints else None
                    if cell is not None and id(cell) not in restored:
                        cell.initial_image = image
                        restored.add(id(cell))
            self._playback_frame_cache.clear()
            self.changed.emit()
        elif isinstance(e, (LayerUndo, LayerRegionUndo, LayerTilesUndo)):
            target = self._resolve_drawing_undo_target(e)
            if target is None:
                return
            fi, layer = target
            if isinstance(e, LayerUndo):
                layer.image = e.image
            else:
                painter = QPainter(layer.image)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                patches = e.tiles if isinstance(e, LayerTilesUndo) else ((e.rect, e.image),)
                for rect, image in patches:
                    painter.drawImage(QRect(rect).topLeft(), image)
                painter.end()
            layer.has_content = bool(e.has_content)
            if fi is not None:
                self.current_frame = fi
                self.active_layer_index = e.layer
                self.cellChanged.emit(fi, e.layer)
            self.selectionChanged.emit()
        elif isinstance(e, PaletteStateUndo):
            # 使用色パネルの並び順・親子プレビュー・表示/マスクを復元する。
            palette = getattr(self, "_palette", None)
            if palette is not None:
                palette.restore_history_state(e.state)
        elif isinstance(e, ExposureUndo):
            self._ensure_frame_count(e.frame_count)
            for frame in self.frames[e.frame_count:]:
                release_slots(frame.layers)
            del self.frames[e.frame_count:]
            for frame, values in zip(self.frames[e.column:], e.references):
                slot = frame.layers[e.layer]
                content, blank, start, number, pending, tween, member, imported, position = values
                slot.has_content = False
                slot.sequence_number = number
                slot.has_content = content
                slot.is_blank_key = blank
                slot.blank_start = start
                slot.set_pending(pending)
                slot.tween = deepcopy(tween)
                slot.tween_member = member
                for name, value in (("_dense_import", imported), ("_v1_position", position)):
                    if value is None:
                        slot.__dict__.pop(name, None)
                    else:
                        slot.__dict__[name] = value
            self.current_frame = e.current_frame
            self.active_layer_index = e.active_layer
            self._invalidate_display_caches()
            self.changed.emit()
            self.selectionChanged.emit()
        elif isinstance(e, DocUndo):
            w, h = self._document.restore(e.snapshot)
            constants.CANVAS_WIDTH = w
            constants.CANVAS_HEIGHT = h
            self._invalidate_exposure_index()
            self.link_layer_tracks()
            self.changed.emit()
        elif isinstance(e, (LayerBatchUndo, ScopedBatchUndo)):
            scoped = isinstance(e, ScopedBatchUndo)
            if not scoped:
                self.active_layer_index = e.layer
            for cell in e.cells:
                fi, li, img, hc, number = cell if scoped else (cell[0], e.layer, *cell[1:])
                target = self._resolve_numbered_target(fi, li, number)
                if target is None:
                    continue
                resolved_fi, layer = target
                layer.image = img
                if number is None:
                    layer.has_content = hc
                    self.cellChanged.emit(fi, li)
                else:
                    if resolved_fi is None:
                        layer.has_content = hc
                    for sheet_fi, frame in enumerate(self.frames):
                        if 0 <= li < len(frame.layers):
                            sheet_layer = frame.layers[li]
                            former_content = (
                                sheet_layer._track is layer._track
                                and sheet_layer.sequence_number == number
                                and sheet_layer.__dict__.get("_content_origin") == number
                            )
                            if sheet_layer.cell_id == layer.cell_id or former_content:
                                sheet_layer.has_content = hc
                                self.cellChanged.emit(sheet_fi, li)
            if scoped:
                if e.cells:
                    self.active_layer_index = max(
                        0, min(int(e.cells[0][1]), len(self.layers) - 1),
                    )
                self._onion_cache.clear()
            self.selectionChanged.emit()
        elif isinstance(e, LayerRemoveUndo):
            index = e.index
            for frame in self.frames:
                if 0 <= index < len(frame.layers) and len(frame.layers) > 1:
                    release_slots([frame.layers[index]])
                    frame.layers.pop(index)
            self._document.sync_columns()
            self.active_layer_index = max(
                0,
                min(int(e.target_active), len(self.layers) - 1),
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        elif isinstance(e, LayerInsertUndo):
            index = e.index
            from .document import clone_frame_graph
            from .models import Frame
            copied, _ = clone_frame_graph([Frame([layer]) for layer in e.layers])
            stored_layers = [frame.layers[0] for frame in copied]
            for frame_index, frame in enumerate(self.frames):
                if frame_index < len(stored_layers):
                    layer = stored_layers[frame_index]
                else:
                    layer = Layer(
                        default_layer_name(index),
                        blank_image(),
                        visible=True,
                        opacity=1.0,
                    )
                if stored_layers:
                    layer.share_track_with(stored_layers[0])
                frame.layers.insert(
                    max(0, min(int(index), len(frame.layers))),
                    layer,
                )
            self._document.sync_columns()
            self.active_layer_index = max(
                0, min(int(e.target_active), len(self.layers) - 1))
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        else:
            log.warning("unknown undo entry type: %r", type(e).__name__)
            return
        self.update()

    def undo(self):
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
