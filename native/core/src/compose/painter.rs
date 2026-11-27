//! 変形を掛けた `QPainter::drawImage` を、Qt の raster エンジンと同じ画素で描く。
//!
//! 変形ツールの通常の変形（`_project_transform_source`）と、オニオンのずらし・回転・
//! 拡大（`_draw_onion_range`）は、どちらも QPainter に行列を掛けて乗算済みの画像を
//! ニアレストで描く。Qt は行列の種類と大きさで道を分けるので、ここも同じに分ける
//! （qpaintengine_raster.cpp の `QRasterPaintEngine::drawImage`）。
//!
//! - 平行移動だけ：位置を `qRound` して、そのまま重ねる。
//! - 拡大縮小だけ：qblendfunctions_p.h の `qt_scale_image_32bit`（16.16 の固定小数）。
//! - 回転・せん断で、描く範囲が 16 画素以上：`qt_transform_image`。
//! - それ以外（射影、描く範囲が 16 画素未満の回転・せん断など）：変形した四角形を `QRasterizer` で
//!   塗り、`fetchTransformed` で元の画素を拾う。Qt は回転と等倍の拡大だけのときは
//!   `QRasterizer::rasterizeLine` で塗るが、ここでは同じ四角形の塗りで代える
//!   （16 画素未満の回転だけ、縁の 1 画素が Qt と違いうる）。
//!
//! 元の画像が 1×1 のときの単色の塗りの道は持たない（変形の元とオニオンでは起きない）。

use super::pixel::{blend, const_alpha, Premul, PremulImage};
use crate::brush::raster::{rasterize_polygons, FillRule};
use crate::brush::Point;

/// `qFuzzyIsNull(double)`。
fn fuzzy_is_null(value: f64) -> bool {
    value.abs() <= 0.000_000_000_001
}

/// `qFuzzyCompare(double, double)`。
fn fuzzy_compare(a: f64, b: f64) -> bool {
    (a - b).abs() * 1_000_000_000_000.0 <= a.abs().min(b.abs())
}

/// Qt の qRound（0.5 は 0 から遠い方へ）。
pub(crate) fn q_round(value: f64) -> i64 {
    if value >= 0.0 {
        (value + 0.5) as i64
    } else {
        (value - 0.5) as i64
    }
}

/// Qt の `QTransform::TransformationType`。
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum TxType {
    /// 恒等。
    None,
    /// 平行移動。
    Translate,
    /// 拡大縮小。
    Scale,
    /// 回転。
    Rotate,
    /// せん断。
    Shear,
    /// 射影。
    Project,
}

/// Qt の `QTransform`。行列の並び・種類の覚え方（`m_type`・`m_dirty`）も同じにする。
///
/// `m[0] = [m11, m12, m13]`、`m[1] = [m21, m22, m23]`、`m[2] = [dx, dy, m33]`。
/// 点 (x, y) は `(m11 x + m21 y + dx, m12 x + m22 y + dy)` に写る。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Transform {
    m: [[f64; 3]; 3],
    kind: TxType,
    dirty: TxType,
}

impl Default for Transform {
    fn default() -> Self {
        Self::identity()
    }
}

impl Transform {
    /// 恒等の行列（`QTransform()`）。
    pub fn identity() -> Self {
        Self {
            m: [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            kind: TxType::None,
            dirty: TxType::None,
        }
    }

    /// 9 つの値の行列（`QTransform(m11, m12, m13, m21, m22, m23, m31, m32, m33)`）。
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        m11: f64,
        m12: f64,
        m13: f64,
        m21: f64,
        m22: f64,
        m23: f64,
        m31: f64,
        m32: f64,
        m33: f64,
    ) -> Self {
        Self {
            m: [[m11, m12, m13], [m21, m22, m23], [m31, m32, m33]],
            kind: TxType::None,
            dirty: TxType::Project,
        }
    }

    /// アフィンの 6 つの値の行列（`QTransform(m11, m12, m21, m22, dx, dy)`）。
    pub fn affine(m11: f64, m12: f64, m21: f64, m22: f64, dx: f64, dy: f64) -> Self {
        let t = Self::new(m11, m12, 0.0, m21, m22, 0.0, dx, dy, 1.0);
        let mut t = t;
        t.dirty = TxType::Shear;
        t
    }

    /// 行列の値（行優先 3×3）。
    pub fn values(&self) -> [[f64; 3]; 3] {
        self.m
    }

    /// `QTransform::type()`。
    pub fn kind(&self) -> TxType {
        let dirty = self.dirty;
        if dirty == TxType::None || dirty < self.kind {
            return self.kind;
        }
        let m = &self.m;
        let mut level = dirty;
        loop {
            match level {
                TxType::Project => {
                    if !fuzzy_is_null(m[0][2])
                        || !fuzzy_is_null(m[1][2])
                        || !fuzzy_is_null(m[2][2] - 1.0)
                    {
                        break TxType::Project;
                    }
                    level = TxType::Shear;
                }
                TxType::Shear | TxType::Rotate => {
                    if !fuzzy_is_null(m[0][1]) || !fuzzy_is_null(m[1][0]) {
                        let dot = m[0][0] * m[1][0] + m[0][1] * m[1][1];
                        break if fuzzy_is_null(dot) {
                            TxType::Rotate
                        } else {
                            TxType::Shear
                        };
                    }
                    level = TxType::Scale;
                }
                TxType::Scale => {
                    if !fuzzy_is_null(m[0][0] - 1.0) || !fuzzy_is_null(m[1][1] - 1.0) {
                        break TxType::Scale;
                    }
                    level = TxType::Translate;
                }
                TxType::Translate => {
                    if !fuzzy_is_null(m[2][0]) || !fuzzy_is_null(m[2][1]) {
                        break TxType::Translate;
                    }
                    level = TxType::None;
                }
                TxType::None => break TxType::None,
            }
        }
    }

    /// `inline_type()`：種類を求めて覚える（Qt の変更の関数は最初にこれを呼ぶ）。
    fn settle(&mut self) -> TxType {
        let kind = self.kind();
        self.kind = kind;
        self.dirty = TxType::None;
        kind
    }

    fn mark(&mut self, level: TxType) {
        if self.dirty < level {
            self.dirty = level;
        }
    }

    /// `QTransform::translate`。
    pub fn translate(&mut self, dx: f64, dy: f64) -> &mut Self {
        if dx == 0.0 && dy == 0.0 {
            return self;
        }
        let kind = self.settle();
        let m = &mut self.m;
        match kind {
            TxType::None => {
                m[2][0] = dx;
                m[2][1] = dy;
            }
            TxType::Translate => {
                m[2][0] += dx;
                m[2][1] += dy;
            }
            TxType::Scale => {
                m[2][0] += dx * m[0][0];
                m[2][1] += dy * m[1][1];
            }
            kind => {
                if kind == TxType::Project {
                    m[2][2] += dx * m[0][2] + dy * m[1][2];
                }
                m[2][0] += dx * m[0][0] + dy * m[1][0];
                m[2][1] += dy * m[1][1] + dx * m[0][1];
            }
        }
        self.mark(TxType::Translate);
        self
    }

    /// `QTransform::scale`。
    pub fn scale(&mut self, sx: f64, sy: f64) -> &mut Self {
        if sx == 1.0 && sy == 1.0 {
            return self;
        }
        let kind = self.settle();
        let m = &mut self.m;
        match kind {
            TxType::None | TxType::Translate => {
                m[0][0] = sx;
                m[1][1] = sy;
            }
            _ => {
                if kind == TxType::Project {
                    m[0][2] *= sx;
                    m[1][2] *= sy;
                }
                if kind >= TxType::Rotate {
                    m[0][1] *= sx;
                    m[1][0] *= sy;
                }
                m[0][0] *= sx;
                m[1][1] *= sy;
            }
        }
        self.mark(TxType::Scale);
        self
    }

    /// `QTransform::rotate`（Z 軸、度）。
    pub fn rotate(&mut self, a: f64) -> &mut Self {
        if a == 0.0 {
            return self;
        }
        let (sina, cosa) = if a == 90.0 || a == -270.0 {
            (1.0, 0.0)
        } else if a == 270.0 || a == -90.0 {
            (-1.0, 0.0)
        } else if a == 180.0 {
            (0.0, -1.0)
        } else {
            let b = a * (std::f64::consts::PI / 180.0);
            (b.sin(), b.cos())
        };
        let kind = self.settle();
        let m = &mut self.m;
        match kind {
            TxType::None | TxType::Translate => {
                m[0][0] = cosa;
                m[0][1] = sina;
                m[1][0] = -sina;
                m[1][1] = cosa;
            }
            TxType::Scale => {
                let tm11 = cosa * m[0][0];
                let tm12 = sina * m[1][1];
                let tm21 = -sina * m[0][0];
                let tm22 = cosa * m[1][1];
                m[0][0] = tm11;
                m[0][1] = tm12;
                m[1][0] = tm21;
                m[1][1] = tm22;
            }
            _ => {
                if kind == TxType::Project {
                    let tm13 = cosa * m[0][2] + sina * m[1][2];
                    let tm23 = -sina * m[0][2] + cosa * m[1][2];
                    m[0][2] = tm13;
                    m[1][2] = tm23;
                }
                let tm11 = cosa * m[0][0] + sina * m[1][0];
                let tm12 = cosa * m[0][1] + sina * m[1][1];
                let tm21 = -sina * m[0][0] + cosa * m[1][0];
                let tm22 = -sina * m[0][1] + cosa * m[1][1];
                m[0][0] = tm11;
                m[0][1] = tm12;
                m[1][0] = tm21;
                m[1][1] = tm22;
            }
        }
        self.mark(TxType::Rotate);
        self
    }

    /// `self * other`（`QTransform::operator*`：先に self、次に other を掛ける）。
    pub fn then(&self, other: &Transform) -> Transform {
        let other_kind = other.kind();
        if other_kind == TxType::None {
            return *self;
        }
        let this_kind = self.kind();
        if this_kind == TxType::None {
            return *other;
        }
        let t = this_kind.max(other_kind);
        let a = &self.m;
        let o = &other.m;
        let mut r = self.m;
        match t {
            TxType::None => {}
            TxType::Translate => {
                r[2][0] += o[2][0];
                r[2][1] += o[2][1];
            }
            TxType::Scale => {
                r[0][0] = a[0][0] * o[0][0];
                r[1][1] = a[1][1] * o[1][1];
                r[2][0] = a[2][0] * o[0][0] + o[2][0];
                r[2][1] = a[2][1] * o[1][1] + o[2][1];
            }
            TxType::Rotate | TxType::Shear => {
                r[0][0] = a[0][0] * o[0][0] + a[0][1] * o[1][0];
                r[0][1] = a[0][0] * o[0][1] + a[0][1] * o[1][1];
                r[1][0] = a[1][0] * o[0][0] + a[1][1] * o[1][0];
                r[1][1] = a[1][0] * o[0][1] + a[1][1] * o[1][1];
                r[2][0] = a[2][0] * o[0][0] + a[2][1] * o[1][0] + o[2][0];
                r[2][1] = a[2][0] * o[0][1] + a[2][1] * o[1][1] + o[2][1];
            }
            TxType::Project => {
                for (row, out) in r.iter_mut().enumerate() {
                    for (column, value) in out.iter_mut().enumerate() {
                        *value = a[row][0] * o[0][column]
                            + a[row][1] * o[1][column]
                            + a[row][2] * o[2][column];
                    }
                }
            }
        }
        Transform {
            m: r,
            kind: t,
            dirty: t,
        }
    }

    /// `QTransform::map(qreal, qreal)`。
    pub fn map(&self, x: f64, y: f64) -> (f64, f64) {
        let m = &self.m;
        match self.kind() {
            TxType::None => (x, y),
            TxType::Translate => (x + m[2][0], y + m[2][1]),
            TxType::Scale => (m[0][0] * x + m[2][0], m[1][1] * y + m[2][1]),
            kind => {
                let nx = m[0][0] * x + m[1][0] * y + m[2][0];
                let ny = m[0][1] * x + m[1][1] * y + m[2][1];
                if kind == TxType::Project {
                    let mut w = m[0][2] * x + m[1][2] * y + m[2][2];
                    if w < 0.000001 {
                        w = 0.000001;
                    }
                    let w = 1.0 / w;
                    (nx * w, ny * w)
                } else {
                    (nx, ny)
                }
            }
        }
    }

    /// `QTransform::mapRect(QRectF)`。
    pub fn map_rect(&self, rect: RectF) -> RectF {
        let m = &self.m;
        let kind = self.kind();
        if kind <= TxType::Translate {
            return RectF::new(rect.x + m[2][0], rect.y + m[2][1], rect.width, rect.height);
        }
        if kind <= TxType::Scale {
            let mut x = m[0][0] * rect.x + m[2][0];
            let mut y = m[1][1] * rect.y + m[2][1];
            let mut w = m[0][0] * rect.width;
            let mut h = m[1][1] * rect.height;
            if w < 0.0 {
                w = -w;
                x -= w;
            }
            if h < 0.0 {
                h = -h;
                y -= h;
            }
            return RectF::new(x, y, w, h);
        }
        let corners = [
            self.map(rect.x, rect.y),
            self.map(rect.x + rect.width, rect.y),
            self.map(rect.x + rect.width, rect.y + rect.height),
            self.map(rect.x, rect.y + rect.height),
        ];
        let (mut x0, mut y0) = corners[0];
        let (mut x1, mut y1) = corners[0];
        for &(x, y) in &corners[1..] {
            x0 = x0.min(x);
            y0 = y0.min(y);
            x1 = x1.max(x);
            y1 = y1.max(y);
        }
        RectF::new(x0, y0, x1 - x0, y1 - y0)
    }

    /// `QTransform::inverted`。逆が無ければ None。
    pub fn inverted(&self) -> Option<Transform> {
        let m = &self.m;
        let mut inv = Transform::identity();
        match self.kind() {
            TxType::None => {}
            TxType::Translate => {
                inv.m[2][0] = -m[2][0];
                inv.m[2][1] = -m[2][1];
            }
            TxType::Scale => {
                if fuzzy_is_null(m[0][0]) || fuzzy_is_null(m[1][1]) {
                    return None;
                }
                inv.m[0][0] = 1.0 / m[0][0];
                inv.m[1][1] = 1.0 / m[1][1];
                inv.m[2][0] = -m[2][0] * inv.m[0][0];
                inv.m[2][1] = -m[2][1] * inv.m[1][1];
            }
            _ => {
                let det = m[0][0] * (m[2][2] * m[1][1] - m[2][1] * m[1][2])
                    - m[1][0] * (m[2][2] * m[0][1] - m[2][1] * m[0][2])
                    + m[2][0] * (m[1][2] * m[0][1] - m[1][1] * m[0][2]);
                if fuzzy_is_null(det) {
                    return None;
                }
                let h = [
                    [
                        m[1][1] * m[2][2] - m[1][2] * m[2][1],
                        m[0][2] * m[2][1] - m[0][1] * m[2][2],
                        m[0][1] * m[1][2] - m[0][2] * m[1][1],
                    ],
                    [
                        m[1][2] * m[2][0] - m[1][0] * m[2][2],
                        m[0][0] * m[2][2] - m[0][2] * m[2][0],
                        m[0][2] * m[1][0] - m[0][0] * m[1][2],
                    ],
                    [
                        m[1][0] * m[2][1] - m[1][1] * m[2][0],
                        m[0][1] * m[2][0] - m[0][0] * m[2][1],
                        m[0][0] * m[1][1] - m[0][1] * m[1][0],
                    ],
                ];
                let scale = 1.0 / det;
                for (row, out) in inv.m.iter_mut().enumerate() {
                    for (column, value) in out.iter_mut().enumerate() {
                        *value = h[row][column] * scale;
                    }
                }
            }
        }
        inv.kind = self.kind;
        inv.dirty = self.dirty;
        Some(inv)
    }

    /// `qt_scaleForTransform` の戻り値（回転と等倍の拡大だけか）。
    fn no_shear(&self) -> bool {
        let m = &self.m;
        match self.kind() {
            TxType::None | TxType::Translate => true,
            TxType::Scale => fuzzy_compare(m[0][0].abs(), m[1][1].abs()),
            kind => {
                let x1 = m[0][0] * m[0][0] + m[1][0] * m[1][0];
                let y1 = m[0][1] * m[0][1] + m[1][1] * m[1][1];
                let x2 = m[0][0] * m[0][0] + m[0][1] * m[0][1];
                let y2 = m[1][0] * m[1][0] + m[1][1] * m[1][1];
                if (x1 - y1).abs() > (x2 - y2).abs() {
                    kind == TxType::Rotate && fuzzy_compare(x1, y1)
                } else {
                    kind == TxType::Rotate && fuzzy_compare(x2, y2)
                }
            }
        }
    }
}

/// 実数の矩形（`QRectF`）。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RectF {
    /// 左端。
    pub x: f64,
    /// 上端。
    pub y: f64,
    /// 幅。
    pub width: f64,
    /// 高さ。
    pub height: f64,
}

impl RectF {
    /// 矩形を作る。
    pub const fn new(x: f64, y: f64, width: f64, height: f64) -> Self {
        Self {
            x,
            y,
            width,
            height,
        }
    }

    fn right(&self) -> f64 {
        self.x + self.width
    }

    fn bottom(&self) -> f64 {
        self.y + self.height
    }
}

/// 描く先。Qt の画面（`deviceRect` が `width × height`）のうち、`window` の範囲だけを
/// 画素として持つ。範囲の外へ描く画素は捨てる（Qt の計算は画面全体のまま行う）。
pub struct Device<'a> {
    image: &'a mut PremulImage,
    origin: (i64, i64),
    width: i64,
    height: i64,
}

impl<'a> Device<'a> {
    /// `image` が画面全体（`deviceRect` が画像の大きさ）。
    pub fn new(image: &'a mut PremulImage) -> Self {
        let (width, height) = (image.width() as i64, image.height() as i64);
        Self {
            image,
            origin: (0, 0),
            width,
            height,
        }
    }

    /// `image` が、大きさ `width × height` の画面の `(x, y)` から始まる一部。
    pub fn window(image: &'a mut PremulImage, x: i64, y: i64, width: usize, height: usize) -> Self {
        Self {
            image,
            origin: (x, y),
            width: width as i64,
            height: height as i64,
        }
    }

    #[inline]
    fn put(&mut self, x: i64, y: i64, src: Premul, alpha: u8) {
        let (lx, ly) = (x - self.origin.0, y - self.origin.1);
        if lx < 0 || ly < 0 || lx >= self.image.width() as i64 || ly >= self.image.height() as i64 {
            return;
        }
        let at = (ly as usize * self.image.width() + lx as usize) * 4;
        blend(&mut self.image.pixels_mut()[at..at + 4], src, alpha);
    }
}

/// `QPainter::drawImage(QPointF(x, y), image)`（行列 `matrix`・不透明度 `opacity`）。
pub fn draw_image_at(
    device: &mut Device,
    matrix: &Transform,
    x: f64,
    y: f64,
    image: &PremulImage,
    opacity: f64,
) {
    let (w, h) = (image.width() as f64, image.height() as f64);
    if matrix.kind() > TxType::Translate {
        draw_image(
            device,
            matrix,
            RectF::new(x, y, w, h),
            image,
            RectF::new(0.0, 0.0, w, h),
            opacity,
        );
        return;
    }
    let m = matrix.values();
    translate_blend(device, image, (x + m[2][0], y + m[2][1]), None, opacity);
}

/// `QRasterPaintEnginePrivate::drawImage(pt, img, func, deviceRect, alpha, sr)`。
fn translate_blend(
    device: &mut Device,
    image: &PremulImage,
    pt: (f64, f64),
    source: Option<(i64, i64, i64, i64)>,
    opacity: f64,
) {
    let alpha = const_alpha(opacity);
    if alpha == 0 || device.width <= 0 || device.height <= 0 {
        return;
    }
    let (iw0, ih0) = (image.width() as f64, image.height() as f64);
    if pt.0 > (device.width - 1) as f64 || pt.1 > (device.height - 1) as f64 {
        return;
    }
    if pt.0 + iw0 < 0.0 || pt.1 + ih0 < 0.0 {
        return;
    }
    let (mut sx, mut sy, mut iw, mut ih) = (0, 0, image.width() as i64, image.height() as i64);
    if let Some((x, y, w, h)) = source.filter(|s| s.2 > 0 && s.3 > 0) {
        sx = x;
        sy = y;
        iw = w;
        ih = h;
    }
    let mut x = q_round(pt.0);
    if x < 0 {
        sx -= x;
        iw += x;
        x = 0;
    }
    if x + iw > device.width {
        iw = device.width - x;
    }
    let mut y = q_round(pt.1);
    if y < 0 {
        sy -= y;
        ih += y;
        y = 0;
    }
    if y + ih > device.height {
        ih = device.height - y;
    }
    if iw <= 0 || ih <= 0 {
        return;
    }
    for row in 0..ih {
        for column in 0..iw {
            let (px, py) = (sx + column, sy + row);
            if px < 0 || py < 0 || px >= image.width() as i64 || py >= image.height() as i64 {
                continue;
            }
            device.put(
                x + column,
                y + row,
                image.pixel(px as usize, py as usize),
                alpha,
            );
        }
    }
}

/// `QPainter::drawImage(target, image, source)`（行列 `matrix`・不透明度 `opacity`）。
pub fn draw_image(
    device: &mut Device,
    matrix: &Transform,
    r: RectF,
    image: &PremulImage,
    sr: RectF,
    opacity: f64,
) {
    if r.width == 0.0 || r.height == 0.0 || image.width() == 0 || image.height() == 0 {
        return;
    }
    let stretch = r.width != sr.width || r.height != sr.height;
    let kind = matrix.kind();
    if kind <= TxType::Translate && !stretch {
        let m = matrix.values();
        let source = (
            q_round(sr.x),
            q_round(sr.y),
            q_round(sr.right()) - q_round(sr.x),
            q_round(sr.bottom()) - q_round(sr.y),
        );
        translate_blend(
            device,
            image,
            (r.x + m[2][0], r.y + m[2][1]),
            Some(source),
            opacity,
        );
        return;
    }
    let bounds = matrix.map_rect(r);
    let m = matrix.values();
    let limit = 32767.0;
    let exceeds = r.width > limit
        || r.height > limit
        || bounds.x < -limit
        || bounds.y < -limit
        || bounds.right() > limit
        || bounds.bottom() > limit
        || bounds.width > limit
        || bounds.height > limit
        || m[0][0] >= 512.0
        || m[1][1] >= 512.0;
    // fast_images は射影では偽（recalculateFastImages）。
    if !exceeds && kind <= TxType::Shear {
        if kind > TxType::Scale {
            if bounds.width >= 16.0 && bounds.height >= 16.0 {
                transform_image(device, image, r, sr, matrix, opacity);
                return;
            }
        } else {
            let source_2x = r.width * 2.0 == sr.width && r.height * 2.0 == sr.height;
            if kind == TxType::Scale && source_2x && m[0][0] == 2.0 && m[1][1] == 2.0 {
                let pt = (r.x * 2.0 + m[2][0], r.y * 2.0 + m[2][1]);
                let source = (
                    q_round(sr.x),
                    q_round(sr.y),
                    q_round(sr.right()) - q_round(sr.x),
                    q_round(sr.bottom()) - q_round(sr.y),
                );
                translate_blend(device, image, pt, Some(source), opacity);
                return;
            }
            let (x0, y0) = (m[0][0] * r.x + m[2][0], m[1][1] * r.y + m[2][1]);
            let (x1, y1) = (
                m[0][0] * r.right() + m[2][0],
                m[1][1] * r.bottom() + m[2][1],
            );
            let mut target = RectF::new(x0, y0, x1 - x0, y1 - y0);
            // QRectF::setX・setY は右端・下端を保つ。
            let right = target.right();
            target.x = q_round(target.x) as f64;
            target.width = right - target.x;
            let bottom = target.bottom();
            target.y = q_round(target.y) as f64;
            target.height = bottom - target.y;
            target.width = q_round(target.width) as f64;
            target.height = q_round(target.height) as f64;
            scale_image(device, image, target, sr, opacity);
            return;
        }
    }
    generic_fill(device, matrix, r, image, sr, stretch, opacity);
}

/// qblendfunctions_p.h の `qt_scale_image_32bit`。
fn scale_image(device: &mut Device, image: &PremulImage, target: RectF, sr: RectF, opacity: f64) {
    let alpha = const_alpha(opacity);
    if alpha == 0 {
        return;
    }
    let sx = sr.width / target.width;
    let sy = sr.height / target.height;
    let ix = (65536.0 * sx) as i32;
    let iy = (65536.0 * sy) as i32;
    // QRectF::normalized().toRect() と画面の共通部分。
    let (mut nx, mut ny, mut nw, mut nh) = (target.x, target.y, target.width, target.height);
    if nw < 0.0 {
        nx += nw;
        nw = -nw;
    }
    if nh < 0.0 {
        ny += nh;
        nh = -nh;
    }
    let left = q_round(nx);
    let top = q_round(ny);
    let right = q_round(nx + nw);
    let bottom = q_round(ny + nh);
    let tx1 = left.max(0);
    let ty1 = top.max(0);
    let tx2 = right.min(device.width);
    let ty2 = bottom.min(device.height);
    if tx2 <= tx1 || ty2 <= ty1 {
        return;
    }
    let mut w = tx2 - tx1;
    let mut h = ty2 - ty1;
    let mut basex: u32 = if sx < 0.0 {
        let dstx = ((tx1 as f64 + 0.5 - target.right()) * sx * 65536.0).floor() as i32 + 1;
        ((sr.right() * 65536.0) as u32).wrapping_add(dstx as u32)
    } else {
        let dstx = ((tx1 as f64 + 0.5 - target.x) * sx * 65536.0).ceil() as i32 - 1;
        ((sr.x * 65536.0) as u32).wrapping_add(dstx as u32)
    };
    let mut srcy: u32 = if sy < 0.0 {
        let dsty = ((ty1 as f64 + 0.5 - target.bottom()) * sy * 65536.0).floor() as i32 + 1;
        ((sr.bottom() * 65536.0) as u32).wrapping_add(dsty as u32)
    } else {
        let dsty = ((ty1 as f64 + 0.5 - target.y) * sy * 65536.0).ceil() as i32 - 1;
        ((sr.y * 65536.0) as u32).wrapping_add(dsty as u32)
    };
    let srch = image.height() as i64;
    let srcw = image.width() as i64;
    if ((srcy >> 16) as i32 as i64) >= srch && iy < 0 {
        srcy = srcy.wrapping_add(iy as u32);
        h -= 1;
    }
    if ((basex >> 16) as i32 as i64) >= srcw && ix < 0 {
        basex = basex.wrapping_add(ix as u32);
        w -= 1;
    }
    let yend = (srcy.wrapping_add((iy as i64 * (h - 1)) as u32) >> 16) as i32 as i64;
    if yend < 0 || yend >= srch {
        h -= 1;
    }
    let xend = (basex.wrapping_add((ix as i64 * (w - 1)) as u32) >> 16) as i32 as i64;
    if xend < 0 || xend >= srcw {
        w -= 1;
    }
    let mut y = ty1;
    while h > 0 {
        h -= 1;
        let row = (srcy >> 16) as usize;
        let mut srcx = basex;
        for x in 0..w.max(0) {
            let column = (srcx >> 16) as usize;
            if row < image.height() && column < image.width() {
                device.put(tx1 + x, y, image.pixel(column, row), alpha);
            }
            srcx = srcx.wrapping_add(ix as u32);
        }
        y += 1;
        srcy = srcy.wrapping_add(iy as u32);
    }
}

#[derive(Clone, Copy)]
struct Vertex {
    x: f64,
    y: f64,
    u: f64,
    v: f64,
}

/// qblendfunctions_p.h の `qt_transform_image`。
fn transform_image(
    device: &mut Device,
    image: &PremulImage,
    r: RectF,
    sr: RectF,
    matrix: &Transform,
    opacity: f64,
) {
    let alpha = const_alpha(opacity);
    if alpha == 0 {
        return;
    }
    let vertex = |x: f64, y: f64, u: f64, v: f64| {
        let (x, y) = matrix.map(x, y);
        Vertex { x, y, u, v }
    };
    let mut v = [
        vertex(r.x, r.y, sr.x, sr.y),
        vertex(r.right(), r.y, sr.right(), sr.y),
        vertex(r.right(), r.bottom(), sr.right(), sr.bottom()),
        vertex(r.x, r.bottom(), sr.x, sr.bottom()),
    ];
    let mut topmost = 0;
    for i in 1..4 {
        if v[i].y < v[topmost].y {
            topmost = i;
        }
    }
    match topmost {
        1 => v.rotate_left(1),
        2 => {
            v.swap(0, 2);
            v.swap(1, 3);
        }
        3 => v.rotate_right(1),
        _ => {}
    }
    let (dx1, dy1) = (v[1].x - v[0].x, v[1].y - v[0].y);
    let (dx2, dy2) = (v[3].x - v[0].x, v[3].y - v[0].y);
    if dx1 * dy2 - dx2 * dy1 > 0.0 {
        v.swap(1, 3);
    }
    let u = Vertex {
        x: v[1].x - v[0].x,
        y: v[1].y - v[0].y,
        u: v[1].u - v[0].u,
        v: v[1].v - v[0].v,
    };
    let w = Vertex {
        x: v[2].x - v[0].x,
        y: v[2].y - v[0].y,
        u: v[2].u - v[0].u,
        v: v[2].v - v[0].v,
    };
    let det = u.x * w.y - u.y * w.x;
    if det == 0.0 {
        return;
    }
    let inv_det = 1.0 / det;
    let m11 = (u.u * w.y - u.y * w.u) * inv_det;
    let m12 = (u.x * w.u - u.u * w.x) * inv_det;
    let m21 = (u.v * w.y - u.y * w.v) * inv_det;
    let m22 = (u.x * w.v - u.v * w.x) * inv_det;
    let mdx = v[0].u - m11 * v[0].x - m12 * v[0].y;
    let mdy = v[0].v - m21 * v[0].x - m22 * v[0].y;
    let steps = Steps {
        dudx: (m11 * 65536.0) as i32 as i64,
        dvdx: (m21 * 65536.0) as i32 as i64,
        dudy: (m12 * 65536.0) as i32 as i64,
        dvdy: (m22 * 65536.0) as i32 as i64,
        u0: ((0.5 * m11 + 0.5 * m12 + mdx) * 65536.0).ceil() as i32 as i64 - 1,
        v0: ((0.5 * m21 + 0.5 * m22 + mdy) * 65536.0).ceil() as i32 as i64 - 1,
        left: sr.x.floor() as i64,
        top: sr.y.floor() as i64,
        width: sr.right().ceil() as i64 - sr.x.floor() as i64,
        height: sr.bottom().ceil() as i64 - sr.y.floor() as i64,
        alpha,
    };
    let mut raster = |a: Vertex, b: Vertex, c: Vertex, d: Vertex, top: f64, bottom: f64| {
        rasterize(device, image, a, b, c, d, top, bottom, &steps);
    };
    if v[1].y < v[3].y {
        raster(v[0], v[1], v[0], v[3], v[0].y, v[1].y);
        raster(v[1], v[2], v[0], v[3], v[1].y, v[3].y);
        raster(v[1], v[2], v[3], v[2], v[3].y, v[2].y);
    } else {
        raster(v[0], v[1], v[0], v[3], v[0].y, v[3].y);
        raster(v[0], v[1], v[3], v[2], v[3].y, v[1].y);
        raster(v[1], v[2], v[3], v[2], v[1].y, v[2].y);
    }
}

struct Steps {
    dudx: i64,
    dvdx: i64,
    dudy: i64,
    dvdy: i64,
    u0: i64,
    v0: i64,
    left: i64,
    top: i64,
    width: i64,
    height: i64,
    alpha: u8,
}

/// `qt_transform_image_rasterize`。
#[allow(clippy::too_many_arguments)]
fn rasterize(
    device: &mut Device,
    image: &PremulImage,
    top_left: Vertex,
    bottom_left: Vertex,
    top_right: Vertex,
    bottom_right: Vertex,
    top_y: f64,
    bottom_y: f64,
    s: &Steps,
) {
    let from_y = q_round(top_y).max(0);
    let to_y = q_round(bottom_y).min(device.height);
    if from_y >= to_y {
        return;
    }
    let left_slope = (bottom_left.x - top_left.x) / (bottom_left.y - top_left.y);
    let right_slope = (bottom_right.x - top_right.x) / (bottom_right.y - top_right.y);
    let dx_l = (left_slope * 65536.0) as i64;
    let dx_r = (right_slope * 65536.0) as i64;
    let mut x_l =
        ((top_left.x + (0.5 + from_y as f64 - top_left.y) * left_slope + 0.5) * 65536.0) as i64;
    let mut x_r =
        ((top_right.x + (0.5 + from_y as f64 - top_right.y) * right_slope + 0.5) * 65536.0) as i64;
    let inside = |u: i64, v: i64| {
        let (uu, vv) = (u >> 16, v >> 16);
        uu >= s.left && uu < s.left + s.width && vv >= s.top && vv < s.top + s.height
    };
    for y in from_y..to_y {
        let from_x = (x_l >> 16).max(0);
        let to_x = (x_r >> 16).min(device.width);
        if from_x < to_x {
            let mut u = from_x * s.dudx + y * s.dudy + s.u0;
            let mut v = from_x * s.dvdx + y * s.dvdy + s.v0;
            let mut x1 = from_x;
            while x1 < to_x && !inside(u, v) {
                u += s.dudx;
                v += s.dvdx;
                x1 += 1;
            }
            let mut x2 = to_x;
            let mut u = (x2 - 1) * s.dudx + y * s.dudy + s.u0;
            let mut v = (x2 - 1) * s.dvdx + y * s.dvdy + s.v0;
            while x2 > x1 && !inside(u, v) {
                u -= s.dudx;
                v -= s.dvdx;
                x2 -= 1;
            }
            let mut u = from_x * s.dudx + y * s.dudy + s.u0;
            let mut v = from_x * s.dvdx + y * s.dvdy + s.v0;
            for x in from_x..to_x {
                let (mut uu, mut vv) = (u >> 16, v >> 16);
                if x < x1 || x >= x2 {
                    uu = uu.clamp(s.left, s.left + s.width - 1);
                    vv = vv.clamp(s.top, s.top + s.height - 1);
                }
                if uu >= 0
                    && vv >= 0
                    && (uu as usize) < image.width()
                    && (vv as usize) < image.height()
                {
                    device.put(x, y, image.pixel(uu as usize, vv as usize), s.alpha);
                }
                u += s.dudx;
                v += s.dvdx;
            }
        }
        x_l += dx_l;
        x_r += dx_r;
    }
}

/// 速い道を使えないときの、塗りの道（`fillRect_normalized`・`fillPath` と `fetchTransformed`）。
fn generic_fill(
    device: &mut Device,
    matrix: &Transform,
    r: RectF,
    image: &PremulImage,
    sr: RectF,
    stretch: bool,
    opacity: f64,
) {
    // handleSpans は被覆 255 に intOpacity を掛け（`(255 * o) >> 8`）、ソースオーバーの
    // 関数（comp_func_SourceOver_neon）はそれを 0〜256 の値として `(c * 255) >> 8` にする。
    let alpha = match const_alpha(opacity) {
        255 => 255,
        c => ((u32::from(c) * 255) >> 8) as u8,
    };
    if alpha == 0 {
        return;
    }
    let mut copy = *matrix;
    copy.translate(r.x, r.y);
    if stretch {
        copy.scale(r.width / sr.width, r.height / sr.height);
    }
    copy.translate(-sr.x, -sr.y);
    let mut delta = Transform::identity();
    delta.translate(1.0 / 65536.0, 1.0 / 65536.0);
    let Some(inv) = delta.then(&copy).inverted() else {
        return;
    };
    // toAlignedRect_positive(sr) と画像の範囲。
    let x1 = (sr.x.floor() as i64).max(0);
    let y1 = (sr.y.floor() as i64).max(0);
    let x2 = (sr.right().ceil() as i64).min(image.width() as i64);
    let y2 = (sr.bottom().ceil() as i64).min(image.height() as i64);
    if x2 <= x1 || y2 <= y1 {
        return;
    }
    let fetch = Fetch {
        inv,
        bounds: (x1, y1, x2, y2),
    };
    let mut spans: Vec<(i64, i64, i64)> = Vec::new();
    if matrix.kind() == TxType::Scale {
        let rr = matrix.map_rect(r);
        let (left, top) = (q_round(rr.x), q_round(rr.y));
        let (right, bottom) = (q_round(rr.right()), q_round(rr.bottom()));
        let (left, right) = (left.min(right).max(0), left.max(right).min(device.width));
        let (top, bottom) = (top.min(bottom).max(0), top.max(bottom).min(device.height));
        for y in top..bottom {
            if left < right {
                spans.push((y, left, right - left));
            }
        }
    } else {
        let _ = matrix.no_shear();
        let corners = [
            matrix.map(r.x, r.y),
            matrix.map(r.right(), r.y),
            matrix.map(r.right(), r.bottom()),
            matrix.map(r.x, r.bottom()),
            matrix.map(r.x, r.y),
        ];
        let points: Vec<Point> = corners.iter().map(|&(x, y)| Point::new(x, y)).collect();
        let mask = rasterize_polygons(
            &[points],
            FillRule::OddEven,
            device.width.max(0) as usize,
            device.height.max(0) as usize,
        );
        for (row, left, right) in mask.runs() {
            spans.push((row as i64, left as i64, (right - left) as i64));
        }
    }
    let mut buffer = Vec::new();
    for (y, x, length) in spans {
        buffer.clear();
        fetch.span(image, y, x, length, &mut buffer);
        for (offset, &(px, py)) in buffer.iter().enumerate() {
            device.put(x + offset as i64, y, image.pixel(px, py), alpha);
        }
    }
}

struct Fetch {
    inv: Transform,
    bounds: (i64, i64, i64, i64),
}

impl Fetch {
    /// `fetchTransformed_fetcher<BlendTransformed>` の拾う画素の位置。
    fn span(
        &self,
        image: &PremulImage,
        y: i64,
        x: i64,
        length: i64,
        out: &mut Vec<(usize, usize)>,
    ) {
        let _ = image;
        let m = self.inv.values();
        let (m11, m12, m13) = (m[0][0], m[0][1], m[0][2]);
        let (m21, m22, m23) = (m[1][0], m[1][1], m[1][2]);
        let (dx, dy, m33) = (m[2][0], m[2][1], m[2][2]);
        let (x1, y1, x2, y2) = self.bounds;
        let clamp =
            |px: i64, py: i64| (px.clamp(x1, x2 - 1) as usize, py.clamp(y1, y2 - 1) as usize);
        let cx = x as f64 + 0.5;
        let cy = y as f64 + 0.5;
        let affine = self.inv.kind() < TxType::Project;
        let f1 = m11 * m11 + m21 * m21;
        let f2 = m12 * m12 + m22 * m22;
        let fast_matrix = affine
            && f1 < 1e4
            && f2 < 1e4
            && f1 > 1.0 / 65536.0
            && f2 > 1.0 / 65536.0
            && dx.abs() < 1e4
            && dy.abs() < 1e4;
        let fast = fast_matrix && {
            let mut fx = (m21 * cy + m11 * cx + dx) * 65536.0;
            let mut fy = (m22 * cy + m12 * cx + dy) * 65536.0;
            let mut low = fx.min(fy);
            let mut high = fx.max(fy);
            fx += (m11 * 65536.0).trunc() * length as f64;
            fy += (m12 * 65536.0).trunc() * length as f64;
            low = low.min(fx.min(fy));
            high = high.max(fx.max(fy));
            low >= i32::MIN as f64 && high <= i32::MAX as f64
        };
        if fast {
            let fdx = (m11 * 65536.0) as i32;
            let fdy = (m12 * 65536.0) as i32;
            let mut fx = ((m21 * cy + m11 * cx + dx) * 65536.0) as i32;
            let mut fy = ((m22 * cy + m12 * cx + dy) * 65536.0) as i32;
            for _ in 0..length {
                out.push(clamp((fx >> 16) as i64, (fy >> 16) as i64));
                fx = fx.wrapping_add(fdx);
                fy = fy.wrapping_add(fdy);
            }
        } else {
            let mut fx = m21 * cy + m11 * cx + dx;
            let mut fy = m22 * cy + m12 * cx + dy;
            let mut fw = m23 * cy + m13 * cx + m33;
            for _ in 0..length {
                let iw = if fw == 0.0 { 1.0 } else { 1.0 / fw };
                out.push(clamp((fx * iw).floor() as i64, (fy * iw).floor() as i64));
                fx += m11;
                fy += m12;
                fw += m13;
                if fw == 0.0 {
                    fw += m13;
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn gradient(width: usize, height: usize) -> PremulImage {
        let mut pixels = Vec::new();
        for y in 0..height {
            for x in 0..width {
                pixels.extend_from_slice(&[(x * 9) as u8, (y * 11) as u8, 77, 255]);
            }
        }
        PremulImage::from_premultiplied(width, height, pixels).unwrap()
    }

    #[test]
    fn transform_kinds_follow_qt() {
        let mut t = Transform::identity();
        assert_eq!(t.kind(), TxType::None);
        t.translate(3.0, 4.0);
        assert_eq!(t.kind(), TxType::Translate);
        t.scale(2.0, 2.0);
        assert_eq!(t.kind(), TxType::Scale);
        t.rotate(30.0);
        assert_eq!(t.kind(), TxType::Rotate);
        let shear = Transform::new(1.0, 0.5, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0);
        assert_eq!(shear.kind(), TxType::Shear);
        let near = Transform::new(1.0, 1e-14, 0.0, 0.0, 1.0, 0.0, 5.0, 0.0, 1.0);
        assert_eq!(near.kind(), TxType::Translate);
        let inv = t.inverted().unwrap();
        let (x, y) = t.map(5.0, 7.0);
        let (bx, by) = inv.map(x, y);
        assert!((bx - 5.0).abs() < 1e-9 && (by - 7.0).abs() < 1e-9);
    }

    #[test]
    fn integer_translation_copies_pixels() {
        let source = gradient(5, 4);
        let mut out = PremulImage::transparent(12, 10).unwrap();
        let mut t = Transform::identity();
        t.translate(3.0, 2.0);
        draw_image_at(&mut Device::new(&mut out), &t, 0.0, 0.0, &source, 1.0);
        assert_eq!(out.pixel(3, 2), source.pixel(0, 0));
        assert_eq!(out.pixel(7, 5), source.pixel(4, 3));
        assert_eq!(out.pixel(8, 5), [0; 4]);
    }

    #[test]
    fn window_matches_full_device() {
        let source = gradient(20, 18);
        let mut t = Transform::identity();
        t.translate(30.0, 20.0);
        t.rotate(33.0);
        t.scale(1.3, 0.8);
        let mut full = PremulImage::transparent(64, 64).unwrap();
        draw_image_at(&mut Device::new(&mut full), &t, 0.0, 0.0, &source, 1.0);
        let mut part = PremulImage::transparent(20, 30).unwrap();
        draw_image_at(
            &mut Device::window(&mut part, 15, 10, 64, 64),
            &t,
            0.0,
            0.0,
            &source,
            1.0,
        );
        for y in 0..30 {
            for x in 0..20 {
                assert_eq!(part.pixel(x, y), full.pixel(x + 15, y + 10));
            }
        }
        assert!(full.pixels().iter().any(|&v| v != 0));
    }
}
