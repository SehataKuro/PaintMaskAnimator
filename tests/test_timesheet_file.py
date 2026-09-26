"""XDTS / TDTS time sheet reading and writing."""
import json

import pytest

from paintmaskanimator import timesheet_file as tf
from paintmaskanimator.errors import OperationError
from paintmaskanimator.main_window_import import ImportController

N, H = tf.NULL_CELL, tf.HYPHEN
# A: 1 for 3 frames, 2 for 2, then empty. B: empty, then 1 to the end.
TRACKS = [["1", H, H, "2", H, N, N], [N, N, "1", H, H, H, H]]


def _payload(text):
    signature, _, body = text.partition("\n")
    return signature, json.loads(body)


def test_tdts_lists_only_changes_and_ends_with_a_null_cell():
    text = tf.tdts_text("c002", ["A", "B"], TRACKS, 7, cut="2", episode="", scene="")
    signature, payload = _payload(text)
    assert signature == tf.TDTS_SIGNATURE
    assert payload["version"] == 11
    sheet = payload["timeSheets"][0]
    assert sheet["header"]["cut"] == "2"
    table = sheet["timeTables"][0]
    assert table["name"] == "c002" and table["duration"] == 7
    assert table["timeTableHeaders"][0] == {"fieldId": 0, "names": ["A", "B"]}

    def changes(track):
        return [(f["frame"], f["data"][0]["values"][0]) for f in track["frames"]]

    first, second = table["fields"][0]["tracks"]
    assert changes(first) == [(0, "1"), (3, "2"), (5, N)]
    # Already empty at the end: no second end marker.
    assert changes(second) == [(0, N), (2, "1"), (7, N)]
    assert first["frames"][0]["data"][0]["fontColorId"] == 0


def test_xdts_keeps_one_entry_per_frame():
    _signature, payload = _payload(tf.xdts_text("PMA", ["A", "B"], TRACKS, 7))
    track = payload["timeTables"][0]["fields"][0]["tracks"][0]
    assert [f["data"][0]["values"][0] for f in track["frames"]] == TRACKS[0]
    assert payload["version"] == 5


@pytest.mark.parametrize("fmt", tf.FORMATS)
def test_both_formats_load_to_the_same_table_shape(fmt):
    text = tf.sheet_text(fmt, "c002", ["A", "B"], TRACKS, 7)
    table = tf.load_time_table("﻿" + text)
    assert table["duration"] == 7
    assert table["fields"][0]["fieldId"] == 0


@pytest.mark.parametrize("fmt", tf.FORMATS)
def test_parser_holds_a_cell_through_frames_without_an_entry(fmt):
    parsed = ImportController._parse_xdts_timesheet(
        tf.sheet_text(fmt, "c002", ["A", "B"], TRACKS, 7)
    )
    assert parsed["states"] == [1, 1, 1, 2, 2, None, None]
    assert parsed["format"].startswith(fmt.upper())


def test_unknown_signature_is_rejected():
    with pytest.raises(OperationError):
        tf.load_time_table("somethingElse\n{}")
