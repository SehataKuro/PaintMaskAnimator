"""Rust 正本の文書（document_live）の近道が、今の Python の手順と同じ結果になること（段階8-4 C4a）。

近道：resolve を Rust で一度に求める、読むだけの操作を窓口のまま当てる、同じ値の代入を省く、
列の表示設定は列だけ Rust へ書く、窓口のまま試して素のグラフが要る書き込みでやり直す。
"""
import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage

from paintmaskanimator import document_live
from paintmaskanimator.document import GraphDocument
from paintmaskanimator.models import ArchiveHint, Frame, Layer
from paintmaskanimator.native import resolve_timeline
from paintmaskanimator.pma_image import PmaImage


def _frames(hold=2, count=6):
    frames = []
    for column in range(count):
        layers = []
        for index, name in enumerate(("A", "B")):
            layer = Layer(name, PmaImage.blank(8, 8))
            if column % 3 != 2 or index == 0:
                layer.has_content = True
                layer.sequence_number = column // hold + 1
            layers.append(layer)
        frames.append(Frame(layers))
    return frames


def _pair(**shape):
    plain = GraphDocument()
    plain.install_legacy_frames(_frames(**shape))
    live = document_live.Document()
    live.install_legacy_frames(_frames(**shape))
    assert live._live()["graph"] is None
    return plain, live


def _round_trips(document, monkeypatch):
    calls = []
    original = document_live.Document._demote

    def demote(self):
        if self._live()["graph"] is None:
            calls.append(True)
        return original(self)
    monkeypatch.setattr(document_live.Document, "_demote", demote)
    return calls


def _signature(document):
    return [[(slot.has_content, slot.is_blank_key, slot.sequence_number, slot.sequence_only,
              slot.blank_start, slot.visible, slot.opacity, slot.color_filter_rgb)
             for slot in frame.layers] for frame in document.frames]


def test_resolve_uses_core_and_matches_python():
    plain, live = _pair()
    expected = resolve_timeline(plain.frames)
    assert live.frames._resolve_sources() is not None
    assert np.array_equal(resolve_timeline(live.frames), expected)
    assert np.array_equal(resolve_timeline(list(live.frames)), expected)
    # 書き込みのあとは作り直す。
    plain.frames[1].layers[1].has_content = False
    live.frames[1].layers[1].has_content = False
    assert np.array_equal(resolve_timeline(live.frames), resolve_timeline(plain.frames))


def test_reads_run_in_place(monkeypatch):
    plain, live = _pair()
    calls = _round_trips(live, monkeypatch)
    track = live.tracks[0]
    assert live.placed_numbers(track) == plain.placed_numbers(plain.tracks[0])
    assert live.timeline_step_columns(1) == plain.timeline_step_columns(1)
    assert live.timeline_block_at(4, 1) == plain.timeline_block_at(4, 1)
    assert live.next_drawing_number(0) == plain.next_drawing_number(0)
    target = live.resolve_numbered_target(0, 0, 2, sheet=True)
    assert target.slot is live.frames[2].layers[0] and target.track is track
    assert calls == []


def test_unchanged_writes_and_pixels_skip_the_round_trip(monkeypatch):
    plain, live = _pair()
    calls = _round_trips(live, monkeypatch)
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(255, 0, 0, 255))
    expected = plain.write_drawing_patch(0, 0, patch, (1, 1, 2, 2), has_content=True)
    assert live.write_drawing_patch(0, 0, patch, (1, 1, 2, 2), has_content=True) == expected
    assert live.set_drawing_content(0, 0, True) == plain.set_drawing_content(0, 0, True)
    assert calls == []
    assert (live.frames[1].layers[0].image.read_rgba(1, 1, 2, 2)
            == plain.frames[1].layers[0].image.read_rgba(1, 1, 2, 2)).all()
    # 変わる代入は Rust の命令で当てる（C4d-21、test_set_drawing_content_uses_core_command）。
    assert live.set_drawing_content(0, 1, False) == plain.set_drawing_content(0, 1, False)
    assert calls == []
    assert _signature(live) == _signature(plain)


def test_track_settings_write_one_track(monkeypatch):
    plain, live = _pair()
    calls = _round_trips(live, monkeypatch)
    for document in (plain, live):
        document.frames[0].layers[1].opacity = 0.25
    assert (live.set_track_properties((0, 1), visible=False, opacity=0.25)
            == plain.set_track_properties((0, 1), visible=False, opacity=0.25))
    assert calls == []
    assert _signature(live) == _signature(plain)


def test_trial_rolls_back_before_retrying(monkeypatch):
    plain, live = _pair()
    calls = _round_trips(live, monkeypatch)
    # 2つめの列の色は Rust で表せない（整数3つでない）ので、試行は素のグラフが要る。
    settings = {"opacity": 0.5, "color_filter_rgb": (1.5, 2, 3)}
    expected = plain.set_track_properties((0, 1), **settings)
    assert live.set_track_properties((0, 1), **settings) == expected
    assert calls
    assert live._live()["graph"] is not None  # 表せない値なので素の状態のまま
    assert _signature(live) == _signature(plain)


def test_review_cases_match_python():
    plain, live = _pair()
    # 反復の途中の変更は、今の list と同じく見える。
    for document in (plain, live):
        seen = 0
        for _ in document.frames:
            seen += 1
            if seen == 1:
                document.ensure_frame_count(len(document.frames) + 1)
        assert seen == 7
    # tween の 1 と 1.0 は別の値として入る。
    for document in (plain, live):
        document.frames[0].layers[0].tween = {"scale": 1}
        document.frames[0].layers[0].tween = {"scale": 1.0}
    assert type(live.frames[0].layers[0].tween["scale"]) is float
    # 局所の絵の無い位置の clone は、今と同じく文書に絵を作る。
    for document in (plain, live):
        slot = document.frames[2].layers[1]
        slot.clone()
        assert "_cell" in slot.__dict__
    assert _signature(live) == _signature(plain)


def test_snapshot_is_lazy_and_matches_python(monkeypatch):
    from paintmaskanimator.document import DocumentSnapshot
    from paintmaskanimator.undo_entries import DocUndo

    plain, live = _pair()
    calls = _round_trips(live, monkeypatch)
    expected = plain.snapshot()
    snapshot = live.snapshot()
    assert calls == [] and type(snapshot) is not DocumentSnapshot
    assert isinstance(snapshot, DocumentSnapshot)
    # 大きさは中身を作らずに求め、今の手順の値と同じ。
    assert DocUndo(snapshot).nbytes == DocUndo(expected).nbytes
    assert snapshot.__dict__["_value"] is None
    # 写しを取ったあとの描き込みは写しに入らない。
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(0, 0, 255, 255))
    live.write_drawing_patch(0, 0, patch, (0, 0, 2, 2))
    plain.write_drawing_patch(0, 0, patch, (0, 0, 2, 2))
    assert (snapshot.frames[0].layers[0].image.read_rgba(0, 0, 2, 2)
            == expected.frames[0].layers[0].image.read_rgba(0, 0, 2, 2)).all()

    def signature(snap):
        frames = [[(s.has_content, s.sequence_number, s.sequence_only, s.name) for s in f.layers]
                  for f in snap.frames]
        return len(snap), snap[1:5], frames, [sorted(track.cells) for track in snap.tracks]
    assert signature(snapshot) == signature(expected)
    assert snapshot == tuple(snapshot) and snapshot[:5][1:] == expected[:5][1:]
    other = live.snapshot()
    assert snapshot == snapshot and not (snapshot != snapshot)
    assert (other == snapshot) == (tuple(other) == tuple(snapshot))
    # 戻すと、今の手順と同じ状態になる。
    live.restore_snapshot(snapshot)
    plain.restore_snapshot(expected)
    assert _signature(live) == _signature(plain)
    assert (live.frames[0].layers[0].image.read_rgba(0, 0, 2, 2)
            == plain.frames[0].layers[0].image.read_rgba(0, 0, 2, 2)).all()


def test_duplicate_frame_uses_core_command_and_matches_python(monkeypatch):
    plain, live = _pair()
    calls = _round_trips(live, monkeypatch)
    # キーのコマの複製（保持の途中ではない）。局所の絵は元の位置と同じ Cell を共有する。
    held_before = live.frames[2].layers[1]
    assert live.duplicate_frame(2) == plain.duplicate_frame(2)
    assert calls == []
    assert _signature(live) == _signature(plain)
    assert live.frames[2].layers[1] is held_before
    assert live.frames[3].layers[1]._cell is live.frames[2].layers[1]._cell
    assert plain.frames[3].layers[1]._cell is plain.frames[2].layers[1]._cell
    # 保持の途中の複製：保持の後ろの位置は新しい Layer になり、古い Layer は文書から外れる。
    plain, live = _pair(hold=5, count=10)
    calls = _round_trips(live, monkeypatch)
    moved = live.frames[2].layers[0]
    old_plain = plain.frames[2].layers[0]
    tail = live.frames[4].layers[0]
    assert live.duplicate_frame(1) == plain.duplicate_frame(1)
    assert calls == []
    assert _signature(live) == _signature(plain)
    in_live = any(slot is moved for frame in live.frames for slot in frame.layers)
    in_plain = any(slot is old_plain for frame in plain.frames for slot in frame.layers)
    assert in_live == in_plain
    assert (moved.has_content, moved.sequence_number) == (old_plain.has_content,
                                                          old_plain.sequence_number)
    assert any(slot is tail for frame in live.frames for slot in frame.layers)


def test_delete_frame_uses_core_command_and_matches_python(monkeypatch):
    plain, live = _pair(hold=3, count=9)
    # 末尾のコマが空かない削除（最後のコマにほかの列の絵がある）。
    calls = _round_trips(live, monkeypatch)
    deleted, plain_deleted = live.frames[1].layers[1], plain.frames[1].layers[1]
    # 5コマ目の列 B は絵の無い位置（局所の Cell を持つ）。
    following, plain_following = live.frames[5].layers[1], plain.frames[5].layers[1]
    cell = following._cell
    image = following.image
    assert live.delete_frame(1, 1) == plain.delete_frame(1, 1)
    assert calls == []
    assert _signature(live) == _signature(plain)
    for slot, plain_slot in ((deleted, plain_deleted), (following, plain_following)):
        assert (any(s is slot for f in live.frames for s in f.layers)
                == any(s is plain_slot for f in plain.frames for s in f.layers))
        assert (slot.has_content, slot.sequence_number, slot.sequence_only) == (
            plain_slot.has_content, plain_slot.sequence_number, plain_slot.sequence_only)
    # 局所の Cell と画像は写しとともに一つ前の位置へ移る。
    assert plain.frames[4].layers[1]._cell is plain_following._cell
    assert live.frames[4].layers[1]._cell is cell
    assert (live.frames[4].layers[1].image is image) == (
        plain.frames[4].layers[1].image is plain_following.image)


def test_delete_frame_that_trims_frames_uses_core_command_and_matches_python(monkeypatch):
    # (コマ数を足す数, 消すコマ, 消す列)。
    cases = ((0, 5, 0),   # A の最後のコマを消す（B の最後は空き、末尾の1コマが空く）
             (3, 7, 1),   # 足した空きのコマの途中を消す（列を寄せた位置ごと末尾を切る）
             (3, 6, 0),   # 空きの最初のコマを消す（局所の絵が切るコマへ移る）
             (2, 7, 1))   # 最後のコマを消す（その場で消して切る）
    for extra, column, layer_index in cases:
        plain, live = _pair(hold=2, count=6)
        for document in (plain, live):
            document.ensure_frame_count(6 + extra)
        live._promote()
        count = len(live.frames)
        held = [(live.frames[i], plain.frames[i]) for i in range(count)]
        slots = [(live.frames[i].layers[j], plain.frames[i].layers[j])
                 for i in range(count) for j in range(2)]
        cells = [(live.frames[i].layers[j]._cell, plain.frames[i].layers[j]._cell)
                 for i in range(count) for j in range(2)]
        images = [(live.frames[i].layers[j].image, plain.frames[i].layers[j].image)
                  for i in range(count) for j in range(2)]
        calls = _round_trips(live, monkeypatch)
        changes = live.delete_frame(column, layer_index)
        assert changes == plain.delete_frame(column, layer_index)
        assert calls == [], (extra, column, layer_index)
        assert len(live.frames) == len(plain.frames) < count
        monkeypatch.undo()

        def where(obj, document):
            return [(i, j) for i, frame in enumerate(document.frames)
                    for j, slot in enumerate(frame.layers) if slot is obj]

        for frame, plain_frame in held:
            assert any(f is frame for f in live.frames) == any(f is plain_frame for f in plain.frames)
            assert [(s.has_content, s.sequence_number, s.sequence_only, s.is_blank_key)
                    for s in frame.layers] == [
                (s.has_content, s.sequence_number, s.sequence_only, s.is_blank_key)
                for s in plain_frame.layers]
            # 外れたコマの位置が、誰が持っていた位置か（同一性の分け方）。
            assert [[k for k, (a, _) in enumerate(slots) if a is s] for s in frame.layers] == [
                [k for k, (_, b) in enumerate(slots) if b is s] for s in plain_frame.layers]
            assert [[k for k, (a, _) in enumerate(cells) if a is s._cell] for s in frame.layers] == [
                [k for k, (_, b) in enumerate(cells) if b is s._cell] for s in plain_frame.layers]
        for slot, plain_slot in slots:
            assert where(slot, live) == where(plain_slot, plain)
            assert (slot.has_content, slot.sequence_number) == (
                plain_slot.has_content, plain_slot.sequence_number)
            assert [k for k, (a, _) in enumerate(cells) if a is slot._cell] == [
                k for k, (_, b) in enumerate(cells) if b is plain_slot._cell]
        for image, plain_image in images:
            if image is None:
                assert plain_image is None
                continue
            assert image.rgba().tobytes() == plain_image.rgba().tobytes()
        assert _whole_signature(live) == _whole_signature(plain)



def test_delete_track_frame_uses_core_command_and_matches_python(monkeypatch):
    plain, live = _pair(hold=3, count=9)
    calls = _round_trips(live, monkeypatch)
    following, plain_following = live.frames[5].layers[1], plain.frames[5].layers[1]
    cell = following._cell
    assert live.delete_track_frame(1, 1) == plain.delete_track_frame(1, 1)
    assert calls == []
    assert _signature(live) == _signature(plain)
    assert len(live.frames) == len(plain.frames)
    assert plain.frames[4].layers[1]._cell is plain_following._cell
    assert live.frames[4].layers[1]._cell is cell
    assert not any(s is following for f in live.frames for s in f.layers)
    assert not any(s is plain_following for f in plain.frames for s in f.layers)


def test_insert_track_frame_uses_core_command_and_matches_python(monkeypatch):
    plain, live = _pair(hold=3, count=9)
    calls = _round_trips(live, monkeypatch)
    # 保持の先頭（4コマ目は列 B の保持の先頭ではないので 3コマ目）に挿入する。
    moving, plain_moving = live.frames[5].layers[1], plain.frames[5].layers[1]
    cell = moving._cell
    assert live.insert_track_frame(3, 1) == plain.insert_track_frame(3, 1)
    assert calls == []
    assert _signature(live) == _signature(plain)
    assert len(live.frames) == len(plain.frames)
    assert plain.frames[6].layers[1]._cell is plain_moving._cell
    assert live.frames[6].layers[1]._cell is cell
    assert (any(s is moving for f in live.frames for s in f.layers)
            == any(s is plain_moving for f in plain.frames for s in f.layers))
    assert (moving._cell is cell) == (plain_moving._cell is plain.frames[6].layers[1]._cell)


def test_shift_left_review_cases(monkeypatch):
    # 最後のコマを消す（その場で消し、局所の Cell は外れる）。
    plain, live = _pair(hold=3, count=9)
    last, plain_last = live.frames[8].layers[1], plain.frames[8].layers[1]
    cell, plain_cell = last._cell, plain_last._cell
    assert live.delete_track_frame(8, 1) == plain.delete_track_frame(8, 1)
    assert _signature(live) == _signature(plain)
    assert (live.frames[8].layers[1] is last) == (plain.frames[8].layers[1] is plain_last)
    assert (live.frames[8].layers[1]._cell is cell) == (
        plain.frames[8].layers[1]._cell is plain_cell)
    # 外れた Layer は、文書の一つ前の位置へ移った同じ Cell を指す。
    plain, live = _pair(hold=3, count=9)
    following, plain_following = live.frames[5].layers[1], plain.frames[5].layers[1]
    live.delete_track_frame(1, 1)
    plain.delete_track_frame(1, 1)
    assert plain_following._cell is plain.frames[4].layers[1]._cell
    assert following._cell is live.frames[4].layers[1]._cell


def test_create_blank_key_uses_core_command_and_matches_python(monkeypatch):
    # 保持の途中を分ける場合と、未使用のセルに作る場合（直前のキーから伸ばす）。
    for column, layer_index in ((2, 0), (8, 1)):
        plain, live = _pair(hold=5, count=10)
        calls = _round_trips(live, monkeypatch)
        held, plain_held = live.frames[3].layers[0], plain.frames[3].layers[0]
        assert (live.create_blank_key(column, layer_index)
                == plain.create_blank_key(column, layer_index))
        assert calls == []
        assert _signature(live) == _signature(plain)
        assert (any(s is held for f in live.frames for s in f.layers)
                == any(s is plain_held for f in plain.frames for s in f.layers))
        # 後ろの保持の位置は空キーの位置と同じ局所の Cell を共有する。
        head = live.frames[column].layers[layer_index]
        plain_head = plain.frames[column].layers[layer_index]
        if column + 1 < 5:
            assert plain.frames[column + 1].layers[layer_index]._cell is plain_head._cell
            assert live.frames[column + 1].layers[layer_index]._cell is head._cell


def _plain_flags(document):
    """素の Layer の配置待ちの所属の印（_pending_detached）。live は素の状態に落として読む。"""
    frames = document._demote().plain.frames if hasattr(document, "_demote") else document.frames
    return [[bool(slot.__dict__.get("_pending_detached")) for slot in frame.layers]
            for frame in frames]


def test_create_blank_key_at_the_last_frame(monkeypatch):
    # 保持の最後のセル（保持が文書の最後のコマで終わる）と、最後のコマの未使用セル。
    for shape, column, layer_index in (({"hold": 5, "count": 10}, 9, 0),
                                       ({"hold": 3, "count": 9}, 8, 1)):
        plain, live = _pair(**shape)
        calls = _round_trips(live, monkeypatch)
        assert (live.create_blank_key(column, layer_index)
                == plain.create_blank_key(column, layer_index))
        assert calls == []
        assert _signature(live) == _signature(plain)
        assert len(live.frames) == len(plain.frames)


def test_create_blank_key_fills_a_gap_from_the_previous_key(monkeypatch):
    # 直前のキー（4コマ目）との間が3つある未使用セル。間の位置は直前のキーの写しになる。
    plain, live = _pair(hold=5, count=10)
    for document in (plain, live):
        document.clear_sheet_span(1, 5, 8)
    live._promote()
    assert live._live()["graph"] is None
    calls = _round_trips(live, monkeypatch)
    gap, plain_gap = live.frames[6].layers[1], plain.frames[6].layers[1]
    assert live.create_blank_key(8, 1) == plain.create_blank_key(8, 1)
    assert calls == []
    assert _signature(live) == _signature(plain)
    assert (any(s is gap for f in live.frames for s in f.layers)
            == any(s is plain_gap for f in plain.frames for s in f.layers))
    assert (gap.has_content, gap.sequence_number) == (plain_gap.has_content,
                                                      plain_gap.sequence_number)
    assert _plain_flags(live) == _plain_flags(plain)


def test_create_blank_key_keeps_pending_detached_of_the_blank_position(monkeypatch):
    # B 列 [1,1,空,空,空] で 4コマ目に空キー（2・3 は直前のキーの写し、配置待ちは写しが持つ）、
    # 続けて 3コマ目（保持の途中）に空キー。空キーの位置はその場で書き換わるので印が残る。
    plain, live = _pair(hold=5, count=5)
    for document in (plain, live):
        document.clear_sheet_span(1, 2, 5)
    live._promote()
    calls = _round_trips(live, monkeypatch)
    for column in (4, 3):
        assert live.create_blank_key(column, 1) == plain.create_blank_key(column, 1)
        assert _signature(live) == _signature(plain)
    assert calls == []
    assert _plain_flags(live) == _plain_flags(plain)


def _held_windows(document, layer_index):
    """列の各位置の Layer と局所の Cell（命令の前に持っておく窓口）。"""
    return [(frame.layers[layer_index], frame.layers[layer_index]._cell)
            for frame in document.frames]


def _held_view(document, layer_index, held):
    """持っておいた Layer・Cell が、命令のあと文書のどこにあるか（外れた Layer は値も）。"""
    slots = [frame.layers[layer_index] for frame in document.frames]
    cells = [slot._cell for slot in slots]
    view = []
    for layer, cell in held:
        place = next((i for i, slot in enumerate(slots) if slot is layer), None)
        values = None if place is not None else (
            layer.has_content, layer.is_blank_key, layer.sequence_number, layer.sequence_only,
            layer.blank_start)
        shares = [i for i, other in enumerate(cells) if other is layer._cell]
        cell_at = [i for i, other in enumerate(cells) if other is cell]
        view.append((place, values, shares, cell_at))
    return view


def test_create_blank_key_at_a_key_shifts_the_track_and_matches_python(monkeypatch):
    # キーを選んだとき：保持の後ろへ同じ長さの空キーを入れ、後ろの位置を寄せる。保持の長さ
    # 1〜3、途中のキー・最後のキー（末尾の空いた位置は移り先が無く落ちる）・コマを足す場合。
    for shape, column, layer_index in (({"hold": 2, "count": 6}, 2, 0),
                                       ({"hold": 3, "count": 9}, 3, 0),
                                       ({"hold": 3, "count": 9}, 6, 0),
                                       ({"hold": 3, "count": 9}, 0, 1),
                                       ({"hold": 1, "count": 6}, 4, 0),
                                       ({"hold": 5, "count": 10}, 5, 0),
                                       ({"hold": 5, "count": 10}, 0, 1)):
        plain, live = _pair(**shape)
        held = _held_windows(live, layer_index)
        plain_held = _held_windows(plain, layer_index)
        calls = _round_trips(live, monkeypatch)
        assert (live.create_blank_key(column, layer_index)
                == plain.create_blank_key(column, layer_index)), (shape, column)
        assert calls == [], (shape, column)
        assert len(live.frames) == len(plain.frames)
        assert _signature(live) == _signature(plain), (shape, column)
        assert (_held_view(live, layer_index, held)
                == _held_view(plain, layer_index, plain_held)), (shape, column)
        assert _plain_flags(live) == _plain_flags(plain)
        live._promote()
        assert _v1_records(live) == _v1_records(plain), (shape, column)
        live._promote()
        assert _whole_signature(live) == _whole_signature(plain), (shape, column)


def _held_images(document, layer_index):
    """列の各位置の Layer と画像（命令の前に持っておく窓口。局所の Cell は読まない）。"""
    return [(frame.layers[layer_index], frame.layers[layer_index].image)
            for frame in document.frames]


def _held_image_view(document, layer_index, held):
    """持っておいた Layer・画像が命令のあと文書のどこにあるか、外れた Layer の値と画素。"""
    import hashlib
    slots = [frame.layers[layer_index] for frame in document.frames]
    view = []
    for layer, image in held:
        place = next((i for i, slot in enumerate(slots) if slot is layer), None)
        values = None if place is not None else (
            layer.has_content, layer.is_blank_key, layer.sequence_number, layer.sequence_only,
            layer.blank_start, layer.initial_image is None,
            hashlib.sha1(layer.image.rgba().tobytes()).hexdigest())
        image_at = [i for i, slot in enumerate(slots) if slot.image is image]
        view.append((place, values, image_at, hashlib.sha1(image.rgba().tobytes()).hexdigest()))
    return view


def test_create_blank_key_at_a_key_with_pending_and_bare_positions(monkeypatch):
    # キーを選んだとき：局所の Cell を読んでいない位置（寄せた先に空の Cell ができる）、
    # 画像の窓口、番号のある配置待ちの位置が後ろにある場合、E〜E+n-1 が末尾から落ちる場合。
    from paintmaskanimator import pma_image, utils
    for shape, column, layer_index, pending in (({"hold": 2, "count": 8}, 2, 0, False),
                                                ({"hold": 3, "count": 9}, 0, 1, False),
                                                ({"hold": 2, "count": 6}, 4, 1, False),
                                                ({"hold": 2, "count": 8}, 0, 1, True),
                                                ({"hold": 2, "count": 8}, 3, 1, True)):
        plain, live = _pair(**shape)
        for document in (plain, live):
            _paint_numbers(document, 0, shape["hold"])
            if pending:
                document.frames[5].layers[1].set_pending(True)
                document.insert_sequence_blank(0, 1)
        live._promote()
        held = _held_images(live, layer_index)
        plain_held = _held_images(plain, layer_index)
        monkeypatch.setattr(pma_image, "_last_import", None)
        monkeypatch.setattr(utils._BlankTemplate, "model_image", None)
        utils.blank_model_image()
        calls = _round_trips(live, monkeypatch)
        assert (live.create_blank_key(column, layer_index)
                == plain.create_blank_key(column, layer_index)), (shape, column)
        assert calls == [], (shape, column)
        assert _signature(live) == _signature(plain), (shape, column)
        assert (_held_image_view(live, layer_index, held)
                == _held_image_view(plain, layer_index, plain_held)), (shape, column)
        assert _plain_flags(live) == _plain_flags(plain)
        live._promote()
        assert _v1_records(live) == _v1_records(plain), (shape, column)
        live._promote()
        assert _whole_signature(live) == _whole_signature(plain), (shape, column)


def test_restore_snapshot_uses_core_and_matches_python(monkeypatch):
    plain, live = _pair(hold=5, count=10)
    for document in (plain, live):
        document.create_blank_key(7, 0)
    live._promote()
    expected = plain.snapshot()
    snapshot = live.snapshot()
    assert snapshot.__dict__["_value"] is None
    runs = []
    original = document_live.Document._run

    def run(self, name, args, kwargs):
        runs.append(name)
        return original(self, name, args, kwargs)
    monkeypatch.setattr(document_live.Document, "_run", run)
    # 写しのあとに構造と画素を変える。
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(0, 255, 0, 255))
    for document in (plain, live):
        document.duplicate_frame(2)
        document.write_drawing_patch(0, 0, patch, (0, 0, 2, 2))
    old_frames, plain_old_frames = live.frames, plain.frames
    old_tracks, plain_old_tracks = live.tracks, plain.tracks
    old_slot = live.frames[1].layers[0]
    plain_old_slot = plain.frames[1].layers[0]
    for _ in range(2):
        assert live.restore_snapshot(snapshot) == plain.restore_snapshot(expected)
        assert "restore_snapshot" not in runs
        assert snapshot.__dict__["_value"] is None
        assert _signature(live) == _signature(plain)
        # 戻す前の並び・位置は文書から外れ、戻す前の値のまま。
        assert len(old_frames) == len(plain_old_frames) == 11
        assert len(old_tracks) == len(plain_old_tracks)
        assert (live.frames is old_frames) == (plain.frames is plain_old_frames)
        assert (any(s is old_slot for f in live.frames for s in f.layers)
                == any(s is plain_old_slot for f in plain.frames for s in f.layers))
        assert (old_slot.has_content, old_slot.sequence_number) == (
            plain_old_slot.has_content, plain_old_slot.sequence_number)
        # 戻したあとに描いても写しは汚れない。
        for document in (plain, live):
            document.write_drawing_patch(0, 0, patch, (2, 2, 2, 2))
        assert (live.frames[0].layers[0].image.read_rgba(0, 0, 4, 4)
                == plain.frames[0].layers[0].image.read_rgba(0, 0, 4, 4)).all()
    assert (snapshot.frames[0].layers[0].image.read_rgba(0, 0, 4, 4)
            == expected.frames[0].layers[0].image.read_rgba(0, 0, 4, 4)).all()


def test_link_layer_tracks_runs_in_place(monkeypatch):
    plain, live = _pair(hold=5, count=10)
    calls = _round_trips(live, monkeypatch)
    old_tracks, plain_old_tracks = live.tracks, plain.tracks
    assert live.link_layer_tracks() == plain.link_layer_tracks()
    assert calls == []
    # 今の手順は tracks に新しいリストを入れる（中身の列は同じ）。
    assert (live.tracks is old_tracks) == (plain.tracks is plain_old_tracks)
    assert [t is o for t, o in zip(live.tracks, old_tracks)] == [True, True]
    assert _signature(live) == _signature(plain)
    # 配置待ちの所属を位置が持つ位置があれば、今の経路（列へつなぎ直す）。
    for document in (plain, live):
        document.clear_sheet_span(1, 2, 10)
        document.create_blank_key(9, 1)
    live._promote()
    assert live._live()["graph"] is None
    assert live.link_layer_tracks() == plain.link_layer_tracks()
    assert _signature(live) == _signature(plain)
    assert _plain_flags(live) == _plain_flags(plain)


def _same_position(live, plain, column, layer_index, run):
    """位置の Layer・局所の Cell・画像の同一性と値・列のバンクが、今の手順と同じになること。"""
    slot, plain_slot = live.frames[column].layers[layer_index], plain.frames[column].layers[layer_index]
    cell, plain_cell = slot._cell, plain_slot._cell
    image, plain_image = slot.image, plain_slot.image
    assert run(live) == run(plain)
    assert _signature(live) == _signature(plain)
    assert ((live.frames[column].layers[layer_index] is slot)
            == (plain.frames[column].layers[layer_index] is plain_slot))
    assert (slot._cell is cell) == (plain_slot._cell is plain_cell)
    assert (slot.image is image) == (plain_slot.image is plain_image)
    assert (slot.has_content, slot.sequence_number) == (plain_slot.has_content,
                                                        plain_slot.sequence_number)
    assert sorted(live.tracks[layer_index].cells) == sorted(plain.tracks[layer_index].cells)
    assert _plain_flags(live) == _plain_flags(plain)
    assert _v1_records(live) == _v1_records(plain)


def _v1_records(document):
    """素の Layer の旧形式の記録（_v1_position）の有無と区切りの印。live は素の状態に落として読む。"""
    frames = document._demote().plain.frames if hasattr(document, "_demote") else document.frames
    records = []
    for frame in frames:
        row = []
        for slot in frame.layers:
            v1 = slot.__dict__.get("_v1_position")
            row.append(None if v1 is None else (v1.key_start, v1.blank_start, v1.pending,
                                                v1.legacy is not None))
        records.append(row)
    return records


def test_clear_sheet_span_uses_core_command_and_matches_python(monkeypatch):
    for shape, args in (({"hold": 5, "count": 10}, (0, 2, 4)),
                        ({"hold": 5, "count": 10}, (1, 0, 10)),
                        ({"hold": 3, "count": 9}, (1, 4, 9))):
        plain, live = _pair(**shape)
        calls = _round_trips(live, monkeypatch)
        _same_position(live, plain, args[1], args[0],
                       lambda document, args=args: document.clear_sheet_span(*args))
        assert len(calls) == 1  # 最後の _plain_flags だけが素の状態に落とす


def test_new_sheet_cell_and_number_use_core_command_and_match_python(monkeypatch):
    for name, shape, (column, layer_index) in (
            ("new_sheet_cell", {"hold": 5, "count": 10}, (3, 0)),
            ("new_sheet_cell", {"hold": 3, "count": 9}, (2, 1)),
            ("new_sheet_number", {"hold": 5, "count": 10}, (3, 0)),
            ("new_sheet_number", {"hold": 5, "count": 10}, (0, 1))):
        plain, live = _pair(**shape)
        calls = _round_trips(live, monkeypatch)
        _same_position(live, plain, column, layer_index,
                       lambda document, name=name, column=column, layer_index=layer_index: (
                           getattr(document, name)(column, layer_index)))
        assert len(calls) == 1


def test_split_sheet_clip_uses_core_command_and_matches_python(monkeypatch):
    for shape, (column, layer_index) in (({"hold": 5, "count": 10}, (2, 0)),
                                         ({"hold": 5, "count": 10}, (4, 0)),
                                         ({"hold": 3, "count": 9}, (1, 1)),
                                         ({"hold": 5, "count": 10}, (7, 1))):
        plain, live = _pair(**shape)
        calls = _round_trips(live, monkeypatch)
        _same_position(live, plain, column, layer_index,
                       lambda document, column=column, layer_index=layer_index: (
                           document.split_sheet_clip(column, layer_index)))
        assert len(calls) == 1  # 最後の _plain_flags だけが素の状態に落とす


def test_split_sheet_clip_keeps_old_local_artwork_detached(monkeypatch):
    # 分けた位置の局所の絵の窓口は、命令の前の画素のまま文書から外れる。
    plain, live = _pair(hold=5, count=10)
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(0, 0, 255, 255))
    for document in (plain, live):
        document.split_sheet_clip(3, 0)
        document.write_drawing_patch(3, 0, patch, (0, 0, 2, 2))
    head, plain_head = live.frames[0].layers[0].image, plain.frames[0].layers[0].image
    assert not head.read_rgba(0, 0, 1, 1).any() and not plain_head.read_rgba(0, 0, 1, 1).any()
    assert (live.frames[4].layers[0].image.read_rgba(0, 0, 1, 1).any()
            == plain.frames[4].layers[0].image.read_rgba(0, 0, 1, 1).any())
    assert _signature(live) == _signature(plain)


def test_insert_sequence_blank_uses_core_command_and_matches_python(monkeypatch):
    for shape, (layer_index, after) in (({"hold": 2, "count": 6}, (0, 1)),
                                        ({"hold": 2, "count": 6}, (1, 0)),
                                        ({"hold": 3, "count": 9}, (0, 3)),
                                        ({"hold": 3, "count": 9}, (1, 9))):
        plain, live = _pair(**shape)
        live._promote()
        bank = {number: (cell, cell.image) for number, cell in live.tracks[layer_index].cells.items()}
        plain_bank = {number: (cell, cell.image)
                      for number, cell in plain.tracks[layer_index].cells.items()}
        slots = [frame.layers[layer_index] for frame in live.frames]
        calls = _round_trips(live, monkeypatch)
        assert (live.insert_sequence_blank(layer_index, after)
                == plain.insert_sequence_blank(layer_index, after))
        assert calls == []
        assert _signature(live) == _signature(plain)
        assert all(frame.layers[layer_index] is slot for frame, slot in zip(live.frames, slots))
        # 送った番号のバンクの Cell・画像は、同じオブジェクトのまま新しい番号へ移る。
        moved = {number: [n for n, cell in live.tracks[layer_index].cells.items()
                          if cell is pair[0] and cell.image is pair[1]]
                 for number, pair in bank.items()}
        plain_moved = {number: [n for n, cell in plain.tracks[layer_index].cells.items()
                                if cell is pair[0] and cell.image is pair[1]]
                       for number, pair in plain_bank.items()}
        assert moved == plain_moved
        assert sorted(live.tracks[layer_index].cells) == sorted(plain.tracks[layer_index].cells)
        # バンクの番号の並び（dict の挿入順）は比べない（graph_signature と同じ。Rust は番号順に
        # 持ち、保存は番号順に並べ直す）。署名の画像の番号は出てきた順なので、並びをそろえる。
        for track in plain.tracks:
            track.cells = dict(sorted(track.cells.items()))
        for index in range(2):
            assert (_column_signature([frame.layers[index] for frame in live.frames], monkeypatch)
                    == _column_signature([frame.layers[index] for frame in plain.frames],
                                         monkeypatch))
        assert _plain_flags(live) == _plain_flags(plain)
        assert _v1_records(live) == _v1_records(plain)


def _whole_signature(document):
    """文書全体の署名。バンクは番号順に並べ、画像の世代は文書全体で出てきた順に振り直す。
    live は素の状態に落として読む（命令のあとに呼ぶ）。"""
    from paintmaskanimator import document_core
    if hasattr(document, "_demote"):
        document = document._demote().plain
    for track in document.tracks:
        track.cells = dict(sorted(track.cells.items()))
    original = document_core._image_signature
    seen = {}

    def image_signature(image):
        value = original(image)
        if isinstance(value, tuple) and len(value) == 4:
            value = (value[0], value[1], seen.setdefault(value[2], len(seen)), value[3])
        return value
    document_core._image_signature = image_signature
    try:
        return document_core.graph_signature(document)
    finally:
        document_core._image_signature = original


def _colored_layer(name, rgba, *, content=True, number=None, blank=False):
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(*rgba))
    layer = Layer(name, PmaImage(image))
    layer.has_content, layer.sequence_number, layer.is_blank_key = content, number, blank
    return layer


def test_split_sheet_clip_with_local_artwork_takes_the_current_path(monkeypatch):
    # 先頭が番号を残した空キー（局所の絵を写す）、番号の無い内容の保持（局所の絵）では、
    # 今の手順は局所の Cell を残して写すので、Rust の命令は使わない。
    def retained():
        return ([Frame([_colored_layer("A", (255, 0, 0, 255), number=1)]) for _ in range(2)]
                + [Frame([_colored_layer("A", (0, 0, 255, 255), content=False, number=1,
                                         blank=True)]) for _ in range(3)])

    def unnumbered():
        return [Frame([_colored_layer("A", (0, 255, 0, 255))]) for _ in range(3)]
    for frames, column in ((retained, 3), (unnumbered, 1)):
        plain = GraphDocument()
        plain.install_legacy_frames(frames())
        live = document_live.Document()
        live.install_legacy_frames(frames())
        live._promote()
        calls = _round_trips(live, monkeypatch)
        assert live.split_sheet_clip(column, 0) == plain.split_sheet_clip(column, 0)
        assert calls
        assert _whole_signature(live) == _whole_signature(plain)


def test_insert_sequence_blank_imports_the_blank_like_python(monkeypatch):
    # 今の手順の Cell(blank_image()) は直前の取り込みしだいで雛形と世代を共有する。
    from paintmaskanimator import pma_image
    for colored in (False, True):
        documents = []
        for factory in (GraphDocument, document_live.Document):
            document = factory()
            document.install_legacy_frames(_frames(hold=2, count=6))
            if factory is not GraphDocument:
                document._promote()
                calls = _round_trips(document, monkeypatch)
            monkeypatch.setattr(pma_image, "_last_import", None)
            if colored:
                image = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(QColor(9, 9, 9, 255))
                PmaImage(image)
            documents.append((document, document.insert_sequence_blank(0, 1)))
        (plain, expected), (live, result) = documents
        assert result == expected
        assert calls == []
        assert _whole_signature(live) == _whole_signature(plain)


def test_insert_sequence_blank_drops_dense_import_of_shifted_numbers_like_python():
    # 今の手順は送った番号を代入するので、その位置の取り込み時の記録（_dense_import）が消える。
    from paintmaskanimator.legacy_v1 import BLANK, EMPTY
    from paintmaskanimator.timeline_metadata import set_dense_import
    documents = []
    for factory in (GraphDocument, document_live.Document):
        frames = _frames(hold=2, count=6)
        for column in (0, 4):
            slot = frames[column].layers[0]
            set_dense_import(slot, ((True, False, slot.sequence_number, False, False),
                                    BLANK, EMPTY))
        document = factory()
        document.install_legacy_frames(frames)
        documents.append((document, document.insert_sequence_blank(0, 1)))
    (plain, expected), (live, result) = documents
    assert result == expected
    assert _whole_signature(live) == _whole_signature(plain)


def test_delete_sequence_entry_uses_core_command_and_matches_python(monkeypatch):
    for shape, (layer_index, number, column) in (({"hold": 5, "count": 10}, (0, 2, 5)),
                                                 ({"hold": 3, "count": 9}, (0, 1, 1)),
                                                 ({"hold": 3, "count": 9}, (1, 2, 3))):
        plain, live = _pair(**shape)
        calls = _round_trips(live, monkeypatch)
        _same_position(live, plain, column, layer_index,
                       lambda document, layer_index=layer_index, number=number: (
                           document.delete_sequence_entry(layer_index, number)))
        assert len(calls) == 1  # 最後の _plain_flags だけが素の状態に落とす


def test_delete_sequence_entry_detaches_the_removed_bank_artwork(monkeypatch):
    # 取り除いたバンクの Cell・画像は、命令の前の画素のまま文書から外れる。
    plain, live = _pair(hold=5, count=10)
    live._promote()
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(0, 0, 255, 255))
    held = []
    for document in (plain, live):
        document.write_drawing_patch(5, 0, patch, (0, 0, 2, 2))
        cell = document.tracks[0].cells[2]
        held.append((cell, cell.image))
    calls = _round_trips(live, monkeypatch)
    assert live.delete_sequence_entry(0, 2) == plain.delete_sequence_entry(0, 2)
    assert calls == []
    for document, (cell, image) in zip((plain, live), held):
        assert 2 not in document.tracks[0].cells
        assert cell.image is image
        assert image.read_rgba(0, 0, 1, 1).any()
        image.write_qimage(patch, 4, 4)
        assert not document.frames[5].layers[0].image.read_rgba(4, 4, 1, 1).any()
    assert _whole_signature(live) == _whole_signature(plain)


def test_delete_sequence_entry_that_trims_frames_takes_the_current_path(monkeypatch):
    plain, live = _pair(hold=2, count=6)
    for document in (plain, live):
        document.delete_sequence_entry(1, 3)  # B の最後の番号を消す（A は残る）
    live._promote()
    calls = _round_trips(live, monkeypatch)
    assert live.delete_sequence_entry(0, 3) == plain.delete_sequence_entry(0, 3)
    assert calls
    assert _whole_signature(live) == _whole_signature(plain)


def test_move_sheet_cells_uses_core_command_and_matches_python(monkeypatch):
    # (選んだ位置, 列のずれ, コマのずれ, カーソル, 選ぶ列)
    cases = (({"hold": 5, "count": 10}, [(0, 0)], 0, 2, 2, 0),
             ({"hold": 5, "count": 10}, [(0, 5)], 0, 3, 8, 0),
             ({"hold": 5, "count": 10}, [(0, 0)], 1, 5, 5, 1),
             ({"hold": 3, "count": 9}, [(1, 3), (0, 3)], 0, 1, 4, 1),
             ({"hold": 3, "count": 9}, [(1, 2)], 0, 6, 8, 1),
             ({"hold": 3, "count": 9}, [(1, 0)], -1, 4, 4, 0))
    from paintmaskanimator import pma_image, utils
    for shape, positions, layer_delta, column_delta, cursor, active in cases:
        plain, live = _pair(**shape)
        live._promote()
        slots = [[frame.layers[index] for frame in live.frames] for index in range(2)]
        # 消した位置の空画像の世代は直前の取り込みしだい（空画像を作る Rust の命令に共通の
        # 既知の差）なので、直前の取り込みを空画像の雛形にそろえてから比べる。
        monkeypatch.setattr(pma_image, "_last_import", None)
        monkeypatch.setattr(utils._BlankTemplate, "model_image", None)
        utils.blank_model_image()
        results = []
        for document in (plain, live):
            moves = document.prepare_sheet_moves(positions, layer_delta, column_delta)
            if document is live:
                calls = _round_trips(live, monkeypatch)
            results.append(document.move_sheet_cells(moves, cursor, active))
        assert results[1] == results[0]
        assert calls == [] or positions == [(1, 2)]  # 移動先が文書の外へ伸びる形は今の経路
        detached = [slot for column in slots for slot in column
                    if not any(slot is layer for frame in live.frames for layer in frame.layers)]
        assert all(type(slot) is Layer for slot in detached)
        assert _whole_signature(live) == _whole_signature(plain)


def test_move_sheet_cells_across_tracks_detaches_replaced_bank_images(monkeypatch):
    # A の1番を B の列5へ、B の1番（初期画像あり）を C へ一緒に動かす。今の手順は B のバンクの
    # 1番の Cell に新しい画像を入れ、前の画像（命令の前に取り出した窓口）は文書から外れる。
    from paintmaskanimator import pma_image, utils

    def frames():
        rows = []
        for column in range(8):
            a = (_colored_layer("A", (255, 0, 0, 255), number=1) if 5 <= column < 7
                 else _colored_layer("A", (0, 0, 0, 0), content=False))
            if column < 2:
                b = _colored_layer("B", (0, 0, 255, 255), number=1)
                if column == 0:
                    yellow = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
                    yellow.fill(QColor(255, 255, 0, 255))
                    b.initial_image = PmaImage(yellow)
            else:
                b = _colored_layer("B", (0, 0, 0, 0), content=False)
            rows.append(Frame([a, b, _colored_layer("C", (0, 0, 0, 0), content=False)]))
        return rows
    held, results = [], []
    for factory in (GraphDocument, document_live.Document):
        document = factory()
        document.install_legacy_frames(frames())
        if factory is not GraphDocument:
            document._promote()
        monkeypatch.setattr(pma_image, "_last_import", None)
        monkeypatch.setattr(utils._BlankTemplate, "model_image", None)
        utils.blank_model_image()
        moves = document.prepare_sheet_moves([(0, 5), (1, 0)], 1, 0)
        cell = document.tracks[1].cells[1]
        held.append((cell, cell.image))
        if factory is not GraphDocument:
            calls = _round_trips(document, monkeypatch)
        results.append((document, document.move_sheet_cells(moves, 5, 1)))
    (plain, expected), (live, result) = results
    assert result == expected
    assert calls == []
    for document, (cell, image) in zip((plain, live), held):
        assert document.tracks[1].cells[1] is cell and cell.image is not image
        assert tuple(image.read_rgba(0, 0, 1, 1)[0, 0]) == (0, 0, 255, 255)
        assert document.frames[5].layers[1].initial_image is None
        assert tuple(document.frames[5].layers[1].image.read_rgba(0, 0, 1, 1)[0, 0]) == (
            255, 0, 0, 255)
    assert _whole_signature(live) == _whole_signature(plain)


def test_move_sheet_cell_uses_core_command_and_matches_python(monkeypatch):
    # (元のコマ, 元の列, 移動先のコマ, 移動先の列)。前の保持で埋める・保持の途中へ・末尾の先へ・
    # 列またぎ。命令の前に取り出した Layer は、今の手順と同じ状態で外れるか残る。
    from paintmaskanimator import pma_image, utils
    from paintmaskanimator.document_core import _slot_signature
    for shape, args in (({"hold": 5, "count": 10}, (5, 0, 7, 0)),
                        ({"hold": 5, "count": 10}, (5, 0, 2, 0)),
                        ({"hold": 5, "count": 10}, (5, 0, 12, 0)),
                        ({"hold": 3, "count": 9}, (3, 0, 4, 1)),
                        ({"hold": 3, "count": 9}, (6, 0, 1, 0)),
                        # 移動先がキーの頭（移動先の列の後ろを右へ送る）。同じ列・列またぎ。
                        ({"hold": 3, "count": 9}, (0, 0, 6, 0)),
                        ({"hold": 3, "count": 9}, (0, 0, 3, 1)),
                        ({"hold": 2, "count": 8}, (4, 1, 0, 0))):
        plain, live = _pair(**shape)
        live._promote()
        monkeypatch.setattr(pma_image, "_last_import", None)
        monkeypatch.setattr(utils._BlankTemplate, "model_image", None)
        utils.blank_model_image()
        fates, results = [], []
        for document in (plain, live):
            slots = [[frame.layers[index] for frame in document.frames] for index in range(2)]
            prepared = document.prepare_sheet_move(*args)
            if document is live:
                calls = _round_trips(live, monkeypatch)
            results.append(document.move_sheet_cell(prepared))
            fates.append([(index, column, _slot_signature(slot, slot._track)[:8])
                          for index, row in enumerate(slots) for column, slot in enumerate(row)
                          if not any(slot is layer for frame in document.frames
                                     for layer in frame.layers)])
        assert results[1] == results[0]
        assert calls == []
        assert fates[1] == fates[0]
        assert _whole_signature(live) == _whole_signature(plain)


def test_move_sheet_cell_where_the_fill_changes_the_destination_takes_the_current_path(monkeypatch):
    # 前の保持で埋めると後ろの同じ番号の範囲とつながる形、埋めが元の範囲の外のキーを上書きして
    # 今の手順が移動先を右へ送る形、雛形の前の保持が空キーの形。今の手順と同じ結果になる。
    from paintmaskanimator import pma_image, utils
    colors = {1: (255, 0, 0, 255), 2: (0, 255, 0, 255), 3: (0, 0, 255, 255)}

    def column(numbers):
        def frames():
            rows = []
            for number in numbers:
                if number == "b":
                    layer = _colored_layer("A", (0, 0, 0, 0), content=False, blank=True)
                elif number is None:
                    layer = _colored_layer("A", (0, 0, 0, 0), content=False)
                else:
                    layer = _colored_layer("A", colors[number], number=number)
                rows.append(Frame([layer]))
            return rows
        return frames
    merge = column([1, 1, 2, 2, 1, 1])
    shift = column([1, 1, 2, 2, None, 3, 3, 3, 3, None, None, None])
    blank = column(["b", None, 2, 2, None, None])
    for frames, (sf, df) in ((merge, (2, 3)), (column([1, 1, 1, 1, 2, 2, 1, 1]), (4, 2)),
                             (shift, (2, 6)), (shift, (2, 8)), (blank, (2, 3))):
        documents = []
        for factory in (GraphDocument, document_live.Document):
            document = factory()
            document.install_legacy_frames(frames())
            if factory is not GraphDocument:
                document._promote()
            monkeypatch.setattr(pma_image, "_last_import", None)
            monkeypatch.setattr(utils._BlankTemplate, "model_image", None)
            utils.blank_model_image()
            slots = [frame.layers[0] for frame in document.frames]
            prepared = document.prepare_sheet_move(sf, 0, df, 0)
            changes = document.move_sheet_cell(prepared)
            kept = [any(slot is frame.layers[0] for frame in document.frames) for slot in slots]
            documents.append((document, changes, kept))
        (plain, expected, plain_kept), (live, result, live_kept) = documents
        assert result == expected
        assert live_kept == plain_kept
        assert _whole_signature(live) == _whole_signature(plain)


def test_resize_sheet_exposure_uses_core_command_and_matches_python(monkeypatch):
    # (列, キーのコマ, 境界のコマ, 端)。後ろのキーを右へ送る・縮める・末尾の先へ・左端。
    # 命令の前に取り出した Layer は、今の手順と同じ状態で外れるか残る。
    from paintmaskanimator import pma_image, utils
    from paintmaskanimator.document_core import _slot_signature
    for shape, args in (({"hold": 5, "count": 10}, (0, 0, 7, "right")),
                        ({"hold": 5, "count": 10}, (0, 5, 6, "right")),
                        ({"hold": 5, "count": 10}, (0, 5, 13, "right")),
                        ({"hold": 3, "count": 9}, (0, 3, 4, "left")),
                        ({"hold": 3, "count": 9}, (1, 3, 5, "left"))):
        plain, live = _pair(**shape)
        live._promote()
        monkeypatch.setattr(pma_image, "_last_import", None)
        monkeypatch.setattr(utils._BlankTemplate, "model_image", None)
        utils.blank_model_image()
        layer_index, key, boundary, edge = args
        fates, results = [], []
        for document in (plain, live):
            slots = [[frame.layers[index] for frame in document.frames] for index in range(2)]
            prepared = document.prepare_exposure_resize(layer_index, key, boundary, edge)
            assert prepared is not None
            if document is live:
                calls = _round_trips(live, monkeypatch)
            results.append(document.resize_sheet_exposure(layer_index, edge, prepared))
            fates.append([(index, column, _slot_signature(slot, slot._track)[:8])
                          for index, row in enumerate(slots) for column, slot in enumerate(row)
                          if not any(slot is layer for frame in document.frames
                                     for layer in frame.layers)])
        assert results[1] == results[0]
        assert calls == []
        assert fates[1] == fates[0]
        assert _whole_signature(live) == _whole_signature(plain)


def _exposure_values(entries):
    """ExposureUndo の記録を値で比べる形にする（旧位置の画像はオブジェクトでなく画素で）。"""
    def image(value):
        return None if value is None else (value.width, value.height, value.rgba().tobytes())

    def legacy(position):
        if position is None:
            return None
        slot = position.legacy
        fields = None if slot is None else tuple(
            image(getattr(slot, name)) if name in ("image", "initial_image")
            else getattr(slot, name) for name in type(slot).__dataclass_fields__)
        return (fields, position.key_start, position.blank_start, position.pending,
                position.input_length)
    return [[(*item[:8], legacy(item[8])) for item in references] for references in entries]


def test_restore_exposure_runs_in_place_and_matches_python(monkeypatch):
    # コマの長さ変更（右端）の Undo・Redo。記録（exposure_references）と戻し（restore_exposure）
    # は往復なしで、今の手順と同じ値・同じ版番号の進み・同じ窓口の外れ方になる。
    from paintmaskanimator import pma_image, utils
    from paintmaskanimator.document_core import _slot_signature
    from paintmaskanimator.models import Layer as ModelLayer
    from paintmaskanimator.undo_entries import ExposureUndo
    for shape, args in (({"hold": 5, "count": 10}, (0, 0, 7)),
                        ({"hold": 5, "count": 10}, (0, 5, 6)),
                        ({"hold": 3, "count": 9}, (1, 3, 5))):
        plain, live = _pair(**shape)
        live._promote()
        layer_index, key, boundary = args
        entries, results, revisions, fates = [], [], [], []
        for document in (plain, live):
            monkeypatch.setattr(pma_image, "_last_import", None)
            monkeypatch.setattr(utils._BlankTemplate, "model_image", None)
            utils.blank_model_image()
            prepared = document.prepare_exposure_resize(layer_index, key, boundary, "right")
            column = prepared[0]
            if document is live:
                calls = _round_trips(live, monkeypatch)

            def capture(document=document, column=column, layer_index=layer_index):
                return ExposureUndo(layer_index, column, 1, len(document.frames), 0, layer_index,
                                    document.exposure_references(layer_index, column))
            undo = capture()
            document.resize_sheet_exposure(layer_index, "right", prepared)
            redo = capture()
            slots = [frame.layers[layer_index] for frame in document.frames]
            cells = [slot.__dict__.get("_cell") for slot in slots]
            before = ModelLayer.timeline_revision
            results.append([document.restore_exposure(undo)])
            middle = capture()
            results[-1].append(document.restore_exposure(redo))
            revisions.append(ModelLayer.timeline_revision - before)
            entries.append((undo.references, redo.references, middle.references))
            fates.append([(column, _slot_signature(slot, slot._track)[:8])
                          for column, slot in enumerate(slots)
                          if not any(slot is layer for frame in document.frames
                                     for layer in frame.layers)])
            fates[-1].append([index for index, (slot, cell) in enumerate(zip(slots, cells))
                              if slot.__dict__.get("_cell") is not cell])
        assert calls == []
        assert results[1] == results[0]
        assert _exposure_values(entries[1]) == _exposure_values(entries[0])
        assert revisions[1] == revisions[0]
        assert fates[1] == fates[0]
        assert _whole_signature(live) == _whole_signature(plain)


def test_restore_exposure_that_rust_cannot_apply_takes_the_current_path(monkeypatch):
    # 戻した値が Rust で表せない（絵と空キーが同時の）記録は今の経路で、結果は同じ。
    from paintmaskanimator.undo_entries import ExposureUndo
    plain, live = _pair(hold=5, count=10)
    live._promote()
    results = []
    for document in (plain, live):
        references = list(document.exposure_references(0, 5))
        references[0] = (True, True, *references[0][2:])
        undo = ExposureUndo(0, 5, 1, len(document.frames), 0, 0, tuple(references))
        if document is live:
            calls = _round_trips(live, monkeypatch)
        try:
            results.append(document.restore_exposure(undo))
        except Exception as exc:  # 今の手順が失敗するなら、同じ失敗になること
            results.append(type(exc))
    assert calls
    assert results[1] == results[0]


def test_resize_sheet_exposure_detaches_unshifted_tail_windows_like_python(monkeypatch):
    # 後ろを送る形で、今の手順の送りは最初に伸ばした長さまでしか送らない。送られない末尾の
    # 位置の窓口（Layer・局所の Cell・画像）は文書から外れ、外れた Layer に描いても文書は
    # 変わらない。送りの途中で足したコマの旧形式の記録は、移ってきた位置を消した写しになる。
    from paintmaskanimator import pma_image, utils

    def frames():
        rows = []
        for column in range(8):
            if column < 2:
                layer = _colored_layer("A", (255, 0, 0, 255), number=1)
            elif 3 <= column < 5:
                layer = _colored_layer("A", (0, 255, 0, 255), number=2)
                if column == 3:
                    yellow = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
                    yellow.fill(QColor(255, 255, 0, 255))
                    layer.initial_image = PmaImage(yellow)
            else:
                layer = _colored_layer("A", (0, 0, 0, 0), content=False)
            rows.append(Frame([layer]))
        return rows
    for boundary, count in ((3, 8), (6, 8), (3, 5)):
        documents = []
        for factory in (GraphDocument, document_live.Document):
            document = factory()
            document.install_legacy_frames(frames()[:count])
            if factory is not GraphDocument:
                document._promote()
            monkeypatch.setattr(pma_image, "_last_import", None)
            monkeypatch.setattr(utils._BlankTemplate, "model_image", None)
            utils.blank_model_image()
            slots = [frame.layers[0] for frame in document.frames]
            images = [slot.image for slot in slots]
            prepared = document.prepare_exposure_resize(0, 0, boundary, "right")
            if factory is not GraphDocument:
                calls = _round_trips(document, monkeypatch)
            changes = document.resize_sheet_exposure(0, "right", prepared)
            kept = [any(slot is frame.layers[0] for frame in document.frames) for slot in slots]
            placed = [any(image is frame.layers[0].image for frame in document.frames)
                      for image in images]
            patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
            patch.fill(QColor(0, 0, 255, 255))
            for slot, stays in zip(slots, kept):
                if not stays:
                    slot.image.write_qimage(patch, 0, 0)
            documents.append((document, changes, kept, placed))
        (plain, *expected), (live, *result) = documents
        assert result == expected
        assert calls == []
        assert _whole_signature(live) == _whole_signature(plain)


def test_move_sheet_cell_onto_a_key_detaches_overwritten_locals_like_python(monkeypatch):
    # 幅3の絵を、長さ1のキーと空きが続く位置へ動かす（移動先の列の後ろを右へ送る）。送りで
    # 一つ後ろへ移る局所の Cell の移り先が移動した絵で上書きされる位置では、その Cell は文書
    # から外れ、取り出していた Layer・画像は読めて、描いても文書は変わらない。
    from paintmaskanimator import pma_image, utils

    def frames():
        rows = []
        for column in range(8):
            a = (_colored_layer("A", (255, 0, 0, 255), number=1) if column < 3
                 else _colored_layer("A", (0, 0, 0, 0), content=False))
            b = (_colored_layer("B", (0, 255, 0, 255), number=1) if column == 3
                 else _colored_layer("B", (0, 0, 0, 0), content=False))
            rows.append(Frame([a, b]))
        return rows
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(0, 0, 255, 255))
    for args in ((0, 0, 3, 1), (0, 1, 3, 1)):
        documents = []
        for factory in (GraphDocument, document_live.Document):
            document = factory()
            document.install_legacy_frames(frames())
            if factory is not GraphDocument:
                document._promote()
            monkeypatch.setattr(pma_image, "_last_import", None)
            monkeypatch.setattr(utils._BlankTemplate, "model_image", None)
            utils.blank_model_image()
            slots = [frame.layers[1] for frame in document.frames]
            images = [slot.image for slot in slots]
            prepared = document.prepare_sheet_move(*args)
            changes = document.move_sheet_cell(prepared)
            kept = [any(slot is frame.layers[1] for frame in document.frames) for slot in slots]
            pixels = [tuple(image.read_rgba(0, 0, 1, 1)[0, 0]) for image in images]
            for slot, image, stays in zip(slots, images, kept):
                if not stays:
                    slot.image.write_qimage(patch, 0, 0)
                    image.write_qimage(patch, 4, 4)
            documents.append((document, changes, kept, pixels))
        (plain, *expected), (live, *result) = documents
        assert result == expected
        assert _whole_signature(live) == _whole_signature(plain)


def test_set_sheet_duration_uses_core_command_and_matches_python(monkeypatch):
    # (コマ, 列, 長さ)。後ろのキーを上書きして伸ばす・縮める・末尾の先へ。命令の前に取り出した
    # Layer は、今の手順と同じ状態で外れるか残る（伸ばした位置は直接代入で入れ直される）。
    from paintmaskanimator import pma_image, utils
    from paintmaskanimator.document_core import _slot_signature
    for shape, args in (({"hold": 5, "count": 10}, (0, 0, 7)),
                        ({"hold": 5, "count": 10}, (6, 0, 2)),
                        ({"hold": 5, "count": 10}, (5, 0, 9)),
                        ({"hold": 3, "count": 9}, (1, 0, 1)),
                        ({"hold": 3, "count": 9}, (4, 0, 2))):
        plain, live = _pair(**shape)
        live._promote()
        monkeypatch.setattr(pma_image, "_last_import", None)
        monkeypatch.setattr(utils._BlankTemplate, "model_image", None)
        utils.blank_model_image()
        fates, results = [], []
        for document in (plain, live):
            slots = [[frame.layers[index] for frame in document.frames] for index in range(2)]
            if document is live:
                calls = _round_trips(live, monkeypatch)
            results.append(document.set_sheet_duration(*args))
            fates.append([(index, column, _slot_signature(slot, slot._track)[:8])
                          for index, row in enumerate(slots) for column, slot in enumerate(row)
                          if not any(slot is layer for frame in document.frames
                                     for layer in frame.layers)])
        assert results[1] == results[0]
        assert calls == []
        assert fates[1] == fates[0]
        assert _whole_signature(live) == _whole_signature(plain)


def test_core_set_sheet_duration_drops_shares_left_with_one_position():
    # 空キーを伸ばして局所の絵の共有の組（2・3）を作り、前の保持を伸ばして 2 を上書きすると、
    # 今の手順では 3 の Cell を持つ位置は一つだけになる（共有でなくなる）。
    import json

    def frames():
        rows = []
        for column in range(6):
            if column < 2:
                layer = _colored_layer("A", (255, 0, 0, 255), number=1)
            elif column == 2:
                layer = _colored_layer("A", (0, 0, 0, 0), content=False, blank=True)
            else:
                layer = _colored_layer("A", (0, 0, 0, 0), content=False)
            rows.append(Frame([layer]))
        return rows
    live = document_live.Document()
    live.install_legacy_frames(frames())
    live._promote()
    core = live._live()["core"]
    core.apply_command(json.dumps(["set_sheet_duration", 2, 0, 2]))

    def shares():
        order = json.loads(core.frames_graph([])[0])["frame_order"]
        graph = json.loads(core.frames_graph(order)[0])
        return [frame["positions"][0]["local_share"] for frame in graph["frames"]]
    assert shares()[2] is not None and shares()[2] == shares()[3]
    core.apply_command(json.dumps(["set_sheet_duration", 0, 0, 3]))
    assert shares()[3] is None


def test_merge_sheet_clip_uses_core_command_and_matches_python(monkeypatch):
    # 保持を前の保持へつなぐ。命令の前に取り出した Layer は今の手順と同じ状態で外れる。
    from paintmaskanimator.document_core import _slot_signature
    for shape, args in (({"hold": 5, "count": 10}, (5, 0)),
                        ({"hold": 5, "count": 10}, (7, 1)),
                        ({"hold": 3, "count": 9}, (3, 0)),
                        ({"hold": 3, "count": 9}, (0, 0))):
        plain, live = _pair(**shape)
        live._promote()
        fates, results = [], []
        for document in (plain, live):
            slots = [[frame.layers[index] for frame in document.frames] for index in range(2)]
            if document is live:
                calls = _round_trips(live, monkeypatch)
            results.append(document.merge_sheet_clip(*args))
            fates.append([(index, column, _slot_signature(slot, slot._track)[:8])
                          for index, row in enumerate(slots) for column, slot in enumerate(row)
                          if not any(slot is layer for frame in document.frames
                                     for layer in frame.layers)])
        assert results[1] == results[0]
        assert calls == []
        assert fates[1] == fates[0]
        assert _whole_signature(live) == _whole_signature(plain)


def test_merge_sheet_clip_onto_an_unnumbered_head_matches_python(monkeypatch):
    # 前の保持の頭が番号の無い内容（読み込みでバンクに入った絵）でも、今の手順と同じになる。
    def frames():
        return ([Frame([_colored_layer("A", (0, 255, 0, 255))]) for _ in range(2)]
                + [Frame([_colored_layer("A", (255, 0, 0, 255), number=1)]) for _ in range(3)])
    plain = GraphDocument()
    plain.install_legacy_frames(frames())
    live = document_live.Document()
    live.install_legacy_frames(frames())
    live._promote()
    calls = _round_trips(live, monkeypatch)
    assert live.merge_sheet_clip(3, 0) == plain.merge_sheet_clip(3, 0)
    assert calls == []
    assert _whole_signature(live) == _whole_signature(plain)


def test_merge_sheet_clip_with_local_artwork_head_takes_the_current_path(monkeypatch):
    # 前の保持の頭が番号を残した空キー（局所の絵）なら Rust の命令は使わず、今の手順と同じになる。
    def frames():
        return ([Frame([_colored_layer("A", (0, 0, 255, 255), content=False, number=1,
                                       blank=True)]) for _ in range(2)]
                + [Frame([_colored_layer("A", (255, 0, 0, 255), number=2)]) for _ in range(3)])
    plain = GraphDocument()
    plain.install_legacy_frames(frames())
    live = document_live.Document()
    live.install_legacy_frames(frames())
    live._promote()
    calls = _round_trips(live, monkeypatch)
    assert live.merge_sheet_clip(3, 0) == plain.merge_sheet_clip(3, 0)
    assert calls
    assert _whole_signature(live) == _whole_signature(plain)

def test_merge_sheet_clip_after_a_gap_and_undo_match_python(monkeypatch):
    # 前が空きの位置（B 列の 2 コマ目）、つないだあとの Undo も今の手順と同じになる。
    for shape, args in (({"hold": 5, "count": 10}, (3, 1)),
                        ({"hold": 3, "count": 9}, (6, 0))):
        plain, live = _pair(**shape)
        live._promote()
        before = [document.snapshot() for document in (plain, live)]
        assert live.merge_sheet_clip(*args) == plain.merge_sheet_clip(*args)
        assert _signature(live) == _signature(plain)
        plain.restore_snapshot(before[0])
        live.restore_snapshot(before[1])
        assert _signature(live) == _signature(plain)
        assert _whole_signature(live) == _whole_signature(plain)


def test_copy_sheet_cell_uses_core_command_and_matches_python(monkeypatch):
    # (元のコマ, 移動先のコマ, 列)。保持の途中・キーの頭・空き・末尾の先へ写す。命令の前に
    # 取り出した Layer は今の手順と同じ状態で外れるか残る。
    from paintmaskanimator.document_core import _slot_signature
    for shape, args in (({"hold": 5, "count": 10}, (1, 7, 0)),
                        ({"hold": 5, "count": 10}, (6, 0, 0)),
                        ({"hold": 5, "count": 10}, (0, 8, 1)),
                        ({"hold": 3, "count": 9}, (0, 5, 0)),
                        ({"hold": 3, "count": 9}, (4, 0, 1))):
        plain, live = _pair(**shape)
        live._promote()
        fates, results = [], []
        for document in (plain, live):
            slots = [[frame.layers[index] for frame in document.frames] for index in range(2)]
            if document is live:
                calls = _round_trips(live, monkeypatch)
            results.append(document.copy_sheet_cell(*args))
            fates.append([(index, column, _slot_signature(slot, slot._track)[:8])
                          for index, row in enumerate(slots) for column, slot in enumerate(row)
                          if not any(slot is layer for frame in document.frames
                                     for layer in frame.layers)])
        assert results[1] == results[0]
        assert calls == []
        assert fates[1] == fates[0]
        assert _whole_signature(live) == _whole_signature(plain)


def test_copy_sheet_cell_that_overlaps_takes_the_current_path(monkeypatch):
    # 元の範囲と重なる写しは今の経路で、今の手順と同じになる。
    for frames, args in ((lambda: _frames(hold=5, count=10), (0, 2, 0)),
                         (lambda: _frames(hold=3, count=9), (3, 1, 0))):
        plain = GraphDocument()
        plain.install_legacy_frames(frames())
        live = document_live.Document()
        live.install_legacy_frames(frames())
        live._promote()
        calls = _round_trips(live, monkeypatch)
        assert live.copy_sheet_cell(*args) == plain.copy_sheet_cell(*args)
        assert calls
        assert _whole_signature(live) == _whole_signature(plain)


def test_copy_sheet_cell_more_shapes_and_undo_match_python(monkeypatch):
    # 元の範囲の直後・別の保持の途中・末尾から離れた先へ写し、続けて写した保持の上へもう一度
    # 写す（2回目は配置待ちの位置なので今の経路）。Undo も今の手順と同じになる。
    for shape, steps in (({"hold": 5, "count": 10}, [(0, 5, 0), (0, 6, 0)]),
                         ({"hold": 3, "count": 9}, [(0, 4, 0)]),
                         ({"hold": 3, "count": 9}, [(3, 12, 0)])):
        plain, live = _pair(**shape)
        live._promote()
        before = [document.snapshot() for document in (plain, live)]
        for args in steps:
            assert live.copy_sheet_cell(*args) == plain.copy_sheet_cell(*args)
            assert _signature(live) == _signature(plain)
        assert _whole_signature(live) == _whole_signature(plain)
        plain.restore_snapshot(before[0])
        live.restore_snapshot(before[1])
        assert _signature(live) == _signature(plain)
        assert _whole_signature(live) == _whole_signature(plain)


def _clipboard(document, column, layer_index, rgba):
    """画面の操作と同じくセルを複製して控え、その絵を塗り替える（控えたあとに文書の絵を
    描き替えた形も、文書の外の絵として同じに扱う）。"""
    clip = document.frames[column].layers[layer_index].clone()
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(*rgba))
    clip._track.cells[clip.sequence_number].image = PmaImage(image)
    return clip


def test_paste_sheet_cell_uses_core_command_and_matches_python(monkeypatch):
    # (貼り先のコマ, 列, 長さ, 控えるコマ, 控える列, 現在のコマ)。保持の途中・キーの頭・空き・
    # 末尾の先へ、後ろのキーで止まる長さ、別の列の番号、現在のコマが同じ番号の内容を持つ
    # （画素はその位置の絵）形。命令の前に取り出した Layer・バンクの画像は今の手順と同じ
    # 状態で外れるか残る。
    from paintmaskanimator.document_core import _slot_signature
    for shape, args, clip_at, current in (
            ({"hold": 5, "count": 10}, (2, 0, 3), (0, 0), 0),
            ({"hold": 5, "count": 10}, (2, 0, 6), (6, 0), 9),
            ({"hold": 5, "count": 10}, (2, 1, 2), (6, 0), 7),
            ({"hold": 5, "count": 10}, (8, 1, 5), (0, 1), 0),
            ({"hold": 3, "count": 9}, (5, 0, 1), (0, 0), 1),
            ({"hold": 3, "count": 9}, (2, 1, 4), (7, 0), 4),
            ({"hold": 5, "count": 10}, (8, 0, 4), (6, 0), 0)):
        plain, live = _pair(**shape)
        live._promote()
        fates, results, banks = [], [], []
        for document in (plain, live):
            clip = _clipboard(document, *clip_at, (0, 200, 0, 255))
            document.current_frame = current
            slots = [[frame.layers[index] for frame in document.frames] for index in range(2)]
            number = clip.sequence_number
            old = document.tracks[args[1]].cells.get(number)
            old_image = None if old is None else old.image
            if document is live:
                calls = _round_trips(live, monkeypatch)
            results.append(document.paste_sheet_cell(*args[:2], clip, args[2]))
            fates.append([(index, column, _slot_signature(slot, slot._track)[:8])
                          for index, row in enumerate(slots) for column, slot in enumerate(row)
                          if not any(slot is layer for frame in document.frames
                                     for layer in frame.layers)])
            cell = document.tracks[args[1]].cells[number]
            banks.append((old is None or cell is old,
                          old_image is None or cell.image is not old_image,
                          None if old_image is None else old_image.rgba().tobytes(),
                          cell.image.rgba().tobytes()))
        assert results[1] == results[0]
        assert calls == []
        assert fates[1] == fates[0]
        assert banks[1] == banks[0]
        assert _signature(live) == _signature(plain)
        assert _whole_signature(live) == _whole_signature(plain)


def test_paste_sheet_cell_unusual_clipboards_take_the_current_path(monkeypatch):
    # 番号の無い絵・空キー・配置待ちの位置への貼り付けは今の経路で、今の手順と同じになる。
    def unnumbered(document):
        clip = _clipboard(document, 0, 0, (0, 0, 200, 255))
        clip.sequence_number = None
        return clip

    def blank(document):
        clip = _clipboard(document, 0, 0, (0, 0, 200, 255))
        clip.is_blank_key = True
        return clip
    for make, args in ((unnumbered, (4, 1, 2)), (blank, (4, 1, 2))):
        plain, live = _pair(hold=3, count=9)
        live._promote()
        calls = _round_trips(live, monkeypatch)
        assert (live.paste_sheet_cell(*args[:2], make(live), args[2])
                == plain.paste_sheet_cell(*args[:2], make(plain), args[2]))
        assert calls
        assert _whole_signature(live) == _whole_signature(plain)


def test_paste_sheet_cell_with_active_layer_out_of_range_takes_the_current_path(monkeypatch):
    # 選択中の列が範囲外だと Rust の選択を合わせられないので今の経路で、今の手順と同じになる。
    plain, live = _pair(hold=3, count=9)
    live._promote()
    for document in (plain, live):
        document.active_layer_index = 5
    calls = _round_trips(live, monkeypatch)
    assert (live.paste_sheet_cell(4, 1, _clipboard(live, 0, 0, (0, 0, 200, 255)), 2)
            == plain.paste_sheet_cell(4, 1, _clipboard(plain, 0, 0, (0, 0, 200, 255)), 2))
    assert calls
    assert _whole_signature(live) == _whole_signature(plain)


def test_paste_sheet_cell_twice_and_undo_match_python(monkeypatch):
    # 貼った保持の上へもう一度貼る（後ろは配置待ちが解かれた入れ直しの位置）。Undo も今の
    # 手順と同じになる。
    plain, live = _pair(hold=5, count=10)
    live._promote()
    before = [document.snapshot() for document in (plain, live)]
    for args, clip_at, rgba in (((3, 0, 4), (0, 0), (200, 0, 0, 255)),
                                ((4, 0, 2), (6, 0), (0, 0, 200, 255))):
        assert (live.paste_sheet_cell(*args[:2], _clipboard(live, *clip_at, rgba), args[2])
                == plain.paste_sheet_cell(*args[:2], _clipboard(plain, *clip_at, rgba), args[2]))
        assert _signature(live) == _signature(plain)
        assert _whole_signature(live) == _whole_signature(plain)
    plain.restore_snapshot(before[0])
    live.restore_snapshot(before[1])
    assert _signature(live) == _signature(plain)
    assert _whole_signature(live) == _whole_signature(plain)


def _paint_numbers(document, layer_index, hold):
    # 番号ごとに違う色を左上に塗る（動いた絵を画素で見分ける）。
    for number in sorted(document.tracks[layer_index].cells):
        patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
        patch.fill(QColor(number * 20, 255 - number * 20, 7, 255))
        document.write_drawing_patch((number - 1) * hold, layer_index, patch, (0, 0, 2, 2))


def _move_sequence_image(document, layer_index, first, second):
    sources = document.sequence_move_sources(layer_index, first, second, sheet_mode=True)
    return document.move_sequence_image(layer_index, first, second, sources)


def test_move_sequence_image_uses_core_command_and_matches_python(monkeypatch):
    for shape, moves in (({"hold": 2, "count": 8}, ((0, 1, 3), (0, 3, 1))),
                         ({"hold": 3, "count": 9}, ((0, 3, 1), (0, 1, 2))),
                         ({"hold": 2, "count": 8}, ((0, 4, 2), (0, 2, 4), (0, 1, 4)))):
        plain, live = _pair(**shape)
        for document in (plain, live):
            _paint_numbers(document, 0, shape["hold"])
        live._promote()
        slots = [frame.layers[0] for frame in live.frames]
        for layer_index, first, second in moves:
            bank = {n: (cell, cell.image) for n, cell in live.tracks[layer_index].cells.items()}
            plain_bank = {n: (cell, cell.image)
                          for n, cell in plain.tracks[layer_index].cells.items()}
            calls = _round_trips(live, monkeypatch)
            assert (_move_sequence_image(live, layer_index, first, second)
                    == _move_sequence_image(plain, layer_index, first, second))
            assert calls == []
            monkeypatch.undo()
            assert _signature(live) == _signature(plain)
            assert all(frame.layers[0] is slot for frame, slot in zip(live.frames, slots))
            # バンクの Cell・画像は同じオブジェクトのまま、動いた先の番号へ移る。
            moved = {n: [m for m, cell in live.tracks[layer_index].cells.items()
                         if cell is pair[0] and cell.image is pair[1]]
                     for n, pair in bank.items()}
            plain_moved = {n: [m for m, cell in plain.tracks[layer_index].cells.items()
                               if cell is pair[0] and cell.image is pair[1]]
                           for n, pair in plain_bank.items()}
            assert moved == plain_moved
            assert ([frame.layers[0].image.read_rgba(0, 0, 1, 1).tolist() for frame in live.frames]
                    == [frame.layers[0].image.read_rgba(0, 0, 1, 1).tolist()
                        for frame in plain.frames])
        # 動いた絵の画像へ書くと、動いた先の番号の位置に映る。
        patch = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
        patch.fill(QColor(1, 2, 3, 255))
        for document in (plain, live):
            document.tracks[0].cells[1].image.write_qimage(patch, 5, 5)
        assert ([frame.layers[0].image.read_rgba(5, 5, 1, 1).tolist() for frame in live.frames]
                == [frame.layers[0].image.read_rgba(5, 5, 1, 1).tolist()
                    for frame in plain.frames])
        assert _whole_signature(live) == _whole_signature(plain)


def test_move_sequence_image_with_a_blank_key_takes_the_current_path(monkeypatch):
    plain, live = _pair(hold=2, count=8)
    for document in (plain, live):
        _paint_numbers(document, 0, 2)
    # 空キーの空画像の世代は直前の取り込みしだい（空画像を作る Rust の命令に共通の既知の差）
    # なので、直前の取り込みを空画像の雛形にそろえてから比べる。
    from paintmaskanimator import pma_image, utils
    monkeypatch.setattr(pma_image, "_last_import", None)
    monkeypatch.setattr(utils._BlankTemplate, "model_image", None)
    utils.blank_model_image()
    for document in (plain, live):
        document.create_blank_key(2, 0)  # 番号2 の保持の頭を空キーにする
    live._promote()
    calls = _round_trips(live, monkeypatch)
    assert _move_sequence_image(live, 0, 1, 3) == _move_sequence_image(plain, 0, 1, 3)
    assert calls
    monkeypatch.undo()
    assert _signature(live) == _signature(plain)
    assert _whole_signature(live) == _whole_signature(plain)


def test_has_unlinked_tracks_answers_without_walking_positions(monkeypatch):
    plain, live = _pair()
    calls = _round_trips(live, monkeypatch)
    cached = len(live._live()["cache"].items())
    assert live.has_unlinked_tracks() is plain.has_unlinked_tracks() is False
    # Rust の状態では位置の窓口を作らず、素の状態にも落とさない。
    assert len(live._live()["cache"].items()) == cached
    assert calls == []
    # 素の状態では今の手順と同じく位置を見る。
    live._demote()
    assert live.has_unlinked_tracks() is False
    unlinked = GraphDocument()
    unlinked.install_legacy_frames(_frames())
    unlinked.frames[0].layers[0]._track.connected = False
    assert unlinked.has_unlinked_tracks() is True


def test_restore_snapshot_retires_proxies_without_writing_them_out(monkeypatch):
    plain, live = _pair(hold=5, count=10)
    live._promote()
    expected, snapshot = plain.snapshot(), live.snapshot()
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(0, 0, 255, 255))
    for document in (plain, live):
        document.duplicate_frame(2)
        document.write_drawing_patch(0, 0, patch, (0, 0, 2, 2))
    olds = []
    for document in (live, plain):
        frame = document.frames[1]
        slot = frame.layers[0]
        olds.append((frame, frame.layers, slot, slot._track, slot.image, document.frames))
    before = [old[4].read_rgba(0, 0, 4, 4).copy() for old in olds]
    calls = _round_trips(live, monkeypatch)
    assert live.restore_snapshot(snapshot) == plain.restore_snapshot(expected)
    assert calls == []  # 全体を素のグラフへ書き出さない
    assert _signature(live) == _signature(plain)
    for (frame, layers, slot, track, image, frames), pixels in zip(olds, before):
        # 外れた窓口は同じオブジェクトのまま、戻す前の値を持つ。
        assert frame.layers is layers and layers[0] is slot and slot._track is track
        assert slot.image is image
        assert (image.read_rgba(0, 0, 4, 4) == pixels).all()
        assert len(frames) == 11
    live_old, plain_old = olds
    assert ([layer.sequence_number for layer in live_old[1]]
            == [layer.sequence_number for layer in plain_old[1]])
    # 外れた窓口に書いても文書には届かない。
    for old in (live_old, plain_old):
        old[2].opacity = 0.5
        old[4].write_rgba(np.full((2, 2, 4), 255, dtype=np.uint8), 0, 0)
    assert _signature(live) == _signature(plain)
    assert (live.frames[1].layers[0].image.read_rgba(0, 0, 4, 4)
            == plain.frames[1].layers[0].image.read_rgba(0, 0, 4, 4)).all()
    assert live_old[2].opacity == plain_old[2].opacity == 0.5
    assert (live_old[4].read_rgba(0, 0, 2, 2) == 255).all()
    assert (plain_old[4].read_rgba(0, 0, 2, 2) == 255).all()
    # 戻したあとの並びは別物で、古い並びは古いコマを持つ。
    for old, document in ((live_old, live), (plain_old, plain)):
        assert document.frames is not old[5] and old[5][1] is old[0]
    # 外れた列を文書の操作に渡すと、今と同じく文書にない列として扱う。
    assert live.placed_numbers(live_old[3]) == plain.placed_numbers(plain_old[3])
    assert live.placed_numbers(live.tracks[0]) == plain.placed_numbers(plain.tracks[0])
    # 外れた列を素のグラフで当てる操作に渡しても、文書には届かない。
    for old, document in ((live_old, live), (plain_old, plain)):
        document.put_archive_hint(old[3], 1, ArchiveHint())
    assert _signature(live) == _signature(plain)
    assert live.archive_hints(live.tracks[0]) == plain.archive_hints(plain.tracks[0])


def test_restore_snapshot_keeps_reassigned_and_held_views():
    plain, live = _pair(hold=5, count=10)
    live._promote()
    expected, snapshot = plain.snapshot(), live.snapshot()
    for document in (plain, live):
        document.duplicate_frame(2)
    held = []
    for document in (live, plain):
        layers = document.frames[1].layers
        layers[0]  # 位置の窓口を作っておく
        held.append((document.frames, document.tracks, layers, layers[0]))
    # 全体の往復を一度挟む（コマの辞書から並びの窓口が外れる）。
    for document in (live, plain):
        document.set_drawing_content(0, 1, False)
    assert live.restore_snapshot(snapshot) == plain.restore_snapshot(expected)
    for (frames, tracks, layers, slot), document in zip(held, (live, plain)):
        # 持ち続けた位置の並びは古い位置を返す。
        assert layers[0] is slot and len(layers) == 2
        assert all(layer is not slot for frame in document.frames for layer in frame.layers)
        # 古い並びを入れ直すと、今と同じくその並びが文書の並びになる。
        document.frames = frames
        document.tracks = tracks
        assert document.frames is frames and document.tracks is tracks
    assert _signature(live) == _signature(plain)


def test_add_track_uses_core_command_and_matches_python(monkeypatch):
    plain, live = _pair(hold=3, count=7)
    calls = _round_trips(live, monkeypatch)
    old_tracks, plain_old_tracks = live.tracks, plain.tracks
    old_frames = list(live.frames)
    old_layers = [list(frame.layers) for frame in live.frames]
    assert live.add_track() == plain.add_track()
    assert calls == []
    assert _signature(live) == _signature(plain)
    assert [layer.name for layer in live.layers] == [layer.name for layer in plain.layers]
    # 今の手順は tracks に新しいリストを入れ、既存のコマ・位置のオブジェクトはそのまま。
    assert (live.tracks is old_tracks) == (plain.tracks is plain_old_tracks)
    assert len(old_tracks) == len(plain_old_tracks) == 2
    assert all(a is b for a, b in zip(live.frames, old_frames))
    assert all(frame.layers[:2] == row and all(x is y for x, y in zip(frame.layers, row))
               for frame, row in zip(live.frames, old_layers))
    # 新しい列は一つの Track を共有し、各位置は独立した空画像の局所の Cell を持つ。
    column = [frame.layers[2] for frame in live.frames]
    assert all(slot._track is live.tracks[2] for slot in column)
    assert len({id(slot._cell) for slot in column}) == len(column)
    assert not any(slot.image.rgba().any() for slot in column)
    # 続けてもう一列。名前は今の手順と同じ。
    assert live.add_track() == plain.add_track()
    assert calls == []
    assert [layer.name for layer in live.layers] == [layer.name for layer in plain.layers]
    assert _signature(live) == _signature(plain)
    # 一つの位置の絵を替えても、同じ列の他の位置の絵は変わらない。
    width = column[1].image.width
    assert width != 3
    column[0].image = PmaImage.blank(3, 3)
    assert column[0].image.width == 3 and live.frames[1].layers[2].image.width == width


def test_add_track_with_pending_detached_positions_takes_the_current_path(monkeypatch):
    plain, live = _pair(hold=5, count=10)
    for document in (plain, live):
        document.clear_sheet_span(1, 2, 10)
        document.create_blank_key(9, 1)
    live._promote()
    assert live._live()["graph"] is None
    assert any(values.get("pending_detached")
               for values, _ in live._live()["state"].positions.values())
    calls = _round_trips(live, monkeypatch)
    assert live.add_track() == plain.add_track()
    assert calls
    assert _signature(live) == _signature(plain)
    assert _plain_flags(live) == _plain_flags(plain)


def _column_signature(layers, monkeypatch):
    """列の写しの署名。画像の世代は文書ごとに違うので、出てきた順の番号に読み替える。"""
    from paintmaskanimator import document_core
    original = document_core._image_signature
    seen = {}

    def image_signature(image):
        value = original(image)
        if isinstance(value, tuple) and len(value) == 4:
            value = (value[0], value[1], seen.setdefault(value[2], len(seen)), value[3])
        return value
    plain = object.__new__(GraphDocument)
    plain.frames = [Frame([layer]) for layer in layers]
    plain.tracks = [layers[0]._track]
    plain.current_frame = plain.active_layer_index = 0
    with monkeypatch.context() as patch:
        patch.setattr(document_core, "_image_signature", image_signature)
        return document_core.graph_signature(plain)


def test_capture_column_reads_one_column_and_matches_python(monkeypatch):
    plain, live = _pair(hold=5, count=10)
    for document in (plain, live):
        document.clear_sheet_span(1, 2, 6)
        document.create_blank_key(8, 1)
    live._promote()
    calls = _round_trips(live, monkeypatch)
    for index in (0, 1):
        captured = live.capture_column(index)
        expected = plain.capture_column(index)
        assert calls == []
        assert (_column_signature(captured, monkeypatch)
                == _column_signature(expected, monkeypatch))
        # 写しは文書から外れている（同じオブジェクトでなく、列も別）。
        assert not any(slot is layer for slot in captured
                       for frame in live.frames for layer in frame.layers)
        assert captured[0]._track is not live.tracks[index]
        assert ([len(slot._track.pending_slots) for slot in captured[:1]]
                == [len(slot._track.pending_slots) for slot in expected[:1]])
    before = _signature(live)
    captured = live.capture_column(0)
    captured[0].has_content = False
    captured[0]._track.name = "Z"
    assert _signature(live) == before and live.layers[0].name == "A"
    assert _signature(live) == _signature(plain)


def test_remove_tracks_uses_core_command_and_retires_the_column(monkeypatch):
    plain, live = _pair(hold=3, count=7)
    for document in (plain, live):
        document.add_track()
    live._promote()
    calls = _round_trips(live, monkeypatch)
    old_tracks, plain_old_tracks = live.tracks, plain.tracks
    removed_track = live.tracks[1]
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(0, 255, 0, 255))
    for document in (plain, live):
        document.write_drawing_patch(0, 1, patch, (0, 0, 2, 2))
    calls.clear()
    removed_slot = live.frames[0].layers[1]
    removed_cell = removed_slot._cell
    removed_image = removed_slot.image
    pixels = removed_image.read_rgba(0, 0, 4, 4).copy()
    assert pixels.any()
    values = (removed_slot.has_content, removed_slot.sequence_number, removed_track.name,
              sorted(removed_track.cells))
    kept = live.frames[0].layers[2]
    assert live.remove_tracks([1]) == plain.remove_tracks([1])
    assert calls == []
    assert _signature(live) == _signature(plain)
    assert [layer.name for layer in live.layers] == [layer.name for layer in plain.layers]
    assert (live.tracks is old_tracks) == (plain.tracks is plain_old_tracks)
    assert live.frames[0].layers[1] is kept
    # 外れた位置・列・絵は今の値のまま、文書の外に残る。
    assert not any(slot is removed_slot for frame in live.frames for slot in frame.layers)
    assert (removed_slot.has_content, removed_slot.sequence_number, removed_track.name,
            sorted(removed_track.cells)) == values
    assert removed_slot._track is removed_track and removed_slot._cell is removed_cell
    assert removed_slot.image is removed_image
    # restore と同じく、外れた画像は今の画素を持つ独立した画像に戻る。
    assert type(removed_image) is PmaImage
    assert (removed_image.read_rgba(0, 0, 4, 4) == pixels).all()
    # 外れたものへの書き込みは文書に届かない。
    removed_track.name = "Z"
    removed_slot.has_content = False
    assert [layer.name for layer in live.layers] == [layer.name for layer in plain.layers]
    assert _signature(live) == _signature(plain)
    # 外れた位置を文書に戻すと、今と同じく素のオブジェクトとして扱われる。
    assert removed_track.name == "Z" and not removed_slot.has_content


def test_restore_removed_track_undoes_add_track(monkeypatch):
    from paintmaskanimator.undo_entries import LayerRemoveUndo
    plain, live = _pair(hold=3, count=7)
    for document in (plain, live):
        document.add_track()
    live._promote()
    calls = _round_trips(live, monkeypatch)
    entry = LayerRemoveUndo(2, 1)
    captured = live.capture_column(2)
    assert live.restore_removed_track(entry) == plain.restore_removed_track(entry)
    assert calls == []
    assert _signature(live) == _signature(plain)
    assert len(live.layers) == 2 and len(captured) == len(live.frames)


def test_remove_tracks_with_pending_positions_takes_the_current_path(monkeypatch):
    plain, live = _pair(hold=5, count=10)
    for document in (plain, live):
        document.add_track()
        document.insert_sequence_blank(1, 1)
    live._promote()
    assert live._live()["graph"] is None
    state = live._live()["state"]
    assert any(values["sequence_only"] for (_, tid), (values, _) in state.positions.items()
               if tid == state.track_ids[1])
    calls = _round_trips(live, monkeypatch)
    assert live.remove_tracks([1]) == plain.remove_tracks([1])
    assert calls
    assert _signature(live) == _signature(plain)


def test_remove_several_tracks_with_active_uses_core_command(monkeypatch):
    plain, live = _pair(hold=3, count=7)
    for document in (plain, live):
        document.add_track()
        document.add_track()
    live._promote()
    calls = _round_trips(live, monkeypatch)
    assert (live.remove_tracks([2, 0, 2], active=1)
            == plain.remove_tracks([2, 0, 2], active=1))
    assert calls == []
    assert _signature(live) == _signature(plain)
    assert [layer.name for layer in live.layers] == [layer.name for layer in plain.layers]
    assert live.active_layer_index == plain.active_layer_index
    # 範囲外の active は今の手順と同じく何もしない。
    assert live.remove_tracks([0], active=5) == plain.remove_tracks([0], active=5)
    assert _signature(live) == _signature(plain)


def test_restore_snapshot_after_remove_tracks_matches_python(monkeypatch):
    plain, live = _pair(hold=3, count=7)
    for document in (plain, live):
        document.add_track()
    live._promote()
    expected = plain.snapshot()
    snapshot = live.snapshot()
    calls = _round_trips(live, monkeypatch)
    assert live.remove_tracks([1]) == plain.remove_tracks([1])
    assert calls == []
    assert live.restore_snapshot(snapshot) == plain.restore_snapshot(expected)
    assert _signature(live) == _signature(plain)
    assert [layer.name for layer in live.layers] == [layer.name for layer in plain.layers]
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(0, 0, 255, 255))
    for document in (plain, live):
        document.write_drawing_patch(0, 1, patch, (1, 1, 2, 2))
    assert (live.frames[0].layers[1].image.read_rgba(0, 0, 4, 4)
            == plain.frames[0].layers[1].image.read_rgba(0, 0, 4, 4)).all()


def test_capture_column_reads_pixels_written_through_the_image(monkeypatch):
    # ブラシの確定と同じく、画像へ直接書いた画素も写しに入る（delete_layer の Undo）。
    plain, live = _pair(hold=3, count=7)
    live._promote()
    calls = _round_trips(live, monkeypatch)
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(0, 0, 255, 255))
    for document in (plain, live):
        document.frames[0].layers[1].image.write_qimage(patch, 3, 3)
    captured = live.capture_column(1)
    expected = plain.capture_column(1)
    assert calls == []
    for got, want in zip(captured, expected):
        assert (got.image.read_rgba(0, 0, 8, 8) == want.image.read_rgba(0, 0, 8, 8)).all()
    assert captured[0].image.read_rgba(3, 3, 1, 1).any()


def _delete_column(document, index, target):
    """canvas.delete_layer と同じ手順（写しを取ってから消す）。Undo の記録を返す。"""
    from paintmaskanimator.undo_entries import LayerInsertUndo
    entry = LayerInsertUndo(index, document.capture_column(index), target)
    document.remove_tracks((index,), active=target)
    return entry


def test_restore_inserted_track_uses_core_command_and_matches_python(monkeypatch):
    plain, live = _pair(hold=3, count=7)
    for document in (plain, live):
        document.add_track()
    live._promote()
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(0, 0, 255, 255))
    for document in (plain, live):
        document.frames[0].layers[1].image.write_qimage(patch, 3, 3)
    calls = _round_trips(live, monkeypatch)
    entries = [_delete_column(document, 1, 0) for document in (plain, live)]
    old_tracks, plain_old_tracks = live.tracks, plain.tracks
    assert live.restore_inserted_track(entries[1]) == plain.restore_inserted_track(entries[0])
    assert calls == []
    assert _signature(live) == _signature(plain)
    assert [layer.name for layer in live.layers] == [layer.name for layer in plain.layers]
    assert live.active_layer_index == plain.active_layer_index
    assert (live.tracks is old_tracks) == (plain.tracks is plain_old_tracks)
    assert len(old_tracks) == len(plain_old_tracks) == 2
    for a, b in zip(live.frames, plain.frames):
        assert (a.layers[1].image.read_rgba(0, 0, 8, 8)
                == b.layers[1].image.read_rgba(0, 0, 8, 8)).all()
    assert live.frames[0].layers[1].image.read_rgba(3, 3, 1, 1).any()
    # 列の中身（バンク・隠し属性・絵の名前など）も今の手順と同じ。
    assert (_column_signature([frame.layers[1] for frame in live.frames], monkeypatch)
            == _column_signature([frame.layers[1] for frame in plain.frames], monkeypatch))
    # 戻した列は一つの Track を共有し、Undo の記録の写しとは別のオブジェクト。
    column = [frame.layers[1] for frame in live.frames]
    assert all(slot._track is live.tracks[1] for slot in column)
    assert not any(slot is layer for slot in column for layer in entries[1].layers)
    # 戻した列に描いても、記録の写しは変わらない（もう一度戻せる）。
    before = entries[1].layers[0].image.read_rgba(0, 0, 8, 8).copy()
    for document in (plain, live):
        document.frames[0].layers[1].image.write_qimage(patch, 0, 0)
    assert (entries[1].layers[0].image.read_rgba(0, 0, 8, 8) == before).all()
    # 真ん中の列も同じ（Redo → Undo の順）。
    for document in (plain, live):
        document.remove_tracks((1,), active=0)
    assert live.restore_inserted_track(entries[1]) == plain.restore_inserted_track(entries[0])
    assert calls == []
    assert _signature(live) == _signature(plain)
    assert [layer.name for layer in live.layers] == [layer.name for layer in plain.layers]


def test_restore_inserted_track_with_fewer_layers_takes_the_current_path(monkeypatch):
    from paintmaskanimator.undo_entries import LayerInsertUndo
    plain, live = _pair(hold=3, count=7)
    for document in (plain, live):
        document.add_track()
    live._promote()
    entries = [LayerInsertUndo(2, document.capture_column(2)[:4], 1) for document in (plain, live)]
    for document in (plain, live):
        document.remove_tracks((2,), active=1)
    calls = _round_trips(live, monkeypatch)
    assert live.restore_inserted_track(entries[1]) == plain.restore_inserted_track(entries[0])
    assert calls
    assert _signature(live) == _signature(plain)
    assert [layer.name for layer in live.layers] == [layer.name for layer in plain.layers]


def test_restore_inserted_track_with_pending_positions_takes_the_current_path(monkeypatch):
    plain, live = _pair(hold=5, count=10)
    for document in (plain, live):
        document.add_track()
        document.insert_sequence_blank(1, 1)
    live._promote()
    entries = [_delete_column(document, 1, 0) for document in (plain, live)]
    assert any(layer.sequence_only for layer in entries[1].layers)
    calls = _round_trips(live, monkeypatch)
    assert live.restore_inserted_track(entries[1]) == plain.restore_inserted_track(entries[0])
    assert calls
    assert _signature(live) == _signature(plain)


def test_restore_inserted_track_out_of_range_or_unshared_takes_the_current_path(monkeypatch):
    from paintmaskanimator.undo_entries import LayerInsertUndo
    plain, live = _pair(hold=3, count=7)
    for document in (plain, live):
        document.add_track()
    live._promote()
    entries = [_delete_column(document, 2, 1) for document in (plain, live)]
    calls = _round_trips(live, monkeypatch)
    # 範囲外の index：今の手順は通知に丸める前の値を入れる。
    wide = [LayerInsertUndo(5, entry.layers, entry.target_active) for entry in entries]
    assert live.restore_inserted_track(wide[1]) == plain.restore_inserted_track(wide[0])
    assert calls
    assert _signature(live) == _signature(plain)
    for document in (plain, live):
        document.remove_tracks((2,), active=1)
    live._promote()
    calls.clear()
    # 一つの Track を共有していない記録。
    for entry in entries:
        entry.layers[3].detach_track()
    assert live.restore_inserted_track(entries[1]) == plain.restore_inserted_track(entries[0])
    assert calls
    assert _signature(live) == _signature(plain)
    assert [layer.name for layer in live.layers] == [layer.name for layer in plain.layers]


def test_move_tracks_uses_core_command_and_matches_python(monkeypatch):
    plain, live = _pair(hold=3, count=7)
    for document in (plain, live):
        document.add_track()
        document.active_layer_index = 2
    live._promote()
    calls = _round_trips(live, monkeypatch)
    old_tracks, plain_old_tracks = live.tracks, plain.tracks
    old_layers, plain_old_layers = live.frames[1].layers, plain.frames[1].layers
    slots = [list(frame.layers) for frame in live.frames]
    revision = Layer.timeline_revision
    assert live.move_tracks([2, 0, 1]) == plain.move_tracks([2, 0, 1])
    assert calls == []
    assert Layer.timeline_revision == revision
    assert _signature(live) == _signature(plain)
    assert [layer.name for layer in live.layers] == [layer.name for layer in plain.layers]
    assert live.active_layer_index == plain.active_layer_index
    # 位置のオブジェクトはそのまま並びだけ変わる。
    assert all(frame.layers[0] is row[2] and frame.layers[1] is row[0]
               for frame, row in zip(live.frames, slots))
    assert all(slot._track is live.tracks[0] for slot in (f.layers[0] for f in live.frames))
    # 渡した並びは入れ替え前のまま（今の手順はリストを入れ替える）。
    assert (live.tracks is old_tracks) == (plain.tracks is plain_old_tracks)
    assert [t.name for t in old_tracks] == [t.name for t in plain_old_tracks] == ["A", "B", "C"]
    assert (live.frames[1].layers is old_layers) == (plain.frames[1].layers is plain_old_layers)
    assert [s.name for s in old_layers] == [s.name for s in plain_old_layers] == ["A", "B", "C"]
    # 入れ替え前の並びへの書き込みは文書に届かない。
    for layers in (old_layers, plain_old_layers):
        del layers[0]
    assert [s.name for s in old_layers] == ["B", "C"]
    assert [s.name for s in live.frames[1].layers] == [s.name for s in plain.frames[1].layers]
    # 続けて Undo（restore_snapshot）でも同じ。
    snapshot, expected = live.snapshot(), plain.snapshot()
    assert live.move_tracks([1, 2, 0]) == plain.move_tracks([1, 2, 0])
    assert calls == []
    assert live.restore_snapshot(snapshot) == plain.restore_snapshot(expected)
    assert _signature(live) == _signature(plain)
    assert [layer.name for layer in live.layers] == [layer.name for layer in plain.layers]
    # 並びが同じ・不正なら何もしない。
    for order in ([0, 1, 2], [0, 0, 1], [0, 1]):
        assert live.move_tracks(order) == plain.move_tracks(order)
    assert _signature(live) == _signature(plain)


def test_move_tracks_with_pending_detached_positions_takes_the_current_path(monkeypatch):
    plain, live = _pair(hold=5, count=10)
    for document in (plain, live):
        document.clear_sheet_span(1, 2, 10)
        document.create_blank_key(9, 1)
    live._promote()
    assert any(values.get("pending_detached")
               for values, _ in live._live()["state"].positions.values())
    calls = _round_trips(live, monkeypatch)
    # 画面と同じく使い切りの並びで渡しても、今の経路へそのまま届く。
    assert live.move_tracks(reversed([0, 1])) == plain.move_tracks(reversed([0, 1]))
    assert calls
    assert _signature(live) == _signature(plain)
    assert _plain_flags(live) == _plain_flags(plain)


def test_duplicate_tracks_uses_core_command_and_matches_python(monkeypatch):
    plain, live = _pair(hold=3, count=7)
    for document in (plain, live):
        document.add_track()
    live._promote()
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(0, 0, 255, 255))
    for document in (plain, live):
        document.frames[0].layers[1].image.write_qimage(patch, 3, 3)
    calls = _round_trips(live, monkeypatch)
    old_tracks, plain_old_tracks = live.tracks, plain.tracks
    slots = [list(frame.layers) for frame in live.frames]
    names = {1: "B コピー", 0: "A コピー"}
    assert live.duplicate_tracks(names) == plain.duplicate_tracks(names)
    assert calls == []
    assert _signature(live) == _signature(plain)
    assert [layer.name for layer in live.layers] == [layer.name for layer in plain.layers]
    assert live.active_layer_index == plain.active_layer_index
    assert (live.tracks is old_tracks) == (plain.tracks is plain_old_tracks)
    assert len(old_tracks) == len(plain_old_tracks) == 3
    for index in range(5):
        assert (_column_signature([frame.layers[index] for frame in live.frames], monkeypatch)
                == _column_signature([frame.layers[index] for frame in plain.frames], monkeypatch))
    # 既存の位置はそのまま、複製は独立した列とバンク。
    assert all(frame.layers[0] is row[0] and frame.layers[2] is row[1] and frame.layers[4] is row[2]
               for frame, row in zip(live.frames, slots))
    copy = [frame.layers[3] for frame in live.frames]
    assert all(slot._track is live.tracks[3] for slot in copy)
    assert live.tracks[3] is not live.tracks[2]
    for document in (plain, live):
        document.frames[0].layers[3].image.write_qimage(patch, 0, 0)
    assert not live.frames[0].layers[2].image.read_rgba(0, 0, 1, 1).any()
    assert live.frames[0].layers[3].image.read_rgba(0, 0, 1, 1).any()
    assert _signature(live) == _signature(plain)


def test_duplicate_tracks_with_imageless_positions_takes_the_current_path(monkeypatch):
    # 今の手順は局所の絵が無い位置に空の絵を作って写すので、Rust の複製（絵なしで写す）は使わない。
    def frames():
        return [Frame([Layer(name, None) for name in ("A", "B", "C")]) for _ in range(2)]
    plain = GraphDocument()
    plain.install_legacy_frames(frames())
    live = document_live.Document()
    live.install_legacy_frames(frames())
    live._promote()
    calls = _round_trips(live, monkeypatch)
    names = {0: "Copy", 2: "Other"}
    assert live.duplicate_tracks(names) == plain.duplicate_tracks(names)
    assert calls
    assert _signature(live) == _signature(plain)
    for index in range(5):
        assert (_column_signature([frame.layers[index] for frame in live.frames], monkeypatch)
                == _column_signature([frame.layers[index] for frame in plain.frames], monkeypatch))


def test_duplicate_tracks_with_pending_positions_takes_the_current_path(monkeypatch):
    plain, live = _pair(hold=5, count=10)
    for document in (plain, live):
        document.add_track()
        document.insert_sequence_blank(1, 1)
    live._promote()
    calls = _round_trips(live, monkeypatch)
    assert live.duplicate_tracks({1: "B2"}) == plain.duplicate_tracks({1: "B2"})
    assert calls
    assert _signature(live) == _signature(plain)


def test_single_track_commands_reload_only_their_track(monkeypatch):
    # 1つの列だけを書き換える命令（後ろを送る露出の変更・連番の空きの挿入・連番の削除）は、
    # その列の位置だけを Rust から読み直す。ほかの列の写しはそのまま（同じ値のオブジェクト）
    # で、読み直したあとの写しは Rust の文書全体と一致し、結果は今の手順と同じ。
    import json
    from paintmaskanimator import pma_image, utils

    def normalized(value, handles):
        if isinstance(value, dict):
            return {key: (("img", handles[item].generation, handles[item].tag)
                          if key in ("image", "initial_image") and isinstance(item, int)
                          else normalized(item, handles))
                    for key, item in value.items()}
        if isinstance(value, list):
            return [normalized(item, handles) for item in value]
        return value

    narrowed = []
    original = document_live._CoreState.reload_rows

    def spy(self, frame_ids, track_ids=None, full_ids=()):
        narrowed.append(None if track_ids is None else list(track_ids))
        return original(self, frame_ids, track_ids, full_ids)
    monkeypatch.setattr(document_live._CoreState, "reload_rows", spy)
    operations = (
        lambda document: document.resize_sheet_exposure(
            0, "right", document.prepare_exposure_resize(0, 0, 7, "right")),
        lambda document: document.resize_sheet_exposure(
            0, "right", document.prepare_exposure_resize(0, 5, 13, "right")),
        lambda document: document.insert_sequence_blank(0, 1),
        lambda document: document.delete_sequence_entry(1, 2),
    )
    for operation in operations:
        plain, live = _pair(hold=5, count=10)
        live._promote()
        monkeypatch.setattr(pma_image, "_last_import", None)
        monkeypatch.setattr(utils._BlankTemplate, "model_image", None)
        utils.blank_model_image()
        calls = _round_trips(live, monkeypatch)
        state = live._live()["state"]
        layer_index = 1 if operation is operations[-1] else 0
        edited, other = state.track_ids[layer_index], state.track_ids[1 - layer_index]
        kept = {fid: state.position(fid, other)[0] for fid in state.frame_ids}
        del narrowed[:]
        assert operation(live) == operation(plain)
        assert narrowed == [[edited]]
        state = live._live()["state"]
        assert all(state.position(fid, other)[0] is values for fid, values in kept.items()
                   if fid in state.frame_index)
        core = live._live()["core"]
        text, handles = core.frames_graph(list(state.frame_ids))
        graph, handles = json.loads(text), list(handles)
        for frame in graph["frames"]:
            for tid, position in zip(graph["track_order"], frame["positions"]):
                values, cached = state.position(frame["id"], tid)
                assert normalized(values, cached) == normalized(position, handles)
        assert calls == []
        assert _whole_signature(live) == _whole_signature(plain)


def _block_frames():
    # 列 A：番号1・1・2、空キー（○）・空キー開始・空キー、配置待ちの1、未使用、番号2。
    # 列 B：番号3 が続き、末尾だけ未使用。
    shapes = [((True, False, 1, False, False), (True, False, 3, False, False)),
              ((True, False, 1, False, False), (True, False, 3, False, False)),
              ((True, False, 2, False, False), (True, False, 3, False, False)),
              ((False, True, None, False, False), (True, False, 3, False, False)),
              ((False, True, None, False, True), (True, False, 3, False, False)),
              ((False, True, None, False, False), (True, False, 3, False, False)),
              ((True, False, 1, True, False), (True, False, 3, False, False)),
              ((False, False, None, False, False), (True, False, 3, False, False)),
              ((True, False, 2, False, False), (False, False, None, False, False))]
    frames = []
    for row in shapes:
        layers = []
        for name, (content, blank, number, pending, start) in zip(("A", "B"), row):
            layer = Layer(name, PmaImage.blank(8, 8))
            layer.has_content, layer.is_blank_key = content, blank
            layer.sequence_number, layer.blank_start = number, start
            layer.sequence_only = pending
            layers.append(layer)
        frames.append(Frame(layers))
    return frames


def test_timeline_block_at_uses_core_and_matches_python(monkeypatch):
    plain = GraphDocument()
    plain.install_legacy_frames(_block_frames())
    live = document_live.Document()
    live.install_legacy_frames(_block_frames())
    calls = []
    original = document_live._core_timeline_block_at

    def counted(document, column, layer_index):
        result = original(document, column, layer_index)
        calls.append(result is not document_live._NO_CORE)
        return result
    monkeypatch.setattr(document_live, "_core_timeline_block_at", counted)
    cases = [(column, layer) for column in range(-1, 11) for layer in range(-1, 3)]
    for column, layer in cases:
        expected = GraphDocument.timeline_block_at(live, column, layer)
        assert live.timeline_block_at(column, layer) == expected
        assert plain.timeline_block_at(column, layer) == expected
    assert calls and all(calls)
    assert live.timeline_block_at(4, 0) == ("blank", 4, 2)
    assert live.timeline_block_at(6, 0) is None
    assert live._live()["graph"] is None
    # 書き換えたあとも Rust の文書の値で求まる。
    live.set_sheet_duration(0, 0, 3)
    plain.set_sheet_duration(0, 0, 3)
    for column, layer in cases:
        assert live.timeline_block_at(column, layer) == plain.timeline_block_at(column, layer)


def _imported_block_frames():
    # 取り込み時の参照（_dense_import）：署名が合えばその参照で見る。合わなければ今の値。
    from paintmaskanimator.legacy_v1 import BLANK, EMPTY, DenseReference
    from paintmaskanimator.timeline_metadata import set_dense_import
    frames = _block_frames()

    def signature(layer):
        return (layer.has_content, layer.is_blank_key, layer.sequence_number,
                layer.sequence_only, layer.blank_start)
    layer = frames[1].layers[0]
    set_dense_import(layer, (signature(layer), EMPTY, DenseReference("CELL", 1)))
    layer = frames[7].layers[0]
    set_dense_import(layer, (signature(layer), BLANK, EMPTY))
    layer = frames[2].layers[1]
    stale = (False, True, None, False, False)
    set_dense_import(layer, (stale, EMPTY, EMPTY))
    layer = frames[5].layers[1]
    set_dense_import(layer, (signature(layer), DenseReference("CELL", 4), DenseReference("CELL", 3)))
    return frames


def test_timeline_block_at_with_import_references_uses_core_and_matches_python(monkeypatch):
    plain = GraphDocument()
    plain.install_legacy_frames(_imported_block_frames())
    live = document_live.Document()
    live.install_legacy_frames(_imported_block_frames())
    calls = []
    original = document_live._core_timeline_block_at

    def counted(document, column, layer_index):
        result = original(document, column, layer_index)
        calls.append(result is not document_live._NO_CORE)
        return result
    monkeypatch.setattr(document_live, "_core_timeline_block_at", counted)
    for column in range(-1, 11):
        for layer in range(-1, 3):
            expected = GraphDocument.timeline_block_at(live, column, layer)
            assert live.timeline_block_at(column, layer) == expected
            assert plain.timeline_block_at(column, layer) == expected
    assert calls and all(calls)
    assert live.timeline_block_at(0, 0) == ("content", 0, 1)
    assert live.timeline_block_at(7, 0) == ("blank", 7, 1)
    assert live.timeline_block_at(5, 1) == ("content", 5, 1)
    assert live.timeline_block_at(2, 1) == ("content", 0, 5)
    assert live._live()["graph"] is None


def test_timeline_block_at_after_v2_load_uses_core_and_matches_python(monkeypatch):
    # v2 の読込は全位置に取り込み時の参照（_dense_import）を付ける（番号なしの絵も含む）。
    import pytest
    from paintmaskanimator import document as document_module, project_io
    from v2_golden_support import CASES, V2_ROOT
    original = document_live._core_timeline_block_at
    calls = []

    def counted(document, column, layer_index):
        result = original(document, column, layer_index)
        calls.append(result is not document_live._NO_CORE)
        return result
    for case in CASES:
        path = V2_ROOT / f"{case}.pman"
        with monkeypatch.context() as patch:
            patch.setattr(document_module, "Document", GraphDocument)
            plain = project_io.load_project(path).document
        with monkeypatch.context() as patch:
            patch.setattr(document_module, "Document", document_live.Document)
            live = project_io.load_project(path).document
        if not isinstance(live, document_live.Document):
            pytest.skip("v2 の読込が Rust 正本の文書を作らない")
        assert any("_dense_import" in layer.__dict__ for frame in plain.frames
                   for layer in frame.layers)
        calls.clear()
        with monkeypatch.context() as patch:
            patch.setattr(document_live, "_core_timeline_block_at", counted)
            for column in range(-1, len(plain.frames) + 1):
                for layer in range(-1, len(plain.tracks) + 1):
                    expected = plain.timeline_block_at(column, layer)
                    assert GraphDocument.timeline_block_at(live, column, layer) == expected, case
                    assert live.timeline_block_at(column, layer) == expected, case
        assert calls and all(calls), case


def _graph_value(value, handles):
    """to_graph の値の画像の添字を、画像の (世代, 札, 幅, 高さ) に置き換える。"""
    if isinstance(value, dict):
        return {key: (handles[item].generation, handles[item].tag, handles[item].width,
                      handles[item].height)
                if key in ("image", "initial_image") and isinstance(item, int)
                else _graph_value(item, handles) for key, item in value.items()}
    if isinstance(value, list):
        return [_graph_value(item, handles) for item in value]
    return value


def test_restore_snapshot_reuses_unchanged_values_after_painting(monkeypatch):
    plain, live = _pair(hold=5, count=10)
    live._promote()
    reused = []
    original = document_live._CoreState.restored.__func__

    def restored(cls, core, previous):
        state = original(cls, core, previous)
        reused.append(sum(previous.positions.get(key) is entry
                          for key, entry in state.positions.items()))
        fresh = cls(core)
        assert (state.prints, state.track_prints) == (fresh.prints, fresh.track_prints)
        # 使い回した値も、全体を読み直した値と同じ（画像は世代・札・大きさで比べる）。
        for name in ("positions", "tracks"):
            ours, theirs = getattr(state, name), getattr(fresh, name)
            assert ours.keys() == theirs.keys()
            for key, (values, handles) in theirs.items():
                assert _graph_value(*ours[key]) == _graph_value(values, handles), (name, key)
        return state
    monkeypatch.setattr(document_live._CoreState, "restored", classmethod(restored))

    def images(document):
        return [[None if slot.image is None else slot.image.rgba().tobytes()
                 for slot in frame.layers] for frame in document.frames]
    red = np.full((2, 2, 4), (255, 0, 0, 255), np.uint8)
    blue = np.full((2, 2, 4), (0, 0, 255, 255), np.uint8)
    # 窓口の画像に直接描くと Rust の画像だけが変わり、写しの値は古い世代のまま残る。
    # 写しの前に描いた列 A の絵は、写しのあとに変えない（戻しても見え方が同じなので
    # 使い回す候補になるが、写しの値は古いので取り直す）。写しのあとに描いた画素は消える。
    for document in (plain, live):
        document.frames[0].layers[0].image.write_rgba(red, 0, 0)
    expected, snapshot = plain.snapshot(), live.snapshot()
    for document in (plain, live):
        document.create_blank_key(7, 1)
        document.frames[4].layers[1].image.write_rgba(blue, 0, 0)
    assert images(live) == images(plain)
    assert live.restore_snapshot(snapshot) == plain.restore_snapshot(expected)
    assert reused and reused[0] > 0
    assert _signature(live) == _signature(plain)
    assert images(live) == images(plain)
    assert live.frames[0].layers[0].image.rgba()[0, 0].tolist() == [255, 0, 0, 255]


def _filled(rgba):
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(*rgba))
    return image


def _pixels(document):
    return [[None if slot.image is None else slot.image.rgba().tobytes()
             for slot in frame.layers] for frame in document.frames]


def test_replace_drawing_image_replaces_the_bank_image_in_place(monkeypatch):
    plain, live = _pair()
    calls = _round_trips(live, monkeypatch)
    old = live.frames[2].layers[0].image
    before = old.rgba().tobytes()
    image = PmaImage(_filled((255, 0, 0, 255)))
    assert (live.replace_drawing_image(2, 0, image, has_content=True)
            == plain.replace_drawing_image(2, 0, PmaImage(_filled((255, 0, 0, 255))),
                                           has_content=True))
    assert calls == []
    # 今の手順と同じく、同じ番号の位置は入れた画像そのものを指し、前の画像は今の画素で外れる。
    assert live.frames[2].layers[0].image is image and live.frames[3].layers[0].image is image
    assert old.rgba().tobytes() == before
    # 入れた画像への書き込みは文書の絵に届く。
    blue = np.full((2, 2, 4), (0, 0, 255, 255), np.uint8)
    image.write_rgba(blue, 0, 0)
    plain.frames[2].layers[0].image.write_rgba(blue, 0, 0)
    assert live.frames[3].layers[0].image.rgba()[0, 0].tolist() == [0, 0, 255, 255]
    # 同じ番号の位置へ一つの画像を入れる一括の差し替え（QImage を渡す）。同じ画像を入れ直すのは何もしない。
    groups = [([(4, 0), (5, 0)], _filled((0, 255, 0, 255))), ([(0, 0)], _filled((9, 9, 9, 255)))]
    assert live.replace_drawing_images(groups) == plain.replace_drawing_images(groups)
    assert live.replace_drawing_image(2, 0, image, has_content=True) == plain.replace_drawing_image(
        2, 0, plain.frames[2].layers[0].image, has_content=True)
    assert calls == []
    assert live.frames[4].layers[0].image is live.frames[5].layers[0].image
    assert _pixels(live) == _pixels(plain)
    assert _whole_signature(live) == _whole_signature(plain)


def test_restore_drawing_and_batch_restore_replace_the_bank_image_in_place(monkeypatch):
    plain, live = _pair()
    calls = _round_trips(live, monkeypatch)
    cells = [(column, 0, _filled((column * 40, 0, 0, 255)), True,
              plain.frames[column].layers[0].sequence_number) for column in (0, 2, 4)]
    for frame, layer, image, content, number in cells:
        expected = list(plain.restore_batch_drawing(frame, layer, image, content, number, sheet=True))
        assert list(live.restore_batch_drawing(frame, layer, image, content, number,
                                               sheet=True)) == expected
    # 番号を記録しない戻しと、取り消し一回分の戻し（restore_drawing）。
    image = _filled((1, 2, 3, 255))
    assert (list(live.restore_batch_drawing(1, 0, image, True, None, sheet=True))
            == list(plain.restore_batch_drawing(1, 0, image, True, None, sheet=True)))
    image = _filled((4, 5, 6, 255))
    assert (live.restore_drawing(4, 0, 3, image, True, sheet=True)
            == plain.restore_drawing(4, 0, 3, image, True, sheet=True))
    assert calls == []
    assert _pixels(live) == _pixels(plain)
    assert _whole_signature(live) == _whole_signature(plain)


def test_restore_drawing_with_a_copy_of_a_bound_image_runs_in_place(monkeypatch):
    # Undo の記録は窓口の画像を copy_image で写す。写しは型が BoundImage のままでも
    # 独立した画素なので、近道で差し替える（今の経路と同じ結果）。
    from paintmaskanimator.pma_image import copy_image
    plain, live = _pair()
    calls = _round_trips(live, monkeypatch)
    red = np.full((2, 2, 4), (255, 0, 0, 255), np.uint8)
    saved = [copy_image(document.frames[4].layers[0].image) for document in (plain, live)]
    assert type(saved[1]) is document_live.BoundImage
    for document in (plain, live):
        document.frames[4].layers[0].image.write_rgba(red, 0, 0)
    number = plain.frames[4].layers[0].sequence_number
    assert (live.restore_drawing(4, 0, number, saved[1], True, sheet=True)
            == plain.restore_drawing(4, 0, number, saved[0], True, sheet=True))
    assert calls == []
    assert live.frames[4].layers[0].image is saved[1]
    assert _pixels(live) == _pixels(plain)
    assert _whole_signature(live) == _whole_signature(plain)
    # 差し替えた画像は窓口になり、描けば文書に届く。
    for image in saved:
        image.write_rgba(red, 1, 1)
    assert _pixels(live) == _pixels(plain)


def test_image_replacement_that_needs_the_graph_takes_the_current_path(monkeypatch):
    plain, live = _pair()
    calls = _round_trips(live, monkeypatch)
    # 番号の無い位置（局所の絵）への差し替えで has_content も変わる。
    image = _filled((7, 7, 7, 255))
    assert (live.replace_drawing_image(2, 1, image, has_content=True)
            == plain.replace_drawing_image(2, 1, image, has_content=True))
    assert calls
    calls.clear()
    # has_content を外す戻しは、位置に局所の写しを作る。
    image = _filled((8, 8, 8, 255))
    assert (list(live.restore_batch_drawing(0, 0, image, False, 1, sheet=True))
            == list(plain.restore_batch_drawing(0, 0, image, False, 1, sheet=True)))
    assert calls == []
    # 別の番号の位置がすでに持つ画像（文書の絵の窓口）を入れるのも今の経路。
    calls.clear()
    shared = live.frames[4].layers[0].image
    assert (live.replace_drawing_image(2, 0, shared, has_content=True)
            == plain.replace_drawing_image(2, 0, plain.frames[4].layers[0].image,
                                           has_content=True))
    assert calls
    assert _pixels(live) == _pixels(plain)


def test_image_replacement_retries_with_the_same_groups_and_after_the_shortcut(monkeypatch):
    plain, live = _pair()
    # frame_scope と同じく生成式を渡す。2つめの組は局所の位置で、試しのやり直しになる。
    groups = [([(0, 0), (1, 0)], _filled((0, 255, 0, 255))), ([(2, 1)], _filled((9, 9, 9, 255)))]
    assert (live.replace_drawing_images(group for group in groups)
            == plain.replace_drawing_images(group for group in groups))
    assert _pixels(live) == _pixels(plain)
    assert _signature(live) == _signature(plain)
    # バンクの位置で has_content が変わる：近道で画像を入れたあと、今の経路でやり直す。
    for document in (plain, live):
        document.frames[4].layers[0].has_content = False
    image = _filled((3, 3, 3, 255))
    assert (live.replace_drawing_image(4, 0, image, has_content=True)
            == plain.replace_drawing_image(4, 0, image, has_content=True))
    assert _pixels(live) == _pixels(plain)
    assert _whole_signature(live) == _whole_signature(plain)


def test_initial_image_and_cell_assignment_use_the_shortcut(monkeypatch):
    plain, live = _pair()
    calls = _round_trips(live, monkeypatch)
    red = np.full((2, 2, 4), (255, 0, 0, 255), np.uint8)
    for document in (plain, live):
        # もう一方の画像に描いたあとでも、その画素を保つ。
        document.frames[0].layers[0].image.write_rgba(red, 0, 0)
        document.frames[0].layers[0].initial_image = _filled((1, 1, 1, 255))
        document.tracks[0].cells[2].image = _filled((2, 2, 2, 255))
    assert calls == []
    assert live.frames[0].layers[0].image.rgba()[0, 0].tolist() == [255, 0, 0, 255]
    assert (live.frames[1].layers[0].initial_image.rgba().tobytes()
            == plain.frames[1].layers[0].initial_image.rgba().tobytes())
    assert _pixels(live) == _pixels(plain)
    assert _whole_signature(live) == _whole_signature(plain)


def test_batch_restore_of_a_bank_only_drawing_runs_in_place(monkeypatch):
    from paintmaskanimator.models import Cell
    plain, live = _pair()
    for document in (plain, live):
        document.tracks[0].cells[9] = Cell(PmaImage.blank(8, 8))
    calls = _round_trips(live, monkeypatch)
    image = _filled((5, 5, 5, 255))
    expected = list(plain.restore_batch_drawing(0, 0, image, True, 9, sheet=True))
    assert list(live.restore_batch_drawing(0, 0, image, True, 9, sheet=True)) == expected
    assert expected and expected[0].changes[0].frame is None
    assert calls == []
    assert (live.tracks[0].cells[9].image.rgba().tobytes()
            == plain.tracks[0].cells[9].image.rgba().tobytes())
    assert _whole_signature(live) == _whole_signature(plain)



def test_make_position_editable_on_empty_positions_uses_core_command(monkeypatch):
    plain, live = _pair(hold=2, count=10)
    # 空キーの空画像の世代は直前の取り込みしだい（空画像を作る Rust の命令に共通の既知の差）
    # なので、直前の取り込みを空画像の雛形にそろえてから比べる。
    from paintmaskanimator import pma_image, utils
    monkeypatch.setattr(pma_image, "_last_import", None)
    monkeypatch.setattr(utils._BlankTemplate, "model_image", None)
    utils.blank_model_image()
    for document in (plain, live):
        document.create_blank_key(4, 1)          # 列 B の 5 から空キー
        document.set_sheet_duration(5, 1, 3)     # 空キーを 5〜7 の 3 コマ保持にする
        document.frames[7].layers[0].has_content = False   # 番号 4 が残る位置
    calls = _round_trips(live, monkeypatch)
    # 局所の絵のある空の位置、空キーの保持の途中（後ろも同じ番号になる）、番号の残る位置。
    for current, layer, block in ((2, 1, None), (6, 1, ("blank", 5, 3)), (7, 0, None)):
        assert live.timeline_block_at(current, layer) == plain.timeline_block_at(
            current, layer) == block
        old_image = live.frames[current].layers[layer].image
        before = old_image.rgba().tobytes()
        expected = plain.make_position_editable(current, layer, "sheet")
        assert live.make_position_editable(current, layer, "sheet") == expected, (current, layer)
        assert calls == [], (current, layer)
        # 前の局所の絵の画像は、外したときの画素のまま文書から外れる。
        assert old_image is not live.frames[current].layers[layer].image
        assert old_image.rgba().tobytes() == before
        assert _signature(live) == _signature(plain), (current, layer)
        assert _pixels(live) == _pixels(plain)
    assert live.frames[7].layers[1].sequence_number == plain.frames[7].layers[1].sequence_number
    assert _whole_signature(live) == _whole_signature(plain)


def _editable_pair(shape):
    """make_position_editable を当てる前の形（列 B の 2 は局所の絵のある空の位置）。"""
    from paintmaskanimator.models import Cell
    plain, live = _pair(hold=2, count=6)
    for document in (plain, live):
        if shape in ("initial", "three"):
            document.frames[2].layers[1].initial_image = PmaImage.blank(8, 8)
            document.frames[2].layers[1].cell_name = "X"
        if shape != "bank":
            document.duplicate_frame(2)          # 2 と 3 が局所の絵の共有の組（先頭は 2）
        if shape == "three":
            document.duplicate_frame(2)          # 2・3・4 の組
        if shape == "initial":
            # 組の絵への書き込みは組の先頭の値に届く。
            document.frames[2].layers[1].initial_image.write_rgba(
                np.full((2, 2, 4), (0, 0, 255, 255), np.uint8), 0, 0)
        if shape == "bank":
            document.tracks[1].cells[9] = Cell(PmaImage.blank(8, 8))
    return plain, live


def test_make_position_editable_on_shared_and_numbered_shapes(monkeypatch):
    for shape, current, fast in (("pair", 3, True), ("initial", 3, True), ("three", 4, True),
                                 ("pair", 2, False), ("bank", 2, True)):
        plain, live = _editable_pair(shape)
        cells = [(live.frames[c].layers[1]._cell, plain.frames[c].layers[1]._cell)
                 for c in range(len(plain.frames))]
        calls = _round_trips(live, monkeypatch)
        assert (live.make_position_editable(current, 1, "sheet")
                == plain.make_position_editable(current, 1, "sheet")), shape
        assert bool(calls) is not fast, (shape, current)
        assert _signature(live) == _signature(plain), shape
        assert _pixels(live) == _pixels(plain), shape
        for column in range(len(plain.frames)):
            ours, theirs = live.frames[column].layers[1], plain.frames[column].layers[1]
            for name in ("initial_image", "cell_name"):
                value, expected = getattr(ours, name), getattr(theirs, name)
                if name == "initial_image" and value is not None:
                    value, expected = value.rgba().tobytes(), expected.rgba().tobytes()
                assert value == expected, (shape, column, name)
            if column != current and fast:
                # 組に残る位置の絵は描き始める前と同じ Cell（組の Cell の同一性も同じ）。
                same = cells[column][1] is theirs._cell
                assert (cells[column][0] is ours._cell) is same, (shape, column)
        if shape == "bank":
            assert live.frames[2].layers[1].sequence_number == 10
        assert _whole_signature(live) == _whole_signature(plain), shape
        monkeypatch.undo()


def _shuffled_frames():
    # A 列 [3,3,1,空キー(番号2を残す),2,5]、B 列 [1,2,2,1,未使用,1]。5 と 3 はシートの
    # 初出順で 1・2 になり、空キーの残す番号も写る。
    rows = []
    for a, b in ((3, 1), (3, 2), (1, 2), ("blank", 1), (2, None), (5, 1)):
        layers = []
        for name, number in (("A", a), ("B", b)):
            if number == "blank":
                layer = _colored_layer(name, (0, 0, 255, 255), content=False, number=2,
                                       blank=True)
            elif number is None:
                layer = _colored_layer(name, (0, 0, 0, 0), content=False)
            else:
                layer = _colored_layer(name, (number * 40, 0, 0, 255), number=number)
            layers.append(layer)
        rows.append(Frame(layers))
    return rows


def test_normalize_sheet_numbers_uses_core_command_and_matches_python(monkeypatch):
    for indices in ((0,), (1,), (0, 1), (1, 0)):
        plain = GraphDocument()
        plain.install_legacy_frames(_shuffled_frames())
        live = document_live.Document()
        live.install_legacy_frames(_shuffled_frames())
        for document in (plain, live):
            document.put_archive_hint(document.tracks[0], 5, ArchiveHint())
        live._promote()
        banks = [{number: (cell, cell.image) for number, cell in track.cells.items()}
                 for track in live.tracks]
        plain_banks = [{number: (cell, cell.image) for number, cell in track.cells.items()}
                       for track in plain.tracks]
        slots = [list(frame.layers) for frame in live.frames]
        calls = _round_trips(live, monkeypatch)
        assert live.normalize_sheet_numbers(indices) == plain.normalize_sheet_numbers(indices)
        assert calls == []
        assert _signature(live) == _signature(plain)
        assert [list(frame.layers) for frame in live.frames] == slots
        assert all(a is b for frame, row in zip(live.frames, slots)
                   for a, b in zip(frame.layers, row))
        # バンクの Cell・画像は、同じオブジェクトのまま新しい番号へ移る。
        for bank, plain_bank, track, plain_track in zip(banks, plain_banks, live.tracks,
                                                       plain.tracks):
            moved = {number: [n for n, cell in track.cells.items()
                              if cell is pair[0] and cell.image is pair[1]]
                     for number, pair in bank.items()}
            plain_moved = {number: [n for n, cell in plain_track.cells.items()
                                    if cell is pair[0] and cell.image is pair[1]]
                           for number, pair in plain_bank.items()}
            assert moved == plain_moved
        assert ({n: vars(h) for n, h in live.tracks[0].legacy_archive_hints.items()}
                == {n: vars(h) for n, h in plain.tracks[0].legacy_archive_hints.items()})
        assert _whole_signature(live) == _whole_signature(plain)


def test_normalize_sheet_numbers_with_unnumbered_content_takes_the_current_path(monkeypatch):
    # 番号の無い内容キー（局所の絵をバンクへ移す）、番号が変わる位置の取り込み時の記録、
    # 列の指定が不正・重なる場合は今の経路。
    from paintmaskanimator.legacy_v1 import BLANK, EMPTY
    from paintmaskanimator.timeline_metadata import set_dense_import


    def imported():
        frames = _shuffled_frames()
        slot = frames[0].layers[0]
        set_dense_import(slot, ((True, False, 3, False, False), BLANK, EMPTY))
        return frames
    for frames, indices in ((_shuffled_frames, (1,)), (imported, (0,)),
                            (_shuffled_frames, (0, 0)), (_shuffled_frames, (2,))):
        plain = GraphDocument()
        plain.install_legacy_frames(frames())
        live = document_live.Document()
        live.install_legacy_frames(frames())
        if indices == (1,):
            # 取り込みは番号の無い内容キーに番号を振るので、取り込んだあとに外す。
            for document in (plain, live):
                document.frames[1].layers[1].sequence_number = None
        live._promote()
        assert live._live()["graph"] is None
        calls = _round_trips(live, monkeypatch)
        assert live.normalize_sheet_numbers(indices) == plain.normalize_sheet_numbers(indices)
        assert calls
        assert _whole_signature(live) == _whole_signature(plain)


def test_normalize_sheet_numbers_with_v1_records_pending_and_undo(monkeypatch):
    # 旧形式の記録（番号も写る）・配置待ちの位置・バンクにしか無い番号があっても Rust の命令で
    # 当て、Undo の写しへ戻してもう一度当てても今の手順と同じになる。
    from paintmaskanimator.legacy_v1 import V1Position, snapshot_layer

    def frames():
        rows = [Frame([_colored_layer("A", (n * 40, 0, 0, 255), number=n),
                       _colored_layer("B", (0, n * 40, 0, 255), number=n)])
                for n in (3, 3, 1, 2, 2, 6)]
        source = _colored_layer("A", (9, 9, 9, 255), number=3)
        rows[1].layers[0].__dict__["_v1_position"] = V1Position(snapshot_layer(source),
                                                                key_start=True)
        return rows
    documents = []
    for factory in (GraphDocument, document_live.Document):
        document = factory()
        document.install_legacy_frames(frames())
        document.frames[4].layers[0].set_pending(True)
        # 6 はバンクにだけ残る番号にする。
        document.clear_sheet_span(0, 5, 6)
        documents.append(document)
    plain, live = documents
    live._promote()
    snapshots = (plain.snapshot(), live.snapshot())
    for _ in range(2):
        calls = _round_trips(live, monkeypatch)
        assert (live.normalize_sheet_numbers((0, 1))
                == plain.normalize_sheet_numbers((0, 1)))
        assert calls == []
        assert _signature(live) == _signature(plain)
        assert sorted(live.tracks[0].cells) == sorted(plain.tracks[0].cells)
        assert _v1_records(live) == _v1_records(plain)
        assert _whole_signature(live) == _whole_signature(plain)
        live._promote()
        assert live.restore_snapshot(snapshots[1]) == plain.restore_snapshot(snapshots[0])
        assert _whole_signature(live) == _whole_signature(plain)
        live._promote()


def test_place_pending_entries_without_pending_runs_in_place(monkeypatch):
    # 番号のある配置待ちが無ければ、今の手順は文書を変えずに空の変更を返す。
    plain, live = _pair(hold=2, count=6)
    live._promote()
    slots = [list(frame.layers) for frame in live.frames]
    calls = _round_trips(live, monkeypatch)
    assert live.place_pending_entries() == plain.place_pending_entries()
    assert calls == []
    assert all(a is b for frame, row in zip(live.frames, slots) for a, b in zip(frame.layers, row))
    assert _whole_signature(live) == _whole_signature(plain)
    # 配置待ちはあるが番号が無い：まだ往復しない（条件の「番号がある」側を固定する）。
    for document in (plain, live):
        assert document.frames[2].layers[1].sequence_number is None
        document.frames[2].layers[1].set_pending(True)
    live._promote()
    calls = _round_trips(live, monkeypatch)
    assert live.place_pending_entries() == plain.place_pending_entries()
    assert calls == []
    assert _whole_signature(live) == _whole_signature(plain)
    for document in (plain, live):
        document.insert_sequence_blank(0, 1)
    live._promote()
    calls = _round_trips(live, monkeypatch)
    expected = plain.place_pending_entries()
    assert expected
    assert live.place_pending_entries() == expected
    assert calls == []
    assert _whole_signature(live) == _whole_signature(plain)


def test_clone_graph_copies_from_core_without_round_trip(monkeypatch):
    # 保存の写しは書き出し往復なしで取り、今の手順の写しと同じ値。写しは文書から切り離れている。
    from paintmaskanimator.models import Layer
    from paintmaskanimator.project_io import ProjectSnapshot, build_v2_payload
    plain, live = _pair(hold=2, count=6)
    for document in (plain, live):
        document.frames[2].layers[1].set_pending(True)
        document.insert_sequence_blank(0, 1)
    live._promote()
    slots = [list(frame.layers) for frame in live.frames]
    calls = _round_trips(live, monkeypatch)
    before = Layer.timeline_revision
    expected = plain.clone_graph()
    step = Layer.timeline_revision - before
    copied = live.clone_graph()
    assert calls == []
    # 今の手順は写した位置の形しだいの量だけ進め、近道は1つ進める（意味は「進んだか」だけ）。
    assert step > 0 and Layer.timeline_revision - before == step + 1
    assert all(a is b for frame, row in zip(live.frames, slots) for a, b in zip(frame.layers, row))
    assert (copied.current_frame, copied.active_layer_index) == (
        expected.current_frame, expected.active_layer_index)
    assert _whole_signature(copied) == _whole_signature(expected)
    metadata, pngs = build_v2_payload(ProjectSnapshot({}, copied, 8, 8))
    expected_metadata, expected_pngs = build_v2_payload(ProjectSnapshot({}, expected, 8, 8))
    assert metadata == expected_metadata
    assert [(path, error, image.read_rgba(0, 0, image.width, image.height).tobytes())
            for path, image, error in pngs] == [
        (path, error, image.read_rgba(0, 0, image.width, image.height).tobytes())
        for path, image, error in expected_pngs]
    # 写しを取ったあとの描き込みは写しに入らない。
    original = copied.frames[0].layers[0].image.read_rgba(0, 0, 2, 2).copy()
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(0, 0, 255, 255))
    live.write_drawing_patch(0, 0, patch, (0, 0, 2, 2))
    assert (copied.frames[0].layers[0].image.read_rgba(0, 0, 2, 2) == original).all()
    assert calls == []


def test_clone_graph_falls_back_when_round_trip_needed(monkeypatch):
    # 素の状態では今の経路。Rust の状態でも今の手順と同じにできなければ往復する。
    from paintmaskanimator import document_live as module
    plain, live = _pair()
    live._demote()
    assert _whole_signature(live.clone_graph()) == _whole_signature(plain.clone_graph())
    plain, live = _pair()
    live._promote()
    monkeypatch.setattr(module, "_snapshot_bytes", lambda state: None)
    calls = _round_trips(live, monkeypatch)
    assert _whole_signature(live.clone_graph()) == _whole_signature(plain.clone_graph())
    assert calls


def test_set_drawing_content_uses_core_command(monkeypatch):
    # 印を外すと局所の絵はバンクの絵の写しになり（前の局所の絵は文書から外れる）、外す前の
    # 番号を残す。立て直すと _content_origin を外す。どちらも往復なしで今の手順と同じ。
    from paintmaskanimator.models import Layer
    plain, live = _pair(hold=2, count=6)
    live._promote()
    calls = _round_trips(live, monkeypatch)
    commands = []
    apply_core_command = document_live._apply_core_command

    def apply(document, operation, *args, **kwargs):
        commands.append(operation)
        return apply_core_command(document, operation, *args, **kwargs)

    monkeypatch.setattr(document_live, "_apply_core_command", apply)
    slot = live.frames[1].layers[0]
    local = slot.__dict__["_cell"] if "_cell" in slot.__dict__ else None
    for frame, value in ((1, False), (3, False), (1, True), (0, False)):
        before = Layer.timeline_revision
        expected = plain.set_drawing_content(frame, 0, value)
        step = Layer.timeline_revision - before
        assert live.set_drawing_content(frame, 0, value) == expected
        assert commands.pop() == ["set_drawing_content", frame, 0, value]
        assert Layer.timeline_revision - before == 2 * step
        assert calls == []
        assert _whole_signature(live) == _whole_signature(plain)
        live._promote()
        calls.clear()
    assert live.frames[1].layers[0] is slot
    if local is not None:
        assert local is not live.frames[1].layers[0].__dict__.get("_cell")
    # 番号の無い位置の印も同じ。
    plain, live = _pair()
    live._promote()
    calls = _round_trips(live, monkeypatch)
    for frame, layer, value in ((0, 0, True), (0, 0, False)):
        assert (live.set_drawing_content(frame, layer, value)
                == plain.set_drawing_content(frame, layer, value))
        assert _whole_signature(live) == _whole_signature(plain)
        live._promote()
        calls.clear()


def test_restore_drawing_changing_content_runs_in_place(monkeypatch):
    # バンクの絵の画像を戻して印を変える Undo は往復なし。窓口の画像は戻した画像になる。
    plain, live = _pair(hold=2, count=6)
    live._promote()
    calls = _round_trips(live, monkeypatch)
    number = live.frames[1].layers[0].sequence_number
    image = QImage(live.frames[1].layers[0].image.to_qimage())
    image.fill(QColor(0, 255, 0, 255))
    for has_content in (False, True):
        expected = plain.restore_drawing(1, 0, number, QImage(image), has_content, sheet=True)
        assert live.restore_drawing(1, 0, number, QImage(image), has_content,
                                    sheet=True) == expected
        assert calls == []
        assert _whole_signature(live) == _whole_signature(plain)
        live._promote()
        calls.clear()
        assert (live.frames[1].layers[0].image.read_rgba(0, 0, 1, 1)
                == plain.frames[1].layers[0].image.read_rgba(0, 0, 1, 1)).all()
        calls.clear()
    # 画素の書き込みで戻す形も同じ。
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(9, 9, 9, 255))
    patches = [((0, 0, 2, 2), patch)]
    expected = plain.restore_drawing(1, 0, number, None, False, sheet=True, patches=patches)
    assert live.restore_drawing(1, 0, number, None, False, sheet=True,
                                patches=patches) == expected
    assert calls == []
    assert _whole_signature(live) == _whole_signature(plain)


def test_restore_drawing_patches_on_a_local_drawing_changing_content_runs_in_place(monkeypatch):
    # 番号の無い位置（局所の絵）へ画素の差分を戻して印を変える Undo も往復なし。
    plain, live = _pair()
    live._promote()
    calls = _round_trips(live, monkeypatch)
    assert live.frames[2].layers[1].sequence_number is None
    assert not live.frames[2].layers[1].has_content
    patch = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    for has_content, color in ((True, (9, 9, 9, 255)), (False, (0, 200, 0, 255))):
        patch.fill(QColor(*color))
        patches = [((1, 1, 2, 2), QImage(patch))]
        expected = plain.restore_drawing(2, 1, None, None, has_content, sheet=True,
                                         patches=patches)
        assert live.restore_drawing(2, 1, None, None, has_content, sheet=True,
                                    patches=patches) == expected
        assert calls == []
        assert _pixels(live) == _pixels(plain)
        assert _whole_signature(live) == _whole_signature(plain)
        live._promote()
        calls.clear()


def test_content_changes_rust_cannot_represent_take_the_python_path(monkeypatch):
    # 空キーの位置に内容を立てる（今の手順は空キーのまま）と、blank_start の内容の位置の
    # 内容を外す（今の手順は空キーにしない）は、Rust の位置の表現が違う形になるので今の経路。
    from paintmaskanimator.models import Frame, Layer
    from paintmaskanimator.pma_image import PmaImage

    def build(content, blank, number, start):
        frames = []
        for _ in range(2):
            layer = Layer("A", PmaImage.blank(8, 8))
            layer.sequence_number = number
            layer.has_content = content
            layer.is_blank_key = blank
            layer.blank_start = start
            frames.append(Frame([layer]))
        return frames

    cases = (((False, True, None, True), "set", True),
             ((False, True, 1, True), "set", True),
             ((True, False, 2, True), "set", False),
             ((True, False, 2, True), "restore", False))
    for spec, kind, value in cases:
        plain, live = GraphDocument(), document_live.Document()
        plain.install_legacy_frames(build(*spec))
        live.install_legacy_frames(build(*spec))
        live._promote()
        calls = _round_trips(live, monkeypatch)
        if kind == "set":
            expected = plain.set_drawing_content(1, 0, value)
            assert live.set_drawing_content(1, 0, value) == expected
        else:
            image = plain.frames[1].layers[0].image.to_qimage().copy()
            expected = plain.restore_drawing(1, 0, spec[2], image, value, sheet=True)
            assert live.restore_drawing(1, 0, spec[2], image, value, sheet=True) == expected
        assert calls
        assert ([(s.has_content, s.is_blank_key) for f in live.frames for s in f.layers]
                == [(s.has_content, s.is_blank_key) for f in plain.frames for s in f.layers])
        assert _whole_signature(live) == _whole_signature(plain)


def test_delete_frame_that_trims_frames_keeps_detached_pixels_like_python(monkeypatch):
    # 末尾のコマに色の付いた局所の絵と初期画像、コマごとに違う尺。切られるコマへ局所の絵が
    # 移る形・最後のコマを消す形を、全部持つ場合と位置だけ持つ場合で比べる。
    def image(rgba):
        result = PmaImage.blank(8, 8)
        values = np.zeros((8, 8, 4), dtype=np.uint8)
        values[:, :] = rgba
        result.write_rgba(values)
        return result

    def frames():
        result = []
        for column in range(6):
            a = Layer("A", image((200, 10 * column, 0, 255) if column < 3
                                 else (0, 30 * column, 200, 255)))
            if column < 3:
                a.has_content, a.sequence_number = True, column + 1
            else:
                a.initial_image = image((5 * column, 1, 2, 255))
            b = Layer("B", image((1, 2, 20 * column, 200)))
            b.initial_image = image((9, 9, 10 * column, 255))
            result.append(Frame([a, b], column + 1))
        return result

    def pixels(value):
        return None if value is None else np.asarray(value.rgba()).tobytes()

    for column, layer_index in ((3, 0), (4, 1), (5, 1), (2, 0)):
        for whole in (True, False):
            plain = GraphDocument()
            plain.install_legacy_frames(frames())
            live = document_live.Document()
            live.install_legacy_frames(frames())
            live._promote()
            count = len(live.frames)
            held = [(live.frames[i], plain.frames[i]) for i in range(count)]
            slots = [(live.frames[i].layers[j], plain.frames[i].layers[j])
                     for i in range(count) for j in range(2)]
            if whole:
                for slot, _ in slots:
                    _ = (slot._cell, slot.image, slot.initial_image)  # 窓口を全部作っておく
            calls = _round_trips(live, monkeypatch)
            assert live.delete_frame(column, layer_index) == plain.delete_frame(column, layer_index)
            assert calls == [], (column, layer_index, whole)
            assert len(live.frames) == len(plain.frames) < count
            monkeypatch.undo()
            for frame, plain_frame in held:
                assert frame.duration == plain_frame.duration
                assert ([(pixels(s.image), pixels(s.initial_image)) for s in frame.layers]
                        == [(pixels(s.image), pixels(s.initial_image))
                            for s in plain_frame.layers])
            for slot, plain_slot in slots:
                assert (pixels(slot.image), pixels(slot.initial_image)) == (
                    pixels(plain_slot.image), pixels(plain_slot.initial_image))
                assert ([k for k, (a, _) in enumerate(slots) if a._cell is slot._cell]
                        == [k for k, (_, b) in enumerate(slots) if b._cell is plain_slot._cell])
            assert _whole_signature(live) == _whole_signature(plain)


def test_recall_sheet_number_uses_core_command_and_matches_python(monkeypatch):
    # (形, 置くコマ, 列, 番号を取るコマ, 置く前に消すか)。保持の途中・キーの頭・消した位置・
    # コマ数より先（コマを足す）へ、バンクの絵の札と位置の Layer の両方を source にして置く。
    for shape, column, layer_index, origin, clear in (
            ({"hold": 5, "count": 10}, 7, 0, 0, False),
            ({"hold": 5, "count": 10}, 2, 1, 4, False),
            ({"hold": 3, "count": 9}, 4, 0, 0, True),
            ({"hold": 3, "count": 9}, 12, 1, 3, False)):
        for as_layer in (False, True):
            plain, live = _pair(**shape)
            live._promote()
            results, held = [], []
            for document in (plain, live):
                if clear:
                    document.clear_sheet_span(layer_index, column, column + 1)
                number = document.frames[origin].layers[layer_index].sequence_number
                source = (document.frames[origin].layers[layer_index] if as_layer
                          else document.resolve_numbered_target(origin, layer_index, number,
                                                                sheet=True))
                slots = [frame.layers[layer_index] for frame in document.frames]
                if document is live:
                    calls = _round_trips(live, monkeypatch)
                results.append(document.recall_sheet_number(column, layer_index, number, source))
                held.append([any(slot is layer for frame in document.frames
                                 for layer in frame.layers) for slot in slots])
            assert results[1] == results[0]
            assert calls == [], (shape, column, as_layer)
            assert held[1] == held[0]
            assert len(live.frames) == len(plain.frames)
            assert _whole_signature(live) == _whole_signature(plain)
            monkeypatch.undo()


def test_recall_sheet_number_after_unsetting_content_matches_python(monkeypatch):
    # 印を外した位置（_content_origin を持つ）へ置く形は今の経路で、今の手順と同じになる。
    for column in (1, 2):
        plain, live = _pair(hold=2, count=6)
        live._promote()
        results = []
        for document in (plain, live):
            document.set_drawing_content(column, 0, False)
            source = document.resolve_numbered_target(0, 0, 1, sheet=True)
            results.append(document.recall_sheet_number(column, 0, 1, source))
        assert results[1] == results[0]
        assert (live.frames[column].layers[0].__dict__.get("_content_origin")
                == plain.frames[column].layers[0].__dict__.get("_content_origin"))
        assert _whole_signature(live) == _whole_signature(plain)


def test_recall_sheet_number_keeps_v2_resolve_source_like_python(monkeypatch):
    # v2 の読み込みで _v2_resolve_source を持つ位置へ置く形は今の経路で、今の手順と同じく残す。
    from paintmaskanimator import document as document_module, project_io
    from v2_golden_support import V2_ROOT

    def load(cls):
        saved = document_module.Document
        document_module.Document = cls
        try:
            return project_io.load_project(V2_ROOT / "pending-compat.pman").document
        finally:
            document_module.Document = saved

    for number in (1, 9):
        plain, live = load(GraphDocument), load(document_live.Document)
        if live._live()["graph"] is not None:
            live._promote()
        assert live.frames[5].layers[2].__dict__.get("_v2_resolve_source") is not None
        results = [document.recall_sheet_number(5, 2, number, document.frames[0].layers[2])
                   for document in (plain, live)]
        assert results[1] == results[0]
        assert _whole_signature(live) == _whole_signature(plain)


def test_archive_hints_edit_without_round_trip_and_match_python(monkeypatch):
    # 旧連番保管の一件の書き込み・上書き・取り除き（tween 付き・無い番号を含む）は往復なしで、
    # 今の手順と同じ値になる。
    plain, live = _pair(hold=2, count=6)
    live._promote()
    calls = _round_trips(live, monkeypatch)
    results = []
    for document in (plain, live):
        track = document.tracks[1]
        steps = [
            document.put_archive_hint(track, 2, ArchiveHint(False, True, 3)),
            document.put_archive_hint(track, 7, ArchiveHint(True, False, 2, True, None, None)),
            document.put_archive_hint(track, 2, ArchiveHint(True, False, 1)),
            document.remove_archive_hint(track, 7),
            document.remove_archive_hint(track, 9),
        ]
        track.legacy_archive_hints[4] = ArchiveHint(False, False, 5)
        del track.legacy_archive_hints[2]
        results.append((steps, dict(document.archive_hints(track)),
                        document.legacy_drawing_state(track, 4)))
    assert results[1] == results[0]
    assert calls == []
    assert _whole_signature(live) == _whole_signature(plain)


def test_archive_hint_none_value_and_plain_self_keep_current_path(monkeypatch):
    # 値 None の書き込みは取り除きにならず今の経路（素のグラフに None を持つ）。素の入れ物を
    # self にした呼び出しは今の Python の手順のまま（独立レビューの指摘）。
    plain, live = _pair(hold=2, count=6)
    live._promote()
    for document in (plain, live):
        document.put_archive_hint(document.tracks[0], 3, ArchiveHint(False, True, 2))
    for document in (plain, live):
        try:
            document.put_archive_hint(document.tracks[0], 3, None)
        except Exception:  # noqa: BLE001 - 今の経路は取り込みで失敗する
            pass
    graph = live._live()["graph"]
    assert graph is not None and live.tracks[0].legacy_archive_hints == {3: None}
    assert plain.tracks[0].legacy_archive_hints == {3: None}
    other, _ = _pair(hold=2, count=4)
    document_live.Document.put_archive_hint(other, other.tracks[0], 2, ArchiveHint())
    assert document_live.Document.remove_archive_hint(other, other.tracks[0], 2) == ArchiveHint()


def test_clone_graph_and_lazy_snapshot_build_once_from_core(monkeypatch):
    # 保存の写しと遅延の Undo の写しの中身は、Rust で整えた写しから一度だけ組み立てる
    # （素のグラフを Python で写し直さない）。値は今の手順と同じ。
    from paintmaskanimator import document as document_module
    plain, live = _pair(hold=2, count=6)
    for document in (plain, live):
        document.frames[2].layers[1].set_pending(True)
        document.insert_sequence_blank(0, 1)
    live._promote()
    expected_graph, expected_snapshot = plain.clone_graph(), plain.snapshot()
    snapshot = live.snapshot()

    def forbidden(*args, **kwargs):
        raise AssertionError("clone_frame_graph は呼ばない")
    monkeypatch.setattr(document_module, "clone_frame_graph", forbidden)
    calls = _round_trips(live, monkeypatch)
    copied = live.clone_graph()
    frames = snapshot.frames
    assert calls == []
    assert _whole_signature(copied) == _whole_signature(expected_graph)
    graph = object.__new__(document_module.GraphDocument)
    graph.frames, graph.tracks = frames, snapshot.tracks
    graph.current_frame, graph.active_layer_index = snapshot[1], snapshot[2]
    reference = object.__new__(document_module.GraphDocument)
    reference.frames, reference.tracks = expected_snapshot.frames, expected_snapshot.tracks
    reference.current_frame, reference.active_layer_index = expected_snapshot[1], expected_snapshot[2]
    assert _whole_signature(graph) == _whole_signature(reference)


def test_batch_restore_changes_content_in_sheet_order(monkeypatch):
    plain, live = _pair()
    for doc in (plain, live):
        doc.tracks[0].legacy_archive_hints[1] = ArchiveHint(has_content=False)
    slots = [tuple(frame.layers[0] for frame in doc.frames[:2]) for doc in (plain, live)]
    cells = [group[0]._cell for group in slots]
    old_images = [cell.image for cell in cells]
    old_pixels = [image.rgba().tobytes() for image in old_images]
    tokens = [tuple(slot.__dict__["_pending_token"] for slot in group) for group in slots]
    calls = []
    original = document_live.Document._run
    synced = []
    sync_selection = document_live.Document._sync_selection

    def run(self, name, args, kwargs):
        calls.append(name)
        return original(self, name, args, kwargs)

    def sync(self):
        synced.append(self)
        sync_selection(self)

    monkeypatch.setattr(document_live.Document, "_run", run)
    monkeypatch.setattr(document_live.Document, "_sync_selection", sync)
    for content, color in ((False, (8, 9, 10, 255)), (True, (20, 21, 22, 255))):
        images = [PmaImage(_filled(color)) for _ in range(2)]
        steps = [doc.restore_batch_drawing(0, 0, image, content, 1, sheet=True)
                 for doc, image in zip((plain, live), images)]
        while True:
            revision = Layer.timeline_revision
            expected = next(steps[0], None)
            delta = Layer.timeline_revision - revision
            revision = Layer.timeline_revision
            state = live._live()["state"]
            list(live.tracks[0].pending_slots)
            assert state.pending
            synced.clear()
            assert next(steps[1], None) == expected
            assert calls == []
            assert Layer.timeline_revision - revision == delta
            if delta:
                assert not state.pending
                assert synced == [live]
            assert _signature(live) == _signature(plain)
            assert _pixels(live) == _pixels(plain)
            if expected is None:
                break
        for doc, group, cell, image, held_tokens in zip((plain, live), slots, cells, images, tokens):
            assert all(doc.frames[i].layers[0] is slot for i, slot in enumerate(group))
            assert all(slot.__dict__["_pending_token"] is token
                       for slot, token in zip(group, held_tokens))
            assert doc.tracks[0].cells[1] is cell
            assert cell.image is image
            assert all((slot._cell is cell) == content for slot in group)
            assert doc.tracks[0].legacy_archive_hints[1].has_content == content
    # 番号を記録していない戻しは、共有中のバンク画像を戻して指定位置だけ印を外す。
    images = [PmaImage(_filled((30, 31, 32, 255))) for _ in range(2)]
    assert list(live.restore_batch_drawing(0, 0, images[1], False, None, sheet=True)) == list(
        plain.restore_batch_drawing(0, 0, images[0], False, None, sheet=True))
    assert calls == []
    for group, cell, image in zip(slots, cells, images):
        assert not group[0].has_content and group[1].has_content
        assert group[0]._cell is not cell and group[1]._cell is cell
        assert group[0].image is not image and group[1].image is image
    assert _signature(live) == _signature(plain)
    assert _pixels(live) == _pixels(plain)
    assert _whole_signature(live) == _whole_signature(plain)
    assert [image.rgba().tobytes() for image in old_images] == old_pixels


def test_batch_restore_rust_rejection_continues_from_the_current_position(monkeypatch):
    plain, live = _pair(hold=3)
    core_type = type(live._live()["core"])
    restore = core_type.restore_position_content
    attempts = []

    def reject_second(core, frame, layer, has_content):
        attempts.append(frame)
        if frame == 1:
            assert not live.frames[0].layers[0].has_content
            assert live.frames[1].layers[0].has_content
            assert live.tracks[0].cells[1].image is images[1]
            raise ValueError("restore_position_content rejected the second slot")
        return restore(core, frame, layer, has_content)

    monkeypatch.setattr(core_type, "restore_position_content", reject_second)
    images = [PmaImage(_filled((41, 42, 43, 255))) for _ in range(2)]
    expected, actual = [doc.restore_batch_drawing(0, 0, image, False, 1, sheet=True)
                        for doc, image in zip((plain, live), images)]
    for change in expected:
        assert next(actual) == change
        assert _signature(live) == _signature(plain)
        assert _pixels(live) == _pixels(plain)
        assert live.tracks[0].cells[1].image is images[1]
    assert next(actual, None) is None
    assert attempts == [0, 1, 2]
    assert _whole_signature(live) == _whole_signature(plain)


@pytest.mark.parametrize("kind", ["plain", "materialized", "tuple"])
def test_restore_materialized_and_plain_snapshots_without_round_trip(monkeypatch, kind):
    plain, live = _pair()
    snapshots = [doc.snapshot() for doc in (plain, live)]
    # 読み出した写しには編集もできる。捕獲時の Rust の値に戻してはいけない。
    for snapshot in snapshots:
        snapshot.frames[0].layers[0].image = PmaImage(_filled((51, 52, 53, 255)))
        snapshot.frames[0].duration = 3
    calls = _round_trips(live, monkeypatch)
    source = snapshots[1] if kind == "materialized" else snapshots[0]
    if kind == "tuple":
        source = tuple(source)[:5]
    for _ in range(2):
        old = [doc.frames[0].layers[0] for doc in (plain, live)]
        old_images = [slot.image for slot in old]
        revision = Layer.timeline_revision
        expected = plain.restore_snapshot(snapshots[0])
        delta = Layer.timeline_revision - revision
        revision = Layer.timeline_revision
        assert live.restore_snapshot(source) == expected
        assert calls == []
        assert Layer.timeline_revision - revision == delta
        assert _signature(live) == _signature(plain)
        assert _pixels(live) == _pixels(plain)
        for doc, slot, image, snapshot in zip((plain, live), old, old_images,
                                               (snapshots[0], source)):
            restored = doc.frames[0].layers[0]
            stored = snapshot[0][0].layers[0]
            assert restored is not slot and restored is not stored
            assert slot.image is image
            assert restored.image is not image and restored.image is not stored.image
            assert restored._cell is doc.frames[1].layers[0]._cell
            assert restored.__dict__["_pending_token"] is not stored.__dict__["_pending_token"]
    assert _whole_signature(live) == _whole_signature(plain)


def test_restore_snapshot_falls_back_for_content_and_blank_key(monkeypatch):
    plain, live = _pair()
    snapshot = plain.snapshot()
    snapshot.frames[0].layers[0].is_blank_key = True
    calls = _round_trips(live, monkeypatch)
    revision = Layer.timeline_revision
    expected = plain.restore_snapshot(snapshot)
    delta = Layer.timeline_revision - revision
    revision = Layer.timeline_revision
    assert live.restore_snapshot(snapshot) == expected
    assert calls
    assert Layer.timeline_revision - revision == delta
    assert live._live()["graph"] is not None
    assert _signature(live) == _signature(plain)
    assert _pixels(live) == _pixels(plain)


@pytest.mark.parametrize("shape", [
    "split", "append", "collision", "blank", "blank_split", "empty_split",
    "no_previous", "duplicate", "columns", "same_source", "pending_hold", "release_tail",
])
def test_place_pending_entries_matches_python(shape, monkeypatch, tmp_path):
    from zipfile import ZipFile

    from paintmaskanimator import constants, project_io, utils

    monkeypatch.setattr(constants, "CANVAS_WIDTH", 8)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 8)
    monkeypatch.setattr(utils, "OUTSIDE_MARGIN", 0)

    def make(cls):
        doc = cls()
        rows = []
        for column in range(8):
            row = []
            for layer in range(2):
                length = 4 if shape != "collision" else 2
                if shape in ("append", "blank", "same_source"):
                    length = 1
                number = 1 if column < length else None
                source = 1 if shape == "same_source" else 6
                pending = column == source and (layer == 0 or shape == "columns")
                pending |= shape == "duplicate" and column == 7 and layer == 0
                if pending:
                    number = 7 if shape in ("no_previous", "same_source") else 2
                if shape == "collision" and column == 2:
                    number = 3
                blank = pending and shape in ("blank", "blank_split")
                content = (number is not None and not blank
                           and not (pending and shape in ("empty_split", "same_source")))
                pending |= shape == "release_tail" and column == 7 and layer == 1
                image = PmaImage(_filled((100 + (number or 0), layer, 7, 255)))
                row.append(Layer(str(layer), image, has_content=content,
                                 is_blank_key=blank, sequence_number=number,
                                 sequence_only=pending, initial_image=image.copy()))
            rows.append(Frame(row))
        doc.install_legacy_frames(rows, canvas_size=(8, 8))
        if shape in ("collision", "pending_hold"):
            from dataclasses import replace
            from paintmaskanimator.legacy_v1 import V1Position, snapshot_layer
            from paintmaskanimator.timeline_metadata import reference_for, set_dense_import

            graph = doc._demote().plain if cls is document_live.Document else doc
            if shape == "collision":
                # 旧入力の長さで分けると次の明示キーに重なる。
                slot = graph.frames[0].layers[0]
                slot.__dict__["_v1_position"] = V1Position(
                    replace(snapshot_layer(slot), exposure=4), key_start=True)
                sheet = reference_for(slot)
            else:
                # 保持の末尾に、表示上は保持を続ける番号なしの配置待ちを置く。
                slot = graph.frames[3].layers[0]
                sheet = reference_for(slot)
                slot.has_content = False
                slot.sequence_number = None
                slot.set_pending(True)
            signature = (slot.has_content, slot.is_blank_key, slot.sequence_number,
                         slot.sequence_only, slot.blank_start)
            set_dense_import(slot, (signature, sheet, reference_for(slot, resolve=True)))
            if cls is document_live.Document:
                assert doc._promote()
        return doc

    plain, live = make(GraphDocument), make(document_live.Document)
    assert live._live()["graph"] is None
    plain.current_frame = live.current_frame = 7

    def capture(doc):
        frames = list(doc.frames)
        slots = [list(frame.layers) for frame in frames]
        return (frames, slots, [[slot._cell for slot in row] for row in slots],
                [[slot.image for slot in row] for row in slots],
                [[slot.__dict__["_pending_token"] for slot in row] for row in slots],
                [{number: (cell, cell.image, cell.initial_image)
                  for number, cell in track.cells.items()} for track in doc.tracks])

    before = [capture(doc) for doc in (plain, live)]
    # 文書外の Layer が同じ列に登録した札も残る。
    extra = [doc.frames[0].layers[0].clone_slot() for doc in (plain, live)]
    for doc, slot in zip((plain, live), extra):
        slot.attach_track(doc.tracks[0])
        slot.set_pending(True)

    def forbidden(*args, **kwargs):
        pytest.fail("place_pending_entries が _run を呼んだ")

    monkeypatch.setattr(document_live.Document, "_run", forbidden)
    revision = Layer.timeline_revision
    expected = plain.place_pending_entries()
    delta = Layer.timeline_revision - revision
    revision = Layer.timeline_revision
    assert live.place_pending_entries() == expected
    assert Layer.timeline_revision - revision == delta
    assert live.current_frame == plain.current_frame == 7
    assert _signature(live) == _signature(plain)
    assert _pixels(live) == _pixels(plain)

    def ownership(doc, saved, external):
        frames, slots, cells, images, tokens, banks = saved
        result = []
        for column, frame in enumerate(frames):
            present = column < len(doc.frames)
            result.append((present and doc.frames[column] is frame, [
                (frame.layers[layer] is slot, slot._cell is cells[column][layer],
                 slot.image is images[column][layer],
                 slot.__dict__["_pending_token"] is tokens[column][layer],
                 tokens[column][layer] in doc.tracks[layer].pending_slots)
                for layer, slot in enumerate(slots[column])
            ]))
        for track, bank in zip(doc.tracks, banks):
            for number, (cell, image, initial) in bank.items():
                assert track.cells[number] is cell
                assert cell.image is image
                assert cell.initial_image is initial
        assert external.__dict__["_pending_token"] in doc.tracks[0].pending_slots
        return result

    assert ownership(live, before[1], extra[1]) == ownership(plain, before[0], extra[0])
    if shape in ("blank_split", "empty_split"):
        for doc in (plain, live):
            head, held = (doc.frames[column].layers[0] for column in (2, 3))
            assert head._cell is held._cell
            assert head.image is held.image
            assert head.__dict__["_pending_token"] is not held.__dict__["_pending_token"]

    def save(doc, name):
        path = tmp_path / f"{name}.pman"
        project_io.write_project_snapshot(path, project_io.ProjectSnapshot.capture({}, doc))
        with ZipFile(path) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    assert save(live, "live") == save(plain, "plain")


def test_place_pending_entries_keeps_shared_local_artwork_on_fallback(monkeypatch):
    plain, live = _pair(hold=4)
    for doc in (plain, live):
        doc.insert_sequence_blank(0, 1)
        graph = doc._demote().plain if doc is live else doc
        source = graph.frames[-1].layers[0]
        source.__dict__["_cell"] = graph.frames[5].layers[1]._cell
    assert live._promote()
    calls = _round_trips(live, monkeypatch)
    assert live.place_pending_entries() == plain.place_pending_entries()
    assert calls == [True]
    assert _signature(live) == _signature(plain)
    assert _pixels(live) == _pixels(plain)


@pytest.mark.parametrize("input_kind", ["view", "list", "tuple"])
@pytest.mark.parametrize("pending", [False, True])
def test_install_current_legacy_frames(monkeypatch, tmp_path, input_kind, pending):
    from zipfile import ZipFile

    from paintmaskanimator import constants, project_io, utils

    monkeypatch.setattr(constants, "CANVAS_WIDTH", 8)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 8)
    monkeypatch.setattr(utils, "OUTSIDE_MARGIN", 0)
    plain, live = _pair()
    saved = []
    for document in (plain, live):
        frames, tracks = document.frames, document.tracks
        frame = frames[0]
        layers = frame.layers
        slot = layers[0]
        slot.set_pending(pending)
        token = slot.__dict__["_pending_token"]
        saved.append((frames, tracks, frame, layers, slot, slot._cell, slot.image, token))

    def unexpected(*args, **kwargs):
        pytest.fail("install_legacy_frames called _run")

    monkeypatch.setattr(document_live.Document, "_run", unexpected)
    revisions = []
    for document, (frames, tracks, frame, layers, slot, cell, image, token) in zip(
            (plain, live), saved):
        source = frames if input_kind == "view" else {"list": list, "tuple": tuple}[input_kind](frames)
        before = Layer.timeline_revision
        assert document.install_legacy_frames(source, {}, canvas_size=(9, 11)) is None
        revisions.append(Layer.timeline_revision - before)
        assert document.frames is not frames and document.tracks is not tracks
        assert frames[0] is frame and document.frames[0] is not frame
        assert list(frame.layers) == list(layers) and document.frames[0].layers is not layers
        assert document.frames[0].layers[0] is slot and document.tracks[0] is tracks[0]
        assert slot._cell is cell and slot.image is image
        assert slot.__dict__["_pending_token"] is token
        assert (token in tracks[0].pending_slots) is pending

    assert revisions == [int(pending)] * 2
    assert _signature(live) == _signature(plain)
    assert _pixels(live) == _pixels(plain)

    def save(doc, name):
        path = tmp_path / f"{name}.pman"
        project_io.write_project_snapshot(path, project_io.ProjectSnapshot.capture({}, doc))
        with ZipFile(path) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    assert save(live, "live") == save(plain, "plain")


@pytest.mark.parametrize("shape", ["numberless", "retained", "detached_pending", "shadow"])
def test_install_current_legacy_frames_normalizes_positions(shape, monkeypatch, tmp_path):
    from zipfile import ZipFile

    from paintmaskanimator import constants, project_io, utils
    from paintmaskanimator.models import Cell

    monkeypatch.setattr(constants, "CANVAS_WIDTH", 8)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 8)
    monkeypatch.setattr(utils, "OUTSIDE_MARGIN", 0)
    plain, live = _pair()
    for document in (plain, live._demote().plain):
        slot = document.frames[0].layers[0]
        if shape in ("numberless", "retained"):
            slot.has_content = False
            slot.sequence_number = None if shape == "numberless" else 9
            if shape == "numberless":
                slot.has_content = True
            slot.image = PmaImage(_filled((11, 22, 33, 255)))
            slot.initial_image = PmaImage(_filled((44, 55, 66, 255)))
            slot.cell_name = shape
        elif shape == "detached_pending":
            clone = slot.clone_slot()
            clone.set_pending(True)
            document.frames[0].layers[0] = clone
        else:
            slot.__dict__["_cell"] = Cell(PmaImage(_filled((77, 88, 99, 255))))
    assert live._promote()

    def forbidden(*args, **kwargs):
        pytest.fail("install_legacy_frames が _run を呼んだ")

    monkeypatch.setattr(document_live.Document, "_run", forbidden)
    saved = []
    for document in (plain, live):
        slot = document.frames[0].layers[0]
        track = slot._track
        token = slot.__dict__["_pending_token"]
        cell, image = slot._cell, slot.image
        shadow = slot.__dict__.get("_cell")
        shadow_image = None if shadow is None else shadow.image
        before = Layer.timeline_revision
        assert document.install_legacy_frames(document.frames) is None
        assert Layer.timeline_revision - before == int(shape in ("numberless", "detached_pending"))
        assert document.frames[0].layers[0] is slot
        assert slot._track is track and slot._cell is cell and slot.image is image
        assert slot.__dict__["_pending_token"] is token
        assert (token in track.pending_slots) == (shape == "detached_pending")
        if shape == "numberless":
            assert track.cells[slot.sequence_number] is cell
        elif shape == "retained":
            assert track.cells[9] is not cell and track.cells[9].image is not image
            assert np.array_equal(track.cells[9].image.rgba(), image.rgba())
        elif shape == "shadow":
            assert "_cell" not in slot.__dict__
            assert shadow.image is shadow_image
            assert tuple(shadow_image.rgba()[0, 0]) == (77, 88, 99, 255)
        path = tmp_path / f"{type(document).__module__}.pman"
        project_io.write_project_snapshot(path, project_io.ProjectSnapshot.capture({}, document))
        with ZipFile(path) as archive:
            saved.append({name: archive.read(name) for name in archive.namelist()})
    assert saved[0] == saved[1]
    assert _signature(live) == _signature(plain)
    assert _pixels(live) == _pixels(plain)


def test_install_detached_legacy_frames_without_materializing_old_document(monkeypatch, tmp_path):
    from zipfile import ZipFile

    from paintmaskanimator import constants, project_io, utils

    monkeypatch.setattr(constants, "CANVAS_WIDTH", 8)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 8)
    monkeypatch.setattr(utils, "OUTSIDE_MARGIN", 0)
    plain, live = _pair()

    def forbidden(*args, **kwargs):
        pytest.fail("install_legacy_frames が旧文書を書き出した")

    monkeypatch.setattr(document_live.Document, "_run", forbidden)
    monkeypatch.setattr(document_live.Document, "_materialize", forbidden)
    archives = []
    revisions = []
    for document in (plain, live):
        old_frames, old_tracks = document.frames, document.tracks
        old_frame, old_track = old_frames[0], old_tracks[0]
        old_slot, old_image = old_frame.layers[0], old_frame.layers[0].image
        image = PmaImage(_filled((13, 42, 71, 255)))
        slot = Layer("C", image, has_content=True, sequence_only=True)
        slot.set_pending(True)
        pending_token = slot.__dict__["_pending_token"]
        local = slot._cell
        row = Frame([slot], 3)
        before = Layer.timeline_revision
        assert document.install_legacy_frames([row]) is None
        revisions.append(Layer.timeline_revision - before)
        assert document.frames[0] is not row and document.frames[0].duration == 3
        assert document.frames[0].layers[0] is slot and document.tracks[0] is slot._track
        assert slot.image is image and slot._cell is local
        assert slot.__dict__["_pending_token"] is pending_token
        assert pending_token in slot._track.pending_slots
        assert old_frames[0] is old_frame and old_tracks[0] is old_track
        assert old_frame.layers[0] is old_slot and old_slot.image is old_image
        assert old_slot._track is old_track and old_track is not slot._track
        path = tmp_path / f"{type(document).__module__}.pman"
        project_io.write_project_snapshot(path, project_io.ProjectSnapshot.capture({}, document))
        with ZipFile(path) as archive:
            archives.append({name: archive.read(name) for name in archive.namelist()})
    assert revisions == [2, 2]
    assert _signature(live) == _signature(plain)
    assert _pixels(live) == _pixels(plain)
    assert archives[0] == archives[1]


@pytest.mark.parametrize("operation", [
    "plain_noop", "shared_noop", "bool_noop", "float_noop", "extend", "advance",
])
def test_ensure_frame_count_matches_python(operation, monkeypatch, tmp_path):
    from zipfile import ZipFile

    from paintmaskanimator import constants, project_io, utils

    monkeypatch.setattr(constants, "CANVAS_WIDTH", 8)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 8)
    monkeypatch.setattr(utils, "OUTSIDE_MARGIN", 0)
    plain, live = _pair()
    if operation in ("plain_noop", "shared_noop"):
        # 直接編集で素の状態にいるが、従来経路と同じく再接続できる。
        live._demote()

    def unexpected(*args, **kwargs):
        pytest.fail("ensure_frame_count called _run")

    revisions = []
    results = []
    for document in (plain, live):
        document.current_frame = len(document.frames) - 1
        frames, tracks = document.frames, document.tracks
        waiting = frames[0].layers[0]
        waiting.set_pending(True)
        waiting.image.write_rgba(np.array([[[80, 40, 20, 255]]], dtype=np.uint8))
        token = waiting.__dict__["_pending_token"]
        if operation == "shared_noop":
            # バンクの画像を別の位置の局所の絵が共有するため、再接続はできない。
            frames[2].layers[1].image = waiting.image
        if document is live and operation in ("extend", "advance"):
            assert live._live()["graph"] is None
        old = [(frame, [(slot, slot._cell, slot.image, slot.__dict__["_pending_token"])
                        for slot in frame.layers]) for frame in frames]
        track_objects = list(tracks)
        with monkeypatch.context() as guard:
            guard.setattr(document_live.Document, "_run", unexpected)
            before = Layer.timeline_revision
            if operation == "advance":
                results.append(document.advance_frame())
            else:
                counts = {"bool_noop": True, "float_noop": len(old) - 0.5, "extend": len(old) + 2}
                results.append(document.ensure_frame_count(counts.get(operation, len(old))))
            revisions.append(Layer.timeline_revision - before)

        assert document.frames is frames and document.tracks is tracks
        for index, track in enumerate(track_objects):
            assert document.tracks[index] is track
        for column, (frame, slots) in enumerate(old):
            assert document.frames[column] is frame
            for index, (slot, cell, image, old_token) in enumerate(slots):
                assert frame.layers[index] is slot
                assert slot._cell is cell and slot.image is image
                assert slot.__dict__["_pending_token"] is old_token
        assert token in tracks[0].pending_slots
        cells, images, tokens = set(), set(), {id(token)}
        # 窓口の弱参照キャッシュから消えないよう、新しいオブジェクトも保持する。
        new_objects = []
        for frame in frames[len(old):]:
            assert frame.duration == 1
            for slot, track in zip(frame.layers, tracks):
                cell, image = slot._cell, slot.image
                new_token = slot.__dict__["_pending_token"]
                assert slot._track is track and new_token not in track.pending_slots
                assert id(cell) not in cells and id(image) not in images and id(new_token) not in tokens
                cells.add(id(cell))
                images.add(id(image))
                tokens.add(id(new_token))
                new_objects.append((cell, image, new_token))

    assert (live._live()["graph"] is not None) is (operation == "shared_noop")
    assert results[0] == results[1]
    assert revisions == [0, 0]
    assert _signature(live) == _signature(plain)
    assert _pixels(live) == _pixels(plain)

    def save(doc, name):
        path = tmp_path / f"{name}.pman"
        project_io.write_project_snapshot(path, project_io.ProjectSnapshot.capture({}, doc))
        with ZipFile(path) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    assert save(live, "live") == save(plain, "plain")
