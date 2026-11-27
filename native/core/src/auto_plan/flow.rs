//! 2枚の線画のあいだの区画ごとの動き（Python の `line_flow.estimate_flow`）。
use crate::line_flow::{interpolate_flow, score_shifts};
use crate::regions::dilate;
use crate::Error;

/// 区画の大きさ（画素）。
pub(crate) const BLOCK: usize = 32;
const COARSE: usize = 8;
const REACH: usize = 96;
const REFINE_STEPS: [usize; 3] = [4, 2, 1];
const REFINE_REACH: usize = 2;
const NEIGHBOUR_WEIGHT: f64 = 0.5;
const MOTION_PENALTY: f64 = 0.002;
const MIN_LINE_PIXELS: f64 = 6.0;

/// 区画ごとのずれ（画素）。
pub(crate) struct Flow {
    dy: Vec<f64>,
    dx: Vec<f64>,
    rows: usize,
    cols: usize,
}

impl Flow {
    /// 画素 `(ys, xs)` のずれ（`Flow.at`）。
    pub(crate) fn at(&self, ys: &[f64], xs: &[f64]) -> Result<(Vec<f64>, Vec<f64>), Error> {
        interpolate_flow(&self.dy, &self.dx, (self.cols, self.rows), BLOCK, ys, xs)
    }
}

/// `scale` 画素四方に線が1画素でもあれば線とする縮小（`_pool`）。
fn pool(
    mask: &[u8],
    width: usize,
    height: usize,
    scale: usize,
    rows: usize,
    cols: usize,
) -> Vec<u8> {
    let (pw, ph) = (cols * BLOCK / scale, rows * BLOCK / scale);
    let mut out = vec![0u8; pw * ph];
    for y in 0..height {
        for x in 0..width {
            if mask[y * width + x] != 0 {
                out[(y / scale) * pw + x / scale] = 1;
            }
        }
    }
    out
}

/// 区画ごとの和（`_block_sum`）。
fn block_sum(values: &[u8], width: usize, rows: usize, cols: usize) -> Vec<f64> {
    let size = width / cols;
    let mut out = vec![0.0; rows * cols];
    for (i, &v) in values.iter().enumerate() {
        if v != 0 {
            let (y, x) = (i / width, i % width);
            out[(y / size) * cols + x / size] += 1.0;
        }
    }
    out
}

/// となりの区画の値を重み付きで足す（`_with_neighbours`）。足す順も同じ。
fn with_neighbours(grid: &[f64], rows: usize, cols: usize) -> Vec<f64> {
    let mut total = vec![0.0; rows * cols];
    for dy in 0..3usize {
        for dx in 0..3usize {
            let weight = if dy == 1 && dx == 1 {
                1.0
            } else {
                NEIGHBOUR_WEIGHT
            };
            for y in 0..rows {
                for x in 0..cols {
                    let (sy, sx) = (y as i64 + dy as i64 - 1, x as i64 + dx as i64 - 1);
                    let value =
                        if sy >= 0 && sx >= 0 && (sy as usize) < rows && (sx as usize) < cols {
                            grid[sy as usize * cols + sx as usize]
                        } else {
                            0.0
                        };
                    total[y * cols + x] += weight * value;
                }
            }
        }
    }
    total
}

/// 候補ごとの比（となりを足した重なり ÷ 線の数）から、区画ごとに一番良い候補。
fn best_candidates(
    scores: &[u32],
    counts: &[f64],
    offsets: &[(i64, i64)],
    scale: usize,
    rows: usize,
    cols: usize,
) -> Vec<usize> {
    let blocks = rows * cols;
    let denominator: Vec<f64> = with_neighbours(counts, rows, cols)
        .into_iter()
        .map(|v| v.max(1.0))
        .collect();
    let penalty: Vec<f64> = offsets
        .iter()
        .map(|&(dy, dx)| MOTION_PENALTY * scale as f64 * (dy as f64).hypot(dx as f64))
        .collect();
    let mut best = vec![0usize; blocks];
    let mut best_value = vec![f64::NEG_INFINITY; blocks];
    for (candidate, &p) in penalty.iter().enumerate() {
        let grid: Vec<f64> = scores[candidate * blocks..(candidate + 1) * blocks]
            .iter()
            .map(|&s| f64::from(s))
            .collect();
        let summed = with_neighbours(&grid, rows, cols);
        for b in 0..blocks {
            let value = summed[b] / denominator[b] - p;
            // 同点は先の候補（np.argmax）。
            if candidate == 0 || value > best_value[b] {
                best_value[b] = value;
                best[b] = candidate;
            }
        }
    }
    best
}

/// `estimate_flow`：`target` の各区画が `guide` のどこにあるか。
pub(crate) fn estimate_flow(target: &[u8], guide: &[u8], width: usize, height: usize) -> Flow {
    let rows = height.div_ceil(BLOCK);
    let cols = width.div_ceil(BLOCK);
    let blocks = rows * cols;
    // 1. 粗い解像度で広く探す。
    let scale = COARSE;
    let (pw, ph) = (cols * BLOCK / scale, rows * BLOCK / scale);
    let pooled = pool(target, width, height, scale, rows, cols);
    let wide = dilate(&pool(guide, width, height, scale, rows, cols), pw, ph, 1);
    let counts = block_sum(&pooled, pw, rows, cols);
    let reach = REACH / scale;
    let offsets: Vec<(i64, i64)> = (-(reach as i64)..=reach as i64)
        .flat_map(|dy| (-(reach as i64)..=reach as i64).map(move |dx| (dy, dx)))
        .collect();
    let zero = vec![0i64; blocks];
    let scores = score_shifts(&pooled, &wide, (pw, ph), BLOCK / scale, &zero, &zero, reach);
    let best = best_candidates(&scores, &counts, &offsets, scale, rows, cols);
    let mut flow_y: Vec<f64> = best
        .iter()
        .map(|&i| (offsets[i].0 * scale as i64) as f64)
        .collect();
    let mut flow_x: Vec<f64> = best
        .iter()
        .map(|&i| (offsets[i].1 * scale as i64) as f64)
        .collect();
    // 2. 解像度を上げて、今のずれのまわりだけを探し直す。
    let deltas: Vec<(i64, i64)> = (-(REFINE_REACH as i64)..=REFINE_REACH as i64)
        .flat_map(|dy| (-(REFINE_REACH as i64)..=REFINE_REACH as i64).map(move |dx| (dy, dx)))
        .collect();
    for scale in REFINE_STEPS {
        let (pw, ph) = (cols * BLOCK / scale, rows * BLOCK / scale);
        let pooled = pool(target, width, height, scale, rows, cols);
        let wide = dilate(&pool(guide, width, height, scale, rows, cols), pw, ph, 1);
        let counts = block_sum(&pooled, pw, rows, cols);
        let base_y: Vec<i64> = flow_y
            .iter()
            .map(|v| (v / scale as f64).round_ties_even() as i64)
            .collect();
        let base_x: Vec<i64> = flow_x
            .iter()
            .map(|v| (v / scale as f64).round_ties_even() as i64)
            .collect();
        let scores = score_shifts(
            &pooled,
            &wide,
            (pw, ph),
            BLOCK / scale,
            &base_y,
            &base_x,
            REFINE_REACH,
        );
        let best = best_candidates(&scores, &counts, &deltas, scale, rows, cols);
        for b in 0..blocks {
            let (dy, dx) = deltas[best[b]];
            flow_y[b] += (dy * scale as i64) as f64;
            flow_x[b] += (dx * scale as i64) as f64;
        }
    }
    // 線の無い区画は、まわりの区画のずれ（線の多さで重み付け）に従う。
    let lines = block_sum(
        &pool(target, width, height, 1, rows, cols),
        cols * BLOCK,
        rows,
        cols,
    );
    let mut weak: Vec<bool> = lines.iter().map(|&l| l < MIN_LINE_PIXELS).collect();
    if weak.iter().any(|&w| w) && weak.iter().any(|&w| !w) {
        let mut weight: Vec<f64> = lines
            .iter()
            .zip(&weak)
            .map(|(&l, &w)| if w { 0.0 } else { l })
            .collect();
        for _ in 0..rows.max(cols) {
            if !weak.iter().any(|&w| w) {
                break;
            }
            let spread = with_neighbours(&weight, rows, cols);
            let wy: Vec<f64> = weight.iter().zip(&flow_y).map(|(w, f)| w * f).collect();
            let wx: Vec<f64> = weight.iter().zip(&flow_x).map(|(w, f)| w * f).collect();
            let fill_y = with_neighbours(&wy, rows, cols);
            let fill_x = with_neighbours(&wx, rows, cols);
            let reached: Vec<bool> = weak
                .iter()
                .zip(&spread)
                .map(|(&w, &s)| w && s > 0.0)
                .collect();
            if !reached.iter().any(|&r| r) {
                break;
            }
            for b in 0..blocks {
                if reached[b] {
                    flow_y[b] = fill_y[b] / spread[b].max(1e-9);
                    flow_x[b] = fill_x[b] / spread[b].max(1e-9);
                    weight[b] = 1.0;
                    weak[b] = false;
                }
            }
        }
    }
    Flow {
        dy: flow_y,
        dx: flow_x,
        rows,
        cols,
    }
}

/// 区画の流れを何段かつないだ経路（`_FlowPath`）。
pub(crate) struct FlowPair {
    pub forward: std::rc::Rc<Flow>,
    pub backward: std::rc::Rc<Flow>,
    pub x0: usize,
    pub y0: usize,
}

/// 経路をたどった往路・復路の整数座標と、途中で画像の外へ出なかったか。
pub(crate) struct Path {
    pub sy: Vec<i64>,
    pub sx: Vec<i64>,
    pub by: Vec<i64>,
    pub bx: Vec<i64>,
    pub valid: Vec<u8>,
}

/// `_FlowPath.coordinates`。
pub(crate) fn path_coordinates(
    pairs: &[std::rc::Rc<FlowPair>],
    (width, height): (usize, usize),
    ys: &[i64],
    xs: &[i64],
) -> Result<Path, Error> {
    let mut sy: Vec<f64> = ys.iter().map(|&v| v as f64).collect();
    let mut sx: Vec<f64> = xs.iter().map(|&v| v as f64).collect();
    let mut valid = vec![1u8; ys.len()];
    let shifted = |values: &[f64], origin: usize| -> Vec<f64> {
        values.iter().map(|v| v - origin as f64).collect()
    };
    for pair in pairs {
        let (dy, dx) = pair
            .forward
            .at(&shifted(&sy, pair.y0), &shifted(&sx, pair.x0))?;
        for i in 0..sy.len() {
            sy[i] += dy[i];
            sx[i] += dx[i];
            let inside =
                sy[i] >= 0.0 && sy[i] < height as f64 && sx[i] >= 0.0 && sx[i] < width as f64;
            valid[i] &= u8::from(inside);
        }
    }
    let (mut by, mut bx) = (sy.clone(), sx.clone());
    for pair in pairs.iter().rev() {
        let (dy, dx) = pair
            .backward
            .at(&shifted(&by, pair.y0), &shifted(&bx, pair.x0))?;
        for i in 0..by.len() {
            by[i] += dy[i];
            bx[i] += dx[i];
        }
    }
    let rint = |values: Vec<f64>| -> Vec<i64> {
        values
            .into_iter()
            .map(|v| v.round_ties_even() as i64)
            .collect()
    };
    Ok(Path {
        sy: rint(sy),
        sx: rint(sx),
        by: rint(by),
        bx: rint(bx),
        valid,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_moved_square_is_found() {
        let (width, height) = (96, 64);
        let square = |dx: usize| {
            let mut mask = vec![0u8; width * height];
            for y in 20..40 {
                for x in (20 + dx)..(40 + dx) {
                    if y == 20 || y == 39 || x == 20 + dx || x == 39 + dx {
                        mask[y * width + x] = 1;
                    }
                }
            }
            mask
        };
        let flow = estimate_flow(&square(0), &square(5), width, height);
        let (dy, dx) = flow.at(&[25.0], &[25.0]).unwrap();
        assert_eq!((dy[0].round(), dx[0].round()), (0.0, 4.0)); // Python の line_flow と同じ値
    }
}
