"""Color selection / sampling / mask-color interaction for MainWindow.

Split out of ``main_window.py`` as a mixin. These methods handle the accent
color, main/sub color pickers, sampled-color application, mask/visible color
sets, color-filter isolation, and tool-selector swatch sync. They run against a
live ``MainWindow`` instance.
"""
from .common import *  # noqa: F401,F403
from ._main_window_members import MainWindowMembers
from . import theme
from .widgets import TransformLineThicknessDialog
from .logging_setup import get_logger

log = get_logger(__name__)


class ColorInteractionMixin(MainWindowMembers):
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
            self.canvas.color_mode = mode
        else:
            mode = "sub" if mode == "sub" else "main"
            setattr(self.canvas, f"{mode}_color", qc)
            self.canvas.color_mode = mode
        self.tools.set_colors(
            self.canvas.main_color,
            self.canvas.sub_color,
            mode,
            self.canvas.transparent_display_color,
        )
        self._sync_tool_selector_swatch()
        return mode

    def choose_accent_color(self):
        current = QColor(theme.current_accent())
        color = QColorDialog.getColor(
            current, self, "アクセントカラーを選択"
        )
        if color.isValid():
            self.set_accent(color.name())

    def set_accent(self, color):
        """Change the accent colour app-wide, persist it, and restyle."""
        app = QApplication.instance()
        if app is not None:
            theme.set_accent(app, color, persist=True)
        self._refresh_theme_dependent_ui()

    def set_mask_colors(self, colors):
        normalized = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in colors
            if rgb is not None and len(rgb) >= 3
        }
        self.canvas.mask_color_rgbs = normalized
        self.canvas.mask_all_enabled = self.palette.all_masks_enabled()
        # 古い単色属性は互換用に残すが、複数選択時の判定には使用しない。
        self.canvas.mask_color_rgb = (
            next(iter(normalized)) if len(normalized) == 1 else None
        )
        if self.canvas.mask_all_enabled:
            self.statusBar().showMessage(
                "マスクは「全体」です。すべての領域に描画できます。", 1800
            )
        elif normalized:
            self.statusBar().showMessage(
                f"描画可能なマスクを {len(normalized)}色選択しています。", 1800
            )
        else:
            self.statusBar().showMessage(
                "すべてのマスクがOFFです。描画できません。", 1800
            )

    def focus_used_color(self, rgb):
        """指定色の最初のコマへ移動し、その色全体を選択範囲で囲む。"""
        rgb = tuple(int(value) for value in rgb[:3])
        layer_index = int(self.canvas.active_layer_index)
        target_frame = None

        for frame_index, frame in enumerate(self.canvas.frames):
            if not (0 <= layer_index < len(frame.layers)):
                continue
            layer = frame.layers[layer_index]
            if not layer.has_content:
                continue
            cache_key = self._used_color_cache_key(layer.image)
            cached = self._used_color_cache.get(cache_key)
            if cached is not None:
                cached_colors, cached_exceeded = cached
                if rgb in cached_colors:
                    target_frame = frame_index
                    break
                if not cached_exceeded:
                    continue
            if self._image_contains_rgb(layer.image, rgb):
                target_frame = frame_index
                break

        if target_frame is None:
            QMessageBox.information(
                self,
                "対象に注視",
                "現在のレイヤー内に、この色が使われているコマはありません。",
            )
            return

        visual_row = len(self.canvas.layers) - 1 - layer_index
        self.canvas.select_exposure(target_frame, visual_row)
        self.timeline.select_current(target_frame, layer_index)
        self.set_selected_used_colors({rgb})
        layer = self.canvas.frames[target_frame].layers[layer_index]
        rgba = self.canvas._qimage_rgba_array(layer.image)
        target = np.asarray(rgb, dtype=np.uint8)
        matching = (
            (rgba[:, :, 3] > 0)
            & np.all(rgba[:, :, :3] == target, axis=2)
        )
        contours = self._mask_contours(matching)
        contour = self._largest_contour(contours)
        if contour:
            self.canvas.selection_polygon = contour
            self.canvas.selection_mask_override = matching.copy()
            self.canvas.selection_outline_polygons = contours
            ys, xs = np.nonzero(matching)
            self.canvas.selection_mask_rect = QRectF(
                int(xs.min()),
                int(ys.min()),
                int(xs.max() - xs.min() + 1),
                int(ys.max() - ys.min() + 1),
            )
            self.canvas.lasso = []
            self.canvas.rect_start = None
            self.canvas.rect_end = None
            self.canvas.selectionChanged.emit()
            self.canvas.update()
        self.canvas.setFocus()
        self.statusBar().showMessage(
            f"使用色 {rgb} が最初に現れる {target_frame + 1} コマ目へ移動しました。",
            3200,
        )

    def adjust_parent_line_thickness(self, colors):
        """●ーーーー｜全体を1つの画像として、選択色の線幅を調整する。"""
        if getattr(self.canvas, "tween_pending", None):
            self.statusBar().showMessage(
                "トゥイーン中は線の太さを変更できません。",
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

        parent_rgb = tuple(self.palette.parent_rgb or ())
        if not line_colors:
            line_colors = {
                tuple(value)
                for value in self.palette.selected_rgbs
            }

        if not parent_rgb or parent_rgb not in line_colors:
            QMessageBox.information(
                self,
                "太さを調整",
                "親として選択している色で右クリックしてください。",
            )
            return

        if self.canvas.transform_active:
            QMessageBox.warning(
                self,
                "太さを調整",
                "別の変形処理を確定またはキャンセルしてから"
                "実行してください。",
            )
            return

        original_frame = int(self.canvas.current_frame)
        layer_index = int(self.canvas.active_layer_index)
        block = self.canvas.resolve_exposure_block(
            original_frame,
            layer_index,
        )
        if block is None:
            QMessageBox.information(
                self,
                "太さを調整",
                "現在位置には調整できるキーフレームがありません。",
            )
            return

        key_frame, block_end, exposure = block
        key_layer = self.canvas.frames[
            key_frame
        ].layers[layer_index]
        if not key_layer.has_content:
            QMessageBox.information(
                self,
                "太さを調整",
                "現在の露出ブロックに画像がありません。",
            )
            return

        previous_tool = self.tools.active_tool
        previous_quality = (
            self.tools.transform_quality.isChecked()
        )
        previous_threshold = (
            self.tools.transform_line_width.value()
        )
        previous_selected = set(self.palette.selected_rgbs)

        # 保持セルから実行しても、必ず●の画像本体へ移動して処理する。
        # 既存の部分選択は使わず、キーフレーム画像全体を対象にする。
        self.canvas.current_frame = key_frame
        self.canvas.active_layer_index = layer_index
        self.canvas.clear_selection_preserving_used_colors()

        if not self.canvas.auto_select_used_area():
            self.canvas.current_frame = original_frame
            QMessageBox.warning(
                self,
                "太さを調整",
                "キーフレーム全体から描画領域を検出できません。",
            )
            return

        self.tools.select_tool("rect_select")
        self.tools.transform_quality.setChecked(True)
        self.tools.transform_line_width.setValue(
            previous_threshold
        )
        self.start_wire_transform(
            "free",
            line_colors_override=set(line_colors),
        )

        if not self.canvas.transform_active:
            self.canvas.current_frame = original_frame
            self.tools.transform_quality.setChecked(
                previous_quality
            )
            self.tools.select_tool(previous_tool)
            return

        dialog = TransformLineThicknessDialog(
            self.tools.transform_line_width.value(),
            self,
        )
        dialog.slider.valueChanged.connect(
            self.tools.transform_line_width.setValue
        )
        dialog.slider.sliderPressed.connect(
            self.canvas.begin_transform_line_adjustment
        )
        dialog.slider.sliderReleased.connect(
            self.canvas.finish_transform_line_adjustment
        )

        accepted = (
            dialog.exec() == QDialog.DialogCode.Accepted
        )
        if accepted:
            value = dialog.value()
            self.tools.transform_line_width.setValue(value)
            self.canvas.set_transform_line_threshold(value)
            self.canvas.commit_selection_transform(
                all_frames=False
            )
            # 画像は●にだけ保存し、ーーーー｜は同じ露出を参照する。
            key_layer = self.canvas.frames[
                key_frame
            ].layers[layer_index]
            key_layer.exposure = max(1, int(exposure))
            self.canvas.current_frame = min(
                original_frame,
                block_end,
            )
            self.canvas.active_layer_index = layer_index
            self.canvas._onion_cache.clear()
            self.canvas._color_filter_cache.clear()
            self.canvas._color_index_cache.clear()
            self.canvas._silhouette_cache.clear()
            self._used_color_cache.clear()
            self.canvas.cellChanged.emit(
                key_frame,
                layer_index,
            )
            self.canvas.changed.emit()
            self.statusBar().showMessage(
                f"キーフレーム {key_frame + 1}～"
                f"{block_end + 1}（{exposure}コマ）全体へ、"
                f"選択中の{len(line_colors)}色の太さ "
                f"{255 - value} を適用しました。",
                4200,
            )
        else:
            self.canvas.cancel_selection_transform()
            self.tools.transform_line_width.setValue(
                previous_threshold
            )
            self.canvas.current_frame = original_frame
            self.canvas.active_layer_index = layer_index

        self.canvas.clear_selection_preserving_used_colors()
        self.set_selected_used_colors(previous_selected)
        self.tools.transform_quality.setChecked(
            previous_quality
        )
        self.tools.select_tool(previous_tool)
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def set_selected_used_colors(self, colors):
        normalized = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in colors
            if rgb is not None and len(rgb) >= 3
        }
        self.canvas.set_transform_line_colors(normalized)
        self.tools.set_transform_line_colors_available(bool(normalized))

    def set_visible_colors(self, colors):
        self._pending_visible_colors = set(colors)
        self._visible_color_timer.start()

    def _apply_pending_visible_colors(self):
        colors = self._pending_visible_colors
        self._pending_visible_colors = None
        if colors is None:
            return

        palette_colors = {
            (color.red(), color.green(), color.blue())
            for color in self.palette.colors
        }
        # 全色ONならフィルター処理そのものを行わない。
        effective_colors = (
            None if not palette_colors or palette_colors.issubset(colors)
            else set(colors)
        )
        if effective_colors == self.canvas.visible_color_rgbs:
            return
        self.canvas.visible_color_rgbs = effective_colors
        # 可視色セットはキャッシュキーに含まれるため全消去しない。
        # 以前の表示状態へ戻した時は既存キャッシュを再利用できる。
        self.canvas.update()

    def set_preview_color_groups(self, _mapping):
        """親子付けでは色を置換せず、残っている旧プレビューも解除する。"""
        if not getattr(self.canvas, "preview_color_remap", {}):
            return
        self.canvas.preview_color_remap = {}
        self.canvas.update()

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
        self.canvas.preview_color_remap = {}
        applied = self.apply_palette_replacements(
            rgb_mapping,
            operation="親子統合",
        )
        if applied:
            self.palette.on_groups_frozen()

    def apply_sampled_color_to_mode(self, mode, color):
        self._apply_sampled_drawing_color(mode, color)

    def apply_sampled_color(self, color):
        mode = self.canvas.color_mode
        mode = self._apply_sampled_drawing_color(mode, color)
        if mode != "transparent":
            self.palette.select_matching_color(color)

    def isolate_selected_color(self, selected_color=None):
        if not self.canvas.frames:
            return
        if selected_color is None:
            if self.canvas.color_mode == "transparent":
                QMessageBox.information(
                    self,
                    "特定色だけ表示",
                    "背景色では特定色表示を設定できません。メイン色またはサブ色を選択してください。",
                )
                return
            color = self.canvas.main_color if self.canvas.color_mode == "main" else self.canvas.sub_color
        else:
            color = QColor(selected_color)
        rgb = (color.red(), color.green(), color.blue())
        layer_index = self.canvas.active_layer_index
        changed = False

        for frame in self.canvas.frames:
            if layer_index >= len(frame.layers):
                continue
            layer = frame.layers[layer_index]
            if (not layer.color_filter_enabled) or layer.color_filter_rgb != rgb:
                layer.color_filter_enabled = True
                layer.color_filter_rgb = rgb
                changed = True

        if changed:
            self.canvas._color_filter_cache.clear()
            self.canvas.changed.emit()
            self.canvas.update()
        self.statusBar().showMessage(
            f"選択レイヤーを #{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X} だけ表示しています。元画像は変更されません。",
            3500,
        )

    def clear_selected_color_filter(self):
        if not self.canvas.frames:
            return
        layer_index = self.canvas.active_layer_index
        changed = False
        for frame in self.canvas.frames:
            if layer_index >= len(frame.layers):
                continue
            layer = frame.layers[layer_index]
            if layer.color_filter_enabled or layer.color_filter_rgb is not None:
                layer.color_filter_enabled = False
                layer.color_filter_rgb = None
                changed = True

        if changed:
            self.canvas._color_filter_cache.clear()
            self.canvas.changed.emit()
            self.canvas.update()
            self.statusBar().showMessage("選択レイヤーの特定色表示を解除しました。", 2500)
        else:
            self.statusBar().showMessage("選択レイヤーには特定色表示が設定されていません。", 2500)

    def toggle_draw_background_color(self):
        previous = getattr(self, "_previous_draw_color_mode", "main")
        if self.canvas.color_mode == "transparent":
            self.set_color_mode(previous if previous in ("main", "sub") else "main")
        else:
            self._previous_draw_color_mode = self.canvas.color_mode
            self.set_color_mode("transparent")

    def set_color_value(self,mode,color):
        setattr(self.canvas,mode+"_color",QColor(color));self.set_color_mode(mode)

    def choose_background_color(self):
        color = QColorDialog.getColor(
            self.canvas.transparent_display_color,
            self,
            "背景色の表示色を選択",
        )
        if not color.isValid():
            return
        self.canvas.transparent_display_color = QColor(color)
        self.canvas.checker_light = QColor(color)
        self.canvas.checker_dark = QColor(color)
        self.canvas.color_mode = "transparent"
        self.tools.set_colors(
            self.canvas.main_color,
            self.canvas.sub_color,
            "transparent",
            self.canvas.transparent_display_color,
        )
        self.canvas.update()

    def choose_color(self,mode):
        base=self.canvas.main_color if mode=="main" else self.canvas.sub_color;c=QColorDialog.getColor(base,self,"色を選択")
        if c.isValid():setattr(self.canvas,mode+"_color",c);self.set_color_mode(mode)

    def set_color_mode(self,mode):self.canvas.color_mode=mode;self.tools.set_colors(self.canvas.main_color,self.canvas.sub_color,mode,self.canvas.transparent_display_color);self._sync_tool_selector_swatch()

    def _sync_tool_selector_swatch(self):
        selector = getattr(self, "tool_selector", None)
        if selector is not None and hasattr(selector, "set_swatch_colors"):
            selector.set_swatch_colors(
                self.tools.main_color,
                self.tools.sub_color,
                self.tools.color_mode,
                self.canvas.transparent_display_color,
            )

    def image_color_hex(self, color):
        return QColor(color).name(QColor.NameFormat.HexRgb).upper()
