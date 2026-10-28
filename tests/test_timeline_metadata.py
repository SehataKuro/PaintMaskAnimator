"""Timeline migration must preserve editing and independent undo snapshots."""
import pytest
from PySide6.QtGui import QImage

from paintmaskanimator import native
from paintmaskanimator.models import Frame, Layer
from paintmaskanimator.timeline_resolution import resolve_exposures



def cell(**kwargs):
    return Layer("A", QImage(), **kwargs)


def test_metadata_has_one_owner_and_clone_is_independent():
    layer = cell(has_content=True, exposure=8, sequence_number=12,
                 sequence_only=True, cell_name="セル十二")
    data = layer.__dict__["_timeline_metadata"]
    assert isinstance(data, native._native.TimelineCell)
    for name in ("has_content", "exposure", "is_blank_key", "sequence_number",
                 "sequence_only", "cell_name"):
        assert name not in layer.__dict__
        assert getattr(layer, name) == getattr(data, name)
    cloned = layer.clone()
    cloned.exposure = 3
    cloned.sequence_number = None
    cloned.cell_name = "別セル"
    assert layer.exposure == 8
    assert layer.sequence_number == 12
    assert layer.cell_name == "セル十二"
    assert cloned.__dict__["_timeline_metadata"] is not data


def test_edit_insert_delete_move_and_exposure_resolution():
    frames = [Frame([cell(has_content=True, exposure=4, sequence_number=1)]),
              Frame([cell()]), Frame([cell(is_blank_key=True)]), Frame([cell()])]
    assert resolve_exposures(frames).tolist() == [[0], [0], [-1], [-1]]
    frames[2].layers[0].is_blank_key = False
    assert resolve_exposures(frames).tolist() == [[0], [0], [0], [0]]
    frames.insert(1, Frame([cell(has_content=True, exposure=2, sequence_number=2)]))
    assert resolve_exposures(frames).tolist() == [[0], [1], [1], [-1], [-1]]
    moved = frames.pop(1)
    frames.insert(3, moved)
    assert moved.layers[0].sequence_number == 2
    assert resolve_exposures(frames).tolist() == [[0], [0], [0], [3], [3]]
    del frames[3]
    frames[0].layers[0].exposure = 1
    assert resolve_exposures(frames).tolist() == [[0], [-1], [-1], [-1]]


def test_revision_changes_only_when_exposure_metadata_changes():
    layer = cell()
    revision = Layer.timeline_revision
    layer.has_content = False
    layer.exposure = 1
    assert Layer.timeline_revision == revision
    layer.has_content = True
    layer.exposure = 3
    layer.is_blank_key = True
    assert Layer.timeline_revision == revision + 3


def test_rust_path_does_not_pack_numpy_inputs(monkeypatch):
    frames = [Frame([cell(has_content=True, exposure=3)]), Frame([]), Frame([cell()])]

    def unexpected(*args, **kwargs):
        pytest.fail("Rust metadata must not be repacked as NumPy inputs")

    monkeypatch.setattr(native, "resolve_exposures", unexpected)
    assert resolve_exposures(frames).tolist() == [[0], [0], [0]]
    assert resolve_exposures([]).shape == (0, 0)


@pytest.mark.parametrize("name", ["exposure", "sequence_number"])
@pytest.mark.parametrize("value", [2**100, -(2**100)])
def test_out_of_range_metadata_is_rejected_without_replacing_storage(name, value):
    layer = cell(has_content=True, exposure=3, sequence_number=2)
    metadata = layer.__dict__["_timeline_metadata"]
    with pytest.raises(OverflowError):
        setattr(layer, name, value)
    assert layer.__dict__["_timeline_metadata"] is metadata
    assert layer.exposure == 3
    assert layer.sequence_number == 2
