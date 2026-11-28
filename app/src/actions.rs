//! 操作の一覧（Qt 版の main_window_ui_build.build_actions の QAction）。
//!
//! メニュー（menu.rs）・ショートカット（キーの割り当てと保存）・画面のキー入力（shortcuts.js）は
//! この一覧を正本にする。
//!
//! - `id` は Qt の属性名（`a_new` など）とツールの `tool:<id>`。保存の鍵（設定の
//!   `shortcuts.actions`）も Qt と同じ（main_window_shortcuts.ShortcutStore）。
//! - `keys` は Qt の既定のキー（QKeySequence の PortableText）。
//! - `wired` は画面が引き受ける操作（shell.js の run、または ui/*.js が `pma-action` を
//!   preventDefault で受ける）。偽の操作はまだ移していない機能で、メニューから選ぶと
//!   `menu-action` の事件に `pending: true` を付けて送り、画面は状況の帯に知らせるだけ。
//!   食い違えば試験（`wired_flags_match_what_the_ui_claims`）が落ちる。

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
    a("a_open_cut_folder", "カットフォルダーを開く…", &[]).tab(FileEdit, "カットフォルダーを開く"),
    a("a_import_images", "画像を読み込む…", &[])
        .tab(FileEdit, "画像を読み込む")
        .wired(),
    a("a_import_folder", "画像フォルダーを読み込む…", &[])
        .tab(FileEdit, "画像フォルダーを読み込む")
        .wired(),
    a("a_save_project", "上書き保存", &["Ctrl+S"])
        .tab(FileEdit, "上書き保存")
        .wired(),
    a("a_save_project_as", "名前を付けて保存…", &["Ctrl+Shift+S"])
        .tab(FileEdit, "名前を付けて保存")
        .wired(),
    a("a_save", "現在のコマをPNG書き出し…", &[])
        .tab(FileEdit, "現在のコマをPNG書き出し")
        .wired(),
    a("a_save_tga", "現在のコマをTGA書き出し…", &[])
        .tab(FileEdit, "現在のコマをTGA書き出し")
        .wired(),
    a("a_export_png_seq", "連番PNG＋CSV書き出し…", &[])
        .tab(FileEdit, "連番PNG＋CSV書き出し")
        .wired(),
    a("a_export_tga_seq", "連番TGA＋CSV書き出し…", &[])
        .tab(FileEdit, "連番TGA＋CSV書き出し")
        .wired(),
    a("a_export_cut_folder", "カットフォルダーへ書き出し…", &[])
        .tab(FileEdit, "カットフォルダーへ書き出し")
        .wired(),
    a("a_export_shadow", "影素材を書き出し…", &[])
        .tab(FileEdit, "影素材を書き出し")
        .wired(),
    a("a_export_mp4", "MP4書き出し", &[])
        .tab(FileEdit, "MP4書き出し")
        .wired(),
    a("a_undo", "元に戻す", &["Ctrl+Z"])
        .tab(FileEdit, "元に戻す")
        .wired(),
    a("a_redo", "やり直す", &["Ctrl+Y", "Ctrl+Shift+Z"])
        .tab(FileEdit, "やり直す")
        .wired(),
    a("a_cut", "切り取り", &["Ctrl+X"]).tab(FileEdit, "切り取り"),
    a("a_copy", "コピー", &["Ctrl+C"]).tab(FileEdit, "コピー"),
    a("a_paste", "貼り付け", &["Ctrl+V"]).tab(FileEdit, "貼り付け"),
    a("a_resize", "キャンバスサイズの変更…", &[]).tab(FileEdit, "キャンバスサイズ変更"),
    a("a_pressure", "筆圧設定…", &[])
        .tab(FileEdit, "筆圧設定")
        .wired(),
    a("a_memo", "メモを開く", &[])
        .tab(FileEdit, "メモを開く")
        .wired(),
    a("a_preferences", "環境設定…", &["Ctrl+,"])
        .tab(FileEdit, "環境設定")
        .wired(),
    a("a_silhouette", "背景以外を黒シルエット表示", &["Ctrl+B"])
        .tab(FileEdit, "背景以外を黒シルエット表示")
        .checkable(),
    a("a_preview_map", "隣接色分けで表示", &[])
        .tab(FileEdit, "隣接色分けで表示")
        .checkable(),
    a("a_exposure_toggle", "露光量の調整を切り替え", &[]).tab(FileEdit, "露光量の調整を切り替え"),
    a("a_channel_red", "赤チャンネルを表示", &[]).tab(FileEdit, "赤チャンネルを表示"),
    a("a_channel_green", "緑チャンネルを表示", &[]).tab(FileEdit, "緑チャンネルを表示"),
    a("a_channel_blue", "青チャンネルを表示", &[]).tab(FileEdit, "青チャンネルを表示"),
    a("a_channel_alpha", "アルファチャンネルを表示", &[]).tab(FileEdit, "アルファチャンネルを表示"),
    a("a_isolate_color", "選択色だけ表示", &[])
        .tab(FileEdit, "選択色だけ表示")
        .wired(),
    a("a_clear_color_filter", "特定色表示を解除", &[])
        .tab(FileEdit, "特定色表示を解除")
        .wired(),
    a("a_remove_dust", "ゴミ取り／塗り抜け…", &[])
        .tab(FileEdit, "ゴミ取り")
        .wired(),
    a("a_auto_paint", "自動で塗る…", &[])
        .tab(FileEdit, "自動で塗る")
        .wired(),
    a("a_keep_initial_state", "今の絵を初期状態として保持", &[])
        .tab(FileEdit, "今の絵を初期状態として保持"),
    a(
        "a_import_initial_state",
        "線画カットフォルダーを初期状態として読み込む…",
        &[],
    )
    .tab(FileEdit, "線画カットフォルダーを初期状態として読み込む")
    .wired(),
    a(
        "a_import_clip_initial_state",
        "CLIPの線画を初期状態として読み込む…",
        &[],
    )
    .tab(FileEdit, "CLIPの線画を初期状態として読み込む")
    .wired(),
    a("a_clear_initial_state", "保持した初期状態を消す", &[])
        .tab(FileEdit, "保持した初期状態を消す"),
    a("a_reference_check", "見本との違いチェック…", &[])
        .tab(FileEdit, "見本との違いチェック")
        .wired(),
    // ---- メニューだけの操作（ショートカット設定には出ない） ----
    a("a_import_genga", "原画を読み込む…", &[]).wired(),
    a(
        "a_import_images_raw",
        "変換せず読み込む（下書きレイヤー）…",
        &[],
    )
    .wired(),
    a("a_import_psd", "PSDを読み込む…", &[]).wired(),
    a("a_import_clip", "CLIP STUDIOアニメーションを読み込む…", &[]).wired(),
    a("a_export_xdts", "XDTSタイムシートを書き出す…", &[]).wired(),
    a("a_export_psd", "PSDを書き出す…", &[]).wired(),
    a("a_binarize_layer", "2値化…", &[]),
    a("a_reconvert_layer", "元のファイルから再変換…", &[]),
    a("a_tidy_lines", "線整え…", &[]),
    a("a_color_chart", "カラーチャート…", &[]).wired(),
    a("a_shortcuts", "ショートカット設定…", &[]).wired(),
    a("a_prev", "前のフレーム", &["1"]).wired(),
    a("a_next", "次のフレーム", &["2"]).wired(),
    // ---- ツール（ToolPanel.TOOLS の順。キーは build_actions の defaults） ----
    a("tool:brush", "ツール：ブラシ", &["P"])
        .tab(Tools, "ツール：ブラシ")
        .wired(),
    a("tool:shape", "ツール：図形", &["O"])
        .tab(Tools, "ツール：図形")
        .wired(),
    a("tool:bucket", "ツール：バケツ", &["G"])
        .tab(Tools, "ツール：バケツ")
        .wired(),
    a("tool:lasso_fill", "ツール：投げ縄塗り", &["F"])
        .tab(Tools, "ツール：投げ縄塗り")
        .wired(),
    a("tool:replace_color", "ツール：色置換", &[])
        .tab(Tools, "ツール：色置換")
        .wired(),
    a("tool:skewer_fill", "ツール：串刺し塗り", &[])
        .tab(Tools, "ツール：串刺し塗り")
        .wired(),
    a("tool:lasso", "ツール：投げ縄選択", &["L"])
        .tab(Tools, "ツール：投げ縄選択")
        .wired(),
    a("tool:rect_select", "ツール：長方形選択", &["R"])
        .tab(Tools, "ツール：長方形選択")
        .wired(),
    a("tool:auto_select", "ツール：自動選択", &["W"])
        .tab(Tools, "ツール：自動選択")
        .wired(),
    a("tool:eyedropper", "ツール：スポイト", &["I"])
        .tab(Tools, "ツール：スポイト")
        .wired(),
    a("tool:dust", "ツール：ゴミ取り", &["D"])
        .tab(Tools, "ツール：ゴミ取り")
        .wired(),
    // ---- キャンバス操作（押している間） ----
    a("a_hold_hand", "ハンド（押している間）", &["Space"])
        .tab(CanvasOperation, "ハンド")
        .hold()
        .wired(),
    a("a_hold_zoom", "拡大縮小（押している間）", HOLD_ZOOM)
        .tab(CanvasOperation, "拡大縮小")
        .hold()
        .wired(),
    a("a_hold_rotate", "回転（押している間）", &["Shift+Space"])
        .tab(CanvasOperation, "回転")
        .hold()
        .wired(),
    a("a_hold_eyedropper", "スポイト（押している間）", &["Alt"])
        .tab(CanvasOperation, "スポイト")
        .hold()
        .wired(),
    // ---- ツールコマンド（tool_command_actions の順） ----
    a("a_select_main", "描画色：メインを選択", &[])
        .tab(ToolCommand, "描画色：メインを選択")
        .wired(),
    a("a_select_sub", "描画色：サブを選択", &[])
        .tab(ToolCommand, "描画色：サブを選択")
        .wired(),
    a("a_select_transparent", "描画色：背景色を選択", &["E"])
        .tab(ToolCommand, "描画色：背景色を選択")
        .wired(),
    a(
        "a_toggle_draw_background",
        "描画色と背景色を切り替え",
        &["C"],
    )
    .tab(ToolCommand, "描画色と背景色を切り替え")
    .wired(),
    a("a_swap_main_sub", "メイン色とサブ色を切り替え", &["X"])
        .tab(ToolCommand, "メイン色とサブ色を切り替え")
        .wired(),
    a("a_choose_background", "背景色の表示色を変更", &[])
        .tab(ToolCommand, "背景色の表示色を変更")
        .wired(),
    a("a_mainline_repaint", "MainLineRepaint", &[])
        .tab(ToolCommand, "MainLineRepaint")
        .wired(),
    a("a_selection_transform", "選択範囲：自由変形", &["Ctrl+T"])
        .tab(ToolCommand, "選択範囲：自由変形")
        .wired(),
    a("a_selection_scale", "選択範囲：拡大縮小", &[])
        .tab(ToolCommand, "選択範囲：拡大縮小")
        .wired(),
    a("a_selection_mesh", "選択範囲：メッシュ変形", &[])
        .tab(ToolCommand, "選択範囲：メッシュ変形")
        .wired(),
    a("a_selection_all", "すべてを選択", &["Ctrl+A"])
        .tab(ToolCommand, "すべてを選択")
        .wired(),
    a(
        "a_selection_clear",
        "選択範囲を解除",
        &["Ctrl+D", "Ctrl+Shift+A"],
    )
    .tab(ToolCommand, "選択範囲を解除")
    .wired(),
    a("a_selection_invert", "選択範囲を反転", &["Ctrl+Shift+I"])
        .tab(ToolCommand, "選択範囲を反転")
        .wired(),
    a(
        "a_selection_delete",
        "選択範囲の中を消去",
        &["Delete", "Backspace"],
    )
    .tab(ToolCommand, "選択範囲の中を消去")
    .wired(),
    a("a_view_zoom_in", "表示：拡大", &["Ctrl+=", "Ctrl++"])
        .tab(ToolCommand, "表示：拡大")
        .wired(),
    a("a_view_zoom_out", "表示：縮小", &["Ctrl+-"])
        .tab(ToolCommand, "表示：縮小")
        .wired(),
    a("a_view_fit", "表示：全体を表示", &["Ctrl+0"])
        .tab(ToolCommand, "表示：全体を表示")
        .wired(),
    a("a_brush_smaller", "ブラシサイズを小さく", &["["])
        .tab(ToolCommand, "ブラシサイズを小さく")
        .wired(),
    a("a_brush_larger", "ブラシサイズを大きく", &["]"])
        .tab(ToolCommand, "ブラシサイズを大きく")
        .wired(),
    a("a_transform_rotate_left", "変形：左へ90°回転", &[])
        .tab(ToolCommand, "変形：左へ90°回転")
        .wired(),
    a("a_transform_rotate_right", "変形：右へ90°回転", &[])
        .tab(ToolCommand, "変形：右へ90°回転")
        .wired(),
    a("a_transform_mesh_grid", "メッシュ変形：格子数を変更…", &[])
        .tab(ToolCommand, "メッシュ変形：格子数を変更")
        .wired(),
    a("a_transform_commit", "変形を確定", &["Return", "Enter"])
        .tab(ToolCommand, "変形を確定")
        .wired(),
    a("a_transform_cancel", "変形をキャンセル", &[])
        .tab(ToolCommand, "変形をキャンセル")
        .wired(),
    a("a_focus_used_color", "使用色：この色を選択範囲にする", &[])
        .tab(ToolCommand, "使用色：この色を選択範囲にする")
        .wired(),
    a("a_bucket_include_sub", "バケツ：含み塗り ON/OFF", &[])
        .tab(ToolCommand, "バケツ：含み塗り ON/OFF")
        .wired(),
    a("a_bucket_close_gap", "バケツ：隙間閉じ ON/OFF", &[])
        .tab(ToolCommand, "バケツ：隙間閉じ ON/OFF")
        .wired(),
    a("a_bucket_adjacent", "バケツ：隣接 ON/OFF", &[])
        .tab(ToolCommand, "バケツ：隣接 ON/OFF")
        .wired(),
    a("a_bucket_extend_lines", "バケツ：線の延長 ON/OFF", &[])
        .tab(ToolCommand, "バケツ：線の延長 ON/OFF")
        .wired(),
    a("a_bucket_island_fill", "バケツ：塗り残し拾い ON/OFF", &[])
        .tab(ToolCommand, "バケツ：塗り残し拾い ON/OFF")
        .wired(),
    a(
        "a_bucket_all_frames",
        "ツール：串刺し塗り／バケツを切り替え",
        &[],
    )
    .tab(ToolCommand, "ツール：串刺し塗り／バケツを切り替え")
    .wired(),
    a(
        "a_bucket_replace_color",
        "ツール：色置換／バケツを切り替え",
        &[],
    )
    .tab(ToolCommand, "ツール：色置換／バケツを切り替え")
    .wired(),
    a("a_dust_all_frames", "ゴミ取り：すべてのコマ ON/OFF", &[])
        .tab(ToolCommand, "ゴミ取り：すべてのコマ ON/OFF")
        .wired(),
    a("a_dust_apply", "ゴミ取り／塗り抜けを適用", &[])
        .tab(ToolCommand, "ゴミ取り／塗り抜けを適用")
        .wired(),
    a(
        "a_selection_all_frames",
        "選択変形：すべてのコマ ON/OFF",
        &[],
    )
    .tab(ToolCommand, "選択変形：すべてのコマ ON/OFF")
    .wired(),
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
    .tab(Timeline, "タイムリマップをコピー")
    .wired(),
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

    /// 【Group C】画面（ui/*.js）が引き受ける id。`const ACTIONS = {…}`・`TIMELINE_BUTTONS` の鍵、
    /// shell.js の `case "…":`、ひとつだけ受ける `id !== "…"`、押している間の `keys.a_hold_*`。
    fn claimed_by_ui() -> HashSet<String> {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("ui");
        let mut ids = HashSet::new();
        let id_char = |c: char| c.is_ascii_alphanumeric() || c == '_' || c == ':';
        for entry in std::fs::read_dir(&dir).unwrap() {
            let path = entry.unwrap().path();
            if path.extension().is_none_or(|e| e != "js")
                || path
                    .file_name()
                    .is_some_and(|n| n.to_string_lossy().contains("selftest"))
            {
                continue;
            }
            let text = std::fs::read_to_string(&path).unwrap();
            // 表の鍵。
            for head in ["const ACTIONS = {", "const TIMELINE_BUTTONS = {"] {
                let mut rest = text.as_str();
                while let Some(at) = rest.find(head) {
                    let block = &rest[at + head.len()..];
                    let end = block.find("\n};").or_else(|| block.find("\n  };")).unwrap();
                    for line in block[..end].lines() {
                        let key = line.trim_start().trim_start_matches('"');
                        let name: String = key
                            .chars()
                            .take_while(|c| *c != ':' && id_char(*c))
                            .collect();
                        let after = key[name.len()..].trim_start_matches('"');
                        if name.starts_with("a_") && after.starts_with(':') {
                            ids.insert(name);
                        }
                    }
                    rest = &block[end..];
                }
            }
            // 文字の形で受けるもの。
            for prefix in ["case \"", "id !== \"", "keys."] {
                for piece in text.split(prefix).skip(1) {
                    let name: String = piece.chars().take_while(|c| id_char(*c)).collect();
                    if name.starts_with("a_") {
                        ids.insert(name);
                    }
                }
            }
        }
        ids
    }

    /// 【Group C】`wired` は画面が実際に引き受ける操作と一致する（ショートカット設定の「＊」）。
    #[test]
    fn wired_flags_match_what_the_ui_claims() {
        let claimed = claimed_by_ui();
        // shell.js の case にあるが、文字の欄の中だけで、ほかは pending へ回すもの。
        let text_field_only = ["a_cut", "a_copy", "a_paste"];
        for action in ACTIONS {
            let id = action.id;
            // ツールは canvas_tools.js が tool:<id> をまとめて受ける。
            let ui =
                id.starts_with("tool:") || claimed.contains(id) && !text_field_only.contains(&id);
            assert_eq!(action.wired, ui, "wired と画面が食い違う：{id}");
        }
    }

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
