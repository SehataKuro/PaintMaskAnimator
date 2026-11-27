"""分離したプレビュー計算を分離前の式と比較する。"""

from unittest.mock import Mock

import numpy as np
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator import color_ops, color_reduction_preview as preview, imaging
from paintmaskanimator.color_reduction import ColorReductionDialog
from paintmaskanimator.errors import OperationError
from paintmaskanimator.i18n import tr


@pytest.mark.parametrize("shape", [(1, 1), (1, 17), (19, 1), (31, 37)])
@pytest.mark.parametrize("background", [None, (255, 255, 255), (0, 0, 0), (43, 92, 171)])
@pytest.mark.parametrize("points", [
    [(0, 0), (1, 1)],
    [(0, 0), (0.37, 0.72), (1, 1)],
    ["bad", (0,), None, (0.3, 0.48)],
    [(0, 0), (0.001, 1), (0.999, 0), (1, 1)],
])
def test_live_pixels(shape, background, points):
    rgba = np.random.default_rng(55).integers(0, 256, (*shape, 4), dtype=np.uint8)
    rgba[..., 3].flat[:2] = [127, 0][:rgba[..., 3].size]
    if background is not None:
        sign = -1 if background[0] == 255 else 1
        rgba[..., :3].reshape(-1, 3)[:4] = np.asarray(background) + sign * np.array(
            [[5, 5, 5], [6, 6, 2], [7, 5, 0], [0, 0, 0]]
        )[: min(4, shape[0] * shape[1])]

    expected_mask = preview.tone_preview_background_mask_numpy(rgba, background)
    mask = preview.tone_preview_background_mask(rgba, background)
    if expected_mask is None:
        assert mask is None
    else:
        assert expected_mask.flat[0]
        if expected_mask.size > 1:
            assert not expected_mask.flat[1]
            assert expected_mask.flat[2]
        np.testing.assert_array_equal(mask, expected_mask, strict=True)

    source_before = rgba.copy()
    lut = color_ops.tone_curve_lut(points)
    expected = preview.apply_tone_preview_lut_numpy(rgba, lut, expected_mask, background)
    np.testing.assert_array_equal(
        preview.apply_tone_preview_lut_from_source(rgba, lut, background), expected, strict=True
    )
    actual = preview.apply_tone_preview_lut(rgba, lut, mask, background)
    np.testing.assert_array_equal(actual, expected, strict=True)
    np.testing.assert_array_equal(actual[..., 3], source_before[..., 3])
    np.testing.assert_array_equal(rgba, source_before)


@pytest.mark.parametrize("background", [
    (43.5, 92.8, 171.9), (np.int64(43), np.uint8(92), np.int16(171)),
    np.array([43, 92, 171]), (43,), (43, 92), (43, 92, 171, 0),
    (-1, 0, 0), (256, 92, 171), (65536, 0, 0), (np.int64(65536), 0, 0),
])
def test_unusual_background_matches_numpy(background):
    rgba = np.array([[[43, 92, 171, 0], [43, 43, 43, 127], [0, 0, 0, 255],
                      [255, 92, 171, 42]]], dtype=np.uint8)
    lut = np.zeros(256, dtype=np.uint8)

    def check(actual, reference):
        try:
            expected = reference()
        except (ValueError, TypeError, OverflowError) as exc:
            with pytest.raises(type(exc)) as error:
                actual()
            assert str(error.value) == str(exc)
        else:
            np.testing.assert_array_equal(actual(), expected, strict=True)

    def mask():
        return preview.tone_preview_background_mask_numpy(rgba, background)

    check(lambda: preview.tone_preview_background_mask(rgba, background), mask)
    check(
        lambda: preview.apply_tone_preview_lut_from_source(rgba, lut, background),
        lambda: preview.apply_tone_preview_lut_numpy(rgba, lut, mask(), background),
    )
    explicit_mask = np.ones(rgba.shape[:2], dtype=bool)
    check(
        lambda: preview.apply_tone_preview_lut(rgba, lut, explicit_mask, background),
        lambda: preview.apply_tone_preview_lut_numpy(rgba, lut, explicit_mask, background),
    )


@pytest.mark.parametrize("layout", ["strided", "rgb", "uint16"])
def test_unusual_array_layout_matches_numpy(layout):
    rgba = np.full((2, 8, 4), 32, dtype=np.uint8)
    rgba = {"strided": rgba[:, ::2], "rgb": rgba[..., :3],
            "uint16": rgba.astype(np.uint16)}[layout]
    lut = color_ops.tone_curve_lut([(0, 0), (0.5, 0.1), (1, 1)])
    mask = preview.tone_preview_background_mask_numpy(rgba, (32, 32, 32))
    np.testing.assert_array_equal(
        preview.tone_preview_background_mask(rgba, (32, 32, 32)), mask, strict=True
    )
    np.testing.assert_array_equal(
        preview.apply_tone_preview_lut(rgba, lut, mask, (32, 32, 32)),
        preview.apply_tone_preview_lut_numpy(rgba, lut, mask, (32, 32, 32)), strict=True,
    )


def test_explicit_mask_controls_background_restoration():
    rgba = np.array([[[0, 0, 0, 0], [255, 255, 255, 127]]], dtype=np.uint8)
    lut = np.full(256, 42, dtype=np.uint8)
    mask = np.array([[False, True]])
    background = (0, 0, 0)
    np.testing.assert_array_equal(
        preview.apply_tone_preview_lut(rgba, lut, mask, background),
        preview.apply_tone_preview_lut_numpy(rgba, lut, mask, background), strict=True,
    )


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("background", [None, (43, 92, 171), (43,)])
def test_live_dialog_matches_head_pixels(qapp, monkeypatch, background):
    monkeypatch.setattr(ColorReductionDialog, "_update_preview", lambda self: None)
    rgba = np.random.default_rng(555).integers(0, 256, (13, 17, 4), dtype=np.uint8)
    rgba[0, :3] = [[48, 97, 176, 127], [48, 97, 177, 0], [43, 43, 43, 255]]
    image = imaging.rgba_array_to_qimage(rgba)
    source = imaging.qimage_rgba_array(image)
    dialog = ColorReductionDialog(image, 32, background_rgb=background)
    dialog.tone_curve.blockSignals(True)
    try:
        mask = preview.tone_preview_background_mask_numpy(source, background)
        for points, current_background in [
            ([(0, 0), (1, 1)], background),
            ([(0, 0), (0.5, 0.1), (1, 1)], background),
            ([(0, 0), (0.5, 0.99), (1, 1)], (32, 64, 128)),
        ]:
            dialog.tone_curve.setPoints(points)
            dialog.background_rgb = current_background
            lut = color_ops.tone_curve_lut(dialog.tone_curve_points())
            expected = imaging.rgba_array_to_qimage(
                preview.apply_tone_preview_lut_numpy(source, lut, mask, current_background)
            )
            dialog._show_tone_curve_live_preview(force=True)
            actual = dialog._tone_adjusted_preview_image
            assert actual.size() == expected.size()
            assert actual.format() == expected.format()
            assert bytes(actual.constBits()) == bytes(expected.constBits())
            assert dialog.original_title.text() == tr("元画像（トーンカーブ）　100%")
            if background is None or (len(background) == 3 and background == current_background):
                assert dialog._tone_preview_background_mask is None
        np.testing.assert_array_equal(dialog._tone_preview_source_rgba, source)
    finally:
        dialog.close()


def test_live_dialog_restores_confirmed_preview_after_invalid_background(qapp, monkeypatch):
    monkeypatch.setattr(ColorReductionDialog, "_update_preview", lambda self: None)
    dialog = ColorReductionDialog(imaging.rgba_array_to_qimage(
        np.zeros((2, 2, 4), dtype=np.uint8)), 2, background_rgb=(1, 2))
    restore = Mock()
    monkeypatch.setattr(dialog, "_restore_confirmed_preview", restore)
    try:
        dialog._show_tone_curve_live_preview(force=True)
        restore.assert_called_once_with(show_dirty_state=True)
        cached_source = dialog._tone_preview_source_rgba
        assert cached_source is not None
        assert dialog._tone_preview_background_mask is None
        dialog._show_tone_curve_live_preview(force=True)
        assert dialog._tone_preview_source_rgba is cached_source
        restore.assert_called_once()
    finally:
        dialog.close()


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
