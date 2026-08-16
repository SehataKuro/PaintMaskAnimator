"""Per-colour visibility for the used-color panel.

Split out of ``color_panel.py`` as a mixin. These methods drive the eye
checkboxes: showing or hiding a single colour, isolating one, restoring all, and
the press-and-sweep drag that toggles a run of rows in one gesture. Changes are
published to the canvas through ``visibleColorsChanged``.
"""
from .common import *  # noqa: F401,F403
from ._color_panel_members import UsedColorPanelMembers
from .logging_setup import get_logger

log = get_logger(__name__)


class ColorVisibilityMixin(UsedColorPanelMembers):
    def enabled_rgb_set(self):
        enabled = {
            rgb for rgb, is_enabled in self.enabled_colors.items() if is_enabled
        }
        return enabled

    def _show_all_colors(self):
        with self._history_edit("全表示"):
            self._isolated_rgb = None
            self._pre_isolate_enabled = None
            for rgb in self.visibility_checks:
                self._set_checkbox_without_signal(rgb, True)
            self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _set_visibility_state(self, rgb, enabled):
        with self._history_edit("表示の切り替え"):
            self._isolated_rgb = None
            self._pre_isolate_enabled = None
            self._set_checkbox_without_signal(rgb, enabled)
            self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _disable_other_visible_colors(self, rgb):
        """右クリック対象だけを表示し、それ以外をOFFにする。"""
        with self._history_edit("対象以外を非表示"):
            self._isolated_rgb = None
            self._pre_isolate_enabled = None
            for key in self.visibility_checks:
                self._set_checkbox_without_signal(
                    key,
                    key == rgb,
                )
            self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _show_visibility_context_menu(self, rgb, global_position):
        menu = QMenu(self)
        action_on = menu.addAction("ON")
        action_off = menu.addAction("OFF")
        menu.addSeparator()
        action_others = menu.addAction("対象以外をOFF")
        action_all = menu.addAction("全表示")
        chosen = menu.exec(global_position)
        if chosen is action_on:
            self._set_visibility_state(rgb, True)
        elif chosen is action_off:
            self._set_visibility_state(rgb, False)
        elif chosen is action_others:
            self._disable_other_visible_colors(rgb)
        elif chosen is action_all:
            self._show_all_colors()

    def _set_checkbox_without_signal(self, rgb, enabled):
        checkbox = self.visibility_checks.get(rgb)
        self.enabled_colors[rgb] = bool(enabled)
        if checkbox is not None and checkbox.isChecked() != bool(enabled):
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(enabled))
            checkbox.blockSignals(False)

    def _isolate_visible_color(self, source_rgb):
        with self._history_edit("この色だけ表示"):
            if (
                self._isolated_rgb == source_rgb
                and self._pre_isolate_enabled is not None
            ):
                restore = dict(self._pre_isolate_enabled)
                self._isolated_rgb = None
                self._pre_isolate_enabled = None
                for rgb in self.visibility_checks:
                    self._set_checkbox_without_signal(
                        rgb,
                        restore.get(rgb, True),
                    )
            else:
                self._pre_isolate_enabled = dict(self.enabled_colors)
                self._isolated_rgb = source_rgb
                for rgb in self.visibility_checks:
                    self._set_checkbox_without_signal(
                        rgb,
                        rgb == source_rgb,
                    )
            self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _begin_visibility_sweep(self, source_rgb, checked):
        self._sweep_history_before = self.capture_history_state()
        self._isolated_rgb = None
        self._pre_isolate_enabled = None
        self._visibility_sweep_active = True
        self._visibility_sweep_state = bool(checked)
        self._visibility_sweep_touched = {source_rgb}
        old = self.enabled_colors.get(source_rgb, True)
        self._set_checkbox_without_signal(source_rgb, checked)
        self._visibility_sweep_changed = old != bool(checked)

    def _checkbox_rgb_at_global(self, global_position):
        for rgb, checkbox in self.visibility_checks.items():
            if checkbox.rect().contains(
                checkbox.mapFromGlobal(global_position)
            ):
                return rgb
        return None

    def _move_visibility_sweep(self, global_position):
        if not self._visibility_sweep_active:
            return
        rgb = self._checkbox_rgb_at_global(global_position)
        if (
            rgb is None
            or rgb in self._visibility_sweep_touched
        ):
            return
        self._visibility_sweep_touched.add(rgb)
        old = self.enabled_colors.get(rgb, True)
        self._set_checkbox_without_signal(
            rgb, self._visibility_sweep_state
        )
        self._visibility_sweep_changed |= (
            old != self._visibility_sweep_state
        )

    def _end_visibility_sweep(self):
        if not self._visibility_sweep_active:
            return
        self._visibility_sweep_active = False
        if self._visibility_sweep_changed:
            self.visibleColorsChanged.emit(self.enabled_rgb_set())
            self._record_history("表示の一括切り替え", self._sweep_history_before)
        self._sweep_history_before = None
        self._visibility_sweep_touched.clear()
        self._visibility_sweep_changed = False

    def _set_color_visible(self, source_rgb, checked):
        if self._visibility_sweep_active:
            return
        with self._history_edit("表示の切り替え"):
            self._isolated_rgb = None
            self._pre_isolate_enabled = None
            self.enabled_colors[source_rgb] = bool(checked)
            self.visibleColorsChanged.emit(self.enabled_rgb_set())
