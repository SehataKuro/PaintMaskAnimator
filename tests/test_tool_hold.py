"""ツールの一時切り替え（キーを押している間だけ切り替える）と、カーソル中心の拡大縮小。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import main_window_tool_hold, preferences  # noqa: E402
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
    window.activateWindow()
    window.canvas.setFocus()
    qapp.processEvents()
    try:
        yield window
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(main_window_tool_hold.time, "monotonic", clock)
    return clock


def press_tool_key(window, clock, key, hold_seconds):
    QTest.keyPress(window.canvas, key)
    clock.now += hold_seconds
    QTest.keyRelease(window.canvas, key)
    QApplication.processEvents()


def test_off_by_default_keeps_the_chosen_tool(window, clock):
    window.tools.select_tool("brush")
    press_tool_key(window, clock, Qt.Key.Key_G, 2.0)
    assert window.tools.active_tool == "bucket"


def test_zero_ms_switches_only_while_the_key_is_held(window, clock):
    preferences.set_tool_hold_enabled(True)
    preferences.set_tool_hold_ms(0)
    window.tools.select_tool("brush")

    QTest.keyPress(window.canvas, Qt.Key.Key_G)
    assert window.tools.active_tool == "bucket", "押している間はバケツ"
    QTest.keyRelease(window.canvas, Qt.Key.Key_G)
    assert window.tools.active_tool == "brush", "離したらペンへ戻る"


def test_short_press_below_the_threshold_keeps_the_new_tool(window, clock):
    preferences.set_tool_hold_enabled(True)
    preferences.set_tool_hold_ms(400)
    window.tools.select_tool("brush")
    press_tool_key(window, clock, Qt.Key.Key_G, 0.1)
    assert window.tools.active_tool == "bucket", "短く押したら切り替えたまま"
    press_tool_key(window, clock, Qt.Key.Key_I, 0.6)
    assert window.tools.active_tool == "bucket", "長押しは元のツールへ戻る"


def test_revert_waits_until_the_stroke_ends(window, clock, qapp):
    preferences.set_tool_hold_enabled(True)
    preferences.set_tool_hold_ms(0)
    window.tools.select_tool("brush")
    QTest.keyPress(window.canvas, Qt.Key.Key_G)
    window.canvas.drawing = True
    QTest.keyRelease(window.canvas, Qt.Key.Key_G)
    assert window.tools.active_tool == "bucket", "描いている途中では戻さない"
    window.canvas.drawing = False
    QTest.qWait(80)
    assert window.tools.active_tool == "brush"


def test_choosing_from_the_menu_is_not_a_hold(window, clock):
    preferences.set_tool_hold_enabled(True)
    preferences.set_tool_hold_ms(0)
    window.tools.select_tool("brush")
    window.tool_actions["bucket"].trigger()
    QTest.keyRelease(window.canvas, Qt.Key.Key_G)
    assert window.tools.active_tool == "bucket"


def test_keyboard_zoom_keeps_the_point_under_the_cursor(window, monkeypatch):
    canvas = window.canvas
    anchor = QPoint(canvas.width() // 4, canvas.height() // 3)
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: canvas.mapToGlobal(anchor)))
    before = canvas.widget_to_canvas(QPointF(anchor))
    window.step_canvas_zoom(1.5)
    after = canvas.widget_to_canvas(QPointF(anchor))
    assert abs(before.x() - after.x()) < 1e-6 and abs(before.y() - after.y()) < 1e-6


def test_keyboard_zoom_uses_the_view_centre_when_the_cursor_is_elsewhere(window, monkeypatch):
    canvas = window.canvas
    monkeypatch.setattr(
        QCursor, "pos", staticmethod(lambda: canvas.mapToGlobal(QPoint(-50, -50)))
    )
    calls = []
    original = canvas.set_zoom_around_canvas_center
    monkeypatch.setattr(
        canvas, "set_zoom_around_canvas_center",
        lambda zoom: (calls.append(zoom), original(zoom)),
    )
    window.step_canvas_zoom(1.5)
    assert calls, "カーソルがキャンバスの外なら表示の中心で拡大する"
