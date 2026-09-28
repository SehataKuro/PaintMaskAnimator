"""Project open/save orchestration; owned by ``MainWindow`` as ``window.project``.

Drives the save/open file dialogs, the unsaved-changes guard, and loading a
project archive back into the widgets. The low-level archive read/write lives in
``project_io``; this module is the UI orchestration on top of it.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from typing import TYPE_CHECKING
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QMessageBox
from .i18n import tr
from .constants import APP_DISPLAY_NAME
from . import constants, project_io
from .errors import OPERATION_ERRORS as _OPERATION_ERRORS
from .logging_setup import get_logger

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)


class ProjectIOController:
    """Owned by ``MainWindow`` as ``window.project``.

    A collaborator rather than a mixin -- see main_window_export.py for why.
    """

    def __init__(self, window: "MainWindow"):
        self.window = window

    def update_title(self):
        if self.window.current_project_path:
            name = Path(self.window.current_project_path).name
            self.window.setWindowTitle(f"{APP_DISPLAY_NAME} — {name}")
        else:
            self.window.setWindowTitle(tr("{NAME} — 新規プロジェクト").format(NAME=APP_DISPLAY_NAME))

    def save(self):
        if (
            not self.window.current_project_path
            or Path(self.window.current_project_path).suffix.lower() != ".pman"
        ):
            return self.save_as()
        return self.write(self.window.current_project_path)

    def save_as(self):
        initial = (
            str(Path(self.window.current_project_path).with_suffix(".pman"))
            if self.window.current_project_path
            else "untitled.pman"
        )
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            tr("名前を付けて保存"),
            initial,
            "PaintMaskAnimator Project (*.pman)",
        )
        if not path:
            return False
        if not path.lower().endswith(".pman"):
            path = str(Path(path).with_suffix(".pman"))
        if self.write(path):
            self.window.current_project_path = Path(path)
            self.update_title()
            return True
        return False

    def write(self, path):
        project_path = Path(path)

        metadata = self.window.build_project_metadata()

        try:
            project_io.write_project_archive(
                project_path,
                metadata,
                self.window.canvas.frames,
                self.window.canvas._sequence_archive,
            )
            self.window.current_project_path = project_path
            self.update_title()
            self.window.statusBar().showMessage(
                tr("プロジェクトを保存しました：{name}").format(name=project_path.name),
                3000,
            )
            return True
        except _OPERATION_ERRORS as error:
            log.error("project save failed: %s", error, exc_info=True)
            QMessageBox.critical(
                self.window,
                tr("プロジェクト保存エラー"),
                tr("保存できませんでした。\n\n{error}").format(error=error),
            )
            return False

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self.window,
            tr("プロジェクトを開く"),
            "",
            tr("PaintMaskAnimator Project (*.pman);;"
            "旧Oekaki Animation Project (*.oap)"),
        )
        if path:
            self.open(path)

    def confirm_save_before_dropped(self):
        dialog = QMessageBox(self.window)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle(tr("プロジェクトを開く"))
        dialog.setText(
            tr("現在のキャンバスを保存してから、"
            "ドロップしたプロジェクトを開きますか？")
        )
        save_button = dialog.addButton(
            tr("保存する"), QMessageBox.ButtonRole.AcceptRole
        )
        discard_button = dialog.addButton(
            tr("保存しない"), QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = dialog.addButton(
            tr("キャンセル"), QMessageBox.ButtonRole.RejectRole
        )
        dialog.setDefaultButton(save_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is cancel_button or clicked is None:
            return False
        if clicked is save_button:
            return bool(self.save())
        return clicked is discard_button

    def open_dropped(self, path):
        project_path = Path(path)
        if project_path.suffix.lower() != ".pman":
            return False
        if not self.confirm_save_before_dropped():
            return False
        return self.open(project_path)

    def open(self, path):
        project_path = Path(path)
        try:
            metadata, loaded_frames, width, height = project_io.read_project_archive(project_path)

            self.window.palette.clear_categories()
            self.window._pending_palette_categories = metadata.get(
                "used_color_categories", {}
            )
            self.window.color_chart_ops.set_data(metadata.get("color_chart", {}))

            constants.CANVAS_WIDTH = width
            constants.CANVAS_HEIGHT = height
            self.window.canvas.frames = loaded_frames
            self.window.canvas._sequence_archive = metadata.pop(
                "_loaded_sequence_archive", {}
            )
            self.window.timeline.sequence_archive = self.window.canvas._sequence_archive
            clip_source = metadata.get("clip_studio_source")
            self.window.canvas.clip_studio_source_metadata = (
                dict(clip_source) if isinstance(clip_source, dict) else None
            )
            self.window.canvas.current_frame = max(
                0,
                min(
                    int(metadata.get("current_frame", 0)),
                    len(loaded_frames) - 1,
                ),
            )
            layer_count = len(
                loaded_frames[self.window.canvas.current_frame].layers
            )
            self.window.canvas.active_layer_index = max(
                0,
                min(
                    int(metadata.get("active_layer_index", 0)),
                    layer_count - 1,
                ),
            )

            colors = metadata.get("colors", {})
            self.window.canvas.main_color = QColor(
                colors.get("main", "#000000")
            )
            self.window.canvas.sub_color = QColor(
                colors.get("sub", "#FF0000")
            )
            mode = colors.get("mode", "main")
            self.window.canvas.color_mode = (
                mode if mode in ("main", "sub", "transparent") else "main"
            )
            self.window.canvas.transparent_display_color = QColor(
                colors.get("background", "#FFFFFF")
            )

            display = metadata.get("display", {})
            self.window.canvas.silhouette_non_background = bool(
                display.get("silhouette_non_background", False)
            )
            self.window.canvas.onion_skin = bool(display.get("onion_skin", False))
            self.window.canvas.onion_previous_count = max(
                0, min(12, int(display.get("onion_previous_count", 1)))
            )
            self.window.canvas.onion_next_count = max(
                0, min(12, int(display.get("onion_next_count", 1)))
            )
            self.window.canvas.onion_previous_opacity = max(
                0.01,
                min(1.0, float(display.get("onion_previous_opacity", 0.22))),
            )
            self.window.canvas.onion_next_opacity = max(
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

            self.window.canvas.onion_previous_levels = (
                normalized_onion_levels(
                    display.get("onion_previous_levels"),
                    self.window.canvas.onion_previous_count,
                )
            )
            self.window.canvas.onion_next_levels = (
                normalized_onion_levels(
                    display.get("onion_next_levels"),
                    self.window.canvas.onion_next_count,
                )
            )
            self.window.canvas.onion_center_percent = max(
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
            self.window.canvas.onion_previous_color = QColor(
                display.get("onion_previous_color", "#FF5C5C")
            )
            self.window.canvas.onion_next_color = QColor(
                display.get("onion_next_color", "#5CA0FF")
            )
            self.window.canvas.onion_previous_color_enabled = bool(
                display.get("onion_previous_color_enabled", False)
            )
            self.window.canvas.onion_next_color_enabled = bool(
                display.get("onion_next_color_enabled", False)
            )
            self.window.canvas.onion_selected_colors_only = bool(
                display.get("onion_selected_colors_only", False)
            )
            self.window.canvas.onion_previous_shift_x = max(
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
            self.window.canvas.onion_previous_shift_y = max(
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
            self.window.canvas.onion_previous_rotation = (
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
            self.window.canvas.onion_previous_scale = max(
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
            self.window.canvas.onion_next_shift_x = max(
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
            self.window.canvas.onion_next_shift_y = max(
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
            self.window.canvas.onion_next_rotation = (
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
            self.window.canvas.onion_next_scale = max(
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
            self.window.canvas.onion_tu_tb_scale = max(
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

            # 筆圧は描く人とペンの設定なので、ファイルに入っていても使わない
            # （環境設定のプリセットとブラシの設定を使う）。
            self.window.timeline.fps.setValue(
                max(1, min(60, int(metadata.get("fps", 24))))
            )
            self.window.canvas.undo_stack.clear()
            self.window.canvas.redo_stack.clear()
            self.window.canvas.clear_history_branches()
            self.window.canvas._color_filter_cache.clear()
            self.window.canvas._silhouette_cache.clear()
            self.window._used_color_cache.clear()
            self.window.current_project_path = project_path
            self.update_title()
            self.window.tools.set_colors(
                self.window.canvas.main_color,
                self.window.canvas.sub_color,
                self.window.canvas.color_mode,
                self.window.canvas.transparent_display_color,
            )
            self.window.a_silhouette.setChecked(
                self.window.canvas.silhouette_non_background
            )
            silhouette_button = self.window.action_panel.button("silhouette")
            if silhouette_button is not None:
                silhouette_button.setChecked(
                    self.window.canvas.silhouette_non_background
                )
            self.window.timeline.onion.blockSignals(True)
            self.window.timeline.onion.setChecked(self.window.canvas.onion_skin)
            self.window.timeline.onion.blockSignals(False)
            if not any(
                layer.sequence_number is not None
                for frame in self.window.canvas.frames
                for layer in frame.layers
                if layer.has_content
            ):
                self.window.canvas.normalize_sequence_numbers()
            self.window.canvas.apply_sequence_only_entries()
            self.window.timeline_ops.set_mode("sheet")
            self.window.refresh_ui()
            self.window.used_color.schedule_refresh()
            QTimer.singleShot(0, self.window.fit_canvas)
            self.window.statusBar().showMessage(
                tr("プロジェクトを開きました：{name}").format(name=project_path.name),
                3000,
            )
            return True
        except _OPERATION_ERRORS as error:
            log.error("project open failed: %s", error, exc_info=True)
            QMessageBox.critical(
                self.window,
                tr("プロジェクト読込エラー"),
                tr("プロジェクトを開けませんでした。\n\n{error}").format(error=error),
            )
            return False
