"""Rust の部品（pma_native）と numpy 版が同じ結果になることの突き合わせ。"""
from __future__ import annotations

import numpy as np
import pytest
import subprocess
import sys

from paintmaskanimator import bucket_fill, imaging, native

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
