"""#65 の残り：連番画像の読み込み後に初期状態の保持を尋ねる・何も塗らなかった理由。"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.main_window_auto_paint import AutoPaintController


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


def _line_art(tmp_path, count=2):
    """透明の地に黒い枠を描いた連番画像。"""
    from paintmaskanimator import constants

    paths = []
    for index in range(count):
        image = QImage(constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        for x in range(10, 40):
            image.setPixelColor(x + index, 10, QColor("black"))
            image.setPixelColor(x + index, 39, QColor("black"))
        path = tmp_path / f"A{index + 1:04d}.png"
        assert image.save(str(path))
        paths.append(str(path))
    return paths


def _answer(clicked, asked, *, always=False):
    def answer(box):
        if box.objectName() == "importMaterialQuestion":
            next(button for button in box.buttons() if button.text() == "デジタル（TP）").click()
            return 0
        asked.append(box.text())
        if always:
            box.checkBox().setChecked(True)
        button = next(button for button in box.buttons() if button.text() == clicked)
        button.click()
        return 0
    return answer


def test_sequence_import_offers_to_keep_only_the_imported_layer(window, tmp_path, monkeypatch):
    canvas = window.canvas
    other = canvas.frames[0].layers[0]
    other.has_content = True
    asked = []
    monkeypatch.setattr(QMessageBox, "exec", _answer("保存する", asked))

    window.import_dropped_images(_line_art(tmp_path))

    assert asked and "初期状態" in asked[0]
    imported = canvas.active_layer_index
    cells = window.initial_state.layer_cells(imported)
    assert cells and all(
        canvas.frames[frame].layers[layer].initial_image is not None for frame, layer in cells
    )
    assert other.initial_image is None, "ほかのレイヤーには触れない"


def test_declining_keeps_nothing(window, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "exec", _answer("保存しない", asked))

    window.import_dropped_images(_line_art(tmp_path))

    assert asked
    for frame, layer in window.initial_state.layer_cells(window.canvas.active_layer_index):
        assert window.canvas.frames[frame].layers[layer].initial_image is None


def test_always_keep_skips_the_question_next_time(window, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "exec", _answer("保存する", asked, always=True))
    window.import_dropped_images(_line_art(tmp_path))
    asked.clear()

    more = tmp_path / "more"
    more.mkdir()
    window.import_dropped_images(_line_art(more, 1))

    assert not asked
    cells = window.initial_state.layer_cells(window.canvas.active_layer_index)
    assert all(
        window.canvas.frames[frame].layers[layer].initial_image is not None
        for frame, layer in cells
    )


def test_keep_cells_does_not_overwrite_a_kept_initial_state(window):
    canvas = window.canvas
    cell = canvas.frames[0].layers[0]
    cell.has_content = True
    kept = QImage(cell.image)
    kept.fill(QColor("red"))
    cell.initial_image = kept

    assert window.initial_state.keep_cells([(0, 0)]) == 0
    assert QColor(cell.initial_image.pixel(0, 0)) == QColor("red")


def _cell(fill=None, lines=True):
    pixels = np.zeros((100, 100, 4), dtype=np.uint8)
    if fill is not None:
        pixels[:] = (*fill, 255)
    if lines:
        pixels[10, 10:90] = pixels[89, 10:90] = (0, 0, 0, 255)
        pixels[10:90, 10] = pixels[10:90, 89] = (0, 0, 0, 255)
    return pixels


def test_reason_when_the_lines_are_too_light():
    grey = _cell(lines=False)
    grey[10, 10:90] = (90, 90, 90, 255)
    reason = AutoPaintController.nothing_painted_reason([_cell(), grey], {0}, [None, None])
    assert "線として認識" in reason


def test_reason_when_nothing_is_left_unpainted():
    painted = _cell(fill=(200, 180, 220))
    reason = AutoPaintController.nothing_painted_reason([_cell(), painted], {0}, [None, None])
    assert "透明な" in reason and "初期状態" in reason


def test_white_background_is_not_reported_as_painted():
    on_white = _cell(fill=(255, 255, 255))
    assert AutoPaintController.nothing_painted_reason([_cell(), on_white], {0}, [None, None]) == ""
