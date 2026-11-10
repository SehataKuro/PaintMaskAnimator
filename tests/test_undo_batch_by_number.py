"""Batch history stores one image per drawing number."""
# ruff: noqa: F811
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor

from paintmaskanimator.document import ArchiveHint, clear_slot
from paintmaskanimator.frame_scope import FrameScope, apply_over_scope
from paintmaskanimator.models import make_frame
from paintmaskanimator.undo_entries import LayerBatchUndo, ScopedBatchUndo
from test_undo_unit_characterization import put, qapp, window  # noqa: F401


def batch(c):
    def blue(ctx):
        image = ctx.image.copy()
        image.fill(QColor("blue"))
        return image
    return apply_over_scope(c, FrameScope((0, 2, 3), (0,)), blue, label="batch").undo


def repeated(c, number=3):
    for fi in (0, 2, 3):
        put(c, fi, number)
    c.install_legacy_frames()


def colors(c, expected):
    assert all(c.frames[fi].layers[0].image.pixelColor(5, 5) == QColor(expected)
               for fi in (0, 2, 3))


def test_scoped_one_image_and_metadata(window):
    c = window.canvas
    repeated(c)
    entry = batch(c)
    assert isinstance(entry, ScopedBatchUndo)
    assert len(entry.cells) == 1 and entry.cells[0][-1] == 3
    for fi in (0, 2, 3):
        c.frames[fi].layers[0].has_content = fi == 0
    signals = []
    c.cellChanged.connect(lambda *args: signals.append(args))
    c.current_frame, c.active_layer_index = 1, 1
    c.undo()
    colors(c, "red")
    assert all(c.frames[fi].layers[0].has_content for fi in (0, 2, 3))
    assert signals == [(0, 0), (2, 0), (3, 0)]
    assert c.current_frame == 1 and c.active_layer_index == 0


@pytest.mark.parametrize("scoped", [True, False])
@pytest.mark.parametrize("is_blank_key", [True, False])
@pytest.mark.parametrize("archived", [True, False])
def test_retained_number_metadata_unchanged(window, scoped, is_blank_key, archived):
    c = window.canvas
    # Undo 自体の更新対象を検査し、UI の番号同期による再正規化を除く。
    c.cellChanged.disconnect(window._on_canvas_cell_changed)
    for fi in (0, 2):
        put(c, fi, 3)
    c.install_legacy_frames()
    retained = c.frames[1].layers[0]
    retained.sequence_number = 3
    retained.has_content = False
    retained.is_blank_key = is_blank_key
    entry = apply_over_scope(
        c, FrameScope((0, 2), (0,)),
        lambda ctx: ctx.image.copy(), label="batch",
    ).undo
    assert len(entry.cells) == 1
    if not scoped:
        c.undo_stack[-1] = LayerBatchUndo(
            0, [(fi, image, hc, number) for fi, _, image, hc, number in entry.cells],
        )
    layer = c.frames[0].layers[0]
    assert retained.cell_id != layer.cell_id
    drawing_id = layer.cell_id
    if archived:
        c._document.put_archive_hint(layer._track, 3, ArchiveHint())
        for fi in (0, 2):
            clear_slot(c.frames[fi].layers[0])
        assert all(frame.layers[0].cell_id != drawing_id for frame in c.frames)
    signals = []
    c.cellChanged.connect(lambda *args: signals.append(args))
    c.undo()
    assert not retained.has_content
    assert retained.is_blank_key == is_blank_key
    assert signals == ([] if archived else [(0, 0), (2, 0)])
    assert (c._document.tracks[0].legacy_archive_hints[3].has_content
            if archived else layer.has_content)


@pytest.mark.parametrize("mode,paper", [("sequence", False), ("sheet", True)])
def test_unnumbered_per_cell(window, mode, paper):
    c = window.canvas
    c.timeline_mode = mode
    for fi in (0, 2, 3):
        put(c, fi, None).is_paper = paper
    c.install_legacy_frames()
    entry = batch(c)
    assert len(entry.cells) == 3
    assert [cell[-1] for cell in entry.cells] == [1, 2, 3]
    c.undo()
    colors(c, "red")
    c.redo()
    colors(c, "blue")


def test_move_and_redo_resolve_again(window):
    c = window.canvas
    repeated(c)
    batch(c)
    c.frames.insert(0, make_frame(["A", "B"]))
    c.undo()
    assert c.redo_stack[-1].cells[0][0] == 1
    assert all(c.frames[fi].layers[0].image.pixelColor(5, 5) == QColor("red")
               for fi in (1, 3, 4))
    c.frames.insert(0, make_frame(["A", "B"]))
    c.redo()
    assert all(c.frames[fi].layers[0].image.pixelColor(5, 5) == QColor("blue")
               for fi in (2, 4, 5))


def test_archive_and_redo(window):
    c = window.canvas
    repeated(c)
    batch(c)
    archive = c.frames[0].layers[0]
    cell = archive._cell
    c._document.put_archive_hint(archive._track, 3, ArchiveHint())
    for fi in (0, 2, 3):
        clear_slot(c.frames[fi].layers[0])
    c.current_frame = 1
    signals = []
    c.cellChanged.connect(lambda *args: signals.append(args))
    c.undo()
    assert c.redo_stack[-1].cells[0][0] == 0
    assert cell.image.pixelColor(5, 5) == QColor("red")
    assert c._document.tracks[0].legacy_archive_hints[3].has_content
    assert c.current_frame == 1 and not signals
    c.redo()
    assert cell.image.pixelColor(5, 5) == QColor("blue")


def test_missing_discarded(window):
    c = window.canvas
    repeated(c)
    batch(c)
    c.frames = [make_frame(["A", "B"]) for _ in range(4)]
    c.undo()
    assert not c.undo_stack and not c.redo_stack


def test_selection_transform_batch(window):
    c = window.canvas
    repeated(c)
    c.set_transform_quality(False)
    c.selection_polygon = [QPointF(2, 2), QPointF(9, 2),
                           QPointF(9, 9), QPointF(2, 9)]
    assert c.begin_selection_transform("free")
    c.rotate_selection_transform(30)
    c.commit_selection_transform(True)
    entry = c.undo_stack[-1]
    assert isinstance(entry, LayerBatchUndo)
    assert len(entry.cells) == 1 and entry.cells[0][-1] == 3
    c.undo()
    colors(c, "red")
    c.redo()
    assert c.undo_stack[-1].cells[0][-1] == 3


def test_nbytes_one_image(window):
    c = window.canvas
    repeated(c)
    entry = batch(c)
    image = entry.cells[0][2]
    assert entry.nbytes == image.sizeInBytes()
    assert LayerBatchUndo(0, [(0, image, True, 3)]).nbytes == image.sizeInBytes()


def test_mixed_metadata_falls_back(window):
    c = window.canvas
    repeated(c)
    c.frames[2].layers[0].has_content = False
    # 従来のセル単位復元を検査し、UI の番号同期による再正規化を除く。
    c.cellChanged.disconnect(window._on_canvas_cell_changed)
    scope = FrameScope((0, 2, 3), (0,), key_frames_only=False)
    entry = apply_over_scope(c, scope, lambda ctx: ctx.image.copy(), label="batch").undo
    assert len(entry.cells) == 3
    assert all(cell[-1] is None for cell in entry.cells)
    c.undo()
    assert not c.frames[2].layers[0].has_content
    colors(c, "red")
