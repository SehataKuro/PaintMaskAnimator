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
