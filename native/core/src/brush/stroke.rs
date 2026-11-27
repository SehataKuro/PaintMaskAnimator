//! 押してから離すまでの 1 本のブラシ・消しゴムのストローク。
//!
//! `canvas_input_events`・`canvas_brush_stabilizer`・`canvas_paint_tools.draw_line`・
//! `canvas._blend_overlay_into_active_layer`・`canvas_stroke_display` と同じ画素になる。
//!
//! 1. `BrushStroke::begin` で押した点を描く。
//! 2. 動かすたびに `move_to`、入力が止まっている間は約 16 ms ごとに `tick`
//!    （または `advance_time`）を呼ぶ。戻り値は画素が変わりうる矩形。
//! 3. 途中の見た目は `image()`（層にこれまでの線を描いた画像）の、その矩形を読む。
//! 4. `finish` で離した点まで描き、層の新しい画像と Undo 用の差分を受け取る。

use super::mask::Mask;
use super::pressure::PressureSettings;
use super::qt::Point;
use super::raster::{fill_path, FillRule, Path};
use super::segment::segment_mask;
use super::stabilizer::{Segment, Stabilizer, FOLLOW_INTERVAL_MS};
use crate::image::{Rect, RgbaImage};
use crate::Error;

/// 消しゴムを表す色（`#FFFFFF`）。この色で描いた画素は透明（すべて 0）になる。
pub const ERASER_RGB: [u8; 3] = [255, 255, 255];

/// 「選んだマスク色の上だけに描く」設定（`mask_all_enabled` が偽のとき）。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct MaskColorFilter {
    /// 描いてよい下地の色（不透明な画素の RGB）。空なら何も描かない。
    pub colors: Vec<[u8; 3]>,
    /// 背景のマスク色が選ばれているか。真なら透明と白の画素にも描く。
    pub background: bool,
}

impl MaskColorFilter {
    fn allows(&self, pixel: &[u8]) -> bool {
        if self.colors.is_empty() {
            return false;
        }
        let rgb = [pixel[0], pixel[1], pixel[2]];
        (pixel[3] > 0 && self.colors.contains(&rgb))
            || (self.background && (pixel[3] == 0 || rgb == ERASER_RGB))
    }
}

/// 選択範囲。描く画素は選択の中だけになる。
#[derive(Clone, Debug, PartialEq)]
pub enum Selection {
    /// 画像の座標の 2 値の面（`selection_mask_override`）。
    Mask(Mask),
    /// 投げ縄・矩形の選択の多角形（`selection_polygon`、偶奇の塗り）。3 点未満は選択なし。
    Polygon(Vec<Point>),
}

impl Selection {
    /// 描く先の矩形 `rect`（画像の座標）での選択の画素。
    pub(crate) fn mask_for(&self, rect: Rect) -> Option<Mask> {
        match self {
            Self::Mask(mask) => {
                let mut local = Mask::new(rect.x as i64, rect.y as i64, rect.width, rect.height);
                for row in 0..rect.height {
                    for column in 0..rect.width {
                        if mask.get((rect.x + column) as i64, (rect.y + row) as i64) {
                            local.set_local(column as i64, row as i64);
                        }
                    }
                }
                Some(local)
            }
            Self::Polygon(points) if points.len() >= 3 => {
                // Python と同じく、多角形を描く先の左上へずらしてから塗る。
                let (dx, dy) = (rect.x as f64, rect.y as f64);
                let shifted: Vec<Point> = points
                    .iter()
                    .map(|p| Point::new(p.x - dx, p.y - dy))
                    .collect();
                let mut path = Path::from_polygon(&shifted);
                path.close();
                let mut mask = fill_path(&path, FillRule::OddEven, rect.width, rect.height);
                mask.x = rect.x as i64;
                mask.y = rect.y as i64;
                Some(mask)
            }
            Self::Polygon(_) => None,
        }
    }
}

/// ブラシ・消しゴムの設定。
#[derive(Clone, Debug, PartialEq)]
pub struct BrushSettings {
    /// ブラシの大きさ（`pen_size`）。
    pub size: f64,
    /// 描く色。`ERASER_RGB` は消しゴムになる。
    pub color: [u8; 3],
    /// 真なら `color` によらず消しゴム。
    pub eraser: bool,
    /// 筆圧の設定。
    pub pressure: PressureSettings,
    /// 手ぶれ補正の強さ（0〜300）。
    pub stabilizer: i64,
    /// 表示倍率（手ぶれ補正の半径と刻みに使う）。
    pub zoom: f64,
    /// 透明部分の保護。真なら、押す前に不透明だった画素にだけ描き、消しゴムは何もしない。
    /// Qt の画面にはない機能で、偽なら Python と同じ画素になる。
    pub alpha_lock: bool,
    /// 選んだマスク色の上だけに描く。`None` は全体に描く（`mask_all_enabled`）。
    pub mask_colors: Option<MaskColorFilter>,
    /// 選択範囲。
    pub selection: Option<Selection>,
}

impl Default for BrushSettings {
    fn default() -> Self {
        Self {
            size: 5.0,
            color: [0, 0, 0],
            eraser: false,
            pressure: PressureSettings::default(),
            stabilizer: 0,
            zoom: 1.0,
            alpha_lock: false,
            mask_colors: None,
            selection: None,
        }
    }
}

impl BrushSettings {
    fn paint_rgb(&self) -> [u8; 3] {
        if self.eraser {
            ERASER_RGB
        } else {
            self.color
        }
    }
}

/// ポインターの 1 回の入力。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PointerSample {
    /// 画像の座標の x。
    pub x: f64,
    /// 画像の座標の y。
    pub y: f64,
    /// ペンの筆圧（0〜1）。`None` はマウス（筆圧 1、平滑化しない）。
    pub pressure: Option<f64>,
    /// 入力の時刻（ミリ秒、単調増加）。`advance_time` の基準に使う。
    pub time_ms: f64,
}

impl PointerSample {
    fn point(&self) -> Point {
        Point::new(self.x, self.y)
    }
}

/// Undo に積む差分。`before` と `after` は `rect` の RGBA（行優先）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UndoPatch {
    /// 変わりうる範囲（各区間の描く先の矩形を合わせたもの）。
    pub rect: Rect,
    /// 押す前の画素。
    pub before: Vec<u8>,
    /// 離したあとの画素。
    pub after: Vec<u8>,
}

/// ストロークを終えた結果。
#[derive(Clone, Debug)]
pub struct StrokeResult {
    /// 線を描いた層の画像。
    pub image: RgbaImage,
    /// Undo の差分。どの区間も画像に掛からなければ `None`。
    pub undo: Option<UndoPatch>,
    /// 実際に塗った色（消しゴムの白は含めない）。使用色の通知に使う。
    pub colors: Vec<[u8; 3]>,
}

/// 1 本のストローク。
#[derive(Clone, Debug)]
pub struct BrushStroke {
    settings: BrushSettings,
    base: RgbaImage,
    work: RgbaImage,
    stabilizer: Stabilizer,
    dirty: Option<Rect>,
    painted: bool,
    pen: bool,
    next_tick_ms: f64,
}

fn union(a: Option<Rect>, b: Rect) -> Rect {
    match a {
        None => b,
        Some(a) => {
            let left = a.x.min(b.x);
            let top = a.y.min(b.y);
            let right = (a.x + a.width).max(b.x + b.width);
            let bottom = (a.y + a.height).max(b.y + b.height);
            Rect {
                x: left,
                y: top,
                width: right - left,
                height: bottom - top,
            }
        }
    }
}

impl BrushStroke {
    /// 押した点 `sample` からストロークを始め、押した点を描く。
    /// 戻り値の矩形は画素が変わりうる所（画像の外にかかる区間は含まない）。
    pub fn begin(
        layer: &RgbaImage,
        settings: BrushSettings,
        sample: PointerSample,
    ) -> Result<(Self, Vec<Rect>), Error> {
        let mut stabilizer = Stabilizer::new(settings.stabilizer, settings.zoom);
        let point = sample.point();
        stabilizer.reset(point);
        let pen = sample.pressure.is_some();
        let pressure = match sample.pressure {
            Some(value) => stabilizer.smooth_pressure(value.max(0.001)),
            None => 1.0,
        };
        let mut stroke = Self {
            settings,
            base: layer.clone(),
            work: layer.clone(),
            stabilizer,
            dirty: None,
            painted: false,
            pen,
            next_tick_ms: sample.time_ms + FOLLOW_INTERVAL_MS,
        };
        let mut rects = Vec::new();
        if let Some(rect) = stroke.draw_segment(point, point, pressure)? {
            rects.push(rect);
        }
        Ok((stroke, rects))
    }

    /// 設定。
    pub fn settings(&self) -> &BrushSettings {
        &self.settings
    }

    /// 層にこれまでの線を描いた画像（途中の見た目）。
    pub fn image(&self) -> &RgbaImage {
        &self.work
    }

    /// 押す前の層の画像。
    pub fn base(&self) -> &RgbaImage {
        &self.base
    }

    /// これまでに変わりうる範囲。
    pub fn dirty_rect(&self) -> Option<Rect> {
        self.dirty
    }

    /// 手ぶれ補正の追従のために `tick` を呼び続ける必要があるか。
    pub fn needs_ticks(&self) -> bool {
        self.stabilizer.follow_active()
    }

    fn pressure_for(&mut self, sample: &PointerSample) -> f64 {
        match sample.pressure {
            Some(value) => self.stabilizer.smooth_pressure(value.max(0.001)),
            None => 1.0,
        }
    }

    fn draw_all(&mut self, segments: Vec<Segment>) -> Result<Vec<Rect>, Error> {
        let mut rects = Vec::new();
        for segment in segments {
            if let Some(rect) = self.draw_segment(segment.start, segment.end, segment.pressure)? {
                rects.push(rect);
            }
        }
        Ok(rects)
    }

    /// ポインターを動かした点まで描く。
    pub fn move_to(&mut self, sample: PointerSample) -> Result<Vec<Rect>, Error> {
        let pressure = self.pressure_for(&sample);
        let segments = self.stabilizer.move_to(sample.point(), pressure);
        self.draw_all(segments)
    }

    /// 手ぶれ補正の追従タイマー 1 回分を進める（約 16 ms ごと）。
    pub fn tick(&mut self) -> Result<Vec<Rect>, Error> {
        let segments = self.stabilizer.tick();
        self.draw_all(segments)
    }

    /// 時刻 `now_ms` までに過ぎた 16 ms ごとの `tick` をまとめて進める。
    pub fn advance_time(&mut self, now_ms: f64) -> Result<Vec<Rect>, Error> {
        let mut rects = Vec::new();
        let mut guard = 0;
        while self.stabilizer.follow_active() && self.next_tick_ms <= now_ms && guard < 1024 {
            rects.extend(self.tick()?);
            self.next_tick_ms += FOLLOW_INTERVAL_MS;
            guard += 1;
        }
        if self.next_tick_ms <= now_ms {
            self.next_tick_ms = now_ms + FOLLOW_INTERVAL_MS;
        }
        Ok(rects)
    }

    /// 離した点まで描いて（`None` なら離した点は描かない）、ストロークを終える。
    pub fn finish(mut self, release: Option<PointerSample>) -> Result<StrokeResult, Error> {
        if let Some(sample) = release {
            // ペンは離したときの筆圧ではなく、直前の筆圧で終える。
            let pressure = if self.pen {
                self.stabilizer.last_pressure().max(0.001)
            } else {
                1.0
            };
            let segments = self.stabilizer.finish(sample.point(), pressure);
            self.draw_all(segments)?;
        }
        let undo = match self.dirty {
            None => None,
            Some(rect) => Some(UndoPatch {
                rect,
                before: self.base.copy_rgba_rect(rect)?,
                after: self.work.copy_rgba_rect(rect)?,
            }),
        };
        let rgb = self.settings.paint_rgb();
        let colors = if self.painted && rgb != ERASER_RGB {
            vec![rgb]
        } else {
            Vec::new()
        };
        Ok(StrokeResult {
            image: self.work,
            undo,
            colors,
        })
    }

    /// `draw_line` と `_blend_overlay_into_active_layer`（筆の経路）。
    fn draw_segment(&mut self, a: Point, b: Point, pressure: f64) -> Result<Option<Rect>, Error> {
        let width = 0.5f64.max(self.settings.size * self.settings.pressure.size_scale(pressure));
        let draw_width = 0.5f64.max(width);
        let values = [a.x, a.y, b.x, b.y];
        if values.iter().any(|value| !value.is_finite()) {
            return Ok(None);
        }
        let image_width = self.work.width() as i64;
        let image_height = self.work.height() as i64;
        let margin = (draw_width / 2.0).ceil() as i64 + 3;
        let left = 0.max(a.x.min(b.x).floor() as i64 - margin);
        let top = 0.max(a.y.min(b.y).floor() as i64 - margin);
        let right = image_width.min(a.x.max(b.x).ceil() as i64 + margin + 1);
        let bottom = image_height.min(a.y.max(b.y).ceil() as i64 + margin + 1);
        if right <= left || bottom <= top {
            return Ok(None);
        }
        let rect = Rect {
            x: left as usize,
            y: top as usize,
            width: (right - left) as usize,
            height: (bottom - top) as usize,
        };
        self.dirty = Some(union(self.dirty, rect));
        let (ox, oy) = (rect.x as f64, rect.y as f64);
        let mut mask = segment_mask(
            Point::new(a.x - ox, a.y - oy),
            Point::new(b.x - ox, b.y - oy),
            draw_width,
            rect.width,
            rect.height,
        );
        mask.x = left;
        mask.y = top;
        if let Some(selection) = &self.settings.selection {
            if let Some(clip) = selection.mask_for(rect) {
                mask.intersect(&clip);
            }
        }
        let rgb = self.settings.paint_rgb();
        let erase = rgb == ERASER_RGB;
        if erase && self.settings.alpha_lock {
            return Ok(Some(rect));
        }
        let before = self.base.copy_rgba_rect(rect)?;
        let mut pixels = self.work.copy_rgba_rect(rect)?;
        let mut changed = false;
        for (index, &bit) in mask.bits().iter().enumerate() {
            if !bit {
                continue;
            }
            let base = &before[index * 4..index * 4 + 4];
            if let Some(filter) = &self.settings.mask_colors {
                if !filter.allows(base) {
                    continue;
                }
            }
            if self.settings.alpha_lock && base[3] == 0 {
                continue;
            }
            let value = if erase {
                [0, 0, 0, 0]
            } else {
                [rgb[0], rgb[1], rgb[2], 255]
            };
            pixels[index * 4..index * 4 + 4].copy_from_slice(&value);
            changed = true;
        }
        if changed {
            self.painted = true;
            self.work.write_rgba(rect, &pixels)?;
        }
        Ok(Some(rect))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mouse(x: f64, y: f64) -> PointerSample {
        PointerSample {
            x,
            y,
            pressure: None,
            time_ms: 0.0,
        }
    }

    #[test]
    fn stroke_paints_and_records_undo() {
        let layer = RgbaImage::blank(32, 32).unwrap();
        let settings = BrushSettings {
            size: 3.0,
            color: [10, 20, 30],
            ..BrushSettings::default()
        };
        let (mut stroke, rects) = BrushStroke::begin(&layer, settings, mouse(5.0, 5.0)).unwrap();
        assert_eq!(rects.len(), 1);
        stroke.move_to(mouse(20.0, 5.0)).unwrap();
        let result = stroke.finish(Some(mouse(20.0, 5.0))).unwrap();
        let undo = result.undo.unwrap();
        assert!(undo.before.iter().all(|&value| value == 0));
        let pixel = (5 * 32 + 12) * 4;
        assert_eq!(&result.image.rgba()[pixel..pixel + 4], &[10, 20, 30, 255]);
        assert_eq!(result.colors, vec![[10, 20, 30]]);
        assert_eq!(layer.rgba()[pixel + 3], 0);
    }

    #[test]
    fn eraser_clears_and_respects_alpha_lock() {
        let layer = RgbaImage::from_rgba(8, 8, &[200; 8 * 8 * 4]).unwrap();
        let settings = BrushSettings {
            size: 2.0,
            eraser: true,
            ..BrushSettings::default()
        };
        let (stroke, _) = BrushStroke::begin(&layer, settings.clone(), mouse(4.0, 4.0)).unwrap();
        let result = stroke.finish(None).unwrap();
        let pixel = (4 * 8 + 4) * 4;
        assert_eq!(&result.image.rgba()[pixel - 4..pixel], &[0, 0, 0, 0]);
        assert!(result.colors.is_empty());
        let locked = BrushSettings {
            alpha_lock: true,
            ..settings
        };
        let (stroke, _) = BrushStroke::begin(&layer, locked, mouse(4.0, 4.0)).unwrap();
        assert_eq!(stroke.finish(None).unwrap().image.rgba(), layer.rgba());
    }

    #[test]
    fn mask_colors_and_selection_limit_painting() {
        let mut pixels = vec![0u8; 16 * 16 * 4];
        for y in 0..16 {
            for x in 8..16 {
                pixels[(y * 16 + x) * 4..(y * 16 + x) * 4 + 4].copy_from_slice(&[1, 2, 3, 255]);
            }
        }
        let layer = RgbaImage::from_rgba(16, 16, &pixels).unwrap();
        let settings = BrushSettings {
            size: 6.0,
            color: [9, 9, 9],
            mask_colors: Some(MaskColorFilter {
                colors: vec![[1, 2, 3]],
                background: false,
            }),
            selection: Some(Selection::Polygon(vec![
                Point::new(0.0, 0.0),
                Point::new(16.0, 0.0),
                Point::new(16.0, 8.0),
                Point::new(0.0, 8.0),
            ])),
            ..BrushSettings::default()
        };
        let (mut stroke, _) = BrushStroke::begin(&layer, settings, mouse(2.0, 8.0)).unwrap();
        stroke.move_to(mouse(14.0, 8.0)).unwrap();
        let image = stroke.finish(None).unwrap().image;
        let at = |x: usize, y: usize| image.rgba()[(y * 16 + x) * 4];
        assert_eq!(at(4, 7), 0, "マスク色でない所には描かない");
        assert_eq!(at(12, 7), 9);
        assert_eq!(at(12, 9), 1, "選択の外には描かない");
    }
}
