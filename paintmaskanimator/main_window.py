"""The ``MainWindow`` — application shell, menu/dock wiring, and top-level actions.

Architecture: cohesive method clusters were extracted into ``main_window_<topic>.py``
as ``*Mixin`` classes and composed onto ``MainWindow`` below (autosave, workspaces,
onion skin, export, import, docking, time-remap, timeline ops, layer ops, tween,
used-color, project I/O, color interaction, UI construction, line ops, input).
What remains here is the irreducible shell: application state held on the window,
the ``PaintCanvas`` <-> timeline glue, playback, and document lifecycle
(``new_doc``/``replace_doc``). UI assembly (actions, menus, layout, signal
wiring) now lives in ``main_window_ui_build.py``. Type-only member
declarations shared by the mixins live in ``_main_window_members.py``; the shared
error set is ``errors.OPERATION_ERRORS`` (aliased ``_OPERATION_ERRORS``).
"""
from .common import *  # noqa: F401,F403
from . import constants, imaging, theme, updater
from .actionpanel import ActionPanel
from .canvas import PaintCanvas
from .errors import OPERATION_ERRORS
from .color_panel import UsedColorPanel
from .history_panel import HistoryPanel
from .color_reduction import ColorReductionDialog
from .models import Frame, Layer, make_frame
from .pressure import PressureDialog
from .timeline import TimelineWidget
from .toolpanel import ToolPanel, ToolSelectorPanel
from .utils import blank_image, disable_windows_ink_feedback, workspace_size
from .widgets import (CanvasSizeDialog, ShortcutDialog)
from .logging_setup import get_logger
from .main_window_autosave import AutosaveMixin
from .main_window_color_interaction import ColorInteractionMixin
from .main_window_docking import DockingMixin
from .main_window_export import ExportMixin
from .main_window_import import ImportMixin
from .main_window_input import InputMixin
from .main_window_layer_ops import LayerOpsMixin
from .main_window_line_ops import LineOpsMixin
from .main_window_onion import OnionSkinMixin
from .main_window_project_io import ProjectIOMixin
from .main_window_time_remap import TimeRemapMixin
from .main_window_timeline_ops import TimelineOpsMixin
from .main_window_tween import TweenMixin
from .main_window_ui_build import UIBuildMixin
from .main_window_used_color import UsedColorMixin
from .main_window_workspace import WorkspaceMixin

log = get_logger(__name__)

# Realistic failure set for the top-level user-action handlers (shared with the
# extracted mixins); see errors.py.
_OPERATION_ERRORS = OPERATION_ERRORS


class MainWindow(
    UIBuildMixin, WorkspaceMixin, OnionSkinMixin, ExportMixin, ImportMixin,
    InputMixin, DockingMixin, TimeRemapMixin, TimelineOpsMixin, LayerOpsMixin,
    LineOpsMixin, TweenMixin, UsedColorMixin, ProjectIOMixin,
    ColorInteractionMixin, AutosaveMixin, QMainWindow
):
    def __init__(self):
        super().__init__();self.setWindowTitle(APP_DISPLAY_NAME);self.resize(1500,960);self.setAcceptDrops(True)
        self.canvas=PaintCanvas();self.tool_selector=ToolSelectorPanel();self.tools=ToolPanel();self.timeline=TimelineWidget();self.palette=UsedColorPanel();self.history_panel=HistoryPanel();self.timer=QTimer(self);self.timer.timeout.connect(self.advance)
        # palette_state のUndo/Redoでパネル状態を復元できるよう相互参照を張る。
        self.canvas._palette=self.palette
        self.history_panel.set_canvas(self.canvas)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._playback_started_at=None
        self._playback_emitted_steps=0
        self._used_color_cache = {}
        self._used_color_layer_cache = {}
        self._used_color_request = 0
        self._used_color_timer = QTimer(self)
        self._used_color_timer.setSingleShot(True)
        self._used_color_timer.setInterval(180)
        self._used_color_timer.timeout.connect(self.refresh_used_colors)
        self._pending_visible_colors = None
        self._suppress_used_color_refresh_once = False
        self._tween_command_popup = None
        self._onion_settings_browser = None
        self._onion_settings_dock = None
        self._held_canvas_shortcut_tokens = set()
        self._ui_hold_drag_mode = None
        self._ui_hold_scroll_area = None
        self._ui_hold_grab_widget = None
        self._ui_hold_start_global = QPointF()
        self._ui_hold_last_global = QPointF()
        self._ui_hold_start_scroll = (0, 0)
        self._visible_color_timer = QTimer(self)
        self._visible_color_timer.setSingleShot(True)
        self._visible_color_timer.setInterval(20)
        self._visible_color_timer.timeout.connect(self._apply_pending_visible_colors)
        self._pending_tool_selector_snap = None
        self._tool_selector_resize_drag_active = False
        self._tool_selector_snap_timer = QTimer(self)
        self._tool_selector_snap_timer.setSingleShot(True)
        self._tool_selector_snap_timer.setInterval(180)
        self._tool_selector_snap_timer.timeout.connect(
            self._apply_tool_selector_snap
        )
        self._split_drop_candidate = None
        self._split_drop_dragged_dock = None
        self._split_drop_source_area = None
        self._split_drop_press_pos = None
        self._dock_menu_builders = {}
        self.current_project_path = None
        self.build_actions();self.action_panel=ActionPanel(self);self.build_action_panel();self.build_menu();self.build_ui();self.connect_signals();self.refresh_ui();self.action_panel.reload_python_actions()
        self._refresh_theme_dependent_ui()
        self._sync_tool_selector_swatch()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.update_project_title()
        QTimer.singleShot(0,self.fit_canvas)
        QTimer.singleShot(
            0,
            lambda: disable_windows_ink_feedback(
                self,
                self.canvas,
            ),
        )
        self._setup_autosave()
        QTimer.singleShot(0, self._maybe_restore_autosave)
    def status(self, message, level="info", timeout=4000):
        """Show a severity-coloured message in the bottom status bar.

        ``level`` is one of ``info`` / ``success`` / ``warning`` / ``error``.
        Falls back to the plain status bar if the styled one is unavailable.
        """
        bar = self.statusBar()
        show = getattr(bar, "show_message", None)
        if callable(show):
            show(message, level, timeout)
        else:
            bar.showMessage(message, timeout)

    def set_theme(self, name):
        """Switch the light/dark theme, persist it, and refresh the UI."""
        app = QApplication.instance()
        if app is not None:
            theme.apply_theme(app, name, persist=True)
        self._refresh_theme_dependent_ui()
        if hasattr(self, "theme_actions"):
            for key, action in self.theme_actions.items():
                action.setChecked(key == theme.current_theme())

    def _on_canvas_cell_changed(self, frame_index, layer_index):
        self.canvas.sync_numbered_image_from_cell(
            frame_index,
            layer_index,
        )
        # Existing brush cells do not change timeline structure. Rebuilding the
        # entire table here made brush release scale with the number of imported images.
        structure_dirty = bool(
            getattr(self.canvas, "_cell_structure_dirty", True)
        )
        self.canvas._cell_structure_dirty = True
        if structure_dirty:
            self.timeline.update_cell(
                self.canvas.frames, frame_index, layer_index
            )

        # Opaque brush colors are added immediately through colorUsed. Avoid a
        # full all-frame color scan after every normal 100% stroke.
        normal_opaque_brush = (
            self.canvas.effective_tool() == "brush"
            and float(self.canvas.pen_opacity) >= 0.999
            and not self.canvas.is_pseudo_transparent_color(
                self.canvas.paint_source_color()
            )
        )
        if not normal_opaque_brush:
            self.schedule_used_color_refresh()

    def refresh_ui(self):
        self.canvas.coalesce_numbered_images()
        self.timeline.sequence_archive = self.canvas._sequence_archive
        self.timeline.refresh(
            self.canvas.frames,
            self.canvas.current_frame,
            self.canvas.active_layer_index,
            getattr(self.canvas, "tween_pending", None),
        )
        if self._suppress_used_color_refresh_once:
            self._suppress_used_color_refresh_once = False
        else:
            self.schedule_used_color_refresh()


    def refresh_selection(self):
        if getattr(self, "_closing", False) or not self.canvas.frames:
            return
        self.canvas.current_frame = max(0, min(self.canvas.current_frame, len(self.canvas.frames) - 1))
        layers = self.canvas.layers
        if not layers:
            return
        self.canvas.active_layer_index = max(0, min(self.canvas.active_layer_index, len(layers) - 1))
        self.timeline.select_current(self.canvas.current_exposure(), self.canvas.active_layer_index)
        self.timeline.layer_list.blockSignals(True)
        self.timeline.layer_list.setCurrentRow(len(layers) - 1 - self.canvas.active_layer_index)
        self.timeline.layer_list.blockSignals(False)
        self.timeline.duration.blockSignals(True)
        layer = self.canvas.frames[self.canvas.current_frame].layers[self.canvas.active_layer_index]
        if not layer.has_content:
            source = self.canvas.resolve_key_frame(self.canvas.current_frame, self.canvas.active_layer_index)
            layer = self.canvas.frames[source].layers[self.canvas.active_layer_index] if source is not None else layer
        self.timeline.duration.setValue(max(1, layer.exposure))
        self.timeline.duration.blockSignals(False)
        self.timeline._sync_layer_opacity_slider(layer.opacity)


    @staticmethod
    def _parse_toei_timesheet(raw_text):
        text_value = str(raw_text or "").strip()
        json_start = text_value.find("{")
        if json_start < 0:
            raise ValueError("JSONデータが見つかりません。")
        try:
            payload = json.loads(text_value[json_start:])
        except json.JSONDecodeError as exc:
            raise ValueError(
                "ToeiDigitalTimeSheetのJSONを解析できません。"
                f"\n{exc}"
            ) from exc

        layers = payload.get("layers")
        if not isinstance(layers, list) or not layers:
            raise ValueError("layersデータが見つかりません。")

        selected_layer = None
        for layer in layers:
            frames = (
                layer.get("frames")
                if isinstance(layer, dict) else None
            )
            if isinstance(frames, list) and frames:
                selected_layer = layer
                break
        if selected_layer is None:
            raise ValueError("framesデータが見つかりません。")

        parsed_entries = {}
        for entry in selected_layer.get("frames", []):
            if not isinstance(entry, dict):
                continue
            try:
                frame = int(entry.get("frame"))
            except (TypeError, ValueError):
                continue
            if frame < 0:
                continue

            values = []
            data_items = entry.get("data", [])
            if isinstance(data_items, list):
                for data_item in data_items:
                    if not isinstance(data_item, dict):
                        continue
                    item_values = data_item.get("values", [])
                    if isinstance(item_values, list):
                        values.extend(item_values)
                    elif item_values not in (None, ""):
                        values.append(item_values)

            token = None
            for value in values:
                candidate = str(value).strip()
                if candidate:
                    token = candidate
                    break
            parsed_entries[frame] = token

        if not parsed_entries:
            raise ValueError(
                "有効なToeiDigitalTimeSheetフレームがありません。"
            )

        start_frame = min(parsed_entries)
        end_frame = max(parsed_entries)
        current_state = None
        states = []
        blank_label_count = 0

        for frame in range(start_frame, end_frame + 1):
            if frame in parsed_entries:
                token = parsed_entries[frame]
                if token is None:
                    # 値なしセルは直前セルの状態を保持。
                    pass
                elif re.fullmatch(r"[0-9]+", token):
                    current_state = max(1, int(token))
                else:
                    # 中割トラックラベル／記号セルは空フレーム。
                    current_state = None
                    blank_label_count += 1
            states.append(current_state)

        return {
            "format": "ToeiDigitalTimeSheet",
            "fps": None,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "states": states,
            "blank_label_count": blank_label_count,
        }


    def prepare_color_reduction(self, paths):
        paths = sorted(
            [str(path) for path in paths],
            key=self.canvas._natural_path_key,
        )
        if not paths:
            return None, ""

        first_image, error = self.canvas._read_image_file(
            paths[0]
        )
        if first_image is None:
            return None, (
                f"{Path(paths[0]).name}\n{error}"
            )

        try:
            color_count = (
                self.canvas.opaque_rgb_color_count(first_image)
            )
            alpha_statistics = (
                self.canvas.image_alpha_statistics(first_image)
            )
            background_rgb = (
                self.canvas.detect_opaque_border_background(
                    first_image
                )
            )
            opaque_background = background_rgb is not None
        except _OPERATION_ERRORS as exc:
            log.warning("first-image color/alpha inspection failed: %s", exc, exc_info=True)
            return None, (
                "1枚目の色と透明度を確認できませんでした。\n"
                f"{exc}"
            )

        semi_transparent_count = int(
            alpha_statistics["semi_transparent"]
        )

        # 多色、半透明AA、または白背景画像を調整対象にする。
        if (
            color_count < 100
            and semi_transparent_count <= 0
            and not opaque_background
        ):
            return None, ""

        try:
            dialog = ColorReductionDialog(
                first_image,
                color_count,
                semi_transparent_count,
                opaque_background,
                background_rgb,
                self,
            )
            if (
                dialog.exec()
                != QDialog.DialogCode.Accepted
            ):
                return None, "__cancelled__"
            if not dialog.reduction_enabled:
                return None, ""
            palette = dialog.selected_palette()
        except _OPERATION_ERRORS as exc:
            log.warning("binarization preparation failed: %s", exc, exc_info=True)
            return None, (
                "2値化の準備中に"
                "エラーが発生しました。\n"
                f"{exc}"
            )

        return {
            "palette": palette,
            "extraction_mode": dialog.selected_extraction_mode(),
            "target_colors": int(
                dialog.color_count.value()
            ),
            "source_color_count": int(color_count),
            "alpha_threshold": int(
                dialog.alpha_threshold_255()
            ),
            "semi_transparent_source_pixels": (
                semi_transparent_count
            ),
            "opaque_background": bool(
                dialog.opaque_background
            ),
            "background_rgb": (
                tuple(dialog.background_rgb)
                if dialog.background_rgb is not None
                else None
            ),
            "tone_curve_points": [
                (float(x), float(y))
                for x, y in dialog.tone_curve_points()
            ],
        }, ""

    def prepare_image_import(self, paths):
        # Keep this step light: inspect dimensions only. Color analysis is deferred
        # until after the image is visible and is cached per cell.
        max_width = constants.CANVAS_WIDTH
        max_height = constants.CANVAS_HEIGHT
        for path in paths:
            reader = QImageReader(str(path))
            if hasattr(reader, "setDecideFormatFromContent"):
                reader.setDecideFormatFromContent(True)
            size = reader.size()
            if size.isValid():
                width, height = size.width(), size.height()
            elif PILImage is not None:
                try:
                    with PILImage.open(path) as pil:
                        width, height = pil.size
                except (OSError, ValueError, TypeError) as exc:
                    log.info("PIL size read of %s failed: %s", path, exc)
                    return False, f"{Path(path).name}\n画像サイズを取得できませんでした。\n{exc}"
            else:
                image, error = self.canvas._read_image_file(path)
                if image is None:
                    return False, f"{Path(path).name}\n{error}"
                width, height = image.width(), image.height()
            max_width = max(max_width, width)
            max_height = max(max_height, height)
        if max_width > constants.CANVAS_WIDTH or max_height > constants.CANVAS_HEIGHT:
            self.canvas.push_doc_undo()
            self.replace_doc(max_width, max_height, preserve=True)
            self.statusBar().showMessage(
                f"画像に合わせてキャンバスを {max_width} × {max_height}px に拡張しました。", 3500)
        return True, ""

    def import_dropped_image(self, path):
        color_reduction, error = (
            self.prepare_color_reduction([path])
        )
        if error:
            if error != "__cancelled__":
                QMessageBox.warning(
                    self,
                    "画像読み込み",
                    f"{Path(path).name} を読み込めませんでした。"
                    f"\n\n{error}",
                )
            return

        prepared, error = self.prepare_image_import([path])
        if not prepared:
            if error != "__cancelled__":
                QMessageBox.warning(self, "画像読み込み", f"{Path(path).name} を読み込めませんでした。\n\n{error}")
            return
        ok, error = self.canvas.import_image(
            path,
            color_reduction=color_reduction,
        )
        if not ok:
            QMessageBox.warning(self,"画像読み込み",f"{Path(path).name} を読み込めませんでした。\n\n{error}")
        else:
            self.set_timeline_mode("sequence")
            self._used_color_cache.clear()
            self.schedule_used_color_refresh()

    def import_dropped_images(self, paths, layer_name=None):
        color_reduction, error = (
            self.prepare_color_reduction(paths)
        )
        if error:
            if error != "__cancelled__":
                QMessageBox.warning(
                    self,
                    "連番画像読み込み",
                    "画像を読み込めませんでした。"
                    f"\n\n{error}",
                )
            return

        prepared, error = self.prepare_image_import(paths)
        if not prepared:
            if error != "__cancelled__":
                QMessageBox.warning(self, "連番画像読み込み", f"画像を読み込めませんでした。\n\n{error}")
            return
        # 画像配置だけでなく、その直後の使用色認識まで同じカウンターで表示する。
        estimated_frames = max(1, len(self.canvas.frames) + len(paths))
        combined_total = max(1, len(paths) + estimated_frames)
        progress = self.create_progress_counter(
            "連番画像読み込み",
            combined_total,
            "画像を読み込んでいます",
        )
        ok = False
        error = ""
        try:
            ok, error = self.canvas.import_image_sequence(
                paths,
                lambda value, total, label: self.update_progress_counter(
                    progress,
                    value,
                    combined_total,
                    f"{label}（画像 {value}/{total}）",
                ),
                color_reduction=color_reduction,
                layer_name=layer_name,
            )
            if ok:
                self.set_timeline_mode("sequence")
                self._used_color_timer.stop()
                self._used_color_request += 1
                self._used_color_cache.clear()

                actual_frames = len(self.canvas.frames)
                combined_total = max(1, len(paths) + actual_frames)
                progress.setMaximum(combined_total)
                self.update_progress_counter(
                    progress,
                    len(paths),
                    combined_total,
                    "画像配置完了。使用色を認識しています",
                )
                self.refresh_used_colors(
                    progress=progress,
                    progress_offset=len(paths),
                    progress_total=combined_total,
                    progress_label="使用色を認識しています",
                )
        finally:
            self.close_progress_counter(progress)

        if not ok:
            QMessageBox.warning(
                self,
                "連番画像読み込み",
                f"画像を読み込めませんでした。\n\n{error}",
            )
        elif len(paths) > 1:
            reduction_note = ""
            if color_reduction:
                reduction_method = (
                    "元画像へトーンカーブ適用後に2値化"
                    if color_reduction.get("opaque_background")
                    else "半透明を二値化"
                )
                reduction_note = (
                    f"、{reduction_method}して1枚目の共通パレット"
                    f"{int(color_reduction['target_colors'])}色を適用"
                )
            self.statusBar().showMessage(
                f"{len(paths)}枚の画像をタイムラインへ連番配置"
                f"{reduction_note}し、使用色認識まで完了しました。",
                3600,
            )


    def sync_canvas_view_controls(self, zoom_value, rotation_value):
        zoom_percent = max(self.zoom.minimum(), min(self.zoom.maximum(), int(round(float(zoom_value) * 100))))
        rotation_degrees = max(-180, min(180, int(round(float(rotation_value)))))
        self.zoom.blockSignals(True); self.zoom.setValue(zoom_percent); self.zoom.blockSignals(False)
        self.rot.blockSignals(True); self.rot.setValue(rotation_degrees); self.rot.blockSignals(False)
        self.zoom_label.setText(f"{zoom_percent}%")
        self.rot_label.setText(f"{rotation_degrees}°")

    def set_zoom(self,v):
        self.canvas.set_zoom_around_canvas_center(v / 100)
        self.sync_canvas_view_controls(self.canvas.zoom, self.canvas.rotation)
        self.canvas.update()
    def show_canvas_at_100_percent(self):
        self.canvas.zoom = 1.0
        self.canvas.center_canvas()
        self.sync_canvas_view_controls(self.canvas.zoom, self.canvas.rotation)
        self.canvas.update()
    def set_rot(self,v):
        self.canvas.rotation=float(v)
        self.sync_canvas_view_controls(self.canvas.zoom, self.canvas.rotation)
        self.canvas.update()
    def fit_canvas(self):
        w,h=workspace_size();availw=max(100,self.canvas.width()-40);availh=max(100,self.canvas.height()-40);z=min(availw/w,availh/h);self.canvas.zoom=z;self.canvas.pan=QPointF((self.canvas.width()-w*z)/2,(self.canvas.height()-h*z)/2);self.zoom.blockSignals(True);self.zoom.setValue(int(z*100));self.zoom.blockSignals(False);self.zoom_label.setText(f"{z*100:.0f}%");self.canvas.update()

    def new_doc(self):
        d=CanvasSizeDialog(constants.CANVAS_WIDTH,constants.CANVAS_HEIGHT,"新規作成",self)
        if d.exec():
            self.replace_doc(*d.values())
            self.current_project_path = None
            self.update_project_title()

    def check_for_updates_interactive(self):
        # The update feed is public (installers are hosted outside the site's
        # viewing-page password), so no token/credential is required.
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            result = updater.check_for_update()
        finally:
            QApplication.restoreOverrideCursor()

        status = result.get("status")
        if status == "error":
            QMessageBox.warning(
                self, "更新確認エラー", result.get("message", "不明なエラー")
            )
            return
        if status == "up_to_date":
            QMessageBox.information(
                self,
                "更新の確認",
                f"最新版を使用しています。（現在: v{constants.APP_VERSION}）",
            )
            return

        latest = result.get("latest", "")
        asset = result.get("asset")
        if not asset:
            QMessageBox.information(
                self,
                "更新あり",
                f"新しいバージョン {latest} が利用可能ですが、この環境向けの\n"
                "インストーラが見つかりませんでした。配布ページを確認してください。",
            )
            return

        answer = QMessageBox.question(
            self,
            "更新があります",
            f"新しいバージョン {latest} が利用可能です。\n"
            f"（現在: v{constants.APP_VERSION}）\n\n"
            "ダウンロードしてインストールしますか？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._download_and_run_installer(asset, latest)

    def _download_and_run_installer(self, asset, latest):
        dest = Path(tempfile.gettempdir()) / str(
            asset.get("name", f"PaintMaskAnimator-Setup-{latest}.exe")
        )
        dialog = QProgressDialog(
            "更新をダウンロードしています…", "キャンセル", 0, 100, self
        )
        dialog.setWindowTitle("更新のダウンロード")
        dialog.setAutoClose(False)
        dialog.setMinimumDuration(0)
        cancelled = {"flag": False}
        dialog.canceled.connect(lambda: cancelled.__setitem__("flag", True))

        def on_progress(downloaded, total):
            if total > 0:
                dialog.setValue(int(downloaded * 100 / total))
            QApplication.processEvents()
            if cancelled["flag"]:
                raise RuntimeError("cancelled")

        try:
            updater.download_asset(asset, dest, progress=on_progress)
        except RuntimeError:
            dialog.close()
            return
        except (OSError, ValueError) as error:
            log.warning("update download failed: %s", error, exc_info=True)
            dialog.close()
            QMessageBox.warning(
                self, "ダウンロード失敗", f"更新を取得できませんでした。\n\n{error}"
            )
            return
        dialog.close()

        answer = QMessageBox.question(
            self,
            "インストール",
            "ダウンロードが完了しました。インストーラを起動して\n"
            "アプリを終了します。よろしいですか？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            import os
            if sys.platform.startswith("win"):
                os.startfile(str(dest))  # noqa: SLF001 - Windows installer launch
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(dest)])
            else:
                subprocess.Popen(["xdg-open", str(dest)])
        except (OSError, ValueError) as error:
            log.warning("installer launch failed: %s", error, exc_info=True)
            QMessageBox.warning(
                self, "起動失敗", f"インストーラを起動できませんでした。\n\n{error}"
            )
            return
        self.close()


    def build_project_metadata(self):
        """Assemble the project metadata dict from current widget state."""
        return {
            "format": "PaintMaskAnimatorProject",
            "format_version": 1,
            "application_version": 100,
            "canvas": {
                "width": int(constants.CANVAS_WIDTH),
                "height": int(constants.CANVAS_HEIGHT),
            },
            "fps": int(self.timeline.fps.value()),
            "timeline_mode": str(self.canvas.timeline_mode),
            "current_frame": int(self.canvas.current_frame),
            "active_layer_index": int(self.canvas.active_layer_index),
            "colors": {
                "main": self.image_color_hex(self.canvas.main_color),
                "sub": self.image_color_hex(self.canvas.sub_color),
                "mode": self.canvas.color_mode,
                "background": self.image_color_hex(
                    self.canvas.transparent_display_color
                ),
            },
            "display": {
                "silhouette_non_background": bool(
                    self.canvas.silhouette_non_background
                ),
                "onion_skin": bool(self.canvas.onion_skin),
                "onion_previous_count": int(self.canvas.onion_previous_count),
                "onion_next_count": int(self.canvas.onion_next_count),
                "onion_previous_opacity": float(self.canvas.onion_previous_opacity),
                "onion_next_opacity": float(self.canvas.onion_next_opacity),
                "onion_previous_levels": [
                    int(value)
                    for value in self.canvas.onion_previous_levels
                ],
                "onion_next_levels": [
                    int(value)
                    for value in self.canvas.onion_next_levels
                ],
                "onion_center_percent": float(
                    self.canvas.onion_center_percent
                ),
                "onion_previous_color": self.image_color_hex(
                    self.canvas.onion_previous_color
                ),
                "onion_next_color": self.image_color_hex(
                    self.canvas.onion_next_color
                ),
                "onion_previous_color_enabled": bool(
                    self.canvas.onion_previous_color_enabled
                ),
                "onion_next_color_enabled": bool(
                    self.canvas.onion_next_color_enabled
                ),
                "onion_selected_colors_only": bool(
                    self.canvas.onion_selected_colors_only
                ),
                "onion_previous_shift_x": float(
                    self.canvas.onion_previous_shift_x
                ),
                "onion_previous_shift_y": float(
                    self.canvas.onion_previous_shift_y
                ),
                "onion_previous_rotation": float(
                    self.canvas.onion_previous_rotation
                ),
                "onion_previous_scale": float(
                    self.canvas.onion_previous_scale
                ),
                "onion_next_shift_x": float(
                    self.canvas.onion_next_shift_x
                ),
                "onion_next_shift_y": float(
                    self.canvas.onion_next_shift_y
                ),
                "onion_next_rotation": float(
                    self.canvas.onion_next_rotation
                ),
                "onion_next_scale": float(
                    self.canvas.onion_next_scale
                ),
                "onion_tu_tb_scale": float(
                    self.canvas.onion_tu_tb_scale
                ),
            },
            "pressure": {
                "enabled": bool(self.canvas.pressure_enabled),
                "minimum": float(self.canvas.pressure_min),
                "maximum": float(self.canvas.pressure_max),
                "curve": float(self.canvas.pressure_curve),
                "points": getattr(self.canvas, "pressure_curve_points", [[0.0,0.0],[1.0,1.0]]),
            },
            "frames": [],
        }


    def dragEnterEvent(self, event):
        urls = (
            event.mimeData().urls()
            if event.mimeData().hasUrls() else []
        )
        if any(
            Path(url.toLocalFile()).suffix.lower()
            in (".pman", ".xdts", ".xtds")
            for url in urls
        ):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        project_paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if Path(url.toLocalFile()).suffix.lower() == ".pman"
        ] if event.mimeData().hasUrls() else []
        remap_paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if Path(url.toLocalFile()).suffix.lower()
            in (".xdts", ".xtds")
        ] if event.mimeData().hasUrls() else []
        if project_paths:
            self.open_dropped_project(project_paths[0])
            event.acceptProposedAction()
            return
        if remap_paths:
            self.open_dropped_time_remap(remap_paths[0])
            event.acceptProposedAction()
            return
        super().dropEvent(event)


    def import_images_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "画像を読み込む",
            "",
            "画像 (*.png *.jpg *.jpeg *.tga);;"
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;TGA (*.tga)",
        )
        if not paths:
            return
        if len(paths) == 1:
            self.import_dropped_image(paths[0])
        else:
            self.import_dropped_images(paths)

    def import_image_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "画像フォルダーを読み込む",
            "",
        )
        if not folder:
            return

        folder_path = Path(folder)
        supported_suffixes = {".png", ".jpg", ".jpeg", ".tga"}
        paths = sorted(
            (
                path
                for path in folder_path.iterdir()
                if path.is_file()
                and path.suffix.lower() in supported_suffixes
            ),
            key=self.canvas._natural_path_key,
        )
        if not paths:
            QMessageBox.information(
                self,
                "画像フォルダーを読み込む",
                "選択したフォルダーに対応画像がありません。\n\n"
                "対応形式：PNG、JPEG、TGA",
            )
            return

        self.import_dropped_images(
            [str(path) for path in paths],
            layer_name=folder_path.name,
        )


    def resize_doc(self):
        d=CanvasSizeDialog(constants.CANVAS_WIDTH,constants.CANVAS_HEIGHT,"キャンバスサイズの変更",self)
        if d.exec():self.canvas.push_doc_undo();self.replace_doc(*d.values(),preserve=True)
    def replace_doc(self,w,h,preserve=False):
        old_frames=self.canvas.frames if preserve else None;oldw,oldh=constants.CANVAS_WIDTH,constants.CANVAS_HEIGHT;constants.CANVAS_WIDTH,constants.CANVAS_HEIGHT=w,h
        if preserve:
            new=[]
            for f in old_frames:  # pyright: ignore[reportOptionalIterable]
                ls=[]
                for l in f.layers:
                    ni=blank_image();p=QPainter(ni);p.drawImage(QRectF(OUTSIDE_MARGIN, OUTSIDE_MARGIN, min(oldw, w), min(oldh, h)), l.image, QRectF(OUTSIDE_MARGIN, OUTSIDE_MARGIN, min(oldw, w), min(oldh, h)));p.end();ls.append(Layer(l.name,ni,l.visible,l.opacity,l.is_paper,l.has_content,l.alpha_locked,l.exposure,l.color_filter_enabled,tuple(l.color_filter_rgb) if l.color_filter_rgb is not None else None,bool(l.is_blank_key),l.sequence_number,bool(l.sequence_only)))
                new.append(Frame(ls,f.duration))
            self.canvas.frames=new
        else:
            # A brand-new document must not inherit transient interaction state
            # from the previous one: an active selection/transform or a running
            # playback still references the old (now-discarded) frames and canvas
            # size, which produced stale overlays and intermittent errors on the
            # fresh document.
            if self.canvas._playback_active:
                self.timeline.play.setChecked(False)
                self.play(False)
            self.cancel_transform_or_tween()
            self.canvas.clear_selection()
            self.canvas.frames=[make_frame()];self.canvas.undo_stack.clear();self.canvas.redo_stack.clear();self.set_timeline_mode("sheet")
        self.canvas.current_frame=0
        self.canvas.active_layer_index=0
        if not preserve:
            self.current_project_path = None
            self.update_project_title()
        self.refresh_ui()
        # Allocate the blank stroke-display buffer once the new document UI is
        # back in the event loop, rather than on the user's first pen press.
        QTimer.singleShot(
            0,
            self.canvas.prewarm_blank_stroke_display,
        )
        QTimer.singleShot(0, self.canvas.warm_up_brush_runtime)
        QTimer.singleShot(0,self.fit_canvas)

    def closeEvent(self, event):
        """Stop active timers and UI signals before Qt destroys child widgets."""
        self._closing = True
        try:
            # A closed window must stop filtering application-wide events;
            # otherwise it keeps intercepting input for the rest of the process
            # (and leaks across tests that share one QApplication).
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
        except RuntimeError:
            log.debug("application was deleted during event-filter cleanup", exc_info=True)
        try:
            self.timer.stop()
            self._used_color_timer.stop()
            self._visible_color_timer.stop()
            if getattr(self, "_autosave_timer", None) is not None:
                self._autosave_timer.stop()
            self.timeline.blockSignals(True)
            self.timeline.table.blockSignals(True)
            self.timeline.layer_list.blockSignals(True)
            self.canvas.blockSignals(True)
            self._split_drop_timer.stop()
            self._split_drop_overlay.close()
            for skeleton in self._split_drop_skeletons:
                skeleton.close()
        except RuntimeError:
            log.debug("window child was deleted during shutdown", exc_info=True)
        # A clean shutdown clears the autosave so we don't prompt to restore
        # on the next launch.
        self._clear_autosave()
        event.accept()

    def pressure(self):
        d=PressureDialog(self.canvas.pressure_enabled,self.canvas.pressure_min,self.canvas.pressure_max,getattr(self.canvas,"pressure_curve_points",self.canvas.pressure_curve),self)
        if d.exec():
            self.canvas.pressure_enabled = d.enabled.isChecked()
            self.canvas.pressure_min = d.minimum.value() / 100.0
            self.canvas.pressure_max = d.maximum.value() / 100.0
            self.canvas.pressure_curve_points = d.curve.points()
            self.canvas.pressure_curve = 1.0
    def shortcuts(self):
        categories = [
            ("ファイル・編集", self.file_edit_actions),
            ("ツール", self.tool_action_list),
            ("キャンバス操作", self.canvas_operation_actions),
            ("ツールコマンド", self.tool_command_actions),
            ("タイムライン", self.timeline_actions),
        ]
        ShortcutDialog(categories, self).exec()
    def crop_image(self,fi):return self.canvas.composite(fi,True).copy(OUTSIDE_MARGIN,OUTSIDE_MARGIN,constants.CANVAS_WIDTH,constants.CANVAS_HEIGHT)
    def save_png(self):
        path,_=QFileDialog.getSaveFileName(self,"PNG保存","frame.png","PNG (*.png)");
        if path:self.crop_image(self.canvas.current_frame).save(path if path.lower().endswith('.png') else path+'.png','PNG')
    def exposure_images(self):
        out=[]
        for i,f in enumerate(self.canvas.frames):
            im=self.crop_image(i)
            for _ in range(f.duration):out.append(im.copy())
        return out
    def save_tga_image(self, image, path):
        if PILImage is not None:
            rgba=image.convertToFormat(QImage.Format.Format_RGBA8888)
            width,height=rgba.width(),rgba.height()
            ptr = imaging.qimage_buffer(rgba)
            rows=np.frombuffer(ptr,dtype=np.uint8).reshape((height,rgba.bytesPerLine()))
            pixels=rows[:,:width*4].reshape((height,width,4)).copy()
            pil=PILImage.fromarray(pixels,"RGBA")
            pil.save(str(path),format="TGA",compression="tga_rle")
            return True
        return image.save(str(path),"TGA")

    def save_tga(self):
        path,_=QFileDialog.getSaveFileName(self,"TGA保存","frame.tga","TGA (*.tga)")
        if not path:return
        if not path.lower().endswith(".tga"):path+=".tga"
        if not self.save_tga_image(self.crop_image(self.canvas.current_frame),Path(path)):
            QMessageBox.warning(self,"TGA保存","TGAを保存できませんでした。Pillowの導入を確認してください。")

    def _sheet_duration(self):
        duration = 1
        for column, frame in enumerate(self.canvas.frames):
            for layer in frame.layers:
                if layer.sequence_only:
                    continue
                if layer.has_content or layer.is_blank_key:
                    duration = max(
                        duration,
                        column + max(1, int(layer.exposure)),
                    )
        return duration


    def play(self, on):
        if on:
            fps = max(1, int(self.timeline.fps.value()))
            self._playback_started_at = time.perf_counter()
            self._playback_emitted_steps = 0
            self.canvas.set_playback_active(True)

            # 本来のフレーム間隔より細かく確認し、遅延時は
            # 経過時間に合わせてフレームを追いつかせる。
            poll_interval = max(
                4,
                min(16, int(round(500.0 / fps))),
            )
            self.timer.start(poll_interval)
        else:
            self.timer.stop()
            self._playback_started_at = None
            self._playback_emitted_steps = 0
            self.canvas.set_playback_active(False)

            # 再生中は省略していたタイムライン選択同期を停止時に1回だけ行う。
            self.canvas.selectionChanged.emit()
            self.canvas.update()

    def advance(self):
        if self._playback_started_at is None:
            return
        fps = max(1, int(self.timeline.fps.value()))
        elapsed = max(
            0.0,
            time.perf_counter() - self._playback_started_at,
        )
        expected_steps = int(math.floor(elapsed * fps))
        delta = expected_steps - self._playback_emitted_steps
        if delta <= 0:
            return

        self._playback_emitted_steps = expected_steps
        self.canvas.playback_advance(delta)
