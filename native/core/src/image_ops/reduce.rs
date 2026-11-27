//! 2 値化（#62、`MainWindow.binarize_layer`）と、取り込みの前処理。
//!
//! Python 版 `canvas.reduce_image_with_settings` と同じ順番（ぼかし→トーンカーブ→
//! 共通パレット→ゴミ取り、デジタル TP はアンチエイリアス除去→白を透明）で画素を変える。
//! Python 版は段ごとに QImage（乗算済み形式）へ戻すので、同じ所で同じ往復をする。
use super::{antialias, despeckle, finish, soft_blur, PixelEdit};
use crate::image::RgbaImage;
use crate::project::qt_alpha::qt_premultiplied_roundtrip;
use crate::Error;

/// 2 値化のやり方（`extraction_mode`）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExtractionMode {
    /// 色面（`"surface"`）。
    Surface,
    /// ライン抽出（`"line"`）：背景を白として扱う。
    Line,
    /// デジタル TP（`"digital"`）：色数を決めず、アンチエイリアスだけを取り除く。
    Digital,
}

/// 2 値化ダイアログで決めた設定（`_color_reduction_settings` の dict）。
#[derive(Clone, Debug)]
pub struct ReduceSettings {
    /// やり方。
    pub mode: ExtractionMode,
    /// 共通パレット。`None` ならパレットへ寄せない。
    pub palette: Option<Vec<[u8; 3]>>,
    /// 紙の地色をそろえるか（#51）。純 Rust には未移植で、真ならエラーを返す。
    pub flatten_paper: bool,
    /// ぼかしの半径（0 で無し）。
    pub blur: f64,
    /// 最後に消す離れた点の大きさ（0 で無し）。
    pub despeckle: usize,
    /// 半透明のしきい値。
    pub alpha_threshold: i64,
    /// 背景を不透明な色として扱うか。
    pub opaque_background: bool,
    /// 背景色。
    pub background: Option<[u8; 3]>,
    /// トーンカーブの制御点（空ならかけない）。
    pub tone_curve_points: Vec<(f64, f64)>,
}

fn roundtrip(mut pixels: Vec<u8>, width: usize) -> Vec<u8> {
    qt_premultiplied_roundtrip(&mut pixels, width);
    pixels
}

/// `color_ops.apply_tone_curve`：恒等のカーブなら変えない。
pub fn apply_tone_curve(
    rgba: &[u8],
    width: usize,
    height: usize,
    points: &[(f64, f64)],
    background: Option<[u8; 3]>,
) -> Result<Vec<u8>, Error> {
    let points = crate::colors::normalize_tone_curve_points(points.to_vec());
    if points == [(0.0, 0.0), (1.0, 1.0)] || rgba.is_empty() {
        return Ok(rgba.to_vec());
    }
    let mut out = vec![0; rgba.len()];
    crate::tone_curve::apply_tone_curve(rgba, width, height, points, background, &mut out)?;
    Ok(roundtrip(out, width))
}

/// `color_ops.apply_color_reduction_palette`（トーンカーブは恒等）。
#[allow(clippy::too_many_arguments)]
pub fn apply_palette(
    rgba: &[u8],
    width: usize,
    height: usize,
    palette: &[[u8; 3]],
    alpha_threshold: i64,
    opaque_background: bool,
    background: Option<[u8; 3]>,
    line: bool,
) -> Result<Vec<u8>, Error> {
    if rgba.is_empty() || palette.is_empty() {
        return Ok(rgba.to_vec());
    }
    let colours = palette.concat();
    let surface = (!line).then_some(|rgb: &[u8], visible: &[u8]| {
        crate::palette_labels::surface_guided_labels(rgb, width, height, visible, &colours, true)
    });
    let mut out = vec![0; rgba.len()];
    crate::palette::apply_color_reduction_palette(
        rgba,
        width,
        height,
        &colours,
        alpha_threshold.clamp(1, 254) as u8,
        opaque_background,
        background,
        None,
        surface,
        &mut out,
    )?;
    Ok(roundtrip(out, width))
}

/// `_make_white_transparent`：不透明度が 0 でない純白を透明にする。
pub fn make_white_transparent(rgba: &[u8], width: usize) -> Vec<u8> {
    let mut out = rgba.to_vec();
    for pixel in out.chunks_exact_mut(4) {
        if pixel[..3] == [255, 255, 255] && pixel[3] != 0 {
            pixel[3] = 0;
        }
    }
    roundtrip(out, width)
}

/// `reduce_image_with_settings`：1 枚に 2 値化の設定をかけた画素。
pub fn reduce_rgba(
    rgba: &[u8],
    width: usize,
    height: usize,
    settings: &ReduceSettings,
) -> Result<Vec<u8>, Error> {
    let mut pixels = rgba.to_vec();
    if settings.mode == ExtractionMode::Digital {
        let (cleaned, changed) = antialias::remove_antialias(&pixels, width, height);
        if changed > 0 {
            pixels = roundtrip(cleaned, width);
        }
        pixels = make_white_transparent(&pixels, width);
    } else {
        if settings.flatten_paper {
            return Err(Error::Unsupported(
                "紙の地色をそろえる処理（scan_paper）は純 Rust に未移植です".into(),
            ));
        }
        if settings.blur > 0.0 {
            pixels = roundtrip(
                soft_blur::soft_blur(&pixels, width, height, settings.blur),
                width,
            );
        }
        let line = settings.mode == ExtractionMode::Line;
        let background = if line {
            Some([255, 255, 255])
        } else {
            settings.background
        };
        if !settings.tone_curve_points.is_empty() {
            pixels = apply_tone_curve(
                &pixels,
                width,
                height,
                &settings.tone_curve_points,
                background,
            )?;
        }
        if let Some(palette) = &settings.palette {
            pixels = apply_palette(
                &pixels,
                width,
                height,
                palette,
                settings.alpha_threshold,
                line || settings.opaque_background,
                background,
                line,
            )?;
        }
    }
    if settings.despeckle > 0 {
        let (cleaned, removed) =
            despeckle::remove_isolated_specks(&pixels, width, height, settings.despeckle);
        if removed > 0 {
            pixels = roundtrip(cleaned, width);
        }
    }
    Ok(pixels)
}

/// 2 値化の 1 セル分（`binarize_layer` の op）。変わらなければ `None`。
/// 変えた画素数は数えないので、`pixels` は変わった画素の数。
pub fn binarize(image: &RgbaImage, settings: &ReduceSettings) -> Result<Option<PixelEdit>, Error> {
    if image.rgba().is_empty() {
        return Ok(None);
    }
    let pixels = reduce_rgba(image.rgba(), image.width(), image.height(), settings)?;
    let count = pixels
        .chunks_exact(4)
        .zip(image.rgba().chunks_exact(4))
        .filter(|(a, b)| a != b)
        .count();
    if count == 0 {
        return Ok(None);
    }
    finish(image, pixels, count).map(Some)
}

/// `make_border_background_transparent`（取り込み、#10）：外周からつながる明るい背景を透明にする。
/// 背景が見つからなければ `None`（元の画像のまま）。
pub fn border_background_transparent(
    rgba: &[u8],
    width: usize,
    height: usize,
    tolerance: i64,
) -> Result<Option<Vec<u8>>, Error> {
    if rgba.is_empty() || rgba.chunks_exact(4).any(|pixel| pixel[3] < 250) {
        return Ok(None);
    }
    let rgb = |x: usize, y: usize| &rgba[(y * width + x) * 4..(y * width + x) * 4 + 3];
    let mut border = Vec::new();
    for x in 0..width {
        border.extend_from_slice(rgb(x, 0));
    }
    for x in 0..width {
        border.extend_from_slice(rgb(x, height - 1));
    }
    if height > 2 {
        for y in 1..height - 1 {
            border.extend_from_slice(rgb(0, y));
        }
        for y in 1..height - 1 {
            border.extend_from_slice(rgb(width - 1, y));
        }
    }
    let Some(background) = crate::color_stats::border_background(&border)? else {
        return Ok(None);
    };
    let limit = tolerance * tolerance * 3;
    let near: Vec<u8> = rgba
        .chunks_exact(4)
        .map(|pixel| {
            let distance: i64 = (0..3)
                .map(|c| {
                    let d = i64::from(pixel[c]) - i64::from(background[c]);
                    d * d
                })
                .sum();
            u8::from(distance <= limit)
        })
        .collect();
    let seeds = super::border_seeds(|index| near[index] != 0, width, height);
    if seeds.is_empty() {
        return Ok(None);
    }
    let outside = crate::regions::flood_fill(&near, width, height, &seeds);
    if !outside.iter().any(|&v| v != 0) {
        return Ok(None);
    }
    let mut out = rgba.to_vec();
    for (pixel, _) in out
        .chunks_exact_mut(4)
        .zip(&outside)
        .filter(|(_, &v)| v != 0)
    {
        pixel[3] = 0;
    }
    Ok(Some(roundtrip(out, width)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::image_ops::test_support::image;

    fn settings(mode: ExtractionMode) -> ReduceSettings {
        ReduceSettings {
            mode,
            palette: None,
            flatten_paper: false,
            blur: 0.0,
            despeckle: 0,
            alpha_threshold: 128,
            opaque_background: false,
            background: None,
            tone_curve_points: vec![(0.0, 0.0), (1.0, 1.0)],
        }
    }

    #[test]
    fn digital_mode_makes_white_transparent() {
        let source = image(2, 1, &[[255, 255, 255, 255], [0, 0, 0, 255]]);
        let edit = binarize(&source, &settings(ExtractionMode::Digital))
            .unwrap()
            .unwrap();
        assert_eq!(edit.image.rgba(), &[0, 0, 0, 0, 0, 0, 0, 255]);
        assert_eq!(edit.pixels, 1);
    }

    #[test]
    fn identity_settings_change_nothing_and_paper_is_unsupported() {
        let source = image(2, 1, &[[1, 2, 3, 255], [4, 5, 6, 255]]);
        assert!(binarize(&source, &settings(ExtractionMode::Surface))
            .unwrap()
            .is_none());
        let paper = ReduceSettings {
            flatten_paper: true,
            ..settings(ExtractionMode::Surface)
        };
        assert!(binarize(&source, &paper).is_err());
    }

    #[test]
    fn border_background_is_cleared() {
        let mut rgba = [250u8, 252, 251, 255].repeat(25);
        rgba[12 * 4..12 * 4 + 4].copy_from_slice(&[0, 0, 0, 255]);
        let out = border_background_transparent(&rgba, 5, 5, 24)
            .unwrap()
            .unwrap();
        assert_eq!(&out[12 * 4..12 * 4 + 4], &[0, 0, 0, 255]);
        assert_eq!(&out[..4], &[0, 0, 0, 0]);
    }
}
