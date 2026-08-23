"""Unit tests for the pure colour-analysis helpers in :mod:`color_ops`.

These functions operate on ``QImage`` inputs built from numpy RGBA arrays, so
they are deterministic and cheap to exercise without any GUI. They were largely
uncovered; these tests pin their observable behaviour.
"""
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


def test_image_alpha_statistics_counts_each_band(qapp):
    rgba = np.zeros((2, 2, 4), dtype=np.uint8)
    rgba[0, 0] = (10, 20, 30, 0)      # transparent
    rgba[0, 1] = (10, 20, 30, 128)    # semi-transparent
    rgba[1, 0] = (10, 20, 30, 255)    # opaque
    rgba[1, 1] = (40, 50, 60, 255)    # opaque
    stats = color_ops.image_alpha_statistics(_image(rgba))
    assert stats == {"transparent": 1, "semi_transparent": 1, "opaque": 2}


def test_image_alpha_statistics_null_image(qapp):
    assert color_ops.image_alpha_statistics(QImage()) == {
        "transparent": 0,
        "semi_transparent": 0,
        "opaque": 0,
    }
    assert color_ops.image_alpha_statistics(None) == {
        "transparent": 0,
        "semi_transparent": 0,
        "opaque": 0,
    }


def test_binarize_alpha_for_pixel_art_snaps_alpha(qapp):
    rgba = np.zeros((1, 3, 4), dtype=np.uint8)
    rgba[0, 0] = (200, 100, 50, 40)    # below threshold -> cleared
    rgba[0, 1] = (200, 100, 50, 200)   # above threshold -> opaque, rgb kept
    rgba[0, 2] = (10, 20, 30, 128)     # exactly at default threshold -> opaque
    out = color_ops.binarize_alpha_for_pixel_art(_image(rgba), alpha_threshold=128)
    back = imaging.qimage_rgba_array(out)
    # Below-threshold pixel is fully cleared.
    assert tuple(back[0, 0]) == (0, 0, 0, 0)
    # Kept pixels become fully opaque; their RGB survives (±1 from the
    # premultiplied-ARGB round trip that building the source image incurs).
    assert back[0, 1, 3] == 255
    assert np.allclose(back[0, 1, :3], (200, 100, 50), atol=1)
    assert back[0, 2, 3] == 255
    assert np.allclose(back[0, 2, :3], (10, 20, 30), atol=1)


def test_binarize_alpha_null_image_returns_null(qapp):
    assert color_ops.binarize_alpha_for_pixel_art(QImage()).isNull()


def test_opaque_rgb_color_count_ignores_transparent(qapp):
    rgba = np.zeros((2, 2, 4), dtype=np.uint8)
    rgba[0, 0] = (255, 0, 0, 255)
    rgba[0, 1] = (255, 0, 0, 255)   # duplicate colour
    rgba[1, 0] = (0, 255, 0, 255)
    rgba[1, 1] = (9, 9, 9, 0)       # transparent -> excluded
    assert color_ops.opaque_rgb_color_count(_image(rgba)) == 2


def test_opaque_rgb_color_count_all_transparent(qapp):
    rgba = np.zeros((2, 2, 4), dtype=np.uint8)
    assert color_ops.opaque_rgb_color_count(_image(rgba)) == 0


def test_detect_opaque_border_background_white(qapp):
    rgba = np.full((5, 5, 4), 255, dtype=np.uint8)      # all white, opaque
    rgba[2, 2] = (10, 20, 30, 255)                       # small foreground blob
    assert color_ops.detect_opaque_border_background(_image(rgba)) == (255, 255, 255)


def test_detect_opaque_border_background_rejects_dark_border(qapp):
    rgba = np.zeros((5, 5, 4), dtype=np.uint8)
    rgba[:, :, 3] = 255                                   # opaque but dark border
    assert color_ops.detect_opaque_border_background(_image(rgba)) is None


def test_detect_opaque_border_background_rejects_semitransparent(qapp):
    rgba = np.full((4, 4, 4), 255, dtype=np.uint8)
    rgba[0, 0, 3] = 100                                   # any translucency -> None
    assert color_ops.detect_opaque_border_background(_image(rgba)) is None


def test_tone_curve_lut_identity_by_default(qapp):
    lut = color_ops.tone_curve_lut()
    assert lut.dtype == np.uint8
    assert lut.shape == (256,)
    np.testing.assert_array_equal(lut, np.arange(256, dtype=np.uint8))


def test_tone_curve_lut_is_monotonic_for_linear_points(qapp):
    lut = color_ops.tone_curve_lut([(0.0, 0.0), (1.0, 1.0)])
    assert np.all(np.diff(lut.astype(np.int16)) >= 0)


def test_apply_tone_curve_identity_returns_copy(qapp):
    rgba = np.zeros((1, 2, 4), dtype=np.uint8)
    rgba[0, 0] = (100, 150, 200, 255)
    out = color_ops.apply_tone_curve(_image(rgba), [(0.0, 0.0), (1.0, 1.0)])
    back = imaging.qimage_rgba_array(out)
    assert tuple(back[0, 0]) == (100, 150, 200, 255)


def test_build_color_reduction_palette_shape(qapp):
    rgba = np.zeros((8, 8, 4), dtype=np.uint8)
    rgba[:, :4] = (220, 30, 30, 255)     # red half
    rgba[:, 4:] = (30, 30, 220, 255)     # blue half
    palette = color_ops.build_color_reduction_palette(_image(rgba), color_count=4)
    assert palette.dtype == np.uint8
    assert palette.ndim == 2 and palette.reshape(-1, 3).shape == palette.shape
    assert 1 <= palette.shape[0] <= 4


def test_build_color_reduction_palette_empty_image(qapp):
    palette = color_ops.build_color_reduction_palette(QImage(), color_count=4)
    np.testing.assert_array_equal(palette, np.asarray([[0, 0, 0]], dtype=np.uint8))


def test_apply_color_reduction_palette_maps_to_palette_colors(qapp):
    rgba = np.zeros((8, 8, 4), dtype=np.uint8)
    rgba[:, :4] = (220, 30, 30, 255)
    rgba[:, 4:] = (30, 30, 220, 255)
    palette = np.asarray([(220, 30, 30), (30, 30, 220)], dtype=np.uint8)
    out = color_ops.apply_color_reduction_palette(_image(rgba), palette)
    back = imaging.qimage_rgba_array(out)
    opaque = back[back[:, :, 3] == 255]
    assert opaque.shape[0] > 0
    palette_set = {tuple(int(c) for c in row) for row in palette}
    for pixel in opaque:
        assert tuple(int(c) for c in pixel[:3]) in palette_set


def test_apply_color_reduction_palette_null_image(qapp):
    out = color_ops.apply_color_reduction_palette(QImage(), np.zeros((1, 3)))
    assert out is not None and out.isNull()


def test_apply_tone_curve_darkening_lowers_values(qapp):
    rgba = np.zeros((1, 1, 4), dtype=np.uint8)
    rgba[0, 0] = (128, 128, 128, 255)
    # Pull the midpoint down: output at 0.5 input becomes ~0.25.
    out = color_ops.apply_tone_curve(
        _image(rgba), [(0.0, 0.0), (0.5, 0.25), (1.0, 1.0)]
    )
    back = imaging.qimage_rgba_array(out)
    assert back[0, 0, 0] < 128
    assert back[0, 0, 3] == 255
