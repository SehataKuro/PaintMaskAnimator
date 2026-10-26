"""Pixel cursor and click/resize gestures share the brush rasterizer."""
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QApplication

from paintmaskanimator.brush_raster import stamp_mask, stamp_outline
from paintmaskanimator.canvas import PaintCanvas


@pytest.fixture
def canvas():
    app = QApplication.instance() or QApplication([])
    widget = PaintCanvas()
    widget.zoom = 1
    widget.pan = QPointF(0, 0)
    widget.rotation = 0
    widget.active_layer_index = next(i for i, layer in enumerate(widget.frames[0].layers) if not layer.is_paper)
    widget.set_tool('brush')
    widget.main_color = QColor('red')
    widget.pressure_enabled = False
    yield widget
    widget.close()
    app.processEvents()


def mouse(canvas, kind, point, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QMouseEvent(kind, point, point, Qt.MouseButton.LeftButton,
                        Qt.MouseButton.NoButton if kind == QMouseEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton,
                        modifiers)
    QApplication.sendEvent(canvas, event)


@pytest.mark.parametrize('size', [0.5, 1, 1.5, 2, 8])
@pytest.mark.parametrize('phase', [0, 0.25, 0.5, 0.75])
def test_click_paints_immediately_and_matches_pixel_preview(canvas, size, phase):
    canvas.set_pen_size(size)
    point = QPointF(20 + phase, 20 + phase)
    mask, rect = stamp_mask(point, size)
    outline = stamp_outline(point, size)
    mouse(canvas, QMouseEvent.Type.MouseButtonPress, point)
    painted = 0
    for y in range(rect.height()):
        for x in range(rect.width()):
            expected = mask.pixelColor(x, y).alpha() != 0
            pixel_center = QPointF(rect.x() + x + .5, rect.y() + y + .5)
            assert outline.contains(pixel_center) == expected
            assert (canvas.active_layer.image.pixelColor(rect.x() + x, rect.y() + y).alpha() != 0) == expected
            painted += expected
    assert painted > 0
    mouse(canvas, QMouseEvent.Type.MouseButtonRelease, point)
    assert len(canvas.undo_stack) == 1
    canvas.undo()
    for y in range(rect.height()):
        for x in range(rect.width()):
            assert canvas.active_layer.image.pixelColor(rect.x() + x, rect.y() + y).alpha() == 0


def test_ctrl_alt_drag_changes_size_without_painting(canvas):
    canvas.set_pen_size(1.5)
    modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    mouse(canvas, QMouseEvent.Type.MouseButtonPress, QPointF(20, 20), modifiers)
    mouse(canvas, QMouseEvent.Type.MouseMove, QPointF(40, 20), modifiers)
    assert canvas.pen_size == 3.5
    assert not canvas.drawing
    assert not canvas.active_layer.has_content
    # Releasing modifiers before the mouse must still finish resizing, without a stroke.
    mouse(canvas, QMouseEvent.Type.MouseButtonRelease, QPointF(40, 20))
    assert canvas._brush_resize_anchor is None
    mouse(canvas, QMouseEvent.Type.MouseButtonPress, QPointF(20, 20), modifiers)
    mouse(canvas, QMouseEvent.Type.MouseButtonRelease, QPointF(-1000, 20), modifiers)
    assert canvas.pen_size == .5
    assert not canvas.active_layer.has_content
