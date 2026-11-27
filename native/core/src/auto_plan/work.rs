//! 色とクリック位置を見本から選び、塗る範囲は通常のバケツで決める（Python の
//! `auto_bucket.BucketWork` と `auto_bucket.paint_cell`）。
use std::collections::{BTreeSet, HashMap, HashSet};
use std::rc::Rc;

use crate::auto_paint::refine_face_of;

use crate::reference_scan::Rgb;
use crate::reference_scan::{pack, to_rgb, Finding, FindingKind};
use crate::tools::bucket::mask_bounds;
use crate::Error;

use super::alignment::{Alignment, Halt};
use super::cell::{crop, is_primary, majority, paintable, Cell, Key, Shifted, GAP_VALUE};
use super::session::{AutoBucketOptions, Fill, ReferenceSessions, Session};
use super::trace::{self, FaceRules, FinishPalette, Rules};
use super::{CellPaint, Paint};

const FACE_MATCH_SHARE: f64 = 0.72;
const FACE_REVERSE_SHARE: f64 = 0.50;
const FACE_SAMPLE_SHARE: f64 = 0.55;
const TRACE_MATCH_SHARE: f64 = 0.75;
const TRACE_REVERSE_SHARE: f64 = 0.45;
const TRACE_RADIUS: i64 = 2;
const FACE_REFINE_RADIUS: i64 = 5;
const HINT_SHARE: f64 = 0.9;
const FILL_SHARE: f64 = 0.75;
const MAX_AREA_RATIO: f64 = 3.0;
const MIN_LINE_OVERLAP: f64 = 0.4;
const MIN_REGION_PIXELS: usize = 3;
const REPORT_MIN_PIXELS: usize = 30;

/// 塗る候補の部分 `(x, y, 値, バケツの結果)`。
pub(crate) struct Entry {
    pub x: usize,
    pub y: usize,
    pub value: u32,
    pub filled: Rc<Fill>,
}

/// 計画全体で共有する位置合わせと見本のバケツ。
pub(crate) struct Shared {
    pub alignment: Alignment,
    pub references: ReferenceSessions,
    pub options: AutoBucketOptions,
}

fn similar(size: usize, other: f64) -> bool {
    size as f64 / MAX_AREA_RATIO <= other && other <= size as f64 * MAX_AREA_RATIO
}

fn is_trace(filled: &Fill) -> bool {
    filled.target.is_some_and(is_primary)
}

/// `_paints`：含み塗りで広がった矩形も、通常バケツと同じ範囲へ切り詰める。
pub(crate) fn paints(filled: &Fill, rgb: Rgb) -> Vec<Paint> {
    let (x0, y0, x1, y1) = filled.rect();
    let (lw, lh) = (x1 - x0, y1 - y0);
    let Some(tight) = mask_bounds(filled.mask(), lw, lh) else {
        return Vec::new();
    };
    vec![Paint {
        rgb,
        bounds: (x0 + tight.0, y0 + tight.1, x0 + tight.2, y0 + tight.3),
        mask: crop(filled.mask(), lw, tight),
    }]
}

fn sample(
    guide: &Cell,
    filled: &Fill,
    (dy, dx): (i64, i64),
    ignore: u8,
    require: u8,
    by_label: bool,
) -> crate::auto_paint::Sample {
    guide
        .sampler()
        .sample(filled.runs(), dy, dx, ignore, require, by_label)
}

/// 1セル分の、回をまたいで使い回す一覧と判断。
pub(crate) struct BucketWork {
    pub target: Rc<Cell>,
    island_max_width: usize,
    entries: Option<Rc<Vec<Entry>>>,
    matches: HashMap<(usize, Key, usize, bool), Option<Rc<Fill>>>,
}

/// `resolve` に渡す状態。
struct Context<'a> {
    work: &'a mut BucketWork,
    shared: &'a mut Shared,
}

fn alignment_of<'a, 'b>(context: &'a mut Context<'b>) -> &'a mut Alignment {
    &mut context.shared.alignment
}

impl BucketWork {
    pub(crate) fn new(target: Rc<Cell>, options: &AutoBucketOptions) -> Self {
        Self {
            target,
            island_max_width: options.island_max_width,
            entries: None,
            matches: HashMap::new(),
        }
    }

    /// `collect`：塗る候補の部分の一覧。
    pub(crate) fn collect(&mut self, options: &AutoBucketOptions) -> Result<Rc<Vec<Entry>>, Error> {
        if let Some(entries) = &self.entries {
            return Ok(entries.clone());
        }
        let target = &self.target;
        let (width, height) = (target.width, target.height);
        let mut visited = vec![0u8; width * height];
        let mut session = Session::new(target.pixels.clone(), width, height, options, 0)?;
        let regions = &target.regions;
        let traces: BTreeSet<u32> = target
            .primary
            .iter()
            .zip(&regions.labels)
            .filter(|(&p, _)| p != 0)
            .map(|(_, &l)| l)
            .collect();
        let mut entries = Vec::new();
        for index in 0..regions.sizes.len() {
            if regions.sizes[index] < MIN_REGION_PIXELS && !traces.contains(&(index as u32)) {
                continue;
            }
            let value = target.region_value(index);
            if value == GAP_VALUE {
                continue;
            }
            let (x0, y0, x1, _) = regions.bounds[index];
            let lw = x1 - x0;
            let points: Vec<usize> = regions
                .local_mask(index)
                .iter()
                .enumerate()
                .filter(|&(i, &v)| v != 0 && visited[(i / lw + y0) * width + i % lw + x0] == 0)
                .map(|(i, _)| i)
                .collect();
            if points.is_empty() {
                continue;
            }
            let middle = points[points.len() / 2];
            let (x, y) = (middle % lw + x0, middle / lw + y0);
            if target.black[y * width + x] != 0 {
                continue;
            }
            let filled = session.compact(x, y)?;
            if let (Some(local), Some((bx0, by0, bx1, _))) = (filled.local.as_ref(), filled.bounds)
            {
                let bw = bx1 - bx0;
                for (i, &v) in local.iter().enumerate() {
                    visited[(i / bw + by0) * width + i % bw + bx0] |= v;
                }
            }
            if filled.ok() {
                entries.push(Entry {
                    x,
                    y,
                    value,
                    filled,
                });
            }
        }
        let entries = Rc::new(entries);
        self.entries = Some(entries.clone());
        Ok(entries)
    }

    fn reference(
        &self,
        shared: &mut Shared,
        geometry: &Cell,
        x: usize,
        y: usize,
    ) -> Result<Option<Rc<Fill>>, Error> {
        let result = shared.references.compact(
            geometry.pixels_key(),
            &geometry.pixels,
            geometry.width,
            geometry.height,
            &shared.options,
            x,
            y,
        )?;
        Ok(result.ok().then_some(result))
    }

    /// `choose_color`。
    fn choose_color(
        &mut self,
        shared: &mut Shared,
        guide: &Rc<Cell>,
        ordinal: usize,
        filled: &Fill,
        margin: usize,
    ) -> Result<Option<Rgb>, Halt> {
        let mut geometries: Vec<(Rc<Cell>, bool)> = Vec::new();
        if let Some(initial) = &guide.initial {
            geometries.push((initial.clone(), true));
        }
        geometries.push((guide.clone(), false));
        for (geometry, original_lines) in geometries {
            let key = (ordinal, geometry.geometry_key(), margin, original_lines);
            let reference = match self.matches.get(&key) {
                Some(found) => found.clone(),
                None => {
                    let found =
                        self.find_match(shared, &geometry, filled, margin, original_lines)?;
                    self.matches.insert(key, found.clone());
                    found
                }
            };
            let Some(reference) = reference else {
                continue;
            };
            let (_, total, value, n, _, _, unknown, _, _) =
                sample(guide, &reference, (0, 0), 0, 0, false);
            if let Some(value) = value {
                let value = value as u32;
                if !unknown
                    && total > 0
                    && n as f64 / total as f64 >= HINT_SHARE
                    && paintable(value)
                {
                    return Ok(to_rgb(value));
                }
            }
        }
        if let Some(rgb) = self.sample_face_color(shared, guide, filled, margin)? {
            return Ok(Some(rgb));
        }
        if let Some(rgb) = self.refine_face_color(shared, guide, filled, margin)? {
            return Ok(Some(rgb));
        }
        self.part_color(shared, guide, filled, margin)
    }

    /// `part_color`：未完成のセルの確定した画素だけで、部位ごとに引き継ぐ。
    fn part_color(
        &self,
        shared: &mut Shared,
        guide: &Rc<Cell>,
        filled: &Fill,
        margin: usize,
    ) -> Result<Option<Rgb>, Halt> {
        let Some(initial) = guide.initial.as_ref() else {
            return Ok(None);
        };
        if guide.confirmed.is_none() || filled.target.is_some() {
            return Ok(None);
        }
        let size = filled.size();
        let large = size >= 8192;
        let (shift, score) =
            shared
                .alignment
                .shift(&self.target, initial, filled.rect(), margin)?;
        if score < if large { 0.4 } else { 0.6 } {
            return Ok(None);
        }
        let (_, known, value, n, other, _, _, _, _) = sample(guide, filled, shift, 2 | 4, 8, false);
        let Some(value) = value.map(|v| v as u32) else {
            return Ok(None);
        };
        if (known as f64) < size as f64 * if large { 0.8 } else { 0.65 }
            || known == 0
            || (n as f64 / known as f64) < if large { 0.98 } else { 0.95 }
            || !paintable(value)
        {
            return Ok(None);
        }
        if !large
            && (!similar(size, other as f64) || (n as f64) < other as f64 * FACE_REVERSE_SHARE)
        {
            return Ok(None);
        }
        Ok(to_rgb(value))
    }

    /// `_refine_face_color`：局所的な伸縮で線の位置合わせがずれた小さな面。
    fn refine_face_color(
        &self,
        shared: &mut Shared,
        guide: &Rc<Cell>,
        filled: &Fill,
        margin: usize,
    ) -> Result<Option<Rgb>, Halt> {
        let Some(initial) = guide.initial.as_ref() else {
            return Ok(None);
        };
        if filled.target.is_some() {
            return Ok(None);
        }
        let size = filled.size();
        if !(8..=4096).contains(&size) {
            return Ok(None);
        }
        let (shift, score) =
            shared
                .alignment
                .shift(&self.target, initial, filled.rect(), margin)?;
        if score < MIN_LINE_OVERLAP {
            return Ok(None);
        }
        let (x0, y0, x1, y1) = filled.rect();
        let (width, height) = (guide.width as i64, guide.height as i64);
        let r = FACE_REFINE_RADIUS;
        let ax0 = (x0 as i64 + shift.1 - r).max(0);
        let ay0 = (y0 as i64 + shift.0 - r).max(0);
        let ax1 = width.min(x1 as i64 + shift.1 + r);
        let ay1 = height.min(y1 as i64 + shift.0 + r);
        if ax1 <= ax0 || ay1 <= ay0 {
            return Ok(None);
        }
        let roi = (ax0 as usize, ay0 as usize, ax1 as usize, ay1 as usize);
        let labels = crop(&guide.regions.labels, guide.width, roi);
        let limit = size as f64 * MAX_AREA_RATIO;
        if !labels
            .iter()
            .any(|&l| guide.regions.sizes[l as usize] as f64 <= limit)
        {
            return Ok(None); // どのずれでも面積比を満たす仕上げの部分が無い。
        }
        let values: Vec<u64> = crop(&guide.values, guide.width, roi)
            .into_iter()
            .map(u64::from)
            .collect();
        let labels: Vec<usize> = labels.into_iter().map(|l| l as usize).collect();
        let usable: Vec<u8> = crop(&initial.boundary, guide.width, roi)
            .into_iter()
            .map(|b| 1 - b)
            .collect();
        let excluded: Vec<u8> = crop(&guide.open, guide.width, roi)
            .into_iter()
            .zip(crop(&guide.undecided, guide.width, roi))
            .map(|(a, b)| a | b)
            .collect();
        let sizes: Vec<u64> = guide.regions.sizes.iter().map(|&s| s as u64).collect();
        let lw = x1 - x0;
        let points: Vec<(i64, i64)> = filled
            .mask()
            .iter()
            .enumerate()
            .filter(|(_, &v)| v != 0)
            .map(|(i, _)| {
                (
                    (i / lw + y0) as i64 + shift.0 - ay0,
                    (i % lw + x0) as i64 + shift.1 - ax0,
                )
            })
            .collect();
        let refined = refine_face_of(
            &values,
            &labels,
            &usable,
            &excluded,
            &sizes,
            &points,
            roi.2 - roi.0,
            roi.3 - roi.1,
            r,
            (
                FACE_SAMPLE_SHARE,
                HINT_SHARE,
                MAX_AREA_RATIO,
                FACE_REVERSE_SHARE,
            ),
        );
        // 辞書と同じく、最初に出た順を保って後の値で上書きする。
        let mut answers: Vec<(u32, f64)> = Vec::new();
        for (value, fit) in refined {
            let value = value as u32;
            if !paintable(value) {
                continue;
            }
            match answers.iter_mut().find(|(v, _)| *v == value) {
                Some(entry) => entry.1 = fit,
                None => answers.push((value, fit)),
            }
        }
        answers.sort_by(|a, b| b.1.partial_cmp(&a.1).expect("比べられる値"));
        if answers.is_empty() || (answers.len() > 1 && answers[0].1 - answers[1].1 < 0.05) {
            return Ok(None);
        }
        Ok(to_rgb(answers[0].0))
    }

    /// `_sample_face_color`：元の線で面が分かれたり開いたりしても、仕上げの同色部分と照合する。
    fn sample_face_color(
        &self,
        shared: &mut Shared,
        guide: &Rc<Cell>,
        filled: &Fill,
        margin: usize,
    ) -> Result<Option<Rgb>, Halt> {
        let Some(initial) = guide.initial.as_ref() else {
            return Ok(None);
        };
        if filled.target.is_some() {
            return Ok(None);
        }
        let (shift, score) =
            shared
                .alignment
                .shift(&self.target, initial, filled.rect(), margin)?;
        let size = filled.size();
        let large = size >= 8192;
        if score < if large { 0.3 } else { MIN_LINE_OVERLAP } {
            return Ok(None);
        }
        let (_, usable, value, n, other, bad, _, _, _) = sample(guide, filled, shift, 2, 0, false);
        let Some(value) = value.map(|v| v as u32) else {
            return Ok(None);
        };
        if (usable as f64) < size as f64 * FACE_SAMPLE_SHARE
            || usable == 0
            || (n as f64 / usable as f64) < HINT_SHARE
            || !paintable(value)
            || bad
        {
            return Ok(None);
        }
        if large && n as f64 / usable as f64 >= 0.98 && n as f64 >= size as f64 * 0.9 {
            return Ok(to_rgb(value));
        }
        if !similar(size, other as f64) || (n as f64) < other as f64 * FACE_REVERSE_SHARE {
            return Ok(None);
        }
        Ok(to_rgb(value))
    }

    /// `_match`：見本の対応する部分をバケツで取り直す。
    fn find_match(
        &self,
        shared: &mut Shared,
        geometry: &Rc<Cell>,
        filled: &Fill,
        margin: usize,
        original_lines: bool,
    ) -> Result<Option<Rc<Fill>>, Halt> {
        let (shift, overlap) =
            shared
                .alignment
                .shift(&self.target, geometry, filled.rect(), margin)?;
        if overlap < MIN_LINE_OVERLAP {
            return Ok(None);
        }
        let size = filled.size() as f64;
        if !is_trace(filled) {
            let (inside, usable, index, n, other, _, _, sy, sx) =
                sample(geometry, filled, shift, u8::from(original_lines), 0, true);
            let required = if original_lines {
                FACE_MATCH_SHARE
            } else {
                HINT_SHARE
            };
            let reverse = if original_lines {
                FACE_REVERSE_SHARE
            } else {
                FILL_SHARE
            };
            if (inside as f64) < size * HINT_SHARE
                || (usable as f64) < size * FACE_SAMPLE_SHARE
                || usable == 0
                || index.is_none()
                || (n as f64 / usable as f64) < required
                || !similar(filled.size(), other as f64)
                || (n as f64) < other as f64 * reverse
            {
                return Ok(None);
            }
            return Ok(self.reference(shared, geometry, sx, sy)?);
        }
        let shifted = Shifted::new(
            self.target.width,
            self.target.height,
            filled.mask(),
            filled.rect(),
            shift,
        );
        let inside = shifted.inside.iter().filter(|&&v| v).count();
        if (inside as f64) < size * HINT_SHARE {
            return Ok(None);
        }
        if original_lines {
            return self
                .match_trace(shared, geometry, filled, &shifted)
                .map_err(Halt::from);
        }
        // 形の変化を許すのは元の線画で確かめるときだけ。ここでは使える画素は内側すべて。
        let width = geometry.width;
        let usable: Vec<usize> = (0..shifted.sy.len())
            .filter(|&i| shifted.inside[i])
            .collect();
        if (usable.len() as f64) < size * FACE_SAMPLE_SHARE {
            return Ok(None);
        }
        let (index, share) = majority(
            usable
                .iter()
                .map(|&i| geometry.regions.labels[shifted.at(i, width)]),
        );
        let Some(index) = index else {
            return Ok(None);
        };
        if share < HINT_SHARE {
            return Ok(None);
        }
        let other = geometry.regions.sizes[index as usize];
        if !similar(filled.size(), other as f64) {
            return Ok(None);
        }
        let matched: Vec<usize> = usable
            .into_iter()
            .filter(|&i| geometry.regions.labels[shifted.at(i, width)] == index)
            .collect();
        if (matched.len() as f64) < other as f64 * FILL_SHARE {
            return Ok(None);
        }
        let at = matched[matched.len() / 2];
        Ok(self.reference(shared, geometry, shifted.sx[at], shifted.sy[at])?)
    }

    /// `_match_trace`：同じ色トレス線を近くで照合する。
    fn match_trace(
        &self,
        shared: &mut Shared,
        geometry: &Cell,
        filled: &Fill,
        shifted: &Shifted,
    ) -> Result<Option<Rc<Fill>>, Error> {
        let (width, height) = (geometry.width as i64, geometry.height as i64);
        let code = filled.target.map_or(0, pack);
        let points = shifted.sy.len();
        let mut selected: Vec<i64> = vec![-1; points];
        let mut selected_y: Vec<usize> = shifted.sy.clone();
        let mut selected_x: Vec<usize> = shifted.sx.clone();
        for (dy, dx) in trace::offsets(TRACE_RADIUS) {
            for i in 0..points {
                if !shifted.inside[i] || selected[i] >= 0 {
                    continue;
                }
                let (y, x) = (shifted.sy[i] as i64 + dy, shifted.sx[i] as i64 + dx);
                if y < 0 || y >= height || x < 0 || x >= width {
                    continue;
                }
                let at = y as usize * geometry.width + x as usize;
                if geometry.values[at] == code {
                    selected[i] = i64::from(geometry.regions.labels[at]);
                    selected_y[i] = y as usize;
                    selected_x[i] = x as usize;
                }
            }
        }
        let matched: Vec<u32> = selected
            .iter()
            .filter(|&&s| s >= 0)
            .map(|&s| s as u32)
            .collect();
        if (matched.len() as f64) < points as f64 * TRACE_MATCH_SHARE {
            return Ok(None);
        }
        let (index, share) = majority(matched);
        let Some(index) = index else {
            return Ok(None);
        };
        if share < HINT_SHARE {
            return Ok(None);
        }
        let chosen: Vec<usize> = (0..points)
            .filter(|&i| selected[i] == i64::from(index))
            .collect();
        let other = geometry.regions.sizes[index as usize];
        if !similar(filled.size(), other as f64) {
            return Ok(None);
        }
        let unique: HashSet<usize> = chosen
            .iter()
            .map(|&i| selected_y[i] * geometry.width + selected_x[i])
            .collect();
        if (unique.len() as f64) < other as f64 * TRACE_REVERSE_SHARE {
            return Ok(None);
        }
        let at = chosen[chosen.len() / 2];
        self.reference(shared, geometry, selected_x[at], selected_y[at])
    }
}

/// 色トレス線の仕上げ色を決める材料（選んだ見本から一度だけ作る）。
pub(crate) struct TraceContext<'a> {
    pub palette: &'a FinishPalette,
    pub contexts: &'a [Rules],
    pub rules: &'a FaceRules,
}

/// 覚えた部位から色を引き継ぐ呼び出し（`tracked_color`）。
pub(crate) type Tracked<'a> = dyn FnMut(&Fill, &mut Alignment) -> Result<Option<Rgb>, Error> + 'a;

fn record(result: &mut CellPaint, entry: &Entry, choices: &[Option<Rgb>], is_trace: bool) {
    result.checked += 1;
    let choices: Vec<Rgb> = choices.iter().flatten().copied().collect();
    let filled = &entry.filled;
    if choices.is_empty() || choices.iter().any(|rgb| *rgb != choices[0]) {
        result.unsure += 1;
        let local = filled.mask().to_vec();
        let bounds = filled.rect();
        let size = filled.size();
        let kind = if is_trace {
            result.undecided.push((bounds, local.clone()));
            FindingKind::UndecidedLine
        } else {
            result.unpainted.push((bounds, local.clone()));
            FindingKind::Unpainted
        };
        if size >= REPORT_MIN_PIXELS {
            result.notes.push(Finding {
                kind,
                rgb: to_rgb(entry.value),
                pixels: size,
                bounds: Some(bounds),
                expected: None,
                mask: Some(local),
            });
        }
        return;
    }
    result.clicks.push((entry.x, entry.y, choices[0]));
    result.paints.extend(paints(filled, choices[0]));
    if is_trace {
        result.line_pixels += filled.size();
    } else {
        result.filled += 1;
    }
}

/// `auto_bucket.paint_cell`：1セル分の塗り。
#[allow(clippy::too_many_arguments)]
pub(crate) fn paint_cell(
    work: &mut BucketWork,
    shared: &mut Shared,
    guides: &[Rc<Cell>],
    part_guides: &[Rc<Cell>],
    trace_context: &TraceContext<'_>,
    margin: usize,
    tracked: &mut Tracked<'_>,
) -> Result<CellPaint, Error> {
    let mut result = CellPaint::default();
    let options = shared.options.clone();
    let entries = work.collect(&options)?;
    let target = work.target.clone();
    let faces: Vec<usize> = (0..entries.len())
        .filter(|&i| !is_trace(&entries[i].filled))
        .collect();
    let face_requests: Vec<(usize, usize)> = faces
        .iter()
        .flat_map(|&ordinal| (0..guides.len()).map(move |g| (ordinal, g)))
        .collect();
    let mut context = Context { work, shared };
    let face_choices = Alignment::resolve(
        &mut context,
        &face_requests,
        alignment_of,
        |c, &(ordinal, g)| {
            c.work.choose_color(
                c.shared,
                &guides[g],
                ordinal,
                &entries[ordinal].filled,
                margin,
            )
        },
    )?;
    let mut face_colors: HashMap<usize, Vec<Option<Rgb>>> = HashMap::new();
    for (&(ordinal, _), rgb) in face_requests.iter().zip(face_choices) {
        face_colors.entry(ordinal).or_default().push(rgb);
    }
    let part_requests: Vec<usize> = faces
        .iter()
        .copied()
        .filter(|o| {
            !face_colors
                .get(o)
                .is_some_and(|c| c.iter().any(Option::is_some))
        })
        .collect();
    let part_choices =
        Alignment::resolve(&mut context, &part_requests, alignment_of, |c, &ordinal| {
            part_guides
                .iter()
                .map(|guide| {
                    c.work
                        .part_color(c.shared, guide, &entries[ordinal].filled, margin)
                })
                .collect::<Result<Vec<_>, Halt>>()
        })?;
    let part_colors: HashMap<usize, Vec<Option<Rgb>>> =
        part_requests.into_iter().zip(part_choices).collect();
    let mut traces = Vec::new();
    let mut pending_faces = Vec::new();
    for (ordinal, entry) in entries.iter().enumerate() {
        if is_trace(&entry.filled) {
            traces.push(ordinal);
            continue;
        }
        let some = |c: &Vec<Option<Rgb>>| c.iter().any(Option::is_some);
        let mut choices = face_colors.get(&ordinal).cloned().unwrap_or_default();
        if !some(&choices) {
            choices = part_colors.get(&ordinal).cloned().unwrap_or_default();
        }
        if !some(&choices) {
            choices = vec![tracked(&entry.filled, &mut context.shared.alignment)?];
        }
        if !some(&choices) {
            pending_faces.push(ordinal);
        } else {
            record(&mut result, entry, &choices, false);
        }
    }
    // 面の色を先に決め、その大きな面の色は線の候補から除く。
    let mut painted = target.values.clone();
    for paint in &result.paints {
        let (x0, y0, x1, _) = paint.bounds;
        let lw = x1 - x0;
        for (i, &v) in paint.mask.iter().enumerate() {
            if v != 0 {
                painted[(i / lw + y0) * target.width + i % lw + x0] = pack(paint.rgb);
            }
        }
    }
    let palettes = trace_context.contexts;
    let finishes = trace_context.palette;
    let mut hosts_cache = HashMap::new();
    let mut face_samples = HashMap::new();
    let mut line_finishes: HashMap<usize, u32> = HashMap::new();
    struct TraceRequest {
        ordinal: usize,
        hosts: trace::Hosts,
        allowed: HashSet<u32>,
        finish_colors: HashSet<u32>,
        ambiguous: bool,
    }
    let mut requests = Vec::new();
    for &ordinal in &traces {
        let filled = &entries[ordinal].filled;
        let hosts = trace::large_face_colors(
            &target,
            filled.rect(),
            filled.mask(),
            &painted,
            &mut hosts_cache,
        );
        let allowed: HashSet<u32> =
            trace::candidates(filled.target, &hosts, palettes, trace::MIN_FINISH_EVIDENCE)
                .into_keys()
                .collect();
        let all = trace::candidates(filled.target, &hosts, palettes, 1);
        let finish_colors: HashSet<u32> = finishes
            .get(&filled.target)
            .map(|f| f.keys().filter(|v| !hosts.contains(v)).copied().collect())
            .unwrap_or_default();
        let ambiguous = allowed.len() != 1 || all.len() != 1;
        requests.push(TraceRequest {
            ordinal,
            hosts,
            allowed,
            finish_colors,
            ambiguous,
        });
    }
    let trace_choices = Alignment::resolve(&mut context, &requests, alignment_of, |c, request| {
        if !request.ambiguous {
            return Ok(Vec::new());
        }
        let filled = &entries[request.ordinal].filled;
        let mut direct = Vec::new();
        for guide in guides {
            direct.push(
                c.work
                    .choose_color(c.shared, guide, request.ordinal, filled, margin)?,
            );
        }
        let allowed = if request.finish_colors.is_empty() {
            &request.allowed
        } else {
            &request.finish_colors
        };
        for guide in guides {
            direct.push(trace::local_finish(
                &target,
                guide,
                filled.rect(),
                filled.mask(),
                filled.target,
                margin,
                &mut c.shared.alignment,
                allowed,
            )?);
        }
        Ok(direct)
    })?;
    for (request, mut direct) in requests.iter().zip(trace_choices) {
        let entry = &entries[request.ordinal];
        let filled = &entry.filled;
        let finish_colors = &request.finish_colors;
        direct.push(trace::small_face_finish(
            &target,
            filled.rect(),
            filled.mask(),
            &painted,
            &|v| finish_colors.contains(&v),
            Some(&mut face_samples),
        ));
        let rgb = trace::choose(filled.target, &request.hosts, palettes, &direct, finishes);
        record(&mut result, entry, &[rgb], true);
        if let Some(rgb) = rgb {
            let (x0, y0, x1, _) = filled.rect();
            let lw = x1 - x0;
            for (i, &v) in filled.mask().iter().enumerate() {
                if v != 0 {
                    let label = target.regions.labels[(i / lw + y0) * target.width + i % lw + x0];
                    line_finishes.insert(label as usize, pack(rgb));
                }
            }
        }
    }
    for ordinal in pending_faces {
        let entry = &entries[ordinal];
        let filled = &entry.filled;
        let rgb = trace::enclosed_face_color(
            &target,
            filled.rect(),
            filled.mask(),
            filled.target,
            &line_finishes,
            trace_context.rules,
        );
        record(&mut result, entry, &[rgb], false);
    }
    let island_max_width = context.work.island_max_width;
    if island_max_width != 0 {
        // 決めたクリックを通常のバケツで再現し、同じ塗り残しを拾う。
        result.paints.clear();
        let mut session = Session::new(
            target.pixels.clone(),
            target.width,
            target.height,
            &options,
            island_max_width,
        )?;
        for &(x, y, rgb) in &result.clicks {
            let filled = session.compact(x, y)?;
            if filled.ok() {
                result.paints.extend(paints(&filled, rgb));
            }
        }
    }
    Ok(result)
}
