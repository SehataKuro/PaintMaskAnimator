"""一括処理ランナー（frame_scope）のテスト。"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator import frame_scope, imaging
from paintmaskanimator.canvas import PaintCanvas
from paintmaskanimator.undo_entries import ScopeBatchUndo


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def canvas(qapp):
    widget = PaintCanvas()
    widget._ensure_frame_count(4)
    for frame in widget.frames:
        for layer in frame.layers:
            layer.image.fill(QColor(255, 255, 255, 255))
            layer.has_content = True
            layer.exposure = 1
    yield widget
    widget.deleteLater()


def _fill_black(context):
    image = context.image.copy()
    image.fill(QColor(0, 0, 0, 255))
    return image, 1


def _first_pixel(canvas, frame_index, layer_index=0):
    return canvas.frames[frame_index].layers[layer_index].image.pixelColor(0, 0)


def test_all_frames_scope_covers_every_key_frame(canvas):
    scope = frame_scope.FrameScope.all_frames(canvas)
    cells = frame_scope.resolve_scope_cells(canvas, scope)
    assert [frame for frame, _layer in cells] == list(range(len(canvas.frames)))


def test_held_cells_are_processed_once(canvas):
    # 2コマ目以降を保持区間（○）にする。
    for frame in canvas.frames[1:]:
        frame.layers[0].has_content = False
    canvas.frames[0].layers[0].exposure = len(canvas.frames)

    cells = frame_scope.resolve_scope_cells(
        canvas, frame_scope.FrameScope.all_frames(canvas)
    )
    assert cells == [(0, 0)]


def test_apply_over_scope_writes_and_reports(canvas):
    result = frame_scope.apply_over_scope(
        canvas,
        frame_scope.FrameScope.all_frames(canvas),
        _fill_black,
        label="テスト",
    )

    assert not result.cancelled
    assert result.changed_cells == len(canvas.frames)
    assert result.changed_pixels == len(canvas.frames)
    for frame_index in range(len(canvas.frames)):
        assert _first_pixel(canvas, frame_index) == QColor(0, 0, 0, 255)


def test_single_undo_restores_every_cell(canvas):
    frame_scope.apply_over_scope(
        canvas,
        frame_scope.FrameScope.all_frames(canvas),
        _fill_black,
        label="テスト",
    )
    assert isinstance(canvas.undo_stack[-1], ScopeBatchUndo)
    assert canvas.undo_stack[-1].history_label == "テスト"

    canvas.undo()

    for frame_index in range(len(canvas.frames)):
        assert _first_pixel(canvas, frame_index) == QColor(255, 255, 255, 255)


def test_redo_reapplies_the_whole_batch(canvas):
    frame_scope.apply_over_scope(
        canvas,
        frame_scope.FrameScope.all_frames(canvas),
        _fill_black,
        label="テスト",
    )
    canvas.undo()
    canvas.redo()

    for frame_index in range(len(canvas.frames)):
        assert _first_pixel(canvas, frame_index) == QColor(0, 0, 0, 255)


def test_cancel_leaves_no_partial_application(canvas):
    seen = {"count": 0}

    def cancel_after_two():
        return seen["count"] >= 2

    def counting_op(context):
        seen["count"] += 1
        return _fill_black(context)

    undo_depth = len(canvas.undo_stack)
    result = frame_scope.apply_over_scope(
        canvas,
        frame_scope.FrameScope.all_frames(canvas),
        counting_op,
        cancelled=cancel_after_two,
    )

    assert result.cancelled
    assert result.changed_cells == 0
    assert result.undo_entry is None
    # 途中まで書いたコマも元の白へ戻っている。
    for frame_index in range(len(canvas.frames)):
        assert _first_pixel(canvas, frame_index) == QColor(255, 255, 255, 255)
    # Undoスタックも汚さない。
    assert len(canvas.undo_stack) == undo_depth


def test_operation_returning_none_skips_the_cell(canvas):
    result = frame_scope.apply_over_scope(
        canvas,
        frame_scope.FrameScope.all_frames(canvas),
        lambda context: None,
    )
    assert result.changed_cells == 0
    assert result.undo_entry is None


def test_multiple_layers_are_bundled_into_one_undo_entry(canvas):
    canvas.add_layer()
    for frame in canvas.frames:
        for layer in frame.layers:
            layer.image.fill(QColor(255, 255, 255, 255))
            layer.has_content = True

    layer_count = len(canvas.frames[0].layers)
    result = frame_scope.apply_over_scope(
        canvas,
        frame_scope.FrameScope.all_frames(
            canvas,
            layers=range(layer_count),
        ),
        _fill_black,
        label="全レイヤー",
    )

    assert result.changed_cells == len(canvas.frames) * layer_count
    entry = canvas.undo_stack[-1]
    assert isinstance(entry, ScopeBatchUndo)
    assert len(entry.entries) == layer_count

    canvas.undo()
    for frame_index in range(len(canvas.frames)):
        for layer_index in range(layer_count):
            assert _first_pixel(canvas, frame_index, layer_index) == QColor(
                255, 255, 255, 255
            )


def test_progress_reports_every_cell(canvas):
    reports = []
    frame_scope.apply_over_scope(
        canvas,
        frame_scope.FrameScope.all_frames(canvas),
        _fill_black,
        progress=lambda value, total, message: reports.append((value, total)),
    )
    assert reports[0] == (0, len(canvas.frames))
    assert reports[-1] == (len(canvas.frames), len(canvas.frames))


def test_frame_range_scope_limits_to_requested_frames(canvas):
    scope = frame_scope.FrameScope.frame_range(canvas, 1, 2)
    result = frame_scope.apply_over_scope(canvas, scope, _fill_black)

    assert result.changed_cells == 2
    assert _first_pixel(canvas, 0) == QColor(255, 255, 255, 255)
    assert _first_pixel(canvas, 1) == QColor(0, 0, 0, 255)
    assert _first_pixel(canvas, 2) == QColor(0, 0, 0, 255)
    assert _first_pixel(canvas, 3) == QColor(255, 255, 255, 255)


def test_runner_matches_direct_pixel_processing(canvas):
    """ランナー経由でも、素の画素処理と同じ結果になる。"""

    def darken(context):
        pixels = imaging.qimage_rgba_array(context.image)
        pixels[:, :, :3] = (pixels[:, :, :3] // 2).astype(np.uint8)
        return imaging.rgba_array_to_qimage(pixels), 1

    frame_scope.apply_over_scope(
        canvas,
        frame_scope.FrameScope.current_frame(canvas),
        darken,
    )
    assert _first_pixel(canvas, canvas.current_frame) == QColor(127, 127, 127, 255)
