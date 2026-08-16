"""Parent/child grouping and row reordering for the used-color panel.

Split out of ``color_panel.py`` as a mixin. These methods own the group graph
(``_color_groups``): making one colour a child of another, dropping links, the
drag-and-drop reorder of single rows and of multi-row selections, keeping
children laid out under their parent, and the preview / freeze / merge signals
that publish the current grouping to the main window.
"""
from .common import *  # noqa: F401,F403
from ._color_panel_members import UsedColorPanelMembers
from .logging_setup import get_logger

log = get_logger(__name__)


class ColorGroupingMixin(UsedColorPanelMembers):
    def _color_index(self, rgb):
        for index, color in enumerate(self.colors):
            if self._rgb_key(color) == rgb:
                return index
        return None

    def _group_root(self, rgb):
        """親子チェーンをたどって最終的な親（ルート色）を返す。"""
        seen = set()
        current = rgb
        while current in self.child_to_parent and current not in seen:
            seen.add(current)
            current = self.child_to_parent[current]
        return current

    def _drop_group_links_for(self, rgb):
        """指定色が親でも子でも、その親子リンクをすべて解除する。"""
        changed = self.child_to_parent.pop(rgb, None) is not None
        for child in [c for c, p in self.child_to_parent.items() if p == rgb]:
            self.child_to_parent.pop(child, None)
            changed = True
        return changed

    def _handle_color_drop(self, dragged_rgb, target_rgb, mode):
        dragged_rgb = tuple(dragged_rgb)
        target_rgb = tuple(target_rgb)
        if dragged_rgb == target_rgb or dragged_rgb == self.background_rgb:
            return
        if target_rgb == self.background_rgb:
            return
        label = "親子付け" if mode == "child" else "使用色の並べ替え"
        with self._history_edit(label):
            if mode == "child":
                selected = {
                    tuple(value) for value in self.selected_rgbs
                    if tuple(value) != self.background_rgb
                }
                children = (
                    [
                        self._rgb_key(color) for color in self.colors
                        if self._rgb_key(color) in selected
                        and self._rgb_key(color) != target_rgb
                    ]
                    if dragged_rgb in selected
                    else [dragged_rgb]
                )
                for child_rgb in children:
                    self._make_child_of(child_rgb, target_rgb)
            else:
                selected = {
                    tuple(value) for value in self.selected_rgbs
                    if tuple(value) != self.background_rgb
                }
                if dragged_rgb in selected and len(selected) > 1:
                    self._reorder_color_block(selected, target_rgb, mode)
                else:
                    self._reorder_color(dragged_rgb, target_rgb, mode)
                if self._drop_group_links_outside_parent_blocks():
                    self._refresh_all_group_displays()
                    self._emit_preview()

    def _make_child_of(self, child_rgb, parent_rgb):
        """child_rgb を parent_rgb の子にして、親色プレビューを更新する。"""
        # 循環を避ける。ドロップ先が自分の子孫なら親子化しない。
        probe = parent_rgb
        guard = 0
        while probe in self.child_to_parent and guard < len(self.child_to_parent) + 1:
            if probe == child_rgb:
                return
            probe = self.child_to_parent[probe]
            guard += 1
        # 親自身が誰かの子なら、実際のルートへ束ねる（単層に正規化）。
        root = self._group_root(parent_rgb)
        if root == child_rgb:
            return
        # child_rgb にぶら下がっていた子は、まとめて新しいルートへ移す。
        for grandchild in [c for c, p in self.child_to_parent.items() if p == child_rgb]:
            self.child_to_parent[grandchild] = root
        self.child_to_parent[child_rgb] = root
        self._normalize_groups()
        self._reorder_children_under_parents()
        self._refresh_all_group_displays()
        self._emit_preview()

    def _normalize_groups(self):
        """全リンクをルート直付けに正規化し、背景・自己参照を除去する。"""
        cleaned = {}
        for child, parent in self.child_to_parent.items():
            if child == self.background_rgb or child == parent:
                continue
            root = self._group_root(parent)
            if root == child or root == self.background_rgb:
                continue
            cleaned[child] = root
        self.child_to_parent = cleaned

    def _reorder_color(self, dragged_rgb, target_rgb, mode):
        """dragged_rgb を target_rgb の前／後ろへ移動する（背景は先頭固定）。"""
        drag_index = self._color_index(dragged_rgb)
        if drag_index is None:
            return
        moved = self.colors.pop(drag_index)
        target_index = self._color_index(target_rgb)
        if target_index is None:
            self.colors.insert(drag_index, moved)
            return
        if mode == "after":
            target_index += 1
        # 背景色（先頭）より前には入れない。
        target_index = max(1, target_index)
        self.colors.insert(target_index, moved)
        self._reapply_row_order()

    def _reorder_color_block(self, selected_rgbs, target_rgb, mode):
        """複数選択色を現在の並び順のまま一括移動する。"""
        selected = set(selected_rgbs)
        selected.discard(self.background_rgb)
        if not selected or target_rgb in selected:
            return

        moved = [
            color for color in self.colors
            if self._rgb_key(color) in selected
        ]
        if not moved:
            return
        remaining = [
            color for color in self.colors
            if self._rgb_key(color) not in selected
        ]
        target_index = next(
            (
                index for index, color in enumerate(remaining)
                if self._rgb_key(color) == target_rgb
            ),
            None,
        )
        if target_index is None:
            return
        if mode == "after":
            target_index += 1
        target_index = max(1, target_index)
        self.colors = [
            *remaining[:target_index],
            *moved,
            *remaining[target_index:],
        ]
        self._reapply_row_order()

    def _drop_group_links_outside_parent_blocks(self):
        """親の直後に連続していない子の親子リンクを解除する。"""
        if not self.child_to_parent:
            return False

        order = [self._rgb_key(color) for color in self.colors]
        retained_children = set()
        parents = set(self.child_to_parent.values())
        for parent in parents:
            try:
                index = order.index(parent) + 1
            except ValueError:
                continue
            while (
                index < len(order)
                and self.child_to_parent.get(order[index]) == parent
            ):
                retained_children.add(order[index])
                index += 1

        detached = set(self.child_to_parent) - retained_children
        for child in detached:
            self.child_to_parent.pop(child, None)
        return bool(detached)

    def _reorder_children_under_parents(self):
        """子色を、その親色（ルート）の直後へまとめて並べ替える。"""
        if not self.child_to_parent:
            return
        key_to_color = {self._rgb_key(color): color for color in self.colors}
        order = [self._rgb_key(color) for color in self.colors]
        child_set = set(self.child_to_parent)

        # 各ルート親ごとに、現在の並び順を保ったまま子をぶら下げる。
        children_by_root = {}
        for child in order:
            if child in child_set:
                children_by_root.setdefault(
                    self._group_root(child), []
                ).append(child)

        result = []
        for rgb in order:
            if rgb in child_set:
                continue  # 親の直後にまとめて置くのでここでは飛ばす。
            result.append(rgb)
            result.extend(children_by_root.get(rgb, []))
        # 親が見つからない子は末尾へ回して取りこぼしを防ぐ。
        for child in order:
            if child in child_set and child not in result:
                result.append(child)

        self.colors = [
            key_to_color[rgb] for rgb in result if rgb in key_to_color
        ]
        self._reapply_row_order()

    def _reapply_row_order(self):
        """self.colors の順序どおりに行ウィジェットを並べ替える。"""
        for position, color in enumerate(self.colors):
            rgb = self._rgb_key(color)
            widget = self.row_widgets.get(rgb)
            if widget is None:
                continue
            self.rows.removeWidget(widget)
            self.rows.insertWidget(position, widget)

    def _refresh_all_group_displays(self):
        for rgb in list(self.source_buttons):
            self._refresh_group_display(rgb)
        has_groups = bool(self.child_to_parent)
        self.freeze_button.setEnabled(has_groups)

    def _refresh_group_display(self, rgb):
        """子色は元色のまま表示し、ボーダーを親色にして階層を示す。"""
        button = self.source_buttons.get(rgb)
        wrapper = self.source_wrappers.get(rgb)
        if button is None:
            return
        parent = self.child_to_parent.get(rgb)
        if parent is not None:
            # 元色は保持し、縁取り（ボーダー）だけを親色にする。
            root = self._group_root(rgb)
            parent_color = QColor(*root)
            own = QColor(*rgb)
            text_color = self._text_color(rgb)
            button.setStyleSheet(
                "QToolButton{"
                f"background:{own.name()};color:{text_color};"
                f"border:2px solid {parent_color.name()};padding:2px;}}"
                "QToolButton:hover{"
                f"background:{own.name()};color:{text_color};"
                f"border:2px solid {parent_color.name()};}}"
            )
            if wrapper is not None:
                margin = wrapper.layout()
                if margin is not None:
                    # 行頭インデントで階層を示す（右余白は0でスウォッチを広げる）。
                    margin.setContentsMargins(20, 3, 0, 3)
        else:
            if wrapper is not None:
                margin = wrapper.layout()
                if margin is not None:
                    margin.setContentsMargins(2, 3, 0, 3)
        # 選択枠・グループ縦ライン・チェック同期・HEX表示・ツールチップを更新。
        self._set_source_button_style(rgb)
        self._apply_swatch_text(rgb)

    def _group_mapping(self):
        """{子rgb: ルート親rgb} を返す（プレビュー／フリーズ共通）。"""
        return {
            child: self._group_root(child)
            for child in self.child_to_parent
        }

    def _emit_preview(self):
        self.previewGroupsChanged.emit(self._group_mapping())

    def _clear_groups(self):
        if not self.child_to_parent:
            return
        with self._history_edit("親子をすべて解除"):
            self.child_to_parent = {}
            self._refresh_all_group_displays()
            self._emit_preview()

    def on_groups_frozen(self):
        """フリーズ確定後：プレビューを解除する（実ピクセルは親色に確定済み）。"""
        self.child_to_parent = {}
        self._refresh_all_group_displays()
        self.previewGroupsChanged.emit({})

    def _emit_freeze(self):
        mapping = self._group_mapping()
        if not mapping:
            window: Any = self.window()
            if hasattr(window, "statusBar"):
                window.statusBar().showMessage(
                    "フリーズする親子（プレビュー）がありません。"
                    "色を別の色の中へドロップして親子を作成してください。",
                    2800,
                )
            return
        self.freezeGroupsRequested.emit(mapping)

    def _retain_parent_selection(self):
        old = set(self.selected_rgbs)
        self.selected_rgbs = [self.parent_rgb] if self.parent_rgb is not None else []
        for rgb in old | set(self.selected_rgbs):
            self._set_source_button_style(rgb)
        self.selectedColorsChanged.emit(set(self.selected_rgbs))

    def _emit_merge(self):
        if self.parent_rgb is None or len(self.selected_rgbs) < 2:
            window: Any = self.window()
            if hasattr(window, "statusBar"):
                window.statusBar().showMessage(
                    "統合する使用色を2色以上選択してください。最後に選んだ色が親です。",
                    2800,
                )
            return
        selected = set(self.selected_rgbs)
        self.mergeColorsRequested.emit(tuple(self.parent_rgb), selected)
