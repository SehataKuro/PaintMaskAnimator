//! CLIP STUDIO アニメーションの読み込み（Qt の `ImportController.clip_animation` と
//! `InitialStateController.import_clip_line_art`）。
//!
//! 読み取りと合成は pma-clip-import → pma-core（import::clip・clip_render）、文書は
//! `Document::from_animation`。ここは別のスレッドで読み、進み具合と結果を `import-job` で送る。
//! 置き換えの確かめ（保存する／保存せず読み込む）は画面（imports.js）が尋ねてから
//! `import_clip_apply` を呼ぶ（Qt と同じく、読んでから尋ねる）。

use crate::imports::{apply_line_art, begin_job, finish, ImportEvent, ImportsShared, LineArt};
use crate::project_meta;
use crate::session::{Session, Shared};
use pma_core::document::model::{Document, Reference};
use pma_core::export::py::format_g;
use pma_core::import::clip::{check_frames, ClipAnimation, ClipError};
use pma_core::project::json::Json;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{AppHandle, Emitter, Manager, State};

/// 読み込みの窓の題名（_ClipImportDialog・失敗の窓）。
pub const CLIP_TITLE: &str = "CLIP STUDIOアニメーション読み込み";
const LINE_ART_TITLE: &str = "CLIPの線画を初期状態として読み込む";

/// 進み具合（Qt の _ClipProgressDisplay：全体が 0 なら終わりの見えない棒）。
fn progress(app: &AppHandle, value: usize, total: usize, label: String) {
    let _ = app.emit(
        "import-job",
        ImportEvent {
            phase: "progress",
            value,
            total,
            label,
            ..ImportEvent::default()
        },
    );
}

/// .clip を読む（中止したら Ok(None)）。`build_pma_frames` の上限の検査もここで済ませる
/// （Qt は読む側のスレッドで並べるので、失敗は「読み込めませんでした」になる）。
fn read(
    app: &AppHandle,
    path: &str,
    cancel: &AtomicBool,
) -> Result<Option<ClipAnimation>, ClipError> {
    let read =
        pma_clip_import::read_clip_animation(Path::new(path), &mut |value, total, label| {
            progress(app, value, total, label);
            !cancel.load(Ordering::Relaxed)
        })?;
    let Some(read) = read else {
        return Ok(None);
    };
    check_frames(
        read.width,
        read.height,
        read.start_frame,
        read.end_frame,
        &read.layers,
    )?;
    Ok(Some(read))
}

/// CLIP STUDIO アニメーションを別のスレッドで読む。読めたら結果を控え、`import-job` の
/// done（error も cancelled も無い）を送る。画面はそのあと確かめて `import_clip_apply` を呼ぶ。
#[tauri::command]
pub fn import_clip(app: AppHandle, path: String) -> Result<(), String> {
    let cancel = begin_job(&app)?;
    app.state::<ImportsShared>().lock().unwrap().clip = None;
    std::thread::spawn(move || {
        let event = match read(&app, &path, &cancel) {
            Ok(Some(parsed)) => {
                app.state::<ImportsShared>().lock().unwrap().clip = Some(parsed);
                ImportEvent::default()
            }
            Ok(None) => ImportEvent {
                cancelled: true,
                ..ImportEvent::default()
            },
            Err(e) => ImportEvent {
                error: Some((
                    CLIP_TITLE.into(),
                    format!(
                        "読み込めませんでした。現在のドキュメントは変更されていません。\n\n{e}"
                    ),
                )),
                ..ImportEvent::default()
            },
        };
        finish(&app, event);
    });
    Ok(())
}

/// 置き換える前に尋ねるか（Qt の `_clip_import_needs_confirmation`）：保存先がある、
/// コマかレイヤーが1つでない、絵か空セルがある。
pub fn needs_confirmation(s: &Session) -> bool {
    let Some(p) = &s.project else {
        return false;
    };
    if p.path.is_some() {
        return true;
    }
    let doc = &p.document;
    if doc.frame_order().len() != 1 || doc.track_order().len() != 1 {
        return true;
    }
    doc.frame(doc.frame_order()[0]).is_some_and(|frame| {
        frame
            .positions
            .values()
            .any(|position| position.reference != Reference::Empty)
    })
}

#[tauri::command]
pub fn import_clip_needs_confirm(session: State<Shared>) -> bool {
    needs_confirmation(&session.lock().unwrap())
}

/// 読んだ結果を捨てる（置き換えをやめた）。
#[tauri::command]
pub fn import_clip_discard(state: State<ImportsShared>) {
    state.lock().unwrap().clip = None;
}

/// Python の `max(1, min(60, int(round(fps))))`。
fn display_fps(fps: f64) -> i64 {
    (fps.round_ties_even() as i64).clamp(1, 60)
}

/// 置き換えたあとの付帯情報。Qt は窓の状態（メモ・描画色・表示・カラーチャート）を
/// 残し、使用色フォルダーは空にし、版・fps・再生範囲・表示の種類・CLIP の出どころは改める。
/// `clip` が偽（カットフォルダー）なら CLIP の出どころは書かない（Qt の source_metadata=None）。
fn replaced_metadata(s: &mut Session, parsed: &ClipAnimation, fps: i64, clip: bool) -> Json {
    let mut metadata = project_meta::new_metadata(parsed.width, parsed.height);
    if s.project.is_some() {
        project_meta::store_from_session(s);
        if let Some(p) = &mut s.project {
            s.colors.store(&s.tools.settings, &mut p.metadata);
            for key in ["memo", "colors", "display", "color_chart"] {
                if let Some(value) = p.metadata.get(key) {
                    metadata.set(key, value.clone());
                }
            }
        }
    }
    metadata.set("fps", Json::Int(fps.to_string()));
    if !clip {
        return metadata;
    }
    metadata.set(
        "clip_studio_source",
        Json::Object(vec![
            ("file_name".into(), Json::Str(parsed.source_name.clone())),
            (
                "timeline_name".into(),
                Json::Str(parsed.timeline_name.clone()),
            ),
            ("fps".into(), Json::Float(parsed.fps)),
            (
                "start_frame".into(),
                Json::Int(parsed.start_frame.to_string()),
            ),
            ("end_frame".into(), Json::Int(parsed.end_frame.to_string())),
        ]),
    );
    metadata
}

/// 今の文書を読んだアニメーションで置き換え（履歴は空）、絵を色塗り前の初期状態として
/// 保持する（Qt の `_replace_document` と `initial_state.keep(confirm=False)`）。知らせの文を返す。
pub fn replace_with_clip(s: &mut Session, parsed: &ClipAnimation) -> Result<String, String> {
    let ((content_keys, archived), fps) =
        (replace_document(s, parsed, true)?, display_fps(parsed.fps));
    let kept = s.edit_with(None, |doc, _| {
        let kept = doc.keep_initial_images(None);
        Ok((kept, kept > 0))
    })?;
    let archive_note = if archived > 0 {
        format!(" 未配置セル{archived}枚は連番に保持しました。")
    } else {
        String::new()
    };
    let fps_note = if (parsed.fps - fps as f64).abs() > 1e-6 {
        format!("（元FPS {}、PMA表示 {fps} fps）", format_g(parsed.fps))
    } else {
        String::new()
    };
    // Qt の keep() は絵のあるキーごとに数える（同じ番号を2度置いても2つ）。
    let initial_note = if kept > 0 {
        format!(" {content_keys}セルを色塗り前の初期状態として保持しました。")
    } else {
        String::new()
    };
    Ok(format!(
        "CLIP STUDIOから{}フォルダー・{}フレームを読み込みました。{archive_note}{fps_note}{initial_note}",
        parsed.folder_count(),
        parsed.frame_count()
    ))
}

/// 今の文書を読んだアニメーションで置き換える（Qt の `_replace_document`、履歴は空）。
/// CLIP とカットフォルダーで共通。(絵のあるキーの数, 連番に残したセルの数) を返す。
// 【読み込み・編集：Group B】カットフォルダー（imports_layer.rs）も使う。
pub fn replace_document(
    s: &mut Session,
    parsed: &ClipAnimation,
    clip: bool,
) -> Result<(usize, usize), String> {
    let replaced = s
        .project
        .as_ref()
        .map_or((parsed.width, parsed.height), |p| p.document.size());
    let built = Document::from_animation(
        parsed.width,
        parsed.height,
        parsed.start_frame,
        parsed.end_frame,
        &parsed.layers,
        replaced,
    )
    .map_err(|e| e.to_string())?;
    let fps = display_fps(parsed.fps);
    let metadata = replaced_metadata(s, parsed, fps, clip);
    s.open_document(built.document)?;
    if let Some(p) = &mut s.project {
        p.metadata = metadata;
    }
    project_meta::load_into_timeline(s);
    Ok((built.content_keys, built.archived))
}

/// 控えた結果で今の文書を置き換える。失敗は Qt の文言（文書は元のまま）。
#[tauri::command]
pub fn import_clip_apply(app: AppHandle) -> Result<String, String> {
    let parsed = app
        .state::<ImportsShared>()
        .lock()
        .unwrap()
        .clip
        .take()
        .ok_or("CLIP STUDIOアニメーションを読み込めませんでした。")?;
    crate::with_session(&app, "open", |s| replace_with_clip(s, &parsed)).map_err(|e| {
        format!(
            "読み込み結果を反映できませんでした。現在のドキュメントは元の状態へ戻しました。\n\n{e}"
        )
    })
}

/// CLIP のセルを、アニメーションフォルダー名とセル番号で今の絵の初期状態に合わせる
/// （Undo 1回「初期状態の読み込み」）。
#[tauri::command]
pub fn import_clip_initial_state(app: AppHandle, path: String) -> Result<(), String> {
    let cancel = begin_job(&app)?;
    std::thread::spawn(move || {
        let fail = |text: String| ImportEvent {
            error: Some((
                LINE_ART_TITLE.into(),
                format!(
                    "線画を読み込めませんでした。現在の絵と初期状態は変更していません。\n\n{text}"
                ),
            )),
            ..ImportEvent::default()
        };
        let event = match pma_clip_import::read_clip_animation(
            Path::new(&path),
            &mut |value, total, label| {
                progress(&app, value, total, label);
                !cancel.load(Ordering::Relaxed)
            },
        ) {
            Err(e) => fail(e.to_string()),
            Ok(None) => ImportEvent {
                cancelled: true,
                ..ImportEvent::default()
            },
            Ok(Some(parsed)) => {
                progress(&app, 0, 0, "線画の初期状態を読み込んでいます…".into());
                let doc = {
                    let session = app.state::<Shared>();
                    let s = session.lock().unwrap();
                    s.project.as_ref().map(|p| p.document.clone())
                };
                match doc.map(|doc| doc.match_clip_line_art(&parsed)) {
                    None => fail("作品を開いていません".into()),
                    Some(Err(e)) => fail(e),
                    Some(Ok(_)) if cancel.load(Ordering::Relaxed) => ImportEvent {
                        cancelled: true,
                        ..ImportEvent::default()
                    },
                    Some(Ok(art)) if art.images.is_empty() => ImportEvent {
                        info: Some((
                            LINE_ART_TITLE.into(),
                            "一致するセルがありません。読み込み元のレイヤー名とセル番号を確認してください。"
                                .into(),
                        )),
                        ..ImportEvent::default()
                    },
                    Some(Ok(art)) => {
                        let art = LineArt {
                            images: art.images,
                            missing: art.missing,
                        };
                        match crate::with_session(&app, "import", |s| apply_line_art(s, art)) {
                            Ok(message) => ImportEvent {
                                message: Some(message),
                                ..ImportEvent::default()
                            },
                            Err(e) => fail(e),
                        }
                    }
                }
            }
        };
        finish(&app, event);
    });
    Ok(())
}

#[cfg(test)]
#[path = "imports_clip_tests.rs"]
mod tests;
