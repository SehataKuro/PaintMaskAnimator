"""Drawing within an exposure edits its original cell without splitting it."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator.canvas import PaintCanvas
from paintmaskanimator.models import make_frame


@pytest.mark.parametrize("column", [1, 3])
@pytest.mark.parametrize("blank", [False, True])
def test_edit_held_cell_keeps_exposure_and_undo_target(column, blank):
    app = QApplication.instance() or QApplication([])
    canvas = PaintCanvas()
    names = [layer.name for layer in canvas.layers]
    canvas.frames = [make_frame(names) for _ in range(5)]
    source = canvas.frames[0].layers[0]
    source.has_content = not blank
    source.is_blank_key = blank
    source.exposure = 4
    source.sequence_number = 7
    following = canvas.frames[4].layers[0]
    following.has_content = True
    following.sequence_number = 8
    canvas.current_frame = column
    canvas.active_layer_index = 0

    canvas.ensure_editable_key()
    assert canvas.active_layer is source
    assert source.exposure == 4
    assert source.sequence_number == 7
    assert source.has_content
    assert not source.is_blank_key
    assert not any(canvas.frames[i].layers[0].has_content for i in range(1, 4))

    before = source.image.pixelColor(10, 10)
    canvas.push_layer_undo()
    source.image.setPixelColor(10, 10, QColor("red"))
    canvas.undo()
    assert canvas.frames[0].layers[0].image.pixelColor(10, 10) == before
    canvas.redo()
    assert canvas.frames[0].layers[0].image.pixelColor(10, 10) == QColor("red")
    assert canvas.frames[0].layers[0].exposure == 4
    assert canvas.frames[4].layers[0] is following
    canvas.close()
    app.processEvents()
