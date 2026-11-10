"""Pure v1 boundary preparation. Nothing here installs or mutates live slots.

Images are opaque copyable payloads; this module does not import Qt or Rust.
The sheet and image-resolution columns deliberately have different pending rules.
"""
from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass, fields, replace
from typing import Any


@dataclass(frozen=True)
class LegacyLayerV1:
    name: str = ""
    image: Any = None
    visible: bool = True
    opacity: float = 1.0
    is_paper: bool = False
    has_content: bool = False
    alpha_locked: bool = False
    exposure: int = 1
    color_filter_enabled: bool = False
    color_filter_rgb: tuple | None = None
    is_blank_key: bool = False
    sequence_number: int | None = None
    sequence_only: bool = False
    is_draft: bool = False
    cell_name: str | None = None
    tween: dict | None = None
    tween_member: str | None = None
    initial_image: Any = None


def snapshot_layer(layer) -> LegacyLayerV1:
    """Detach payloads/settings from a raw, layout, restored or imported slot."""
    values = {}
    original = getattr(layer, "__dict__", {}).get("_legacy_input")
    for field in fields(LegacyLayerV1):
        value = (original.exposure if original is not None and field.name == "exposure"
                 else getattr(layer, field.name, field.default))
        if field.name in ("image", "initial_image"):
            value = copy(value) if value is not None else None
        else:
            value = deepcopy(value)
        values[field.name] = value
    return LegacyLayerV1(**values)


@dataclass(frozen=True)
class DenseReference:
    kind: str = "EMPTY"  # CELL / BLANK / EMPTY
    number: int | None = None


EMPTY = DenseReference()
BLANK = DenseReference("BLANK")


@dataclass(frozen=True)
class V1Position:
    """Original key boundary, blank start, pending and invisible save payload."""
    legacy: LegacyLayerV1 | None = None
    key_start: bool = False
    blank_start: bool = False
    pending: bool = False


@dataclass(frozen=True)
class LegacyExpansion:
    sheet: tuple[tuple[DenseReference, ...], ...]
    resolved: tuple[tuple[DenseReference, ...], ...]
    sheet_keys: tuple[tuple[int, ...], ...]
    resolve_keys: tuple[tuple[int, ...], ...]
    positions: tuple[tuple[V1Position, ...], ...]


def snapshot_frames(frames):
    """Preserve ragged shape; durations belong to Frame and stay with the caller."""
    return tuple(tuple(snapshot_layer(slot) for slot in frame.layers) for frame in frames)


def expand_legacy(rows, *, number_unnumbered=False) -> LegacyExpansion:
    """Expand old raw rows, truncating at each new key and at document end.

    Optional numbering happens once per old content key, per column, above every
    retained/pending number. Installation/bank conflict resolution remains the
    caller's responsibility. The default preserves unnumbered raw sheet output.
    """
    rows = tuple(tuple(snapshot_layer(slot) for slot in row) for row in rows)
    width = max(map(len, rows), default=0)
    if number_unnumbered:
        rows = [list(row) for row in rows]
        for li in range(width):
            next_number = max((row[li].sequence_number for row in rows
                               if li < len(row) and row[li].sequence_number is not None),
                              default=0) + 1
            for row in rows:
                if li < len(row) and row[li].has_content and row[li].sequence_number is None:
                    row[li] = replace(row[li], sequence_number=next_number)
                    next_number += 1
    positions = tuple(tuple(
        V1Position(row[li], bool(row[li].has_content or row[li].is_blank_key),
                   bool(row[li].is_blank_key and not row[li].has_content),
                   row[li].sequence_only) if li < len(row) else V1Position()
        for li in range(width)) for row in rows)

    def expand(resolve):
        result = [[EMPTY] * width for _ in rows]
        keys = [[-1] * width for _ in rows]
        for li in range(width):
            current, key, end = EMPTY, -1, 0
            for column, row in enumerate(rows):
                slot = row[li] if li < len(row) else None
                if slot is not None and (resolve or not slot.sequence_only):
                    if slot.has_content:
                        current = DenseReference("CELL", slot.sequence_number)
                        key, end = column, column + max(1, int(slot.exposure))
                    elif slot.is_blank_key:
                        current = EMPTY if resolve else BLANK
                        key = -1 if resolve else column
                        end = 0 if resolve else column + max(1, int(slot.exposure))
                if column < end:
                    result[column][li], keys[column][li] = current, key
        return tuple(map(tuple, result)), tuple(map(tuple, keys))

    sheet, sheet_keys = expand(False)
    resolved, resolve_keys = expand(True)
    return LegacyExpansion(sheet, resolved, sheet_keys, resolve_keys, positions)


def compress_v1(sheet, positions=None):
    """Project dense references into old rows without touching source payloads.

    Exact unedited input wins over normalization (including ragged rows and invalid
    raw lengths). Otherwise runs break at old key/blank markers, each tween/member
    position and pending position. Incompatible pending edits are rejected rather
    than silently changing visible references; callers must move/clear pending
    metadata as part of an edit plan.
    """
    sheet = tuple(map(tuple, sheet))
    width = max(map(len, sheet), default=0)
    if any(len(row) != width for row in sheet):
        raise ValueError("dense sheet must be rectangular")
    if positions is not None:
        positions = tuple(map(tuple, positions))
        if len(positions) != len(sheet) or any(len(row) != width for row in positions):
            raise ValueError("position shape differs from sheet")
        original = tuple(tuple(pos.legacy for pos in row if pos.legacy is not None)
                         for row in positions)
        # Missing slots can only be a suffix in old ragged rows.
        baseline = expand_legacy(original)
        markers_match = baseline.positions and all(
            len(old_row) == len(new_row) and all(
                (old.key_start, old.blank_start, old.pending, old.legacy is None)
                == (new.key_start, new.blank_start, new.pending, new.legacy is None)
                for old, new in zip(old_row, new_row))
            for old_row, new_row in zip(baseline.positions, positions))
        if baseline.sheet == sheet and (markers_match or not sheet):
            return tuple(tuple(snapshot_layer(slot) for slot in row) for row in original)
    else:
        positions = tuple(tuple(V1Position() for _ in row) for row in sheet)
    rows = [[replace(snapshot_layer(pos.legacy), sequence_only=pos.pending)
             if pos.legacy is not None else LegacyLayerV1(sequence_only=pos.pending)
             for pos in row] for row in positions]
    for li in range(width):
        column = 0
        while column < len(sheet):
            state, pos = sheet[column][li], positions[column][li]
            slot = rows[column][li]
            if pos.pending:
                # A pending slot cannot itself become a visible v1 key. Keep its
                # payload and extend the preceding compatible key through it.
                if state.kind != "EMPTY":
                    for start in range(column - 1, -1, -1):
                        previous = rows[start][li]
                        if previous.sequence_only:
                            continue
                        if previous.has_content or previous.is_blank_key:
                            reference = (DenseReference("CELL", previous.sequence_number)
                                         if previous.has_content else BLANK)
                            if reference == state:
                                rows[start][li] = replace(previous, exposure=column - start + 1)
                            break
                column += 1
                continue
            end = column + 1
            if state.kind != "EMPTY":
                while end < len(sheet) and sheet[end][li] == state:
                    marker = positions[end][li]
                    previous = positions[end - 1][li]
                    if (marker.key_start or marker.blank_start or marker.pending
                            or (marker.legacy and (marker.legacy.tween is not None
                                                   or marker.legacy.tween_member is not None))
                            or (previous.legacy and (previous.legacy.tween is not None
                                                     or previous.legacy.tween_member is not None))):
                        break
                    end += 1
            if state.kind not in ("CELL", "BLANK", "EMPTY"):
                raise ValueError(f"unknown dense kind: {state.kind}")
            rows[column][li] = replace(
                slot, has_content=state.kind == "CELL", is_blank_key=state.kind == "BLANK",
                exposure=end - column,
                sequence_number=state.number if state.kind == "CELL" else slot.sequence_number,
            )
            for held in range(column + 1, end):
                rows[held][li] = replace(rows[held][li], has_content=False,
                                         is_blank_key=False, exposure=1)
            column = end
    result = tuple(map(tuple, rows))
    if expand_legacy(result).sheet != sheet:
        raise ValueError("pending metadata conflicts with edited dense sheet")
    return result


def legacy_layer_to_slot(layer: LegacyLayerV1):
    """Wrap raw v1 input in a detached slot for the installation boundary.

    The DTO has no Track/Cell. Keep its sparse length in _legacy_input until
    Document.install_legacy_frames expands it into dense references; never attach
    an exposure property to the live Layer.
    """
    from .models import Layer

    copied = snapshot_layer(layer)
    slot = Layer(**{field.name: getattr(copied, field.name)
                    for field in fields(LegacyLayerV1) if field.name != "exposure"})
    slot.__dict__["_legacy_input"] = copied
    return slot


def raw_v1_slot(*args, **kwargs):
    """Build a detached input for the v1 reader; never a live exposure owner."""
    return legacy_layer_to_slot(LegacyLayerV1(*args, **kwargs))
