"""Issue #31 Phase 4: frame/layer Undo の現状を固定する（理想仕様ではない）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import constants  # noqa: E402
from paintmaskanimator.frame_scope import FrameScope, apply_over_scope  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.models import make_frame  # noqa: E402
from paintmaskanimator.undo_entries import (  # noqa: E402
    LayerBatchUndo, LayerRegionUndo, LayerTilesUndo, LayerUndo,
    ScopedBatchUndo, history_label_for,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 16)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 16)
    window = MainWindow()
    canvas = window.canvas
    canvas.frames = [make_frame(["A", "B"]) for _ in range(4)]
    canvas.timeline_mode = "sheet"
    canvas.current_frame = 0
    canvas.active_layer_index = 0
    canvas.undo_stack.clear()
    canvas.redo_stack.clear()
    try:
        yield window
    finally:
        window.close()
        window.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def put(canvas, frame, number, color="red", exposure=1):
    layer = canvas.frames[frame].layers[0]
    layer.sequence_number = number
    layer.exposure = exposure
    layer.has_content = True
    layer.image.fill(QColor(color))
    return layer


def state(canvas):
    # 未使用／保持セルの画素も省略せず、対象レイヤーの全コマを比較する。
    return ([
        (layer.sequence_number, layer.exposure, layer.has_content,
         layer.image.pixelColor(5, 5).name(QColor.NameFormat.HexArgb))
        for frame in canvas.frames for layer in [frame.layers[0]]
    ], canvas.current_frame, canvas.active_layer_index,
        len(canvas.undo_stack), len(canvas.redo_stack))


def paint(canvas, route):
    canvas.ensure_editable_key()
    canvas.main_color = QColor("blue")
    if route == "tiles":
        canvas.pen_size = 3
        canvas._begin_opaque_brush_stroke()
        canvas.draw_line(QPoint(5, 5), QPoint(5, 5), 1.0)
        canvas._finish_opaque_brush_stroke()
        kind = LayerTilesUndo
    elif route == "region":
        canvas.flood_fill(QPoint(5, 5))
        kind = LayerRegionUndo
    else:
        canvas.push_layer_undo()
        canvas.active_layer.image.setPixelColor(5, 5, QColor("blue"))
        kind = LayerUndo
    assert isinstance(canvas.undo_stack[-1], kind)
    # Phase 4 第3段階で (layer, 番号) 単位へ。
    assert history_label_for(canvas.undo_stack[-1]) == "描画（A の 1番）"


def record(canvas, states):
    states.append(state(canvas))


# 以下は現行実装を実行して得た全状態。色名は代表画素 (5, 5) の ARGB。
E = (None, 1, False, "#00000000")
R = (1, 1, True, "#ffff0000")
B = (1, 1, True, "#ff0000ff")
G = (2, 1, True, "#ff008000")
T = (1, 1, True, "#00000000")
RH = (1, 3, True, "#ffff0000")
BH = (1, 3, True, "#ff0000ff")
RS = (1, 2, True, "#ffff0000")
BS = (1, 2, True, "#ff0000ff")

# (全コマのセルと画素, current_frame, active_layer_index, Undo件数, Redo件数)
REPEATED = [
    ([R, E, E, R], 0, 0, 0, 0),
    ([B, E, E, B], 0, 0, 1, 0),
    ([R, E, E, R], 0, 0, 0, 1),
    ([B, E, E, B], 0, 0, 1, 0),
]
HELD = [
    ([RH, E, E, G], 2, 0, 0, 0),
    ([BH, E, E, G], 0, 0, 1, 0),
    ([RH, E, E, G], 0, 0, 0, 1),
    ([BH, E, E, G], 0, 0, 1, 0),
]
INSERT = [
    ([E, R, E, G], 1, 0, 0, 0),
    ([E, B, E, G], 1, 0, 1, 0),
    ([E, E, B, E, G], 1, 0, 2, 0),
    ([E, B, E, G], 1, 0, 1, 1),
    ([E, R, E, G], 1, 0, 0, 2),
    ([E, R, E, G], 1, 0, 0, 2),
    ([E, B, E, G], 1, 0, 1, 1),
    ([E, E, B, E, G], 1, 0, 2, 0),
]
DELETE = [
    ([E, R, E, G], 1, 0, 0, 0),
    ([E, B, E, G], 1, 0, 1, 0),
    ([B, E, G, E], 0, 0, 2, 0),
    ([E, B, E, G], 1, 0, 1, 1),
    ([E, R, E, G], 1, 0, 0, 2),
    ([E, R, E, G], 1, 0, 0, 2),
    ([E, B, E, G], 1, 0, 1, 1),
    ([B, E, G, E], 0, 0, 2, 0),
]
UNNUMBERED = [
    ([E, E, E, E], 2, 0, 0, 0),
    ([E, E, B, E], 2, 0, 1, 0),
    ([E, E, T, E], 2, 0, 0, 1),
    ([E, E, B, E], 2, 0, 1, 0),
]
SCOPED = [
    ([RS, E, E, R], 2, 0, 0, 0),
    ([BS, E, E, B], 2, 0, 1, 0),
    ([RS, E, E, R], 2, 0, 0, 1),
    ([BS, E, E, B], 2, 0, 1, 0),
]


@pytest.mark.parametrize("route", ["tiles", "region", "full"])
def test_repeated_number(window, route):
    canvas = window.canvas
    put(canvas, 0, 1)
    put(canvas, 3, 1)
    canvas.install_legacy_frames()
    states = [state(canvas)]
    paint(canvas, route)
    record(canvas, states)
    canvas.current_frame = 2
    canvas.active_layer_index = 1
    canvas.undo()
    record(canvas, states)
    canvas.redo()
    record(canvas, states)
    assert states == REPEATED


@pytest.mark.parametrize("route", ["tiles", "region"])
def test_held_cell(window, route):
    canvas = window.canvas
    put(canvas, 0, 1, exposure=3)
    put(canvas, 3, 2, "green")
    canvas.current_frame = 2
    states = [state(canvas)]
    paint(canvas, route)
    assert (canvas.undo_stack[-1].frame, canvas.undo_stack[-1].layer) == (0, 0)
    record(canvas, states)
    canvas.current_frame = 3
    canvas.active_layer_index = 1
    canvas.undo()
    record(canvas, states)
    canvas.redo()
    record(canvas, states)
    assert states == HELD


@pytest.mark.parametrize("operation", ["insert", "delete"])
@pytest.mark.parametrize("route", ["tiles", "region"])
def test_sheet_edit_after_drawing(window, operation, route):
    canvas = window.canvas
    put(canvas, 1, 1)
    put(canvas, 3, 2, "green")
    canvas.current_frame = 1
    states = [state(canvas)]
    paint(canvas, route)
    record(canvas, states)
    method = (window.timeline_ops.insert_frame_at if operation == "insert"
              else window.timeline_ops.delete_frame_at)
    assert method(1, 0)  # A は2レイヤーの下段。
    assert history_label_for(canvas.undo_stack[-1]) == "全体編集"
    record(canvas, states)
    for _ in range(3):
        canvas.undo()  # シート → 描画 → 空スタック。
        record(canvas, states)
    for _ in range(2):
        canvas.redo()
        record(canvas, states)
    # 実測: シートの DocUndo を先に戻すので、描画履歴は元のコマへ適用。
    # 番号の違う絵への誤適用も、描画履歴の破棄も、この経路では起きない。
    assert states == (INSERT if operation == "insert" else DELETE)


def test_unnumbered_cell(window):
    canvas = window.canvas
    canvas.current_frame = 2
    states = [state(canvas)]
    paint(canvas, "region")
    record(canvas, states)
    canvas.undo()
    record(canvas, states)
    canvas.redo()
    record(canvas, states)
    # 実測: Undo が保存するのは ensure_editable_key 後。番号と内容フラグは残る。
    assert states == UNNUMBERED


def test_scoped_batch_repeated_number(window):
    canvas = window.canvas
    put(canvas, 0, 1, exposure=2)
    put(canvas, 3, 1)
    canvas.install_legacy_frames()
    canvas.current_frame = 2
    states = [state(canvas)]
    calls = []

    def op(context):
        calls.append(context.shared_cells)
        image = context.image.copy()
        image.fill(QColor("blue"))
        return image

    result = apply_over_scope(canvas, FrameScope.all_frames(canvas), op, label="一括処理")
    assert calls == [((0, 0), (3, 0))]
    assert isinstance(result.undo, ScopedBatchUndo)
    # Phase 4 第4段階で番号1つ＝画像1枚へ。
    assert [(fi, li) for fi, li, *_ in result.undo.cells] == [(0, 0)]
    # 書き戻す前の赤い snapshot を番号につき1枚採る。
    assert [img.pixelColor(5, 5).name() for _, _, img, _, _number in result.undo.cells] == [
        "#ff0000",
    ]
    assert history_label_for(result.undo) == "一括処理"
    assert history_label_for(ScopedBatchUndo([], "テスト一括")) == "テスト一括"
    assert history_label_for(LayerBatchUndo(0, [])) == "色編集"
    record(canvas, states)
    canvas.active_layer_index = 1
    canvas.undo()
    record(canvas, states)
    canvas.redo()
    record(canvas, states)
    # 実測: Undo で両コマとも赤へ戻り、Redo で両コマとも青になる。
    assert states == SCOPED


def test_archived_number_drawing_undo(window):
    canvas = window.canvas
    put(canvas, 3, 1)
    states = [state(canvas)]
    canvas.current_frame = 3
    paint(canvas, "region")
    record(canvas, states)
    canvas.delete_frame()
    record(canvas, states)
    archive = canvas._document.tracks[0].cells[1]
    assert sorted(canvas._document.tracks[0].legacy_archive_hints) == [1]
    assert archive.image.pixelColor(5, 5).name() == "#0000ff"
    canvas.undo()
    record(canvas, states)
    canvas.undo()
    record(canvas, states)
    # J1-A: restoring the pre-deletion document also restores its empty archive.
    assert canvas._document.tracks[0].legacy_archive_hints == {}
    assert canvas.frames[3].layers[0].image.pixelColor(5, 5).name() == "#ff0000"
    assert states == [
        ([E, E, E, R], 0, 0, 0, 0),
        ([E, E, E, B], 3, 0, 1, 0),
        ([E], 0, 0, 2, 0),
        ([E, E, E, B], 3, 0, 1, 1),
        ([E, E, E, R], 3, 0, 0, 2),
    ]
