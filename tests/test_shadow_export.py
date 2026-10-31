"""使用色フォルダーを使った影素材の書き出し（Issue #21）。"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import shadow_export  # noqa: E402
from paintmaskanimator.constants import OUTSIDE_MARGIN  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402


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


NORMAL_RGB = (240, 200, 180)
SHADOW_RGB = (160, 130, 170)


def cel():
    pixels = np.zeros((8, 8, 4), dtype=np.uint8)
    pixels[2:6, 2:6, :3] = NORMAL_RGB
    pixels[2:6, 2:6, 3] = 255
    pixels[4:6, 4:6, :3] = SHADOW_RGB
    return pixels


def test_the_normal_material_is_one_flat_colour():
    result = shadow_export.flat_normal(cel())

    assert tuple(result[2, 2, :3]) == shadow_export.DEFAULT_NORMAL_COLOR
    # 影の画素も「絵がある」ので、ノーマルでは同じ色になる。
    assert tuple(result[4, 4, :3]) == shadow_export.DEFAULT_NORMAL_COLOR
    # 絵の無いところは透明のまま。
    assert result[0, 0, 3] == 0


def test_the_shadow_material_holds_only_the_folder_colours():
    result = shadow_export.flat_shadow(cel(), [SHADOW_RGB])

    assert tuple(result[4, 4, :3]) == shadow_export.DEFAULT_SHADOW_COLOR
    assert result[2, 2, 3] == 0


def test_the_mask_is_opaque_black_and_white():
    result = shadow_export.shadow_matte(cel(), [SHADOW_RGB])

    assert tuple(result[4, 4]) == (255, 255, 255, 255)
    assert tuple(result[2, 2]) == (0, 0, 0, 255)
    # マスクは全面不透明。
    assert np.all(result[:, :, 3] == 255)


def test_an_empty_folder_marks_nothing_as_shadow():
    assert not shadow_export.shadow_mask_of(cel(), []).any()


def test_three_materials_are_produced():
    materials = shadow_export.materials(cel(), [SHADOW_RGB])
    assert set(materials) == set(shadow_export.KINDS)


def paint_window_cel(window):
    layer = window.canvas.frames[0].layers[0]
    for y in range(10, 20):
        for x in range(10, 20):
            layer.image.setPixelColor(
                OUTSIDE_MARGIN + x, OUTSIDE_MARGIN + y, QColor(*NORMAL_RGB)
            )
    for y in range(15, 20):
        for x in range(15, 20):
            layer.image.setPixelColor(
                OUTSIDE_MARGIN + x, OUTSIDE_MARGIN + y, QColor(*SHADOW_RGB)
            )
    layer.has_content = True
    layer.sequence_number = 1
    layer.name = "A"


def test_only_folders_holding_colours_are_offered(window):
    paint_window_cel(window)
    window.used_color.refresh()
    assert window.export.shadow_folder_names() == []

    window.palette.create_category("影")
    window.palette._move_colors_to_category(SHADOW_RGB, "影")

    assert window.export.shadow_folder_names() == ["影"]


def test_the_export_writes_three_folders(window, tmp_path):
    paint_window_cel(window)
    window.used_color.refresh()
    window.palette.create_category("影")
    window.palette._move_colors_to_category(SHADOW_RGB, "影")

    assert window.export.write_shadow_sequence("影", tmp_path)

    for kind in shadow_export.KINDS:
        assert (tmp_path / kind / "A" / "A0001.png").is_file()


def test_an_empty_folder_exports_nothing(window, tmp_path):
    paint_window_cel(window)
    window.used_color.refresh()
    window.palette.create_category("空")

    assert not window.export.write_shadow_sequence("空", tmp_path)
    assert not (tmp_path / shadow_export.NORMAL).exists()
