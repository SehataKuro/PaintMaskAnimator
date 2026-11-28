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
//! - 保持のあるキーのトゥイーン（Qt の `main_window_tween.TweenController`）：変形を
//!   始めた点から確定した点までを各コマに割り振り（pma-core の
//!   `TransformSession::tween_images`）、`Document::install_tween` で 1 回の「全体編集」
//!   として書く。確定したトゥイーンの形は、帯から直せる（`reopen_tween`）。
//! - 変形の途中でクオリティを切り替えたら、今の変形にも効かせる（`set_transform_quality`）。

use crate::session::{current_index, Session};
use crate::tools::{drawing_label, select_used_area};
use pma_core::document::model::{CellNumber, Document};
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
    /// トゥイーンの編集中（Qt の canvas.tween_pending）。
    pub tween: Option<TweenPending>,
    /// 形を変えずに描き方を変えた回数（クオリティの切り替え。画面が描き直す合図）。
    revision: u32,
}

/// トゥイーンの編集中の値（Qt の tween_pending）。
pub struct TweenPending {
    /// キーのコマとトゥイーンの長さ。
    pub key: usize,
    pub exposure: usize,
    /// 変形を始めたときの点（割合 0 の形）。
    start: Vec<(f64, f64)>,
    /// 逆生成（キー側を変形した形、最後のコマを元の形にする）。
    pub reverse: bool,
    /// 確定したトゥイーンを直しているとき。
    reedit: Option<Reedit>,
}

/// 確定したトゥイーンを直しているときの、戻す先と引き継ぐ値。
struct Reedit {
    /// 直し始める前の文書（キャンセルで戻す・確定の取り消しで戻る）。
    before: Document,
    id: Option<String>,
    numbers: Option<Vec<CellNumber>>,
}

/// 画面へ渡すトゥイーンの状態（ポップアップ）。
#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct TweenView {
    pub key: usize,
    pub layer: usize,
    pub exposure: usize,
    pub reverse: bool,
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
    /// トゥイーンの編集中なら、その値。
    pub tween: Option<TweenView>,
    /// 描き方を変えた回数（形が同じでも描き直す）。
    pub revision: u32,
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
            tween: self.tween.as_ref().map(|t| TweenView {
                key: t.key,
                layer: self.layer,
                exposure: t.exposure,
                reverse: t.reverse,
            }),
            revision: self.revision,
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
/// 「保持のあるコマの変形はトゥイーンにする」が ON で、今のコマが保持のあるキーなら
/// トゥイーンとして始める（`TweenController.start_from_transform`、#61）。
pub fn begin(s: &mut Session, mode: &str) -> Result<Outcome, String> {
    if let Some(outcome) = start_from_transform(s, mode)? {
        return Ok(outcome);
    }
    begin_plain(s, mode)
}

/// 【Group C】line_thickness.rs も使う（トゥイーンへ切り替えずに始める）。
pub(crate) fn begin_plain(s: &mut Session, mode: &str) -> Result<Outcome, String> {
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
        tween: None,
        revision: 0,
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

/// 【Group C】確定の範囲を今のコマだけにする（「線の太さを調整」は commit(all_frames=False)）。
pub(crate) fn set_current_frame_only(s: &mut Session) {
    if let Some(t) = s.tools.transform.as_mut() {
        t.all_frames = false;
    }
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
    // 直し始めたトゥイーンをやめたら、確定済みのトゥイーンをそのまま戻す。
    let before = match active.tween.and_then(|t| t.reedit) {
        Some(reedit) => reedit.before,
        None => active.before,
    };
    restore(s, before)?;
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
    if s.tools
        .transform
        .as_ref()
        .is_some_and(|t| t.tween.is_some())
    {
        commit_tween(s)?;
        let (w, h) = doc_size(s);
        return Ok(Some(Rect {
            x: 0,
            y: 0,
            width: w,
            height: h,
        }));
    }
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

/// クオリティ変形の切り替え（`set_transform_quality`）。変形中なら、今の使用色の選択で
/// 色の番号を作り直して描き直す。
pub fn set_quality(s: &mut Session, quality: bool) {
    let colors = s.tools.paint.used_selection.clone();
    if let Some(t) = s.tools.transform.as_mut() {
        t.line_colors = colors.len();
        t.session.set_quality(quality, colors);
        t.revision = t.revision.wrapping_add(1);
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
// トゥイーン（Qt の main_window_tween.TweenController）
// ---------------------------------------------------------------------------

type Pt = (f64, f64);

fn tween_mode_name(mesh: bool) -> &'static str {
    if mesh {
        "メッシュ変形"
    } else {
        "自由変形"
    }
}

/// `column` を含む保持の範囲が、絵のあるキーで 2 コマ以上なら (キーのコマ, 長さ)。
fn held_block(
    doc: &Document,
    column: usize,
    layer: usize,
) -> Result<Option<(usize, usize)>, String> {
    let Some((blank, start, length)) = doc.timeline_block_at(column, layer) else {
        return Ok(None);
    };
    if blank || length < 2 {
        return Ok(None);
    }
    let has_content = !scope::scope_cells(doc, start, layer, false)
        .map_err(|e| e.to_string())?
        .is_empty();
    Ok(has_content.then_some((start, length)))
}

fn sheet_mode(s: &Session) -> bool {
    s.timeline.mode == crate::timeline::Mode::Sheet
}

/// 変形を始めたコマが保持のあるキーなら、確認なしでトゥイーンにする
/// （`start_from_transform`）。始めなければ None（ふつうの変形を始める）。
fn start_from_transform(s: &mut Session, mode: &str) -> Result<Option<Outcome>, String> {
    if !matches!(mode, "free" | "mesh")
        || !s.tools.settings.transform_as_tween
        || s.tools.transform.is_some()
        || !sheet_mode(s)
    {
        return Ok(None);
    }
    let Some(p) = s.project.as_ref() else {
        return Ok(None);
    };
    let (frame, layer) = current_index(&p.document);
    let Some((key, exposure)) = held_block(&p.document, frame, layer)? else {
        return Ok(None);
    };
    start_tween(s, layer, key, exposure, mode, false, None, None).map(Some)
}

/// キーのコマで変形を始め、トゥイーンの編集中にする（`_start`）。`shape` は直すときの
/// 保存してあった (変形前の点, 変形後の点)。始められなければ `reedit` の文書へ戻す。
#[allow(clippy::too_many_arguments)]
fn start_tween(
    s: &mut Session,
    layer: usize,
    key: usize,
    exposure: usize,
    mode: &str,
    reverse: bool,
    shape: Option<(Vec<Pt>, Vec<Pt>)>,
    reedit: Option<Reedit>,
) -> Result<Outcome, String> {
    let mode = if mode == "mesh" { "mesh" } else { "free" };
    let name = tween_mode_name(mode == "mesh");
    s.timeline_select(layer, key, "only")?;
    s.tools.selection.clear();
    // 「すべてのコマに適用」だけはトゥイーン生成と分けて切る（クオリティ・線の色・太さは保つ）。
    s.tools.settings.selection_all_frames = false;
    s.tools.settings.tool = "rect_select".into();
    let failed = |s: &mut Session, reedit: Option<Reedit>, message: String| {
        if let Some(reedit) = reedit {
            restore(s, reedit.before)?;
        }
        Err(message)
    };
    if !select_used_area(s)? {
        return failed(
            s,
            reedit,
            "画像内に変形対象となる描画領域がありません。".into(),
        );
    }
    if let Err(message) = begin_plain(s, mode) {
        return failed(s, reedit, message);
    }
    let t = s
        .tools
        .transform
        .as_mut()
        .ok_or("変形を始められませんでした。")?;
    let start = t.session.points().to_vec();
    if let Some((saved_start, saved_final)) = shape {
        // キーの絵を描き足して基準の点が少しずれても形が崩れないよう、保存した
        // 「変形前→変形後」の差を今の点に足す。
        if saved_start.len() == saved_final.len() && saved_final.len() == start.len() {
            let points = start
                .iter()
                .zip(saved_start.iter().zip(&saved_final))
                .map(|(p, (a, b))| (p.0 + b.0 - a.0, p.1 + b.1 - a.1))
                .collect();
            t.session.set_points(points);
        }
    }
    t.tween = Some(TweenPending {
        key,
        exposure,
        start,
        reverse,
        reedit,
    });
    Ok(Outcome {
        message: Some(format!(
            "♦ {name}トゥイーン中です。変形形状を指定し、ポップアップの「逆生成」で生成方向を選べます。"
        )),
    })
}

/// タイムラインの「トゥイーンを有効にする」（`enable`）。確かめの窓は画面が先に出す。
pub fn enable(s: &mut Session, layer: usize, column: usize, mode: &str) -> Result<Outcome, String> {
    let p = s.project.as_ref().ok_or("作品を開いていません")?;
    let Some((key, exposure)) = held_block(&p.document, column, layer)? else {
        return Err("2コマ以上の表示区間を持つ画像キーフレームで実行してください。".into());
    };
    cancel(s)?;
    start_tween(s, layer, key, exposure, mode, false, None, None)
}

fn points_from(value: Option<&serde_json::Value>) -> Vec<Pt> {
    value
        .and_then(|v| v.as_array())
        .map(|points| {
            points
                .iter()
                .filter_map(|p| Some((p.get(0)?.as_f64()?, p.get(1)?.as_f64()?)))
                .collect()
        })
        .unwrap_or_default()
}

/// 確定したトゥイーンを、保存した変形の形から直し始める（`edit`）。帯の先頭でなければ None。
pub fn edit(s: &mut Session, layer: usize, column: usize) -> Result<Option<Outcome>, String> {
    let p = s.project.as_ref().ok_or("作品を開いていません")?;
    let Some(group) = p
        .document
        .tween_groups(layer)
        .into_iter()
        .find(|g| g.start == column)
    else {
        return Ok(None);
    };
    cancel(s)?;
    let p = s.project.as_mut().ok_or("作品を開いていません")?;
    let doc = &p.document;
    let position = |column: usize| {
        let track = doc.track_order().get(layer)?;
        doc.frame(*doc.frame_order().get(column)?)?
            .positions
            .get(track)
    };
    let spec = position(column)
        .and_then(|p| p.tween.clone())
        .unwrap_or_default();
    let reverse = spec
        .get("reverse")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let length = group.length;
    let numbers: Option<Vec<CellNumber>> = (column + 1..column + length)
        .map(|c| position(c).and_then(|p| p.number()))
        .collect();
    // 元の形の絵（通常生成は先頭、逆生成は末尾）をキーへ戻し、区間を 1 枚のキーにまとめる。
    let source = if reverse { column + length - 1 } else { column };
    let original = doc
        .position_cell(source, layer)
        .map(|cell| cell.image.clone())
        .ok_or("トゥイーンの絵が見つかりませんでした。")?;
    let reedit = Reedit {
        before: doc.clone(),
        id: spec.get("id").and_then(|v| v.as_str()).map(str::to_owned),
        numbers,
    };
    p.document
        .reopen_tween(column, layer, length, original)
        .map_err(|e| e.to_string())?;
    s.touch_structure();
    let mode = if spec.get("mode").and_then(|v| v.as_str()) == Some("mesh") {
        let grid = |key: &str| {
            spec.get(key)
                .and_then(|v| v.as_i64())
                .unwrap_or(4)
                .clamp(2, 12)
        };
        s.tools.settings.transform_mesh_x = grid("mesh_cols");
        s.tools.settings.transform_mesh_y = grid("mesh_rows");
        "mesh"
    } else {
        "free"
    };
    let shape = (
        points_from(spec.get("start_points")),
        points_from(spec.get("final_points")),
    );
    start_tween(
        s,
        layer,
        column,
        length,
        mode,
        reverse,
        Some(shape),
        Some(reedit),
    )
    .map(Some)
}

/// 生成の向きを切り替える（`set_reverse_generation`）。知らせを返す。
pub fn set_reverse(s: &mut Session, reverse: bool) -> Option<String> {
    let t = s.tools.transform.as_mut()?;
    let mesh = t.session.mode() == Mode::Mesh;
    t.tween.as_mut()?.reverse = reverse;
    let name = tween_mode_name(mesh);
    Some(if reverse {
        format!("◆ {name}の逆生成：キーフレーム側を変形形状、ラストコマ側を元の初期形状として生成します。")
    } else {
        format!("♦ {name}の通常生成：キーフレーム側を元の初期形状、ラストコマ側を変形形状として生成します。")
    })
}

/// トゥイーンの編集中か。
pub fn tween_pending(s: &Session) -> bool {
    s.tools
        .transform
        .as_ref()
        .is_some_and(|t| t.tween.is_some())
}

fn new_tween_id() -> String {
    use std::hash::{BuildHasher, Hasher};
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_nanos());
    (0..2u8)
        .map(|salt| {
            let mut h = std::collections::hash_map::RandomState::new().build_hasher();
            h.write_u128(now);
            h.write_u8(salt);
            format!("{:016x}", h.finish())
        })
        .collect()
}

/// トゥイーンを確定する（`commit_transform`）。キーから中割りまでを 1 回の「全体編集」
/// として書き、知らせを返す。確定できなければ変形を続けたままエラーを返す。
pub fn commit_tween(s: &mut Session) -> Result<String, String> {
    // 始めたあとにクオリティ・使用色を変えた場合も、確定するときの設定を使う。
    let quality = s.tools.settings.transform_quality;
    let colors = s.tools.paint.used_selection.clone();
    let not_ready = || "変形情報を取得できないため、トゥイーンを確定できません。".to_string();
    let Some(t) = s.tools.transform.as_mut() else {
        return Err(not_ready());
    };
    let Some(pending) = t.tween.as_ref() else {
        return Err(not_ready());
    };
    if pending.start.is_empty() || pending.start.len() != t.session.points().len() {
        return Err(not_ready());
    }
    t.line_colors = colors.len();
    let quality_active = t.session.set_quality(quality, colors);
    t.revision = t.revision.wrapping_add(1);
    if quality && !quality_active {
        return Err("TPクオリティ用の色マスクを生成できませんでした。\n\
            Pillowが利用できることと、変形対象に色があることを確認してください。"
            .into());
    }
    let images = t
        .session
        .tween_images(&pending.start, pending.exposure, pending.reverse)
        .map_err(|e| e.to_string())?
        .ok_or_else(not_ready)?;
    let Some(active) = s.tools.transform.take() else {
        return Err(not_ready());
    };
    let Some(pending) = active.tween else {
        return Err(not_ready());
    };
    let (layer, key, exposure, reverse) =
        (active.layer, pending.key, pending.exposure, pending.reverse);
    let mode = active.session.mode();
    let id = pending
        .reedit
        .as_ref()
        .and_then(|r| r.id.clone())
        .unwrap_or_else(new_tween_id);
    let spec = pma_core::tools::transform::tween_spec(
        &id,
        exposure,
        mode,
        reverse,
        &pending.start,
        active.session.points(),
        active.session.mesh_size(),
        active.session.mesh_reference(),
    );
    // 取り消しで戻る先は、トゥイーンを始める前（直していたなら直し始める前）の文書。
    let (undo_to, base, numbers) = match pending.reedit {
        Some(reedit) => (reedit.before, Some(active.before), reedit.numbers),
        None => (active.before, None, None),
    };
    restore(s, undo_to)?;
    let cursor = if reverse { key } else { key + exposure - 1 };
    s.edit_with(Some("全体編集"), move |doc, ui| {
        if let Some(base) = base {
            *doc = base;
        }
        let numbers = match numbers {
            Some(numbers) => numbers,
            None => doc
                .tween_numbers(layer, exposure - 1)
                .map_err(|e| e.to_string())?,
        };
        doc.install_tween(key, layer, &images, spec, &numbers)
            .map_err(|e| e.to_string())?;
        // 確定のあとは通常生成なら最後のコマ、逆生成ならキーへ移る（publish_tween_commit）。
        ui.set_frame(doc, cursor, layer)?;
        Ok(((), true))
    })?;
    s.touch_pixels();
    s.tools.selection.clear();
    let direction = if reverse {
        "逆生成（◆側が変形／右端が初期）"
    } else {
        "通常生成（先頭が初期／♦側が変形）"
    };
    let quality_note = if quality_active {
        "（TPクオリティ適用）"
    } else {
        ""
    };
    Ok(format!(
        "{exposure}コマの{}トゥイーンを{direction}で確定しました。{quality_note}帯をダブルクリックすると形を直せます。",
        tween_mode_name(mode == Mode::Mesh)
    ))
}

/// トゥイーンの窓口：変形を始める・終えると表示の画像ごと変わるので、画面に読み直させる。
fn with_tween<T>(
    app: &tauri::AppHandle,
    f: impl FnOnce(&mut Session) -> Result<T, String>,
) -> Result<T, String> {
    use tauri::Emitter;
    let mut view = None;
    let out = crate::with_session(app, "transform", |s| {
        let out = f(s);
        if let Err(e) = s.tools.save() {
            eprintln!("ツールの設定を保存できなかった：{e}");
        }
        view = Some(s.tools.view());
        out
    });
    if let Some(view) = view {
        let _ = app.emit("tools-changed", view);
    }
    out
}

/// タイムラインの「トゥイーンを有効にする」（[`enable`]）。知らせを返す。
#[tauri::command]
pub fn tween_enable(
    app: tauri::AppHandle,
    layer: usize,
    column: usize,
    mode: String,
) -> Result<Option<String>, String> {
    with_tween(&app, |s| enable(s, layer, column, &mode).map(|o| o.message))
}

/// 確定したトゥイーンの形を直す（[`edit`]）。知らせを返す。
#[tauri::command]
pub fn tween_edit(
    app: tauri::AppHandle,
    layer: usize,
    column: usize,
) -> Result<Option<String>, String> {
    with_tween(&app, |s| {
        edit(s, layer, column).map(|o| o.and_then(|o| o.message))
    })
}

/// 生成の向き（[`set_reverse`]）。知らせを返す。
#[tauri::command]
pub fn tween_set_reverse(app: tauri::AppHandle, reverse: bool) -> Result<Option<String>, String> {
    crate::tools::with_tools(&app, false, |s| Ok(set_reverse(s, reverse)))
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

    /// A（3コマ打ちのレイヤー）の先頭のキーに四角を塗る。
    fn held_square(s: &mut Session) -> usize {
        let layer = 2;
        s.timeline_select(layer, 0, "only").unwrap();
        s.tools.settings.color_main = [9, 8, 7];
        lasso_fill(s, &[[10.0, 10.0], [30.0, 10.0], [30.0, 30.0], [10.0, 30.0]]).unwrap();
        s.tools.selection.clear();
        layer
    }

    fn groups(s: &Session, layer: usize) -> Vec<(usize, usize, bool)> {
        let doc = &s.project.as_ref().unwrap().document;
        doc.tween_groups(layer)
            .into_iter()
            .map(|g| (g.start, g.length, g.reverse))
            .collect()
    }

    fn shift_points(s: &mut Session, dx: f64) {
        let t = s.tools.transform.as_mut().unwrap();
        let moved = t.session.points().iter().map(|p| (p.0 + dx, p.1)).collect();
        t.session.set_points(moved);
    }

    #[test]
    fn tween_commit_is_one_whole_edit_and_reedit_keeps_the_group() {
        let mut s = big();
        let layer = held_square(&mut s);
        let undo_before = s.history().undo.len();
        let outcome = enable(&mut s, layer, 0, "free").unwrap();
        assert!(outcome.message.unwrap().contains("自由変形トゥイーン中"));
        let view = s.tools.transform.as_ref().unwrap().view();
        assert_eq!(
            view.tween,
            Some(TweenView {
                key: 0,
                layer,
                exposure: 3,
                reverse: false
            })
        );
        assert!(!s.tools.settings.selection_all_frames);
        shift_points(&mut s, 6.0);
        assert!(set_reverse(&mut s, true).unwrap().contains("逆生成"));
        assert!(set_reverse(&mut s, false).unwrap().contains("通常生成"));
        let message = commit_tween(&mut s).unwrap();
        assert!(
            message.starts_with("3コマの自由変形トゥイーンを通常生成"),
            "{message}"
        );
        assert!(s.tools.transform.is_none());
        assert_eq!(groups(&s, layer), vec![(0, 3, false)]);
        let undo = s.history().undo;
        assert_eq!(undo.len(), undo_before + 1);
        assert_eq!(undo.last().unwrap(), "全体編集");
        // 通常生成は最後のコマへ移る。
        let doc = &s.project.as_ref().unwrap().document;
        assert_eq!(current_index(doc), (2, layer));
        let id = |s: &Session| {
            let doc = &s.project.as_ref().unwrap().document;
            let track = doc.track_order()[layer];
            doc.frame(doc.frame_order()[0]).unwrap().positions[&track]
                .tween
                .clone()
                .unwrap()["id"]
                .clone()
        };
        let first_id = id(&s);

        // 直し始めてキャンセルしたら、確定したトゥイーンに戻る。
        assert!(edit(&mut s, layer, 0).unwrap().is_some());
        assert!(tween_pending(&s));
        assert!(groups(&s, layer).is_empty());
        assert!(cancel(&mut s).unwrap());
        assert_eq!(groups(&s, layer), vec![(0, 3, false)]);
        assert_eq!(s.history().undo.len(), undo_before + 1);

        // 直して確定すると、同じ id のまま 1 回の編集になる。
        edit(&mut s, layer, 0).unwrap().unwrap();
        // 保存した形（6 画素右）から始まる。
        let t = s.tools.transform.as_ref().unwrap();
        let start = &t.tween.as_ref().unwrap().start;
        assert_eq!(t.session.points()[0].0, start[0].0 + 6.0);
        set_reverse(&mut s, true);
        commit_tween(&mut s).unwrap();
        assert_eq!(groups(&s, layer), vec![(0, 3, true)]);
        assert_eq!(id(&s), first_id);
        assert_eq!(s.history().undo.len(), undo_before + 2);
        s.undo().unwrap();
        assert_eq!(groups(&s, layer), vec![(0, 3, false)]);
        s.undo().unwrap();
        assert!(groups(&s, layer).is_empty());
    }

    #[test]
    fn held_key_transform_becomes_a_tween_when_the_option_is_on() {
        let mut s = big();
        let layer = held_square(&mut s);
        // オプションが OFF ならふつうの変形。
        begin(&mut s, "free").unwrap();
        assert!(!tween_pending(&s));
        cancel(&mut s).unwrap();
        s.tools.settings.transform_as_tween = true;
        // 拡大縮小はトゥイーンにしない。
        begin(&mut s, "scale").unwrap();
        assert!(!tween_pending(&s));
        cancel(&mut s).unwrap();
        let outcome = begin(&mut s, "mesh").unwrap();
        assert!(outcome
            .message
            .unwrap()
            .contains("メッシュ変形トゥイーン中"));
        assert!(tween_pending(&s));
        // 途中でクオリティを切り替えても続けられる。
        set_quality(&mut s, false);
        assert!(!s.tools.transform.as_ref().unwrap().view().quality);
        s.tools.settings.transform_quality = false;
        shift_points(&mut s, 3.0);
        let message = commit_tween(&mut s).unwrap();
        assert!(!message.contains("TPクオリティ"), "{message}");
        assert_eq!(groups(&s, layer), vec![(0, 3, false)]);
        // 絵のない保持・1コマのキーでは有効にできない。
        assert!(enable(&mut s, 1, 15, "free").is_err());
    }
}
