import copy
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtGui import QImage

from .utils import blank_image


@dataclass
class Layer:
    name: str
    image: QImage
    visible: bool = True
    opacity: float = 1.0
    is_paper: bool = False
    has_content: bool = False
    alpha_locked: bool = False
    exposure: int = 1
    color_filter_enabled: bool = False
    color_filter_rgb: Optional[tuple] = None
    is_blank_key: bool = False
    sequence_number: Optional[int] = None
    sequence_only: bool = False
    # 下書きレイヤー: 色数削減の対象外で、読み込んだ画素をそのまま表示・保存する。
    is_draft: bool = False
    # 外部タイムライン由来のセル名。数値セルは sequence_number と併記する。
    cell_name: Optional[str] = None
    # あとから直せるトゥイーン。キーのセルに設定（id・長さ・変形の形）を持ち、
    # 中割りのセルは tween_member に同じ id を持つ（tween_groups.py）。
    tween: Optional[dict] = None
    tween_member: Optional[str] = None

    def clone(self):
        # QImage は暗黙共有。QImage(image) は画素をコピーせず参照を共有し、
        # どちらかに書き込んだ時点（QPainter や bits()）で初めて複製される。
        # 大きなキャンバスでは Undo の文書スナップショットやコマの複製で
        # 全レイヤーを即座にコピーすると数百MB単位の確保になるため。
        return Layer(
            self.name, QImage(self.image), self.visible, self.opacity,
            self.is_paper, self.has_content, self.alpha_locked, self.exposure,
            self.color_filter_enabled,
            tuple(self.color_filter_rgb) if self.color_filter_rgb is not None else None,
            bool(self.is_blank_key),
            self.sequence_number,
            bool(self.sequence_only),
            bool(self.is_draft),
            str(self.cell_name) if self.cell_name is not None else None,
            copy.deepcopy(self.tween) if self.tween is not None else None,
            self.tween_member,
        )


@dataclass
class Frame:
    layers: List[Layer]
    duration: int = 1

    def clone(self):
        return Frame([x.clone() for x in self.layers], self.duration)


def default_layer_name(index):
    """Cel-style default name for the layer at ``index``: A..Z, AA, AB, ..."""
    index = max(0, int(index))
    name = ""
    while True:
        index, remainder = divmod(index, 26)
        name = chr(ord("A") + remainder) + name
        if index == 0:
            return name
        index -= 1


def next_layer_name(existing):
    """The first default name not already used by ``existing`` names."""
    used = {str(name).strip().casefold() for name in existing}
    index = 0
    while default_layer_name(index).casefold() in used:
        index += 1
    return default_layer_name(index)


def make_frame(layer_names=None):
    names = layer_names or [default_layer_name(0)]
    return Frame([Layer(name, blank_image()) for name in names])
