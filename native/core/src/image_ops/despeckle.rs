//! 取り込みの段階としてのゴミ取り（#10、`despeckle.remove_isolated_specks`）。
//!
//! ほかの絵に 8 近傍で 1 画素も触れていない、`max_pixels` 画素以下の不透明な塊だけを
//! 透明にする。塊は不透明な画素の 4 近傍のつながりで分ける。
use crate::regions::{label_regions, INVALID_CODE};

/// 既定で消す塊の大きさ（画素）。
pub const DEFAULT_MAX_PIXELS: usize = 3;

/// 消す画素のマスク：ほかの絵から離れた `max_pixels` 画素以下の塊。
pub fn isolated_specks(rgba: &[u8], width: usize, height: usize, max_pixels: usize) -> Vec<bool> {
    let opaque: Vec<bool> = rgba.chunks_exact(4).map(|pixel| pixel[3] > 0).collect();
    if max_pixels == 0 || !opaque.iter().any(|&v| v) {
        return vec![false; opaque.len()];
    }
    let codes: Vec<u32> = opaque
        .iter()
        .map(|&v| if v { 1 } else { INVALID_CODE })
        .collect();
    let (labels, count) = label_regions(&codes, width, height);
    let mut sizes = vec![0usize; count as usize + 1];
    for &label in &labels {
        sizes[label as usize] += 1;
    }
    // 斜めにだけ触れている別の塊があれば、離れていない（線の先の点など）。
    let mut touching = vec![false; count as usize + 1];
    for y in 0..height {
        for x in 0..width {
            let label = labels[y * width + x];
            if label == 0 {
                continue;
            }
            for dy in -1isize..=1 {
                for dx in -1isize..=1 {
                    let (ny, nx) = (y as isize + dy, x as isize + dx);
                    if (dy == 0 && dx == 0)
                        || ny < 0
                        || nx < 0
                        || ny as usize >= height
                        || nx as usize >= width
                    {
                        continue;
                    }
                    let other = labels[ny as usize * width + nx as usize];
                    if other > 0 && other != label {
                        touching[label as usize] = true;
                    }
                }
            }
        }
    }
    labels
        .iter()
        .map(|&label| label > 0 && sizes[label as usize] <= max_pixels && !touching[label as usize])
        .collect()
}

/// 離れた小さな点を透明にした RGBA と、消した画素の数。
pub fn remove_isolated_specks(
    rgba: &[u8],
    width: usize,
    height: usize,
    max_pixels: usize,
) -> (Vec<u8>, usize) {
    let mask = isolated_specks(rgba, width, height, max_pixels);
    let mut out = rgba.to_vec();
    let mut removed = 0;
    for (pixel, _) in out.chunks_exact_mut(4).zip(&mask).filter(|(_, &v)| v) {
        pixel.fill(0);
        removed += 1;
    }
    (out, removed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn isolated_dot_is_removed_but_touching_dot_is_kept() {
        let mut rgba = vec![0u8; 5 * 5 * 4];
        for (x, y) in [(0, 0), (3, 3), (4, 4)] {
            rgba[(y * 5 + x) * 4 + 3] = 255;
        }
        let (out, removed) = remove_isolated_specks(&rgba, 5, 5, 1);
        assert_eq!(removed, 1);
        assert_eq!(out[3], 0);
        assert_eq!(out[(3 * 5 + 3) * 4 + 3], 255);
    }
}
