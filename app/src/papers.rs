//! レイアウト用紙（Qt 版の layout_paper）。
//!
//! - 画像は設定のフォルダーの `layout_papers/` に写して持つ。一覧は設定の `layout_papers`
//!   （`[{id, name, file}]`）。画像の無くなった項目は一覧に出さない。
//! - 大きさは画像の頭（PNG・JPEG・TGA）だけを読んで出す。
//! - 新規作成で選んだ用紙は、一番下の下書きレイヤーに入れる（imports.rs の
//!   insert_layout_paper）。最後に使った用紙も覚える。

use crate::prefs;
use crate::shell::ShellShared;
use serde::Serialize;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;

/// 読み込める画像（layout_paper.SUFFIXES）。
pub const SUFFIXES: [&str; 4] = ["png", "jpg", "jpeg", "tga"];

#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Paper {
    pub id: String,
    pub name: String,
    pub file: String,
    /// 画像の大きさ。読めなければ None。
    pub size: Option<(u32, u32)>,
    /// 画像の場所（サムネイルに使う）。
    pub path: String,
}

fn papers_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = prefs::config_dir(app)?.join("layout_papers");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(dir)
}

fn entries(stored: Option<&Value>) -> Vec<Value> {
    stored
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter(|e| {
                    e.get("id")
                        .and_then(Value::as_str)
                        .is_some_and(|s| !s.is_empty())
                        && e.get("file")
                            .and_then(Value::as_str)
                            .is_some_and(|s| !s.is_empty())
                })
                .cloned()
                .collect()
        })
        .unwrap_or_default()
}

fn image_path(dir: &Path, entry: &Value) -> PathBuf {
    let file = entry
        .get("file")
        .and_then(Value::as_str)
        .unwrap_or_default();
    dir.join(Path::new(file).file_name().unwrap_or_default())
}

/// 画像の頭から大きさを読む（PNG・JPEG・TGA）。
pub fn image_size(bytes: &[u8]) -> Option<(u32, u32)> {
    let be32 = |b: &[u8]| u32::from_be_bytes([b[0], b[1], b[2], b[3]]);
    if bytes.len() >= 24 && bytes.starts_with(b"\x89PNG\r\n\x1a\n") && &bytes[12..16] == b"IHDR" {
        return Some((be32(&bytes[16..20]), be32(&bytes[20..24])));
    }
    if bytes.starts_with(&[0xFF, 0xD8]) {
        let mut i = 2;
        while i + 9 < bytes.len() {
            if bytes[i] != 0xFF {
                i += 1;
                continue;
            }
            let marker = bytes[i + 1];
            let len = usize::from(u16::from_be_bytes([bytes[i + 2], bytes[i + 3]]));
            // SOF0〜SOF15（DHT・JPG・DAC を除く）。
            if (0xC0..=0xCF).contains(&marker) && ![0xC4, 0xC8, 0xCC].contains(&marker) {
                let h = u16::from_be_bytes([bytes[i + 5], bytes[i + 6]]);
                let w = u16::from_be_bytes([bytes[i + 7], bytes[i + 8]]);
                return Some((u32::from(w), u32::from(h)));
            }
            i += 2 + len;
        }
        return None;
    }
    // TGA：決まった印が無いので、種類と大きさが妥当なときだけ。
    if bytes.len() >= 18 && [1, 2, 3, 9, 10, 11].contains(&bytes[2]) {
        let w = u32::from(u16::from_le_bytes([bytes[12], bytes[13]]));
        let h = u32::from(u16::from_le_bytes([bytes[14], bytes[15]]));
        if w > 0 && h > 0 {
            return Some((w, h));
        }
    }
    None
}

fn read_size(path: &Path) -> Option<(u32, u32)> {
    use std::io::Read;
    let mut head = vec![0u8; 64 * 1024];
    let mut file = std::fs::File::open(path).ok()?;
    let n = file.read(&mut head).ok()?;
    image_size(&head[..n])
}

fn list(app: &AppHandle, shell: &ShellShared) -> Result<Vec<Paper>, String> {
    let dir = papers_dir(app)?;
    let sh = shell.lock().unwrap();
    Ok(entries(sh.prefs.get(prefs::LAYOUT_PAPERS))
        .iter()
        .filter_map(|e| {
            let path = image_path(&dir, e);
            path.is_file().then(|| Paper {
                id: e["id"].as_str().unwrap_or_default().into(),
                name: e
                    .get("name")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .into(),
                file: e["file"].as_str().unwrap_or_default().into(),
                size: read_size(&path),
                path: path.display().to_string(),
            })
        })
        .collect())
}

/// 用紙の一覧（画像のあるものだけ）。
#[tauri::command]
pub fn papers_list(app: AppHandle, shell: State<ShellShared>) -> Result<Vec<Paper>, String> {
    list(&app, &shell)
}

fn new_id() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or_default();
    format!("{:032x}", nanos ^ (u128::from(std::process::id()) << 96))
}

/// 「追加…」：画像を選んで写す。読めなかった画像の名前を返す。
#[tauri::command]
pub async fn papers_add(
    app: AppHandle,
    title: String,
    filter: String,
) -> Result<Vec<String>, String> {
    let Some(paths) = app
        .dialog()
        .file()
        .set_title(title)
        .add_filter(filter, &SUFFIXES)
        .blocking_pick_files()
    else {
        return Ok(vec![]);
    };
    let dir = papers_dir(&app)?;
    let mut failed = Vec::new();
    let mut added = Vec::new();
    for path in paths.into_iter().filter_map(|p| p.into_path().ok()) {
        let name = path
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        let suffix = path
            .extension()
            .map(|e| e.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        if !SUFFIXES.contains(&suffix.as_str()) || read_size(&path).is_none() {
            failed.push(name);
            continue;
        }
        let id = new_id();
        let target = dir.join(format!("{id}.{suffix}"));
        if std::fs::copy(&path, &target).is_err() {
            failed.push(name);
            continue;
        }
        let stem = path
            .file_stem()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        added.push(
            json!({"id": id, "name": stem, "file": target.file_name().unwrap().to_string_lossy()}),
        );
        // 同じ時刻の id にならないように。
        std::thread::sleep(std::time::Duration::from_micros(1));
    }
    if !added.is_empty() {
        let shell = app.state::<ShellShared>();
        let mut sh = shell.lock().unwrap();
        let mut all = entries(sh.prefs.get(prefs::LAYOUT_PAPERS));
        all.extend(added);
        sh.prefs.set(prefs::LAYOUT_PAPERS, Value::Array(all))?;
    }
    Ok(failed)
}

#[tauri::command]
pub fn papers_rename(shell: State<ShellShared>, id: String, name: String) -> Result<(), String> {
    let name = name.trim();
    if name.is_empty() {
        return Ok(());
    }
    let mut sh = shell.lock().unwrap();
    let mut all = entries(sh.prefs.get(prefs::LAYOUT_PAPERS));
    for e in &mut all {
        if e["id"] == id.as_str() {
            e["name"] = json!(name);
        }
    }
    sh.prefs.set(prefs::LAYOUT_PAPERS, Value::Array(all))
}

#[tauri::command]
pub fn papers_remove(app: AppHandle, shell: State<ShellShared>, id: String) -> Result<(), String> {
    let dir = papers_dir(&app)?;
    let mut sh = shell.lock().unwrap();
    let all = entries(sh.prefs.get(prefs::LAYOUT_PAPERS));
    for e in all.iter().filter(|e| e["id"] == id.as_str()) {
        let _ = std::fs::remove_file(image_path(&dir, e));
    }
    let rest: Vec<Value> = all.into_iter().filter(|e| e["id"] != id.as_str()).collect();
    sh.prefs.set(prefs::LAYOUT_PAPERS, Value::Array(rest))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_sizes_from_headers() {
        let mut png = b"\x89PNG\r\n\x1a\n\0\0\0\x0dIHDR".to_vec();
        png.extend(1920u32.to_be_bytes());
        png.extend(1080u32.to_be_bytes());
        assert_eq!(image_size(&png), Some((1920, 1080)));
        let jpeg = [
            0xFF, 0xD8, 0xFF, 0xE0, 0, 4, 0, 0, 0xFF, 0xC0, 0, 17, 8, 0x02, 0xD0, 0x05, 0x00, 3, 0,
        ];
        assert_eq!(image_size(&jpeg), Some((1280, 720)));
        let mut tga = vec![0u8; 18];
        tga[2] = 2;
        tga[12..14].copy_from_slice(&64u16.to_le_bytes());
        tga[14..16].copy_from_slice(&48u16.to_le_bytes());
        assert_eq!(image_size(&tga), Some((64, 48)));
        assert_eq!(image_size(b"hello"), None);
    }

    #[test]
    fn keeps_only_complete_entries() {
        let stored = json!([{"id": "a", "file": "a.png", "name": "A"}, {"id": "", "file": "x"}, 3]);
        assert_eq!(entries(Some(&stored)).len(), 1);
    }
}
