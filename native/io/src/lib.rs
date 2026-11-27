//! 書き出し・読み込みのファイル操作（`main_window_export.py`・`main_window_import.py` の
//! ファイルに触れる部分）。何を書くかは `pma_core::export`・`pma_core::import` が決める。
//!
//! 文字のファイル（タイムシート・メモ）は Python の `Path.write_text` と同じく、Windows
//! では改行を CRLF にする。TS.csv は Python も改行を変えない（`newline=""`）。
//! 上書きの確認など画面の判断は呼び出し側が行う（[`target_state`] で状態を調べる）。

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use pma_core::compose::PremulImage;
use pma_core::document::model::Document;
use pma_core::export::cut_folder::{
    scan_cut_folder as scan_entries, CutFolderContents, ExportPlan, ImageFormat, ListedEntry,
};
use pma_core::export::{self, sheet};
use pma_core::import::image::{read_image, ReadError};

/// 書き出しの失敗。
#[derive(Debug)]
pub enum WriteError {
    /// ファイル操作の失敗（対象のパス）。
    Io(PathBuf, io::Error),
    /// 内容を作れなかった。
    Core(pma_core::Error),
    /// 同じ名前のファイルがあってフォルダーを作れない。
    FileInTheWay(PathBuf),
}

impl std::fmt::Display for WriteError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WriteError::Io(path, error) => write!(f, "{}: {error}", path.display()),
            WriteError::Core(error) => write!(f, "{error}"),
            WriteError::FileInTheWay(_) => {
                write!(
                    f,
                    "同じ名前のファイルがあるため、フォルダーを作成できません。"
                )
            }
        }
    }
}

impl std::error::Error for WriteError {}

impl From<pma_core::Error> for WriteError {
    fn from(error: pma_core::Error) -> Self {
        WriteError::Core(error)
    }
}

fn io_error(path: &Path) -> impl FnOnce(io::Error) -> WriteError + '_ {
    move |error| WriteError::Io(path.to_path_buf(), error)
}

/// 書き出し先のフォルダーの状態。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TargetState {
    /// まだない。
    Missing,
    /// 空のフォルダー。
    Empty,
    /// 中身のあるフォルダー（上書きの確認が要る）。
    NotEmpty,
    /// 同じ名前のファイル（フォルダーを作れない）。
    File,
}

/// 書き出し先の状態（Python の `target.exists()`・`is_dir()`・`any(iterdir())`）。
pub fn target_state(path: &Path) -> io::Result<TargetState> {
    match fs::metadata(path) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(TargetState::Missing),
        Err(error) => Err(error),
        Ok(meta) if !meta.is_dir() => Ok(TargetState::File),
        Ok(_) => Ok(if fs::read_dir(path)?.next().is_some() {
            TargetState::NotEmpty
        } else {
            TargetState::Empty
        }),
    }
}

/// Python の `Path.write_text(text, encoding="utf-8")` と同じバイト列（Windows は CRLF）。
pub fn text_bytes(text: &str) -> Vec<u8> {
    if cfg!(windows) {
        text.replace('\n', "\r\n").into_bytes()
    } else {
        text.as_bytes().to_vec()
    }
}

fn write(path: &Path, data: &[u8]) -> Result<(), WriteError> {
    fs::write(path, data).map_err(io_error(path))
}

fn create_dirs(path: &Path) -> Result<(), WriteError> {
    fs::create_dir_all(path).map_err(io_error(path))
}

fn ensure_folder(path: &Path) -> Result<(), WriteError> {
    if target_state(path).map_err(io_error(path))? == TargetState::File {
        return Err(WriteError::FileInTheWay(path.to_path_buf()));
    }
    create_dirs(path)
}

/// カットフォルダーの書き出しの入力。
pub struct CutFolderWrite<'a> {
    /// 文書。
    pub document: &'a Document,
    /// 計画（`plan_export`）。
    pub plan: &'a ExportPlan,
    /// セル画像の形式。
    pub image_format: ImageFormat,
    /// プロジェクトのメモ（空なら書かない）。
    pub memo: &'a str,
    /// 再生範囲の終わり（タイムシートの長さ）。
    pub playback_range_end: Option<i64>,
}

/// カットフォルダーを parent の下に書き出し、カットフォルダーのパスを返す
/// （`ExportController.write_cut_folder`）。progress は (書いた枚数, 全体)。
pub fn write_cut_folder(
    parent: &Path,
    job: &CutFolderWrite<'_>,
    progress: &mut dyn FnMut(usize, usize),
) -> Result<PathBuf, WriteError> {
    let plan = job.plan;
    let target = parent.join(&plan.folder_name);
    ensure_folder(&target)?;
    for folder in &plan.folders {
        create_dirs(&target.join(folder))?;
    }
    let total = plan.files.len();
    for (index, (relative, cel)) in plan.files.iter().enumerate() {
        progress(index, total);
        let data = export::encode_cel(job.document, cel, job.image_format)?;
        write(&target.join(relative), &data)?;
    }
    progress(total, total);
    if let Some((path, text)) =
        export::cut_folder_sheet(job.document, plan, job.playback_range_end)?
    {
        write(&target.join(path), &text_bytes(&text))?;
    }
    if let Some((path, text)) = export::cut_folder_memo(plan, job.memo) {
        write(&target.join(path), &text_bytes(&text))?;
    }
    Ok(target)
}

/// 今のシートをタイムシートのファイルに書く（`ExportController.xdts_dialog`）。
pub fn write_timesheet(path: &Path, text: &str) -> Result<(), WriteError> {
    write(path, &text_bytes(text))
}

/// 連番＋TS.csv を destination に書き出す（`ExportController.key_sequence`）。
///
/// フォルダー（レイヤーごと）は先に作る。書けるキーがなければ None（フォルダーは残る）。
pub fn write_key_sequence(
    destination: &Path,
    document: &Document,
    format: ImageFormat,
    progress: &mut dyn FnMut(usize, usize),
) -> Result<Option<usize>, WriteError> {
    ensure_folder(destination)?;
    for name in sheet::key_sequence_layer_names(document) {
        create_dirs(&destination.join(name))?;
    }
    let keys = sheet::key_sequence_keys(document);
    if keys.is_empty() {
        return Ok(None);
    }
    for (index, key) in keys.iter().enumerate() {
        progress(index, keys.len());
        let image = export::drawing_at(document, key.frame, key.layer_index)?;
        let data = export::encode_drawing(&image, format)?;
        let path =
            destination
                .join(&key.folder)
                .join(format!("{}.{}", key.file_stem, format.extension()));
        write(&path, &data)?;
    }
    progress(keys.len(), keys.len());
    write(&destination.join("TS.csv"), &sheet::key_sequence_csv(&keys))?;
    Ok(Some(keys.len()))
}

/// 影素材の連番を destination に書き出し、書いた枚数を返す（`write_shadow_sequence`）。
/// 書ける絵がなければ None。
pub fn write_shadow_sequence(
    destination: &Path,
    document: &Document,
    shadow_rgbs: &[[u8; 3]],
    progress: &mut dyn FnMut(usize, usize),
) -> Result<Option<usize>, WriteError> {
    if shadow_rgbs.is_empty() {
        return Ok(None);
    }
    let cels = sheet::shadow_cels(document);
    if cels.is_empty() {
        return Ok(None);
    }
    let mut written = 0;
    for (index, cel) in cels.iter().enumerate() {
        progress(index, cels.len());
        let image = export::drawing_at(document, cel.frame, cel.layer_index)?;
        let pngs = export::shadow_pngs(&image, shadow_rgbs)?;
        for (kind, data) in export::shadow::KINDS.iter().zip(pngs) {
            let folder = destination.join(kind).join(&cel.layer_name);
            create_dirs(&folder)?;
            let path = destination.join(export::shadow_path(kind, &cel.layer_name, cel.number));
            write(&path, &data)?;
            written += 1;
        }
    }
    progress(cels.len(), cels.len());
    Ok(Some(written))
}

fn listed(path: &Path, name: String, depth: usize) -> io::Result<ListedEntry> {
    let meta = fs::metadata(path);
    let (is_file, is_dir) = match &meta {
        Ok(meta) => (meta.is_file(), meta.is_dir()),
        Err(_) => (false, false),
    };
    let children = if is_dir && depth > 0 && !name.starts_with('.') {
        list(path, depth - 1)?
    } else {
        Vec::new()
    };
    Ok(ListedEntry {
        name,
        is_file,
        is_dir,
        children,
    })
}

fn list(path: &Path, depth: usize) -> io::Result<Vec<ListedEntry>> {
    let mut entries = Vec::new();
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        entries.push(listed(&entry.path(), name, depth)?);
    }
    Ok(entries)
}

/// カットフォルダーの直下と1段下の一覧（`scan_cut_folder` に渡す形）。
pub fn list_cut_folder(root: &Path) -> io::Result<Vec<ListedEntry>> {
    list(root, 1)
}

/// カットフォルダーのタイムシートとセル画像を探す（`cut_folder.scan_cut_folder`）。
/// パスは root からの相対（`/` 区切り）。
pub fn scan_cut_folder(root: &Path) -> io::Result<CutFolderContents> {
    Ok(scan_entries(&list_cut_folder(root)?))
}

/// 読み込みの失敗。
#[derive(Debug)]
pub enum ReadFileError {
    /// ファイルを読めない。
    Io(io::Error),
    /// 画像として読めない。
    Image(ReadError),
}

impl std::fmt::Display for ReadFileError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ReadFileError::Io(error) => write!(f, "{error}"),
            ReadFileError::Image(error) => write!(f, "{error}"),
        }
    }
}

impl std::error::Error for ReadFileError {}

/// 画像ファイルを読み、Qt と同じ乗算済みの画素にする（`_read_image_file`）。
pub fn read_image_file(path: &Path) -> Result<PremulImage, ReadFileError> {
    let data = fs::read(path).map_err(ReadFileError::Io)?;
    read_image(&data).map_err(ReadFileError::Image)
}

#[cfg(test)]
mod tests {
    use super::*;
    use pma_core::export::cut_folder::{plan_export, pma_standard_layout, Values};

    fn scratch(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!("pma-io-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn target_states() {
        let root = scratch("state");
        assert_eq!(target_state(&root.join("x")).unwrap(), TargetState::Missing);
        assert_eq!(target_state(&root).unwrap(), TargetState::Empty);
        fs::write(root.join("f"), b"1").unwrap();
        assert_eq!(target_state(&root).unwrap(), TargetState::NotEmpty);
        assert_eq!(target_state(&root.join("f")).unwrap(), TargetState::File);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn empty_cut_folder_writes_the_sheet_and_folders() {
        let root = scratch("cut");
        let position = serde_json::json!({
            "has_content": false, "is_blank_key": false, "sequence_number": null,
            "sequence_only": false, "blank_start": false, "tween": null, "tween_member": null,
            "local": null, "content_origin": null, "v1_position": null, "dense_import": null,
            "length_imported": null, "resolve_source": null, "legacy_missing": false,
        });
        let graph = serde_json::json!({
            "width": 4, "height": 3, "current_frame": 0, "active_layer": 0,
            "tracks": [{"settings": {
                "name": "A", "visible": true, "opacity": 1.0, "alpha_locked": false,
                "is_paper": false, "is_draft": false, "color_filter_enabled": false,
                "color_filter_rgb": null,
            }, "hints": [], "cells": []}],
            "frames": [{"duration": 1, "positions": [position]}],
        });
        let document = Document::from_graph(&graph, &[]).unwrap();
        let values = Values {
            title: Some("T".into()),
            episode: Some("1".into()),
            cut: Some("2".into()),
            ..Values::default()
        };
        let mut layout = pma_standard_layout();
        layout.extra_folders = vec![vec![pma_core::export::cut_folder::Token::text("BG")]];
        let plan = plan_export(&layout, &values, &[]);
        let job = CutFolderWrite {
            document: &document,
            plan: &plan,
            image_format: ImageFormat::Png,
            memo: "  memo  ",
            playback_range_end: None,
        };
        let target = write_cut_folder(&root, &job, &mut |_, _| {}).unwrap();
        assert_eq!(target, root.join("T_01_c002"));
        assert!(target.join("BG").is_dir());
        let sheet = fs::read_to_string(target.join("T_01_c002.xdts")).unwrap();
        assert!(sheet.starts_with("exchangeDigitalTimeSheet Save Data\n"));
        assert_eq!(
            fs::read(target.join("_memo.txt")).unwrap(),
            text_bytes("memo\n")
        );
        let contents = scan_cut_folder(&target).unwrap();
        assert_eq!(contents.timesheets, ["T_01_c002.xdts"]);
        fs::remove_dir_all(&root).unwrap();
    }
}
