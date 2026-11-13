"""Image-free explicit archive state at the Track / raw-v1 boundary."""
from paintmaskanimator.document import (
    ArchiveHint, Document, apply_number_mapping, project_v1_state,
)
from paintmaskanimator.models import Cell, TRACK_FIELDS
from paintmaskanimator.legacy_v1 import LegacyLayerV1


def test_hint_projection_includes_placed_explicit_and_unrepresented_bank():
    doc = Document()
    slot = doc.frames[0].layers[0]
    track = doc.tracks[0]
    track.cells[1] = Cell()
    slot.sequence_number = 1
    slot.has_content = True
    track.cells[9] = Cell()
    state = ArchiveHint(False, True, 3, True, {'steps': [1]}, None)
    doc.put_archive_hint(track, 1, state)
    state.tween['steps'].append(2)
    frames, archive = project_v1_state(doc.frames)
    assert frames is not doc.frames
    assert isinstance(frames[0].layers[0], LegacyLayerV1)
    assert frames[0].layers[0].sequence_number == 1
    assert set(archive) == {(0, 1), (0, 9)}
    explicit = archive[(0, 1)]
    assert explicit.image is track.cells[1].image
    assert not explicit.has_content and explicit.is_blank_key
    assert explicit.exposure == 3 and explicit.sequence_only
    assert explicit.tween == {'steps': [1]}
    assert isinstance(explicit, LegacyLayerV1)
    assert isinstance(archive[(0, 9)], LegacyLayerV1)
    explicit.tween['steps'].append(3)
    assert doc.archive_hints(track)[1].tween == {'steps': [1]}
    assert doc.placed_numbers(track) == {1}
    assert doc.unplaced_numbers(track) == {9}
    assert doc.remove_archive_hint(track, 1).is_blank_key
    assert doc.remove_archive_hint(track, 1) is None
    assert set(track.cells) == {1, 9}


def test_hint_graph_copy_and_snapshot_are_independent():
    doc = Document()
    track = doc.tracks[0]
    track.cells[9] = Cell(cell_name='nine')
    doc.put_archive_hint(track, 9, ArchiveHint(tween={'steps': [1]}))
    copied = doc.clone_graph()
    assert copied.tracks[0].cells[9] is not track.cells[9]
    copied.tracks[0].legacy_archive_hints[9].tween['steps'].append(2)
    assert track.legacy_archive_hints[9].tween == {'steps': [1]}
    snap = doc.snapshot()
    doc.restore(snap)
    doc.tracks[0].legacy_archive_hints[9].tween['steps'].append(3)
    doc.restore(snap)
    assert doc.tracks[0].legacy_archive_hints[9].tween == {'steps': [1]}
    assert track.copy_settings().legacy_archive_hints == {}
    assert 'legacy_archive_hints' not in TRACK_FIELDS


def test_number_cycle_remaps_hints_together_with_bank():
    doc = Document()
    track = doc.tracks[0]
    first, second = Cell(cell_name='one'), Cell(cell_name='two')
    track.cells.update({1: first, 2: second})
    doc.put_archive_hint(track, 1, ArchiveHint(False, True))
    doc.put_archive_hint(track, 2, ArchiveHint(exposure=4))
    apply_number_mapping(doc.frames, {}, 0, {1: 2, 2: 1})
    assert track.cells[2] is first and track.cells[1] is second
    assert doc.legacy_drawing_state(track, 2).is_blank_key
    assert doc.legacy_drawing_state(track, 1).exposure == 4
    before = dict(track.cells)
    assert doc.legacy_drawing_state(track, 99) == ArchiveHint()
    assert track.cells == before


def test_install_absorbs_raw_hint_and_reinstall_keeps_bank():
    from paintmaskanimator.models import Frame, Layer
    from paintmaskanimator.utils import blank_image

    doc = Document()
    raw = Layer('A', blank_image(), has_content=False, is_blank_key=True,
                sequence_number=9, tween={'steps': [1]})
    raw.cell_name = 'nine'
    doc.install_legacy_frames([Frame([Layer('A', blank_image())])], {(0, 9): raw})
    track = doc.tracks[0]
    assert not hasattr(doc, 'sequence_archive')
    assert track.cells[9].cell_name == 'nine'
    assert doc.archive_hints(track)[9].is_blank_key
    raw.tween['steps'].append(2)
    assert track.legacy_archive_hints[9].tween == {'steps': [1]}
    doc.install_legacy_frames(doc.frames)
    assert doc.tracks[0] is track
    assert set(track.cells) == {9}
    assert len(doc.snapshot()) == 6
    column = doc.capture_column(0)
    assert column[0]._track.cells[9] is not track.cells[9]
    assert column[0]._track.legacy_archive_hints == track.legacy_archive_hints


def test_bank_history_target_changes_existing_hint_only():
    from paintmaskanimator.document import DrawingTarget

    doc = Document()
    track = doc.tracks[0]
    track.cells[9] = Cell()
    target = DrawingTarget(track.cells[9], track, None, None, 9)
    target.has_content = False
    assert track.legacy_archive_hints == {}
    doc.put_archive_hint(track, 9, ArchiveHint(tween={'steps': [1]}))
    target.has_content = False
    assert not track.legacy_archive_hints[9].has_content
    assert track.legacy_archive_hints[9].tween == {'steps': [1]}


def test_unplaced_drawing_and_initial_history_use_bank_without_cursor_move():
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication
    from paintmaskanimator.canvas import PaintCanvas
    from paintmaskanimator.undo_entries import InitialStateUndo, LayerUndo
    from paintmaskanimator.utils import blank_image

    app = QApplication.instance() or QApplication([])
    canvas = PaintCanvas()
    try:
        track = canvas._document.tracks[0]
        red = blank_image()
        red.fill(QColor('red'))
        blue = blank_image()
        blue.fill(QColor('blue'))
        track.cells[9] = Cell(blue, blue)
        canvas._document.put_archive_hint(track, 9, ArchiveHint(False, True))
        signals = []
        canvas.cellChanged.connect(lambda *args: signals.append(args))
        canvas.push_undo(LayerUndo(0, 0, red, True, number=9))
        canvas.undo()
        assert track.cells[9].image.pixelColor(0, 0) == QColor('red')
        assert track.legacy_archive_hints[9].has_content
        assert not canvas.layers[0].has_content
        assert canvas.current_frame == 0 and not signals
        canvas.redo()
        assert track.cells[9].image.pixelColor(0, 0) == QColor('blue')
        assert not track.legacy_archive_hints[9].has_content
        canvas.push_undo(InitialStateUndo([], [(0, 9, red)]))
        canvas.undo()
        assert track.cells[9].initial_image.pixelColor(0, 0) == QColor('red')
        canvas.redo()
        assert track.cells[9].initial_image.pixelColor(0, 0) == QColor('blue')
        before = set(track.cells)
        assert canvas._resolve_numbered_target(0, 0, 99) is None
        assert set(track.cells) == before
    finally:
        canvas.close()
        canvas.deleteLater()
        app.processEvents()
