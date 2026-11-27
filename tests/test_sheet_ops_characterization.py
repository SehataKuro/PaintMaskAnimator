"""Public notifications, history, and v2 projection for sheet edits."""
import json
from zipfile import ZipFile

import pytest

from paintmaskanimator.models import Frame, Layer
from paintmaskanimator.undo_entries import DocUndo
from paintmaskanimator.utils import blank_image
from test_project_boundaries import window as _window

window = _window


@pytest.mark.parametrize("operation,sheet,cursor,signals,bank_number", [
    ("blank", [7, "BLANK"], 1, "changed selection update", 7),
    ("duplicate", [7, 7, None], 1, "changed selection update", 7),
    ("delete", [None], 0, "changed selection update", 7),
    ("next", [7, None, None], 2, "changed selection update", 7),
    ("duration", [7, 7], 0, "changed", 7),
    ("insert-frame", [7, None, None], 0, "changed selection update refresh", 7),
    ("clear", [None, None], 0, "changed selection update refresh", 7),
    ("paste", [7, 7], 0, "changed selection update refresh", 7),
    ("copy", [7, 7], 1, "changed selection update", 7),
    ("recall", [7, 7], 1, "changed selection update", 7),
    ("move", [None, 7], 1, "changed selection update", 7),
    ("normalize", [1, None], 0, "changed selection update", 1),
    ("delete-number", [None], 0, "changed selection update", None),
])
def test_sheet_command(window, tmp_path, monkeypatch, operation, sheet, cursor, signals, bank_number):
    canvas = window.canvas
    canvas.frames = [Frame([Layer("A", blank_image(), has_content=True, sequence_number=7)]),
                     Frame([Layer("A", blank_image())])]
    canvas.current_frame = 1 if operation == "next" else 0
    canvas.undo_stack.clear()
    canvas.changed.disconnect()
    canvas.selectionChanged.disconnect()
    monkeypatch.setattr(window.used_color, "refresh_with_counter", lambda *_: None)
    monkeypatch.setattr(window.used_color, "_refresh_without_delay", lambda: None)
    events = []
    canvas.changed.connect(lambda: events.append("changed"))
    canvas.selectionChanged.connect(lambda: events.append("selection"))
    canvas.cellChanged.connect(lambda *_: events.append("cell"))
    monkeypatch.setattr(canvas, "update", lambda: events.append("update"))
    monkeypatch.setattr(window, "refresh_ui", lambda: events.append("refresh"))

    path = tmp_path / "frame.pman"

    def saved():
        assert window.project.write(path)
        with ZipFile(path) as archive:
            track = json.loads(archive.read("project.json"))["tracks"][0]
            return track["sheet"], [(cell["number"], archive.read(cell["image"]))
                                    for cell in track["cells"]]

    before, bank = saved()
    events.clear()
    ops = window.timeline_ops
    if operation == "paste":
        assert ops.copy_cell_to_clipboard(0, 0)
        events.clear()
    {
        "blank": lambda: canvas.add_frame(False),
        "duplicate": lambda: canvas.add_frame(True),
        "delete": canvas.delete_frame,
        "next": canvas.next_frame,
        "duration": lambda: canvas.set_duration(2),
        "insert-frame": lambda: ops.insert_frame_at(0, 1),
        "clear": lambda: ops.clear_cell(0, 0),
        "paste": lambda: ops.paste_cell_from_clipboard(0, 1),
        "copy": lambda: ops.copy_cell(0, 0, 0, 1),
        "recall": lambda: ops.recall_sequence_number(0, 1, 7),
        "move": lambda: canvas.move_timeline_cell(0, 0, 1, 0),
        "normalize": lambda: ops.apply_normalize_numbers([0]),
        "delete-number": lambda: canvas.delete_sequence_entry(0, 7),
    }[operation]()
    assert events == signals.split()
    assert canvas.current_frame == cursor
    after_bank = [] if bank_number is None else [(bank_number, bank[0][1])]
    assert saved() == (sheet, after_bank)
    if operation in {"next", "duration"}:
        assert not canvas.undo_stack
    else:
        assert len(canvas.undo_stack) == 1
        assert isinstance(canvas.undo_stack[-1], DocUndo)
        canvas.undo()
        assert saved() == (before, bank)
        canvas.redo()
        assert saved() == (sheet, after_bank)
