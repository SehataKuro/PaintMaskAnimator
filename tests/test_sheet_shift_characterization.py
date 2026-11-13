"""Issue #31 Phase 3: 配列操作への置換前に、現行のシート編集結果を固定する。

期待値は現行実装の実測値。露出内の未使用セルも含め、全コマを比較する。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dense_fixtures import hold
from paintmaskanimator.document import create_drawing, project_v1_state, clear_pending
from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.models import Layer, make_frame  # noqa: E402
from paintmaskanimator.utils import blank_image  # noqa: E402

COLORS = {1: "#ff0000", 2: "#00ff00", 3: "#0000ff", 4: "#ffaa00"}
EMPTY = (None, 1, False, False, False)

BLANK = (None, 1, False, True, False)
K1 = (1, 3, True, False, False)
K2 = (2, 1, True, False, False)
K3 = (3, 1, True, False, False)

# 現行実装を実行して採取。EMPTYも省略せず全コマを並べる。
INSERT = {
    0: ([EMPTY, K1, EMPTY, EMPTY, BLANK, EMPTY, K2, EMPTY, K3, EMPTY],
        {1: "#ff0000", 6: "#00ff00", 8: "#0000ff"}, 10, 2, []),
    1: ([(1, 4, True, False, False), EMPTY, EMPTY, EMPTY, BLANK, EMPTY, K2, EMPTY, K3, EMPTY],
        {0: "#ff0000", 6: "#00ff00", 8: "#0000ff"}, 10, 2, []),
    3: ([K1, EMPTY, EMPTY, EMPTY, BLANK, EMPTY, K2, EMPTY, K3, EMPTY],
        {0: "#ff0000", 6: "#00ff00", 8: "#0000ff"}, 10, 2, []),
    4: ([K1, EMPTY, EMPTY, BLANK, EMPTY, EMPTY, K2, EMPTY, K3, EMPTY],
        {0: "#ff0000", 6: "#00ff00", 8: "#0000ff"}, 10, 2, []),
    8: ([K1, EMPTY, EMPTY, BLANK, EMPTY, K2, EMPTY, K3, EMPTY, EMPTY],
        {0: "#ff0000", 5: "#00ff00", 7: "#0000ff"}, 10, 2, []),
}
MENU_DELETE = {
    1: ([(1, 2, True, False, False), EMPTY, BLANK, EMPTY, K2, EMPTY,
         K3, EMPTY, EMPTY],
        {0: "#ff0000", 4: "#00ff00", 6: "#0000ff"}, 9, 1, []),
    5: ([K1, EMPTY, EMPTY, BLANK, EMPTY, EMPTY, K3, EMPTY, EMPTY],
        {0: "#ff0000", 6: "#0000ff"}, 9, 5, []),
    3: ([K1, EMPTY, EMPTY, EMPTY, K2, EMPTY, K3, EMPTY, EMPTY],
        {0: "#ff0000", 4: "#00ff00", 6: "#0000ff"}, 9, 3, []),
    4: ([K1, EMPTY, EMPTY, BLANK, K2, EMPTY, K3, EMPTY, EMPTY],
        {0: "#ff0000", 4: "#00ff00", 6: "#0000ff"}, 9, 4, []),
}

CANVAS_DELETE = {
    1: ([(1, 2, True, False, False), EMPTY, BLANK, EMPTY, K2, EMPTY, K3],
        {0: "#ff0000", 4: "#00ff00", 6: "#0000ff"}, 7, 1, [(0, 1)]),
    5: ([K1, EMPTY, EMPTY, BLANK, EMPTY, EMPTY, K3],
        {0: "#ff0000", 6: "#0000ff"}, 7, 5, [(0, 2)]),
    3: ([K1, EMPTY, EMPTY, EMPTY, K2, EMPTY, K3],
        {0: "#ff0000", 4: "#00ff00", 6: "#0000ff"}, 7, 3, []),
    4: ([K1, EMPTY, EMPTY, BLANK, K2, EMPTY, K3],
        {0: "#ff0000", 4: "#00ff00", 6: "#0000ff"}, 7, 4, []),
}


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
        window.close()
        window.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def put(canvas, column, number, exposure=1, layer_index=0):
    layer = canvas.frames[column].layers[layer_index]
    layer.has_content = number is not None
    layer.is_blank_key = number is None
    clear_pending(layer)
    if number is not None:
        image = layer.image.copy()
        image.setPixelColor(5, 5, QColor(COLORS[number]))
        create_drawing(layer, image, number=number)
    hold(canvas, column, exposure, layer_index)
    return layer


def setup_sheet(window):
    canvas = window.canvas
    names = [canvas.layers[0].name, "Other"]
    canvas.frames = [make_frame(names) for _ in range(9)]
    canvas.timeline_mode = "sheet"
    canvas.active_layer_index = 0
    canvas.current_frame = 2
    put(canvas, 0, 1, 3)
    put(canvas, 3, None)
    put(canvas, 5, 2)
    put(canvas, 7, 3)
    put(canvas, 1, 4, 2, layer_index=1)
    window.refresh_ui()
    return canvas


def row(canvas, layer_index=0):
    return len(canvas.layers) - 1 - layer_index


def layer_state(canvas, layer_index=0):
    cells, pixels = [], {}
    for column, frame in enumerate(project_v1_state(canvas.frames)[0]):
        layer = frame.layers[layer_index]
        cells.append((layer.sequence_number, layer.exposure, layer.has_content,
                      layer.is_blank_key, layer.sequence_only))
        if layer.has_content:
            pixels[column] = layer.image.pixelColor(5, 5).name()
    return cells, pixels


def state(canvas):
    return (*layer_state(canvas), len(canvas.frames), canvas.current_frame,
            sorted((li, number) for li, track in enumerate(canvas._document.tracks)
                   for number in track.legacy_archive_hints))


def check(canvas, expected):
    assert state(canvas) == expected


@pytest.mark.parametrize("column", [0, 1, 3, 4, 8],
                         ids=["head", "held", "blank", "unused", "after-last-key"])
def test_insert_frame_and_undo(window, column):
    canvas = setup_sheet(window)
    before = state(canvas)
    other = layer_state(canvas, 1)
    assert window.timeline_ops.insert_frame_at(row(canvas), column)
    # 決定(3): 露出途中の挿入はその区間を1コマ延ばす。
    check(canvas, INSERT[column])
    assert layer_state(canvas, 1) == (other[0] + [EMPTY], other[1])
    canvas.undo()
    assert state(canvas) == before
    assert layer_state(canvas, 1) == other


@pytest.mark.parametrize("column", [1, 5, 3, 4],
                         ids=["held", "single-key", "blank", "unused"])
def test_menu_and_canvas_delete(window, column):
    canvas = setup_sheet(window)
    other = layer_state(canvas, 1)
    assert window.timeline_ops.delete_frame_at(row(canvas), column)
    check(canvas, MENU_DELETE[column])
    assert layer_state(canvas, 1) == other
    canvas.undo()
    canvas.active_layer_index = 0
    canvas.current_frame = column
    canvas.delete_frame()
    # メニュー経由とは異なり、末尾を切り詰め、番号つきの絵を保管する。
    check(canvas, CANVAS_DELETE[column])
    assert layer_state(canvas, 1) == (other[0][:7], other[1])
    if column == 5:
        assert 2 in canvas._document.tracks[0].legacy_archive_hints
        archived = canvas._document.tracks[0].cells[2]
        assert archived.image.pixelColor(5, 5).name() == COLORS[2]
    if column == 4:
        # 未使用の1コマだけの場合は何も削除せず、Undoも増やさない。
        canvas.frames = [make_frame(["Only"])]
        canvas.current_frame = 0
        canvas.active_layer_index = 0
        undo_count = len(canvas.undo_stack)
        canvas.delete_frame()
        check(canvas, ([EMPTY], {}, 1, 0, []))
        assert len(canvas.undo_stack) == undo_count
        put(canvas, 0, 1)
        canvas.delete_frame()
        check(canvas, ([EMPTY], {}, 1, 0, [(0, 1)]))


def test_blank_key_at_exposure_head_shifts_later_keys(window):
    canvas = setup_sheet(window)
    assert canvas.create_blank_key(0, 0)
    check(canvas, (
        [K1, EMPTY, EMPTY, (None, 3, False, True, False), EMPTY, EMPTY,
         BLANK, EMPTY, K2, EMPTY, K3],
        {0: "#ff0000", 8: "#00ff00", 10: "#0000ff"}, 11, 3, [],
    ))


@pytest.mark.parametrize("states", [[4, 4], [1, 1, None, None, 4, 4]],
                         ids=["cut-prior-and-preserve-tail", "compress-number-and-blank"])
def test_time_remap_and_undo(window, states):
    canvas = setup_sheet(window)
    image = blank_image()
    image.setPixelColor(5, 5, QColor(COLORS[4]))
    canvas.install_legacy_frames(sequence_archive={
        (0, 4): Layer(canvas.layers[0].name, image, has_content=True, sequence_number=4)
    })
    before = state(canvas)
    parsed = {"states": states, "start_frame": 1,
              "end_frame": len(states), "format": "test"}
    assert window.time_remap.apply_to_active_layer(parsed, confirm=False)
    if len(states) == 2:
        expected = (
            [(1, 1, True, False, False), (4, 2, True, False, False), EMPTY,
             BLANK, EMPTY, K2, EMPTY, K3, EMPTY],
            {0: "#ff0000", 1: "#ffaa00", 5: "#00ff00", 7: "#0000ff"},
            9, 1, [],
        )
    else:
        expected = (
            [(1, 1, True, False, False), (1, 2, True, False, False), EMPTY,
             (None, 2, False, True, False), EMPTY, (4, 2, True, False, False),
             EMPTY, K3, EMPTY],
            {0: "#ff0000", 1: "#ff0000", 5: "#ffaa00", 7: "#0000ff"},
            9, 1, [(0, 2)],
        )
    check(canvas, expected)
    if len(states) != 2:
        assert canvas._document.tracks[0].cells[2].image.pixelColor(5, 5).name() == COLORS[2]
    canvas.undo()
    # J1-A: DocUndo restores the compatibility archive with the sheet.
    assert state(canvas) == before
    assert sorted((li, number) for li, track in enumerate(canvas._document.tracks)
                   for number in track.legacy_archive_hints) == [(0, 4)]
