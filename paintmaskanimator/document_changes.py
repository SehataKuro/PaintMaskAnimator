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
class ChangeSet:
    changes: tuple[Union[TrackStructure, TrackProperties, Selection], ...] = ()

    def __bool__(self):
        return bool(self.changes)
