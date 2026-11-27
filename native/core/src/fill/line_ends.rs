//! 線端の検出とキャンバス端への延長。
const TRACE_LENGTH: usize = 12;
const MIN_TRACE: usize = 6;
type Point = (usize, usize);

/// 帯の座標と行優先の細線化済みマスク。入口で寸法と長さを検証する。
pub struct Strip<'a> {
    pub y0: usize,
    pub y1: usize,
    pub x0: usize,
    pub x1: usize,
    pub skeleton: &'a [u8],
}

fn neighbours(skeleton: &[u8], width: usize, height: usize, (x, y): Point) -> Vec<Point> {
    let mut result = Vec::with_capacity(8);
    for ny in y.saturating_sub(1)..=(y + 1).min(height - 1) {
        for nx in x.saturating_sub(1)..=(x + 1).min(width - 1) {
            if (nx, ny) != (x, y) && skeleton[ny * width + nx] != 0 {
                result.push((nx, ny));
            }
        }
    }
    result
}

fn trace(skeleton: &[u8], width: usize, height: usize, start: Point) -> Vec<Point> {
    let mut path = vec![start];
    let mut visited = vec![start];
    while path.len() < TRACE_LENGTH {
        let (cx, cy) = *path.last().unwrap();
        let candidates: Vec<_> = neighbours(skeleton, width, height, (cx, cy))
            .into_iter()
            .filter(|p| !visited.contains(p))
            .collect();
        if candidates.is_empty()
            || candidates.iter().any(|&(ax, ay)| {
                candidates
                    .iter()
                    .any(|&(bx, by)| ax.abs_diff(bx).max(ay.abs_diff(by)) > 1)
            })
        {
            break;
        }
        visited.extend_from_slice(&candidates);
        let mut best = candidates[0];
        let distance = |(x, y): Point| x.abs_diff(cx).pow(2) + y.abs_diff(cy).pow(2);
        for &candidate in &candidates[1..] {
            if distance(candidate) > distance(best) {
                best = candidate;
            }
        }
        path.push(best);
    }
    path
}

#[allow(clippy::too_many_arguments)]
fn cast_ray(
    boundary: &[u8],
    width: usize,
    height: usize,
    x: usize,
    y: usize,
    dx: f64,
    dy: f64,
    max_length: usize,
) -> Option<Vec<Point>> {
    let major = dx.abs().max(dy.abs());
    let (step_x, step_y) = (dx / major, dy / major);
    let mut pixels = Vec::new();
    let mut left_own_line = false;
    let steps = max_length
        .saturating_mul(2)
        .min(width.max(height).saturating_add(1));
    for t in 1..=steps {
        let px = (x as f64 + step_x * t as f64).round_ties_even();
        let py = (y as f64 + step_y * t as f64).round_ties_even();
        if px < 0.0 || py < 0.0 || px >= width as f64 || py >= height as f64 {
            return Some(pixels);
        }
        let (px, py) = (px as usize, py as usize);
        if boundary[py * width + px] != 0 {
            if left_own_line {
                return Some(pixels);
            }
            continue;
        }
        left_own_line = true;
        pixels.push((px, py));
        if pixels.len() > max_length {
            return None;
        }
    }
    None
}

/// `bucket_fill.extend_line_ends_numpy` と同じ延長画素の行優先マスクを返す。
/// 帯分割と細線化キャッシュは Python 側で管理し、入力は入口で検証済みとする。
pub fn extend_line_ends(
    boundary: &[u8],
    width: usize,
    height: usize,
    strips: &[Strip<'_>],
    max_length: usize,
) -> Vec<u8> {
    let mut extension = vec![0; boundary.len()];
    for strip in strips {
        let (sw, sh) = (strip.x1 - strip.x0, strip.y1 - strip.y0);
        for ly in 0..sh {
            for lx in 0..sw {
                if strip.skeleton[ly * sw + lx] == 0
                    || neighbours(strip.skeleton, sw, sh, (lx, ly)).len() != 1
                {
                    continue;
                }
                let (x, y) = (lx + strip.x0, ly + strip.y0);
                if x.min(y).min(width - 1 - x).min(height - 1 - y) > max_length {
                    continue;
                }
                let path = trace(strip.skeleton, sw, sh, (lx, ly));
                if path.len() < MIN_TRACE {
                    continue;
                }
                let &(inner_x, inner_y) = path.last().unwrap();
                let (dx, dy) = (lx as f64 - inner_x as f64, ly as f64 - inner_y as f64);
                if dx == 0.0 && dy == 0.0 {
                    continue;
                }
                let norm = (dx * dx + dy * dy).powf(0.5);
                let (ux, uy) = (dx / norm, dy / norm);
                let edge = |p: usize, size: usize, u: f64| {
                    if u > 0.0 {
                        (size - 1 - p) as f64 / u
                    } else if u < 0.0 {
                        p as f64 / -u
                    } else {
                        f64::INFINITY
                    }
                };
                if edge(x, width, ux).min(edge(y, height, uy)) > max_length as f64 {
                    continue;
                }
                if let Some(ray) = cast_ray(boundary, width, height, x, y, dx, dy, max_length) {
                    for (px, py) in ray {
                        extension[py * width + px] = 1;
                    }
                }
            }
        }
    }
    extension
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ties_even_and_negative_coordinates() {
        for (value, expected) in [
            (0.5_f64, 0.0),
            (1.5, 2.0),
            (-0.5, 0.0),
            (-1.5, -2.0),
            (-2.5, -2.0),
        ] {
            assert_eq!(value.round_ties_even(), expected);
        }
        assert_eq!(
            cast_ray(&[0; 12], 3, 4, 0, 0, -1.0, 2.0, 5),
            Some(vec![(0, 1)])
        );
    }
}
