"""Brush strokes draw straight into the layer image; no display-buffer detour.

Since #FFFFFF is a true eraser (alpha=0) rather than a pseudo-transparent
sentinel, the display image equals ``layer.image`` for a plain layer. There is
no white->transparent transform to amortize, so the brush blends directly into
the real layer pixels and the incremental display buffer is gone. These tests
guard the remaining contracts: strokes land in the layer, undo stores only the
touched tiles, and overlapping stamps keep a stable opacity.
"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.undo_entries import LayerTilesUndo  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _rgba(image):
    im = image.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = im.constBits()
    rows = np.frombuffer(ptr, dtype=np.uint8).reshape((im.height(), im.bytesPerLine()))
    return rows[:, : im.width() * 4].reshape((im.height(), im.width(), 4)).copy()


def _draw_test_stroke(canvas):
    idx = next(
        (i for i, layer in enumerate(canvas.frames[canvas.current_frame].layers)
         if not getattr(layer, "is_paper", False)),
        0,
    )
    canvas.active_layer_index = idx
    canvas.set_tool("brush")
    canvas.main_color = QColor("#3355ff")
    canvas.color_mode = "main"
    canvas.pen_size = 18.0
    canvas.ensure_editable_key()
    canvas._begin_opaque_brush_stroke()
    points = [QPointF(250 + i * 60, 250 + i * 45) for i in range(12)]
    for i in range(1, len(points)):
        canvas.draw_line(points[i - 1], points[i], 1.0)
    return idx


def test_brush_runtime_warmup_is_idempotent_and_non_mutating(qapp):
    from paintmaskanimator.canvas import PaintCanvas

    canvas = PaintCanvas()
    image_key = int(canvas.active_layer.image.cacheKey())
    undo_count = len(canvas.undo_stack)

    canvas.warm_up_brush_runtime()
    canvas.warm_up_brush_runtime()

    assert canvas._brush_runtime_warmed
    assert int(canvas.active_layer.image.cacheKey()) == image_key
    assert len(canvas.undo_stack) == undo_count
    assert not canvas.active_layer.has_content


def test_stroke_is_visible_through_layer_image(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from paintmaskanimator import constants
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 1400, raising=False)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 1400, raising=False)

    from paintmaskanimator.main_window import MainWindow
    window = MainWindow()
    try:
        canvas = window.canvas
        idx = _draw_test_stroke(canvas)

        # No detour buffer: the display path is the live layer image itself, so
        # in-progress stamps are already visible.
        assert (
            canvas._display_layer_image(canvas.active_layer, idx)
            is canvas.active_layer.image
        )
        painted = _rgba(canvas.active_layer.image)
        assert np.any(painted[:, :, 3] == 255)

        canvas._finish_opaque_brush_stroke()
        assert np.array_equal(_rgba(canvas.active_layer.image), painted)
    finally:
        window.close()
        window.deleteLater()
        window = None
        qapp.processEvents()
        qapp.processEvents()


def test_brush_undo_stores_only_touched_tiles(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from paintmaskanimator import constants
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 1400, raising=False)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 1400, raising=False)

    from paintmaskanimator.main_window import MainWindow
    window = MainWindow()
    try:
        canvas = window.canvas
        before = _rgba(canvas.active_layer.image)
        _draw_test_stroke(canvas)
        painted = _rgba(canvas.active_layer.image)
        canvas._finish_opaque_brush_stroke()

        entry = canvas.undo_stack[-1]
        assert isinstance(entry, LayerTilesUndo)
        tiles = entry.tiles
        assert tiles
        assert all(rect.width() <= 256 for rect, _image in tiles)
        assert all(rect.height() <= 256 for rect, _image in tiles)
        stored_pixels = sum(
            rect.width() * rect.height() for rect, _image in tiles
        )
        assert stored_pixels < (
            canvas.active_layer.image.width()
            * canvas.active_layer.image.height()
        )

        canvas.undo()
        assert np.array_equal(_rgba(canvas.active_layer.image), before)
        canvas.redo()
        assert np.array_equal(_rgba(canvas.active_layer.image), painted)
    finally:
        window.close()


def test_brush_is_always_opaque(qapp, monkeypatch, tmp_path):
    """The brush always writes 100% opaque pixels; there is no opacity setting."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from paintmaskanimator import constants
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 512, raising=False)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 512, raising=False)

    from paintmaskanimator.main_window import MainWindow
    window = MainWindow()
    try:
        canvas = window.canvas
        # The opacity feature has been removed entirely.
        assert not hasattr(canvas, "pen_opacity")
        assert not hasattr(canvas, "paint_opacity_value")
        assert not hasattr(window.tools, "opacity_enabled")
        canvas.active_layer_index = next(
            i for i, layer in enumerate(canvas.layers)
            if not getattr(layer, "is_paper", False)
        )
        canvas.main_color = QColor("#0000ff")
        canvas.color_mode = "main"
        canvas.pen_size = 20.0
        start = QPointF(190, 200)
        end = QPointF(210, 200)
        canvas._begin_opaque_brush_stroke()
        canvas.draw_line(start, end, 1.0)
        once = canvas.active_layer.image.pixelColor(200, 200)
        canvas.draw_line(start, end, 1.0)
        twice = canvas.active_layer.image.pixelColor(200, 200)
        canvas._finish_opaque_brush_stroke()

        assert once == twice
        assert (once.red(), once.green(), once.blue()) == (0, 0, 255)
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
