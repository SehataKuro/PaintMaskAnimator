//! 画素の選択範囲（投げ縄・矩形・自動選択・色で選択・反転）と、その中の編集。
//!
//! Python の `canvas_selection.py`（SelectionMixin）と、選択に関わる
//! `canvas_input_events.py`・`canvas_paint_tools.auto_select_region`・
//! `canvas.apply_selection_clip`・`main_window_color_interaction` の判断を移したもの。
//!
//! 選択範囲は2通りで持つ（Python と同じ）。
//!
//! - 多角形（投げ縄・矩形・すべてを選択・描画範囲の自動選択）。画素にするときは
//!   Qt のアンチエイリアスなしの塗りと同じ画素になる（[`raster`]）。
//! - マスク（自動選択・反転・色で選択）。画像と同じ大きさの二値マスクと、
//!   点線を引くための輪郭を持つ。
//!
//! 選択の解除（[`SelectionState::clear`]）は、今のツールに関係なく行える
//! （issue #110）。解除できるかは [`SelectionState::can_clear`] で、ツールを見ない。

mod contour;
mod edit;
mod pick;
mod raster;
#[cfg(test)]
mod tests;

pub use contour::{largest_contour, mask_contours, Corner};
pub use edit::{
    cleared_selection, clip_write, copy_selection, cut_selection, delete_selection, lift_selection,
    masked_copy, paste, transform_source_rect, Cut, Lifted, SelectionEdit,
};
pub use pick::{color_mask, contiguous_region, used_area, ContiguousOptions, ContiguousResult};
pub use raster::Point;

use crate::image::Rect;
use crate::Error;

/// 負の座標も持てる整数の矩形。Qt の QRect（左上と幅・高さ）に当たる。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct IntRect {
    /// 左端の x 座標。
    pub x: i64,
    /// 上端の y 座標。
    pub y: i64,
    /// 幅。0 以下なら空。
    pub width: i64,
    /// 高さ。0 以下なら空。
    pub height: i64,
}

impl IntRect {
    /// 幅か高さが 0 以下なら真（QRect::isEmpty）。
    pub fn is_empty(&self) -> bool {
        self.width <= 0 || self.height <= 0
    }

    /// `width × height` の画像と重なる部分を返す。重ならなければ `None`。
    pub fn clip_to(&self, width: usize, height: usize) -> Option<Rect> {
        if self.is_empty() {
            return None;
        }
        let left = self.x.max(0);
        let top = self.y.max(0);
        let right = (self.x + self.width).min(width as i64);
        let bottom = (self.y + self.height).min(height as i64);
        (right > left && bottom > top).then(|| Rect {
            x: left as usize,
            y: top as usize,
            width: (right - left) as usize,
            height: (bottom - top) as usize,
        })
    }
}

impl From<Rect> for IntRect {
    fn from(rect: Rect) -> Self {
        Self {
            x: rect.x as i64,
            y: rect.y as i64,
            width: rect.width as i64,
            height: rect.height as i64,
        }
    }
}

/// 二値の選択マスク。1 画素 1 バイト（0 か 1）の行優先。
///
/// Qt のアンチエイリアスなしの塗りは画素を塗るか塗らないかの2値なので、
/// 中間の値は持たない（numpy の bool 配列の `tobytes()` と同じ並び）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SelectionMask {
    width: usize,
    height: usize,
    bits: Vec<u8>,
}

impl SelectionMask {
    /// 何も選んでいないマスク。
    pub fn empty(width: usize, height: usize) -> Self {
        Self {
            width,
            height,
            bits: vec![0; width * height],
        }
    }

    /// 1 画素 1 バイトの並びから作る。0 以外は 1 にそろえる。
    pub fn from_bits(width: usize, height: usize, bits: &[u8]) -> Result<Self, Error> {
        if width.checked_mul(height) != Some(bits.len()) {
            return Err(Error::Image("マスクと寸法の長さが一致しません"));
        }
        Ok(Self {
            width,
            height,
            bits: bits.iter().map(|&value| u8::from(value != 0)).collect(),
        })
    }

    /// 多角形を、Qt のアンチエイリアスなし・偶奇規則の塗りと同じ画素で選ぶ。
    ///
    /// `selection_mask_bool` の多角形の場合（`drawPolygon`）と、
    /// `apply_selection_clip` の多角形の場合（`setClipPath`）は、同じ画素になる。
    /// 頂点は `offset` を引いた位置に塗る（マスクの左上が画像の `offset`）。
    pub fn from_polygon(points: &[Point], width: usize, height: usize, offset: Point) -> Self {
        let mut mask = Self::empty(width, height);
        raster::fill_polygon(&mut mask.bits, width, height, points, offset);
        mask
    }

    /// マスクの幅。
    pub fn width(&self) -> usize {
        self.width
    }

    /// マスクの高さ。
    pub fn height(&self) -> usize {
        self.height
    }

    /// 1 画素 1 バイト（0 か 1）の並び。
    pub fn bits(&self) -> &[u8] {
        &self.bits
    }

    /// (x, y) を選んでいれば真。範囲外は偽。
    pub fn contains(&self, x: usize, y: usize) -> bool {
        x < self.width && y < self.height && self.bits[y * self.width + x] != 0
    }

    /// 1 画素も選んでいなければ真。
    pub fn is_empty(&self) -> bool {
        self.bits.iter().all(|&value| value == 0)
    }

    /// 選んだ画素を囲む最小の矩形。何も選んでいなければ `None`。
    pub fn bounds(&self) -> Option<Rect> {
        let mut left = usize::MAX;
        let mut top = usize::MAX;
        let mut right = 0;
        let mut bottom = 0;
        for y in 0..self.height {
            let row = &self.bits[y * self.width..(y + 1) * self.width];
            let Some(first) = row.iter().position(|&value| value != 0) else {
                continue;
            };
            let last = row.iter().rposition(|&value| value != 0).unwrap_or(first);
            left = left.min(first);
            right = right.max(last + 1);
            top = top.min(y);
            bottom = y + 1;
        }
        (left != usize::MAX).then(|| Rect {
            x: left,
            y: top,
            width: right - left,
            height: bottom - top,
        })
    }

    /// 左上を (`offset_x`, `offset_y`) にした `width × height` の部分。範囲外は選ばない。
    pub fn crop(&self, width: usize, height: usize, offset_x: i64, offset_y: i64) -> Self {
        let mut result = Self::empty(width, height);
        let source_left = offset_x.max(0);
        let source_top = offset_y.max(0);
        let source_right = (offset_x + width as i64).min(self.width as i64);
        let source_bottom = (offset_y + height as i64).min(self.height as i64);
        if source_right <= source_left || source_bottom <= source_top {
            return result;
        }
        let run = (source_right - source_left) as usize;
        for source_y in source_top..source_bottom {
            let target_y = (source_y - offset_y) as usize;
            let target_x = (source_left - offset_x) as usize;
            let source = source_y as usize * self.width + source_left as usize;
            let target = target_y * width + target_x;
            result.bits[target..target + run].copy_from_slice(&self.bits[source..source + run]);
        }
        result
    }

    fn check_same_size(&self, other: &Self) -> Result<(), Error> {
        if self.width != other.width || self.height != other.height {
            return Err(Error::Image("マスクの寸法が一致しません"));
        }
        Ok(())
    }

    /// 両方の和（Shift で足す）。
    pub fn union(&self, other: &Self) -> Result<Self, Error> {
        self.check_same_size(other)?;
        Ok(self.zip_with(other, |a, b| a | b))
    }

    /// `other` を除いた部分（Alt で引く）。
    pub fn subtract(&self, other: &Self) -> Result<Self, Error> {
        self.check_same_size(other)?;
        Ok(self.zip_with(other, |a, b| a & !b & 1))
    }

    /// 両方に共通の部分。
    pub fn intersect(&self, other: &Self) -> Result<Self, Error> {
        self.check_same_size(other)?;
        Ok(self.zip_with(other, |a, b| a & b))
    }

    fn zip_with(&self, other: &Self, op: impl Fn(u8, u8) -> u8) -> Self {
        Self {
            width: self.width,
            height: self.height,
            bits: self
                .bits
                .iter()
                .zip(&other.bits)
                .map(|(&a, &b)| op(a, b))
                .collect(),
        }
    }

    /// `area` の中だけを反転し、外は選ばない（`invert_selection`）。
    pub fn inverted_within(&self, area: Rect) -> Self {
        let mut result = Self::empty(self.width, self.height);
        let right = (area.x + area.width).min(self.width);
        let bottom = (area.y + area.height).min(self.height);
        for y in area.y.min(bottom)..bottom {
            for x in area.x.min(right)..right {
                let index = y * self.width + x;
                result.bits[index] = 1 - self.bits[index];
            }
        }
        result
    }

    /// 輪郭（[`mask_contours`]）。
    pub fn contours(&self) -> Vec<Vec<Corner>> {
        mask_contours(&self.bits, self.width, self.height)
    }
}

/// 自動選択の足し引き（Python では修飾キー）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CombineMode {
    /// 今の選択を置き換える（修飾キーなし）。
    Replace,
    /// 今の選択に足す（Shift）。
    Add,
    /// 今の選択から引く（Alt。Shift と同時なら引く方が勝つ）。
    Subtract,
}

#[derive(Clone, Debug, PartialEq)]
enum Shape {
    Polygon(Vec<Point>),
    Mask {
        mask: SelectionMask,
        outlines: Vec<Vec<Point>>,
        bounds: Rect,
    },
}

/// 1つの選択範囲。多角形かマスクのどちらかで持つ。
#[derive(Clone, Debug, PartialEq)]
pub struct Selection {
    shape: Shape,
}

impl Selection {
    /// 多角形の選択（投げ縄など）。頂点が無ければ `None`。
    ///
    /// Python は頂点が1つ以上あれば「選択あり」として扱う（塗る画素が無くても）。
    pub fn polygon(points: Vec<Point>) -> Option<Self> {
        (!points.is_empty()).then_some(Self {
            shape: Shape::Polygon(points),
        })
    }

    /// 矩形選択。ドラッグの始点と終点から、左上から時計回りの4点にする。
    ///
    /// 始点と終点が同じでも選択を作る（Python と同じ。画素は選ばない）。
    pub fn rect(start: Point, end: Point) -> Self {
        let (x1, x2) = if start.0 <= end.0 {
            (start.0, end.0)
        } else {
            (end.0, start.0)
        };
        let (y1, y2) = if start.1 <= end.1 {
            (start.1, end.1)
        } else {
            (end.1, start.1)
        };
        Self {
            shape: Shape::Polygon(vec![(x1, y1), (x2, y1), (x2, y2), (x1, y2)]),
        }
    }

    /// 整数の矩形の選択（すべてを選択・描画範囲の自動選択）。
    pub fn from_rect(rect: Rect) -> Self {
        let (left, top) = (rect.x as f64, rect.y as f64);
        let (right, bottom) = (left + rect.width as f64, top + rect.height as f64);
        Self {
            shape: Shape::Polygon(vec![
                (left, top),
                (right, top),
                (right, bottom),
                (left, bottom),
            ]),
        }
    }

    /// マスクの選択（`set_selection_mask`）。空のマスクなら `None`（選択の解除）。
    pub fn from_mask(mask: SelectionMask) -> Option<Self> {
        let bounds = mask.bounds()?;
        let outlines = mask
            .contours()
            .into_iter()
            .map(|contour| {
                contour
                    .into_iter()
                    .map(|(x, y)| (x as f64, y as f64))
                    .collect()
            })
            .collect();
        Some(Self {
            shape: Shape::Mask {
                mask,
                outlines,
                bounds,
            },
        })
    }

    /// マスクで持つ選択なら真。
    pub fn is_mask(&self) -> bool {
        matches!(self.shape, Shape::Mask { .. })
    }

    /// 多角形で持つ選択なら、その頂点。
    pub fn polygon_points(&self) -> Option<&[Point]> {
        match &self.shape {
            Shape::Polygon(points) => Some(points),
            Shape::Mask { .. } => None,
        }
    }

    /// マスクで持つ選択なら、そのマスク（画像と同じ大きさ）。
    pub fn mask_override(&self) -> Option<&SelectionMask> {
        match &self.shape {
            Shape::Polygon(_) => None,
            Shape::Mask { mask, .. } => Some(mask),
        }
    }

    /// 左上を (`offset_x`, `offset_y`) にした `width × height` の選択マスク
    /// （`selection_mask_bool`）。
    pub fn mask(&self, width: usize, height: usize, offset_x: i64, offset_y: i64) -> SelectionMask {
        match &self.shape {
            Shape::Polygon(points) => SelectionMask::from_polygon(
                points,
                width,
                height,
                (offset_x as f64, offset_y as f64),
            ),
            Shape::Mask { mask, .. } => mask.crop(width, height, offset_x, offset_y),
        }
    }

    /// 描画ツールのクリップ（`apply_selection_clip`）。描く先の左上が画像の `offset`。
    ///
    /// `None` はクリップしない（頂点が3つ未満の多角形。Python も何もしない）。
    /// マスクの選択では、Python と同じく `offset` を偶数丸めした整数でずらす。
    pub fn clip_mask(&self, width: usize, height: usize, offset: Point) -> Option<SelectionMask> {
        match &self.shape {
            Shape::Polygon(points) => (points.len() >= 3)
                .then(|| SelectionMask::from_polygon(points, width, height, offset)),
            Shape::Mask { mask, .. } => Some(mask.crop(
                width,
                height,
                offset.0.round_ties_even() as i64,
                offset.1.round_ties_even() as i64,
            )),
        }
    }

    /// 選択の外接矩形（`selection_bounds`）。多角形は頂点の範囲を外へ整数に広げる。
    pub fn bounds(&self) -> IntRect {
        match &self.shape {
            Shape::Polygon(points) => polygon_bounds(points),
            Shape::Mask { bounds, .. } => IntRect::from(*bounds),
        }
    }

    /// 点線（選択範囲の輪郭）を引く閉じた折れ線。画像の座標。
    ///
    /// 各折れ線の最後の点から最初の点へも線を引く（頂点が3つ以上のとき。Python と同じ）。
    pub fn outlines(&self) -> Vec<Vec<Point>> {
        match &self.shape {
            Shape::Polygon(points) => vec![points.clone()],
            Shape::Mask { outlines, .. } => outlines.clone(),
        }
    }

    /// `area`（キャンバスの範囲）の中だけで反転する（`invert_selection`）。
    /// 反転して空になれば `None`（選択の解除）。
    pub fn inverted(&self, width: usize, height: usize, area: Rect) -> Option<Self> {
        Self::from_mask(self.mask(width, height, 0, 0).inverted_within(area))
    }

    /// 自動選択の結果 `region`（画像と同じ大きさ）を今の選択と足し引きする
    /// （`auto_select_region` の最後）。空になれば `None`。
    pub fn combine(
        current: Option<&Self>,
        region: &SelectionMask,
        mode: CombineMode,
    ) -> Result<Option<Self>, Error> {
        let (width, height) = (region.width(), region.height());
        let current = current
            .map(|selection| selection.mask(width, height, 0, 0))
            .unwrap_or_else(|| SelectionMask::empty(width, height));
        let combined = match mode {
            CombineMode::Replace => region.clone(),
            CombineMode::Add => current.union(region)?,
            CombineMode::Subtract => current.subtract(region)?,
        };
        Ok(Self::from_mask(combined))
    }
}

/// 頂点を囲む矩形を外へ整数に広げる（`QRectF(...).toAlignedRect()`）。
fn polygon_bounds(points: &[Point]) -> IntRect {
    let min_x = points.iter().map(|p| p.0).fold(f64::INFINITY, f64::min);
    let min_y = points.iter().map(|p| p.1).fold(f64::INFINITY, f64::min);
    let max_x = points.iter().map(|p| p.0).fold(f64::NEG_INFINITY, f64::max);
    let max_y = points.iter().map(|p| p.1).fold(f64::NEG_INFINITY, f64::max);
    // QRectF の右端は x + 幅。幅を引き算で作ってから足し直す丸めも Qt と同じにする。
    let left = min_x.floor() as i64;
    let top = min_y.floor() as i64;
    let right = (min_x + (max_x - min_x)).ceil() as i64;
    let bottom = (min_y + (max_y - min_y)).ceil() as i64;
    IntRect {
        x: left,
        y: top,
        width: right - left,
        height: bottom - top,
    }
}

/// レイヤー画像のうちキャンバス（外側の余白を除く）の範囲（`_canvas_area_rect`）。
pub fn canvas_area(
    image_width: usize,
    image_height: usize,
    margin: usize,
    canvas_width: usize,
    canvas_height: usize,
) -> Option<Rect> {
    IntRect {
        x: margin as i64,
        y: margin as i64,
        width: canvas_width as i64,
        height: canvas_height as i64,
    }
    .clip_to(image_width, image_height)
}

/// 選択の解除で、呼び出し側がすること。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Cleared {
    /// 選択範囲の変形中だったので、変形を取り消す必要がある。
    pub cancel_transform: bool,
    /// 選択・投げ縄の途中・矩形のドラッグのどれかが消えた（画面の更新が要る）。
    pub changed: bool,
}

/// キャンバスの選択の状態。今の選択と、作っている途中の投げ縄・矩形を持つ。
///
/// ツールの種類は持たない。解除はどのツールでも同じように行える（issue #110）。
#[derive(Clone, Debug, Default, PartialEq)]
pub struct SelectionState {
    selection: Option<Selection>,
    lasso: Vec<Point>,
    rect_drag: Option<(Point, Point)>,
    transform_active: bool,
}

impl SelectionState {
    /// 選択なしの状態。
    pub fn new() -> Self {
        Self::default()
    }

    /// 今の選択。
    pub fn selection(&self) -> Option<&Selection> {
        self.selection.as_ref()
    }

    /// 選択があれば真。
    pub fn has_selection(&self) -> bool {
        self.selection.is_some()
    }

    /// 「選択範囲を解除」を押せるか。選択があれば、ツールに関係なく真（issue #110）。
    pub fn can_clear(&self) -> bool {
        self.selection.is_some()
    }

    /// 選択範囲の変形中か（変形の状態は呼び出し側が持ち、ここへ知らせる）。
    pub fn transform_active(&self) -> bool {
        self.transform_active
    }

    /// 選択範囲の変形を始めた・終えたことを知らせる。
    pub fn set_transform_active(&mut self, active: bool) {
        self.transform_active = active;
    }

    /// 作っている途中の投げ縄の頂点（赤い点線で引く）。
    pub fn lasso(&self) -> &[Point] {
        &self.lasso
    }

    /// ドラッグ中の矩形選択の始点と終点（赤い点線で引く）。
    pub fn rect_drag(&self) -> Option<(Point, Point)> {
        self.rect_drag
    }

    /// 選択を解除する（`clear_selection`）。今のツールに関係なく、同じように解除する。
    ///
    /// 変形中なら、呼び出し側は変形を取り消す（`cancel_transform`）。
    pub fn clear(&mut self) -> Cleared {
        let changed =
            self.selection.is_some() || !self.lasso.is_empty() || self.rect_drag.is_some();
        let cancel_transform = self.transform_active;
        self.selection = None;
        self.lasso.clear();
        self.rect_drag = None;
        self.transform_active = false;
        Cleared {
            cancel_transform,
            changed,
        }
    }

    /// 選択を置き換える（`None` なら解除と同じ）。途中の投げ縄・矩形は消す。
    pub fn set_selection(&mut self, selection: Option<Selection>) {
        self.selection = selection;
        self.lasso.clear();
        self.rect_drag = None;
    }

    /// マスクを選択にする（`set_selection_mask`）。空なら解除する。
    pub fn set_mask(&mut self, mask: SelectionMask) {
        self.set_selection(Selection::from_mask(mask));
    }

    /// 投げ縄を始める（押したとき）。
    pub fn begin_lasso(&mut self, point: Point) {
        self.lasso = vec![point];
    }

    /// 投げ縄に点を足す（動かしたとき。キャンバスの外の点も持つ）。
    pub fn extend_lasso(&mut self, point: Point) {
        if !self.lasso.is_empty() {
            self.lasso.push(point);
        }
    }

    /// 投げ縄を確定する（離したとき）。頂点が3つ以上なら選択にして真を返す。
    ///
    /// 3つ未満なら選択は変えずに偽を返す。途中の投げ縄はどちらでも消す。
    pub fn finish_lasso(&mut self) -> bool {
        let points = std::mem::take(&mut self.lasso);
        if points.len() < 3 {
            return false;
        }
        self.selection = Selection::polygon(points);
        true
    }

    /// 矩形選択のドラッグを始める。
    pub fn begin_rect(&mut self, point: Point) {
        self.rect_drag = Some((point, point));
    }

    /// 矩形選択のドラッグの終点を動かす（キャンバスの外の点も持つ）。
    pub fn update_rect(&mut self, point: Point) {
        if let Some((_, end)) = self.rect_drag.as_mut() {
            *end = point;
        }
    }

    /// 矩形選択を確定する。ドラッグ中でなければ偽。
    pub fn finish_rect(&mut self) -> bool {
        let Some((start, end)) = self.rect_drag.take() else {
            return false;
        };
        self.selection = Some(Selection::rect(start, end));
        true
    }

    /// キャンバス全体を選ぶ（`select_all`）。変形中は何もしないで偽。
    pub fn select_all(&mut self, area: Rect) -> bool {
        if self.transform_active || area.width == 0 || area.height == 0 {
            return false;
        }
        self.set_selection(Some(Selection::from_rect(area)));
        true
    }

    /// `area` の中で反転する（`invert_selection`）。選択が無いか変形中なら偽。
    pub fn invert(&mut self, width: usize, height: usize, area: Rect) -> bool {
        if self.transform_active {
            return false;
        }
        let Some(selection) = &self.selection else {
            return false;
        };
        let inverted = selection.inverted(width, height, area);
        self.set_selection(inverted);
        true
    }

    /// 自動選択の結果を足し引きして選択にする（`auto_select_region` の最後）。
    pub fn combine(&mut self, region: &SelectionMask, mode: CombineMode) -> Result<(), Error> {
        let combined = Selection::combine(self.selection.as_ref(), region, mode)?;
        self.set_selection(combined);
        Ok(())
    }
}
