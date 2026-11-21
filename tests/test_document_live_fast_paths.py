"""Rust 正本の文書（document_live）の近道が、今の Python の手順と同じ結果になること（段階8-4 C4a）。

近道：resolve を Rust で一度に求める、読むだけの操作を窓口のまま当てる、同じ値の代入を省く、
列の表示設定は列だけ Rust へ書く、窓口のまま試して素のグラフが要る書き込みでやり直す。
"""
import numpy as np
from PySide6.QtGui import QColor, QImage

from paintmaskanimator import document_live
from paintmaskanimator.document import GraphDocument
from paintmaskanimator.models import Frame, Layer
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
