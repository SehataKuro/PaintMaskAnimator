//! 開いている作品の状態と、画面へ画素を渡す窓口。
//!
//! 今は表示の土台を確かめる段階なので、文書の代わりに「表示する画像」1枚だけを持ち、
//! 線は丸い点を並べるだけの仮の描き方で描く。文書（`pma_core::document::Document`）、
//! 重ね合わせ、ブラシは pma-core へ移す作業が済みしだい差し替える。

use pma_core::image::{Rect, RgbaImage};
use serde::Deserialize;
use std::sync::Mutex;

pub struct Session {
    pub canvas: RgbaImage,
    last: Option<Point>,
}

pub type Shared = Mutex<Session>;

#[derive(Clone, Copy, Debug, Deserialize)]
pub struct Point {
    /// 画像の座標（画素）。
    pub x: f64,
    pub y: f64,
    pub pressure: f64,
}

impl Session {
    pub fn new(width: usize, height: usize) -> Self {
        let mut canvas = RgbaImage::blank(width, height).expect("画像を作れなかった");
        let white = vec![255u8; width * height * 4];
        canvas
            .write_rgba(
                Rect {
                    x: 0,
                    y: 0,
                    width,
                    height,
                },
                &white,
            )
            .expect("白で塗れなかった");
        Self { canvas, last: None }
    }

    /// 仮の線：前の点から丸い点を並べる。変わった範囲を返す。
    pub fn stroke(&mut self, points: &[Point], begin: bool) -> Option<Rect> {
        const MAX_RADIUS: f64 = 12.0;
        if begin {
            self.last = None;
        }
        let mut dirty: Option<Rect> = None;
        for &p in points {
            let from = self.last.unwrap_or(p);
            let steps = ((p.x - from.x).hypot(p.y - from.y) / 1.0).ceil().max(1.0) as usize;
            for i in 0..=steps {
                let t = i as f64 / steps as f64;
                let x = from.x + (p.x - from.x) * t;
                let y = from.y + (p.y - from.y) * t;
                let pressure = from.pressure + (p.pressure - from.pressure) * t;
                let r = (MAX_RADIUS * pressure).max(0.5);
                if let Some(rect) = self.stamp(x, y, r) {
                    dirty = Some(union(dirty, rect));
                }
            }
            self.last = Some(p);
        }
        dirty
    }

    fn stamp(&mut self, cx: f64, cy: f64, r: f64) -> Option<Rect> {
        let (w, h) = (self.canvas.width() as i64, self.canvas.height() as i64);
        let x0 = ((cx - r).floor() as i64).clamp(0, w);
        let y0 = ((cy - r).floor() as i64).clamp(0, h);
        let x1 = ((cx + r).ceil() as i64 + 1).clamp(0, w);
        let y1 = ((cy + r).ceil() as i64 + 1).clamp(0, h);
        if x0 >= x1 || y0 >= y1 {
            return None;
        }
        let rect = Rect {
            x: x0 as usize,
            y: y0 as usize,
            width: (x1 - x0) as usize,
            height: (y1 - y0) as usize,
        };
        let mut px = self.canvas.copy_rgba_rect(rect).ok()?;
        for y in y0..y1 {
            for x in x0..x1 {
                let (dx, dy) = (x as f64 + 0.5 - cx, y as f64 + 0.5 - cy);
                if dx * dx + dy * dy <= r * r {
                    let i = (((y - y0) * (x1 - x0) + (x - x0)) * 4) as usize;
                    px[i..i + 4].copy_from_slice(&[0, 0, 0, 255]);
                }
            }
        }
        self.canvas.write_rgba(rect, &px).ok()?;
        Some(rect)
    }
}

pub fn union(a: Option<Rect>, b: Rect) -> Rect {
    let Some(a) = a else { return b };
    let x0 = a.x.min(b.x);
    let y0 = a.y.min(b.y);
    let x1 = (a.x + a.width).max(b.x + b.width);
    let y1 = (a.y + a.height).max(b.y + b.height);
    Rect {
        x: x0,
        y: y0,
        width: x1 - x0,
        height: y1 - y0,
    }
}

/// 範囲と画素を、画面へ渡す1つのバイト列にする：先頭に x・y・幅・高さ（u32、リトル
/// エンディアン）、続けてストレート RGBA の画素。
pub fn pack_rect(image: &RgbaImage, rect: Option<Rect>) -> Vec<u8> {
    let Some(rect) = rect else { return vec![0; 16] };
    let pixels = image.copy_rgba_rect(rect).unwrap_or_default();
    let mut out = Vec::with_capacity(16 + pixels.len());
    for v in [rect.x, rect.y, rect.width, rect.height] {
        out.extend_from_slice(&(v as u32).to_le_bytes());
    }
    out.extend_from_slice(&pixels);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stroke_marks_dirty_rect_and_paints_black() {
        let mut s = Session::new(64, 64);
        let rect = s
            .stroke(
                &[
                    Point {
                        x: 10.0,
                        y: 10.0,
                        pressure: 0.5,
                    },
                    Point {
                        x: 30.0,
                        y: 10.0,
                        pressure: 0.5,
                    },
                ],
                true,
            )
            .unwrap();
        assert!(rect.x <= 4 && rect.x + rect.width >= 36);
        let px = s
            .canvas
            .copy_rgba_rect(Rect {
                x: 20,
                y: 10,
                width: 1,
                height: 1,
            })
            .unwrap();
        assert_eq!(px, vec![0, 0, 0, 255]);
    }

    #[test]
    fn packs_header_then_pixels() {
        let s = Session::new(8, 8);
        let bytes = pack_rect(
            &s.canvas,
            Some(Rect {
                x: 1,
                y: 2,
                width: 3,
                height: 4,
            }),
        );
        assert_eq!(&bytes[0..4], &1u32.to_le_bytes());
        assert_eq!(&bytes[12..16], &4u32.to_le_bytes());
        assert_eq!(bytes.len(), 16 + 3 * 4 * 4);
        assert_eq!(pack_rect(&s.canvas, None), vec![0; 16]);
    }
}
