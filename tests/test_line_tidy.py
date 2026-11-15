"""線整え（#64）：角だけで接する段をつなぐ・突き出た1画素を消す。"""
from __future__ import annotations

import numpy as np
import pytest

from paintmaskanimator import line_tidy


def grid(text):
    return np.array([[ch == "#" for ch in row] for row in text.strip().splitlines()])


def orientations(mask):
    """90°回転と左右反転を組み合わせた8つの向き。"""
    for k in range(4):
        rotated = np.rot90(mask, k)
        yield rotated
        yield np.fliplr(rotated)


def four_connected(mask):
    """4近傍で1つにつながっているか。"""
    ys, xs = np.nonzero(mask)
    seen = {(ys[0], xs[0])}
    stack = [(ys[0], xs[0])]
    cells = set(zip(ys, xs))
    while stack:
        y, x = stack.pop()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (y + dy, x + dx)
            if n in cells and n not in seen:
                seen.add(n)
                stack.append(n)
    return len(seen) == len(cells)


STAIRS = grid("""
.........
.###.....
....###..
.......##
.........
""")

SPUR = grid("""
.........
....#....
.#######.
.........
""")


@pytest.mark.parametrize("index", range(8))
def test_steps_touching_only_at_a_corner_are_joined(index):
    mask = list(orientations(STAIRS))[index]
    add, remove = line_tidy.tidy_mask(mask)
    assert not remove.any()
    assert add.sum() == 2, "角1点ずつ、1画素だけ足す"
    assert not four_connected(mask) and four_connected(mask | add)


@pytest.mark.parametrize("index", range(8))
def test_a_one_pixel_spur_is_removed(index):
    mask = list(orientations(SPUR))[index]
    add, remove = line_tidy.tidy_mask(mask)
    assert remove.sum() == 1 and not add.any()
    tip = list(orientations(np.pad(np.array([[True]]), ((1, 2), (4, 4)))))[index]
    assert (remove == tip).all(), "消すのは突き出た1画素だけ"


@pytest.mark.parametrize("shape", [
    # 1画素幅の線の先端（となり1画素）
    """
    .......
    .#####.
    .......
    """,
    # 交差
    """
    ...#...
    ...#...
    #######
    ...#...
    ...#...
    """,
    # 直角の角
    """
    .#.....
    .#.....
    .#####.
    """,
    # T字の分岐（2画素の枝。根元の画素はとなりが4画素ある）
    """
    ...#...
    ...#...
    .#####.
    .#####.
    """,
    # 1画素幅の45°の線（段を持たない）
    """
    #......
    .#.....
    ..#....
    ...#...
    """,
    # 太さ2の線の端
    """
    .......
    .#####.
    .#####.
    .......
    """,
])
@pytest.mark.parametrize("index", range(8))
def test_ends_crossings_corners_and_diagonals_are_left_alone(shape, index):
    mask = list(orientations(grid("\n".join(line.strip() for line in shape.strip().splitlines()))))[index]
    add, remove = line_tidy.tidy_mask(mask)
    assert not add.any() and not remove.any()


def test_tidy_lines_writes_the_line_colour_and_the_surrounding_colour():
    skin = (255, 228, 205)
    rgba = np.zeros((5, 9, 4), dtype=np.uint8)
    rgba[:, :] = (*skin, 255)
    rgba[STAIRS] = (0, 0, 0, 255)
    rgba[0, 2] = (0, 0, 0, 255)  # 段の上に突き出た1画素（ヒゲ）

    out, added, removed = line_tidy.tidy_lines(rgba, [(0, 0, 0)])

    assert removed[0, 2] and tuple(out[0, 2]) == (*skin, 255)
    assert added.sum() == 2
    assert all(tuple(out[y, x]) == (0, 0, 0, 255) for y, x in zip(*np.nonzero(added)))


def test_does_not_add_over_another_line_colour():
    rgba = np.zeros((5, 9, 4), dtype=np.uint8)
    rgba[STAIRS] = (0, 0, 0, 255)
    add, _remove = line_tidy.tidy_mask(STAIRS)
    rgba[add] = (255, 0, 0, 255)  # 足すはずの所に赤の線

    _out, added, _removed = line_tidy.tidy_lines(rgba, [(0, 0, 0), (255, 0, 0)])

    assert not (added & add).any()


@pytest.fixture
def window(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from paintmaskanimator.main_window import MainWindow

    QApplication.instance() or QApplication([])
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def _paint_stairs(window, frame_count=2):
    from PySide6.QtGui import QColor

    from paintmaskanimator.document import create_drawing

    canvas = window.canvas
    canvas._ensure_frame_count(frame_count)
    for index in range(frame_count):
        layer = canvas.frames[index].layers[0]
        layer.image.fill(QColor(0, 0, 0, 0))
        for y, x in zip(*np.nonzero(STAIRS)):
            layer.image.setPixelColor(int(x) + 10, int(y) + 10, QColor("black"))
        create_drawing(layer, layer.image.copy(), number=index + 1)
    canvas.current_frame = 0
    canvas.active_layer_index = 0


def test_line_colours_default_to_dark_and_primary_colours(window):
    _paint_stairs(window, 1)
    assert window.line_ops.tidy_line_colours() == [(0, 0, 0)]


def test_preview_marks_the_pixels_it_will_add(window):
    from paintmaskanimator import imaging

    _paint_stairs(window, 1)
    rgba = imaging.qimage_rgba_array(window.canvas.active_layer.image)
    preview, added, removed = window.line_ops.line_tidy_preview(rgba, [(0, 0, 0)])
    assert preview is not None and (added, removed) == (2, 0)


def test_tidy_every_frame_and_undo_once(window):
    from paintmaskanimator.undo_entries import ScopedBatchUndo

    _paint_stairs(window, 2)
    before = [frame.layers[0].image.copy() for frame in window.canvas.frames]

    assert window.line_ops.tidy_lines([(0, 0, 0)], all_frames=True)

    from paintmaskanimator import imaging

    for frame in window.canvas.frames:
        rgba = imaging.qimage_rgba_array(frame.layers[0].image)
        line = (rgba[:, :, 3] > 0) & (rgba[:, :, :3] == 0).all(axis=2)
        assert line.sum() == STAIRS.sum() + 2 and four_connected(line)
    assert isinstance(window.canvas.undo_stack[-1], ScopedBatchUndo)
    window.canvas.undo()
    for frame, original in zip(window.canvas.frames, before):
        assert frame.layers[0].image == original


def test_nothing_to_tidy_changes_nothing(window):
    _paint_stairs(window, 1)
    window.line_ops.tidy_lines([(0, 0, 0)])
    depth = len(window.canvas.undo_stack)
    assert window.line_ops.tidy_lines([(0, 0, 0)]) is False
    assert len(window.canvas.undo_stack) == depth


def test_the_dialog_applies_to_the_current_frame(window, monkeypatch):
    from PySide6.QtWidgets import QDialog

    _paint_stairs(window, 2)
    second = window.canvas.frames[1].layers[0].image.copy()
    monkeypatch.setattr(QDialog, "exec", lambda _self: QDialog.DialogCode.Accepted)

    assert window.line_ops.tidy_lines_dialog()

    assert window.canvas.frames[1].layers[0].image == second, "既定は今のコマだけ"


# -- パターン3：段の重なりが厚くなった所 -------------------------------------
THICK_STEP = grid("""
.........
.##......
..###....
...####..
.........
""")
# 下の段（4画素）が長いので、その始まり (3, 3) を消す。
THICK_STEP_FIXED = grid("""
.........
.##......
..###....
....###..
.........
""")


@pytest.mark.parametrize("index", range(8))
def test_a_thick_step_overlap_loses_one_pixel(index):
    mask = list(orientations(THICK_STEP))[index]
    expected = list(orientations(THICK_STEP_FIXED))[index]
    add, remove = line_tidy.tidy_mask(mask)
    assert not add.any()
    assert remove.sum() == 1
    assert ((mask & ~remove) == expected).all()
    assert four_connected(mask & ~remove), "線は切れない"


def test_the_longer_step_loses_the_pixel():
    upper_longer = grid("""
........
.####...
...###..
........
""")
    _add, remove = line_tidy.tidy_mask(upper_longer)
    assert remove.sum() == 1 and remove[1, 4], "上の段（4画素）の終わりを消す"


def test_equal_steps_remove_the_start_of_the_lower_step():
    equal = grid("""
........
.###....
..###...
........
""")
    _add, remove = line_tidy.tidy_mask(equal)
    assert remove.sum() == 1 and remove[2, 2]


@pytest.mark.parametrize("shape", [
    # 太さ2の45°の階段（重なりは1画素ずつ）
    """
    ##....
    .##...
    ..##..
    ...##.
    """,
    # 太さ2のまっすぐな線
    """
    ......
    .####.
    .####.
    ......
    """,
])
@pytest.mark.parametrize("index", range(8))
def test_thick_lines_are_not_thinned(shape, index):
    mask = list(orientations(grid("\n".join(line.strip() for line in shape.strip().splitlines()))))[index]
    _add, remove = line_tidy.tidy_mask(mask)
    assert not remove.any()
