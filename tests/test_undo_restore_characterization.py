"""Signal order and round trips for structural document history."""
import json
from zipfile import ZipFile

import pytest
from PySide6.QtGui import QColor

from paintmaskanimator import main_window_scope_ops
from paintmaskanimator.frame_scope import FrameScope
from paintmaskanimator.undo_entries import ExposureUndo, ScopedBatchUndo, history_label_for
from test_undo_batch_by_number import repeated
from test_undo_unit_characterization import put, window as _window, qapp as _qapp

window = _window
qapp = _qapp


def saved_drawings(window, path):
    assert window.project.write(path)
    with ZipFile(path) as archive:
        tracks = json.loads(archive.read("project.json"))["tracks"]
        return [(track["sheet"], [(cell["number"], archive.read(cell["image"]))
                                  for cell in track["cells"]]) for track in tracks]


@pytest.mark.parametrize("kind", ["document", "exposure", "column"])
def test_structural_restore(window, monkeypatch, tmp_path, kind):
    canvas = window.canvas
    put(canvas, 0, 3)
    events = []
    for signal, name in ((canvas.changed, "changed"), (canvas.selectionChanged, "selection"),
                         (canvas.cellChanged, "cell")):
        signal.disconnect()
        signal.connect(lambda *args, name=name: events.append((name, *args)))
    monkeypatch.setattr(canvas, "update", lambda: events.append(("update",)))

    def state():
        return (len(canvas.frames), tuple(layer.name for layer in canvas.layers),
                canvas.current_frame, canvas.active_layer_index,
                tuple(canvas.frames[0].layers[0].image.rgba()[5, 5]),
                saved_drawings(window, tmp_path / "restore.pman"))

    before = state()
    if kind == "document":
        canvas.push_doc_undo()
        canvas._ensure_frame_count(5)
        expected = [("changed",), ("update",)]
    elif kind == "exposure":
        canvas.push_undo(ExposureUndo(0, 0, 4, 4, 0, 0))
        canvas._ensure_frame_count(5)
        expected = [("update",), ("changed",), ("selection",), ("update",)]
    else:
        canvas.add_layer()
        expected = [("changed",), ("selection",), ("update",)]
    after = state()
    assert after != before
    label = history_label_for(canvas.undo_stack[-1])
    for step, snapshot, stack in ((canvas.undo, before, canvas.redo_stack),
                                  (canvas.redo, after, canvas.undo_stack)):
        events.clear()
        step()
        assert events == expected
        assert state() == snapshot
        assert len(stack) == 1
        assert history_label_for(stack[-1]) == label
    assert not canvas.redo_stack


def test_numbered_scope_restore(window, monkeypatch, tmp_path):
    canvas = window.canvas
    repeated(canvas)
    events = []
    for signal, name in ((canvas.changed, "changed"), (canvas.selectionChanged, "selection"),
                         (canvas.cellChanged, "cell")):
        signal.disconnect()
        signal.connect(lambda *args, name=name: events.append((name, *args)))
    monkeypatch.setattr(canvas, "update", lambda: events.append(("update",)))
    monkeypatch.setattr(main_window_scope_ops, "create_counter", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_window_scope_ops, "update_counter", lambda *args: None)
    monkeypatch.setattr(main_window_scope_ops, "close_counter", lambda *args: None)

    def paint(context):
        image = context.image.copy()
        image.fill(QColor("blue"))
        return image

    path = tmp_path / "scope.pman"
    before = saved_drawings(window, path)
    events.clear()
    result = window.scope.run_over(FrameScope((0, 2, 3), (0,), key_frames_only=False), paint, label="batch")
    assert isinstance(result.undo, ScopedBatchUndo)
    assert len(result.undo.cells) == 1 and result.undo.cells[0][-1] == 3
    cells = [("cell", i, 0) for i in (0, 2, 3)]
    assert events == cells + [("changed",), ("update",)]
    after = saved_drawings(window, path)
    assert after != before and after[0][0] == before[0][0]
    for step, snapshot, stack in ((canvas.undo, before, canvas.redo_stack),
                                  (canvas.redo, after, canvas.undo_stack)):
        events.clear()
        step()
        assert events == cells + [("selection",), ("update",)]
        assert (canvas.current_frame, canvas.active_layer_index) == (0, 0)
        assert history_label_for(stack[-1]) == "batch"
        assert stack[-1].nbytes == result.undo.nbytes
        assert saved_drawings(window, path) == snapshot
    assert not canvas.redo_stack
