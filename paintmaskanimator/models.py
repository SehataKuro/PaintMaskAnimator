import copy
from dataclasses import dataclass
from typing import ClassVar, List, Optional
from weakref import WeakSet

from PySide6.QtGui import QImage

from .utils import blank_image
from .timeline_metadata import set_dense_import, timeline_metadata


@dataclass(frozen=True)
class ArchiveHint:
    """Image-free legacy archive metadata; presence denotes explicit storage.

    Values contain metadata only and are copied at graph boundaries.
    Display settings belong to Track and artwork attributes belong to Cell.
    """

    has_content: bool = True
    is_blank_key: bool = False
    exposure: int = 1
    sequence_only: bool = False
    tween: object = None
    tween_member: object = None


class Cell:
    """1枚の絵。同じ絵番号のセルはこれを共有する（Issue #31 Phase 1）。

    接続済みの内容キーと pending slot は、同じ Track の bank にある
    ``Cell`` を番号で参照する。``layer.image = x`` はその中身を書き換えるので、
    同番号の参照にもそのまま反映される。未接続の旧データは
    ``install_legacy_frames`` で bank へ取り込む。

    ○・未使用の残存番号はローカルの ``Cell`` を持ち、bank を参照しない。

    ``Cell`` は ``image``、色塗り前の初期状態（``initial_image``）、
    セル名（``cell_name``）を持つ。同じ番号の絵は3属性とも共有し、
    clone・detach ではすべて独立させる（Issue #31 Phase 5-4）。

    設計は docs/sequence-first-data-model.md。
    """

    __slots__ = ("image", "initial_image", "cell_name")

    def __init__(self, image=None, initial_image=None, cell_name=None):
        self.image = image if image is not None else blank_image()
        self.initial_image = initial_image
        self.cell_name = cell_name


class PendingToken:
    """Identity of a position; no artwork or owner references."""

    __slots__ = ("__weakref__",)


class Track:
    """レイヤー1本ぶんの表示設定と番号付きの絵の所有元。

    connected は文書へ公開済みの Track。未接続の読み込み途中の Layer は
    ローカル Cell を持ち、Document.install_legacy_frames が一度だけ統合する。

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
        "color_filter_enabled", "color_filter_rgb", "cells", "connected",
        "legacy_archive_hints", "pending_slots",
    )

    def __init__(
        self, name="", visible=True, opacity=1.0, is_paper=False,
        is_draft=False, alpha_locked=False, color_filter_enabled=False,
        color_filter_rgb=None,
    ):
        self.cells: dict[int, Cell] = {}
        self.legacy_archive_hints: dict[int, ArchiveHint] = {}
        self.pending_slots: WeakSet[PendingToken] = WeakSet()
        self.connected = False
        self.name = name
        self.visible = visible
        self.opacity = opacity
        self.is_paper = is_paper
        self.is_draft = is_draft
        self.alpha_locked = alpha_locked
        self.color_filter_enabled = color_filter_enabled
        self.color_filter_rgb = color_filter_rgb

    def copy_settings(self):
        """Copy display settings only."""
        return Track(
            self.name, self.visible, self.opacity, self.is_paper,
            self.is_draft, self.alpha_locked, self.color_filter_enabled,
            tuple(self.color_filter_rgb)
            if self.color_filter_rgb is not None else None,
        )


    def copy(self):
        """Compatibility alias for copy_settings."""
        return self.copy_settings()


#: ``Track`` が持つ（＝レイヤー共通の）設定。
TRACK_FIELDS = (
    "name", "visible", "opacity", "is_paper", "is_draft", "alpha_locked",
    "color_filter_enabled", "color_filter_rgb",
)


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
    """絵の属性を正規の Cell（接続済みなら Track.cells）で読み書きする。"""

    def get(layer):
        return getattr(layer._cell, name)

    def set_value(layer, value):
        # dataclass の __init__ からも呼ばれるので、ここで作る。
        setattr(layer._cell, name, value)

    return property(get, set_value)


@timeline_metadata
@dataclass
class Layer:
    # Exposure lookup also runs during scrubbing. Track direct metadata edits,
    # including undo/import paths, without rescanning every cell on each lookup.
    timeline_revision: ClassVar[int] = 0

    def __setattr__(self, name, value):
        if name == "exposure":
            raise AttributeError("Live Layer has no exposure; edit dense references instead")
        if name == "has_content" and not value and getattr(self, "has_content", False):
            track = self.__dict__.get("_track")
            number = getattr(self, "sequence_number", None)
            if track is not None and track.connected and number in track.cells:
                # Legacy callers can change content metadata directly. Keep
                # the hidden v1 payload independent, and record its former
                # membership for batch metadata restoration (not drawing).
                cell = track.cells[number]
                self.__dict__["_cell"] = Cell(
                    QImage(cell.image),
                    QImage(cell.initial_image) if cell.initial_image is not None else None,
                    cell.cell_name)
                self.__dict__["_content_origin"] = number
        if (name == "has_content" and value) or (name == "is_blank_key" and value):
            self.__dict__.pop("_content_origin", None)
        changed = (name in ("has_content", "is_blank_key", "blank_start", "sequence_number", "sequence_only")
                   and getattr(self, name, None) != value)
        object.__setattr__(self, name, value)
        if changed:
            Layer.timeline_revision += 1
            self.__dict__.pop("_dense_import", None)

    name: str
    image: QImage
    visible: bool = True
    opacity: float = 1.0
    is_paper: bool = False
    has_content: bool = False
    alpha_locked: bool = False
    blank_start: bool = False
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

    def clone_slot(self):
        """Copy position metadata without creating a Cell or Track.

        Mutable tween settings and timeline metadata belong to each position.
        """
        copied = object.__new__(Layer)
        copied.__dict__.update(self.__dict__)
        copied.__dict__.pop("_timeline_metadata", None)
        imported = self.__dict__.get("_dense_import")
        copied.__dict__["_pending_token"] = PendingToken()
        pending = self.sequence_only
        copied.__dict__["_pending_detached"] = True
        for name in ("has_content", "is_blank_key", "blank_start", "sequence_number", "sequence_only",
                     "sequence_number", "sequence_only"):
            setattr(copied, name, getattr(self, name))
        from .timeline_metadata import metadata_for
        metadata_for(copied).sequence_only = pending
        if imported is not None:
            set_dense_import(copied, imported)
        position = self.__dict__.get("_v1_position")
        if position is not None:
            from dataclasses import replace
            from .legacy_v1 import snapshot_layer
            copied.__dict__["_v1_position"] = replace(
                position, legacy=snapshot_layer(position.legacy)
                if position.legacy is not None else None)
        copied.tween = copy.deepcopy(self.tween)
        return copied

    def clone_reference(self):
        """Extend a reference without duplicating a key's hidden v1 payload."""
        from dataclasses import replace
        from .legacy_v1 import snapshot_layer, V1Position
        copied = self.clone_slot()
        copied.__dict__.pop("_dense_import", None)
        copied.__dict__["_v1_position"] = V1Position(replace(
            snapshot_layer(self), image=blank_image(), initial_image=None,
            cell_name=None, has_content=False, is_blank_key=False,
            sequence_number=None, sequence_only=False, exposure=1,
            tween=None, tween_member=None))
        copied.blank_start = False
        copied.tween = None
        copied.tween_member = None
        return copied

    def clone(self):
        """Clone one drawing and its display settings independently (Qt COW)."""
        # QImage は暗黙共有。QImage(image) は画素をコピーせず参照を共有し、
        # どちらかに書き込んだ時点（QPainter や bits()）で初めて複製される。
        # 大きなキャンバスでは Undo の文書スナップショットやコマの複製で
        # 全レイヤーを即座にコピーすると数百MB単位の確保になるため。
        copied = Layer(
            self.name, QImage(self.image), self.visible, self.opacity,
            self.is_paper, self.has_content, self.alpha_locked, self.blank_start,
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
        if (self._track.connected and self.sequence_number is not None
                and (self.has_content or self.sequence_only)):
            copied._track.cells[int(self.sequence_number)] = copied._cell
            pending = copied.sequence_only
            copied._track.connected = True
            copied.set_pending(pending)
            copied.__dict__.pop("_cell", None)
        position = self.__dict__.get("_v1_position")
        if position is not None:
            from dataclasses import replace
            from .legacy_v1 import snapshot_layer
            copied.__dict__["_v1_position"] = replace(
                position, legacy=snapshot_layer(position.legacy)
                if position.legacy is not None else None)
        if "_dense_import" in self.__dict__:
            set_dense_import(copied, self.__dict__["_dense_import"])
        return copied

    # ------------------------------------------------------------------
    # 絵の共有（Issue #31 Phase 1）
    # ------------------------------------------------------------------
    @property
    def _cell(self):
        track = self.__dict__.get("_track")
        number = getattr(self, "sequence_number", None)
        if (track is not None and track.connected and number is not None
                and (self.has_content or self.sequence_only)):
            number = int(number)
            return track.cells[number]
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
        self.attach_track(other._track)

    def detach_track(self):
        """共有をやめ、いまの設定を自分だけのものにする。"""
        self.attach_track(self._track.copy_settings())

    def set_pending(self, pending):
        """Change placement intent; connected truth belongs to the Track."""
        pending = bool(pending)
        previous = self.sequence_only
        token = self.__dict__.setdefault("_pending_token", PendingToken())
        track = self._track
        if track.connected and not self.__dict__.get("_pending_detached", False):
            if pending:
                track.pending_slots.add(token)
            else:
                track.pending_slots.discard(token)
        else:
            from .timeline_metadata import metadata_for
            metadata_for(self).sequence_only = pending
        if previous != pending:
            self.__dict__.pop("_dense_import", None)
            Layer.timeline_revision += 1
        from .timeline_metadata import metadata_for
        metadata_for(self).sequence_only = pending

    def attach_track(self, track):
        """Transfer this position's pending membership to its new Track."""
        pending = self.sequence_only
        imported = self.__dict__.get("_dense_import")
        old = self._track
        token = self.__dict__.setdefault("_pending_token", PendingToken())
        old.pending_slots.discard(token)
        self.__dict__["_track"] = track
        self.__dict__.pop("_pending_detached", None)
        self.set_pending(pending)
        # Moving ownership does not edit the pending position's visible/image
        # exception. set_pending sees the new bank before its token is added.
        if imported is not None:
            set_dense_import(self, imported)

    @property
    def cell_id(self):
        """共有の見分けに使う値。同じなら同じ絵を指している。"""
        return id(self._cell)

    def share_cell_with(self, other):
        """``other`` と同じ絵を指すようにする。

        以後はどちらに書き込んでも両方に出る。接続済みの内容キーと
        pending slot は bank を参照するため、既存の Cell と一致する場合だけ許す。
        """
        if other is self:
            return
        cell = other._cell
        if (self._track.connected and self.sequence_number is not None
                and (self.has_content or self.sequence_only)):
            if self._track.cells[int(self.sequence_number)] is not cell:
                raise ValueError("Cannot replace a connected drawing through share_cell_with")
            return
        self.__dict__["_cell"] = cell

    def detach_cell(self):
        """共有をやめ、いまの絵を自分だけのものにする。

        シート操作（このコマだけ別の絵にする・消す）で使う。``QImage`` は
        暗黙共有なので、ここでは画素を複製しない。
        """
        current = self._cell
        self.__dict__["_cell"] = Cell(
            QImage(current.image) if current.image is not None else None,
            QImage(current.initial_image) if current.initial_image is not None else None,
            current.cell_name,
        )


# @dataclass が image の既定値を拾ったあとで差し替える。timeline_metadata と
# 同じ手口（デコレータの順に依存しないよう、ここで行う）。型チェッカーからは
# dataclass のフィールドに見えるので、代入だけ無視させる。
for _field, _descriptor in (
    *((name, _cell_property(name)) for name in ("image", "initial_image", "cell_name")),
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
