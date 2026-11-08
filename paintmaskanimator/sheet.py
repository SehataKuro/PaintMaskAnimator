"""シートを参照の表として読み出す（Issue #31 Phase 3 第1段階）。

既存の Layer から読む口だけを追加し、露出や保存形式は変えない。
列全体の読み出しは、この表を通して行う。
第4段階では、明示コマの移動と番号列の区間圧縮を行う配列操作を追加した。
設計は docs/sequence-first-data-model.md。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from . import native


SHEET_EMPTY = 0
SHEET_BLANK = 1
SHEET_CELL = 2


class _Blank:
    def __repr__(self):
        return "BLANK"


class _Empty:
    def __repr__(self):
        return "EMPTY"


class _Unnumbered:
    """番号の無い絵の移行期の番兵。§6.3 の番号振りが入ったら消える。"""

    def __repr__(self):
        return "UNNUMBERED"


BLANK = _Blank()
EMPTY = _Empty()
UNNUMBERED = _Unnumbered()


@dataclass(frozen=True)
class SheetEntry:
    """1コマの参照。トゥイーンの指定は明示コマそのものだけに付ける。"""

    number: int | _Blank | _Empty | _Unnumbered
    tween: dict | None = None
    tween_member: str | None = None

    @property
    def is_blank(self):
        return self.number is BLANK

    @property
    def is_empty(self):
        return self.number is EMPTY

    @property
    def is_cell(self):
        return not (self.is_blank or self.is_empty)


def sheet_from_frames(frames) -> list[list[SheetEntry]]:
    """Layer を変更せず、表示中の絵番号と明示コマの指定を写した表を返す。"""
    result = []
    for column, row in enumerate(native.sheet_entries(frames)):
        entries = []
        for layer_index, entry in enumerate(row):
            if entry.kind == SHEET_EMPTY:
                number = EMPTY
            elif entry.kind == SHEET_BLANK:
                number = BLANK
            else:
                number = entry.number if entry.number is not None else UNNUMBERED
            layer = frames[column].layers[layer_index] if entry.key == column else None
            entries.append(SheetEntry(
                number,
                deepcopy(layer.tween) if layer is not None else None,
                layer.tween_member if layer is not None else None,
            ))
        result.append(entries)
    return result


def sheet_numbers(frames, layer_index) -> list[int | None]:
    """レイヤー1本の絵番号列。空セル・未使用・番号の無い絵は None。"""
    return [
        row[layer_index].number
        if 0 <= layer_index < len(row) and isinstance(row[layer_index].number, int)
        else None
        for row in sheet_from_frames(frames)
    ]


def shift_slots_right(slots, start, amount):
    """指定位置以降の明示コマを右へ移した新しい列を返す。"""
    start = max(0, int(start))
    amount = max(1, int(amount))
    result = list(slots)
    required_count = max(
        [len(slots), start + amount]
        + [column + amount + 1 for column in range(start, len(slots))
           if slots[column] is not None]
    )
    result.extend([None] * (required_count - len(result)))
    for column in range(start, len(slots)):
        result[column] = None
    for column in range(start, len(slots)):
        if slots[column] is not None:
            result[column + amount] = slots[column]
    return result


def shift_slots_left(slots, start, amount):
    """指定位置以降を空にして明示コマを左へ移し、負の位置は捨てる。"""
    start = max(0, int(start))
    amount = max(1, int(amount))
    result = list(slots)
    for column in range(start, len(slots)):
        result[column] = None
    for column in range(start, len(slots)):
        if slots[column] is not None and column - amount >= 0:
            result[column - amount] = slots[column]
    return result


def runs_from_states(states):
    """番号列を同じ値が連続する (開始位置, 長さ, 値) の区間へ圧縮する。"""
    runs = []
    for offset, state in enumerate(states):
        if runs and runs[-1][2] == state:
            start, length, _state = runs[-1]
            runs[-1] = (start, length + 1, state)
        else:
            runs.append((offset, 1, state))
    return runs
