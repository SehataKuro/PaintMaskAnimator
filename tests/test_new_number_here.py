"""選んだ1コマだけを新しい絵番号へ分ける操作。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    window.canvas._ensure_frame_count(6)
    window.canvas.timeline_mode = "sheet"
    try:
        yield window
    finally:
        window.deleteLater()


def visual_row(window, layer_index=0):
    return len(window.canvas.layers) - 1 - layer_index


def paint(window, column=1, number=1, exposure=4):
    layer = window.canvas.frames[column].layers[0]
    layer.image.setPixelColor(10, 10, QColor("#123456"))
    layer.has_content = True
    layer.sequence_number = number
    layer.exposure = exposure
    return layer


def snapshot(window):
    return (
        window.time_remap.timeline_states(0),
        [[(layer.sequence_number, layer.exposure, layer.has_content,
           layer.is_blank_key, layer.cell_id, layer.image.cacheKey())
          for layer in frame.layers] for frame in window.canvas.frames],
    )


def test_middle_preserves_the_original_drawing_and_detaches_the_new_one(window):
    source = paint(window)
    source.cell_name = "original"
    source.tween = {"id": "original-tween"}
    source.tween_member = "original-tween"
    before_image = source.image.copy()
    paint(window, 5, number=5, exposure=1)
    other_frames = [frame.layers[1:] for frame in window.canvas.frames]
    frame_count = len(window.canvas.frames)

    assert window.timeline_ops.new_number_here(visual_row(window), 2)

    assert window.time_remap.timeline_states(0) == [None, 1, 6, 1, 1, 5]
    assert len(window.canvas.frames) == frame_count
    assert [frame.layers[1:] for frame in window.canvas.frames] == other_frames
    head, selected, tail = [window.canvas.frames[c].layers[0] for c in (1, 2, 3)]
    assert [head.exposure, selected.exposure, tail.exposure] == [1, 1, 2]
    assert head is source
    assert head.tween == {"id": "original-tween"}
    assert head.tween_member == "original-tween"
    assert tail.tween is tail.tween_member is None
    assert tail.cell_name == "original"
    assert head.cell_id == tail.cell_id != selected.cell_id
    assert head.track_id == tail.track_id == selected.track_id
    assert selected.image == before_image
    assert selected.cell_name is selected.tween is selected.tween_member is None
    assert selected.has_content
    assert not selected.is_blank_key
    assert not selected.sequence_only
    selected.image.setPixelColor(10, 10, QColor("#abcdef"))
    assert head.image == before_image
    tail.image.setPixelColor(10, 10, QColor("#fedcba"))
    assert head.image.pixelColor(10, 10) == QColor("#fedcba")
    assert selected.image.pixelColor(10, 10) == QColor("#abcdef")
    assert window.canvas.current_frame == 2
    assert window.canvas.active_layer_index == 0


@pytest.mark.parametrize("exposure,column,expected,keys", [
    (4, 1, [None, 2, 1, 1, 1, None], [(1, 1), (2, 3)]),
    (1, 1, [None, 2, None, None, None, None], [(1, 1)]),
    (4, 4, [None, 1, 1, 1, 2, None], [(1, 3), (4, 1)]),
])
def test_head_single_and_last_frame(window, exposure, column, expected, keys):
    source = paint(window, exposure=exposure)
    source.cell_name = "original"
    original_cell = source.cell_id

    assert window.timeline_ops.new_number_here(visual_row(window), column)

    assert window.time_remap.timeline_states(0) == expected
    assert len(window.canvas.frames) == 6
    assert [(c, window.canvas.frames[c].layers[0].exposure) for c, _ in keys] == keys
    if exposure == 4 and column == 1:
        tail = window.canvas.frames[2].layers[0]
        assert tail.cell_name == "original"
        assert tail.cell_id == original_cell


def test_new_number_includes_archived_numbers_from_the_same_layer(window):
    source = paint(window)
    window.canvas._sequence_archive[(0, 9)] = source.clone()
    window.canvas._sequence_archive[(1, 99)] = source.clone()

    assert window.timeline_ops.new_number_here(visual_row(window), 2)

    assert window.time_remap.timeline_states(0) == [None, 1, 10, 1, 1, None]


def test_invalid_targets_leave_the_document_and_undo_unchanged(window, monkeypatch):
    paint(window, 1, number=None, exposure=1)
    window.canvas.frames[3].layers[0].is_blank_key = True
    before = snapshot(window)
    undo_calls = []
    monkeypatch.setattr(window.canvas, "push_doc_undo", lambda: undo_calls.append(1))
    row = visual_row(window)

    for target_row, column in [(row, 1), (row, 3), (row, 5),
                               (row, -1), (row, 6), (-1, 1)]:
        assert window.timeline_ops.new_number_here(target_row, column) is False
        assert snapshot(window) == before
        assert window.statusBar().currentMessage()
    assert undo_calls == []


def test_one_document_undo_restores_numbers_and_exposures(window, monkeypatch):
    paint(window)
    before_numbers = window.time_remap.timeline_states(0)
    before_exposures = [frame.layers[0].exposure for frame in window.canvas.frames]
    push = window.canvas.push_doc_undo
    undo_calls = []

    def push_once():
        undo_calls.append(1)
        push()

    monkeypatch.setattr(window.canvas, "push_doc_undo", push_once)
    assert window.timeline_ops.new_number_here(visual_row(window), 2)
    assert undo_calls == [1]

    window.canvas.undo()

    assert window.time_remap.timeline_states(0) == before_numbers
    assert [frame.layers[0].exposure for frame in window.canvas.frames] == before_exposures
