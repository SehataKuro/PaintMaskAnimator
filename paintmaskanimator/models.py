import copy
from dataclasses import dataclass
from typing import ClassVar, List, Optional

from PySide6.QtGui import QImage

from .utils import blank_image
from .timeline_metadata import timeline_metadata


class Cell:
    """1枚の絵。同じ絵番号のセルはこれを共有する（Issue #31 Phase 1）。

    これまで「同じ番号は同じ絵」は、``Layer.image`` が同じ ``QImage`` を指して
    いるか（``id()`` が同じか）でしか確かめられなかった。共有を作る
    ``coalesce_numbered_images`` と、描く直前に結び直す
    ``sync_numbered_image_from_cell`` の2つで支えていて、どこかで片方だけ
    差し替わると、同じ番号が別の絵を指す状態が静かにできる。

    ``Cell`` を間に置くと、共有は「同じ ``Cell`` を指しているか」というデータに
    なる。``layer.image = x`` は ``Cell`` の中身を書き換えるので、共有している
    セルにもそのまま出る。

    Phase 1 では、共有を**結ぶところだけ**を明示する（``share_cell_with``）。
    シート操作（セルを消す・別の絵を置く）が共有を壊さないよう、既定では
    レイヤーごとに別の ``Cell`` を持つ。既定を共有側へ寄せるのは Phase 3。

    Phase 1 で ``Cell`` が持つのは ``image`` だけ。色塗り前の初期状態
    （``initial_image``）はセルごとに復元する Undo があるため、レイヤー側に
    残す。まとめるのは Undo を ``(track, number)`` 単位へ移す Phase 4。

    設計は docs/sequence-first-data-model.md。
    """

    __slots__ = ("image",)

    def __init__(self, image=None):
        self.image = image if image is not None else blank_image()


class Track:
    """レイヤー1本ぶんの表示の設定（Issue #31 Phase 2）。

    ``name`` / ``visible`` / ``opacity`` / ``is_paper`` / ``is_draft`` /
    ``alpha_locked`` / ``color_filter_enabled`` / ``color_filter_rgb`` は、
    本来レイヤーの設定なのに ``Layer``（＝1コマ分のセル）が各自で持っていた。
    そのため、セルを作るたび・コマを動かすたびに
    ``_copy_display_properties`` が8個の値を写して回る必要があり、写し忘れた
    経路があるとそのコマだけ不透明度が違う、という状態になっていた。

    同じレイヤーのセルは1つの ``Track`` を共有する。どのコマから変えても
    レイヤー全体に効くので、写す処理が要らなくなる。

    設計は docs/sequence-first-data-model.md。
    """

    __slots__ = (
        "name", "visible", "opacity", "is_paper", "is_draft", "alpha_locked",
        "color_filter_enabled", "color_filter_rgb",
    )

    def __init__(
        self, name="", visible=True, opacity=1.0, is_paper=False,
        is_draft=False, alpha_locked=False, color_filter_enabled=False,
        color_filter_rgb=None,
    ):
        self.name = name
        self.visible = visible
        self.opacity = opacity
        self.is_paper = is_paper
        self.is_draft = is_draft
        self.alpha_locked = alpha_locked
        self.color_filter_enabled = color_filter_enabled
        self.color_filter_rgb = color_filter_rgb

    def copy(self):
        return Track(
            self.name, self.visible, self.opacity, self.is_paper,
            self.is_draft, self.alpha_locked, self.color_filter_enabled,
            tuple(self.color_filter_rgb)
            if self.color_filter_rgb is not None else None,
        )


#: ``Track`` が持つ（＝レイヤー共通の）設定。
TRACK_FIELDS = Track.__slots__


def _track_property(name):
    """``Layer`` の表示設定を ``Track`` 越しに読み書きする。"""

    def get(layer):
        return getattr(layer._track, name)

    def set_value(layer, value):
        track = layer.__dict__.get("_track")
        if track is None:
            track = layer.__dict__["_track"] = Track()
        setattr(track, name, value)

    return property(get, set_value)


def _cell_property(name):
    """``Layer.image`` を ``Cell`` 越しに読み書きする。"""

    def get(layer):
        return getattr(layer._cell, name)

    def set_value(layer, value):
        # dataclass の __init__ からも呼ばれるので、ここで作る。
        cell = layer.__dict__.get("_cell")
        if cell is None:
            cell = layer.__dict__["_cell"] = Cell()
        setattr(cell, name, value)

    return property(get, set_value)


@timeline_metadata
@dataclass
class Layer:
    # Exposure lookup also runs during scrubbing. Track direct metadata edits,
    # including undo/import paths, without rescanning every cell on each lookup.
    timeline_revision: ClassVar[int] = 0

    def __setattr__(self, name, value):
        if name in ("has_content", "is_blank_key", "exposure"):
            if getattr(self, name, None) != value:
                Layer.timeline_revision += 1
        object.__setattr__(self, name, value)

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
    # 色塗り前の初期状態（カットフォルダーから読み込んだままの絵など）。自動塗りは
    # ここから黒の主線と色トレス線を取り出して部分を分ける。塗りかけの色や、仕上げ色で
    # 塗りつぶした色トレス線に惑わされないように保持する。比較表示にも使う。
    initial_image: Optional[QImage] = None

    def clone(self):
        # QImage は暗黙共有。QImage(image) は画素をコピーせず参照を共有し、
        # どちらかに書き込んだ時点（QPainter や bits()）で初めて複製される。
        # 大きなキャンバスでは Undo の文書スナップショットやコマの複製で
        # 全レイヤーを即座にコピーすると数百MB単位の確保になるため。
        copied = Layer(
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
            QImage(self.initial_image) if self.initial_image is not None else None,
        )
        # Undo のスナップショットが、あとの表示設定の変更で汚れないようにする。
        copied.detach_track()
        return copied

    # ------------------------------------------------------------------
    # 絵の共有（Issue #31 Phase 1）
    # ------------------------------------------------------------------
    @property
    def _cell(self):
        cell = self.__dict__.get("_cell")
        if cell is None:
            cell = self.__dict__["_cell"] = Cell()
        return cell

    @property
    def _track(self):
        track = self.__dict__.get("_track")
        if track is None:
            track = self.__dict__["_track"] = Track()
        return track

    @property
    def track_id(self):
        """共有の見分けに使う値。同じなら同じレイヤーの設定を見ている。"""
        return id(self._track)

    def share_track_with(self, other):
        """``other`` と同じレイヤー設定を見るようにする。"""
        if other is self:
            return
        self.__dict__["_track"] = other._track

    def detach_track(self):
        """共有をやめ、いまの設定を自分だけのものにする。"""
        self.__dict__["_track"] = self._track.copy()

    @property
    def cell_id(self):
        """共有の見分けに使う値。同じなら同じ絵を指している。"""
        return id(self._cell)

    def share_cell_with(self, other):
        """``other`` と同じ絵を指すようにする。

        以後はどちらに書き込んでも両方に出る。``coalesce_numbered_images`` が
        同じ絵番号のセルを結ぶときに使う。
        """
        if other is self:
            return
        self.__dict__["_cell"] = other._cell

    def detach_cell(self):
        """共有をやめ、いまの絵を自分だけのものにする。

        シート操作（このコマだけ別の絵にする・消す）で使う。``QImage`` は
        暗黙共有なので、ここでは画素を複製しない。
        """
        current = self._cell
        self.__dict__["_cell"] = Cell(
            QImage(current.image) if current.image is not None else None
        )


# @dataclass が image の既定値を拾ったあとで差し替える。timeline_metadata と
# 同じ手口（デコレータの順に依存しないよう、ここで行う）。型チェッカーからは
# dataclass のフィールドに見えるので、代入だけ無視させる。
for _field, _descriptor in (
    ("image", _cell_property("image")),
    *((name, _track_property(name)) for name in TRACK_FIELDS),
):
    setattr(Layer, _field, _descriptor)
del _field, _descriptor


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
