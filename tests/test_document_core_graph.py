"""Python の文書グラフを Rust の Document へ写して戻すと、観測できる状態が変わらない。"""

import json

import pytest

from paintmaskanimator import project_io
from paintmaskanimator.document import Document
from paintmaskanimator.document_core import (
    GraphError, core_document, graph_from_core, graph_payload, graph_signature,
    roundtrip_mismatch,
)
from paintmaskanimator.models import Cell, Frame, Layer
from paintmaskanimator.pma_image import PmaImage
from v2_golden_support import CASES, ROOT, V2_ROOT

FIXTURES = [*sorted((ROOT / "v1").glob("*.pman")), *(V2_ROOT / f"{case}.pman" for case in CASES)]


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: f"{path.parent.name}/{path.stem}")
def test_loaded_projects_roundtrip(path):
    document = project_io.load_project(path).document
    assert roundtrip_mismatch(document) is None


@pytest.mark.parametrize("path", FIXTURES[:4], ids=lambda path: path.stem)
def test_edited_projects_roundtrip(path):
    document = project_io.load_project(path).document
    for edit in (
        lambda: document.duplicate_frame(0),
        lambda: document.create_blank_key(1, 0),
        lambda: document.new_sheet_cell(0, 0),
        lambda: document.insert_track_frame(0, 0),
        lambda: document.add_track(),
        lambda: document.normalize_sheet_numbers([0]),
    ):
        edit()
        assert roundtrip_mismatch(document) is None


def test_hidden_position_state_survives():
    document = Document()
    image = PmaImage.blank(4, 3)
    # 同じ PmaImage を複数の絵に入れると Rust では表せない（別の検査）ので、別々に渡す。
    document.install_legacy_frames([
        Frame([Layer("A", image, has_content=True, sequence_number=7)]),
        Frame([Layer("A", image.copy())]),
        Frame([Layer("A", image.copy(), is_blank_key=True, blank_start=True, sequence_number=7)]),
    ], canvas_size=(4, 3))
    slot = document.frames[0].layers[0]
    slot.has_content = False
    assert slot.__dict__["_content_origin"] == 7
    document.frames[1].layers[0].set_pending(True)
    assert roundtrip_mismatch(document) is None
    frames, tracks, current, active, _ = graph_from_core(core_document(document))
    assert frames[0].layers[0].__dict__["_content_origin"] == 7
    assert frames[1].layers[0].sequence_only
    assert frames[2].layers[0].is_blank_key and frames[2].layers[0].sequence_number == 7
    assert [frame.duration for frame in frames] == [1, 1, 1]
    assert (current, active) == (0, 0)
    # 同じ画素の画像は Rust でも一枚を共有する。
    assert tracks[0].cells[7].image.generation == image.generation


def test_images_are_shared_and_none_images_survive():
    document = Document()
    document.install_legacy_frames([Frame([Layer("A", None), Layer("B", None)])])
    assert roundtrip_mismatch(document) is None
    text, images = graph_payload(document)
    assert len(images) == 1
    assert json.loads(text)["frames"][0]["positions"][0]["local"]["image"] == 0


def test_unrepresentable_states_are_reported():
    document = Document()
    document.frames = [Frame([Layer("A", PmaImage.blank(1, 1))])]
    with pytest.raises(GraphError):
        graph_payload(document)
    connected = Document()
    connected.frames[0].layers[0].has_content = True
    connected.frames[0].layers[0].sequence_number = 1
    connected.frames[0].layers[0].is_blank_key = True
    assert roundtrip_mismatch(connected).startswith("unrepresentable")
    # 表せない状態でも、Python 側の署名は作れる（比較の片側に使う）。
    assert graph_signature(connected)[4] == graph_signature(Document())[4]


def test_duplicated_frame_track_and_layer_objects_are_unrepresentable():
    frames = Document()
    frames.frames = [frames.frames[0], frames.frames[0]]
    assert "同じ Frame" in roundtrip_mismatch(frames)
    tracks = Document()
    tracks.add_track()
    tracks.tracks[1] = tracks.tracks[0]
    assert "同じ Track" in roundtrip_mismatch(tracks)
    layers = Document()
    layers.add_track()
    layers.frames[0].layers[1] = layers.frames[0].layers[0]
    assert "同じ Layer" in roundtrip_mismatch(layers)


def test_non_integer_cell_numbers_are_unrepresentable():
    document = Document()
    document.tracks[0].cells[None] = Cell()
    assert roundtrip_mismatch(document).startswith("unrepresentable")


def test_cell_image_writes_reach_the_core_document():
    from paintmaskanimator.native import _native

    document = Document()
    image = PmaImage.blank(4, 3)
    document.install_legacy_frames(
        [Frame([Layer("A", image, has_content=True, sequence_number=1)])], canvas_size=(4, 3))
    core = core_document(document, 4, 3)
    track_id = core.order()[1][0]
    window = _native._CellImage(core, True, track_id, 1, False)
    assert window.exists() and (window.width, window.height) == (4, 3)
    before = window.generation
    window.write_rect(1, 1, 1, 1, bytes([10, 20, 30, 255]))
    assert window.read_rect(1, 1, 1, 1) == bytes([10, 20, 30, 255])
    assert window.generation != before
    _, tracks, _, _, _ = graph_from_core(core)
    assert tracks[0].cells[1].image.generation == window.generation
    # 書き込みは元の Python の画像には届かない（値の共有は書き込みで分かれる）。
    assert image.generation == before
    assert not _native._CellImage(core, True, track_id, 2, False).exists()


def test_shared_local_cells_and_detached_pending_survive():
    document = project_io.load_project(FIXTURES[1]).document
    document.duplicate_frame(0)
    document.create_blank_key(1, 0)
    slots = [frame.layers[0] for frame in document.frames]
    shared = [slot for slot in slots if "_cell" in slot.__dict__]
    detached = [index for index, slot in enumerate(slots)
                if slot.__dict__.get("_pending_detached")]
    assert detached, "前提：配置待ちの複製が残る編集"
    assert len({id(slot.__dict__["_cell"]) for slot in shared}) < len(shared), "前提：局所 Cell の共有"
    assert roundtrip_mismatch(document) is None
    frames, tracks, _, _, _ = graph_from_core(core_document(document))
    copied = [frame.layers[0] for frame in frames]
    for index in detached:
        assert copied[index].__dict__["_pending_detached"]
        assert copied[index].__dict__["_pending_token"] not in tracks[0].pending_slots
    # 同じ Cell を持っていた位置どうしは、戻した後も同じ Cell を持つ。
    pairs = [(a, b) for a in range(len(slots)) for b in range(a + 1, len(slots))
             if slots[a].__dict__.get("_cell") is not None
             and slots[a].__dict__.get("_cell") is slots[b].__dict__.get("_cell")]
    assert pairs
    for a, b in pairs:
        assert copied[a].__dict__["_cell"] is copied[b].__dict__["_cell"]


def test_aliased_images_are_unrepresentable_and_decoded_images_are_independent():
    document = Document()
    document.install_legacy_frames([Frame([Layer("A", PmaImage.blank(2, 2)),
                                           Layer("B", PmaImage.blank(2, 2))])])
    first, second = document.frames[0].layers
    first.image = second.image
    assert roundtrip_mismatch(document) == (
        "unrepresentable: 同じ画像オブジェクトを複数の絵が持っている")
    first.image = second.image.copy()
    assert roundtrip_mismatch(document) is None
    frames, _, _, _, _ = graph_from_core(core_document(document))
    a, b = (slot.image for slot in frames[0].layers)
    # 画素を共有していても、戻した画像は別のオブジェクト。
    assert a is not b and a.generation == b.generation
