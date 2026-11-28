//! 直線ツール（直線・2 次曲線・入り抜き）と図形ツール（多角形・楕円、塗りと輪郭）。
//!
//! `canvas_paint_tools.commit_line_tool`・`_draw_tapered_path`・`_shape_path`・
//! `commit_shape_tool` と `canvas._blend_overlay_into_active_layer` と同じ画素になる。
//!
//! 確定前の見た目（#117）は、確定と同じ画素の面（[`line_mask`]・[`shape_masks`]）を
//! そのまま重ねて描けばよい。Python の画面は確定前に別の描き方（アンチエイリアスの
//! QPainter）で描いていたので、見た目と確定がずれていた。
//!
//! 細いペン（幅 1 以下）は Qt が幅を色の不透明度にする（`cosmetic::thin_pen_paints`）。
//! 薄い色は同じ画素に重なると濃くなる（SourceOver）ので、層と同じ大きさの不透明度を
//! 重ねて計算し、2 値化（不透明度 128 以上）で残る画素を面にする。

use super::bezier::Bezier;
use super::cosmetic::{cosmetic_path_pixels, thin_pen_coverage};
use super::mask::Mask;
use super::qt::{points_equal, Line, Point};
use super::raster::{cosmetic_line, fill_path, FillRule, Path, PathElement};
use super::segment::segment_mask;
use super::shapes::{ellipse_path, stroke_path};
use super::stroke::{Selection, UndoPatch, ERASER_RGB};
use crate::color_stats::snap_distance;
use crate::image::{Rect, RgbaImage};
use crate::Error;

/// 直線ツールの線（`_line_preview_path`）。`control` があれば 2 次曲線。
pub fn line_path(start: Point, end: Point, control: Option<Point>) -> Path {
    let mut path = Path::new();
    path.move_to(start);
    match control {
        Some(control) => {
            // QPainterPath::quadTo：空の曲線は足さない。3 次へは Qt と同じ式で直す。
            if points_equal(start, control) && points_equal(control, end) {
                return path;
            }
            let c1 = Point::new(
                (start.x + 2.0 * control.x) / 3.0,
                (start.y + 2.0 * control.y) / 3.0,
            );
            let c2 = Point::new(
                (end.x + 2.0 * control.x) / 3.0,
                (end.y + 2.0 * control.y) / 3.0,
            );
            path.cubic_to(c1, c2, end);
        }
        None => path.line_to(end),
    }
    path
}

fn element_end(element: &PathElement) -> Point {
    match *element {
        PathElement::MoveTo(point) | PathElement::LineTo(point) => point,
        PathElement::CubicTo(_, _, point) => point,
    }
}

/// `QPainterPath::length`。
pub fn path_length(path: &Path) -> f64 {
    let elements = path.elements();
    let mut length = 0.0;
    for index in 1..elements.len() {
        let previous = element_end(&elements[index - 1]);
        match elements[index] {
            PathElement::MoveTo(_) => {}
            PathElement::LineTo(point) => {
                length += Line {
                    p1: previous,
                    p2: point,
                }
                .length()
            }
            PathElement::CubicTo(c1, c2, end) => {
                length += Bezier::new(previous, c1, c2, end).length();
            }
        }
    }
    length
}

/// `QPainterPath::pointAtPercent`（`t` は 0〜1）。
pub fn point_at_percent(path: &Path, t: f64) -> Point {
    let elements = path.elements();
    match elements.len() {
        0 => return Point::new(0.0, 0.0),
        1 => return element_end(&elements[0]),
        _ => {}
    }
    let total = path_length(path);
    // bezierAtT
    let mut current = 0.0;
    let mut starting = 0.0;
    let mut found: Option<(Bezier, f64)> = None;
    let last = elements.len() - 1;
    for index in 0..=last {
        match elements[index] {
            PathElement::MoveTo(_) => {}
            PathElement::LineTo(point) => {
                let a = element_end(&elements[index - 1]);
                let llen = Line { p1: a, p2: point }.length();
                current += llen;
                if index == last || current / total >= t {
                    let delta = Point::new(point.x - a.x, point.y - a.y);
                    let bezier = Bezier::new(
                        a,
                        Point::new(a.x + delta.x / 3.0, a.y + delta.y / 3.0),
                        Point::new(a.x + 2.0 * delta.x / 3.0, a.y + 2.0 * delta.y / 3.0),
                        point,
                    );
                    found = Some((bezier, llen));
                    break;
                }
            }
            PathElement::CubicTo(c1, c2, end) => {
                let bezier = Bezier::new(element_end(&elements[index - 1]), c1, c2, end);
                let blen = bezier.length();
                current += blen;
                if index == last || current / total >= t {
                    found = Some((bezier, blen));
                    break;
                }
            }
        }
        starting = current;
    }
    let Some((bezier, length)) = found else {
        return Point::new(0.0, 0.0);
    };
    let real_t = (total * t - starting) / length;
    bezier.point_at(real_t.clamp(0.0, 1.0))
}

/// 入りまたは抜きの設定（線の太さは直径、`curve` は効き方の指数）。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Taper {
    /// 端の線の太さ。
    pub width: f64,
    /// 太さの変わり方の指数。
    pub curve: f64,
}

/// 直線ツールの線の設定。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct LineOptions {
    /// 線の太さ（`max(0.5, pen_size)`）。
    pub width: f64,
    /// 入り。
    pub taper_in: Option<Taper>,
    /// 抜き。
    pub taper_out: Option<Taper>,
}

impl LineOptions {
    /// 画面の値から作る（`commit_line_tool`）。`taper_in`・`taper_out` はパネルの
    /// (大きさ, カーブ %)。Shift の直線（`_brush_shift_line`）は入り抜きを付けない。
    pub fn from_panel(
        pen_size: f64,
        shift_line: bool,
        taper_in: Option<(f64, f64)>,
        taper_out: Option<(f64, f64)>,
    ) -> Self {
        let mut options = Self {
            width: pen_size.max(0.5),
            taper_in: None,
            taper_out: None,
        };
        if !shift_line {
            options.taper_in = taper_in.map(|(size, curve)| Taper {
                width: size / 2.0,
                curve: curve / 100.0,
            });
            options.taper_out = taper_out.map(|(size, curve)| Taper {
                width: size / 2.0,
                curve: 1.0 / (curve / 100.0).max(0.05),
            });
        }
        options
    }
}

/// 確定前の描画先（`QImage::Format_ARGB32_Premultiplied` の透明な画像）。
///
/// Python は層と同じ大きさの透明な画像へ非 AA の QPainter で描き、2 値化してから
/// 層へ書く。細いペンの薄い色は SourceOver で重なるので、同じ計算で重ねる。
#[derive(Clone, Debug)]
pub struct Overlay {
    width: usize,
    height: usize,
    /// プレマルチプライの (R, G, B, A)。
    pixels: Vec<[u8; 4]>,
}

/// `qt_div_255` を使う `BYTE_MUL` の 1 成分。
fn byte_mul(value: u8, alpha: u8) -> u8 {
    let product = u32::from(value) * u32::from(alpha);
    ((product + (product >> 8) + 0x80) >> 8) as u8
}

impl Overlay {
    /// 透明な描画先を作る。
    pub fn new(width: usize, height: usize) -> Self {
        Self {
            width,
            height,
            pixels: vec![[0; 4]; width * height],
        }
    }

    /// SourceOver で色 `rgb` を不透明度 `coverage` で置く。
    fn put(&mut self, x: i64, y: i64, rgb: [u8; 3], coverage: u8) {
        if x < 0 || y < 0 || x >= self.width as i64 || y >= self.height as i64 {
            return;
        }
        let pixel = &mut self.pixels[y as usize * self.width + x as usize];
        if coverage == 255 {
            *pixel = [rgb[0], rgb[1], rgb[2], 255];
            return;
        }
        let source = [
            byte_mul(rgb[0], coverage),
            byte_mul(rgb[1], coverage),
            byte_mul(rgb[2], coverage),
            coverage,
        ];
        for channel in 0..4 {
            let kept = byte_mul(pixel[channel], 255 - coverage);
            pixel[channel] = source[channel].saturating_add(kept);
        }
    }

    fn put_mask(&mut self, mask: &Mask, rgb: [u8; 3], coverage: u8) {
        for row in 0..mask.height {
            for column in 0..mask.width {
                if mask.get_local(column, row) {
                    self.put(mask.x + column as i64, mask.y + row as i64, rgb, coverage);
                }
            }
        }
    }

    /// 幅 `pen` の丸い端・丸い角のペンで経路を描く（ブラシなしの `drawPath`）。
    fn stroke(&mut self, path: &Path, pen: f64, rgb: [u8; 3]) {
        if pen > 1.0 {
            let mask = stroke_path(path, pen, self.width, self.height);
            self.put_mask(&mask, rgb, 255);
            return;
        }
        let coverage = thin_pen_coverage(pen);
        let mut hits = Vec::new();
        cosmetic_path_pixels(path, self.width, self.height, |x, y| hits.push((x, y)));
        for (x, y) in hits {
            self.put(x, y, rgb, coverage);
        }
    }

    /// 2 値化（`_binary_paint_overlay_rgba`、不透明度 128 以上）で残る画素。
    pub fn mask(&self) -> Mask {
        let bits = self.pixels.iter().map(|pixel| pixel[3] >= 128).collect();
        Mask::from_bits(0, 0, self.width, self.height, bits)
            .unwrap_or_else(|| Mask::new(0, 0, self.width, self.height))
    }

    /// 2 値化して正規の色へ寄せた画素（`palette` は Python の `exact_colors` の順）。
    /// 残らない画素は None。白は消しゴム（確定で透明にする画素）。
    fn binary_color(&self, index: usize, palette: &[[u8; 3]]) -> Option<[u8; 3]> {
        let [r, g, b, a] = self.pixels[index];
        if a < 128 {
            return None;
        }
        // RGBA8888 への変換（qUnpremultiply）。
        let unpremultiply = |value: u8| -> u8 {
            if a == 255 {
                value
            } else {
                ((u32::from(value) * 255 + u32::from(a) / 2) / u32::from(a)).min(255) as u8
            }
        };
        Some(snap_to_palette(
            [unpremultiply(r), unpremultiply(g), unpremultiply(b)],
            palette,
        ))
    }

    /// 確定前の見た目（#117）：確定で書く色の RGBA（行優先）。書かない画素は透明、
    /// 消しゴムの画素は白（α255）。
    pub fn preview_rgba(&self, palette: &[[u8; 3]]) -> Vec<u8> {
        let mut out = vec![0; self.pixels.len() * 4];
        for index in 0..self.pixels.len() {
            if let Some(rgb) = self.binary_color(index, palette) {
                out[index * 4..index * 4 + 4].copy_from_slice(&[rgb[0], rgb[1], rgb[2], 255]);
            }
        }
        out
    }

    /// 確定前の見た目（#117）を、[`paint_overlay`] が書く画素だけの矩形で返す。
    /// `selection` も確定と同じに効かせる。書く画素が無ければ None。
    ///
    /// 戻り値は (矩形, その矩形の RGBA)。書かない画素は透明、消しゴムは白（α255）。
    pub fn preview_within(
        &self,
        palette: &[[u8; 3]],
        selection: Option<&Selection>,
    ) -> Option<(Rect, Vec<u8>)> {
        let full = Rect {
            x: 0,
            y: 0,
            width: self.width,
            height: self.height,
        };
        let clip = selection.and_then(|selection| selection.mask_for(full));
        let mut colors: Vec<Option<[u8; 3]>> = vec![None; self.pixels.len()];
        let (mut left, mut top, mut right, mut bottom) = (usize::MAX, usize::MAX, 0, 0);
        for y in 0..self.height {
            for x in 0..self.width {
                if self.pixels[y * self.width + x][3] < 128
                    || clip
                        .as_ref()
                        .is_some_and(|clip| !clip.get(x as i64, y as i64))
                {
                    continue;
                }
                colors[y * self.width + x] = self.binary_color(y * self.width + x, palette);
                left = left.min(x);
                top = top.min(y);
                right = right.max(x + 1);
                bottom = bottom.max(y + 1);
            }
        }
        if left == usize::MAX {
            return None;
        }
        let rect = Rect {
            x: left,
            y: top,
            width: right - left,
            height: bottom - top,
        };
        let mut out = vec![0; rect.width * rect.height * 4];
        for row in 0..rect.height {
            for column in 0..rect.width {
                if let Some(rgb) = colors[(rect.y + row) * self.width + rect.x + column] {
                    let at = (row * rect.width + column) * 4;
                    out[at..at + 4].copy_from_slice(&[rgb[0], rgb[1], rgb[2], 255]);
                }
            }
        }
        Some((rect, out))
    }
}

/// 直線ツールの線を描いた描画先（`commit_line_tool`）。
pub fn line_overlay(
    path: &Path,
    options: &LineOptions,
    rgb: [u8; 3],
    width: usize,
    height: usize,
) -> Overlay {
    let mut overlay = Overlay::new(width, height);
    if options.taper_in.is_none() && options.taper_out.is_none() {
        overlay.stroke(path, options.width, rgb);
    } else {
        draw_tapered(&mut overlay, path, options, rgb);
    }
    overlay
}

/// 直線ツールの線の画素（`width × height` の層の座標）。確定と同じ画素。
pub fn line_mask(path: &Path, options: &LineOptions, width: usize, height: usize) -> Mask {
    line_overlay(path, options, [0, 0, 0], width, height).mask()
}

/// `_draw_tapered_path`。経路の上の点を細かく取り、太さを変えながら線分でつなぐ。
fn draw_tapered(overlay: &mut Overlay, path: &Path, options: &LineOptions, rgb: [u8; 3]) {
    let (width, height) = (overlay.width, overlay.height);
    let length = path_length(path).max(1.0);
    let steps = ((length * 1.5).ceil() as i64).clamp(2, 4096);
    let base = options.width;
    let mut previous = point_at_percent(path, 0.0);
    for index in 1..=steps {
        let ratio = index as f64 / steps as f64;
        let point = point_at_percent(path, ratio);
        let mut pen = base;
        if let (Some(taper), true) = (options.taper_in, ratio <= 0.5) {
            let local = (ratio * 2.0).clamp(0.0, 1.0);
            let eased = local.powf(taper.curve.max(0.05));
            pen = taper.width + (base - taper.width) * eased;
        } else if let (Some(taper), true) = (options.taper_out, ratio >= 0.5) {
            let local = ((ratio - 0.5) * 2.0).clamp(0.0, 1.0);
            let eased = local.powf(taper.curve.max(0.05));
            pen = base + (taper.width - base) * eased;
        }
        let pen = pen.max(0.5);
        if pen > 1.0 {
            let mask = segment_mask(previous, point, pen, width, height);
            overlay.put_mask(&mask, rgb, 255);
        } else {
            let mask = cosmetic_line(previous, point, width, height);
            overlay.put_mask(&mask, rgb, thin_pen_coverage(pen));
        }
        previous = point;
    }
}

/// 図形の種類。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ShapeKind {
    /// 楕円。
    Ellipse,
    /// 角の数（3 以上、4 はドラッグ範囲の長方形）。
    Polygon(usize),
}

/// ドラッグの 2 点からの図形の範囲（`_shape_rect`）。(x, y, 幅, 高さ)。
pub fn shape_rect(start: Point, end: Point, lock_ratio: bool) -> (f64, f64, f64, f64) {
    let mut end = end;
    if lock_ratio {
        let (dx, dy) = (end.x - start.x, end.y - start.y);
        let size = dx.abs().max(dy.abs());
        end = Point::new(
            start.x + if dx >= 0.0 { size } else { -size },
            start.y + if dy >= 0.0 { size } else { -size },
        );
    }
    // QRectF(start, end).normalized()
    let (mut x, mut y, mut w, mut h) = (start.x, start.y, end.x - start.x, end.y - start.y);
    if w < 0.0 {
        x += w;
        w = -w;
    }
    if h < 0.0 {
        y += h;
        h = -h;
    }
    (x, y, w, h)
}

/// 図形の経路（`_shape_path`）。範囲が空なら None。
pub fn shape_path(rect: (f64, f64, f64, f64), kind: ShapeKind) -> Option<Path> {
    let (x, y, w, h) = rect;
    if w <= 0.0 || h <= 0.0 {
        return None;
    }
    let corners = match kind {
        ShapeKind::Ellipse => return Some(ellipse_path(rect)),
        ShapeKind::Polygon(corners) => corners.max(3),
    };
    let points: Vec<Point> = if corners == 4 {
        vec![
            Point::new(x, y),
            Point::new(x + w, y),
            Point::new(x + w, y + h),
            Point::new(x, y + h),
        ]
    } else {
        // QRectF::center と半径。
        let (cx, cy) = ((x + x + w) / 2.0, (y + y + h) / 2.0);
        let (rx, ry) = (w / 2.0, h / 2.0);
        (0..corners)
            .map(|index| {
                let angle = -std::f64::consts::PI / 2.0
                    + (2.0 * std::f64::consts::PI * index as f64 / corners as f64);
                Point::new(cx + angle.cos() * rx, cy + angle.sin() * ry)
            })
            .collect()
    };
    let mut path = Path::new();
    path.move_to(points[0]);
    for &point in &points[1..] {
        path.line_to(point);
    }
    path.close();
    Some(path)
}

/// 図形ツールを描いた描画先（`commit_shape_tool`）。`outline_width` はパネルの値の
/// 半分（`shape_outline_width.value() / 2`）。塗りを先に、輪郭をその上に描く。
pub fn shape_overlay(
    path: &Path,
    outline_width: f64,
    fill: Option<[u8; 3]>,
    outline: [u8; 3],
    width: usize,
    height: usize,
) -> Overlay {
    let mut overlay = Overlay::new(width, height);
    if let Some(rgb) = fill {
        let mask = fill_path(path, FillRule::OddEven, width, height);
        overlay.put_mask(&mask, rgb, 255);
    }
    overlay.stroke(path, outline_width.max(0.5), outline);
    overlay
}

/// 図形ツールの `exact_colors`（輪郭、塗り）の重複を除いた並び。
pub fn shape_palette(fill: Option<[u8; 3]>, outline: [u8; 3]) -> Vec<[u8; 3]> {
    let mut palette = vec![outline];
    if let Some(rgb) = fill.filter(|rgb| *rgb != outline) {
        palette.push(rgb);
    }
    palette
}

/// `_snap_overlay_to_exact_rgbs`：描いた色をいちばん近い正規の色へ寄せる。
///
/// Python は距離を int16 で掛け算するので、遠い色どうしでは桁があふれて負になる。
/// そのため色を分けた図形で、白（消しゴム）の輪郭が塗りの暗い色に変わることがある。
/// 画素を Python と揃えるため、同じ計算（`color_stats::snap_distance`）をする。
fn snap_to_palette(rgb: [u8; 3], palette: &[[u8; 3]]) -> [u8; 3] {
    let Some((&first, rest)) = palette.split_first() else {
        return rgb;
    };
    let mut nearest = first;
    let mut best = snap_distance(rgb, first);
    for &color in rest {
        let distance = snap_distance(rgb, color);
        if distance < best {
            best = distance;
            nearest = color;
        }
    }
    nearest
}

/// 直線ツール・図形ツールで描いた結果。
#[derive(Clone, Debug)]
pub struct ToolResult {
    /// 描いた層の画像。
    pub image: RgbaImage,
    /// Undo の差分（描いた画素を囲む矩形）。何も描かなければ None。
    pub undo: Option<UndoPatch>,
    /// 塗った色（白は含めない）。使用色の通知に使う。
    pub colors: Vec<[u8; 3]>,
}

/// 描画先を層へ書く（`_blend_overlay_into_active_layer`、不透明度 100%）。
///
/// `palette` は Python の `exact_colors`（直線は線の色、図形は輪郭・塗りの色）。
/// 白（`ERASER_RGB`）は消しゴムとして透明にする。`selection` があれば、その中だけに描く。
pub fn paint_overlay(
    layer: &RgbaImage,
    overlay: &Overlay,
    palette: &[[u8; 3]],
    selection: Option<&Selection>,
) -> Result<ToolResult, Error> {
    let (width, height) = (layer.width(), layer.height());
    if overlay.width != width || overlay.height != height {
        return Err(Error::Invalid("overlay size must match the layer".into()));
    }
    let full = Rect {
        x: 0,
        y: 0,
        width,
        height,
    };
    let clip = selection.and_then(|selection| selection.mask_for(full));
    let mut color_at: Vec<Option<[u8; 3]>> = vec![None; width * height];
    let (mut left, mut top, mut right, mut bottom) = (usize::MAX, usize::MAX, 0, 0);
    for y in 0..height {
        for x in 0..width {
            if clip
                .as_ref()
                .is_some_and(|clip| !clip.get(x as i64, y as i64))
            {
                continue;
            }
            let Some(rgb) = overlay.binary_color(y * width + x, palette) else {
                continue;
            };
            color_at[y * width + x] = Some(rgb);
            left = left.min(x);
            top = top.min(y);
            right = right.max(x + 1);
            bottom = bottom.max(y + 1);
        }
    }
    let mut image = layer.clone();
    if left == usize::MAX {
        return Ok(ToolResult {
            image,
            undo: None,
            colors: Vec::new(),
        });
    }
    let rect = Rect {
        x: left,
        y: top,
        width: right - left,
        height: bottom - top,
    };
    let before = layer.copy_rgba_rect(rect)?;
    let mut after = before.clone();
    for row in 0..rect.height {
        for column in 0..rect.width {
            let Some(rgb) = color_at[(rect.y + row) * width + rect.x + column] else {
                continue;
            };
            let value = if rgb == ERASER_RGB {
                [0, 0, 0, 0]
            } else {
                [rgb[0], rgb[1], rgb[2], 255]
            };
            let at = (row * rect.width + column) * 4;
            after[at..at + 4].copy_from_slice(&value);
        }
    }
    image.write_rgba(rect, &after)?;
    // Python は描いた正規の色をそのまま通知する（白は除く）。
    let mut colors: Vec<[u8; 3]> = Vec::new();
    for rgb in palette {
        if *rgb != ERASER_RGB && !colors.contains(rgb) {
            colors.push(*rgb);
        }
    }
    Ok(ToolResult {
        image,
        undo: Some(UndoPatch {
            rect,
            before,
            after,
        }),
        colors,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn straight_line_length_and_midpoint() {
        let path = line_path(Point::new(0.0, 0.0), Point::new(10.0, 0.0), None);
        assert_eq!(path_length(&path), 10.0);
        let mid = point_at_percent(&path, 0.5);
        assert!((mid.x - 5.0).abs() < 1e-12 && mid.y.abs() < 1e-12);
    }

    #[test]
    fn half_pixel_pen_draws_nothing() {
        let path = line_path(Point::new(1.0, 1.0), Point::new(9.0, 4.0), None);
        let thin = LineOptions::from_panel(0.5, false, None, None);
        assert!(line_mask(&path, &thin, 12, 8).is_empty());
        let one = LineOptions::from_panel(1.0, false, None, None);
        assert!(!line_mask(&path, &one, 12, 8).is_empty());
    }

    #[test]
    fn white_erases_and_outline_covers_fill() {
        let mut layer = RgbaImage::blank(10, 10).unwrap();
        let one = Rect {
            x: 0,
            y: 0,
            width: 1,
            height: 1,
        };
        layer.write_rgba(one, &[9, 9, 9, 255]).unwrap();
        let path = shape_path((0.5, 0.5, 6.0, 6.0), ShapeKind::Polygon(4)).unwrap();
        let (fill, outline) = (Some([250, 240, 230]), ERASER_RGB);
        let overlay = shape_overlay(&path, 1.0, fill, outline, 10, 10);
        let palette = shape_palette(fill, outline);
        let result = paint_overlay(&layer, &overlay, &palette, None).unwrap();
        let pixel = |x: usize, y: usize| {
            let at = (y * 10 + x) * 4;
            result.image.rgba()[at..at + 4].to_vec()
        };
        assert_eq!(pixel(3, 3), vec![250, 240, 230, 255]);
        assert_eq!(pixel(0, 0), vec![0, 0, 0, 0]);
        assert_eq!(result.colors, vec![[250, 240, 230]]);
    }

    #[test]
    fn preview_within_matches_committed_pixels_under_a_selection() {
        let layer = RgbaImage::blank(24, 16).unwrap();
        let path = line_path(
            Point::new(2.0, 3.0),
            Point::new(21.0, 12.0),
            Some(Point::new(4.0, 15.0)),
        );
        let options = LineOptions::from_panel(3.0, false, Some((1.0, 100.0)), None);
        let overlay = line_overlay(&path, &options, [10, 20, 30], 24, 16);
        let selection = Selection::Polygon(vec![
            Point::new(0.0, 0.0),
            Point::new(14.0, 0.0),
            Point::new(14.0, 16.0),
            Point::new(0.0, 16.0),
        ]);
        let palette = [[10, 20, 30]];
        let (rect, preview) = overlay.preview_within(&palette, Some(&selection)).unwrap();
        let result = paint_overlay(&layer, &overlay, &palette, Some(&selection)).unwrap();
        let undo = result.undo.unwrap();
        assert_eq!(rect, undo.rect);
        assert_eq!(preview, undo.after);
        assert!(rect.x + rect.width <= 14);
        // 何も残らなければ None。
        let thin = line_overlay(
            &path,
            &LineOptions::from_panel(0.5, true, None, None),
            [0; 3],
            24,
            16,
        );
        assert!(thin.preview_within(&palette, None).is_none());
    }

    #[test]
    fn thin_pens_add_up() {
        let mut overlay = Overlay::new(2, 1);
        overlay.put(0, 0, [10, 20, 30], 127);
        assert!(overlay.mask().is_empty());
        overlay.put(0, 0, [10, 20, 30], 127);
        assert!(overlay.mask().get(0, 0));
    }

    #[test]
    fn far_colors_snap_like_python() {
        // int16 のあふれで、白と (1, 2, 3) の距離は負になる。
        let palette = [[255, 255, 255], [1, 2, 3]];
        assert_eq!(snap_to_palette([255, 255, 255], &palette), [1, 2, 3]);
        assert_eq!(
            snap_to_palette([9, 9, 9], &[[9, 9, 9], [10, 10, 10]]),
            [9, 9, 9]
        );
    }
}
