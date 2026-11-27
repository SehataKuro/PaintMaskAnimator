//! 選択範囲の変形（自由変形・拡大縮小・メッシュ変形・90°回転・確定・キャンセル）。
//!
//! Qt 版の `main_window_line_ops.start_wire_transform`・`canvas_selection` の
//! `begin_selection_transform`／`commit_selection_transform`／`cancel_selection_transform`／
//! `rotate_selection_transform`、`canvas_transform_geometry`（ハンドル・ドラッグ）、
//! `canvas_transform_mask.set_transform_mesh_grid` に当たる。計算は pma-core の
//! `tools::transform::TransformSession`（ハンドルの半径 14/表示倍率、回転のつまみ 16/表示倍率、
//! 移動は 1 画素単位 #116）。
//!
//! - 変形のあいだ、文書の選択レイヤーには選択の中を消した絵を置く（Qt と同じ。履歴には
//!   積まない）。始める前の文書は [`ActiveTransform`] が持ち、確定・キャンセルでそこへ戻す。
//! - 確定は、始める前の文書に戻してから `Session::edit_with` で 1 回の編集として書く。
//!   履歴の名前は Qt と同じ（1 コマなら「描画（A の 1番）」、すべてのコマなら「色編集」）。
//! - 変形の途中の形の取り消し（Qt の TransformUndo「変形操作」）は、変形のあいだだけ
//!   取り消し・やり直しのキーで戻す（session.rs の step_history から呼ぶ）。

use crate::session::{current_index, Session};
use crate::tools::{drawing_label, select_used_area};
use pma_core::document::model::Document;
use pma_core::image::Rect;
use pma_core::reference_scan::Progress;
use pma_core::tools::scope;
use pma_core::tools::transform::{Mode, TransformOptions, TransformSession};
use serde::Serialize;

/// 変形の途中の状態。
pub struct ActiveTransform {
    pub session: TransformSession,
    /// 変形しているコマとレイヤー（Qt の transform_frame_index・transform_layer_index）。
    pub frame: usize,
    pub layer: usize,
    /// 変形を始める前の文書（キャンセル・確定でここへ戻す）。
    before: Document,
    /// 「すべてのコマに適用」（始めたときの値。Qt の transform_apply_all_frames）。
    all_frames: bool,
    /// 選択の輪郭（始めたときの形。今の形へ写して点線で引く）。
    outlines: Vec<Vec<(f64, f64)>>,
    /// クオリティ変形の線の色（始めたときの使用色の選択）。
    line_colors: usize,
}

/// 画面へ渡す変形の状態（`tools-changed` の `transform`）。
#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct TransformView {
    /// scale / free / mesh。
    pub mode: &'static str,
    /// 変形の点（四角形は左上から時計回りの 4 点、メッシュは行優先の格子）。
    pub points: Vec<[f64; 2]>,
    pub cols: usize,
    pub rows: usize,
    /// 選択の輪郭を今の形へ写したもの。
    pub outlines: Vec<Vec<[f64; 2]>>,
    /// クオリティ変形で描いているか。
    pub quality: bool,
    /// クオリティ変形で実線として扱う使用色の数（実線の太さを出すか）。
    pub line_colors: usize,
    /// 形の取り消しの数（ヒストリーの「変形操作」）。
    pub history: usize,
}

fn mode_name(mode: Mode) -> &'static str {
    match mode {
        Mode::Scale => "scale",
        Mode::Free => "free",
        Mode::Mesh => "mesh",
    }
}

impl ActiveTransform {
    pub fn view(&self) -> TransformView {
        let (cols, rows) = self.session.mesh_size();
        TransformView {
            mode: mode_name(self.session.mode()),
            points: self.session.points().iter().map(|p| [p.0, p.1]).collect(),
            cols,
            rows,
            outlines: self
                .outlines
                .iter()
                .map(|o| {
                    self.session
                        .transformed_outline(o)
                        .into_iter()
                        .map(|p| [p.0, p.1])
                        .collect()
                })
                .collect(),
            quality: self.session.quality(),
            line_colors: self.line_colors,
            history: self.session.history_len(),
        }
    }
}

/// 変形を始めた・操作したときの知らせ（状態の帯に出す）。
#[derive(Clone, Debug, Default, Serialize, PartialEq)]
pub struct Outcome {
    pub message: Option<String>,
}

fn options(s: &Session) -> TransformOptions {
    let settings = &s.tools.settings;
    TransformOptions {
        quality: settings.transform_quality,
        line_colors: s.tools.paint.used_selection.clone(),
        line_threshold: settings.transform_line_width.clamp(1, 254) as u8,
        mesh_cols: settings.transform_mesh_x.clamp(2, 12) as usize,
        mesh_rows: settings.transform_mesh_y.clamp(2, 12) as usize,
    }
}

/// 変形を始める（`start_wire_transform`）。`mode` は free / scale / mesh。
pub fn begin(s: &mut Session, mode: &str) -> Result<Outcome, String> {
    let mode = Mode::from_name(mode);
    // 変形中にもう一度始めたら、今の変形をやめてから始め直す（Qt と同じ）。
    if s.tools.transform.is_some() {
        cancel(s)?;
    }
    let mut notes = Vec::new();
    if s.tools.selection.selection().is_none() {
        if !select_used_area(s)? {
            return Err("使用色がある領域を検出できないため、変形を開始できません。".into());
        }
        notes.push("選択範囲がなかったため、使用色がある領域を自動選択しました。".to_string());
    }
    let p = s.project.as_ref().ok_or("作品を開いていません")?;
    let doc = &p.document;
    let (frame, layer) = current_index(doc);
    let layers = pma_core::compose::frame_layers(doc, frame).map_err(|e| e.to_string())?;
    let (w, h) = doc.size();
    let image = match layers
        .get(layer)
        .and_then(|l| l.as_ref())
        .and_then(|l| l.image)
    {
        Some(image) => image.clone(),
        None => pma_core::image::RgbaImage::blank(w, h).map_err(|e| e.to_string())?,
    };
    let options = options(s);
    // 実線の太さは、使用色を選んでいれば出す（Qt の set_transform_line_colors_available）。
    let line_colors = options.line_colors.len();
    let selection = s.tools.selection.selection().cloned();
    let Some(begun) = TransformSession::begin(&image, selection.as_ref(), mode, options)
        .map_err(|e| e.to_string())?
    else {
        return Err("選択範囲から変形対象を作成できませんでした。".into());
    };
    let before = doc.clone();
    let outlines = selection.map(|sel| sel.outlines()).unwrap_or_default();
    let all_frames = s.tools.settings.selection_all_frames;
    let quality_requested = s.tools.settings.transform_quality;
    // 選択の中を消した絵を置く（履歴には積まない）。
    let rect = begun.edit.rect;
    let pixels = begun
        .layer
        .copy_rgba_rect(rect)
        .map_err(|e| e.to_string())?;
    let p = s.project.as_mut().ok_or("作品を開いていません")?;
    p.document
        .write_drawing_patch_with_content(frame, layer, rect, &pixels, true)
        .map_err(|e| e.to_string())?;
    let quality = begun.session.quality();
    s.tools.transform = Some(ActiveTransform {
        session: begun.session,
        frame,
        layer,
        before,
        all_frames,
        outlines,
        line_colors,
    });
    s.tools.selection.set_transform_active(true);
    s.touch_pixels();
    s.recompose()?;
    if quality_requested && !quality {
        notes.push("TpMaskを準備できなかったため、通常変形へ切り替えました。".into());
    }
    let target = format!(
        "{}{}",
        if quality {
            "クオリティ方式／"
        } else {
            ""
        },
        if all_frames {
            "すべてのコマへ適用します。"
        } else {
            "現在のコマへ適用します。"
        }
    );
    let line = if quality && line_colors > 0 {
        format!(" 選択中の使用色{line_colors}色を実線として処理します。")
    } else {
        String::new()
    };
    notes.push(format!(
        "白い点：変形／枠内：移動／黄色い点：回転。Enterで確定、Escでキャンセルできます。{target}{line}"
    ));
    Ok(Outcome {
        message: Some(notes.join(" ")),
    })
}

/// 始める前の文書へ戻す（表示も合わせる）。
fn restore(s: &mut Session, before: Document) -> Result<(), String> {
    if let Some(p) = s.project.as_mut() {
        p.document = before;
    }
    s.tools.selection.set_transform_active(false);
    s.touch_pixels();
    s.recompose()
}

/// キャンセル（`cancel_selection_transform`）。選択範囲は残す。変形中でなければ何もしない。
pub fn cancel(s: &mut Session) -> Result<bool, String> {
    let Some(active) = s.tools.transform.take() else {
        return Ok(false);
    };
    restore(s, active.before)?;
    Ok(true)
}

/// すべてのコマのうち、選択レイヤーに絵のあるコマの数（Qt は書いたコマの数で
/// LayerUndo と LayerBatchUndo を分ける）。
fn frames_with_content(doc: &Document, layer: usize) -> Result<usize, String> {
    let mut count = 0;
    for frame in 0..doc.frame_order().len() {
        if !scope::scope_cells(doc, frame, layer, false)
            .map_err(|e| e.to_string())?
            .is_empty()
        {
            count += 1;
        }
    }
    Ok(count)
}

/// 確定（`commit_selection_transform`）。1 回の編集として履歴に積み、選択範囲を解除する。
/// 変わった範囲（すべてのコマなら全体）を返す。変形中でなければ None。
pub fn commit(s: &mut Session) -> Result<Option<Rect>, String> {
    let Some(active) = s.tools.transform.take() else {
        return Ok(None);
    };
    let ActiveTransform {
        session,
        frame,
        layer,
        before,
        all_frames,
        ..
    } = active;
    // 始める前の文書に戻してから、1 回の編集として書く。
    restore(s, before)?;
    let doc = &s.project.as_ref().ok_or("作品を開いていません")?.document;
    let label = if all_frames && frames_with_content(doc, layer)? != 1 {
        "色編集".to_string()
    } else {
        drawing_label(doc, frame, layer)
    };
    let rect = if all_frames {
        let cells = scope::scope_cells(doc, frame, layer, true).map_err(|e| e.to_string())?;
        s.edit_with(Some(&label), |doc, _| {
            let outcome =
                scope::apply_over_cells(doc, &cells, &mut Progress::default(), |_, image| {
                    session.apply_to(image).map(|image| Some((image, 1)))
                })
                .map_err(|e| e.to_string())?;
            Ok(((), outcome.changed_cells > 0))
        })?;
        let (w, h) = doc_size(s);
        Some(Rect {
            x: 0,
            y: 0,
            width: w,
            height: h,
        })
    } else {
        let committed = session.commit().map_err(|e| e.to_string())?;
        let undo = committed.undo;
        if undo.rect.width == 0 || undo.rect.height == 0 {
            None
        } else {
            s.edit_with(Some(&label), |doc, _| {
                doc.write_drawing_patch_with_content(frame, layer, undo.rect, &undo.after, true)
                    .map_err(|e| e.to_string())?;
                Ok(((), true))
            })?;
            Some(undo.rect)
        }
    };
    s.touch_pixels();
    // 確定したら選択範囲も終える（Qt の V62）。
    s.tools.selection.clear();
    Ok(rect)
}

fn doc_size(s: &Session) -> (usize, usize) {
    s.project.as_ref().map_or((0, 0), |p| p.document.size())
}

/// 重心のまわりに回す（`rotate_selection_transform`、±90 のボタン）。
pub fn rotate(s: &mut Session, degrees: f64) -> bool {
    match s.tools.transform.as_mut() {
        Some(t) => {
            t.session.rotate(degrees);
            true
        }
        None => false,
    }
}

/// メッシュの格子数（`set_transform_mesh_grid`）。変形中のメッシュなら引き直す。
pub fn set_mesh_grid(s: &mut Session, cols: usize, rows: usize) {
    if let Some(t) = s.tools.transform.as_mut() {
        t.session.set_mesh_grid(cols, rows);
    }
}

/// 実線の太さのしきい値（`set_transform_line_threshold`）。
pub fn set_line_threshold(s: &mut Session, value: i64) {
    if let Some(t) = s.tools.transform.as_mut() {
        t.session.set_line_threshold(value.clamp(1, 254) as u8);
    }
}

/// ドラッグ（`begin_transform_drag`・`update_transform_drag`・`end_transform_drag`）。
/// `phase` は begin / move / end。begin で何もつかめなければ偽。
pub fn drag(s: &mut Session, phase: &str, x: f64, y: f64, zoom: f64) -> bool {
    let Some(t) = s.tools.transform.as_mut() else {
        return false;
    };
    match phase {
        "begin" => t.session.begin_drag((x, y), zoom).is_some(),
        "move" => {
            t.session.update_drag((x, y));
            true
        }
        _ => {
            t.session.update_drag((x, y));
            t.session.end_drag()
        }
    }
}

/// 変形の途中の形を 1 つ戻す・やり直す（取り消しのキー）。変形中でなければ None。
pub fn step_history(s: &mut Session, back: bool) -> Option<bool> {
    let t = s.tools.transform.as_mut()?;
    Some(if back {
        t.session.undo()
    } else {
        t.session.redo()
    })
}

/// 今の形で描いた画素（確定と同じ画素）。返す形は 16 バイトの矩形のあとにストレート RGBA。
pub fn preview(s: &Session) -> Result<Vec<u8>, String> {
    let mut out = vec![0u8; 16];
    let Some(t) = &s.tools.transform else {
        return Ok(out);
    };
    let rendered = t.session.preview().map_err(|e| e.to_string())?;
    let w = rendered.window;
    if w.width == 0 || w.height == 0 {
        return Ok(out);
    }
    for (i, v) in [w.x, w.y, w.width, w.height].iter().enumerate() {
        out[i * 4..i * 4 + 4].copy_from_slice(&(*v as u32).to_le_bytes());
    }
    out.extend_from_slice(&rendered.pixels.to_straight_bytes());
    Ok(out)
}

// ---------------------------------------------------------------------------
// 画面との窓口
// ---------------------------------------------------------------------------

/// ドラッグ（[`drag`]）。形が変わったら `tools-changed` で知らせる。つかめたか（begin）・
/// 形の履歴を積んだか（end）を返す。
#[tauri::command]
pub fn transform_drag(
    app: tauri::AppHandle,
    phase: String,
    x: f64,
    y: f64,
    zoom: f64,
) -> Result<bool, String> {
    crate::tools::with_tools(&app, false, |s| Ok(drag(s, &phase, x, y, zoom)))
}

/// 今の形で描いた画素（[`preview`]）。
#[tauri::command]
pub fn transform_preview(
    session: tauri::State<crate::session::Shared>,
) -> Result<tauri::ipc::Response, String> {
    let s = session.lock().unwrap();
    preview(&s).map(tauri::ipc::Response::new)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tools::{lasso_fill, select_rect};

    fn big() -> Session {
        let mut s = Session::empty(8, 8);
        s.open_document(crate::timeline::sample_document(4, (64, 48)).unwrap())
            .unwrap();
        s
    }

    fn pixel(s: &Session, x: usize, y: usize) -> [u8; 4] {
        let doc = &s.project.as_ref().unwrap().document;
        let (frame, layer) = current_index(doc);
        let layers = pma_core::compose::frame_layers(doc, frame).unwrap();
        let image = layers[layer].as_ref().unwrap().image.unwrap();
        let i = (y * image.width() + x) * 4;
        let v = &image.rgba()[i..i + 4];
        [v[0], v[1], v[2], v[3]]
    }

    fn label(s: &Session) -> String {
        let doc = &s.project.as_ref().unwrap().document;
        let (f, l) = current_index(doc);
        drawing_label(doc, f, l)
    }

    /// 四角を塗り、その四角を選ぶ。
    fn square(s: &mut Session) {
        s.tools.settings.color_main = [9, 8, 7];
        lasso_fill(s, &[[10.0, 10.0], [40.0, 10.0], [40.0, 40.0], [10.0, 40.0]]).unwrap();
        assert!(select_rect(s, [8.0, 8.0], [42.0, 42.0]));
    }

    #[test]
    fn move_commit_is_one_undo_with_drawing_label_and_clears_selection() {
        let mut s = big();
        square(&mut s);
        let undo_before = s.history().undo.len();
        let name = label(&s);
        let outcome = begin(&mut s, "free").unwrap();
        assert!(outcome.message.unwrap().contains("Enterで確定"));
        assert!(s.tools.selection.transform_active());
        // 変形のあいだは選択の中が消えている（履歴には積まない）。
        assert_eq!(pixel(&s, 25, 25)[3], 0);
        assert_eq!(s.history().undo.len(), undo_before);
        // 中をつかんで 5.4 画素動かす（1 画素単位に丸める #116）。
        assert!(drag(&mut s, "begin", 25.0, 25.0, 1.0));
        drag(&mut s, "move", 30.4, 25.0, 1.0);
        assert!(drag(&mut s, "end", 30.4, 25.0, 1.0));
        assert_eq!(s.tools.transform.as_ref().unwrap().view().history, 1);
        // 形の取り消し・やり直し（変形操作）。
        assert_eq!(step_history(&mut s, true), Some(true));
        assert_eq!(step_history(&mut s, false), Some(true));
        let preview = preview(&s).unwrap();
        assert!(preview.len() > 16);
        commit(&mut s).unwrap().unwrap();
        assert!(s.tools.transform.is_none());
        assert!(!s.tools.selection.transform_active());
        assert!(s.tools.selection.selection().is_none());
        assert_eq!(pixel(&s, 12, 25)[3], 0);
        assert_eq!(pixel(&s, 44, 25), [9, 8, 7, 255]);
        let undo = s.history().undo;
        assert_eq!(undo.len(), undo_before + 1);
        assert_eq!(undo.last().unwrap(), &name);
        s.undo().unwrap();
        assert_eq!(pixel(&s, 12, 25), [9, 8, 7, 255]);
    }

    #[test]
    fn cancel_restores_the_layer_and_keeps_the_selection() {
        let mut s = big();
        square(&mut s);
        let undo_before = s.history().undo.len();
        begin(&mut s, "scale").unwrap();
        assert!(rotate(&mut s, 90.0));
        assert!(cancel(&mut s).unwrap());
        assert_eq!(pixel(&s, 25, 25), [9, 8, 7, 255]);
        assert!(s.tools.selection.selection().is_some());
        assert_eq!(s.history().undo.len(), undo_before);
        assert!(!cancel(&mut s).unwrap());
        assert_eq!(commit(&mut s).unwrap(), None);
    }

    #[test]
    fn begin_without_selection_selects_used_area_or_refuses() {
        let mut s = big();
        // 何も描いていなければ始められない。
        let doc = &s.project.as_ref().unwrap().document;
        let empty = pma_core::selection::used_area(
            &pma_core::compose::frame_layers(doc, 0).unwrap()[0]
                .as_ref()
                .and_then(|l| l.image.cloned())
                .unwrap_or_else(|| pma_core::image::RgbaImage::blank(64, 48).unwrap()),
        )
        .is_none();
        if empty {
            assert!(begin(&mut s, "free").is_err());
        }
        square(&mut s);
        s.tools.selection.clear();
        let outcome = begin(&mut s, "mesh").unwrap();
        assert!(outcome.message.unwrap().contains("自動選択"));
        let view = s.tools.transform.as_ref().unwrap().view();
        assert_eq!(view.mode, "mesh");
        assert_eq!(view.points.len(), view.cols * view.rows);
        set_mesh_grid(&mut s, 3, 5);
        let view = s.tools.transform.as_ref().unwrap().view();
        assert_eq!((view.cols, view.rows, view.points.len()), (3, 5, 15));
        cancel(&mut s).unwrap();
    }

    #[test]
    fn all_frames_commit_uses_batch_label() {
        let mut s = big();
        square(&mut s);
        s.tools.settings.selection_all_frames = true;
        let doc = &s.project.as_ref().unwrap().document;
        let frames = frames_with_content(doc, current_index(doc).1).unwrap();
        begin(&mut s, "free").unwrap();
        rotate(&mut s, 90.0);
        commit(&mut s).unwrap();
        let last = s.history().undo.last().cloned().unwrap();
        if frames == 1 {
            assert!(last.starts_with("描画"), "{last}");
        } else {
            assert_eq!(last, "色編集");
        }
    }
}
