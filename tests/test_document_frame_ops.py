"""Frame operations on the document require no widget or signal receiver."""
import pytest

from paintmaskanimator.document import Document
from paintmaskanimator.document_changes import ChangeSet, Cursor, ExposureIndexReset, FrameStructure, SheetSpans
from paintmaskanimator.models import Frame, Layer
from paintmaskanimator.pma_image import PmaImage
from paintmaskanimator.sheet import sheet_numbers
from paintmaskanimator.utils import blank_image


def test_duplicate_and_delete_preserve_bank():
    doc = Document()
    slot = Layer("A", blank_image(), has_content=True, sequence_number=7)
    doc.install_legacy_frames([Frame([slot]), Frame([Layer("A", blank_image())])])
    track = doc.tracks[0]
    cell = track.cells[7]
    assert doc.duplicate_frame(0) == ChangeSet((FrameStructure("duplicate", 2, 3, 1), Cursor(1)))
    assert sheet_numbers(doc.frames, 0) == [7, 7, None]
    assert doc.delete_frame(0, 0) == ChangeSet((
        FrameStructure("trim", 3, 1, 1), SheetSpans(0, 0, 3), Cursor(0),
    ))
    assert sheet_numbers(doc.frames, 0) == [7]
    assert doc.delete_frame(0, 0) == ChangeSet((SheetSpans(0, 0, 1), Cursor(0)))
    assert sheet_numbers(doc.frames, 0) == [None]
    assert track.cells[7] is cell
    assert doc.delete_frame(0, 0) == ChangeSet()


def test_invalid_frame_commands_and_forward_extension():
    doc = Document()
    before = doc.frames[:]
    assert doc.duplicate_frame(-1) == ChangeSet()
    assert doc.delete_frame(0, 100) == ChangeSet()
    assert doc.ensure_frame_count(0) == ChangeSet()
    assert doc.frames == before
    assert doc.advance_frame() == ChangeSet((FrameStructure("extend", 1, 2, 1), Cursor(1)))
    assert sheet_numbers(doc.frames, 0) == [None, None]
    assert doc.frames[1].layers[0]._track is doc.frames[0].layers[0]._track


@pytest.mark.parametrize("connected", [False, True])
def test_delete_frame_resets_exposure_index_before_other_changes_only_when_connecting(connected):
    doc = Document()
    image = PmaImage.blank(4, 3)
    frames = [Frame([Layer("A", image, has_content=True, sequence_number=7)]),
              Frame([Layer("A", image)])]
    if connected:
        doc.install_legacy_frames(frames, canvas_size=(4, 3))
    else:
        doc.frames = frames
    reset = () if connected else (ExposureIndexReset(),)
    assert doc.delete_frame(0, 0) == ChangeSet((
        *reset, FrameStructure("trim", 2, 1, 1), SheetSpans(0, 0, 2), Cursor(0),
    ))


def test_remap_validates_every_column_before_growing_or_changing_the_sheet():
    doc = Document()
    image = blank_image()
    doc.install_legacy_frames([Frame([
        Layer("A", image, has_content=True, sequence_number=7),
        Layer("B", image),
    ])])
    old_frame = doc.frames[0]
    assert doc.apply_sheet_remap(0, 1, [(0, [1, 1], [image]),
                                       (1, [2, None], [image])], 0) == ChangeSet()
    assert doc.frames == [old_frame]
    assert sheet_numbers(doc.frames, 0) == [7]
    assert set(doc.tracks[0].cells) == {7}

    changes = doc.apply_sheet_remap(0, 1, [(0, [1, 1], [image])], 0)
    assert changes.changes[:2] == (FrameStructure("extend", 1, 2, 1), SheetSpans(0, 0, 2))
    assert sheet_numbers(doc.frames, 0) == [1, 1]
    assert set(doc.tracks[0].cells) == {1, 7}


def test_remap_accepts_a_template_frame_past_the_end_inside_the_range():
    # 配置待ちの確定でコマが詰まり、カーソルが末尾より後ろに残った場合も、
    # 範囲までコマを伸ばしてから適用する。
    doc = Document()
    image = blank_image()
    doc.install_legacy_frames([Frame([Layer("A", image)])])
    changes = doc.apply_sheet_remap(0, 3, [(0, [1, 1, None, 1], [image])], 3)
    assert changes.changes[0] == FrameStructure("extend", 1, 4, 1)
    assert sheet_numbers(doc.frames, 0) == [1, 1, None, 1]
    assert doc.apply_sheet_remap(0, 3, [(0, [1, 1, None, 1], [image])], 4) == ChangeSet()


def test_move_selection_deduplicates_holds_and_rejects_invalid_destinations():
    doc = Document()
    image = blank_image()
    doc.install_legacy_frames([
        Frame([Layer("A", image, has_content=True, sequence_number=7), Layer("B", image)])
        for _ in range(2)
    ])
    positions = [(0, 0), (0, 1)]
    assert doc.prepare_sheet_moves(positions, -1, 1) is None
    assert sheet_numbers(doc.frames, 0) == [7, 7]
    assert sheet_numbers(doc.frames, 1) == [None, None]

    moves = doc.prepare_sheet_moves(positions, 1, 1)
    assert len(moves) == 1
    assert doc.move_sheet_cells(moves, 1, 1)
    assert sheet_numbers(doc.frames, 0) == [None, None, None]
    assert sheet_numbers(doc.frames, 1) == [None, 7, 7]
    assert set(doc.tracks[0].cells) == {7}
