"""Onion skin; owned by ``MainWindow`` as ``window.onion``.

Toggles onion-skin display, owns the floating settings window, and implements
the "center canvas between onion shifts" transform.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from typing import TYPE_CHECKING

import math
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor

from .i18n import tr
from .onion import OnionSkinSettingsBrowser
from .tool_window import ToolWindow

if TYPE_CHECKING:
    from .main_window import MainWindow


class OnionSkinController:
    """Owned by ``MainWindow`` as ``window.onion``.

    A collaborator rather than a mixin -- see main_window_export.py for why.
    """

    def __init__(self, window: "MainWindow"):
        self.window = window

    """Onion-skin toggles and the onion settings window."""

    def set_all_layers(self, enabled):
        self.window.canvas.onion_all_layers = bool(enabled)
        self.window.canvas._onion_cache.clear()
        self.window.canvas.update()

    def set_enabled(self, enabled):
        self.window.canvas.onion_skin = bool(enabled)
        self.window.canvas._onion_cache.clear()
        self.window.canvas.update()

    def toggle_settings_popup(self, checked):
        checked = bool(checked)
        if checked:
            self.show_settings()
        else:
            settings_window = self.window._onion_settings_window
            if settings_window is not None:
                settings_window.close()
        self.window.canvas.update()

    def show_settings(self):
        browser = self.window._onion_settings_browser
        if browser is not None:
            if self.window._onion_settings_window is not None:
                self.window._onion_settings_window.show_and_raise()
            return

        browser = OnionSkinSettingsBrowser(
            self.window.canvas.onion_previous_count,
            self.window.canvas.onion_next_count,
            self.window.canvas.onion_previous_opacity,
            self.window.canvas.onion_next_opacity,
            self.window.canvas.onion_previous_color,
            self.window.canvas.onion_next_color,
            self.window.canvas.onion_previous_color_enabled,
            self.window.canvas.onion_next_color_enabled,
            self.window.canvas.onion_selected_colors_only,
            self.window.canvas.onion_previous_shift_x,
            self.window.canvas.onion_previous_shift_y,
            self.window.canvas.onion_previous_rotation,
            self.window.canvas.onion_next_shift_x,
            self.window.canvas.onion_next_shift_y,
            self.window.canvas.onion_next_rotation,
            self.window.canvas.onion_previous_scale,
            self.window.canvas.onion_next_scale,
            self.window.canvas.onion_previous_levels,
            self.window.canvas.onion_next_levels,
            self.window.canvas.onion_center_percent,
            self.window.canvas.rotation,
            self.window,
        )
        self.window._onion_settings_browser = browser
        browser.settingsChanged.connect(
            self.apply_browser_settings
        )
        browser.shiftEditRequested.connect(
            self.window.canvas.begin_onion_shift_interaction
        )
        browser.canvasPositionEditRequested.connect(
            self.window.canvas.begin_onion_canvas_position_interaction
        )
        browser.canvasRotationRequested.connect(
            self.window.canvas.set_onion_canvas_view_rotation
        )
        browser.centerCanvasRequested.connect(
            self.center_canvas_between_shifts
        )
        browser.destroyed.connect(
            self._settings_browser_destroyed
        )

        # パネルの列に差し込むのではなく、キャンバスの上に浮かぶ別ウィンドウで
        # 開く。閉じると中身ごと破棄し、次に開くときは現在の設定から作り直す。
        settings_window = ToolWindow(
            self.window, tr("オニオンスキン設定"), "onion_settings", browser,
        )
        settings_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.window._onion_settings_window = settings_window
        settings_window.show_and_raise()

    def _settings_browser_destroyed(self, *_args):
        self.window._onion_settings_browser = None
        self.window._onion_settings_window = None
        self.window.canvas.cancel_onion_interaction(restore=False)
        if self.window.timeline.onion_settings.isChecked():
            self.window.timeline.onion_settings.blockSignals(True)
            self.window.timeline.onion_settings.setChecked(False)
            self.window.timeline.onion_settings.blockSignals(False)

    def apply_browser_settings(self):
        browser = self.window._onion_settings_browser
        if browser is None:
            return

        self.window.canvas.onion_previous_count = (
            browser.previous_count.value()
        )
        self.window.canvas.onion_next_count = (
            browser.next_count.value()
        )
        self.window.canvas.onion_previous_opacity = (
            browser.previous_opacity.value() / 100.0
        )
        self.window.canvas.onion_next_opacity = (
            browser.next_opacity.value() / 100.0
        )
        self.window.canvas.onion_previous_levels = (
            browser.previous_levels()
        )
        self.window.canvas.onion_next_levels = (
            browser.next_levels()
        )
        self.window.canvas.onion_previous_color = QColor(
            browser.previous_color
        )
        self.window.canvas.onion_next_color = QColor(
            browser.next_color
        )
        self.window.canvas.onion_previous_color_enabled = (
            browser.previous_color_enabled.isChecked()
        )
        self.window.canvas.onion_next_color_enabled = (
            browser.next_color_enabled.isChecked()
        )
        self.window.canvas.onion_selected_colors_only = (
            browser.selected_colors_only.isChecked()
        )
        self.window.canvas.onion_previous_shift_x = float(
            browser.previous_shift_x.value()
        )
        self.window.canvas.onion_previous_shift_y = float(
            browser.previous_shift_y.value()
        )
        self.window.canvas.onion_previous_rotation = float(
            browser.previous_rotation.value()
        )
        self.window.canvas.onion_next_shift_x = float(
            browser.next_shift_x.value()
        )
        self.window.canvas.onion_next_shift_y = float(
            browser.next_shift_y.value()
        )
        self.window.canvas.onion_next_rotation = float(
            browser.next_rotation.value()
        )
        self.window.canvas.onion_previous_scale = float(
            browser.previous_scale.value()
        )
        self.window.canvas.onion_next_scale = float(
            browser.next_scale.value()
        )
        self.window.canvas.onion_center_percent = float(
            browser.center_percent_value.value()
        )

        self.window.canvas._onion_cache.clear()
        self.window.canvas.update()

    def sync_browser_from_canvas(self):
        browser = self.window._onion_settings_browser
        if browser is None:
            return
        browser.set_transform_values(
            self.window.canvas.onion_previous_shift_x,
            self.window.canvas.onion_previous_shift_y,
            self.window.canvas.onion_previous_rotation,
            self.window.canvas.onion_previous_scale,
            self.window.canvas.onion_next_shift_x,
            self.window.canvas.onion_next_shift_y,
            self.window.canvas.onion_next_rotation,
            self.window.canvas.onion_next_scale,
        )
        browser.set_canvas_rotation_value(
            self.window.canvas.rotation
        )

    def finish_browser_interaction(self):
        browser = self.window._onion_settings_browser
        if browser is not None:
            browser.clear_interaction_buttons()

    def center_canvas_between_shifts(self, percent):
        """前後間のTU/TB変形を現在キャンバスの基準へ取り込む。"""
        self.apply_browser_settings()
        percent = max(0.0, min(100.0, float(percent)))
        self.window.canvas.onion_center_percent = percent
        ratio = percent / 100.0

        previous_x = float(self.window.canvas.onion_previous_shift_x)
        previous_y = float(self.window.canvas.onion_previous_shift_y)
        next_x = float(self.window.canvas.onion_next_shift_x)
        next_y = float(self.window.canvas.onion_next_shift_y)
        previous_rotation = float(
            self.window.canvas.onion_previous_rotation
        )
        next_rotation = float(
            self.window.canvas.onion_next_rotation
        )
        previous_scale = max(
            0.01,
            float(self.window.canvas.onion_previous_scale) / 100.0,
        )
        next_scale = max(
            0.01,
            float(self.window.canvas.onion_next_scale) / 100.0,
        )

        target_x = previous_x + (next_x - previous_x) * ratio
        target_y = previous_y + (next_y - previous_y) * ratio

        # 回転は最短方向、TU/TB拡大率は撮影倍率として線形補間する。
        rotation_delta = (
            (next_rotation - previous_rotation + 180.0)
            % 360.0
        ) - 180.0
        target_rotation = self.window.canvas._normalized_angle(
            previous_rotation + rotation_delta * ratio
        )
        target_scale = max(
            0.01,
            previous_scale + (next_scale - previous_scale) * ratio,
        )

        # 表示用デジタルズームself.canvas.zoomは変更しない。
        digital_zoom = max(0.01, float(self.window.canvas.zoom))
        current_tu_tb = max(
            0.0001,
            float(self.window.canvas.onion_tu_tb_scale),
        )
        old_view_rotation = float(self.window.canvas.rotation)
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

        self.window.canvas.pan += QPointF(display_x, display_y)
        self.window.canvas.rotation = self.window.canvas._normalized_angle(
            old_view_rotation + target_rotation
        )
        self.window.canvas.onion_tu_tb_scale = max(
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
        absorbed_view_rotation = float(self.window.canvas.rotation)
        previous_screen_relative = self.window.canvas._rotated_vector(
            previous_relative.x(),
            previous_relative.y(),
            absorbed_view_rotation,
        )
        next_screen_relative = self.window.canvas._rotated_vector(
            next_relative.x(),
            next_relative.y(),
            absorbed_view_rotation,
        )

        self.window.canvas.onion_previous_shift_x = (
            previous_screen_relative.x()
        )
        self.window.canvas.onion_previous_shift_y = (
            previous_screen_relative.y()
        )
        self.window.canvas.onion_next_shift_x = (
            next_screen_relative.x()
        )
        self.window.canvas.onion_next_shift_y = (
            next_screen_relative.y()
        )
        self.window.canvas.onion_previous_rotation = (
            self.window.canvas._normalized_angle(
                previous_rotation - target_rotation
            )
        )
        self.window.canvas.onion_next_rotation = (
            self.window.canvas._normalized_angle(
                next_rotation - target_rotation
            )
        )
        self.window.canvas.rotation = 0.0
        self.window.canvas.onion_previous_scale = max(
            1.0,
            min(199.0, previous_scale / target_scale * 100.0),
        )
        self.window.canvas.onion_next_scale = max(
            1.0,
            min(199.0, next_scale / target_scale * 100.0),
        )

        self.sync_browser_from_canvas()
        self.window.canvas.viewChanged.emit(
            float(self.window.canvas.zoom),
            float(self.window.canvas.rotation),
        )
        self.window.canvas.update()
        self.window.statusBar().showMessage(
            tr("前後の位置・回転・TU/TB拡大率間の{percent:g}%をキャンバス基準へ移しました。（中央回転 {rotation:.1f}°を基準として取得／撮影倍率 {value:.1f}%／キャンバス回転 0°／デジタルズーム変更なし）").format(percent=percent, rotation=target_rotation, value=target_scale * 100.0),
            4200,
        )
