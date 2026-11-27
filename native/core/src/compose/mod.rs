//! キャンバスのレイヤー合成（Python・Qt に依存しない）。
//!
//! Python の `canvas.PaintCanvas.composite`（書き出し・サムネイル・オニオンの合成）と
//! `draw_frame_direct`（編集画面の表示）を、文書（[`Document`]）から直接作る。
//! Qt は乗算済みの ARGB32 で QPainter のソースオーバーを掛けるので、ここも乗算済みの
//! 画素（[`PremulImage`]）で同じ丸めの式を使い、Qt と同じバイト列にする。
//!
//! * [`composite`]・[`composite_rect`]：書き出しと同じ合成（`composite`）。
//! * [`layer_image`]・[`view_layer_image`]：レイヤーごとの表示用画像。GPU で重ねる画面向け。
//! * [`view_composite`]・[`display_frame`]：編集画面の表示（初期画像・表示プレビュー・
//!   縮小・反転・オニオン）。
//! * [`scale`]：縮小表示（ニアレストと線を残す縮小）。
//!
//! Qt は表示画像を乗算済みからストレートへ戻してから色で判定する（使用色の絞り込み・
//! 表示プレビュー・線を残す縮小）。その割り算は SIMD 版では浮動小数の近似で、スカラー版
//! （[`pixel::unpremultiply`]）と半透明の画素で ±1 違う。文書の画素は Qt で正規化済み
//! （乗算済みにして戻した値）なので、ここではその値をそのまま使い、Qt と同じ判定にする。
//! 置換・表示プレビューで色を変えた半透明の画素と、オニオンの合成結果をストレートへ
//! 戻す所だけはスカラー版で割るため、CPU によっては Qt と ±1 違いうる。

pub mod layers;
pub mod onion;
pub mod painter;
pub mod pixel;
pub mod scale;
pub mod view;

use crate::display::Rgb;
use crate::document::model::Document;
use crate::image::Rect;
use crate::Error;

pub use layers::{frame_layers, FrameLayer};
pub use onion::{
    draw_onions, onion_draws, onion_frame_indices, onion_target_size, tinted_onion_image,
    OnionDraw, OnionSettings, OnionSide,
};
pub use pixel::{const_alpha, premultiply, unpremultiply, Premul, PremulImage};
pub use scale::{
    display_image_for_size, display_image_for_size_with, display_size, shrink_keeping_lines,
    shrink_keeping_lines_with, ShrinkMode,
};
pub use view::{Channel, MapColorMapping, ViewPreview};

use layers::{display_pixels, overlap, view_pixels, Sources};

/// 合成の下地。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Background {
    /// 透明（`composite(fi, white=False)`）。
    #[default]
    Transparent,
    /// 白（`composite(fi, white=True)`）。
    White,
    /// 不透明な1色（画面の擬似透明の表示色など）。
    Color([u8; 3]),
}

impl Background {
    fn pixel(self) -> Premul {
        match self {
            Self::Transparent => [0; 4],
            Self::White => [255; 4],
            Self::Color([r, g, b]) => [r, g, b, 255],
        }
    }
}

/// レイヤーの表示フィルタの設定（キャンバスの表示状態）。表示・不透明度・用紙・下書き・
/// 色フィルタは文書の列の設定から読む。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct DisplayOptions {
    /// 使用色の絞り込み・置換を掛けるレイヤー（表示順の番号）。None なら文書の選択列。
    pub active_layer: Option<usize>,
    /// 表示する使用色（`visible_color_rgbs`）。None なら絞り込まない。アクティブレイヤーだけ。
    pub visible_colors: Option<Vec<Rgb>>,
    /// 子色を親色で見せる置換（`preview_color_remap`）。アクティブレイヤーだけ。
    pub color_remap: Vec<(Rgb, Rgb)>,
    /// 用紙以外を黒で見せる（`silhouette_non_background`）。
    pub silhouette: bool,
}

/// 編集画面だけの表示設定。書き出しの合成には掛けない。
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ViewOptions {
    /// 初期画像を見せる（`show_initial_state`）。初期画像には表示フィルタを掛けない。
    pub show_initial_state: bool,
    /// 表示プレビュー（露光量・チャンネル・隣接色分け）。
    pub preview: ViewPreview,
}

/// レイヤーごとの表示用画像と、重ねるときの不透明度。
#[derive(Clone, Debug, PartialEq)]
pub struct LayerImage {
    /// レイヤーの表示順の番号（下から 0）。
    pub layer: usize,
    /// 絵を持つコマ。
    pub key_frame: usize,
    /// 重ねるときの不透明度（列の設定）。[`const_alpha`] で Qt と同じ定数アルファになる。
    pub opacity: f64,
    /// キャンバス上の位置（左上）。全体の画像なら (0, 0)。
    pub x: usize,
    /// キャンバス上の位置（左上）。
    pub y: usize,
    /// 乗算済みの画素。
    pub image: PremulImage,
}

/// 使用色の絞り込みを掛けるレイヤー。
pub(crate) fn active_layer(document: &Document, options: &DisplayOptions) -> usize {
    options.active_layer.unwrap_or_else(|| {
        let (_, track) = document.selection();
        document.track_index(track).unwrap_or(usize::MAX)
    })
}

fn canvas_rect(document: &Document) -> Rect {
    let (width, height) = document.size();
    Rect {
        x: 0,
        y: 0,
        width,
        height,
    }
}

fn check_rect(document: &Document, rect: Rect) -> Result<(), Error> {
    let (width, height) = document.size();
    if rect.x.checked_add(rect.width).is_none_or(|x| x > width)
        || rect.y.checked_add(rect.height).is_none_or(|y| y > height)
    {
        return Err(Error::Image("矩形がキャンバスの範囲外です"));
    }
    Ok(())
}

/// 書き出しと同じ合成の、`rect`（None なら全体）の範囲。
pub(crate) fn composite_with(
    document: &Document,
    sources: &Sources,
    frame: usize,
    options: &DisplayOptions,
    background: Background,
    rect: Option<Rect>,
) -> Result<PremulImage, Error> {
    let rect = rect.unwrap_or_else(|| canvas_rect(document));
    let mut result = PremulImage::filled(rect.width, rect.height, background.pixel())?;
    let active = active_layer(document, options);
    for layer in 0..sources.layer_count() {
        let Some(source) = sources.layer(document, frame, layer) else {
            continue;
        };
        if !source.settings.visible {
            continue;
        }
        let Some(image) = source.image else { continue };
        let Some(area) = overlap(image, rect) else {
            continue;
        };
        let pixels = display_pixels(&source, image, area, layer == active, options)?;
        result.draw(
            &pixels,
            (area.x - rect.x) as i64,
            (area.y - rect.y) as i64,
            source.settings.opacity,
        );
    }
    Ok(result)
}

/// コマ `frame` の全レイヤーを重ねた画像（`PaintCanvas.composite`）。
///
/// 書き出し・サムネイルと同じく、初期画像・表示プレビュー・オニオンは掛けない。
pub fn composite(
    document: &Document,
    frame: usize,
    options: &DisplayOptions,
    background: Background,
) -> Result<PremulImage, Error> {
    let sources = Sources::new(document)?;
    composite_with(document, &sources, frame, options, background, None)
}

/// [`composite`] の `rect` の範囲だけを合成する。結果は全体を合成して切り出したものと同じ。
pub fn composite_rect(
    document: &Document,
    frame: usize,
    options: &DisplayOptions,
    background: Background,
    rect: Rect,
) -> Result<PremulImage, Error> {
    check_rect(document, rect)?;
    let sources = Sources::new(document)?;
    composite_with(document, &sources, frame, options, background, Some(rect))
}

/// コマ `frame` のレイヤー `layer` の、書き出しと同じ表示用画像（`_display_layer_image`）。
///
/// 表示しないレイヤー・絵のないレイヤーは None。
pub fn layer_image(
    document: &Document,
    frame: usize,
    layer: usize,
    options: &DisplayOptions,
) -> Result<Option<LayerImage>, Error> {
    let sources = Sources::new(document)?;
    let Some(source) = sources.layer(document, frame, layer) else {
        return Ok(None);
    };
    let Some(image) = source.image.filter(|_| source.settings.visible) else {
        return Ok(None);
    };
    let active = active_layer(document, options) == layer;
    let pixels = display_pixels(&source, image, layers::full_rect(image), active, options)?;
    Ok(Some(LayerImage {
        layer,
        key_frame: source.key_frame,
        opacity: source.settings.opacity,
        x: 0,
        y: 0,
        image: pixels,
    }))
}

fn view_layers_with(
    document: &Document,
    sources: &Sources,
    frame: usize,
    options: &DisplayOptions,
    view: &ViewOptions,
    mapping: &mut MapColorMapping,
    rect: Rect,
) -> Result<Vec<(LayerImage, Vec<u8>)>, Error> {
    let active = active_layer(document, options);
    let mut images = Vec::new();
    for layer in 0..sources.layer_count() {
        let Some(source) = sources.layer(document, frame, layer) else {
            continue;
        };
        if !source.settings.visible {
            continue;
        }
        let Some((pixels, area)) = view_pixels(
            &source,
            rect,
            layer == active,
            options,
            view.show_initial_state,
            &view.preview,
            mapping,
        )?
        else {
            continue;
        };
        let image = pixels.premultiplied()?;
        images.push((
            LayerImage {
                layer,
                key_frame: source.key_frame,
                opacity: source.settings.opacity,
                x: area.x,
                y: area.y,
                image,
            },
            pixels.straight,
        ));
    }
    Ok(images)
}

/// 編集画面に描くレイヤーごとの表示用画像（`draw_frame_direct` の各レイヤー）を下から順に返す。
///
/// 初期画像の表示と表示プレビューを掛ける。縮小・反転はしない（[`display_image_for_size`]）。
/// 隣接色分けの対応 `mapping` は呼び出し側が持ち続ける。
pub fn view_layer_images(
    document: &Document,
    frame: usize,
    options: &DisplayOptions,
    view: &ViewOptions,
    mapping: &mut MapColorMapping,
) -> Result<Vec<LayerImage>, Error> {
    let sources = Sources::new(document)?;
    Ok(view_layers_with(
        document,
        &sources,
        frame,
        options,
        view,
        mapping,
        canvas_rect(document),
    )?
    .into_iter()
    .map(|(image, _)| image)
    .collect())
}

/// [`view_layer_images`] のレイヤー `layer` の分だけを返す。
pub fn view_layer_image(
    document: &Document,
    frame: usize,
    layer: usize,
    options: &DisplayOptions,
    view: &ViewOptions,
    mapping: &mut MapColorMapping,
) -> Result<Option<LayerImage>, Error> {
    Ok(view_layer_images(document, frame, options, view, mapping)?
        .into_iter()
        .find(|image| image.layer == layer))
}

/// 編集画面と同じ表示（初期画像・表示プレビュー）を元の解像度で重ねた画像の `rect`
/// （None なら全体）の範囲。下地は `background`（画面では擬似透明の表示色）。
pub fn view_composite(
    document: &Document,
    frame: usize,
    options: &DisplayOptions,
    view: &ViewOptions,
    mapping: &mut MapColorMapping,
    background: Background,
    rect: Option<Rect>,
) -> Result<PremulImage, Error> {
    let rect = rect.unwrap_or_else(|| canvas_rect(document));
    check_rect(document, rect)?;
    let sources = Sources::new(document)?;
    let mut result = PremulImage::filled(rect.width, rect.height, background.pixel())?;
    for (image, _) in view_layers_with(document, &sources, frame, options, view, mapping, rect)? {
        result.draw(
            &image.image,
            (image.x - rect.x) as i64,
            (image.y - rect.y) as i64,
            image.opacity,
        );
    }
    Ok(result)
}

/// 縮小表示の設定。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct DisplayTarget {
    /// 画面に描く画素の大きさ（[`display_size`] で求める。元の大きさを超えない）。
    pub width: usize,
    /// 画面に描く画素の大きさ。
    pub height: usize,
    /// 縮小のしかた。
    pub mode: ShrinkMode,
    /// 左右反転。
    pub flip_horizontal: bool,
    /// 上下反転。
    pub flip_vertical: bool,
}

/// 編集画面の1コマの表示（`draw_frame_direct` とオニオン）を `target` の大きさで返す。
///
/// 各レイヤーを `_display_image_for_rect` と同じく縮小・反転してから重ね、`onion` が
/// あれば上へオニオンを重ねる（変形なし。[`draw_onions`]）。画面の大きさがこの画像と
/// 同じなら、Qt が画面へ描く画素と同じになる。
#[allow(clippy::too_many_arguments)]
pub fn display_frame(
    document: &Document,
    frame: usize,
    options: &DisplayOptions,
    view: &ViewOptions,
    mapping: &mut MapColorMapping,
    background: Background,
    target: DisplayTarget,
    onion: Option<&OnionSettings>,
) -> Result<PremulImage, Error> {
    let sources = Sources::new(document)?;
    let (width, height) = (target.width.max(1), target.height.max(1));
    let mut result = PremulImage::filled(width, height, background.pixel())?;
    let images = view_layers_with(
        document,
        &sources,
        frame,
        options,
        view,
        mapping,
        canvas_rect(document),
    )?;
    for (layer, straight) in images {
        let image = &layer.image;
        let size = (
            width.min(image.width()).max(1),
            height.min(image.height()).max(1),
        );
        let scaled = display_image_for_size_with(
            image,
            Some(&straight),
            size.0,
            size.1,
            target.mode,
            target.flip_horizontal,
            target.flip_vertical,
        )?;
        // 画面へは drawImage(target_rect, 画像) で描く（1×1 の画像は塗りになる）。
        scale::draw_scaled(&mut result, &scaled, layer.opacity);
    }
    if let Some(onion) = onion {
        draw_onions(
            &mut result,
            document,
            frame,
            options,
            onion,
            target.flip_horizontal,
            target.flip_vertical,
        )?;
    }
    Ok(result)
}

#[cfg(test)]
mod tests;
