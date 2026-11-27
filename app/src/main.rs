//! PaintMaskAnimator の画面（Tauri）。0.9.0 で Qt の画面と置き換える。
//!
//! 画面は ui/ の HTML と JavaScript。計算とデータは `pma-core`（native/core）を使う予定。
//! 今あるのは骨組みと、ペンの筆圧を画面へ送る部品（pen.rs）だけ。
//!
//! - `cargo run --release`：普通に開く
//! - `PMA_PEN_SELFTEST=1 cargo run --release`：筆圧の経路を自分で確かめて終わる（macOS）
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod history;
mod pen;
mod pen_selftest;
mod session;

use session::{pack_rect, Point, Session, Shared, StrokeTool, Summary};
use tauri::ipc::Response;
use tauri::State;

/// 画面が知りたい起動の情報。
#[derive(serde::Serialize)]
struct Startup {
    /// macOS では筆圧を `pen` の事件で送る（PointerEvent の筆圧は使えない）。
    native_pen: bool,
    pen_selftest: bool,
    /// 起動のときに開く .pman（コマンドラインの最初の引数）。
    open: Option<String>,
}

#[tauri::command]
fn startup() -> Startup {
    Startup {
        native_pen: cfg!(target_os = "macos"),
        pen_selftest: std::env::var_os("PMA_PEN_SELFTEST").is_some(),
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
fn open_project(session: State<Shared>, path: String) -> Result<Summary, String> {
    let mut s = session.lock().unwrap();
    s.open(std::path::Path::new(&path))?;
    Ok(s.summary())
}

/// .pman に保存する。path が無ければ開いたときの場所に上書きする。
#[tauri::command]
fn save_project(session: State<Shared>, path: Option<String>) -> Result<String, String> {
    let mut s = session.lock().unwrap();
    let saved = s.save(path.as_deref().map(std::path::Path::new))?;
    Ok(saved.display().to_string())
}

/// レイヤーを1つ足す（Qt 版の「レイヤーを追加」と同じく pma-core の add_track）。
#[tauri::command]
fn add_layer(session: State<Shared>) -> Result<Summary, String> {
    let mut s = session.lock().unwrap();
    s.edit("レイヤーを追加", |doc| {
        doc.add_track().map(|_| ()).map_err(|e| e.to_string())
    })?;
    Ok(s.summary())
}

/// 取り消す。取り消せたら true。
#[tauri::command]
fn undo(session: State<Shared>) -> Result<bool, String> {
    session.lock().unwrap().undo()
}

/// やり直す。やり直せたら true。
#[tauri::command]
fn redo(session: State<Shared>) -> Result<bool, String> {
    session.lock().unwrap().redo()
}

/// 履歴の一覧（ヒストリーパネル用）。
#[tauri::command]
fn history(session: State<Shared>) -> history::Listing {
    session.lock().unwrap().history()
}

/// 今のコマを変える。
#[tauri::command]
fn set_frame(session: State<Shared>, frame: usize) -> Result<Summary, String> {
    let mut s = session.lock().unwrap();
    s.set_frame(frame)?;
    Ok(s.summary())
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

/// 線を終える。変わった範囲の画素を返す。
#[tauri::command]
fn stroke_end(session: State<Shared>, point: Option<Point>) -> Result<Response, String> {
    let mut s = session.lock().unwrap();
    let rect = s.stroke_end(point)?;
    Ok(Response::new(pack_rect(&s.display, rect)))
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
        .manage::<Shared>(std::sync::Mutex::new(Session::empty(1920, 1080)))
        .setup(|app| {
            open_main_window(app)?;
            pen::install(app.handle());
            Ok(())
        })
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
            quit
        ])
        .run(tauri::generate_context!())
        .expect("画面を開けなかった");
}
