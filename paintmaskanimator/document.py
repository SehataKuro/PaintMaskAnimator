"""Qt-independent document model.

Owns the *what* of a project — the list of frames, which frame is current,
and which layer is active — independent of any widget, event loop, or
rendering. The PaintCanvas widget delegates its state to a Document instance
so that this core model can be reasoned about and tested without a GUI.

Frames/Layers still carry QImage payloads (a Qt data type, not a widget); the
constraint here is *UI independence*, not total Qt independence.
"""
from . import constants
from .models import make_frame


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
