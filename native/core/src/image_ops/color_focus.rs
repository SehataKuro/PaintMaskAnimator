//! 「この色を選択範囲にする」（`focus_used_color`）の画素の部分：色のマスクと、
//! マスクの輪郭（画素の境界をたどる多角形、`line_ops._mask_contours`）。
use crate::image::{Rect, RgbaImage};
use std::collections::{BTreeSet, HashMap};

/// 不透明度が 0 でない、RGB が `rgb` の画素があるか（`_image_contains_rgb`）。
pub fn contains_rgb(image: &RgbaImage, rgb: [u8; 3]) -> bool {
    image
        .rgba()
        .chunks_exact(4)
        .any(|pixel| pixel[3] > 0 && pixel[..3] == rgb)
}

/// 不透明度が 0 でない、RGB が `rgb` の画素のマスク（行優先、1 が真）。
pub fn color_mask(image: &RgbaImage, rgb: [u8; 3]) -> Vec<u8> {
    image
        .rgba()
        .chunks_exact(4)
        .map(|pixel| u8::from(pixel[3] > 0 && pixel[..3] == rgb))
        .collect()
}

/// マスクの真の画素を囲む矩形。
pub fn mask_bounds(mask: &[u8], width: usize) -> Option<Rect> {
    if width == 0 {
        return None;
    }
    let (mut x0, mut y0, mut x1, mut y1) = (usize::MAX, usize::MAX, 0, 0);
    for (index, _) in mask.iter().enumerate().filter(|(_, &v)| v != 0) {
        let (x, y) = (index % width, index / width);
        x0 = x0.min(x);
        y0 = y0.min(y);
        x1 = x1.max(x);
        y1 = y1.max(y);
    }
    (x0 != usize::MAX).then(|| Rect {
        x: x0,
        y: y0,
        width: x1 - x0 + 1,
        height: y1 - y0 + 1,
    })
}

type Point = (i64, i64);

fn direction(from: Point, to: Point) -> i32 {
    match (to.0 - from.0, to.1 - from.1) {
        (1, 0) => 0,
        (0, 1) => 1,
        (-1, 0) => 2,
        _ => 3,
    }
}

/// 2 値マスクの外周と穴の輪郭（頂点は画素の角、直線の途中の点は除く）。
///
/// 辺を時計回りに向け、まだ使っていない一番小さい辺から、右折・直進・左折・後退の
/// 順に辺をたどる。Python 版と同じ順番・同じ頂点の多角形を返す。
pub fn mask_contours(mask: &[u8], width: usize, height: usize) -> Vec<Vec<Point>> {
    if width == 0 || mask.len() != width * height || !mask.iter().any(|&v| v != 0) {
        return Vec::new();
    }
    let at = |x: usize, y: usize| mask[y * width + x] != 0;
    let mut edges: BTreeSet<(Point, Point)> = BTreeSet::new();
    for y in 0..height {
        for x in 0..width {
            if !at(x, y) {
                continue;
            }
            let (xi, yi) = (x as i64, y as i64);
            if y == 0 || !at(x, y - 1) {
                edges.insert(((xi, yi), (xi + 1, yi)));
            }
            if x + 1 == width || !at(x + 1, y) {
                edges.insert(((xi + 1, yi), (xi + 1, yi + 1)));
            }
            if y + 1 == height || !at(x, y + 1) {
                edges.insert(((xi + 1, yi + 1), (xi, yi + 1)));
            }
            if x == 0 || !at(x - 1, y) {
                edges.insert(((xi, yi + 1), (xi, yi)));
            }
        }
    }
    let mut outgoing: HashMap<Point, Vec<Point>> = HashMap::new();
    for &(start, end) in &edges {
        outgoing.entry(start).or_default().push(end);
    }
    let mut unused = edges;
    let mut contours = Vec::new();
    while let Some(start_edge) = unused.pop_first() {
        let (start, mut current) = start_edge;
        let mut contour = vec![start, current];
        let mut previous = start;
        while current != start {
            let previous_direction = direction(previous, current);
            let next = outgoing.get(&current).and_then(|ends| {
                ends.iter()
                    .copied()
                    .filter(|&end| unused.contains(&(current, end)))
                    .min_by_key(|&end| {
                        let turn = (direction(current, end) - previous_direction).rem_euclid(4);
                        let priority = match turn {
                            1 => 0,
                            0 => 1,
                            3 => 2,
                            _ => 3,
                        };
                        (priority, end)
                    })
            });
            let Some(next) = next else {
                contour.clear();
                break;
            };
            unused.remove(&(current, next));
            previous = current;
            current = next;
            contour.push(current);
        }
        if contour.len() >= 4 && contour.last() == contour.first() {
            contour.pop();
            contours.push(contour);
        }
    }
    contours
        .into_iter()
        .filter_map(|contour| {
            let n = contour.len();
            let simplified: Vec<Point> = (0..n)
                .filter(|&index| {
                    let previous = contour[(index + n - 1) % n];
                    let point = contour[index];
                    let following = contour[(index + 1) % n];
                    !((previous.0 == point.0 && point.0 == following.0)
                        || (previous.1 == point.1 && point.1 == following.1))
                })
                .map(|index| contour[index])
                .collect();
            (simplified.len() >= 3).then_some(simplified)
        })
        .collect()
}

fn area2(points: &[Point]) -> i64 {
    let n = points.len();
    (0..n)
        .map(|i| {
            let (x1, y1) = points[i];
            let (x2, y2) = points[(i + 1) % n];
            x1 * y2 - x2 * y1
        })
        .sum::<i64>()
        .abs()
}

/// 面積がいちばん大きい輪郭（同じなら先の輪郭）の添字。
pub fn largest_contour(contours: &[Vec<Point>]) -> Option<usize> {
    let mut best: Option<(usize, i64)> = None;
    for (index, contour) in contours.iter().enumerate() {
        let area = area2(contour);
        if best.is_none_or(|(_, best_area)| area > best_area) {
            best = Some((index, area));
        }
    }
    best.map(|(index, _)| index)
}

/// 色を選択範囲にするときの形：マスク・輪郭・いちばん大きい輪郭・囲む矩形。
#[derive(Clone, Debug)]
pub struct ColorSelection {
    /// 色のマスク（行優先、1 が真）。
    pub mask: Vec<u8>,
    /// すべての輪郭（外周と穴）。
    pub contours: Vec<Vec<Point>>,
    /// 選択の多角形にする輪郭の添字。
    pub largest: usize,
    /// マスクを囲む矩形。
    pub bounds: Rect,
}

/// `focus_used_color` の選択範囲。色が無ければ `None`。
pub fn color_selection(image: &RgbaImage, rgb: [u8; 3]) -> Option<ColorSelection> {
    let mask = color_mask(image, rgb);
    let contours = mask_contours(&mask, image.width(), image.height());
    let largest = largest_contour(&contours)?;
    let bounds = mask_bounds(&mask, image.width())?;
    Some(ColorSelection {
        mask,
        contours,
        largest,
        bounds,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn square_with_hole() {
        #[rustfmt::skip]
        let mask = [
            1, 1, 1,
            1, 0, 1,
            1, 1, 1,
        ];
        let contours = mask_contours(&mask, 3, 3);
        assert_eq!(contours.len(), 2);
        assert_eq!(contours[0], [(0, 0), (3, 0), (3, 3), (0, 3)]);
        assert_eq!(contours[1].len(), 4);
        assert_eq!(largest_contour(&contours), Some(0));
        assert_eq!(
            mask_bounds(&mask, 3),
            Some(Rect {
                x: 0,
                y: 0,
                width: 3,
                height: 3
            })
        );
    }

    #[test]
    fn empty_mask() {
        assert!(mask_contours(&[0, 0], 2, 1).is_empty());
        assert_eq!(largest_contour(&[]), None);
    }
}
