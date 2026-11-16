"""線整えを範囲で：メニューの範囲（選択範囲）と、ゴミ取りツールでなぞった所だけ整える。"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog

from paintmaskanimator import imaging, line_tidy
from paintmaskanimator.line_tidy_region import region_mask

BLACK = (0, 0, 0)

STAIRS = np.array([[ch == "#" for ch in row] for row in """
.........
.###.....
....###..
.......##
.........
""".strip().splitlines()])

LEFT, RIGHT = (10, 10), (10, 80)  # (y, x) 階段を2か所に置く


@pytest.fixture
def window(tmp_path, monkeypatch):
    from paintmaskanimator.main_window import MainWindow

    QApplication.instance() or QApplication([])
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def paint_two_stairs(window, frame_count=1):
    from paintmaskanimator.document import create_drawing

    canvas = window.canvas
    canvas._ensure_frame_count(frame_count)
    for index in range(frame_count):
        layer = canvas.frames[index].layers[0]
        layer.image.fill(QColor(0, 0, 0, 0))
        for oy, ox in (LEFT, RIGHT):
            for y, x in zip(*np.nonzero(STAIRS)):
                layer.image.setPixelColor(int(x) + ox, int(y) + oy, QColor("black"))
        create_drawing(layer, layer.image.copy(), number=index + 1)
    canvas.current_frame = 0
    canvas.active_layer_index = 0


def line_pixels(window, frame=0, at=LEFT):
    rgba = imaging.qimage_rgba_array(window.canvas.frames[frame].layers[0].image)
    oy, ox = at
    part = rgba[oy:oy + STAIRS.shape[0], ox:ox + STAIRS.shape[1]]
    return int(((part[:, :, 3] > 0) & (part[:, :, :3] == 0).all(axis=2)).sum())


def select_rect(canvas, x1, y1, x2, y2):
    canvas.selection_polygon = [QPointF(x1, y1), QPointF(x2, y1), QPointF(x2, y2), QPointF(x1, y2)]
    canvas.selection_mask_override = None


# -- 純粋な処理 ----------------------------------------------------------------
def test_tidy_lines_changes_only_inside_the_region():
    rgba = np.zeros((20, 40, 4), dtype=np.uint8)
    for ox in (2, 22):
        rgba[5:10, ox:ox + 9][STAIRS] = (0, 0, 0, 255)
    region = np.zeros((20, 40), dtype=bool)
    region[:, :20] = True

    _out, added, removed = line_tidy.tidy_lines(rgba, [BLACK], region=region)

    assert added.sum() == 2 and not removed.any()
    assert not added[:, 20:].any(), "範囲の外は変えない"


def test_region_mask_brush_is_a_band_and_lasso_is_the_inside():
    band = region_mask([(10, 10), (30, 10)], "brush", 40, 20, brush_width=6)
    assert band[10, 20] and band[8, 20] and not band[2, 20]
    inside = region_mask([(5, 5), (25, 5), (25, 15), (5, 15)], "lasso", 40, 20)
    assert inside[10, 15] and not inside[10, 30]
    assert not region_mask([(5, 5), (6, 6)], "lasso", 40, 20).any(), "2点では囲めない"


# -- メニューの線整え：範囲 ------------------------------------------------------
def scope_rows(dialog):
    combo = dialog.findChild(QComboBox, "lineTidyScope")
    model = combo.model()
    return combo, [(combo.itemText(i), model.item(i).isEnabled()) for i in range(combo.count())]


def test_dialog_selection_scope_is_disabled_without_a_selection(window, monkeypatch):
    paint_two_stairs(window)
    seen = {}

    def accept(dialog):
        combo, rows = scope_rows(dialog)
        seen["rows"], seen["current"] = rows, combo.currentIndex()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", accept)
    window.line_ops.tidy_lines_dialog()
    assert [enabled for _text, enabled in seen["rows"]] == [True, True, False, False]
    assert seen["current"] == 0


def test_dialog_tidies_only_inside_the_selection_by_default(window, monkeypatch):
    paint_two_stairs(window)
    select_rect(window.canvas, 0, 0, 50, 40)
    monkeypatch.setattr(QDialog, "exec", lambda _self: QDialog.DialogCode.Accepted)

    assert window.line_ops.tidy_lines_dialog()

    assert line_pixels(window, at=LEFT) == STAIRS.sum() + 2
    assert line_pixels(window, at=RIGHT) == STAIRS.sum(), "選択範囲の外はそのまま"


# -- ゴミ取りツールの線整え ------------------------------------------------------
def use_line_tidy_tool(window, shape="brush"):
    tools = window.tools
    tools.select_tool("dust")
    tools.dust_mode.setCurrentIndex(tools.dust_mode.findData("line_tidy"))
    tools.line_tidy_shape.setCurrentIndex(tools.line_tidy_shape.findData(shape))
    return tools


def test_line_tidy_mode_shows_its_own_settings(window):
    tools = use_line_tidy_tool(window)
    assert tools.line_tidy_stroke_active()
    assert tools.line_tidy_shape.isVisibleTo(tools)
    assert tools.line_tidy_brush_size.isVisibleTo(tools)
    assert not tools.dust_size.isVisibleTo(tools)
    tools.line_tidy_shape.setCurrentIndex(tools.line_tidy_shape.findData("lasso"))
    assert not tools.line_tidy_brush_size.isVisibleTo(tools)
    tools.dust_mode.setCurrentIndex(tools.dust_mode.findData("despeckle"))
    assert not tools.line_tidy_stroke_active()
    assert tools.dust_size.isVisibleTo(tools)
    assert not tools.line_tidy_shape.isVisibleTo(tools)


def drag(canvas, points):
    widget_points = [canvas.canvas_to_widget(QPointF(*p)).toPoint() for p in points]
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, widget_points[0])
    for point in widget_points[1:]:
        QTest.mouseMove(canvas, point)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, widget_points[-1])


@pytest.mark.parametrize("shape", ["brush", "lasso"])
def test_tracing_on_the_canvas_tidies_only_the_traced_part(window, shape):
    paint_two_stairs(window)
    use_line_tidy_tool(window, shape)
    window.tools.line_tidy_brush_size.setValue(30)
    assert window.canvas.effective_tool() == "dust"
    depth = len(window.canvas.undo_stack)

    if shape == "brush":
        drag(window.canvas, [(12, 12), (16, 13), (20, 14)])
    else:
        drag(window.canvas, [(2, 2), (40, 2), (40, 30), (2, 30)])

    assert line_pixels(window, at=LEFT) == STAIRS.sum() + 2
    assert line_pixels(window, at=RIGHT) == STAIRS.sum()
    assert len(window.canvas.undo_stack) == depth + 1
    window.canvas.undo()
    assert line_pixels(window, at=LEFT) == STAIRS.sum()


def test_apply_button_in_line_tidy_mode_uses_the_selection_or_the_whole_frame(window):
    paint_two_stairs(window)
    tools = use_line_tidy_tool(window)
    select_rect(window.canvas, 60, 0, 120, 40)
    tools.dust_apply.click()
    assert line_pixels(window, at=LEFT) == STAIRS.sum()
    assert line_pixels(window, at=RIGHT) == STAIRS.sum() + 2

    window.canvas.selection_polygon = []
    tools.dust_apply.click()
    assert line_pixels(window, at=LEFT) == STAIRS.sum() + 2
