"""Qt-independent document model.

Owns the *what* of a project — the list of frames, which frame is current,
and which layer is active — independent of any widget, event loop, or
rendering. The PaintCanvas widget delegates its state to a Document instance
so that this core model can be reasoned about and tested without a GUI.

Frames/Layers still carry QImage payloads (a Qt data type, not a widget); the
constraint here is *UI independence*, not total Qt independence.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, NamedTuple, cast

from . import constants
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from .models import ArchiveHint, Cell, Frame, Layer, Track, make_frame
from .timeline_metadata import set_dense_import
from .utils import blank_image
from .legacy_v1 import (LegacyLayerV1, legacy_layer_to_slot, snapshot_layer,
                        expand_legacy, compress_v1, V1Position)


class PendingEntry(NamedTuple):
    """A pending position, preserving duplicates and an absent number."""

    column: int
    slot: Layer


def is_pending(slot: Layer) -> bool:
    """Read the legacy pending flag without resolving or creating artwork."""
    return bool(slot.sequence_only)


def mark_pending(slot: Layer):
    """Keep a position pending without changing its drawing reference."""
    slot.set_pending(True)


def clear_pending(slot: Layer):
    """Remove placement intent while retaining the slot and bank artwork."""
    slot.set_pending(False)


def release_slots(slots):
    """Release removed positions even when history or callers retain them."""
    for slot in slots:
        clear_pending(slot)


def replace_slot(frame, index, slot):
    """Install a position copy and release the overwritten pending token."""
    previous = frame.layers[index]
    track = previous._track
    if previous is slot:
        slot.attach_track(track)
        return
    clear_pending(previous)
    slot.attach_track(track)
    frame.layers[index] = slot


def drawing(track: Track, number: int) -> Cell | None:
    """Return bank artwork without creating a missing Cell."""
    return track.cells.get(number)


@dataclass
class DrawingTarget:
    """Borrowed drawing and optional sheet position for history restoration."""

    cell: Cell
    track: Track
    column: int | None
    slot: Layer | None
    number: int | None

    def __iter__(self):
        # History consumers also use the legacy (column, target) unpacking.
        yield self.column
        yield self

    @property
    def image(self):
        return self.cell.image

    @image.setter
    def image(self, value):
        self.cell.image = value

    def _archive_hint(self):
        if self.number is None:
            return ArchiveHint()
        return self.track.legacy_archive_hints.get(self.number, ArchiveHint())

    @property
    def has_content(self):
        if self.slot is not None:
            return self.slot.has_content
        return self._archive_hint().has_content

    @has_content.setter
    def has_content(self, value):
        if self.slot is not None:
            self.slot.has_content = bool(value)
        elif self.number in self.track.legacy_archive_hints:
            self.track.legacy_archive_hints[self.number] = replace(
                self.track.legacy_archive_hints[self.number], has_content=bool(value))

    @property
    def is_blank_key(self):
        if self.slot is not None:
            return self.slot.is_blank_key
        return self._archive_hint().is_blank_key

    @property
    def initial_image(self):
        return self.cell.initial_image

    @property
    def cell_name(self):
        return self.cell.cell_name

    @property
    def sequence_number(self):
        return self.number

    @property
    def name(self):
        return self.track.name

    @property
    def is_paper(self):
        return self.track.is_paper

    @property
    def is_draft(self):
        return self.track.is_draft

    @property
    def cell_id(self):
        return id(self.cell)

    @property
    def _track(self):
        return self.track


class DocumentSnapshot(NamedTuple):
    frames: list
    current_frame: int
    active_layer_index: int
    width: int
    height: int
    tracks: list


class Document:
    """Holds project state: frames + the current frame / active layer cursor."""

    def __init__(self):
        self.frames = [make_frame()]
        self.current_frame = 0
        self.active_layer_index = 0
        self.tracks = []
        self.install_legacy_frames(self.frames)

    @property
    def layers(self):
        """Layers of the current frame."""
        return self.frames[self.current_frame].layers

    @property
    def active_layer(self):
        return self.layers[self.active_layer_index]

    def _track_slots(self, track):
        for column, frame in enumerate(self.frames):
            for slot in frame.layers:
                if slot._track is track:
                    yield column, slot

    def placed_numbers(self, track: Track) -> set[int]:
        """Content-key numbers only: pending, blank and retained slots excluded."""
        return {int(slot.sequence_number) for _, slot in self._track_slots(track)
                if slot.has_content and not is_pending(slot)
                and slot.sequence_number is not None}

    def unplaced_numbers(self, track: Track) -> set[int]:
        """Bank minus placed references, independently of explicit archive."""
        return set(track.cells) - self.placed_numbers(track)

    def pending_entries(self, track: Track) -> tuple[PendingEntry, ...]:
        """Read positions in frame order, including duplicate/None numbers."""
        return tuple(PendingEntry(column, slot)
                     for column, slot in self._track_slots(track) if is_pending(slot))

    def archive_hints(self, track: Track) -> dict[int, ArchiveHint]:
        """Read detached explicit metadata without borrowing mutable values."""
        return deepcopy(track.legacy_archive_hints)

    def legacy_drawing_state(self, track: Track, number: int) -> ArchiveHint:
        """Explicit archive state, otherwise the old bank-view defaults."""
        return self.archive_hints(track).get(number, ArchiveHint())

    def put_archive_hint(self, track: Track, number: int, state: ArchiveHint):
        """Record explicit legacy state without storing artwork or a Layer."""
        track.legacy_archive_hints[int(number)] = deepcopy(state)

    def remove_archive_hint(self, track: Track, number: int):
        """Remove explicit state only; the drawing remains in its bank."""
        return track.legacy_archive_hints.pop(int(number), None)

    def clone_graph(self):
        """Copy the full Track graph, preserving sharing and unplaced cells."""
        copied = object.__new__(Document)
        copied.frames, _ = clone_frame_graph(self.frames)
        copied.current_frame = self.current_frame
        copied.active_layer_index = self.active_layer_index
        copied.tracks = [layer._track for layer in copied.frames[0].layers] if copied.frames else []
        return copied

    def sync_columns(self):
        """Commit column structure; each Track carries its own bank and hints."""
        self.tracks = [layer._track for layer in self.frames[0].layers] if self.frames else []
        for frame in self.frames:
            for index, slot in enumerate(frame.layers):
                if slot.__dict__.get("_pending_detached", False):
                    slot.attach_track(self.tracks[index])

    def capture_column(self, index):
        frames = [Frame([frame.layers[index]], frame.duration) for frame in self.frames]
        copied, _ = clone_frame_graph(frames)
        return [frame.layers[0] for frame in copied]

    def install_legacy_frames(self, frames, sequence_archive=None, *, canvas_size=None):
        """Normalize detached legacy data once, before publishing it to the UI."""
        frames = [Frame([legacy_layer_to_slot(slot) if isinstance(slot, LegacyLayerV1)
                         else slot for slot in frame.layers], frame.duration) for frame in frames]
        archive = {key: legacy_layer_to_slot(slot) if isinstance(slot, LegacyLayerV1)
                   else slot for key, slot in (sequence_archive or {}).items()}
        raw = any("_legacy_input" in slot.__dict__ for frame in frames for slot in frame.layers)
        width = max((len(frame.layers) for frame in frames), default=0)
        for frame in frames:
            while len(frame.layers) < width:
                li = len(frame.layers)
                template = next(other.layers[li] for other in frames if li < len(other.layers))
                if canvas_size is None:
                    image = blank_image()
                else:
                    width, height = canvas_size
                    image = QImage(int(width), int(height), QImage.Format.Format_ARGB32_Premultiplied)
                    image.fill(Qt.GlobalColor.transparent)
                slot = Layer(template.name, image)
                slot.__dict__["_legacy_missing"] = True
                frame.layers.append(slot)
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
            pending = [layer.sequence_only for layer in slots]
            track.connected = True
            for layer, waiting in zip(slots, pending):
                layer.attach_track(track)
                layer.set_pending(waiting)
                if layer.has_content or layer.sequence_only:
                    layer.__dict__.pop("_cell", None)
            for number, layer in archived:
                self.put_archive_hint(track, number, archive_hint(layer))
        if raw:
            rows = [[snapshot_layer(slot) for slot in frame.layers
                     if not slot.__dict__.get("_legacy_missing", False)] for frame in frames]
            expanded = expand_legacy(rows)
            for column, frame in enumerate(frames):
                for li, slot in enumerate(frame.layers):
                    reference = expanded.sheet[column][li]
                    slot.__dict__["_v1_position"] = expanded.positions[column][li]
                    if not slot.sequence_only:
                        slot.has_content = reference.kind == "CELL"
                        slot.is_blank_key = reference.kind == "BLANK"
                        slot.blank_start = expanded.positions[column][li].blank_start
                        if slot.has_content:
                            slot.sequence_number = reference.number
                            slot.__dict__.pop("_cell", None)
                    signature = (slot.has_content, slot.is_blank_key, slot.sequence_number,
                                 slot.sequence_only, slot.blank_start)
                    set_dense_import(slot, (
                        signature, reference, expanded.resolved[column][li]))
                    slot.__dict__.pop("_legacy_input", None)
        self.frames = frames
        self.tracks = [layer._track for layer in frames[0].layers] if frames else []

    def snapshot(self):
        """Copy the entire drawing bank, including unplaced drawings."""
        frames, _ = clone_frame_graph(self.frames)
        tracks = [layer._track for layer in frames[0].layers] if frames else []
        return DocumentSnapshot(frames, self.current_frame, self.active_layer_index,
                                constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT,
                                tracks)

    def restore(self, snap):
        frames, current_frame, active_layer_index, width, height = snap[:5]
        frames = [Frame([legacy_layer_to_slot(slot) if isinstance(slot, LegacyLayerV1)
                         else slot for slot in frame.layers], frame.duration) for frame in frames]
        self.frames, _ = clone_frame_graph(frames)
        self.current_frame = current_frame
        if any("_legacy_input" in slot.__dict__ for frame in self.frames for slot in frame.layers):
            self.install_legacy_frames(self.frames)
        self.tracks = [layer._track for layer in self.frames[0].layers] if self.frames else []
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
        if "_dense_import" in layer.__dict__:
            set_dense_import(copied, deepcopy(layer.__dict__["_dense_import"]))
        copied.tween_member = deepcopy(layer.tween_member)
        cell = layer._cell
        clone_cell(cell)
        track = layer._track
        if id(track) not in tracks:
            copied_track = tracks[id(track)] = track.copy_settings()
            copied_track.cells = {n: clone_cell(c) for n, c in track.cells.items()}
            copied_track.legacy_archive_hints = deepcopy(track.legacy_archive_hints)
            copied_track.connected = track.connected
        copied.__dict__["_cell"] = cells[id(cell)]
        copied.attach_track(tracks[id(track)])
        if (track.connected and layer.sequence_number is not None
                and (layer.has_content or layer.sequence_only)):
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


def archive_hint(layer):
    """Detach the non-image state of a legacy explicit archive entry."""
    return ArchiveHint(bool(layer.has_content), bool(layer.is_blank_key),
                       int(getattr(layer, "exposure",
                           getattr(layer.__dict__.get("_legacy_input"), "exposure", 1))),
                       bool(layer.sequence_only),
                       deepcopy(layer.tween), deepcopy(layer.tween_member))


def clear_slot(layer, *, blank=False):
    """Remove a sheet reference, retaining its drawing in the Track bank.

    Detached legacy slots keep their former initial-image copy semantics.
    """
    position = layer.__dict__.get("_v1_position")
    layer.detach_cell()
    layer.__dict__.pop("_v1_position", None)
    layer.__dict__.pop("_dense_import", None)
    layer.__dict__.pop("_content_origin", None)
    layer.sequence_number = None
    if position is not None and position.legacy is not None and not position.legacy.has_content:
        initial = position.legacy.initial_image
        layer.initial_image = QImage(initial) if initial is not None else None
    layer.image = blank_image()
    layer.has_content = False
    layer.is_blank_key = blank
    layer.blank_start = bool(layer.is_blank_key)
    layer.sequence_number = None
    clear_pending(layer)
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
    layer.blank_start = bool(layer.is_blank_key)
    if layer._track.connected:
        layer.__dict__.pop("_cell", None)


def place_number(target, source, number):
    """Recall a drawing with the legacy recall operation's exact copy policy.

    Connected content/pending slots share the Cell in their Track bank. The
    image object is reused, while initial_image comes from an independent clone.
    """
    copied = source if isinstance(source, DrawingTarget) else source.clone()
    target.detach_cell()
    target.sequence_number = None
    target.image = source.image
    target.initial_image = (QImage(copied.initial_image) if copied.initial_image is not None else None)
    target.has_content = bool(copied.has_content)
    target.is_blank_key = not bool(copied.has_content)
    target.cell_name = getattr(copied, "cell_name", None)
    if target._track.connected:
        target._track.cells.setdefault(int(number), target._cell)
    target.sequence_number = int(number)
    clear_pending(target)
    target.blank_start = bool(target.is_blank_key)
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
    target.set_pending(source.sequence_only)
    target.blank_start = source.blank_start
    target.__dict__["_v1_position"] = V1Position(
        snapshot_layer(target), key_start=bool(content or blank),
        blank_start=target.blank_start, pending=source.sequence_only)
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
        owner = (preferred if preferred is not None and any(layer is preferred for layer in matches)
                 else matches[0])
        attrs = list(matches)
        track = target._track
        if number in track.legacy_archive_hints and number in track.cells:
            attrs.append(track.cells[number])
        attrs.extend(layer for (index, n), layer in archive.items()
                     if index == li and n == number)
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
        track.legacy_archive_hints = {mapping.get(n, n): hint
                                      for n, hint in track.legacy_archive_hints.items()}
    for frame in frames:
        layer = frame.layers[layer_index]
        position = layer.__dict__.get("_v1_position")
        if position is not None and position.legacy is not None:
            saved_number = position.legacy.sequence_number
            if saved_number in mapping:
                layer.__dict__["_v1_position"] = replace(
                    position, legacy=replace(position.legacy,
                                             sequence_number=mapping[saved_number]))
        if layer.sequence_number is not None:
            old = int(layer.sequence_number)
            if normalize_archive_keys or old in mapping:
                layer.sequence_number = mapping[old]
        elif next_number is not None and layer.has_content and not layer.sequence_only:
            cell = layer._cell
            if track is not None and track.connected:
                track.cells[next_number] = cell
            layer.sequence_number = next_number
            if track is not None and track.connected:
                layer.__dict__.pop("_cell", None)
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


def project_v1_state(frames, sequence_archive=None) -> tuple[list[Any], dict[Any, Any]]:
    """Project unreferenced bank drawings to v1 archive without mutating live data."""
    if not frames or not any(layer._track.connected for layer in frames[0].layers):
        # Raw v1 inputs pass through without installation or numbering.
        # v1 frames hold LegacyLayerV1 slots, not live Layers.
        return [Frame(cast(Any, [snapshot_layer(slot) for slot in frame.layers]), frame.duration)
                for frame in frames], {key: snapshot_layer(slot)
                                      for key, slot in (sequence_archive or {}).items()}
    connected_columns = {li for li, layer in enumerate(frames[0].layers)
                         if layer._track.connected}
    # Raw entries belong only to unconnected columns. Connected columns derive
    # their entire archive from H union (cells - R), with bank-owned artwork.
    archive = {key: layer for key, layer in (sequence_archive or {}).items()
               if int(key[0]) not in connected_columns}
    for li in sorted(connected_columns):
        track = frames[0].layers[li]._track
        represented = {int(frame.layers[li].sequence_number) for frame in frames
                       if frame.layers[li].sequence_number is not None
                       and (frame.layers[li].has_content or is_pending(frame.layers[li]))}
        numbers = track.legacy_archive_hints.keys() | (track.cells.keys() - represented)
        for number in sorted(numbers):
            cell = track.cells.get(number)
            if cell is None:
                continue
            hint = track.legacy_archive_hints.get(number, ArchiveHint())
            layer = LegacyLayerV1(track.name, cell.image,
                          visible=track.visible, opacity=track.opacity,
                          is_paper=track.is_paper, is_draft=track.is_draft,
                          alpha_locked=track.alpha_locked,
                          color_filter_enabled=track.color_filter_enabled,
                          color_filter_rgb=deepcopy(track.color_filter_rgb),
                          initial_image=QImage(cell.initial_image) if cell.initial_image is not None else None,
                          cell_name=cell.cell_name, has_content=hint.has_content,
                          is_blank_key=hint.is_blank_key, exposure=hint.exposure,
                          sequence_number=number, sequence_only=hint.sequence_only,
                          tween=deepcopy(hint.tween), tween_member=deepcopy(hint.tween_member))
            archive[(li, number)] = layer
    rows, positions = [], []
    from .timeline_metadata import reference_for
    from .timeline_span import span_at
    for column, frame in enumerate(frames):
        row, markers = [], []
        for li, slot in enumerate(frame.layers):
            reference = reference_for(slot)
            row.append(reference)
            old = slot.__dict__.get("_v1_position")
            payload = snapshot_layer(slot)
            if old is not None and old.legacy is not None:
                payload = replace(old.legacy, name=slot.name, tween=deepcopy(slot.tween),
                                  tween_member=slot.tween_member)
                if slot.has_content and (old.legacy.has_content
                                         or span_at(frames, li, column)[1] == column):
                    payload = replace(payload, image=QImage(slot.image),
                                      initial_image=QImage(slot.initial_image)
                                      if slot.initial_image is not None else None,
                                      cell_name=slot.cell_name)
            if old is not None and old.legacy is None:
                payload = None
            markers.append(V1Position(payload, old.key_start if old else False,
                                      slot.blank_start, slot.sequence_only))
        rows.append(row)
        positions.append(markers)
    projected = compress_v1(rows, positions)
    return [Frame(cast(Any, list(row)), frame.duration)
            for row, frame in zip(projected, frames)], archive
