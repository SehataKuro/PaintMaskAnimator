"""Track edits preserve the legacy Qt, history and v2 save contracts."""

import json
from zipfile import ZipFile

import pytest
from PySide6.QtGui import QColor, QImage

from paintmaskanimator.models import Frame, Layer
from paintmaskanimator.undo_entries import DocUndo, LayerInsertUndo, LayerRemoveUndo
from test_project_boundaries import window as _window

window = _window


@pytest.mark.parametrize(
    "operation,names,active,history,signals",
    [
        ("add", ["A", "B", "C", "D"], 3, LayerRemoveUndo, "changed selection update"),
        ("delete", ["A", "C"], 0, LayerInsertUndo, "changed selection update"),
        ("duplicate", ["A", "A コピー", "B", "C", "C コピー"], 4, DocUndo,
         "changed selection update"),
        ("delete_rows", ["B"], 0, DocUndo, "changed selection update"),
        ("merge", ["A", "C"], 1, DocUndo, "changed selection update"),
        ("move", ["B", "C", "A"], 0, DocUndo, "changed selection update"),
        ("rename", ["A", "Ink", "C"], 1, DocUndo, "changed"),
        ("same_name", ["A", "B", "C"], 1, DocUndo, "changed"),
        ("same_visibility", ["A", "B", "C"], 1, None, ""),
        ("same_opacity", ["A", "B", "C"], 1, None, ""),
        ("draft", ["A", "B", "C"], 1, DocUndo, "changed selection update"),
        ("visibility", ["A", "B", "C"], 1, None, "update"),
        ("filter", ["A", "B", "C"], 1, None, "changed update"),
        ("clear_filter", ["A", "B", "C"], 1, None, "changed update"),
        ("opacity", ["A", "B", "C"], 1, None, "update"),
    ],
)
def test_track_edit_contract(window, tmp_path, monkeypatch, operation, names, active,
                             history, signals):
    canvas = window.canvas
    canvas.add_layer()
    canvas.add_layer()
    canvas._ensure_frame_count(2)
    for layer, name in zip(canvas.layers, ("A", "B", "C")):
        layer.name = name
    canvas.active_layer_index = 1
    if operation == "clear_filter":
        window.colors.isolate_selected_color(QColor(12, 34, 56))
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

    ops = window.layers
    actions = {
        "add": canvas.add_layer,
        "delete": canvas.delete_layer,
        "duplicate": lambda: ops.duplicate_rows([0, 2]),
        "delete_rows": lambda: ops.delete_rows([0, 2]),
        "merge": lambda: ops.merge_rows([0, 1]),
        "move": lambda: ops.move_row([2], 0),
        "rename": lambda: ops.rename_row(1, " Ink "),
        "same_name": lambda: ops.rename_row(1, " B "),
        "same_visibility": lambda: canvas.set_layer_visibility(1, True),
        "same_opacity": lambda: canvas.set_layer_opacity(1, 1.0),
        "draft": lambda: ops.set_draft_rows([0, 2]),
        "visibility": lambda: canvas.set_layer_visibility(1, False),
        "filter": lambda: window.colors.isolate_selected_color(QColor(12, 34, 56)),
        "clear_filter": window.colors.clear_selected_color_filter,
        "opacity": lambda: canvas.set_layer_opacity(1, 0.25),
    }
    actions[operation]()
    assert events == signals.split()
    assert canvas.active_layer_index == active
    assert len(canvas.undo_stack) == int(history is not None)
    if history is not None:
        entry = canvas.undo_stack[-1]
        assert type(entry) is history
        if history is DocUndo:
            assert [layer.name for layer in entry.snapshot.frames[0].layers] == ["A", "B", "C"]
            assert entry.snapshot.active_layer_index == 1
        elif history is LayerRemoveUndo:
            assert (entry.index, entry.target_active) == (3, 1)
        else:
            assert (entry.index, entry.target_active) == (1, 0)
            assert [layer.name for layer in entry.layers] == ["B", "B"]

    path = tmp_path / "track-edit.pman"
    assert window.project.write(path)
    with ZipFile(path) as archive:
        tracks = json.loads(archive.read("project.json"))["tracks"]
    assert [track["name"] for track in tracks] == names
    assert [track["is_draft"] for track in tracks] == [
        operation == "draft" and index in (0, 2) for index in range(len(names))
    ]
    assert [track["visible"] for track in tracks] == [
        operation != "visibility" or index != 1 for index in range(len(names))
    ]
    assert [track["opacity"] for track in tracks] == [
        0.25 if operation == "opacity" and index == 1 else 1.0
        for index in range(len(names))
    ]
    assert [track["sheet"] for track in tracks] == [[None, None]] * len(names)
    assert [track["color_filter_enabled"] for track in tracks] == [
        operation == "filter" and index == 1 for index in range(len(names))
    ]
    assert [track["color_filter_rgb"] for track in tracks] == [
        [12, 34, 56] if operation == "filter" and index == 1 else None
        for index in range(len(names))
    ]
    if history is not None:
        canvas.undo()
        assert [slot.name for slot in canvas.layers] == ["A", "B", "C"]
        assert all(not slot.is_draft for slot in canvas.layers)
        canvas.redo()
        assert [slot.name for slot in canvas.layers] == names
        assert canvas.active_layer_index == active
        assert [slot.is_draft for slot in canvas.layers] == [
            track["is_draft"] for track in tracks
        ]


@pytest.mark.parametrize("operation,expected", [
    ("duplicate", [("Ink", "#ff0000"), ("Ink コピー", "#ff0000"), ("Paint", "#0000ff")]),
    ("merge", [("Paint", "#0000ff")]),
])
def test_track_edits_preserve_saved_artwork(window, tmp_path, monkeypatch, operation, expected):
    canvas = window.canvas
    slots = []
    for name, color in (("Ink", "red"), ("Paint", "blue")):
        image = QImage(4, 3, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(color))
        slots.append(Layer(name, image, has_content=True, sequence_number=1))
    canvas.frames = [Frame(slots)]
    canvas.active_layer_index = 0
    monkeypatch.setattr(window.used_color, "refresh_with_counter", lambda *_: None)
    if operation == "duplicate":
        window.layers.duplicate_rows([1])
    else:
        window.layers.merge_rows([0, 1])

    path = tmp_path / "artwork.pman"
    assert window.project.write(path)
    with ZipFile(path) as archive:
        tracks = json.loads(archive.read("project.json"))["tracks"]
        actual = [
            (track["name"], QImage.fromData(
                archive.read(track["cells"][0]["image"])).pixelColor(0, 0).name())
            for track in tracks
        ]
    assert actual == expected
    assert [track["sheet"] for track in tracks] == [[1]] * len(expected)
