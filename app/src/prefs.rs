//! 環境設定の入れ物（Qt 版の config.py と preferences.py）。
//!
//! 設定は JSON のオブジェクト一つで、Tauri のアプリ設定のフォルダーの `config.json` に置く。
//! 鍵と値の形・既定値・範囲の丸めは Qt 版と同じ（preferences.py・theme.py・i18n.py・
//! main_window_project_io.py・main_window_shortcuts.py・main_window_workspace.py）。
//!
//! - 書くときは隣に書いてから置き換える（Qt の save_config と同じ）。値が null の鍵は消す。
//! - 初めて開くとき、Tauri の設定がまだ無く Qt 版の設定（`~/.config/PaintMaskAnimator/config.json`、
//!   Windows は `%APPDATA%\PaintMaskAnimator\config.json`）があれば、写してから使う。
//!   パネルの配置（ワークスペース）は Qt の形（dock_state）のまま写し、選んだときに
//!   workspace.rs がこの画面の並びへ読み替える。

use serde::Serialize;
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};

/// Qt 版の APP_NAME（設定のフォルダーの名前）。
pub const QT_APP_NAME: &str = "PaintMaskAnimator";

// ---- 鍵（Qt 版と同じ名前） ----
pub const AUTOSAVE_ENABLED: &str = "autosave_enabled";
pub const AUTOSAVE_INTERVAL: &str = "autosave_interval_minutes";
pub const UNDO_STEPS: &str = "undo_max_steps";
pub const UNDO_MEMORY: &str = "undo_max_memory_mb";
pub const NEW_CANVAS_SIZE: &str = "new_canvas_size";
pub const KEEP_INITIAL_STATE: &str = "keep_initial_state_on_cut_folder";
pub const MAP_PALETTE: &str = "map_color_palette";
pub const TOOL_HOLD_ENABLED: &str = "tool_hold_switch_enabled";
pub const TOOL_HOLD_MS: &str = "tool_hold_switch_ms";
pub const AUTO_UPDATE_CHECK: &str = "check_updates_automatically";
pub const SKIPPED_UPDATE: &str = "skipped_update_version";
pub const BACKGROUND_CHECKER_ENABLED: &str = "background_checker_enabled";
pub const BACKGROUND_CHECKER_COLOR: &str = "background_checker_color";
pub const TIME_DISPLAY: &str = "time_display_mode";
pub const DISPLAY_SHRINK_MODE: &str = "display_shrink_mode";
pub const BACKGROUND_COLOR_TOOL: &str = "background_color_tool";
pub const THEME: &str = "ui_theme";
pub const ACCENT: &str = "ui_accent";
pub const LANGUAGE: &str = "language";
pub const RECENT_PROJECTS: &str = "recent_projects";
pub const SHORTCUTS: &str = "shortcuts";
pub const WORKSPACES: &str = "workspaces";
pub const ACTIVE_WORKSPACE: &str = "active_workspace";
pub const LAYOUT_PAPERS: &str = "layout_papers";
pub const LAYOUT_PAPER_LAST: &str = "layout_paper_last";
pub const PRESSURE_PRESETS: &str = "pressure_presets";
pub const PRESSURE_ACTIVE: &str = "pressure_active_preset";
/// Qt 版から写した印（写した元のファイル）。
pub const MIGRATED_FROM: &str = "migrated_from_qt";

// ---- 既定値と範囲（preferences.py・constants.py） ----
pub const DEFAULT_AUTOSAVE_INTERVAL: i64 = 3;
pub const AUTOSAVE_INTERVAL_RANGE: (i64, i64) = (1, 60);
/// constants.MAX_UNDO。
pub const DEFAULT_UNDO_STEPS: i64 = 30;
pub const UNDO_STEPS_RANGE: (i64, i64) = (5, 500);
/// constants.MAX_UNDO_BYTES（768 MB）。
pub const DEFAULT_UNDO_MEMORY_MB: i64 = 768;
pub const UNDO_MEMORY_RANGE: (i64, i64) = (128, 16384);
/// constants.CANVAS_WIDTH・CANVAS_HEIGHT の既定。
pub const DEFAULT_CANVAS_SIZE: (i64, i64) = (1280, 720);
/// constants.MAX_IMAGE_DIMENSION。
pub const CANVAS_SIZE_RANGE: (i64, i64) = (64, 16384);
pub const DEFAULT_TOOL_HOLD_MS: i64 = 0;
pub const TOOL_HOLD_MS_RANGE: (i64, i64) = (0, 3000);
/// main_window_project_io.RECENT_PROJECTS_LIMIT。
pub const RECENT_PROJECTS_LIMIT: usize = 10;
/// view_preview.MAP_PALETTE。
pub const DEFAULT_MAP_PALETTE: [[u8; 3]; 12] = [
    [230, 25, 75],
    [60, 180, 75],
    [255, 225, 25],
    [0, 130, 200],
    [245, 130, 48],
    [145, 30, 180],
    [70, 240, 240],
    [240, 50, 230],
    [210, 245, 60],
    [250, 190, 212],
    [0, 128, 128],
    [170, 110, 40],
];
/// theme.DEFAULT_ACCENT。
pub const DEFAULT_ACCENT: &str = "#2f6fed";
pub const SYSTEM: &str = "system";
/// i18n.SUPPORTED_LANGUAGES（元の言語が先）。
pub const LANGUAGES: [(&str, &str); 2] = [("ja", "日本語"), ("en", "English")];

/// 設定の入れ物。`path` が None なら書かない（試験用）。
pub struct Prefs {
    path: Option<PathBuf>,
    data: Map<String, Value>,
}

impl Prefs {
    /// 書き出さない入れ物（試験用）。
    #[cfg_attr(not(test), allow(dead_code))]
    pub fn in_memory(data: Value) -> Self {
        Self {
            path: None,
            data: data.as_object().cloned().unwrap_or_default(),
        }
    }

    /// `dir/config.json` を読む。無ければ `qt` の設定を写す。読めなければ空にする
    /// （Qt の load_config と同じく、壊れた設定で起動できなくならない）。
    pub fn load(dir: &Path, qt: Option<PathBuf>) -> Self {
        let path = dir.join("config.json");
        let mut prefs = Self {
            path: Some(path.clone()),
            data: read_object(&path).unwrap_or_default(),
        };
        if !path.exists() {
            if let Some(qt) = qt.filter(|p| p.is_file()) {
                if let Some(mut data) = read_object(&qt) {
                    // 【Group C】窓の配置（Qt の dock の状態）も写す。選んだときに workspace.rs が
                    // この画面の並びへ読み替える。
                    data.insert(
                        MIGRATED_FROM.into(),
                        Value::String(qt.display().to_string()),
                    );
                    prefs.data = data;
                    let _ = prefs.save();
                }
            }
        }
        prefs
    }

    /// 生の値（無ければ None）。
    pub fn get(&self, key: &str) -> Option<&Value> {
        self.data.get(key)
    }

    /// 値を入れて保存する。null なら鍵を消す（Qt の set_value）。
    pub fn set(&mut self, key: &str, value: Value) -> Result<(), String> {
        if value.is_null() {
            self.data.remove(key);
        } else {
            self.data.insert(key.to_string(), value);
        }
        self.save()
    }

    fn save(&self) -> Result<(), String> {
        let Some(path) = &self.path else {
            return Ok(());
        };
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        let text = serde_json::to_string_pretty(&Value::Object(self.data.clone()))
            .map_err(|e| e.to_string())?;
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, text).map_err(|e| e.to_string())?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600));
        }
        std::fs::rename(&tmp, path).map_err(|e| e.to_string())
    }

    fn flag(&self, key: &str, default: bool) -> bool {
        // Python の bool(value)：0・空・null は偽。
        match self.data.get(key) {
            None => default,
            Some(value) => truthy(value),
        }
    }

    /// preferences._clamped_int。
    fn clamped(&self, key: &str, default: i64, (low, high): (i64, i64)) -> i64 {
        let value = match self.data.get(key) {
            None | Some(Value::Null) => default,
            Some(value) => python_int(value).unwrap_or(default),
        };
        value.clamp(low, high)
    }

    pub fn autosave_enabled(&self) -> bool {
        self.flag(AUTOSAVE_ENABLED, true)
    }

    pub fn autosave_interval_minutes(&self) -> i64 {
        self.clamped(
            AUTOSAVE_INTERVAL,
            DEFAULT_AUTOSAVE_INTERVAL,
            AUTOSAVE_INTERVAL_RANGE,
        )
    }

    pub fn undo_max_steps(&self) -> i64 {
        self.clamped(UNDO_STEPS, DEFAULT_UNDO_STEPS, UNDO_STEPS_RANGE)
    }

    pub fn undo_max_memory_mb(&self) -> i64 {
        self.clamped(UNDO_MEMORY, DEFAULT_UNDO_MEMORY_MB, UNDO_MEMORY_RANGE)
    }

    /// preferences.new_canvas_size。
    pub fn new_canvas_size(&self) -> (i64, i64) {
        let Some(Value::Array(items)) = self.data.get(NEW_CANVAS_SIZE) else {
            return DEFAULT_CANVAS_SIZE;
        };
        let values: Option<Vec<i64>> = items.iter().map(python_int).collect();
        match values.as_deref() {
            Some(&[w, h]) => {
                let (low, high) = CANVAS_SIZE_RANGE;
                (w.clamp(low, high), h.clamp(low, high))
            }
            _ => DEFAULT_CANVAS_SIZE,
        }
    }

    pub fn tool_hold_ms(&self) -> i64 {
        self.clamped(TOOL_HOLD_MS, DEFAULT_TOOL_HOLD_MS, TOOL_HOLD_MS_RANGE)
    }

    fn choice(&self, key: &str, choices: &[&str], default: &str) -> String {
        match self.data.get(key).and_then(Value::as_str) {
            Some(value) if choices.contains(&value) => value.to_string(),
            _ => default.to_string(),
        }
    }

    /// theme.current_theme（macOS の既定は「システムに合わせる」）。
    pub fn theme(&self) -> String {
        let default = if cfg!(target_os = "macos") {
            SYSTEM
        } else {
            "light"
        };
        self.choice(THEME, &[SYSTEM, "light", "dark"], default)
    }

    /// theme.accent_setting。
    pub fn accent(&self) -> String {
        let default = if cfg!(target_os = "macos") {
            SYSTEM
        } else {
            DEFAULT_ACCENT
        };
        match self.data.get(ACCENT).and_then(Value::as_str) {
            Some(SYSTEM) => SYSTEM.into(),
            Some(value) if parse_hex(value).is_some() => value.to_string(),
            _ => default.into(),
        }
    }

    /// i18n.preferred_language。
    pub fn language(&self) -> String {
        self.choice(LANGUAGE, &[SYSTEM, "ja", "en"], SYSTEM)
    }

    /// preferences.background_checker_color（"#rrggbb"）。
    pub fn background_checker_color(&self) -> String {
        let rgb = self
            .data
            .get(BACKGROUND_CHECKER_COLOR)
            .and_then(Value::as_str)
            .and_then(parse_hex)
            .unwrap_or([255, 255, 255]);
        hex(rgb)
    }

    /// preferences.map_palette：読めない色はその番号の既定。
    pub fn map_palette(&self) -> Vec<String> {
        let stored = self.data.get(MAP_PALETTE).and_then(Value::as_array);
        DEFAULT_MAP_PALETTE
            .iter()
            .enumerate()
            .map(|(i, default)| {
                let rgb = stored
                    .and_then(|s| s.get(i))
                    .and_then(Value::as_str)
                    .and_then(parse_hex)
                    .unwrap_or(*default);
                hex(rgb)
            })
            .collect()
    }

    /// 新しい順の最近使ったファイル。
    pub fn recent_projects(&self) -> Vec<String> {
        match self.data.get(RECENT_PROJECTS) {
            Some(Value::Array(items)) => items
                .iter()
                .filter_map(Value::as_str)
                .filter(|s| !s.is_empty())
                .map(String::from)
                .collect(),
            _ => vec![],
        }
    }

    /// 一覧の先頭に入れる（main_window_project_io.remember_recent）。
    pub fn remember_recent(&mut self, path: &Path) -> Result<(), String> {
        let text = std::fs::canonicalize(path)
            .unwrap_or_else(|_| path.to_path_buf())
            .display()
            .to_string();
        let mut paths = vec![text.clone()];
        paths.extend(self.recent_projects().into_iter().filter(|p| *p != text));
        paths.truncate(RECENT_PROJECTS_LIMIT);
        self.set(RECENT_PROJECTS, json!(paths))
    }

    pub fn forget_recent(&mut self, path: &str) -> Result<(), String> {
        let paths: Vec<String> = self
            .recent_projects()
            .into_iter()
            .filter(|p| p != path)
            .collect();
        self.set(RECENT_PROJECTS, json!(paths))
    }

    /// 画面へ渡す、丸めた後の全部の設定。
    pub fn resolved(&self) -> Resolved {
        let (width, height) = self.new_canvas_size();
        Resolved {
            language: self.language(),
            time_display_mode: self.choice(TIME_DISPLAY, &["frames", "seconds_frames"], "frames"),
            background_color_tool: self.choice(
                BACKGROUND_COLOR_TOOL,
                &["brush", "bucket"],
                "brush",
            ),
            keep_initial_state_on_cut_folder: self.flag(KEEP_INITIAL_STATE, false),
            autosave_enabled: self.autosave_enabled(),
            autosave_interval_minutes: self.autosave_interval_minutes(),
            check_updates_automatically: self.flag(AUTO_UPDATE_CHECK, true),
            skipped_update_version: self
                .data
                .get(SKIPPED_UPDATE)
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            tool_hold_switch_enabled: self.flag(TOOL_HOLD_ENABLED, false),
            tool_hold_switch_ms: self.tool_hold_ms(),
            theme: self.theme(),
            accent: self.accent(),
            background_checker_enabled: self.flag(BACKGROUND_CHECKER_ENABLED, false),
            background_checker_color: self.background_checker_color(),
            new_canvas_size: (width, height),
            map_color_palette: self.map_palette(),
            display_shrink_mode: self.choice(
                DISPLAY_SHRINK_MODE,
                &["nearest", "keep_lines"],
                "nearest",
            ),
            undo_max_steps: self.undo_max_steps(),
            undo_max_memory_mb: self.undo_max_memory_mb(),
            layout_papers: self
                .data
                .get(LAYOUT_PAPERS)
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter(|e| {
                            e.get("id").is_some_and(truthy) && e.get("file").is_some_and(truthy)
                        })
                        .cloned()
                        .collect()
                })
                .unwrap_or_default(),
            layout_paper_last: self
                .data
                .get(LAYOUT_PAPER_LAST)
                .cloned()
                .unwrap_or_default(),
            pressure_presets: self
                .data
                .get(PRESSURE_PRESETS)
                .cloned()
                .unwrap_or(json!([])),
            pressure_active_preset: self.data.get(PRESSURE_ACTIVE).cloned().unwrap_or_default(),
            workspaces: match self.data.get(WORKSPACES) {
                Some(Value::Object(map)) => Value::Object(map.clone()),
                _ => json!({}),
            },
            active_workspace: self.data.get(ACTIVE_WORKSPACE).cloned().unwrap_or_default(),
            config_path: self
                .path
                .as_ref()
                .map(|p| p.display().to_string())
                .unwrap_or_default(),
            migrated_from_qt: self
                .data
                .get(MIGRATED_FROM)
                .and_then(Value::as_str)
                .map(String::from),
        }
    }

    /// 画面から1つの設定を変える。鍵ごとに値の形を確かめ、Qt と同じ形で書く。
    pub fn set_checked(&mut self, key: &str, value: Value) -> Result<(), String> {
        let bad = || format!("設定の値が不正です：{key}");
        let stored = match key {
            AUTOSAVE_ENABLED
            | KEEP_INITIAL_STATE
            | AUTO_UPDATE_CHECK
            | TOOL_HOLD_ENABLED
            | BACKGROUND_CHECKER_ENABLED => Value::Bool(value.as_bool().ok_or_else(bad)?),
            AUTOSAVE_INTERVAL | UNDO_STEPS | UNDO_MEMORY | TOOL_HOLD_MS => {
                json!(python_int(&value).ok_or_else(bad)?)
            }
            NEW_CANVAS_SIZE => {
                let items = value.as_array().ok_or_else(bad)?;
                let v: Option<Vec<i64>> = items.iter().map(python_int).collect();
                match v.as_deref() {
                    Some(&[w, h]) => json!([w, h]),
                    _ => return Err(bad()),
                }
            }
            THEME => one_of(&value, &[SYSTEM, "light", "dark"]).ok_or_else(bad)?,
            LANGUAGE => one_of(&value, &[SYSTEM, "ja", "en"]).ok_or_else(bad)?,
            TIME_DISPLAY => one_of(&value, &["frames", "seconds_frames"]).ok_or_else(bad)?,
            BACKGROUND_COLOR_TOOL => one_of(&value, &["brush", "bucket"]).ok_or_else(bad)?,
            DISPLAY_SHRINK_MODE => one_of(&value, &["nearest", "keep_lines"]).ok_or_else(bad)?,
            ACCENT => match value.as_str() {
                Some(SYSTEM) => json!(SYSTEM),
                Some(text) => json!(hex(parse_hex(text).ok_or_else(bad)?)),
                None => return Err(bad()),
            },
            BACKGROUND_CHECKER_COLOR => {
                json!(hex(value.as_str().and_then(parse_hex).ok_or_else(bad)?))
            }
            MAP_PALETTE => {
                // 既定と同じなら設定から消す（preferences.set_map_palette）。
                let colors: Option<Vec<[u8; 3]>> = value
                    .as_array()
                    .ok_or_else(bad)?
                    .iter()
                    .map(|v| v.as_str().and_then(parse_hex))
                    .collect();
                let colors = colors.ok_or_else(bad)?;
                if colors == DEFAULT_MAP_PALETTE {
                    Value::Null
                } else {
                    json!(colors.into_iter().map(hex).collect::<Vec<_>>())
                }
            }
            SKIPPED_UPDATE => match value.as_str() {
                Some("") | None => Value::Null,
                Some(text) => json!(text),
            },
            WORKSPACES => match value {
                Value::Object(map) if map.is_empty() => Value::Null,
                Value::Object(map) => Value::Object(map),
                _ => return Err(bad()),
            },
            // 筆圧のプリセット（pressure_settings.save_presets）。中身は筆圧の窓が丸める。
            PRESSURE_PRESETS => match value {
                Value::Array(items)
                    if items.iter().all(|e| {
                        e.get("name")
                            .and_then(Value::as_str)
                            .is_some_and(|n| !n.trim().is_empty())
                    }) =>
                {
                    Value::Array(items)
                }
                _ => return Err(bad()),
            },
            ACTIVE_WORKSPACE | LAYOUT_PAPER_LAST | PRESSURE_ACTIVE => match value {
                Value::String(_) | Value::Null => value,
                _ => return Err(bad()),
            },
            _ => return Err(format!("知らない設定です：{key}")),
        };
        self.set(key, stored)
    }
}

/// 画面へ渡す設定の全体（丸めたあとの値）。
#[derive(Debug, Serialize)]
pub struct Resolved {
    pub language: String,
    pub time_display_mode: String,
    pub background_color_tool: String,
    pub keep_initial_state_on_cut_folder: bool,
    pub autosave_enabled: bool,
    pub autosave_interval_minutes: i64,
    pub check_updates_automatically: bool,
    pub skipped_update_version: String,
    pub tool_hold_switch_enabled: bool,
    pub tool_hold_switch_ms: i64,
    pub theme: String,
    pub accent: String,
    pub background_checker_enabled: bool,
    pub background_checker_color: String,
    pub new_canvas_size: (i64, i64),
    pub map_color_palette: Vec<String>,
    pub display_shrink_mode: String,
    pub undo_max_steps: i64,
    pub undo_max_memory_mb: i64,
    pub layout_papers: Vec<Value>,
    pub layout_paper_last: Value,
    pub pressure_presets: Value,
    pub pressure_active_preset: Value,
    pub workspaces: Value,
    pub active_workspace: Value,
    pub config_path: String,
    pub migrated_from_qt: Option<String>,
}

/// この画面の設定のフォルダー（tauri の app_config_dir）。確かめ用に `PMA_CONFIG_DIR` で
/// 別の場所にできる。
pub fn config_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    use tauri::Manager;
    match std::env::var_os("PMA_CONFIG_DIR").filter(|v| !v.is_empty()) {
        Some(dir) => Ok(PathBuf::from(dir)),
        None => app.path().app_config_dir().map_err(|e| e.to_string()),
    }
}

/// Qt 版の設定ファイル（config._config_paths）。
pub fn qt_config_path() -> Option<PathBuf> {
    if cfg!(windows) {
        let base = std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .or_else(|| home().map(|h| h.join("AppData").join("Roaming")))?;
        Some(base.join(QT_APP_NAME).join("config.json"))
    } else {
        let base = std::env::var_os("XDG_CONFIG_HOME")
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
            .or_else(|| home().map(|h| h.join(".config")))?;
        Some(base.join(QT_APP_NAME).join("config.json"))
    }
}

fn home() -> Option<PathBuf> {
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from)
}

fn read_object(path: &Path) -> Option<Map<String, Value>> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str::<Value>(&text)
        .ok()?
        .as_object()
        .cloned()
}

/// Python の bool(value)。
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Python の int(value)（真偽・整数・小数は切り捨て・整数の文字列）。読めなければ None。
fn python_int(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Number(n) => n.as_i64().or_else(|| {
            n.as_f64()
                .filter(|f| f.is_finite())
                .map(|f| f.trunc() as i64)
        }),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

fn one_of(value: &Value, choices: &[&str]) -> Option<Value> {
    value
        .as_str()
        .filter(|v| choices.contains(v))
        .map(|v| Value::String(v.to_string()))
}

/// "#rrggbb"（# は無くてもよい）を読む（preferences._rgb_from_hex）。
pub fn parse_hex(text: &str) -> Option<[u8; 3]> {
    let text = text.trim().trim_start_matches('#');
    if text.len() != 6 || !text.is_ascii() {
        return None;
    }
    let part = |i: usize| u8::from_str_radix(&text[i..i + 2], 16).ok();
    Some([part(0)?, part(2)?, part(4)?])
}

pub fn hex([r, g, b]: [u8; 3]) -> String {
    format!("#{r:02x}{g:02x}{b:02x}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_match_qt() {
        let p = Prefs::in_memory(json!({}));
        let r = p.resolved();
        assert!(r.autosave_enabled);
        assert_eq!(r.autosave_interval_minutes, 3);
        assert_eq!(r.undo_max_steps, 30);
        assert_eq!(r.undo_max_memory_mb, 768);
        assert_eq!(r.new_canvas_size, (1280, 720));
        assert!(r.check_updates_automatically);
        assert!(!r.tool_hold_switch_enabled);
        assert_eq!(r.tool_hold_switch_ms, 0);
        assert_eq!(r.language, "system");
        assert_eq!(r.time_display_mode, "frames");
        assert_eq!(r.background_checker_color, "#ffffff");
        assert_eq!(r.map_color_palette[0], "#e6194b");
        assert_eq!(r.display_shrink_mode, "nearest");
    }

    #[test]
    fn clamps_like_qt() {
        let p = Prefs::in_memory(json!({
            "autosave_interval_minutes": 0,
            "undo_max_steps": "9999",
            "undo_max_memory_mb": 1.9,
            "new_canvas_size": [10, 99999],
            "tool_hold_switch_ms": "abc",
            "ui_theme": "neon",
            "ui_accent": "#12345",
            "map_color_palette": ["#000000", 3],
        }));
        assert_eq!(p.autosave_interval_minutes(), 1);
        assert_eq!(p.undo_max_steps(), 500);
        assert_eq!(p.undo_max_memory_mb(), 128);
        assert_eq!(p.new_canvas_size(), (64, 16384));
        assert_eq!(p.tool_hold_ms(), 0);
        assert!(["system", "light"].contains(&p.theme().as_str()));
        assert_ne!(p.accent(), "#12345");
        let palette = p.map_palette();
        assert_eq!(palette[0], "#000000");
        assert_eq!(palette[1], "#3cb44b");
        let bad = Prefs::in_memory(json!({"new_canvas_size": [1, 2, 3]}));
        assert_eq!(bad.new_canvas_size(), (1280, 720));
    }

    #[test]
    fn recent_files_like_qt() {
        let mut p = Prefs::in_memory(json!({}));
        for i in 0..12 {
            p.remember_recent(Path::new(&format!("/nowhere/{i}.pman")))
                .unwrap();
        }
        let list = p.recent_projects();
        assert_eq!(list.len(), RECENT_PROJECTS_LIMIT);
        assert_eq!(list[0], "/nowhere/11.pman");
        p.remember_recent(Path::new("/nowhere/5.pman")).unwrap();
        assert_eq!(p.recent_projects()[0], "/nowhere/5.pman");
        assert_eq!(p.recent_projects().len(), RECENT_PROJECTS_LIMIT);
        p.forget_recent("/nowhere/5.pman").unwrap();
        assert!(!p.recent_projects().contains(&"/nowhere/5.pman".to_string()));
    }

    #[test]
    fn set_checked_writes_qt_shapes() {
        let mut p = Prefs::in_memory(json!({}));
        p.set_checked(MAP_PALETTE, json!(DEFAULT_MAP_PALETTE.map(hex)))
            .unwrap();
        assert!(p.get(MAP_PALETTE).is_none());
        p.set_checked(ACCENT, json!("#ABCDEF")).unwrap();
        assert_eq!(p.get(ACCENT), Some(&json!("#abcdef")));
        p.set_checked(NEW_CANVAS_SIZE, json!([1920, 1080])).unwrap();
        assert_eq!(p.new_canvas_size(), (1920, 1080));
        p.set_checked(SKIPPED_UPDATE, json!("")).unwrap();
        assert!(p.get(SKIPPED_UPDATE).is_none());
        assert!(p.set_checked(THEME, json!("neon")).is_err());
        assert!(p.set_checked("nope", json!(1)).is_err());
    }

    #[test]
    fn migrates_qt_settings_once() {
        let dir = std::env::temp_dir().join(format!("pma-prefs-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let qt = dir.join("qt.json");
        std::fs::write(
            &qt,
            r#"{"ui_theme":"dark","workspaces":{"a":{}},"recent_projects":["/x.pman"]}"#,
        )
        .unwrap();
        let tauri = dir.join("tauri");
        let p = Prefs::load(&tauri, Some(qt.clone()));
        assert_eq!(p.theme(), "dark");
        assert_eq!(p.get(WORKSPACES), Some(&json!({"a": {}})));
        assert_eq!(p.recent_projects(), vec!["/x.pman".to_string()]);
        assert!(tauri.join("config.json").is_file());
        // 2回目は Tauri の設定を読む（Qt 版を変えても写し直さない）。
        std::fs::write(&qt, r#"{"ui_theme":"light"}"#).unwrap();
        assert_eq!(Prefs::load(&tauri, Some(qt)).theme(), "dark");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
