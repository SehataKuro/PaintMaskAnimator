"""ショートカットの保存と復元（起動し直しても・アップデートしても残る）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import config  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.main_window_shortcuts import SHORTCUTS_KEY  # noqa: E402
from paintmaskanimator.widgets import ShortcutDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    created = []

    def make():
        window = MainWindow()
        created.append(window)
        return window

    yield make
    for window in created:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def keys(action):
    return [s.toString() for s in action.shortcuts()]


def test_changed_shortcuts_survive_a_restart(make_window):
    window = make_window()
    window.a_tl_play.setShortcut(QKeySequence("P"))
    window.tool_actions["brush"].setShortcut(QKeySequence("B"))
    window.a_hold_hand.setProperty("holdShortcutText", "H")
    window.a_hold_hand.setShortcut(QKeySequence("H"))
    window.shortcut_store.save()

    saved = config.get_value(SHORTCUTS_KEY)["actions"]
    assert set(saved) == {"a_tl_play", "tool:brush", "a_hold_hand"}, "変えたものだけ保存する"

    again = make_window()
    assert keys(again.a_tl_play) == ["P"]
    assert keys(again.tool_actions["brush"]) == ["B"]
    assert again.a_hold_hand.property("holdShortcutText") == "H"
    assert keys(again.a_undo) == ["Ctrl+Z"], "変えていないものは既定のまま"


def test_user_key_wins_over_a_new_default(make_window):
    # 将来の既定（ここでは Ctrl+T＝自由変形）と、前に自分で決めたキーがぶつかる。
    config.set_value(SHORTCUTS_KEY, {"version": 1, "actions": {"a_redo": {"keys": ["Ctrl+T"]}}})
    window = make_window()
    assert keys(window.a_redo) == ["Ctrl+T"]
    assert "Ctrl+T" not in keys(window.a_selection_transform)


def test_unknown_entries_are_ignored(make_window):
    config.set_value(SHORTCUTS_KEY, {"version": 1, "actions": {"a_gone": {"keys": ["F9"]}, "a_undo": "bad"}})
    window = make_window()
    assert keys(window.a_undo) == ["Ctrl+Z"]


def test_saving_everything_back_to_defaults_clears_the_setting(make_window):
    window = make_window()
    window.a_tl_play.setShortcut(QKeySequence("P"))
    window.shortcut_store.save()
    window.a_tl_play.setShortcut(QKeySequence())
    window.shortcut_store.save()
    assert config.get_value(SHORTCUTS_KEY) is None


def test_ok_without_changes_keeps_second_keys(make_window):
    window = make_window()
    categories = [("all", [("redo", window.a_redo), ("clear", window.a_selection_clear)])]
    dialog = ShortcutDialog(categories, window)
    try:
        dialog.apply()
    finally:
        dialog.deleteLater()
    assert "Ctrl+Shift+Z" in keys(window.a_redo)
    assert "Ctrl+Shift+A" in keys(window.a_selection_clear)
