"""統合や色置換を Undo したら、画像と一緒に使用色のフォルダーも戻る。"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor
from image_edit_helpers import edit_qimage
from PySide6.QtWidgets import QApplication

BLACK, BLUE = (0, 0, 0), (32, 80, 192)


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


def refresh(window):
    window.used_color._refresh_without_delay()


def prepare(window):
    layer = window.canvas.frames[0].layers[0]
    with edit_qimage(layer.image) as image:
        for x, rgb in ((5, BLACK), (9, BLUE)):
            image.setPixelColor(x, 5, QColor(*rgb))
    layer.has_content = True
    refresh(window)
    panel = window.palette
    folder = panel.create_category("線")
    panel._select_used_color(BLACK)
    panel._move_colors_to_category(BLACK, folder)
    return layer, panel, folder


def test_undoing_a_merge_puts_the_color_back_in_its_folder(window):
    layer, panel, folder = prepare(window)
    assert panel.category_colors.get(BLACK) == folder

    window.used_color.apply_palette_merge(BLUE, {BLACK, BLUE})
    refresh(window)
    assert layer.image.to_qimage().pixelColor(5, 5) == QColor(*BLUE)
    assert BLACK not in panel.source_buttons

    window.used_color.undo()
    refresh(window)
    assert layer.image.to_qimage().pixelColor(5, 5) == QColor(*BLACK)
    assert panel.category_colors.get(BLACK) == folder
    assert BLACK in panel._category_members(folder)

    window.used_color.redo()
    refresh(window)
    assert layer.image.to_qimage().pixelColor(5, 5) == QColor(*BLUE)
    assert BLACK not in panel.source_buttons


def test_undoing_a_replacement_restores_the_folder_too(window):
    layer, panel, folder = prepare(window)

    window.used_color.apply_palette_replacements({BLACK: (200, 10, 10)})
    refresh(window)
    assert BLACK not in panel.source_buttons

    window.used_color.undo()
    refresh(window)
    assert panel.category_colors.get(BLACK) == folder
