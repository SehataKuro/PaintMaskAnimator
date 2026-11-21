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

from .document_core import GraphError, graph_payload, model_image
from .models import ArchiveHint, Cell, Frame, Layer, Track, TRACK_FIELDS
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

    def _owner(self):
        return self._document

    def _items(self):
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
    number = slot.sequence_number
    track = slot.__dict__.get("_track")
    bank = (track is not None and track.connected and number is not None
            and (slot.has_content or slot.sequence_only))
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
        from .document_core import plain_slot, plain_track
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
        reuse = (isinstance(value, _FramesView) and value._owner is self
                 and value._name == name)
        items = self._plain_value(list(value) if reuse else value)
        self._retire_view(view_name)
        graph = self._demote()
        setattr(graph.plain, name, items)
        if reuse:
            value._frozen = None
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
        from .document_core import plain_slot, plain_track
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
        from .document_core import _plain_settings, _settings
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
