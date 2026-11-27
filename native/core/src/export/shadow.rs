//! 使用色フォルダーを使った影素材（Issue #21、`shadow_export.py`）。
//!
//! 1枚のセルから3種類を作る。ノーマル単色塗り（絵のある画素を1色）、影単色（フォルダーの
//! 色の画素だけを1色）、影のマスク（フォルダーの色を白、それ以外を黒の不透明な画像）。

/// 素材の種類（フォルダー名にも使う、`KINDS` の順）。
pub const KINDS: [&str; 3] = ["normal", "shadow", "shadow_mask"];
/// ノーマルの既定の色。
pub const DEFAULT_NORMAL_COLOR: [u8; 3] = [255, 255, 255];
/// 影の既定の色。
pub const DEFAULT_SHADOW_COLOR: [u8; 3] = [0, 0, 255];

/// フォルダーの色が置かれている画素（`shadow_mask_of`）。
pub fn shadow_mask_of(rgba: &[u8], shadow_rgbs: &[[u8; 3]]) -> Vec<bool> {
    rgba.chunks_exact(4)
        .map(|p| p[3] > 0 && shadow_rgbs.iter().any(|rgb| p[..3] == rgb[..]))
        .collect()
}

fn paint(mask: impl Iterator<Item = bool>, color: [u8; 3], len: usize) -> Vec<u8> {
    let mut out = vec![0u8; len];
    for (pixel, on) in out.chunks_exact_mut(4).zip(mask) {
        if on {
            pixel.copy_from_slice(&[color[0], color[1], color[2], 255]);
        }
    }
    out
}

/// ノーマル単色塗り（`flat_normal`）。
pub fn flat_normal(rgba: &[u8], color: [u8; 3]) -> Vec<u8> {
    paint(rgba.chunks_exact(4).map(|p| p[3] > 0), color, rgba.len())
}

/// 影単色（`flat_shadow`）。
pub fn flat_shadow(rgba: &[u8], shadow_rgbs: &[[u8; 3]], color: [u8; 3]) -> Vec<u8> {
    paint(
        shadow_mask_of(rgba, shadow_rgbs).into_iter(),
        color,
        rgba.len(),
    )
}

/// 影のマスク（`shadow_matte`）。
pub fn shadow_matte(rgba: &[u8], shadow_rgbs: &[[u8; 3]]) -> Vec<u8> {
    let mut out = paint(
        shadow_mask_of(rgba, shadow_rgbs).into_iter(),
        [255; 3],
        rgba.len(),
    );
    for pixel in out.chunks_exact_mut(4) {
        pixel[3] = 255;
    }
    out
}

/// 3種類を [`KINDS`] の順に作る（`materials`）。
pub fn materials(rgba: &[u8], shadow_rgbs: &[[u8; 3]]) -> [Vec<u8>; 3] {
    [
        flat_normal(rgba, DEFAULT_NORMAL_COLOR),
        flat_shadow(rgba, shadow_rgbs, DEFAULT_SHADOW_COLOR),
        shadow_matte(rgba, shadow_rgbs),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn three_materials() {
        let rgba = [10, 20, 30, 255, 1, 2, 3, 9, 0, 0, 0, 0, 1, 2, 3, 0];
        let [normal, shadow, matte] = materials(&rgba, &[[1, 2, 3]]);
        assert_eq!(
            normal,
            [255, 255, 255, 255, 255, 255, 255, 255, 0, 0, 0, 0, 0, 0, 0, 0]
        );
        assert_eq!(shadow, [0, 0, 0, 0, 0, 0, 255, 255, 0, 0, 0, 0, 0, 0, 0, 0]);
        assert_eq!(
            matte,
            [0, 0, 0, 255, 255, 255, 255, 255, 0, 0, 0, 255, 0, 0, 0, 255]
        );
    }
}
