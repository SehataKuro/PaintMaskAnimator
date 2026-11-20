"""Document editing works without a QApplication or any canvas observers."""

import pytest

from paintmaskanimator.document import Document
from paintmaskanimator.document_changes import ChangeSet, Selection, TrackProperties, TrackStructure
from paintmaskanimator.models import Frame, Layer


def make_document():
    doc = Document()
    doc.install_legacy_frames([
        Frame([Layer(name, None) for name in ("A", "B", "C")])
        for _ in range(2)
    ])
    return doc


def test_edit_track_properties_changes_shared_track_once():
    doc = make_document()
    changes = doc.set_track_properties((1, 1), is_paper=True, alpha_locked=True)
    assert changes == ChangeSet((TrackProperties((1,), ("is_paper", "alpha_locked")),))
    assert doc.frames[0].layers[1]._track is doc.frames[1].layers[1]._track
    assert all(frame.layers[1].is_paper and frame.layers[1].alpha_locked for frame in doc.frames)
    assert not doc.set_track_properties((1,), is_paper=True, alpha_locked=True)


def test_invalid_commands_leave_columns_and_settings_untouched():
    doc = make_document()
    before = tuple(doc.tracks)
    slots = [Layer("Merged", None) for _ in doc.frames]
    assert not doc.insert_track(1, slots[:1])
    assert not doc.remove_tracks((0, 1, 2))
    assert not doc.duplicate_tracks({0: "Copy", 3: "Invalid"})
    assert not doc.move_tracks((2, 2, 1))
    assert not doc.merge_tracks((0, 2), slots)
    assert not doc.set_track_properties((0, -1), visible=False)
    with pytest.raises(ValueError, match="Unknown track property"):
        doc.set_track_properties((0,), visible=False, cells=None)
    assert tuple(doc.tracks) == before
    assert [[slot.name for slot in frame.layers] for frame in doc.frames] == [["A", "B", "C"]] * 2
    assert all(slot.visible for frame in doc.frames for slot in frame.layers)


def test_track_structure_events_and_ownership():
    doc = make_document()
    changes = doc.duplicate_tracks({0: "Copy", 2: "Other"})
    assert changes == ChangeSet((TrackStructure("duplicate", (0, 2), (1, 4)), Selection(4)))
    assert [track.name for track in doc.tracks] == ["A", "Copy", "B", "C", "Other"]
    assert doc.tracks[0] is not doc.tracks[1]
    assert all(frame.layers[1]._track is doc.tracks[1] for frame in doc.frames)
    assert doc.move_tracks((4, 0, 1, 2, 3)) == ChangeSet((
        TrackStructure("move", (4, 0, 1, 2, 3), (0, 1, 2, 3, 4)), Selection(1),
    ))
    assert doc.remove_tracks((0, 2)) == ChangeSet((
        TrackStructure("remove", (0, 2), ()), Selection(0),
    ))
    slots = [Layer("Merged", None) for _ in doc.frames]
    assert doc.merge_tracks((0, 1), slots) == ChangeSet((
        TrackStructure("merge", (0, 1), (0,)), Selection(0),
    ))
    assert [track.name for track in doc.tracks] == ["Merged", "C"]


def test_inserted_track_is_installed_in_every_frame():
    doc = make_document()
    slots = [Layer("Draft", None, is_draft=True) for _ in doc.frames]
    assert doc.insert_track(1, slots) == ChangeSet((TrackStructure("insert", (), (1,)),))
    assert doc.tracks[1].is_draft
    assert doc.tracks[1].connected
    assert all(frame.layers[1]._track is doc.tracks[1] for frame in doc.frames)
