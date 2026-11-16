"""使用色の置換色のプレビュー（ON/OFF できる、表示だけで画像は変えない）。"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator import config


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from paintmaskanimator.main_window import MainWindow

    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


RED, BLUE = (255, 0, 0), (0, 0, 255)


def prepare(window):
    layer = window.canvas.frames[0].layers[0]
    layer.image.setPixelColor(5, 5, QColor(*RED))
    layer.has_content = True
    window.palette.set_colors([QColor(*RED)])
    return layer


def test_registered_replacements_are_previewed_without_changing_pixels(window):
    layer = prepare(window)
    window.palette.replacement_preview_check.setChecked(True)

    window.palette._set_replacement_color(RED, QColor(*BLUE))

    assert window.canvas.preview_color_remap == {RED: BLUE}
    assert layer.image.pixelColor(5, 5) == QColor(*RED), "画像はまだ変えない"


def test_the_preview_can_be_turned_off_and_is_remembered(window):
    prepare(window)
    window.palette._set_replacement_color(RED, QColor(*BLUE))

    window.palette.replacement_preview_check.setChecked(False)
    assert window.canvas.preview_color_remap == {}
    assert config.get_value(window.palette.REPLACEMENT_PREVIEW_KEY) is False

    window.palette.replacement_preview_check.setChecked(True)
    assert window.canvas.preview_color_remap == {RED: BLUE}


def test_clearing_a_replacement_removes_it_from_the_preview(window):
    prepare(window)
    window.palette.replacement_preview_check.setChecked(True)
    window.palette._set_replacement_color(RED, QColor(*BLUE))

    window.palette.clear_replacement(RED)

    assert window.canvas.preview_color_remap == {}


def test_group_changes_keep_the_replacement_preview(window):
    prepare(window)
    window.palette.replacement_preview_check.setChecked(True)
    window.palette._set_replacement_color(RED, QColor(*BLUE))

    window.colors.set_preview_color_groups({})

    assert window.canvas.preview_color_remap == {RED: BLUE}


def test_undo_restores_the_preview_with_the_replacement(window):
    prepare(window)
    window.palette.replacement_preview_check.setChecked(True)
    window.palette._set_replacement_color(RED, QColor(*BLUE))
    window.palette.clear_replacement(RED)

    window.canvas.undo()

    assert window.canvas.preview_color_remap == {RED: BLUE}
