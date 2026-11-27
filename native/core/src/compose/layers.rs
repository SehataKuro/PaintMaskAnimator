//! コマの各レイヤーが表示する絵の解決と、レイヤーごとの表示用画像。
//!
//! Python の `PaintCanvas.resolve_key_frame`・`_display_layer_image`（`filtered_layer_image`・
//! `silhouette_layer_image`）・`_model_display_source`・`view_layer_image` に対応する。

use crate::display::{filter_palette_of, Rgb};
use crate::document::model::{Document, TrackId, TrackSettings};
use crate::image::{Rect, RgbaImage};
use crate::Error;

use super::pixel::{premultiply, unpremultiply, PremulImage};
use super::view::{apply_view_preview, MapColorMapping, ViewPreview};
use super::DisplayOptions;

/// コマの1レイヤーが表示する絵（`resolve_key_frame` が指す位置の絵）。
#[derive(Clone, Copy, Debug)]
pub struct FrameLayer<'a> {
    /// レイヤーの表示順の番号（下から 0）。
    pub layer: usize,
    /// 絵を持つコマ（保持セルなら手前の実体のコマ）。
    pub key_frame: usize,
    /// 列の ID。
    pub track: TrackId,
    /// 列の表示設定（表示・不透明度・用紙・下書き・色フィルタ）。
    pub settings: &'a TrackSettings,
    /// 絵の画像。絵がなければ None。
    pub image: Option<&'a RgbaImage>,
    /// 絵の初期画像。
    pub initial_image: Option<&'a RgbaImage>,
}

/// 文書全体の参照元の表（`resolve_sources`）。何コマも引くときに1回だけ作る。
pub(crate) struct Sources {
    width: usize,
    sources: Vec<i64>,
    frames: usize,
}

impl Sources {
    pub(crate) fn new(document: &Document) -> Result<Self, Error> {
        let (width, sources) = document.resolve_sources()?;
        Ok(Self {
            width,
            sources,
            frames: document.frame_order().len(),
        })
    }

    pub(crate) fn frame_count(&self) -> usize {
        self.frames
    }

    pub(crate) fn layer_count(&self) -> usize {
        self.width
    }

    /// `canvas_key_frame.resolve_key_frame` と同じく、コマを範囲内へ寄せて参照元を返す。
    pub(crate) fn key_frame(&self, frame: usize, layer: usize) -> Option<usize> {
        if self.frames == 0 || layer >= self.width {
            return None;
        }
        let frame = frame.min(self.frames - 1);
        let source = self.sources[frame * self.width + layer];
        (source >= 0).then_some(source as usize)
    }

    pub(crate) fn layer<'a>(
        &self,
        document: &'a Document,
        frame: usize,
        layer: usize,
    ) -> Option<FrameLayer<'a>> {
        let key_frame = self.key_frame(frame, layer)?;
        let track = *document.track_order().get(layer)?;
        let frame_id = *document.frame_order().get(key_frame)?;
        let position = document.frame(frame_id)?.positions.get(&track)?;
        let owner = document.track(track)?;
        // Python の Layer._cell と同じく、番号付きの絵は列のバンク、それ以外は位置の局所の絵。
        let cell = if position.references_bank() {
            position
                .number()
                .and_then(|number| owner.cells.get(&number))
        } else {
            position.compatibility.local.as_ref()
        };
        Some(FrameLayer {
            layer,
            key_frame,
            track,
            settings: &owner.settings,
            image: cell.map(|cell| &cell.image),
            initial_image: cell.and_then(|cell| cell.initial_image.as_ref()),
        })
    }
}

/// コマ `frame` の各レイヤーが表示する絵を下から順に返す。表示しない（未使用・空キー）
/// レイヤーは None。コマは範囲内へ寄せる（Python の resolve_key_frame と同じ）。
pub fn frame_layers(
    document: &Document,
    frame: usize,
) -> Result<Vec<Option<FrameLayer<'_>>>, Error> {
    let sources = Sources::new(document)?;
    Ok((0..sources.layer_count())
        .map(|layer| sources.layer(document, frame, layer))
        .collect())
}

/// 使用色の絞り込みと置換（`_palette_filter_key` の中身）。
struct PaletteFilter<'a> {
    visible: Option<&'a [Rgb]>,
    legacy: Option<Rgb>,
    remap: &'a [(Rgb, Rgb)],
}

/// レイヤーに掛ける表示フィルタ。
enum LayerFilter<'a> {
    /// 元の画素のまま。
    Raw,
    /// 使用色の絞り込み・置換と、シルエット。
    Filtered {
        palette: Option<PaletteFilter<'a>>,
        silhouette: bool,
    },
}

fn layer_filter<'a>(
    settings: &TrackSettings,
    active: bool,
    options: &'a DisplayOptions,
) -> LayerFilter<'a> {
    // 下書きは色数削減の対象外で、表示変換を一切掛けない。
    if settings.is_draft {
        return LayerFilter::Raw;
    }
    let visible = if active {
        options.visible_colors.as_deref()
    } else {
        None
    };
    let legacy = if settings.color_filter_enabled {
        settings.color_filter_rgb.map(|[r, g, b]| (r, g, b))
    } else {
        None
    };
    let remap: &[(Rgb, Rgb)] = if active { &options.color_remap } else { &[] };
    let palette =
        (visible.is_some() || legacy.is_some() || !remap.is_empty()).then_some(PaletteFilter {
            visible,
            legacy,
            remap,
        });
    let silhouette = options.silhouette && !settings.is_paper;
    if palette.is_none() && !silhouette {
        LayerFilter::Raw
    } else {
        LayerFilter::Filtered {
            palette,
            silhouette,
        }
    }
}

/// 表示用画像を Qt がストレートへ戻したときの画素（RGBA8888）と大きさ。
///
/// Qt は乗算済みの表示画像をストレートへ戻してから色で判定する。文書の画素は Qt で
/// 正規化済み（乗算済みにして戻した値）なので、そのまま使えば Qt の値と同じになる。
/// 置換などで色を変えた画素だけは、乗算済みにして戻した値（スカラー版）にする。
pub(crate) struct LayerPixels {
    pub(crate) width: usize,
    pub(crate) height: usize,
    pub(crate) straight: Vec<u8>,
}

impl LayerPixels {
    fn from_image(image: &RgbaImage, rect: Rect) -> Result<Self, Error> {
        let mut straight = image.copy_rgba_rect(rect)?;
        for pixel in straight.chunks_exact_mut(4) {
            if pixel[3] == 0 {
                pixel.fill(0);
            }
        }
        Ok(Self {
            width: rect.width,
            height: rect.height,
            straight,
        })
    }

    /// 乗算済みの画素（Qt の ARGB32_Premultiplied と同じ値）。
    pub(crate) fn premultiplied(&self) -> Result<PremulImage, Error> {
        PremulImage::from_straight(self.width, self.height, &self.straight)
    }

    /// 色を変えた画素を、乗算済みにしてストレートへ戻した値にそろえる。
    fn normalize_changed(&mut self, before: &[u8]) {
        for (pixel, old) in self
            .straight
            .chunks_exact_mut(4)
            .zip(before.chunks_exact(4))
        {
            if pixel[3] == 0 {
                pixel.fill(0);
            } else if pixel != old {
                let rounded = unpremultiply(premultiply([pixel[0], pixel[1], pixel[2], pixel[3]]));
                pixel.copy_from_slice(&rounded);
            }
        }
    }

    fn crop(self, area: Rect, from: Rect) -> Self {
        if area == from {
            return self;
        }
        let row = area.width * 4;
        let mut straight = Vec::with_capacity(area.height * row);
        for y in area.y..area.y + area.height {
            let start = ((y - from.y) * self.width + (area.x - from.x)) * 4;
            straight.extend_from_slice(&self.straight[start..start + row]);
        }
        Self {
            width: area.width,
            height: area.height,
            straight,
        }
    }
}

/// 画像の `rect` の範囲に表示フィルタを掛ける（`filtered_layer_image`・`silhouette_layer_image`）。
fn filtered_pixels(
    image: &RgbaImage,
    rect: Rect,
    filter: &LayerFilter,
) -> Result<LayerPixels, Error> {
    let mut pixels = LayerPixels::from_image(image, rect)?;
    let LayerFilter::Filtered {
        palette,
        silhouette,
    } = filter
    else {
        return Ok(pixels);
    };
    if let Some(palette) = palette {
        let before = std::mem::take(&mut pixels.straight);
        pixels.straight = filter_palette_of(
            &before,
            palette.visible.map(<[Rgb]>::to_vec),
            palette.legacy,
            palette.remap.to_vec(),
        );
        pixels.normalize_changed(&before);
    }
    if *silhouette {
        // 不透明な画素の RGB を黒にする（透明な画素はもともと 0）。
        for pixel in pixels.straight.chunks_exact_mut(4) {
            pixel[..3].fill(0);
        }
    }
    Ok(pixels)
}

/// 画像の全体を表す矩形。
pub(crate) fn full_rect(image: &RgbaImage) -> Rect {
    Rect {
        x: 0,
        y: 0,
        width: image.width(),
        height: image.height(),
    }
}

/// キャンバス上の `rect` と画像（原点に置く）の重なり。重ならなければ None。
pub(crate) fn overlap(image: &RgbaImage, rect: Rect) -> Option<Rect> {
    let right = (rect.x + rect.width).min(image.width());
    let bottom = (rect.y + rect.height).min(image.height());
    (rect.x < right && rect.y < bottom).then(|| Rect {
        x: rect.x,
        y: rect.y,
        width: right - rect.x,
        height: bottom - rect.y,
    })
}

/// 書き出し・サムネイル・オニオンに使う表示用画像（`_display_layer_image`）の `rect` の範囲。
///
/// `rect` は画像の範囲内であること。
pub(crate) fn display_pixels(
    layer: &FrameLayer,
    image: &RgbaImage,
    rect: Rect,
    active: bool,
    options: &DisplayOptions,
) -> Result<PremulImage, Error> {
    filtered_pixels(image, rect, &layer_filter(layer.settings, active, options))?.premultiplied()
}

/// 画面の編集表示に使う表示用画像（`_model_display_source` と `view_layer_image`）の
/// `rect` の範囲。初期画像の表示と表示プレビューを掛ける。戻り値は画像の範囲と重なる
/// 部分の画素と、その矩形。描く絵がなければ None。
#[allow(clippy::too_many_arguments)]
pub(crate) fn view_pixels(
    layer: &FrameLayer,
    rect: Rect,
    active: bool,
    options: &DisplayOptions,
    show_initial_state: bool,
    preview: &ViewPreview,
    mapping: &mut MapColorMapping,
) -> Result<Option<(LayerPixels, Rect)>, Error> {
    let initial = layer.initial_image.filter(|_| show_initial_state);
    let Some(image) = initial.or(layer.image) else {
        return Ok(None);
    };
    let Some(area) = overlap(image, rect) else {
        return Ok(None);
    };
    let settings = layer.settings;
    let map_colors = preview.map_colors && active && !settings.is_paper;
    let previewed =
        preview.active() && !settings.is_draft && (map_colors || preview.adjusts_pixels());
    // 隣接色分けは領域をまたぐので、絵の全体で計算してから切り出す。
    let source_rect = if previewed && map_colors {
        full_rect(image)
    } else {
        area
    };
    let mut pixels = if initial.is_some() {
        // 初期画像は表示フィルタを通さない。
        LayerPixels::from_image(image, source_rect)?
    } else {
        filtered_pixels(image, source_rect, &layer_filter(settings, active, options))?
    };
    if previewed {
        let before = pixels.straight.clone();
        apply_view_preview(
            &mut pixels.straight,
            pixels.width,
            pixels.height,
            preview,
            map_colors,
            mapping,
        );
        pixels.normalize_changed(&before);
    }
    Ok(Some((pixels.crop(area, source_rect), area)))
}
