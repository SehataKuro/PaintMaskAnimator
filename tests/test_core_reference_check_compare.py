"""Compare pma-core's reference check (reference_scan) with the Python controller.

同じ文書を、今の ``ReferenceCheckController.collect``（Rust 拡張を使う経路）と
pma-core の ``reference_check_layer`` へ渡し、候補（種類・色・画素数・場所・前後の色・
マスク）と「見本だけの色」の要約が同じになることを確かめる。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from paintmaskanimator import constants, imaging
from paintmaskanimator.document import create_drawing
from paintmaskanimator.document_live import _live_of
from paintmaskanimator.main_window import MainWindow
import pma_native as native

WIDTH, HEIGHT = 160, 120
BLACK = (0, 0, 0, 255)
SKIN = (255, 223, 212, 255)
LEAF = (197, 174, 103, 255)
DRESS = (200, 180, 220, 255)
NOTE = (255, 0, 255, 255)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(constants, "CANVAS_WIDTH", WIDTH)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", HEIGHT)
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()
        qapp.processEvents()


def _frame(fill, shift=0, *, seed=0, note=False, extra=None, hole=False, alpha=None):
    """白地に枠と中の部分、まわりの模様。"""
    rng = np.random.default_rng(seed)
    pixels = np.zeros((HEIGHT, WIDTH, 4), np.uint8)
    pixels[:] = (255, 255, 255, 255)
    for x, y in rng.integers((0, 0), (WIDTH, HEIGHT), (300, 2)):
        pixels[y, x] = (90, 140, 200, 255) if (x + y) % 2 else (120, 200, 90, 255)
    x0, y0 = 30 + shift, 30
    pixels[y0:y0 + 40, x0:x0 + 40] = BLACK
    pixels[y0 + 1:y0 + 39, x0 + 1:x0 + 39] = fill
    pixels[y0 + 1:y0 + 39, x0 + 20] = BLACK
    pixels[y0 + 1:y0 + 39, x0 + 21:x0 + 39] = LEAF
    pixels[80:100, 100 + shift:130 + shift] = DRESS
    pixels[85:95, 110 + shift:118 + shift] = SKIN
    if hole:
        pixels[40:48, x0 + 5:x0 + 12] = 0
    if note:
        pixels[5:8, 5:15] = NOTE
    if extra is not None:
        pixels[10:16, 120:130] = extra
    if alpha is not None:
        pixels[100:104, 10:20] = (*SKIN[:3], alpha)
    return pixels


def _install(window, frames):
    canvas = window.canvas
    canvas._ensure_frame_count(len(frames))
    for frame, pixels in zip(canvas.frames, frames):
        layer = frame.layers[canvas.active_layer_index]
        create_drawing(layer, imaging.rgba_array_to_qimage(np.ascontiguousarray(pixels)),
                       number=layer.sequence_number)


def _core(window):
    document = window.canvas._document
    assert document._promote(), "文書を pma-core で表せない"
    return _live_of(document)["state"].core


def _python(window):
    controller = window.reference_check
    canvas = window.canvas
    cells = controller._cells()
    layer = int(canvas.active_layer_index)
    reference = canvas.resolve_key_frame(int(canvas.current_frame), layer)
    if reference is None or (reference, layer) not in cells:
        return "no_reference", None, [], []
    if len(cells) < 2:
        return "too_few", None, [], []
    entries = controller.collect(cells, (reference, layer))
    rows = []
    for frame, layer_index, finding in entries:
        mask = None if finding.mask is None else np.ascontiguousarray(
            finding.mask, dtype=np.uint8).tobytes()
        rows.append((frame, layer_index, finding.kind, finding.rgb, finding.pixels,
                     finding.bounds, finding.expected, mask))
    return "ok", reference, rows, list(controller.reference_only)


def _assert_same(window):
    canvas = window.canvas
    expected = _python(window)
    actual = native.reference_check_layer(
        _core(window), int(canvas.current_frame), int(canvas.active_layer_index))
    assert actual[0] == expected[0]
    assert actual[1] == expected[1]
    assert actual[3] == expected[3]
    assert actual[2] == expected[2]
    return expected


SCENES = {
    "mismatch": [_frame(SKIN), _frame(LEAF, 2, seed=1), _frame(SKIN, 4, seed=2)],
    "consistent": [_frame(SKIN), _frame(SKIN, 1, seed=1), _frame(SKIN, 2, seed=2),
                   _frame(SKIN, 3, seed=3)],
    "missing_and_extra": [_frame(SKIN, note=True), _frame(SKIN, 1, extra=(1, 2, 250, 255)),
                          _frame(DRESS, 2, hole=True, seed=3), _frame(SKIN, 3, seed=4)],
    "alpha": [_frame(SKIN, alpha=255), _frame(SKIN, 1, alpha=128, seed=5),
              _frame(LEAF, 2, alpha=40, seed=6), _frame(SKIN, 3, seed=7, alpha=128)],
    "two_cells": [_frame(SKIN), _frame(LEAF, 2)],
}


@pytest.mark.parametrize("name", sorted(SCENES))
@pytest.mark.parametrize("current", [0, 1])
def test_reference_check_matches_python(window, name, current):
    _install(window, SCENES[name])
    window.canvas.current_frame = current
    _assert_same(window)


def test_mismatch_scene_finds_a_mismatch(window):
    _install(window, SCENES["mismatch"])
    status, _reference, rows, _only = _assert_same(window)
    assert status == "ok"
    assert any(row[2] == "mismatch" for row in rows)


def test_no_reference_on_an_empty_frame(window):
    _install(window, SCENES["two_cells"])
    window.canvas._ensure_frame_count(4)
    window.canvas.current_frame = 3
    status = native.reference_check_layer(_core(window), 3, 0)[0]
    assert status in ("no_reference", "ok")
    _assert_same(window)
