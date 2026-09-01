"""Undo/redo snapshots of the used-color panel state.

Split out of ``color_panel.py`` as a mixin. ``capture_history_state`` freezes the
panel (order, groups, visibility, masks, selection) into a plain snapshot,
``restore_history_state`` puts one back, and ``_history_edit`` is the context
manager every panel edit wraps itself in so a single undo entry is pushed only
when the snapshot actually changed.
"""
from .common import *  # noqa: F401,F403
from ._color_panel_members import UsedColorPanelMembers
from .logging_setup import get_logger

from contextlib import contextmanager

log = get_logger(__name__)


class ColorPanelHistoryMixin(UsedColorPanelMembers):
    def capture_history_state(self):
        """Undo/Redoで復元するパネル状態のスナップショットを作る。"""
        return {
            "order": tuple(self._rgb_key(color) for color in self.colors),
            "groups": dict(self.child_to_parent),
            "enabled": dict(self.enabled_colors),
            "mask": set(self.mask_rgbs),
            "selected": list(self.selected_rgbs),
            "parent": self.parent_rgb,
            "category_order": list(self.category_order),
            "category_colors": dict(self.category_colors),
            "category_collapsed": dict(self.category_collapsed),
        }

    @staticmethod
    def _history_significant(snapshot):
        """変更検知に使う、順序・親子・表示・マスクだけの部分を取り出す。"""
        return (
            tuple(snapshot.get("order", ())),
            tuple(sorted(snapshot.get("groups", {}).items())),
            tuple(sorted(snapshot.get("enabled", {}).items())),
            tuple(sorted(snapshot.get("mask", set()))),
            tuple(snapshot.get("category_order", ())),
            tuple(sorted(snapshot.get("category_colors", {}).items())),
        )

    def restore_history_state(self, snapshot):
        """スナップショットへパネルを戻し、キャンバス側の再描画信号も出す。"""
        if not snapshot:
            return
        self._history_suspended = True
        try:
            existing = {self._rgb_key(color) for color in self.colors}
            by_key = {self._rgb_key(color): color for color in self.colors}

            # 並び順を復元する（背景は必ず先頭。未知色は末尾へ回す）。
            ordered = []
            if self.background_rgb in existing:
                ordered.append(self.background_rgb)
            for rgb in snapshot.get("order", ()):
                if rgb in existing and rgb not in ordered:
                    ordered.append(rgb)
            for rgb in (self._rgb_key(color) for color in self.colors):
                if rgb not in ordered:
                    ordered.append(rgb)
            self.colors = [by_key[rgb] for rgb in ordered if rgb in by_key]

            # 使用色フォルダーを復元する。色が一時的に別レイヤーで未表示でも
            # プロジェクト内の割り当ては保持する。
            self.category_order = [
                str(name) for name in snapshot.get("category_order", [])
                if str(name).strip()
            ]
            self.category_colors = {
                tuple(rgb): str(category)
                for rgb, category in snapshot.get("category_colors", {}).items()
                if str(category) in self.category_order
            }
            self.category_collapsed = {
                name: bool(snapshot.get("category_collapsed", {}).get(name, False))
                for name in self.category_order
            }
            self._sync_category_headers()
            for name, header in self.category_widgets.items():
                header.setCollapsed(self.category_collapsed.get(name, False))
            self._reapply_row_order()

            # 親子関係を復元する（キャンバス上の色は変更しない）。
            self.child_to_parent = {
                child: parent
                for child, parent in snapshot.get("groups", {}).items()
                if child in existing and parent in existing
            }
            self._normalize_groups()
            self._reorder_children_under_parents()
            self._refresh_all_group_displays()

            # 表示状態を復元する。
            enabled = snapshot.get("enabled", {})
            self._isolated_rgb = None
            self._pre_isolate_enabled = None
            for rgb in self.visibility_checks:
                self._set_checkbox_without_signal(rgb, enabled.get(rgb, True))

            # マスク状態を復元する。
            mask = snapshot.get("mask", set())
            self.mask_rgbs = {
                rgb for rgb in mask
                if rgb in existing or rgb == self.background_rgb
            }
            for rgb in self.mask_checks:
                self._set_mask_checkbox_without_signal(rgb, rgb in self.mask_rgbs)
            self._mask_all_mode = self.all_masks_enabled()

            # 選択状態も戻す（履歴の見た目を揃えるため）。
            self.selected_rgbs = [
                rgb for rgb in snapshot.get("selected", []) if rgb in existing
            ]
            parent = snapshot.get("parent")
            self.parent_rgb = (
                parent if parent in existing
                else (self.selected_rgbs[-1] if self.selected_rgbs else None)
            )
            self._refresh_used_color_styles()
            self._refresh_category_headers()
        finally:
            self._history_suspended = False

        # キャンバス側へ最新状態を伝える。
        self._emit_preview()
        self.maskColorsChanged.emit(set(self.mask_rgbs))
        self.selectedColorsChanged.emit(set(self.selected_rgbs))
        self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _record_history(self, label, before):
        """変更前後を比べ、意味のある差があればUndo履歴へ積む。"""
        if self._history_suspended or before is None:
            return
        after = self.capture_history_state()
        if self._history_significant(before) == self._history_significant(after):
            return
        self.historyStatePush.emit(before, label)

    @contextmanager
    def _history_edit(self, label):
        """with で囲んだ範囲の変更を1件のUndoエントリにまとめる。"""
        if self._history_suspended:
            yield
            return
        before = self.capture_history_state()
        yield
        self._record_history(label, before)
