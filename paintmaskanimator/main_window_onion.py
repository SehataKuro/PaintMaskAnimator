"""Onion-skin display + settings-browser wiring for :class:`MainWindow`.

Split out of ``main_window.py`` as a mixin. These methods toggle onion-skin
display, own the dockable settings browser, and implement the
"center canvas between onion shifts" transform. They run against a live
``MainWindow`` instance (``self.canvas``, ``self.timeline``, ``self.dock_manager``).
"""
import math

import PySide6QtAds as QtAds
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor

from .onion import OnionSkinSettingsBrowser
from ._main_window_members import MainWindowMembers


class OnionSkinMixin(MainWindowMembers):
    """Onion-skin toggles and the onion settings browser dock."""

    def set_onion_all_layers(self, enabled):
        self.canvas.onion_all_layers = bool(enabled)
        self.canvas._onion_cache.clear()
        self.canvas.update()

    def set_onion_skin(self, enabled):
        self.canvas.onion_skin = bool(enabled)
        self.canvas._onion_cache.clear()
        self.canvas.update()

    def toggle_onion_settings_popup(self, checked):
        checked = bool(checked)
        if checked:
            self.show_onion_settings()
        else:
            onion_dock = self._onion_settings_dock
            if onion_dock is not None:
                onion_dock.closeDockWidget()
        self.canvas.update()

    def show_onion_settings(self):
        browser = self._onion_settings_browser
        if browser is not None:
            if self._onion_settings_dock is not None:
                self._onion_settings_dock.toggleView(True)
                self._onion_settings_dock.raise_()
            return

        browser = OnionSkinSettingsBrowser(
            self.canvas.onion_previous_count,
            self.canvas.onion_next_count,
            self.canvas.onion_previous_opacity,
            self.canvas.onion_next_opacity,
            self.canvas.onion_previous_color,
            self.canvas.onion_next_color,
            self.canvas.onion_previous_color_enabled,
            self.canvas.onion_next_color_enabled,
            self.canvas.onion_selected_colors_only,
            self.canvas.onion_previous_shift_x,
            self.canvas.onion_previous_shift_y,
            self.canvas.onion_previous_rotation,
            self.canvas.onion_next_shift_x,
            self.canvas.onion_next_shift_y,
            self.canvas.onion_next_rotation,
            self.canvas.onion_previous_scale,
            self.canvas.onion_next_scale,
            self.canvas.onion_previous_levels,
            self.canvas.onion_next_levels,
            self.canvas.onion_center_percent,
            self.canvas.rotation,
            self,
        )
        self._onion_settings_browser = browser
        browser.settingsChanged.connect(
            self.apply_onion_browser_settings
        )
        browser.shiftEditRequested.connect(
            self.canvas.begin_onion_shift_interaction
        )
        browser.canvasPositionEditRequested.connect(
            self.canvas.begin_onion_canvas_position_interaction
        )
        browser.canvasRotationRequested.connect(
            self.canvas.set_onion_canvas_view_rotation
        )
        browser.centerCanvasRequested.connect(
            self.center_canvas_between_onion_shifts
        )
        browser.destroyed.connect(
            self._onion_settings_browser_destroyed
        )

        onion_dock = QtAds.CDockWidget(
            self.dock_manager, "オニオンスキン設定"
        )
        onion_dock.setObjectName("onionSkinSettingsDock")
        onion_dock.setFeatures(
            QtAds.CDockWidget.DockWidgetFeature.DockWidgetClosable
            | QtAds.CDockWidget.DockWidgetFeature.DockWidgetMovable
            | QtAds.CDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QtAds.CDockWidget.DockWidgetFeature.DockWidgetDeleteOnClose
            | QtAds.CDockWidget.DockWidgetFeature.DeleteContentOnClose
        )
        onion_dock.setWidget(
            browser, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        onion_dock.topLevelChanged.connect(
            lambda floating, current=onion_dock:
            self._sync_floating_title(current, floating)
        )
        self._onion_settings_dock = onion_dock
        palette_area = self.palette_dock.dockAreaWidget()
        self.dock_manager.addDockWidgetTabToArea(onion_dock, palette_area)
        self._add_dock_hamburger(onion_dock)
        onion_dock.toggleView(True)
        onion_dock.raise_()

    def _onion_settings_browser_destroyed(self, *_args):
        self._onion_settings_browser = None
        self._onion_settings_dock = None
        self.canvas.cancel_onion_interaction(restore=False)
        if self.timeline.onion_settings.isChecked():
            self.timeline.onion_settings.blockSignals(True)
            self.timeline.onion_settings.setChecked(False)
            self.timeline.onion_settings.blockSignals(False)

    def apply_onion_browser_settings(self):
        browser = self._onion_settings_browser
        if browser is None:
            return

        self.canvas.onion_previous_count = (
            browser.previous_count.value()
        )
        self.canvas.onion_next_count = (
            browser.next_count.value()
        )
        self.canvas.onion_previous_opacity = (
            browser.previous_opacity.value() / 100.0
        )
        self.canvas.onion_next_opacity = (
            browser.next_opacity.value() / 100.0
        )
        self.canvas.onion_previous_levels = (
            browser.previous_levels()
        )
        self.canvas.onion_next_levels = (
            browser.next_levels()
        )
        self.canvas.onion_previous_color = QColor(
            browser.previous_color
        )
        self.canvas.onion_next_color = QColor(
            browser.next_color
        )
        self.canvas.onion_previous_color_enabled = (
            browser.previous_color_enabled.isChecked()
        )
        self.canvas.onion_next_color_enabled = (
            browser.next_color_enabled.isChecked()
        )
        self.canvas.onion_selected_colors_only = (
            browser.selected_colors_only.isChecked()
        )
        self.canvas.onion_previous_shift_x = float(
            browser.previous_shift_x.value()
        )
        self.canvas.onion_previous_shift_y = float(
            browser.previous_shift_y.value()
        )
        self.canvas.onion_previous_rotation = float(
            browser.previous_rotation.value()
        )
        self.canvas.onion_next_shift_x = float(
            browser.next_shift_x.value()
        )
        self.canvas.onion_next_shift_y = float(
            browser.next_shift_y.value()
        )
        self.canvas.onion_next_rotation = float(
            browser.next_rotation.value()
        )
        self.canvas.onion_previous_scale = float(
            browser.previous_scale.value()
        )
        self.canvas.onion_next_scale = float(
            browser.next_scale.value()
        )
        self.canvas.onion_center_percent = float(
            browser.center_percent_value.value()
        )

        self.canvas._onion_cache.clear()
        self.canvas.update()

    def sync_onion_browser_from_canvas(self):
        browser = self._onion_settings_browser
        if browser is None:
            return
        browser.set_transform_values(
            self.canvas.onion_previous_shift_x,
            self.canvas.onion_previous_shift_y,
            self.canvas.onion_previous_rotation,
            self.canvas.onion_previous_scale,
            self.canvas.onion_next_shift_x,
            self.canvas.onion_next_shift_y,
            self.canvas.onion_next_rotation,
            self.canvas.onion_next_scale,
        )
        browser.set_canvas_rotation_value(
            self.canvas.rotation
        )

    def finish_onion_browser_interaction(self):
        browser = self._onion_settings_browser
        if browser is not None:
            browser.clear_interaction_buttons()

    def center_canvas_between_onion_shifts(self, percent):
        """前後間のTU/TB変形を現在キャンバスの基準へ取り込む。"""
        self.apply_onion_browser_settings()
        percent = max(0.0, min(100.0, float(percent)))
        self.canvas.onion_center_percent = percent
        ratio = percent / 100.0

        previous_x = float(self.canvas.onion_previous_shift_x)
        previous_y = float(self.canvas.onion_previous_shift_y)
        next_x = float(self.canvas.onion_next_shift_x)
        next_y = float(self.canvas.onion_next_shift_y)
        previous_rotation = float(
            self.canvas.onion_previous_rotation
        )
        next_rotation = float(
            self.canvas.onion_next_rotation
        )
        previous_scale = max(
            0.01,
            float(self.canvas.onion_previous_scale) / 100.0,
        )
        next_scale = max(
            0.01,
            float(self.canvas.onion_next_scale) / 100.0,
        )

        target_x = previous_x + (next_x - previous_x) * ratio
        target_y = previous_y + (next_y - previous_y) * ratio

        # 回転は最短方向、TU/TB拡大率は撮影倍率として線形補間する。
        rotation_delta = (
            (next_rotation - previous_rotation + 180.0)
            % 360.0
        ) - 180.0
        target_rotation = self.canvas._normalized_angle(
            previous_rotation + rotation_delta * ratio
        )
        target_scale = max(
            0.01,
            previous_scale + (next_scale - previous_scale) * ratio,
        )

        # 表示用デジタルズームself.canvas.zoomは変更しない。
        digital_zoom = max(0.01, float(self.canvas.zoom))
        current_tu_tb = max(
            0.0001,
            float(self.canvas.onion_tu_tb_scale),
        )
        old_view_rotation = float(self.canvas.rotation)
        view_angle = math.radians(old_view_rotation)
        local_display_x = (
            target_x * digital_zoom * current_tu_tb
        )
        local_display_y = (
            target_y * digital_zoom * current_tu_tb
        )
        display_x = (
            local_display_x * math.cos(view_angle)
            - local_display_y * math.sin(view_angle)
        )
        display_y = (
            local_display_x * math.sin(view_angle)
            + local_display_y * math.cos(view_angle)
        )

        self.canvas.pan += QPointF(display_x, display_y)
        self.canvas.rotation = self.canvas._normalized_angle(
            old_view_rotation + target_rotation
        )
        self.canvas.onion_tu_tb_scale = max(
            0.05,
            min(20.0, current_tu_tb * target_scale),
        )

        # H_target^-1 × H_eachで相対位置・回転・倍率を再取得。
        angle = math.radians(-target_rotation)
        cos_angle = math.cos(angle)
        sin_angle = math.sin(angle)

        def relative_shift(source_x, source_y):
            dx = float(source_x) - target_x
            dy = float(source_y) - target_y
            return QPointF(
                (dx * cos_angle - dy * sin_angle)
                / target_scale,
                (dx * sin_angle + dy * cos_angle)
                / target_scale,
            )

        previous_relative = relative_shift(
            previous_x,
            previous_y,
        )
        next_relative = relative_shift(next_x, next_y)

        # 選択した中央％の回転を新しい0°基準として取得する。
        # キャンバス表示角度は0°へ戻し、前後の角度は
        # 取得した中央回転との差分として再設定する。
        absorbed_view_rotation = float(self.canvas.rotation)
        previous_screen_relative = self.canvas._rotated_vector(
            previous_relative.x(),
            previous_relative.y(),
            absorbed_view_rotation,
        )
        next_screen_relative = self.canvas._rotated_vector(
            next_relative.x(),
            next_relative.y(),
            absorbed_view_rotation,
        )

        self.canvas.onion_previous_shift_x = (
            previous_screen_relative.x()
        )
        self.canvas.onion_previous_shift_y = (
            previous_screen_relative.y()
        )
        self.canvas.onion_next_shift_x = (
            next_screen_relative.x()
        )
        self.canvas.onion_next_shift_y = (
            next_screen_relative.y()
        )
        self.canvas.onion_previous_rotation = (
            self.canvas._normalized_angle(
                previous_rotation - target_rotation
            )
        )
        self.canvas.onion_next_rotation = (
            self.canvas._normalized_angle(
                next_rotation - target_rotation
            )
        )
        self.canvas.rotation = 0.0
        self.canvas.onion_previous_scale = max(
            1.0,
            min(199.0, previous_scale / target_scale * 100.0),
        )
        self.canvas.onion_next_scale = max(
            1.0,
            min(199.0, next_scale / target_scale * 100.0),
        )

        self.sync_onion_browser_from_canvas()
        self.canvas.viewChanged.emit(
            float(self.canvas.zoom),
            float(self.canvas.rotation),
        )
        self.canvas.update()
        self.statusBar().showMessage(
            f"前後の位置・回転・TU/TB拡大率間の{percent:g}%を"
            "キャンバス基準へ移しました。"
            f"（中央回転 {target_rotation:.1f}°を基準として取得／"
            f"撮影倍率 {target_scale * 100.0:.1f}%／"
            "キャンバス回転 0°／デジタルズーム変更なし）",
            4200,
        )
