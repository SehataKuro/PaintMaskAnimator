//! ブラシの 1 区間と筆先の形（`brush_raster.paint_segment` / `stamp_mask`）。

use super::mask::Mask;
use super::qt::{points_equal, Point};
use super::raster::{cosmetic_line, fill_path, FillRule};
use super::stroker::stroke_polyline;

/// `paint_segment`：`(0, 0, width, height)` の画像へ、幅 `pen_width` の丸い端の線を
/// アンチエイリアスなしで描いた画素。ペンの幅は 1 未満にしない。
///
/// - 幅 1 以下は Qt の 1px 線（`QCosmeticStroker`）の画素になる。
/// - 始点と終点が等しければ `drawPoint`（太いペンは長さ 1/63 の横線）になる。
pub fn segment_mask(start: Point, end: Point, pen_width: f64, width: usize, height: usize) -> Mask {
    let pen = pen_width.max(1.0);
    if pen <= 1.0 {
        return cosmetic_line(start, end, width, height);
    }
    let points = if points_equal(start, end) {
        [start, Point::new(start.x + 1.0 / 63.0, start.y)]
    } else {
        [start, end]
    };
    fill_path(
        &stroke_polyline(&points, pen),
        FillRule::Winding,
        width,
        height,
    )
}

/// `stamp_mask`：`center` に幅 `width` の筆先を 1 回押した画素を、画像の座標の面で返す。
/// 画素単位の筆先カーソル（`stamp_outline`）は、この面の `runs` を矩形として描く。
pub fn stamp_mask(center: Point, width: f64) -> Mask {
    let margin = (width / 2.0).ceil() as i64 + 3;
    let origin_x = center.x.floor() as i64 - margin;
    let origin_y = center.y.floor() as i64 - margin;
    let size = (margin * 2 + 1) as usize;
    let local = Point::new(center.x - origin_x as f64, center.y - origin_y as f64);
    let mut mask = segment_mask(local, local, width, size, size);
    mask.x = origin_x;
    mask.y = origin_y;
    mask
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn thin_pens_paint_single_pixels() {
        let mask = stamp_mask(Point::new(20.25, 20.75), 0.5);
        assert_eq!(mask.count(), 1);
        assert!(mask.get(20, 20));
    }

    #[test]
    fn wide_stamp_is_symmetric_around_pixel_centre() {
        let mask = stamp_mask(Point::new(10.5, 10.5), 5.0);
        assert_eq!(mask.count(), 21);
        for (dx, dy) in [(-2, 0), (2, 0), (0, -2), (0, 2)] {
            assert!(mask.get(10 + dx, 10 + dy));
        }
        assert!(!mask.get(12, 12));
    }
}
