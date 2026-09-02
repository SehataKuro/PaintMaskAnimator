"""Colour selection, sampling and mask colours; owned as ``window.colors``.

Handles the accent colour, the main/sub colour pickers, applying a sampled
colour, the mask/visible colour sets, colour-filter isolation, and keeping the
tool-selector swatch in sync.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QColorDialog, QDialog, QMessageBox
from . import theme
from .i18n import tr
from .widgets import TransformLineThicknessDialog
from .logging_setup import get_logger

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)


class ColorInteractionController:
    """Owned by ``MainWindow`` as ``window.colors``.

    A collaborator rather than a mixin -- see main_window_export.py for why.
    """

    def __init__(self, window: "MainWindow"):
        self.window = window

    @staticmethod
    def _is_background_sample(color):
        qc = QColor(color)
        return qc.isValid() and (qc.red(), qc.green(), qc.blue()) == (
            255, 255, 255
        )

    def _apply_sampled_drawing_color(self, mode, color):
        qc = QColor(color)
        if self._is_background_sample(qc):
            mode = "transparent"
            self.window.canvas.color_mode = mode
        else:
            mode = "sub" if mode == "sub" else "main"
            setattr(self.window.canvas, f"{mode}_color", qc)
            self.window.canvas.color_mode = mode
        self.window.tools.set_colors(
            self.window.canvas.main_color,
            self.window.canvas.sub_color,
            mode,
            self.window.canvas.transparent_display_color,
        )
        self._sync_tool_selector_swatch()
        return mode

    def choose_accent_color(self):
        current = QColor(theme.current_accent())
        color = QColorDialog.getColor(
            current, self.window, tr("アクセントカラーを選択")
        )
        if color.isValid():
            self.set_accent(color.name())

    def set_accent(self, color):
        """Change the accent colour app-wide, persist it, and restyle."""
        app = QApplication.instance()
        if app is not None:
            theme.set_accent(app, color, persist=True)
        self.window._refresh_theme_dependent_ui()

    def set_mask_colors(self, colors):
        normalized = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in colors
            if rgb is not None and len(rgb) >= 3
        }
        self.window.canvas.mask_color_rgbs = normalized
        self.window.canvas.mask_all_enabled = self.window.palette.all_masks_enabled()
        # 古い単色属性は互換用に残すが、複数選択時の判定には使用しない。
        self.window.canvas.mask_color_rgb = (
            next(iter(normalized)) if len(normalized) == 1 else None
        )
        if self.window.canvas.mask_all_enabled:
            self.window.statusBar().showMessage(
                tr("マスクは「全体」です。すべての領域に描画できます。"), 1800
            )
        elif normalized:
            self.window.statusBar().showMessage(
                tr("描画可能なマスクを {len}色選択しています。").format(len=len(normalized)), 1800
            )
        else:
            self.window.statusBar().showMessage(
                tr("すべてのマスクがOFFです。描画できません。"), 1800
            )

    def focus_used_color(self, rgb):
        """指定色の最初のコマへ移動し、その色全体を選択範囲で囲む。"""
        rgb = tuple(int(value) for value in rgb[:3])
        layer_index = int(self.window.canvas.active_layer_index)
        target_frame = None

        for frame_index, frame in enumerate(self.window.canvas.frames):
            if not (0 <= layer_index < len(frame.layers)):
                continue
            layer = frame.layers[layer_index]
            if not layer.has_content:
                continue
            cache_key = self.window.used_color._cache_key(layer.image)
            cached = self.window._used_color_cache.get(cache_key)
            if cached is not None:
                cached_colors, cached_exceeded = cached
                if rgb in cached_colors:
                    target_frame = frame_index
                    break
                if not cached_exceeded:
                    continue
            if self.window.line_ops._image_contains_rgb(layer.image, rgb):
                target_frame = frame_index
                break

        if target_frame is None:
            QMessageBox.information(
                self.window,
                tr("対象に注視"),
                tr("現在のレイヤー内に、この色が使われているコマはありません。"),
            )
            return

        visual_row = len(self.window.canvas.layers) - 1 - layer_index
        self.window.canvas.select_exposure(target_frame, visual_row)
        self.window.timeline.select_current(target_frame, layer_index)
        self.set_selected_used_colors({rgb})
        layer = self.window.canvas.frames[target_frame].layers[layer_index]
        rgba = self.window.canvas._qimage_rgba_array(layer.image)
        target = np.asarray(rgb, dtype=np.uint8)
        matching = (
            (rgba[:, :, 3] > 0)
            & np.all(rgba[:, :, :3] == target, axis=2)
        )
        contours = self.window.line_ops._mask_contours(matching)
        contour = self.window.line_ops._largest_contour(contours)
        if contour:
            self.window.canvas.selection_polygon = contour
            self.window.canvas.selection_mask_override = matching.copy()
            self.window.canvas.selection_outline_polygons = contours
            ys, xs = np.nonzero(matching)
            self.window.canvas.selection_mask_rect = QRectF(
                int(xs.min()),
                int(ys.min()),
                int(xs.max() - xs.min() + 1),
                int(ys.max() - ys.min() + 1),
            )
            self.window.canvas.lasso = []
            self.window.canvas.rect_start = None
            self.window.canvas.rect_end = None
            self.window.canvas.selectionChanged.emit()
            self.window.canvas.update()
        self.window.canvas.setFocus()
        self.window.statusBar().showMessage(
            tr("使用色 {rgb} が最初に現れる {value} コマ目へ移動しました。").format(rgb=rgb, value=target_frame + 1),
            3200,
        )

    def adjust_parent_line_thickness(self, colors):
        """●ーーーー｜全体を1つの画像として、選択色の線幅を調整する。"""
        if getattr(self.window.canvas, "tween_pending", None):
            self.window.statusBar().showMessage(
                tr("トゥイーン中は線の太さを変更できません。"),
                2600,
            )
            return

        line_colors = set()
        try:
            for color in colors:
                if color is not None and len(color) >= 3:
                    line_colors.add(tuple(
                        int(value) for value in color[:3]
                    ))
        except (TypeError, ValueError):
            line_colors = set()

        parent_rgb = tuple(self.window.palette.parent_rgb or ())
        if not line_colors:
            line_colors = {
                tuple(value)
                for value in self.window.palette.selected_rgbs
            }

        if not parent_rgb or parent_rgb not in line_colors:
            QMessageBox.information(
                self.window,
                tr("太さを調整"),
                tr("親として選択している色で右クリックしてください。"),
            )
            return

        if self.window.canvas.transform_active:
            QMessageBox.warning(
                self.window,
                tr("太さを調整"),
                tr("別の変形処理を確定またはキャンセルしてから"
                "実行してください。"),
            )
            return

        original_frame = int(self.window.canvas.current_frame)
        layer_index = int(self.window.canvas.active_layer_index)
        block = self.window.canvas.resolve_exposure_block(
            original_frame,
            layer_index,
        )
        if block is None:
            QMessageBox.information(
                self.window,
                tr("太さを調整"),
                tr("現在位置には調整できるキーフレームがありません。"),
            )
            return

        key_frame, block_end, exposure = block
        key_layer = self.window.canvas.frames[
            key_frame
        ].layers[layer_index]
        if not key_layer.has_content:
            QMessageBox.information(
                self.window,
                tr("太さを調整"),
                tr("現在の露出ブロックに画像がありません。"),
            )
            return

        previous_tool = self.window.tools.active_tool
        previous_quality = (
            self.window.tools.transform_quality.isChecked()
        )
        previous_threshold = (
            self.window.tools.transform_line_width.value()
        )
        previous_selected = set(self.window.palette.selected_rgbs)

        # 保持セルから実行しても、必ず●の画像本体へ移動して処理する。
        # 既存の部分選択は使わず、キーフレーム画像全体を対象にする。
        self.window.canvas.current_frame = key_frame
        self.window.canvas.active_layer_index = layer_index
        self.window.canvas.clear_selection_preserving_used_colors()

        if not self.window.canvas.auto_select_used_area():
            self.window.canvas.current_frame = original_frame
            QMessageBox.warning(
                self.window,
                tr("太さを調整"),
                tr("キーフレーム全体から描画領域を検出できません。"),
            )
            return

        self.window.tools.select_tool("rect_select")
        self.window.tools.transform_quality.setChecked(True)
        self.window.tools.transform_line_width.setValue(
            previous_threshold
        )
        self.window.line_ops.start_wire_transform(
            "free",
            line_colors_override=set(line_colors),
        )

        if not self.window.canvas.transform_active:
            self.window.canvas.current_frame = original_frame
            self.window.tools.transform_quality.setChecked(
                previous_quality
            )
            self.window.tools.select_tool(previous_tool)
            return

        dialog = TransformLineThicknessDialog(
            self.window.tools.transform_line_width.value(),
            self.window,
        )
        dialog.slider.valueChanged.connect(
            self.window.tools.transform_line_width.setValue
        )
        dialog.slider.sliderPressed.connect(
            self.window.canvas.begin_transform_line_adjustment
        )
        dialog.slider.sliderReleased.connect(
            self.window.canvas.finish_transform_line_adjustment
        )

        accepted = (
            dialog.exec() == QDialog.DialogCode.Accepted
        )
        if accepted:
            value = dialog.value()
            self.window.tools.transform_line_width.setValue(value)
            self.window.canvas.set_transform_line_threshold(value)
            self.window.canvas.commit_selection_transform(
                all_frames=False
            )
            # 画像は●にだけ保存し、ーーーー｜は同じ露出を参照する。
            key_layer = self.window.canvas.frames[
                key_frame
            ].layers[layer_index]
            key_layer.exposure = max(1, int(exposure))
            self.window.canvas.current_frame = min(
                original_frame,
                block_end,
            )
            self.window.canvas.active_layer_index = layer_index
            self.window.canvas._onion_cache.clear()
            self.window.canvas._color_filter_cache.clear()
            self.window.canvas._color_index_cache.clear()
            self.window.canvas._silhouette_cache.clear()
            self.window._used_color_cache.clear()
            self.window.canvas.cellChanged.emit(
                key_frame,
                layer_index,
            )
            self.window.canvas.changed.emit()
            self.window.statusBar().showMessage(
                tr("キーフレーム {value}～{value2}（{exposure}コマ）全体へ、選択中の{len}色の太さ {value3} を適用しました。").format(value=key_frame + 1, value2=block_end + 1, exposure=exposure, len=len(line_colors), value3=255 - value),
                4200,
            )
        else:
            self.window.canvas.cancel_selection_transform()
            self.window.tools.transform_line_width.setValue(
                previous_threshold
            )
            self.window.canvas.current_frame = original_frame
            self.window.canvas.active_layer_index = layer_index

        self.window.canvas.clear_selection_preserving_used_colors()
        self.set_selected_used_colors(previous_selected)
        self.window.tools.transform_quality.setChecked(
            previous_quality
        )
        self.window.tools.select_tool(previous_tool)
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()

    def set_selected_used_colors(self, colors):
        normalized = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in colors
            if rgb is not None and len(rgb) >= 3
        }
        self.window.canvas.set_transform_line_colors(normalized)
        self.window.tools.set_transform_line_colors_available(bool(normalized))

    def set_visible_colors(self, colors):
        self.window._pending_visible_colors = set(colors)
        self.window._visible_color_timer.start()

    def _apply_pending_visible_colors(self):
        colors = self.window._pending_visible_colors
        self.window._pending_visible_colors = None
        if colors is None:
            return

        palette_colors = {
            (color.red(), color.green(), color.blue())
            for color in self.window.palette.colors
        }
        # 全色ONならフィルター処理そのものを行わない。
        effective_colors = (
            None if not palette_colors or palette_colors.issubset(colors)
            else set(colors)
        )
        if effective_colors == self.window.canvas.visible_color_rgbs:
            return
        self.window.canvas.visible_color_rgbs = effective_colors
        # 可視色セットはキャッシュキーに含まれるため全消去しない。
        # 以前の表示状態へ戻した時は既存キャッシュを再利用できる。
        self.window.canvas.update()

    def set_preview_color_groups(self, _mapping):
        """親子付けでは色を置換せず、残っている旧プレビューも解除する。"""
        if not getattr(self.window.canvas, "preview_color_remap", {}):
            return
        self.window.canvas.preview_color_remap = {}
        self.window.canvas.update()

    def freeze_preview_color_groups(self, mapping):
        """登録した親子を実ピクセルへ統合する。"""
        rgb_mapping = {}
        for child, parent in (mapping or {}).items():
            child = tuple(int(channel) for channel in child[:3])
            parent = tuple(int(channel) for channel in parent[:3])
            if child != parent:
                rgb_mapping[child] = parent
        if not rgb_mapping:
            return
        # 旧バージョン由来のプレビューが残っていても先に解除する。
        self.window.canvas.preview_color_remap = {}
        applied = self.window.used_color.apply_palette_replacements(
            rgb_mapping,
            operation="parent_merge",
        )
        if applied:
            self.window.palette.on_groups_frozen()

    def apply_sampled_color_to_mode(self, mode, color):
        self._apply_sampled_drawing_color(mode, color)

    def apply_sampled_color(self, color):
        mode = self.window.canvas.color_mode
        mode = self._apply_sampled_drawing_color(mode, color)
        if mode != "transparent":
            self.window.palette.select_matching_color(color)

    def isolate_selected_color(self, selected_color=None):
        if not self.window.canvas.frames:
            return
        if selected_color is None:
            if self.window.canvas.color_mode == "transparent":
                QMessageBox.information(
                    self.window,
                    tr("特定色だけ表示"),
                    tr("背景色では特定色表示を設定できません。メイン色またはサブ色を選択してください。"),
                )
                return
            color = self.window.canvas.main_color if self.window.canvas.color_mode == "main" else self.window.canvas.sub_color
        else:
            color = QColor(selected_color)
        rgb = (color.red(), color.green(), color.blue())
        layer_index = self.window.canvas.active_layer_index
        changed = False

        for frame in self.window.canvas.frames:
            if layer_index >= len(frame.layers):
                continue
            layer = frame.layers[layer_index]
            if (not layer.color_filter_enabled) or layer.color_filter_rgb != rgb:
                layer.color_filter_enabled = True
                layer.color_filter_rgb = rgb
                changed = True

        if changed:
            self.window.canvas._color_filter_cache.clear()
            self.window.canvas.changed.emit()
            self.window.canvas.update()
        self.window.statusBar().showMessage(
            tr("選択レイヤーを #{value:02X}{value2:02X}{value3:02X} だけ表示しています。元画像は変更されません。").format(value=rgb[0], value2=rgb[1], value3=rgb[2]),
            3500,
        )

    def clear_selected_color_filter(self):
        if not self.window.canvas.frames:
            return
        layer_index = self.window.canvas.active_layer_index
        changed = False
        for frame in self.window.canvas.frames:
            if layer_index >= len(frame.layers):
                continue
            layer = frame.layers[layer_index]
            if layer.color_filter_enabled or layer.color_filter_rgb is not None:
                layer.color_filter_enabled = False
                layer.color_filter_rgb = None
                changed = True

        if changed:
            self.window.canvas._color_filter_cache.clear()
            self.window.canvas.changed.emit()
            self.window.canvas.update()
            self.window.statusBar().showMessage(tr("選択レイヤーの特定色表示を解除しました。"), 2500)
        else:
            self.window.statusBar().showMessage(tr("選択レイヤーには特定色表示が設定されていません。"), 2500)

    def toggle_draw_background_color(self):
        previous = getattr(self.window, "_previous_draw_color_mode", "main")
        if self.window.canvas.color_mode == "transparent":
            self.set_color_mode(previous if previous in ("main", "sub") else "main")
        else:
            self.window._previous_draw_color_mode = self.window.canvas.color_mode
            self.set_color_mode("transparent")

    def set_color_value(self,mode,color):
        setattr(self.window.canvas,mode+"_color",QColor(color));self.set_color_mode(mode)

    def choose_background_color(self):
        color = QColorDialog.getColor(
            self.window.canvas.transparent_display_color,
            self.window,
            tr("背景色の表示色を選択"),
        )
        if not color.isValid():
            return
        self.window.canvas.transparent_display_color = QColor(color)
        self.window.canvas.checker_light = QColor(color)
        self.window.canvas.checker_dark = QColor(color)
        self.window.canvas.color_mode = "transparent"
        self.window.tools.set_colors(
            self.window.canvas.main_color,
            self.window.canvas.sub_color,
            "transparent",
            self.window.canvas.transparent_display_color,
        )
        self.window.canvas.update()

    def choose_color(self,mode):
        base=self.window.canvas.main_color if mode=="main" else self.window.canvas.sub_color;c=QColorDialog.getColor(base,self.window,tr("色を選択"))
        if c.isValid():setattr(self.window.canvas,mode+"_color",c);self.set_color_mode(mode)

    def set_color_mode(self,mode):self.window.canvas.color_mode=mode;self.window.tools.set_colors(self.window.canvas.main_color,self.window.canvas.sub_color,mode,self.window.canvas.transparent_display_color);self._sync_tool_selector_swatch()

    def _sync_tool_selector_swatch(self):
        selector = getattr(self.window, "tool_selector", None)
        if selector is not None and hasattr(selector, "set_swatch_colors"):
            selector.set_swatch_colors(
                self.window.tools.main_color,
                self.window.tools.sub_color,
                self.window.tools.color_mode,
                self.window.canvas.transparent_display_color,
            )

    def image_color_hex(self, color):
        return QColor(color).name(QColor.NameFormat.HexRgb).upper()
