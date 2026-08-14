"""取り込みパイプライン（代表フレーム／外周連結の背景除去）のテスト。"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import color_ops, imaging  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _image(rgba):
    return imaging.rgba_array_to_qimage(np.asarray(rgba, dtype=np.uint8))


def _jpeg_like_scene():
    """白背景＋内側の白い塗り面を持つ、圧縮ノイズ入りの画像。"""
    rgba = np.zeros((16, 16, 4), dtype=np.uint8)
    rgba[:, :, 3] = 255
    rng = np.random.default_rng(0)
    noise = rng.integers(250, 256, size=(16, 16, 3), dtype=np.uint8)
    rgba[:, :, :3] = noise
    # 閉じた黒い輪郭と、その内側の純白の塗り面。
    rgba[4:12, 4:12, :3] = 0
    rgba[6:10, 6:10, :3] = 255
    return rgba


def test_border_connected_background_keeps_enclosed_white(qapp):
    rgba = _jpeg_like_scene()
    mask = color_ops.border_connected_background_mask(rgba, (255, 255, 255))
    # 外周の背景は全て背景と判定される。
    assert mask[0, 0] and mask[0, 15] and mask[15, 0] and mask[15, 15]
    # 輪郭の内側の白い塗り面は残る。
    assert not mask[7, 7]
    # 黒い輪郭も残る。
    assert not mask[5, 5]


def test_remove_border_connected_background_clears_alpha(qapp):
    image = _image(_jpeg_like_scene())
    cleaned = color_ops.remove_border_connected_background(image)
    out = imaging.qimage_rgba_array(cleaned)
    assert out[0, 0, 3] == 0
    assert out[7, 7, 3] == 255
    assert tuple(out[7, 7, :3]) == (255, 255, 255)


def test_remove_border_connected_background_without_background_is_noop(qapp):
    rgba = np.zeros((8, 8, 4), dtype=np.uint8)
    rgba[:, :, :3] = 20
    rgba[:, :, 3] = 255
    image = _image(rgba)
    cleaned = color_ops.remove_border_connected_background(image)
    out = imaging.qimage_rgba_array(cleaned)
    assert np.all(out[:, :, 3] == 255)


def test_select_representative_indexes_covers_first_middle_last():
    assert imaging.select_representative_indexes(0) == []
    assert imaging.select_representative_indexes(2) == [0, 1]
    assert imaging.select_representative_indexes(9) == [0, 4, 8]
    indexes = imaging.select_representative_indexes(10, 5)
    assert len(indexes) == 5
    assert indexes[0] == 0 and indexes[-1] == 9
    assert indexes == sorted(set(indexes))


def test_representative_sheet_contains_every_frame_color(qapp):
    frames = []
    for value in (10, 120, 240):
        rgba = np.zeros((4, 4, 4), dtype=np.uint8)
        rgba[:, :, :3] = value
        rgba[:, :, 3] = 255
        frames.append(_image(rgba))
    sheet = imaging.build_representative_sheet(frames, (255, 255, 255))
    assert (sheet.width(), sheet.height()) == (12, 4)
    out = imaging.qimage_rgba_array(sheet)
    present = {tuple(pixel) for pixel in out[:, :, :3].reshape(-1, 3)}
    # 中間フレームにしか無い色もパレット推定の入力に含まれる。
    assert {(10, 10, 10), (120, 120, 120), (240, 240, 240)} <= present


def test_representative_sheet_single_frame_is_a_copy(qapp):
    rgba = np.zeros((3, 3, 4), dtype=np.uint8)
    rgba[:, :, 3] = 255
    sheet = imaging.build_representative_sheet([_image(rgba)])
    assert (sheet.width(), sheet.height()) == (3, 3)
