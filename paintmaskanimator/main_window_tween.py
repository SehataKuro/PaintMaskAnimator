"""In-between generation and its command popup; owned as ``window.tween``.

Drives the tween command popup, enables/commits/cancels tween interpolation on
the timeline, and dispatches the shared "commit or cancel the current transform
or tween" actions.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QMessageBox
from .errors import OPERATION_ERRORS as _OPERATION_ERRORS, OperationError
from .widgets import TweenCommandPopup
from .progress import close_counter, create_counter, update_counter
from .logging_setup import get_logger
from .undo_entries import DocUndo

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)


class TweenController:
    """Owned by ``MainWindow`` as ``window.tween``.

    A collaborator rather than a mixin -- see main_window_export.py for why.
    """

    def __init__(self, window: "MainWindow"):
        self.window = window

    def _refresh_timeline_tween_marker(self):
        self.window.timeline.refresh(
            self.window.canvas.frames,
            self.window.canvas.current_frame,
            self.window.canvas.active_layer_index,
            getattr(self.window.canvas, "tween_pending", None),
        )

    def _close_tween_command_popup(self):
        popup = self.window._tween_command_popup
        self.window._tween_command_popup = None
        if popup is not None:
            popup.close()
            popup.deleteLater()

    def _show_tween_command_popup(self, mode=None):
        self._close_tween_command_popup()
        pending = getattr(self.window.canvas, "tween_pending", None) or {}
        tween_mode = (
            "mesh"
            if (mode or pending.get("transform_mode")) == "mesh"
            else "free"
        )
        popup = TweenCommandPopup(
            tween_mode,
            pending.get(
                "mesh_cols",
                getattr(self.window.canvas, "transform_mesh_cols", 4),
            ),
            pending.get(
                "mesh_rows",
                getattr(self.window.canvas, "transform_mesh_rows", 4),
            ),
            self.window,
            reverse=bool(
                pending.get(
                    "reverse_generation",
                    False,
                )
            ),
        )
        self.window._tween_command_popup = popup
        popup.commitRequested.connect(self.commit_transform_or_tween)
        popup.cancelRequested.connect(self.cancel_transform_or_tween)
        popup.reverseChanged.connect(
            self.set_reverse_generation
        )
        popup.rotateLeftRequested.connect(
            lambda: self.window.canvas.rotate_selection_transform(-90.0)
        )
        popup.rotateRightRequested.connect(
            lambda: self.window.canvas.rotate_selection_transform(90.0)
        )
        popup.destroyed.connect(
            lambda _obj=None, current=popup:
            setattr(
                self.window,
                "_tween_command_popup",
                None if self.window._tween_command_popup is current
                else self.window._tween_command_popup,
            )
        )
        popup.adjustSize()
        anchor = self.window.timeline_dock.mapToGlobal(
            QPoint(
                max(0, self.window.timeline_dock.width() - popup.sizeHint().width() - 16),
                24,
            )
        )
        popup.move(anchor)
        popup.show()
        popup.raise_()
        popup.activateWindow()

    def set_reverse_generation(self, enabled):
        """生成方向を切り替え、タイムライン記号を即時更新する。"""
        pending = getattr(
            self.window.canvas,
            "tween_pending",
            None,
        )
        if not isinstance(pending, dict):
            return

        reverse = bool(enabled)
        pending["reverse_generation"] = reverse
        self._refresh_timeline_tween_marker()
        self.window.canvas.setFocus()

        transform_mode = (
            "メッシュ変形"
            if pending.get("transform_mode") == "mesh"
            else "自由変形"
        )
        if reverse:
            self.window.statusBar().showMessage(
                f"◆ {transform_mode}の逆生成："
                "キーフレーム側を変形形状、"
                "ラストコマ側を元の初期形状として生成します。",
                5000,
            )
        else:
            self.window.statusBar().showMessage(
                f"♦ {transform_mode}の通常生成："
                "キーフレーム側を元の初期形状、"
                "ラストコマ側を変形形状として生成します。",
                5000,
            )

    def enable(self, visual_row, key_column, mode="free"):
        """タイムライン終端の｜を、自由／メッシュトゥイーンの♦へ切り替える。"""
        mode = "mesh" if mode == "mesh" else "free"
        mode_name = "メッシュ変形" if mode == "mesh" else "自由変形"
        if not self.window.canvas.frames:
            return
        layer_count = len(self.window.canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        key_column = int(key_column)
        if not (
            0 <= layer_index < layer_count
            and 0 <= key_column < len(self.window.canvas.frames)
        ):
            return
        key_layer = self.window.canvas.frames[key_column].layers[layer_index]
        exposure = max(1, int(key_layer.exposure))
        if not key_layer.has_content or exposure < 2:
            QMessageBox.warning(
                self.window,
                "トゥイーン",
                "2コマ以上の表示区間を持つ画像キーフレームで実行してください。",
            )
            return

        answer = QMessageBox.question(
            self.window,
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

        if self.window.canvas.transform_active:
            self.cancel_transform_or_tween()

        # 自由変形が画像を一時的に消去する前の文書全体を保存する。
        # Ctrl+Zではこの状態へ戻すため、元画像が消えることはない。
        tween_undo_snapshot = self.window.canvas.document_snapshot()

        self.window.canvas.current_frame = key_column
        self.window.canvas.active_layer_index = layer_index
        self.window.canvas.tween_pending = None
        self.window.tools.set_tween_active(False)
        self.window.palette.setProperty("tweenActive", False)
        self.window.canvas.selection_polygon = []
        self.window.canvas.selection_mask_override = None
        self.window.canvas.selection_outline_polygons = []
        self.window.canvas.selection_mask_rect = None
        self.window.canvas.lasso = []
        self.window.canvas.rect_start = None
        self.window.canvas.rect_end = None

        # トゥイーンでも現在のTPクオリティ、選択色、線幅を維持する。
        # 「すべてのコマに適用」だけはトゥイーン生成と分離する。
        self.window.tools.selection_all_frames.setChecked(False)
        self.window.colors.set_selected_used_colors(set(self.window.palette.selected_rgbs))
        self.window.tools.select_tool("rect_select")

        if not self.window.canvas.auto_select_used_area():
            QMessageBox.warning(
                self.window,
                "トゥイーン",
                "画像内に変形対象となる描画領域がありません。",
            )
            return

        self.window.line_ops.start_wire_transform(mode)
        if not self.window.canvas.transform_active:
            return

        end_column = key_column + exposure - 1
        self.window.canvas.tween_pending = {
            "layer_index": layer_index,
            "key_col": key_column,
            "end_col": end_column,
            "exposure": exposure,
            "undo_snapshot": tween_undo_snapshot,
            "quality_active": bool(
                self.window.canvas.transform_quality_active
            ),
            "line_threshold": int(
                self.window.canvas.transform_line_threshold
            ),
            "line_colors": tuple(
                self.window.canvas.transform_tp_line_colors
            ),
            "transform_mode": mode,
            "mesh_cols": int(
                getattr(self.window.canvas, "transform_mesh_cols", 4)
            ),
            "mesh_rows": int(
                getattr(self.window.canvas, "transform_mesh_rows", 4)
            ),
            "mesh_reference_points": [
                QPointF(point)
                for point in getattr(
                    self.window.canvas,
                    "transform_mesh_reference_points",
                    [],
                )
            ],
            "start_points": [
                QPointF(point) for point in self.window.canvas.transform_points
            ],
            "reverse_generation": False,
        }
        self.window.tools.set_tween_active(True)
        self.window.palette.setProperty("tweenActive", True)
        self._refresh_timeline_tween_marker()
        self._show_tween_command_popup(mode)
        self.window.canvas.setFocus()
        self.window.statusBar().showMessage(
            f"♦ {mode_name}トゥイーン中です。"
            "変形形状を指定し、ポップアップの"
            "「逆生成」で生成方向を選べます。",
            5000,
        )

    def commit_transform_or_tween(self):
        if getattr(self.window.canvas, "tween_pending", None):
            self.commit_transform()
        else:
            self.window.canvas.commit_selection_transform()

    def cancel_transform_or_tween(self):
        had_tween = bool(getattr(self.window.canvas, "tween_pending", None))
        if self.window.canvas.transform_active:
            self.window.canvas.cancel_selection_transform()
        self.window.canvas.tween_pending = None
        self.window.tools.set_tween_active(False)
        self.window.palette.setProperty("tweenActive", False)
        self._close_tween_command_popup()
        if had_tween:
            self._refresh_timeline_tween_marker()
            self.window.statusBar().showMessage(
                "トゥイーンをキャンセルしました。",
                2200,
            )

    def commit_transform(self):
        """通常生成または逆生成で自由／メッシュ変形形状を補間する。"""
        pending = getattr(self.window.canvas, "tween_pending", None)
        if not pending or not self.window.canvas.transform_active:
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
        self.window.canvas.transform_mode = transform_mode
        if transform_mode == "mesh":
            self.window.canvas.transform_mesh_cols = max(
                2, int(pending.get("mesh_cols", 4))
            )
            self.window.canvas.transform_mesh_rows = max(
                2, int(pending.get("mesh_rows", 4))
            )
            self.window.canvas.transform_mesh_grid = (
                self.window.canvas.transform_mesh_cols
            )
            self.window.canvas._active_mesh_cols = (
                self.window.canvas.transform_mesh_cols
            )
            self.window.canvas._active_mesh_rows = (
                self.window.canvas.transform_mesh_rows
            )
            reference_points = pending.get(
                "mesh_reference_points",
                [],
            )
            if len(reference_points) == (
                self.window.canvas.transform_mesh_cols
                * self.window.canvas.transform_mesh_rows
            ):
                self.window.canvas.transform_mesh_reference_points = [
                    QPointF(point) for point in reference_points
                ]
            else:
                self.window.canvas.transform_mesh_reference_points = (
                    self.window.canvas._regular_mesh_reference_points(
                        self.window.canvas.transform_source_rect,
                        self.window.canvas.transform_mesh_cols,
                        self.window.canvas.transform_mesh_rows,
                    )
                )
        start_points = [
            QPointF(point) for point in pending.get("start_points", [])
        ]
        final_points = [
            QPointF(point) for point in self.window.canvas.transform_points
        ]
        if (
            len(start_points) != len(final_points)
            or not start_points
            or self.window.canvas.transform_original_layer is None
            or self.window.canvas.transform_source is None
        ):
            QMessageBox.warning(
                self.window,
                "トゥイーン",
                "変形情報を取得できないため、トゥイーンを確定できません。",
            )
            return

        undo_snapshot = pending.get("undo_snapshot")

        # トゥイーン開始後に「クオリティ」をON/OFFした場合も、
        # 確定時の現在設定をそのまま使用する。
        quality_active = bool(
            self.window.tools.transform_quality.isChecked()
        )
        current_line_threshold = max(
            1,
            min(
                254,
                int(self.window.tools.transform_line_width.value()),
            ),
        )
        current_line_colors = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in self.window.palette.selected_rgbs
            if rgb is not None and len(rgb) >= 3
        }

        self.window.canvas.transform_quality = quality_active
        self.window.canvas.transform_quality_active = quality_active
        self.window.canvas.transform_apply_all_frames = False
        self.window.canvas.transform_line_threshold = current_line_threshold
        self.window.canvas.selected_used_color_rgbs = set(
            current_line_colors
        )
        self.window.canvas.transform_tp_line_colors = tuple(
            sorted(current_line_colors)
        )

        # 保存済み設定も現在値へ更新し、確定処理の全経路で同じ値を使う。
        pending["quality_active"] = quality_active
        pending["line_threshold"] = current_line_threshold
        pending["line_colors"] = tuple(
            sorted(current_line_colors)
        )

        self.window.canvas._invalidate_tp_preview_cache()

        # クオリティ確定では、通常変形への暗黙フォールバックを許可しない。
        # 元画像から色マスクを作り直し、各トゥイーンコマへ確実に適用する。
        if quality_active:
            if not self.window.canvas._prepare_tp_transform_masks():
                QMessageBox.warning(
                    self.window,
                    "トゥイーン確定",
                    "TPクオリティ用の色マスクを生成できませんでした。\n"
                    "Pillowが利用できることと、変形対象に色があることを"
                    "確認してください。",
                )
                return

        original = self.window.canvas.transform_original_layer.copy()
        source = self.window.canvas.transform_source
        generated_images = []
        total_steps = exposure + 2
        progress = create_counter(
            self.window,
            f"{mode_name}トゥイーンを確定",
            total_steps,
            f"{mode_name}トゥイーン画像を準備しています",
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            update_counter(
                progress,
                1,
                total_steps,
                "開始キーフレームを準備しています",
            )
            for index in range(exposure):
                update_counter(
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
                    self.window.canvas.transform_points = [
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
                    self.window.canvas._invalidate_tp_preview_cache()
                    if quality_active:
                        preview, _target = (
                            self.window.canvas._tp_mask_preview_image(
                                source,
                                original.width(),
                                original.height(),
                            )
                        )
                    else:
                        preview, _target = (
                            self.window.canvas._project_transform_source(
                                source,
                                original.width(),
                                original.height(),
                                smooth=False,
                            )
                        )
                    if preview is None:
                        raise OperationError(
                            f"{index + 1}コマ目の変形画像を生成できませんでした。"
                        )
                    image = self.window.canvas._cleared_selection_base(
                        original
                    )
                    painter = QPainter(image)
                    painter.setCompositionMode(
                        QPainter.CompositionMode.CompositionMode_SourceOver
                    )
                    painter.drawImage(0, 0, preview)
                    painter.end()

                generated_images.append(image)

            self.window.canvas.transform_points = [
                QPointF(point) for point in final_points
            ]
            update_counter(
                progress,
                exposure + 1,
                total_steps,
                "生成した画像をタイムラインへ登録しています",
            )
            self.window.canvas._ensure_frame_count(end_column + 1)
            for offset, image in enumerate(generated_images):
                frame_index = key_column + offset
                layer = self.window.canvas.frames[frame_index].layers[layer_index]
                layer.image = image
                layer.has_content = True
                layer.exposure = 1

            if undo_snapshot is None:
                raise OperationError(
                    "トゥイーン開始前のUndo情報を取得できませんでした。"
                )
            self.window.canvas.push_undo(DocUndo(undo_snapshot))

            update_counter(
                progress,
                total_steps,
                total_steps,
                "トゥイーンのキーフレーム化が完了しました",
            )
        except _OPERATION_ERRORS as exc:
            log.warning("tween keyframe generation failed: %s", exc, exc_info=True)
            # 途中生成に失敗した場合も、開始前の元画像へ戻す。
            if undo_snapshot is not None:
                self.window.canvas.apply_undo_entry(DocUndo(undo_snapshot))
            else:
                self.window.canvas.transform_points = [
                    QPointF(point) for point in final_points
                ]
                self.window.canvas.cancel_selection_transform()
            self.window.canvas.tween_pending = None
            self.window.tools.set_tween_active(False)
            self.window.palette.setProperty("tweenActive", False)
            self._close_tween_command_popup()
            QMessageBox.warning(
                self.window,
                "トゥイーン確定エラー",
                str(exc),
            )
            return
        finally:
            QApplication.restoreOverrideCursor()
            close_counter(progress)

        self.window.canvas._reset_selection_transform()
        self.window.canvas.transform_apply_all_frames = False
        self.window.canvas.tween_pending = None
        self.window.canvas.selection_polygon = []
        self.window.canvas.selection_mask_override = None
        self.window.canvas.selection_outline_polygons = []
        self.window.canvas.selection_mask_rect = None
        self.window.canvas.lasso = []
        self.window.canvas.rect_start = None
        self.window.canvas.rect_end = None
        self.window.canvas.current_frame = (
            key_column
            if reverse_generation
            else end_column
        )
        self.window.canvas.active_layer_index = layer_index
        self.window.canvas._onion_cache.clear()
        self.window.canvas._color_filter_cache.clear()
        self.window.canvas._color_index_cache.clear()
        self.window.canvas._silhouette_cache.clear()
        self._close_tween_command_popup()

        # 補間では使用色自体は増えないため、全コマ色走査は省略する。
        self.window._suppress_used_color_refresh_once = True
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas._update_selection_clear_overlay()
        self.window.canvas.update()
        quality_note = (
            "（TPクオリティ適用）"
            if quality_active else ""
        )
        direction_note = (
            "逆生成（◆側が変形／右端が初期）"
            if reverse_generation
            else "通常生成（先頭が初期／♦側が変形）"
        )
        self.window.statusBar().showMessage(
            f"{exposure}コマの{mode_name}トゥイーンを"
            f"{direction_note}でキーフレーム化しました。"
            f"{quality_note}",
            4200,
        )
