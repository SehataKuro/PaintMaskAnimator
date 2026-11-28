//! 1px のペン（`QCosmeticStroker`）で、変形を掛けた矩形の枠を描く。
//!
//! オニオンの外周（`_draw_onion_range` の `drawRect(outline_rect)`）は、幅 1 の
//! コスメティックペン・アンチエイリアスなし・実線で描く。Qt はこれを
//! qcosmeticstroker.cpp の `QCosmeticStroker::drawPath` で描き、辺ごとの
//! 抜け（dropout）と、角で同じ画素を 2 度打たない処理をする。ここはその
//! 非 AA・実線の道だけを写す（線そのものは `brush::cosmetic`）。

use super::painter::{Device, RectF, Transform};
use super::pixel::Premul;
use crate::brush::cosmetic::CosmeticStroker;

/// `QPainter::drawRect(rect)`（行列 `matrix`、幅 1 のコスメティックペン、非 AA、
/// 実線、ブラシなし）。`color` は不透明な乗算済みの色。
pub fn draw_rect_outline(device: &mut Device, matrix: &Transform, rect: RectF, color: Premul) {
    let (left, top, right, bottom) = (rect.x, rect.y, rect.right(), rect.bottom());
    let (width, height) = device.size();
    let implicit_close = matrix.no_shear();
    let points: Vec<(f64, f64)> = if implicit_close {
        // QRasterPaintEngine::drawRects：QRectVectorPath（4 点・暗に閉じる）。
        vec![(left, top), (right, top), (right, bottom), (left, bottom)]
    } else {
        // QPaintEngineEx::drawRects：始点へ戻る 5 点。
        vec![
            (left, top),
            (right, top),
            (right, bottom),
            (left, bottom),
            (left, top),
        ]
    };
    let mapped: Vec<(f64, f64)> = points.iter().map(|p| matrix.map(p.0, p.1)).collect();
    // 不透明な色なので、重ねると色そのものになる。
    let mut stroker = CosmeticStroker::new(width, height, |x, y| device.put(x, y, color, 255));
    stroker.polygon(&mapped, implicit_close);
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
