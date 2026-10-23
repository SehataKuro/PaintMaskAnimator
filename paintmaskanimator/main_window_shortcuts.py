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
        for action_id, action in actions.items():
            if not action.objectName():
                action.setObjectName(action_id)
        self._actions = actions
        self._defaults = {
            action_id: _state(action) for action_id, action in actions.items()
        }

    # ------------------------------------------------------------------
    def load(self):
        """保存したショートカットを反映する（無ければ既定のまま）。"""
        data = config.get_value(SHORTCUTS_KEY, {})
        overrides = data.get("actions", {}) if isinstance(data, dict) else {}
        if not isinstance(overrides, dict):
            return
        claimed = set()
        applied = set()
        for action_id, state in overrides.items():
            action = self._actions.get(action_id)
            if action is None or not isinstance(state, dict):
                continue  # 消えた操作の設定は無視する
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
            keys = _key_texts(action)
            kept = [key for key in keys if key not in claimed]
            if kept != keys:
                log.info("shortcut %s dropped keys taken by user settings", action_id)
                action.setShortcuts([QKeySequence(key) for key in kept])

    def save(self):
        """既定から変えたショートカットだけを保存する。"""
        overrides = {}
        for action_id, action in self._actions.items():
            state = _state(action)
            if state != self._defaults.get(action_id):
                overrides[action_id] = state
        config.set_value(
            SHORTCUTS_KEY,
            {"version": _FORMAT_VERSION, "actions": overrides} if overrides else None,
        )
