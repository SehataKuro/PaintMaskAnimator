//! 自動塗りで使う二値境界の局所相関とピーク選択。
use crate::{fill::tracking::fft, Error};

fn invalid() -> Error {
    Error::Invalid("invalid local shift buffers or parameters".into())
}

fn direct_counts(
    points: &[(usize, usize)],
    exact: &[u8],
    wide: &[u8],
    (height, width): (usize, usize),
    (rows, cols): (usize, usize),
    ys: &[usize],
    xs: &[usize],
) -> Vec<(usize, usize)> {
    let mut counts = vec![(0, 0); ys.len() * xs.len()];
    // dy ごとに行の回り込みを一度だけ処理する。
    let mut row_points = Vec::with_capacity(points.len());
    for (&dy, row) in ys.iter().zip(counts.chunks_exact_mut(xs.len())) {
        row_points.clear();
        for &(y, x) in points {
            let y = y + dy;
            let y = if y >= rows { y - rows } else { y };
            if y < height {
                row_points.push((y * width, x));
            }
        }
        for (&dx, count) in xs.iter().zip(row) {
            let (mut e, mut w) = (0, 0);
            for &(start, x) in &row_points {
                let x = x + dx;
                let x = if x >= cols { x - cols } else { x };
                if x < width {
                    e += usize::from(exact[start + x]);
                    w += usize::from(wide[start + x]);
                }
            }
            *count = (e, w);
        }
    }
    counts
}

/// NumPy の `_solve_local_shift_numpy` と同じ画素ずれ、一致率、スコアの上位差を返す。
/// 動き共有なしのとき上位差は 0、共有ありで候補が一つのときは正の無限大。
/// 窓は行優先の 0/1 で、相関は fast_size の周期でゼロ詰めして求める。
///
/// FFT は二つの件数を基数（before の境界画素数 + 1）で詰めて計算する。
/// Parseval と Cauchy–Schwarz により、正規化した積の絶対値の和は
/// 両入力の L2 ノルムの積以下で、最大でも FFT 点数 × (基数 + 1)。
/// 384×384、既定の探索幅では各軸は最大432で、このスケールに f64 の
/// eps と余裕係数1024を掛けた見積もりは約0.0063。整数への丸め幅0.5より十分小さい。
/// この値は誤差の工学的見積もりで、最大窓の候補全件を直接集計と照合する。
/// 小素因数の周期と見積もり0.25未満の範囲で FFT を使い、それ以外は直接集計する。
#[allow(clippy::too_many_arguments, clippy::type_complexity)]
pub fn solve_local_shift(
    before: &[u8],
    exact: &[u8],
    wide: &[u8],
    width: usize,
    height: usize,
    fast_size: (usize, usize),
    dys: &[i64],
    dxs: &[i64],
    total: f64,
    step: i64,
    motion_penalty: f64,
    near_line_weight: f64,
    share_motion: bool,
) -> Result<((i64, i64), f64, f64), Error> {
    let (rows, cols) = fast_size;
    let candidates = dys.len().checked_mul(dxs.len()).ok_or_else(invalid)?;
    let fft_points = rows.checked_mul(cols).ok_or_else(invalid)?;
    if width == 0
        || height == 0
        || width.checked_mul(height) != Some(before.len())
        || exact.len() != before.len()
        || wide.len() != before.len()
        || rows < height
        || cols < width
        || rows > isize::MAX as usize
        || cols > isize::MAX as usize
        || candidates == 0
        || candidates > isize::MAX as usize / size_of::<(usize, usize)>()
        || !total.is_finite()
        || total <= 0.0
        || step <= 0
        || !motion_penalty.is_finite()
        || motion_penalty < 0.0
        || !(0.0..=1.0).contains(&near_line_weight)
        || dys
            .iter()
            .chain(dxs)
            .any(|&v| v.checked_mul(step).is_none())
        || before.iter().chain(exact).chain(wide).any(|&v| v > 1)
    {
        return Err(invalid());
    }
    let distance = dys
        .iter()
        .map(|v| v.unsigned_abs())
        .max()
        .unwrap_or(0)
        .saturating_add(dxs.iter().map(|v| v.unsigned_abs()).max().unwrap_or(0));
    let penalty = motion_penalty * step as f64;
    if distance > i64::MAX as u64 || !(penalty * distance as f64).is_finite() {
        return Err(invalid());
    }
    let points: Vec<_> = before
        .iter()
        .enumerate()
        .filter(|&(_, &v)| v != 0)
        .map(|(i, _)| (i / width, i % width))
        .collect();
    if points.len() as f64 != total {
        return Err(invalid());
    }
    let ys: Vec<_> = dys
        .iter()
        .map(|dy| dy.rem_euclid(rows as i64) as usize)
        .collect();
    let xs: Vec<_> = dxs
        .iter()
        .map(|dx| dx.rem_euclid(cols as i64) as usize)
        .collect();
    let radix = points.len() as u64 + 1;
    let error_estimate = fft_points as f64 * (radix + 1) as f64 * (1024.0 * f64::EPSILON);
    let fft_work = fft_points.saturating_mul((rows.ilog2() + cols.ilog2()) as usize);
    let direct_work = points.len().saturating_mul(candidates);
    let shape = (height, width);
    let counts = if direct_work > fft_work && error_estimate < 0.25 && smooth(rows) && smooth(cols)
    {
        fft::circular_counts(before, exact, wide, shape, fast_size, &ys, &xs, radix)?.0
    } else {
        direct_counts(&points, exact, wide, shape, fast_size, &ys, &xs)
    };

    let (mut best, mut second) = (f64::NEG_INFINITY, f64::NEG_INFINITY);
    let (mut shift, mut share) = ((0, 0), 0.0);
    for (&dy, row) in dys.iter().zip(counts.chunks_exact(dxs.len())) {
        for (&dx, &(exact, wide)) in dxs.iter().zip(row) {
            let window = exact as f64 * (1.0 - near_line_weight) + wide as f64 * near_line_weight;
            let overlap = window / total;
            let score = overlap - penalty * (dy.unsigned_abs() + dx.unsigned_abs()) as f64;
            if score > best {
                second = best;
                best = score;
                shift = (dy * step, dx * step);
                share = overlap;
            } else if score > second {
                second = score;
            }
        }
    }
    let gap = match (share_motion, candidates) {
        (false, _) => 0.0,
        (true, 1) => f64::INFINITY,
        (true, _) => best - second,
    };
    Ok((shift, share, gap))
}

/// NumPy の `auto_motion.certify_numpy` と同じ画素ずれ、上位差、一致率を返す。
/// 上限から最良と次点を確定できない場合は None。ヒントは間引き前の画素座標。
#[allow(clippy::too_many_arguments, clippy::type_complexity)]
pub fn certify_local_shift(
    before: &[u8],
    exact: &[u8],
    wide: &[u8],
    width: usize,
    height: usize,
    fast_size: (usize, usize),
    dys: &[i64],
    dxs: &[i64],
    total: f64,
    step: i64,
    motion_penalty: f64,
    near_line_weight: f64,
    hint: (f64, f64),
) -> Result<Option<((i64, i64), f64, f64)>, Error> {
    const EPSILON: f64 = 2e-5;
    const MAX_RADIUS: f64 = 8.0;
    const MAX_WORK: usize = 32768;

    if total < 4.0 {
        return Ok(None);
    }
    let (rows, cols) = fast_size;
    let penalty = motion_penalty * step as f64;
    if width == 0
        || height == 0
        || width.checked_mul(height) != Some(before.len())
        || exact.len() != before.len()
        || wide.len() != before.len()
        || !(height..=isize::MAX as usize).contains(&rows)
        || !(width..=isize::MAX as usize).contains(&cols)
        || !total.is_finite()
        || step <= 0
        || !penalty.is_finite()
        || penalty <= 0.0
        || !(0.0..=1.0).contains(&near_line_weight)
        || !hint.0.is_finite()
        || !hint.1.is_finite()
        || dys
            .iter()
            .chain(dxs)
            .any(|&v| v.checked_mul(step).is_none())
    {
        return Err(invalid());
    }
    if before.iter().any(|&v| v > 1) {
        return Ok(None);
    }
    if exact.iter().chain(wide).any(|&v| v > 1) {
        return Err(invalid());
    }
    let step_f64 = step as f64;
    let dy = (hint.0 / step_f64).round_ties_even();
    let dx = (hint.1 / step_f64).round_ties_even();
    // i64::MAX は f64 では 2^63 に丸められるため、上端は含めない。
    let range = i64::MIN as f64..-(i64::MIN as f64);
    if !range.contains(&dy) || !range.contains(&dx) {
        return Ok(None);
    }
    let (dy, dx) = (dy as i64, dx as i64);
    if !dys.contains(&dy) || !dxs.contains(&dx) {
        return Ok(None);
    }

    let points: Vec<_> = before
        .iter()
        .enumerate()
        .filter(|&(_, &v)| v != 0)
        .map(|(i, _)| (i / width, i % width))
        .collect();
    let shape = (height, width);
    let y_index = |y: i64| y.rem_euclid(rows as i64) as usize;
    let x_index = |x: i64| x.rem_euclid(cols as i64) as usize;
    let distance = |y: i64, x: i64| y.unsigned_abs().saturating_add(x.unsigned_abs());
    let weighted =
        |e: usize, w: usize| e as f64 * (1.0 - near_line_weight) + w as f64 * near_line_weight;
    let max_overlap = weighted(
        usize::from(exact.contains(&1)),
        usize::from(wide.contains(&1)),
    );
    let (e, w) = direct_counts(
        &points,
        exact,
        wide,
        shape,
        fast_size,
        &[y_index(dy)],
        &[x_index(dx)],
    )[0];
    let score = weighted(e, w) / total - penalty * distance(dy, dx) as f64;
    let radius = ((max_overlap - score + EPSILON) / penalty).floor();
    if !(0.0..=MAX_RADIUS).contains(&radius) {
        return Ok(None);
    }

    let mut candidates = Vec::new();
    let mut checked = 0_usize;
    for &y in dys {
        let xs: Vec<_> = dxs
            .iter()
            .copied()
            .filter(|&x| distance(y, x) <= radius as u64)
            .collect();
        if !xs.is_empty() {
            checked += xs.len();
            if checked.saturating_mul(points.len()) > MAX_WORK {
                return Ok(None);
            }
            candidates.push((y, xs));
        }
    }
    if checked == 0 {
        return Ok(None);
    }

    let (mut best, mut runner) = (f64::NEG_INFINITY, f64::NEG_INFINITY);
    let (mut shift, mut share) = ((0, 0), 0.0);
    for (y, xs) in candidates {
        let indices: Vec<_> = xs.iter().map(|&x| x_index(x)).collect();
        let counts = direct_counts(
            &points,
            exact,
            wide,
            shape,
            fast_size,
            &[y_index(y)],
            &indices,
        );
        for (x, (e, w)) in xs.into_iter().zip(counts) {
            let overlap = weighted(e, w) / total;
            let score = overlap - penalty * distance(y, x) as f64;
            if score > best {
                runner = best;
                best = score;
                shift = (y * step, x * step);
                share = overlap;
            } else if score > runner {
                runner = score;
            }
        }
    }
    let unseen = if checked < dys.len().saturating_mul(dxs.len()) {
        max_overlap - penalty * (radius + 1.0)
    } else {
        f64::NEG_INFINITY
    };
    if best - runner.max(unseen) <= EPSILON || runner < unseen {
        return Ok(None);
    }
    Ok(Some((shift, best - runner, share)))
}

fn smooth(mut n: usize) -> bool {
    for factor in [2, 3, 5] {
        while n.is_multiple_of(factor) {
            n /= factor;
        }
    }
    n == 1
}

#[cfg(test)]
mod tests {
    use super::*;

    // 画素ごとの巡回位置を直接決め、64画素ずつ AND と popcount で集計する。
    fn direct_table_384(before: &[u8], guide: &[u8], shifts: &[usize]) -> Vec<usize> {
        const WIDTH: usize = 384;
        const PERIOD: usize = 432;
        const WORDS: usize = WIDTH / 64;
        let pack_rows = |dx| {
            let mut rows = vec![[0_u64; WORDS]; PERIOD];
            for (row, pixels) in rows.iter_mut().zip(guide.chunks_exact(WIDTH)) {
                for x in 0..WIDTH {
                    let sx = (x + dx) % PERIOD;
                    if sx < WIDTH {
                        row[x / 64] |= u64::from(pixels[sx]) << (x % 64);
                    }
                }
            }
            rows
        };
        let before: Vec<[u64; WORDS]> = before
            .chunks_exact(WIDTH)
            .map(|pixels| {
                std::array::from_fn(|word| {
                    pixels[word * 64..][..64]
                        .iter()
                        .enumerate()
                        .map(|(bit, &value)| u64::from(value) << bit)
                        .sum()
                })
            })
            .collect();
        let mut counts = vec![0; shifts.len() * shifts.len()];
        for (xi, &dx) in shifts.iter().enumerate() {
            let guide = pack_rows(dx);
            for (yi, &dy) in shifts.iter().enumerate() {
                let sum: u32 = before
                    .iter()
                    .enumerate()
                    .map(|(y, row)| {
                        row.iter()
                            .zip(guide[(y + dy) % PERIOD])
                            .map(|(&a, b)| (a & b).count_ones())
                            .sum::<u32>()
                    })
                    .sum();
                counts[yi * shifts.len() + xi] = sum as usize;
            }
        }
        counts
    }

    #[test]
    fn fft_rounding_matches_full_direct_table() {
        let shape = (384, 384);
        let period = (432, 432);
        let shifts: Vec<_> = (0..=48).chain(384..432).collect();
        let mut state = 384_u32;
        let mut sample = |threshold| {
            state = state.wrapping_mul(1664525).wrapping_add(1013904223);
            u8::from((state >> 24) < threshold)
        };
        for solid in [false, true] {
            let before: Vec<_> = (0..384 * 384)
                .map(|_| if solid { 1 } else { sample(253) })
                .collect();
            let exact: Vec<_> = (0..before.len())
                .map(|_| if solid { 1 } else { sample(205) })
                .collect();
            let wide: Vec<_> = exact.iter().map(|&value| value | sample(242)).collect();
            let points: Vec<_> = before
                .iter()
                .enumerate()
                .filter(|&(_, &v)| v != 0)
                .map(|(i, _)| (i / shape.1, i % shape.1))
                .collect();
            let (counts, error) = fft::circular_counts(
                &before,
                &exact,
                &wide,
                shape,
                period,
                &shifts,
                &shifts,
                points.len() as u64 + 1,
            )
            .unwrap();
            let expected: Vec<_> = direct_table_384(&before, &exact, &shifts)
                .into_iter()
                .zip(direct_table_384(&before, &wide, &shifts))
                .collect();
            assert_eq!(counts, expected);
            eprintln!("384×384 solid={solid}: max rounding error = {error:e}");
            assert!(error < 0.0063);
        }
    }

    #[test]
    fn periodic_ties_and_gap() {
        let before = [1, 0, 0];
        let guide = [0, 1, 1];
        let solve = |period, dxs, share| {
            solve_local_shift(
                &before,
                &guide,
                &guide,
                3,
                1,
                (1, period),
                &[0],
                dxs,
                1.0,
                2,
                0.0625,
                0.25,
                share,
            )
            .unwrap()
        };
        assert_eq!(solve(3, &[1, -1, 0], true), ((0, 2), 1.0, 0.0));
        assert_eq!(solve(3, &[-1, 1, 0], true), ((0, -2), 1.0, 0.0));
        assert_eq!(solve(4, &[1, -1, 0], true), ((0, 2), 1.0, 0.875));
        assert_eq!(solve(3, &[-1], true), ((0, -2), 1.0, f64::INFINITY));
        assert_eq!(solve(3, &[-1], false), ((0, -2), 1.0, 0.0));
        let (fft, _) =
            fft::circular_counts(&before, &guide, &guide, (1, 3), (1, 3), &[0], &[1, 2, 0], 2)
                .unwrap();
        assert_eq!(fft, [(1, 1), (1, 1), (0, 0)]);
    }

    #[test]
    fn certify_wraps_and_checks_the_runner_bound() {
        let before = [1, 1, 1, 1, 0, 0, 0, 0];
        let exact = [1, 1, 1, 0, 0, 0, 0, 1];
        let certify = |period, dxs: &[i64], penalty, hint| {
            certify_local_shift(
                &before,
                &exact,
                &exact,
                8,
                1,
                (1, period),
                &[0],
                dxs,
                4.0,
                2,
                penalty,
                0.25,
                hint,
            )
            .unwrap()
        };
        // 周期8では -1 の先頭画素が窓内へ戻る。周期9ではゼロ詰め領域へ落ちる。
        assert_eq!(
            certify(8, &[0, -1], 0.0625, (0.0, -2.0)),
            Some(((0, -2), 0.125, 1.0))
        );
        assert_eq!(
            certify(9, &[0, -1], 0.0625, (0.0, -2.0)),
            Some(((0, 0), 0.125, 0.75))
        );
        // 半径1で次点が未検査上限と等しい場合は確定する。
        assert_eq!(
            certify(8, &[0, -1, 2], 0.0625, (0.0, -2.0)),
            Some(((0, -2), 0.125, 1.0))
        );
        assert_eq!(certify(8, &[0, -1, 2], 0.05, (0.0, -2.0)), None);
        assert_eq!(certify(8, &[-1], 0.0625, (1.0, -3.0)), None);
        assert_eq!(certify(8, &[0, -1], 0.0625, (2.0, -2.0)), None);
        assert_eq!(
            certify(8, &[-1], 0.0625, (1.0, -2.0)),
            Some(((0, -2), f64::INFINITY, 1.0))
        );
        assert_eq!(certify(8, &[0, -1, 9], 0.0625, (0.0, 18.0)), None);
        assert_eq!(certify(8, &[-1, -1], 0.0625, (0.0, -2.0)), None);
    }

    #[test]
    fn certify_gap_threshold_is_inclusive() {
        let certify = |penalty| {
            certify_local_shift(
                &[1; 4],
                &[0; 4],
                &[0; 4],
                4,
                1,
                (1, 4),
                &[0],
                &[0, 1],
                4.0,
                1,
                penalty,
                0.25,
                (0.0, 1.0),
            )
            .unwrap()
        };
        assert_eq!(certify(2e-5), None);
        let above = f64::from_bits(2e-5_f64.to_bits() + 1);
        assert_eq!(certify(above), Some(((0, 0), above, 0.0)));
    }

    #[test]
    fn certify_rejects_small_nonbinary_and_over_budget_inputs() {
        let certify = |before: &[u8], total, dxs: &[i64], hint| {
            let exact = vec![1; before.len()];
            certify_local_shift(
                before,
                &exact,
                &exact,
                before.len(),
                1,
                (1, before.len()),
                &[0],
                dxs,
                total,
                1,
                0.25,
                0.25,
                hint,
            )
            .unwrap()
        };
        assert_eq!(certify(&[1; 3], 3.0, &[0], (0.0, 0.0)), None);
        assert_eq!(certify(&[1, 1, 1, 1, 2], 6.0, &[0], (0.0, 0.0)), None);
        assert_eq!(certify(&[1; 6], 4.0, &[0], (0.0, 0.0)), None); // 半径が負
        assert_eq!(certify(&[1; 5], 4.0, &[1], (0.0, 1.0)), None); // 候補なし
        assert_eq!(
            certify(&[1; 16384], 16384.0, &[0, 1], (0.0, 1.0)),
            Some(((0, 0), 0.25, 1.0))
        );
        assert_eq!(certify(&[1; 16385], 16385.0, &[0, 1], (0.0, 1.0)), None);
    }

    #[test]
    fn rejects_invalid_inputs() {
        let solve = |before, exact, size, dys, total| {
            solve_local_shift(
                before,
                exact,
                &[1],
                1,
                1,
                size,
                dys,
                &[0],
                total,
                1,
                0.02,
                0.25,
                true,
            )
        };
        assert!(solve(&[], &[1], (1, 1), &[0], 1.0).is_err());
        assert!(solve(&[1], &[], (1, 1), &[0], 1.0).is_err());
        assert!(solve(&[1], &[1], (0, 1), &[0], 1.0).is_err());
        assert!(solve(&[1], &[1], (1, 1), &[], 1.0).is_err());
        assert!(solve(&[1], &[1], (1, 1), &[0], 0.0).is_err());
        assert!(solve(&[2], &[1], (1, 1), &[0], 1.0).is_err());
    }
}
