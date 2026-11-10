"""D1: UI/boundary outcomes captured with the unchanged 3470c32 body.

The JSON is an offline observation, never recomputed as a test oracle. Images
contain every RGBA pixel (run-length encoded); Cell references are relative to
traversal order, never absolute identities. No event loop is pumped while logging
synchronous signals. Fixture provenance is recorded in its _capture entry.
"""
import json
from dataclasses import asdict
from itertools import groupby
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from paintmaskanimator import constants, layout_paper, project_io
from paintmaskanimator.clip_animation import (
    ClipAnimationDocument, ClipAnimationLayer, ClipCelKey, build_pma_frames,
)
from paintmaskanimator.document import ArchiveHint, Document, clear_slot, project_v1_state
from paintmaskanimator.legacy_v1 import LegacyLayerV1, legacy_layer_to_slot, snapshot_layer
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.models import Cell, Frame
from paintmaskanimator.undo_entries import DocUndo, history_label_for


BASELINE = Path(__file__).with_name("p5_2d_characterization_expected.json")


def image(color="red", width=16, height=16):
    result = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(color))
    return result


def slot(number=None, color="transparent", **kwargs):
    return legacy_layer_to_slot(LegacyLayerV1("A", image(color), sequence_number=number, **kwargs))


def pixels(img):
    if img is None:
        return None
    rgba = img.convertToFormat(QImage.Format.Format_RGBA8888)
    data = bytes(rgba.constBits())
    # RGBA8888 has exactly width*4 bytes per row, hence no padding to discard.
    return {"size": [rgba.width(), rgba.height()],
            "rgba_runs": [[key.hex(), sum(1 for _ in run)] for key, run in
                          groupby(data[i:i + 4] for i in range(0, len(data), 4))]}


def history(stack):
    def logical_bytes(entry):
        if not isinstance(entry, DocUndo):
            return entry.nbytes
        # D1 measured the sparse graph's image slots. Dense holds share a Cell;
        # observe the equivalent v1 graph instead of its storage allocation.
        frames = entry.snapshot.frames
        projected, _ = project_v1_state(frames)
        seen = set()
        total = 0
        for fi, frame in enumerate(projected):
            for li, saved in enumerate(frame.layers):
                live = frames[fi].layers[li]
                cell = (live._track.cells.get(saved.sequence_number)
                        if saved.has_content or saved.sequence_only else None)
                if cell is not None:
                    if id(cell) in seen:
                        continue
                    seen.add(id(cell))
                item = cell if cell is not None else saved
                total += item.image.sizeInBytes()
                if item.initial_image is not None:
                    total += item.initial_image.sizeInBytes()
        for track in getattr(entry.snapshot, "tracks", ()):
            for cell in track.cells.values():
                if id(cell) not in seen:
                    seen.add(id(cell))
                    total += cell.image.sizeInBytes()
                    if cell.initial_image is not None:
                        total += cell.initial_image.sizeInBytes()
        return total
    return [[type(entry).__name__, history_label_for(entry), logical_bytes(entry)]
            for entry in stack]


def graph(c, *, size=None):
    cells, refs = [], {}

    def cell_ref(cell):
        key = id(cell)
        if key not in refs:
            refs[key] = len(cells)
            cells.append({"image": pixels(cell.image),
                          "initial_image": pixels(cell.initial_image),
                          "cell_name": cell.cell_name})
        return refs[key]

    # Observe the old sparse position graph through the v1 projection. Dense
    # hold references are storage, not extra drawings in the D1 observation.
    if any(not layer._track.connected for layer in c.frames[0].layers):
        # Raw boundary observations precede installation, including mixed columns.
        projected = [Frame([snapshot_layer(s) for s in f.layers], f.duration)
                     for f in c.frames]
    else:
        projected, _archive = project_v1_state(c.frames)
    slots = []
    for fi, frame in enumerate(projected):
        row = []
        for li, saved in enumerate(frame.layers):
            live = c.frames[fi].layers[li]
            cell = (live._track.cells.get(saved.sequence_number)
                    if saved.has_content or saved.sequence_only else None)
            row.append({"number": saved.sequence_number, "content": saved.has_content,
                        "blank": saved.is_blank_key, "exposure": saved.exposure,
                        "pending": saved.sequence_only, "tween": saved.tween,
                        "tween_member": saved.tween_member,
                        "cell": cell_ref(cell if cell is not None else saved),
                        "connected": live._track.connected,
                        "owner_column": next((i for i, t in enumerate(c._document.tracks)
                                              if t is live._track), None)})
        slots.append(row)
    tracks = []
    for t in c._document.tracks:
        tracks.append({
            "name": t.name, "draft": t.is_draft, "paper": t.is_paper,
            "connected": t.connected,
            "bank": {str(n): cell_ref(cell) for n, cell in sorted(t.cells.items())},
            "hints": {str(n): asdict(h) for n, h in sorted(t.legacy_archive_hints.items())},
            "pending_slots": [[fi, li] for fi, f in enumerate(c.frames)
                              for li, s in enumerate(f.layers)
                              if s.__dict__.get("_pending_token") in t.pending_slots],
            "pending_count": len(t.pending_slots),
        })
    return {"size": size or [constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT],
            "slots": slots, "tracks": tracks, "cells": cells,
            "cursor": [c.current_frame, c.active_layer_index],
            "mode": c.timeline_mode, "undo": history(c.undo_stack),
            "redo": history(c.redo_stack)}


class Recorder:
    def __init__(self, c):
        self.canvas = c
        self.events = []
        self.states = []
        # These observers run synchronously, after the window's own handlers.
        for name in ("cellChanged", "changed", "selectionChanged", "selectionCleared", "colorUsed"):
            getattr(c, name).connect(lambda *args, name=name: self.signal(name, args))

    def signal(self, name, args):
        c = self.canvas
        self.events.append({"signal": name,
                            "args": [list(arg.getRgb()) if isinstance(arg, QColor) else arg
                                     for arg in args], "graph": graph(c)})

    def take(self, phase):
        self.states.append({"phase": phase, "graph": graph(self.canvas),
                            "events": list(self.events)})
        self.events.clear()

    def roundtrip(self):
        self.take("after")
        self.canvas.undo()
        self.take("undo")
        self.canvas.redo()
        self.take("redo")


def golden(key, actual):
    # No update flag: replacing this fixture requires a separate baseline capture.
    assert actual == json.loads(BASELINE.read_text(encoding="utf-8"))[key]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 16)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 16)
    w = MainWindow()
    for timer in (w._autosave_timer, w._used_color_timer, w._title_timer):
        timer.stop()
    monkeypatch.setattr(w.used_color, "schedule_refresh", lambda: None)
    monkeypatch.setattr(w.used_color, "refresh_with_counter", lambda *_: None)
    c = w.canvas
    c.install_legacy_frames([Frame([slot(1, "red", has_content=True)])])
    c.timeline_mode = "sheet"
    c.current_frame = c.active_layer_index = 0
    c.undo_stack.clear()
    c.redo_stack.clear()
    w.refresh_ui()
    yield w
    w._closing = True
    w.deleteLater()


def install(c, slots, archive=None):
    c.current_frame = c.active_layer_index = 0
    c.install_legacy_frames([Frame([s]) for s in slots], archive or {})
    c.undo_stack.clear()
    c.redo_stack.clear()


def conflicts():
    return [slot(1, "red", has_content=True, cell_name="first", exposure=2),
            slot(), slot(1, "red", has_content=True, initial_image=image("blue")),
            slot(12, "green", is_blank_key=True),
            slot(1, "yellow", has_content=True, initial_image=image("green"),
                 cell_name="later"),
            slot(8, "magenta", has_content=True, sequence_only=True,
                 tween={"points": [1, 2]}, tween_member="pending")]


def held(c, kind=None):
    kind = kind or {}
    install(c, [slot(1, "red", has_content=True, exposure=3, **kind),
                slot(**kind), slot(**kind),
                slot(1, "red", has_content=True, **kind),
                slot(1, "green", is_blank_key=True, **kind),
                slot(1, "yellow", **kind)])
    c.current_frame = 2


def paint(c, route="full"):
    c.ensure_editable_key()
    c.main_color = QColor("blue")
    if route == "tiles":
        c.pen_size = 3
        c._begin_opaque_brush_stroke()
        c.draw_line(QPoint(5, 5), QPoint(5, 5), 1.0)
        c._finish_opaque_brush_stroke()
        # Existing-content mouse/tablet release notifies after finalizing tiles.
        c._cell_structure_dirty = False
        c.cellChanged.emit(c.current_frame, c.active_layer_index)
    elif route == "region":
        c.flood_fill(QPoint(5, 5))
    else:
        c.push_layer_undo()
        c.active_layer.image.setPixelColor(5, 5, QColor("blue"))
        c.cellChanged.emit(c.current_frame, c.active_layer_index)


def test_refresh_installs_detached_conflicts_once(window):
    c = window.canvas
    existing = c.frames[0].layers[0]
    existing.name = "B"
    track = existing._track
    track.cells[9] = Cell(image("cyan"), image("black"), "archive")
    c._document.put_archive_hint(track, 9, ArchiveHint())
    # A raw conflicting column alongside an already registered hint-bearing bank.
    # Deliberately stage the raw refresh boundary, rather than the frames setter
    # (which now installs immediately with the current preferred frame).
    c._document.frames = [Frame([s, existing.clone_slot()]) for s in conflicts()]
    c._document.sync_columns()
    c.current_frame = 4
    r = Recorder(c)
    r.take("before")
    window.refresh_ui()
    r.take("first")
    cells = dict(c._document.tracks[0].cells)
    window.refresh_ui()
    r.take("second")
    assert r.states[1]["graph"] == r.states[2]["graph"]
    assert all(c._document.tracks[0].cells[n] is cell for n, cell in cells.items())
    assert c.frames[0].layers[0].image == image("yellow")
    assert c.frames[0].layers[0].initial_image == image("blue")
    assert c.frames[0].layers[0].cell_name == "first"
    golden("refresh", r.states)


def test_layout_paper_refresh_keeps_existing_bank(window, monkeypatch):
    c = window.canvas
    install(c, [slot(1, "red", has_content=True),
                slot(2, "blue", has_content=True, sequence_only=True)])
    track = c._document.tracks[0]
    track.cells[9] = Cell(image("yellow"), image("magenta"), "bank-only")
    c._document.put_archive_hint(track, 9, ArchiveHint(False, True, 3, False,
                                                    {"points": [4]}, "hint"))
    monkeypatch.setattr(layout_paper, "load_image", lambda _: image("cyan"))
    r = Recorder(c)
    r.take("before")
    window.insert_layout_paper({"id": "test"})
    assert c._document.tracks[1] is track
    r.roundtrip()
    golden("layout", r.states)


@pytest.mark.parametrize("detached", [False, True], ids=["connected", "raw-compatibility"])
def test_docundo_restores_connected_and_detached_graphs(window, detached):
    c = window.canvas
    doc = Document()
    doc.current_frame = 4
    if detached:
        # Internal raw snapshot compatibility; never install this snapshot itself.
        doc.frames = [Frame([s]) for s in conflicts()]
        doc.tracks = [doc.frames[0].layers[0]._track]
    else:
        doc.install_legacy_frames([Frame([s]) for s in conflicts()])
        doc.tracks[0].cells[9] = Cell(image("cyan"), image("black"), "snapshot-bank")
        doc.put_archive_hint(doc.tracks[0], 9, ArchiveHint(exposure=3))
    snapshot = doc.snapshot()._replace(width=13, height=15)
    snapshot_view = SimpleNamespace(
        _document=snapshot, frames=snapshot.frames, current_frame=snapshot.current_frame,
        active_layer_index=snapshot.active_layer_index, timeline_mode="sheet",
        undo_stack=[], redo_stack=[])
    frozen = graph(snapshot_view, size=[snapshot.width, snapshot.height])
    r = Recorder(c)
    r.take("before")
    c.undo_stack.append(DocUndo(snapshot))
    c.undo()
    r.take("undo")
    c.redo()
    r.take("redo")
    c.undo()
    r.take("reuse")
    assert c.frames[0].layers[0].image == image("yellow")
    assert graph(snapshot_view, size=[snapshot.width, snapshot.height]) == frozen
    assert snapshot.frames[0].layers[0]._track.connected is not detached
    assert snapshot.frames[0].layers[0].image == image("red" if detached else "yellow")
    golden(f"docundo/{detached}", r.states)


@pytest.mark.parametrize("kind", [{}, {"is_draft": True}, {"is_paper": True}],
                         ids=["normal", "draft", "paper"])
@pytest.mark.parametrize("route", ["tiles", "region", "full"])
def test_edit_held_number_preserves_signals_and_history(window, kind, route):
    c = window.canvas
    held(c, kind)
    r = Recorder(c)
    r.take("before")
    paint(c, route)
    assert len(c.undo_stack) == 1
    assert c.current_frame == 0
    assert not any(c.frames[i].layers[0].has_content for i in (4, 5))
    r.roundtrip()
    golden(f"held/{route}/{next(iter(kind), 'normal')}", r.states)


def test_selection_commit_shared_number_signal_order(window):
    c = window.canvas
    install(c, [slot(1, "red", has_content=True), slot(),
                slot(2, "green", has_content=True), slot(1, "red", has_content=True)])
    c.set_transform_quality(False)
    c.selection_polygon = [QPointF(2, 2), QPointF(9, 2), QPointF(9, 9), QPointF(2, 9)]
    r = Recorder(c)
    r.take("before")
    assert c.begin_selection_transform("free")
    c.rotate_selection_transform(30)
    r.take("preview")
    c.commit_selection_transform(True)
    assert len(c.undo_stack) == 1
    assert len(c.undo_stack[0].cells) == 2
    r.roundtrip()
    golden("selection", r.states)


@pytest.mark.parametrize("dirty", [False, True])
@pytest.mark.parametrize("tool,color", [("brush", "blue"), ("brush", "white"),
                                       ("eraser", "blue")])
def test_cell_changed_preserves_timeline_and_color_conditions(window, monkeypatch,
                                                             dirty, tool, color):
    c = window.canvas
    held(c)
    c.current_frame = 0
    calls = []
    monkeypatch.setattr(window.timeline, "update_cell",
                        lambda frames, fi, li: calls.append(["timeline", fi, li,
                                                            frames is c.frames]))
    monkeypatch.setattr(window.used_color, "schedule_refresh", lambda: calls.append(["color"]))
    monkeypatch.setattr(c, "effective_tool", lambda: tool)
    monkeypatch.setattr(c, "paint_source_color", lambda: QColor(color))
    c.active_layer.image = image("blue")
    c._cell_structure_dirty = dirty
    r = Recorder(c)
    r.take("before")
    c.cellChanged.emit(0, 0)
    r.take("after")
    assert c._cell_structure_dirty is True
    golden(f"cell_changed/{dirty}/{tool}/{color}", {"states": r.states, "calls": calls})


def legacy_file(path):
    frames = [Frame([s]) for s in conflicts() + [slot(None, "cyan", has_content=True)]]
    metadata = {"format": "PaintMaskAnimatorProject", "format_version": 1,
                "mask_format": 2, "canvas": {"width": 16, "height": 16},
                "frames": [], "current_frame": 4, "active_layer_index": 0}
    archive = {(0, 9): slot(9, "magenta", has_content=True, cell_name="archived")}
    project_io.write_project_archive(path, metadata, frames, archive)


def test_open_legacy_conflicts_before_first_edit(window, tmp_path):
    c = window.canvas
    path = tmp_path / "legacy.pman"
    legacy_file(path)
    r = Recorder(c)
    r.take("before")
    assert window.project.open(path)
    r.take("open")
    assert c.frames[6].layers[0].sequence_number == 13
    assert c.active_layer.image == image("yellow")
    paint(c)
    r.take("paint")
    c.undo()
    r.take("undo")
    golden("open", r.states)


@pytest.mark.parametrize("draft", [False, True])
def test_import_sequence_installs_before_changed(window, tmp_path, draft):
    c = window.canvas
    paths = []
    for index, color in enumerate(("blue", "green")):
        path = tmp_path / f"input{index}.png"
        assert image(color).save(str(path))
        paths.append(path)
    r = Recorder(c)
    r.take("before")
    assert c.import_image_sequence(paths, layer_name="import", draft=draft) == (True, "")
    assert len(c.undo_stack) == 1
    r.roundtrip()
    golden(f"import/{draft}", r.states)


def parsed_clip():
    source = ClipAnimationLayer("clip", (ClipCelKey(0, "A-7"),),
                                {"A-7": image("green"), "B-8": image("magenta")})
    frames = build_pma_frames(16, 16, 0, 1, (source,))
    frames[0].layers[0].initial_image = image("blue")
    return ClipAnimationDocument("sample.clip", "Main", 16, 16, 24.0, 0, 1,
                                 (source,), frames)


@pytest.mark.parametrize("failure", [False, True], ids=["success", "rollback"])
def test_clip_apply_installs_placed_and_unplaced_before_refresh(window, monkeypatch, failure):
    c = window.canvas
    r = Recorder(c)
    r.take("before")
    old_doc, old_frames, old_track = c._document, c.frames, c._document.tracks[0]
    observed = []
    refresh = window.refresh_ui

    def inspect_refresh():
        observed.append(graph(c))
        if failure and len(observed) == 1:
            raise RuntimeError("synthetic apply failure")
        refresh()

    monkeypatch.setattr(window, "refresh_ui", inspect_refresh)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *_: None))
    ok = window.importer._replace_document(parsed_clip(), title="CLIP",
                                           source_metadata={"source": "fixed"})
    assert ok is not failure
    r.take("apply")
    if failure:
        assert c._document is old_doc and c.frames is old_frames
        assert c._document.tracks[0] is old_track
    else:
        paint(c)
        r.take("paint")
        c.undo()
        r.take("undo")
    golden(f"clip/{failure}", {"states": r.states, "before_refresh": observed})


@pytest.mark.parametrize("operation,preferred", [("move", 1), ("paste", 0), ("paste", 1)])
def test_timeline_paste_collision_registers_before_notification(window, operation, preferred):
    from paintmaskanimator.document import create_drawing

    c = window.canvas
    install(c, [slot(1, "red", has_content=True, initial_image=image("blue"),
                     cell_name="source"), slot(2, "green", has_content=True)])
    c.add_layer()
    target = c.frames[0].layers[1]
    create_drawing(target, image("yellow"), number=1)
    target.cell_name = "destination-first"
    target.initial_image = image("cyan")
    c.current_frame = preferred
    c.undo_stack.clear()
    c.redo_stack.clear()
    r = Recorder(c)
    r.take("before")
    if operation == "move":
        assert c.move_timeline_cell(0, 0, 1, 1)
    else:
        assert window.timeline_ops.copy_cell_to_clipboard(1, 0)
        assert window.timeline_ops.paste_cell_from_clipboard(0, 1)
    r.roundtrip()
    golden(f"collision/{operation}/{preferred}", r.states)


def test_image_clipboard_edits_registered_cell_once(window):
    c = window.canvas
    held(c)
    clipboard = QApplication.clipboard()
    clipboard.setImage(image("blue", 3, 2))
    try:
        r = Recorder(c)
        r.take("before")
        c.paste_clipboard()
        assert len(c.undo_stack) == 1 and c.current_frame == 0
        r.roundtrip()
        golden("clipboard", r.states)
    finally:
        clipboard.clear()


def payload(path):
    with ZipFile(path) as archive:
        metadata = json.loads(archive.read("project.json"))
        pngs = {name: archive.read(name) for name in archive.namelist()
                if name.endswith(".png") and not name.startswith("thumbnail")}
    return {"frames": metadata["frames"], "archive": metadata["sequence_archive"],
            "pngs": {name: pixels(QImage.fromData(data)) for name, data in sorted(pngs.items())}}, pngs


def test_removal_paths_keep_v1_payload_and_reload_behavior(window, tmp_path, monkeypatch):
    c = window.canvas
    r = Recorder(c)
    r.take("before")
    path = tmp_path / "legacy.pman"
    legacy_file(path)
    assert window.project.open(path)
    r.take("open")
    paint(c)
    r.take("paint")
    c.push_doc_undo()
    # Remove the old sparse key's range, retaining a later same-number key.
    saved_key = project_v1_state(c.frames)[0][c.current_frame].layers[c.active_layer_index]
    for index in range(c.current_frame, min(len(c.frames), c.current_frame + saved_key.exposure)):
        clear_slot(c.frames[index].layers[c.active_layer_index])
    c.undo()
    r.take("docundo")
    # Remove all content keys: reload deliberately exercises existing number normalization.
    for f in c.frames:
        clear_slot(f.layers[0])
    r.take("unplaced")
    normal = tmp_path / "normal.pman"
    assert window.project.write(normal)
    expected, png_bytes = payload(normal)
    # Independent raw DTO: fixed colors/attributes, with no live projection involved.
    raw_archive = {
        (0, 1): slot(1, "yellow", has_content=True, initial_image=image("blue"),
                     cell_name="first"),
        (0, 8): slot(8, "magenta", has_content=True),
        (0, 9): slot(9, "magenta", has_content=True, cell_name="archived"),
        (0, 12): slot(12, "green", has_content=True),
        (0, 13): slot(13, "cyan", has_content=True),
    }
    raw_archive[0, 1].image.setPixelColor(5, 5, QColor("blue"))
    # open adds one trailing empty frame; clear_slot preserves local initial images.
    raw_frames = [Frame([slot(initial_image=image("blue") if i in (0, 2, 4) else None)])
                  for i in range(8)]
    frames, archive, pngs = project_io.build_v1_payload(
        [Frame([snapshot_layer(s) for s in f.layers], f.duration) for f in raw_frames],
        {key: snapshot_layer(s) for key, s in raw_archive.items()})
    independent = {"frames": frames, "archive": archive,
                   "pngs": {name: pixels(img) for name, img, _ in pngs}}
    golden("save/independent", independent)
    # This equality is checked in addition to the captured payload.
    assert expected == independent
    saved = {"normal": expected}
    for mode in ("sync", "background"):
        destination = tmp_path / f"{mode}.pman"
        monkeypatch.setattr(window.autosave, "path", lambda destination=destination: destination)
        try:
            if mode == "sync":
                window.autosave.save()
            else:
                window.autosave.request_save()
                window.autosave._pending.result(timeout=10)
            saved[mode], encoded = payload(destination)
            assert encoded == png_bytes
            assert saved[mode] == expected
        finally:
            window.autosave.clear()
    r.take("saved")
    assert not window.project.has_unsaved_changes()
    assert window.project.open(normal)
    r.take("reload")
    assert not window.project.has_unsaved_changes()
    golden("save", {"states": r.states, "payloads": saved})
