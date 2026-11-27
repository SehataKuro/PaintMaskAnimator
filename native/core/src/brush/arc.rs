//! `qt_curves_for_arc` と同じ、円弧（楕円弧）を 3 次ベジエ曲線で近似した点の並び。
//! 丸い角のつなぎ（`RoundJoin`）と `QPainterPath::addEllipse` が使う。

use super::qt::{fuzzy_compare, fuzzy_is_null, Point};

pub(crate) const KAPPA: f64 = 0.5522847498;

type Cubic = [Point; 4];

/// `QBezier::parameterSplitLeft`。`curve` を後半に置き換え、前半を返す。
fn split_left(curve: &mut Cubic, t: f64) -> Cubic {
    let split = |c: &mut [f64; 4]| -> [f64; 4] {
        let mut left = [0.0; 4];
        left[0] = c[0];
        left[1] = c[0] + t * (c[1] - c[0]);
        left[2] = c[1] + t * (c[2] - c[1]);
        c[2] += t * (c[3] - c[2]);
        c[1] = left[2] + t * (c[2] - left[2]);
        left[2] = left[1] + t * (left[2] - left[1]);
        let s = left[2] + t * (c[1] - left[2]);
        left[3] = s;
        c[0] = s;
        left
    };
    let mut xs = curve.map(|p| p.x);
    let mut ys = curve.map(|p| p.y);
    let lx = split(&mut xs);
    let ly = split(&mut ys);
    for index in 0..4 {
        curve[index] = Point::new(xs[index], ys[index]);
    }
    [0, 1, 2, 3].map(|index| Point::new(lx[index], ly[index]))
}

/// `QBezier::bezierOnInterval`。
fn on_interval(curve: Cubic, t0: f64, t1: f64) -> Cubic {
    if t0 == 0.0 && t1 == 1.0 {
        return curve;
    }
    let mut rest = curve;
    split_left(&mut rest, t0);
    let true_t = (t1 - t0) / (1.0 - t0);
    split_left(&mut rest, true_t)
}

/// `qt_t_for_arc_angle`。0〜90 度の角度を、単位円の 4 分の 1 の曲線の媒介変数にする。
fn t_for_arc_angle(angle: f64) -> f64 {
    if fuzzy_is_null(angle) {
        return 0.0;
    }
    if fuzzy_compare(angle, 90.0) {
        return 1.0;
    }
    let radians = angle * (std::f64::consts::PI / 180.0);
    let cos_angle = radians.cos();
    let sin_angle = radians.sin();
    let mut tc = angle / 90.0;
    for _ in 0..2 {
        let fx = ((2.0 - 3.0 * KAPPA) * tc + 3.0 * (KAPPA - 1.0)) * tc * tc + 1.0 - cos_angle;
        let dfx = ((6.0 - 9.0 * KAPPA) * tc + 6.0 * (KAPPA - 1.0)) * tc;
        tc -= fx / dfx;
    }
    let mut ts = tc;
    for _ in 0..2 {
        let fy =
            (((3.0 * KAPPA - 2.0) * ts - 6.0 * KAPPA + 3.0) * ts + 3.0 * KAPPA) * ts - sin_angle;
        let dfy = ((9.0 * KAPPA - 6.0) * ts + 12.0 * KAPPA - 6.0) * ts + 3.0 * KAPPA;
        ts -= fy / dfy;
    }
    0.5 * (tc + ts)
}

/// `QBezier::coefficients`。
fn coefficients(t: f64) -> (f64, f64, f64, f64) {
    let m_t = 1.0 - t;
    let mut b = m_t * m_t;
    let mut c = t * t;
    let d = c * t;
    let a = b * m_t;
    b *= 3.0 * t;
    c *= 3.0 * m_t;
    (a, b, c, d)
}

/// `qt_find_ellipse_coords` の 1 点。
fn ellipse_point(rect: (f64, f64, f64, f64), angle: f64) -> Point {
    let (x, y, width, height) = rect;
    let (w2, h2) = (width / 2.0, height / 2.0);
    let theta = angle - 360.0 * (angle / 360.0).floor();
    let mut t = theta / 90.0;
    let quadrant = t as i64;
    t -= quadrant as f64;
    t = t_for_arc_angle(90.0 * t);
    if quadrant & 1 != 0 {
        t = 1.0 - t;
    }
    let (a, b, c, d) = coefficients(t);
    let mut px = a + b + c * KAPPA;
    let mut py = d + c + b * KAPPA;
    if quadrant == 1 || quadrant == 2 {
        px = -px;
    }
    if quadrant == 0 || quadrant == 1 {
        py = -py;
    }
    Point::new(x + w2 + w2 * px, y + h2 + h2 * py)
}

/// `qt_curves_for_arc`。始点と、曲線ごとの（制御点, 制御点, 終点）の並びを返す。
/// `rect` は `(x, y, width, height)`、角度は度（反時計回り）。
pub(crate) fn curves_for_arc(
    rect: (f64, f64, f64, f64),
    start_angle: f64,
    sweep: f64,
) -> (Point, Vec<Point>) {
    let (x, y, w, h) = rect;
    let mut curves = Vec::new();
    if [x, y, w, h, start_angle, sweep].iter().any(|v| v.is_nan()) || (w == 0.0 && h == 0.0) {
        return (Point::new(0.0, 0.0), curves);
    }
    let (w2, h2) = (w / 2.0, h / 2.0);
    let (w2k, h2k) = (w2 * KAPPA, h2 * KAPPA);
    let p = [
        Point::new(x + w, y + h2),
        Point::new(x + w, y + h2 + h2k),
        Point::new(x + w2 + w2k, y + h),
        Point::new(x + w2, y + h),
        Point::new(x + w2 - w2k, y + h),
        Point::new(x, y + h2 + h2k),
        Point::new(x, y + h2),
        Point::new(x, y + h2 - h2k),
        Point::new(x + w2 - w2k, y),
        Point::new(x + w2, y),
        Point::new(x + w2 + w2k, y),
        Point::new(x + w, y + h2 - h2k),
        Point::new(x + w, y + h2),
    ];
    let sweep = sweep.clamp(-360.0, 360.0);
    if start_angle == 0.0 {
        if sweep == 360.0 {
            for index in [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0] {
                curves.push(p[index]);
            }
            return (p[12], curves);
        }
        if sweep == -360.0 {
            curves.extend_from_slice(&p[1..13]);
            return (p[0], curves);
        }
    }
    let mut start_segment = (start_angle / 90.0).floor() as i64;
    let mut end_segment = ((start_angle + sweep) / 90.0).floor() as i64;
    let mut start_t = (start_angle - start_segment as f64 * 90.0) / 90.0;
    let mut end_t = (start_angle + sweep - end_segment as f64 * 90.0) / 90.0;
    let delta: i64 = if sweep > 0.0 { 1 } else { -1 };
    if delta < 0 {
        start_t = 1.0 - start_t;
        end_t = 1.0 - end_t;
    }
    if fuzzy_is_null(start_t - 1.0) {
        start_t = 0.0;
        start_segment += delta;
    }
    if fuzzy_is_null(end_t) {
        end_t = 1.0;
        end_segment -= delta;
    }
    start_t = t_for_arc_angle(start_t * 90.0);
    end_t = t_for_arc_angle(end_t * 90.0);
    let split_at_start = !fuzzy_is_null(start_t);
    let split_at_end = !fuzzy_is_null(end_t - 1.0);
    let end = end_segment + delta;
    let quadrant_index = |segment: i64| (3 - segment.rem_euclid(4)) as usize * 3;
    if start_segment == end {
        let j = quadrant_index(start_segment);
        return (if delta > 0 { p[j + 3] } else { p[j] }, curves);
    }
    let start_point = ellipse_point(rect, start_angle);
    let end_point = ellipse_point(rect, start_angle + sweep);
    let mut segment = start_segment;
    while segment != end {
        let j = quadrant_index(segment);
        let mut curve = if delta > 0 {
            [p[j + 3], p[j + 2], p[j + 1], p[j]]
        } else {
            [p[j], p[j + 1], p[j + 2], p[j + 3]]
        };
        if start_segment == end_segment && fuzzy_compare(start_t, end_t) {
            return (start_point, curves);
        }
        if segment == start_segment {
            if segment == end_segment && split_at_end {
                curve = on_interval(curve, start_t, end_t);
            } else if split_at_start {
                curve = on_interval(curve, start_t, 1.0);
            }
        } else if segment == end_segment && split_at_end {
            curve = on_interval(curve, 0.0, end_t);
        }
        curves.extend_from_slice(&curve[1..]);
        segment += delta;
    }
    if let Some(last) = curves.last_mut() {
        *last = end_point;
    }
    (start_point, curves)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn full_circle_is_four_curves_from_the_right() {
        let (start, curves) = curves_for_arc((0.0, 0.0, 2.0, 2.0), 0.0, -360.0);
        assert_eq!((start.x, start.y), (2.0, 1.0));
        assert_eq!(curves.len(), 12);
        assert_eq!((curves[11].x, curves[11].y), (2.0, 1.0));
    }

    #[test]
    fn quarter_arc_ends_on_the_circle() {
        let (start, curves) = curves_for_arc((-1.0, -1.0, 2.0, 2.0), 90.0, -45.0);
        assert!((start.x).abs() < 1e-9 && (start.y + 1.0).abs() < 1e-9);
        let end = curves.last().copied().unwrap();
        let r = (end.x * end.x + end.y * end.y).sqrt();
        assert!((r - 1.0).abs() < 1e-3);
        assert!(end.x > 0.0 && end.y < 0.0);
    }
}
