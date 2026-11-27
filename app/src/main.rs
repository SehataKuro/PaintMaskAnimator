//! PaintMaskAnimator の画面（Tauri）。0.9.0 で Qt の画面と置き換える。
//!
//! 画面は ui/ の HTML と JavaScript。計算とデータは `pma-core`（native/core）を使う予定。
//! 今あるのは骨組みと、ペンの筆圧を画面へ送る部品（pen.rs）だけ。
//!
//! - `cargo run --release`：普通に開く
//! - `PMA_PEN_SELFTEST=1 cargo run --release`：筆圧の経路を自分で確かめて終わる（macOS）
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod pen;
mod pen_selftest;
mod session;
// タイムラインのパネル（timeline.rs）。
mod timeline;

use session::{pack_rect, Point, Session, Shared};
use tauri::ipc::Response;
use tauri::State;

/// 画面が知りたい起動の情報。
#[derive(serde::Serialize)]
struct Startup {
    /// macOS では筆圧を `pen` の事件で送る（PointerEvent の筆圧は使えない）。
    native_pen: bool,
    pen_selftest: bool,
}

#[tauri::command]
fn startup() -> Startup {
    Startup {
        native_pen: cfg!(target_os = "macos"),
        pen_selftest: std::env::var_os("PMA_PEN_SELFTEST").is_some(),
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
    (s.canvas.width(), s.canvas.height())
}

/// 表示する画像の全体（先頭 16 バイトに範囲、続けて RGBA）。
#[tauri::command]
fn read_canvas(session: State<Shared>) -> Response {
    let s = session.lock().unwrap();
    let rect = pma_core::image::Rect {
        x: 0,
        y: 0,
        width: s.canvas.width(),
        height: s.canvas.height(),
    };
    Response::new(pack_rect(&s.canvas, Some(rect)))
}

/// 線の点（画像の座標）を描き、変わった範囲の画素を返す。
#[tauri::command]
fn stroke(session: State<Shared>, points: Vec<Point>, begin: bool) -> Response {
    let mut s = session.lock().unwrap();
    let rect = s.stroke(&points, begin);
    Response::new(pack_rect(&s.canvas, rect))
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
    use tauri::Manager;
    let window = app.get_webview_window(&label).ok_or("窓が無い")?;
    window
        .set_size(tauri::LogicalSize::new(width, height))
        .map_err(|e| e.to_string())?;
    window.set_title(&title).map_err(|e| e.to_string())
}

/// 別の窓を閉じる（dockview がパネルをもとの場所に戻す）。
#[tauri::command]
fn close_window(app: tauri::AppHandle, label: String) -> Result<(), String> {
    use tauri::Manager;
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
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .manage::<Shared>(std::sync::Mutex::new(Session::new(1920, 1080)))
        // タイムラインの状態（文書は Option<Document>。timeline.rs）。
        .manage::<timeline::SharedTimeline>(timeline::shared())
        .setup(|app| {
            open_main_window(app)?;
            pen::install(app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            startup,
            pen_selftest_step,
            canvas_size,
            read_canvas,
            stroke,
            shape_window,
            close_window,
            report,
            quit,
            // タイムライン（timeline.rs）。
            timeline::timeline_view,
            timeline::timeline_debug_load_sample,
            timeline::timeline_debug_options,
            timeline::timeline_select,
            timeline::timeline_select_rect,
            timeline::timeline_set_frame,
            timeline::timeline_navigate,
            timeline::timeline_edit,
            timeline::timeline_undo,
            timeline::timeline_redo,
            timeline::timeline_set_mode,
            timeline::timeline_set_fps,
            timeline::timeline_set_range,
            timeline::timeline_set_options,
            timeline::timeline_normalize_preview,
            timeline::timeline_thumbnail,
            timeline::timeline_play
        ])
        .run(tauri::generate_context!())
        .expect("画面を開けなかった");
}
