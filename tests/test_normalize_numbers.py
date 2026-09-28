"""番号の正規化：対応表の計算・確認画面・ツールバーのバッジ。"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.cell_numbering import (  # noqa: E402
    changed_count,
    normalization_mapping,
)
from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.models import make_frame  # noqa: E402
from paintmaskanimator.normalize_dialog import NormalizeNumbersDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _fake_frames(numbers):
    """1 レイヤーの各コマに番号を置いた軽い代用品（None は空きコマ）。"""
    return [
        SimpleNamespace(layers=[SimpleNamespace(
            sequence_number=number,
            has_content=number is not None,
            sequence_only=False,
        )])
        for number in numbers
    ]


def test_mapping_follows_first_appearance_and_keeps_reuse():
    # 1 2 [6 を足した] 3 4 2(使い回し) 5
    frames = _fake_frames([1, 2, 6, 3, 4, 2, 5])
    mapping = normalization_mapping(frames, {}, 0)
    assert mapping == {1: 1, 2: 2, 6: 3, 3: 4, 4: 5, 5: 6}
    assert changed_count(mapping) == 4


def test_unused_and_archived_numbers_go_last():
    frames = _fake_frames([2, 1])
    mapping = normalization_mapping(frames, {(0, 3): None, (1, 9): None}, 0)
    assert mapping == {2: 1, 1: 2, 3: 3}


def test_dialog_scopes_and_counts(qapp):
    layers = [
        (1, "B", {1: 1, 3: 2, 2: 3}),
        (0, "A", {1: 1, 2: 2}),
    ]
    dialog = NormalizeNumbersDialog(layers, selected={0}, parent=None)
    try:
        # 選択レイヤーに変更がないので、すべてのレイヤーが最初から選ばれる。
        assert dialog.scope_all.isChecked()
        assert dialog.target_layer_indices() == [1]
        assert dialog.change_count() == 2
        assert dialog.apply_button.isEnabled()
        dialog.scope_selected.setChecked(True)
        assert dialog.target_layer_indices() == []
        assert not dialog.apply_button.isEnabled()
        # 撮影済みカットへの注意は必ず出す。
        assert dialog.caution is not None
    finally:
        dialog.deleteLater()


def _out_of_order_window():
    window = MainWindow()
    canvas = window.canvas
    name = canvas.frames[0].layers[0].name
    canvas.frames = [make_frame([name]) for _ in range(6)]
    # コマ 0-1: 1、コマ 2: 3（後から足した絵）、コマ 3-5: 2
    for column, number, exposure in ((0, 1, 2), (2, 3, 1), (3, 2, 3)):
        layer = canvas.frames[column].layers[0]
        layer.has_content = True
        layer.sequence_number = number
        layer.exposure = exposure
    canvas.current_frame = 0
    window.refresh_ui()
    return window


def test_toolbar_badge_shows_out_of_order_count(qapp):
    window = _out_of_order_window()
    try:
        timeline = window.timeline
        assert timeline._normalize_pending == 2
        assert timeline.normalize_badge.text() == "2"
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_normalize_applies_after_confirmation(qapp, monkeypatch):
    window = _out_of_order_window()
    try:
        monkeypatch.setattr(
            NormalizeNumbersDialog, "exec",
            lambda self: NormalizeNumbersDialog.DialogCode.Accepted,
        )
        window.timeline_ops.normalize_numbers((0,))
        numbers = [
            frame.layers[0].sequence_number
            for frame in window.canvas.frames
            if frame.layers[0].has_content
        ]
        assert numbers == [1, 2, 3]
        window.refresh_ui()
        assert window.timeline._normalize_pending == 0
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_normalize_cancel_changes_nothing(qapp, monkeypatch):
    window = _out_of_order_window()
    try:
        monkeypatch.setattr(
            NormalizeNumbersDialog, "exec",
            lambda self: NormalizeNumbersDialog.DialogCode.Rejected,
        )
        window.timeline_ops.normalize_numbers((0,))
        assert window.canvas.frames[2].layers[0].sequence_number == 3
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
