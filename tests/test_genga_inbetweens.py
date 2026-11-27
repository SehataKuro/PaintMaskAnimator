"""原画のあいだに中割りの○を入れる（#24）。"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from dense_fixtures import hold
from paintmaskanimator.document import create_drawing
from paintmaskanimator.main_window import MainWindow


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


def drawing(canvas, column, number, genga=True):
    layer = canvas.frames[column].layers[0]
    image = layer.image.to_qimage()
    image.fill(QColor(0, 0, 0, 0))
    image.setPixelColor(number, 3, QColor("black"))
    create_drawing(layer, image, number=number)
    layer.is_genga = genga
    return layer


def prepare(window, span=6):
    """原画1（0〜span-1コマ目）と原画2（spanコマ目）が隣り合うシート。"""
    canvas = window.canvas
    canvas.timeline_mode = "sheet"
    canvas._ensure_frame_count(span + 2)
    drawing(canvas, 0, 1)
    hold(canvas, 0, span)
    drawing(canvas, span, 2)
    hold(canvas, span, 2)
    canvas._cell_structure_dirty = True
    window.refresh_ui()
    return canvas


def row(window):
    return len(window.canvas.layers) - 1


def blocks(canvas, end):
    found, column = [], 0
    while column < end:
        kind, start, exposure = canvas.timeline_block_at(column, 0)
        found.append((kind, int(start), int(exposure)))
        column = int(start) + int(exposure)
    return found


def test_inbetweens_split_the_span_evenly_into_blank_keys(window):
    canvas = prepare(window, span=6)
    assert window.timeline_ops.insert_inbetweens(row(window), 0, 2)
    assert blocks(canvas, 6) == [("content", 0, 2), ("blank", 2, 2), ("blank", 4, 2)]
    assert canvas.timeline_block_at(6, 0)[:2] == ("content", 6), "次の原画はそのまま"


def test_one_undo_removes_all_inbetweens(window):
    canvas = prepare(window, span=6)
    before = blocks(canvas, 8)
    window.timeline_ops.insert_inbetweens(row(window), 0, 2)
    canvas.undo()
    assert blocks(canvas, 8) == before


def test_too_many_inbetweens_for_the_span_are_refused(window):
    canvas = prepare(window, span=3)
    before = blocks(canvas, 5)
    assert window.timeline_ops.insert_inbetweens(row(window), 0, 3) is False
    assert blocks(canvas, 5) == before


def test_needs_a_genga_cell_and_a_following_genga(window):
    canvas = prepare(window, span=6)
    canvas.frames[0].layers[0].is_genga = False
    assert window.timeline_ops.insert_inbetweens(row(window), 0, 1) is False
    canvas.frames[0].layers[0].is_genga = True
    assert window.timeline_ops.insert_inbetweens(row(window), 6, 1) is False, "次の原画が無い"


def test_existing_cells_between_the_genga_are_left_alone(window):
    canvas = prepare(window, span=6)
    window.timeline_ops.insert_inbetweens(row(window), 0, 1)
    before = blocks(canvas, 8)
    assert window.timeline_ops.insert_inbetweens(row(window), 0, 1) is False
    assert blocks(canvas, 8) == before


def test_the_menu_asks_for_the_count(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    canvas = prepare(window, span=6)
    monkeypatch.setattr(QInputDialog, "getInt", lambda *_a, **_k: (1, True))
    assert window.timeline_ops.insert_inbetweens_dialog(row(window), 0)
    assert blocks(canvas, 6) == [("content", 0, 3), ("blank", 3, 3)]
