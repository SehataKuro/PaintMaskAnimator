//! アプリの外枠：メニュー・ファイルの開閉・最近使ったファイル・環境設定・ショートカット・
//! ヒストリー・メモ・自動保存・題名（Qt 版の MainWindow のうち、パネルの外の部分）。
//!
//! 画面（ui/shell.js）はこのモジュールのコマンドを呼ぶ。メニューの項目は menu.rs が
//! `menu-action` の事件で画面へ送り、画面が処理する（テキスト入力の中のコピーなどを見分けるため）。
//!
//! 錠の順：Session の錠を取ってから Shell の錠を取る（逆にはしない）。

use crate::actions::{Category, ACTIONS};
use crate::autosave::Autosave;
use crate::i18n;
use crate::menu::{self, MenuState, UiMenuState};
use crate::prefs::{self, Prefs, Resolved};
use crate::project_meta;
use crate::session::{Shared, Summary};
use crate::shortcuts;
use crate::with_session;
use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;

/// 画面の名前（Qt の APP_DISPLAY_NAME）。
pub fn display_name() -> String {
    format!("PaintMaskAnimator V{}", project_meta::APP_VERSION)
}

pub struct Shell {
    pub prefs: Prefs,
    pub language: String,
    pub ui_menu: UiMenuState,
    pub update: Option<String>,
    /// 保存した・開いたときの中身の世代。違えば未保存の変更がある。
    saved: (u64, u64),
    /// メモなど、文書の外の値を変えた（保存すると消える）。
    meta_dirty: bool,
    /// 自動保存から復元した作品（保存先は無く、未保存として扱う）。
    restored: bool,
}

pub type ShellShared = Mutex<Shell>;

impl Shell {
    pub fn new(prefs: Prefs) -> Self {
        Self {
            prefs,
            language: i18n::DEFAULT_LANGUAGE.into(),
            ui_menu: UiMenuState::default(),
            update: None,
            saved: (0, 0),
            meta_dirty: false,
            restored: false,
        }
    }

    fn menu_state(&self) -> MenuState {
        let resolved = self.prefs.resolved();
        let workspaces = resolved
            .workspaces
            .as_object()
            .map(|m| m.keys().cloned().collect())
            .unwrap_or_default();
        MenuState {
            language: self.language.clone(),
            keys: shortcuts::effective(self.prefs.get(prefs::SHORTCUTS)),
            recent: self.prefs.recent_projects(),
            workspaces,
            active_workspace: resolved.active_workspace.as_str().map(String::from),
            ui: self.ui_menu.clone(),
            update: self.update.clone(),
        }
    }

    pub fn mark_saved(&mut self, revision: (u64, u64)) {
        self.saved = revision;
        self.meta_dirty = false;
        self.restored = false;
    }

    pub fn is_dirty(&self, revision: (u64, u64)) -> bool {
        self.restored || self.meta_dirty || revision != self.saved
    }
}

/// メニューを組み直す。
pub fn refresh_menu(app: &AppHandle) {
    let state = app.state::<ShellShared>().lock().unwrap().menu_state();
    let handle = app.clone();
    // メニューは主スレッドで組む（macOS）。
    let _ = app.run_on_main_thread(move || {
        if let Err(e) = menu::install(&handle, &state) {
            eprintln!("メニューを組めなかった：{e}");
        }
    });
}

/// 題名（Qt の ProjectIOController.update_title）。
fn title(path: Option<&str>, dirty: bool) -> (String, String) {
    let suffix = if dirty { " *" } else { "" };
    let name = display_name();
    match path {
        Some(path) => {
            let file = Path::new(path)
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
            (format!("{name} — {file}{suffix}"), String::new())
        }
        None => (String::new(), suffix.to_string()),
    }
}

/// 画面へ渡す作品の状態。
#[derive(Serialize)]
pub struct ProjectState {
    pub path: Option<String>,
    pub dirty: bool,
    /// 保存先がある作品の題名。無ければ空（画面が「新規プロジェクト」を訳して付ける）。
    pub title: String,
    /// 保存先の無い作品の題名に付ける印（未保存なら " *"）。
    pub suffix: String,
    pub app_name: String,
    pub summary: Summary,
    /// 読み込み（imports.rs）：新規作成でレイアウト用紙を入れられなかったときの知らせ。
    pub warning: Option<String>,
}

fn project_state_locked(session: &crate::session::Session, shell: &Shell) -> ProjectState {
    let summary = session.summary();
    let dirty = shell.is_dirty(session.content_revision());
    let path = summary.path.clone();
    let (title, suffix) = title(path.as_deref(), dirty);
    ProjectState {
        path,
        dirty,
        title,
        suffix,
        app_name: display_name(),
        summary,
        warning: None,
    }
}

#[tauri::command]
pub fn project_state(session: State<Shared>, shell: State<ShellShared>) -> ProjectState {
    let s = session.lock().unwrap();
    let sh = shell.lock().unwrap();
    project_state_locked(&s, &sh)
}

/// 起動のとき画面が読む外枠の情報。
#[derive(Serialize)]
pub struct ShellInfo {
    pub language: String,
    /// 言葉の表（日本語 → 訳）の JSON。
    pub catalogue: &'static str,
    pub prefs: Resolved,
    pub version: String,
    pub app_name: String,
    pub platform: &'static str,
    /// 前回の自動保存が残っている（異常終了の印）。
    pub recovery: bool,
    pub languages: Vec<(&'static str, &'static str)>,
    /// 確かめ用：起動したら順に選ぶメニューの項目（`PMA_SHELL_DEMO`、カンマ区切り）。
    pub demo: Option<String>,
}

/// 起動のとき：OS の言語（navigator.languages）で使う言語を決め、メニューを組む。
#[tauri::command]
pub fn shell_init(
    app: AppHandle,
    shell: State<ShellShared>,
    autosave: State<Autosave>,
    languages: Vec<String>,
) -> ShellInfo {
    let info = {
        let mut sh = shell.lock().unwrap();
        sh.language = i18n::resolve(&sh.prefs.language(), &languages);
        ShellInfo {
            language: sh.language.clone(),
            catalogue: i18n::catalogue_json(&sh.language),
            prefs: sh.prefs.resolved(),
            version: project_meta::APP_VERSION.into(),
            app_name: display_name(),
            platform: std::env::consts::OS,
            recovery: autosave.leftover(),
            languages: prefs::LANGUAGES.to_vec(),
            demo: std::env::var("PMA_SHELL_DEMO")
                .ok()
                .filter(|v| !v.is_empty()),
        }
    };
    refresh_menu(&app);
    info
}

/// 画面の側のメニューの状態（パネルの表示など）を受けて、メニューを組み直す。
#[tauri::command]
pub fn shell_menu_state(app: AppHandle, shell: State<ShellShared>, state: UiMenuState) {
    shell.lock().unwrap().ui_menu = state;
    refresh_menu(&app);
}

/// 新しい版のお知らせを出す・消す。
#[tauri::command]
pub fn shell_set_update(app: AppHandle, shell: State<ShellShared>, latest: Option<String>) {
    shell.lock().unwrap().update = latest;
    refresh_menu(&app);
}

// ---------------------------------------------------------------------------
// 環境設定
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn prefs_get(shell: State<ShellShared>) -> Resolved {
    shell.lock().unwrap().prefs.resolved()
}

/// 1つの設定を変えて保存する。履歴の上限は今の作品にもすぐ使う。
#[tauri::command]
pub fn prefs_set(
    app: AppHandle,
    session: State<Shared>,
    shell: State<ShellShared>,
    key: String,
    value: Value,
) -> Result<Resolved, String> {
    let mut s = session.lock().unwrap();
    let resolved = {
        let mut sh = shell.lock().unwrap();
        sh.prefs.set_checked(&key, value)?;
        sh.prefs.resolved()
    };
    if key == prefs::UNDO_STEPS || key == prefs::UNDO_MEMORY {
        s.set_history_limits(crate::history::limits(
            resolved.undo_max_steps,
            resolved.undo_max_memory_mb,
        ));
    }
    drop(s);
    if key == prefs::WORKSPACES || key == prefs::ACTIVE_WORKSPACE {
        refresh_menu(&app);
    }
    Ok(resolved)
}

// ---------------------------------------------------------------------------
// ショートカット
// ---------------------------------------------------------------------------

#[derive(Serialize)]
pub struct ShortcutEntry {
    pub id: &'static str,
    pub label: &'static str,
    pub name: Option<&'static str>,
    pub category: Option<Category>,
    pub keys: Vec<String>,
    pub default_keys: Vec<&'static str>,
    pub hold: bool,
    pub wired: bool,
    /// メニューに付けたキー（画面のキー入力では受けない）。
    pub menu_key: Option<String>,
}

#[derive(Serialize)]
pub struct ShortcutTable {
    pub categories: Vec<(Category, &'static str)>,
    pub actions: Vec<ShortcutEntry>,
}

/// メニューに載る操作（そのキーはメニューが受ける）。
const MENU_ACTIONS: &[&str] = &[
    "a_new",
    "a_open_project",
    "a_save_project",
    "a_save_project_as",
    "a_undo",
    "a_redo",
    "a_cut",
    "a_copy",
    "a_paste",
    "a_silhouette",
    "a_preferences",
    "a_selection_all",
    "a_selection_clear",
    "a_selection_invert",
    "a_selection_transform",
    "a_selection_delete",
    "a_transform_commit",
    "a_prev",
    "a_next",
    "a_tl_previous_key",
    "a_tl_next_key",
    "a_open_cut_folder",
    "a_import_images",
    "a_import_folder",
    "a_save",
    "a_save_tga",
    "a_export_png_seq",
    "a_export_tga_seq",
    "a_export_cut_folder",
    "a_export_shadow",
    "a_export_mp4",
    "a_resize",
    "a_pressure",
    "a_memo",
    "a_preview_map",
    "a_isolate_color",
    "a_clear_color_filter",
    "a_swap_main_sub",
    "a_remove_dust",
    "a_auto_paint",
    "a_keep_initial_state",
    "a_import_initial_state",
    "a_import_clip_initial_state",
    "a_clear_initial_state",
    "a_reference_check",
    "a_selection_scale",
    "a_selection_mesh",
    "a_transform_rotate_left",
    "a_transform_rotate_right",
    "a_transform_mesh_grid",
    "a_transform_cancel",
    "a_tl_paste_time_remap",
    "a_tl_copy_time_remap",
];

fn shortcut_table(sh: &Shell) -> ShortcutTable {
    let keys = shortcuts::effective(sh.prefs.get(prefs::SHORTCUTS));
    ShortcutTable {
        categories: Category::ALL.iter().map(|c| (*c, c.title())).collect(),
        actions: ACTIONS
            .iter()
            .map(|a| {
                let list = keys.get(a.id).cloned().unwrap_or_default();
                let menu_key = MENU_ACTIONS
                    .contains(&a.id)
                    .then(|| {
                        list.first()
                            .filter(|k| menu::accelerator(k).is_some())
                            .cloned()
                    })
                    .flatten();
                ShortcutEntry {
                    id: a.id,
                    label: a.label,
                    name: a.shortcut.map(|(_, n)| n),
                    category: a.shortcut.map(|(c, _)| c),
                    keys: list,
                    default_keys: a.keys.to_vec(),
                    hold: a.hold,
                    wired: a.wired,
                    menu_key,
                }
            })
            .collect(),
    }
}

#[tauri::command]
pub fn shortcuts_get(shell: State<ShellShared>) -> ShortcutTable {
    shortcut_table(&shell.lock().unwrap())
}

/// ショートカット設定の OK：全部の操作のキーを受け、重なりが無ければ保存する。
/// 重なりがあれば「キー: 名前 / 名前」の行を返す（Qt の「ショートカットの重複」）。
#[tauri::command]
pub fn shortcuts_set(
    app: AppHandle,
    shell: State<ShellShared>,
    keys: HashMap<String, Vec<String>>,
) -> Result<ShortcutTable, Vec<(String, String, String)>> {
    let table = {
        let mut sh = shell.lock().unwrap();
        let dups = shortcuts::duplicates(&keys);
        if !dups.is_empty() {
            return Err(dups);
        }
        let stored = shortcuts::to_stored(sh.prefs.get(prefs::SHORTCUTS), &keys);
        sh.prefs
            .set(prefs::SHORTCUTS, stored)
            .map_err(|e| vec![(e, String::new(), String::new())])?;
        shortcut_table(&sh)
    };
    refresh_menu(&app);
    Ok(table)
}

// ---------------------------------------------------------------------------
// ファイル
// ---------------------------------------------------------------------------

fn after_install(app: &AppHandle, path: Option<&Path>) {
    let session = app.state::<Shared>();
    let s = session.lock().unwrap();
    let shell = app.state::<ShellShared>();
    let mut sh = shell.lock().unwrap();
    sh.mark_saved(s.content_revision());
    if let Some(path) = path {
        let _ = sh.prefs.remember_recent(path);
    }
}

/// 新規作成（Qt の new_doc）。選んだレイアウト用紙を一番下の下書きレイヤーに入れる。
#[tauri::command]
pub fn project_new(
    app: AppHandle,
    width: usize,
    height: usize,
    paper: Option<String>,
) -> Result<ProjectState, String> {
    let (low, high) = prefs::CANVAS_SIZE_RANGE;
    let clamp = |v: usize| (v as i64).clamp(low, high) as usize;
    // 読み込み（imports.rs）：選んだレイアウト用紙を一番下の下書きレイヤーに入れる。
    let paper_path = paper
        .as_deref()
        .map(|id| crate::imports::paper_path(&app, id));
    let paper_warning = with_session(&app, "open", |s| {
        s.new_document(clamp(width), clamp(height))?;
        Ok::<_, String>(match &paper_path {
            Some(Some(path)) => crate::imports::insert_layout_paper(s, path).err(),
            Some(None) => Some("レイアウト用紙の画像を読み込めませんでした。".into()),
            None => None,
        })
    })?;
    after_install(&app, None);
    {
        let shell = app.state::<ShellShared>();
        let mut sh = shell.lock().unwrap();
        let _ = sh.prefs.set(
            prefs::LAYOUT_PAPER_LAST,
            paper.map_or(Value::Null, Value::String),
        );
    }
    let mut state = project_state(app.state(), app.state());
    state.warning = paper_warning;
    Ok(state)
}

/// .pman を開く（Qt の ProjectIOController.open）。最近使ったファイルに入れる。
#[tauri::command]
pub fn project_open(app: AppHandle, path: String) -> Result<ProjectState, String> {
    let path = PathBuf::from(path);
    with_session(&app, "open", |s| {
        s.open(&path)?;
        project_meta::load_into_timeline(s);
        Ok::<_, String>(())
    })?;
    after_install(&app, Some(&path));
    refresh_menu(&app);
    Ok(project_state(app.state(), app.state()))
}

/// 上書き保存（保存先が無い・.pman でなければ Err("save_as")）。path を渡せば名前を付けて保存。
#[tauri::command]
pub fn project_save(app: AppHandle, path: Option<String>) -> Result<ProjectState, String> {
    let session = app.state::<Shared>();
    let mut s = session.lock().unwrap();
    let target = match path {
        Some(p) => {
            let p = PathBuf::from(p);
            if p.extension()
                .is_some_and(|e| e.eq_ignore_ascii_case("pman"))
            {
                p
            } else {
                p.with_extension("pman")
            }
        }
        None => match s.summary().path {
            Some(p) if p.to_lowercase().ends_with(".pman") => PathBuf::from(p),
            _ => return Err("save_as".into()),
        },
    };
    project_meta::store_from_session(&mut s);
    let saved = s.save(Some(&target))?;
    let state = {
        let shell = app.state::<ShellShared>();
        let mut sh = shell.lock().unwrap();
        sh.mark_saved(s.content_revision());
        let _ = sh.prefs.remember_recent(&saved);
        project_state_locked(&s, &sh)
    };
    drop(s);
    refresh_menu(&app);
    Ok(state)
}

/// ファイルを開く窓（「プロジェクトを開く」）。選ばなければ None。
#[tauri::command]
pub async fn pick_open_path(app: AppHandle, title: String, filter: String) -> Option<String> {
    app.dialog()
        .file()
        .set_title(title)
        .add_filter(filter, &["pman"])
        .blocking_pick_file()
        .and_then(|p| p.into_path().ok())
        .map(|p| p.display().to_string())
}

/// 保存の窓（「名前を付けて保存」）。選ばなければ None。
#[tauri::command]
pub async fn pick_save_path(app: AppHandle, title: String, initial: String) -> Option<String> {
    let initial = PathBuf::from(initial);
    let mut dialog = app
        .dialog()
        .file()
        .set_title(title)
        .add_filter("PaintMaskAnimator Project", &["pman"]);
    if let Some(dir) = initial.parent().filter(|d| d.is_dir()) {
        dialog = dialog.set_directory(dir);
    }
    if let Some(name) = initial.file_name() {
        dialog = dialog.set_file_name(name.to_string_lossy());
    }
    dialog
        .blocking_save_file()
        .and_then(|p| p.into_path().ok())
        .map(|p| p.display().to_string())
}

/// 最近使ったファイルの n 番目。無くなっていれば一覧から外して Err(パス)。
#[tauri::command]
pub fn recent_path(app: AppHandle, index: usize) -> Result<String, String> {
    let path = {
        let shell = app.state::<ShellShared>();
        let mut sh = shell.lock().unwrap();
        let Some(path) = sh.prefs.recent_projects().get(index).cloned() else {
            return Err(String::new());
        };
        if Path::new(&path).exists() {
            return Ok(path);
        }
        let _ = sh.prefs.forget_recent(&path);
        path
    };
    refresh_menu(&app);
    Err(path)
}

#[tauri::command]
pub fn recent_clear(app: AppHandle, shell: State<ShellShared>) -> Result<(), String> {
    shell
        .lock()
        .unwrap()
        .prefs
        .set(prefs::RECENT_PROJECTS, Value::Array(vec![]))?;
    refresh_menu(&app);
    Ok(())
}

// ---------------------------------------------------------------------------
// メモ（Qt の MemoWindow と project_memo）
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn memo_get(session: State<Shared>) -> String {
    let s = session.lock().unwrap();
    s.project
        .as_ref()
        .map(|p| project_meta::memo(&p.metadata))
        .unwrap_or_default()
}

#[tauri::command]
pub fn memo_set(session: State<Shared>, shell: State<ShellShared>, text: String) {
    let mut s = session.lock().unwrap();
    if let Some(p) = &mut s.project {
        if project_meta::memo(&p.metadata) != text {
            project_meta::set_memo(&mut p.metadata, &text);
            shell.lock().unwrap().meta_dirty = true;
        }
    }
}

// ---------------------------------------------------------------------------
// ヒストリーパネル
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn history_graph(session: State<Shared>) -> crate::history::Graph {
    session.lock().unwrap().history_graph()
}

#[tauri::command]
pub fn history_jump(app: AppHandle, delta: i64) -> Result<bool, String> {
    with_session(&app, "history", |s| s.jump_history(delta))
}

#[tauri::command]
pub fn history_switch(app: AppHandle, index: usize, steps: usize) -> Result<bool, String> {
    with_session(&app, "history", |s| s.switch_history_branch(index, steps))
}

// ---------------------------------------------------------------------------
// 自動保存の復元・終わり
// ---------------------------------------------------------------------------

/// 自動保存から作品を開く。保存先は無く、未保存の作品として扱う。
#[tauri::command]
pub fn autosave_restore(app: AppHandle) -> Result<ProjectState, String> {
    let path = app.state::<Autosave>().path.clone();
    with_session(&app, "open", |s| {
        s.open(&path)?;
        project_meta::load_into_timeline(s);
        if let Some(p) = &mut s.project {
            p.path = None;
        }
        Ok::<_, String>(())
    })?;
    after_install(&app, None);
    app.state::<ShellShared>().lock().unwrap().restored = true;
    Ok(project_state(app.state(), app.state()))
}

#[tauri::command]
pub fn autosave_discard(autosave: State<Autosave>) {
    autosave.clear();
}

/// きちんと終わる（自動保存を消す）。
#[tauri::command]
pub fn shell_quit(app: AppHandle) {
    app.state::<Autosave>().finish();
    app.exit(0);
}

/// 窓の題名を変える。
#[tauri::command]
pub fn set_title(app: AppHandle, title: String) -> Result<(), String> {
    let window = app.get_webview_window("main").ok_or("窓が無い")?;
    window.set_title(&title).map_err(|e| e.to_string())
}

/// ブラウザでページを開く（リリースノート・インストーラ・ソースコード）。この作品の GitHub と
/// ライセンスのページだけ。
#[tauri::command]
pub fn open_url(url: String) -> Result<(), String> {
    const ALLOWED: [&str; 3] = [
        "https://github.com/SehataKuro/PaintMaskAnimator",
        "https://www.apache.org/licenses/",
        "https://api.github.com/repos/SehataKuro/PaintMaskAnimator",
    ];
    if !ALLOWED.iter().any(|p| url.starts_with(p)) || url.contains(['"', '\'', ' ', '\n']) {
        return Err("開けない URL です".into());
    }
    #[cfg(target_os = "macos")]
    let result = std::process::Command::new("open").arg(&url).spawn();
    #[cfg(target_os = "windows")]
    let result = std::process::Command::new("rundll32")
        .args(["url.dll,FileProtocolHandler", &url])
        .spawn();
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let result = std::process::Command::new("xdg-open").arg(&url).spawn();
    result.map(|_| ()).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn titles_like_qt() {
        let (t, _) = title(Some("/a/b/cut01.pman"), true);
        assert_eq!(t, format!("{} — cut01.pman *", display_name()));
        let (t, suffix) = title(None, false);
        assert!(t.is_empty() && suffix.is_empty());
        assert!(display_name().starts_with("PaintMaskAnimator V"));
    }

    #[test]
    fn dirty_follows_revisions_and_memo() {
        let mut sh = Shell::new(Prefs::in_memory(serde_json::json!({})));
        sh.mark_saved((3, 4));
        assert!(!sh.is_dirty((3, 4)));
        assert!(sh.is_dirty((3, 5)));
        sh.meta_dirty = true;
        assert!(sh.is_dirty((3, 4)));
        sh.mark_saved((3, 4));
        assert!(!sh.is_dirty((3, 4)));
    }

    #[test]
    fn menu_actions_are_known() {
        for id in MENU_ACTIONS {
            assert!(crate::actions::find(id).is_some(), "{id}");
        }
    }

    #[test]
    fn open_url_refuses_other_sites() {
        assert!(open_url("https://example.com/".into()).is_err());
        assert!(open_url("https://github.com/SehataKuro/PaintMaskAnimator\" x".into()).is_err());
    }
}
