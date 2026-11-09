"""Qt-independent document model.

Owns the *what* of a project — the list of frames, which frame is current,
and which layer is active — independent of any widget, event loop, or
rendering. The PaintCanvas widget delegates its state to a Document instance
so that this core model can be reasoned about and tested without a GUI.

Frames/Layers still carry QImage payloads (a Qt data type, not a widget); the
constraint here is *UI independence*, not total Qt independence.
"""
from typing import NamedTuple

from . import constants
from PySide6.QtGui import QImage

from .models import Cell, Frame, Layer, make_frame
from .utils import blank_image


class DocumentSnapshot(NamedTuple):
    frames: list
    current_frame: int
    active_layer_index: int
    width: int
    height: int
    tracks: list
    archive: dict


class SequenceArchive(dict):
    """Compatibility entries refer to the document's drawing bank."""

    def __init__(self, document, entries=()):
        self.document = document
        super().__init__()
        self.update(entries)

    def __setitem__(self, key, layer):
        li, number = map(int, key)
        if 0 <= li < len(self.document.tracks):
            track = self.document.tracks[li]
            if track.connected:
                cell = layer._cell
                track.cells.setdefault(number, cell)
                layer = layer.clone_slot()
                layer.sequence_number = number
                layer.__dict__["_track"] = track
                layer.__dict__["_archive_reference"] = True
                layer.__dict__.pop("_cell", None)
        super().__setitem__((li, number), layer)

    def update(self, entries=(), **kwargs):
        for key, value in dict(entries, **kwargs).items():
            self[key] = value


class Document:
    """Holds project state: frames + the current frame / active layer cursor."""

    def __init__(self):
        self.frames = [make_frame()]
        self.current_frame = 0
        self.active_layer_index = 0
        self.tracks = []
        self.sequence_archive = {}
        self.install_legacy_frames(self.frames)

    @property
    def sequence_archive(self):
        return self._sequence_archive

    @sequence_archive.setter
    def sequence_archive(self, entries):
        self._sequence_archive = (entries if isinstance(entries, SequenceArchive)
                                  and entries.document is self
                                  else SequenceArchive(self, entries))

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
        The compatibility archive and every unplaced Cell are copied together.
        """
        copied = object.__new__(Document)
        copied.frames, archive = clone_frame_graph(
            self.frames, self.sequence_archive if sequence_archive is None else sequence_archive)
        copied.current_frame = self.current_frame
        copied.active_layer_index = self.active_layer_index
        copied.tracks = [layer._track for layer in copied.frames[0].layers] if copied.frames else []
        copied.sequence_archive = archive
        return copied, archive

    def sync_columns(self):
        """Commit column structure and remap archive indices by Track identity."""
        old = self.tracks
        new = [layer._track for layer in self.frames[0].layers] if self.frames else []
        if len(old) == len(new) and all(a is b for a, b in zip(old, new)):
            return
        indices = {id(track): i for i, track in enumerate(new)}
        archive = {(indices[id(old[li])], n): layer
                   for (li, n), layer in self.sequence_archive.items()
                   if 0 <= li < len(old) and id(old[li]) in indices}
        self.tracks = new
        self.sequence_archive = archive

    def capture_column(self, index):
        frames = [Frame([frame.layers[index]], frame.duration) for frame in self.frames]
        archive = {(0, n): layer for (li, n), layer in self.sequence_archive.items()
                   if li == index}
        copied, archive = clone_frame_graph(frames, archive)
        return [frame.layers[0] for frame in copied], archive

    def install_legacy_frames(self, frames, sequence_archive=None):
        """Normalize detached legacy data once, before publishing it to the UI."""
        archive = sequence_archive if sequence_archive is not None else {}
        for li, first in enumerate(frames[0].layers if frames else []):
            slots = [frame.layers[li] for frame in frames]
            archived = [(int(n), layer) for (index, n), layer in archive.items()
                        if int(index) == li]
            numbered = {}
            for layer in slots:
                if layer.sequence_number is not None and (layer.has_content or layer.sequence_only):
                    numbered.setdefault(int(layer.sequence_number), []).append(layer)
            for number, layer in archived:
                numbered.setdefault(number, []).append(layer)
            retained = {}
            for layer in slots:
                if layer.sequence_number is not None:
                    retained.setdefault(int(layer.sequence_number), []).append(layer)
            for number, candidates in retained.items():
                numbered.setdefault(number, candidates)
            track = first._track if first._track.connected else first._track.copy_settings()
            for number, candidates in numbered.items():
                if number in track.cells:
                    continue
                content = [layer for layer in candidates if layer.has_content]
                owner = (content or candidates)[0]
                if 0 <= self.current_frame < len(slots):
                    preferred = slots[self.current_frame]
                    if any(preferred is layer for layer in content) and preferred.sequence_number == number:
                        owner = preferred
                attribute_sources = [layer for layer in slots
                                     if layer.has_content and layer.sequence_number == number]
                attribute_sources.extend(layer for n, layer in archived if n == number)
                if not attribute_sources:
                    attribute_sources = candidates
                attrs = []
                for name in ("initial_image", "cell_name"):
                    attrs.append(next((getattr(layer, name) for layer in attribute_sources
                                       if getattr(layer, name) is not None), None))
                rescued = not content and not any(layer.sequence_only for layer in candidates)
                track.cells[number] = Cell(
                    QImage(owner.image) if rescued else owner.image,
                    QImage(attrs[0]) if rescued and attrs[0] is not None else attrs[0],
                    attrs[1],
                )
            next_number = max(track.cells, default=0) + 1
            for layer in slots:
                if layer.has_content and layer.sequence_number is None:
                    cell = layer._cell
                    layer.sequence_number = next_number
                    track.cells[next_number] = cell
                    next_number += 1
            track.connected = True
            for layer in slots:
                layer.__dict__["_track"] = track
                if layer.has_content or layer.sequence_only:
                    layer.__dict__.pop("_cell", None)
            for number, layer in archived:
                layer.sequence_number = number
                layer.__dict__["_track"] = track
                layer.__dict__["_archive_reference"] = True
                layer.__dict__.pop("_cell", None)
        self.frames = frames
        self.tracks = [layer._track for layer in frames[0].layers] if frames else []
        self.sequence_archive = archive

    def snapshot(self):
        """Copy the entire drawing bank, including unplaced drawings."""
        frames, archive = clone_frame_graph(self.frames, self.sequence_archive)
        tracks = [layer._track for layer in frames[0].layers] if frames else []
        return DocumentSnapshot(frames, self.current_frame, self.active_layer_index,
                                constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT,
                                tracks, archive)

    def restore(self, snap):
        frames, current_frame, active_layer_index, width, height = snap[:5]
        self.frames, archive = clone_frame_graph(
            frames, getattr(snap, "archive", {}))
        self.tracks = [layer._track for layer in self.frames[0].layers] if self.frames else []
        self.sequence_archive = archive
        self.current_frame = current_frame
        self.active_layer_index = active_layer_index
        return width, height


def clone_frame_graph(frames, sequence_archive=None):
    """Copy frame/archive payloads with one memo shared across both."""
    cells, tracks = {}, {}

    def clone_cell(cell):
        if id(cell) not in cells:
            cells[id(cell)] = Cell(
                QImage(cell.image),
                QImage(cell.initial_image) if cell.initial_image is not None else None,
                cell.cell_name,
            )
        return cells[id(cell)]

    def clone_layer(layer):
        copied = layer.clone_slot()
        cell = layer._cell
        clone_cell(cell)
        track = layer._track
        if id(track) not in tracks:
            copied_track = tracks[id(track)] = track.copy_settings()
            copied_track.cells = {n: clone_cell(c) for n, c in track.cells.items()}
            copied_track.connected = track.connected
        copied.__dict__["_cell"] = cells[id(cell)]
        copied.__dict__["_track"] = tracks[id(track)]
        if (track.connected and layer.sequence_number is not None
                and (layer.has_content or layer.sequence_only
                     or layer.__dict__.get("_archive_reference", False))):
            copied.__dict__.pop("_cell", None)
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
    """Remove a sheet reference, retaining its drawing in the Track bank.

    Detached legacy slots keep their former initial-image copy semantics.
    """
    layer.detach_cell()
    layer.__dict__.pop("_content_origin", None)
    layer.sequence_number = None
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
    Passing None supports detached legacy construction before installation.
    """
    if layer._track.connected:
        if number is None:
            number = max(layer._track.cells, default=0) + 1
        previous = layer._cell
        layer._track.cells[int(number)] = Cell(
            image, QImage(previous.initial_image) if previous.initial_image is not None else None,
            previous.cell_name)
    layer.sequence_number = number
    layer.image = image
    layer.has_content = True
    layer.exposure = 1
    if layer._track.connected:
        layer.__dict__.pop("_cell", None)


def place_number(target, source, number):
    """Recall a drawing with the legacy recall operation's exact copy policy.

    coalesce/sync remain responsible for Cell sharing, as before. The image
    object is reused, while initial_image comes from an independent clone.
    """
    copied = source.clone()
    target.detach_cell()
    target.sequence_number = None
    target.image = source.image
    target.initial_image = copied.initial_image
    target.has_content = bool(copied.has_content)
    target.is_blank_key = not bool(copied.has_content)
    target.cell_name = getattr(copied, "cell_name", None)
    if target._track.connected:
        target._track.cells.setdefault(int(number), target._cell)
    target.sequence_number = int(number)
    target.sequence_only = False
    target.exposure = 1
    if target._track.connected:
        target.__dict__.pop("_cell", None)


def copy_drawing_reference(target, source, *, content=None, blank=None):
    """Replace a slot reference, registering a detached incoming drawing once."""
    content = bool(source.has_content) if content is None else bool(content)
    blank = bool(source.is_blank_key) if blank is None else bool(blank)
    number = source.sequence_number
    track = target._track
    copied = source.clone()
    clear_slot(target, blank=blank)
    target.__dict__["_cell"] = copied._cell
    if content or source.sequence_only:
        if track.connected:
            if number is None:
                number = max(track.cells, default=0) + 1
            track.cells.setdefault(int(number), copied._cell)
        target.sequence_number = number
    else:
        target.sequence_number = number
    target.has_content = content
    target.is_blank_key = blank
    target.sequence_only = bool(source.sequence_only)
    target.exposure = source.exposure
    if track.connected and (content or source.sequence_only):
        target.__dict__.pop("_cell", None)


def put_archive(archive, key, layer):
    """Store the supplied legacy Layer; copying policy belongs to the caller."""
    archive[key] = layer


def import_drawing_reference(frames, archive, fi, li, source, *, preferred_frame,
                             content=None, blank=None):
    """Place incoming artwork using legacy image-owner and attribute priority.

    Cross-column moves and clipboard pastes can collide with an existing
    number. The preferred content position owns the pixels, while each shared
    attribute comes from the first non-None sheet value, then the archive.
    """
    target = frames[fi].layers[li]
    copied = source.clone()
    number = copied.sequence_number
    content = copied.has_content if content is None else content
    if target._track.connected and number is not None and content:
        candidates = [copied if index == fi else frame.layers[li]
                      for index, frame in enumerate(frames)]
        matches = [layer for layer in candidates
                   if layer.has_content and layer.sequence_number == number]
        preferred = candidates[preferred_frame] if 0 <= preferred_frame < len(candidates) else None
        owner = preferred if any(layer is preferred for layer in matches) else matches[0]
        attrs = matches + [layer for (index, n), layer in archive.items()
                           if index == li and n == number]
        initial, name = (next((getattr(layer, attr) for layer in attrs
                              if getattr(layer, attr) is not None), None)
                         for attr in ("initial_image", "cell_name"))
        cell = target._track.cells.get(int(number))
        if cell is None:
            cell = target._track.cells[int(number)] = Cell()
        cell.image = QImage(owner.image)
        cell.initial_image = QImage(initial) if initial is not None else None
        cell.cell_name = name
    copy_drawing_reference(target, copied, content=content, blank=blank)


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
    track = frames[0].layers[layer_index]._track if frames else None
    if track is not None and track.connected:
        track.cells = {mapping.get(n, n): cell for n, cell in track.cells.items()}
    for frame in frames:
        layer = frame.layers[layer_index]
        if layer.sequence_number is not None:
            old = int(layer.sequence_number)
            if normalize_archive_keys or old in mapping:
                layer.sequence_number = mapping[old]
        elif next_number is not None and layer.has_content and not layer.sequence_only:
            cell = layer._cell
            if track is not None and track.connected:
                track.cells[next_number] = cell
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
    track = frames[0].layers[layer_index]._track if frames else None
    if track is not None and track.connected:
        old = dict(track.cells)
        track.cells.update({n: old[source] for n, source in source_for_number.items()})
    for frame in frames:
        layer = frame.layers[layer_index]
        number = layer.sequence_number
        if number in source_for_number:
            image, has_content, is_blank, cell_name, initial_image = snapshots[
                source_for_number[number]
            ]
            if track is None or not track.connected:
                layer.image = image.copy()
            layer.has_content = has_content
            layer.is_blank_key = is_blank
            if track is None or not track.connected:
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
    """Project unreferenced bank drawings to v1 archive without mutating live data."""
    archive = sequence_archive
    if not frames:
        return frames, archive
    for li, first in enumerate(frames[0].layers):
        track = first._track
        if not track.connected:
            continue
        represented = {int(frame.layers[li].sequence_number) for frame in frames
                       if frame.layers[li].sequence_number is not None
                       and (frame.layers[li].has_content or frame.layers[li].sequence_only)}
        represented.update(int(n) for index, n in (archive or {}) if int(index) == li)
        for number, cell in track.cells.items():
            if number in represented:
                continue
            layer = Layer(track.name, cell.image, has_content=True,
                          sequence_number=number)
            layer.__dict__["_track"] = track
            if archive is sequence_archive:
                archive = dict(sequence_archive or {})
            archive[(li, number)] = layer
    return frames, archive
