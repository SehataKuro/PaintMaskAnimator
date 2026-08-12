"""Regression tests: creating a brand-new document must not leak transient
interaction state (selection / transform / playback) from the previous one.

A stale selection polygon or a still-running playback timer references the
old frames and canvas size, which produced stale overlays and intermittent
errors on the freshly created document."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import constants  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_canvas_size():
    # CANVAS_WIDTH/HEIGHT are mutable module globals; replace_doc() rewrites them.
    # Restore them so a 64x64 test document does not leak into later tests.
    saved = (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
    try:
        yield
    finally:
        constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = saved


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    win = MainWindow()
    yield win
    win.deleteLater()


def test_new_document_clears_active_selection(window):
    canvas = window.canvas
    canvas.selection_polygon = [
        QPointF(0, 0),
        QPointF(constants.CANVAS_WIDTH, 0),
        QPointF(constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT),
    ]

    window.replace_doc(64, 64, preserve=False)

    assert canvas.selection_polygon == []
    assert canvas.transform_active is False
    assert canvas.selection_mask_override is None


def test_new_document_stops_playback(window):
    canvas = window.canvas
    window.play(True)
    assert canvas._playback_active is True
    assert window.timer.isActive() is True

    window.replace_doc(64, 64, preserve=False)

    assert canvas._playback_active is False
    assert window.timer.isActive() is False
    assert window.timeline.play.isChecked() is False


def test_new_document_starts_from_single_blank_frame(window):
    window.replace_doc(64, 64, preserve=False)

    canvas = window.canvas
    assert len(canvas.frames) == 1
    assert canvas.current_frame == 0
    assert canvas.active_layer_index == 0
    assert canvas.undo_stack == []
    assert canvas.redo_stack == []
    assert constants.CANVAS_WIDTH == 64
    assert constants.CANVAS_HEIGHT == 64
