"""Live install/projection tests independent of removed sparse Layer inputs."""
from PySide6.QtGui import QImage, QColor

from paintmaskanimator.document import Document, project_v1_state
from paintmaskanimator.legacy_v1 import LegacyLayerV1, expand_legacy, snapshot_frames
from paintmaskanimator.models import Frame
from paintmaskanimator.sheet import sheet_numbers
from paintmaskanimator.timeline_resolution import resolve_exposures
from paintmaskanimator.timeline_span import span_at


def image(color):
    result = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(color))
    return result


def raw(number=None, **kwargs):
    kwargs.setdefault("image", image("red"))
    return LegacyLayerV1("A", sequence_number=number, **kwargs)


def test_install_dense_roundtrip_hidden_payload_and_clone():
    frames = [Frame([raw(7, has_content=True, exposure=3)], 2),
              Frame([raw(19, image=image("cyan"), initial_image=image("green"),
                         cell_name="hidden")]), Frame([raw()])]
    baseline = snapshot_frames(frames)
    doc = Document()
    doc.install_legacy_frames(frames)
    assert sheet_numbers(doc.frames, 0) == [7, 7, 7]
    assert span_at(doc.frames, 0, 2) == ("content", 0, 3)
    projected, _ = project_v1_state(doc.frames)
    assert snapshot_frames(projected) == baseline
    copied = doc.clone_graph()
    assert copied.frames[0].duration == 2
    assert copied.frames[1].layers[0]._track is copied.frames[0].layers[0]._track
    assert copied.frames[0].layers[0]._track is not doc.tracks[0]
    doc.install_legacy_frames(doc.frames)
    assert sheet_numbers(doc.frames, 0) == [7, 7, 7]
    assert snapshot_frames(project_v1_state(doc.frames)[0]) == baseline


def test_pending_sheet_and_resolved_are_distinct():
    frames = [Frame([raw(7, has_content=True, exposure=4)]),
              Frame([raw(99, has_content=True, sequence_only=True, exposure=2)]),
              Frame([raw()]), Frame([raw()])]
    doc = Document()
    doc.install_legacy_frames(frames)
    assert sheet_numbers(doc.frames, 0) == [7, 7, 7, 7]
    assert resolve_exposures(doc.frames).tolist() == [[0], [1], [1], [-1]]
    assert resolve_exposures(doc.clone_graph().frames).tolist() == [[0], [1], [1], [-1]]
    assert doc.placed_numbers(doc.tracks[0]) == {7}
    assert doc.pending_entries(doc.tracks[0])[0].slot.sequence_number == 99
    projected, _ = project_v1_state(doc.frames)
    assert expand_legacy(snapshot_frames(projected)).sheet == expand_legacy(snapshot_frames(frames)).sheet


def test_adjacent_content_merges_but_blank_starts_remain():
    frames = [Frame([raw(1, has_content=True, exposure=2)]), Frame([raw()]),
              Frame([raw(1, has_content=True, exposure=2)]), Frame([raw()]),
              Frame([raw(is_blank_key=True, exposure=2)]), Frame([raw()]),
              Frame([raw(is_blank_key=True)])]
    doc = Document()
    doc.install_legacy_frames(frames)
    assert span_at(doc.frames, 0, 3) == ("content", 0, 4)
    assert span_at(doc.frames, 0, 5) == ("blank", 4, 2)
    assert span_at(doc.frames, 0, 6) == ("blank", 6, 1)


def test_ragged_legacy_hold_is_installed_once_and_preserved_for_save():
    doc = Document()
    frames = [Frame([raw(7, has_content=True, exposure=3)]), Frame([]), Frame([])]
    doc.install_legacy_frames(frames)
    assert sheet_numbers(doc.frames, 0) == [7, 7, 7]
    projected, _ = project_v1_state(doc.frames)
    assert [len(frame.layers) for frame in projected] == [1, 0, 0]


def test_v1_archive_reader_installs_dense_without_live_exposure(tmp_path):
    from paintmaskanimator import project_io
    frames = [Frame([raw(7, has_content=True, exposure=3)]),
              Frame([raw(19, image=image("cyan"), cell_name="hidden")]), Frame([raw()])]
    doc = Document()
    doc.install_legacy_frames(frames)
    metadata = {"format": "PaintMaskAnimatorProject", "format_version": 1,
                "mask_format": 2, "canvas": {"width": 2, "height": 2}, "frames": []}
    path = tmp_path / "dense.pman"
    project_io.write_project_archive(path, metadata, doc.frames)
    _metadata, loaded, _width, _height = project_io.read_project_archive(path)
    reopened = Document()
    reopened.install_legacy_frames(loaded)
    assert sheet_numbers(reopened.frames, 0) == [7, 7, 7]
    assert not hasattr(reopened.frames[0].layers[0], "exposure")
    projected, _ = project_v1_state(reopened.frames)
    assert projected[1].layers[0].cell_name == "hidden"
    assert projected[1].layers[0].image == image("cyan")


def test_projection_keeps_unplaced_bank_attributes_and_settings():
    from paintmaskanimator.models import Cell
    doc = Document()
    track = doc.tracks[0]
    track.visible = False
    track.opacity = 0.5
    track.cells[12] = Cell(image("blue"), image("green"), "unplaced")
    _, archive = project_v1_state(doc.frames)
    saved = archive[(0, 12)]
    assert saved.cell_name == "unplaced"
    assert saved.initial_image == image("green")
    assert saved.image == image("blue")
    assert not saved.visible and saved.opacity == 0.5


def test_single_clone_detaches_hidden_position_payload_and_pending_snapshot():
    doc = Document()
    doc.install_legacy_frames([
        Frame([raw(7, has_content=True, exposure=3)]),
        Frame([raw(99, has_content=True, sequence_only=True, exposure=2,
                   tween={"points": [1]}, initial_image=image("green"))]),
        Frame([raw()])])
    source = doc.frames[1].layers[0]
    copied = source.clone()
    assert copied.__dict__["_dense_import"] == source.__dict__["_dense_import"]
    position = copied.__dict__["_v1_position"]
    position.legacy.tween["points"].append(2)
    position.legacy.image.fill(QColor("black"))
    assert source.__dict__["_v1_position"].legacy.tween == {"points": [1]}
    assert source.__dict__["_v1_position"].legacy.image == image("red")
    copied.image.fill(QColor("blue"))
    assert source.image == image("red")


def test_refresh_and_track_attachment_preserve_pending_visible_exception():
    doc = Document()
    doc.install_legacy_frames([Frame([raw(7, has_content=True)]),
                               Frame([raw(99, has_content=True, sequence_only=True)])])
    baseline = snapshot_frames(project_v1_state(doc.frames)[0])
    doc.install_legacy_frames(doc.frames)
    assert sheet_numbers(doc.frames, 0) == [7, None]
    assert snapshot_frames(project_v1_state(doc.frames)[0]) == baseline
    copied = doc.clone_graph()
    assert sheet_numbers(copied.frames, 0) == [7, None]
    assert snapshot_frames(project_v1_state(copied.frames)[0]) == baseline
