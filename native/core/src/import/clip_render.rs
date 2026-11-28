//! CLIP STUDIO のセル（レイヤー1枚かフォルダーの木）を1枚の絵に合成する
//! （`clip_animation.py` の `_CelRenderer`・`_composite`・`_blend_colors`・`_apply_layer_color`）。
//!
//! Qt 版は、通常の合成とクリッピングだけの木を QPainter（乗算済みの 8bit）で描き、
//! 合成モード・レイヤーマスク・通過のある木を NumPy の float32 で描く。ここも同じ2つの道を
//! 同じ式・同じ順の演算で持つ：
//!
//! - QImage は [`PremulImage`]（Qt の ARGB32_Premultiplied と同じ値）。`qimage_rgba_array` は
//!   [`qt_unpremultiply`]、`rgba_array_to_qimage` は [`PremulImage::from_straight`]。
//! - NumPy の配列は f32 の RGBA（乗算済み）。Python の小数（不透明度など）は NumPy 2 の
//!   規則どおり f32 に丸めてから掛ける。`np.minimum` などの NaN の伝わり方も合わせる。

use std::collections::{BTreeSet, HashMap};

use super::clip::{ClipError, ClipLayerRow, ClipRaster, RasterSource};
use crate::compose::painter::{draw_image_at, Device, Transform};
use crate::compose::pixel::{const_alpha, PremulImage};
use crate::export::thumbnail::qt_unpremultiply;

// CLIP STUDIO の `LayerComposite`（PSD の合成モードの表と同じ番号）。
pub const BLEND_NORMAL: i64 = 0;
pub const BLEND_DARKEN: i64 = 1;
pub const BLEND_MULTIPLY: i64 = 2;
pub const BLEND_COLOR_BURN: i64 = 3;
pub const BLEND_LINEAR_BURN: i64 = 4;
pub const BLEND_SUBTRACT: i64 = 5;
pub const BLEND_DARKER_COLOR: i64 = 6;
pub const BLEND_LIGHTEN: i64 = 7;
pub const BLEND_SCREEN: i64 = 8;
pub const BLEND_COLOR_DODGE: i64 = 9;
pub const BLEND_GLOW_DODGE: i64 = 10;
pub const BLEND_ADD: i64 = 11;
pub const BLEND_ADD_GLOW: i64 = 12;
pub const BLEND_LIGHTER_COLOR: i64 = 13;
pub const BLEND_OVERLAY: i64 = 14;
pub const BLEND_SOFT_LIGHT: i64 = 15;
pub const BLEND_HARD_LIGHT: i64 = 16;
pub const BLEND_VIVID_LIGHT: i64 = 17;
pub const BLEND_LINEAR_LIGHT: i64 = 18;
pub const BLEND_PIN_LIGHT: i64 = 19;
pub const BLEND_HARD_MIX: i64 = 20;
pub const BLEND_DIFFERENCE: i64 = 21;
pub const BLEND_EXCLUSION: i64 = 22;
pub const BLEND_HUE: i64 = 23;
pub const BLEND_SATURATION: i64 = 24;
pub const BLEND_COLOR: i64 = 25;
pub const BLEND_LUMINOSITY: i64 = 26;
pub const BLEND_PASS_THROUGH: i64 = 30;
pub const BLEND_DIVIDE: i64 = 36;

// ---------------------------------------------------------------------------
// レイヤーの色（_csp_color_byte・_single_channel_layer_color・_apply_layer_color）
// ---------------------------------------------------------------------------

/// `_csp_color_byte`：255 までは値そのまま、それより大きければ上位 8bit。
fn csp_color_byte(value: i64) -> u8 {
    let component = value.max(0);
    if component <= 255 {
        component as u8
    } else {
        (component >> 24).min(255) as u8
    }
}

fn color_of(layer: &ClipLayerRow, names: [&str; 3], default: i64) -> [u8; 3] {
    names.map(|name| csp_color_byte(layer.get(name).unwrap_or(default)))
}

const MAIN: [&str; 3] = [
    "DrawColorMainRed",
    "DrawColorMainGreen",
    "DrawColorMainBlue",
];
const SUB: [&str; 3] = ["DrawColorSubRed", "DrawColorSubGreen", "DrawColorSubBlue"];
const PALETTE: [&str; 3] = ["LayerPaletteRed", "LayerPaletteGreen", "LayerPaletteBlue"];

/// `_single_channel_layer_color`：グレー・アルファだけのラスターに付ける色。
fn single_channel_layer_color(layer: &ClipLayerRow) -> [u8; 3] {
    if layer.number("DrawColorEnable") != 0 {
        color_of(layer, MAIN, 0)
    } else if layer.number("LayerUsePaletteColor") != 0 {
        color_of(layer, PALETTE, 0)
    } else {
        [0, 0, 0]
    }
}

/// `_apply_layer_color`：レイヤーカラー（黒をメイン色・白をサブ色へ、アルファはそのまま）。
pub fn apply_layer_color(image: PremulImage, layer: &ClipLayerRow) -> PremulImage {
    if layer.number("DrawColorEnable") == 0 {
        return image;
    }
    let main = color_of(layer, MAIN, 0);
    if image.pixels().chunks_exact(4).all(|p| p[..3] == [0, 0, 0]) {
        // 線画の控えは黒＋アルファのことが多い。SourceIn で色を塗る（アルファを保つ）。
        let mut result = image;
        for pixel in result.pixels_mut().chunks_exact_mut(4) {
            let alpha = pixel[3];
            pixel[0] = mul_255(main[0], alpha);
            pixel[1] = mul_255(main[1], alpha);
            pixel[2] = mul_255(main[2], alpha);
        }
        return result;
    }
    let sub = color_of(layer, SUB, 255);
    let mut palette = [[0u8; 3]; 256];
    for (level, entry) in palette.iter_mut().enumerate() {
        let level = level as u32;
        for channel in 0..3 {
            entry[channel] =
                ((u32::from(main[channel]) * (255 - level) + u32::from(sub[channel]) * level + 127)
                    / 255) as u8;
        }
    }
    let mut rgba = qt_unpremultiply(&image);
    for pixel in rgba.chunks_exact_mut(4) {
        let gray = (u32::from(pixel[0]) * 299
            + u32::from(pixel[1]) * 587
            + u32::from(pixel[2]) * 114
            + 500)
            / 1000;
        pixel[..3].copy_from_slice(&palette[gray as usize]);
    }
    PremulImage::from_straight(image.width(), image.height(), &rgba).expect("同じ大きさの画素")
}

/// Qt の BYTE_MUL の1チャンネル分。
fn mul_255(c: u8, a: u8) -> u8 {
    let t = u32::from(c) * u32::from(a);
    ((t + (t >> 8) + 0x80) >> 8) as u8
}

/// `_native_raster`：展開したラスターを QImage（乗算済み）にする。
fn native_raster(raster: &ClipRaster, color: [u8; 3]) -> Result<PremulImage, ClipError> {
    let (width, height) = (raster.width as usize, raster.height as usize);
    let channels = usize::from(raster.channels);
    let count = width * height;
    if raster.pixels.len() != count * channels || !matches!(channels, 1 | 2 | 4) {
        return Err(ClipError(
            "cannot reshape array of size into shape".to_string(),
        ));
    }
    let mut rgba = vec![0u8; count * 4];
    for (index, out) in rgba.chunks_exact_mut(4).enumerate() {
        let pixel = &raster.pixels[index * channels..(index + 1) * channels];
        if channels == 4 {
            out.copy_from_slice(pixel);
        } else if raster.alpha_only {
            out[..3].copy_from_slice(&color);
            out[3] = pixel[0];
        } else {
            out[..3].fill(pixel[0]);
            out[3] = if channels == 2 { pixel[1] } else { 255 };
        }
    }
    PremulImage::from_straight(width, height, &rgba).map_err(|e| ClipError(e.to_string()))
}

// ---------------------------------------------------------------------------
// float32 の合成（_blend_colors・_composite）
// ---------------------------------------------------------------------------

/// `np.minimum`（NaN を伝える）。
fn np_min(a: f32, b: f32) -> f32 {
    if a.is_nan() || b.is_nan() {
        f32::NAN
    } else if a < b {
        a
    } else {
        b
    }
}

/// `np.maximum`（NaN を伝える）。
fn np_max(a: f32, b: f32) -> f32 {
    if a.is_nan() || b.is_nan() {
        f32::NAN
    } else if a > b {
        a
    } else {
        b
    }
}

/// `np.clip(x, low, high)`。
fn np_clip(x: f32, low: f32, high: f32) -> f32 {
    np_min(np_max(x, low), high)
}

/// `_safe_divide`。
fn safe_divide(numerator: f32, denominator: f32, fallback: f32) -> f32 {
    if denominator > 0.0 {
        numerator / denominator
    } else {
        fallback
    }
}

type Rgb = [f32; 3];

fn luminosity(c: Rgb) -> f32 {
    c[0] * 0.3 + c[1] * 0.59 + c[2] * 0.11
}

fn channel_min(c: Rgb) -> f32 {
    // ndarray.min は NaN を伝える。
    np_min(np_min(c[0], c[1]), c[2])
}

fn channel_max(c: Rgb) -> f32 {
    np_max(np_max(c[0], c[1]), c[2])
}

fn set_luminosity(color: Rgb, lum: f32) -> Rgb {
    let delta = lum - luminosity(color);
    let mut color = color.map(|c| c + delta);
    let lum = luminosity(color);
    let low = channel_min(color);
    let high = channel_max(color);
    if low < 0.0 {
        let factor = safe_divide(lum, lum - low, 0.0);
        color = color.map(|c| lum + (c - lum) * factor);
    }
    if high > 1.0 {
        let factor = safe_divide(1.0 - lum, high - lum, 0.0);
        color = color.map(|c| lum + (c - lum) * factor);
    }
    color
}

fn saturation(c: Rgb) -> f32 {
    channel_max(c) - channel_min(c)
}

fn set_saturation(color: Rgb, sat: f32) -> Rgb {
    let low = channel_min(color);
    let denominator = saturation(color);
    color.map(|c| safe_divide((c - low) * sat, denominator, 0.0))
}

fn color_burn(b: f32, s: f32) -> f32 {
    if b >= 1.0 {
        1.0
    } else {
        1.0 - np_min(1.0, safe_divide(1.0 - b, s, f32::INFINITY))
    }
}

fn color_dodge(b: f32, s: f32) -> f32 {
    if b <= 0.0 {
        0.0
    } else {
        np_min(1.0, safe_divide(b, 1.0 - s, f32::INFINITY))
    }
}

fn hard_light(b: f32, s: f32) -> f32 {
    let doubled = s * 2.0;
    if s <= 0.5 {
        b * doubled
    } else {
        b + (doubled - 1.0) - b * (doubled - 1.0)
    }
}

fn soft_light(b: f32, s: f32) -> f32 {
    let darken = if b <= 0.25 {
        ((16.0 * b - 12.0) * b + 4.0) * b
    } else {
        b.sqrt()
    };
    if s <= 0.5 {
        b - (1.0 - 2.0 * s) * b * (1.0 - b)
    } else {
        b + (2.0 * s - 1.0) * (darken - b)
    }
}

fn per_channel(b: Rgb, s: Rgb, f: impl Fn(f32, f32) -> f32) -> Rgb {
    [f(b[0], s[0]), f(b[1], s[1]), f(b[2], s[2])]
}

/// `_blend_colors`：ストレートの RGB（0〜1）の B(Cb, Cs)。
pub fn blend_colors(mode: i64, b: Rgb, s: Rgb) -> Rgb {
    match mode {
        BLEND_DARKEN => per_channel(b, s, np_min),
        BLEND_MULTIPLY => per_channel(b, s, |b, s| b * s),
        BLEND_COLOR_BURN => per_channel(b, s, color_burn),
        BLEND_LINEAR_BURN => per_channel(b, s, |b, s| np_max(0.0, b + s - 1.0)),
        BLEND_SUBTRACT => per_channel(b, s, |b, s| np_max(0.0, b - s)),
        BLEND_DARKER_COLOR => {
            if luminosity(s) < luminosity(b) {
                s
            } else {
                b
            }
        }
        BLEND_LIGHTEN => per_channel(b, s, np_max),
        BLEND_SCREEN => per_channel(b, s, |b, s| b + s - b * s),
        BLEND_COLOR_DODGE | BLEND_GLOW_DODGE => per_channel(b, s, color_dodge),
        BLEND_ADD | BLEND_ADD_GLOW => per_channel(b, s, |b, s| np_min(1.0, b + s)),
        BLEND_LIGHTER_COLOR => {
            if luminosity(s) > luminosity(b) {
                s
            } else {
                b
            }
        }
        BLEND_OVERLAY => per_channel(b, s, |b, s| hard_light(s, b)),
        BLEND_SOFT_LIGHT => per_channel(b, s, soft_light),
        BLEND_HARD_LIGHT => per_channel(b, s, hard_light),
        BLEND_VIVID_LIGHT => per_channel(b, s, |b, s| {
            if s <= 0.5 {
                color_burn(b, s * 2.0)
            } else {
                color_dodge(b, s * 2.0 - 1.0)
            }
        }),
        BLEND_LINEAR_LIGHT => per_channel(b, s, |b, s| np_clip(b + s * 2.0 - 1.0, 0.0, 1.0)),
        BLEND_PIN_LIGHT => per_channel(b, s, |b, s| {
            if s <= 0.5 {
                np_min(b, s * 2.0)
            } else {
                np_max(b, s * 2.0 - 1.0)
            }
        }),
        BLEND_HARD_MIX => per_channel(b, s, |b, s| if b + s >= 1.0 { 1.0 } else { 0.0 }),
        BLEND_DIFFERENCE => per_channel(b, s, |b, s| (b - s).abs()),
        BLEND_EXCLUSION => per_channel(b, s, |b, s| b + s - 2.0 * b * s),
        BLEND_HUE => set_luminosity(set_saturation(s, saturation(b)), luminosity(b)),
        BLEND_SATURATION => set_luminosity(set_saturation(b, saturation(s)), luminosity(b)),
        BLEND_COLOR => set_luminosity(s, luminosity(b)),
        BLEND_LUMINOSITY => set_luminosity(b, luminosity(s)),
        BLEND_DIVIDE => per_channel(b, s, |b, s| {
            if s > 0.0 {
                np_min(1.0, safe_divide(b, s, 1.0))
            } else if b > 0.0 {
                1.0
            } else {
                0.0
            }
        }),
        // 通常・通過・知らない値はふつうの「重ね」。
        _ => s,
    }
}

/// `np.nan_to_num(x, nan=0, posinf=1, neginf=0)`。
fn nan_to_num(x: f32) -> f32 {
    if x.is_nan() {
        0.0
    } else if x == f32::INFINITY {
        1.0
    } else if x == f32::NEG_INFINITY {
        0.0
    } else {
        x
    }
}

/// 乗算済み RGBA（f32）の画像。
#[derive(Clone, Debug, PartialEq)]
pub struct Pixels {
    pub width: usize,
    pub height: usize,
    pub data: Vec<[f32; 4]>,
}

impl Pixels {
    pub fn blank(width: usize, height: usize) -> Self {
        Self {
            width,
            height,
            data: vec![[0.0; 4]; width * height],
        }
    }

    /// `pixels * scalar`（Python の小数は f32 に丸めて掛ける）。
    fn scaled(mut self, factor: f64) -> Self {
        let factor = factor as f32;
        for pixel in &mut self.data {
            *pixel = pixel.map(|c| c * factor);
        }
        self
    }
}

/// `_composite`：乗算済みの `source` を `backdrop` に合成する（W3C の合成の式）。
/// `atop` は下のレイヤーのアルファを保つ（クリッピング）。
pub fn composite(backdrop: &Pixels, source: &Pixels, mode: i64, atop: bool) -> Pixels {
    let data = backdrop
        .data
        .iter()
        .zip(&source.data)
        .map(|(b, s)| composite_pixel(*b, *s, mode, atop))
        .collect();
    Pixels {
        width: backdrop.width,
        height: backdrop.height,
        data,
    }
}

fn composite_pixel(b: [f32; 4], s: [f32; 4], mode: i64, atop: bool) -> [f32; 4] {
    let (ba, sa) = (b[3], s[3]);
    let mixed: Rgb = if mode == BLEND_NORMAL || mode == BLEND_PASS_THROUGH {
        [s[0] * ba, s[1] * ba, s[2] * ba]
    } else {
        let straight_b = [0, 1, 2].map(|i| safe_divide(b[i], ba, 0.0));
        let straight_s = [0, 1, 2].map(|i| safe_divide(s[i], sa, 0.0));
        let blended = blend_colors(mode, straight_b, straight_s).map(nan_to_num);
        let weight = sa * ba;
        blended.map(|c| weight * np_clip(c, 0.0, 1.0))
    };
    let (color, alpha): (Rgb, f32) = if atop {
        ([0, 1, 2].map(|i| mixed[i] + b[i] * (1.0 - sa)), ba)
    } else {
        (
            [0, 1, 2].map(|i| s[i] * (1.0 - ba) + b[i] * (1.0 - sa) + mixed[i]),
            sa + ba * (1.0 - sa),
        )
    };
    [
        np_clip(color[0], 0.0, alpha),
        np_clip(color[1], 0.0, alpha),
        np_clip(color[2], 0.0, alpha),
        alpha,
    ]
}

/// `_image_to_premultiplied`。
pub fn image_to_premultiplied(image: &PremulImage) -> Pixels {
    let rgba = qt_unpremultiply(image);
    let data = rgba
        .chunks_exact(4)
        .map(|p| {
            let mut v = [0, 1, 2, 3].map(|i| f32::from(p[i]) / 255.0);
            for i in 0..3 {
                v[i] *= v[3];
            }
            v
        })
        .collect();
    Pixels {
        width: image.width(),
        height: image.height(),
        data,
    }
}

/// `_premultiplied_to_image`。
pub fn premultiplied_to_image(pixels: &Pixels) -> PremulImage {
    let mut rgba = Vec::with_capacity(pixels.data.len() * 4);
    for p in &pixels.data {
        let alpha = p[3];
        let straight = [
            safe_divide(p[0], alpha, 0.0),
            safe_divide(p[1], alpha, 0.0),
            safe_divide(p[2], alpha, 0.0),
            alpha,
        ];
        for value in straight {
            // np.clip(np.rint(x * 255), 0, 255).astype(np.uint8)
            let v = np_clip((value * 255.0).round_ties_even(), 0.0, 255.0);
            rgba.push(if v.is_nan() { 0 } else { v as u8 });
        }
    }
    PremulImage::from_straight(pixels.width, pixels.height, &rgba).expect("同じ大きさの画素")
}

/// `_shift_pixels`。
fn shift_pixels(pixels: Pixels, dx: i64, dy: i64) -> Pixels {
    if dx == 0 && dy == 0 {
        return pixels;
    }
    let (width, height) = (pixels.width as i64, pixels.height as i64);
    let mut result = Pixels::blank(pixels.width, pixels.height);
    if dx.abs() >= width || dy.abs() >= height {
        return result;
    }
    for y in dy.max(0)..height + dy.min(0) {
        for x in dx.max(0)..width + dx.min(0) {
            result.data[(y * width + x) as usize] =
                pixels.data[((y - dy) * width + x - dx) as usize];
        }
    }
    result
}

// ---------------------------------------------------------------------------
// QPainter の描き方（_qt_draw）
// ---------------------------------------------------------------------------

/// `QPainter::drawImage(QPoint(x, y), source)`（ソースオーバー、不透明度 `opacity`）。
fn draw_over(target: &mut PremulImage, source: &PremulImage, opacity: f64, x: i64, y: i64) {
    let mut device = Device::new(target);
    draw_image_at(
        &mut device,
        &Transform::identity(),
        x as f64,
        y as f64,
        source,
        opacity,
    );
}

/// `CompositionMode_SourceAtop` で同じ大きさの画像を (0, 0) に描く（comp_func_SourceAtop）。
fn draw_atop(target: &mut PremulImage, source: &PremulImage, opacity: f64) {
    let alpha = const_alpha(opacity);
    let width = target.width().min(source.width());
    let height = target.height().min(source.height());
    let (tw, sw) = (target.width(), source.width());
    let src = source.pixels();
    let dst = target.pixels_mut();
    for y in 0..height {
        for x in 0..width {
            let s = &src[(y * sw + x) * 4..(y * sw + x) * 4 + 4];
            let s: [u8; 4] = if alpha == 255 {
                [s[0], s[1], s[2], s[3]]
            } else {
                [0, 1, 2, 3].map(|i| mul_255(s[i], alpha))
            };
            let d = &mut dst[(y * tw + x) * 4..(y * tw + x) * 4 + 4];
            let (da, inverse) = (u32::from(d[3]), 255 - u32::from(s[3]));
            for i in 0..4 {
                let v = u32::from(s[i]) * da + u32::from(d[i]) * inverse;
                d[i] = ((v + ((v >> 8) & 0xff) + 0x80) >> 8) as u8;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// セルの合成（_CelRenderer）
// ---------------------------------------------------------------------------

/// 1セル（レイヤー1枚かフォルダーの木）を1枚の絵にする。
///
/// PMA はセルごとに平らな絵を1枚持つので、フォルダーの構造・合成モード・クリッピング・
/// レイヤーマスクはここで解いてしまう。
pub struct CelRenderer<'a> {
    source: &'a mut dyn RasterSource,
    layers: &'a HashMap<i64, ClipLayerRow>,
    width: usize,
    height: usize,
}

const CYCLE: &str = "セル内のレイヤー構造が循環しています。";

fn visible(layer: &ClipLayerRow) -> bool {
    layer.number_or("LayerVisibility", 1) & 1 != 0
}

fn opacity(layer: &ClipLayerRow) -> f64 {
    (layer.number_or("LayerOpacity", 256) as f64 / 256.0).clamp(0.0, 1.0)
}

fn is_folder(layer: &ClipLayerRow) -> bool {
    layer.number("LayerFirstChildIndex") != 0 || layer.number("LayerFolder") != 0
}

fn has_mask(layer: &ClipLayerRow) -> bool {
    layer.number("LayerLayerMaskMipmap") != 0 && layer.number_or("LayerVisibility", 1) & 2 != 0
}

/// 下のレイヤーへのクリッピングを組にする（下から上へ：基のレイヤーとクリップするレイヤー）。
fn clip_groups<'b>(
    children: &[&'b ClipLayerRow],
) -> Vec<(&'b ClipLayerRow, Vec<&'b ClipLayerRow>)> {
    let mut groups: Vec<(&ClipLayerRow, Vec<&ClipLayerRow>)> = Vec::new();
    for &child in children.iter().rev() {
        match groups.last_mut() {
            Some(last) if child.number("LayerClip") != 0 => last.1.push(child),
            _ => groups.push((child, Vec::new())),
        }
    }
    groups
}

type Stack = BTreeSet<i64>;

impl<'a> CelRenderer<'a> {
    pub fn new(
        source: &'a mut dyn RasterSource,
        layers: &'a HashMap<i64, ClipLayerRow>,
        width: usize,
        height: usize,
    ) -> Self {
        Self {
            source,
            layers,
            width,
            height,
        }
    }

    fn blank_image(&self) -> PremulImage {
        PremulImage::transparent(self.width, self.height).expect("キャンバスの大きさ")
    }

    /// セルを合成する（不透明度まで）。セル自身の合成モード・クリッピング・表示は使わない
    /// （空のキャンバスに描くので相手がなく、表示は CLIP の「今のコマ」の意味しかない）。
    pub fn render(&mut self, cel: &ClipLayerRow) -> Result<PremulImage, ClipError> {
        let stack = Stack::new();
        if self.can_render_qt(cel, &stack)? {
            let image = self.qt_content(cel, &stack)?;
            if opacity(cel) == 1.0 {
                return Ok(image);
            }
            let mut result = self.blank_image();
            draw_over(&mut result, &image, opacity(cel), 0, 0);
            return Ok(result);
        }
        let pixels = self.content(cel, &stack)?.scaled(opacity(cel));
        Ok(premultiplied_to_image(&pixels))
    }

    fn children(
        &self,
        layer: &ClipLayerRow,
        stack: &Stack,
    ) -> Result<(Vec<&'a ClipLayerRow>, Stack), ClipError> {
        if stack.contains(&layer.id) {
            return Err(ClipError(CYCLE.into()));
        }
        let layers = self.layers;
        let children = layer
            .children
            .iter()
            .rev()
            .map(|id| layers.get(id).ok_or_else(|| ClipError(id.to_string())))
            .collect::<Result<Vec<_>, _>>()?;
        let mut next = stack.clone();
        next.insert(layer.id);
        Ok((children, next))
    }

    /// 通常の合成とクリッピングだけなら Qt の乗算済みの画素のまま描ける。
    fn can_render_qt(&self, layer: &ClipLayerRow, stack: &Stack) -> Result<bool, ClipError> {
        if has_mask(layer) {
            return Ok(false);
        }
        if is_folder(layer) {
            let (children, child_stack) = self.children(layer, stack)?;
            for child in children {
                if !visible(child) {
                    continue;
                }
                if child.number("LayerComposite") != BLEND_NORMAL
                    || !self.can_render_qt(child, &child_stack)?
                {
                    return Ok(false);
                }
            }
        }
        Ok(true)
    }

    /// `_render_cached_layer`：レイヤーの表示合成画像をキャンバスに置く（不透明度の前）。
    fn render_cached_layer(&mut self, layer: &ClipLayerRow) -> Result<PremulImage, ClipError> {
        let mipmap = layer.number("LayerRenderMipmap");
        if mipmap == 0 {
            return Err(ClipError(format!(
                "セル「{}」の表示合成画像が.clip内にありません。",
                layer.name
            )));
        }
        let color = if layer.number("DrawColorEnable") != 0 {
            [0, 0, 0]
        } else {
            single_channel_layer_color(layer)
        };
        let raster = self.source.raster(mipmap)?;
        let raster = apply_layer_color(native_raster(&raster, color)?, layer);
        let mut result = self.blank_image();
        let x = layer.number("LayerOffsetX") + layer.number("LayerRenderOffscrOffsetX");
        let y = layer.number("LayerOffsetY") + layer.number("LayerRenderOffscrOffsetY");
        draw_over(&mut result, &raster, 1.0, x, y);
        Ok(result)
    }

    fn qt_content(
        &mut self,
        layer: &ClipLayerRow,
        stack: &Stack,
    ) -> Result<PremulImage, ClipError> {
        if !is_folder(layer) {
            return self.render_cached_layer(layer);
        }
        let (children, child_stack) = self.children(layer, stack)?;
        let mut result = self.blank_image();
        for (base, clipped) in clip_groups(&children) {
            if !visible(base) {
                continue;
            }
            let mut group = self.qt_content(base, &child_stack)?;
            for child in clipped {
                if visible(child) {
                    let image = self.qt_content(child, &child_stack)?;
                    draw_atop(&mut group, &image, opacity(child));
                }
            }
            draw_over(&mut result, &group, opacity(base), 0, 0);
        }
        let (x, y) = (layer.number("LayerOffsetX"), layer.number("LayerOffsetY"));
        if x != 0 || y != 0 {
            let mut shifted = self.blank_image();
            draw_over(&mut shifted, &result, 1.0, x, y);
            result = shifted;
        }
        Ok(apply_layer_color(result, layer))
    }

    /// `_content`：レイヤー1枚の画素（マスクまで、不透明度の前）。
    fn content(&mut self, layer: &ClipLayerRow, stack: &Stack) -> Result<Pixels, ClipError> {
        let folder = is_folder(layer);
        let mut pixels = if folder {
            // CLIP STUDIO はフォルダーの控えを省いたり一部だけにしたりするので、
            // フォルダーはいつも子から組み直す。
            let (children, child_stack) = self.children(layer, stack)?;
            let blank = Pixels::blank(self.width, self.height);
            shift_pixels(
                self.composite_children(&children, blank, &child_stack)?,
                layer.number("LayerOffsetX"),
                layer.number("LayerOffsetY"),
            )
        } else {
            image_to_premultiplied(&self.render_cached_layer(layer)?)
        };
        let mask = self.mask(layer)?;
        if folder && layer.number("DrawColorEnable") != 0 {
            pixels =
                image_to_premultiplied(&apply_layer_color(premultiplied_to_image(&pixels), layer));
        }
        if let Some(mask) = mask {
            for (pixel, m) in pixels.data.iter_mut().zip(&mask) {
                *pixel = pixel.map(|c| c * m);
            }
        }
        Ok(pixels)
    }

    fn composite_children(
        &mut self,
        children: &[&'a ClipLayerRow],
        backdrop: Pixels,
        stack: &Stack,
    ) -> Result<Pixels, ClipError> {
        // 子は上から下の順。クリップするレイヤーはすぐ下のクリップしないレイヤーに付き、
        // その組を先に合わせてから基のレイヤーのモード・不透明度で重ねる。
        let mut result = backdrop;
        for (base, clipped) in clip_groups(children) {
            if !visible(base) {
                continue;
            }
            let mode = base.number("LayerComposite");
            if mode == BLEND_PASS_THROUGH && clipped.is_empty() && is_folder(base) {
                result = self.pass_through(base, result, stack)?;
                continue;
            }
            let mut group = self.content(base, stack)?;
            for layer in clipped {
                if !visible(layer) {
                    continue;
                }
                let source = self.content(layer, stack)?.scaled(opacity(layer));
                group = composite(&group, &source, layer.number("LayerComposite"), true);
            }
            result = composite(&result, &group.scaled(opacity(base)), mode, false);
        }
        Ok(result)
    }

    /// 通過のフォルダー：子を下の絵へそのまま重ねる。
    fn pass_through(
        &mut self,
        folder: &ClipLayerRow,
        backdrop: Pixels,
        stack: &Stack,
    ) -> Result<Pixels, ClipError> {
        let (children, child_stack) = self.children(folder, stack)?;
        let mixed = self.composite_children(&children, backdrop.clone(), &child_stack)?;
        let weight = opacity(folder);
        let mask = self.mask(folder)?;
        if mask.is_none() && weight >= 1.0 {
            return Ok(mixed);
        }
        let weight32 = weight as f32;
        let mut result = backdrop;
        for (index, (out, m)) in result.data.iter_mut().zip(&mixed.data).enumerate() {
            let amount = match &mask {
                None => weight32,
                Some(mask) => mask[index] * weight32,
            };
            *out = [0, 1, 2, 3].map(|i| out[i] + (m[i] - out[i]) * amount);
        }
        Ok(result)
    }

    /// `_mask`：キャンバス上のレイヤーマスク（1 が見える）。有効でなければ None。
    fn mask(&mut self, layer: &ClipLayerRow) -> Result<Option<Vec<f32>>, ClipError> {
        let mipmap = layer.number("LayerLayerMaskMipmap");
        if mipmap == 0 || layer.number_or("LayerVisibility", 1) & 2 == 0 {
            return Ok(None);
        }
        let raster = self.source.raster(mipmap)?;
        let image = native_raster(&raster, [0, 0, 0])?;
        let rgba = qt_unpremultiply(&image);
        let channel = if raster.alpha_only { 3 } else { 0 };
        let (mw, mh) = (image.width() as i64, image.height() as i64);
        let fill = if raster.default_white { 1.0 } else { 0.0 };
        let mut mask = vec![fill; self.width * self.height];
        let x = layer.number("LayerMaskOffsetX")
            + layer.number("LayerOffsetX")
            + layer.number("LayerMaskOffscrOffsetX");
        let y = layer.number("LayerMaskOffsetY")
            + layer.number("LayerOffsetY")
            + layer.number("LayerMaskOffscrOffsetY");
        let (w, h) = (self.width as i64, self.height as i64);
        let (left, top) = (x.max(0), y.max(0));
        let (right, bottom) = (w.min(x + mw), h.min(y + mh));
        if left < right && top < bottom {
            for ty in top..bottom {
                for tx in left..right {
                    let source = ((ty - y) * mw + (tx - x)) as usize * 4 + channel;
                    mask[(ty * w + tx) as usize] = f32::from(rgba[source]) / 255.0;
                }
            }
        }
        Ok(Some(mask))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn multiply_uses_backdrop_where_both_layers_overlap() {
        let backdrop = Pixels {
            width: 1,
            height: 1,
            data: vec![[0.5, 0.5, 0.5, 1.0]],
        };
        let result = composite(&backdrop, &backdrop, BLEND_MULTIPLY, false);
        assert_eq!(result.data, vec![[0.25, 0.25, 0.25, 1.0]]);
    }

    #[test]
    fn empty_backdrop_keeps_source_for_any_mode() {
        let backdrop = Pixels::blank(1, 1);
        let source = Pixels {
            width: 1,
            height: 1,
            data: vec![[0.2, 0.1, 0.0, 0.4]],
        };
        for mode in [0, 2, 8, 14, 21, 25] {
            let result = composite(&backdrop, &source, mode, false);
            for (a, b) in result.data[0].iter().zip(source.data[0]) {
                assert!((a - b).abs() < 1e-6, "mode {mode}");
            }
        }
    }

    fn row(values: &[(&'static str, i64)]) -> ClipLayerRow {
        ClipLayerRow {
            id: 1,
            name: String::new(),
            children: vec![],
            values: values.to_vec(),
        }
    }

    #[test]
    fn layer_color_maps_black_gray_white_and_preserves_alpha() {
        let image = PremulImage::from_straight(
            3,
            1,
            &[0, 0, 0, 255, 128, 128, 128, 255, 255, 255, 255, 128],
        )
        .unwrap();
        let layer = row(&[
            ("DrawColorEnable", 1),
            ("DrawColorMainRed", 10),
            ("DrawColorMainGreen", 20),
            ("DrawColorMainBlue", 30),
            ("DrawColorSubRed", 210),
            ("DrawColorSubGreen", 220),
            ("DrawColorSubBlue", 230),
        ]);
        let out = qt_unpremultiply(&apply_layer_color(image, &layer));
        assert_eq!(&out[0..4], &[10, 20, 30, 255]);
        assert_eq!(&out[4..8], &[110, 120, 130, 255]);
        assert_eq!(out[11], 128);
        assert!((i32::from(out[8]) - 210).abs() <= 1);
    }

    #[test]
    fn black_line_art_is_tinted_with_source_in() {
        let image = PremulImage::from_straight(2, 1, &[0, 0, 0, 255, 0, 0, 0, 100]).unwrap();
        let layer = row(&[
            ("DrawColorEnable", 1),
            ("DrawColorMainRed", 0x8080_8080),
            ("DrawColorMainGreen", 255),
            ("DrawColorMainBlue", 0),
        ]);
        let out = apply_layer_color(image, &layer);
        assert_eq!(&out.pixels()[0..4], &[128, 255, 0, 255]);
        assert_eq!(&out.pixels()[4..8], &[50, 100, 0, 100]);
    }

    #[test]
    fn source_atop_keeps_backdrop_alpha() {
        let mut target =
            PremulImage::from_straight(2, 1, &[200, 100, 40, 255, 0, 0, 0, 0]).unwrap();
        let source = PremulImage::from_straight(2, 1, &[0, 0, 255, 255, 0, 0, 255, 255]).unwrap();
        draw_atop(&mut target, &source, 1.0);
        assert_eq!(target.pixels(), &[0, 0, 255, 255, 0, 0, 0, 0]);
    }

    #[test]
    fn shift_moves_and_clears() {
        let mut pixels = Pixels::blank(2, 2);
        pixels.data[0] = [1.0; 4];
        let shifted = shift_pixels(pixels.clone(), 1, 1);
        assert_eq!(shifted.data[3], [1.0; 4]);
        assert_eq!(shifted.data[0], [0.0; 4]);
        assert_eq!(shift_pixels(pixels, 2, 0), Pixels::blank(2, 2));
    }
}
