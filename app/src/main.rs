//! PaintMaskAnimator の画面（Tauri）。0.9.0 で Qt の画面と置き換える。
//!
//! 画面は ui/ の HTML と JavaScript。計算とデータは `pma-core`（native/core）を使う。
//! 開いている作品（文書と履歴）は `Session` に一つだけ持ち、キャンバスもタイムラインも
//! それを読み書きする。文書が変わったら `document-changed` の事件で画面へ知らせる
//! （[`with_session`]）。
//!
//! - `cargo run --release`：普通に開く
//! - `PMA_PEN_SELFTEST=1 cargo run --release`：筆圧の経路を自分で確かめて終わる（macOS）
//! - `PMA_PLAYBACK_SELFTEST=コマ数 cargo run --release`：再生してキャンバスの fps を測り、
//!   報告して終わる（`PMA_TIMELINE_SAMPLE`・`PMA_TIMELINE_SAMPLE_SIZE` で見本の文書を開ける）
//! - `PMA_COLORS_SELFTEST=待つms cargo run --release -- 作品.pman`：色のパネルを画面の操作で動かして
//!   確かめ、報告して終わる（1手ごとに待つので、そのあいだに窓を撮れる）
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// 描画色・カラーサークル・カラースライダー・使用色・カラーチャート（colors.rs）。
mod colors;
mod export; // 書き出し（export.rs）。
mod history;
// 読み込み（imports.rs）：画像・フォルダー・PSD・CLIP・線画の初期状態・レイアウト用紙。
mod imports;
mod paint_ops;
mod pen;
mod pen_selftest;
mod session;
// タイムラインのパネル（timeline.rs）。
mod timeline;
// ツール・ツールプロパティ・アクションのパネル（tools.rs）。
mod tools;
// 【アプリの外枠】メニュー・環境設定・ショートカット・自動保存など（shell.rs ほか）。
mod actions;
mod autosave;
mod i18n;
mod menu;
mod papers;
mod prefs;
mod project_meta;
mod shell;
mod shortcuts;

use session::{pack_rect, Point, Session, Shared, StrokeTool, Summary};
use tauri::ipc::Response;
use tauri::{Emitter, Manager, State};

/// Session を変える操作をし、文書が変わったら `document-changed` の事件で画面へ知らせる。
/// 知らせは錠を外してから送る（受けた画面がすぐ読みに来ても待たせない）。
pub(crate) fn with_session<T>(
    app: &tauri::AppHandle,
    source: &'static str,
    f: impl FnOnce(&mut Session) -> T,
) -> T {
    let state = app.state::<Shared>();
    let (out, changed, synced, palette) = {
        let mut s = state.lock().unwrap();
        let before = s.mark();
        let out = f(&mut s);
        // 使用色パネル（colors.rs）：使用色を数え直し、表示の絞り込みをそろえる。
        let synced = colors::sync(&mut s);
        let palette = synced.palette.then(|| s.palette_view());
        (out, s.changes_since(before, source), synced, palette)
    };
    if let Some(changed) = changed {
        let _ = app.emit("document-changed", changed);
    }
    colors::emit_synced(app, synced, palette);
    out
}

/// 画面が知りたい起動の情報。
#[derive(serde::Serialize)]
struct Startup {
    /// macOS では筆圧を `pen` の事件で送る（PointerEvent の筆圧は使えない）。
    native_pen: bool,
    pen_selftest: bool,
    /// 再生の自己確認で数えるコマ数（PMA_PLAYBACK_SELFTEST）。
    playback_selftest: Option<usize>,
    /// 色のパネルの自己確認（PMA_COLORS_SELFTEST。値は1手ごとに待つ ms）。
    colors_selftest: Option<u64>,
    /// キーの持ち主の自己確認（PMA_TOOLS_SELFTEST。ui/tools_selftest.js）。
    tools_selftest: bool,
    /// 起動のときに開く .pman（コマンドラインの最初の引数）。
    open: Option<String>,
}

#[tauri::command]
fn startup() -> Startup {
    Startup {
        native_pen: cfg!(target_os = "macos"),
        pen_selftest: std::env::var_os("PMA_PEN_SELFTEST").is_some(),
        playback_selftest: std::env::var("PMA_PLAYBACK_SELFTEST")
            .ok()
            .and_then(|v| v.parse().ok()),
        colors_selftest: std::env::var("PMA_COLORS_SELFTEST")
            .ok()
            .and_then(|v| v.parse().ok()),
        tools_selftest: std::env::var_os("PMA_TOOLS_SELFTEST").is_some(),
        open: std::env::args().nth(1),
    }
}

/// 自己確認の n 手目の事件を、主スレッドで窓へ渡す。続きがあれば true。
/// `mouse` のときはペンの線ではなく、マウスの線を事件の列に入れる。
#[tauri::command]
fn pen_selftest_step(app: tauri::AppHandle, n: usize, mouse: bool) -> Result<bool, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    let handle = app.clone();
    app.run_on_main_thread(move || {
        let _ = tx.send(if mouse {
            pen_selftest::mouse_step(&handle, n)
        } else {
            pen_selftest::step(&handle, n)
        });
    })
    .map_err(|e| e.to_string())?;
    match rx.recv().map_err(|e| e.to_string())? {
        Some(true) => Ok(true),
        Some(false) => Err("事件を窓へ渡せなかった".into()),
        None => Ok(false),
    }
}

/// 作品の大きさ（画素）。
#[tauri::command]
fn canvas_size(session: State<Shared>) -> (usize, usize) {
    let s = session.lock().unwrap();
    (s.display.width(), s.display.height())
}

/// 表示する画像の全体（先頭 16 バイトに範囲、続けて RGBA）。
#[tauri::command]
fn read_canvas(session: State<Shared>) -> Response {
    let s = session.lock().unwrap();
    let rect = pma_core::image::Rect {
        x: 0,
        y: 0,
        width: s.display.width(),
        height: s.display.height(),
    };
    Response::new(pack_rect(&s.display, Some(rect)))
}

/// 作品の概要（大きさ・コマ数・レイヤー数・今のコマ・保存先）。
#[tauri::command]
fn summary(session: State<Shared>) -> Summary {
    session.lock().unwrap().summary()
}

/// .pman を開く。
#[tauri::command]
fn open_project(app: tauri::AppHandle, path: String) -> Result<Summary, String> {
    with_session(&app, "open", |s| {
        s.open(std::path::Path::new(&path))?;
        Ok(s.summary())
    })
}

/// .pman に保存する。path が無ければ開いたときの場所に上書きする。
#[tauri::command]
fn save_project(session: State<Shared>, path: Option<String>) -> Result<String, String> {
    let mut s = session.lock().unwrap();
    let saved = s.save(path.as_deref().map(std::path::Path::new))?;
    Ok(saved.display().to_string())
}

/// レイヤーを1つ足す（Qt 版の「レイヤーを追加」と同じく pma-core の add_track。
/// 履歴の名前は Qt の LayerRemoveUndo と同じ「レイヤー構成」）。
#[tauri::command]
fn add_layer(app: tauri::AppHandle) -> Result<Summary, String> {
    with_session(&app, "timeline", |s| {
        s.timeline_apply(timeline::Edit::AddLayer)?;
        Ok(s.summary())
    })
}

/// 取り消す（キャンバスもタイムラインも同じ履歴）。取り消せたら true。
#[tauri::command]
fn undo(app: tauri::AppHandle) -> Result<bool, String> {
    with_session(&app, "history", |s| s.undo())
}

/// やり直す。やり直せたら true。
#[tauri::command]
fn redo(app: tauri::AppHandle) -> Result<bool, String> {
    with_session(&app, "history", |s| s.redo())
}

/// 履歴の一覧（ヒストリーパネル用）。
#[tauri::command]
fn history(session: State<Shared>) -> history::Listing {
    session.lock().unwrap().history()
}

/// 今のコマを変える。
#[tauri::command]
fn set_frame(app: tauri::AppHandle, frame: usize) -> Result<Summary, String> {
    with_session(&app, "frame", |s| {
        s.set_frame(frame)?;
        Ok(s.summary())
    })
}

/// 線を始める（画像の座標）。変わった範囲の画素を返す。
#[tauri::command]
fn stroke_begin(
    session: State<Shared>,
    tool: StrokeTool,
    point: Point,
) -> Result<Response, String> {
    let mut s = session.lock().unwrap();
    let rect = s.stroke_begin(&tool, point)?;
    Ok(Response::new(pack_rect(&s.display, rect)))
}

/// 線の続きの点。変わった範囲の画素を返す。
#[tauri::command]
fn stroke_move(session: State<Shared>, points: Vec<Point>) -> Result<Response, String> {
    let mut s = session.lock().unwrap();
    let rect = s.stroke_move(&points)?;
    Ok(Response::new(pack_rect(&s.display, rect)))
}

/// 線を終える。変わった範囲の画素を返す（キャンバスはこの画素で足りるので、
/// 知らせのもとは `canvas`。タイムラインはサムネイルを読み直す）。
#[tauri::command]
fn stroke_end(app: tauri::AppHandle, point: Option<Point>) -> Result<Response, String> {
    with_session(&app, "canvas", |s| {
        let rect = s.stroke_end(point)?;
        Ok(Response::new(pack_rect(&s.display, rect)))
    })
}

/// 別の窓の大きさと題名を変える（別の窓の題名は dockview が主の窓の題名を写すので、パネルの名前にする）。
#[tauri::command]
fn shape_window(
    app: tauri::AppHandle,
    label: String,
    width: f64,
    height: f64,
    title: String,
) -> Result<(), String> {
    let window = app.get_webview_window(&label).ok_or("窓が無い")?;
    window
        .set_size(tauri::LogicalSize::new(width, height))
        .map_err(|e| e.to_string())?;
    window.set_title(&title).map_err(|e| e.to_string())
}

/// 別の窓を閉じる（dockview がパネルをもとの場所に戻す）。
#[tauri::command]
fn close_window(app: tauri::AppHandle, label: String) -> Result<(), String> {
    let window = app.get_webview_window(&label).ok_or("窓が無い")?;
    window.close().map_err(|e| e.to_string())
}

/// 画面からの報告を標準出力へ出す（自己確認の結果）。
#[tauri::command]
fn report(line: String) {
    println!("{line}");
}

#[tauri::command]
fn quit(app: tauri::AppHandle, code: i32) {
    app.exit(code);
}

/// 主の窓を開く。パネルを別の窓に出すとき（dockview の window.open）は、同じ設定の
/// WebView を持つ窓として開く。同じ設定でないと、開いた側の JavaScript から中身を動かせない。
fn open_main_window(app: &tauri::App) -> tauri::Result<()> {
    use std::sync::atomic::{AtomicUsize, Ordering};
    let handle = app.handle().clone();
    let count = AtomicUsize::new(0);
    tauri::WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::App("index.html".into()))
        .title("PaintMaskAnimator")
        .inner_size(1440.0, 900.0)
        .on_new_window(move |url, features| {
            let n = count.fetch_add(1, Ordering::SeqCst) + 1;
            match tauri::WebviewWindowBuilder::new(
                &handle,
                format!("popout-{n}"),
                tauri::WebviewUrl::External(url),
            )
            .window_features(features)
            .title("PaintMaskAnimator")
            .build()
            {
                Ok(window) => tauri::webview::NewWindowResponse::Create { window },
                Err(_) => tauri::webview::NewWindowResponse::Deny,
            }
        })
        .build()?;
    // 【アプリの外枠】窓を閉じるときは、画面が未保存の変更を確かめてから終わる（shell.js）。
    let handle = app.handle().clone();
    if let Some(window) = app.get_webview_window("main") {
        window.on_window_event(move |event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = handle.emit_to("main", "close-requested", ());
            }
        });
    }
    Ok(())
}

/// 【アプリの外枠】設定を読み、起動の文書（環境設定の大きさの新しい文書）・履歴の上限・
/// メニュー・自動保存・窓を閉じるときの確認を用意する。
fn setup_shell(app: &tauri::App, sample: bool) -> Result<(), Box<dyn std::error::Error>> {
    let dir = prefs::config_dir(app.handle())?;
    let prefs = prefs::Prefs::load(&dir, prefs::qt_config_path());
    {
        let state = app.state::<Shared>();
        let mut s = state.lock().unwrap();
        s.set_history_limits(history::limits(
            prefs.undo_max_steps(),
            prefs.undo_max_memory_mb(),
        ));
        if !sample && std::env::args().nth(1).is_none() {
            let (w, h) = prefs.new_canvas_size();
            if let Err(e) = s.new_document(w as usize, h as usize) {
                eprintln!("新しい文書を作れなかった：{e}");
            }
        }
        let mut sh = shell::Shell::new(prefs);
        sh.mark_saved(s.content_revision());
        app.manage::<shell::ShellShared>(std::sync::Mutex::new(sh));
    }
    app.manage(autosave::Autosave::new(dir.join("autosave.pmap")));
    let handle = app.handle().clone();
    autosave::start(app.handle().clone(), move || {
        let shell = handle.state::<shell::ShellShared>();
        let sh = shell.lock().unwrap();
        (
            sh.prefs.autosave_enabled(),
            sh.prefs.autosave_interval_minutes(),
        )
    });
    shell::refresh_menu(app.handle());
    Ok(())
}

fn main() {
    let mut session = Session::empty(
        prefs::DEFAULT_CANVAS_SIZE.0 as usize,
        prefs::DEFAULT_CANVAS_SIZE.1 as usize,
    );
    // 確かめ用の見本の文書（PMA_TIMELINE_SAMPLE）。.pman を渡したときは、そちらを開く。
    let mut sample = false;
    if std::env::args().nth(1).is_none() {
        sample = std::env::var_os("PMA_TIMELINE_SAMPLE").is_some();
        if let Err(e) = timeline::open_sample_from_env(&mut session) {
            eprintln!("見本の文書を開けなかった：{e}");
        }
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage::<Shared>(std::sync::Mutex::new(session))
        // 読み込み（imports.rs）の途中の状態。
        .manage::<imports::ImportsShared>(Default::default())
        .setup(move |app| {
            setup_shell(app, sample)?;
            open_main_window(app)?;
            pen::install(app.handle());
            // ツールの設定を読む（tools.rs）。
            tools::install(app.handle());
            Ok(())
        })
        // 【アプリの外枠】メニューの項目は画面へ送る（menu.rs）。
        .on_menu_event(|app, event| menu::on_event(app, event.id().as_ref()))
        .invoke_handler(tauri::generate_handler![
            startup,
            pen_selftest_step,
            canvas_size,
            summary,
            open_project,
            save_project,
            set_frame,
            add_layer,
            undo,
            redo,
            history,
            read_canvas,
            stroke_begin,
            stroke_move,
            stroke_end,
            shape_window,
            close_window,
            report,
            quit,
            // タイムライン（timeline.rs）。
            timeline::timeline_view,
            timeline::timeline_debug_options,
            timeline::timeline_select,
            timeline::timeline_select_rect,
            timeline::timeline_set_frame,
            timeline::timeline_navigate,
            timeline::timeline_edit,
            timeline::timeline_set_mode,
            timeline::timeline_set_fps,
            timeline::timeline_set_range,
            timeline::timeline_set_options,
            timeline::timeline_normalize_preview,
            timeline::timeline_thumbnail,
            timeline::timeline_play,
            // 描画色・使用色・カラーチャート（colors.rs）。
            colors::palette_view,
            colors::palette_op,
            colors::chart_view,
            colors::chart_op,
            colors::chart_save_pmag,
            colors::chart_load_pmag,
            colors::replacement_set_save,
            colors::replacement_set_load,
            // ツール（tools.rs）。
            tools::tools_state,
            tools::tools_set,
            tools::tools_set_mask_colors,
            tools::tools_pressure_curve,
            tools::tool_lasso_fill,
            tools::tool_shape,
            tools::tool_line,
            tools::tool_fill,
            tools::tool_transform,
            tools::tool_dust_apply,
            tools::tool_eyedropper,
            tools::tool_sample_color,
            tools::action_run,
            tools::selection_edit,
            tools::selection_delete,
            // 塗りの操作（paint_ops.rs）：バケツ・串刺し塗り・色置換・ゴミ取り・自動塗り・チェック。
            paint_ops::paint_bucket_begin,
            paint_ops::paint_bucket_extend,
            paint_ops::paint_bucket_end,
            paint_ops::paint_skewer_click,
            paint_ops::paint_skewer_key,
            paint_ops::paint_skewer_guide,
            paint_ops::paint_replace_color,
            paint_ops::paint_dust_apply,
            paint_ops::paint_set_used_colors,
            paint_ops::paint_auto_dialog,
            paint_ops::paint_cell_thumbnail,
            paint_ops::paint_auto_run,
            paint_ops::paint_reference_check,
            paint_ops::paint_job_cancel,
            paint_ops::paint_show_finding,
            // 書き出し（export.rs）：今のコマ・連番・カットフォルダー・影素材・XDTS・PSD・MP4・タイムリマップ。
            export::export_pick_save,
            export::export_pick_folder,
            export::export_target_state,
            export::export_frame,
            export::export_xdts,
            export::export_time_remap,
            export::export_copy_text,
            export::export_info,
            export::export_cut_folder_settings,
            export::export_cut_folder_layout,
            export::export_cut_folder_remember,
            export::export_cut_folder_plan,
            export::export_cancel,
            export::export_key_sequence_target,
            export::export_key_sequence_start,
            export::export_cut_folder_start,
            export::export_shadow_start,
            export::export_psd_start,
            export::export_slate,
            export::export_find_ffmpeg,
            export::export_mp4_start,
            // 読み込み（imports.rs）。
            imports::import_pick,
            imports::import_set_material,
            imports::import_inspect,
            imports::import_reduce_preview,
            imports::import_cancel,
            imports::import_scan_antialias,
            imports::import_images,
            imports::import_initial_pending,
            imports::import_keep_initial,
            imports::import_psd,
            imports::import_initial_state,
            // 【アプリの外枠】（shell.rs）。
            shell::shell_init,
            shell::shell_menu_state,
            shell::shell_set_update,
            shell::project_state,
            shell::prefs_get,
            shell::prefs_set,
            shell::shortcuts_get,
            shell::shortcuts_set,
            shell::project_new,
            shell::project_open,
            shell::project_save,
            shell::pick_open_path,
            shell::pick_save_path,
            shell::recent_path,
            shell::recent_clear,
            shell::memo_get,
            shell::memo_set,
            shell::history_graph,
            shell::history_jump,
            shell::history_switch,
            shell::autosave_restore,
            shell::autosave_discard,
            shell::shell_quit,
            shell::set_title,
            shell::open_url,
            papers::papers_list,
            papers::papers_add,
            papers::papers_rename,
            papers::papers_remove
        ])
        .run(tauri::generate_context!())
        .expect("画面を開けなかった");
}
