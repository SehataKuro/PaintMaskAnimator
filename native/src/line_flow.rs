//! 線の位置合わせで、候補のずれごとの一致画素数を数える。
//! 元の線の座標・探索順・区画をそのまま使い、背景の偽画素だけを省く。

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

#[cfg(test)]
mod tests {
    use super::*;

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
