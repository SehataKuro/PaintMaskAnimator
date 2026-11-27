//! RGBA8888 の統計と前処理。

use crate::{colors::local_color_variation, Error};

pub(crate) fn rgba_pixels(rgba: &[u8], width: usize, height: usize) -> Result<&[[u8; 4]], Error> {
    let (pixels, tail) = rgba.as_chunks::<4>();
    if !tail.is_empty() || width.checked_mul(height) != Some(pixels.len()) {
        return Err(Error::Invalid(
            "expected width × height packed RGBA pixels".into(),
        ));
    }
    Ok(pixels)
}

fn rgb(pixel: &[u8; 4]) -> [u8; 3] {
    [pixel[0], pixel[1], pixel[2]]
}

/// `color_ops.image_alpha_statistics_numpy` と同じ順に透明・半透明・不透明の画素数を返す。
pub fn image_alpha_statistics(
    rgba: &[u8],
    width: usize,
    height: usize,
) -> Result<(usize, usize, usize), Error> {
    let pixels = rgba_pixels(rgba, width, height)?;
    let mut transparent = 0;
    let mut opaque = 0;
    for pixel in pixels {
        transparent += usize::from(pixel[3] == 0);
        opaque += usize::from(pixel[3] == 255);
    }
    Ok((transparent, pixels.len() - transparent - opaque, opaque))
}

/// `color_ops.binarize_alpha_for_pixel_art_numpy` と同じ二値化済み RGBA8888 を返す。
/// しきい値は呼び出し側で 1〜254 に正規化する。
pub fn binarize_alpha_for_pixel_art(
    rgba: &[u8],
    width: usize,
    height: usize,
    threshold: u8,
) -> Result<Vec<u8>, Error> {
    let pixels = rgba_pixels(rgba, width, height)?;
    if !(1..=254).contains(&threshold) {
        return Err(Error::Invalid(
            "expected alpha threshold from 1 to 254".into(),
        ));
    }
    let result: Vec<[u8; 4]> = pixels
        .iter()
        .map(|&[r, g, b, a]| {
            if a >= threshold {
                [r, g, b, 255]
            } else {
                [0; 4]
            }
        })
        .collect();
    Ok(result.into_flattened())
}

fn visible_colors(pixels: &[[u8; 4]]) -> impl Iterator<Item = usize> + '_ {
    pixels.iter().filter_map(|&[r, g, b, a]| {
        (a != 0).then_some((usize::from(r) << 16) | (usize::from(g) << 8) | usize::from(b))
    })
}

/// `color_ops.opaque_rgb_color_count_numpy` と同じ、アルファが正の画素の異なる RGB 色数を返す。
pub fn opaque_rgb_color_count(rgba: &[u8], width: usize, height: usize) -> Result<usize, Error> {
    let pixels = rgba_pixels(rgba, width, height)?;
    if pixels.len() < 4096 {
        let mut colors: Vec<_> = visible_colors(pixels).collect();
        colors.sort_unstable();
        colors.dedup();
        return Ok(colors.len());
    }
    // RGB8 の色空間は固定なので、大きい画像では 2 MiB のビット集合で数える。
    let mut seen = vec![0u64; (1 << 24) / 64];
    let mut count = 0;
    for color in visible_colors(pixels) {
        let word = &mut seen[color / 64];
        let bit = 1u64 << (color % 64);
        count += usize::from(*word & bit == 0);
        *word |= bit;
    }
    Ok(count)
}

fn distance_squared(left: [u8; 3], right: [u8; 3]) -> i32 {
    left.into_iter()
        .zip(right)
        .map(|(a, b)| {
            let delta = i32::from(a) - i32::from(b);
            delta * delta
        })
        .sum()
}

/// `color_ops.detect_opaque_border_background_numpy` と同じ条件で明るい外周背景の RGB を返す。
pub fn border_background(border: &[u8]) -> Result<Option<[u8; 3]>, Error> {
    let (colors, tail) = border.as_chunks::<3>();
    if !tail.is_empty() {
        return Err(Error::Invalid("expected packed RGB border pixels".into()));
    }
    if colors.is_empty() {
        return Ok(None);
    }
    let mut border = colors.to_vec();
    border.sort_unstable();
    let mut dominant = [0; 3];
    let mut count = 0;
    for run in border.chunk_by(|left, right| left == right) {
        // 辞書順で先に現れた最大頻度色を保持する。
        if run.len() > count {
            dominant = run[0];
            count = run.len();
        }
    }
    let min = dominant[0].min(dominant[1]).min(dominant[2]);
    let max = dominant[0].max(dominant[1]).max(dominant[2]);
    if min < 218 || max - min > 24 {
        return Ok(None);
    }
    let near = border
        .iter()
        .filter(|&&color| distance_squared(color, dominant) <= 26 * 26 * 3)
        .count();
    Ok((near as f64 / border.len() as f64 >= 0.52).then_some(dominant))
}

/// `color_ops.estimate_mixed_boundary_pixels_numpy` と同じ、混色が疑われる境界の画素数を返す。
pub fn estimate_mixed_boundary_pixels(
    rgba: &[u8],
    width: usize,
    height: usize,
    background: Option<[u8; 3]>,
) -> Result<usize, Error> {
    let pixels = rgba_pixels(rgba, width, height)?;
    let packed: Vec<[u8; 3]> = pixels.iter().map(rgb).collect();
    let variation = local_color_variation(packed.as_flattened(), width, height)?;
    Ok(pixels
        .iter()
        .zip(variation)
        .filter(|&(pixel, variation)| {
            pixel[3] >= 250
                && variation >= 10
                && background.is_none_or(|bg| {
                    // RGB8 の二乗和では sqrt の丸めで 5 や 190 に達する隣接整数はない。
                    let squared = distance_squared(rgb(pixel), bg);
                    squared > 5 * 5 && squared < 190 * 190
                })
        })
        .count())
}

/// `color_ops._snap_overlay_to_exact_rgbs_numpy` と同じ、マスク内の RGB をパレットに吸着した RGBA8888 を返す。
pub fn snap_overlay_to_exact_rgbs(
    rgba: &[u8],
    width: usize,
    height: usize,
    mask: &[u8],
    palette: &[u8],
) -> Result<Vec<u8>, Error> {
    let pixels = rgba_pixels(rgba, width, height)?;
    let (palette, tail) = palette.as_chunks::<3>();
    if mask.len() != pixels.len() || !tail.is_empty() {
        return Err(Error::Invalid(
            "expected one mask byte per pixel and packed RGB palette".into(),
        ));
    }
    let mut result = pixels.to_vec();
    if let Some((&first, rest)) = palette.split_first() {
        for (pixel, &active) in result.iter_mut().zip(mask) {
            if active == 0 {
                continue;
            }
            let mut nearest = first;
            if !rest.is_empty() {
                let source = rgb(pixel);
                let mut best = snap_distance(source, first);
                for &color in rest {
                    let distance = snap_distance(source, color);
                    if distance < best {
                        best = distance;
                        nearest = color;
                    }
                }
            }
            pixel[..3].copy_from_slice(&nearest);
        }
    }
    Ok(result.into_flattened())
}

pub(crate) fn snap_distance(left: [u8; 3], right: [u8; 3]) -> i32 {
    left.into_iter()
        .zip(right)
        .map(|(a, b)| {
            let delta = i16::from(a) - i16::from(b);
            // NumPy は int16 で乗算してから int32 で合計する。
            i32::from(delta.wrapping_mul(delta))
        })
        .sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn alpha_statistics_binarization_and_color_count() {
        let pixels = [
            [2, 3, 4, 0],
            [2, 3, 4, 249],
            [5, 6, 7, 250],
            [5, 6, 7, 254],
            [8, 9, 10, 255],
        ];
        let rgba = pixels.as_flattened();
        assert_eq!(image_alpha_statistics(rgba, 5, 1).unwrap(), (1, 3, 1));
        assert_eq!(opaque_rgb_color_count(rgba, 5, 1).unwrap(), 3);
        assert_eq!(
            binarize_alpha_for_pixel_art(rgba, 5, 1, 254).unwrap(),
            [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5, 6, 7, 255, 8, 9, 10, 255]
        );
        let large = pixels.repeat(1000);
        assert_eq!(
            opaque_rgb_color_count(large.as_flattened(), 5, 1000).unwrap(),
            3
        );
        assert!(image_alpha_statistics(rgba, 2, 2).is_err());
        assert!(binarize_alpha_for_pixel_art(rgba, 5, 1, 0).is_err());
        assert_eq!(image_alpha_statistics(&[], 0, 4).unwrap(), (0, 0, 0));
    }

    #[test]
    fn border_background_handles_ties_and_validates_input() {
        let pixels = [[221; 3], [220; 3], [221; 3], [220; 3]];
        assert_eq!(
            border_background(pixels.as_flattened()).unwrap(),
            Some([220; 3])
        );
        assert_eq!(border_background(&[]).unwrap(), None);
        assert!(border_background(&[255; 4]).is_err());
    }

    #[test]
    fn mixed_boundary_uses_strict_distance_limits() {
        for (value, expected) in [(5, 0), (6, 1), (189, 1), (190, 0)] {
            let rgba = [value, 0, 0, 250, 255, 255, 255, 255];
            assert_eq!(
                estimate_mixed_boundary_pixels(&rgba, 2, 1, None).unwrap(),
                2
            );
            assert_eq!(
                estimate_mixed_boundary_pixels(&rgba, 2, 1, Some([0; 3])).unwrap(),
                expected
            );
        }
    }

    #[test]
    fn overlay_distance_wraps_and_keeps_first_tie() {
        let rgba = [10, 20, 30, 123, 20, 30, 40, 248];
        assert_eq!(
            snap_overlay_to_exact_rgbs(&rgba, 2, 1, &[1, 0], &[9, 20, 30, 11, 20, 30]).unwrap(),
            [9, 20, 30, 123, 20, 30, 40, 248]
        );
        assert_eq!(
            snap_overlay_to_exact_rgbs(&rgba, 2, 1, &[1, 0], &[10, 20, 30, 255, 255, 255]).unwrap(),
            [255, 255, 255, 123, 20, 30, 40, 248]
        );
        assert_eq!(
            snap_overlay_to_exact_rgbs(&rgba, 2, 1, &[1, 0], &[]).unwrap(),
            rgba
        );
        assert!(snap_overlay_to_exact_rgbs(&rgba, 2, 1, &[1], &[]).is_err());
    }
}
