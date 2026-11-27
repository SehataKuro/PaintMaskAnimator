"""Compare pma-core's auto paint (auto_plan) with the Python controller.

同じ文書を、今の ``AutoPaintController.run``（Rust 拡張を使う CPU の経路）と
pma-core の ``auto_paint_layer`` へ渡し、塗ったあとの各セルの画素と、確かめたい所の
一覧（種類・色・画素数・場所・マスク）が同じになることを確かめる。
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
from paintmaskanimator.pma_image import image_rgba
import pma_native as native

from test_auto_paint import BLUSH, LEAF, cheek, figure, overpainted, with_box

WIDTH, HEIGHT = 160, 120
BLACK = (0, 0, 0, 255)
RED = (255, 0, 0, 255)


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


def _install(window, cells):
    canvas = window.canvas
    canvas._ensure_frame_count(len(cells))
    for frame, pixels in zip(canvas.frames, cells):
        layer = frame.layers[canvas.active_layer_index]
        full = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
        full[:pixels.shape[0], :pixels.shape[1]] = pixels
        create_drawing(layer, imaging.rgba_array_to_qimage(full), number=layer.sequence_number)


def _core(window):
    document = window.canvas._document
    assert document._promote(), "文書を pma-core で表せない"
    return _live_of(document)["state"].core


def _images(window):
    canvas = window.canvas
    layer = canvas.active_layer_index
    return [np.ascontiguousarray(image_rgba(frame.layers[layer].image)).copy()
            for frame in canvas.frames]


def _rows(entries):
    rows = []
    for frame, layer, finding in entries:
        mask = None if finding.mask is None else np.ascontiguousarray(
            finding.mask, dtype=np.uint8).tobytes()
        rows.append((frame, layer, finding.kind, finding.rgb, finding.pixels,
                     finding.bounds, finding.expected, mask))
    return rows


def _assert_same(window, monkeypatch, references):
    canvas = window.canvas
    layer = int(canvas.active_layer_index)
    core = _core(window).copy()
    before = _images(window)
    shown = []
    monkeypatch.setattr(window.reference_check, "show_list",
                        lambda entries, *args, **kwargs: shown.extend(entries))
    options = canvas.bucket_options(respect_modifiers=False)
    cells = window.auto_paint.cells()
    painted = window.auto_paint.run(cells, references[0], reference_indices=list(references))
    after = _images(window)

    status, writes, notes, written, _refs, _rounds = native.auto_paint_layer(
        core, layer, list(references),
        island_max_width=int(options.island_max_width), close_gap=bool(options.close_gap),
        gap_width=int(options.gap_width), extend_lines=bool(options.extend_lines),
        extend_length=int(options.extend_length), require_closed=bool(options.require_closed),
    )
    assert status == "ok"
    assert bool(writes) == painted
    result = [image.copy() for image in before]
    for frame, write_layer, (x, y, width, height), pixels in writes:
        assert write_layer == layer
        patch = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 4)
        result[frame][y:y + height, x:x + width] = patch
    for frame, (expected, actual) in enumerate(zip(after, result)):
        assert np.array_equal(expected, actual), f"コマ {frame} の画素が違う"
    assert notes == _rows(shown)
    return writes, notes, written


PALETTE = [(255, 241, 232), (197, 174, 103), (200, 180, 220), (255, 223, 212), (240, 167, 160),
           (180, 210, 230), (230, 220, 150), (170, 200, 170)]


def grid(dx, painted=False, *, dy=0):
    """黒い枠の箱を並べた絵。偶数列の箱は赤の線で左右に分ける（色を選ぶ部分が多い場面）。"""
    pixels = np.zeros((HEIGHT, WIDTH, 4), np.uint8)
    for column in range(5):
        for row in range(4):
            x0, y0 = 6 + column * 30 + dx + row, 6 + row * 28 + dy
            x1, y1 = x0 + 24, y0 + 22
            split = column % 2 == 0
            left = PALETTE[(column + row * 5) % len(PALETTE)]
            right = PALETTE[(column + row * 5 + 3) % len(PALETTE)]
            if painted:
                pixels[y0:y1, x0:x1] = (*left, 255)
                if split:
                    pixels[y0:y1, x0 + 12:x1] = (*right, 255)
            pixels[y0, x0:x1] = pixels[y1 - 1, x0:x1] = BLACK
            pixels[y0:y1, x0] = pixels[y0:y1, x1 - 1] = BLACK
            if split:
                pixels[y0 + 1:y1 - 1, x0 + 11] = (*left, 255) if painted else RED
            pixels[y0 + 4:y0 + 6, x0 + 4:x0 + 6] = BLACK
    return pixels


def _scene(name, painted):
    """場面のセルと見本。``painted`` が偽なら、見本も塗る前の線だけの絵。"""
    if name == "figure":
        return [figure(0, painted), figure(2), figure(4), figure(6)], (0,)
    if name == "middle":
        return [figure(0), figure(2, painted), figure(4)], (1,)
    if name == "cheek":
        return [cheek(0, painted), cheek(2), cheek(4), cheek(6)], (0,)
    if name == "notes":
        reference = figure(0, painted)
        if painted:
            reference = with_box(reference, 40, 36, 8, fill=LEAF)
        return [reference, with_box(figure(2), 15, 36, 14, fill=LEAF),
                with_box(figure(4), 16, 36, 10)], (0,)
    if name == "grid":
        return [grid(0, painted), grid(1), grid(2, dy=1), grid(3, dy=1), grid(5), grid(6)], (0,)
    assert name == "two_references"
    last = with_box(figure(8, painted), 23, 35, 12, fill=BLUSH if painted else None)
    return [figure(0, painted), figure(2), figure(4), with_box(figure(6), 21, 35, 12), last], (0, 4)


NAMES = ["cheek", "figure", "grid", "middle", "notes", "two_references"]


@pytest.mark.parametrize("name", NAMES)
def test_auto_paint_matches_python(window, monkeypatch, name):
    cells, references = _scene(name, True)
    _install(window, cells)
    _assert_same(window, monkeypatch, references)


def test_figure_scene_paints_and_lists_spots(window, monkeypatch):
    cells, references = _scene("notes", True)
    _install(window, cells)
    writes, notes, written = _assert_same(window, monkeypatch, references)
    assert writes and notes and written


@pytest.mark.parametrize("name", NAMES)
def test_auto_paint_with_initial_state_matches_python(window, monkeypatch, name):
    lines, _references = _scene(name, False)
    _install(window, lines)
    window.initial_state.keep(confirm=False)  # 塗る前の線だけの絵を保持
    cells, references = _scene(name, True)
    _install(window, cells)
    _assert_same(window, monkeypatch, references)


def test_overpainted_cell_is_repainted_from_the_initial_state(window, monkeypatch):
    _install(window, [figure(0), figure(2), figure(4)])
    window.initial_state.keep(confirm=False)
    _install(window, [figure(0, painted=True), overpainted(2), overpainted(4)])
    writes, _notes, _written = _assert_same(window, monkeypatch, (0,))
    assert writes


SETTINGS = {
    "plain": dict(island=False, width=1, close_gap=False, closed=False, extend=False),
    "islands_closed": dict(island=True, width=3, close_gap=True, closed=True, extend=True),
    "close_gap": dict(island=False, width=1, close_gap=True, closed=False, extend=False),
}


@pytest.mark.parametrize("scene", ["cheek", "grid", "notes", "two_references"])
@pytest.mark.parametrize("settings", sorted(SETTINGS))
@pytest.mark.parametrize("initial", [False, True])
def test_bucket_options_match_python(window, monkeypatch, scene, settings, initial):
    values = SETTINGS[settings]
    tools = window.canvas._bucket_tools()
    tools.bucket_island_fill.setChecked(values["island"])
    tools.bucket_island_fill_width.setValue(values["width"])
    tools.bucket_close_gap.setChecked(values["close_gap"])
    tools.bucket_require_closed.setChecked(values["closed"])
    tools.bucket_extend_lines.setChecked(values["extend"])
    options = window.canvas.bucket_options(respect_modifiers=False)
    assert options.close_gap == values["close_gap"]
    assert options.require_closed == values["closed"]
    assert options.extend_lines == values["extend"]
    if initial:
        _install(window, _scene(scene, False)[0])
        window.initial_state.keep(confirm=False)
    cells, references = _scene(scene, True)
    _install(window, cells)
    writes, _notes, _written = _assert_same(window, monkeypatch, references)
    if settings == "plain" and (initial or scene != "cheek"):
        assert writes, "塗り残しを拾わない設定では塗る"


def test_too_few_cells(window):
    _install(window, [figure(0, painted=True)])
    status, writes, *_rest = native.auto_paint_layer(_core(window), 0, [0])
    assert status == "too_few" and writes == []
