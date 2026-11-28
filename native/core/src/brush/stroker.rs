//! `QStroker` と同じ輪郭を作る、丸い端・丸い角の太線の輪郭づくり。
//!
//! 直線だけからなる部分経路を、行き（左側）と帰り（右側）に半幅ずらした線でたどり、
//! 端は 2 本の 3 次ベジエ曲線の半円（係数 `QT_PATH_KAPPA`）でつなぐ。浮動小数点の
//! 演算は Qt と同じ順にしてあり、出力を `fill_path`（巻き数）で塗ると Qt の画素になる。

use super::arc::{curves_for_arc, KAPPA};
use super::bezier::Bezier;
use super::qt::{fuzzy_compare, points_equal, Line, Point};
use super::raster::{Path, PathElement};

/// `QLineF::intersects` が `BoundedIntersection`（両方の線分の上で交わる）か。
// NaN を範囲外とみなさない Qt の比較に合わせるため、contains を使わない。
#[allow(clippy::manual_range_contains)]
fn intersects_bounded(a: &Line, b: &Line) -> bool {
    let (ax, ay) = (a.p2.x - a.p1.x, a.p2.y - a.p1.y);
    let (bx, by) = (b.p1.x - b.p2.x, b.p1.y - b.p2.y);
    let (cx, cy) = (a.p1.x - b.p1.x, a.p1.y - b.p1.y);
    let denominator = ay * bx - ax * by;
    if denominator == 0.0 || !denominator.is_finite() {
        return false;
    }
    let reciprocal = 1.0 / denominator;
    let na = (by * cx - bx * cy) * reciprocal;
    // NaN は範囲外とみなさない（Qt と同じ比較の向き）。
    if na < 0.0 || na > 1.0 {
        return false;
    }
    let nb = (ax * cy - ay * cx) * reciprocal;
    !(nb < 0.0 || nb > 1.0)
}

/// 角のつなぎ方（`QStroker::LineJoinMode` のうち使うもの）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Join {
    RoundJoin,
    RoundCap,
}

struct Stroker {
    width: f64,
    back1: Point,
    back2: Point,
    output: Path,
}

impl Stroker {
    fn emit_move_to(&mut self, point: Point) {
        self.back2 = self.back1;
        self.back1 = point;
        self.output.push(PathElement::MoveTo(point));
    }

    fn emit_line_to(&mut self, point: Point) {
        self.back2 = self.back1;
        self.back1 = point;
        self.output.push(PathElement::LineTo(point));
    }

    fn emit_cubic_to(&mut self, c1: Point, c2: Point, end: Point) {
        if points_equal(c2, end) {
            if points_equal(c1, end) {
                self.back2 = self.back1;
            } else {
                self.back2 = c1;
            }
        } else {
            self.back2 = c2;
        }
        self.back1 = end;
        self.output.push(PathElement::CubicTo(c1, c2, end));
    }

    /// `QStroker::joinPoints`。`focal` は元の頂点。
    fn join_points(&mut self, focal: Point, next: &Line, join: Join) {
        if fuzzy_compare(self.back1.x, next.p1.x) && fuzzy_compare(self.back1.y, next.p1.y) {
            return;
        }
        let previous = Line {
            p1: self.back2,
            p2: self.back1,
        };
        let offset = self.width / 2.0;
        match join {
            Join::RoundCap => {
                let mut l1 = previous;
                let dot = previous.dx() * next.dx() + previous.dy() * next.dy();
                if dot > 0.0 {
                    l1 = previous.reversed();
                } else {
                    l1.translate(l1.dx(), l1.dy());
                }
                l1.set_length(KAPPA * offset);
                let mut l2 = Line {
                    p1: focal,
                    p2: previous.p2,
                };
                l2.translate(-l2.dy(), l2.dx());
                l2.set_length(KAPPA * offset);
                self.emit_cubic_to(l1.p2, l2.p2, l2.p1);
                let l2 = Line::new(l2.p1.x, l2.p1.y, l2.p1.x - l2.dx(), l2.p1.y - l2.dy());
                l1.translate(next.p1.x - l1.p1.x, next.p1.y - l1.p1.y);
                self.emit_cubic_to(l2.p2, l1.p2, l1.p1);
            }
            Join::RoundJoin => self.round_join(focal, &previous, next, offset),
        }
    }

    /// `joinPoints` の `RoundJoin`。内側の角は頂点を通して折り返す。
    fn round_join(&mut self, focal: Point, previous: &Line, next: &Line, offset: f64) {
        let bounded = intersects_bounded(previous, next);
        let short_cut = Line {
            p1: previous.p2,
            p2: next.p1,
        };
        let angle = short_cut.angle_to(previous);
        if (bounded || angle > 90.01) && next.length() > offset {
            self.emit_line_to(focal);
            self.emit_line_to(next.p1);
            return;
        }
        let axis = Line::new(0.0, 0.0, 1.0, 0.0);
        let l1_on_x = axis.angle_to(previous);
        let l2_on_x = axis.angle_to(next);
        let sweep = (l2_on_x - l1_on_x).abs();
        let rect = (
            focal.x - offset,
            focal.y - offset,
            offset * 2.0,
            offset * 2.0,
        );
        let (_, curves) = curves_for_arc(rect, l1_on_x + 90.0, -sweep);
        for triple in curves.chunks_exact(3) {
            self.emit_cubic_to(triple[0], triple[1], triple[2]);
        }
        self.emit_line_to(next.p1);
    }

    /// `qt_stroke_side`。`closed` の部分経路なら、始点の角を最後につなぐ。
    /// 戻り値は（閉じたか, 最初の線）。
    fn stroke_side(
        &mut self,
        start: Point,
        segments: &[Segment],
        cap_first: bool,
    ) -> (bool, Option<Line>) {
        let offset = self.width / 2.0;
        let mut previous = start;
        let mut first = true;
        let mut start_tangent = None;
        for segment in segments {
            match *segment {
                Segment::Line(point) => {
                    let mut line = Line {
                        p1: previous,
                        p2: point,
                    };
                    if points_equal(line.p1, line.p2) {
                        continue;
                    }
                    let mut normal = line.normal_vector();
                    normal.set_length(offset);
                    line.translate(normal.dx(), normal.dy());
                    if first {
                        if cap_first {
                            self.join_points(previous, &line, Join::RoundCap);
                        } else {
                            self.emit_move_to(line.p1);
                        }
                        start_tangent = Some(line);
                        first = false;
                    } else {
                        self.join_points(previous, &line, Join::RoundJoin);
                    }
                    self.emit_line_to(line.p2);
                    previous = point;
                }
                Segment::Cubic(c1, c2, end) => {
                    let bezier = Bezier::new(previous, c1, c2, end);
                    let curves = bezier.shifted(MAX_OFFSET, offset, curve_threshold(self.width));
                    if let Some(head) = curves.first() {
                        let mut tangent = bezier.start_tangent();
                        tangent.translate(head.p1.x - bezier.p1.x, head.p1.y - bezier.p1.y);
                        if first {
                            if cap_first {
                                self.join_points(previous, &tangent, Join::RoundCap);
                            } else {
                                self.emit_move_to(head.p1);
                            }
                            start_tangent = Some(tangent);
                            first = false;
                        } else {
                            self.join_points(previous, &tangent, Join::RoundJoin);
                        }
                        for curve in &curves {
                            self.emit_cubic_to(curve.p2, curve.p3, curve.p4);
                        }
                    }
                    previous = end;
                }
            }
        }
        if points_equal(start, previous) {
            if let Some(tangent) = start_tangent {
                self.join_points(previous, &tangent, Join::RoundJoin);
            }
            return (true, start_tangent);
        }
        (false, start_tangent)
    }
}

/// 部分経路の 1 区間（直線か 3 次曲線の終点まで）。
#[derive(Clone, Copy, Debug, PartialEq)]
enum Segment {
    Line(Point),
    Cubic(Point, Point, Point),
}

/// `qt_stroke_side` の `MAX_OFFSET`。
const MAX_OFFSET: usize = 16;
/// 曲線をずらすときの許容の誤差。Qt 6.11 の `QStroker::setStrokeWidth` は
/// `qBound(0.00025, 1 / width, 0.25)` にする（6.9 のソースは 0.25 の固定）。
/// `QBezier::shifted` は `float` で受け取る。
fn curve_threshold(width: f64) -> f32 {
    (1.0 / width).clamp(0.00025, 0.25) as f32
}

/// 逆向きにたどる区間（`QSubpathBackwardIterator`）。曲線は制御点を入れ替える。
fn reversed(start: Point, segments: &[Segment]) -> (Point, Vec<Segment>) {
    let mut points = vec![start];
    for segment in segments {
        points.push(match *segment {
            Segment::Line(point) | Segment::Cubic(_, _, point) => point,
        });
    }
    let mut out = Vec::with_capacity(segments.len());
    for (index, segment) in segments.iter().enumerate().rev() {
        let begin = points[index];
        out.push(match *segment {
            Segment::Line(_) => Segment::Line(begin),
            Segment::Cubic(c1, c2, _) => Segment::Cubic(c2, c1, begin),
        });
    }
    (*points.last().unwrap_or(&start), out)
}

fn stroke_subpath(stroker: &mut Stroker, start: Point, segments: &[Segment]) {
    let (forward_closed, forward_tangent) = stroker.stroke_side(start, segments, false);
    let (back_start, back) = reversed(start, segments);
    let (backward_closed, _) = stroker.stroke_side(back_start, &back, !forward_closed);
    if !backward_closed {
        if let Some(tangent) = forward_tangent {
            stroker.join_points(start, &tangent, Join::RoundCap);
        }
    }
}

fn new_stroker(width: f64) -> Stroker {
    Stroker {
        width,
        back1: Point::new(0.0, 0.0),
        back2: Point::new(0.0, 0.0),
        output: Path::new(),
    }
}

/// 直線だけの部分経路 `points` を、幅 `width` の丸い端・丸い角の線として塗る輪郭を返す。
/// 始点と終点が（あいまいに）等しい部分経路は閉じた形として扱う。
pub fn stroke_polyline(points: &[Point], width: f64) -> Path {
    let mut stroker = new_stroker(width);
    if points.len() < 2 {
        return stroker.output;
    }
    let segments: Vec<Segment> = points[1..]
        .iter()
        .map(|&point| Segment::Line(point))
        .collect();
    stroke_subpath(&mut stroker, points[0], &segments);
    stroker.output
}

/// 経路 `path`（直線と 3 次曲線）を、幅 `width` の丸い端・丸い角の線として塗る輪郭
/// （`QStroker`、曲線は `QBezier::shifted` で半幅ずらす）。巻き数で塗る。
pub fn stroke_outline(path: &Path, width: f64) -> Path {
    let mut stroker = new_stroker(width);
    let mut start: Option<Point> = None;
    let mut segments: Vec<Segment> = Vec::new();
    let flush = |stroker: &mut Stroker, start: Option<Point>, segments: &mut Vec<Segment>| {
        if let Some(start) = start {
            if !segments.is_empty() {
                stroke_subpath(stroker, start, segments);
            }
        }
        segments.clear();
    };
    for element in path.elements() {
        match *element {
            PathElement::MoveTo(point) => {
                flush(&mut stroker, start, &mut segments);
                start = Some(point);
            }
            PathElement::LineTo(point) => segments.push(Segment::Line(point)),
            PathElement::CubicTo(c1, c2, end) => segments.push(Segment::Cubic(c1, c2, end)),
        }
    }
    flush(&mut stroker, start, &mut segments);
    stroker.output
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn single_line_is_two_sides_and_two_half_circles() {
        let path = stroke_polyline(&[Point::new(0.0, 0.0), Point::new(10.0, 0.0)], 4.0);
        let kinds: Vec<_> = path
            .elements()
            .iter()
            .map(|element| match element {
                PathElement::MoveTo(_) => 'M',
                PathElement::LineTo(_) => 'L',
                PathElement::CubicTo(..) => 'C',
            })
            .collect();
        assert_eq!(kinds, vec!['M', 'L', 'C', 'C', 'L', 'C', 'C']);
        let PathElement::MoveTo(start) = path.elements()[0] else {
            unreachable!()
        };
        assert_eq!((start.x, start.y), (0.0, -2.0));
    }
}
