//! 自動保存と、異常終了のあとの復元（Qt 版の main_window_autosave.AutosaveController）。
//!
//! - 環境設定の「自動保存する」「N 分ごと」で、作品をアプリ設定のフォルダーの
//!   `autosave.pmap`（中身は .pman と同じ）へ書く。線を描いている途中なら 1 秒待つ。
//! - 書くのは別のスレッド。文書は写し（画像は共有）を取ってから錠を外して符号化する。
//! - きちんと終わったら消す（[`clear`]）。次の起動で残っていれば、画面が復元を尋ねる。
//!
//! Qt 版は時間ごとに必ず書くが、こちらは前に書いたときから中身が変わっていなければ書かない。

use crate::session::{zip_time_now, Shared};
use pma_core::project::archive::{self, PngCache, SaveOptions};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::Manager;

/// 自動保存の状態（tauri の manage で持つ）。
pub struct Autosave {
    pub path: PathBuf,
    /// きちんと終わるところ（もう書かない）。
    closing: AtomicBool,
    /// 書いている途中（終わるときに待つ）。
    writing: Mutex<()>,
}

impl Autosave {
    pub fn new(path: PathBuf) -> Self {
        Self {
            path,
            closing: AtomicBool::new(false),
            writing: Mutex::new(()),
        }
    }

    /// 前回の自動保存が残っているか（異常終了の印）。
    pub fn leftover(&self) -> bool {
        std::fs::metadata(&self.path).is_ok_and(|m| m.len() > 0)
    }

    /// 自動保存を消す（きちんと終わったとき・復元しないと決めたとき）。
    pub fn clear(&self) {
        let _guard = self.writing.lock().unwrap();
        let _ = std::fs::remove_file(&self.path);
    }

    /// 終わる前：もう書かないようにして、書きかけを待ってから消す。
    pub fn finish(&self) {
        self.closing.store(true, Ordering::SeqCst);
        self.clear();
    }
}

/// 作品の写しを `path` へ書く（隣に書いてから置き換える）。
pub fn write(
    path: &Path,
    document: &pma_core::document::model::Document,
    metadata: &pma_core::project::json::Json,
    cache: &mut PngCache,
) -> Result<(), String> {
    let options = SaveOptions::new(zip_time_now());
    let bytes = archive::save_project(document, metadata, &options, Some(cache))
        .map_err(|e| e.message())?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let tmp = path.with_extension("pmap.tmp");
    std::fs::write(&tmp, bytes).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, path).map_err(|e| e.to_string())
}

/// 自動保存の時計を始める。`settings` は（する, 分）を返す。
pub fn start(app: tauri::AppHandle, settings: impl Fn() -> (bool, i64) + Send + 'static) {
    std::thread::spawn(move || {
        let mut cache = PngCache::new();
        let mut last_write = Instant::now();
        let mut last_revision: Option<(u64, u64, String)> = None;
        loop {
            std::thread::sleep(Duration::from_secs(1));
            let state = app.state::<Autosave>();
            if state.closing.load(Ordering::SeqCst) {
                return;
            }
            let (enabled, minutes) = settings();
            if !enabled {
                last_write = Instant::now();
                continue;
            }
            if last_write.elapsed() < Duration::from_secs(minutes.max(1) as u64 * 60) {
                continue;
            }
            // 写しを取る（描いている途中なら 1 秒後にもう一度）。
            let snapshot = {
                let session = app.state::<Shared>();
                let mut s = session.lock().unwrap();
                if s.is_drawing() {
                    continue;
                }
                crate::project_meta::store_from_session(&mut s);
                let revision = s.content_revision();
                s.project.as_ref().map(|p| {
                    (
                        p.document.clone(),
                        p.metadata.clone(),
                        (
                            revision.0,
                            revision.1,
                            crate::project_meta::memo(&p.metadata),
                        ),
                    )
                })
            };
            last_write = Instant::now();
            let Some((document, metadata, revision)) = snapshot else {
                continue;
            };
            if last_revision.as_ref() == Some(&revision) {
                continue;
            }
            let _guard = state.writing.lock().unwrap();
            if state.closing.load(Ordering::SeqCst) {
                return;
            }
            match write(&state.path, &document, &metadata, &mut cache) {
                Ok(()) => last_revision = Some(revision),
                Err(e) => eprintln!("自動保存できませんでした：{e}"),
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::session::Session;

    #[test]
    fn writes_a_project_that_opens_again_and_clears() {
        let dir = std::env::temp_dir().join(format!("pma-autosave-{}", std::process::id()));
        let state = Autosave::new(dir.join("autosave.pmap"));
        assert!(!state.leftover());
        let s = Session::empty(32, 16);
        let p = s.project.as_ref().unwrap();
        write(&state.path, &p.document, &p.metadata, &mut PngCache::new()).unwrap();
        assert!(state.leftover());
        let mut again = Session::unloaded(8, 8);
        again.open(&state.path).unwrap();
        assert_eq!((again.summary().width, again.summary().height), (32, 16));
        assert_eq!(again.summary().layers, 1);
        state.finish();
        assert!(!state.leftover());
        let _ = std::fs::remove_dir_all(dir);
    }
}
