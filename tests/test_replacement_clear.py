"""置換色を外す操作（#66）：1色・すべて・ポップアップの「外す」、Undo。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.color_panel import UsedColorPanel  # noqa: E402

RED, BLUE, GREEN = (255, 0, 0), (0, 0, 255), (0, 128, 0)


def _panel():
    QApplication.instance() or QApplication([])
    panel = UsedColorPanel()
    panel.set_colors([QColor("red"), QColor("blue"), QColor("green")])
    pushed = []
    panel.historyStatePush.connect(lambda before, label: pushed.append((before, label)))
    return panel, pushed


def _emitted(panel):
    emitted = []
    panel.applyReplacementRequested.connect(emitted.append)
    panel._emit_replacements()
    return emitted[-1]


def test_clearing_one_replacement_resets_the_button_and_leaves_the_others():
    panel, pushed = _panel()
    try:
        panel._set_replacement_color(RED, QColor("yellow"))
        panel._set_replacement_color(BLUE, QColor("white"))

        assert panel.clear_replacement(RED) is True

        assert panel.replacement_buttons[RED].text() == "未設定"
        assert _emitted(panel) == {BLUE: (255, 255, 255)}, "外した色は色置換で変わらない"
        assert [label for _before, label in pushed] == ["置換色を外す"]
        assert panel.clear_replacement(RED) is False, "未設定なら何もしない"
        assert len(pushed) == 1
    finally:
        panel.close()


def test_clearing_all_replacements_is_one_undo_entry_that_restores_them():
    panel, pushed = _panel()
    try:
        panel._set_replacement_color(RED, QColor("yellow"))
        panel._set_replacement_color(GREEN, QColor("black"))

        assert panel.clear_all_replacements() == 2
        assert panel.current_replacements() == {}
        assert [label for _before, label in pushed] == ["置換色をすべて外す"]

        panel.restore_history_state(pushed[0][0])

        assert panel.current_replacements() == {
            RED: (255, 255, 0), GREEN: (0, 0, 0),
        }
        assert panel.replacement_buttons[RED].text() == "#FFFF00"
        assert panel.clear_all_replacements() == 2
        assert panel.clear_all_replacements() == 0
    finally:
        panel.close()


def test_the_replacement_popup_has_a_clear_button():
    panel, pushed = _panel()
    try:
        panel._set_replacement_color(RED, QColor("yellow"))
        panel.open_replacement_editor(RED)
        popup = panel._replacement_popup
        assert popup is not None and popup.clear_button is not None

        popup.clear_button.click()

        assert RED not in panel.replacements
        assert panel._replacement_popup is None, "外したらポップアップを閉じる"
        assert pushed[-1][1] == "置換色を外す"
        panel.restore_history_state(pushed[-1][0])
        assert panel.current_replacements() == {RED: (255, 255, 0)}
    finally:
        panel._close_replacement_editor()
        panel.close()


def test_editing_in_the_popup_is_one_undo_entry():
    panel, pushed = _panel()
    try:
        panel.open_replacement_editor(RED)
        popup = panel._replacement_popup
        popup.set_color(QColor("#ff8800"))
        popup.set_color(QColor("#00ff88"))
        panel._close_replacement_editor()

        assert [label for _before, label in pushed] == ["置換色を設定"]
        panel.restore_history_state(pushed[0][0])
        assert RED not in panel.replacements
        assert panel.replacement_buttons[RED].text() == "未設定"
    finally:
        panel.close()


@pytest.fixture
def window(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from paintmaskanimator.main_window import MainWindow

    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def test_canvas_undo_brings_a_cleared_replacement_back(window):
    panel = window.palette
    panel.set_colors([QColor("#112233")])
    rgb = (0x11, 0x22, 0x33)
    panel._set_replacement_color(rgb, QColor("#445566"))

    panel.clear_replacement(rgb)
    assert rgb not in panel.replacements

    window.canvas.undo()
    assert panel.current_replacements() == {rgb: (0x44, 0x55, 0x66)}
    window.canvas.redo()
    assert rgb not in panel.replacements
