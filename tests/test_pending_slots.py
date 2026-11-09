"""Track-owned placement intent, separate from unplaced artwork."""
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage

from paintmaskanimator.document import Document, clear_pending, clear_slot, mark_pending
from paintmaskanimator.models import Cell, Frame, Layer
from paintmaskanimator.timeline_metadata import metadata_for


def image():
    result = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(0xff123456)
    return result


def test_install_preserves_duplicate_and_unnumbered_pending_positions():
    slots = [Layer('A', image(), sequence_number=n, sequence_only=True)
             for n in (3, 3, None)]
    doc = Document()
    doc.install_legacy_frames([Frame([slot]) for slot in slots])
    track = doc.tracks[0]
    assert len(track.pending_slots) == 3
    assert [entry.column for entry in doc.pending_entries(track)] == [0, 1, 2]
    assert all(metadata_for(slot).sequence_only for slot in slots)
    before = Layer.timeline_revision
    clear_pending(slots[0])
    assert Layer.timeline_revision > before
    assert len(track.pending_slots) == 2
    assert not metadata_for(slots[0]).sequence_only
    assert slots[1].sequence_only and slots[2].sequence_only
    doc.install_legacy_frames(doc.frames)
    assert len(track.pending_slots) == 2


def test_temporary_slot_copy_registers_only_on_attach_and_clear_keeps_bank():
    doc = Document()
    slot = doc.frames[0].layers[0]
    slot.sequence_number = 3
    slot._track.cells[3] = Cell(image())
    mark_pending(slot)
    track = slot._track
    copied = slot.clone_slot()
    assert copied.sequence_only
    assert len(track.pending_slots) == 1
    assert copied.__dict__['_pending_token'] is not slot.__dict__['_pending_token']
    copied.attach_track(track)
    assert len(track.pending_slots) == 2
    clear_slot(copied)
    assert len(track.pending_slots) == 1
    assert 3 in track.cells
    assert slot.sequence_only


def test_snapshot_pending_tokens_and_rust_projection_are_independent():
    doc = Document()
    slot = doc.frames[0].layers[0]
    mark_pending(slot)
    snapshot = doc.snapshot()
    frozen = snapshot.frames[0].layers[0]
    assert frozen.sequence_only and metadata_for(frozen).sequence_only
    assert frozen.__dict__['_pending_token'] is not slot.__dict__['_pending_token']
    clear_pending(slot)
    assert frozen.sequence_only
    doc.restore(snapshot)
    clear_pending(doc.frames[0].layers[0])
    doc.restore(snapshot)
    assert doc.frames[0].layers[0].sequence_only
    assert len(doc.tracks[0].pending_slots) == 1
    assert not doc.tracks[0].copy_settings().pending_slots


def test_apply_does_not_place_all_unplaced_bank_drawings():
    from PySide6.QtWidgets import QApplication
    from paintmaskanimator.canvas import PaintCanvas

    app = QApplication.instance() or QApplication([])
    canvas = PaintCanvas()
    try:
        track = canvas._document.tracks[0]
        track.cells.update({3: Cell(image()), 9: Cell(image())})
        before = [(layer.sequence_number, layer.has_content, layer.exposure)
                  for frame in canvas.frames for layer in frame.layers]
        changes = []
        canvas.changed.connect(lambda: changes.append(True))
        canvas.apply_sequence_only_entries()
        assert before == [(layer.sequence_number, layer.has_content, layer.exposure)
                          for frame in canvas.frames for layer in frame.layers]
        assert canvas._document.unplaced_numbers(track) == {3, 9}
        assert not changes
        assert not canvas.undo_stack
    finally:
        canvas.close()
        canvas.deleteLater()
        app.processEvents()


def assert_pending_graph(doc, removed=()):
    """Inspect raw Rust values before any metadata_for call can repair them."""
    for track in doc.tracks:
        slots = [slot for frame in doc.frames for slot in frame.layers
                 if slot._track is track]
        expected = {slot.__dict__['_pending_token'] for slot in slots
                    if slot.sequence_only}
        assert set(track.pending_slots) == expected
        for slot in slots:
            assert slot.__dict__['_timeline_metadata'].sequence_only == slot.sequence_only
    for slot in removed:
        assert slot.__dict__.get('_pending_token') not in slot._track.pending_slots
        assert not slot.__dict__['_timeline_metadata'].sequence_only


def test_pending_graph_shift_copy_exposure_trim_and_restore(qapp):
    from paintmaskanimator.canvas import PaintCanvas
    from paintmaskanimator.undo_entries import ExposureUndo

    canvas = PaintCanvas()
    try:
        slots = [Layer('A', image()), Layer('A', image(), has_content=True,
                                          sequence_number=3, sequence_only=True)]
        canvas.install_legacy_frames([Frame([slot]) for slot in slots])
        assert_pending_graph(canvas._document)
        copied = slots[1].clone_slot()
        assert len(slots[1]._track.pending_slots) == 1
        assert copied.__dict__['_timeline_metadata'].sequence_only
        canvas._shift_timeline_layer_right(0, 1, 1)
        assert_pending_graph(canvas._document)
        canvas._shift_timeline_layer_left(0, 2, 1)
        assert_pending_graph(canvas._document)
        canvas.current_frame = 1
        canvas.add_frame(True)
        assert_pending_graph(canvas._document)
        removed = canvas.frames[2].layers[0]
        canvas.apply_undo_entry(ExposureUndo(0, 0, 1, 2, 0, 0))
        assert_pending_graph(canvas._document, [removed])
        # Pending without content is invisible to trim, but must release its token.
        trailing = canvas.frames[-1].layers[0]
        trailing.has_content = False
        canvas.timeline_mode = 'sheet'
        canvas._trim_unused_trailing_frames()
        assert_pending_graph(canvas._document, [trailing])
        mark_pending(canvas.frames[0].layers[0])
        snapshot = canvas._document.snapshot()
        for _ in range(2):
            canvas._document.restore(snapshot)
            assert_pending_graph(canvas._document)
            clear_pending(canvas.frames[0].layers[0])
    finally:
        canvas.close()
        canvas.deleteLater()
        qapp.processEvents()


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    from paintmaskanimator import constants
    from paintmaskanimator.main_window import MainWindow

    monkeypatch.setattr(constants, 'CANVAS_WIDTH', 4)
    monkeypatch.setattr(constants, 'CANVAS_HEIGHT', 4)
    monkeypatch.setenv('APPDATA', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    result = MainWindow()
    yield result
    result.deleteLater()
    qapp.sendPostedEvents()


@pytest.mark.parametrize('exposure, split', [(4, (2, 2)), (5, (2, 3)), (1, (1, 1))])
def test_set_mode_places_only_pending_and_preserves_selection_and_undo(window, exposure, split):
    canvas = window.canvas
    slots = [Layer('A', image(), has_content=True, sequence_number=1, exposure=exposure)]
    slots += [Layer('A', image()) for _ in range(4)]
    slots += [Layer('A', image(), has_content=True, sequence_number=2, sequence_only=True)]
    canvas.install_legacy_frames([Frame([slot]) for slot in slots])
    track = canvas._document.tracks[0]
    track.cells[9] = Cell(image())
    canvas.timeline_mode = 'sequence'
    canvas.current_frame = 5
    before = len(canvas.undo_stack)
    changes = []
    canvas.changed.connect(lambda: changes.append(True))
    window.timeline_ops.set_mode('sheet')
    target = max(1, exposure // 2) if exposure > 1 else 1
    assert canvas.current_frame == target
    assert canvas.timeline_mode == 'sheet'
    assert len(canvas.undo_stack) == before + 1
    assert len(changes) == 1
    assert canvas.frames[0].layers[0].exposure == split[0]
    placed = canvas.frames[target].layers[0]
    assert (placed.sequence_number, placed.has_content, placed.exposure) == (2, True, split[1])
    assert not slots[5].has_content and not slots[5].sequence_only
    assert canvas._document.unplaced_numbers(track) == {9}
    assert_pending_graph(canvas._document, [slots[5]])
    canvas.undo()
    assert canvas.frames[5].layers[0].sequence_only
    assert_pending_graph(canvas._document)
    canvas.redo()
    assert_pending_graph(canvas._document)


def test_pending_graph_column_duplicate_reorder_delete(window):
    canvas = window.canvas
    mark_pending(canvas.frames[0].layers[0])
    canvas.add_layer()
    assert_pending_graph(canvas._document)
    window.layers.duplicate_rows([1])
    assert_pending_graph(canvas._document)
    window.layers.move_row([1], 0)
    assert_pending_graph(canvas._document)
    removed = [frame.layers[canvas.active_layer_index] for frame in canvas.frames]
    canvas.delete_layer()
    assert_pending_graph(canvas._document, removed)
    canvas.undo()
    assert_pending_graph(canvas._document)
    removed = [frame.layers[-1] for frame in canvas.frames]
    window.layers.delete_rows([0])
    assert_pending_graph(canvas._document, removed)


@pytest.mark.parametrize('case', ['bank_only', 'none', 'duplicate'])
def test_set_mode_pending_edge_cases_keep_bank_and_token_membership(window, case):
    canvas = window.canvas
    slots = [Layer('A', image(), has_content=True, sequence_number=1, exposure=4)]
    slots += [Layer('A', image()) for _ in range(4)]
    if case == 'none':
        slots.append(Layer('A', image(), sequence_only=True))
    elif case == 'duplicate':
        slots[4] = Layer('A', image(), has_content=True, sequence_number=2, sequence_only=True)
        slots.append(Layer('A', image(), has_content=True, sequence_number=2, sequence_only=True))
    canvas.install_legacy_frames([Frame([slot]) for slot in slots])
    track = canvas._document.tracks[0]
    track.cells[9] = Cell(image())
    canvas.timeline_mode = 'sequence'
    canvas.current_frame = len(slots) - 1
    changes = []
    canvas.changed.connect(lambda: changes.append(True))
    before = len(canvas.undo_stack)
    window.timeline_ops.set_mode('sheet')
    assert canvas.timeline_mode == 'sheet'
    assert len(canvas.undo_stack) == before + (case != 'bank_only')
    assert len(changes) == (case == 'duplicate')
    assert 9 in canvas._document.unplaced_numbers(track)
    assert all(slot.sequence_number != 9 for frame in canvas.frames for slot in frame.layers)
    assert_pending_graph(canvas._document)
    if case == 'none':
        assert canvas.current_frame == 5
        assert canvas.frames[5].layers[0].sequence_only
        assert len(track.pending_slots) == 1
    elif case == 'duplicate':
        assert not track.pending_slots
        assert not slots[4].has_content and not slots[5].has_content
        assert canvas.frames[2].layers[0].sequence_number == 2
        assert canvas.frames[1].layers[0].sequence_number == 2
        assert canvas.current_frame == 1
    else:
        assert canvas.current_frame == 4
        assert not track.pending_slots


@pytest.mark.parametrize('operation', ['merge', 'undo_add_layer'])
def test_removed_columns_release_pending_with_retained_references(window, operation):
    canvas = window.canvas
    canvas.add_layer()
    removed = [canvas.frames[0].layers[1]]
    mark_pending(removed[0])
    if operation == 'merge':
        removed.append(canvas.frames[0].layers[0])
        mark_pending(removed[1])
        window.layers.merge_rows([0, 1])
    else:
        canvas.undo()  # LayerRemoveUndo, not a document snapshot replacement.
    assert_pending_graph(canvas._document, removed)
    if operation == 'undo_add_layer':
        canvas.redo()
        assert canvas.frames[0].layers[1].sequence_only
        assert_pending_graph(canvas._document)
