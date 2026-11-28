//! 編集メニューの操作のうち、窓を出してから文書へ書くもの（Qt 版 main_window.py の
//! resize_doc、main_window_initial_state.py の keep・clear、main_window_line_ops.py の
//! tidy_lines_dialog の画面の側）。
//!
//! 計算は pma-core（`Document::resize_canvas`・`initial_state_cells`・
//! `keep_initial_state`・`clear_initial_state`・`image_ops::line_tidy`・`tools::scope`）。
//! ここは Qt と同じ名前で履歴に積み、Qt と同じ言葉で知らせる。

use crate::session::{current_index, Session, Shared};
use pma_core::image_ops::line_tidy::{self, Diagonal, PreviewOptions};
use pma_core::image_ops::MaskRef;
use pma_core::reference_scan::Progress;
use pma_core::tools::{bucket, scope};
use serde::Serialize;
use tauri::ipc::Response;
use tauri::{AppHandle, State};

fn err(e: impl ToString) -> String {
    e.to_string()
}

// ---------------------------------------------------------------------------
// キャンバスサイズの変更（resize_doc）
// ---------------------------------------------------------------------------

/// Qt の DocUndo（push_doc_undo）の名前。
const DOC_LABEL: &str = "全体編集";

/// キャンバスの大きさを変える（左上をそろえ、はみ出す分は切る）。Undo 1回「全体編集」。
/// Qt の replace_doc(preserve=True) と同じく、今のコマ・レイヤーは先頭に戻し、選択範囲を解く。
pub fn resize_canvas(s: &mut Session, width: usize, height: usize) -> Result<bool, String> {
    let max = pma_core::import::image::MAX_IMAGE_DIMENSION;
    if !(64..=max).contains(&width) || !(64..=max).contains(&height) {
        return Err(format!("キャンバスの大きさは 64〜{max}px です。"));
    }
    let changed = s.edit_with(Some(DOC_LABEL), |doc, _| {
        let resized = doc.resize_canvas(width, height).map_err(err)?;
        let moved = current_index(doc) != (0, 0);
        if doc.track_order().is_empty() || doc.frame_order().is_empty() {
            return Ok((resized, resized));
        }
        doc.set_selection_index(0, 0).map_err(err)?;
        Ok((resized, resized || moved))
    })?;
    s.tools.selection.clear();
    Ok(changed)
}

#[tauri::command]
pub fn edit_resize_canvas(app: AppHandle, width: usize, height: usize) -> Result<bool, String> {
    crate::with_session(&app, "edit", |s| resize_canvas(s, width, height))
}

/// 今のキャンバスの大きさ（窓の初めの値）。
#[tauri::command]
pub fn edit_canvas_size(session: State<Shared>) -> Option<(usize, usize)> {
    let s = session.lock().unwrap();
    s.project.as_ref().map(|p| p.document.size())
}

// ---------------------------------------------------------------------------
// 色塗り前の初期状態（InitialStateController.keep・clear）
// ---------------------------------------------------------------------------

/// 初期状態の操作の結果：尋ねる文（題名, 本文）か、知らせ。
#[derive(Clone, Debug, Default, Serialize)]
pub struct InitialOutcome {
    pub confirm: Option<(String, String)>,
    pub message: Option<String>,
    /// 消した（画面は「初期状態と見比べる」を外す）。
    pub cleared: bool,
}

impl InitialOutcome {
    fn say(message: String) -> Self {
        Self {
            message: Some(message),
            ..Self::default()
        }
    }
}

/// 今の絵を初期状態として保持する（`keep(confirm=True)`）。すでに保持しているセルが
/// あれば、`confirmed` でない限り上書きしてよいか尋ねる。Qt も履歴に積まない。
pub fn keep_initial_state(s: &mut Session, confirmed: bool) -> Result<InitialOutcome, String> {
    let doc = &s.project.as_ref().ok_or("作品を開いていません")?.document;
    let cells = doc.initial_state_cells().map_err(err)?;
    if cells.is_empty() {
        return Ok(InitialOutcome::say(
            "初期状態として保持できるセルがありません。".into(),
        ));
    }
    let kept = doc.count_initial_state(&cells);
    if kept > 0 && !confirmed {
        return Ok(InitialOutcome {
            confirm: Some((
                "今の絵を初期状態として保持".into(),
                format!(
                    "{kept}セルはすでに色塗り前の初期状態を保持しています。今の絵で上書きしますか？\n塗ったあとの絵で上書きすると、色塗り前の状態には戻せなくなります。"
                ),
            )),
            ..InitialOutcome::default()
        });
    }
    let count = s.edit_with(None, |doc, _| {
        let count = doc.keep_initial_state(&cells).map_err(err)?;
        Ok((count, true))
    })?;
    Ok(InitialOutcome::say(format!(
        "{count}セルを色塗り前の初期状態として保持しました。保存するとプロジェクトに入ります。"
    )))
}

/// 保持した初期状態を消す（`clear`）。`confirmed` でなければ尋ねる。Qt も履歴に積まない。
pub fn clear_initial_state(s: &mut Session, confirmed: bool) -> Result<InitialOutcome, String> {
    let doc = &s.project.as_ref().ok_or("作品を開いていません")?.document;
    let cells = doc.initial_state_cells().map_err(err)?;
    let kept = doc.count_initial_state(&cells);
    if kept == 0 {
        return Ok(InitialOutcome::say(
            "色塗り前の初期状態を保持していません。".into(),
        ));
    }
    if !confirmed {
        return Ok(InitialOutcome {
            confirm: Some((
                "保持した初期状態を消す".into(),
                format!("{kept}セルで保持している色塗り前の初期状態を消しますか？"),
            )),
            ..InitialOutcome::default()
        });
    }
    s.edit_with(None, |doc, _| {
        doc.clear_initial_state(&cells).map_err(err)?;
        Ok(((), true))
    })?;
    Ok(InitialOutcome {
        message: Some(format!("{kept}セルの初期状態を消しました。")),
        cleared: true,
        ..InitialOutcome::default()
    })
}

/// `kind`：keep / clear。
#[tauri::command]
pub fn edit_initial_state(
    app: AppHandle,
    kind: String,
    confirmed: bool,
) -> Result<InitialOutcome, String> {
    crate::with_session(&app, "edit", |s| match kind.as_str() {
        "keep" => keep_initial_state(s, confirmed),
        "clear" => clear_initial_state(s, confirmed),
        other => Err(format!("知らない初期状態の操作：{other}")),
    })
}

// ---------------------------------------------------------------------------
// 線整え…（tidy_lines_dialog）
// ---------------------------------------------------------------------------

/// 窓の選び：2×2の45°の線（add / remove / null）と範囲。
#[derive(Clone, Debug, serde::Deserialize)]
pub struct TidyForm {
    pub diagonal: Option<String>,
    pub all_frames: bool,
    pub in_selection: bool,
}

impl TidyForm {
    fn diagonal(&self) -> Diagonal {
        match self.diagonal.as_deref() {
            Some("add") => Diagonal::Add,
            Some("remove") => Diagonal::Remove,
            _ => Diagonal::Keep,
        }
    }
}

/// 窓を開く前の確かめ。
#[derive(Clone, Debug, Serialize)]
pub struct TidyDialog {
    /// 整える線の色が無いときの知らせ（窓は出さない）。
    pub message: Option<String>,
    /// 選択範囲がある（範囲の既定を「選択範囲（今のコマ）」にする）。
    pub has_selection: bool,
}

/// 線整えの対象色（`tidy_line_colours`）：使用色パネルで選んだ色。無ければ今のコマの線の色。
fn tidy_colours(s: &Session) -> Result<Vec<[u8; 3]>, String> {
    let doc = &s.project.as_ref().ok_or("作品を開いていません")?.document;
    let (frame, layer) = current_index(doc);
    let image = bucket::position_image(doc, frame, layer).map_err(err)?;
    let selected = crate::paint_ops::used_selection(s);
    Ok(line_tidy::tidy_line_colours(&image, &selected))
}

const NO_COLOURS: &str = "線整え：整える線の色がありません。使用色パネルで線の色を選んでください。";

#[tauri::command]
pub fn edit_tidy_dialog(session: State<Shared>) -> Result<TidyDialog, String> {
    let s = session.lock().unwrap();
    let colours = tidy_colours(&s)?;
    Ok(TidyDialog {
        message: colours.is_empty().then(|| NO_COLOURS.to_string()),
        has_selection: s.tools.selection.selection().is_some(),
    })
}

/// 選んだ範囲のマスク（選択範囲の中だけのとき）。
fn tidy_region(s: &Session, form: &TidyForm) -> Option<Vec<u8>> {
    form.in_selection
        .then(|| crate::paint_ops::selection_mask(s))
        .flatten()
}

/// プレビュー（`line_tidy_preview`）。[足す u32][消す u32][幅 u32][高さ u32][RGBA]。
/// 直す所が無ければ幅・高さが 0。
#[tauri::command]
pub fn edit_tidy_preview(session: State<Shared>, form: TidyForm) -> Result<Response, String> {
    let s = session.lock().unwrap();
    let colours = tidy_colours(&s)?;
    let doc = &s.project.as_ref().ok_or("作品を開いていません")?.document;
    let (frame, layer) = current_index(doc);
    let image = bucket::position_image(doc, frame, layer).map_err(err)?;
    let region = tidy_region(&s, &form);
    let mask = region.as_deref().map(|data| MaskRef {
        data,
        width: image.width(),
        height: image.height(),
    });
    let preview = line_tidy::tidy_preview(
        &image,
        &colours,
        form.diagonal(),
        mask,
        PreviewOptions::default(),
    )
    .map_err(err)?;
    let mut out = Vec::new();
    match preview {
        Some(p) => {
            for v in [p.added, p.removed, p.image.width(), p.image.height()] {
                out.extend_from_slice(&(v as u32).to_le_bytes());
            }
            out.extend_from_slice(p.image.rgba());
        }
        None => out.extend_from_slice(&[0; 16]),
    }
    Ok(Response::new(out))
}

/// 線を整える（`tidy_lines`）。今のコマ／すべてのコマ、Undo 1回「線整え」。知らせを返す。
pub fn tidy_lines(s: &mut Session, form: &TidyForm) -> Result<String, String> {
    let colours = tidy_colours(s)?;
    if colours.is_empty() {
        return Ok(NO_COLOURS.into());
    }
    let region = tidy_region(s, form);
    let doc = &s.project.as_ref().ok_or("作品を開いていません")?.document;
    let (frame, layer) = current_index(doc);
    let cells = scope::scope_cells(doc, frame, layer, form.all_frames).map_err(err)?;
    let mut outcome = scope::TidyOutcome::default();
    s.edit_with(Some("線整え"), |doc, _| {
        outcome = scope::tidy_lines(
            doc,
            &cells,
            &colours,
            form.diagonal(),
            region.as_deref(),
            &mut Progress::default(),
        )
        .map_err(err)?;
        Ok(((), outcome.scope.changed_cells > 0))
    })?;
    if outcome.scope.changed_cells == 0 {
        return Ok("線整え：直す所はありませんでした。".into());
    }
    s.touch_pixels();
    Ok(format!(
        "線整え：{}セルで{}px足し、{}px消しました。",
        outcome.scope.changed_cells, outcome.added, outcome.removed
    ))
}

#[tauri::command]
pub fn edit_tidy_apply(app: AppHandle, form: TidyForm) -> Result<String, String> {
    crate::with_session(&app, "paint", |s| tidy_lines(s, &form))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Session {
        let mut s = Session::unloaded(8, 8);
        s.new_document(80, 70).unwrap();
        s
    }

    #[test]
    fn resize_canvas_records_one_undo_and_rejects_small_sizes() {
        let mut s = sample();
        assert!(resize_canvas(&mut s, 10, 70).is_err());
        assert!(resize_canvas(&mut s, 100, 64).unwrap());
        assert_eq!(s.project.as_ref().unwrap().document.size(), (100, 64));
        assert_eq!(s.history().undo.len(), 1);
        assert!(!resize_canvas(&mut s, 100, 64).unwrap());
        assert_eq!(s.history().undo.len(), 1);
        s.undo().unwrap();
        assert_eq!(s.project.as_ref().unwrap().document.size(), (80, 70));
    }

    #[test]
    fn keep_asks_before_overwriting_and_clear_asks_first() {
        let mut s = sample();
        // 新しい文書の空の絵は保持できない。
        let out = keep_initial_state(&mut s, false).unwrap();
        assert!(out.message.is_some() && out.confirm.is_none());
        let out = clear_initial_state(&mut s, false).unwrap();
        assert_eq!(
            out.message.as_deref(),
            Some("色塗り前の初期状態を保持していません。")
        );
        assert!(s.history().undo.is_empty());
    }
}
