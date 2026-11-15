"""取り込みの段階としてのゴミ取り（#10）。"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from paintmaskanimator import despeckle, imaging


def canvas():
    rgba = np.zeros((40, 60, 4), dtype=np.uint8)
    rgba[10, 5:50] = (0, 0, 0, 255)             # 線
    rgba[11, 50] = (0, 0, 0, 255)               # 線の先に斜めで付いた点
    rgba[30, 30] = (0, 0, 0, 255)               # 離れた1画素のゴミ
    rgba[25:27, 10:12] = (200, 30, 30, 255)     # 離れた2×2のゴミ（4画素）
    rgba[33:35, 50] = (0, 0, 0, 255)            # 離れた2画素のゴミ
    return rgba


def test_only_isolated_small_specks_are_removed():
    out, removed = despeckle.remove_isolated_specks(canvas(), 3)
    assert removed == 3
    assert out[30, 30, 3] == 0 and (out[33:35, 50, 3] == 0).all()
    assert (out[25:27, 10:12, 3] == 255).all(), "4画素は 3px より大きいので残す"
    assert (out[10, 5:50, 3] == 255).all(), "線は残す"
    assert out[11, 50, 3] == 255, "斜めでも線に触れていれば残す"


def test_a_larger_limit_removes_bigger_specks_but_never_the_line():
    out, _removed = despeckle.remove_isolated_specks(canvas(), 10)
    assert (out[25:27, 10:12, 3] == 0).all()
    assert (out[10, 5:50, 3] == 255).all()


def test_zero_disables_the_stage():
    rgba = canvas()
    out, removed = despeckle.remove_isolated_specks(rgba, 0)
    assert removed == 0 and np.array_equal(out, rgba)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_the_import_settings_run_the_stage(qapp, tmp_path, monkeypatch):
    from paintmaskanimator.main_window import MainWindow

    monkeypatch.setenv("APPDATA", str(tmp_path))
    window = MainWindow()
    try:
        image = imaging.rgba_array_to_qimage(canvas())
        settings = {"extraction_mode": "digital", "despeckle": 3}
        out = imaging.qimage_rgba_array(window.canvas.reduce_image_with_settings(image, settings))
        assert out[30, 30, 3] == 0 and out[10, 20, 3] == 255
    finally:
        window.deleteLater()


def test_the_dialog_offers_the_stage_for_scans_and_keeps_it_in_presets(qapp):
    from paintmaskanimator.color_reduction import ColorReductionDialog

    image = imaging.rgba_array_to_qimage(canvas())
    dialog = ColorReductionDialog(image, 3, 0, False, None, scan=True)
    try:
        assert dialog.despeckle_pixels() == despeckle.DEFAULT_MAX_PIXELS
        dialog.despeckle_size.setValue(6)
        assert dialog.current_settings()["despeckle"] == 6
        dialog.despeckle_check.setChecked(False)
        assert dialog.despeckle_pixels() == 0
        dialog.apply_settings({"despeckle": 4})
        assert dialog.despeckle_pixels() == 4
    finally:
        dialog.deleteLater()
