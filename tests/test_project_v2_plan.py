"""v2 Rust 計画と保存した Python デコーダーの差分検証。"""
from copy import deepcopy
from dataclasses import fields, is_dataclass
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import random
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QImage

from paintmaskanimator import project_io as io, project_v2 as v2
from paintmaskanimator.errors import OperationError
from paintmaskanimator.native import decode_v2_plan
from paintmaskanimator.models import Cell

ROOT = Path(__file__).parent / "fixtures/projects/v2"


def value_state(value):
    if isinstance(value, QImage):
        image = value.convertToFormat(QImage.Format.Format_RGBA8888)
        return (image.width(), image.height(), bytes(image.constBits()),
                value.format(), value.dotsPerMeterX(), value.dotsPerMeterY())
    if isinstance(value, Cell):
        return tuple(value_state(getattr(value, k)) for k in Cell.__slots__)
    if is_dataclass(value):
        return {f.name: value_state(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {k: value_state(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(value_state(v) for v in value)
    return value


def graph_state(loaded, cache):
    doc = loaded.document
    tracks = []
    for t in doc.tracks:
        tracks.append({k: len(t.pending_slots) if k == "pending_slots"
                       else value_state(getattr(t, k)) for k in t.__slots__})
    frames = []
    for frame in doc.frames:
        slots = []
        for slot in frame.layers:
            state = value_state(slot)
            state["__dict__"] = {
                k: value_state(v) for k, v in slot.__dict__.items()
                if k not in ("_track", "_timeline_metadata", "_pending_token")}
            state["_timeline_metadata"] = tuple(
                getattr(slot.__dict__["_timeline_metadata"], k) for k in
                ("has_content", "is_blank_key", "blank_start", "sequence_number", "sequence_only"))
            # Compare cache membership and exact PNG bytes against every live image.
            state["cached"] = tuple(None if image is None else cache._entries.get(int(image.cacheKey()))
                                    for image in (slot.image, slot.initial_image))
            slots.append(state)
        frames.append((frame.duration, slots))
    return (loaded.width, loaded.height, loaded.metadata, doc.current_frame,
            doc.active_layer_index, tracks, frames, sorted(cache._entries.values()))


def outcome(decoder, payload, archive):
    cache = io.PngCache()
    cache._entries[-1] = b"unchanged on failure"
    try:
        result = decoder(payload, archive, cache)
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        # The unchanged public decode_payload boundary performs this normalization.
        return ("error", OperationError, "プロジェクトの構造が不正です。", cache._entries)
    except OperationError as error:
        return ("error", type(error), str(error), cache._entries)
    return ("ok", graph_state(result, cache))


def compare(payload, archive):
    before = deepcopy(payload)
    expected = outcome(v2._decode_payload_python, payload, archive)
    actual = outcome(v2._decode_payload, payload, archive)
    assert actual == expected, repr(payload)
    assert payload == before
    return actual


@pytest.mark.parametrize("path", sorted(ROOT.glob("*.pman")), ids=lambda p: p.stem)
def test_fixture_and_new_save_match(path, tmp_path):
    with ZipFile(path) as archive:
        payload = json.loads(archive.read("project.json"))
        assert compare(payload, archive)[0] == "ok"
        rng = random.Random(9634)
        locations = list(paths(payload))
        for _ in range(50):
            damaged = deepcopy(payload)
            location = rng.choice(locations)
            parent = damaged
            for key in location[:-1]:
                parent = parent[key]
            parent[location[-1]] = deepcopy(rng.choice([None, True, 10**30, [], {}, "absent.png"]))
            compare(damaged, archive)
    loaded = io.load_project(path)
    snapshot = io.ProjectSnapshot(deepcopy(loaded.metadata), loaded.document,
                                  loaded.width, loaded.height)
    target = tmp_path / "resaved.pman"
    v2.write_snapshot(target, snapshot)
    with ZipFile(target) as archive:
        assert compare(json.loads(archive.read("project.json")), archive)[0] == "ok"


def tiny():
    return {"mask_format": 2, "canvas": {"width": 1, "height": 1},
            "frame_durations": [1, 2, 3, 4], "tracks": [{
                "cells": [{"number": 1, "image": "one.png", "initial": "one.png", "cell_name": "a"}],
                "sheet": [1, None, "BLANK", None],
                "positions": [{"column": 2, "blank_start": True}, {"column": 0, "tween_member": "a"}],
                "pending": [{"column": 1, "number": 1, "kind": "CELL", "length": 2}],
                "drawing_states": [{"number": 1, "length": 3}],
                "compatibility": {
                    "local_states": [{"column": 2, "retained_number": 2, "image": "one.png"}, {"column": 3}],
                    "resolved_overrides": [{"column": 3, "value": 1, "source_column": 0}],
                    "length_overrides": [{"column": 0, "length": 2, "input_length": 9,
                        "guard": {"kind": "CELL", "number": 1, "pending": False,
                                  "blank_start": False, "imported": True}}]}}]}


@pytest.fixture
def archive():
    image = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    data = BytesIO()
    with ZipFile(data, "w") as z:
        for path in ("one.png", "a//b.PNG/", "./a.png", "a.png/", ".png", "a.png/."):
            z.writestr(path, io.encode_png(image))
    with ZipFile(data) as z:
        yield z


def paths(value, prefix=()):
    if isinstance(value, dict):
        for k, child in value.items():
            yield prefix + (k,)
            yield from paths(child, prefix + (k,))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield prefix + (i,)
            yield from paths(child, prefix + (i,))


def test_seeded_corruptions_match(archive):
    assert compare(tiny(), archive)[0] == "ok"
    rng = random.Random(9634)
    locations = list(paths(tiny()))
    replacements = [None, True, False, -1, 0, 10001, 10**30, 1.5,
                    "BLANK", "absent.png", "", [], {}, (), [1], float("inf")]
    for _ in range(500):
        payload = tiny()
        for _ in range(rng.randint(1, 3)):
            path = rng.choice(locations)
            parent = payload
            try:
                for key in path[:-1]:
                    parent = parent[key]
                if rng.randrange(5) == 0:
                    del parent[path[-1]]
                else:
                    parent[path[-1]] = deepcopy(rng.choice(replacements))
            except (KeyError, IndexError, TypeError):
                pass
        compare(payload, archive)


@pytest.mark.parametrize("value", [True, False, -0, 10**30, -(10**30), 10**400, 1.0, -0.0])
def test_integer_and_opacity_rules(value, archive):
    for key in ("opacity", "current_frame", "width", "number"):
        p = tiny()
        target = (p["tracks"][0] if key == "opacity" else p if key == "current_frame"
                  else p["canvas"] if key == "width" else p["tracks"][0]["cells"][0])
        target[key] = value
        compare(p, archive)
    if type(value) is int and value == 0:
        plan, _, error = decode_v2_plan(tiny() | {"current_frame": value}, archive.namelist())
        assert error is None and type(plan["current_frame"]) is int and plan["current_frame"] == 0


def test_image_error_precedence(archive):
    p = tiny()
    p["tracks"][0]["cells"][0]["image"] = "absent.png"
    p["tracks"][0]["sheet"] = []
    assert compare(p, archive)[2] == "プロジェクト内の画像がありません。"
    p = tiny()
    p["tracks"][0]["cells"][0].update(number=False, image="absent.png")
    assert compare(p, archive)[2] == "プロジェクトの構造が不正です。"
    p = tiny()
    p["tracks"][0]["compatibility"]["local_states"][0]["initial"] = "absent.png"
    p["tracks"][0]["compatibility"]["length_overrides"][0]["guard"]["pending"] = True
    assert compare(p, archive)[2] == "プロジェクトの構造が不正です。"


@pytest.mark.parametrize("path", ["a//b.PNG/", "./a.png", "a.png/", "a.png/.", ".png", "../a.png",
                                  "/a.png", "a/../b.png", "a\\b.png", "a:b.png", "", "one.png"])
def test_posix_path_rules(path, archive):
    p = tiny()
    p["tracks"][0]["cells"][0]["image"] = path
    compare(p, archive)
    _, requests, error = decode_v2_plan(p, archive.namelist())
    normal = PurePosixPath(path)
    valid = bool(path) and not normal.is_absolute() and ".." not in normal.parts and "\\" not in path and ":" not in path and normal.suffix.lower() == ".png"
    assert bool(requests) == valid
    if not valid:
        assert error is not None


@pytest.mark.parametrize("name", ["/bad", "../bad", "a/../bad", "a\\bad", "a:bad", "a//b", "./a", "a/"])
def test_archive_names(name, archive):
    class Names:
        def infolist(self):
            from zipfile import ZipInfo
            return archive.infolist() + [ZipInfo(name)]
        def getinfo(self, path):
            return archive.getinfo(path)
        def read(self, path):
            return archive.read(path)
    compare(tiny(), Names())


def test_tween_is_opaque_and_rgb_tuple(archive):
    p = tiny()
    p["tracks"][0]["color_filter_rgb"] = (1, 2, 3)
    deep = "ignored"
    for _ in range(100):
        deep = {"length": deep}
    p["tracks"][0]["positions"][0]["tween"] = deep
    assert compare(p, archive)[0] == "ok"


def test_explicit_compatibility_errors(archive):
    for field, values in [
        ("positions", [{"column": 0}, {"column": 0}]),
        ("positions", [{"column": 0, "blank_start": True}]),
        ("pending", [{"column": 0, "number": 2, "kind": "CELL"}]),
    ]:
        p = tiny()
        p["tracks"][0][field] = values
        assert compare(p, archive)[0] == "error"
    for override in [{"column": 3, "value": 1}, {"column": 3, "source_column": 0},
                     {"column": 3, "value": 1, "source_column": 2}]:
        p = tiny()
        p["tracks"][0]["compatibility"]["resolved_overrides"] = [override]
        assert compare(p, archive)[0] == "error"


@pytest.mark.parametrize("mask,paper,draft", [(1,False,False),(1,True,False),(1,False,True),(2,False,False)])
def test_legacy_mask_and_cache(mask, paper, draft, archive):
    p = tiny()
    p["mask_format"] = mask
    p["tracks"][0].update(is_paper=paper, is_draft=draft)
    assert compare(p, archive)[0] == "ok"


def test_python_strings_and_non_string_keys(archive):
    for text in ("\ud800", "\udfff", "\U000f0000", "\U000f0000\ue000"):
        p = tiny()
        p[text] = {"metadata": [text]}
        p["tracks"][0]["name"] = text
        p["tracks"][0]["cells"][0]["cell_name"] = text
        assert compare(p, archive)[0] == "ok"
        p["tracks"][0]["cells"][0]["image"] = text + ".png"
        assert compare(p, archive)[2] == "プロジェクト内の画像がありません。"
    p = tiny()
    p["tracks"][0]["cells"][0][7] = "not a string key"
    # JSON では出現しないキー。依頼の入口規則に従い、旧実装が無視したキーも拒否する。
    _, requests, error = decode_v2_plan(p, archive.namelist())
    assert not requests and error is not None


@pytest.mark.parametrize("frames,layers,cells", [(2,256,100000),(10000,0,100000),(10000,256,3)])
def test_dynamic_limits(frames, layers, cells, monkeypatch, archive):
    monkeypatch.setattr(io,"MAX_PROJECT_FRAMES",frames)
    monkeypatch.setattr(io,"MAX_PROJECT_LAYERS",layers)
    monkeypatch.setattr(io,"MAX_PROJECT_LAYER_CELLS",cells)
    assert compare(tiny(), archive)[0] == "error"
