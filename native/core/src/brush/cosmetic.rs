//! `QCosmeticStroker`（幅 1 以下のペン）の非 AA・実線の道。
//!
//! qcosmeticstroker.cpp の `drawLine<drawPixel, NoDasher>`・`calculateLastPoint`・
//! `drawPath`・`renderCubic` を写す。辺ごとの抜け（dropout）を足し、角で同じ画素を
//! 2 度打たない。破線・アンチエイリアスは持たない。打つ画素は呼ぶ側の `sink` が受け取る
//! （ブラシの `Mask` と、表示の `compose::cosmetic` の画面）。

use super::mask::Mask;
use super::qt::Point;
use super::raster::{Path, PathElement};

const CAP_BEGIN: u8 = 0x1;
const CAP_END: u8 = 0x2;

const TOP_TO_BOTTOM: u8 = 0x1;
const BOTTOM_TO_TOP: u8 = 0x2;
const LEFT_TO_RIGHT: u8 = 0x4;
const RIGHT_TO_LEFT: u8 = 0x8;
const VERTICAL_MASK: u8 = 0x3;
const HORIZONTAL_MASK: u8 = 0xc;

/// `INT_MIN`（前の画素が無い印）。
const NO_PIXEL: i64 = i32::MIN as i64;

/// `toF26Dot6`（切り捨て）。
fn to_26_6(value: f64) -> i64 {
    (value * 64.0) as i32 as i64
}

/// `FDot16FixedDiv`（64 bit）。
fn fixed_div(x: i64, y: i64) -> i64 {
    x * (1 << 16) / y
}

fn swap_caps(caps: u8) -> u8 {
    ((caps & CAP_BEGIN) << 1) | ((caps & CAP_END) >> 1)
}

/// `capAdjust`。端を半画素のばす。
fn cap_adjust(caps: u8, x1: &mut i64, x2: &mut i64, y: &mut i64, yinc: i64) {
    if caps & CAP_BEGIN != 0 {
        *x1 -= 32;
        *y -= yinc >> 1;
    }
    if caps & CAP_END != 0 {
        *x2 += 32;
    }
}

pub(crate) struct CosmeticStroker<F: FnMut(i64, i64)> {
    sink: F,
    width: i64,
    height: i64,
    xmin: f64,
    xmax: f64,
    ymin: f64,
    ymax: f64,
    last_pixel: (i64, i64),
    last_dir: u8,
    last_axis_aligned: bool,
}

impl<F: FnMut(i64, i64)> CosmeticStroker<F> {
    /// 描く先が `(0, 0, width, height)` の画面。`sink` は打つ画素を受け取る。
    pub(crate) fn new(width: i64, height: i64, sink: F) -> Self {
        // deviceRect.left() - 1 〜 deviceRect.right() + 2。
        Self {
            sink,
            width,
            height,
            xmin: -1.0,
            xmax: (width - 1 + 2) as f64,
            ymin: -1.0,
            ymax: (height - 1 + 2) as f64,
            last_pixel: (NO_PIXEL, NO_PIXEL),
            last_dir: 0,
            last_axis_aligned: false,
        }
    }

    /// `drawPixel`。描く先の外は捨てる。
    fn pixel(&mut self, x: i64, y: i64) {
        if x < 0 || x >= self.width || y < 0 || y >= self.height {
            return;
        }
        (self.sink)(x, y);
    }

    /// `QCosmeticStroker::clipLine`。全部切れたら None。
    fn clip_line(
        &mut self,
        mut x1: f64,
        mut y1: f64,
        mut x2: f64,
        mut y2: f64,
    ) -> Option<[f64; 4]> {
        if !(x1.is_finite() && y1.is_finite() && x2.is_finite() && y2.is_finite()) {
            return None;
        }
        let (xmin, xmax, ymin, ymax) = (self.xmin, self.xmax, self.ymin, self.ymax);
        if x1 < xmin {
            if x2 <= xmin {
                self.last_pixel.0 = NO_PIXEL;
                return None;
            }
            y1 += (y2 - y1) / (x2 - x1) * (xmin - x1);
            x1 = xmin;
        } else if x1 > xmax {
            if x2 >= xmax {
                self.last_pixel.0 = NO_PIXEL;
                return None;
            }
            y1 += (y2 - y1) / (x2 - x1) * (xmax - x1);
            x1 = xmax;
        }
        if x2 < xmin {
            self.last_pixel.0 = NO_PIXEL;
            y2 += (y2 - y1) / (x2 - x1) * (xmin - x2);
            x2 = xmin;
        } else if x2 > xmax {
            self.last_pixel.0 = NO_PIXEL;
            y2 += (y2 - y1) / (x2 - x1) * (xmax - x2);
            x2 = xmax;
        }
        if y1 < ymin {
            if y2 <= ymin {
                self.last_pixel.0 = NO_PIXEL;
                return None;
            }
            x1 += (x2 - x1) / (y2 - y1) * (ymin - y1);
            y1 = ymin;
        } else if y1 > ymax {
            if y2 >= ymax {
                self.last_pixel.0 = NO_PIXEL;
                return None;
            }
            x1 += (x2 - x1) / (y2 - y1) * (ymax - y1);
            y1 = ymax;
        }
        if y2 < ymin {
            self.last_pixel.0 = NO_PIXEL;
            x2 += (x2 - x1) / (y2 - y1) * (ymin - y2);
            y2 = ymin;
        } else if y2 > ymax {
            self.last_pixel.0 = NO_PIXEL;
            x2 += (x2 - x1) / (y2 - y1) * (ymax - y2);
            y2 = ymax;
        }
        Some([x1, y1, x2, y2])
    }

    /// `calculateLastPoint`。閉じた輪郭の最後の辺の向きと最後の画素を先に求める。
    fn calculate_last_point(&mut self, rx1: f64, ry1: f64, rx2: f64, ry2: f64) {
        self.last_pixel = (NO_PIXEL, NO_PIXEL);
        let Some([rx1, ry1, rx2, ry2]) = self.clip_line(rx1, ry1, rx2, ry2) else {
            return;
        };
        let (mut x1, mut y1, mut x2, mut y2) =
            (to_26_6(rx1), to_26_6(ry1), to_26_6(rx2), to_26_6(ry2));
        let (dx, dy) = ((x2 - x1).abs(), (y2 - y1).abs());
        if dx < dy {
            let mut swapped = false;
            if y1 > y2 {
                swapped = true;
                std::mem::swap(&mut y1, &mut y2);
                std::mem::swap(&mut x1, &mut x2);
            }
            let xinc = fixed_div(x2 - x1, y2 - y1);
            let mut x = x1 * (1 << 10);
            let y = (y1 + 32) >> 6;
            let ys = (y2 + 32) >> 6;
            let round = if xinc > 0 { 32 } else { 0 };
            if y != ys {
                x += (((y * (1 << 6)) + round - y1) * xinc) >> 6;
                if swapped {
                    self.last_pixel = (x >> 16, y);
                    self.last_dir = BOTTOM_TO_TOP;
                } else {
                    self.last_pixel = ((x + (ys - y - 1) * xinc) >> 16, ys - 1);
                    self.last_dir = TOP_TO_BOTTOM;
                }
                self.last_axis_aligned = xinc.abs() < (1 << 14);
            }
        } else {
            if dx == 0 {
                return;
            }
            let mut swapped = false;
            if x1 > x2 {
                swapped = true;
                std::mem::swap(&mut x1, &mut x2);
                std::mem::swap(&mut y1, &mut y2);
            }
            let yinc = fixed_div(y2 - y1, x2 - x1);
            let mut y = y1 * (1 << 10);
            let x = (x1 + 32) >> 6;
            let xs = (x2 + 32) >> 6;
            let round = if yinc > 0 { 32 } else { 0 };
            if x != xs {
                y += (((x * (1 << 6)) + round - x1) * yinc) >> 6;
                if swapped {
                    self.last_pixel = (x, y >> 16);
                    self.last_dir = RIGHT_TO_LEFT;
                } else {
                    self.last_pixel = (xs - 1, (y + (xs - x - 1) * yinc) >> 16);
                    self.last_dir = LEFT_TO_RIGHT;
                }
                self.last_axis_aligned = yinc.abs() < (1 << 14);
            }
        }
    }

    /// `drawLine<drawPixel, NoDasher>`。戻り値は、次の辺の始点をこの辺の終点へ進めるか。
    pub(crate) fn line(&mut self, rx1: f64, ry1: f64, rx2: f64, ry2: f64, caps: u8) -> bool {
        let mut caps = caps;
        let mut did_draw = (rx2 - rx1).abs() + (ry2 - ry1).abs() >= 1.0;
        let Some([rx1, ry1, rx2, ry2]) = self.clip_line(rx1, ry1, rx2, ry2) else {
            return true;
        };
        let (mut x1, mut y1, mut x2, mut y2) =
            (to_26_6(rx1), to_26_6(ry1), to_26_6(rx2), to_26_6(ry2));
        let (dx, dy) = ((x2 - x1).abs(), (y2 - y1).abs());
        let mut last = self.last_pixel;
        if dx < dy {
            let mut dir = TOP_TO_BOTTOM;
            let mut swapped = false;
            if y1 > y2 {
                swapped = true;
                std::mem::swap(&mut y1, &mut y2);
                std::mem::swap(&mut x1, &mut x2);
                caps = swap_caps(caps);
                dir = BOTTOM_TO_TOP;
            }
            let xinc = fixed_div(x2 - x1, y2 - y1);
            let mut x = x1 * (1 << 10);
            if (self.last_dir ^ VERTICAL_MASK) == dir {
                caps |= if swapped { CAP_END } else { CAP_BEGIN };
            }
            cap_adjust(caps, &mut y1, &mut y2, &mut x, xinc);
            let mut y = (y1 + 32) >> 6;
            let mut ys = (y2 + 32) >> 6;
            let round = if xinc > 0 { 32 } else { 0 };
            if caps & CAP_BEGIN != 0 && self.last_pixel.1 == y + 1 {
                y += 1;
            }
            if y != ys {
                x += (((y * (1 << 6)) + round - y1) * xinc) >> 6;
                let mut first = (x >> 16, y);
                last = ((x + (ys - y - 1) * xinc) >> 16, ys - 1);
                if swapped {
                    std::mem::swap(&mut first, &mut last);
                }
                let axis_aligned = xinc.abs() < (1 << 14);
                let lp = self.last_pixel;
                if lp.0 > NO_PIXEL {
                    if first == lp {
                        // 同じ画素を 2 度打たない。
                        if swapped {
                            ys -= 1;
                        } else {
                            y += 1;
                            x += xinc;
                        }
                    } else if self.last_dir != dir
                        && (((axis_aligned && self.last_axis_aligned)
                            && lp.0 != first.0
                            && lp.1 != first.1)
                            || ((lp.0 - first.0).abs() > 1 || (lp.1 - first.1).abs() > 1))
                    {
                        // 抜けた画素を足す。
                        if swapped {
                            ys += 1;
                        } else {
                            y -= 1;
                            x -= xinc;
                        }
                    } else if self.last_dir == dir
                        && ((lp.0 - first.0).abs() <= 1 && (lp.1 - first.1).abs() > 1)
                    {
                        x += xinc >> 1;
                        if swapped {
                            last.0 = x >> 16;
                        } else {
                            last.0 = (x + (ys - y - 1) * xinc) >> 16;
                        }
                    }
                }
                self.last_dir = dir;
                self.last_axis_aligned = axis_aligned;
                loop {
                    self.pixel(x >> 16, y);
                    x += xinc;
                    y += 1;
                    if y >= ys {
                        break;
                    }
                }
                did_draw = true;
            }
        } else {
            if dx == 0 {
                return true;
            }
            let mut dir = LEFT_TO_RIGHT;
            let mut swapped = false;
            if x1 > x2 {
                swapped = true;
                std::mem::swap(&mut x1, &mut x2);
                std::mem::swap(&mut y1, &mut y2);
                caps = swap_caps(caps);
                dir = RIGHT_TO_LEFT;
            }
            let yinc = fixed_div(y2 - y1, x2 - x1);
            let mut y = y1 * (1 << 10);
            if (self.last_dir ^ HORIZONTAL_MASK) == dir {
                caps |= if swapped { CAP_END } else { CAP_BEGIN };
            }
            cap_adjust(caps, &mut x1, &mut x2, &mut y, yinc);
            let mut x = (x1 + 32) >> 6;
            let mut xs = (x2 + 32) >> 6;
            let round = if yinc > 0 { 32 } else { 0 };
            if caps & CAP_BEGIN != 0 && self.last_pixel.0 == x + 1 {
                x += 1;
            }
            if x != xs {
                y += (((x * (1 << 6)) + round - x1) * yinc) >> 6;
                let mut first = (x, y >> 16);
                last = (xs - 1, (y + (xs - x - 1) * yinc) >> 16);
                if swapped {
                    std::mem::swap(&mut first, &mut last);
                }
                let axis_aligned = yinc.abs() < (1 << 14);
                let lp = self.last_pixel;
                if lp.0 > NO_PIXEL {
                    if first == lp {
                        if swapped {
                            xs -= 1;
                        } else {
                            x += 1;
                            y += yinc;
                        }
                    } else if self.last_dir != dir
                        && (((axis_aligned && self.last_axis_aligned)
                            && lp.0 != first.0
                            && lp.1 != first.1)
                            || ((lp.0 - first.0).abs() > 1 || (lp.1 - first.1).abs() > 1))
                    {
                        if swapped {
                            xs += 1;
                        } else {
                            x -= 1;
                            y -= yinc;
                        }
                    } else if self.last_dir == dir
                        && ((lp.0 - first.0).abs() <= 1 && (lp.1 - first.1).abs() > 1)
                    {
                        y += yinc >> 1;
                        if swapped {
                            last.1 = y >> 16;
                        } else {
                            last.1 = (y + (xs - x - 1) * yinc) >> 16;
                        }
                    }
                }
                self.last_dir = dir;
                self.last_axis_aligned = axis_aligned;
                loop {
                    self.pixel(x, y >> 16);
                    y += yinc;
                    x += 1;
                    if x >= xs {
                        break;
                    }
                }
                did_draw = true;
            }
        }
        self.last_pixel = last;
        did_draw
    }

    /// `drawPath` の要素の種類が無い（点の並びだけの）経路。点は行列を掛けたあと。
    pub(crate) fn polygon(&mut self, points: &[(f64, f64)], implicit_close: bool) {
        if points.is_empty() {
            return;
        }
        let map = |p: (f64, f64)| p;
        let mut p = map(points[0]);
        let moved_to = p;
        self.last_pixel = (NO_PIXEL, NO_PIXEL);
        let n = points.len();
        let end_equal = points[0] == points[n - 1];
        let closed = implicit_close || end_equal;
        // drawCaps は真（既定の四角い端）。閉じた輪郭は端を付けない。
        let mut caps = if closed { 0 } else { CAP_BEGIN };
        if closed {
            let p2 = if end_equal && n > 2 {
                map(points[n - 2])
            } else {
                map(points[n - 1])
            };
            self.calculate_last_point(p2.0, p2.1, p.0, p.1);
        }
        // fast_pen かつ非 AA。
        for (index, &point) in points.iter().enumerate().skip(1) {
            let p2 = map(point);
            if !closed && index == n - 1 {
                caps |= CAP_END;
            }
            let move_next_start = self.line(p.0, p.1, p2.0, p2.1, caps);
            if move_next_start || index == 1 || index == n - 1 {
                p = p2;
            }
            caps = 0;
        }
        if implicit_close {
            self.line(p.0, p.1, moved_to.0, moved_to.1, 0);
        }
    }
}

impl<F: FnMut(i64, i64)> CosmeticStroker<F> {
    /// `renderCubicSubdivision`。
    fn cubic_subdivision(&mut self, points: &mut [(f64, f64)], level: u32, caps: u8) {
        if level > 0 {
            let dx = points[3].0 - points[0].0;
            let dy = points[3].1 - points[0].1;
            let len = 0.25 * (dx.abs() + dy.abs());
            if (dx * (points[0].1 - points[2].1) - dy * (points[0].0 - points[2].0)).abs() >= len
                || (dx * (points[0].1 - points[1].1) - dy * (points[0].0 - points[1].0)).abs()
                    >= len
            {
                split_cubic(points);
                self.cubic_subdivision(&mut points[3..], level - 1, caps & CAP_BEGIN);
                self.cubic_subdivision(points, level - 1, caps & CAP_END);
                return;
            }
        }
        self.line(points[3].0, points[3].1, points[0].0, points[0].1, caps);
    }

    /// `renderCubic`。点は逆順に積んで 2 分割を繰り返す。
    fn cubic(&mut self, p1: Point, p2: Point, p3: Point, p4: Point, caps: u8) {
        const MAX_SUB_DIVISIONS: u32 = 6;
        let mut points = [(0.0, 0.0); 3 * MAX_SUB_DIVISIONS as usize + 4];
        points[3] = (p1.x, p1.y);
        points[2] = (p2.x, p2.y);
        points[1] = (p3.x, p3.y);
        points[0] = (p4.x, p4.y);
        self.cubic_subdivision(&mut points, MAX_SUB_DIVISIONS, caps);
    }

    /// `drawPath` の要素の種類つきの経路（`QPainter::drawPath`）。閉じた部分経路
    /// （始点と終点が同じ）は端を付けず、最後の辺から抜けを調べる。`caps` は
    /// 端を付けるペン（`FlatCap` 以外）か。
    pub(crate) fn path(&mut self, path: &Path, caps_enabled: bool) {
        let elements = path.elements();
        let mut index = 0;
        while index < elements.len() {
            let PathElement::MoveTo(start) = elements[index] else {
                index += 1;
                continue;
            };
            let mut end = index + 1;
            while end < elements.len() && !matches!(elements[end], PathElement::MoveTo(_)) {
                end += 1;
            }
            let last_point = match elements[end - 1] {
                PathElement::MoveTo(point) | PathElement::LineTo(point) => point,
                PathElement::CubicTo(_, _, point) => point,
            };
            self.last_pixel = (NO_PIXEL, NO_PIXEL);
            let closed = start.x == last_point.x && start.y == last_point.y;
            if closed && end - index > 1 {
                // 最後の 2 点（曲線なら最後の制御点と終点）。
                let before = match elements[end - 1] {
                    PathElement::CubicTo(_, c2, _) => c2,
                    _ => match elements[end - 2] {
                        PathElement::MoveTo(point) | PathElement::LineTo(point) => point,
                        PathElement::CubicTo(_, _, point) => point,
                    },
                };
                self.calculate_last_point(before.x, before.y, last_point.x, last_point.y);
            }
            let mut caps = if !closed && caps_enabled {
                CAP_BEGIN
            } else {
                0
            };
            let mut p = start;
            for (offset, element) in elements[index + 1..end].iter().enumerate() {
                let is_last = index + 1 + offset == end - 1;
                if !closed && caps_enabled && is_last {
                    caps |= CAP_END;
                }
                match *element {
                    PathElement::LineTo(p2) => {
                        self.line(p.x, p.y, p2.x, p2.y, caps);
                        p = p2;
                    }
                    PathElement::CubicTo(c1, c2, p4) => {
                        self.cubic(p, c1, c2, p4, caps);
                        p = p4;
                    }
                    PathElement::MoveTo(_) => {}
                }
                caps = 0;
            }
            index = end;
        }
    }
}

/// `splitCubic`（逆順に積んだ 4 点を、7 点の 2 本へ分ける）。
fn split_cubic(points: &mut [(f64, f64)]) {
    let half = 0.5;
    points[6].0 = points[3].0;
    let c = points[1].0;
    let d = points[2].0;
    let a = (points[0].0 + c) * half;
    points[1].0 = a;
    let b = (points[3].0 + d) * half;
    points[5].0 = b;
    let c = (c + d) * half;
    let a = (a + c) * half;
    points[2].0 = a;
    let b = (b + c) * half;
    points[4].0 = b;
    points[3].0 = (a + b) * half;

    points[6].1 = points[3].1;
    let c = points[1].1;
    let d = points[2].1;
    let a = (points[0].1 + c) * half;
    points[1].1 = a;
    let b = (points[3].1 + d) * half;
    points[5].1 = b;
    let c = (c + d) * half;
    let a = (a + c) * half;
    points[2].1 = a;
    let b = (b + c) * half;
    points[4].1 = b;
    points[3].1 = (a + b) * half;
}

/// 幅 `pen_width`（1 以下）のペンで描いた画素が、2 値化（不透明度 128 以上）で残るか。
///
/// Qt はペンの幅を不透明度にする（`opacity = int(256 * width)`）ので、幅 0.5 の線は
/// 不透明度 127 になり、`_binary_paint_overlay_rgba` で消える。
pub fn thin_pen_paints(pen_width: f64) -> bool {
    thin_pen_coverage(pen_width) >= 128
}

/// `QPainter::drawPath`（幅 1 以下のペン、非 AA、丸い端、ブラシなし）が色を置く画素。
/// 描く先は `(0, 0, width, height)`。置く色の不透明度はペンの幅で薄くなる
/// （[`thin_pen_paints`]）。
pub fn cosmetic_path(path: &Path, width: usize, height: usize) -> Mask {
    let mut mask = Mask::new(0, 0, width, height);
    cosmetic_path_pixels(path, width, height, |x, y| mask.set_local(x, y));
    mask
}

/// [`cosmetic_path`] の画素を、置く順に（同じ画素を 2 度置くこともそのまま）渡す。
pub(crate) fn cosmetic_path_pixels(
    path: &Path,
    width: usize,
    height: usize,
    sink: impl FnMut(i64, i64),
) {
    if width == 0 || height == 0 {
        return;
    }
    CosmeticStroker::new(width as i64, height as i64, sink).path(path, true);
}

/// 幅 `pen_width`（1 以下）のペンが 1 画素に置く不透明度（`QCosmeticStroker` の
/// `coverage * opacity >> 8`）。
pub(crate) fn thin_pen_coverage(pen_width: f64) -> u8 {
    let opacity = ((256.0 * pen_width) as i64).clamp(0, 256);
    ((255 * opacity) >> 8) as u8
}
