"""C1: observations captured from the unchanged pre-C1 implementation."""
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator import constants, project_io
from paintmaskanimator.document import Document, project_v1_state
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.models import Cell, Frame
from paintmaskanimator.legacy_v1 import raw_v1_slot as Layer


BASELINE = Path(__file__).with_name("p5_2c_boundary_expected.json")


def image(color):
    result = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(color))
    return result


def layer(number=None, **kwargs):
    return Layer("A", image("red"), sequence_number=number, **kwargs)


def projection_observation():
    doc = Document()
    slots = [layer(1, has_content=True, initial_image=image("blue"), cell_name="placed"),
             layer(2, has_content=True, sequence_only=True),
             layer(2, has_content=True, sequence_only=True),
             layer(sequence_only=True), layer(12, is_blank_key=True)]
    doc.install_legacy_frames([Frame([slot]) for slot in slots], {
        (0, 1): layer(1, has_content=False, is_blank_key=True),
        (0, 3): layer(3, has_content=False, is_blank_key=True,
                      initial_image=image("green"), cell_name="false archive"),
    })
    doc.tracks[0].cells[9] = Cell(image("yellow"), image("magenta"), "bank only")
    frames, archive = project_v1_state(doc.frames)
    frame_data, archive_data, pngs = project_io.build_v1_payload(frames, archive)
    return {"frames": frame_data, "archive": archive_data,
            "pngs": [[name, img.width(), img.height(), img.pixelColor(0, 0).name()]
                     for name, img, _ in pngs]}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 4)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 4)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = MainWindow()
    yield result
    result.deleteLater()
    qapp.sendPostedEvents()


def mode_observation(window, case):
    exposure = {"even": 4, "odd": 5, "one": 1}.get(case, 4)
    slots = [layer(1, has_content=True, exposure=exposure), layer(), layer(),
             layer(), layer(), layer(2, has_content=True, sequence_only=True)]
    if case == "missing":
        slots[0].sequence_number = 8
    if case == "collision":
        slots[2] = layer(7, has_content=True)
    if case == "none":
        slots[5] = layer(sequence_only=True)
    if case == "duplicate":
        slots[4] = layer(2, has_content=True, sequence_only=True)
    canvas = window.canvas
    canvas.install_legacy_frames([Frame([slot]) for slot in slots], {})
    canvas.frames[0].layers[0]._track.cells[9] = Cell(image("yellow"))
    canvas.timeline_mode = "sequence"
    canvas.current_frame = 5
    before = len(canvas.undo_stack)
    window.timeline_ops.set_mode("sheet")
    return {"cursor": canvas.current_frame, "undo": len(canvas.undo_stack) - before,
            "mode": canvas.timeline_mode,
            "slots": [[s.sequence_number, s.has_content, s.is_blank_key,
                       s.exposure, s.sequence_only] for f in project_v1_state(canvas.frames)[0] for s in f.layers]}


def import_observation(window, tmp_path):
    slots = [layer(1, has_content=True), layer(1, has_content=True),
             layer(4, has_content=True), layer()]
    # Explicit duplicate and false archive; bank-only is deliberately unmatched.
    canvas = window.canvas
    canvas.install_legacy_frames([Frame([s]) for s in slots], {
        (0, 1): layer(1, has_content=True),
        (0, 2): layer(2, has_content=True),
        (0, 3): layer(3, has_content=False, is_blank_key=True),
    })
    track = canvas.frames[0].layers[0]._track
    track.cells[9] = Cell(image("yellow"))
    folder = tmp_path / "A"
    folder.mkdir(exist_ok=True)
    for number in (1, 2, 3, 9):
        assert image("black").save(str(folder / f"A{number}.png"))
    count = window.initial_state.import_line_art(tmp_path)
    return {"count": count, "message": window.statusBar().currentMessage(),
            "targets": sorted(n for n, c in track.cells.items() if c.initial_image is not None)}


def color_resize_observation(window, monkeypatch):
    canvas = window.canvas
    canvas.install_legacy_frames([Frame([layer(1, has_content=True)]),
                                  Frame([Layer("A", image("blue"), has_content=True,
                                               sequence_number=2, sequence_only=True)])], {
        (0, 3): Layer("A", image("green"), has_content=True, sequence_number=3),
    })
    track = canvas.frames[0].layers[0]._track
    track.cells[9] = Cell(image("yellow"), image("magenta"), "bank")
    colors = []
    monkeypatch.setattr(window.used_color, "_apply_result", lambda values, exceeded=False: colors.extend(values))
    window.used_color.refresh()
    window.replace_doc(6, 7, preserve=True)
    track = canvas.frames[0].layers[0]._track
    return {"colors": [list(c) for c in colors],
            "sizes": {str(n): [c.image.width(), c.image.height(),
                               None if c.initial_image is None else [c.initial_image.width(), c.initial_image.height()],
                               c.cell_name] for n, c in sorted(track.cells.items())}}


def expected():
    return json.loads(BASELINE.read_text())


def test_projection():
    assert projection_observation() == expected()["projection"]


@pytest.mark.parametrize("case", ["even", "odd", "one", "missing", "collision", "none", "duplicate"])
def test_mode(window, case):
    assert mode_observation(window, case) == expected()["mode"][case]


def test_initial_import(window, tmp_path):
    assert import_observation(window, tmp_path) == expected()["import"]


def test_color_resize(window, monkeypatch):
    assert color_resize_observation(window, monkeypatch) == expected()["color_resize"]


def test_read_boundary_preserves_positions_and_does_not_create_drawings():
    from dataclasses import fields
    from paintmaskanimator.document import ArchiveHint, drawing, is_pending

    doc = Document()
    slots = [layer(1, has_content=True), layer(2, has_content=True, sequence_only=True),
             layer(2, sequence_only=True), layer(sequence_only=True),
             layer(12, is_blank_key=True)]
    archived = layer(1, has_content=False, is_blank_key=True, exposure=5,
                     tween={"nested": [1]}, tween_member="member", sequence_only=True)
    doc.install_legacy_frames([Frame([s]) for s in slots], {(0, 1): archived})
    track = doc.tracks[0]
    track.cells[9] = Cell(image("transparent"))
    before = dict(track.cells)
    assert drawing(track, 99) is None
    assert drawing(track, 1) is track.cells[1]
    assert doc.placed_numbers(track) == {1}
    assert doc.unplaced_numbers(track) == {2, 9, 12}
    assert [(entry.column, entry.slot.sequence_number) for entry in doc.pending_entries(track)] == [
        (1, 2), (2, 2), (3, None)]
    assert all(is_pending(entry.slot) for entry in doc.pending_entries(track))
    assert set(doc.archive_hints(track)) == {1}
    hint = doc.legacy_drawing_state(track, 1)
    assert hint == ArchiveHint(False, True, 5, True, {"nested": [1]}, "member")
    hint.tween["nested"].append(2)
    assert archived.tween == {"nested": [1]}
    assert doc.legacy_drawing_state(track, 9) == ArchiveHint()
    assert doc.legacy_drawing_state(track, 99) == ArchiveHint()
    assert dict(track.cells) == before
    assert [f.name for f in fields(ArchiveHint)] == [
        "has_content", "is_blank_key", "exposure", "sequence_only", "tween", "tween_member"]
