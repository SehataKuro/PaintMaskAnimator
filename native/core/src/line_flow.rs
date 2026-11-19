//! 線の位置合わせで、候補のずれごとの一致画素数を数える。
//! 元の線の座標・探索順・区画をそのまま使い、背景の偽画素だけを省く。

/// 候補のずれ `(dy, dx)` ごと・区画ごとの一致画素数を返す。
/// `paintmaskanimator/line_flow.py` の numpy 版 `_scores_numpy` と同じ一致数・候補順。
///
/// `target` と `guide` は長さ `width * height` の行優先マスク（0 が背景、非0 が線）。
/// `(width, height)` は画像の幅と高さ、`block` は区画の一辺の画素数で、
/// `block > 0` かつ幅・高さが `block` で割り切れることを前提とする。
/// `base_y`・`base_x` は各区画の基準のずれで、長さはともに
/// `blocks = (height / block) * (width / block)`、区画は行優先に並ぶ。
/// `reach` は基準のずれから上下左右に探索する画素数。
///
/// `side = 2 * reach + 1` とし、候補は
/// `(dy, dx) = (base_y[index] + iy - reach, base_x[index] + ix - reach)`。
/// 結果の並びは `(iy * side + ix) * blocks + index`（`iy` が外側、`ix` が内側）。
/// 画像外やずれの加算がオーバーフローする画素・候補は一致として数えない。
pub fn score_shifts(
    target: &[u8],
    guide: &[u8],
    (width, height): (usize, usize),
    block: usize,
    base_y: &[i64],
    base_x: &[i64],
    reach: usize,
) -> Vec<u32> {
    let rows = height / block;
    let cols = width / block;
    let blocks = rows * cols;
    let side = 2 * reach + 1;
    let mut scores = vec![0; side * side * blocks];
    for by in 0..rows {
        for bx in 0..cols {
            let index = by * cols + bx;
            let mut points = Vec::new();
            for y in by * block..(by + 1) * block {
                for x in bx * block..(bx + 1) * block {
                    if target[y * width + x] != 0 {
                        points.push((y as i64, x as i64));
                    }
                }
            }
            if points.is_empty() {
                continue;
            }
            // numpy の候補順 (dy が外側、dx が内側) を保つ。同点時の選択も同じ。
            for iy in 0..side {
                let Some(dy) = base_y[index].checked_add(iy as i64 - reach as i64) else {
                    continue;
                };
                for ix in 0..side {
                    let Some(dx) = base_x[index].checked_add(ix as i64 - reach as i64) else {
                        continue;
                    };
                    let mut count = 0;
                    for &(y, x) in &points {
                        let (Some(gy), Some(gx)) = (y.checked_add(dy), x.checked_add(dx)) else {
                            continue;
                        };
                        if gy >= 0
                            && gx >= 0
                            && gy < height as i64
                            && gx < width as i64
                            && guide[gy as usize * width + gx as usize] != 0
                        {
                            count += 1;
                        }
                    }
                    scores[(iy * side + ix) * blocks + index] = count;
                }
            }
        }
    }
    scores
}

/// 行優先の区画格子から、画素座標順のずれ `(dy, dx)` を返す。
/// broadcast 済みの f64 座標を `line_flow.Flow.at` の NumPy 版と同じ順序で補間する。
/// 無限大の座標は格子端へ寄せる。NaN 座標や非有限・極端な格子値は
/// NumPy に戻せるよう Unsupported を返す。
pub fn interpolate_flow(
    dy: &[f64],
    dx: &[f64],
    (width, height): (usize, usize),
    block: usize,
    ys: &[f64],
    xs: &[f64],
) -> Result<(Vec<f64>, Vec<f64>), crate::Error> {
    if width == 0
        || height == 0
        || block == 0
        || width.checked_mul(height) != Some(dy.len())
        || dy.len() != dx.len()
        || ys.len() != xs.len()
    {
        return Err(crate::Error::Invalid(
            "invalid flow interpolation dimensions".into(),
        ));
    }
    if dy
        .iter()
        .chain(dx)
        .any(|value| !value.is_finite() || value.abs() > f64::MAX / 2.0)
    {
        return Err(crate::Error::Unsupported(
            "non-finite or extreme flow grid".into(),
        ));
    }

    let block = block as f64;
    let axis = |coord: f64, last: usize| {
        let fraction = ((coord + 0.5) / block - 0.5).max(0.0).min(last as f64);
        let lower = fraction.floor() as usize;
        (lower, (lower + 1).min(last), fraction - lower as f64)
    };
    let mut out_y = Vec::with_capacity(ys.len());
    let mut out_x = Vec::with_capacity(xs.len());
    for (&y, &x) in ys.iter().zip(xs) {
        if y.is_nan() || x.is_nan() {
            return Err(crate::Error::Unsupported("NaN flow coordinate".into()));
        }
        let (y0, y1, wy) = axis(y, height - 1);
        let (x0, x1, wx) = axis(x, width - 1);
        let row0 = y0 * width;
        let row1 = y1 * width;
        let left = 1.0 - wx;
        let upper = 1.0 - wy;
        let interpolate = |grid: &[f64]| {
            // NumPy と同様に、乗算と加算を個別に丸める。
            let top = grid[row0 + x0] * left + grid[row0 + x1] * wx;
            let bottom = grid[row1 + x0] * left + grid[row1 + x1] * wx;
            top * upper + bottom * wy
        };
        out_y.push(interpolate(dy));
        out_x.push(interpolate(dx));
    }
    Ok((out_y, out_x))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn interpolate_clips_and_handles_single_axis_grids() {
        let axis = [
            -f64::INFINITY,
            -1.0,
            15.5,
            23.5,
            47.5,
            1000.0,
            f64::INFINITY,
        ];
        let fixed = [23.5; 7];
        let expected = (
            vec![2.0, 2.0, 2.0, 4.0, 10.0, 10.0, 10.0],
            vec![-8.0, -8.0, -8.0, -5.0, 4.0, 4.0, 4.0],
        );
        assert_eq!(
            interpolate_flow(&[2.0, 10.0], &[-8.0, 4.0], (2, 1), 32, &fixed, &axis).unwrap(),
            expected,
        );
        assert_eq!(
            interpolate_flow(&[2.0, 10.0], &[-8.0, 4.0], (1, 2), 32, &axis, &fixed).unwrap(),
            expected,
        );
        assert_eq!(
            interpolate_flow(&[2.0], &[-8.0], (1, 1), 32, &axis, &axis).unwrap(),
            (vec![2.0; 7], vec![-8.0; 7]),
        );
        assert_eq!(
            interpolate_flow(
                &[0.0, 4.0, 8.0, 16.0],
                &[8.0, 0.0, 4.0, 4.0],
                (2, 2),
                32,
                &[31.5, 47.5],
                &[31.5, 47.5]
            )
            .unwrap(),
            (vec![7.0, 16.0], vec![4.0, 4.0]),
        );
    }

    #[test]
    fn interpolate_rejects_nan_coordinates() {
        assert!(matches!(
            interpolate_flow(&[1.0], &[2.0], (1, 1), 32, &[f64::NAN], &[0.0]),
            Err(crate::Error::Unsupported(_))
        ));
    }

    #[test]
    fn candidates_are_dy_then_dx_and_outside_is_false() {
        let target = [1, 0, 0, 0];
        let guide = [0, 0, 0, 1];
        let scores = score_shifts(&target, &guide, (2, 2), 2, &[0], &[0], 1);
        assert_eq!(scores, vec![0, 0, 0, 0, 0, 0, 0, 0, 1]);
    }

    #[test]
    fn blocks_keep_independent_base_offsets() {
        let target = [1, 0, 1, 0, 0, 0, 0, 0];
        let guide = [0, 1, 1, 0, 0, 0, 0, 0];
        assert_eq!(
            score_shifts(&target, &guide, (4, 2), 2, &[0, 0], &[1, 0], 0),
            vec![1, 1]
        );
    }

    #[test]
    fn extreme_offsets_do_not_overflow() {
        let mask = [1];
        let scores = score_shifts(&mask, &mask, (1, 1), 1, &[i64::MAX], &[i64::MIN], 1);
        assert_eq!(scores, vec![0; 9]);
    }
}
