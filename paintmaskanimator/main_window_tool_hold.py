"""ツールの一時切り替え; owned by ``MainWindow`` as ``window.tool_hold``.

CLIP STUDIO PAINT の「一時切り替え」と同じく、ツールのキーを押すとすぐその
ツールに切り替え、環境設定の長さ以上押し続けてから離したら元のツールへ戻す。
長さを 0 ms にすると、押している間だけ切り替わる（完全な一時切り替え）。
環境設定で OFF のとき（既定）は、これまでどおり押したツールに切り替えたままにする。

キーを押したことは、ショートカットの直前に届く ``ShortcutOverride`` で知る
（ショートカットになったキーには ``KeyPress`` が届かないため）。メニューから
選んだときは長押しにならないよう、キーの押下と結び付いたときだけ扱う。

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, FrozenSet, Optional

from PySide6.QtCore import QTimer

from . import preferences

if TYPE_CHECKING:
    from .main_window import MainWindow

_MODIFIERS = frozenset({"Ctrl", "Shift", "Alt", "Meta"})
#: キーの押下（ShortcutOverride）からショートカットの実行までに許す時間（秒）。
_PRESS_MATCH_SECONDS = 0.5
#: 描いている途中で戻すのを待つとき、描き終わりを確かめる間隔（ミリ秒）。
_WAIT_FOR_STROKE_MS = 30


@dataclass
class _Hold:
    tool: str
    previous: str
    keys: FrozenSet[str]
    started: float


class ToolHoldController:
    """Owned by ``MainWindow`` as ``window.tool_hold``."""

    def __init__(self, window: "MainWindow"):
        self.window = window
        self._hold: Optional[_Hold] = None
        self._last_key: Optional[tuple] = None
        self._revert_to: Optional[tuple] = None

    def key_pressed(self, token):
        """キーが押された（ショートカットになる前の ShortcutOverride で呼ぶ）。"""
        if token:
            self._last_key = (token, time.monotonic())

    def select(self, tool_id, action):
        """ツールのショートカット・メニューが選ばれた。"""
        tools = self.window.tools
        keys = frozenset(
            token for token in self.window._shortcut_tokens(action, "")
            if token not in _MODIFIERS
        )
        hold = self._hold
        if hold is not None and hold.tool == tool_id:
            return  # キーの自動リピート
        from_key = (
            self._last_key is not None
            and self._last_key[0] in keys
            and time.monotonic() - self._last_key[1] < _PRESS_MATCH_SECONDS
        )
        self._last_key = None
        if not preferences.tool_hold_enabled() or not from_key:
            self._hold = None
            tools.select_tool(tool_id)
            return
        # 一時切り替え中に別のツールのキーを押したら、戻り先は最初のツールのまま。
        previous = hold.previous if hold is not None else tools.active_tool
        self._hold = _Hold(tool_id, previous, keys, time.monotonic())
        tools.select_tool(tool_id)

    def key_released(self, token):
        """キーが離された。長押しだったら元のツールへ戻す。"""
        hold = self._hold
        if hold is None or token not in hold.keys:
            return
        self._hold = None
        held_ms = (time.monotonic() - hold.started) * 1000.0
        if held_ms >= preferences.tool_hold_ms():
            self._revert(hold.tool, hold.previous)

    def release_all(self):
        """ウィンドウからフォーカスが外れた。押していたキーは離したものとみなす。"""
        hold = self._hold
        if hold is not None:
            self.key_released(next(iter(hold.keys), None))

    def _revert(self, tool, previous):
        self._revert_to = (tool, previous)
        self._try_revert()

    def _try_revert(self):
        pending = self._revert_to
        if pending is None:
            return
        if self.window.canvas.drawing:
            # 描き終わってから戻す（描いている途中でツールを変えない）。
            QTimer.singleShot(_WAIT_FOR_STROKE_MS, self._try_revert)
            return
        self._revert_to = None
        tool, previous = pending
        # その間に別のツールを選んでいたら、そちらを優先する。
        if self.window.tools.active_tool == tool and previous != tool:
            self.window.tools.select_tool(previous)


__all__ = ["ToolHoldController"]
