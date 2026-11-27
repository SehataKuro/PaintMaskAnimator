//! 筆圧カーブ（`pressure.py`）と、筆圧から線幅の倍率を出す計算
//! （`PaintToolsMixin.pressure_size_scale`）。Python と同じ式・同じ順で計算する。

/// Python の `min(a, b)`（`b < a` のときだけ `b`）。NaN の扱いも同じ。
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// Python の `max(a, b)`（`b > a` のときだけ `b`）。
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

fn unit(value: f64) -> f64 {
    py_max(0.0, py_min(1.0, value))
}

/// 区分 3 次ベジエ曲線の 1 区間（始点・制御点・制御点・終点）。
pub type CurveSegment = [(f64, f64); 4];

/// `_pressure_bezier_segments`。点を並べ替え、形を保つ接線で区分 3 次ベジエ曲線にする。
pub fn pressure_bezier_segments(points: &[(f64, f64)]) -> Vec<CurveSegment> {
    let mut sorted: Vec<(f64, f64)> = points.iter().map(|&(x, y)| (unit(x), unit(y))).collect();
    // Python の sorted（タプルの辞書順）。NaN は unit で 0〜1 に収まっている。
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mut ordered: Vec<(f64, f64)> = Vec::new();
    for (x, y) in sorted {
        match ordered.last_mut() {
            Some(last) if (x - last.0).abs() < 1e-6 => *last = (x, y),
            _ => ordered.push((x, y)),
        }
    }
    if ordered.len() < 2 {
        ordered = vec![(0.0, 0.0), (1.0, 1.0)];
    }
    let count = ordered.len();
    let widths: Vec<f64> = (0..count - 1)
        .map(|index| py_max(1e-6, ordered[index + 1].0 - ordered[index].0))
        .collect();
    let slopes: Vec<f64> = (0..count - 1)
        .map(|index| (ordered[index + 1].1 - ordered[index].1) / widths[index])
        .collect();
    let mut tangents = vec![0.0; count];
    tangents[0] = slopes[0];
    tangents[count - 1] = slopes[count - 2];
    for index in 1..count - 1 {
        let left = slopes[index - 1];
        let right = slopes[index];
        if left == 0.0 || right == 0.0 || left * right <= 0.0 {
            tangents[index] = 0.0;
        } else {
            let left_width = widths[index - 1];
            let right_width = widths[index];
            let weight_left = 2.0 * right_width + left_width;
            let weight_right = right_width + 2.0 * left_width;
            tangents[index] =
                (weight_left + weight_right) / (weight_left / left + weight_right / right);
        }
    }
    for (index, &slope) in slopes.iter().enumerate() {
        if slope.abs() < 1e-12 {
            tangents[index] = 0.0;
            tangents[index + 1] = 0.0;
            continue;
        }
        let alpha = tangents[index] / slope;
        let beta = tangents[index + 1] / slope;
        let magnitude = alpha * alpha + beta * beta;
        if magnitude > 9.0 {
            let scale = 3.0 / magnitude.sqrt();
            tangents[index] = scale * alpha * slope;
            tangents[index + 1] = scale * beta * slope;
        }
    }
    (0..count - 1)
        .map(|index| {
            let p1 = ordered[index];
            let p2 = ordered[index + 1];
            let width = widths[index];
            let c1 = (p1.0 + width / 3.0, p1.1 + tangents[index] * width / 3.0);
            let c2 = (p2.0 - width / 3.0, p2.1 - tangents[index + 1] * width / 3.0);
            let c1 = (py_max(p1.0, py_min(p2.0, c1.0)), unit(c1.1));
            let c2 = (py_max(p1.0, py_min(p2.0, c2.0)), unit(c2.1));
            [p1, c1, c2, p2]
        })
        .collect()
}

/// `_cubic_bezier_value`。
pub fn cubic_bezier_value(a: f64, b: f64, c: f64, d: f64, t: f64) -> f64 {
    let inv = 1.0 - t;
    inv * inv * inv * a + 3.0 * inv * inv * t * b + 3.0 * inv * t * t * c + t * t * t * d
}

/// `_pressure_bezier_at`。入力 `pressure`（0〜1）に対応する曲線上の出力を返す。
pub fn pressure_bezier_at(points: &[(f64, f64)], pressure: f64) -> f64 {
    let x = unit(pressure);
    let segments = pressure_bezier_segments(points);
    let mut segment = segments[segments.len() - 1];
    for candidate in &segments {
        if x <= candidate[3].0 {
            segment = *candidate;
            break;
        }
    }
    let [p0, c1, c2, p3] = segment;
    if x <= p0.0 {
        return unit(p0.1);
    }
    if x >= p3.0 {
        return unit(p3.1);
    }
    let (mut low, mut high) = (0.0, 1.0);
    for _ in 0..32 {
        let t = (low + high) * 0.5;
        let bx = cubic_bezier_value(p0.0, c1.0, c2.0, p3.0, t);
        if bx < x {
            low = t;
        } else {
            high = t;
        }
    }
    let t = (low + high) * 0.5;
    unit(cubic_bezier_value(p0.1, c1.1, c2.1, p3.1, t))
}

/// `PressureCurveWidget._parse` の数値の形：指数から 3 点の曲線を作る。
pub fn curve_points_from_exponent(exponent: f64) -> Vec<(f64, f64)> {
    let exponent = py_max(0.20, py_min(4.0, exponent));
    vec![(0.0, 0.0), (0.5, 0.5f64.powf(exponent)), (1.0, 1.0)]
}

/// ブラシの筆圧の設定（`pressure_enabled` / `pressure_min` / `pressure_max` /
/// `pressure_curve_points` / `pressure_curve`）。
#[derive(Clone, Debug, PartialEq)]
pub struct PressureSettings {
    /// 筆圧を線幅に使うか。偽なら倍率は常に 1。
    pub enabled: bool,
    /// 倍率の下限（0〜1）。
    pub minimum: f64,
    /// 倍率に掛ける最大倍率。
    pub maximum: f64,
    /// 筆圧カーブのアンカー。2 点未満なら `exponent` の累乗を使う。
    pub points: Vec<(f64, f64)>,
    /// 旧形式の指数（`pressure ** exponent`）。
    pub exponent: f64,
}

impl Default for PressureSettings {
    fn default() -> Self {
        Self {
            enabled: true,
            minimum: 0.1,
            maximum: 1.0,
            points: Vec::new(),
            exponent: 1.0,
        }
    }
}

impl PressureSettings {
    /// `pressure_size_scale`。筆圧から線幅の倍率だけを返す（不透明度には使わない）。
    pub fn size_scale(&self, pressure: f64) -> f64 {
        if !self.enabled {
            return 1.0;
        }
        let pressure = py_max(0.0, py_min(1.0, pressure));
        let value = if self.points.len() >= 2 {
            pressure_bezier_at(&self.points, pressure)
        } else {
            pressure.powf(self.exponent)
        };
        py_max(self.minimum, value) * self.maximum
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identity_curve_passes_through_anchors() {
        let points = [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)];
        assert!((pressure_bezier_at(&points, 0.5) - 0.5).abs() < 1e-9);
        assert_eq!(pressure_bezier_at(&points, -1.0), 0.0);
        assert_eq!(pressure_bezier_at(&points, 2.0), 1.0);
    }

    #[test]
    fn too_few_points_fall_back_to_a_line() {
        let segments = pressure_bezier_segments(&[(0.3, 0.3)]);
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0][0], (0.0, 0.0));
        assert_eq!(segments[0][3], (1.0, 1.0));
    }

    #[test]
    fn size_scale_respects_minimum_and_maximum() {
        let settings = PressureSettings {
            minimum: 0.25,
            maximum: 2.0,
            ..PressureSettings::default()
        };
        assert_eq!(settings.size_scale(0.1), 0.5);
        assert_eq!(settings.size_scale(1.0), 2.0);
        let off = PressureSettings {
            enabled: false,
            ..settings
        };
        assert_eq!(off.size_scale(0.1), 1.0);
    }
}
