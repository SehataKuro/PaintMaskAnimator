//! Qt のラスターエンジンの、アンチエイリアスなしの塗りと 1px 線を同じ画素で行う。
//!
//! - 塗り（`fill_path`）：`QOutlineMapper` と同じく曲線を `QBezier::addToPolygon`
//!   （しきい値 0.25）で折れ線にし、輪郭を閉じ、座標を 26.6 固定小数点へ `qRound` する。
//!   そのあと `QRasterizer` の走査変換と同じく、各行の画素の中心で辺を 16.16 固定小数点で
//!   求め、巻き数（または偶奇）で区間を塗る。
//! - 1px 線（`cosmetic_line`）：幅 1 以下のペンは `QCosmeticStroker` を通る。端点を
//!   26.6 へ切り捨て、主軸の向きに 1 画素ずつ進める。両端に半画素の延長（キャップ）を付ける。

use super::mask::Mask;
use super::qt::{fuzzy_compare, points_equal, q_round, Point};

/// 塗りの規則。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FillRule {
    /// 偶奇（`Qt::OddEvenFill`）。`QPainterPath` と `drawPolygon` の既定。
    OddEven,
    /// 巻き数が 0 でない所（`Qt::WindingFill`）。線の輪郭の塗りで使う。
    Winding,
}

/// 経路の要素（`QPainterPath` の要素と同じ）。
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum PathElement {
    /// 新しい部分経路を始める。
    MoveTo(Point),
    /// 直線でつなぐ。
    LineTo(Point),
    /// 3 次ベジエ曲線でつなぐ（制御点 2 つと終点）。
    CubicTo(Point, Point, Point),
}

/// 直線と 3 次ベジエ曲線からなる経路。
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Path {
    elements: Vec<PathElement>,
    start: Option<Point>,
}

impl Path {
    /// 空の経路。
    pub fn new() -> Self {
        Self::default()
    }

    /// 要素の並び。
    pub fn elements(&self) -> &[PathElement] {
        &self.elements
    }

    /// `QPainterPath::moveTo`。
    pub fn move_to(&mut self, point: Point) {
        self.elements.push(PathElement::MoveTo(point));
        self.start = Some(point);
    }

    /// `QPainterPath::lineTo`。先に点が無ければ原点から始める。直前の点と（あいまいに）
    /// 等しい点は足さない。
    pub fn line_to(&mut self, point: Point) {
        self.ensure_start();
        if self
            .last_point()
            .is_some_and(|last| points_equal(last, point))
        {
            return;
        }
        self.elements.push(PathElement::LineTo(point));
    }

    /// `QPainterPath::cubicTo`。
    pub fn cubic_to(&mut self, c1: Point, c2: Point, end: Point) {
        self.ensure_start();
        self.elements.push(PathElement::CubicTo(c1, c2, end));
    }

    /// `QPainterPath::closeSubpath`。終点が始点と違えば、ほぼ等しいなら終点を始点に寄せ、
    /// そうでなければ始点へ直線を引く。
    pub fn close(&mut self) {
        let (Some(start), Some(last)) = (self.start, self.last_point()) else {
            return;
        };
        if start.x == last.x && start.y == last.y {
            return;
        }
        if fuzzy_compare(start.x, last.x) && fuzzy_compare(start.y, last.y) {
            if let Some(element) = self.elements.last_mut() {
                match element {
                    PathElement::MoveTo(point) | PathElement::LineTo(point) => *point = start,
                    PathElement::CubicTo(_, _, end) => *end = start,
                }
            }
        } else {
            self.elements.push(PathElement::LineTo(start));
        }
    }

    /// 折れ線（`QPolygonF`）から経路を作る（`QPainterPath::addPolygon`、同じ点も残す）。閉じない。
    pub fn from_polygon(points: &[Point]) -> Self {
        let mut path = Self::new();
        if let Some((&first, rest)) = points.split_first() {
            path.move_to(first);
            for &point in rest {
                path.elements.push(PathElement::LineTo(point));
            }
        }
        path
    }

    fn ensure_start(&mut self) {
        if self.elements.is_empty() {
            self.move_to(Point::new(0.0, 0.0));
        }
    }

    fn last_point(&self) -> Option<Point> {
        self.elements.last().map(|element| match *element {
            PathElement::MoveTo(point) | PathElement::LineTo(point) => point,
            PathElement::CubicTo(_, _, end) => end,
        })
    }

    /// 制御点を含む外接矩形 `(left, top, right, bottom)`。空なら `None`。
    pub fn control_point_bounds(&self) -> Option<(f64, f64, f64, f64)> {
        let mut bounds: Option<(f64, f64, f64, f64)> = None;
        let mut add = |point: Point| {
            bounds = Some(match bounds {
                None => (point.x, point.y, point.x, point.y),
                Some((l, t, r, b)) => (
                    l.min(point.x),
                    t.min(point.y),
                    r.max(point.x),
                    b.max(point.y),
                ),
            });
        };
        for element in &self.elements {
            match *element {
                PathElement::MoveTo(point) | PathElement::LineTo(point) => add(point),
                PathElement::CubicTo(c1, c2, end) => {
                    add(c1);
                    add(c2);
                    add(end);
                }
            }
        }
        bounds
    }

    /// 全体を平行移動した経路。
    pub fn translated(&self, dx: f64, dy: f64) -> Self {
        let shift = |point: Point| Point::new(point.x + dx, point.y + dy);
        Self {
            elements: self
                .elements
                .iter()
                .map(|element| match *element {
                    PathElement::MoveTo(point) => PathElement::MoveTo(shift(point)),
                    PathElement::LineTo(point) => PathElement::LineTo(shift(point)),
                    PathElement::CubicTo(c1, c2, end) => {
                        PathElement::CubicTo(shift(c1), shift(c2), shift(end))
                    }
                })
                .collect(),
            start: self.start.map(shift),
        }
    }

    pub(crate) fn push(&mut self, element: PathElement) {
        if let PathElement::MoveTo(point) = element {
            self.start = Some(point);
        }
        self.elements.push(element);
    }
}

/// `QBezier::addToPolygon`。終点だけを順に足す（始点は足さない）。
pub(crate) fn flatten_cubic(
    p1: Point,
    p2: Point,
    p3: Point,
    p4: Point,
    threshold: f64,
    output: &mut Vec<Point>,
) {
    let mut stack: Vec<([Point; 4], u32)> = vec![([p1, p2, p3, p4], 9)];
    while let Some(&([b1, b2, b3, b4], level)) = stack.last() {
        let y4y1 = b4.y - b1.y;
        let x4x1 = b4.x - b1.x;
        let mut l = x4x1.abs() + y4y1.abs();
        let d = if l > 1.0 {
            ((x4x1) * (b1.y - b2.y) - (y4y1) * (b1.x - b2.x)).abs()
                + ((x4x1) * (b1.y - b3.y) - (y4y1) * (b1.x - b3.x)).abs()
        } else {
            l = 1.0;
            (b1.x - b2.x).abs() + (b1.y - b2.y).abs() + (b1.x - b3.x).abs() + (b1.y - b3.y).abs()
        };
        if d < threshold * l || level == 0 {
            output.push(b4);
            stack.pop();
        } else {
            let mid = |a: Point, b: Point| Point::new((a.x + b.x) * 0.5, (a.y + b.y) * 0.5);
            let m12 = mid(b1, b2);
            let m23 = mid(b2, b3);
            let m34 = mid(b3, b4);
            let m1223 = mid(m12, m23);
            let m2334 = mid(m23, m34);
            let center = mid(m1223, m2334);
            // 後半を下に残し、前半を先に処理する。
            let top = stack.len() - 1;
            stack[top] = ([center, m2334, m34, b4], level - 1);
            stack.push(([b1, m12, m1223, center], level - 1));
        }
    }
}

/// `QRectF::intersects`。幅か高さが 0 の矩形はどれとも交わらない。
fn rects_intersect(a: (f64, f64, f64, f64), b: (f64, f64, f64, f64)) -> bool {
    let (l1, t1, r1, b1) = a;
    let (l2, t2, r2, b2) = b;
    if l1 == r1 || l2 == r2 || t1 == b1 || t2 == b2 {
        return false;
    }
    !(l1 >= r2 || l2 >= r1 || t1 >= b2 || t2 >= b1)
}

/// `QOutlineMapper::convertPath` と同じく、26.6 固定小数点の閉じた輪郭の並びにする。
fn outline(path: &Path, width: usize, height: usize) -> Vec<Vec<(i64, i64)>> {
    let clip = (0.0, 0.0, width as f64, height as f64);
    let mut contours: Vec<Vec<Point>> = Vec::new();
    let mut current: Vec<Point> = Vec::new();
    let close = |current: &mut Vec<Point>| {
        if let (Some(&first), Some(&last)) = (current.first(), current.last()) {
            if !points_equal(last, first) {
                current.push(first);
            }
        }
    };
    let count = path.elements.len();
    for (index, element) in path.elements.iter().enumerate() {
        match *element {
            PathElement::MoveTo(point) => {
                // 最後の要素の moveTo は読み飛ばす。
                if index + 1 == count {
                    continue;
                }
                close(&mut current);
                if !current.is_empty() {
                    contours.push(std::mem::take(&mut current));
                }
                current.push(point);
            }
            PathElement::LineTo(point) => current.push(point),
            PathElement::CubicTo(c1, c2, end) => {
                let Some(&start) = current.last() else {
                    continue;
                };
                let inside =
                    end.x >= clip.0 && end.x <= clip.2 && end.y >= clip.1 && end.y <= clip.3;
                let mut outside = false;
                if !inside {
                    let xs = [start.x, c1.x, c2.x, end.x];
                    let ys = [start.y, c1.y, c2.y, end.y];
                    let bounds = (
                        xs.iter().copied().fold(f64::INFINITY, f64::min),
                        ys.iter().copied().fold(f64::INFINITY, f64::min),
                        xs.iter().copied().fold(f64::NEG_INFINITY, f64::max),
                        ys.iter().copied().fold(f64::NEG_INFINITY, f64::max),
                    );
                    outside = !rects_intersect(bounds, clip);
                }
                if outside {
                    current.push(end);
                } else {
                    flatten_cubic(start, c1, c2, end, 0.25, &mut current);
                }
            }
        }
    }
    close(&mut current);
    if !current.is_empty() {
        contours.push(current);
    }
    contours
        .into_iter()
        .map(|contour| {
            contour
                .into_iter()
                .map(|point| (q_round(point.x * 64.0), q_round(point.y * 64.0)))
                .collect()
        })
        .collect()
}

#[derive(Clone, Copy, Debug)]
struct Edge {
    x: i64,
    delta: i64,
    top: i64,
    bottom: i64,
    winding: i32,
}

/// `QScanConverter`。クリップ矩形は `(0, 0, width, height)`。
struct ScanConverter {
    top: i64,
    bottom: i64,
    left_fp: i64,
    right_fp: i64,
    edges: Vec<Edge>,
}

impl ScanConverter {
    fn clip(
        &mut self,
        x: &mut i64,
        top: &mut i64,
        bottom: &mut i64,
        slope: i64,
        edge: i64,
        winding: i32,
    ) -> bool {
        let right = edge == self.right_fp;
        let vertical = |top, bottom| Edge {
            x: edge,
            delta: 0,
            top,
            bottom,
            winding,
        };
        if *x == edge {
            if (slope > 0) ^ right {
                return false;
            }
            self.edges.push(vertical(*top, *bottom));
            return true;
        }
        let last = *x + slope * (*bottom - *top);
        if last == edge {
            if (slope < 0) ^ right {
                return false;
            }
            self.edges.push(vertical(*top, *bottom));
            return true;
        }
        if (last < edge) ^ (*x < edge) {
            let delta_y = ((edge - *x) as f64 / (slope as f64 * (1.0 / 65536.0))) as i64;
            if (*x < edge) ^ right {
                let height = (delta_y + 1) >> 16;
                let middle = *top + height;
                self.edges.push(vertical(*top, middle));
                if middle != *bottom {
                    *x += slope * (height + 1);
                    *top = middle + 1;
                } else {
                    return true;
                }
            } else {
                let height = delta_y >> 16;
                let middle = *top + height;
                if middle != *bottom {
                    self.edges.push(vertical(middle + 1, *bottom));
                    *bottom = middle;
                }
            }
            return false;
        } else if (*x < edge) ^ right {
            self.edges.push(vertical(*top, *bottom));
            return true;
        }
        false
    }

    fn merge_line(&mut self, mut a: (i64, i64), mut b: (i64, i64)) {
        let mut winding = 1;
        if a.1 > b.1 {
            std::mem::swap(&mut a, &mut b);
            winding = -1;
        }
        let mut top = self.top.max((a.1 + 32) >> 6);
        let mut bottom = self.bottom.min((b.1 - 32) >> 6);
        if top > bottom {
            return;
        }
        let a_fp = 32768 + (a.0 << 10);
        if b.0 == a.0 {
            self.edges.push(Edge {
                x: a_fp.clamp(self.left_fp, self.right_fp),
                delta: 0,
                top,
                bottom,
                winding,
            });
            return;
        }
        let slope = (b.0 - a.0) as f64 / (b.1 - a.1) as f64;
        let slope_fp = (slope * 65536.0) as i64;
        let mut x = a_fp + ((slope_fp * ((top << 16) + 32768 - (a.1 << 10))) >> 16);
        let (left, right) = (self.left_fp, self.right_fp);
        if self.clip(&mut x, &mut top, &mut bottom, slope_fp, left, winding)
            || self.clip(&mut x, &mut top, &mut bottom, slope_fp, right, winding)
        {
            return;
        }
        self.edges.push(Edge {
            x,
            delta: slope_fp,
            top,
            bottom,
            winding,
        });
    }

    fn emit(mut self, rule: FillRule, mask: &mut Mask) {
        let rule_mask: i32 = match rule {
            FillRule::Winding => !0,
            FillRule::OddEven => 1,
        };
        self.edges.sort_by_key(|edge| edge.top);
        let Some(first) = self.edges.first() else {
            return;
        };
        let mut next = 0;
        let mut active: Vec<Edge> = Vec::new();
        let mut crossings: Vec<(i64, i32)> = Vec::new();
        let mut y = first.top;
        while y <= self.bottom {
            while next < self.edges.len() && self.edges[next].top == y {
                active.push(self.edges[next]);
                next += 1;
            }
            if active.is_empty() && next >= self.edges.len() {
                break;
            }
            crossings.clear();
            crossings.extend(active.iter().map(|edge| (edge.x >> 16, edge.winding)));
            crossings.sort_by_key(|&(x, _)| x);
            let (mut x, mut winding) = (0i64, 0i32);
            for &(current, edge_winding) in &crossings {
                if winding & rule_mask != 0 {
                    mask.fill_span(y as usize, x, current);
                }
                x = current;
                winding += edge_winding;
            }
            active.retain_mut(|edge| {
                if edge.bottom == y {
                    false
                } else {
                    edge.x += edge.delta;
                    true
                }
            });
            y += 1;
        }
    }
}

/// `QPainter` で非 AA の塗りをした画素を返す。描く先は `(0, 0, width, height)` の画像。
pub fn fill_path(path: &Path, rule: FillRule, width: usize, height: usize) -> Mask {
    let mut mask = Mask::new(0, 0, width, height);
    if width == 0 || height == 0 {
        return mask;
    }
    let contours = outline(path, width, height);
    let points: usize = contours.iter().map(Vec::len).sum();
    if points < 3 {
        return mask;
    }
    let min_y = contours.iter().flatten().map(|p| p.1).min().unwrap_or(0);
    let max_y = contours.iter().flatten().map(|p| p.1).max().unwrap_or(0);
    let top = ((min_y + 32) >> 6).max(0);
    let bottom = ((max_y - 32) >> 6).min(height as i64 - 1);
    if top > bottom {
        return mask;
    }
    let mut converter = ScanConverter {
        top,
        bottom,
        left_fp: 0,
        right_fp: (width as i64) << 16,
        edges: Vec::new(),
    };
    for contour in &contours {
        for pair in contour.windows(2) {
            converter.merge_line(pair[0], pair[1]);
        }
    }
    converter.emit(rule, &mut mask);
    mask
}

/// `QCosmeticStroker` の非 AA・実線・両端キャップつきの 1px 線。描く先は
/// `(0, 0, width, height)` の画像。始点と終点が（あいまいに）等しければ 1 点を打つ。
pub fn cosmetic_line(p1: Point, p2: Point, width: usize, height: usize) -> Mask {
    let mut mask = Mask::new(0, 0, width, height);
    if width == 0 || height == 0 {
        return mask;
    }
    if points_equal(p1, p2) {
        mask.set_local(p1.x.floor() as i64, p1.y.floor() as i64);
        return mask;
    }
    let Some((rx1, ry1, rx2, ry2)) = clip_line(p1, p2, width, height) else {
        return mask;
    };
    let to_26_6 = |value: f64| (value * 64.0) as i64;
    let (mut x1, mut y1, mut x2, mut y2) = (to_26_6(rx1), to_26_6(ry1), to_26_6(rx2), to_26_6(ry2));
    let (dx, dy) = ((x2 - x1).abs(), (y2 - y1).abs());
    if dx < dy {
        if y1 > y2 {
            std::mem::swap(&mut y1, &mut y2);
            std::mem::swap(&mut x1, &mut x2);
        }
        let xinc = ((x2 - x1) * 65536) / (y2 - y1);
        let mut x = x1 << 10;
        y1 -= 32;
        x -= xinc >> 1;
        y2 += 32;
        let mut y = (y1 + 32) >> 6;
        let ys = (y2 + 32) >> 6;
        let round = if xinc > 0 { 32 } else { 0 };
        if y != ys {
            x += (((y << 6) + round - y1) * xinc) >> 6;
            loop {
                mask.set_local(x >> 16, y);
                x += xinc;
                y += 1;
                if y >= ys {
                    break;
                }
            }
        }
    } else {
        if dx == 0 {
            return mask;
        }
        if x1 > x2 {
            std::mem::swap(&mut x1, &mut x2);
            std::mem::swap(&mut y1, &mut y2);
        }
        let yinc = ((y2 - y1) * 65536) / (x2 - x1);
        let mut y = y1 << 10;
        x1 -= 32;
        y -= yinc >> 1;
        x2 += 32;
        let mut x = (x1 + 32) >> 6;
        let xs = (x2 + 32) >> 6;
        let round = if yinc > 0 { 32 } else { 0 };
        if x != xs {
            y += (((x << 6) + round - x1) * yinc) >> 6;
            loop {
                mask.set_local(x, y >> 16);
                y += yinc;
                x += 1;
                if x >= xs {
                    break;
                }
            }
        }
    }
    mask
}

/// `QCosmeticStroker::clipLine`。描く先の 1 画素外までを浮動小数点で切る。
fn clip_line(p1: Point, p2: Point, width: usize, height: usize) -> Option<(f64, f64, f64, f64)> {
    let (mut x1, mut y1, mut x2, mut y2) = (p1.x, p1.y, p2.x, p2.y);
    if !(x1.is_finite() && y1.is_finite() && x2.is_finite() && y2.is_finite()) {
        return None;
    }
    let (xmin, xmax) = (-1.0, width as f64 + 1.0);
    let (ymin, ymax) = (-1.0, height as f64 + 1.0);
    if x1 < xmin {
        if x2 <= xmin {
            return None;
        }
        y1 += (y2 - y1) / (x2 - x1) * (xmin - x1);
        x1 = xmin;
    } else if x1 > xmax {
        if x2 >= xmax {
            return None;
        }
        y1 += (y2 - y1) / (x2 - x1) * (xmax - x1);
        x1 = xmax;
    }
    if x2 < xmin {
        y2 += (y2 - y1) / (x2 - x1) * (xmin - x2);
        x2 = xmin;
    } else if x2 > xmax {
        y2 += (y2 - y1) / (x2 - x1) * (xmax - x2);
        x2 = xmax;
    }
    if y1 < ymin {
        if y2 <= ymin {
            return None;
        }
        x1 += (x2 - x1) / (y2 - y1) * (ymin - y1);
        y1 = ymin;
    } else if y1 > ymax {
        if y2 >= ymax {
            return None;
        }
        x1 += (x2 - x1) / (y2 - y1) * (ymax - y1);
        y1 = ymax;
    }
    if y2 < ymin {
        x2 += (x2 - x1) / (y2 - y1) * (ymin - y2);
        y2 = ymin;
    } else if y2 > ymax {
        x2 += (x2 - x1) / (y2 - y1) * (ymax - y2);
        y2 = ymax;
    }
    Some((x1, y1, x2, y2))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rectangle_covers_pixel_centres() {
        let path = Path::from_polygon(&[
            Point::new(1.0, 1.0),
            Point::new(4.0, 1.0),
            Point::new(4.0, 3.0),
            Point::new(1.0, 3.0),
        ]);
        let mask = fill_path(&path, FillRule::OddEven, 6, 5);
        assert_eq!(mask.runs(), vec![(1, 1, 4), (2, 1, 4)]);
    }

    #[test]
    fn half_pixel_boundary_is_rounded_like_qt() {
        // 画素の中心ちょうどの左端は含み、右端は含まない。
        let path = Path::from_polygon(&[
            Point::new(0.5, 0.0),
            Point::new(2.5, 0.0),
            Point::new(2.5, 1.0),
            Point::new(0.5, 1.0),
        ]);
        assert_eq!(
            fill_path(&path, FillRule::Winding, 4, 1).runs(),
            vec![(0, 1, 3)]
        );
    }

    #[test]
    fn odd_even_leaves_a_hole() {
        let mut inner = Path::from_polygon(&[
            Point::new(2.0, 2.0),
            Point::new(4.0, 2.0),
            Point::new(4.0, 4.0),
            Point::new(2.0, 4.0),
        ]);
        inner.close();
        let mut both = Path::from_polygon(&[
            Point::new(0.0, 0.0),
            Point::new(6.0, 0.0),
            Point::new(6.0, 6.0),
            Point::new(0.0, 6.0),
        ]);
        both.close();
        for element in inner.elements() {
            both.push(*element);
        }
        let odd = fill_path(&both, FillRule::OddEven, 6, 6);
        assert!(!odd.get(3, 3) && odd.get(1, 1));
        let wind = fill_path(&both, FillRule::Winding, 6, 6);
        assert!(wind.get(3, 3));
    }

    #[test]
    fn cosmetic_line_extends_half_a_pixel_at_both_ends() {
        let mask = cosmetic_line(Point::new(1.0, 1.0), Point::new(4.0, 1.0), 8, 3);
        assert_eq!(mask.runs(), vec![(1, 1, 5)]);
        let dot = cosmetic_line(Point::new(2.7, 1.2), Point::new(2.7, 1.2), 8, 3);
        assert_eq!(dot.runs(), vec![(1, 2, 3)]);
    }
}
