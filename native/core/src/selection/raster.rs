//! 多角形を、Qt の raster エンジンがアンチエイリアスなしで塗るのと同じ画素で二値にする。
//!
//! Python の選択範囲は、`QPainter.drawPolygon`（選択マスク・削除・変形の切り出し）と
//! `QPainter.setClipPath`（描画ツールのクリップ）の2通りで多角形を画素にする。どちらも
//! アンチエイリアスなしで、塗りの規則は偶奇（OddEvenFill）。Qt はどちらも同じ道を通る。
//!
//! - 軸に沿った長方形（左上から時計回りの4点。5点目が始点と同じでもよい）は、
//!   座標を `qRound` して矩形として塗る（qpaintengine_raster.cpp の isRect と
//!   qt_mapFillRect / toNormalizedFillRect）。
//! - それ以外は QOutlineMapper で 26.6 の固定小数点へ丸め（qRound(v * 64)）、
//!   QScanConverter（qrasterizer.cpp）で走査線ごとに塗る。辺の x は 16.16 の固定小数点で、
//!   1行ごとに傾きを足していく。この積み重ねの丸めも Qt と同じにする。
//!
//! 座標が Qt の上限（およそ ±2^23 画素）を超える多角形は、Qt が事前に切り詰めるので
//! 一致しない。画面の座標ではこの範囲を超えない。

/// 多角形の頂点（画像の座標。画素の左上が整数）。
pub type Point = (f64, f64);

/// 16.16 の固定小数点の 1。
const ONE_FP: i32 = 1 << 16;
const HALF_FP: i32 = ONE_FP / 2;

/// Qt の qRound（0.5 は 0 から遠い方へ）。範囲外は i32 の端に寄せる。
fn q_round(value: f64) -> i32 {
    value.round() as i32
}

/// 26.6 の固定小数点（QOutlineMapper の qreal_to_fixed_26_6）。
fn to_fixed_26_6(value: f64) -> i32 {
    q_round(value * 64.0)
}

/// Q16Dot16Multiply。
fn mul_fp(a: i32, b: i32) -> i32 {
    ((a as i64 * b as i64) >> 16) as i32
}

/// 左上から時計回りの、軸に沿った長方形なら (左, 上, 右, 下) を返す（Qt の isRect）。
fn axis_rect(points: &[Point]) -> Option<(f64, f64, f64, f64)> {
    let four = match points.len() {
        4 => points,
        5 if points[4] == points[0] => &points[..4],
        _ => return None,
    };
    let [(x0, y0), (x1, y1), (x2, y2), (x3, y3)] = [four[0], four[1], four[2], four[3]];
    (x0 == x3 && x1 == x2 && y0 == y1 && y2 == y3 && x0 < x2 && y0 < y2).then_some((x0, y0, x2, y2))
}

/// `mask`（幅 `width` の行優先、1 画素 1 バイト）の `[left, right)` を 1 にする。
fn fill_span(mask: &mut [u8], width: usize, y: usize, left: i64, right: i64) {
    let left = left.clamp(0, width as i64) as usize;
    let right = right.clamp(0, width as i64) as usize;
    if right > left {
        mask[y * width + left..y * width + right].fill(1);
    }
}

/// 長方形を Qt の qt_mapFillRect と同じく丸めて塗る。
fn fill_rect(mask: &mut [u8], width: usize, height: usize, rect: (f64, f64, f64, f64)) {
    let (left, top, right, bottom) = rect;
    // QRectF(x, y, w, h).right() は x + w。点から作り直した値をそのまま丸める。
    let x1 = q_round(left) as i64;
    let y1 = q_round(top) as i64;
    let x2 = q_round(left + (right - left)) as i64;
    let y2 = q_round(top + (bottom - top)) as i64;
    let top = y1.clamp(0, height as i64) as usize;
    let bottom = y2.clamp(0, height as i64) as usize;
    for y in top..bottom {
        fill_span(mask, width, y, x1, x2);
    }
}

/// 走査線変換で使う辺。x は今の行での交点（16.16、0.5 画素ずらし済み）。
struct Line {
    x: i32,
    delta: i32,
    top: i32,
    bottom: i32,
    winding: i32,
}

/// 多角形をアンチエイリアスなし・偶奇規則で塗り、`mask` の該当画素を 1 にする。
///
/// `mask` は `width * height` バイト。頂点は `offset` を引いてから塗る
/// （Python の `point.x() - offset_x`）。頂点が3つ未満なら何も塗らない。
pub(crate) fn fill_polygon(
    mask: &mut [u8],
    width: usize,
    height: usize,
    points: &[Point],
    offset: Point,
) {
    if width == 0 || height == 0 || points.len() < 3 {
        return;
    }
    let local: Vec<Point> = points
        .iter()
        .map(|&(x, y)| (x - offset.0, y - offset.1))
        .collect();
    if let Some(rect) = axis_rect(&local) {
        fill_rect(mask, width, height, rect);
        return;
    }
    let fixed: Vec<(i32, i32)> = local
        .iter()
        .map(|&(x, y)| (to_fixed_26_6(x), to_fixed_26_6(y)))
        .collect();
    let clip_top = 0i32;
    let clip_bottom = (height.min(i32::MAX as usize) - 1) as i32;
    let min_y = fixed.iter().map(|p| p.1).min().unwrap_or(0);
    let max_y = fixed.iter().map(|p| p.1).max().unwrap_or(0);
    let top = clip_top.max(min_y.saturating_add(32) >> 6);
    let bottom = clip_bottom.min(max_y.saturating_sub(32) >> 6);
    if top > bottom {
        return;
    }

    let mut lines = Vec::with_capacity(fixed.len());
    for index in 0..fixed.len() {
        let mut a = fixed[index];
        let mut b = fixed[(index + 1) % fixed.len()];
        let mut winding = 1;
        if a.1 > b.1 {
            std::mem::swap(&mut a, &mut b);
            winding = -1;
        }
        let line_top = top.max(a.1.saturating_add(32) >> 6);
        let line_bottom = bottom.min(b.1.saturating_sub(32) >> 6);
        if line_top > line_bottom {
            continue;
        }
        let a_fp = HALF_FP.wrapping_add(a.0.wrapping_mul(1 << 10));
        if b.0 == a.0 {
            lines.push(Line {
                x: a_fp,
                delta: 0,
                top: line_top,
                bottom: line_bottom,
                winding,
            });
        } else {
            let slope = (b.0 as f64 - a.0 as f64) / (b.1 as f64 - a.1 as f64);
            // FloatToQ16Dot16 は切り捨て。範囲外は（arm64 の Qt と同じく）端に寄せる。
            let slope_fp = (slope * ONE_FP as f64) as i32;
            let start = line_top
                .wrapping_mul(ONE_FP)
                .wrapping_add(HALF_FP)
                .wrapping_sub(a.1.wrapping_mul(1 << 10));
            lines.push(Line {
                x: a_fp.wrapping_add(mul_fp(slope_fp, start)),
                delta: slope_fp,
                top: line_top,
                bottom: line_bottom,
                winding,
            });
        }
    }

    let mut crossings: Vec<(i32, i32)> = Vec::with_capacity(lines.len());
    for y in top..=bottom {
        crossings.clear();
        for line in lines.iter_mut() {
            if line.top <= y && y <= line.bottom {
                crossings.push((line.x >> 16, line.winding));
                line.x = line.x.wrapping_add(line.delta);
            }
        }
        crossings.sort_by_key(|&(x, _)| x);
        let mut x = 0i64;
        let mut winding = 0i32;
        for &(current, line_winding) in &crossings {
            if winding & 1 != 0 {
                fill_span(mask, width, y as usize, x, current as i64);
            }
            x = current as i64;
            winding += line_winding;
        }
    }
}
