//! サブビュー（Qt 版の `subview.py`・`session_state.py`）。画像ファイルをコピーせず、元の場所への
//! リンクとして見せる参考画像のパネル。画面は ui/subview.js。
//!
//! - 開くのはファイルかフォルダー。フォルダーなら中の対応画像（.png .jpg .jpeg .tga .psd）を
//!   名前順（大文字小文字を区別しない）に並べて最初を、ファイルなら同じフォルダーの並びの中で見せる。
//! - 画像は pma-core で読む。PSD は表示しているレイヤーの合成（`PSDImage.composite()`）、JPEG は
//!   Qt の `setAutoTransform(True)` と同じく EXIF の向きに従う。
//! - 見え方（パス・拡大・回転・ずらし・開いているか）は Qt と同じく config.json の
//!   `last_paint_session.subview` に置く。

use std::path::{Path, PathBuf};

use pma_core::import::{image, orientation, psd};
use serde::Serialize;
use serde_json::{json, Value};
use tauri::ipc::Response;
use tauri::State;

use crate::session::Shared;
use crate::shell::ShellShared;

/// 対応する拡張子（Qt の SUBVIEW_EXTENSIONS）。
const EXTENSIONS: [&str; 5] = ["png", "jpg", "jpeg", "tga", "psd"];

fn supported(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| EXTENSIONS.contains(&e.to_lowercase().as_str()))
}

fn sort_key(path: &Path) -> String {
    path.file_name()
        .map(|n| n.to_string_lossy().to_lowercase())
        .unwrap_or_default()
}

/// フォルダーの中の対応画像（Qt の `_images_in_folder`）。読めなければ空。
fn images_in_folder(folder: &Path) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(folder) else {
        return Vec::new();
    };
    let mut files: Vec<PathBuf> = entries
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.is_file() && supported(p))
        .collect();
    files.sort_by_key(|p| sort_key(p));
    files
}

/// 開いた並び。`index` が見せる画像。
#[derive(Debug, Serialize, PartialEq)]
pub struct Listing {
    pub files: Vec<String>,
    pub index: usize,
}

/// ファイルかフォルダーから並びを作る（Qt の `load_path` の前半）。
pub fn listing(path: &Path) -> Result<Listing, String> {
    let (target, mut files) = if path.is_dir() {
        let files = images_in_folder(path);
        let Some(first) = files.first().cloned() else {
            return Err("画像が見つかりません".into());
        };
        (first, files)
    } else {
        let files = path.parent().map(images_in_folder).unwrap_or_default();
        (path.to_path_buf(), files)
    };
    if !files.contains(&target) {
        files.push(target.clone());
        files.sort_by_key(|p| sort_key(p));
    }
    let index = files.iter().position(|p| *p == target).unwrap_or(0);
    Ok(Listing {
        files: files.iter().map(|p| p.display().to_string()).collect(),
        index,
    })
}

/// 画像を (幅, 高さ, ストレート RGBA) で読む（Qt の `_read_image`）。
pub fn read_image(path: &Path) -> Result<(usize, usize, Vec<u8>), String> {
    let data = std::fs::read(path).map_err(|e| e.to_string())?;
    let is_psd = path
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| e.eq_ignore_ascii_case("psd"));
    if is_psd {
        return psd::composite_psd(&data).map_err(|e| e.to_string());
    }
    let (w, h, rgba) = image::decode_image(&data).map_err(|e| e.to_string())?;
    if matches!(image::image_kind(&data), image::ImageKind::Jpeg) {
        return Ok(orientation::apply_orientation(
            orientation::jpeg_orientation(&data),
            w,
            h,
            &rgba,
        ));
    }
    Ok((w, h, rgba))
}

/// 開く。並びを返す。見つからなければ Err（画面は名前の欄に出す）。
#[tauri::command]
pub fn subview_open(path: String) -> Result<Listing, String> {
    listing(Path::new(&path))
}

/// 「開く」の窓（Qt の `choose_image`）。選ばなければ None。
#[tauri::command]
pub async fn subview_pick(app: tauri::AppHandle) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    app.dialog()
        .file()
        .set_title("サブビュー画像を開く")
        .add_filter("対応画像", &EXTENSIONS)
        .add_filter("PNG", &["png"])
        .add_filter("JPEG", &["jpg", "jpeg"])
        .add_filter("TGA", &["tga"])
        .add_filter("PSD", &["psd"])
        .add_filter("すべてのファイル", &["*"])
        .blocking_pick_file()
        .and_then(|p| p.into_path().ok())
        .map(|p| p.display().to_string())
}

/// 画像の画素。頭の 8 バイトが幅・高さ（u32 LE）、続きがストレート RGBA。
#[tauri::command]
pub async fn subview_image(path: String) -> Result<Response, String> {
    let (w, h, rgba) = tauri::async_runtime::spawn_blocking(move || read_image(Path::new(&path)))
        .await
        .map_err(|e| e.to_string())??;
    let mut out = Vec::with_capacity(8 + rgba.len());
    out.extend_from_slice(&(w as u32).to_le_bytes());
    out.extend_from_slice(&(h as u32).to_le_bytes());
    out.extend_from_slice(&rgba);
    Ok(Response::new(out))
}

fn number(record: &Value, key: &str, default: f64, min: f64, max: f64) -> f64 {
    match record.get(key).and_then(Value::as_f64) {
        Some(v) if v.is_finite() => v.clamp(min, max),
        _ => default,
    }
}

/// 保存した見え方（Qt の `session_state.restore`）。値は Qt と同じ範囲に収める。
pub fn restore_record(root: Option<&Value>) -> Option<Value> {
    let record = root?.get("subview")?;
    if !record.is_object() {
        return None;
    }
    Some(json!({
        "path": record.get("path").and_then(Value::as_str).filter(|p| !p.is_empty()),
        "zoom": number(record, "zoom", 100.0, 5.0, 1600.0).trunc(),
        "rotation": number(record, "rotation", 0.0, -180.0, 180.0).trunc(),
        "pan_x": number(record, "pan_x", 0.0, -1e7, 1e7),
        "pan_y": number(record, "pan_y", 0.0, -1e7, 1e7),
        "open": record.get("open").and_then(Value::as_bool),
    }))
}

#[tauri::command]
pub fn subview_state_get(shell: State<ShellShared>) -> Option<Value> {
    let sh = shell.lock().unwrap();
    restore_record(sh.prefs.get("last_paint_session"))
}

/// 見え方を残す（Qt の `session_state.save`）。`brush_size` は今のツールの設定の値を一緒に書く。
#[tauri::command]
pub fn subview_state_set(
    session: State<Shared>,
    shell: State<ShellShared>,
    state: Value,
) -> Result<(), String> {
    let brush_size = session.lock().unwrap().tools.settings.brush_size;
    let mut sh = shell.lock().unwrap();
    let mut record = sh
        .prefs
        .get("last_paint_session")
        .cloned()
        .filter(Value::is_object)
        .unwrap_or_else(|| json!({}));
    record["brush_size"] = json!(brush_size);
    record["subview"] = json!({
        "path": state.get("path").and_then(Value::as_str),
        "zoom": state.get("zoom").and_then(Value::as_f64).unwrap_or(100.0) as i64,
        "rotation": state.get("rotation").and_then(Value::as_f64).unwrap_or(0.0) as i64,
        "pan_x": state.get("pan_x").and_then(Value::as_f64).unwrap_or(0.0),
        "pan_y": state.get("pan_y").and_then(Value::as_f64).unwrap_or(0.0),
        "open": state.get("open").and_then(Value::as_bool).unwrap_or(true),
    });
    sh.prefs.set("last_paint_session", record)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn png(path: &Path) {
        let data = pma_core::export::png_bytes(1, 1, &[1, 2, 3, 255]).unwrap();
        std::fs::write(path, data).unwrap();
    }

    #[test]
    fn lists_folders_and_files_like_qt() {
        let dir = std::env::temp_dir().join(format!("pma-subview-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        assert_eq!(listing(&dir), Err("画像が見つかりません".into()));
        png(&dir.join("b.PNG"));
        png(&dir.join("A.png"));
        std::fs::write(dir.join("note.txt"), "x").unwrap();
        let l = listing(&dir).unwrap();
        assert_eq!(l.index, 0);
        assert!(l.files[0].ends_with("A.png") && l.files[1].ends_with("b.PNG"));
        assert_eq!(l.files.len(), 2);
        let l = listing(&dir.join("b.PNG")).unwrap();
        assert_eq!(l.index, 1);
        // 対応しない拡張子のファイルも、並びに加えて開こうとする（読めなければ画面が知らせる）。
        let l = listing(&dir.join("note.txt")).unwrap();
        assert!(l.files[l.index].ends_with("note.txt"));
        assert!(read_image(&dir.join("note.txt")).is_err());
        assert_eq!(
            read_image(&dir.join("A.png")).unwrap(),
            (1, 1, vec![1, 2, 3, 255])
        );
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn restores_within_qt_ranges() {
        let root = json!({"subview": {"path": "/x.png", "zoom": 99999, "rotation": -720.5,
            "pan_x": "bad", "pan_y": 3.5, "open": false}});
        let r = restore_record(Some(&root)).unwrap();
        assert_eq!(r["zoom"], json!(1600.0));
        assert_eq!(r["rotation"], json!(-180.0));
        assert_eq!(r["pan_x"], json!(0.0));
        assert_eq!(r["pan_y"], json!(3.5));
        assert_eq!(r["open"], json!(false));
        assert!(restore_record(Some(&json!({"subview": 1}))).is_none());
    }
}
