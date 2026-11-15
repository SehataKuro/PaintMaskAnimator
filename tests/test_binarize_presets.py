"""2値化の設定のプリセット（#10）と、確定パレットの外の色が残らないこと。"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from paintmaskanimator import preferences
from paintmaskanimator.color_reduction import ColorReductionDialog

from test_binarize_palette import antialiased, colours


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def dialog(**kwargs):
    return ColorReductionDialog(antialiased(), 5, 0, False, None, **kwargs)


def test_a_preset_saves_and_restores_every_setting(qapp):
    first = dialog(scan=True)
    try:
        first.extraction_mode.setCurrentIndex(first.extraction_mode.findData("surface"))
        first.color_count.setValue(5)
        first.tone_curve.setPoints([(0.0, 0.0), (0.4, 0.2), (1.0, 1.0)])
        first.flatten_paper.setChecked(False)
        first.set_custom_palette([(255, 0, 0), (0, 0, 255)])
        assert first.save_preset("赤青TP")
    finally:
        first.deleteLater()

    assert "赤青TP" in preferences.binarize_presets()

    second = dialog(scan=True)
    try:
        assert second.preset_choice.findData("赤青TP") >= 0, "保存したプリセットが一覧に出る"
        assert second.load_preset("赤青TP")
        assert second.selected_extraction_mode() == "surface"
        assert second.color_count.value() == 5
        assert [tuple(round(v, 3) for v in p) for p in second.tone_curve_points()] == [
            (0.0, 0.0), (0.4, 0.2), (1.0, 1.0)
        ]
        assert second.flatten_paper_enabled() is False
        assert second._custom_palette_key() == ((255, 0, 0), (0, 0, 255))
        assert second._preview_dirty, "呼び出したらプレビューを作り直す"
    finally:
        second.deleteLater()


def test_deleting_a_preset(qapp):
    preferences.save_binarize_preset("消す", {"color_count": 4})
    box = dialog()
    try:
        box.refresh_presets(selected="消す")
        box._delete_preset_clicked()
        assert "消す" not in preferences.binarize_presets()
        assert box.preset_choice.findData("消す") < 0
    finally:
        box.deleteLater()


def test_broken_preset_values_are_ignored(qapp):
    preferences.save_binarize_preset("壊れた", {
        "extraction_mode": "nonsense", "color_count": "many", "tone_curve_points": "x",
        "palette": [[999, 0, 0]],
    })
    box = dialog()
    try:
        before = box.color_count.value()
        assert box.load_preset("壊れた")
        assert box.color_count.value() == before
        assert box._custom_palette is None
    finally:
        box.deleteLater()


def test_no_colour_outside_the_confirmed_palette_remains(qapp, tmp_path, monkeypatch):
    from paintmaskanimator.main_window import MainWindow

    monkeypatch.setenv("APPDATA", str(tmp_path))
    window = MainWindow()
    try:
        palette = [(255, 0, 0), (0, 0, 255)]
        settings = {
            "palette": np.asarray(palette, dtype=np.uint8), "extraction_mode": "surface",
            "alpha_threshold": 128, "opaque_background": False, "background_rgb": None,
            "tone_curve_points": [(0.0, 0.0), (1.0, 1.0)],
        }
        out = window.canvas.reduce_image_with_settings(antialiased(), settings)
        assert colours(out) <= set(palette)
    finally:
        window.deleteLater()
