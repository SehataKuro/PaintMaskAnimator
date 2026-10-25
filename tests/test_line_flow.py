"""2枚の線画のあいだの区画ごとの動き（:mod:`line_flow`）。"""
from __future__ import annotations

import numpy as np

from paintmaskanimator import line_flow


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
