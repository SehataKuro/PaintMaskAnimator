//! 画面を Rust にする判断のための試作（Tauri + dockview）。アプリには組み込まない。
//!
//! 画面は ui/ の HTML と JavaScript（ドッキングは dockview-core）。Rust 側は、
//! 台本どおりに動かすときの出力先、終了、ログ、WKWebView の写し（毎フレームの PNG）だけを持つ。
//!
//! - `cargo run --release`：普通に操作する
//! - `PMA_DEMO_OUT=<dir> cargo run --release`：ドッキングの操作を台本どおりに入力し、毎フレームを保存して終わる
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[tauri::command]
fn demo_out() -> Option<String> {
    std::env::var("PMA_DEMO_OUT").ok()
}

mod ime_native;
mod pen_selftest;

#[tauri::command]
fn pen_selftest_mode() -> bool {
    std::env::var_os("PMA_PEN_SELFTEST").is_some()
}

/// 筆圧の自己確認の n 手目のペンの事件を、主スレッドで窓へ渡す。
#[tauri::command]
fn pen_selftest_step(app: tauri::AppHandle, n: usize) -> Result<bool, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    app.run_on_main_thread(move || {
        let _ = tx.send(pen_selftest::step(n, 0.35, 0.35));
    })
    .map_err(|e| e.to_string())?;
    Ok(rx.recv().map_err(|e| e.to_string())?.is_some())
}

#[tauri::command]
fn ime_selftest_mode() -> bool {
    std::env::var_os("PMA_IME_SELFTEST").is_some()
}

/// 日本語入力の自己確認：WKWebView に NSTextInputClient の窓口を呼ぶ（0＝最初の応答者にする、
/// 1・2＝変換中、3＝確定）。
#[cfg(target_os = "macos")]
#[tauri::command]
fn ime_selftest_step(webview: tauri::Webview, step: usize) -> Result<(), String> {
    webview
        .with_webview(move |platform| {
            use objc2_app_kit::NSView;
            let view: &NSView = unsafe { &*(platform.inner() as *const NSView) };
            match step {
                0 => {
                    if let Some(window) = view.window() {
                        window.makeFirstResponder(Some(view));
                    }
                }
                1 => ime_native::marked(view, "せんが"),
                2 => ime_native::marked(view, "線画"),
                _ => ime_native::insert(view, "線画"),
            }
        })
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn latency_mode() -> bool {
    std::env::var_os("PMA_LATENCY").is_some()
}

#[tauri::command]
fn quit(app: tauri::AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn log(line: String) {
    println!("{line}");
}

/// WKWebView の今の表示を PNG で保存する（台本の録画用）。
#[cfg(target_os = "macos")]
#[tauri::command]
async fn snapshot(webview: tauri::Webview, path: String) -> Result<(), String> {
    use std::sync::mpsc;
    let (tx, rx) = mpsc::channel::<Result<(), String>>();
    webview
        .with_webview(move |platform| {
            use block2::RcBlock;
            use objc2_app_kit::{NSBitmapImageFileType, NSBitmapImageRep, NSImage};
            use objc2_foundation::{NSDictionary, NSError};
            use objc2_web_kit::WKWebView;

            let view: &WKWebView = unsafe { &*(platform.inner() as *const WKWebView) };
            let block = RcBlock::new(move |image: *mut NSImage, _error: *mut NSError| {
                let result = (|| -> Result<(), String> {
                    let image = unsafe { image.as_ref() }.ok_or("写しが空")?;
                    let tiff = image.TIFFRepresentation().ok_or("TIFF にできない")?;
                    let rep = NSBitmapImageRep::imageRepWithData(&tiff).ok_or("画像にできない")?;
                    let png = unsafe {
                        rep.representationUsingType_properties(
                            NSBitmapImageFileType::PNG,
                            &NSDictionary::new(),
                        )
                    }
                    .ok_or("PNG にできない")?;
                    std::fs::write(&path, png.to_vec()).map_err(|e| e.to_string())
                })();
                let _ = tx.send(result);
            });
            unsafe { view.takeSnapshotWithConfiguration_completionHandler(None, &block) };
        })
        .map_err(|e| e.to_string())?;
    tauri::async_runtime::spawn_blocking(move || rx.recv().map_err(|e| e.to_string())?)
        .await
        .map_err(|e| e.to_string())?
}

#[cfg(not(target_os = "macos"))]
#[tauri::command]
async fn snapshot(_path: String) -> Result<(), String> {
    Err("この OS では未実装".into())
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            use tauri::Manager;
            if std::env::var_os("PMA_PROTO_DEBUG").is_some() {
                let window = app.get_webview_window("main").unwrap();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_secs(3));
                    let _ = window.eval("document.title = 'dbg:' + typeof window.__TAURI__ + '|' + typeof window['dockview-core'] + '|' + document.querySelectorAll('.dv-tab').length + '|' + (window.__err || '')");
                    std::thread::sleep(std::time::Duration::from_secs(1));
                    eprintln!("title={:?}", window.title());
                });
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![demo_out, ime_selftest_mode, ime_selftest_step, pen_selftest_mode, pen_selftest_step, latency_mode, quit, log, snapshot])
        .run(tauri::generate_context!())
        .expect("Tauri を起動できない");
}
