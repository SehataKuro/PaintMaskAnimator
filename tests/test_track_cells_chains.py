"""Regression chains on one live document, using tiny v1 legacy projects."""
import pytest
from PySide6.QtGui import QColor, QImage, QPainter

from paintmaskanimator.models import Frame, Layer
from paintmaskanimator.project_io import write_project_archive
from test_track_cells import image, small_window  # noqa: F401


@pytest.mark.parametrize("kind", [{}, {"is_draft": True}, {"is_paper": True}],
                         ids=["normal", "draft", "paper"])
@pytest.mark.parametrize("mode", ["sequence", "sheet"])
def test_load_duplicate_split_paint_move_paste_delete_undo_save_reload(
        small_window, tmp_path, monkeypatch, kind, mode):  # noqa: F811
    window = small_window
    canvas = window.canvas
    retirements = []
    original_setattr = Layer.__setattr__

    def record_retirement(layer, name, value):
        if (name == "has_content" and not value and layer.has_content
                and layer._track.connected and layer.sequence_number in layer._track.cells):
            retirements.append(layer.sequence_number)
        original_setattr(layer, name, value)

    monkeypatch.setattr(Layer, "__setattr__", record_retirement)
    # Archive and retained numbers must participate in load-time allocation.
    frames = [Frame([Layer("A", image(), has_content=True, exposure=3,
                           initial_image=image(0xff112233), cell_name="legacy", **kind),
                     Layer("B", image(0), **kind)])]
    frames.extend(Frame([Layer("A", image(0), **kind), Layer("B", image(0), **kind)])
                  for _ in range(3))
    frames[3].layers[0].sequence_number = 10
    frames[3].layers[0].is_blank_key = True
    archive = {(0, 8): Layer("A", image(0xff8899aa), has_content=True,
                             sequence_number=8, cell_name="archived", **kind)}
    path = tmp_path / "legacy.pman"
    metadata = {"format": "PaintMaskAnimatorProject", "format_version": 1,
                "mask_format": 2, "canvas": {"width": 2, "height": 2},
                "frames": [], "timeline_mode": mode}
    write_project_archive(path, metadata, frames, archive)
    window.project.open(path)
    assert canvas.timeline_mode == "sheet"  # Existing open policy ignores saved mode.
    assert not window.project.has_unsaved_changes()
    assert set(canvas._document.tracks[0].cells) == {8, 10, 11}
    assert canvas.frames[0].layers[0].sequence_number == 11
    assert canvas.frames[0].layers[0].exposure == 3
    # Sheet gestures are exercised after both file modes have been loaded.
    canvas.timeline_mode = "sheet"
    window.refresh_ui()
    window.project.mark_saved()

    def check(painted=False, dirty=True):
        tracks = canvas._document.tracks
        assert retirements == []
        assert window.project.has_unsaved_changes() == dirty
        assert tracks[0].is_paper == bool(kind.get("is_paper"))
        assert tracks[0].is_draft == bool(kind.get("is_draft"))
        assert set(tracks[0].cells) == {8, 10, 11}
        assert tracks[0].cells[8].image.pixel(0, 0) == 0xff8899aa
        assert tracks[0].cells[8].cell_name == "archived"
        assert tracks[0].cells[10].image.pixelColor(0, 0).alpha() == 0
        assert tracks[0].cells[10].initial_image is None
        assert tracks[0].cells[10].cell_name is None
        assert tracks[0].cells[11].image.pixel(0, 0) == (
            0xffabcdef if painted else 0xff123456)
        assert tracks[0].cells[11].initial_image.pixel(0, 0) == 0xff112233
        assert tracks[0].cells[11].cell_name == "legacy"
        for frame in canvas.frames:
            for layer in frame.layers:
                if layer.has_content or layer.sequence_only:
                    assert "_cell" not in layer.__dict__
                    assert layer._cell is layer._track.cells[layer.sequence_number]
                assert "_content_origin" not in layer.__dict__

    check(dirty=False)
    canvas.current_frame = 0
    canvas.active_layer_index = 0
    assert canvas.add_frame(True)
    check()
    assert canvas.frames[1].layers[0].sequence_number == 11
    assert canvas.frames[1].layers[0].exposure == 3
    assert window.project.has_unsaved_changes()
    assert window.timeline_ops.split_clip(1, 2)
    check()
    assert [canvas.frames[i].layers[0].exposure for i in (1, 2)] == [1, 2]
    assert canvas.frames[2].layers[0].sequence_number == 11

    canvas.current_frame = 2
    canvas.ensure_editable_key()  # Existing keys need no conversion.
    canvas.push_layer_undo()
    painter = QPainter(canvas.active_layer.image)
    painter.fillRect(0, 0, 2, 2, QColor(0xffabcdef))
    painter.end()
    check(True)
    canvas.undo()
    check()
    canvas.redo()
    check(True)
    assert canvas.move_timeline_cell(2, 0, 5, 0)
    check(True)
    assert canvas.frames[5].layers[0].sequence_number == 11
    assert canvas.frames[5].layers[0].exposure == 2
    window.refresh_ui()
    assert window.timeline_ops.copy_cell_to_clipboard(1, 5)
    assert window.timeline_ops.paste_cell_from_clipboard(0, 0)
    check(True)
    assert canvas.frames[0].layers[1].sequence_number == 11
    assert canvas._document.tracks[1].cells[11].image.pixel(0, 0) == 0xffabcdef
    assert window.timeline_ops.clear_cell(0, 0)
    check(True)
    assert not any(f.layers[1].has_content for f in canvas.frames)
    assert 11 in canvas._document.tracks[1].cells
    canvas.undo()
    check(True)
    assert canvas.frames[0].layers[1].has_content
    canvas.redo()
    check(True)
    assert not any(f.layers[1].has_content for f in canvas.frames)
    assert window.project.has_unsaved_changes()
    # Sequence deletion is the only operation that removes an unplaced drawing.
    assert canvas.delete_sequence_entry(1, 11)
    assert 11 not in canvas._document.tracks[1].cells
    canvas.undo()
    check(True)
    assert canvas._document.tracks[1].cells[11].cell_name == "legacy"
    canvas.redo()
    assert 11 not in canvas._document.tracks[1].cells
    canvas.undo()
    check(True)

    def state():
        return (
            [(frame.duration, [(layer.sequence_number, layer.exposure,
                                layer.has_content, layer.is_blank_key, layer.sequence_only)
                               for layer in frame.layers]) for frame in canvas.frames],
            [{n: (QImage(cell.image), QImage(cell.initial_image)
                   if cell.initial_image is not None else None, cell.cell_name)
              for n, cell in track.cells.items()} for track in canvas._document.tracks],
        )

    before_save = state()
    saved = tmp_path / "chain.pman"
    assert window.project.write(saved)
    check(True, dirty=False)
    assert state() == before_save
    assert not window.project.has_unsaved_changes()
    window.project.open(saved)
    check(True, dirty=False)
    assert state() == before_save
    assert not window.project.has_unsaved_changes()
    assert canvas._document.tracks[1].cells[11].image.pixel(0, 0) == 0xffabcdef
    assert canvas._document.tracks[1].cells[11].cell_name == "legacy"


def test_share_cannot_replace_connected_cell(small_window):  # noqa: F811
    layer = small_window.canvas.frames[0].layers[0]
    original = layer._cell
    detached = Layer("other", image(0xff000000), has_content=True, sequence_number=1)
    with pytest.raises(ValueError, match="connected drawing"):
        layer.share_cell_with(detached)
    assert layer._cell is original
    assert "_cell" not in layer.__dict__
    layer.share_cell_with(layer.clone_slot())
    assert "_cell" not in layer.__dict__


@pytest.mark.parametrize("operation", ["first_paint", "clone", "new_number", "undo_view", "save_view",
                                       "remap", "sequence_blank", "number_mapping", "link"])
def test_registration_consumes_local_cell(small_window, operation):  # noqa: F811
    from paintmaskanimator.document import clear_slot, project_v1_state

    canvas = small_window.canvas
    source = canvas.frames[0].layers[0]
    if operation == "first_paint":
        canvas._ensure_frame_count(3)
        canvas.current_frame = 2
        assert canvas.ensure_editable_key()
        target = canvas.active_layer
        assert target.sequence_number == 3
    elif operation == "clone":
        target = source.clone()
        assert target._cell is not source._cell
    elif operation == "new_number":
        assert small_window.timeline_ops.new_number_here(0, 0)
        target = canvas.frames[0].layers[0]
        assert target.sequence_number == 3
        assert target._cell is not canvas._document.tracks[0].cells[1]
    elif operation == "remap":
        assert small_window.time_remap.apply_to_active_layer(
            {"states": [2, 1], "start_frame": 0, "end_frame": 1}, confirm=False)
        target = canvas.frames[1].layers[0]
    elif operation == "sequence_blank":
        assert canvas.insert_sequence_blank(0, 2)
        target = canvas.frames[-1].layers[0]
        assert target.sequence_only
    elif operation == "number_mapping":
        from paintmaskanimator.document import apply_number_mapping

        source.__dict__["_cell"] = source._cell
        source.sequence_number = None
        apply_number_mapping(canvas.frames, canvas._sequence_archive, 0, {}, unnumbered_start=3)
        target = source
    elif operation == "link":
        canvas.frames[1].layers[0] = source.clone()
        canvas.link_layer_tracks()
        target = canvas.frames[1].layers[0]
    else:
        clear_slot(source)
        if operation == "undo_view":
            _, target = canvas._resolve_numbered_target(0, 0, 1)
        else:
            _, archive = project_v1_state(canvas.frames, canvas._sequence_archive)
            target = archive[(0, 1)]
        assert target.cell_name == "one"
        assert target.initial_image.pixel(0, 0) == 0xffaabbcc
    assert target._track.connected
    assert target._cell is target._track.cells[target.sequence_number]
    assert "_cell" not in target.__dict__
