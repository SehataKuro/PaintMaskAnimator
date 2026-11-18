"""Rust の部品（pma_native）と numpy 版が同じ結果になることの突き合わせ。"""
from __future__ import annotations

import numpy as np
import pytest
import re
import subprocess
import sys

from paintmaskanimator import bucket_fill, colors, imaging, native

needs_native = pytest.mark.skipif(
    not native.available(), reason="Rust の部品（pma_native）が入っていない"
)
# 64画素の区切り（ビット列の1語）をまたぐ幅を必ず含める。
WIDTHS = [1, 2, 31, 63, 64, 65, 127, 128, 129, 200]


@needs_native
@pytest.mark.parametrize("limit", [0, 1, 3, 101, 300])
def test_unique_colors_matches_numpy(limit):
    rng = np.random.default_rng(18)
    for pixels in (
        np.array([], dtype=np.uint32),
        np.zeros(200, dtype=np.uint32),
        rng.integers(0, 256, 10000, dtype=np.uint32),
        np.array([0xffffff, 0, 0xffffff, 0, 1], dtype=np.uint32),
        rng.integers(0, 1 << 24, (129, 7), dtype=np.uint32)[::-1, ::2],
        np.arange(limit, dtype=np.uint32)[::-1],
        np.repeat(np.array([0xffffff, 0, 63], dtype=np.uint32), [32769, 32769, 3]),
    ):
        assert native.unique_colors(pixels, limit) == np.unique(pixels)[:limit].tolist()


def test_old_backend_is_rejected_with_rebuild_instruction():
    with pytest.raises(ImportError, match="pip install ./native"):
        native.validate_backend(object())


def test_missing_backend_fails_on_import_with_rebuild_instruction():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.modules['pma_native'] = None; "
         "import paintmaskanimator.native"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "pip install ./native" in result.stderr


@needs_native
def test_unique_colors_rejects_invalid_input():
    with pytest.raises(ValueError, match="packed u32"):
        native._native.unique_colors(b"abc", 101)
    with pytest.raises(ValueError, match="24-bit RGB"):
        native._native.unique_colors(np.array([0xffffffff], dtype=np.uint32).tobytes(), 101)
    pixels = np.zeros(65545, dtype=np.uint32)
    pixels[-1] = 0xffffffff
    with pytest.raises(ValueError, match="24-bit RGB"):
        native.unique_colors(pixels, 101)


@needs_native
def test_local_color_variation_matches_numpy():
    rng = np.random.default_rng(51)
    for index in range(300):
        height = index % 39
        width = WIDTHS[index % len(WIDTHS)]
        rgba = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
        rgb = rgba[..., :3]
        if index % 3 == 0:
            rgb = rgb[::-1, ::-1]
        elif index % 3 == 1:
            rgb = rgb.transpose(1, 0, 2)
        actual = colors.local_color_variation(rgb)
        assert actual.dtype == np.int16
        assert actual.flags.writeable
        np.testing.assert_array_equal(actual, colors.local_color_variation_numpy(rgb))

    for rgb in (
        np.zeros((7, 0, 3), dtype=np.uint8),
        np.full((1, 1, 3), 255, dtype=np.uint8),
        np.full((4, 5, 3), 255, dtype=np.uint8),
        np.array([[[0, 255, 0], [255, 0, 255]]], dtype=np.uint8),
        np.array([[[-32768, 32767, 0], [32767, 0, -32768]]], dtype=np.int32),
        np.array([[[1.1, -2.5, 0.9], [256.0, -500.0, 0.0]]]),
        np.ma.array([[[0, 255, 0], [255, 0, 255]]], mask=[[[1, 0, 0], [0, 0, 0]]], dtype=np.uint8),
    ):
        np.testing.assert_array_equal(
            colors.local_color_variation(rgb), colors.local_color_variation_numpy(rgb)
        )


@needs_native
def test_median_cut_palette_matches_numpy():
    rng = np.random.default_rng(52)
    for index in range(330):
        count = int(rng.integers(1, 250))
        # 少数値のチャンネルで、色の同点と範囲の同点を多く作る。
        source = rng.integers(0, [256, 4, 3] if index % 2 else 256, (count, 3), dtype=np.uint8)
        sample = source[rng.integers(0, count, int(rng.integers(0, 2500)))][::-1]
        limit = [-1, 0, 1, 2, 3, 8, 32, 100, count, count + 1, 1 << 80][index % 11]
        actual = colors.median_cut_palette_from_samples(sample, limit)
        assert actual.dtype == np.uint8
        assert actual.flags.writeable
        np.testing.assert_array_equal(
            actual, colors.median_cut_palette_from_samples_numpy(sample, limit)
        )

    for sample, limit in (
        ([], 100),
        ([[0, 255, 0]], 1),
        ([[0, 255, 0]] * 35, 100),
        ([[0, 1, 2], [1, 2, 3], [255, 0, 0], [255, 0, 0]], 2),
        (np.column_stack([np.arange(100), np.zeros((100, 2))]), 100),
    ):
        np.testing.assert_array_equal(
            colors.median_cut_palette_from_samples(sample, limit),
            colors.median_cut_palette_from_samples_numpy(sample, limit),
        )


@needs_native
def test_normalize_tone_curve_points_matches_numpy():
    rng = np.random.default_rng(53)
    for index in range(300):
        points = rng.uniform(-0.25, 1.25, (index % 42, 2)).tolist()
        if index % 3:
            points.extend([(0.5, 0.0), (0.500008, 0.9), (0.500016, 0.8), (0.5, 0.7)])
        assert colors.normalize_tone_curve_points(iter(points)) == (
            colors.normalize_tone_curve_points_numpy(points)
        )

    for points in (
        None, [], [(0.25, -0.0)],
        [(0.4, float("nan")), (float("inf"), 0.3), (float("-inf"), 1.0)],
        [(), None, ("bad", 0.2), ("0.4", "0.5"), (0.3, 0.4, "ignored")],
        [(i * 0.000009, 0.3) for i in range(111112)],
    ):
        assert colors.normalize_tone_curve_points(points) == (
            colors.normalize_tone_curve_points_numpy(points)
        )


def random_masks(seed=7, count=120):
    rng = np.random.default_rng(seed)
    for index in range(count):
        height = int(rng.integers(1, 40))
        width = WIDTHS[index % len(WIDTHS)]
        yield rng, rng.random((height, width)) < rng.uniform(0.25, 0.85)


@needs_native
def test_flood_fill_matches_numpy():
    for rng, passable in random_masks():
        height, width = passable.shape
        seeds = [
            (int(rng.integers(-2, width + 2)), int(rng.integers(-2, height + 2)))
            for _ in range(int(rng.integers(1, 4)))
        ]
        expected = imaging.scanline_connected_region_numpy(passable, seeds)
        assert (imaging.scanline_connected_region(passable, seeds) == expected).all()


@needs_native
@pytest.mark.parametrize("iterations", [0, 1, 2, 4])
def test_dilate_and_erode_match_numpy(iterations):
    for _rng, mask in random_masks(seed=iterations + 11, count=60):
        assert (bucket_fill.dilate(mask, iterations) == bucket_fill.dilate_numpy(mask, iterations)).all()
        assert (bucket_fill.erode(mask, iterations) == bucket_fill.erode_numpy(mask, iterations)).all()


@needs_native
def test_results_are_writable_arrays():
    region = imaging.scanline_connected_region(np.ones((3, 3), bool), (0, 0))
    region[0, 0] = False  # 呼び出し側は結果を書き換えることがある
    assert region.dtype == bool and region.shape == (3, 3)


def test_legacy_opt_out_does_not_disable_required_backend(monkeypatch):
    monkeypatch.setenv("PMA_NO_NATIVE", "1")
    assert native.available()
    region = native.flood_fill(np.ones((2, 2), bool), [(0, 0)])
    assert region.all()


@needs_native
def test_label_regions_matches_numpy():
    from paintmaskanimator import view_preview

    for rng, valid in random_masks(seed=23, count=80):
        codes = rng.integers(0, 3, valid.shape).astype(np.int32)
        labels, count = view_preview.label_regions(codes, valid)
        expected, expected_count = view_preview.label_regions_numpy(codes, valid)
        assert count == expected_count
        assert (labels == expected).all()


@needs_native
@pytest.mark.parametrize("iterations", [0, 1, 2, 12])
def test_native_thinning_matches_numpy(iterations):
    if not hasattr(native._native, "thin"):
        pytest.skip("Native thinning is not installed")
    for _rng, mask in random_masks(seed=iterations + 47, count=60):
        np.testing.assert_array_equal(bucket_fill._thin(mask, iterations),
                                      bucket_fill._thin_numpy(mask, iterations))
    for shape in ((1, 1), (1, 20), (20, 1), (25, 31)):
        mask = np.ones(shape, dtype=bool)
        np.testing.assert_array_equal(bucket_fill._thin(mask, iterations),
                                      bucket_fill._thin_numpy(mask, iterations))


def test_backend_contract_includes_thinning():
    native.validate_backend(native._native)
    assert "thin" in native.REQUIRED_API


@needs_native
@pytest.mark.parametrize("line_luma", [0, 110, 255])
@pytest.mark.parametrize("radius", [0, 1, 2, 8])
@pytest.mark.parametrize("majority,isolation", [(0, 0), (5, 1), (9, 8)])
def test_remove_line_noise_matches_numpy(line_luma, radius, majority, isolation):
    from paintmaskanimator import line_noise

    rng = np.random.default_rng(96)
    palette = np.array([
        [240, 240, 240, 255], [255, 255, 0, 127], [0, 0, 0, 255],
        [109, 109, 109, 255], [110, 110, 110, 255],
        [111, 111, 111, 255], [45, 67, 89, 0],
    ], dtype=np.uint8)
    images = [np.zeros(shape, dtype=np.uint8) for shape in
              [(0, 3, 4), (3, 0, 4), (1, 1, 4), (3, 3, 4)]]
    dot = np.tile([0, 0, 0, 255], (3, 3, 1)).astype(np.uint8)
    dot[1, 1] = [255, 255, 0, 42]
    images.append(dot)
    for index, width in enumerate(WIDTHS):
        height = int(rng.integers(1, 41))
        pixels = palette[rng.choice(len(palette), (height, width),
                                    p=[.30, .30, .12, .06, .06, .06, .10])]
        if index % 3 == 0:
            pixels[..., 3] = 255
        elif index % 3 == 1:
            pixels[..., 3] = 0
        else:
            pixels = pixels[:, ::-1]
        images.append(pixels)
    options = dict(line_luma=line_luma, radius=radius,
                   majority=majority, isolation=isolation)
    for rgba in images:
        before = rgba.copy()
        expected, count = line_noise.remove_line_noise_numpy(rgba, **options)
        actual, changed = line_noise.remove_line_noise(rgba, **options)
        assert changed == count
        assert np.array_equal(actual, expected)
        assert np.array_equal(rgba, before)
        assert actual.dtype == np.uint8 and actual.shape == rgba.shape
        if not changed:
            assert actual is rgba
        else:
            assert actual.flags.writeable
            assert not np.shares_memory(actual, rgba)


@needs_native
@pytest.mark.parametrize("line_luma,majority,isolation,radius", [
    (-1, -1, -1, -2), (256, 0, 8, 1),
    (110, -(2**40), 2**40, 2), (2**40, 2**40, 0, 8),
])
def test_remove_line_noise_signed_parameters(line_luma, majority, isolation, radius):
    from paintmaskanimator import line_noise

    rgba = np.tile([240, 240, 240, 255], (5, 5, 1)).astype(np.uint8)
    rgba[1, 1] = [0, 0, 0, 255]
    rgba[2, 2] = [255, 255, 0, 1]
    options = dict(line_luma=line_luma, majority=majority,
                   isolation=isolation, radius=radius)
    expected, count = line_noise.remove_line_noise_numpy(rgba, **options)
    actual, changed = line_noise.remove_line_noise(rgba, **options)
    assert changed == count
    assert np.array_equal(actual, expected)
    if not changed:
        assert actual is rgba


@needs_native
def test_remove_line_noise_native_entry_and_validation():
    rgba = np.zeros((3, 3, 4), dtype=np.uint8)
    actual, changed = native.remove_line_noise(rgba, 110, 2, 5, 0)
    assert changed == 0 and np.array_equal(actual, rgba)
    assert actual.flags.writeable and not np.shares_memory(actual, rgba)
    with pytest.raises(ValueError, match="rgba has 35 bytes, expected 3 x 3 x 4"):
        native._native.remove_line_noise(bytes(35), 3, 3, 110, 2, 5, 0)


@needs_native
@pytest.mark.parametrize("shape", [(3, 3), (3, 3, 3), (1, 3, 4), (3, 2, 4)])
def test_remove_line_noise_early_return(shape):
    from paintmaskanimator import line_noise

    rgba = np.zeros(shape, dtype=np.uint8)
    actual, changed = line_noise.remove_line_noise(rgba)
    assert actual is rgba and changed == 0


@needs_native
def test_remove_line_noise_tie_follows_shift_direction():
    from paintmaskanimator import line_noise

    rgba = np.tile([200, 200, 200, 255], (3, 3, 1)).astype(np.uint8)
    rgba.reshape(-1, 4)[[0, 1, 2, 8]] = [0, 0, 0, 255]
    rgba[1, 1] = [255, 255, 0, 42]
    expected, count = line_noise.remove_line_noise_numpy(rgba, majority=4)
    actual, changed = line_noise.remove_line_noise(rgba, majority=4)
    assert count == changed == 1
    assert np.array_equal(actual, expected)
    assert tuple(actual[1, 1]) == (0, 0, 0, 255)


@pytest.mark.parametrize("width", WIDTHS)
def test_extend_line_ends_matches_numpy(width):
    rng = np.random.default_rng(4100 + width)
    shape = (37, width)
    masks = [np.zeros(shape, bool), np.ones(shape, bool), rng.random(shape) < .08,
             rng.random(shape) < .7]
    lines = np.zeros(shape, bool)
    for _ in range(12):
        x, y = int(rng.integers(width)), int(rng.integers(37))
        dx, dy = int(rng.integers(-11, 12)), int(rng.integers(-11, 12))
        for t in range(16):
            px, py = round(x + dx * t / 11), round(y + dy * t / 11)
            if 0 <= px < width and 0 <= py < 37:
                lines[py, px] = True
    masks.extend([lines, np.pad(np.ones((1, 1), bool), ((0, 36), (0, width - 1)))])
    for length in (1, 2, 5, 20, 50, 10000):
        cache = {}
        for boundary in masks:
            expected = bucket_fill.extend_line_ends_numpy(boundary, length)
            np.testing.assert_array_equal(bucket_fill.extend_line_ends(boundary, length), expected)
            for _ in range(2):
                np.testing.assert_array_equal(
                    bucket_fill.extend_line_ends(boundary, length, cache), expected)


@pytest.mark.parametrize("dx,dy", [(2, 1), (-2, 1), (1, -2), (-1, -2),
                                   (11, 7), (-11, -7), (0, 11), (11, 0)])
def test_extend_line_ends_direction_and_rounding(dx, dy):
    # Skeleton を直接渡し、.5 丸め・負方向・norm の向きを細線化と分離して比較。
    boundary = np.zeros((61, 61), bool)
    for t in range(12):
        boundary[round(30 + dy * t / 11), round(30 + dx * t / 11)] = True
    skeleton = boundary.copy()
    expected = bucket_fill.extend_line_ends_numpy(boundary, 50)
    np.testing.assert_array_equal(bucket_fill.extend_line_ends(boundary, 50), expected)
    assert native.extend_line_ends(boundary, [(0, 61, 0, 61, skeleton)], 50).shape == boundary.shape


def test_extend_line_ends_native_rejects_invalid_input():
    backend = native._native
    for boundary, width, height, strips, length in [
        (b'', 2, 2, [], 1), (b'\0', 1, 1, [(0, 2, 0, 1, b'\0\0')], 1),
        (b'\0', 1, 1, [(0, 1, 0, 1, b'')], 1), (b'\0', 1, 1, [], 0),
    ]:
        with pytest.raises(ValueError):
            backend.extend_line_ends(boundary, width, height, strips, length)


@pytest.mark.parametrize("width", WIDTHS)
def test_fill_target_mask_matches_numpy(width):
    rng = np.random.default_rng(4200 + width)
    pixels = rng.integers(0, 256, (23, width, 4), dtype=np.uint8)
    edges = np.array([0, 47, 48, 49, 239, 240, 241, 255], dtype=np.uint8)
    pixels[::2, :, :3] = rng.choice(edges, pixels[::2, :, :3].shape)
    pixels[:, :, 3] = rng.choice([0, 1, 49, 50, 51, 254, 255], (23, width))
    pixels[0, :, :3] = 255
    pixels[1, :, :3] = 0
    for image in (pixels, pixels[:, ::-1], pixels.transpose(1, 0, 2)):
        for target in (*bucket_fill.TARGET_COLORS, "unknown_target"):
            for category in (None, (0, 0, 0), (255, 255, 255), (48, 240, 49)):
                for packed in (None, imaging.rgba_packed_view(image)):
                    expected = bucket_fill.fill_target_mask_numpy(image, target, category, packed)
                    actual = bucket_fill.fill_target_mask(image, target, category, packed)
                    assert actual.dtype == np.bool_ and actual.shape == image.shape[:2]
                    np.testing.assert_array_equal(actual, expected)


def test_fill_target_mask_native_rejects_invalid_input():
    for data, width, height in [(b'', 1, 1), (b'\0' * 3, 1, 1),
                                (b'\0' * 4, 2, 1), (b'', 2**63, 4)]:
        with pytest.raises(ValueError):
            native._native.fill_target_mask(data, width, height, "all", None)
    assert native._native.fill_target_mask(b'', 0, 3, "all", None) == b''
    assert "fill_target_mask" in native.REQUIRED_API


@needs_native
def test_compute_fill_region_matches_numpy():
    """固定線画、全対象色、選択、索引の組合せで全フィールドを比較する。"""
    from dataclasses import replace
    rng = np.random.default_rng(430)
    pixels = np.full((19, 23, 4), 255, np.uint8)
    pixels[3:16, 4, :3] = 0
    pixels[3:16, 18, :3] = 0
    pixels[3, 4:19, :3] = 0
    pixels[15, 4:19, :3] = 0
    pixels[3, 10] = 255  # 隙間
    pixels[0:7, 21, :3] = 0  # 端で途切れる線
    pixels[8:10, 9:11] = (120, 30, 80, 255)
    pixels[6:8, 13:15, 3] = 0
    pixels[11, 12] = (10, 10, 10, 30)
    for y, x in rng.integers((1, 1), (18, 22), (12, 2)):
        pixels[y, x] = (0, 0, 0, 255)
    selection = np.zeros(pixels.shape[:2], bool)
    selection[2:17, 2:20] = True
    base = bucket_fill.BucketOptions(
        mask_colors=((120, 30, 80),), island_replacements=(((120, 30, 80), (3, 4, 5)),))
    variants = [{}, {'adjacent': False}, {'require_closed': True},
                {'close_gap': True, 'gap_width': 1},
                {'extend_lines': True, 'extend_length': 4},
                {'include_masks': True},
                {'include_masks': True, 'include_background_mask': True},
                {'island_max_width': 1}, {'island_max_width': 2},
                {'close_gap': True, 'extend_lines': True, 'include_masks': True,
                 'island_max_width': 1, 'require_closed': True}]
    def compare(options, x, y, selected=None, session=None):
        a = bucket_fill.compute_fill_region(pixels, x, y, options, selected, session=session)
        b = bucket_fill.compute_fill_region_numpy(pixels, x, y, options, selected, session=session)
        assert (a.reason, a.target, a.bounds) == (b.reason, b.target, b.bounds)
        assert (a.region is None) == (b.region is None)
        if a.region is not None:
            np.testing.assert_array_equal(a.region, b.region)
        assert len(a.islands) == len(b.islands)
        for (ac, am), (bc, bm) in zip(a.islands, b.islands):
            assert ac == bc
            np.testing.assert_array_equal(am, bm)
    for target in bucket_fill.TARGET_COLORS:
        for changes in variants:
            options = replace(base, target_color=target, **changes)
            for selected in (None, selection):
                for x, y in ((6, 6), (10, 3), (0, 0), (14, 6), (9, 8), (-1, 3)):
                    compare(options, x, y, selected)
            for indexed in (False, True):
                session = bucket_fill.BucketSession(pixels, options, indexed=indexed)
                for x, y in ((6, 6), (10, 3), (0, 0)):
                    compare(options, x, y, session=session)


@needs_native
@pytest.mark.parametrize('bad', ['rgba', 'selection', 'x', 'y', 'dimensions'])
def test_compute_fill_region_rejects_bad_buffer_lengths(bad):
    options = native._fill_options(bucket_fill.BucketOptions())
    args = dict(rgba=bytes(36), width=3, height=3, x=1, y=1,
                options=options, selection=None)
    if bad in ('rgba', 'selection'):
        args[bad] = b''
    elif bad == 'dimensions':
        args['width'] = 2**63
    else:
        args[bad] = -1 if bad == 'x' else 3
    with pytest.raises(ValueError):
        native._native.compute_fill_region_once(**args)


@needs_native
def test_fill_session_fifo_prepare_and_categories():
    pixels = np.zeros((17, 29, 4), np.uint8)
    colors = [(0, 0, 0), (20, 40, 60), (80, 90, 100), (120, 30, 40),
              (180, 90, 20), (255, 255, 255)]
    for x, color in enumerate(colors):
        pixels[:, x*4:(x+1)*4] = (*color, 255)
    options = bucket_fill.BucketOptions(close_gap=True, gap_width=1,
                                      extend_lines=True, extend_length=3)
    session = bucket_fill.BucketSession(pixels, options, indexed=True)
    for x in (1, 5, 9, 13, 17, 21, 25, 1, 9):
        category = bucket_fill.seed_category(pixels, x, 8)
        actual = bucket_fill.compute_fill_region(pixels, x, 8, options, session=session)
        expected = bucket_fill.compute_fill_region_numpy(pixels, x, 8, options)
        assert (actual.reason, actual.target, actual.bounds) == (expected.reason, category, expected.bounds)
        if actual.region is not None:
            np.testing.assert_array_equal(actual.region, expected.region)
        prepared = session.prepare(category)
        reference = bucket_fill._prepare_bucket(pixels, category, options, session.background, None, {})
        for a, b in zip(prepared, reference):
            assert (a is None) == (b is None)
            if a is not None:
                np.testing.assert_array_equal(a, b)
        assert len(session._native().categories()) <= 4
        assert all(c in session._native().categories() for c in session._prepared)
    with pytest.raises(ValueError):
        session._native().compute(29, 0)
    with pytest.raises(ValueError):
        native._native.FillSession(b'', 29, 17, native._fill_options(options))


@needs_native
def test_fill_once_drag_line_cache():
    pixels = np.full((43, 47, 4), 255, np.uint8)
    pixels[4:39, 23] = (0, 0, 0, 255)
    options = bucket_fill.BucketOptions(extend_lines=True, extend_length=8)
    cache = {}
    selection = np.zeros((43, 47), bool)
    selection[2:41, 1:46] = True
    for changed in (False, False, True, True, False):
        image = pixels.copy()
        if changed:
            image[10:20, 23] = 255
        for selected in (None, selection):
            a = bucket_fill.compute_fill_region(image, 10, 10, options, selected, line_cache=cache)
            b = bucket_fill.compute_fill_region_numpy(image, 10, 10, options, selected, line_cache={})
            assert (a.reason, a.target, a.bounds) == (b.reason, b.target, b.bounds)
            np.testing.assert_array_equal(a.region, b.region)
    assert '_native_line_cache' in cache


@needs_native
def test_fill_tracking_matches_numpy():
    from paintmaskanimator import fill_tracking as tracking

    rng = np.random.default_rng(11)
    dimensions = [(19, w) for w in WIDTHS] + [(29, 353), (347, 31), (280, 271), (100, 103)]
    palette = np.array([
        (255, 255, 255, 255), (0, 0, 0, 255), (200, 30, 30, 255),
        (30, 160, 60, 255), (12, 16, 88, 0), (0, 0, 0, 0),
        (200, 30, 30, 128), (30, 160, 60, 128), (70, 80, 200, 255),
    ], dtype=np.uint8)
    for case in range(280):
        height, width = dimensions[case % len(dimensions)]
        previous = np.full((height, width, 4), 255, np.uint8)
        current = previous.copy()
        region = np.zeros((height, width), bool)
        current_region = region.copy()
        for x in range(4, width, 17):
            previous[:, x, :3] = current[:, x, :3] = 0
        for box in range(3):
            w = int(rng.integers(1, width + 1))
            h = int(rng.integers(1, height + 1))
            x = 0 if case % 7 == 0 else int(rng.integers(width - w + 1))
            y = 0 if case % 9 == 0 else int(rng.integers(height - h + 1))
            nx = int(np.clip(x + rng.integers(-25, 26), 0, width - w))
            ny = int(np.clip(y + rng.integers(-25, 26), 0, height - h))
            color = palette[int(rng.integers(len(palette)))]
            for pixels, xx, yy in [(previous, x, y), (current, nx, ny)]:
                pixels[yy:yy + h, xx:xx + w] = palette[1]
                pixels[yy + 1:yy + h - 1, xx + 1:xx + w - 1] = color
            if box == 0 and case % 13:
                if w > 2 and h > 2:
                    region[y + 1:y + h - 1, x + 1:x + w - 1] = True
                    current_region[ny + 1:ny + h - 1, nx + 1:nx + w - 1] = True
                else:
                    region[y, x] = current_region[ny, nx] = True
        if case % 11 == 0:
            previous.fill(255)
            current.fill(255)
        if case % 19 == 0:
            # 同じ頻度の色が6色の採用境界をまたぐ。
            previous[:] = palette[np.indices(region.shape).sum(axis=0) % len(palette)]
            current[:] = np.roll(previous, (1, -1), axis=(0, 1))
        if case % 17 == 0:
            # スライスのメモリ順序と座標の向きが異なる配列。
            previous, current = previous[:, ::-1], current[:, ::-1]
            region, current_region = region[:, ::-1], current_region[:, ::-1]
        lines = tracking.line_art(previous), tracking.line_art(current), region
        margin = (None, -1, 0, 2, 15, 36, 100)[case % 7]
        count = (0, 1, 3, 5)[case % 4]
        radius = (0, 3, 12, 24, 38)[case % 5]
        shifts = tracking.estimate_shifts(*lines, count=count, margin=margin)
        assert shifts == tracking.estimate_shifts_numpy(*lines, count=count, margin=margin), case
        shift = shifts[0] if shifts else (ny - y, nx - x)
        warped = tracking.warp_region(*lines, shift=shift)
        expected = tracking.warp_region_numpy(*lines, shift=shift)
        assert (warped is None) == (expected is None), case
        if warped is not None:
            assert np.array_equal(warped, expected), case
        color_args = previous, current, region
        options = dict(count=count, radius=radius, margin=margin)
        assert tracking.estimate_color_shifts(*color_args, **options) == (
            tracking.estimate_color_shifts_numpy(*color_args, **options)
        ), case
        context = previous, region, current, current_region
        assert tracking.context_similarity(*context, radius=radius) == (
            tracking.context_similarity_numpy(*context, radius=radius)
        ), case


@needs_native
def test_fill_tracking_selects_lower_labels_at_equal_frequency():
    from paintmaskanimator import fill_tracking as tracking

    previous = np.zeros((3, 3, 4), np.uint8)
    previous[..., 3] = 255
    previous[..., 0] = np.arange(9).reshape(3, 3)
    region = np.zeros((3, 3), bool)
    region[1, 1] = True
    current = np.empty_like(previous)
    current[:] = (7, 0, 0, 255)  # 7 と 8 は同頻度の8色から選ばれない。
    assert tracking.estimate_color_shifts(previous, current, region) == []
    assert tracking.estimate_color_shifts_numpy(previous, current, region) == []


@needs_native
@pytest.mark.parametrize("origin", [(0, 0), (19, 27), (96, 128)])
def test_tracking_cropped_windows_match_at_canvas_edges(origin):
    from paintmaskanimator import fill_tracking as tracking

    shape = (167, 227)
    y, x = origin
    yy, xx = np.ogrid[:shape[0], :shape[1]]
    region = np.zeros(shape, dtype=bool)
    region[y:y + 69, x:x + 97] = True
    before = ((yy + xx) % 2 == 0) & ~region
    after = np.roll(before, (7, -11), axis=(0, 1))
    for margin in (-5, 0, 1, 3, 19, 5000, None):
        assert tracking.estimate_shifts(before, after, region, margin=margin) == (
            tracking.estimate_shifts_numpy(before, after, region, margin=margin)
        )
    for shift in ((0, 0), (-13, -22), (14, 21), (-220, 10), (10, 300)):
        assert np.array_equal(
            tracking.warp_region(before, after, region, shift),
            tracking.warp_region_numpy(before, after, region, shift),
        )


def _assert_tracking_equal(actual, expected):
    assert (actual.reason, actual.target, actual.bounds, actual.score) == (
        expected.reason, expected.target, expected.bounds, expected.score
    )
    for name in ("region", "matched"):
        a, b = getattr(actual, name), getattr(expected, name)
        assert (a is None) == (b is None)
        if b is not None:
            np.testing.assert_array_equal(a, b)
    assert [rgb for rgb, _ in actual.islands] == [rgb for rgb, _ in expected.islands]
    for (_, actual_mask), (_, expected_mask) in zip(actual.islands, expected.islands):
        np.testing.assert_array_equal(actual_mask, expected_mask)


@needs_native
def test_track_fill_region_matches_numpy():
    from itertools import product

    from paintmaskanimator import fill_tracking

    rng = np.random.default_rng(4403)
    variants = (
        {}, {"adjacent": False}, {"require_closed": True},
        {"close_gap": True, "gap_width": 2},
        {"extend_lines": True, "extend_length": 4},
        {"include_masks": True},
        {"include_masks": True, "include_background_mask": True},
        {"island_max_width": 1}, {"island_max_width": 2},
        {"close_gap": True, "extend_lines": True, "include_masks": True,
         "island_max_width": 1, "require_closed": True},
    )
    palette = np.array([
        (255, 255, 255, 255), (0, 0, 0, 255), (120, 30, 80, 255),
        (230, 120, 160, 255), (15, 20, 48, 0), (10, 10, 10, 30),
    ], dtype=np.uint8)
    for case, (target, variant, clue) in enumerate(product(
        bucket_fill.TARGET_COLORS, variants, range(4)
    )):
        h, w = map(int, rng.integers(76, 128, size=2))
        bh, bw = map(int, rng.integers(5, (h - 3, w - 3)))
        y = 0 if case % 5 == 0 else int(rng.integers(h - bh + 1))
        x = 0 if case % 7 == 0 else int(rng.integers(w - bw + 1))
        ny = int(np.clip(y + rng.integers(-16, 17), 0, h - bh))
        nx = int(np.clip(x + rng.integers(-16, 17), 0, w - bw))
        color = palette[int(rng.integers(len(palette)))]
        yy, xx = np.indices((h, w))
        texture = (yy + xx) % 2 == 0 if case % 3 == 0 else rng.random((h, w)) < .12
        before = palette[np.where(texture, 3, 0)]
        after = np.roll(before, (ny - y, nx - x), axis=(0, 1))
        for frame, py, px in [(before, y, x), (after, ny, nx)]:
            frame[py:py + bh, px:px + bw] = palette[1]
            frame[py + 1:py + bh - 1, px + 1:px + bw - 1] = color
            frame[py, px + bw // 2] = palette[0]
            frame[py + 2:py + 4, px + 2:px + 4] = palette[2]
        previous = np.zeros((h, w), bool)
        if case % 19:
            previous[y + 1:y + bh - 1, x + 1:x + bw - 1] = True
        selection = (yy < h - 2) & (xx >= 2) if case % 2 else None
        reference = bucket_fill.seed_category(before, x + bw // 2, y + bh // 2)
        options = bucket_fill.BucketOptions(
            target_color=target, mask_colors=((120, 30, 80),),
            island_replacements=(((120, 30, 80), (3, 4, 5)),), **variant,
        )
        args = (after, previous, reference, options, selection)
        kwargs = {
            "previous_pixels": before if clue in (1, 3) else None,
            "previous_lines": fill_tracking.line_art(before) if clue >= 2 else None,
        }
        _assert_tracking_equal(
            bucket_fill.track_fill_region(*args, **kwargs),
            bucket_fill.track_fill_region_numpy(*args, **kwargs),
        )


@needs_native
@pytest.mark.parametrize("split", [False, True])
def test_track_fill_region_preserves_islands_and_bounds(split):
    pixels = np.zeros((24, 26, 4), dtype=np.uint8)
    pixels[..., 3] = 255
    pixels[3:17, 3:16] = 255
    pixels[8:10, 7:9] = (120, 30, 80, 255)
    # この島は左の白領域の矩形を1画素はみ出す。
    pixels[12, 2] = (120, 30, 80, 255)
    previous = np.zeros(pixels.shape[:2], bool)
    previous[3:17, 3:16] = True
    if split:
        pixels[3:17, 10, :3] = 0
        pixels[8:10, 12:14] = (120, 30, 80, 255)
    options = bucket_fill.BucketOptions(
        target_color=bucket_fill.TARGET_ALL,
        island_replacements=(((120, 30, 80), (3, 4, 5)),),
    )
    args = (pixels, previous, None, options)
    result = bucket_fill.track_fill_region(*args)
    _assert_tracking_equal(result, bucket_fill.track_fill_region_numpy(*args))
    assert len(result.islands) == (2 if split else 1)
    assert result.islands[0][1][12, 2]
    assert result.bounds == (3 if split else 2, 3, 16, 17)
    assert result.mask[5, 5] and result.mask[5, 15]


@needs_native
def test_track_fill_region_rescues_small_region():
    rng = np.random.default_rng(702)
    colors = np.array([(210, 180, 150, 255), (75, 120, 110, 255)], dtype=np.uint8)
    before = colors[rng.integers(2, size=(42, 54))]
    previous = np.zeros(before.shape[:2], bool)
    previous[17:19, 18:20] = True
    red = (229, 59, 70)
    before[previous] = (*red, 255)
    after = np.roll(before, 10, axis=1)
    options = bucket_fill.BucketOptions(target_color=bucket_fill.TARGET_ALL)
    args = (after, previous, red, options)
    kwargs = {"previous_pixels": before, "previous_lines": np.zeros_like(previous)}
    assert not bucket_fill.track_fill_region(*args).ok
    result = bucket_fill.track_fill_region(*args, **kwargs)
    _assert_tracking_equal(result, bucket_fill.track_fill_region_numpy(*args, **kwargs))
    assert result.ok
    assert result.bounds == (28, 17, 30, 19)
    np.testing.assert_array_equal(result.matched, result.mask)


@needs_native
def test_fill_decisions_match_numpy_randomly():
    rng = np.random.default_rng(4503)
    palette = np.array([
        (0, 0, 0, 0), (255, 255, 255, 255), (48, 48, 48, 255),
        (48, 49, 48, 255), (220, 80, 60, 255), (220, 80, 60, 0),
        (230, 75, 65, 1),
    ], dtype=np.uint8)
    red, other = (220, 80, 60), (230, 75, 65)
    replacements = ((red, (1, 2, 3)), (other, other), (other, (4, 5, 6)),
                    (red, (7, 8, 9)))
    for index in range(300):
        w = WIDTHS[index % len(WIDTHS)]
        h = 1 if index % 11 == 0 else int(rng.integers(2, 25))
        before = palette[rng.integers(len(palette), size=(h, w))]
        pixels = np.roll(before, (index % 7 - 3, index % 5 - 2), axis=(0, 1))
        if index % 23 == 0:
            pixels[:] = 0
        if index % 29 == 0:
            pixels[:] = 255
        region = rng.random((h, w)) < (index % 11) / 10
        selection = None if index % 2 else rng.random((h, w)) < 0.83
        share = (0, 0.5, 0.6, 0.75, 1, float("nan"))[index % 6]
        args = (pixels, region, replacements, selection, share)
        actual = bucket_fill.enclosed_islands(*args)
        expected = bucket_fill.enclosed_islands_numpy(*args)
        assert [rgb for rgb, _ in actual] == [rgb for rgb, _ in expected]
        for (_, actual_mask), (_, expected_mask) in zip(actual, expected):
            np.testing.assert_array_equal(actual_mask, expected_mask)
        np.testing.assert_array_equal(
            bucket_fill._enclosed_by(region), bucket_fill._enclosed_by_numpy(region),
        )
        args = (pixels, region, before, index % 6 - 2)
        assert bucket_fill.guess_target_categories(*args) == bucket_fill.guess_target_categories_numpy(*args)
        if index % 5 == 0:
            options = bucket_fill.BucketOptions(adjacent=False, island_replacements=replacements)
            reference = (None, red, other)[index % 3]
            seed = bucket_fill.skewer_seed_numpy(pixels, reference)
            expected = (None if seed is None else
                        bucket_fill.compute_fill_region_numpy(pixels, *seed, options, selection))
            actual = bucket_fill.skewer_fill_once(pixels, reference, options, selection)
            if expected is None or not expected.ok:
                assert actual is None
            else:
                _assert_tracking_equal(actual, expected)


@needs_native
def test_enclosed_islands_exact_threshold():
    pixels = np.array([[
        [255, 255, 255, 255], [120, 30, 80, 1], [0, 0, 0, 0], [255, 255, 255, 255],
    ]], dtype=np.uint8)
    region = np.array([[True, False, False, True]])
    replacements = (((120, 30, 80), (3, 4, 5)),)
    for threshold in (0.5, np.nextafter(0.5, 1)):
        args = (pixels, region, replacements, None, threshold)
        actual = bucket_fill.enclosed_islands(*args)
        expected = bucket_fill.enclosed_islands_numpy(*args)
        assert len(actual) == len(expected) == int(threshold == 0.5)
        if actual:
            np.testing.assert_array_equal(actual[0][1], expected[0][1])


def _island_fill_head_fingerprints():
    import hashlib

    def fingerprint(mask):
        return None if mask is None else hashlib.sha256(mask.tobytes()).hexdigest()

    rng = np.random.default_rng(4596)
    rows = []
    for i in range(144):
        h, w = 24 + i % 14, WIDTHS[2 + i % (len(WIDTHS) - 2)]
        pixels = np.zeros((h, w, 4), np.uint8)
        pixels[..., 3] = 255
        pixels[3:-3, 3:-3, :] = 255
        red, blue = (120, 30, 80), (55, 20, 220)
        pixels[9:11, 3:6] = (*red, 255)
        pixels[12:13, 2:4] = (*red, 255)
        pixels[5:8, 7:8] = (*blue, 1)
        pixels[5:7, -8:-5] = (*blue, 255)
        previous = pixels[..., 0] != 0
        before = pixels.copy()
        if i % 3 == 0:
            pixels[3:-3, w // 2, :3] = 0
        if i % 4 == 0:
            pixels[4:-4, 4:-4][rng.random((h - 8, w - 8)) < 0.08] = (*red, 255)
        options = bucket_fill.BucketOptions(
            adjacent=i % 7 != 1, close_gap=i % 7 == 2,
            island_replacements=((red, (3, 4, 5)), (blue, blue), (blue, (6, 7, 8))),
        )
        selection = None
        if i % 3 == 1:
            selection = np.ones_like(previous)
            selection[8:12, 1:5] = False
        results = [
            bucket_fill.compute_fill_region(pixels, 6, 6, options, selection),
            bucket_fill.track_fill_region(
                pixels, previous, None, options, selection,
                previous_pixels=before if i % 2 else None,
            ),
        ]
        if selection is None:
            session = bucket_fill.BucketSession(pixels, options, indexed=True)
            results.extend(bucket_fill.compute_fill_region(
                pixels, 6, 6, options, session=session,
            ) for _ in range(2))
        for result in results:
            rows.append((
                result.reason, result.target, result.bounds, result.score,
                fingerprint(result.region), fingerprint(result.matched),
                [(rgb, fingerprint(mask)) for rgb, mask in result.islands],
            ))
    return rows


@needs_native
def test_island_fill_matches_9e6fac7():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    baseline = root / "native/target/head_45"
    if not all((baseline / item).exists() for item in ("bucket_fill.py", "native.py", "pma_native")):
        pytest.skip("比較用の 9e6fac7 の Python ソースと拡張が用意された環境で実行する")
    old = subprocess.check_output(
        [sys.executable, "-c", r"""
import importlib.util
import json
import runpy
import sys
from pathlib import Path

baseline = Path(sys.argv[1])
sys.path.insert(0, str(baseline))
for module in ("native", "bucket_fill"):
    name = f"paintmaskanimator.{module}"
    spec = importlib.util.spec_from_file_location(name, baseline / f"{module}.py")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
cases = runpy.run_path(sys.argv[2])
print(json.dumps(cases["_island_fill_head_fingerprints"]()))
""", str(baseline), str(Path(__file__).resolve())],
        cwd=root, text=True,
    )
    assert json.loads(old) == json.loads(json.dumps(_island_fill_head_fingerprints()))


@needs_native
def test_guess_target_categories_cropped_windows_match_numpy():
    rng = np.random.default_rng(4517)
    palette = np.array([
        (250, 250, 250, 0), (255, 255, 255, 255), (48, 48, 48, 255),
        (49, 48, 48, 255), (190, 105, 90, 255), (220, 100, 80, 255),
    ], dtype=np.uint8)
    for index, (y, x) in enumerate(((0, 0), (17, 600), (430, 379), (480, 780))):
        before = np.full((490, 790, 4), 255, dtype=np.uint8)
        before[y:y + 10, x:x + 10] = palette[rng.integers(len(palette), size=(10, 10))]
        pixels = np.roll(before, (index * 9 - 4, index * 29 - 12), axis=(0, 1))
        previous = np.zeros(before.shape[:2], dtype=bool)
        previous[y:y + 9, x:x + 9] = True
        assert bucket_fill.guess_target_categories(
            pixels, previous, before,
        ) == bucket_fill.guess_target_categories_numpy(pixels, previous, before)


def _assert_color_stats_parity(rgba, threshold=128, background=None):
    from paintmaskanimator import color_ops, imaging

    image = imaging.rgba_array_to_qimage(rgba)
    for name in ("image_alpha_statistics", "opaque_rgb_color_count", "detect_opaque_border_background"):
        result = getattr(color_ops, name)(image)
        expected = getattr(color_ops, name + "_numpy")(image)
        assert type(result) is type(expected)
        assert result == expected
        if isinstance(result, dict):
            assert list(result) == list(expected)
            assert all(type(value) is int for value in result.values())
        if isinstance(result, tuple):
            assert all(type(value) is int for value in result)

    result = color_ops.estimate_mixed_boundary_pixels(image, background)
    assert type(result) is int
    assert result == color_ops.estimate_mixed_boundary_pixels_numpy(image, background)
    result = color_ops.binarize_alpha_for_pixel_art(image, threshold)
    expected = color_ops.binarize_alpha_for_pixel_art_numpy(image, threshold)
    assert type(result) is type(expected)
    assert result.format() == expected.format()
    assert np.array_equal(imaging.qimage_rgba_array(result), imaging.qimage_rgba_array(expected))


@needs_native
def test_color_stats_and_overlay_match_numpy():
    from PySide6.QtGui import QColor
    from paintmaskanimator import color_ops

    rng = np.random.default_rng(520)
    for index in range(320):
        height = (1, 2, 3, int(rng.integers(4, 21)))[index % 4]
        width = WIDTHS[(index // 4) % len(WIDTHS)]
        color_count = (1, 2, 13, 257)[(index // 3) % 4]
        palette = rng.integers(0, 256, (color_count, 3), dtype=np.uint8)
        rgba = np.empty((height, width, 4), dtype=np.uint8)
        rgba[..., :3] = palette[rng.integers(len(palette), size=(height, width))]
        alphas = ([0], [255], [250, 254, 255], [0, 1, 249, 250, 254, 255], np.arange(256))
        rgba[..., 3] = rng.choice(alphas[index % len(alphas)], size=(height, width))
        background = tuple(map(int, palette[0])) if index % 2 else None
        threshold = (-20, 1, 128, 254, 500, "128", 128.75)[index % 7]
        _assert_color_stats_parity(rgba, threshold, background)

        exact = [QColor(*map(int, color)) for color in palette[:index % 5]]
        mask = rng.choice([-2, 0, 3], size=(height, width))
        overlay = rgba[:, ::-1].astype(np.int32)
        result = color_ops._snap_overlay_to_exact_rgbs(overlay, mask, exact)
        expected = color_ops._snap_overlay_to_exact_rgbs_numpy(overlay, mask, exact)
        assert type(result) is np.ndarray
        assert result.dtype == expected.dtype
        assert result.flags.writeable
        assert np.array_equal(result, expected)


@needs_native
def test_color_stats_thresholds_and_unusual_arguments():
    from PySide6.QtGui import QImage
    from paintmaskanimator import color_ops, imaging

    def image_of(rows):
        rgb = np.array(rows, dtype=np.uint8)
        rgba = np.full((*rgb.shape[:2], 4), 255, dtype=np.uint8)
        rgba[..., :3] = rgb
        _assert_color_stats_parity(rgba)
        return imaging.rgba_array_to_qimage(rgba)

    for color, expected in (
        ((217, 217, 217), None),
        ((218, 218, 218), (218, 218, 218)),
        ((218, 230, 242), (218, 230, 242)),
        ((218, 230, 243), None),
    ):
        assert color_ops.detect_opaque_border_background(image_of([[color]])) == expected

    low, high = (232, 232, 232), (233, 233, 233)
    for rows in ([[high, low]], [[high], [low]], [[high, low], [low, high]],
                 [[high], [low], [high], [low]]):
        assert color_ops.detect_opaque_border_background(image_of(rows)) == low
    white = (255, 255, 255)
    for count in (25, 26, 27):
        rows = [[white] * count + [(0, 0, 0)] * 13 + [(110, 110, 110)] * (37 - count)]
        expected = white if count >= 26 else None
        assert color_ops.detect_opaque_border_background(image_of(rows)) == expected

    bg = (0, 0, 0)
    for distance, expected in ((4, 0), (5, 0), (6, 1), (189, 1), (190, 0), (191, 0)):
        image = image_of([[(distance, 0, 0), white]])
        assert color_ops.estimate_mixed_boundary_pixels(image, bg) == expected
        assert color_ops.estimate_mixed_boundary_pixels_numpy(image, bg) == expected

    for background in (0, [1], (1, 2), [1, 2, 3, 4], (-32768, 0, 0),
                       (32767, 0, 0), (32768, 0, 0), (1.75, 2, 3),
                       np.array([1, 2, 3], dtype=np.int16), [[1, 2, 3]]):
        try:
            expected = color_ops.estimate_mixed_boundary_pixels_numpy(image, background)
        except (ValueError, OverflowError) as exc:
            with pytest.raises(type(exc)) as actual:
                color_ops.estimate_mixed_boundary_pixels(image, background)
            assert str(actual.value) == str(exc)
        else:
            assert color_ops.estimate_mixed_boundary_pixels(image, background) == expected

    assert color_ops.image_alpha_statistics(None) == color_ops.image_alpha_statistics_numpy(QImage())
    assert color_ops.opaque_rgb_color_count(QImage()) == 0
    assert color_ops.detect_opaque_border_background(None) is None
    assert color_ops.estimate_mixed_boundary_pixels(QImage(), object()) == 0
    assert color_ops.binarize_alpha_for_pixel_art(QImage(), object()).isNull()

    rgba = np.array([[[10, 20, 30, 249]]], dtype=np.uint8)
    for palette, expected in (
        ([(9, 20, 30), (11, 20, 30)], [9, 20, 30, 249]),
        ([(10, 20, 30), (255, 255, 255)], [255, 255, 255, 249]),
    ):
        result = color_ops._snap_overlay_to_exact_rgbs(rgba, [[1]], palette)
        assert result[0, 0].tolist() == expected
        assert np.array_equal(result, color_ops._snap_overlay_to_exact_rgbs_numpy(rgba, [[1]], palette))
    assert np.array_equal(
        color_ops._snap_overlay_to_exact_rgbs(rgba[..., :3], [[1]], [white]),
        color_ops._snap_overlay_to_exact_rgbs_numpy(rgba[..., :3], [[1]], [white]),
    )
    with pytest.raises(IndexError):
        color_ops._snap_overlay_to_exact_rgbs(rgba, [1, 1], [white])


@needs_native
def test_color_stats_rgba8888_with_padded_rows():
    from PySide6.QtGui import QImage
    from paintmaskanimator import color_ops

    raw = bytes([
        10, 20, 30, 0, 10, 20, 30, 1, 10, 20, 30, 249, 9, 9, 9, 9,
        50, 60, 70, 250, 50, 60, 70, 254, 50, 60, 70, 255, 8, 8, 8, 8,
    ])
    image = QImage(raw, 3, 2, 16, QImage.Format.Format_RGBA8888)
    rgba = imaging.qimage_rgba_array(image, readonly=True)
    assert isinstance(rgba.base, bytes)
    assert not rgba.flags.writeable
    assert rgba.tobytes() == raw[:12] + raw[16:28]
    assert color_ops.image_alpha_statistics(image) == {
        "transparent": 1, "semi_transparent": 4, "opaque": 1,
    }
    assert color_ops.detect_opaque_border_background(image) is None
    for threshold in (-1, 1, 128, 249, 250, 254, 255, 1000):
        result = color_ops.binarize_alpha_for_pixel_art(image, threshold)
        expected = color_ops.binarize_alpha_for_pixel_art_numpy(image, threshold)
        assert result.format() == expected.format()
        assert result == expected


@needs_native
def test_tone_curve_samples_and_lut_match_numpy():
    from paintmaskanimator import color_ops

    rng = np.random.default_rng(53)
    curves = [
        None, [], [(0.5, 0)], [("0.4", "0.7"), None], [(np.nan, -np.inf)],
        [(128 / 255, 126.5 / 255)], [(128 / 255, 127.5 / 255)],
    ]
    quiet_nan = np.array([0xFFF8000000000042], dtype=np.uint64).view(np.float64)
    for index in range(480):
        count = 1 + index % 16
        xs = rng.uniform(-0.3, 1.3, count) if index % 2 else rng.random(count)
        if index % 7 == 0:
            xs = np.round(xs, 1)
        ys = rng.random(count)
        if index % 3 == 0:
            xs.sort()
            ys.sort()
        if index % 5 == 0:
            ys.fill(0.3)
        curves.append(list(zip(xs, ys)))

    for points in curves:
        knots = colors.normalize_tone_curve_points(points)
        values = np.r_[
            rng.uniform(-1, 2, 129), np.linspace(0, 1, 256),
            [x for x, _ in knots], -0.0, -np.inf, np.inf, np.nan, -np.nan, quiet_nan,
        ][::-1]
        actual = color_ops.tone_curve_samples(iter(points) if points else points, values)
        expected = color_ops.tone_curve_samples_numpy(points, values)
        assert actual.dtype == expected.dtype == np.float64
        assert actual.shape == expected.shape
        np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))
        lut = color_ops.tone_curve_lut(points)
        assert lut.dtype == np.uint8 and lut.shape == (256,)
        np.testing.assert_array_equal(lut, color_ops.tone_curve_lut_numpy(points))

    for points in [None, [(0.5, 0.25)]]:
        for values in [
            0.3, np.array(0.5), [0, 0.5, 1], np.ones((3, 2)),
            np.arange(3, dtype=np.int16), np.array([], dtype=np.float64),
        ]:
            actual = color_ops.tone_curve_samples(points, values)
            expected = color_ops.tone_curve_samples_numpy(points, values)
            assert type(actual) is type(expected)
            assert actual.shape == expected.shape and actual.dtype == expected.dtype
            np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))


@needs_native
def test_tone_curve_samples_preserves_numpy_float_errors():
    from paintmaskanimator import color_ops

    points = [(0.5, 0.8)]
    signaling_nan = np.array([0x7ff0000000000001], dtype=np.uint64).view(np.float64)
    with pytest.warns(RuntimeWarning, match="invalid value"):
        samples = color_ops.tone_curve_samples(iter(points), signaling_nan)
    assert samples.view(np.uint64).tolist() == [0x7ff8000000000001]
    with np.errstate(over="raise"):
        with pytest.raises(FloatingPointError, match="overflow"):
            color_ops.tone_curve_samples(points, np.array([np.finfo(float).max]))
    with np.errstate(under="raise"):
        with pytest.raises(FloatingPointError, match="underflow"):
            color_ops.tone_curve_samples(points, np.array([1e-200]))


@needs_native
def test_tone_curve_samples_collapsed_to_one_point():
    from paintmaskanimator import color_ops

    # 端点も含め、隣り合う x が全て統合の許容差より小さい。
    points = list(zip(np.linspace(0, 1, 100002), np.zeros(100002)))
    values = np.array([-np.inf, -0.0, 0.5, np.inf, np.nan])
    actual = color_ops.tone_curve_samples(points, values)
    expected = color_ops.tone_curve_samples_numpy(points, values)
    np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))
    np.testing.assert_array_equal(color_ops.tone_curve_lut(points), np.full(256, 255, dtype=np.uint8))


@needs_native
def test_apply_tone_curve_matches_numpy():
    from PySide6.QtGui import QImage
    from paintmaskanimator import color_ops

    rng = np.random.default_rng(54)
    for height, width in [(1, 1), (1, 65), (64, 1), (29, 31)]:
        rgba = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
        for pixel, value in zip(rgba.reshape(-1, 4), [[105, 105, 105, 255], [106, 106, 102, 255]]):
            pixel[:] = value
        image = QImage(rgba.tobytes(), width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
        curves = [None, [(0, 0), (1, 1)], [(0.5, 0.2)], [(0.3, 0.8), (0.7, 0.2)]]
        curves.extend(
            rng.uniform(-0.2, 1.2, (count, 2)).tolist() for count in range(1, 17)
        )
        for points in curves:
            for background in [None, (255, 255, 255), (0, 0, 0), [100, 100, 100], 100, [100]]:
                actual = color_ops.apply_tone_curve(image, points, background)
                expected = color_ops.apply_tone_curve_numpy(image, points, background)
                assert actual.format() == expected.format()
                assert actual == expected
                np.testing.assert_array_equal(
                    imaging.qimage_rgba_array(actual), imaging.qimage_rgba_array(expected)
                )
        for background in [[256, 0, 0], [0, 0], [0, 0, 0, 0]]:
            with pytest.raises((ValueError, OverflowError)) as reference:
                color_ops.apply_tone_curve_numpy(image, [(0.5, 0.2)], background)
            with pytest.raises(reference.type, match="^" + re.escape(str(reference.value)) + "$"):
                color_ops.apply_tone_curve(image, [(0.5, 0.2)], background)

    for image in [None, QImage()]:
        assert color_ops.apply_tone_curve(image) is image
