"""Row selection state for the used-color panel.

Split out of ``color_panel.py`` as a mixin. These methods own which colours are
selected -- click, ctrl/shift-click and checkbox toggling, the sweep drag over
the selection column, and the queries (``selected_rgb_set``,
``_ordered_non_background_rgbs``) the rest of the panel and the main window use.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from ._color_panel_members import UsedColorPanelMembers
from .color_panel_widgets import CheckClickArea
from .logging_setup import get_logger

log = get_logger(__name__)


class ColorSelectionMixin(UsedColorPanelMembers):
    def selected_rgb_set(self):
        return set(self.selected_rgbs)

    def select_matching_color(self, color):
        """スポイト色と完全一致する使用色を、単独の親色として選択する。"""
        qc = QColor(color)
        rgb = self._rgb_key(qc)
        if (
            not qc.isValid()
            or rgb == self.background_rgb
            or rgb not in self.source_buttons
        ):
            self._clear_used_color_selection()
            return False

        old = set(self.selected_rgbs)
        changed = self.selected_rgbs != [rgb] or self.parent_rgb != rgb
        self.selected_rgbs = [rgb]
        self.parent_rgb = rgb
        self._selection_anchor_rgb = rgb
        self._refresh_used_color_styles(old | {rgb})
        row_widget = self.row_widgets.get(rgb)
        if row_widget is not None:
            self.scroll.ensureWidgetVisible(row_widget)
        if changed:
            self.selectedColorsChanged.emit({rgb})
        return True

    def _sync_selection_check(self, rgb):
        checkbox = self.selection_checks.get(rgb)
        if checkbox is None:
            return
        selected = rgb in self.selected_rgbs
        if checkbox.isChecked() != selected:
            checkbox.blockSignals(True)
            checkbox.setChecked(selected)
            checkbox.blockSignals(False)
        else:
            checkbox.update()

    def _ordered_non_background_rgbs(self):
        return [
            self._rgb_key(color) for color in self.colors
            if self._rgb_key(color) != self.background_rgb
        ]

    def _select_used_color(self, rgb, modifiers=Qt.KeyboardModifier.NoModifier):
        """クリック＝単独選択／Shift＝範囲選択／Ctrl＝個別トグル。"""
        if rgb == self.background_rgb:
            return
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)

        if shift and self._selection_anchor_rgb:
            ordered = self._ordered_non_background_rgbs()
            if rgb in ordered and self._selection_anchor_rgb in ordered:
                start = ordered.index(self._selection_anchor_rgb)
                end = ordered.index(rgb)
                lo, hi = sorted((start, end))
                old = set(self.selected_rgbs)
                # 直前選択からクリック色までを範囲としてまとめ選択する。
                self.selected_rgbs = list(ordered[lo:hi + 1])
                self.parent_rgb = rgb
                self._refresh_used_color_styles(old | set(self.selected_rgbs))
                self.selectedColorsChanged.emit(set(self.selected_rgbs))
                return

        if ctrl:
            # 個別に追加／解除。
            self._selection_anchor_rgb = rgb
            self._toggle_used_color_selection(rgb)
            return

        # 修飾なしクリックは、その色だけを単独選択する。
        self._selection_anchor_rgb = rgb
        self._select_single_used_color(rgb)

    def _select_single_used_color(self, rgb):
        if rgb == self.background_rgb:
            return
        old = set(self.selected_rgbs)
        self.selected_rgbs = [rgb]
        self.parent_rgb = rgb
        self._selection_anchor_rgb = rgb
        self._refresh_used_color_styles(old | {rgb})
        self.selectedColorsChanged.emit({rgb})

    # ------------------------------------------------------------------
    # 「選択」チェックボックス（クリック連動・上下スイープ一括選択）
    # ------------------------------------------------------------------
    def _apply_selection_state(self, rgb, selected):
        """1色分の選択状態を更新する（emitは呼び出し側で行う）。"""
        if rgb == self.background_rgb:
            return
        was = rgb in self.selected_rgbs
        if bool(selected) == was:
            return
        if selected:
            self.selected_rgbs.append(rgb)
        else:
            self.selected_rgbs.remove(rgb)
        self.parent_rgb = self.selected_rgbs[-1] if self.selected_rgbs else None
        self._selection_anchor_rgb = rgb
        self._set_source_button_style(rgb)
        self._selection_sweep_changed = True

    def _on_selection_check_toggled(self, rgb, checked):
        if self._selection_sweep_active:
            return
        self._selection_sweep_changed = False
        self._apply_selection_state(rgb, checked)
        if self._selection_sweep_changed:
            self.selectedColorsChanged.emit(set(self.selected_rgbs))
        self._selection_sweep_changed = False

    def _selection_check_rgb_at_global(self, global_position):
        for rgb, checkbox in self.selection_checks.items():
            area = checkbox.parentWidget()
            target = area if isinstance(area, CheckClickArea) else checkbox
            if target.rect().contains(target.mapFromGlobal(global_position)):
                return rgb
        return None

    def _begin_selection_sweep(self, source_rgb, checked):
        self._selection_sweep_active = True
        self._selection_sweep_state = bool(checked)
        self._selection_sweep_touched = {source_rgb}
        self._selection_sweep_changed = False
        self._apply_selection_state(source_rgb, checked)

    def _move_selection_sweep(self, global_position):
        if not self._selection_sweep_active:
            return
        rgb = self._selection_check_rgb_at_global(global_position)
        if (
            rgb is None
            or rgb in self._selection_sweep_touched
            or rgb == self.background_rgb
        ):
            return
        self._selection_sweep_touched.add(rgb)
        self._apply_selection_state(rgb, self._selection_sweep_state)

    def _end_selection_sweep(self):
        if not self._selection_sweep_active:
            return
        self._selection_sweep_active = False
        if self._selection_sweep_changed:
            self.selectedColorsChanged.emit(set(self.selected_rgbs))
        self._selection_sweep_touched.clear()
        self._selection_sweep_changed = False

    # ------------------------------------------------------------------
    # ドラッグ＆ドロップによる並べ替え・親子付け（非破壊プレビュー）
    # ------------------------------------------------------------------
    def _toggle_used_color_selection(self, rgb):
        if rgb == self.background_rgb:
            return
        affected = set(self.selected_rgbs)
        if rgb in self.selected_rgbs:
            self.selected_rgbs.remove(rgb)
        else:
            self.selected_rgbs.append(rgb)
        self.parent_rgb = self.selected_rgbs[-1] if self.selected_rgbs else None
        affected.update(self.selected_rgbs)
        for key in affected:
            self._set_source_button_style(key)
        self.selectedColorsChanged.emit(set(self.selected_rgbs))

    def _clear_used_color_selection(self):
        old = set(self.selected_rgbs)
        changed = bool(old) or self.parent_rgb is not None
        self.selected_rgbs = []
        self.parent_rgb = None
        self._selection_anchor_rgb = None
        self._refresh_used_color_styles(old)
        if changed:
            self.selectedColorsChanged.emit(set())
