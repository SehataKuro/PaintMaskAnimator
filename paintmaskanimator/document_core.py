"""Python の文書グラフと pma-core の Document の相互変換（段階8-4）。

``graph_payload`` は Document の frames・tracks と位置の互換属性を、Rust の
``Document::from_graph`` が受ける JSON と画像の列にする。``graph_from_payload`` は
``Document::to_graph`` の出力から同じ形の Python グラフを組み立てる。
``graph_signature`` は比較用に、Python の文書が持つ観測できる状態を値で返す。
"""
from __future__ import annotations

import json
from weakref import WeakSet

from . import constants
from .legacy_v1 import DenseReference, LegacyLayerV1, V1Position
from .models import ArchiveHint, Cell, Frame, Layer, PendingToken, Track
from .native import _native
from .pma_image import PmaImage, attributes_tag, tag_attributes
from .timeline_metadata import metadata_for, set_dense_import

_KNOWN_SLOT_KEYS = frozenset((
    "_cell", "_track", "_timeline_metadata", "_pending_token", "_pending_detached",
    "_v1_position", "_dense_import", "_content_origin", "_v2_length_imported",
    "_v2_resolve_source", "_legacy_missing", "tween", "tween_member",
))
_SETTINGS = ("name", "visible", "opacity", "alpha_locked", "is_paper", "is_draft",
             "color_filter_enabled", "color_filter_rgb")


class GraphError(ValueError):
    """Rust の文書で表せない Python の状態。"""


# Cell の画像が None のとき Rust に渡す 0×0 の画像。世代で見分けて None に戻す。
_NO_IMAGE = PmaImage.blank(0, 0)


class _Images:
    """画像に添字を振り、Qt 属性の札を付けた Rust の画像を並べる。

    Rust の絵の画像は独立した値なので、同じ PmaImage を二か所の絵が持つ状態
    （片方への書き込みが他方に出る）は表せない。二度目に出たら GraphError。
    Cell も同じく、二か所が同じ Cell を持つ状態は表せない。

    v1 互換の記録（_v1_position.legacy）の画像は例外で、v2 の読み込みは絵と同じ
    PmaImage を入れる。この画像の画素を読むのはキャンバスの大きさ変更だけで、結果も
    記録に戻るだけなので（保存・表示には出ない）、共有を許し、Rust では独立した値にする。
    """

    def __init__(self):
        self.images = []
        self.seen = set()
        self.cells = set()
        self.blank = None
        # 複数の位置が同じ局所 Cell を持つとき、id(Cell) → (組の番号, 書き出した値)。
        self.shares = {}

    def add(self, image, *, required=False, shared=False):
        if image is None:
            if not required:
                return None
            if self.blank is None:
                self.blank = self._push(_NO_IMAGE)
            return self.blank
        if not isinstance(image, PmaImage):
            raise GraphError(f"PmaImage ではない画像: {type(image).__name__}")
        if not shared:
            if id(image) in self.seen:
                raise GraphError("同じ画像オブジェクトを複数の絵が持っている")
            self.seen.add(id(image))
        return self._push(image)

    def _push(self, image):
        self.images.append(image._image.copy().with_tag(attributes_tag(image._attributes)))
        return len(self.images) - 1

    def cell(self, cell):
        if not isinstance(cell, Cell):
            raise GraphError(f"Cell ではない絵: {type(cell).__name__}")
        if id(cell) in self.cells:
            raise GraphError("同じ Cell を複数の場所が持っている")
        self.cells.add(id(cell))


def _number(value):
    """セル番号を int にする。整数でない番号は GraphError。"""
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            if isinstance(value, bool) or int(value) != value:
                raise GraphError(f"整数でないセル番号: {value!r}")
        except (TypeError, ValueError, OverflowError) as exc:
            raise GraphError(f"整数でないセル番号: {value!r}") from exc
    return int(value)


def _cell(cell, images):
    images.cell(cell)
    return {"image": images.add(cell.image, required=True), "initial_image": images.add(cell.initial_image),
            "cell_name": cell.cell_name, "is_genga": bool(cell.is_genga)}


def _settings(source):
    values = {name: getattr(source, name) for name in _SETTINGS}
    rgb = values["color_filter_rgb"]
    if rgb is not None:
        rgb = list(rgb)
        if len(rgb) != 3 or not all(isinstance(c, int) and not isinstance(c, bool) for c in rgb):
            raise GraphError(f"色フィルタの色が整数3つでない: {rgb!r}")
    values["color_filter_rgb"] = rgb
    return values


def _hint(hint):
    return {"has_content": hint.has_content, "is_blank_key": hint.is_blank_key,
            "exposure": hint.exposure, "sequence_only": hint.sequence_only,
            "tween": hint.tween, "tween_member": hint.tween_member}


def _reference(reference):
    return [reference.kind, reference.number]


def _legacy(position, images):
    legacy = position.legacy
    slot = None
    if legacy is not None:
        artwork = None
        if legacy.image is not None:
            artwork = {"image": images.add(legacy.image, shared=True),
                       "initial_image": images.add(legacy.initial_image, shared=True),
                       "cell_name": legacy.cell_name}
        elif legacy.initial_image is not None or legacy.cell_name is not None:
            raise GraphError("画像のない旧入力に初期画像・セル名がある")
        slot = {"settings": _settings(legacy),
                "state": {"has_content": legacy.has_content,
                          "is_blank_key": legacy.is_blank_key,
                          "exposure": legacy.exposure,
                          "sequence_only": legacy.sequence_only,
                          "tween": legacy.tween, "tween_member": legacy.tween_member},
                "sequence_number": legacy.sequence_number, "artwork": artwork}
    return {"legacy": slot, "key_start": position.key_start,
            "blank_start": position.blank_start, "pending": position.pending,
            "input_length": position.input_length}


def _local(cell, images, groups):
    """局所の絵の値と共有の組の番号。groups は id(Cell) → 組の番号（共有されるものだけ）。"""
    group = groups.get(id(cell))
    if group is None:
        return _cell(cell, images), None
    known = images.shares.get(id(cell))
    if known is None:
        known = images.shares[id(cell)] = (group, _cell(cell, images))
    return known[1], group


def _local_groups(slots):
    """複数の位置が持つ局所 Cell（_cell）に組の番号を振る。

    バンクを参照する位置の使われない _cell も含む。参照先のバンクの Cell そのもの
    （local_bank）は除く。
    """
    counts = {}
    for slot in slots:
        cell = slot.__dict__.get("_cell")
        if cell is None or _is_bank_cell(slot, cell):
            continue
        counts[id(cell)] = counts.get(id(cell), 0) + 1
    shared = [key for key, count in counts.items() if count > 1]
    return {key: number for number, key in enumerate(shared)}


def slot_metadata(slot):
    """位置の (has_content, is_blank_key, sequence_number, sequence_only, blank_start)。

    素の Layer では metadata_for を一度だけ呼んで読む（Layer のメタデータの読み取りは
    項目ごとに metadata_for を呼ぶが、値は同じ）。
    """
    if type(slot) is Layer:
        data = metadata_for(slot)
        return (data.has_content, data.is_blank_key, data.sequence_number, data.sequence_only,
                data.blank_start)
    return (slot.has_content, slot.is_blank_key, slot.sequence_number, slot.sequence_only,
            slot.blank_start)


def _is_bank_cell(slot, cell):
    track = slot.__dict__.get("_track")
    return (not _owns_local(slot) and track is not None
            and track.cells.get(slot.sequence_number) is cell)


def _owns_local(slot):
    """位置が局所の絵を使う（バンクを参照しない）か。Layer._cell と同じ条件。"""
    has_content, _, number, sequence_only, _ = slot_metadata(slot)
    track = slot.__dict__.get("_track")
    bank = (track is not None and track.connected and number is not None
            and (has_content or sequence_only))
    return not bank


def position_value(slot, track, images):
    """素の Layer を from_graph の位置の値にする。images は画像に添字を振る表。"""
    return _position(slot, track, images, {})


def track_value(track, images):
    """接続済みの素の Track を from_graph の列の値にする。"""
    if not track.connected:
        raise GraphError("未接続の列")
    return {
        "settings": _settings(track),
        "cells": [[_number(n), _cell(cell, images)] for n, cell in track.cells.items()],
        "hints": [[_number(n), _hint(hint)] for n, hint in track.legacy_archive_hints.items()],
        "import_sources": _import_sources(track.import_sources),
    }


def _import_sources(sources):
    """連番として読み込んだ元のファイル（#89）。None か文字列の一覧。"""
    if sources is None:
        return None
    sources = list(sources)
    if not all(isinstance(path, str) for path in sources):
        raise GraphError(f"読み込み元のファイルが文字列でない: {sources!r}")
    return sources


def _position(slot, track, images, groups):
    data = slot.__dict__
    unknown = set(data) - _KNOWN_SLOT_KEYS
    if unknown:
        raise GraphError(f"位置の未知の属性: {sorted(unknown)}")
    if data.get("_track") is not track:
        raise GraphError("列の Track と異なる Track を持つ位置")
    has_content, is_blank_key, number, sequence_only, blank_start = slot_metadata(slot)
    bank = number is not None and (has_content or sequence_only)
    local = data.get("_cell")
    imported = data.get("_dense_import")
    source = data.get("_v2_resolve_source")
    v1 = data.get("_v1_position")
    if number is not None:
        number = _number(number)
    if bank and number not in track.cells:
        raise GraphError("バンクにない番号を参照する位置")
    local_bank = bank and local is not None and track.cells.get(number) is local
    if local_bank:
        # 使われない _cell が参照先のバンクの Cell そのもの。印だけ運ぶ。
        local, share = None, None
    elif local is not None:
        # バンクを参照する位置の _cell は使われないが、値と共有の組は運ぶ。
        local, share = _local(local, images, groups)
    else:
        share = None
    return {
        "has_content": bool(has_content), "is_blank_key": bool(is_blank_key),
        "sequence_number": number, "sequence_only": bool(sequence_only),
        "blank_start": bool(blank_start), "tween": slot.tween,
        "tween_member": slot.tween_member,
        "local": local, "local_share": share, "local_bank": local_bank,
        "content_origin": data.get("_content_origin"),
        "legacy_missing": bool(data.get("_legacy_missing", False)),
        "pending_detached": bool(data.get("_pending_detached", False)),
        "v1_position": None if v1 is None else _legacy(v1, images),
        "dense_import": None if imported is None else [
            list(imported[0]), _reference(imported[1]), _reference(imported[2])],
        "length_imported": data.get("_v2_length_imported"),
        "resolve_source": None if source is None else [list(source[0]), *source[1:]],
    }


class _PlainView:
    """Rust を正本にする文書（document_live）を、一時的に素のグラフとして見る。"""

    def __init__(self, document):
        self.document = document
        self.live = hasattr(type(document), "_demote")

    def __enter__(self):
        return self.document._demote().plain if self.live else self.document

    def __exit__(self, *exc):
        if self.live:
            self.document._promote()


def graph_payload(document, width=None, height=None, ids=None, selection=None):
    """文書グラフを (JSON 文字列, Rust の画像の列) にする。

    表せない状態（未接続の列、列と異なる Track、絵と空キーが同時の位置など）は
    GraphError にする。``ids`` は (コマ ID の列, 列 ID の列, 次のコマ ID, 次の列 ID)。
    ``selection`` を渡すと、文書の選択の代わりに (コマ, 列) を Rust に渡す。
    Rust を正本にする文書を渡したときは、その素のグラフを写す。
    """
    with _PlainView(document) as plain:
        return _graph_payload(plain, width, height, ids, selection)


def _graph_payload(document, width, height, ids, selection):
    images = _Images()
    try:
        # 同じ Track・Frame・Layer のオブジェクトが二か所にある状態は、Rust では別々の
        # 列・コマ・位置になってしまうので表せない。
        if len({id(track) for track in document.tracks}) != len(document.tracks):
            raise GraphError("同じ Track を複数の列が持っている")
        if len({id(frame) for frame in document.frames}) != len(document.frames):
            raise GraphError("同じ Frame を複数のコマが持っている")
        slots = [slot for frame in document.frames for slot in frame.layers]
        if len({id(slot) for slot in slots}) != len(slots):
            raise GraphError("同じ Layer を複数の位置が持っている")
        tracks = [track_value(track, images) for track in document.tracks]
        groups = _local_groups(slots)
        frames = []
        for frame in document.frames:
            if len(frame.layers) != len(document.tracks):
                raise GraphError("列の数と位置の数が異なるコマ")
            frames.append({"duration": frame.duration, "positions": [
                _position(slot, track, images, groups)
                for slot, track in zip(frame.layers, document.tracks)]})
    except GraphError:
        raise
    except (TypeError, AttributeError, KeyError, IndexError, ValueError) as exc:
        # ValueError は窓口の画像（_CellImage）が絵を引けない・借用中のときなど。
        # 形の壊れた値（Cell でない局所の絵、形の違う隠し属性など）も表せない状態。
        raise GraphError(f"表せない値: {type(exc).__name__}: {exc}") from exc
    if ids is not None:
        frame_ids, track_ids, next_frame, next_track = ids
        if len(frame_ids) != len(frames) or len(track_ids) != len(tracks):
            raise GraphError("ID の数がコマ・列の数と一致しない")
        for entry, value in zip(frames, frame_ids):
            entry["id"] = value
        for entry, value in zip(tracks, track_ids):
            entry["id"] = value
    payload = {
        "width": constants.CANVAS_WIDTH if width is None else width,
        "height": constants.CANVAS_HEIGHT if height is None else height,
        "current_frame": document.current_frame if selection is None else selection[0],
        "active_layer": document.active_layer_index if selection is None else selection[1],
        "tracks": tracks, "frames": frames,
    }
    if ids is not None:
        payload["next_frame_id"], payload["next_track_id"] = next_frame, next_track
    try:
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise GraphError(f"JSON にできない値: {exc}") from exc
    return text, images.images


def core_document(document, width=None, height=None, ids=None):
    """Python の文書から Rust の文書を作る。"""
    text, images = graph_payload(document, width, height, ids)
    try:
        return _native._CoreDocument.from_graph(text, images)
    except ValueError as exc:
        raise GraphError(str(exc)) from exc


def model_image(handle):
    """Rust の画像（札付き）から PmaImage を作る。0×0 の番兵は None。"""
    if handle.generation == _NO_IMAGE.generation:
        return None
    image = PmaImage.__new__(PmaImage)
    # 同じ添字の画像（to_graph は同じ画素の画像を一つにまとめる）を持つ絵どうしで、Python の
    # 画像オブジェクトを共有しないよう写しを持たせる（画素は書き込みまで共有）。共有すると、
    # 一方への書き込みが他方の絵にも届いてしまう。
    image._image = handle.copy()
    image._attributes = tag_attributes(handle.tag)
    return image


def _decoder(handles):
    # 添字は画素の受け渡しを減らすためのもの。絵ごとに独立した PmaImage を作る
    # （画素は書き込みまで共有）。
    return lambda index: None if index is None else model_image(handles[index])


def _plain_cell(values, image):
    # Cell() は None の画像を空画像にするので、値をそのまま入れる。
    result = Cell.__new__(Cell)
    result.image = image(values["image"])
    result.initial_image = image(values["initial_image"])
    result.cell_name = values["cell_name"]
    result.is_genga = values["is_genga"]
    return result


def _plain_settings(values):
    rgb = values["color_filter_rgb"]
    return {**values, "color_filter_rgb": None if rgb is None else tuple(rgb)}


def plain_track(data, handles=None, image=None):
    """to_graph の列の値から、接続済みの素の Track を作る。"""
    image = image or _decoder(handles)
    # Track() を呼ばずに作る（Cell と同じく、書き出しは今の文書の割り当てに数えない）。
    track = Track.__new__(Track)
    for name, value in _plain_settings(data["settings"]).items():
        setattr(track, name, value)
    track.pending_slots = WeakSet()
    track.cells = {n: _plain_cell(values, image) for n, values in data["cells"]}
    track.legacy_archive_hints = {
        n: ArchiveHint(h["has_content"], h["is_blank_key"], h["exposure"],
                       h["sequence_only"], h["tween"], h["tween_member"])
        for n, h in data["hints"]}
    track.import_sources = data["import_sources"]
    track.connected = True
    return track


def plain_slot(values, track, handles=None, image=None, shares=None):
    """to_graph の位置の値から、track に接続した素の Layer を作る。

    shares は局所 Cell の共有の組の番号 → Cell。同じ表を渡した位置どうしで、同じ組は
    同じ Cell になる（先に作った位置の値を使う）。
    """
    image = image or _decoder(handles)
    slot = object.__new__(Layer)
    meta = _native.TimelineCell()
    for name in ("has_content", "is_blank_key", "sequence_number", "sequence_only",
                 "blank_start"):
        setattr(meta, name, values[name])
    token = PendingToken()
    state = slot.__dict__
    state.update({"_track": track, "_timeline_metadata": meta, "_pending_token": token,
                  "tween": values["tween"], "tween_member": values["tween_member"]})
    if values.get("pending_detached"):
        # 配置待ちの所属は列でなく位置のメタデータが持つ。
        state["_pending_detached"] = True
    elif values["sequence_only"]:
        track.pending_slots.add(token)
    if values.get("local_bank"):
        state["_cell"] = track.cells[int(values["sequence_number"])]
    elif values["local"] is not None:
        group = values.get("local_share")
        cell = None if group is None or shares is None else shares.get(group)
        if cell is None:
            cell = _plain_cell(values["local"], image)
            if group is not None and shares is not None:
                shares[group] = cell
        state["_cell"] = cell
    if values["content_origin"] is not None:
        state["_content_origin"] = values["content_origin"]
    if values.get("legacy_missing"):
        state["_legacy_missing"] = True
    v1 = values["v1_position"]
    if v1 is not None:
        legacy = v1["legacy"]
        if legacy is not None:
            artwork = legacy["artwork"] or {"image": None, "initial_image": None,
                                            "cell_name": None}
            legacy = LegacyLayerV1(
                **_plain_settings(legacy["settings"]), **legacy["state"],
                sequence_number=legacy["sequence_number"],
                image=image(artwork["image"]),
                initial_image=image(artwork["initial_image"]),
                cell_name=artwork["cell_name"])
        state["_v1_position"] = V1Position(
            legacy, v1["key_start"], v1["blank_start"], v1["pending"], v1["input_length"])
    if values["dense_import"] is not None:
        signature, sheet, resolved = values["dense_import"]
        set_dense_import(slot, (tuple(signature), DenseReference(*sheet),
                                DenseReference(*resolved)))
    if values["length_imported"] is not None:
        state["_v2_length_imported"] = values["length_imported"]
    if values["resolve_source"] is not None:
        signature, *rest = values["resolve_source"]
        state["_v2_resolve_source"] = (tuple(signature), *rest)
    return slot


def graph_from_payload(text, handles):
    """Rust の to_graph の出力から (frames, tracks, current_frame, active_layer, payload) を作る。

    payload は読んだ JSON（コマ・列の ID を含む）。
    """
    payload = json.loads(text)
    image = _decoder(handles)
    tracks = [plain_track(data, image=image) for data in payload["tracks"]]
    shares = {}
    frames = [Frame([plain_slot(values, track, image=image, shares=shares)
                     for track, values in zip(tracks, data["positions"])], data["duration"])
              for data in payload["frames"]]
    return frames, tracks, payload["current_frame"], payload["active_layer"], payload


def graph_from_core(core):
    """Rust の文書から Python の文書グラフ (frames, tracks, current, active, payload) を作る。"""
    text, handles = core.to_graph()
    return graph_from_payload(text, handles)


# ----------------------------------------------------------------------
# 比較用の署名
# ----------------------------------------------------------------------
def _image_signature(image):
    if image is None:
        return None
    if not isinstance(image, PmaImage):
        return ("qt", type(image).__name__)
    return (image.width, image.height, image.generation, image._attributes)


def _cell_signature(cell):
    return (_image_signature(cell.image), _image_signature(cell.initial_image), cell.cell_name,
            cell.is_genga)


def _legacy_signature(legacy):
    if legacy is None:
        return None
    return tuple((name, _image_signature(getattr(legacy, name))
                  if name in ("image", "initial_image") else getattr(legacy, name))
                 for name in LegacyLayerV1.__dataclass_fields__)


def _slot_signature(slot, track):
    data = slot.__dict__
    v1 = data.get("_v1_position")
    local = data.get("_cell")
    return (
        bool(slot.has_content), bool(slot.is_blank_key), slot.sequence_number,
        bool(slot.sequence_only), bool(slot.blank_start), slot.tween, slot.tween_member,
        data.get("_track") is track,
        None if local is None else _cell_signature(local),
        data.get("_content_origin"),
        None if v1 is None else (_legacy_signature(v1.legacy), v1.key_start, v1.blank_start,
                                 v1.pending, v1.input_length),
        data.get("_dense_import"), data.get("_v2_length_imported"),
        data.get("_v2_resolve_source"), bool(data.get("_legacy_missing", False)),
        bool(data.get("_pending_detached", False)),
        tuple(sorted(set(data) - _KNOWN_SLOT_KEYS)),
    )


def _identity_signature(document):
    """画像・Cell のオブジェクトの同一性の分け方。出てきた順に、初出の番号で表す。"""
    seen = {}
    order = []

    def visit(value):
        if value is not None:
            order.append(seen.setdefault(id(value), len(seen)))

    def visit_cell(cell):
        visit(cell)
        visit(cell.image)
        visit(cell.initial_image)

    for track in document.tracks:
        visit(track)
        for _, cell in sorted(track.cells.items(), key=lambda item: repr(item[0])):
            visit_cell(cell)
    for frame in document.frames:
        visit(frame)
        for slot in frame.layers:
            visit(slot)
            data = slot.__dict__
            if data.get("_cell") is not None:
                visit_cell(data["_cell"])
    # v1 互換の記録の画像は共有を許す（_Images の説明）ので比べない。
    return tuple(order)


def graph_signature(document):
    """文書の観測できる状態を、画像は寸法・世代・Qt 属性で表した値にする。

    画像・Cell のオブジェクトの同一性の分け方も含む。
    """
    tracks = tuple(
        (tuple(getattr(track, name) for name in _SETTINGS), track.import_sources, track.connected,
         tuple(sorted((n, _cell_signature(c)) for n, c in track.cells.items())),
         tuple(sorted(track.legacy_archive_hints.items())))
        for track in document.tracks)
    frames = tuple(
        (frame.duration, tuple(_slot_signature(slot, track)
                               for slot, track in zip(frame.layers, document.tracks)),
         len(frame.layers))
        for frame in document.frames)
    # バンクの番号の並び（dict の挿入順）は比べない。Rust は番号順に持ち、本番で並びに
    # 依存する処理は無い（保存は番号順に並べ直し、他は最大値や番号での引き当てだけ）。
    return (document.current_frame, document.active_layer_index, tracks, frames,
            _identity_signature(document))


def roundtrip_mismatch(document):
    """Rust へ写して戻した文書の署名が一致しなければ、その説明を返す。"""
    from .document import GraphDocument
    with _PlainView(document) as plain:
        try:
            core = core_document(plain)
        except GraphError as exc:
            return f"unrepresentable: {exc}"
        frames, tracks, current, active, _ = graph_from_core(core)
        copied = object.__new__(GraphDocument)
        copied.frames, copied.tracks = frames, tracks
        copied.current_frame, copied.active_layer_index = current, active
        before, after = graph_signature(plain), graph_signature(copied)
    if before == after:
        return None
    return _first_difference(before, after)


def _first_difference(before, after, path="doc"):
    if isinstance(before, tuple) and isinstance(after, tuple) and len(before) == len(after):
        for index, (a, b) in enumerate(zip(before, after)):
            if a != b:
                return _first_difference(a, b, f"{path}[{index}]")
    return f"{path}: {before!r} != {after!r}"[:400]


__all__ = ["GraphError", "core_document", "graph_from_core", "graph_from_payload",
           "graph_payload", "graph_signature", "model_image", "plain_slot", "plain_track",
           "position_value", "roundtrip_mismatch", "track_value"]
