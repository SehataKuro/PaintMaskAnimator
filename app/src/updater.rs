//! 更新の「ダウンロードしてインストール…」（Qt 版の `updater.download_asset` と
//! `MainWindow._download_and_run_installer`）。
//!
//! - 読むのはこの作品の GitHub の公開リリースだけ（HTTPS）。渡されたインストーラの URL は、
//!   リリースのダウンロードの場所で始まり、いまの最新リリース（API）の資産に載っているものに限る。
//!   GitHub が返す転送先も、GitHub のリリースの置き場（githubusercontent.com）だけを辿る。
//! - 置き場は OS の一時フォルダー（Qt と同じ）。進み具合は `update-download` の事件で送る。
//! - 起動するのは、ここでダウンロードを終えたファイルだけ（macOS は open で .dmg を開く、
//!   Windows はシェルの start で .exe を起動。Qt の os.startfile と同じく昇格の確認が出る）。
//!
//! Tauri の updater プラグインは使わない。あれは署名した更新の束（latest.json・.sig）と公開鍵を
//! リリースに要し、アプリを置き換える仕組みなので、今のリリース（署名なしの .dmg・Setup.exe を
//! 置くだけ）と、Qt 版の「インストーラを起動して終える」動きに合わない。

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::Emitter;

const REPO: &str = "SehataKuro/PaintMaskAnimator";
/// 資産のダウンロードの場所（browser_download_url の頭）。
fn download_prefix() -> String {
    format!("https://github.com/{REPO}/releases/download/")
}
fn release_api() -> String {
    format!("https://api.github.com/repos/{REPO}/releases/latest")
}
/// GitHub がリリースの資産を転送する先。
const REDIRECT_HOSTS: [&str; 3] = [
    "https://objects.githubusercontent.com/",
    "https://release-assets.githubusercontent.com/",
    "https://github-releases.githubusercontent.com/",
];
/// 大きすぎる応答で止める（インストーラは数百 MB まで）。
const MAX_BYTES: u64 = 2 << 30;

static CANCEL: AtomicBool = AtomicBool::new(false);
static RUNNING: AtomicBool = AtomicBool::new(false);
/// ダウンロードを終えたファイル（起動してよいのはこれだけ）。
static DOWNLOADED: Mutex<Option<PathBuf>> = Mutex::new(None);

fn installer_suffix() -> Option<&'static str> {
    if cfg!(target_os = "windows") {
        Some(".exe")
    } else if cfg!(target_os = "macos") {
        Some(".dmg")
    } else {
        None
    }
}

/// インストーラの URL と名前を確かめる。名前は URL の最後の部分と同じで、区切りを含まず、
/// この OS のインストーラの拡張子で終わること。
pub fn check_asset(url: &str, name: &str, suffix: &str) -> Result<(), String> {
    let bad = || "公式のリリースのインストーラではありません。".to_string();
    let rest = url.strip_prefix(&download_prefix()).ok_or_else(bad)?;
    let (tag, file) = rest.split_once('/').ok_or_else(bad)?;
    let plain = |s: &str| {
        !s.is_empty()
            && s != "."
            && s != ".."
            && s.chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_' | '+'))
    };
    if !plain(tag) || !plain(file) || file != name || !name.to_lowercase().ends_with(suffix) {
        return Err(bad());
    }
    Ok(())
}

fn agent() -> ureq::Agent {
    ureq::Agent::config_builder()
        .https_only(true)
        .max_redirects(0)
        .http_status_as_error(false)
        .user_agent("PaintMaskAnimator-Updater")
        .timeout_connect(Some(Duration::from_secs(15)))
        .timeout_per_read(Some(Duration::from_secs(30)))
        .build()
        .new_agent()
}

fn http_error(status: u16) -> String {
    format!("HTTPエラー: {status}")
}

/// 最新リリースの資産に `url` が載っているか（API を読み直す）。
fn listed_in_latest(agent: &ureq::Agent, url: &str) -> Result<bool, String> {
    let response = agent
        .get(&release_api())
        .header("Accept", "application/vnd.github+json")
        .call()
        .map_err(|e| format!("ネットワークエラー: {e}"))?;
    if response.status() != 200 {
        return Err(http_error(response.status().as_u16()));
    }
    let text = response
        .into_body()
        .into_with_config()
        .limit(8 << 20)
        .read_to_string()
        .map_err(|e| e.to_string())?;
    let release: serde_json::Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    Ok(release["assets"].as_array().is_some_and(|assets| {
        assets
            .iter()
            .any(|a| a["browser_download_url"].as_str() == Some(url))
    }))
}

/// 転送をたどってよい場所か。
fn allowed_hop(url: &str) -> bool {
    url.starts_with(&download_prefix()) || REDIRECT_HOSTS.iter().any(|h| url.starts_with(h))
}

/// `url` を `dest` へ書く。`progress(読んだ量, 全体)`。止めたら Err("cancelled")。
fn download(
    agent: &ureq::Agent,
    url: &str,
    dest: &Path,
    mut progress: impl FnMut(u64, u64),
) -> Result<(), String> {
    let mut current = url.to_string();
    let mut response = None;
    for _ in 0..6 {
        if !allowed_hop(&current) {
            return Err("GitHub 以外の場所へ転送されました。".into());
        }
        let r = agent
            .get(&current)
            .header("Accept", "application/octet-stream")
            .call()
            .map_err(|e| format!("ネットワークエラー: {e}"))?;
        if r.status().is_redirection() {
            current = r
                .headers()
                .get("location")
                .and_then(|v| v.to_str().ok())
                .ok_or("転送先がありません。")?
                .to_string();
            continue;
        }
        if r.status() != 200 {
            return Err(http_error(r.status().as_u16()));
        }
        response = Some(r);
        break;
    }
    let response = response.ok_or("転送が多すぎます。")?;
    let total = response
        .headers()
        .get("content-length")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(0);
    let part = dest.with_extension("part");
    let mut out = std::fs::File::create(&part).map_err(|e| e.to_string())?;
    let mut reader = response
        .into_body()
        .into_with_config()
        .limit(MAX_BYTES)
        .reader();
    let mut buffer = vec![0u8; 65536];
    let mut downloaded = 0u64;
    let result = loop {
        if CANCEL.load(Ordering::SeqCst) {
            break Err("cancelled".to_string());
        }
        match reader.read(&mut buffer) {
            Ok(0) => break Ok(()),
            Ok(n) => {
                if let Err(e) = out.write_all(&buffer[..n]) {
                    break Err(e.to_string());
                }
                downloaded += n as u64;
                progress(downloaded, total);
            }
            Err(e) => break Err(e.to_string()),
        }
    };
    drop(out);
    if let Err(e) = result {
        let _ = std::fs::remove_file(&part);
        return Err(e);
    }
    if total > 0 && downloaded != total {
        let _ = std::fs::remove_file(&part);
        return Err("ダウンロードが途中で終わりました。".into());
    }
    std::fs::rename(&part, dest).map_err(|e| e.to_string())
}

/// `update-download` の事件の中身。
#[derive(Clone, Serialize)]
pub struct Progress {
    pub downloaded: u64,
    pub total: u64,
    /// 終わった（成功・失敗・中止）。
    pub done: bool,
    pub cancelled: bool,
    pub error: Option<String>,
    pub path: Option<String>,
}

/// ダウンロードを始める（終わりは `update-download` の事件の done）。
#[tauri::command]
pub fn update_download(app: tauri::AppHandle, url: String, name: String) -> Result<(), String> {
    let suffix = installer_suffix().ok_or("この OS のインストーラはありません。")?;
    check_asset(&url, &name, suffix)?;
    if RUNNING.swap(true, Ordering::SeqCst) {
        return Err("ダウンロードの途中です。".into());
    }
    CANCEL.store(false, Ordering::SeqCst);
    *DOWNLOADED.lock().unwrap() = None;
    std::thread::spawn(move || {
        let dest = std::env::temp_dir().join(&name);
        let agent = agent();
        let mut last = std::time::Instant::now();
        let result = listed_in_latest(&agent, &url).and_then(|listed| {
            if !listed {
                return Err("最新のリリースにこのインストーラがありません。".into());
            }
            download(&agent, &url, &dest, |downloaded, total| {
                if last.elapsed() >= Duration::from_millis(100) {
                    last = std::time::Instant::now();
                    let _ = app.emit(
                        "update-download",
                        Progress {
                            downloaded,
                            total,
                            done: false,
                            cancelled: false,
                            error: None,
                            path: None,
                        },
                    );
                }
            })
        });
        let cancelled = matches!(&result, Err(e) if e == "cancelled");
        if result.is_ok() {
            *DOWNLOADED.lock().unwrap() = Some(dest.clone());
        }
        RUNNING.store(false, Ordering::SeqCst);
        let _ = app.emit(
            "update-download",
            Progress {
                downloaded: 0,
                total: 0,
                done: true,
                cancelled,
                error: result.as_ref().err().filter(|_| !cancelled).cloned(),
                path: result.ok().map(|_| dest.display().to_string()),
            },
        );
    });
    Ok(())
}

/// ダウンロードをやめる（Qt の QProgressDialog のキャンセル）。
#[tauri::command]
pub fn update_cancel() {
    CANCEL.store(true, Ordering::SeqCst);
}

/// ダウンロードを終えたインストーラを起動する。
#[tauri::command]
pub fn update_launch() -> Result<(), String> {
    let path = DOWNLOADED
        .lock()
        .unwrap()
        .clone()
        .filter(|p| p.is_file())
        .ok_or("ダウンロードしたインストーラがありません。")?;
    #[cfg(target_os = "macos")]
    let result = std::process::Command::new("/usr/bin/open")
        .arg(&path)
        .spawn();
    #[cfg(target_os = "windows")]
    let result = {
        use std::os::windows::process::CommandExt;
        // シェルの start で起動する（os.startfile と同じく、要るなら昇格の確認が出る）。
        std::process::Command::new("cmd")
            .args(["/C", "start", ""])
            .arg(&path)
            .creation_flags(0x0800_0000)
            .spawn()
    };
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let result = std::process::Command::new("xdg-open").arg(&path).spawn();
    result.map(|_| ()).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_official_release_assets() {
        let ok = "https://github.com/SehataKuro/PaintMaskAnimator/releases/download/v0.9.0/PaintMaskAnimator-Setup-0.9.0.exe";
        assert!(check_asset(ok, "PaintMaskAnimator-Setup-0.9.0.exe", ".exe").is_ok());
        assert!(check_asset(ok, "PaintMaskAnimator-Setup-0.9.0.exe", ".dmg").is_err());
        assert!(check_asset(ok, "other.exe", ".exe").is_err());
        for bad in [
            "https://github.com/someone/PaintMaskAnimator/releases/download/v1/a.exe",
            "http://github.com/SehataKuro/PaintMaskAnimator/releases/download/v1/a.exe",
            "https://github.com/SehataKuro/PaintMaskAnimator/releases/download/../a.exe",
            "https://github.com/SehataKuro/PaintMaskAnimator/releases/download/v1/x/a.exe",
            "https://github.com/SehataKuro/PaintMaskAnimator/releases/download/v1/a.exe?x=1",
        ] {
            assert!(check_asset(bad, "a.exe", ".exe").is_err(), "{bad}");
        }
        assert!(allowed_hop(
            "https://objects.githubusercontent.com/github-production-release-asset/1"
        ));
        assert!(!allowed_hop("https://example.com/a.exe"));
        assert!(!allowed_hop("http://objects.githubusercontent.com/x"));
    }

    #[test]
    fn launch_refuses_without_a_download() {
        assert!(update_launch().is_err());
    }
}
