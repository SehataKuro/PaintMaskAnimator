"""In-between (tween) generation and its command popup for MainWindow.

Split out of ``main_window.py`` as a mixin. These methods drive the tween
command popup, enable/commit/cancel tween interpolation on the timeline, and
dispatch the shared "commit/cancel current transform-or-tween" actions. They
run against a live ``MainWindow`` instance.
"""
from .common import *  # noqa: F401,F403
from .errors import OPERATION_ERRORS as _OPERATION_ERRORS
from .widgets import TweenCommandPopup
from .logging_setup import get_logger

log = get_logger(__name__)


class TweenMixin:
    def _refresh_timeline_tween_marker(self):
        self.timeline.refresh(
            self.canvas.frames,
            self.canvas.current_frame,
            self.canvas.active_layer_index,
            getattr(self.canvas, "tween_pending", None),
        )

    def _close_tween_command_popup(self):
        popup = self._tween_command_popup
        self._tween_command_popup = None
        if popup is not None:
            popup.close()
            popup.deleteLater()

    def _show_tween_command_popup(self, mode=None):
        self._close_tween_command_popup()
        pending = getattr(self.canvas, "tween_pending", None) or {}
        tween_mode = (
            "mesh"
            if (mode or pending.get("transform_mode")) == "mesh"
            else "free"
        )
        popup = TweenCommandPopup(
            tween_mode,
            pending.get(
                "mesh_cols",
                getattr(self.canvas, "transform_mesh_cols", 4),
            ),
            pending.get(
                "mesh_rows",
                getattr(self.canvas, "transform_mesh_rows", 4),
            ),
            self,
            reverse=bool(
                pending.get(
                    "reverse_generation",
                    False,
                )
            ),
        )
        self._tween_command_popup = popup
        popup.commitRequested.connect(self.commit_transform_or_tween)
        popup.cancelRequested.connect(self.cancel_transform_or_tween)
        popup.reverseChanged.connect(
            self.set_tween_reverse_generation
        )
        popup.rotateLeftRequested.connect(
            lambda: self.canvas.rotate_selection_transform(-90.0)
        )
        popup.rotateRightRequested.connect(
            lambda: self.canvas.rotate_selection_transform(90.0)
        )
        popup.destroyed.connect(
            lambda _obj=None, current=popup:
            setattr(
                self,
                "_tween_command_popup",
                None if self._tween_command_popup is current
                else self._tween_command_popup,
            )
        )
        popup.adjustSize()
        anchor = self.timeline_dock.mapToGlobal(
            QPoint(
                max(0, self.timeline_dock.width() - popup.sizeHint().width() - 16),
                24,
            )
        )
        popup.move(anchor)
        popup.show()
        popup.raise_()
        popup.activateWindow()

    def set_tween_reverse_generation(self, enabled):
        """生成方向を切り替え、タイムライン記号を即時更新する。"""
        pending = getattr(
            self.canvas,
            "tween_pending",
            None,
        )
        if not isinstance(pending, dict):
            return

        reverse = bool(enabled)
        pending["reverse_generation"] = reverse
        self._refresh_timeline_tween_marker()
        self.canvas.setFocus()

        transform_mode = (
            "メッシュ変形"
            if pending.get("transform_mode") == "mesh"
            else "自由変形"
        )
        if reverse:
            self.statusBar().showMessage(
                f"◆ {transform_mode}の逆生成："
                "キーフレーム側を変形形状、"
                "ラストコマ側を元の初期形状として生成します。",
                5000,
            )
        else:
            self.statusBar().showMessage(
                f"♦ {transform_mode}の通常生成："
                "キーフレーム側を元の初期形状、"
                "ラストコマ側を変形形状として生成します。",
                5000,
            )

    def enable_tween(self, visual_row, key_column, mode="free"):
        """タイムライン終端の｜を、自由／メッシュトゥイーンの♦へ切り替える。"""
        mode = "mesh" if mode == "mesh" else "free"
        mode_name = "メッシュ変形" if mode == "mesh" else "自由変形"
        if not self.canvas.frames:
            return
        layer_count = len(self.canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        key_column = int(key_column)
        if not (
            0 <= layer_index < layer_count
            and 0 <= key_column < len(self.canvas.frames)
        ):
            return
        key_layer = self.canvas.frames[key_column].layers[layer_index]
        exposure = max(1, int(key_layer.exposure))
        if not key_layer.has_content or exposure < 2:
            QMessageBox.warning(
                self,
                "トゥイーン",
                "2コマ以上の表示区間を持つ画像キーフレームで実行してください。",
            )
            return

        answer = QMessageBox.question(
            self,
            "トゥイーンを有効にする",
            (
                f"{exposure}コマの{mode_name}トゥイーンを開始します。\n"
                "確定後は区間内の各コマが画像キーフレームになります。"
            ),
            QMessageBox.StandardButton.Ok
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return

        if self.canvas.transform_active:
            self.cancel_transform_or_tween()

        # 自由変形が画像を一時的に消去する前の文書全体を保存する。
        # Ctrl+Zではこの状態へ戻すため、元画像が消えることはない。
        tween_undo_snapshot = self.canvas.document_snapshot()

        self.canvas.current_frame = key_column
        self.canvas.active_layer_index = layer_index
        self.canvas.tween_pending = None
        self.tools.set_tween_active(False)
        self.palette.setProperty("tweenActive", False)
        self.canvas.selection_polygon = []
        self.canvas.selection_mask_override = None
        self.canvas.selection_outline_polygons = []
        self.canvas.selection_mask_rect = None
        self.canvas.lasso = []
        self.canvas.rect_start = None
        self.canvas.rect_end = None

        # トゥイーンでも現在のTPクオリティ、選択色、線幅を維持する。
        # 「すべてのコマに適用」だけはトゥイーン生成と分離する。
        self.tools.selection_all_frames.setChecked(False)
        self.set_selected_used_colors(set(self.palette.selected_rgbs))
        self.tools.select_tool("rect_select")

        if not self.canvas.auto_select_used_area():
            QMessageBox.warning(
                self,
                "トゥイーン",
                "画像内に変形対象となる描画領域がありません。",
            )
            return

        self.start_wire_transform(mode)
        if not self.canvas.transform_active:
            return

        end_column = key_column + exposure - 1
        self.canvas.tween_pending = {
            "layer_index": layer_index,
            "key_col": key_column,
            "end_col": end_column,
            "exposure": exposure,
            "undo_snapshot": tween_undo_snapshot,
            "quality_active": bool(
                self.canvas.transform_quality_active
            ),
            "line_threshold": int(
                self.canvas.transform_line_threshold
            ),
            "line_colors": tuple(
                self.canvas.transform_tp_line_colors
            ),
            "transform_mode": mode,
            "mesh_cols": int(
                getattr(self.canvas, "transform_mesh_cols", 4)
            ),
            "mesh_rows": int(
                getattr(self.canvas, "transform_mesh_rows", 4)
            ),
            "mesh_reference_points": [
                QPointF(point)
                for point in getattr(
                    self.canvas,
                    "transform_mesh_reference_points",
                    [],
                )
            ],
            "start_points": [
                QPointF(point) for point in self.canvas.transform_points
            ],
            "reverse_generation": False,
        }
        self.tools.set_tween_active(True)
        self.palette.setProperty("tweenActive", True)
        self._refresh_timeline_tween_marker()
        self._show_tween_command_popup(mode)
        self.canvas.setFocus()
        self.statusBar().showMessage(
            f"♦ {mode_name}トゥイーン中です。"
            "変形形状を指定し、ポップアップの"
            "「逆生成」で生成方向を選べます。",
            5000,
        )

    def commit_transform_or_tween(self):
        if getattr(self.canvas, "tween_pending", None):
            self.commit_tween_transform()
        else:
            self.canvas.commit_selection_transform()

    def cancel_transform_or_tween(self):
        had_tween = bool(getattr(self.canvas, "tween_pending", None))
        if self.canvas.transform_active:
            self.canvas.cancel_selection_transform()
        self.canvas.tween_pending = None
        self.tools.set_tween_active(False)
        self.palette.setProperty("tweenActive", False)
        self._close_tween_command_popup()
        if had_tween:
            self._refresh_timeline_tween_marker()
            self.statusBar().showMessage(
                "トゥイーンをキャンセルしました。",
                2200,
            )

    def commit_tween_transform(self):
        """通常生成または逆生成で自由／メッシュ変形形状を補間する。"""
        pending = getattr(self.canvas, "tween_pending", None)
        if not pending or not self.canvas.transform_active:
            return

        layer_index = int(pending["layer_index"])
        key_column = int(pending["key_col"])
        end_column = int(pending["end_col"])
        exposure = max(2, int(pending["exposure"]))
        reverse_generation = bool(
            pending.get(
                "reverse_generation",
                False,
            )
        )
        transform_mode = (
            "mesh"
            if pending.get("transform_mode") == "mesh"
            else "free"
        )
        mode_name = (
            "メッシュ変形" if transform_mode == "mesh" else "自由変形"
        )
        self.canvas.transform_mode = transform_mode
        if transform_mode == "mesh":
            self.canvas.transform_mesh_cols = max(
                2, int(pending.get("mesh_cols", 4))
            )
            self.canvas.transform_mesh_rows = max(
                2, int(pending.get("mesh_rows", 4))
            )
            self.canvas.transform_mesh_grid = (
                self.canvas.transform_mesh_cols
            )
            self.canvas._active_mesh_cols = (
                self.canvas.transform_mesh_cols
            )
            self.canvas._active_mesh_rows = (
                self.canvas.transform_mesh_rows
            )
            reference_points = pending.get(
                "mesh_reference_points",
                [],
            )
            if len(reference_points) == (
                self.canvas.transform_mesh_cols
                * self.canvas.transform_mesh_rows
            ):
                self.canvas.transform_mesh_reference_points = [
                    QPointF(point) for point in reference_points
                ]
            else:
                self.canvas.transform_mesh_reference_points = (
                    self.canvas._regular_mesh_reference_points(
                        self.canvas.transform_source_rect,
                        self.canvas.transform_mesh_cols,
                        self.canvas.transform_mesh_rows,
                    )
                )
        start_points = [
            QPointF(point) for point in pending.get("start_points", [])
        ]
        final_points = [
            QPointF(point) for point in self.canvas.transform_points
        ]
        if (
            len(start_points) != len(final_points)
            or not start_points
            or self.canvas.transform_original_layer is None
            or self.canvas.transform_source is None
        ):
            QMessageBox.warning(
                self,
                "トゥイーン",
                "変形情報を取得できないため、トゥイーンを確定できません。",
            )
            return

        undo_snapshot = pending.get("undo_snapshot")

        # トゥイーン開始後に「クオリティ」をON/OFFした場合も、
        # 確定時の現在設定をそのまま使用する。
        quality_active = bool(
            self.tools.transform_quality.isChecked()
        )
        current_line_threshold = max(
            1,
            min(
                254,
                int(self.tools.transform_line_width.value()),
            ),
        )
        current_line_colors = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in self.palette.selected_rgbs
            if rgb is not None and len(rgb) >= 3
        }

        self.canvas.transform_quality = quality_active
        self.canvas.transform_quality_active = quality_active
        self.canvas.transform_apply_all_frames = False
        self.canvas.transform_line_threshold = current_line_threshold
        self.canvas.selected_used_color_rgbs = set(
            current_line_colors
        )
        self.canvas.transform_tp_line_colors = tuple(
            sorted(current_line_colors)
        )

        # 保存済み設定も現在値へ更新し、確定処理の全経路で同じ値を使う。
        pending["quality_active"] = quality_active
        pending["line_threshold"] = current_line_threshold
        pending["line_colors"] = tuple(
            sorted(current_line_colors)
        )

        self.canvas._invalidate_tp_preview_cache()

        # クオリティ確定では、通常変形への暗黙フォールバックを許可しない。
        # 元画像から色マスクを作り直し、各トゥイーンコマへ確実に適用する。
        if quality_active:
            if not self.canvas._prepare_tp_transform_masks():
                QMessageBox.warning(
                    self,
                    "トゥイーン確定",
                    "TPクオリティ用の色マスクを生成できませんでした。\n"
                    "Pillowが利用できることと、変形対象に色があることを"
                    "確認してください。",
                )
                return

        original = self.canvas.transform_original_layer.copy()
        source = self.canvas.transform_source
        generated_images = []
        total_steps = exposure + 2
        progress = self.create_progress_counter(
            f"{mode_name}トゥイーンを確定",
            total_steps,
            f"{mode_name}トゥイーン画像を準備しています",
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.update_progress_counter(
                progress,
                1,
                total_steps,
                "開始キーフレームを準備しています",
            )
            for index in range(exposure):
                self.update_progress_counter(
                    progress,
                    index + 1,
                    total_steps,
                    f"{mode_name}の補間コマ {index + 1}/{exposure} を生成しています",
                )

                timeline_ratio = (
                    index / float(exposure - 1)
                )
                transform_ratio = (
                    1.0 - timeline_ratio
                    if reverse_generation
                    else timeline_ratio
                )

                # 変形率0は元画像をそのまま使用する。
                # 通常生成では先頭、逆生成ではラストコマが初期形状になる。
                if transform_ratio <= 1e-9:
                    image = original.copy()
                else:
                    self.canvas.transform_points = [
                        QPointF(
                            start.x()
                            + (
                                finish.x()
                                - start.x()
                            ) * transform_ratio,
                            start.y()
                            + (
                                finish.y()
                                - start.y()
                            ) * transform_ratio,
                        )
                        for start, finish in zip(
                            start_points,
                            final_points,
                        )
                    ]
                    self.canvas._invalidate_tp_preview_cache()
                    if quality_active:
                        preview, _target = (
                            self.canvas._tp_mask_preview_image(
                                source,
                                original.width(),
                                original.height(),
                            )
                        )
                    else:
                        preview, _target = (
                            self.canvas._project_transform_source(
                                source,
                                original.width(),
                                original.height(),
                                smooth=False,
                            )
                        )
                    if preview is None:
                        raise RuntimeError(
                            f"{index + 1}コマ目の変形画像を生成できませんでした。"
                        )
                    image = self.canvas._cleared_selection_base(
                        original
                    )
                    painter = QPainter(image)
                    painter.setCompositionMode(
                        QPainter.CompositionMode.CompositionMode_SourceOver
                    )
                    painter.drawImage(0, 0, preview)
                    painter.end()

                generated_images.append(image)

            self.canvas.transform_points = [
                QPointF(point) for point in final_points
            ]
            self.update_progress_counter(
                progress,
                exposure + 1,
                total_steps,
                "生成した画像をタイムラインへ登録しています",
            )
            self.canvas._ensure_frame_count(end_column + 1)
            for offset, image in enumerate(generated_images):
                frame_index = key_column + offset
                layer = self.canvas.frames[frame_index].layers[layer_index]
                layer.image = image
                layer.has_content = True
                layer.exposure = 1

            if undo_snapshot is None:
                raise RuntimeError(
                    "トゥイーン開始前のUndo情報を取得できませんでした。"
                )
            self.canvas.undo_stack.append(("doc", undo_snapshot))
            self.canvas.undo_stack = self.canvas.undo_stack[-MAX_UNDO:]
            self.canvas.redo_stack.clear()

            self.update_progress_counter(
                progress,
                total_steps,
                total_steps,
                "トゥイーンのキーフレーム化が完了しました",
            )
        except _OPERATION_ERRORS as exc:
            log.warning("tween keyframe generation failed: %s", exc, exc_info=True)
            # 途中生成に失敗した場合も、開始前の元画像へ戻す。
            if undo_snapshot is not None:
                self.canvas.apply_undo_entry(("doc", undo_snapshot))
            else:
                self.canvas.transform_points = [
                    QPointF(point) for point in final_points
                ]
                self.canvas.cancel_selection_transform()
            self.canvas.tween_pending = None
            self.tools.set_tween_active(False)
            self.palette.setProperty("tweenActive", False)
            self._close_tween_command_popup()
            QMessageBox.warning(
                self,
                "トゥイーン確定エラー",
                str(exc),
            )
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.close_progress_counter(progress)

        self.canvas._reset_selection_transform()
        self.canvas.transform_apply_all_frames = False
        self.canvas.tween_pending = None
        self.canvas.selection_polygon = []
        self.canvas.selection_mask_override = None
        self.canvas.selection_outline_polygons = []
        self.canvas.selection_mask_rect = None
        self.canvas.lasso = []
        self.canvas.rect_start = None
        self.canvas.rect_end = None
        self.canvas.current_frame = (
            key_column
            if reverse_generation
            else end_column
        )
        self.canvas.active_layer_index = layer_index
        self.canvas._onion_cache.clear()
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._silhouette_cache.clear()
        self._close_tween_command_popup()

        # 補間では使用色自体は増えないため、全コマ色走査は省略する。
        self._suppress_used_color_refresh_once = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas._update_selection_clear_overlay()
        self.canvas.update()
        quality_note = (
            "（TPクオリティ適用）"
            if quality_active else ""
        )
        direction_note = (
            "逆生成（◆側が変形／右端が初期）"
            if reverse_generation
            else "通常生成（先頭が初期／♦側が変形）"
        )
        self.statusBar().showMessage(
            f"{exposure}コマの{mode_name}トゥイーンを"
            f"{direction_note}でキーフレーム化しました。"
            f"{quality_note}",
            4200,
        )
