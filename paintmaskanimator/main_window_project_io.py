"""Project open/save orchestration; owned by ``MainWindow`` as ``window.project``.

Drives the save/open file dialogs, the unsaved-changes guard, and loading a
project archive back into the widgets. The low-level archive read/write lives in
``project_io``; this module is the UI orchestration on top of it.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
import copy
from dataclasses import fields
from typing import TYPE_CHECKING
from types import SimpleNamespace
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QMessageBox
from .i18n import tr
from .constants import APP_DISPLAY_NAME
from . import config, constants, project_io
from .errors import OperationError, OPERATION_ERRORS as _OPERATION_ERRORS
from .logging_setup import get_logger

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)

#: 「最近使用したファイル」を残す設定のキーと、残す件数。
RECENT_PROJECTS_KEY = "recent_projects"
RECENT_PROJECTS_LIMIT = 10


def prepare_ui_state(metadata):
    """Report malformed restoration values before touching the current UI."""
    try:
        return _prepare_ui_state(metadata)
    except (TypeError, ValueError, OverflowError) as error:
        raise OperationError(tr("プロジェクトの表示設定が不正です。")) from error


def _prepare_ui_state(metadata):
    """Convert every restored value before publishing a loaded document."""
    state = SimpleNamespace()
    colors = metadata.get("colors", {})
    if not isinstance(colors, dict):
        raise OperationError(tr("プロジェクトの色設定が不正です。"))
    state.main_color = QColor(
        colors.get("main", "#000000")
    )
    state.sub_color = QColor(
        colors.get("sub", "#FF0000")
    )
    mode = colors.get("mode", "main")
    state.color_mode = (
        mode if mode in ("main", "sub", "transparent") else "main"
    )
    state.transparent_display_color = QColor(
        colors.get("background", "#FFFFFF")
    )

    display = metadata.get("display", {})
    if not isinstance(display, dict):
        raise OperationError(tr("プロジェクトの表示設定が不正です。"))
    state.silhouette_non_background = bool(
        display.get("silhouette_non_background", False)
    )
    state.onion_skin = bool(display.get("onion_skin", False))
    state.onion_previous_count = max(
        0, min(12, int(display.get("onion_previous_count", 1)))
    )
    state.onion_next_count = max(
        0, min(12, int(display.get("onion_next_count", 1)))
    )
    state.onion_previous_opacity = max(
        0.01,
        min(1.0, float(display.get("onion_previous_opacity", 0.22))),
    )
    state.onion_next_opacity = max(
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

    state.onion_previous_levels = (
        normalized_onion_levels(
            display.get("onion_previous_levels"),
            state.onion_previous_count,
        )
    )
    state.onion_next_levels = (
        normalized_onion_levels(
            display.get("onion_next_levels"),
            state.onion_next_count,
        )
    )
    state.onion_center_percent = max(
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
    state.onion_previous_color = QColor(
        display.get("onion_previous_color", "#FF5C5C")
    )
    state.onion_next_color = QColor(
        display.get("onion_next_color", "#5CA0FF")
    )
    state.onion_previous_color_enabled = bool(
        display.get("onion_previous_color_enabled", False)
    )
    state.onion_next_color_enabled = bool(
        display.get("onion_next_color_enabled", False)
    )
    state.onion_selected_colors_only = bool(
        display.get("onion_selected_colors_only", False)
    )
    state.onion_previous_shift_x = max(
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
    state.onion_previous_shift_y = max(
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
    state.onion_previous_rotation = (
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
    state.onion_previous_scale = max(
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
    state.onion_next_shift_x = max(
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
    state.onion_next_shift_y = max(
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
    state.onion_next_rotation = (
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
    state.onion_next_scale = max(
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
    state.onion_tu_tb_scale = max(
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
    state.fps = max(1, min(60, int(metadata.get("fps", 24))))
    state.memo = str(metadata.get("memo", "") or "")
    playback_range = metadata.get("playback_range", [0, None])
    try:
        range_start, range_end = playback_range
        range_start = max(0, int(range_start))
        range_end = None if range_end is None else max(range_start + 1, int(range_end))
    except (TypeError, ValueError):
        range_start, range_end = 0, None
    state.playback_range_start = range_start
    state.playback_range_end = range_end
    return vars(state)


class ProjectIOController:
    """Owned by ``MainWindow`` as ``window.project``.

    A collaborator rather than a mixin -- see main_window_export.py for why.
    """

    def __init__(self, window: "MainWindow"):
        self.window = window
        self._saved_signature = None

    # ------------------------------------------------------------------
    # 最近使用したファイル
    # ------------------------------------------------------------------
    def recent_projects(self):
        """新しい順のパス一覧（文字列）。"""
        value = config.get_value(RECENT_PROJECTS_KEY, [])
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, str) and item]

    def remember_recent(self, path):
        """開いた・保存したプロジェクトを一覧の先頭に入れる。"""
        try:
            text = str(Path(path).resolve())
        except OSError:
            text = str(path)
        paths = [text] + [item for item in self.recent_projects() if item != text]
        config.set_value(RECENT_PROJECTS_KEY, paths[:RECENT_PROJECTS_LIMIT])

    def forget_recent(self, path):
        paths = [item for item in self.recent_projects() if item != str(path)]
        config.set_value(RECENT_PROJECTS_KEY, paths)

    def clear_recent(self):
        config.set_value(RECENT_PROJECTS_KEY, [])

    def fill_recent_menu(self, menu):
        """ファイル › 最近使用したファイル の中身を、開くたびに作り直す。"""
        menu.clear()
        paths = self.recent_projects()
        if not paths:
            empty = menu.addAction(tr("（なし）"))
            empty.setEnabled(False)
            return
        for index, text in enumerate(paths, 1):
            path = Path(text)
            label = f"&{index % 10}  {path.name}" if index <= 10 else path.name
            action = menu.addAction(label)
            action.setToolTip(text)
            action.setStatusTip(text)
            action.triggered.connect(
                lambda _checked=False, target=text: self.open_recent(target)
            )
        menu.addSeparator()
        menu.addAction(tr("一覧を消去"), self.clear_recent)

    def open_recent(self, path):
        if not Path(path).exists():
            self.forget_recent(path)
            QMessageBox.warning(
                self.window,
                tr("最近使用したファイル"),
                tr("ファイルが見つかりませんでした。一覧から外します。\n\n{path}").format(
                    path=path
                ),
            )
            return False
        return self.open(path)

    def update_title(self):
        suffix = " *" if self.has_unsaved_changes() else ""
        if self.window.current_project_path:
            name = Path(self.window.current_project_path).name
            self.window.setWindowTitle(f"{APP_DISPLAY_NAME} — {name}{suffix}")
        else:
            self.window.setWindowTitle(
                tr("{NAME} — 新規プロジェクト").format(NAME=APP_DISPLAY_NAME) + suffix
            )

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

        try:
            snapshot = project_io.ProjectSnapshot.capture(
                self.window.build_project_metadata(), self.window.canvas._document,
                self.window.crop_image(self.window.canvas.current_frame),
            )
            project_io.write_project_snapshot(project_path, snapshot, self.window.png_cache)
            self.window.current_project_path = project_path
            self.update_title()
            self.remember_recent(project_path)
            self.mark_saved()
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

    def _content_signature(self):
        """Track document content without encoding or scanning image pixels."""
        def layer_signature(layer):
            return tuple(
                value.cacheKey() if hasattr(value, "cacheKey") else (
                    copy.deepcopy(value) if isinstance(value, (dict, list, set)) else value)
                for field in fields(layer)
                for value in (getattr(layer, field.name),)
            )
        canvas = self.window.canvas
        frames = tuple((frame.duration, tuple(layer_signature(layer) for layer in frame.layers))
                       for frame in canvas.frames)
        archive = tuple(
            (li, number, copy.deepcopy(hint))
            for li, track in enumerate(canvas._document.tracks)
            for number, hint in sorted(track.legacy_archive_hints.items())
        )
        metadata = self.window.build_project_metadata()
        for key in (
            "current_frame", "active_layer_index", "colors", "display", "pressure",
            "timeline_mode",
        ):
            metadata.pop(key, None)
        cells = tuple(
            (li, number, cell.image.cacheKey(),
             cell.initial_image.cacheKey() if cell.initial_image is not None else None,
             cell.cell_name)
            for li, layer in enumerate(canvas.frames[0].layers)
            for number, cell in sorted(layer._track.cells.items())
        ) if canvas.frames else ()
        # Compatibility is determined by the live fields above and these
        # image-free import observations. Avoid the writer's projection/resolve
        # and PNG naming work on the 500 ms title timer. Old artwork is excluded.
        compatibility = []
        local = []
        for frame in canvas.frames:
            for slot in frame.layers:
                state = slot.__dict__
                old = state.get("_v1_position")
                legacy = old.legacy if old is not None else None
                compatibility.append((
                    old.key_start if old is not None else None,
                    None if legacy is None else (
                        legacy.has_content, legacy.is_blank_key,
                        legacy.sequence_number, legacy.sequence_only, legacy.exposure),
                    old.input_length if old is not None else None,
                    state.get("_dense_import"), state.get("_v2_length_imported", True),
                ))
                if not ((slot.has_content or slot.sequence_only)
                        and slot.sequence_number is not None):
                    cell = slot._cell
                    local.append((cell.image.cacheKey(),
                                  cell.initial_image.cacheKey()
                                  if cell.initial_image is not None else None,
                                  cell.cell_name))
        return frames, archive, cells, copy.deepcopy(metadata), tuple(compatibility), tuple(local)

    def mark_saved(self):
        self._saved_signature = self._content_signature()
        self.update_title()

    def has_unsaved_changes(self):
        return self._saved_signature != self._content_signature()

    def confirm_save_before_close(self):
        if not self.has_unsaved_changes():
            return True
        dialog = QMessageBox(self.window)
        dialog.setObjectName("unsavedCloseConfirmation")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle(tr("未保存の変更"))
        dialog.setText(tr("変更を保存してから終了しますか？"))
        save_button = dialog.addButton(tr("保存する"), QMessageBox.ButtonRole.AcceptRole)
        discard_button = dialog.addButton(
            tr("保存しない"), QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = dialog.addButton(tr("キャンセル"), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(save_button)
        dialog.setEscapeButton(cancel_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is save_button:
            return bool(self.save())
        return clicked is discard_button

    def confirm_save_before_dropped(self):
        if not self.has_unsaved_changes():
            return True
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
            read_cache = project_io.PngCache()
            loaded = project_io.load_project(project_path, read_cache)
            metadata = loaded.metadata
            ui_state = prepare_ui_state(metadata)

            self.window.palette.clear_categories()
            self.window._pending_palette_categories = metadata.get(
                "used_color_categories", {}
            )
            self.window.color_chart_ops.set_data(metadata.get("color_chart", {}))

            constants.CANVAS_WIDTH = loaded.width
            constants.CANVAS_HEIGHT = loaded.height
            clip_source = metadata.get("clip_studio_source")
            self.window.canvas.clip_studio_source_metadata = (
                dict(clip_source) if isinstance(clip_source, dict) else None
            )
            self.window.canvas._document = loaded.document
            self.window.canvas._invalidate_exposure_index()
            self.window.png_cache.replace_from(read_cache)

            for name, value in ui_state.items():
                if name not in ("fps", "memo"):
                    setattr(self.window.canvas, name, value)
            self.window.timeline.fps.setValue(ui_state["fps"])
            self.window.set_project_memo(ui_state["memo"])
            self.window.palette.set_background_display_color(
                self.window.canvas.transparent_display_color
            )
            self.window.canvas.undo_stack.clear()
            self.window.canvas.redo_stack.clear()
            self.window.canvas.clear_history_branches()
            self.window.canvas._color_filter_cache.clear()
            self.window.canvas._silhouette_cache.clear()
            self.window._used_color_cache.clear()
            self.window.created_app_version = metadata["created_app_version"]
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
            if loaded.format_version == 1 and not any(
                layer.sequence_number is not None
                for frame in self.window.canvas.frames
                for layer in frame.layers
                if layer.has_content
            ):
                self.window.canvas.normalize_sequence_numbers()
            self.window.canvas.apply_sequence_only_entries()
            self.window.timeline_ops.set_mode("sheet", select_number=True)
            self.window.refresh_ui()
            self.window.used_color.schedule_refresh()
            QTimer.singleShot(0, self.window.fit_canvas)
            self.remember_recent(project_path)
            self.mark_saved()
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
