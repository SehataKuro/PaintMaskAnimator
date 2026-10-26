"""色分けの線で自動塗り（:mod:`auto_paint` と、メニューから呼ぶ ``window.auto_paint``）。"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator import auto_paint, fill_tracking, imaging, line_flow, bucket_fill, project_io, reference_check
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


def two_rooms(dx, divider_gap=False, wall_gap=False, painted=False):
    """黒い枠を縦線で左右の部屋に分けた絵。``dx`` だけ右へ動く。

    ``divider_gap`` なら仕切りに、``wall_gap`` なら左の壁に切れ目がある（元の線画で
    よくある、線の切れ目）。``painted`` なら見本の塗り：左は肌、右は葉。
    """
    pixels = np.zeros((160, 240, 4), dtype=np.uint8)
    x0, y0, x1, y1 = 20 + dx, 20, 200 + dx, 140
    if painted:
        pixels[y0:y1, x0:110 + dx] = (*SKIN, 255)
        pixels[y0:y1, 110 + dx:x1] = (*LEAF, 255)
    black = (0, 0, 0, 255)
    pixels[y0, x0:x1] = pixels[y1 - 1, x0:x1] = black
    pixels[y0:y1, x0] = pixels[y0:y1, x1 - 1] = pixels[y0:y1, 110 + dx] = black
    if divider_gap:
        pixels[76:84, 110 + dx] = 0
    if wall_gap:
        pixels[76:84, x0] = 0
    # 動きを決める手がかりになる、部屋の中のかぎ形の印。
    for mx, my in ((40, 40), (80, 100), (60, 70), (140, 45), (170, 110), (150, 80)):
        pixels[my, mx + dx:mx + dx + 6] = black
        pixels[my:my + 6, mx + dx + 5] = black
    return pixels


def test_rooms_joined_by_a_gap_are_painted_separately():
    target = two_rooms(3, divider_gap=True)
    cells = [two_rooms(0, painted=True), target]
    plan = auto_paint.plan_auto_paint(cells, 0, hints=[None, target],
                                      lines=[two_rooms(0), target])
    assert plan is not None
    after = auto_paint.apply_paint(target, plan.cells[1])
    assert color_at(after, 60, 50) == SKIN
    assert color_at(after, 160, 50) == LEAF, "切れ目でつながっても、手本の色で塗り分ける"
    assert color_at(after, 160, 130) == LEAF


def test_room_open_to_the_outside_through_a_gap_is_painted():
    target = two_rooms(3, wall_gap=True)
    cells = [two_rooms(0, painted=True), target]
    plan = auto_paint.plan_auto_paint(cells, 0, hints=[None, target],
                                      lines=[two_rooms(0), target])
    assert plan is not None
    after = auto_paint.apply_paint(target, plan.cells[1])
    assert color_at(after, 60, 50) == SKIN, "壁の切れ目で絵の外とつながっても塗る"
    assert color_at(after, 160, 50) == LEAF
    assert color_at(after, 10, 80) is None, "本当の絵の外は塗らない"
    assert color_at(after, 220, 80) is None


def boxed(dx, inside=None):
    """``figure`` の左の部分に、見本には無い小さな四角を描き足した絵。

    ``inside`` なら、その四角の中を人が手で塗ってある。
    """
    pixels = figure(dx)
    x0, x1 = 13 + dx, 25 + dx
    pixels[38, x0:x1] = pixels[51, x0:x1] = (0, 0, 0, 255)
    pixels[38:52, x0] = pixels[38:52, x1 - 1] = (0, 0, 0, 255)
    if inside is not None:
        pixels[39:51, x0 + 1:x1 - 1] = (*inside, 255)
    return pixels


def test_hand_painted_parts_are_kept_as_answers():
    lines = figure(2)
    hint = figure(2)
    hint[11:33, 33:61] = (*DRESS, 255)  # 見本では葉の部分を、人が服の色で塗った
    plan = auto_paint.plan_auto_paint([figure(0, painted=True), lines], 0,
                                      hints=[None, hint], lines=[figure(0), lines])
    assert plan is not None
    after = auto_paint.apply_paint(lines, plan.cells[1])
    assert color_at(after, 47, 20) == DRESS, "手で塗った色は塗り替えない"
    assert color_at(after, 52, 50) == DRESS
    assert color_at(after, 17, 20) == SKIN


def test_hand_painted_cell_guides_its_neighbours():
    cells = [figure(0, painted=True), boxed(2), boxed(4)]
    hints = [None, boxed(2), boxed(4, inside=BLUSH)]
    plan = auto_paint.plan_auto_paint(cells, 0, hints=hints,
                                      lines=[figure(0), boxed(2), boxed(4)])
    assert plan is not None
    after = auto_paint.apply_paint(cells[1], plan.cells[1])
    assert color_at(after, 20, 45) == BLUSH, "見本に無い四角は、手で塗った隣のセルから写す"
    assert color_at(after, 15, 20) == SKIN


def test_auto_paint_paints_only_the_layer_and_undoes_once(window):
    canvas = window.canvas
    canvas.add_layer()
    other = canvas.active_layer_index
    put_cells(window, [figure(0), figure(2), figure(4)])
    canvas.active_layer_index = 0
    put_cells(window, [figure(0, painted=True), figure(2), figure(4)])
    before = [frame.layers[other].image.copy() for frame in canvas.frames]

    cells = window.auto_paint.cells()
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

    assert window.auto_paint.run(window.auto_paint.cells(), 0)

    dialog = window.reference_check.dialog
    assert dialog is not None
    assert dialog.windowTitle() == "自動で塗った、確かめたい所"
    assert dialog.tree.topLevelItemCount() >= 1
    assert canvas.current_frame == 0
    assert canvas.selection_mask_bool() is None
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    assert canvas.current_frame == 0, "Focusing a row must not select pixels"
    dialog.tree.itemClicked.emit(dialog.tree.topLevelItem(0), 0)
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

    assert window.auto_paint.run(window.auto_paint.cells(), 0)

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


# 仕上げでは、原画のしるし（原色の色トレス線と、原色でない色で描き足した印）を
# 残してはいけない。けれど、まわりの色で塗りつぶしてもいけない。
CHARM = (255, 90, 195)
CHARM_PAINT = (230, 160, 158)


def figure_with_charm(dx, painted=False):
    """髪の中にピンクのしるし（チャームの点線）を足した絵。見本ではチャームの色。"""
    pixels = figure(dx, painted)
    x0 = 10 + dx
    for index in range(4):
        pixels[14 + index * 2, x0 + 30:x0 + 33] = (
            *(CHARM_PAINT if painted else CHARM), 255,
        )
    return pixels


def test_mark_mask_finds_marks_but_not_paint_colours():
    pixels = figure_with_charm(0, painted=True)
    mask = auto_paint.mark_mask(pixels)
    assert mask[14, 41], "ピンクのしるしの色（原色ではない）"
    assert not mask[20, 15], "広く塗った肌はしるしではない"
    assert not mask[10, 20], "黒の主線はしるしではない"
    assert not auto_paint.mark_mask(figure(0))[20, 30], "原色の色トレス線は色トレス線のまま"


def test_line_only_keeps_marks_in_another_colour():
    """元の線画として保持する絵に、原色でないしるしも残す（消すと塗りつぶされる）。"""
    kept = auto_paint.line_only(figure_with_charm(0))
    assert color_at(kept, 41, 14) == CHARM, "ピンクのしるしを捨ててはいけない"
    assert color_at(kept, 30, 20) == RED, "原色の色トレス線はこれまでどおり残す"
    assert color_at(kept, 10, 20) == (0, 0, 0)


def test_a_mark_in_another_colour_is_not_painted_over():
    cells = [figure_with_charm(0, painted=True), figure_with_charm(2), figure_with_charm(4)]
    plan = auto_paint.plan_auto_paint(cells, 0)
    assert plan is not None
    for index, dx in ((1, 2), (2, 4)):
        after = auto_paint.apply_paint(cells[index], plan.cells[index])
        charm = [color_at(after, 40 + dx + x, 14 + y * 2) for y in range(4) for x in range(3)]
        assert CHARM not in charm, "しるしの色は仕上げに残さない"
        assert charm.count(CHARM_PAINT) >= 8, "まわりの髪の色で塗りつぶしてはいけない"


def figure_with_red_edge(dx, painted=False):
    """シルエットの外側に沿った赤の色トレス線。見本では肌の色で塗りつぶしてある。"""
    pixels = figure(dx, painted)
    x0 = 10 + dx
    pixels[10, x0 + 5:x0 + 15] = (*SKIN, 255) if painted else (*RED, 255)
    return pixels


def test_a_colour_trace_line_on_the_silhouette_is_replaced():
    cells = [figure_with_red_edge(0, painted=True), figure_with_red_edge(2),
             figure_with_red_edge(4)]
    plan = auto_paint.plan_auto_paint(cells, 0)
    assert plan is not None
    for index, dx in ((1, 2), (2, 4)):
        after = auto_paint.apply_paint(cells[index], plan.cells[index])
        edge = [color_at(after, 15 + dx + x, 10) for x in range(10)]
        assert RED not in edge, "色トレス線は置き換える前提で置かれている"
        assert edge.count(SKIN) >= 8, "となりの部分の色で置き換える"


def test_an_undecidable_colour_trace_line_takes_the_neighbour_colour():
    """手本から決められない色トレス線も、残さずとなりの部分の色にする。

    シルエットの外側に沿った線は、ずらした先の半分が絵の外なので、手本から色を
    決められない。残すと原色の赤がそのまま仕上げに出てしまう。
    """
    target = np.zeros((40, 40, 4), dtype=np.uint8)
    target[10:30, 10:30] = (*SKIN, 255)
    target[10, 10:30] = (*RED, 255)
    paint = auto_paint.paint_from_guide(target, np.zeros((40, 40, 4), dtype=np.uint8))
    after = auto_paint.apply_paint(target, paint)
    assert color_at(after, 20, 10) == SKIN


def figure_with_hole(dx, painted=False):
    """肌の中に、黒い印で囲まれた1画素の穴がある絵。"""
    pixels = figure(dx, painted)
    x0 = 10 + dx
    pixels[39:42, x0 + 9:x0 + 12] = (0, 0, 0, 255)
    pixels[40, x0 + 10] = (0, 0, 0, 0)
    return pixels


def test_a_one_pixel_hole_inside_the_line_takes_the_line_colour():
    """主線の中にできた穴は、線の色で埋める（背景が透けるほうがまずい）。"""
    cells = [figure_with_hole(0, painted=True), figure_with_hole(2), figure_with_hole(4)]
    plan = auto_paint.plan_auto_paint(cells, 0)
    assert plan is not None
    for index, dx in ((1, 2), (2, 4)):
        after = auto_paint.apply_paint(cells[index], plan.cells[index])
        assert color_at(after, 20 + dx, 40) == (0, 0, 0), "1画素の穴から背景が透けてはいけない"


def two_colours_with_a_hole():
    """左が肌、右が頬。境目に1画素の穴が開いた絵。"""
    pixels = np.zeros((20, 20, 4), dtype=np.uint8)
    pixels[:, :10] = (*SKIN, 255)
    pixels[:, 10:] = (*BLUSH, 255)
    pixels[10, 9] = 0
    return pixels


def test_a_one_pixel_hole_between_two_colours_is_filled():
    """2つの塗りの境目にできた1画素は、どちらの色でもよいので埋める。

    まわりの色がそろっているかを少し離れた窓で見ると、境目の穴はいつまでも
    埋まらない（頬の中に緑の点が残る）。
    """
    pixels = two_colours_with_a_hole()
    cell = auto_paint._Cell(pixels)
    paints = auto_paint._tiny_holes(cell, fill_tracking._labels(pixels))

    assert len(paints) == 1
    rgb, bounds, mask = paints[0]
    assert rgb in (SKIN, BLUSH), "透明のままにしない"
    assert bounds == (9, 10, 10, 11) and mask.all()


def test_a_closed_gap_in_a_colour_trace_line_is_filled():
    """部分分けのために閉じた切れ目の画素も、こちらで足した画素なので埋める。"""
    pixels = np.zeros((24, 24, 4), dtype=np.uint8)
    pixels[:, :] = (*SKIN, 255)
    # 2画素だけ離れた色トレス線。あいだは切れ目として閉じられ、どの部分にも
    # 入らないまま透明で残る（4画素より長いので、小さな穴としては拾えない）。
    pixels[5:16, 10] = pixels[5:16, 13] = (*RED, 255)
    pixels[5:16, 11:13] = 0
    cell = auto_paint._Cell(pixels)
    assert cell.gaps[10, 11] and cell.gaps[10, 12], "切れ目は閉じたものとして扱う"

    paints = auto_paint._tiny_holes(cell, fill_tracking._labels(pixels))

    filled = {}
    for rgb, (x0, y0, _x1, _y1), mask in paints:
        for y, x in zip(*np.nonzero(mask)):
            filled[(int(y) + y0, int(x) + x0)] = rgb
    assert filled.get((10, 11)) == SKIN and filled.get((10, 12)) == SKIN
    assert len(filled) == 22, "切れ目の画素をすべて埋める"


def outside_room_with_a_fold():
    """An open wall and a narrow fold like the skirt tips in the supplied project."""
    raw = np.zeros((90, 90, 4), dtype=np.uint8)
    black = (0, 0, 0, 255)
    raw[10, 10:71] = raw[70, 10:71] = black
    raw[10:71, 10] = raw[10:71, 70] = black
    raw[20:61, 40] = black
    for y in range(20, 61):
        raw[y, 40 + (60 - y) // 2] = black
    raw[20, 40:61] = black
    raw[30:39, 40] = raw[30:39, 10] = 0
    guide = raw.copy()
    guide[11:70, 11:70] = (*SKIN, 255)
    guide[raw[:, :, 3] > 0] = raw[raw[:, :, 3] > 0]
    return raw, guide


def paint_outside_with_no_motion(raw, guide):
    target = auto_paint._Cell(raw)
    teacher = auto_paint._Cell(guide)
    flow = line_flow.Flow(np.zeros((1, 1)), np.zeros((1, 1)), np.ones((1, 1)),
                          np.ones((1, 1)), raw.shape[:2])
    paint = auto_paint.paint_cell(raw, [teacher], hint=raw,
                                 flow_for=lambda _teacher: flow, target=target)
    return auto_paint.apply_paint(raw, paint)


def test_outside_fill_restores_the_narrow_tip_of_a_fold():
    raw, guide = outside_room_with_a_fold()
    after = paint_outside_with_no_motion(raw, guide)
    expected = (guide[:, :, 3] > 0) & (raw[:, :, 3] == 0)
    assert np.all(after[expected] == guide[expected]), "Do not shave off the fold tip"
    assert np.array_equal(after[raw[:, :, 3] > 0], raw[raw[:, :, 3] > 0])
    assert not after[:10, :, 3].any(), "Keep the exterior transparent"
    assert not after[:, :10, 3].any()


@pytest.mark.parametrize("tip", [(*LEAF, 255), (0, 0, 0, 0)])
def test_outside_fill_does_not_invent_the_colour_of_a_tip(tip):
    raw, guide = outside_room_with_a_fold()
    # The fold tip has a different colour, or is intentionally transparent.
    for y in range(49, 60):
        for x in range(41, 40 + (60 - y) // 2):
            guide[y, x] = tip
    after = paint_outside_with_no_motion(raw, guide)
    # The guide has no right to introduce a new boundary inside this target region.
    target = auto_paint._Cell(raw)
    index = int(target.regions.labels[56, 41])
    region = target.regions.mask(index)
    assert np.unique(fill_tracking._labels(after)[region]).size == 1
    assert color_at(after, 41, 56) != LEAF
    assert color_at(after, 20, 50) == SKIN


@pytest.mark.parametrize("first, second", [(0, 4), (4, 0)])
def test_two_references_paint_inward_and_are_never_repainted(first, second):
    last = figure(8, painted=True)
    last[np.all(last[:, :, :3] == SKIN, axis=2) & (last[:, :, 3] > 0)] = (*BLUSH, 255)
    cells = [figure(0, painted=True), figure(2), figure(4), figure(6), last]
    originals = [cell.copy() for cell in cells]
    plan = auto_paint.plan_auto_paint(cells, first, second_reference_index=second, max_rounds=1)
    assert plan is not None
    assert set(plan.cells) == {1, 2, 3}
    assert 0 in plan.references and 4 in plan.references
    assert color_at(auto_paint.apply_paint(cells[1], plan.cells[1]), 17, 40) == SKIN
    assert color_at(auto_paint.apply_paint(cells[3], plan.cells[3]), 21, 40) == BLUSH
    assert all(np.array_equal(cell, original) for cell, original in zip(cells, originals))


def test_second_reference_supplies_a_part_absent_from_the_first():
    cells = [figure(0, painted=True), figure(2), figure(4),
             with_box(figure(6), 21, 35, 12),
             with_box(figure(8, painted=True), 23, 35, 12, fill=BLUSH)]
    plan = auto_paint.plan_auto_paint(cells, 0, second_reference_index=4)
    assert plan is not None
    after = auto_paint.apply_paint(cells[3], plan.cells[3])
    assert color_at(after, 26, 40) == BLUSH
    assert 0 not in plan.cells and 4 not in plan.cells


def test_two_reference_controller_keeps_both_painted_images_and_undoes(window):
    put_cells(window, [figure(0), figure(2), figure(4), figure(6)])
    window.initial_state.keep(confirm=False)
    put_cells(window, [figure(0, painted=True), figure(2), figure(4), figure(6, painted=True)])
    canvas = window.canvas
    first = canvas.frames[0].layers[0].image.copy()
    last = canvas.frames[3].layers[0].image.copy()
    assert window.auto_paint.run(window.auto_paint.cells(), 0, 3)
    assert canvas.frames[0].layers[0].image == first
    assert canvas.frames[3].layers[0].image == last
    assert canvas.frames[2].layers[0].image.pixelColor(49, 20) == QColor(*LEAF)
    assert len(canvas.undo_stack[-1].cells) == 2
    canvas.undo()
    assert canvas.frames[2].layers[0].image.pixelColor(49, 20).alpha() == 0
    assert canvas.frames[3].layers[0].image == last


def test_reference_picker_supports_three_and_requires_a_cell_to_paint(qapp):
    from PySide6.QtCore import Qt
    from paintmaskanimator.main_window_auto_paint import AutoPaintDialog
    cells = [(index, 0) for index in range(4)]
    images = [imaging.rgba_array_to_qimage(figure(index * 2, painted=True))
              for index in range(4)]
    dialog = AutoPaintDialog(None, "A", cells, [str(i + 1) for i in range(4)],
                             0, images=images)
    try:
        assert dialog.chosen() == [cells[0]]
        assert not dialog.references.item(0).icon().isNull()
        dialog.references.item(3).setCheckState(Qt.CheckState.Checked)
        assert dialog.chosen() == [cells[0], cells[3]]
        assert dialog.selected_refs.item(1).text() == "4"
        dialog.references.item(1).setCheckState(Qt.CheckState.Checked)
        assert dialog.chosen() == [cells[0], cells[1], cells[3]]
        assert dialog.selected_refs.item(2).text() == "4"
        assert dialog.run_button.isEnabled()
        dialog.references.item(2).setCheckState(Qt.CheckState.Checked)
        assert not dialog.run_button.isEnabled(), "Leave a cel to paint"
        for index in range(4):
            dialog.references.item(index).setCheckState(Qt.CheckState.Unchecked)
        assert not dialog.run_button.isEnabled()
    finally:
        dialog.deleteLater()


def test_three_references_are_all_used_and_preserved():
    cells = [figure(index * 2, painted=index in (0, 3, 6)) for index in range(7)]
    for index, color in ((3, BLUSH), (6, LEAF)):
        image = cells[index]
        image[np.all(image[:, :, :3] == SKIN, axis=2) & (image[:, :, 3] > 0)] = (*color, 255)
    originals = [image.copy() for image in cells]
    plan = auto_paint.plan_auto_paint(cells, 0, reference_indices=[0, 3, 6], max_rounds=1)
    assert plan is not None
    assert set(plan.cells) == {1, 2, 4, 5}
    for index, color in ((1, SKIN), (2, BLUSH), (4, BLUSH), (5, LEAF)):
        after = auto_paint.apply_paint(cells[index], plan.cells[index])
        assert color_at(after, 15 + index * 2, 40) == color
    assert all(np.array_equal(image, original) for image, original in zip(cells, originals))


def test_three_reference_controller_keeps_every_reference(window):
    cells = [figure(index * 2, painted=index in (0, 2, 4)) for index in range(5)]
    put_cells(window, cells)
    canvas = window.canvas
    originals = {index: canvas.frames[index].layers[0].image.copy() for index in (0, 2, 4)}
    assert window.auto_paint.run(window.auto_paint.cells(), 0, reference_indices=[0, 2, 4])
    for index, image in originals.items():
        assert canvas.frames[index].layers[0].image == image
    assert len(canvas.undo_stack[-1].cells) == 2
    canvas.undo()
    assert canvas.frames[1].layers[0].image.pixelColor(47, 20).alpha() == 0


def test_progress_shows_target_cels_per_round_instead_of_three_times_the_count(window, monkeypatch):
    from paintmaskanimator import main_window_auto_paint as ui
    reports = []
    class Counter:
        def wasCanceled(self):
            return False
    counter = Counter()
    monkeypatch.setattr(ui, "create_counter", lambda *args, **kwargs: counter)
    monkeypatch.setattr(ui, "close_counter", lambda dialog: None)
    monkeypatch.setattr(ui, "update_counter",
                        lambda dialog, value, total, label: reports.append((value, total, label)))
    def make_plan(pixels, reference, **kwargs):
        progress = kwargs["progress"]
        for done in (0, 3, 4, 9):
            assert progress(done, 9)
        return auto_paint.AutoPlan()
    monkeypatch.setattr(auto_paint, "plan_auto_paint", make_plan)
    window.auto_paint._plan([figure(0)] * 5, 0, reference_indices=[0, 4])
    assert [(value, total) for value, total, label in reports] == [(0, 3), (0, 3), (1, 3), (3, 3)]
    assert "1回目" in reports[0][2]
    assert "2回目" in reports[1][2]
    assert "対象3セル" in reports[2][2]


def test_auto_paint_keeps_the_existing_selection_and_frame_when_results_open(window, qapp):
    reference = figure(0, painted=True)
    target = with_box(figure(2), 15, 36, 14, fill=LEAF)
    put_cells(window, [reference, target])
    canvas = window.canvas
    canvas.current_frame = 0
    selected = np.zeros((canvas.frames[0].layers[0].image.height(),
                         canvas.frames[0].layers[0].image.width()), dtype=bool)
    selected[2:6, 3:7] = True
    assert canvas.set_selection_mask(selected)
    assert window.auto_paint.run(window.auto_paint.cells(), 0)
    qapp.processEvents()
    assert canvas.current_frame == 0
    assert np.array_equal(canvas.selection_mask_bool(), selected)
    window.reference_check.dialog.close()


def skirt_at_canvas_edge():
    raw = np.zeros((120, 120, 4), dtype=np.uint8)
    # Outer walls and a fold stop just short of the lower canvas edge.
    raw[10, 20:101] = (0, 0, 0, 255)
    raw[10:116, 20] = raw[10:116, 100] = (0, 0, 0, 255)
    raw[45:116, 60] = (*RED, 255)
    guide = raw.copy()
    guide[11:, 21:60] = (*SKIN, 255)
    guide[11:45, 60:100] = (*SKIN, 255)
    guide[45:, 61:100] = (*SKIN, 255)
    return raw, guide


def test_auto_paint_region_matches_an_ordinary_bucket_and_keeps_both_line_types():
    raw, guide = skirt_at_canvas_edge()
    after = paint_outside_with_no_motion(raw, guide)
    bucket = bucket_fill.compute_fill_region(
        raw, 30, 70, bucket_fill.BucketOptions(extend_lines=True),
    )
    assert bucket.ok
    assert bucket.region is not None
    assert np.all(after[bucket.region, 3] == 255), "Fill the entire bucket region, including edges"
    assert np.all(after[bucket.region, :3] == SKIN)
    black = auto_paint.boundary_mask(raw) & ~auto_paint.primary_line_mask(raw)
    assert np.array_equal(after[black], raw[black]), "Never move or overwrite a solid line"
    # The TP line separates two bucket regions even though their finished colours can match.
    assert bucket.region[70, 59] and not bucket.region[70, 61]
    assert not after[:, :20, 3].any(), "Do not copy the guide silhouette into the exterior"


def test_a_guide_must_not_cut_a_new_boundary_inside_a_target_region():
    raw = np.zeros((100, 120, 4), dtype=np.uint8)
    raw[10, 10:111] = raw[90, 10:111] = (0, 0, 0, 255)
    raw[10:91, 10] = raw[10:91, 110] = (0, 0, 0, 255)
    guide = raw.copy()
    guide[11:90, 11:60] = (*SKIN, 255)
    guide[11:90, 60:110] = (*LEAF, 255)
    after = paint_outside_with_no_motion(raw, guide)
    assert not after[11:90, 11:110, 3].any(), "Unclear colour: report it rather than invent a cut"


@pytest.fixture
def reference_picker(qapp):
    from paintmaskanimator.auto_paint_dialog import AutoPaintDialog
    cells = [(i * 3, 0) for i in range(6)]
    labels = [f"コマ {i * 3 + 1}（セル {i + 1}）" for i in range(6)]
    images = [imaging.rgba_array_to_qimage(figure(i, painted=True)) for i in range(6)]
    dialog = AutoPaintDialog(None, "A", cells, labels, 0, images=images)
    dialog.show()
    qapp.processEvents()
    yield dialog
    dialog.close()
    dialog.deleteLater()
    qapp.processEvents()


def test_clicking_the_thumbnail_toggles_a_reference_without_ctrl(reference_picker, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    dialog = reference_picker
    item = dialog.references.item(2)
    point = dialog.references.visualItemRect(item).center()
    QTest.mouseClick(dialog.references.viewport(), Qt.MouseButton.LeftButton, pos=point)
    qapp.processEvents()
    assert dialog.chosen() == [dialog.cells[0], dialog.cells[2]]
    assert dialog.preview_title.text() == dialog.labels[2]
    assert "2" in dialog.selection_status.text()
    QTest.mouseClick(dialog.references.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert dialog.chosen() == [dialog.cells[0]]


def test_reference_search_keeps_hidden_choices_and_selected_list_reveals_them(reference_picker):
    from PySide6.QtCore import Qt
    dialog = reference_picker
    dialog.references.item(5).setCheckState(Qt.CheckState.Checked)
    dialog.search.setText("16")
    assert dialog.references.item(0).isHidden()
    assert not dialog.references.item(5).isHidden()
    assert dialog.chosen() == [dialog.cells[0], dialog.cells[5]]
    dialog.selected_refs.itemClicked.emit(dialog.selected_refs.item(0))
    assert dialog.search.text() == ""
    assert dialog.references.currentRow() == 0
    assert dialog.chosen() == [dialog.cells[0], dialog.cells[5]]
    dialog.selected_refs.setCurrentRow(1)
    dialog.remove_button.click()
    assert dialog.chosen() == [dialog.cells[0]]
    dialog.clear_button.click()
    assert not dialog.chosen()
    assert not dialog.run_button.isEnabled()


def test_picker_keyboard_zoom_and_jump_do_not_reset_choices(reference_picker):
    from PySide6.QtCore import QItemSelectionModel, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton
    dialog = reference_picker
    dialog.references.setCurrentRow(1, QItemSelectionModel.SelectionFlag.NoUpdate)
    QTest.keyClick(dialog.references, Qt.Key.Key_Space)
    assert dialog.chosen() == [dialog.cells[0], dialog.cells[1]]
    dialog.card_size.setValue(220)
    dialog.findChild(QPushButton, "reference-last").click()
    assert dialog.references.currentRow() == 5
    assert dialog.chosen() == [dialog.cells[0], dialog.cells[1]]
    assert dialog.preview_title.text() == dialog.labels[5]
    dialog.toggle_reference.click()
    assert dialog.chosen() == [dialog.cells[0], dialog.cells[1], dialog.cells[5]]


def test_enter_in_reference_search_does_not_start_painting(reference_picker):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    dialog = reference_picker
    dialog.search.setFocus()
    dialog.search.setText("16")
    QTest.keyClick(dialog.search, Qt.Key.Key_Return)
    assert dialog.isVisible()
    assert dialog.chosen() == [dialog.cells[0]]
