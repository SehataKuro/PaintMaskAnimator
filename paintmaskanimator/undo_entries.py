"""Typed undo/redo stack entries.

Undo entries used to be tag-dispatched tuples (``("layer", fi, li, img, hc)``
and seven other shapes), which meant every producer and consumer agreed on
positional indices by convention only -- unreadable, uncheckable, and hostile
to refactoring. Each shape is a dataclass here instead, so a wrong field name
is a type error rather than a silent ``IndexError`` at undo time.

Each entry also reports ``nbytes``, letting the stack be bounded by memory
rather than by entry count alone (a 4K full-layer snapshot and a 32x32 tile
patch are not remotely the same cost).

The classes are deliberately dumb records: the logic that captures the inverse
state and re-applies an entry lives on ``PaintCanvas`` (``current_state_for`` /
``apply_undo_entry``), because it needs the live document.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Sequence, Tuple

from .i18n import tr


def _image_bytes(image) -> int:
    """Best-effort in-memory size of a QImage (0 when it is not one)."""
    try:
        return int(image.sizeInBytes())
    except (AttributeError, TypeError):
        return 0


@dataclass
class UndoEntry:
    """Base class for every undo/redo stack entry."""

    #: Label shown in the history panel. Subclasses override.
    HISTORY_LABEL = "編集"

    @property
    def history_label(self) -> str:
        return self.HISTORY_LABEL

    @property
    def nbytes(self) -> int:
        """Approximate retained image memory, for stack trimming."""
        return 0


@dataclass
class DocUndo(UndoEntry):
    """Whole-document snapshot: ``(frames, current_frame, active_layer, w, h)``."""

    HISTORY_LABEL = "全体編集"

    snapshot: Any

    @property
    def nbytes(self) -> int:
        try:
            frames = self.snapshot[0]
        except (TypeError, IndexError):
            return 0
        return sum(
            _image_bytes(layer.image)
            for frame in frames
            for layer in frame.layers
        )


@dataclass
class LayerUndo(UndoEntry):
    """Full snapshot of one layer image in one frame."""

    HISTORY_LABEL = "描画"

    frame: int
    layer: int
    image: Any
    has_content: bool

    @property
    def nbytes(self) -> int:
        return _image_bytes(self.image)


@dataclass
class LayerRegionUndo(UndoEntry):
    """Only the bounding box an operation could change."""

    HISTORY_LABEL = "描画"

    frame: int
    layer: int
    rect: Any
    image: Any
    has_content: bool

    @property
    def nbytes(self) -> int:
        return _image_bytes(self.image)


@dataclass
class LayerTilesUndo(UndoEntry):
    """Per-tile patches touched by a stroke: a sequence of ``(rect, image)``."""

    HISTORY_LABEL = "描画"

    frame: int
    layer: int
    tiles: Sequence[Tuple[Any, Any]]
    has_content: bool

    @property
    def nbytes(self) -> int:
        return sum(_image_bytes(image) for _rect, image in self.tiles)


@dataclass
class LayerBatchUndo(UndoEntry):
    """One layer across many frames: cells of ``(frame, image, has_content)``."""

    HISTORY_LABEL = "色編集"

    layer: int
    cells: List[Tuple[int, Any, bool]]

    @property
    def nbytes(self) -> int:
        return sum(_image_bytes(image) for _fi, image, _hc in self.cells)


@dataclass
class ScopedBatchUndo(UndoEntry):
    """一括処理ランナー1回分：``(frame, layer, image, has_content)`` のセル群。

    ``LayerBatchUndo`` が1レイヤー固定だったのに対し、こちらはレイヤーを跨げる。
    どれだけのコマ・レイヤーへ適用しても Undo は常にこの1件になる。
    """

    HISTORY_LABEL = "一括処理"

    cells: List[Tuple[int, int, Any, bool]]
    label: str = field(default="一括処理")

    @property
    def history_label(self) -> str:
        return self.label or self.HISTORY_LABEL

    @property
    def nbytes(self) -> int:
        return sum(_image_bytes(image) for _fi, _li, image, _hc in self.cells)


@dataclass
class LayerRemoveUndo(UndoEntry):
    """Undo of "add layer": drop the layer at ``index`` from every frame."""

    HISTORY_LABEL = "レイヤー構成"

    index: int
    target_active: int


@dataclass
class LayerInsertUndo(UndoEntry):
    """Undo of "delete layer": re-insert the stored per-frame layers."""

    HISTORY_LABEL = "レイヤー構成"

    index: int
    layers: List[Any]
    target_active: int

    @property
    def nbytes(self) -> int:
        return sum(_image_bytes(layer.image) for layer in self.layers)


@dataclass
class TweenBatchUndo(UndoEntry):
    """A tween run: cells of ``(frame, image, has_content, exposure)``.

    ``restore_count`` is the frame count to restore, since tweening can add or
    remove frames.
    """

    HISTORY_LABEL = "トゥイーン"

    layer: int
    restore_count: int
    start: int
    end: int
    cells: List[Tuple[int, Any, bool, int]]

    @property
    def nbytes(self) -> int:
        return sum(_image_bytes(image) for _fi, image, _hc, _exp in self.cells)


@dataclass
class PaletteStateUndo(UndoEntry):
    """Used-color panel state. Carries its own label per operation kind."""

    state: Any
    label: str = field(default="パレット編集")

    @property
    def history_label(self) -> str:
        return self.label or "パレット編集"


def history_label_for(entry) -> str:
    """Undoエントリ1件を、ヒストリー表示用の短いラベルに変換する。"""
    label = entry.history_label if isinstance(entry, UndoEntry) else tr("編集")
    return translate_history_label(label)


def translate_history_label(label: str) -> str:
    """Translate a history label at display time.

    ``HISTORY_LABEL`` lives in a class body, which is evaluated at import time --
    before the translator is installed -- so it holds the untranslated source and
    is translated here instead. ``pyside6-lupdate`` only sees a literal inside
    ``tr()``, hence the table.

    Entries that carry a runtime label (a palette edit, a scoped batch) pass it
    in already translated; an unknown key is returned unchanged, which is exactly
    what those need.
    """
    labels = {
        "編集": tr("編集"),
        "全体編集": tr("全体編集"),
        "描画": tr("描画"),
        "色編集": tr("色編集"),
        "一括処理": tr("一括処理"),
        "レイヤー構成": tr("レイヤー構成"),
        "トゥイーン": tr("トゥイーン"),
        "パレット編集": tr("パレット編集"),
    }
    return labels.get(label, label)
