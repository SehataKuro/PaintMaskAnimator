"""オニオン表示の前後のコマを Ctrl＋クリックして直接編集する（#60）。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from paintmaskanimator.document import create_drawing
from paintmaskanimator.main_window import MainWindow


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


def block(layer, x0, y0, size=12, colour="black"):
    for x in range(x0, x0 + size):
        for y in range(y0, y0 + size):
            layer.image.setPixelColor(x, y, QColor(colour))


def prepare(window):
    """3コマ：0コマ目は左上、1コマ目は真ん中、2コマ目は右下に四角。今は1コマ目。"""
    canvas = window.canvas
    canvas._ensure_frame_count(3)
    for index, origin in enumerate(((20, 20), (60, 60), (100, 100))):
        layer = canvas.frames[index].layers[0]
        layer.image.fill(QColor(0, 0, 0, 0))
        block(layer, *origin)
        create_drawing(layer, layer.image.copy(), number=index + 1)
    canvas.current_frame = 1
    canvas.active_layer_index = 0
    canvas.onion_skin = True
    canvas.onion_previous_count = 1
    canvas.onion_next_count = 1
    canvas.onion_all_layers = False
    return canvas


def test_clicking_a_previous_or_next_onion_picks_that_frame(window):
    canvas = prepare(window)
    assert canvas.onion_frame_at(QPointF(25, 25)) == (0, 0)
    assert canvas.onion_frame_at(QPointF(105, 105)) == (2, 0)
    assert canvas.onion_frame_at(QPointF(5, 5)) is None, "何もない所"
    assert canvas.onion_frame_at(QPointF(65, 65)) is None, "今のコマの絵がある所は今のコマ"


def test_picking_switches_the_editing_frame(window):
    canvas = prepare(window)
    assert canvas.pick_onion_frame(QPointF(105, 105))
    assert canvas.current_frame == 2
    # 切り替えた先から見ると、前の今のコマ（1コマ目）がオニオンになる。
    assert canvas.onion_frame_at(QPointF(65, 65)) == (1, 0)


def test_the_onion_shift_is_taken_into_account(window):
    canvas = prepare(window)
    canvas.onion_previous_shift_x = 30.0
    assert canvas.onion_frame_at(QPointF(25, 25)) is None, "ずらした先に見えている"
    assert canvas.onion_frame_at(QPointF(55, 25)) == (0, 0)


def test_nothing_happens_when_the_onion_is_off(window):
    canvas = prepare(window)
    canvas.onion_skin = False
    assert canvas.onion_frame_at(QPointF(25, 25)) is None


def test_ctrl_click_on_the_canvas_switches_without_drawing(window):
    canvas = prepare(window)
    window.show()
    QApplication.processEvents()
    before = [frame.layers[0].image.copy() for frame in canvas.frames]
    target = canvas.canvas_to_widget(QPointF(25, 25)).toPoint()

    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, QPoint(target))

    assert canvas.current_frame == 0
    for frame, original in zip(canvas.frames, before):
        assert frame.layers[0].image == original, "クリックでは描かない"
    window.hide()
