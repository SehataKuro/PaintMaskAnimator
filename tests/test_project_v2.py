"""Explicit v2 payload contracts; existing v1 expectations remain untouched."""
from copy import deepcopy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from paintmaskanimator import project_io
from paintmaskanimator.project_v2 import compatibility_scalars, placement_keys
from p5_6_support import CASES, ROOT, pixels
from test_project_boundaries import graph_state


@pytest.mark.parametrize("case", CASES)
def test_v2_builder_preserves_snapshot_and_all_numbered_artwork(case):
    loaded = project_io.load_project(ROOT / "v1" / f"{case}.pman")
    snapshot = project_io.ProjectSnapshot(deepcopy(loaded.metadata), loaded.document.clone_graph(),
                                           loaded.width, loaded.height)
    before = json.dumps(graph_state(snapshot.document), sort_keys=True)
    metadata = deepcopy(snapshot.metadata)
    payload, pngs = project_io.build_v2_payload(snapshot)
    assert snapshot.metadata == metadata
    assert json.dumps(graph_state(snapshot.document), sort_keys=True) == before
    assert payload["format_version"] == 2
    assert payload["mask_format"] == 2
    assert "frames" not in payload and "sequence_archive" not in payload
    assert "_loaded_sequence_archive" not in payload
    assert payload["frame_durations"] == [f.duration for f in snapshot.document.frames]
    paths = {path: image for path, image, _message in pngs}
    assert len(paths) == len(pngs)
    for li, (track, data) in enumerate(zip(snapshot.document.tracks, payload["tracks"])):
        assert "alpha_locked" not in data
        assert [cell["number"] for cell in data["cells"]] == sorted(track.cells)
        assert [state["number"] for state in data["drawing_states"]] == sorted(track.legacy_archive_hints)
        assert len(data["sheet"]) == len(snapshot.document.frames)
        for cell in data["cells"]:
            number = cell["number"]
            assert cell["image"] == f"layers/layer_{li:04d}/cell_{number:06d}.png"
            assert pixels(paths[cell["image"]]) == pixels(track.cells[number].image)
            assert cell["cell_name"] == track.cells[number].cell_name
            if cell["initial"]:
                assert pixels(paths[cell["initial"]]) == pixels(track.cells[number].initial_image)
        for local in data.get("compatibility", {}).get("local_states", []):
            slot = snapshot.document.frames[local["column"]].layers[li]
            if "image" in local:
                assert pixels(paths[local["image"]]) == pixels(slot.image)
            else:
                assert slot.image.pixelColor(0, 0).alpha() == 0
                assert slot.initial_image is None and slot.cell_name is None
            assert local["retained_number"] == slot.sequence_number
    # Mutable nested JSON is save-owned, even when the caller alters the result.
    payload["tracks"][0]["name"] = "changed"
    payload["frame_durations"][0] = 999
    assert json.dumps(graph_state(snapshot.document), sort_keys=True) == before


def test_compatibility_conversion_keeps_old_key_boundary_and_input_tail():
    sheet = [1, 1, 1, 1, None, None]
    starts = [False] * 6
    projected = [{"column": 0, "kind": "CELL", "number": 1, "length": 2},
                 {"column": 2, "kind": "CELL", "number": 1, "length": 2}]
    resolved = [(1, 0)] * 4 + [(9, 4), (9, 4)]
    arguments = deepcopy((sheet, starts, {4}, projected, {0: {"length": 8}}, resolved))
    result = compatibility_scalars(*arguments)
    assert arguments == (sheet, starts, {4}, projected, {0: {"length": 8}}, resolved)
    assert result["placement_keys"] == projected
    assert result["length_overrides"][0]["input_length"] == 8
    assert result["length_overrides"][0]["guard"]["imported"] is True
    assert result["resolved_overrides"] == [
        {"column": 4, "value": 9, "source_column": 4},
        {"column": 5, "value": 9, "source_column": 4}]


def test_ordinary_column_needs_no_scalar_compatibility():
    sheet = [3, 3, "BLANK", "BLANK", None]
    starts = [False, False, True, True, False]
    keys = placement_keys(sheet, starts, set())
    assert keys == [{"column": 0, "kind": "CELL", "number": 3, "length": 2},
                    {"column": 2, "kind": "BLANK", "number": None, "length": 1},
                    {"column": 3, "kind": "BLANK", "number": None, "length": 1}]
    assert compatibility_scalars(sheet, starts, set(), keys, {},
                                 [(3, 0), (3, 0), (None, None), (None, None), (None, None)]) == {}


def fixture_payload(case):
    loaded = project_io.load_project(ROOT / "v1" / f"{case}.pman")
    return project_io.build_v2_payload(project_io.ProjectSnapshot(
        loaded.metadata, loaded.document, loaded.width, loaded.height))[0]


def test_v2_builder_pending_duplicates_none_and_legacy_placement_boundaries():
    data = fixture_payload("placement-key-boundary")["tracks"][0]
    assert data["sheet"] == [1, 1, 1, 1, None, None]
    assert data["pending"] == [{"column": 4, "number": 9, "kind": "CELL", "length": 2}]
    assert data["compatibility"]["placement_keys"] == [
        {"column": 0, "kind": "CELL", "number": 1, "length": 2},
        {"column": 2, "kind": "CELL", "number": 1, "length": 2}]
    assert data["compatibility"]["resolved_overrides"] == [
        {"column": 4, "value": 9, "source_column": 4},
        {"column": 5, "value": 9, "source_column": 4}]
    duplicate = fixture_payload("pending-duplicate")["tracks"][0]["pending"]
    assert [p["number"] for p in duplicate] == [2, 2]
    assert [p["column"] for p in duplicate] == [4, 5]
    assert fixture_payload("pending-none")["tracks"][0]["pending"] == [
        {"column": 5, "number": None, "kind": "EMPTY", "length": 1}]


def test_v2_builder_does_not_make_hints_from_bank_only_or_renumber():
    data = fixture_payload("all-unplaced")["tracks"][0]
    assert [cell["number"] for cell in data["cells"]] == [1, 8, 12]
    assert data["sheet"] == [None, None, None]
    assert [hint["number"] for hint in data["drawing_states"]] == [1, 8, 12]
    loaded = project_io.load_project(ROOT / "v1" / "all-unplaced.pman")
    loaded.document.tracks[0].legacy_archive_hints.clear()
    payload, _ = project_io.build_v2_payload(project_io.ProjectSnapshot(
        loaded.metadata, loaded.document, loaded.width, loaded.height))
    assert payload["tracks"][0]["drawing_states"] == []
    assert [cell["number"] for cell in payload["tracks"][0]["cells"]] == [1, 8, 12]


@pytest.mark.parametrize("case", CASES)
def test_v2_explicit_archive_roundtrip(tmp_path, monkeypatch, case):
    from paintmaskanimator import constants
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 4)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 3)
    loaded = project_io.load_project(ROOT / "v1" / f"{case}.pman")
    path = tmp_path / "v2.pman"
    snapshot = project_io.ProjectSnapshot(loaded.metadata, loaded.document, loaded.width, loaded.height)
    project_io.write_project_snapshot(path, snapshot)
    reopened = project_io.load_project(path)
    assert reopened.format_version == 2
    assert graph_state(reopened.document) == graph_state(loaded.document)


from test_p5_6_characterization import qapp, window as _window  # noqa: E402, F401

window = _window
from p5_6_support import observe  # noqa: E402


def assert_stable_v2_json(path, tmp_path):
    """Compare actual ZIP JSON over two further decode/write cycles."""
    from zipfile import ZipFile
    def saved_json(source):
        with ZipFile(source) as archive:
            return json.loads(archive.read("project.json"))
    expected = saved_json(path)
    for cycle in range(2):
        loaded = project_io.load_project(path)
        destination = tmp_path / f"{path.stem}-cycle-{cycle}.pman"
        project_io.write_project_snapshot(destination, project_io.ProjectSnapshot.capture(
            loaded.metadata, loaded.document))
        assert saved_json(destination) == expected
        path = destination


@pytest.mark.parametrize("case", CASES)
def test_v2_a_read_install_and_canonical_roundtrip(window, tmp_path, case):
    expected = json.loads((ROOT / "expected" / f"{case}.json").read_text(encoding="utf-8"))["read_install"]
    loaded = project_io.load_project(ROOT / "v1" / f"{case}.pman")
    path = tmp_path / "v2.pman"
    snap = project_io.ProjectSnapshot(loaded.metadata, loaded.document, loaded.width, loaded.height)
    first, _ = project_io.build_v2_payload(snap)
    project_io.write_project_snapshot(path, snap)
    reopened = project_io.load_project(path)
    for frame in reopened.document.frames:
        for slot in frame.layers:
            if slot.sequence_number is not None and (slot.has_content or slot.sequence_only):
                assert "_cell" not in slot.__dict__
                assert slot._cell is slot._track.cells[slot.sequence_number]
    window.canvas._document = reopened.document
    window.canvas._invalidate_exposure_index()
    assert json.loads(json.dumps(observe(window))) == expected
    second, _ = project_io.build_v2_payload(project_io.ProjectSnapshot(
        reopened.metadata, reopened.document, reopened.width, reopened.height))
    assert second == first
    assert_stable_v2_json(path, tmp_path)


@pytest.mark.parametrize("case", CASES)
def test_v2_ui_open_roundtrip_matches_a(window, tmp_path, case):
    expected = json.loads((ROOT / "expected" / f"{case}.json").read_text(encoding="utf-8"))["ui_open"]
    assert window.project.open(ROOT / "v1" / f"{case}.pman")
    path = tmp_path / "ui-v2.pman"
    project_io.write_project_snapshot(path, project_io.ProjectSnapshot.capture(
        window.build_project_metadata(), window.canvas._document))
    assert window.project.open(path)
    assert json.loads(json.dumps(observe(window))) == expected


def handwritten_payload():
    return {"format": "PaintMaskAnimatorProject", "format_version": 2, "mask_format": 2,
            "canvas": {"width": 4, "height": 3}, "frame_durations": [1, 2, 1],
            "tracks": [{"cells": [{"number": 9, "image": "art.png", "initial": "start.png"},
                                   {"number": 12, "image": "art.png"}],
                        "sheet": [9, "BLANK", None],
                        "positions": [{"column": 1, "blank_start": False}],
                        "drawing_states": [{"number": 12, "has_content": False, "length": 3}]}]}


def write_handwritten(path, payload, *, raw=None, extra=None):
    from zipfile import ZipFile
    from p5_6_support import image
    with ZipFile(path, "w") as archive:
        archive.writestr("project.json", json.dumps(payload) if raw is None else raw)
        archive.writestr("art.png", project_io.encode_png(image("red")))
        archive.writestr("start.png", project_io.encode_png(image("blue")))
        for name, blob in (extra or {}).items():
            archive.writestr(name, blob)


def test_handwritten_v2_bank_initial_defaults_and_no_number_compaction(window, tmp_path):
    path = tmp_path / "hand.pman"
    write_handwritten(path, handwritten_payload())
    cache = project_io.PngCache()
    loaded = project_io.load_project(path, cache)
    assert cache
    assert loaded.format_version == 2
    track = loaded.document.tracks[0]
    assert sorted(track.cells) == [9, 12]
    assert sorted(track.legacy_archive_hints) == [12]
    assert track.legacy_archive_hints[12].exposure == 3
    assert not track.alpha_locked
    assert track.cells[9].initial_image is not None
    assert not loaded.document.frames[1].layers[0].blank_start
    assert window.project.open(path)
    assert sorted(window.canvas._document.tracks[0].cells) == [9, 12]
    # Bank-only documents must also keep their original numbers on UI open.
    payload = handwritten_payload()
    payload["tracks"][0]["sheet"] = [None] * 3
    payload["tracks"][0]["positions"] = []
    write_handwritten(path, payload)
    assert window.project.open(path)
    assert sorted(window.canvas._document.tracks[0].cells) == [9, 12]


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(format_version=True),
    lambda p: p.update(format_version=0),
    lambda p: p.update(frame_durations=[]),
    lambda p: p.update(frame_durations=[1, True, 1]),
    lambda p: p.update(tracks=[]),
    lambda p: p["canvas"].update(width=16385),
    lambda p: p["tracks"][0].update(sheet=[9]),
    lambda p: p["tracks"][0].update(sheet=[True, None, None]),
    lambda p: p["tracks"][0].update(sheet=[10, None, None]),
    lambda p: p["tracks"][0]["cells"].append({"number": 9, "image": "art.png"}),
    lambda p: p["tracks"][0]["cells"][0].update(number=10001),
    lambda p: p["tracks"][0]["cells"][0].update(image="../art.png"),
    lambda p: p["tracks"][0]["cells"][0].update(image="/art.png"),
    lambda p: p["tracks"][0]["cells"][0].update(initial="missing.png"),
    lambda p: p["tracks"][0].update(opacity=float("inf")),
    lambda p: p["tracks"][0].update(color_filter_rgb=[0, 0, 256]),
    lambda p: p["tracks"][0].update(positions=[{"column": 0, "blank_start": True}]),
    lambda p: p["tracks"][0].update(pending=[{"column": 1, "kind": "NO"}]),
    lambda p: p["tracks"][0].update(pending=[{"column": 1, "kind": "EMPTY"}]*2),
    lambda p: p["tracks"][0].update(drawing_states=[{"number": 12}]*2),
    lambda p: p["tracks"][0].pop("cells"),
    lambda p: p["tracks"][0].update(compatibility={"resolved_overrides": [{"column": 3}]}),
])
def test_v2_rejects_malformed_handwritten_structure(tmp_path, mutation):
    from paintmaskanimator.errors import OperationError
    payload = handwritten_payload()
    mutation(payload)
    path = tmp_path / "bad.pman"
    write_handwritten(path, payload)
    with pytest.raises(OperationError):
        project_io.load_project(path)


def test_v2_rejects_duplicate_json_keys(tmp_path):
    from paintmaskanimator.errors import OperationError
    path = tmp_path / "duplicate.pman"
    raw = json.dumps(handwritten_payload()).replace('"format_version": 2',
                                                   '"format_version": 2, "format_version": 2')
    write_handwritten(path, None, raw=raw)
    with pytest.raises(OperationError):
        project_io.load_project(path)


@pytest.mark.parametrize("limit,value", [
    ("MAX_PROJECT_FRAMES", 2), ("MAX_PROJECT_LAYERS", 0),
    ("MAX_PROJECT_LAYER_CELLS", 2), ("MAX_PROJECT_METADATA_BYTES", 1),
    ("MAX_PROJECT_IMAGE_BYTES", 1), ("MAX_PROJECT_ARCHIVE_BYTES", 1),
    ("MAX_PROJECT_DECODED_PIXELS", 12),
])
def test_v2_limits_include_initial_png(tmp_path, monkeypatch, limit, value):
    from paintmaskanimator.errors import OperationError
    path = tmp_path / "limit.pman"
    write_handwritten(path, handwritten_payload())
    monkeypatch.setattr(project_io, limit, value)
    with pytest.raises(OperationError):
        project_io.load_project(path)


def test_v2_rejects_png_header_dimensions_before_decode(tmp_path, monkeypatch):
    import struct
    from paintmaskanimator.errors import OperationError
    path = tmp_path / "oversize.pman"
    blob = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 16384, 16384)
    write_handwritten(path, handwritten_payload(), extra={"huge.png": blob})
    payload = handwritten_payload()
    payload["tracks"][0]["cells"][0]["image"] = "huge.png"
    write_handwritten(path, payload, extra={"huge.png": blob})
    monkeypatch.setattr(project_io, "_decode_png", lambda *_: pytest.fail("decoded oversized header"))
    with pytest.raises(OperationError):
        project_io.load_project(path)


@pytest.mark.parametrize("case", CASES)
def test_v2_after_pending_placement_edit_and_undo(window, tmp_path, case):
    assert window.project.open(ROOT / "v1" / f"{case}.pman")
    canvas = window.canvas
    canvas.timeline_mode = "sequence"
    assert canvas.insert_sequence_blank(0, max(canvas._document.tracks[0].cells, default=0))
    window.timeline_ops.set_mode("sheet")

    def check(stage):
        before = graph_state(canvas._document)
        observed = observe(window)
        path = tmp_path / f"{stage}.pman"
        snapshot = project_io.ProjectSnapshot.capture(window.build_project_metadata(), canvas._document)
        project_io.write_project_snapshot(path, snapshot)
        assert_stable_v2_json(path, tmp_path)
        loaded = project_io.load_project(path)
        assert graph_state(loaded.document) == before
        original = canvas._document
        canvas._document = loaded.document
        canvas._invalidate_exposure_index()
        try:
            after = observe(window)
            for key in ("render", "sheet", "positions", "tracks", "durations", "sheet_duration", "export"):
                assert after[key] == observed[key], (stage, key)
        finally:
            canvas._document = original
            canvas._invalidate_exposure_index()

    check("placed")
    canvas.current_frame = 0
    canvas.delete_frame()
    check("deleted")
    canvas.undo()
    check("undo")
    canvas.redo()
    check("redo")


@pytest.mark.parametrize("expiration", ["signature", "marker", "position", "count"])
def test_v2_resolved_source_is_read_and_survives_snapshot(tmp_path, expiration):
    from paintmaskanimator.native import resolve_timeline
    payload = handwritten_payload()
    payload["tracks"][0]["sheet"] = [9, 9, 9]
    payload["tracks"][0]["positions"] = []
    payload["tracks"][0]["compatibility"] = {"resolved_overrides": [
        {"column": 2, "value": 9, "source_column": 1}]}
    path = tmp_path / "source.pman"
    write_handwritten(path, payload)
    loaded = project_io.load_project(path)
    assert resolve_timeline(loaded.document.frames)[:, 0].tolist() == [0, 0, 1]
    cloned = loaded.document.clone_graph()
    assert resolve_timeline(cloned.frames)[:, 0].tolist() == [0, 0, 1]
    if expiration == "signature":
        cloned.frames[2].layers[0].has_content = False
        expected = [0, 0, -1]
    elif expiration == "marker":
        cloned.frames[2].layers[0].__dict__.pop("_dense_import")
        expected = [0, 0, 0]
    elif expiration == "position":
        cloned.frames[1], cloned.frames[2] = cloned.frames[2], cloned.frames[1]
        expected = [0, 0, 0]
    else:
        cloned.frames.pop(0)
        expected = [0, 0]
    assert resolve_timeline(cloned.frames)[:, 0].tolist() == expected


@pytest.mark.parametrize("source", [0, 1, 3])
def test_v2_rejects_resolved_source_without_matching_drawing(tmp_path, source):
    from paintmaskanimator.errors import OperationError
    payload = handwritten_payload()
    payload["tracks"][0]["compatibility"] = {"resolved_overrides": [
        {"column": 2, "value": 12, "source_column": source}]}
    path = tmp_path / "bad-source.pman"
    write_handwritten(path, payload)
    with pytest.raises(OperationError):
        project_io.load_project(path)


def test_v2_writer_rejection_preserves_existing_file(tmp_path, monkeypatch):
    from paintmaskanimator.errors import OperationError
    path = tmp_path / "existing.pman"
    path.write_bytes(b"original")
    loaded = project_io.load_project(ROOT / "v1" / "missing.pman")
    snapshot = project_io.ProjectSnapshot(loaded.metadata, loaded.document, loaded.width, loaded.height)
    monkeypatch.setattr(project_io, "MAX_PROJECT_METADATA_BYTES", 1)
    with pytest.raises(OperationError):
        project_io.write_project_snapshot(path, snapshot)
    assert path.read_bytes() == b"original"
    assert not path.with_suffix(".pman.tmp").exists()


@pytest.mark.parametrize("case", CASES)
def test_v2_compatibility_placement_matches_legacy_algorithm(window, tmp_path, case):
    expected = json.loads((ROOT / "expected" / f"{case}.json").read_text(encoding="utf-8"))["mode_placement"]
    original = project_io.load_project(ROOT / "v1" / f"{case}.pman")
    path = tmp_path / "placement.pman"
    project_io.write_project_snapshot(path, project_io.ProjectSnapshot(
        original.metadata, original.document, original.width, original.height))
    loaded = project_io.load_project(path)
    window.canvas._document = loaded.document
    window.canvas._invalidate_exposure_index()
    window.canvas.timeline_mode = "sequence"
    window.timeline_ops.set_mode("sheet")
    assert json.loads(json.dumps(observe(window))) == expected


def test_v2_zip_contract_and_png_names(tmp_path, monkeypatch):
    from zipfile import ZipFile, ZIP_STORED
    from paintmaskanimator import constants
    from p5_6_support import image
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 4)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 3)
    loaded = project_io.load_project(ROOT / "v1" / "hidden-retained.pman")
    path = tmp_path / "names.pman"
    project_io.write_project_snapshot(path, project_io.ProjectSnapshot(
        loaded.metadata, loaded.document, loaded.width, loaded.height, image()))
    with ZipFile(path) as archive:
        entries = archive.infolist()
        assert entries[0].filename == "thumbnail.png"
        assert entries[0].compress_type == ZIP_STORED
        assert not entries[0].flag_bits & 8
        assert len(entries) == len({entry.filename for entry in entries})
        assert "layers/layer_0000/cell_000012.png" in archive.namelist()
        assert "initial/layers/layer_0000/cell_000012.png" in archive.namelist()
        assert "compatibility/layer_0000/position_000001.png" in archive.namelist()
        assert "initial/compatibility/layer_0000/position_000001.png" in archive.namelist()


@pytest.mark.parametrize("extra", [{"../unused.png": b"x"}, {"/unused.png": b"x"},
                                   {"unused.dat": b"x" * 2048}])
def test_v2_unreferenced_zip_entries_are_checked(tmp_path, monkeypatch, extra):
    from paintmaskanimator.errors import OperationError
    path = tmp_path / "unsafe.pman"
    write_handwritten(path, handwritten_payload(), extra=extra)
    if "unused.dat" in extra:
        monkeypatch.setattr(project_io, "MAX_PROJECT_ARCHIVE_BYTES", 1024)
    with pytest.raises(OperationError):
        project_io.load_project(path)


def test_v2_slot_and_artwork_budgets_are_separate(tmp_path, monkeypatch):
    from paintmaskanimator.errors import OperationError
    path = tmp_path / "budgets.pman"
    payload = handwritten_payload()
    write_handwritten(path, payload)
    monkeypatch.setattr(project_io, "MAX_PROJECT_LAYER_CELLS", 3)
    # S=3, B=2: S+B exceeds three, but each independent budget fits.
    assert project_io.load_project(path).format_version == 2
    payload["tracks"][0]["compatibility"] = {"local_states": [
        {"column": 1}, {"column": 2}]}
    write_handwritten(path, payload)
    # S still fits; B+L=4 now exceeds the artwork budget.
    with pytest.raises(OperationError):
        project_io.load_project(path)


def test_v2_identical_png_bytes_share_decoded_pixel_budget(tmp_path, monkeypatch):
    path = tmp_path / "deduplicated.pman"
    payload = handwritten_payload()
    payload["tracks"][0]["cells"][0]["initial"] = "art.png"
    write_handwritten(path, payload)
    monkeypatch.setattr(project_io, "MAX_PROJECT_DECODED_PIXELS", 12)
    assert project_io.load_project(path).format_version == 2


def test_v2_length_guard_without_import_does_not_gain_input_length(tmp_path):
    payload = handwritten_payload()
    payload["tracks"][0]["sheet"] = [9, 9, 9]
    payload["tracks"][0]["positions"] = []
    payload["tracks"][0]["compatibility"] = {"length_overrides": [
        {"column": 0, "length": 5, "guard": {"kind": "CELL", "number": 9,
         "pending": False, "blank_start": False, "imported": False}}]}
    path = tmp_path / "guard.pman"
    write_handwritten(path, payload)
    loaded = project_io.load_project(path)
    built, _ = project_io.build_v2_payload(project_io.ProjectSnapshot(
        loaded.metadata, loaded.document, loaded.width, loaded.height))
    assert built["tracks"][0]["compatibility"]["length_overrides"] == payload["tracks"][0]["compatibility"]["length_overrides"]


def test_v2_tween_move_repeated_json(window, tmp_path, monkeypatch):
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QMessageBox
    from test_p5_3_characterization import install, row
    from p5_6_support import slot
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *_: QMessageBox.StandardButton.Ok))
    canvas = install(window, [slot(1, "red", has_content=True, exposure=3), slot(), slot(),
                              slot(), slot(2, "blue", has_content=True)])
    canvas.current_frame = 0
    window.tween.enable(row(canvas), 0, "free")
    canvas.transform_points = [QPointF(p.x() + 2, p.y() + 1) for p in canvas.transform_points]
    window.tween.commit_transform_or_tween()
    window.tween.edit(row(canvas), 0)
    window.tween.cancel_transform_or_tween()
    assert canvas.move_timeline_cell(1, 0, 3, 0)
    for stage in ("moved", "undo", "redo"):
        if stage == "undo":
            canvas.undo()
        elif stage == "redo":
            canvas.redo()
        path = tmp_path / f"tween-{stage}.pman"
        assert window.project.write(path)
        assert_stable_v2_json(path, tmp_path)
    assert window.project.open(path)
    def payload():
        return project_io.build_v2_payload(project_io.ProjectSnapshot.capture({}, canvas._document))[0]
    before = payload()
    position = canvas.frames[0].layers[0].__dict__["_v1_position"]
    assert position.legacy.exposure == 1 and position.input_length == 3
    canvas.current_frame = 0
    canvas.delete_frame()
    canvas.undo()
    assert payload() == before
    canvas.redo()
    edited = tmp_path / "loaded-redo.pman"
    assert window.project.write(edited)
    assert_stable_v2_json(edited, tmp_path)


@pytest.mark.parametrize("expiration", ["signature", "marker"])
def test_v2_separate_input_length_clone_and_guard(tmp_path, expiration):
    from dataclasses import replace
    from paintmaskanimator.document import project_v1_state
    loaded = project_io.load_project(ROOT / "v1" / "placement-key-boundary.pman")
    slot = loaded.document.frames[0].layers[0]
    position = slot.__dict__["_v1_position"]
    slot.__dict__["_v1_position"] = replace(position, input_length=9)
    clone = loaded.document.clone_graph()
    copied = clone.frames[0].layers[0]
    assert copied.__dict__["_v1_position"].input_length == 9
    def payload():
        return project_io.build_v2_payload(project_io.ProjectSnapshot.capture(
            loaded.metadata, clone))[0]["tracks"][0].get("compatibility", {})
    assert payload()["length_overrides"][0]["input_length"] == 9
    if expiration == "signature":
        copied.blank_start = not copied.blank_start
    else:
        copied.__dict__.pop("_dense_import", None)
    assert all("input_length" not in value or value["column"] != 0
               for value in payload().get("length_overrides", []))
    assert slot.__dict__["_v1_position"].input_length == 9
    assert project_v1_state(clone.frames)[0][0].layers[0].exposure != 9


def test_v2_default_blank_has_no_png_and_fits_zero_pixel_budget(tmp_path, monkeypatch):
    from zipfile import ZipFile
    from PySide6.QtGui import QImage
    from paintmaskanimator.document import Document
    from paintmaskanimator.legacy_v1 import raw_v1_slot
    from paintmaskanimator.models import Frame
    image = QImage(4, 3, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    document = Document()
    document.install_legacy_frames([Frame([raw_v1_slot("A", image)]) for _ in range(6)])
    path = tmp_path / "blank-v2.pman"
    monkeypatch.setattr(project_io, "MAX_PROJECT_DECODED_PIXELS", 0)
    project_io.write_project_snapshot(path, project_io.ProjectSnapshot({}, document, 4, 3))
    with ZipFile(path) as archive:
        assert archive.namelist() == ["project.json"]
    loaded = project_io.load_project(path)
    assert len(loaded.document.frames) == 6
    assert not loaded.document.tracks[0].cells
    assert all(frame.layers[0].image == image for frame in loaded.document.frames)


@pytest.mark.parametrize("boundary", ["reader", "writer"])
@pytest.mark.parametrize("pixels_limit", [35, 36])
def test_v2_pixel_budget_combines_art_initial_and_local(tmp_path, monkeypatch, boundary, pixels_limit):
    from paintmaskanimator.errors import OperationError
    from p5_6_support import image
    payload = handwritten_payload()
    payload["tracks"][0]["compatibility"] = {"local_states": [
        {"column": 1, "image": "local.png"}]}
    path = tmp_path / "combined.pman"
    write_handwritten(path, payload, extra={"local.png": project_io.encode_png(image("green"))})
    loaded = project_io.load_project(path)
    monkeypatch.setattr(project_io, "MAX_PROJECT_DECODED_PIXELS", pixels_limit)
    def operation():
        if boundary == "reader":
            return project_io.load_project(path)
        return project_io.write_project_snapshot(tmp_path / "output.pman", project_io.ProjectSnapshot(
            loaded.metadata, loaded.document, 4, 3))
    if pixels_limit == 35:
        with pytest.raises(OperationError):
            operation()
    else:
        operation()
