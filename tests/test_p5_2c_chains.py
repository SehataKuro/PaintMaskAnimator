"""C4: legacy drawing semantics survive column, pending, undo and save chains."""
import json
from copy import deepcopy
from zipfile import ZipFile

import pytest
from PySide6.QtGui import QImage

from paintmaskanimator.document import project_v1_state
from paintmaskanimator.legacy_v1 import raw_v1_slot as Layer
from dense_fixtures import clear_range
from paintmaskanimator.models import Frame
from paintmaskanimator.project_io import legacy_fixture_build_v1_payload
from test_pending_slots import assert_pending_graph
from test_track_cells import image, small_window  # noqa: F401


def payload(path):
    """Compare v1 drawing metadata and encoded pixels across real save paths."""
    with ZipFile(path) as archive:
        metadata = json.loads(archive.read("project.json"))
        if metadata["format_version"] == 2:
            return (metadata["tracks"], metadata["frame_durations"],
                    {name: archive.read(name) for name in archive.namelist()
                     if name.endswith(".png") and not name.startswith("thumbnail")})
        return (metadata["frames"], metadata["sequence_archive"],
                {name: archive.read(name) for name in archive.namelist()
                 if name.endswith(".png") and not name.startswith("thumbnail")})


def legacy_expected_payload(canvas, kind):
    """Independent raw-v1 DTO for the fixed legacy outcome, without projection."""
    names = [track.name for track in canvas._document.tracks]
    frames = []
    for column in range(7):
        layers = []
        for index, name in enumerate(names):
            number = (13 if index == 0 else 12) if column == 4 else None
            if column == 6 and index == 0:
                number = 2
            layers.append(Layer(name, image(0), sequence_number=number,
                                is_blank_key=number is not None, **kind))
        frames.append(Frame(layers))
    archive = {}
    for index, numbers in enumerate(({1, 2, 9, 13}, {1, 8, 12})):
        for number in numbers:
            explicit = number == (9 if index == 0 else 8)
            color = 0xff8899aa if explicit else (0xff123456 if number == 1 else 0)
            archive[index, number] = Layer(
                names[index], image(color), has_content=True, sequence_number=number,
                initial_image=image(0xff112233) if explicit else None,
                cell_name="eight" if explicit else None, **kind)
    return legacy_fixture_build_v1_payload(*project_v1_state(frames, archive))


def graph(canvas):
    return (
        canvas.current_frame,
        [[(s.sequence_number, s.has_content, s.is_blank_key, s.exposure, s.sequence_only)
          for s in frame.layers] for frame in project_v1_state(canvas.frames)[0]],
        [{n: (QImage(c.image), QImage(c.initial_image) if c.initial_image is not None else None,
              c.cell_name) for n, c in track.cells.items()}
         for track in canvas._document.tracks],
        [dict(track.legacy_archive_hints) for track in canvas._document.tracks],
    )


@pytest.mark.parametrize("boundary", ["legacy", "v2"])
@pytest.mark.parametrize("kind", [{}, {"is_draft": True}, {"is_paper": True}],
                         ids=["normal", "draft", "paper"])
@pytest.mark.parametrize("save", ["normal", "sync", "background"])
def test_unplaced_undo_columns_pending_modes_docundo_save_reload(
        small_window, tmp_path, monkeypatch, kind, save, boundary):  # noqa: F811
    if boundary == "legacy":
        from legacy_save_boundary import install_legacy_save_boundary
        install_legacy_save_boundary(monkeypatch)
    window = small_window
    canvas = window.canvas
    # Retained blank 12 is rescued, never reused; explicit archive 8 has attributes.
    slots = [Layer("A", image(), has_content=True, sequence_number=1, exposure=4, **kind)]
    slots += [Layer("A", image(0), **kind) for _ in range(4)]
    slots[4].sequence_number = 12
    slots[4].is_blank_key = True
    archived = Layer("A", image(0xff8899aa), has_content=True, sequence_number=8,
                     initial_image=image(0xff112233), cell_name="eight", **kind)
    canvas.install_legacy_frames([Frame([slot]) for slot in slots], {(0, 8): archived})
    canvas.timeline_mode = "sheet"
    canvas.current_frame = 0
    canvas.active_layer_index = 0
    canvas.undo_stack.clear()
    canvas.redo_stack.clear()
    # Record a drawing edit while placed, then undo it after its slot is cleared.
    canvas.push_layer_undo()
    canvas.active_layer.image = image(0xffabcdef)
    clear_range(canvas, 0, 4)
    canvas.current_frame = 3
    canvas.undo()
    assert canvas.current_frame == 3
    assert canvas._document.tracks[0].cells[1].image.pixel(0, 0) == 0xff123456
    assert not any(s.layers[0].has_content for s in canvas.frames)
    canvas.redo()
    assert canvas.current_frame == 3
    assert canvas._document.tracks[0].cells[1].image.pixel(0, 0) == 0xffabcdef
    canvas.undo()

    window.layers.duplicate_rows([0])
    original, copied = canvas._document.tracks
    assert copied.cells[8] is not original.cells[8]
    assert copied.cells[8].initial_image == original.cells[8].initial_image
    window.layers.move_row([1], 0)
    assert canvas._document.tracks == [copied, original]
    canvas.active_layer_index = 0
    window.timeline_ops.set_mode("sequence")
    assert canvas.insert_sequence_blank(0, 1)
    # Insertion shifts bank 8/retained 12 to 9/13 only in the copied column.
    assert set(copied.cells) == {1, 2, 9, 13}
    assert set(original.cells) == {1, 8, 12}
    assert canvas.frames[4].layers[0].sequence_number == 13
    assert canvas.frames[4].layers[1].sequence_number == 12
    assert_pending_graph(canvas._document)
    before = graph(canvas)
    undo_count = len(canvas.undo_stack)
    window.timeline_ops.set_mode("sheet")
    assert len(canvas.undo_stack) == undo_count + 1
    # Last sheet entry is at 4; pending blank at 5 collides, so use len(frames).
    # Design §4.1(3–4) explicitly preserves this legacy collision rule.
    assert canvas.current_frame == 6
    assert canvas.frames[6].layers[0].sequence_number == 2
    assert canvas.frames[6].layers[0].is_blank_key
    assert not copied.pending_slots
    assert canvas._document.unplaced_numbers(copied) == {1, 2, 9, 13}
    assert canvas._document.unplaced_numbers(original) == {1, 8, 12}
    after = graph(canvas)
    canvas.undo()
    assert graph(canvas) == before
    assert_pending_graph(canvas._document)
    canvas.redo()
    assert graph(canvas) == after
    assert_pending_graph(canvas._document)
    for index, number in [(0, 9), (1, 8)]:
        track = canvas._document.tracks[index]
        assert track.is_draft == bool(kind.get("is_draft"))
        assert track.is_paper == bool(kind.get("is_paper"))
        assert track.cells[number].cell_name == "eight"
        assert track.cells[number].initial_image.pixel(0, 0) == 0xff112233
        assert track.cells[number].image.pixel(0, 0) == 0xff8899aa

    reference = tmp_path / "normal.pman"
    assert window.project.write(reference)
    expected = payload(reference)
    if boundary == "legacy":
        raw_frames, raw_archive, raw_pngs = legacy_expected_payload(canvas, kind)
        assert expected[:2] == (raw_frames, raw_archive)
        assert set(expected[2]) == {name for name, _, _ in raw_pngs}
        for name, pixels, _ in raw_pngs:
            assert QImage.fromData(expected[2][name]).convertToFormat(pixels.format()) == pixels
    else:
        tracks = expected[0]
        assert [t["sheet"] for t in tracks] == [
            [None, None, None, None, "BLANK", None, "BLANK"],
            [None, None, None, None, "BLANK", None, None]]
        assert [{entry["number"] for entry in t["cells"]} for t in tracks] == [
            {1, 2, 9, 13}, {1, 8, 12}]
        for index, number in ((0, 9), (1, 8)):
            entry = next(c for c in tracks[index]["cells"] if c["number"] == number)
            assert entry["cell_name"] == "eight"
            assert QImage.fromData(expected[2][entry["image"]]).pixel(0, 0) == 0xff8899aa
            assert QImage.fromData(expected[2][entry["initial"]]).pixel(0, 0) == 0xff112233
    assert graph(canvas) == after
    assert not window.project.has_unsaved_changes()
    destination = tmp_path / f"{save}.pman"
    monkeypatch.setattr(window.autosave, "path", lambda: destination)
    try:
        if save == "normal":
            assert window.project.write(destination)
        elif save == "sync":
            window.autosave.save()
        else:
            window.autosave.request_save()
            window.autosave._pending.result(timeout=10)
        assert payload(destination) == expected
        assert graph(canvas) == after
        window.project.open(destination)
        assert canvas.timeline_mode == "sheet"
        # Existing open policy normalizes when there is no numbered content key
        # (main_window_project_io.open); blank keys alone do not suppress it.
        # No content keys: sorted bank order gives these exact legacy maps.
        if boundary == "legacy":
            mappings = [{1: 1, 2: 2, 9: 3, 13: 4}, {1: 1, 8: 2, 12: 3}]
            loaded_slots = deepcopy(after[1])
            for row in loaded_slots:
                for index, values in enumerate(row):
                    row[index] = (mappings[index].get(values[0], values[0]), *values[1:])
            loaded_cells = [{mappings[index][n]: values for n, values in cells.items()}
                            for index, cells in enumerate(after[2])]
            assert graph(canvas)[:3] == (6, loaded_slots, loaded_cells)
            # v1 projection turns bank-only/blank drawings into explicit raw entries.
            assert [set(t.legacy_archive_hints) for t in canvas._document.tracks] == [
                {1, 2, 3, 4}, {1, 2, 3}]
        else:
            assert graph(canvas) == after
        assert_pending_graph(canvas._document)
        assert not window.project.has_unsaved_changes()
        loaded = graph(canvas)
        roundtrip = tmp_path / "roundtrip.pman"
        assert window.project.write(roundtrip)
        window.project.open(roundtrip)
        assert graph(canvas) == loaded
        stable = tmp_path / "stable.pman"
        assert window.project.write(stable)
        assert payload(stable) == payload(roundtrip)
    finally:
        window.autosave.clear()
