//! 二値マスクの輪郭（選択範囲の点線を引く折れ線）。
//!
//! `main_window_line_ops._mask_contours` と同じ順・同じ点を返す。画素の境界を
//! 時計回り（y は下向き）に辿る外周と、反時計回りの穴の輪郭を、まっすぐ続く
//! 途中の点を省いて返す。

use std::collections::{BTreeSet, HashMap};

/// 輪郭の頂点（画素の角。整数）。
pub type Corner = (i64, i64);

type Edge = (Corner, Corner);

/// 方向（右・下・左・上）の番号。Python の `direction_index`。
fn direction(from: Corner, to: Corner) -> i64 {
    match (to.0 - from.0, to.1 - from.1) {
        (1, 0) => 0,
        (0, 1) => 1,
        (-1, 0) => 2,
        _ => 3,
    }
}

/// 曲がり方の優先順（右折・直進・左折・折り返しの順）。
fn turn_priority(turn: i64) -> u8 {
    match turn.rem_euclid(4) {
        1 => 0,
        0 => 1,
        3 => 2,
        _ => 3,
    }
}

/// `mask`（`width * height` バイト、0 以外が選択）の輪郭を返す。
///
/// 外周も穴も、閉じた折れ線（最後の点から最初の点へ戻る）として返す。
/// `mask` の長さが寸法と合わなければ空を返す。
pub fn mask_contours(mask: &[u8], width: usize, height: usize) -> Vec<Vec<Corner>> {
    if width.checked_mul(height) != Some(mask.len()) {
        return Vec::new();
    }
    let at = |x: usize, y: usize| mask[y * width + x] != 0;
    let mut edges: BTreeSet<Edge> = BTreeSet::new();
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
    let mut outgoing: HashMap<Corner, Vec<Corner>> = HashMap::new();
    for &(start, end) in &edges {
        outgoing.entry(start).or_default().push(end);
    }

    let mut contours = Vec::new();
    while let Some(start_edge) = edges.pop_first() {
        let (start, mut current) = start_edge;
        let mut contour = vec![start, current];
        let mut previous = start;
        let mut closed = true;
        while current != start {
            let previous_direction = direction(previous, current);
            let next = outgoing
                .get(&current)
                .into_iter()
                .flatten()
                .copied()
                .filter(|&end| edges.contains(&(current, end)))
                .min_by_key(|&end| {
                    let turn = direction(current, end) - previous_direction;
                    (turn_priority(turn), end)
                });
            let Some(next) = next else {
                closed = false;
                break;
            };
            edges.remove(&(current, next));
            previous = current;
            current = next;
            contour.push(current);
        }
        if closed && contour.len() >= 4 && contour.last() == contour.first() {
            contour.pop();
            contours.push(contour);
        }
    }

    contours
        .into_iter()
        .filter_map(|contour| {
            let count = contour.len();
            let simplified: Vec<Corner> = (0..count)
                .filter(|&index| {
                    let previous = contour[(index + count - 1) % count];
                    let point = contour[index];
                    let following = contour[(index + 1) % count];
                    !((previous.0 == point.0 && point.0 == following.0)
                        || (previous.1 == point.1 && point.1 == following.1))
                })
                .map(|index| contour[index])
                .collect();
            (simplified.len() >= 3).then_some(simplified)
        })
        .collect()
}

/// 囲む面積が最大の輪郭の番号（同じ面積なら先の方）。`_largest_contour` と同じ。
pub fn largest_contour(contours: &[Vec<Corner>]) -> Option<usize> {
    let area = |points: &[Corner]| {
        let count = points.len();
        let twice: i64 = (0..count)
            .map(|index| {
                let (x1, y1) = points[index];
                let (x2, y2) = points[(index + 1) % count];
                x1 * y2 - x2 * y1
            })
            .sum();
        twice.abs()
    };
    let mut best: Option<(usize, i64)> = None;
    for (index, contour) in contours.iter().enumerate() {
        let value = area(contour);
        if best.is_none_or(|(_, best_value)| value > best_value) {
            best = Some((index, value));
        }
    }
    best.map(|(index, _)| index)
}
