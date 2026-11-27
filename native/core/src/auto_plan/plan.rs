//! 塗り進める順と見本の選び方（Python の `auto_paint._plan_auto_paint` のバケツの経路）と、
//! ドキュメントのレイヤーを塗る入口（`AutoPaintController.run`）。
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::rc::Rc;

use crate::document::access::ImageField;
use crate::document::drawing_commands::DrawingTarget;
use crate::document::model::Document;
use crate::image::Rect;
use crate::project::qt_alpha::qt_premultiplied_roundtrip;
use crate::reference_scan::Rgb;
use crate::reference_scan::{cell_image, layer_cells, Finding, Progress};
use crate::tools::bucket::qt_pixels;
use crate::Error;

use super::alignment::Alignment;
use super::cell::{line_only, white_as_unpainted, Cell};
use super::session::{AutoBucketOptions, Fill, ReferenceSessions};
use super::trace::{self, FaceRules, FinishPalette, Rules};
use super::tracker::{Cells, PartTracker};
use super::work::{paint_cell, BucketWork, Shared, TraceContext};
use super::{apply_paint, CellPaint};

/// 見本から部位の手本にしてよいセルの距離。
const TEACHER_REACH: usize = 2;
const CONFIDENT_UNSURE_SHARE: f64 = 0.1;
/// 塗り直しを含めた回数の上限。
pub const MAX_ROUNDS: usize = 3;
const SEARCH_MARGIN: usize = 48;

/// 自動塗りの計画（Python の `auto_paint.AutoPlan`）。セルの番号は渡した並びの位置。
#[derive(Clone, Debug, Default, PartialEq)]
pub struct AutoPlan {
    pub cells: BTreeMap<usize, CellPaint>,
    /// 見本に使ったセル（最初の見本と途中で加えたセル）。
    pub references: Vec<usize>,
    /// 塗った回数。
    pub rounds: usize,
}

/// `_order`：見本から近い順に `(セル, 見本の側の隣)`。
fn order(count: usize, references: &BTreeSet<usize>) -> Vec<(usize, usize)> {
    let nearest = |index: usize| {
        *references
            .iter()
            .min_by_key(|&&r| (r.abs_diff(index), r))
            .expect("見本")
    };
    let mut items: Vec<(usize, usize)> = (0..count)
        .filter(|i| !references.contains(i))
        .map(|i| (i.abs_diff(nearest(i)), i))
        .collect();
    items.sort_unstable();
    items
        .into_iter()
        .map(|(_, i)| (i, if i > nearest(i) { i - 1 } else { i + 1 }))
        .collect()
}

/// 塗りを持つ回の状態（`made`・`sources`・`undecided_*`・`part_paints`）と、回をまたぐ
/// 見本・塗るセルの部分分け。
struct Guides {
    width: usize,
    height: usize,
    cells: Vec<Rc<Vec<u8>>>,
    lines: Option<Vec<Option<Rc<Vec<u8>>>>>,
    fixed: BTreeSet<usize>,
    targets: HashMap<usize, Rc<Cell>>,
    fixed_guides: HashMap<usize, Rc<Cell>>,
    made: HashMap<(bool, usize), Rc<Cell>>,
    current: HashMap<usize, Rc<Vec<u8>>>,
    previous: HashMap<usize, Rc<Vec<u8>>>,
    paints_now: HashMap<usize, Rc<CellPaint>>,
    paints_before: HashMap<usize, Rc<CellPaint>>,
}

impl Guides {
    fn line(&self, index: usize) -> Option<Rc<Vec<u8>>> {
        self.lines.as_ref().and_then(|lines| lines[index].clone())
    }

    fn target_for(&mut self, index: usize) -> Rc<Cell> {
        if let Some(found) = self.targets.get(&index) {
            return found.clone();
        }
        let cell = Rc::new(Cell::new(
            self.cells[index].clone(),
            self.width,
            self.height,
            true,
        ));
        self.targets.insert(index, cell.clone());
        cell
    }

    fn guide(&mut self, now: bool, index: usize) -> Rc<Cell> {
        if self.fixed.contains(&index) {
            if let Some(found) = self.fixed_guides.get(&index) {
                return found.clone();
            }
            let mut cell = Cell::new(self.cells[index].clone(), self.width, self.height, false);
            cell.index = Some(index);
            if let Some(line) = self.line(index) {
                cell.initial = Some(Rc::new(Cell::new(line, self.width, self.height, false)));
            }
            let cell = Rc::new(cell);
            self.fixed_guides.insert(index, cell.clone());
            return cell;
        }
        if let Some(found) = self.made.get(&(now, index)) {
            return found.clone();
        }
        let source = if now { &self.current } else { &self.previous }[&index].clone();
        let mut cell = Cell::new(source, self.width, self.height, false);
        if self.line(index).is_some() {
            cell.initial = Some(self.target_for(index));
        }
        let paint = if now {
            &self.paints_now
        } else {
            &self.paints_before
        }
        .get(&index)
        .cloned();
        if let Some(paint) = &paint {
            cell.mark_undecided(&paint.undecided, &paint.unpainted);
        }
        cell.index = Some(index);
        let mut confirmed = vec![0u8; self.width * self.height];
        for item in paint.iter().flat_map(|p| p.paints.iter()) {
            let (x0, y0, x1, _) = item.bounds;
            let lw = x1 - x0;
            for (i, &v) in item.mask.iter().enumerate() {
                confirmed[(i / lw + y0) * self.width + i % lw + x0] |= v;
            }
        }
        cell.confirmed = Some(confirmed);
        let cell = Rc::new(cell);
        self.made.insert((now, index), cell.clone());
        cell
    }
}

impl Cells for Guides {
    fn geometry(&mut self, index: usize) -> Result<Rc<Cell>, Error> {
        if self.fixed.contains(&index) {
            let guide = self.guide(true, index);
            return Ok(guide.initial.clone().unwrap_or(guide));
        }
        Ok(self.target_for(index))
    }

    fn guide(&mut self, index: usize) -> Result<Rc<Cell>, Error> {
        let now = self.current.contains_key(&index);
        Ok(Guides::guide(self, now, index))
    }
}

/// `plan_auto_paint`：`references` の見本から前後へ塗り進める。
///
/// `cells` はセルごとの RGBA8888（同じ大きさ）、`lines` はセルごとの元の線画。
/// 不透明な白は透明と同じ「まだ塗っていない所」として扱う。中止したら None。
pub fn plan_auto_paint(
    cells: &[Vec<u8>],
    width: usize,
    height: usize,
    references: &[usize],
    lines: Option<&[Option<Vec<u8>>]>,
    options: &AutoBucketOptions,
    progress: &mut Progress<'_>,
) -> Result<Option<AutoPlan>, Error> {
    let fixed: BTreeSet<usize> = references.iter().copied().collect();
    if fixed.is_empty() || fixed.iter().any(|&i| i >= cells.len()) {
        return Err(Error::Invalid("見本がセルの並びの外です".into()));
    }
    if cells.iter().any(|c| c.len() != width * height * 4)
        || lines.is_some_and(|l| {
            l.len() != cells.len() || l.iter().flatten().any(|c| c.len() != width * height * 4)
        })
    {
        return Err(Error::Invalid("セルの大きさがそろっていません".into()));
    }
    let cells: Vec<Rc<Vec<u8>>> = cells
        .iter()
        .map(|c| Rc::new(white_as_unpainted(c)))
        .collect();
    let lines: Option<Vec<Option<Rc<Vec<u8>>>>> = lines.map(|l| {
        l.iter()
            .map(|c| c.as_ref().map(|c| Rc::new(white_as_unpainted(c))))
            .collect()
    });
    let mut plan = AutoPlan::default();
    let usable = cells.len();
    let work = order(cells.len(), &fixed);
    let mut guides = Guides {
        width,
        height,
        cells: cells.clone(),
        lines,
        fixed: fixed.clone(),
        targets: HashMap::new(),
        fixed_guides: HashMap::new(),
        made: HashMap::new(),
        current: HashMap::new(),
        previous: fixed.iter().map(|&i| (i, cells[i].clone())).collect(),
        paints_now: HashMap::new(),
        paints_before: HashMap::new(),
    };
    let mut references = fixed.clone();
    let mut shared = Shared {
        alignment: Alignment::new(),
        references: ReferenceSessions::new(),
        options: options.clone(),
    };
    let mut bucket_work: HashMap<usize, BucketWork> = HashMap::new();
    let mut tracker = PartTracker::new(width, height);
    let mut trace_state: Option<(FinishPalette, Vec<Rules>, FaceRules)> = None;
    let mut painted: BTreeMap<usize, Rc<CellPaint>> = BTreeMap::new();
    let total = work.len() * MAX_ROUNDS;
    let mut done = 0;
    for _round in 0..MAX_ROUNDS {
        plan.rounds += 1;
        guides.current = fixed.iter().map(|&i| (i, cells[i].clone())).collect();
        guides.made.clear();
        guides.paints_now.clear();
        guides.paints_before = painted.iter().map(|(&i, p)| (i, p.clone())).collect();
        let teachers: BTreeMap<usize, Rc<Cell>> =
            fixed.iter().map(|&i| (i, guides.guide(true, i))).collect();
        let teacher_list: Vec<Rc<Cell>> = teachers.values().cloned().collect();
        if trace_state.is_none() {
            let palette = trace::finish_palette(&teacher_list);
            let contexts: Vec<Rules> = teacher_list.iter().map(|t| trace::palette(t)).collect();
            let rules = trace::enclosed_face_rules(&teacher_list, &palette);
            for (&index, teacher) in &teachers {
                tracker.remember_fixed(index, teacher);
            }
            trace_state = Some((palette, contexts, rules));
        }
        let (palette, contexts, rules) = trace_state.as_ref().expect("色トレスの表");
        let trace_context = TraceContext {
            palette,
            contexts,
            rules,
        };
        for &(index, toward) in &work {
            if !progress.step(done, total) {
                return Ok(None);
            }
            done += 1;
            let nearest = *fixed
                .iter()
                .min_by_key(|&&r| (r.abs_diff(index), r))
                .expect("見本");
            let teacher = teachers[&nearest].clone();
            let mut cell_guides: Vec<Rc<Cell>> = Vec::new();
            if guides.current.contains_key(&toward) {
                cell_guides.push(guides.guide(true, toward));
            }
            // 前の回の塗り：反対側の隣と、近くの見本（両側）。
            let mut extra: Vec<usize> = Vec::new();
            let side = 2 * index as i64 - toward as i64;
            if side >= 0 && guides.previous.contains_key(&(side as usize)) {
                extra.push(side as usize);
            }
            for direction in [-1i64, 1] {
                let near = references
                    .iter()
                    .filter(|&&r| (r as i64 - index as i64) * direction > 0)
                    .min_by_key(|&&r| r.abs_diff(index));
                if let Some(&near) = near {
                    extra.push(near);
                }
            }
            let mut seen = BTreeSet::new();
            for other in extra {
                if !seen.insert(other) {
                    continue;
                }
                if fixed.contains(&other) {
                    let candidate = &teachers[&other];
                    if index.abs_diff(other) <= TEACHER_REACH
                        && !cell_guides.iter().any(|g| Rc::ptr_eq(g, candidate))
                    {
                        cell_guides.push(candidate.clone());
                    }
                } else if other != toward {
                    cell_guides.push(guides.guide(false, other));
                }
            }
            if !cell_guides.iter().any(|g| Rc::ptr_eq(g, &teacher))
                && index.abs_diff(nearest) <= TEACHER_REACH
            {
                cell_guides.push(teacher.clone());
            }
            let mut part_indices: Vec<usize> = guides
                .current
                .keys()
                .chain(guides.previous.keys())
                .copied()
                .collect::<BTreeSet<usize>>()
                .into_iter()
                .filter(|&other| {
                    !fixed.contains(&other)
                        && other != index
                        && other.abs_diff(index) <= 4
                        && cell_guides.iter().all(|g| g.index != Some(other))
                })
                .collect();
            part_indices.sort_by_key(|&other| (other.abs_diff(index), other));
            part_indices.truncate(2);
            let part_guides: Vec<Rc<Cell>> = part_indices
                .iter()
                .map(|&other| {
                    let now = guides.current.contains_key(&other);
                    guides.guide(now, other)
                })
                .collect();
            let target = guides.target_for(index);
            let cell_work = bucket_work
                .entry(index)
                .or_insert_with(|| BucketWork::new(target.clone(), options));
            if cell_work.collect(options)?.len() >= 16 {
                tracker.prime_motion(index, &cell_guides, &mut guides)?;
            }
            let mut tracked =
                |filled: &Fill, alignment: &mut Alignment| -> Result<Option<Rgb>, Error> {
                    tracker.color(index, &target, filled, alignment, &mut guides)
                };
            let paint = paint_cell(
                cell_work,
                &mut shared,
                &cell_guides,
                &part_guides,
                &trace_context,
                SEARCH_MARGIN,
                &mut tracked,
            )?;
            let paint = Rc::new(paint);
            painted.insert(index, paint.clone());
            guides.paints_now.insert(index, paint.clone());
            guides
                .current
                .insert(index, Rc::new(apply_paint(&cells[index], width, &paint)));
            tracker.remember(index, &paint.paints);
        }
        guides.previous = guides.current.clone();
        let confident: BTreeSet<usize> = painted
            .iter()
            .filter(|(_, p)| p.unsure_share() <= CONFIDENT_UNSURE_SHARE)
            .map(|(&i, _)| i)
            .collect();
        let added: Vec<usize> = confident.difference(&references).copied().collect();
        references.extend(added.iter().copied());
        if added.is_empty() || references.len() == usable {
            break; // 増やす見本が無い、またはみんな見本になった
        }
    }
    plan.references = references.into_iter().collect();
    progress.step(total, total);
    plan.cells = painted
        .into_iter()
        .map(|(i, p)| (i, Rc::try_unwrap(p).unwrap_or_else(|p| (*p).clone())))
        .collect();
    Ok(Some(plan))
}

/// 1セル分の書き込み：位置・変わった矩形・その矩形の新しい画素（RGBA8888）。
#[derive(Clone, Debug, PartialEq)]
pub struct Write {
    pub frame: usize,
    pub layer: usize,
    pub rect: Rect,
    pub pixels: Vec<u8>,
}

/// レイヤーを塗った結果。
#[derive(Clone, Debug, PartialEq)]
pub enum LayerOutcome {
    /// 塗れるセルが2つ未満。
    TooFewCells,
    /// 中止した。
    Cancelled,
    /// 塗った結果。`writes` が空なら塗る所は無かった。
    Done {
        /// 塗った位置と画素。
        writes: Vec<Write>,
        /// 一覧に載せる `(コマ, レイヤー, 見つかったもの)`。塗る所が無ければ空。
        notes: Vec<(usize, usize, Finding)>,
        /// 書いた色（小さい順）。
        written: Vec<Rgb>,
        /// 見本に使ったセル `(コマ, レイヤー)`。
        references: Vec<(usize, usize)>,
        rounds: usize,
    },
}

/// `AutoPaintController.run` の計算：レイヤー `layer` の絵のあるセルを、
/// `references`（`layer_cells` の並びの位置）を見本に塗る。ドキュメントは変えない。
pub fn paint_layer(
    document: &Document,
    layer: usize,
    references: &[usize],
    options: &AutoBucketOptions,
    progress: &mut Progress<'_>,
) -> Result<LayerOutcome, Error> {
    let cells = layer_cells(document, layer)?;
    if cells.len() < 2 {
        return Ok(LayerOutcome::TooFewCells);
    }
    let (width, height) = document.size();
    let fixed: BTreeSet<usize> = references.iter().copied().collect();
    let blank = vec![0u8; width * height * 4];
    let mut pixels = Vec::with_capacity(cells.len());
    let mut lines = Vec::with_capacity(cells.len());
    for (index, &(frame, layer)) in cells.iter().enumerate() {
        let current = cell_image(document, frame, layer, ImageField::Image)
            .map_or_else(|| blank.clone(), |image| image.rgba().to_vec());
        let initial = cell_image(document, frame, layer, ImageField::Initial)
            .map(|image| line_only(image.rgba(), width, height));
        // 見本は人が塗った今の絵。ほかは色塗り前の初期状態があれば、その線だけで分ける。
        match (&initial, fixed.contains(&index)) {
            (Some(initial), false) => pixels.push(initial.clone()),
            _ => pixels.push(current),
        }
        lines.push(initial);
    }
    let lines = lines.iter().any(Option::is_some).then_some(lines);
    let Some(plan) = plan_auto_paint(
        &pixels,
        width,
        height,
        references,
        lines.as_deref(),
        options,
        progress,
    )?
    else {
        return Ok(LayerOutcome::Cancelled);
    };
    let mut writes = Vec::new();
    let mut written = BTreeSet::new();
    let mut notes = Vec::new();
    for (&index, paint) in &plan.cells {
        let (frame, layer) = cells[index];
        for finding in &paint.notes {
            notes.push((frame, layer, finding.clone()));
        }
        if paint.paints.is_empty() {
            continue;
        }
        let Some(image) = cell_image(document, frame, layer, ImageField::Image) else {
            continue;
        };
        // Python は絵を Qt の乗算済みで往復した画素に塗り、乗算済みにして差し替える。
        let mut out = super::apply_paint(&qt_pixels(image), width, paint);
        // 同じ色で塗り直しただけでも、Python は絵を差し替える（塗ったセルに数える）。
        // 書き込む矩形は、塗った範囲と往復で値の変わった画素を囲む。
        let mut bounds: Option<(usize, usize, usize, usize)> = None;
        for item in &paint.paints {
            written.insert(item.rgb);
            let b = item.bounds;
            bounds = Some(match bounds {
                None => b,
                Some(r) => (r.0.min(b.0), r.1.min(b.1), r.2.max(b.2), r.3.max(b.3)),
            });
        }
        qt_premultiplied_roundtrip(&mut out, width);
        let before = image.rgba();
        for (i, (a, b)) in out.chunks_exact(4).zip(before.chunks_exact(4)).enumerate() {
            if a != b {
                let (x, y) = (i % width, i / width);
                bounds = Some(match bounds {
                    None => (x, y, x + 1, y + 1),
                    Some(r) => (r.0.min(x), r.1.min(y), r.2.max(x + 1), r.3.max(y + 1)),
                });
            }
        }
        let Some((x0, y0, x1, y1)) = bounds else {
            continue;
        };
        let rect = Rect {
            x: x0,
            y: y0,
            width: x1 - x0,
            height: y1 - y0,
        };
        let mut patch = Vec::with_capacity(rect.width * rect.height * 4);
        for y in y0..y1 {
            patch.extend_from_slice(&out[(y * width + x0) * 4..(y * width + x1) * 4]);
        }
        writes.push(Write {
            frame,
            layer,
            rect,
            pixels: patch,
        });
    }
    if plan.cells.values().all(|paint| paint.paints.is_empty()) {
        notes.clear(); // Python は塗る所が無ければ一覧を出さない。
    }
    Ok(LayerOutcome::Done {
        writes,
        notes,
        written: written.into_iter().collect(),
        references: plan.references.iter().map(|&i| cells[i]).collect(),
        rounds: plan.rounds,
    })
}

/// 書き込みをドキュメントへ当てる（1回の取り消しにまとめるのは呼び出し側）。
pub fn apply_writes(document: &mut Document, writes: &[Write]) -> Result<(), Error> {
    for write in writes {
        document.write_drawing_patch(
            write.layer,
            DrawingTarget::Frame(write.frame),
            write.rect,
            &write.pixels,
        )?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const WIDTH: usize = 100;
    const HEIGHT: usize = 70;
    const SKIN: Rgb = [255, 241, 232];
    const LEAF: Rgb = [197, 174, 103];
    const DRESS: Rgb = [200, 180, 220];
    const RED: Rgb = [255, 0, 0];
    const BLUE: Rgb = [0, 0, 255];

    fn put(pixels: &mut [u8], x0: usize, y0: usize, x1: usize, y1: usize, rgb: Rgb) {
        for y in y0..y1 {
            for x in x0..x1 {
                let at = (y * WIDTH + x) * 4;
                pixels[at..at + 3].copy_from_slice(&rgb);
                pixels[at + 3] = 255;
            }
        }
    }

    /// Python のテストの `figure`：黒い枠を赤の線で左右に、右半分を青の線で上下に分けた絵。
    fn figure(dx: usize, painted: bool) -> Vec<u8> {
        let mut pixels = vec![0u8; WIDTH * HEIGHT * 4];
        let (x0, y0, x1, y1) = (10 + dx, 10, 60 + dx, 56);
        if painted {
            put(&mut pixels, x0, y0, x0 + 20, y1, SKIN);
            put(&mut pixels, x0 + 20, y0, x1, 33, LEAF);
            put(&mut pixels, x0 + 20, 33, x1, y1, DRESS);
        }
        let black = [0, 0, 0];
        put(&mut pixels, x0, y0, x1, y0 + 1, black);
        put(&mut pixels, x0, y1 - 1, x1, y1, black);
        put(&mut pixels, x0, y0, x0 + 1, y1, black);
        put(&mut pixels, x1 - 1, y0, x1, y1, black);
        put(
            &mut pixels,
            x0 + 20,
            y0 + 1,
            x0 + 21,
            y1 - 1,
            if painted { SKIN } else { RED },
        );
        put(&mut pixels, x0 + 21, 33, x1 - 1, 34, BLUE);
        put(&mut pixels, x0 + 6, 18, x0 + 9, 21, black);
        put(&mut pixels, x0 + 34, 44, x0 + 38, 47, black);
        pixels
    }

    fn color_at(pixels: &[u8], x: usize, y: usize) -> Option<Rgb> {
        let at = (y * WIDTH + x) * 4;
        (pixels[at + 3] != 0).then(|| [pixels[at], pixels[at + 1], pixels[at + 2]])
    }

    #[test]
    fn order_goes_outwards_from_the_references() {
        let references: BTreeSet<usize> = [2].into_iter().collect();
        assert_eq!(order(5, &references), vec![(1, 2), (3, 2), (0, 1), (4, 3)]);
    }

    #[test]
    fn paints_each_area_from_the_neighbour_cell() {
        let cells = vec![figure(0, true), figure(2, false), figure(4, false)];
        let plan = plan_auto_paint(
            &cells,
            WIDTH,
            HEIGHT,
            &[0],
            None,
            &AutoBucketOptions::default(),
            &mut Progress::default(),
        )
        .unwrap()
        .unwrap();
        for (index, dx) in [(1, 2), (2, 4)] {
            let after = apply_paint(&cells[index], WIDTH, &plan.cells[&index]);
            assert_eq!(color_at(&after, 15 + dx, 40), Some(SKIN));
            assert_eq!(color_at(&after, 45 + dx, 20), Some(LEAF));
            assert_eq!(color_at(&after, 45 + dx, 50), Some(DRESS));
            assert_eq!(
                color_at(&after, 30 + dx, 40),
                Some(RED),
                "元の線画が無ければ、見本で塗りつぶした赤の線は決められず残す（Python と同じ）"
            );
            assert_eq!(
                color_at(&after, 45 + dx, 33),
                Some(BLUE),
                "見本で残した青の線は残す"
            );
            assert_eq!(color_at(&after, 5, 5), None, "枠の外は塗らない");
        }
        assert_eq!(
            plan.references,
            vec![0],
            "赤の線を決められないセルは見本に加えない（Python と同じ）"
        );
    }

    #[test]
    fn progress_can_cancel() {
        let cells = vec![figure(0, true), figure(2, false)];
        let mut stop = |_: usize, _: usize| false;
        let mut progress = Progress {
            report: Some(&mut stop),
            cancel: None,
        };
        let plan = plan_auto_paint(
            &cells,
            WIDTH,
            HEIGHT,
            &[0],
            None,
            &AutoBucketOptions::default(),
            &mut progress,
        )
        .unwrap();
        assert!(plan.is_none());
    }
}
