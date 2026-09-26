"""Undo/redo stacks for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods push document / layer /
region snapshots onto the undo stack and apply them on undo/redo. They run
against a live ``PaintCanvas`` instance and its ``_document`` + stacks.
"""
from PySide6.QtCore import QRect
from PySide6.QtGui import QPainter
from .constants import MAX_UNDO, MAX_UNDO_BYTES
from ._canvas_members import CanvasMembers
from . import constants
from .models import Layer, default_layer_name
from .utils import blank_image
from .logging_setup import get_logger
from .undo_entries import (
    DocUndo,
    LayerBatchUndo,
    LayerInsertUndo,
    LayerRegionUndo,
    LayerRemoveUndo,
    LayerTilesUndo,
    LayerUndo,
    PaletteStateUndo,
    ScopedBatchUndo,
    TweenBatchUndo,
    history_label_for,
)

log = get_logger(__name__)

__all__ = [
    "HistoryBranch",
    "MAX_HISTORY_BRANCHES",
    "UndoMixin",
    "history_label_for",
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
        ))
        return True

    def apply_undo_entry(self, e):
        if isinstance(e, LayerTilesUndo):
            if (
                0 <= e.frame < len(self.frames)
                and 0 <= e.layer < len(self.frames[e.frame].layers)
            ):
                fi, li = e.frame, e.layer
                self.current_frame = fi
                self.active_layer_index = li
                layer = self.frames[fi].layers[li]
                painter = QPainter(layer.image)
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Source
                )
                for rect, image in e.tiles:
                    painter.drawImage(QRect(rect).topLeft(), image)
                painter.end()
                layer.has_content = bool(e.has_content)
                self.cellChanged.emit(fi, li)
                self.selectionChanged.emit()
        elif isinstance(e, LayerRegionUndo):
            if (
                0 <= e.frame < len(self.frames)
                and 0 <= e.layer < len(self.frames[e.frame].layers)
            ):
                fi, li = e.frame, e.layer
                self.current_frame = fi
                self.active_layer_index = li
                layer = self.frames[fi].layers[li]
                painter = QPainter(layer.image)
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Source
                )
                painter.drawImage(QRect(e.rect).topLeft(), e.image)
                painter.end()
                layer.has_content = bool(e.has_content)
                self.cellChanged.emit(fi, li)
                self.selectionChanged.emit()
        elif isinstance(e, PaletteStateUndo):
            # 使用色パネルの並び順・親子プレビュー・表示/マスクを復元する。
            palette = getattr(self, "_palette", None)
            if palette is not None:
                palette.restore_history_state(e.state)
        elif isinstance(e, DocUndo):
            fs, cf, al, w, h = e.snapshot
            constants.CANVAS_WIDTH = w
            constants.CANVAS_HEIGHT = h
            # The entry was popped before application and its inverse has
            # already been captured. Transfer the stored snapshot directly;
            # cloning every frame/layer/image again only adds latency.
            self.frames = fs
            self.current_frame = cf
            self.active_layer_index = al
            self.coalesce_numbered_images()
            self.changed.emit()
        elif isinstance(e, LayerBatchUndo):
            li = e.layer
            self.active_layer_index = li
            for fi, img, hc in e.cells:
                if 0 <= fi < len(self.frames) and 0 <= li < len(self.frames[fi].layers):
                    self.frames[fi].layers[li].image = img
                    self.frames[fi].layers[li].has_content = hc
                    self.cellChanged.emit(fi, li)
            self.selectionChanged.emit()
        elif isinstance(e, ScopedBatchUndo):
            # 一括処理ランナー（frame_scope）1回分。レイヤーを跨いで復元する。
            for fi, li, img, hc in e.cells:
                if (
                    0 <= fi < len(self.frames)
                    and 0 <= li < len(self.frames[fi].layers)
                ):
                    self.frames[fi].layers[li].image = img
                    self.frames[fi].layers[li].has_content = hc
                    self.cellChanged.emit(fi, li)
            if e.cells:
                self.active_layer_index = max(
                    0,
                    min(int(e.cells[0][1]), len(self.layers) - 1),
                )
            self._onion_cache.clear()
            self.selectionChanged.emit()
        elif isinstance(e, LayerRemoveUndo):
            index = e.index
            for frame in self.frames:
                if 0 <= index < len(frame.layers) and len(frame.layers) > 1:
                    frame.layers.pop(index)
            self.active_layer_index = max(
                0,
                min(int(e.target_active), len(self.layers) - 1),
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        elif isinstance(e, LayerInsertUndo):
            index = e.index
            stored_layers = e.layers
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
                frame.layers.insert(
                    max(0, min(int(index), len(frame.layers))),
                    layer,
                )
            self.active_layer_index = max(
                0,
                min(int(e.target_active), len(self.layers) - 1),
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        elif isinstance(e, TweenBatchUndo):
            li = e.layer
            restore_count = max(1, int(e.restore_count))
            if len(self.frames) < restore_count:
                self._ensure_frame_count(restore_count)
            for fi, image, has_content, exposure in e.cells:
                if (
                    0 <= fi < len(self.frames)
                    and 0 <= li < len(self.frames[fi].layers)
                ):
                    layer = self.frames[fi].layers[li]
                    layer.image = image
                    layer.has_content = bool(has_content)
                    layer.exposure = max(1, int(exposure))
            if len(self.frames) > restore_count:
                del self.frames[restore_count:]
            self.current_frame = max(
                0, min(int(e.start), len(self.frames) - 1)
            )
            self.active_layer_index = max(
                0, min(int(li), len(self.layers) - 1)
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        elif isinstance(e, LayerUndo):
            fi, li = e.frame, e.layer
            self.current_frame = fi
            self.active_layer_index = li
            self.frames[fi].layers[li].image = e.image
            self.frames[fi].layers[li].has_content = e.has_content
            self.cellChanged.emit(fi, li)
            self.selectionChanged.emit()
        else:
            log.warning("unknown undo entry type: %r", type(e).__name__)
            return
        self.update()

    def undo(self):
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
        while self.redo_stack:
            entry = self.redo_stack.pop()
            current = self.current_state_for(entry)
            if current is None:
                continue
            self.undo_stack.append(current)
            self.apply_undo_entry(entry)
            return
