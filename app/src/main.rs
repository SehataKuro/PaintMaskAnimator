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

/// 画面からの報告を標準出力へ出す（自己確認の結果）。
#[tauri::command]
fn report(line: String) {
    println!("{line}");
}

#[tauri::command]
fn quit(app: tauri::AppHandle, code: i32) {
    app.exit(code);
}

fn main() {
    tauri::Builder::default()
        .manage::<Shared>(std::sync::Mutex::new(Session::new(1920, 1080)))
        .setup(|app| {
            pen::install(app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            startup,
            pen_selftest_step,
            canvas_size,
            read_canvas,
            stroke,
            report,
            quit
        ])
        .run(tauri::generate_context!())
        .expect("画面を開けなかった");
}
