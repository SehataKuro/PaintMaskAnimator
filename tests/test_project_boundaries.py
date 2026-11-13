"""Save ownership and failure-before-publication contracts for Phase 5-6 B."""
import json
from dataclasses import asdict
import os
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from paintmaskanimator import constants, project_io
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.models import ArchiveHint
from paintmaskanimator.utils import remove_app_event_filter
from p5_6_support import CASES, ROOT, pixels


def entries(path):
    with ZipFile(path) as archive:
        return [(name, archive.read(name)) for name in archive.namelist()]


def save_metadata(loaded):
    metadata = dict(loaded.metadata)
    metadata["frames"] = []
    return metadata


def graph_state(doc):
    """Include state that the v1 payload intentionally omits (notably hints)."""
    return {
        "cursor": (doc.current_frame, doc.active_layer_index),
        "tracks": [{"hints": {n: asdict(h) for n, h in t.legacy_archive_hints.items()},
                    "cells": {n: (pixels(c.image), pixels(c.initial_image), c.cell_name)
                              for n, c in t.cells.items()}}
                   for t in doc.tracks],
        "slots": [[(s.sequence_number, s.sequence_only, s.blank_start,
                    pixels(s.image), pixels(s.initial_image), s.cell_name,
                    s.tween, s.tween_member) for s in f.layers] for f in doc.frames],
    }


@pytest.mark.parametrize("case", CASES)
def test_snapshot_v1_bytes_and_live_independence(tmp_path, monkeypatch, case):
    loaded = project_io.load_project(ROOT / "v1" / f"{case}.pman")
    monkeypatch.setattr(constants, "CANVAS_WIDTH", loaded.width)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", loaded.height)
    doc = loaded.document
    metadata = save_metadata(loaded)
    metadata["memo"] = "before"
    metadata["display"] = {"onion_skin": False}
    snapshot = project_io.ProjectSnapshot.capture(metadata, doc)
    expected_graph = json.dumps(graph_state(snapshot.document), sort_keys=True)
    assert expected_graph == json.dumps(graph_state(doc), sort_keys=True)
    for live_track, frozen_track in zip(doc.tracks, snapshot.document.tracks):
        assert not (set(live_track.pending_slots) & set(frozen_track.pending_slots))
        for number, cell in live_track.cells.items():
            assert frozen_track.cells[number].image is not cell.image
    baseline = tmp_path / "live.pman"
    project_io.legacy_fixture_write_archive(baseline, save_metadata(loaded) | {
        "memo": "before", "display": {"onion_skin": False}}, doc.frames)
    expected = entries(baseline)
    saved = tmp_path / "snapshot.pman"
    project_io.legacy_fixture_write_snapshot(saved, snapshot)
    assert entries(saved) == expected

    # Mutate both bank and live local artwork, not just references to images.
    for track in doc.tracks:
        track.visible = not track.visible
        track.color_filter_rgb = [3, 4, 5]
        for cell in track.cells.values():
            cell.image.fill(QColor("blue"))
            if cell.initial_image is not None:
                cell.initial_image.fill(QColor("green"))
            cell.initial_image = QImage(cell.image)
            cell.cell_name = "after"
        for hint in track.legacy_archive_hints.values():
            if hint.tween is not None:
                hint.tween["snapshot_mutation"] = [1, 2]
        track.legacy_archive_hints.clear()
        track.legacy_archive_hints[999] = ArchiveHint(tween={"nested": [1]})
    for frame in doc.frames:
        frame.duration += 1
        for slot in frame.layers:
            slot.image.fill(QColor("red"))
            if slot.initial_image is not None:
                slot.initial_image.fill(QColor("yellow"))
            slot.cell_name = "local after"
            slot.set_pending(not slot.sequence_only)
            slot.sequence_number = 999
            slot.blank_start = not slot.blank_start
            slot.tween = {"nested": [2]}
    doc.current_frame = 0
    doc.active_layer_index = 0
    metadata["memo"] = "after"
    metadata["display"]["onion_skin"] = True
    metadata["frames"].append({"after": True})
    project_io.legacy_fixture_write_snapshot(saved, snapshot)
    assert entries(saved) == expected
    assert json.dumps(graph_state(snapshot.document), sort_keys=True) == expected_graph
    # A repeated write must not extend the snapshot's metadata frames.
    assert snapshot.metadata["frames"] == []
    assert snapshot.document is not doc
    assert snapshot.document.tracks[0] is not doc.tracks[0]


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 4)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 3)
    w = MainWindow()
    for timer in (w._autosave_timer, w._used_color_timer, w._title_timer):
        timer.stop()
    yield w
    w._closing = True
    remove_app_event_filter(w)
    remove_app_event_filter(w.subview)
    w.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("changes", [
    {"fps": "invalid"}, {"display": {"onion_next_scale": "invalid"}},
    {"colors": []}, {"current_frame": None}, {"active_layer_index": []},
    {"format_version": 2},
])
def test_failed_load_preserves_document_path_and_history(window, tmp_path, monkeypatch, changes):
    source = ROOT / "v1" / "tween-initial.pman"
    content = dict(entries(source))
    metadata = json.loads(content["project.json"])
    metadata.update(changes)
    content["project.json"] = json.dumps(metadata).encode()
    broken = tmp_path / "broken.pman"
    with ZipFile(broken, "w") as archive:
        for name, data in content.items():
            archive.writestr(name, data)
    window.current_project_path = tmp_path / "original.pman"
    window.created_app_version = "original"
    window.set_project_memo("original memo")
    window.canvas.undo_stack.append(object())
    window.canvas.redo_stack.append(object())
    window.canvas.history_branches.append(object())
    window.png_cache.remember(window.canvas.active_layer.image, b"original")
    document = window.canvas._document
    undo, redo = list(window.canvas.undo_stack), list(window.canvas.redo_stack)
    branches = list(window.canvas.history_branches)
    signature = window.project._content_signature()
    saved_signature = window.project._saved_signature
    cache = dict(window.png_cache._entries)
    dimensions = constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args))
    assert not window.project.open(broken)
    assert errors
    assert window.canvas._document is document
    assert window.current_project_path == tmp_path / "original.pman"
    assert window.created_app_version == "original"
    assert window.project_memo == "original memo"
    assert window.canvas.undo_stack == undo
    assert window.canvas.redo_stack == redo
    assert window.canvas.history_branches == branches
    assert window.project._content_signature() == signature
    assert window.project._saved_signature == saved_signature
    assert window.png_cache._entries == cache
    assert (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT) == dimensions
