//! タイムリマップの貼り付け（Qt 版の `TimeRemapController.show_paste_dialog`・
//! `paste_on_timeline` と `widgets.TimeRemapPasteDialog`）。
//!
//! 読み取り・列・既定の紐づけ・使う行の絞り込み・当てる前の確かめは pma-core の
//! `export::time_remap_paste`。ここはそれを今の作品へつなぎ、文書の変更を
//! `Session::edit_with`（「全体編集」）で行う。画面は ui/time_remap.js。
//!
//! 文言は翻訳前の文と差し込みの値（[`TextView`]）で返し、画面が訳す。

use crate::session::{current_index, Session};
use crate::timeline::Mode;
use pma_core::export::time_remap_paste::{self as paste, RemapChoice, RemapPlan, Text};
use serde::Serialize;
use std::process::{Command, Stdio};
use tauri::AppHandle;

/// 翻訳前の文と差し込みの値。
#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct TextView {
    pub template: String,
    pub values: Vec<(String, String)>,
}

impl From<Text> for TextView {
    fn from(text: Text) -> Self {
        Self {
            template: text.template,
            values: text.values,
        }
    }
}

impl From<&str> for TextView {
    fn from(text: &str) -> Self {
        Text::new(text).into()
    }
}

/// 表の1列。
#[derive(Clone, Debug, Serialize)]
pub struct ColumnView {
    pub uid: String,
    pub name: String,
    pub group: &'static str,
    pub bindable: bool,
    pub display_values: Vec<String>,
}

/// 表の中身（`_update_timesheet_preview`）。
#[derive(Clone, Debug, Serialize)]
pub struct Preview {
    pub format: String,
    pub start_frame: usize,
    pub columns: Vec<ColumnView>,
    /// 列 uid → レイヤー番号（既定の紐づけ、紐づけた順）。
    pub bindings: Vec<(String, usize)>,
    /// 今のコマのレイヤー（番号, 名前）。
    pub layers: Vec<(usize, String)>,
}

/// 当てる前の確かめ。
#[derive(Clone, Debug, Serialize)]
pub struct PlanView {
    pub title: TextView,
    /// 訳してからつなぐ部分。
    pub message: Vec<TextView>,
    /// 1行ずつ訳して改行でつなぎ、`message[0]` の `{links}` へ入れる行。
    pub links: Vec<TextView>,
}

impl From<&RemapPlan> for PlanView {
    fn from(plan: &RemapPlan) -> Self {
        Self {
            title: plan.title.clone().into(),
            message: plan.message.iter().cloned().map(Into::into).collect(),
            links: plan.links.iter().cloned().map(Into::into).collect(),
        }
    }
}

fn layers(s: &Session) -> Vec<(usize, String)> {
    let Some(p) = s.project.as_ref() else {
        return Vec::new();
    };
    let doc = &p.document;
    if doc.frame_order().is_empty() {
        return Vec::new();
    }
    doc.track_order()
        .iter()
        .enumerate()
        .filter_map(|(i, id)| Some((i, doc.track(*id)?.settings.name.clone())))
        .collect()
}

/// 貼り付けた文字を読んで表にする。
pub fn preview(s: &Session, text: &str, cell_name: &str) -> Result<Preview, TextView> {
    let sheet = paste::parse_text(text)?;
    let columns = paste::sheet_columns_from_parsed(&sheet, cell_name);
    let layers = layers(s);
    let active = s
        .project
        .as_ref()
        .map_or(0, |p| current_index(&p.document).1);
    Ok(Preview {
        format: sheet.format.clone(),
        start_frame: sheet.start_frame,
        bindings: paste::default_layer_bindings(&columns, &layers, active),
        columns: columns
            .into_iter()
            .map(|c| ColumnView {
                uid: c.uid,
                name: c.name,
                group: c.group.id(),
                bindable: c.bindable,
                display_values: c.display_values,
            })
            .collect(),
        layers,
    })
}

/// 使う行と紐づけで確かめる。使う行が無ければ Ok(None)。
fn plan(
    s: &Session,
    text: &str,
    cell_name: &str,
    included: &[bool],
    bindings: &[(String, usize)],
) -> Result<Option<(RemapChoice, RemapPlan)>, TextView> {
    let sheet = paste::parse_text(text)?;
    let Some(choice) = paste::parsed_result(&sheet, cell_name, included, bindings) else {
        return Ok(None);
    };
    let p = s.project.as_ref().ok_or("タイムラインがありません。")?;
    let doc = &p.document;
    let planned = if choice.uses_bindings() {
        let names: Vec<String> = layers(s).into_iter().map(|(_, n)| n).collect();
        paste::plan_xdts_bindings(doc, &choice, &names)?
    } else {
        let (_, layer) = current_index(doc);
        paste::plan_active_layer(doc, &choice, layer)?
    };
    Ok(Some((choice, planned)))
}

/// 確かめた中身を当てる（`set_mode("sheet")`・`push_doc_undo`・`apply_sheet_remap`）。
/// 何も変わらなければ None、変えたらステータスの知らせ。
pub fn apply(
    s: &mut Session,
    text: &str,
    cell_name: &str,
    included: &[bool],
    bindings: &[(String, usize)],
) -> Result<Option<TextView>, TextView> {
    let Some((_, planned)) = plan(s, text, cell_name, included, bindings)? else {
        return Err("使用するタイムシート行がありません。".into());
    };
    let current = s
        .project
        .as_ref()
        .map_or(0, |p| current_index(&p.document).0);
    s.timeline_set_mode(Mode::Sheet)
        .map_err(|e| TextView::from(e.as_str()))?;
    let changed = s
        .edit_with(Some("全体編集"), |doc, _| {
            let changes = paste::apply_plan(doc, &planned, current).map_err(|e| e.to_string())?;
            let changed = !changes.is_empty();
            Ok((changed, changed))
        })
        .map_err(|e| TextView::from(e.as_str()))?;
    Ok(changed.then(|| planned.status.clone().into()))
}

/// タイムラインで貼り付けたときの結果（`paste_on_timeline`）。
#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum PasteOutcome {
    /// タイムシートの文字ではない（呼び出し側が通常の貼り付けをする）。
    NotTimesheet,
    /// 紐づけ先を決められないので、貼り付けの窓で選んでもらう。
    Dialog,
    /// 反映した（知らせ。何も変わらなければ None）。
    Applied { status: Option<TextView> },
    /// 反映できなかった（「タイムラインへ反映できませんでした。」の窓に出す理由）。
    Failed { reason: TextView },
}

/// タイムラインでの貼り付け。確かめずにそのまま反映する。
pub fn paste_on_timeline(s: &mut Session, text: &str, cell_name: &str) -> PasteOutcome {
    if text.trim().is_empty() {
        return PasteOutcome::NotTimesheet;
    }
    let Ok(sheet) = paste::parse_text(text) else {
        return PasteOutcome::NotTimesheet;
    };
    let columns = paste::sheet_columns_from_parsed(&sheet, cell_name);
    if columns.is_empty() {
        return PasteOutcome::Failed {
            reason: "使用するタイムシート行がありません。".into(),
        };
    }
    let bindings = if sheet.format.starts_with("XDTS") && !sheet.sheet_columns.is_empty() {
        let layers = layers(s);
        let active = s
            .project
            .as_ref()
            .map_or(0, |p| current_index(&p.document).1);
        let bindings = paste::default_layer_bindings(&columns, &layers, active);
        if bindings.is_empty() {
            return PasteOutcome::Dialog;
        }
        bindings
    } else {
        if !columns.iter().any(|c| !c.states.is_empty()) {
            return PasteOutcome::Failed {
                reason: "使用するタイムシート行がありません。".into(),
            };
        }
        Vec::new()
    };
    match apply(s, text, cell_name, &[], &bindings) {
        Ok(status) => PasteOutcome::Applied { status },
        Err(reason) => PasteOutcome::Failed { reason },
    }
}

/// OS のクリップボードの文字（WKWebView の navigator.clipboard は貼り付けの確かめを出すので、
/// export_copy_text と同じく OS の道具で読む）。
fn clipboard_text() -> String {
    let mut command = if cfg!(target_os = "windows") {
        let mut c = Command::new("powershell");
        c.args(["-NoProfile", "-Command", "Get-Clipboard -Raw"]);
        c
    } else if cfg!(target_os = "macos") {
        Command::new("pbpaste")
    } else {
        let mut c = Command::new("xclip");
        c.args(["-selection", "clipboard", "-o"]);
        c
    };
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    command
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .map(|out| String::from_utf8_lossy(&out.stdout).into_owned())
        .unwrap_or_default()
}

/// XDTS などのファイルを文字として読む（utf-8-sig）。
fn read_text(path: &std::path::Path) -> Result<String, TextView> {
    let bytes = std::fs::read(path)
        .map_err(|e| TextView::from(Text::new("読み込めませんでした。\n\n{exc}").with("exc", e)))?;
    let text = String::from_utf8(bytes)
        .map_err(|e| TextView::from(Text::new("読み込めませんでした。\n\n{exc}").with("exc", e)))?;
    Ok(text.trim_start_matches('\u{feff}').to_string())
}

// ---------------------------------------------------------------------------
// 画面の窓口
// ---------------------------------------------------------------------------

const SOURCE: &str = "time_remap";

#[tauri::command]
pub fn time_remap_clipboard() -> String {
    clipboard_text()
}

#[tauri::command]
pub fn time_remap_preview(
    session: tauri::State<crate::session::Shared>,
    text: String,
    cell_name: String,
) -> Result<Preview, TextView> {
    let s = session.lock().unwrap();
    preview(&s, &text, &cell_name)
}

#[tauri::command]
pub fn time_remap_set_binding(
    mut bindings: Vec<(String, usize)>,
    uid: String,
    layer: Option<usize>,
) -> Vec<(String, usize)> {
    paste::set_layer_binding(&mut bindings, &uid, layer);
    bindings
}

#[tauri::command]
pub fn time_remap_plan(
    session: tauri::State<crate::session::Shared>,
    text: String,
    cell_name: String,
    included: Vec<bool>,
    bindings: Vec<(String, usize)>,
) -> Result<Option<PlanView>, TextView> {
    let s = session.lock().unwrap();
    Ok(plan(&s, &text, &cell_name, &included, &bindings)?.map(|(_, p)| PlanView::from(&p)))
}

#[tauri::command]
pub fn time_remap_apply(
    app: AppHandle,
    text: String,
    cell_name: String,
    included: Vec<bool>,
    bindings: Vec<(String, usize)>,
) -> Result<Option<TextView>, TextView> {
    crate::with_session(&app, SOURCE, |s| {
        apply(s, &text, &cell_name, &included, &bindings)
    })
}

#[tauri::command]
pub fn time_remap_paste_on_timeline(app: AppHandle, cell_name: String) -> PasteOutcome {
    let text = clipboard_text();
    crate::with_session(&app, SOURCE, |s| paste_on_timeline(s, &text, &cell_name))
}

/// 「XDTS／TDTSを読み込む…」：ファイルを選んで文字を返す（やめたら None）。
#[tauri::command]
pub async fn time_remap_pick_file(
    app: AppHandle,
    title: String,
    filter: String,
) -> Result<Option<String>, TextView> {
    use tauri_plugin_dialog::DialogExt;
    let picked = app
        .dialog()
        .file()
        .set_title(&title)
        .add_filter(&filter, &["xdts", "xtds", "tdts"])
        .blocking_pick_file()
        .and_then(|p| p.into_path().ok());
    match picked {
        Some(path) => read_text(&path).map(Some),
        None => Ok(None),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn open(name: &str) -> Session {
        let mut s = Session::empty(8, 8);
        s.open(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../tests/fixtures/projects/v2")
                .join(name),
        )
        .unwrap();
        s
    }

    const AE: &str = "Adobe After Effects 8.0 Keyframe Data\n\n\tUnits Per Second\t24\n\nTime Remap\n\tFrame\tseconds\n\t0\t0\n\t2\t0.041667\n\nEnd of Keyframe Data\n";

    #[test]
    fn after_effects_text_applies_to_the_active_layer() {
        let mut s = open("basic-bank-sheet.pman");
        let layer = current_index(&s.project.as_ref().unwrap().document).1;
        let bank = paste::source_bank(&s.project.as_ref().unwrap().document, layer);
        let number = bank.iter().position(Option::is_some).expect("絵がある") + 1;
        let text = AE.replace(
            "\t2\t0.041667",
            &format!("\t2\t{:.6}", (number - 1) as f64 / 24.0),
        );
        let view = preview(&s, &text, "セル").unwrap();
        assert_eq!(view.format, "Adobe After Effects");
        assert_eq!(view.columns[0].display_values.len(), 3);
        let missing = apply(&mut s, AE, "セル", &[true, true, true], &view.bindings);
        if bank.get(1).is_none_or(Option::is_none) {
            assert!(missing.unwrap_err().template.contains("存在しない絵番号"));
            assert!(s.history().undo.is_empty());
        }
        let status = apply(&mut s, &text, "セル", &[true, false, true], &view.bindings)
            .unwrap()
            .unwrap();
        assert!(status.template.contains("フレームへ反映しました"));
        assert!(!s.history().undo.is_empty());
    }

    #[test]
    fn paste_on_timeline_ignores_plain_text() {
        let mut s = open("basic-bank-sheet.pman");
        assert!(matches!(
            paste_on_timeline(&mut s, "hello", "セル"),
            PasteOutcome::NotTimesheet
        ));
        assert!(s.history().undo.is_empty());
    }
}
