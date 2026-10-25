"""2枚の線画のあいだの動き（区画ごとのずれ）を、粗い解像度から細かい解像度へ求める。

中割りのセル同士は、顔の向きや手の動きで場所ごとにずれ方が違い、大きく動く所もある。
部分ごとに1つのずれを探すと、大きな部分では場所によって合わず、小さな部分では
まわりの線が少なくて迷う。ここでは絵を ``BLOCK`` 画素の区画に分け、区画ごとのずれを

1. 縮小した線で広い範囲を探し（となりの区画の重なりも足して、ばらつきを抑える）、
2. 解像度を上げながら、そのずれのまわりだけを探し直す。

線が無い区画は、となりの区画の答えに従う。重なりは「動かしたあとの手本の線
（1画素太らせる）に、こちらの線がどれだけ重なったか」で数える。numpy だけを使う。
"""
from dataclasses import dataclass
from typing import Tuple

import numpy as np

from . import bucket_fill

#: 区画の大きさ（画素）。
BLOCK = 32
#: いちばん粗い縮小率と、そこで探すずれの範囲（画素）。
COARSE = 8
REACH = 96
#: 細かくするときの縮小率と、それぞれで探し直す範囲（その縮小率での画素）。
REFINE_STEPS = (4, 2, 1)
REFINE_REACH = 2
#: となりの区画の重なりも足す重み。ずれがばらばらになるのを防ぐ。
NEIGHBOUR_WEIGHT = 0.5
#: 大きなずれほど少し割り引く（同じくらい重なるなら、動きの小さいほうを選ぶ）。
MOTION_PENALTY = 0.002
#: 区画の線がこれより少なければ、その区画は「手がかりなし」。
MIN_LINE_PIXELS = 6


@dataclass
class Flow:
    """区画ごとのずれ ``(dy, dx)``（画素）と、その確かさ（0〜1、重なった線の割合）。"""

    dy: np.ndarray
    dx: np.ndarray
    score: np.ndarray
    #: 区画にある、こちらの線の画素数（手がかりの量）。
    lines: np.ndarray
    shape: Tuple[int, int]

    def at(self, ys, xs):
        """画素 ``(ys, xs)`` のずれ。区画の中心のあいだを直線でつないで滑らかにする。"""
        return _bilinear(self.dy, ys, xs), _bilinear(self.dx, ys, xs)

    def score_at(self, ys, xs):
        by = np.clip(ys // BLOCK, 0, self.score.shape[0] - 1)
        bx = np.clip(xs // BLOCK, 0, self.score.shape[1] - 1)
        return self.score[by, bx]


def _bilinear(grid, ys, xs):
    """区画の中心に置いた値 ``grid`` を、画素 ``(ys, xs)`` で直線補間する。"""
    rows, cols = grid.shape
    fy = np.clip((ys + 0.5) / BLOCK - 0.5, 0, rows - 1)
    fx = np.clip((xs + 0.5) / BLOCK - 0.5, 0, cols - 1)
    y0 = np.floor(fy).astype(np.int64)
    x0 = np.floor(fx).astype(np.int64)
    y1 = np.minimum(y0 + 1, rows - 1)
    x1 = np.minimum(x0 + 1, cols - 1)
    wy = fy - y0
    wx = fx - x0
    top = grid[y0, x0] * (1 - wx) + grid[y0, x1] * wx
    bottom = grid[y1, x0] * (1 - wx) + grid[y1, x1] * wx
    return top * (1 - wy) + bottom * wy


def _pool(mask, scale, rows, cols):
    """``scale`` 画素四方に1画素でも線があれば線とする縮小。区画の数に合わせて余白を足す。"""
    height, width = rows * BLOCK // scale, cols * BLOCK // scale
    padded = np.zeros((rows * BLOCK, cols * BLOCK), dtype=bool)
    padded[:mask.shape[0], :mask.shape[1]] = mask
    if scale == 1:
        return padded
    return padded.reshape(height, scale, width, scale).any(axis=(1, 3))


def _block_sum(values, rows, cols):
    size = values.shape[0] // rows
    return values.reshape(rows, size, cols, size).sum(axis=(1, 3))


def _with_neighbours(scores):
    """区画ごとの重なりに、上下左右ななめの区画の重なりを ``NEIGHBOUR_WEIGHT`` 倍して足す。"""
    padded = np.pad(scores, [(0, 0)] * (scores.ndim - 2) + [(1, 1), (1, 1)])
    total = np.zeros_like(scores, dtype=np.float64)
    rows, cols = scores.shape[-2:]
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            weight = 1.0 if dy == 1 and dx == 1 else NEIGHBOUR_WEIGHT
            total += weight * padded[..., dy:dy + rows, dx:dx + cols]
    return total


def _shifted(mask, dy, dx):
    """``out[y, x] = mask[y + dy, x + dx]``（はみ出しは偽）。"""
    out = np.zeros_like(mask)
    height, width = mask.shape
    ys0, ys1 = max(0, -dy), min(height, height - dy)
    xs0, xs1 = max(0, -dx), min(width, width - dx)
    if ys0 < ys1 and xs0 < xs1:
        out[ys0:ys1, xs0:xs1] = mask[ys0 + dy:ys1 + dy, xs0 + dx:xs1 + dx]
    return out


def estimate_flow(target_lines, guide_lines) -> Flow:
    """``target_lines`` の各区画が、``guide_lines`` のどこにあるか（真偽マスク同士）。"""
    height, width = target_lines.shape
    rows, cols = -(-height // BLOCK), -(-width // BLOCK)

    # 1. 粗い解像度で広く探す。
    scale = COARSE
    target = _pool(target_lines, scale, rows, cols)
    guide = bucket_fill.dilate(_pool(guide_lines, scale, rows, cols), 1)
    counts = _block_sum(target, rows, cols).astype(np.float64)
    reach = REACH // scale
    shifts = [(dy, dx) for dy in range(-reach, reach + 1) for dx in range(-reach, reach + 1)]
    scores = np.empty((len(shifts), rows, cols))
    for index, (dy, dx) in enumerate(shifts):
        scores[index] = _block_sum(target & _shifted(guide, dy, dx), rows, cols)
    penalty = np.array([MOTION_PENALTY * scale * np.hypot(dy, dx) for dy, dx in shifts])
    ratio = _with_neighbours(scores) / np.maximum(_with_neighbours(counts[None]), 1.0)
    best = np.argmax(ratio - penalty[:, None, None], axis=0)
    flow_y = np.array([shifts[i][0] for i in best.ravel()]).reshape(rows, cols) * scale
    flow_x = np.array([shifts[i][1] for i in best.ravel()]).reshape(rows, cols) * scale

    # 2. 解像度を上げて、今のずれのまわりだけを探し直す。
    score = np.zeros((rows, cols))
    for scale in REFINE_STEPS:
        target = _pool(target_lines, scale, rows, cols)
        guide = bucket_fill.dilate(_pool(guide_lines, scale, rows, cols), 1)
        counts = _block_sum(target, rows, cols).astype(np.float64)
        size = BLOCK // scale
        level_h, level_w = target.shape
        ys, xs = np.mgrid[0:level_h, 0:level_w]
        base_y = np.repeat(np.repeat(np.round(flow_y / scale).astype(np.int64), size, 0), size, 1)
        base_x = np.repeat(np.repeat(np.round(flow_x / scale).astype(np.int64), size, 0), size, 1)
        deltas = [(dy, dx) for dy in range(-REFINE_REACH, REFINE_REACH + 1)
                  for dx in range(-REFINE_REACH, REFINE_REACH + 1)]
        scores = np.empty((len(deltas), rows, cols))
        for index, (dy, dx) in enumerate(deltas):
            gy = ys + base_y + dy
            gx = xs + base_x + dx
            inside = (gy >= 0) & (gy < level_h) & (gx >= 0) & (gx < level_w)
            hit = np.zeros_like(target)
            hit[inside] = guide[gy[inside], gx[inside]]
            scores[index] = _block_sum(target & hit, rows, cols)
        own = scores / np.maximum(counts, 1.0)
        ratio = _with_neighbours(scores) / np.maximum(_with_neighbours(counts[None]), 1.0)
        penalty = np.array([MOTION_PENALTY * scale * np.hypot(dy, dx) for dy, dx in deltas])
        best = np.argmax(ratio - penalty[:, None, None], axis=0)
        flow_y = flow_y + np.array([deltas[i][0] for i in best.ravel()]).reshape(rows, cols) * scale
        flow_x = flow_x + np.array([deltas[i][1] for i in best.ravel()]).reshape(rows, cols) * scale
        score = np.take_along_axis(own, best[None], axis=0)[0]
        lines = counts

    # 線の無い区画は、まわりの区画のずれ（線の多さで重み付け）に従う。
    lines = _block_sum(_pool(target_lines, 1, rows, cols), rows, cols)
    weak = lines < MIN_LINE_PIXELS
    if weak.any() and (~weak).any():
        weight = np.where(weak, 0.0, lines.astype(np.float64))
        for _ in range(max(rows, cols)):
            if not weak.any():
                break
            spread = _with_neighbours(weight[None])[0]
            fill_y = _with_neighbours((weight * flow_y)[None])[0]
            fill_x = _with_neighbours((weight * flow_x)[None])[0]
            reached = weak & (spread > 0)
            if not reached.any():
                break
            flow_y = np.where(reached, fill_y / np.maximum(spread, 1e-9), flow_y)
            flow_x = np.where(reached, fill_x / np.maximum(spread, 1e-9), flow_x)
            weight = np.where(reached, 1.0, weight)
            weak = weak & ~reached
    score = np.where(lines < MIN_LINE_PIXELS, 0.0, score)
    return Flow(flow_y.astype(np.float64), flow_x.astype(np.float64), score, lines,
                (height, width))


__all__ = ["BLOCK", "Flow", "estimate_flow"]
