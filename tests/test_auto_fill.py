"""前のセルの塗りを写す自動塗り（auto_fill）の単体テスト。"""
from __future__ import annotations

import numpy as np

from paintmaskanimator import auto_fill, bucket_fill

RED = (220, 40, 40)
GREEN = (40, 180, 60)
BLACK = (0, 0, 0)


def blank(height=90, width=140):
    return np.full((height, width, 4), 255, dtype=np.uint8)


def box(pixels, x0, y0, width, height, fill=None):
    """黒い枠を描き、``fill`` があれば内側をその色で塗る。"""
    pixels[y0, x0:x0 + width, :3] = BLACK
    pixels[y0 + height - 1, x0:x0 + width, :3] = BLACK
    pixels[y0:y0 + height, x0, :3] = BLACK
    pixels[y0:y0 + height, x0 + width - 1, :3] = BLACK
    if fill is not None:
        pixels[y0 + 1:y0 + height - 1, x0 + 1:x0 + width - 1, :3] = fill


def full_masks(plan, shape=(90, 140)):
    """計画の塗りを、色ごとのキャンバス全体のマスクにする。"""
    masks = {}
    for rgb, (x0, y0, x1, y1), mask in plan.fills:
        full = masks.setdefault(rgb, np.zeros(shape, dtype=bool))
        full[y0:y1, x0:x1] |= mask
    return masks


def test_copies_each_colour_to_the_moved_shapes():
    reference = blank()
    box(reference, 10, 10, 30, 40, RED)
    box(reference, 39, 10, 30, 40, GREEN)  # 線を共有して隣り合う
    current = blank()
    box(current, 16, 14, 30, 40)
    box(current, 45, 14, 30, 40)

    plan = auto_fill.plan_auto_fill(reference, current, bucket_fill.BucketOptions())

    assert plan.filled == 2 and plan.unmatched == 0
    colours = full_masks(plan)
    assert colours[RED][30, 30] and not colours[RED][30, 60]
    assert colours[GREEN][30, 60] and not colours[GREEN][30, 30]
    assert not colours[RED][14, 16], "線は塗らない"
    assert not any(mask[2, 2] for mask in colours.values()), "外の背景は塗らない"


def test_fills_both_parts_of_a_region_split_by_a_new_line():
    reference = blank()
    box(reference, 10, 10, 40, 40, RED)
    current = blank()
    box(current, 10, 10, 40, 40)
    current[10:50, 30, :3] = BLACK

    plan = auto_fill.plan_auto_fill(reference, current, bucket_fill.BucketOptions())

    assert plan.filled == 1
    mask = full_masks(plan)[RED]
    assert mask[30, 20] and mask[30, 40] and not mask[30, 30]


def test_leaves_a_region_without_a_good_match():
    reference = blank()
    box(reference, 10, 10, 20, 20, RED)
    current = blank()
    box(current, 10, 10, 34, 34)  # 面積が2倍超：同じ図形とは言い切れない

    plan = auto_fill.plan_auto_fill(reference, current, bucket_fill.BucketOptions())

    assert plan.filled == 0
    assert plan.unmatched == 1


def test_ignores_specks_and_line_coloured_fills():
    reference = blank()
    box(reference, 10, 10, 30, 30, RED)
    reference[60:62, 100:102, :3] = GREEN  # 小さなゴミ
    regions = auto_fill.reference_regions(reference, {0})
    assert [rgb for rgb, _bounds, _mask in regions] == [RED]


def test_progress_can_cancel():
    reference = blank()
    box(reference, 10, 10, 30, 30, RED)
    current = blank()
    box(current, 10, 10, 30, 30)
    plan = auto_fill.plan_auto_fill(
        reference, current, bucket_fill.BucketOptions(),
        progress=lambda done, total: False,
    )
    assert plan.cancelled and plan.filled == 0
