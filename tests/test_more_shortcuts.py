"""追加したショートカット：Space 短押しで再生、選択範囲の消去、表示・ブラシサイズほか。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QKeySequence, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import main_window_input  # noqa: E402
from paintmaskanimator.constants import OUTSIDE_MARGIN  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    qapp.processEvents()
    try:
        yield window
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_default_keys(window):
    def keys(action):
        return [sequence.toString() for sequence in action.shortcuts()]

    assert "Ctrl+Shift+Z" in keys(window.a_redo) and "Ctrl+Y" in keys(window.a_redo)
    assert keys(window.a_selection_transform) == ["Ctrl+T"]
    assert keys(window.tool_actions["eyedropper"]) == ["I"]
    assert "Ctrl+=" in keys(window.a_view_zoom_in)
    assert keys(window.a_view_zoom_out) == ["Ctrl+-"]
    assert keys(window.a_view_fit) == ["Ctrl+0"]
    assert keys(window.a_brush_smaller) == ["["] and keys(window.a_brush_larger) == ["]"]
    assert keys(window.a_tl_add_exposure) == ["E"]
    assert keys(window.a_tl_delete_frame) == ["Shift+E"]
    assert keys(window.a_tl_layer_add) == ["Ctrl+Shift+N"]
    assert set(keys(window.a_selection_delete)) == {"Del", "Backspace"}
    # 同じキーを2つの操作に割り当てていない。
    seen = {}
    for action in window.findChildren(type(window.a_redo)):
        for sequence in action.shortcuts():
            text = sequence.toString()
            assert text not in seen or seen[text] is action, f"{text} が重複"
            seen[text] = action


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(main_window_input.time, "monotonic", clock)
    return clock


def _press_space(window, clock, hold_seconds, during=None, modifier=Qt.KeyboardModifier.NoModifier):
    target = window.canvas
    QTest.keyPress(target, Qt.Key.Key_Space, modifier)
    if during is not None:
        during()
    clock.now += hold_seconds
    QTest.keyRelease(target, Qt.Key.Key_Space, modifier)


def test_space_tap_toggles_playback_but_hold_pans(window, clock):
    played = []
    window.a_tl_play.triggered.connect(lambda *_: played.append(True))

    _press_space(window, clock, 0.1)
    assert len(played) == 1, "短く押して離すと再生／停止"

    _press_space(window, clock, 0.8)
    assert len(played) == 1, "長押しは画面の移動だけで、再生しない"

    def drag():
        assert window.canvas.temp_tool == "hand", "押している間はハンド"
        QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(300, 300))
        QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(320, 310))

    _press_space(window, clock, 0.1, during=drag)
    assert len(played) == 1, "ドラッグで画面を動かしたら、短くても再生しない"
    assert window.canvas.temp_tool is None


def test_shift_space_tap_does_not_play(window, clock):
    played = []
    window.a_tl_play.triggered.connect(lambda *_: played.append(True))
    QTest.keyPress(window.canvas, Qt.Key.Key_Shift)
    _press_space(window, clock, 0.1, modifier=Qt.KeyboardModifier.ShiftModifier)
    QTest.keyRelease(window.canvas, Qt.Key.Key_Shift)
    assert played == []


def test_delete_clears_inside_the_selection_and_undoes(window):
    canvas = window.canvas
    canvas.ensure_editable_key()
    image = canvas.active_layer.image
    painter = QPainter(image)
    painter.fillRect(image.rect(), QColor("#40c070"))
    painter.end()
    canvas.active_layer.has_content = True
    m = OUTSIDE_MARGIN
    canvas.selection_polygon = [
        QPointF(m + 10, m + 10), QPointF(m + 30, m + 10),
        QPointF(m + 30, m + 30), QPointF(m + 10, m + 30),
    ]
    # 反転した選択（マスクで持つ選択）でも中だけを消す。
    assert canvas.invert_selection()
    assert canvas.delete_selection_contents()
    image = canvas.active_layer.image
    assert image.pixelColor(m + 20, m + 20) == QColor("#40c070"), "選択の外は残る"
    assert image.pixelColor(m + 100, m + 100).alpha() == 0, "選択の中は消える"

    canvas.undo()
    assert canvas.active_layer.image.pixelColor(m + 100, m + 100) == QColor("#40c070")


def test_delete_without_selection_does_nothing(window):
    window.canvas.clear_selection()
    depth = len(window.canvas.undo_stack)
    assert not window.canvas.delete_selection_contents()
    assert len(window.canvas.undo_stack) == depth


def test_brush_size_and_zoom_steps(window):
    spin = window.tools.brush_size_spinbox
    spin.setValue(10.0)
    window.a_brush_larger.trigger()
    assert spin.value() == 11.5
    window.a_brush_smaller.trigger()
    window.a_brush_smaller.trigger()
    assert spin.value() < 10.0
    spin.setValue(0.5)
    window.a_brush_smaller.trigger()
    assert spin.value() == 0.5, "最小値より小さくしない"

    zoom = window.canvas.zoom
    window.a_view_zoom_in.trigger()
    assert window.canvas.zoom == pytest.approx(zoom * 1.25)
    window.a_view_zoom_out.trigger()
    assert window.canvas.zoom == pytest.approx(zoom)
    window.a_view_fit.trigger()
    assert window.zoom_label.text().endswith("%")


def test_key_sequence_strings_parse():
    for text in ("Ctrl+T", "Ctrl+Shift+Z", "Ctrl+=", "Ctrl+-", "Ctrl+0", "[", "]", "Shift+E"):
        assert not QKeySequence(text).isEmpty(), text
