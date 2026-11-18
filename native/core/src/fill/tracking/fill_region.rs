use super::{
    bounds, center, context_similarity, copy_rect, crop, estimate_color_shifts, estimate_shifts,
    warp_region, Rect, Shift, Size,
};
use crate::fill::session::{BucketState, Category, LocalOutcome, PrepareOptions};
use crate::fill::target::{fill_target_mask, TargetColor};
use crate::regions::{dilate, flood_fill};
use crate::Error;
use std::borrow::Cow;
use std::collections::HashMap;

/// Python の `bucket_fill.TRACK_MAX_TRIES`。
const TRACK_MAX_TRIES: usize = 8;
/// Python の `bucket_fill.TRACK_MIN_IOU`。
const TRACK_MIN_IOU: f64 = 0.1;
/// Python の `bucket_fill.TRACK_GOOD_IOU`。
const TRACK_GOOD_IOU: f64 = 0.75;
/// Python の `bucket_fill.TRACK_MAX_AREA_RATIO`。
const TRACK_MAX_AREA_RATIO: f64 = 3.0;
/// Python の `bucket_fill.TRACK_INCLUDE_RATIO`。
const TRACK_INCLUDE_RATIO: f64 = 0.7;
/// Python の `bucket_fill.TRACK_SMALL_AREA`。
const TRACK_SMALL_AREA: usize = 2500;
/// Python の `bucket_fill.TRACK_SMALL_REACH`。
const TRACK_SMALL_REACH: i64 = 6;
/// Python の `bucket_fill.TRACK_SMALL_SEEDS`。
const TRACK_SMALL_SEEDS: usize = 6;
/// Python の `fill_tracking.CONTEXT_RADIUS`。
const CONTEXT_RADIUS: i64 = 24;
/// Python の `bucket_fill.TRACK_CONTEXT_PENALTY`。
const TRACK_CONTEXT_PENALTY: f64 = 0.1;

/// `track_fill_region_numpy` に渡す画像と手がかり。
pub struct TrackInput<'a> {
    pub rgba: &'a [u8],
    pub previous: &'a [u8],
    pub previous_rgba: Option<&'a [u8]>,
    pub previous_lines: Option<&'a [u8]>,
    pub selection: Option<&'a [u8]>,
    pub dimensions: (usize, usize),
    pub reference: Category,
}

/// 採用された `FillResult.matched` の作り方。
pub enum TrackMatch {
    Previous,
    Shifted(Shift),
    Warped(Vec<u8>),
}

/// `track_fill_region_numpy` の候補選択結果。複数の領域は採用順に並ぶ。
pub struct TrackOutcome {
    pub score: Option<f64>,
    pub members: Vec<LocalOutcome>,
    pub matched: Option<TrackMatch>,
}

fn target_kind(options: &PrepareOptions, reference: Category) -> TargetColor {
    match &options.target_color {
        TargetColor::All(_) => TargetColor::All(reference),
        other => other.clone(),
    }
}

fn invert(mask: &mut [u8]) {
    for value in mask {
        *value ^= 1;
    }
}

fn translated(mask: &[u8], size: Size, shift: Shift) -> Vec<u8> {
    let mut result = vec![0; mask.len()];
    copy_rect(mask, size, &mut result, size, shift);
    result
}

fn area(mask: &[u8]) -> usize {
    mask.iter().map(|&value| usize::from(value)).sum()
}

fn positions(rect: Rect) -> impl Iterator<Item = (usize, usize)> {
    (rect.y0 as usize..rect.y1 as usize)
        .flat_map(move |y| (rect.x0 as usize..rect.x1 as usize).map(move |x| (x, y)))
}

fn centroid(mask: &[u8], size: Size, rect: Rect) -> (f64, f64) {
    let (mut sx, mut sy, mut count) = (0_u64, 0_u64, 0_u64);
    for (x, y) in positions(rect) {
        let value = u64::from(mask[y * size.w + x]);
        sx += x as u64 * value;
        sy += y as u64 * value;
        count += value;
    }
    (sy as f64 / count as f64, sx as f64 / count as f64)
}

fn distance((x, y): (usize, usize), (cy, cx): (f64, f64)) -> f64 {
    let dy = y as f64 - cy;
    let dx = x as f64 - cx;
    dy * dy + dx * dx
}

fn fill(
    state: &mut BucketState<&[u8]>,
    (x, y): (usize, usize),
    selection: Option<&[u8]>,
) -> Result<Option<LocalOutcome>, Error> {
    let result = state
        .compute_tracking(x, y, selection)
        .map_err(Error::Invalid)?;
    Ok((result.reason == "ok").then_some(result))
}

fn marked_pixels(result: &LocalOutcome) -> impl Iterator<Item = (usize, usize)> + '_ {
    result.bounds.into_iter().flat_map(move |(x0, y0, x1, _)| {
        result
            .region
            .iter()
            .flatten()
            .enumerate()
            .filter(|&(_, &v)| v != 0)
            .map(move |(i, _)| (x0 + i % (x1 - x0), y0 + i / (x1 - x0)))
    })
}

fn clear_region(mask: &mut [u8], size: Size, result: &LocalOutcome) {
    for (x, y) in marked_pixels(result) {
        mask[y * size.w + x] = 0;
    }
}

fn iou(overlap: usize, area: usize, reference_area: usize) -> f64 {
    overlap as f64 / (area + reference_area - overlap).max(1) as f64
}

struct Reference<'a> {
    mask: Cow<'a, [u8]>,
    kind: TrackMatch,
    bounds: Option<Rect>,
    area: usize,
}

impl<'a> Reference<'a> {
    fn new(mask: Cow<'a, [u8]>, kind: TrackMatch, size: Size) -> Self {
        Self {
            bounds: bounds(&mask, size),
            area: area(&mask),
            mask,
            kind,
        }
    }
}

struct Candidate {
    result: LocalOutcome,
    area: usize,
    overlaps: Vec<usize>,
}

fn pick_overlap(
    mut eligible: Vec<u8>,
    references: &[Reference<'_>],
    previous_bounds: Rect,
    input: &TrackInput<'_>,
    size: Size,
    state: &mut BucketState<&[u8]>,
) -> Result<(Vec<LocalOutcome>, f64, usize), Error> {
    let previous_index = references.len() - 1;
    let previous_area = references[previous_index].area;
    let lead = references[0].bounds.map_or(
        (&*references[previous_index].mask, previous_bounds),
        |rect| (&*references[0].mask, rect),
    );
    let center = centroid(lead.0, size, lead.1);
    // 行優先の順を保持し、同距離なら最初の種を選ぶ。
    let mut points: Vec<_> = eligible
        .chunks_exact(size.w)
        .enumerate()
        .flat_map(|(y, row)| {
            row.iter()
                .enumerate()
                .filter_map(move |(x, &v)| (v != 0).then_some((x, y)))
        })
        .collect();
    let mut candidates = Vec::new();
    let mut best = Vec::new();
    let (mut best_iou, mut matched) = (0.0, 0);
    for _ in 0..TRACK_MAX_TRIES {
        let Some(seed @ (x, y)) = points
            .iter()
            .copied()
            .min_by(|&a, &b| distance(a, center).total_cmp(&distance(b, center)))
        else {
            break;
        };
        if let Some(result) = fill(state, seed, input.selection)? {
            clear_region(&mut eligible, size, &result);
            let area = area(result.region.as_deref().unwrap_or_default());
            let ratio = area as f64 / previous_area as f64;
            if ratio <= TRACK_MAX_AREA_RATIO {
                let overlaps: Vec<_> = references
                    .iter()
                    .map(|reference| {
                        marked_pixels(&result)
                            .map(|(x, y)| usize::from(reference.mask[y * size.w + x]))
                            .sum()
                    })
                    .collect();
                if ratio >= 1.0 / TRACK_MAX_AREA_RATIO {
                    for (index, (&overlap, reference)) in
                        overlaps.iter().zip(references).enumerate()
                    {
                        let score = iou(overlap, area, reference.area);
                        if score > best_iou {
                            best_iou = score;
                            matched = index;
                            best = vec![candidates.len()];
                        }
                    }
                }
                candidates.push(Candidate {
                    result,
                    area,
                    overlaps,
                });
                if best_iou >= TRACK_GOOD_IOU {
                    break;
                }
            }
        } else {
            let rejected = flood_fill(&eligible, size.w, size.h, &[(x as i64, y as i64)]);
            for (value, removed) in eligible.iter_mut().zip(rejected) {
                *value &= removed ^ 1;
            }
        }
        points.retain(|&(x, y)| eligible[y * size.w + x] != 0);
    }
    if best_iou < TRACK_GOOD_IOU {
        for (index, reference) in references.iter().enumerate() {
            let members: Vec<_> = candidates
                .iter()
                .enumerate()
                .filter(|(_, c)| c.overlaps[index] as f64 >= c.area as f64 * TRACK_INCLUDE_RATIO)
                .map(|(i, _)| i)
                .collect();
            if members.len() < 2 {
                continue;
            }
            // 仮想境界を回収した領域は互いに重なることがある。
            let mut union = vec![0; eligible.len()];
            let (mut area, mut overlap) = (0, 0);
            for &i in &members {
                for (x, y) in marked_pixels(&candidates[i].result) {
                    let j = y * size.w + x;
                    area += usize::from(union[j] == 0);
                    overlap += usize::from((union[j] == 0) && reference.mask[j] != 0);
                    union[j] = 1;
                }
            }
            let score = iou(overlap, area, reference.area);
            if score > best_iou {
                best_iou = score;
                best = members;
                matched = index;
            }
        }
    }
    Ok((
        candidates
            .into_iter()
            .enumerate()
            .filter_map(|(i, c)| best.contains(&i).then_some(c.result))
            .collect(),
        best_iou,
        matched,
    ))
}

fn rgba_crop(rgba: &[u8], size: Size, rect: Rect) -> Vec<u8> {
    crop(
        rgba,
        Size {
            w: size.w * 4,
            h: size.h,
        },
        Rect {
            x0: rect.x0 * 4,
            x1: rect.x1 * 4,
            ..rect
        },
    )
}

// bucket_fill._shift_candidates_numpy と同じ順に重複を除く。
fn shift_candidates(
    before: &[u8],
    rgba: &[u8],
    previous: &[u8],
    size: Size,
    rect: Rect,
) -> Result<Vec<Shift>, Error> {
    let margin = 64.max(3 * (rect.x1 - rect.x0).max(rect.y1 - rect.y0));
    let template = rect.expand(CONTEXT_RADIUS).intersect(size.rect());
    let window = template.expand(margin).intersect(size.rect());
    let line_window = rect.expand(margin).intersect(size.rect());
    let ts = template.size();
    let ws = window.size();
    let ls = line_window.size();
    ts.check(before, 4)?;
    let mut before_lines = vec![0; ls.w * ls.h];
    copy_rect(
        &fill_target_mask(before, ts.w, ts.h, TargetColor::Black),
        ts,
        &mut before_lines,
        ls,
        (template.y0 - line_window.y0, template.x0 - line_window.x0),
    );
    let mut line_shifts = estimate_shifts(
        &before_lines,
        &line_crop(rgba, size, line_window),
        &crop(previous, size, line_window),
        ls.w,
        ls.h,
        3,
        Some(margin),
    )?;
    let colors = estimate_color_shifts(
        before,
        &rgba_crop(rgba, size, window),
        &crop(previous, size, template),
        ts.w,
        ts.h,
        ws.w,
        ws.h,
        (window.y0 - template.y0, window.x0 - template.x0),
        3,
    )?;
    let mut unique = Vec::new();
    for shift in line_shifts.drain(..).chain(colors).chain([(0, 0)]) {
        if !unique.contains(&shift) {
            unique.push(shift);
        }
    }
    Ok(unique)
}

fn line_crop(rgba: &[u8], size: Size, rect: Rect) -> Vec<u8> {
    let ws = rect.size();
    if ws.w == size.w && ws.h == size.h {
        return fill_target_mask(rgba, size.w, size.h, TargetColor::Black);
    }
    fill_target_mask(&rgba_crop(rgba, size, rect), ws.w, ws.h, TargetColor::Black)
}

/// `bucket_fill.guess_target_categories_numpy` と同じ色候補を得票順で返す。
/// 前コマの RGBA は previous の外接矩形を CONTEXT_RADIUS だけ広げた矩形内のデータ。
/// 今のコマと previous は、すべての探索窓・移動先を含む同じ矩形に切り出せる。
pub fn guess_target_categories(
    rgba: &[u8],
    previous: &[u8],
    before: &[u8],
    w: usize,
    h: usize,
) -> Result<Vec<Category>, Error> {
    let size = Size { w, h };
    size.check(rgba, 4)?;
    size.check(previous, 1)?;
    let Some(rect) = bounds(previous, size) else {
        return Ok(Vec::new());
    };
    let shifts = shift_candidates(before, rgba, previous, size, rect)?;
    let mut votes: Vec<(i32, f64)> = Vec::new();
    let mut counts: HashMap<i32, usize> = HashMap::new();
    for (dy, dx) in shifts {
        let mut total = 0;
        let mut last = None;
        let mut run = 0;
        for (x, y) in positions(rect.intersect(Rect {
            x0: -dx,
            y0: -dy,
            x1: w as i64 - dx,
            y1: h as i64 - dy,
        })) {
            if previous[y * w + x] == 0 {
                continue;
            }
            let i = ((y as i64 + dy) as usize * w + (x as i64 + dx) as usize) * 4;
            let p = &rgba[i..i + 4];
            if p[3] != 0 && p[..3].iter().all(|&c| c <= crate::fill::target::BLACK_MAX) {
                continue;
            }
            let key = if p[3] == 0 || p[..3] == [255; 3] {
                -1
            } else {
                i32::from_be_bytes([0, p[0], p[1], p[2]])
            };
            total += 1;
            if last != Some(key) {
                if let Some(key) = last {
                    *counts.entry(key).or_default() += run;
                }
                last = Some(key);
                run = 0;
            }
            run += 1;
        }
        if let Some(key) = last {
            *counts.entry(key).or_default() += run;
        }
        // np.unique はキーの昇順、argmax は同数なら最初のキー。
        if let Some((key, n)) = counts
            .drain()
            .max_by_key(|&(key, n)| (n, std::cmp::Reverse(key)))
        {
            let share = n as f64 / total as f64;
            if let Some((_, vote)) = votes.iter_mut().find(|(color, _)| *color == key) {
                *vote += share;
            } else {
                votes.push((key, share));
            }
        }
    }
    // 同票の順位は、ずれの探索順で票が入った順。
    votes.sort_by(|a, b| b.1.total_cmp(&a.1));
    Ok(votes
        .into_iter()
        .map(|(key, _)| {
            (key >= 0).then(|| {
                let [_, r, g, b] = key.to_be_bytes();
                [r, g, b]
            })
        })
        .collect())
}

struct Context {
    rgba: Vec<u8>,
    region: Vec<u8>,
    dimensions: (usize, usize),
    origin: Shift,
}

fn context(rgba: &[u8], region: &[u8], size: Size, (y, x): Shift) -> Context {
    let window = Rect {
        x0: x,
        y0: y,
        x1: x + 1,
        y1: y + 1,
    }
    .expand(CONTEXT_RADIUS)
    .intersect(size.rect());
    Context {
        rgba: rgba_crop(rgba, size, window),
        region: crop(region, size, window),
        dimensions: (window.size().w, window.size().h),
        origin: (window.y0 - y, window.x0 - x),
    }
}

fn small_region(
    input: &TrackInput<'_>,
    before: &[u8],
    target: &[u8],
    size: Size,
    previous_bounds: Rect,
    previous_area: usize,
    state: &mut BucketState<&[u8]>,
) -> Result<Option<(LocalOutcome, f64, Shift)>, Error> {
    let Some(previous_center) = center(input.previous, size) else {
        return Ok(None);
    };
    let before_context = context(before, input.previous, size, previous_center);
    let mut available = target.to_vec();
    let mut best_score = 0.0;
    let mut best = None;
    for shift in shift_candidates(
        &rgba_crop(
            before,
            size,
            previous_bounds
                .expand(CONTEXT_RADIUS)
                .intersect(size.rect()),
        ),
        input.rgba,
        input.previous,
        size,
        previous_bounds,
    )? {
        let moved = translated(input.previous, size, shift);
        let Some(rect) = bounds(&moved, size) else {
            continue;
        };
        let window = rect.expand(TRACK_SMALL_REACH).intersect(size.rect());
        let ws = window.size();
        let near = dilate(
            &crop(&moved, size, window),
            ws.w,
            ws.h,
            TRACK_SMALL_REACH as usize,
        );
        let center_point = centroid(&moved, size, rect);
        let mut points: Vec<_> = positions(window)
            .zip(near)
            .filter_map(|((x, y), v)| (v != 0 && available[y * size.w + x] != 0).then_some((x, y)))
            .collect();
        points.sort_by(|&a, &b| distance(a, center_point).total_cmp(&distance(b, center_point)));
        let mut seeds = 0;
        for seed @ (x, y) in points {
            if available[y * size.w + x] == 0 {
                continue;
            }
            if let Some(result) = fill(state, seed, input.selection)? {
                clear_region(&mut available, size, &result);
                let ratio = area(result.region.as_deref().unwrap_or_default()) as f64
                    / previous_area as f64;
                if (1.0 / TRACK_MAX_AREA_RATIO..=TRACK_MAX_AREA_RATIO).contains(&ratio) {
                    let mut region = vec![0; target.len()];
                    for (x, y) in marked_pixels(&result) {
                        region[y * size.w + x] = 1;
                    }
                    if let Some(after_center) = center(&region, size) {
                        let after = context(input.rgba, &region, size, after_center);
                        let score = context_similarity(
                            &before_context.rgba,
                            &before_context.region,
                            before_context.dimensions,
                            before_context.origin,
                            &after.rgba,
                            &after.region,
                            after.dimensions,
                            after.origin,
                        )?;
                        if score > best_score {
                            best_score = score;
                            best = Some((result, (score - TRACK_CONTEXT_PENALTY).max(0.0), shift));
                        }
                    }
                }
            } else {
                available[y * size.w + x] = 0;
            }
            seeds += 1;
            if seeds >= TRACK_SMALL_SEEDS {
                break;
            }
        }
    }
    Ok(best)
}

/// `bucket_fill.track_fill_region_numpy` と同じ領域・重なり・手がかりを選ぶ。
/// 島の色置き換え用に、採用した各領域を個別に返す。
pub fn track_fill_region(
    input: TrackInput<'_>,
    options: PrepareOptions,
) -> Result<TrackOutcome, Error> {
    let (w, h) = input.dimensions;
    let size = Size { w, h };
    size.check(input.rgba, 4)?;
    size.check(input.previous, 1)?;
    if let Some(selection) = input.selection {
        size.check(selection, 1)?;
    }
    let mut outcome = TrackOutcome {
        score: None,
        members: Vec::new(),
        matched: None,
    };
    let Some(previous_bounds) = bounds(input.previous, size) else {
        return Ok(outcome);
    };
    let mut target = fill_target_mask(input.rgba, w, h, target_kind(&options, input.reference));
    if let Some(selection) = input.selection {
        for (value, selected) in target.iter_mut().zip(selection) {
            *value &= *selected;
        }
    }
    let mut references = Vec::new();
    let before = input.previous_rgba.filter(|p| p.len() == input.rgba.len());
    let before_lines = input.previous_lines.map(Cow::Borrowed).or_else(|| {
        before.map(|rgba| {
            let mut lines = fill_target_mask(rgba, w, h, target_kind(&options, input.reference));
            invert(&mut lines);
            Cow::Owned(lines)
        })
    });
    if let Some(lines) = before_lines.filter(|lines| lines.len() == target.len()) {
        let mut current_lines = target.clone();
        invert(&mut current_lines);
        let shifts = estimate_shifts(&lines, &current_lines, input.previous, w, h, 3, None)?;
        if let Some(warped) = warp_region(
            &lines,
            &current_lines,
            input.previous,
            w,
            h,
            shifts.first().copied().unwrap_or((0, 0)),
            (0, 0),
        )? {
            references.push(Reference::new(
                Cow::Owned(warped),
                TrackMatch::Previous,
                size,
            ));
        }
        for shift in shifts.into_iter().filter(|&shift| shift != (0, 0)) {
            references.push(Reference::new(
                Cow::Owned(translated(input.previous, size, shift)),
                TrackMatch::Shifted(shift),
                size,
            ));
        }
    }
    references.push(Reference::new(
        Cow::Borrowed(input.previous),
        TrackMatch::Previous,
        size,
    ));
    let previous_area = references.last().map_or(0, |r| r.area);
    let mut eligible = vec![0; target.len()];
    for reference in &references {
        if let Some(rect) = reference.bounds {
            for y in rect.y0 as usize..rect.y1 as usize {
                let row = y * w + rect.x0 as usize..y * w + rect.x1 as usize;
                for ((out, &t), &r) in eligible[row.clone()]
                    .iter_mut()
                    .zip(&target[row.clone()])
                    .zip(&reference.mask[row])
                {
                    *out |= t & r;
                }
            }
        }
    }
    let mut state = BucketState::new(input.rgba, w, h, options, false).map_err(Error::Invalid)?;
    let (members, score, index) = pick_overlap(
        eligible,
        &references,
        previous_bounds,
        &input,
        size,
        &mut state,
    )?;
    if let Some(before) =
        before.filter(|_| previous_area <= TRACK_SMALL_AREA && score < TRACK_GOOD_IOU)
    {
        if let Some((mut result, rescued_score, shift)) = small_region(
            &input,
            before,
            &target,
            size,
            previous_bounds,
            previous_area,
            &mut state,
        )? {
            if rescued_score > score {
                outcome.score = Some(rescued_score);
                if rescued_score >= TRACK_MIN_IOU {
                    state.add_islands(&mut result, input.selection)?;
                    outcome.members.push(result);
                    outcome.matched = Some(TrackMatch::Shifted(shift));
                }
                return Ok(outcome);
            }
        }
    }
    if score >= TRACK_MIN_IOU {
        outcome.members = members;
        for member in &mut outcome.members {
            state.add_islands(member, input.selection)?;
        }
        let reference = references.swap_remove(index);
        outcome.matched = Some(match reference.kind {
            TrackMatch::Previous if matches!(reference.mask, Cow::Owned(_)) => {
                TrackMatch::Warped(reference.mask.into_owned())
            }
            kind => kind,
        });
    }
    outcome.score = Some(score);
    Ok(outcome)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fill::region::{Enclosed, FillOptions};

    #[test]
    fn guess_breaks_count_ties_by_sorted_rgb_key() {
        let current = [100, 20, 20, 255, 20, 100, 20, 255, 0, 0, 0, 255];
        let before = [255; 12];
        assert_eq!(
            guess_target_categories(&current, &[1, 1, 1], &before, 3, 1).unwrap(),
            [Some([20, 100, 20])]
        );
    }

    fn options() -> PrepareOptions {
        PrepareOptions {
            target_color: TargetColor::All(None),
            extend_lines: false,
            extend_length: 4,
            close_gap: false,
            gap_width: 1,
            island_replacements: Vec::new(),
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

    fn split_row(previous: &[u8], current: &[u8]) -> TrackOutcome {
        let rgba: Vec<_> = current
            .iter()
            .flat_map(|v| [*v * 255, *v * 255, *v * 255, 255])
            .collect();
        track_fill_region(
            TrackInput {
                rgba: &rgba,
                previous,
                previous_rgba: None,
                previous_lines: None,
                selection: None,
                dimensions: (previous.len(), 1),
                reference: None,
            },
            options(),
        )
        .unwrap()
    }

    #[test]
    fn union_keeps_candidate_order_and_row_major_ties() {
        let outcome = split_row(&[1, 1, 1, 1, 1], &[1, 1, 0, 1, 1]);
        assert_eq!(outcome.score, Some(4.0 / 5.0));
        assert_eq!(outcome.members.len(), 2);
        assert_eq!(outcome.members[0].bounds, Some((0, 0, 2, 1)));
        assert_eq!(outcome.members[1].bounds, Some((3, 0, 5, 1)));
    }

    #[test]
    fn good_overlap_stops_before_union() {
        let outcome = split_row(&[1; 10], &[1, 1, 1, 1, 1, 1, 1, 1, 0, 1]);
        assert_eq!(outcome.score, Some(0.8));
        assert_eq!(outcome.members.len(), 1);
        assert_eq!(outcome.members[0].bounds, Some((0, 0, 8, 1)));
    }

    #[test]
    fn rescue_uses_color_context_with_no_line_clue() {
        let (w, h) = (54, 42);
        let mut previous = vec![0; w * h];
        let mut before = vec![0; w * h * 4];
        let mut generator = 702_u32;
        for (i, rgba) in before.chunks_exact_mut(4).enumerate() {
            generator = generator.wrapping_mul(1664525).wrapping_add(1013904223);
            let (x, y) = (i % w, i / w);
            let p = if (18..20).contains(&x) && (17..19).contains(&y) {
                previous[i] = 1;
                [229, 59, 70, 255]
            } else if generator >> 31 == 0 {
                [210, 180, 150, 255]
            } else {
                [75, 120, 110, 255]
            };
            rgba.copy_from_slice(&p);
        }
        let mut after = vec![0; before.len()];
        for (i, rgba) in before.chunks_exact(4).enumerate() {
            let j = (i / w * w + (i % w + 10) % w) * 4;
            after[j..j + 4].copy_from_slice(rgba);
        }
        let outcome = track_fill_region(
            TrackInput {
                rgba: &after,
                previous: &previous,
                previous_rgba: Some(&before),
                previous_lines: Some(&vec![0; w * h]),
                selection: None,
                dimensions: (w, h),
                reference: Some([229, 59, 70]),
            },
            options(),
        )
        .unwrap();
        assert_eq!(outcome.score, Some(0.9));
        assert_eq!(outcome.members[0].bounds, Some((28, 17, 30, 19)));
        assert!(matches!(
            outcome.matched,
            Some(TrackMatch::Shifted((0, 10)))
        ));
    }
}
