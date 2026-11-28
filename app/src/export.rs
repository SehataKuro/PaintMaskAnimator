//! 書き出し（Qt 版の main_window_export.py・MainWindow.save_png／save_tga・
//! TimeRemapController.copy_from_timeline の画面の側）。
//!
//! 何を書くかは pma-core（`export`）が決め、ファイルに書くのは pma-io。ここは文書を写して
//! 渡し、時間のかかるもの（連番・カットフォルダー・影素材・PSD・MP4）は別のスレッドで書いて
//! 進み具合を `export-job` の事件で送る（中止できる）。書き出しは文書を変えないので、
//! 履歴には積まない（Qt と同じ）。
//!
//! 窓（保存先・確かめ・知らせ）と言葉は画面（ui/export.js）が出す。Rust が返すのは数と
//! パスと、失敗のときの文（OS の文）だけ。
//!
//! MP4 は Qt と同じく外の ffmpeg（アプリの隣の ffmpeg(.exe)、なければ PATH）に、
//! 白地の合成をコマの尺の数だけ渡して H.264（libx264・yuv420p）にする。Qt は PNG を
//! 一時フォルダーに書いてから渡すが、ここでは同じ画素を標準入力へ流す（一時ファイルを
//! 作らない）。頭のカット名は画面が透明な画像に描いて `export_slate` で渡す。

use crate::session::Shared;
use crate::shell::ShellShared;
use pma_core::compose::DisplayOptions;
use pma_core::document::model::Document;
use pma_core::export::cut_folder::{
    builtin_presets, guess_cut_number, path_stem, plan_export, CutFolderLayout, ImageFormat,
    Problem, SheetFormat, Values,
};
use pma_core::export::{self, movie, sheet, time_remap};
use pma_core::project::json;
use pma_io::{CutFolderWrite, TargetState};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use tauri::{Emitter, Manager, State};
use tauri_plugin_dialog::DialogExt;

/// 動いている書き出しの中止の印（同時に1つだけ）。
static JOB: Mutex<Option<Arc<AtomicBool>>> = Mutex::new(None);
/// 次の MP4 に重ねるカット名（画面が描いた乗算済み RGBA の切り抜き）。
static SLATE: Mutex<Option<Slate>> = Mutex::new(None);

/// カット名の画像（文字のある所だけの切り抜き）。
struct Slate {
    x: usize,
    y: usize,
    width: usize,
    height: usize,
    rgba: Vec<u8>,
}

/// カットフォルダー書き出しの設定の鍵（Qt の `cut_folder_dialog.CONFIG_KEY`）。
const CUT_FOLDER_KEY: &str = "cut_folder_export";

// ---------------------------------------------------------------------------
// 文書の写し
// ---------------------------------------------------------------------------

struct Snapshot {
    document: Document,
    path: Option<PathBuf>,
    memo: String,
    range_end: Option<i64>,
    fps: u32,
    /// 【Group C】書き出しの合成の表示フィルタ（display_options）。
    display: DisplayOptions,
}

fn snapshot(app: &tauri::AppHandle) -> Result<Snapshot, String> {
    let state = app.state::<Shared>();
    let s = state.lock().unwrap();
    let project = s.project.as_ref().ok_or("作品を開いていません")?;
    Ok(Snapshot {
        document: project.document.clone(),
        path: project.path.clone(),
        memo: crate::project_meta::memo(&project.metadata),
        range_end: s.timeline.range_end.map(|end| end as i64),
        fps: s.timeline.fps,
        display: display_options(&s),
    })
}

/// 書き出しの合成の表示の設定。
// 【Group C】Qt の composite は画面と同じ表示フィルタ（使用色の絞り込み visible_color_rgbs・
// 子色を親色で見せる preview_color_remap・シルエット）を掛ける（canvas.py の
// _display_layer_image）。キャンバスと同じ設定（colors.rs の sync が合わせる）を読む。
fn display_options(s: &crate::session::Session) -> DisplayOptions {
    s.colors.display.clone()
}

// ---------------------------------------------------------------------------
// 窓（保存先）
// ---------------------------------------------------------------------------

/// 保存の窓。initial はファイル名か、フォルダーを含むパス。選ばなければ None。
#[tauri::command]
pub async fn export_pick_save(
    app: tauri::AppHandle,
    title: String,
    initial: String,
    filter: String,
    extensions: Vec<String>,
) -> Option<String> {
    let initial = PathBuf::from(initial);
    let mut dialog = app.dialog().file().set_title(title);
    if !extensions.is_empty() {
        let exts: Vec<&str> = extensions.iter().map(String::as_str).collect();
        dialog = dialog.add_filter(filter, &exts);
    }
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

/// フォルダーを選ぶ窓。選ばなければ None。
#[tauri::command]
pub async fn export_pick_folder(
    app: tauri::AppHandle,
    title: String,
    start: Option<String>,
) -> Option<String> {
    let mut dialog = app.dialog().file().set_title(title);
    if let Some(dir) = start.map(PathBuf::from).filter(|d| d.is_dir()) {
        dialog = dialog.set_directory(dir);
    }
    dialog
        .blocking_pick_folder()
        .and_then(|p| p.into_path().ok())
        .map(|p| p.display().to_string())
}

fn state_name(state: TargetState) -> &'static str {
    match state {
        TargetState::Missing => "missing",
        TargetState::Empty => "empty",
        TargetState::NotEmpty => "not_empty",
        TargetState::File => "file",
    }
}

/// 書き出し先の状態（missing・empty・not_empty・file）。
#[tauri::command]
pub fn export_target_state(path: String) -> Result<&'static str, String> {
    pma_io::target_state(Path::new(&path))
        .map(state_name)
        .map_err(|e| e.to_string())
}

// ---------------------------------------------------------------------------
// 今のコマ（PNG・TGA）・XDTS・タイムリマップ
// ---------------------------------------------------------------------------

/// 今のコマの白地の合成を PNG か TGA で書く（`save_png`・`save_tga`）。
#[tauri::command]
pub fn export_frame(app: tauri::AppHandle, format: String, path: String) -> Result<(), String> {
    let (data, frame, options) = {
        let state = app.state::<Shared>();
        let s = state.lock().unwrap();
        let project = s.project.as_ref().ok_or("作品を開いていません")?;
        let (frame, _) = crate::session::current_index(&project.document);
        (project.document.clone(), frame, display_options(&s))
    };
    let bytes = match format.as_str() {
        "tga" => export::frame_tga(&data, frame, &options),
        _ => export::frame_png(&data, frame, &options),
    }
    .map_err(|e| e.to_string())?;
    std::fs::write(&path, bytes).map_err(|e| e.to_string())
}

/// 今のシートを XDTS で書く（`xdts_dialog`。シートの名前は PaintMaskAnimator）。
#[tauri::command]
pub fn export_xdts(app: tauri::AppHandle, path: String) -> Result<(), String> {
    let snap = snapshot(&app)?;
    let text = sheet::sheet_text(
        &snap.document,
        SheetFormat::Xdts,
        "PaintMaskAnimator",
        &Default::default(),
        snap.range_end,
    )
    .map_err(|e| e.to_string())?;
    pma_io::write_timesheet(Path::new(&path), &text).map_err(|e| e.to_string())
}

/// 選択レイヤーのタイムリマップのキーフレームデータ（`after_effects_text`）。
/// 書けるセルがなければ Err（文は Qt と同じ、画面が訳す）。
#[tauri::command]
pub fn export_time_remap(app: tauri::AppHandle) -> Result<String, String> {
    let snap = snapshot(&app)?;
    let (_, layer) = crate::session::current_index(&snap.document);
    let states = sheet::sheet_numbers(&snap.document, layer).map_err(|e| e.to_string())?;
    time_remap::after_effects_text(&states, snap.fps as f64)
        .ok_or_else(|| "書き出せるセルがありません。絵番号の付いたセルが要ります。".to_string())
}

/// 文字をクリップボードへ（画面の navigator.clipboard が使えないときの控え）。
/// タイムリマップの文字は ASCII だけなので、OS の pbcopy・clip で足りる。
#[tauri::command]
pub fn export_copy_text(text: String) -> Result<(), String> {
    let mut command = if cfg!(target_os = "windows") {
        Command::new("clip")
    } else if cfg!(target_os = "macos") {
        Command::new("pbcopy")
    } else {
        let mut c = Command::new("xclip");
        c.args(["-selection", "clipboard"]);
        c
    };
    hide_console(&mut command);
    let mut child = command
        .stdin(Stdio::piped())
        .spawn()
        .map_err(|e| e.to_string())?;
    child
        .stdin
        .take()
        .ok_or("クリップボードへ書けませんでした")?
        .write_all(text.as_bytes())
        .map_err(|e| e.to_string())?;
    let status = child.wait().map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err("クリップボードへ書けませんでした".into())
    }
}

// ---------------------------------------------------------------------------
// 書き出しの情報（窓に出すもの）
// ---------------------------------------------------------------------------

#[derive(Serialize)]
pub struct CelView {
    layer_index: usize,
    cell: String,
    number: i64,
}

#[derive(Serialize)]
pub struct Info {
    project_path: Option<String>,
    /// プロジェクト名から推したカット番号（`guess_cut_number`）。
    cut_hint: String,
    /// MP4 の頭に重ねるカット名（`cut_name_for`）。
    cut_name: String,
    cels: Vec<CelView>,
    skipped: usize,
    width: usize,
    height: usize,
}

/// 書き出しの窓に要る文書の情報。
#[tauri::command]
pub fn export_info(app: tauri::AppHandle) -> Result<Info, String> {
    let snap = snapshot(&app)?;
    let (cels, skipped) = sheet::cut_folder_cels(&snap.document);
    let path = snap.path.as_ref().map(|p| p.display().to_string());
    let stem = snap
        .path
        .as_ref()
        .and_then(|p| p.file_name())
        .map(|n| path_stem(&n.to_string_lossy()).to_string())
        .unwrap_or_default();
    let (width, height) = snap.document.size();
    Ok(Info {
        cut_hint: if path.is_some() {
            guess_cut_number(&stem)
        } else {
            String::new()
        },
        cut_name: movie::cut_name_for(path.as_deref(), ""),
        project_path: path,
        cels: cels
            .into_iter()
            .map(|c| CelView {
                layer_index: c.layer_index,
                cell: c.cell,
                number: c.number,
            })
            .collect(),
        skipped,
        width,
        height,
    })
}

// ---------------------------------------------------------------------------
// カットフォルダー（構成・プレビュー）
// ---------------------------------------------------------------------------

fn parse_layout(layout: &Value) -> Result<CutFolderLayout, String> {
    let parsed = json::parse(&layout.to_string())
        .map_err(|e| e.to_string())?
        .value;
    CutFolderLayout::from_json(&parsed)
}

fn layout_value(layout: &CutFolderLayout) -> Value {
    layout.to_json().to_value()
}

/// 入力欄の値（作品名・シーン・話数・カット名）。
#[derive(Deserialize, Default)]
pub struct ValuesIn {
    #[serde(default)]
    title: String,
    #[serde(default)]
    scene: String,
    #[serde(default)]
    episode: String,
    #[serde(default)]
    cut: String,
}

impl ValuesIn {
    fn values(&self) -> Values {
        let text = |s: &String| Some(s.trim().to_string());
        Values {
            title: text(&self.title),
            scene: text(&self.scene),
            episode: text(&self.episode),
            cut: text(&self.cut),
            ..Values::default()
        }
    }
}

/// 窓を開くときの設定：保存した値（Qt の config `cut_folder_export`）と組み込みのプリセット。
#[tauri::command]
pub fn export_cut_folder_settings(shell: State<ShellShared>) -> Value {
    let saved = shell
        .lock()
        .unwrap()
        .prefs
        .get(CUT_FOLDER_KEY)
        .cloned()
        .filter(Value::is_object)
        .unwrap_or_else(|| json!({}));
    let saved_layout = saved
        .get("layout")
        .and_then(|l| parse_layout(l).ok())
        .map(|l| layout_value(&l));
    let builtin: Vec<Value> = builtin_presets()
        .into_iter()
        .map(|(name, layout)| json!({"name": name, "layout": layout_value(&layout)}))
        .collect();
    json!({
        "saved": saved,
        "layout": saved_layout,
        "builtin": builtin,
    })
}

/// 構成を検める（プリセットとして保存する前・読んだプリセット）。読めなければ Err。
#[tauri::command]
pub fn export_cut_folder_layout(layout: Value) -> Result<Value, String> {
    parse_layout(&layout).map(|l| layout_value(&l))
}

/// 設定を覚える（Qt の `remember`・`_save_presets`）。data は `cut_folder_export` の値そのもの。
#[tauri::command]
pub fn export_cut_folder_remember(shell: State<ShellShared>, data: Value) -> Result<(), String> {
    shell.lock().unwrap().prefs.set(CUT_FOLDER_KEY, data)
}

#[derive(Serialize)]
pub struct ProblemView {
    kind: &'static str,
    /// missing_value の欄の id。
    field: Option<&'static str>,
    /// bad_name・duplicate の名前。
    name: Option<String>,
    /// bad_name の場所。
    place: Option<&'static str>,
}

#[derive(Serialize)]
pub struct PlanView {
    folder_name: String,
    folders: Vec<String>,
    files: Vec<String>,
    timesheet_path: Option<String>,
    problems: Vec<ProblemView>,
    /// 保存先に同じ名前のものがある。
    existing: bool,
}

fn problem_view(problem: &Problem) -> ProblemView {
    let none = ProblemView {
        kind: "",
        field: None,
        name: None,
        place: None,
    };
    match problem {
        Problem::MissingValue(field) => ProblemView {
            kind: "missing_value",
            field: Some(field.id()),
            ..none
        },
        Problem::NoNumber => ProblemView {
            kind: "no_number",
            ..none
        },
        Problem::Duplicate(path) => ProblemView {
            kind: "duplicate",
            name: Some(path.clone()),
            ..none
        },
        Problem::BadName(name, place) => ProblemView {
            kind: "bad_name",
            name: Some(name.clone()),
            place: Some(place.id()),
            ..none
        },
    }
}

/// 構成と値から、作るフォルダー・ファイルと問題を返す（プレビュー、`plan_export`）。
#[tauri::command]
pub fn export_cut_folder_plan(
    app: tauri::AppHandle,
    layout: Value,
    values: ValuesIn,
    destination: String,
) -> Result<PlanView, String> {
    let layout = parse_layout(&layout)?;
    let cels = {
        let state = app.state::<Shared>();
        let s = state.lock().unwrap();
        let project = s.project.as_ref().ok_or("作品を開いていません")?;
        sheet::cut_folder_cels(&project.document).0
    };
    let plan = plan_export(&layout, &values.values(), &cels);
    let destination = destination.trim();
    let existing = !destination.is_empty()
        && !plan.folder_name.is_empty()
        && Path::new(destination).join(&plan.folder_name).exists();
    Ok(PlanView {
        folders: plan.folders.clone(),
        files: plan.files.iter().map(|(path, _)| path.clone()).collect(),
        timesheet_path: plan.timesheet_path.clone(),
        problems: plan.problems.iter().map(problem_view).collect(),
        folder_name: plan.folder_name,
        existing,
    })
}

// ---------------------------------------------------------------------------
// 時間のかかる書き出し
// ---------------------------------------------------------------------------

/// 進み具合と結果（`export-job` の事件）。
#[derive(Clone, Serialize)]
pub struct JobEvent {
    /// key_sequence・cut_folder・shadow・psd・mp4
    job: &'static str,
    /// progress・done
    phase: &'static str,
    /// MP4 の段（prepare・encode）。
    stage: &'static str,
    value: usize,
    total: usize,
    /// 終わったときの結果（数・パスなど）。
    result: Option<Value>,
    /// 失敗の文。
    error: Option<String>,
    cancelled: bool,
}

impl JobEvent {
    fn progress(job: &'static str, stage: &'static str, value: usize, total: usize) -> Self {
        Self {
            job,
            phase: "progress",
            stage,
            value,
            total: total.max(1),
            result: None,
            error: None,
            cancelled: false,
        }
    }
}

/// 中止を確かめる進み具合の送り手。
struct Reporter {
    app: tauri::AppHandle,
    job: &'static str,
    cancel: Arc<AtomicBool>,
}

impl Reporter {
    fn report(&self, stage: &'static str, value: usize, total: usize) {
        let _ = self.app.emit(
            "export-job",
            JobEvent::progress(self.job, stage, value, total),
        );
    }

    fn cancelled(&self) -> bool {
        self.cancel.load(Ordering::SeqCst)
    }
}

/// 別のスレッドで書き出す。run は Ok(結果) か Err(文)。中止なら cancelled の印で終わる。
fn start_job(
    app: &tauri::AppHandle,
    job: &'static str,
    run: impl FnOnce(&Reporter) -> Result<Value, String> + Send + 'static,
) -> Result<(), String> {
    let cancel = {
        let mut current = JOB.lock().unwrap();
        if current.is_some() {
            return Err("ほかの書き出しが動いています".into());
        }
        let cancel = Arc::new(AtomicBool::new(false));
        *current = Some(cancel.clone());
        cancel
    };
    let reporter = Reporter {
        app: app.clone(),
        job,
        cancel,
    };
    std::thread::spawn(move || {
        reporter.report("", 0, 1);
        let outcome = run(&reporter);
        let cancelled = reporter.cancelled();
        *JOB.lock().unwrap() = None;
        let (result, error) = match outcome {
            Ok(value) => (Some(value), None),
            Err(error) => (None, Some(error)),
        };
        let _ = reporter.app.emit(
            "export-job",
            JobEvent {
                job,
                phase: "done",
                stage: "",
                value: 0,
                total: 0,
                result: if cancelled { None } else { result },
                error: if cancelled { None } else { error },
                cancelled,
            },
        );
    });
    Ok(())
}

/// 動いている書き出しを止める（次の1枚の前で止まる）。
#[tauri::command]
pub fn export_cancel() {
    if let Some(flag) = JOB.lock().unwrap().as_ref() {
        flag.store(true, Ordering::SeqCst);
    }
}

/// pma-io の進み具合を Reporter へ渡す。中止の印が立っていたら false（次の1枚の前で止まる）。
fn progress_fn(reporter: &Reporter) -> impl FnMut(usize, usize) -> bool + '_ {
    move |value, total| {
        reporter.report("", value, total);
        !reporter.cancelled()
    }
}

/// pma-io の失敗を文にする（中止は空の文。終わりの事件は cancelled で知らせる）。
fn write_error(error: pma_io::WriteError) -> String {
    match error {
        pma_io::WriteError::Cancelled => String::new(),
        pma_io::WriteError::FileInTheWay(_) => "file_in_the_way".into(),
        other => other.to_string(),
    }
}

/// 連番フォルダー名を整える（`key_sequence`）：使えない文字を `_` に、前後の空白とピリオドを
/// 落とす。使えなければ Err（画面が「使用できるフォルダー名を指定してください。」と出す）。
#[derive(Serialize)]
pub struct SequenceTarget {
    destination: String,
    folder_name: String,
    state: &'static str,
}

#[tauri::command]
pub fn export_key_sequence_target(path: String) -> Result<SequenceTarget, String> {
    let chosen = PathBuf::from(&path);
    let raw = chosen
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let invalid = "<>:\"/\\|?*";
    let replaced: String = export::py::strip(&raw)
        .chars()
        .map(|c| if invalid.contains(c) { '_' } else { c })
        .collect();
    let folder_name = export::py::strip_chars(&replaced, " .").to_string();
    if folder_name.is_empty() || folder_name == "." || folder_name == ".." {
        return Err("bad_name".into());
    }
    let destination = chosen
        .parent()
        .map(|p| p.join(&folder_name))
        .unwrap_or_else(|| PathBuf::from(&folder_name));
    let state = pma_io::target_state(&destination).map_err(|e| e.to_string())?;
    Ok(SequenceTarget {
        destination: destination.display().to_string(),
        folder_name,
        state: state_name(state),
    })
}

/// 連番＋TS.csv を書き出す（`key_sequence`）。結果は {count} か {no_keys: true}。
#[tauri::command]
pub fn export_key_sequence_start(
    app: tauri::AppHandle,
    destination: String,
    format: String,
) -> Result<(), String> {
    let snap = snapshot(&app)?;
    let format = if format.eq_ignore_ascii_case("tga") {
        ImageFormat::Tga
    } else {
        ImageFormat::Png
    };
    start_job(&app, "key_sequence", move |reporter| {
        let written = pma_io::write_key_sequence(
            Path::new(&destination),
            &snap.document,
            format,
            &mut progress_fn(reporter),
        )
        .map_err(write_error)?;
        Ok(match written {
            Some(count) => json!({"count": count}),
            None => json!({"no_keys": true}),
        })
    })
}

/// カットフォルダーを書き出す（`write_cut_folder`）。上書きの確認は画面が先に済ませる。
#[tauri::command]
pub fn export_cut_folder_start(
    app: tauri::AppHandle,
    parent: String,
    layout: Value,
    values: ValuesIn,
) -> Result<(), String> {
    let snap = snapshot(&app)?;
    let layout = parse_layout(&layout)?;
    start_job(&app, "cut_folder", move |reporter| {
        let (cels, skipped) = sheet::cut_folder_cels(&snap.document);
        let plan = plan_export(&layout, &values.values(), &cels);
        if !plan.ok() {
            return Err("書き出しの構成に問題があります".into());
        }
        let job = CutFolderWrite {
            document: &snap.document,
            plan: &plan,
            image_format: layout.image_format,
            memo: &snap.memo,
            playback_range_end: snap.range_end,
        };
        let target = pma_io::write_cut_folder(Path::new(&parent), &job, &mut progress_fn(reporter))
            .map_err(write_error)?;
        Ok(json!({
            "folder_name": plan.folder_name,
            "count": plan.files.len(),
            "skipped": skipped,
            "target": target.display().to_string(),
        }))
    })
}

/// 影素材を書き出す（`write_shadow_sequence`）。colors はフォルダーの色。結果は
/// {count} か {nothing: true}。
#[tauri::command]
pub fn export_shadow_start(
    app: tauri::AppHandle,
    destination: String,
    colors: Vec<[u8; 3]>,
) -> Result<(), String> {
    let snap = snapshot(&app)?;
    start_job(&app, "shadow", move |reporter| {
        let written = pma_io::write_shadow_sequence(
            Path::new(&destination),
            &snap.document,
            &colors,
            &mut progress_fn(reporter),
        )
        .map_err(write_error)?;
        Ok(match written {
            Some(count) => json!({"count": count, "destination": destination}),
            None => json!({"nothing": true}),
        })
    })
}

/// PSD を書き出す（`psd_dialog`）。結果は {keys} か {no_keys: true}（ファイルは作らない）。
#[tauri::command]
pub fn export_psd_start(app: tauri::AppHandle, path: String) -> Result<(), String> {
    let snap = snapshot(&app)?;
    start_job(&app, "psd", move |reporter| {
        let written =
            pma_io::write_psd(Path::new(&path), &snap.document, &mut progress_fn(reporter))
                .map_err(write_error)?;
        Ok(match written {
            Some(keys) => json!({"keys": keys, "path": path}),
            None => json!({"no_keys": true}),
        })
    })
}

// ---------------------------------------------------------------------------
// MP4
// ---------------------------------------------------------------------------

/// カット名の画像を受け取る（本体は乗算済み RGBA、x-slate の見出しに "x,y,幅,高さ"）。
/// 空の本体なら消す（カット名なし）。
#[tauri::command]
pub fn export_slate(request: tauri::ipc::Request<'_>) -> Result<(), String> {
    let tauri::ipc::InvokeBody::Raw(body) = request.body() else {
        return Err("カット名の画像がありません".into());
    };
    if body.is_empty() {
        *SLATE.lock().unwrap() = None;
        return Ok(());
    }
    let rect: Vec<usize> = request
        .headers()
        .get("x-slate")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .split(',')
        .filter_map(|v| v.trim().parse().ok())
        .collect();
    let [x, y, width, height] = rect[..] else {
        return Err("カット名の画像の場所がありません".into());
    };
    if body.len() != width * height * 4 {
        return Err("カット名の画像の大きさが違います".into());
    }
    *SLATE.lock().unwrap() = Some(Slate {
        x,
        y,
        width,
        height,
        rgba: body.clone(),
    });
    Ok(())
}

/// 切り抜きを文書の大きさの重ね絵に広げる。
fn slate_overlay(slate: &Slate, width: usize, height: usize) -> Vec<u8> {
    let mut out = vec![0u8; width * height * 4];
    for row in 0..slate.height {
        let y = slate.y + row;
        if y >= height {
            break;
        }
        let columns = slate.width.min(width.saturating_sub(slate.x));
        let src = &slate.rgba[row * slate.width * 4..(row * slate.width + columns) * 4];
        let start = (y * width + slate.x) * 4;
        out[start..start + columns * 4].copy_from_slice(src);
    }
    out
}

fn hide_console(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    let _ = command;
}

/// ffmpeg を探す（Qt：アプリの隣の ffmpeg.exe、なければ PATH）。macOS の Finder から開いた
/// アプリは PATH が短いので、Homebrew の場所も見る。
#[tauri::command]
pub fn export_find_ffmpeg() -> Option<String> {
    let exe = if cfg!(windows) {
        "ffmpeg.exe"
    } else {
        "ffmpeg"
    };
    if let Some(dir) = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
    {
        let candidates = [dir.join(exe), dir.join("../Resources").join(exe)];
        if let Some(found) = candidates.iter().find(|p| p.is_file()) {
            return Some(found.display().to_string());
        }
    }
    let mut dirs: Vec<PathBuf> = std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).collect())
        .unwrap_or_default();
    if cfg!(target_os = "macos") {
        dirs.extend(["/opt/homebrew/bin", "/usr/local/bin"].map(PathBuf::from));
    }
    dirs.into_iter()
        .map(|d| d.join(exe))
        .find(|p| p.is_file())
        .map(|p| p.display().to_string())
}

/// MP4 を書き出す（`mp4`）。ffmpeg は `export_find_ffmpeg` で見つけたもの。
/// 失敗のときは ffmpeg の出力の終わり 1500 文字（Qt と同じ）。
#[tauri::command]
pub fn export_mp4_start(app: tauri::AppHandle, path: String, ffmpeg: String) -> Result<(), String> {
    let snap = snapshot(&app)?;
    let slate = SLATE.lock().unwrap().take();
    start_job(&app, "mp4", move |reporter| {
        encode_mp4(reporter, &snap, slate.as_ref(), &ffmpeg, Path::new(&path))
    })
}

fn encode_mp4(
    reporter: &Reporter,
    snap: &Snapshot,
    slate: Option<&Slate>,
    ffmpeg: &str,
    path: &Path,
) -> Result<Value, String> {
    let document = &snap.document;
    let (width, height) = document.size();
    let order = movie::output_frames(document);
    let total = order.len();
    let overlay = slate.map(|s| slate_overlay(s, width, height));
    let mut command = Command::new(ffmpeg);
    command
        .args(["-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s"])
        .arg(format!("{width}x{height}"))
        .arg("-framerate")
        .arg(snap.fps.max(1).to_string())
        .args(["-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p"])
        .arg(path)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    hide_console(&mut command);
    let mut child = command.spawn().map_err(|e| e.to_string())?;
    let mut stderr = child.stderr.take();
    let log = std::thread::spawn(move || {
        let mut text = String::new();
        if let Some(stderr) = stderr.as_mut() {
            let mut bytes = Vec::new();
            let _ = stderr.read_to_end(&mut bytes);
            text = String::from_utf8_lossy(&bytes).into_owned();
        }
        text
    });
    let mut stdin = child.stdin.take();
    let options = snap.display.clone();
    let mut cached: Option<(usize, Vec<u8>)> = None;
    let mut failure = None;
    for (index, &frame) in order.iter().enumerate() {
        reporter.report("prepare", index, total);
        if reporter.cancelled() {
            break;
        }
        let rgba =
            if index >= movie::LEADING_FRAMES && cached.as_ref().is_some_and(|c| c.0 == frame) {
                cached.as_ref().unwrap().1.clone()
            } else {
                let plain = match movie::movie_frame(document, frame, &options) {
                    Ok(p) => p,
                    Err(e) => {
                        failure = Some(e.to_string());
                        break;
                    }
                };
                cached = Some((frame, plain.clone()));
                let mut rgba = plain;
                if let Some(overlay) = overlay.as_ref().filter(|_| index < movie::LEADING_FRAMES) {
                    let _ = movie::overlay_slate(&mut rgba, overlay);
                }
                rgba
            };
        let Some(pipe) = stdin.as_mut() else { break };
        if pipe.write_all(&rgba).is_err() {
            // ffmpeg が先に終わった（出力に理由がある）。
            break;
        }
    }
    drop(stdin);
    let cancelled = reporter.cancelled();
    if cancelled || failure.is_some() {
        let _ = child.kill();
    }
    reporter.report("encode", 0, 1);
    let status = child.wait().map_err(|e| e.to_string())?;
    let text = log.join().unwrap_or_default();
    if cancelled {
        let _ = std::fs::remove_file(path);
        return Err(String::new());
    }
    if let Some(failure) = failure {
        let _ = std::fs::remove_file(path);
        return Err(failure);
    }
    if !status.success() {
        let chars: Vec<char> = text.chars().collect();
        let tail: String = chars[chars.len().saturating_sub(1500)..].iter().collect();
        return Err(tail);
    }
    Ok(json!({"path": path.display().to_string(), "frames": total}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slate_is_placed_and_clipped() {
        let slate = Slate {
            x: 1,
            y: 1,
            width: 2,
            height: 2,
            rgba: (1..=16).collect(),
        };
        let out = slate_overlay(&slate, 2, 2);
        assert_eq!(&out[..12], &[0; 12]);
        assert_eq!(&out[12..16], &[1, 2, 3, 4]);
    }

    #[test]
    fn key_sequence_names_are_cleaned() {
        let target = export_key_sequence_target("/no/such/dir/a:b. ".into()).unwrap();
        assert_eq!(target.folder_name, "a_b");
        assert!(target.destination.ends_with("a_b"));
        assert_eq!(target.state, "missing");
        assert_eq!(
            export_key_sequence_target("/no/such/..".into()).err(),
            Some("bad_name".into())
        );
    }
}
