"""図形のライン・Shiftブラシ・投げ縄の参照設定・モーダルスポイト。"""
from typing import Any

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from paintmaskanimator import bucket_fill, utils
from paintmaskanimator.canvas import PaintCanvas
from paintmaskanimator.color_picker_dialog import ColorPickerDialog
from paintmaskanimator.toolpanel import TOOL_DEFINITIONS, ToolPanel


@pytest.fixture
def drawing(qtbot):
    host: Any = ToolPanel()
    host.tools = host
    qtbot.addWidget(host)
    canvas = PaintCanvas()
    canvas.setParent(host)
    canvas.resize(600, 500)
    canvas.zoom = 1
    canvas.pan = QPointF(100, 100)
    canvas.active_layer_index = next(
        i for i, layer in enumerate(canvas.frames[0].layers) if not layer.is_paper
    )
    canvas.ensure_editable_key()
    canvas.active_layer.image.fill(Qt.GlobalColor.transparent)
    canvas.main_color = QColor("red")
    canvas.color_mode = "main"
    canvas.pen_size = 3
    canvas.brush_stabilizer_strength = 0
    host.toolChanged.connect(canvas.set_tool)
    return host, canvas


def mouse(canvas, kind, x, y, shift=False):
    point = canvas.canvas_to_widget(QPointF(x, y))
    event = QMouseEvent(
        kind, point, QPointF(canvas.mapToGlobal(point.toPoint())),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton if kind == QMouseEvent.Type.MouseButtonRelease
        else Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(canvas, event)


def drag(canvas, start, end, shift=False):
    mouse(canvas, QMouseEvent.Type.MouseButtonPress, *start, shift)
    mouse(canvas, QMouseEvent.Type.MouseMove, *end, shift)
    mouse(canvas, QMouseEvent.Type.MouseButtonRelease, *end, shift)


@pytest.mark.parametrize("kind", ["straight", "curve"])
def test_shape_tool_draws_lines_and_curves(drawing, kind):
    panel, canvas = drawing
    assert "line" not in dict(TOOL_DEFINITIONS)
    panel.shape_type.setCurrentIndex(panel.shape_type.findData(kind))
    panel.select_tool("shape")
    assert canvas.effective_tool() == "line"
    assert not panel.shape_type.isHidden()
    assert not panel.brush_size_spinbox.isHidden()
    assert panel.shape_fill_inside.isHidden()
    drag(canvas, (20, 20), (100, 20))
    if kind == "curve":
        assert canvas.line_curve_stage == 2
        assert canvas.active_layer.image.pixelColor(60, 20).alpha() == 0
        mouse(canvas, QMouseEvent.Type.MouseButtonPress, 60, 100)
        mouse(canvas, QMouseEvent.Type.MouseButtonRelease, 60, 100)
        assert canvas.active_layer.image.pixelColor(60, 60) == QColor("red")
    else:
        assert canvas.active_layer.image.pixelColor(60, 20) == QColor("red")
    assert canvas.line_curve_stage == 0


def test_shift_brush_ignores_detour_and_commits_one_straight_line(drawing):
    panel, canvas = drawing
    panel.line_type.setCurrentIndex(panel.line_type.findData("curve"))
    panel.select_tool("brush")
    mouse(canvas, QMouseEvent.Type.MouseButtonPress, 20, 20, True)
    mouse(canvas, QMouseEvent.Type.MouseMove, 60, 90, True)
    assert canvas.active_layer.image.pixelColor(60, 90).alpha() == 0
    mouse(canvas, QMouseEvent.Type.MouseButtonRelease, 100, 20, True)
    assert canvas.active_layer.image.pixelColor(60, 20) == QColor("red")
    assert canvas.active_layer.image.pixelColor(60, 90).alpha() == 0
    assert canvas.effective_tool() == "brush"
    canvas.undo()
    assert canvas.active_layer.image.pixelColor(60, 20).alpha() == 0


def test_shift_click_connects_previous_brush_endpoint(drawing):
    panel, canvas = drawing
    panel.select_tool("brush")
    drag(canvas, (20, 20), (30, 20))
    drag(canvas, (100, 20), (100, 20), True)
    assert canvas.active_layer.image.pixelColor(65, 20) == QColor("red")


def test_shift_line_can_be_canceled(drawing):
    panel, canvas = drawing
    panel.select_tool("brush")
    mouse(canvas, QMouseEvent.Type.MouseButtonPress, 20, 20, True)
    mouse(canvas, QMouseEvent.Type.MouseMove, 100, 20, True)
    QTest.keyClick(canvas, Qt.Key.Key_Escape)
    mouse(canvas, QMouseEvent.Type.MouseButtonRelease, 100, 20)
    assert canvas.active_layer.image.pixelColor(60, 20).alpha() == 0
    assert canvas.effective_tool() == "brush"


@pytest.mark.parametrize("reference", bucket_fill.REFERENCES)
@pytest.mark.parametrize("target", bucket_fill.TARGET_COLORS)
def test_lasso_target_uses_selected_reference(drawing, reference, target):
    panel, canvas = drawing
    painter = QPainter(canvas.active_layer.image)
    painter.fillRect(10, 10, 35, 60, QColor("black"))
    painter.end()
    canvas.active_layer.initial_image = canvas.active_layer.image.copy()
    canvas.add_layer()
    canvas.ensure_editable_key()
    canvas.active_layer.image.fill(Qt.GlobalColor.transparent)
    canvas.active_layer.initial_image = canvas.frames[0].layers[canvas.active_layer_index-1].image.copy()
    panel.bucket_reference.setCurrentIndex(panel.bucket_reference.findData(reference))
    panel.bucket_target_color.setCurrentIndex(panel.bucket_target_color.findData(target))
    panel.select_tool("lasso_fill")
    assert not panel.bucket_reference_row.isHidden()
    assert not panel.bucket_target_row.isHidden()
    canvas.fill_lasso_polygon([
        QPointF(15, 15), QPointF(80, 15), QPointF(80, 65), QPointF(15, 65),
    ])
    left = canvas.active_layer.image.pixelColor(25, 30)
    right = canvas.active_layer.image.pixelColor(60, 30)
    black_targets = {
        bucket_fill.TARGET_BLACK, bucket_fill.TARGET_SURROUNDED_BY_TRANSPARENT,
        bucket_fill.TARGET_SURROUNDED_BY_WHITE_TRANSPARENT,
        bucket_fill.TARGET_OPAQUE_AND_INNER_TRANSPARENT,
    }
    if target in (bucket_fill.TARGET_ALL, bucket_fill.TARGET_TRANSPARENT_AND_CLOSED_OPAQUE):
        painted_left = painted_right = True
    else:
        painted_right = target not in black_targets
        painted_left = painted_right if reference == bucket_fill.REFERENCE_LAYER else not painted_right
    assert (left == QColor("red")) == painted_left
    assert (right == QColor("red")) == painted_right


def test_lasso_with_no_matching_target_does_not_add_undo(drawing):
    panel, canvas = drawing
    panel.bucket_target_color.setCurrentIndex(panel.bucket_target_color.findData(bucket_fill.TARGET_BLACK))
    before = len(canvas.undo_stack)
    canvas.fill_lasso_polygon([QPointF(10, 10), QPointF(80, 10), QPointF(80, 80)])
    assert len(canvas.undo_stack) == before
    assert canvas.active_layer.image.pixelColor(50, 30).alpha() == 0


def test_lasso_boundary_option_works_with_explicit_black_target(drawing):
    panel, canvas = drawing
    painter = QPainter(canvas.active_layer.image)
    painter.fillRect(10, 10, 35, 60, QColor("black"))
    painter.end()
    panel.bucket_target_color.setCurrentIndex(panel.bucket_target_color.findData(bucket_fill.TARGET_BLACK))
    panel.lasso_inside_boundary.setChecked(True)
    canvas.fill_lasso_polygon([
        QPointF(15, 15), QPointF(80, 15), QPointF(80, 65), QPointF(15, 65),
    ])
    assert canvas.active_layer.image.pixelColor(25, 30) == QColor("red")
    assert canvas.active_layer.image.pixelColor(60, 30).alpha() == 0


def test_modal_color_picker_eyedropper_receives_escape(qtbot, monkeypatch):
    monkeypatch.setattr(utils, "show_screen_color_loupe", lambda *a, **kw: None)
    dialog = ColorPickerDialog(QColor("#123456"))
    qtbot.addWidget(dialog)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.show()
    QApplication.processEvents()
    dialog.eyedropper.click()
    overlay = dialog._screen_picker
    assert overlay is not None and overlay.parentWidget() is dialog
    assert QApplication.activeModalWidget() is dialog
    QTest.keyClick(overlay, Qt.Key.Key_Escape)
    assert dialog._screen_picker is None
    assert dialog.isVisible()
    assert dialog.color() == QColor("#123456")


def test_closing_color_dialog_cancels_eyedropper(qtbot, monkeypatch):
    monkeypatch.setattr(utils, "show_screen_color_loupe", lambda *a, **kw: None)
    dialog = ColorPickerDialog(QColor("red"))
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.eyedropper.click()
    overlay = dialog._screen_picker
    dialog.done(QDialog.DialogCode.Accepted)
    assert overlay._finished and dialog._screen_picker is None


def test_modal_eyedropper_accepts_color_and_returns_to_dialog(qtbot, monkeypatch):
    monkeypatch.setattr(utils, "show_screen_color_loupe", lambda *a, **kw: None)
    monkeypatch.setattr(utils, "_sample_screen_color", lambda *a: QColor("#987654"))
    seen = []
    dialog = ColorPickerDialog(QColor("red"), preview=seen.append)
    qtbot.addWidget(dialog)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.show()
    dialog.eyedropper.click()
    dialog._screen_picker.finish(QPoint(10, 10))
    assert dialog._screen_picker is None and dialog.isVisible()
    assert dialog.color() == QColor("#987654")
    assert seen[-1] == QColor("#987654")
    dialog.reject()
    assert seen[-1] == QColor("red")


def test_pen_shift_drag_draws_straight_line(drawing):
    from types import SimpleNamespace

    panel, canvas = drawing
    panel.select_tool("brush")
    for kind, coords in (
        (QEvent.Type.TabletPress, (20, 20)),
        (QEvent.Type.TabletMove, (60, 90)),
        (QEvent.Type.TabletRelease, (100, 20)),
    ):
        point = canvas.canvas_to_widget(QPointF(*coords))
        event = SimpleNamespace(
            Type=QEvent.Type, type=lambda kind=kind: kind, position=lambda point=point: point,
            buttons=lambda: Qt.MouseButton.LeftButton,
            modifiers=lambda: Qt.KeyboardModifier.ShiftModifier,
            accept=lambda: None, ignore=lambda: None,
        )
        canvas.tabletEvent(event)
    assert canvas.active_layer.image.pixelColor(60, 20) == QColor("red")
    assert canvas.active_layer.image.pixelColor(60, 90).alpha() == 0
    assert canvas.effective_tool() == "brush"
