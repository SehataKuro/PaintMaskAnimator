"""Project open/save orchestration for MainWindow.

Split out of ``main_window.py`` as a mixin. These methods drive the save/open
file dialogs, the unsaved-changes guard, and loading a project archive back
into the widgets (the low-level archive read/write lives in ``project_io``).
They run against a live ``MainWindow`` instance.
"""
from .common import *  # noqa: F401,F403
from ._main_window_members import MainWindowMembers
from . import constants, project_io
from .errors import OPERATION_ERRORS as _OPERATION_ERRORS
from .logging_setup import get_logger

log = get_logger(__name__)


class ProjectIOMixin(MainWindowMembers):
    def update_project_title(self):
        if self.current_project_path:
            name = Path(self.current_project_path).name
            self.setWindowTitle(f"{APP_DISPLAY_NAME} — {name}")
        else:
            self.setWindowTitle(f"{APP_DISPLAY_NAME} — 新規プロジェクト")

    def save_project(self):
        if (
            not self.current_project_path
            or Path(self.current_project_path).suffix.lower() != ".pman"
        ):
            return self.save_project_as()
        return self.write_project(self.current_project_path)

    def save_project_as(self):
        initial = (
            str(Path(self.current_project_path).with_suffix(".pman"))
            if self.current_project_path
            else "untitled.pman"
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "名前を付けて保存",
            initial,
            "PaintMaskAnimator Project (*.pman)",
        )
        if not path:
            return False
        if not path.lower().endswith(".pman"):
            path = str(Path(path).with_suffix(".pman"))
        if self.write_project(path):
            self.current_project_path = Path(path)
            self.update_project_title()
            return True
        return False

    def write_project(self, path):
        project_path = Path(path)

        metadata = self.build_project_metadata()

        try:
            project_io.write_project_archive(
                project_path,
                metadata,
                self.canvas.frames,
                self.canvas._sequence_archive,
            )
            self.current_project_path = project_path
            self.update_project_title()
            self.statusBar().showMessage(
                f"プロジェクトを保存しました：{project_path.name}",
                3000,
            )
            return True
        except _OPERATION_ERRORS as error:
            log.error("project save failed: %s", error, exc_info=True)
            QMessageBox.critical(
                self,
                "プロジェクト保存エラー",
                f"保存できませんでした。\n\n{error}",
            )
            return False

    def open_project_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "プロジェクトを開く",
            "",
            "PaintMaskAnimator Project (*.pman);;"
            "旧Oekaki Animation Project (*.oap)",
        )
        if path:
            self.open_project(path)

    def confirm_save_before_dropped_project(self):
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle("プロジェクトを開く")
        dialog.setText(
            "現在のキャンバスを保存してから、"
            "ドロップしたプロジェクトを開きますか？"
        )
        save_button = dialog.addButton(
            "保存する", QMessageBox.ButtonRole.AcceptRole
        )
        discard_button = dialog.addButton(
            "保存しない", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = dialog.addButton(
            "キャンセル", QMessageBox.ButtonRole.RejectRole
        )
        dialog.setDefaultButton(save_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is cancel_button or clicked is None:
            return False
        if clicked is save_button:
            return bool(self.save_project())
        return clicked is discard_button

    def open_dropped_project(self, path):
        project_path = Path(path)
        if project_path.suffix.lower() != ".pman":
            return False
        if not self.confirm_save_before_dropped_project():
            return False
        return self.open_project(project_path)

    def open_project(self, path):
        project_path = Path(path)
        try:
            metadata, loaded_frames, width, height = project_io.read_project_archive(project_path)

            self.palette.clear_categories()
            self._pending_palette_categories = metadata.get(
                "used_color_categories", {}
            )
            self.set_color_chart_data(metadata.get("color_chart", {}))

            constants.CANVAS_WIDTH = width
            constants.CANVAS_HEIGHT = height
            self.canvas.frames = loaded_frames
            self.canvas._sequence_archive = metadata.pop(
                "_loaded_sequence_archive", {}
            )
            self.timeline.sequence_archive = self.canvas._sequence_archive
            clip_source = metadata.get("clip_studio_source")
            self.canvas.clip_studio_source_metadata = (
                dict(clip_source) if isinstance(clip_source, dict) else None
            )
            self.canvas.current_frame = max(
                0,
                min(
                    int(metadata.get("current_frame", 0)),
                    len(loaded_frames) - 1,
                ),
            )
            layer_count = len(
                loaded_frames[self.canvas.current_frame].layers
            )
            self.canvas.active_layer_index = max(
                0,
                min(
                    int(metadata.get("active_layer_index", 0)),
                    layer_count - 1,
                ),
            )

            colors = metadata.get("colors", {})
            self.canvas.main_color = QColor(
                colors.get("main", "#000000")
            )
            self.canvas.sub_color = QColor(
                colors.get("sub", "#FF0000")
            )
            mode = colors.get("mode", "main")
            self.canvas.color_mode = (
                mode if mode in ("main", "sub", "transparent") else "main"
            )
            self.canvas.transparent_display_color = QColor(
                colors.get("background", "#FFFFFF")
            )

            display = metadata.get("display", {})
            self.canvas.silhouette_non_background = bool(
                display.get("silhouette_non_background", False)
            )
            self.canvas.onion_skin = bool(display.get("onion_skin", False))
            self.canvas.onion_previous_count = max(
                0, min(12, int(display.get("onion_previous_count", 1)))
            )
            self.canvas.onion_next_count = max(
                0, min(12, int(display.get("onion_next_count", 1)))
            )
            self.canvas.onion_previous_opacity = max(
                0.01,
                min(1.0, float(display.get("onion_previous_opacity", 0.22))),
            )
            self.canvas.onion_next_opacity = max(
                0.01,
                min(1.0, float(display.get("onion_next_opacity", 0.22))),
            )

            def normalized_onion_levels(raw_values, count):
                values = []
                if isinstance(raw_values, list):
                    for value in raw_values[:12]:
                        try:
                            values.append(
                                max(0, min(100, int(value)))
                            )
                        except (TypeError, ValueError):
                            values.append(100)
                count = max(0, min(12, int(count)))
                while len(values) < count:
                    index = len(values)
                    if count <= 1:
                        default_value = 100
                    else:
                        default_value = int(round(
                            100.0
                            - 55.0
                            * index
                            / float(max(1, count - 1))
                        ))
                    values.append(
                        max(10, min(100, default_value))
                    )
                return values[:count]

            self.canvas.onion_previous_levels = (
                normalized_onion_levels(
                    display.get("onion_previous_levels"),
                    self.canvas.onion_previous_count,
                )
            )
            self.canvas.onion_next_levels = (
                normalized_onion_levels(
                    display.get("onion_next_levels"),
                    self.canvas.onion_next_count,
                )
            )
            self.canvas.onion_center_percent = max(
                0.0,
                min(
                    100.0,
                    float(
                        display.get(
                            "onion_center_percent",
                            50.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_previous_color = QColor(
                display.get("onion_previous_color", "#FF5C5C")
            )
            self.canvas.onion_next_color = QColor(
                display.get("onion_next_color", "#5CA0FF")
            )
            self.canvas.onion_previous_color_enabled = bool(
                display.get("onion_previous_color_enabled", False)
            )
            self.canvas.onion_next_color_enabled = bool(
                display.get("onion_next_color_enabled", False)
            )
            self.canvas.onion_selected_colors_only = bool(
                display.get("onion_selected_colors_only", False)
            )
            self.canvas.onion_previous_shift_x = max(
                -10000.0,
                min(
                    10000.0,
                    float(
                        display.get(
                            "onion_previous_shift_x",
                            0.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_previous_shift_y = max(
                -10000.0,
                min(
                    10000.0,
                    float(
                        display.get(
                            "onion_previous_shift_y",
                            0.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_previous_rotation = (
                (
                    float(
                        display.get(
                            "onion_previous_rotation",
                            0.0,
                        )
                    )
                    + 180.0
                )
                % 360.0
            ) - 180.0
            self.canvas.onion_previous_scale = max(
                1.0,
                min(
                    199.0,
                    float(
                        display.get(
                            "onion_previous_scale",
                            100.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_next_shift_x = max(
                -10000.0,
                min(
                    10000.0,
                    float(
                        display.get(
                            "onion_next_shift_x",
                            0.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_next_shift_y = max(
                -10000.0,
                min(
                    10000.0,
                    float(
                        display.get(
                            "onion_next_shift_y",
                            0.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_next_rotation = (
                (
                    float(
                        display.get(
                            "onion_next_rotation",
                            0.0,
                        )
                    )
                    + 180.0
                )
                % 360.0
            ) - 180.0
            self.canvas.onion_next_scale = max(
                1.0,
                min(
                    199.0,
                    float(
                        display.get(
                            "onion_next_scale",
                            100.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_tu_tb_scale = max(
                0.05,
                min(
                    20.0,
                    float(
                        display.get(
                            "onion_tu_tb_scale",
                            1.0,
                        )
                    ),
                ),
            )

            pressure = metadata.get("pressure", {})
            self.canvas.pressure_enabled = bool(
                pressure.get("enabled", True)
            )
            self.canvas.pressure_min = float(
                pressure.get("minimum", 0.05)
            )
            self.canvas.pressure_max = float(
                pressure.get("maximum", 1.0)
            )
            self.canvas.pressure_curve = float(
                pressure.get("curve", 1.0)
            )
            saved_points = pressure.get("points")
            if isinstance(saved_points, list) and len(saved_points) >= 2:
                self.canvas.pressure_curve_points = saved_points
            else:
                exponent = self.canvas.pressure_curve
                self.canvas.pressure_curve_points = [
                    [0.0, 0.0], [0.5, 0.5 ** exponent], [1.0, 1.0]
                ]

            self.timeline.fps.setValue(
                max(1, min(60, int(metadata.get("fps", 24))))
            )
            self.canvas.undo_stack.clear()
            self.canvas.redo_stack.clear()
            self.canvas.clear_history_branches()
            self.canvas._color_filter_cache.clear()
            self.canvas._silhouette_cache.clear()
            self._used_color_cache.clear()
            self.current_project_path = project_path
            self.update_project_title()
            self.tools.set_colors(
                self.canvas.main_color,
                self.canvas.sub_color,
                self.canvas.color_mode,
                self.canvas.transparent_display_color,
            )
            self.a_silhouette.setChecked(
                self.canvas.silhouette_non_background
            )
            silhouette_button = self.action_panel.button("silhouette")
            if silhouette_button is not None:
                silhouette_button.setChecked(
                    self.canvas.silhouette_non_background
                )
            self.timeline.onion.blockSignals(True)
            self.timeline.onion.setChecked(self.canvas.onion_skin)
            self.timeline.onion.blockSignals(False)
            if not any(
                layer.sequence_number is not None
                for frame in self.canvas.frames
                for layer in frame.layers
                if layer.has_content
            ):
                self.canvas.normalize_sequence_numbers()
            self.canvas.apply_sequence_only_entries()
            self.set_timeline_mode("sheet")
            self.refresh_ui()
            self.schedule_used_color_refresh()
            QTimer.singleShot(0, self.fit_canvas)
            self.statusBar().showMessage(
                f"プロジェクトを開きました：{project_path.name}",
                3000,
            )
            return True
        except _OPERATION_ERRORS as error:
            log.error("project open failed: %s", error, exc_info=True)
            QMessageBox.critical(
                self,
                "プロジェクト読込エラー",
                f"プロジェクトを開けませんでした。\n\n{error}",
            )
            return False
