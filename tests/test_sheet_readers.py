"""列の読み出しを移した後も、従来の参照実装と一致する。"""
import os
import random

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator import timesheet_file
from paintmaskanimator.legacy_v1 import raw_v1_slot, snapshot_frames
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.models import Frame
from dense_fixtures import dense_raw
from paintmaskanimator.timeline import TimelineWidget


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    window.canvas._ensure_frame_count(12)
    window.canvas.add_layer()
    try:
        yield window
    finally:
        window.deleteLater()


def random_metadata(rng):
    return dict(
        has_content=rng.choice([False, True]),
        is_blank_key=rng.choice([False, True]),
        sequence_only=rng.choice([False, True]),
        sequence_number=rng.choice([None, 1, 2, 5, 19]),
        exposure=rng.randint(-2, 25),
    )


def reference_gaps(frames, layer_index):
    kinds = [TimelineWidget.timeline_span_at(frames, layer_index, column)[0]
             for column in range(len(frames))]
    covered = [column for column, kind in enumerate(kinds) if kind is not None]
    if not covered:
        return {}
    last = covered[-1]
    gaps = {}
    column = 0
    while column < last:
        if kinds[column] is not None:
            column += 1
            continue
        start = column
        while column < last and kinds[column] is None:
            column += 1
        length = column - start
        cross = not (start > 0 and kinds[start - 1] == "blank")
        for gap_column in range(start, column):
            gaps[gap_column] = (start, length, cross)
    return gaps


def reference_tracks(window):
    duration = window._sheet_duration()
    tracks = []
    names = []
    layer_count = len(window.canvas.frames[0].layers)
    for layer_index in range(layer_count):
        names.append(window.canvas.frames[0].layers[layer_index].name)
        values = []
        for column in range(duration):
            kind, key_column, _exposure = TimelineWidget.timeline_span_at(
                window.canvas.frames, layer_index, column
            )
            if kind == "content":
                if key_column is None:
                    value = timesheet_file.NULL_CELL
                elif column == key_column:
                    number = window.canvas.frames[key_column].layers[layer_index].sequence_number
                    value = str(number) if number is not None else timesheet_file.NULL_CELL
                else:
                    value = timesheet_file.HYPHEN
            elif kind == "blank":
                value = (timesheet_file.NULL_CELL if column == key_column
                         else timesheet_file.HYPHEN)
            else:
                value = timesheet_file.NULL_CELL
            values.append(value)
        tracks.append(values)
    return names, tracks, duration


def reference_states(window, layer_index=None):
    canvas = window.canvas
    if layer_index is None:
        layer_index = int(canvas.active_layer_index)
    layer_index = int(layer_index)
    states = []
    for column in range(len(canvas.frames)):
        if not (0 <= layer_index < len(canvas.frames[column].layers)):
            states.append(None)
            continue
        block = canvas.timeline_block_at(column, layer_index)
        if block is None:
            states.append(None)
            continue
        _kind, start, _exposure = block
        layer = canvas.frames[start].layers[layer_index]
        states.append(int(layer.sequence_number)
                      if layer.has_content and layer.sequence_number is not None else None)
    return states


def test_random_gaps_match_reference(qapp):
    rng = random.Random(31032)
    cases = [[], [Frame([])], [Frame([]), Frame([])]]
    for _ in range(300):
        cases.append([Frame([
            raw_v1_slot("A", QImage(), **random_metadata(rng))
            for _ in range(rng.randrange(5))
        ]) for _ in range(rng.randint(1, 45))])
    for frames in cases:
        frames = dense_raw(frames)
        maximum = max((len(frame.layers) for frame in frames), default=0)
        for layer_index in range(-1, maximum + 2):
            assert TimelineWidget.timeline_gaps(frames, layer_index) == reference_gaps(
                frames, layer_index
            )


def test_random_window_readers_match_reference(window):
    rng = random.Random(31033)
    canvas = window.canvas
    layer_count = len(canvas.frames[0].layers)
    for case in range(60):
        raw = [Frame([raw_v1_slot("A", QImage(), **random_metadata(rng))
                      for _ in range(layer_count)]) for _ in canvas.frames]
        canvas.active_layer_index = case % layer_count
        canvas.playback_range_end = [None, 1, len(canvas.frames) + 7][case % 3]
        if canvas.playback_range_end is None:
            # 終端の露出を必ず frames より先まで伸ばす。
            # Input duration beyond the document remains a v1 boundary value.
            raw[-1].layers[0] = raw_v1_slot("A", QImage(), has_content=True,
                                                sequence_number=20, exposure=9)
        raw_rows = snapshot_frames(raw)
        expected_duration = max(len(raw), *(column + max(1, slot.exposure)
                                for column, row in enumerate(raw_rows) for slot in row
                                if not slot.sequence_only and (slot.has_content or slot.is_blank_key)))
        canvas.install_legacy_frames(raw)
        if canvas.playback_range_end is None:
            assert window._sheet_duration() == expected_duration
            assert window._sheet_duration() > len(canvas.frames)
            assert TimelineWidget.timeline_span_at(canvas.frames, 0, len(canvas.frames))[0] is None
        assert window.export.sheet_tracks() == reference_tracks(window)
        for layer_index in [None, -1, *range(layer_count + 2), "0"]:
            actual = window.time_remap.timeline_states(layer_index)
            assert actual == reference_states(window, layer_index)
            if layer_index in (-1, layer_count, layer_count + 1):
                assert actual == [None] * len(canvas.frames)


@pytest.mark.parametrize("following_number, cursor", [(7, 6), (2, 2)])
def test_pending_collision_keeps_v1_tail_duration_outside_dense_document(window, following_number, cursor):
    from paintmaskanimator.document import project_v1_state

    canvas = window.canvas
    raw = [Frame([raw_v1_slot("A", QImage())]) for _ in range(6)]
    raw[0].layers[0] = raw_v1_slot("A", QImage(), has_content=True,
                                  sequence_number=1, exposure=4)
    raw[2].layers[0] = raw_v1_slot("A", QImage(), has_content=True,
                                  sequence_number=following_number)
    raw[5].layers[0] = raw_v1_slot("A", QImage(), has_content=True,
                                  sequence_number=2, sequence_only=True)
    canvas.install_legacy_frames(raw)
    canvas.timeline_mode = "sequence"
    canvas.active_layer_index = 0
    canvas.current_frame = 5
    window.timeline_ops.set_mode("sheet")
    assert len(canvas.frames) == 7
    assert canvas.current_frame == cursor
    assert canvas.timeline_block_at(6, 0) == ("content", 6, 1)
    projected, _ = project_v1_state(canvas.frames)
    assert projected[0].layers[0].exposure == 2
    assert projected[6].layers[0].exposure == 2
    assert window._sheet_duration() == 8
    assert window.export.sheet_tracks() == reference_tracks(window)
