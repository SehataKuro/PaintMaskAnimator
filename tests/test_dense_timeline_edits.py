"""Dense edit behavior through real Canvas/Undo and timeline controller methods."""
from types import SimpleNamespace

from paintmaskanimator.canvas import PaintCanvas
from paintmaskanimator.document import Document
from paintmaskanimator.legacy_v1 import LegacyLayerV1
from paintmaskanimator.main_window_timeline_ops import TimelineOpsController
from paintmaskanimator.models import Frame
from paintmaskanimator.sheet import sheet_numbers
from paintmaskanimator.undo_entries import ExposureUndo
from paintmaskanimator.utils import blank_image


def setup(qtbot, blank=False):
    doc = Document()
    rows = [LegacyLayerV1("A", blank_image(), has_content=not blank,
                          is_blank_key=blank, exposure=3,
                          sequence_number=None if blank else 1)]
    rows += [LegacyLayerV1("A", blank_image()) for _ in range(3)]
    doc.install_legacy_frames([Frame([slot]) for slot in rows])
    canvas = PaintCanvas()
    qtbot.addWidget(canvas)
    canvas.frames = doc.frames
    canvas.current_frame = 0
    canvas.active_layer_index = 0
    controller = TimelineOpsController(SimpleNamespace(canvas=canvas))
    controller._cell_target = lambda row, col: (0, col)
    controller._cell_block = lambda row, col: (0, *canvas.timeline_block_at(col, 0)[1:])
    controller._after_cell_edit = lambda: None
    return canvas, controller


def test_insert_hold_delete_and_undo(qtbot):
    canvas, controller = setup(qtbot)
    assert controller.insert_frame_at(0, 1)
    assert sheet_numbers(canvas.frames, 0) == [1, 1, 1, 1, None]
    canvas.undo()
    assert sheet_numbers(canvas.frames, 0) == [1, 1, 1, None]
    canvas.redo()
    assert sheet_numbers(canvas.frames, 0) == [1, 1, 1, 1, None]
    assert controller.delete_frame_at(0, 1)
    assert sheet_numbers(canvas.frames, 0) == [1, 1, 1, None, None]


def test_insert_blank_middle_preserves_start(qtbot):
    canvas, controller = setup(qtbot, blank=True)
    controller.insert_frame_at(0, 1)
    assert canvas.timeline_block_at(3, 0) == ("blank", 0, 4)
    assert not canvas.frames[1].layers[0].blank_start


def test_split_independent_number_and_undo_redo(qtbot):
    canvas, controller = setup(qtbot)
    source = canvas.frames[0].layers[0]
    source.cell_name = "original"
    source.initial_image = blank_image()
    assert controller.split_clip(0, 1)
    assert sheet_numbers(canvas.frames, 0) == [1, 2, 2, None]
    tail = canvas.frames[1].layers[0]
    assert tail.cell_id != source.cell_id
    assert tail.cell_name == "original"
    assert tail.initial_image is not source.initial_image
    canvas.undo()
    assert sheet_numbers(canvas.frames, 0) == [1, 1, 1, None]
    canvas.redo()
    assert sheet_numbers(canvas.frames, 0) == [1, 2, 2, None]


def test_resize_reference_undo_and_redo(qtbot):
    canvas, controller = setup(qtbot)
    controller.resize_exposure(0, 0, 4)
    assert isinstance(canvas.undo_stack[-1], ExposureUndo)
    assert sheet_numbers(canvas.frames, 0) == [1] * 5
    canvas.undo()
    assert sheet_numbers(canvas.frames, 0) == [1, 1, 1, None]
    canvas.redo()
    assert sheet_numbers(canvas.frames, 0) == [1] * 5


def test_blank_creation_splits_hold_and_undo(qtbot):
    canvas, _controller = setup(qtbot)
    assert canvas.create_blank_key(1, 0)
    assert sheet_numbers(canvas.frames, 0) == [1, None, None, None]
    assert canvas.timeline_block_at(2, 0) == ("blank", 1, 2)
    canvas.undo()
    assert sheet_numbers(canvas.frames, 0) == [1, 1, 1, None]
    canvas.redo()
    assert canvas.timeline_block_at(2, 0) == ("blank", 1, 2)


def test_blank_creation_at_start_inserts_full_blank_span(qtbot):
    canvas, _controller = setup(qtbot)
    assert canvas.create_blank_key(0, 0)
    assert canvas.timeline_block_at(5, 0) == ("blank", 3, 3)
    assert sheet_numbers(canvas.frames, 0) == [1, 1, 1, None, None, None]
    canvas.undo()
    assert sheet_numbers(canvas.frames, 0) == [1, 1, 1, None]


def test_clear_whole_span_and_undo(qtbot):
    canvas, controller = setup(qtbot)
    assert controller.clear_cell(0, 1)
    assert sheet_numbers(canvas.frames, 0) == [None] * 4
    canvas.undo()
    assert sheet_numbers(canvas.frames, 0) == [1, 1, 1, None]


def test_move_full_span_preserves_number_and_undo(qtbot):
    canvas, _controller = setup(qtbot)
    assert canvas.move_timeline_cell(0, 0, 4, 0)
    assert sheet_numbers(canvas.frames, 0) == [None] * 4 + [1] * 3
    canvas.undo()
    assert sheet_numbers(canvas.frames, 0) == [1, 1, 1, None]
    canvas.redo()
    assert sheet_numbers(canvas.frames, 0) == [None] * 4 + [1] * 3
