"""Rust の 2 値化と取り込みの前処理（pma_core::image_ops）を、Python の経路と画素で突き合わせる。

デジタル TP のアンチエイリアス除去・離れた点のゴミ取り・弱いぼかし・色相の特徴量・
優先色・共通パレット・2 値化（今のレイヤー）・外周の背景の透明化を、同じ入力で
Python（numpy・Qt）と Rust に通し、同じ画素・同じ色になることを確かめる。
"""

import os
import random

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import pma_native  # noqa: E402
from paintmaskanimator import color_ops, colors, despeckle, imaging, soft_blur  # noqa: E402
from paintmaskanimator import digital_antialias  # noqa: E402
from paintmaskanimator.canvas_image_import import ImageImportMixin  # noqa: E402
from paintmaskanimator.pma_image import PmaImage  # noqa: E402

PREMULTIPLIED = QImage.Format.Format_ARGB32_Premultiplied
INKS = [(0, 0, 0), (230, 20, 30), (20, 60, 220), (30, 180, 70), (240, 220, 30), (120, 60, 30)]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _normalized(rgba):
    return PmaImage.from_qimage(imaging.rgba_array_to_qimage(rgba)).rgba().copy()


def _core(rgba):
    height, width = rgba.shape[:2]
    return pma_native.RgbaImage(width, height, np.ascontiguousarray(rgba).tobytes())


def _core_rgba(image):
    return np.frombuffer(image.rgba_bytes(), np.uint8).reshape(image.height, image.width, 4)


def _from_bytes(data, rgba):
    return np.frombuffer(data, np.uint8).reshape(rgba.shape)


def _digital(rng, width, height):
    """2 値の塗り分けに、境界のアンチエイリアスと半透明が混ざった画像。"""
    rgba = np.zeros((height, width, 4), np.uint8)
    rgba[:, :] = (255, 255, 255, 255) if rng.random() < 0.8 else (0, 0, 0, 0)
    palette = rng.sample(INKS, rng.randint(1, 4))
    for _ in range(rng.randint(1, 5)):
        x0, y0 = rng.randrange(width), rng.randrange(height)
        rgba[y0 : y0 + rng.randint(2, 12), x0 : x0 + rng.randint(2, 12)] = (
            *rng.choice(palette),
            255,
        )
    for _ in range(rng.randint(0, 3)):
        y = rng.randrange(height)
        rgba[y, :, :3] = rng.choice(palette)
        rgba[y, :, 3] = 255
    for _ in range(rng.randint(0, 25)):
        x, y = rng.randrange(width), rng.randrange(height)
        a, b = np.array(rng.choice(palette)), np.array(rng.choice(palette + [(255, 255, 255)]))
        t = rng.random()
        rgba[y, x, :3] = np.round(a * t + b * (1 - t)).astype(np.uint8)
        rgba[y, x, 3] = rng.choice([255, 255, rng.randrange(1, 255)])
    return _normalized(rgba)


def _scan(rng, width, height):
    """紙（明るいばらつき）に、ざらついた色の線と塗りが乗ったスキャン風の画像。"""
    base = np.array([rng.randint(235, 255), rng.randint(235, 255), rng.randint(228, 255)])
    rgba = np.zeros((height, width, 4), np.uint8)
    rgba[:, :, :3] = np.clip(base + np.array([[[rng.randint(-6, 6)]]]), 0, 255)
    rgba[:, :, 3] = 255
    for _ in range(rng.randint(1, 6)):
        ink = np.array(rng.choice(INKS))
        x0, y0 = rng.randrange(width), rng.randrange(height)
        rgba[y0 : y0 + rng.randint(1, 14), x0 : x0 + rng.randint(1, 14), :3] = ink
    noise = np.array([[rng.randint(-12, 12) for _ in range(width)] for _ in range(height)])
    rgba[:, :, :3] = np.clip(rgba[:, :, :3].astype(int) + noise[:, :, None], 0, 255)
    if rng.random() < 0.3:
        rgba[rng.randrange(height), :, 3] = rng.randrange(0, 255)
    return _normalized(rgba)


# --- デジタル TP・ゴミ取り・ぼかし ----------------------------------------------


def test_digital_antialias_matches_python(app):
    rng = random.Random(63)
    for _ in range(250):
        width, height = rng.randint(1, 30), rng.randint(1, 30)
        rgba = _digital(rng, width, height)
        assert pma_native.image_ops_count_antialias(rgba.tobytes(), width, height) == (
            digital_antialias.count_antialias(rgba)
        )
        out, changed = digital_antialias.remove_antialias(rgba)
        got, got_changed = pma_native.image_ops_remove_antialias(rgba.tobytes(), width, height)
        assert got_changed == changed
        np.testing.assert_array_equal(_from_bytes(got, rgba), out)


def test_isolated_specks_match_python(app):
    rng = random.Random(10)
    for _ in range(300):
        width, height = rng.randint(1, 30), rng.randint(1, 30)
        rgba = np.zeros((height, width, 4), np.uint8)
        for _dot in range(rng.randint(0, 40)):
            x, y = rng.randrange(width), rng.randrange(height)
            rgba[y : y + rng.randint(1, 3), x : x + rng.randint(1, 3)] = (*rng.choice(INKS), 255)
        size = rng.choice([0, 1, 2, 3, 5, 9])
        out, removed = despeckle.remove_isolated_specks(rgba, size)
        got, got_removed = pma_native.image_ops_remove_isolated_specks(
            rgba.tobytes(), width, height, size
        )
        assert got_removed == removed
        np.testing.assert_array_equal(_from_bytes(got, rgba), out)


def test_soft_blur_matches_python(app):
    rng = random.Random(17)
    for _ in range(120):
        width, height = rng.randint(1, 40), rng.randint(1, 40)
        rgba = _scan(rng, width, height)
        sigma = rng.choice([0.0, 0.3, 0.5, 1.0, 1.25, 2.0, 2.7, 3.0, 5.0, rng.uniform(0.1, 3.0)])
        got = pma_native.image_ops_soft_blur(rgba.tobytes(), width, height, sigma)
        np.testing.assert_array_equal(_from_bytes(got, rgba), soft_blur.soft_blur(rgba, sigma))


# --- 共通パレット ----------------------------------------------------------------


def test_hsv_features_match_numpy_for_many_colours(app):
    rng = np.random.default_rng(4)
    rgb = rng.integers(0, 256, (60000, 3)).astype(np.uint8)
    rgb[:256] = np.arange(256)[:, None]
    got = np.frombuffer(
        pma_native.image_ops_rgb_hsv_features([tuple(int(v) for v in c) for c in rgb]),
        np.float32,
    ).reshape(-1, 4)
    expected = colors.rgb_hsv_features(rgb)
    assert np.array_equal(got, expected)


def test_priority_and_hue_cluster_match_python(app):
    rng = random.Random(6)
    for _ in range(150):
        rgba = _scan(rng, rng.randint(4, 40), rng.randint(4, 40))
        sample = rgba[:, :, :3].reshape(-1, 3)
        maximum = rng.choice([0, 1, 3, 6, 12])
        pairs = [tuple(int(v) for v in c) for c in sample]
        expected = colors.priority_palette_colors_from_samples(sample, maximum)
        assert pma_native.image_ops_priority_palette_colors(pairs, maximum) == [
            tuple(int(v) for v in c) for c in expected
        ]
        count = rng.choice([1, 2, 4, 8, 16])
        expected = color_ops._hue_cluster_palette_from_samples(sample, count)
        assert pma_native.image_ops_hue_cluster_palette(pairs, count) == [
            tuple(int(v) for v in c) for c in expected
        ]


def _qimage(rgba):
    return imaging.rgba_array_to_qimage(rgba)


def test_build_palette_matches_python(app):
    rng = random.Random(1058)
    for _ in range(60):
        width, height = rng.randint(4, 48), rng.randint(4, 48)
        images = [_scan(rng, width, height) for _ in range(rng.randint(1, 3))]
        count = rng.choice([2, 3, 5, 8, 12])
        line = rng.random() < 0.4
        opaque = line or rng.random() < 0.5
        background = (255, 255, 255) if line else rng.choice([None, (250, 248, 240)])
        threshold = rng.choice([1, 64, 128, 254])
        max_samples = rng.choice([262144, 131072, 500, 77])
        expected = color_ops.build_color_reduction_palette(
            _qimage(images[0]),
            count,
            alpha_threshold=threshold,
            max_samples=max_samples,
            opaque_background=opaque,
            background_rgb=background,
            extraction_mode="line" if line else "surface",
            extra_images=[_qimage(image) for image in images[1:]],
        )
        got = pma_native.image_ops_build_palette(
            [_core(image) for image in images],
            count,
            threshold,
            max_samples,
            opaque,
            background,
            line,
        )
        assert got == [tuple(int(v) for v in c) for c in np.asarray(expected).reshape(-1, 3)]


# --- 2 値化（今のレイヤー）・外周の背景 ----------------------------------------------


class _Canvas(ImageImportMixin):
    """``reduce_image_with_settings`` が使う PaintCanvas の口だけを持つ。"""

    apply_tone_curve = staticmethod(color_ops.apply_tone_curve)
    apply_color_reduction_palette = staticmethod(color_ops.apply_color_reduction_palette)


def _python_binarize(rgba, settings):
    """``MainWindow.binarize_layer`` の op と同じ判定で、層に書き戻す画素（無ければ None）。"""
    image = PmaImage.from_qimage(_qimage(rgba)).to_qimage()
    reduced = _Canvas().reduce_image_with_settings(image, settings)
    assert reduced is not None
    reduced = reduced.convertToFormat(PREMULTIPLIED)
    if reduced == image.convertToFormat(PREMULTIPLIED):
        return None
    return PmaImage.from_qimage(reduced).rgba().copy()


def test_binarize_matches_python(app):
    rng = random.Random(62)
    for case in range(120):
        width, height = rng.randint(2, 40), rng.randint(2, 40)
        mode = rng.choice(["surface", "line", "digital"])
        rgba = _digital(rng, width, height) if mode == "digital" else _scan(rng, width, height)
        palette = None
        if mode != "digital" and rng.random() < 0.85:
            palette = rng.sample(INKS + [(255, 255, 255), (250, 248, 240)], rng.randint(1, 5))
        points = rng.choice(
            [
                [(0.0, 0.0), (1.0, 1.0)],
                [(0.0, 0.0), (0.4, 0.2), (1.0, 1.0)],
                [(0.0, 0.1), (0.6, 0.9), (1.0, 1.0)],
                [],
            ]
        )
        settings = {
            "palette": None if palette is None else np.asarray(palette, np.uint8),
            "extraction_mode": mode,
            "flatten_paper": False,
            "despeckle": rng.choice([0, 0, 1, 3]),
            "blur": rng.choice([0.0, 0.0, 0.6, 1.5]),
            "alpha_threshold": rng.choice([64, 128, 200]),
            "opaque_background": rng.random() < 0.5,
            "background_rgb": rng.choice([None, (255, 255, 255), (250, 248, 240)]),
            "tone_curve_points": points,
        }
        if mode == "digital":
            settings = {"extraction_mode": "digital", "palette": None}
        expected = _python_binarize(rgba, settings)
        got = pma_native.image_ops_binarize(
            _core(rgba),
            mode,
            palette=palette,
            blur=float(settings.get("blur") or 0),
            despeckle_pixels=int(settings.get("despeckle") or 0),
            alpha_threshold=int(settings.get("alpha_threshold", 128)),
            opaque_background=bool(settings.get("opaque_background", False)),
            background=settings.get("background_rgb"),
            tone_curve_points=settings.get("tone_curve_points", [(0.0, 0.0), (1.0, 1.0)]),
        )
        if expected is None:
            assert got is None, case
            continue
        assert got is not None, case
        image, rect, pixels = got
        np.testing.assert_array_equal(_core_rgba(image), expected)
        changed = (expected != rgba).any(axis=2)
        assert pixels == int(changed.sum())
        ys, xs = np.nonzero(changed)
        assert rect == (
            int(xs.min()),
            int(ys.min()),
            int(xs.max() - xs.min() + 1),
            int(ys.max() - ys.min() + 1),
        )


def test_flatten_paper_is_reported_as_not_ported(app):
    rgba = _normalized(np.full((4, 4, 4), 255, np.uint8))
    with pytest.raises(ValueError):
        pma_native.image_ops_binarize(_core(rgba), "surface", flatten_paper=True)


def test_border_background_transparent_matches_python(app):
    rng = random.Random(152)
    for _ in range(200):
        width, height = rng.randint(1, 30), rng.randint(1, 30)
        rgba = _scan(rng, width, height)
        if rng.random() < 0.2:
            rgba[:, :, :3] = rng.choice(INKS)
        image = PmaImage.from_qimage(_qimage(rgba)).to_qimage()
        result = _Canvas().make_border_background_transparent(image)
        got = pma_native.image_ops_border_background_transparent(rgba.tobytes(), width, height)
        if result is image:
            assert got is None
            continue
        assert result is not None
        expected = PmaImage.from_qimage(result).rgba()
        np.testing.assert_array_equal(_from_bytes(got, rgba), expected)
