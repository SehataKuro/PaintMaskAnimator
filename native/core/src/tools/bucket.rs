//! バケツの操作。Python の `canvas_paint_tools.PaintToolsMixin` のバケツ部分
//! （`_bucket_fill_at`・`begin_bucket_drag` から `finish_bucket_drag`・`_fill_patch`・
//! `bucket_reference_pixels`・`replace_color_at` の判定）に対応する。
//!
//! 塗る範囲の計算は [`crate::fill`] にある。ここでは「どの絵を見て、どこを塗り、
//! どの色をどう書き込むか」を決め、文書のセルへ書く。
//!
//! * [`fill_at`]：1か所を塗る（`flood_fill` のバケツ）。
//! * [`BucketDrag`]：押した所を塗り、なぞった先の囲まれた部分も塗る（マウスで押して離す
//!   バケツはこちら。押して離しただけなら [`fill_at`] と同じ画素になる）。
//! * [`compute_region`]：塗る範囲だけを求める（書き込まない）。串刺し塗りも使う。
//! * [`paint_patch`]：塗る範囲へ色を書き込む（不透明度・白の消しゴム）。
//! * [`replace_color_request`]：色置換（#67）で何をどの色へ置き換えるかの判定。
//!
//! 座標はキャンバスの画素座標の実数で受け取り、Python の `int(p.x())` と同じく
//! 0 の方向へ切り捨てる。

use std::borrow::Cow;
use std::collections::BTreeSet;

use crate::compose::layers::{frame_layers, Sources};
use crate::compose::PremulImage;
use crate::document::access::{ArtworkSlot, ImageField};
use crate::document::changes::ChangeSet;
use crate::document::drawing_commands::DrawingTarget;
use crate::document::model::Document;
use crate::fill::islands::Replacement;
use crate::fill::region::{Enclosed, FillOptions};
use crate::fill::session::{
    Bounds, BucketState, Category, LineCache, LocalOutcome, PrepareOptions,
};
use crate::fill::target::{fill_target_mask, TargetColor};
use crate::image::{Rect, RgbaImage};
use crate::project::qt_alpha::qt_premultiplied_roundtrip;
use crate::Error;

/// RGB の色。
pub type Rgb = [u8; 3];

/// なぞり塗りで、端に接した領域を「外の背景ではない」とみなす大きさの上限
/// （画像全体に対する割合）。Python の `BUCKET_DRAG_EDGE_AREA`。
pub const DRAG_EDGE_AREA: f64 = 0.1;
/// 色置換で「線の色」とみなす各チャンネルの上限。Python の `fill_tracking.LINE_MAX`。
pub const LINE_MAX: u8 = 48;
/// 色トレス線とみなす強いチャンネルの幅。Python の `auto_paint.PRIMARY_TOLERANCE`。
const PRIMARY_TOLERANCE: u8 = 40;
/// 色トレス線とみなす弱いチャンネルの上限。Python の `auto_paint.PRIMARY_LOW_TOLERANCE`。
const PRIMARY_LOW_TOLERANCE: u8 = 60;

/// どの画素をたどって塗るか（Python の `bucket_fill.TARGET_*`）。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum TargetKind {
    /// クリックした色の領域（`all`）。
    #[default]
    All,
    /// 透明な画素だけ（`transparent`）。
    Transparent,
    /// 透明以外（`surrounded_by_transparent`）。
    SurroundedByTransparent,
    /// 黒い画素だけ（`black`）。
    Black,
    /// 黒以外（`surrounded_by_black`）。
    SurroundedByBlack,
    /// 白と透明（`white_transparent`）。
    WhiteTransparent,
    /// 白と透明以外（`surrounded_by_white_transparent`）。
    SurroundedByWhiteTransparent,
    /// ごく薄い半透明も透明とみなす（`faint_as_transparent`）。
    FaintAsTransparent,
    /// 透明以外と、その内側の透明の穴（`opaque_and_inner_transparent`）。
    OpaqueAndInnerTransparent,
    /// 透明と、その中の閉じた透明以外の島（`transparent_and_closed_opaque`）。
    TransparentAndClosedOpaque,
}

impl TargetKind {
    /// Python の設定値（`bucket_fill.TARGET_*` の文字列）から読む。知らない値は None。
    pub fn from_name(name: &str) -> Option<Self> {
        Some(match name {
            "all" => Self::All,
            "transparent" => Self::Transparent,
            "surrounded_by_transparent" => Self::SurroundedByTransparent,
            "black" => Self::Black,
            "surrounded_by_black" => Self::SurroundedByBlack,
            "white_transparent" => Self::WhiteTransparent,
            "surrounded_by_white_transparent" => Self::SurroundedByWhiteTransparent,
            "faint_as_transparent" => Self::FaintAsTransparent,
            "opaque_and_inner_transparent" => Self::OpaqueAndInnerTransparent,
            "transparent_and_closed_opaque" => Self::TransparentAndClosedOpaque,
            _ => return None,
        })
    }

    /// 開始点の色種別 `category` でたどる画素の判定（`fill_target_mask` に渡す値）。
    pub fn target_color(self, category: Category) -> TargetColor {
        match self {
            Self::All => TargetColor::All(category),
            Self::Transparent => TargetColor::Transparent,
            Self::SurroundedByTransparent => TargetColor::SurroundedByTransparent,
            Self::Black => TargetColor::Black,
            Self::SurroundedByBlack => TargetColor::SurroundedByBlack,
            Self::WhiteTransparent => TargetColor::WhiteTransparent,
            Self::SurroundedByWhiteTransparent => TargetColor::SurroundedByWhiteTransparent,
            Self::FaintAsTransparent => TargetColor::FaintAsTransparent,
            Self::OpaqueAndInnerTransparent => TargetColor::OpaqueAndInnerTransparent,
            Self::TransparentAndClosedOpaque => TargetColor::TransparentAndClosedOpaque,
        }
    }

    fn enclosed(self) -> Enclosed {
        match self {
            Self::OpaqueAndInnerTransparent => Enclosed::InnerTransparent,
            Self::TransparentAndClosedOpaque => Enclosed::ClosedOpaque,
            _ => Enclosed::None,
        }
    }
}

/// どの絵を見て塗る範囲を決めるか（Python の `bucket_fill.REFERENCE_*`）。
/// 塗る先はいつも指定したレイヤー。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ReferenceSource {
    /// 選択したレイヤーのみ（`layer`）。
    #[default]
    Layer,
    /// キャンバス全体（表示している用紙以外のレイヤーを重ねた絵。`canvas`）。
    Canvas,
    /// 色塗り前の初期状態（保持していなければ今の絵。`initial`）。
    Initial,
}

impl ReferenceSource {
    /// Python の設定値から読む。知らない値は None。
    pub fn from_name(name: &str) -> Option<Self> {
        Some(match name {
            "layer" => Self::Layer,
            "canvas" => Self::Canvas,
            "initial" => Self::Initial,
            _ => return None,
        })
    }
}

/// バケツの設定。Python の `bucket_fill.BucketOptions` と同じ値を持つ。
///
/// ツールパネルからの読み出し（`BucketOptions.from_tools`）は画面側で行う。
/// 「含む色」が未登録ならサブカラーを `mask_colors` に入れ、Shift を押している間は
/// `include_masks` を偽にし、置換色の塗り替えが OFF なら `island_replacements` を空にする。
#[derive(Clone, Debug, PartialEq)]
pub struct BucketSettings {
    /// クリック位置につながる領域だけを塗る（OFF ならレイヤー内の同色を一括）。
    pub adjacent: bool,
    /// 角で接する塗り残しの幅の上限（px）。0 なら拾わない。
    pub island_max_width: usize,
    /// 含み塗り。
    pub include_masks: bool,
    /// 含み塗りの対象色。
    pub mask_colors: Vec<Rgb>,
    /// 含み塗りの対象に背景（擬似透明）が含まれるか。
    pub include_background_mask: bool,
    /// 線の隙間を仮想的に閉じてから塗る。
    pub close_gap: bool,
    /// 隙間閉じの半径（px）。
    pub gap_width: usize,
    /// キャンバス端の手前で途切れた線を端まで仮想的に延長する。
    pub extend_lines: bool,
    /// 線を延長する最大の長さ（px）。
    pub extend_length: usize,
    /// 領域がキャンバス端まで開いている場合は塗らない。
    pub require_closed: bool,
    /// どの画素をたどって塗るか。
    pub target: TargetKind,
    /// どの絵を見て領域を決めるか。
    pub reference: ReferenceSource,
    /// 塗った範囲に囲まれた島を塗り替える色（元の RGB, 塗り替える RGB）。
    pub island_replacements: Vec<Replacement>,
}

impl Default for BucketSettings {
    /// ツールパネルのないときの既定値（`BucketOptions()`）。
    fn default() -> Self {
        Self {
            adjacent: true,
            island_max_width: 0,
            include_masks: false,
            mask_colors: Vec::new(),
            include_background_mask: false,
            close_gap: false,
            gap_width: 4,
            extend_lines: false,
            extend_length: 150,
            require_closed: false,
            target: TargetKind::All,
            reference: ReferenceSource::Layer,
            island_replacements: Vec::new(),
        }
    }
}

impl BucketSettings {
    /// 領域計算の設定（Python の `native._fill_options` と同じ丸め）。
    pub(crate) fn prepare_options(&self) -> PrepareOptions {
        PrepareOptions {
            target_color: self.target.target_color(None),
            extend_lines: self.extend_lines,
            extend_length: self.extend_length.max(1),
            close_gap: self.close_gap,
            gap_width: self.gap_width.max(1),
            island_replacements: self.island_replacements.clone(),
            fill: FillOptions {
                adjacent: self.adjacent,
                require_closed: self.require_closed,
                island_max_width: self.island_max_width,
                include_masks: self.include_masks,
                include_background_mask: self.include_background_mask,
                mask_colors: self.mask_colors.clone(),
                enclosed: self.target.enclosed(),
            },
        }
    }
}

/// 塗る色と不透明度。Python の `paint_source_color()` と `fill_opacity`。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PaintSettings {
    /// 塗る RGB。白は消しゴム（alpha を抜く）。透明色モードなら白を渡す。
    pub color: Rgb,
    /// 不透明度（0〜1）。1 なら色をそのまま書き、未満なら下地と混ぜる。
    pub opacity: f64,
}

impl Default for PaintSettings {
    fn default() -> Self {
        Self {
            color: [0, 0, 0],
            opacity: 1.0,
        }
    }
}

/// 塗れた・塗れなかった理由（Python の `bucket_fill.REASON_*`）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Reason {
    /// 塗れた。
    Ok,
    /// 開始点がキャンバスの外。
    OutOfBounds,
    /// 開始点が選択範囲の外。
    OutsideSelection,
    /// 塗る画素が 1 つも残らなかった。
    Empty,
    /// 領域がキャンバス端まで開いていた（「開いている領域は塗らない」）。
    OpenRegion,
    /// クリックした画素が「対象色」で塗る対象ではなかった。
    NotTarget,
}

impl Reason {
    /// Python の理由の文字列。
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Ok => "ok",
            Self::OutOfBounds => "out_of_bounds",
            Self::OutsideSelection => "outside_selection",
            Self::Empty => "empty",
            Self::OpenRegion => "open_region",
            Self::NotTarget => "not_target",
        }
    }

    fn from_str(name: &str) -> Self {
        match name {
            "ok" => Self::Ok,
            "out_of_bounds" => Self::OutOfBounds,
            "outside_selection" => Self::OutsideSelection,
            "open_region" => Self::OpenRegion,
            "not_target" => Self::NotTarget,
            _ => Self::Empty,
        }
    }

    /// ユーザーに知らせる価値がある理由か（`_bucket_failure_message` が文言を返すもの）。
    /// 選択範囲の外・開いた領域・対象色でない、の3つ。
    pub fn is_reported(self) -> bool {
        matches!(
            self,
            Self::OutsideSelection | Self::OpenRegion | Self::NotTarget
        )
    }
}

/// 塗る範囲の計算結果。Python の `bucket_fill.FillResult`。マスクはキャンバス全体の
/// 0/1 のバイト列（行優先）。
#[derive(Clone, Debug, PartialEq)]
pub struct FillResult {
    /// 塗れたか、塗れなかった理由。
    pub reason: Reason,
    /// 開始点の色種別。None は背景（透明か純白）。
    pub category: Category,
    /// 塗る画素。塗れたときと、開いた領域で塗らなかったとき（どこまで開いていたか）に入る。
    pub region: Option<Vec<u8>>,
    /// 塗れたときの書き込み矩形 `(x0, y0, x1, y1)`（半開。島を含む）。
    pub bounds: Option<Bounds>,
    /// 囲まれた島の塗り替え（塗り替える RGB, 全体マスク）。
    pub islands: Vec<(Rgb, Vec<u8>)>,
    /// 追跡で前のコマの領域と重なった度合い。追跡でない塗りは None。
    pub score: Option<f64>,
}

impl FillResult {
    pub(crate) fn failed(reason: Reason, category: Category) -> Self {
        Self {
            reason,
            category,
            region: None,
            bounds: None,
            islands: Vec::new(),
            score: None,
        }
    }

    /// 塗れたか（Python の `FillResult.ok`）。
    pub fn ok(&self) -> bool {
        self.reason == Reason::Ok && self.region.is_some()
    }

    /// Rust の矩形内の結果を全体マスクへ広げる（Python の `_native_fill_result`）。
    pub(crate) fn from_local(output: LocalOutcome, width: usize, height: usize) -> Self {
        let reason = Reason::from_str(output.reason);
        let region = match (&output.region, output.bounds) {
            (Some(local), Some(bounds)) => Some(expand(local, bounds, width, height)),
            _ => None,
        };
        let mut bounds = output.bounds;
        let mut islands = Vec::with_capacity(output.islands.len());
        for island in output.islands {
            if let Some((x0, y0, x1, y1)) = bounds {
                let (ix0, iy0, ix1, iy1) = island.bounds;
                bounds = Some((x0.min(ix0), y0.min(iy0), x1.max(ix1), y1.max(iy1)));
            }
            islands.push((
                island.color,
                expand(&island.mask, island.bounds, width, height),
            ));
        }
        Self {
            reason,
            category: output.category,
            region,
            bounds: if reason == Reason::Ok { bounds } else { None },
            islands,
            score: None,
        }
    }
}

/// 矩形内のマスクをキャンバス全体のマスクに置く。
pub(crate) fn expand(local: &[u8], bounds: Bounds, width: usize, height: usize) -> Vec<u8> {
    let (x0, y0, x1, y1) = bounds;
    let mut out = vec![0; width * height];
    let row = x1 - x0;
    if row == 0 {
        return out;
    }
    for (y, part) in (y0..y1).zip(local.chunks_exact(row)) {
        out[y * width + x0..y * width + x1].copy_from_slice(part);
    }
    out
}

/// 全体マスクの `bounds` の範囲を切り出す。
pub(crate) fn crop(mask: &[u8], bounds: Bounds, width: usize) -> Vec<u8> {
    let (x0, y0, x1, y1) = bounds;
    let mut out = Vec::with_capacity((x1 - x0) * (y1 - y0));
    for y in y0..y1 {
        out.extend_from_slice(&mask[y * width + x0..y * width + x1]);
    }
    out
}

/// マスクの外接矩形（半開）。空なら None。Python の `imaging.mask_bounds`。
pub fn mask_bounds(mask: &[u8], width: usize, height: usize) -> Option<Bounds> {
    if width == 0 || height == 0 || mask.len() != width * height {
        return None;
    }
    crate::fill::region::bounds(mask, width, height)
}

pub(crate) fn bounds_rect((x0, y0, x1, y1): Bounds) -> Rect {
    Rect {
        x: x0,
        y: y0,
        width: x1 - x0,
        height: y1 - y0,
    }
}

fn invalid(message: impl Into<String>) -> Error {
    Error::Invalid(message.into())
}

/// Python の `int(p.x())`（0 の方向へ切り捨て）。非有限の値はキャンバスの外とする。
pub(crate) fn truncate(value: f64) -> i64 {
    if value.is_finite() {
        value.trunc() as i64
    } else {
        i64::MIN
    }
}

/// `(x, y)` を種に塗る範囲を求める（Python の `bucket_fill.compute_fill_region`）。
///
/// `rgba` は判定に使う絵（[`reference_pixels`]）。`selection` はキャンバス全体の 0/1 の
/// マスク。`line_cache` はなぞり塗りの間だけ使い回す線の延長の下処理（結果は変わらない）。
#[allow(clippy::too_many_arguments)]
pub fn compute_region(
    rgba: &[u8],
    width: usize,
    height: usize,
    x: i64,
    y: i64,
    settings: &BucketSettings,
    selection: Option<&[u8]>,
    line_cache: Option<&mut LineCache>,
) -> Result<FillResult, Error> {
    if x < 0 || y < 0 || x as u64 >= width as u64 || y as u64 >= height as u64 {
        return Ok(FillResult::failed(Reason::OutOfBounds, None));
    }
    let (x, y) = (x as usize, y as usize);
    if let Some(selection) = selection {
        if selection.len() != width * height {
            return Err(invalid("選択範囲の大きさが画像と違います"));
        }
        if selection[y * width + x] == 0 {
            return Ok(FillResult::failed(Reason::OutsideSelection, None));
        }
    }
    let mut state = BucketState::new(rgba, width, height, settings.prepare_options(), false)
        .map_err(invalid)?;
    if let Some(cache) = line_cache {
        std::mem::swap(&mut state.line_cache, cache);
        let result = state.compute(x, y, selection);
        std::mem::swap(&mut state.line_cache, cache);
        Ok(FillResult::from_local(
            result.map_err(invalid)?,
            width,
            height,
        ))
    } else {
        let result = state.compute(x, y, selection).map_err(invalid)?;
        Ok(FillResult::from_local(result, width, height))
    }
}

/// 開始点の色種別（Python の `bucket_fill.seed_category`）。背景（透明か純白）は None。
pub fn seed_category(rgba: &[u8], width: usize, x: usize, y: usize) -> Category {
    let i = (y * width + x) * 4;
    let pixel = &rgba[i..i + 4];
    if pixel[3] == 0 || pixel[..3] == [255; 3] {
        None
    } else {
        Some([pixel[0], pixel[1], pixel[2]])
    }
}

/// `patch` のうち `mask` が立った画素。
fn selected<'a>(patch: &'a mut [u8], mask: &'a [u8]) -> impl Iterator<Item = &'a mut [u8]> {
    patch
        .chunks_exact_mut(4)
        .zip(mask)
        .filter(|(_, m)| **m != 0)
        .map(|(p, _)| p)
}

/// `patch`（RGBA の部分画像）の `mask`（同じ大きさの 0/1）の画素を塗り、書き込んだ RGB を
/// 返す（Python の `_fill_patch`）。
///
/// 白は消しゴムとして画素を透明（全チャンネル 0）にし、空を返す（使用色に登録しない）。
/// 不透明度が 100% なら色をそのまま書く。未満なら下地（透明は白とみなす）と混ぜ、
/// 混ぜた結果が白になった画素は 254 にする（消しゴム扱いにしない）。
pub fn paint_patch(patch: &mut [u8], mask: &[u8], paint: PaintSettings) -> Vec<Rgb> {
    let source = paint.color;
    if source == [255; 3] {
        for pixel in selected(patch, mask) {
            pixel.fill(0);
        }
        return Vec::new();
    }
    // Python の max(0.0, min(1.0, float(opacity)))。NaN は min・max で 1.0 になる。
    let opacity = if paint.opacity.is_nan() {
        1.0
    } else {
        paint.opacity.clamp(0.0, 1.0)
    };
    if opacity >= 0.999999 {
        for pixel in selected(patch, mask) {
            pixel[..3].copy_from_slice(&source);
            pixel[3] = 255;
        }
        return vec![source];
    }
    // numpy の float32 の配列と Python の float の演算（float32 で計算する）。
    let keep = (1.0 - opacity) as f32;
    let weight = opacity as f32;
    let mut written = BTreeSet::new();
    for pixel in selected(patch, mask) {
        let transparent = pixel[3] == 0;
        let mut mixed = [0u8; 3];
        for (channel, out) in mixed.iter_mut().enumerate() {
            let base = if transparent {
                255.0
            } else {
                f32::from(pixel[channel])
            };
            let value = base * keep + f32::from(source[channel]) * weight;
            *out = value.round_ties_even().clamp(0.0, 255.0) as u8;
        }
        if mixed == [255; 3] {
            mixed = [254; 3];
        }
        pixel[..3].copy_from_slice(&mixed);
        pixel[3] = 255;
        written.insert(mixed);
    }
    written.into_iter().collect()
}

/// 位置 `(frame, layer)` が持つ絵の窓口（Python の `frames[frame].layers[layer]` の絵）。
/// 番号付きの絵は列のバンク、それ以外は位置の局所の絵。
pub fn position_slot(document: &Document, frame: usize, layer: usize) -> Option<ArtworkSlot> {
    let track = *document.track_order().get(layer)?;
    let frame_id = *document.frame_order().get(frame)?;
    let position = document.frame(frame_id)?.positions.get(&track)?;
    Some(if position.references_bank() {
        ArtworkSlot::Bank(track, position.number()?)
    } else {
        ArtworkSlot::Local(frame_id, track)
    })
}

/// 位置 `(frame, layer)` の絵（Python の `layer.image`）。絵がなければキャンバスの大きさの
/// 透明な画像。保持セルを露出元へ寄せはしない（バケツは呼び出し側が編集できるキーにしてから塗る）。
pub fn position_image(document: &Document, frame: usize, layer: usize) -> Result<RgbaImage, Error> {
    if frame >= document.frame_order().len() || layer >= document.track_order().len() {
        return Err(invalid("コマかレイヤーが範囲の外です"));
    }
    match position_slot(document, frame, layer)
        .and_then(|slot| document.artwork_image(slot, ImageField::Image))
    {
        Some(image) => Ok(image.clone()),
        None => {
            let (width, height) = document.size();
            RgbaImage::blank(width, height)
        }
    }
}

/// Python が絵を QImage（乗算済み）にしてから RGBA8888 に戻して読む画素
/// （`_layer_fill_pixels(as_qimage(image))`）。
///
/// 半透明の画素はこの往復で丸められ、往復を重ねると値が変わることもある（冪等ではない）。
/// 戻しは aarch64 の Qt に合わせる（[`qt_premultiplied_roundtrip`] の注記を参照）。
pub fn qt_pixels(image: &RgbaImage) -> Vec<u8> {
    let mut pixels = image.rgba().to_vec();
    qt_premultiplied_roundtrip(&mut pixels, image.width());
    pixels
}

/// Qt の qPremultiply の 1 チャンネル分。
fn qt_premultiply(c: u32, a: u32) -> u32 {
    let t = c * a;
    (t + (t >> 8) + 0x80) >> 8
}

/// 乗算済みの値 p（alpha a）を乗算して p になるストレートの値の表。`[a][p]`。
fn premultiplied_inverse() -> &'static [u8] {
    static TABLE: std::sync::OnceLock<Vec<u8>> = std::sync::OnceLock::new();
    TABLE.get_or_init(|| {
        let mut table = vec![0u8; 256 * 256];
        for a in 1..256u32 {
            // c を上から回し、同じ p になる c のうち一番小さい値を残す（どれでも戻す値は同じ）。
            for c in (0..256u32).rev() {
                table[(a * 256 + qt_premultiply(c, a)) as usize] = c as u8;
            }
        }
        table
    })
}

/// 乗算済みの画像を Qt の RGBA8888 への変換と同じ値で戻す（[`qt_pixels`] と同じ丸め）。
///
/// 乗算すると同じ値になるストレートの画素を作り、Qt の往復を当てる。
fn qt_unpremultiplied(image: &PremulImage) -> Vec<u8> {
    let inverse = premultiplied_inverse();
    let mut pixels = image.pixels().to_vec();
    for pixel in pixels.chunks_exact_mut(4) {
        let a = usize::from(pixel[3]);
        if a == 0 || a == 255 {
            continue;
        }
        for channel in &mut pixel[..3] {
            *channel = inverse[a * 256 + usize::from(*channel).min(a)];
        }
    }
    qt_premultiplied_roundtrip(&mut pixels, image.width());
    pixels
}

/// 塗る範囲の判定に使う絵（Python の `bucket_reference_pixels`）。ストレート RGBA。
///
/// `fallback` は塗る先のレイヤーの今の画素（[`qt_pixels`]）で、「選択したレイヤーのみ」なら
/// そのまま返す。初期状態は、露出元の初期画像が同じ大きさであるときだけ使う。キャンバス
/// 全体は、表示していて用紙でないレイヤーを、Qt の QPainter と同じ式で乗算済みのまま重ねて
/// Qt と同じ丸めでストレートへ戻す。
pub fn reference_pixels<'a>(
    document: &Document,
    frame: usize,
    layer: usize,
    reference: ReferenceSource,
    fallback: &'a [u8],
    width: usize,
    height: usize,
) -> Result<Cow<'a, [u8]>, Error> {
    match reference {
        ReferenceSource::Layer => Ok(Cow::Borrowed(fallback)),
        ReferenceSource::Initial => {
            let sources = Sources::new(document)?;
            let initial = sources
                .layer(document, frame, layer)
                .and_then(|source| source.initial_image)
                .filter(|image| image.width() == width && image.height() == height);
            Ok(match initial {
                Some(image) => Cow::Owned(qt_pixels(image)),
                None => Cow::Borrowed(fallback),
            })
        }
        ReferenceSource::Canvas => {
            if frame >= document.frame_order().len() {
                return Ok(Cow::Borrowed(fallback));
            }
            let mut result = PremulImage::transparent(width, height)?;
            for source in frame_layers(document, frame)?.into_iter().flatten() {
                let Some(image) = source.image else { continue };
                if !source.settings.visible
                    || source.settings.is_paper
                    || image.width() == 0
                    || image.height() == 0
                {
                    continue;
                }
                let pixels = PremulImage::from_image_rect(
                    image,
                    Rect {
                        x: 0,
                        y: 0,
                        width: image.width(),
                        height: image.height(),
                    },
                )?;
                result.draw(&pixels, 0, 0, source.settings.opacity);
            }
            Ok(Cow::Owned(qt_unpremultiplied(&result)))
        }
    }
}

/// 1回の塗りで文書へ書いた内容。Undo と再描画に使う。
#[derive(Clone, Debug)]
pub struct BucketEdit {
    /// 塗ったコマ（位置の表示インデックス）。
    pub frame: usize,
    /// 塗ったレイヤー。
    pub layer: usize,
    /// 書き換えた矩形（この外の画素は変えていない）。
    pub rect: Rect,
    /// 書く前の `rect` の画素（ストレート RGBA）。
    pub before: Vec<u8>,
    /// 実際に書き込んだ RGB（使用色への登録用）。昇順で重複なし。白の消しゴムは含めない。
    pub written_colors: Vec<Rgb>,
    /// 塗った画素のマスク（キャンバス全体）。島は含まない。
    pub mask: Vec<u8>,
    /// 文書の変更通知（画素と、絵の有無の印）。
    pub changes: ChangeSet,
}

/// [`fill_at`] の結果。
#[derive(Clone, Debug)]
pub enum FillOutcome {
    /// 塗った。
    Filled(BucketEdit),
    /// 塗らなかった。理由と、分かっていればどこまでを判定したか（開いた領域など）。
    Skipped(FillResult),
}

/// 塗る先の画素（[`qt_pixels`]）。なぞり塗りの間は、書いた分を反映して使い回す
/// （Python の `drag["pixels"]`。往復は冪等でないので、書いた絵を読み直さない）。
#[derive(Default)]
pub(crate) struct LayerPixels {
    generation: Option<u64>,
    pixels: Vec<u8>,
}

impl LayerPixels {
    fn get(&mut self, image: &RgbaImage) -> &mut Vec<u8> {
        if self.generation != Some(image.generation()) {
            self.pixels = qt_pixels(image);
            self.generation = Some(image.generation());
        }
        &mut self.pixels
    }
}

/// 塗る範囲 `result` を位置へ書く（`_bucket_fill_at` の後半）。`pixels` は塗る先の
/// [`qt_pixels`]。書いた矩形の画素を `pixels` にも反映する。
#[allow(clippy::too_many_arguments)]
fn write_fill(
    document: &mut Document,
    frame: usize,
    layer: usize,
    image: &RgbaImage,
    pixels: &mut [u8],
    result: FillResult,
    paint: PaintSettings,
) -> Result<BucketEdit, Error> {
    let bounds = result
        .bounds
        .ok_or_else(|| invalid("塗る範囲がありません"))?;
    let region = result
        .region
        .ok_or_else(|| invalid("塗る範囲がありません"))?;
    let width = image.width();
    let rect = bounds_rect(bounds);
    let before = image.copy_rgba_rect(rect)?;
    let mut patch = crop_rgba(pixels, rect, width);
    let mut written: BTreeSet<Rgb> = paint_patch(&mut patch, &crop(&region, bounds, width), paint)
        .into_iter()
        .collect();
    for (rgb, island) in &result.islands {
        let local = crop(island, bounds, width);
        if local.iter().any(|v| *v != 0) {
            written.extend(paint_patch(
                &mut patch,
                &local,
                PaintSettings {
                    color: *rgb,
                    ..paint
                },
            ));
        }
    }
    paste_rgba(pixels, &patch, rect, width);
    // Python の PmaImage.write_rgba は、部分画像を Qt の乗算済み形式で往復してから書く
    // （4 画素ずつの区切りは部分画像の行の先頭から）。読み回しの画素は往復前のまま。
    qt_premultiplied_roundtrip(&mut patch, rect.width);
    let mut changes =
        document.write_drawing_patch(layer, DrawingTarget::Frame(frame), rect, &patch)?;
    changes.extend(document.set_drawing_content(frame, layer, true)?);
    Ok(BucketEdit {
        frame,
        layer,
        rect,
        before,
        written_colors: written.into_iter().collect(),
        mask: region,
        changes,
    })
}

/// 全体の RGBA から矩形を切り出す。
pub(crate) fn crop_rgba(pixels: &[u8], rect: Rect, width: usize) -> Vec<u8> {
    let mut out = Vec::with_capacity(rect.width * rect.height * 4);
    for y in rect.y..rect.y + rect.height {
        out.extend_from_slice(
            &pixels[(y * width + rect.x) * 4..(y * width + rect.x + rect.width) * 4],
        );
    }
    out
}

/// 全体の RGBA の矩形へ書き戻す。
pub(crate) fn paste_rgba(pixels: &mut [u8], patch: &[u8], rect: Rect, width: usize) {
    let row = rect.width * 4;
    if row == 0 {
        return;
    }
    for (y, part) in (rect.y..rect.y + rect.height).zip(patch.chunks_exact(row)) {
        pixels[(y * width + rect.x) * 4..][..row].copy_from_slice(part);
    }
}

/// 判定と書き込み。`accept` が偽を返した結果は塗らずに返す（なぞり塗りの外の背景）。
#[allow(clippy::too_many_arguments)]
fn fill_once(
    document: &mut Document,
    frame: usize,
    layer: usize,
    settings: &BucketSettings,
    paint: PaintSettings,
    selection: Option<&[u8]>,
    (x, y): (i64, i64),
    accept: Option<&dyn Fn(&FillResult) -> bool>,
    line_cache: Option<&mut LineCache>,
    cache: &mut LayerPixels,
) -> Result<FillOutcome, Error> {
    let image = position_image(document, frame, layer)?;
    let (width, height) = (image.width(), image.height());
    let pixels = cache.get(&image);
    let reference = reference_pixels(
        document,
        frame,
        layer,
        settings.reference,
        pixels,
        width,
        height,
    )?;
    let result = compute_region(
        &reference, width, height, x, y, settings, selection, line_cache,
    )?;
    drop(reference);
    if !result.ok() || accept.is_some_and(|accept| !accept(&result)) {
        return Ok(FillOutcome::Skipped(result));
    }
    let edit = write_fill(document, frame, layer, &image, pixels, result, paint)?;
    // 書いた絵の世代で、反映済みの画素を使い回す。
    let written = position_image(document, frame, layer)?;
    cache.generation = Some(written.generation());
    Ok(FillOutcome::Filled(edit))
}

/// `(x, y)` を種に、位置 `(frame, layer)` を1か所塗る（Python の `flood_fill` のバケツ）。
///
/// 位置は編集できるキー（`ensure_editable_key` の後）であること。`selection` は
/// キャンバス全体の 0/1 の選択マスク（無ければ None）。
pub fn fill_at(
    document: &mut Document,
    frame: usize,
    layer: usize,
    settings: &BucketSettings,
    paint: PaintSettings,
    selection: Option<&[u8]>,
    (x, y): (f64, f64),
) -> Result<FillOutcome, Error> {
    fill_once(
        document,
        frame,
        layer,
        settings,
        paint,
        selection,
        (truncate(x), truncate(y)),
        None,
        None,
        &mut LayerPixels::default(),
    )
}

/// なぞり塗りの1回分の結果。
#[derive(Clone, Debug, Default)]
pub struct DragStep {
    /// この回に塗った分（順に書いた）。
    pub edits: Vec<BucketEdit>,
    /// 押した所を塗れなかった理由（押したときだけ。なぞった先の失敗は知らせない）。
    pub failure: Option<Reason>,
}

/// なぞり塗りの Undo に積む内容（Python の `LayerRegionUndo`）。
#[derive(Clone, Debug, PartialEq)]
pub struct DragUndo {
    /// 塗ったコマ。
    pub frame: usize,
    /// 塗ったレイヤー。
    pub layer: usize,
    /// なぞりで塗った画素の外接矩形。
    pub rect: Rect,
    /// 押す前の `rect` の画素。
    pub before: Vec<u8>,
}

/// バケツのなぞり塗り（Python の `begin_bucket_drag`〜`finish_bucket_drag`）。
///
/// 押した所は1か所の塗りと同じく塗る。なぞった先では、まだ塗っていない囲まれた
/// 部分を塗る。線の上を通っても線を塗らないよう、塗り始めてよい画素は押したときの
/// 絵で決める。端に接して大きすぎる領域（外の背景）はなぞった先では塗らない。
pub struct BucketDrag {
    frame: usize,
    layer: usize,
    settings: BucketSettings,
    paint: PaintSettings,
    selection: Option<Vec<u8>>,
    before: RgbaImage,
    eligible: Vec<u8>,
    tried: Vec<u8>,
    painted: Vec<u8>,
    width: usize,
    height: usize,
    last: (f64, f64),
    first_area: usize,
    line_cache: LineCache,
    pixels: LayerPixels,
}

impl BucketDrag {
    /// 押した。押した所を塗り、なぞりの準備をする。押した所がキャンバスの外なら
    /// None（何もしない）。
    pub fn begin(
        document: &mut Document,
        frame: usize,
        layer: usize,
        settings: BucketSettings,
        paint: PaintSettings,
        selection: Option<Vec<u8>>,
        point: (f64, f64),
    ) -> Result<Option<(Self, DragStep)>, Error> {
        let before = position_image(document, frame, layer)?;
        let (width, height) = (before.width(), before.height());
        let (x, y) = (truncate(point.0), truncate(point.1));
        if x < 0 || y < 0 || x as u64 >= width as u64 || y as u64 >= height as u64 {
            return Ok(None);
        }
        let mut pixels = LayerPixels::default();
        let reference = reference_pixels(
            document,
            frame,
            layer,
            settings.reference,
            pixels.get(&before),
            width,
            height,
        )?;
        let category = seed_category(&reference, width, x as usize, y as usize);
        let eligible = fill_target_mask(
            &reference,
            width,
            height,
            settings.target.target_color(category),
        );
        drop(reference);
        let mut drag = Self {
            frame,
            layer,
            settings,
            paint,
            selection,
            before,
            eligible,
            tried: vec![0; width * height],
            painted: vec![0; width * height],
            width,
            height,
            last: point,
            first_area: 0,
            line_cache: LineCache::default(),
            pixels,
        };
        let step = drag.fill_point(document, point, true)?;
        Ok(Some((drag, step)))
    }

    /// なぞった。前の点から `point` までの区間上の、まだ塗っていない囲まれた部分を塗る。
    pub fn extend(
        &mut self,
        document: &mut Document,
        point: (f64, f64),
    ) -> Result<DragStep, Error> {
        let (sx, sy) = self.last;
        let (px, py) = point;
        let distance = (px - sx).hypot(py - sy);
        let steps = distance.ceil().max(1.0) as u64;
        let mut result = DragStep::default();
        for step in 1..=steps {
            let t = step as f64 / steps as f64;
            let part =
                self.fill_point(document, (sx + (px - sx) * t, sy + (py - sy) * t), false)?;
            result.edits.extend(part.edits);
        }
        self.last = point;
        Ok(result)
    }

    /// なぞりの途中で設定を変える（Shift で含み塗りを1回外すなど）。塗り始めてよい画素は
    /// 押したときのまま。
    pub fn set_settings(&mut self, settings: BucketSettings, paint: PaintSettings) {
        self.settings = settings;
        self.paint = paint;
    }

    /// 塗っている位置 `(コマ, レイヤー)`。
    pub fn target(&self) -> (usize, usize) {
        (self.frame, self.layer)
    }

    /// これまでに塗った画素（キャンバス全体の 0/1）。
    pub fn painted(&self) -> &[u8] {
        &self.painted
    }

    /// 離した。なぞりで塗った分を Undo 1回で戻せるよう、外接矩形と押す前の画素を返す。
    /// 何も塗っていないか、位置がなくなっていれば None。
    pub fn finish(self, document: &Document) -> Result<Option<DragUndo>, Error> {
        if self.frame >= document.frame_order().len() || self.layer >= document.track_order().len()
        {
            return Ok(None);
        }
        let Some(bounds) = mask_bounds(&self.painted, self.width, self.height) else {
            return Ok(None);
        };
        let rect = bounds_rect(bounds);
        Ok(Some(DragUndo {
            frame: self.frame,
            layer: self.layer,
            rect,
            before: self.before.copy_rgba_rect(rect)?,
        }))
    }

    fn fill_point(
        &mut self,
        document: &mut Document,
        point: (f64, f64),
        first: bool,
    ) -> Result<DragStep, Error> {
        let (x, y) = (truncate(point.0), truncate(point.1));
        let mut step = DragStep::default();
        if x < 0 || y < 0 || x as u64 >= self.width as u64 || y as u64 >= self.height as u64 {
            return Ok(step);
        }
        let index = y as usize * self.width + x as usize;
        if self.tried[index] != 0 || self.eligible[index] == 0 {
            return Ok(step);
        }
        let (size, first_area) = ((self.width, self.height), self.first_area);
        let accept = move |result: &FillResult| accepts(result, size, first_area);
        let outcome = fill_once(
            document,
            self.frame,
            self.layer,
            &self.settings,
            self.paint,
            self.selection.as_deref(),
            (x, y),
            if first { None } else { Some(&accept) },
            Some(&mut self.line_cache),
            &mut self.pixels,
        )?;
        // 塗れなかった点・塗らなかった領域も、同じ場所で何度も判定しない。
        self.tried[index] = 1;
        match outcome {
            FillOutcome::Skipped(result) => {
                if first && !result.ok() {
                    step.failure = Some(result.reason);
                }
                if let Some(region) = &result.region {
                    merge(&mut self.tried, region);
                }
            }
            FillOutcome::Filled(edit) => {
                merge(&mut self.tried, &edit.mask);
                merge(&mut self.painted, &edit.mask);
                if first {
                    self.first_area = edit.mask.iter().filter(|v| **v != 0).count();
                }
                step.edits.push(edit);
            }
        }
        Ok(step)
    }
}

fn merge(target: &mut [u8], mask: &[u8]) {
    for (t, m) in target.iter_mut().zip(mask) {
        *t |= *m;
    }
}

/// なぞった先の領域を塗るか（Python の `_bucket_drag_accepts`）。外の背景だけは塗らない。
///
/// 端に接していなければ塗る。端に接していれば、最初に塗った部分の3倍か画像全体の
/// [`DRAG_EDGE_AREA`] の大きい方までなら塗る。
fn accepts(result: &FillResult, (width, height): (usize, usize), first_area: usize) -> bool {
    let Some(region) = &result.region else {
        return true;
    };
    let Some((x0, y0, x1, y1)) = mask_bounds(region, width, height) else {
        return true;
    };
    let touches_edge = x0 == 0 || y0 == 0 || x1 == width || y1 == height;
    if !touches_edge {
        return true;
    }
    let area = region.iter().filter(|v| **v != 0).count();
    let limit = (3 * first_area).max((region.len() as f64 * DRAG_EDGE_AREA) as usize);
    area <= limit
}

/// 色置換（#67）で置き換える色の組。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ReplaceRequest {
    /// クリックした色。
    pub source: Rgb,
    /// 置き換える色（今の色）。
    pub target: Rgb,
    /// クリックした色が線の色（黒の主線か色トレス線）か。画面は置き換えてよいか尋ねる。
    pub is_line: bool,
}

/// 色置換で置き換えなかった理由。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReplaceRefusal {
    /// クリックした所がキャンバスの外。
    OutOfBounds,
    /// このコマには絵がない（露出元がない）。
    NoArtwork,
    /// 透明の所をクリックした。
    Transparent,
    /// クリックした色が今の色と同じ。
    SameColor,
}

/// 色トレス線の色か（Python の `auto_paint._is_primary`）。
pub fn is_primary_line_color(rgb: Rgb) -> bool {
    let floor = 255 - PRIMARY_TOLERANCE;
    let strong = rgb.iter().filter(|c| **c >= floor).count();
    rgb.iter()
        .all(|c| *c >= floor || *c <= PRIMARY_LOW_TOLERANCE)
        && (1..=2).contains(&strong)
}

/// バケツの色置換（Python の `replace_color_at` の判定）。クリックした色を、選択
/// レイヤーの全コマで `color` へ置き換える組を返す。
///
/// 置き換えそのもの（使用色パネルの `apply_palette_replacements`）はここでは行わない。
/// 半透明の画素は Qt と同じく、乗算済みにして戻した値で読む。
pub fn replace_color_request(
    document: &Document,
    frame: usize,
    layer: usize,
    color: Rgb,
    (x, y): (f64, f64),
) -> Result<Result<ReplaceRequest, ReplaceRefusal>, Error> {
    let (x, y) = (truncate(x), truncate(y));
    let current = position_image(document, frame, layer)?;
    if x < 0 || y < 0 || x as u64 >= current.width() as u64 || y as u64 >= current.height() as u64 {
        return Ok(Err(ReplaceRefusal::OutOfBounds));
    }
    let sources = Sources::new(document)?;
    let Some(key) = sources.key_frame(frame, layer) else {
        return Ok(Err(ReplaceRefusal::NoArtwork));
    };
    let image = position_image(document, key, layer)?;
    if x as usize >= image.width() || y as usize >= image.height() {
        return Ok(Err(ReplaceRefusal::OutOfBounds));
    }
    let i = (y as usize * image.width() + x as usize) * 4;
    // Python は1画素の QImage（乗算済み）の pixelColor で読む（qUnpremultiply で戻す）。
    let mut pixel = image.rgba()[i..i + 4].to_vec();
    qt_premultiplied_roundtrip(&mut pixel, 1);
    if pixel[3] == 0 {
        return Ok(Err(ReplaceRefusal::Transparent));
    }
    let source = [pixel[0], pixel[1], pixel[2]];
    if source == color {
        return Ok(Err(ReplaceRefusal::SameColor));
    }
    let is_line = source.iter().all(|c| *c <= LINE_MAX) || is_primary_line_color(source);
    Ok(Ok(ReplaceRequest {
        source,
        target: color,
        is_line,
    }))
}
