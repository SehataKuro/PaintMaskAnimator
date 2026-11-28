//! OS から渡されたファイルを開く（Qt 版の `file_open.ProjectFileOpener`）。
//!
//! Windows はファイルをコマンドラインで渡す（`startup` の `open`）。macOS は Finder で
//! .pman をダブルクリックしたときやアプリのアイコンに落としたとき、引数ではなく
//! Apple Event で渡す（Tauri の `RunEvent::Opened`）。起動のときは画面より先に届くので、
//! 画面が `file_open_take` で受け取りに来るまで貯めておき、そのあとは `file-open` の
//! 事件で送る。画面はどちらも、窓に落としたファイルと同じに扱う（未保存なら尋ねる）。

use std::path::PathBuf;
use std::sync::Mutex;

use tauri::Emitter;

/// 画面が受け取りに来たか、と、それまでに届いたファイル。
static PENDING: Mutex<(bool, Vec<String>)> = Mutex::new((false, Vec::new()));

/// 届いた URL のうち、ファイルの場所だけ。
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn paths(urls: &[tauri::Url]) -> Vec<String> {
    urls.iter()
        .filter_map(|u| u.to_file_path().ok())
        .map(|p: PathBuf| p.display().to_string())
        .collect()
}

/// `RunEvent::Opened` を受ける（macOS だけに届く）。
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn opened(app: &tauri::AppHandle, urls: &[tauri::Url]) {
    let files = paths(urls);
    if files.is_empty() {
        return;
    }
    let mut pending = PENDING.lock().unwrap();
    if pending.0 {
        drop(pending);
        let _ = app.emit_to("main", "file-open", files);
    } else {
        pending.1.extend(files);
    }
}

/// 起動までに届いたファイルを受け取る。これより後に届いたものは `file-open` の事件で送る。
#[tauri::command]
pub fn file_open_take() -> Vec<String> {
    let mut pending = PENDING.lock().unwrap();
    pending.0 = true;
    std::mem::take(&mut pending.1)
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    #[test]
    fn keeps_only_file_urls() {
        let urls = [
            tauri::Url::parse("file:///tmp/a%20b.pman").unwrap(),
            tauri::Url::parse("https://example.com/c.pman").unwrap(),
        ];
        assert_eq!(paths(&urls), vec!["/tmp/a b.pman".to_string()]);
    }
}
