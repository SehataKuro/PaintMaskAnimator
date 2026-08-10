from .common import *  # noqa: F401,F403
import PySide6QtAds as QtAds
from . import config, constants, theme, updater
from .theme import StatusBar
from .actionpanel import ActionPanel
from .canvas import PaintCanvas
from .errors import OPERATION_ERRORS
from .color_panel import UsedColorPanel
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
from .main_window_layer_ops import LayerOpsMixin
from .main_window_onion import OnionSkinMixin
from .main_window_project_io import ProjectIOMixin
from .main_window_time_remap import TimeRemapMixin
from .main_window_timeline_ops import TimelineOpsMixin
from .main_window_tween import TweenMixin
from .main_window_used_color import UsedColorMixin
from .main_window_workspace import WorkspaceMixin

log = get_logger(__name__)

# Realistic failure set for the top-level user-action handlers (shared with the
# extracted mixins); see errors.py.
_OPERATION_ERRORS = OPERATION_ERRORS


class MainWindow(
    WorkspaceMixin, OnionSkinMixin, ExportMixin, ImportMixin, DockingMixin,
    TimeRemapMixin, TimelineOpsMixin, LayerOpsMixin, TweenMixin, UsedColorMixin,
    ProjectIOMixin, ColorInteractionMixin, AutosaveMixin, QMainWindow
):
    def __init__(self):
        super().__init__();self.setWindowTitle(APP_DISPLAY_NAME);self.resize(1500,960);self.setAcceptDrops(True)
        self.canvas=PaintCanvas();self.tool_selector=ToolSelectorPanel();self.tools=ToolPanel();self.timeline=TimelineWidget();self.palette=UsedColorPanel();self.timer=QTimer(self);self.timer.timeout.connect(self.advance)
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
        self.build_actions();self.action_panel=ActionPanel(self);self.build_action_panel();self.build_menu();self.build_ui();self.connect();self.refresh_ui();self.action_panel.reload_python_actions()
        self._refresh_theme_dependent_ui()
        self._sync_tool_selector_swatch()
        QApplication.instance().installEventFilter(self)
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

    def make_shortcut_action(self, name, callback, shortcut=""):
        action = QAction(name, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        action.triggered.connect(callback)
        self.addAction(action)
        return action

    def build_action_panel(self):
        self.action_panel.add_action(
            "silhouette",
            "背景以外を黒シルエット表示",
            lambda _checked=False: self.a_silhouette.trigger(),
            checkable=True,
            source="builtin",
        )
        self.action_panel.add_action(
            "same_image_replacement",
            "同一画像を置換色に登録",
            self.register_same_image_replacements,
            tooltip=(
                "同じタイムライン位置にある上のレイヤーと画素配置を比較し、"
                "一致した色対応を置換色へ登録します。"
            ),
            source="builtin",
        )
        self.action_panel.add_action(
            "main_line_repaint",
            "MainLineRepaint",
            self.main_line_repaint,
            tooltip=(
                "メイン色・サブ色を線レイヤーへ分離し、"
                "抜けた面を周囲の最多色で埋めます。"
            ),
            source="builtin",
        )

    def build_actions(self):
        self.a_new=QAction("新規作成…",self);self.a_new.setShortcut("Ctrl+N");self.a_new.triggered.connect(self.new_doc)
        self.a_resize=QAction("キャンバスサイズの変更…",self);self.a_resize.triggered.connect(self.resize_doc)
        self.a_undo=QAction("元に戻す",self);self.a_undo.setShortcut("Ctrl+Z");self.a_undo.triggered.connect(self.undo_with_used_colors)
        self.a_redo=QAction("やり直す",self);self.a_redo.setShortcut("Ctrl+Y");self.a_redo.triggered.connect(self.redo_with_used_colors)
        self.a_copy=QAction("コピー",self);self.a_copy.setShortcut("Ctrl+C");self.a_copy.triggered.connect(self.canvas.copy_selection)
        self.a_cut=QAction("切り取り",self);self.a_cut.setShortcut("Ctrl+X");self.a_cut.triggered.connect(self.canvas.cut_selection)
        self.a_paste=QAction("貼り付け",self);self.a_paste.setShortcut("Ctrl+V");self.a_paste.triggered.connect(self.canvas.paste_clipboard)
        self.a_open_project=QAction("プロジェクトを開く…",self)
        self.a_open_project.setShortcut("Ctrl+O")
        self.a_open_project.triggered.connect(self.open_project_dialog)

        self.a_import_images=QAction("画像を読み込む…",self)
        self.a_import_images.triggered.connect(self.import_images_dialog)
        self.a_import_folder=QAction("画像フォルダーを読み込む…",self)
        self.a_import_folder.triggered.connect(self.import_image_folder_dialog)
        self.a_import_psd=QAction("PSDを読み込む…",self)
        self.a_import_psd.triggered.connect(self.import_psd_dialog)

        self.a_save_project=QAction("上書き保存",self)
        self.a_save_project.setShortcut("Ctrl+S")
        self.a_save_project.triggered.connect(self.save_project)

        self.a_save_project_as=QAction("名前を付けて保存…",self)
        self.a_save_project_as.setShortcut("Ctrl+Shift+S")
        self.a_save_project_as.triggered.connect(self.save_project_as)

        self.a_save=QAction("現在のコマをPNG書き出し…",self)
        self.a_save.triggered.connect(self.save_png)
        self.a_save_tga=QAction("現在のコマをTGA書き出し…",self)
        self.a_save_tga.triggered.connect(self.save_tga)
        self.a_export_png_seq=QAction("連番PNG＋CSV書き出し…",self);self.a_export_png_seq.triggered.connect(lambda:self.export_key_sequence("PNG"))
        self.a_export_tga_seq=QAction("連番TGA＋CSV書き出し…",self);self.a_export_tga_seq.triggered.connect(lambda:self.export_key_sequence("TGA"))
        self.a_export_xdts=QAction("XDTSタイムシートを書き出す…",self)
        self.a_export_xdts.triggered.connect(self.export_xdts_dialog)
        self.a_export_psd=QAction("PSDを書き出す…",self)
        self.a_export_psd.triggered.connect(self.export_psd_dialog)
        self.a_prev=QAction("前のフレーム",self);self.a_prev.setShortcut("1");self.a_prev.triggered.connect(self.previous_timeline_frame)
        self.a_next=QAction("次のフレーム",self);self.a_next.setShortcut("2");self.a_next.triggered.connect(self.next_timeline_frame)
        self.a_pressure=QAction("筆圧設定…",self);self.a_pressure.triggered.connect(self.pressure)
        self.a_isolate_color=QAction("選択色だけ表示",self)
        self.a_isolate_color.triggered.connect(self.isolate_selected_color)
        self.a_clear_color_filter=QAction("特定色表示を解除",self)
        self.a_clear_color_filter.triggered.connect(self.clear_selected_color_filter)
        self.a_swap_main_sub=QAction("メインカラーとサブカラーを交換",self)
        self.a_swap_main_sub.triggered.connect(self.swap_main_sub)
        self.a_silhouette=QAction("背景以外を黒シルエット表示",self); self.a_silhouette.setCheckable(True)
        self.a_silhouette.triggered.connect(self.toggle_silhouette)
        self.a_remove_dust=QAction("ゴミ取り／塗り抜け…",self)
        self.a_remove_dust.triggered.connect(self.remove_dust_fill_surrounding)
        self.a_shortcuts=QAction("ショートカット設定…",self);self.a_shortcuts.triggered.connect(self.shortcuts)
        self.tool_actions={}
        defaults={"brush":"P","line":"U","shape":"O","bucket":"G","lasso_fill":"F","lasso":"L","rect_select":"R","auto_select":"W","eyedropper":"","dust":"D"}
        for tid,label in ToolPanel.TOOLS:
            a=QAction("ツール："+label,self);a.setShortcut(defaults.get(tid,""));a.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut);a.triggered.connect(lambda _=False,t=tid:self.tools.select_tool(t));self.addAction(a);self.tool_actions[tid]=a
        self.general_actions=[("新規作成",self.a_new),("プロジェクトを開く",self.a_open_project),("上書き保存",self.a_save_project),("元に戻す",self.a_undo),("やり直す",self.a_redo),("前のフレーム",self.a_prev),("次のフレーム",self.a_next)]
        self.tool_action_list=[("ツール："+label,self.tool_actions[tid]) for tid,label in ToolPanel.TOOLS]

        # 押している間だけ有効になるキャンバス操作。QActionは設定値の保持に使い、
        # 通常のアプリケーションショートカットとしては登録しない。
        self.a_hold_hand = QAction("ハンド（押している間）", self)
        self.a_hold_hand.setProperty("holdOperation", True)
        self.a_hold_hand.setProperty("holdShortcutText", "Space")
        self.a_hold_hand.setShortcut(QKeySequence("Space"))
        self.a_hold_zoom = QAction("拡大縮小（押している間）", self)
        self.a_hold_zoom.setProperty("holdOperation", True)
        self.a_hold_zoom.setProperty("holdShortcutText", "Ctrl+Space")
        self.a_hold_zoom.setShortcut(QKeySequence("Ctrl+Space"))
        self.a_hold_rotate = QAction("回転（押している間）", self)
        self.a_hold_rotate.setProperty("holdOperation", True)
        self.a_hold_rotate.setProperty("holdShortcutText", "Shift+Space")
        self.a_hold_rotate.setShortcut(QKeySequence("Shift+Space"))
        self.a_hold_eyedropper = QAction("スポイト（押している間）", self)
        self.a_hold_eyedropper.setProperty("holdOperation", True)
        self.a_hold_eyedropper.setProperty("holdShortcutText", "Alt")
        self.a_hold_eyedropper.setShortcut(QKeySequence("Alt"))
        self.canvas_operation_actions = [
            ("ハンド", self.a_hold_hand),
            ("拡大縮小", self.a_hold_zoom),
            ("回転", self.a_hold_rotate),
            ("スポイト", self.a_hold_eyedropper),
        ]

        # File and edit actions not previously exposed in the shortcut dialog.
        for action in (
            self.a_new, self.a_open_project, self.a_import_images,
            self.a_save_project, self.a_save_project_as,
            self.a_resize, self.a_undo, self.a_redo,
            self.a_cut, self.a_copy, self.a_paste, self.a_save, self.a_save_tga,
            self.a_export_png_seq, self.a_export_tga_seq, self.a_prev, self.a_next,
            self.a_pressure, self.a_isolate_color, self.a_clear_color_filter,
            self.a_swap_main_sub, self.a_silhouette, self.a_remove_dust,
        ):
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)

        self.a_export_mp4 = self.make_shortcut_action(
            "MP4書き出し", self.export_mp4
        )

        # Tool-panel commands.
        self.a_select_main = self.make_shortcut_action(
            "描画色：メインを選択", lambda: self.set_color_mode("main")
        )
        self.a_select_sub = self.make_shortcut_action(
            "描画色：サブを選択", lambda: self.set_color_mode("sub")
        )
        self.a_select_transparent = self.make_shortcut_action(
            "描画色：背景色を選択", lambda: self.set_color_mode("transparent")
        )
        self.a_toggle_draw_background = self.make_shortcut_action(
            "描画色と背景色を切り替え", self.toggle_draw_background_color, "C"
        )
        self.a_swap_main_sub = self.make_shortcut_action(
            "メイン色とサブ色を入れ替え", self.swap_main_sub, "X"
        )
        self.a_choose_background = self.make_shortcut_action(
            "背景色の表示色を変更", self.choose_background_color
        )
        self.a_mainline_repaint = self.make_shortcut_action(
            "MainLineRepaint", self.main_line_repaint
        )
        self.a_selection_transform = self.make_shortcut_action(
            "選択範囲：自由変形", lambda: self.start_wire_transform("free")
        )
        self.a_selection_scale = self.make_shortcut_action(
            "選択範囲：拡大縮小", lambda: self.start_wire_transform("scale")
        )
        self.a_selection_mesh = self.make_shortcut_action(
            "選択範囲：メッシュ変形", lambda: self.start_wire_transform("mesh")
        )
        self.a_selection_clear = self.make_shortcut_action(
            "選択範囲を解除", self.canvas.clear_selection, "Ctrl+Shift+A"
        )
        self.a_transform_rotate_left = self.make_shortcut_action(
            "変形：左へ90°回転",
            lambda: self.canvas.rotate_selection_transform(-90.0)
        )
        self.a_transform_rotate_right = self.make_shortcut_action(
            "変形：右へ90°回転",
            lambda: self.canvas.rotate_selection_transform(90.0)
        )
        self.a_transform_mesh_grid = self.make_shortcut_action(
            "メッシュ変形：格子数を変更…",
            self.choose_transform_mesh_grid
        )
        self.a_transform_commit = self.make_shortcut_action(
            "変形を確定", self.commit_transform_or_tween
        )
        self.a_transform_commit.setShortcuts([
            QKeySequence(Qt.Key.Key_Return),
            QKeySequence(Qt.Key.Key_Enter),
        ])
        self.a_transform_cancel = self.make_shortcut_action(
            "変形をキャンセル", self.cancel_transform_or_tween
        )
        self.a_bucket_include_sub = self.make_shortcut_action(
            "バケツ：含み塗り ON/OFF",
            lambda: self.tools.bucket_include_sub.toggle()
        )
        self.a_bucket_close_gap = self.make_shortcut_action(
            "バケツ：隙間閉じ ON/OFF",
            lambda: self.tools.bucket_close_gap.toggle()
        )
        self.a_dust_all_frames = self.make_shortcut_action(
            "ゴミ取り：すべてのコマ ON/OFF",
            lambda: self.tools.dust_all_frames.toggle()
        )
        self.a_dust_apply = self.make_shortcut_action(
            "ゴミ取り／塗り抜けを適用", self.remove_dust_fill_surrounding
        )
        self.a_selection_all_frames = self.make_shortcut_action(
            "選択変形：すべてのコマ ON/OFF",
            self.tools.toggle_selection_all_frames
        )

        # Timeline commands.
        self.a_tl_add_exposure = self.make_shortcut_action(
            "タイムライン：コマ数を1つ増やす",
            self.timeline._extend_current_exposure,
        )
        self.a_tl_delete_frame = self.make_shortcut_action(
            "タイムライン：コマを削除",
            self.delete_timeline_frame,
        )
        self.a_tl_previous = self.make_shortcut_action(
            "タイムライン：前のフレーム",
            self.previous_timeline_frame,
        )
        self.a_tl_next = self.make_shortcut_action(
            "タイムライン：次のフレーム",
            self.next_timeline_frame,
        )
        self.a_tl_previous_key = self.make_shortcut_action(
            "タイムライン：前のコマ",
            self.previous_timeline_key,
            "A",
        )
        self.a_tl_next_key = self.make_shortcut_action(
            "タイムライン：次のコマ",
            self.next_timeline_key,
            "S",
        )
        self.a_tl_play = self.make_shortcut_action(
            "タイムライン：再生／停止",
            lambda: self.timeline.play.toggle(),
        )
        self.a_tl_paste_time_remap = self.make_shortcut_action(
            "タイムライン：タイムリマップを貼り付け",
            self.show_time_remap_paste_dialog,
        )
        self.a_tl_onion = self.make_shortcut_action(
            "タイムライン：オニオンスキン設定",
            lambda: self.timeline.onion.toggle(),
        )
        self.a_tl_layer_add = self.make_shortcut_action(
            "タイムライン：レイヤー追加",
            self.canvas.add_layer,
        )
        self.a_tl_layer_delete = self.make_shortcut_action(
            "タイムライン：レイヤー削除",
            self.canvas.delete_layer,
        )
        self.a_tl_layer_rename = self.make_shortcut_action(
            "タイムライン：レイヤー名変更",
            self.timeline.rename_selected_layer,
        )
        self.file_edit_actions = [
            ("新規作成", self.a_new),
            ("プロジェクトを開く", self.a_open_project),
            ("画像を読み込む", self.a_import_images),
            ("画像フォルダーを読み込む", self.a_import_folder),
            ("上書き保存", self.a_save_project),
            ("名前を付けて保存", self.a_save_project_as),
            ("現在のコマをPNG書き出し", self.a_save),
            ("現在のコマをTGA書き出し", self.a_save_tga),
            ("連番PNG＋CSV書き出し", self.a_export_png_seq),
            ("連番TGA＋CSV書き出し", self.a_export_tga_seq),
            ("MP4書き出し", self.a_export_mp4),
            ("元に戻す", self.a_undo),
            ("やり直す", self.a_redo),
            ("切り取り", self.a_cut),
            ("コピー", self.a_copy),
            ("貼り付け", self.a_paste),
            ("キャンバスサイズ変更", self.a_resize),
            ("筆圧設定", self.a_pressure),
            ("背景以外を黒シルエット表示", self.a_silhouette),
            ("選択色だけ表示", self.a_isolate_color),
            ("特定色表示を解除", self.a_clear_color_filter),
            ("メイン色とサブ色を交換", self.a_swap_main_sub),
            ("ゴミ取り", self.a_remove_dust),
            ("選択範囲を解除", self.a_selection_clear),
        ]
        self.tool_command_actions = [
            ("描画色：メインを選択", self.a_select_main),
            ("描画色：サブを選択", self.a_select_sub),
            ("描画色：背景色を選択", self.a_select_transparent),
            ("描画色と背景色を切り替え", self.a_toggle_draw_background),
            ("メイン色とサブ色を入れ替え", self.a_swap_main_sub),
            ("背景色の表示色を変更", self.a_choose_background),
            ("MainLineRepaint", self.a_mainline_repaint),
            ("選択範囲：自由変形", self.a_selection_transform),
            ("選択範囲：拡大縮小", self.a_selection_scale),
            ("選択範囲：メッシュ変形", self.a_selection_mesh),
            ("選択範囲を解除", self.a_selection_clear),
            ("変形：左へ90°回転", self.a_transform_rotate_left),
            ("変形：右へ90°回転", self.a_transform_rotate_right),
            ("メッシュ変形：格子数を変更", self.a_transform_mesh_grid),
            ("変形を確定", self.a_transform_commit),
            ("変形をキャンセル", self.a_transform_cancel),
            ("バケツ：含み塗り ON/OFF", self.a_bucket_include_sub),
            ("バケツ：隙間閉じ ON/OFF", self.a_bucket_close_gap),
            ("ゴミ取り：すべてのコマ ON/OFF", self.a_dust_all_frames),
            ("ゴミ取り／塗り抜けを適用", self.a_dust_apply),
            ("選択変形：すべてのコマ ON/OFF", self.a_selection_all_frames),
        ]
        self.timeline_actions = [
            ("コマ数を1つ増やす", self.a_tl_add_exposure),
            ("コマを削除", self.a_tl_delete_frame),
            ("タイムリマップを貼り付け", self.a_tl_paste_time_remap),
            ("前のフレーム", self.a_tl_previous),
            ("前のコマ", self.a_tl_previous_key),
            ("再生／停止", self.a_tl_play),
            ("次のコマ", self.a_tl_next_key),
            ("次のフレーム", self.a_tl_next),
            ("オニオンスキン設定", self.a_tl_onion),
            ("レイヤー追加", self.a_tl_layer_add),
            ("レイヤー削除", self.a_tl_layer_delete),
            ("レイヤー名変更", self.a_tl_layer_rename),
        ]
    def build_menu(self):
        f=self.menuBar().addMenu("ファイル")
        f.addAction(self.a_new)
        f.addAction(self.a_open_project)
        f.addAction(self.a_import_images)
        f.addAction(self.a_import_folder)
        f.addAction(self.a_import_psd)
        f.addSeparator()
        f.addAction(self.a_save_project)
        f.addAction(self.a_save_project_as)
        f.addSeparator()
        f.addAction(self.a_save)
        f.addAction(self.a_save_tga)
        f.addAction(self.a_export_png_seq)
        f.addAction(self.a_export_tga_seq)
        f.addAction(self.a_export_xdts)
        f.addAction(self.a_export_psd)
        f.addAction(self.a_export_mp4)
        e=self.menuBar().addMenu("編集");e.addAction(self.a_undo);e.addAction(self.a_redo);e.addSeparator();e.addAction(self.a_cut);e.addAction(self.a_copy);e.addAction(self.a_paste);e.addSeparator();e.addAction(self.a_silhouette);e.addAction(self.a_isolate_color);e.addAction(self.a_clear_color_filter);e.addAction(self.a_swap_main_sub);e.addAction(self.a_remove_dust);e.addSeparator();e.addAction(self.a_resize);e.addAction(self.a_shortcuts);e.addAction(self.a_pressure)
        selection_menu=self.menuBar().addMenu("選択範囲")
        selection_menu.addAction(self.a_selection_clear)
        selection_menu.addSeparator()
        selection_menu.addAction(self.a_selection_transform)
        selection_menu.addAction(self.a_selection_scale)
        selection_menu.addAction(self.a_selection_mesh)
        selection_menu.addSeparator()
        selection_menu.addAction(self.a_transform_rotate_left)
        selection_menu.addAction(self.a_transform_rotate_right)
        selection_menu.addAction(self.a_transform_mesh_grid)
        selection_menu.addSeparator()
        selection_menu.addAction(self.a_transform_commit)
        selection_menu.addAction(self.a_transform_cancel)
        a=self.menuBar().addMenu("アニメーション")
        a.addAction(self.a_prev)
        a.addAction(self.a_next)
        a.addSeparator()
        a.addAction(self.a_tl_previous_key)
        a.addAction(self.a_tl_next_key)
        a.addSeparator()
        a.addAction(self.a_tl_paste_time_remap)
        self._build_view_menu()
    def _build_view_menu(self):
        view_menu = self.menuBar().addMenu("表示")
        theme_menu = view_menu.addMenu("テーマ")
        group = QActionGroup(self)
        group.setExclusive(True)
        self.theme_actions = {}
        labels = {"light": "ライト（明るい）", "dark": "ダーク（暗い）"}
        active = theme.current_theme()
        for name in theme.available_themes():
            action = QAction(labels.get(name, name), self)
            action.setCheckable(True)
            action.setChecked(name == active)
            action.triggered.connect(lambda _=False, n=name: self.set_theme(n))
            group.addAction(action)
            theme_menu.addAction(action)
            self.theme_actions[name] = action
        accent_menu = view_menu.addMenu("アクセントカラー")
        for label, hexval in theme.ACCENT_PRESETS:
            act = QAction(f"{label}", self)
            act.triggered.connect(
                lambda _=False, h=hexval: self.set_accent(h)
            )
            accent_menu.addAction(act)
        accent_menu.addSeparator()
        custom = QAction("カスタム…", self)
        custom.triggered.connect(self.choose_accent_color)
        accent_menu.addAction(custom)
    def _refresh_theme_dependent_ui(self):
        """Re-apply palette-derived styles after a theme/accent change."""
        bar = self.statusBar()
        if hasattr(bar, "refresh_palette"):
            bar.refresh_palette()
        if hasattr(self.tools, "apply_theme"):
            self.tools.apply_theme()
        if hasattr(self.tool_selector, "apply_theme"):
            self.tool_selector.apply_theme()
        if hasattr(self.timeline, "apply_theme"):
            self.timeline.apply_theme()
        if hasattr(self, "dock_manager"):
            self._customize_docking_hover()
    def build_ui(self):
        QtAds.CDockManager.setConfigFlag(
            QtAds.CDockManager.eConfigFlag.AlwaysShowTabs, True
        )
        QtAds.CDockManager.setConfigFlag(
            QtAds.CDockManager.eConfigFlag.DockAreaHasUndockButton, False
        )
        QtAds.CDockManager.setConfigFlag(
            QtAds.CDockManager.eConfigFlag.DockAreaHasTabsMenuButton, False
        )
        QtAds.CDockManager.setConfigFlag(
            QtAds.CDockManager.eConfigFlag.ActiveTabHasCloseButton, False
        )
        QtAds.CDockManager.setConfigFlag(
            QtAds.CDockManager.eConfigFlag.DockAreaHasCloseButton, False
        )
        QtAds.CDockManager.setConfigFlag(
            QtAds.CDockManager.eConfigFlag.OpaqueSplitterResize, True
        )
        QtAds.CDockManager.setConfigFlag(
            QtAds.CDockManager.eConfigFlag.FloatingContainerForceQWidgetTitleBar,
            True,
        )
        QtAds.CDockManager.setConfigFlag(
            QtAds.CDockManager.eConfigFlag.FloatingContainerForceNativeTitleBar,
            False,
        )
        QtAds.CDockManager.setConfigFlag(
            QtAds.CDockManager.eConfigFlag.DoubleClickUndocksWidget, True
        )
        self.setDockNestingEnabled(True)
        self.status_bar = StatusBar(self)
        self.setStatusBar(self.status_bar)
        center=QWidget()
        cv=QVBoxLayout(center)
        cv.setContentsMargins(0,0,0,0)
        self.canvas.setMinimumSize(160, 40)
        cv.addWidget(self.canvas,1)
        bar=QHBoxLayout()
        self.zoom=QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(5,800)
        self.zoom.setValue(100)
        self.zoom_label=QLabel("100%")
        self.rot=QSlider(Qt.Orientation.Horizontal)
        self.rot.setRange(-180,180)
        self.rot_label=QLabel("0°")
        b100=QPushButton("100%表示")
        bfit=QPushButton("全体を表示")
        b0=QPushButton("0°")
        for w in (QLabel("拡大"),self.zoom,self.zoom_label,b100,bfit,QLabel("回転"),self.rot,self.rot_label,b0):
            bar.addWidget(w)
        cv.addLayout(bar)
        self.dock_manager = QtAds.CDockManager(self)
        self._customize_docking_hover()
        self._setup_split_drop_overlay()
        self.dock_manager.floatingWidgetCreated.connect(
            self._configure_floating_window
        )
        self.central_dock = QtAds.CDockWidget(self.dock_manager, "キャンバス")
        self.central_dock.setObjectName("canvasDock")
        self.central_dock.setWidget(
            center, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        self.dock_manager.setCentralWidget(self.central_dock)
        # setCentralWidget() intentionally strips movable/floatable in ADS;
        # restore them afterwards because the canvas is detachable here.
        self.central_dock.setFeatures(
            QtAds.CDockWidget.DockWidgetFeature.DockWidgetFocusable
            | QtAds.CDockWidget.DockWidgetFeature.DockWidgetMovable
            | QtAds.CDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.central_dock.topLevelChanged.connect(
            lambda floating: self._sync_floating_title(
                self.central_dock, floating
            )
        )

        self.tools.setMinimumWidth(180)
        self.tools.setMaximumWidth(16777215)
        self.tools.setMinimumHeight(0)
        self.palette.setMinimumWidth(220)
        self.palette.setMaximumWidth(16777215)
        self.palette.setMinimumHeight(0)

        self.tools_scroll = QScrollArea()
        self.tools_scroll.setWidgetResizable(True)
        self.tools_scroll.setMinimumSize(0, 0)
        self.tools_scroll.setWidget(self.tools)

        self.palette_scroll = QScrollArea()
        self.palette_scroll.setWidgetResizable(True)
        self.palette_scroll.setMinimumSize(0, 0)
        self.palette_scroll.setWidget(self.palette)

        # 描画色パネルは廃止。描画色（メイン/サブ/背景）はツールバー最下部の
        # スウォッチへ移設した。drawing_color_box 自体は色状態の保持用として
        # 構築されるが、ドックには表示しない。
        self.color_wheel_scroll = QScrollArea()
        self.color_wheel_scroll.setWidgetResizable(True)
        self.color_wheel_scroll.setMinimumSize(0, 0)
        self.color_wheel_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.color_wheel_scroll.setWidget(self.tools.color_wheel_box)

        self.color_slider_scroll = QScrollArea()
        self.color_slider_scroll.setWidgetResizable(True)
        self.color_slider_scroll.setMinimumSize(0, 0)
        self.color_slider_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.color_slider_scroll.setWidget(self.tools.color_slider_box)

        self.tool_selector_dock=QtAds.CDockWidget(self.dock_manager, "ツール")
        self.tool_selector_dock.setObjectName("toolSelectorDock")
        self.tool_selector_dock.setFeatures(
            QtAds.CDockWidget.DockWidgetFeature.DockWidgetMovable
            | QtAds.CDockWidget.DockWidgetFeature.DockWidgetFocusable
            | QtAds.CDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.tool_selector_dock.setMinimumSizeHintMode(
            QtAds.CDockWidget.eMinimumSizeHintMode.MinimumSizeHintFromContentMinimumSize
        )
        self.tool_selector_dock.setWidget(
            self.tool_selector, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        tool_area = self.dock_manager.addDockWidget(
            QtAds.LeftDockWidgetArea, self.tool_selector_dock
        )

        self.tools_dock=QtAds.CDockWidget(self.dock_manager, "ツールプロパティ")
        self.tools_dock.setObjectName("toolsDock")
        self.tools_dock.setWidget(
            self.tools_scroll, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        tools_area = self.dock_manager.addDockWidget(
            QtAds.RightDockWidgetArea, self.tools_dock, tool_area
        )

        self.action_panel_scroll = QScrollArea()
        self.action_panel_scroll.setWidgetResizable(True)
        self.action_panel_scroll.setMinimumSize(0, 0)
        self.action_panel_scroll.setWidget(self.action_panel)
        self.action_panel_dock=QtAds.CDockWidget(self.dock_manager, "アクション")
        self.action_panel_dock.setObjectName("actionPanelDock")
        self.action_panel_dock.setWidget(
            self.action_panel_scroll, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        self.dock_manager.addDockWidget(
            QtAds.BottomDockWidgetArea, self.action_panel_dock, tools_area
        )

        self.color_wheel_dock=QtAds.CDockWidget(self.dock_manager, "カラーサークル")
        self.color_wheel_dock.setObjectName("colorWheelDock")
        self.color_wheel_dock.setWidget(
            self.color_wheel_scroll, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        # カラーサークルを右エリアのアンカーにする（旧・描画色ドックの位置）。
        drawing_area = self.dock_manager.addDockWidget(
            QtAds.RightDockWidgetArea, self.color_wheel_dock
        )
        wheel_area = drawing_area

        self.color_slider_dock=QtAds.CDockWidget(self.dock_manager, "カラースライダー")
        self.color_slider_dock.setObjectName("colorSliderDock")
        self.color_slider_dock.setWidget(
            self.color_slider_scroll, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        self.dock_manager.addDockWidget(
            QtAds.BottomDockWidgetArea, self.color_slider_dock, wheel_area
        )

        self.palette_dock=QtAds.CDockWidget(self.dock_manager, "使用色")
        self.palette_dock.setObjectName("paletteDock")
        self.palette_dock.setWidget(
            self.palette_scroll, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        self.dock_manager.addDockWidget(
            QtAds.BottomDockWidgetArea, self.palette_dock, drawing_area
        )

        self.timeline_dock=QtAds.CDockWidget(self.dock_manager, "タイムライン")
        self.timeline_dock.setObjectName("timelineDock")
        self.timeline.setMaximumHeight(16777215)
        self.timeline_dock.setWidget(
            self.timeline, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        self.timeline_dock.setMinimumHeight(70)
        self.timeline_dock.setMaximumHeight(16777215)
        self.dock_manager.addDockWidget(
            QtAds.BottomDockWidgetArea, self.timeline_dock
        )
        # 各パネルのタブ左端に付けるハンバーガーメニューの内容。
        # 未登録のパネルは閉じる／フロートの共通項目だけになる。
        dock_menu_builders = {
            self.color_wheel_dock: self._build_color_wheel_menu,
            self.color_slider_dock: self._build_color_slider_menu,
        }
        for dock in (
            self.tool_selector_dock,
            self.tools_dock,
            self.action_panel_dock,
            self.color_wheel_dock,
            self.color_slider_dock,
            self.palette_dock,
            self.timeline_dock,
        ):
            dock.topLevelChanged.connect(
                lambda floating, current=dock:
                self._sync_floating_title(current, floating)
            )
            self._add_dock_hamburger(dock, dock_menu_builders.get(dock))
        self.dock_manager.dockAreasAdded.connect(
            lambda *_args: QTimer.singleShot(
                0, self._sync_all_area_hamburgers
            )
        )
        self.dock_manager.dockWidgetAdded.connect(
            lambda *_args: QTimer.singleShot(
                0, self._sync_all_area_hamburgers
            )
        )
        self._build_workspace_menu()
        self._finalize_startup_dock_ui()
        QTimer.singleShot(
            0,
            lambda: self._resize_tool_selector_area(
                self.tool_selector.width_for_columns(1)
            ),
        )

        panel_menu=self.menuBar().addMenu("パネル")
        view_menu=panel_menu
        view_menu.addAction(self.tool_selector_dock.toggleViewAction())
        view_menu.addAction(self.tools_dock.toggleViewAction())
        view_menu.addAction(self.action_panel_dock.toggleViewAction())
        view_menu.addAction(self.color_wheel_dock.toggleViewAction())
        view_menu.addAction(self.color_slider_dock.toggleViewAction())
        view_menu.addAction(self.palette_dock.toggleViewAction())
        view_menu.addAction(self.timeline_dock.toggleViewAction())

        help_menu=self.menuBar().addMenu("ヘルプ")
        a_check_update=QAction("更新を確認…", self)
        a_check_update.triggered.connect(self.check_for_updates_interactive)
        help_menu.addAction(a_check_update)

        self.zoom.valueChanged.connect(self.set_zoom)
        b100.clicked.connect(lambda:self.set_zoom(100))
        bfit.clicked.connect(self.fit_canvas)
        self.rot.valueChanged.connect(self.set_rot)
        b0.clicked.connect(lambda:self.rot.setValue(0))


    def _resize_tool_selector_area(self, content_width):
        dock = self.tool_selector_dock
        frame_width = max(0, dock.width() - self.tool_selector.width())
        target_width = int(content_width) + frame_width
        child = dock
        parent = dock.parentWidget()
        while parent is not None:
            if isinstance(parent, QSplitter) and parent.orientation() == Qt.Orientation.Horizontal:
                index = parent.indexOf(child)
                sizes = parent.sizes()
                if 0 <= index < len(sizes):
                    delta = sizes[index] - target_width
                    sizes[index] = target_width
                    recipients = [i for i in range(len(sizes)) if i != index]
                    if recipients:
                        recipient = max(recipients, key=lambda i: sizes[i])
                        sizes[recipient] = max(1, sizes[recipient] + delta)
                    parent.setSizes(sizes)
                return
            child = parent
            parent = parent.parentWidget()

    def connect(self):
        self.tool_selector.toolChanged.connect(self.tools.select_tool)
        self.tool_selector.snapWidthRequested.connect(
            self._snap_tool_selector_width
        )
        self.tools.toolChanged.connect(self.tool_selector.set_active_tool)
        self.tools.toolChanged.connect(self.canvas.set_tool)
        self.tools.size.valueChanged.connect(self.canvas.set_pen_size)
        self.tools.brush_stabilizer.valueChanged.connect(
            self.canvas.set_brush_stabilizer
        )
        self.tools.size.pressureRequested.connect(self.pressure)
        self.tools.opacity.valueChanged.connect(
            lambda value: setattr(self.canvas, "pen_opacity", value / 100)
        )
        self.tools.colorModeChanged.connect(self.set_color_mode)
        self.tools.colorChanged.connect(self.set_color_value)
        # Keep the tool-bar drawing-colour swatch in sync with the panel.
        self.tool_selector.colorModeRequested.connect(self.set_color_mode)
        self.tool_selector.backgroundColorRequested.connect(
            self.choose_background_color
        )
        self.tools.colorModeChanged.connect(
            lambda _m=None: self._sync_tool_selector_swatch()
        )
        self.tools.colorChanged.connect(
            lambda *_a: self._sync_tool_selector_swatch()
        )
        self.tools.main_btn.colorPicked.connect(
            lambda color: self.apply_sampled_color_to_mode("main", color)
        )
        self.tools.sub_btn.colorPicked.connect(
            lambda color: self.apply_sampled_color_to_mode("sub", color)
        )
        self.tools.meshCommitRequested.connect(self.canvas.commit_mesh)
        self.tools.meshCancelRequested.connect(self.canvas.cancel_mesh)
        self.tools.selectionTransformRequested.connect(
            lambda: self.start_wire_transform("free")
        )
        self.tools.selectionScaleRequested.connect(
            lambda: self.start_wire_transform("scale")
        )
        self.tools.selectionMeshRequested.connect(
            lambda: self.start_wire_transform("mesh")
        )
        self.tools.selectionClearRequested.connect(self.canvas.clear_selection)
        self.tools.selectionRotateRequested.connect(
            self.canvas.rotate_selection_transform
        )
        self.tools.transformMeshGridChanged.connect(
            self.canvas.set_transform_mesh_grid
        )
        self.tools.transform_quality.toggled.connect(
            self.canvas.set_transform_quality
        )
        self.tools.transform_line_width.valueChanged.connect(
            self.canvas.set_transform_line_threshold
        )
        self.tools.transform_line_width.sliderPressed.connect(
            self.canvas.begin_transform_line_adjustment
        )
        self.tools.transform_line_width.sliderReleased.connect(
            self.canvas.finish_transform_line_adjustment
        )
        self.canvas.set_transform_quality(
            self.tools.transform_quality.isChecked()
        )
        self.canvas.set_transform_line_threshold(
            self.tools.transform_line_width.value()
        )
        self.tools.selectionCommitRequested.connect(
            self.commit_transform_or_tween
        )
        self.tools.selectionCancelRequested.connect(
            self.cancel_transform_or_tween
        )
        self.tools.flipLayerRequested.connect(self.canvas.flip_active_layer)
        self.tools.swapMainSubRequested.connect(self.swap_main_sub)
        self.tools.resetMainSubRequested.connect(self.reset_main_sub)
        self.tools.removeDustRequested.connect(self.remove_dust_fill_surrounding)
        self.tools.backgroundColorRequested.connect(self.choose_background_color)

        self.timeline.frameSelected.connect(self.select_timeline_exposure)
        self.timeline.addFrameRequested.connect(
            self.canvas.add_frame
        )
        self.timeline.extendExposureRequested.connect(
            lambda row, key, exposure:
                self.resize_timeline_exposure(
                    row,
                    key,
                    key + max(1, exposure),
                    "right",
                )
        )
        self.timeline.deleteFrameRequested.connect(
            self.delete_timeline_frame
        )
        self.timeline.previousRequested.connect(self.previous_timeline_frame)
        self.timeline.nextRequested.connect(self.next_timeline_frame)
        self.timeline.previousKeyRequested.connect(self.previous_timeline_key)
        self.timeline.nextKeyRequested.connect(self.next_timeline_key)
        self.timeline.durationChanged.connect(self.canvas.set_duration)
        self.timeline.cellMoveRequested.connect(self.move_timeline_cell)
        self.timeline.cellCopyRequested.connect(self.copy_timeline_cell)
        self.timeline.sequenceRecallRequested.connect(self.recall_sequence_number)
        self.timeline.multiCellMoveRequested.connect(
            self.move_timeline_selection
        )
        self.timeline.timelineModeChanged.connect(
            self.set_timeline_mode
        )
        self.timeline.normalizeNumbersRequested.connect(
            self.normalize_timeline_numbers
        )
        self.timeline.blankFrameRequested.connect(
            self.create_blank_timeline_key
        )
        self.timeline.exposureResizeRequested.connect(self.resize_timeline_exposure)
        self.timeline.tweenRequested.connect(
            lambda row, column: self.enable_tween(row, column, "free")
        )
        self.timeline.tweenMeshRequested.connect(
            lambda row, column: self.enable_tween(row, column, "mesh")
        )
        self.timeline.tweenCancelRequested.connect(
            self.cancel_transform_or_tween
        )
        self.timeline.onionPopupToggled.connect(
            self.toggle_onion_settings_popup
        )
        self.timeline.onionChanged.connect(self.set_onion_skin)
        self.timeline.timeRemapPasteRequested.connect(
            self.show_time_remap_paste_dialog
        )
        self.timeline.timeRemapFileDropped.connect(
            self.open_dropped_time_remap
        )
        self.timeline.playRequested.connect(self.play)

        self.canvas.changed.connect(self.refresh_ui)
        self.canvas.selectionChanged.connect(self.refresh_selection)
        self.canvas.selectionCleared.connect(
            self.palette._clear_used_color_selection
        )
        self.canvas.cellChanged.connect(self._on_canvas_cell_changed)
        self.canvas.colorUsed.connect(self.palette.add_color)

        self.timeline.layerSelected.connect(self.layer_selected)
        self.timeline.layerVisibilityChanged.connect(self.layer_visibility_row)
        self.timeline.layerOpacityChanged.connect(self.layer_opacity_row)
        self.timeline.layer_opacity_slider.sliderPressed.connect(
            self.canvas.push_doc_undo
        )
        self.timeline.layerNameChanged.connect(self.layer_name_row)
        self.timeline.layerMoveRequested.connect(self.move_layer_row)
        self.timeline.addLayerRequested.connect(self.add_layer_fast)
        self.timeline.deleteLayerRequested.connect(self.canvas.delete_layer)
        self.timeline.duplicateLayersRequested.connect(self.duplicate_layer_rows)
        self.timeline.mergeLayersRequested.connect(self.merge_layer_rows)
        self.timeline.deleteLayersRequested.connect(self.delete_layer_rows)

        self.canvas.imagesDropped.connect(self.import_dropped_images)
        self.canvas.projectDropped.connect(self.open_dropped_project)
        self.canvas.timeRemapDropped.connect(self.open_dropped_time_remap)
        self.canvas.colorSampled.connect(self.apply_sampled_color)
        self.canvas.status_message.connect(
            lambda message: self.statusBar().showMessage(message, 2500)
        )
        self.canvas.viewChanged.connect(self.sync_canvas_view_controls)
        self.canvas.onionInteractionChanged.connect(
            self.sync_onion_browser_from_canvas
        )
        self.canvas.onionInteractionFinished.connect(
            self.finish_onion_browser_interaction
        )

        self.palette.isolateColorClicked.connect(self.apply_palette_isolate_color)
        self.palette.mainColorRequested.connect(
            lambda color: self.apply_sampled_color_to_mode("main", color)
        )
        self.palette.sourceScreenColorPicked.connect(self.apply_sampled_color)
        self.palette.applyReplacementRequested.connect(
            self.apply_palette_replacements
        )
        self.palette.mergeColorsRequested.connect(self.apply_palette_merge)
        self.palette.deleteColorsRequested.connect(
            self.apply_palette_delete
        )
        self.palette.adjustLineThicknessRequested.connect(
            self.adjust_parent_line_thickness
        )
        self.palette.focusColorRequested.connect(self.focus_used_color)
        self.palette.clearIsolateRequested.connect(
            self.clear_selected_color_filter
        )
        self.palette.maskColorsChanged.connect(self.set_mask_colors)
        self.palette.selectedColorsChanged.connect(self.set_selected_used_colors)
        self.palette.visibleColorsChanged.connect(self.set_visible_colors)

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
    def _image_contains_rgb(image, rgb):
        if image is None or image.isNull():
            return False
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        if width <= 0 or height <= 0:
            return False
        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, rgba.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))
        target = np.array(
            [int(rgb[0]), int(rgb[1]), int(rgb[2])],
            dtype=np.uint8,
        )
        return bool(
            np.any(
                (pixels[:, :, 3] > 0)
                & np.all(pixels[:, :, :3] == target, axis=2)
            )
        )

    @staticmethod
    def _mask_contours(mask):
        """2値マスクの外周と穴の輪郭を画素境界上のポリゴンとして返す。"""
        mask = np.asarray(mask, dtype=bool)
        if mask.ndim != 2 or not np.any(mask):
            return []

        top = mask.copy()
        top[1:, :] &= ~mask[:-1, :]
        right = mask.copy()
        right[:, :-1] &= ~mask[:, 1:]
        bottom = mask.copy()
        bottom[:-1, :] &= ~mask[1:, :]
        left = mask.copy()
        left[:, 1:] &= ~mask[:, :-1]
        edges = set()
        for y, x in zip(*np.nonzero(top)):
            edges.add(((int(x), int(y)), (int(x) + 1, int(y))))
        for y, x in zip(*np.nonzero(right)):
            edges.add(((int(x) + 1, int(y)), (int(x) + 1, int(y) + 1)))
        for y, x in zip(*np.nonzero(bottom)):
            edges.add(((int(x) + 1, int(y) + 1), (int(x), int(y) + 1)))
        for y, x in zip(*np.nonzero(left)):
            edges.add(((int(x), int(y) + 1), (int(x), int(y))))

        outgoing = {}
        for start, end in edges:
            outgoing.setdefault(start, []).append(end)

        direction_index = {
            (1, 0): 0,
            (0, 1): 1,
            (-1, 0): 2,
            (0, -1): 3,
        }
        unused = set(edges)
        contours = []
        while unused:
            start_edge = min(unused)
            start, current = start_edge
            unused.remove(start_edge)
            contour = [start, current]
            previous = start

            while current != start:
                candidates = [
                    end for end in outgoing.get(current, ())
                    if (current, end) in unused
                ]
                if not candidates:
                    contour = []
                    break
                previous_direction = direction_index[
                    (current[0] - previous[0], current[1] - previous[1])
                ]

                def turn_priority(
                    end,
                    current=current,
                    previous_direction=previous_direction,
                ):
                    next_direction = direction_index[
                        (end[0] - current[0], end[1] - current[1])
                    ]
                    turn = (next_direction - previous_direction) % 4
                    return ({1: 0, 0: 1, 3: 2, 2: 3}[turn], end)

                next_point = min(candidates, key=turn_priority)
                unused.remove((current, next_point))
                previous, current = current, next_point
                contour.append(current)

            if len(contour) >= 4 and contour[-1] == contour[0]:
                contours.append(contour[:-1])

        simplified_contours = []
        for contour in contours:
            simplified = []
            for index, point in enumerate(contour):
                previous = contour[index - 1]
                following = contour[(index + 1) % len(contour)]
                if (
                    (previous[0] == point[0] == following[0])
                    or (previous[1] == point[1] == following[1])
                ):
                    continue
                simplified.append(QPointF(point[0], point[1]))
            if len(simplified) >= 3:
                simplified_contours.append(simplified)
        return simplified_contours

    @classmethod
    def _largest_mask_outer_contour(cls, mask):
        contours = cls._mask_contours(mask)
        return cls._largest_contour(contours)

    @staticmethod
    def _largest_contour(contours):
        if not contours:
            return []

        def area(points):
            values = [(point.x(), point.y()) for point in points]
            return abs(0.5 * sum(
                x1 * y2 - x2 * y1
                for (x1, y1), (x2, y2) in zip(
                    values,
                    values[1:] + values[:1],
                )
            ))

        return max(contours, key=area)


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
            try:
                reader.setDecideFormatFromContent(True)
            except AttributeError:
                pass
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


    def swap_main_sub(self):
        self.canvas.main_color, self.canvas.sub_color = (
            QColor(self.canvas.sub_color),
            QColor(self.canvas.main_color),
        )
        self.tools.set_colors(
            self.canvas.main_color,
            self.canvas.sub_color,
            self.canvas.color_mode,
            self.canvas.transparent_display_color,
        )
        self.statusBar().showMessage("メインカラーとサブカラーを交換しました。", 1800)

    def reset_main_sub(self):
        self.canvas.main_color = QColor("black")
        self.canvas.sub_color = QColor("white")
        if self.canvas.color_mode not in ("main", "sub"):
            self.canvas.color_mode = "main"
        self.tools.set_colors(
            self.canvas.main_color,
            self.canvas.sub_color,
            self.canvas.color_mode,
            self.canvas.transparent_display_color,
        )
        self.canvas.update()
        self.statusBar().showMessage("メイン色とサブ色を初期化しました。", 1800)

    def toggle_silhouette(self, checked=None):
        if checked is None:
            checked = not self.canvas.silhouette_non_background
        checked = bool(checked)
        self.canvas.silhouette_non_background = checked

        if hasattr(self, "a_silhouette"):
            self.a_silhouette.blockSignals(True)
            self.a_silhouette.setChecked(checked)
            self.a_silhouette.blockSignals(False)

        silhouette_button = self.action_panel.button("silhouette")
        if silhouette_button is not None:
            silhouette_button.blockSignals(True)
            silhouette_button.setChecked(checked)
            silhouette_button.blockSignals(False)

        self.canvas._silhouette_cache.clear()
        self.canvas.update()
        self.statusBar().showMessage(
            "背景以外を黒シルエット表示しています。"
            if checked else
            "黒シルエット表示を解除しました。",
            2200,
        )

    def choose_transform_mesh_grid(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("メッシュ格子数")
        form = QFormLayout(dialog)

        cols_spin = QSpinBox(dialog)
        rows_spin = QSpinBox(dialog)
        for spin in (cols_spin, rows_spin):
            spin.setRange(2, 12)
        cols_spin.setValue(
            max(2, int(getattr(self.canvas, "transform_mesh_cols", 4)))
        )
        rows_spin.setValue(
            max(2, int(getattr(self.canvas, "transform_mesh_rows", 4)))
        )
        form.addRow("横の格子数", cols_spin)
        form.addRow("縦の格子数", rows_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        cols = cols_spin.value()
        rows = rows_spin.value()
        for spin, value in (
            (self.tools.transform_mesh_grid_x, cols),
            (self.tools.transform_mesh_grid_y, rows),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.canvas.set_transform_mesh_grid(cols, rows)
        if not self.canvas.transform_active:
            self.statusBar().showMessage(
                f"次回のメッシュ変形を 横{cols}×縦{rows} 格子に設定しました。",
                2200,
            )


    def start_wire_transform(self, mode, line_colors_override=None):
        if not self.canvas.selection_polygon:
            if not self.canvas.auto_select_used_area():
                QMessageBox.warning(
                    self,
                    "変形",
                    "使用色がある領域を検出できないため、変形を開始できません。",
                )
                return
            self.statusBar().showMessage(
                "選択範囲がなかったため、使用色がある領域を自動選択しました。",
                2600,
            )
        self.canvas.transform_mesh_cols = self.tools.transform_mesh_grid_x.value()
        self.canvas.transform_mesh_rows = self.tools.transform_mesh_grid_y.value()
        self.canvas.transform_mesh_grid = self.canvas.transform_mesh_cols

        active_line_colors = (
            {
                tuple(int(channel) for channel in rgb[:3])
                for rgb in line_colors_override
            }
            if line_colors_override is not None
            else {
                tuple(int(channel) for channel in rgb[:3])
                for rgb in self.palette.selected_rgbs
            }
        )
        self.canvas.set_transform_line_colors(active_line_colors)
        self.tools.set_transform_line_colors_available(
            bool(active_line_colors)
        )

        quality_active = self.tools.transform_quality.isChecked()
        self.canvas.set_transform_quality(quality_active)
        self.canvas.set_transform_line_threshold(
            self.tools.transform_line_width.value()
        )
        self.canvas.transform_apply_all_frames = (
            False if quality_active
            else self.tools.selection_all_frames.isChecked()
        )
        if not self.canvas.begin_selection_transform(mode):
            self.canvas.transform_apply_all_frames = False
            QMessageBox.warning(
                self, "変形",
                "選択範囲から変形対象を作成できませんでした。"
            )
            return
        self.canvas.setFocus()
        target_note = (
            "Tp_mask v0.7クオリティ方式／現在のコマへ適用します。"
            if quality_active else
            (
                "すべてのコマへ適用します。"
                if self.canvas.transform_apply_all_frames
                else "現在のコマへ適用します。"
            )
        )
        line_note = (
            f" 選択中の使用色{len(self.canvas.transform_tp_line_colors)}色を実線として処理します。"
            if quality_active and self.canvas.transform_tp_line_colors else ""
        )
        self.statusBar().showMessage(
            "白い点：変形／枠内：移動／黄色い点：回転。"
            f"Enterで確定、Escでキャンセルできます。{target_note}{line_note}",
            5000,
        )

    @staticmethod
    def _qimage_rgba_array(image):
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, rgba.bytesPerLine())
        )
        return rows[:, :width * 4].reshape((height, width, 4)).copy()

    def _replacement_mapping_for_aligned_images(self, source_image, target_image):
        """Return source->target RGB mapping when every aligned pixel is consistent."""
        if (
            source_image.isNull()
            or target_image.isNull()
            or source_image.size() != target_image.size()
        ):
            return None
        source = self._qimage_rgba_array(source_image)
        target = self._qimage_rgba_array(target_image)

        # A palette-swapped copy must occupy exactly the same pixels and retain
        # the same alpha values. Color values may differ.
        if not np.array_equal(source[:, :, 3], target[:, :, 3]):
            return None
        opaque = source[:, :, 3] > 0
        if not np.any(opaque):
            return None

        source_rgb = source[:, :, :3][opaque]
        target_rgb = target[:, :, :3][opaque]
        source_packed = (
            source_rgb[:, 0].astype(np.uint32) << 16
            | source_rgb[:, 1].astype(np.uint32) << 8
            | source_rgb[:, 2].astype(np.uint32)
        )
        target_packed = (
            target_rgb[:, 0].astype(np.uint32) << 16
            | target_rgb[:, 1].astype(np.uint32) << 8
            | target_rgb[:, 2].astype(np.uint32)
        )

        mapping = {}
        for packed in np.unique(source_packed):
            mapped_values = np.unique(target_packed[source_packed == packed])
            if len(mapped_values) != 1:
                return None
            mapped = int(mapped_values[0])
            source_value = int(packed)
            mapping[(
                (source_value >> 16) & 255,
                (source_value >> 8) & 255,
                source_value & 255,
            )] = (
                (mapped >> 16) & 255,
                (mapped >> 8) & 255,
                mapped & 255,
            )
        return mapping

    def register_same_image_replacements(self):
        if not self.canvas.frames or not self.canvas.layers:
            return
        frame_index = self.canvas.current_frame
        source_index = self.canvas.active_layer_index
        source_key = self.canvas.resolve_key_frame(frame_index, source_index)
        if source_key is None:
            QMessageBox.warning(
                self,
                "同一画像を置換色に登録",
                "選択レイヤーの現在位置に画像がありません。",
            )
            return
        source_layer = self.canvas.frames[source_key].layers[source_index]
        if not source_layer.has_content:
            QMessageBox.warning(
                self,
                "同一画像を置換色に登録",
                "選択レイヤーの現在位置に画像がありません。",
            )
            return

        candidates = []
        for layer_index in range(source_index + 1, len(self.canvas.layers)):
            key_frame = self.canvas.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                continue
            layer = self.canvas.frames[key_frame].layers[layer_index]
            if layer.has_content:
                candidates.append((layer_index, key_frame, layer))

        if not candidates:
            QMessageBox.warning(
                self,
                "同一画像を置換色に登録",
                "同じタイムライン位置の上側レイヤーに、比較できる画像がありません。",
            )
            return

        for _layer_index, _key_frame, target_layer in candidates:
            mapping = self._replacement_mapping_for_aligned_images(
                source_layer.image,
                target_layer.image,
            )
            if mapping is None:
                continue
            registered = self.palette.register_replacements(mapping)
            if registered <= 0:
                QMessageBox.warning(
                    self,
                    "同一画像を置換色に登録",
                    "一致する画像は見つかりましたが、登録できる使用色がありません。",
                )
                return
            self.statusBar().showMessage(
                f"上側レイヤー「{target_layer.name}」から"
                f"{registered}色を置換色に登録しました。",
                4000,
            )
            return

        QMessageBox.warning(
            self,
            "同一画像を置換色に登録",
            "上側レイヤーの画像とピクセルが一致していないため、"
            "置換色には登録できません。\n"
            "画像サイズ、透明度、色領域の形が同じか確認してください。",
        )

    def main_line_repaint(self):
        if not self.canvas.frames:
            return
        answer = QMessageBox.question(
            self,
            "MainLineRepaint",
            "選択した使用色と、それ以外の部分を別レイヤーに分離します。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return

        selected = list(self.palette.selected_rgb_set())
        if not selected:
            QMessageBox.information(
                self, "MainLineRepaint",
                "使用色欄で分離したい色を1色以上選択してください。"
            )
            return
        selected_set = {tuple(map(int, rgb)) for rgb in selected}

        source_index = self.canvas.active_layer_index
        frame_total = max(1, len(self.canvas.frames))
        progress = self.create_progress_counter(
            "MainLineRepaint",
            frame_total * 2,
            "対象色を確認しています",
        )

        found_target = False
        for scan_index, frame in enumerate(self.canvas.frames, 1):
            self.update_progress_counter(
                progress,
                scan_index - 1,
                frame_total * 2,
                f"コマ {scan_index} の対象色を確認しています",
            )
            source = frame.layers[source_index]
            if not source.has_content:
                continue
            image = source.image.convertToFormat(QImage.Format.Format_RGBA8888)
            width, height = image.width(), image.height()
            ptr = image.bits()
            try:
                ptr.setsize(image.sizeInBytes())
            except AttributeError:
                pass
            rows = np.frombuffer(ptr, dtype=np.uint8).reshape((height, image.bytesPerLine()))
            pixels = rows[:, :width*4].reshape((height, width, 4))
            visible = pixels[:, :, 3] > 0
            rgb24 = (
                pixels[:, :, 0].astype(np.uint32) << 16
                | pixels[:, :, 1].astype(np.uint32) << 8
                | pixels[:, :, 2].astype(np.uint32)
            )
            selected24 = np.array(
                [(r << 16) | (g << 8) | b for r, g, b in selected_set],
                dtype=np.uint32,
            )
            match = visible & np.isin(rgb24, selected24)
            if np.any(match):
                found_target = True
                break

        if not found_target:
            self.close_progress_counter(progress)
            QMessageBox.warning(
                self,
                "MainLineRepaint",
                "選択した使用色が選択レイヤー内に見つかりませんでした。\n処理は適用しません。",
            )
            return

        self.canvas.push_doc_undo()

        # Add two layers directly above the source layer:
        # source (hidden) -> Paint -> LINE
        paint_index = source_index + 1
        line_index = source_index + 2

        for frame in self.canvas.frames:
            source_layer = frame.layers[source_index]
            source_layer.visible = False

            paint_layer = Layer(
                "Paint",
                blank_image(),
                visible=True,
                opacity=1.0,
                has_content=False,
            )
            line_layer = Layer(
                "LINE",
                blank_image(),
                visible=True,
                opacity=1.0,
                has_content=False,
            )
            frame.layers.insert(paint_index, paint_layer)
            frame.layers.insert(line_index, line_layer)

        for frame_index, frame in enumerate(self.canvas.frames):
            self.update_progress_counter(
                progress,
                frame_total + frame_index,
                frame_total * 2,
                f"コマ {frame_index + 1} をレイヤー分離しています",
            )
            source = frame.layers[source_index]
            if not source.has_content:
                continue

            rgba = source.image.convertToFormat(QImage.Format.Format_RGBA8888)
            width, height = rgba.width(), rgba.height()
            ptr = rgba.bits()
            try:
                ptr.setsize(rgba.sizeInBytes())
            except AttributeError:
                pass

            rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
                (height, rgba.bytesPerLine())
            )
            pixels = rows[:, :width * 4].reshape((height, width, 4))

            visible = pixels[:, :, 3] > 0
            rgb24 = (
                pixels[:, :, 0].astype(np.uint32) << 16
                | pixels[:, :, 1].astype(np.uint32) << 8
                | pixels[:, :, 2].astype(np.uint32)
            )
            selected24 = np.array(
                [(r << 16) | (g << 8) | b for r, g, b in selected_set],
                dtype=np.uint32,
            )
            line_mask = visible & np.isin(rgb24, selected24)
            line_pixels = np.zeros_like(pixels)
            line_pixels[line_mask] = pixels[line_mask]

            # Paint: remove the line pixels, then fill those gaps using
            # the most frequent adjacent existing color. No intermediate colors.
            paint_pixels = pixels.copy()
            paint_pixels[line_mask, 3] = 0

            pending = [tuple(v) for v in np.argwhere(line_mask)]
            for _pass in range(12):
                if not pending:
                    break
                remaining = []
                for y, x in pending:
                    neighbors = []
                    for nx, ny in (
                        (x - 1, y),
                        (x + 1, y),
                        (x, y - 1),
                        (x, y + 1),
                    ):
                        if (
                            0 <= nx < width
                            and 0 <= ny < height
                            and paint_pixels[ny, nx, 3] > 0
                        ):
                            neighbors.append(
                                (
                                    int(paint_pixels[ny, nx, 0]),
                                    int(paint_pixels[ny, nx, 1]),
                                    int(paint_pixels[ny, nx, 2]),
                                )
                            )
                    if not neighbors:
                        remaining.append((y, x))
                        continue

                    counts = {}
                    for color in neighbors:
                        counts[color] = counts.get(color, 0) + 1
                    fill_color = max(counts, key=counts.get)
                    paint_pixels[y, x, 0] = fill_color[0]
                    paint_pixels[y, x, 1] = fill_color[1]
                    paint_pixels[y, x, 2] = fill_color[2]
                    paint_pixels[y, x, 3] = 255
                pending = remaining

            line_image = QImage(
                line_pixels.data,
                width,
                height,
                line_pixels.strides[0],
                QImage.Format.Format_RGBA8888,
            ).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

            paint_image = QImage(
                paint_pixels.data,
                width,
                height,
                paint_pixels.strides[0],
                QImage.Format.Format_RGBA8888,
            ).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

            frame.layers[line_index].image = line_image
            frame.layers[line_index].has_content = bool(np.any(line_mask))
            frame.layers[line_index].exposure = source.exposure

            frame.layers[paint_index].image = paint_image
            frame.layers[paint_index].has_content = bool(np.any(paint_pixels[:, :, 3] > 0))
            frame.layers[paint_index].exposure = source.exposure

        self.close_progress_counter(progress)
        self.canvas.active_layer_index = line_index
        self._used_color_cache.clear()
        self.canvas._color_filter_cache.clear()
        self.canvas._silhouette_cache.clear()
        self._suppress_used_color_refresh_once = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

        self.statusBar().showMessage(
            "LINE／Paintを作成し、元レイヤーを非表示にしました。",
            3800,
        )

    def create_progress_counter(self, title, total, label=None):
        total = max(1, int(total))
        dialog = QProgressDialog(
            label or title,
            "",
            0,
            total,
            self,
        )
        dialog.setWindowTitle(title)
        dialog.setCancelButton(None)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumWidth(330)
        dialog.setValue(0)
        dialog.show()
        QApplication.processEvents()
        return dialog

    def update_progress_counter(self, dialog, value, total, label):
        if dialog is None:
            return
        value = max(0, min(int(total), int(value)))
        dialog.setLabelText(f"{label}\n{value} / {int(total)}")
        dialog.setValue(value)
        QApplication.processEvents()

    def close_progress_counter(self, dialog):
        if dialog is None:
            return
        dialog.setValue(dialog.maximum())
        dialog.close()
        dialog.deleteLater()
        QApplication.processEvents()

    def remove_dust_fill_surrounding(self):
        """選択レイヤー内でゴミ取り／塗り抜けを実行する。"""
        mode = self.tools.dust_mode.currentText()
        max_area = max(
            1,
            min(100, int(self.tools.dust_size.value())),
        )
        selected_only = (
            self.tools.dust_selected_only.isChecked()
        )
        selected_colors = self.canvas.selected_used_colors()
        selected_colors.discard((255,255,255))

        if selected_only and not selected_colors:
            self.statusBar().showMessage(
                "使用色パネルで対象色を選択してください。",
                2800,
            )
            return

        layer_index = int(
            self.canvas.active_layer_index
        )
        requested_frames = (
            range(len(self.canvas.frames))
            if self.tools.dust_all_frames.isChecked()
            else (self.canvas.current_frame,)
        )

        # 保持区間は同じキーフレーム画像を指すため1回だけ処理する。
        frame_indices = []
        seen_keys = set()
        for frame_index in requested_frames:
            key_frame = self.canvas.resolve_key_frame(
                int(frame_index),
                layer_index,
            )
            if key_frame is None:
                continue
            if key_frame in seen_keys:
                continue
            seen_keys.add(key_frame)
            frame_indices.append(key_frame)

        if not frame_indices:
            self.statusBar().showMessage(
                "選択レイヤーに処理できるキーフレームがありません。",
                2800,
            )
            return

        def component_list(mask):
            """4方向接続成分を返す。各画素は1度だけ走査する。"""
            pending = np.asarray(
                mask,
                dtype=bool,
            ).copy()
            candidate_y, candidate_x = np.nonzero(
                pending
            )
            height, width = pending.shape

            for y0, x0 in zip(
                candidate_y,
                candidate_x,
            ):
                y0 = int(y0)
                x0 = int(x0)
                if not pending[y0, x0]:
                    continue

                stack = [(x0, y0)]
                pending[y0, x0] = False
                component = []
                touches_edge = False

                while stack:
                    x, y = stack.pop()
                    component.append((y, x))
                    if (
                        x == 0 or y == 0
                        or x == width - 1
                        or y == height - 1
                    ):
                        touches_edge = True

                    if x > 0 and pending[y, x - 1]:
                        pending[y, x - 1] = False
                        stack.append((x - 1, y))
                    if x + 1 < width and pending[y, x + 1]:
                        pending[y, x + 1] = False
                        stack.append((x + 1, y))
                    if y > 0 and pending[y - 1, x]:
                        pending[y - 1, x] = False
                        stack.append((x, y - 1))
                    if y + 1 < height and pending[y + 1, x]:
                        pending[y + 1, x] = False
                        stack.append((x, y + 1))

                yield component, touches_edge

        changed_cells = 0
        changed_pixels = 0
        changed_frame_indices = []
        undo_cells = []

        progress = self.create_progress_counter(
            mode,
            max(1, len(frame_indices)),
            f"{mode}対象を解析しています",
        )
        QApplication.setOverrideCursor(
            Qt.CursorShape.WaitCursor
        )
        try:
            for counter, frame_index in enumerate(
                frame_indices,
                1,
            ):
                self.update_progress_counter(
                    progress,
                    counter - 1,
                    max(1, len(frame_indices)),
                    f"コマ {frame_index + 1} を解析しています",
                )
                frame = self.canvas.frames[frame_index]
                if layer_index >= len(frame.layers):
                    continue
                layer = frame.layers[layer_index]
                if not layer.has_content:
                    continue

                rgba = layer.image.convertToFormat(
                    QImage.Format.Format_RGBA8888
                )
                width = rgba.width()
                height = rgba.height()
                if width <= 0 or height <= 0:
                    continue

                ptr = rgba.bits()
                try:
                    ptr.setsize(rgba.sizeInBytes())
                except AttributeError:
                    pass
                rows = np.frombuffer(
                    ptr,
                    dtype=np.uint8,
                ).reshape(
                    (height, rgba.bytesPerLine())
                )
                pixels = rows[:,:width * 4].reshape(
                    (height, width, 4)
                )
                rgb = pixels[:,:,:3]
                pseudo_white = (
                    (pixels[:,:,3] == 0)
                    | np.all(rgb == 255, axis=2)
                )
                cell_changed = 0

                if mode == "塗り抜け":
                    # 外周につながらない小さな白領域を周囲色で埋める。
                    starts = []
                    starts.extend(
                        (int(x), 0)
                        for x in np.flatnonzero(
                            pseudo_white[0,:]
                        )
                    )
                    if height > 1:
                        starts.extend(
                            (int(x), height - 1)
                            for x in np.flatnonzero(
                                pseudo_white[-1,:]
                            )
                        )
                    if width > 1:
                        starts.extend(
                            (0, int(y))
                            for y in np.flatnonzero(
                                pseudo_white[:,0]
                            )
                        )
                        starts.extend(
                            (width - 1, int(y))
                            for y in np.flatnonzero(
                                pseudo_white[:,-1]
                            )
                        )

                    outside = (
                        self.canvas._scanline_connected_region(
                            pseudo_white,
                            starts,
                        )
                        if starts
                        else np.zeros_like(pseudo_white)
                    )
                    holes = pseudo_white & ~outside

                    for component, _touches_edge in component_list(
                        holes
                    ):
                        area = len(component)
                        if area == 0 or area > max_area:
                            continue

                        border_positions = set()
                        for y, x in component:
                            for ny in range(
                                max(0, y - 1),
                                min(height, y + 2),
                            ):
                                for nx in range(
                                    max(0, x - 1),
                                    min(width, x + 2),
                                ):
                                    if (
                                        (ny != y or nx != x)
                                        and not pseudo_white[ny, nx]
                                    ):
                                        border_positions.add(
                                            (ny, nx)
                                        )

                        if not border_positions:
                            continue

                        border_yx = np.asarray(
                            tuple(border_positions),
                            dtype=np.int32,
                        )
                        border_rgb = pixels[
                            border_yx[:,0],
                            border_yx[:,1],
                            :3,
                        ]

                        if selected_only:
                            keep = np.zeros(
                                len(border_rgb),
                                dtype=bool,
                            )
                            for selected_color in selected_colors:
                                keep |= np.all(
                                    border_rgb
                                    == np.asarray(
                                        selected_color,
                                        dtype=np.uint8,
                                    ),
                                    axis=1,
                                )
                            border_rgb = border_rgb[keep]
                            if border_rgb.size == 0:
                                continue

                        packed = (
                            (
                                border_rgb[:,0].astype(
                                    np.uint32
                                ) << 16
                            )
                            | (
                                border_rgb[:,1].astype(
                                    np.uint32
                                ) << 8
                            )
                            | border_rgb[:,2].astype(
                                np.uint32
                            )
                        )
                        values, counts = np.unique(
                            packed,
                            return_counts=True,
                        )
                        selected = int(
                            values[int(np.argmax(counts))]
                        )
                        fill = np.asarray(
                            [
                                (selected >> 16) & 255,
                                (selected >> 8) & 255,
                                selected & 255,
                            ],
                            dtype=np.uint8,
                        )
                        coordinates = np.asarray(
                            component,
                            dtype=np.int32,
                        )
                        cy = coordinates[:,0]
                        cx = coordinates[:,1]
                        pixels[cy,cx,:3] = fill
                        pixels[cy,cx,3] = 255
                        cell_changed += area

                else:
                    # ゴミ取り：小さな色点を白へ変更する。
                    removal_mask = np.zeros(
                        (height, width),
                        dtype=bool,
                    )

                    if selected_only:
                        # 選択色ごとに独立判定する。
                        # 青1pxが黒に接していても青成分は1pxとして消える。
                        for selected_color in selected_colors:
                            color_mask = (
                                ~pseudo_white
                                & np.all(
                                    rgb
                                    == np.asarray(
                                        selected_color,
                                        dtype=np.uint8,
                                    ),
                                    axis=2,
                                )
                            )
                            for component, _edge in component_list(
                                color_mask
                            ):
                                if (
                                    0 < len(component)
                                    <= max_area
                                ):
                                    coordinates = np.asarray(
                                        component,
                                        dtype=np.int32,
                                    )
                                    removal_mask[
                                        coordinates[:,0],
                                        coordinates[:,1],
                                    ] = True
                    else:
                        # 未選択時は、白背景から独立した小さな色塊を削除。
                        foreground = ~pseudo_white
                        for component, touches_edge in component_list(
                            foreground
                        ):
                            if (
                                not touches_edge
                                and 0 < len(component)
                                <= max_area
                            ):
                                coordinates = np.asarray(
                                    component,
                                    dtype=np.int32,
                                )
                                removal_mask[
                                    coordinates[:,0],
                                    coordinates[:,1],
                                ] = True

                    cell_changed = int(
                        np.count_nonzero(removal_mask)
                    )
                    if cell_changed:
                        pixels[removal_mask,:3] = 255
                        pixels[removal_mask,3] = 255

                if cell_changed:
                    undo_cells.append(
                        (
                            frame_index,
                            layer.image.copy(),
                            bool(layer.has_content),
                        )
                    )
                    layer.image = rgba.convertToFormat(
                        QImage.Format.Format_ARGB32_Premultiplied
                    )
                    # ○化や未使用化はせず、キーフレーム構造を維持する。
                    layer.has_content = True
                    changed_cells += 1
                    changed_pixels += cell_changed
                    changed_frame_indices.append(
                        int(frame_index)
                    )

                self.update_progress_counter(
                    progress,
                    counter,
                    max(1, len(frame_indices)),
                    f"コマ {frame_index + 1} の処理が完了しました",
                )
        finally:
            QApplication.restoreOverrideCursor()
            self.close_progress_counter(progress)

        if not changed_pixels:
            target_text = (
                "選択色の" if selected_only else ""
            )
            self.statusBar().showMessage(
                f"指定サイズ以内の{target_text}{mode}対象は"
                "見つかりませんでした。",
                3000,
            )
            return

        self.canvas.undo_stack.append(
            ("layer_batch", layer_index, undo_cells)
        )
        self.canvas.undo_stack = (
            self.canvas.undo_stack[-MAX_UNDO:]
        )
        self.canvas.redo_stack.clear()
        # 表示専用キャッシュも含めてすべて破棄する。
        # ゴミ取り／塗り抜けは画像オブジェクトを差し替えるため、
        # ここを更新しないと表示／非表示切替まで旧画像が残る場合がある。
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._pseudo_transparency_cache.clear()
        self.canvas._silhouette_cache.clear()
        self.canvas._onion_cache.clear()
        self.canvas._playback_frame_cache.clear()

        # 変更したセルを通知し、現在表示中の保持コマも即時再描画する。
        for changed_frame_index in changed_frame_indices:
            self.canvas.cellChanged.emit(
                int(changed_frame_index),
                int(layer_index),
            )

        self.canvas.changed.emit()
        self.canvas.update()
        self.canvas.repaint()
        QApplication.processEvents()

        self.refresh_used_colors_with_counter(
            f"{mode}後の使用色を更新しています"
        )

        # 使用色の再走査後にも再描画を予約し、進捗ダイアログの
        # 閉鎖後に旧表示へ戻ることを防ぐ。
        self.canvas.update()
        self.statusBar().showMessage(
            f"選択レイヤーの{changed_cells}コマで"
            f"{changed_pixels:,}ピクセルへ{mode}を適用しました。",
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
        self.canvas.zoom=v/100
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

    def _prompt_github_token(self):
        """Ask for (and remember) the GitHub token used for update checks."""
        existing = config.get_value("github_token", "")
        token, ok = QInputDialog.getText(
            self,
            "更新用トークン",
            "更新確認には、このリポジトリを読み取れるGitHubトークンが必要です。\n"
            "（Settings > Developer settings > Personal access tokens で発行）",
            text=existing,
        )
        if not ok:
            return None
        token = token.strip()
        config.set_value("github_token", token or None)
        return token or None

    def check_for_updates_interactive(self):
        token = config.get_value("github_token")
        if not token:
            token = self._prompt_github_token()
            if not token:
                return

        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            result = updater.check_for_update(token)
        finally:
            QApplication.restoreOverrideCursor()

        status = result.get("status")
        if status == "error":
            # A bad/expired token would otherwise be stuck forever, since a
            # stored token is reused without re-prompting — offer to re-enter it.
            if result.get("auth_error"):
                answer = QMessageBox.question(
                    self,
                    "更新確認エラー",
                    result.get("message", "") + "\n\nトークンを入力し直しますか？",
                )
                if answer == QMessageBox.StandardButton.Yes:
                    new_token = self._prompt_github_token()
                    if new_token:
                        self.check_for_updates_interactive()
                return
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
                f"新しいバージョン {latest} がありますが、インストーラが\n"
                "見つかりませんでした。リリースページを確認してください。",
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
        self._download_and_run_installer(asset, token, latest)

    def _download_and_run_installer(self, asset, token, latest):
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
            updater.download_asset(asset, token, dest, progress=on_progress)
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
            os.startfile(str(dest))  # noqa: SLF001 - Windows installer launch
        except OSError as error:
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
        else:self.canvas.frames=[make_frame()];self.canvas.undo_stack.clear();self.canvas.redo_stack.clear();self.set_timeline_mode("sheet")
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
    @staticmethod
    def _normalize_shortcut_token(token):
        aliases = {
            "Control": "Ctrl",
            "CTRL": "Ctrl",
            "SHIFT": "Shift",
            "ALT": "Alt",
            "META": "Meta",
            " ": "Space",
        }
        token = str(token).strip()
        return aliases.get(token, token)

    def _shortcut_tokens(self, action, fallback):
        stored = action.property("holdShortcutText")
        if stored is not None:
            text = str(stored)
        else:
            text = action.shortcut().toString(
                QKeySequence.SequenceFormat.PortableText
            )
            if not text:
                text = fallback
        # Hold operations use the first chord only.
        text = text.split(",", 1)[0]
        return {
            self._normalize_shortcut_token(token)
            for token in text.split("+")
            if token.strip()
        }

    def _event_key_token(self, event):
        key_map = {
            Qt.Key.Key_Control: "Ctrl",
            Qt.Key.Key_Shift: "Shift",
            Qt.Key.Key_Alt: "Alt",
            Qt.Key.Key_Meta: "Meta",
            Qt.Key.Key_Space: "Space",
        }
        if event.key() in key_map:
            return key_map[event.key()]
        text = QKeySequence(int(event.key())).toString(
            QKeySequence.SequenceFormat.PortableText
        )
        return self._normalize_shortcut_token(text) if text else None

    def _sync_modifier_tokens(self, modifiers):
        mapping = (
            (Qt.KeyboardModifier.ControlModifier, "Ctrl"),
            (Qt.KeyboardModifier.ShiftModifier, "Shift"),
            (Qt.KeyboardModifier.AltModifier, "Alt"),
            (Qt.KeyboardModifier.MetaModifier, "Meta"),
        )
        for flag, token in mapping:
            if modifiers & flag:
                self._held_canvas_shortcut_tokens.add(token)
            else:
                self._held_canvas_shortcut_tokens.discard(token)

    def _widget_in_timeline(self, widget):
        return bool(
            isinstance(widget, QWidget)
            and (
                widget is self.timeline
                or self.timeline.isAncestorOf(widget)
            )
        )

    def _hand_scroll_area_for_widget(self, widget):
        if not isinstance(widget, QWidget):
            return None
        if self._widget_in_timeline(widget):
            return self.timeline.table
        candidates = (
            self.palette.scroll,
            self.tools_scroll,
            self.color_wheel_scroll,
            self.color_slider_scroll,
            self.palette_scroll,
        )
        for area in candidates:
            if widget is area or area.isAncestorOf(widget):
                return area
        return None

    @staticmethod
    def _mouse_global_position(event):
        if hasattr(event, "globalPosition"):
            return QPointF(event.globalPosition())
        return QPointF(QCursor.pos())

    def _auxiliary_cursor_targets(self):
        return (
            self.timeline,
            self.timeline.table.viewport(),
            self.tools_dock,
            self.tools_scroll.viewport(),
            self.color_wheel_dock,
            self.color_wheel_scroll.viewport(),
            self.color_slider_dock,
            self.color_slider_scroll.viewport(),
            self.palette_dock,
            self.palette_scroll.viewport(),
            self.palette.scroll.viewport(),
        )

    def _update_auxiliary_hold_cursors(self):
        tool = self.canvas.temp_tool
        for widget in self._auxiliary_cursor_targets():
            try:
                widget.unsetCursor()
            except RuntimeError:
                pass
        if tool == "hand":
            for widget in self._auxiliary_cursor_targets():
                try:
                    widget.setCursor(Qt.CursorShape.OpenHandCursor)
                except RuntimeError:
                    pass
        elif tool == "zoom":
            try:
                self.timeline.setCursor(Qt.CursorShape.SizeVerCursor)
                self.timeline.table.viewport().setCursor(
                    Qt.CursorShape.SizeVerCursor
                )
            except RuntimeError:
                pass

    def _finish_auxiliary_hold_drag(self):
        grab_widget = self._ui_hold_grab_widget
        self._ui_hold_drag_mode = None
        self._ui_hold_scroll_area = None
        self._ui_hold_grab_widget = None
        if grab_widget is not None:
            try:
                grab_widget.releaseMouse()
            except RuntimeError:
                pass
        self._update_auxiliary_hold_cursors()

    def _handle_auxiliary_hold_event(self, watched, event):
        event_type = event.type()
        tool = self.canvas.temp_tool

        if self._ui_hold_drag_mode is not None:
            if event_type == QEvent.Type.MouseMove:
                current = self._mouse_global_position(event)
                if self._ui_hold_drag_mode == "hand":
                    area = self._ui_hold_scroll_area
                    if area is not None:
                        delta = current - self._ui_hold_start_global
                        horizontal, vertical = self._ui_hold_start_scroll
                        area.horizontalScrollBar().setValue(
                            int(round(horizontal - delta.x()))
                        )
                        area.verticalScrollBar().setValue(
                            int(round(vertical - delta.y()))
                        )
                        area.viewport().setCursor(
                            Qt.CursorShape.ClosedHandCursor
                        )
                elif self._ui_hold_drag_mode == "timeline_zoom":
                    delta_y = current.y() - self._ui_hold_last_global.y()
                    factor = math.pow(1.01, -delta_y)
                    viewport = self.timeline.table.viewport()
                    anchor = viewport.mapFromGlobal(
                        current.toPoint()
                    )
                    self.timeline.adjust_timeline_zoom(
                        factor,
                        anchor.x(),
                        anchor.y(),
                    )
                    self._ui_hold_last_global = current
                event.accept()
                return True
            if event_type == QEvent.Type.MouseButtonRelease:
                self._finish_auxiliary_hold_drag()
                event.accept()
                return True

        if event_type == QEvent.Type.Wheel and tool == "zoom":
            if self._widget_in_timeline(watched):
                delta = event.angleDelta().y()
                if delta:
                    viewport = self.timeline.table.viewport()
                    global_point = self._mouse_global_position(event)
                    anchor = viewport.mapFromGlobal(global_point.toPoint())
                    self.timeline.adjust_timeline_zoom(
                        1.15 if delta > 0 else 1.0 / 1.15,
                        anchor.x(),
                        anchor.y(),
                    )
                    event.accept()
                    return True

        if event_type == QEvent.Type.MouseButtonPress:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            area = self._hand_scroll_area_for_widget(watched)
            if tool == "hand" and area is not None:
                self._ui_hold_drag_mode = "hand"
                self._ui_hold_scroll_area = area
                self._ui_hold_start_global = self._mouse_global_position(event)
                self._ui_hold_last_global = QPointF(
                    self._ui_hold_start_global
                )
                self._ui_hold_start_scroll = (
                    area.horizontalScrollBar().value(),
                    area.verticalScrollBar().value(),
                )
                self._ui_hold_grab_widget = (
                    watched if isinstance(watched, QWidget) else area.viewport()
                )
                try:
                    self._ui_hold_grab_widget.grabMouse()
                except RuntimeError:
                    self._ui_hold_grab_widget = None
                area.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return True
            if tool == "zoom" and self._widget_in_timeline(watched):
                current = self._mouse_global_position(event)
                self._ui_hold_drag_mode = "timeline_zoom"
                self._ui_hold_scroll_area = self.timeline.table
                self._ui_hold_start_global = current
                self._ui_hold_last_global = QPointF(current)
                self._ui_hold_grab_widget = (
                    watched
                    if isinstance(watched, QWidget)
                    else self.timeline.table.viewport()
                )
                try:
                    self._ui_hold_grab_widget.grabMouse()
                except RuntimeError:
                    self._ui_hold_grab_widget = None
                event.accept()
                return True

        if event_type == QEvent.Type.MouseMove:
            if tool == "hand" and self._hand_scroll_area_for_widget(watched):
                event.accept()
                return True
            if tool == "zoom" and self._widget_in_timeline(watched):
                event.accept()
                return True
        return False

    def _update_canvas_hold_operation(self):
        held = set(self._held_canvas_shortcut_tokens)
        bindings = [
            ("zoom", self.a_hold_zoom, "Ctrl+Space"),
            ("rotate", self.a_hold_rotate, "Shift+Space"),
            ("hand", self.a_hold_hand, "Space"),
            ("eyedropper", self.a_hold_eyedropper, "Alt"),
        ]
        matches = []
        for priority, (tool, action, fallback) in enumerate(bindings):
            required = self._shortcut_tokens(action, fallback)
            if required and required.issubset(held):
                matches.append((len(required), -priority, tool))
        new_tool = max(matches)[2] if matches else None
        if self.canvas.temp_tool != new_tool:
            self.canvas.temp_tool = new_tool
            self.canvas.drawing = False
            self.canvas.middle_hand = False
            self.canvas.update_tool_cursor()
            self.canvas.update()
        if (
            self._ui_hold_drag_mode == "hand"
            and new_tool != "hand"
        ) or (
            self._ui_hold_drag_mode == "timeline_zoom"
            and new_tool != "zoom"
        ):
            self._finish_auxiliary_hold_drag()
        else:
            self._update_auxiliary_hold_cursors()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonRelease:
            QTimer.singleShot(0, self._sync_all_area_hamburgers)
        if type(watched).__name__ == "QSplitterHandle":
            splitter = watched.parentWidget()
            area = self.tool_selector_dock.dockAreaWidget()
            if (
                isinstance(splitter, QSplitter)
                and splitter.orientation() == Qt.Orientation.Horizontal
                and area is not None
                and splitter.indexOf(area) >= 0
            ):
                if event.type() == QEvent.Type.MouseButtonPress:
                    self._tool_selector_resize_drag_active = True
                elif event.type() == QEvent.Type.MouseButtonRelease:
                    self._tool_selector_resize_drag_active = False
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and self._pending_tool_selector_snap is not None
        ):
            self._tool_selector_snap_timer.stop()
            QTimer.singleShot(0, self._apply_tool_selector_snap)
        if event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseMove,
            QEvent.Type.Wheel,
        ) and self._handle_auxiliary_hold_event(watched, event):
            return True
        if (
            event.type() == QEvent.Type.KeyPress
            and self.canvas.transform_active
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            self.canvas.commit_selection_transform()
            return True
        if (
            event.type() == QEvent.Type.KeyPress
            and self.canvas.transform_active
            and event.key() == Qt.Key.Key_Escape
        ):
            self.canvas.cancel_selection_transform()
            return True

        if event.type() in (
            QEvent.Type.ApplicationDeactivate,
            QEvent.Type.WindowDeactivate,
        ):
            self._held_canvas_shortcut_tokens.clear()
            self._update_canvas_hold_operation()
            return super().eventFilter(watched, event)

        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            # ショートカット編集ダイアログ内では、入力したキーをキャンバス操作に使わない。
            modal = QApplication.activeModalWidget()
            if isinstance(modal, ShortcutDialog):
                return super().eventFilter(watched, event)
            if getattr(event, "isAutoRepeat", lambda: False)():
                return super().eventFilter(watched, event)
            token = self._event_key_token(event)
            if event.type() == QEvent.Type.KeyPress:
                if token:
                    self._held_canvas_shortcut_tokens.add(token)
                self._sync_modifier_tokens(event.modifiers())
            else:
                if token:
                    self._held_canvas_shortcut_tokens.discard(token)
                self._sync_modifier_tokens(event.modifiers())
                # Qtの環境によっては解放イベントのmodifiersに解放前のキーが残る。
                if token in ("Ctrl", "Shift", "Alt", "Meta"):
                    self._held_canvas_shortcut_tokens.discard(token)
            self._update_canvas_hold_operation()
            if token in ("Space", "Ctrl", "Shift", "Alt", "Meta"):
                return True
        return super().eventFilter(watched, event)

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
            pass
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
            pass
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
            ptr=rgba.bits()
            try:ptr.setsize(rgba.sizeInBytes())
            except AttributeError:pass
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
