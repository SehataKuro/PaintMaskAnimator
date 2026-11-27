//! 変形した画像を描く（通常の変形・メッシュ・クオリティ変形）。
//!
//! Python の `_project_transform_source`・`_mesh_preview_image`・`_tp_mask_preview_image`
//! に対応する。どれも「透明なレイヤーの大きさの画像に、変形した元の画像を描いたもの」を
//! 作る。ここでは画素のある範囲（`window`）だけを持つ乗算済みの画像として返す。

use std::collections::BTreeSet;

use super::geometry::{curved_mesh_points, homography, invert3, outer_polygon, quad_transform, Pt};
use crate::compose::painter::{draw_image_at, Device};
use crate::compose::{premultiply, unpremultiply, PremulImage};
use crate::image::Rect;
use crate::mask_transform::{
    labels_to_rgba, render_mesh, render_quad, splat_labels, ForwardMap, Homography, LabelImage,
    Mesh,
};
use crate::Error;

/// RGB の色。
pub type Rgb = [u8; 3];

/// クオリティ変形で、1 つの色を 1 つの番号にする上限（`TP_MASK_MAX_TRANSFORM_COLORS`）。
pub const MAX_TRANSFORM_COLORS: usize = 4096;
/// 被覆を数える 1 画素あたりの縦横の分割数（`mask_transform.DEFAULT_SUBSAMPLES`）。
pub const SUBSAMPLES: usize = 4;
/// メッシュの逆写像で一度に解く行数（`mask_transform.DEFAULT_BAND_ROWS`）。
pub const BAND_ROWS: usize = 64;

/// 変形の形（描くのに要る値だけ）。
#[derive(Clone, Debug, PartialEq)]
pub struct Shape<'a> {
    /// メッシュ変形か（偽なら四角形の 4 点）。
    pub mesh: bool,
    /// 変形の点。
    pub points: &'a [Pt],
    /// メッシュの基準格子。
    pub reference: &'a [Pt],
    /// メッシュの横の点の数。
    pub cols: usize,
    /// メッシュの縦の点の数。
    pub rows: usize,
}

/// 描いた結果。`window` はレイヤーの座標での範囲、`pixels` はその範囲の乗算済みの画素。
#[derive(Clone, Debug)]
pub struct Rendered {
    /// 範囲。
    pub window: Rect,
    /// 範囲の画素。
    pub pixels: PremulImage,
}

fn clip_window(left: f64, top: f64, right: f64, bottom: f64, size: (usize, usize)) -> Option<Rect> {
    if !(left.is_finite() && top.is_finite() && right.is_finite() && bottom.is_finite()) {
        return Some(Rect {
            x: 0,
            y: 0,
            width: size.0,
            height: size.1,
        });
    }
    let x0 = (left.floor() as i64 - 2).clamp(0, size.0 as i64);
    let y0 = (top.floor() as i64 - 2).clamp(0, size.1 as i64);
    let x1 = (right.ceil() as i64 + 2).clamp(0, size.0 as i64);
    let y1 = (bottom.ceil() as i64 + 2).clamp(0, size.1 as i64);
    (x1 > x0 && y1 > y0).then_some(Rect {
        x: x0 as usize,
        y: y0 as usize,
        width: (x1 - x0) as usize,
        height: (y1 - y0) as usize,
    })
}

fn bounds_of(points: &[Pt]) -> (f64, f64, f64, f64) {
    let mut b = (
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    );
    for p in points {
        b = (b.0.min(p.0), b.1.min(p.1), b.2.max(p.0), b.3.max(p.1));
    }
    b
}

fn empty() -> Result<Rendered, Error> {
    Ok(Rendered {
        window: Rect {
            x: 0,
            y: 0,
            width: 0,
            height: 0,
        },
        pixels: PremulImage::transparent(0, 0)?,
    })
}

/// 通常の変形（`_project_transform_source`・`_mesh_preview_image`）。
pub fn render_plain(
    source: &PremulImage,
    shape: &Shape,
    size: (usize, usize),
) -> Result<Rendered, Error> {
    if shape.mesh {
        return render_mesh_plain(source, shape, size);
    }
    if shape.points.len() != 4 || source.width() == 0 || source.height() == 0 {
        return empty();
    }
    let b = bounds_of(shape.points);
    let Some(window) = clip_window(b.0, b.1, b.2, b.3, size) else {
        return empty();
    };
    let matrix = quad_transform(source.width() as f64, source.height() as f64, shape.points);
    let mut pixels = PremulImage::transparent(window.width, window.height)?;
    let mut device = Device::window(
        &mut pixels,
        window.x as i64,
        window.y as i64,
        size.0,
        size.1,
    );
    draw_image_at(&mut device, &matrix, 0.0, 0.0, source, 1.0);
    Ok(Rendered { window, pixels })
}

/// `_mesh_preview_image`。三角形ごとに重心座標で元の画素を最近傍で拾う。
fn render_mesh_plain(
    source: &PremulImage,
    shape: &Shape,
    size: (usize, usize),
) -> Result<Rendered, Error> {
    let (sw, sh) = (source.width(), source.height());
    let (cw, ch) = size;
    if sw == 0 || sh == 0 || cw == 0 || ch == 0 || shape.points.len() != shape.cols * shape.rows {
        return empty();
    }
    let (curved, dense_cols, dense_rows) =
        curved_mesh_points(shape.points, shape.reference, shape.cols, shape.rows, 8);
    let b = bounds_of(&curved);
    let Some(window) = clip_window(b.0, b.1, b.2, b.3, size) else {
        return empty();
    };
    // Qt は RGBA8888 へ変えて（ストレートにして）拾い、乗算済みへ戻す。
    let straight: Vec<[u8; 4]> = source
        .pixels()
        .chunks_exact(4)
        .map(|p| unpremultiply([p[0], p[1], p[2], p[3]]))
        .collect();
    let mut output = vec![[0u8; 4]; window.width * window.height];
    let mut raster = |t: [Pt; 3], s: [Pt; 3]| {
        let tx = [t[0].0, t[1].0, t[2].0];
        let ty = [t[0].1, t[1].1, t[2].1];
        let min_x = 0.max(tx.iter().copied().fold(f64::INFINITY, f64::min).floor() as i64);
        let max_x =
            (cw as i64 - 1).min(tx.iter().copied().fold(f64::NEG_INFINITY, f64::max).ceil() as i64);
        let min_y = 0.max(ty.iter().copied().fold(f64::INFINITY, f64::min).floor() as i64);
        let max_y =
            (ch as i64 - 1).min(ty.iter().copied().fold(f64::NEG_INFINITY, f64::max).ceil() as i64);
        if max_x < min_x || max_y < min_y {
            return;
        }
        let den = (ty[1] - ty[2]) * (tx[0] - tx[2]) + (tx[2] - tx[1]) * (ty[0] - ty[2]);
        if den.abs() < 1e-8 {
            return;
        }
        for y in min_y.max(window.y as i64)..=max_y.min((window.y + window.height) as i64 - 1) {
            let yy = y as f64;
            for x in min_x.max(window.x as i64)..=max_x.min((window.x + window.width) as i64 - 1) {
                let xx = x as f64;
                let w0 = ((ty[1] - ty[2]) * (xx - tx[2]) + (tx[2] - tx[1]) * (yy - ty[2])) / den;
                let w1 = ((ty[2] - ty[0]) * (xx - tx[2]) + (tx[0] - tx[2]) * (yy - ty[2])) / den;
                let w2 = 1.0 - w0 - w1;
                if !(w0 >= -1e-6 && w1 >= -1e-6 && w2 >= -1e-6) {
                    continue;
                }
                let sx = w0 * s[0].0 + w1 * s[1].0 + w2 * s[2].0;
                let sy = w0 * s[0].1 + w1 * s[1].1 + w2 * s[2].1;
                let px = (sx.round_ties_even() as i32).clamp(0, sw as i32 - 1) as usize;
                let py = (sy.round_ties_even() as i32).clamp(0, sh as i32 - 1) as usize;
                let at = (y as usize - window.y) * window.width + (x as usize - window.x);
                output[at] = straight[py * sw + px];
            }
        }
    };
    let span = |count: usize| (count.max(2) - 1).max(1) as f64;
    for dy in 0..dense_rows - 1 {
        let sy0 = (sh - 1) as f64 * dy as f64 / span(dense_rows);
        let sy1 = (sh - 1) as f64 * (dy + 1) as f64 / span(dense_rows);
        for dx in 0..dense_cols - 1 {
            let sx0 = (sw - 1) as f64 * dx as f64 / span(dense_cols);
            let sx1 = (sw - 1) as f64 * (dx + 1) as f64 / span(dense_cols);
            let i = dy * dense_cols + dx;
            let (p00, p10) = (curved[i], curved[i + 1]);
            let (p01, p11) = (curved[i + dense_cols], curved[i + dense_cols + 1]);
            raster([p00, p10, p11], [(sx0, sy0), (sx1, sy0), (sx1, sy1)]);
            raster([p00, p11, p01], [(sx0, sy0), (sx1, sy1), (sx0, sy1)]);
        }
    }
    let mut pixels = Vec::with_capacity(output.len() * 4);
    for pixel in output {
        pixels.extend_from_slice(&premultiply(pixel));
    }
    Ok(Rendered {
        window,
        pixels: PremulImage::from_premultiplied(window.width, window.height, pixels)?,
    })
}

/// クオリティ変形の元（1 画素 1 番号）。`_prepare_tp_transform_masks` の結果。
#[derive(Clone, Debug)]
pub struct Labels {
    /// 番号（0 は「何もない」）。
    pub labels: Vec<u16>,
    /// 幅。
    pub width: usize,
    /// 高さ。
    pub height: usize,
    /// 番号ごとの RGBA（行 0 は透明）。
    pub colors: Vec<[u8; 4]>,
    /// 線の色と、その番号（指定の順）。
    pub lines: Vec<(Rgb, u16)>,
}

/// 元の画像（乗算済み）から番号の画像を作る（`_prepare_tp_transform_masks`）。
///
/// 色が [`MAX_TRANSFORM_COLORS`] を超えるとき、Python は減色してから変形するが、
/// その減色（Pillow の quantize）はまだ移していないので None を返す。
pub fn prepare_labels(source: &PremulImage, line_colors: &[Rgb]) -> Option<Labels> {
    let (width, height) = (source.width(), source.height());
    if width == 0 || height == 0 {
        return None;
    }
    // 透明は不透明の白にする（v0.7 の規則）。
    let prepared: Vec<[u8; 4]> = source
        .pixels()
        .chunks_exact(4)
        .map(|p| {
            let s = unpremultiply([p[0], p[1], p[2], p[3]]);
            if s[3] == 0 {
                [255, 255, 255, 255]
            } else {
                s
            }
        })
        .collect();
    let pack = |c: [u8; 4]| (u32::from(c[0]) << 16) | (u32::from(c[1]) << 8) | u32::from(c[2]);
    let palette: BTreeSet<u32> = prepared
        .iter()
        .filter(|p| p[3] > 0)
        .map(|&p| pack(p))
        .collect();
    if palette.len() > MAX_TRANSFORM_COLORS {
        return None;
    }
    let mut palette: Vec<Rgb> = palette
        .into_iter()
        .map(|v| [(v >> 16) as u8, (v >> 8) as u8, v as u8])
        .collect();
    let mut lines = Vec::new();
    for &line in line_colors {
        let present = prepared.iter().any(|p| p[3] > 0 && p[..3] == line);
        if present {
            lines.push(line);
            palette.retain(|c| *c != line);
        }
    }
    let luminance = |c: &Rgb| 0.299 * c[0] as f64 + 0.587 * c[1] as f64 + 0.114 * c[2] as f64;
    palette.sort_by(|a, b| luminance(a).total_cmp(&luminance(b)));
    let mut ordered: Vec<Rgb> = Vec::new();
    for color in lines.iter().chain(palette.iter()) {
        if !ordered.contains(color) {
            ordered.push(*color);
        }
    }
    let mut ids = std::collections::HashMap::new();
    let mut colors = vec![[0u8; 4]];
    for (index, c) in ordered.iter().enumerate() {
        ids.insert(*c, index as u16 + 1);
        colors.push([c[0], c[1], c[2], 255]);
    }
    let labels = prepared
        .iter()
        .map(|p| {
            if p[3] == 0 {
                0
            } else {
                ids[&[p[0], p[1], p[2]]]
            }
        })
        .collect();
    let lines = lines.iter().map(|c| (*c, ids[c])).collect();
    Some(Labels {
        labels,
        width,
        height,
        colors,
        lines,
    })
}

/// クオリティ変形で描く範囲（`_tp_transform_bbox`）。外周の外接矩形を 3 画素広げて画像に収める。
pub fn quality_bbox(shape: &Shape, size: (usize, usize)) -> Option<Rect> {
    let outline = outer_polygon(
        shape.mesh,
        shape.points,
        shape.reference,
        shape.cols,
        shape.rows,
    );
    if outline.is_empty() {
        return None;
    }
    let b = bounds_of(&outline);
    let (left, top) = ((b.0 - 3.0).max(0.0), (b.1 - 3.0).max(0.0));
    let (right, bottom) = (
        (b.2 + 3.0).min(size.0 as f64),
        (b.3 + 3.0).min(size.1 as f64),
    );
    if right <= left || bottom <= top {
        return None;
    }
    let x0 = 0.max(left.floor() as i64);
    let y0 = 0.max(top.floor() as i64);
    let x1 = (size.0 as i64).min(right.ceil() as i64);
    let y1 = (size.1 as i64).min(bottom.ceil() as i64);
    (x1 > x0 && y1 > y0).then_some(Rect {
        x: x0 as usize,
        y: y0 as usize,
        width: (x1 - x0) as usize,
        height: (y1 - y0) as usize,
    })
}

/// クオリティ変形（`_tp_mask_preview_image`）。`threshold` は線の太さのしきい値（1〜254）。
/// 変形の形が写像にならなければ None（Python は通常の変形で描く）。
pub fn render_quality(
    labels: &Labels,
    shape: &Shape,
    size: (usize, usize),
    threshold: u8,
) -> Result<Option<Rendered>, Error> {
    let Some(bbox) = quality_bbox(shape, size) else {
        return Ok(Some(empty()?));
    };
    let (left, top) = (bbox.x as f64, bbox.y as f64);
    let (width, height) = (bbox.width, bbox.height);
    let image = LabelImage {
        labels: &labels.labels,
        width: labels.width,
        height: labels.height,
    };
    let line_labels: Vec<u16> = labels.lines.iter().map(|&(_, l)| l).collect();
    let (sw, sh) = (labels.width as f64, labels.height as f64);
    let dense;
    let (mut result, mut coverage, forward) = if shape.mesh {
        if shape.points.len() != shape.cols * shape.rows {
            return Ok(None);
        }
        let (curved, cols, rows) =
            curved_mesh_points(shape.points, shape.reference, shape.cols, shape.rows, 8);
        dense = curved
            .iter()
            .map(|p| (p.0 - left, p.1 - top))
            .collect::<Vec<_>>();
        let mesh = Mesh {
            points: &dense,
            cols,
            rows,
        };
        let (r, c) = render_mesh(
            &image,
            &mesh,
            width,
            height,
            SUBSAMPLES,
            BAND_ROWS,
            &line_labels,
        );
        (r, c, ForwardMap::Mesh(mesh))
    } else {
        if shape.points.len() != 4 {
            return Ok(None);
        }
        let target: [Pt; 4] =
            std::array::from_fn(|i| (shape.points[i].0 - left, shape.points[i].1 - top));
        let source = [(0.0, 0.0), (sw, 0.0), (sw, sh), (0.0, sh)];
        let Some(h) = homography(&target, &source) else {
            return Ok(None);
        };
        let matrix = [h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], 1.0];
        let (r, c) = render_quad(
            &image,
            &Homography(matrix),
            width,
            height,
            SUBSAMPLES,
            &line_labels,
        );
        let Some(inverse) = invert3(&matrix) else {
            return Ok(None);
        };
        (r, c, ForwardMap::Quad(Homography(inverse)))
    };
    // 縮小のときは線を前向きに写して途切れないようにする（`_protect_lines`）。
    if !line_labels.is_empty() && area_scale(&forward, labels.width, labels.height) < 0.999 {
        let hits = splat_labels(&image, &forward, width, height, &line_labels)?;
        for (slot, indices) in hits.iter().enumerate() {
            for &index in indices {
                result[index] = line_labels[slot];
                coverage[slot][index] = 255;
            }
        }
    }
    let palette: Vec<u8> = labels.colors.iter().flatten().copied().collect();
    let mut rgba = labels_to_rgba(
        &LabelImage {
            labels: &result,
            width,
            height,
        },
        &palette,
        true,
    )?;
    for (slot, &(color, _)) in labels.lines.iter().enumerate() {
        for (index, &value) in coverage[slot].iter().enumerate() {
            if value >= threshold {
                rgba[index * 4..index * 4 + 4]
                    .copy_from_slice(&[color[0], color[1], color[2], 255]);
            }
        }
    }
    let pixels = rgba
        .chunks_exact(4)
        .flat_map(|p| premultiply([p[0], p[1], p[2], p[3]]))
        .collect();
    Ok(Some(Rendered {
        window: bbox,
        pixels: PremulImage::from_premultiplied(width, height, pixels)?,
    }))
}

/// 中央での出力と元の長さの比（`_area_scale`）。
fn area_scale(forward: &ForwardMap, width: usize, height: usize) -> f64 {
    let (w, h) = (width as f64, height as f64);
    let (cx, cy) = (w / 2.0, h / 2.0);
    let span = (w.min(h) / 8.0).max(1.0);
    let map = |x: f64, y: f64| -> (f64, f64) {
        match forward {
            ForwardMap::Quad(Homography(m)) => {
                let d = m[6] * x + m[7] * y + m[8];
                if d.abs() > 1e-12 {
                    (
                        (m[0] * x + m[1] * y + m[2]) / d,
                        (m[3] * x + m[4] * y + m[5]) / d,
                    )
                } else {
                    (f64::NAN, f64::NAN)
                }
            }
            ForwardMap::Mesh(mesh) => {
                let axis = |c: f64, size: f64, points: usize| {
                    let last = (size - 1.0).max(1e-9);
                    let span = (points.max(2) - 1) as f64;
                    let grid = (c / last * span).clamp(0.0, span);
                    let base = (grid as usize).min(points - 2);
                    (base, grid - base as f64)
                };
                let (col, fx) = axis(x, w, mesh.cols);
                let (row, fy) = axis(y, h, mesh.rows);
                let i = row * mesh.cols + col;
                let (a, b) = (mesh.points[i], mesh.points[i + 1]);
                let (c, d) = (mesh.points[i + mesh.cols], mesh.points[i + mesh.cols + 1]);
                let lerp = |a: f64, b: f64, c: f64, d: f64| {
                    let top = a * (1.0 - fx) + b * fx;
                    let bottom = c * (1.0 - fx) + d * fx;
                    top * (1.0 - fy) + bottom * fy
                };
                (lerp(a.0, b.0, c.0, d.0), lerp(a.1, b.1, c.1, d.1))
            }
        }
    };
    let p0 = map(cx, cy);
    let p1 = map(cx + span, cy);
    let p2 = map(cx, cy + span);
    if ![p0.0, p1.0, p2.0].iter().all(|v| v.is_finite()) {
        return 1.0;
    }
    let ex = (p1.0 - p0.0, p1.1 - p0.1);
    let ey = (p2.0 - p0.0, p2.1 - p0.1);
    (ex.0 * ey.1 - ex.1 * ey.0).abs().sqrt() / span
}
