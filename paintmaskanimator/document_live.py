"""Rust の文書を正本にする Document と、文書に入った Frame・Layer・Track・Cell の窓口（段階8-4）。

文書は二つの状態を持つ。

- Rust の状態：正本は pma-core の Document（``_CoreDocument``）。``frames`` などは
  コマ ID・列 ID で Rust を引く窓口を返す。窓口は ID ごとに一つに保つので、
  ``a._track is b._track`` などの同一性は今と同じ。
- 素の状態：Rust で表せない途中の状態（未接続の列を直接代入した直後、列の数が
  そろっていないコマ、範囲外の選択など）では、今と同じ素の Python のグラフ
  （document.GraphDocument）を持つ。

まだ Rust に無い操作は、素のグラフを ID 付きで組み立てて今の Python の手順を当て、
ID を保って Rust へ戻す。戻せない状態なら素の状態のまま続ける。
選択（現在コマ・選択列）は今と同じく範囲外も含めて Python の値を正とし、
Rust には範囲内のときに写す。
"""
from __future__ import annotations

import inspect
import json
from collections.abc import MutableMapping, MutableSequence, MutableSet
from copy import deepcopy
from weakref import WeakSet, ref

from .document_core import (
    _NO_IMAGE, GraphError, _plain_settings, _settings, graph_from_core, graph_payload, model_image,
    plain_slot, plain_track, slot_metadata,
)
from .models import ArchiveHint, Cell, Frame, Layer, PendingToken, Track, TRACK_FIELDS
from .native import _native
from .pma_image import PmaImage, tag_attributes

class _NeedsGraph(BaseException):
    """窓口のまま試す操作（_TRY_IN_PLACE）が、素のグラフを要る書き込みに来た印。

    操作の中の ``except Exception`` に捕まらないよう BaseException にする。
    """


class StaleDocumentObject(LookupError):
    """文書から消えたコマ・列・絵の窓口に書こうとした。"""


# ----------------------------------------------------------------------
# Rust の文書の写し（位置・列の値）と ID
# ----------------------------------------------------------------------
class _CoreState:
    """Rust の文書と、窓口が読む値の写し。書き込んだ所だけ取り直す。"""

    def __init__(self, core):
        self.core = core
        self.revision = 0
        self._load()

    def _load(self):
        text, handles = self.core.to_graph()
        payload = json.loads(text)
        self.handles = list(handles)
        self.frame_ids = [frame["id"] for frame in payload["frames"]]
        self.track_ids = [track["id"] for track in payload["tracks"]]
        self.next_ids = (payload["next_frame_id"], payload["next_track_id"])
        self.positions = {}
        for frame in payload["frames"]:
            for track_id, position in zip(self.track_ids, frame["positions"]):
                self.positions[frame["id"], track_id] = (position, self.handles)
        self.durations = {frame["id"]: frame["duration"] for frame in payload["frames"]}
        self.tracks = {track["id"]: (track, self.handles) for track in payload["tracks"]}
        # 読み取り用の写し（位置の素の Layer・その __dict__・列の素の Track）。Rust の状態が
        # 変わる（_promote で作り直す）までは使い回す。写しへの書き込みは文書に届かない
        # ので、使い回しても見え方は毎回作るのと同じ。
        self.slot_copies = {}
        # 窓口の並び（コマ ID → 位置の窓口の列、コマの窓口の列）。
        self.layer_lists = {}
        self.view_lists = {}
        self.slot_dicts = {}
        self.plain_tracks = {}
        # resolve_sources の表（native.resolve_timeline と同じ形）。
        self.resolved = None
        # 列 ID → (配置待ちの札の列, その id の集合)。_PendingView が使う。
        self.pending = {}
        self.frame_index = {fid: index for index, fid in enumerate(self.frame_ids)}
        self.track_index = {tid: index for index, tid in enumerate(self.track_ids)}
        # 局所の絵を共有する組（Python では同じ Cell）→ 組の先頭（表示順で最初）の位置。
        self.share_leaders = {}
        for fid in self.frame_ids:
            for tid in self.track_ids:
                group = self.positions[fid, tid][0].get("local_share")
                if group is not None:
                    self.share_leaders.setdefault(group, (fid, tid))

    def local_key(self, frame_id, track_id):
        """位置の局所の絵のキー。共有の組なら組の先頭の位置のキー。"""
        position = self.positions.get((frame_id, track_id))
        group = None if position is None else position[0].get("local_share")
        if group is not None:
            return ("local", *self.share_leaders[group])
        return ("local", frame_id, track_id)

    def position(self, frame_id, track_id):
        return self.positions.get((frame_id, track_id))

    def track(self, track_id):
        return self.tracks.get(track_id)

    def refresh_position(self, frame_id, track_id):
        text, handles = self.core.position_graph(frame_id, track_id)
        self.positions[frame_id, track_id] = (json.loads(text), list(handles))
        self.revision += 1

    def refresh_track(self, track_id):
        text, handles = self.core.track_graph(track_id)
        self.tracks[track_id] = (json.loads(text), list(handles))
        self.revision += 1

    def reload_rows(self, frame_ids):
        """構造の命令のあと、並び・次の ID と指定したコマの行だけを Rust から取り直す。"""
        text, handles = self.core.frames_graph(list(frame_ids))
        payload = json.loads(text)
        handles = list(handles)
        previous = set(self.frame_ids)
        self.frame_ids = payload["frame_order"]
        self.track_ids = payload["track_order"]
        self.next_ids = (payload["next_frame_id"], payload["next_track_id"])
        current = set(self.frame_ids)
        stale = set(frame_ids) | (previous - current)
        for fid in previous - current:
            self.durations.pop(fid, None)
            for tid in self.track_ids:
                self.positions.pop((fid, tid), None)
        for frame in payload["frames"]:
            for tid, position in zip(self.track_ids, frame["positions"]):
                self.positions[frame["id"], tid] = (position, handles)
            self.durations[frame["id"]] = frame["duration"]
        self.frame_index = {fid: index for index, fid in enumerate(self.frame_ids)}
        self.track_index = {tid: index for index, tid in enumerate(self.track_ids)}
        self.share_leaders = {}
        for fid in self.frame_ids:
            for tid in self.track_ids:
                group = self.positions[fid, tid][0].get("local_share")
                if group is not None:
                    self.share_leaders.setdefault(group, (fid, tid))
        for cache in (self.slot_copies, self.slot_dicts):
            for key in [key for key in cache if key[0] in stale]:
                del cache[key]
        for fid in stale:
            self.layer_lists.pop(fid, None)
        self.view_lists.clear()
        self.resolved = None
        self.pending.clear()
        self.revision += 1

    def forget_track(self, track_id):
        """列を書き換えたあと、その列から作った読み取り用の写しを捨てる。"""
        self.plain_tracks.pop(track_id, None)
        for cache in (self.slot_copies, self.slot_dicts):
            for key in [key for key in cache if key[1] == track_id]:
                del cache[key]


# ----------------------------------------------------------------------
# 窓口の画像
# ----------------------------------------------------------------------
class BoundImage(PmaImage):
    """文書の絵に結び付いた PmaImage。読み書きは Rust のセルの画像に届く。

    Qt の画像属性は Rust の画像の札が持つ。
    """

    __slots__ = ()

    @property
    def _attributes(self):
        return tag_attributes(self._image.tag)

    @_attributes.setter
    def _attributes(self, value):
        pass


def _bind(image, handle):
    """image（PmaImage）を Rust の絵の窓口にする。"""
    if type(image) is PmaImage:
        image.__class__ = BoundImage
    object.__setattr__(image, "_image", handle)
    return image


def _unbind(image):
    """窓口の画像を、今の画素と属性を持つ独立した PmaImage に戻す。"""
    handle = image._image
    try:
        detached = handle.copy()
    except ValueError:
        return
    image.__class__ = PmaImage
    object.__setattr__(image, "_image", detached)
    object.__setattr__(image, "_attributes", tag_attributes(detached.tag))


# ----------------------------------------------------------------------
# 窓口
# ----------------------------------------------------------------------
_META = frozenset(("_owner", "_key", "_detached", "_plain"))
_INTERNAL = frozenset(("__class__", "__dict__", "_live_meta"))


def _meta(proxy):
    """窓口の内部情報（_owner・_key・_detached・_plain の dict）。

    Cell・Track は __slots__ の型なので、呼び出し側のオブジェクトをそのまま窓口に
    替えられるよう（_adopt）、窓口のクラスは元の型と同じ作りにし、内部情報は元の
    スロット（Cell.image・Track.name）に置く。Layer・Frame はインスタンスの辞書に置く。
    """
    cls = type(proxy)
    if cls is LiveLayer or cls is LiveFrame:
        return object.__getattribute__(proxy, "_live_meta")
    if issubclass(cls, LiveCell):
        return _CELL_SLOT.__get__(proxy, Cell)
    if issubclass(cls, LiveTrack):
        return _TRACK_SLOT.__get__(proxy, Track)
    return object.__getattribute__(proxy, "_live_meta")


def _m(proxy, name):
    return _meta(proxy)[name]


def _setm(proxy, name, value):
    _meta(proxy)[name] = value


class _Cache:
    """(種類, キー) ごとに窓口を一つに保つ表。

    強参照で持つ。弱参照だと誰も持っていない窓口がすぐ消え、次の窓口が同じ id() を
    使い回すので、id() で引く今の処理（cell_id・track_id など）が壊れる。文書から
    消えた窓口は Rust へ戻すとき（_promote）に外す。
    """

    def __init__(self):
        self._objects = {}

    def get(self, kind, key, factory):
        obj = self._objects.get((kind, key))
        if obj is None:
            obj = factory()
            self._objects[kind, key] = obj
        return obj

    def put(self, kind, key, obj):
        self._objects[kind, key] = obj

    def pop(self, kind, key):
        return self._objects.pop((kind, key), None)

    def items(self):
        return list(self._objects.items())


def _new_meta(owner, key):
    return {"_owner": owner, "_key": key, "_detached": None, "_plain": None}


def _install_meta(proxy, meta):
    cls = type(proxy)
    if issubclass(cls, LiveCell):
        _CELL_SLOT.__set__(proxy, meta)
    elif issubclass(cls, LiveTrack):
        _TRACK_SLOT.__set__(proxy, meta)
    else:
        object.__setattr__(proxy, "_live_meta", meta)


def _make(cls, owner, key):
    proxy = object.__new__(cls)
    _install_meta(proxy, _new_meta(owner, key))
    return proxy


def _adopt(obj, cls, owner, key):
    """文書に入った素のオブジェクトそのものを窓口に替える（``x is document...`` を保つ）。

    値は Rust に移ったので、素の値は捨てる。
    """
    if isinstance(obj, (Cell, Track)):
        slots = Cell.__slots__ if isinstance(obj, Cell) else Track.__slots__
        for name in slots:
            descriptor = getattr(Cell if isinstance(obj, Cell) else Track, name)
            try:
                descriptor.__delete__(obj)
            except AttributeError:
                pass
        object.__setattr__(obj, "__class__", cls)
    else:
        object.__getattribute__(obj, "__dict__").clear()
        object.__setattr__(obj, "__class__", cls)
    _install_meta(obj, _new_meta(owner, key))
    return obj


def _delegate(proxy):
    """素の状態・文書から外れた窓口では、対応する素のオブジェクトを返す。"""
    return (_m(proxy, "_detached")
            or _m(proxy, "_plain"))


def _core_edit(proxy, edit):
    """Rust の状態の窓口に書く。素のグラフで今の処理を当て、Rust に戻す。

    文書がすでに素の状態なら、窓口だったオブジェクトは素の型に戻っているのでそのまま当てる。
    """
    if not isinstance(proxy, _LIVE):
        return edit(proxy)
    owner = _m(proxy, "_owner")
    owner._demote()
    if isinstance(proxy, _LIVE):
        owner._promote()
        raise StaleDocumentObject("文書にない窓口")
    try:
        return edit(proxy)
    finally:
        owner._promote()


class LiveCell(Cell):
    """文書の絵の窓口。key は ("bank", 列 ID, 番号) か ("local", コマ ID, 列 ID)。"""

    __slots__ = ()

    def __getattribute__(self, name):
        if name in _META:
            return _m(self, name)
        if name in _INTERNAL:
            return object.__getattribute__(self, name)
        target = _delegate(self)
        if target is not None:
            return getattr(target, name)
        owner, key = _m(self, "_owner"), _m(self, "_key")
        if name in ("image", "initial_image"):
            return owner._bound_image(key, name == "initial_image")
        if name == "cell_name":
            value = owner._cell_value(key)
            if value is None:
                raise StaleDocumentObject("文書にない絵")
            return value[0]["cell_name"]
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        target = _delegate(self)
        if target is not None:
            setattr(target, name, value)
            return
        _core_edit(self, lambda cell: setattr(cell, name, value))

    def __repr__(self):
        return f"LiveCell{_m(self, '_key')}"


class _CellsView(MutableMapping):
    """列のバンク（番号→絵）の窓口。"""

    def __init__(self, track):
        self._track = track

    def _numbers(self):
        owner, key = self._track._owner, self._track._key
        value = owner._track_value(key[0])
        if value is None:
            raise StaleDocumentObject("文書にない列")
        return [n for n, _ in value[0]["cells"]]

    def __getitem__(self, number):
        if number not in self:
            raise KeyError(number)
        return self._track._owner._cell(("bank", self._track._key[0], int(number)))

    def __setitem__(self, number, cell):
        owner = self._track._owner
        _core_edit(self._track, lambda track: track.cells.__setitem__(
            number, owner._plain_value(cell)))

    def __delitem__(self, number):
        if number not in self:
            raise KeyError(number)
        _core_edit(self._track, lambda track: track.cells.__delitem__(number))

    def __iter__(self):
        return iter(self._numbers())

    def __len__(self):
        return len(self._numbers())

    def __contains__(self, number):
        try:
            return int(number) in self._numbers() and int(number) == number
        except (TypeError, ValueError):
            return False

    def __repr__(self):
        return f"LiveCells({self._numbers()})"


def _hint(values):
    return ArchiveHint(values["has_content"], values["is_blank_key"], values["exposure"],
                       values["sequence_only"], values["tween"], values["tween_member"])


class _HintsView(MutableMapping):
    """列の旧連番保管（番号→ArchiveHint）の窓口。"""

    def __init__(self, track):
        self._track = track

    def _hints(self):
        value = self._track._owner._track_value(self._track._key[0])
        if value is None:
            raise StaleDocumentObject("文書にない列")
        return {n: _hint(h) for n, h in value[0]["hints"]}

    def __getitem__(self, number):
        return self._hints()[number]

    def __setitem__(self, number, hint):
        _core_edit(self._track, lambda track: track.legacy_archive_hints.__setitem__(number, hint))

    def __delitem__(self, number):
        if number not in self._hints():
            raise KeyError(number)
        _core_edit(self._track, lambda track: track.legacy_archive_hints.__delitem__(number))

    def __iter__(self):
        return iter(self._hints())

    def __len__(self):
        return len(self._hints())

    def __deepcopy__(self, memo):
        return self._hints()

    def __repr__(self):
        return f"LiveHints({self._hints()!r})"


class _PendingView(MutableSet):
    """列の配置待ちの札の集合の窓口。

    文書の位置の札（配置待ちで、所属を位置自身が持つ複製でないもの）と、文書の外の位置が
    列に登録した札（_promote が保つ）を合わせて見せる。
    """

    def __init__(self, track):
        self._track = track

    def _parts(self):
        """(文書の位置の札の列, その id の集合, 文書の外の位置の札の WeakSet)。"""
        owner = _m(self._track, "_owner")
        tid = _m(self._track, "_key")[0]
        live = owner._live()
        state = live["state"]
        extra = live.get("extra_pending", {}).get(tid, ())
        # 文書の位置の札は Rust の状態が変わる（_promote で作り直す）までは同じ。
        # 文書の外の札は WeakSet のまま毎回読む（消えた札は見せない）。
        cached = state.pending.get(tid)
        if cached is None:
            tokens = live.get("tokens", {})
            result = []
            for fid in state.frame_ids:
                data = state.position(fid, tid)[0]
                if data["sequence_only"] and not data.get("pending_detached"):
                    token = tokens.get((fid, tid))
                    if token is not None:
                        result.append(token)
            cached = state.pending[tid] = (result, {id(token) for token in result})
        return cached[0], cached[1], extra

    def _tokens(self):
        own, _, extra = self._parts()
        return [*own, *extra]

    def __contains__(self, token):
        _, own, extra = self._parts()
        return id(token) in own or any(token is item for item in extra)

    def __iter__(self):
        return iter(self._tokens())

    def __len__(self):
        return len(self._tokens())

    def add(self, token):
        ref(token)  # 今の WeakSet と同じく、弱参照にできない値は TypeError。
        if token in self:
            # 今の WeakSet と同じく、入っている札を足しても何も変えない。
            return
        _core_edit(self._track, lambda track: track.pending_slots.add(token))

    def discard(self, token):
        ref(token)  # 今の WeakSet と同じく、弱参照にできない値は TypeError。
        if token not in self:
            # 入っていない札を外しても何も変えない（全体を書き出さない）。
            return
        _core_edit(self._track, lambda track: track.pending_slots.discard(token))

    def __repr__(self):
        return f"LivePending({len(self)})"


class LiveTrack(Track):
    """文書の列の窓口。key は (列 ID,)。"""

    __slots__ = ()

    def __getattribute__(self, name):
        if name in _META:
            return _m(self, name)
        if name in _INTERNAL:
            return object.__getattribute__(self, name)
        target = _delegate(self)
        if target is not None:
            return getattr(target, name)
        owner, key = _m(self, "_owner"), _m(self, "_key")
        if name in TRACK_FIELDS:
            value = owner._track_value(key[0])
            if value is None:
                raise StaleDocumentObject("文書にない列")
            value = value[0]["settings"][name]
            return tuple(value) if name == "color_filter_rgb" and value is not None else value
        if name == "cells":
            return _CellsView(self)
        if name == "legacy_archive_hints":
            return _HintsView(self)
        if name == "connected":
            return True
        if name == "pending_slots":
            return _PendingView(self)
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        target = _delegate(self)
        if target is not None:
            setattr(target, name, value)
            return
        owner = _m(self, "_owner")
        if name in TRACK_FIELDS and owner._set_track_setting(_m(self, "_key")[0], name, value):
            return

        def edit(track):
            converted = value
            if name == "cells":
                converted = {n: owner._plain_value(c) for n, c in dict(value).items()}
            setattr(track, name, owner._plain_value(converted))
        _core_edit(self, edit)

    def copy_settings(self):
        target = _delegate(self)
        if target is not None:
            return target.copy_settings()
        rgb = self.color_filter_rgb
        return Track(self.name, self.visible, self.opacity, self.is_paper, self.is_draft,
                     self.alpha_locked, self.color_filter_enabled,
                     None if rgb is None else tuple(rgb))

    def copy(self):
        return self.copy_settings()

    def __repr__(self):
        return f"LiveTrack{_m(self, '_key')}"


_METADATA = ("has_content", "is_blank_key", "blank_start", "sequence_number", "sequence_only")
_POSITION_FIELDS = frozenset((*_METADATA, "tween", "tween_member"))
# 同じ値なら何もしない代入（_unchanged_position）。sequence_only は接続済みの位置への
# 代入そのものが AttributeError なので含めない。
_SKIPPABLE = _POSITION_FIELDS - {"sequence_only"}
_LAYER_CELL_FIELDS = ("image", "initial_image", "cell_name")
_LAYER_READS = frozenset(("clone", "clone_slot", "clone_reference"))


class LiveLayer(Layer):
    """文書の位置の窓口。key は (コマ ID, 列 ID)。

    読み取りは Rust の値の写しから。書き込みとメソッドは素のグラフで今の処理を当て、
    Rust に戻す（_core_edit）。
    """

    def __getattribute__(self, name):
        meta = object.__getattribute__(self, "_live_meta")
        if name in _META:
            return meta[name]
        if name in _INTERNAL:
            return object.__getattribute__(self, name)
        target = meta["_detached"] or meta["_plain"]
        if target is not None:
            return getattr(target, name)
        owner, key = meta["_owner"], meta["_key"]
        if name in _POSITION_FIELDS:
            value = owner._position_value(*key)
            if value is None:
                raise StaleDocumentObject("文書にない位置")
            value = value[0][name]
            if name == "tween" and value is not None:
                return owner._tween(key, value)
            return value
        if name in TRACK_FIELDS:
            return getattr(owner._track(key[1]), name)
        if name in _LAYER_CELL_FIELDS:
            return getattr(owner._cell(owner._artwork_key(*key)), name)
        if name == "_track":
            return owner._track(key[1])
        if name == "_cell":
            return owner._cell(owner._artwork_key(*key))
        if name == "track_id":
            return id(owner._track(key[1]))
        if name == "cell_id":
            return id(owner._cell(owner._artwork_key(*key)))
        if name in _LAYER_READS:
            if name != "clone_slot":
                # clone・clone_reference は絵を読む。今の Layer._cell と同じく、局所の絵が
                # 無ければ先に文書へ作らせる（写しの上に作って文書に残らないことがないように）。
                owner._cell(owner._artwork_key(*key))
                return lambda *args, **kwargs: getattr(
                    owner._plain_copy(*_m(self, "_key")), name)(*args, **kwargs)
            return getattr(owner._plain_copy(*key), name)
        attribute = getattr(Layer, name, None)
        if callable(attribute) and not name.startswith("_"):
            return lambda *args, **kwargs: _core_edit(
                self, lambda slot: getattr(slot, name)(
                    *owner._plain_args(args), **owner._plain_args(kwargs)))
        try:
            return object.__getattribute__(self, name)
        except AttributeError:
            # 今の Layer は隠し属性（_v1_position など）をインスタンスの辞書に持つ。
            values = _SlotDictView(self)._read()
            if name in values:
                return values[name]
            raise

    def __setattr__(self, name, value):
        target = _delegate(self)
        if target is not None:
            setattr(target, name, value)
            return
        owner, key = _m(self, "_owner"), _m(self, "_key")
        if name in TRACK_FIELDS:
            # Layer の表示設定は接続済みの列（Track）への書き込み（models._track_property）。
            setattr(owner._track(key[1]), name, value)
            return
        if name in _SKIPPABLE and _unchanged_position(owner, key, name, value):
            return
        _core_edit(self, lambda slot: setattr(slot, name, owner._plain_value(value)))

    def __delattr__(self, name):
        target = _delegate(self)
        if target is not None:
            delattr(target, name)
            return
        _core_edit(self, lambda slot: delattr(slot, name))

    @property
    def __dict__(self):
        target = _delegate(self)
        if target is not None:
            return target.__dict__
        return _SlotDictView(self)

    def __eq__(self, other):
        if not isinstance(other, Layer):
            return NotImplemented
        names = list(Layer.__dataclass_fields__)
        return all(getattr(self, name) == getattr(other, name) for name in names)

    __hash__ = None

    def __repr__(self):
        return f"LiveLayer{_m(self, '_key')}"


def _unchanged_position(owner, key, name, value):
    """位置のメタデータへの書き込みが、今の Layer.__setattr__ で何も変えないか。

    同じ値の代入は、timeline_revision も _dense_import も変えない。ただし内容キー・
    空キーを立てる代入は _content_origin を外すので、それが無いときだけ。
    has_content を外す代入は、今 has_content でなければ何もしない（局所への複製なし）。
    """
    data = owner._position_value(*key)
    if data is None:
        return False
    values = data[0]
    current = values[name]
    if name == "tween":
        current = owner._tween(key, current)
    if type(current) is not type(value) or current != value:
        return False
    if name == "tween":
        # 中の値の 1・1.0・True は == で等しいが、Rust（JSON）では別の値。
        try:
            if json.dumps(current, allow_nan=False) != json.dumps(value, allow_nan=False):
                return False
        except (TypeError, ValueError):
            return False
    if name in ("has_content", "is_blank_key") and value and values["content_origin"] is not None:
        return False
    return True


class _SlotDictView(MutableMapping):
    """Rust の状態の位置の __dict__。読みは素の写し、書きは素のグラフで当てて戻す。"""

    def __init__(self, layer):
        self._layer = layer

    def _owner(self):
        return _m(self._layer, "_owner")

    def _read(self):
        layer = self._layer
        key = _m(layer, "_key")
        owner = self._owner()
        state = owner._live()["state"]
        values = state.slot_dicts.get(key)
        if values is None:
            values = dict(owner._plain_copy(*key).__dict__)
            if "tween" in values:
                values["tween"] = layer.tween
            state.slot_dicts[key] = values
        return values

    def __getitem__(self, key):
        return self._read()[key]

    def __setitem__(self, key, value):
        owner = self._owner()
        _core_edit(self._layer, lambda slot: slot.__dict__.__setitem__(
            key, owner._plain_value(value)))

    def __delitem__(self, key):
        _core_edit(self._layer, lambda slot: slot.__dict__.__delitem__(key))

    def __iter__(self):
        return iter(self._read())

    def __len__(self):
        return len(self._read())

    def __contains__(self, key):
        return key in self._read()

    def copy(self):
        return dict(self._read())


class _LayersView(MutableSequence):
    """コマの位置の並び。並びを変える書き込みは素のグラフで当てて戻す。"""

    def __init__(self, frame):
        self._frame = frame
        # 素の状態に落ちてコマが素の型に戻っても文書を引けるよう、作った時に覚える。
        self._document = _m(frame, "_owner")
        # 今の手順がコマの layers に新しいリストを入れたあと（move_tracks）、それまでに渡した
        # 並びは入れ替え前のリストのまま（文書から外れた素のリスト）。
        self._frozen = None

    def _owner(self):
        # 窓口のコマなら今の持ち主を引く（restore で外れた文書へ付け替わることがある）。
        frame = self._frame
        if isinstance(frame, LiveFrame):
            self._document = _m(frame, "_owner")
        return self._document

    def _items(self):
        if self._frozen is not None:
            return self._frozen
        # 素の状態（途中で表せなくなった）では、素のコマの並びをそのまま返す。
        if not isinstance(self._frame, LiveFrame):
            return self._frame.layers
        owner = self._owner()
        fid = _m(self._frame, "_key")[0]
        state = owner._live()["state"]
        items = state.layer_lists.get(fid)
        if items is None:
            items = state.layer_lists[fid] = [owner._layer(fid, tid) for tid in state.track_ids]
        return items

    def __getitem__(self, index):
        items = self._items()
        return list(items[index]) if isinstance(index, slice) else items[index]

    def __len__(self):
        return len(self._items())

    def _edit(self, edit):
        if self._frozen is not None:
            # 入れ替え前のリストへの書き込みは文書に届かない（値はそのまま入る）。
            return edit(self._frozen, _AS_IS)
        owner = self._owner()
        if not isinstance(self._frame, LiveFrame):
            # 素の状態ではコマの並びにそのまま当てる。
            return edit(self._frame.layers, owner)
        return _core_edit(self._frame, lambda frame: edit(frame.layers, owner))

    def __setitem__(self, index, value):
        self._edit(lambda layers, owner: layers.__setitem__(index, owner._plain_value(value)))

    def __delitem__(self, index):
        self._edit(lambda layers, owner: layers.__delitem__(index))

    def insert(self, index, value):
        self._edit(lambda layers, owner: layers.insert(index, owner._plain_value(value)))

    def __eq__(self, other):
        try:
            return list(self) == list(other)
        except TypeError:
            return NotImplemented

    def __repr__(self):
        return repr(self._items())


class _AsIs:
    """_LayersView の書き込みで、値を文書の素の値に変えずにそのまま入れる。"""

    @staticmethod
    def _plain_value(value):
        return value


_AS_IS = _AsIs()


class LiveFrame(Frame):
    """文書のコマの窓口。key は (コマ ID,)。"""

    def __getattribute__(self, name):
        meta = object.__getattribute__(self, "_live_meta")
        if name in _META:
            return meta[name]
        if name in _INTERNAL:
            return object.__getattribute__(self, name)
        target = meta["_detached"] or meta["_plain"]
        if target is not None:
            return getattr(target, name)
        owner, key = meta["_owner"], meta["_key"]
        if name == "layers":
            view = object.__getattribute__(self, "__dict__").get("_layers_view")
            if view is None:
                view = _LayersView(self)
                object.__getattribute__(self, "__dict__")["_layers_view"] = view
            return view
        if name == "duration":
            duration = owner._duration(key[0])
            if duration is None:
                raise StaleDocumentObject("文書にないコマ")
            return duration
        if name == "clone":
            return lambda: Frame([layer.clone() for layer in _LayersView(self)],
                                 owner._duration(key[0]))
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        target = _delegate(self)
        if target is not None:
            setattr(target, name, value)
            return
        owner = _m(self, "_owner")
        _core_edit(self, lambda frame: setattr(frame, name, owner._plain_value(value)))

    def __eq__(self, other):
        if not isinstance(other, Frame):
            return NotImplemented
        return list(self.layers) == list(other.layers) and self.duration == other.duration

    __hash__ = None

    def __repr__(self):
        return f"LiveFrame{_m(self, '_key')}"


class _FramesView(MutableSequence):
    """文書のコマ（または列）の並び。並びを変える書き込みは素のグラフで当てて戻す。"""

    _name = "frames"

    def __init__(self, owner):
        self._owner = owner
        # 文書の frames（tracks）を入れ替えた後は、入れ替え前の並びに固定する（今の
        # Python ではリストそのものを持つので、古い名前からは古い並びが見える）。
        self._frozen = None

    def _freeze(self, items=None):
        self._frozen = list(self) if items is None else items

    def _ids(self):
        return self._owner._frame_ids()

    def _all(self):
        """Rust の状態での窓口の並び。状態が変わるまで使い回す。"""
        state = self._owner._live()["state"]
        items = state.view_lists.get(self._name)
        if items is None:
            items = state.view_lists[self._name] = [self._item(i) for i in self._ids()]
        return items

    def _item(self, item_id):
        return self._owner._frame(item_id)

    def _resolve_sources(self):
        """Rust の状態なら、native.resolve_timeline と同じ表を Rust の文書から求める。

        素の状態・入れ替え前の並びでは None（呼び出し側が今の手順で求める）。
        """
        if self._frozen is not None or self._name != "frames":
            return None
        live = self._owner._live()
        if live["graph"] is not None:
            return None
        import numpy as np
        state = live["state"]
        if state.resolved is None:
            state.resolved = live["core"].resolve_sources()
        width, data = state.resolved
        # 呼ぶたびに新しい（読み取り専用の）配列を返す。native.resolve_timeline と同じ。
        return np.frombuffer(data, dtype=np.int64).reshape(len(state.frame_ids), width)

    def __getitem__(self, index):
        if self._frozen is not None:
            return self._frozen[index]
        graph = self._owner._live()["graph"]
        if graph is not None:
            # 素の状態では素のグラフの並びをそのまま使う。
            return getattr(graph.plain, self._name)[index]
        items = self._all()
        return list(items[index]) if isinstance(index, slice) else items[index]

    def __len__(self):
        if self._frozen is not None:
            return len(self._frozen)
        live = self._owner._live()
        graph = live["graph"]
        if graph is not None:
            return len(getattr(graph.plain, self._name))
        state = live["state"]
        return len(state.frame_ids if self._name == "frames" else state.track_ids)

    def _edit(self, edit):
        if self._frozen is not None:
            return edit(self._frozen)
        owner = self._owner
        graph = owner._demote()
        try:
            return edit(getattr(graph.plain, self._name))
        finally:
            owner._promote()

    def __setitem__(self, index, value):
        value = self._owner._plain_value(value)
        self._edit(lambda frames: frames.__setitem__(index, value))

    def __delitem__(self, index):
        self._edit(lambda frames: frames.__delitem__(index))

    def insert(self, index, value):
        value = self._owner._plain_value(value)
        self._edit(lambda frames: frames.insert(index, value))

    def __eq__(self, other):
        try:
            return list(self) == list(other)
        except TypeError:
            return NotImplemented

    def __repr__(self):
        return f"Live{self._name.capitalize()}({len(self)})"


class _TracksView(_FramesView):
    """文書の列の並び。"""

    _name = "tracks"

    def _ids(self):
        return self._owner._track_ids()

    def _item(self, item_id):
        return self._owner._track(item_id)


# ----------------------------------------------------------------------
# 文書
# ----------------------------------------------------------------------
class _Keys(dict):
    """id(素のオブジェクト) → (種類, キー)。objects に id → オブジェクトも持つ。"""

    def __init__(self):
        super().__init__()
        self.objects = {}

    def setdefault(self, obj_id, value, obj=None):
        if obj is not None and obj_id not in self:
            self.objects[obj_id] = obj
        return super().setdefault(obj_id, value)


class _Graph:
    """素の状態のグラフ。Rust から書き出したときは素のオブジェクトと ID の対応も持つ。"""

    def __init__(self, plain):
        self.plain = plain
        # 書き出した時点の (frames のリスト, tracks のリスト)。_promote が入れ替えを見る。
        self.lists = None
        self.frame_ids = {}
        self.track_ids = {}
        # 書き出したオブジェクトを生かしておき、id() の使い回しを防ぐ。
        self.instances = []


def _slot_owns_local(slot):
    """位置が局所の絵を持つ（バンクを参照しない）か。今の Layer._cell と同じ条件。"""
    has_content, _, number, sequence_only, _ = slot_metadata(slot)
    track = slot.__dict__.get("_track")
    bank = (track is not None and track.connected and number is not None
            and (has_content or sequence_only))
    return not bank and "_cell" in slot.__dict__


def _graph_keys(plain, frame_ids, track_ids):
    """id(素のオブジェクト) → (種類, キー)。画像は (キー, 初期画像か) を別に返す。

    keys["objects"] ではなく、別の表 objects に id → オブジェクトを持つ（_adopt 用）。
    """
    keys, images = _Keys(), {}

    def image(value, key, initial):
        if isinstance(value, PmaImage):
            images.setdefault(id(value), (value, key, initial))

    for track, tid in zip(plain.tracks, track_ids):
        keys.setdefault(id(track), ("track", (tid,)), track)
        for number, cell in track.cells.items():
            key = ("bank", tid, int(number))
            keys.setdefault(id(cell), ("cell", key), cell)
            image(cell.image, key, False)
            image(cell.initial_image, key, True)
    for frame, fid in zip(plain.frames, frame_ids):
        keys.setdefault(id(frame), ("frame", (fid,)), frame)
        for slot, tid in zip(frame.layers, track_ids):
            keys.setdefault(id(slot), ("layer", (fid, tid)), slot)
            if _slot_owns_local(slot):
                local = slot.__dict__["_cell"]
                key = ("local", fid, tid)
                keys.setdefault(id(local), ("cell", key), local)
                image(local.image, key, False)
                image(local.initial_image, key, True)
    return keys, images


class Document:
    """プロジェクトの状態（コマ・列・番号付きの絵と選択）。正本は Rust の文書。

    操作のメソッドは document.GraphDocument と同じ。まだ Rust に無い操作は素のグラフに
    当てて Rust に戻す（_run）。
    """

    def __init__(self):
        from .document import GraphDocument
        self._live()["graph"] = _Graph(GraphDocument())
        self._promote()

    # ------------------------------------------------------------------
    # 状態の切り替え
    # ------------------------------------------------------------------
    def _live(self):
        live = self.__dict__.get("_live_state")
        if live is None:
            from .document import GraphDocument
            live = self.__dict__["_live_state"] = {
                "graph": _Graph(object.__new__(GraphDocument)), "core": None, "state": None,
                "cache": _Cache(), "next_ids": (0, 0), "cursor": [0, 0],
            }
        return live

    def _demote(self):
        """素の状態にして、そのグラフを返す。Rust の状態なら書き出す。"""
        live = self._live()
        if live["graph"] is None:
            if live.get("trial"):
                # 窓口のまま試している操作が、素のグラフを要る書き込みに来た。
                raise _NeedsGraph
            live["graph"] = self._materialize()
            live["state"] = None
        return live["graph"]

    def _materialize(self):
        from .document import GraphDocument
        live = self._live()
        state, cache = live["state"], live["cache"]
        graph = _Graph(object.__new__(GraphDocument))
        cached = dict(cache.items())
        by_key = {}
        tracks = []
        for tid in state.track_ids:
            data, handles = state.track(tid)
            track = plain_track(data, handles)
            for number, cell in track.cells.items():
                key = ("bank", tid, number)
                _reuse_images(cell, key, cached)
                by_key["cell", key] = cell
            for token in live.get("extra_pending", {}).get(tid, ()):
                track.pending_slots.add(token)
            graph.track_ids[id(track)] = tid
            by_key["track", (tid,)] = track
            tracks.append(track)
        frames = []
        shares = {}
        for fid in state.frame_ids:
            layers = []
            for track, tid in zip(tracks, state.track_ids):
                data, handles = state.position(fid, tid)
                slot = plain_slot(data, track, handles, shares=shares)
                _restore_token(slot, track, live.get("tokens", {}).get((fid, tid)))
                by_key["layer", (fid, tid)] = slot
                if _slot_owns_local(slot):
                    key = state.local_key(fid, tid)
                    _reuse_images(slot.__dict__["_cell"], key, cached)
                    by_key["cell", key] = slot.__dict__["_cell"]
                layers.append(slot)
            frame = Frame(layers, state.durations[fid])
            graph.frame_ids[id(frame)] = fid
            by_key["frame", (fid,)] = frame
            frames.append(frame)
        graph.plain.frames = frames
        graph.plain.tracks = tracks
        graph.plain.current_frame, graph.plain.active_layer_index = live["cursor"]
        graph.instances = list(by_key.values())
        live["next_ids"] = state.next_ids
        # 窓口のオブジェクトそのものを素の型に戻し、書き出した値を詰める（同一性を保つ）。
        replaced = {}
        for (kind, key), proxy in cached.items():
            if kind == "image":
                continue
            cache.pop(kind, key)
            stand = by_key.get((kind, key))
            if stand is not None:
                _transplant(proxy, stand)
                replaced[id(stand)] = proxy
        if replaced:
            _replace_references(graph.plain, replaced)
            # ID の対応表も、素に戻した窓口のオブジェクトへ付け替える（付け替えないと
            # 次の _promote で全部のコマ・列に新しい ID が振られる）。
            for table in (graph.frame_ids, graph.track_ids):
                for obj_id, value in list(table.items()):
                    target = replaced.get(obj_id)
                    if target is not None:
                        del table[obj_id]
                        table[id(target)] = value
        graph.instances = [replaced.get(id(obj), obj) for obj in graph.instances]
        graph.lists = (graph.plain.frames, graph.plain.tracks)
        return graph

    def _assign_ids(self, graph):
        plain = graph.plain
        next_frame, next_track = self._live()["next_ids"]

        def assign(objects, known, start):
            used, result = set(), []
            for obj in objects:
                value = known.get(id(obj))
                result.append(None if value is None or value in used else value)
                if result[-1] is not None:
                    used.add(value)
            start = max([start, *(value + 1 for value in used)])
            for index, value in enumerate(result):
                if value is None:
                    result[index] = start
                    start += 1
            return result, start

        frame_ids, next_frame = assign(plain.frames, graph.frame_ids, next_frame)
        track_ids, next_track = assign(plain.tracks, graph.track_ids, next_track)
        return frame_ids, track_ids, next_frame, next_track

    def _promote(self):
        """素のグラフを Rust に戻す。表せなければ素の状態のまま False を返す。"""
        live = self._live()
        graph = live["graph"]
        if graph is None:
            return True
        plain = graph.plain
        # 素のグラフの frames・tracks のリストそのものが入れ替わっていたら、それまでに渡した
        # 並びの窓口は入れ替え前のリストを指したままにする（今の Python と同じ）。
        lists = getattr(graph, "lists", None)
        if lists is not None:
            if getattr(plain, "frames", None) is not lists[0]:
                self._retire_view("frames_view", lists[0])
            if getattr(plain, "tracks", None) is not lists[1]:
                self._retire_view("tracks_view", lists[1])
            graph.lists = (getattr(plain, "frames", None), getattr(plain, "tracks", None))
        if not getattr(plain, "frames", None) or getattr(plain, "tracks", None) is None:
            return False
        frame_ids, track_ids, next_frame, next_track = self._assign_ids(graph)
        # 選択は Python の値が正。範囲外の途中の値でも Rust には範囲内の値を渡す。
        frame, layer = (getattr(plain, "current_frame", 0), getattr(plain, "active_layer_index", 0))
        selection = (frame if _in_range(frame, len(plain.frames)) else 0,
                     layer if _in_range(layer, len(plain.tracks)) else 0)
        try:
            text, images = graph_payload(
                plain, ids=(frame_ids, track_ids, next_frame, next_track), selection=selection)
        except GraphError as exc:
            _note_plain(exc)
            return False
        keys, image_keys = _graph_keys(plain, frame_ids, track_ids)
        # 配置待ちの札（PendingToken）は位置ごとに保つ（素に書き出すたびに作り直さない）。
        tokens = {}
        for plain_frame, fid in zip(plain.frames, frame_ids):
            for slot, tid in zip(plain_frame.layers, track_ids):
                token = slot.__dict__.get("_pending_token")
                if token is not None:
                    tokens[fid, tid] = token
        # 文書の外の位置が列に登録した配置待ちの札は Rust に無いので、ここで保つ。
        own = {id(token) for token in tokens.values()}
        extra_pending = {}
        for track, tid in zip(plain.tracks, track_ids):
            extra = [token for token in track.pending_slots if id(token) not in own]
            if extra:
                extra_pending[tid] = WeakSet(extra)
        cache = live["cache"]
        # 素のグラフから外れる窓口の画像は、Rust を書き換える前に今の画素で切り離す。
        for (kind, key), image in cache.items():
            if kind == "image" and id(image) not in image_keys:
                cache.pop(kind, key)
                _unbind(image)
        core = live["core"]
        try:
            if core is None:
                core = _native._CoreDocument.from_graph(text, images)
            else:
                core.replace_from_graph(text, images)
        except ValueError:
            return False
        live["core"] = core
        live["cursor"] = [frame, layer]
        live["state"] = _CoreState(core)
        live["graph"] = None
        live["next_ids"] = (next_frame, next_track)
        live["last_keys"] = keys
        live["tokens"] = tokens
        live["extra_pending"] = extra_pending
        self._sync_selection()
        # 文書に入っている素のオブジェクトそのものを窓口にする（同一性を保つ）。文書から
        # 外れたオブジェクトは素のまま残り、今の値を持ち続ける。
        for (kind, key), _ in cache.items():
            if kind != "image":
                cache.pop(kind, key)
        classes = {"frame": LiveFrame, "layer": LiveLayer, "track": LiveTrack, "cell": LiveCell}
        for obj_id, (kind, key) in keys.items():
            obj = keys.objects[obj_id]
            if not isinstance(obj, _LIVE):
                cache.put(kind, key, _adopt(obj, classes[kind], self, key))
        # グラフの画像を Rust の絵に結び付け、同じ絵には同じ画像オブジェクトを返す。
        # 絵が別のキーへ移っていることがあるので、古いキーの項目は先に全部外す（グラフに
        # 残る画像は切り離さない。外れた画像は上で切り離し済み）。
        for (kind, key), _ in cache.items():
            if kind == "image":
                cache.pop(kind, key)
        for image, key, initial in image_keys.values():
            _bind(image, _native._CellImage(core, key[0] == "bank", key[1], key[2], initial))
            cache.put("image", (key, initial), image)
        return True

    def _sync_selection(self):
        live = self._live()
        frame, layer = live["cursor"]
        try:
            live["core"].set_selection_index(int(frame), int(layer))
        except (ValueError, TypeError, OverflowError):
            pass

    # ------------------------------------------------------------------
    # 公開の状態
    # ------------------------------------------------------------------
    @property
    def frames(self):
        # 素の状態でも並びの窓口を返す（窓口が素のグラフの並びを読む）。状態をまたいで
        # ``document.frames is document.frames`` を保つ。
        live = self._live()
        view = live.get("frames_view")
        if view is None:
            view = live["frames_view"] = _FramesView(self)
        return view

    @frames.setter
    def frames(self, value):
        self._assign_list("frames", value)

    def _assign_list(self, name, value):
        """frames・tracks を入れ替える。この文書が前に渡した並びの窓口を戻したときは、
        今の Python と同じく ``document.frames is value`` になるよう、その窓口を使い直す。"""
        view_name = f"{name}_view"
        reuse = (isinstance(value, _FramesView) and value._name == name
                 and (value._owner is self or value.__dict__.get("_retired_from") is self))
        items = self._plain_value(list(value) if reuse else value)
        self._retire_view(view_name)
        graph = self._demote()
        setattr(graph.plain, name, items)
        if reuse:
            value._frozen = None
            value._owner = self
            value.__dict__.pop("_retired_from", None)
            self._live()[view_name] = value
            # _promote が「リストが入れ替わった」と見て、戻した窓口を固定し直さないように。
            if graph.lists is not None:
                graph.lists = ((items, graph.lists[1]) if name == "frames"
                               else (graph.lists[0], items))

    @property
    def tracks(self):
        # 素の状態でも並びの窓口を返す（窓口が素のグラフの並びを読む）。状態をまたいで
        # ``document.tracks is document.tracks`` を保つ。
        live = self._live()
        view = live.get("tracks_view")
        if view is None:
            view = live["tracks_view"] = _TracksView(self)
        return view

    @tracks.setter
    def tracks(self, value):
        self._assign_list("tracks", value)

    def _retire_view(self, name, items=None):
        view = self._live().pop(name, None)
        if view is not None:
            view._freeze(items)

    @property
    def current_frame(self):
        graph = self._live()["graph"]
        if graph is not None:
            return graph.plain.current_frame
        return self._live()["cursor"][0]

    @current_frame.setter
    def current_frame(self, value):
        live = self._live()
        if live["graph"] is not None:
            live["graph"].plain.current_frame = value
            return
        live["cursor"][0] = value
        self._sync_selection()

    @property
    def active_layer_index(self):
        graph = self._live()["graph"]
        if graph is not None:
            return graph.plain.active_layer_index
        return self._live()["cursor"][1]

    @active_layer_index.setter
    def active_layer_index(self, value):
        live = self._live()
        if live["graph"] is not None:
            live["graph"].plain.active_layer_index = value
            return
        live["cursor"][1] = value
        self._sync_selection()

    def delete_frame(self, column, layer_index):
        changes = _CORE_COMMANDS["delete_frame"](self, column, layer_index)
        if changes is None:
            return self._run("delete_frame", (column, layer_index), {})
        return changes

    def delete_track_frame(self, column, layer_index):
        changes = _CORE_COMMANDS["delete_track_frame"](self, column, layer_index)
        if changes is None:
            return self._run("delete_track_frame", (column, layer_index), {})
        return changes

    def ensure_frame_count(self, count):
        changes = _CORE_COMMANDS["ensure_frame_count"](self, count)
        return self._run("ensure_frame_count", (count,), {}) if changes is None else changes

    def advance_frame(self):
        changes = _CORE_COMMANDS["advance_frame"](self)
        return self._run("advance_frame", (), {}) if changes is None else changes

    def insert_track_frame(self, column, layer_index):
        changes = _CORE_COMMANDS["insert_track_frame"](self, column, layer_index)
        if changes is None:
            return self._run("insert_track_frame", (column, layer_index), {})
        return changes

    def duplicate_frame(self, column):
        changes = _CORE_COMMANDS["duplicate_frame"](self, column)
        return self._run("duplicate_frame", (column,), {}) if changes is None else changes

    def insert_sequence_blank(self, layer_index, after_number):
        changes = _CORE_COMMANDS["insert_sequence_blank"](self, layer_index, after_number)
        if changes is None:
            return self._run("insert_sequence_blank", (layer_index, after_number), {})
        return changes

    def delete_sequence_entry(self, layer_index, number):
        changes = _CORE_COMMANDS["delete_sequence_entry"](self, layer_index, number)
        if changes is None:
            return self._run("delete_sequence_entry", (layer_index, number), {})
        return changes

    def split_sheet_clip(self, column, layer_index):
        changes = _CORE_COMMANDS["split_sheet_clip"](self, column, layer_index)
        if changes is None:
            return self._run("split_sheet_clip", (column, layer_index), {})
        return changes

    def has_unlinked_tracks(self):
        """Rust の状態では列の窓口はいつも文書へ公開済み（connected）なので、位置をなめない。"""
        live = self._live()
        if live["graph"] is None and not live.get("trial"):
            return False
        from .document import GraphDocument
        return GraphDocument.has_unlinked_tracks(self)

    def link_layer_tracks(self):
        """Rust の状態では位置はいつも自分の列につながっているので、配置待ちの所属を位置が持つ
        位置（pending_detached）が無ければ、今の手順は tracks に新しいリストを入れるだけ。"""
        from .document_changes import ChangeSet
        live = self._live()
        if live["graph"] is None and not live.get("trial"):
            state = live["state"]
            if state.frame_ids and not any(values.get("pending_detached")
                                           for values, _ in state.positions.values()):
                self._retire_view("tracks_view")
                return ChangeSet()
        return self._run("link_layer_tracks", (), {})

    def capture_column(self, index):
        """列の写し（今の手順と同じく clone_frame_graph で作る、文書から外れた Layer の並び）。

        Rust の状態では、全部を素に書き出さず、その列だけを _materialize と同じ手順で素の
        オブジェクトにしてから写す。
        """
        live = self._live()
        if (live["graph"] is None and not live.get("trial") and type(index) is int
                and 0 <= index < len(live["state"].track_ids)):
            from .document import clone_frame_graph
            state = live["state"]
            # _materialize と同じく、窓口の画像（今の画素を持つ）があればそれを入れてから写す。
            cached = dict(live["cache"].items())
            tid = state.track_ids[index]
            track = plain_track(*state.track(tid))
            for number, cell in track.cells.items():
                _reuse_images(cell, ("bank", tid, number), cached)
            for token in live.get("extra_pending", {}).get(tid, ()):
                track.pending_slots.add(token)
            shares, frames = {}, []
            for fid in state.frame_ids:
                data, handles = state.position(fid, tid)
                slot = plain_slot(data, track, handles, shares=shares)
                _restore_token(slot, track, live.get("tokens", {}).get((fid, tid)))
                if _slot_owns_local(slot):
                    _reuse_images(slot.__dict__["_cell"], state.local_key(fid, tid), cached)
                frames.append(Frame([slot], state.durations[fid]))
            copied, _ = clone_frame_graph(frames)
            return [frame.layers[0] for frame in copied]
        return self._run("capture_column", (index,), {})

    def remove_tracks(self, indices, *, active=None):
        indices = tuple(indices)
        changes = _CORE_COMMANDS["remove_tracks"](self, indices, active=active)
        if changes is None:
            return self._run("remove_tracks", (indices,), {"active": active})
        return changes

    def restore_removed_track(self, e):
        changes = _CORE_COMMANDS["restore_removed_track"](self, e)
        return self._run("restore_removed_track", (e,), {}) if changes is None else changes

    def duplicate_tracks(self, names):
        changes = _CORE_COMMANDS["duplicate_tracks"](self, names)
        return self._run("duplicate_tracks", (names,), {}) if changes is None else changes

    def move_tracks(self, order):
        # 画面からは reversed(...) が来る。Rust の命令で当てられず今の経路へ戻るときに
        # 使い切った並びを渡さないよう、先に固める（今の手順も最初に tuple にする）。
        order = tuple(order)
        changes = _CORE_COMMANDS["move_tracks"](self, order)
        return self._run("move_tracks", (order,), {}) if changes is None else changes

    def restore_inserted_track(self, e):
        changes = _CORE_COMMANDS["restore_inserted_track"](self, e)
        return self._run("restore_inserted_track", (e,), {}) if changes is None else changes

    def add_track(self):
        changes = _CORE_COMMANDS["add_track"](self)
        return self._run("add_track", (), {}) if changes is None else changes

    def create_blank_key(self, column, layer_index):
        changes = _CORE_COMMANDS["create_blank_key"](self, column, layer_index)
        if changes is None:
            return self._run("create_blank_key", (column, layer_index), {})
        return changes

    def clear_sheet_span(self, layer_index, start, stop, *, archive=False):
        changes = None if archive else _CORE_COMMANDS["clear_sheet_span"](
            self, layer_index, start, stop)
        if changes is None:
            return self._run("clear_sheet_span", (layer_index, start, stop),
                             {"archive": archive})
        return changes

    def new_sheet_cell(self, column, layer_index):
        changes = _CORE_COMMANDS["new_sheet_cell"](self, column, layer_index)
        if changes is None:
            return self._run("new_sheet_cell", (column, layer_index), {})
        return changes

    def new_sheet_number(self, column, layer_index):
        changes = _CORE_COMMANDS["new_sheet_number"](self, column, layer_index)
        if changes is None:
            return self._run("new_sheet_number", (column, layer_index), {})
        return changes

    def restore_snapshot(self, snapshot):
        changes = _CORE_COMMANDS["restore_snapshot"](self, snapshot)
        if changes is None:
            return self._run("restore_snapshot", (snapshot,), {})
        return changes

    def snapshot(self):
        """Copy the entire drawing bank, including unplaced drawings.

        Rust の状態では Rust の文書の写し（画素は書き込みまで共有）だけを取り、
        今の手順（clone_frame_graph）の値は読まれたときに写しから作る。今の手順が
        文書に局所の絵を作る位置（局所の絵の無い位置）があるときは今の経路で取る。
        """
        snapshot = _capture_snapshot(self)
        return self._run("snapshot", (), {}) if snapshot is None else snapshot

    @property
    def layers(self):
        """Layers of the current frame."""
        return self.frames[self.current_frame].layers

    @property
    def active_layer(self):
        return self.layers[self.active_layer_index]

    # ------------------------------------------------------------------
    # 窓口が使う読み取り
    # ------------------------------------------------------------------
    def _frame_ids(self):
        return self._live()["state"].frame_ids

    def _track_ids(self):
        return self._live()["state"].track_ids

    def _proxy(self, kind, cls, key):
        cache = self._live()["cache"]
        obj = cache._objects.get((kind, key))
        if obj is None:
            obj = cache._objects[kind, key] = _make(cls, self, key)
        return obj

    def _frame(self, fid):
        return self._proxy("frame", LiveFrame, (fid,))

    def _layer(self, fid, tid):
        return self._proxy("layer", LiveLayer, (fid, tid))

    def _track(self, tid):
        return self._proxy("track", LiveTrack, (tid,))

    def _cell(self, key):
        return self._proxy("cell", LiveCell, key)

    def _duration(self, fid):
        return self._live()["state"].durations.get(fid)

    def _position_value(self, fid, tid):
        return self._live()["state"].position(fid, tid)

    def _track_value(self, tid):
        return self._live()["state"].track(tid)

    def _tween(self, key, value):
        return json.loads(json.dumps(value))

    def _artwork_key(self, fid, tid):
        data = self._position_value(fid, tid)
        if data is None:
            raise StaleDocumentObject("文書にない位置")
        values = data[0]
        number = values["sequence_number"]
        if number is not None and (values["has_content"] or values["sequence_only"]):
            return ("bank", tid, number)
        if values["local"] is None:
            # 今の Layer._cell は局所の絵が無ければ空の絵を作る。
            _core_edit(self._layer(fid, tid), lambda slot: slot._cell)
        return self._live()["state"].local_key(fid, tid)

    def _cell_value(self, key):
        state = self._live()["state"]
        if key[0] == "bank":
            track = state.track(key[1])
            if track is None:
                return None
            for number, cell in track[0]["cells"]:
                if number == key[2]:
                    return cell, track[1]
            return None
        position = state.position(key[1], key[2])
        if position is None or position[0]["local"] is None:
            return None
        return position[0]["local"], position[1]

    def _bound_image(self, key, initial):
        live = self._live()
        cache = live["cache"]
        image = cache._objects.get(("image", (key, initial)))
        if image is not None:
            return image
        value = self._cell_value(key)
        if value is None:
            raise StaleDocumentObject("文書にない絵")
        cell, handles = value
        index = cell["initial_image" if initial else "image"]
        if index is None:
            return None
        image = model_image(handles[index])
        if image is None:
            return None
        _bind(image, _native._CellImage(live["core"], key[0] == "bank", key[1], key[2], initial))
        cache.put("image", (key, initial), image)
        return image

    def _plain_copy(self, fid, tid):
        """位置の素の Layer を返す。列と絵は文書の窓口を参照する（今の Layer と同じく、
        clone_slot などの写しは同じ列・同じ絵を指す）。読むだけの処理に使う。"""
        state = self._live()["state"]
        cached = state.slot_copies.get((fid, tid))
        if cached is not None:
            return cached
        data = state.position(fid, tid)
        track = state.track(tid)
        if data is None or track is None:
            raise StaleDocumentObject("文書にない位置")
        plain = state.plain_tracks.get(tid)
        if plain is None:
            plain = state.plain_tracks[tid] = plain_track(*track)
        slot = plain_slot(data[0], plain, data[1])
        _restore_token(slot, plain, self._live().get("tokens", {}).get((fid, tid)))
        values = slot.__dict__
        values["_track"] = self._track(tid)
        if data[0].get("local_bank"):
            values["_cell"] = self._cell(("bank", tid, int(data[0]["sequence_number"])))
        elif values.get("_cell") is not None:
            values["_cell"] = self._cell(state.local_key(fid, tid))
        state.slot_copies[fid, tid] = slot
        return slot

    def _set_track_setting(self, tid, name, value):
        """列の表示設定を、その列だけ Rust へ書く。書けなければ False（今の経路で書く）。

        Track の設定の代入には副作用が無いので、Rust へ戻すときの値（document_core の
        _settings と JSON）が同じなら、全体を書き出して戻すのと同じ結果になる。
        """
        live = self._live()
        state = live["state"]
        if live["graph"] is not None or state is None:
            return False
        entry = state.track(tid)
        if entry is None:
            return False
        before = entry[0]["settings"]
        probe = Track.__new__(Track)
        for field, current in _plain_settings(before).items():
            setattr(probe, field, current)
        setattr(probe, name, self._plain_value(value))
        try:
            text = json.dumps(_settings(probe), ensure_ascii=False, allow_nan=False)
            previous = json.dumps(before, ensure_ascii=False, allow_nan=False)
            if text == previous:
                return True
            live["core"].set_track_settings_graph(tid, text)
        except (GraphError, TypeError, ValueError):
            return False
        log = live.get("trial_log")
        if log is not None:
            # 窓口のまま試している操作の中。やり直すときに戻す。
            log.append((tid, previous))
        state.refresh_track(tid)
        state.forget_track(tid)
        return True

    def _plain_value(self, value):
        """窓口を、今の素のグラフの対応するオブジェクトに写す。窓口でなければそのまま。"""
        if isinstance(value, _LIVE):
            # 素の状態にすると、窓口のオブジェクトそのものが素の型に戻る。
            _m(value, "_owner")._demote()
            if isinstance(value, _LIVE):
                raise StaleDocumentObject("文書にない窓口")
            return value
        if isinstance(value, (_FramesView, _LayersView)):  # _TracksView も含む
            return [self._plain_value(item) for item in value]
        if type(value) in (list, tuple):
            items = [self._plain_value(item) for item in value]
            return items if type(value) is list else tuple(items)
        return value

    def _plain_args(self, values):
        if isinstance(values, dict):
            return {key: self._plain_value(value) for key, value in values.items()}
        return tuple(self._plain_value(value) for value in values)

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _run(self, name, args, kwargs):
        from .document import GraphDocument
        method = getattr(GraphDocument, name)
        args, kwargs = self._plain_args(args), self._plain_args(kwargs)
        graph = self._demote()
        try:
            result = method(graph.plain, *args, **kwargs)
        except BaseException:
            self._promote()
            raise
        if inspect.isgenerator(result):
            return self._run_generator(result)
        promoted = self._promote()
        return self._live_value(result) if promoted else result

    def _run_generator(self, generator):
        try:
            for item in generator:
                yield item
        finally:
            self._promote()

    def _live_value(self, value):
        """戻り値の素のオブジェクトを、Rust の状態の窓口に写す。"""
        from .document import DrawingTarget, PendingEntry
        live = self._live()
        keys = live.pop("last_keys", {})
        classes = {"frame": LiveFrame, "layer": LiveLayer, "track": LiveTrack, "cell": LiveCell}

        def convert(item):
            if isinstance(item, (LiveLayer, LiveTrack, LiveCell, LiveFrame)):
                return item
            if isinstance(item, (Layer, Track, Cell, Frame)):
                found = keys.get(id(item))
                if found is None:
                    return item
                kind, key = found
                return self._proxy(kind, classes[kind], key)
            if isinstance(item, DrawingTarget):
                return DrawingTarget(convert(item.cell), convert(item.track), item.column,
                                     convert(item.slot), item.number)
            if isinstance(item, PendingEntry):
                return PendingEntry(item.column, convert(item.slot))
            if type(item) in (list, tuple):
                items = [convert(element) for element in item]
                return items if type(item) is list else tuple(items)
            return item

        return convert(value)


def _snapshot_bytes(state):
    """遅延の写しの DocUndo.nbytes（clone_frame_graph の結果の Cell の画像の大きさの和）。

    局所の絵の無い位置（今の手順が文書に絵を作る）があれば None。
    """
    def size(handles, index):
        if index is None:
            return 0
        handle = handles[index]
        return handle.width * handle.height * 4

    def cell_bytes(values, handles):
        index = values["image"]
        if index is not None and handles[index].generation == _NO_IMAGE.generation:
            # 画像が None の Cell。今の手順はその時点の作業領域の大きさの空画像を作る。
            raise _NeedsEager
        return size(handles, index) + size(handles, values["initial_image"])

    try:
        return _snapshot_bytes_of(state, cell_bytes)
    except _NeedsEager:
        return None


class _NeedsEager(Exception):
    """遅延の写しでは今の snapshot と同じにできない（今の経路で取る）。"""


def _snapshot_bytes_of(state, cell_bytes):
    total = 0
    for tid in state.track_ids:
        data, handles = state.track(tid)
        total += sum(cell_bytes(values, handles) for _, values in data["cells"])
    seen = set()
    for fid in state.frame_ids:
        for tid in state.track_ids:
            values, handles = state.position(fid, tid)
            if values["sequence_number"] is not None and (
                    values["has_content"] or values["sequence_only"]):
                continue  # バンクの絵（上で数えた）
            if values["local"] is None:
                return None
            group = values.get("local_share")
            key = ("share", group) if group is not None else (fid, tid)
            if key not in seen:
                seen.add(key)
                total += cell_bytes(values["local"], handles)
    return total


def _capture_snapshot(document):
    """Rust の状態なら遅延の DocumentSnapshot を返す。取れなければ None。"""
    from . import constants
    live = document._live()
    state = live["state"]
    if live["graph"] is not None or state is None:
        return None
    nbytes = _snapshot_bytes(state)
    if nbytes is None:
        return None
    frame, layer = live["cursor"]
    # 今の手順は写しを取るとき（clone_slot）に timeline_revision を進める（キャッシュの判定）。
    Layer.timeline_revision += 1
    return _lazy_snapshot_class()._capture(
        live["core"].copy(), frame, layer, constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT,
        nbytes)


_LAZY_SNAPSHOT = []


def _lazy_snapshot_class():
    """_LazySnapshot（document.DocumentSnapshot の派生）。document との循環 import を避けて作る。"""
    if _LAZY_SNAPSHOT:
        return _LAZY_SNAPSHOT[0]
    from .document import DocumentSnapshot

    def delegate(name):
        def method(self, *args, **kwargs):
            # 相手も遅延の写しなら、相手も中身にしてから比べる（C の tuple の比較は相手の
            # 仮の中身を直に読むため）。
            args = tuple(arg._resolved() if isinstance(arg, cls) else arg for arg in args)
            return getattr(self._resolved(), name)(*args, **kwargs)
        method.__name__ = name
        return method

    def field(index):
        return property(lambda self: self._resolved()[index])

    namespace = {
        "__doc__": """Rust の文書の写しから、読まれたときに中身を作る DocumentSnapshot。

        中身は今の Document.snapshot と同じ値（写しから素のグラフを作って
        clone_frame_graph を当てる）。tuple としての読み取りはすべて中身に任せる。
        """,
        "_capture": classmethod(_capture_lazy),
        "_resolved": _resolve_lazy,
        "image_bytes": lambda self: self.__dict__["_nbytes"],
        "__getitem__": lambda self, index: self._resolved()[index],
        "__iter__": lambda self: iter(self._resolved()),
        "__reduce_ex__": lambda self, protocol: self._resolved().__reduce_ex__(protocol),
        "__copy__": lambda self: self,
        "_replace": lambda self, **kwargs: self._resolved()._replace(**kwargs),
        "_asdict": lambda self: self._resolved()._asdict(),
        "_make": classmethod(lambda _cls, iterable: DocumentSnapshot._make(iterable)),
        "__hash__": lambda self: hash(self._resolved()),
    }
    for name in ("__len__", "__contains__", "__eq__", "__ne__", "__lt__", "__le__",
                 "__gt__", "__ge__", "__add__", "__mul__", "__rmul__", "__repr__",
                 "count", "index"):
        namespace[name] = delegate(name)
    for index, name in enumerate(DocumentSnapshot._fields):
        namespace[name] = field(index)
    cls = type("_LazySnapshot", (DocumentSnapshot,), namespace)
    _LAZY_SNAPSHOT.append(cls)
    return cls


def _capture_lazy(cls, core, frame, layer, width, height, nbytes):
    snapshot = tuple.__new__(cls, (None, frame, layer, width, height, None))
    values = snapshot.__dict__
    values["_core"], values["_value"], values["_nbytes"] = core, None, nbytes
    return snapshot


def _resolve_lazy(self):
    values = self.__dict__
    if values["_value"] is None:
        from .document import DocumentSnapshot, clone_frame_graph
        frames, _, _, _, _ = graph_from_core(values["_core"])
        copied, _ = clone_frame_graph(frames)
        tracks = [layer._track for layer in copied[0].layers] if copied else []
        current, active, width, height = tuple.__getitem__(self, slice(1, 5))
        values["_value"] = DocumentSnapshot(copied, current, active, width, height, tracks)
        values["_core"] = None
    return values["_value"]


def _changes_from_json(text):
    """Rust の命令が返した変更（Python の ChangeSet と同じ種類名・フィールド）を ChangeSet にする。"""
    from . import document_changes as changes
    (group,) = json.loads(text)
    items = []
    for notice in group:
        notice = dict(notice)
        cls = getattr(changes, notice.pop("kind"))
        for name in ("indices", "fields", "before", "after"):
            if isinstance(notice.get(name), list):
                notice[name] = tuple(notice[name])
        if isinstance(notice.get("rects"), list):
            notice["rects"] = tuple(tuple(rect) for rect in notice["rects"])
        items.append(cls(**notice))
    return changes.ChangeSet(tuple(items))


def _apply_core_command(document, operation, rows, apply=None):
    """Rust の文書に構造の命令を当て、窓口が読む写しを行ごとに取り直す。

    rows(frame_ids_before) は、命令で中身が変わり得る既存のコマ ID の列を返す。新しいコマの
    行は自動で読む。命令が当てられなければ None（文書は変わっていない）。apply(core) を
    渡すと、operation の代わりにそれで命令を当てる（apply_command と同じ形の JSON を返す）。
    """
    live = document._live()
    state = live["state"]
    before = list(state.frame_ids)
    changed = rows(before)
    try:
        live["core"].set_blank_image(_blank_handle())
        text = (live["core"].apply_command(json.dumps(operation)) if apply is None
                else apply(live["core"]))
    except ValueError:
        return None
    order = json.loads(live["core"].frames_graph([])[0])["frame_order"]
    known = set(before)
    new_ids = [fid for fid in order if fid not in known]
    state.reload_rows([*changed, *new_ids])
    # 今の Python の手順はどれも Layer の代入を通るので timeline_revision を進める。
    Layer.timeline_revision += 1
    # 選択は Python の値が正（命令は Rust の選択を動かすことがある）。
    document._sync_selection()
    return _changes_from_json(text), new_ids


def _core_duplicate_frame(document, column):
    """duplicate_frame を Rust の命令で当てる。今の手順と同じにできなければ None。

    保持の途中のコマを複製すると、今の手順は既存の位置に新しい Layer を入れ直す（古い
    オブジェクトは文書から外れる）。その付け替えはまだ無いので、その場合は今の経路。
    """
    live = document._live()
    if live["graph"] is not None or live.get("trial") or type(column) is not int:
        return None
    state = live["state"]
    if not 0 <= column < len(state.frame_ids):
        return None
    # 保持の途中なら、今の手順は複製の後ろの保持の位置に新しい Layer を入れ直し（古い
    # オブジェクトは文書から外れる）、保持の最後の位置をその場で消す（局所の絵が替わる）。
    replaced, cleared = [], []
    last = column + 1
    for layer_index, tid in enumerate(state.track_ids):
        block = document.timeline_block_at(column, layer_index)
        if block is not None and block[1] < column:
            end = block[1] + block[2]
            last = max(last, end)
            replaced += [(state.frame_ids[i], tid) for i in range(column + 1, end - 1)]
            if end - 1 > column:
                cleared.append((state.frame_ids[end - 1], tid))
    plan = _detach_plan(document, replaced, cleared)
    if plan is None:
        return None
    # 読み直すのは複製元から保持の終わりまでの行（共有の組の印が変わる位置を含む）。
    touched = state.frame_ids[column:last]
    result = _apply_core_command(document, ["duplicate_frame", column], lambda ids: touched)
    if result is None:
        return None
    changes, new_ids = result
    tokens = live.setdefault("tokens", {})
    for fid in new_ids:
        for tid in state.track_ids:
            tokens[fid, tid] = PendingToken()
    for key in replaced:
        tokens[key] = PendingToken()
    state.pending.clear()
    _detach(document, plan)
    return changes


def _detach_plan(document, replaced, cleared):
    """今の手順が文書から外すオブジェクト（入れ直す位置の Layer とその局所の絵、その場で
    消す位置の局所の絵）を、命令の前の値で用意する。用意できなければ None。

    外すのは窓口として渡したもの（表にあるもの）だけでよい（誰も持っていなければ見えない）。
    局所の絵を他の位置と共有する位置は、外す Cell の同一性を保てないので None。
    """
    live = document._live()
    state, cache = live["state"], live["cache"]
    plan = {"layers": [], "cells": []}
    for key, whole in [*((key, True) for key in replaced), *((key, False) for key in cleared)]:
        values, handles = state.position(*key)
        if values.get("local_share") is not None:
            return None
        local = ("local", *key)
        images = {}
        for initial in (False, True):
            image = cache._objects.get(("image", (local, initial)))
            if image is not None:
                try:
                    images[initial] = (image, image._image.copy())
                except ValueError:
                    return None
        cell = cache._objects.get(("cell", local))
        if cell is not None or images:
            plan["cells"].append((local, cell, images, values, handles))
        layer = cache._objects.get(("layer", key)) if whole else None
        if layer is not None:
            token = live.get("tokens", {}).get(key)
            plan["layers"].append((key, layer, values, handles, token))
    return plan


def _detach(document, plan, moved=None, between=None, release=True):
    """_detach_plan で用意したオブジェクトを、命令の前の値の素のオブジェクトにして文書から外す。

    moved は位置 → 局所の Cell が移った先の位置（外す Layer が、文書に残る同じ Cell を
    指すように）。between は Cell を外したあと、Layer を外す前に呼ぶ。release が偽なら、
    外す Layer の配置待ちを解かない（今の手順が replace_slot を通さず直接入れ替える場合）。
    """
    from .document import clear_pending
    live = document._live()
    cache = live["cache"]
    cells = {}
    for local, cell, images, values, handles in plan["cells"]:
        for initial, (image, pixels) in images.items():
            cache.pop("image", (local, initial))
            image.__class__ = PmaImage
            object.__setattr__(image, "_image", pixels)
            object.__setattr__(image, "_attributes", tag_attributes(pixels.tag))
        data = values["local"]
        if data is None:
            stand = None
        else:
            stand = Cell.__new__(Cell)
            for initial, name in ((False, "image"), (True, "initial_image")):
                found = images.get(initial)
                index = data[name]
                value = (found[0] if found else
                         None if index is None else model_image(handles[index]))
                object.__setattr__(stand, name, value)
            stand.cell_name = data["cell_name"]
        if cell is not None:
            cache.pop("cell", local)
            if stand is not None:
                _transplant(cell, stand)
                stand = cell
        cells[local] = stand
    if between is not None:
        between()
    # 素の Layer を作るための仮の Track（配置待ちの札の出し入れと、あとで付け替える _cell にだけ
    # 使い、外した Layer は今の列の窓口を指す）。列ごとに一つ作って使い回す。
    temporaries = {}
    for key, layer, values, handles, token in plan["layers"]:
        cache.pop("layer", key)
        track_proxy = document._track(key[1])
        temporary = temporaries.get(key[1])
        if temporary is None:
            temporary = temporaries[key[1]] = plain_track(*live["state"].track(key[1]))
        stand = plain_slot(values, temporary, handles)
        _restore_token(stand, temporary, token)
        local = ("local", *key)
        if values.get("local_bank"):
            stand.__dict__["_cell"] = document._cell(("bank", key[1], int(values["sequence_number"])))
        elif moved and key in moved and values["local"] is not None:
            stand.__dict__["_cell"] = document._cell(("local", *moved[key]))
        elif local in cells and cells[local] is not None:
            stand.__dict__["_cell"] = cells[local]
        # 今の手順（replace_slot）は外す位置の配置待ちを解く。
        if release:
            clear_pending(stand)
        stand.__dict__["_track"] = track_proxy
        _transplant(layer, stand)


def _core_delete_frame(document, column, layer_index):
    """delete_frame を Rust の命令で当てる。今の手順と同じにできなければ None。"""
    return _core_shift_left(document, "delete_frame", column, layer_index)


def _core_delete_track_frame(document, column, layer_index):
    """delete_track_frame を Rust の命令で当てる。今の手順と同じにできなければ None。"""
    return _core_shift_left(document, "delete_track_frame", column, layer_index)


def _core_shift_left(document, name, column, layer_index):
    """列の column の位置を消して後ろを前へ寄せる命令（delete_frame・delete_track_frame）。

    今の手順（_shift_timeline_layer_left）は列の column 以降の位置を次の位置の clone_slot
    で入れ直し（古い Layer は文書から外れ、局所の Cell は写しとともに一つ前の位置へ移る）、
    最後の位置をその場で消す。delete_frame が末尾の空いたコマを取り除く場合と、局所の絵を
    共有する位置がある場合は今の経路。
    """
    live = document._live()
    if (live["graph"] is not None or live.get("trial") or type(column) is not int
            or type(layer_index) is not int):
        return None
    state = live["state"]
    frame_ids = list(state.frame_ids)
    count = len(frame_ids)
    if not (0 <= column < count and 0 <= layer_index < len(state.track_ids)):
        return None
    tid = state.track_ids[layer_index]
    if any(state.position(fid, tid)[0].get("local_share") is not None
           for fid in frame_ids[column:]):
        return None
    block = document.timeline_block_at(column, layer_index)
    operation = [name, column, layer_index]
    if name == "delete_frame":
        if block is None and count == 1:
            from .document_changes import ChangeSet
            return ChangeSet()  # 今の手順は何もしない
        # 末尾のコマを取り除くかは、写しに当てて確かめる（取り除いたコマの付け替えはまだ無い）。
        trial = live["core"].copy()
        trial.set_blank_image(_blank_handle())
        try:
            trial.apply_command(json.dumps(operation))
        except ValueError:
            return None
        if len(json.loads(trial.frames_graph([])[0])["frame_order"]) != count:
            return None
    replaced = [(fid, tid) for fid in frame_ids[column:count - 1]]
    plan = _detach_plan(document, replaced[:1], [] if replaced else [(frame_ids[column], tid)])
    if plan is None:
        return None
    # 消す位置より後ろの Layer も入れ直される（局所の Cell は一つ前へ移るので外さない）。
    cache = live["cache"]
    for key in replaced[1:]:
        layer = cache._objects.get(("layer", key))
        if layer is not None:
            values, handles = state.position(*key)
            plan["layers"].append((key, layer, values, handles, live.get("tokens", {}).get(key)))
    first = column if block is None else min(column, block[1])
    result = _apply_core_command(document, operation, lambda ids: frame_ids[first:])
    if result is None:
        return None
    changes, _ = result
    state.refresh_track(tid)
    state.forget_track(tid)
    moves = [((frame_ids[index], tid), (frame_ids[index - 1], tid))
             for index in range(column + 1, count)]
    # 消す位置の局所の Cell を外し、後ろの局所の Cell・画像の窓口を一つ前の位置へ付け替えて
    # （前から順に、移り先を空けてから）、入れ直された Layer を外す（局所の Cell は移った先）。
    tokens = live.setdefault("tokens", {})
    for key in replaced:
        tokens[key] = PendingToken()
    state.pending.clear()
    _detach(document, plan, moved=dict(moves), between=lambda: _rekey_locals(document, moves))
    return changes


def _core_insert_track_frame(document, column, layer_index):
    """insert_track_frame を Rust の命令で当てる。今の手順と同じにできなければ None。

    今の手順（_shift_timeline_layer_right）は列の column より後ろの位置を一つ前の位置の
    clone_slot で入れ直し（古い Layer は文書から外れ、局所の Cell は写しとともに一つ後ろの
    位置へ移る）、column の位置をその場で消す。保持の途中に挿入するときは column に保持の
    参照（clone_reference、元の局所の Cell を共有する）を入れ直すので、その場合と、局所の
    絵を共有する位置がある場合は今の経路。
    """
    live = document._live()
    if (live["graph"] is not None or live.get("trial") or type(column) is not int
            or type(layer_index) is not int):
        return None
    state = live["state"]
    frame_ids = list(state.frame_ids)
    count = len(frame_ids)
    if not (0 <= column < count and 0 <= layer_index < len(state.track_ids)):
        return None
    tid = state.track_ids[layer_index]
    if any(state.position(fid, tid)[0].get("local_share") is not None
           for fid in frame_ids[column:]):
        return None
    block = document.timeline_block_at(column, layer_index)
    if block is not None and column > block[1]:
        return None  # 保持の途中（fill あり）
    replaced = [(fid, tid) for fid in frame_ids[column + 1:]]
    plan = _detach_plan(document, [], [])
    if plan is None:
        return None
    cache = live["cache"]
    for key in replaced:
        layer = cache._objects.get(("layer", key))
        if layer is not None:
            values, handles = state.position(*key)
            plan["layers"].append((key, layer, values, handles, live.get("tokens", {}).get(key)))
    first = column if block is None else min(column, block[1])
    result = _apply_core_command(document, ["insert_track_frame", column, layer_index],
                                 lambda ids: frame_ids[first:])
    if result is None:
        return None
    changes, new_ids = result
    after = state.frame_ids
    # 局所の Cell は一つ後ろへ移る（後ろから順に、移り先を空けてから）。
    moves = [((after[index], tid), (after[index + 1], tid))
             for index in range(count - 1, column - 1, -1)]
    tokens = live.setdefault("tokens", {})
    for fid in new_ids:
        for track in state.track_ids:
            tokens[fid, track] = PendingToken()
    for key in replaced:
        tokens[key] = PendingToken()
    state.pending.clear()
    _detach(document, plan, moved=dict(moves), between=lambda: _rekey_locals(document, moves))
    return changes


def _core_ensure_frame_count(document, count):
    """ensure_frame_count を Rust の命令で当てる。今の手順と同じにできなければ None。

    今の手順は新しいコマ（make_frame、各位置は空画像の局所の Cell）を末尾に足すだけで、
    既存のオブジェクトは変えない。
    """
    from .document_changes import ChangeSet
    live = document._live()
    if live["graph"] is not None or live.get("trial") or type(count) is not int:
        return None
    state = live["state"]
    if count <= len(state.frame_ids):
        return ChangeSet()
    result = _apply_core_command(document, ["ensure_frame_count", count], lambda ids: [])
    if result is None:
        return None
    changes, new_ids = result
    tokens = live.setdefault("tokens", {})
    for fid in new_ids:
        for tid in state.track_ids:
            tokens[fid, tid] = PendingToken()
    state.pending.clear()
    return changes


def _core_advance_frame(document):
    """advance_frame を Rust の命令で当てる（今の手順と同じく、現在コマ＋1 まで伸ばす）。"""
    from .document_changes import ChangeSet, Cursor
    live = document._live()
    if live["graph"] is not None or live.get("trial"):
        return None
    current = live["cursor"][0]
    if type(current) is not int:
        return None
    column = current + 1
    changes = _core_ensure_frame_count(document, column + 1)
    if changes is None:
        return None
    return ChangeSet((*changes.changes, Cursor(column)))


def _core_add_track(document):
    """add_track を Rust の命令で当てる。今の手順と同じにできなければ None。

    今の手順は新しい列（各位置は空画像の局所の Cell、一つの Track を共有）を末尾に足し、
    sync_columns で tracks に新しいリストを入れる。既存の位置・コマ・列のオブジェクトは
    変えない。ただし sync_columns は配置待ちの所属を位置が持つ位置（pending_detached）を
    列へつなぎ直すので、そういう位置があれば今の経路。
    """
    live = document._live()
    if live["graph"] is not None or live.get("trial"):
        return None
    state = live["state"]
    if not state.frame_ids or any(values.get("pending_detached")
                                  for values, _ in state.positions.values()):
        return None
    before = set(state.track_ids)
    # 今の手順は tracks に新しいリストを入れるので、渡した並びの窓口は前の中身に固定する。
    old_tracks = list(document.tracks)
    # 新しい列の位置は全部のコマの行に入るので、全部の行を読み直す。
    result = _apply_core_command(document, ["add_track"], lambda ids: list(ids))
    if result is None:
        return None
    changes, _ = result
    (tid,) = [tid for tid in state.track_ids if tid not in before]
    state.refresh_track(tid)
    tokens = live.setdefault("tokens", {})
    for fid in state.frame_ids:
        tokens[fid, tid] = PendingToken()
    state.pending.clear()
    document._retire_view("tracks_view", old_tracks)
    return changes


def _core_restore_inserted_track(document, e):
    """restore_inserted_track（列の削除の Undo）を Rust の命令で当てる。できなければ None。

    今の手順は外した列の写し e.layers を clone_frame_graph で写し、一つの Track を共有させて
    各コマの列 e.index に入れ、sync_columns で tracks に新しいリストを入れる。写しを一列だけの
    Rust の文書にし、その列を insert_track で入れる。写しのコマ数が文書と違う（足りない分を
    新しい Layer で埋める）、写しが一つの Track を共有していない、e.index が範囲外、写しや
    文書に配置待ち・pending_detached の位置がある、写しに局所の絵の共有の組がある、なら
    今の経路。
    """
    from .document import GraphDocument
    live = document._live()
    if live["graph"] is not None or live.get("trial"):
        return None
    state = live["state"]
    layers = list(getattr(e, "layers", None) or ())
    if (not state.frame_ids or len(layers) != len(state.frame_ids)
            or not all(isinstance(layer, Layer) for layer in layers)):
        return None
    try:
        index, target = int(e.index), int(e.target_active)
    except (TypeError, ValueError):
        return None
    if any(values.get("pending_detached") for values, _ in state.positions.values()):
        return None
    count = len(state.track_ids)
    # 今の手順は通知に丸める前の e.index を入れるので、範囲外なら今の経路。
    if not 0 <= index <= count:
        return None
    # 記録の列（capture_column の写し）は一つの Track を共有している。そのまま Rust の文書に
    # する（写しを取り直すのと同じ値になり、Rust の文書は画素を書き込みまで共有し、記録の
    # オブジェクトは変えない）。共有していない記録は今の経路。
    if not all(layer._track is layers[0]._track for layer in layers):
        return None
    stored = layers
    track = stored[0]._track
    if (track.pending_slots or not track.connected
            or any(layer.sequence_only or layer.__dict__.get("_pending_detached")
                   for layer in stored)):
        return None
    plain = object.__new__(GraphDocument)
    plain.frames = [Frame([layer]) for layer in stored]
    plain.tracks = [track]
    plain.current_frame = plain.active_layer_index = 0
    try:
        text, images = graph_payload(plain)
        if any(position.get("local_share") is not None
               for frame in json.loads(text)["frames"] for position in frame["positions"]):
            return None
        source = _native._CoreDocument.from_graph(text, images)
    except (GraphError, ValueError, KeyError, TypeError):
        return None
    at = index
    active = max(0, min(target, count))
    before = set(state.track_ids)
    old_tracks = list(document.tracks)
    result = _apply_core_command(
        document, None, lambda ids: list(ids),
        apply=lambda core: core.insert_track_from(source, 0, at, active))
    if result is None:
        return None
    changes, _ = result
    (tid,) = [tid for tid in state.track_ids if tid not in before]
    state.refresh_track(tid)
    tokens = live.setdefault("tokens", {})
    # 今の手順は写しの位置を新しく作るので、札も新しい（記録の位置の札は使わない）。
    for fid in state.frame_ids:
        tokens[fid, tid] = PendingToken()
    state.pending.clear()
    document._retire_view("tracks_view", old_tracks)
    return changes


def _has_no_image(cell, handles):
    """Cell の絵が None（Rust では 0×0 の番兵）か。"""
    index = cell["image"]
    return index is not None and handles[index].generation == _NO_IMAGE.generation


def _core_duplicate_tracks(document, names):
    """duplicate_tracks を Rust の命令で当てる。今の手順と同じにできなければ None。

    今の手順は各列を capture_column で写し（独立したバンク）、名前を替えて元の列の直後に
    入れ、sync_columns で tracks に新しいリストを入れる。既存の位置・コマ・列のオブジェクト
    は変えない。どこかに pending_detached の位置がある（sync_columns がつなぎ直す）、複製
    する列に配置待ちの位置・局所の絵の共有の組・列の外の配置待ちの札がある、なら今の経路。
    """
    live = document._live()
    if live["graph"] is not None or live.get("trial"):
        return None
    state = live["state"]
    count = len(state.track_ids)
    try:
        items = sorted(dict(names).items())
    except (TypeError, ValueError):
        return None
    if (not items or not state.frame_ids
            or any(type(index) is not int or not 0 <= index < count or type(name) is not str
                   for index, name in items)):
        return None
    track_ids = {state.track_ids[index] for index, _ in items}
    for (_, tid), (values, handles) in state.positions.items():
        if values.get("pending_detached"):
            return None
        if tid in track_ids and (values["sequence_only"] or values.get("local_share") is not None):
            return None
        # 今の手順の capture_column（clone_frame_graph）は絵が None の位置に空の絵を作って
        # 写す。Rust の複製は絵の無いまま写すので、そういう位置があれば今の経路。
        if (tid in track_ids and values["local"] is not None
                and _has_no_image(values["local"], handles)):
            return None
    for tid in track_ids:
        data, handles = state.track(tid)
        if any(_has_no_image(cell, handles) for _, cell in data["cells"]):
            return None
    if any(live.get("extra_pending", {}).get(tid) for tid in track_ids):
        return None
    before = set(state.track_ids)
    old_tracks = list(document.tracks)
    result = _apply_core_command(
        document, ["duplicate_tracks", [[index, name] for index, name in items]],
        lambda ids: list(ids))
    if result is None:
        return None
    changes, _ = result
    tokens = live.setdefault("tokens", {})
    for tid in [tid for tid in state.track_ids if tid not in before]:
        state.refresh_track(tid)
        for fid in state.frame_ids:
            tokens[fid, tid] = PendingToken()
    state.pending.clear()
    document._retire_view("tracks_view", old_tracks)
    return changes


def _core_move_tracks(document, order):
    """move_tracks を Rust の命令で当てる。今の手順と同じにできなければ None。

    今の手順は各コマの layers に並べ替えた新しいリストを入れ、sync_columns で tracks に新しい
    リストを入れる。位置・列・コマのオブジェクトと値は変わらず、Layer の代入も通らない
    （timeline_revision は進まない）。それまでに渡したコマの並び・列の並びは入れ替え前の
    リストのまま。pending_detached の位置がある（sync_columns がつなぎ直す）、局所の絵の
    共有の組が複数の列にまたがる（組の先頭が変わる）なら今の経路。
    """
    live = document._live()
    if live["graph"] is not None or live.get("trial"):
        return None
    state = live["state"]
    count = len(state.track_ids)
    try:
        order = tuple(order)
    except TypeError:
        return None
    if (not state.frame_ids or len(order) != count
            or any(type(index) is not int for index in order)
            or sorted(order) != list(range(count)) or order == tuple(range(count))):
        return None
    columns = {}
    for (_, tid), (values, _) in state.positions.items():
        if values.get("pending_detached"):
            return None
        group = values.get("local_share")
        if group is not None and columns.setdefault(group, tid) != tid:
            return None
    old_tracks = list(document.tracks)
    # 渡したコマの並びの窓口は、入れ替え前の中身に固定する。
    held = []
    for (kind, _), frame in live["cache"].items():
        view = frame.__dict__.get("_layers_view") if kind == "frame" else None
        if view is not None:
            held.append((frame, view, list(view)))
    try:
        live["core"].set_blank_image(_blank_handle())
        text = live["core"].apply_command(json.dumps(["move_tracks", list(order)]))
    except ValueError:
        return None
    for frame, view, items in held:
        view._frozen = items
        del frame.__dict__["_layers_view"]
    state.reload_rows([])
    state.layer_lists.clear()
    document._sync_selection()
    document._retire_view("tracks_view", old_tracks)
    return _changes_from_json(text)


def _retire_columns(document, track_ids, frozen_core, old_state):
    """列 track_ids の窓口を、文書から外れた別の Document（写し frozen_core が正本）へ移す。

    今の手順（remove_tracks）は列の位置を frame.layers から外し、外れた Layer・Track・Cell・
    画像は今の値のまま残る。C4b-9 の restore と同じく、書き出す代わりに窓口の持ち主を
    外れた Document に付け替える。外れた側は命令の前の状態の写し old_state を読む。
    """
    live = document._live()
    retired = object.__new__(Document)
    old_state.core = frozen_core
    cache = _Cache()
    tokens = live.get("tokens", {})
    retired.__dict__["_live_state"] = {
        "graph": None, "core": frozen_core, "state": old_state, "cache": cache,
        "cursor": list(live["cursor"]), "next_ids": old_state.next_ids, "last_keys": {},
        "extra_pending": {tid: live["extra_pending"][tid] for tid in track_ids
                          if tid in live.get("extra_pending", {})},
        "tokens": {key: token for key, token in tokens.items() if key[1] in track_ids},
    }

    def removed(kind, key):
        if kind in ("layer", "track"):
            return key[-1] in track_ids if kind == "layer" else key[0] in track_ids
        if kind == "cell":
            return (key[1] if key[0] == "bank" else key[2]) in track_ids
        if kind == "image":
            place = key[0]
            return (place[1] if place[0] == "bank" else place[2]) in track_ids
        return False

    for (kind, key), obj in live["cache"].items():
        if not removed(kind, key):
            continue
        live["cache"].pop(kind, key)
        cache.put(kind, key, obj)
        if kind == "image":
            # restore と同じく今の画素を持つ独立した画像に戻し、外れた側の表に残す。命令の
            # あとの文書にはもうこの列の絵が無いので、写しの同じ絵へ結び直してから戻す。
            (place, first, second), initial = key
            _bind(obj, _native._CellImage(frozen_core, place == "bank", first, second, initial))
            _unbind(obj)
            continue
        _setm(obj, "_owner", retired)
    for key in [key for key in tokens if key[1] in track_ids]:
        del tokens[key]
    for tid in track_ids:
        live.get("extra_pending", {}).pop(tid, None)


def _core_remove_columns(document, indices, active):
    """列を Rust の命令で消し、外れた列の窓口を外れた Document へ移す。できなければ None。

    今の手順は外す位置の配置待ちを解き（release_slots）、sync_columns で tracks に新しい
    リストを入れ、配置待ちの所属を位置が持つ位置をつなぎ直す。消す列に配置待ちの位置・
    局所の絵の共有の組がある場合と、pending_detached の位置がある場合は今の経路。
    """
    live = document._live()
    if live["graph"] is not None or live.get("trial"):
        return None
    state = live["state"]
    count = len(state.track_ids)
    if (not indices or any(type(index) is not int or not 0 <= index < count for index in indices)
            or len(indices) >= count or not state.frame_ids):
        return None
    track_ids = {state.track_ids[index] for index in indices}
    for (_, tid), (values, _) in state.positions.items():
        if values.get("pending_detached"):
            return None
        if tid in track_ids and (values["sequence_only"] or values.get("local_share") is not None):
            return None
    if any(live.get("extra_pending", {}).get(tid) for tid in track_ids):
        return None
    operation = (["remove_tracks", sorted(indices)] if active is None
                 else ["remove_tracks", sorted(indices), active])
    old_tracks = list(document.tracks)
    core = live["core"]
    frozen = core.copy()
    try:
        core.set_blank_image(_blank_handle())
        text = core.apply_command(json.dumps(operation))
    except ValueError:
        return None
    # 命令が当たった文書の状態を先に入れ、窓口の付け替えはそのあと（途中で失敗しても、
    # 文書の読み書きは命令のあとの状態にそろう）。今の手順は配置待ちでない位置の
    # timeline_revision を進めない（release_slots は値が同じ、pop は代入を通らない）。
    live["state"] = _CoreState(core)
    _retire_columns(document, track_ids, frozen, state)
    document._sync_selection()
    document._retire_view("tracks_view", old_tracks)
    return _changes_from_json(text)


def _core_remove_tracks(document, indices, *, active=None):
    """remove_tracks を Rust の命令で当てる。今の手順と同じにできなければ None。"""
    indices = tuple(sorted(set(indices)))
    if active is not None and type(active) is not int:
        return None
    return _core_remove_columns(document, indices, active)


def _core_restore_removed_track(document, e):
    """restore_removed_track（列の追加の Undo）を Rust の命令で当てる。"""
    live = document._live()
    if live["graph"] is not None or live.get("trial"):
        return None
    index, target = e.index, e.target_active
    count = len(live["state"].track_ids)
    if type(index) is not int or not 0 <= index < count or count < 2:
        return None
    try:
        active = max(0, min(int(target), count - 2))
    except (TypeError, ValueError):
        return None
    return _core_remove_columns(document, (index,), active)


def _rekey_locals(document, moves):
    """局所の Cell・画像の窓口を、移った先の位置のキーへ付け替える。"""
    live = document._live()
    cache, core = live["cache"], live["core"]
    for old, new in moves:
        old_key, new_key = ("local", *old), ("local", *new)
        cell = cache.pop("cell", old_key)
        if cell is not None:
            _setm(cell, "_key", new_key)
            cache.put("cell", new_key, cell)
        for initial in (False, True):
            image = cache.pop("image", (old_key, initial))
            if image is not None:
                _bind(image, _native._CellImage(core, False, new[0], new[1], initial))
                cache.put("image", (new_key, initial), image)


def _core_create_blank_key(document, column, layer_index):
    """create_blank_key を Rust の命令で当てる。今の手順と同じにできなければ None。

    今の手順は、空キーにする位置をその場で書き換え（detach_cell で局所の Cell が替わる）、
    その後ろ（保持の途中で分けた残り）や前（未使用のセルの直前のキーからの間）の位置に
    clone_reference() を直接入れる（replace_slot を通さないので、外れた古い Layer は配置待ちを
    解かれず、その札は列に残る）。キーを選んだとき（後ろへ寄せてから書き換える）と、
    局所の絵を共有する位置がある場合は今の経路。
    """
    live = document._live()
    if (live["graph"] is not None or live.get("trial") or type(column) is not int
            or type(layer_index) is not int):
        return None
    state = live["state"]
    frame_ids = list(state.frame_ids)
    count = len(frame_ids)
    if not (0 <= column < count and 0 <= layer_index < len(state.track_ids)):
        return None
    tid = state.track_ids[layer_index]
    block = document.timeline_block_at(column, layer_index)
    if block is not None:
        start, exposure = block[1], max(1, block[2])
        if column == start:
            return None  # キーを選んだとき（後ろへ寄せる）
        head = start + min(max(1, column - start), exposure - 1)
        replaced = list(range(head + 1, start + exposure))
        first = start
    else:
        head = column
        previous = None
        for candidate in range(column - 1, -1, -1):
            values = state.position(frame_ids[candidate], tid)[0]
            if values["has_content"] or values["is_blank_key"]:
                previous = candidate
                break
        replaced = [] if previous is None else list(range(previous + 1, column))
        first = column if previous is None else previous
    stop = max([head, *replaced]) + 1
    if stop > count:
        return None  # 保持が文書の末尾より先まで続く（今の手順がコマを足す）
    # 今の手順は元にする位置（保持の先頭・直前のキー）を clone() するので、バンクを参照せず
    # 局所の絵の無い位置には、そこで空の Cell ができる（写しとも共有する）。
    values = state.position(frame_ids[first], tid)[0]
    number = values["sequence_number"]
    if (first != head and values["local"] is None
            and not (number is not None and (values["has_content"] or values["sequence_only"]))):
        return None
    keys = [(frame_ids[index], tid) for index in range(first, stop)]
    if any(state.position(*key)[0].get("local_share") is not None for key in keys):
        return None
    replaced_keys = [(frame_ids[index], tid) for index in replaced]
    plan = _detach_plan(document, replaced_keys, [(frame_ids[head], tid)])
    if plan is None:
        return None
    # 直接入れ替えるので、外れる配置待ちの位置の札は列に残る（今の手順の pending_slots）。
    kept = []
    for key in replaced_keys:
        values = state.position(*key)[0]
        token = live.get("tokens", {}).get(key)
        if token is not None and values["sequence_only"] and not values.get("pending_detached"):
            kept.append(token)
    rows = frame_ids[first:stop]
    result = _apply_core_command(document, ["create_blank_key", column, layer_index],
                                 lambda ids: rows)
    if result is None:
        return None
    changes, new_ids = result
    tokens = live.setdefault("tokens", {})
    for fid in new_ids:
        for track in state.track_ids:
            tokens[fid, track] = PendingToken()
    for key in replaced_keys:
        tokens[key] = PendingToken()
    if kept:
        extra = live.setdefault("extra_pending", {})
        extra.setdefault(tid, WeakSet()).update(kept)
    state.pending.clear()
    _detach(document, plan, release=False)
    return changes


def _core_clear_sheet_span(document, layer_index, start, stop):
    """clear_sheet_span（archive なし）を Rust の命令で当てる。今の手順と同じにできなければ None。

    今の手順は範囲の位置をその場で消す（clear_slot。Layer はそのまま、detach_cell で局所の
    Cell が替わり、画像は新しい空画像になる）。局所の絵を共有する位置がある場合は今の経路。
    """
    live = document._live()
    if (live["graph"] is not None or live.get("trial")
            or any(type(value) is not int for value in (layer_index, start, stop))):
        return None
    state = live["state"]
    frame_ids = list(state.frame_ids)
    if not (0 <= layer_index < len(state.track_ids) and 0 <= start < stop <= len(frame_ids)):
        return None
    tid = state.track_ids[layer_index]
    keys = [(frame_ids[index], tid) for index in range(start, stop)]
    plan = _detach_plan(document, [], keys)
    if plan is None:
        return None
    result = _apply_core_command(document, ["clear_sheet_span", layer_index, start, stop],
                                 lambda ids: ids[start:stop])
    if result is None:
        return None
    changes, _ = result
    state.pending.clear()
    _detach(document, plan)
    return changes


def _core_split_sheet_clip(document, column, layer_index):
    """split_sheet_clip を Rust の命令で当てる。今の手順と同じにできなければ None。

    今の手順は保持の先頭の絵を写して新しい番号でバンクに入れ、column から保持の終わりまでの
    位置をその場で書き換えてその番号を指させる（Layer はそのまま、配置待ちを解き、中割りの
    印を消す）。Layer・局所の Cell・画像のオブジェクトは変えない。分けられない（保持の先頭・
    保持の外）とき、先頭が番号付きの内容でないとき、保持の位置が局所の絵や旧形式の記録を
    持つときは今の経路。
    """
    live = document._live()
    if (live["graph"] is not None or live.get("trial") or type(column) is not int
            or type(layer_index) is not int):
        return None
    state = live["state"]
    frame_ids = list(state.frame_ids)
    if not (0 <= column < len(frame_ids) and 0 <= layer_index < len(state.track_ids)):
        return None
    block = document.timeline_block_at(column, layer_index)
    if block is None or column <= block[1]:
        return None  # 今の手順は何もしない
    _kind, start, exposure = block
    stop = start + exposure
    if stop > len(frame_ids):
        return None
    tid = state.track_ids[layer_index]
    # 今の手順は先頭を clone() して写す（内容の位置ならバンクの絵、そうでなければ局所の絵）。
    # Rust は残った番号でもバンクを優先するので、先頭は番号付きの内容に限る。
    head = state.position(frame_ids[start], tid)[0]
    if not (head["has_content"] and head["sequence_number"] is not None):
        return None
    # 今の手順は書き換える位置の番号と印だけを変え、局所の Cell（Layer._cell）や旧形式の記録は
    # 残す。Rust はそれらを消すので、そういうものを持つ位置があれば今の経路。
    for index in range(start, stop):
        values = state.position(frame_ids[index], tid)[0]
        if (values["local"] is not None or values["local_bank"]
                or values.get("local_share") is not None or values.get("pending_detached")
                or any(values.get(name) is not None for name in _SPLIT_RECORDS)
                or values.get("legacy_missing")):
            return None
    result = _apply_core_command(document, ["split_sheet_clip", column, layer_index],
                                 lambda ids: ids[column:stop])
    if result is None:
        return None
    changes, _ = result
    # 新しい番号の絵がバンクに入るので、列の写しも取り直す。
    state.refresh_track(tid)
    state.forget_track(tid)
    state.pending.clear()
    return changes


_SPLIT_RECORDS = ("v1_position", "dense_import", "content_origin", "resolve_source",
                  "length_imported")


def _rekey_bank(document, tid, mapping):
    """列のバンクの Cell・画像の窓口を、送った先の番号のキーへ付け替える（今の手順は
    track.cells の Cell を新しい番号へ移すだけで、Cell・画像のオブジェクトは変えない）。"""
    live = document._live()
    cache, core = live["cache"], live["core"]
    moved = []
    for old, new in mapping.items():
        old_key = ("bank", tid, old)
        cell = cache.pop("cell", old_key)
        images = [(initial, cache.pop("image", (old_key, initial))) for initial in (False, True)]
        moved.append((new, cell, images))
    for new, cell, images in moved:
        new_key = ("bank", tid, new)
        if cell is not None:
            _setm(cell, "_key", new_key)
            cache.put("cell", new_key, cell)
        for initial, image in images:
            if image is not None:
                _bind(image, _native._CellImage(core, True, tid, new, initial))
                cache.put("image", (new_key, initial), image)


def _core_insert_sequence_blank(document, layer_index, after_number):
    """insert_sequence_blank を Rust の命令で当てる。今の手順と同じにできなければ None。

    今の手順は after_number の後ろの番号を一つずつ送り（位置の番号をその場で書き換え、
    バンクの Cell は新しい番号へ移るだけ）、空の絵を新しい番号でバンクに入れ、末尾に足した
    コマの位置を配置待ちの空キーにする。既存の Layer・Cell・画像のオブジェクトは変えない。
    バンクが空で番号付きの位置がある（今の手順は番号を 1 にしない）、番号でない
    after_number、送る番号の絵を局所の絵として共有する位置がある、なら今の経路。
    """
    live = document._live()
    if (live["graph"] is not None or live.get("trial") or type(layer_index) is not int
            or type(after_number) is not int):
        return None
    state = live["state"]
    if not (0 <= layer_index < len(state.track_ids)) or not state.frame_ids:
        return None
    tid = state.track_ids[layer_index]
    numbers = [number for number, _ in state.track(tid)[0]["cells"]]
    rows = []
    for fid in state.frame_ids:
        values = state.position(fid, tid)[0]
        if values.get("local_share") is not None or values.get("pending_detached"):
            return None
        if (values["sequence_number"] is not None or values.get("v1_position") is not None
                or values.get("dense_import") is not None):
            rows.append(fid)
        if values["sequence_number"] is not None:
            if not numbers and values["sequence_number"] >= 1 and (
                    values["has_content"] or values["sequence_only"]):
                return None
    insert_number = 1 if not numbers else max(1, after_number + 1)
    mapping = {number: number + 1 for number in numbers if number >= insert_number}
    # 今の手順は番号を代入するので（Layer.__setattr__）その位置の _dense_import が消えるが、
    # Rust は取り込み時の記録を残す。送る番号の位置が記録を持つなら今の経路。
    for fid in rows:
        values = state.position(fid, tid)[0]
        if (values.get("dense_import") is not None
                and values["sequence_number"] in mapping):
            return None
    # 新しい番号の絵は今の手順と同じく Cell(blank_image()) で作る（取り込みは直前の取り込み
    # しだいで雛形と画素・世代を共有する。PmaImage の _last_import）。Rust の命令のあとで
    # その絵に置き換える。
    from .models import Cell
    from .pma_image import attributes_tag
    from .utils import blank_image
    image = Cell(blank_image()).image
    handle = image._image.copy().with_tag(attributes_tag(image._attributes))
    cell_value = json.dumps({"image": 0, "initial_image": None, "cell_name": None})

    def apply(core):
        text = core.apply_command(json.dumps(
            ["insert_sequence_blank", layer_index, after_number]))
        core.set_cell_graph(tid, insert_number, cell_value, [handle])
        return text
    result = _apply_core_command(document, None, lambda ids: rows, apply)
    if result is None:
        return None
    changes, new_ids = result
    _rekey_bank(document, tid, mapping)
    state.refresh_track(tid)
    state.forget_track(tid)
    tokens = live.setdefault("tokens", {})
    for fid in new_ids:
        for track in state.track_ids:
            tokens[fid, track] = PendingToken()
    state.pending.clear()
    return changes


def _core_delete_sequence_entry(document, layer_index, number):
    """delete_sequence_entry を Rust の命令で当てる。今の手順と同じにできなければ None。

    今の手順はその番号を指す位置をその場で消し（clear_slot。Layer はそのまま、局所の Cell が
    替わり、画像は新しい空画像）、旧連番保管の札とバンクの絵を取り除く（外れた Cell・画像は
    命令の前の値のまま）。末尾の空いたコマを取り除く場合、消す位置が局所の絵を共有する・
    バンクの絵を局所の絵として持つ場合は今の経路。
    """
    live = document._live()
    if (live["graph"] is not None or live.get("trial") or type(layer_index) is not int
            or type(number) is not int):
        return None
    state = live["state"]
    frame_ids = list(state.frame_ids)
    count = len(frame_ids)
    if not (0 <= layer_index < len(state.track_ids)) or not count or number < 1:
        return None
    tid = state.track_ids[layer_index]
    keys = []
    for fid in frame_ids:
        values = state.position(fid, tid)[0]
        if values["sequence_number"] == number:
            if values.get("local_share") is not None or values["local_bank"]:
                return None
            keys.append((fid, tid))
    data, handles = state.track(tid)
    cell = next((cell for n, cell in data["cells"] if n == number), None)
    if not keys and cell is None:
        return None  # 今の手順は札だけ取り除く（何も変わらなければ空の ChangeSet）
    operation = ["delete_sequence_entry", layer_index, number]
    # 末尾のコマを取り除くかは、写しに当てて確かめる（取り除いたコマの付け替えはまだ無い）。
    # ほかの列が末尾のコマを使っていれば取り除かないので、写しは要らない。
    last = count - 1
    if not any(document.timeline_block_at(last, index) is not None
               for index in range(len(state.track_ids)) if index != layer_index):
        if not _keeps_frame_count(live, operation, count):
            return None
    plan = _detach_plan(document, [], keys)
    if plan is None:
        return None
    if cell is not None:
        # 取り除くバンクの絵の窓口も、命令の前の値の素の Cell・画像にして外す。
        bank = ("bank", tid, number)
        cache = live["cache"]
        images = {}
        for initial in (False, True):
            image = cache._objects.get(("image", (bank, initial)))
            if image is not None:
                try:
                    images[initial] = (image, image._image.copy())
                except ValueError:
                    return None
        window = cache._objects.get(("cell", bank))
        if window is not None or images:
            plan["cells"].append((bank, window, images, {"local": cell}, handles))
    rows = [key[0] for key in keys]
    result = _apply_core_command(document, operation, lambda ids: rows)
    if result is None:
        return None
    changes, _ = result
    state.refresh_track(tid)
    state.forget_track(tid)
    state.pending.clear()
    _detach(document, plan)
    return changes


def _keeps_frame_count(live, operation, count):
    """命令を写しに当てて、コマの数が変わらないか確かめる。"""
    trial = live["core"].copy()
    trial.set_blank_image(_blank_handle())
    try:
        trial.apply_command(json.dumps(operation))
    except ValueError:
        return False
    return len(json.loads(trial.frames_graph([])[0])["frame_order"]) == count


def _core_position_command(document, name, column, layer_index, replace):
    """一つの位置だけを書き換える命令を Rust で当てる。今の手順と同じにできなければ None。

    replace が真なら今の手順は位置に新しい Layer を入れ直す（replace_slot。古い Layer は外れ、
    配置待ちを解かれる）。偽ならその場で書き換える（clear_slot。局所の Cell・画像が替わる）。
    """
    live = document._live()
    if (live["graph"] is not None or live.get("trial") or type(column) is not int
            or type(layer_index) is not int):
        return None
    state = live["state"]
    frame_ids = list(state.frame_ids)
    if not (0 <= column < len(frame_ids) and 0 <= layer_index < len(state.track_ids)):
        return None
    key = (frame_ids[column], state.track_ids[layer_index])
    plan = _detach_plan(document, [key] if replace else [], [] if replace else [key])
    if plan is None:
        return None
    result = _apply_core_command(document, [name, column, layer_index],
                                 lambda ids: ids[column:column + 1])
    if result is None:
        return None
    changes, _ = result
    # 新しい番号の絵がバンクに入るので、列の写しも取り直す。
    state.refresh_track(key[1])
    state.forget_track(key[1])
    if replace:
        live.setdefault("tokens", {})[key] = PendingToken()
    state.pending.clear()
    _detach(document, plan)
    return changes


def _core_new_sheet_cell(document, column, layer_index):
    """new_sheet_cell を Rust の命令で（位置をその場で消して空の絵を作る）。"""
    return _core_position_command(document, "new_sheet_cell", column, layer_index, False)


def _core_new_sheet_number(document, column, layer_index):
    """new_sheet_number を Rust の命令で（位置に新しい番号の新しい Layer を入れ直す）。"""
    live = document._live()
    if (live["graph"] is not None or type(column) is not int or type(layer_index) is not int):
        return None
    state = live["state"]
    if not (0 <= column < len(state.frame_ids) and 0 <= layer_index < len(state.track_ids)):
        return None
    block = document.timeline_block_at(column, layer_index)
    if block is None or block[0] != "content":
        return None  # 今の手順は何もしない
    values = state.position(state.frame_ids[block[1]], state.track_ids[layer_index])[0]
    if values["sequence_number"] is None:
        return None
    return _core_position_command(document, "new_sheet_number", column, layer_index, True)


def _retire_document(document, frozen_core):
    """文書の今の状態を、文書から外れた別の Document（写し frozen_core が正本）へ移す。

    今の手順は文書のコマ・列を新しいオブジェクトに入れ替え、古いオブジェクトは今の値のまま
    文書から外れる。全部を素のグラフへ書き出す代わりに、今ある窓口（位置・コマ・列・絵・
    画像・並び）の持ち主を外れた Document に付け替える。窓口のオブジェクトと値はそのまま
    で、書き込みは外れた側の写しに当たり文書には届かない。外れた窓口を文書の操作に渡すと、
    今と同じく持ち主が素の状態に落ちて素のオブジェクトとして扱われる（_plain_value）。
    """
    live = document._live()
    retired = object.__new__(Document)
    live["core"] = frozen_core
    live["state"].core = frozen_core
    retired.__dict__["_live_state"] = live
    for (kind, key), obj in live["cache"].items():
        if kind == "image":
            # 今と同じく、今の画素を持つ独立した画像に戻す（同じオブジェクトのまま外れた側の
            # 表に残すので、外れた絵からも同じ画像が返る。写し全体は持たない）。戻せない
            # （もう無い絵）ものは写しの同じ絵へ結び直す（新しい文書の絵を読まないように）。
            _unbind(obj)
            if isinstance(obj, BoundImage):
                (place, first, second), initial = key
                _bind(obj, _native._CellImage(frozen_core, place == "bank", first, second,
                                              initial))
            continue
        _setm(obj, "_owner", retired)
        view = obj.__dict__.get("_layers_view") if kind == "frame" else None
        if view is not None:
            view._document = retired
    for name in ("frames_view", "tracks_view"):
        view = live.pop(name, None)
        if view is not None:
            # 文書に入れ直されたら（document.frames = 古い並び）今と同じく使い直す。
            view._owner = retired
            view.__dict__["_retired_from"] = document


def _core_restore_snapshot(document, snapshot):
    """restore_snapshot を、遅延の写しの Rust の文書から戻す。今の手順と同じにできなければ None。

    今の手順（restore）は写しを clone_frame_graph でもう一度写して、文書のコマ・列を新しい
    オブジェクトに入れ替える（古いオブジェクトは今の値のまま文書から外れる）。写しの中身を
    まだ作っていない遅延の写しだけを扱う。
    """
    from .document_changes import ChangeSet, DocumentRestored, ExposureIndexReset
    live = document._live()
    lazy = _LAZY_SNAPSHOT[0] if _LAZY_SNAPSHOT else None
    if (lazy is None or type(snapshot) is not lazy or live["graph"] is not None
            or live.get("trial")):
        return None
    core = snapshot.__dict__.get("_core")
    if core is None:
        return None
    current, active, width, height = tuple.__getitem__(snapshot, slice(1, 5))
    target = live["core"]
    _retire_document(document, target.copy())
    target.set_blank_image(_blank_handle())
    target.restore_from(core)
    state = _CoreState(target)
    document.__dict__["_live_state"] = {
        "graph": None, "core": target, "state": state, "cache": _Cache(),
        "cursor": [current, active], "next_ids": state.next_ids, "last_keys": {},
        "extra_pending": {},
        "tokens": {(fid, tid): PendingToken() for fid in state.frame_ids
                   for tid in state.track_ids},
    }
    document._sync_selection()
    # 今の手順は写しの位置を clone_slot で作るので timeline_revision が進む。
    Layer.timeline_revision += 1
    return ChangeSet((DocumentRestored(width, height), ExposureIndexReset()))


# Rust の命令で当てる操作（名前 → 関数(document, *args)、当てられなければ None）。
_CORE_COMMANDS = {"add_track": _core_add_track,
                  "remove_tracks": _core_remove_tracks,
                  "restore_removed_track": _core_restore_removed_track,
                  "restore_inserted_track": _core_restore_inserted_track,
                  "move_tracks": _core_move_tracks,
                  "duplicate_tracks": _core_duplicate_tracks,
                  "duplicate_frame": _core_duplicate_frame,
                  "delete_frame": _core_delete_frame,
                  "delete_track_frame": _core_delete_track_frame,
                  "insert_track_frame": _core_insert_track_frame,
                  "ensure_frame_count": _core_ensure_frame_count,
                  "advance_frame": _core_advance_frame,
                  "restore_snapshot": _core_restore_snapshot,
                  "create_blank_key": _core_create_blank_key,
                  "clear_sheet_span": _core_clear_sheet_span,
                  "new_sheet_cell": _core_new_sheet_cell,
                  "split_sheet_clip": _core_split_sheet_clip,
                  "insert_sequence_blank": _core_insert_sequence_blank,
                  "delete_sequence_entry": _core_delete_sequence_entry,
                  "new_sheet_number": _core_new_sheet_number}


def _blank_handle():
    """今の blank_model_image() の Rust の画像（札付き）。Rust の命令が作る空画像の雛形。

    今の手順の空画像（blank_image()・blank_model_image()）は雛形の画素と世代を共有するので、
    Rust の命令もこの雛形を使い回す。
    """
    from .pma_image import attributes_tag
    from .utils import blank_model_image
    image = blank_model_image()
    return image._image.with_tag(attributes_tag(image._attributes))


def _note_plain(reason):
    """PMA_CORE_DOCUMENT_TRACE にファイル名があれば、Rust に戻せなかった理由を追記する（調査用）。"""
    import os
    path = os.environ.get("PMA_CORE_DOCUMENT_TRACE")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(f"{os.environ.get('PYTEST_CURRENT_TEST', '')}\t{reason}\n")


def _restore_token(slot, track, token):
    """素に書き出した位置の配置待ちの札を、文書が保っている札に戻す。"""
    old = slot.__dict__.get("_pending_token")
    if token is None or old is token:
        return
    slot.__dict__["_pending_token"] = token
    if old in track.pending_slots:
        track.pending_slots.discard(old)
        track.pending_slots.add(token)


def _transplant(proxy, stand):
    """窓口 proxy を素の型に戻し、素に書き出した stand の値を詰める。"""
    plain_cls = type(stand)
    object.__setattr__(proxy, "__class__", plain_cls)
    if isinstance(stand, (Cell, Track)):
        for name in plain_cls.__slots__:
            descriptor = getattr(plain_cls, name)
            try:
                value = descriptor.__get__(stand, plain_cls)
            except AttributeError:
                try:
                    descriptor.__delete__(proxy)
                except AttributeError:
                    pass
                continue
            descriptor.__set__(proxy, value)
    else:
        data = object.__getattribute__(proxy, "__dict__")
        data.clear()
        data.update(object.__getattribute__(stand, "__dict__"))


def _replace_references(plain, replaced):
    """素のグラフの中の書き出したオブジェクトへの参照を、素に戻した窓口へ付け替える。"""
    def swap(obj):
        return replaced.get(id(obj), obj)

    plain.tracks = [swap(track) for track in plain.tracks]
    for track in plain.tracks:
        track.cells = {number: swap(cell) for number, cell in track.cells.items()}
    plain.frames = [swap(frame) for frame in plain.frames]
    for frame in plain.frames:
        frame.layers = [swap(slot) for slot in frame.layers]
        for slot in frame.layers:
            data = slot.__dict__
            if "_track" in data:
                data["_track"] = swap(data["_track"])
            if data.get("_cell") is not None:
                data["_cell"] = swap(data["_cell"])


_CELL_SLOT = Cell.image
_TRACK_SLOT = Track.name


_LIVE = (LiveLayer, LiveTrack, LiveCell, LiveFrame)


def _plain_guard(cls, real):
    """窓口だったオブジェクトが素の型に戻っている間は、ビューの操作を素の値へ回す。"""
    for name in ("__getitem__", "__setitem__", "__delitem__", "__iter__", "__len__",
                 "__contains__", "__repr__", "copy", "__deepcopy__", "add", "discard"):
        original = cls.__dict__.get(name)
        if original is None:
            continue

        def wrapper(self, *args, _original=original, _name=name, **kwargs):
            target = real(self)
            if target is None:
                return _original(self, *args, **kwargs)
            if _name == "copy":
                return dict(target)
            if _name == "__deepcopy__":
                return deepcopy(target, *args)
            return getattr(target, _name)(*args, **kwargs)
        setattr(cls, name, wrapper)


_plain_guard(_CellsView, lambda view: None if isinstance(view._track, LiveTrack)
             else view._track.cells)
_plain_guard(_HintsView, lambda view: None if isinstance(view._track, LiveTrack)
             else view._track.legacy_archive_hints)
_plain_guard(_PendingView, lambda view: None if isinstance(view._track, LiveTrack)
             else view._track.pending_slots)
_plain_guard(_SlotDictView, lambda view: None if isinstance(view._layer, LiveLayer)
             else object.__getattribute__(view._layer, "__dict__"))


def _in_range(value, count):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < count


def _reuse_images(cell, key, cached):
    """窓口の画像があれば、素の絵にもその画像オブジェクトを入れる（同一性を保つ）。"""
    for initial, name in ((False, "image"), (True, "initial_image")):
        image = cached.get(("image", (key, initial)))
        if image is not None and getattr(cell, name) is not None:
            object.__setattr__(cell, name, image)


# 文書を書き換えない操作。素のグラフへ書き出さず、文書そのもの（窓口）に今の Python の
# 手順を当てる。窓口の読み取りは今の値と同じなので結果も同じで、戻り値は初めから窓口。
_IN_PLACE = frozenset((
    "resolve_numbered_target", "_track_slots", "placed_numbers", "unplaced_numbers",
    "pending_entries", "archive_hints", "legacy_drawing_state", "_valid_position",
    "timeline_block_at", "_span_bounds", "timeline_step_columns", "prepare_sheet_move",
    "prepare_sheet_moves", "prepare_exposure_resize", "sequence_entry_columns",
    "sequence_move_sources", "next_drawing_number", "editable_cursor",
    "tween_member_numbers", "_column_count", "_valid_columns", "_valid_track_slots",
))


# 書き込みが無いか、あっても同じ値の代入・列の表示設定・同じ画素の上書き（何度当てても
# 同じ結果）だけで済むことが多い操作。まず窓口のまま当て、素のグラフを要る書き込みに
# 来たら（_NeedsGraph）、最初から今の経路でやり直す。素のグラフを要る書き込みより前に
# 文書の外へ副作用を出さない操作だけを入れる。
_TRY_IN_PLACE = frozenset((
    "write_drawing_patch", "set_drawing_content", "make_position_editable",
    "set_track_properties", "set_position_track_properties", "restore_drawing",
))


def _try_in_place(document, plain, args, kwargs):
    """窓口のまま当てる。素のグラフが要るなら _NeedsGraph（文書は変えていない）。"""
    live = document._live()
    live["trial"] = True
    live["trial_log"] = log = []
    try:
        return plain(document, *args, **kwargs)
    except _NeedsGraph:
        # 試しに書いた列の表示設定を戻してから、今の経路でやり直させる（やり直しの
        # 操作が「変わった項目」を試す前の値で判断するように）。
        state = live["state"]
        for tid, previous in reversed(log):
            live["core"].set_track_settings_graph(tid, previous)
            state.refresh_track(tid)
            state.forget_track(tid)
        raise
    finally:
        live["trial"] = False
        live["trial_log"] = None


def _install_methods():
    from .document import GraphDocument
    for name, value in vars(GraphDocument).items():
        if isinstance(value, staticmethod) and name not in vars(Document):
            # 操作が self 越しに呼ぶ静的な補助（_changed_track_fields など）。
            setattr(Document, name, value)
            continue
        if not _is_document_method(name, value) or name in vars(Document):
            continue

        def method(self, *args, _name=name, _plain=value, _in_place=name in _IN_PLACE,
                   _try=name in _TRY_IN_PLACE, **kwargs):
            if _in_place or not isinstance(self, Document):
                # テストなどが素の入れ物を self にして呼ぶ場合は、今の Python の手順のまま。
                return _plain(self, *args, **kwargs)
            live = self._live()
            if live.get("trial"):
                # 窓口のまま試している操作の中。書き込みが素のグラフを要れば外側がやり直す。
                return _plain(self, *args, **kwargs)
            if _try and live["graph"] is None:
                try:
                    return _try_in_place(self, _plain, args, kwargs)
                except _NeedsGraph:
                    pass
            return self._run(_name, args, kwargs)
        method.__name__ = name
        method.__doc__ = value.__doc__
        setattr(Document, name, method)


def _is_document_method(name, value):
    return (callable(value) and not name.startswith("__")
            and not isinstance(value, (staticmethod, classmethod, property)))


_install_methods()
