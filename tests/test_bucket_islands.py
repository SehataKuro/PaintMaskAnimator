"""小さな塗り残し拾いを通常・索引・自動バケツで共有する。"""
import numpy as np
import pytest

from paintmaskanimator import auto_bucket, bucket_fill


def test_panel_enables_expansion_by_default_and_can_disable_it(qtbot):
    from paintmaskanimator.toolpanel import ToolPanel
    tools = ToolPanel()
    qtbot.addWidget(tools)
    assert tools.bucket_island_fill.isChecked()
    assert bucket_fill.BucketOptions.from_tools(tools).island_max_width == 1
    assert tools.bucket_island_fill_width_label.text() == "幅 1 px 以下"
    tools.bucket_island_fill_width.setValue(2)
    assert bucket_fill.BucketOptions.from_tools(tools).island_max_width == 2
    assert tools.bucket_island_fill_width_label.text() == "幅 2 px 以下"
    tools.bucket_island_fill.setChecked(False)
    assert bucket_fill.BucketOptions.from_tools(tools).island_max_width == 0
    assert not tools.bucket_island_fill_width.isEnabled()


def island_image(gap=1):
    pixels = np.zeros((24, 30, 4), dtype=np.uint8)
    pixels[:, :, 3] = 255
    pixels[5:15, 5:15, :3] = 255
    pixels[8:10, 15+gap:17+gap, :3] = 255
    pixels[18:20, 5:7, :3] = 255  # too far
    pixels[1:23, 22:29, :3] = 255  # large separate face
    return pixels


@pytest.mark.parametrize("gap", [1, 2])
@pytest.mark.parametrize("enabled", [False, True])
def test_separate_islands_are_protected_in_indexed_and_auto_bucket(gap, enabled):
    pixels = island_image(gap)
    options = bucket_fill.BucketOptions(island_max_width=gap if enabled else 0)
    result = bucket_fill.compute_fill_region(pixels, 10, 10, options)
    expected = np.zeros(pixels.shape[:2], dtype=bool)
    expected[5:15, 5:15] = True
    np.testing.assert_array_equal(result.mask, expected)
    indexed = bucket_fill.BucketSession(pixels, options, indexed=True)
    np.testing.assert_array_equal(indexed.compact(10, 10).materialize(expected.shape).mask, expected)
    filled, paints = auto_bucket.execute_click(
        pixels, auto_bucket.BucketClick(10, 10, (255, 0, 0)), options,
    )
    np.testing.assert_array_equal(filled.mask, expected)
    assert paints[0][1] == result.bounds


def test_island_max_width_selection_and_no_chain():
    pixels = island_image(2)
    options = bucket_fill.BucketOptions(island_max_width=1)
    assert not bucket_fill.compute_fill_region(pixels, 10, 10, options).mask[8, 17]
    options = bucket_fill.BucketOptions(island_max_width=2)
    selection = np.ones(pixels.shape[:2], dtype=bool)
    selection[:, 17:] = False
    result = bucket_fill.compute_fill_region(pixels, 10, 10, options, selection)
    assert result.mask.sum() == 100
    pixels[8:10, 21:23, :3] = 255
    result = bucket_fill.compute_fill_region(pixels, 10, 10, options)
    assert not result.mask[8, 21]


def test_image_border_background_is_not_picked_up():
    pixels = island_image()
    pixels[:4, :4, :3] = 255
    result = bucket_fill.compute_fill_region(
        pixels, 10, 10, bucket_fill.BucketOptions(island_max_width=2),
    )
    assert not result.mask[2, 2]


def test_auto_paint_collects_same_island_after_color_decision(monkeypatch):
    from paintmaskanimator import auto_paint
    pixels = island_image(2)
    target = auto_paint._Cell(pixels, target=True)
    options = bucket_fill.BucketOptions(island_max_width=2)
    work = auto_bucket.BucketWork(target, options)
    main = bucket_fill.BucketSession(pixels, work.options, indexed=True).compact(10, 10)
    monkeypatch.setattr(work, "collect", lambda: [(10, 10, 0, main)])
    result = auto_bucket.paint_cell(
        pixels, [], options, target=target, margin=0, work=work,
        tracked_color=lambda filled: (100, 150, 200),
    )
    painted = np.zeros(pixels.shape[:2], dtype=bool)
    for _rgb, (x0, y0, x1, y1), mask in result.paints:
        painted[y0:y1, x0:x1] |= mask
    expected = bucket_fill.compute_fill_region(pixels, 10, 10, options)
    np.testing.assert_array_equal(painted, expected.mask)
