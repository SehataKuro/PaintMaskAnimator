"""分離したプレビュー計算を分離前の式と比較する。"""

from unittest.mock import Mock

import numpy as np
import pytest
from PySide6.QtGui import QImage

from paintmaskanimator import color_ops, color_reduction_preview as preview, imaging
from paintmaskanimator.errors import OperationError
from paintmaskanimator.i18n import tr


@pytest.mark.parametrize("shape", [(1, 1), (1, 17), (19, 1), (31, 37)])
@pytest.mark.parametrize("background", [None, (255, 255, 255), (0, 0, 0), (43, 92, 171)])
@pytest.mark.parametrize("points", [
    [(0, 0), (1, 1)],
    [(0, 0), (0.37, 0.72), (1, 1)],
    ["bad", (0,), None, (0.3, 0.48)],
])
def test_live_pixels(shape, background, points):
    rgba = np.random.default_rng(55).integers(0, 256, (*shape, 4), dtype=np.uint8)
    rgba[..., 3].flat[0] = 127
    if background is not None:
        sign = -1 if background[0] == 255 else 1
        rgba[..., :3].reshape(-1, 3)[:3] = np.asarray(background) + sign * np.array(
            [[5, 5, 5], [5, 5, 6], [0, 0, 0]]
        )[: min(3, shape[0] * shape[1])]

    expected_mask = None
    if background is not None:
        difference = rgba[:, :, :3].astype(np.int16) - np.asarray(background, dtype=np.int16)
        expected_mask = np.sum(
            difference.astype(np.int32) * difference.astype(np.int32), axis=2
        ) <= (5 * 5 * 3)
        assert expected_mask.flat[0]
        if expected_mask.size > 1:
            assert not expected_mask.flat[1]

    mask = preview.tone_preview_background_mask(rgba, background)
    if expected_mask is None:
        assert mask is None
    else:
        np.testing.assert_array_equal(mask, expected_mask, strict=True)

    source_before = rgba.copy()
    lut = color_ops.tone_curve_lut(points)
    expected = rgba.copy()
    expected[:, :, :3] = lut[expected[:, :, :3]]
    if expected_mask is not None:
        expected[:, :, :3][expected_mask] = np.asarray(background, dtype=np.uint8)
    actual = preview.apply_tone_preview_lut(rgba, lut, mask, background)
    np.testing.assert_array_equal(actual, expected, strict=True)
    np.testing.assert_array_equal(actual[..., 3], source_before[..., 3])
    np.testing.assert_array_equal(rgba, source_before)


@pytest.mark.parametrize(("points", "expected"), [
    ([], ((0.0, 0.0), (1.0, 1.0))),
    (["bad", (0,), None], ((0.0, 0.0), (1.0, 1.0))),
    (["bad", (0,), None, ("0.876543", -0.123456, 42), "12", (0, 0)],
     ((0.87654, -0.12346), (1.0, 2.0), (0.0, 0.0))),
])
def test_tone_curve_key(points, expected):
    assert preview.tone_curve_points_key(iter(points)) == expected


@pytest.mark.parametrize("error_type", [TypeError, ValueError, IndexError, AttributeError])
def test_tone_key_load_fallback(error_type):
    error = error_type("invalid key")
    key = Mock(side_effect=error)
    log = Mock()
    points, tone_key = preview.resolve_tone_preview_points([(0.4, 0.6)], key, log)
    assert points == [(0.0, 0.0), (1.0, 1.0)]
    assert tone_key == tuple(points)
    key.assert_called_once_with()
    log.debug.assert_called_once_with("tone_curve_points_key() failed, using identity: %s", error)


def test_unhandled_tone_key_error():
    with pytest.raises(RuntimeError, match="unexpected"):
        preview.resolve_tone_preview_points([], Mock(side_effect=RuntimeError("unexpected")), Mock())


@pytest.mark.parametrize("mode", ["line", "surface", None, "other"])
@pytest.mark.parametrize("opaque,background", [(False, None), (True, (5, 72, 93))])
def test_preview_settings(mode, opaque, background):
    tone_key = ((0.37, 0.72),)
    actual = preview.resolve_color_preview_settings(mode, 13, tone_key, opaque, background)
    if mode == "line":
        assert actual == (("line", 13, tone_key), True, (255, 255, 255))
    else:
        assert actual == (("surface", 13, tone_key), opaque, background)


@pytest.mark.parametrize("mode", ["line", "surface"])
def test_color_preview_pipeline(mode):
    rng = np.random.default_rng(551)
    source, reference = [
        imaging.rgba_array_to_qimage(rng.integers(0, 256, (29, 33, 4), dtype=np.uint8))
        for _ in range(2)
    ]
    points = [(0.0, 0.0), (0.4, 0.79), (1.0, 1.0)]
    background = (255, 255, 255) if mode == "line" else None
    settings = dict(alpha_threshold=83, extraction_mode=mode, background_rgb=background,
                    opaque_background=mode == "line")
    adjusted = color_ops.apply_tone_curve(source, tone_curve_points=points, background_rgb=background)
    expected_palette = color_ops.build_color_reduction_palette(
        adjusted, 5, max_samples=131072, **settings,
        extra_images=[color_ops.apply_tone_curve(
            reference, tone_curve_points=points, background_rgb=background,
        )],
    )
    expected_reduced = color_ops.apply_color_reduction_palette(
        adjusted, expected_palette, tone_curve_points=[(0.0, 0.0), (1.0, 1.0)], **settings
    )

    actual_adjusted = preview.prepare_color_preview_source(source, points, background)
    assert actual_adjusted == adjusted
    progress = Mock()
    palette, reduced = preview.reduce_color_preview(
        actual_adjusted, [reference], 5, tone_points=points, report_progress=progress, **settings
    )
    np.testing.assert_array_equal(palette, expected_palette, strict=True)
    assert reduced == expected_reduced
    label = (
        "線のアンチエイリアスを線色と白背景へ分けています"
        if mode == "line" else "色面の境界を2色へ分けています"
    )
    progress.assert_called_once_with(2, tr(label))


@pytest.mark.parametrize("invalid", [None, QImage()])
def test_null_image_errors(monkeypatch, invalid):
    monkeypatch.setattr(color_ops, "apply_tone_curve", Mock(return_value=invalid))
    with pytest.raises(OperationError) as error:
        preview.prepare_color_preview_source(QImage(), [], None)
    assert str(error.value) == tr("トーンカーブ適用後の画像を生成できませんでした。")

    monkeypatch.setattr(color_ops, "build_color_reduction_palette", Mock(return_value=[]))
    monkeypatch.setattr(color_ops, "apply_color_reduction_palette", Mock(return_value=invalid))
    with pytest.raises(OperationError) as error:
        preview.reduce_color_preview(QImage(), [], 5, 128, [], "surface", False, None, Mock())
    assert str(error.value) == tr("2値化画像を生成できませんでした。")
