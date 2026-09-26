"""Time sheet files: XDTS (exchange) and TDTS (Toei Digital Timesheet).

Both are a one-line signature followed by JSON, and share the time-table
structure (``duration`` / ``timeTableHeaders`` / ``fields`` → ``tracks`` →
``frames``). TDTS wraps the tables in ``timeSheets[0]`` together with a sheet
header (作品情報・作業伝票) and adds display settings to each table.

A track lists only the frames where the instruction changes; a frame with no
entry continues the previous one. The sheet ends with a ``SYMBOL_NULL_CELL``
at ``frame == duration``.
"""
from __future__ import annotations

import json

from .errors import OperationError
from .i18n import tr

XDTS_SIGNATURE = "exchangeDigitalTimeSheet Save Data"
TDTS_SIGNATURE = "toeiDigitalTimeSheet Save Data"
XDTS_VERSION = 5
TDTS_VERSION = 11
#: ``.xtds`` is a common misspelling that some tools write.
SUFFIXES = (".xdts", ".xtds", ".tdts")
FORMATS = ("xdts", "tdts")

NULL_CELL = "SYMBOL_NULL_CELL"
HYPHEN = "SYMBOL_HYPHEN"


def load_time_table(raw_text):
    """The first time table of an XDTS or TDTS file, as a dict."""
    text = str(raw_text or "").lstrip("﻿")
    signature, _, body = text.partition("\n")
    signature = signature.strip()
    if signature not in (XDTS_SIGNATURE, TDTS_SIGNATURE):
        raise OperationError(tr("XDTS／TDTSの先頭識別文字列が一致しません。"))
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OperationError(tr("タイムシートのJSONを解析できません。\n{exc}").format(exc=exc)) from exc
    if not isinstance(payload, dict):
        raise OperationError(tr("タイムシート情報がありません。"))
    if signature == XDTS_SIGNATURE:
        if int(payload.get("version", -1)) != XDTS_VERSION:
            raise OperationError(tr("対応しているXDTSバージョンは5です。"))
        tables = payload.get("timeTables") or []
    else:
        sheets = payload.get("timeSheets") or []
        tables = (sheets[0].get("timeTables") or []) if sheets and isinstance(sheets[0], dict) else []
    if not tables or not isinstance(tables[0], dict):
        raise OperationError(tr("タイムシート情報がありません。"))
    return tables[0]


def format_label(raw_text):
    """``"TDTS version 11"`` or ``"XDTS version 5"``, for display."""
    if str(raw_text or "").lstrip("\ufeff").startswith(TDTS_SIGNATURE):
        return f"TDTS version {TDTS_VERSION}"
    return f"XDTS version {XDTS_VERSION}"


def _frames(values, *, sparse):
    """Frame entries for one track; *values* has one instruction per frame."""
    frames = []
    previous = None
    for frame, value in enumerate(values):
        if sparse and (value == HYPHEN or (value == NULL_CELL and previous == NULL_CELL)):
            continue
        entry = {"id": 0, "values": [value]}
        if sparse:
            entry = {"fontColorId": 0, **entry}
        frames.append({"frame": frame, "data": [entry]})
        if value != HYPHEN:
            previous = value
    if sparse and previous != NULL_CELL:
        frames.append({
            "frame": len(values),
            "data": [{"fontColorId": 0, "id": 0, "values": [NULL_CELL]}],
        })
    return frames


def xdts_text(name, layer_names, tracks, duration):
    """XDTS v5 text. *tracks* holds one instruction per frame for each layer."""
    payload = {
        "timeTables": [{
            "duration": duration,
            "name": name,
            "timeTableHeaders": [{"fieldId": 0, "names": list(layer_names)}],
            "fields": [{
                "fieldId": 0,
                "tracks": [
                    {"trackNo": index, "frames": _frames(values, sparse=False)}
                    for index, values in enumerate(tracks)
                ],
            }],
        }],
        "version": XDTS_VERSION,
    }
    return XDTS_SIGNATURE + "\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


# The defaults Toei's application writes for an empty sheet; the other
# fields' header names are what it shows for those columns.
_FONT_COLORS = [[0, 0, 0], [224, 0, 0], [32, 128, 32], [32, 32, 192], [192, 32, 192], [255, 128, 32]]
_DEFAULT_HEADERS = [
    {"fieldId": 1, "names": [""]},
    {"fieldId": 2, "names": [""]},
    {"fieldId": 3, "names": ["S1", "S2"]},
    {"fieldId": 4, "names": ["a", "b", "c", "d", "e", "f", "g"]},
    {"fieldId": 5, "names": ["1", "2"]},
]
_DUMMY_KOMAS = 24


def _work_slip():
    process = {"assistant": "", "chief": "", "director": "", "free": "", "freeRole": ""}
    track = {"artist": "", "numberOfSheets": 0}
    return {
        "memo": "",
        "processes": [dict(process) for _ in range(3)],
        "works": [
            {"enable": False, "tracks": [dict(track) for _ in range(7)]}
            for _ in range(2)
        ],
    }


def tdts_text(name, layer_names, tracks, duration, *, cut="", episode="", scene=""):
    """TDTS v11 text, laid out as Toei's application saves it."""
    payload = {
        "timeSheets": [{
            "free": [],
            "header": {
                "cut": str(cut),
                "episode": str(episode),
                "scene": str(scene),
                "showHeadDummy": False,
                "timeTableFontColors": [list(color) for color in _FONT_COLORS],
                "workSlip": _work_slip(),
            },
            "timeTables": [{
                "color": 0,
                "direction": "",
                "duration": duration,
                "fields": [{
                    "fieldId": 0,
                    "tracks": [
                        {"frames": _frames(values, sparse=True), "trackNo": index}
                        for index, values in enumerate(tracks)
                    ],
                }],
                "footDummykomas": _DUMMY_KOMAS,
                "headDummykomas": _DUMMY_KOMAS,
                "name": name,
                "operatorName": "",
                "timeTableHeaders": [
                    {"fieldId": 0, "names": list(layer_names)},
                    *[dict(header, names=list(header["names"])) for header in _DEFAULT_HEADERS],
                ],
            }],
        }],
        "version": TDTS_VERSION,
    }
    return TDTS_SIGNATURE + "\n" + json.dumps(payload, ensure_ascii=False, indent=4) + "\n"


def sheet_text(fmt, name, layer_names, tracks, duration, **header):
    """The time sheet in *fmt* (``"xdts"`` or ``"tdts"``)."""
    if fmt == "tdts":
        return tdts_text(name, layer_names, tracks, duration, **header)
    return xdts_text(name, layer_names, tracks, duration)
