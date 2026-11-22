"""Rust 正本の文書（document_live）の近道が、今の Python の手順と同じ結果になること（段階8-4 C4a）。

近道：resolve を Rust で一度に求める、読むだけの操作を窓口のまま当てる、同じ値の代入を省く、
列の表示設定は列だけ Rust へ書く、窓口のまま試して素のグラフが要る書き込みでやり直す。
"""
import numpy as np
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
    # 変わる代入は今の経路（全体往復）で当てる。
    assert live.set_drawing_content(0, 1, False) == plain.set_drawing_content(0, 1, False)
    assert calls
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
                        ({"hold": 3, "count": 9}, (6, 0, 1, 0))):
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
