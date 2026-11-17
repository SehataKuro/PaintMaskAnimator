//! 線周りのノイズ除去。Python の入口から呼ばれ、
//! `line_noise.remove_line_noise_numpy` と同じ RGBA8888 と変更画素数を返す。

const TRANSPARENT: u32 = 0x1000000;
const OUTSIDE: u32 = 0x2000000;
const OFFSETS: [(isize, isize); 8] = [
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
];

/// RGBA8888 行優先の入力から、線近傍の孤立色を多数決で置き換えた画像と画素数を返す。
///
/// `line_noise.remove_line_noise_numpy` と一致する。native の Python 向け入口から呼ぶ。
/// 不正な長さ・3 未満の寸法・変更なしの場合は (空 Vec, 0) を返す。
#[allow(clippy::too_many_arguments)]
pub fn remove_line_noise(
    rgba: &[u8],
    width: usize,
    height: usize,
    line_luma: i64,
    radius: usize,
    majority: i64,
    isolation: i64,
) -> (Vec<u8>, usize) {
    if width < 3
        || height < 3
        || width.checked_mul(height).and_then(|n| n.checked_mul(4)) != Some(rgba.len())
    {
        return (Vec::new(), 0);
    }
    let packed: Vec<u32> = rgba
        .chunks_exact(4)
        .map(|p| {
            if p[3] == 0 {
                TRANSPARENT
            } else {
                (u32::from(p[0]) << 16) | (u32::from(p[1]) << 8) | u32::from(p[2])
            }
        })
        .collect();
    let lines: Vec<u8> = rgba
        .chunks_exact(4)
        .map(|p| {
            u8::from(
                p[3] > 0 && (i64::from(p[0]) + i64::from(p[1]) + i64::from(p[2])) / 3 <= line_luma,
            )
        })
        .collect();
    // 画像全体が覆われた後の膨張は不要。巨大な radius でも同じ結果になる。
    let grown = crate::regions::dilate(&lines, width, height, radius.min(width.max(height)));
    let mut result = Vec::new();
    let mut changed = 0;
    for y in 0..height {
        for x in 0..width {
            let index = y * width + x;
            if rgba[index * 4 + 3] == 0 || lines[index] != 0 || grown[index] == 0 {
                continue;
            }
            let mut neighbours = [OUTSIDE; 8];
            for (i, (dy, dx)) in OFFSETS.iter().enumerate() {
                // numpy の _shifted は result[y,x] = values[y-dy,x-dx]。
                if let (Some(ny), Some(nx)) = (y.checked_add_signed(-dy), x.checked_add_signed(-dx))
                {
                    if ny < height && nx < width {
                        neighbours[i] = packed[ny * width + nx];
                    }
                }
            }
            let mut winner = neighbours[0];
            let mut best_count = 0i64;
            for &value in &neighbours {
                let count = if value == OUTSIDE {
                    0
                } else {
                    neighbours.iter().filter(|&&other| other == value).count() as i64
                };
                // 厳密な > により np.argmax と同じ最初の最大値を選ぶ。
                if count > best_count {
                    best_count = count;
                    winner = value;
                }
            }
            let own = neighbours
                .iter()
                .filter(|&&v| v != OUTSIDE && v == packed[index])
                .count() as i64;
            if own <= isolation
                && best_count >= majority
                && winner != packed[index]
                && winner != OUTSIDE
                && winner != TRANSPARENT
            {
                if changed == 0 {
                    result = rgba.to_vec();
                }
                result[index * 4..index * 4 + 4].copy_from_slice(&[
                    (winner >> 16) as u8,
                    (winner >> 8) as u8,
                    winner as u8,
                    255,
                ]);
                changed += 1;
            }
        }
    }
    (result, changed)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn image(width: usize, height: usize, value: u8) -> Vec<u8> {
        [value, value, value, 255].repeat(width * height)
    }

    #[test]
    fn isolated_dot_changes_but_lines_do_not() {
        let mut rgba = image(3, 3, 0);
        rgba[16..20].copy_from_slice(&[255, 255, 0, 42]);
        let (out, count) = remove_line_noise(&rgba, 3, 3, 110, 1, 5, 0);
        assert_eq!(count, 1);
        assert_eq!(out, image(3, 3, 0));
        assert_eq!(remove_line_noise(&rgba, 3, 3, 110, 0, 5, 0), (vec![], 0));
    }

    #[test]
    fn radius_limits_changes() {
        let mut rgba = image(7, 7, 240);
        rgba[0..4].copy_from_slice(&[0, 0, 0, 255]);
        rgba[96..100].copy_from_slice(&[255, 255, 0, 255]); // (3,3)
        assert_eq!(remove_line_noise(&rgba, 7, 7, 110, 2, 5, 0), (vec![], 0));
        let (out, count) = remove_line_noise(&rgba, 7, 7, 110, 3, 5, 0);
        assert_eq!(count, 1);
        assert_eq!(&out[96..100], &[240, 240, 240, 255]);
        assert_eq!(&out[0..4], &[0, 0, 0, 255]);
    }

    #[test]
    fn transparent_majority_is_not_used() {
        let mut rgba = vec![0; 36];
        rgba[0..4].copy_from_slice(&[0, 0, 0, 255]);
        rgba[16..20].copy_from_slice(&[255, 255, 0, 255]);
        assert_eq!(remove_line_noise(&rgba, 3, 3, 110, 1, 5, 0), (vec![], 0));
    }

    #[test]
    fn isolation_allows_a_pair() {
        let mut rgba = image(4, 3, 0);
        rgba[20..28].copy_from_slice(&[255, 255, 0, 255, 255, 255, 0, 255]);
        assert_eq!(remove_line_noise(&rgba, 4, 3, 110, 1, 5, 0), (vec![], 0));
        assert_eq!(
            remove_line_noise(&rgba, 4, 3, 110, 1, 5, 1),
            (image(4, 3, 0), 2)
        );
    }

    #[test]
    fn tie_uses_first_shifted_neighbour() {
        let mut rgba = image(3, 3, 200);
        // 最初の近傍 (2,2) は黒。黒4票、灰4票で黒が勝つ。
        for index in [0, 1, 2, 8] {
            rgba[index * 4..index * 4 + 4].copy_from_slice(&[0, 0, 0, 255]);
        }
        rgba[16..20].copy_from_slice(&[255, 255, 0, 255]);
        let (out, count) = remove_line_noise(&rgba, 3, 3, 110, 1, 4, 0);
        assert_eq!(count, 1);
        assert_eq!(&out[16..20], &[0, 0, 0, 255]);
    }

    #[test]
    fn invalid_inputs_do_not_panic() {
        for (w, h, len) in [(2, 3, 24), (3, 2, 24), (3, 3, 35), (usize::MAX, 3, 0)] {
            assert_eq!(
                remove_line_noise(&vec![0; len], w, h, 110, 2, 5, 0),
                (vec![], 0)
            );
        }
    }
}
