"""見本を使った塗りの道具のテスト。

* 囲まれた色を置換色で塗るバケツ（``bucket_fill.enclosed_islands``）
* 串刺し塗りで、Shift+Esc でその向きの残りを飛ばす操作
* 選択範囲の中だけの色置換
* 小さな部分の追跡に使う手がかり（``fill_tracking``）
* 見本との違い・塗り間違いのチェック（``reference_check``）
"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator import bucket_fill, fill_tracking, imaging, reference_check
from paintmaskanimator.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


# ----------------------------------------------------------------------
# 絵を作る道具
# ----------------------------------------------------------------------
def rgba(height, width, color=(255, 255, 255)):
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[..., :3] = color
    pixels[..., 3] = 255
    return pixels


def ring(pixels, x0, y0, size, color=(0, 0, 0)):
    x1, y1 = x0 + size, y0 + size
    pixels[y0, x0:x1, :3] = pixels[y1 - 1, x0:x1, :3] = color
    pixels[y0:y1, x0, :3] = pixels[y0:y1, x1 - 1, :3] = color


def block(pixels, x0, y0, x1, y1, color):
    pixels[y0:y1, x0:x1, :3] = color


def textured(height, width):
    """白地に色の違う四角を散らした絵。まわりの色の並びが手がかりになる。"""
    pixels = rgba(height, width)
    colors = [(200, 80, 80), (80, 200, 80), (80, 80, 200), (200, 200, 80)]
    for index, x in enumerate(range(2, width - 8, 9)):
        for row, y in enumerate(range(2, height - 8, 9)):
            block(pixels, x, y, x + 6, y + 6, colors[(index + row) % len(colors)])
    return pixels


def scattered(height, width, seed=7):
    """白地に色の四角を不規則に置いた絵。繰り返しが無いので、ずれが1つに決まる。"""
    rng = np.random.default_rng(seed)
    pixels = rgba(height, width)
    colors = [(200, 80, 80), (80, 200, 80), (80, 80, 200), (200, 200, 80), (80, 200, 200)]
    for _ in range(height * width // 60):
        x, y = (int(value) for value in rng.integers(0, [width - 4, height - 4]))
        size = int(rng.integers(3, 8))
        block(pixels, x, y, x + size, y + size, colors[int(rng.integers(len(colors)))])
    return pixels


def draw_outline(layer, x0, y0, size, fill=None):
    image = layer.image
    image.fill(QColor("white"))
    black = QColor("black")
    for x in range(x0, x0 + size):
        image.setPixelColor(x, y0, black)
        image.setPixelColor(x, y0 + size - 1, black)
    for y in range(y0, y0 + size):
        image.setPixelColor(x0, y, black)
        image.setPixelColor(x0 + size - 1, y, black)
    if fill is not None:
        for y in range(y0 + 1, y0 + size - 1):
            for x in range(x0 + 1, x0 + size - 1):
                image.setPixelColor(x, y, QColor(fill))
    layer.has_content = True


# ----------------------------------------------------------------------
# 囲まれた色を置換色で塗る
# ----------------------------------------------------------------------
def test_enclosed_islands_finds_marks_surrounded_by_the_region():
    pixels = rgba(20, 20)
    block(pixels, 8, 8, 11, 11, (255, 0, 0))   # 塗る範囲の中の赤い斜線
    block(pixels, 0, 0, 3, 3, (255, 0, 0))     # 範囲の外の赤（髪など）
    region = np.zeros((20, 20), dtype=bool)
    region[4:16, 4:16] = True
    region[8:11, 8:11] = False

    islands = bucket_fill.enclosed_islands(
        pixels, region, (((255, 0, 0), (240, 167, 160)),)
    )

    assert len(islands) == 1
    destination, mask = islands[0]
    assert destination == (240, 167, 160)
    assert mask[8:11, 8:11].all()
    assert not mask[0:3, 0:3].any(), "囲まれていない赤は塗り替えない"


def test_enclosed_islands_ignores_colours_without_a_replacement():
    pixels = rgba(20, 20)
    block(pixels, 8, 8, 11, 11, (255, 0, 0))
    region = np.zeros((20, 20), dtype=bool)
    region[4:16, 4:16] = True
    region[8:11, 8:11] = False
    assert bucket_fill.enclosed_islands(pixels, region, ()) == ()
    assert bucket_fill.enclosed_islands(
        pixels, region, (((0, 0, 255), (1, 2, 3)),)
    ) == ()


def test_bucket_repaints_enclosed_marks_with_their_replacement(window):
    layer = window.canvas.frames[0].layers[0]
    draw_outline(layer, 4, 4, 16)
    for y in range(10, 13):
        for x in range(10, 13):
            layer.image.setPixelColor(x, y, QColor("#ff0000"))
    window.palette.replacements[(255, 0, 0)] = QColor("#f0a7a0")
    window.canvas.set_tool("bucket")
    window.canvas.color_mode = "main"
    window.canvas.main_color = QColor("#ffdfd4")
    window.tools.bucket_island_replace.setChecked(True)

    window.canvas.flood_fill(QPoint(6, 6))

    image = window.canvas.frames[0].layers[0].image
    assert image.pixelColor(6, 6) == QColor("#ffdfd4")
    assert image.pixelColor(11, 11) == QColor("#f0a7a0")
    assert image.pixelColor(4, 4) == QColor("black"), "線は塗らない"


def test_bucket_leaves_marks_alone_when_the_option_is_off(window):
    layer = window.canvas.frames[0].layers[0]
    draw_outline(layer, 4, 4, 16)
    layer.image.setPixelColor(11, 11, QColor("#ff0000"))
    window.palette.replacements[(255, 0, 0)] = QColor("#f0a7a0")
    window.canvas.set_tool("bucket")
    window.canvas.main_color = QColor("#ffdfd4")
    window.tools.bucket_island_replace.setChecked(False)

    window.canvas.flood_fill(QPoint(6, 6))

    assert window.canvas.frames[0].layers[0].image.pixelColor(11, 11) == QColor("#ff0000")


# ----------------------------------------------------------------------
# 見本から塗り写す
# ----------------------------------------------------------------------
LEAF = (197, 174, 103)
SKIN = (255, 241, 232)
BLUSH = (255, 223, 212)
HATCH_MARK = (255, 0, 0)
HATCH_DONE = (240, 167, 160)
RANGE_LINE = (0, 255, 0)


def clear(height=160, width=200):
    return np.zeros((height, width, 4), dtype=np.uint8)


def opaque(pixels, x0, y0, x1, y1, color):
    pixels[y0:y1, x0:x1, :3] = color
    pixels[y0:y1, x0:x1, 3] = 255


def outlined(x, color=None, size=60):
    """透明地に黒枠を1つ。``color`` なら中を塗る。"""
    pixels = clear()
    opaque(pixels, x, 40, x + size, 40 + size, (0, 0, 0))
    if color is None:
        pixels[41:39 + size, x + 1:x + size - 1] = 0
    else:
        opaque(pixels, x + 1, 41, x + size - 1, 39 + size, color)
    return pixels


def stack_cells(window, frames):
    """アクティブレイヤーの各コマへ絵を置く。"""
    canvas = window.canvas
    canvas._ensure_frame_count(len(frames))
    for frame_index, pixels in enumerate(frames):
        layer = canvas.frames[frame_index].layers[canvas.active_layer_index]
        height, width = layer.image.height(), layer.image.width()
        full = np.zeros((height, width, 4), dtype=np.uint8)
        full[:pixels.shape[0], :pixels.shape[1]] = pixels
        layer.image = imaging.rgba_array_to_qimage(full)
        layer.has_content = True


def test_copy_paint_is_not_a_tool_any_more(window):
    assert "copy_fill" not in window.tool_selector.items
    assert not hasattr(window.tools, "bucket_copy_reference")
    assert not hasattr(window, "copy_paint"), "「見本から塗り写す」は「自動で塗る」に統合した"


def test_auto_menu_holds_one_way_to_paint_and_the_reference_check(window):
    def menu_actions(title):
        for action in window.menuBar().actions():
            if action.text() == title:
                menu = action.menu()
                assert menu is not None
                return menu.actions()
        raise AssertionError(title)

    auto = menu_actions("自動塗り")
    assert window.a_auto_paint in auto
    assert window.a_reference_check in auto
    assert window.a_auto_paint.text() == "自動で塗る…", "塗る道具は1つだけになった"
    assert window.a_reference_check not in menu_actions("編集")


# ----------------------------------------------------------------------
# Shift+Esc
# ----------------------------------------------------------------------
def test_shift_esc_skips_the_rest_of_the_direction(window):
    window.canvas._ensure_frame_count(4)
    draw_outline(window.canvas.frames[0].layers[0], 4, 4, 8)
    for frame_index in (1, 2, 3):
        # 面積が3倍を超える枠に変わるので、どのコマも自信が持てず止まる。
        draw_outline(window.canvas.frames[frame_index].layers[0], 8, 4, 22)
    window.canvas.set_tool("bucket")
    window.canvas.color_mode = "main"
    window.canvas.main_color = QColor("#3355ff")
    window.tools.bucket_all_frames.setChecked(True)

    window.canvas.flood_fill(QPoint(6, 6))
    assert window.canvas.skewer_fill_pending()

    assert window.canvas.skip_skewer_fill_direction()

    assert not window.canvas.skewer_fill_pending(), "残りのコマは1回でまとめて飛ばす"
    assert window.canvas.frames[0].layers[0].image.pixelColor(6, 6) == QColor("#3355ff")
    for frame_index in (1, 2, 3):
        image = window.canvas.frames[frame_index].layers[0].image
        assert image.pixelColor(12, 12) == QColor("white")


def test_shift_esc_without_a_waiting_fill_does_nothing(window):
    assert not window.canvas.skip_skewer_fill_direction()


# ----------------------------------------------------------------------
# 選択範囲の中だけの色置換
# ----------------------------------------------------------------------
def test_colour_replacement_stays_inside_the_selection(window):
    layer = window.canvas.frames[0].layers[0]
    layer.image.fill(QColor("white"))
    for y in range(0, 10):
        for x in range(0, 40):
            layer.image.setPixelColor(x, y, QColor("#ff0000"))
    layer.has_content = True
    height, width = layer.image.height(), layer.image.width()
    selection = np.zeros((height, width), dtype=bool)
    selection[0:10, 0:20] = True
    assert window.canvas.set_selection_mask(selection)

    assert window.used_color.apply_palette_replacements({(255, 0, 0): (0, 0, 255)})

    image = window.canvas.frames[0].layers[0].image
    assert image.pixelColor(5, 5) == QColor("#0000ff")
    assert image.pixelColor(30, 5) == QColor("#ff0000"), "選択の外は残す"


def test_colour_replacement_reports_when_the_selection_has_no_match(window):
    layer = window.canvas.frames[0].layers[0]
    layer.image.fill(QColor("white"))
    layer.image.setPixelColor(30, 5, QColor("#ff0000"))
    layer.has_content = True
    selection = np.zeros((layer.image.height(), layer.image.width()), dtype=bool)
    selection[0:10, 0:20] = True
    window.canvas.set_selection_mask(selection)
    depth = len(window.canvas.undo_stack)

    assert not window.used_color.apply_palette_replacements({(255, 0, 0): (0, 0, 255)})
    assert len(window.canvas.undo_stack) == depth


# ----------------------------------------------------------------------
# 小さな部分の追跡の手がかり
# ----------------------------------------------------------------------
def test_colour_shifts_follow_the_surrounding_colours():
    before = scattered(120, 120)
    after = np.roll(before, (3, 5), axis=(0, 1))
    region = np.zeros((120, 120), dtype=bool)
    region[50:56, 50:56] = True

    shifts = fill_tracking.estimate_color_shifts(before, after, region)

    assert shifts and shifts[0] == (3, 5)


def test_context_similarity_prefers_the_same_place():
    before = scattered(120, 120)
    after = np.roll(before, (3, 5), axis=(0, 1))
    region = np.zeros((120, 120), dtype=bool)
    region[47:51, 47:51] = True
    moved = fill_tracking.shift_mask(region, 3, 5)
    elsewhere = fill_tracking.shift_mask(region, 3, 32)

    same = fill_tracking.context_similarity(before, region, after, moved)
    other = fill_tracking.context_similarity(before, region, after, elsewhere)

    assert same > 0.9
    assert same > other


def test_context_on_a_plain_background_is_no_evidence():
    plain = rgba(80, 80)
    region = np.zeros((80, 80), dtype=bool)
    region[30:34, 30:34] = True
    other = fill_tracking.shift_mask(region, 0, 20)
    # 白地はどこを比べても一致するので、色の一致は 0 として形の重なりだけが残る。
    assert fill_tracking.context_similarity(plain, region, plain, other) <= 0.3 + 1e-6


# ----------------------------------------------------------------------
# 見本との違い・塗り間違い
# ----------------------------------------------------------------------
def test_label_regions_numbers_four_connected_parts():
    values = np.array([
        [1, 1, 2],
        [3, 1, 2],
        [1, 3, 3],
    ])
    labels, count = reference_check.label_regions(values)
    assert count == 5
    assert labels[0, 0] == labels[0, 1] == labels[1, 1]
    assert labels[2, 0] != labels[0, 0], "斜めはつながらない"
    assert labels[2, 1] == labels[2, 2]
    assert labels[1, 0] != labels[2, 1]


def test_compare_colors_reports_missing_and_extra_colours():
    reference = rgba(20, 20)
    block(reference, 0, 0, 5, 5, (200, 0, 0))
    block(reference, 10, 10, 15, 15, (0, 200, 0))
    cell = rgba(20, 20)
    block(cell, 0, 0, 5, 5, (200, 0, 0))
    block(cell, 12, 2, 16, 6, (0, 0, 200))

    findings = reference_check.compare_colors(reference, cell)

    kinds = {(finding.kind, finding.rgb) for finding in findings}
    assert kinds == {
        (reference_check.KIND_MISSING, (0, 200, 0)),
        (reference_check.KIND_EXTRA, (0, 0, 200)),
    }
    extra = next(f for f in findings if f.kind == reference_check.KIND_EXTRA)
    assert extra.bounds == (12, 2, 16, 6)
    assert reference_check.color_mask(cell, (0, 0, 200)).sum() == 16


def test_compare_colors_ignores_specks():
    reference = rgba(20, 20)
    cell = rgba(20, 20)
    cell[3, 3, :3] = (0, 0, 200)
    assert reference_check.compare_colors(reference, cell) == []


def frame_with_patch(color, shift=0):
    pixels = textured(120, 120)
    block(pixels, 38 + shift, 38, 70 + shift, 70, (255, 255, 255))
    ring(pixels, 40 + shift, 40, 28)
    block(pixels, 41 + shift, 41, 67 + shift, 67, color)
    return pixels


def test_find_mismatches_flags_a_part_unlike_both_neighbours():
    skin = (255, 223, 212)
    previous = reference_check.CellRegions(frame_with_patch(skin, 0))
    current = reference_check.CellRegions(frame_with_patch((197, 174, 103), 0))
    following = reference_check.CellRegions(frame_with_patch(skin, 0))

    findings = reference_check.find_mismatches(previous, current, following)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == reference_check.KIND_MISMATCH
    assert finding.rgb == (197, 174, 103)
    assert finding.expected == skin
    assert finding.bounds == (41, 41, 67, 67)
    assert finding.mask is not None and finding.mask.all()


def test_find_mismatches_accepts_a_consistent_cell():
    skin = (255, 223, 212)
    previous, current, following = (
        reference_check.CellRegions(frame_with_patch(skin)) for _ in range(3)
    )
    assert reference_check.find_mismatches(previous, current, following) == []


def test_find_mismatches_needs_the_neighbours_to_agree():
    previous = reference_check.CellRegions(frame_with_patch((255, 223, 212)))
    current = reference_check.CellRegions(frame_with_patch((197, 174, 103)))
    following = reference_check.CellRegions(frame_with_patch((100, 100, 255)))
    assert reference_check.find_mismatches(previous, current, following) == []


def test_reference_check_lists_findings_and_jumps_to_them(window):
    canvas = window.canvas
    canvas._ensure_frame_count(3)
    skin, wrong = "#ffdfd4", "#c5ae67"
    for frame_index, fill in enumerate((skin, wrong, skin)):
        layer = canvas.frames[frame_index].layers[0]
        color = QColor(fill)
        pixels = frame_with_patch((color.red(), color.green(), color.blue()))
        height, width = layer.image.height(), layer.image.width()
        layer.image.fill(QColor("white"))
        for y in range(min(height, 120)):
            for x in range(min(width, 120)):
                layer.image.setPixelColor(x, y, QColor(*pixels[y, x, :3]))
        layer.has_content = True
    canvas.active_layer_index = 0
    canvas.current_frame = 0

    window.reference_check.run()

    dialog = window.reference_check.dialog
    assert dialog is not None
    kinds = [entry[2].kind for entry in dialog.entries]
    assert reference_check.KIND_MISMATCH in kinds
    row = kinds.index(reference_check.KIND_MISMATCH)
    assert canvas.current_frame == 0
    assert canvas.selection_mask_bool() is None
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(row))
    assert canvas.current_frame == 0
    dialog.tree.itemClicked.emit(dialog.tree.topLevelItem(row), 0)
    assert canvas.current_frame == 1
    selection = canvas.selection_mask_bool()
    assert selection is not None and selection[50, 50]
    dialog.close()
