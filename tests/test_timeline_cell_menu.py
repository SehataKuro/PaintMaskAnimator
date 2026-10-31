"""タイムラインのセル右クリックメニューと、その操作（Issue #48）。"""
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
    try:
        yield window
    finally:
        window.deleteLater()


def visual_row(window, layer_index=0):
    return len(window.canvas.layers) - 1 - layer_index


def paint(window, column, layer_index=0, color="#ff0000"):
    layer = window.canvas.frames[column].layers[layer_index]
    layer.image.setPixelColor(10, 10, QColor(color))
    layer.has_content = True
    return layer


def test_a_new_animation_cell_marks_the_cell_as_drawn(window):
    window.timeline_ops.new_animation_cell(visual_row(window), 2)

    layer = window.canvas.frames[2].layers[0]
    assert layer.has_content
    assert layer.exposure == 1


def test_clearing_a_cell_leaves_it_unused_without_closing_the_gap(window):
    paint(window, 1)
    paint(window, 3)

    assert window.timeline_ops.clear_cell(visual_row(window), 1)

    assert not window.canvas.frames[1].layers[0].has_content
    # 後ろのセルは動かない。
    assert window.canvas.frames[3].layers[0].has_content


def test_cut_and_paste_moves_the_drawing(window):
    paint(window, 1, color="#123456")

    assert window.timeline_ops.cut_cell_to_clipboard(visual_row(window), 1)
    assert not window.canvas.frames[1].layers[0].has_content

    assert window.timeline_ops.paste_cell_from_clipboard(visual_row(window), 4)

    pasted = window.canvas.frames[4].layers[0]
    assert pasted.has_content
    assert pasted.image.pixelColor(10, 10) == QColor("#123456")


def test_pasting_without_a_clipboard_is_refused(window):
    assert not window.timeline_ops.paste_cell_from_clipboard(visual_row(window), 2)


def test_copying_lets_the_menu_offer_paste(window):
    assert not window.timeline.table.cell_clipboard_filled
    paint(window, 1)

    window.timeline_ops.copy_cell_to_clipboard(visual_row(window), 1)

    assert window.timeline.table.cell_clipboard_filled


def test_splitting_a_clip_cuts_the_exposure_in_two(window):
    layer = paint(window, 1)
    layer.exposure = 4

    assert window.timeline_ops.split_clip(visual_row(window), 3)

    assert window.canvas.frames[1].layers[0].exposure == 2
    tail = window.canvas.frames[3].layers[0]
    assert tail.has_content and tail.exposure == 2


def test_splitting_at_the_head_is_refused(window):
    layer = paint(window, 1)
    layer.exposure = 3

    assert not window.timeline_ops.split_clip(visual_row(window), 1)
    assert window.canvas.frames[1].layers[0].exposure == 3


def test_merging_swallows_the_cell_into_the_one_before(window):
    first = paint(window, 1)
    first.exposure = 2
    paint(window, 3)

    assert window.timeline_ops.merge_clip(visual_row(window), 3)

    assert window.canvas.frames[1].layers[0].exposure == 3
    assert not window.canvas.frames[3].layers[0].has_content


def test_merging_with_nothing_before_is_refused(window):
    paint(window, 0)
    assert not window.timeline_ops.merge_clip(visual_row(window), 0)


def test_inserting_a_frame_pushes_the_later_cells_right(window):
    paint(window, 2, color="#00ff00")

    assert window.timeline_ops.insert_frame_at(visual_row(window), 2)

    assert not window.canvas.frames[2].layers[0].has_content
    assert window.canvas.frames[3].layers[0].image.pixelColor(10, 10) == QColor(
        "#00ff00"
    )


def test_deleting_a_frame_pulls_the_later_cells_left(window):
    paint(window, 2, color="#0000ff")

    assert window.timeline_ops.delete_frame_at(visual_row(window), 1)

    assert window.canvas.frames[1].layers[0].image.pixelColor(10, 10) == QColor(
        "#0000ff"
    )


def test_the_menu_sets_the_playback_range(window):
    window.timeline_ops.resize_range("start", 2)
    window.timeline_ops.resize_range("end", 5)

    assert window.canvas.playback_range_start == 2
    assert window.canvas.playback_range_end == 5
