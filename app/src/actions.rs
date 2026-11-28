//! 操作の一覧（Qt 版の main_window_ui_build.build_actions の QAction）。
//!
//! メニュー（menu.rs）・ショートカット（キーの割り当てと保存）・画面のキー入力（shortcuts.js）は
//! この一覧を正本にする。
//!
//! - `id` は Qt の属性名（`a_new` など）とツールの `tool:<id>`。保存の鍵（設定の
//!   `shortcuts.actions`）も Qt と同じ（main_window_shortcuts.ShortcutStore）。
//! - `keys` は Qt の既定のキー（QKeySequence の PortableText）。
//! - `wired` が偽の操作は、まだ移していない機能。メニューから選ぶと `menu-action` の事件に
//!   `pending: true` を付けて送り、画面は今は何もしない（状況の帯に知らせるだけ）。

use serde::Serialize;

/// ショートカット設定のタブ（Qt の MainWindow.shortcuts の categories）。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Category {
    /// 「ファイル・編集」。
    FileEdit,
    /// 「ツール」。
    Tools,
    /// 「キャンバス操作」（押している間だけ効く）。
    CanvasOperation,
    /// 「ツールコマンド」。
    ToolCommand,
    /// 「タイムライン」。
    Timeline,
}

impl Category {
    pub const ALL: [Category; 5] = [
        Category::FileEdit,
        Category::Tools,
        Category::CanvasOperation,
        Category::ToolCommand,
        Category::Timeline,
    ];

    /// タブの名前（Qt と同じ日本語）。
    pub fn title(self) -> &'static str {
        match self {
            Category::FileEdit => "ファイル・編集",
            Category::Tools => "ツール",
            Category::CanvasOperation => "キャンバス操作",
            Category::ToolCommand => "ツールコマンド",
            Category::Timeline => "タイムライン",
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize)]
pub struct Action {
    pub id: &'static str,
    /// メニュー・QAction の文字（Qt と同じ日本語）。
    pub label: &'static str,
    /// ショートカット設定に並べる名前とタブ。None はショートカット設定に出ない操作。
    pub shortcut: Option<(Category, &'static str)>,
    /// 既定のキー（Qt の PortableText）。
    pub keys: &'static [&'static str],
    /// 押している間だけ効く操作（Qt の holdOperation）。
    pub hold: bool,
    /// 切り替えの操作（Qt の setCheckable）。
    pub checkable: bool,
    /// もう動く操作か。
    pub wired: bool,
}

const fn a(id: &'static str, label: &'static str, keys: &'static [&'static str]) -> Action {
    Action {
        id,
        label,
        shortcut: None,
        keys,
        hold: false,
        checkable: false,
        wired: false,
    }
}

impl Action {
    const fn tab(mut self, category: Category, name: &'static str) -> Self {
        self.shortcut = Some((category, name));
        self
    }
    const fn wired(mut self) -> Self {
        self.wired = true;
        self
    }
    const fn checkable(mut self) -> Self {
        self.checkable = true;
        self
    }
    const fn hold(mut self) -> Self {
        self.hold = true;
        self
    }
}

use Category::*;

/// 「拡大縮小（押している間）」の既定（constants.HOLD_ZOOM_SHORTCUT）。
#[cfg(target_os = "macos")]
const HOLD_ZOOM: &[&str] = &["Alt+Space"];
#[cfg(not(target_os = "macos"))]
const HOLD_ZOOM: &[&str] = &["Ctrl+Space"];

/// 全部の操作。ショートカット設定の各タブはこの順に並ぶ（Qt の各一覧の順に合わせた）。
pub const ACTIONS: &[Action] = &[
    // ---- ファイル・編集（file_edit_actions の順） ----
    a("a_new", "新規作成…", &["Ctrl+N"])
        .tab(FileEdit, "新規作成")
        .wired(),
    a("a_open_project", "プロジェクトを開く…", &["Ctrl+O"])
        .tab(FileEdit, "プロジェクトを開く")
        .wired(),
    a("a_open_cut_folder", "カットフォルダーを開く…", &[])
        .tab(FileEdit, "カットフォルダーを開く")
        .wired(), // 【Group B】
    a("a_import_images", "画像を読み込む…", &[]).tab(FileEdit, "画像を読み込む"),
    a("a_import_folder", "画像フォルダーを読み込む…", &[])
        .tab(FileEdit, "画像フォルダーを読み込む"),
    a("a_save_project", "上書き保存", &["Ctrl+S"])
        .tab(FileEdit, "上書き保存")
        .wired(),
    a("a_save_project_as", "名前を付けて保存…", &["Ctrl+Shift+S"])
        .tab(FileEdit, "名前を付けて保存")
        .wired(),
    a("a_save", "現在のコマをPNG書き出し…", &[]).tab(FileEdit, "現在のコマをPNG書き出し"),
    a("a_save_tga", "現在のコマをTGA書き出し…", &[]).tab(FileEdit, "現在のコマをTGA書き出し"),
    a("a_export_png_seq", "連番PNG＋CSV書き出し…", &[]).tab(FileEdit, "連番PNG＋CSV書き出し"),
    a("a_export_tga_seq", "連番TGA＋CSV書き出し…", &[]).tab(FileEdit, "連番TGA＋CSV書き出し"),
    a("a_export_cut_folder", "カットフォルダーへ書き出し…", &[])
        .tab(FileEdit, "カットフォルダーへ書き出し"),
    a("a_export_shadow", "影素材を書き出し…", &[]).tab(FileEdit, "影素材を書き出し"),
    a("a_export_mp4", "MP4書き出し", &[]).tab(FileEdit, "MP4書き出し"),
    a("a_undo", "元に戻す", &["Ctrl+Z"])
        .tab(FileEdit, "元に戻す")
        .wired(),
    a("a_redo", "やり直す", &["Ctrl+Y", "Ctrl+Shift+Z"])
        .tab(FileEdit, "やり直す")
        .wired(),
    a("a_cut", "切り取り", &["Ctrl+X"])
        .tab(FileEdit, "切り取り")
        .wired(), // 【Group B】
    a("a_copy", "コピー", &["Ctrl+C"])
        .tab(FileEdit, "コピー")
        .wired(), // 【Group B】
    a("a_paste", "貼り付け", &["Ctrl+V"])
        .tab(FileEdit, "貼り付け")
        .wired(), // 【Group B】
    a("a_resize", "キャンバスサイズの変更…", &[])
        .tab(FileEdit, "キャンバスサイズ変更")
        .wired(), // 【Group B】
    a("a_pressure", "筆圧設定…", &[]).tab(FileEdit, "筆圧設定"),
    a("a_memo", "メモを開く", &[])
        .tab(FileEdit, "メモを開く")
        .wired(),
    a("a_preferences", "環境設定…", &["Ctrl+,"])
        .tab(FileEdit, "環境設定")
        .wired(),
    // 【キャンバスの見え方】canvas_view.js が pma-action で受ける（ここから a_channel_alpha まで）。
    a("a_silhouette", "背景以外を黒シルエット表示", &["Ctrl+B"])
        .tab(FileEdit, "背景以外を黒シルエット表示")
        .checkable()
        .wired(),
    a("a_preview_map", "隣接色分けで表示", &[])
        .tab(FileEdit, "隣接色分けで表示")
        .checkable()
        .wired(),
    a("a_exposure_toggle", "露光量の調整を切り替え", &[])
        .tab(FileEdit, "露光量の調整を切り替え")
        .wired(),
    a("a_channel_red", "赤チャンネルを表示", &[])
        .tab(FileEdit, "赤チャンネルを表示")
        .wired(),
    a("a_channel_green", "緑チャンネルを表示", &[])
        .tab(FileEdit, "緑チャンネルを表示")
        .wired(),
    a("a_channel_blue", "青チャンネルを表示", &[])
        .tab(FileEdit, "青チャンネルを表示")
        .wired(),
    a("a_channel_alpha", "アルファチャンネルを表示", &[])
        .tab(FileEdit, "アルファチャンネルを表示")
        .wired(),
    a("a_isolate_color", "選択色だけ表示", &[]).tab(FileEdit, "選択色だけ表示"),
    a("a_clear_color_filter", "特定色表示を解除", &[]).tab(FileEdit, "特定色表示を解除"),
    a("a_remove_dust", "ゴミ取り／塗り抜け…", &[]).tab(FileEdit, "ゴミ取り"),
    a("a_auto_paint", "自動で塗る…", &[]).tab(FileEdit, "自動で塗る"),
    a("a_keep_initial_state", "今の絵を初期状態として保持", &[])
        .tab(FileEdit, "今の絵を初期状態として保持")
        .wired(), // 【Group B】
    a(
        "a_import_initial_state",
        "線画カットフォルダーを初期状態として読み込む…",
        &[],
    )
    .tab(FileEdit, "線画カットフォルダーを初期状態として読み込む"),
    a(
        "a_import_clip_initial_state",
        "CLIPの線画を初期状態として読み込む…",
        &[],
    )
    .tab(FileEdit, "CLIPの線画を初期状態として読み込む"),
    a("a_clear_initial_state", "保持した初期状態を消す", &[])
        .tab(FileEdit, "保持した初期状態を消す")
        .wired(), // 【Group B】
    a("a_reference_check", "見本との違いチェック…", &[]).tab(FileEdit, "見本との違いチェック"),
    // ---- メニューだけの操作（ショートカット設定には出ない） ----
    a("a_import_genga", "原画を読み込む…", &[]),
    a(
        "a_import_images_raw",
        "変換せず読み込む（下書きレイヤー）…",
        &[],
    ),
    a("a_import_psd", "PSDを読み込む…", &[]),
    a("a_import_clip", "CLIP STUDIOアニメーションを読み込む…", &[]),
    a("a_export_xdts", "XDTSタイムシートを書き出す…", &[]),
    a("a_export_psd", "PSDを書き出す…", &[]),
    a("a_binarize_layer", "2値化…", &[]).wired(), // 【Group B】
    a("a_reconvert_layer", "元のファイルから再変換…", &[]).wired(), // 【Group B】
    a("a_tidy_lines", "線整え…", &[]).wired(),    // 【Group B】
    a("a_color_chart", "カラーチャート…", &[]),
    a("a_shortcuts", "ショートカット設定…", &[]).wired(),
    a("a_prev", "前のフレーム", &["1"]).wired(),
    a("a_next", "次のフレーム", &["2"]).wired(),
    // ---- ツール（ToolPanel.TOOLS の順。キーは build_actions の defaults） ----
    a("tool:brush", "ツール：ブラシ", &["P"]).tab(Tools, "ツール：ブラシ"),
    a("tool:shape", "ツール：図形", &["O"]).tab(Tools, "ツール：図形"),
    a("tool:bucket", "ツール：バケツ", &["G"]).tab(Tools, "ツール：バケツ"),
    a("tool:lasso_fill", "ツール：投げ縄塗り", &["F"]).tab(Tools, "ツール：投げ縄塗り"),
    a("tool:replace_color", "ツール：色置換", &[]).tab(Tools, "ツール：色置換"),
    a("tool:skewer_fill", "ツール：串刺し塗り", &[]).tab(Tools, "ツール：串刺し塗り"),
    a("tool:lasso", "ツール：投げ縄選択", &["L"]).tab(Tools, "ツール：投げ縄選択"),
    a("tool:rect_select", "ツール：長方形選択", &["R"]).tab(Tools, "ツール：長方形選択"),
    a("tool:auto_select", "ツール：自動選択", &["W"]).tab(Tools, "ツール：自動選択"),
    a("tool:eyedropper", "ツール：スポイト", &["I"]).tab(Tools, "ツール：スポイト"),
    a("tool:dust", "ツール：ゴミ取り", &["D"]).tab(Tools, "ツール：ゴミ取り"),
    // ---- キャンバス操作（押している間） ----
    a("a_hold_hand", "ハンド（押している間）", &["Space"])
        .tab(CanvasOperation, "ハンド")
        .hold()
        .wired(),
    a("a_hold_zoom", "拡大縮小（押している間）", HOLD_ZOOM)
        .tab(CanvasOperation, "拡大縮小")
        .hold(),
    a("a_hold_rotate", "回転（押している間）", &["Shift+Space"])
        .tab(CanvasOperation, "回転")
        .hold(),
    a("a_hold_eyedropper", "スポイト（押している間）", &["Alt"])
        .tab(CanvasOperation, "スポイト")
        .hold(),
    // ---- ツールコマンド（tool_command_actions の順） ----
    a("a_select_main", "描画色：メインを選択", &[]).tab(ToolCommand, "描画色：メインを選択"),
    a("a_select_sub", "描画色：サブを選択", &[]).tab(ToolCommand, "描画色：サブを選択"),
    a("a_select_transparent", "描画色：背景色を選択", &["E"])
        .tab(ToolCommand, "描画色：背景色を選択"),
    a(
        "a_toggle_draw_background",
        "描画色と背景色を切り替え",
        &["C"],
    )
    .tab(ToolCommand, "描画色と背景色を切り替え"),
    a("a_swap_main_sub", "メイン色とサブ色を切り替え", &["X"])
        .tab(ToolCommand, "メイン色とサブ色を切り替え"),
    a("a_choose_background", "背景色の表示色を変更", &[]).tab(ToolCommand, "背景色の表示色を変更"),
    a("a_mainline_repaint", "MainLineRepaint", &[])
        .tab(ToolCommand, "MainLineRepaint")
        .wired(),
    a("a_selection_transform", "選択範囲：自由変形", &["Ctrl+T"])
        .tab(ToolCommand, "選択範囲：自由変形"),
    a("a_selection_scale", "選択範囲：拡大縮小", &[]).tab(ToolCommand, "選択範囲：拡大縮小"),
    a("a_selection_mesh", "選択範囲：メッシュ変形", &[]).tab(ToolCommand, "選択範囲：メッシュ変形"),
    a("a_selection_all", "すべてを選択", &["Ctrl+A"]).tab(ToolCommand, "すべてを選択"),
    a(
        "a_selection_clear",
        "選択範囲を解除",
        &["Ctrl+D", "Ctrl+Shift+A"],
    )
    .tab(ToolCommand, "選択範囲を解除"),
    a("a_selection_invert", "選択範囲を反転", &["Ctrl+Shift+I"]).tab(ToolCommand, "選択範囲を反転"),
    a(
        "a_selection_delete",
        "選択範囲の中を消去",
        &["Delete", "Backspace"],
    )
    .tab(ToolCommand, "選択範囲の中を消去"),
    a("a_view_zoom_in", "表示：拡大", &["Ctrl+=", "Ctrl++"])
        .tab(ToolCommand, "表示：拡大")
        .wired(),
    a("a_view_zoom_out", "表示：縮小", &["Ctrl+-"])
        .tab(ToolCommand, "表示：縮小")
        .wired(),
    a("a_view_fit", "表示：全体を表示", &["Ctrl+0"])
        .tab(ToolCommand, "表示：全体を表示")
        .wired(),
    a("a_brush_smaller", "ブラシサイズを小さく", &["["]).tab(ToolCommand, "ブラシサイズを小さく"),
    a("a_brush_larger", "ブラシサイズを大きく", &["]"]).tab(ToolCommand, "ブラシサイズを大きく"),
    a("a_transform_rotate_left", "変形：左へ90°回転", &[]).tab(ToolCommand, "変形：左へ90°回転"),
    a("a_transform_rotate_right", "変形：右へ90°回転", &[]).tab(ToolCommand, "変形：右へ90°回転"),
    a("a_transform_mesh_grid", "メッシュ変形：格子数を変更…", &[])
        .tab(ToolCommand, "メッシュ変形：格子数を変更"),
    a("a_transform_commit", "変形を確定", &["Return", "Enter"]).tab(ToolCommand, "変形を確定"),
    a("a_transform_cancel", "変形をキャンセル", &[]).tab(ToolCommand, "変形をキャンセル"),
    a("a_focus_used_color", "使用色：この色を選択範囲にする", &[])
        .tab(ToolCommand, "使用色：この色を選択範囲にする"),
    a("a_bucket_include_sub", "バケツ：含み塗り ON/OFF", &[])
        .tab(ToolCommand, "バケツ：含み塗り ON/OFF"),
    a("a_bucket_close_gap", "バケツ：隙間閉じ ON/OFF", &[])
        .tab(ToolCommand, "バケツ：隙間閉じ ON/OFF"),
    a("a_bucket_adjacent", "バケツ：隣接 ON/OFF", &[]).tab(ToolCommand, "バケツ：隣接 ON/OFF"),
    a("a_bucket_extend_lines", "バケツ：線の延長 ON/OFF", &[])
        .tab(ToolCommand, "バケツ：線の延長 ON/OFF"),
    a("a_bucket_island_fill", "バケツ：塗り残し拾い ON/OFF", &[])
        .tab(ToolCommand, "バケツ：塗り残し拾い ON/OFF"),
    a(
        "a_bucket_all_frames",
        "ツール：串刺し塗り／バケツを切り替え",
        &[],
    )
    .tab(ToolCommand, "ツール：串刺し塗り／バケツを切り替え"),
    a(
        "a_bucket_replace_color",
        "ツール：色置換／バケツを切り替え",
        &[],
    )
    .tab(ToolCommand, "ツール：色置換／バケツを切り替え"),
    a("a_dust_all_frames", "ゴミ取り：すべてのコマ ON/OFF", &[])
        .tab(ToolCommand, "ゴミ取り：すべてのコマ ON/OFF"),
    a("a_dust_apply", "ゴミ取り／塗り抜けを適用", &[]).tab(ToolCommand, "ゴミ取り／塗り抜けを適用"),
    a(
        "a_selection_all_frames",
        "選択変形：すべてのコマ ON/OFF",
        &[],
    )
    .tab(ToolCommand, "選択変形：すべてのコマ ON/OFF"),
    // ---- タイムライン（timeline_actions の順） ----
    a("a_tl_add_exposure", "タイムライン：コマ数を1つ増やす", &[])
        .tab(Timeline, "コマ数を1つ増やす")
        .wired(),
    a(
        "a_tl_delete_frame",
        "タイムライン：コマを削除",
        &["Shift+E"],
    )
    .tab(Timeline, "コマを削除")
    .wired(),
    a(
        "a_tl_paste_time_remap",
        "タイムライン：タイムリマップを貼り付け",
        &[],
    )
    .tab(Timeline, "タイムリマップを貼り付け")
    .wired(),
    a(
        "a_tl_copy_time_remap",
        "タイムライン：タイムリマップをコピー",
        &[],
    )
    .tab(Timeline, "タイムリマップをコピー"),
    a("a_tl_previous", "タイムライン：前のフレーム", &[])
        .tab(Timeline, "前のフレーム")
        .wired(),
    a("a_tl_previous_key", "タイムライン：前のコマ", &["A"])
        .tab(Timeline, "前のコマ")
        .wired(),
    a("a_tl_play", "タイムライン：再生／停止", &[])
        .tab(Timeline, "再生／停止")
        .wired(),
    a("a_tl_next_key", "タイムライン：次のコマ", &["S"])
        .tab(Timeline, "次のコマ")
        .wired(),
    a("a_tl_next", "タイムライン：次のフレーム", &[])
        .tab(Timeline, "次のフレーム")
        .wired(),
    a("a_tl_onion", "タイムライン：オニオンスキン設定", &[])
        .tab(Timeline, "オニオンスキン設定")
        .wired(),
    a(
        "a_tl_layer_add",
        "タイムライン：レイヤー追加",
        &["Ctrl+Shift+N"],
    )
    .tab(Timeline, "レイヤー追加")
    .wired(),
    a("a_tl_layer_delete", "タイムライン：レイヤー削除", &[])
        .tab(Timeline, "レイヤー削除")
        .wired(),
    a("a_tl_layer_rename", "タイムライン：レイヤー名変更", &[])
        .tab(Timeline, "レイヤー名変更")
        .wired(),
];

pub fn find(id: &str) -> Option<&'static Action> {
    ACTIONS.iter().find(|a| a.id == id)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    #[test]
    fn ids_are_unique_and_default_keys_do_not_collide() {
        let mut ids = HashSet::new();
        let mut keys = HashSet::new();
        for action in ACTIONS {
            assert!(ids.insert(action.id), "同じ ID：{}", action.id);
            if action.hold {
                continue;
            }
            for key in action.keys {
                assert!(keys.insert(*key), "既定のキーが重なる：{key}");
            }
        }
    }

    #[test]
    fn labels_come_from_the_qt_sources() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../paintmaskanimator");
        let Ok(source) = std::fs::read_to_string(root.join("main_window_ui_build.py")) else {
            return;
        };
        let tools = std::fs::read_to_string(root.join("toolpanel.py")).unwrap_or_default();
        for action in ACTIONS {
            let label = action.label;
            if let Some(tool) = label
                .strip_prefix("ツール：")
                .filter(|_| action.id.starts_with("tool:"))
            {
                assert!(
                    tools.contains(&format!("tr(\"{tool}\")")),
                    "ツール名：{tool}"
                );
                continue;
            }
            assert!(
                source.contains(&format!("\"{label}\"")),
                "Qt に無い文字：{label}"
            );
            if let Some((_, name)) = action.shortcut {
                assert!(
                    source.contains(&format!("\"{name}\"")),
                    "Qt に無い名前：{name}"
                );
            }
        }
    }
}
