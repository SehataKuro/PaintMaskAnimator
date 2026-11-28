//! スキャンした紙の地色とムラを白へそろえる（#51、Python 版 `scan_paper.flatten_paper`）。
//!
//! 場所ごとの紙の色（ブロックごとに明るい側の色）を見積もり、その色で割って白にそろえる。
//! 計算は numpy と同じ順で f64 で行う。ブロックの中の明るさの順位は numpy の
//! `argsort`（安定でない並べ替え）と違い安定に並べるので、明るさの同じ違う色が並ぶ所では
//! 選ぶ画素が違うことがある（灰色の紙では同じ色になる）。

/// 紙の色を見積もるブロックの一辺（px）。
pub const BLOCK: usize = 24;
/// ブロックの中で紙とみなす明るさの順位（上位から）。
pub const PAPER_QUANTILE: f64 = 0.9;
/// 紙の色をならすブロックの数（半径）。
pub const SMOOTH_RADIUS: usize = 2;
/// これより暗い所は紙ではない。
pub const MIN_PAPER: f64 = 96.0;

type Rgb = [f64; 3];

fn blur_blocks(values: &[Rgb], rows: usize, cols: usize, radius: usize) -> Vec<Rgb> {
    if radius == 0 {
        return values.to_vec();
    }
    let at = |r: isize, c: isize| -> Rgb {
        let r = r.clamp(0, rows as isize - 1) as usize;
        let c = c.clamp(0, cols as isize - 1) as usize;
        values[r * cols + c]
    };
    let size = 2 * radius + 1;
    let mut out = vec![[0.0; 3]; rows * cols];
    for dy in 0..size {
        for dx in 0..size {
            for r in 0..rows {
                for c in 0..cols {
                    let v = at(
                        r as isize + dy as isize - radius as isize,
                        c as isize + dx as isize - radius as isize,
                    );
                    let o = &mut out[r * cols + c];
                    for k in 0..3 {
                        o[k] += v[k];
                    }
                }
            }
        }
    }
    let n = (size * size) as f64;
    for o in &mut out {
        for v in o.iter_mut() {
            *v /= n;
        }
    }
    out
}

/// 画素ごとの紙の色の見積もり（0〜255、`paper_colour`）。`rgba` はストレート RGBA。
pub fn paper_colour(rgba: &[u8], width: usize, height: usize, block: usize) -> Vec<Rgb> {
    let rows = height.div_ceil(block);
    let cols = width.div_ceil(block);
    let pick = ((block * block - 1) as f64 * PAPER_QUANTILE).round_ties_even() as usize;
    let mut blocks = vec![[0.0; 3]; rows * cols];
    let mut tile: Vec<(f64, Rgb)> = Vec::with_capacity(block * block);
    for r in 0..rows {
        for c in 0..cols {
            tile.clear();
            for y in 0..block {
                let sy = (r * block + y).min(height - 1);
                for x in 0..block {
                    let sx = (c * block + x).min(width - 1);
                    let i = (sy * width + sx) * 4;
                    let rgb = [rgba[i] as f64, rgba[i + 1] as f64, rgba[i + 2] as f64];
                    tile.push((rgb[0] + rgb[1] + rgb[2], rgb));
                }
            }
            tile.sort_by(|a, b| a.0.total_cmp(&b.0));
            blocks[r * cols + c] = tile[pick].1;
        }
    }
    let dark: Vec<bool> = blocks
        .iter()
        .map(|b| b.iter().copied().fold(f64::INFINITY, f64::min) < MIN_PAPER)
        .collect();
    if dark.iter().all(|&d| d) {
        return vec![[255.0; 3]; width * height];
    }
    if dark.iter().any(|&d| d) {
        let mut sum = [0.0; 3];
        let mut count = 0usize;
        for (b, &d) in blocks.iter().zip(&dark) {
            if !d {
                for k in 0..3 {
                    sum[k] += b[k];
                }
                count += 1;
            }
        }
        let mean = sum.map(|s| s / count as f64);
        for (b, &d) in blocks.iter_mut().zip(&dark) {
            if d {
                *b = mean;
            }
        }
    }
    let blocks = blur_blocks(&blocks, rows, cols, SMOOTH_RADIUS);
    // ブロックの中心を結ぶ双一次補間（`_upsample`）。
    let axis = |n: usize, count: usize| -> Vec<(usize, usize, f64)> {
        (0..n)
            .map(|i| {
                let v = (i as f64 + 0.5) / block as f64 - 0.5;
                let f = v.floor();
                let i0 = (f as i64).clamp(0, count as i64 - 1) as usize;
                let i1 = (i0 + 1).min(count - 1);
                (i0, i1, (v - f).clamp(0.0, 1.0))
            })
            .collect()
    };
    let ys = axis(height, rows);
    let xs = axis(width, cols);
    let mut out = Vec::with_capacity(width * height);
    for &(y0, y1, wy) in &ys {
        for &(x0, x1, wx) in &xs {
            let b = |r: usize, c: usize| blocks[r * cols + c];
            out.push(std::array::from_fn(|k| {
                let top = b(y0, x0)[k] * (1.0 - wx) + b(y0, x1)[k] * wx;
                let bottom = b(y1, x0)[k] * (1.0 - wx) + b(y1, x1)[k] * wx;
                top * (1.0 - wy) + bottom * wy
            }));
        }
    }
    out
}

/// 紙の地色とムラを白（255）へそろえた RGBA（`flatten_paper`）。透明度は変えない。
pub fn flatten_paper(rgba: &[u8], width: usize, height: usize) -> Vec<u8> {
    if width == 0 || height == 0 {
        return rgba.to_vec();
    }
    let paper = paper_colour(rgba, width, height, BLOCK);
    let mut out = rgba.to_vec();
    for (pixel, p) in out.chunks_exact_mut(4).zip(&paper) {
        for k in 0..3 {
            let scale = 255.0 / p[k].max(1.0);
            pixel[k] = (pixel[k] as f64 * scale)
                .round_ties_even()
                .clamp(0.0, 255.0) as u8;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cream_paper_becomes_white_and_lines_stay_dark() {
        let (w, h) = (50, 30);
        let mut rgba = vec![0u8; w * h * 4];
        for (i, p) in rgba.chunks_exact_mut(4).enumerate() {
            let (x, y) = (i % w, i / w);
            let shade = 230 - (x / 10) as u8;
            p.copy_from_slice(&[shade, shade - 5, shade - 20, 255]);
            if y == 15 {
                p[..3].copy_from_slice(&[40, 40, 40]);
            }
        }
        let out = flatten_paper(&rgba, w, h);
        assert!(out[..4].iter().take(3).all(|&v| v >= 250));
        let line = (15 * w + 25) * 4;
        assert!(out[line] < 60);
        assert_eq!(out[3], 255);
    }

    #[test]
    fn all_dark_is_unchanged() {
        let rgba = [10u8, 20, 30, 255].repeat(9);
        assert_eq!(flatten_paper(&rgba, 3, 3), rgba);
    }
}
