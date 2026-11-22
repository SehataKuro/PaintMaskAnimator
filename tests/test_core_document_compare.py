"""Compare the private Rust Document with the Python document oracle."""

import json
from dataclasses import asdict
from unittest.mock import patch
from zipfile import ZipFile

import numpy as np
import pytest

from paintmaskanimator import project_io, utils
from paintmaskanimator.document_changes import Cursor, Selection
from paintmaskanimator.native import resolve_timeline
from paintmaskanimator.project_v2 import _decode_core_document, build_payload
from pma_native.pma_native import _compare_document_v2
from python_document_oracle import Document as OracleDocument
from v2_golden_support import CASES, ROOT, V2_ROOT


def _load_oracle(path):
    # v1 constructs the document in project_io, and v2 imports its class lazily.
    with patch.object(project_io, "Document", OracleDocument), patch(
            "paintmaskanimator.document.Document", OracleDocument):
        loaded = project_io.load_project(path)
    assert type(loaded.document) is OracleDocument
    return loaded


def _project(loaded):
    payload, pngs = build_payload(project_io.ProjectSnapshot(
        loaded.metadata, loaded.document, loaded.width, loaded.height,
    ))
    return payload, [
        (name, image.width, image.height, image.rgba().tobytes())
        for name, image, _ in pngs
    ]


def _notices(group):
    return [{"kind": type(change).__name__, **asdict(change)} for change in group.changes]


def _compare(loaded, source, artwork, operations=(), expected_changes=()):
    expected, pngs = _project(loaded)
    actual, images, sources, changes, selection = _compare_document_v2(
        source, {name: tuple(data) for name, *data in artwork}, json.dumps(operations),
    )
    assert json.loads(changes) == json.loads(json.dumps(expected_changes))
    decoded = json.loads(actual)
    assert decoded == expected
    assert json.dumps(decoded, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)
    assert images == pngs
    assert sources == resolve_timeline(loaded.document.frames).ravel().tolist()
    assert selection == (loaded.document.current_frame, loaded.document.active_layer_index)


def _apply_selection(doc, changes):
    for change in changes.changes:
        if isinstance(change, Cursor):
            doc.current_frame = change.frame
        elif isinstance(change, Selection):
            doc.active_layer_index = change.layer_index


@pytest.mark.parametrize("case", CASES)
def test_document_v2_golden(case):
    path = V2_ROOT / f"{case}.pman"
    loaded = _load_oracle(path)
    _, artwork = _project(loaded)
    with ZipFile(path) as archive:
        source = archive.read("project.json").decode()
    _compare(loaded, source, artwork)


@pytest.mark.parametrize("override_selection", [False, True])
@pytest.mark.parametrize("case", CASES)
def test_core_document_v2_archive(case, override_selection):
    path = V2_ROOT / f"{case}.pman"
    loaded = _load_oracle(path)
    with ZipFile(path) as archive:
        source = json.loads(archive.read("project.json"))
        document, width, height = _decode_core_document(source, archive)
    assert (width, height) == (loaded.width, loaded.height)
    if override_selection:
        loaded.metadata.update(
            current_frame=(loaded.document.current_frame + 1) % len(loaded.document.frames),
            active_layer_index=(loaded.document.active_layer_index + 1) % len(loaded.document.tracks),
        )
    _compare_core_projection(loaded, document)


def _compare_core_projection(loaded, document):
    expected, artwork = _project(loaded)
    payload, images = document.project_v2(json.dumps(loaded.metadata))
    assert json.loads(payload) == expected
    assert json.dumps(json.loads(payload), ensure_ascii=False, indent=2) == json.dumps(
        expected, ensure_ascii=False, indent=2,
    )
    assert [(name, image.width, image.height, image.rgba_bytes()) for name, image in images] == artwork


def test_core_v2_archive_applies_legacy_mask_per_image_request(tmp_path):
    from PySide6.QtGui import QColor, QImage

    track = {"cells": [{"number": 1, "image": "art.png"}], "sheet": [1]}
    source = {
        "format": "PaintMaskAnimatorProject", "format_version": 2, "mask_format": 1,
        "canvas": {"width": 1, "height": 1},
        "frame_durations": [1],
        "tracks": [track, dict(track, is_paper=True)],
    }
    image = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("white"))
    path = tmp_path / "shared-legacy-mask.pman"
    with ZipFile(path, "w") as archive:
        archive.writestr("project.json", json.dumps(source))
        archive.writestr("art.png", project_io.encode_png(image))
    loaded = _load_oracle(path)
    with ZipFile(path) as archive:
        document, _, _ = _decode_core_document(source, archive)
    _compare_core_projection(loaded, document)
    assert loaded.document.tracks[0].cells[1].image.rgba().tobytes() == bytes(4)
    assert loaded.document.tracks[1].cells[1].image.rgba().tobytes() == bytes([255] * 4)


@pytest.mark.parametrize("path", sorted((ROOT / "v1").glob("*.pman")), ids=lambda path: path.stem)
def test_document_v1_after_python_import(path):
    loaded = _load_oracle(path)
    payload, artwork = _project(loaded)
    _compare(loaded, json.dumps(payload), artwork)


def _rgba(image):
    return [image.width, image.height, list(image.rgba().tobytes())]


def test_document_edits(monkeypatch):
    loaded = _load_oracle(V2_ROOT / "basic-bank-sheet.pman")
    monkeypatch.setattr(utils, "workspace_size", lambda: (loaded.width, loaded.height))
    payload, artwork = _project(loaded)
    doc = loaded.document
    pixel = [255, 17, 0, 255]
    operations = [
        (["add_track"], doc.add_track, ()),
        (["ensure_frame_count", 9], doc.ensure_frame_count, (9,)),
        (["duplicate_frame", 0], doc.duplicate_frame, (0,)),
        (["new_sheet_number", 1, 0], doc.new_sheet_number, (1, 0)),
        (["write_drawing_patch", 0, 1, 2, 1, pixel], None, ()),
        (["move_tracks", [1, 0]], doc.move_tracks, ([1, 0],)),
        (["remap"], None, ()),
        (["create_blank_key", 4, 0], doc.create_blank_key, (4, 0)),
        (["new_sheet_cell", 4, 0], doc.new_sheet_cell, (4, 0)),
        (["clear_initial_image", 2, 1], doc.set_initial_images, ([(2, 1, None)],)),
    ]
    edits = []
    expected_changes = []
    for operation, method, args in operations:
        if operation == ["remap"]:
            bank = [None, None, doc.tracks[1].cells[3].image]
            operation = ["remap", 0, 1, [3, None, 3], [None, None, _rgba(bank[2])]]
            changes = doc.apply_sheet_remap(0, 2, [(1, [3, None, 3], bank)], 0)
        elif method:
            changes = method(*args)
        else:
            changes = doc.write_drawing_patch(
                1, 0, np.array([[pixel]], dtype=np.uint8), (2, 1, 1, 1), rgba=True,
            )
        _apply_selection(doc, changes)
        edits.append(operation)
        expected_changes.append(_notices(changes))
        _compare(loaded, json.dumps(payload), artwork, edits, expected_changes)


def test_pending_placement():
    loaded = _load_oracle(V2_ROOT / "pending-compat.pman")
    payload, artwork = _project(loaded)
    changes = loaded.document.place_pending_entries()
    _apply_selection(loaded.document, changes)
    _compare(
        loaded, json.dumps(payload), artwork, [["place_pending_entries"]],
        [_notices(changes)],
    )


def test_python_oracle_reference_clone_clears_legacy_pending():
    loaded = _load_oracle(V2_ROOT / "pending-compat.pman")
    slot = next(slot for frame in loaded.document.frames for slot in frame.layers
                if slot.sequence_only)
    held = slot.clone_reference()
    assert held.sequence_only
    position = held.__dict__["_v1_position"]
    assert position.pending is False
    assert position.legacy.sequence_only is False


def test_move_sheet_cell(monkeypatch):
    loaded = _load_oracle(V2_ROOT / "basic-bank-sheet.pman")
    monkeypatch.setattr(utils, "workspace_size", lambda: (loaded.width, loaded.height))
    payload, artwork = _project(loaded)
    doc = loaded.document
    added = doc.add_track()
    moved = doc.move_sheet_cell(doc.prepare_sheet_move(0, 0, 1, 1))
    _apply_selection(doc, moved)
    _compare(
        loaded, json.dumps(payload), artwork, [["add_track"], ["move_sheet_cell", 0, 0, 1, 1]],
        [_notices(group) for group in (added, moved)],
    )


@pytest.mark.parametrize(
    ("case", "layer", "key", "boundary", "edge"),
    [
        ("basic-bank-sheet", 0, 0, 4, "right"),  # 後ろのキーを右へ送る
        ("basic-bank-sheet", 0, 0, 0, "right"),  # 縮める
        ("basic-bank-sheet", 0, 5, 6, "right"),  # 空きへ伸ばす
        ("basic-bank-sheet", 0, 5, 9, "right"),  # 文書の末尾の先へ
        ("basic-bank-sheet", 0, 2, 3, "right"),  # 空キーを伸ばす
        ("basic-bank-sheet", 0, 5, 4, "left"),   # 前の保持の終わりまで
        ("basic-bank-sheet", 0, 0, 1, "left"),   # 左端を縮める
        ("basic-bank-sheet", 0, 3, 3, "left"),
        ("paper-draft", 1, 1, 3, "right"),
        ("paper-draft", 0, 3, 2, "left"),
        ("paper-draft", 2, 0, 2, "right"),
    ],
)
def test_resize_sheet_exposure(monkeypatch, case, layer, key, boundary, edge):
    loaded = _load_oracle(V2_ROOT / f"{case}.pman")
    monkeypatch.setattr(utils, "workspace_size", lambda: (loaded.width, loaded.height))
    payload, artwork = _project(loaded)
    doc = loaded.document
    prepared = doc.prepare_exposure_resize(layer, key, boundary, edge)
    if prepared is None:
        pytest.skip("今の手順は何もしない")
    key_column, old_end, new_start, new_end, _source = prepared
    changes = doc.resize_sheet_exposure(layer, edge, prepared)
    _apply_selection(doc, changes)
    _compare(
        loaded, json.dumps(payload), artwork,
        [["resize_sheet_exposure", layer, edge, key_column, old_end, new_start, new_end]],
        [_notices(changes)],
    )


def test_replace_drawing_image():
    loaded = _load_oracle(V2_ROOT / "basic-bank-sheet.pman")
    payload, artwork = _project(loaded)
    doc = loaded.document
    image = doc.frames[0].layers[0].image.copy()
    pixel = [33, 44, 55, 255]
    image.write_rgba(np.array([[pixel]], dtype=np.uint8), 0, 0)
    changes = doc.replace_drawing_image(0, 0, image, has_content=False)
    operation = [
        "replace_drawing_image", 0, 0, image.width, image.height,
        list(image.rgba().tobytes()), False,
    ]
    _compare(
        loaded, json.dumps(payload), artwork, [operation],
        [_notices(changes)],
    )


@pytest.mark.parametrize(
    ("case", "positions", "layer_delta", "column_delta", "cursor", "active_layer"),
    [
        ("basic-bank-sheet", [(0, 0), (0, 3)], 0, 1, 1, 0),
        ("paper-draft", [(0, 0), (0, 3)], 1, 1, 1, 1),
    ],
)
def test_move_sheet_cells(monkeypatch, case, positions, layer_delta, column_delta, cursor, active_layer):
    loaded = _load_oracle(V2_ROOT / f"{case}.pman")
    monkeypatch.setattr(utils, "workspace_size", lambda: (loaded.width, loaded.height))
    doc = loaded.document
    if layer_delta:
        doc.tracks[0].opacity = 0.25
        doc.tracks[1].opacity = 0.75
    payload, artwork = _project(loaded)
    moves = doc.prepare_sheet_moves(positions, layer_delta, column_delta)
    assert len(moves) == 2
    operations = [[source, layer, target, dest] for layer, source, dest, target, *_ in moves]
    changes = doc.move_sheet_cells(moves, cursor, active_layer)
    if layer_delta:
        _, _, destination_layer, destination_frame, *_ = moves[0]
        destination = doc.frames[destination_frame].layers[destination_layer]
        assert destination.opacity == 0.25
        assert destination.__dict__["_v1_position"].legacy.opacity == 0.75
    _apply_selection(doc, changes)
    _compare(
        loaded, json.dumps(payload), artwork, [["move_sheet_cells", operations, cursor, active_layer]],
        [_notices(changes)],
    )


def test_patch_content_and_replace_group():
    loaded = _load_oracle(V2_ROOT / "basic-bank-sheet.pman")
    doc = loaded.document
    payload, artwork = _project(loaded)
    pixel = [33, 44, 55, 255]
    patch = doc.write_drawing_patch(
        0, 0, np.array([[pixel]], dtype=np.uint8), (0, 0, 1, 1), rgba=True,
        has_content=False,
    )
    operations = [["write_drawing_patch", 0, 0, 0, 0, pixel, False]]
    changes = [_notices(patch)]
    _compare(loaded, json.dumps(payload), artwork, operations, changes)

    image = doc.frames[1].layers[0].image.copy()
    image.write_rgba(np.array([[[20, 100, 30, 255]]], dtype=np.uint8), 1, 0)
    positions = [(1, 0), (5, 0)]
    groups = [(positions, image)]
    replacement = doc.replace_drawing_images(groups)
    operations.append(["replace_drawing_images", [
        [positions, image.width, image.height, list(image.rgba().tobytes())],
    ]])
    changes.append(_notices(replacement))
    _compare(loaded, json.dumps(payload), artwork, operations, changes)


def _legacy_project(path, frames, archive):
    project_io.legacy_fixture_write_archive(path, {
        "format": "PaintMaskAnimatorProject", "format_version": 1,
        "mask_format": 2, "canvas": {"width": 4, "height": 3}, "frames": [],
    }, frames, archive)
    return _load_oracle(path)


def test_legacy_unnumbered_duplicate_and_split(tmp_path, monkeypatch):
    from paintmaskanimator.models import Frame
    from p5_6_support import image, slot

    frames = [
        Frame([slot(None, "red", has_content=True, exposure=3,
                    initial_image=image("blue"), cell_name="legacy"), slot()]),
        Frame([slot(), slot()]),
        Frame([slot(), slot()]),
        Frame([slot(10, is_blank_key=True), slot()]),
    ]
    archive = {(0, 8): slot(8, "green", has_content=True, cell_name="archived")}
    loaded = _legacy_project(tmp_path / "legacy.pman", frames, archive)
    monkeypatch.setattr(utils, "workspace_size", lambda: (4, 3))
    doc = loaded.document
    payload, artwork = _project(loaded)
    assert sorted(doc.tracks[0].cells) == [8, 10, 11]
    assert [frame.layers[0].sequence_number for frame in doc.frames[:3]] == [11, 11, 11]
    duplicate = doc.duplicate_frame(0)
    _apply_selection(doc, duplicate)
    operations = [["duplicate_frame", 0]]
    changes = [_notices(duplicate)]
    assert [frame.layers[0].sequence_number for frame in doc.frames[:4]] == [11] * 4
    _compare(loaded, json.dumps(payload), artwork, operations, changes)
    split = doc.split_sheet_clip(2, 0)
    _apply_selection(doc, split)
    operations.append(["split_sheet_clip", 2, 0])
    changes.append(_notices(split))
    assert [frame.layers[0].sequence_number for frame in doc.frames[:4]] == [11, 11, 12, 12]
    assert doc.tracks[0].cells[12].cell_name == "legacy"
    _compare(loaded, json.dumps(payload), artwork, operations, changes)


def test_multiple_moves_with_number_collision(tmp_path, monkeypatch):
    from paintmaskanimator.models import Frame
    from p5_6_support import image, slot

    frames = [
        Frame([slot(3, "red", has_content=True, cell_name="incoming",
                    initial_image=image("yellow")),
               slot(3, "blue", has_content=True, cell_name="existing")]),
        Frame([slot(), slot()]),
        Frame([slot(3, "red", has_content=True), slot()]),
        Frame([slot(), slot()]),
    ]
    loaded = _legacy_project(tmp_path / "collision.pman", frames, {})
    monkeypatch.setattr(utils, "workspace_size", lambda: (4, 3))
    doc = loaded.document
    payload, artwork = _project(loaded)
    source = doc.tracks[0].cells[3]
    existing = doc.tracks[1].cells[3].image.rgba().tobytes()
    moves = doc.prepare_sheet_moves([(0, 0), (0, 2)], 1, 1)
    assert len(moves) == 2
    changes = doc.move_sheet_cells(moves, 0, 1)
    _apply_selection(doc, changes)
    assert doc.tracks[1].cells[3].image.rgba().tobytes() == existing
    assert doc.tracks[1].cells[3].cell_name == "existing"
    assert doc.tracks[1].cells[3].initial_image.rgba().tobytes() == source.initial_image.rgba().tobytes()
    operations = [["move_sheet_cells", [[0, 0, 1, 1], [2, 0, 3, 1]], 0, 1]]
    _compare(loaded, json.dumps(payload), artwork, operations, [_notices(changes)])

def _compare_edits(loaded, edits):
    doc = loaded.document
    payload, artwork = _project(loaded)
    operations, notices = [], []
    for operation, edit in edits:
        change_set = edit()
        operations.append(operation)
        notices.append(_notices(change_set))
        _apply_selection(doc, change_set)
        _compare(loaded, json.dumps(payload), artwork, operations, notices)


@pytest.fixture
def initial_image_document(tmp_path, monkeypatch):
    from paintmaskanimator.models import Frame
    from p5_6_support import image, slot

    monkeypatch.setattr(utils, "workspace_size", lambda: (4, 3))
    frames = [
        Frame([slot(1, "red", has_content=True, initial_image=image("blue")), slot()]),
        Frame([slot(), slot(3, "green", has_content=True, initial_image=image("yellow"))]),
        Frame([slot(), slot()]),
    ]
    return _legacy_project(tmp_path / "initial.pman", frames, {})


def test_review_frames(initial_image_document):
    doc = initial_image_document.document
    _compare_edits(initial_image_document, [
        (["advance_frame"], doc.advance_frame),
        (["insert_track_frame", 0, 0], lambda: doc.insert_track_frame(0, 0)),
        (["delete_frame", 1, 0], lambda: doc.delete_frame(1, 0)),
    ])


def test_review_tracks(initial_image_document):
    doc = initial_image_document.document
    _compare_edits(initial_image_document, [
        (["insert_track", 0, 0, 1], lambda: doc.insert_track(0, doc.capture_column(0), active=1)),
        (["duplicate_tracks", 1, "copy"], lambda: doc.duplicate_tracks({1: "copy"})),
        (["set_track_properties", [0], False, 0.5, "edited"],
         lambda: doc.set_track_properties([0], visible=False, opacity=0.5, name="edited")),
        (["merge_tracks", [1, 2], 1], lambda: doc.merge_tracks([1, 2], doc.capture_column(1))),
        (["remove_tracks", [1]], lambda: doc.remove_tracks([1])),
    ])


def test_review_sheet(initial_image_document):
    doc = initial_image_document.document
    _compare_edits(initial_image_document, [
        (["paste_numbered_cell", 0, 0, 0, 1, 1],
         lambda: doc.paste_sheet_cell(0, 1, doc.frames[0].layers[0], 1)),
        (["clear_sheet_span", 0, 0, 1], lambda: doc.clear_sheet_span(0, 0, 1)),
        (["recall_sheet_number", 0, 0, 1],
         lambda: doc.recall_sheet_number(0, 0, 1, doc.resolve_numbered_target(0, 0, 1, sheet=True))),
        (["delete_sequence_entry", 0, 1], lambda: doc.delete_sequence_entry(0, 1)),
    ])


def test_review_normalize_unnumbered(initial_image_document):
    doc = initial_image_document.document
    _compare_edits(initial_image_document, [
        (["set_drawing_content", 1, 0, True], lambda: doc.set_drawing_content(1, 0, True)),
        (["normalize_sheet_numbers", [0]], lambda: doc.normalize_sheet_numbers([0])),
        (["insert_sequence_blank", 0, 1], lambda: doc.insert_sequence_blank(0, 1)),
    ])
    assert doc.frames[1].layers[0].sequence_number == 3


def test_review_patch_and_replace_image(initial_image_document):
    doc = initial_image_document.document
    pixel = [1, 2, 3, 255]
    image = doc.frames[0].layers[0].image.copy()
    image.write_rgba(np.array([[pixel]], dtype=np.uint8), 0, 0)
    _compare_edits(initial_image_document, [
        (["ensure_frame_count", 4], lambda: doc.ensure_frame_count(4)),
        (["write_drawing_patch", 0, 3, 0, 0, pixel],
         lambda: doc.write_drawing_patch(3, 0, np.array([[pixel]], dtype=np.uint8), (0, 0, 1, 1), rgba=True)),
        (["replace_drawing_image", 0, 0, *_rgba(image), True],
         lambda: doc.replace_drawing_image(0, 0, image, has_content=True)),
    ])


def test_review_recall_blank(initial_image_document):
    from python_document_oracle import DrawingTarget

    doc = initial_image_document.document

    def recall():
        slot = doc.frames[-1].layers[0]
        source = DrawingTarget(doc.tracks[0].cells[2], doc.tracks[0], len(doc.frames) - 1, slot, 2)
        return doc.recall_sheet_number(1, 0, 2, source)

    _compare_edits(initial_image_document, [
        (["insert_sequence_blank", 0, 1], lambda: doc.insert_sequence_blank(0, 1)),
        (["recall_sheet_number", 1, 0, 2], recall),
    ])
    assert doc.frames[1].layers[0].is_blank_key


def test_review_default_name_casefold(initial_image_document):
    doc = initial_image_document.document
    # With A–J occupied, Kelvin sign already owns the casefold of K.
    edits = [(["set_track_properties", [1], True, 1.0, "B"],
              lambda: doc.set_track_properties([1], visible=True, opacity=1.0, name="B"))]
    edits.extend((["add_track"], doc.add_track) for _ in range(9))
    edits.extend([
        (["set_track_properties", [10], True, 1.0, "K"],
         lambda: doc.set_track_properties([10], visible=True, opacity=1.0, name="K")),
        (["add_track"], doc.add_track),
    ])
    _compare_edits(initial_image_document, edits)
    assert doc.tracks[-1].name == "L"


@pytest.mark.parametrize("command", ["duplicate_frame", "delete_frame", "create_blank_key"])
def test_review_held_initial_images(tmp_path, monkeypatch, command):
    from paintmaskanimator.models import Frame
    from p5_6_support import image, slot

    monkeypatch.setattr(utils, "workspace_size", lambda: (4, 3))
    frames = [
        Frame([slot(1, "red", has_content=True, exposure=4, initial_image=image("blue")), slot()]),
        Frame([slot(), slot()]),
        Frame([slot(tween_member="held"), slot()]),
        Frame([slot(), slot(2, "green", has_content=True)]),
    ]
    loaded = _legacy_project(tmp_path / "held.pman", frames, {})
    doc = loaded.document
    if command == "duplicate_frame":
        edits = [([command, 2], lambda: doc.duplicate_frame(2))]
    elif command == "delete_frame":
        edits = [([command, 3, 0], lambda: doc.delete_frame(3, 0))]
    else:
        edits = [([command, 2, 0], lambda: doc.create_blank_key(2, 0))]
    _compare_edits(loaded, edits)


def test_review_pending_initial_image(tmp_path, monkeypatch):
    from paintmaskanimator.models import Frame
    from p5_6_support import image, slot

    monkeypatch.setattr(utils, "workspace_size", lambda: (4, 3))
    frames = [
        Frame([slot(1, "red", has_content=True, initial_image=image("blue")), slot()]),
        Frame([slot(), slot()]),
        Frame([slot(2, "green", has_content=True, sequence_only=True, initial_image=image("yellow")),
               slot(3, "blue", has_content=True, exposure=2)]),
        Frame([slot(), slot()]),
    ]
    loaded = _legacy_project(tmp_path / "pending.pman", frames, {})
    _compare_edits(loaded, [(["place_pending_entries"], loaded.document.place_pending_entries)])


def test_review_blank_after_pending(tmp_path, monkeypatch):
    from paintmaskanimator.models import Frame
    from p5_6_support import image, slot

    monkeypatch.setattr(utils, "workspace_size", lambda: (4, 3))
    frames = [
        Frame([slot(1, "red", has_content=True, sequence_only=True, initial_image=image("blue"))]),
        Frame([slot()]),
        Frame([slot()]),
    ]
    loaded = _legacy_project(tmp_path / "pending-blank.pman", frames, {})
    _compare_edits(loaded, [(["create_blank_key", 2, 0],
                             lambda: loaded.document.create_blank_key(2, 0))])


def test_clear_moved_reference(tmp_path, monkeypatch):
    from paintmaskanimator.models import Frame
    from p5_6_support import image, slot

    monkeypatch.setattr(utils, "workspace_size", lambda: (4, 3))
    loaded = _legacy_project(tmp_path / "move-clear.pman", [
        Frame([slot(1, "red", has_content=True, exposure=2, initial_image=image("blue"))]),
        Frame([slot()]),
        Frame([slot()]),
        Frame([slot()]),
    ], {})
    doc = loaded.document
    _compare_edits(loaded, [
        (["move_sheet_cells", [[0, 0, 2, 0]], 2, 0],
         lambda: doc.move_sheet_cells(doc.prepare_sheet_moves([(0, 0)], 0, 2), 2, 0)),
        (["clear_sheet_span", 0, 3, 4], lambda: doc.clear_sheet_span(0, 3, 4)),
    ])


@pytest.mark.parametrize("numbered", [False, True])
def test_clear_remapped_reference(initial_image_document, numbered):
    doc = initial_image_document.document
    bank = [None, doc.tracks[1].cells[3].image] if numbered else []
    states = [2, 2] if numbered else [None, None]
    layer = 1 if numbered else 0
    _compare_edits(initial_image_document, [
        (["remap", 0, layer, states, [None, _rgba(bank[1])] if numbered else []],
         lambda: doc.apply_sheet_remap(0, 1, [(layer, states, bank)], 0)),
        (["clear_sheet_span", layer, 0, 2], lambda: doc.clear_sheet_span(layer, 0, 2)),
    ])


def test_clear_placed_pending_hold(tmp_path, monkeypatch):
    from paintmaskanimator.models import Frame
    from p5_6_support import image, slot

    monkeypatch.setattr(utils, "workspace_size", lambda: (4, 3))
    loaded = _legacy_project(tmp_path / "pending-hold.pman", [
        Frame([slot(1, "red", has_content=True, exposure=4)]),
        Frame([slot()]),
        Frame([slot()]),
        Frame([slot()]),
        Frame([slot(2, "green", has_content=True, sequence_only=True, initial_image=image("yellow"))]),
    ], {})
    doc = loaded.document
    _compare_edits(loaded, [
        (["place_pending_entries"], doc.place_pending_entries),
        (["clear_sheet_span", 0, 3, 4], lambda: doc.clear_sheet_span(0, 3, 4)),
    ])


@pytest.mark.parametrize("command", ["insert_track", "merge_tracks"])
def test_prepared_track_after_numbering_detached_input(initial_image_document, command):
    from paintmaskanimator.models import Layer

    doc = initial_image_document.document
    # The core takes a prepared column. Its source here is the equivalent,
    # already numbered column, while Python receives detached unnumbered art.
    slots = [
        Layer(layer.name, layer.image.copy(), has_content=layer.has_content,
              initial_image=layer.initial_image)
        for layer in doc.capture_column(0)
    ]
    if command == "insert_track":
        edit = ([command, 0, 0, 1], lambda: doc.insert_track(0, slots, active=1))
    else:
        edit = ([command, [0, 1], 0], lambda: doc.merge_tracks([0, 1], slots))
    assert slots[0].sequence_number is None
    _compare_edits(initial_image_document, [edit])
    assert slots[0].sequence_number == 1


def test_invalid_clear_does_not_change_document(initial_image_document):
    loaded = initial_image_document
    doc = loaded.document
    payload, artwork = _project(loaded)
    assert not doc.clear_sheet_span(0, 1, 1).changes
    with pytest.raises(ValueError):
        _compare_document_v2(
            json.dumps(payload),
            {name: tuple(data) for name, *data in artwork},
            json.dumps([["clear_sheet_span", 0, 1, 1]]),
        )
    _compare(loaded, json.dumps(payload), artwork)


def test_clear_reference_filled_by_same_track_move(tmp_path, monkeypatch):
    from paintmaskanimator.models import Frame
    from p5_6_support import image, slot

    monkeypatch.setattr(utils, "workspace_size", lambda: (4, 3))
    loaded = _legacy_project(tmp_path / "move-fill-clear.pman", [
        Frame([slot(1, "red", has_content=True, initial_image=image("blue"))]),
        Frame([slot(2, "green", has_content=True)]),
        Frame([slot(is_blank_key=True)]),
        Frame([slot()]),
        Frame([slot()]),
    ], {})
    doc = loaded.document
    _compare_edits(loaded, [
        (["move_sheet_cell", 1, 0, 3, 0], lambda: doc.move_sheet_cell(doc.prepare_sheet_move(1, 0, 3, 0))),
        (["clear_sheet_span", 0, 1, 2], lambda: doc.clear_sheet_span(0, 1, 2)),
    ])


def test_clear_shifted_key_origin(tmp_path, monkeypatch):
    from paintmaskanimator.models import Frame
    from p5_6_support import image, slot

    monkeypatch.setattr(utils, "workspace_size", lambda: (4, 3))
    loaded = _legacy_project(tmp_path / "shift-key.pman", [
        Frame([slot(1, "red", has_content=True, exposure=3, initial_image=image("blue"))]),
        Frame([slot()]),
        Frame([slot()]),
        Frame([slot(2, "green", has_content=True, initial_image=image("yellow"))]),
    ], {})
    doc = loaded.document
    _compare_edits(loaded, [
        (["insert_track_frame", 1, 0], lambda: doc.insert_track_frame(1, 0)),
        (["clear_sheet_span", 0, 3, 4], lambda: doc.clear_sheet_span(0, 3, 4)),
    ])
    initial = doc.frames[3].layers[0].initial_image
    assert initial.rgba().tobytes() == doc.tracks[0].cells[2].initial_image.rgba().tobytes()


@pytest.mark.parametrize("command", ["paste_numbered_cell", "create_blank_key", "duplicate_frame"])
def test_clear_created_hold(tmp_path, monkeypatch, command):
    from paintmaskanimator.models import Frame
    from p5_6_support import image, slot

    monkeypatch.setattr(utils, "workspace_size", lambda: (4, 3))
    held = slot()
    following = slot()
    if command == "duplicate_frame":
        held = slot(color="green", initial_image=image("yellow"), cell_name="複製元の保持")
        following = slot(color="magenta", initial_image=image("cyan"), cell_name="移動先の保持")
    loaded = _legacy_project(tmp_path / "create-hold.pman", [
        Frame([slot(1, "red", has_content=True, exposure=4, initial_image=image("blue")), slot()]),
        Frame([held, slot()]),
        Frame([following, slot()]),
        Frame([slot(), slot()]),
    ], {})
    doc = loaded.document
    if command == "paste_numbered_cell":
        layer, hold = 1, 2
        create = ([command, 0, 0, 0, layer, 3],
                  lambda: doc.paste_sheet_cell(0, layer, doc.frames[0].layers[0], 3))
    elif command == "create_blank_key":
        layer, hold = 0, 2
        create = ([command, 1, layer], lambda: doc.create_blank_key(1, layer))
    else:
        layer, hold = 0, 2

        def duplicate_held_frame():
            before = doc.frames[1].layers[0].__dict__["_v1_position"].legacy
            assert before.cell_name == "複製元の保持"
            assert tuple(before.image.rgba()[0, 0]) == (0, 128, 0, 255)
            changes = doc.duplicate_frame(1)
            assert doc.frames[hold].layers[layer].sequence_number == 1
            assert tuple(doc.frames[hold].layers[layer].image.rgba()[0, 0]) == (255, 0, 0, 255)
            return changes

        create = ([command, 1], duplicate_held_frame)
    assert doc.tracks[0].cells[1].initial_image is not None
    _compare_edits(loaded, [
        create,
        (["clear_sheet_span", layer, hold, hold + 1],
         lambda: doc.clear_sheet_span(layer, hold, hold + 1)),
    ])
    assert doc.frames[hold].layers[layer].initial_image is None
    if command == "duplicate_frame":
        cleared = doc.frames[hold].layers[layer]
        assert cleared.sequence_number is None
        assert cleared.cell_name is None
        assert not np.any(cleared.image.rgba())
        _, pngs = _project(loaded)
        pixels = {path: rgba for path, _, _, rgba in pngs}
        assert pixels["layers/layer_0000/cell_000001.png"][:4] == bytes((255, 0, 0, 255))
        assert pixels["initial/layers/layer_0000/cell_000001.png"][:4] == bytes((0, 0, 255, 255))


def test_core_document_projection_shares_image_generation():
    from pma_native.pma_native import RgbaImage, _CoreDocument

    rgba = bytes((10, 20, 30, 255))
    image = RgbaImage(1, 1, rgba)
    source = {
        "canvas": {"width": 1, "height": 1},
        "frame_durations": [1],
        "tracks": [{"cells": [{"number": 1, "image": "input.png"}], "sheet": [1]}],
    }
    document = _CoreDocument.from_v2(json.dumps(source), ["input.png"], [image])
    _, pngs = document.project_v2("{}")
    assert len(pngs) == 1
    projected = pngs[0][1]
    assert projected.generation == image.generation
    projected.write_rect(0, 0, 1, 1, bytes((40, 50, 60, 255)))
    _, pngs = document.project_v2("{}")
    assert pngs[0][1].generation == image.generation
    assert pngs[0][1].rgba_bytes() == image.rgba_bytes() == rgba
    assert projected.generation != image.generation


@pytest.mark.parametrize("local_pending_artwork", [False, True])
def test_clear_placed_pending_blank(tmp_path, monkeypatch, local_pending_artwork):
    from paintmaskanimator.models import Frame
    from p5_6_support import image, slot

    monkeypatch.setattr(utils, "workspace_size", lambda: (4, 3))
    loaded = _legacy_project(tmp_path / "pending-blank-local.pman", [
        Frame([slot(1, "red", has_content=True)]),
        Frame([slot(9, initial_image=image("yellow"))]),
        Frame([slot()]),
        Frame([slot(4, is_blank_key=True, sequence_only=True, initial_image=image("blue"))]),
    ], {})
    v2_path = tmp_path / "pending-blank-local-v2.pman"
    project_io.write_project_snapshot(v2_path, project_io.ProjectSnapshot(
        loaded.metadata, loaded.document, loaded.width, loaded.height,
    ))
    payload, artwork = _project(_load_oracle(v2_path))
    if local_pending_artwork:
        track = payload["tracks"][0]
        # The v2 decoder also accepts the pending number from a local entry.
        track["pending"][0]["number"] = None
        local_states = track["compatibility"]["local_states"]
        initial = next(entry["initial"] for entry in local_states if entry["column"] == 1)
        local_states.append({"column": 3, "retained_number": 4, "initial": initial})
        with ZipFile(v2_path) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()
                       if name != "project.json"}
        with ZipFile(v2_path, "w") as archive:
            archive.writestr("project.json", json.dumps(payload))
            for name, contents in entries.items():
                archive.writestr(name, contents)
    loaded = _load_oracle(v2_path)
    doc = loaded.document
    source = doc.frames[3].layers[0]
    assert source.is_blank_key and source.sequence_only and source.initial_image is not None
    target = doc.frames[1].layers[0]
    assert target.sequence_number == 9 and target.initial_image is not None
    assert not (target.has_content or target.is_blank_key or target.sequence_only)

    def place():
        changes = doc.place_pending_entries()
        placed = doc.frames[1].layers[0]
        assert placed.is_blank_key and placed.sequence_number == 4
        assert not placed.sequence_only and placed.initial_image is None
        return changes

    operations = [["place_pending_entries"]]
    notices = [_notices(place())]
    _compare(loaded, json.dumps(payload), artwork, operations, notices)
    operations.append(["clear_sheet_span", 0, 1, 2])
    notices.append(_notices(doc.clear_sheet_span(0, 1, 2)))
    _compare(loaded, json.dumps(payload), artwork, operations, notices)
    assert doc.frames[1].layers[0].initial_image is None
