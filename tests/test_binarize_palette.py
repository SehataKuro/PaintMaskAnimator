"""#62：2値化で出力する色を指定する・読み込んだあとのレイヤーにかける。"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator import imaging
from paintmaskanimator.color_reduction import ColorReductionDialog
from paintmaskanimator.document import create_drawing
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.undo_entries import ScopedBatchUndo

RED = (230, 30, 40)
BLUE = (30, 40, 220)


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


def antialiased(width=48, height=32):
    """左が赤・右が青の色面で、境目に混ざった画素（アンチエイリアス）がある絵。"""
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[:, : width // 2] = (*RED, 255)
    pixels[:, width // 2:] = (*BLUE, 255)
    middle = width // 2
    for offset, share in ((-1, 0.7), (0, 0.5), (1, 0.3)):
        mixed = np.array(RED) * share + np.array(BLUE) * (1 - share)
        pixels[:, middle + offset, :3] = mixed.astype(np.uint8)
    return imaging.rgba_array_to_qimage(pixels).convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied
    )


def colours(image):
    rgba = imaging.qimage_rgba_array(image)
    visible = rgba[:, :, 3] > 0
    return {tuple(int(v) for v in rgb) for rgb in rgba[visible][:, :3]}


def dialog_for(image, **kwargs):
    dialog = ColorReductionDialog(image, 5, 0, False, None, **kwargs)
    dialog.extraction_mode.setCurrentIndex(0)  # 色面抽出
    dialog.color_count.setValue(2)
    assert dialog._update_preview()
    return dialog


def test_the_automatic_palette_is_shown_as_swatches(qapp):
    dialog = dialog_for(antialiased())
    try:
        assert len(dialog.palette_swatches) == len(dialog.selected_palette())
        assert not dialog.auto_palette_button.isEnabled()
    finally:
        dialog.deleteLater()


def test_a_specified_palette_is_used_exactly(qapp):
    dialog = dialog_for(antialiased())
    try:
        wanted = [(255, 0, 0), (0, 0, 255)]
        dialog.set_custom_palette(wanted)
        assert dialog._preview_dirty, "指定したらプレビューを作り直す"
        assert not dialog.color_count.isEnabled()

        assert dialog._update_preview()
        assert [tuple(c) for c in dialog.selected_palette()] == wanted
        assert colours(dialog._reduced_full_image) == set(wanted), "出力は指定した色だけ"

        dialog.color_count.setValue(6)
        assert dialog._update_preview()
        assert [tuple(c) for c in dialog.selected_palette()] == wanted, "色数で上書きしない"
    finally:
        dialog.deleteLater()


def test_removing_and_resetting_the_palette(qapp):
    dialog = dialog_for(antialiased())
    try:
        dialog.set_custom_palette([(255, 0, 0), (0, 0, 255), (0, 255, 0)])
        dialog._remove_palette_color(2)
        assert dialog._custom_palette_key() == ((255, 0, 0), (0, 0, 255))
        dialog._reset_custom_palette()
        assert dialog._custom_palette is None
        assert dialog.color_count.isEnabled()
    finally:
        dialog.deleteLater()


def test_layer_dialog_hides_import_only_button(qapp):
    dialog = dialog_for(antialiased(), allow_without_reduction=False, scope_choice=True)
    try:
        assert dialog.no_reduction_button.isHidden()
        assert dialog.selected_all_frames() is True
        dialog.all_frames_choice.setCurrentIndex(0)
        assert dialog.selected_all_frames() is False
    finally:
        dialog.deleteLater()


def prepare(window, frame_count=2):
    canvas = window.canvas
    canvas._ensure_frame_count(frame_count)
    for index in range(frame_count):
        layer = canvas.frames[index].layers[0]
        layer.image = QImage(layer.image.size(), QImage.Format.Format_ARGB32_Premultiplied)
        layer.image.fill(QColor(0, 0, 0, 0))
        from PySide6.QtGui import QPainter

        painter = QPainter(layer.image)
        painter.drawImage(0, 0, antialiased())
        painter.end()
        create_drawing(layer, layer.image.copy(), number=index + 1)
    canvas.current_frame = 0
    canvas.active_layer_index = 0


def settings(palette):
    return {
        "palette": np.asarray(palette, dtype=np.uint8),
        "extraction_mode": "surface",
        "alpha_threshold": 128,
        "opaque_background": False,
        "background_rgb": None,
        "tone_curve_points": [(0.0, 0.0), (1.0, 1.0)],
    }


def test_binarizing_a_layer_after_import_covers_every_frame_and_undoes_once(window):
    prepare(window)
    before = [frame.layers[0].image.copy() for frame in window.canvas.frames]
    wanted = [(255, 0, 0), (0, 0, 255)]

    assert window.binarize_layer(settings(wanted), all_frames=True)

    for frame in window.canvas.frames:
        found = colours(frame.layers[0].image.copy(0, 0, 48, 32))
        assert found == set(wanted)
    assert isinstance(window.canvas.undo_stack[-1], ScopedBatchUndo)
    window.canvas.undo()
    for frame, original in zip(window.canvas.frames, before):
        assert frame.layers[0].image == original


def test_binarizing_only_the_current_frame(window):
    prepare(window)
    second = window.canvas.frames[1].layers[0].image.copy()

    assert window.binarize_layer(settings([(255, 0, 0), (0, 0, 255)]), all_frames=False)

    assert window.canvas.frames[1].layers[0].image == second
