"""クリスタ寄りのタイムライン：帯の描画・再生ヘッド・サムネイルの開閉。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.models import make_frame  # noqa: E402
from paintmaskanimator.timeline import (  # noqa: E402
    LayerListDelegate,
    TimelineCellDelegate,
    TimelineRuler,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    window = MainWindow()
    canvas = window.canvas
    names = [layer.name for layer in canvas.frames[0].layers]
    canvas.frames = [make_frame(names) for _ in range(12)]
    # 1(3コマ) 2(4コマ) ✗(2コマ) 3(3コマ)
    for column, number, exposure in ((0, 1, 3), (3, 2, 4), (9, 3, 3)):
        layer = canvas.frames[column].layers[0]
        layer.has_content = True
        layer.sequence_number = number
        layer.exposure = exposure
    blank = canvas.frames[7].layers[0]
    blank.is_blank_key = True
    blank.exposure = 2
    canvas.current_frame = 4
    window.refresh_ui()
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()


def _row(window):
    return window.timeline.table.rowCount() - 1


def test_cells_keep_marker_text_and_carry_labels(window):
    table = window.timeline.table
    row = _row(window)
    # 操作の判定に使う文字は従来どおり。
    assert table.item(row, 3).text() == "2"
    assert table.item(row, 4).text() == "ー"
    assert table.item(row, 6).text() == "→"
    assert table.item(row, 7).text() == "○"
    # 描画用の番号は別に持つ。空セルの先頭は番号なし（✗で描く）。
    assert table.item(row, 3).data(TimelineCellDelegate.LABEL_ROLE) == "2"
    assert table.item(row, 7).data(TimelineCellDelegate.LABEL_ROLE) == ""


def test_pending_tween_marks_the_whole_span(window):
    window.canvas.tween_pending = {
        "layer_index": 0, "key_col": 3, "end_col": 6, "reverse_generation": False,
    }
    window.refresh_ui()
    table = window.timeline.table
    row = _row(window)
    spans = [
        table.item(row, column).data(TimelineCellDelegate.TWEEN_SPAN_ROLE)
        for column in range(0, 9)
    ]
    assert spans == [None, None, None] + ["forward"] * 4 + [None, None]
    # 逆生成でも番号の箱には番号を描く（◆は判定用の文字）。
    window.canvas.tween_pending["reverse_generation"] = True
    window.refresh_ui()
    key = table.item(row, 3)
    assert key.text() == "◆"
    assert key.data(TimelineCellDelegate.LABEL_ROLE) == "2"
    assert key.data(TimelineCellDelegate.TWEEN_SPAN_ROLE) == "reverse"


def test_playhead_and_frame_counter_follow_current_frame(window):
    timeline = window.timeline
    assert isinstance(timeline.table.horizontalHeader(), TimelineRuler)
    assert timeline.table._playhead_column == 4
    assert "005" in timeline.frame_counter.text()
    assert "012" in timeline.frame_counter.text()
    timeline.select_current(8, 0)
    assert timeline.table._playhead_column == 8
    assert "009" in timeline.frame_counter.text()


def test_expanding_a_layer_keeps_name_and_track_rows_aligned(window):
    timeline = window.timeline
    row = _row(window)
    collapsed = timeline.table.rowHeight(row)
    timeline.toggle_layer_expanded(row)
    expanded = timeline.expanded_row_height()
    assert expanded > collapsed
    assert timeline.table.rowHeight(row) == expanded
    item = timeline.layer_list.item(row)
    assert item.sizeHint().height() == expanded
    assert item.data(LayerListDelegate.EXPANDED_ROLE) is True
    assert row in timeline.table._expanded_rows
    # キーの絵をサムネイルの元として覚えている。
    assert (row, 0) in timeline.table._thumb_sources
    # 再描画しても開いたまま。
    window.refresh_ui()
    assert timeline.table.rowHeight(row) == expanded
    timeline.toggle_layer_expanded(row)
    assert timeline.table.rowHeight(row) == collapsed


def test_expand_all_and_zoom_respect_expanded_rows(window):
    timeline = window.timeline
    timeline._toggle_all_expanded()
    assert timeline._all_expanded()
    timeline.set_timeline_zoom_scale(1.5)
    row = _row(window)
    assert timeline.table.rowHeight(row) == timeline.expanded_row_height()
    assert (
        timeline.layer_list.item(row).sizeHint().height()
        == timeline.table.rowHeight(row)
    )
    timeline._toggle_all_expanded()
    assert not timeline._expanded_layers


def test_timeline_paints_expanded_and_collapsed(window):
    timeline = window.timeline
    timeline.resize(900, 220)
    image_before = timeline.table.viewport().grab().toImage()
    timeline.toggle_layer_expanded(_row(window))
    image_after = timeline.table.viewport().grab().toImage()
    assert not image_before.isNull() and not image_after.isNull()
    assert image_before != image_after


def test_chevron_click_toggles_thumbnails(window, qapp):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    timeline = window.timeline
    timeline.resize(900, 220)
    timeline.show()
    qapp.processEvents()
    row = _row(window)
    rect = timeline.layer_list.visualItemRect(timeline.layer_list.item(row))
    QTest.mouseClick(
        timeline.layer_list.viewport(), Qt.MouseButton.LeftButton,
        pos=QPoint(6, rect.center().y()),
    )
    assert timeline._layer_index_for_row(row) in timeline._expanded_layers


def test_ruler_click_and_drag_move_the_current_frame(window, qapp):
    from PySide6.QtCore import QEvent, QPoint, QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest

    timeline = window.timeline
    timeline.resize(900, 220)
    timeline.show()
    qapp.processEvents()
    ruler = timeline.table.horizontalHeader()

    def x_of(column):
        return ruler.sectionViewportPosition(column) + ruler.sectionSize(column) // 2

    QTest.mouseClick(
        ruler.viewport(), Qt.MouseButton.LeftButton,
        pos=QPoint(x_of(7), 10),
    )
    qapp.processEvents()
    assert window.canvas.current_frame == 7
    assert timeline.table._playhead_column == 7

    move = QMouseEvent(
        QEvent.Type.MouseMove, QPointF(x_of(2), 10), QPointF(x_of(2), 10),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(ruler.viewport(), move)
    qapp.processEvents()
    assert window.canvas.current_frame == 2
    # 列ごと選択されない。
    assert len(timeline.table.selectedIndexes()) <= 1


def test_gaps_between_cells_are_drawn_as_blanks(window):
    from paintmaskanimator.timeline import TimelineWidget

    frames = window.canvas.frames
    # 1(0-2) 2(3-6) ✗(7-8) 3(9-11) の 2 を 1 コマ短くし、先頭も空ける。
    frames[3].layers[0].exposure = 3
    frames[0].layers[0].has_content = False
    frames[0].layers[0].sequence_number = None
    gaps = TimelineWidget.timeline_gaps(frames, 0)
    # 先頭の空き（0-2）は ✗ 付き、6 は直後に ✗ が続くので ✗ 付き、
    # 最後のセルより後ろは対象外。
    assert gaps[0] == (0, 3, True)
    assert gaps[6] == (6, 1, True)
    assert 12 not in gaps
    # 空セルの直後の空きは、空セルの続きとして ✗ を描かない。
    frames[7].layers[0].exposure = 1
    gaps = TimelineWidget.timeline_gaps(frames, 0)
    assert gaps[8] == (8, 1, False)

    window.refresh_ui()
    item = window.timeline.table.item(_row(window), 8)
    assert item.data(TimelineCellDelegate.GAP_ROLE) == (8, 1, False)
    # 操作の判定用のデータは空のまま（空きはデータを変えない）。
    assert item.data(Qt.ItemDataRole.UserRole) is None
    assert not frames[8].layers[0].is_blank_key


def test_ruler_can_seek_past_the_last_frame(window, qapp):
    timeline = window.timeline
    frame_count = len(window.canvas.frames)
    timeline.table._ruler_scrubbed(frame_count + 3)
    qapp.processEvents()
    assert window.canvas.current_frame == frame_count + 3
    assert timeline.table._playhead_column == frame_count + 3
    # 延ばしたコマは尺に数えない。
    assert window._sheet_duration() == 12


def test_auto_select_builds_a_selection(window):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QPainter

    canvas = window.canvas
    canvas.current_frame = 0
    canvas.active_layer_index = 0
    image = canvas.active_layer.image
    painter = QPainter(image)
    painter.fillRect(40, 40, 60, 60, QColor("black"))
    painter.end()
    assert canvas.auto_select_region(QPoint(60, 60)) is True
    assert canvas.selection_polygon


def _spans(frames, layer_index=0):
    """(先頭, 種類, 長さ) の並び。種類は数字（絵番号）か "x"（空セル）。"""
    spans = []
    for column, frame in enumerate(frames):
        layer = frame.layers[layer_index]
        if layer.has_content:
            spans.append((column, layer.sequence_number, layer.exposure))
        elif layer.is_blank_key:
            spans.append((column, "x", layer.exposure))
    return spans


def _blank_after_one(window):
    canvas = window.canvas
    names = [layer.name for layer in canvas.frames[0].layers]
    canvas.frames = [make_frame(names) for _ in range(16)]
    for column, number, exposure in ((0, 1, 6), (9, 2, 4)):
        layer = canvas.frames[column].layers[0]
        layer.has_content = True
        layer.sequence_number = number
        layer.exposure = exposure
    blank = canvas.frames[6].layers[0]
    blank.is_blank_key = True
    blank.exposure = 3
    window.refresh_ui()
    return canvas


def test_moving_a_cell_right_pushes_the_blank_after_it(window):
    canvas = _blank_after_one(window)
    assert canvas.move_timeline_cell(0, 0, 1, 0)
    # 1 は長さそのまま、空セルは 1 の後ろへ押し出され、2 の手前で終わる。
    assert _spans(canvas.frames)[:3] == [(1, 1, 6), (7, "x", 2), (9, 2, 4)]


def test_moving_a_cell_left_keeps_the_blank_attached(window):
    canvas = _blank_after_one(window)
    assert canvas.move_timeline_cell(0, 0, 1, 0)
    assert canvas.move_timeline_cell(1, 0, 0, 0)
    assert _spans(canvas.frames)[:3] == [(0, 1, 6), (6, "x", 3), (9, 2, 4)]


def test_blank_fully_covered_by_the_moved_cell_disappears(window):
    canvas = _blank_after_one(window)
    canvas.frames[6].layers[0].exposure = 1
    assert canvas.move_timeline_cell(0, 0, 1, 0)
    assert _spans(canvas.frames)[:2] == [(1, 1, 6), (9, 2, 4)]
