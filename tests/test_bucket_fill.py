"""バケツ塗りの領域判定（``bucket_fill``）の単体テスト。

判定を ``PaintCanvas`` から切り出したので、QWidget もイベントループもなしに
「どの画素が塗られるか」だけを直接固定できる。串刺し塗りは同じ判定をコマごとに
繰り返すだけなので、ここが正しければ両方の入口が同時に守られる。
"""
from __future__ import annotations

import numpy as np
import pytest

from paintmaskanimator import bucket_fill


def blank(width=16, height=16):
    """白地（擬似透明）のRGBA配列。"""
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[:, :, :] = 255
    return pixels


def box(pixels, x0, y0, x1, y1, rgb):
    pixels[y0:y1, x0:x1, :3] = rgb
    pixels[y0:y1, x0:x1, 3] = 255


def test_seed_category_treats_white_and_alpha_zero_as_background():
    pixels = blank(4, 4)
    assert bucket_fill.seed_category(pixels, 0, 0) is None
    pixels[1, 1] = (0, 0, 0, 0)
    assert bucket_fill.seed_category(pixels, 1, 1) is None
    box(pixels, 2, 2, 3, 3, (255, 0, 0))
    assert bucket_fill.seed_category(pixels, 2, 2) == (255, 0, 0)


def test_adjacent_fill_stops_at_a_closed_outline():
    pixels = blank()
    # 4..12 の枠を黒線で囲い、その内側だけが塗られることを確かめる。
    box(pixels, 4, 4, 12, 5, (0, 0, 0))
    box(pixels, 4, 11, 12, 12, (0, 0, 0))
    box(pixels, 4, 4, 5, 12, (0, 0, 0))
    box(pixels, 11, 4, 12, 12, (0, 0, 0))

    result = bucket_fill.compute_fill_region(
        pixels, 8, 8, bucket_fill.BucketOptions()
    )

    assert result.ok
    assert result.mask[8, 8]
    assert not result.mask[0, 0], "外側へ漏れてはいけない"
    assert not result.mask[4, 4], "線そのものは塗らない"
    assert result.mask.sum() == 6 * 6


def test_non_adjacent_fill_takes_every_pixel_of_the_same_colour():
    pixels = blank()
    box(pixels, 1, 1, 3, 3, (255, 0, 0))
    box(pixels, 10, 10, 12, 12, (255, 0, 0))

    result = bucket_fill.compute_fill_region(
        pixels, 1, 1, bucket_fill.BucketOptions(adjacent=False)
    )

    assert result.ok
    assert result.mask[11, 11], "離れた同色も対象になる"
    assert result.mask.sum() == 8


def test_out_of_bounds_and_outside_selection_are_reported_separately():
    pixels = blank(8, 8)
    options = bucket_fill.BucketOptions()

    assert (
        bucket_fill.compute_fill_region(pixels, -1, 0, options).reason
        == bucket_fill.REASON_OUT_OF_BOUNDS
    )

    selection = np.zeros((8, 8), dtype=bool)
    selection[4:, 4:] = True
    assert (
        bucket_fill.compute_fill_region(pixels, 0, 0, options, selection).reason
        == bucket_fill.REASON_OUTSIDE_SELECTION
    )


def test_selection_confines_the_region():
    pixels = blank(8, 8)
    selection = np.zeros((8, 8), dtype=bool)
    selection[4:, 4:] = True

    result = bucket_fill.compute_fill_region(
        pixels, 5, 5, bucket_fill.BucketOptions(), selection
    )

    assert result.ok
    assert result.mask.sum() == 16
    assert not result.mask[0, 0]


def test_require_closed_refuses_a_region_open_to_the_canvas_edge():
    pixels = blank(8, 8)
    options = bucket_fill.BucketOptions(require_closed=True)

    result = bucket_fill.compute_fill_region(pixels, 4, 4, options)

    assert not result.ok
    assert result.reason == bucket_fill.REASON_OPEN_REGION


def test_close_gap_prevents_leaking_through_a_broken_outline():
    pixels = blank()
    # 上辺に 1px の隙間を開けた枠。隙間閉じなしでは外へ漏れる。
    box(pixels, 4, 4, 12, 5, (0, 0, 0))
    pixels[4, 8, :3] = 255
    box(pixels, 4, 11, 12, 12, (0, 0, 0))
    box(pixels, 4, 4, 5, 12, (0, 0, 0))
    box(pixels, 11, 4, 12, 12, (0, 0, 0))

    leaked = bucket_fill.compute_fill_region(
        pixels, 8, 8, bucket_fill.BucketOptions(close_gap=False)
    )
    assert leaked.ok and leaked.mask[0, 0], "隙間閉じなしなら漏れる"

    sealed = bucket_fill.compute_fill_region(
        pixels, 8, 8, bucket_fill.BucketOptions(close_gap=True, gap_width=3)
    )
    assert sealed.ok
    assert not sealed.mask[0, 0], "隙間閉じで漏れが止まる"


def test_include_masks_absorbs_an_adjacent_selected_colour():
    pixels = blank()
    box(pixels, 4, 4, 12, 5, (0, 0, 0))
    box(pixels, 4, 11, 12, 12, (0, 0, 0))
    box(pixels, 4, 4, 5, 12, (0, 0, 0))
    box(pixels, 11, 4, 12, 12, (0, 0, 0))
    # 内側の一部が既に赤で塗られている（＝影の色などのつもり）。
    box(pixels, 5, 5, 8, 8, (255, 0, 0))

    plain = bucket_fill.compute_fill_region(
        pixels, 9, 9, bucket_fill.BucketOptions()
    )
    assert plain.ok and not plain.mask[6, 6], "含み塗りOFFなら赤は残る"

    merged = bucket_fill.compute_fill_region(
        pixels,
        9,
        9,
        bucket_fill.BucketOptions(
            include_masks=True, mask_colors=((255, 0, 0),)
        ),
    )
    assert merged.ok and merged.mask[6, 6], "含み塗りONなら赤も飲み込む"


def test_empty_result_when_the_seed_region_is_erased_by_the_selection():
    pixels = blank(8, 8)
    box(pixels, 0, 0, 8, 8, (0, 0, 0))
    box(pixels, 2, 2, 4, 4, (255, 0, 0))
    selection = np.zeros((8, 8), dtype=bool)
    selection[2:4, 2:4] = True

    # 種は選択内だが、隣接判定の対象色が選択内に閉じている場合は塗れる。
    ok = bucket_fill.compute_fill_region(
        pixels, 2, 2, bucket_fill.BucketOptions(), selection
    )
    assert ok.ok and ok.mask.sum() == 4


@pytest.mark.parametrize("iterations", [1, 2, 3])
def test_dilate_and_erode_are_inverse_on_a_thick_block(iterations):
    mask = np.zeros((16, 16), dtype=bool)
    mask[4:12, 4:12] = True
    restored = bucket_fill.erode(
        bucket_fill.dilate(mask, iterations), iterations
    )
    assert np.array_equal(restored, mask)
