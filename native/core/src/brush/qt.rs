//! Qt の浮動小数点の比較と線分の計算を、同じ演算順で行う小道具。
//!
//! 画素を Qt と一致させるため、`QLineF` の長さ合わせ・平行移動・法線、`qRound`、
//! `QPointF` のあいまいな等号を、Qt 6 と同じ式・同じ順序で書く。

/// 2 点を結ぶ浮動小数点の点。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Point {
    /// x 座標。
    pub x: f64,
    /// y 座標。
    pub y: f64,
}

impl Point {
    /// 点を作る。
    pub const fn new(x: f64, y: f64) -> Self {
        Self { x, y }
    }
}

/// `qFuzzyIsNull(double)`。
pub(crate) fn fuzzy_is_null(value: f64) -> bool {
    value.abs() <= 0.000_000_000_001
}

/// `qFuzzyCompare(double, double)`。どちらかが 0 なら 0 とだけ等しい。
pub(crate) fn fuzzy_compare(a: f64, b: f64) -> bool {
    (a - b).abs() * 1_000_000_000_000.0 <= a.abs().min(b.abs())
}

/// `QtPrivate::fuzzyCompare`（片方が 0 なら差が 0 に近いかで見る）。
fn fuzzy_equal(a: f64, b: f64) -> bool {
    if a == 0.0 || b == 0.0 {
        fuzzy_is_null(a - b)
    } else {
        fuzzy_compare(a, b)
    }
}

/// `QPointF` の `==`（Qt 6 ではあいまいな比較）。
pub(crate) fn points_equal(a: Point, b: Point) -> bool {
    fuzzy_equal(a.x, b.x) && fuzzy_equal(a.y, b.y)
}

/// `qRound(double)`。0.5 を足して（負なら引いて）0 の方向へ切り捨てる。
pub(crate) fn q_round(value: f64) -> i64 {
    let shifted = if value >= 0.0 {
        value + 0.5
    } else {
        value - 0.5
    };
    shifted as i64
}

/// `QLineF` と同じ演算をする線分。
#[derive(Clone, Copy, Debug)]
pub(crate) struct Line {
    pub(crate) p1: Point,
    pub(crate) p2: Point,
}

impl Line {
    pub(crate) fn new(x1: f64, y1: f64, x2: f64, y2: f64) -> Self {
        Self {
            p1: Point::new(x1, y1),
            p2: Point::new(x2, y2),
        }
    }

    pub(crate) fn dx(&self) -> f64 {
        self.p2.x - self.p1.x
    }

    pub(crate) fn dy(&self) -> f64 {
        self.p2.y - self.p1.y
    }

    /// `QLineF::length()`（`qHypot`）。
    pub(crate) fn length(&self) -> f64 {
        self.dx().hypot(self.dy())
    }

    /// `QLineF::setLength`。長さが 0 の線は変えない。
    pub(crate) fn set_length(&mut self, length: f64) {
        let old = self.length();
        if old > 0.0 {
            let (dx, dy) = (self.dx(), self.dy());
            self.p2 = Point::new(
                self.p1.x + length * (dx / old),
                self.p1.y + length * (dy / old),
            );
        }
    }

    /// `QLineF::translate`。
    pub(crate) fn translate(&mut self, dx: f64, dy: f64) {
        self.p1 = Point::new(self.p1.x + dx, self.p1.y + dy);
        self.p2 = Point::new(self.p2.x + dx, self.p2.y + dy);
    }

    /// `QLineF::normalVector()`。
    pub(crate) fn normal_vector(&self) -> Self {
        Self::new(
            self.p1.x,
            self.p1.y,
            self.p1.x + self.dy(),
            self.p1.y - self.dx(),
        )
    }

    /// 端点を入れ替えた線（`QLineF(p2, p1)`）。
    pub(crate) fn reversed(&self) -> Self {
        Self {
            p1: self.p2,
            p2: self.p1,
        }
    }

    /// `QLineF::angle()`（度、反時計回り、[0, 360)）。
    pub(crate) fn angle(&self) -> f64 {
        let theta = (-self.dy()).atan2(self.dx()).to_degrees();
        let normalized = if theta < 0.0 { theta + 360.0 } else { theta };
        if fuzzy_compare(normalized, 360.0) {
            0.0
        } else {
            normalized
        }
    }

    /// `QLineF::isNull()`。
    pub(crate) fn is_null(&self) -> bool {
        points_equal(self.p1, self.p2)
    }

    /// `QLineF::angleTo`。
    pub(crate) fn angle_to(&self, other: &Line) -> f64 {
        if self.is_null() || other.is_null() {
            return 0.0;
        }
        let delta = other.angle() - self.angle();
        let normalized = if delta < 0.0 { delta + 360.0 } else { delta };
        if fuzzy_compare(delta, 360.0) {
            0.0
        } else {
            normalized
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn q_round_rounds_half_away_from_zero() {
        assert_eq!(q_round(2.5), 3);
        assert_eq!(q_round(-2.5), -3);
        assert_eq!(q_round(-2.4), -2);
        assert_eq!(q_round(0.49999999999999994), 1);
    }

    #[test]
    fn fuzzy_point_equality_matches_qt() {
        assert!(points_equal(
            Point::new(1.0, 0.0),
            Point::new(1.0 + 1e-13, 1e-13)
        ));
        assert!(!points_equal(Point::new(1.0, 0.0), Point::new(1.0, 1e-11)));
    }

    #[test]
    fn set_length_keeps_direction() {
        let mut line = Line::new(1.0, 1.0, 4.0, 5.0);
        line.set_length(10.0);
        assert!((line.p2.x - 7.0).abs() < 1e-12 && (line.p2.y - 9.0).abs() < 1e-12);
        let normal = Line::new(0.0, 0.0, 2.0, 0.0).normal_vector();
        assert_eq!((normal.p2.x, normal.p2.y), (0.0, -2.0));
    }
}
