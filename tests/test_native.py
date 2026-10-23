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
