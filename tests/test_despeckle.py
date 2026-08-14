"""ゴミ取り／塗り抜けの純粋アルゴリズムのテスト。"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import despeckle, imaging  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _white_canvas(size=12):
    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[:, :, :3] = 255
    rgba[:, :, 3] = 255
    return rgba


def test_dust_removal_clears_isolated_speck():
    pixels = _white_canvas()
    pixels[5, 5, :3] = (0, 0, 0)
    # 大きな塊は残す。
    pixels[8:12, 8:12, :3] = (0, 0, 0)

    changed = despeckle.despeckle_pixels(pixels, max_area=4)

    assert changed == 1
    assert tuple(pixels[5, 5, :3]) == (255, 255, 255)
    assert tuple(pixels[9, 9, :3]) == (0, 0, 0)


def test_dust_removal_can_write_transparent_instead_of_white():
    pixels = _white_canvas()
    pixels[5, 5, :3] = (0, 0, 0)

    despeckle.despeckle_pixels(
        pixels,
        max_area=4,
        removal_rgba=(0, 0, 0, 0),
    )

    assert pixels[5, 5, 3] == 0


def test_dust_removal_with_selected_colors_only_touches_those_colors():
    pixels = _white_canvas()
    pixels[3, 3, :3] = (0, 0, 255)
    pixels[7, 7, :3] = (255, 0, 0)

    despeckle.despeckle_pixels(
        pixels,
        max_area=4,
        selected_colors=[(0, 0, 255)],
    )

    assert tuple(pixels[3, 3, :3]) == (255, 255, 255)
    assert tuple(pixels[7, 7, :3]) == (255, 0, 0)


def test_fill_mode_closes_small_enclosed_hole():
    pixels = _white_canvas()
    pixels[4:9, 4:9, :3] = (0, 128, 0)
    pixels[6, 6, :3] = (255, 255, 255)

    changed = despeckle.despeckle_pixels(
        pixels,
        mode=despeckle.FILL_MODE,
        max_area=4,
    )

    assert changed == 1
    assert tuple(pixels[6, 6, :3]) == (0, 128, 0)


def test_despeckle_image_returns_untouched_image_when_nothing_changes(qapp):
    image = imaging.rgba_array_to_qimage(_white_canvas())
    result, changed = despeckle.despeckle_image(image, max_area=4)
    assert changed == 0
    assert result is image


def test_despeckle_keeps_result_within_palette(qapp):
    """ゴミ取り後も、確定パレット外の色が生まれない。"""
    pixels = _white_canvas()
    pixels[:, :, :3] = 255
    pixels[2:6, 2:6, :3] = (12, 34, 56)
    pixels[9, 9, :3] = (12, 34, 56)
    palette = {(255, 255, 255), (12, 34, 56)}

    despeckle.despeckle_pixels(pixels, max_area=4)

    present = {tuple(pixel) for pixel in pixels[:, :, :3].reshape(-1, 3)}
    assert present <= palette
