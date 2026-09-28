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
from .i18n import tr
from .errors import OPERATION_ERRORS as _OPERATION_ERRORS, OperationError
from .widgets import TweenCommandPopup
from .progress import close_counter, create_counter, update_counter
from .logging_setup import get_logger
from .undo_entries import DocUndo
from . import tween_groups

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
            tr("メッシュ変形")
            if pending.get("transform_mode") == "mesh"
            else tr("自由変形")
        )
        if reverse:
            self.window.statusBar().showMessage(
                tr("◆ {mode}の逆生成：キーフレーム側を変形形状、ラストコマ側を元の初期形状として生成します。").format(mode=transform_mode),
                5000,
            )
        else:
            self.window.statusBar().showMessage(
                tr("♦ {mode}の通常生成：キーフレーム側を元の初期形状、ラストコマ側を変形形状として生成します。").format(mode=transform_mode),
                5000,
            )

    def enable(self, visual_row, key_column, mode="free"):
        """タイムライン終端の｜を、自由／メッシュトゥイーンの♦へ切り替える。"""
        mode = "mesh" if mode == "mesh" else "free"
        mode_name = tr("メッシュ変形") if mode == "mesh" else tr("自由変形")
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
                tr("トゥイーン"),
                tr("2コマ以上の表示区間を持つ画像キーフレームで実行してください。"),
            )
            return

        answer = QMessageBox.question(
            self.window,
            tr("トゥイーンを有効にする"),
            (
                tr("{exposure}コマの{name}トゥイーンを開始します。\n確定後もトゥイーンとして残り、タイムラインの帯をダブルクリックすると形を直せます。").format(exposure=exposure, name=mode_name)
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
        self._start(
            layer_index, key_column, exposure, mode, tween_undo_snapshot,
        )

    def _start(
        self, layer_index, key_column, exposure, mode, tween_undo_snapshot,
        reverse=False, shape=None, reedit=None,
    ):
        """キーのコマで変形を始め、トゥイーンの編集中にする。

        ``shape`` に保存してあった (変形前の点, 変形後の点) を渡すと、その
        変形から始める（あとから直すとき）。``reedit`` はキャンセル時に戻す
        状態など。
        """
        mode_name = tr("メッシュ変形") if mode == "mesh" else tr("自由変形")
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
            self._restore_reedit(reedit)
            QMessageBox.warning(
                self.window,
                tr("トゥイーン"),
                tr("画像内に変形対象となる描画領域がありません。"),
            )
            return

        self.window.line_ops.start_wire_transform(mode)
        if not self.window.canvas.transform_active:
            self._restore_reedit(reedit)
            return

        start_points = [
            QPointF(point) for point in self.window.canvas.transform_points
        ]
        if shape is not None:
            # キーの絵を描き足して基準の点が少しずれても形が崩れないよう、
            # 保存した「変形前→変形後」の差を今の点に足す。
            saved_start, saved_final = shape
            if len(saved_start) == len(saved_final) == len(start_points):
                self.window.canvas.transform_points = [
                    QPointF(
                        point.x() + final.x() - start.x(),
                        point.y() + final.y() - start.y(),
                    )
                    for point, start, final in zip(
                        start_points, saved_start, saved_final
                    )
                ]
                self.window.canvas._invalidate_tp_preview_cache()
                self.window.canvas.update()

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
            "start_points": start_points,
            "reverse_generation": bool(reverse),
            "reedit": reedit,
        }
        self.window.tools.set_tween_active(True)
        self.window.palette.setProperty("tweenActive", True)
        self._refresh_timeline_tween_marker()
        self._show_tween_command_popup(mode)
        self.window.canvas.setFocus()
        self.window.statusBar().showMessage(
            tr("♦ {name}トゥイーン中です。変形形状を指定し、ポップアップの「逆生成」で生成方向を選べます。").format(name=mode_name),
            5000,
        )

    def _restore_reedit(self, reedit):
        """編集し直しを始められなかったら、確定済みのトゥイーンへ戻す。"""
        if reedit is None:
            return
        self.window.canvas.apply_undo_entry(DocUndo(reedit["snapshot"]))
        self.window.refresh_ui()

    def _member_numbers(self, layer_index, key_column, exposure, previous):
        """中割りのセルの番号。直し直しなら前の番号、なければ末尾に足す。"""
        count = max(0, int(exposure) - 1)
        if previous and len(previous) == count:
            return [int(number) for number in previous]
        used = {
            int(frame.layers[layer_index].sequence_number)
            for frame in self.window.canvas.frames
            if frame.layers[layer_index].sequence_number is not None
        }
        used.update(
            int(number)
            for archived_layer, number in self.window.canvas._sequence_archive
            if int(archived_layer) == layer_index
        )
        next_number = max(used, default=0) + 1
        return list(range(next_number, next_number + count))

    def edit(self, visual_row, key_column):
        """確定済みのトゥイーンを、保存した変形の形から編集し直す。"""
        canvas = self.window.canvas
        if not canvas.frames:
            return
        layer_count = len(canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        key_column = int(key_column)
        length = tween_groups.group_length(canvas.frames, layer_index, key_column)
        if not length:
            return
        if canvas.transform_active:
            self.cancel_transform_or_tween()
        key = canvas.frames[key_column].layers[layer_index]
        spec = dict(key.tween or {})
        reverse = bool(spec.get("reverse", False))
        members = [
            canvas.frames[key_column + offset].layers[layer_index]
            for offset in range(1, length)
        ]
        reedit = {
            "snapshot": canvas.document_snapshot(),
            "id": spec.get("id"),
            "numbers": [member.sequence_number for member in members],
        }
        if any(number is None for number in reedit["numbers"]):
            reedit["numbers"] = None
        # 元の形の絵（通常生成は先頭、逆生成は末尾）をキーへ戻し、
        # 区間を 1 枚のキーにまとめてから変形を始める。
        original = (members[-1] if reverse else key).image.copy()
        key.image = original
        key.exposure = length
        key.tween = None
        for member in members:
            canvas._clear_timeline_layer_cell(member)
        mode = "mesh" if spec.get("mode") == "mesh" else "free"
        if mode == "mesh":
            self.window.tools.transform_mesh_grid_x.setValue(
                int(spec.get("mesh_cols", 4))
            )
            self.window.tools.transform_mesh_grid_y.setValue(
                int(spec.get("mesh_rows", 4))
            )
        canvas._cell_structure_dirty = True
        self._start(
            layer_index, key_column, length, mode, reedit["snapshot"],
            reverse=reverse,
            shape=(
                tween_groups.points_from_data(spec.get("start_points", [])),
                tween_groups.points_from_data(spec.get("final_points", [])),
            ),
            reedit=reedit,
        )

    def release(self, visual_row, key_column):
        """トゥイーンの帯をやめ、中割りを通常のセルとして扱う。"""
        canvas = self.window.canvas
        layer_index = len(canvas.layers) - 1 - int(visual_row)
        key_column = int(key_column)
        length = tween_groups.group_length(canvas.frames, layer_index, key_column)
        if not length:
            return
        canvas.push_doc_undo()
        canvas.frames[key_column].layers[layer_index].tween = None
        for offset in range(1, length):
            canvas.frames[key_column + offset].layers[layer_index].tween_member = None
        canvas._cell_structure_dirty = True
        canvas.changed.emit()
        canvas.update()
        self.window.statusBar().showMessage(
            tr("トゥイーンを解除し、中割りを通常のセルにしました。"), 2500
        )

    def commit_transform_or_tween(self):
        if getattr(self.window.canvas, "tween_pending", None):
            self.commit_transform()
        else:
            self.window.canvas.commit_selection_transform()

    def cancel_transform_or_tween(self):
        pending = getattr(self.window.canvas, "tween_pending", None)
        had_tween = bool(pending)
        reedit = pending.get("reedit") if isinstance(pending, dict) else None
        if self.window.canvas.transform_active:
            self.window.canvas.cancel_selection_transform()
        if reedit is not None:
            # 編集し直しをやめたら、確定済みのトゥイーンをそのまま戻す。
            self.window.canvas.apply_undo_entry(DocUndo(reedit["snapshot"]))
        self.window.canvas.tween_pending = None
        self.window.tools.set_tween_active(False)
        self.window.palette.setProperty("tweenActive", False)
        self._close_tween_command_popup()
        if had_tween:
            self._refresh_timeline_tween_marker()
            self.window.statusBar().showMessage(
                tr("トゥイーンをキャンセルしました。"),
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
            tr("メッシュ変形") if transform_mode == "mesh" else tr("自由変形")
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
                tr("トゥイーン"),
                tr("変形情報を取得できないため、トゥイーンを確定できません。"),
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
                    tr("トゥイーン確定"),
                    tr("TPクオリティ用の色マスクを生成できませんでした。\n"
                    "Pillowが利用できることと、変形対象に色があることを"
                    "確認してください。"),
                )
                return

        original = self.window.canvas.transform_original_layer.copy()
        source = self.window.canvas.transform_source
        generated_images = []
        total_steps = exposure + 2
        progress = create_counter(
            self.window,
            tr("{name}トゥイーンを確定").format(name=mode_name),
            total_steps,
            tr("{name}トゥイーン画像を準備しています").format(name=mode_name),
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            update_counter(
                progress,
                1,
                total_steps,
                tr("開始キーフレームを準備しています"),
            )
            for index in range(exposure):
                update_counter(
                    progress,
                    index + 1,
                    total_steps,
                    tr("{name}の補間コマ {value}/{exposure} を生成しています").format(name=mode_name, value=index + 1, exposure=exposure),
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
                            tr("{value}コマ目の変形画像を生成できませんでした。").format(value=index + 1)
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
                tr("生成した画像をタイムラインへ登録しています"),
            )
            self.window.canvas._ensure_frame_count(end_column + 1)
            reedit = pending.get("reedit") or {}
            tween_id = reedit.get("id") or tween_groups.new_id()
            member_numbers = self._member_numbers(
                layer_index, key_column, exposure, reedit.get("numbers"),
            )
            for offset, image in enumerate(generated_images):
                frame_index = key_column + offset
                layer = self.window.canvas.frames[frame_index].layers[layer_index]
                layer.image = image
                layer.has_content = True
                layer.is_blank_key = False
                layer.exposure = 1
                if offset == 0:
                    layer.tween = tween_groups.make_spec(
                        tween_id, exposure, transform_mode, reverse_generation,
                        start_points, final_points,
                        self.window.canvas.transform_mesh_cols,
                        self.window.canvas.transform_mesh_rows,
                        getattr(
                            self.window.canvas,
                            "transform_mesh_reference_points", [],
                        ),
                    )
                    layer.tween_member = None
                else:
                    # 中割りは番号を持つ通常の絵として書き出される。
                    # 直し直しでは、元の番号をそのまま使う（リテイク対策）。
                    layer.sequence_number = member_numbers[offset - 1]
                    layer.sequence_only = False
                    layer.cell_name = None
                    layer.tween = None
                    layer.tween_member = tween_id

            if undo_snapshot is None:
                raise OperationError(
                    tr("トゥイーン開始前のUndo情報を取得できませんでした。")
                )
            self.window.canvas.push_undo(DocUndo(undo_snapshot))

            update_counter(
                progress,
                total_steps,
                total_steps,
                tr("トゥイーンのキーフレーム化が完了しました"),
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
                tr("トゥイーン確定エラー"),
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
            tr("（TPクオリティ適用）")
            if quality_active else ""
        )
        direction_note = (
            tr("逆生成（◆側が変形／右端が初期）")
            if reverse_generation
            else tr("通常生成（先頭が初期／♦側が変形）")
        )
        self.window.statusBar().showMessage(
            tr("{exposure}コマの{name}トゥイーンを{note}で確定しました。{note2}帯をダブルクリックすると形を直せます。").format(exposure=exposure, name=mode_name, note=direction_note, note2=quality_note),
            4200,
        )
