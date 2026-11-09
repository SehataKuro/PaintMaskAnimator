"""Small-image regression coverage for the Track-owned drawing bank."""
import pytest
from PySide6.QtGui import QImage

from paintmaskanimator.document import ArchiveHint, Document, clear_slot, clone_frame_graph, project_v1_state
from paintmaskanimator.models import Cell, Frame, Layer


def image(color=0xff123456):
    result = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(color)
    return result


def slot(number=None, **kwargs):
    return Layer("A", image(), sequence_number=number, **kwargs)


@pytest.mark.parametrize("kind", [{}, {"is_paper": True}, {"is_draft": True}])
def test_install_collects_all_numbers_before_assigning_13_14(kind):
    layers = [slot(3, has_content=True, **kind),
              slot(10, sequence_only=True, **kind), slot(12, **kind),
              slot(has_content=True, **kind), slot(has_content=True, **kind)]
    archive = {(0, 8): slot(8, has_content=True, **kind)}
    doc = Document()
    doc.install_legacy_frames([Frame([layer]) for layer in layers], archive)
    assert [layer.sequence_number for layer in layers] == [3, 10, 12, 13, 14]
    assert set(doc.tracks[0].cells) == {3, 8, 10, 12, 13, 14}
    assert not layers[2].has_content
    assert layers[2].cell_id != id(doc.tracks[0].cells[12])
    assert doc.tracks[0].legacy_archive_hints[8].has_content
    assert doc.tracks[0].cells[8].image is archive[(0, 8)].image


def test_install_uses_current_image_and_first_non_none_attributes():
    first = slot(3, has_content=True, initial_image=image(0xff111111), cell_name="first")
    second = slot(3, has_content=True, initial_image=image(0xff222222), cell_name="second")
    second.image = image(0xffabcdef)
    doc = Document()
    doc.current_frame = 1
    doc.install_legacy_frames([Frame([first]), Frame([second])])
    assert first.cell_id == second.cell_id
    assert first.image.pixel(0, 0) == 0xffabcdef
    assert second.initial_image.pixel(0, 0) == 0xff111111
    assert second.cell_name == "first"
    second.image = image(0xff998877)
    assert doc.tracks[0].cells[3].image is first.image is second.image


def test_retained_only_image_is_rescued_and_projected_without_live_mutation():
    retained = slot(12, is_blank_key=True, cell_name="rescued")
    doc = Document()
    doc.install_legacy_frames([Frame([retained])])
    frames, archive = project_v1_state(doc.frames)
    assert frames is doc.frames
    assert doc.tracks[0].legacy_archive_hints == {}
    assert archive[(0, 12)].image.pixel(0, 0) == retained.image.pixel(0, 0)
    assert archive[(0, 12)].cell_name == "rescued"
    assert retained.is_blank_key and not retained.has_content
    assert retained.sequence_number == 12


def test_clear_preserves_bank_and_projection_uses_same_cell():
    layer = slot(3, has_content=True)
    doc = Document()
    doc.install_legacy_frames([Frame([layer])])
    cell = layer._cell
    clear_slot(layer)
    assert doc.tracks[0].cells[3] is cell
    assert cell.image.pixel(0, 0) == 0xff123456
    assert layer.sequence_number is None
    _, archive = project_v1_state(doc.frames)
    assert archive[(0, 3)].image is cell.image
    assert not archive[(0, 3)]._track.connected


def test_graph_copy_copies_unplaced_cells_once_and_preserves_sharing():
    doc = Document()
    doc.install_legacy_frames([Frame([slot(3, has_content=True)]),
                               Frame([slot(3, has_content=True)])],
                              {(0, 3): slot(3, has_content=True)})
    doc.tracks[0].cells[9] = Cell(image())
    frames, archive = clone_frame_graph(doc.frames)
    track = frames[0].layers[0]._track
    assert track is frames[1].layers[0]._track
    assert frames[0].layers[0]._cell is track.cells[3]
    assert archive == {}
    assert track.legacy_archive_hints == doc.tracks[0].legacy_archive_hints
    assert track.legacy_archive_hints is not doc.tracks[0].legacy_archive_hints
    assert track.cells[9] is not doc.tracks[0].cells[9]
    track.cells[9].image.fill(0xffabcdef)
    assert doc.tracks[0].cells[9].image.pixel(0, 0) == 0xff123456


def test_snapshot_restores_bank_archive_and_is_reusable():
    doc = Document()
    doc.install_legacy_frames([Frame([slot(3, has_content=True)])],
                              {(0, 8): slot(8, has_content=True)})
    doc.tracks[0].cells[9] = Cell(image(), image(), "unplaced")
    snap = doc.snapshot()
    doc.tracks[0].cells[9].image.fill(0xff000000)
    doc.tracks[0].legacy_archive_hints.clear()
    doc.restore(snap)
    assert set(doc.tracks[0].legacy_archive_hints) == {8}
    assert doc.tracks[0].cells[9].image.pixel(0, 0) == 0xff123456
    doc.tracks[0].cells[9].image.fill(0xffffffff)
    doc.restore(snap)
    assert doc.tracks[0].cells[9].image.pixel(0, 0) == 0xff123456


def test_clone_one_drawing_and_slot_have_distinct_copy_contracts():
    doc = Document()
    layer = slot(3, has_content=True, initial_image=image(), cell_name="three")
    doc.install_legacy_frames([Frame([layer])])
    doc.tracks[0].cells[9] = Cell(image())
    copied = layer.clone()
    assert copied.track_id != layer.track_id
    assert copied.cell_id != layer.cell_id
    assert 9 not in copied._track.cells
    copied.image.fill(0xffabcdef)
    copied.cell_name = "copy"
    assert layer.image.pixel(0, 0) == 0xff123456
    assert layer.cell_name == "three"
    position = layer.clone_slot()
    assert position.track_id == layer.track_id and position.cell_id == layer.cell_id


def test_reinstall_connected_graph_does_not_drop_unplaced_bank():
    doc = Document()
    doc.install_legacy_frames([Frame([slot(3, has_content=True)])])
    cell = doc.tracks[0].cells[9] = Cell(image())
    doc.install_legacy_frames(doc.frames)
    assert doc.tracks[0].cells[9] is cell


@pytest.mark.parametrize("save_mode", ["normal", "sync", "background"])
def test_all_save_paths_include_unplaced_bank(tmp_path, monkeypatch, save_mode):
    from types import SimpleNamespace
    from paintmaskanimator import project_io
    from paintmaskanimator.main_window_autosave import AutosaveController

    doc = Document()
    doc.install_legacy_frames([Frame([slot(3, has_content=True)])])
    doc.tracks[0].cells[9] = Cell(image(0xffabcdef), image(), "unplaced")
    metadata = lambda: {"format": "PaintMaskAnimatorProject", "format_version": 1,
                        "mask_format": 2, "canvas": {"width": 2, "height": 2},
                        "frames": []}
    canvas = SimpleNamespace(frames=doc.frames, _document=doc,
                             drawing=False)
    window = SimpleNamespace(canvas=canvas, build_project_metadata=metadata,
                             png_cache=project_io.PngCache())
    owner = AutosaveController(window)
    path = tmp_path / "saved.pman"
    monkeypatch.setattr(owner, "path", lambda: path)
    try:
        if save_mode == "normal":
            project_io.write_project_archive(path, metadata(), doc.frames)
        elif save_mode == "sync":
            owner.save()
        else:
            owner.request_save()
            owner._pending.result(timeout=10)
        data, _, _, _ = project_io.read_project_archive(path)
        archived = data["_loaded_sequence_archive"][(0, 9)]
        assert archived.image.pixel(0, 0) == 0xffabcdef
        assert archived.initial_image.pixel(0, 0) == 0xff123456
        assert archived.cell_name == "unplaced"
        assert doc.tracks[0].legacy_archive_hints == {}
        from zipfile import ZipFile
        reference = tmp_path / "normal.pman"
        project_io.write_project_archive(reference, metadata(), doc.frames)
        with ZipFile(reference) as expected, ZipFile(path) as actual:
            assert actual.namelist() == expected.namelist()
            assert [actual.read(name) for name in actual.namelist()] == [
                expected.read(name) for name in expected.namelist()]
    finally:
        owner.clear()


def test_unplaced_images_attributes_and_membership_change_signature():
    from types import SimpleNamespace
    from paintmaskanimator.main_window_project_io import ProjectIOController

    doc = Document()
    doc.install_legacy_frames([Frame([slot(3, has_content=True)])])
    canvas = SimpleNamespace(frames=doc.frames, _document=doc)
    owner = ProjectIOController(SimpleNamespace(canvas=canvas, build_project_metadata=lambda: {}))
    previous = owner._content_signature()
    cell = doc.tracks[0].cells[9] = Cell(image(), image(), "nine")
    assert owner._content_signature() != previous
    for edit in (lambda: cell.image.fill(0xffabcdef),
                 lambda: cell.initial_image.fill(0xff000000),
                 lambda: setattr(cell, "cell_name", "changed"),
                 lambda: doc.tracks[0].cells.pop(9)):
        previous = owner._content_signature()
        edit()
        assert owner._content_signature() != previous


def test_doc_undo_bytes_count_each_bank_image_and_initial_once():
    from paintmaskanimator.undo_entries import DocUndo

    doc = Document()
    doc.install_legacy_frames([Frame([slot(3, has_content=True)]),
                               Frame([slot(3, has_content=True)])],
                              {(0, 8): slot(8, has_content=True)})
    doc.tracks[0].cells[9] = Cell(image(), image())
    assert DocUndo(doc.snapshot()).nbytes == image().sizeInBytes() * 4


@pytest.mark.parametrize("mode", ["sheet", "sequence"])
def test_painting_retained_slot_allocates_tail_and_keeps_rescued_drawing(monkeypatch, mode):
    from types import SimpleNamespace
    from paintmaskanimator import canvas_key_frame

    doc = Document()
    layer = slot(12, is_blank_key=True)
    doc.install_legacy_frames([Frame([layer])])
    rescued = doc.tracks[0].cells[12]
    canvas = SimpleNamespace(frames=doc.frames, current_frame=0, active_layer_index=0,
                             active_layer=layer, timeline_mode=mode,
                             _document=doc, _playback_active=False,
                             timeline_block_at=lambda *_args: None)
    canvas._next_drawing_number = lambda index: canvas_key_frame.KeyFrameMixin._next_drawing_number(canvas, index)
    monkeypatch.setattr(canvas_key_frame, "blank_image", lambda: image(0))
    assert canvas_key_frame.KeyFrameMixin.ensure_editable_key(canvas)
    assert layer.sequence_number == 13
    assert layer._cell is doc.tracks[0].cells[13]
    assert doc.tracks[0].cells[12] is rescued
    assert rescued.image.pixel(0, 0) == 0xff123456
    layer.image.fill(0xffabcdef)
    assert rescued.image.pixel(0, 0) == 0xff123456


def test_sequence_only_empty_slot_does_not_override_content_attributes():
    pending = slot(3, sequence_only=True, cell_name="ignored", initial_image=image(0xff000000))
    content = slot(3, has_content=True, cell_name="content", initial_image=image(0xffabcdef))
    doc = Document()
    doc.install_legacy_frames([Frame([pending]), Frame([content])])
    assert pending.cell_name == content.cell_name == "content"
    assert pending.initial_image.pixel(0, 0) == 0xffabcdef


def test_new_drawing_detaches_shared_unused_slot_payload():
    from types import SimpleNamespace
    from paintmaskanimator.canvas_key_frame import KeyFrameMixin

    first = slot()
    second = first.clone_slot()
    doc = Document()
    doc.install_legacy_frames([Frame([first]), Frame([second])])
    canvas = SimpleNamespace(frames=doc.frames, current_frame=0, active_layer_index=0,
                             active_layer=first, timeline_mode="sheet",
                             _document=doc, _playback_active=False,
                             timeline_block_at=lambda *_args: None,
                             _next_drawing_number=lambda _index: 1)
    assert KeyFrameMixin.ensure_editable_key(canvas)
    first.image.fill(0xffabcdef)
    assert second.image.pixel(0, 0) == 0xff123456
    assert not second.has_content and second.sequence_number is None


@pytest.fixture
def small_window(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from paintmaskanimator import constants
    from paintmaskanimator.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 2)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 2)
    window = MainWindow()
    # Avoid background work and modal close prompts in these model scenarios.
    window._autosave_timer.stop()
    window._used_color_timer.stop()
    window._title_timer.stop()
    monkeypatch.setattr(window.used_color, "schedule_refresh", lambda: None)
    monkeypatch.setattr(window.used_color, "refresh_with_counter", lambda *_: None)
    window.canvas.install_legacy_frames([
        Frame([slot(1, has_content=True, initial_image=image(0xffaabbcc), cell_name="one")]),
        Frame([slot(2, has_content=True)]),
    ])
    window.canvas.frames[1].layers[0].image = image(0xffabcdef)
    window.canvas.timeline_mode = "sheet"
    window.refresh_ui()
    yield window
    window._closing = True
    window._autosave_timer.stop()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def saved_document(doc, path):
    from paintmaskanimator.project_io import read_project_archive, write_project_archive

    metadata = {"format": "PaintMaskAnimatorProject", "format_version": 1,
                "mask_format": 2, "canvas": {"width": 2, "height": 2}, "frames": []}
    write_project_archive(path, metadata, doc.frames)
    metadata, frames, _, _ = read_project_archive(path)
    restored = Document()
    restored.install_legacy_frames(frames, metadata["_loaded_sequence_archive"])
    return restored


@pytest.mark.parametrize("operation", ["clear", "blank", "menu_delete", "canvas_delete",
                                        "cut", "merge", "overwrite", "remap", "trim"])
def test_last_reference_survives_save_reload_and_recall(small_window, tmp_path, operation):
    window = small_window
    canvas = window.canvas
    doc = canvas._document
    number = 2 if operation in ("merge", "overwrite", "remap", "trim") else 1
    cell = doc.tracks[0].cells[number]
    original = (QImage(cell.image), cell.initial_image, cell.cell_name)
    if operation == "clear":
        clear_slot(canvas.frames[0].layers[0])
    elif operation == "blank":
        clear_slot(canvas.frames[0].layers[0], blank=True)
    elif operation == "menu_delete":
        assert window.timeline_ops.clear_cell(0, 0)
    elif operation == "canvas_delete":
        canvas.delete_frame()
    elif operation == "cut":
        assert window.timeline_ops.cut_cell_to_clipboard(0, 0)
    elif operation == "merge":
        assert window.timeline_ops.merge_clip(0, 1)
    elif operation == "overwrite":
        window.timeline_ops.copy_cell(0, 0, 0, 1)
    elif operation == "remap":
        assert window.time_remap.apply_to_active_layer(
            {"states": [1, 1], "start_frame": 0, "end_frame": 1}, confirm=False)
    else:
        clear_slot(canvas.frames[1].layers[0])
        canvas._trim_unused_trailing_frames()
        assert len(canvas.frames) == 1
    assert not any(layer.has_content and layer.sequence_number == number
                   for frame in doc.frames for layer in frame.layers)
    assert doc.tracks[0].cells[number] is cell
    assert (cell.image, cell.initial_image, cell.cell_name) == original
    # The live bank is available in the sequence UI before a save too.
    window.refresh_ui()
    assert number in window.timeline.table._sequence_numbers_by_row[0]
    restored = saved_document(doc, tmp_path / "roundtrip.pman")
    recovered = restored.tracks[0].cells[number]
    assert (recovered.image, recovered.initial_image, recovered.cell_name) == original
    window.timeline_ops.recall_sequence_number(0, 3, number)
    assert canvas.frames[3].layers[0]._cell is cell
    assert canvas.frames[3].layers[0].image == original[0]


def test_sequence_delete_removes_all_references_and_bank_with_undo(small_window, tmp_path):
    canvas = small_window.canvas
    canvas._ensure_frame_count(4)
    retained = canvas.frames[3].layers[0]
    retained.sequence_number = 1
    retained.is_blank_key = True
    canvas._document.put_archive_hint(canvas._document.tracks[0], 1, ArchiveHint())
    assert canvas.delete_sequence_entry(0, 1)
    assert 1 not in canvas._document.tracks[0].cells
    assert 1 not in canvas._document.tracks[0].legacy_archive_hints
    assert all(frame.layers[0].sequence_number != 1 for frame in canvas.frames)
    assert 1 not in saved_document(canvas._document, tmp_path / "deleted.pman").tracks[0].cells
    canvas.undo()
    assert canvas._document.tracks[0].cells[1].cell_name == "one"
    assert canvas.frames[3].layers[0].sequence_number == 1
    canvas.redo()
    assert 1 not in canvas._document.tracks[0].cells


def test_number_cycles_insertion_and_image_move_keep_all_cell_attributes(small_window):
    from paintmaskanimator.document import apply_number_mapping

    canvas = small_window.canvas
    track = canvas._document.tracks[0]
    one, two = track.cells[1], track.cells[2]
    three = track.cells[3] = Cell(image(0), image(0xff111111), "empty-unplaced")
    canvas._document.put_archive_hint(canvas._document.tracks[0], 1, ArchiveHint())
    canvas._ensure_frame_count(3)
    canvas.frames[2].layers[0].sequence_number = 3
    apply_number_mapping(canvas.frames, {}, 0, {1: 2, 2: 3, 3: 1})
    assert track.cells == {2: one, 3: two, 1: three}
    assert [f.layers[0].sequence_number for f in canvas.frames] == [2, 3, 1]
    assert 2 in track.legacy_archive_hints
    assert track.cells[2] is one
    assert canvas.insert_sequence_blank(0, 1)
    assert track.cells[3] is one and track.cells[4] is two and track.cells[1] is three
    assert canvas.frames[2].layers[0].sequence_number == 1
    assert track.cells[2].image.pixelColor(0, 0).alpha() == 0
    # Moving an unplaced entry moves the complete Cell, including initial/name.
    assert canvas.move_sequence_image(0, 1, 3)
    assert track.cells[3] is three
    assert track.cells[2] is one
    assert track.cells[4] is two
    assert 3 in track.legacy_archive_hints
    assert track.cells[3] is three
    assert three.cell_name == "empty-unplaced"
    assert three.initial_image.pixel(0, 0) == 0xff111111


def test_layer_structure_undo_and_duplicate_keep_unplaced_bank_and_archive(small_window):
    window = small_window
    canvas = window.canvas
    canvas.add_layer()
    track = canvas._document.tracks[1]
    track.cells[9] = Cell(image(0xffabcdef), image(), "unplaced")
    canvas._document.put_archive_hint(track, 9, ArchiveHint())
    window.layers.move_row([0], 1)
    assert canvas._document.tracks[0] is track
    assert 9 in canvas._document.tracks[0].legacy_archive_hints
    assert canvas._document.tracks[0].cells[9] is track.cells[9]
    canvas.active_layer_index = 0
    canvas.delete_layer()
    assert all(9 not in t.cells for t in canvas._document.tracks)
    canvas.undo()
    restored = canvas._document.tracks[0]
    assert restored.cells[9].cell_name == "unplaced"
    assert 9 in restored.legacy_archive_hints
    assert canvas._document.tracks[0].cells[9] is restored.cells[9]
    canvas.redo()
    canvas.undo()
    window.layers.duplicate_rows([1])
    original, copied = canvas._document.tracks[:2]
    assert copied.cells[9] is not original.cells[9]
    copied.cells[9].image.fill(0xff000000)
    assert original.cells[9].image.pixel(0, 0) == 0xffabcdef
    assert 9 in copied.legacy_archive_hints
    assert canvas._document.tracks[1].cells[9] is copied.cells[9]
    canvas.undo()
    assert len(canvas._document.tracks) == 2
    canvas.undo()  # undo layer restoration/deletion chain
    canvas.undo()  # undo row move
    canvas.undo()  # undo add_layer (captures its full bank for redo)
    canvas.redo()
    assert canvas._document.tracks[1].cells[9].cell_name == "unplaced"


@pytest.mark.parametrize("blank", [False, True])
def test_retained_drawing_round_trip_keeps_original_attributes(small_window, tmp_path, blank):
    canvas = small_window.canvas
    layer = canvas.frames[0].layers[0]
    old = layer._cell
    layer.has_content = False
    layer.is_blank_key = blank
    assert canvas.ensure_editable_key()
    assert layer.sequence_number == 3
    layer.image.fill(0xff000000)
    restored = saved_document(canvas._document, tmp_path / "retained.pman")
    recovered = restored.tracks[0].cells[1]
    assert recovered.image == old.image
    assert recovered.initial_image == old.initial_image
    assert recovered.cell_name == "one"
    assert restored.tracks[0].cells[3].image.pixel(0, 0) == 0xff000000


def test_snapshot_freezes_every_cell_payload_and_position_metadata():
    doc = Document()
    layer = slot(1, has_content=True, initial_image=image(), cell_name="placed",
                 tween={"points": [1, 2]})
    doc.install_legacy_frames([Frame([layer])])
    doc.tracks[0].cells[9] = Cell(image(), image(), "unplaced")
    snap = doc.snapshot()
    copied = doc.clone_graph()
    for cell in doc.tracks[0].cells.values():
        cell.image.fill(0xff000000)
        cell.image = image(0xffffffff)
        cell.initial_image.fill(0xff000000)
        cell.initial_image = image(0xffffffff)
        cell.cell_name = "changed"
    doc.tracks[0].cells.pop(9)
    doc.tracks[0].cells[10] = Cell(image())
    layer.tween["points"].append(3)
    for track in (snap.tracks[0], copied.tracks[0]):
        assert set(track.cells) == {1, 9}
        for cell in track.cells.values():
            assert cell.image.pixel(0, 0) == 0xff123456
            assert cell.initial_image.pixel(0, 0) == 0xff123456
        assert track.cells[9].cell_name == "unplaced"
    assert snap.frames[0].layers[0].tween == {"points": [1, 2]}
    assert copied.frames[0].layers[0].tween == {"points": [1, 2]}


def test_refresh_is_read_only_and_unplaced_saved_signature_round_trip(small_window, tmp_path):
    window = small_window
    cell = window.canvas._document.tracks[0].cells[9] = Cell(image(), image(), "nine")
    window.project.mark_saved()
    before = window.project._content_signature()
    for _ in range(3):
        window.refresh_ui()
    assert window.project._content_signature() == before
    cell.cell_name = "edited"
    assert window.project._content_signature() != before
    assert window.project.write(tmp_path / "saved.pman")
    assert window.project._content_signature() == window.project._saved_signature
    assert not window.project.has_unsaved_changes()


def test_remap_reads_registered_edits_instead_of_stale_import_cache(small_window):
    window = small_window
    canvas = window.canvas
    canvas._sequence_source_bank = [image(0xff000000), image(0xffffffff)]
    canvas._sequence_source_bank_layer_index = 0
    cell = canvas._document.tracks[0].cells[1]
    cell.image = image(0xff112233)
    bank = window.time_remap._time_remap_source_bank(0)
    assert bank[0] == cell.image
    assert window.time_remap.apply_to_active_layer(
        {"states": [2, 1], "start_frame": 0, "end_frame": 1}, confirm=False)
    assert canvas.frames[1].layers[0]._cell is cell
    assert cell.image.pixel(0, 0) == 0xff112233
    assert cell.cell_name == "one"


def test_connected_drawing_has_one_owner_and_missing_numbers_are_not_repaired():
    doc = Document()
    layer = slot(1, has_content=True)
    doc.install_legacy_frames([Frame([layer])])
    assert "_cell" not in layer.__dict__
    layer.sequence_number = 99
    with pytest.raises(KeyError, match="99"):
        _ = layer.image
    assert set(doc.tracks[0].cells) == {1}


def test_layout_paper_insertion_remaps_existing_archive(small_window, monkeypatch):
    from paintmaskanimator import layout_paper

    window = small_window
    doc = window.canvas._document
    track = doc.tracks[0]
    doc.put_archive_hint(track, 1, ArchiveHint())
    monkeypatch.setattr(layout_paper, "load_image", lambda _: image())
    window.insert_layout_paper({"id": "test"})
    assert doc.tracks[1] is track
    assert 1 not in doc.tracks[0].legacy_archive_hints
    assert 1 in doc.tracks[1].legacy_archive_hints
    assert doc.tracks[1].cells[1] is track.cells[1]
    assert doc.tracks[0].connected
    assert doc.frames[0].layers[0].sequence_number == 1


def test_small_snapshot_structure_scales_with_drawings_not_positions():
    doc = Document()
    doc.install_legacy_frames([
        Frame([slot(1, has_content=True), slot(2, has_content=True)])
        for _ in range(12)
    ])
    for track in doc.tracks:
        track.cells[9] = Cell(image())
    snapshot = doc.snapshot()
    assert len({id(layer._track) for frame in snapshot.frames for layer in frame.layers}) == 2
    assert sum(len(track.cells) for track in snapshot.tracks) == 4
    assert len({layer.cell_id for frame in snapshot.frames for layer in frame.layers}) == 2


@pytest.mark.parametrize("operation,preferred", [("move", 1), ("paste", 0), ("paste", 1)])
def test_cross_track_collision_keeps_legacy_pixels_and_attribute_priority(small_window, operation, preferred):
    from paintmaskanimator.document import create_drawing

    window = small_window
    canvas = window.canvas
    source_cell = canvas._document.tracks[0].cells[1]
    canvas.add_layer()
    destination = canvas.frames[0].layers[1]
    create_drawing(destination, image(0xff998877), number=1)
    destination.cell_name = "destination-first"
    destination.initial_image = image(0xff445566)
    destination_cell = destination._cell
    canvas.current_frame = preferred
    if operation == "move":
        assert canvas.move_timeline_cell(0, 0, 1, 1)
    else:
        assert window.timeline_ops.copy_cell_to_clipboard(1, 0)
        assert window.timeline_ops.paste_cell_from_clipboard(0, 1)
    assert canvas.frames[1].layers[1]._cell is destination_cell
    expected = source_cell.image if preferred == 1 else image(0xff998877)
    assert destination_cell.image == expected
    assert destination_cell.cell_name == "destination-first"
    assert destination_cell.initial_image.pixel(0, 0) == 0xff445566
    assert canvas._document.tracks[0].cells[1] is source_cell
    canvas.undo()
    assert canvas._document.tracks[1].cells[1].image.pixel(0, 0) == 0xff998877


def test_new_animation_cell_preserves_legacy_initial_residue_without_editing_old_bank(small_window):
    window = small_window
    canvas = window.canvas
    old = canvas._document.tracks[0].cells[1]
    assert window.timeline_ops.new_animation_cell(0, 0)
    created = canvas.frames[0].layers[0]._cell
    assert created is not old
    assert created.initial_image == old.initial_image
    assert created.cell_name is None
    created.initial_image.fill(0xff000000)
    assert old.initial_image.pixel(0, 0) == 0xffaabbcc
    assert old.cell_name == "one"


@pytest.mark.parametrize("blank", [False, True])
def test_recall_retained_number_uses_rescued_bank_not_empty_slot(small_window, blank):
    window = small_window
    canvas = window.canvas
    layer = canvas.frames[0].layers[0]
    cell = layer._cell
    layer.has_content = False
    layer.is_blank_key = blank
    assert 0 not in canvas.sequence_entry_columns(0)
    window.refresh_ui()
    window.timeline_ops.recall_sequence_number(0, 3, 1)
    recalled = canvas.frames[3].layers[0]
    assert recalled.has_content and not recalled.is_blank_key
    assert recalled._cell is cell
    assert recalled.cell_name == "one"


def test_exposure_undo_moves_slots_without_replacing_the_track(small_window):
    from paintmaskanimator.undo_entries import ExposureUndo

    canvas = small_window.canvas
    track = canvas._document.tracks[0]
    track.cells[9] = Cell(image(), image(), "unplaced")
    canvas._document.put_archive_hint(canvas._document.tracks[0], 1, ArchiveHint())
    clear_slot(canvas.frames[0].layers[0])
    canvas.apply_undo_entry(ExposureUndo(0, 0, 1, 2, 0, 0, ((1, 0),)))
    assert canvas._document.tracks[0] is track
    assert canvas.frames[0].layers[0]._track is track
    assert set(track.cells) == {1, 2, 9}
    assert 1 in track.legacy_archive_hints
    assert canvas._document.tracks[0].cells[1] is track.cells[1]
    assert canvas.frames[0].layers[0]._cell is track.cells[2]
