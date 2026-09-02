"""Mask-colour checkboxes for the used-color panel.

Split out of ``color_panel.py`` as a mixin. A masked colour is one the transform
tools treat as protected; these methods toggle that state for one colour or all
of them, run the press-and-sweep drag that toggles a run of checkboxes in one
gesture, and provide the mask context menu.
"""
from PySide6.QtWidgets import QMenu
from .i18n import tr
from ._color_panel_members import UsedColorPanelMembers
from .color_panel_widgets import CheckClickArea
from .logging_setup import get_logger

log = get_logger(__name__)


class MaskColorsMixin(UsedColorPanelMembers):
    def all_masks_enabled(self):
        keys = set(self.mask_checks)
        return bool(keys) and keys.issubset(self.mask_rgbs)

    def _emit_mask_state(self):
        self._mask_all_mode = self.all_masks_enabled()
        self.maskColorsChanged.emit(set(self.mask_rgbs))

    def _set_mask_color(self, rgb, checked):
        if self._mask_sweep_active:
            if checked:
                self.mask_rgbs.add(rgb)
            else:
                self.mask_rgbs.discard(rgb)
            self._emit_mask_state()
            return
        with self._history_edit(tr("マスクの切り替え")):
            if checked:
                self.mask_rgbs.add(rgb)
            else:
                self.mask_rgbs.discard(rgb)
            self._emit_mask_state()

    def _set_mask_checkbox_without_signal(self, rgb, checked):
        checkbox = self.mask_checks.get(rgb)
        if checkbox is not None and checkbox.isChecked() != bool(checked):
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(checked))
            checkbox.blockSignals(False)

    def _set_all_masks_on(self):
        with self._history_edit(tr("マスクを全体ON")):
            self.mask_rgbs = set(self.mask_checks)
            for rgb in self.mask_checks:
                self._set_mask_checkbox_without_signal(rgb, True)
            self._emit_mask_state()

    def _clear_mask_colors(self):
        """Compatibility helper: turn every mask OFF."""
        with self._history_edit(tr("マスクを全体OFF")):
            self.mask_rgbs.clear()
            for rgb in self.mask_checks:
                self._set_mask_checkbox_without_signal(rgb, False)
            self._emit_mask_state()

    def _isolate_mask_color(self, rgb):
        """Alt+click: enable only the clicked mask."""
        with self._history_edit(tr("この色だけマスクON")):
            self.mask_rgbs = {rgb}
            for key in self.mask_checks:
                self._set_mask_checkbox_without_signal(key, key == rgb)
            self._emit_mask_state()

    def _disable_other_masks(self, rgb):
        """右クリック対象だけをONにし、それ以外のマスクをOFFにする。"""
        with self._history_edit(tr("対象以外のマスクOFF")):
            self.mask_rgbs = {rgb}
            for key in self.mask_checks:
                self._set_mask_checkbox_without_signal(key, key == rgb)
            self._emit_mask_state()

    def _set_single_mask_state(self, rgb, enabled):
        with self._history_edit(tr("マスクの切り替え")):
            self._set_mask_checkbox_without_signal(rgb, enabled)
            if enabled:
                self.mask_rgbs.add(rgb)
            else:
                self.mask_rgbs.discard(rgb)
            self._emit_mask_state()

    def _begin_mask_sweep(self, source_rgb, checked):
        self._sweep_history_before = self.capture_history_state()
        self._mask_sweep_active = True
        self._mask_sweep_state = bool(checked)
        self._mask_sweep_touched = {source_rgb}
        old = source_rgb in self.mask_rgbs
        self._set_mask_checkbox_without_signal(source_rgb, checked)
        if checked:
            self.mask_rgbs.add(source_rgb)
        else:
            self.mask_rgbs.discard(source_rgb)
        self._mask_sweep_changed = old != bool(checked)

    def _mask_checkbox_rgb_at_global(self, global_position):
        for rgb, checkbox in self.mask_checks.items():
            area = checkbox.parentWidget()
            target = area if isinstance(area, CheckClickArea) else checkbox
            if target.rect().contains(target.mapFromGlobal(global_position)):
                return rgb
        return None

    def _move_mask_sweep(self, global_position):
        if not self._mask_sweep_active:
            return
        rgb = self._mask_checkbox_rgb_at_global(global_position)
        if rgb is None or rgb in self._mask_sweep_touched:
            return
        self._mask_sweep_touched.add(rgb)
        old = rgb in self.mask_rgbs
        self._set_mask_checkbox_without_signal(rgb, self._mask_sweep_state)
        if self._mask_sweep_state:
            self.mask_rgbs.add(rgb)
        else:
            self.mask_rgbs.discard(rgb)
        self._mask_sweep_changed |= old != self._mask_sweep_state

    def _end_mask_sweep(self):
        if not self._mask_sweep_active:
            return
        self._mask_sweep_active = False
        if self._mask_sweep_changed:
            self._emit_mask_state()
            self._record_history(tr("マスクの一括切り替え"), self._sweep_history_before)
        self._sweep_history_before = None
        self._mask_sweep_touched.clear()
        self._mask_sweep_changed = False

    def _show_mask_context_menu(self, rgb, global_position):
        menu = QMenu(self)
        action_on = menu.addAction("ON")
        action_off = menu.addAction("OFF")
        menu.addSeparator()
        action_others = menu.addAction(tr("対象以外をOFF"))
        action_all = menu.addAction(tr("全表示"))
        chosen = menu.exec(global_position)
        if chosen is action_on:
            self._set_single_mask_state(rgb, True)
        elif chosen is action_off:
            self._set_single_mask_state(rgb, False)
        elif chosen is action_others:
            self._disable_other_masks(rgb)
        elif chosen is action_all:
            self._set_all_masks_on()
