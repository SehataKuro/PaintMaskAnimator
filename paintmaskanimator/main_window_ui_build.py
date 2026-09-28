"""Window construction for MainWindow: actions, menus, layout and signal wiring.

Split out of ``main_window.py`` as a mixin. These methods build the UI itself --
the ``QAction`` set and shortcut actions, the menu bar, the dock/canvas layout in
``build_ui``, the action panel, and the ``connect`` pass that wires every widget
signal to its handler. They run once during ``MainWindow.__init__`` (plus on
theme changes) against a live ``MainWindow`` instance, and hold no state of their
own -- everything is assigned onto ``self``.
"""
from typing import Any
from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from .constants import APP_DISPLAY_NAME, APP_NAME, GITHUB_REPO, HOLD_ZOOM_SHORTCUT
from ._main_window_members import MainWindowMembers
import PySide6QtAds as QtAds
from . import i18n, icons, theme
from .i18n import tr
from .theme import StatusBar
from .toolpanel import ToolPanel, tool_label
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
        self.a_new=QAction(tr("新規作成…"),self);self.a_new.setShortcut("Ctrl+N");self.a_new.triggered.connect(self.new_doc)
        self.a_resize=QAction(tr("キャンバスサイズの変更…"),self);self.a_resize.triggered.connect(self.resize_doc)
        self.a_undo=QAction(tr("元に戻す"),self);self.a_undo.setShortcut("Ctrl+Z");self.a_undo.triggered.connect(self.used_color.undo)
        self.a_redo=QAction(tr("やり直す"),self);self.a_redo.setShortcut("Ctrl+Y");self.a_redo.triggered.connect(self.used_color.redo)
        self.a_copy=QAction(tr("コピー"),self);self.a_copy.setShortcut("Ctrl+C");self.a_copy.triggered.connect(self.canvas.copy_selection)
        self.a_cut=QAction(tr("切り取り"),self);self.a_cut.setShortcut("Ctrl+X");self.a_cut.triggered.connect(self.canvas.cut_selection)
        self.a_paste=QAction(tr("貼り付け"),self);self.a_paste.setShortcut("Ctrl+V");self.a_paste.triggered.connect(self.canvas.paste_clipboard)
        self.a_open_project=QAction(tr("プロジェクトを開く…"),self)
        self.a_open_project.setShortcut("Ctrl+O")
        self.a_open_project.triggered.connect(self.project.open_dialog)

        self.a_import_images=QAction(tr("画像を読み込む…"),self)
        self.a_import_images.triggered.connect(self.import_images_dialog)
        self.a_import_folder=QAction(tr("画像フォルダーを読み込む…"),self)
        self.a_import_folder.triggered.connect(self.import_image_folder_dialog)
        self.a_import_images_raw=QAction(tr("変換せず読み込む（下書きレイヤー）…"),self)
        self.a_import_images_raw.triggered.connect(self.import_images_raw_dialog)
        self.a_import_psd=QAction(tr("PSDを読み込む…"),self)
        self.a_import_psd.triggered.connect(self.importer.psd_dialog)
        self.a_import_clip=QAction(tr("CLIP STUDIOアニメーションを読み込む…"),self)
        self.a_import_clip.triggered.connect(self.importer.clip_animation_dialog)
        self.a_open_cut_folder=QAction(tr("カットフォルダーを開く…"),self)
        self.a_open_cut_folder.triggered.connect(self.importer.cut_folder_dialog)

        self.a_save_project=QAction(tr("上書き保存"),self)
        self.a_save_project.setShortcut("Ctrl+S")
        self.a_save_project.triggered.connect(self.project.save)

        self.a_save_project_as=QAction(tr("名前を付けて保存…"),self)
        self.a_save_project_as.setShortcut("Ctrl+Shift+S")
        self.a_save_project_as.triggered.connect(self.project.save_as)

        self.a_save=QAction(tr("現在のコマをPNG書き出し…"),self)
        self.a_save.triggered.connect(self.save_png)
        self.a_save_tga=QAction(tr("現在のコマをTGA書き出し…"),self)
        self.a_save_tga.triggered.connect(self.save_tga)
        self.a_export_png_seq=QAction(tr("連番PNG＋CSV書き出し…"),self);self.a_export_png_seq.triggered.connect(lambda:self.export.key_sequence("PNG"))
        self.a_export_tga_seq=QAction(tr("連番TGA＋CSV書き出し…"),self);self.a_export_tga_seq.triggered.connect(lambda:self.export.key_sequence("TGA"))
        self.a_export_cut_folder=QAction(tr("カットフォルダーへ書き出し…"),self)
        self.a_export_cut_folder.triggered.connect(self.export.cut_folder)
        self.a_export_xdts=QAction(tr("XDTSタイムシートを書き出す…"),self)
        self.a_export_xdts.triggered.connect(self.export.xdts_dialog)
        self.a_export_psd=QAction(tr("PSDを書き出す…"),self)
        self.a_export_psd.triggered.connect(self.export.psd_dialog)
        self.a_prev=QAction(tr("前のフレーム"),self);self.a_prev.setShortcut("1");self.a_prev.triggered.connect(self.timeline_ops.previous_frame)
        self.a_next=QAction(tr("次のフレーム"),self);self.a_next.setShortcut("2");self.a_next.triggered.connect(self.timeline_ops.next_frame)
        self.a_pressure=QAction(tr("筆圧設定…"),self);self.a_pressure.triggered.connect(self.pressure)
        self.a_isolate_color=QAction(tr("選択色だけ表示"),self)
        self.a_isolate_color.triggered.connect(self.colors.isolate_selected_color)
        self.a_clear_color_filter=QAction(tr("特定色表示を解除"),self)
        self.a_clear_color_filter.triggered.connect(self.colors.clear_selected_color_filter)
        self.a_silhouette=QAction(tr("背景以外を黒シルエット表示"),self); self.a_silhouette.setCheckable(True)
        self.a_silhouette.triggered.connect(self.line_ops.toggle_silhouette)
        self.a_remove_dust=QAction(tr("ゴミ取り／塗り抜け…"),self)
        self.a_remove_dust.triggered.connect(self.line_ops.remove_dust_fill_surrounding)
        self.a_shortcuts=QAction(tr("ショートカット設定…"),self);self.a_shortcuts.triggered.connect(self.shortcuts)
        self.tool_actions={}
        defaults={"brush":"P","line":"U","shape":"O","bucket":"G","lasso_fill":"F","lasso":"L","rect_select":"R","auto_select":"W","eyedropper":"","dust":"D"}
        for tid,_source_label in ToolPanel.TOOLS:
            a=QAction(tr("ツール：{tool}").format(tool=tool_label(tid)),self);a.setShortcut(defaults.get(tid,""));a.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut);a.triggered.connect(lambda _=False,t=tid:self.tools.select_tool(t));self.addAction(a);self.tool_actions[tid]=a
        self.general_actions=[(tr("新規作成"),self.a_new),(tr("プロジェクトを開く"),self.a_open_project),(tr("上書き保存"),self.a_save_project),(tr("元に戻す"),self.a_undo),(tr("やり直す"),self.a_redo),(tr("前のフレーム"),self.a_prev),(tr("次のフレーム"),self.a_next)]
        self.tool_action_list=[
            (tr("ツール：{tool}").format(tool=tool_label(tid)), self.tool_actions[tid])
            for tid, _source_label in ToolPanel.TOOLS
        ]

        # 押している間だけ有効になるキャンバス操作。QActionは設定値の保持に使い、
        # 通常のアプリケーションショートカットとしては登録しない。
        self.a_hold_hand = QAction(tr("ハンド（押している間）"), self)
        self.a_hold_hand.setProperty("holdOperation", True)
        self.a_hold_hand.setProperty("holdShortcutText", "Space")
        self.a_hold_hand.setShortcut(QKeySequence("Space"))
        self.a_hold_zoom = QAction(tr("拡大縮小（押している間）"), self)
        self.a_hold_zoom.setProperty("holdOperation", True)
        self.a_hold_zoom.setProperty("holdShortcutText", HOLD_ZOOM_SHORTCUT)
        self.a_hold_zoom.setShortcut(QKeySequence(HOLD_ZOOM_SHORTCUT))
        self.a_hold_rotate = QAction(tr("回転（押している間）"), self)
        self.a_hold_rotate.setProperty("holdOperation", True)
        self.a_hold_rotate.setProperty("holdShortcutText", "Shift+Space")
        self.a_hold_rotate.setShortcut(QKeySequence("Shift+Space"))
        self.a_hold_eyedropper = QAction(tr("スポイト（押している間）"), self)
        self.a_hold_eyedropper.setProperty("holdOperation", True)
        self.a_hold_eyedropper.setProperty("holdShortcutText", "Alt")
        self.a_hold_eyedropper.setShortcut(QKeySequence("Alt"))
        self.canvas_operation_actions = [
            (tr("ハンド"), self.a_hold_hand),
            (tr("拡大縮小"), self.a_hold_zoom),
            (tr("回転"), self.a_hold_rotate),
            (tr("スポイト"), self.a_hold_eyedropper),
        ]

        # File and edit actions not previously exposed in the shortcut dialog.
        for action in (
            self.a_new, self.a_open_project, self.a_open_cut_folder,
            self.a_import_images,
            self.a_save_project, self.a_save_project_as,
            self.a_resize, self.a_undo, self.a_redo,
            self.a_cut, self.a_copy, self.a_paste, self.a_save, self.a_save_tga,
            self.a_export_png_seq, self.a_export_tga_seq, self.a_export_cut_folder,
            self.a_prev, self.a_next,
            self.a_pressure, self.a_isolate_color, self.a_clear_color_filter,
            self.a_silhouette, self.a_remove_dust,
        ):
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)

        self.a_export_mp4 = self.make_shortcut_action(
            tr("MP4書き出し"), self.export.mp4
        )

        # Tool-panel commands.
        self.a_select_main = self.make_shortcut_action(
            tr("描画色：メインを選択"), lambda: self.colors.set_color_mode("main")
        )
        self.a_select_sub = self.make_shortcut_action(
            tr("描画色：サブを選択"), lambda: self.colors.set_color_mode("sub")
        )
        self.a_select_transparent = self.make_shortcut_action(
            tr("描画色：背景色を選択"), lambda: self.colors.set_color_mode("transparent")
        )
        self.a_toggle_draw_background = self.make_shortcut_action(
            tr("描画色と背景色を切り替え"), self.colors.toggle_draw_background_color, "C"
        )
        self.a_swap_main_sub = self.make_shortcut_action(
            tr("メイン色とサブ色を切り替え"), self.line_ops.swap_main_sub, "X"
        )
        self.a_choose_background = self.make_shortcut_action(
            tr("背景色の表示色を変更"), self.colors.choose_background_color
        )
        self.a_mainline_repaint = self.make_shortcut_action(
            "MainLineRepaint", self.line_ops.main_line_repaint
        )
        self.a_selection_transform = self.make_shortcut_action(
            tr("選択範囲：自由変形"), lambda: self.line_ops.start_wire_transform("free")
        )
        self.a_selection_scale = self.make_shortcut_action(
            tr("選択範囲：拡大縮小"), lambda: self.line_ops.start_wire_transform("scale")
        )
        self.a_selection_mesh = self.make_shortcut_action(
            tr("選択範囲：メッシュ変形"), lambda: self.line_ops.start_wire_transform("mesh")
        )
        self.a_selection_clear = self.make_shortcut_action(
            tr("選択範囲を解除"), self.canvas.clear_selection, "Ctrl+Shift+A"
        )
        self.a_transform_rotate_left = self.make_shortcut_action(
            tr("変形：左へ90°回転"),
            lambda: self.canvas.rotate_selection_transform(-90.0)
        )
        self.a_transform_rotate_right = self.make_shortcut_action(
            tr("変形：右へ90°回転"),
            lambda: self.canvas.rotate_selection_transform(90.0)
        )
        self.a_transform_mesh_grid = self.make_shortcut_action(
            tr("メッシュ変形：格子数を変更…"),
            self.line_ops.choose_transform_mesh_grid
        )
        self.a_transform_commit = self.make_shortcut_action(
            tr("変形を確定"), self.tween.commit_transform_or_tween
        )
        self.a_transform_commit.setShortcuts([
            QKeySequence(Qt.Key.Key_Return),
            QKeySequence(Qt.Key.Key_Enter),
        ])
        self.a_transform_cancel = self.make_shortcut_action(
            tr("変形をキャンセル"), self.tween.cancel_transform_or_tween
        )
        self.a_bucket_include_sub = self.make_shortcut_action(
            tr("バケツ：含み塗り ON/OFF"),
            lambda: self.tools.bucket_include_sub.toggle()
        )
        self.a_bucket_close_gap = self.make_shortcut_action(
            tr("バケツ：隙間閉じ ON/OFF"),
            lambda: self.tools.bucket_close_gap.toggle()
        )
        self.a_bucket_all_frames = self.make_shortcut_action(
            tr("バケツ：串刺し塗り ON/OFF"),
            lambda: self.tools.bucket_all_frames.toggle()
        )
        self.a_dust_all_frames = self.make_shortcut_action(
            tr("ゴミ取り：すべてのコマ ON/OFF"),
            lambda: self.tools.dust_all_frames.toggle()
        )
        self.a_dust_apply = self.make_shortcut_action(
            tr("ゴミ取り／塗り抜けを適用"), self.line_ops.remove_dust_fill_surrounding
        )
        self.a_selection_all_frames = self.make_shortcut_action(
            tr("選択変形：すべてのコマ ON/OFF"),
            self.tools.toggle_selection_all_frames
        )

        # Timeline commands.
        self.a_tl_add_exposure = self.make_shortcut_action(
            tr("タイムライン：コマ数を1つ増やす"),
            self.timeline._extend_current_exposure,
        )
        self.a_tl_delete_frame = self.make_shortcut_action(
            tr("タイムライン：コマを削除"),
            self.timeline_ops.delete_frame,
        )
        self.a_tl_previous = self.make_shortcut_action(
            tr("タイムライン：前のフレーム"),
            self.timeline_ops.previous_frame,
        )
        self.a_tl_next = self.make_shortcut_action(
            tr("タイムライン：次のフレーム"),
            self.timeline_ops.next_frame,
        )
        self.a_tl_previous_key = self.make_shortcut_action(
            tr("タイムライン：前のコマ"),
            self.timeline_ops.previous_key,
            "A",
        )
        self.a_tl_next_key = self.make_shortcut_action(
            tr("タイムライン：次のコマ"),
            self.timeline_ops.next_key,
            "S",
        )
        self.a_tl_play = self.make_shortcut_action(
            tr("タイムライン：再生／停止"),
            lambda: self.timeline.play.toggle(),
        )
        self.a_tl_paste_time_remap = self.make_shortcut_action(
            tr("タイムライン：タイムリマップを貼り付け"),
            self.time_remap.show_paste_dialog,
        )
        self.a_tl_onion = self.make_shortcut_action(
            tr("タイムライン：オニオンスキン設定"),
            lambda: self.timeline.onion.toggle(),
        )
        self.a_tl_layer_add = self.make_shortcut_action(
            tr("タイムライン：レイヤー追加"),
            self.canvas.add_layer,
        )
        self.a_tl_layer_delete = self.make_shortcut_action(
            tr("タイムライン：レイヤー削除"),
            self.canvas.delete_layer,
        )
        self.a_tl_layer_rename = self.make_shortcut_action(
            tr("タイムライン：レイヤー名変更"),
            self.timeline.rename_selected_layer,
        )
        self.file_edit_actions = [
            (tr("新規作成"), self.a_new),
            (tr("プロジェクトを開く"), self.a_open_project),
            (tr("カットフォルダーを開く"), self.a_open_cut_folder),
            (tr("画像を読み込む"), self.a_import_images),
            (tr("画像フォルダーを読み込む"), self.a_import_folder),
            (tr("上書き保存"), self.a_save_project),
            (tr("名前を付けて保存"), self.a_save_project_as),
            (tr("現在のコマをPNG書き出し"), self.a_save),
            (tr("現在のコマをTGA書き出し"), self.a_save_tga),
            (tr("連番PNG＋CSV書き出し"), self.a_export_png_seq),
            (tr("連番TGA＋CSV書き出し"), self.a_export_tga_seq),
            (tr("カットフォルダーへ書き出し"), self.a_export_cut_folder),
            (tr("MP4書き出し"), self.a_export_mp4),
            (tr("元に戻す"), self.a_undo),
            (tr("やり直す"), self.a_redo),
            (tr("切り取り"), self.a_cut),
            (tr("コピー"), self.a_copy),
            (tr("貼り付け"), self.a_paste),
            (tr("キャンバスサイズ変更"), self.a_resize),
            (tr("筆圧設定"), self.a_pressure),
            (tr("背景以外を黒シルエット表示"), self.a_silhouette),
            (tr("選択色だけ表示"), self.a_isolate_color),
            (tr("特定色表示を解除"), self.a_clear_color_filter),
            (tr("ゴミ取り"), self.a_remove_dust),
        ]
        self.tool_command_actions = [
            (tr("描画色：メインを選択"), self.a_select_main),
            (tr("描画色：サブを選択"), self.a_select_sub),
            (tr("描画色：背景色を選択"), self.a_select_transparent),
            (tr("描画色と背景色を切り替え"), self.a_toggle_draw_background),
            (tr("メイン色とサブ色を切り替え"), self.a_swap_main_sub),
            (tr("背景色の表示色を変更"), self.a_choose_background),
            ("MainLineRepaint", self.a_mainline_repaint),
            (tr("選択範囲：自由変形"), self.a_selection_transform),
            (tr("選択範囲：拡大縮小"), self.a_selection_scale),
            (tr("選択範囲：メッシュ変形"), self.a_selection_mesh),
            (tr("選択範囲を解除"), self.a_selection_clear),
            (tr("変形：左へ90°回転"), self.a_transform_rotate_left),
            (tr("変形：右へ90°回転"), self.a_transform_rotate_right),
            (tr("メッシュ変形：格子数を変更"), self.a_transform_mesh_grid),
            (tr("変形を確定"), self.a_transform_commit),
            (tr("変形をキャンセル"), self.a_transform_cancel),
            (tr("バケツ：含み塗り ON/OFF"), self.a_bucket_include_sub),
            (tr("バケツ：隙間閉じ ON/OFF"), self.a_bucket_close_gap),
            (tr("バケツ：串刺し塗り ON/OFF"), self.a_bucket_all_frames),
            (tr("ゴミ取り：すべてのコマ ON/OFF"), self.a_dust_all_frames),
            (tr("ゴミ取り／塗り抜けを適用"), self.a_dust_apply),
            (tr("選択変形：すべてのコマ ON/OFF"), self.a_selection_all_frames),
        ]
        self.timeline_actions = [
            (tr("コマ数を1つ増やす"), self.a_tl_add_exposure),
            (tr("コマを削除"), self.a_tl_delete_frame),
            (tr("タイムリマップを貼り付け"), self.a_tl_paste_time_remap),
            (tr("前のフレーム"), self.a_tl_previous),
            (tr("前のコマ"), self.a_tl_previous_key),
            (tr("再生／停止"), self.a_tl_play),
            (tr("次のコマ"), self.a_tl_next_key),
            (tr("次のフレーム"), self.a_tl_next),
            (tr("オニオンスキン設定"), self.a_tl_onion),
            (tr("レイヤー追加"), self.a_tl_layer_add),
            (tr("レイヤー削除"), self.a_tl_layer_delete),
            (tr("レイヤー名変更"), self.a_tl_layer_rename),
        ]
    def build_menu(self):
        f=self.menuBar().addMenu(tr("ファイル"))
        f.addAction(self.a_new)
        f.addAction(self.a_open_project)
        f.addAction(self.a_open_cut_folder)
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
        f.addAction(self.a_export_cut_folder)
        f.addAction(self.a_export_png_seq)
        f.addAction(self.a_export_tga_seq)
        f.addAction(self.a_export_xdts)
        f.addAction(self.a_export_psd)
        f.addAction(self.a_export_mp4)
        e=self.menuBar().addMenu(tr("編集"));e.addAction(self.a_undo);e.addAction(self.a_redo);e.addSeparator();e.addAction(self.a_cut);e.addAction(self.a_copy);e.addAction(self.a_paste);e.addSeparator();e.addAction(self.a_silhouette);e.addAction(self.a_isolate_color);e.addAction(self.a_clear_color_filter);e.addAction(self.a_swap_main_sub);e.addAction(self.a_remove_dust);e.addSeparator();e.addAction(self.a_resize);e.addAction(self.a_shortcuts);e.addAction(self.a_pressure)
        selection_menu=self.menuBar().addMenu(tr("選択範囲"))
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
        a=self.menuBar().addMenu(tr("アニメーション"))
        a.addAction(self.a_prev)
        a.addAction(self.a_next)
        a.addSeparator()
        a.addAction(self.a_tl_previous_key)
        a.addAction(self.a_tl_next_key)
        a.addSeparator()
        a.addAction(self.a_tl_paste_time_remap)
        self._build_view_menu()
    def _build_view_menu(self):
        view_menu = self.menuBar().addMenu(tr("表示"))
        theme_menu = view_menu.addMenu(tr("テーマ"))
        group = QActionGroup(self)
        group.setExclusive(True)
        self.theme_actions = {}
        labels = {"light": tr("ライト（明るい）"), "dark": tr("ダーク（暗い）")}
        active = theme.current_theme()
        for name in theme.available_themes():
            action = QAction(labels.get(name, name), self)
            action.setCheckable(True)
            action.setChecked(name == active)
            action.triggered.connect(lambda _=False, n=name: self.set_theme(n))
            group.addAction(action)
            theme_menu.addAction(action)
            self.theme_actions[name] = action
        self._build_language_menu(view_menu)
        accent_menu = view_menu.addMenu(tr("アクセントカラー"))
        for label, hexval in theme.accent_presets():
            act = QAction(f"{label}", self)
            act.triggered.connect(
                lambda _=False, h=hexval: self.colors.set_accent(h)
            )
            accent_menu.addAction(act)
        accent_menu.addSeparator()
        custom = QAction(tr("カスタム…"), self)
        custom.triggered.connect(self.colors.choose_accent_color)
        accent_menu.addAction(custom)
        view_menu.addSeparator()
        self.subview_action = QAction(tr("サブビュー"), self)
        self.subview_action.setCheckable(True)
        self.subview_action.setChecked(True)
        self.subview_action.triggered.connect(self._set_subview_visible)
        view_menu.addAction(self.subview_action)
        self.color_chart_action = QAction(tr("カラーチャート"), self)
        self.color_chart_action.setCheckable(True)
        self.color_chart_action.setChecked(False)
        self.color_chart_action.triggered.connect(
            self._set_color_chart_visible
        )
        view_menu.addAction(self.color_chart_action)

    def _build_language_menu(self, view_menu):
        """Language picker. Qt resolves ``tr()`` when a widget is built, so the
        whole UI would have to be torn down to retranslate live; the choice is
        persisted and applied on the next start instead."""
        menu = view_menu.addMenu(tr("言語 / Language"))
        group = QActionGroup(self)
        group.setExclusive(True)
        current = i18n.preferred_language()
        entries = [(i18n.SYSTEM, tr("システムに合わせる"))]
        entries += list(i18n.available_languages().items())
        self.language_actions = {}
        for code, label in entries:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(code == current)
            action.triggered.connect(lambda _=False, c=code: self._choose_language(c))
            group.addAction(action)
            menu.addAction(action)
            self.language_actions[code] = action

    def _choose_language(self, code):
        if code == i18n.preferred_language():
            return
        i18n.set_preferred_language(code)
        QMessageBox.information(
            self,
            tr("言語 / Language"),
            tr("次回の起動から新しい言語で表示されます。"),
        )

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
    def _apply_canvas_bar_icons(self):
        for button, name in getattr(self, "_canvas_bar_icons", {}).items():
            button.setIcon(icons.icon(name))
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
        self._apply_canvas_bar_icons()
        if hasattr(self.palette, "apply_theme"):
            self.palette.apply_theme()
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
        b100=QPushButton()
        b100.setToolTip(tr("100%表示"))
        bfit=QPushButton()
        bfit.setToolTip(tr("全体を表示"))
        b0=QPushButton()
        b0.setToolTip(tr("回転を0°に戻す"))
        # 表示操作はアイコンボタンにして、キャンバスの下を細い1本のバーにする。
        self._canvas_bar_icons = {b100: "zoom_actual", bfit: "fit", b0: "rotate_reset"}
        for button in self._canvas_bar_icons:
            button.setProperty("iconButton", True)
            button.setFixedSize(26, 24)
            button.setIconSize(QSize(16, 16))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._apply_canvas_bar_icons()
        for label in (self.zoom_label, self.rot_label):
            label.setMinimumWidth(38)
        bar.setContentsMargins(8, 2, 8, 2)
        bar.setSpacing(4)
        zoom_title=QLabel(tr("拡大"))
        rot_title=QLabel(tr("回転"))
        for w in (zoom_title,self.zoom,self.zoom_label,b100,bfit):
            bar.addWidget(w)
        bar.addSpacing(16)
        for w in (rot_title,self.rot,self.rot_label,b0):
            bar.addWidget(w)
        cv.addLayout(bar)
        self.dock_manager = QtAds.CDockManager(self)
        self._customize_docking_hover()
        self._setup_split_drop_overlay()
        self.dock_manager.floatingWidgetCreated.connect(
            self._configure_floating_window
        )
        self.central_dock = QtAds.CDockWidget(self.dock_manager, tr("キャンバス"))
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

        # 描画色（メイン/サブ/背景）はツールバー最下部のスウォッチに加え、
        # カラーサークルのドック上部（tools.drawing_color_box）にも大きく表示する。
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

        self.tool_selector_dock=QtAds.CDockWidget(self.dock_manager, tr("ツール"))
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

        self.tools_dock=QtAds.CDockWidget(self.dock_manager, tr("ツールプロパティ"))
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
        self.action_panel_dock=QtAds.CDockWidget(self.dock_manager, tr("アクション"))
        self.action_panel_dock.setObjectName("actionPanelDock")
        self.action_panel_dock.setWidget(
            self.action_panel_scroll, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        self.dock_manager.addDockWidget(
            QtAds.BottomDockWidgetArea, self.action_panel_dock, tools_area
        )

        self.color_wheel_dock=QtAds.CDockWidget(self.dock_manager, tr("カラーサークル"))
        self.color_wheel_dock.setObjectName("colorWheelDock")
        self.color_wheel_dock.setWidget(
            self.color_wheel_scroll, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        # カラーサークルを右エリアのアンカーにする（旧・描画色ドックの位置）。
        drawing_area = self.dock_manager.addDockWidget(
            QtAds.RightDockWidgetArea, self.color_wheel_dock
        )
        wheel_area = drawing_area

        self.color_slider_dock=QtAds.CDockWidget(self.dock_manager, tr("カラースライダー"))
        self.color_slider_dock.setObjectName("colorSliderDock")
        self.color_slider_dock.setWidget(
            self.color_slider_scroll, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        self.dock_manager.addDockWidget(
            QtAds.BottomDockWidgetArea, self.color_slider_dock, wheel_area
        )

        self.palette_dock=QtAds.CDockWidget(self.dock_manager, tr("使用色"))
        self.palette_dock.setObjectName("paletteDock")
        self.palette_dock.setWidget(
            self.palette_scroll, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        palette_area = self.dock_manager.addDockWidget(
            QtAds.BottomDockWidgetArea, self.palette_dock, drawing_area
        )
        # タイトルバーの×の左側に「？」ヘルプボタンを置き、
        # 常時表示していた使い方の説明をそこへ収める。
        self.palette_help_action = QAction("?", self)
        self.palette_help_action.setToolTip(tr("使用色パネルの使い方を表示します。"))
        self.palette_help_action.triggered.connect(self.palette.show_help)
        self.palette_dock.setTitleBarActions([self.palette_help_action])

        self.history_dock=QtAds.CDockWidget(self.dock_manager, tr("ヒストリー"))
        self.history_dock.setObjectName("historyDock")
        self.history_dock.setWidget(
            self.history_scroll, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        # 使用色パネルと同じ場所にタブとして重ねる。
        self.dock_manager.addDockWidget(
            QtAds.CenterDockWidgetArea, self.history_dock, palette_area
        )
        self.history_help_action = QAction("?", self)
        self.history_help_action.setToolTip(tr("ヒストリーパネルの使い方を表示します。"))
        self.history_help_action.triggered.connect(self.history_panel.show_help)
        self.history_dock.setTitleBarActions([self.history_help_action])
        # 使用色をヒストリーより前のタブとして、起動時の前面にする。
        palette_area.setCurrentDockWidget(self.palette_dock)

        self.color_chart_dock=QtAds.CDockWidget(
            self.dock_manager, tr("カラーチャート")
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

        self.subview_dock=QtAds.CDockWidget(self.dock_manager, tr("サブビュー"))
        self.subview_dock.setObjectName("subviewDock")
        self.subview_dock.setWidget(
            self.subview, QtAds.CDockWidget.eInsertMode.ForceNoScrollArea
        )
        self.dock_manager.addDockWidget(
            QtAds.RightDockWidgetArea, self.subview_dock
        )

        self.timeline_dock=QtAds.CDockWidget(self.dock_manager, tr("タイムライン"))
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
        self.workspace.build_menu()
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

        panel_menu=self.menuBar().addMenu(tr("パネル"))
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

        help_menu=self.menuBar().addMenu(tr("ヘルプ"))
        a_check_update=QAction(tr("更新を確認…"), self)
        a_check_update.triggered.connect(self.check_for_updates_interactive)
        help_menu.addAction(a_check_update)
        help_menu.addSeparator()
        a_about=QAction(tr("バージョン情報…"), self)
        a_about.triggered.connect(self.show_about_dialog)
        help_menu.addAction(a_about)

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
        self.tools.fill_opacity.valueChanged.connect(
            lambda value: setattr(self.canvas, "fill_opacity", value / 100)
        )
        self.tools.colorModeChanged.connect(self.colors.set_color_mode)
        self.tools.colorChanged.connect(self.colors.set_color_value)
        self.subview.colorPicked.connect(self.colors.apply_sampled_color)
        self.color_chart.colorPicked.connect(self.colors.apply_sampled_color)
        self.color_chart.chartChanged.connect(self.color_chart_ops.on_edited)
        self.color_chart.captureRequested.connect(self.color_chart_ops.capture)
        self.color_chart.applyRequested.connect(self.color_chart_ops.apply)
        self.color_chart.saveRequested.connect(self.color_chart_ops.save_pmag)
        self.color_chart.loadRequested.connect(self.color_chart_ops.load_pmag)
        self.subview.set_color_provider(
            lambda: (
                self.canvas.sub_color
                if self.canvas.color_mode == "sub"
                else self.canvas.main_color
            )
        )
        self.subview.colorPicked.connect(
            lambda color: self.status(
                tr("サブビューから {color} を取得しました").format(
                    color=color.name().upper()
                ),
                "success",
                2500,
            )
        )
        # Keep the tool-bar drawing-colour swatch in sync with the panel.
        self.tool_selector.colorModeRequested.connect(self.colors.set_color_mode)
        self.tool_selector.backgroundColorRequested.connect(
            self.colors.choose_background_color
        )
        self.tools.colorModeChanged.connect(
            lambda _m=None: self.colors._sync_tool_selector_swatch()
        )
        self.tools.colorChanged.connect(
            lambda *_a: self.colors._sync_tool_selector_swatch()
        )
        self.tools.main_btn.colorPicked.connect(
            lambda color: self.colors.apply_sampled_color_to_mode("main", color)
        )
        self.tools.sub_btn.colorPicked.connect(
            lambda color: self.colors.apply_sampled_color_to_mode("sub", color)
        )
        self.tools.meshCommitRequested.connect(self.canvas.commit_mesh)
        self.tools.meshCancelRequested.connect(self.canvas.cancel_mesh)
        self.tools.selectionTransformRequested.connect(
            lambda: self.line_ops.start_wire_transform("free")
        )
        self.tools.selectionScaleRequested.connect(
            lambda: self.line_ops.start_wire_transform("scale")
        )
        self.tools.selectionMeshRequested.connect(
            lambda: self.line_ops.start_wire_transform("mesh")
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
            self.tween.commit_transform_or_tween
        )
        self.tools.selectionCancelRequested.connect(
            self.tween.cancel_transform_or_tween
        )
        self.tools.flipLayerRequested.connect(self.canvas.flip_active_layer)
        self.tools.swapMainSubRequested.connect(self.line_ops.swap_main_sub)
        self.tools.resetMainSubRequested.connect(self.line_ops.reset_main_sub)
        self.tools.removeDustRequested.connect(self.line_ops.remove_dust_fill_surrounding)
        self.tools.backgroundColorRequested.connect(self.colors.choose_background_color)

        self.timeline.frameSelected.connect(self.timeline_ops.select_exposure)
        self.timeline.addFrameRequested.connect(
            self.canvas.add_frame
        )
        self.timeline.extendExposureRequested.connect(
            lambda row, key, exposure:
                self.timeline_ops.resize_exposure(
                    row,
                    key,
                    key + max(1, exposure),
                    "right",
                )
        )
        self.timeline.deleteFrameRequested.connect(
            self.timeline_ops.delete_frame
        )
        self.timeline.previousRequested.connect(self.timeline_ops.previous_frame)
        self.timeline.nextRequested.connect(self.timeline_ops.next_frame)
        self.timeline.previousKeyRequested.connect(self.timeline_ops.previous_key)
        self.timeline.nextKeyRequested.connect(self.timeline_ops.next_key)
        self.timeline.durationChanged.connect(self.canvas.set_duration)
        self.timeline.cellMoveRequested.connect(self.timeline_ops.move_cell)
        self.timeline.cellCopyRequested.connect(self.timeline_ops.copy_cell)
        self.timeline.sequenceRecallRequested.connect(self.timeline_ops.recall_sequence_number)
        self.timeline.multiCellMoveRequested.connect(
            self.timeline_ops.move_selection
        )
        self.timeline.timelineModeChanged.connect(
            self.timeline_ops.set_mode
        )
        self.timeline.normalizeNumbersRequested.connect(
            self.timeline_ops.normalize_numbers
        )
        self.timeline.blankFrameRequested.connect(
            self.timeline_ops.create_blank_key
        )
        self.timeline.exposureResizeRequested.connect(self.timeline_ops.resize_exposure)
        self.timeline.tweenRequested.connect(
            lambda row, column: self.tween.enable(row, column, "free")
        )
        self.timeline.tweenMeshRequested.connect(
            lambda row, column: self.tween.enable(row, column, "mesh")
        )
        self.timeline.tweenCancelRequested.connect(
            self.tween.cancel_transform_or_tween
        )
        self.timeline.onionPopupToggled.connect(
            self.onion.toggle_settings_popup
        )
        self.timeline.onionChanged.connect(self.onion.set_enabled)
        self.timeline.timeRemapPasteRequested.connect(
            self.time_remap.show_paste_dialog
        )
        self.timeline.timeRemapFileDropped.connect(
            self.time_remap.open_dropped
        )
        self.timeline.playRequested.connect(self.play)

        self.canvas.changed.connect(self.refresh_ui)
        self.canvas.selectionChanged.connect(self.refresh_selection)
        self.canvas.selectionCleared.connect(
            self.palette._clear_used_color_selection
        )
        self.canvas.cellChanged.connect(self._on_canvas_cell_changed)
        self.canvas.colorUsed.connect(self.palette.add_color)

        self.timeline.layerSelected.connect(self.layers.on_selected)
        self.timeline.layerVisibilityChanged.connect(self.layers.set_visibility_row)
        self.timeline.layerOpacityChanged.connect(self.layers.set_opacity_row)
        self.timeline.layer_opacity_slider.sliderPressed.connect(
            self.canvas.push_doc_undo
        )
        self.timeline.layerNameChanged.connect(self.layers.rename_row)
        self.timeline.layerMoveRequested.connect(self.layers.move_row)
        self.timeline.addLayerRequested.connect(self.layers.add_fast)
        self.timeline.deleteLayerRequested.connect(self.canvas.delete_layer)
        self.timeline.duplicateLayersRequested.connect(self.layers.duplicate_rows)
        self.timeline.mergeLayersRequested.connect(self.layers.merge_rows)
        self.timeline.deleteLayersRequested.connect(self.layers.delete_rows)
        self.timeline.toggleDraftLayersRequested.connect(
            self.layers.set_draft_rows
        )

        self.canvas.imagesDropped.connect(self.import_dropped_images)
        self.canvas.projectDropped.connect(self.project.open_dropped)
        self.canvas.timeRemapDropped.connect(self.time_remap.open_dropped)
        self.canvas.clipAnimationDropped.connect(self.importer.clip_animation)
        self.canvas.colorSampled.connect(self.colors.apply_sampled_color)
        self.canvas.status_message.connect(
            lambda message: self.statusBar().showMessage(message, 2500)
        )
        self.canvas.viewChanged.connect(self.sync_canvas_view_controls)
        self.canvas.onionInteractionChanged.connect(
            self.onion.sync_browser_from_canvas
        )
        self.canvas.onionInteractionFinished.connect(
            self.onion.finish_browser_interaction
        )

        self.palette.isolateColorClicked.connect(self.used_color.apply_palette_isolate_color)
        self.palette.mainColorRequested.connect(
            lambda color: self.colors.apply_sampled_color_to_mode("main", color)
        )
        self.palette.sourceScreenColorPicked.connect(self.colors.apply_sampled_color)
        self.palette.applyReplacementRequested.connect(
            self.used_color.apply_palette_replacements
        )
        self.palette.previewGroupsChanged.connect(self.colors.set_preview_color_groups)
        self.palette.mergeColorsRequested.connect(self.used_color.apply_palette_merge)
        self.palette.deleteColorsRequested.connect(
            self.used_color.apply_palette_delete
        )
        self.palette.adjustLineThicknessRequested.connect(
            self.colors.adjust_parent_line_thickness
        )
        self.palette.focusColorRequested.connect(self.colors.focus_used_color)
        self.palette.clearIsolateRequested.connect(
            self.colors.clear_selected_color_filter
        )
        self.palette.maskColorsChanged.connect(self.colors.set_mask_colors)
        self.palette.selectedColorsChanged.connect(self.colors.set_selected_used_colors)
        self.palette.visibleColorsChanged.connect(self.colors.set_visible_colors)
        self.palette.historyStatePush.connect(self.used_color.push_palette_history)
        self.history_panel.jumpRequested.connect(self.used_color.jump_history)
        self.history_panel.branchSwitchRequested.connect(
            self.used_color.switch_history_branch
        )
        # 編集のたびにヒストリー一覧を更新する。
        self.canvas.changed.connect(self.used_color.refresh_history_panel)
        self.canvas.cellChanged.connect(
            lambda *_args: self.used_color.refresh_history_panel()
        )


    def show_about_dialog(self):
        """Show version, copyright and the license notices.

        Crediting the LGPL-licensed Qt/PySide6 and Qt-Advanced-Docking-System
        components is a distribution requirement (LGPLv3 4a/4c), not
        decoration -- keep this reachable from the menu. See
        THIRD_PARTY_LICENSES.md for the full compliance notes.
        """
        repo_url = f"https://github.com/{GITHUB_REPO}"
        QMessageBox.about(
            self,
            tr("{app} について").format(app=APP_NAME),
            tr("""<h3>{app}</h3>
<p>Copyright &copy; 2026 PaintMaskAnimator contributors</p>
<p>本ソフトウェアは <b>Apache License 2.0</b> のもとで配布されています。
商用・非商用を問わず、自由に利用・改変・再配布できます。</p>
<p>本ソフトウェアは「現状有姿」で提供され、明示黙示を問わず<b>いかなる保証もありません</b>。
詳細はライセンス全文を参照してください。</p>
<p>ライセンス全文: <a href="https://www.apache.org/licenses/LICENSE-2.0">Apache License 2.0</a><br>
ソースコード: <a href="{repo}">{repo}</a></p>
<p><b>サードパーティコンポーネント</b><br>
Qt for Python (PySide6) &mdash; LGPLv3<br>
Qt Advanced Docking System &mdash; LGPL-2.1<br>
NumPy &mdash; BSD-3-Clause / Pillow &mdash; MIT-CMU / psd-tools &mdash; MIT</p>
<p>これらの LGPL ライブラリは差し替え可能な形で同梱されています。ライセンス全文と
対応ソースの入手先は、配布物内の <tt>THIRD_PARTY_LICENSES.md</tt> を参照してください。</p>""").format(
                app=APP_DISPLAY_NAME, repo=repo_url
            ),
        )
