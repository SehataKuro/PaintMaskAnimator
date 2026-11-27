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
fn popout_out() -> Option<String> {
    std::env::var("PMA_POPOUT_OUT").ok()
}

/// すべての窓の位置（画面の座標、point）。外枠と中身。
#[tauri::command]
fn window_rects(app: tauri::AppHandle) -> Vec<serde_json::Value> {
    use tauri::Manager;
    let mut out = Vec::new();
    for (label, window) in app.webview_windows() {
        let scale = window.scale_factor().unwrap_or(1.0);
        let (Ok(op), Ok(os), Ok(ip), Ok(is)) = (
            window.outer_position(),
            window.outer_size(),
            window.inner_position(),
            window.inner_size(),
        ) else {
            continue;
        };
        out.push(serde_json::json!({
            "label": label,
            "title": window.title().unwrap_or_default(),
            "outer": [op.x as f64 / scale, op.y as f64 / scale, os.width as f64 / scale, os.height as f64 / scale],
            "inner": [ip.x as f64 / scale, ip.y as f64 / scale, is.width as f64 / scale, is.height as f64 / scale],
        }));
    }
    out.sort_by_key(|w| w["label"].as_str().map(|l| (l != "main", l.to_string())));
    out
}

/// 窓を画面の座標（point）へ動かす（台本で、題名の帯を掴んで動かす代わり）。
#[tauri::command]
fn move_window(app: tauri::AppHandle, label: String, x: f64, y: f64) -> Result<(), String> {
    use tauri::Manager;
    let window = app.get_webview_window(&label).ok_or("窓が無い")?;
    window
        .set_position(tauri::LogicalPosition::new(x, y))
        .map_err(|e| e.to_string())
}

/// 窓の大きさと題名を変える（別の窓の題名は dockview がメインの窓の題名を写すので、パネルの名前にする）。
#[tauri::command]
fn shape_window(app: tauri::AppHandle, label: String, width: f64, height: f64, title: String) -> Result<(), String> {
    use tauri::Manager;
    let window = app.get_webview_window(&label).ok_or("窓が無い")?;
    window
        .set_size(tauri::LogicalSize::new(width, height))
        .map_err(|e| e.to_string())?;
    window.set_title(&title).map_err(|e| e.to_string())
}

/// 画面（モニター）の一覧（画面の座標、point）。
#[tauri::command]
fn monitors(app: tauri::AppHandle) -> Vec<[f64; 4]> {
    app.available_monitors()
        .unwrap_or_default()
        .iter()
        .map(|m| {
            let s = m.scale_factor();
            let (p, z) = (m.position(), m.size());
            [p.x as f64 / s, p.y as f64 / s, z.width as f64 / s, z.height as f64 / s]
        })
        .collect()
}

#[tauri::command]
fn close_window(app: tauri::AppHandle, label: String) -> Result<(), String> {
    use tauri::Manager;
    let window = app.get_webview_window(&label).ok_or("窓が無い")?;
    window.close().map_err(|e| e.to_string())
}

/// OS の窓の一覧（NSApplication.windows の見えているもの）。
#[cfg(target_os = "macos")]
#[tauri::command]
fn os_windows(app: tauri::AppHandle) -> Result<Vec<String>, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    app.run_on_main_thread(move || {
        use objc2::MainThreadMarker;
        use objc2_app_kit::NSApplication;
        let ns = NSApplication::sharedApplication(MainThreadMarker::new().unwrap());
        let lines: Vec<String> = ns
            .windows()
            .iter()
            .filter(|w| w.isVisible())
            .map(|w| {
                let f = w.frame();
                let screen = w.screen().map(|s| s.localizedName().to_string()).unwrap_or_default();
                // 中身のビューの子（WKWebView）の位置と大きさ。題名の帯の下まで広がっているかを見る。
                let web = w
                    .contentView()
                    .map(|v| {
                        let c = v.frame();
                        let subs: Vec<String> = v
                            .subviews()
                            .iter()
                            .map(|s| {
                                let f = s.frame();
                                format!("{:.0},{:.0} {:.0}×{:.0}", f.origin.x, f.origin.y, f.size.width, f.size.height)
                            })
                            .collect();
                        format!("中身のビュー {:.0}×{:.0}、その子 [{}]", c.size.width, c.size.height, subs.join("; "))
                    })
                    .unwrap_or_default();
                format!(
                    "#{} 「{}」 画面「{screen}」 位置 ({:.0}, {:.0}) 大きさ {:.0}×{:.0}（中身の領域 {:.0}×{:.0}、{web}）",
                    w.windowNumber(),
                    w.title(),
                    f.origin.x,
                    f.origin.y,
                    f.size.width,
                    f.size.height,
                    w.contentLayoutRect().size.width,
                    w.contentLayoutRect().size.height
                )
            })
            .collect();
        let _ = tx.send(lines);
    })
    .map_err(|e| e.to_string())?;
    rx.recv().map_err(|e| e.to_string())
}

#[cfg(not(target_os = "macos"))]
#[tauri::command]
fn os_windows() -> Vec<String> {
    Vec::new()
}

#[tauri::command]
fn write_text(path: String, text: String) -> Result<(), String> {
    std::fs::write(path, text).map_err(|e| e.to_string())
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
    snapshot_of(webview, path).await
}

/// 名前の窓の WKWebView を PNG で保存する（別の窓の録画用。別の窓の JavaScript は Rust を呼べない）。
#[cfg(target_os = "macos")]
#[tauri::command]
async fn snapshot_window(app: tauri::AppHandle, label: String, path: String) -> Result<(), String> {
    use tauri::Manager;
    let window = app.get_webview_window(&label).ok_or("窓が無い")?;
    snapshot_of(window.as_ref().clone(), path).await
}

#[cfg(target_os = "macos")]
async fn snapshot_of(webview: tauri::Webview, path: String) -> Result<(), String> {
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

#[cfg(not(target_os = "macos"))]
#[tauri::command]
async fn snapshot_window(_label: String, _path: String) -> Result<(), String> {
    Err("この OS では未実装".into())
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            use tauri::Manager;
            // dockview の別の窓（window.open）を、同じ設定の WKWebView を持つ Tauri の窓として開く。
            // 同じ設定でないと、開いた側の JavaScript から中身を動かせない。
            let handle = app.handle().clone();
            let count = std::sync::atomic::AtomicUsize::new(0);
            tauri::WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::App("index.html".into()))
                .title("PMA 試作：Tauri + dockview")
                .inner_size(1280.0, 800.0)
                .on_new_window(move |url, features| {
                    let n = count.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
                    let window = tauri::WebviewWindowBuilder::new(
                        &handle,
                        format!("popout-{n}"),
                        tauri::WebviewUrl::External(url),
                    )
                    .window_features(features)
                    .title("PMA 試作（別の窓）")
                    .build()
                    .expect("別の窓を作れない");
                    println!("別の窓を開いた：popout-{n}");
                    tauri::webview::NewWindowResponse::Create { window }
                })
                .build()?;
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
        .invoke_handler(tauri::generate_handler![demo_out, popout_out, write_text, shape_window, monitors, window_rects, move_window, close_window, os_windows, snapshot_window, ime_selftest_mode, ime_selftest_step, pen_selftest_mode, pen_selftest_step, latency_mode, quit, log, snapshot])
        .run(tauri::generate_context!())
        .expect("Tauri を起動できない");
}
