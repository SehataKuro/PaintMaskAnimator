import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.canvas import PaintCanvas  # noqa: E402
from paintmaskanimator.models import make_frame  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_playback_key_map_respects_exposure_and_gaps(qapp):
    canvas = PaintCanvas()
    canvas.frames = [make_frame() for _ in range(5)]
    first = canvas.frames[0].layers[0]
    first.has_content = True
    first.exposure = 3
    fourth = canvas.frames[3].layers[0]
    fourth.has_content = True
    fourth.exposure = 1

    canvas._build_playback_key_map()

    assert canvas._playback_resolved_keys == [[0], [0], [0], [3], [None]]


def test_playback_advance_wraps_at_document_end(qapp):
    canvas = PaintCanvas()
    canvas.frames = [make_frame() for _ in range(3)]
    canvas.current_frame = 2

    canvas.playback_advance()

    assert canvas.current_frame == 0
