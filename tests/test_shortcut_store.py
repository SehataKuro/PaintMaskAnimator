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


def _write_panel_script(window, body):
    script = window.action_panel.actions_dir() / "my_shortcut_action.py"
    script.write_text(body, encoding="utf-8")
    return script


_PANEL_SCRIPT = (
    "def register_actions(panel, window):\n"
    "    window.panel_runs = getattr(window, 'panel_runs', 0)\n"
    "    def run():\n"
    "        window.panel_runs += 1\n"
    "    panel.add_action('my.run', 'マイアクション', run)\n"
)


def test_panel_actions_get_a_shortcut_that_survives_reloads(make_window):
    window = make_window()
    _write_panel_script(window, _PANEL_SCRIPT)
    window.action_panel.reload_python_actions()

    entries = dict(window.action_panel.shortcut_entries())
    action = entries["マイアクション"]
    action.trigger()
    assert window.panel_runs == 1, "ショートカットからボタンと同じ処理が動く"

    action.setShortcut(QKeySequence("F9"))
    window.shortcut_store.save()
    assert config.get_value(SHORTCUTS_KEY)["actions"] == {"panel:my.run": {"keys": ["F9"]}}

    # スクリプトを読み込み直しても、起動し直しても残る。
    window.action_panel.reload_python_actions()
    assert keys(dict(window.action_panel.shortcut_entries())["マイアクション"]) == ["F9"]
    again = make_window()
    assert keys(dict(again.action_panel.shortcut_entries())["マイアクション"]) == ["F9"]


def test_shortcut_of_a_panel_action_that_failed_to_load_is_kept(make_window):
    config.set_value(SHORTCUTS_KEY, {"version": 1, "actions": {"panel:gone": {"keys": ["F8"]}}})
    window = make_window()
    window.a_tl_play.setShortcut(QKeySequence("P"))
    window.shortcut_store.save()
    saved = config.get_value(SHORTCUTS_KEY)["actions"]
    assert saved["panel:gone"] == {"keys": ["F8"]}


def test_black_check_is_listed_once_under_actions(make_window, monkeypatch):
    window = make_window()
    captured = {}

    class FakeDialog:
        def __init__(self, categories, parent=None):
            captured["categories"] = categories

        def exec(self):
            return False

    monkeypatch.setattr("paintmaskanimator.main_window.ShortcutDialog", FakeDialog)
    window.shortcuts()
    categories = dict(captured["categories"])
    action_tab = [action for _name, action in categories["アクション"]]
    assert window.a_silhouette in action_tab
    assert window.a_mainline_repaint in action_tab
    others = [
        action
        for title, entries in captured["categories"]
        if title != "アクション"
        for _name, action in entries
    ]
    assert window.a_silhouette not in others
    assert window.a_mainline_repaint not in others


def test_black_check_defaults_to_ctrl_b(make_window):
    window = make_window()
    assert keys(window.a_silhouette) == ["Ctrl+B"]
