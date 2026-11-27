//! 1px のペン（`QCosmeticStroker`）で、変形を掛けた矩形の枠を描く。
//!
//! オニオンの外周（`_draw_onion_range` の `drawRect(outline_rect)`）は、幅 1 の
//! コスメティックペン・アンチエイリアスなし・実線で描く。Qt はこれを
//! qcosmeticstroker.cpp の `QCosmeticStroker::drawPath` で描き、辺ごとの
//! 抜け（dropout）と、角で同じ画素を 2 度打たない処理をする。ここはその
//! 非 AA・実線の道だけを写す（破線・アンチエイリアスは持たない）。

use super::painter::{Device, RectF, Transform};
use super::pixel::Premul;

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

struct Stroker<'d, 'a> {
    device: &'d mut Device<'a>,
    color: Premul,
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

impl<'d, 'a> Stroker<'d, 'a> {
    fn new(device: &'d mut Device<'a>, color: Premul) -> Self {
        let (width, height) = device.size();
        // deviceRect.left() - 1 〜 deviceRect.right() + 2。
        Self {
            device,
            color,
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

    /// `drawPixel`（不透明な色なので、重ねると色そのものになる）。
    fn pixel(&mut self, x: i64, y: i64) {
        if x < 0 || x >= self.width || y < 0 || y >= self.height {
            return;
        }
        self.device.put(x, y, self.color, 255);
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
    fn line(&mut self, rx1: f64, ry1: f64, rx2: f64, ry2: f64, caps: u8) -> bool {
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

    /// `drawPath` の要素の種類が無い（点の並びだけの）経路。
    fn polygon(&mut self, matrix: &Transform, points: &[(f64, f64)], implicit_close: bool) {
        if points.is_empty() {
            return;
        }
        let map = |p: (f64, f64)| matrix.map(p.0, p.1);
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

/// `QPainter::drawRect(rect)`（行列 `matrix`、幅 1 のコスメティックペン、非 AA、
/// 実線、ブラシなし）。`color` は不透明な乗算済みの色。
pub fn draw_rect_outline(device: &mut Device, matrix: &Transform, rect: RectF, color: Premul) {
    let (left, top, right, bottom) = (rect.x, rect.y, rect.right(), rect.bottom());
    let mut stroker = Stroker::new(device, color);
    if matrix.no_shear() {
        // QRasterPaintEngine::drawRects：QRectVectorPath（4 点・暗に閉じる）。
        let points = [(left, top), (right, top), (right, bottom), (left, bottom)];
        stroker.polygon(matrix, &points, true);
    } else {
        // QPaintEngineEx::drawRects：始点へ戻る 5 点。
        let points = [
            (left, top),
            (right, top),
            (right, bottom),
            (left, bottom),
            (left, top),
        ];
        stroker.polygon(matrix, &points, false);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::compose::PremulImage;

    #[test]
    fn axis_aligned_rect_outline_is_closed() {
        let mut image = PremulImage::transparent(10, 8).unwrap();
        let color = [255, 0, 0, 255];
        draw_rect_outline(
            &mut Device::new(&mut image),
            &Transform::identity(),
            RectF::new(1.5, 1.5, 6.0, 4.0),
            color,
        );
        // 辺は 1 画素、四隅もつながる。
        for x in 1..=7 {
            assert_eq!(image.pixel(x, 1), color, "上 {x}");
            assert_eq!(image.pixel(x, 5), color, "下 {x}");
        }
        for y in 1..=5 {
            assert_eq!(image.pixel(1, y), color, "左 {y}");
            assert_eq!(image.pixel(7, y), color, "右 {y}");
        }
        assert_eq!(image.pixel(3, 3), [0; 4]);
    }
}
