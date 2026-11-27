"""スキャンした紙の地色とムラを白へそろえる（#51）。"""
from __future__ import annotations

import numpy as np

from paintmaskanimator import scan_paper

CREAM = np.array([238, 228, 200], dtype=np.float64)


def scan(height=240, width=320, shade=0.25):
    """クリーム色の紙に、左から右へ暗くなるムラと、黒と灰色（鉛筆）の線。"""
    gradient = 1.0 - shade * np.linspace(0, 1, width)[None, :, None]
    paper = CREAM[None, None, :] * gradient
    rgb = np.repeat(paper, height, axis=0)
    rgb[60:62, 20:300] *= 0.08   # 黒い線
    rgb[150, 20:300] *= 0.55     # 薄い鉛筆の線
    rgb[20:220, 160] *= 0.08     # 縦の線
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    rgba[:, :, 3] = 255
    return rgba


def test_cream_paper_with_uneven_light_becomes_white():
    out = scan_paper.flatten_paper(scan())
    paper = np.delete(out[100:140, 10:310, :3], 150, axis=1)  # 縦の線（x=160）を除く
    assert paper.min() >= 245, "クリーム色も暗い側も白にそろう"


def test_lines_stay_dark_and_pencil_keeps_its_tone():
    out = scan_paper.flatten_paper(scan())
    assert out[60, 40:290, :3].max() <= 40, "黒い線は暗いまま（明るい側でも暗い側でも）"
    pencil = np.delete(out[150, 40:290, :3].astype(int), 120, axis=0)  # 縦の線との交点を除く
    assert 110 <= pencil.min() and pencil.max() <= 160, "鉛筆の中間調は比のまま残る"
    assert pencil[:, 0].max() - pencil[:, 0].min() <= 12, "ムラが抜けて、線の濃さが端から端までそろう"


def test_alpha_is_kept_and_the_input_is_not_changed():
    rgba = scan()
    rgba[0:4, 0:4, 3] = 0
    before = rgba.copy()
    out = scan_paper.flatten_paper(rgba)
    assert (out[:, :, 3] == rgba[:, :, 3]).all()
    assert (rgba == before).all()


def test_a_dark_hole_does_not_darken_the_paper_around_it():
    rgba = scan(shade=0.0)
    rgba[0:40, 0:40, :3] = 10  # タップ穴（暗い大きな塊）
    out = scan_paper.flatten_paper(rgba)
    assert out[100:140, 60:100, :3].min() >= 245


def test_the_analog_dialog_flattens_the_preview_and_can_turn_it_off():
    from PySide6.QtWidgets import QApplication

    from paintmaskanimator import imaging
    from paintmaskanimator.color_reduction import ColorReductionDialog

    QApplication.instance() or QApplication([])
    image = imaging.rgba_array_to_qimage(scan())
    dialog = ColorReductionDialog(image, 500, 0, False, None, scan=True)
    try:
        assert dialog.flatten_paper_enabled()
        flattened = imaging.qimage_rgba_array(dialog.source_image)
        assert flattened[120, 300, :3].min() >= 245
        assert dialog.background_rgb == (255, 255, 255)

        dialog.flatten_paper.setChecked(False)
        raw = imaging.qimage_rgba_array(dialog.source_image)
        assert raw[120, 300, 0] < 200, "切ると元のスキャンに戻る"
        assert dialog.background_rgb is None
        assert dialog._preview_dirty
    finally:
        dialog.deleteLater()


def test_the_import_settings_flatten_before_binarizing(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from paintmaskanimator import imaging
    from paintmaskanimator.main_window import MainWindow

    QApplication.instance() or QApplication([])
    monkeypatch.setenv("APPDATA", str(tmp_path))
    window = MainWindow()
    try:
        image = imaging.rgba_array_to_qimage(scan())
        settings = {
            "extraction_mode": "line", "palette": None, "flatten_paper": True,
            "tone_curve_points": [(0.0, 0.0), (1.0, 1.0)],
        }
        out = imaging.qimage_rgba_array(window.canvas.reduce_image_with_settings(image, settings))
        assert out[120, 300, :3].min() >= 245
    finally:
        window.deleteLater()
