"""取り込みの回帰テスト（#10）：JPEG・PNG 連番・スキャン線画の代表サンプル。

どの経路でも、取り込んだ画像に確定パレットの外の色（と半透明）が残らないこと、
ほかの代表フレームも同じ設定で並べて見られることを確かめる。
"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator import imaging
from paintmaskanimator.color_reduction import ColorReductionDialog

import test_digital_antialias as tp_samples
from test_scan_paper import scan


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from paintmaskanimator.main_window import MainWindow

    monkeypatch.setenv("APPDATA", str(tmp_path))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def colours(image):
    rgba = imaging.qimage_rgba_array(image)
    visible = rgba[:, :, 3] > 0
    return {tuple(int(v) for v in rgb) for rgb in rgba[visible][:, :3]}, set(
        np.unique(rgba[:, :, 3]).tolist()
    )


def jpeg_line_art(tmp_path, shift=0):
    """白地に黒い線と赤い線。JPEG で保存して読み直し、圧縮のゆれを入れる。"""
    rgba = np.full((80, 120, 4), 255, dtype=np.uint8)
    rgba[20:23, 10 + shift:110, :3] = 0
    rgba[50:52, 10:110 - shift, :3] = (230, 20, 20)
    path = tmp_path / f"jpeg_{shift}.jpg"
    assert imaging.rgba_array_to_qimage(rgba).convertToFormat(QImage.Format.Format_RGB32).save(
        str(path), "JPG", 70
    )
    return QImage(str(path))


def analog_settings(window, first, references, *, scan_material, mode, count):
    dialog = ColorReductionDialog(
        first, 500, 0, True, (255, 255, 255), reference_images=references, scan=scan_material,
    )
    try:
        dialog.extraction_mode.setCurrentIndex(dialog.extraction_mode.findData(mode))
        dialog.color_count.setValue(count)
        assert dialog._update_preview()
        palette = dialog.selected_palette()
        settings = window._color_reduction_settings(dialog, palette, 500, 0)
        previews = list(getattr(dialog, "_reference_reduced_images", []))
        shown = len(dialog.reference_previews)
    finally:
        dialog.deleteLater()
    return settings, {tuple(int(v) for v in c) for c in palette}, previews, shown


def assert_only(image, palette):
    found, alphas = colours(image)
    assert found <= palette, f"パレットの外の色 {sorted(found - palette)[:5]}"
    assert alphas <= {0, 255}, "半透明が残らない"


def test_jpeg_line_art(window, tmp_path):
    frames = [jpeg_line_art(tmp_path, shift) for shift in (0, 4, 8)]
    settings, palette, previews, shown = analog_settings(
        window, frames[0], frames[1:], scan_material=False, mode="line", count=3,
    )
    assert shown == 2 and len(previews) == 2, "ほかの代表フレームも並べる"
    for image in previews:
        assert_only(image, palette)
    for image in frames:
        assert_only(window.canvas.reduce_image_with_settings(image, settings), palette)


def test_scanned_line_art(window):
    frames = [imaging.rgba_array_to_qimage(scan(shade=shade)) for shade in (0.1, 0.25, 0.4)]
    settings, palette, previews, _shown = analog_settings(
        window, frames[0], frames[1:], scan_material=True, mode="line", count=3,
    )
    assert settings["flatten_paper"] and settings["despeckle"] > 0
    for image in previews + frames:
        out = window.canvas.reduce_image_with_settings(image, settings) if image in frames else image
        assert_only(out, palette)


def test_png_sequence_with_antialias(window):
    frames = [imaging.rgba_array_to_qimage(tp_samples.antialiased()) for _ in range(3)]
    expected = {tp_samples.SKIN, tp_samples.HAIR, tp_samples.RED, tp_samples.BLUE,
                tp_samples.GREEN, tp_samples.BLACK}
    settings = {"extraction_mode": "digital", "palette": None}
    for image in frames:
        found, alphas = colours(window.canvas.reduce_image_with_settings(image, settings))
        assert found == expected and alphas <= {0, 255}
