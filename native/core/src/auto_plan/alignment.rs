//! 部分のまわりの線の局所的なずれ（`auto_paint._local_shift`）と、その結果を使い回す
//! `auto_alignment.AlignmentCache`（CPU の経路）。
//!
//! Python 版は、採色の呼び出しを一度走らせて要るずれを集め、まとめて解いてから
//! 呼び出しを走らせ直す（`resolve`）。まとめて解くときだけ、同じ線の点で決まるずれを
//! ほかの矩形へ流用する（`_learn_motion`）。流用は結果を変えうるので、集める順・
//! 解く順・流用の打ち切りまで同じにする。
use std::collections::{HashMap, HashSet, VecDeque};
use std::rc::Rc;

use crate::local_shift::{prepare_local_shift, solve_local_shift};
use crate::reference_check::Bounds;
use crate::Error;

use super::cell::{content_key, Cell, Key};

/// ずれを求めるときに重ねる、部分のまわりの幅。
pub(crate) const CONTEXT: usize = 24;
/// 間引いたあとの探索窓の一辺の上限。
const MAX_WINDOW: usize = 384;
const MOTION_PENALTY: f64 = 0.003;
const NEAR_LINE_WEIGHT: f64 = 0.8;
/// 流用する結果の数の上限。
const SHARED_LIMIT: usize = 16384;

/// 解き直すまで待つ印（Python の `_DeferredShift`）。
#[derive(Debug)]
pub(crate) struct Deferred;

/// 採色の失敗：ずれを待つか、計算の誤り。
#[derive(Debug)]
pub(crate) enum Halt {
    Deferred,
    Error(Error),
}

impl From<Error> for Halt {
    fn from(error: Error) -> Self {
        Self::Error(error)
    }
}

impl From<Deferred> for Halt {
    fn from(_: Deferred) -> Self {
        Self::Deferred
    }
}

/// ずれ `(dy, dx)`、重なった割合、次点との差（流用するときだけ）。
#[derive(Clone, Copy, Debug)]
pub(crate) struct ShiftResult {
    pub shift: (i64, i64),
    pub overlap: f64,
    gap: Option<f64>,
}

type AlignKey = (Key, Key, Bounds, usize);
type Request = (Rc<Cell>, Rc<Cell>, Bounds, usize);
type SupportKey = (usize, usize, usize, usize, Key);

/// 探索窓・対象矩形・間引き幅（`_local_shift_geometry`）。
fn geometry(width: usize, height: usize, (x0, y0, x1, y1): Bounds, margin: usize) -> [usize; 9] {
    let tx0 = x0.saturating_sub(CONTEXT);
    let ty0 = y0.saturating_sub(CONTEXT);
    let tx1 = width.min(x1 + CONTEXT);
    let ty1 = height.min(y1 + CONTEXT);
    let wx0 = tx0.saturating_sub(margin);
    let wy0 = ty0.saturating_sub(margin);
    let wx1 = width.min(tx1 + margin);
    let wy1 = height.min(ty1 + margin);
    let step = 1usize.max((wx1 - wx0).max(wy1 - wy0).div_ceil(MAX_WINDOW));
    [wx0, wy0, wx1, wy1, tx0, ty0, tx1, ty1, step]
}

/// `mask[y0:y1:step, x0:x1:step]`。
fn strided(
    mask: &[u8],
    width: usize,
    (x0, y0, x1, y1): Bounds,
    step: usize,
) -> (Vec<u8>, usize, usize) {
    let rows = if y1 > y0 { (y1 - y0).div_ceil(step) } else { 0 };
    let cols = if x1 > x0 { (x1 - x0).div_ceil(step) } else { 0 };
    let mut out = Vec::with_capacity(rows * cols);
    for r in 0..rows {
        let y = y0 + r * step;
        for c in 0..cols {
            out.push(mask[y * width + x0 + c * step]);
        }
    }
    (out, rows, cols)
}

/// `_local_shift`：`bounds` のまわりの線が `guide` でどれだけずれた所にあるか。
pub(crate) fn local_shift(
    target: &Cell,
    guide: &Cell,
    bounds: Bounds,
    margin: usize,
    share_motion: bool,
) -> Result<ShiftResult, Error> {
    let (width, height) = (target.width, target.height);
    let [wx0, wy0, wx1, wy1, tx0, ty0, tx1, ty1, step] = geometry(width, height, bounds, margin);
    let oy = (ty0 - wy0).div_ceil(step);
    let ox = (tx0 - wx0).div_ceil(step);
    let (source, sh, sw) = strided(
        &target.boundary,
        width,
        (wx0 + ox * step, wy0 + oy * step, tx1, ty1),
        step,
    );
    let (wide, rows, cols) = strided(&guide.wide_boundary, width, (wx0, wy0, wx1, wy1), step);
    let limit = margin / step;
    let none = ShiftResult {
        shift: (0, 0),
        overlap: 0.0,
        gap: None,
    };
    let Some(prepared) =
        prepare_local_shift(&source, (sh, sw), &wide, (rows, cols), (oy, ox), limit)?
    else {
        return Ok(none);
    };
    let roi = (
        wx0,
        wy0,
        width.min(wx0 + cols * step),
        height.min(wy0 + rows * step),
    );
    let (exact, _, _) = strided(&guide.boundary, width, roi, step);
    let (near, _, _) = strided(&guide.wide_boundary, width, roi, step);
    let (shift, overlap, gap) = solve_local_shift(
        &prepared.before,
        &exact,
        &near,
        cols,
        rows,
        prepared.size,
        &prepared.dys,
        &prepared.dxs,
        prepared.total,
        step as i64,
        MOTION_PENALTY,
        NEAR_LINE_WEIGHT,
        share_motion,
    )?;
    Ok(ShiftResult {
        shift,
        overlap,
        gap: share_motion.then_some(gap),
    })
}

/// 区画を一段ずつ追う途中の状態（`_TrackState`）。
struct TrackState {
    bounds: (i64, i64, i64, i64),
    dy: i64,
    dx: i64,
    back_y: i64,
    back_x: i64,
}

/// 挿入順を保つ流用表（`OrderedDict`）。上書きは位置を変えない。
#[derive(Default)]
struct SharedTable {
    values: HashMap<(Key, SupportKey), ShiftResult>,
    order: VecDeque<(Key, SupportKey)>,
}

impl SharedTable {
    fn insert(&mut self, key: (Key, SupportKey), value: ShiftResult) {
        if self.values.insert(key, value).is_none() {
            self.order.push_back(key);
        }
        while self.order.len() > SHARED_LIMIT {
            if let Some(old) = self.order.pop_front() {
                self.values.remove(&old);
            }
        }
    }

    fn clear(&mut self) {
        self.values.clear();
        self.order.clear();
    }
}

/// 位置合わせの結果の表（`AlignmentCache(gpu="off")`）。
/// 集めている途中のずれの要求（順番と、重なりの確認用）。
type Collected = (Vec<(AlignKey, Request)>, HashSet<AlignKey>);
/// 区画の往路のずれ `(dy, dx)` と復路のずれ `(dy, dx)`。
pub(crate) type Track = (i64, i64, i64, i64);

pub(crate) struct Alignment {
    results: HashMap<AlignKey, ShiftResult>,
    requests: Option<Collected>,
    supports: HashMap<(Key, Bounds, usize), Option<SupportKey>>,
    shared: SharedTable,
    support_enabled: bool,
    pub(crate) support_checks: usize,
    pub(crate) shared_hits: usize,
    pub(crate) hits: usize,
    pub(crate) solves: usize,
}

impl Alignment {
    pub(crate) fn new() -> Self {
        Self {
            results: HashMap::new(),
            requests: None,
            supports: HashMap::new(),
            shared: SharedTable::default(),
            support_enabled: true,
            support_checks: 0,
            shared_hits: 0,
            hits: 0,
            solves: 0,
        }
    }

    fn key(target: &Cell, guide: &Cell, bounds: Bounds, margin: usize) -> AlignKey {
        (target.boundary_key(), guide.boundary_key(), bounds, margin)
    }

    /// `shift`：求めてあれば返す。集めている途中なら要求を記録して待つ。
    pub(crate) fn shift(
        &mut self,
        target: &Rc<Cell>,
        guide: &Rc<Cell>,
        bounds: Bounds,
        margin: usize,
    ) -> Result<((i64, i64), f64), Halt> {
        let key = Self::key(target, guide, bounds, margin);
        if let Some(result) = self.results.get(&key) {
            self.hits += 1;
            return Ok((result.shift, result.overlap));
        }
        if let Some((requests, seen)) = self.requests.as_mut() {
            if seen.insert(key) {
                requests.push((key, (target.clone(), guide.clone(), bounds, margin)));
            }
            return Err(Halt::Deferred);
        }
        let result = local_shift(target, guide, bounds, margin, false)?;
        self.results.insert(key, result);
        self.solves += 1;
        Ok((result.shift, result.overlap))
    }

    /// `_support_key`：ずれを決める線の点の並び。
    fn support_key(&mut self, target: &Cell, bounds: Bounds, margin: usize) -> Option<SupportKey> {
        let cache = (target.boundary_key(), bounds, margin);
        if let Some(found) = self.supports.get(&cache) {
            return *found;
        }
        let (width, height) = (target.width, target.height);
        let [wx0, wy0, wx1, wy1, tx0, ty0, tx1, ty1, step] =
            geometry(width, height, bounds, margin);
        let limit = margin / step;
        let result = if (wx1 - wx0).div_ceil(step).min((wy1 - wy0).div_ceil(step)) <= 2 * limit {
            None
        } else {
            let sx = tx0 + ((wx0 as i64 - tx0 as i64).rem_euclid(step as i64)) as usize;
            let sy = ty0 + ((wy0 as i64 - ty0 as i64).rem_euclid(step as i64)) as usize;
            let mut points: Vec<u8> = Vec::new();
            let mut y = sy;
            while y < ty1 {
                let mut x = sx;
                while x < tx1 {
                    if target.boundary[y * width + x] != 0 {
                        points.extend_from_slice(&((y * width + x) as i64).to_le_bytes());
                    }
                    x += step;
                }
                y += step;
            }
            Some((width, height, step, limit, content_key(9, &[&points])))
        };
        self.supports.insert(cache, result);
        result
    }

    /// `solve_many`：要るずれだけをまとめて解き、流用も学ぶ。
    pub(crate) fn solve_many(&mut self, requests: Vec<Request>) -> Result<(), Error> {
        let mut pending: Vec<(AlignKey, Request)> = Vec::new();
        let mut seen = HashSet::new();
        for request in requests {
            let key = Self::key(&request.0, &request.1, request.2, request.3);
            if !self.results.contains_key(&key) && seen.insert(key) {
                pending.push((key, request));
            }
        }
        if pending.is_empty() {
            return Ok(());
        }
        let mut kept = Vec::with_capacity(pending.len());
        let mut checking = true;
        for (key, request) in pending {
            if checking && !self.support_enabled {
                checking = false;
            }
            if checking {
                self.support_checks += 1;
                let support = self.support_key(&request.0, request.2, request.3);
                let shared = support
                    .and_then(|support| {
                        self.shared.values.get(&(request.1.boundary_key(), support))
                    })
                    .copied();
                if let Some(shared) = shared {
                    self.results.insert(key, shared);
                    self.shared_hits += 1;
                    continue;
                }
            }
            kept.push((key, request));
        }
        if kept.is_empty() {
            return Ok(());
        }
        let share = self.support_enabled;
        for (key, (target, guide, bounds, margin)) in &kept {
            let result = local_shift(target, guide, *bounds, *margin, share)?;
            self.results.insert(*key, result);
            self.solves += 1;
        }
        self.learn_motion(&kept);
        Ok(())
    }

    /// `_learn_motion`。
    fn learn_motion(&mut self, pending: &[(AlignKey, Request)]) {
        if !self.support_enabled {
            return;
        }
        for (key, (target, guide, bounds, margin)) in pending {
            let Some(result) = self.results.get(key).copied() else {
                continue;
            };
            let gap = result.gap.unwrap_or(0.0);
            let far = [0.3, 0.4, 0.5, 0.6]
                .iter()
                .map(|t: &f64| (result.overlap - t).abs())
                .fold(f64::INFINITY, f64::min);
            if gap > 2e-5 && far > 2e-5 {
                if let Some(support) = self.support_key(target, *bounds, *margin) {
                    self.shared.insert((guide.boundary_key(), support), result);
                }
            }
        }
        if self.support_checks >= 256 && self.shared_hits * 100 < self.support_checks {
            self.support_enabled = false;
            self.shared.clear();
            self.supports.clear();
        }
    }

    /// `resolve`：呼び出しを走らせ、待ったずれをまとめて解いて、待った呼び出しを走らせ直す。
    pub(crate) fn resolve<S, I, T>(
        state: &mut S,
        items: &[I],
        alignment: fn(&mut S) -> &mut Alignment,
        mut call: impl FnMut(&mut S, &I) -> Result<T, Halt>,
    ) -> Result<Vec<T>, Error> {
        let mut answers: Vec<Option<T>> = (0..items.len()).map(|_| None).collect();
        let mut pending: Vec<usize> = (0..items.len()).collect();
        while !pending.is_empty() {
            alignment(state).requests = Some((Vec::new(), HashSet::new()));
            let mut unresolved = Vec::new();
            let mut failure = None;
            for &index in &pending {
                match call(state, &items[index]) {
                    Ok(answer) => answers[index] = Some(answer),
                    Err(Halt::Deferred) => unresolved.push(index),
                    Err(Halt::Error(error)) => {
                        failure = Some(error);
                        break;
                    }
                }
            }
            let requests = alignment(state)
                .requests
                .take()
                .map(|r| r.0)
                .unwrap_or_default();
            if let Some(error) = failure {
                return Err(error);
            }
            alignment(state).solve_many(requests.into_iter().map(|(_, r)| r).collect())?;
            pending = unresolved;
        }
        Ok(answers.into_iter().map(|a| a.expect("answer")).collect())
    }

    /// `track_many`：区画を一段ずつ進め、復路もまとめて追う。
    pub(crate) fn track_many(
        &mut self,
        steps: &[(Rc<Cell>, Rc<Cell>)],
        bounds_list: &[Bounds],
        (width, height): (usize, usize),
    ) -> Result<Vec<Option<Track>>, Error> {
        let mut states: Vec<Option<TrackState>> = bounds_list
            .iter()
            .map(|b| {
                Some(TrackState {
                    bounds: (b.0 as i64, b.1 as i64, b.2 as i64, b.3 as i64),
                    dy: 0,
                    dx: 0,
                    back_y: 0,
                    back_x: 0,
                })
            })
            .collect();
        let reversed: Vec<(Rc<Cell>, Rc<Cell>)> = steps.iter().rev().cloned().collect();
        for (backward, pairs) in [(false, steps), (true, reversed.as_slice())] {
            for (left, right) in pairs {
                let mut requests: Vec<Request> = Vec::new();
                let mut indices = Vec::new();
                for (index, state) in states.iter_mut().enumerate() {
                    let Some(current) = state else {
                        continue;
                    };
                    let b = current.bounds;
                    let clipped = (
                        b.0.max(0),
                        b.1.max(0),
                        b.2.min(width as i64),
                        b.3.min(height as i64),
                    );
                    if clipped.2 <= clipped.0 || clipped.3 <= clipped.1 {
                        *state = None;
                        continue;
                    }
                    let clipped = (
                        clipped.0 as usize,
                        clipped.1 as usize,
                        clipped.2 as usize,
                        clipped.3 as usize,
                    );
                    requests.push(if backward {
                        (right.clone(), left.clone(), clipped, 48)
                    } else {
                        (left.clone(), right.clone(), clipped, 48)
                    });
                    indices.push(index);
                }
                self.solve_many(requests.clone())?;
                for (index, (a, b, bounds, margin)) in indices.into_iter().zip(requests) {
                    let (shift, score) = match self.shift(&a, &b, bounds, margin) {
                        Ok(found) => found,
                        Err(Halt::Error(error)) => return Err(error),
                        Err(Halt::Deferred) => unreachable!("集めていないときは待たない"),
                    };
                    if score < 0.5 {
                        states[index] = None;
                        continue;
                    }
                    let state = states[index].as_mut().expect("state");
                    if backward {
                        state.back_y += shift.0;
                        state.back_x += shift.1;
                        let b = state.bounds;
                        state.bounds = (b.0 + shift.1, b.1 + shift.0, b.2 + shift.1, b.3 + shift.0);
                    } else {
                        state.dy += shift.0;
                        state.dx += shift.1;
                        let initial = bounds_list[index];
                        state.bounds = (
                            initial.0 as i64 + state.dx,
                            initial.1 as i64 + state.dy,
                            initial.2 as i64 + state.dx,
                            initial.3 as i64 + state.dy,
                        );
                    }
                }
            }
        }
        Ok(states
            .into_iter()
            .map(|s| s.map(|s| (s.dy, s.dx, s.back_y, s.back_x)))
            .collect())
    }
}
