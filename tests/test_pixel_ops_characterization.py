"""Pixel commits preserve notification order, history, and saved drawing bytes."""
import json
from zipfile import ZipFile

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator.undo_entries import LayerRegionUndo, LayerTilesUndo, history_label_for
from test_brush_commit import mouse, window as _window
from test_bucket_all_frames import outlined_cell

window = _window


@pytest.mark.parametrize("operation, entry_type", [
    ("brush", LayerTilesUndo), ("paste", LayerRegionUndo), ("bucket", LayerRegionUndo),
])
def test_pixel_commit(window, monkeypatch, tmp_path, operation, entry_type):
    canvas = window.canvas
    outlined_cell(window, 0)
    if operation == "bucket":
        canvas.set_tool("bucket")
    elif operation == "paste":
        patch = QImage(3, 3, QImage.Format.Format_ARGB32_Premultiplied)
        patch.fill(QColor(255, 0, 0, 128))
        QApplication.clipboard().setImage(patch)
    events = []
    for signal, name in ((canvas.changed, "changed"), (canvas.selectionChanged, "selection"),
                         (canvas.cellChanged, "cell"), (canvas.colorUsed, "color")):
        signal.disconnect()
        signal.connect(lambda *args, name=name: events.append((name, *args)))
    monkeypatch.setattr(canvas, "update", lambda *args: events.append(("region-update" if args else "update",)))

    path = tmp_path / "pixels.pman"

    def saved():
        assert window.project.write(path)
        with ZipFile(path) as archive:
            track = json.loads(archive.read("project.json"))["tracks"][0]
            return track["sheet"], [(cell["number"], archive.read(cell["image"]))
                                    for cell in track["cells"]]

    original = saved()
    events.clear()
    if operation == "brush":
        mouse(canvas, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton)
        assert canvas.active_layer.image.read_qimage(20, 20, 1, 1).pixelColor(0, 0) == QColor("white")
        events.clear()
        mouse(canvas, QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)
    elif operation == "paste":
        canvas.paste_clipboard()
    else:
        canvas.flood_fill(QPoint(8, 8))
    expected = [("cell", 0, 0), ("update",)]
    if operation != "paste":
        expected.insert(0, ("color", QColor("blue")))
    if operation == "brush":
        expected.insert(0, ("region-update",))
        expected.append(("update",))
    assert events == expected
    assert (canvas.current_frame, canvas.active_layer_index) == (0, 0)
    assert len(canvas.undo_stack) == 1 and not canvas.redo_stack
    entry = canvas.undo_stack[-1]
    assert isinstance(entry, entry_type)
    assert history_label_for(entry) == "描画（A の 1番）"
    final = saved()
    assert final[0] == original[0]
    assert final[1][0][0] == original[1][0][0] == 1
    assert final[1][0][1] != original[1][0][1]

    for revert, snapshot, history in ((canvas.undo, original, canvas.redo_stack),
                                      (canvas.redo, final, canvas.undo_stack)):
        events.clear()
        revert()
        assert events == [("cell", 0, 0), ("selection",), ("update",)]
        assert (canvas.current_frame, canvas.active_layer_index) == (0, 0)
        assert len(history) == 1
        assert history_label_for(history[-1]) == history_label_for(entry)
        assert history[-1].nbytes == entry.nbytes
        assert saved() == snapshot
    assert not canvas.redo_stack
