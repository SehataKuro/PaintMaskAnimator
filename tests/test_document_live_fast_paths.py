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
