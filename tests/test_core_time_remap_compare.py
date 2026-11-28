"""Compare pma-core's time-remap paste with Qt's ``TimeRemapController`` and paste dialog.

Tauri の画面は、貼り付けた文字の読み取り（After Effects・ToeiDigitalTimeSheet・XDTS）、
紐づけられる列と既定の紐づけ、使う行の絞り込み、選択レイヤー／紐づけたレイヤーへの反映を
pma-core（``export::time_remap_paste``）で行う。Qt の ``parse_text``・
``sheet_columns_from_parsed``・``default_layer_bindings``・``TimeRemapPasteDialog.parsed_result``・
``apply_to_active_layer``・``apply_xdts_layer_bindings`` と同じ結果かを確かめる。
"""

import json
import random

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QMessageBox

from paintmaskanimator import constants, timesheet_file
from paintmaskanimator.document_live import _blank_handle
from paintmaskanimator.errors import OperationError
from paintmaskanimator.i18n import tr
from paintmaskanimator.legacy_v1 import LegacyLayerV1, legacy_layer_to_slot
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.main_window_time_remap import TimeRemapController
from paintmaskanimator.models import Frame
from paintmaskanimator.widgets import (
    TimeRemapPasteDialog,
    default_layer_bindings,
    sheet_columns_from_parsed,
)
from PySide6.QtCore import Qt
from pma_native import pma_native as native
from pma_native.pma_native import _CoreDocument

SIZE = 4


def _ae_text(rng):
    fps = rng.choice(["24", "12", "30", "23.976", "0", "abc"])
    newline = rng.choice(["\n", "\r\n"])
    lines = ["Adobe After Effects 8.0 Keyframe Data", "",
             rng.choice([f"\tUnits Per Second\t{fps}", f"units  per second {fps}", ""]), ""]
    sections = []
    if rng.random() < 0.9:
        keys = ["Time Remap", "\tFrame\tseconds"]
        for _ in range(rng.randint(0, 6)):
            frame = rng.choice([str(rng.randint(-2, 12)), f"{rng.randint(0, 12)}.5",
                                f"{rng.randint(0, 9)}.25"])
            value = rng.choice([f"{rng.uniform(-0.2, 1.0):.6f}", "0", "-1", ".5", "1e-1",
                                f"{rng.randint(0, 9)}"])
            sep = rng.choice(["\t", " ", "  \t"])
            keys.append(f"\t{frame}{sep}{value}" + rng.choice(["", "\textra"]))
        sections.append(keys)
    if rng.random() < 0.6:
        keys = ["Transform\tOpacity", "\tFrame\tpercent"]
        for _ in range(rng.randint(0, 4)):
            keys.append(f"\t{rng.randint(0, 12)}\t{rng.choice([0, 100, 50, -5])}")
        sections.append(keys)
    rng.shuffle(sections)
    for keys in sections:
        lines.extend(keys + [""])
    lines.append("End of Keyframe Data")
    if rng.random() < 0.3:
        lines.append("\t3\t0.5")
    return newline.join(lines)


def _toei_value(rng):
    return rng.choice(["1", "2", "3", "12", "0", " 4 ", "中", "×", "", None, 5, 2.0, True,
                       ["7"], {"a": 1}])


def _toei_text(rng):
    layers = []
    if rng.random() < 0.2:
        layers.append({"frames": []})
    if rng.random() < 0.1:
        layers.append("x")
    frames = []
    for _ in range(rng.randint(0, 8)):
        entry = {"frame": rng.choice([rng.randint(-1, 10), str(rng.randint(0, 10)), None,
                                      "x", 3.7])}
        if rng.random() < 0.9:
            entry["data"] = [
                {"values": rng.choice([[_toei_value(rng)], [_toei_value(rng), "9"], [],
                                       _toei_value(rng)])}
                for _ in range(rng.randint(0, 2))
            ]
        frames.append(entry if rng.random() < 0.95 else "bad")
    layers.append({"frames": frames})
    body = json.dumps({"layers": layers}, ensure_ascii=False)
    return rng.choice(["ToeiDigitalTimeSheet Copy Data\n" + body, body])


def _xdts_text(rng, names=("A", "B")):
    duration = rng.randint(1, 6)
    tracks = [[rng.choice(["1", "2", "3", "5", timesheet_file.HYPHEN, timesheet_file.NULL_CELL,
                           "SYMBOL_TICK_1"]) for _ in range(duration)] for _ in names]
    return timesheet_file.xdts_text("c001", list(names), tracks, duration)


def _texts():
    rng = random.Random(5)
    texts = ["", "   ", "﻿", "hello", "{}", "{\"layers\": [], \"frames\": 1}",
             "ToeiDigitalTimeSheet Copy Data", "ToeiDigitalTimeSheet Copy Data {bad",
             "time remap", "Adobe After Effects\nTime Remap\n\t0\t1e999\n"]
    texts += [_ae_text(rng) for _ in range(60)]
    texts += [_toei_text(rng) for _ in range(60)]
    texts += [_xdts_text(rng) for _ in range(10)]
    return texts


def _python_parse(text):
    try:
        return TimeRemapController.parse_text(text), None
    except OperationError as exc:
        return None, str(exc)
    except OverflowError:
        return None, "overflow"


def test_parse_text_matches_python(qapp):
    for text in _texts():
        expected, error = _python_parse(text)
        try:
            actual = native.time_remap_parse(text)
        except ValueError as exc:
            assert error is not None, text
            if error != "overflow" and "JSON" not in error:
                assert str(exc) == error, text
            continue
        assert error is None, (text, str(actual))
        assert actual == expected, text


def test_columns_and_default_bindings_match_python(qapp):
    rng = random.Random(9)
    layer_sets = [[(0, "A"), (1, "b")], [(0, "x")], [], [(0, " a "), (1, "A"), (2, "B")]]
    for text in _texts():
        parsed, error = _python_parse(text)
        if error is not None:
            continue
        layers = rng.choice(layer_sets)
        active = rng.randrange(3)
        columns = sheet_columns_from_parsed(parsed)
        bindings = default_layer_bindings(columns, layers, active)
        actual_columns, actual_bindings = native.time_remap_columns(text, tr("セル"), layers, active)
        assert actual_columns == columns, text
        assert actual_bindings == list(bindings.items()), text


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", SIZE)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", SIZE)
    w = MainWindow()
    for timer in (w._autosave_timer, w._used_color_timer, w._title_timer):
        timer.stop()
    monkeypatch.setattr(w.used_color, "schedule_refresh", lambda: None)
    monkeypatch.setattr(w.used_color, "refresh_with_counter", lambda *_: None)
    yield w
    w._closing = True
    w.deleteLater()


def _solid(rgb):
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(*rgb))
    return image


def _install(window, rng):
    canvas = window.canvas
    count = rng.randint(2, 5)
    frames = []
    for index in range(count):
        layers = []
        for name in ("A", "B"):
            number = rng.choice([1, 2, 3, 5, None])
            if number is None:
                slot = LegacyLayerV1(name, _solid((0, 0, 0, 0)))
            else:
                slot = LegacyLayerV1(name, _solid((number * 40, index * 30, 90)),
                                     sequence_number=number, has_content=True)
            layers.append(legacy_layer_to_slot(slot))
        frames.append(Frame(layers))
    canvas.install_legacy_frames(frames)
    canvas.timeline_mode = "sheet"
    canvas.current_frame = rng.randrange(len(canvas.frames))
    canvas.active_layer_index = rng.randrange(2)
    canvas.undo_stack.clear()
    window.refresh_ui()
    return canvas


def _graph(core):
    text, images = core.to_graph()
    graph = json.loads(text)
    order = {frame["id"]: index for index, frame in enumerate(graph["frames"])}
    for frame in graph["frames"]:
        frame["id"] = order[frame["id"]]
    graph.pop("next_frame_id")
    pixels = [(i.width, i.height, i.rgba_bytes()) for i in images]

    def resolve(value):
        if isinstance(value, dict):
            return {
                key: (pixels[item] if key in ("image", "initial_image") and isinstance(item, int)
                      else resolve(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    return resolve(graph)


def _python_apply(window, monkeypatch, text, included, binding_changes):
    """show_paste_dialog と同じ手順（窓は使わずに行と紐づけを決める）。"""
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda _parent, _title, message, *_: asked.append(message)
        or QMessageBox.StandardButton.Yes))
    dialog = TimeRemapPasteDialog(text, window, parse_text=TimeRemapController.parse_text,
                                  canvas=window.canvas)
    dialog._update_timesheet_preview()
    for row, keep in enumerate(included):
        item = dialog.preview_table.item(row, 0)
        if item is not None:
            item.setCheckState(Qt.CheckState.Checked if keep else Qt.CheckState.Unchecked)
    for uid, layer in binding_changes:
        dialog._set_column_layer_binding(uid, layer)
    bindings = list(dialog._column_layer_bindings.items())
    parsed = dialog.parsed_result()
    dialog.deleteLater()
    if parsed is None:
        return bindings, None, None
    try:
        if str(parsed.get("format", "")).startswith("XDTS") and parsed.get("sheet_columns"):
            ok = window.importer.apply_xdts_layer_bindings(parsed)
        else:
            ok = window.time_remap.apply_to_active_layer(parsed)
    except OperationError as exc:
        return bindings, None, str(exc)
    assert ok
    return bindings, (asked[0], window.statusBar().currentMessage()), None


@pytest.mark.parametrize("seed", range(48))
def test_apply_matches_python(window, monkeypatch, seed):
    rng = random.Random(seed)
    canvas = _install(window, rng)
    kind = rng.choice(["ae", "toei", "xdts", "xdts"])
    if kind == "ae":
        text = ("Adobe After Effects 8.0 Keyframe Data\n\n\tUnits Per Second\t24\n\nTime Remap\n"
                "\tFrame\tseconds\n" + "".join(
                    f"\t{frame}\t{rng.choice([0, 1, 2, 4]) / 24:.6f}\n"
                    for frame in sorted(rng.sample(range(6), rng.randint(1, 4))))
                + "\nEnd of Keyframe Data\n")
    elif kind == "toei":
        text = _toei_text(rng)
    else:
        text = _xdts_text(rng, rng.choice([("A", "B"), ("B",), ("X", "A"), ("Q",)]))
    parsed, error = _python_parse(text)
    if error is not None:
        return
    rows = max([len(c.get("display_values", [])) for c in sheet_columns_from_parsed(parsed)] + [0])
    included = [rng.random() < 0.8 for _ in range(rows)]
    changes = []
    columns = sheet_columns_from_parsed(parsed)
    for _ in range(rng.randint(0, 2)):
        bindable = [c["uid"] for c in columns if c.get("bindable")]
        if bindable:
            changes.append((rng.choice(bindable), rng.choice([None, 0, 1, 5])))

    document = canvas._document
    graph_text, images = document._live()["core"].to_graph()
    rust = _CoreDocument.from_graph(graph_text, images)
    rust.set_blank_image(_blank_handle())
    layer = canvas.active_layer_index
    current = canvas.current_frame
    names = [str(slot.name) for slot in canvas.frames[current].layers]

    bindings, done, problem = _python_apply(window, monkeypatch, text, included, changes)
    try:
        actual = native.time_remap_apply(rust, text, tr("セル"), included, bindings, layer,
                                         current, names)
    except ValueError as exc:
        assert problem == str(exc)
        return
    assert problem is None
    if done is None:
        assert actual is None
        return
    message, status, _notices = actual
    assert (message, status) == done
    assert _graph(rust) == _graph(document._live()["core"])
