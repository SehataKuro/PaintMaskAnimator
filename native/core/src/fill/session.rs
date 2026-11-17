//! numpy の `_prepare_bucket` と BucketSession の画像・前処理キャッシュ。
use super::line_ends::{extend_line_ends, Strip};
use super::region::{compute_fill_region, FillOptions, FillOutcome};
use super::target::{fill_target_mask, TargetColor};
use crate::regions::{dilate, erode};
use crate::thin::thin;
use std::collections::VecDeque;

const PREPARED_LIMIT: usize = 4;
const THIN_ITERATIONS: usize = 8;
const TRACE_LENGTH: usize = 12;
/// Python seed_category と同じ色種別。None は透明または純白。
pub type Category = Option<[u8; 3]>;
/// 半開の外接矩形。
pub type Bounds = (usize, usize, usize, usize);
/// 前処理と領域決定に使う設定。
pub struct PrepareOptions {
    pub target_color: TargetColor,
    pub extend_lines: bool,
    pub extend_length: usize,
    pub close_gap: bool,
    pub gap_width: usize,
    pub fill: FillOptions,
}
#[derive(Default)]
/// ドラッグ中の帯の画素が完全一致した場合だけ細線化を再利用する。
pub struct LineCache {
    entries: Vec<(Bounds, Vec<u8>, Vec<u8>)>,
}
impl LineCache {
    /// 保持する画素と骨格のバイト数を返す。
    pub fn nbytes(&self) -> usize {
        self.entries.iter().map(|(_, p, s)| p.len() + s.len()).sum()
    }
    /// numpy extend_line_ends と同じ延長画素を返す。不正寸法は Err。
    pub fn extend(
        &mut self,
        boundary: &[u8],
        w: usize,
        h: usize,
        length: usize,
    ) -> Result<Vec<u8>, String> {
        if w == 0 || h == 0 || w.checked_mul(h) != Some(boundary.len()) {
            return Err("invalid line dimensions".into());
        }
        let length = length.max(1);
        let band = length.saturating_add(TRACE_LENGTH + THIN_ITERATIONS + 2);
        let boxes = [
            (0, 0, w, h.min(band)),
            (0, h.saturating_sub(band), w, h),
            (0, 0, w.min(band), h),
            (w.saturating_sub(band), 0, w, h),
        ];
        let mut owned = Vec::new();
        for b @ (x0, y0, x1, y1) in boxes {
            let mut part = Vec::with_capacity((x1 - x0) * (y1 - y0));
            for y in y0..y1 {
                part.extend_from_slice(&boundary[y * w + x0..y * w + x1]);
            }
            if !part.iter().any(|v| *v != 0) {
                continue;
            }
            let cached = self
                .entries
                .iter()
                .find(|(key, p, _)| *key == b && *p == part);
            let skeleton = if let Some((_, _, s)) = cached {
                s.clone()
            } else {
                let s = thin(&part, x1 - x0, y1 - y0, THIN_ITERATIONS);
                if let Some(i) = self.entries.iter().position(|(key, _, _)| *key == b) {
                    self.entries.remove(i);
                } else if self.entries.len() >= PREPARED_LIMIT {
                    self.entries.clear();
                }
                self.entries.push((b, part, s.clone()));
                s
            };
            owned.push((b, skeleton));
        }
        let strips: Vec<_> = owned
            .iter()
            .map(|((x0, y0, x1, y1), s)| Strip {
                x0: *x0,
                y0: *y0,
                x1: *x1,
                y1: *y1,
                skeleton: s,
            })
            .collect();
        Ok(extend_line_ends(boundary, w, h, &strips, length))
    }
}
/// Python `_prepare_bucket` と一致する 0/1 マスク。
pub struct Prepared<M = Vec<u8>> {
    pub target: M,
    pub passable: Option<M>,
    pub gap: Option<M>,
    index: Option<LabelIndex<M>>,
    uses: usize,
}
/// 番号と矩形を共有可能な bytes に保持する CPU 索引。
pub struct LabelIndex<M> {
    pub labels: M,
    pub boxes: M,
}
impl<M: std::ops::Deref<Target = [u8]>> LabelIndex<M> {
    fn new(pass: &[u8], w: usize, h: usize, store: fn(Vec<u8>) -> M) -> Self {
        let (labels, boxes) = index_runs(pass, w, h);
        Self {
            labels: store(labels),
            boxes: store(
                boxes
                    .into_iter()
                    .flat_map(|(a, b, c, d)| [a, b, c, d])
                    .flat_map(usize::to_ne_bytes)
                    .collect(),
            ),
        }
    }
    fn label(&self, pixel: usize) -> u32 {
        u32::from_ne_bytes(
            self.labels[pixel * 4..pixel * 4 + 4]
                .try_into()
                .expect("label"),
        )
    }
    fn bounds(&self, label: usize) -> Bounds {
        let n = std::mem::size_of::<usize>();
        let at = |i| {
            usize::from_ne_bytes(
                self.boxes[(label * 4 + i) * n..(label * 4 + i + 1) * n]
                    .try_into()
                    .expect("bounds"),
            )
        };
        (at(0), at(1), at(2), at(3))
    }
}
/// Python と同じ理由・矩形内だけの領域・開始色を返す。
#[derive(Clone, Debug, PartialEq)]
pub struct LocalOutcome {
    pub reason: &'static str,
    pub bounds: Option<Bounds>,
    pub region: Option<Vec<u8>>,
    pub category: Category,
}
/// 不変 RGBA と最大4色の FIFO 前処理を所有するバケツセッション。
pub struct BucketState<R: AsRef<[u8]>, M = Vec<u8>> {
    rgba: R,
    width: usize,
    height: usize,
    options: PrepareOptions,
    background: Option<M>,
    prepared: VecDeque<(Category, Prepared<M>)>,
    store: fn(Vec<u8>) -> M,
    share: fn(&M) -> M,
    indexed: bool,
    pub line_cache: LineCache,
}
impl<R: AsRef<[u8]>> BucketState<R> {
    /// Vec を保持する、Python に依存しない既定のセッション。
    pub fn new(
        rgba: R,
        width: usize,
        height: usize,
        options: PrepareOptions,
        indexed: bool,
    ) -> Result<Self, String> {
        Self::with_storage(rgba, width, height, options, indexed, |v| v, Vec::clone)
    }
}
impl<R: AsRef<[u8]>, M: std::ops::Deref<Target = [u8]>> BucketState<R, M> {
    /// RGBA を所有するセッションを作る。不正な寸法・長さは Err。
    pub fn with_storage(
        rgba: R,
        width: usize,
        height: usize,
        options: PrepareOptions,
        indexed: bool,
        store: fn(Vec<u8>) -> M,
        share: fn(&M) -> M,
    ) -> Result<Self, String> {
        if width == 0
            || height == 0
            || width.checked_mul(height).and_then(|n| n.checked_mul(4)) != Some(rgba.as_ref().len())
        {
            return Err("invalid fill dimensions or RGBA length".into());
        }
        Ok(Self {
            rgba,
            store,
            share,
            width,
            height,
            options,
            background: None,
            prepared: VecDeque::new(),
            indexed,
            line_cache: LineCache::default(),
        })
    }
    /// Python seed_category と同じ色を返す。範囲外は Err。
    pub fn category(&self, x: usize, y: usize) -> Result<Category, String> {
        if x >= self.width || y >= self.height {
            return Err("invalid fill coordinates".into());
        }
        let p = &self.rgba.as_ref()[(y * self.width + x) * 4..][..4];
        Ok(if p[3] == 0 || p[..3] == [255; 3] {
            None
        } else {
            Some([p[0], p[1], p[2]])
        })
    }
    fn build(
        &mut self,
        category: Category,
        selection: Option<&[u8]>,
    ) -> Result<Prepared<M>, String> {
        let (w, h) = (self.width, self.height);
        let kind = match self.options.target_color {
            TargetColor::All(_) => TargetColor::All(category),
            ref other => other.clone(),
        };
        let mut target = fill_target_mask(self.rgba.as_ref(), w, h, kind);
        if let Some(s) = selection {
            for (t, s) in target.iter_mut().zip(s) {
                *t &= *s;
            }
        }
        let mut passable = None;
        let mut gap = None;
        let same_passable =
            self.options.fill.adjacent && !self.options.extend_lines && !self.options.close_gap;
        if self.options.fill.adjacent && !same_passable {
            let mut boundary: Vec<_> = target.iter().map(|t| u8::from(*t == 0)).collect();
            if self.options.extend_lines {
                let extension =
                    self.line_cache
                        .extend(&boundary, w, h, self.options.extend_length)?;
                if extension.iter().any(|v| *v != 0) {
                    for (b, e) in boundary.iter_mut().zip(&extension) {
                        *b |= *e;
                    }
                    gap = Some(extension);
                }
            }
            if self.options.close_gap {
                let radius = self.options.gap_width.max(1);
                let mut virtual_boundary = erode(&dilate(&boundary, w, h, radius), w, h, radius);
                for (v, b) in virtual_boundary.iter_mut().zip(&boundary) {
                    *v |= *b;
                }
                gap = Some(
                    virtual_boundary
                        .iter()
                        .zip(&target)
                        .map(|(v, t)| v & t)
                        .collect(),
                );
                boundary = virtual_boundary;
            }
            passable = Some(
                target
                    .iter()
                    .zip(boundary)
                    .map(|(t, b)| t & u8::from(b == 0))
                    .collect(),
            );
        }
        let target = if category.is_none()
            && selection.is_none()
            && matches!(self.options.target_color, TargetColor::All(_))
            && self.background.is_some()
        {
            (self.share)(self.background.as_ref().expect("background present"))
        } else {
            (self.store)(target)
        };
        let passable = if same_passable {
            Some((self.share)(&target))
        } else {
            passable.map(self.store)
        };
        Ok(Prepared {
            target,
            passable,
            gap: gap.map(self.store),
            index: None,
            uses: 0,
        })
    }
    /// 最大4色を古い順に保持する。5色目で最古の索引も破棄する。
    pub fn prepare(&mut self, category: Category) -> Result<&Prepared<M>, String> {
        self.prepare_for(category, None)
    }

    fn prepare_for(
        &mut self,
        category: Category,
        selection: Option<&[u8]>,
    ) -> Result<&Prepared<M>, String> {
        if !self.prepared.iter().any(|(c, _)| *c == category) {
            let p = self.build(category, selection)?;
            if self.prepared.len() >= PREPARED_LIMIT {
                self.prepared.pop_front();
            }
            self.prepared.push_back((category, p));
        }
        self.prepared
            .iter()
            .find(|(c, _)| *c == category)
            .map(|(_, p)| p)
            .ok_or("missing prepared category".into())
    }
    /// CPU 索引を作り、共有可能な不変ストレージで返す。
    pub fn index(&mut self, category: Category) -> Result<&LabelIndex<M>, String> {
        self.prepare(category)?;
        let (_, p) = self
            .prepared
            .iter_mut()
            .find(|(c, _)| *c == category)
            .ok_or("missing prepared category")?;
        if p.index.is_none() {
            let pass = p.passable.as_deref().ok_or("missing passable")?;
            p.index = Some(LabelIndex::new(pass, self.width, self.height, self.store));
        }
        p.index.as_ref().ok_or("missing index".into())
    }
    /// 背景マスクも同じ格納方式で共有する。
    pub fn background(&mut self) -> &M {
        if self.background.is_none() {
            self.background = Some(
                if matches!(self.options.target_color, TargetColor::All(_)) {
                    if let Some((_, p)) = self.prepared.iter().find(|(c, _)| c.is_none()) {
                        (self.share)(&p.target)
                    } else {
                        (self.store)(fill_target_mask(
                            self.rgba.as_ref(),
                            self.width,
                            self.height,
                            TargetColor::All(None),
                        ))
                    }
                } else {
                    (self.store)(fill_target_mask(
                        self.rgba.as_ref(),
                        self.width,
                        self.height,
                        TargetColor::All(None),
                    ))
                },
            );
        }
        self.background.as_ref().expect("background initialized")
    }
    /// 保持中の色を古い順に返す（Python 側の破棄を揃えるため）。
    pub fn categories(&self) -> Vec<Category> {
        self.prepared.iter().map(|(c, _)| *c).collect()
    }
    /// 所有する画像・前処理・索引・細線化キャッシュのバイト数を返す。
    pub fn nbytes(&self) -> usize {
        let mut buffers: Vec<&[u8]> = vec![self.rgba.as_ref()];
        if let Some(background) = &self.background {
            buffers.push(background);
        }
        for (_, p) in &self.prepared {
            buffers.push(&p.target);
            if let Some(passable) = &p.passable {
                buffers.push(passable);
            }
            if let Some(gap) = &p.gap {
                buffers.push(gap);
            }
            if let Some(index) = &p.index {
                buffers.extend([&*index.labels, &*index.boxes]);
            }
        }
        self.line_cache.nbytes()
            + buffers
                .iter()
                .enumerate()
                .filter(|(i, b)| {
                    !buffers[..*i]
                        .iter()
                        .any(|prior| prior.as_ptr() == b.as_ptr() && prior.len() == b.len())
                })
                .map(|(_, b)| b.len())
                .sum::<usize>()
    }

    /// キャッシュを借りて最終領域を求め、矩形内だけを返す。selection は単発用。
    pub fn compute(
        &mut self,
        x: usize,
        y: usize,
        selection: Option<&[u8]>,
    ) -> Result<LocalOutcome, String> {
        self.compute_impl(x, y, selection, false)
    }

    // 追跡専用の新規セッションで、全候補に同じ選択範囲を使う。
    pub(crate) fn compute_tracking(
        &mut self,
        x: usize,
        y: usize,
        selection: Option<&[u8]>,
    ) -> Result<LocalOutcome, String> {
        self.compute_impl(x, y, selection, true)
    }

    fn compute_impl(
        &mut self,
        x: usize,
        y: usize,
        selection: Option<&[u8]>,
        reuse_selection: bool,
    ) -> Result<LocalOutcome, String> {
        let category = self.category(x, y)?;
        let key = if reuse_selection && !matches!(self.options.target_color, TargetColor::All(_)) {
            None
        } else {
            category
        };
        if selection.is_some_and(|s| s.len() != self.width * self.height) {
            return Err("invalid selection length".into());
        }
        let seed = (y * self.width + x) * 4;
        let kind = match self.options.target_color {
            TargetColor::All(_) => TargetColor::All(category),
            ref other => other.clone(),
        };
        if fill_target_mask(&self.rgba.as_ref()[seed..seed + 4], 1, 1, kind).first() == Some(&0) {
            return Ok(LocalOutcome {
                reason: "not_target",
                bounds: None,
                region: None,
                category,
            });
        }
        if reuse_selection
            && self.options.fill.include_masks
            && self.options.fill.include_background_mask
            && self.background.is_none()
        {
            self.background();
        }
        let single = if selection.is_some() && !reuse_selection {
            Some(self.build(key, selection)?)
        } else {
            self.prepare_for(key, selection)?;
            None
        };
        if self.options.fill.include_masks
            && self.options.fill.include_background_mask
            && self.background.is_none()
        {
            self.background();
        }
        let p = if let Some(ref p) = single {
            p
        } else {
            let (_, p) = self
                .prepared
                .iter_mut()
                .find(|(c, _)| *c == key)
                .ok_or("missing prepared category")?;
            if self.indexed && p.uses > 0 && p.index.is_none() {
                if let Some(pass) = &p.passable {
                    p.index = Some(LabelIndex::new(pass, self.width, self.height, self.store));
                }
            }
            p.uses = p.uses.saturating_add(1);
            p
        };
        let o = &self.options.fill;
        let seed = y * self.width + x;
        if p.target[seed] == 0 {
            return Ok(LocalOutcome {
                reason: "not_target",
                bounds: None,
                region: None,
                category,
            });
        }
        if o.adjacent && !o.include_masks && o.enclosed == super::region::Enclosed::None {
            let pass = p.passable.as_deref().ok_or("missing passable")?;
            let (bounds, local) = if let Some(index) = &p.index {
                let label = index.label(seed);
                if label != 0 {
                    let b @ (x0, y0, x1, y1) = index.bounds(label as usize);
                    let mut local = Vec::with_capacity((x1 - x0) * (y1 - y0));
                    for y in y0..y1 {
                        for x in x0..x1 {
                            local.push(u8::from(index.label(y * self.width + x) == label));
                        }
                    }
                    (b, local)
                } else {
                    local_flood(pass, self.width, self.height, x, y)
                }
            } else {
                local_flood(pass, self.width, self.height, x, y)
            };
            let (x0, y0, x1, y1) = bounds;
            let open = selection.is_none()
                && o.require_closed
                && (x0 == 0 || y0 == 0 || x1 == self.width || y1 == self.height);
            // 仮想境界の回収が接していなければ、矩形内マスクを直接返せる。
            let touching_gap = p.gap.as_ref().is_some_and(|g| {
                for gy in y0.saturating_sub(1)..(y1 + 1).min(self.height) {
                    for gx in x0.saturating_sub(1)..(x1 + 1).min(self.width) {
                        if g[gy * self.width + gx] == 0 {
                            continue;
                        }
                        for ny in gy.saturating_sub(1).max(y0)..=(gy + 1).min(y1 - 1) {
                            for nx in gx.saturating_sub(1).max(x0)..=(gx + 1).min(x1 - 1) {
                                if local[(ny - y0) * (x1 - x0) + nx - x0] != 0 {
                                    return true;
                                }
                            }
                        }
                    }
                }
                false
            });
            let touching_island = o.island_max_width > 0 && {
                let mut found = false;
                for gy in y0.saturating_sub(1)..(y1 + 1).min(self.height) {
                    for gx in x0.saturating_sub(1)..(x1 + 1).min(self.width) {
                        if p.target[gy * self.width + gx] == 0 {
                            continue;
                        }
                        if gy >= y0
                            && gy < y1
                            && gx >= x0
                            && gx < x1
                            && local[(gy - y0) * (x1 - x0) + gx - x0] != 0
                        {
                            continue;
                        }
                        for ny in gy.saturating_sub(1).max(y0)..=(gy + 1).min(y1 - 1) {
                            for nx in gx.saturating_sub(1).max(x0)..=(gx + 1).min(x1 - 1) {
                                if local[(ny - y0) * (x1 - x0) + nx - x0] != 0 {
                                    found = true;
                                }
                            }
                        }
                    }
                }
                found
            };
            if open || (!touching_gap && !touching_island) {
                return Ok(LocalOutcome {
                    reason: if open { "open_region" } else { "ok" },
                    bounds: Some(bounds),
                    region: Some(local),
                    category,
                });
            }
        }
        let mut initial = None;
        if p.target[y * self.width + x] != 0 {
            if let Some(index) = &p.index {
                let label = index.label(y * self.width + x);
                if label != 0 {
                    let (x0, y0, x1, y1) = index.bounds(label as usize);
                    let mut region = vec![0; self.width * self.height];
                    for y in y0..y1 {
                        for x in x0..x1 {
                            region[y * self.width + x] =
                                u8::from(index.label(y * self.width + x) == label);
                        }
                    }
                    initial = Some(region);
                }
            }
        }
        let result = compute_fill_region(
            self.rgba.as_ref(),
            self.width,
            self.height,
            (x, y),
            &p.target,
            p.passable.as_deref(),
            p.gap.as_deref(),
            self.background.as_deref(),
            selection,
            initial.as_deref(),
            &self.options.fill,
        )?;
        let (reason, region, bounds) = match result {
            FillOutcome::NotTarget => ("not_target", None, None),
            FillOutcome::Empty => ("empty", None, None),
            FillOutcome::Open { region } => {
                let b = super::region::bounds(&region, self.width, self.height);
                ("open_region", Some(region), b)
            }
            FillOutcome::Ok { region, bounds } => ("ok", Some(region), Some(bounds)),
        };
        let region = region.and_then(|r| {
            bounds.map(|(x0, y0, x1, y1)| {
                let mut local = Vec::with_capacity((x1 - x0) * (y1 - y0));
                for y in y0..y1 {
                    local.extend_from_slice(&r[y * self.width + x0..y * self.width + x1]);
                }
                local
            })
        });
        Ok(LocalOutcome {
            reason,
            region,
            bounds,
            category,
        })
    }
}

// 全体マスクを返さず、到達したランと外接矩形から local を作る。
fn local_flood(pass: &[u8], w: usize, h: usize, sx: usize, sy: usize) -> (Bounds, Vec<u8>) {
    let mut visited = vec![0u8; pass.len()];
    let mut pending = vec![(sx, sy)];
    let mut runs = Vec::new();
    let (mut x0, mut y0, mut x1, mut y1) = (sx, sy, sx + 1, sy + 1);
    while let Some((x, y)) = pending.pop() {
        let row = y * w;
        if visited[row + x] != 0 {
            continue;
        }
        let mut left = x;
        let mut right = x + 1;
        while left > 0 && pass[row + left - 1] != 0 && visited[row + left - 1] == 0 {
            left -= 1;
        }
        while right < w && pass[row + right] != 0 && visited[row + right] == 0 {
            right += 1;
        }
        visited[row + left..row + right].fill(1);
        runs.push((y, left, right));
        x0 = x0.min(left);
        x1 = x1.max(right);
        y0 = y0.min(y);
        y1 = y1.max(y + 1);
        for ny in [y.wrapping_sub(1), y + 1] {
            if ny >= h {
                continue;
            }
            let mut nx = left;
            while nx < right {
                if pass[ny * w + nx] != 0 && visited[ny * w + nx] == 0 {
                    pending.push((nx, ny));
                    nx += 1;
                    while nx < right && pass[ny * w + nx] != 0 {
                        nx += 1;
                    }
                } else {
                    nx += 1;
                }
            }
        }
    }
    let mut local = vec![0; (x1 - x0) * (y1 - y0)];
    for (y, left, right) in runs {
        local[(y - y0) * (x1 - x0) + left - x0..(y - y0) * (x1 - x0) + right - x0].fill(1);
    }
    ((x0, y0, x1, y1), local)
}

// 画素単位の union-find の代わりに行ランを連結し、索引を一度だけ展開する。
fn index_runs(pass: &[u8], w: usize, h: usize) -> (Vec<u8>, Vec<Bounds>) {
    fn root(parent: &mut [usize], mut n: usize) -> usize {
        while parent[n] != n {
            parent[n] = parent[parent[n]];
            n = parent[n];
        }
        n
    }
    let mut runs: Vec<(usize, usize, usize)> = Vec::new();
    let mut parent = Vec::new();
    let mut previous = 0..0;
    for y in 0..h {
        let first = runs.len();
        let mut x = 0;
        let mut prev = previous.start;
        while x < w {
            if pass[y * w + x] == 0 {
                x += 1;
                continue;
            }
            let left = x;
            while x < w && pass[y * w + x] != 0 {
                x += 1;
            }
            let id = runs.len();
            parent.push(id);
            runs.push((y, left, x));
            while prev < previous.end && runs[prev].2 <= left {
                prev += 1;
            }
            let mut j = prev;
            while j < previous.end && runs[j].1 < x {
                let a = root(&mut parent, id);
                let b = root(&mut parent, j);
                if a != b {
                    parent[a.max(b)] = a.min(b);
                }
                j += 1;
            }
        }
        previous = first..runs.len();
    }
    let mut labels = vec![0; w * h * 4];
    let mut boxes = vec![(w, h, 0, 0)];
    let mut ids = vec![0; runs.len()];
    for (i, &(y, left, right)) in runs.iter().enumerate() {
        let r = root(&mut parent, i);
        if ids[r] == 0 {
            ids[r] = boxes.len() as u32;
            boxes.push((w, h, 0, 0));
        }
        let id = ids[r];
        for pixel in labels[(y * w + left) * 4..(y * w + right) * 4].chunks_exact_mut(4) {
            pixel.copy_from_slice(&id.to_ne_bytes());
        }
        let b = &mut boxes[id as usize];
        b.0 = b.0.min(left);
        b.1 = b.1.min(y);
        b.2 = b.2.max(right);
        b.3 = b.3.max(y + 1);
    }
    (labels, boxes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fill::region::Enclosed;
    fn options() -> PrepareOptions {
        PrepareOptions {
            target_color: TargetColor::All(None),
            extend_lines: false,
            extend_length: 4,
            close_gap: false,
            gap_width: 1,
            fill: FillOptions {
                adjacent: true,
                require_closed: false,
                island_max_width: 0,
                include_masks: false,
                include_background_mask: false,
                mask_colors: vec![],
                enclosed: Enclosed::None,
            },
        }
    }
    fn state(indexed: bool) -> BucketState<Vec<u8>> {
        let mut rgba = vec![255; 7 * 5 * 4];
        for y in 0..5 {
            rgba[(y * 7 + 3) * 4..(y * 7 + 3) * 4 + 3].fill(0);
        }
        BucketState::new(rgba, 7, 5, options(), indexed).expect("valid fixture")
    }
    #[test]
    fn fifo_does_not_refresh_on_hit() {
        let mut s = state(true);
        for r in 0..4 {
            s.prepare(Some([r, 0, 0])).unwrap();
        }
        s.prepare(Some([0, 0, 0])).unwrap();
        s.prepare(Some([4, 0, 0])).unwrap();
        assert_eq!(
            s.categories(),
            vec![
                Some([1, 0, 0]),
                Some([2, 0, 0]),
                Some([3, 0, 0]),
                Some([4, 0, 0])
            ]
        );
    }
    #[test]
    fn indexed_matches_scanline() {
        let mut a = state(false);
        let mut b = state(true);
        for (x, y) in [(1, 1), (5, 2), (3, 0)] {
            assert_eq!(a.compute(x, y, None), b.compute(x, y, None));
        }
    }
    #[test]
    fn selection_limits_single_preparation() {
        let mut s = state(false);
        let mut selection = vec![0; 35];
        selection[8] = 1;
        let result = s.compute(1, 1, Some(&selection)).unwrap();
        assert_eq!(result.bounds, Some((1, 1, 2, 2)));
        assert_eq!(result.region, Some(vec![1]));
        assert!(s.categories().is_empty());
    }
    #[test]
    fn invalid_dimensions_and_coordinates() {
        assert!(BucketState::new(vec![0; 3], 1, 1, options(), false).is_err());
        assert!(BucketState::new(vec![], usize::MAX, 2, options(), false).is_err());
        assert!(state(false).compute(7, 0, None).is_err());
        assert!(state(false).compute(0, 0, Some(&[])).is_err());
    }
    #[test]
    fn line_cache_reuses_and_rebuilds() {
        let mut cache = LineCache::default();
        let mut boundary = vec![0; 40 * 40];
        for y in 5..35 {
            boundary[y * 40 + 20] = 1;
        }
        let a = cache.extend(&boundary, 40, 40, 5).unwrap();
        let before = cache.nbytes();
        let pointers: Vec<_> = cache.entries.iter().map(|(_, _, s)| s.as_ptr()).collect();
        assert_eq!(a, cache.extend(&boundary, 40, 40, 5).unwrap());
        assert_eq!(before, cache.nbytes());
        assert_eq!(
            pointers,
            cache
                .entries
                .iter()
                .map(|(_, _, s)| s.as_ptr())
                .collect::<Vec<_>>()
        );
        boundary[10 * 40 + 20] = 0;
        let actual = cache.extend(&boundary, 40, 40, 5).unwrap();
        assert_eq!(
            actual,
            LineCache::default().extend(&boundary, 40, 40, 5).unwrap()
        );
        assert!(cache.entries.len() <= 4);
        assert!(cache.extend(&[], 40, 40, 5).is_err());
    }
    #[test]
    fn category_background_rules() {
        let mut s = state(false);
        assert_eq!(s.category(0, 0).unwrap(), None);
        assert_eq!(s.category(3, 0).unwrap(), Some([0, 0, 0]));
        s.rgba[3 * 4 + 3] = 0;
        assert_eq!(s.category(3, 0).unwrap(), None);
    }
    #[test]
    fn virtual_seed_recovers_without_mutation() {
        let pass = [0; 9];
        assert_eq!(local_flood(&pass, 3, 3, 1, 1), ((1, 1, 2, 2), vec![1]));
        assert_eq!(pass, [0; 9]);
    }
}
