import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from paintmaskanimator.canvas import PaintCanvas
from paintmaskanimator.undo_entries import LayerRegionUndo, LayerUndo


def _app():
    return QApplication.instance() or QApplication([])


def test_region_undo_redo_restores_only_requested_area():
    _app()
    canvas = PaintCanvas()
    rect = QRect(100, 110, 24, 18)
    before = canvas.active_layer.image.copy(rect)
    assert canvas.push_layer_region_undo(rect)

    painter = QPainter(canvas.active_layer.image)
    painter.fillRect(rect, QColor("red"))
    painter.end()
    canvas.active_layer.has_content = True

    assert isinstance(canvas.undo_stack[-1], LayerRegionUndo)
    assert canvas.undo_stack[-1].image.size() == rect.size()
    canvas.undo()
    assert canvas.active_layer.image.copy(rect) == before
    canvas.redo()
    assert canvas.active_layer.image.pixelColor(rect.center()) == QColor("red")


def test_small_bucket_fill_replaces_full_snapshot_with_region():
    _app()
    canvas = PaintCanvas()
    source_rect = QRect(100, 100, 20, 16)
    painter = QPainter(canvas.active_layer.image)
    painter.fillRect(source_rect, QColor("red"))
    painter.end()
    canvas.active_layer.has_content = True
    canvas.main_color = QColor("blue")

    canvas.flood_fill(QPoint(105, 105))

    entry = canvas.undo_stack[-1]
    assert isinstance(entry, LayerRegionUndo)
    assert entry.rect == source_rect
    assert entry.image.size() == source_rect.size()
    canvas.undo()
    assert canvas.active_layer.image.pixelColor(105, 105) == QColor("red")
    canvas.redo()
    assert canvas.active_layer.image.pixelColor(105, 105) == QColor("blue")


def test_undo_stack_is_bounded_by_bytes_not_only_by_count(monkeypatch):
    """A few huge snapshots must be trimmed long before MAX_UNDO entries."""
    from paintmaskanimator import canvas_undo

    monkeypatch.setattr(canvas_undo, "MAX_UNDO", 30, raising=False)
    monkeypatch.setattr(canvas_undo, "MAX_UNDO_BYTES", 10_000, raising=False)

    stack = []
    newest = None
    for _ in range(5):
        newest = LayerUndo(
            0, 0, QImage(50, 50, QImage.Format.Format_ARGB32), True
        )
        stack.append(newest)
        canvas_undo.trim_undo_stack(stack)

    # 50*50*4 = 10000 bytes each, so only the newest entry fits.
    assert len(stack) == 1
    assert stack[-1] is newest


def test_undo_stack_always_keeps_one_entry_however_large(monkeypatch):
    from paintmaskanimator import canvas_undo

    monkeypatch.setattr(canvas_undo, "MAX_UNDO_BYTES", 1, raising=False)
    stack = [LayerUndo(0, 0, QImage(64, 64, QImage.Format.Format_ARGB32), True)]
    canvas_undo.trim_undo_stack(stack)
    assert len(stack) == 1


def test_full_layer_inverse_shares_pixels_but_survives_later_painting():
    _app()
    canvas = PaintCanvas()
    canvas.active_layer.image.fill(QColor("red"))
    entry = LayerUndo(0, 0, QImage(canvas.active_layer.image), True)
    inverse = canvas.current_state_for(entry)
    assert inverse.image.cacheKey() == canvas.active_layer.image.cacheKey()
    painter = QPainter(canvas.active_layer.image)
    painter.fillRect(QRect(0, 0, 2, 2), QColor("blue"))
    painter.end()
    assert inverse.image.pixelColor(0, 0) == QColor("red")
    assert canvas.active_layer.image.pixelColor(0, 0) == QColor("blue")


def test_controller_defers_palette_scan_until_after_repeated_undo_redo():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from paintmaskanimator.main_window_used_color import UsedColorController

    canvas = Mock(undo_stack=[object()], redo_stack=[object()])
    timer = Mock()
    window = SimpleNamespace(
        canvas=canvas, _used_color_timer=timer, _used_color_request=0,
        history_panel=Mock(),
    )
    controller = UsedColorController(window)
    controller.refresh = Mock()
    controller.undo()
    controller.undo()
    controller.redo()
    assert canvas.undo.call_count == 2
    assert canvas.redo.call_count == 1
    assert timer.start.call_count == 3
    assert window._used_color_request == 3
    controller.refresh.assert_not_called()
