from .common import *  # noqa: F401,F403
from . import config, constants, project_io, updater
from .canvas import PaintCanvas
from .color_panel import UsedColorPanel
from .color_reduction import ColorReductionDialog
from .models import Frame, Layer, make_frame
from .onion import OnionSkinSettingsBrowser
from .pressure import PressureDialog
from .timeline import TimelineWidget
from .toolpanel import ToolPanel
from .utils import blank_image, disable_windows_ink_feedback, workspace_size
from .widgets import (CanvasSizeDialog, ShortcutDialog, TimeRemapPasteDialog, TransformLineThicknessDialog, TweenCommandPopup)
from .logging_setup import get_logger

log = get_logger(__name__)

# Realistic failure set for the top-level user-action handlers below (file
# I/O, PIL/numpy/Qt image pipelines): everything expected while still letting
# non-Exception control-flow (KeyboardInterrupt/SystemExit) propagate.
_OPERATION_ERRORS = (
    OSError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    RuntimeError,
    AttributeError,
    MemoryError,
    zipfile.BadZipFile,  # archive read/write paths (projects, PSD) — not an OSError
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle(APP_DISPLAY_NAME);self.resize(1500,960);self.setAcceptDrops(True)
        self.canvas=PaintCanvas();self.tools=ToolPanel();self.timeline=TimelineWidget();self.palette=UsedColorPanel();self.timer=QTimer(self);self.timer.timeout.connect(self.advance)
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
        self.current_project_path = None
        self.build_actions();self.build_menu();self.build_ui();self.connect();self.refresh_ui()
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
    def make_shortcut_action(self, name, callback, shortcut=""):
        action = QAction(name, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        action.triggered.connect(callback)
        self.addAction(action)
        return action

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
    def build_ui(self):
        self.setDockNestingEnabled(True)
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
        self.setCentralWidget(center)

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

        self.drawing_color_scroll = QScrollArea()
        self.drawing_color_scroll.setWidgetResizable(True)
        self.drawing_color_scroll.setMinimumSize(0, 0)
        self.drawing_color_scroll.setFrameShape(
            QScrollArea.Shape.NoFrame
        )
        self.drawing_color_scroll.setWidget(
            self.tools.drawing_color_box
        )

        self.tools_dock=QDockWidget("ツール", self)
        self.tools_dock.setObjectName("toolsDock")
        self.tools_dock.setWidget(self.tools_scroll)
        self.tools_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tools_dock)

        self.drawing_color_dock=QDockWidget("描画色", self)
        self.drawing_color_dock.setObjectName("drawingColorDock")
        self.drawing_color_dock.setWidget(self.drawing_color_scroll)
        self.drawing_color_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self.drawing_color_dock,
        )

        self.palette_dock=QDockWidget("使用色", self)
        self.palette_dock.setObjectName("paletteDock")
        self.palette_dock.setWidget(self.palette_scroll)
        self.palette_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.palette_dock)
        self.splitDockWidget(
            self.drawing_color_dock,
            self.palette_dock,
            Qt.Orientation.Vertical,
        )

        self.timeline_dock=QDockWidget("タイムライン", self)
        self.timeline_dock.setObjectName("timelineDock")
        self.timeline.setMaximumHeight(16777215)
        self.timeline_dock.setWidget(self.timeline)
        self.timeline_dock.setMinimumHeight(70)
        self.timeline_dock.setMaximumHeight(16777215)
        self.timeline_dock.setAllowedAreas(
            Qt.DockWidgetArea.TopDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.timeline_dock)
        self.setCorner(
            Qt.Corner.BottomLeftCorner,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self.setCorner(
            Qt.Corner.BottomRightCorner,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )

        view_menu=self.menuBar().addMenu("表示")
        view_menu.addAction(self.tools_dock.toggleViewAction())
        view_menu.addAction(self.drawing_color_dock.toggleViewAction())
        view_menu.addAction(self.palette_dock.toggleViewAction())
        view_menu.addAction(self.timeline_dock.toggleViewAction())

        help_menu=self.menuBar().addMenu("ヘルプ")
        a_check_update=QAction("更新を確認…", self)
        a_check_update.triggered.connect(self.check_for_updates_interactive)
        help_menu.addAction(a_check_update)

        self.resizeDocks(
            [self.tools_dock, self.drawing_color_dock, self.palette_dock],
            [220, 240, 260],
            Qt.Orientation.Horizontal,
        )
        self.resizeDocks(
            [self.drawing_color_dock, self.palette_dock],
            [390, 450],
            Qt.Orientation.Vertical,
        )
        self.resizeDocks(
            [self.timeline_dock],
            [210],
            Qt.Orientation.Vertical,
        )
        self.zoom.valueChanged.connect(self.set_zoom)
        b100.clicked.connect(lambda:self.set_zoom(100))
        bfit.clicked.connect(self.fit_canvas)
        self.rot.valueChanged.connect(self.set_rot)
        b0.clicked.connect(lambda:self.rot.setValue(0))

    def connect(self):
        self.tools.silhouette_btn.clicked.connect(
            lambda _checked=False: self.a_silhouette.trigger()
        )
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
        self.tools.removeDustRequested.connect(self.remove_dust_fill_surrounding)
        self.tools.sameImageReplacementRequested.connect(
            self.register_same_image_replacements
        )
        self.tools.mainLineRepaintRequested.connect(self.main_line_repaint)
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

    def set_timeline_mode(self, mode):
        mode = "sheet" if str(mode) == "sheet" else "sequence"
        layer_index = int(self.canvas.active_layer_index)
        selected_number = None
        if self.canvas.frames and 0 <= layer_index < len(self.canvas.layers):
            selected_number = self.canvas.frames[
                self.canvas.current_frame
            ].layers[layer_index].sequence_number
        if self.canvas.timeline_mode == "sequence" and mode == "sheet":
            # 連番専用セルのシート配置ではフレーム数・位置が変わるため、
            # 移動前の状態を文書単位で保存してUndo参照切れを防ぐ。
            if any(
                layer.sequence_only
                for frame in self.canvas.frames
                for layer in frame.layers
            ):
                self.canvas.push_doc_undo()
            self.canvas.apply_sequence_only_entries()
        if selected_number is not None:
            matching = [
                column
                for column, frame in enumerate(self.canvas.frames)
                if (
                    frame.layers[layer_index].sequence_number == selected_number
                    and (frame.layers[layer_index].has_content or frame.layers[layer_index].is_blank_key)
                    and (mode == "sequence" or not frame.layers[layer_index].sequence_only)
                )
            ]
            if matching:
                self.canvas.current_frame = matching[0]
        self.canvas.timeline_mode = mode
        self.timeline.set_timeline_mode(mode)
        self.timeline.sequence_archive = self.canvas._sequence_archive
        self.timeline.add_exposure.setVisible(mode == "sheet")
        # 切り替え前の選択セルを保持すると、キャンバスだけ先に切り替わり
        # 赤枠が旧タブの列へ残る。再構築前に選択を明示的に解除する。
        self.timeline.table.clearSelection()
        self.timeline.refresh(
            self.canvas.frames,
            self.canvas.current_frame,
            self.canvas.active_layer_index,
            getattr(self.canvas, "tween_pending", None),
        )
        self.timeline.select_current(
            self.canvas.current_frame,
            self.canvas.active_layer_index,
        )
        self.timeline.table.viewport().update()

    def _navigate_sequence_number(self, step, wrap=False):
        """シート配置ではなく絵番号順に連番セルを移動する。"""
        layer_index = int(self.canvas.active_layer_index)
        columns = self.canvas.sequence_entry_columns(layer_index)
        entries = sorted(
            (
                int(self.canvas.frames[column].layers[layer_index].sequence_number),
                int(column),
            )
            for column in columns
        )
        if not entries:
            return
        current_layer = self.canvas.frames[
            self.canvas.current_frame
        ].layers[layer_index]
        current_number = current_layer.sequence_number
        current_index = next(
            (
                index for index, (number, _column) in enumerate(entries)
                if number == current_number
            ),
            -1 if int(step) > 0 else len(entries),
        )
        target_index = current_index + (1 if int(step) > 0 else -1)
        if wrap:
            target_index %= len(entries)
        else:
            target_index = max(0, min(len(entries) - 1, target_index))
        self.canvas.current_frame = entries[target_index][1]
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def previous_timeline_frame(self):
        if self.canvas.timeline_mode == "sequence":
            self._navigate_sequence_number(-1, wrap=False)
        else:
            self.canvas.previous_frame()

    def next_timeline_frame(self):
        if self.canvas.timeline_mode == "sequence":
            self._navigate_sequence_number(1, wrap=False)
        else:
            self.canvas.next_frame()

    def previous_timeline_key(self):
        if self.canvas.timeline_mode == "sequence":
            self._navigate_sequence_number(-1, wrap=True)
        else:
            self.canvas.previous_key_frame()

    def next_timeline_key(self):
        if self.canvas.timeline_mode == "sequence":
            self._navigate_sequence_number(1, wrap=True)
        else:
            self.canvas.next_key_frame()

    def normalize_timeline_numbers(self, visual_rows):
        """選択レイヤーの絵番号をシート順へ振り直す。"""
        if self.canvas.timeline_mode != "sheet":
            return
        layer_count = len(self.canvas.layers)
        layer_indices = sorted({
            layer_count - 1 - int(row)
            for row in visual_rows
            if 0 <= layer_count - 1 - int(row) < layer_count
        })
        if not layer_indices:
            return
        self.canvas.push_doc_undo()
        for layer_index in layer_indices:
            self.canvas.normalize_sequence_numbers(layer_index)
        self.canvas._cell_structure_dirty = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        self.statusBar().showMessage(
            "選択レイヤーの番号をシート順に正規化しました。", 2500
        )
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
    def create_blank_timeline_key(
        self,
        visual_row,
        column,
    ):
        layer_count = len(self.canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        if not (0 <= layer_index < layer_count):
            return
        if self.canvas.timeline_mode == "sequence":
            self.canvas.insert_sequence_blank(
                layer_index,
                int(column) + 1,
            )
            return
        self.canvas.create_blank_key(
            int(column),
            layer_index,
        )

    def delete_timeline_frame(self):
        if self.canvas.timeline_mode == "sequence":
            self.canvas.delete_sequence_entry(
                self.canvas.active_layer_index,
                self.timeline.table.currentColumn() + 1,
            )
            return
        layer_index = int(self.canvas.active_layer_index)
        block = self.canvas.timeline_block_at(
            self.canvas.current_frame, layer_index
        )
        if block is None:
            return
        _kind, start, _exposure = block
        layer = self.canvas.frames[int(start)].layers[layer_index]
        self.canvas.push_doc_undo()
        if layer.has_content and layer.sequence_number is not None:
            self.canvas._sequence_archive[
                (layer_index, int(layer.sequence_number))
            ] = layer.clone()
        self.canvas._clear_timeline_layer_cell(layer)
        self.canvas._cell_structure_dirty = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def _restore_timeline_selection(self, cells):
        table = self.timeline.table
        table.clearSelection()
        valid = []
        for row, column in cells:
            item = table.item(int(row), int(column))
            if item is not None:
                item.setSelected(True)
                valid.append((int(row), int(column)))
        if valid:
            table.setCurrentCell(
                valid[0][0],
                valid[0][1],
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )

    def move_timeline_selection(
        self,
        cells,
        anchor_row,
        anchor_column,
        destination_row,
        destination_column,
    ):
        """複数選択に含まれるコマ塊を相対配置のまま移動する。"""
        if self.canvas.timeline_mode == "sequence":
            if int(anchor_row) != int(destination_row):
                self.statusBar().showMessage(
                    "連番画像は同じレイヤー内で入れ替えてください。", 2500
                )
                return
            layer_count = len(self.canvas.layers)
            layer_index = layer_count - 1 - int(anchor_row)
            self.canvas.move_sequence_image(
                layer_index,
                int(anchor_column) + 1,
                int(destination_column) + 1,
            )
            return
        try:
            selected_cells = {
                (int(row), int(column))
                for row, column in cells
            }
        except (TypeError, ValueError) as exc:
            log.debug("could not normalize selected cells: %s", exc)
            return
        if not selected_cells:
            return

        row_delta = int(destination_row) - int(anchor_row)
        column_delta = (
            int(destination_column) - int(anchor_column)
        )
        if row_delta == 0 and column_delta == 0:
            return

        layer_count = len(self.canvas.layers)
        blocks = {}
        for visual_row, column in selected_cells:
            layer_index = layer_count - 1 - visual_row
            if not (0 <= layer_index < layer_count):
                continue
            kind, start, exposure = TimelineWidget.timeline_span_at(
                self.canvas.frames,
                layer_index,
                column,
            )
            if (
                kind in ("content", "blank")
                and start is not None
            ):
                key = (layer_index, int(start))
                if key not in blocks:
                    layer = self.canvas.frames[
                        int(start)
                    ].layers[layer_index]
                    blocks[key] = (
                        kind,
                        max(1, int(exposure)),
                        layer.clone(),
                    )

        if not blocks:
            return

        moves = []
        for (
            source_layer,
            source_start,
        ), (
            kind,
            exposure,
            copied,
        ) in blocks.items():
            source_visual_row = (
                layer_count - 1 - source_layer
            )
            target_visual_row = (
                source_visual_row + row_delta
            )
            target_layer = (
                layer_count - 1 - target_visual_row
            )
            target_start = (
                source_start + column_delta
            )
            if (
                target_start < 0
                or not (0 <= target_layer < layer_count)
            ):
                self.statusBar().showMessage(
                    "移動先がタイムライン範囲外です。",
                    2500,
                )
                return
            moves.append(
                (
                    source_layer,
                    source_start,
                    target_layer,
                    target_start,
                    kind,
                    exposure,
                    copied,
                )
            )

        self.canvas.push_doc_undo()

        # 元位置を未使用セルへ戻す。
        for (
            source_layer,
            source_start,
            _target_layer,
            _target_start,
            _kind,
            _exposure,
            _copied,
        ) in moves:
            source = self.canvas.frames[
                source_start
            ].layers[source_layer]
            self.canvas._clear_timeline_layer_cell(source)

        maximum_end = max(
            target_start + exposure
            for (
                _source_layer,
                _source_start,
                _target_layer,
                target_start,
                _kind,
                exposure,
                _copied,
            ) in moves
        )
        self.canvas._ensure_frame_count(maximum_end)

        # 移動先と重なる既存露出を切り、既存の明示コマを消す。
        for (
            _source_layer,
            _source_start,
            target_layer,
            target_start,
            _kind,
            exposure,
            _copied,
        ) in moves:
            target_end = target_start + exposure - 1
            covering = self.canvas.timeline_block_at(
                target_start,
                target_layer,
            )
            if covering is not None:
                _cover_kind, cover_start, _cover_exposure = covering
                if cover_start < target_start:
                    cover_layer = self.canvas.frames[
                        cover_start
                    ].layers[target_layer]
                    cover_layer.exposure = max(
                        1,
                        target_start - cover_start,
                    )

            for column in range(
                target_start,
                target_end + 1,
            ):
                target = self.canvas.frames[
                    column
                ].layers[target_layer]
                if (
                    target.has_content
                    or getattr(
                        target,
                        "is_blank_key",
                        False,
                    )
                ):
                    self.canvas._clear_timeline_layer_cell(
                        target
                    )

        # 相対位置を保って配置。
        for (
            _source_layer,
            _source_start,
            target_layer,
            target_start,
            kind,
            exposure,
            copied,
        ) in moves:
            target = self.canvas.frames[
                target_start
            ].layers[target_layer]
            target.image = (
                copied.image.copy()
                if kind == "content"
                else blank_image()
            )
            target.visible = bool(copied.visible)
            target.opacity = float(copied.opacity)
            target.alpha_locked = bool(
                copied.alpha_locked
            )
            target.color_filter_enabled = bool(
                copied.color_filter_enabled
            )
            target.color_filter_rgb = (
                tuple(copied.color_filter_rgb)
                if copied.color_filter_rgb is not None
                else None
            )
            target.has_content = kind == "content"
            target.is_blank_key = kind == "blank"
            target.sequence_number = (
                copied.sequence_number
                if kind == "content"
                else None
            )
            target.sequence_only = bool(copied.sequence_only)
            target.exposure = max(1, int(exposure))

        moved_cells = [
            (
                row + row_delta,
                column + column_delta,
            )
            for row, column in selected_cells
            if (
                row + row_delta >= 0
                and column + column_delta >= 0
            )
        ]

        self.canvas.current_frame = max(
            0,
            int(destination_column),
        )
        self.canvas.active_layer_index = max(
            0,
            min(
                layer_count - 1,
                layer_count - 1 - int(destination_row),
            ),
        )
        self.canvas._cell_structure_dirty = True
        self.canvas._onion_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        QTimer.singleShot(
            0,
            lambda cells=tuple(moved_cells):
                self._restore_timeline_selection(cells)
        )

    def resize_timeline_exposure(
        self, visual_row, key_column, boundary_column, edge="right"
    ):
        layer_count = len(self.canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        key_column = int(key_column)
        boundary_column = int(boundary_column)
        if not (
            0 <= layer_index < layer_count
            and 0 <= key_column < len(self.canvas.frames)
        ):
            return
        layer = self.canvas.frames[key_column].layers[layer_index]
        old_exposure = max(1, int(layer.exposure))
        old_end = key_column + old_exposure - 1
        self.canvas.push_doc_undo()

        if edge == "left":
            previous_keys = [
                col for col in range(0, key_column)
                if (
                    self.canvas.frames[col].layers[layer_index].has_content
                    or getattr(
                        self.canvas.frames[col].layers[layer_index],
                        "is_blank_key",
                        False,
                    )
                )
            ]
            minimum_start = 0
            if previous_keys:
                previous_key = previous_keys[-1]
                previous_layer = self.canvas.frames[previous_key].layers[layer_index]
                minimum_start = previous_key + max(1, previous_layer.exposure)
            new_start = max(minimum_start, min(boundary_column, old_end))
            if new_start == key_column:
                if self.canvas.undo_stack:
                    self.canvas.undo_stack.pop()
                return
            self.canvas._ensure_frame_count(old_end + 1)
            destination = self.canvas.frames[new_start].layers[layer_index]
            if (
                (
                    destination.has_content
                    or getattr(destination, "is_blank_key", False)
                )
                and new_start != key_column
            ):
                if self.canvas.undo_stack:
                    self.canvas.undo_stack.pop()
                self.statusBar().showMessage(
                    "左端の移動先に別のコマがあるため伸縮できません。", 2500
                )
                return
            source = self.canvas.frames[key_column].layers[layer_index]
            copied = source.clone()
            destination.image = copied.image.copy()
            destination.has_content = bool(copied.has_content)
            destination.is_blank_key = bool(
                getattr(copied, "is_blank_key", False)
            )
            destination.sequence_number = copied.sequence_number
            destination.sequence_only = bool(copied.sequence_only)
            destination.exposure = max(1, old_end - new_start + 1)
            destination.visible = copied.visible
            destination.opacity = copied.opacity
            destination.alpha_locked = copied.alpha_locked
            destination.color_filter_enabled = copied.color_filter_enabled
            destination.color_filter_rgb = (
                tuple(copied.color_filter_rgb)
                if copied.color_filter_rgb is not None else None
            )
            if new_start != key_column:
                self.canvas._clear_timeline_layer_cell(source)
            self.canvas.current_frame = new_start
        else:
            new_end = max(key_column, boundary_column)
            requested_exposure = max(1, new_end - key_column + 1)
            next_keys = [
                col for col in range(key_column + 1, len(self.canvas.frames))
                if (
                    self.canvas.frames[col].layers[layer_index].has_content
                    or getattr(
                        self.canvas.frames[col].layers[layer_index],
                        "is_blank_key",
                        False,
                    )
                )
            ]
            shift = 0
            if next_keys and key_column + requested_exposure > next_keys[0]:
                shift = key_column + requested_exposure - next_keys[0]
            if shift > 0:
                old_count = len(self.canvas.frames)
                self.canvas._ensure_frame_count(old_count + shift)
                for col in range(old_count - 1, key_column, -1):
                    source = self.canvas.frames[col].layers[layer_index]
                    if not (
                        source.has_content
                        or getattr(source, "is_blank_key", False)
                    ):
                        continue
                    destination = self.canvas.frames[col + shift].layers[layer_index]
                    destination.image = source.image.copy()
                    destination.has_content = bool(source.has_content)
                    destination.is_blank_key = bool(
                        getattr(source, "is_blank_key", False)
                    )
                    destination.sequence_number = source.sequence_number
                    destination.sequence_only = bool(source.sequence_only)
                    destination.exposure = source.exposure
                    destination.visible = source.visible
                    destination.opacity = source.opacity
                    destination.alpha_locked = source.alpha_locked
                    destination.color_filter_enabled = source.color_filter_enabled
                    destination.color_filter_rgb = (
                        tuple(source.color_filter_rgb)
                        if source.color_filter_rgb is not None else None
                    )
                    self.canvas._clear_timeline_layer_cell(source)
            else:
                self.canvas._ensure_frame_count(key_column + requested_exposure)
            self.canvas.frames[key_column].layers[layer_index].exposure = requested_exposure
            self.canvas.current_frame = key_column

        self.canvas.active_layer_index = layer_index
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def move_timeline_cell(self, source_row, source_column, destination_row, destination_column):
        layer_count = len(self.canvas.layers)
        source_layer = layer_count - 1 - source_row
        destination_layer = layer_count - 1 - destination_row
        if self.canvas.timeline_mode == "sequence":
            if source_layer != destination_layer:
                self.statusBar().showMessage(
                    "連番画像は同じレイヤー内で入れ替えてください。", 2500
                )
                return
            self.canvas.move_sequence_image(
                source_layer,
                int(source_column) + 1,
                int(destination_column) + 1,
            )
            return
        self._suppress_used_color_refresh_once = True
        moved = self.canvas.move_timeline_cell(source_column, source_layer, destination_column, destination_layer)
        if not moved:
            self._suppress_used_color_refresh_once = False
            self.statusBar().showMessage("コマを移動できませんでした。", 2500)

    def copy_timeline_cell(self, source_row, source_column, destination_row, destination_column):
        """Altドラッグで、同じ絵番号を参照するシートキーを複製する。"""
        if self.canvas.timeline_mode != "sheet":
            return
        layer_count = len(self.canvas.layers)
        source_layer = layer_count - 1 - int(source_row)
        destination_layer = layer_count - 1 - int(destination_row)
        if source_layer != destination_layer:
            self.statusBar().showMessage("複製は同じレイヤー内で行ってください。", 2500)
            return
        block = self.canvas.timeline_block_at(int(source_column), source_layer)
        if block is None or block[0] != "content":
            return
        source = self.canvas.frames[int(block[1])].layers[source_layer]
        self.canvas.push_doc_undo()
        self.canvas._ensure_frame_count(int(destination_column) + 1)
        target = self.canvas.frames[int(destination_column)].layers[destination_layer]
        copied = source.clone()
        target.image = source.image
        target.has_content = True
        target.is_blank_key = False
        target.sequence_number = copied.sequence_number
        target.sequence_only = False
        target.exposure = max(1, int(copied.exposure))
        target.visible = copied.visible
        target.opacity = copied.opacity
        target.alpha_locked = copied.alpha_locked
        target.color_filter_enabled = copied.color_filter_enabled
        target.color_filter_rgb = copied.color_filter_rgb
        self.canvas.current_frame = int(destination_column)
        self.canvas.active_layer_index = destination_layer
        self.canvas._cell_structure_dirty = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def recall_sequence_number(self, visual_row, column, number):
        """既存の絵番号をシートの指定位置へ再配置する。"""
        if self.canvas.timeline_mode != "sheet":
            return
        layer_count = len(self.canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        candidates = self.canvas.sequence_entry_columns(layer_index)
        source = next((
            self.canvas.frames[index].layers[layer_index]
            for index in candidates
            if self.canvas.frames[index].layers[layer_index].sequence_number == int(number)
        ), None)
        if source is None:
            source = self.canvas._sequence_archive.get(
                (layer_index, int(number))
            )
        if source is None:
            return
        self.canvas.push_doc_undo()
        self.canvas._ensure_frame_count(int(column) + 1)
        target = self.canvas.frames[int(column)].layers[layer_index]
        copied = source.clone()
        target.image = source.image
        target.has_content = bool(copied.has_content)
        target.is_blank_key = not bool(copied.has_content)
        target.sequence_number = int(number)
        target.sequence_only = False
        target.exposure = 1
        self.canvas.current_frame = int(column)
        self.canvas.active_layer_index = layer_index
        self.canvas._cell_structure_dirty = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def schedule_used_color_refresh(self):
        # 描画直後は colorUsed で新色だけを即時追加し、全画像の色走査は
        # アイドル時にまとめる。投げ縄塗り・バケツ確定時の一拍停止を防ぐ。
        self._used_color_request += 1
        self._used_color_timer.start()

    def _refresh_used_colors_without_delay(self):
        self._used_color_timer.stop()
        self._used_color_request += 1
        self.refresh_used_colors(request=self._used_color_request)

    def undo_with_used_colors(self):
        if not self.canvas.undo_stack:
            return
        self.canvas.undo()
        self._refresh_used_colors_without_delay()

    def redo_with_used_colors(self):
        if not self.canvas.redo_stack:
            return
        self.canvas.redo()
        self._refresh_used_colors_without_delay()

    def _used_color_cache_key(self, image):
        try:
            return (int(image.cacheKey()), image.width(), image.height())
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.debug("cacheKey() unavailable, using id() fallback: %s", exc)
            return (id(image), image.width(), image.height())

    def _used_color_layer_signature(self, layer_index):
        signature = []
        for frame in self.canvas.frames:
            if layer_index >= len(frame.layers):
                signature.append(None)
                continue
            layer = frame.layers[layer_index]
            signature.append(
                self._used_color_cache_key(layer.image)
                if layer.has_content else None
            )
        return tuple(signature)

    def _apply_used_color_result(self, colors, exceeded=False):
        ordered = [QColor(r, g, b) for r, g, b in colors[:100]]
        self.palette.set_colors(ordered)
        if exceeded:
            self.palette.count_label.setText("100色以上")

    def _extract_used_colors(self, image, limit=101):
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        if width <= 0 or height <= 0:
            return []
        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        array = np.frombuffer(ptr, dtype=np.uint8).reshape((height, rgba.bytesPerLine()))[:, :width * 4]
        pixels = array.reshape((-1, 4))
        pixels = pixels[pixels[:, 3] > 0, :3]
        if pixels.size == 0:
            return []
        # np.unique is much faster than pixelColor() calls from Python.
        packed = (
            (pixels[:, 0].astype(np.uint32) << 16)
            | (pixels[:, 1].astype(np.uint32) << 8)
            | pixels[:, 2].astype(np.uint32)
        )
        unique = np.unique(packed)
        if len(unique) > limit:
            unique = unique[:limit]
        return [
            (
                int((value >> 16) & 255),
                int((value >> 8) & 255),
                int(value & 255),
            )
            for value in unique
        ]

    def refresh_used_colors(
        self,
        request=None,
        progress=None,
        progress_offset=0,
        progress_total=None,
        progress_label="使用色を認識しています",
    ):
        if request is not None and request != self._used_color_request:
            return
        if self.canvas.drawing:
            # Never let an all-frame palette scan interrupt a live brush stroke.
            self._used_color_timer.start(500)
            return
        if not self.canvas.frames:
            self.palette.set_colors([])
            if progress is not None:
                total = progress_total or max(1, progress_offset)
                self.update_progress_counter(
                    progress, progress_offset, total, progress_label
                )
            return

        layer_index = self.canvas.active_layer_index
        layer_signature = self._used_color_layer_signature(layer_index)
        layer_cache_key = (int(layer_index), layer_signature)
        cached_layer = self._used_color_layer_cache.get(layer_cache_key)
        if cached_layer is not None:
            colors, exceeded = cached_layer
            self._apply_used_color_result(colors, exceeded)
            if progress is not None:
                total = progress_total or max(1, progress_offset + len(self.canvas.frames))
                self.update_progress_counter(
                    progress, total, total, "使用色の認識が完了しました"
                )
            return
        all_colors = []
        seen_colors = set()
        exceeded = False
        frame_total = len(self.canvas.frames)
        combined_total = progress_total or max(1, progress_offset + frame_total)

        for scan_index, frame in enumerate(self.canvas.frames, 1):
            if progress is not None:
                self.update_progress_counter(
                    progress,
                    progress_offset + scan_index - 1,
                    combined_total,
                    f"{progress_label}（{scan_index}/{frame_total}コマ）",
                )

            if layer_index < len(frame.layers):
                layer = frame.layers[layer_index]
                if layer.has_content:
                    image = layer.image
                    key = self._used_color_cache_key(image)
                    cached = self._used_color_cache.get(key)
                    if cached is None:
                        colors = self._extract_used_colors(image, 101)
                        cached = (colors[:100], len(colors) > 100)
                        if len(self._used_color_cache) >= 512:
                            self._used_color_cache.pop(
                                next(iter(self._used_color_cache))
                            )
                        self._used_color_cache[key] = cached
                    colors, cell_exceeded = cached
                    for rgb in colors:
                        if rgb not in seen_colors:
                            seen_colors.add(rgb)
                            all_colors.append(rgb)
                    exceeded = (
                        exceeded or cell_exceeded or len(all_colors) > 100
                    )

            # 100色を超えた後も、進捗表示は最後まで進める。
            if len(all_colors) > 100:
                exceeded = True

        colors = all_colors[:100]
        if len(self._used_color_layer_cache) >= 128:
            self._used_color_layer_cache.pop(
                next(iter(self._used_color_layer_cache))
            )
        self._used_color_layer_cache[layer_cache_key] = (
            tuple(colors), bool(exceeded)
        )
        self._apply_used_color_result(colors, exceeded)

        if progress is not None:
            self.update_progress_counter(
                progress,
                progress_offset + frame_total,
                combined_total,
                "使用色の認識が完了しました",
            )

    def apply_palette_isolate_color(self, color):
        self.isolate_selected_color(QColor(color))

    def apply_palette_replacements(self, mapping, operation="色置換"):
        if not mapping:
            if operation == "色置換":
                message = "置換色が登録されていません。"
            elif operation == "色削除":
                message = "削除する使用色が選択されていません。"
            else:
                message = "統合する使用色が選択されていません。"
            self.statusBar().showMessage(message, 2200)
            return False

        packed_mapping = {}
        rgb_mapping = {}
        for source, destination in mapping.items():
            source = tuple(int(value) for value in source[:3])
            destination = tuple(int(value) for value in destination[:3])
            if source == (255, 255, 255) or source == destination:
                continue
            source_value = (source[0] << 16) | (source[1] << 8) | source[2]
            packed_mapping[source_value] = destination
            rgb_mapping[source] = destination

        if not packed_mapping:
            if operation == "色置換":
                message = "置換前と置換後が同じ色です。"
            elif operation == "色削除":
                message = "削除できる使用色が選択されていません。"
            else:
                message = "親以外の使用色を選択してください。"
            self.statusBar().showMessage(message, 2200)
            return False

        # 色ごとに画像全体を再走査せず、24bit RGBを一度だけ検索する。
        source_values = np.array(
            sorted(packed_mapping.keys()), dtype=np.uint32
        )
        destination_values = np.array(
            [packed_mapping[int(value)] for value in source_values],
            dtype=np.uint8,
        )

        layer_index = self.canvas.active_layer_index
        changed_pixels = 0
        changed_cells = 0
        undo_cells = []
        cache_updates = {}
        cache_removals = set()
        frame_items = list(enumerate(self.canvas.frames))
        progress = self.create_progress_counter(
            operation,
            len(frame_items),
            f"{operation}の対象コマを確認しています",
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for progress_index, (frame_index, frame) in enumerate(
                frame_items, 1
            ):
                self.update_progress_counter(
                    progress,
                    progress_index - 1,
                    len(frame_items),
                    f"コマ {frame_index + 1} に{operation}を適用しています",
                )
                if layer_index >= len(frame.layers):
                    continue
                layer = frame.layers[layer_index]
                if not layer.has_content:
                    continue

                old_cache_key = self._used_color_cache_key(layer.image)
                old_cached_colors = self._used_color_cache.get(old_cache_key)
                rgba = layer.image.convertToFormat(
                    QImage.Format.Format_RGBA8888
                )
                width, height = rgba.width(), rgba.height()
                if width <= 0 or height <= 0:
                    continue
                ptr = rgba.bits()
                try:
                    ptr.setsize(rgba.sizeInBytes())
                except AttributeError:
                    pass
                rows = np.frombuffer(
                    ptr, dtype=np.uint8
                ).reshape((height, rgba.bytesPerLine()))
                pixels = rows[:, :width * 4].reshape((height, width, 4))

                packed = (
                    (pixels[:, :, 0].astype(np.uint32) << 16)
                    | (pixels[:, :, 1].astype(np.uint32) << 8)
                    | pixels[:, :, 2].astype(np.uint32)
                )
                indices = np.searchsorted(source_values, packed)
                safe_indices = np.minimum(indices, len(source_values) - 1)
                matches = (
                    (indices < len(source_values))
                    & (source_values[safe_indices] == packed)
                    & (pixels[:, :, 3] != 0)
                )
                cell_count = int(np.count_nonzero(matches))
                if not cell_count:
                    continue

                undo_cells.append(
                    (frame_index, layer.image.copy(), layer.has_content)
                )
                pixels[:, :, :3][matches] = destination_values[
                    safe_indices[matches]
                ]
                layer.image = rgba.convertToFormat(
                    QImage.Format.Format_ARGB32_Premultiplied
                )
                cache_removals.add(old_cache_key)
                if old_cached_colors is not None:
                    old_colors, exceeded = old_cached_colors
                    transformed_colors = []
                    seen_transformed = set()
                    for rgb in old_colors:
                        new_rgb = tuple(rgb_mapping.get(tuple(rgb), tuple(rgb)))
                        if new_rgb in seen_transformed:
                            continue
                        seen_transformed.add(new_rgb)
                        transformed_colors.append(new_rgb)
                    cache_updates[
                        self._used_color_cache_key(layer.image)
                    ] = (transformed_colors[:100], exceeded)
                changed_pixels += cell_count
                changed_cells += 1
                self.update_progress_counter(
                    progress,
                    progress_index,
                    len(frame_items),
                    f"コマ {frame_index + 1} の{operation}が完了しました",
                )
        finally:
            QApplication.restoreOverrideCursor()
            self.close_progress_counter(progress)

        if not changed_pixels:
            self.statusBar().showMessage(
                "選択した使用色は画像内にありませんでした。",
                2400,
            )
            return False

        self.canvas.undo_stack.append(("layer_batch", layer_index, undo_cells))
        self.canvas.undo_stack = self.canvas.undo_stack[-MAX_UNDO:]
        self.canvas.redo_stack.clear()
        for cache_key in cache_removals:
            self._used_color_cache.pop(cache_key, None)
        self._used_color_cache.update(cache_updates)
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._silhouette_cache.clear()

        # コマ構造は変わらないためタイムラインを再構築しない。
        # 画像更新と、キャッシュを利用した使用色一覧の更新だけを行う。
        self.canvas.update()
        self.schedule_used_color_refresh()
        self.statusBar().showMessage(
            f"{changed_cells}セル・{changed_pixels:,}ピクセルへ{operation}を適用しました。",
            3000,
        )
        return True
    def apply_palette_delete(self, selected_rgbs):
        """選択した使用色を #FFFFFF へ統合する。"""
        selected = set()
        try:
            for rgb in selected_rgbs:
                if rgb is None or len(rgb) < 3:
                    continue
                color = tuple(
                    max(0, min(255, int(channel)))
                    for channel in rgb[:3]
                )
                if color != (255, 255, 255):
                    selected.add(color)
        except (TypeError, ValueError):
            selected = set()

        if not selected:
            self.statusBar().showMessage(
                "削除する使用色が選択されていません。",
                2400,
            )
            return

        mapping = {
            color: (255, 255, 255)
            for color in selected
        }
        if self.apply_palette_replacements(
            mapping,
            operation="色削除",
        ):
            # 削除後に存在しない親・子選択を残さない。
            self.palette._clear_used_color_selection()
            self.statusBar().showMessage(
                f"{len(selected)}色を #FFFFFF へ統合しました。",
                3200,
            )

    def apply_palette_merge(self, parent_rgb, selected_rgbs):
        parent = tuple(int(channel) for channel in parent_rgb[:3])
        selected = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in selected_rgbs
            if rgb is not None and len(rgb) >= 3
        }
        if parent == (255, 255, 255):
            self.statusBar().showMessage(
                "背景色は統合先にできません。",
                2400,
            )
            return
        mapping = {
            rgb: parent
            for rgb in selected
            if rgb != parent and rgb != (255, 255, 255)
        }
        if self.apply_palette_replacements(mapping, operation="色統合"):
            self.palette._retain_parent_selection()

    def _layer_indices_from_rows(self, rows):
        count = len(self.canvas.layers)
        result = []
        for row in rows:
            index = count - 1 - int(row)
            if 0 <= index < count and index not in result:
                result.append(index)
        return sorted(result)

    def refresh_used_colors_with_counter(self, title="使用色を更新しています"):
        self._used_color_timer.stop()
        self._used_color_request += 1
        total = max(1, len(self.canvas.frames))
        progress = self.create_progress_counter(
            title,
            total,
            "選択レイヤーの使用色を認識しています",
        )
        try:
            self.refresh_used_colors(
                progress=progress,
                progress_total=total,
                progress_label="選択レイヤーの使用色を認識しています",
            )
        finally:
            self.close_progress_counter(progress)

    def select_timeline_exposure(self, column, visual_row):
        previous_layer = self.canvas.active_layer_index
        if self.canvas.timeline_mode == "sequence":
            layer_count = len(self.canvas.layers)
            layer_index = layer_count - 1 - int(visual_row)
            number = int(column) + 1
            entries = self.canvas.sequence_entry_columns(layer_index)
            frame_by_number = {
                int(self.canvas.frames[index].layers[layer_index].sequence_number): index
                for index in entries
            }
            if number in frame_by_number:
                self.canvas.current_frame = frame_by_number[number]
                self.canvas.active_layer_index = layer_index
                self.canvas.selectionChanged.emit()
                self.canvas.update()
            return
        self.canvas.select_exposure(column, visual_row)
        # タイムラインの別レイヤーのコマを選んだ場合も、そのレイヤーの使用色へ即時更新。
        if self.canvas.active_layer_index != previous_layer:
            self._refresh_used_colors_without_delay()
        else:
            # 同じレイヤーでは全コマ共通の使用色一覧なので、既存表示を維持する。
            self.canvas.update()

    def add_layer_fast(self):
        """新規空レイヤーでは全コマの使用色走査を行わず即時表示する。"""
        self._used_color_timer.stop()
        self._used_color_request += 1
        self._suppress_used_color_refresh_once = True
        self.canvas.add_layer()
        self.palette.set_colors([])

    def duplicate_layer_rows(self, rows):
        indices = self._layer_indices_from_rows(rows)
        if not indices:
            return
        self.canvas.push_doc_undo()
        for frame in self.canvas.frames:
            for index in sorted(indices, reverse=True):
                copied = frame.layers[index].clone()
                copied.name = f"{copied.name} コピー"
                frame.layers.insert(index + 1, copied)
        self.canvas.active_layer_index = min(
            len(self.canvas.layers) - 1,
            max(indices) + len(indices),
        )
        self.canvas._onion_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def merge_layer_rows(self, rows):
        indices = self._layer_indices_from_rows(rows)
        if len(indices) < 2:
            self.statusBar().showMessage(
                "結合するレイヤーをShift＋クリックで2つ以上選択してください。",
                2600,
            )
            return
        if indices != list(range(indices[0], indices[-1] + 1)):
            self.statusBar().showMessage(
                "結合できるのは連続しているレイヤーです。",
                2600,
            )
            return

        base_index = indices[0]
        top_index = indices[-1]
        result_name = self.canvas.layers[top_index].name
        frame_count = len(self.canvas.frames)
        self.canvas.push_doc_undo()

        # 元レイヤーを削除する前に、各タイムライン位置で実際に表示される
        # キーフレームを解決する。これにより「ーーー｜」の保持区間が
        # 白紙へ置き換わる問題を防ぐ。
        merged_layers = [
            Layer(
                result_name,
                blank_image(),
                visible=True,
                opacity=1.0,
                has_content=False,
                exposure=1,
            )
            for _ in range(frame_count)
        ]

        previous_signature = None
        active_key_frame = None

        progress = self.create_progress_counter(
            "レイヤーを結合",
            max(1, frame_count),
            "保持コマを解析しています",
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for frame_index in range(frame_count):
                resolved = []
                signature_parts = []

                for layer_index in indices:
                    key_frame = self.canvas.resolve_key_frame(
                        frame_index, layer_index
                    )
                    if key_frame is None:
                        continue
                    source_layer = self.canvas.frames[
                        key_frame
                    ].layers[layer_index]
                    if (
                        not source_layer.has_content
                        or not source_layer.visible
                        or source_layer.opacity <= 0.0
                    ):
                        continue

                    resolved.append(source_layer)
                    signature_parts.append((
                        int(layer_index),
                        int(key_frame),
                        int(source_layer.image.cacheKey()),
                        round(float(source_layer.opacity), 6),
                    ))

                signature = tuple(signature_parts)

                if not resolved:
                    # 白紙区間では直前の露出を延長しない。
                    previous_signature = None
                    active_key_frame = None
                elif (
                    signature == previous_signature
                    and active_key_frame is not None
                ):
                    merged_layers[active_key_frame].exposure += 1
                else:
                    merged_image = blank_image()
                    painter = QPainter(merged_image)
                    painter.setCompositionMode(
                        QPainter.CompositionMode.CompositionMode_SourceOver
                    )
                    for source_layer in resolved:
                        painter.setOpacity(
                            max(
                                0.0,
                                min(1.0, float(source_layer.opacity)),
                            )
                        )
                        # 表示フィルターはデータへ焼き込まず、元画像を結合する。
                        painter.drawImage(0, 0, source_layer.image)
                    painter.end()

                    merged_layers[frame_index] = Layer(
                        result_name,
                        merged_image,
                        visible=True,
                        opacity=1.0,
                        has_content=True,
                        exposure=1,
                    )
                    previous_signature = signature
                    active_key_frame = frame_index

                self.update_progress_counter(
                    progress,
                    frame_index + 1,
                    max(1, frame_count),
                    f"{frame_index + 1} / {frame_count} コマを結合しています",
                )
        finally:
            QApplication.restoreOverrideCursor()
            self.close_progress_counter(progress)

        for frame_index, frame in enumerate(self.canvas.frames):
            for layer_index in reversed(indices):
                frame.layers.pop(layer_index)
            frame.layers.insert(base_index, merged_layers[frame_index])

        self.canvas.active_layer_index = base_index
        self.canvas._onion_cache.clear()
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._silhouette_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        self.refresh_used_colors_with_counter(
            "結合後の使用色を更新しています"
        )

    def delete_layer_rows(self, rows):
        indices = self._layer_indices_from_rows(rows)
        if not indices:
            return
        if len(indices) >= len(self.canvas.layers):
            QMessageBox.warning(
                self,
                "レイヤー削除",
                "すべてのレイヤーは削除できません。1つ以上残してください。",
            )
            return
        self.canvas.push_doc_undo()
        for frame in self.canvas.frames:
            for index in sorted(indices, reverse=True):
                frame.layers.pop(index)
        self.canvas.active_layer_index = min(
            indices[0],
            len(self.canvas.layers) - 1,
        )
        self.canvas._onion_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        self.refresh_used_colors_with_counter("削除後の使用色を更新しています")

    def move_layer_row(self, source_rows, destination_row):
        """レイヤー名と全コマのタイムラインデータを同じ順序で移動する。"""
        if not self.canvas.frames:
            return

        count = len(self.canvas.layers)
        try:
            rows = sorted({int(row) for row in source_rows})
        except TypeError:
            rows = [int(source_rows)]

        if (
            not rows
            or any(row < 0 or row >= count for row in rows)
            or rows != list(range(rows[0], rows[-1] + 1))
        ):
            self.refresh_ui()
            return

        block_count = len(rows)
        destination_row = max(
            0,
            min(int(destination_row), count - block_count),
        )
        if destination_row == rows[0]:
            return

        # 現在レイヤーを視覚行番号で記憶し、移動後も同じレイヤーを選択する。
        active_visual_row = count - 1 - self.canvas.active_layer_index
        visual_order = list(range(count))
        moved_order = visual_order[rows[0]:rows[-1] + 1]
        del visual_order[rows[0]:rows[-1] + 1]
        visual_order[destination_row:destination_row] = moved_order
        new_active_visual_row = visual_order.index(active_visual_row)

        self.canvas.push_doc_undo()
        for frame in self.canvas.frames:
            visual_layers = list(reversed(frame.layers))
            moved_layers = visual_layers[rows[0]:rows[-1] + 1]
            del visual_layers[rows[0]:rows[-1] + 1]
            visual_layers[destination_row:destination_row] = moved_layers
            frame.layers = list(reversed(visual_layers))

        self.canvas.active_layer_index = count - 1 - new_active_visual_row
        self.canvas._onion_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def layer_name_row(self, row, name):
        if getattr(self, "_closing", False) or row < 0:
            return
        layers = self.canvas.layers
        index = len(layers) - 1 - row
        if not (0 <= index < len(layers)):
            return
        name = name.strip() or f"Layer {index + 1}"
        self.canvas.push_doc_undo()
        for frame in self.canvas.frames:
            if index < len(frame.layers):
                frame.layers[index].name = name
        self.canvas.changed.emit()

    def layer_selected(self, row):
        if getattr(self, "_closing", False) or row < 0:
            return
        layers = self.canvas.layers
        index = len(layers) - 1 - row
        if 0 <= index < len(layers):
            if self.canvas.active_layer_index == index:
                return
            self.canvas.active_layer_index = index
            self.canvas._onion_cache.clear()
            self.timeline.select_current(self.canvas.current_exposure(), index)
            self._refresh_used_colors_without_delay()
            self.canvas.update()

    def layer_visibility_row(self, row, on):
        if getattr(self, "_closing", False) or row < 0:
            return
        layers = self.canvas.layers
        index = len(layers) - 1 - row
        if 0 <= index < len(layers):
            self.canvas.set_layer_visibility(index, on)

    def layer_opacity_row(self, row, opacity):
        if getattr(self, "_closing", False) or row < 0:
            return
        layers = self.canvas.layers
        index = len(layers) - 1 - row
        if 0 <= index < len(layers):
            self.canvas.set_layer_opacity(index, opacity)
            # 一覧の保持値もその場で更新し、別レイヤー選択時に正しく復元する。
            item = self.timeline.layer_list.item(row)
            if item is not None:
                item.setData(
                    Qt.ItemDataRole.UserRole + 5,
                    float(opacity),
                )

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

    def apply_sampled_color_to_mode(self, mode, color):
        qc = QColor(color)
        if mode == "sub":
            self.canvas.sub_color = qc
        else:
            self.canvas.main_color = qc
        self.canvas.color_mode = mode
        self.tools.set_colors(
            self.canvas.main_color, self.canvas.sub_color, self.canvas.color_mode
        )

    def apply_sampled_color(self, color):
        mode = self.canvas.color_mode
        if mode == "sub":
            self.canvas.sub_color = QColor(color)
        else:
            self.canvas.main_color = QColor(color)
            mode = "main"
            self.canvas.color_mode = "main"
        self.tools.set_colors(self.canvas.main_color, self.canvas.sub_color, mode)
        self.palette.select_matching_color(color)

    @staticmethod
    def _parse_after_effects_time_remap(raw_text):
        text_value = str(raw_text or "").replace("\r", "")
        fps_match = re.search(
            r"Units\s+Per\s+Second\s+([0-9]+(?:\.[0-9]+)?)",
            text_value,
            re.IGNORECASE,
        )
        fps = float(fps_match.group(1)) if fps_match else 24.0
        if fps <= 0.0:
            fps = 24.0

        time_entries = []
        opacity_entries = []
        section = None
        number_pattern = re.compile(
            r"^\s*(-?\d+(?:\.\d+)?)\s+"
            r"(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
        )

        for line in text_value.splitlines():
            lowered = line.strip().lower()
            if lowered.startswith("time remap"):
                section = "time"
                continue
            if (
                lowered.startswith("transform")
                and "opacity" in lowered
            ):
                section = "opacity"
                continue
            if lowered.startswith("end of keyframe data"):
                section = None
                continue

            match = number_pattern.match(line)
            if match is None or section is None:
                continue
            frame = int(round(float(match.group(1))))
            value = float(match.group(2))
            if frame < 0:
                continue
            if section == "time":
                time_entries.append((frame, value))
            else:
                opacity_entries.append((frame, value))

        if not time_entries:
            raise ValueError(
                "Time RemapのFrame／secondsデータが見つかりません。"
            )

        # 同じフレームが複数ある場合は、後から書かれた値を優先。
        time_map = {}
        for frame, value in time_entries:
            time_map[int(frame)] = float(value)
        opacity_map = {}
        for frame, value in opacity_entries:
            opacity_map[int(frame)] = float(value)
        time_entries = sorted(time_map.items())
        opacity_entries = sorted(opacity_map.items())

        all_frames = [frame for frame, _value in time_entries]
        all_frames.extend(
            frame for frame, _value in opacity_entries
        )
        start_frame = max(0, min(all_frames))
        end_frame = max(all_frames)

        time_index = 0
        current_seconds = float(time_entries[0][1])
        opacity_index = 0
        current_opacity = (
            float(opacity_entries[0][1])
            if opacity_entries else 100.0
        )
        states = []

        for frame in range(start_frame, end_frame + 1):
            while (
                time_index + 1 < len(time_entries)
                and time_entries[time_index + 1][0] <= frame
            ):
                time_index += 1
                current_seconds = float(
                    time_entries[time_index][1]
                )
            while (
                opacity_entries
                and opacity_index + 1 < len(opacity_entries)
                and opacity_entries[opacity_index + 1][0] <= frame
            ):
                opacity_index += 1
                current_opacity = float(
                    opacity_entries[opacity_index][1]
                )

            if current_seconds < 0.0 or current_opacity <= 0.0:
                states.append(None)
            else:
                # AEの0秒は連番1番、1/fps秒は連番2番。
                source_index = int(
                    math.floor(current_seconds * fps + 0.5)
                ) + 1
                states.append(max(1, source_index))

        return {
            "format": "Adobe After Effects",
            "fps": fps,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "states": states,
            "blank_label_count": 0,
        }

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

    @staticmethod
    def _parse_xdts_timesheet(raw_text):
        text_value = str(raw_text or "").lstrip("\ufeff")
        lines = text_value.splitlines()
        if not lines or lines[0].strip() != "exchangeDigitalTimeSheet Save Data":
            raise ValueError("XDTSの先頭識別文字列が一致しません。")
        try:
            payload = json.loads("\n".join(lines[1:]))
        except json.JSONDecodeError as exc:
            raise ValueError(f"XDTSのJSONを解析できません。\n{exc}") from exc
        if int(payload.get("version", -1)) != 5:
            raise ValueError("対応しているXDTSバージョンは5です。")
        time_tables = payload.get("timeTables") or []
        if not time_tables:
            raise ValueError("XDTSにタイムシート情報がありません。")
        time_table = time_tables[0]
        duration = max(1, int(time_table.get("duration", 1)))
        headers = {}
        for item in time_table.get("timeTableHeaders", []):
            if not isinstance(item, dict):
                continue
            field_id = int(item.get("fieldId", -1))
            headers.setdefault(field_id, list(item.get("names", [])))

        def column_group(field_id, name):
            if field_id == 3:
                return "ACTION"
            if field_id == 5:
                return "CAM"
            normalized = str(name).strip().casefold()
            action_words = ("memo", "action", "act", "camera", "cam", "pan")
            if (
                normalized.startswith(("_", "◆", "-"))
                or normalized in ("ts", "タイムシート")
                or any(word in normalized for word in action_words)
            ):
                return "ACTION"
            return "CELL"

        symbol_labels = {
            "SYMBOL_HYPHEN": "｜",
            "SYMBOL_NULL_CELL": "×",
            "SYMBOL_TICK_1": "○",
            "SYMBOL_TICK_2": "●",
        }
        sheet_columns = []
        parsed_tracks = []
        supported_fields = {0, 3, 5}
        for field_index, field in enumerate(time_table.get("fields", [])):
            if not isinstance(field, dict):
                continue
            field_id = int(field.get("fieldId", -1))
            if field_id not in supported_fields:
                continue
            names = headers.get(field_id, [])
            action_boundary = next(
                (
                    index for index, header_name in enumerate(names[:-1])
                    if "memo" in str(header_name).strip().casefold()
                    or "メモ" in str(header_name).strip()
                ),
                None,
            ) if field_id == 0 else None
            for track in sorted(
                field.get("tracks", []),
                key=lambda item: int(item.get("trackNo", 0)),
            ):
                if not isinstance(track, dict):
                    continue
                track_no = int(track.get("trackNo", 0))
                default_names = {
                    0: "セル欄",
                    3: "アクション",
                    5: "カメラ",
                }
                name = (
                    str(names[track_no]).strip()
                    if 0 <= track_no < len(names)
                    and str(names[track_no]).strip()
                    else f"{default_names[field_id]} {track_no + 1}"
                )
                entries = {
                    int(item.get("frame", 0)): item
                    for item in track.get("frames", [])
                    if isinstance(item, dict)
                }
                states = []
                display_values = []
                current_state = None
                blank_label_count = 0
                for frame in range(duration):
                    item = entries.get(frame)
                    values = []
                    if item is not None:
                        instruction = next(
                            (
                                data for data in item.get("data", [])
                                if isinstance(data, dict)
                                and int(data.get("id", -1)) == 0
                            ),
                            None,
                        )
                        if instruction is not None:
                            raw_values = instruction.get("values", [])
                            values = (
                                list(raw_values)
                                if isinstance(raw_values, list)
                                else [raw_values]
                            )
                    tokens = [str(value).strip() for value in values]
                    token = tokens[0] if tokens else None
                    if token in symbol_labels:
                        display = symbol_labels[token]
                    elif field_id == 3 and tokens:
                        dialogue = [value for value in tokens[:2] if value]
                        display = "：".join(dialogue)
                    elif tokens:
                        display = " / ".join(value for value in tokens if value)
                    else:
                        display = ""

                    if field_id == 0:
                        if token in (None, "SYMBOL_HYPHEN"):
                            if current_state is not None:
                                display = "｜"
                        elif token == "SYMBOL_NULL_CELL":
                            current_state = None
                        elif token in ("SYMBOL_TICK_1", "SYMBOL_TICK_2"):
                            current_state = None
                            blank_label_count += 1
                        elif token.isdecimal():
                            current_state = max(1, int(token))
                            display = str(current_state)
                        else:
                            current_state = None
                            blank_label_count += 1
                        states.append(current_state)
                    display_values.append(display)

                group = column_group(field_id, name)
                if (
                    field_id == 0
                    and action_boundary is not None
                    and track_no <= action_boundary
                ):
                    group = "ACTION"
                column = {
                    "uid": f"{field_id}:{field_index}:{track_no}",
                    "field_id": field_id,
                    "track_no": track_no,
                    "name": name,
                    "group": group,
                    "start_frame": 0,
                    "end_frame": duration - 1,
                    "states": states,
                    "display_values": display_values,
                    "blank_label_count": blank_label_count,
                    "bindable": field_id == 0 and group == "CELL",
                }
                sheet_columns.append(column)
                if field_id == 0:
                    parsed_tracks.append(dict(column))

        if not parsed_tracks:
            raise ValueError("XDTSにセル欄（fieldId 0）がありません。")
        group_order = {"ACTION": 0, "CELL": 1, "CAM": 2}
        sheet_columns.sort(
            key=lambda item: (
                group_order.get(str(item.get("group", "CELL")), 9),
                int(item.get("field_id", 0)),
                int(item.get("track_no", 0)),
            )
        )
        primary = parsed_tracks[0]
        return {
            "format": "XDTS version 5",
            "fps": None,
            "start_frame": 0,
            "end_frame": duration - 1,
            "states": list(primary["states"]),
            "blank_label_count": int(primary["blank_label_count"]),
            "tracks": parsed_tracks,
            "sheet_columns": sheet_columns,
        }

    @classmethod
    def parse_time_remap_text(cls, raw_text):
        text_value = (
            str(raw_text or "")
            .lstrip("\ufeff")
            .strip()
        )
        if not text_value:
            raise ValueError("貼り付けデータが空です。")

        lowered = text_value.lower()
        if text_value.startswith("exchangeDigitalTimeSheet Save Data"):
            return cls._parse_xdts_timesheet(text_value)
        if (
            "toeidigitaltimesheet copy data" in lowered
            or (
                text_value.startswith("{")
                and '"layers"' in text_value
                and '"frames"' in text_value
            )
        ):
            return cls._parse_toei_timesheet(text_value)

        if (
            "adobe after effects" in lowered
            or "time remap" in lowered
        ):
            return cls._parse_after_effects_time_remap(text_value)

        raise ValueError(
            "Adobe After Effects、ToeiDigitalTimeSheet、XDTS形式を"
            "判別できませんでした。"
        )

    def _time_remap_source_bank(self, layer_index):
        stored_bank = getattr(
            self.canvas,
            "_sequence_source_bank",
            [],
        )
        stored_index = int(
            getattr(
                self.canvas,
                "_sequence_source_bank_layer_index",
                -1,
            )
        )
        stored_name = str(
            getattr(
                self.canvas,
                "_sequence_source_bank_layer_name",
                "",
            )
        )

        active_name = ""
        if (
            self.canvas.frames
            and 0 <= self.canvas.current_frame
            < len(self.canvas.frames)
            and 0 <= layer_index
            < len(
                self.canvas.frames[
                    self.canvas.current_frame
                ].layers
            )
        ):
            active_name = str(
                self.canvas.frames[
                    self.canvas.current_frame
                ].layers[layer_index].name
            )

        if stored_bank and (
            stored_index == layer_index
            or (
                stored_name
                and stored_name == active_name
            )
        ):
            return [image.copy() for image in stored_bank]

        numbered_bank = {}
        for frame in self.canvas.frames:
            if layer_index >= len(frame.layers):
                continue
            layer = frame.layers[layer_index]
            number = layer.sequence_number
            if (
                layer.has_content
                and number is not None
                and int(number) >= 1
                and int(number) not in numbered_bank
            ):
                numbered_bank[int(number)] = layer.image.copy()
        if numbered_bank and set(numbered_bank) == set(
            range(1, max(numbered_bank) + 1)
        ):
            return [
                numbered_bank[number]
                for number in range(1, max(numbered_bank) + 1)
            ]

        # 番号情報のない旧プロジェクトでは、選択レイヤー内の
        # 内容キーを左から連番ソースとして採用する。
        bank = []
        for frame in self.canvas.frames:
            if layer_index >= len(frame.layers):
                continue
            layer = frame.layers[layer_index]
            if layer.has_content and not layer.image.isNull():
                bank.append(layer.image.copy())
        return bank

    @staticmethod
    def _copy_layer_display_properties(source, target):
        target.name = str(source.name)
        target.visible = bool(source.visible)
        target.opacity = float(source.opacity)
        target.is_paper = bool(source.is_paper)
        target.alpha_locked = bool(source.alpha_locked)
        target.color_filter_enabled = bool(
            source.color_filter_enabled
        )
        target.color_filter_rgb = (
            tuple(source.color_filter_rgb)
            if source.color_filter_rgb is not None
            else None
        )

    def _apply_time_remap_states_to_layer(
        self,
        states,
        start_frame,
        end_frame,
        layer_index,
        source_bank,
        current_frame,
    ):
        """検証済みのセル番号列を1レイヤーのシートへ展開する。"""
        self.canvas._ensure_frame_count(end_frame + 1)
        template = self.canvas.frames[
            current_frame
        ].layers[layer_index].clone()

        # 対象範囲より前のキーの露出が入り込まないよう切る。
        for prior in range(start_frame - 1, -1, -1):
            prior_layer = self.canvas.frames[prior].layers[layer_index]
            exposure = max(1, int(prior_layer.exposure))
            if prior + exposure > start_frame:
                prior_layer.exposure = max(1, start_frame - prior)
                break
            if prior_layer.has_content:
                break

        # 対象範囲をいったん明示空セルに戻す。
        for frame_index in range(start_frame, end_frame + 1):
            layer = self.canvas.frames[frame_index].layers[layer_index]
            self._copy_layer_display_properties(template, layer)
            layer.image = blank_image()
            layer.has_content = False
            layer.is_blank_key = False
            layer.sequence_number = None
            layer.exposure = 1

        # 同じ絵番号／空フレームが連続する区間を露出へ圧縮。
        run_start = start_frame
        run_state = states[0]
        sentinel = object()
        for offset in range(1, len(states) + 1):
            next_state = states[offset] if offset < len(states) else sentinel
            if offset < len(states) and next_state == run_state:
                continue
            run_end = start_frame + offset - 1
            target = self.canvas.frames[run_start].layers[layer_index]
            self._copy_layer_display_properties(template, target)
            target.exposure = max(1, run_end - run_start + 1)
            if run_state is None:
                target.image = blank_image()
                target.has_content = False
                target.is_blank_key = True
                target.sequence_number = None
            else:
                target.image = source_bank[int(run_state) - 1].copy()
                target.has_content = True
                target.is_blank_key = False
                target.sequence_number = int(run_state)
            if offset < len(states):
                run_start = start_frame + offset
                run_state = next_state

    def apply_time_remap_to_active_layer(self, parsed):
        states = list(parsed.get("states", []))
        start_frame = int(parsed.get("start_frame", 0))
        end_frame = int(parsed.get("end_frame", -1))
        if (
            not states
            or start_frame < 0
            or end_frame < start_frame
            or len(states) != end_frame - start_frame + 1
        ):
            raise ValueError("解析したフレーム範囲が不正です。")

        if not self.canvas.frames:
            raise ValueError("タイムラインがありません。")
        layer_index = int(self.canvas.active_layer_index)
        current_frame = max(
            0,
            min(
                int(self.canvas.current_frame),
                len(self.canvas.frames) - 1,
            ),
        )
        if not (
            0 <= layer_index
            < len(self.canvas.frames[current_frame].layers)
        ):
            raise ValueError("対象レイヤーを選択してください。")

        source_bank = self._time_remap_source_bank(layer_index)
        if not source_bank:
            raise ValueError(
                "選択レイヤーに連番画像がありません。\n"
                "先に画像連番を読み込んでください。"
            )

        referenced = sorted({
            int(state)
            for state in states
            if state is not None
        })
        missing = [
            index
            for index in referenced
            if index < 1 or index > len(source_bank)
        ]
        if missing:
            preview = ", ".join(
                str(value) for value in missing[:12]
            )
            if len(missing) > 12:
                preview += "…"
            raise ValueError(
                f"連番画像は{len(source_bank)}枚ですが、"
                "存在しない絵番号が参照されています。\n"
                f"{preview}"
            )

        format_name = str(
            parsed.get("format", "タイムリマップ")
        )
        blank_count = sum(
            1 for state in states if state is None
        )
        label_blanks = int(
            parsed.get("blank_label_count", 0)
        )
        message = (
            f"形式：{format_name}\n"
            f"反映範囲：{start_frame + 1}～"
            f"{end_frame + 1}フレーム\n"
            f"連番画像：{len(source_bank)}枚\n"
            f"空フレーム：{blank_count}フレーム"
        )
        if label_blanks:
            message += (
                f"\n中割・記号ラベル：{label_blanks}セル"
            )
        message += (
            "\n\n選択レイヤーの対象範囲を置き換えます。"
        )

        answer = QMessageBox.question(
            self,
            "タイムリマップを反映",
            message,
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False

        self.set_timeline_mode("sheet")
        self.canvas.push_doc_undo()
        self._apply_time_remap_states_to_layer(
            states,
            start_frame,
            end_frame,
            layer_index,
            source_bank,
            current_frame,
        )

        self.canvas.current_frame = start_frame
        self.canvas.active_layer_index = layer_index
        self.canvas._onion_cache.clear()
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._silhouette_cache.clear()
        self._used_color_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        self.schedule_used_color_refresh()

        self.statusBar().showMessage(
            f"{format_name}を{start_frame + 1}～"
            f"{end_frame + 1}フレームへ反映しました。",
            4200,
        )
        return True

    def apply_xdts_layer_bindings(self, parsed):
        columns = {
            str(column.get("uid", "")): column
            for column in parsed.get("sheet_columns", [])
            if isinstance(column, dict)
        }
        bindings = dict(parsed.get("layer_bindings", {}))
        start_frame = int(parsed.get("start_frame", 0))
        end_frame = int(parsed.get("end_frame", -1))
        duration = end_frame - start_frame + 1
        if duration <= 0 or not bindings:
            raise ValueError("読み込むCELLとレイヤーの紐づけがありません。")
        if not self.canvas.frames:
            raise ValueError("タイムラインがありません。")
        current_frame = max(
            0,
            min(int(self.canvas.current_frame), len(self.canvas.frames) - 1),
        )
        current_layers = self.canvas.frames[current_frame].layers
        prepared = []
        for uid, layer_index_value in bindings.items():
            column = columns.get(str(uid))
            if column is None or not bool(column.get("bindable", False)):
                continue
            states = list(column.get("states", []))
            if len(states) != duration:
                raise ValueError(
                    f"CELL「{column.get('name', '')}」のフレーム数が不正です。"
                )
            layer_index = int(layer_index_value)
            if not (0 <= layer_index < len(current_layers)):
                raise ValueError(
                    f"CELL「{column.get('name', '')}」の紐づけ先レイヤーがありません。"
                )
            source_bank = self._time_remap_source_bank(layer_index)
            if not source_bank:
                raise ValueError(
                    f"レイヤー「{current_layers[layer_index].name}」に"
                    "連番画像がありません。"
                )
            referenced = sorted({
                int(state) for state in states if state is not None
            })
            missing = [
                number for number in referenced
                if number < 1 or number > len(source_bank)
            ]
            if missing:
                preview = ", ".join(str(value) for value in missing[:12])
                if len(missing) > 12:
                    preview += "…"
                raise ValueError(
                    f"CELL「{column.get('name', '')}」は存在しない絵番号を"
                    f"参照しています（レイヤー画像 {len(source_bank)}枚）。\n"
                    f"{preview}"
                )
            prepared.append((
                column,
                layer_index,
                states,
                source_bank,
            ))
        if not prepared:
            raise ValueError("読み込めるCELLの紐づけがありません。")

        links = "\n".join(
            f"・{column.get('name', 'CELL')} → "
            f"{current_layers[layer_index].name}"
            for column, layer_index, _states, _bank in prepared
        )
        answer = QMessageBox.question(
            self,
            "XDTSタイムシートを反映",
            f"反映範囲：{start_frame + 1}～{end_frame + 1}フレーム\n"
            f"紐づけ：\n{links}\n\n"
            "紐づけたレイヤーの対象範囲を置き換えます。",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False

        self.set_timeline_mode("sheet")
        self.canvas.push_doc_undo()
        for _column, layer_index, states, source_bank in prepared:
            self._apply_time_remap_states_to_layer(
                states,
                start_frame,
                end_frame,
                layer_index,
                source_bank,
                current_frame,
            )
        self.canvas.current_frame = start_frame
        self.canvas.active_layer_index = prepared[0][1]
        self.canvas._onion_cache.clear()
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._silhouette_cache.clear()
        self._used_color_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        self.schedule_used_color_refresh()
        self.statusBar().showMessage(
            f"XDTSの{len(prepared)}個のCELLを{start_frame + 1}～"
            f"{end_frame + 1}フレームへ反映しました。",
            4200,
        )
        return True

    def show_time_remap_paste_dialog(self, file_path=None):
        clipboard_text = QApplication.clipboard().text()
        if file_path:
            try:
                clipboard_text = Path(file_path).read_text(
                    encoding="utf-8-sig"
                )
            except (OSError, UnicodeError) as exc:
                QMessageBox.warning(
                    self,
                    "XDTS読み込み",
                    f"読み込めませんでした。\n\n{exc}",
                )
                return False
        dialog = TimeRemapPasteDialog(
            clipboard_text,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            parsed = dialog.parsed_result()
            if parsed is None:
                raise ValueError("使用するタイムシート行がありません。")
            if (
                str(parsed.get("format", "")).startswith("XDTS")
                and parsed.get("sheet_columns")
            ):
                return self.apply_xdts_layer_bindings(parsed)
            return self.apply_time_remap_to_active_layer(parsed)
        except _OPERATION_ERRORS as exc:
            log.warning("time-remap paste failed: %s", exc, exc_info=True)
            QMessageBox.warning(
                self,
                "タイムリマップ貼り付け",
                "タイムラインへ反映できませんでした。\n\n"
                f"{exc}",
            )
            return False


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

    def toggle_silhouette(self, checked=None):
        if checked is None:
            checked = not self.canvas.silhouette_non_background
        checked = bool(checked)
        self.canvas.silhouette_non_background = checked

        if hasattr(self, "a_silhouette"):
            self.a_silhouette.blockSignals(True)
            self.a_silhouette.setChecked(checked)
            self.a_silhouette.blockSignals(False)

        if hasattr(self.tools, "silhouette_btn"):
            self.tools.silhouette_btn.blockSignals(True)
            self.tools.silhouette_btn.setChecked(checked)
            self.tools.silhouette_btn.blockSignals(False)

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

    def set_onion_all_layers(self, enabled):
        self.canvas.onion_all_layers = bool(enabled)
        self.canvas._onion_cache.clear()
        self.canvas.update()

    def set_onion_skin(self, enabled):
        self.canvas.onion_skin = bool(enabled)
        self.canvas._onion_cache.clear()
        self.canvas.update()

    def toggle_onion_settings_popup(self, checked):
        checked = bool(checked)
        if checked:
            self.show_onion_settings()
        else:
            browser = self._onion_settings_browser
            if browser is not None:
                browser.close()
        self.canvas.update()

    def show_onion_settings(self):
        browser = self._onion_settings_browser
        if browser is not None:
            browser.show()
            browser.raise_()
            return

        browser = OnionSkinSettingsBrowser(
            self.canvas.onion_previous_count,
            self.canvas.onion_next_count,
            self.canvas.onion_previous_opacity,
            self.canvas.onion_next_opacity,
            self.canvas.onion_previous_color,
            self.canvas.onion_next_color,
            self.canvas.onion_previous_color_enabled,
            self.canvas.onion_next_color_enabled,
            self.canvas.onion_selected_colors_only,
            self.canvas.onion_previous_shift_x,
            self.canvas.onion_previous_shift_y,
            self.canvas.onion_previous_rotation,
            self.canvas.onion_next_shift_x,
            self.canvas.onion_next_shift_y,
            self.canvas.onion_next_rotation,
            self.canvas.onion_previous_scale,
            self.canvas.onion_next_scale,
            self.canvas.onion_previous_levels,
            self.canvas.onion_next_levels,
            self.canvas.onion_center_percent,
            self.canvas.rotation,
            self,
        )
        self._onion_settings_browser = browser
        browser.settingsChanged.connect(
            self.apply_onion_browser_settings
        )
        browser.shiftEditRequested.connect(
            self.canvas.begin_onion_shift_interaction
        )
        browser.canvasPositionEditRequested.connect(
            self.canvas.begin_onion_canvas_position_interaction
        )
        browser.canvasRotationRequested.connect(
            self.canvas.set_onion_canvas_view_rotation
        )
        browser.centerCanvasRequested.connect(
            self.center_canvas_between_onion_shifts
        )
        browser.destroyed.connect(
            self._onion_settings_browser_destroyed
        )

        self.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            browser,
        )
        if (
            hasattr(self, "palette_dock")
            and self.palette_dock is not None
        ):
            self.tabifyDockWidget(self.palette_dock, browser)
        browser.show()
        browser.raise_()

    def _onion_settings_browser_destroyed(self, *_args):
        self._onion_settings_browser = None
        self.canvas.cancel_onion_interaction(restore=False)
        if self.timeline.onion_settings.isChecked():
            self.timeline.onion_settings.blockSignals(True)
            self.timeline.onion_settings.setChecked(False)
            self.timeline.onion_settings.blockSignals(False)

    def apply_onion_browser_settings(self):
        browser = self._onion_settings_browser
        if browser is None:
            return

        self.canvas.onion_previous_count = (
            browser.previous_count.value()
        )
        self.canvas.onion_next_count = (
            browser.next_count.value()
        )
        self.canvas.onion_previous_opacity = (
            browser.previous_opacity.value() / 100.0
        )
        self.canvas.onion_next_opacity = (
            browser.next_opacity.value() / 100.0
        )
        self.canvas.onion_previous_levels = (
            browser.previous_levels()
        )
        self.canvas.onion_next_levels = (
            browser.next_levels()
        )
        self.canvas.onion_previous_color = QColor(
            browser.previous_color
        )
        self.canvas.onion_next_color = QColor(
            browser.next_color
        )
        self.canvas.onion_previous_color_enabled = (
            browser.previous_color_enabled.isChecked()
        )
        self.canvas.onion_next_color_enabled = (
            browser.next_color_enabled.isChecked()
        )
        self.canvas.onion_selected_colors_only = (
            browser.selected_colors_only.isChecked()
        )
        self.canvas.onion_previous_shift_x = float(
            browser.previous_shift_x.value()
        )
        self.canvas.onion_previous_shift_y = float(
            browser.previous_shift_y.value()
        )
        self.canvas.onion_previous_rotation = float(
            browser.previous_rotation.value()
        )
        self.canvas.onion_next_shift_x = float(
            browser.next_shift_x.value()
        )
        self.canvas.onion_next_shift_y = float(
            browser.next_shift_y.value()
        )
        self.canvas.onion_next_rotation = float(
            browser.next_rotation.value()
        )
        self.canvas.onion_previous_scale = float(
            browser.previous_scale.value()
        )
        self.canvas.onion_next_scale = float(
            browser.next_scale.value()
        )
        self.canvas.onion_center_percent = float(
            browser.center_percent_value.value()
        )

        self.canvas._onion_cache.clear()
        self.canvas.update()

    def sync_onion_browser_from_canvas(self):
        browser = self._onion_settings_browser
        if browser is None:
            return
        browser.set_transform_values(
            self.canvas.onion_previous_shift_x,
            self.canvas.onion_previous_shift_y,
            self.canvas.onion_previous_rotation,
            self.canvas.onion_previous_scale,
            self.canvas.onion_next_shift_x,
            self.canvas.onion_next_shift_y,
            self.canvas.onion_next_rotation,
            self.canvas.onion_next_scale,
        )
        browser.set_canvas_rotation_value(
            self.canvas.rotation
        )

    def finish_onion_browser_interaction(self):
        browser = self._onion_settings_browser
        if browser is not None:
            browser.clear_interaction_buttons()

    def center_canvas_between_onion_shifts(self, percent):
        """前後間のTU/TB変形を現在キャンバスの基準へ取り込む。"""
        self.apply_onion_browser_settings()
        percent = max(0.0, min(100.0, float(percent)))
        self.canvas.onion_center_percent = percent
        ratio = percent / 100.0

        previous_x = float(self.canvas.onion_previous_shift_x)
        previous_y = float(self.canvas.onion_previous_shift_y)
        next_x = float(self.canvas.onion_next_shift_x)
        next_y = float(self.canvas.onion_next_shift_y)
        previous_rotation = float(
            self.canvas.onion_previous_rotation
        )
        next_rotation = float(
            self.canvas.onion_next_rotation
        )
        previous_scale = max(
            0.01,
            float(self.canvas.onion_previous_scale) / 100.0,
        )
        next_scale = max(
            0.01,
            float(self.canvas.onion_next_scale) / 100.0,
        )

        target_x = previous_x + (next_x - previous_x) * ratio
        target_y = previous_y + (next_y - previous_y) * ratio

        # 回転は最短方向、TU/TB拡大率は撮影倍率として線形補間する。
        rotation_delta = (
            (next_rotation - previous_rotation + 180.0)
            % 360.0
        ) - 180.0
        target_rotation = self.canvas._normalized_angle(
            previous_rotation + rotation_delta * ratio
        )
        target_scale = max(
            0.01,
            previous_scale + (next_scale - previous_scale) * ratio,
        )

        # 表示用デジタルズームself.canvas.zoomは変更しない。
        digital_zoom = max(0.01, float(self.canvas.zoom))
        current_tu_tb = max(
            0.0001,
            float(self.canvas.onion_tu_tb_scale),
        )
        old_view_rotation = float(self.canvas.rotation)
        view_angle = math.radians(old_view_rotation)
        local_display_x = (
            target_x * digital_zoom * current_tu_tb
        )
        local_display_y = (
            target_y * digital_zoom * current_tu_tb
        )
        display_x = (
            local_display_x * math.cos(view_angle)
            - local_display_y * math.sin(view_angle)
        )
        display_y = (
            local_display_x * math.sin(view_angle)
            + local_display_y * math.cos(view_angle)
        )

        self.canvas.pan += QPointF(display_x, display_y)
        self.canvas.rotation = self.canvas._normalized_angle(
            old_view_rotation + target_rotation
        )
        self.canvas.onion_tu_tb_scale = max(
            0.05,
            min(20.0, current_tu_tb * target_scale),
        )

        # H_target^-1 × H_eachで相対位置・回転・倍率を再取得。
        angle = math.radians(-target_rotation)
        cos_angle = math.cos(angle)
        sin_angle = math.sin(angle)

        def relative_shift(source_x, source_y):
            dx = float(source_x) - target_x
            dy = float(source_y) - target_y
            return QPointF(
                (dx * cos_angle - dy * sin_angle)
                / target_scale,
                (dx * sin_angle + dy * cos_angle)
                / target_scale,
            )

        previous_relative = relative_shift(
            previous_x,
            previous_y,
        )
        next_relative = relative_shift(next_x, next_y)

        # 選択した中央％の回転を新しい0°基準として取得する。
        # キャンバス表示角度は0°へ戻し、前後の角度は
        # 取得した中央回転との差分として再設定する。
        absorbed_view_rotation = float(self.canvas.rotation)
        previous_screen_relative = self.canvas._rotated_vector(
            previous_relative.x(),
            previous_relative.y(),
            absorbed_view_rotation,
        )
        next_screen_relative = self.canvas._rotated_vector(
            next_relative.x(),
            next_relative.y(),
            absorbed_view_rotation,
        )

        self.canvas.onion_previous_shift_x = (
            previous_screen_relative.x()
        )
        self.canvas.onion_previous_shift_y = (
            previous_screen_relative.y()
        )
        self.canvas.onion_next_shift_x = (
            next_screen_relative.x()
        )
        self.canvas.onion_next_shift_y = (
            next_screen_relative.y()
        )
        self.canvas.onion_previous_rotation = (
            self.canvas._normalized_angle(
                previous_rotation - target_rotation
            )
        )
        self.canvas.onion_next_rotation = (
            self.canvas._normalized_angle(
                next_rotation - target_rotation
            )
        )
        self.canvas.rotation = 0.0
        self.canvas.onion_previous_scale = max(
            1.0,
            min(199.0, previous_scale / target_scale * 100.0),
        )
        self.canvas.onion_next_scale = max(
            1.0,
            min(199.0, next_scale / target_scale * 100.0),
        )

        self.sync_onion_browser_from_canvas()
        self.canvas.viewChanged.emit(
            float(self.canvas.zoom),
            float(self.canvas.rotation),
        )
        self.canvas.update()
        self.statusBar().showMessage(
            f"前後の位置・回転・TU/TB拡大率間の{percent:g}%を"
            "キャンバス基準へ移しました。"
            f"（中央回転 {target_rotation:.1f}°を基準として取得／"
            f"撮影倍率 {target_scale * 100.0:.1f}%／"
            "キャンバス回転 0°／デジタルズーム変更なし）",
            4200,
        )

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
    def set_color_mode(self,mode):self.canvas.color_mode=mode;self.tools.set_colors(self.canvas.main_color,self.canvas.sub_color,mode,self.canvas.transparent_display_color)
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
    def update_project_title(self):
        if self.current_project_path:
            name = Path(self.current_project_path).name
            self.setWindowTitle(f"{APP_DISPLAY_NAME} — {name}")
        else:
            self.setWindowTitle(f"{APP_DISPLAY_NAME} — 新規プロジェクト")

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

    def image_color_hex(self, color):
        return QColor(color).name(QColor.NameFormat.HexRgb).upper()

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

    def _autosave_path(self):
        return config.config_dir() / "autosave.pmap"

    def _setup_autosave(self, interval_ms=180000):
        """Periodically snapshot the project so a crash doesn't lose work."""
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(int(interval_ms))
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

    def _autosave(self):
        # Must never raise into the event loop — autosave is best-effort.
        try:
            project_io.write_project_archive(
                self._autosave_path(),
                self.build_project_metadata(),
                self.canvas.frames,
            )
        except Exception:
            # Autosave is best-effort and must never raise into the event loop,
            # so the broad catch is intentional; log the traceback instead of
            # printing it so it lands in the app log.
            log.exception("autosave failed")

    def _maybe_restore_autosave(self):
        """On startup, offer to restore a leftover autosave (likely a crash)."""
        path = self._autosave_path()
        try:
            if not path.exists() or path.stat().st_size == 0:
                return
        except OSError:
            return
        answer = QMessageBox.question(
            self,
            "作業の復元",
            "前回のセッションが正常に終了しなかった可能性があります。\n"
            "自動保存された作業を復元しますか？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.open_project(str(path))
        else:
            self._clear_autosave()

    def _clear_autosave(self):
        try:
            self._autosave_path().unlink(missing_ok=True)
        except OSError:
            pass

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

    def write_project(self, path):
        project_path = Path(path)

        metadata = self.build_project_metadata()

        try:
            project_io.write_project_archive(
                project_path, metadata, self.canvas.frames
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

    def open_dropped_time_remap(self, path):
        remap_path = Path(path)
        if remap_path.suffix.lower() not in (".xdts", ".xtds"):
            return False
        return bool(self.show_time_remap_paste_dialog(str(remap_path)))

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

    def open_project(self, path):
        project_path = Path(path)
        try:
            metadata, loaded_frames, width, height = project_io.read_project_archive(project_path)

            constants.CANVAS_WIDTH = width
            constants.CANVAS_HEIGHT = height
            self.canvas.frames = loaded_frames
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
            self.tools.silhouette_btn.setChecked(
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

    def import_psd_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "PSDを読み込む", "", "Photoshop Document (*.psd)"
        )
        if not path:
            return
        if PSDImage is None or PILImage is None:
            QMessageBox.warning(
                self,
                "PSD読み込み",
                "PSDの読み込みには psd-tools と Pillow が必要です。\n"
                "requirements.txtをインストールしてください。",
            )
            return
        try:
            psd = PSDImage.open(path)
            psd_width = int(psd.width)
            psd_height = int(psd.height)
            if (
                psd_width < 1
                or psd_height < 1
                or psd_width > MAX_IMAGE_DIMENSION
                or psd_height > MAX_IMAGE_DIMENSION
                or psd_width * psd_height > MAX_SINGLE_IMAGE_PIXELS
            ):
                raise ValueError(
                    "PSDの画像サイズが上限を超えています。"
                    f" ({psd_width} × {psd_height}px)"
                )
            if len(psd) > MAX_PROJECT_LAYERS:
                raise ValueError("PSDの最上位レイヤー数が上限を超えています。")
            imported = []
            skipped = 0
            imported_cell_count = 0
            viewport = (0, 0, psd_width, psd_height)
            for top_layer in psd:
                frame_layers = list(top_layer) if top_layer.is_group() else [top_layer]
                imported_cell_count += len(frame_layers)
                if imported_cell_count > MAX_PROJECT_LAYER_CELLS:
                    raise ValueError("PSDのレイヤー項目数が上限を超えています。")
                key_images = []
                for psd_layer in frame_layers:
                    try:
                        rendered = psd_layer.composite(
                            viewport=viewport,
                            force=True,
                        )
                    except Exception:
                        rendered = None
                    if rendered is None:
                        skipped += 1
                        continue
                    key_images.append(
                        PaintCanvas._pil_rgba_to_qimage(rendered)
                    )
                if key_images:
                    imported.append((
                        str(top_layer.name or "Layer"),
                        bool(top_layer.is_visible()),
                        max(0.0, min(1.0, float(top_layer.opacity) / 255.0)),
                        key_images,
                    ))
                else:
                    skipped += 1
            if not imported:
                raise ValueError("読み込める画像レイヤーがありません。")
        except Exception as exc:
            QMessageBox.critical(
                self, "PSD読み込み", f"PSDを読み込めませんでした。\n\n{exc}"
            )
            return

        if int(psd.width) > constants.CANVAS_WIDTH or int(psd.height) > constants.CANVAS_HEIGHT:
            self.canvas.push_doc_undo()
            self.replace_doc(
                max(constants.CANVAS_WIDTH, int(psd.width)),
                max(constants.CANVAS_HEIGHT, int(psd.height)),
                preserve=True,
            )
        self.canvas.push_doc_undo()
        start_frame = int(self.canvas.current_frame)
        maximum_keys = max(len(images) for _name, _visible, _opacity, images in imported)
        self.canvas._ensure_frame_count(start_frame + maximum_keys)
        first_new_layer = len(self.canvas.frames[0].layers)
        for name, visible, opacity, key_images in imported:
            layer_index = len(self.canvas.frames[0].layers)
            for frame in self.canvas.frames:
                frame.layers.append(Layer(
                    name,
                    blank_image(),
                    visible=visible,
                    opacity=opacity,
                ))
            for offset, image in enumerate(key_images):
                target = self.canvas.frames[start_frame + offset].layers[layer_index]
                self.canvas._place_imported_image(image, target.image)
                target.has_content = True
                target.exposure = 1
                target.sequence_number = offset + 1
        self.canvas.current_frame = start_frame
        self.canvas.active_layer_index = first_new_layer
        self.canvas.timeline_mode = "sheet"
        self.timeline.set_timeline_mode("sheet")
        self.canvas._cell_structure_dirty = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        message = f"PSDから{len(imported)}レイヤーを読み込みました。"
        if skipped:
            message += f"\n調整レイヤーなど{skipped}項目は破棄しました。"
        QMessageBox.information(self, "PSD読み込み", message)

    def resize_doc(self):
        d=CanvasSizeDialog(constants.CANVAS_WIDTH,constants.CANVAS_HEIGHT,"キャンバスサイズの変更",self)
        if d.exec():self.canvas.push_doc_undo();self.replace_doc(*d.values(),preserve=True)
    def replace_doc(self,w,h,preserve=False):
        old_frames=self.canvas.frames if preserve else None;oldw,oldh=constants.CANVAS_WIDTH,constants.CANVAS_HEIGHT;constants.CANVAS_WIDTH,constants.CANVAS_HEIGHT=w,h
        if preserve:
            new=[]
            for f in old_frames:
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
            self.drawing_color_scroll,
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
            self.drawing_color_dock,
            self.drawing_color_scroll.viewport(),
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
            self.timer.stop()
            self._used_color_timer.stop()
            self._visible_color_timer.stop()
            if getattr(self, "_autosave_timer", None) is not None:
                self._autosave_timer.stop()
            self.timeline.blockSignals(True)
            self.timeline.table.blockSignals(True)
            self.timeline.layer_list.blockSignals(True)
            self.canvas.blockSignals(True)
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

    def export_xdts_dialog(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "XDTSタイムシートを書き出す",
            "PaintMaskAnimator.xdts",
            "XDTSタイムシート (*.xdts)",
        )
        if not path:
            return
        if not path.lower().endswith(".xdts"):
            path += ".xdts"

        duration = self._sheet_duration()
        tracks = []
        names = []
        layer_count = len(self.canvas.frames[0].layers)
        for layer_index in range(layer_count):
            names.append(self.canvas.frames[0].layers[layer_index].name)
            frame_data = []
            for column in range(duration):
                kind, key_column, _exposure = TimelineWidget.timeline_span_at(
                    self.canvas.frames, layer_index, column
                )
                if kind == "content":
                    if column == key_column:
                        number = self.canvas.frames[key_column].layers[
                            layer_index
                        ].sequence_number
                        value = str(number) if number is not None else "SYMBOL_NULL_CELL"
                    else:
                        value = "SYMBOL_HYPHEN"
                elif kind == "blank":
                    value = (
                        "SYMBOL_NULL_CELL"
                        if column == key_column else "SYMBOL_HYPHEN"
                    )
                else:
                    value = "SYMBOL_NULL_CELL"
                frame_data.append({
                    "frame": column,
                    "data": [{"id": 0, "values": [value]}],
                })
            tracks.append({"trackNo": layer_index, "frames": frame_data})

        payload = {
            "timeTables": [{
                "duration": duration,
                "name": "PaintMaskAnimator",
                "timeTableHeaders": [{"fieldId": 0, "names": names}],
                "fields": [{"fieldId": 0, "tracks": tracks}],
            }],
            "version": 5,
        }
        try:
            text = (
                "exchangeDigitalTimeSheet Save Data\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
                + "\n"
            )
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "XDTS書き出し", str(exc))
            return
        QMessageBox.information(
            self, "XDTS書き出し", f"タイムシートを書き出しました。\n\n{path}"
        )

    def export_psd_dialog(self):
        if PSDImage is None or PILImage is None:
            QMessageBox.warning(
                self,
                "PSD書き出し",
                "PSDの書き出しには psd-tools と Pillow が必要です。\n"
                "requirements.txtをインストールしてください。",
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "PSDを書き出す",
            "PaintMaskAnimator.psd",
            "Photoshop Document (*.psd)",
        )
        if not path:
            return
        if not path.lower().endswith(".psd"):
            path += ".psd"
        try:
            psd = PSDImage.new(
                "RGB",
                (int(constants.CANVAS_WIDTH), int(constants.CANVAS_HEIGHT)),
                color=(255, 255, 255),
            )
            layer_count = len(self.canvas.frames[0].layers)
            exported_keys = 0
            for layer_index in range(layer_count):
                template = self.canvas.frames[0].layers[layer_index]
                folder_name = str(template.name or f"Layer {layer_index + 1}")
                group = psd.create_group(
                    name=folder_name,
                    opacity=max(0, min(255, int(round(template.opacity * 255)))),
                )
                group.visible = bool(template.visible)
                key_number = 0
                for frame in self.canvas.frames:
                    layer = frame.layers[layer_index]
                    if not layer.has_content or layer.sequence_only:
                        continue
                    key_number += 1
                    source = layer.image.copy(
                        OUTSIDE_MARGIN,
                        OUTSIDE_MARGIN,
                        constants.CANVAS_WIDTH,
                        constants.CANVAS_HEIGHT,
                    )
                    pil_image = PaintCanvas._qimage_to_pil_rgba(source)
                    pixel_layer = psd.create_pixel_layer(
                        pil_image,
                        name=f"{folder_name}{key_number:04d}",
                    )
                    group.append(pixel_layer)
                    exported_keys += 1
            if exported_keys == 0:
                raise ValueError("書き出せるキーフレームがありません。")
            psd.save(path)
        except Exception as exc:
            QMessageBox.critical(
                self, "PSD書き出し", f"PSDを書き出せませんでした。\n\n{exc}"
            )
            return
        QMessageBox.information(
            self,
            "PSD書き出し",
            f"{exported_keys}個のキーフレームを書き出しました。\n\n{path}",
        )

    def import_xdts_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "XDTSタイムシートを読み込む",
            "",
            "XDTSタイムシート (*.xdts *.xtds);;すべてのファイル (*)",
        )
        if not path:
            return
        try:
            raw = Path(path).read_text(encoding="utf-8-sig")
            first_line, json_text = raw.split("\n", 1)
            if first_line.rstrip("\r") != "exchangeDigitalTimeSheet Save Data":
                raise ValueError("XDTSの先頭識別文字列が一致しません。")
            payload = json.loads(json_text)
            if int(payload.get("version", -1)) != 5:
                raise ValueError("対応しているXDTSバージョンは5です。")
            time_tables = payload.get("timeTables") or []
            if not time_tables:
                raise ValueError("タイムシート情報がありません。")
            time_table = time_tables[0]
            duration = max(1, int(time_table.get("duration", 1)))
            cell_field = next(
                (field for field in time_table.get("fields", [])
                 if int(field.get("fieldId", -1)) == 0),
                None,
            )
            if cell_field is None:
                raise ValueError("セル欄（fieldId 0）がありません。")
            tracks = sorted(
                cell_field.get("tracks", []),
                key=lambda track: int(track.get("trackNo", 0)),
            )
            header = next(
                (item for item in time_table.get("timeTableHeaders", [])
                 if int(item.get("fieldId", -1)) == 0),
                {},
            )
            names = list(header.get("names", []))
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "XDTS読み込み", f"読み込めませんでした。\n\n{exc}")
            return

        self.canvas.push_doc_undo()
        maximum_track = max(
            (int(track.get("trackNo", 0)) for track in tracks), default=0
        )
        required_layers = maximum_track + 1
        for frame in self.canvas.frames:
            while len(frame.layers) < required_layers:
                index = len(frame.layers)
                name = names[index] if index < len(names) else f"Layer {index + 1}"
                frame.layers.append(Layer(name, blank_image()))
        self.canvas._ensure_frame_count(duration)

        missing_images = set()
        for track in tracks:
            layer_index = int(track.get("trackNo", 0))
            image_bank = {}
            for frame in self.canvas.frames:
                layer = frame.layers[layer_index]
                if layer.has_content and layer.sequence_number is not None:
                    image_bank.setdefault(int(layer.sequence_number), layer.image.copy())
            for frame in self.canvas.frames:
                self.canvas._clear_timeline_layer_cell(frame.layers[layer_index])
            values = {int(item.get("frame", 0)): item for item in track.get("frames", [])}
            states = []
            previous = None
            for frame_number in range(duration):
                item = values.get(frame_number, {})
                instruction = next(
                    (data for data in item.get("data", []) if int(data.get("id", -1)) == 0),
                    {},
                )
                raw_values = instruction.get("values", [])
                value = str(raw_values[0]) if raw_values else "SYMBOL_NULL_CELL"
                if value == "SYMBOL_HYPHEN":
                    state = previous
                elif value in ("SYMBOL_NULL_CELL", "SYMBOL_TICK_1", "SYMBOL_TICK_2"):
                    state = None
                else:
                    try:
                        state = int(value)
                    except ValueError:
                        state = None
                states.append(state)
                previous = state

            run_start = 0
            for end in range(1, duration + 1):
                if end < duration and states[end] == states[run_start]:
                    continue
                state = states[run_start]
                target = self.canvas.frames[run_start].layers[layer_index]
                target.exposure = end - run_start
                if state is None:
                    target.image = blank_image()
                    target.has_content = False
                    target.is_blank_key = True
                else:
                    target.image = image_bank.get(state, blank_image()).copy()
                    target.has_content = True
                    target.is_blank_key = False
                    target.sequence_number = state
                    if state not in image_bank:
                        missing_images.add((layer_index, state))
                run_start = end
            if layer_index < len(names):
                for frame in self.canvas.frames:
                    frame.layers[layer_index].name = names[layer_index]

        self.canvas.current_frame = 0
        self.canvas.active_layer_index = 0
        self.canvas.timeline_mode = "sheet"
        self.timeline.set_timeline_mode("sheet")
        self.canvas._cell_structure_dirty = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        message = "XDTSタイムシートを読み込みました。"
        if missing_images:
            message += f"\n対応画像がない番号：{len(missing_images)}件（白画像で配置）"
        QMessageBox.information(self, "XDTS読み込み", message)

    def export_key_sequence(self, image_format):
        invalid = '<>:"/\\|?*'
        safe_layer_names = []
        used_names = set()
        for layer_index, layer in enumerate(self.canvas.layers):
            base_name = "".join(
                "_" if character in invalid else character
                for character in (layer.name.strip() or f"Layer {layer_index + 1}")
            ).strip(" .") or f"Layer {layer_index + 1}"
            safe_name = base_name
            suffix = 2
            while safe_name.casefold() in used_names:
                safe_name = f"{base_name}_{suffix}"
                suffix += 1
            used_names.add(safe_name.casefold())
            safe_layer_names.append(safe_name)
        default_folder_name = f"PaintMaskAnimator_{image_format}_CSV"

        # 最初のダイアログで保存場所と親フォルダー名を同時に指定する。
        folder_dialog = QFileDialog(
            self,
            f"連番{image_format}＋CSVの書き出しフォルダー",
        )
        folder_dialog.setOption(
            QFileDialog.Option.DontUseNativeDialog,
            True,
        )
        folder_dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        folder_dialog.setAcceptMode(
            QFileDialog.AcceptMode.AcceptSave
        )
        folder_dialog.setLabelText(
            QFileDialog.DialogLabel.FileName,
            "フォルダー名：",
        )
        folder_dialog.setLabelText(
            QFileDialog.DialogLabel.Accept,
            "この名前で作成",
        )
        folder_dialog.selectFile(default_folder_name)

        if folder_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = folder_dialog.selectedFiles()
        if not selected:
            return

        destination = Path(selected[0])
        folder_name = destination.name.strip()
        folder_name = "".join(
            "_" if character in invalid else character
            for character in folder_name
        ).strip(" .")
        if not folder_name or folder_name in (".", ".."):
            QMessageBox.warning(
                self,
                "フォルダー名",
                "使用できるフォルダー名を指定してください。",
            )
            return
        destination = destination.parent / folder_name

        try:
            if destination.exists() and not destination.is_dir():
                QMessageBox.warning(
                    self,
                    "書き出し先",
                    "同じ名前のファイルが存在するため、"
                    "フォルダーを作成できません。",
                )
                return
            if destination.exists() and any(destination.iterdir()):
                answer = QMessageBox.question(
                    self,
                    "同名フォルダー",
                    f"「{folder_name}」には既存のファイルがあります。\n"
                    "このフォルダーへ書き出しますか？",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            destination.mkdir(parents=True, exist_ok=True)
            for safe_layer_name in safe_layer_names:
                (destination / safe_layer_name).mkdir(
                    parents=True,
                    exist_ok=True,
                )
        except OSError as exc:
            QMessageBox.critical(
                self,
                "フォルダー作成エラー",
                f"書き出しフォルダーを作成できません。\n\n{exc}",
            )
            return

        keys = []
        for layer_index, safe_layer_name in enumerate(safe_layer_names):
            for frame_index, frame in enumerate(self.canvas.frames):
                if (
                    layer_index < len(frame.layers)
                    and frame.layers[layer_index].has_content
                ):
                    keys.append((
                        layer_index,
                        safe_layer_name,
                        frame_index,
                        frame.layers[layer_index],
                    ))

        if not keys:
            QMessageBox.warning(
                self,
                "連番書き出し",
                "書き出せるキーフレームがありません。",
            )
            return

        timing_rows = []
        progress = self.create_progress_counter(
            f"連番{image_format}書き出し",
            len(keys),
            f"「{folder_name}」へ書き出しています",
        )
        try:
            layer_numbers = {}
            for number, (
                layer_index,
                safe_layer_name,
                frame_index,
                layer,
            ) in enumerate(keys, 1):
                self.update_progress_counter(
                    progress,
                    number - 1,
                    len(keys),
                    f"{number}枚目を書き出しています",
                )
                image = layer.image.copy(
                    OUTSIDE_MARGIN,
                    OUTSIDE_MARGIN,
                    constants.CANVAS_WIDTH,
                    constants.CANVAS_HEIGHT,
                )
                layer_number = layer_numbers.get(layer_index, 0) + 1
                layer_numbers[layer_index] = layer_number
                filename = f"{safe_layer_name}{layer_number:04d}"
                image_destination = destination / safe_layer_name
                if image_format == "PNG":
                    output_path = image_destination / (
                        filename + ".png"
                    )
                    if not image.save(str(output_path), "PNG"):
                        raise OSError(
                            f"{output_path.name}を保存できませんでした。"
                        )
                else:
                    output_path = image_destination / (
                        filename + ".tga"
                    )
                    self.save_tga_image(image, output_path)

                timing_rows.append([
                    filename,
                    frame_index + 1,
                    frame_index + max(1, layer.exposure),
                    max(1, layer.exposure),
                ])
                self.update_progress_counter(
                    progress,
                    number,
                    len(keys),
                    f"{number}枚目の書き出しが完了しました",
                )

            csv_path = destination / "TS.csv"
            with csv_path.open(
                "w", newline="", encoding="utf-8-sig"
            ) as file:
                writer = csv.writer(file)
                writer.writerow([
                    "name",
                    "start_frame",
                    "end_frame",
                    "duration",
                ])
                writer.writerows(timing_rows)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "連番書き出しエラー",
                f"書き出し中にエラーが発生しました。\n\n{exc}",
            )
            return
        finally:
            self.close_progress_counter(progress)

        self.statusBar().showMessage(
            f"「{folder_name}」へ{len(keys)}枚と"
            "TS.csvを書き出しました。",
            4000,
        )
        QMessageBox.information(
            self,
            "連番書き出し完了",
            "次の構成で書き出しました。\n\n"
            f"{destination}\n"
            "├─ 各レイヤー名のフォルダー\n"
            "│  └─ レイヤー名0001...\n"
            "└─ TS.csv",
        )


    def export_mp4(self):
        app_file = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
        bundled_ffmpeg = app_file.with_name("ffmpeg.exe")
        ff = str(bundled_ffmpeg) if bundled_ffmpeg.exists() else shutil.which("ffmpeg")
        if not ff:QMessageBox.warning(self,'FFmpeg','ffmpegが必要です。');return
        path,_=QFileDialog.getSaveFileName(self,'MP4書き出し','animation.mp4','MP4 (*.mp4)')
        if not path:return
        if not path.lower().endswith('.mp4'):path+='.mp4'
        with tempfile.TemporaryDirectory() as td:
            total = sum(max(1, int(frame.duration)) for frame in self.canvas.frames)
            progress = self.create_progress_counter(
                "MP4書き出し",
                total,
                "動画用フレームを準備しています",
            )
            output_index = 0
            for frame_index, frame in enumerate(self.canvas.frames):
                image = self.crop_image(frame_index)
                for _ in range(max(1, int(frame.duration))):
                    output_index += 1
                    self.update_progress_counter(
                        progress,
                        output_index - 1,
                        total,
                        f"フレーム {output_index} / {total} を準備しています",
                    )
                    image.save(
                        str(Path(td) / f"f_{output_index:06}.png"),
                        "PNG",
                    )
            self.close_progress_counter(progress)
            encoding = self.create_progress_counter(
                "MP4書き出し",
                1,
                "FFmpegで動画へ変換しています",
            )
            QApplication.processEvents()
            r=subprocess.run([ff,'-y','-framerate',str(self.timeline.fps.value()),'-i',str(Path(td)/'f_%06d.png'),'-c:v','libx264','-pix_fmt','yuv420p',path],capture_output=True,text=True)
            self.close_progress_counter(encoding)
            if r.returncode:QMessageBox.critical(self,'MP4エラー',r.stderr[-1500:])
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
