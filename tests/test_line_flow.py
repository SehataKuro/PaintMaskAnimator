"""2枚の線画のあいだの区画ごとの動き（:mod:`line_flow`）。"""
from __future__ import annotations

import numpy as np
import pytest

from paintmaskanimator import line_flow, native


def scatter(shape, seed=1):
    """かぎ形の短い線をばらまいた線画（真偽マスク）。"""
    rng = np.random.default_rng(seed)
    lines = np.zeros(shape, dtype=bool)
    for _ in range(shape[0] * shape[1] // 300):
        y, x = rng.integers(0, shape[0] - 12), rng.integers(0, shape[1] - 12)
        size = int(rng.integers(4, 11))
        lines[y, x:x + size] = True
        lines[y:y + size, x + size - 1] = True
    return lines


def near(value, expected):
    """太らせた線で重ねるので、1画素の違いは区別しない。"""
    return abs(float(value) - expected) <= 1.0


def moved(lines, dy, dx):
    out = np.zeros_like(lines)
    height, width = lines.shape
    out[max(0, dy):height + min(0, dy), max(0, dx):width + min(0, dx)] = \
        lines[max(0, -dy):height - max(0, dy), max(0, -dx):width - max(0, dx)]
    return out


def test_finds_a_uniform_motion():
    guide = scatter((192, 256))
    target = moved(guide, 7, -12)  # 手本の (y, x) が、こちらでは (y+7, x-12)
    flow = line_flow.estimate_flow(target, guide)
    dy, dx = flow.at(np.array([96]), np.array([128]))
    assert near(dy[0], -7)
    assert near(dx[0], 12)
    assert flow.score_at(np.array([96]), np.array([128]))[0] > 0.8


def test_left_and_right_halves_can_move_differently():
    guide = scatter((192, 320), seed=3)
    target = np.zeros_like(guide)
    target[:, :160] = moved(guide, 0, 6)[:, :160]
    target[:, 160:] = moved(guide, 0, -6)[:, 160:]
    flow = line_flow.estimate_flow(target, guide)
    _dy, left = flow.at(np.array([96]), np.array([48]))
    _dy, right = flow.at(np.array([96]), np.array([272]))
    assert near(left[0], -6)
    assert near(right[0], 6)


def test_blocks_without_lines_follow_their_neighbours():
    guide = scatter((192, 256), seed=5)
    guide[:, 96:160] = False
    target = moved(guide, 4, 4)
    flow = line_flow.estimate_flow(target, guide)
    dy, dx = flow.at(np.array([96]), np.array([128]))
    assert near(dy[0], -4)
    assert near(dx[0], -4)


needs_flow_native = pytest.mark.skipif(
    not native.available() or not hasattr(native._native, "line_flow_scores"),
    reason="Rust line-flow component is not installed",
)


@needs_flow_native
@pytest.mark.parametrize("block, reach", [(1, 0), (2, 2), (4, 12), (8, 2), (16, 2), (32, 2)])
def test_native_candidate_scores_match_numpy_exactly(block, reach):
    rng = np.random.default_rng(27 + block)
    rows, cols = 3, 4
    shape = rows * block, cols * block
    for density in (0.0, 0.05, 0.5, 1.0):
        target = rng.random(shape) < density
        guide = rng.random(shape) < density
        base_y = rng.integers(-10, 11, (rows, cols), dtype=np.int64)
        base_x = rng.integers(-10, 11, (rows, cols), dtype=np.int64)
        expected = line_flow._scores_numpy(target, guide, rows, cols, reach, base_y, base_x)
        actual = native.line_flow_scores(target, guide, block, base_y, base_x, reach)
        np.testing.assert_array_equal(actual, expected)
        # The coarse search has zero base offsets.
        zero = np.zeros((rows, cols), dtype=np.int64)
        expected = line_flow._scores_numpy(target, guide, rows, cols, reach)
        actual = native.line_flow_scores(target, guide, block, zero, zero, reach)
        np.testing.assert_array_equal(actual, expected)


@needs_flow_native
@pytest.mark.parametrize("shape, seed", [((1, 1), 1), ((35, 67), 2), ((192, 256), 3),
                                        ((193, 321), 5)])
def test_native_full_flow_matches_numpy_exactly(shape, seed, monkeypatch):
    rng = np.random.default_rng(seed)
    guide = rng.random(shape) < 0.08
    target = moved(guide, 3, -5) if min(shape) > 5 else guide.copy()
    actual = line_flow.estimate_flow(target, guide)
    monkeypatch.setenv("PMA_NO_NATIVE", "1")
    expected = line_flow.estimate_flow(target, guide)
    for name in ("dy", "dx", "score", "lines"):
        np.testing.assert_array_equal(getattr(actual, name), getattr(expected, name))


def test_old_native_component_uses_the_numpy_fallback(monkeypatch):
    monkeypatch.setattr(native, "_native", object())
    mask = np.zeros((4, 4), dtype=bool)
    assert native.line_flow_scores(mask, mask, 4, np.zeros((1, 1)), np.zeros((1, 1)), 2) is None


@needs_flow_native
def test_native_rejects_invalid_flow_buffers():
    import pma_native
    offsets = np.zeros((1, 1), dtype=np.int64).tobytes()
    with pytest.raises(ValueError):
        pma_native.line_flow_scores(b"", b"", 4, 4, 4, offsets, offsets, 2)
    with pytest.raises(ValueError):
        pma_native.line_flow_scores(bytes(16), bytes(16), 4, 4, 0, offsets, offsets, 2)
    with pytest.raises(ValueError):
        pma_native.line_flow_scores(bytes(16), bytes(16), 4, 4, 4, b"", offsets, 2)
