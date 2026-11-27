//! 使用色の画素の処理：使用色を数える、色を置き換える、描ける範囲で切り抜く、色相・彩度・
//! 明度をずらす、置換色のセットのファイルの形。
//!
//! - [`scan_layer`]：選択レイヤーの全コマの絵から使用色を数える（Qt の
//!   `UsedColorController.refresh`）。絵ごとに控え（[`ScanCache`]）を持ち、描いたあとは
//!   変わった絵だけを数え直す。
//! - [`remap_layer`]：統合・色置換・色削除（`apply_palette_replacements`）。
//! - [`clip_to_edit_range`]：描ける範囲の外の画素を元に戻す（#104）。
//! - [`shifted_color`]：使用色の色相・彩度・明度の調整（`hsv_adjust_dialog.shifted_color`）。
//!   Qt の QColor の HSV と同じ整数の丸めで計算する。

use std::collections::{BTreeMap, HashMap};

use crate::compose::layers::Sources;
use crate::document::drawing_commands::DrawingTarget;
use crate::document::model::Document;
use crate::image::RgbaImage;
use crate::project::json::Json;
use crate::Error;

use super::{EditRange, Rgb, BACKGROUND, MAX_COLORS};

/// 画素の色を、使用色の行の色にする。透明と白は背景。
#[inline]
pub fn pixel_key(pixel: &[u8]) -> Rgb {
    if pixel[3] == 0 || (pixel[0] == 255 && pixel[1] == 255 && pixel[2] == 255) {
        BACKGROUND
    } else {
        (pixel[0], pixel[1], pixel[2])
    }
}

/// 不透明（アルファが 0 でない）画素の色を、値の昇順に `limit` 色まで返す。
pub fn image_colors(rgba: &[u8], limit: usize) -> Vec<Rgb> {
    let mut packed = Vec::with_capacity(rgba.len());
    for pixel in rgba.chunks_exact(4) {
        if pixel[3] != 0 {
            packed.extend_from_slice(&[pixel[2], pixel[1], pixel[0], 0]);
        }
    }
    crate::colors::unique_colors(&packed, limit)
        .unwrap_or_default()
        .into_iter()
        .map(|v| ((v >> 16) as u8, (v >> 8) as u8, v as u8))
        .collect()
}

/// 数えた色と、上限を超えたか。
type Counted = (Vec<Rgb>, bool);

/// 使用色を数えた控え（絵の世代ごと・レイヤーの絵の並びごと）。
#[derive(Default)]
pub struct ScanCache {
    by_image: HashMap<u64, Counted>,
    by_layer: HashMap<(usize, Vec<Option<u64>>), Counted>,
}

impl ScanCache {
    pub fn clear(&mut self) {
        self.by_image.clear();
        self.by_layer.clear();
    }
}

/// 使用色を数えた結果。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Scan {
    /// 最初に現れたコマの順、同じコマの中は値の昇順。最大 100 色。
    pub colors: Vec<Rgb>,
    /// 100 色を超えたか。
    pub exceeded: bool,
}

/// 選択レイヤー `layer` の全コマの絵から使用色を数える。下書きレイヤーは数えない（空）。
pub fn scan_layer(document: &Document, layer: usize, cache: &mut ScanCache) -> Result<Scan, Error> {
    let Some(&track) = document.track_order().get(layer) else {
        return Ok(Scan::default());
    };
    if document.track(track).is_some_and(|t| t.settings.is_draft) {
        return Ok(Scan::default());
    }
    let sources = Sources::new(document)?;
    let frames = sources.frame_count();
    let images: Vec<Option<&RgbaImage>> = (0..frames)
        .map(|frame| sources.layer(document, frame, layer).and_then(|l| l.image))
        .collect();
    let signature: Vec<Option<u64>> = images
        .iter()
        .map(|i| i.map(RgbaImage::generation))
        .collect();
    let key = (layer, signature);
    if let Some((colors, exceeded)) = cache.by_layer.get(&key) {
        return Ok(Scan {
            colors: colors.clone(),
            exceeded: *exceeded,
        });
    }
    let mut all: Vec<Rgb> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut exceeded = false;
    for image in images.into_iter().flatten() {
        let generation = image.generation();
        if !cache.by_image.contains_key(&generation) {
            let colors = image_colors(image.rgba(), MAX_COLORS + 1);
            let over = colors.len() > MAX_COLORS;
            if cache.by_image.len() >= 512 {
                cache.by_image.clear();
            }
            cache.by_image.insert(
                generation,
                (colors[..colors.len().min(MAX_COLORS)].to_vec(), over),
            );
        }
        let (colors, over) = &cache.by_image[&generation];
        for &rgb in colors {
            if seen.insert(rgb) {
                all.push(rgb);
            }
        }
        exceeded |= *over || all.len() > MAX_COLORS;
    }
    all.truncate(MAX_COLORS);
    if cache.by_layer.len() >= 128 {
        cache.by_layer.clear();
    }
    cache.by_layer.insert(key, (all.clone(), exceeded));
    Ok(Scan {
        colors: all,
        exceeded,
    })
}

/// 色を置き換えた結果。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RemapResult {
    /// 書き換えた絵の数。
    pub cells: usize,
    /// 書き換えた画素の数。
    pub pixels: usize,
}

/// 1枚の絵の色を置き換える。置き換え先が白なら消しゴム（アルファ 0）にする。
/// `mask` があれば（1 画素 1 バイト、0 以外が選択）、その中だけを置き換える。
/// 何も変わらなければ None。
pub fn remap_image(
    image: &RgbaImage,
    mapping: &BTreeMap<Rgb, Rgb>,
    mask: Option<&[u8]>,
) -> Option<(RgbaImage, usize)> {
    let mut rgba = image.rgba().to_vec();
    let mut count = 0;
    for (index, pixel) in rgba.chunks_exact_mut(4).enumerate() {
        if pixel[3] == 0 || mask.is_some_and(|m| m.get(index).is_none_or(|&b| b == 0)) {
            continue;
        }
        let Some(&(r, g, b)) = mapping.get(&(pixel[0], pixel[1], pixel[2])) else {
            continue;
        };
        pixel[0] = r;
        pixel[1] = g;
        pixel[2] = b;
        if (r, g, b) == BACKGROUND {
            pixel[3] = 0;
        }
        count += 1;
    }
    if count == 0 {
        return None;
    }
    RgbaImage::from_rgba(image.width(), image.height(), &rgba)
        .ok()
        .map(|image| (image, count))
}

/// 置き換えの組を整える：背景を元にする組と、元と先が同じ組は除く。
pub fn effective_mapping(mapping: &BTreeMap<Rgb, Rgb>) -> BTreeMap<Rgb, Rgb> {
    mapping
        .iter()
        .filter(|(&s, &d)| s != BACKGROUND && s != d)
        .map(|(&s, &d)| (s, d))
        .collect()
}

/// 選択レイヤーの全コマの絵の色を置き換える（統合・色置換・色削除）。保持のコマは
/// 絵を持つコマ（同じ番号の絵）を1回だけ書き換える。
pub fn remap_layer(
    document: &mut Document,
    layer: usize,
    mapping: &BTreeMap<Rgb, Rgb>,
    mask: Option<&[u8]>,
) -> Result<RemapResult, Error> {
    let mapping = effective_mapping(mapping);
    let mut result = RemapResult::default();
    if mapping.is_empty() {
        return Ok(result);
    }
    let sources = Sources::new(document)?;
    let mut key_frames = Vec::new();
    for frame in 0..sources.frame_count() {
        if let Some(key) = sources.key_frame(frame, layer) {
            if !key_frames.contains(&key) {
                key_frames.push(key);
            }
        }
    }
    for key in key_frames {
        let replaced = {
            let layers = crate::compose::frame_layers(document, key)?;
            let Some(image) = layers
                .get(layer)
                .and_then(|l| l.as_ref())
                .and_then(|l| l.image)
            else {
                continue;
            };
            remap_image(image, &mapping, mask)
        };
        if let Some((image, count)) = replaced {
            document.replace_drawing_pixels(layer, DrawingTarget::Frame(key), image)?;
            result.cells += 1;
            result.pixels += count;
        }
    }
    Ok(result)
}

/// 描ける範囲の外の画素を、元の絵（`base`、描き始めの絵）の画素へ戻す（#104）。
/// どちらも同じ大きさのストレート RGBA。戻した画素の数を返す。
pub fn clip_to_edit_range(base: &[u8], after: &mut [u8], range: &EditRange) -> usize {
    if range.is_unrestricted() {
        return 0;
    }
    let mut restored = 0;
    for (before, now) in base.chunks_exact(4).zip(after.chunks_exact_mut(4)) {
        if before != now && !range.permits(pixel_key(before)) {
            now.copy_from_slice(before);
            restored += 1;
        }
    }
    restored
}

// ---- Qt の QColor の HSV ------------------------------------------------------

const USHRT_MAX: f32 = 65535.0;

fn q_round(value: f32) -> i32 {
    if value >= 0.0 {
        (value + 0.5) as i32
    } else {
        (value - 0.5) as i32
    }
}

/// QColor の HSV（16 ビットの内部値）。hue は 0..36000、無彩色は 65535。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct QtHsv {
    pub hue: u32,
    pub saturation: u32,
    pub value: u32,
}

impl QtHsv {
    /// QColor(r, g, b).toHsv()（Qt 6.11）。16 ビットの整数のまま差を取り、彩度と色相だけを
    /// float で求める（0..1 に直してから計算すると、ちょうど半分の値で丸めが1ずれる）。
    pub fn from_rgb(rgb: Rgb) -> Self {
        let r = rgb.0 as i32 * 257;
        let g = rgb.1 as i32 * 257;
        let b = rgb.2 as i32 * 257;
        let max = r.max(g).max(b);
        let min = r.min(g).min(b);
        let value = max as u32;
        if max == min {
            return Self {
                hue: 65535,
                saturation: 0,
                value,
            };
        }
        let chroma = (max - min) as f32;
        let saturation = q_round((chroma / max as f32) * USHRT_MAX) as u32;
        let hue = if max == r {
            let h = (g - b) as f32 / chroma;
            if h < 0.0 {
                h + 6.0
            } else {
                h
            }
        } else if max == g {
            2.0 + (b - r) as f32 / chroma
        } else {
            4.0 + (r - g) as f32 / chroma
        };
        Self {
            hue: q_round(hue * 6000.0) as u32,
            saturation,
            value,
        }
    }

    /// QColor::fromHsv(h, s, v)（h は -1 か 0..359、s・v は 0..255）。
    pub fn from_ints(hue: i32, saturation: u8, value: u8) -> Self {
        Self {
            hue: if hue < 0 {
                65535
            } else {
                (hue % 360) as u32 * 100
            },
            saturation: saturation as u32 * 0x101,
            value: value as u32 * 0x101,
        }
    }

    /// hue()（無彩色は -1）。
    pub fn hue_degrees(&self) -> i32 {
        if self.hue == 65535 {
            -1
        } else {
            (self.hue / 100) as i32
        }
    }

    pub fn saturation8(&self) -> u8 {
        super::categories::div_257(self.saturation)
    }

    pub fn value8(&self) -> u8 {
        super::categories::div_257(self.value)
    }

    /// toRgb() の 8 ビットの値。
    pub fn to_rgb(&self) -> Rgb {
        let channels = if self.saturation == 0 || self.hue == 65535 {
            [self.value; 3]
        } else {
            let h = if self.hue == 36000 {
                0.0
            } else {
                self.hue as f32 / 6000.0
            };
            let s = self.saturation as f32 / USHRT_MAX;
            let v = self.value as f32 / USHRT_MAX;
            let i = h as i32;
            let f = h - i as f32;
            let p = v * (1.0 - s);
            let (r, g, b) = if i & 1 == 1 {
                // Qt は arm64 の clang で `1 - s * f` を積和（fma）にまとめて求める。
                let q = v * (-s).mul_add(f, 1.0);
                match i {
                    1 => (q, v, p),
                    3 => (p, q, v),
                    _ => (v, p, q),
                }
            } else {
                let t = v * (-s).mul_add(1.0 - f, 1.0);
                match i {
                    0 => (v, t, p),
                    2 => (p, v, t),
                    _ => (t, p, v),
                }
            };
            [r, g, b].map(|c| q_round(c * USHRT_MAX) as u32)
        };
        let c = channels.map(super::categories::div_257);
        (c[0], c[1], c[2])
    }
}

/// `rgb` を HSV でずらす（`hsv_adjust_dialog.shifted_color`）。色相は度、彩度・明度は％。
/// 無彩色は色相を持たないので、回しても色は付かない。
pub fn shifted_color(rgb: Rgb, hue: i32, saturation: i32, value: i32) -> Rgb {
    let hsv = QtHsv::from_rgb(rgb);
    let current = hsv.hue_degrees();
    let new_hue = if current < 0 {
        -1
    } else {
        (current + hue).rem_euclid(360)
    };
    let shift = |base: u8, delta: i32| {
        let step = (delta as f64 * 2.55).round_ties_even() as i32;
        (base as i32 + step).clamp(0, 255) as u8
    };
    let new_saturation = shift(hsv.saturation8(), saturation);
    let new_value = shift(hsv.value8(), value);
    QtHsv::from_ints(
        new_hue.max(0),
        if new_hue < 0 { 0 } else { new_saturation },
        new_value,
    )
    .to_rgb()
}

/// `{元: ずらした色}`。変わらない色と白は含めない（`shifted_mapping`）。
pub fn shifted_mapping(rgbs: &[Rgb], hue: i32, saturation: i32, value: i32) -> BTreeMap<Rgb, Rgb> {
    rgbs.iter()
        .copied()
        .filter(|&rgb| rgb != BACKGROUND)
        .filter_map(|rgb| {
            let shifted = shifted_color(rgb, hue, saturation, value);
            (shifted != rgb).then_some((rgb, shifted))
        })
        .collect()
}

// ---- 置換色のセット（.pmarep） ---------------------------------------------------

/// 置換色のセットのファイルの拡張子。
pub const REPLACEMENT_SUFFIX: &str = ".pmarep";

fn lower_hex(rgb: Rgb) -> String {
    format!("#{:02x}{:02x}{:02x}", rgb.0, rgb.1, rgb.2)
}

/// `replacement_sets.to_data`。
pub fn replacement_set_to_json(mapping: &BTreeMap<Rgb, Rgb>) -> Json {
    Json::Object(vec![
        (
            "format".into(),
            Json::Str("paintmaskanimator.replacements".into()),
        ),
        ("version".into(), Json::Int("1".into())),
        (
            "replacements".into(),
            Json::Array(
                mapping
                    .iter()
                    .map(|(&s, &d)| {
                        Json::Object(vec![
                            ("from".into(), Json::Str(lower_hex(s))),
                            ("to".into(), Json::Str(lower_hex(d))),
                        ])
                    })
                    .collect(),
            ),
        ),
    ])
}

fn rgb_of_code(value: &Json) -> Option<Rgb> {
    let Json::Str(text) = value else {
        // Python は str(value) してから読む。数字などは6桁でないので読めない。
        return None;
    };
    let text = text.trim().trim_start_matches('#');
    if text.len() != 6 || !text.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    let v = u32::from_str_radix(text, 16).ok()?;
    Some(((v >> 16) as u8, (v >> 8) as u8, v as u8))
}

/// `replacement_sets.from_data`。読めない組は飛ばす。形がおかしければ Err。
pub fn replacement_set_from_json(data: &Json) -> Result<BTreeMap<Rgb, Rgb>, &'static str> {
    if !matches!(data, Json::Object(_)) {
        return Err("replacement set must be an object");
    }
    let Some(Json::Array(entries)) = data.get("replacements") else {
        return Err("replacement set has no replacements");
    };
    let mut mapping = BTreeMap::new();
    for entry in entries {
        let (Some(from), Some(to)) = (entry.get("from"), entry.get("to")) else {
            continue;
        };
        if let (Some(s), Some(d)) = (rgb_of_code(from), rgb_of_code(to)) {
            if s != d {
                mapping.insert(s, d);
            }
        }
    }
    Ok(mapping)
}
