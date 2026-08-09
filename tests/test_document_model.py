"""Unit tests for the extracted Document model — exercised WITHOUT the
PaintCanvas widget, which is the whole point of the extraction."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from paintmaskanimator import constants  # noqa: E402
from paintmaskanimator.document import Document  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc(qapp):
    return Document()


def test_initial_state(doc):
    assert len(doc.frames) == 1
    assert doc.current_frame == 0
    assert doc.active_layer_index == 0
    assert doc.layers is doc.frames[0].layers
    assert doc.active_layer is doc.layers[0]


def test_snapshot_records_canvas_size(doc):
    snap = doc.snapshot()
    assert snap[3] == constants.CANVAS_WIDTH
    assert snap[4] == constants.CANVAS_HEIGHT


def test_snapshot_is_independent_deep_copy(doc):
    snap = doc.snapshot()
    doc.frames.append(doc.frames[0].clone())      # mutate live state
    frames_in_snap = snap[0]
    assert len(frames_in_snap) == 1               # snapshot unaffected


def test_restore_roundtrip(doc):
    doc.frames.append(doc.frames[0].clone())
    doc.current_frame = 1
    snap = doc.snapshot()

    doc.frames = [doc.frames[0]]                   # diverge
    doc.current_frame = 0

    w, h = doc.restore(snap)
    assert len(doc.frames) == 2
    assert doc.current_frame == 1
    assert (w, h) == (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)


def test_restore_returns_snapshot_dimensions(doc):
    snap = doc.snapshot()
    snap = (snap[0], snap[1], snap[2], 800, 600)
    w, h = doc.restore(snap)
    assert (w, h) == (800, 600)
