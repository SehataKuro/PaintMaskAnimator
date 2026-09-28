"""あとから直せるトゥイーン：確定・編集し直し・キャンセル・解除・保存。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from paintmaskanimator import constants, tween_groups  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.models import make_frame  # noqa: E402
from paintmaskanimator.project_io import (  # noqa: E402
    read_project_archive,
    write_project_archive,
)
from paintmaskanimator.timeline import TimelineCellDelegate  # noqa: E402

LENGTH = 6


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok),
    )
    window = MainWindow()
    canvas = window.canvas
    names = [layer.name for layer in canvas.frames[0].layers]
    canvas.frames = [make_frame(names) for _ in range(10)]
    key = canvas.frames[0].layers[0]
    key.has_content = True
    key.sequence_number = 1
    key.exposure = LENGTH
    painter = QPainter(key.image)
    painter.setPen(QPen(QColor("black"), 8))
    painter.drawEllipse(300, 200, 200, 200)
    painter.end()
    # 後ろにもう 1 枚（番号 2）。中割りの番号はこの後ろに足される。
    other = canvas.frames[8].layers[0]
    other.has_content = True
    other.sequence_number = 2
    other.exposure = 2
    canvas.current_frame = 0
    window.refresh_ui()
    yield window
    if canvas.transform_active:
        window.tween.cancel_transform_or_tween()
    window.close()
    window.deleteLater()
    qapp.processEvents()


def _row(window):
    return window.timeline.table.rowCount() - 1


def _commit(window, dx=80.0, dy=30.0):
    canvas = window.canvas
    canvas.transform_points = [
        QPointF(point.x() + dx, point.y() + dy) for point in canvas.transform_points
    ]
    window.tween.commit_transform_or_tween()
    window.refresh_ui()


def _layers(window):
    return [frame.layers[0] for frame in window.canvas.frames[:LENGTH]]


def _start_and_commit(window):
    window.tween.enable(_row(window), 0, "free")
    assert window.canvas.tween_pending
    _commit(window)


def test_commit_keeps_the_tween_as_one_span(window):
    _start_and_commit(window)
    key, *members = _layers(window)
    spec = key.tween
    assert spec and spec["length"] == LENGTH and spec["mode"] == "free"
    assert all(member.tween_member == spec["id"] for member in members)
    # 中割りは番号付きの通常の絵（書き出し用）。番号は末尾に足される。
    assert key.sequence_number == 1
    assert [member.sequence_number for member in members] == [3, 4, 5, 6, 7]
    assert tween_groups.group_length(window.canvas.frames, 0, 0) == LENGTH

    table = window.timeline.table
    row = _row(window)
    for column in range(LENGTH):
        item = table.item(row, column)
        assert item.data(TimelineCellDelegate.TWEEN_GROUP_ROLE)
        assert item.data(TimelineCellDelegate.TWEEN_SPAN_ROLE) == "forward"
        assert item.data(Qt.ItemDataRole.UserRole) == 0  # 区間の先頭
    assert table.item(row, 0).data(TimelineCellDelegate.LABEL_ROLE) == "1"
    assert not table.item(row, 6).data(TimelineCellDelegate.TWEEN_GROUP_ROLE)
    assert window.canvas.tween_pending is None


def test_edit_starts_from_the_saved_shape_and_cancel_restores(window):
    _start_and_commit(window)
    before_images = [layer.image.copy() for layer in _layers(window)]
    spec = dict(_layers(window)[0].tween)

    window.tween.edit(_row(window), 0)
    canvas = window.canvas
    assert canvas.transform_active and canvas.tween_pending
    assert canvas.tween_pending["reedit"]["numbers"] == [3, 4, 5, 6, 7]
    assert canvas.frames[0].layers[0].exposure == LENGTH
    assert not canvas.frames[1].layers[0].has_content
    saved_final = tween_groups.points_from_data(spec["final_points"])
    assert [
        (round(p.x(), 3), round(p.y(), 3)) for p in canvas.transform_points
    ] == [(round(p.x(), 3), round(p.y(), 3)) for p in saved_final]

    window.tween.cancel_transform_or_tween()
    window.refresh_ui()
    layers = _layers(window)
    assert tween_groups.group_length(window.canvas.frames, 0, 0) == LENGTH
    assert [layer.sequence_number for layer in layers] == [1, 3, 4, 5, 6, 7]
    assert all(
        layer.image == image for layer, image in zip(layers, before_images)
    )


def test_edit_and_recommit_keeps_id_and_numbers(window):
    _start_and_commit(window)
    first_id = _layers(window)[0].tween["id"]
    last_before = _layers(window)[-1].image.copy()

    window.tween.edit(_row(window), 0)
    _commit(window, dx=40.0, dy=-20.0)
    layers = _layers(window)
    assert layers[0].tween["id"] == first_id
    assert [layer.sequence_number for layer in layers] == [1, 3, 4, 5, 6, 7]
    assert layers[-1].image != last_before
    # Undo で直す前の確定済みトゥイーンへ戻る。
    window.canvas.undo()
    assert tween_groups.group_length(window.canvas.frames, 0, 0) == LENGTH


def test_release_turns_members_into_plain_cells(window):
    _start_and_commit(window)
    window.tween.release(_row(window), 0)
    window.refresh_ui()
    layers = _layers(window)
    assert layers[0].tween is None
    assert all(layer.tween_member is None for layer in layers[1:])
    assert all(layer.has_content and layer.exposure == 1 for layer in layers)
    item = window.timeline.table.item(_row(window), 2)
    assert not item.data(TimelineCellDelegate.TWEEN_GROUP_ROLE)


def test_moving_a_member_breaks_the_span_gracefully(window):
    _start_and_commit(window)
    member = window.canvas.frames[3].layers[0]
    member.tween_member = None
    assert tween_groups.group_length(window.canvas.frames, 0, 0) is None
    window.refresh_ui()
    item = window.timeline.table.item(_row(window), 1)
    assert not item.data(TimelineCellDelegate.TWEEN_GROUP_ROLE)


def test_project_roundtrip_keeps_the_tween(window, tmp_path):
    _start_and_commit(window)
    metadata = {
        "format": "PaintMaskAnimatorProject",
        "canvas": {"width": constants.CANVAS_WIDTH, "height": constants.CANVAS_HEIGHT},
        "frames": [],
    }
    path = tmp_path / "tween.pmap"
    write_project_archive(path, metadata, window.canvas.frames)
    _metadata, frames, _w, _h = read_project_archive(path)
    assert tween_groups.group_length(frames, 0, 0) == LENGTH
    assert frames[0].layers[0].tween == window.canvas.frames[0].layers[0].tween


def test_invalid_spec_is_ignored():
    assert tween_groups.sanitize_spec({"id": "x", "length": 1}) is None
    assert tween_groups.sanitize_spec({"id": "x", "length": 3, "final_points": "bad"}) is None
    assert tween_groups.sanitize_spec("nope") is None
    spec = tween_groups.sanitize_spec({"id": "x", "length": 3})
    assert spec is not None and spec["mode"] == "free"
