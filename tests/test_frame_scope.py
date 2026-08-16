"""一括処理ランナー（Issue #9）の単体テスト。

ランナーは UI から独立しているので、``PaintCanvas`` を作らずに検証する。
最小のドキュメント代役（``frames`` と ``push_undo`` だけを持つ）で足りる。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.canvas import PaintCanvas  # noqa: E402
from paintmaskanimator.frame_scope import (  # noqa: E402
    FrameScope,
    ScopeCancelled,
    apply_over_scope,
    resolve_cells,
)
from paintmaskanimator.models import Frame, Layer  # noqa: E402
from paintmaskanimator.undo_entries import ScopedBatchUndo  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def make_image(color):
    image = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(color))
    return image


class FakeDoc:
    """``frames`` / ``push_undo`` だけを持つ最小のドキュメント。"""

    def __init__(self, frame_count=3, layer_count=1, color="white"):
        self.frames = [
            Frame([
                Layer(
                    f"L{layer_index}",
                    make_image(color),
                    has_content=True,
                )
                for layer_index in range(layer_count)
            ])
            for _ in range(frame_count)
        ]
        self.current_frame = 0
        self.active_layer_index = 0
        self.undo_stack = []

    def push_undo(self, entry):
        self.undo_stack.append(entry)
        return entry


def fill_op(color):
    def op(context):
        return make_image(color)
    return op


def test_current_frame_scope_touches_one_cell(qapp):
    doc = FakeDoc()
    result = apply_over_scope(
        doc, FrameScope.current_frame(doc), fill_op("red"), label="テスト"
    )
    assert result.changed_cells == 1
    assert doc.frames[0].layers[0].image.pixelColor(0, 0) == QColor("red")
    assert doc.frames[1].layers[0].image.pixelColor(0, 0) == QColor("white")


def test_all_frames_scope_touches_every_key_frame(qapp):
    doc = FakeDoc()
    result = apply_over_scope(
        doc, FrameScope.all_frames(doc), fill_op("red"), label="テスト"
    )
    assert result.changed_cells == 3
    assert result.changed_frames == [0, 1, 2]
    assert all(
        frame.layers[0].image.pixelColor(0, 0) == QColor("red")
        for frame in doc.frames
    )


def test_undo_is_a_single_entry_restoring_every_cell(qapp):
    doc = FakeDoc()
    apply_over_scope(
        doc, FrameScope.all_frames(doc), fill_op("red"), label="テスト"
    )
    assert len(doc.undo_stack) == 1
    entry = doc.undo_stack[0]
    assert isinstance(entry, ScopedBatchUndo)
    assert len(entry.cells) == 3
    assert entry.history_label == "テスト"


def test_scope_can_cross_layers(qapp):
    doc = FakeDoc(frame_count=2, layer_count=2)
    scope = FrameScope.cells((0, 1), (0, 1))
    result = apply_over_scope(doc, scope, fill_op("red"), label="テスト")
    assert result.changed_cells == 4
    assert len(doc.undo_stack) == 1
    assert len(doc.undo_stack[0].cells) == 4


def test_hold_cells_collapse_to_their_key_frame(qapp):
    doc = FakeDoc(frame_count=3)
    # コマ1・2は実体を持たない保持セル。コマ0のキーフレームだけを処理する。
    for frame in doc.frames[1:]:
        frame.layers[0].has_content = False
    doc.frames[0].layers[0].exposure = 3

    assert resolve_cells(doc, FrameScope.all_frames(doc)) == [(0, 0)]
    result = apply_over_scope(
        doc, FrameScope.all_frames(doc), fill_op("red"), label="テスト"
    )
    assert result.changed_cells == 1
    assert result.visited_cells == 1


def test_blank_key_cells_are_skipped(qapp):
    doc = FakeDoc(frame_count=2)
    doc.frames[0].layers[0].has_content = False
    doc.frames[0].layers[0].is_blank_key = True
    assert resolve_cells(doc, FrameScope.all_frames(doc)) == [(1, 0)]


def test_shared_images_are_processed_once_and_stay_shared(qapp):
    doc = FakeDoc(frame_count=2)
    # 連番セルは同じ QImage を指す（coalesce_numbered_images 相当の状態）。
    doc.frames[1].layers[0].image = doc.frames[0].layers[0].image

    calls = []

    def op(context):
        calls.append((context.frame, context.layer_index))
        return make_image("red")

    result = apply_over_scope(
        doc, FrameScope.all_frames(doc), op, label="テスト"
    )
    assert calls == [(0, 0)]
    assert result.changed_cells == 2
    assert doc.frames[0].layers[0].image is doc.frames[1].layers[0].image


def test_returning_none_leaves_the_cell_untouched(qapp):
    doc = FakeDoc()

    def op(context):
        return make_image("red") if context.frame == 1 else None

    result = apply_over_scope(
        doc, FrameScope.all_frames(doc), op, label="テスト"
    )
    assert result.changed_cells == 1
    assert result.visited_cells == 3
    assert doc.frames[0].layers[0].image.pixelColor(0, 0) == QColor("white")
    assert doc.frames[1].layers[0].image.pixelColor(0, 0) == QColor("red")


def test_cancel_leaves_no_partial_application(qapp):
    doc = FakeDoc(frame_count=4)
    seen = []

    def should_cancel():
        return len(seen) >= 2

    def op(context):
        seen.append(context.frame)
        return make_image("red")

    result = apply_over_scope(
        doc,
        FrameScope.all_frames(doc),
        op,
        label="テスト",
        should_cancel=should_cancel,
    )
    assert result.cancelled
    assert result.changed_cells == 0
    assert doc.undo_stack == []
    assert all(
        frame.layers[0].image.pixelColor(0, 0) == QColor("white")
        for frame in doc.frames
    )


def test_op_raising_scope_cancelled_also_rolls_back(qapp):
    doc = FakeDoc(frame_count=3)

    def op(context):
        if context.frame == 2:
            raise ScopeCancelled()
        return make_image("red")

    result = apply_over_scope(
        doc, FrameScope.all_frames(doc), op, label="テスト"
    )
    assert result.cancelled
    assert all(
        frame.layers[0].image.pixelColor(0, 0) == QColor("white")
        for frame in doc.frames
    )


def test_progress_is_reported_for_every_cell_and_completes(qapp):
    doc = FakeDoc(frame_count=3)
    reports = []
    apply_over_scope(
        doc,
        FrameScope.all_frames(doc),
        fill_op("red"),
        label="テスト",
        progress=lambda done, total, cell: reports.append((done, total)),
    )
    assert reports == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_count_pixels_feeds_the_result(qapp):
    doc = FakeDoc(frame_count=2)
    result = apply_over_scope(
        doc,
        FrameScope.all_frames(doc),
        fill_op("red"),
        label="テスト",
        count_pixels=lambda context, image: 16,
    )
    assert result.changed_pixels == 32


def test_empty_scope_is_a_no_op(qapp):
    doc = FakeDoc()
    result = apply_over_scope(
        doc, FrameScope.cells((), ()), fill_op("red"), label="テスト"
    )
    assert not result.changed
    assert doc.undo_stack == []


def test_choose_switches_between_current_and_all_frames(qapp):
    doc = FakeDoc(frame_count=3)
    assert FrameScope.choose(doc, False).frames == (0,)
    assert FrameScope.choose(doc, True).frames == (0, 1, 2)
    assert FrameScope.choose(doc, True).is_all_frames


def test_canvas_undo_restores_every_scoped_cell(qapp):
    """ランナーの Undo エントリが実物の PaintCanvas で往復すること。"""
    canvas = PaintCanvas()
    canvas._ensure_frame_count(3)
    for frame in canvas.frames:
        frame.layers[0].has_content = True
    before = [frame.layers[0].image.copy() for frame in canvas.frames]

    result = apply_over_scope(
        canvas,
        FrameScope.all_frames(canvas),
        fill_op("red"),
        label="テスト一括",
    )
    assert result.changed_cells == 3
    assert canvas.undo_stack[-1] is result.undo

    canvas.undo()
    assert all(
        frame.layers[0].image == original
        for frame, original in zip(canvas.frames, before)
    )
    canvas.redo()
    assert all(
        frame.layers[0].image.pixelColor(0, 0) == QColor("red")
        for frame in canvas.frames
    )
