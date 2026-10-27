"""Pixel cursor and click/resize gestures share the brush rasterizer."""
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
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


def test_outline_cache_preserves_exact_subpixel_phase():
    from paintmaskanimator.brush_raster import _local_outline
    _local_outline.cache_clear()
    first = stamp_outline(QPointF(20.25, 20.5), 1.5)
    second = stamp_outline(QPointF(21.25, 20.5), 1.5)
    assert _local_outline.cache_info().hits == 1
    first.translate(1, 0)
    assert first == second
    stamp_outline(QPointF(21.5, 20.5), 1.5)
    assert _local_outline.cache_info().misses == 2


@pytest.mark.parametrize('zoom', [10, 20])
@pytest.mark.parametrize('rotation', [0, 45, 90])
@pytest.mark.parametrize('size', [0.5, 1, 1.5, 8])
def test_hover_damage_contains_snapped_pixel_outlines(canvas, monkeypatch, zoom, rotation, size):
    canvas.zoom = zoom
    canvas.rotation = rotation
    canvas.set_pen_size(size)
    old = canvas.canvas_to_widget(QPointF(20.05, 20.05))
    new = canvas.canvas_to_widget(QPointF(23.95, 23.95))
    canvas._brush_cursor_widget_pos = old
    updates = []
    monkeypatch.setattr(canvas, 'update', lambda *args: updates.append(args))
    event = QMouseEvent(
        QMouseEvent.Type.MouseMove, new, new,
        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    canvas.mouseMoveEvent(event)
    damage = next(args[0] for args in updates if args)
    for position in (old, new):
        outline = stamp_outline(canvas.widget_to_canvas(position), size)
        for polygon in outline.toSubpathPolygons():
            for point in polygon:
                widget_point = canvas.canvas_to_widget(point)
                # Include the cosmetic cursor pen as well as its geometry.
                assert QRectF(damage).contains(QRectF(
                    widget_point.x() - 0.5, widget_point.y() - 0.5, 1, 1,
                ))
