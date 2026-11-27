//! メニューバー（Qt 版の main_window_ui_build.build_menu と、ワークスペース・パネル・
//! ヘルプ・新しい版のお知らせのメニュー）。
//!
//! メニューは [`MenuState`] から毎回まるごと組み直す（言語・ショートカット・最近使ったファイル・
//! パネルの表示・ワークスペース・お知らせが変わったとき）。項目の ID は操作の ID
//! （actions.rs、Qt の属性名）か、`recent:3`・`panel:history` のような印。
//!
//! 選んだ項目は主の窓へ `menu-action` の事件（[`MenuAction`]）で送り、画面（shell.js）が
//! 処理する。テキスト入力の中の切り取り・コピーなども画面が見分ける。
//!
//! キーの割り当て：修飾キー（Ctrl・Alt・Shift）付きのキーはメニューの項目に付ける（Qt と同じく
//! メニューに出る）。修飾キーの無いキー（1・A・Delete など）はメニューに付けない。OS の
//! メニューに付けると、テキスト入力に打った文字までメニューが取ってしまうため。それらは画面の
//! キー入力（shortcuts.js）が同じ一覧で受ける。

use crate::actions::{self, Action};
use crate::i18n::tr;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use tauri::menu::{
    CheckMenuItem, IsMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu, SubmenuBuilder,
};
use tauri::{AppHandle, Emitter, Wry};

/// 画面の側が持つメニューの状態（パネルの表示・切り替えの状態・お知らせ）。
#[derive(Clone, Debug, Default, Deserialize)]
pub struct UiMenuState {
    /// [id, 題名, 表示中か]（パネルの並び順）。
    #[serde(default)]
    pub panels: Vec<(String, String, bool)>,
    /// 切り替えの項目の状態（`a_silhouette` など）。
    #[serde(default)]
    pub checked: HashMap<String, bool>,
}

/// メニューを組むのに要るもの全部。
#[derive(Clone, Debug, Default)]
pub struct MenuState {
    pub language: String,
    /// 操作ごとの今のキー（既定か、設定で変えたもの）。
    pub keys: HashMap<String, Vec<String>>,
    pub recent: Vec<String>,
    pub workspaces: Vec<String>,
    pub active_workspace: Option<String>,
    pub ui: UiMenuState,
    /// 新しい版のお知らせ（版の番号）。
    pub update: Option<String>,
}

/// 画面へ送る `menu-action` の事件。
#[derive(Clone, Debug, Serialize)]
pub struct MenuAction {
    pub id: String,
    /// まだ移していない機能（画面は今は知らせるだけ）。
    pub pending: bool,
}

/// Qt の PortableText のキーを、メニューの accelerator にする。修飾キーが無ければ None。
pub fn accelerator(key: &str) -> Option<String> {
    let parts = split_key(key);
    let (key, mods) = parts.split_last()?;
    if mods.is_empty() {
        return None;
    }
    let mut out = Vec::new();
    for m in mods {
        out.push(match m.to_ascii_lowercase().as_str() {
            // Qt の Ctrl は macOS では ⌘（Command）。
            "ctrl" => "CmdOrCtrl",
            // Qt の Meta は macOS では Control。
            "meta" => {
                if cfg!(target_os = "macos") {
                    "Ctrl"
                } else {
                    "Super"
                }
            }
            "alt" => "Alt",
            "shift" => "Shift",
            _ => return None,
        });
    }
    let key = match *key {
        "Return" => "Enter",
        "Del" => "Delete",
        other => other,
    };
    Some(format!("{}+{key}", out.join("+")))
}

/// "Ctrl++" のような最後の "+" をキーとして分ける。
fn split_key(key: &str) -> Vec<&str> {
    if let Some(rest) = key.strip_suffix("++") {
        let mut parts: Vec<&str> = rest.split('+').filter(|s| !s.is_empty()).collect();
        parts.push("+");
        return parts;
    }
    key.split('+').filter(|s| !s.is_empty()).collect()
}

struct Builder<'a> {
    app: &'a AppHandle,
    state: &'a MenuState,
}

impl Builder<'_> {
    fn t(&self, text: &str) -> String {
        tr(&self.state.language, text)
    }

    fn action(&self, id: &str) -> tauri::Result<Box<dyn IsMenuItem<Wry>>> {
        let action: &Action = actions::find(id).expect("一覧に無い操作");
        let accel = self
            .state
            .keys
            .get(id)
            .and_then(|keys| keys.first())
            .and_then(|k| accelerator(k));
        let label = self.t(action.label);
        if action.checkable {
            let checked = self.state.ui.checked.get(id).copied().unwrap_or(false);
            Ok(Box::new(CheckMenuItem::with_id(
                self.app,
                id,
                label,
                true,
                checked,
                accel.as_deref(),
            )?))
        } else {
            Ok(Box::new(MenuItem::with_id(
                self.app,
                id,
                label,
                true,
                accel.as_deref(),
            )?))
        }
    }

    fn item(
        &self,
        id: impl Into<String>,
        label: &str,
        enabled: bool,
    ) -> tauri::Result<Box<dyn IsMenuItem<Wry>>> {
        Ok(Box::new(MenuItem::with_id(
            self.app,
            id.into(),
            label,
            enabled,
            None::<&str>,
        )?))
    }

    fn check(
        &self,
        id: impl Into<String>,
        label: &str,
        enabled: bool,
        checked: bool,
    ) -> tauri::Result<Box<dyn IsMenuItem<Wry>>> {
        Ok(Box::new(CheckMenuItem::with_id(
            self.app,
            id.into(),
            label,
            enabled,
            checked,
            None::<&str>,
        )?))
    }

    fn sep(&self) -> tauri::Result<Box<dyn IsMenuItem<Wry>>> {
        Ok(Box::new(PredefinedMenuItem::separator(self.app)?))
    }

    /// 項目の並び（"-" は区切り、"recent" は最近使ったファイルの小メニュー）。
    fn submenu(&self, title: &str, ids: &[&str]) -> tauri::Result<Submenu<Wry>> {
        let mut items = Vec::new();
        for id in ids {
            items.push(match *id {
                "-" => self.sep()?,
                "recent" => Box::new(self.recent()?),
                id => self.action(id)?,
            });
        }
        let refs: Vec<&dyn IsMenuItem<Wry>> = items.iter().map(|b| b.as_ref()).collect();
        Submenu::with_items(self.app, self.t(title), true, &refs)
    }

    /// ファイル › 最近使用したファイル（main_window_project_io.fill_recent_menu）。
    fn recent(&self) -> tauri::Result<Submenu<Wry>> {
        let mut builder = SubmenuBuilder::new(self.app, self.t("最近使用したファイル"));
        if self.state.recent.is_empty() {
            builder = builder.item(&MenuItem::with_id(
                self.app,
                "recent:none",
                self.t("（なし）"),
                false,
                None::<&str>,
            )?);
            return builder.build();
        }
        for (index, path) in self.state.recent.iter().enumerate() {
            let name = std::path::Path::new(path)
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_else(|| path.clone());
            let n = index + 1;
            // Qt の「&1  名前」の番号（10 番目は 0）。
            let label = if n <= 10 {
                format!("{}  {name}", n % 10)
            } else {
                name
            };
            builder = builder.item(&MenuItem::with_id(
                self.app,
                format!("recent:{index}"),
                label,
                true,
                None::<&str>,
            )?);
        }
        builder = builder.separator().item(&MenuItem::with_id(
            self.app,
            "recent:clear",
            self.t("一覧を消去"),
            true,
            None::<&str>,
        )?);
        builder.build()
    }

    /// ワークスペース（main_window_workspace.refresh_menu）。
    fn workspace(&self) -> tauri::Result<Submenu<Wry>> {
        let s = self.state;
        let mut items = vec![
            self.item("workspace:save", &self.t("現在の配置を保存…"), true)?,
            self.sep()?,
        ];
        let active = s
            .active_workspace
            .as_ref()
            .filter(|a| s.workspaces.contains(a));
        items.push(self.check(
            "workspace:default",
            &self.t("初期設定"),
            true,
            active.is_none(),
        )?);
        if !s.workspaces.is_empty() {
            for (index, name) in s.workspaces.iter().enumerate() {
                items.push(self.check(
                    format!("workspace:{index}"),
                    name,
                    true,
                    Some(name) == active,
                )?);
            }
            items.push(self.sep()?);
            items.push(self.item("workspace:manage", &self.t("ワークスペースを管理…"), true)?);
        }
        let refs: Vec<&dyn IsMenuItem<Wry>> = items.iter().map(|b| b.as_ref()).collect();
        Submenu::with_items(self.app, self.t("ワークスペース"), true, &refs)
    }

    /// パネル（各パネルの toggleViewAction）。
    fn panels(&self) -> tauri::Result<Submenu<Wry>> {
        let mut items = Vec::new();
        for (id, title, visible) in &self.state.ui.panels {
            if id == "canvas" {
                continue;
            }
            items.push(self.check(format!("panel:{id}"), &self.t(title), true, *visible)?);
        }
        let refs: Vec<&dyn IsMenuItem<Wry>> = items.iter().map(|b| b.as_ref()).collect();
        Submenu::with_items(self.app, self.t("パネル"), true, &refs)
    }

    fn help(&self) -> tauri::Result<Submenu<Wry>> {
        let items = [
            self.item("help:check_update", &self.t("更新を確認…"), true)?,
            self.sep()?,
            self.item("help:about", &self.t("バージョン情報…"), true)?,
        ];
        let refs: Vec<&dyn IsMenuItem<Wry>> = items.iter().map(|b| b.as_ref()).collect();
        Submenu::with_items(self.app, self.t("ヘルプ"), true, &refs)
    }

    /// 新しい版のお知らせ（main_window_update_notice._build_menu。macOS の共通メニューバー
    /// にはボタンを置けないので、Qt と同じくメニューを足す）。
    fn update(&self, latest: &str) -> tauri::Result<Submenu<Wry>> {
        let title = self
            .t("新しいバージョン v{latest} があります")
            .replace("{latest}", latest);
        let items = [
            self.item(
                "update:install",
                &self.t("ダウンロードしてインストール…"),
                true,
            )?,
            self.item("update:notes", &self.t("リリースノートを見る"), true)?,
            self.sep()?,
            self.item("update:skip", &self.t("この版は知らせない"), true)?,
        ];
        let refs: Vec<&dyn IsMenuItem<Wry>> = items.iter().map(|b| b.as_ref()).collect();
        Submenu::with_items(self.app, title, true, &refs)
    }

    /// macOS のアプリ名のメニュー（Qt が PreferencesRole の「環境設定…」を移す先）。
    #[cfg(target_os = "macos")]
    fn app_menu(&self) -> tauri::Result<Submenu<Wry>> {
        let quit = MenuItem::with_id(
            self.app,
            "app:quit",
            // .ts に無い言葉（Qt はアプリ名のメニューを OS に任せていた）。
            if self.state.language == "en" {
                "Quit PaintMaskAnimator".to_string()
            } else {
                "PaintMaskAnimator を終了".to_string()
            },
            true,
            Some("CmdOrCtrl+Q"),
        )?;
        SubmenuBuilder::new(self.app, "PaintMaskAnimator")
            .item(self.action("a_preferences")?.as_ref())
            .separator()
            .services()
            .separator()
            .hide()
            .hide_others()
            .show_all()
            .separator()
            .item(&quit)
            .build()
    }

    fn menu(&self) -> tauri::Result<Menu<Wry>> {
        let file = self.submenu(
            "ファイル",
            &[
                "a_new",
                "a_open_project",
                "recent",
                "a_open_cut_folder",
                "a_import_images",
                "a_import_genga",
                "a_import_folder",
                "a_import_images_raw",
                "a_import_psd",
                "a_import_clip",
                "-",
                "a_save_project",
                "a_save_project_as",
                "-",
                "a_save",
                "a_save_tga",
                "a_export_cut_folder",
                "a_export_shadow",
                "a_export_png_seq",
                "a_export_tga_seq",
                "a_export_xdts",
                "a_export_psd",
                "a_export_mp4",
            ],
        )?;
        let mut edit_ids = vec![
            "a_undo",
            "a_redo",
            "-",
            "a_cut",
            "a_copy",
            "a_paste",
            "-",
            "a_silhouette",
            "a_preview_map",
            "a_isolate_color",
            "a_clear_color_filter",
            "a_swap_main_sub",
            "a_binarize_layer",
            "a_reconvert_layer",
            "a_tidy_lines",
            "a_remove_dust",
            "-",
            "a_resize",
            "a_pressure",
            "-",
            "a_memo",
            "-",
            "a_color_chart",
            "-",
            "a_shortcuts",
        ];
        // macOS では「環境設定…」はアプリ名のメニューへ移る（Qt の PreferencesRole）。
        if !cfg!(target_os = "macos") {
            edit_ids.push("a_preferences");
        }
        let edit = self.submenu("編集", &edit_ids)?;
        let auto = self.submenu(
            "自動塗り",
            &[
                "a_auto_paint",
                "-",
                "a_keep_initial_state",
                "a_import_initial_state",
                "a_import_clip_initial_state",
                "a_clear_initial_state",
                "-",
                "a_reference_check",
            ],
        )?;
        let selection = self.submenu(
            "選択範囲",
            &[
                "a_selection_all",
                "a_selection_clear",
                "a_selection_invert",
                "a_selection_delete",
                "-",
                "a_selection_transform",
                "a_selection_scale",
                "a_selection_mesh",
                "-",
                "a_transform_rotate_left",
                "a_transform_rotate_right",
                "a_transform_mesh_grid",
                "-",
                "a_transform_commit",
                "a_transform_cancel",
            ],
        )?;
        let animation = self.submenu(
            "アニメーション",
            &[
                "a_prev",
                "a_next",
                "-",
                "a_tl_previous_key",
                "a_tl_next_key",
                "-",
                "a_tl_paste_time_remap",
                "a_tl_copy_time_remap",
            ],
        )?;
        let workspace = self.workspace()?;
        let panels = self.panels()?;
        let help = self.help()?;
        let update = match &self.state.update {
            Some(latest) => Some(self.update(latest)?),
            None => None,
        };
        let menu = Menu::new(self.app)?;
        #[cfg(target_os = "macos")]
        menu.append(&self.app_menu()?)?;
        for sub in [
            &file, &edit, &auto, &selection, &animation, &workspace, &panels, &help,
        ] {
            menu.append(sub)?;
        }
        if let Some(update) = &update {
            menu.append(update)?;
        }
        Ok(menu)
    }
}

/// メニューを組み直して窓に付ける。
pub fn install(app: &AppHandle, state: &MenuState) -> tauri::Result<()> {
    let menu = Builder { app, state }.menu()?;
    app.set_menu(menu)?;
    Ok(())
}

/// メニューの項目を選んだとき：画面へ送る。
pub fn on_event(app: &AppHandle, id: &str) {
    let pending = actions::find(id).is_some_and(|a| !a.wired);
    let _ = app.emit_to(
        "main",
        "menu-action",
        MenuAction {
            id: id.to_string(),
            pending,
        },
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_qt_keys_to_accelerators() {
        assert_eq!(accelerator("Ctrl+N").as_deref(), Some("CmdOrCtrl+N"));
        assert_eq!(
            accelerator("Ctrl+Shift+S").as_deref(),
            Some("CmdOrCtrl+Shift+S")
        );
        assert_eq!(accelerator("Ctrl++").as_deref(), Some("CmdOrCtrl++"));
        assert_eq!(accelerator("Ctrl+,").as_deref(), Some("CmdOrCtrl+,"));
        assert_eq!(accelerator("Shift+E").as_deref(), Some("Shift+E"));
        assert_eq!(accelerator("1"), None);
        assert_eq!(accelerator("Delete"), None);
        assert_eq!(accelerator(""), None);
    }

    #[test]
    fn menu_items_name_known_actions() {
        // 組み立てに使う ID がすべて一覧にある（無ければ組み立てで止まる）。
        for id in [
            "a_new",
            "a_import_genga",
            "a_export_mp4",
            "a_color_chart",
            "a_tl_copy_time_remap",
            "a_transform_cancel",
            "a_reference_check",
        ] {
            assert!(actions::find(id).is_some(), "{id}");
        }
    }
}
