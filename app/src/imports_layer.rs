//! 読み込みのうち、文書を置き換える・読み込んだあとのレイヤーへかけ直すもの
//! （Qt 版 main_window_import.py の cut_folder、main_window.py の binarize_layer_dialog・
//! reconvert_layer_dialog、color_reduction.py のプリセット、main_window_time_remap.py の
//! show_paste_dialog(file_path) の読み込み）。
//!
//! 計算は pma-core（`import::cut_folder`・`Document::from_animation`・`import::image`・
//! `tools::scope`）。重い処理は別のスレッドで行い、進み具合と結果を `import-job` の事件で
//! 送る（中止できる。画面は imports.js の runJob）。文書へは `Session::edit_with` で Undo 1回。

use crate::imports::{
    begin_job, finish, progress, read_premul, ImageRequest, ImportEvent, ImportsShared, Prepared,
    Straight,
};
use crate::session::{current_index, Session, Shared};
use crate::shell::ShellShared;
use pma_core::color_stats;
use pma_core::document::model::Document;
use pma_core::export::thumbnail::qt_unpremultiply;
use pma_core::image::RgbaImage;
use pma_core::import::cut_folder::{read_cut_folder, CutFolderRead};
use pma_core::import::image::{self as import_image, Conversion};
use pma_core::import::sequence::{reference_frame_indices, REFERENCE_FRAME_COUNT};
use pma_core::reference_scan::Progress;
use pma_core::tools::{bucket, scope};
use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{AppHandle, Manager, State};

fn err(e: impl ToString) -> String {
    e.to_string()
}

// ---------------------------------------------------------------------------
// カットフォルダーを開く（ImportController.cut_folder）
// ---------------------------------------------------------------------------

pub const CUT_TITLE: &str = "カットフォルダーを開く";

/// カットフォルダーを読む（Qt と同じく、読んでから置き換えを確かめる）。読めたら控え、
/// `import-job` の done（error も cancelled も無い）を送る。
#[tauri::command]
pub fn import_cut_folder(app: AppHandle, path: String) -> Result<(), String> {
    let fps = {
        let session = app.state::<Shared>();
        let s = session.lock().unwrap();
        s.timeline.fps as f64
    };
    let cancel = begin_job(&app)?;
    app.state::<ImportsShared>().lock().unwrap().cut = None;
    std::thread::spawn(move || {
        let root = Path::new(&path);
        let name = root
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| path.clone());
        progress(
            &app,
            0,
            0,
            format!("カットフォルダーを読み込んでいます：{name}"),
        );
        let read = read_folder(root, &name, fps);
        let event = if cancel.load(Ordering::Relaxed) {
            ImportEvent {
                cancelled: true,
                ..ImportEvent::default()
            }
        } else {
            match read {
                Ok(read) => {
                    app.state::<ImportsShared>().lock().unwrap().cut = Some((read, name));
                    ImportEvent::default()
                }
                Err(e) => ImportEvent {
                    error: Some((
                        CUT_TITLE.into(),
                        format!(
                            "読み込めませんでした。現在のドキュメントは変更されていません。\n\n{e}"
                        ),
                    )),
                    ..ImportEvent::default()
                },
            }
        };
        finish(&app, event);
    });
    Ok(())
}

/// フォルダーを一覧し、タイムシートとセル画像を読む。
pub fn read_folder(root: &Path, name: &str, fps: f64) -> Result<CutFolderRead, String> {
    let contents = pma_io::scan_cut_folder(root).map_err(err)?;
    read_cut_folder(
        name,
        &contents,
        &mut |relative| std::fs::read_to_string(root.join(relative)).map_err(err),
        &mut |relative| pma_io::read_image_file(&root.join(relative)).map_err(err),
        fps,
    )
}

/// 置き換えたあとの知らせ（下の帯）と、窓に出す追記。
#[derive(Clone, Debug, Serialize)]
pub struct CutApplied {
    pub message: String,
    pub notes: Vec<String>,
}

/// 今の文書をカットフォルダーで置き換える（履歴は空）。Qt の `_replace_document` と同じく、
/// メモ・描画色・表示・カラーチャートは残し、fps は今のまま。
pub fn replace_with_cut_folder(
    s: &mut Session,
    read: &CutFolderRead,
    name: &str,
) -> Result<CutApplied, String> {
    let parsed = &read.animation;
    let archived = crate::imports_clip::replace_document(s, parsed, false)?.1;
    let mut notes = read.notes.clone();
    if archived > 0 {
        notes.push(format!(
            "タイムシートで使われていないセル{archived}枚は連番に保持しました。"
        ));
    }
    Ok(CutApplied {
        message: format!(
            "「{name}」から{}列・{}フレームを読み込みました。",
            parsed.folder_count(),
            parsed.frame_count()
        ),
        notes,
    })
}

/// 控えた結果で今の文書を置き換える。
#[tauri::command]
pub fn import_cut_folder_apply(app: AppHandle) -> Result<CutApplied, String> {
    let (read, name) = app
        .state::<ImportsShared>()
        .lock()
        .unwrap()
        .cut
        .take()
        .ok_or("カットフォルダーを読み込めませんでした。")?;
    crate::with_session(&app, "open", |s| replace_with_cut_folder(s, &read, &name)).map_err(|e| {
        format!(
            "読み込み結果を反映できませんでした。現在のドキュメントは元の状態へ戻しました。\n\n{e}"
        )
    })
}

/// 読んだ結果を捨てる（置き換えをやめた）。
#[tauri::command]
pub fn import_cut_folder_discard(state: State<ImportsShared>) {
    state.lock().unwrap().cut = None;
}

// ---------------------------------------------------------------------------
// タイムシートのファイルを落としたとき（show_paste_dialog(file_path)）
// ---------------------------------------------------------------------------

/// タイムシートのファイルを文字列で読む（UTF-8、先頭の BOM は外す）。
#[tauri::command]
pub fn import_read_timesheet(path: String) -> Result<String, String> {
    let text =
        std::fs::read_to_string(&path).map_err(|e| format!("読み込めませんでした。\n\n{e}"))?;
    Ok(text.trim_start_matches('\u{feff}').to_string())
}

// ---------------------------------------------------------------------------
// 選択レイヤーへ範囲でかける（frame_scope.run_over、別のスレッド）
// ---------------------------------------------------------------------------

/// 範囲のセルへ `op` をかけた結果。
enum Scoped {
    Cancelled,
    Done(usize),
}

/// `cells` の絵へ `op` を別のスレッドでかけ、すべて済んでから Undo 1回（`label`）で書く。
/// セルごとの変換（変えるときだけ新しい絵を返す）。
type CellOp<'a> = dyn FnMut(scope::Cell, &RgbaImage) -> Result<Option<RgbaImage>, String> + 'a;

/// `op` は絵と番号を受け取り、変えるときだけ新しい絵を返す。
fn run_over(
    app: &AppHandle,
    cancel: &AtomicBool,
    doc: &Document,
    revision: (u64, u64),
    cells: &[scope::Cell],
    label: &'static str,
    op: &mut CellOp<'_>,
) -> Result<Scoped, String> {
    let mut computed: HashMap<scope::Cell, RgbaImage> = HashMap::new();
    let mut failure: Option<String> = None;
    let mut working = doc.clone();
    let emitter = app.clone();
    let mut report = |done: usize, total: usize| {
        progress(&emitter, done, total, format!("{label}（{done}/{total}）"));
        true
    };
    let outcome = scope::apply_over_cells(
        &mut working,
        cells,
        &mut Progress {
            report: Some(&mut report),
            cancel: Some(cancel),
        },
        |cell, image| {
            if failure.is_some() {
                return Ok(None);
            }
            match op(cell, image) {
                Ok(Some(out)) => {
                    computed.insert(cell, out);
                }
                Ok(None) => {}
                Err(e) => failure = Some(e),
            }
            Ok(None)
        },
    )
    .map_err(err)?;
    if let Some(e) = failure {
        return Err(e);
    }
    if outcome.cancelled {
        return Ok(Scoped::Cancelled);
    }
    if computed.is_empty() {
        return Ok(Scoped::Done(0));
    }
    let cells = cells.to_vec();
    crate::with_session(app, "paint", |s| {
        if s.content_revision() != revision {
            return Err("計算の間に絵が変わったので、取りやめました。".to_string());
        }
        let mut changed = 0;
        s.edit_with(Some(label), |doc, _| {
            let outcome =
                scope::apply_over_cells(doc, &cells, &mut Progress::default(), |cell, _| {
                    Ok(computed.remove(&cell).map(|image| (image, 0)))
                })
                .map_err(err)?;
            changed = outcome.changed_cells;
            Ok(((), changed > 0))
        })?;
        s.touch_pixels();
        Ok(Scoped::Done(changed))
    })
}

/// 計算を始める前の文書の写し・版・今のコマとレイヤー。
fn snapshot(app: &AppHandle) -> Result<(Document, (u64, u64)), String> {
    let session = app.state::<Shared>();
    let s = session.lock().unwrap();
    let p = s.project.as_ref().ok_or("作品を開いていません")?;
    Ok((p.document.clone(), s.content_revision()))
}

// ---------------------------------------------------------------------------
// 2値化…（binarize_layer_dialog）
// ---------------------------------------------------------------------------

/// 2値化の窓に出す、今の絵の調べ。
#[derive(Clone, Debug, Serialize)]
pub struct LayerInspection {
    pub color_count: usize,
    pub semi_transparent: usize,
    pub background: Option<[u8; 3]>,
    pub frames: usize,
}

fn straight(image: &RgbaImage) -> Result<Straight, String> {
    // Qt は as_qimage（乗算済み）から qimage_rgba_array で読む。
    let premul =
        import_image::to_premultiplied(image.width(), image.height(), image.rgba()).map_err(err)?;
    Ok(Straight {
        width: image.width(),
        height: image.height(),
        rgba: qt_unpremultiply(&premul),
    })
}

/// 今のレイヤーの絵を調べ、2値化の窓（imports.js の reduceDialog）の手本にする。
/// 今のコマに絵が無ければ Ok(None)（知らせは画面）。
pub fn inspect_layer(s: &Session) -> Result<Option<(LayerInspection, Prepared)>, String> {
    let doc = &s.project.as_ref().ok_or("作品を開いていません")?.document;
    let (frame, layer) = current_index(doc);
    let Some(&(key, _)) = scope::scope_cells(doc, frame, layer, false)
        .map_err(err)?
        .first()
    else {
        return Ok(None);
    };
    let first = straight(&bucket::position_image(doc, key, layer).map_err(err)?)?;
    let cells: Vec<_> = doc
        .initial_state_cells()
        .map_err(err)?
        .into_iter()
        .filter(|c| c.1 == layer)
        .collect();
    let references = reference_frame_indices(cells.len(), REFERENCE_FRAME_COUNT)
        .into_iter()
        .map(|i| straight(&bucket::position_image(doc, cells[i].0, layer).map_err(err)?))
        .collect::<Result<Vec<_>, String>>()?;
    let (w, h) = (first.width, first.height);
    let color_count = color_stats::opaque_rgb_color_count(&first.rgba, w, h).map_err(err)?;
    let semi_transparent = first
        .rgba
        .chunks_exact(4)
        .filter(|p| p[3] > 0 && p[3] < 255)
        .count();
    let background =
        import_image::detect_opaque_border_background(&first.rgba, w, h).map_err(err)?;
    let frames = references.len() + 1;
    Ok(Some((
        LayerInspection {
            color_count,
            semi_transparent,
            background,
            frames,
        },
        Prepared {
            paths: Vec::new(),
            first,
            references,
            background,
        },
    )))
}

#[tauri::command]
pub fn layer_binarize_inspect(app: AppHandle) -> Result<Option<LayerInspection>, String> {
    let inspected = {
        let session = app.state::<Shared>();
        let s = session.lock().unwrap();
        inspect_layer(&s).map_err(|e| format!("2値化の準備中にエラーが発生しました。\n{e}"))?
    };
    Ok(inspected.map(|(inspection, prepared)| {
        app.state::<ImportsShared>().lock().unwrap().prepared = Some(prepared);
        inspection
    }))
}

/// 読み込んだあとの絵に2値化をかける（`binarize_layer`）。今のコマ／すべてのコマ、Undo 1回。
#[tauri::command]
pub fn layer_binarize_run(
    app: AppHandle,
    form: crate::imports::ReduceForm,
    all_frames: bool,
) -> Result<(), String> {
    let detected = app
        .state::<ImportsShared>()
        .lock()
        .unwrap()
        .prepared
        .as_ref()
        .and_then(|p| p.background);
    let palette = form.palette.clone().ok_or(
        "現在の設定はまだプレビューへ反映されていません。先に「プレビュー」を押してください。",
    )?;
    let count = palette.len();
    let settings = form.settings(detected, palette);
    let (doc, revision) = snapshot(&app)?;
    let (frame, layer) = current_index(&doc);
    let cells = scope::scope_cells(&doc, frame, layer, all_frames).map_err(err)?;
    let cancel = begin_job(&app)?;
    std::thread::spawn(move || {
        let conversion = Conversion::Reduce(settings);
        let result = run_over(
            &app,
            &cancel,
            &doc,
            revision,
            &cells,
            "2値化",
            &mut |_, image| {
                let premul =
                    import_image::to_premultiplied(image.width(), image.height(), image.rgba())
                        .map_err(err)?;
                let reduced =
                    import_image::convert_with(&premul, false, &conversion).map_err(err)?;
                if reduced.pixels() == premul.pixels() {
                    return Ok(None);
                }
                import_image::to_document_image(&reduced)
                    .map(Some)
                    .map_err(err)
            },
        );
        let message = match result {
            Ok(Scoped::Cancelled) => "2値化を中止しました。".to_string(),
            Ok(Scoped::Done(0)) => "2値化：変わったセルはありませんでした。".to_string(),
            Ok(Scoped::Done(cells)) => format!("{cells}セルを{count}色で2値化しました。"),
            Err(e) => {
                return finish(
                    &app,
                    ImportEvent {
                        error: Some(("2値化".into(), e)),
                        ..ImportEvent::default()
                    },
                )
            }
        };
        finish(
            &app,
            ImportEvent {
                message: Some(message),
                ..ImportEvent::default()
            },
        );
    });
    Ok(())
}

// ---------------------------------------------------------------------------
// 元のファイルから再変換…（reconvert_layer_dialog）
// ---------------------------------------------------------------------------

/// 再変換の確かめ：元のファイルの一覧、または断り。
#[derive(Clone, Debug, Default, Serialize)]
pub struct ReconvertCheck {
    pub paths: Vec<String>,
    /// 下の帯の知らせ（このアプリで読み込んだレイヤーでない）。
    pub status: Option<String>,
    /// 窓の断り（元のファイルが無い）。
    pub warning: Option<String>,
}

pub fn reconvert_check(s: &Session) -> Result<ReconvertCheck, String> {
    let doc = &s.project.as_ref().ok_or("作品を開いていません")?.document;
    let (_, layer) = current_index(doc);
    let paths = doc
        .track_order()
        .get(layer)
        .and_then(|t| doc.track(*t))
        .and_then(|t| t.import_sources.clone())
        .unwrap_or_default();
    if paths.is_empty() {
        return Ok(ReconvertCheck {
            status: Some(
                "再変換できるのは、このアプリを起動してから連番として読み込んだレイヤーだけです。"
                    .into(),
            ),
            ..ReconvertCheck::default()
        });
    }
    let missing: Vec<&String> = paths.iter().filter(|p| !Path::new(p).is_file()).collect();
    if let Some(first) = missing.first() {
        return Ok(ReconvertCheck {
            warning: Some(format!(
                "元のファイルが見つかりません（{}枚）。\n{first}",
                missing.len()
            )),
            ..ReconvertCheck::default()
        });
    }
    Ok(ReconvertCheck {
        paths,
        ..ReconvertCheck::default()
    })
}

#[tauri::command]
pub fn layer_reconvert_check(session: State<Shared>) -> Result<ReconvertCheck, String> {
    reconvert_check(&session.lock().unwrap())
}

/// 今のレイヤーの各番号へ、元のファイルを決めた設定で変換し直して入れる（`reconvert_layer`）。
/// すべてのコマ、Undo 1回「再変換」。`request` は画像の読み込みと同じ形（paths は元のファイル）。
#[tauri::command]
pub fn layer_reconvert_run(app: AppHandle, request: ImageRequest) -> Result<(), String> {
    let detected = {
        let state = app.state::<ImportsShared>();
        let guard = state.lock().unwrap();
        guard
            .prepared
            .as_ref()
            .filter(|p| p.paths == request.paths)
            .and_then(|p| p.background)
    };
    let (doc, revision) = snapshot(&app)?;
    let (frame, layer) = current_index(&doc);
    let track = doc
        .track_order()
        .get(layer)
        .and_then(|t| doc.track(*t))
        .ok_or("レイヤーがありません")?;
    let paths = track.import_sources.clone().unwrap_or_default();
    let conversion = if track.settings.is_draft {
        Conversion::Draft
    } else {
        crate::imports::conversion(&request, detected)?
    };
    let cells = scope::scope_cells(&doc, frame, layer, true).map_err(err)?;
    let cancel = begin_job(&app)?;
    std::thread::spawn(move || {
        let (width, height) = doc.size();
        let tid = doc.track_order()[layer];
        let number_of = |cell: scope::Cell| -> Option<i64> {
            let fid = *doc.frame_order().get(cell.0)?;
            doc.frame(fid)?
                .positions
                .get(&tid)?
                .number()
                .map(|n| n.get())
        };
        let result = run_over(
            &app,
            &cancel,
            &doc,
            revision,
            &cells,
            "再変換",
            &mut |cell, _| {
                let Some(number) = number_of(cell) else {
                    return Ok(None);
                };
                if number < 1 || number as usize > paths.len() {
                    return Ok(None);
                }
                let path = &paths[number as usize - 1];
                // Qt は読めないファイルを飛ばす。
                let Ok(image) = read_premul(path) else {
                    return Ok(None);
                };
                let converted = import_image::convert_with(
                    &image,
                    import_image::is_jpeg_path(path),
                    &conversion,
                )
                .map_err(err)?;
                let placed =
                    import_image::place_centered(width, height, &converted).map_err(err)?;
                import_image::to_document_image(&placed)
                    .map(Some)
                    .map_err(err)
            },
        );
        let event = match result {
            Ok(Scoped::Done(cells)) if cells > 0 => ImportEvent {
                message: Some(format!("{cells}セルを元のファイルから変換し直しました。")),
                ..ImportEvent::default()
            },
            Ok(Scoped::Cancelled) => ImportEvent {
                cancelled: true,
                ..ImportEvent::default()
            },
            Ok(Scoped::Done(_)) => ImportEvent::default(),
            Err(e) => ImportEvent {
                error: Some(("再変換".into(), e)),
                ..ImportEvent::default()
            },
        };
        finish(&app, event);
    });
    Ok(())
}

// ---------------------------------------------------------------------------
// 2値化のプリセット（preferences.binarize_presets）
// ---------------------------------------------------------------------------

/// 設定の鍵（preferences.BINARIZE_PRESETS_KEY）。
const PRESETS_KEY: &str = "binarize_presets";

fn presets(app: &AppHandle) -> serde_json::Map<String, Value> {
    let shell = app.state::<ShellShared>();
    let sh = shell.lock().unwrap();
    match sh.prefs.get(PRESETS_KEY) {
        // 壊れた値は捨てる（名前が空・設定が表でない）。
        Some(Value::Object(map)) => map
            .iter()
            .filter(|(name, v)| !name.trim().is_empty() && v.is_object())
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect(),
        _ => Default::default(),
    }
}

/// 名前を付けて保存した2値化の設定 {名前: 設定}。
#[tauri::command]
pub fn binarize_presets(app: AppHandle) -> serde_json::Map<String, Value> {
    presets(&app)
}

/// 設定を保存する（名前は前後の空白を除く。空なら保存しない）。
#[tauri::command]
pub fn binarize_preset_save(
    app: AppHandle,
    name: String,
    settings: Value,
) -> Result<Option<String>, String> {
    let name = name.trim().to_string();
    if name.is_empty() {
        return Ok(None);
    }
    if !settings.is_object() {
        return Err("プリセットの設定が不正です".into());
    }
    let mut all = presets(&app);
    all.insert(name.clone(), settings);
    let shell = app.state::<ShellShared>();
    shell
        .lock()
        .unwrap()
        .prefs
        .set(PRESETS_KEY, Value::Object(all))?;
    Ok(Some(name))
}

#[tauri::command]
pub fn binarize_preset_delete(app: AppHandle, name: String) -> Result<(), String> {
    let mut all = presets(&app);
    if all.remove(&name).is_some() {
        let shell = app.state::<ShellShared>();
        shell
            .lock()
            .unwrap()
            .prefs
            .set(PRESETS_KEY, Value::Object(all))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn session(name: &str) -> Session {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../tests/fixtures/projects/v2")
            .join(name);
        let mut s = Session::unloaded(8, 8);
        s.open(&path).unwrap();
        s
    }

    #[test]
    fn binarize_inspection_reads_the_current_layer() {
        let s = session("basic-bank-sheet.pman");
        let inspected = inspect_layer(&s).unwrap();
        if let Some((inspection, prepared)) = inspected {
            assert_eq!(inspection.frames, prepared.references.len() + 1);
            assert!(prepared.paths.is_empty());
        }
    }

    #[test]
    fn reconvert_refuses_layers_without_sources() {
        let s = session("basic-bank-sheet.pman");
        let check = reconvert_check(&s).unwrap();
        assert!(check.paths.is_empty());
        assert!(check.status.unwrap().contains("再変換できるのは"));
    }
}
