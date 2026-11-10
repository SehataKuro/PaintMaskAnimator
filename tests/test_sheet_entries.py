"""Phase 3 第1段階の読み出し口が、既存のシート表示と一致する。"""
import os
import random
from copy import deepcopy

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import native  # noqa: E402
from paintmaskanimator.document import clear_pending, mark_pending
from paintmaskanimator.legacy_v1 import LegacyLayerV1, legacy_layer_to_slot
from dense_fixtures import hold, dense_raw  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.models import Frame, Layer  # noqa: E402
from paintmaskanimator.sheet import (  # noqa: E402
    BLANK, EMPTY, UNNUMBERED, SheetEntry, sheet_from_frames, sheet_numbers,
)
from paintmaskanimator.timeline import TimelineWidget  # noqa: E402


def cell(**kwargs):
    return legacy_layer_to_slot(LegacyLayerV1("A", QImage(), **kwargs))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_sequence_only_missing_layers_and_unnumbered():
    frames = [Frame([cell(has_content=True, exposure=4, sequence_number=7),
                     cell(has_content=True, exposure=0)]),
              Frame([cell(has_content=True, is_blank_key=True, sequence_only=True,
                          sequence_number=99)]), Frame([]), Frame([cell()]), Frame([])]
    frames = dense_raw(frames)
    sheet = sheet_from_frames(frames)
    assert [row[0].number for row in sheet] == [7, 7, 7, 7, EMPTY]
    assert [row[1].number for row in sheet] == [UNNUMBERED, EMPTY, EMPTY, EMPTY, EMPTY]
    entries = native.sheet_entries(frames)
    assert [row[0].key for row in entries] == [0, 0, 0, 0, -1]
    assert entries[0][1].kind == 2
    assert entries[0][1].number is None
    assert sheet[0][1].is_cell
    assert sheet[-1][0].is_empty


@pytest.mark.parametrize("blank", [False, True])
def test_new_explicit_key_cuts_long_exposure(blank):
    frames = [Frame([cell(has_content=True, exposure=20, sequence_number=1)]),
              Frame([cell(has_content=not blank, is_blank_key=blank,
                          exposure=2, sequence_number=2)]), Frame([]), Frame([])]
    frames = dense_raw(frames)
    assert [row[0].number for row in sheet_from_frames(frames)] == [
        1, BLANK if blank else 2, BLANK if blank else 2, EMPTY,
    ]
    entry = native.sheet_entries(frames)[2][0]
    assert entry.key == 1
    assert entry.number == (None if blank else 2)


@pytest.mark.parametrize("exposure", [-5, 0, 1])
def test_exposure_is_at_least_one_and_content_wins(exposure):
    frames = [Frame([cell(has_content=True, is_blank_key=True, exposure=exposure)]),
              Frame([cell()])]
    frames = dense_raw(frames)
    assert sheet_from_frames(frames) == [[SheetEntry(UNNUMBERED)], [SheetEntry(EMPTY)]]


def test_empty_input():
    assert sheet_from_frames([]) == []
    assert sheet_from_frames([Frame([]), Frame([])]) == [[], []]
    assert sheet_numbers([Frame([])], 0) == [None]
    assert [repr(value) for value in (BLANK, EMPTY, UNNUMBERED)] == [
        "BLANK", "EMPTY", "UNNUMBERED",
    ]


def test_random_sheets_match_timeline_span_at(qapp):
    rng = random.Random(3103)
    for _ in range(300):
        frames = [Frame([
            cell(has_content=rng.choice([False, True]),
                 is_blank_key=rng.choice([False, True]),
                 exposure=rng.randint(-2, 15),
                 sequence_only=rng.choice([False, True]),
                 sequence_number=rng.choice([None, 1, 2, 5, 19]))
            for _ in range(rng.randrange(5))
        ]) for _ in range(rng.randint(20, 45))]
        layers = max(len(frame.layers) for frame in frames)
        frames = dense_raw(frames)
        sheet = sheet_from_frames(frames)
        raw = native.sheet_entries(frames)
        for column, row in enumerate(sheet):
            assert len(row) == layers
            for index, entry in enumerate(row):
                kind, start, _ = TimelineWidget.timeline_span_at(frames, index, column)
                if kind is None:
                    expected = EMPTY
                elif kind == "blank":
                    expected = BLANK
                else:
                    number = frames[start].layers[index].sequence_number
                    expected = number if number is not None else UNNUMBERED
                assert entry == SheetEntry(expected)
                assert raw[column][index].key == (-1 if start is None else start)
                assert raw[column][index].kind == {None: 0, "blank": 1, "content": 2}[kind]


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    window.canvas._ensure_frame_count(12)
    try:
        yield window
    finally:
        window.deleteLater()


@pytest.mark.parametrize("pattern", ["held", "blank", "archive", "unnumbered"])
def test_numbers_match_time_remap(window, pattern):
    window.canvas.add_layer()
    for index in range(2):
        for frame in window.canvas.frames:
            layer = frame.layers[index]
            layer.has_content = layer.is_blank_key = False
            clear_pending(layer)
            layer.sequence_number = None
        first = window.canvas.frames[0].layers[index]
        first.has_content = True
        first._track.cells.setdefault(int(3 + index), first._cell)
        first.sequence_number = 3 + index
        hold(window.canvas, 0, 8, index)
        next_key = window.canvas.frames[3].layers[index]
        next_key.has_content = pattern != "blank"
        next_key.is_blank_key = pattern == "blank"
        if pattern == "archive":
            mark_pending(next_key)
        else:
            clear_pending(next_key)
        if pattern in ("held", "archive"):
            next_key._track.cells.setdefault(int(8 + index), next_key._cell)
        next_key.sequence_number = None if pattern == "unnumbered" else 8 + index
        hold(window.canvas, 3, 2, index)
        assert sheet_numbers(window.canvas.frames, index) == window.time_remap.timeline_states(index)


def test_read_does_not_change_layers_and_tween_is_deepcopied():
    frames = [Frame([cell(has_content=True, exposure=3, sequence_number=1,
                          tween={"nested": {"values": [1, 2]}}, tween_member="group")]),
              Frame([cell(tween={"ignored": [3]}, tween_member="held")]),
              Frame([cell(is_blank_key=True, exposure=2,
                          tween={"blank": [4]}, tween_member="blank")]), Frame([])]
    frames = dense_raw(frames)
    names = ("has_content", "is_blank_key", "blank_start", "sequence_only",
             "sequence_number", "tween", "tween_member")
    def snapshot():
        return [[deepcopy([getattr(layer, name) for name in names])
                 for layer in frame.layers] for frame in frames]
    before = snapshot()
    revision = Layer.timeline_revision
    sheet = sheet_from_frames(frames)
    sheet_numbers(frames, 0)
    assert snapshot() == before
    assert Layer.timeline_revision == revision
    assert sheet[0][0].tween_member == "group"
    assert sheet[1][0].tween == {"ignored": [3]}
    assert sheet[1][0].tween_member == "held"
    assert sheet[2][0].is_blank
    assert sheet[2][0].tween_member == "blank"
    assert sheet[3][0].tween is None
    sheet[0][0].tween["nested"]["values"].append(99)
    sheet[2][0].tween["blank"].append(99)
    assert snapshot() == before
