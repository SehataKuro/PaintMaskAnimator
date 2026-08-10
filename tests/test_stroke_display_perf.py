"""Brush strokes on a large canvas must not re-transform the whole layer.

Regression guard for the incremental stroke-display buffer: during a brush
stroke the active layer's white->transparent display image is patched only in
the stamped region, so the per-stamp cost is independent of canvas size. The
patched buffer must stay pixel-identical to a full recompute.
"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


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
    # Prime the display cache like an idle repaint would.
    cached = canvas._pseudo_transparent_display_image(canvas.active_layer.image)
    canvas._begin_opaque_brush_stroke()
    # The warm display buffer is moved into stroke ownership, not copied.
    assert canvas._stroke_display_image is cached
    assert canvas._pseudo_transparency_key(
        canvas.active_layer.image
    ) not in canvas._pseudo_transparency_cache
    points = [QPointF(250 + i * 60, 250 + i * 45) for i in range(12)]
    for i in range(1, len(points)):
        canvas.draw_line(points[i - 1], points[i], 1.0)
    return idx


def test_stroke_display_buffer_matches_full_recompute(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from paintmaskanimator import constants
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 1400, raising=False)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 1400, raising=False)

    from paintmaskanimator.main_window import MainWindow
    window = MainWindow()
    try:
        canvas = window.canvas
        assert canvas._stroke_display_eligible()
        idx = _draw_test_stroke(canvas)

        # During the stroke the display path returns the incremental buffer.
        assert canvas._stroke_display_image is not None
        assert (
            canvas._display_layer_image(canvas.active_layer, idx)
            is canvas._stroke_display_image
        )
        patched = _rgba(canvas._stroke_display_image.copy())

        canvas._finish_opaque_brush_stroke()
        assert canvas._stroke_display_image is None

        # Ground truth: a fresh full white->transparent transform.
        canvas._pseudo_transparency_cache.clear()
        truth = _rgba(
            canvas._pseudo_transparent_display_image(canvas.active_layer.image)
        )
        assert np.array_equal(patched, truth)
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
        assert entry[0] == "layer_tiles"
        tiles = entry[3]
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
        window.deleteLater()
        qapp.processEvents()


def test_tiled_snapshot_keeps_stroke_opacity_stable(qapp, monkeypatch, tmp_path):
    """Overlapping stamps blend against the pre-stroke tile, not each other."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from paintmaskanimator import constants
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 512, raising=False)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 512, raising=False)

    from paintmaskanimator.main_window import MainWindow
    window = MainWindow()
    try:
        canvas = window.canvas
        canvas.active_layer_index = next(
            i for i, layer in enumerate(canvas.layers)
            if not getattr(layer, "is_paper", False)
        )
        canvas.main_color = QColor("#0000ff")
        canvas.color_mode = "main"
        canvas.pen_size = 20.0
        canvas.pen_opacity = 0.5
        window.tools.opacity_enabled.setChecked(True)
        start = QPointF(190, 200)
        end = QPointF(210, 200)
        canvas._pseudo_transparent_display_image(canvas.active_layer.image)
        canvas._begin_opaque_brush_stroke()
        canvas.draw_line(start, end, 1.0)
        once = canvas.active_layer.image.pixelColor(200, 200)
        canvas.draw_line(start, end, 1.0)
        twice = canvas.active_layer.image.pixelColor(200, 200)
        canvas._finish_opaque_brush_stroke()

        assert once == twice
        assert (once.red(), once.green(), once.blue()) == (128, 128, 255)
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
