"""Qt-independent document model.

Owns the *what* of a project — the list of frames, which frame is current,
and which layer is active — independent of any widget, event loop, or
rendering. The PaintCanvas widget delegates its state to a Document instance
so that this core model can be reasoned about and tested without a GUI.

Frames/Layers still carry QImage payloads (a Qt data type, not a widget); the
constraint here is *UI independence*, not total Qt independence.
"""
from . import constants
from PySide6.QtGui import QImage

from .models import Cell, Frame, make_frame


class Document:
    """Holds project state: frames + the current frame / active layer cursor."""

    def __init__(self):
        self.frames = [make_frame()]
        self.current_frame = 0
        self.active_layer_index = 0

    @property
    def layers(self):
        """Layers of the current frame."""
        return self.frames[self.current_frame].layers

    @property
    def active_layer(self):
        return self.layers[self.active_layer_index]

    def clone_graph(self, sequence_archive=None):
        """Copy frames and external archive together, preserving sharing.

        Identity memoization deliberately does not reconcile distinct Cells
        with the same number. Copying must preserve legacy conflict handling.
        The archive stays external to Document and its undo snapshots.
        """
        copied = object.__new__(Document)
        copied.frames, archive = clone_frame_graph(self.frames, sequence_archive)
        copied.current_frame = self.current_frame
        copied.active_layer_index = self.active_layer_index
        return copied, archive

    def snapshot(self):
        """Deep copy of the whole document state (used by undo/redo)."""
        return (
            [f.clone() for f in self.frames],
            self.current_frame,
            self.active_layer_index,
            constants.CANVAS_WIDTH,
            constants.CANVAS_HEIGHT,
        )

    def restore(self, snap):
        """Restore state produced by :meth:`snapshot`.

        Returns the (width, height) recorded in the snapshot so the caller can
        apply any canvas-size side effects it owns.
        """
        frames, current_frame, active_layer_index, width, height = snap
        self.frames = [f.clone() for f in frames]
        self.current_frame = current_frame
        self.active_layer_index = active_layer_index
        return width, height


def clone_frame_graph(frames, sequence_archive=None):
    """Copy frame/archive payloads with one memo shared across both."""
    cells, tracks = {}, {}

    def clone_layer(layer):
        copied = layer.clone_slot()
        cell = layer._cell
        if id(cell) not in cells:
            cells[id(cell)] = Cell(
                QImage(cell.image),
                QImage(cell.initial_image)
                if cell.initial_image is not None else None,
                cell.cell_name,
            )
        track = layer._track
        if id(track) not in tracks:
            tracks[id(track)] = track.copy_settings()
        copied.__dict__["_cell"] = cells[id(cell)]
        copied.__dict__["_track"] = tracks[id(track)]
        return copied

    copied_frames = [
        Frame([clone_layer(layer) for layer in frame.layers], frame.duration)
        for frame in frames
    ]
    archive = {
        key: clone_layer(layer)
        for key, layer in (sequence_archive or {}).items()
    }
    return copied_frames, archive
