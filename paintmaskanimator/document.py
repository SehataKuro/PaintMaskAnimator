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
from .utils import blank_image


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


def clear_slot(layer, *, blank=False):
    """Clear a legacy slot, detaching before touching its image payload.

    This deliberately retains detach_cell's initial-image copy semantics.
    It neither archives the old drawing nor changes any other slot.
    """
    layer.detach_cell()
    layer.image = blank_image()
    layer.has_content = False
    layer.is_blank_key = blank
    layer.exposure = 1
    layer.sequence_number = None
    layer.sequence_only = False
    layer.cell_name = None
    layer.tween = None
    layer.tween_member = None


def create_drawing(layer, image, *, number):
    """Install a new image in an already prepared slot; caller chooses number.

    No detach, clearing, copying, numbering policy or undo is implicit here.
    Passing None preserves operations that currently leave numbering to refresh.
    """
    layer.image = image
    layer.has_content = True
    layer.exposure = 1
    layer.sequence_number = number


def place_number(target, source, number):
    """Recall a drawing with the legacy recall operation's exact copy policy.

    coalesce/sync remain responsible for Cell sharing, as before. The image
    object is reused, while initial_image comes from an independent clone.
    """
    copied = source.clone()
    target.detach_cell()
    target.image = source.image
    target.initial_image = copied.initial_image
    target.has_content = bool(copied.has_content)
    target.is_blank_key = not bool(copied.has_content)
    target.sequence_number = int(number)
    target.cell_name = getattr(copied, "cell_name", None)
    target.sequence_only = False
    target.exposure = 1


def put_archive(archive, key, layer):
    """Store the supplied legacy Layer; copying policy belongs to the caller."""
    archive[key] = layer


def remove_archive(archive, key):
    """Remove an archive entry with the existing missing-key tolerance."""
    return archive.pop(key, None)


def apply_number_mapping(frames, archive, layer_index, mapping, *,
                         unnumbered_start=None, normalize_archive_keys=False):
    """Apply a precomputed number map in frame order, then archive order.

    Normalization also numbers unnumbered content keys and coerces archive
    keys to int. Insertion keeps unrelated keys and metadata untouched.
    Images, Cell sharing and Undo are deliberately outside this operation.
    """
    next_number = unnumbered_start
    for frame in frames:
        layer = frame.layers[layer_index]
        if layer.sequence_number is not None:
            old = int(layer.sequence_number)
            if normalize_archive_keys or old in mapping:
                layer.sequence_number = mapping[old]
        elif next_number is not None and layer.has_content and not layer.sequence_only:
            layer.sequence_number = next_number
            next_number += 1
    result = {}
    for (li, number), archived in archive.items():
        matches = int(li) == layer_index if normalize_archive_keys else li == layer_index
        old = int(number)
        if matches and (normalize_archive_keys or old in mapping):
            number = mapping.get(old, old)
            archived.sequence_number = number
        else:
            number = old
        result[(int(li) if normalize_archive_keys else li, number)] = archived
    return result


def apply_numbered_images(frames, layer_index, source_for_number, snapshots):
    """Apply sequence-image movement using its existing per-slot image copies."""
    for frame in frames:
        layer = frame.layers[layer_index]
        number = layer.sequence_number
        if number in source_for_number:
            image, has_content, is_blank, cell_name, initial_image = snapshots[
                source_for_number[number]
            ]
            layer.image = image.copy()
            layer.has_content = has_content
            layer.is_blank_key = is_blank
            layer.cell_name = cell_name
            layer.initial_image = QImage(initial_image) if initial_image is not None else None


def assign_numbers_to_unnumbered(frames, layer_index):
    """Use refresh's content-key maximum and column order, including paper/draft."""
    content_columns = [
        column for column in range(len(frames))
        if frames[column].layers[layer_index].has_content
    ]
    used_numbers = [
        int(frames[column].layers[layer_index].sequence_number)
        for column in content_columns
        if frames[column].layers[layer_index].sequence_number is not None
    ]
    next_number = max(used_numbers, default=0) + 1
    for column in content_columns:
        layer = frames[column].layers[layer_index]
        if layer.sequence_number is None:
            layer.sequence_number = next_number
            next_number += 1


def project_v1_state(frames, sequence_archive=None):
    """Expose the existing storage unchanged to build_v1_payload."""
    return frames, sequence_archive
