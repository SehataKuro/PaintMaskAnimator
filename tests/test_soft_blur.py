"""取り込みの前処理としての弱いぼかし（#10）。"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from paintmaskanimator import imaging, soft_blur


def grainy(seed=0):
    """白地に黒い太線。画素ごとに ±20 のざらつきがある。"""
    rng = np.random.default_rng(seed)
    rgba = np.full((60, 80, 4), 255, dtype=np.uint8)
    rgba[25:35, 10:70, :3] = 0
    noise = rng.integers(-20, 21, size=(60, 80, 1))
    rgba[:, :, :3] = np.clip(rgba[:, :, :3].astype(int) + noise, 0, 255).astype(np.uint8)
    return rgba


def test_blur_evens_out_grain_and_keeps_the_line():
    rgba = grainy()
    out = soft_blur.soft_blur(rgba, 1.0)
    before = rgba[5:20, 10:70, 0].astype(int).std()
    after = out[5:20, 10:70, 0].astype(int).std()
    assert after < before / 2, "ざらつきが半分以下になる"
    assert out[28:32, 20:60, :3].max() <= 60, "線は暗いまま"
    assert out[5:20, 10:70, :3].min() >= 200, "紙は明るいまま"


def test_alpha_is_untouched_and_zero_is_a_copy():
    rgba = grainy()
    rgba[0:10, 0:10, 3] = 0
    out = soft_blur.soft_blur(rgba, 2.0)
    assert (out[:, :, 3] == rgba[:, :, 3]).all()
    same = soft_blur.soft_blur(rgba, 0)
    assert np.array_equal(same, rgba) and same is not rgba


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_the_dialog_blurs_the_preview_and_saves_the_amount(qapp):
    from paintmaskanimator.color_reduction import ColorReductionDialog

    raw = grainy()
    dialog = ColorReductionDialog(imaging.rgba_array_to_qimage(raw), 300, 0, False, None)
    try:
        assert dialog.blur_amount() == 0
        dialog.blur_sigma.setValue(1.5)
        assert dialog._preview_dirty
        source = imaging.qimage_rgba_array(dialog.source_image)
        assert source[5:20, 10:70, 0].astype(int).std() < raw[5:20, 10:70, 0].astype(int).std() / 2
        assert dialog.current_settings()["blur"] == 1.5
        dialog.apply_settings({"blur": 0.5})
        assert dialog.blur_amount() == 0.5
        dialog.blur_sigma.setValue(0)
        assert np.array_equal(imaging.qimage_rgba_array(dialog.source_image), raw)
    finally:
        dialog.deleteLater()


def test_the_import_settings_blur_before_the_tone_curve(qapp, tmp_path, monkeypatch):
    from paintmaskanimator.main_window import MainWindow

    monkeypatch.setenv("APPDATA", str(tmp_path))
    window = MainWindow()
    try:
        image = imaging.rgba_array_to_qimage(grainy())
        settings = {"extraction_mode": "line", "palette": None, "blur": 1.0,
                    "tone_curve_points": [(0.0, 0.0), (1.0, 1.0)]}
        out = imaging.qimage_rgba_array(window.canvas.reduce_image_with_settings(image, settings))
        assert out[5:20, 10:70, 0].astype(int).std() < grainy()[5:20, 10:70, 0].astype(int).std() / 2
    finally:
        window.deleteLater()
