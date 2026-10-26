"""バケツの「線の延長」の単体テスト。

スタンダードフレームの外では線をキャンバス端まで引かないことが多い。
端の手前で途切れた線を端まで延ばしたものとして塗れることを固定する。
"""
from __future__ import annotations

import numpy as np

from paintmaskanimator import bucket_fill


def blank(width, height):
    """白地（擬似透明）のRGBA配列。"""
    return np.full((height, width, 4), 255, dtype=np.uint8)


def line(pixels, x0, y0, x1, y1, width=2):
    """(x0, y0) から (x1, y1) へ太さ ``width`` の黒線を引く。"""
    steps = max(abs(x1 - x0), abs(y1 - y0)) * 2
    for t in np.linspace(0.0, 1.0, steps + 1):
        x = int(round(x0 + (x1 - x0) * t))
        y = int(round(y0 + (y1 - y0) * t))
        pixels[y:y + width, x:x + width, :3] = 0


def skirt(width=200, height=160, gap=20):
    """裾が下端の ``gap`` px 手前で途切れた、スカートのような台形。"""
    pixels = blank(width, height)
    bottom = height - 1 - gap
    line(pixels, 80, 40, 120, 40)
    line(pixels, 80, 40, 50, bottom)
    line(pixels, 120, 40, 150, bottom)
    return pixels


def test_without_extension_the_fill_leaks_through_the_open_hem():
    pixels = skirt()
    result = bucket_fill.compute_fill_region(
        pixels, 100, 80, bucket_fill.BucketOptions()
    )
    assert result.ok
    assert result.mask[5, 5], "延長なしでは裾から外へ漏れる"


def test_extension_closes_the_hem_at_the_canvas_edge():
    pixels = skirt()
    result = bucket_fill.compute_fill_region(
        pixels, 100, 80,
        bucket_fill.BucketOptions(extend_lines=True, extend_length=50),
    )
    assert result.ok
    assert not result.mask[5, 5], "延長した線で外側への漏れが止まる"
    assert not result.mask[155, 10]
    assert not result.mask[155, 190]
    assert result.mask[159, 100], "裾の下（線のない部分）も端まで塗る"


def test_extension_pixels_are_filled_so_no_hairline_is_left():
    pixels = skirt()
    options = bucket_fill.BucketOptions(extend_lines=True, extend_length=50)
    boundary = ~bucket_fill.fill_target_mask(
        pixels, bucket_fill.TARGET_ALL, None
    )
    extension = bucket_fill.extend_line_ends(boundary, 50)
    assert extension.any()
    assert not (extension & boundary).any(), "既存の線の上には引かない"

    result = bucket_fill.compute_fill_region(pixels, 100, 80, options)
    assert (result.mask & extension).any(), "延長線の上も塗り、細い塗り残しを作らない"


def test_line_ends_farther_than_the_length_are_not_extended():
    pixels = skirt(gap=60)
    result = bucket_fill.compute_fill_region(
        pixels, 100, 80,
        bucket_fill.BucketOptions(extend_lines=True, extend_length=30),
    )
    assert result.ok
    assert result.mask[5, 5], "長さより遠い端点は延長しない"


def test_interior_line_ends_do_not_split_the_background():
    pixels = blank(200, 200)
    # 絵の内側の短いハネ。延長されると背景が分断されてしまう。
    line(pixels, 90, 100, 110, 100)
    result = bucket_fill.compute_fill_region(
        pixels, 5, 5,
        bucket_fill.BucketOptions(extend_lines=True, extend_length=50),
    )
    assert result.ok
    assert result.mask[100, 195] and result.mask[100, 5]
    assert result.mask[195, 100]


def test_tool_panel_options_reach_the_fill(qapp):
    from paintmaskanimator.toolpanel import ToolPanel

    panel = ToolPanel()
    try:
        # 線の延長は既定で入っている。
        assert bucket_fill.BucketOptions.from_tools(panel).extend_lines
        assert panel.bucket_extend_length.isEnabled()
        panel.bucket_extend_lines.setChecked(False)
        assert not bucket_fill.BucketOptions.from_tools(panel).extend_lines
        assert not panel.bucket_extend_length.isEnabled()
        panel.bucket_extend_lines.setChecked(True)
        panel.bucket_extend_length.setValue(300)
        assert panel.bucket_extend_length.isEnabled()
        options = bucket_fill.BucketOptions.from_tools(panel)
        assert options.extend_lines and options.extend_length == 300
        # 「隣接」OFF では領域をたどらないので、延長も使えない。
        panel.bucket_adjacent.setChecked(False)
        assert not panel.bucket_extend_lines.isEnabled()
    finally:
        panel.deleteLater()
        qapp.processEvents()
