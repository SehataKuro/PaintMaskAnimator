//! 塗りの操作：バケツ（なぞり塗り）・串刺し塗り・色置換・ゴミ取り・自動塗り・見本との
//! 違いチェック（Qt 版の canvas_paint_tools.py・canvas_skewer_fill.py・
//! main_window_line_ops.py・main_window_used_color.py・main_window_auto_paint.py・
//! main_window_reference_check.py の画面の側）。
//!
//! 計算は pma-core（`tools::bucket`・`tools::skewer`・`tools::scope`・`auto_plan`・
//! `reference_scan`）にあり、ここは設定を読み、文書へ書き、Qt と同じ名前で履歴に積み、
//! Qt と同じ言葉で知らせるだけ。
//!
//! - 文書を変える操作は `Session::edit_with` を通す。バケツのなぞり塗りは途中を履歴に
//!   積まず、離したときに押す前の文書を1回だけ積む（Qt の `finish_bucket_drag`）。
//! - 自動塗り・チェックは計算に時間がかかるので、文書を写して別のスレッドで計算し、
//!   進み具合を `paint-job` の事件で送る（中止できる）。書き込むときに文書が変わって
//!   いれば書かない。
//! - 知らせは `paint-status` の事件で送る（画面は下の帯に出す）。

use crate::session::{current_index, pack_rect, Session, Shared};
use crate::tools::{drawing_label, Settings};
use pma_core::auto_plan::{self, AutoBucketOptions, LayerOutcome};
use pma_core::compose::PremulImage;
use pma_core::document::access::ImageField;
use pma_core::document::model::Document;
use pma_core::fill::islands::Replacement;
use pma_core::image::{Rect, RgbaImage};
use pma_core::image_ops::{dust, line_tidy};
use pma_core::reference_scan::{
    self, cell_image, layer_cells, CheckOutcome, Finding, FindingKind, Progress,
};
use pma_core::selection::SelectionMask;
use pma_core::tools::bucket::{
    self, BucketDrag, BucketSettings, PaintSettings, Reason, ReferenceSource, ReplaceRefusal,
    TargetKind,
};
use pma_core::tools::scope;
use pma_core::tools::skewer::{SkewerApplied, SkewerEvent, SkewerFill, SkewerStart, SkewerStatus};
use serde::Serialize;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::ipc::Response;
use tauri::{Emitter, Manager, State};

type Rgb = [u8; 3];
const WHITE: Rgb = [255, 255, 255];

// ---------------------------------------------------------------------------
// 状態
// ---------------------------------------------------------------------------

/// 塗りの途中の状態（`ToolState::paint` に持つ）。
#[derive(Default)]
pub struct PaintState {
    drag: Option<Drag>,
    skewer: Option<Box<SkewerFill>>,
    /// 使用色パネルで選んでいる色（ゴミ取りの「選択色を対象」・線整え・MainLineRepaint が
    /// 使う）。colors.rs の sync が使用色パネルの選択から写す。
    pub used_selection: Vec<Rgb>,
    /// 使用色パネルに登録した置換色（元, 置換色）。バケツの「置換色を塗り替え」が使う。
    /// colors.rs の sync が写す。
    pub replacements: Vec<Replacement>,
    /// 一覧に出している見つかったもの（行を選ぶとその場所へ移る）。
    findings: Vec<(usize, usize, Finding)>,
    /// 動いている計算（自動塗り・チェック）の中止の印。
    job: Option<Arc<AtomicBool>>,
}

impl PaintState {
    /// 作品を入れ替えたとき：途中の塗りと一覧を捨てる（使用色の選択は使用色パネルが送り直す）。
    pub fn reset(&mut self) {
        self.drag = None;
        self.skewer = None;
        self.findings.clear();
    }
}

struct Drag {
    drag: BucketDrag,
    /// 押す前の文書（離したときに履歴へ積む）。
    before: Document,
}

/// 時間の表示（Qt の `time_display.frame_position`）。
#[derive(Clone, Copy, Debug)]
pub struct Labels {
    pub fps: u32,
    pub seconds_frames: bool,
}

impl Default for Labels {
    fn default() -> Self {
        Self {
            fps: 24,
            seconds_frames: false,
        }
    }
}

impl Labels {
    /// `frame_position`：0 始まりのコマを「3コマ目」か「0+3」にする。
    pub fn position(&self, frame: usize) -> String {
        if self.seconds_frames {
            let rate = self.fps.max(1) as usize;
            format!("{}+{}", (frame + 1) / rate, (frame + 1) % rate)
        } else {
            format!("{}コマ目", frame + 1)
        }
    }
}

/// 1回の操作の結果：変わった範囲（今のコマ）、知らせ、実際に塗った色。
#[derive(Clone, Debug, Default)]
pub struct Feedback {
    pub rect: Option<Rect>,
    pub message: Option<String>,
    pub colors: Vec<Rgb>,
}

impl Feedback {
    fn say(message: impl Into<String>) -> Self {
        Self {
            message: Some(message.into()),
            ..Self::default()
        }
    }
}

fn err(e: impl ToString) -> String {
    e.to_string()
}

/// `{pixels:,}`：3桁ごとのカンマ。
fn thousands(n: usize) -> String {
    let digits = n.to_string();
    let mut out = String::new();
    for (i, c) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    out
}

fn hex(rgb: Rgb) -> String {
    format!("{:02X}{:02X}{:02X}", rgb[0], rgb[1], rgb[2])
}

fn union(a: Option<Rect>, b: Rect) -> Option<Rect> {
    Some(match a {
        None => b,
        Some(a) => {
            let x0 = a.x.min(b.x);
            let y0 = a.y.min(b.y);
            let x1 = (a.x + a.width).max(b.x + b.width);
            let y1 = (a.y + a.height).max(b.y + b.height);
            Rect {
                x: x0,
                y: y0,
                width: x1 - x0,
                height: y1 - y0,
            }
        }
    })
}

fn document(s: &Session) -> Result<&Document, String> {
    Ok(&s.project.as_ref().ok_or("作品を開いていません")?.document)
}

fn full_rect(s: &Session) -> Option<Rect> {
    let (width, height) = (s.display.width(), s.display.height());
    (width > 0 && height > 0).then_some(Rect {
        x: 0,
        y: 0,
        width,
        height,
    })
}

/// 今のコマ全体の範囲（いくつものセルを変えた操作の画面の書き直し）。
pub fn full_rect_of(s: &Session) -> Option<Rect> {
    full_rect(s)
}

/// 選択範囲（キャンバス全体の 0/1、Qt の `selection_mask_bool`）。選択が無ければ None。
// 【読み込み・編集：Group B】edit_ops.rs の「線整え…」も使う。
pub(crate) fn selection_mask(s: &Session) -> Option<Vec<u8>> {
    let doc = document(s).ok()?;
    let (w, h) = doc.size();
    let selection = s.tools.selection.selection()?;
    Some(selection.mask(w, h, 0, 0).bits().to_vec())
}

/// 今のコマ・レイヤーへ移る（タイムラインの選択も合わせる）。
fn move_to(s: &mut Session, frame: usize, layer: usize) -> Result<(), String> {
    let Some(p) = s.project.as_mut() else {
        return Ok(());
    };
    let count = p.document.frame_order().len();
    if count == 0 {
        return Ok(());
    }
    p.document
        .set_selection_index(frame.min(count - 1), layer)
        .map_err(err)?;
    s.timeline.select_current(&p.document);
    s.recompose()
}

/// 塗れなかった理由のうち、知らせる価値があるもの（`_bucket_failure_message`）。
pub fn failure_message(reason: Reason) -> Option<&'static str> {
    match reason {
        Reason::OutsideSelection => Some("選択範囲の外側なので塗りを開始しませんでした。"),
        Reason::OpenRegion => {
            Some("領域がキャンバス端まで開いているため、塗りを開始しませんでした。")
        }
        Reason::NotTarget => Some("クリックした場所は「対象色」で塗る対象にしていない色です。"),
        _ => None,
    }
}

/// ツールパネルの設定からバケツの設定を作る（`bucket_options` と `BucketOptions.from_tools`）。
/// Shift を押している間は含み塗りをしない（Issue #43）。
pub fn bucket_settings(s: &Settings, shift: bool, replacements: &[Replacement]) -> BucketSettings {
    let mut mask_colors: Vec<Rgb> = s
        .bucket_include_colors
        .iter()
        .zip(&s.bucket_include_colors_enabled)
        .filter_map(|(color, enabled)| color.filter(|_| *enabled))
        .collect();
    if mask_colors.is_empty() {
        mask_colors.push(s.color_sub);
    }
    let include_background_mask = mask_colors.contains(&WHITE);
    BucketSettings {
        adjacent: s.bucket_adjacent,
        island_max_width: if s.bucket_island_fill {
            s.bucket_island_fill_width.max(1) as usize
        } else {
            0
        },
        include_masks: s.bucket_include_sub && !shift,
        mask_colors,
        include_background_mask,
        close_gap: s.bucket_close_gap,
        gap_width: s.bucket_gap_width.max(1) as usize,
        extend_lines: s.bucket_extend_lines,
        extend_length: s.bucket_extend_length.max(1) as usize,
        require_closed: s.bucket_require_closed,
        target: TargetKind::from_name(&s.bucket_target_color).unwrap_or_default(),
        reference: ReferenceSource::from_name(&s.bucket_reference).unwrap_or_default(),
        island_replacements: if s.bucket_island_replace {
            replacements.to_vec()
        } else {
            Vec::new()
        },
    }
}

/// 塗る色と不透明度（`paint_source_color` と `fill_opacity`）。
pub fn paint_settings(s: &Settings) -> PaintSettings {
    PaintSettings {
        color: s.paint_rgb(),
        opacity: s.fill_opacity as f64 / 100.0,
    }
}

// ---------------------------------------------------------------------------
// バケツ（押す・なぞる・離す）
// ---------------------------------------------------------------------------

/// バケツを押した（`begin_bucket_drag`）。押した所を塗る。
pub fn bucket_begin(s: &mut Session, x: f64, y: f64, shift: bool) -> Result<Feedback, String> {
    let mut feedback = Feedback {
        message: abort_skewer(s, None),
        ..Feedback::default()
    };
    bucket_end(s)?;
    let doc = document(s)?;
    let (frame, layer) = current_index(doc);
    let before = doc.clone();
    let settings = bucket_settings(&s.tools.settings, shift, &s.tools.paint.replacements);
    let paint = paint_settings(&s.tools.settings);
    let selection = selection_mask(s);
    let mut started = None;
    s.edit_with(None, |doc, _| {
        let result = BucketDrag::begin(doc, frame, layer, settings, paint, selection, (x, y))
            .map_err(err)?;
        let changed = result
            .as_ref()
            .is_some_and(|(_, step)| !step.edits.is_empty());
        started = result;
        Ok(((), changed))
    })?;
    let Some((drag, step)) = started else {
        return Ok(feedback);
    };
    if let Some(reason) = step.failure {
        if let Some(message) = failure_message(reason) {
            feedback.message = Some(message.into());
        }
    }
    collect_edits(s, &mut feedback, step.edits);
    s.tools.paint.drag = Some(Drag { drag, before });
    Ok(feedback)
}

fn collect_edits(s: &mut Session, feedback: &mut Feedback, edits: Vec<bucket::BucketEdit>) {
    if edits.is_empty() {
        return;
    }
    for edit in edits {
        feedback.rect = union(feedback.rect, edit.rect);
        for rgb in edit.written_colors {
            if !feedback.colors.contains(&rgb) {
                feedback.colors.push(rgb);
            }
        }
    }
    s.touch_pixels();
}

/// なぞった（`continue_bucket_drag`）。通った囲まれた部分を塗る。
pub fn bucket_extend(s: &mut Session, x: f64, y: f64, shift: bool) -> Result<Feedback, String> {
    let mut feedback = Feedback::default();
    let Some(mut drag) = s.tools.paint.drag.take() else {
        return Ok(feedback);
    };
    drag.drag.set_settings(
        bucket_settings(&s.tools.settings, shift, &s.tools.paint.replacements),
        paint_settings(&s.tools.settings),
    );
    let mut edits = Vec::new();
    let result = s.edit_with(None, |doc, _| {
        let step = drag.drag.extend(doc, (x, y)).map_err(err)?;
        let changed = !step.edits.is_empty();
        edits = step.edits;
        Ok(((), changed))
    });
    s.tools.paint.drag = Some(drag);
    result?;
    collect_edits(s, &mut feedback, edits);
    Ok(feedback)
}

/// 離した（`finish_bucket_drag`）。塗った分を Undo 1回で戻せるよう、押す前の文書を積む。
pub fn bucket_end(s: &mut Session) -> Result<(), String> {
    let Some(drag) = s.tools.paint.drag.take() else {
        return Ok(());
    };
    let Some(p) = s.project.as_mut() else {
        return Ok(());
    };
    if let Some(undo) = drag.drag.finish(&p.document).map_err(err)? {
        let label = drawing_label(&drag.before, undo.frame, undo.layer);
        p.undo.record(&label, &drag.before);
        // 履歴の一覧を読み直してもらう。
        s.touch_pixels();
    }
    Ok(())
}

/// 1か所塗る（押して離す）。
pub fn bucket_click(s: &mut Session, x: f64, y: f64, shift: bool) -> Result<Feedback, String> {
    let feedback = bucket_begin(s, x, y, shift)?;
    bucket_end(s)?;
    Ok(feedback)
}

// ---------------------------------------------------------------------------
// 串刺し塗り
// ---------------------------------------------------------------------------

/// 画面へ返す串刺し塗りの状態。
#[derive(Clone, Debug, Default, Serialize)]
pub struct SkewerView {
    /// 指定を待っている（Enter・Esc・クリックを受ける）。
    pub pending: bool,
    /// 推測した範囲がある（Enter で採用できる）。
    pub has_guess: bool,
    /// キャンバスの上に出す案内（待っている間だけ）。
    pub banner: Option<String>,
    #[serde(skip)]
    pub message: Option<String>,
}

fn skewer_view(s: &Session, labels: &Labels, message: Option<String>) -> SkewerView {
    let Some(skewer) = &s.tools.paint.skewer else {
        return SkewerView {
            message,
            ..SkewerView::default()
        };
    };
    let banner = skewer
        .pending()
        .map(|cell| pending_message(skewer.has_guess(), &labels.position(cell.0)));
    SkewerView {
        pending: skewer.pending().is_some(),
        has_guess: skewer.has_guess(),
        message: message.or_else(|| banner.clone()),
        banner,
    }
}

/// `_skewer_pending_message`。
fn pending_message(guess: bool, position: &str) -> String {
    if guess {
        format!(
            "串刺し塗り：{position}は自信がありません。推測した範囲を青で表示しています\
             （Enter：この範囲で塗る／クリック：塗る場所を指定／Esc：このコマを飛ばす\
             ／Shift+Esc：この向きの残りを飛ばす）。"
        )
    } else {
        format!(
            "串刺し塗り：{position}は追いかけきれませんでした。塗る場所をクリックしてください\
             （Esc：このコマを飛ばす／Shift+Esc：この向きの残りを飛ばす）。"
        )
    }
}

const SKEWER_CHANGED: &str = "絵が変わったため、串刺し塗りを取り消しました。";
const SKEWER_NOTHING: &str = "串刺し塗りの対象になるコマがありませんでした。";

/// 途中の串刺し塗りを捨てる（`abort_skewer_fill`）。捨てたら知らせの言葉を返す。
pub fn abort_skewer(s: &mut Session, message: Option<&str>) -> Option<String> {
    s.tools.paint.skewer.take()?;
    Some(message.unwrap_or("串刺し塗りを取り消しました。").into())
}

/// 串刺し塗りのクリック（`flood_fill_over_frames`）。指定待ちなら、その指定として扱う。
pub fn skewer_click(
    s: &mut Session,
    x: f64,
    y: f64,
    shift: bool,
    labels: &Labels,
) -> Result<SkewerView, String> {
    bucket_end(s)?;
    if let Some(mut skewer) = s.tools.paint.skewer.take() {
        let doc = document(s)?;
        let (frame, _) = current_index(doc);
        let event = skewer.resume_click(doc, frame, (x, y)).map_err(err)?;
        return skewer_event(s, skewer, event, labels);
    }
    let doc = document(s)?;
    let (frame, layer) = current_index(doc);
    let settings = bucket_settings(&s.tools.settings, shift, &s.tools.paint.replacements);
    let paint = paint_settings(&s.tools.settings);
    let start = SkewerFill::start(
        doc,
        frame,
        layer,
        settings,
        paint,
        selection_mask(s),
        (x, y),
    )
    .map_err(err)?;
    let message = match start {
        SkewerStart::Ignored => None,
        SkewerStart::NoArtwork => Some("串刺し塗り：このコマには塗れる絵がありません。".into()),
        SkewerStart::Failed(reason) => {
            Some(failure_message(reason).unwrap_or(SKEWER_NOTHING).into())
        }
        SkewerStart::Started(mut skewer) => {
            let status = skewer.advance(document(s)?).map_err(err)?;
            return skewer_status(s, skewer, status, labels);
        }
    };
    Ok(skewer_view(s, labels, message))
}

fn skewer_event(
    s: &mut Session,
    skewer: Box<SkewerFill>,
    event: SkewerEvent,
    labels: &Labels,
) -> Result<SkewerView, String> {
    match event {
        SkewerEvent::Invalidated => Ok(skewer_view(s, labels, Some(SKEWER_CHANGED.into()))),
        SkewerEvent::Ignored => {
            s.tools.paint.skewer = Some(skewer);
            Ok(skewer_view(s, labels, None))
        }
        SkewerEvent::ShowPending((frame, layer)) => {
            s.tools.paint.skewer = Some(skewer);
            move_to(s, frame, layer)?;
            Ok(skewer_view(s, labels, None))
        }
        SkewerEvent::Failed(reason) => {
            s.tools.paint.skewer = Some(skewer);
            let message = failure_message(reason).map(String::from);
            Ok(skewer_view(s, labels, message))
        }
        SkewerEvent::Advanced(status) => skewer_status(s, skewer, status, labels),
    }
}

fn skewer_status(
    s: &mut Session,
    skewer: Box<SkewerFill>,
    status: SkewerStatus,
    labels: &Labels,
) -> Result<SkewerView, String> {
    match status {
        SkewerStatus::Pending((frame, layer)) => {
            s.tools.paint.skewer = Some(skewer);
            move_to(s, frame, layer)?;
            Ok(skewer_view(s, labels, None))
        }
        SkewerStatus::Ready => {
            let message = skewer_apply(s, *skewer)?;
            Ok(skewer_view(s, labels, Some(message)))
        }
    }
}

/// すべてのコマが決まったので一度に塗る（`_skewer_apply`）。Undo は1回（「串刺し塗り」）。
fn skewer_apply(s: &mut Session, skewer: SkewerFill) -> Result<String, String> {
    let origin = skewer.origin_frame();
    let mut applied = None;
    s.edit_with(Some("串刺し塗り"), |doc, _| {
        let result = skewer.apply(doc).map_err(err)?;
        let changed = matches!(&result, SkewerApplied::Done { writes, .. } if !writes.is_empty());
        applied = Some(result);
        Ok(((), changed))
    })?;
    let doc = document(s)?;
    let (_, layer) = current_index(doc);
    move_to(s, origin, layer)?;
    Ok(match applied {
        None | Some(SkewerApplied::Invalidated) => SKEWER_CHANGED.into(),
        Some(SkewerApplied::Done {
            changed_cells,
            skipped,
            ..
        }) => {
            if changed_cells == 0 {
                SKEWER_NOTHING.into()
            } else if skipped > 0 {
                s.touch_pixels();
                format!("串刺し塗り：{changed_cells} コマを塗り、{skipped} コマは対象外でした。")
            } else {
                s.touch_pixels();
                format!("串刺し塗り：{changed_cells} コマを塗りました。")
            }
        }
    })
}

/// 指定待ちのキー：`accept`（Enter）・`skip`（Esc）・`skip_direction`（Shift+Esc）・
/// `abort`（ほかのツールに替えたなど）。
pub fn skewer_key(s: &mut Session, kind: &str, labels: &Labels) -> Result<SkewerView, String> {
    if kind == "abort" {
        let message = abort_skewer(s, None);
        return Ok(skewer_view(s, labels, message));
    }
    let Some(mut skewer) = s.tools.paint.skewer.take() else {
        return Ok(skewer_view(s, labels, None));
    };
    let doc = document(s)?;
    let event = match kind {
        "accept" => skewer.accept_guess(doc),
        "skip_direction" => skewer.skip_direction(doc),
        _ => skewer.skip_frame(doc),
    }
    .map_err(err)?;
    skewer_event(s, skewer, event, labels)
}

/// 指定待ちのコマに重ねる案内：先頭 16 バイトに範囲（x, y, 幅, 高さ。u32 LE）、4 バイトの
/// 色（RGBA）、続けて範囲の中の 0/1。待っていなければ 0 の範囲だけ。
pub fn skewer_guide(s: &Session) -> Vec<u8> {
    let mut out = vec![0u8; 20];
    let Some(guide) = s.tools.paint.skewer.as_ref().and_then(|k| k.guide()) else {
        return out;
    };
    let Ok(doc) = document(s) else { return out };
    let (width, height) = doc.size();
    let Some((x0, y0, x1, y1)) = bucket::mask_bounds(guide.mask, width, height) else {
        return out;
    };
    out.clear();
    for v in [x0, y0, x1 - x0, y1 - y0] {
        out.extend_from_slice(&(v as u32).to_le_bytes());
    }
    out.extend_from_slice(&guide.rgba);
    for y in y0..y1 {
        out.extend_from_slice(&guide.mask[y * width + x0..y * width + x1]);
    }
    out
}

// ---------------------------------------------------------------------------
// 色置換
// ---------------------------------------------------------------------------

/// 色置換のクリックの結果。`confirm` があれば、線の色なので置き換えてよいか尋ねる。
#[derive(Clone, Debug, Default, Serialize)]
pub struct ReplaceView {
    pub confirm: Option<String>,
}

/// バケツの色置換（`replace_color_at`）：クリックした色を、選択レイヤーの全コマで今の色へ
/// 置き換える。線の色なら `confirmed` が偽のあいだは尋ねる。
pub fn replace_color(
    s: &mut Session,
    x: f64,
    y: f64,
    confirmed: bool,
) -> Result<(ReplaceView, Feedback), String> {
    let aborted = abort_skewer(s, None);
    bucket_end(s)?;
    let doc = document(s)?;
    let (frame, layer) = current_index(doc);
    let target = s.tools.settings.paint_rgb();
    let request = bucket::replace_color_request(doc, frame, layer, target, (x, y)).map_err(err)?;
    let say = |m: &str| Ok((ReplaceView::default(), Feedback::say(m)));
    match request {
        Err(ReplaceRefusal::OutOfBounds) => Ok((
            ReplaceView::default(),
            Feedback {
                message: aborted,
                ..Feedback::default()
            },
        )),
        Err(ReplaceRefusal::NoArtwork) => say("色置換：このコマには絵がありません。"),
        Err(ReplaceRefusal::Transparent) => {
            say("色置換：透明の所は置き換えません。置き換えたい色をクリックしてください。")
        }
        Err(ReplaceRefusal::SameColor) => say("色置換：クリックした色は今の色と同じです。"),
        Ok(request) if request.is_line && !confirmed => Ok((
            ReplaceView {
                confirm: Some(hex(request.source)),
            },
            Feedback::default(),
        )),
        Ok(request) => Ok((
            ReplaceView::default(),
            apply_palette_replacements(s, &[(request.source, request.target)])?,
        )),
    }
}

/// 使用色の色置換（`apply_palette_replacements` の "replace"）：選択レイヤーの全コマで、
/// 選択範囲があればその中だけ置き換える。Undo は1回（「色置換」）。
// 【色のパネル】使用色パネルの「全コマを置き換え」と同じ経路（colors.rs の `palette_remap`：
// 計算は pma-core の `scope::replace_colors`、履歴には使用色パネルの状態も添える）。
pub fn apply_palette_replacements(
    s: &mut Session,
    mapping: &[(Rgb, Rgb)],
) -> Result<Feedback, String> {
    let mapping = mapping
        .iter()
        .map(|&([a, b, c], [d, e, f])| ((a, b, c), (d, e, f)))
        .collect();
    let out = s.palette_remap(mapping, crate::colors::Remap::Replace)?;
    Ok(Feedback {
        rect: if out.changed { full_rect(s) } else { None },
        message: out.message,
        colors: Vec::new(),
    })
}

// ---------------------------------------------------------------------------
// ゴミ取り・塗り抜け・線周りのノイズ・線整え
// ---------------------------------------------------------------------------

/// ゴミ取りツールの処理の名前（ツールパネルの「処理モード」の言葉）。
pub fn dust_label(mode: &str) -> &'static str {
    match mode {
        "fill_holes" => "塗り抜け",
        "line_noise" => "線周りのノイズ",
        "line_tidy" => "線整え",
        _ => "ゴミ取り",
    }
}

// 【読み込み・編集：Group B】edit_ops.rs の「線整え…」も使う。
pub(crate) fn used_selection(s: &Session) -> Vec<Rgb> {
    let mut colors: Vec<Rgb> = s
        .tools
        .paint
        .used_selection
        .iter()
        .copied()
        .filter(|rgb| *rgb != WHITE)
        .collect();
    colors.sort_unstable();
    colors.dedup();
    colors
}

/// 選択レイヤーでゴミ取り／塗り抜けを行う（`remove_dust_fill_surrounding`。メニューの
/// 「ゴミ取り／塗り抜け…」とツールの「適用」）。線整えは [`tidy_with_tool`]。
pub fn dust_apply(s: &mut Session, region: Option<Vec<u8>>) -> Result<Feedback, String> {
    let settings = s.tools.settings.clone();
    if settings.dust_mode == "line_tidy" {
        return tidy_with_tool(s, region);
    }
    let mode = match settings.dust_mode.as_str() {
        "fill_holes" => dust::DustMode::FillHoles,
        "line_noise" => dust::DustMode::LineNoise,
        _ => dust::DustMode::Despeckle,
    };
    let label = dust_label(&settings.dust_mode);
    let max_area = settings.dust_size.clamp(1, 100) as usize;
    let selected_only = settings.dust_selected_only;
    let selected = used_selection(s);
    if selected_only && selected.is_empty() {
        return Ok(Feedback::say("使用色パネルで対象色を選択してください。"));
    }
    let doc = document(s)?;
    let (frame, layer) = current_index(doc);
    let cells = scope::scope_cells(doc, frame, layer, settings.dust_all_frames).map_err(err)?;
    if cells.is_empty() {
        return Ok(Feedback::say(
            "選択レイヤーに処理できるキーフレームがありません。",
        ));
    }
    let options = dust::DustOptions {
        mode,
        max_area,
        selected: selected_only.then_some(selected.as_slice()),
    };
    let mut outcome = scope::ScopeOutcome::default();
    s.edit_with(Some(label), |doc, _| {
        outcome =
            scope::remove_dust(doc, &cells, &options, &mut Progress::default()).map_err(err)?;
        Ok(((), outcome.changed_pixels > 0))
    })?;
    if outcome.changed_pixels == 0 {
        let text = if selected_only { "選択色の" } else { "" };
        return Ok(Feedback::say(format!(
            "指定サイズ以内の{text}{label}対象は見つかりませんでした。"
        )));
    }
    s.touch_pixels();
    Ok(Feedback {
        rect: full_rect(s),
        message: Some(format!(
            "選択レイヤーの{}コマで{}ピクセルへ{label}を適用しました。",
            outcome.changed_cells,
            thousands(outcome.changed_pixels)
        )),
        colors: Vec::new(),
    })
}

/// ゴミ取りツールの線整え（`tidy_lines_with_tool`）。`region` が無ければ選択範囲の中、
/// それも無ければ全体。Undo は1回（「線整え」）。
pub fn tidy_with_tool(s: &mut Session, region: Option<Vec<u8>>) -> Result<Feedback, String> {
    let settings = s.tools.settings.clone();
    let selected = used_selection(s);
    let doc = document(s)?;
    let (frame, layer) = current_index(doc);
    let colours = if settings.dust_selected_only {
        selected
    } else {
        let image = bucket::position_image(doc, frame, layer).map_err(err)?;
        line_tidy::tidy_line_colours(&image, &selected)
    };
    if colours.is_empty() {
        return Ok(Feedback::say(
            "線整え：整える線の色がありません。使用色パネルで線の色を選んでください。",
        ));
    }
    let region = region.or_else(|| selection_mask(s));
    let diagonal = match settings.line_tidy_diagonal.as_deref() {
        Some("add") => line_tidy::Diagonal::Add,
        Some("remove") => line_tidy::Diagonal::Remove,
        _ => line_tidy::Diagonal::Keep,
    };
    let cells = scope::scope_cells(doc, frame, layer, settings.dust_all_frames).map_err(err)?;
    let mut outcome = scope::TidyOutcome::default();
    s.edit_with(Some("線整え"), |doc, _| {
        outcome = scope::tidy_lines(
            doc,
            &cells,
            &colours,
            diagonal,
            region.as_deref(),
            &mut Progress::default(),
        )
        .map_err(err)?;
        Ok(((), outcome.scope.changed_cells > 0))
    })?;
    if outcome.scope.changed_cells == 0 {
        return Ok(Feedback::say("線整え：直す所はありませんでした。"));
    }
    s.touch_pixels();
    Ok(Feedback {
        rect: full_rect(s),
        message: Some(format!(
            "線整え：{}セルで{}px足し、{}px消しました。",
            outcome.scope.changed_cells, outcome.added, outcome.removed
        )),
        colors: Vec::new(),
    })
}

// ---------------------------------------------------------------------------
// 自動塗り・見本との違いチェック（別のスレッドで計算する）
// ---------------------------------------------------------------------------

/// 自動塗りの設定画面に出すセル1つ。
#[derive(Clone, Debug, Serialize)]
pub struct AutoCell {
    pub frame: usize,
    pub layer: usize,
    /// 「A1 · 3コマ目」（一覧の言葉。検索にも使う）。
    pub label: String,
    /// カードの名前「A1」。
    pub title: String,
    /// カードの時間「3コマ目」。
    pub position: String,
}

/// 自動塗りの設定画面の中身（`AutoPaintController.open`）。
#[derive(Clone, Debug, Serialize)]
pub struct AutoDialog {
    pub layer_name: String,
    pub cells: Vec<AutoCell>,
    /// 最初に見本にするセル（今のコマ）。
    pub current: usize,
}

/// 設定画面を開く前の確かめ。塗れるセルが2つ未満なら知らせの言葉を返す。
pub fn auto_paint_dialog(
    s: &mut Session,
    labels: &Labels,
) -> Result<Result<AutoDialog, String>, String> {
    let _ = abort_skewer(s, None);
    let doc = document(s)?;
    let (frame, layer) = current_index(doc);
    let cells = layer_cells(doc, layer).map_err(err)?;
    if cells.len() < 2 {
        return Ok(Err("塗れるセルが2つ以上ありません。".into()));
    }
    let key = reference_scan::key_frame(doc, frame, layer).map_err(err)?;
    let current = key
        .and_then(|key| cells.iter().position(|&c| c == (key, layer)))
        .unwrap_or(0);
    let name = doc
        .track_order()
        .get(layer)
        .and_then(|t| doc.track(*t))
        .map(|t| t.settings.name.clone())
        .unwrap_or_default();
    let list = cells
        .iter()
        .enumerate()
        .map(|(index, &(frame, layer))| {
            let number = doc
                .frame_order()
                .get(frame)
                .and_then(|f| doc.frame(*f))
                .and_then(|f| f.positions.get(&doc.track_order()[layer]))
                .and_then(|p| p.number())
                .map(|n| n.get());
            let cell = match number {
                Some(n) => format!("{name}{n}"),
                None => name.clone(),
            };
            let position = labels.position(frame);
            AutoCell {
                frame,
                layer,
                label: format!("{cell} · {position}"),
                title: match number {
                    Some(n) => format!("{name}{n}"),
                    None => format!("{name}{}", index + 1),
                },
                position,
            }
        })
        .collect();
    Ok(Ok(AutoDialog {
        layer_name: name,
        cells: list,
        current,
    }))
}

/// 一覧の1行（画面へ渡す）。
#[derive(Clone, Debug, Serialize)]
pub struct FindingRow {
    pub frame: usize,
    pub position: String,
    pub text: String,
    pub color: String,
    pub rgb: Option<Rgb>,
    pub pixels: String,
}

/// 一覧の窓（`ReferenceCheckDialog`）。
#[derive(Clone, Debug, Serialize)]
pub struct FindingList {
    pub title: String,
    pub intro: String,
    pub reference_only: Option<String>,
    pub rows: Vec<FindingRow>,
}

fn color_label(rgb: Option<Rgb>) -> String {
    match rgb {
        None => "透明".into(),
        Some(rgb) => format!("#{}", hex(rgb)),
    }
}

/// `ReferenceCheckDialog._describe`。
fn describe(finding: &Finding) -> String {
    match finding.kind {
        FindingKind::Missing => "見本の色がありません".into(),
        FindingKind::Extra => "見本に無い色があります".into(),
        FindingKind::Unpainted => "塗り残し（決めきれず塗っていません）".into(),
        FindingKind::NewPart => "新しく出てきた部分（手本に無い形なので塗っていません）".into(),
        FindingKind::UndecidedLine => {
            "色を決められなかった色トレス線（線のまま残しています）".into()
        }
        FindingKind::Suspect => format!(
            "塗り間違いかも：前後のセルでは {}",
            color_label(finding.expected)
        ),
        FindingKind::Mismatch => format!("前後のコマでは {}", color_label(finding.expected)),
    }
}

fn finding_list(
    entries: &[(usize, usize, Finding)],
    labels: &Labels,
    title: &str,
    intro: String,
    reference_only: &[(Rgb, usize, usize)],
) -> FindingList {
    let reference_only = (!reference_only.is_empty()).then(|| {
        let colors: Vec<String> = reference_only
            .iter()
            .map(|(rgb, missing, compared)| {
                format!(
                    "{}（{missing}/{compared}セルに無し）",
                    color_label(Some(*rgb))
                )
            })
            .collect();
        format!("見本だけにある色：{}", colors.join("、"))
    });
    FindingList {
        title: title.into(),
        intro,
        reference_only,
        rows: entries
            .iter()
            .map(|(frame, _, finding)| FindingRow {
                frame: *frame,
                position: labels.position(*frame),
                text: describe(finding),
                color: color_label(finding.rgb),
                rgb: finding.rgb,
                pixels: thousands(finding.pixels),
            })
            .collect(),
    }
}

/// 一覧の行を選んだ（`show_entry`）：そのコマへ移り、見つかった場所を選択範囲にする。
pub fn show_finding(s: &mut Session, row: usize) -> Result<(), String> {
    let Some((frame, layer, finding)) = s.tools.paint.findings.get(row).cloned() else {
        return Ok(());
    };
    move_to(s, frame, layer)?;
    let doc = document(s)?;
    let (width, height) = doc.size();
    let mask = finding.bounds.map(|(x0, y0, x1, y1)| {
        let mut bits = vec![0u8; width * height];
        let lw = x1 - x0;
        match &finding.mask {
            Some(local) => {
                for (i, &v) in local.iter().enumerate() {
                    if v != 0 {
                        bits[(y0 + i / lw) * width + x0 + i % lw] = 1;
                    }
                }
            }
            None => {
                // 色そのものの画素を選ぶ（見本に無い色）。
                if let Some(image) = cell_image(doc, frame, layer, ImageField::Image) {
                    let wanted = finding.rgb.map_or(0, reference_scan::pack);
                    let rgba = image.rgba();
                    for y in y0..y1.min(image.height()) {
                        for x in x0..x1.min(image.width()) {
                            let i = (y * image.width() + x) * 4;
                            let value = reference_scan::pixel_values(&rgba[i..i + 4])[0];
                            if value == wanted && y < height && x < width {
                                bits[y * width + x] = 1;
                            }
                        }
                    }
                }
            }
        }
        bits
    });
    match mask.and_then(|bits| SelectionMask::from_bits(width, height, &bits).ok()) {
        Some(mask) => s.tools.selection.set_mask(mask),
        None => {
            s.tools.selection.clear();
        }
    }
    Ok(())
}

/// 計算の進み具合と結果（`paint-job` の事件）。
#[derive(Clone, Debug, Serialize)]
pub struct JobEvent {
    /// auto_paint / reference_check
    pub job: &'static str,
    /// progress / done
    pub phase: &'static str,
    pub value: usize,
    pub total: usize,
    pub label: String,
    pub message: Option<String>,
    pub list: Option<FindingList>,
}

impl JobEvent {
    fn done(job: &'static str, message: Option<String>, list: Option<FindingList>) -> Self {
        Self {
            job,
            phase: "done",
            value: 0,
            total: 0,
            label: String::new(),
            message,
            list,
        }
    }
}

const AUTO_INTRO: &str = "塗り残し（場所や色を決めきれなかった部分）、新しく出てきた部分（開いた口\
など、手本に無い形）、色を決められなかった色トレス線と、前後のセルと違う色で塗ってあった部分\
（塗り替えていません）です。行を選ぶと、そのコマへ移って場所を選択します。";

/// 自動塗りの設定（`bucket_options(respect_modifiers=False)` の効く所）。
pub fn auto_options(s: &Settings) -> AutoBucketOptions {
    let b = bucket_settings(s, false, &[]);
    AutoBucketOptions {
        island_max_width: b.island_max_width,
        close_gap: b.close_gap,
        gap_width: b.gap_width,
        extend_lines: b.extend_lines,
        extend_length: b.extend_length,
        require_closed: b.require_closed,
    }
}

/// `nothing_painted_reason`（#65）：何も塗らなかったときに考えられる理由。
fn nothing_painted_reason(doc: &Document, layer: usize, references: &[usize]) -> String {
    let Ok(cells) = layer_cells(doc, layer) else {
        return String::new();
    };
    let (width, height) = doc.size();
    let mut targets = Vec::new();
    let mut any_initial = false;
    for (index, &(frame, layer)) in cells.iter().enumerate() {
        let initial = cell_image(doc, frame, layer, ImageField::Initial);
        any_initial |= initial.is_some();
        if references.contains(&index) {
            continue;
        }
        let pixels = match initial {
            Some(image) => auto_plan::line_only(image.rgba(), width, height),
            None => cell_image(doc, frame, layer, ImageField::Image)
                .map_or_else(|| vec![0; width * height * 4], |i| i.rgba().to_vec()),
        };
        targets.push(auto_plan::white_as_unpainted(&pixels));
    }
    if targets.is_empty() {
        return String::new();
    }
    let least = targets
        .iter()
        .map(|t| {
            auto_plan::boundary_mask(t)
                .iter()
                .filter(|v| **v != 0)
                .count()
        })
        .min()
        .unwrap_or(0);
    if least < 16.max(width * height / 5000) {
        return format!(
            "線として認識できる画素がほとんどありません。主線は RGB の各値が {} 以下の\
             濃い色、色トレス線は赤・緑・青などの原色のときだけ境界になります。",
            bucket::LINE_MAX
        );
    }
    if !targets.iter().any(|t| t.chunks_exact(4).any(|p| p[3] == 0)) {
        let mut reason = String::from("塗るセルに、まだ塗っていない（透明な）所がありません。");
        if !any_initial {
            reason.push_str(
                "色塗り前の初期状態を保持していれば、塗りかけのセルも塗り直せます\
                 （メニューの「今の絵を初期状態として保持」）。",
            );
        }
        return reason;
    }
    String::new()
}

fn progress_label(done: usize, total: usize, count: usize, started: std::time::Instant) -> String {
    let total = total.max(1);
    let value = done.min(total);
    let percent = value * 100 / total;
    let label = if done >= total {
        "自動塗りの計算が終わりました".to_string()
    } else {
        format!(
            "{}回目：対象{count}セルを塗っています",
            done / count.max(1) + 1
        )
    };
    let timing = if value > 0 {
        let elapsed = started.elapsed().as_secs_f64();
        let remaining = (elapsed / value as f64 * (total - value) as f64).ceil() as u64;
        format!(
            "全体 {percent}％・残り約{}分{:02}秒",
            remaining / 60,
            remaining % 60
        )
    } else {
        "全体 0％・残り時間を計算中".into()
    };
    format!("{label}\n{timing}")
}

/// 自動塗りを始める（`AutoPaintController.run`）。`references` は設定画面で選んだ見本
/// （セルの並びの位置）。計算は別のスレッドで行い、終わったら Undo 1回（「自動で塗る」）で
/// 書く。
pub fn auto_paint_start(app: &tauri::AppHandle, references: Vec<usize>) -> Result<(), String> {
    let state = app.state::<Shared>();
    let (doc, layer, options, revision, cancel, labels) = {
        let mut s = state.lock().unwrap();
        if s.tools.paint.job.is_some() {
            return Err("ほかの計算が動いています".into());
        }
        let doc = document(&s)?.clone();
        let (_, layer) = current_index(&doc);
        let cancel = Arc::new(AtomicBool::new(false));
        s.tools.paint.job = Some(cancel.clone());
        (
            doc,
            layer,
            auto_options(&s.tools.settings),
            s.content_revision(),
            cancel,
            labels(app, &s),
        )
    };
    let app = app.clone();
    std::thread::spawn(move || {
        let started = std::time::Instant::now();
        let count = layer_cells(&doc, layer)
            .map(|c| c.len().saturating_sub(references.len()))
            .unwrap_or(0);
        let emitter = app.clone();
        let mut report = |done: usize, total: usize| {
            let _ = emitter.emit(
                "paint-job",
                JobEvent {
                    job: "auto_paint",
                    phase: "progress",
                    value: done,
                    total: total.max(1),
                    label: progress_label(done, total, count, started),
                    message: None,
                    list: None,
                },
            );
            true
        };
        let outcome = auto_plan::paint_layer(
            &doc,
            layer,
            &references,
            &options,
            &mut Progress {
                report: Some(&mut report),
                cancel: Some(&cancel),
            },
        );
        let event = finish_auto_paint(&app, &doc, layer, &references, revision, outcome, &labels);
        let state = app.state::<Shared>();
        state.lock().unwrap().tools.paint.job = None;
        let _ = app.emit("paint-job", event);
    });
    Ok(())
}

fn finish_auto_paint(
    app: &tauri::AppHandle,
    doc: &Document,
    layer: usize,
    references: &[usize],
    revision: (u64, u64),
    outcome: Result<LayerOutcome, pma_core::Error>,
    labels: &Labels,
) -> JobEvent {
    let done = |m: String| JobEvent::done("auto_paint", Some(m), None);
    let (writes, notes, rounds, used) = match outcome {
        Err(e) => return done(format!("自動で塗れませんでした：{e}")),
        Ok(LayerOutcome::TooFewCells) => return done("塗れるセルが2つ以上ありません。".into()),
        Ok(LayerOutcome::Cancelled) => return done("自動で塗るのを中止しました。".into()),
        Ok(LayerOutcome::Done {
            writes,
            notes,
            references,
            rounds,
            ..
        }) => (writes, notes, rounds, references),
    };
    if writes.is_empty() {
        let mut message = String::from("自動で塗る：塗るところはありませんでした。");
        message.push_str(&nothing_painted_reason(doc, layer, references));
        return done(message);
    }
    let changed_cells = writes.len();
    let result = crate::with_session(app, "paint", |s| {
        if s.content_revision() != revision {
            return Err("計算の間に絵が変わったので、自動で塗るのを取りやめました。".to_string());
        }
        s.edit_with(Some("自動で塗る"), |doc, _| {
            auto_plan::apply_writes(doc, &writes).map_err(err)?;
            Ok(((), true))
        })?;
        s.touch_pixels();
        s.tools.paint.findings = notes.clone();
        Ok(())
    });
    if let Err(e) = result {
        return done(e);
    }
    let mut message = format!("自動で塗る：{changed_cells}セルを塗りました。");
    if rounds > 1 {
        message.push_str(&format!(
            "見本を{}セルに増やして{}回塗り直しました。",
            used.len(),
            rounds - 1
        ));
    }
    let list = (!notes.is_empty()).then(|| {
        message.push_str(&format!("確かめたい所が{}か所あります。", notes.len()));
        finding_list(
            &notes,
            labels,
            "自動で塗った、確かめたい所",
            AUTO_INTRO.into(),
            &[],
        )
    });
    JobEvent::done("auto_paint", Some(message), list)
}

/// 見本との違いチェック（`ReferenceCheckController.run`）。別のスレッドで調べ、一覧を送る。
pub fn reference_check_start(app: &tauri::AppHandle) -> Result<(), String> {
    let state = app.state::<Shared>();
    let (doc, frame, layer, cancel, labels) = {
        let mut s = state.lock().unwrap();
        if s.tools.paint.job.is_some() {
            return Err("ほかの計算が動いています".into());
        }
        let doc = document(&s)?.clone();
        let (frame, layer) = current_index(&doc);
        let cancel = Arc::new(AtomicBool::new(false));
        s.tools.paint.job = Some(cancel.clone());
        (doc, frame, layer, cancel, labels(app, &s))
    };
    let app = app.clone();
    std::thread::spawn(move || {
        let emitter = app.clone();
        let mut report = |done: usize, total: usize| {
            let cells = total / 2;
            let label = if done < cells {
                "色を数えています"
            } else {
                "前後のコマと比べています"
            };
            let _ = emitter.emit(
                "paint-job",
                JobEvent {
                    job: "reference_check",
                    phase: "progress",
                    value: done,
                    total: total.max(1),
                    label: label.into(),
                    message: None,
                    list: None,
                },
            );
            true
        };
        let outcome = reference_scan::check_layer(
            &doc,
            frame,
            layer,
            &mut Progress {
                report: Some(&mut report),
                cancel: Some(&cancel),
            },
        );
        let done = |m: &str| JobEvent::done("reference_check", Some(m.into()), None);
        let event = match outcome {
            Err(e) => done(&format!("チェックできませんでした：{e}")),
            Ok(CheckOutcome::NoReference) => {
                done("見本にするセル（塗ってあるコマ）を表示してから実行してください。")
            }
            Ok(CheckOutcome::TooFewCells) => done("比べるセルがありません。"),
            Ok(CheckOutcome::Cancelled) => done("チェックを中止しました。"),
            Ok(CheckOutcome::Done {
                reference_frame,
                report,
            }) => {
                if report.entries.is_empty() && report.reference_only.is_empty() {
                    done("見本との違い・塗り間違いは見つかりませんでした。")
                } else {
                    let entries: Vec<(usize, usize, Finding)> = report
                        .entries
                        .iter()
                        .map(|e| (e.frame, e.layer, e.finding.clone()))
                        .collect();
                    let intro = format!(
                        "見本：{}。行を選ぶと、そのコマへ移って場所を選択します。",
                        labels.position(reference_frame)
                    );
                    let list = finding_list(
                        &entries,
                        &labels,
                        "見本との違いチェック",
                        intro,
                        &report.reference_only,
                    );
                    let count = entries.len();
                    app.state::<Shared>().lock().unwrap().tools.paint.findings = entries;
                    JobEvent::done(
                        "reference_check",
                        Some(format!("{count}件の候補が見つかりました。")),
                        Some(list),
                    )
                }
            }
        };
        app.state::<Shared>().lock().unwrap().tools.paint.job = None;
        let _ = app.emit("paint-job", event);
    });
    Ok(())
}

// ---------------------------------------------------------------------------
// 画面との窓口（Tauri のコマンド）
// ---------------------------------------------------------------------------

/// 時間の表示（環境設定の「時間の表示」と、タイムラインの fps）。
fn labels(app: &tauri::AppHandle, s: &Session) -> Labels {
    let seconds_frames = app
        .try_state::<crate::shell::ShellShared>()
        .and_then(|shell| {
            shell
                .lock()
                .ok()?
                .prefs
                .get(crate::prefs::TIME_DISPLAY)
                .and_then(|v| v.as_str().map(|v| v == "seconds_frames"))
        })
        .unwrap_or(false);
    Labels {
        fps: s.timeline.fps,
        seconds_frames,
    }
}

fn status(app: &tauri::AppHandle, message: Option<&str>) {
    if let Some(message) = message {
        let _ = app.emit("paint-status", message);
    }
}

/// 塗った色を使用色パネルへ知らせる（`_emit_actual_paint_colors`）。
fn colors_used(app: &tauri::AppHandle, colors: &[Rgb]) {
    if !colors.is_empty() {
        let _ = app.emit("paint-colors-used", colors.to_vec());
    }
}

/// 今のコマの変わった範囲を返す操作。`source` が canvas なら画面は範囲の画素だけ書く。
fn with_feedback(
    app: &tauri::AppHandle,
    source: &'static str,
    f: impl FnOnce(&mut Session) -> Result<Feedback, String>,
) -> Result<Response, String> {
    let (feedback, packed, view) = crate::with_session(app, source, |s| {
        let feedback = f(s)?;
        let packed = pack_rect(&s.display, feedback.rect);
        Ok::<_, String>((feedback, packed, s.tools.view()))
    })?;
    let _ = app.emit("tools-changed", view);
    status(app, feedback.message.as_deref());
    colors_used(app, &feedback.colors);
    Ok(Response::new(packed))
}

#[tauri::command]
pub fn paint_bucket_begin(
    app: tauri::AppHandle,
    x: f64,
    y: f64,
    shift: bool,
) -> Result<Response, String> {
    with_feedback(&app, "canvas", |s| bucket_begin(s, x, y, shift))
}

#[tauri::command]
pub fn paint_bucket_extend(
    app: tauri::AppHandle,
    x: f64,
    y: f64,
    shift: bool,
) -> Result<Response, String> {
    with_feedback(&app, "canvas", |s| bucket_extend(s, x, y, shift))
}

#[tauri::command]
pub fn paint_bucket_end(app: tauri::AppHandle) -> Result<(), String> {
    crate::with_session(&app, "canvas", bucket_end)
}

fn skewer_reply(app: &tauri::AppHandle, view: SkewerView) -> SkewerView {
    status(app, view.message.as_deref());
    view
}

#[tauri::command]
pub fn paint_skewer_click(
    app: tauri::AppHandle,
    x: f64,
    y: f64,
    shift: bool,
) -> Result<SkewerView, String> {
    let view = crate::with_session(&app, "paint", |s| {
        let labels = labels(&app, s);
        skewer_click(s, x, y, shift, &labels)
    })?;
    Ok(skewer_reply(&app, view))
}

#[tauri::command]
pub fn paint_skewer_key(app: tauri::AppHandle, kind: String) -> Result<SkewerView, String> {
    let view = crate::with_session(&app, "paint", |s| {
        let labels = labels(&app, s);
        skewer_key(s, &kind, &labels)
    })?;
    Ok(skewer_reply(&app, view))
}

#[tauri::command]
pub fn paint_skewer_guide(session: State<Shared>) -> Response {
    Response::new(skewer_guide(&session.lock().unwrap()))
}

#[tauri::command]
pub fn paint_replace_color(
    app: tauri::AppHandle,
    x: f64,
    y: f64,
    confirmed: bool,
) -> Result<ReplaceView, String> {
    let (view, feedback) =
        crate::with_session(&app, "paint", |s| replace_color(s, x, y, confirmed))?;
    status(&app, feedback.message.as_deref());
    Ok(view)
}

/// ゴミ取り・塗り抜け・線周りのノイズ・線整え。`points` は線整えでなぞった点（画像の
/// 座標）。なぞった範囲が空なら何もしない（Qt の `_finish_line_tidy_stroke`）。
#[tauri::command]
pub fn paint_dust_apply(
    app: tauri::AppHandle,
    points: Option<Vec<[f64; 2]>>,
) -> Result<(), String> {
    let feedback = crate::with_session(&app, "paint", |s| {
        let region = match &points {
            Some(points) => {
                let (w, h) = document(s)?.size();
                let t = &s.tools.settings;
                let region = crate::tools::tidy_region(
                    points,
                    &t.line_tidy_shape,
                    t.line_tidy_brush_size as f64,
                    w,
                    h,
                );
                if !region.iter().any(|v| *v != 0) {
                    return Ok(Feedback::default());
                }
                Some(region)
            }
            None => None,
        };
        dust_apply(s, region)
    })?;
    status(&app, feedback.message.as_deref());
    Ok(())
}

/// 自動塗りの設定画面の中身。塗れるセルが2つ未満なら Err の知らせ。
#[tauri::command]
pub fn paint_auto_dialog(app: tauri::AppHandle) -> Result<AutoDialog, String> {
    let state = app.state::<Shared>();
    let mut s = state.lock().unwrap();
    let labels = labels(&app, &s);
    auto_paint_dialog(&mut s, &labels)?
}

/// 設定画面のカードの絵（絵のある所を切り抜いて、長辺 `max` 以下へ縮める）。
/// 先頭 8 バイトに幅・高さ（u32 LE）、続けて RGBA。
#[tauri::command]
pub fn paint_cell_thumbnail(
    session: State<Shared>,
    frame: usize,
    layer: usize,
    max: usize,
) -> Response {
    let s = session.lock().unwrap();
    let mut out = vec![0u8; 8];
    let Ok(doc) = document(&s) else {
        return Response::new(out);
    };
    let Some(image) = cell_image(doc, frame, layer, ImageField::Image) else {
        return Response::new(out);
    };
    if let Some(pixels) = thumbnail(image, max.clamp(16, 1024)) {
        out = pixels;
    }
    Response::new(out)
}

/// `_content_rect` で切り抜き、縮める。
fn thumbnail(image: &RgbaImage, max: usize) -> Option<Vec<u8>> {
    let alpha: Vec<u8> = image.rgba().chunks_exact(4).map(|p| p[3]).collect();
    let rect = bucket::mask_bounds(&alpha, image.width(), image.height())
        .map(|(x0, y0, x1, y1)| Rect {
            x: x0,
            y: y0,
            width: x1 - x0,
            height: y1 - y0,
        })
        .or_else(|| {
            (image.width() > 0 && image.height() > 0).then_some(Rect {
                x: 0,
                y: 0,
                width: image.width(),
                height: image.height(),
            })
        })?;
    let premul = PremulImage::from_image_rect(image, rect).ok()?;
    let small = pma_core::export::thumbnail::thumbnail_image(&premul, max).ok()?;
    let mut out = Vec::with_capacity(8 + small.width() * small.height() * 4);
    out.extend_from_slice(&(small.width() as u32).to_le_bytes());
    out.extend_from_slice(&(small.height() as u32).to_le_bytes());
    out.extend_from_slice(&small.to_straight_bytes());
    Some(out)
}

#[tauri::command]
pub fn paint_auto_run(app: tauri::AppHandle, references: Vec<usize>) -> Result<(), String> {
    auto_paint_start(&app, references)
}

#[tauri::command]
pub fn paint_reference_check(app: tauri::AppHandle) -> Result<(), String> {
    {
        let state = app.state::<Shared>();
        let _ = abort_skewer(&mut state.lock().unwrap(), None);
    }
    reference_check_start(&app)
}

/// 動いている計算を中止する。
#[tauri::command]
pub fn paint_job_cancel(session: State<Shared>) {
    if let Some(flag) = &session.lock().unwrap().tools.paint.job {
        flag.store(true, Ordering::Relaxed);
    }
}

/// 一覧の行を選んだ。
#[tauri::command]
pub fn paint_show_finding(app: tauri::AppHandle, row: usize) -> Result<(), String> {
    let view = crate::with_session(&app, "paint", |s| {
        show_finding(s, row)?;
        Ok::<_, String>(s.tools.view())
    })?;
    let _ = app.emit("tools-changed", view);
    Ok(())
}

#[cfg(test)]
#[path = "paint_ops_tests.rs"]
mod tests;
