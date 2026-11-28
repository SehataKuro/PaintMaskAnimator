//! 変形ツール（拡大縮小・自由変形（遠近）・メッシュ・回転・反転）。
//!
//! Python の `canvas_selection.begin_selection_transform`・`commit_selection_transform`・
//! `cancel_selection_transform`・`rotate_selection_transform`、
//! `canvas_transform_geometry.TransformGeometryMixin`（ハンドルとドラッグ）、
//! `canvas_transform_mask.TransformMaskMixin`（メッシュの格子とクオリティ変形）、
//! `undo_entries.TransformUndo`（変形の途中の形の履歴）に対応する。
//!
//! [`TransformSession`] が変形の始めから確定・取り消しまでを持つ。
//!
//! 1. [`TransformSession::begin`]：選択範囲（無ければ描いてある範囲）を切り出し、
//!    レイヤーからは消す。
//! 2. [`TransformSession::begin_drag`]・[`TransformSession::update_drag`]・
//!    [`TransformSession::end_drag`]：ハンドル・回転のつまみ・中の移動。
//!    [`TransformSession::rotate`]・[`TransformSession::set_mesh_grid`] も形を変える。
//!    形を変えるたびに、変える前の形を履歴（[`TransformSession::undo`]）に積む。
//! 3. [`TransformSession::preview`]：今の形で描いた画素（確定と同じ画素）。
//! 4. [`TransformSession::commit`]：確定したレイヤーの画像と Undo の差分。
//!    [`TransformSession::cancel`]：変形を始める前のレイヤーの画像。
//!
//! **移動は 1 画素単位**（issue #116）：中のドラッグで動かす量は整数の画素に丸める。
//! 拡大・回転などをしていない「移動だけ」の形は、描き直さずに画素をそのままずらして
//! 貼る（線の太さが変わらない）。Python は小数のまま動かしていたので、ここだけは
//! 意図して Python と違う（整数で動かした場合の画素は Python と同じ）。

pub mod geometry;
pub mod render;

#[cfg(test)]
mod tests;

use geometry::{
    center, mesh_reference_points, nearest_handle, outer_polygon, polygon_contains,
    regular_grid_points, resample_mesh, rotate_points, rotation_handle, Pt,
};
use render::{prepare_labels, render_plain, render_quality, Labels, Rendered, Rgb, Shape};

use crate::brush::UndoPatch;
use crate::compose::{premultiply, unpremultiply, PremulImage};
use crate::image::{Rect, RgbaImage};
use crate::selection::{lift_selection, used_area, Selection, SelectionEdit};
use crate::Error;

/// 変形の種類（`transform_mode`）。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Mode {
    /// 拡大縮小（角を動かすと、向かいの角を中心に縦横同じ倍率）。
    Scale,
    /// 自由変形（角を 1 つずつ動かす。遠近になる）。
    #[default]
    Free,
    /// メッシュ変形。
    Mesh,
}

impl Mode {
    /// Python の名前（`"scale"`・`"free"`・`"mesh"`）から。知らない名前は自由変形。
    pub fn from_name(name: &str) -> Self {
        match name {
            "scale" => Self::Scale,
            "mesh" => Self::Mesh,
            _ => Self::Free,
        }
    }
}

/// 変形の設定。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TransformOptions {
    /// クオリティ変形（色ごとの面積で描き、補間色を作らない）。
    pub quality: bool,
    /// クオリティ変形で線として扱う色（使用色パネルで選んだ色）。
    pub line_colors: Vec<Rgb>,
    /// 線の太さのしきい値（`transform_line_threshold`、1〜254、既定 96）。
    pub line_threshold: u8,
    /// メッシュの横の点の数（2〜12）。
    pub mesh_cols: usize,
    /// メッシュの縦の点の数（2〜12）。
    pub mesh_rows: usize,
}

impl Default for TransformOptions {
    fn default() -> Self {
        Self {
            quality: false,
            line_colors: Vec::new(),
            line_threshold: 96,
            mesh_cols: 4,
            mesh_rows: 4,
        }
    }
}

/// 変形の途中の形（`TransformUndo`）。
#[derive(Clone, Debug, PartialEq)]
pub struct Snapshot {
    /// 変形の点。
    pub points: Vec<Pt>,
    /// メッシュの横の点の数。
    pub cols: usize,
    /// メッシュの縦の点の数。
    pub rows: usize,
    /// メッシュの基準格子。
    pub reference: Vec<Pt>,
}

/// ドラッグの種類（`transform_drag_kind`）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DragKind {
    /// 中を動かす。
    Move,
    /// 回転のつまみ。
    Rotate,
    /// ハンドル（点の番号）。
    Handle(usize),
}

#[derive(Clone, Debug)]
struct Drag {
    kind: DragKind,
    start: Pt,
    start_points: Vec<Pt>,
    before: Snapshot,
}

/// 変形を始めた結果。
#[derive(Clone, Debug)]
pub struct Begun {
    /// 変形の状態。
    pub session: TransformSession,
    /// 選択の中を消したレイヤーの画像（変形のあいだ文書に置く）。
    pub layer: RgbaImage,
    /// 消した矩形と、その前の画素。
    pub edit: SelectionEdit,
}

/// 確定の結果。
#[derive(Clone, Debug)]
pub struct Committed {
    /// 確定したレイヤーの画像。
    pub image: RgbaImage,
    /// 変わりうる範囲の、変形を始める前と確定したあとの画素。
    pub undo: UndoPatch,
}

/// 1 回の変形（始めてから確定・取り消しまで）。
#[derive(Clone, Debug)]
pub struct TransformSession {
    mode: Mode,
    options: TransformOptions,
    selection: Selection,
    original: RgbaImage,
    source: PremulImage,
    source_rect: Rect,
    points: Vec<Pt>,
    cols: usize,
    rows: usize,
    reference: Vec<Pt>,
    initial: Vec<Pt>,
    drag: Option<Drag>,
    undo: Vec<Snapshot>,
    redo: Vec<Snapshot>,
    labels: Option<Labels>,
    quality: bool,
}

impl TransformSession {
    /// 変形を始める（`begin_selection_transform`）。
    ///
    /// `selection` が無ければ、描いてある画素の外接矩形を選ぶ（`start_wire_transform` の
    /// 自動選択）。切り出せなければ None。
    pub fn begin(
        layer: &RgbaImage,
        selection: Option<&Selection>,
        mode: Mode,
        options: TransformOptions,
    ) -> Result<Option<Begun>, Error> {
        let selection = match selection {
            Some(selection) => selection.clone(),
            None => match used_area(layer) {
                Some(selection) => selection,
                None => return Ok(None),
            },
        };
        let mut lifted = layer.clone();
        let Some(result) = lift_selection(&mut lifted, &selection)? else {
            return Ok(None);
        };
        let rect = result.edit.rect;
        let source = PremulImage::from_image_rect(&result.source, full(&result.source))?;
        let rect_f = (
            rect.x as f64,
            rect.y as f64,
            rect.width as f64,
            rect.height as f64,
        );
        let cols = options.mesh_cols.clamp(2, 12);
        let rows = options.mesh_rows.clamp(2, 12);
        let (points, reference) = if mode == Mode::Mesh {
            let points =
                mesh_reference_points(rect_f, cols, rows, Some((source.width(), source.height())));
            (points.clone(), points)
        } else {
            let (l, t) = (rect.x as f64, rect.y as f64);
            let (r, b) = ((rect.x + rect.width) as f64, (rect.y + rect.height) as f64);
            (vec![(l, t), (r, t), (r, b), (l, b)], Vec::new())
        };
        let mut quality = options.quality;
        let labels = if quality {
            let labels = prepare_labels(&source, &options.line_colors);
            quality = labels.is_some();
            labels
        } else {
            None
        };
        let session = TransformSession {
            mode,
            options,
            selection,
            original: layer.clone(),
            source,
            source_rect: rect,
            initial: points.clone(),
            points,
            cols,
            rows,
            reference,
            drag: None,
            undo: Vec::new(),
            redo: Vec::new(),
            labels,
            quality,
        };
        Ok(Some(Begun {
            session,
            layer: lifted,
            edit: result.edit,
        }))
    }

    /// 変形の種類。
    pub fn mode(&self) -> Mode {
        self.mode
    }

    /// クオリティ変形で描くか（色が多すぎて準備できなければ偽）。
    pub fn quality(&self) -> bool {
        self.quality
    }

    /// 変形の点（四角形は左上から時計回りの 4 点、メッシュは行優先の格子）。
    pub fn points(&self) -> &[Pt] {
        &self.points
    }

    /// 点をまとめて置き換える（数値の入力やトゥイーンの形）。数が合わなければ偽。
    /// 変える前の形を履歴に積む。
    pub fn set_points(&mut self, points: Vec<Pt>) -> bool {
        if points.len() != self.points.len() {
            return false;
        }
        let before = self.snapshot();
        self.points = points;
        self.record(before);
        true
    }

    /// メッシュの (横, 縦) の点の数。
    pub fn mesh_size(&self) -> (usize, usize) {
        (self.cols, self.rows)
    }

    /// 切り出した矩形（`transform_source_rect`）。
    pub fn source_rect(&self) -> Rect {
        self.source_rect
    }

    /// 変形の元の画像（乗算済み、選択の外は透明）。
    pub fn source(&self) -> &PremulImage {
        &self.source
    }

    /// 線の太さのしきい値を変える（`set_transform_line_threshold`）。
    pub fn set_line_threshold(&mut self, value: u8) {
        self.options.line_threshold = value.clamp(1, 254);
    }

    /// メッシュの基準格子（`transform_mesh_reference_points`）。メッシュでなければ空。
    pub fn mesh_reference(&self) -> &[Pt] {
        &self.reference
    }

    /// クオリティ変形と実線の色を変える（`set_transform_quality`・
    /// `set_transform_line_colors`）。今の形のまま、元の画像から色の番号を作り直す。
    /// 色が多すぎて準備できなければ通常変形で描く。クオリティで描くかを返す。
    pub fn set_quality(&mut self, quality: bool, line_colors: Vec<Rgb>) -> bool {
        self.options.quality = quality;
        self.options.line_colors = line_colors;
        self.labels = if quality {
            prepare_labels(&self.source, &self.options.line_colors)
        } else {
            None
        };
        self.quality = self.labels.is_some();
        self.quality
    }

    /// トゥイーンの各コマの画像（`TweenController.commit_transform`）。
    ///
    /// `start` は始めたときの点、今の点が最後の形。コマ `index` の変形の割合は
    /// `index / (exposure - 1)`（逆生成は 1 から引く）。割合 0 のコマは変形を始める前の
    /// 画像をそのまま使う。点の数が合わなければ None。
    pub fn tween_images(
        &self,
        start: &[Pt],
        exposure: usize,
        reverse: bool,
    ) -> Result<Option<Vec<RgbaImage>>, Error> {
        if start.is_empty() || start.len() != self.points.len() {
            return Ok(None);
        }
        let exposure = exposure.max(2);
        let mut images = Vec::with_capacity(exposure);
        let mut step = self.clone();
        for index in 0..exposure {
            let timeline = index as f64 / (exposure - 1) as f64;
            let ratio = if reverse { 1.0 - timeline } else { timeline };
            if ratio <= 1e-9 {
                images.push(self.original.clone());
                continue;
            }
            step.points = start
                .iter()
                .zip(&self.points)
                .map(|(s, f)| (s.0 + (f.0 - s.0) * ratio, s.1 + (f.1 - s.1) * ratio))
                .collect();
            images.push(step.apply_to(&self.original)?);
        }
        Ok(Some(images))
    }

    fn shape(&self) -> Shape<'_> {
        Shape {
            mesh: self.mode == Mode::Mesh,
            points: &self.points,
            reference: &self.reference,
            cols: self.cols,
            rows: self.rows,
        }
    }

    /// 今の形（`TransformUndo.capture`）。
    pub fn snapshot(&self) -> Snapshot {
        Snapshot {
            points: self.points.clone(),
            cols: self.cols,
            rows: self.rows,
            reference: self.reference.clone(),
        }
    }

    fn apply(&mut self, snapshot: Snapshot) {
        self.points = snapshot.points;
        self.cols = snapshot.cols;
        self.rows = snapshot.rows;
        self.reference = snapshot.reference;
        self.drag = None;
    }

    fn record(&mut self, before: Snapshot) -> bool {
        if before != self.snapshot() {
            self.undo.push(before);
            self.redo.clear();
            true
        } else {
            false
        }
    }

    /// 形の履歴を 1 つ戻す。戻せなければ偽。
    pub fn undo(&mut self) -> bool {
        let Some(before) = self.undo.pop() else {
            return false;
        };
        self.redo.push(self.snapshot());
        self.apply(before);
        true
    }

    /// 戻した形の履歴をやり直す。やり直せなければ偽。
    pub fn redo(&mut self) -> bool {
        let Some(after) = self.redo.pop() else {
            return false;
        };
        self.undo.push(self.snapshot());
        self.apply(after);
        true
    }

    /// 戻せる形の数（ヒストリーの「変形操作」の件数）。
    pub fn history_len(&self) -> usize {
        self.undo.len()
    }

    /// 外周（中のドラッグで動かせる範囲・点線）。
    pub fn outer_polygon(&self) -> Vec<Pt> {
        outer_polygon(
            self.mode == Mode::Mesh,
            &self.points,
            &self.reference,
            self.cols,
            self.rows,
        )
    }

    /// 回転のつまみ（`zoom` は画面の拡大率）。
    pub fn rotation_handle(&self, zoom: f64) -> Option<Pt> {
        rotation_handle(self.mode == Mode::Mesh, &self.points, self.cols, zoom)
    }

    /// 選択の輪郭を今の形へ写す（`transformed_selection_outline`）。
    pub fn transformed_outline(&self, outline: &[Pt]) -> Vec<Pt> {
        let r = self.source_rect;
        geometry::transformed_outline(
            self.mode == Mode::Mesh,
            &self.points,
            &self.reference,
            self.cols,
            self.rows,
            (r.x as f64, r.y as f64, r.width as f64, r.height as f64),
            outline,
        )
    }

    /// 押した所でドラッグを始める（`begin_transform_drag`）。何もつかめなければ None。
    pub fn begin_drag(&mut self, point: Pt, zoom: f64) -> Option<DragKind> {
        let before = self.snapshot();
        let radius = 16.0 / zoom.max(0.01);
        let kind = if self
            .rotation_handle(zoom)
            .is_some_and(|h| (h.0 - point.0).powi(2) + (h.1 - point.1).powi(2) <= radius * radius)
        {
            Some(DragKind::Rotate)
        } else if let Some(handle) = nearest_handle(&self.points, point, zoom) {
            Some(DragKind::Handle(handle))
        } else if polygon_contains(&self.outer_polygon(), point) {
            Some(DragKind::Move)
        } else {
            None
        };
        self.drag = kind.map(|kind| Drag {
            kind,
            start: point,
            start_points: self.points.clone(),
            before,
        });
        kind
    }

    /// ドラッグで形を変える（`update_transform_drag`）。移動は 1 画素単位（#116）。
    pub fn update_drag(&mut self, point: Pt) {
        let Some(drag) = &self.drag else {
            return;
        };
        let start = &drag.start_points;
        match drag.kind {
            DragKind::Move => {
                let dx = (point.0 - drag.start.0).round();
                let dy = (point.1 - drag.start.1).round();
                self.points = start.iter().map(|p| (p.0 + dx, p.1 + dy)).collect();
            }
            DragKind::Rotate => {
                let c = center(start);
                let a0 = (drag.start.1 - c.1).atan2(drag.start.0 - c.0);
                let a1 = (point.1 - c.1).atan2(point.0 - c.0);
                self.points = rotate_points(start, c, a1 - a0);
            }
            DragKind::Handle(index) => {
                if self.mode != Mode::Scale {
                    self.points[index] = point;
                } else {
                    let opposite = start[(index + 2) % 4];
                    let original = (start[index].0 - opposite.0, start[index].1 - opposite.1);
                    let current = (point.0 - opposite.0, point.1 - opposite.1);
                    let den = original.0 * original.0 + original.1 * original.1;
                    let mut scale = if den > 1e-8 {
                        (current.0 * original.0 + current.1 * original.1) / den
                    } else {
                        1.0
                    };
                    if scale.abs() < 0.02 {
                        scale = if scale >= 0.0 { 0.02 } else { -0.02 };
                    }
                    self.points = start
                        .iter()
                        .map(|p| {
                            (
                                opposite.0 + (p.0 - opposite.0) * scale,
                                opposite.1 + (p.1 - opposite.1) * scale,
                            )
                        })
                        .collect();
                }
            }
        }
    }

    /// ドラッグを終える（`end_transform_drag`）。形が変わって履歴を積んだら真。
    pub fn end_drag(&mut self) -> bool {
        match self.drag.take() {
            Some(drag) => self.record(drag.before),
            None => false,
        }
    }

    /// 重心のまわりに `degrees` 度回す（`rotate_selection_transform`、±90 のボタン）。
    pub fn rotate(&mut self, degrees: f64) {
        if self.points.is_empty() {
            return;
        }
        let before = self.snapshot();
        let c = center(&self.points);
        self.points = rotate_points(&self.points, c, degrees.to_radians());
        self.record(before);
    }

    /// 左右（`horizontal`）か上下に反転する。四角形は点の並びを入れ替えずに鏡に写す
    /// （確定すると画素が鏡に写る）。メッシュは格子ごと写す。
    pub fn flip(&mut self, horizontal: bool) {
        if self.points.is_empty() {
            return;
        }
        let before = self.snapshot();
        let c = center(&self.points);
        self.points = self
            .points
            .iter()
            .map(|p| {
                if horizontal {
                    (2.0 * c.0 - p.0, p.1)
                } else {
                    (p.0, 2.0 * c.1 - p.1)
                }
            })
            .collect();
        self.record(before);
    }

    /// メッシュの格子数を変える（`set_transform_mesh_grid`）。今の格子を引き直す。
    pub fn set_mesh_grid(&mut self, cols: usize, rows: usize) {
        let cols = cols.clamp(2, 12);
        let rows = rows.clamp(2, 12);
        self.options.mesh_cols = cols;
        self.options.mesh_rows = rows;
        if self.mode != Mode::Mesh {
            return;
        }
        let before = self.snapshot();
        let r = self.source_rect;
        let rect = (r.x as f64, r.y as f64, r.width as f64, r.height as f64);
        if self.cols * self.rows == self.points.len() && self.cols >= 2 && self.rows >= 2 {
            self.points = resample_mesh(&self.points, self.cols, self.rows, cols, rows);
        } else {
            self.points = regular_grid_points(rect, cols, rows);
        }
        self.reference = mesh_reference_points(
            rect,
            cols,
            rows,
            Some((self.source.width(), self.source.height())),
        );
        self.initial = self.reference.clone();
        self.cols = cols;
        self.rows = rows;
        self.record(before);
    }

    /// 移動だけの形なら、その整数のずれ（#116：描き直さずにずらして貼る）。
    fn integer_offset(&self) -> Option<(i64, i64)> {
        let initial = if self.mode == Mode::Mesh {
            &self.reference
        } else {
            &self.initial
        };
        if initial.len() != self.points.len() || initial.is_empty() {
            return None;
        }
        let dx = self.points[0].0 - initial[0].0;
        let dy = self.points[0].1 - initial[0].1;
        if dx.fract() != 0.0 || dy.fract() != 0.0 {
            return None;
        }
        self.points
            .iter()
            .zip(initial)
            .all(|(p, q)| p.0 - q.0 == dx && p.1 - q.1 == dy)
            .then_some((dx as i64, dy as i64))
    }

    fn render(
        &self,
        source: &PremulImage,
        labels: Option<&Labels>,
        size: (usize, usize),
    ) -> Result<Rendered, Error> {
        if let Some((dx, dy)) = self.integer_offset() {
            let x = self.source_rect.x as i64 + dx;
            let y = self.source_rect.y as i64 + dy;
            let pixels = match labels {
                // クオリティ変形は色を番号に置き換えて描くので、白は透明に、半透明は
                // 不透明になる。ずらすだけでも同じ規則の画素にする。
                Some(labels) => labels_pixels(labels)?,
                None => source.clone(),
            };
            return clip_rendered(pixels, x, y, size);
        }
        if let Some(labels) = labels {
            if let Some(rendered) = render_quality(
                labels,
                &self.shape(),
                size,
                self.options.line_threshold.clamp(1, 254),
            )? {
                return Ok(rendered);
            }
        }
        render_plain(source, &self.shape(), size)
    }

    /// 今の形で描いた画素（`transform_preview_image`）。確定と同じ画素になる。
    pub fn preview(&self) -> Result<Rendered, Error> {
        let size = (self.original.width(), self.original.height());
        let labels = if self.quality {
            self.labels.as_ref()
        } else {
            None
        };
        self.render(&self.source, labels, size)
    }

    /// `original`（変形を始める前のレイヤー）に今の形で変形を掛けた画像
    /// （`commit_selection_transform` の 1 コマ分）。すべてのコマに掛けるときは、
    /// コマごとの元の画像を渡す。今のコマ以外は元の画像から切り出し直す。
    pub fn apply_to(&self, original: &RgbaImage) -> Result<RgbaImage, Error> {
        self.apply_with_rect(original).map(|(image, _)| image)
    }

    fn apply_with_rect(&self, original: &RgbaImage) -> Result<(RgbaImage, Rect), Error> {
        let size = (original.width(), original.height());
        let same = original.generation() == self.original.generation()
            && original.width() == self.original.width()
            && original.height() == self.original.height();
        let rendered = if same {
            self.preview()?
        } else {
            let Some(rect) = self.source_rect.clip_within(size) else {
                return Ok((original.clone(), self.source_rect));
            };
            let copy = crate::selection::masked_copy(original, Some(&self.selection), rect)?;
            let source = PremulImage::from_image_rect(&copy, full(&copy))?;
            let labels = if self.quality {
                prepare_labels(&source, &self.options.line_colors)
            } else {
                None
            };
            self.render(&source, labels.as_ref(), size)?
        };
        let mask = self.selection.mask(size.0, size.1, 0, 0);
        let mut rgba = original.rgba().to_vec();
        let mut changed = mask.bounds();
        for (pixel, &selected) in rgba.chunks_exact_mut(4).zip(mask.bits()) {
            if selected != 0 {
                pixel.fill(0);
            }
        }
        let w = rendered.window;
        if w.width > 0 && w.height > 0 {
            changed = Some(match changed {
                Some(c) => union(c, w),
                None => w,
            });
        }
        for row in 0..w.height {
            for column in 0..w.width {
                let src = rendered.pixels.pixel(column, row);
                if src == [0; 4] {
                    continue;
                }
                let at = ((w.y + row) * size.0 + w.x + column) * 4;
                let pixel = &mut rgba[at..at + 4];
                let mut dst = premultiply([pixel[0], pixel[1], pixel[2], pixel[3]]);
                crate::compose::pixel::blend(&mut dst, src, 255);
                pixel.copy_from_slice(&unpremultiply(dst));
            }
        }
        let rect = changed.unwrap_or(Rect {
            x: 0,
            y: 0,
            width: 0,
            height: 0,
        });
        Ok((RgbaImage::from_rgba(size.0, size.1, &rgba)?, rect))
    }

    /// 確定する（`commit_selection_transform` の今のコマ）。
    pub fn commit(self) -> Result<Committed, Error> {
        let (image, rect) = self.apply_with_rect(&self.original)?;
        let before = self.original.copy_rgba_rect(rect)?;
        let after = image.copy_rgba_rect(rect)?;
        Ok(Committed {
            image,
            undo: UndoPatch {
                rect,
                before,
                after,
            },
        })
    }

    /// 取り消す（`cancel_selection_transform`）。変形を始める前のレイヤーの画像を返す。
    pub fn cancel(self) -> RgbaImage {
        self.original
    }
}

/// トゥイーンのキーのセルに持たせる設定（`tween_groups.make_spec`）。
#[allow(clippy::too_many_arguments)]
pub fn tween_spec(
    id: &str,
    length: usize,
    mode: Mode,
    reverse: bool,
    start: &[Pt],
    final_points: &[Pt],
    mesh: (usize, usize),
    mesh_reference: &[Pt],
) -> serde_json::Value {
    let points = |points: &[Pt]| -> Vec<[f64; 2]> { points.iter().map(|p| [p.0, p.1]).collect() };
    serde_json::json!({
        "id": id,
        "length": length,
        "mode": if mode == Mode::Mesh { "mesh" } else { "free" },
        "reverse": reverse,
        "start_points": points(start),
        "final_points": points(final_points),
        "mesh_cols": mesh.0,
        "mesh_rows": mesh.1,
        "mesh_reference_points": points(mesh_reference),
    })
}

/// 番号の画像をそのまま色にした画素（白は透明）。
fn labels_pixels(labels: &Labels) -> Result<PremulImage, Error> {
    let mut pixels = Vec::with_capacity(labels.labels.len() * 4);
    for &label in &labels.labels {
        let color = labels.colors[usize::from(label).min(labels.colors.len() - 1)];
        let color = if color[..3] == [255; 3] {
            [0; 4]
        } else {
            color
        };
        pixels.extend_from_slice(&premultiply(color));
    }
    PremulImage::from_premultiplied(labels.width, labels.height, pixels)
}

fn full(image: &RgbaImage) -> Rect {
    Rect {
        x: 0,
        y: 0,
        width: image.width(),
        height: image.height(),
    }
}

fn union(a: Rect, b: Rect) -> Rect {
    let x = a.x.min(b.x);
    let y = a.y.min(b.y);
    let r = (a.x + a.width).max(b.x + b.width);
    let bottom = (a.y + a.height).max(b.y + b.height);
    Rect {
        x,
        y,
        width: r - x,
        height: bottom - y,
    }
}

/// 画素をそのまま (x, y) に置いた結果を、レイヤーの範囲で切る。
fn clip_rendered(
    pixels: PremulImage,
    x: i64,
    y: i64,
    size: (usize, usize),
) -> Result<Rendered, Error> {
    let x0 = x.max(0);
    let y0 = y.max(0);
    let x1 = (x + pixels.width() as i64).min(size.0 as i64);
    let y1 = (y + pixels.height() as i64).min(size.1 as i64);
    if x1 <= x0 || y1 <= y0 {
        return Ok(Rendered {
            window: Rect {
                x: 0,
                y: 0,
                width: 0,
                height: 0,
            },
            pixels: PremulImage::transparent(0, 0)?,
        });
    }
    let crop = Rect {
        x: (x0 - x) as usize,
        y: (y0 - y) as usize,
        width: (x1 - x0) as usize,
        height: (y1 - y0) as usize,
    };
    Ok(Rendered {
        window: Rect {
            x: x0 as usize,
            y: y0 as usize,
            width: crop.width,
            height: crop.height,
        },
        pixels: pixels.crop(crop)?,
    })
}

trait ClipWithin {
    fn clip_within(&self, size: (usize, usize)) -> Option<Rect>;
}

impl ClipWithin for Rect {
    fn clip_within(&self, size: (usize, usize)) -> Option<Rect> {
        let x1 = (self.x + self.width).min(size.0);
        let y1 = (self.y + self.height).min(size.1);
        (x1 > self.x && y1 > self.y).then_some(Rect {
            x: self.x,
            y: self.y,
            width: x1 - self.x,
            height: y1 - self.y,
        })
    }
}
