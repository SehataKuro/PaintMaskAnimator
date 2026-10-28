"""キャンバスの表示プレビュー（露光量・チャンネル・隣接色分け）のテスト。"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractSpinBox, QApplication

from paintmaskanimator import view_preview
from paintmaskanimator.view_preview import ViewPreview

NORMAL = (232, 196, 172)
SHADOW = (229, 193, 170)  # ノーマルとほとんど同じ影色


@pytest.mark.parametrize("horizontal,vertical", [(True, False), (False, True), (True, True)])
def test_flip_preview_keeps_pointer_coordinates_and_exports(window, horizontal, vertical):
    canvas = window.canvas
    painted_layer(window)
    before = window.crop_image(0)
    window.flip_horizontal_button.setChecked(horizontal)
    window.flip_vertical_button.setChecked(vertical)
    assert canvas.flip_horizontal == horizontal
    assert canvas.flip_vertical == vertical
    canvas.rotation = 31.0
    point = QPointF(123.5, 98.25)
    recovered = canvas.widget_to_canvas(canvas.canvas_to_widget(point))
    assert recovered.x() == pytest.approx(point.x())
    assert recovered.y() == pytest.approx(point.y())
    assert window.crop_image(0) == before


def canvas_pixels(height=40, width=60):
    return np.full((height, width, 4), 255, dtype=np.uint8)


def test_exposure_scales_by_stops_and_clips():
    lut = view_preview.exposure_lut(1.0)
    assert lut[100] == 200 and lut[200] == 255
    assert view_preview.exposure_lut(-1.0)[200] == 100
    assert (view_preview.exposure_lut(0.0) == np.arange(256)).all()


def test_channels_show_one_channel_as_gray():
    pixels = np.array([[[10, 20, 30, 255], [0, 0, 0, 0]]], dtype=np.uint8)
    green = view_preview.adjust_pixels(pixels.copy(), ViewPreview(channel="green"))
    assert tuple(green[0, 0]) == (20, 20, 20, 255)
    alpha = view_preview.adjust_pixels(pixels.copy(), ViewPreview(channel="alpha"))
    assert tuple(alpha[0, 0]) == (255, 255, 255, 255)
    assert alpha[0, 1, 3] == 0, "透明はそのまま（黒い背景が見える）"
    assert view_preview.background_color((255, 255, 255), ViewPreview(channel="alpha")) == (0, 0, 0)
    assert view_preview.background_color((200, 100, 50), ViewPreview(channel="red")) == (200, 200, 200)


def test_map_colors_separate_nearly_identical_neighbours():
    pixels = canvas_pixels()
    pixels[5:35, 5:30, :3] = NORMAL
    pixels[5:35, 30:55, :3] = SHADOW  # 直接接している
    view_preview.map_color_pixels(pixels)
    assert tuple(pixels[20, 10, :3]) != tuple(pixels[20, 50, :3])
    assert tuple(pixels[2, 2, :3]) == (255, 255, 255), "背景（白）はそのまま"


def test_map_colors_keep_the_line_between_regions_of_the_same_color():
    pixels = canvas_pixels()
    pixels[5:35, 5:55, :3] = NORMAL
    pixels[5:35, 29:31, :3] = 0  # 主線で2つに分かれた同じ色
    view_preview.map_color_pixels(pixels)
    assert tuple(pixels[20, 10, :3]) == tuple(pixels[20, 50, :3]),         "元の色が同じなら、分かれていても同じ仮の色"
    assert tuple(pixels[20, 30, :3]) == (0, 0, 0), "主線は元の色のまま"


def test_map_colors_reuse_one_color_for_every_area_of_the_same_color():
    pixels = canvas_pixels()
    pixels[5:35, 5:55, :3] = NORMAL
    pixels[10:15, 10:20, :3] = SHADOW  # 隢れた影の島を２つ
    pixels[25:30, 40:50, :3] = SHADOW
    view_preview.map_color_pixels(pixels)
    assert tuple(pixels[12, 15, :3]) == tuple(pixels[27, 45, :3])
    assert tuple(pixels[12, 15, :3]) != tuple(pixels[20, 30, :3])


def test_map_colors_make_a_speck_stand_out():
    pixels = canvas_pixels()
    pixels[5:35, 5:55, :3] = NORMAL
    pixels[20, 30, :3] = SHADOW  # 塗り漏れの1点
    view_preview.map_color_pixels(pixels)
    assert tuple(pixels[20, 30, :3]) != tuple(pixels[20, 31, :3])
    assert tuple(pixels[20, 30, :3]) in view_preview.MAP_PALETTE


def test_map_colors_keep_assignments_when_frame_areas_and_colors_change():
    mapping = {}
    first = canvas_pixels()
    first[5:35, 5:55, :3] = NORMAL
    first[10:15, 10:15, :3] = SHADOW
    view_preview.map_color_pixels(first, color_mapping=mapping)
    normal_color = first[20, 20, :3].copy()
    shadow_color = first[12, 12, :3].copy()

    second = canvas_pixels()
    second[5:35, 5:55, :3] = SHADOW
    second[10:15, 10:15, :3] = NORMAL
    second[25:30, 40:45, :3] = (100, 150, 200)
    view_preview.map_color_pixels(second, color_mapping=mapping)
    np.testing.assert_array_equal(second[12, 12, :3], normal_color)
    np.testing.assert_array_equal(second[20, 20, :3], shadow_color)
    assert not np.array_equal(second[27, 42, :3], shadow_color)

    # 色が一度画面から消えても、再登場時には割り当てを維持する。
    third = canvas_pixels()
    third[5:35, 5:55, :3] = NORMAL
    view_preview.map_color_pixels(third, color_mapping=mapping)
    np.testing.assert_array_equal(third[20, 20, :3], normal_color)


def test_label_regions_numpy_numbers_in_first_appearance_order():
    codes = np.array([[1, 1, 0, 2], [3, 1, 0, 2], [3, 3, 1, 1]], dtype=np.int32)
    valid = codes != 0
    labels, count = view_preview.label_regions_numpy(codes, valid)
    assert count == 4
    assert labels.tolist() == [[1, 1, 0, 2], [3, 1, 0, 2], [3, 3, 4, 4]]


# ----------------------------------------------------------------------
# キャンバスとバー
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from paintmaskanimator.main_window import MainWindow

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def painted_layer(window):
    layer = window.canvas.frames[0].layers[window.canvas.active_layer_index]
    layer.image.fill(QColor(*NORMAL))
    for y in range(10, 20):
        for x in range(10, 20):
            layer.image.setPixelColor(x, y, QColor(*SHADOW))
    layer.has_content = True
    return layer


def test_preview_changes_only_the_screen_not_exports(window):
    canvas = window.canvas
    layer = painted_layer(window)
    before = window.crop_image(0)

    window.exposure_spin.setValue(-2.0)
    window.channel_combo.setCurrentIndex(window.channel_combo.findData("red"))
    window.map_colors_button.click()

    shown = canvas.view_layer_image(layer, canvas.active_layer_index, layer.image)
    assert shown is not layer.image
    assert QColor(shown.pixel(15, 15)) != QColor(*SHADOW)
    assert layer.image.pixelColor(15, 15) == QColor(*SHADOW), "画素データは変えない"
    assert window.crop_image(0) == before, "書き出し・サムネイルには掛けない"


def test_map_colors_remain_stable_during_the_stroke(qapp, monkeypatch):
    from paintmaskanimator.canvas import PaintCanvas

    canvas = PaintCanvas()
    layer = canvas.frames[0].layers[canvas.active_layer_index]
    layer.image.fill(QColor(*NORMAL))
    layer.image.setPixelColor(15, 15, QColor(*SHADOW))
    canvas.set_view_preview(map_colors=True)
    before = canvas.view_layer_image(layer, canvas.active_layer_index, layer.image)
    normal_color = before.pixelColor(0, 0)
    shadow_color = before.pixelColor(15, 15)

    def unexpected_labels(*args, **kwargs):
        pytest.fail("Known colors must not require region analysis while drawing")

    monkeypatch.setattr(view_preview, "label_regions", unexpected_labels)
    canvas.drawing = True
    try:
        layer.image.setPixelColor(30, 30, QColor(*SHADOW))
        shown = canvas.view_layer_image(layer, canvas.active_layer_index, layer.image)
        assert shown.pixelColor(0, 0) == normal_color
        assert shown.pixelColor(30, 30) == shadow_color
        assert layer.image.pixelColor(30, 30) == QColor(*SHADOW)
    finally:
        canvas.drawing = False
    assert canvas.view_layer_image(layer, canvas.active_layer_index, layer.image) == shown


def test_canvas_map_colors_keep_the_same_rgb_when_switching_images(window):
    canvas = window.canvas
    layer = painted_layer(window)
    canvas.set_view_preview(map_colors=True)
    first_image = layer.image.copy()
    first = canvas.view_layer_image(layer, canvas.active_layer_index, first_image)
    second_image = first_image.copy()
    second_image.fill(QColor(*SHADOW))
    second_image.setPixelColor(15, 15, QColor(*NORMAL))
    second = canvas.view_layer_image(layer, canvas.active_layer_index, second_image)
    assert second.pixelColor(15, 15) == first.pixelColor(0, 0)
    assert second.pixelColor(0, 0) == first.pixelColor(15, 15)
    assert canvas.view_layer_image(layer, canvas.active_layer_index, first_image) == first


def test_exposure_is_a_link_until_clicked(window):
    control = window.exposure_control
    assert control.stack.currentWidget() is control.link
    control.begin_edit()
    assert control.stack.currentWidget() is window.exposure_spin
    assert window.exposure_spin.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
    window.exposure_spin.setValue(1.5)
    control.end_edit()
    assert control.stack.currentWidget() is control.link
    assert window.canvas.view_preview.exposure == 1.5
    assert "+1.5" in control.link.text()


def test_exposure_horizontal_drag_changes_value(window):
    window.exposure_control._drag(0.5)
    assert window.canvas.view_preview.exposure == 0.5


def test_channel_actions_toggle_back_to_rgb(window):
    window.a_channel_blue.trigger()
    assert window.canvas.view_preview.channel == "blue"
    assert window.channel_combo.currentData() == "blue"
    window.a_channel_blue.trigger()
    assert window.canvas.view_preview.channel == "rgb"


def test_black_check_button_follows_the_menu_and_shortcut(window):
    window.a_silhouette.trigger()
    assert window.canvas.silhouette_non_background
    assert window.black_check_button.isChecked()
    window.black_check_button.click()
    assert not window.canvas.silhouette_non_background
    assert not window.a_silhouette.isChecked()
    assert window.action_panel.button("silhouette") is None, "アクションパネルには出さない"


def test_map_palette_preference_falls_back_per_colour(qapp, tmp_path, monkeypatch):
    from paintmaskanimator import config, preferences

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert preferences.map_palette() == view_preview.MAP_PALETTE
    config.set_value(preferences.MAP_PALETTE_KEY, ["#010203", "壊れた値"])
    palette = preferences.map_palette()
    assert palette[0] == (1, 2, 3)
    assert palette[1:] == view_preview.MAP_PALETTE[1:], "読めない色・無い色は既定の色"
    preferences.set_map_palette(view_preview.MAP_PALETTE)
    assert config.get_value(preferences.MAP_PALETTE_KEY) is None


def test_map_colors_use_the_given_palette():
    pixels = canvas_pixels()
    pixels[5:35, 5:55, :3] = NORMAL
    view_preview.map_color_pixels(pixels, palette=((1, 2, 3), (4, 5, 6)))
    assert tuple(pixels[20, 20, :3]) == (1, 2, 3)


def test_preferences_page_changes_the_palette(window, monkeypatch):
    from paintmaskanimator import preferences
    from paintmaskanimator.preferences_dialog import ColorPickerDialog, PreferencesDialog

    monkeypatch.setattr(
        ColorPickerDialog, "get_color", staticmethod(lambda *args, **kwargs: QColor(1, 2, 3))
    )
    dialog = PreferencesDialog(window)
    try:
        dialog.map_palette_buttons[2].click()
        assert preferences.map_palette()[2] == (1, 2, 3)
        assert window.canvas.view_preview.palette[2] == (1, 2, 3)
        dialog._reset_map_palette()
        assert window.canvas.view_preview.palette == view_preview.MAP_PALETTE
    finally:
        dialog.deleteLater()
