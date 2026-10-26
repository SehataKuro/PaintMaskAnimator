"""ツールの一時切り替え（キーを押している間だけ切り替える）と、カーソル中心の拡大縮小。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, Qt  # noqa: E402
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
    # Window repaint/layout work can consume the entire fixed wait before Qt
    # dispatches the 30 ms retry. Wait for the actual transition instead.
    deadline = QElapsedTimer()
    deadline.start()
    while window.tools.active_tool != "brush" and deadline.elapsed() < 2000:
        QTest.qWait(10)
    assert window.tools.active_tool == "brush", (
        "描画終了後に元のツールへ戻る",
        window.tool_hold._hold, window.tool_hold._revert_to, window.canvas.drawing,
    )


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


def test_keyboard_zoom_keeps_the_cursor_point_even_outside_the_canvas(window, monkeypatch):
    canvas = window.canvas
    anchor = QPoint(canvas.width() + 120, -40)  # キャンバスの外（右上のパネルの辺り）
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: canvas.mapToGlobal(anchor)))
    before = canvas.widget_to_canvas(QPointF(anchor))
    window.step_canvas_zoom(0.5)
    after = canvas.widget_to_canvas(QPointF(anchor))
    assert abs(before.x() - after.x()) < 1e-6 and abs(before.y() - after.y()) < 1e-6


def test_ctrl_alt_resize_takes_priority_over_eyedropper(window):
    canvas = window.canvas
    canvas.set_tool("brush")
    spin = window.tools.brush_size_spinbox
    spin.setValue(1.5)
    QTest.keyPress(canvas, Qt.Key.Key_Control)
    QTest.keyPress(canvas, Qt.Key.Key_Alt, Qt.KeyboardModifier.ControlModifier)
    assert canvas.effective_tool() == "brush"
    modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, modifiers, QPoint(100, 100))
    QTest.mouseMove(canvas, QPoint(120, 100))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, modifiers, QPoint(120, 100))
    assert spin.value() == 3.5
    assert canvas.pen_size == spin.value()
    assert not canvas.drawing
    QTest.keyRelease(canvas, Qt.Key.Key_Alt, Qt.KeyboardModifier.ControlModifier)
    QTest.keyRelease(canvas, Qt.Key.Key_Control)
    QTest.keyClick(canvas, Qt.Key.Key_BracketLeft)
    assert spin.value() < 3.5
    smaller = spin.value()
    QTest.keyClick(canvas, Qt.Key.Key_BracketRight)
    assert spin.value() > smaller


def test_untouched_drop_does_not_prompt(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    def unexpected_dialog(*args):
        pytest.fail("Unedited projects must open without a save prompt")
    monkeypatch.setattr(QMessageBox, "exec", unexpected_dialog)
    monkeypatch.setattr(window.project, "open", lambda path: True)
    window.canvas.zoom = 2
    window.canvas.main_color = window.canvas.sub_color
    window.canvas.set_pen_size(22)
    assert not window.project.has_unsaved_changes()
    assert window.project.open_dropped("next.pman")


def test_edited_drop_can_cancel_without_opening(window, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QMessageBox
    prompts = []
    monkeypatch.setattr(QMessageBox, "exec", lambda dialog: prompts.append(dialog.text()))
    monkeypatch.setattr(window.project, "open", lambda path: pytest.fail("Cancelled drop must not open"))
    window.canvas.active_layer.image.setPixelColor(5, 5, QColor("red"))
    assert window.project.has_unsaved_changes()
    assert not window.project.open_dropped("next.pman")
    assert len(prompts) == 1


def test_save_and_open_reset_drop_guard(window, tmp_path):
    from PySide6.QtGui import QColor
    project = window.project
    path = tmp_path / "saved.pman"
    window.canvas.active_layer.image.setPixelColor(5, 5, QColor("red"))
    assert project.has_unsaved_changes()
    assert project.write(path)
    assert not project.has_unsaved_changes()
    window.canvas.active_layer.name = "renamed"
    assert project.has_unsaved_changes()
    assert project.open(path)
    assert not project.has_unsaved_changes()
    window.timeline.fps.setValue(12)
    assert project.has_unsaved_changes()


def test_revert_retry_keeps_waiting_while_drawing(window, clock, monkeypatch):
    from types import SimpleNamespace
    scheduled = []
    monkeypatch.setattr(main_window_tool_hold, "QTimer", SimpleNamespace(
        singleShot=lambda delay, callback: scheduled.append((delay, callback))))
    preferences.set_tool_hold_enabled(True)
    preferences.set_tool_hold_ms(0)
    window.tools.select_tool("brush")
    QTest.keyPress(window.canvas, Qt.Key.Key_G)
    window.canvas.drawing = True
    QTest.keyRelease(window.canvas, Qt.Key.Key_G)
    assert window.tools.active_tool == "bucket"
    delay, retry = scheduled.pop(0)
    assert delay > 0
    retry()
    assert window.tools.active_tool == "bucket", "タイマーが発火しても描画中は戻らない"
    assert len(scheduled) == 1
    window.canvas.drawing = False
    scheduled.pop(0)[1]()
    assert window.tools.active_tool == "brush"
    assert not scheduled
