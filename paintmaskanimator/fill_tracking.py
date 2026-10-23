"""串刺し塗りの追跡で使う、コマ間のずれの推定（UI 非依存）。

前のコマで塗った範囲と今のコマの図形が重なっていることを前提にすると、
図形がその幅ほど動いたコマで追跡が切れる。そこで、塗った範囲のまわりの
線画（塗る対象でない画素）を前のコマと今のコマで比べ、どれだけずれたかを
位相相関で求める。前の範囲をそのぶん動かしてから比べれば、大きく動いても
重なりが保てる。

numpy 配列だけを扱う純粋な関数にしてあるので、あとで Rust の部品に置き換えやすい
（``docs/design-principles.md``「将来は Rust へ置き換えていく」）。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

#: ずれを数える窓の一辺の上限。これより大きい窓は間引いて計算する。
#: ずれは粗くてよい（最後は実際に塗って重なりで選ぶ）ので、速さを優先する。
_MAX_WINDOW = 256


def mask_bounds(mask) -> Optional[Tuple[int, int, int, int]]:
    """真の画素を囲む ``(x0, y0, x1, y1)``（x1, y1 は排他）。無ければ None。"""
    rows = np.flatnonzero(mask.any(axis=1))
    if not rows.size:
        return None
    cols = np.flatnonzero(mask.any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def shift_mask(mask, dy, dx):
    """``mask`` を (dy, dx) だけ動かす。はみ出した分は捨て、空いた所は偽。"""
    height, width = mask.shape
    out = np.zeros_like(mask)
    if abs(dy) >= height or abs(dx) >= width:
        return out
    src_y = slice(max(0, -dy), height - max(0, dy))
    dst_y = slice(max(0, dy), height - max(0, -dy))
    src_x = slice(max(0, -dx), width - max(0, dx))
    dst_x = slice(max(0, dx), width - max(0, -dx))
    out[dst_y, dst_x] = mask[src_y, src_x]
    return out


def _outline(region, lines, width=3):
    """``region`` の縁に接する線画だけを残す（遠くの動かない線を比べないため）。"""
    from .bucket_fill import dilate

    return lines & dilate(region, width) & ~region


def estimate_shifts(previous_lines, current_lines, region, count=3, margin=None):
    """``region`` の縁の線画が、前のコマから今のコマへどれだけ動いたかの候補。

    位相相関の山の高い順に ``count`` 個まで ``(dy, dx)`` を返す。図形の縁の線
    （塗った範囲に接する線）だけを型にするので、近くにある動かない線に
    引きずられにくい。どれが正しいかは、呼び出し側が実際に塗って比べて決める。

    :param previous_lines: 前のコマの線画（塗る対象でない画素）の真偽マスク。
    :param current_lines: 今のコマの線画の真偽マスク。
    :param region: 前のコマで塗った範囲。
    :param margin: 探すずれの最大。省略時は範囲の大きさの3倍。
    """
    bounds = mask_bounds(region)
    if bounds is None:
        return []
    height, width = region.shape
    x0, y0, x1, y1 = bounds
    if margin is None:
        margin = max(64, int(max(x1 - x0, y1 - y0) * 3))
    wx0, wy0 = max(0, x0 - margin), max(0, y0 - margin)
    wx1, wy1 = min(width, x1 + margin), min(height, y1 + margin)
    if wx1 - wx0 < 8 or wy1 - wy0 < 8:
        return []
    template = _outline(region, previous_lines)
    step = max(1, int(np.ceil(max(wx1 - wx0, wy1 - wy0) / _MAX_WINDOW)))
    before = template[wy0:wy1:step, wx0:wx1:step].astype(np.float32)
    after = current_lines[wy0:wy1:step, wx0:wx1:step].astype(np.float32)
    if not before.any() or not after.any():
        return []
    # 線画は「線か、線でないか」の2値なので、輪郭と今のコマの線が一番多く
    # 重なるずれを数える（相互相関）。ゼロで埋めて広げ、回り込みを防ぐ。
    rows, cols = before.shape
    size = (2 * rows, 2 * cols)
    correlation = np.fft.irfft2(
        np.fft.rfft2(after, size) * np.conj(np.fft.rfft2(before, size)), size
    )
    limit_y, limit_x = margin // step, margin // step
    shifts = []
    for _ in range(count):
        peak_y, peak_x = np.unravel_index(int(np.argmax(correlation)), correlation.shape)
        if correlation[peak_y, peak_x] <= 0:
            break
        dy = int(peak_y) - size[0] if peak_y >= size[0] // 2 else int(peak_y)
        dx = int(peak_x) - size[1] if peak_x >= size[1] // 2 else int(peak_x)
        # 同じ山の近くを次の候補にしないよう、まわりを消しておく。
        for y in range(peak_y - 2, peak_y + 3):
            for x in range(peak_x - 2, peak_x + 3):
                correlation[y % size[0], x % size[1]] = -np.inf
        if abs(dy) <= limit_y and abs(dx) <= limit_x:
            shifts.append((dy * step, dx * step))
    return shifts


def estimate_shift(previous_lines, current_lines, region, margin=None):
    """一番それらしいずれ ``(dy, dx)``。推定できなければ ``(0, 0)``。"""
    shifts = estimate_shifts(previous_lines, current_lines, region, 1, margin)
    return shifts[0] if shifts else (0, 0)
