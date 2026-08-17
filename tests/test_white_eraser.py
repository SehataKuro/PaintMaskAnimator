"""White (#FFFFFF) strokes act as a true eraser (alpha=0), not opaque paint.

Issue #15: painting/filling with pure white must not bake white pixels into the
layer. Instead the touched pixels are cleared to alpha=0 so the layers below
remain intact and show through.
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
    rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
        (im.height(), im.bytesPerLine())
    )
    return rows[:, : im.width() * 4].reshape((im.height(), im.width(), 4)).copy()


def _paintable_layer_index(canvas):
    return next(
        (
            i
            for i, layer in enumerate(canvas.frames[canvas.current_frame].layers)
            if not getattr(layer, "is_paper", False)
        ),
        0,
    )


def test_white_brush_clears_to_transparent(qapp):
    from paintmaskanimator.canvas import PaintCanvas

    canvas = PaintCanvas()
    canvas.active_layer_index = _paintable_layer_index(canvas)
    canvas.set_tool("brush")
    canvas.pen_size = 20.0
    canvas.color_mode = "main"

    # First lay down opaque blue, then erase part of it with white.
    canvas.main_color = QColor("#3355ff")
    canvas.ensure_editable_key()
    canvas._begin_opaque_brush_stroke()
    canvas.draw_line(QPointF(40, 40), QPointF(160, 160), 1.0)

    blue = _rgba(canvas.active_layer.image)
    assert np.any(blue[:, :, 3] == 255), "blue stroke should be opaque"

    canvas.main_color = QColor("#ffffff")
    canvas._begin_opaque_brush_stroke()
    canvas.draw_line(QPointF(40, 40), QPointF(160, 160), 1.0)

    after = _rgba(canvas.active_layer.image)
    # No pure-white opaque pixels are baked into the layer.
    baked_white = (
        (after[:, :, 3] == 255)
        & np.all(after[:, :, :3] == 255, axis=2)
    )
    assert not np.any(baked_white), "white must not be written as opaque paint"
    # The erased path is now transparent, not blue.
    assert np.any(after[:, :, 3] == 0)


def test_white_bucket_fill_clears_region(qapp):
    from paintmaskanimator.canvas import PaintCanvas

    canvas = PaintCanvas()
    canvas.active_layer_index = _paintable_layer_index(canvas)

    # Fill an area with an opaque color first.
    canvas.set_tool("bucket")
    canvas.main_color = QColor("#22aa44")
    canvas.color_mode = "main"
    canvas.ensure_editable_key()
    canvas.flood_fill(QPointF(10, 10))
    filled = _rgba(canvas.active_layer.image)
    assert np.any(filled[:, :, 3] == 255)

    # Now bucket white over it -> region becomes transparent, not white.
    canvas.main_color = QColor("#ffffff")
    canvas.flood_fill(QPointF(10, 10))
    after = _rgba(canvas.active_layer.image)
    baked_white = (
        (after[:, :, 3] == 255)
        & np.all(after[:, :, :3] == 255, axis=2)
    )
    assert not np.any(baked_white)
    assert np.all(after[:, :, 3] == 0)
