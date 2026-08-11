"""Undo/redo of used-color panel operations and history labelling."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator.canvas import PaintCanvas
from paintmaskanimator.canvas_undo import history_label_for
from paintmaskanimator.color_panel import UsedColorPanel


def _app():
    return QApplication.instance() or QApplication([])


def _wire(panel, canvas):
    """Mimic MainWindow: route palette history pushes onto the canvas stack."""
    canvas._palette = panel

    def push(before, label):
        canvas.undo_stack.append(("palette_state", before, label))
        canvas.redo_stack.clear()

    panel.historyStatePush.connect(push)


def _panel_with_colors():
    panel = UsedColorPanel()
    panel.set_colors([QColor("red"), QColor("green"), QColor("blue")])
    return panel


def test_reorder_is_undoable():
    _app()
    canvas = PaintCanvas()
    panel = _panel_with_colors()
    _wire(panel, canvas)

    before_order = [panel._rgb_key(c) for c in panel.colors]
    red, blue = (255, 0, 0), (0, 0, 255)
    # Move red after blue.
    panel._handle_color_drop(red, blue, "after")
    after_order = [panel._rgb_key(c) for c in panel.colors]

    assert after_order != before_order
    assert canvas.undo_stack[-1][0] == "palette_state"

    canvas.undo()
    assert [panel._rgb_key(c) for c in panel.colors] == before_order

    canvas.redo()
    assert [panel._rgb_key(c) for c in panel.colors] == after_order


def test_parent_child_group_is_undoable():
    _app()
    canvas = PaintCanvas()
    panel = _panel_with_colors()
    _wire(panel, canvas)

    red, blue = (255, 0, 0), (0, 0, 255)
    assert panel.child_to_parent == {}
    panel._handle_color_drop(red, blue, "child")
    assert panel.child_to_parent.get(red) == blue

    canvas.undo()
    assert panel.child_to_parent == {}

    canvas.redo()
    assert panel.child_to_parent.get(red) == blue


def test_visibility_toggle_is_undoable():
    _app()
    canvas = PaintCanvas()
    panel = _panel_with_colors()
    _wire(panel, canvas)

    red = (255, 0, 0)
    assert panel.enabled_colors.get(red, True) is True
    panel._set_color_visible(red, False)
    assert panel.enabled_colors[red] is False

    canvas.undo()
    assert panel.enabled_colors[red] is True

    canvas.redo()
    assert panel.enabled_colors[red] is False


def test_no_history_entry_when_state_unchanged():
    _app()
    canvas = PaintCanvas()
    panel = _panel_with_colors()
    _wire(panel, canvas)

    # Dropping a color onto itself must not create a history entry.
    red = (255, 0, 0)
    panel._handle_color_drop(red, red, "after")
    assert not canvas.undo_stack


def test_history_labels():
    assert history_label_for(("layer_region", 0, 0)) == "描画"
    assert history_label_for(("palette_state", {}, "使用色の並べ替え")) == "使用色の並べ替え"
    assert history_label_for(("layer_batch", 0, [])) == "色編集"
