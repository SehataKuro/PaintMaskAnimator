"""コマ間のずれの推定（fill_tracking）の単体テスト。"""
from __future__ import annotations

import numpy as np

from paintmaskanimator import bucket_fill, fill_tracking


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


def lines_to_pixels(lines):
    """線画の真偽マスクを、白地に黒線の RGBA にする。"""
    pixels = np.full(lines.shape + (4,), 255, dtype=np.uint8)
    pixels[lines, :3] = 0
    return pixels


def iou(a, b):
    return np.count_nonzero(a & b) / max(1, np.count_nonzero(a | b))


def bent_bar(height, width, bend):
    """横長の枠。右へ行くほど ``bend`` の割合で下へずれる（回転・曲げの代わり）。"""
    lines = np.zeros((height, width), dtype=bool)
    inside = np.zeros((height, width), dtype=bool)
    x0, x1, top, size = 20, 200, 40, 30
    for x in range(x0, x1):
        dy = int(round((x - x0) * bend))
        lines[top + dy, x] = lines[top + dy + size - 1, x] = True
        if x0 < x < x1 - 1:
            inside[top + dy + 1:top + dy + size - 1, x] = True
    lines[top:top + size, x0] = True
    end_dy = int(round((x1 - 1 - x0) * bend))
    lines[top + end_dy:top + end_dy + size, x1 - 1] = True
    return lines, inside


def test_warp_region_follows_a_bent_shape_better_than_one_shift():
    before_lines, before_inside = bent_bar(160, 240, 0.0)
    after_lines, after_inside = bent_bar(160, 240, 0.2)  # 右端は 36px 下がる
    shift = fill_tracking.estimate_shift(before_lines, after_lines, before_inside)
    shifted = fill_tracking.shift_mask(before_inside, *shift)
    warped = fill_tracking.warp_region(
        before_lines, after_lines, before_inside, shift
    )
    assert warped is not None
    assert iou(warped, after_inside) > iou(shifted, after_inside) + 0.2
    assert iou(warped, after_inside) > 0.6


def test_warp_region_skips_small_regions():
    lines, inside = ring(60, 60, 10, 10, 20)
    assert fill_tracking.warp_region(lines, lines, inside) is None


def test_tracking_fills_both_halves_of_a_region_split_by_a_new_line():
    """前のコマの1つの領域が、シワの線で2つに分かれても両方まとめて塗る。"""
    before_lines, before_inside = ring(80, 80, 10, 10, 40)
    after_lines, _ = ring(80, 80, 10, 10, 40)
    after_lines[10:50, 30] = True  # 真ん中に縦の線が入る
    options = bucket_fill.BucketOptions()
    result = bucket_fill.track_fill_region(
        lines_to_pixels(after_lines), before_inside, None, options,
        previous_pixels=lines_to_pixels(before_lines),
    )
    assert result.ok
    assert result.mask[30, 20] and result.mask[30, 40], "左右どちらも塗る"
    assert not result.mask[30, 30], "線は塗らない"
    assert not result.mask[2, 2], "外の背景は塗らない"
    assert result.score is not None and result.score > 0.9


def test_tracking_ignores_a_neighbouring_region_mostly_outside():
    """前の範囲から大きくはみ出す隣の領域は、分かれた一部とみなさない。"""
    before_lines, before_inside = ring(80, 120, 10, 10, 40)
    after_lines, _ = ring(80, 120, 10, 10, 40)
    after_lines[10:50, 30] = True
    # 右隣に、前の範囲にはほとんど入らない細長い枠をくっつける。
    after_lines[10, 49:76] = after_lines[60, 49:76] = True
    after_lines[10:61, 75] = True
    after_lines[10:61, 49] = True
    result = bucket_fill.track_fill_region(
        lines_to_pixels(after_lines), before_inside, None,
        bucket_fill.BucketOptions(),
        previous_pixels=lines_to_pixels(before_lines),
    )
    assert result.ok
    assert result.mask[30, 20] and result.mask[30, 40]
    assert not result.mask[30, 70], "隣の枠は塗らない"
