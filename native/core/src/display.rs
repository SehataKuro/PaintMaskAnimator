//! Display computations without Qt dependencies, reusable by a future Rust UI.
use std::collections::{HashMap, HashSet};

pub type Rgb = (u8, u8, u8);

/// 元の色で表示対象を判定し、残す画素の色を一度だけ置き換えた RGBA バイト列を返す。
/// rgba は RGBA8888 の行優先で、長さは4の倍数を前提とし、確認は呼び出し側で行う。
/// `tests/test_display_performance.py::filter_palette_reference` の numpy 版と一致する。
/// `paintmaskanimator/native.py::filter_palette` を経由して
/// `paintmaskanimator/canvas.py::PaintCanvas.filtered_layer_image` から呼ばれる。
pub fn filter_palette_of(
    rgba: &[u8],
    visible: Option<Vec<Rgb>>,
    legacy: Option<Rgb>,
    remap: Vec<(Rgb, Rgb)>,
) -> Vec<u8> {
    let visible: Option<HashSet<_>> = visible.map(|colors| colors.into_iter().collect());
    let remap: HashMap<_, _> = remap.into_iter().collect();
    let mut result = rgba.to_vec();
    for pixel in result.chunks_exact_mut(4) {
        let rgb = (pixel[0], pixel[1], pixel[2]);
        // Test the original color, then remap once: A -> B -> C must
        // display an original A as B, never cascade to C.
        let keep = pixel[3] != 0
            && visible.as_ref().is_none_or(|colors| colors.contains(&rgb))
            && legacy.is_none_or(|color| color == rgb);
        if !keep {
            pixel[3] = 0;
        } else if let Some(&(r, g, b)) = remap.get(&rgb) {
            pixel[..3].copy_from_slice(&[r, g, b]);
        }
    }
    result
}

/// 選択色以外を透明にし、有効時は輝度に応じて着色したオニオンの RGBA バイト列を返す。
/// rgba は RGBA8888 の行優先で、長さは4の倍数を前提とし、確認は呼び出し側で行う。
/// `tests/test_display_performance.py::tint_onion_reference` の numpy 版と一致する。
/// `paintmaskanimator/native.py::tint_onion` を経由して
/// `paintmaskanimator/canvas_onion_render.py::OnionRenderMixin._tinted_onion_image` から呼ばれる。
pub fn tint_onion_of(
    rgba: &[u8],
    color: Rgb,
    enabled: bool,
    selected: Option<Vec<Rgb>>,
) -> Vec<u8> {
    let selected: Option<HashSet<_>> = selected.map(|values| values.into_iter().collect());
    let mut result = rgba.to_vec();
    for pixel in result.chunks_exact_mut(4) {
        if selected
            .as_ref()
            .is_some_and(|set| !set.contains(&(pixel[0], pixel[1], pixel[2])))
        {
            pixel[3] = 0;
        }
        if enabled {
            // Keep NumPy float32 operation order, including truncation.
            let luminance = (pixel[0] as f32 * 0.299f32
                + pixel[1] as f32 * 0.587f32
                + pixel[2] as f32 * 0.114f32)
                / 255.0f32;
            for (channel, base) in pixel[..3].iter_mut().zip([color.0, color.1, color.2]) {
                *channel = (base as f32 * (1.0f32 - luminance) + 255.0f32 * luminance)
                    .clamp(0.0, 255.0) as u8;
            }
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn palette_filters_original_colors_and_remaps_once() {
        // A→B→C。元の A だけが visible にあり、A は B まで、B は透明になる。
        // 透明な A と非表示の B の RGB は変わらない。
        assert_eq!(
            filter_palette_of(
                &[1, 2, 3, 128, 4, 5, 6, 255, 1, 2, 3, 0],
                Some(vec![(1, 2, 3)]),
                None,
                vec![((1, 2, 3), (4, 5, 6)), ((4, 5, 6), (7, 8, 9))],
            ),
            vec![4, 5, 6, 128, 4, 5, 6, 0, 1, 2, 3, 0]
        );
    }

    #[test]
    fn palette_legacy_keeps_only_matching_color() {
        let rgba = [1, 2, 3, 255, 4, 5, 6, 64];
        assert_eq!(
            filter_palette_of(&rgba, None, Some((4, 5, 6)), vec![]),
            vec![1, 2, 3, 0, 4, 5, 6, 64]
        );
        // visible と legacy の両方を満たす必要がある。
        assert_eq!(
            filter_palette_of(&rgba, Some(vec![(1, 2, 3)]), Some((4, 5, 6)), vec![]),
            vec![1, 2, 3, 0, 4, 5, 6, 0]
        );
        assert_eq!(filter_palette_of(&rgba, None, None, vec![]), rgba);
    }

    #[test]
    fn onion_disabled_only_filters_alpha() {
        let rgba = [1, 2, 3, 64, 4, 5, 6, 255];
        assert_eq!(
            tint_onion_of(&rgba, (100, 150, 200), false, Some(vec![(1, 2, 3)])),
            vec![1, 2, 3, 64, 4, 5, 6, 0]
        );
        assert_eq!(tint_onion_of(&rgba, (0, 0, 0), false, None), rgba);
        assert_eq!(
            tint_onion_of(&rgba, (0, 0, 0), false, Some(vec![])),
            vec![1, 2, 3, 0, 4, 5, 6, 0]
        );
    }

    #[test]
    fn onion_enabled_tints_even_filtered_pixels() {
        // 白の輝度は1、黒は0。除外された黒も RGB は着色され、alpha だけ0になる。
        assert_eq!(
            tint_onion_of(
                &[255, 255, 255, 128, 0, 0, 0, 255],
                (20, 40, 60),
                true,
                Some(vec![(255, 255, 255)]),
            ),
            vec![255, 255, 255, 128, 20, 40, 60, 0]
        );
    }

    #[test]
    fn onion_enabled_truncates_fractional_channels() {
        // 赤の輝度は0.299。黒を基色にすると各成分は255*0.299=76.245→76。
        assert_eq!(
            tint_onion_of(&[255, 0, 0, 73], (0, 0, 0), true, None),
            vec![76, 76, 76, 73]
        );
    }

    #[test]
    fn empty_buffers_stay_empty() {
        assert!(filter_palette_of(&[], Some(vec![]), None, vec![]).is_empty());
        assert!(tint_onion_of(&[], (1, 2, 3), true, None).is_empty());
    }
}
