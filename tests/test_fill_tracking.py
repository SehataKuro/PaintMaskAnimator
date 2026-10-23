"""コマ間のずれの推定（fill_tracking）の単体テスト。"""
from __future__ import annotations

import numpy as np

from paintmaskanimator import fill_tracking


def ring(height, width, x0, y0, size):
    lines = np.zeros((height, width), dtype=bool)
    lines[y0, x0:x0 + size] = lines[y0 + size - 1, x0:x0 + size] = True
    lines[y0:y0 + size, x0] = lines[y0:y0 + size, x0 + size - 1] = True
    inside = np.zeros((height, width), dtype=bool)
    inside[y0 + 1:y0 + size - 1, x0 + 1:x0 + size - 1] = True
    return lines, inside


def test_shift_mask_moves_and_clips():
    mask = np.zeros((4, 5), dtype=bool)
    mask[1, 1] = mask[3, 4] = True
    moved = fill_tracking.shift_mask(mask, 1, 2)
    assert moved[2, 3] and moved.sum() == 1, "はみ出した画素は捨てる"
    assert not fill_tracking.shift_mask(mask, 10, 0).any()


def test_estimates_a_large_shift_from_the_outline():
    before_lines, inside = ring(200, 400, 20, 80, 30)
    after_lines, _ = ring(200, 400, 95, 60, 30)  # 自分の幅の2.5倍動く
    # 動かない線がたくさんあっても、塗った範囲の縁だけを型にするので引きずられない。
    for x in range(10, 400, 23):
        before_lines[10:50, x] = after_lines[10:50, x] = True
    shifts = fill_tracking.estimate_shifts(before_lines, after_lines, inside)
    assert shifts[0] == (-20, 75)


def test_nothing_to_compare_gives_no_shift():
    empty = np.zeros((50, 50), dtype=bool)
    assert fill_tracking.estimate_shifts(empty, empty, empty) == []
    assert fill_tracking.estimate_shift(empty, empty, empty) == (0, 0)
