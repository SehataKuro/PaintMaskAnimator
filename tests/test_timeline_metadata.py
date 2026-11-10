"""Dense metadata ownership, revisions and independent position snapshots."""
import pytest
from PySide6.QtGui import QImage

from paintmaskanimator import native
from paintmaskanimator.models import Frame, Layer
from paintmaskanimator.timeline_resolution import resolve_exposures
from paintmaskanimator.timeline_metadata import METADATA_FIELDS


def cell(**kwargs):
    return Layer("A", QImage(), **kwargs)


def test_metadata_has_one_owner_and_clone_is_independent():
    layer = cell(has_content=True, sequence_number=12,
                 sequence_only=True, cell_name="セル十二")
    data = layer.__dict__["_timeline_metadata"]
    assert isinstance(data, native._native.TimelineCell)
    for name in METADATA_FIELDS:
        assert name not in layer.__dict__
        assert getattr(layer, name) == getattr(data, name)
    cloned = layer.clone()
    cloned.blank_start = True
    cloned.sequence_number = None
    cloned.cell_name = "別セル"
    assert not layer.blank_start
    assert layer.sequence_number == 12
    assert layer.cell_name == "セル十二"
    assert cloned._cell is not layer._cell
    assert cloned.__dict__["_timeline_metadata"] is not data
    with pytest.raises(AttributeError):
        layer.exposure = 3


def test_edit_insert_delete_move_and_exposure_resolution():
    frames = [Frame([cell(has_content=True, sequence_number=n)]) for n in (1, 1, 2, 2)]
    assert resolve_exposures(frames).tolist() == [[0], [0], [2], [2]]
    frames.insert(1, Frame([cell()]))
    assert resolve_exposures(frames).tolist() == [[0], [-1], [2], [3], [3]]
    moved = frames.pop(2)
    frames.insert(1, moved)
    assert resolve_exposures(frames).tolist() == [[0], [0], [-1], [3], [3]]
    del frames[2]
    assert resolve_exposures(frames).tolist() == [[0], [0], [2], [2]]


def test_revision_changes_only_when_exposure_metadata_changes():
    layer = cell()
    revision = Layer.timeline_revision
    layer.has_content = False
    layer.blank_start = False
    assert Layer.timeline_revision == revision
    layer.has_content = True
    layer.sequence_number = 3
    layer.blank_start = True
    assert Layer.timeline_revision == revision + 3
    layer.image = QImage()
    assert Layer.timeline_revision == revision + 3
    layer.set_pending(True)
    assert Layer.timeline_revision == revision + 4


def test_rust_path_does_not_pack_numpy_inputs(monkeypatch):
    frames = [Frame([cell(has_content=True, sequence_number=1)]), Frame([]), Frame([cell()])]

    def unexpected(*args, **kwargs):
        pytest.fail("Rust metadata must not be repacked as NumPy inputs")

    monkeypatch.setattr(native.np, "ascontiguousarray", unexpected)
    assert resolve_exposures(frames).tolist() == [[0], [-1], [-1]]
    assert resolve_exposures([]).shape == (0, 0)


@pytest.mark.parametrize("value", [2**100, -(2**100)])
def test_out_of_range_metadata_is_rejected_without_replacing_storage(value):
    layer = cell(has_content=True, sequence_number=2)
    metadata = layer.__dict__["_timeline_metadata"]
    with pytest.raises(OverflowError):
        layer.sequence_number = value
    assert layer.__dict__["_timeline_metadata"] is metadata
    assert layer.sequence_number == 2
