//! 3 次ベジエ曲線（`QBezier`）の、線の輪郭づくりと長さに使う部分。
//!
//! qbezier.cpp・qbezier_p.h と同じ演算順で、`shifted`（半幅ずらした曲線の近似）、
//! `length`（弧の長さ）、`pointAt` などを写す。

use super::qt::{fuzzy_compare, fuzzy_is_null, Line, Point};

/// `QBezier`。
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Bezier {
    pub(crate) p1: Point,
    pub(crate) p2: Point,
    pub(crate) p3: Point,
    pub(crate) p4: Point,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ShiftResult {
    Ok,
    Discard,
    Split,
    Circle,
}

fn mid(a: Point, b: Point) -> Point {
    Point::new((a.x + b.x) * 0.5, (a.y + b.y) * 0.5)
}

fn hypot(x: f64, y: f64) -> f64 {
    x.hypot(y)
}

/// `QLineF(QPointF(), v).normalVector().unitVector().p2()`。
fn unit_normal(v: Point) -> Point {
    // normalVector：(dy, -dx)。unitVector：長さで割る。
    let (x, y) = (v.y, -v.x);
    let len = hypot(x, y);
    Point::new(x / len, y / len)
}

impl Default for Bezier {
    fn default() -> Self {
        let zero = Point::new(0.0, 0.0);
        Self::new(zero, zero, zero, zero)
    }
}

impl Bezier {
    pub(crate) fn new(p1: Point, p2: Point, p3: Point, p4: Point) -> Self {
        Self { p1, p2, p3, p4 }
    }

    /// `pointAt`（数値的に安定な形）。
    pub(crate) fn point_at(&self, t: f64) -> Point {
        let m_t = 1.0 - t;
        let along = |v1: f64, v2: f64, v3: f64, v4: f64| {
            let a = v1 * m_t + v2 * t;
            let b = v2 * m_t + v3 * t;
            let c = v3 * m_t + v4 * t;
            let a = a * m_t + b * t;
            let b = b * m_t + c * t;
            a * m_t + b * t
        };
        Point::new(
            along(self.p1.x, self.p2.x, self.p3.x, self.p4.x),
            along(self.p1.y, self.p2.y, self.p3.y, self.p4.y),
        )
    }

    /// `normalVector(t)`。
    fn normal_vector(&self, t: f64) -> Point {
        let m_t = 1.0 - t;
        let a = m_t * m_t;
        let b = t * m_t;
        let c = t * t;
        let (p1, p2, p3, p4) = (self.p1, self.p2, self.p3, self.p4);
        Point::new(
            (p2.y - p1.y) * a + (p3.y - p2.y) * b + (p4.y - p3.y) * c,
            -(p2.x - p1.x) * a - (p3.x - p2.x) * b - (p4.x - p3.x) * c,
        )
    }

    /// `bounds()`（制御点の範囲）。(x, y, 幅, 高さ)。
    fn bounds(&self) -> (f64, f64, f64, f64) {
        let range = |v1: f64, v2: f64, v3: f64, v4: f64| {
            let (mut min, mut max) = (v1, v1);
            for v in [v2, v3, v4] {
                if v < min {
                    min = v;
                } else if v > max {
                    max = v;
                }
            }
            (min, max)
        };
        let (xmin, xmax) = range(self.p1.x, self.p2.x, self.p3.x, self.p4.x);
        let (ymin, ymax) = range(self.p1.y, self.p2.y, self.p3.y, self.p4.y);
        (xmin, ymin, xmax - xmin, ymax - ymin)
    }

    /// `split()`（t = 0.5 で 2 本へ）。
    pub(crate) fn split(&self) -> (Bezier, Bezier) {
        let mid_12 = mid(self.p1, self.p2);
        let mid_23 = mid(self.p2, self.p3);
        let mid_34 = mid(self.p3, self.p4);
        let mid_12_23 = mid(mid_12, mid_23);
        let mid_23_34 = mid(mid_23, mid_34);
        let middle = mid(mid_12_23, mid_23_34);
        (
            Bezier::new(self.p1, mid_12, mid_12_23, middle),
            Bezier::new(middle, mid_23_34, mid_34, self.p4),
        )
    }

    /// `startTangent()`。
    pub(crate) fn start_tangent(&self) -> Line {
        let mut tangent = Line {
            p1: self.p1,
            p2: self.p2,
        };
        if tangent.is_null() {
            tangent = Line {
                p1: self.p1,
                p2: self.p3,
            };
        }
        if tangent.is_null() {
            tangent = Line {
                p1: self.p1,
                p2: self.p4,
            };
        }
        tangent
    }

    /// `length(error = 0.01)`。
    pub(crate) fn length(&self) -> f64 {
        let mut length = 0.0;
        self.add_if_close(&mut length, 0.01);
        length
    }

    fn add_if_close(&self, length: &mut f64, error: f64) {
        let segment = |a: Point, b: Point| Line { p1: a, p2: b }.length();
        let mut len = 0.0;
        len += segment(self.p1, self.p2);
        len += segment(self.p2, self.p3);
        len += segment(self.p3, self.p4);
        let chord = segment(self.p1, self.p4);
        if len - chord > error {
            let (first, second) = self.split();
            first.add_if_close(length, error);
            second.add_if_close(length, error);
            return;
        }
        *length += len;
    }

    /// `shifted`。半幅 `offset` ずらした曲線を近似する曲線（最大 `max_segments` 本）。
    /// `threshold` は Qt と同じく `float` で持つ。
    pub(crate) fn shifted(&self, max_segments: usize, offset: f64, threshold: f32) -> Vec<Bezier> {
        let (p1, p2, p3, p4) = (self.p1, self.p2, self.p3, self.p4);
        if fuzzy_compare(p1.x, p2.x)
            && fuzzy_compare(p1.x, p3.x)
            && fuzzy_compare(p1.x, p4.x)
            && fuzzy_compare(p1.y, p2.y)
            && fuzzy_compare(p1.y, p3.y)
            && fuzzy_compare(p1.y, p4.y)
        {
            return Vec::new();
        }
        let max_segments = max_segments - 1;
        let mut threshold = threshold;
        let mut out: Vec<Bezier>;
        let mut stack: Vec<Bezier>;
        'redo: loop {
            out = Vec::new();
            stack = vec![*self];
            while let Some(&top) = stack.last() {
                let stack_segments = stack.len();
                if stack_segments == 10 || out.len() == max_segments.wrapping_sub(stack_segments) {
                    threshold *= 1.5f32;
                    if threshold > 2.0f32 {
                        break 'redo;
                    }
                    continue 'redo;
                }
                let (result, shifted) = shift(&top, offset, f64::from(threshold));
                match result {
                    ShiftResult::Discard => {
                        stack.pop();
                    }
                    ShiftResult::Ok => {
                        out.push(shifted);
                        stack.pop();
                    }
                    ShiftResult::Circle if max_segments as i64 - out.len() as i64 >= 2 => {
                        if let Some(circle) = add_circle(&top, offset) {
                            out.extend(circle);
                        }
                        stack.pop();
                    }
                    _ => {
                        let (first, second) = top.split();
                        let last = stack.len() - 1;
                        stack[last] = second;
                        stack.push(first);
                    }
                }
            }
            return out;
        }
        // give_up：残りをそのままずらす。
        while let Some(top) = stack.pop() {
            let (result, shifted) = shift(&top, offset, f64::from(threshold));
            if matches!(result, ShiftResult::Ok | ShiftResult::Split) {
                out.push(shifted);
            }
        }
        out
    }
}

/// `good_offset`。
fn good_offset(b1: &Bezier, b2: &Bezier, offset: f64, threshold: f64) -> ShiftResult {
    let o2 = offset * offset;
    let max_dist_line = threshold * offset * offset;
    let max_dist_normal = threshold * offset;
    let divisions = 4;
    let spacing = 1.0 / divisions as f64;
    let mut t = spacing;
    for _ in 1..divisions {
        let p1 = b1.point_at(t);
        let p2 = b2.point_at(t);
        let d = (p1.x - p2.x) * (p1.x - p2.x) + (p1.y - p2.y) * (p1.y - p2.y);
        if (d - o2).abs() > max_dist_line {
            return ShiftResult::Split;
        }
        let normal = b1.normal_vector(t);
        let l = normal.x.abs() + normal.y.abs();
        if l != 0.0 {
            let d = (normal.x * (p1.y - p2.y) - normal.y * (p1.x - p2.x)).abs() / l;
            if d > max_dist_normal {
                return ShiftResult::Split;
            }
        }
        t += spacing;
    }
    ShiftResult::Ok
}

/// `shift`。結果と、ずらした曲線（Ok・Split のときだけ意味がある）。
fn shift(orig: &Bezier, offset: f64, threshold: f64) -> (ShiftResult, Bezier) {
    let (o1, o2, o3, o4) = (orig.p1, orig.p2, orig.p3, orig.p4);
    let p1_p2_equal = fuzzy_compare(o1.x, o2.x) && fuzzy_compare(o1.y, o2.y);
    let p2_p3_equal = fuzzy_compare(o2.x, o3.x) && fuzzy_compare(o2.y, o3.y);
    let p3_p4_equal = fuzzy_compare(o3.x, o4.x) && fuzzy_compare(o3.y, o4.y);
    let mut points = [Point::new(0.0, 0.0); 4];
    let mut map = [0usize; 4];
    let mut np = 0;
    points[np] = o1;
    np += 1;
    if !p1_p2_equal {
        points[np] = o2;
        np += 1;
    }
    map[1] = np - 1;
    if !p2_p3_equal {
        points[np] = o3;
        np += 1;
    }
    map[2] = np - 1;
    if !p3_p4_equal {
        points[np] = o4;
        np += 1;
    }
    map[3] = np - 1;
    if np == 1 {
        return (ShiftResult::Discard, Bezier::default());
    }
    let b = orig.bounds();
    if np == 4 && b.2 < 0.1 * offset && b.3 < 0.1 * offset {
        // Qt の式のまま（括弧の位置も同じ）。
        let l = (o1.x - o2.x) * (o1.x - o2.x)
            + (o1.y - o2.y) * (o1.y - o2.y) * (o3.x - o4.x) * (o3.x - o4.x)
            + (o3.y - o4.y) * (o3.y - o4.y);
        let dot = (o1.x - o2.x) * (o3.x - o4.x) + (o1.y - o2.y) * (o3.y - o4.y);
        if dot < 0.0 && dot * dot < 0.8 * l {
            return (ShiftResult::Circle, Bezier::default());
        }
    }
    let mut shifted = [Point::new(0.0, 0.0); 4];
    let first = Point::new(points[1].x - points[0].x, points[1].y - points[0].y);
    if hypot(first.x, first.y) == 0.0 {
        return (ShiftResult::Discard, Bezier::default());
    }
    let mut prev_normal = unit_normal(first);
    shifted[0] = Point::new(
        points[0].x + offset * prev_normal.x,
        points[0].y + offset * prev_normal.y,
    );
    for i in 1..np - 1 {
        let next = Point::new(points[i + 1].x - points[i].x, points[i + 1].y - points[i].y);
        let next_normal = unit_normal(next);
        let sum = Point::new(prev_normal.x + next_normal.x, prev_normal.y + next_normal.y);
        let r = 1.0 + prev_normal.x * next_normal.x + prev_normal.y * next_normal.y;
        if fuzzy_is_null(r) {
            shifted[i] = Point::new(
                points[i].x + offset * prev_normal.x,
                points[i].y + offset * prev_normal.y,
            );
        } else {
            let k = offset / r;
            shifted[i] = Point::new(points[i].x + k * sum.x, points[i].y + k * sum.y);
        }
        prev_normal = next_normal;
    }
    shifted[np - 1] = Point::new(
        points[np - 1].x + offset * prev_normal.x,
        points[np - 1].y + offset * prev_normal.y,
    );
    let result = Bezier::new(
        shifted[map[0]],
        shifted[map[1]],
        shifted[map[2]],
        shifted[map[3]],
    );
    if np > 2 {
        return (good_offset(orig, &result, offset, threshold), result);
    }
    (ShiftResult::Ok, result)
}

/// `addCircle`。向きが反転する小さな曲線を半円で近似する。
fn add_circle(b: &Bezier, offset: f64) -> Option<[Bezier; 2]> {
    const KAPPA: f64 = 0.5522847498;
    let mut normals = [Point::new(0.0, 0.0); 3];
    normals[0] = Point::new(b.p2.y - b.p1.y, b.p1.x - b.p2.x);
    let dist = (normals[0].x * normals[0].x + normals[0].y * normals[0].y).sqrt();
    if fuzzy_is_null(dist) {
        return None;
    }
    normals[0] = Point::new(normals[0].x / dist, normals[0].y / dist);
    normals[2] = Point::new(b.p4.y - b.p3.y, b.p3.x - b.p4.x);
    let dist = (normals[2].x * normals[2].x + normals[2].y * normals[2].y).sqrt();
    if fuzzy_is_null(dist) {
        return None;
    }
    normals[2] = Point::new(normals[2].x / dist, normals[2].y / dist);
    normals[1] = Point::new(
        b.p1.x - b.p2.x - b.p3.x + b.p4.x,
        b.p1.y - b.p2.y - b.p3.y + b.p4.y,
    );
    let divisor = -(normals[1].x * normals[1].x + normals[1].y * normals[1].y).sqrt();
    normals[1] = Point::new(normals[1].x / divisor, normals[1].y / divisor);
    let mut angles = [0.0; 2];
    let mut sign = 1.0;
    for i in 0..2 {
        let cos_a =
            (normals[i].x * normals[i + 1].x + normals[i].y * normals[i + 1].y).clamp(-1.0, 1.0);
        angles[i] = cos_a.acos() * std::f64::consts::FRAC_1_PI;
    }
    if angles[0] + angles[1] > 1.0 {
        normals[1] = Point::new(-normals[1].x, -normals[1].y);
        angles[0] = 1.0 - angles[0];
        angles[1] = 1.0 - angles[1];
        sign = -1.0;
    }
    let circle = [
        Point::new(
            b.p1.x + normals[0].x * offset,
            b.p1.y + normals[0].y * offset,
        ),
        Point::new(
            0.5 * (b.p1.x + b.p4.x) + normals[1].x * offset,
            0.5 * (b.p1.y + b.p4.y) + normals[1].y * offset,
        ),
        Point::new(
            b.p4.x + normals[2].x * offset,
            b.p4.y + normals[2].y * offset,
        ),
    ];
    let mut out = [Bezier::default(); 2];
    for i in 0..2 {
        let kappa = 2.0 * KAPPA * sign * offset * angles[i];
        out[i] = Bezier::new(
            circle[i],
            Point::new(
                circle[i].x - normals[i].y * kappa,
                circle[i].y + normals[i].x * kappa,
            ),
            Point::new(
                circle[i + 1].x + normals[i + 1].y * kappa,
                circle[i + 1].y - normals[i + 1].x * kappa,
            ),
            circle[i + 1],
        );
    }
    Some(out)
}
