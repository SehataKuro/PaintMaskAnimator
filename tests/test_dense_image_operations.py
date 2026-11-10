"""Resize must retain the sparse save payload alongside dense shared drawings."""
import pytest
from PySide6.QtGui import QColor, QImage

from paintmaskanimator import constants
from paintmaskanimator.document import project_v1_state
from paintmaskanimator.legacy_v1 import raw_v1_slot
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.models import Cell, Frame


def image(color):
    result = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(color))
    return result


@pytest.mark.parametrize("kind", [{}, {"is_draft": True}, {"is_paper": True}])
def test_resize_dense_holds_hidden_payload_and_unplaced_bank(qtbot, monkeypatch, kind):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 4)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 4)
    window = MainWindow()
    qtbot.addWidget(window)
    canvas = window.canvas
    canvas.install_legacy_frames([
        Frame([raw_v1_slot("A", image("red"), has_content=True, sequence_number=1,
                           exposure=3, initial_image=image("black"), **kind)]),
        Frame([raw_v1_slot("A", image("cyan"), initial_image=image("green"), **kind)]),
        Frame([raw_v1_slot("A", image("transparent"), **kind)]),
    ], {(0, 8): raw_v1_slot("A", image("blue"), has_content=True,
                            sequence_number=8, initial_image=image("yellow"), **kind)})
    canvas._document.tracks[0].cells[9] = Cell(image("magenta"), image("white"), "unplaced")

    def observe():
        projected, archive = project_v1_state(canvas.frames)
        return ([(QImage(f.layers[0].image), QImage(f.layers[0].initial_image)
                  if f.layers[0].initial_image is not None else None) for f in projected],
                {n: (QImage(c.image), QImage(c.initial_image), c.cell_name)
                 for n, c in canvas._document.tracks[0].cells.items()},
                sorted(archive))

    before = observe()
    canvas.push_doc_undo()
    window.replace_doc(6, 7, preserve=True)
    projected, archive = project_v1_state(canvas.frames)
    assert [f.layers[0].image.size().toTuple() for f in projected] == [(6, 7)] * 3
    assert projected[1].layers[0].initial_image == image("green")
    assert archive[0, 8].image == image("blue")
    assert archive[0, 9].image == image("magenta")
    assert canvas.frames[0].layers[0]._cell is canvas.frames[2].layers[0]._cell
    after = observe()
    canvas.undo()
    assert observe() == before
    canvas.redo()
    assert observe() == after
