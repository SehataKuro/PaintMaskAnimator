"""一括処理ランナーへ移行した操作の結合テスト（Issue #9）。

ゴミ取り（``remove_dust_fill_surrounding``）と色置換（``apply_palette_replacements``）
は、各自で全コマ走査・進捗・Undo を組み立てていたものを ``frame_scope`` の
ランナー経由に置き換えた。移行前と同じ結果になること、そして全コマ適用が
Undo 1回で戻ることをここで固定する。
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.undo_entries import ScopedBatchUndo


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def paint_cell(window, frame_index, color, *, size=3, origin=(10, 10)):
    """コマの左上付近を白地の上から塗り、キーフレーム化する。"""
    layer = window.canvas.frames[frame_index].layers[0]
    layer.image.fill(QColor("white"))
    x0, y0 = origin
    for y in range(y0, y0 + size):
        for x in range(x0, x0 + size):
            layer.image.setPixelColor(x, y, QColor(color))
    layer.has_content = True
    return layer


def add_frames(window, count):
    window.canvas._ensure_frame_count(count)


def test_color_replacement_applies_to_every_frame_and_undoes_once(window):
    add_frames(window, 3)
    for frame_index in range(3):
        paint_cell(window, frame_index, "#ff0000")
    before = [
        frame.layers[0].image.copy() for frame in window.canvas.frames
    ]

    assert window.used_color.apply_palette_replacements({(255, 0, 0): (0, 0, 255)})

    for frame in window.canvas.frames:
        assert frame.layers[0].image.pixelColor(10, 10) == QColor("#0000ff")

    entry = window.canvas.undo_stack[-1]
    assert isinstance(entry, ScopedBatchUndo)
    assert len(entry.cells) == 3

    window.canvas.undo()
    for frame, original in zip(window.canvas.frames, before):
        assert frame.layers[0].image == original


def test_color_replacement_reports_no_match_without_touching_the_stack(window):
    paint_cell(window, 0, "#ff0000")
    depth = len(window.canvas.undo_stack)
    assert not window.used_color.apply_palette_replacements({(1, 2, 3): (4, 5, 6)})
    assert len(window.canvas.undo_stack) == depth


def test_dust_removal_over_all_frames_is_one_undo_entry(window):
    add_frames(window, 3)
    for frame_index in range(3):
        # 白地に浮いた 2x2 の孤立塊。ゴミ取りの対象サイズ内に収める。
        paint_cell(window, frame_index, "#000000", size=2, origin=(20, 20))
    before = [
        frame.layers[0].image.copy() for frame in window.canvas.frames
    ]

    window.tools.dust_mode.setCurrentIndex(
        window.tools.dust_mode.findData("despeckle")
    )
    window.tools.dust_size.setValue(16)
    window.tools.dust_selected_only.setChecked(False)
    window.tools.dust_all_frames.setChecked(True)

    window.line_ops.remove_dust_fill_surrounding()

    for frame in window.canvas.frames:
        assert frame.layers[0].image.pixelColor(20, 20) == QColor("white")

    entry = window.canvas.undo_stack[-1]
    assert isinstance(entry, ScopedBatchUndo)
    assert len(entry.cells) == 3
    assert entry.history_label == "ゴミ取り"

    window.canvas.undo()
    for frame, original in zip(window.canvas.frames, before):
        assert frame.layers[0].image == original


def test_dust_removal_without_all_frames_touches_only_the_current_cell(window):
    add_frames(window, 2)
    for frame_index in range(2):
        paint_cell(window, frame_index, "#000000", size=2, origin=(20, 20))

    window.tools.dust_mode.setCurrentIndex(
        window.tools.dust_mode.findData("despeckle")
    )
    window.tools.dust_size.setValue(16)
    window.tools.dust_selected_only.setChecked(False)
    window.tools.dust_all_frames.setChecked(False)
    window.canvas.current_frame = 0

    window.line_ops.remove_dust_fill_surrounding()

    assert window.canvas.frames[0].layers[0].image.pixelColor(20, 20) == QColor(
        "white"
    )
    assert window.canvas.frames[1].layers[0].image.pixelColor(20, 20) == QColor(
        "#000000"
    )


def test_every_palette_operation_has_a_label(qapp):
    """Each caller's `operation=` must be a key of OPERATION_LABELS.

    The label lookup is unconditional, so an unknown key raises KeyError before
    anything else runs -- and the parent/child merge path had exactly that gap
    when the labels were split from the identifiers.
    """
    import ast
    import pathlib

    from paintmaskanimator.main_window_used_color import UsedColorController

    used = set()
    for path in pathlib.Path("paintmaskanimator").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", "") != "apply_palette_replacements":
                continue
            for keyword in node.keywords:
                if keyword.arg == "operation" and isinstance(keyword.value, ast.Constant):
                    used.add(keyword.value.value)

    assert used, "no apply_palette_replacements(operation=...) call sites found"
    assert used <= set(UsedColorController.OPERATION_LABELS)
