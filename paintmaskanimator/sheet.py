"""シートを参照の表として読み出す（Issue #31 Phase 3 第1段階）。

既存の Layer から読む口だけを追加し、露出や保存形式は変えない。
まだ既存の呼び出し側からは使っていない。次の段階で呼び出し側を移す。
設計は docs/sequence-first-data-model.md。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from . import native


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
            if entry.kind == 0:
                number = EMPTY
            elif entry.kind == 1:
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
