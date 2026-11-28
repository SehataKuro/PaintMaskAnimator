//! 手ぶれ補正（`canvas_brush_stabilizer.py`）。入力の点を、実際に描く線分の並びにする。
//!
//! Python と同じく、直近の点の重み付き平均と「遅れの半径」で描く点を決め、入力の間は
//! 細かく刻んで補間する。入力が止まっている間は、画面側が約 16 ms ごとに `tick` を呼ぶと
//! 描く点が終点へ追いつく（Python の `_brush_follow_timer`）。

use super::qt::Point;

/// 追従タイマーの間隔（ミリ秒）。
pub const FOLLOW_INTERVAL_MS: f64 = 16.0;

/// 描く線分（始点・終点・筆圧）。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Segment {
    /// 始点。
    pub start: Point,
    /// 終点。
    pub end: Point,
    /// 筆圧（0〜1）。
    pub pressure: f64,
}

/// 1 ストロークの間の手ぶれ補正の状態。
#[derive(Clone, Debug)]
pub struct Stabilizer {
    strength: i64,
    zoom: f64,
    stabilized: Option<Point>,
    last_raw: Option<Point>,
    history: Vec<Point>,
    last_pressure: f64,
    pressure_history: Vec<f64>,
    follow_settle: f64,
    follow_active: bool,
    last_canvas: Option<Point>,
}

/// Python の `math.hypot`（CPython の `vector_norm`）。C の `hypot` と最後の桁が違うことがある。
fn python_hypot(x: f64, y: f64) -> f64 {
    let values = [x.abs(), y.abs()];
    if values.iter().any(|v| v.is_infinite()) {
        return f64::INFINITY;
    }
    if values.iter().any(|v| v.is_nan()) {
        return f64::NAN;
    }
    let max = values[0].max(values[1]);
    if max == 0.0 {
        return max;
    }
    // frexp の指数（max = m * 2^e、0.5 <= m < 1）。非正規化数は C と同じく別に扱う。
    if max < f64::MIN_POSITIVE {
        return f64::MIN_POSITIVE * python_hypot(x / f64::MIN_POSITIVE, y / f64::MIN_POSITIVE);
    }
    let exponent = ((max.to_bits() >> 52) & 0x7ff) as i32 - 1022;
    let scale = 2f64.powi(-exponent);
    // 和を (sum, frac1, frac2) の 3 つで持ち、誤差なしの積と和を足していく。
    let mut state = (1.0f64, 0.0f64, 0.0f64);
    let add = |(sum, frac1, frac2): (f64, f64, f64), hi: f64, lo: f64| {
        let total = sum + hi;
        (total, frac1 + lo, frac2 + ((sum - total) + hi))
    };
    for value in values {
        let scaled = value * scale;
        let square = scaled * scaled;
        state = add(state, square, scaled.mul_add(scaled, -square));
    }
    let mut h = (state.0 - 1.0 + (state.1 + state.2)).sqrt();
    let square = -h * h;
    state = add(state, square, (-h).mul_add(h, -square));
    let correction = state.0 - 1.0 + (state.1 + state.2);
    h += correction / (2.0 * h);
    h / scale
}

/// Python 3.12 以降の `sum`（浮動小数点は Neumaier の補正つきで足す）。
fn python_sum(values: impl Iterator<Item = f64>) -> f64 {
    let mut total = 0.0f64;
    let mut compensation = 0.0f64;
    for value in values {
        let next = total + value;
        if total.abs() >= value.abs() {
            compensation += (total - next) + value;
        } else {
            compensation += (value - next) + total;
        }
        total = next;
    }
    if compensation != 0.0 && compensation.is_finite() {
        total += compensation;
    }
    total
}

/// Python の `round`（偶数への丸め）で整数にする。
fn round_half_even(value: f64) -> i64 {
    value.round_ties_even() as i64
}

impl Stabilizer {
    /// 強さ（0〜300 に丸める）と表示倍率で作る。点はまだ持たない。
    pub fn new(strength: i64, zoom: f64) -> Self {
        Self {
            strength: strength.clamp(0, 300),
            zoom,
            stabilized: None,
            last_raw: None,
            history: Vec::new(),
            last_pressure: 1.0,
            pressure_history: Vec::new(),
            follow_settle: 0.0,
            follow_active: false,
            last_canvas: None,
        }
    }

    /// 強さ（0〜300）。
    pub fn strength(&self) -> i64 {
        self.strength
    }

    /// 最後に描いた点。
    pub fn last_canvas(&self) -> Option<Point> {
        self.last_canvas
    }

    /// 直前の（平滑化した）筆圧。
    pub fn last_pressure(&self) -> f64 {
        self.last_pressure
    }

    /// 追従タイマーが動いているか（強さが 0 より大きいストロークの途中）。
    pub fn follow_active(&self) -> bool {
        self.follow_active
    }

    /// `_brush_stabilizer_window`。
    pub fn window(&self) -> usize {
        if self.strength <= 0 {
            return 1;
        }
        (2 + round_half_even(self.strength as f64 * 0.20)).clamp(2, 64) as usize
    }

    /// `_reset_brush_stabilizer(point)` と、押した点を描く点にする。
    pub fn reset(&mut self, point: Point) {
        self.stabilized = Some(point);
        self.last_raw = Some(point);
        self.history = vec![point];
        self.last_pressure = 1.0;
        self.pressure_history.clear();
        self.last_canvas = Some(point);
        self.follow_settle = 0.0;
        self.follow_active = self.strength > 0;
    }

    /// `_smooth_brush_pressure`。直近 7 個の筆圧の重み付き平均を返し、直前の筆圧として覚える。
    pub fn smooth_pressure(&mut self, pressure: f64) -> f64 {
        // Python の max(0.001, min(1.0, pressure))。NaN は 1 になる。
        let clipped = if pressure < 1.0 { pressure } else { 1.0 };
        let value = if clipped > 0.001 { clipped } else { 0.001 };
        self.pressure_history.push(value);
        if self.pressure_history.len() > 7 {
            let excess = self.pressure_history.len() - 7;
            self.pressure_history.drain(..excess);
        }
        let total = python_sum(
            self.pressure_history
                .iter()
                .enumerate()
                .map(|(index, sample)| sample * (index + 1) as f64),
        );
        let count = self.pressure_history.len() as i64;
        let smoothed = total / (count * (count + 1) / 2) as f64;
        self.last_pressure = smoothed;
        smoothed
    }

    /// 筆圧を平滑化せず、直前の筆圧として覚える（マウスは常に 1）。
    pub fn set_last_pressure(&mut self, pressure: f64) {
        self.last_pressure = pressure;
    }

    /// `_stabilized_brush_point`。
    fn stabilized_point(&mut self, raw: Point, settle: f64) -> Point {
        let settle = settle.clamp(0.0, 1.0);
        self.last_raw = Some(raw);
        if self.strength <= 0 {
            self.history = vec![raw];
            self.stabilized = Some(raw);
            return raw;
        }
        self.history.push(raw);
        let window = self.window();
        if self.history.len() > window {
            let excess = self.history.len() - window;
            self.history.drain(..excess);
        }
        let weighted = |coordinate: fn(&Point) -> f64| {
            python_sum(
                self.history
                    .iter()
                    .enumerate()
                    .map(|(index, point)| coordinate(point) * (index + 1) as f64),
            )
        };
        let sum_x = weighted(|point| point.x);
        let sum_y = weighted(|point| point.y);
        let count = self.history.len() as i64;
        let weight_sum = (count * (count + 1) / 2) as f64;
        let target = Point::new(sum_x / weight_sum, sum_y / weight_sum);
        let Some(current) = self.stabilized else {
            self.stabilized = Some(target);
            return target;
        };
        let dx = target.x - current.x;
        let dy = target.y - current.y;
        let distance = python_hypot(dx, dy);
        let radius_screen = 60.0 * (self.strength as f64 / 300.0).powf(1.35);
        let mut radius_canvas = radius_screen / self.zoom.max(0.05);
        radius_canvas *= 1.0 - settle;
        let filtered = if distance <= radius_canvas || distance <= 1e-9 {
            current
        } else {
            let ratio = (distance - radius_canvas) / distance;
            Point::new(current.x + dx * ratio, current.y + dy * ratio)
        };
        self.stabilized = Some(filtered);
        filtered
    }

    fn draw_if_moved(
        &mut self,
        filtered: Point,
        pressure: f64,
        threshold: f64,
        out: &mut Vec<Segment>,
    ) {
        let Some(last) = self.last_canvas else {
            return;
        };
        if (filtered.x - last.x).abs() > threshold || (filtered.y - last.y).abs() > threshold {
            out.push(Segment {
                start: last,
                end: filtered,
                pressure,
            });
            self.last_canvas = Some(filtered);
        }
    }

    /// `_draw_stabilized_brush_to`。入力の点 `raw` までの線分を返す。
    pub fn move_to(&mut self, raw: Point, pressure: f64) -> Vec<Segment> {
        let mut out = Vec::new();
        self.follow_settle = 0.0;
        if self.strength > 0 {
            self.follow_active = true;
        }
        let last = *self.last_canvas.get_or_insert(raw);
        if self.strength <= 0 {
            out.push(Segment {
                start: last,
                end: raw,
                pressure,
            });
            self.last_canvas = Some(raw);
            self.last_raw = Some(raw);
            return out;
        }
        let start_raw = self.last_raw.unwrap_or(raw);
        let distance = python_hypot(raw.x - start_raw.x, raw.y - start_raw.y);
        let step_canvas = 0.5f64.max(2.5f64.min(1.5 / self.zoom.max(0.05)));
        let steps = ((distance / step_canvas).ceil() as i64).clamp(1, 96);
        for index in 1..=steps {
            let ratio = index as f64 / steps as f64;
            let intermediate = Point::new(
                start_raw.x + (raw.x - start_raw.x) * ratio,
                start_raw.y + (raw.y - start_raw.y) * ratio,
            );
            let filtered = self.stabilized_point(intermediate, 0.0);
            self.draw_if_moved(filtered, pressure, 0.005, &mut out);
        }
        out
    }

    /// `_advance_stabilized_brush`。追従タイマー 1 回分の線分を返す。
    pub fn tick(&mut self) -> Vec<Segment> {
        let mut out = Vec::new();
        let (Some(raw), Some(last)) = (self.last_raw, self.last_canvas) else {
            self.follow_active = false;
            return out;
        };
        if !self.follow_active {
            return out;
        }
        let distance = python_hypot(raw.x - last.x, raw.y - last.y);
        if distance <= 0.01 {
            return out;
        }
        if self.strength <= 0 {
            out.push(Segment {
                start: last,
                end: raw,
                pressure: self.last_pressure,
            });
            self.last_canvas = Some(raw);
            return out;
        }
        self.follow_settle =
            1.0f64.min(self.follow_settle + 0.035 + 0.055 * (self.strength as f64 / 300.0));
        let filtered = self.stabilized_point(raw, self.follow_settle);
        let pressure = self.last_pressure;
        self.draw_if_moved(filtered, pressure, 0.005, &mut out);
        out
    }

    /// `_finish_stabilized_brush`。離した点 `raw` へ収束させる線分を返す。
    pub fn finish(&mut self, raw: Point, pressure: f64) -> Vec<Segment> {
        let mut out = self.move_to(raw, pressure);
        if self.strength > 0 {
            let settle_steps = self.window() + 8;
            for index in 0..settle_steps {
                let settle = (index + 1) as f64 / settle_steps as f64;
                let filtered = self.stabilized_point(raw, settle);
                self.draw_if_moved(filtered, pressure, 0.005, &mut out);
            }
            if let Some(last) = self.last_canvas {
                if (raw.x - last.x).abs() > 0.01 || (raw.y - last.y).abs() > 0.01 {
                    out.push(Segment {
                        start: last,
                        end: raw,
                        pressure,
                    });
                    self.last_canvas = Some(raw);
                }
            }
        }
        self.follow_active = false;
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zero_strength_draws_raw_segments() {
        let mut stabilizer = Stabilizer::new(0, 1.0);
        stabilizer.reset(Point::new(1.0, 1.0));
        let segments = stabilizer.move_to(Point::new(5.0, 1.0), 0.5);
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0].end, Point::new(5.0, 1.0));
        assert!(stabilizer.tick().is_empty());
        assert!(stabilizer.finish(Point::new(5.0, 1.0), 1.0).len() == 1);
    }

    #[test]
    fn strong_stabilizer_lags_then_settles_on_release() {
        let mut stabilizer = Stabilizer::new(150, 1.0);
        assert_eq!(stabilizer.window(), 32);
        stabilizer.reset(Point::new(0.0, 0.0));
        let moved = stabilizer.move_to(Point::new(40.0, 0.0), 1.0);
        let reached = moved.last().map_or(0.0, |segment| segment.end.x);
        assert!(reached < 40.0);
        let follow = stabilizer.tick();
        assert!(follow.iter().all(|segment| segment.end.x >= reached));
        let finish = stabilizer.finish(Point::new(40.0, 0.0), 1.0);
        assert_eq!(finish.last().unwrap().end, Point::new(40.0, 0.0));
        assert!(!stabilizer.follow_active());
    }

    #[test]
    fn pressure_is_a_weighted_average_of_seven_samples() {
        let mut stabilizer = Stabilizer::new(0, 1.0);
        assert_eq!(stabilizer.smooth_pressure(1.0), 1.0);
        assert!((stabilizer.smooth_pressure(0.0) - (1.0 + 0.002) / 3.0).abs() < 1e-12);
    }
}
