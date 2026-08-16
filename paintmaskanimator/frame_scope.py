"""フレーム横断の一括処理ランナー。

「すべてのコマへ適用」は機能ごとに書き直されてきた（ゴミ取り、色置換、
変形、2値化…）。適用範囲の決定・保持セルの重複排除・進捗・キャンセル・
Undo のまとめ上げをここへ集約し、各操作は「1レイヤーを受け取って結果の
QImage を返す関数」だけを書けばよいようにする。

ランナーは Qt ウィジェットに依存しない（進捗とキャンセルは呼び出し側が
渡すコールバック）ので、UI なしで単体テストできる。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Tuple

from .undo_entries import LayerBatchUndo, ScopeBatchUndo
from .logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class FrameScope:
    """どのコマ・どのレイヤーに適用するか。"""

    frames: Tuple[int, ...]
    layers: Tuple[int, ...]
    selection_only: bool = False

    @classmethod
    def build(cls, frames, layers, selection_only=False):
        return cls(
            tuple(int(value) for value in frames),
            tuple(int(value) for value in layers),
            bool(selection_only),
        )

    @classmethod
    def current_frame(cls, canvas, layers=None, selection_only=False):
        return cls.build(
            [int(canvas.current_frame)],
            _default_layers(canvas, layers),
            selection_only,
        )

    @classmethod
    def all_frames(cls, canvas, layers=None, selection_only=False):
        return cls.build(
            range(len(canvas.frames)),
            _default_layers(canvas, layers),
            selection_only,
        )

    @classmethod
    def frame_range(cls, canvas, start, end, layers=None, selection_only=False):
        first, last = sorted((int(start), int(end)))
        return cls.build(
            range(max(0, first), min(last, len(canvas.frames) - 1) + 1),
            _default_layers(canvas, layers),
            selection_only,
        )


def _default_layers(canvas, layers):
    if layers is None:
        return [int(canvas.active_layer_index)]
    return [int(value) for value in layers]


@dataclass
class LayerContext:
    """`op` が受け取る1セル分の情報。"""

    canvas: Any
    frame_index: int
    layer_index: int
    layer: Any
    selection_only: bool = False

    @property
    def image(self):
        return self.layer.image


@dataclass
class ApplyResult:
    """一括処理1回の結果。"""

    cells: List[Tuple[int, int]] = field(default_factory=list)
    changed_pixels: int = 0
    cancelled: bool = False
    undo_entry: Optional[ScopeBatchUndo] = None

    @property
    def changed_cells(self) -> int:
        return len(self.cells)


def resolve_scope_cells(canvas, scope: FrameScope) -> List[Tuple[int, int]]:
    """適用範囲を、実際に書き換える (コマ, レイヤー) の並びへ落とす。

    保持区間（○セル）は同じキーフレーム画像を指すため、キーフレームへ
    寄せたうえで重複を除く。同じ絵を二重に処理しない。
    """
    cells: List[Tuple[int, int]] = []
    for layer_index in scope.layers:
        seen = set()
        for frame_index in scope.frames:
            if not (0 <= frame_index < len(canvas.frames)):
                continue
            key_frame = canvas.resolve_key_frame(int(frame_index), layer_index)
            if key_frame is None or key_frame in seen:
                continue
            frame = canvas.frames[key_frame]
            if not (0 <= layer_index < len(frame.layers)):
                continue
            if not frame.layers[layer_index].has_content:
                continue
            seen.add(key_frame)
            cells.append((int(key_frame), int(layer_index)))
    return cells


def apply_over_scope(
    canvas,
    scope: FrameScope,
    op: Callable[[LayerContext], Any],
    *,
    label: str = "一括処理",
    progress: Optional[Callable[[int, int, str], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    push_undo: bool = True,
) -> ApplyResult:
    """`scope` の各セルへ `op` を適用する。

    `op` は ``LayerContext`` を受け取り、``None``（変更なし）、新しい
    ``QImage``、または ``(QImage, 変更画素数)`` を返す。

    キャンセルされた場合は、それまでに書き換えたセルを元へ戻してから
    ``cancelled=True`` の結果を返す。部分適用も Undo エントリも残さない。
    """
    cells = resolve_scope_cells(canvas, scope)
    result = ApplyResult()
    if not cells:
        return result

    originals: List[Tuple[int, int, Any, bool]] = []
    total = len(cells)
    for counter, (frame_index, layer_index) in enumerate(cells, 1):
        if cancelled is not None and cancelled():
            _restore(canvas, originals)
            result.cells = []
            result.changed_pixels = 0
            result.cancelled = True
            return result

        if progress is not None:
            progress(counter - 1, total, f"コマ {frame_index + 1} を処理しています")

        layer = canvas.frames[frame_index].layers[layer_index]
        context = LayerContext(
            canvas=canvas,
            frame_index=frame_index,
            layer_index=layer_index,
            layer=layer,
            selection_only=scope.selection_only,
        )
        outcome = op(context)
        if outcome is None:
            continue
        if isinstance(outcome, tuple):
            image, changed_pixels = outcome
        else:
            image, changed_pixels = outcome, 0
        if image is None:
            continue

        originals.append(
            (frame_index, layer_index, layer.image.copy(), bool(layer.has_content))
        )
        layer.image = image
        layer.has_content = True
        result.cells.append((frame_index, layer_index))
        result.changed_pixels += int(changed_pixels)

    if progress is not None:
        progress(total, total, "処理が完了しました")

    if not originals:
        return result

    if push_undo:
        result.undo_entry = canvas.push_undo(_batch_undo(originals, label))
    else:
        result.undo_entry = _batch_undo(originals, label)
    return result


def _batch_undo(originals, label) -> ScopeBatchUndo:
    """レイヤーごとにまとめ、Undo 1回で全セルが戻るエントリにする。"""
    per_layer = {}
    for frame_index, layer_index, image, has_content in originals:
        per_layer.setdefault(layer_index, []).append(
            (frame_index, image, has_content)
        )
    return ScopeBatchUndo(
        [
            LayerBatchUndo(layer_index, cells)
            for layer_index, cells in sorted(per_layer.items())
        ],
        label,
    )


def _restore(canvas, originals: Sequence[Tuple[int, int, Any, bool]]) -> None:
    for frame_index, layer_index, image, has_content in reversed(originals):
        if not (0 <= frame_index < len(canvas.frames)):
            continue
        frame = canvas.frames[frame_index]
        if not (0 <= layer_index < len(frame.layers)):
            continue
        frame.layers[layer_index].image = image
        frame.layers[layer_index].has_content = has_content
