"""バケツの色置換モード（#67）：クリックした色を、選択レイヤーの全コマで置き換える。"""
from __future__ import annotations

import pytest
import numpy as np
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator.document import create_drawing
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.undo_entries import ScopedBatchUndo

RED = QColor("#ff2020")
SKIN = QColor("#ffe0c0")
NEW = QColor("#3355ff")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def cell(window, frame_index, layer_index=0):
    """左に肌・右に離れた肌の島・黒い線・赤の小さな点があるコマ。"""
    layer = window.canvas.frames[frame_index].layers[layer_index]
    image = layer.image
    image.fill(QColor(0, 0, 0, 0))
    for x in range(2, 10):
        for y in range(2, 10):
            image.setPixelColor(x, y, SKIN)
    for x in range(20, 24):
        for y in range(20, 24):
            image.setPixelColor(x, y, SKIN)  # つながっていない同じ色
    for y in range(0, 30):
        image.setPixelColor(14, y, QColor("black"))
    image.setPixelColor(4 + frame_index, 4, RED)
    create_drawing(layer, image.copy(), number=frame_index + 1)
    return layer


def prepare(window, frame_count=3):
    window.canvas._ensure_frame_count(frame_count)
    for index in range(frame_count):
        cell(window, index)
    window.canvas.current_frame = 0
    window.canvas.active_layer_index = 0
    window.canvas.color_mode = "main"
    window.canvas.main_color = NEW
    window.tools.bucket_replace_color.setChecked(True)


def pixel(window, frame, x, y, layer=0):
    return window.canvas.frames[frame].layers[layer].image.pixelColor(x, y)


def test_replaces_the_exact_colour_in_every_frame_and_undoes_once(window):
    prepare(window)
    before = [frame.layers[0].image.copy() for frame in window.canvas.frames]

    window.canvas.flood_fill(QPoint(3, 3))

    for frame in range(3):
        assert pixel(window, frame, 3, 3) == NEW
        assert pixel(window, frame, 21, 21) == NEW, "つながっていない同じ色も置き換える"
        assert pixel(window, frame, 4 + frame, 4) == RED, "ほかの色は変わらない"
        assert pixel(window, frame, 14, 5) == QColor("black")
        assert pixel(window, frame, 25, 25).alpha() == 0, "透明は塗らない"
    assert isinstance(window.canvas.undo_stack[-1], ScopedBatchUndo)

    window.canvas.undo()
    for frame, original in zip(window.canvas.frames, before):
        assert frame.layers[0].image == original


def test_bucket_options_do_not_widen_the_replacement(window):
    prepare(window)
    tools = window.tools
    tools.bucket_include_sub.setChecked(True)
    tools.bucket_island_replace.setChecked(True)
    tools.bucket_all_frames.setChecked(True)

    window.canvas.flood_fill(QPoint(3, 3))

    for frame in range(3):
        assert pixel(window, frame, 4 + frame, 4) == RED
        assert pixel(window, frame, 14, 5) == QColor("black")


def test_clicking_transparency_paints_nothing(window):
    prepare(window)
    depth = len(window.canvas.undo_stack)

    assert window.canvas.replace_color_at(QPoint(25, 25)) is False

    assert len(window.canvas.undo_stack) == depth
    assert pixel(window, 1, 25, 25).alpha() == 0


def test_line_colours_ask_before_replacing(window):
    prepare(window)
    asked = []

    assert window.canvas.replace_color_at(
        QPoint(14, 5), confirm_line=lambda rgb: asked.append(rgb) or False
    ) is False
    assert asked == [(0, 0, 0)]
    assert pixel(window, 0, 14, 5) == QColor("black")

    assert window.canvas.replace_color_at(QPoint(14, 5), confirm_line=lambda rgb: True)
    assert pixel(window, 2, 14, 5) == NEW


def test_other_layers_and_outside_the_selection_stay(window):
    prepare(window, 2)
    canvas = window.canvas
    if len(canvas.frames[0].layers) < 2:
        canvas.add_layer()
    for index in range(2):
        cell(window, index, layer_index=1)
    canvas.active_layer_index = 0
    image = canvas.frames[0].layers[0].image
    selection = np.zeros((image.height(), image.width()), dtype=bool)
    selection[0:12, 0:12] = True
    assert canvas.set_selection_mask(selection)

    canvas.replace_color_at(QPoint(3, 3))

    for frame in range(2):
        assert pixel(window, frame, 3, 3) == NEW
        assert pixel(window, frame, 21, 21) == SKIN, "選択範囲の外は変わらない"
        assert pixel(window, frame, 3, 3, layer=1) == SKIN, "ほかのレイヤーは変わらない"
