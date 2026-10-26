"""キャンバス下の表示チェック; owned by ``MainWindow`` as ``window.view_preview``.

ブラックチェック（背景以外を黒シルエット表示）・隣接色分け・チャンネル表示・
露光量を、キャンバスの下のバーにまとめる。どれも画面の見え方だけを変え、
画素データや書き出しには影響しない。計算は :mod:`view_preview` と
``PaintCanvas.view_layer_image`` が持ち、ここはボタンと設定の同期だけを持つ。

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker

from . import view_preview
from .i18n import tr

if TYPE_CHECKING:
    from .main_window import MainWindow


def channel_labels():
    """チャンネル表示の選択肢 ``(値, 表示名)``。呼び出し時に翻訳する。"""
    return (
        (view_preview.CHANNEL_RGB, tr("RGB")),
        (view_preview.CHANNEL_RED, tr("赤")),
        (view_preview.CHANNEL_GREEN, tr("緑")),
        (view_preview.CHANNEL_BLUE, tr("青")),
        (view_preview.CHANNEL_ALPHA, tr("アルファ")),
    )


class ViewPreviewController:
    """Owned by ``MainWindow`` as ``window.view_preview``."""

    def __init__(self, window: "MainWindow"):
        self.window = window
        # 露光量のボタンで 0 に戻す前の値。もう一度押すとこの値に戻す（AE と同じ）。
        self._last_exposure = 1.0

    @property
    def _settings(self):
        return self.window.canvas.view_preview

    # ------------------------------------------------------------------
    # 隣接色分け
    # ------------------------------------------------------------------
    def set_map_colors(self, checked):
        self.window.canvas.set_view_preview(map_colors=bool(checked))
        self.sync()

    def toggle_map_colors(self):
        self.set_map_colors(not self._settings.map_colors)

    # ------------------------------------------------------------------
    # チャンネル
    # ------------------------------------------------------------------
    def set_channel(self, channel):
        if channel not in view_preview.CHANNELS:
            channel = view_preview.CHANNEL_RGB
        # もう一度同じチャンネルを選んだら RGB に戻す（AE の Alt+1〜4 と同じ）。
        if channel == self._settings.channel and channel != view_preview.CHANNEL_RGB:
            channel = view_preview.CHANNEL_RGB
        self.window.canvas.set_view_preview(channel=channel)
        self.sync()

    def _channel_chosen(self, index):
        combo = self.window.channel_combo
        self.window.canvas.set_view_preview(channel=combo.itemData(index))
        self.sync()

    # ------------------------------------------------------------------
    # 露光量
    # ------------------------------------------------------------------
    def set_exposure(self, value):
        value = view_preview.clamp_exposure(value)
        if abs(value) > 1e-9:
            self._last_exposure = value
        self.window.canvas.set_view_preview(exposure=value)
        self.sync()

    def toggle_exposure(self):
        """露光量を 0 に戻す。0 のときは、直前に使っていた値に戻す。"""
        if abs(self._settings.exposure) > 1e-9:
            self.window.canvas.set_view_preview(exposure=0.0)
            self.sync()
        else:
            self.set_exposure(self._last_exposure)

    # ------------------------------------------------------------------
    # ボタンとメニューの表示を、今の設定に合わせる
    # ------------------------------------------------------------------
    def sync(self):
        window = self.window
        settings = self._settings
        button = getattr(window, "map_colors_button", None)
        if button is not None:
            with QSignalBlocker(button):
                button.setChecked(settings.map_colors)
        action = getattr(window, "a_preview_map", None)
        if action is not None:
            with QSignalBlocker(action):
                action.setChecked(settings.map_colors)
        combo = getattr(window, "channel_combo", None)
        if combo is not None:
            index = combo.findData(settings.channel)
            with QSignalBlocker(combo):
                combo.setCurrentIndex(max(0, index))
        spin = getattr(window, "exposure_spin", None)
        if spin is not None:
            with QSignalBlocker(spin):
                spin.setValue(settings.exposure)
        exposure_button = getattr(window, "exposure_button", None)
        if exposure_button is not None:
            with QSignalBlocker(exposure_button):
                exposure_button.setChecked(abs(settings.exposure) > 1e-9)

    def sync_black_check(self, checked):
        button = getattr(self.window, "black_check_button", None)
        if button is not None:
            with QSignalBlocker(button):
                button.setChecked(bool(checked))


__all__ = ["ViewPreviewController", "channel_labels"]
