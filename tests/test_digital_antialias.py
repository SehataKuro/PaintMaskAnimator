"""デジタル TP のアンチエイリアス除去（#63）。"""
from __future__ import annotations

import numpy as np

from paintmaskanimator import digital_antialias as aa

SKIN = (255, 228, 205)
HAIR = (120, 80, 60)
RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)
BLACK = (0, 0, 0)


def tp():
    """肌と髪の色面に、1px の赤・黒の線と 2px の青・緑の線が入った 2 値の TP。"""
    pixels = np.zeros((80, 120, 4), dtype=np.uint8)
    pixels[:, :, 3] = 255
    pixels[:, :60, :3] = SKIN
    pixels[:, 60:, :3] = HAIR
    pixels[10:70, 20, :3] = RED
    pixels[10:70, 30:32, :3] = BLUE
    pixels[10:70, 80:82, :3] = GREEN
    pixels[40, 5:115, :3] = BLACK
    return pixels


def blend(a, b, share):
    return tuple(int(round(x * share + y * (1 - share))) for x, y in zip(a, b))


def antialiased():
    """線と色面の境目の片側に、混ざった画素（アンチエイリアス）を入れた TP。

    混ぜるのは塗り（肌・髪）の画素だけで、線の画素はもとの色のまま。
    """
    pixels = tp()
    fill = np.isin(
        aa._pack(pixels[:, :, :3]), [aa._pack(np.array(SKIN)), aa._pack(np.array(HAIR))]
    )

    def put(rows, cols, rgb):
        area = np.zeros(fill.shape, dtype=bool)
        area[rows, cols] = True
        pixels[area & fill, :3] = rgb

    put(slice(10, 70), 21, blend(RED, SKIN, 0.4))
    put(slice(10, 70), 29, blend(BLUE, SKIN, 0.5))
    put(slice(10, 70), 79, blend(GREEN, HAIR, 0.3))
    put(41, slice(5, 60), blend(BLACK, SKIN, 0.5))
    put(41, slice(60, 115), blend(BLACK, HAIR, 0.5))
    put(slice(None), 59, blend(SKIN, HAIR, 0.5))
    pixels[5:8, 5:8, 3] = 90  # 薄い半透明
    pixels[5:8, 100:103, 3] = 200  # 濃い半透明
    return pixels


def colours(pixels):
    visible = pixels[:, :, 3] > 0
    return {tuple(int(v) for v in rgb) for rgb in pixels[visible][:, :3]}


def test_a_binary_tp_has_no_antialias_and_is_left_unchanged():
    pixels = tp()
    assert aa.count_antialias(pixels) == 0
    out, changed = aa.remove_antialias(pixels)
    assert changed == 0
    assert np.array_equal(out, pixels), "1px も変えない"


def test_the_palette_is_the_original_colours_including_thin_lines():
    palette = {tuple(int(v) for v in c) for c in aa._unpack(aa.surface_palette(antialiased()))}
    assert palette == {SKIN, HAIR, RED, BLUE, GREEN, BLACK}


def test_removal_keeps_only_the_original_colours():
    out, changed = aa.remove_antialias(antialiased())
    assert changed > 0
    assert colours(out) == {SKIN, HAIR, RED, BLUE, GREEN, BLACK}, "色が増えも減りもしない"
    assert aa.count_antialias(out) == 0
    assert set(np.unique(out[:, :, 3]).tolist()) <= {0, 255}


def test_thin_lines_stay_continuous_and_do_not_thicken():
    out, _changed = aa.remove_antialias(antialiased())
    for row in range(10, 70):
        assert tuple(out[row, 20, :3]) == (BLACK if row == 40 else RED)
        if row not in (40, 41):
            assert tuple(out[row, 21, :3]) == SKIN, "混ざった画素は塗りへ寄せ、線を太らせない"
        if row != 40:
            assert tuple(out[row, 30, :3]) == BLUE and tuple(out[row, 31, :3]) == BLUE
            assert tuple(out[row, 80, :3]) == GREEN and tuple(out[row, 81, :3]) == GREEN
    assert all(tuple(out[40, x, :3]) == BLACK for x in range(5, 115))
    assert tuple(out[41, 10, :3]) == SKIN and tuple(out[41, 100, :3]) == HAIR


def test_faint_semi_transparency_becomes_transparent_and_dense_becomes_opaque():
    out, _changed = aa.remove_antialias(antialiased())
    assert (out[5:8, 5:8, 3] == 0).all()
    assert (out[5:8, 100:103, 3] == 255).all()
    assert tuple(out[6, 101, :3]) == HAIR
