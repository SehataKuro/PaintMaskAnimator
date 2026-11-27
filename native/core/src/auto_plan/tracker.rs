//! 確定した部位を覚えておき、線画の変形で再発見する（Python の
//! `auto_part_tracking.PartTracker` と、`native.flow_part_color`・`native.tiled_part_color`
//! の窓の作り方）。
use std::collections::{HashMap, VecDeque};
use std::rc::Rc;

use crate::flow_color::{self, Coordinates, Interior, Outline};
use crate::reference_check::Bounds;
use crate::regions::{dilate, erode};
use crate::tiled_color::{self, Rect, Tile};
use crate::tools::bucket::mask_bounds;
use crate::Error;

use super::alignment::Alignment;
use super::cell::{count, crop, Cell, Key};
use super::flow::{estimate_flow, path_coordinates, FlowPair, Path};
use super::session::Fill;

const MEMORY_BLOCK: usize = 128;
const MAX_DONORS: usize = 3;
const MAX_FLOW_PAIRS: usize = 64;
const MAX_TRACK_STEPS: usize = 12;
const MAX_TILE_STEPS: usize = 2;

/// 区画ごとの覚えた画素数。
type Coverage = HashMap<(usize, usize), usize>;

/// 計画の側から、セルの形（元の線画）と手本を受け取る。
pub(crate) trait Cells {
    /// `tracking_geometry(index)`：そのセルの部分分け（見本は元の線画）。
    fn geometry(&mut self, index: usize) -> Result<Rc<Cell>, Error>;
    /// 部位の手本にするセル（この回か前の回の塗り）。
    fn guide(&mut self, index: usize) -> Result<Rc<Cell>, Error>;
}

/// 確定した場所の索引。
pub(crate) struct PartTracker {
    width: usize,
    height: usize,
    coverage: Vec<(usize, Coverage)>,
    pairs: VecDeque<((Key, Key), Rc<FlowPair>)>,
    pub(crate) flow_solves: usize,
    pub(crate) matches: usize,
    pub(crate) long_matches: usize,
}

fn coverage(bounds: Bounds, mask: &[u8]) -> Coverage {
    let (x0, y0, x1, y1) = bounds;
    let lw = x1 - x0;
    let mut counts = Coverage::new();
    if x1 <= x0 || y1 <= y0 {
        return counts;
    }
    for ty in y0 / MEMORY_BLOCK..=(y1 - 1) / MEMORY_BLOCK {
        for tx in x0 / MEMORY_BLOCK..=(x1 - 1) / MEMORY_BLOCK {
            let ax0 = x0.max(tx * MEMORY_BLOCK);
            let ay0 = y0.max(ty * MEMORY_BLOCK);
            let ax1 = x1.min((tx + 1) * MEMORY_BLOCK);
            let ay1 = y1.min((ty + 1) * MEMORY_BLOCK);
            let mut n = 0;
            for y in ay0..ay1 {
                for x in ax0..ax1 {
                    n += usize::from(mask[(y - y0) * lw + (x - x0)] != 0);
                }
            }
            if n >= 8 {
                counts.insert((ty, tx), n);
            }
        }
    }
    counts
}

/// 画像内に切り詰めた窓（`_tiled_window`）。
fn window(
    (x0, y0, x1, y1): (i64, i64, i64, i64),
    (width, height): (usize, usize),
    margin: i64,
) -> Rect {
    let (w, h) = (width as i64, height as i64);
    let left = (x0 - margin).min(w).max(0);
    let right = (x1 + margin).min(w).max(0);
    let top = (y0 - margin).min(h).max(0);
    let bottom = (y1 + margin).min(h).max(0);
    (
        left as i32,
        top as i32,
        (right - left) as i32,
        (bottom - top) as i32,
    )
}

fn rect_bounds((left, top, w, h): Rect) -> Bounds {
    let (left, top) = (left.max(0) as usize, top.max(0) as usize);
    (left, top, left + w.max(0) as usize, top + h.max(0) as usize)
}

fn values_bytes(values: &[u32]) -> Vec<u8> {
    values
        .iter()
        .flat_map(|v| i64::from(*v).to_ne_bytes())
        .collect()
}

fn labels_bytes(labels: &[u32]) -> Vec<u8> {
    labels
        .iter()
        .flat_map(|v| (*v as i32).to_ne_bytes())
        .collect()
}

fn sizes_bytes(sizes: &[usize]) -> Vec<u8> {
    sizes
        .iter()
        .flat_map(|v| (*v as i64).to_ne_bytes())
        .collect()
}

fn axis_bytes(values: &[i64]) -> Vec<u8> {
    values.iter().flat_map(|v| v.to_ne_bytes()).collect()
}

/// `_flow_window`：画像内に収まる往路の座標を囲む窓。
fn flow_window(path: &Path, shape: (usize, usize)) -> Rect {
    let (width, height) = (shape.0 as i64, shape.1 as i64);
    let mut bounds: Option<(i64, i64, i64, i64)> = None;
    for i in 0..path.sy.len() {
        let (y, x) = (path.sy[i], path.sx[i]);
        if path.valid[i] == 0 || y < 0 || y >= height || x < 0 || x >= width {
            continue;
        }
        bounds = Some(match bounds {
            None => (x, y, x + 1, y + 1),
            Some(b) => (b.0.min(x), b.1.min(y), b.2.max(x + 1), b.3.max(y + 1)),
        });
    }
    window(bounds.unwrap_or((0, 0, 0, 0)), shape, 0)
}

impl PartTracker {
    pub(crate) fn new(width: usize, height: usize) -> Self {
        Self {
            width,
            height,
            coverage: Vec::new(),
            pairs: VecDeque::new(),
            flow_solves: 0,
            matches: 0,
            long_matches: 0,
        }
    }

    fn set_coverage(&mut self, index: usize, counts: Coverage) {
        match self.coverage.iter_mut().find(|(i, _)| *i == index) {
            Some(entry) => entry.1 = counts,
            None => self.coverage.push((index, counts)),
        }
    }

    /// `remember_fixed`：見本の塗ってある所を覚える。
    pub(crate) fn remember_fixed(&mut self, index: usize, guide: &Cell) {
        let known: Vec<u8> = (0..guide.values.len())
            .map(|i| {
                u8::from(
                    guide.values[i] != 0
                        && guide.black[i] == 0
                        && guide.primary[i] == 0
                        && guide.open[i] == 0
                        && guide.undecided[i] == 0,
                )
            })
            .collect();
        let counts = coverage((0, 0, self.width, self.height), &known);
        self.set_coverage(index, counts);
    }

    /// `remember`：このセルで色を決めた所を覚える。
    pub(crate) fn remember(&mut self, index: usize, paints: &[super::Paint]) {
        let mut counts = Coverage::new();
        for paint in paints {
            for (key, n) in coverage(paint.bounds, &paint.mask) {
                let entry = counts.entry(key).or_insert(0);
                *entry = (MEMORY_BLOCK * MEMORY_BLOCK).min(*entry + n);
            }
        }
        self.set_coverage(index, counts);
    }

    fn donors(&self, index: usize, filled: &Fill) -> Vec<usize> {
        let query = coverage(filled.rect(), filled.mask());
        let total: usize = query.values().sum();
        if total == 0 {
            return Vec::new();
        }
        let mut ranked: Vec<(f64, i64, i64, usize)> = Vec::new();
        for (other, known) in &self.coverage {
            if *other == index || other.abs_diff(index) > MAX_TRACK_STEPS || known.is_empty() {
                continue;
            }
            let mut overlap = 0;
            for (&(ty, tx), &n) in &query {
                let mut nearby = 0;
                for dy in -1i64..=1 {
                    for dx in -1i64..=1 {
                        let (y, x) = (ty as i64 + dy, tx as i64 + dx);
                        if y >= 0 && x >= 0 {
                            nearby =
                                nearby.max(*known.get(&(y as usize, x as usize)).unwrap_or(&0));
                        }
                    }
                }
                overlap += n.min(nearby);
            }
            if overlap as f64 >= 8f64.max(total as f64 * 0.2) {
                ranked.push((
                    overlap as f64 / total as f64,
                    -(other.abs_diff(index) as i64),
                    -(*other as i64),
                    *other,
                ));
            }
        }
        ranked.sort_by(|a, b| b.partial_cmp(a).expect("比べられる値"));
        ranked.into_iter().take(MAX_DONORS).map(|r| r.3).collect()
    }

    fn pair(&mut self, target: &Cell, source: &Cell) -> Option<Rc<FlowPair>> {
        let key = (target.geometry_key(), source.geometry_key());
        if let Some(position) = self.pairs.iter().position(|(k, _)| *k == key) {
            let entry = self.pairs.remove(position).expect("pair");
            let found = entry.1.clone();
            self.pairs.push_back(entry);
            return Some(found);
        }
        let reverse = (key.1, key.0);
        if let Some(position) = self.pairs.iter().position(|(k, _)| *k == reverse) {
            let entry = self.pairs.remove(position).expect("pair");
            let found = entry.1.clone();
            self.pairs.push_back(entry);
            return Some(Rc::new(FlowPair {
                forward: found.backward.clone(),
                backward: found.forward.clone(),
                x0: found.x0,
                y0: found.y0,
            }));
        }
        let shape = (self.width, self.height);
        if (target.width, target.height) != shape || (source.width, source.height) != shape {
            return None;
        }
        let first = mask_bounds(&target.boundary, self.width, self.height)?;
        let second = mask_bounds(&source.boundary, self.width, self.height)?;
        let x0 = first.0.min(second.0).saturating_sub(32);
        let y0 = first.1.min(second.1).saturating_sub(32);
        let x1 = self.width.min(first.2.max(second.2) + 32);
        let y1 = self.height.min(first.3.max(second.3) + 32);
        let window = (x0, y0, x1, y1);
        let left = crop(&target.boundary, self.width, window);
        let right = crop(&source.boundary, self.width, window);
        let (w, h) = (x1 - x0, y1 - y0);
        let pair = Rc::new(FlowPair {
            forward: Rc::new(estimate_flow(&left, &right, w, h)),
            backward: Rc::new(estimate_flow(&right, &left, w, h)),
            x0,
            y0,
        });
        self.flow_solves += 1;
        self.pairs.push_back((key, pair.clone()));
        if self.pairs.len() > MAX_FLOW_PAIRS {
            self.pairs.pop_front();
        }
        Some(pair)
    }

    fn path(
        &mut self,
        index: usize,
        guide: &Cell,
        cells: &mut dyn Cells,
    ) -> Result<Option<Vec<Rc<FlowPair>>>, Error> {
        let Some(to) = guide.index else {
            return Ok(None);
        };
        let mut pairs = Vec::new();
        let mut step = index;
        while step != to {
            let next = if to > index { step + 1 } else { step - 1 };
            let (a, b) = (cells.geometry(step)?, cells.geometry(next)?);
            match self.pair(&a, &b) {
                Some(pair) => pairs.push(pair),
                None => return Ok(None),
            }
            step = next;
        }
        Ok(Some(pairs))
    }

    /// `prime_motion`：隣のセルとの変形場を先に用意する。
    pub(crate) fn prime_motion(
        &mut self,
        index: usize,
        guides: &[Rc<Cell>],
        cells: &mut dyn Cells,
    ) -> Result<(), Error> {
        let adjacent = guides
            .iter()
            .find(|g| g.initial.is_some() && g.index.is_some_and(|i| i.abs_diff(index) == 1));
        if let Some(guide) = adjacent {
            self.path(index, guide, cells)?;
        }
        Ok(())
    }

    /// `color`：覚えた部位から、この部分の色を引き継ぐ。
    pub(crate) fn color(
        &mut self,
        index: usize,
        target: &Rc<Cell>,
        filled: &Fill,
        alignment: &mut Alignment,
        cells: &mut dyn Cells,
    ) -> Result<Option<[u8; 3]>, Error> {
        if filled.target.is_some() || filled.size() < 16 {
            return Ok(None);
        }
        if filled.bounds == Some((0, 0, self.width, self.height)) {
            return Ok(None);
        }
        let mut answers: Vec<(usize, [u8; 3])> = Vec::new();
        for other in self.donors(index, filled) {
            let guide = cells.guide(other)?;
            let rgb = match self.flow_color(index, target, filled, &guide, cells)? {
                Some(rgb) => Some(rgb),
                None => self.tiled_color(index, target, filled, &guide, alignment, cells)?,
            };
            if let Some(rgb) = rgb {
                answers.push((other, rgb));
            }
        }
        if answers.is_empty() || answers.iter().any(|(_, rgb)| *rgb != answers[0].1) {
            return Ok(None);
        }
        self.matches += 1;
        let nearest = answers
            .iter()
            .map(|(o, _)| o.abs_diff(index))
            .min()
            .unwrap_or(0);
        self.long_matches += usize::from(nearest > 4);
        Ok(Some(answers[0].1))
    }

    fn flow_color(
        &mut self,
        index: usize,
        target: &Cell,
        filled: &Fill,
        guide: &Cell,
        cells: &mut dyn Cells,
    ) -> Result<Option<[u8; 3]>, Error> {
        let Some(source) = guide.initial.as_deref() else {
            return Ok(None);
        };
        let Some(pairs) = self.path(index, guide, cells)? else {
            return Ok(None);
        };
        let shape = (self.width, self.height);
        let width = self.width;
        let (x0, y0, x1, y1) = filled.rect();
        let rect = window((x0 as i64, y0 as i64, x1 as i64, y1 as i64), shape, 1);
        let roi = rect_bounds(rect);
        let (aw, ah) = (roi.2 - roi.0, roi.3 - roi.1);
        let mut local = vec![0u8; aw * ah];
        let lw = x1 - x0;
        for (i, &v) in filled.mask().iter().enumerate() {
            local[(i / lw + y0 - roi.1) * aw + (i % lw + x0 - roi.0)] = v;
        }
        let dilated = dilate(&local, aw, ah, 1);
        let outline: Vec<u8> = dilated
            .iter()
            .zip(&local)
            .map(|(d, l)| d & (1 - l))
            .collect();
        let boundary = crop(&target.boundary, width, roi);
        let (mut oy, mut ox) = (Vec::new(), Vec::new());
        for (i, (&o, &b)) in outline.iter().zip(&boundary).enumerate() {
            if o & b != 0 {
                oy.push((i / aw + roi.1) as i64);
                ox.push((i % aw + roi.0) as i64);
            }
        }
        if oy.len() < 8 {
            return Ok(None);
        }
        let outline_path = path_coordinates(&pairs, shape, &oy, &ox)?;
        let source_outline = flow_window(&outline_path, shape);
        let outline_roi = rect_bounds(source_outline);
        let mut black_rect = source_outline;
        if source_outline.2 != 0 {
            let (bx, by, bw, bh) = source_outline;
            black_rect = window(
                (bx as i64, by as i64, (bx + bw) as i64, (by + bh) as i64),
                shape,
                1,
            );
        }
        let black_roi = rect_bounds(black_rect);
        let eroded = erode(&local, aw, ah, 2);
        let band: Vec<u8> = dilated
            .iter()
            .zip(&eroded)
            .map(|(d, e)| d & (1 - e))
            .collect();
        let part: Rect = (x0 as i32, y0 as i32, (x1 - x0) as i32, (y1 - y0) as i32);
        let axes = [
            axis_bytes(&outline_path.sy),
            axis_bytes(&outline_path.sx),
            axis_bytes(&outline_path.by),
            axis_bytes(&outline_path.bx),
        ];
        let target_black = crop(&target.black, width, roi);
        let wide = [
            crop(&source.wide_boundary, width, outline_roi),
            crop(&source.wide_primary, width, outline_roi),
        ];
        let source_black = crop(&source.black, width, black_roi);
        let matched = flow_color::outline_matches(
            &Outline {
                points: (rect, &outline),
                coordinates: Coordinates {
                    axes: [&axes[0], &axes[1], &axes[2], &axes[3]],
                    consistent: &outline_path.valid,
                },
                band: (rect, &band),
                target_boundary: &boundary,
                target_black: &target_black,
                source: source_outline,
                wide: [&wide[0], &wide[1]],
                source_black: (black_rect, &source_black),
            },
            part,
        )?;
        if !matched {
            return Ok(None);
        }
        let (mut ys, mut xs) = (Vec::new(), Vec::new());
        for (i, &v) in filled.mask().iter().enumerate() {
            if v != 0 {
                ys.push((i / lw + y0) as i64);
                xs.push((i % lw + x0) as i64);
            }
        }
        let small = ys.len() < 4096;
        let sizes = if small {
            sizes_bytes(&guide.regions.sizes)
        } else {
            Vec::new()
        };
        let inside_path = path_coordinates(&pairs, shape, &ys, &xs)?;
        let source_inside = flow_window(&inside_path, shape);
        let inside_roi = rect_bounds(source_inside);
        let labels = if small {
            labels_bytes(&crop(&guide.regions.labels, width, inside_roi))
        } else {
            Vec::new()
        };
        let excluded = [
            crop(&source.boundary, width, inside_roi),
            crop(&guide.open, width, inside_roi),
            crop(&guide.undecided, width, inside_roi),
            crop(&guide.black, width, inside_roi),
            crop(&guide.primary, width, inside_roi),
        ];
        let confirmed = guide.confirmed.as_ref().map(|c| crop(c, width, inside_roi));
        let values = values_bytes(&crop(&guide.values, width, inside_roi));
        let axes = [
            axis_bytes(&inside_path.sy),
            axis_bytes(&inside_path.sx),
            axis_bytes(&inside_path.by),
            axis_bytes(&inside_path.bx),
        ];
        flow_color::interior_color(
            &Interior {
                local: (part, filled.mask()),
                coordinates: Coordinates {
                    axes: [&axes[0], &axes[1], &axes[2], &axes[3]],
                    consistent: &inside_path.valid,
                },
                source: source_inside,
                excluded: [
                    &excluded[0],
                    &excluded[1],
                    &excluded[2],
                    &excluded[3],
                    &excluded[4],
                ],
                confirmed: confirmed.as_deref(),
                values: &values,
                labels: &labels,
            },
            &sizes,
        )
    }

    fn tiled_color(
        &mut self,
        index: usize,
        target: &Cell,
        filled: &Fill,
        guide: &Cell,
        alignment: &mut Alignment,
        cells: &mut dyn Cells,
    ) -> Result<Option<[u8; 3]>, Error> {
        let Some(source) = guide.initial.as_deref() else {
            return Ok(None);
        };
        let Some(to) = guide.index else {
            return Ok(None);
        };
        if to.abs_diff(index) > MAX_TILE_STEPS {
            return Ok(None); // 長い経路は共有した変形場で追う。
        }
        let mut steps = Vec::new();
        let mut step = index;
        while step != to {
            let next = if to > index { step + 1 } else { step - 1 };
            steps.push((cells.geometry(step)?, cells.geometry(next)?));
            step = next;
        }
        let shape = (self.width, self.height);
        if steps
            .iter()
            .any(|(a, b)| (a.width, a.height) != shape || (b.width, b.height) != shape)
        {
            return Ok(None);
        }
        let width = self.width;
        let (x0, y0, x1, y1) = filled.rect();
        let (lw, lh) = (x1 - x0, y1 - y0);
        let mask = filled.mask();
        let grown = dilate(mask, lw, lh, 1);
        let shrunk = erode(mask, lw, lh, 1);
        let outline: Vec<u8> = grown
            .iter()
            .zip(&shrunk)
            .map(|(g, s)| g & (1 - s))
            .collect();
        let size = count(mask);
        let tile_size = if size < 262144 { 64 } else { 96 };
        struct TileInput {
            tx: usize,
            ty: usize,
            sub: Vec<u8>,
            sw: usize,
            sh: usize,
            points: Vec<(usize, usize)>,
        }
        let mut tiles = Vec::new();
        for ty in (y0..y1).step_by(tile_size) {
            for tx in (x0..x1).step_by(tile_size) {
                let sub_bounds = (
                    tx - x0,
                    ty - y0,
                    lw.min(tx - x0 + tile_size),
                    lh.min(ty - y0 + tile_size),
                );
                let sub = crop(mask, lw, sub_bounds);
                if count(&sub) < 16 {
                    continue;
                }
                let (sw, sh) = (sub_bounds.2 - sub_bounds.0, sub_bounds.3 - sub_bounds.1);
                let ring = crop(&outline, lw, sub_bounds);
                let points: Vec<(usize, usize)> = ring
                    .iter()
                    .enumerate()
                    .filter(|(_, &v)| v != 0)
                    .map(|(i, _)| (i / sw, i % sw))
                    .filter(|&(oy, ox)| target.wide_boundary[(oy + ty) * width + ox + tx] != 0)
                    .collect();
                if points.len() < 4 {
                    continue;
                }
                tiles.push(TileInput {
                    tx,
                    ty,
                    sub,
                    sw,
                    sh,
                    points,
                });
            }
        }
        let initials: Vec<Bounds> = tiles
            .iter()
            .map(|t| (t.tx, t.ty, t.tx + t.sw, t.ty + t.sh))
            .collect();
        let paths = alignment.track_many(&steps, &initials, shape)?;
        // `native.tiled_part_color` の窓。
        struct Window {
            target: Rect,
            sub: Vec<u8>,
            outline: Vec<u8>,
            dest: Rect,
            shift: (i32, i32, i32, i32),
            target_black: (Rect, Vec<u8>),
            source_black: (Rect, Vec<u8>),
            wide: [Vec<u8>; 2],
            excluded: [Vec<u8>; 5],
            confirmed: Option<Vec<u8>>,
            values: Vec<u8>,
            labels: Vec<u8>,
        }
        let mut windows = Vec::new();
        for (tile, path) in tiles.iter().zip(&paths) {
            let Some((dy, dx, back_y, back_x)) = *path else {
                continue;
            };
            let limit = |v: i64| (-(1i64 << 31)..(1i64 << 31)).contains(&v);
            if ![dy, dx, back_y, back_x].into_iter().all(limit) {
                return Err(Error::Unsupported("区画のずれが大きすぎます".into()));
            }
            let (tx, ty) = (tile.tx as i64, tile.ty as i64);
            let (bx1, by1) = ((tile.tx + tile.sw) as i64, (tile.ty + tile.sh) as i64);
            let moved = (tx + dx, ty + dy, bx1 + dx, by1 + dy);
            let dest = window(moved, shape, 0);
            if dest.2 == 0 || dest.3 == 0 {
                continue;
            }
            let roi = rect_bounds(dest);
            let target_black = window((tx, ty, bx1, by1), shape, 1);
            let source_black = window(moved, shape, 1);
            let mut outline = vec![0u8; tile.sw * tile.sh];
            for &(oy, ox) in &tile.points {
                outline[oy * tile.sw + ox] = 1;
            }
            windows.push(Window {
                target: (
                    tile.tx as i32,
                    tile.ty as i32,
                    tile.sw as i32,
                    tile.sh as i32,
                ),
                sub: tile.sub.clone(),
                outline,
                dest,
                shift: (dy as i32, dx as i32, back_y as i32, back_x as i32),
                target_black: (
                    target_black,
                    crop(&target.black, width, rect_bounds(target_black)),
                ),
                source_black: (
                    source_black,
                    crop(&source.black, width, rect_bounds(source_black)),
                ),
                wide: [
                    crop(&source.wide_boundary, width, roi),
                    crop(&source.wide_primary, width, roi),
                ],
                excluded: [
                    crop(&source.boundary, width, roi),
                    crop(&guide.open, width, roi),
                    crop(&guide.undecided, width, roi),
                    crop(&guide.black, width, roi),
                    crop(&guide.primary, width, roi),
                ],
                confirmed: guide.confirmed.as_ref().map(|c| crop(c, width, roi)),
                values: values_bytes(&crop(&guide.values, width, roi)),
                labels: labels_bytes(&crop(&guide.regions.labels, width, roi)),
            });
        }
        if windows.is_empty() {
            return Ok(None);
        }
        let tiles: Vec<Tile<'_>> = windows
            .iter()
            .map(|w| Tile {
                target: w.target,
                filled: &w.sub,
                outline: &w.outline,
                source: w.dest,
                shift: w.shift,
                target_black: (w.target_black.0, &w.target_black.1),
                source_black: (w.source_black.0, &w.source_black.1),
                wide: [&w.wide[0], &w.wide[1]],
                excluded: [
                    &w.excluded[0],
                    &w.excluded[1],
                    &w.excluded[2],
                    &w.excluded[3],
                    &w.excluded[4],
                ],
                confirmed: w.confirmed.as_deref(),
                values: &w.values,
                labels: &w.labels,
            })
            .collect();
        let sizes: Vec<u64> = if size < 4096 {
            guide.regions.sizes.iter().map(|&s| s as u64).collect()
        } else {
            Vec::new()
        };
        let part: Rect = (x0 as i32, y0 as i32, lw as i32, lh as i32);
        tiled_color::tiled_color(&tiles, (part, mask), size, &sizes)
    }
}
