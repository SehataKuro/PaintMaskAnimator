"""ショートカットの保存と復元。``MainWindow`` が ``window.shortcut_store`` として持つ。

ショートカット設定の変更は、これまでその場のアクションに反映するだけで
どこにも保存しておらず、起動し直す（アップデートする）たびに既定へ戻っていた。

- 設定ファイル（アップデートや再インストールでも残る場所）に保存する
- 保存するのは既定から変えたものだけ。変えていない操作は、新しいバージョンで
  既定のキーが変わればそれに付いていく
- 保存のキーは翻訳される表示名ではなく、アクションの属性名から作る固定の ID
- 新しいバージョンで足された既定のキーが自分で決めたキーとぶつかったら、
  自分で決めた方を優先し、既定の側からそのキーを外す（両方に残すとどちらも
  効かなくなるため）
- アクションパネルのボタン（Python アクション）は ``panel:<ID>`` で保存する。
  スクリプトを読み込み直すたびに付け直し、読み込めなかったスクリプトの
  設定も消さずに残す

A collaborator rather than a mixin -- see main_window_export.py for why.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QKeySequence

from . import config
from .logging_setup import get_logger

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)

#: 設定ファイルのキー。
SHORTCUTS_KEY = "shortcuts"
_FORMAT_VERSION = 1
#: アクションパネルのボタンの ID に付ける接頭辞。
PANEL_PREFIX = "panel:"


def _key_texts(action):
    return [
        sequence.toString(QKeySequence.SequenceFormat.PortableText)
        for sequence in action.shortcuts()
        if not sequence.isEmpty()
    ]


def _is_hold(action):
    return bool(action.property("holdOperation"))


def _state(action):
    """保存・比較に使う、アクションの今のショートカット。"""
    if _is_hold(action):
        stored = action.property("holdShortcutText")
        return {"hold": "" if stored is None else str(stored)}
    return {"keys": _key_texts(action)}


class ShortcutStore:
    def __init__(self, window: "MainWindow"):
        self.window = window
        self._actions: dict[str, QAction] = {}
        self._defaults: dict[str, dict] = {}

    def register_actions(self):
        """ショートカットを持てるアクションに固定の ID を付け、既定を覚える。"""
        actions = {}
        for name, value in vars(self.window).items():
            if name.startswith("a_") and isinstance(value, QAction):
                actions[name] = value
        for tool_id, action in getattr(self.window, "tool_actions", {}).items():
            actions[f"tool:{tool_id}"] = action
        actions.update(self._panel_actions())
        for action_id, action in actions.items():
            if not action.objectName():
                action.setObjectName(action_id)
        self._actions = actions
        self._defaults = {
            action_id: _state(action) for action_id, action in actions.items()
        }

    def _panel_actions(self):
        panel = getattr(self.window, "action_panel", None)
        if panel is None:
            return {}
        return {
            PANEL_PREFIX + action_id: action
            for action_id, action in panel.own_shortcut_actions().items()
        }

    def register_panel_actions(self):
        """アクションパネルを読み込み直したあと、そのボタンの分を登録し直す。"""
        if not self._actions:
            return  # 起動中の最初の読み込みは register_actions がまとめて扱う
        for action_id in [key for key in self._actions if key.startswith(PANEL_PREFIX)]:
            del self._actions[action_id]
            self._defaults.pop(action_id, None)
        panel_actions = self._panel_actions()
        for action_id, action in panel_actions.items():
            action.setObjectName(action_id)
            self._actions[action_id] = action
            self._defaults[action_id] = _state(action)
        self.load(only=set(panel_actions))

    # ------------------------------------------------------------------
    @staticmethod
    def _stored_overrides():
        data = config.get_value(SHORTCUTS_KEY, {})
        overrides = data.get("actions", {}) if isinstance(data, dict) else {}
        return overrides if isinstance(overrides, dict) else {}

    def load(self, only=None):
        """保存したショートカットを反映する（無ければ既定のまま）。

        ``only`` を渡すと、その ID のアクションだけに反映する。
        """
        overrides = self._stored_overrides()
        claimed = set()
        applied = set()
        for action_id, state in overrides.items():
            action = self._actions.get(action_id)
            if action is None or not isinstance(state, dict):
                continue  # 消えた操作の設定は無視する
            if only is not None and action_id not in only:
                if not _is_hold(action):
                    claimed.update(_key_texts(action))
                continue
            if _is_hold(action):
                text = str(state.get("hold", ""))
                action.setProperty("holdShortcutText", text)
                action.setShortcut(QKeySequence(text))
                if text:
                    claimed.add(text)
            else:
                keys = [str(key) for key in state.get("keys", []) if str(key)]
                action.setShortcuts([QKeySequence(key) for key in keys])
                claimed.update(_key_texts(action))
            applied.add(action_id)
        # 自分で決めたキーと既定のキーがぶつかったら、既定の側から外す。
        for action_id, action in self._actions.items():
            if action_id in applied or _is_hold(action):
                continue
            if only is not None and action_id not in only:
                continue
            keys = _key_texts(action)
            kept = [key for key in keys if key not in claimed]
            if kept != keys:
                log.info("shortcut %s dropped keys taken by user settings", action_id)
                action.setShortcuts([QKeySequence(key) for key in kept])

    def save(self):
        """既定から変えたショートカットだけを保存する。"""
        # 読み込めなかったパネルのアクションの設定は、直したときのために残す。
        overrides = {
            action_id: state
            for action_id, state in self._stored_overrides().items()
            if action_id.startswith(PANEL_PREFIX) and action_id not in self._actions
        }
        for action_id, action in self._actions.items():
            state = _state(action)
            if state != self._defaults.get(action_id):
                overrides[action_id] = state
        config.set_value(
            SHORTCUTS_KEY,
            {"version": _FORMAT_VERSION, "actions": overrides} if overrides else None,
        )
