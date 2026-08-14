"""取り込み設定（プリセット保存／復元）のテスト。"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import config, despeckle, imaging  # noqa: E402
from paintmaskanimator.color_reduction import (  # noqa: E402
    IMPORT_PRESETS_KEY,
    LAST_IMPORT_SETTINGS_KEY,
    ColorReductionDialog,
)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    return tmp_path


def _dialog(qapp):
    rgba = np.zeros((8, 8, 4), dtype=np.uint8)
    rgba[:, :, :3] = 255
    rgba[:, :, 3] = 255
    rgba[3, 3, :3] = 0
    return ColorReductionDialog(
        imaging.rgba_array_to_qimage(rgba),
        2,
        sample_frame_count=3,
    )


def test_settings_round_trip(qapp, isolated_config):
    dialog = _dialog(qapp)
    dialog.color_count.setValue(12)
    dialog.despeckle_enabled.setChecked(True)
    dialog.despeckle_size.setValue(9)
    dialog.tone_curve.setPoints([(0.0, 0.0), (0.4, 0.6), (1.0, 1.0)])
    settings = dialog.current_settings()

    restored = _dialog(qapp)
    assert restored.apply_settings(settings)
    assert restored.current_settings() == settings
    assert restored.despeckle_options()["max_area"] == 9
    dialog.deleteLater()
    restored.deleteLater()


def test_apply_settings_ignores_broken_payloads(qapp, isolated_config):
    dialog = _dialog(qapp)
    assert not dialog.apply_settings(None)
    assert not dialog.apply_settings({"target_colors": "たくさん"})
    dialog.deleteLater()


def test_stored_preset_is_listed_and_loadable(qapp, isolated_config):
    dialog = _dialog(qapp)
    dialog.despeckle_enabled.setChecked(True)
    config.set_value(IMPORT_PRESETS_KEY, {"線画": dialog.current_settings()})
    dialog.deleteLater()

    reopened = _dialog(qapp)
    reopened._refresh_preset_selector("線画")
    assert reopened.preset_selector.currentText() == "線画"
    reopened._load_selected_preset()
    assert reopened.despeckle_options()["enabled"]
    reopened.deleteLater()


def test_last_settings_are_restored_on_open(qapp, isolated_config):
    config.set_value(
        LAST_IMPORT_SETTINGS_KEY,
        {
            "extraction_mode": "surface",
            "target_colors": 5,
            "tone_curve_points": [[0.0, 0.0], [1.0, 1.0]],
            "despeckle": {
                "enabled": True,
                "mode": despeckle.FILL_MODE,
                "max_area": 7,
            },
        },
    )
    dialog = _dialog(qapp)
    assert dialog.selected_extraction_mode() == "surface"
    assert dialog.color_count.value() == 5
    assert dialog.despeckle_options() == {
        "enabled": True,
        "mode": despeckle.FILL_MODE,
        "max_area": 7,
    }
    dialog.deleteLater()


def test_removal_color_matches_background_mode(qapp, isolated_config):
    dialog = _dialog(qapp)
    dialog.extraction_mode.setCurrentIndex(
        dialog.extraction_mode.findData("line")
    )
    assert dialog.despeckle_removal_rgba() == (255, 255, 255, 255)
    dialog.extraction_mode.setCurrentIndex(
        dialog.extraction_mode.findData("surface")
    )
    # 透明背景の素材では、パレット外の白を作らず透明へ戻す。
    assert dialog.despeckle_removal_rgba() == (0, 0, 0, 0)
    dialog.deleteLater()
