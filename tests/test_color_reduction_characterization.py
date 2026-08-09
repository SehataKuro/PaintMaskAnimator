"""Golden characterization of the color-reduction / tone-curve / alpha cluster.

These functions were untested; this locks their current output before they are
moved out of PaintCanvas into a domain module, so the extraction can't change
behavior. Golden hashes captured from the pre-extraction implementation.
"""
import hashlib
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from paintmaskanimator import imaging  # noqa: E402
from paintmaskanimator.canvas import PaintCanvas as P  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sample(qapp):
    h, w = 32, 48
    yy, xx = np.mgrid[0:h, 0:w]
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., 0] = (xx * 5) % 256
    rgba[..., 1] = (yy * 8) % 256
    rgba[..., 2] = ((xx + yy) * 3) % 256
    rgba[..., 3] = np.where((xx + yy) % 7 == 0, 120, 255)
    return imaging.rgba_array_to_qimage(rgba)


TONE_POINTS = [(0.0, 0.0), (0.3, 0.5), (1.0, 1.0)]


def _qh(q):
    return hashlib.sha256(q.constBits().tobytes()).hexdigest()[:16]


def _ah(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()[:16]


def test_tone_curve_lut_golden(sample):
    assert _ah(P.tone_curve_lut(TONE_POINTS)) == "648cd2d94078d26f"


def test_apply_tone_curve_golden(sample):
    assert _qh(P.apply_tone_curve(sample, TONE_POINTS)) == "bff004b76cb56aa6"


def test_binarize_alpha_golden(sample):
    assert _qh(P.binarize_alpha_for_pixel_art(sample)) == "b37343b54a1d51e2"


def test_alpha_statistics_golden(sample):
    assert P.image_alpha_statistics(sample) == {
        "transparent": 0,
        "semi_transparent": 219,
        "opaque": 1317,
    }


def test_opaque_rgb_color_count_golden(sample):
    assert P.opaque_rgb_color_count(sample) == 1536


def test_build_palette_golden(sample):
    pal = P.build_color_reduction_palette(sample, 8)
    assert pal.shape == (8, 3)
    assert _ah(pal) == "f8249abdf4268a4d"


def test_apply_palette_golden(sample):
    pal = P.build_color_reduction_palette(sample, 8)
    out = P.apply_color_reduction_palette(sample, pal)
    assert _qh(out) == "ead24d633b2eef2e"
