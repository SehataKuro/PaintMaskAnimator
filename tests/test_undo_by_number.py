"""Drawing history follows a drawing number after sheet edits."""
# ruff: noqa: F811
import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor

from paintmaskanimator.document import ArchiveHint, clear_slot
from paintmaskanimator import i18n
from paintmaskanimator.models import make_frame
from paintmaskanimator.undo_entries import LayerRegionUndo, LayerTilesUndo, LayerUndo, history_label_for
from test_undo_unit_characterization import put, window, qapp  # noqa: F401


@pytest.mark.parametrize("kind", [LayerUndo, LayerRegionUndo, LayerTilesUndo])
def test_move_undo_redo(window, kind):
    c = window.canvas
    layer = put(c, 0, 3)
    rect = QRect(0, 0, 16, 16)
    args = ([layer.image.copy()] if kind is LayerUndo else
            [rect, layer.image.copy()] if kind is LayerRegionUndo else
            [((rect, layer.image.copy()),)])
    c.push_undo(kind(0, 0, *args, True, layer_name="A", number=3))
    layer.image.fill(QColor("blue"))
    c.frames.insert(0, make_frame(["A", "B"]))
    other = put(c, 0, 7, "green")
    c.undo()
    assert c.current_frame == 1
    assert layer.image.pixelColor(5, 5) == QColor("red")
    assert other.image.pixelColor(5, 5) == QColor("green")
    assert c.redo_stack[-1].frame == 1
    c.frames.insert(0, make_frame(["A", "B"]))
    c.redo()
    assert c.current_frame == 2
    assert layer.image.pixelColor(5, 5) == QColor("blue")


def test_archive(window):
    c = window.canvas
    layer = put(c, 0, 3)
    c.install_legacy_frames()
    cell = layer._cell
    c.push_layer_undo()
    layer.image.fill(QColor("blue"))
    c._document.put_archive_hint(layer._track, 3, ArchiveHint())
    clear_slot(layer)
    c.current_frame = 2
    signals = []
    c.cellChanged.connect(lambda *args: signals.append(args))
    c.undo()
    assert c.current_frame == 2
    assert not signals
    assert cell.image.pixelColor(5, 5) == QColor("red")
    c.redo()
    assert c.current_frame == 2
    assert cell.image.pixelColor(5, 5) == QColor("blue")


def test_missing_number_discarded(window):
    c = window.canvas
    put(c, 0, 3)
    c.push_layer_undo()
    c.frames[0] = make_frame(["A", "B"])
    c.undo()
    assert not c.undo_stack and not c.redo_stack


@pytest.mark.parametrize("mode,paper,draft", [("sequence", False, False), ("sheet", True, False), ("sheet", False, True)])
def test_unnumbered_legacy(window, mode, paper, draft):
    c = window.canvas
    c.timeline_mode = mode
    layer = put(c, 0, None)
    layer.is_paper, layer.is_draft = paper, draft
    before = c.sequence_entry_columns(0)
    c.ensure_editable_key()
    c.push_layer_undo()
    assert c.undo_stack[-1].number is None
    assert c.sequence_entry_columns(0) == before
    layer.image.fill(QColor("blue"))
    layer.sequence_number = 9
    c.undo()
    assert layer.image.pixelColor(5, 5) == QColor("red")


def test_paint_unused_paper_cell_assigns_number(window):
    c = window.canvas
    c.timeline_mode = "sheet"
    layer = c.active_layer
    layer.is_paper = True
    assert not layer.has_content
    assert layer.sequence_number is None

    c.ensure_editable_key()
    c.push_layer_undo()
    layer.image.setPixelColor(5, 5, QColor("blue"))

    assert layer.has_content
    assert layer.sequence_number == 1


def test_assign_number_including_archive(window):
    c = window.canvas
    layer = put(c, 0, None)
    stored = put(c, 3, 8)
    c.install_legacy_frames()
    c._document.put_archive_hint(stored._track, 8, ArchiveHint())
    clear_slot(stored)
    c.ensure_editable_key()
    c.push_layer_undo()
    assert layer.sequence_number == c.undo_stack[-1].number == 9


def test_resolve_priority_and_sequence_only(window):
    c = window.canvas
    put(c, 0, 3)
    put(c, 2, 3)
    c.current_frame = 2
    c.push_layer_undo()
    e = c.undo_stack[-1]
    assert c._resolve_drawing_undo_target(e).column == 2
    c.frames[2] = make_frame(["A", "B"])
    c.frames[0].layers[0].sequence_only = True
    assert c._resolve_drawing_undo_target(e) is None
    c.timeline_mode = "sequence"
    assert c._resolve_drawing_undo_target(e).column == 0


def test_label(window, qapp):
    c = window.canvas
    put(c, 0, 3)
    c.push_layer_undo()
    e = c.undo_stack[-1]
    try:
        i18n.install(qapp, "ja")
        assert history_label_for(e) == "描画（A の 3番）"
        i18n.install(qapp, "en")
        assert history_label_for(e) == "Drawing (A, No. 3)"
        e.number = None
        assert history_label_for(e) == "Draw"
    finally:
        i18n.install(qapp, i18n.DEFAULT_LANGUAGE)
