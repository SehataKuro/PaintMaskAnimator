"""フレーム横断の一括適用ランナー（Issue #9）。

「すべてのコマに適用する」仕組みは、これまで機能ごとに書かれていた。ゴミ取りは
``tools.dust_all_frames`` を、選択変形は ``tools.selection_all_frames`` と
``canvas.transform_apply_all_frames`` を各自で参照し、進捗ダイアログの回し方も
Undo のまとめ方もその場ごとに組み立てられていた。新しい操作を足すたびに
「全コマ対応」を書き直す構造になっていた、ということ。

このモジュールはその共通部分だけを引き受ける。呼び出し側が渡すのは
**1レイヤーを受け取って結果画像を返す関数**（``op``）と、どのセルに適用するかを
表す :class:`FrameScope` の2つだけ。走査順・保持コマの重複除去・共有画像の扱い・
進捗通知・キャンセル・Undo へのまとめ上げは :func:`apply_over_scope` が持つ。

UI から独立している（QWidget を触らない）ので、GUI なしで単体テストできる。
``doc`` に要求するのは ``frames`` 属性だけで、``resolve_key_frame`` と
``push_undo`` は在れば使う。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

from .logging_setup import get_logger
from .undo_entries import ScopedBatchUndo

log = get_logger(__name__)

__all__ = [
    "FrameScope",
    "LayerContext",
    "ScopeResult",
    "ScopeCancelled",
    "apply_over_scope",
    "resolve_cells",
    "resolve_key_frame",
]


class ScopeCancelled(Exception):
    """``op`` 側から実行を中断するための例外。部分適用は破棄される。"""


def resolve_key_frame(frames, column, layer_index):
    """``column`` のセルが参照している実体キーフレームの番号を返す。

    ``canvas_key_frame.resolve_key_frame`` と同じ規則の、ドキュメントを必要と
    しない純関数版。保持セルは実体を持つ手前のコマを指し、未使用セルと空キー
    （``is_blank_key``）は ``None`` を返す。
    """
    if not frames:
        return None
    column = max(0, min(int(column), len(frames) - 1))
    for key_column in range(column, -1, -1):
        layers = frames[key_column].layers
        if layer_index >= len(layers):
            continue
        layer = layers[layer_index]
        if layer.has_content:
            return (
                key_column
                if column < key_column + max(1, int(layer.exposure))
                else None
            )
        if getattr(layer, "is_blank_key", False):
            return None
    return None


@dataclass(frozen=True)
class FrameScope:
    """どのコマ・どのレイヤーに適用するか。

    これまで各ツールが持っていた「すべてのコマ」チェックボックスの状態は、
    最終的にこの1つの値へ集約される。
    """

    frames: Tuple[int, ...]
    layers: Tuple[int, ...]
    selection_only: bool = False
    #: 保持セルを実体キーフレームへ畳むか。False にすると指定コマをそのまま辿る。
    key_frames_only: bool = True

    def __post_init__(self):
        object.__setattr__(
            self, "frames", tuple(int(value) for value in self.frames)
        )
        object.__setattr__(
            self, "layers", tuple(int(value) for value in self.layers)
        )

    @classmethod
    def current_frame(cls, doc, *, layers=None, selection_only=False):
        """現在コマ×アクティブレイヤー（既定のスコープ）。"""
        return cls(
            (int(doc.current_frame),),
            _default_layers(doc, layers),
            selection_only,
        )

    @classmethod
    def all_frames(cls, doc, *, layers=None, selection_only=False):
        """全コマ×指定レイヤー（既定はアクティブレイヤー）。"""
        return cls(
            tuple(range(len(doc.frames))),
            _default_layers(doc, layers),
            selection_only,
        )

    @classmethod
    def cells(cls, frames, layers, *, selection_only=False):
        """タイムラインで選んだセル範囲などを、そのままスコープにする。"""
        return cls(tuple(frames), tuple(layers), selection_only)

    @classmethod
    def choose(cls, doc, all_frames, *, layers=None, selection_only=False):
        """真偽値1つで現在コマ／全コマを切り替える移行用のヘルパー。

        既存の「すべてのコマ」チェックボックスを段階的に置き換えるための入口。
        """
        factory = cls.all_frames if all_frames else cls.current_frame
        return factory(doc, layers=layers, selection_only=selection_only)

    @property
    def is_all_frames(self) -> bool:
        return len(self.frames) > 1


def _default_layers(doc, layers):
    if layers is not None:
        return tuple(int(value) for value in layers)
    return (int(getattr(doc, "active_layer_index", 0)),)


@dataclass
class LayerContext:
    """``op`` に渡る1セル分の入力。"""

    doc: Any
    frame: int
    layer_index: int
    layer: Any
    selection_only: bool
    #: このセルと画像実体を共有しているセル（``(frame, layer)``）。
    shared_cells: Tuple[Tuple[int, int], ...] = ()

    @property
    def image(self):
        return self.layer.image


@dataclass
class ScopeResult:
    """1回の一括適用の結果。"""

    label: str
    changed_cells: int = 0
    changed_pixels: int = 0
    changed_frames: List[int] = field(default_factory=list)
    #: 変更した ``(frame, layer)`` の一覧（レイヤーを跨ぐ通知のため）。
    changed_cell_list: List[Tuple[int, int]] = field(default_factory=list)
    undo: Optional[ScopedBatchUndo] = None
    cancelled: bool = False
    #: 走査したセル数（変更の有無を問わない）。
    visited_cells: int = 0

    @property
    def changed(self) -> bool:
        return self.changed_cells > 0

    def __bool__(self) -> bool:
        return self.changed


def resolve_cells(doc, scope: FrameScope) -> List[Tuple[int, int]]:
    """スコープを、実際に処理する ``(frame, layer)`` の一覧へ展開する。

    保持セルは実体キーフレームへ畳み、同じセルは1度だけ返す。走査順はコマ番号
    →レイヤー番号の昇順で安定させる（進捗表示とテストのため）。
    """
    frames = getattr(doc, "frames", None) or []
    resolver = getattr(doc, "resolve_key_frame", None)
    seen = set()
    cells: List[Tuple[int, int]] = []
    for layer_index in scope.layers:
        if layer_index < 0:
            continue
        for frame_index in scope.frames:
            if not 0 <= frame_index < len(frames):
                continue
            if scope.key_frames_only:
                if resolver is not None:
                    key_frame = resolver(int(frame_index), int(layer_index))
                else:
                    key_frame = resolve_key_frame(
                        frames, frame_index, layer_index
                    )
                if key_frame is None:
                    continue
            else:
                key_frame = int(frame_index)
            if layer_index >= len(frames[key_frame].layers):
                continue
            cell = (int(key_frame), int(layer_index))
            if cell in seen:
                continue
            seen.add(cell)
            cells.append(cell)
    cells.sort()
    return cells


def _group_shared_images(doc, cells):
    """画像実体を共有するセルをまとめる。

    連番セルは ``coalesce_numbered_images`` で同じ ``QImage`` を指すことがある。
    セルごとに個別処理すると、片方だけが新しい画像に差し替わって共有が壊れる
    ので、共有グループは1回だけ処理して結果を全員へ配る。
    """
    frames = doc.frames
    groups: List[Tuple[Tuple[int, int], List[Tuple[int, int]]]] = []
    index_of = {}
    for frame_index, layer_index in cells:
        layer = frames[frame_index].layers[layer_index]
        key = id(layer.image)
        position = index_of.get(key)
        if position is None:
            index_of[key] = len(groups)
            groups.append(((frame_index, layer_index), [(frame_index, layer_index)]))
        else:
            groups[position][1].append((frame_index, layer_index))
    return groups


def apply_over_scope(
    doc,
    scope: FrameScope,
    op: Callable[[LayerContext], Optional[Any]],
    *,
    label: str,
    progress: Optional[Callable[[int, int, Tuple[int, int]], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    push_undo: bool = True,
    count_pixels: Optional[Callable[[LayerContext, Any], int]] = None,
) -> ScopeResult:
    """``scope`` の各セルへ ``op`` を適用する。

    ``op`` は「1レイヤーを受け取り、変更後の画像を返す（変更なしなら ``None``）」
    純粋な関数として書く。渡された ``layer.image`` を破壊的に書き換えてはいけない
    ――キャンセル時に部分適用を残さないため、結果はすべての ``op`` が終わるまで
    ドキュメントへ書き戻さない。

    :param progress: ``(done, total, cell)`` を受け取る通知。UI 側が進捗ダイアログを
        回すための唯一の接点。
    :param should_cancel: 各セルの前に呼ばれ、``True`` なら中断する。中断時は
        ドキュメントを一切変更せず ``cancelled=True`` の結果を返す。
    :param count_pixels: 変更画素数をメッセージに出したい呼び出し元向け。省略時は
        変更セル数だけを数える。
    :returns: :class:`ScopeResult`。``push_undo`` が真なら Undo は
        :class:`~.undo_entries.ScopedBatchUndo` 1件として積まれる（＝Undo 1回で全復元）。
    """
    result = ScopeResult(label=label)
    cells = resolve_cells(doc, scope)
    if not cells:
        return result

    groups = _group_shared_images(doc, cells)
    total = len(groups)
    pending: List[Tuple[Tuple[int, int], List[Tuple[int, int]], Any, int]] = []

    for done, (lead_cell, shared_cells) in enumerate(groups):
        if should_cancel is not None and should_cancel():
            result.cancelled = True
            return result
        if progress is not None:
            progress(done, total, lead_cell)

        frame_index, layer_index = lead_cell
        layer = doc.frames[frame_index].layers[layer_index]
        context = LayerContext(
            doc=doc,
            frame=frame_index,
            layer_index=layer_index,
            layer=layer,
            selection_only=scope.selection_only,
            shared_cells=tuple(shared_cells),
        )
        try:
            produced = op(context)
        except ScopeCancelled:
            result.cancelled = True
            return result

        result.visited_cells += len(shared_cells)
        if produced is None:
            continue
        changed_pixels = (
            int(count_pixels(context, produced)) if count_pixels is not None else 0
        )
        pending.append((lead_cell, shared_cells, produced, changed_pixels))

    if progress is not None:
        progress(total, total, cells[-1])

    if not pending:
        return result

    undo_cells = []
    for _lead_cell, shared_cells, produced, changed_pixels in pending:
        for frame_index, layer_index in shared_cells:
            layer = doc.frames[frame_index].layers[layer_index]
            undo_cells.append((
                int(frame_index),
                int(layer_index),
                layer.image.copy(),
                bool(layer.has_content),
            ))
            layer.image = produced
            layer.has_content = True
            result.changed_cells += 1
            result.changed_frames.append(int(frame_index))
            result.changed_cell_list.append((int(frame_index), int(layer_index)))
        result.changed_pixels += changed_pixels

    if push_undo:
        entry = ScopedBatchUndo(undo_cells, label)
        pusher = getattr(doc, "push_undo", None)
        if pusher is not None:
            pusher(entry)
        result.undo = entry
    else:
        result.undo = ScopedBatchUndo(undo_cells, label)
    return result
