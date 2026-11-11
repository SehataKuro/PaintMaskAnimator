"""F regressions with synthetic artwork and legacy inputs, independent of /tmp."""
from copy import deepcopy
from dataclasses import replace
import json
from zipfile import ZipFile

import pytest

from paintmaskanimator import project_io
from paintmaskanimator.document import Document
from paintmaskanimator.errors import OperationError
from paintmaskanimator.models import Frame
from paintmaskanimator.timeline_metadata import reference_for
from p5_6_support import create_v1, image, pixels, slot, source
from test_p5_6_characterization import qapp, window as _window  # noqa: F401

window = _window


def snapshot(w):
    return project_io.ProjectSnapshot.capture(w.build_project_metadata(), w.canvas._document)


def invariant(s):
    d = s.document
    return {
        "bank": [{n: (pixels(c.image), pixels(c.initial_image), c.cell_name)
                  for n, c in t.cells.items()} for t in d.tracks],
        "names": [t.name for t in d.tracks],
        "sheet": [[reference_for(s) for s in f.layers] for f in d.frames],
        "resolve": [[reference_for(s, resolve=True).number for s in f.layers] for f in d.frames],
        "starts": [[s.blank_start for s in f.layers] for f in d.frames],
        "positions": [[(s.tween, s.tween_member) for s in f.layers] for f in d.frames],
    }


def rendered(w, document):
    c = w.canvas
    original = c._document
    try:
        c._document = document
        c._invalidate_exposure_index()
        return [pixels(c.composite(i, white=False)) for i in range(len(document.frames))]
    finally:
        c._document = original
        c._invalidate_exposure_index()


def roundtrip(s, path, w=None):
    expected = invariant(s)
    expected_render = rendered(w, s.document) if w else None
    before = None
    render_differences = []
    for generation in range(4):
        project_io.write_project_snapshot(path, s)
        with ZipFile(path) as z:
            payload = json.loads(z.read("project.json"))
        if before is not None:
            assert payload == before
        before = payload
        loaded = project_io.load_project(path)
        s = project_io.ProjectSnapshot(loaded.metadata, loaded.document, 4, 3)
        assert invariant(s) == expected
        if w:
            if rendered(w, s.document) != expected_render:
                render_differences.append(generation)
    assert not render_differences, f"composite changed in generations {render_differences}"
    return s


def refused_without_changes(w, path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    w.project.mark_saved()
    w.canvas._document.tracks[0].name += " edited"
    assert w.project.has_unsaved_changes()
    before = invariant(snapshot(w))
    before_render = rendered(w, w.canvas._document)
    path.write_bytes(b"existing file")
    with pytest.raises(OperationError):
        project_io.write_project_snapshot(path, snapshot(w))
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[-1]))
    assert not w.project.write(path)
    assert errors == ["保存できませんでした。\n\nプロジェクトの構造が不正です。"]
    assert path.read_bytes() == b"existing file"
    assert not path.with_suffix(path.suffix + ".tmp").exists()
    assert w.project.has_unsaved_changes()
    assert invariant(snapshot(w)) == before
    assert rendered(w, w.canvas._document) == before_render


def install_legacy(w, frames, archive=None):
    d = Document()
    d.install_legacy_frames(frames, archive or {}, canvas_size=(4, 3))
    w.canvas._document = d
    w.canvas._invalidate_exposure_index()
    w.canvas.current_frame = 0
    w.canvas.active_layer_index = 0
    return d


@pytest.mark.parametrize("operation", ["alone", "duplicate", "move", "undo_redo", "twice"])
def test_blank_pending_sequence_save_and_legacy_placement(window, tmp_path, operation):
    path = tmp_path / "input.pman"
    create_v1("pending-collision-7-6", path)
    assert window.project.open(path)
    c = window.canvas
    c.timeline_mode = "sequence"
    assert c.insert_sequence_blank(0, 1)
    if operation == "duplicate":
        window.layers.duplicate_rows([0])
    elif operation == "move":
        window.layers.add_fast()
        window.layers.move_row([0], len(c.layers) - 1)
    elif operation == "undo_redo":
        before = invariant(snapshot(window))
        c.undo()
        c.redo()
        assert invariant(snapshot(window)) == before
    elif operation == "twice":
        assert c.insert_sequence_blank(0, 1)
    original = c._document.clone_graph()
    pending = [(i, li) for i, f in enumerate(c.frames) for li, s in enumerate(f.layers)
               if s.sequence_only and s.is_blank_key and s.blank_start]
    assert pending
    assert window.project.write(tmp_path / "ui-blank.pman")
    saved = roundtrip(snapshot(window), tmp_path / "blank.pman", window)
    # Compare with placement on the unsaved v1-derived live state. A v1 ZIP
    # cannot be the boundary oracle: its pending record has no blank_start.
    c._document = original
    c._invalidate_exposure_index()
    c.timeline_mode = "sequence"
    window.timeline_ops.set_mode("sheet")
    expected = invariant(snapshot(window))
    c._document = saved.document
    c._invalidate_exposure_index()
    c.timeline_mode = "sequence"
    window.timeline_ops.set_mode("sheet")
    assert invariant(snapshot(window)) == expected
    roundtrip(snapshot(window), tmp_path / "placed.pman", window)
    c.undo()
    c.redo()
    assert invariant(snapshot(window)) == expected


@pytest.mark.parametrize("key", range(4, 9))
def test_old_two_track_virtual_tail(window, tmp_path, key, monkeypatch):
    frames, _ = source("placement-key-boundary")
    while len(frames) <= key:
        frames.append(Frame([slot()]))
    for i, f in enumerate(frames):
        f.layers.append(slot(3, "green", has_content=True) if i == key else slot())
    path = tmp_path / "historical.pman"
    project_io.legacy_fixture_write_archive(path, {
        "format": "PaintMaskAnimatorProject", "format_version": 1, "frames": [],
        "canvas": {"width": 4, "height": 3}, "mask_format": 2,
        "timeline_mode": "sequence"}, frames, {})
    assert window.project.open(path)
    if key >= 5:
        refused_without_changes(window, tmp_path / "normalized.pman", monkeypatch)
    else:
        roundtrip(snapshot(window), tmp_path / "normalized.pman", window)


def test_artificial_four_tracks_and_collision(window, tmp_path, monkeypatch):
    # Same synthetic rows as the probe, without its frozen archive dependencies.
    inputs = [source(n)[0] for n in
              ("pending-duplicate", "pending-none", "placement-key-boundary")]
    collision = tmp_path / "collision.pman"
    create_v1("pending-collision-7-6", collision)
    assert window.project.open(collision)
    other = window.canvas._document.clone_graph()
    frames = [Frame([rows[i].layers[0] if i < len(rows) else slot()
                     for rows in inputs]) for i in range(7)]
    d = install_legacy(window, frames)
    first = d.frames[0].layers[0]
    first._v1_position = replace(first._v1_position, input_length=None)
    d.tracks.append(other.tracks[0])
    for f, old in zip(d.frames, other.frames):
        f.layers.append(old.layers[0])
    path = tmp_path / "four.pman"
    project_io.write_project_snapshot(path, snapshot(window))
    assert window.project.open(path)
    payload, _ = project_io.build_v2_payload(snapshot(window))
    entry = next(e for e in payload["tracks"][2]["compatibility"]["resolved_overrides"]
                 if e["column"] == 5)
    assert entry == {"column": 5, "value": 9, "source_column": 5}
    assert pixels(window.canvas.frames[5].layers[2].image) != pixels(
        window.canvas._document.tracks[2].cells[9].image)
    refused_without_changes(window, path, monkeypatch)


@pytest.mark.parametrize("candidates,source_column,expected", [
    ([0, 2, 6], 4, 4), ([4, 6], 1, 1), ([0, 2, 6], 6, 6), ([], 3, 3)])
def test_source_candidates_and_missing_preserves_destination(
        window, tmp_path, candidates, source_column, expected, monkeypatch):
    from paintmaskanimator.legacy_v1 import DenseReference
    frames = [Frame([slot(9, "blue", has_content=True, initial_image=image("yellow"),
                          cell_name="source artwork") if i in candidates else slot()])
              for i in range(8)]
    d = install_legacy(window, frames, {(0, 9): slot(9, "blue", has_content=True)})
    target = d.frames[7].layers[0]
    signature = (target.has_content, target.is_blank_key, target.sequence_number,
                 target.sequence_only, target.blank_start)
    target._dense_import = (signature, DenseReference(), DenseReference("CELL", 9))
    target._v2_resolve_source = (signature, source_column, 7, 8)
    s = snapshot(window)
    before = invariant(s)
    payload, _ = project_io.build_v2_payload(s)
    assert invariant(s) == before
    entry = next(e for e in payload["tracks"][0]["compatibility"]["resolved_overrides"]
                 if e["column"] == 7)
    assert entry["source_column"] == expected
    path = tmp_path / "existing.pman"
    path.write_bytes(b"existing file")
    if candidates and source_column not in candidates:
        assert pixels(d.frames[source_column].layers[0].image) != pixels(d.tracks[0].cells[9].image)
        refused_without_changes(window, path, monkeypatch)
    elif candidates:
        roundtrip(s, path, window)
    else:
        with pytest.raises(OperationError):
            project_io.write_project_snapshot(path, s)
        assert path.read_bytes() == b"existing file"
        assert not path.with_suffix(".pman.tmp").exists()


@pytest.mark.parametrize("kind", [None, "CELL", "EMPTY", "BLANK"])
def test_blank_start_only_accepts_blank_pending(tmp_path, kind):
    from test_project_v2 import handwritten_payload, write_handwritten
    p = deepcopy(handwritten_payload())
    t = p["tracks"][0]
    t["sheet"] = [None] * 3
    t["positions"] = [{"column": 1, "blank_start": True}]
    t["pending"] = [] if kind is None else [{"column": 1, "number": 9, "kind": kind}]
    path = tmp_path / "boundary.pman"
    write_handwritten(path, p)
    if kind == "BLANK":
        assert project_io.load_project(path).document.frames[1].layers[0].blank_start
    else:
        with pytest.raises(OperationError):
            project_io.load_project(path)


@pytest.mark.parametrize("candidates,source_column,expected", [
    ([0, 2, 6], 4, 2), ([4, 6], 1, 4)])
def test_equal_source_artwork_can_normalize(window, tmp_path, candidates, source_column, expected):
    from paintmaskanimator.legacy_v1 import DenseReference

    frames = [Frame([slot(9, "blue", has_content=True) if i in candidates else slot()])
              for i in range(8)]
    d = install_legacy(window, frames)
    # Distinct QImage objects with the same pixels must also be accepted.
    d.frames[source_column].layers[0].image = d.tracks[0].cells[9].image.copy()
    target = d.frames[7].layers[0]
    signature = (target.has_content, target.is_blank_key, target.sequence_number,
                 target.sequence_only, target.blank_start)
    target._dense_import = (signature, DenseReference(), DenseReference(
        kind="CELL", number=9))
    target._v2_resolve_source = (signature, source_column, 7, 8)
    payload, _ = project_io.build_v2_payload(snapshot(window))
    entry = next(e for e in payload["tracks"][0]["compatibility"]["resolved_overrides"]
                 if e["column"] == 7)
    assert entry["source_column"] == expected
    roundtrip(snapshot(window), tmp_path / "equal.pman", window)
