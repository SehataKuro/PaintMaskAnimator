"""Qt-independent document model.

Owns the *what* of a project — the list of frames, which frame is current,
and which layer is active — independent of any widget, event loop, or
rendering. The PaintCanvas widget delegates its state to a Document instance
so that this core model can be reasoned about and tested without a GUI.

Frames and layers carry PmaImage payloads.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, NamedTuple, cast

from . import constants
from .cell_numbering import normalization_mapping

from .document_changes import (
    ChangeSet, Cursor, DocumentRestored, DrawingAttributes, DrawingPixels,
    ExposureIndexReset, FrameStructure, Selection, SheetSpans, TrackProperties, TrackStructure,
)
from .timeline_span import span_at
from .models import (
    ArchiveHint, Cell, Frame, Layer, TRACK_FIELDS, Track, default_layer_name,
    make_frame, next_layer_name,
)
from .timeline_metadata import set_dense_import
from .pma_image import PmaImage, copy_image, snapshot_image, to_pma_image
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

    def write_drawing_patch(self, frame, layer_index, patch, rect, *,
                            number=None, rgba=False, has_content=None, stroke_image=None):
        """Commit an image-coordinate patch at a position or directly to a bank.

        Pass a frame for the position's current drawing, or pass a drawing
        number with no frame to address the bank without placing the drawing.
        """
        if frame is None:
            cell = self.tracks[layer_index].cells.get(number)
            if cell is None:
                return ChangeSet()
            slot = None
            image = cell.image
        else:
            slot = self.frames[frame].layers[layer_index]
            number = slot.sequence_number
            # The brush retains the image it prepared when the stroke began.
            image = slot.image if stroke_image is None else stroke_image
        x, y, _, _ = rect
        if rgba:
            image.write_rgba(patch, x, y)
        else:
            image.write_qimage(patch, x, y)
        if slot is not None and has_content is not None:
            slot.has_content = has_content
        return ChangeSet((DrawingPixels(layer_index, number, frame, (rect,)),))

    def replace_drawing_image(self, frame, layer_index, image, *, has_content):
        slot = self.frames[frame].layers[layer_index]
        slot.image = image
        slot.has_content = has_content
        return ChangeSet((DrawingPixels(layer_index, slot.sequence_number, frame, ()),))

    def replace_drawing_images(self, groups):
        """Install one computed image per group, retaining its shared identity."""
        changes = []
        for positions, image in groups:
            image = to_pma_image(image)
            for frame, layer_index in positions:
                changes.extend(self.replace_drawing_image(
                    frame, layer_index, image, has_content=True,
                ).changes)
        return ChangeSet(tuple(changes))

    def set_drawing_content(self, frame, layer_index, has_content):
        slot = self.frames[frame].layers[layer_index]
        slot.has_content = has_content
        return ChangeSet((DrawingAttributes(
            layer_index, slot.sequence_number, frame, ("has_content",)),))

    def stage_drawing_reference(self, frame, layer_index, source):
        """Clone a reference into a staged column before legacy installation."""
        self.frames[frame].layers[layer_index] = source.clone_reference()
        return ChangeSet((SheetSpans(layer_index, frame, frame + 1),))

    def stage_numbered_drawing(self, frame, layer_index, image, has_content, number):
        """Populate a staged slot before installing its column's drawing bank."""
        slot = self.frames[frame].layers[layer_index]
        slot.image = image
        slot.has_content = has_content
        if has_content:
            slot.sequence_number = number
        return ChangeSet((DrawingPixels(layer_index, slot.sequence_number, frame, ()),))

    def retain_resized_bank(self, old_first_frame):
        """Carry archive hints and drawings omitted by the sheet resize."""
        changes = []
        for index, old_slot in enumerate(old_first_frame.layers):
            track = self.tracks[index]
            track.legacy_archive_hints = deepcopy(old_slot._track.legacy_archive_hints)
            for number, cell in old_slot._track.cells.items():
                existing = track.cells.get(number)
                if existing is None:
                    track.cells[number] = Cell(
                        copy_image(cell.image), copy_image(cell.initial_image), cell.cell_name,
                    )
                    changes.append(DrawingPixels(index, number, None, ()))
                elif (number in track.legacy_archive_hints and existing.initial_image is None
                      and cell.initial_image is not None):
                    existing.initial_image = copy_image(cell.initial_image)
                    changes.append(DrawingAttributes(index, number, None, ("initial_image",)))
        return ChangeSet(tuple(changes))

    def set_initial_images(self, positions=(), archived=(), *, restoring=False):
        """Apply initial images in position order, then in archive order.

        Restoration uses the first surviving reference to each drawing.
        Import retains its last-write policy.
        """
        changes = []
        restored = set()
        for frame, layer_index, image in positions:
            if not (0 <= frame < len(self.frames)
                    and 0 <= layer_index < len(self.frames[frame].layers)):
                continue
            slot = self.frames[frame].layers[layer_index]
            if restoring and slot.cell_id in restored:
                continue
            slot.initial_image = image
            if restoring:
                restored.add(slot.cell_id)
            changes.append(DrawingAttributes(
                layer_index, slot.sequence_number, frame, ("initial_image",)))
        for layer_index, number, image in archived:
            if not 0 <= layer_index < len(self.tracks):
                continue
            track = self.tracks[layer_index]
            cell = track.cells.get(number) if number in track.legacy_archive_hints else None
            if cell is None or (restoring and id(cell) in restored):
                continue
            cell.initial_image = image
            if restoring:
                restored.add(id(cell))
            changes.append(DrawingAttributes(layer_index, number, None, ("initial_image",)))
        return ChangeSet(tuple(changes))

    def keep_initial_images(self, positions):
        return self.set_initial_images(
            (frame, layer_index, copy_image(self.frames[frame].layers[layer_index].image))
            for frame, layer_index in positions
        )

    def resolve_numbered_target(self, frame, layer_index, number, *, sheet):
        """Resolve a borrowed Cell/Track target without creating missing drawings."""
        def at(fi):
            if 0 <= fi < len(self.frames) and 0 <= layer_index < len(self.frames[fi].layers):
                return self.frames[fi].layers[layer_index]
            return None

        layer = at(frame)
        if number is None:
            return DrawingTarget(layer._cell, layer._track, frame, layer, number) if layer is not None else None
        if (layer is not None and layer.has_content
                and layer.sequence_number == number):
            return DrawingTarget(layer._cell, layer._track, frame, layer, number)
        for fi in range(len(self.frames)):
            layer = at(fi)
            if (layer is not None and layer.has_content
                    and layer.sequence_number == number
                    and (not sheet or not is_pending(layer))):
                return DrawingTarget(layer._cell, layer._track, fi, layer, number)
        if 0 <= layer_index < len(self.tracks):
            track = self.tracks[layer_index]
            cell = drawing(track, number)
            if cell is not None:
                return DrawingTarget(cell, track, None, None, number)
        return None

    def has_unlinked_tracks(self):
        """文書へ公開していない列（connected でない Track）につながった位置があるか。"""
        return any(not layer._track.connected for frame in self.frames for layer in frame.layers)

    def link_layer_tracks(self):
        """Reconcile slot tracks after loading or restoring a graph."""
        if not self.frames:
            return ChangeSet()
        if any(not layer._track.connected for frame in self.frames for layer in frame.layers):
            self.sync_columns()
            self.install_legacy_frames(self.frames)
            return ChangeSet((ExposureIndexReset(),))
        self.sync_columns()
        for frame in self.frames:
            for li, layer in enumerate(frame.layers):
                track = self.tracks[li]
                if layer._track is not track:
                    number = layer.sequence_number
                    if number is not None and (layer.has_content or layer.sequence_only):
                        track.cells.setdefault(int(number), layer._cell)
                    layer.attach_track(track)
                    if layer.has_content or layer.sequence_only:
                        layer.__dict__.pop("_cell", None)
        return ChangeSet()

    def restore_drawing(self, frame, layer_index, number, image, has_content, *,
                        sheet, patches=None):
        target = self.resolve_numbered_target(frame, layer_index, number, sheet=sheet)
        if target is None:
            return ChangeSet()
        if patches is None:
            target.image = image
            rects = ()
        else:
            target.image.write_qimages((x, y, patch) for (x, y, _, _), patch in patches)
            rects = tuple(rect for rect, _ in patches)
        target.has_content = bool(has_content)
        frame = target.column
        pixels = DrawingPixels(layer_index, number, frame, rects)
        if frame is None:
            return ChangeSet((pixels,))
        return ChangeSet((Cursor(frame, notify=False), Selection(layer_index), pixels))

    def restore_batch_drawing(self, frame, layer_index, image, has_content, number, *, sheet):
        """Yield changes as slots are restored, in sheet observation order."""
        target = self.resolve_numbered_target(frame, layer_index, number, sheet=sheet)
        if target is None:
            return
        target.image = image
        if number is None:
            target.has_content = has_content
            yield ChangeSet((DrawingPixels(layer_index, number, frame, ()),))
            return
        if target.column is None:
            target.has_content = has_content
            yield ChangeSet((DrawingPixels(layer_index, number, None, ()),))
        for sheet_frame, candidate in enumerate(self.frames):
            if not 0 <= layer_index < len(candidate.layers):
                continue
            slot = candidate.layers[layer_index]
            former_content = (
                slot._track is target.track
                and slot.sequence_number == number
                and slot.__dict__.get("_content_origin") == number
            )
            if slot.cell_id == target.cell_id or former_content:
                slot.has_content = has_content
                yield ChangeSet((DrawingPixels(layer_index, number, sheet_frame, ()),))

    def restore_exposure(self, e):
        before = len(self.frames)
        self.ensure_frame_count(e.frame_count)
        for frame in self.frames[e.frame_count:]:
            release_slots(frame.layers)
        del self.frames[e.frame_count:]
        for frame, values in zip(self.frames[e.column:], e.references):
            slot = frame.layers[e.layer]
            content, blank, start, number, pending, tween, member, imported, position = values
            slot.has_content = False
            slot.sequence_number = number
            slot.has_content = content
            slot.is_blank_key = blank
            slot.blank_start = start
            slot.set_pending(pending)
            slot.tween = deepcopy(tween)
            slot.tween_member = member
            if imported is None:
                slot.__dict__.pop("_dense_import", None)
            else:
                set_dense_import(slot, imported)
            if position is None:
                slot.__dict__.pop("_v1_position", None)
            else:
                slot.__dict__["_v1_position"] = position
        return ChangeSet((
            FrameStructure("restore", before, len(self.frames), e.column),
            SheetSpans(e.layer, e.column, len(self.frames)),
            Cursor(e.current_frame), Selection(e.active_layer),
        ))

    def restore_snapshot(self, snapshot):
        width, height = self.restore(snapshot)
        return ChangeSet((DocumentRestored(width, height), ExposureIndexReset()))

    def restore_removed_track(self, e):
        index = e.index
        for frame in self.frames:
            if 0 <= index < len(frame.layers) and len(frame.layers) > 1:
                release_slots([frame.layers[index]])
                frame.layers.pop(index)
        self.sync_columns()
        active = max(0, min(int(e.target_active), len(self.layers) - 1))
        return ChangeSet((TrackStructure("remove", (index,), ()), Selection(active)))

    def restore_inserted_track(self, e):
        index = e.index
        copied, _ = clone_frame_graph([Frame([layer]) for layer in e.layers])
        stored_layers = [frame.layers[0] for frame in copied]
        for frame_index, frame in enumerate(self.frames):
            if frame_index < len(stored_layers):
                layer = stored_layers[frame_index]
            else:
                layer = Layer(
                    default_layer_name(index),
                    blank_image(),
                    visible=True,
                    opacity=1.0,
                )
            if stored_layers:
                layer.share_track_with(stored_layers[0])
            frame.layers.insert(
                max(0, min(int(index), len(frame.layers))),
                layer,
            )
        self.sync_columns()
        active = max(0, min(int(e.target_active), len(self.layers) - 1))
        return ChangeSet((TrackStructure("insert", (), (index,)), Selection(active)))

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
        copied = object.__new__(GraphDocument)
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

    def _valid_position(self, column, layer_index):
        return (0 <= column < len(self.frames)
                and 0 <= layer_index < len(self.frames[column].layers))

    def timeline_block_at(self, column, layer_index):
        kind, start, exposure = span_at(self.frames, int(layer_index), int(column))
        return (kind, start, exposure) if start is not None else None

    def ensure_frame_count(self, count):
        before = len(self.frames)
        if count <= before or not before:
            return ChangeSet()
        names = [layer.name for layer in self.frames[0].layers]
        template = self.frames[0].layers
        added = []
        for _ in range(before, count):
            frame = make_frame(names)
            for index, layer in enumerate(frame.layers):
                # 表示の設定はレイヤーに1つ。写すのではなく同じ Track を指す
                # （Issue #31 Phase 2）。
                layer.share_track_with(template[index])
            added.append(frame)
        self.frames.extend(added)
        return ChangeSet((FrameStructure("extend", before, count, before),))

    def advance_frame(self):
        if not self.frames:
            return ChangeSet()
        column = self.current_frame + 1
        changes = self.ensure_frame_count(column + 1)
        return ChangeSet((*changes.changes, Cursor(column)))

    def _shift_timeline_layer_right(
        self,
        layer_index,
        start_column,
        amount,
        *,
        extend_containing=False,
    ):
        """指定レイヤーの明示コマを右へ移動し、時間の隙間を作る。"""
        if not self.frames:
            return

        layer_index = int(layer_index)
        start_column = max(0, int(start_column))
        amount = max(1, int(amount))
        if not (
            0 <= layer_index
            < len(self.frames[0].layers)
        ):
            return

        containing = self.timeline_block_at(start_column, layer_index)
        slots = [frame.layers[layer_index].clone_slot() for frame in self.frames]
        # Serialization residue follows the old explicit-position copy policy:
        # hidden hold payload stays in place unless a moved key overwrites it.
        positions = [slot.__dict__.get("_v1_position", V1Position(snapshot_layer(slot)))
                     for slot in slots]
        starts = [i for i, slot in enumerate(slots) if i >= start_column and (
            slot.sequence_only or ((block := self.timeline_block_at(i, layer_index)) is not None
                                  and block[1] == i))]
        saved_positions = dict(enumerate(positions))
        for i in reversed(starts):
            empty = slots[i].clone_slot()
            clear_slot(empty)
            saved_positions[i] = V1Position(snapshot_layer(empty))
            saved_positions[i + amount] = positions[i]
        # Trailing empty positions do not force another document extension.
        occupied = [i for i in range(start_column, len(slots))
                    if slots[i].has_content or slots[i].is_blank_key or slots[i].sequence_only]
        self.ensure_frame_count(max([start_column + amount, len(slots)]
                                     + [i + amount + 1 for i in occupied]))
        for column in range(len(self.frames) - 1, start_column - 1, -1):
            source = column - amount
            if source >= start_column and source < len(slots):
                replace_slot(self.frames[column], layer_index, slots[source])
            else:
                clear_slot(self.frames[column].layers[layer_index])
        if containing is not None and containing[1] < start_column and not extend_containing:
            _kind, left, length = containing
            end = left + length
            for column in range(start_column, min(end + amount, len(self.frames))):
                if column < end:
                    replace_slot(self.frames[column], layer_index, slots[column].clone_slot())
                else:
                    clear_slot(self.frames[column].layers[layer_index])
        for column in range(start_column, len(self.frames)):
            target = self.frames[column].layers[layer_index]
            position = saved_positions.get(column)
            if position is None:
                empty = target.clone_slot()
                clear_slot(empty)
                position = V1Position(snapshot_layer(empty))
            target.__dict__["_v1_position"] = position

    def _shift_timeline_layer_left(self, layer_index, start_column, amount=1):
        if not self.frames:
            return
        start_column, amount = max(0, int(start_column)), max(1, int(amount))
        removed_start = max(0, start_column - amount)
        containing = self.timeline_block_at(removed_start, layer_index)
        slots = [frame.layers[layer_index].clone_slot() for frame in self.frames]
        for column in range(max(0, start_column - amount), len(slots)):
            source = column + amount
            if source < len(slots):
                replace_slot(self.frames[column], layer_index, slots[source])
            else:
                clear_slot(self.frames[column].layers[layer_index])
        if containing is not None and containing[1] < removed_start:
            # A held-frame deletion shortens the saved key as well as the dense
            # run. Otherwise a stale marker can pass v1's unchanged-input check
            # when a following key hides its old tail.
            head = self.frames[containing[1]].layers[layer_index]
            position = head.__dict__.get("_v1_position")
            block = self.timeline_block_at(containing[1], layer_index)
            if position is not None and position.legacy is not None and block is not None:
                head.__dict__["_v1_position"] = replace(
                    position, legacy=replace(position.legacy, exposure=block[2]))

    def _trim_unused_trailing_frames(self):
        """全レイヤーで未使用になった末尾の時間列を取り除く。"""
        while len(self.frames) > 1:
            last_column = len(self.frames) - 1
            if any(
                self.timeline_block_at(last_column, layer_index)
                is not None
                for layer_index in range(len(self.frames[0].layers))
            ):
                break
            release_slots(self.frames[-1].layers)
            self.frames.pop()

    def duplicate_frame(self, column):
        if not 0 <= column < len(self.frames):
            return ChangeSet()
        before = len(self.frames)
        source_frame = self.frames[column]
        new_frame = Frame([layer.clone_slot() for layer in source_frame.layers], source_frame.duration)
        at = column + 1
        for layer in new_frame.layers:
            layer.attach_track(layer._track)
        held = [self.timeline_block_at(column, li)
                for li in range(len(source_frame.layers))]
        self.frames.insert(at, new_frame)
        for li, block in enumerate(held):
            if block is not None and block[1] < column:
                clear_slot(new_frame.layers[li])
                end = block[1] + block[2]
                for target in range(at, min(end + 1, len(self.frames))):
                    if target < end:
                        replace_slot(self.frames[target], li, source_frame.layers[li].clone_reference())
                    else:
                        clear_slot(self.frames[target].layers[li])
        return ChangeSet((FrameStructure("duplicate", before, before + 1, at), Cursor(at)))

    def delete_frame(self, column, layer_index):
        """Remove one position on a track and prune the shared unused tail."""
        if not self._valid_position(column, layer_index):
            return ChangeSet()
        before = len(self.frames)
        block = self.timeline_block_at(column, layer_index)
        if block is None and before == 1:
            return ChangeSet()
        changes = []
        start = column
        if block is not None:
            _kind, start, exposure = block
            layer = self.frames[start].layers[layer_index]
            if layer.has_content and layer.sequence_number is not None:
                if not layer._track.connected:
                    self.install_legacy_frames(self.frames)
                    changes.append(ExposureIndexReset())
                self.put_archive_hint(
                    layer._track, int(layer.sequence_number),
                    replace(archive_hint(layer), exposure=exposure),
                )
        self._shift_timeline_layer_left(layer_index, column + 1)
        self._trim_unused_trailing_frames()
        after = len(self.frames)
        if before != after:
            changes.append(FrameStructure("trim", before, after, after))
        changes.extend((SheetSpans(layer_index, start, before), Cursor(min(column, after - 1))))
        return ChangeSet(tuple(changes))

    def _sheet_edit_changes(self, before, layer_index, start, stop, *extra):
        after = len(self.frames)
        structure = (FrameStructure("extend", before, after, before),) if after > before else ()
        return ChangeSet((*structure, SheetSpans(layer_index, start, stop), *extra))

    def create_blank_key(self, column, layer_index):
        """Insert a blank span after a key or replace the tail of a hold."""
        column = max(0, int(column))
        layer_index = int(layer_index)
        if not 0 <= layer_index < self._column_count():
            return ChangeSet()
        before = len(self.frames)
        shifted = False
        self.ensure_frame_count(column + 1)

        block = self.timeline_block_at(
            column,
            layer_index,
        )
        blank_exposure = 1
        affected_start = column

        if block is not None:
            _kind, start, exposure = block
            start = int(start)
            affected_start = start
            exposure = max(1, int(exposure))
            source = self.frames[
                start
            ].layers[layer_index]
            template = source.clone()

            if column == start:
                # ●／○の開始セルを選択：
                # 元のコマを保持し、❘の次へ同じ長さの○を挿入する。
                insertion_column = start + exposure
                blank_exposure = exposure
                self._shift_timeline_layer_right(
                    layer_index,
                    insertion_column,
                    blank_exposure,
                )
                self.ensure_frame_count(
                    insertion_column + blank_exposure
                )
                column = insertion_column
                shifted = True
            else:
                # ー／│を選択：
                # 露出全体の長さは変えず、選択位置で前後に分割する。
                #
                # 例：
                # ●ーーーーー│
                #       ↓
                # ●ーー○ーー│
                split_offset = max(
                    1,
                    column - start,
                )
                split_offset = min(
                    split_offset,
                    exposure - 1,
                )
                blank_exposure = max(
                    1,
                    exposure - split_offset,
                )
                column = start + split_offset
                self.ensure_frame_count(
                    column + blank_exposure
                )
        else:
            # 未使用セル上では、その位置へ○を作る。
            # 直前の明示コマがある場合は、○の直前まで露出を伸ばす。
            self.ensure_frame_count(column + 1)
            target = self.frames[
                column
            ].layers[layer_index]
            template = target.clone()

            previous_start = None
            for candidate in range(
                column - 1,
                -1,
                -1,
            ):
                layer = self.frames[
                    candidate
                ].layers[layer_index]
                if (
                    layer.has_content
                    or getattr(
                        layer,
                        "is_blank_key",
                        False,
                    )
                ):
                    previous_start = candidate
                    template = layer.clone()
                    break

            if previous_start is not None:
                affected_start = previous_start
                previous = self.frames[
                    previous_start
                ].layers[layer_index]
                for index in range(previous_start + 1, column):
                    self.frames[index].layers[layer_index] = previous.clone_reference()

        target = self.frames[
            column
        ].layers[layer_index]
        # The blank position owns local v1 residue; it must not erase a bank Cell.
        target.detach_cell()
        target.has_content = False
        clear_pending(target)
        target.image = blank_image()
        properties = self.set_position_track_properties(
            column, layer_index,
            visible=bool(template.visible), opacity=float(template.opacity),
            alpha_locked=bool(template.alpha_locked),
            color_filter_enabled=bool(template.color_filter_enabled),
            color_filter_rgb=(tuple(template.color_filter_rgb)
                              if template.color_filter_rgb is not None else None),
        )
        target.has_content = False
        target.is_blank_key = True
        target.blank_start = True
        target.__dict__.pop("_v1_position", None)
        for index in range(column + 1, column + blank_exposure):
            self.frames[index].layers[layer_index] = target.clone_reference()

        stop = len(self.frames) if shifted else column + blank_exposure
        return self._sheet_edit_changes(
            before, layer_index, affected_start, stop,
            *properties.changes, Cursor(column), Selection(layer_index),
        )

    def clear_sheet_span(self, layer_index, start, stop, *, archive=False):
        if start >= stop or not all(self._valid_position(i, layer_index) for i in range(start, stop)):
            return ChangeSet()
        block = self.timeline_block_at(start, layer_index)
        affected_start = start if block is None else block[1]
        affected_stop = stop if block is None else max(stop, block[1] + block[2])
        if archive:
            slot = self.frames[start].layers[layer_index]
            if slot.has_content and slot.sequence_number is not None:
                self.put_archive_hint(slot._track, int(slot.sequence_number), archive_hint(slot))
        for column in range(start, stop):
            clear_slot(self.frames[column].layers[layer_index])
        return ChangeSet((SheetSpans(layer_index, affected_start, affected_stop),))

    def insert_track_frame(self, column, layer_index):
        if not self._valid_position(column, layer_index):
            return ChangeSet()
        before = len(self.frames)
        block = self.timeline_block_at(column, layer_index)
        fill = (self.frames[column].layers[layer_index].clone_reference()
                if block is not None and column > block[1] else None)
        self.ensure_frame_count(before + 1)
        self._shift_timeline_layer_right(layer_index, column, 1, extend_containing=True)
        if fill is not None:
            # Extending the reference preserves the hidden PNG at this position.
            position = self.frames[column].layers[layer_index].__dict__.get("_v1_position")
            if position is not None:
                fill.__dict__["_v1_position"] = position
            fill.tween = None
            fill.tween_member = None
            fill.blank_start = False
            replace_slot(self.frames[column], layer_index, fill)
        start = block[1] if block is not None else column
        return self._sheet_edit_changes(before, layer_index, start, len(self.frames))

    def delete_track_frame(self, column, layer_index):
        if not self._valid_position(column, layer_index):
            return ChangeSet()
        block = self.timeline_block_at(column, layer_index)
        self._shift_timeline_layer_left(layer_index, column + 1)
        start = block[1] if block is not None else column
        return ChangeSet((SheetSpans(layer_index, start, len(self.frames)), Cursor(column)))

    def _span_bounds(self, column, layer_index):
        block = self.timeline_block_at(column, layer_index)
        return (block[1], block[1] + block[2]) if block else (column, column + 1)

    def paste_sheet_cell(self, column, layer_index, source, length):
        if not self._valid_position(column, layer_index) or not isinstance(source, Layer) or length < 1:
            return ChangeSet()
        before = len(self.frames)
        affected_start, affected_stop = self._span_bounds(column, layer_index)
        existing = self.frames[column].layers[layer_index]
        pasted = source.clone()
        pasted.name = existing.name
        pasted.visible = existing.visible
        pasted.opacity = existing.opacity
        # トゥイーンの帯は貼り付け先では組み直せないので持ち込まない。
        pasted.tween = None
        pasted.tween_member = None
        import_drawing_reference(
            self.frames, {}, column, layer_index, pasted,
            preferred_frame=self.current_frame)
        existing.tween = None
        existing.tween_member = None
        self.ensure_frame_count(column + length)
        for index in range(column + 1, column + length):
            # A later existing key truncates the pasted exposure.
            block = self.timeline_block_at(index, layer_index)
            if block is not None and block[1] == index:
                break
            replace_slot(self.frames[index], layer_index, existing.clone_reference())
        stop = max(affected_stop, index if length > 1 and block is not None
                   and block[1] == index else column + length)
        return self._sheet_edit_changes(before, layer_index, affected_start, stop)

    def new_sheet_cell(self, column, layer_index):
        if not self._valid_position(column, layer_index):
            return ChangeSet()
        start, stop = self._span_bounds(column, layer_index)
        layer = self.frames[column].layers[layer_index]
        clear_slot(layer)
        create_drawing(layer, blank_image(), number=None)
        return ChangeSet((SheetSpans(layer_index, start, stop), Cursor(column), Selection(layer_index)))

    def split_sheet_clip(self, column, layer_index):
        block = self.timeline_block_at(column, layer_index)
        if block is None or column <= block[1]:
            return ChangeSet()
        _kind, start, exposure = block
        head = self.frames[start].layers[layer_index]
        copied = head.clone()
        copied.detach_cell()
        number = max(head._track.cells, default=0) + 1
        head._track.cells[number] = copied._cell
        for position in range(column, start + exposure):
            slot = self.frames[position].layers[layer_index]
            slot.sequence_number = number
            slot.has_content = True
            slot.is_blank_key = False
            slot.blank_start = False
            clear_pending(slot)
            slot.tween = None
            slot.tween_member = None
        return ChangeSet((SheetSpans(layer_index, start, start + exposure),))

    def merge_sheet_clip(self, column, layer_index):
        block = self.timeline_block_at(column, layer_index)
        if block is None:
            return ChangeSet()
        _kind, start, exposure = block
        previous = self.timeline_block_at(start - 1, layer_index)
        if previous is None:
            return ChangeSet()
        _kind, previous_start, _length = previous
        source = self.frames[previous_start].layers[layer_index]
        for position in range(start, start + exposure):
            slot = source.clone_slot()
            slot.tween = None
            slot.tween_member = None
            slot.blank_start = False
            position_info = slot.__dict__.get("_v1_position")
            if position_info is not None and position_info.legacy is not None:
                # Cloned save keys must not reach beyond the merged region.
                slot.__dict__["_v1_position"] = replace(
                    position_info, legacy=replace(
                        position_info.legacy,
                        exposure=min(position_info.legacy.exposure, start + exposure - position)))
            replace_slot(self.frames[position], layer_index, slot)
        return ChangeSet((SheetSpans(layer_index, previous_start, start + exposure),))

    def timeline_step_columns(self, layer_index):
        from .timeline_metadata import reference_for
        columns = []
        previous = None
        for column, frame in enumerate(self.frames):
            if not 0 <= layer_index < len(frame.layers):
                previous = None
                continue
            layer = frame.layers[layer_index]
            reference = reference_for(layer)
            starts = reference != previous or (reference.kind == "BLANK" and layer.blank_start)
            if reference.kind != "EMPTY" and starts and not is_pending(layer):
                columns.append(column)
            previous = reference
        return columns

    def set_sheet_duration(self, column, layer_index, duration):
        before = len(self.frames)
        block = self.timeline_block_at(column, layer_index)
        if block is not None:
            _kind, start, length = block
            duration = max(1, int(duration))
            self.ensure_frame_count(start + duration)
            source = self.frames[start].layers[layer_index]
            for column in range(start + 1, start + duration):
                self.frames[column].layers[layer_index] = source.clone_reference()
            for column in range(start + duration, start + length):
                clear_slot(self.frames[column].layers[layer_index])
            return self._sheet_edit_changes(before, layer_index, start, start + max(length, duration))
        return ChangeSet()

    def prepare_sheet_move(self, source_frame, source_layer, destination_frame, destination_layer):
        source_frame, source_layer = int(source_frame), int(source_layer)
        destination_frame, destination_layer = int(destination_frame), int(destination_layer)
        if not (0 <= source_frame < len(self.frames)) or destination_frame < 0:
            return None
        if not (0 <= source_layer < len(self.frames[source_frame].layers)
                and 0 <= destination_layer < len(self.frames[0].layers)):
            return None
        block = self.timeline_block_at(source_frame, source_layer)
        if block is None:
            return None
        kind, source_frame, source_span = block
        source_end = source_frame + source_span
        moving = [self.frames[index].layers[source_layer].clone_slot()
                  for index in range(source_frame, source_end)]
        previous = (self.timeline_block_at(source_frame - 1, source_layer)
                    if source_frame else None)
        tail = (self.timeline_block_at(source_end, source_layer)
                if source_end < len(self.frames) else None)
        tail_blank = ((tail[1], tail[1] + tail[2])
                      if kind == "content" and source_layer == destination_layer
                      and tail is not None and tail[0] == "blank" else None)
        return source_frame, source_layer, destination_frame, destination_layer, moving, previous, tail, tail_blank, kind

    def move_sheet_cell(self, prepared):
        if prepared is None:
            return ChangeSet()
        (source_frame, source_layer, destination_frame, destination_layer,
         moving, previous, tail, tail_blank, kind) = prepared
        source_span = len(moving)
        source_end = source_frame + source_span
        before = len(self.frames)
        destination_start, destination_stop = self._span_bounds(destination_frame, destination_layer)
        for index in range(source_frame, source_end):
            clear_slot(self.frames[index].layers[source_layer])
        if previous is not None and previous[1] + previous[2] == source_frame:
            fill_end = source_end
            if source_layer == destination_layer and destination_frame > source_frame:
                fill_end = max(fill_end, destination_frame)
                if tail is not None:
                    fill_end = min(fill_end, tail[1])
            self.ensure_frame_count(fill_end)
            template = self.frames[previous[1]].layers[source_layer]
            for index in range(source_frame, fill_end):
                replace_slot(self.frames[index], source_layer, template.clone_reference())
        self.ensure_frame_count(destination_frame + source_span)
        occupied = self.timeline_block_at(destination_frame, destination_layer)
        if occupied is not None:
            _kind, start, length = occupied
            if start == destination_frame and start != source_frame:
                self._shift_timeline_layer_right(destination_layer, destination_frame, 1)
            elif start < destination_frame:
                # The old key was truncated at the drop position; its tail
                # must not reappear beyond the moved drawing.
                for index in range(destination_frame, start + length):
                    clear_slot(self.frames[index].layers[destination_layer])
        end = destination_frame + source_span
        # Determine displaced circles before replacing their visible positions.
        if kind == "content":
            self._follow_blanks(destination_layer, destination_frame, end, tail_blank)
        for offset, copied in enumerate(moving):
            index = destination_frame + offset
            target = self.frames[index].layers[destination_layer]
            if source_layer != destination_layer:
                import_drawing_reference(
                    self.frames, {}, index, destination_layer, copied,
                    preferred_frame=destination_frame,
                    content=kind == "content", blank=kind == "blank")
            else:
                replace_slot(self.frames[index], destination_layer, copied)
                target = self.frames[index].layers[destination_layer]
            target.blank_start = kind == "blank" and offset == 0
        # Moving a member removes its membership at the destination, while
        # untouched members keep their original position metadata.
        self.frames[destination_frame].layers[destination_layer].tween = None
        self.frames[destination_frame].layers[destination_layer].tween_member = None
        stop = len(self.frames)
        start = min(source_frame, previous[1]) if previous is not None else source_frame
        selection = (Cursor(destination_frame), Selection(destination_layer))
        if source_layer == destination_layer:
            return self._sheet_edit_changes(before, source_layer, min(start, destination_start),
                                            stop, *selection)
        return self._sheet_edit_changes(
            before, destination_layer, destination_start, max(destination_stop, stop),
            SheetSpans(source_layer, start, source_end), *selection,
        )

    def _follow_blanks(self, layer_index, start, end, tail_blank=None):
        """Move displaced blank coverage to the end of a moved drawing."""
        spans = {}
        for column in range(start + 1, min(end, len(self.frames))):
            block = self.timeline_block_at(column, layer_index)
            if block is not None and block[0] == "blank":
                spans[block[1]] = block[1] + block[2]
        if tail_blank is not None:
            spans[tail_blank[0]] = tail_blank[1]
        if not spans:
            return
        blank_end = max(spans.values())
        for left, right in spans.items():
            for column in range(left, min(right, len(self.frames))):
                clear_slot(self.frames[column].layers[layer_index])
        if blank_end <= end:
            return
        next_key = next((column for column in range(end, len(self.frames))
                         if self.timeline_block_at(column, layer_index) is not None), None)
        if next_key is not None:
            blank_end = min(blank_end, next_key)
        self.ensure_frame_count(blank_end)
        for column in range(end, blank_end):
            blank = self.frames[column].layers[layer_index]
            clear_slot(blank)
            blank.is_blank_key = True
            blank.blank_start = column == end

    def prepare_sheet_moves(self, positions, layer_delta, column_delta):
        """Resolve selected positions and freeze the sources before recording Undo."""
        count = self._column_count()
        blocks = {}
        for layer_index, column in positions:
            if not 0 <= layer_index < count:
                continue
            block = self.timeline_block_at(column, layer_index)
            if block is None:
                continue
            kind, start, exposure = block
            key = (layer_index, start)
            if key not in blocks:
                blocks[key] = (kind, exposure, self.frames[start].layers[layer_index].clone())

        moves = []
        for (source_layer, start), (kind, exposure, copied) in blocks.items():
            target_layer = source_layer + layer_delta
            target_start = start + column_delta
            if target_start < 0 or not 0 <= target_layer < count:
                return None
            moves.append((source_layer, start, target_layer, target_start, kind, exposure, copied))
        return tuple(moves)

    def move_sheet_cells(self, moves, destination_column, active_layer):
        moves = tuple(moves)
        if not moves or any(
            exposure < 1 or target_start < 0 or not 0 <= target_layer < self._column_count()
            or not all(self._valid_position(i, source_layer)
                       for i in range(source_start, source_start + exposure))
            for source_layer, source_start, target_layer, target_start, _, exposure, _ in moves
        ):
            return ChangeSet()
        before = len(self.frames)
        spans = []
        for source_layer, source_start, target_layer, target_start, _, exposure, _ in moves:
            left, _ = self._span_bounds(target_start, target_layer)
            _, right = self._span_bounds(target_start + exposure - 1, target_layer)
            spans.extend((SheetSpans(source_layer, source_start, source_start + exposure),
                          SheetSpans(target_layer, left, right)))
        # 元位置を未使用セルへ戻す。
        for (
            source_layer,
            source_start,
            _target_layer,
            _target_start,
            _kind,
            _exposure,
            _copied,
        ) in moves:
            for index in range(source_start, source_start + _exposure):
                clear_slot(
                    self.frames[index].layers[source_layer])

        maximum_end = max(
            target_start + exposure
            for (
                _source_layer,
                _source_start,
                _target_layer,
                target_start,
                _kind,
                exposure,
                _copied,
            ) in moves
        )
        self.ensure_frame_count(maximum_end)

        # 移動先と重なる既存露出を切り、既存の明示コマを消す。
        for (
            _source_layer,
            _source_start,
            target_layer,
            target_start,
            _kind,
            exposure,
            _copied,
        ) in moves:
            target_end = target_start + exposure - 1
            for column in range(
                target_start,
                target_end + 1,
            ):
                target = self.frames[
                    column
                ].layers[target_layer]
                if (
                    target.has_content
                    or getattr(
                        target,
                        "is_blank_key",
                        False,
                    )
                ):
                    clear_slot(
                        target
                    )

        # 相対位置を保って配置。
        for (
            _source_layer,
            _source_start,
            target_layer,
            target_start,
            kind,
            exposure,
            copied,
        ) in moves:
            target = self.frames[
                target_start
            ].layers[target_layer]
            if _source_layer != target_layer:
                import_drawing_reference(
                    self.frames, {},
                    target_start, target_layer, copied, preferred_frame=int(destination_column),
                    content=kind == "content", blank=kind == "blank")
            else:
                copy_drawing_reference(target, copied, content=kind == "content", blank=kind == "blank")
            self.set_position_track_properties(
                target_start, target_layer,
                visible=bool(copied.visible), opacity=float(copied.opacity),
                alpha_locked=bool(copied.alpha_locked),
                color_filter_enabled=bool(copied.color_filter_enabled),
                color_filter_rgb=(tuple(copied.color_filter_rgb)
                                  if copied.color_filter_rgb is not None else None),
            )
            target.blank_start = kind == "blank"
            for index in range(target_start + 1, target_start + exposure):
                self.frames[index].layers[target_layer] = target.clone_reference()

        structure = (FrameStructure("extend", before, len(self.frames), before),) if len(self.frames) > before else ()
        return ChangeSet((*structure, *spans, Cursor(max(0, destination_column)), Selection(active_layer)))

    def copy_sheet_cell(self, source_column, destination_column, layer_index):
        block = self.timeline_block_at(source_column, layer_index)
        if block is None or block[0] != "content" or destination_column < 0:
            return ChangeSet()
        source = self.frames[block[1]].layers[layer_index]
        destination_layer = layer_index
        before = len(self.frames)
        start, _ = self._span_bounds(destination_column, destination_layer)
        _, stop = self._span_bounds(destination_column + block[2] - 1, destination_layer)
        self.ensure_frame_count(destination_column + 1)
        target = self.frames[destination_column].layers[destination_layer]
        copied = source.clone()
        copy_drawing_reference(target, copied)
        for index in range(destination_column + 1, destination_column + block[2]):
            self.ensure_frame_count(index + 1)
            self.frames[index].layers[destination_layer] = target.clone_reference()
        properties = self.set_position_track_properties(
            destination_column, destination_layer,
            visible=copied.visible, opacity=copied.opacity,
            alpha_locked=copied.alpha_locked,
            color_filter_enabled=copied.color_filter_enabled,
            color_filter_rgb=copied.color_filter_rgb,
        )
        return self._sheet_edit_changes(before, layer_index, start, stop, *properties.changes,
                                        Cursor(destination_column), Selection(layer_index))

    def recall_sheet_number(self, column, layer_index, number, source):
        if column < 0 or not 0 <= layer_index < self._column_count() or source is None:
            return ChangeSet()
        before = len(self.frames)
        start, stop = self._span_bounds(column, layer_index)
        self.ensure_frame_count(column + 1)
        place_number(self.frames[column].layers[layer_index], source, number)
        return self._sheet_edit_changes(before, layer_index, start, stop, Cursor(column), Selection(layer_index))

    def prepare_exposure_resize(self, layer_index, key_column, boundary_column, edge):
        block = self.timeline_block_at(key_column, layer_index)
        if block is None:
            return None
        _, key_column, exposure = block
        old_end = key_column + exposure - 1
        new_start, new_end = key_column, old_end
        if edge == "left":
            previous = [col for col in self.timeline_step_columns(layer_index) if col < key_column]
            minimum = 0
            if previous:
                prev = self.timeline_block_at(previous[-1], layer_index)
                if prev is not None:
                    minimum = prev[1] + prev[2]
            new_start = max(minimum, min(boundary_column, old_end))
        else:
            new_end = max(key_column, boundary_column)
        if (new_start, new_end) == (key_column, old_end):
            return None
        source = self.frames[key_column].layers[layer_index].clone_slot()
        return key_column, old_end, new_start, new_end, source

    def resize_sheet_exposure(self, layer_index, edge, prepared):
        if prepared is None:
            return ChangeSet()
        key_column, old_end, new_start, new_end, source = prepared
        before = len(self.frames)
        stop = max(old_end, new_end) + 1
        if edge == "left":
            for col in range(min(key_column, new_start), old_end + 1):
                if col < new_start:
                    clear_slot(self.frames[col].layers[layer_index])
                else:
                    slot = source.clone_reference()
                    slot.blank_start = bool(slot.is_blank_key and col == new_start)
                    if col != new_start:
                        slot.tween = None
                        slot.tween_member = None
                    replace_slot(self.frames[col], layer_index, slot)
            cursor = new_start
        else:
            following = [col for col in self.timeline_step_columns(layer_index)
                         if col > key_column]
            if following and new_end >= following[0]:
                shift = new_end + 1 - following[0]
                frame_count = len(self.frames)
                self._shift_timeline_layer_right(layer_index, following[0], shift)
                self.ensure_frame_count(frame_count + shift)
                stop = len(self.frames)
            self.ensure_frame_count(new_end + 1)
            for col in range(key_column + 1, max(old_end, new_end) + 1):
                if col > new_end:
                    clear_slot(self.frames[col].layers[layer_index])
                elif col > old_end:
                    slot = source.clone_reference()
                    slot.blank_start = False
                    slot.tween = None
                    slot.tween_member = None
                    replace_slot(self.frames[col], layer_index, slot)
            cursor = key_column
        return self._sheet_edit_changes(before, layer_index, min(key_column, new_start), stop,
                                        Cursor(cursor), Selection(layer_index))

    def normalize_sheet_numbers(self, layer_indices):
        layer_indices = tuple(layer_indices)
        if not self._valid_columns(layer_indices):
            return ChangeSet()
        for target_layer_index in layer_indices:
            mapping = normalization_mapping(self.frames, {}, target_layer_index)
            apply_number_mapping(
                self.frames, {}, target_layer_index, mapping,
                unnumbered_start=len(mapping) + 1, normalize_archive_keys=True,
            )
        return ChangeSet(tuple(SheetSpans(index, 0, len(self.frames)) for index in layer_indices))

    def sequence_entry_columns(self, layer_index):
        """絵番号ごとの代表セル位置を番号順で返す。"""
        entries = {}
        for column, frame in enumerate(self.frames):
            if not (0 <= layer_index < len(frame.layers)):
                continue
            layer = frame.layers[layer_index]
            number = layer.sequence_number
            if (
                number is not None
                and int(number) >= 1
                and (layer.has_content or layer.sequence_only)
            ):
                number = int(number)
                if number not in entries or layer.sequence_only:
                    entries[number] = column
        return [entries[number] for number in sorted(entries)]

    def insert_sequence_blank(self, layer_index, after_number):
        """選択番号の直後へ空画像番号を挿入し、後続番号を送る。"""
        layer_index = int(layer_index)
        if not 0 <= layer_index < self._column_count():
            return ChangeSet()
        entries = self.sequence_entry_columns(layer_index)
        track = self.frames[0].layers[layer_index]._track
        insert_number = (
            1 if not entries and not track.cells else max(1, int(after_number) + 1)
        )
        mapping = {n: n + 1 for n in track.cells if n >= insert_number}
        apply_number_mapping(
            self.frames, {}, layer_index, mapping)
        track.cells[insert_number] = Cell(blank_image())
        new_column = len(self.frames)
        self.ensure_frame_count(new_column + 1)
        target = self.frames[new_column].layers[layer_index]
        target.image = blank_image()
        target.has_content = False
        target.is_blank_key = True
        target.sequence_number = insert_number
        mark_pending(target)
        if target._track.connected:
            target.__dict__.pop("_cell", None)
        target.blank_start = True
        return self._sheet_edit_changes(
            new_column, layer_index, 0, len(self.frames), Cursor(new_column), Selection(layer_index),
        )

    def sequence_move_sources(self, layer_index, first_number, second_number, *, sheet_mode):
        if first_number == second_number or not 0 <= layer_index < self._column_count():
            return None
        columns = self.sequence_entry_columns(int(layer_index))
        by_number = {
            int(self.frames[column].layers[layer_index].sequence_number):
            self.frames[column].layers[layer_index]
            for column in columns
        }
        track = self.frames[0].layers[layer_index]._track
        for number in track.cells:
            if number not in by_number:
                slot = self.frames[0].layers[layer_index]
                if not (slot.has_content and slot.sequence_number == number):
                    slot = next((frame.layers[layer_index] for frame in self.frames
                                 if frame.layers[layer_index].has_content
                                 and frame.layers[layer_index].sequence_number == number
                                 and (not sheet_mode or not is_pending(frame.layers[layer_index]))), None)
                by_number[number] = DrawingTarget(track.cells[number], track, None, slot, number)

        if first_number not in by_number or second_number not in by_number:
            return None
        return by_number

    def move_sequence_image(self, layer_index, first_number, second_number, by_number):
        low, high = sorted((first_number, second_number))
        ordered_numbers = list(range(low, high + 1))
        snapshots = {}
        for number in ordered_numbers:
            if number not in by_number:
                return ChangeSet()
            layer = by_number[number]
            snapshots[number] = (
                copy_image(layer.image), bool(layer.has_content),
                bool(layer.is_blank_key), layer.cell_name,
                copy_image(layer.initial_image),
            )
        if first_number < second_number:
            source_for_number = {
                number: number + 1 for number in range(first_number, second_number)
            }
        else:
            source_for_number = {
                number: number - 1 for number in range(second_number + 1, first_number + 1)
            }
        source_for_number[second_number] = first_number
        apply_numbered_images(self.frames, layer_index, source_for_number, snapshots)
        return ChangeSet((SheetSpans(layer_index, 0, len(self.frames)),))

    def delete_sequence_entry(self, layer_index, number):
        """連番画像を削除し、シート側の参照セルを未使用へ戻す。"""
        number = int(number)
        layer_index = int(layer_index)
        if not self._valid_position(0, layer_index):
            return ChangeSet()
        before = len(self.frames)
        track = self.frames[0].layers[int(layer_index)]._track
        found = number in track.cells
        for frame in self.frames:
            layer = frame.layers[int(layer_index)]
            if layer.sequence_number == number:
                clear_slot(layer)
                found = True
        self.remove_archive_hint(self.frames[0].layers[int(layer_index)]._track, number)
        track.cells.pop(number, None)
        if not found:
            return ChangeSet()
        self._trim_unused_trailing_frames()
        after = len(self.frames)
        trimmed = (FrameStructure("trim", before, after, after),) if before != after else ()
        return ChangeSet((*trimmed, SheetSpans(layer_index, 0, before), Cursor(min(self.current_frame, after - 1))))

    def place_pending_entries(self):
        before = len(self.frames)
        pending = []
        for column, frame in enumerate(self.frames):
            for layer_index, layer in enumerate(frame.layers):
                if is_pending(layer) and layer.sequence_number is not None:
                    pending.append((int(layer.sequence_number), layer_index, column, layer.clone()))
        for number, layer_index, source_column, copied in sorted(pending):
            # Placement used sparse explicit keys, including clipped v1 lengths.
            # Keep that boundary policy separate from the dense operation span.
            projected, _ = project_v1_state(self.frames)
            target_exposure = 1
            previous = None
            for column, frame in enumerate(self.frames):
                layer = frame.layers[layer_index]
                if (
                    not is_pending(layer)
                    and layer.sequence_number == number - 1
                    and (layer.has_content or layer.is_blank_key)
                ):
                    previous = (column, layer)
                    break
            target_column = None
            if previous is not None:
                start, previous_layer = previous
                block = self.timeline_block_at(start, layer_index)
                # previous は内容か空白キーのある位置なので、必ずブロックの先頭にある。
                assert block is not None
                exposure = block[2]
                position = previous_layer.__dict__.get("_v1_position")
                imported = previous_layer.__dict__.get("_dense_import")
                signature = (previous_layer.has_content, previous_layer.is_blank_key,
                             previous_layer.sequence_number, previous_layer.sequence_only,
                             previous_layer.blank_start)
                next_column = start + exposure
                following = (self.frames[next_column].layers[layer_index]
                             if next_column < len(self.frames) else None)
                if (position is not None and position.legacy is not None
                        and imported is not None and imported[0] == signature
                        and following is not None and following.has_content
                        and not is_pending(following)):
                    exposure = max(exposure, int(position.legacy.exposure))
                if exposure > 1:
                    # 保持区間の長さは変えず、前半を既存番号、後半を
                    # 追加番号へ分配する。
                    target_column = start + max(1, exposure // 2)
                    target_exposure = start + exposure - target_column
                    previous_layer.__dict__["_v1_position"] = V1Position(
                        replace(snapshot_layer(position.legacy if position is not None
                                               and position.legacy is not None else previous_layer),
                                exposure=target_column - start), key_start=True,
                        blank_start=previous_layer.blank_start)
            if target_column is None:
                visible_columns = [
                    column
                    for column, frame in enumerate(projected)
                    if any(
                        (layer.has_content or layer.is_blank_key)
                        and not layer.sequence_only
                        for layer in frame.layers
                    )
                ]
                target_column = (max(visible_columns) + 1) if visible_columns else 0
            self.ensure_frame_count(target_column + 1)
            target = self.frames[target_column].layers[layer_index]
            occupied = self.timeline_block_at(target_column, layer_index)
            collided = False
            if (target.has_content or target.is_blank_key) and (
                    occupied is None or occupied[1] == target_column):
                collided = True
                target_column = len(self.frames)
                self.ensure_frame_count(target_column + 1)
                target = self.frames[target_column].layers[layer_index]
            if (previous is None and occupied is not None
                    and occupied[1] < target_column):
                # A new explicit key cuts the previous hold, including its tail.
                for index in range(target_column + target_exposure, occupied[1] + occupied[2]):
                    clear_slot(self.frames[index].layers[layer_index])
            # Placing another pending of the same number can split a previous
            # placement; keep that old v1 boundary without splitting dense spans.
            tail_position = (self.frames[target_column + target_exposure].layers[layer_index]
                             if target_column + target_exposure < len(self.frames) else None)
            if tail_position is not None and tail_position.has_content:
                tail_position.__dict__["_v1_position"] = V1Position(
                    snapshot_layer(tail_position), key_start=True)
            copy_drawing_reference(target, copied)
            clear_pending(target)
            target.__dict__["_v1_position"] = V1Position(
                replace(snapshot_layer(target), exposure=target_exposure), key_start=True)
            dense_end = target_column + (1 if collided else target_exposure)
            self.ensure_frame_count(dense_end)
            for index in range(target_column + 1, dense_end):
                replace_slot(self.frames[index], layer_index, target.clone_reference())
            self.set_position_track_properties(
                target_column, layer_index, visible=copied.visible, opacity=copied.opacity,
            )
            clear_slot(
                self.frames[source_column].layers[layer_index]
            )
        if not pending:
            return ChangeSet()
        self._trim_unused_trailing_frames()
        after = len(self.frames)
        structure = ()
        if after != before:
            structure = (FrameStructure("extend" if after > before else "trim",
                                        before, after, min(before, after)),)
        spans = tuple(SheetSpans(index, 0, max(before, after))
                      for index in sorted({index for _, index, _, _ in pending}))
        return ChangeSet((*structure, *spans, Cursor(min(self.current_frame, after - 1))))

    def next_drawing_number(self, layer_index):
        existing_numbers = [
            int(seq)
            for frame in self.frames
            if (frame.layers[layer_index].has_content
                and (seq := frame.layers[layer_index].sequence_number) is not None)
        ]
        existing_numbers.extend(self.frames[0].layers[layer_index]._track.cells)
        return max(existing_numbers, default=0) + 1

    def editable_cursor(self, column, layer_index):
        block = self.timeline_block_at(column, layer_index)
        if block is None or column <= block[1]:
            return ChangeSet()
        old = self.frames[column].layers[layer_index].__dict__.get("_v1_position")
        was_content_key = old is not None and old.key_start and old.legacy is not None and old.legacy.has_content
        return ChangeSet((Cursor(block[1], notify=not was_content_key),))

    def make_position_editable(self, current, layer_index, mode):
        if not self._valid_position(current, layer_index):
            return ChangeSet()
        layer = self.frames[current].layers[layer_index]
        start, stop = self._span_bounds(current, layer_index)
        if layer.has_content:
            changed = False
            if (layer.sequence_number is None and mode == "sheet"
                    and not layer.is_paper and not layer.is_draft):
                number = self.next_drawing_number(layer_index)
                if layer._track.connected:
                    layer._track.cells[number] = layer._cell
                layer.sequence_number = number
                if layer._track.connected:
                    layer.__dict__.pop("_cell", None)
                changed = True
            if layer.is_blank_key:
                # 連番の空セルへ描画した旧データでは両方のフラグが
                # Trueになり得る。内容キーとして即時修復する。
                layer.is_blank_key = False
                clear_pending(layer)
                changed = True
            return ChangeSet((SheetSpans(layer_index, start, stop),)) if changed else ChangeSet()

        block = self.timeline_block_at(current, layer_index)
        retained_number = layer.sequence_number
        next_number = self.next_drawing_number(layer_index)
        if layer._track.connected:
            # A duplicated empty slot may share its local serialization residue.
            # The new drawing must not turn that other slot's PNG into this one.
            layer.detach_cell()

        if block is not None:
            kind, start, exposure = block
            source_layer = self.frames[start].layers[layer_index]
            copied = source_layer.clone()

            self.set_position_track_properties(
                current, layer_index,
                visible=bool(copied.visible), opacity=float(copied.opacity),
                alpha_locked=bool(copied.alpha_locked),
                color_filter_enabled=bool(copied.color_filter_enabled),
                color_filter_rgb=(tuple(copied.color_filter_rgb)
                                  if copied.color_filter_rgb is not None else None),
            )
            layer.image = (
                snapshot_image(copied.image)
                if kind == "content"
                else blank_image()
            )
            layer.blank_start = False
        else:
            # An uncreated cell already owns a transparent image.  Keep it
            # instead of allocating and clearing another full-canvas image at
            # the instant the first stroke begins.
            layer.blank_start = False

        if layer._track.connected:
            # A retained number is serialization residue, never an edit target.
            if retained_number is not None:
                layer.sequence_number = None
                layer.image = blank_image()
            layer._track.cells[next_number] = layer._cell
            layer.sequence_number = next_number
        elif mode == "sheet":
            layer.sequence_number = int(retained_number) if retained_number is not None else next_number
        layer.has_content = True
        if layer._track.connected:
            layer.__dict__.pop("_cell", None)
        layer.is_blank_key = False
        if block is not None and block[0] == "blank":
            for position in range(current + 1, block[1] + block[2]):
                slot = self.frames[position].layers[layer_index]
                slot.sequence_number = layer.sequence_number
                slot.has_content = True
                slot.is_blank_key = False
                slot.blank_start = False
        return ChangeSet((SheetSpans(layer_index, start, stop),))

    def new_sheet_number(self, column, layer_index):
        block = self.timeline_block_at(column, layer_index)
        if block is None or block[0] != "content":
            return ChangeSet()
        _, start, length = block
        source = self.frames[start].layers[layer_index]
        if source.sequence_number is None:
            return ChangeSet()
        numbers = [
            layer.sequence_number
            for frame in self.frames
            if (layer := frame.layers[layer_index]).has_content
            and isinstance(layer.sequence_number, int)
        ]
        numbers.extend(source._track.cells)
        new_number = max(numbers, default=0) + 1
        selected = source.clone()
        selected.detach_cell()
        source._track.cells[new_number] = selected._cell
        selected.share_track_with(source)
        selected.sequence_number = new_number
        if selected._track.connected:
            selected.__dict__.pop("_cell", None)
        selected.cell_name = None
        selected.tween = None
        selected.tween_member = None
        selected.is_blank_key = False
        selected.has_content = True
        clear_pending(selected)
        replace_slot(self.frames[column], layer_index, selected)
        return ChangeSet((SheetSpans(layer_index, start, start + length),
                          Cursor(column), Selection(layer_index)))

    def tween_member_numbers(self, layer_index, length, previous):
        count = max(0, int(length) - 1)
        if previous and len(previous) == count:
            return [int(number) for number in previous]
        used = {
            int(slot.sequence_number)
            for frame in self.frames
            if (slot := frame.layers[layer_index]).sequence_number is not None
        }
        used.update(self.frames[0].layers[layer_index]._track.cells)
        start = max(used, default=0) + 1
        return list(range(start, start + count))

    def reopen_tween(self, column, layer_index, length, image):
        stop = column + length
        if not self._valid_position(column, layer_index) or not column < stop <= len(self.frames):
            return ChangeSet()
        key = self.frames[column].layers[layer_index]
        key.image = to_pma_image(image)
        key.tween = None
        for index in range(column + 1, stop):
            self.frames[index].layers[layer_index] = key.clone_reference()
        return ChangeSet((SheetSpans(layer_index, column, stop),))

    def release_tween(self, column, layer_index, length):
        stop = column + length
        if not self._valid_position(column, layer_index) or not column < stop <= len(self.frames):
            return ChangeSet()
        self.frames[column].layers[layer_index].tween = None
        for index in range(column + 1, stop):
            self.frames[index].layers[layer_index].tween_member = None
        return ChangeSet((SheetSpans(layer_index, column, stop),))

    def install_tween(self, column, layer_index, images, spec, previous_numbers):
        if not self._valid_position(column, layer_index) or not images:
            return ChangeSet()
        images = [to_pma_image(image) for image in images]
        stop = column + len(images)
        tween_id = spec["id"]
        cursor = column if spec["reverse"] else stop - 1
        numbers = self.tween_member_numbers(layer_index, len(images), previous_numbers)
        growth = self.ensure_frame_count(stop)
        for offset, image in enumerate(images):
            layer = self.frames[column + offset].layers[layer_index]
            if offset:
                create_drawing(layer, image, number=numbers[offset - 1])
            else:
                layer.image = image
            layer.has_content = True
            layer.is_blank_key = False
            if offset:
                layer.sequence_number = numbers[offset - 1]
                clear_pending(layer)
                layer.cell_name = None
                layer.tween = None
                layer.tween_member = tween_id
            else:
                layer.tween = spec
                layer.tween_member = None
        return ChangeSet((*growth.changes, SheetSpans(layer_index, column, stop),
                          Cursor(cursor), Selection(layer_index)))

    def apply_sheet_remap(self, start, end, columns, template_frame):
        """Apply all validated remap columns before publishing the change."""
        columns = tuple(columns)
        if not columns or start < 0 or end < start:
            return ChangeSet()
        # 見本のコマは範囲までコマを伸ばした後に読む（HEAD と同じ）。今の末尾より後ろでも
        # end までなら受け付ける。
        frame_count = max(len(self.frames), end + 1)
        for index, states, bank in columns:
            if (not 0 <= template_frame < frame_count
                    or not self.frames or not 0 <= index < len(self.frames[0].layers)
                    or len(states) != end - start + 1):
                return ChangeSet()
            try:
                numbers = {int(state) for state in states if state is not None}
            except (TypeError, ValueError, OverflowError):
                return ChangeSet()
            if any(not 1 <= number <= len(bank) or bank[number - 1] is None
                   for number in numbers):
                return ChangeSet()
        before = len(self.frames)
        spans = []
        for index, states, bank in columns:
            left, _ = self._span_bounds(start, index)
            _, right = self._span_bounds(end, index)
            self._apply_time_remap_states_to_layer(states, start, end, index, bank, template_frame)
            self._keep_unplaced_in_archive(index, bank)
            spans.append(SheetSpans(index, left, right))
        structure = (FrameStructure("extend", before, len(self.frames), before),) if len(self.frames) > before else ()
        return ChangeSet((*structure, *spans, Cursor(start), Selection(columns[0][0])))

    def _keep_unplaced_in_archive(self, layer_index, source_bank):
        """シートで使わなかった絵番号を連番保管セルに残す（使った番号は外す）。"""
        placed = {
            int(frame.layers[layer_index].sequence_number)
            for frame in self.frames
            if layer_index < len(frame.layers)
            and frame.layers[layer_index].has_content
            and frame.layers[layer_index].sequence_number is not None
        }
        track = self.frames[0].layers[layer_index]._track
        for number, image in enumerate(source_bank, 1):
            if image is None:
                continue
            if number in placed:
                self.remove_archive_hint(track, number)
            elif number not in track.legacy_archive_hints:
                track.cells.setdefault(number, Cell(image.copy()))
                self.put_archive_hint(track, number, ArchiveHint())

    def _apply_time_remap_states_to_layer(
        self,
        states,
        start_frame,
        end_frame,
        layer_index,
        source_bank,
        current_frame,
    ):
        """検証済みのセル番号列を1レイヤーのシートへ展開する。"""
        self.ensure_frame_count(end_frame + 1)
        template = self.frames[
            current_frame
        ].layers[layer_index]

        # 対象範囲をいったん明示空セルに戻す。
        for frame_index in range(start_frame, end_frame + 1):
            layer = self.frames[frame_index].layers[layer_index]
            layer.share_track_with(template)
            clear_slot(layer)
            layer.__dict__["_v1_position"] = V1Position(snapshot_layer(layer))

        # The parsed range is already a dense reference column.
        for offset, run_state in enumerate(states):
            target = self.frames[start_frame + offset].layers[layer_index]
            target.share_track_with(template)
            run_start = offset == 0 or states[offset - 1] != run_state
            position = target.__dict__["_v1_position"]
            target.__dict__["_v1_position"] = V1Position(
                position.legacy, key_start=run_start, blank_start=run_state is None and run_start)
            target.blank_start = run_state is None and run_start
            if run_state is None:
                target.image = blank_image()
                target.has_content = False
                target.is_blank_key = True
                target.sequence_number = None
                target.cell_name = None
            else:
                number = int(run_state)
                if target._track.connected:
                    target._track.cells.setdefault(number, Cell(source_bank[number - 1].copy()))
                else:
                    target.image = source_bank[number - 1].copy()
                target.sequence_number = number
                target.has_content = True
                if target._track.connected:
                    target.__dict__.pop("_cell", None)
                target.is_blank_key = False
            if run_start:
                target.__dict__["_v1_position"] = V1Position(
                    snapshot_layer(target), key_start=True, blank_start=run_state is None)


    def _column_count(self):
        return len(self.frames[0].layers) if self.frames else 0

    def _valid_columns(self, indices):
        count = self._column_count()
        return (bool(indices) and all(0 <= index < count for index in indices)
                and all(len(frame.layers) == count for frame in self.frames))

    def _valid_track_slots(self, slots):
        return (bool(slots) and len(slots) == len(self.frames)
                and all(isinstance(slot, Layer) for slot in slots))

    def add_track(self):
        """Append a new empty drawing column with one shared Track."""
        if not self.frames:
            return ChangeSet()
        name = next_layer_name(layer.name for layer in self.layers)
        shared_blank = blank_image()
        slots = [Layer(name, shared_blank.copy()) for _ in self.frames]
        for slot in slots[1:]:
            slot.share_track_with(slots[0])
        slots[0]._track.connected = True
        index = self._column_count()
        return self.insert_track(index, slots, active=index)

    def insert_track(self, index, slots, *, active=None, staged=False):
        """Insert a prepared column. Mixed image imports publish after assembly."""
        slots = tuple(slots)
        count = self._column_count()
        if (not self._valid_track_slots(slots) or not 0 <= index <= count
                or any(len(frame.layers) != count for frame in self.frames)
                or (active is not None and not 0 <= active <= count)):
            return ChangeSet()
        for frame, slot in zip(self.frames, slots):
            frame.layers.insert(index, slot)
        if not staged:
            track = slots[0]._track
            if track.connected and all(slot._track is track for slot in slots):
                self.sync_columns()
            else:
                self.install_legacy_frames(self.frames)
        changes = (TrackStructure("insert", (), (index,)),)
        if active is not None:
            changes += (Selection(active),)
        return ChangeSet(changes)

    def remove_tracks(self, indices, *, active=None):
        indices = tuple(sorted(set(indices)))
        count = self._column_count()
        if not self._valid_columns(indices) or len(indices) == count:
            return ChangeSet()
        if active is None:
            active = min(indices[0], count - len(indices) - 1)
        if not 0 <= active < count - len(indices):
            return ChangeSet()
        for frame in self.frames:
            for index in reversed(indices):
                release_slots([frame.layers[index]])
                frame.layers.pop(index)
        self.sync_columns()
        return ChangeSet((TrackStructure("remove", indices, ()), Selection(active)))

    def duplicate_tracks(self, names):
        """Duplicate indexed columns, including each column's drawing bank."""
        indices = tuple(sorted(names))
        if not self._valid_columns(indices):
            return ChangeSet()
        copies = [(index, self.capture_column(index)) for index in reversed(indices)]
        for index, slots in copies:
            slots[0].name = names[index]
        for index, slots in copies:
            for frame, slot in zip(self.frames, slots):
                frame.layers.insert(index + 1, slot)
        self.sync_columns()
        inserted = tuple(index + offset for offset, index in enumerate(indices, 1))
        return ChangeSet((
            TrackStructure("duplicate", indices, inserted), Selection(inserted[-1]),
        ))

    def move_tracks(self, order):
        """Reorder columns by a permutation of their previous indices."""
        order = tuple(order)
        count = self._column_count()
        if (len(order) != count or sorted(order) != list(range(count))
                or order == tuple(range(count)) or not self._valid_columns(order)
                or self.active_layer_index not in order):
            return ChangeSet()
        active = order.index(self.active_layer_index)
        for frame in self.frames:
            frame.layers = [frame.layers[index] for index in order]
        self.sync_columns()
        return ChangeSet((TrackStructure("move", order, tuple(range(count))), Selection(active)))

    def merge_tracks(self, indices, slots, *, staged=False):
        """Replace consecutive columns with artwork prepared by the caller.

        The canvas can defer normalization until its brush stroke is committed.
        """
        indices = tuple(indices)
        slots = tuple(slots)
        if (len(indices) < 2 or not self._valid_columns(indices)
                or indices != tuple(range(indices[0], indices[-1] + 1))
                or not self._valid_track_slots(slots)):
            return ChangeSet()
        start, stop = indices[0], indices[-1] + 1
        for frame, slot in zip(self.frames, slots):
            release_slots(frame.layers[start:stop])
            frame.layers[start:stop] = [slot]
        if staged:
            self.sync_columns()
        else:
            self.install_legacy_frames(self.frames)
        return ChangeSet((TrackStructure("merge", indices, (start,)), Selection(start)))

    def set_track_properties(self, indices, *, notify_unchanged=False, **settings):
        """Edit column settings, including detached positions awaiting install."""
        indices = tuple(dict.fromkeys(indices))
        if any(field not in TRACK_FIELDS for field in settings):
            raise ValueError("Unknown track property")
        if not self._valid_columns(indices) or not settings:
            return ChangeSet()
        tracks_by_index = {
            index: tuple(dict.fromkeys(frame.layers[index]._track for frame in self.frames))
            for index in indices
        }
        fields_by_track = {
            track: self._changed_track_fields(track, settings)
            for tracks in tracks_by_index.values() for track in tracks
        }
        changes = []
        for index, tracks in tracks_by_index.items():
            affected = {field for track in tracks for field in fields_by_track[track]}
            fields = tuple(field for field in settings if notify_unchanged or field in affected)
            if fields:
                changes.append(TrackProperties((index,), fields))
        for track, fields in fields_by_track.items():
            for field in fields:
                setattr(track, field, settings[field])
        return ChangeSet(tuple(changes))

    @staticmethod
    def _changed_track_fields(track, settings):
        return tuple(field for field, value in settings.items()
                     if getattr(track, field) != value)

    def set_position_track_properties(self, frame, index, **settings):
        """Preserve position-based settings copies during compound cell edits."""
        if any(field not in TRACK_FIELDS for field in settings):
            raise ValueError("Unknown track property")
        if not 0 <= frame < len(self.frames) or not 0 <= index < len(self.frames[frame].layers):
            return ChangeSet()
        track = self.frames[frame].layers[index]._track
        fields = self._changed_track_fields(track, settings)
        for field, value in settings.items():
            setattr(track, field, value)
        return ChangeSet((TrackProperties((index,), fields),)) if fields else ChangeSet()

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
                    image = PmaImage.blank(int(width), int(height))
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
                    copy_image(owner.image) if rescued else owner.image,
                    copy_image(attrs[0]) if rescued else attrs[0],
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
                copy_image(cell.image),
                copy_image(cell.initial_image),
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
        layer.initial_image = copy_image(initial)
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
    image = to_pma_image(image)
    if layer._track.connected:
        if number is None:
            number = max(layer._track.cells, default=0) + 1
        previous = layer._cell
        layer._track.cells[int(number)] = Cell(
            image, copy_image(previous.initial_image),
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
    target.initial_image = copy_image(copied.initial_image)
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
        cell.image = copy_image(owner.image)
        cell.initial_image = copy_image(initial)
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
                layer.image = snapshot_image(image)
            layer.has_content = has_content
            layer.is_blank_key = is_blank
            if track is None or not track.connected:
                layer.cell_name = cell_name
                layer.initial_image = copy_image(initial_image)


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
                          initial_image=copy_image(cell.initial_image),
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
                    payload = replace(payload, image=copy_image(slot.image),
                                      initial_image=copy_image(slot.initial_image),
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


# 段階8-4：Rust の文書を正本にする Document（document_live）は、まだ環境変数
# PMA_CORE_DOCUMENT=1 のときだけ使う。既定は上の Python の Document のまま。
GraphDocument = Document
if __import__("os").environ.get("PMA_CORE_DOCUMENT") == "1":
    from .document_live import Document  # noqa: E402,F811
