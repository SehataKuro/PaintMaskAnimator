"""Changes returned by document operations, in publication order."""
from dataclasses import dataclass
from typing import Literal, Union


@dataclass(frozen=True)
class TrackStructure:
    """Affected indices in the pre-edit and post-edit column order."""

    operation: Literal["insert", "remove", "duplicate", "merge", "move"]
    before: tuple[int, ...]
    after: tuple[int, ...]


@dataclass(frozen=True)
class TrackProperties:
    indices: tuple[int, ...]
    fields: tuple[str, ...]


@dataclass(frozen=True)
class Selection:
    """Select this column using the consumer’s existing cursor transition."""

    layer_index: int


@dataclass(frozen=True)
class FrameStructure:
    """Frame counts before and after an edit, with its first affected index."""

    operation: Literal["extend", "duplicate", "trim", "restore"]
    before: int
    after: int
    start: int


@dataclass(frozen=True)
class SheetSpans:
    """Affected interval on a track, in half-open frame coordinates."""

    layer_index: int
    start: int
    stop: int


@dataclass(frozen=True)
class Cursor:
    """Apply the canvas's existing frame transition."""

    frame: int
    notify: bool = True


@dataclass(frozen=True)
class ExposureIndexReset:
    """The legacy frame graph was normalized."""


@dataclass(frozen=True)
class DrawingPixels:
    """Changed artwork in image coordinates. Empty rectangles mean replacement."""

    layer_index: int
    number: int | None
    frame: int | None
    rects: tuple[tuple[int, int, int, int], ...]


@dataclass(frozen=True)
class DrawingAttributes:
    layer_index: int
    number: int | None
    frame: int | None
    fields: tuple[str, ...]


@dataclass(frozen=True)
class DocumentRestored:
    width: int
    height: int


@dataclass(frozen=True)
class ChangeSet:
    changes: tuple[Union[
        TrackStructure, TrackProperties, Selection, FrameStructure,
        SheetSpans, Cursor, ExposureIndexReset, DrawingAttributes, DrawingPixels, DocumentRestored,
    ], ...] = ()

    def __bool__(self):
        return bool(self.changes)
