"""Characterization tests for PaintCanvas' document model and algorithms.

These lock in the *current* observable behavior before the UI/domain
separation refactor. They are not specifications of ideal behavior — they
exist so that extracting the document model and algorithms out of the
PaintCanvas widget cannot silently change what the app does.

Run headless:  QT_QPA_PLATFORM=offscreen python -m pytest tests/
"""
import hashlib
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from paintmaskanimator.canvas import PaintCanvas  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def canvas(qapp):
    return PaintCanvas()


def _img_hash(img):
    return hashlib.sha256(img.constBits().tobytes()).hexdigest()


# --- initial state ---------------------------------------------------------

def test_initial_document_state(canvas):
    assert len(canvas.frames) == 1
    assert len(canvas.layers) == 1
    assert canvas.current_frame == 0
    assert canvas.active_layer_index == 0


def test_document_snapshot_shape(canvas):
    snap = canvas.document_snapshot()
    assert len(snap) == 5
    frames, cf, ali, w, h = snap
    assert isinstance(frames, list)
    assert (cf, ali) == (0, 0)
    assert (w, h) == (canvas.frames and 1280, 720)


# --- layer add / delete + undo/redo ---------------------------------------

def test_add_layer_then_undo_redo(canvas):
    assert len(canvas.layers) == 1
    canvas.add_layer()
    assert len(canvas.layers) == 2
    assert canvas.active_layer_index == 1
    canvas.undo()
    assert len(canvas.layers) == 1
    assert canvas.active_layer_index == 0
    canvas.redo()
    assert len(canvas.layers) == 2


def test_add_layer_applies_to_all_frames(canvas):
    canvas.add_frame(dup=True)       # 2 frames
    canvas.add_layer()               # layer added to every frame
    assert all(len(f.layers) == 2 for f in canvas.frames)


def test_delete_layer_undo_restores(canvas):
    canvas.add_layer()
    before = len(canvas.layers)
    canvas.delete_layer()
    assert len(canvas.layers) == before - 1
    canvas.undo()
    assert len(canvas.layers) == before


# --- frame add / delete ----------------------------------------------------

def test_add_frame_duplicate_grows_and_moves_current(canvas):
    canvas.add_frame(dup=True)
    assert len(canvas.frames) == 2
    assert canvas.current_frame == 1
    canvas.add_frame(dup=True)
    assert len(canvas.frames) == 3
    assert canvas.current_frame == 2


def test_add_frame_undo_redo(canvas):
    canvas.add_frame(dup=True)
    canvas.add_frame(dup=True)
    assert len(canvas.frames) == 3
    canvas.undo()
    assert len(canvas.frames) == 2
    canvas.redo()
    assert len(canvas.frames) == 3


def test_delete_frame_keeps_at_least_one(canvas):
    canvas.add_frame(dup=True)
    canvas.add_frame(dup=True)
    assert len(canvas.frames) == 3
    canvas.delete_frame()
    # delete_frame removes the current exposure block and never drops below 1
    assert 1 <= len(canvas.frames) < 3


# --- composite (rendering algorithm) golden --------------------------------

def test_composite_dimensions(canvas):
    img = canvas.composite(0)
    assert (img.width(), img.height()) == (1280, 720)


def test_composite_blank_is_stable(canvas):
    """A blank document composites to a deterministic image."""
    h1 = _img_hash(canvas.composite(0, white=True))
    h2 = _img_hash(canvas.composite(0, white=True))
    assert h1 == h2


def test_composite_white_vs_transparent_background_differ(canvas):
    hw = _img_hash(canvas.composite(0, white=True))
    ht = _img_hash(canvas.composite(0, white=False))
    assert hw != ht


# --- snapshot round trip ---------------------------------------------------

def test_snapshot_restore_roundtrip(canvas):
    canvas.add_frame(dup=True)
    canvas.add_layer()
    frames_before = len(canvas.frames)
    layers_before = len(canvas.layers)
    snap = canvas.document_snapshot()

    # mutate away from the snapshot
    canvas.delete_frame()
    assert len(canvas.frames) != frames_before

    # restoring the snapshot's frame list reproduces the structure
    restored_frames, cf, ali, _, _ = snap
    canvas.frames = [f.clone() for f in restored_frames]
    canvas.current_frame = cf
    canvas.active_layer_index = ali
    assert len(canvas.frames) == frames_before
    assert len(canvas.layers) == layers_before
