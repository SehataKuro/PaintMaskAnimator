//! 直線ツール・図形ツール・投げ縄塗りの形を、Qt と同じ画素で塗る。
//!
//! - 塗り：`fill_polygon`（`drawPolygon`）と `raster::fill_path`（`drawPath` のブラシ）。
//! - 線：`stroke_path`（`drawPath` の丸い端・丸い角のペン）。直線と曲線（2 次曲線の
//!   直線ツール・楕円の輪郭）のどちらも描く。

use super::arc::curves_for_arc;
use super::cosmetic::cosmetic_path;
use super::mask::Mask;
use super::qt::Point;
use super::raster::{fill_path, FillRule, Path};
use super::stroker::stroke_outline;

/// `QPainter::drawPolygon`（ペンなし、ブラシで塗る）。既定の規則は偶奇。
pub fn fill_polygon(points: &[Point], rule: FillRule, width: usize, height: usize) -> Mask {
    fill_path(&Path::from_polygon(points), rule, width, height)
}

/// `QPainterPath::addEllipse(rect)` の経路。`rect` は `(x, y, width, height)`。
pub fn ellipse_path(rect: (f64, f64, f64, f64)) -> Path {
    let mut path = Path::new();
    if rect.2 == 0.0 && rect.3 == 0.0 {
        return path;
    }
    let (start, curves) = curves_for_arc(rect, 0.0, -360.0);
    path.move_to(start);
    for triple in curves.chunks_exact(3) {
        path.cubic_to(triple[0], triple[1], triple[2]);
    }
    path
}

/// `QPainter::drawPath`（丸い端・丸い角、幅 `pen_width` のペン、ブラシなし、非 AA）の画素。
///
/// 幅 1 以下は `QCosmeticStroker`（`cosmetic::cosmetic_path`。色の不透明度は幅で
/// 薄くなる）、それより太い線は `QStroker` の輪郭を巻き数で塗る。
pub fn stroke_path(path: &Path, pen_width: f64, width: usize, height: usize) -> Mask {
    if pen_width <= 1.0 {
        return cosmetic_path(path, width, height);
    }
    fill_path(
        &stroke_outline(path, pen_width),
        FillRule::Winding,
        width,
        height,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ellipse_fill_is_symmetric() {
        let path = ellipse_path((2.0, 2.0, 10.0, 6.0));
        let mask = fill_path(&path, FillRule::OddEven, 14, 10);
        assert!(mask.get(7, 5) && !mask.get(2, 2));
        for y in 0..10 {
            for x in 0..14 {
                assert_eq!(mask.get(x, y), mask.get(13 - x, y), "({x}, {y})");
            }
        }
    }

    #[test]
    fn closed_square_outline_has_a_hole() {
        let mut path = Path::new();
        path.move_to(Point::new(2.0, 2.0));
        path.line_to(Point::new(12.0, 2.0));
        path.line_to(Point::new(12.0, 12.0));
        path.line_to(Point::new(2.0, 12.0));
        path.close();
        let mask = stroke_path(&path, 3.0, 16, 16);
        assert!(mask.get(2, 7) && mask.get(12, 7) && !mask.get(7, 7));
        let ring = stroke_path(&ellipse_path((1.0, 1.0, 10.0, 10.0)), 3.0, 12, 12);
        assert!(ring.get(1, 6) && !ring.get(6, 6));
    }
}
