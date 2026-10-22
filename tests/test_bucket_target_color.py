"""バケツの「対象色」：どの画素をたどって塗るか。"""
from __future__ import annotations

import numpy as np

from paintmaskanimator import bucket_fill as bf


def clear(width=20, height=20):
    """透明（alpha=0）のRGBA配列。"""
    return np.zeros((height, width, 4), dtype=np.uint8)


def box(pixels, x0, y0, x1, y1, rgb, alpha=255):
    pixels[y0:y1, x0:x1, :3] = rgb
    pixels[y0:y1, x0:x1, 3] = alpha


def outline(pixels, x0, y0, x1, y1, rgb):
    box(pixels, x0, y0, x1, y0 + 1, rgb)
    box(pixels, x0, y1 - 1, x1, y1, rgb)
    box(pixels, x0, y0, x0 + 1, y1, rgb)
    box(pixels, x1 - 1, y0, x1, y1, rgb)


def fill(pixels, x, y, target):
    return bf.compute_fill_region(pixels, x, y, bf.BucketOptions(target_color=target))


def colored_shape():
    """黒線の枠の中が、赤と青の2色で塗られている絵。外は透明。"""
    pixels = clear()
    box(pixels, 3, 3, 10, 17, (220, 40, 40))
    box(pixels, 10, 3, 17, 17, (40, 40, 220))
    outline(pixels, 2, 2, 18, 18, (0, 0, 0))
    return pixels


def test_all_colors_keeps_the_old_behaviour():
    result = fill(colored_shape(), 5, 5, bf.TARGET_ALL)
    assert result.ok
    assert result.mask[5, 5] and not result.mask[5, 12], "クリックした色の領域だけ"


def test_surrounded_by_black_fills_every_colour_inside_the_line():
    result = fill(colored_shape(), 5, 5, bf.TARGET_SURROUNDED_BY_BLACK)
    assert result.ok
    assert result.mask[5, 5] and result.mask[5, 12], "線の内側は色が違っても塗る"
    assert not result.mask[2, 2], "黒い線は塗らない"
    assert not result.mask[0, 0], "線の外には出ない"


def test_black_only_fills_the_line():
    result = fill(colored_shape(), 2, 10, bf.TARGET_BLACK)
    assert result.ok
    assert result.mask[2, 2] and result.mask[17, 17]
    assert not result.mask[5, 5]


def test_seed_on_a_non_target_pixel_is_refused():
    result = fill(colored_shape(), 5, 5, bf.TARGET_BLACK)
    assert not result.ok and result.reason == bf.REASON_NOT_TARGET


def test_transparent_only_and_surrounded_by_transparent():
    pixels = colored_shape()
    outside = fill(pixels, 0, 0, bf.TARGET_TRANSPARENT)
    assert outside.ok and outside.mask[0, 0] and not outside.mask[5, 5]

    blob = fill(pixels, 5, 5, bf.TARGET_SURROUNDED_BY_TRANSPARENT)
    assert blob.ok
    assert blob.mask[5, 5] and blob.mask[5, 12] and blob.mask[2, 2], "透明以外は全部"
    assert not blob.mask[0, 0]


def test_white_and_transparent():
    pixels = clear()
    box(pixels, 0, 0, 10, 20, (255, 255, 255))  # 左半分は白、右半分は透明
    box(pixels, 9, 0, 11, 20, (0, 0, 0))       # 真ん中に黒い線
    region = fill(pixels, 2, 2, bf.TARGET_WHITE_TRANSPARENT)
    assert region.ok and region.mask[2, 2] and not region.mask[2, 15], "線で止まる"
    surrounded = fill(pixels, 9, 5, bf.TARGET_SURROUNDED_BY_WHITE_TRANSPARENT)
    assert surrounded.ok and surrounded.mask[5, 9] and not surrounded.mask[5, 2]


def test_faint_translucent_counts_as_transparent():
    pixels = clear()
    box(pixels, 8, 0, 10, 20, (0, 0, 0), alpha=30)    # ごく薄い線は越える
    box(pixels, 14, 0, 16, 20, (0, 0, 0), alpha=200)  # 濃い線で止まる
    strict = fill(pixels, 2, 2, bf.TARGET_TRANSPARENT)
    assert strict.ok and not strict.mask[2, 12], "透明のみなら薄い線でも止まる"
    faint = fill(pixels, 2, 2, bf.TARGET_FAINT_AS_TRANSPARENT)
    assert faint.ok and faint.mask[2, 12] and not faint.mask[2, 18]


def test_opaque_and_inner_transparent_fills_holes():
    pixels = clear()
    box(pixels, 4, 4, 16, 16, (220, 40, 40))
    box(pixels, 8, 8, 12, 12, (0, 0, 0), alpha=0)  # 内側の透明の穴
    plain = fill(pixels, 5, 5, bf.TARGET_SURROUNDED_BY_TRANSPARENT)
    assert plain.ok and not plain.mask[10, 10]
    holes = fill(pixels, 5, 5, bf.TARGET_OPAQUE_AND_INNER_TRANSPARENT)
    assert holes.ok and holes.mask[10, 10], "閉じ込められた透明も塗る"
    assert not holes.mask[0, 0], "外の透明は塗らない"


def test_transparent_and_closed_opaque_fills_islands():
    pixels = clear()
    outline(pixels, 2, 2, 18, 18, (0, 0, 0))
    box(pixels, 8, 8, 11, 11, (40, 200, 40))  # 枠の中に浮いた島
    plain = fill(pixels, 5, 5, bf.TARGET_TRANSPARENT)
    assert plain.ok and not plain.mask[9, 9]
    islands = fill(pixels, 5, 5, bf.TARGET_TRANSPARENT_AND_CLOSED_OPAQUE)
    assert islands.ok and islands.mask[9, 9], "内側に閉じ込められた島も塗る"
    assert not islands.mask[2, 2], "外とつながる枠線は塗らない"


def test_non_adjacent_mode_fills_every_target_pixel():
    pixels = clear()
    box(pixels, 1, 1, 3, 3, (0, 0, 0))
    box(pixels, 15, 15, 17, 17, (0, 0, 0))
    result = bf.compute_fill_region(
        pixels, 1, 1, bf.BucketOptions(adjacent=False, target_color=bf.TARGET_BLACK)
    )
    assert result.ok and result.mask[16, 16]


def test_every_target_has_a_label_in_the_tool_panel(qapp=None):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from paintmaskanimator.toolpanel import ToolPanel

    app = QApplication.instance() or QApplication([])
    panel = ToolPanel()
    try:
        combo = panel.bucket_target_color
        values = [combo.itemData(index) for index in range(combo.count())]
        assert values == list(bf.TARGET_COLORS)
        combo.setCurrentIndex(values.index(bf.TARGET_SURROUNDED_BY_BLACK))
        options = bf.BucketOptions.from_tools(panel)
        assert options.target_color == bf.TARGET_SURROUNDED_BY_BLACK
    finally:
        panel.deleteLater()
        app.processEvents()
