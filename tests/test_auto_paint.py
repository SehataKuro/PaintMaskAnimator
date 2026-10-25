"""色分けの線で自動塗り（:mod:`auto_paint` と、メニューから呼ぶ ``window.auto_paint``）。"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator import auto_paint, imaging, project_io, reference_check
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.undo_entries import ScopedBatchUndo

SKIN = (255, 241, 232)
LEAF = (197, 174, 103)
DRESS = (200, 180, 220)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
BLUSH = (255, 223, 212)
HATCH = (240, 167, 160)


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


def figure(dx, painted=False):
    """黒い枠を赤の線で左右に、右半分を青の線で上下に分けた絵。``dx`` だけ右へ動く。

    ``painted`` なら見本の塗り：左は肌、右上は葉、右下は服。赤の線は肌で塗りつぶし、
    青の線は影の境目として残す。
    """
    pixels = np.zeros((70, 100, 4), dtype=np.uint8)
    x0, y0, x1, y1 = 10 + dx, 10, 60 + dx, 56
    if painted:
        pixels[y0:y1, x0:x0 + 20] = (*SKIN, 255)
        pixels[y0:33, x0 + 20:x1] = (*LEAF, 255)
        pixels[33:y1, x0 + 20:x1] = (*DRESS, 255)
    pixels[y0, x0:x1] = pixels[y1 - 1, x0:x1] = (0, 0, 0, 255)
    pixels[y0:y1, x0] = pixels[y0:y1, x1 - 1] = (0, 0, 0, 255)
    pixels[y0 + 1:y1 - 1, x0 + 20] = (*SKIN, 255) if painted else (*RED, 255)
    pixels[33, x0 + 21:x1 - 1] = (*BLUE, 255)
    # 動きを決める手がかりになる、枠の中の小さな黒い印。
    pixels[18:21, x0 + 6:x0 + 9] = (0, 0, 0, 255)
    pixels[44:47, x0 + 34:x0 + 38] = (0, 0, 0, 255)
    return pixels


def color_at(pixels, x, y):
    return tuple(int(value) for value in pixels[y, x, :3]) if pixels[y, x, 3] else None


def test_primary_lines_are_boundaries():
    pixels = figure(0)
    mask = auto_paint.primary_line_mask(pixels)
    assert mask[20, 30] and mask[33, 40]
    assert not mask[10, 20], "黒は原色の線ではない"
    assert auto_paint.boundary_mask(pixels)[10, 20]


def test_paints_each_area_from_the_neighbour_cell():
    cells = [figure(0, painted=True), figure(2), figure(4)]
    plan = auto_paint.plan_auto_paint(cells, 0)
    assert plan is not None
    for index, dx in ((1, 2), (2, 4)):
        after = auto_paint.apply_paint(cells[index], plan.cells[index])
        assert color_at(after, 15 + dx, 40) == SKIN
        assert color_at(after, 45 + dx, 20) == LEAF
        assert color_at(after, 45 + dx, 50) == DRESS
        assert color_at(after, 30 + dx, 40) == SKIN, "見本で塗りつぶした赤の線は塗る"
        assert color_at(after, 45 + dx, 33) == BLUE, "見本で残した青の線は残す"
        assert color_at(after, 5, 5) is None, "枠の外は塗らない"


def test_goes_backwards_from_a_reference_in_the_middle():
    cells = [figure(0), figure(2, painted=True), figure(4)]
    plan = auto_paint.plan_auto_paint(cells, 1)
    assert plan is not None
    assert set(plan.cells) == {0, 2}
    after = auto_paint.apply_paint(cells[0], plan.cells[0])
    assert color_at(after, 45, 20) == LEAF


def test_progress_can_cancel():
    cells = [figure(0, painted=True), figure(2), figure(4)]
    assert auto_paint.plan_auto_paint(cells, 0, progress=lambda done, total: False) is None


def cheek(dx, painted=False):
    """肌を塗った顔の中に、緑の範囲線（上が2画素切れている）と赤の斜線がある絵。

    ``painted`` なら見本：範囲線とその内側は頬の色、斜線は仕上げ色。
    そうでなければ、範囲線の内側も肌で塗ってある（塗り分ける前）。
    """
    pixels = np.zeros((70, 110, 4), dtype=np.uint8)
    x0, y0, x1, y1 = 10 + dx, 8, 90 + dx, 62
    pixels[y0:y1, x0:x1] = (*SKIN, 255)
    pixels[y0, x0:x1] = pixels[y1 - 1, x0:x1] = (0, 0, 0, 255)
    pixels[y0:y1, x0] = pixels[y0:y1, x1 - 1] = (0, 0, 0, 255)
    # 動きを決める手がかりになる、黒い印（目や口）。
    pixels[16:19, x0 + 8:x0 + 20] = (0, 0, 0, 255)
    pixels[50:52, x0 + 40:x0 + 60] = (0, 0, 0, 255)
    pixels[20:24, x0 + 66:x0 + 70] = (0, 0, 0, 255)
    rx0, ry0, rx1, ry1 = x0 + 25, 22, x0 + 45, 42
    if painted:
        pixels[ry0:ry1, rx0:rx1] = (*BLUSH, 255)
        pixels[ry0 + 6:ry0 + 13, rx0 + 9] = (*HATCH, 255)
    else:
        ring = (*GREEN, 255)
        pixels[ry0, rx0:rx1] = pixels[ry1 - 1, rx0:rx1] = ring
        pixels[ry0:ry1, rx0] = pixels[ry0:ry1, rx1 - 1] = ring
        pixels[ry0, rx0 + 9:rx0 + 11] = (*SKIN, 255)  # 範囲線の切れ目
        pixels[ry0 + 6:ry0 + 13, rx0 + 9] = (*RED, 255)
    return pixels


def test_gap_in_a_colour_line_still_separates_areas():
    pixels = cheek(0)
    boundary = auto_paint.boundary_mask(pixels)
    gaps = auto_paint.gap_mask(boundary, auto_paint.primary_line_mask(pixels))
    assert gaps[22, 44] and gaps[22, 45], "2画素の切れ目をふさぐ"
    assert not gaps[30, 50], "線から離れた所はふさがない"


def test_cheek_inside_a_broken_range_line_is_painted():
    cells = [cheek(0, painted=True), cheek(2), cheek(4), cheek(6)]
    plan = auto_paint.plan_auto_paint(cells, 0)
    assert plan is not None
    for index, dx in ((1, 2), (3, 6)):
        after = auto_paint.apply_paint(cells[index], plan.cells[index])
        assert color_at(after, 40 + dx, 32) == BLUSH, "範囲線の内側は頬の色"
        assert color_at(after, 35 + dx, 32) == BLUSH, "範囲線も頬の色"
        assert color_at(after, 44 + dx, 31) == HATCH, "斜線は仕上げ色"
        assert color_at(after, 25 + dx, 32) == SKIN, "範囲線の外は肌のまま"
    assert plan.finishes.get((RED, BLUSH)) == HATCH, "頬の中の赤い斜線の仕上げ色を覚える"


def test_confident_cells_become_references_and_are_painted_again():
    cells = [figure(0, painted=True), figure(2), figure(4), figure(6)]
    plan = auto_paint.plan_auto_paint(cells, 0)
    assert plan is not None
    assert plan.references == [0, 1, 2, 3], "迷った所の無いセルは見本に加える"
    after = auto_paint.apply_paint(cells[3], plan.cells[3])
    assert color_at(after, 51, 20) == LEAF


def with_box(pixels, x0, y0, size, fill=None):
    """黒い枠の小さな四角を描く。``fill`` なら中をその色で塗る。"""
    x1, y1 = x0 + size, y0 + size
    if fill is not None:
        pixels[y0:y1, x0:x1] = (*fill, 255)
    pixels[y0, x0:x1] = pixels[y1 - 1, x0:x1] = (0, 0, 0, 255)
    pixels[y0:y1, x0] = pixels[y0:y1, x1 - 1] = (0, 0, 0, 255)
    return pixels


def test_notes_unpainted_and_suspect_spots():
    reference = figure(0, painted=True)
    # 見本で、右下は線の無いまま上半分を葉、下半分を服に塗り分けてある。
    reference[33:44, 31:59] = (*LEAF, 255)
    # 肌の中に、葉の色で塗った四角。
    target = with_box(figure(2), 15, 36, 14, fill=LEAF)
    plan = auto_paint.plan_auto_paint([reference, target], 0)
    assert plan is not None
    kinds = {finding.kind: finding for finding in plan.cells[1].notes}
    unpainted = kinds[reference_check.KIND_UNPAINTED]
    assert unpainted.bounds is not None and unpainted.bounds[1] >= 33, "右下は決めきれない"
    suspect = kinds[reference_check.KIND_SUSPECT]
    assert suspect.rgb == LEAF and suspect.expected == SKIN
    after = auto_paint.apply_paint(target, plan.cells[1])
    assert color_at(after, 20, 42) == LEAF, "塗り間違いかもしれない所は塗り替えない"


def test_yellow_cyan_and_magenta_lines_are_boundaries():
    pixels = np.zeros((4, 6, 4), dtype=np.uint8)
    for x, rgb in enumerate([(255, 255, 50), (0, 255, 255), (255, 0, 255),
                             (255, 255, 255), SKIN, (0, 0, 0)]):
        pixels[:, x] = (*rgb, 255)
    mask = auto_paint.primary_line_mask(pixels)
    assert mask[0, :3].all(), "黄・水色・赤紫の線も色分けの線"
    assert not mask[0, 3:].any(), "白・肌・黒は色分けの線ではない"


def test_new_part_is_listed_and_left_unpainted():
    # 肌の中に、見本に無い小さな囲み（開いた口など）が出てきた。
    target = with_box(figure(2), 16, 36, 10)
    plan = auto_paint.plan_auto_paint([figure(0, painted=True), target], 0)
    assert plan is not None
    kinds = {finding.kind: finding for finding in plan.cells[1].notes}
    assert reference_check.KIND_NEW_PART in kinds
    after = auto_paint.apply_paint(target, plan.cells[1])
    assert color_at(after, 20, 40) is None, "新しく出てきた部分は塗らない"
    assert color_at(after, 14, 20) == SKIN


def test_colour_line_without_answer_is_listed_and_kept():
    reference = figure(0, painted=True)
    reference[34:55, 31:59] = 0  # 見本でも右下は塗っていない
    target = figure(2)
    target[50, 36:58] = (*GREEN, 255)
    plan = auto_paint.plan_auto_paint([reference, target], 0)
    assert plan is not None
    kinds = {finding.kind for finding in plan.cells[1].notes}
    assert reference_check.KIND_UNDECIDED_LINE in kinds
    after = auto_paint.apply_paint(target, plan.cells[1])
    assert color_at(after, 45, 50) == GREEN, "決められない線はそのまま残す"


def put_cells(window, cells):
    """アクティブレイヤーの各コマへ絵を置く。"""
    canvas = window.canvas
    canvas._ensure_frame_count(len(cells))
    for frame, pixels in zip(canvas.frames, cells):
        layer = frame.layers[canvas.active_layer_index]
        full = np.zeros((layer.image.height(), layer.image.width(), 4), dtype=np.uint8)
        full[:pixels.shape[0], :pixels.shape[1]] = pixels
        layer.image = imaging.rgba_array_to_qimage(full)
        layer.has_content = True


def test_auto_paint_paints_only_the_layer_and_undoes_once(window):
    canvas = window.canvas
    canvas.add_layer()
    other = canvas.active_layer_index
    put_cells(window, [figure(0), figure(2), figure(4)])
    canvas.active_layer_index = 0
    put_cells(window, [figure(0, painted=True), figure(2), figure(4)])
    before = [frame.layers[other].image.copy() for frame in canvas.frames]

    cells = window.copy_paint.cells()
    assert window.auto_paint.run(cells, 0)

    assert canvas.frames[1].layers[0].image.pixelColor(47, 20) == QColor(*LEAF)
    for frame, original in zip(canvas.frames, before):
        assert frame.layers[other].image == original, "ほかのレイヤーには触れない"
    entry = canvas.undo_stack[-1]
    assert isinstance(entry, ScopedBatchUndo)
    assert len(entry.cells) == 2
    canvas.undo()
    assert canvas.frames[1].layers[0].image.pixelColor(47, 20).alpha() == 0


def test_auto_paint_lists_spots_to_check(window):
    canvas = window.canvas
    reference = figure(0, painted=True)
    target = with_box(figure(2), 15, 36, 14, fill=LEAF)
    put_cells(window, [reference, target])

    assert window.auto_paint.run(window.copy_paint.cells(), 0)

    dialog = window.reference_check.dialog
    assert dialog is not None
    assert dialog.windowTitle() == "自動塗りの確かめたい所"
    assert dialog.tree.topLevelItemCount() >= 1
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    assert canvas.current_frame == 1
    dialog.close()


def overpainted(dx):
    """右半分を、青の線ごと服の色で塗りかけてしまった絵。"""
    pixels = figure(dx)
    pixels[11:55, 31 + dx:59 + dx] = (*DRESS, 255)
    return pixels


@pytest.mark.parametrize("keep_initial", [True, False])
def test_initial_state_lets_auto_paint_fix_overpainted_areas(window, keep_initial):
    canvas = window.canvas
    put_cells(window, [figure(0, painted=True), figure(2)])
    if keep_initial:
        window.initial_state.keep(confirm=False)  # 塗る前の線だけの絵を保持
    put_cells(window, [figure(0, painted=True), overpainted(2)])

    assert window.auto_paint.run(window.copy_paint.cells(), 0)

    image = canvas.frames[1].layers[0].image
    if keep_initial:
        assert image.pixelColor(47, 20) == QColor(*LEAF), "初期状態の線で部分を分けて塗り直す"
        assert image.pixelColor(47, 45) == QColor(*DRESS)
    else:
        assert image.pixelColor(47, 20) == QColor(*DRESS), "線が消えていると分けられない"


def test_initial_state_is_saved_with_the_project(window, tmp_path):
    put_cells(window, [figure(0), figure(2)])
    assert window.initial_state.keep(confirm=False) == 2
    put_cells(window, [figure(0, painted=True), figure(2)])
    path = tmp_path / "kept.pman"
    assert window.project.write(path)

    _metadata, frames, _width, _height = project_io.read_project_archive(path)
    initial = frames[0].layers[0].initial_image
    assert initial is not None
    assert initial.pixelColor(20, 40).alpha() == 0, "保持したのは塗る前の絵"
    assert frames[0].layers[0].image.pixelColor(20, 40) == QColor(*SKIN)
    assert frames[1].layers[0].initial_image is not None


def test_compare_button_shows_the_initial_state(window):
    canvas = window.canvas
    put_cells(window, [figure(0), figure(2)])
    window.compare_initial_button.setChecked(True)
    assert not canvas.show_initial_state, "保持していなければ比べられない"
    assert not window.compare_initial_button.isChecked()

    window.initial_state.keep(confirm=False)
    window.compare_initial_button.setChecked(True)
    assert canvas.show_initial_state
    window.compare_initial_button.setChecked(False)
    assert not canvas.show_initial_state


def test_auto_menu_holds_auto_paint(window):
    for action in window.menuBar().actions():
        if action.text() == "自動塗り":
            menu = action.menu()
            assert menu is not None
            assert window.a_auto_paint in menu.actions()
            assert window.a_keep_initial_state in menu.actions()
            assert window.a_clear_initial_state in menu.actions()
            return
    raise AssertionError("自動塗り")
