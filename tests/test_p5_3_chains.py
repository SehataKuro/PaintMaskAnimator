"""D: real v1 files and dense edit/save/pending chains, without new goldens."""
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QMessageBox

from paintmaskanimator import constants, project_io
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.models import Frame
from paintmaskanimator.sheet import sheet_from_frames
from test_p5_3_characterization import image, install, numbers, observe, payload, row, slot
from test_pending_slots import assert_pending_graph


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 16)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 16)
    w = MainWindow()
    qtbot.addWidget(w)
    for timer in (w._autosave_timer, w._used_color_timer, w._title_timer):
        timer.stop()
    yield w
    w._closing = True


def state(c):
    result = observe(c)
    return {key: result[key] for key in ("numbers", "render", "spans", "tween", "tracks")}


def save_visible(window, path):
    """Each serialized key stays within its visible same-number/blank run."""
    c = window.canvas
    before = state(c)
    assert window.project.write(path)
    saved = payload(path)
    entries = sheet_from_frames(c.frames)
    for column, frame in enumerate(saved["metadata"]["frames"]):
        for li, layer in enumerate(frame["layers"]):
            if layer.get("sequence_only") or not (layer["has_content"] or layer["is_blank_key"]):
                continue
            entry = entries[column][li]
            end = column
            while end < len(entries) and entries[end][li].number == entry.number:
                end += 1
            assert 1 <= layer["exposure"] <= end - column
    assert state(c) == before  # projection never edits the live graph
    return saved


def roundtrip(window, tmp_path, label):
    c = window.canvas
    saved = save_visible(window, tmp_path / f"{label}.pman")
    before = state(c)
    c.undo()
    c.redo()
    assert state(c) == before
    assert save_visible(window, tmp_path / f"{label}-redo.pman") == saved
    assert window.project.open(tmp_path / f"{label}.pman")
    after = state(c)
    for key in ("numbers", "render", "spans", "tween"):
        assert after[key] == before[key]
    assert_pending_graph(c._document)
    save_visible(window, tmp_path / f"{label}-open.pman")
    return after


def test_old_v1_open_fixed_semantics_and_pending_recall(window, tmp_path):
    # Sparse legacy input goes straight to the v1 writer, never through dense projection.
    slots = [slot(1, "red", has_content=True, exposure=3,
                  initial_image=image("blue"), cell_name="one"), slot(), slot(),
             slot(12, "yellow", is_blank_key=True, exposure=2), slot(),
             slot(2, "green", has_content=True, sequence_only=True), slot(),
             slot(3, "blue", has_content=True)]
    source = tmp_path / "old-v1.pman"
    project_io.write_project_archive(source, {
        "format": "PaintMaskAnimatorProject", "format_version": 1, "mask_format": 2,
        "canvas": {"width": 16, "height": 16}, "timeline_mode": "sequence", "frames": [],
    }, [Frame([s]) for s in slots],
        {(0, 9): slot(9, "cyan", has_content=True, exposure=2, cell_name="archive")})
    metadata, loaded, _, _ = project_io.read_project_archive(source)
    c = window.canvas
    c.install_legacy_frames(loaded, metadata["_loaded_sequence_archive"])
    c.timeline_mode = "sequence"
    c.current_frame = 0
    c.active_layer_index = 0
    window.refresh_ui()
    assert numbers(c.frames) == [[v] for v in (
        "CELL:1", "CELL:1", "CELL:1", "BLANK", "BLANK", "EMPTY", "EMPTY", "CELL:3")]
    assert [c.timeline_block_at(i, 0) for i in range(3)] == [("content", 0, 3)] * 3
    assert [c.timeline_block_at(i, 0) for i in (3, 4)] == [("blank", 3, 2)] * 2
    assert [c.resolve_key_frame(i, 0) for i in range(3)] == [0, 0, 0]
    track = c._document.tracks[0]
    assert set(track.cells) == {1, 2, 3, 9, 12}
    assert set(track.legacy_archive_hints) == {9}
    assert c._document.unplaced_numbers(track) == {2, 9, 12}
    assert [(e.column, e.slot.sequence_number) for e in c._document.pending_entries(track)] == [(5, 2)]
    assert track.cells[1].image == image("red")
    assert track.cells[1].initial_image == image("blue")
    assert track.cells[1].cell_name == "one"
    assert track.cells[9].image == image("cyan")
    assert track.cells[9].cell_name == "archive"
    # v1 archive entries do not serialize exposure; the reader defaults to 1.
    assert track.legacy_archive_hints[9].exposure == 1
    assert c.composite(0, white=False).pixelColor(0, 0).name() == "#ff0000"
    assert c.composite(3, white=False).pixelColor(0, 0).alpha() == 0
    assert c.resolve_key_frame(5, 0) == 5
    assert c.composite(5, white=False).pixelColor(0, 0).name() == "#008000"
    save_visible(window, tmp_path / "pending.pman")
    metadata, loaded, _, _ = project_io.read_project_archive(tmp_path / "pending.pman")
    c.install_legacy_frames(loaded, metadata["_loaded_sequence_archive"])
    assert [(e.column, e.slot.sequence_number) for e in c._document.pending_entries(c._document.tracks[0])] == [(5, 2)]
    before = state(c)
    window.timeline_ops.set_mode("sheet")
    placed = state(c)
    assert not c._document.tracks[0].pending_slots
    assert any(e.number == 2 for r in sheet_from_frames(c.frames) for e in r)
    c.undo()
    assert state(c) == before
    c.redo()
    assert state(c) == placed
    roundtrip(window, tmp_path, "recalled")
    # UI open has always recalled pending automatically; also exercise that path.
    assert window.project.open(source)
    assert numbers(c.frames) == [[v] for v in (
        "CELL:1", "CELL:2", "CELL:2", "BLANK", "BLANK", "EMPTY", "EMPTY", "CELL:3")]
    assert not c._document.tracks[0].pending_slots
    assert_pending_graph(c._document)


def test_insert_delete_split_merge_new_number_remap_save_chain(window, tmp_path):
    c = install(window, [slot(1, "red", has_content=True, exposure=4), slot(), slot(), slot(),
                         slot(2, "blue", has_content=True, exposure=2), slot()])
    ops = window.timeline_ops
    assert ops.insert_frame_at(row(c), 1)
    assert numbers(c.frames) == [[f"CELL:{n}"] for n in (1, 1, 1, 1, 1, 2, 2)]
    roundtrip(window, tmp_path, "insert")
    assert ops.delete_frame_at(row(c), 2)
    roundtrip(window, tmp_path, "delete")
    # Regression: the old saved key marker still held 5 after deleting a hold.
    assert payload(tmp_path / "delete.pman")["metadata"]["frames"][0]["layers"][0]["exposure"] == 4
    assert ops.split_clip(row(c), 2)
    assert numbers(c.frames) == [[v] for v in ("CELL:1", "CELL:1", "CELL:3", "CELL:3", "CELL:2", "CELL:2", "EMPTY")]
    roundtrip(window, tmp_path, "split")
    assert ops.merge_clip(row(c), 2)
    roundtrip(window, tmp_path, "merge")
    assert ops.new_number_here(row(c), 1)
    assert numbers(c.frames)[1] == ["CELL:4"]
    roundtrip(window, tmp_path, "new-number")
    assert window.time_remap.apply_to_active_layer(
        {"states": [2, 2, 1, 1], "start_frame": 1, "end_frame": 4, "format": "test"},
        confirm=False)
    assert numbers(c.frames)[1:5] == [[f"CELL:{n}"] for n in (2, 2, 1, 1)]
    roundtrip(window, tmp_path, "remap")


def test_tween_save_open_edit_undo_redo_chain(window, tmp_path, monkeypatch):
    questions = []

    def accept_tween_dialog(parent, title, text, buttons, default):
        # conftest forbids real modals. Accept only this exact expected dialog.
        assert parent is window
        assert title == "トゥイーンを有効にする"
        assert text == ("3コマの自由変形トゥイーンを開始します。\n"
                        "確定後もトゥイーンとして残り、タイムラインの帯をダブルクリックすると形を直せます。")
        assert buttons == QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        assert default == QMessageBox.StandardButton.Ok
        questions.append(title)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "question", staticmethod(accept_tween_dialog))
    c = install(window, [slot(1, "red", has_content=True, exposure=3), slot(), slot(),
                         slot(2, "blue", has_content=True)])
    c.current_frame = 0
    window.tween.enable(row(c), 0, "free")
    assert questions == ["トゥイーンを有効にする"]
    c.transform_points = [QPointF(p.x() + 2, p.y() + 1) for p in c.transform_points]
    window.tween.commit_transform_or_tween()
    roundtrip(window, tmp_path, "tween")
    window.tween.edit(row(c), 0)
    window.tween.cancel_transform_or_tween()
    assert c.move_timeline_cell(1, 0, 3, 0)
    assert c.timeline_block_at(0, 0) == ("content", 0, 2)
    roundtrip(window, tmp_path, "tween-moved")
