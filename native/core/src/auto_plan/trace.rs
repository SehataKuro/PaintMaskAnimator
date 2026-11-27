//! 色トレス線の仕上げ色を、線の色とまわりの面の組み合わせから覚える（Python の
//! `auto_trace`）。値は透明度も含めた画素の値のまま扱う。
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::rc::Rc;

use crate::reference_check::Bounds;
use crate::reference_scan::Rgb;
use crate::reference_scan::{pack, to_rgb};
use crate::regions::dilate;

use super::alignment::{Alignment, Halt};
use super::cell::{count, is_primary, majority, paintable, Cell, Shifted, GAP_VALUE};

const LARGE_FACE_RATIO: usize = 3;
const MIN_LARGE_FACE: usize = 32;
const MAX_LARGE_FACE_THRESHOLD: usize = 256;
pub(crate) const MIN_FINISH_EVIDENCE: usize = 6;
const HINT_SHARE: f64 = 0.9;
const FILL_SHARE: f64 = 0.75;
const MIN_COLOR_LINE_OVERLAP: f64 = 0.5;

/// 元の色トレス線の色 → 仕上げ色と画素数（`finish_palette`）。
pub(crate) type FinishPalette = HashMap<Option<Rgb>, HashMap<u32, usize>>;
/// 線に接する大きな面の色。
pub(crate) type Hosts = BTreeSet<u32>;
/// 見本1枚の用例 `(線の色, まわりの面, 仕上げ色, 画素数)`（`palette`）。
pub(crate) type Rules = Vec<(Option<Rgb>, Hosts, u32, usize)>;
/// 用例を数える鍵 `(線の色, まわりの面, 仕上げ色)`。
type RuleKey = (Option<Rgb>, Hosts, u32);
/// 線と小さな内側の面が同色の用例（`enclosed_face_rules`）。
pub(crate) type FaceRules = HashSet<(Option<Rgb>, u32)>;

/// 部分の値ごとの、塗った値の最多と割合。
pub(crate) type FaceCache = HashMap<usize, (Option<u32>, f64)>;

fn label_mask_values(cell: &Cell, label: usize, values: &[u32]) -> Vec<u32> {
    let (x0, y0, x1, _) = cell.regions.bounds[label];
    let local = cell.regions.local_mask(label);
    let lw = x1 - x0;
    local
        .iter()
        .enumerate()
        .filter(|(_, &v)| v != 0)
        .map(|(i, _)| values[(i / lw + y0) * cell.width + i % lw + x0])
        .collect()
}

fn mask_any(cell: &Cell, label: usize, mask: &[u8]) -> bool {
    let (x0, y0, x1, _) = cell.regions.bounds[label];
    let lw = x1 - x0;
    cell.regions
        .local_mask(label)
        .iter()
        .enumerate()
        .any(|(i, &v)| v != 0 && mask[(i / lw + y0) * cell.width + i % lw + x0] != 0)
}

/// 主線や色トレス線にかかる部分の番号（小さい順）。
fn primary_labels(cell: &Cell) -> Vec<usize> {
    let labels: BTreeSet<usize> = cell
        .primary
        .iter()
        .zip(&cell.regions.labels)
        .filter(|(&p, _)| p != 0)
        .map(|(_, &l)| l as usize)
        .collect();
    labels.into_iter().collect()
}

/// 部分を `margin` 画素広げた窓に置いたマスクと、その窓。
fn padded(cell: &Cell, bounds: Bounds, local: &[u8], margin: usize) -> (Bounds, Vec<u8>) {
    let (x0, y0, x1, y1) = bounds;
    let area = (
        x0.saturating_sub(margin),
        y0.saturating_sub(margin),
        cell.width.min(x1 + margin),
        cell.height.min(y1 + margin),
    );
    let aw = area.2 - area.0;
    let mut out = vec![0u8; aw * (area.3 - area.1)];
    let lw = x1 - x0;
    for (i, &v) in local.iter().enumerate() {
        out[(i / lw + y0 - area.1) * aw + i % lw + x0 - area.0] = v;
    }
    (area, out)
}

/// `finish_palette`：選んだ見本の元の色トレス線が何色で仕上げられたか。
pub(crate) fn finish_palette(guides: &[Rc<Cell>]) -> FinishPalette {
    let mut colors: FinishPalette = HashMap::new();
    for guide in guides {
        let Some(geometry) = guide.initial.as_deref() else {
            continue;
        };
        let known: Vec<bool> = (0..geometry.values.len())
            .map(|i| geometry.primary[i] != 0 && guide.open[i] == 0 && guide.undecided[i] == 0)
            .collect();
        let originals: BTreeSet<u32> = known
            .iter()
            .enumerate()
            .filter(|(_, &k)| k)
            .map(|(i, _)| geometry.values[i])
            .collect();
        for original in originals {
            let votes = colors.entry(to_rgb(original)).or_default();
            for (i, &k) in known.iter().enumerate() {
                if k && geometry.values[i] == original && paintable(guide.values[i]) {
                    *votes.entry(guide.values[i]).or_default() += 1;
                }
            }
        }
    }
    for votes in colors.values_mut() {
        votes.retain(|_, n| *n >= MIN_FINISH_EVIDENCE);
    }
    colors
}

/// `small_face_finish`：囲みの内側が決まっていれば、その仕上げ色を選ぶ。
pub(crate) fn small_face_finish(
    geometry: &Cell,
    bounds: Bounds,
    local: &[u8],
    painted: &[u32],
    allowed: &dyn Fn(u32) -> bool,
    mut cache: Option<&mut FaceCache>,
) -> Option<Rgb> {
    let (area, grown_local) = padded(geometry, bounds, local, 2);
    let (aw, ah) = (area.2 - area.0, area.3 - area.1);
    let grown = dilate(&grown_local, aw, ah, 2);
    let mut labels = BTreeSet::new();
    for (i, (&g, &l)) in grown.iter().zip(&grown_local).enumerate() {
        if g != 0 && l == 0 {
            labels.insert(
                geometry.regions.labels[(i / aw + area.1) * geometry.width + i % aw + area.0]
                    as usize,
            );
        }
    }
    let mut answers = BTreeSet::new();
    for label in labels {
        if geometry.region_value(label) != 0 || geometry.regions.sizes[label] < 3 {
            continue;
        }
        let answer = match cache.as_deref_mut().and_then(|c| c.get(&label).copied()) {
            Some(answer) => answer,
            None => {
                let answer = majority(label_mask_values(geometry, label, painted));
                if let Some(cache) = cache.as_deref_mut() {
                    cache.insert(label, answer);
                }
                answer
            }
        };
        if let (Some(value), share) = answer {
            if allowed(value) && share >= HINT_SHARE {
                answers.insert(value);
            }
        }
    }
    if answers.len() == 1 {
        to_rgb(*answers.iter().next().expect("answer"))
    } else {
        None
    }
}

/// `enclosed_face_rules`。
pub(crate) fn enclosed_face_rules(guides: &[Rc<Cell>], finishes: &FinishPalette) -> FaceRules {
    let mut rules = FaceRules::new();
    for guide in guides {
        let Some(geometry) = guide.initial.as_deref() else {
            continue;
        };
        let mut faces = HashMap::new();
        for label in primary_labels(geometry) {
            let rgb = to_rgb(geometry.region_value(label));
            let bounds = geometry.regions.bounds[label];
            if mask_any(geometry, label, &guide.open) || mask_any(geometry, label, &guide.undecided)
            {
                continue;
            }
            let (value, share) = majority(label_mask_values(geometry, label, &guide.values));
            let Some(value) = value else {
                continue;
            };
            if !finishes.get(&rgb).is_some_and(|f| f.contains_key(&value)) || share < HINT_SHARE {
                continue;
            }
            let local = geometry.regions.local_mask(label);
            if large_face_colors(geometry, bounds, &local, &guide.values, &mut faces)
                .contains(&value)
            {
                continue;
            }
            if small_face_finish(
                geometry,
                bounds,
                &local,
                &guide.values,
                &|v| v == value,
                None,
            )
            .is_some()
            {
                rules.insert((rgb, value));
            }
        }
    }
    rules
}

/// `enclosed_face_color`：見本と同じ用途の色トレス線に囲まれた小さな面の色。
pub(crate) fn enclosed_face_color(
    geometry: &Cell,
    bounds: Bounds,
    local: &[u8],
    target: Option<Rgb>,
    line_finishes: &HashMap<usize, u32>,
    rules: &FaceRules,
) -> Option<Rgb> {
    let (x0, y0, x1, y1) = bounds;
    if target.is_some()
        || count(local) > 4096
        || x0 == 0
        || y0 == 0
        || x1 == geometry.width
        || y1 == geometry.height
    {
        return None;
    }
    let (area, around) = padded(geometry, bounds, local, 1);
    let (aw, ah) = (area.2 - area.0, area.3 - area.1);
    let grown = dilate(&around, aw, ah, 1);
    let mut counts: BTreeMap<usize, usize> = BTreeMap::new();
    let mut ring = 0usize;
    for (i, (&g, &l)) in grown.iter().zip(&around).enumerate() {
        if g != 0 && l == 0 {
            ring += 1;
            let label =
                geometry.regions.labels[(i / aw + area.1) * geometry.width + i % aw + area.0];
            *counts.entry(label as usize).or_default() += 1;
        }
    }
    let mut votes: Vec<(u32, usize)> = Vec::new();
    for (label, n) in counts {
        let Some(&value) = line_finishes.get(&label) else {
            continue;
        };
        let original = to_rgb(geometry.region_value(label));
        if rules.contains(&(original, value)) {
            match votes.iter_mut().find(|(v, _)| *v == value) {
                Some(entry) => entry.1 += n,
                None => votes.push((value, n)),
            }
        }
    }
    let threshold = 3usize.max((ring as f64 * 0.35) as usize);
    let answers: Vec<u32> = votes
        .into_iter()
        .filter(|(_, n)| *n >= threshold)
        .map(|(v, _)| v)
        .collect();
    if answers.len() == 1 {
        to_rgb(answers[0])
    } else {
        None
    }
}

/// `large_face_colors`：線に接する大きな面の色。線の仕上げ色には使わない。
pub(crate) fn large_face_colors(
    geometry: &Cell,
    bounds: Bounds,
    local: &[u8],
    painted: &[u32],
    cache: &mut HashMap<usize, Option<u32>>,
) -> Hosts {
    let (area, around) = padded(geometry, bounds, local, 2);
    let (aw, ah) = (area.2 - area.0, area.3 - area.1);
    let grown = dilate(&around, aw, ah, 2);
    let mut neighbours = BTreeSet::new();
    for (i, (&g, &l)) in grown.iter().zip(&around).enumerate() {
        let at = (i / aw + area.1) * geometry.width + i % aw + area.0;
        if g != 0 && l == 0 && geometry.boundary[at] == 0 {
            neighbours.insert(geometry.regions.labels[at] as usize);
        }
    }
    let size = count(local);
    let minimum = MIN_LARGE_FACE.max(MAX_LARGE_FACE_THRESHOLD.min(size * LARGE_FACE_RATIO)) as f64;
    let largest = neighbours
        .iter()
        .filter(|&&label| geometry.region_value(label) != GAP_VALUE)
        .map(|&label| geometry.regions.sizes[label])
        .max()
        .unwrap_or(0);
    let minimum = minimum.max(largest as f64 * 0.25);
    let mut colors = Hosts::new();
    for label in neighbours {
        if (geometry.regions.sizes[label] as f64) < minimum
            || geometry.region_value(label) == GAP_VALUE
        {
            continue;
        }
        let found = *cache.entry(label).or_insert_with(|| {
            let (value, share) = majority(label_mask_values(geometry, label, painted));
            value.filter(|&v| share >= FILL_SHARE && paintable(v))
        });
        if let Some(value) = found {
            colors.insert(value);
        }
    }
    colors
}

/// `palette`：見本の元の色トレス線が、どのまわりでどの色に仕上げられたか。
pub(crate) fn palette(guide: &Cell) -> Rules {
    let Some(geometry) = guide.initial.as_deref() else {
        return Vec::new();
    };
    let mut rules: Vec<(RuleKey, usize)> = Vec::new();
    let mut faces = HashMap::new();
    for label in primary_labels(geometry) {
        let rgb = to_rgb(geometry.region_value(label));
        if !rgb.is_some_and(is_primary) {
            continue;
        }
        if mask_any(geometry, label, &guide.open) || mask_any(geometry, label, &guide.undecided) {
            continue;
        }
        let bounds = geometry.regions.bounds[label];
        let local = geometry.regions.local_mask(label);
        let hosts = large_face_colors(geometry, bounds, &local, &guide.values, &mut faces);
        let sample = label_mask_values(geometry, label, &guide.values);
        let mut counts: BTreeMap<u32, usize> = BTreeMap::new();
        for &value in &sample {
            *counts.entry(value).or_default() += 1;
        }
        let candidates: Vec<(u32, usize)> = counts
            .into_iter()
            .filter(|(v, _)| !hosts.contains(v) && paintable(*v))
            .collect();
        let Some(&(color, n)) = candidates.iter().rev().max_by_key(|(_, n)| *n) else {
            continue;
        };
        // 同数なら小さい値（max は先のもの）。rev と max_by_key（最後の最大）で先のものになる。
        if n < 3.min(sample.len()) || (n as f64) < sample.len() as f64 * 0.2 {
            continue;
        }
        let all: usize = candidates.iter().map(|(_, n)| n).sum();
        if (n as f64) < all as f64 * 0.8 {
            continue;
        }
        let key = (rgb, hosts, color);
        match rules.iter_mut().find(|(k, _)| *k == key) {
            Some(entry) => entry.1 += n,
            None => rules.push((key, n)),
        }
    }
    rules
        .into_iter()
        .map(|((rgb, hosts, color), n)| (rgb, hosts, color, n))
        .collect()
}

/// `candidates`：まわりが一番よく合う用例の仕上げ色と画素数。
pub(crate) fn candidates(
    trace: Option<Rgb>,
    hosts: &Hosts,
    palettes: &[Rules],
    minimum: usize,
) -> HashMap<u32, usize> {
    let mut contextual = Vec::new();
    for rules in palettes {
        for (rgb, context, color, n) in rules {
            if *rgb != trace || hosts.contains(color) {
                continue;
            }
            let shared = hosts.intersection(context).count();
            if !hosts.is_empty() && !context.is_empty() && shared == 0 {
                continue;
            }
            let score = if hosts.is_empty() {
                0.0
            } else {
                shared as f64 / hosts.union(context).count() as f64
            };
            contextual.push((score, *color, *n));
        }
    }
    let best = contextual.iter().map(|c| c.0).fold(0.0, f64::max);
    let mut votes: HashMap<u32, usize> = HashMap::new();
    for (score, color, n) in contextual {
        if score == best {
            *votes.entry(color).or_default() += n;
        }
    }
    votes.retain(|_, n| *n >= minimum);
    votes
}

/// `local_finish`：同じ色の元の色トレス線の上の仕上げ色。
#[allow(clippy::too_many_arguments)]
pub(crate) fn local_finish(
    target: &Rc<Cell>,
    guide: &Cell,
    bounds: Bounds,
    local: &[u8],
    trace: Option<Rgb>,
    margin: usize,
    alignment: &mut Alignment,
    allowed: &HashSet<u32>,
) -> Result<Option<Rgb>, Halt> {
    let Some(geometry) = guide.initial.as_ref() else {
        return Ok(None);
    };
    if allowed.is_empty() {
        return Ok(None);
    }
    let (shift, score) = alignment.shift(target, geometry, bounds, margin)?;
    if score < MIN_COLOR_LINE_OVERLAP {
        return Ok(None);
    }
    let shifted = Shifted::new(target.width, target.height, local, bounds, shift);
    let (width, height) = (geometry.width as i64, geometry.height as i64);
    let code = trace.map_or(0, pack);
    let size = shifted.sy.len();
    let mut selected = vec![0u32; size];
    let mut found = vec![false; size];
    for (dy, dx) in offsets(2) {
        for i in 0..size {
            if !shifted.inside[i] || found[i] {
                continue;
            }
            let (y, x) = (shifted.sy[i] as i64 + dy, shifted.sx[i] as i64 + dx);
            if y < 0 || y >= height || x < 0 || x >= width {
                continue;
            }
            let at = y as usize * geometry.width + x as usize;
            if geometry.values[at] != code {
                continue;
            }
            found[i] = true;
            if guide.open[at] == 0 && guide.undecided[at] == 0 {
                selected[i] = guide.values[at];
            }
        }
    }
    let values: Vec<u32> = selected
        .into_iter()
        .filter(|v| allowed.contains(v))
        .collect();
    if values.len() < 2usize.max((size as f64 * 0.35) as usize) {
        return Ok(None);
    }
    let (value, share) = majority(values);
    Ok(if share >= 0.9 {
        value.and_then(to_rgb)
    } else {
        None
    })
}

/// 近い順（同じ距離なら `(dy, dx)` の小さい順）のずれ。
pub(crate) fn offsets(radius: i64) -> Vec<(i64, i64)> {
    let mut offsets: Vec<(i64, i64)> = (-radius..=radius)
        .flat_map(|dy| (-radius..=radius).map(move |dx| (dy, dx)))
        .collect();
    offsets.sort_by_key(|&(dy, dx)| (dy * dy + dx * dx, dy, dx));
    offsets
}

/// `choose`：近くで照合できた線を優先し、無ければ同じまわりの仕上げ色。
pub(crate) fn choose(
    trace: Option<Rgb>,
    hosts: &Hosts,
    palettes: &[Rules],
    direct: &[Option<Rgb>],
    finishes: &FinishPalette,
) -> Option<Rgb> {
    let votes = candidates(trace, hosts, palettes, MIN_FINISH_EVIDENCE);
    let mut direct_votes: HashSet<u32> =
        candidates(trace, hosts, palettes, 1).into_keys().collect();
    let finish = finishes.get(&trace);
    if let Some(finish) = finish {
        direct_votes.extend(finish.keys().filter(|v| !hosts.contains(v)));
    }
    let accepted: Vec<u32> = direct
        .iter()
        .flatten()
        .map(|&rgb| pack(rgb))
        .filter(|v| direct_votes.contains(v))
        .collect();
    if !accepted.is_empty() && accepted.iter().all(|v| *v == accepted[0]) {
        return to_rgb(accepted[0]);
    }
    if accepted.is_empty() && votes.len() == 1 {
        return to_rgb(*votes.keys().next().expect("vote"));
    }
    if accepted.is_empty() {
        let remaining: Vec<u32> = finish
            .map(|f| f.keys().filter(|v| !hosts.contains(v)).copied().collect())
            .unwrap_or_default();
        if remaining.len() == 1 {
            return to_rgb(remaining[0]);
        }
    }
    None
}
