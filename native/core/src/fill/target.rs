//! 対象色ごとの通過可能画素の判定。

/// Python の bucket_fill.BLACK_MAX と同じ値の黒判定の上限。
pub const BLACK_MAX: u8 = 48;
/// Python の bucket_fill.WHITE_MIN と同じ値の白判定の下限。
pub const WHITE_MIN: u8 = 240;
/// Python の bucket_fill.FAINT_ALPHA_MAX と同じ値の薄い画素の alpha 上限。
pub const FAINT_ALPHA_MAX: u8 = 50;

/// Python の設定文字列は入口でこの型に変換する。
pub enum TargetColor {
    /// TARGET_ALL ("all"): All(Some(rgb)) は alpha 非ゼロで RGB が一致、All(None) は透明または純白を通す。
    All(Option<[u8; 3]>),
    /// TARGET_TRANSPARENT ("transparent"): alpha がゼロの画素を通す。
    Transparent,
    /// TARGET_SURROUNDED_BY_TRANSPARENT ("surrounded_by_transparent"): alpha が非ゼロの画素を通す。
    SurroundedByTransparent,
    /// TARGET_BLACK ("black"): alpha が非ゼロで RGB 全成分が BLACK_MAX 以下の画素を通す。
    Black,
    /// TARGET_SURROUNDED_BY_BLACK ("surrounded_by_black"): Black で通さない画素を通す。
    SurroundedByBlack,
    /// TARGET_WHITE_TRANSPARENT ("white_transparent"): 透明または RGB 全成分が WHITE_MIN 以上の画素を通す。
    WhiteTransparent,
    /// TARGET_SURROUNDED_BY_WHITE_TRANSPARENT ("surrounded_by_white_transparent"): 非透明で RGB のいずれかが WHITE_MIN 未満の画素を通す。
    SurroundedByWhiteTransparent,
    /// TARGET_FAINT_AS_TRANSPARENT ("faint_as_transparent"): alpha が FAINT_ALPHA_MAX 以下の画素を通す。
    FaintAsTransparent,
    /// TARGET_OPAQUE_AND_INNER_TRANSPARENT ("opaque_and_inner_transparent"): alpha が非ゼロの画素を通す。
    OpaqueAndInnerTransparent,
    /// TARGET_TRANSPARENT_AND_CLOSED_OPAQUE ("transparent_and_closed_opaque"): alpha がゼロの画素を通す。
    TransparentAndClosedOpaque,
}

/// RGBA8888 行優先データから、長さ width * height の 0/1 の行優先マスクを返す。
///
/// bucket_fill.fill_target_mask_numpy と一致する。TARGET_ALL は category が None なら
/// imaging.background_mask_packed、Some(rgb) なら imaging.opaque_rgb_mask_packed と一致する。
/// 呼び出し側で寸法の積がオーバーフローせず、入力長が width * height * 4 と一致することを
/// 確かめる前提。不一致は debug_assert で検出し、リリースビルドでは検証しない。
pub fn fill_target_mask(rgba: &[u8], width: usize, height: usize, target: TargetColor) -> Vec<u8> {
    debug_assert_eq!(rgba.len(), width * height * 4);
    fn mask(rgba: &[u8], predicate: impl Fn(u32) -> bool) -> Vec<u8> {
        rgba.as_chunks::<4>()
            .0
            .iter()
            .map(|p| u8::from(predicate(u32::from_le_bytes(*p))))
            .collect()
    }
    let black = |p: u32| {
        (p >> 24 != 0)
            & ((p & 255) <= u32::from(BLACK_MAX))
            & (((p >> 8) & 255) <= u32::from(BLACK_MAX))
            & (((p >> 16) & 255) <= u32::from(BLACK_MAX))
    };
    let white = |p: u32| {
        ((p & 255) >= u32::from(WHITE_MIN))
            & (((p >> 8) & 255) >= u32::from(WHITE_MIN))
            & (((p >> 16) & 255) >= u32::from(WHITE_MIN))
    };
    match target {
        TargetColor::All(None) => mask(rgba, |p| (p >> 24 == 0) | ((p & 0xffffff) == 0xffffff)),
        TargetColor::All(Some([r, g, b])) => {
            let rgb = u32::from_le_bytes([r, g, b, 0]);
            mask(rgba, |p| (p >> 24 != 0) & ((p & 0xffffff) == rgb))
        }
        TargetColor::Transparent | TargetColor::TransparentAndClosedOpaque => {
            mask(rgba, |p| p >> 24 == 0)
        }
        TargetColor::SurroundedByTransparent | TargetColor::OpaqueAndInnerTransparent => {
            mask(rgba, |p| p >> 24 != 0)
        }
        TargetColor::Black => mask(rgba, black),
        TargetColor::SurroundedByBlack => mask(rgba, |p| !black(p)),
        TargetColor::WhiteTransparent => mask(rgba, |p| (p >> 24 == 0) | white(p)),
        TargetColor::SurroundedByWhiteTransparent => mask(rgba, |p| (p >> 24 != 0) & !white(p)),
        TargetColor::FaintAsTransparent => mask(rgba, |p| p >> 24 <= u32::from(FAINT_ALPHA_MAX)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // alpha/RGB の境界、純黒・純白、一成分だけ境界外、同色の透明画素。
    const PIXELS: [[u8; 4]; 14] = [
        [0, 0, 0, 0],
        [0, 0, 0, 1],
        [47, 47, 47, 50],
        [48, 48, 48, 51],
        [49, 49, 49, 255],
        [239, 239, 239, 255],
        [240, 240, 240, 255],
        [241, 241, 241, 255],
        [255, 255, 255, 255],
        [255, 255, 255, 0],
        [12, 34, 56, 255],
        [12, 34, 56, 0],
        [48, 49, 48, 255],
        [240, 239, 240, 255],
    ];

    macro_rules! target_test {
        ($name:ident, $target:expr, $expected:expr) => {
            #[test]
            fn $name() {
                // 2 行の入力で行優先の順序と出力長も確認する。
                assert_eq!(
                    fill_target_mask(PIXELS.as_flattened(), 7, 2, $target),
                    $expected
                );
            }
        };
    }
    target_test!(
        all_background,
        TargetColor::All(None),
        [1, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 0, 0]
    );
    target_test!(
        all_rgb,
        TargetColor::All(Some([12, 34, 56])),
        [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0]
    );
    target_test!(
        transparent,
        TargetColor::Transparent,
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 0]
    );
    target_test!(
        surrounded_by_transparent,
        TargetColor::SurroundedByTransparent,
        [0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 0, 1, 1]
    );
    target_test!(
        black,
        TargetColor::Black,
        [0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    );
    target_test!(
        surrounded_by_black,
        TargetColor::SurroundedByBlack,
        [1, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    );
    target_test!(
        white_transparent,
        TargetColor::WhiteTransparent,
        [1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 1, 0, 0]
    );
    target_test!(
        surrounded_by_white_transparent,
        TargetColor::SurroundedByWhiteTransparent,
        [0, 1, 1, 1, 1, 1, 0, 0, 0, 0, 1, 0, 1, 1]
    );
    target_test!(
        faint_as_transparent,
        TargetColor::FaintAsTransparent,
        [1, 1, 1, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 0]
    );
    target_test!(
        opaque_and_inner_transparent,
        TargetColor::OpaqueAndInnerTransparent,
        [0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 0, 1, 1]
    );
    target_test!(
        transparent_and_closed_opaque,
        TargetColor::TransparentAndClosedOpaque,
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 0]
    );
}
