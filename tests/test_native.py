"""Rust の部品（pma_native）と numpy 版が同じ結果になることの突き合わせ。"""
from __future__ import annotations

import numpy as np
import pytest

from paintmaskanimator import bucket_fill, imaging, native

needs_native = pytest.mark.skipif(
    not native.available(), reason="Rust の部品（pma_native）が入っていない"
)
# 64画素の区切り（ビット列の1語）をまたぐ幅を必ず含める。
WIDTHS = [1, 2, 31, 63, 64, 65, 127, 128, 129, 200]


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


def test_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("PMA_NO_NATIVE", "1")
    assert not native.available()
    assert native.flood_fill(np.ones((2, 2), bool), [(0, 0)]) is None
    region = imaging.scanline_connected_region(np.ones((2, 2), bool), (0, 0))
    assert region.all(), "Rust を使わないときは numpy 版で計算する"


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


def test_thinning_falls_back_with_an_old_native_module(monkeypatch):
    monkeypatch.setattr(native, "_native", object())
    mask = np.eye(5, dtype=bool)
    np.testing.assert_array_equal(bucket_fill._thin(mask, 12), mask)
