"""列の読み出しを移した後も、従来の参照実装と一致する。"""
import os
import random

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator import timesheet_file
from paintmaskanimator.document import clear_pending, mark_pending
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.models import Frame, Layer
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
            Layer("A", QImage(), **random_metadata(rng))
            for _ in range(rng.randrange(5))
        ]) for _ in range(rng.randint(1, 45))])
    for frames in cases:
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
        for frame in canvas.frames:
            for layer in frame.layers:
                for name, value in random_metadata(rng).items():
                    if name == "sequence_only":
                        (mark_pending if value else clear_pending)(layer)
                    else:
                        setattr(layer, name, value)
        canvas.active_layer_index = case % layer_count
        canvas.playback_range_end = [None, 1, len(canvas.frames) + 7][case % 3]
        if canvas.playback_range_end is None:
            # 終端の露出を必ず frames より先まで伸ばす。
            last = canvas.frames[-1].layers[0]
            last.has_content = True
            clear_pending(last)
            last.exposure = 9
            assert window._sheet_duration() > len(canvas.frames)
        assert window.export.sheet_tracks() == reference_tracks(window)
        for layer_index in [None, -1, *range(layer_count + 2), "0"]:
            actual = window.time_remap.timeline_states(layer_index)
            assert actual == reference_states(window, layer_index)
            if layer_index in (-1, layer_count, layer_count + 1):
                assert actual == [None] * len(canvas.frames)
