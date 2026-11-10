"""Legacy conversion and installed dense boundary contracts."""
from dataclasses import replace
import random

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator import layout_paper, native, project_io
from paintmaskanimator.clip_animation import ClipAnimationLayer, ClipCelKey, build_pma_frames
from paintmaskanimator.document import Document
from paintmaskanimator.legacy_v1 import (
    BLANK, EMPTY, DenseReference, LegacyLayerV1, V1Position,
    compress_v1, expand_legacy, legacy_layer_to_slot, snapshot_frames, snapshot_layer,
)
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.models import Frame
from paintmaskanimator.timeline_span import span_at
from paintmaskanimator.undo_entries import DocUndo


def cell(number, **kw):
    return LegacyLayerV1(sequence_number=number, has_content=True, **kw)


def refs(*numbers):
    return tuple(number if isinstance(number, DenseReference)
                 else DenseReference("CELL", number) for number in numbers)


def test_fixed_cutoff_missing_pending_and_resolution():
    rows = ((cell(7, exposure=4), cell(8, exposure=3)),
            (cell(99, sequence_only=True, is_blank_key=True),), (),
            (LegacyLayerV1(),), ())
    dense = expand_legacy(rows)
    assert dense.sheet == tuple(zip(refs(7, 7, 7, 7, EMPTY), refs(8, 8, 8, EMPTY, EMPTY)))
    assert dense.resolved == tuple(zip(refs(7, 99, EMPTY, EMPTY, EMPTY),
                                       refs(8, 8, 8, EMPTY, EMPTY)))
    assert dense.sheet_keys == ((0, 0), (0, 0), (0, 0), (0, -1), (-1, -1))
    assert dense.resolve_keys == ((0, 0), (1, 0), (-1, 0), (-1, -1), (-1, -1))
    assert dense.positions[1][0].pending
    assert dense.positions[2][0].legacy is None
    assert compress_v1(dense.sheet, dense.positions) == rows


@pytest.mark.parametrize("blank", [False, True])
@pytest.mark.parametrize("length", [-5, 0, 2, 20])
def test_fixed_new_key_never_resurrects_previous_key(blank, length):
    rows = ((cell(1, exposure=20),),
            (LegacyLayerV1(has_content=not blank, is_blank_key=blank,
                           sequence_number=2, exposure=length),), (), (), ())
    expected = [DenseReference("CELL", 1)]
    expected += [BLANK if blank else DenseReference("CELL", 2)] * min(4, max(1, length))
    expected += [EMPTY] * (5 - len(expected))
    dense = expand_legacy(rows)
    assert dense.sheet == tuple((value,) for value in expected)
    assert compress_v1(dense.sheet, dense.positions) == rows


def test_number_once_per_old_key_and_content_wins():
    rows = ((cell(None, exposure=3, is_blank_key=True),), (), (),
            (cell(None, exposure=2),), (LegacyLayerV1(sequence_number=9),))
    assert expand_legacy(rows).sheet[0] == refs(None)
    dense = expand_legacy(rows, number_unnumbered=True)
    assert dense.sheet == tuple((value,) for value in refs(10, 10, 10, 11, 11))
    assert not dense.positions[0][0].blank_start
    assert rows[0][0].sequence_number is None


def test_dense_compression_fixed_runs_and_position_cuts():
    sheet = tuple((value,) for value in refs(1, 1, 1, 1, BLANK, BLANK, EMPTY, 2))
    plain = compress_v1(sheet)
    assert [(i, s.exposure, s.sequence_number) for i, (s,) in enumerate(plain)
            if s.has_content or s.is_blank_key] == [(0, 4, 1), (4, 2, None), (7, 1, 2)]
    positions = [[V1Position()] for _ in sheet]
    positions[1][0] = V1Position(LegacyLayerV1(tween={"points": [4]}))
    positions[2][0] = V1Position(LegacyLayerV1(tween_member="t"))
    positions[5][0] = V1Position(blank_start=True)
    packed = compress_v1(sheet, positions)
    assert [(i, s.exposure) for i, (s,) in enumerate(packed)
            if s.has_content or s.is_blank_key] == [(0, 1), (1, 1), (2, 1), (3, 1),
                                                  (4, 1), (5, 1), (7, 1)]
    assert expand_legacy(packed).sheet == sheet
    packed[1][0].tween["points"].append(8)
    assert positions[1][0].legacy.tween == {"points": [4]}


def test_edited_sheet_retains_old_key_markers_and_pending_payload():
    rows = ((cell(1, exposure=3),), (cell(99, sequence_only=True),),
            (cell(1),), (cell(2),))
    dense = expand_legacy(rows)
    edited = dense.sheet[:3] + (refs(3),)
    packed = compress_v1(edited, dense.positions)
    assert expand_legacy(packed).sheet == edited
    assert packed[0][0].exposure == 2
    assert packed[1][0] == rows[1][0]
    assert packed[2][0].has_content  # adjacent same-number old boundary retained
    # A visible key cannot be created inside the pending slot without moving it.
    with pytest.raises(ValueError, match="pending"):
        compress_v1((refs(1), refs(5), refs(1), refs(3)), dense.positions)


def image(color):
    result = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(color))
    return result


def frames_from_rows(rows, durations=None):
    return [Frame(list(row), durations[i] if durations else 1) for i, row in enumerate(rows)]


def assert_payload_equal(a, b):
    assert a[:2] == b[:2]
    assert [(name, img, error) for name, img, error in a[2]] == b[2]


def test_writer_reader_and_hidden_payload_roundtrip(tmp_path):
    frames = [Frame([LegacyLayerV1("A", image("red"), has_content=True, exposure=3,
                           sequence_number=1)], 2),
              Frame([LegacyLayerV1("A", image("cyan"), initial_image=image("green"),
                           cell_name="invisible", sequence_number=19)]),
              Frame([LegacyLayerV1("A", image("yellow"), is_blank_key=True,
                           sequence_number=21, exposure=5)])]
    rows = snapshot_frames(frames)
    dense = expand_legacy(rows)
    recovered = frames_from_rows(compress_v1(dense.sheet, dense.positions), [2, 1, 1])
    assert_payload_equal(project_io.build_v1_payload(frames),
                         project_io.build_v1_payload(recovered))
    metadata = {"format": "PaintMaskAnimatorProject", "format_version": 1,
                "mask_format": 2, "canvas": {"width": 2, "height": 2}, "frames": []}
    path = tmp_path / "boundary.pman"
    project_io.write_project_archive(
        path, metadata, [Frame([legacy_layer_to_slot(slot) for slot in frame.layers],
                               frame.duration) for frame in recovered])
    _data, loaded, _w, _h = project_io.read_project_archive(path)
    reread = expand_legacy(snapshot_frames(loaded))
    assert reread.sheet == dense.sheet
    loaded_again = frames_from_rows(compress_v1(reread.sheet, reread.positions), [2, 1, 1])
    assert_payload_equal(project_io.build_v1_payload(frames_from_rows(snapshot_frames(loaded), [2, 1, 1])),
                         project_io.build_v1_payload(loaded_again))
    frames[1].layers[0].image.fill(QColor("black"))
    frames[1].layers[0].initial_image.fill(QColor("black"))
    assert rows[1][0].image == image("cyan")
    assert rows[1][0].initial_image == image("green")
    assert recovered[1].layers[0].cell_name == "invisible"


def test_random_raw_rust_oracle_and_independent_dense_roundtrip():
    rng = random.Random(3153)
    for _ in range(100):
        frames = [Frame([LegacyLayerV1("A", QImage(), has_content=rng.choice([True, False]),
                               is_blank_key=rng.choice([True, False]),
                               sequence_only=rng.choice([True, False]),
                               sequence_number=rng.choice([None, 1, 2, 5]),
                               exposure=rng.randint(-2, 12))
                         for _ in range(rng.randrange(4))])
                  for _ in range(rng.randrange(15))]
        dense = expand_legacy(snapshot_frames(frames))
        # Rust v2 consumes dense references, never sparse v1 DTOs. Check both
        # independent expanded columns, with number=None preserved at this pure boundary.
        from paintmaskanimator.models import Layer
        from paintmaskanimator.timeline_metadata import metadata_for
        def dense_frames(references, positions=dense.positions):
            return [Frame([Layer("A", QImage(), has_content=ref.kind == "CELL",
                                 is_blank_key=ref.kind == "BLANK",
                                 sequence_number=ref.number,
                                 blank_start=positions[i][li].blank_start)
                           for li, ref in enumerate(row)])
                    for i, row in enumerate(references)]
        oracle = native.sheet_entries(dense_frames(dense.sheet))
        assert dense.sheet == tuple(tuple(
            EMPTY if entry.kind == 0 else BLANK if entry.kind == 1
            else DenseReference("CELL", entry.number) for entry in row) for row in oracle)
        resolved = dense_frames(dense.resolved)
        keys = native.resolve_timeline(resolved).tolist()
        for column, row in enumerate(keys):
            for li, key in enumerate(row):
                ref = dense.resolved[column][li]
                assert (key >= 0) == (ref.kind == "CELL")
                if key >= 0:
                    assert metadata_for(resolved[key].layers[li]).sequence_number == ref.number
        assert expand_legacy(compress_v1(dense.sheet, dense.positions)).sheet == dense.sheet
        # Fresh compression has no legacy markers or pending payloads.
        assert expand_legacy(compress_v1(dense.sheet)).sheet == dense.sheet


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("entrance", ["raw", "layout", "docundo", "clip"])
def test_real_boundary_entrances(app, monkeypatch, entrance):
    window = MainWindow()
    try:
        canvas = window.canvas
        raw = [Frame([LegacyLayerV1("A", image("red"), has_content=True,
                            exposure=3, sequence_number=7)])]
        raw += [Frame([LegacyLayerV1("A", image("cyan"))]) for _ in range(2)]
        if entrance == "clip":
            source = ClipAnimationLayer("clip", (ClipCelKey(0, "A-7"),),
                                        {"A-7": image("green")})
            raw = build_pma_frames(2, 2, 0, 2, (source,))
        if entrance == "layout":
            canvas.install_legacy_frames(raw)
            monkeypatch.setattr(layout_paper, "load_image", lambda _: image("blue"))
            window.insert_layout_paper({"id": "boundary"})
            raw = canvas.frames
        elif entrance == "docundo":
            doc = Document()
            doc.install_legacy_frames(raw)
            canvas.undo_stack.append(DocUndo(doc.snapshot()))
            canvas.undo()
            raw = canvas.frames
        canvas.install_legacy_frames(raw)
        from paintmaskanimator.document import project_v1_state
        from paintmaskanimator.sheet import sheet_numbers
        li = 1 if entrance == "layout" else 0
        number = 1 if entrance == "clip" else 7
        assert sheet_numbers(canvas.frames, li) == [number] * 3
        projected, _archive = project_v1_state(canvas.frames)
        dense = expand_legacy(snapshot_frames(projected))
        assert tuple(row[li] for row in dense.sheet) == refs(number, number, number)
        restored = frames_from_rows(compress_v1(dense.sheet, dense.positions),
                                    [frame.duration for frame in projected])
        assert_payload_equal(project_io.build_v1_payload(projected),
                             project_io.build_v1_payload(restored))
    finally:
        window.close()


def test_span_uses_installed_dense_length_and_pending_skip():
    rows = [[cell(1, exposure=20)], [cell(99, sequence_only=True)], [],
            [LegacyLayerV1(is_blank_key=True, exposure=0)], []]
    doc = Document()
    doc.install_legacy_frames(frames_from_rows(rows))
    frames = doc.frames
    assert span_at(frames, 0, 2) == ("content", 0, 3)
    assert span_at(frames, 0, 3) == ("blank", 3, 1)
    assert span_at(frames, 0, 4) == (None, None, 0)
    assert span_at(frames, -1, 0) == (None, None, 0)
    assert snapshot_layer(rows[0][0]) == rows[0][0]
    assert replace(rows[0][0], exposure=1).exposure == 1


@pytest.mark.parametrize("rows", [(), ((),), ((), (), ())])
def test_empty_shapes(rows):
    dense = expand_legacy(rows)
    assert dense.sheet == tuple(() for _ in rows)
    assert dense.resolved == dense.sheet
    assert compress_v1(dense.sheet, dense.positions) == rows


def test_adjacent_blanks_old_boundaries_and_local_invisible_payload():
    rows = ((LegacyLayerV1(is_blank_key=True, exposure=2),),
            (LegacyLayerV1(image=image("cyan"), initial_image=image("green"),
                           cell_name="hidden", sequence_number=19),),
            (LegacyLayerV1(is_blank_key=True, exposure=2),), (LegacyLayerV1(),),
            (cell(2),))
    dense = expand_legacy(rows)
    assert dense.sheet == ((BLANK,), (BLANK,), (BLANK,), (BLANK,), refs(2))
    assert [row[0].blank_start for row in dense.positions] == [True, False, True, False, False]
    packed = compress_v1(dense.sheet[:4] + (refs(3),), dense.positions)
    assert packed[0][0].exposure == packed[2][0].exposure == 2
    assert packed[1][0].image == image("cyan")
    assert packed[1][0].initial_image == image("green")
    assert packed[1][0].cell_name == "hidden"
    assert packed[1][0].sequence_number == 19
    assert not packed[1][0].has_content


def test_adapter_installs_old_keys_once_without_numbering_each_hold():
    rows = ((cell(None, image=image("red"), exposure=3),),
            (LegacyLayerV1(image=image("cyan")),), (LegacyLayerV1(),))
    dense = expand_legacy(rows, number_unnumbered=True)
    old = compress_v1(dense.sheet, dense.positions)
    doc = Document()
    doc.install_legacy_frames([Frame([legacy_layer_to_slot(slot) for slot in row])
                               for row in old])
    assert list(doc.tracks[0].cells) == [1]
    from paintmaskanimator.sheet import sheet_numbers
    assert sheet_numbers(doc.frames, 0) == [1, 1, 1]


def test_new_blank_marker_overrides_unchanged_visible_column():
    dense = expand_legacy(((LegacyLayerV1(is_blank_key=True, exposure=4),),
                           (LegacyLayerV1(),), (LegacyLayerV1(),), (LegacyLayerV1(),)))
    positions = [list(row) for row in dense.positions]
    positions[2][0] = replace(positions[2][0], blank_start=True)
    packed = compress_v1(dense.sheet, positions)
    assert packed[0][0].is_blank_key and packed[0][0].exposure == 2
    assert packed[2][0].is_blank_key and packed[2][0].exposure == 2
    assert expand_legacy(packed).sheet == dense.sheet
