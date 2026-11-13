"""Dense timeline ABI contract, independent of the unfinished live Layer switch."""
from types import SimpleNamespace

import numpy as np
import pytest

from paintmaskanimator import native


def cell(number=None, *, blank=False, start=False, pending=False):
    result = native._native.TimelineCell()
    result.has_content = number is not None
    result.sequence_number = number
    result.is_blank_key = blank
    result.blank_start = start
    result.sequence_only = pending
    return result


def keys(rows):
    return np.frombuffer(native._native.resolve_timeline(rows), dtype=np.int64).tolist()


def test_dense_references_and_separate_blank_starts():
    rows = [[cell(1)], [cell(1)], [cell()], [cell(1)],
            [cell(blank=True, start=True)], [cell(blank=True)],
            [cell(blank=True, start=True)], [cell(2)]]
    entries = native._native.sheet_entries(rows)
    assert [(row[0].kind, row[0].number, row[0].key) for row in entries] == [
        (2, 1, 0), (2, 1, 0), (0, None, -1), (2, 1, 3),
        (1, None, 4), (1, None, 4), (1, None, 6), (2, 2, 7),
    ]
    assert keys(rows) == [0, 0, -1, 3, -1, -1, -1, 7]


@pytest.mark.parametrize("rows, expected", [
    ([], []), ([[], []], [[], []]),
])
def test_empty_shapes(rows, expected):
    assert native._native.sheet_entries(rows) == expected
    assert native._native.resolve_timeline(rows) == b""
    frames = [SimpleNamespace(layers=[]) for _ in rows]
    assert native.resolve_timeline(frames).shape == (len(rows), 0)


def test_ragged_rows_do_not_inherit_previous_references():
    rows = [[cell(1), cell(2)], [], [cell(1)], [cell(1), cell(2)]]
    assert keys(rows) == [0, 0, -1, -1, 2, -1, 2, 3]
    entries = native._native.sheet_entries(rows)
    assert [[entry.key for entry in row] for row in entries] == [
        [0, 0], [-1, -1], [2, -1], [2, 3],
    ]


def test_pending_sheet_and_image_snapshots_are_explicitly_different():
    visible = [[cell(7)], [cell(7)], [cell(7)]]
    resolved = [[cell(7)], [cell(99, pending=True)], [cell()]]
    assert keys(visible) == [0, 0, 0]
    assert keys(resolved) == [0, 1, -1]
    assert [row[0].number for row in native._native.sheet_entries(visible)] == [7, 7, 7]
    # Pending is not filtered inside Rust; the boundary supplies the visible value.
    assert native._native.sheet_entries(resolved)[1][0].number == 99


def test_wrapper_passes_only_metadata_and_tween_does_not_split_numbers():
    class Slot:
        def __init__(self, metadata, **position):
            self._timeline_metadata = metadata
            self.__dict__.update(position)

        @property
        def image(self):
            raise AssertionError("image must not cross the Rust boundary")

        @property
        def initial_image(self):
            raise AssertionError("initial image must not cross the Rust boundary")

        @property
        def cell_name(self):
            raise AssertionError("drawing name must not cross the Rust boundary")

    frames = [SimpleNamespace(layers=[slot]) for slot in [
        Slot(cell(1)), Slot(cell(1), tween={"id": "t", "length": 2}),
        Slot(cell(1), tween_member="t"),
    ]]
    assert native.resolve_timeline(frames).tolist() == [[0], [0], [0]]
    assert [row[0].key for row in native.sheet_entries(frames)] == [0, 0, 0]
    assert frames[1].layers[0].tween == {"id": "t", "length": 2}
    assert frames[2].layers[0].tween_member == "t"


def test_removed_fields_and_old_buffer_api_are_absent():
    metadata = cell(1)
    for field in ("exposure", "cell_name"):
        assert not hasattr(metadata, field)
        with pytest.raises(AttributeError):
            setattr(metadata, field, 1)
    assert not hasattr(native._native, "resolve_exposures")
    assert "resolve_exposures" not in native.REQUIRED_API
    assert not hasattr(native, "resolve_exposures")


@pytest.mark.parametrize("version", [None, 0, 1, 3, "2"])
def test_old_or_incompatible_backend_is_rejected_even_with_all_methods(version):
    backend = SimpleNamespace(**{name: lambda: None for name in native.REQUIRED_API})
    if version is not None:
        backend.TIMELINE_API_VERSION = version
    with pytest.raises(ImportError, match=r"pip install ./native.*timeline API"):
        native.validate_backend(backend)


def test_current_backend_and_missing_method_validation():
    assert native._native.TIMELINE_API_VERSION == native.TIMELINE_API_VERSION == 2
    native.validate_backend(native._native)
    backend = SimpleNamespace(**{name: lambda: None for name in native.REQUIRED_API
                                 if name != "sheet_entries"},
                              TIMELINE_API_VERSION=2)
    with pytest.raises(ImportError, match="missing API: sheet_entries"):
        native.validate_backend(backend)


def test_sequence_number_stays_signed_i64():
    metadata = cell(1)
    with pytest.raises(OverflowError):
        metadata.sequence_number = 2**63
    assert metadata.sequence_number == 1
