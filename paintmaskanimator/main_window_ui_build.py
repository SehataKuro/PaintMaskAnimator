"""Window construction for MainWindow: actions, menus, layout and signal wiring.

Split out of ``main_window.py`` as a mixin. These methods build the UI itself --
the ``QAction`` set and shortcut actions, the menu bar, the dock/canvas layout in
``build_ui``, the action panel, and the ``connect`` pass that wires every widget
signal to its handler. They run once during ``MainWindow.__init__`` (plus on
theme changes) against a live ``MainWindow`` instance, and hold no state of their
own -- everything is assigned onto ``self``.
"""
from .common import *  # noqa: F401,F403
from ._main_window_members import MainWindowMembers
import PySide6QtAds as QtAds
from . import theme
from .theme import StatusBar
from .toolpanel import ToolPanel
from .errors import OPERATION_ERRORS
from .logging_setup import get_logger

log = get_logger(__name__)

_OPERATION_ERRORS = OPERATION_ERRORS


class UIBuildMixin(MainWindowMembers):
    def make_shortcut_action(self, name, callback, shortcut=""):
        action = QAction(name, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        action.triggered.connect(callback)
        self.addAction(action)
        return action

    def build_action_panel(self):
        # Built-in actions are now editable default scripts seeded into the
        # actions folder and registered via ActionPanel.reload_python_actions().
        # See paintmaskanimator.actionpanel.BUILTIN_SCRIPTS.
        pass

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
        self.a_import_images_raw=QAction("変換せず読み込む（下書きレイヤー）…",self)
        self.a_import_images_raw.triggered.connect(self.import_images_raw_dialog)
        self.a_import_psd=QAction("PSDを読み込む…",self)
        self.a_import_psd.triggered.connect(self.import_psd_dialog)
        self.a_import_clip=QAction("CLIP STUDIOアニメーションを読み込む…",self)
        self.a_import_clip.triggered.connect(self.import_clip_animation_dialog)

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
            self.a_silhouette, self.a_remove_dust,
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
            "メイン色とサブ色を切り替え", self.swap_main_sub, "X"
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
            ("ゴミ取り", self.a_remove_dust),
        ]
        self.tool_command_actions = [
            ("描画色：メインを選択", self.a_select_main),
            ("描画色：サブを選択", self.a_select_sub),
            ("描画色：背景色を選択", self.a_select_transparent),
            ("描画色と背景色を切り替え", self.a_toggle_draw_background),
            ("メイン色とサブ色を切り替え", self.a_swap_main_sub),
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
        f.addAction(self.a_import_images_raw)
        f.addAction(self.a_import_psd)
        f.addAction(self.a_import_clip)
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
        view_menu.addSeparator()
        self.subview_action = QAction("サブビュー", self)
        self.subview_action.setCheckable(True)
        self.subview_action.setChecked(True)
        self.subview_action.triggered.connect(self._set_subview_visible)
        view_menu.addAction(self.subview_action)
        self.color_chart_action = QAction("カラーチャート", self)
        self.color_chart_action.setCheckable(True)
        self.color_chart_action.setChecked(False)
        self.color_chart_action.triggered.connect(
            self._set_color_chart_visible
        )
        view_menu.addAction(self.color_chart_action)

    def _set_subview_visible(self, visible):
        dock = getattr(self, "subview_dock", None)
        if dock is not None:
            dock.toggleView(bool(visible))
            if visible:
                dock.raise_()
            return
        if visible:
            self.subview.show()
            self.subview.raise_()
        else:
            self.subview.hide()
    def _set_color_chart_visible(self, visible):
        dock = getattr(self, "color_chart_dock", None)
        if dock is None:
            return
        dock.toggleView(bool(visible))
        if visible:
            dock.raise_()
    def _refresh_theme_dependent_ui(self):
        """Re-apply palette-derived styles after a theme/accent change."""
        bar: Any = self.statusBar()
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

        self.history_scroll = QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setMinimumSize(0, 0)
        self.history_scroll.setWidget(self.history_panel)

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
        palette_area = self.dock_manager.addDockWidget(
            QtAds.BottomDockWidgetArea, self.palette_dock, drawing_area
        )

        self.history_dock=QtAds.CDockWidget(self.dock_manager, "ヒストリー")
        self.history_dock.setObjectName("historyDock")
        self.history_dock.setWidget(
            self.history_scroll, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        # 使用色パネルと同じ場所にタブとして重ねる。
        self.dock_manager.addDockWidget(
            QtAds.CenterDockWidgetArea, self.history_dock, palette_area
        )
        # 使用色をヒストリーより前のタブとして、起動時の前面にする。
        palette_area.setCurrentDockWidget(self.palette_dock)

        self.color_chart_dock=QtAds.CDockWidget(
            self.dock_manager, "カラーチャート"
        )
        self.color_chart_dock.setObjectName("colorChartDock")
        self.color_chart_dock.setWidget(
            self.color_chart,
            QtAds.CDockWidget.eInsertMode.ForceNoScrollArea,
        )
        self.dock_manager.addDockWidget(
            QtAds.CenterDockWidgetArea,
            self.color_chart_dock,
            palette_area,
        )
        palette_area.setCurrentDockWidget(self.palette_dock)

        self.subview_dock=QtAds.CDockWidget(self.dock_manager, "サブビュー")
        self.subview_dock.setObjectName("subviewDock")
        self.subview_dock.setWidget(
            self.subview, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        self.dock_manager.addDockWidget(
            QtAds.RightDockWidgetArea, self.subview_dock
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
            self.action_panel_dock: self._build_action_panel_menu,
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
            self.history_dock,
            self.color_chart_dock,
            self.subview_dock,
            self.timeline_dock,
        ):
            dock.topLevelChanged.connect(
                lambda floating, current=dock:
                self._sync_floating_title(current, floating)
            )
            self._add_dock_hamburger(dock, dock_menu_builders.get(dock))
            self._sync_floating_title(dock, False)
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
        for dock in (
            self.tool_selector_dock,
            self.tools_dock,
            self.action_panel_dock,
            self.color_wheel_dock,
            self.color_slider_dock,
            self.palette_dock,
            self.history_dock,
            self.color_chart_dock,
            self.subview_dock,
            self.timeline_dock,
        ):
            self._sync_floating_title(dock, dock.isFloating())
        self.subview_dock.visibilityChanged.connect(
            lambda visible: self.subview_action.setChecked(bool(visible))
        )
        self.color_chart_dock.visibilityChanged.connect(
            lambda visible: self.color_chart_action.setChecked(bool(visible))
        )
        # 旧版と同じく通常は閉じた状態。表示メニューまたはパネルメニュー
        # から必要な時だけ開く。
        self.color_chart_dock.closeDockWidget()
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
        view_menu.addAction(self.history_dock.toggleViewAction())
        view_menu.addAction(self.color_chart_dock.toggleViewAction())
        view_menu.addAction(self.subview_dock.toggleViewAction())
        view_menu.addAction(self.timeline_dock.toggleViewAction())

        help_menu=self.menuBar().addMenu("ヘルプ")
        a_check_update=QAction("更新を確認…", self)
        a_check_update.triggered.connect(self.check_for_updates_interactive)
        help_menu.addAction(a_check_update)

        self.zoom.valueChanged.connect(self.set_zoom)
        b100.clicked.connect(self.show_canvas_at_100_percent)
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

    def connect_signals(self):
        """Wire every widget signal to its handler.

        Named ``connect_signals`` rather than ``connect``: on a PySide6 object,
        an override living on a non-``QObject`` base is shadowed by the built-in
        ``QObject.connect`` at attribute-lookup time, so the plain name would
        silently resolve to Qt's method.
        """
        self.tool_selector.toolChanged.connect(self.tools.select_tool)
        self.tool_selector.snapWidthRequested.connect(
            self._snap_tool_selector_width
        )
        self.tools.toolChanged.connect(self.tool_selector.set_active_tool)
        self.tools.toolChanged.connect(self.canvas.set_tool)
        self.tools.brush_size_spinbox.valueChanged.connect(self.canvas.set_pen_size)
        self.tools.brush_stabilizer.valueChanged.connect(
            self.canvas.set_brush_stabilizer
        )
        self.tools.brush_size_spinbox.pressureRequested.connect(self.pressure)
        self.tools.colorModeChanged.connect(self.set_color_mode)
        self.tools.colorChanged.connect(self.set_color_value)
        self.subview.colorPicked.connect(self.apply_sampled_color)
        self.color_chart.colorPicked.connect(self.apply_sampled_color)
        self.color_chart.chartChanged.connect(self.on_color_chart_edited)
        self.color_chart.captureRequested.connect(self.capture_color_chart)
        self.color_chart.applyRequested.connect(self.apply_color_chart)
        self.color_chart.saveRequested.connect(self.save_color_chart_pmag)
        self.color_chart.loadRequested.connect(self.load_color_chart_pmag)
        self.subview.set_color_provider(
            lambda: (
                self.canvas.sub_color
                if self.canvas.color_mode == "sub"
                else self.canvas.main_color
            )
        )
        self.subview.colorPicked.connect(
            lambda color: self.status(
                f"サブビューから {color.name().upper()} を取得しました",
                "success",
                2500,
            )
        )
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
        self.timeline.toggleDraftLayersRequested.connect(
            self.set_layer_draft_rows
        )

        self.canvas.imagesDropped.connect(self.import_dropped_images)
        self.canvas.projectDropped.connect(self.open_dropped_project)
        self.canvas.timeRemapDropped.connect(self.open_dropped_time_remap)
        self.canvas.clipAnimationDropped.connect(self.import_clip_animation)
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
        self.palette.previewGroupsChanged.connect(self.set_preview_color_groups)
        self.palette.freezeGroupsRequested.connect(self.freeze_preview_color_groups)
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
        self.palette.historyStatePush.connect(self.push_palette_history)
        self.history_panel.jumpRequested.connect(self.jump_history)
        self.history_panel.branchSwitchRequested.connect(
            self.switch_history_branch
        )
        # 編集のたびにヒストリー一覧を更新する。
        self.canvas.changed.connect(self.refresh_history_panel)
        self.canvas.cellChanged.connect(
            lambda *_args: self.refresh_history_panel()
        )
