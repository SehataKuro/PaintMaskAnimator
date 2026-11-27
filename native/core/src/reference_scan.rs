//! 見本との違い・塗り間違いのチェック（Python の `reference_check.py` と
//! `ReferenceCheckController.collect` の画面以外の部分）。
//!
//! 見本のセルと色の集計を比べて「見本の色が無い」「見本に無い色がある」を挙げ、
//! 前後のコマの同じ部分と色が違う部分を挙げる。どれも Python 版（Rust 拡張を使う経路）と
//! 同じ候補・同じ並びを返す。書き換えはしない（直すのは人の仕事）。
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};

use crate::auto_paint::label_runs_of;
use crate::document::access::ImageField;
use crate::document::model::{Document, Reference};
use crate::fill::tracking;
use crate::image::RgbaImage;
use crate::reference_check::{color_stats, line_art, Bounds};
use crate::regions::erode;
use crate::tools::bucket::position_slot;
use crate::Error;

/// RGB。
pub type Rgb = [u8; 3];

/// `fill_tracking.LINE_MAX`（主線とみなす濃さ）。
pub const LINE_MAX: u8 = 48;
/// `fill_tracking.CONTEXT_RADIUS`。
pub const CONTEXT_RADIUS: i64 = 24;
/// 色の集計で、これより少ない画素しか無い色は数えない。
pub const MIN_COLOR_PIXELS: usize = 5;
/// 前後のコマと比べる部分の面積の範囲。
pub const MIN_REGION_AREA: usize = 20;
pub const MAX_REGION_AREA: usize = 40000;
/// 前後のコマで、動かした範囲のうち一番多い部分が占める割合の下限。
pub const MIN_AGREEMENT: f64 = 0.6;
/// この画素数だけ縮めると消える細い部分は比べない。
pub const THIN_RADIUS: usize = 2;
/// 前後のコマで対応する部分の面積比の上限。
pub const MAX_AREA_RATIO: f64 = 2.0;
/// 前後のコマで対応する部分の、まわりの色の並びの似ている度合いの下限。
pub const MIN_CONTEXT: f64 = 0.6;

/// 見つかった候補の種類（Python の `KIND_*` と同じ文字列）。
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum FindingKind {
    /// 見本で使っている色が、このセルに無い。
    Missing,
    /// 見本に無い色が、このセルに残っている。
    Extra,
    /// この部分の色が、前後のコマの同じ部分と違う。
    Mismatch,
    /// 自動塗りで、決めきれず塗らなかった部分。
    Unpainted,
    /// 自動塗りで、前後のセルと違う色で塗ってあった部分。
    Suspect,
    /// 自動塗りで、手本に無い形なので塗らなかった部分。
    NewPart,
    /// 自動塗りで、残すか塗るかを決める手がかりの無かった色トレス線。
    UndecidedLine,
}

impl FindingKind {
    /// Python の `KIND_*` の値。
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Missing => "missing",
            Self::Extra => "extra",
            Self::Mismatch => "mismatch",
            Self::Unpainted => "unpainted",
            Self::Suspect => "suspect",
            Self::NewPart => "new_part",
            Self::UndecidedLine => "undecided_line",
        }
    }
}

/// 見つかった候補1つ（Python の `reference_check.Finding`）。
#[derive(Clone, Debug, PartialEq)]
pub struct Finding {
    pub kind: FindingKind,
    /// 問題の色（透明は None）。
    pub rgb: Option<Rgb>,
    /// 画素数（MISSING は見本での画素数）。
    pub pixels: usize,
    /// 場所 `(x0, y0, x1, y1)`（排他）。MISSING には無い。
    pub bounds: Option<Bounds>,
    /// MISMATCH で、前後のコマのその部分の色。
    pub expected: Option<Rgb>,
    /// 外接矩形の中のこの部分のマスク（0/1、行優先）。
    pub mask: Option<Vec<u8>>,
}

impl Finding {
    fn new(kind: FindingKind, rgb: Option<Rgb>, pixels: usize) -> Self {
        Self {
            kind,
            rgb,
            pixels,
            bounds: None,
            expected: None,
            mask: None,
        }
    }
}

/// 進み具合の通知と中止の受け取り。
#[derive(Default)]
pub struct Progress<'a> {
    /// `(済んだ数, 全体)` を受け取る。偽を返すと中止する。
    pub report: Option<&'a mut dyn FnMut(usize, usize) -> bool>,
    /// 立っていれば中止する。
    pub cancel: Option<&'a AtomicBool>,
}

impl Progress<'_> {
    /// 続けてよいか。
    pub fn step(&mut self, done: usize, total: usize) -> bool {
        if self.cancel.is_some_and(|flag| flag.load(Ordering::Relaxed)) {
            return false;
        }
        match self.report.as_mut() {
            Some(report) => report(done, total),
            None => true,
        }
    }

    /// 中止の印だけを見る。
    pub fn cancelled(&self) -> bool {
        self.cancel.is_some_and(|flag| flag.load(Ordering::Relaxed))
    }
}

/// `fill_tracking._labels`：RGBA8888 をリトルエンディアンの u32 にし、透明は 0。
pub fn pixel_values(rgba: &[u8]) -> Vec<u32> {
    rgba.chunks_exact(4)
        .map(|p| {
            if p[3] == 0 {
                0
            } else {
                u32::from_le_bytes([p[0], p[1], p[2], p[3]])
            }
        })
        .collect()
}

/// `reference_check._to_rgb`。
pub fn to_rgb(value: u32) -> Option<Rgb> {
    (value != 0).then_some([value as u8, (value >> 8) as u8, (value >> 16) as u8])
}

/// `reference_check._pack`（不透明）。
pub fn pack(rgb: Rgb) -> u32 {
    u32::from(rgb[0]) | (u32::from(rgb[1]) << 8) | (u32::from(rgb[2]) << 16) | (255 << 24)
}

/// `reference_check._is_line`。
pub fn is_line(rgb: Option<Rgb>) -> bool {
    rgb.is_some_and(|rgb| rgb.iter().all(|&c| c <= LINE_MAX))
}

/// 1セル分の、値ごとにつながった部分（Python の `reference_check.CellRegions`）。
#[derive(Clone, Debug)]
pub struct CellRegions {
    pub width: usize,
    pub height: usize,
    /// 部分分けに使った値。
    pub values: Vec<u32>,
    /// 主線のマスク（`fill_tracking.line_art`）。
    pub lines: Vec<u8>,
    /// 画素ごとの部分の番号（最初の画素の行優先の順）。
    pub labels: Vec<u32>,
    /// 部分ごとの画素数。
    pub sizes: Vec<usize>,
    /// 部分ごとの外接矩形（排他）。
    pub bounds: Vec<Bounds>,
    region_values: Vec<u32>,
}

impl CellRegions {
    /// `values` を省くと画素の色（[`pixel_values`]）で分ける。
    pub fn new(rgba: &[u8], width: usize, height: usize, values: Option<Vec<u32>>) -> Self {
        let values = values.unwrap_or_else(|| pixel_values(rgba));
        let lines = line_art(rgba, LINE_MAX);
        let bytes: Vec<u8> = values.iter().flat_map(|v| v.to_ne_bytes()).collect();
        let (labels, count, ys, starts, ends, run_labels) = label_runs_of(&bytes, width, height, 4);
        drop(bytes);
        let mut sizes = vec![0usize; count];
        let mut bounds = vec![(width, height, 0usize, 0usize); count];
        let mut region_values = vec![0u32; count];
        for i in 0..starts.len() {
            let (y, x0, x1) = (ys[i] as usize, starts[i] as usize, ends[i] as usize);
            let label = run_labels[i] as usize;
            sizes[label] += x1 - x0;
            let b = &mut bounds[label];
            *b = (b.0.min(x0), b.1.min(y), b.2.max(x1), b.3.max(y + 1));
            region_values[label] = values[y * width + x0];
        }
        Self {
            width,
            height,
            values,
            lines,
            labels,
            sizes,
            bounds,
            region_values,
        }
    }

    /// 部分の数。
    pub fn count(&self) -> usize {
        self.sizes.len()
    }

    /// 部分の値。
    pub fn value(&self, index: usize) -> u32 {
        self.region_values[index]
    }

    /// 画像の端に接する部分か。
    pub fn on_edge(&self, index: usize) -> bool {
        let (x0, y0, x1, y1) = self.bounds[index];
        x0 == 0 || y0 == 0 || x1 == self.width || y1 == self.height
    }

    /// 外接矩形の中のこの部分のマスク。
    pub fn local_mask(&self, index: usize) -> Vec<u8> {
        let (x0, y0, x1, y1) = self.bounds[index];
        let mut out = Vec::with_capacity((x1 - x0) * (y1 - y0));
        for y in y0..y1 {
            let row = &self.labels[y * self.width + x0..y * self.width + x1];
            out.extend(row.iter().map(|&l| u8::from(l as usize == index)));
        }
        out
    }

    /// `region_under` の Rust 拡張と同じ：矩形内のマスクの下（主線を除く）で一番多い部分。
    /// 同数なら番号の小さい方。
    pub fn region_under(&self, mask: &[u8], bounds: Bounds) -> (Option<usize>, f64) {
        let (x0, y0, x1, y1) = bounds;
        if x0 >= x1 || y0 >= y1 {
            return (None, 0.0);
        }
        let mut counts: HashMap<u32, usize> = HashMap::new();
        let mut total = 0usize;
        for (y, row) in (y0..y1).zip(mask.chunks_exact(x1 - x0)) {
            let start = y * self.width + x0;
            for (x, &included) in row.iter().enumerate() {
                if included != 0 && self.lines[start + x] == 0 {
                    *counts.entry(self.labels[start + x]).or_default() += 1;
                    total += 1;
                }
            }
        }
        let best = counts
            .into_iter()
            .max_by(|(a, ac), (b, bc)| ac.cmp(bc).then_with(|| b.cmp(a)));
        match best {
            Some((label, count)) => (Some(label as usize), count as f64 / total as f64),
            None => (None, 0.0),
        }
    }
}

/// 挿入順を保つ小さな色の表（Python の dict と同じ並び・上書き）。
#[derive(Default)]
struct ColorTable {
    entries: Vec<(Rgb, usize)>,
}

impl ColorTable {
    fn set(&mut self, rgb: Rgb, count: usize) {
        match self.entries.iter_mut().find(|(c, _)| *c == rgb) {
            Some(entry) => entry.1 = count,
            None => self.entries.push((rgb, count)),
        }
    }

    fn get(&self, rgb: Rgb) -> Option<usize> {
        self.entries.iter().find(|(c, _)| *c == rgb).map(|e| e.1)
    }
}

/// `color_counts`：色ごとの画素数（白と透明を除く）。同じ RGB で透明度の違う値は、
/// Python の辞書と同じく最初の位置に後の画素数を上書きする。
fn color_table(stats: &[(u32, crate::reference_check::RegionStats)]) -> ColorTable {
    let mut table = ColorTable::default();
    for (value, stats) in stats {
        if let Some(rgb) = to_rgb(*value) {
            table.set(rgb, stats.size);
        }
    }
    table
}

/// 見本 `reference` とこのセル `pixels` の使用色の違い（`compare_colors`）。
pub fn compare_colors(reference: &[u8], pixels: &[u8], width: usize) -> Vec<Finding> {
    compare_colors_sized((reference, width), (pixels, width))
}

/// 1枚のセルの画素と部分分け。
pub struct CheckCell<'a> {
    pub rgba: &'a [u8],
    pub regions: CellRegions,
}

impl<'a> CheckCell<'a> {
    /// 画素の色で部分に分ける。
    pub fn new(rgba: &'a [u8], width: usize, height: usize) -> Self {
        Self {
            rgba,
            regions: CellRegions::new(rgba, width, height, None),
        }
    }
}

/// 色の並びを比べる窓：画素・部分のマスク・大きさ・中心からの原点。
type ContextWindow = (Vec<u8>, Vec<u8>, (usize, usize), (i64, i64));

/// `native.reference_context_similarity` の窓の作り方。
fn context_window(
    pixels: &[u8],
    width: usize,
    height: usize,
    local: &[u8],
    bounds: Bounds,
    radius: i64,
) -> Result<ContextWindow, Error> {
    let (x0, y0, x1, y1) = bounds;
    let (lw, lh) = (x1 - x0, y1 - y0);
    let (cy, cx) = tracking::region_center(local, lw, lh)?
        .ok_or_else(|| Error::Invalid("空の部分の中心".into()))?;
    let (cy, cx) = (cy + y0 as i64, cx + x0 as i64);
    let top = (cy - radius).max(0);
    let left = (cx - radius).max(0);
    let bottom = (cy + radius + 1).min(height as i64);
    let right = (cx + radius + 1).min(width as i64);
    let (rw, rh) = (
        (right - left).max(0) as usize,
        (bottom - top).max(0) as usize,
    );
    let mut region = vec![0u8; rw * rh];
    let sy = top.max(y0 as i64);
    let sx = left.max(x0 as i64);
    let ey = bottom.min(y1 as i64);
    let ex = right.min(x1 as i64);
    for y in sy..ey {
        for x in sx..ex {
            region[(y - top) as usize * rw + (x - left) as usize] =
                local[(y as usize - y0) * lw + (x as usize - x0)];
        }
    }
    // `_tracking_context`：窓の中でさらに中心のまわりを切り出す（同じ範囲になる）。
    let (ccy, ccx) = (cy - top, cx - left);
    let wy0 = (ccy - radius).max(0) as usize;
    let wx0 = (ccx - radius).max(0) as usize;
    let wy1 = ((ccy + radius + 1).max(0) as usize).min(rh);
    let wx1 = ((ccx + radius + 1).max(0) as usize).min(rw);
    let (ww, wh) = (wx1.saturating_sub(wx0), wy1.saturating_sub(wy0));
    let mut out_pixels = Vec::with_capacity(ww * wh * 4);
    let mut out_region = Vec::with_capacity(ww * wh);
    for y in wy0..wy1 {
        let gy = top as usize + y;
        let gx = left as usize + wx0;
        out_pixels.extend_from_slice(&pixels[(gy * width + gx) * 4..(gy * width + gx + ww) * 4]);
        out_region.extend_from_slice(&region[y * rw + wx0..y * rw + wx1]);
    }
    Ok((
        out_pixels,
        out_region,
        (ww, wh),
        (wy0 as i64 - ccy, wx0 as i64 - ccx),
    ))
}

/// `native.reference_context_similarity`。
#[allow(clippy::too_many_arguments)]
pub fn local_context_similarity(
    previous: &[u8],
    previous_local: &[u8],
    previous_bounds: Bounds,
    current: &[u8],
    current_local: &[u8],
    current_bounds: Bounds,
    width: usize,
    height: usize,
    radius: i64,
) -> Result<f64, Error> {
    let before = context_window(
        previous,
        width,
        height,
        previous_local,
        previous_bounds,
        radius,
    )?;
    let after = context_window(
        current,
        width,
        height,
        current_local,
        current_bounds,
        radius,
    )?;
    tracking::context_similarity(
        &before.0, &before.1, before.2, before.3, &after.0, &after.1, after.2, after.3,
    )
}

/// `_counterpart_local`：`neighbour` のコマで、この部分に当たる部分の番号。
fn counterpart(
    current: &CheckCell<'_>,
    local: &[u8],
    bounds: Bounds,
    neighbour: &CheckCell<'_>,
    min_agreement: f64,
) -> Result<Option<usize>, Error> {
    let regions = &current.regions;
    let (width, height) = (regions.width, regions.height);
    let (x0, y0, x1, y1) = bounds;
    let (lw, lh) = (x1 - x0, y1 - y0);
    let margin = 64usize.max(3 * lw.max(lh));
    let left = x0.saturating_sub(margin);
    let top = y0.saturating_sub(margin);
    let right = width.min(x1 + margin);
    let bottom = height.min(y1 + margin);
    let (ww, wh) = (right - left, bottom - top);
    let crop = |mask: &[u8]| -> Vec<u8> {
        let mut out = Vec::with_capacity(ww * wh);
        for y in top..bottom {
            out.extend_from_slice(&mask[y * width + left..y * width + right]);
        }
        out
    };
    let mut region = vec![0u8; ww * wh];
    for y in 0..lh {
        let at = (y0 - top + y) * ww + (x0 - left);
        region[at..at + lw].copy_from_slice(&local[y * lw..(y + 1) * lw]);
    }
    let shifts = tracking::estimate_shifts(
        &crop(&regions.lines),
        &crop(&neighbour.regions.lines),
        &region,
        ww,
        wh,
        1,
        Some(margin as i64),
    )?;
    let (dy, dx) = shifts.first().copied().unwrap_or((0, 0));
    let mleft = (x0 as i64 + dx).max(0);
    let mtop = (y0 as i64 + dy).max(0);
    let mright = (x1 as i64 + dx).min(width as i64);
    let mbottom = (y1 as i64 + dy).min(height as i64);
    if mleft >= mright || mtop >= mbottom {
        return Ok(None);
    }
    let (mw, mh) = ((mright - mleft) as usize, (mbottom - mtop) as usize);
    let mut moved = Vec::with_capacity(mw * mh);
    for y in 0..mh {
        let ly = (mtop - y0 as i64 - dy) as usize + y;
        let lx = (mleft - x0 as i64 - dx) as usize;
        moved.extend_from_slice(&local[ly * lw + lx..ly * lw + lx + mw]);
    }
    let moved_bounds = (
        mleft as usize,
        mtop as usize,
        mright as usize,
        mbottom as usize,
    );
    let (index, share) = neighbour.regions.region_under(&moved, moved_bounds);
    let Some(index) = index else {
        return Ok(None);
    };
    if share < min_agreement {
        return Ok(None);
    }
    let area = local.iter().filter(|&&v| v != 0).count();
    let ratio = neighbour.regions.sizes[index] as f64 / area.max(1) as f64;
    if !(1.0 / MAX_AREA_RATIO..=MAX_AREA_RATIO).contains(&ratio) {
        return Ok(None);
    }
    let similarity = local_context_similarity(
        current.rgba,
        local,
        bounds,
        neighbour.rgba,
        &neighbour.regions.local_mask(index),
        neighbour.regions.bounds[index],
        width,
        height,
        CONTEXT_RADIUS,
    )?;
    Ok((similarity >= MIN_CONTEXT).then_some(index))
}

/// `find_mismatches`：この部分の色が前後のコマの同じ部分と違う所。
pub fn find_mismatches(
    previous: &CheckCell<'_>,
    current: &CheckCell<'_>,
    following: &CheckCell<'_>,
) -> Result<Vec<Finding>, Error> {
    let shape = |cell: &CheckCell<'_>| (cell.regions.width, cell.regions.height);
    if shape(previous) != shape(current) || shape(current) != shape(following) {
        return Ok(Vec::new());
    }
    let regions = &current.regions;
    let neighbours = [previous, following];
    let mut findings = Vec::new();
    for index in 0..regions.count() {
        let size = regions.sizes[index];
        if !(MIN_REGION_AREA..=MAX_REGION_AREA).contains(&size) {
            continue;
        }
        let value = regions.value(index);
        let rgb = to_rgb(value);
        if is_line(rgb) || rgb == Some([255; 3]) {
            continue;
        }
        if rgb.is_none() && regions.on_edge(index) {
            continue; // キャンバスの端につながる透明は絵の外（背景）。
        }
        let local = regions.local_mask(index);
        let bounds = regions.bounds[index];
        let (lw, lh) = (bounds.2 - bounds.0, bounds.3 - bounds.1);
        if !erode(&local, lw, lh, THIN_RADIUS).iter().any(|&v| v != 0) {
            continue;
        }
        let consistent = neighbours.iter().any(|neighbour| {
            let (same, share) = neighbour.regions.region_under(&local, bounds);
            same.is_some_and(|same| neighbour.regions.value(same) == value)
                && share >= MIN_AGREEMENT
        });
        if consistent {
            continue;
        }
        let mut expected = Vec::new();
        for neighbour in neighbours {
            match counterpart(current, &local, bounds, neighbour, MIN_AGREEMENT)? {
                Some(other) => expected.push(neighbour.regions.value(other)),
                None => break,
            }
        }
        if expected.len() != 2 || expected[0] != expected[1] || expected[0] == value {
            continue;
        }
        let expected_rgb = to_rgb(expected[0]);
        if is_line(expected_rgb) {
            continue;
        }
        findings.push(Finding {
            kind: FindingKind::Mismatch,
            rgb,
            pixels: size,
            bounds: Some(bounds),
            expected: expected_rgb,
            mask: Some(local),
        });
    }
    findings.sort_by_key(|f| std::cmp::Reverse(f.pixels));
    Ok(findings)
}

/// 一覧の1件：`(コマ, レイヤー, 見つかったもの)`。
#[derive(Clone, Debug, PartialEq)]
pub struct Entry {
    pub frame: usize,
    pub layer: usize,
    pub finding: Finding,
}

/// チェックの結果。
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ReferenceReport {
    /// 一覧に載せる候補（コマ、種類の文字列の順）。
    pub entries: Vec<Entry>,
    /// 見本だけにある色：`(色, 無いセルの数, 比べたセルの数)`（色の順）。
    pub reference_only: Vec<(Rgb, usize, usize)>,
}

/// `_split_reference_only`：半分を超えるセルに無い見本の色を、要約へまとめる。
fn split_reference_only(entries: Vec<Entry>, compared: usize) -> ReferenceReport {
    let mut missing = ColorTable::default();
    for entry in &entries {
        if entry.finding.kind == FindingKind::Missing {
            if let Some(rgb) = entry.finding.rgb {
                let count = missing.get(rgb).unwrap_or(0);
                missing.set(rgb, count + 1);
            }
        }
    }
    let mut common: Vec<(Rgb, usize)> = missing
        .entries
        .iter()
        .filter(|(_, count)| count * 2 > compared)
        .copied()
        .collect();
    common.sort();
    let kept = entries
        .into_iter()
        .filter(|entry| {
            !(entry.finding.kind == FindingKind::Missing
                && entry
                    .finding
                    .rgb
                    .is_some_and(|rgb| common.iter().any(|(c, _)| *c == rgb)))
        })
        .collect();
    ReferenceReport {
        entries: kept,
        reference_only: common
            .into_iter()
            .map(|(rgb, count)| (rgb, count, compared))
            .collect(),
    }
}

/// `ReferenceCheckController.collect`：`cells[reference]` を見本に候補を集める。
/// `cells` は `(コマ, レイヤー)` の昇順、`images` はその絵（同じ大きさとは限らない）。
/// 中止されたら None。
pub fn collect(
    cells: &[(usize, usize)],
    images: &[&RgbaImage],
    reference: usize,
    progress: &mut Progress<'_>,
) -> Result<Option<ReferenceReport>, Error> {
    if cells.len() != images.len() || reference >= cells.len() {
        return Err(Error::Invalid("見本のセルが一覧にありません".into()));
    }
    let total = cells.len() * 2;
    for done in 0..cells.len() {
        if !progress.step(done, total) {
            return Ok(None);
        }
    }
    let reference_image = images[reference];
    let mut entries = Vec::new();
    for (position, (&(frame, layer), image)) in cells.iter().zip(images).enumerate() {
        if position == reference {
            continue;
        }
        // 色の集計は幅ごとに行を読む（大きさの違う絵も Python と同じく比べる）。
        let reference_counts = (reference_image.rgba(), reference_image.width());
        for finding in compare_colors_sized(reference_counts, (image.rgba(), image.width())) {
            entries.push(Entry {
                frame,
                layer,
                finding,
            });
        }
    }
    let mut report = split_reference_only(entries, cells.len() - 1);
    let mut regions: Vec<(usize, CheckCell<'_>)> = Vec::new();
    for position in 1..cells.len().saturating_sub(1) {
        if !progress.step(cells.len() + position, total) {
            return Ok(None);
        }
        let window = [position - 1, position, position + 1];
        regions.retain(|(key, _)| window.contains(key));
        for key in window {
            if !regions.iter().any(|(k, _)| *k == key) {
                let image = images[key];
                regions.push((
                    key,
                    CheckCell::new(image.rgba(), image.width(), image.height()),
                ));
            }
        }
        let get = |key: usize| &regions.iter().find(|(k, _)| *k == key).unwrap().1;
        let (frame, layer) = cells[position];
        for finding in find_mismatches(get(window[0]), get(window[1]), get(window[2]))? {
            report.entries.push(Entry {
                frame,
                layer,
                finding,
            });
        }
    }
    report.entries.sort_by(|a, b| {
        (a.frame, a.finding.kind.as_str()).cmp(&(b.frame, b.finding.kind.as_str()))
    });
    Ok(Some(report))
}

fn compare_colors_sized(reference: (&[u8], usize), pixels: (&[u8], usize)) -> Vec<Finding> {
    let reference_table = color_table(&color_stats(reference.0, reference.1));
    let stats = color_stats(pixels.0, pixels.1);
    compare_tables(reference_table, &stats)
}

fn compare_tables(
    reference: ColorTable,
    stats: &[(u32, crate::reference_check::RegionStats)],
) -> Vec<Finding> {
    let counts = color_table(stats);
    let bounds: HashMap<u32, Bounds> = stats.iter().map(|(v, s)| (*v, s.bounds)).collect();
    let mut findings = Vec::new();
    let mut missing: Vec<(Rgb, usize)> = reference.entries.clone();
    missing.sort_by_key(|(_, count)| std::cmp::Reverse(*count));
    for (rgb, count) in missing {
        if count >= MIN_COLOR_PIXELS && counts.get(rgb).unwrap_or(0) == 0 {
            findings.push(Finding::new(FindingKind::Missing, Some(rgb), count));
        }
    }
    let mut extra: Vec<(Rgb, usize)> = counts
        .entries
        .iter()
        .filter(|(rgb, count)| *count >= MIN_COLOR_PIXELS && reference.get(*rgb).is_none())
        .copied()
        .collect();
    extra.sort_by_key(|(_, count)| std::cmp::Reverse(*count));
    for (rgb, count) in extra {
        let mut finding = Finding::new(FindingKind::Extra, Some(rgb), count);
        finding.bounds = bounds.get(&pack(rgb)).copied();
        findings.push(finding);
    }
    findings
}

/// レイヤーの、絵のあるセル（保持はキーフレームへ畳む、コマ順）。
/// Python の `resolve_cells(canvas, FrameScope.all_frames(canvas))` から絵の無いセルを除いたもの。
pub fn layer_cells(document: &Document, layer: usize) -> Result<Vec<(usize, usize)>, Error> {
    let frames = document.frame_order().len();
    let (width, sources) = document.resolve_sources()?;
    if layer >= width || frames == 0 {
        return Ok(Vec::new());
    }
    let mut cells: Vec<(usize, usize)> = Vec::new();
    for frame in 0..frames {
        let source = sources[frame * width + layer];
        if source < 0 {
            continue;
        }
        let key = source as usize;
        if !cells.iter().any(|&(f, _)| f == key) && has_content(document, key, layer) {
            cells.push((key, layer));
        }
    }
    cells.sort_unstable();
    Ok(cells)
}

/// `canvas.resolve_key_frame(frame, layer)`。
pub fn key_frame(document: &Document, frame: usize, layer: usize) -> Result<Option<usize>, Error> {
    let frames = document.frame_order().len();
    let (width, sources) = document.resolve_sources()?;
    if frames == 0 || layer >= width {
        return Ok(None);
    }
    let source = sources[frame.min(frames - 1) * width + layer];
    Ok((source >= 0).then_some(source as usize))
}

/// 位置の `layer.has_content`。
pub fn has_content(document: &Document, frame: usize, layer: usize) -> bool {
    let (Some(track), Some(frame_id)) = (
        document.track_order().get(layer),
        document.frame_order().get(frame),
    ) else {
        return false;
    };
    document
        .frame(*frame_id)
        .and_then(|f| f.positions.get(track))
        .is_some_and(|position| match position.reference {
            Reference::Number(_) => true,
            Reference::Blank => false,
            Reference::Empty => position.compatibility.unnumbered_content,
        })
}

/// 位置 `(frame, layer)` の絵か初期状態（無ければ None）。
pub fn cell_image(
    document: &Document,
    frame: usize,
    layer: usize,
    field: ImageField,
) -> Option<&RgbaImage> {
    position_slot(document, frame, layer).and_then(|slot| document.artwork_image(slot, field))
}

/// チェックを実行できなかった理由、または結果。
#[derive(Clone, Debug, PartialEq)]
pub enum CheckOutcome {
    /// 今のコマに見本にするセル（塗ってあるコマ）が無い。
    NoReference,
    /// 比べるセルが無い。
    TooFewCells,
    /// 中止した。
    Cancelled,
    /// 見本のコマと結果。
    Done {
        reference_frame: usize,
        report: ReferenceReport,
    },
}

/// `ReferenceCheckController.run` の計算：今のセルを見本にレイヤーの全セルを調べる。
pub fn check_layer(
    document: &Document,
    current_frame: usize,
    layer: usize,
    progress: &mut Progress<'_>,
) -> Result<CheckOutcome, Error> {
    let cells = layer_cells(document, layer)?;
    let Some(reference_frame) = key_frame(document, current_frame, layer)? else {
        return Ok(CheckOutcome::NoReference);
    };
    let Some(reference) = cells.iter().position(|&c| c == (reference_frame, layer)) else {
        return Ok(CheckOutcome::NoReference);
    };
    if cells.len() < 2 {
        return Ok(CheckOutcome::TooFewCells);
    }
    let (width, height) = document.size();
    let blank = RgbaImage::blank(width, height)?;
    let images: Vec<&RgbaImage> = cells
        .iter()
        .map(|&(frame, layer)| {
            cell_image(document, frame, layer, ImageField::Image).unwrap_or(&blank)
        })
        .collect();
    Ok(match collect(&cells, &images, reference, progress)? {
        Some(report) => CheckOutcome::Done {
            reference_frame,
            report,
        },
        None => CheckOutcome::Cancelled,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn image(width: usize, height: usize, paint: impl Fn(usize, usize) -> [u8; 4]) -> Vec<u8> {
        let mut out = Vec::new();
        for y in 0..height {
            for x in 0..width {
                out.extend_from_slice(&paint(x, y));
            }
        }
        out
    }

    #[test]
    fn missing_and_extra_colors_sorted_by_count() {
        let reference = image(10, 4, |x, _| {
            if x < 6 {
                [200, 0, 0, 255]
            } else {
                [0, 0, 200, 255]
            }
        });
        let cell = image(10, 4, |x, _| {
            if x < 3 {
                [200, 0, 0, 255]
            } else {
                [10, 200, 10, 255]
            }
        });
        let findings = compare_colors(&reference, &cell, 10);
        assert_eq!(findings.len(), 2);
        assert_eq!(findings[0].kind, FindingKind::Missing);
        assert_eq!(findings[0].rgb, Some([0, 0, 200]));
        assert_eq!(findings[0].pixels, 16);
        assert_eq!(findings[1].kind, FindingKind::Extra);
        assert_eq!(findings[1].bounds, Some((3, 0, 10, 4)));
    }

    #[test]
    fn regions_count_sizes_and_edges() {
        let pixels = image(6, 3, |x, y| {
            if (1..5).contains(&x) && y == 1 {
                [9, 9, 9, 255]
            } else {
                [0; 4]
            }
        });
        let regions = CellRegions::new(&pixels, 6, 3, None);
        assert_eq!(regions.count(), 2);
        assert_eq!(regions.sizes, vec![14, 4]);
        assert!(regions.on_edge(0));
        assert!(!regions.on_edge(1));
        assert_eq!(regions.bounds[1], (1, 1, 5, 2));
        assert_eq!(regions.local_mask(1), vec![1; 4]);
        // 主線（濃い色）は数えない。
        assert_eq!(regions.region_under(&[1; 4], (1, 1, 5, 2)), (None, 0.0));
    }

    #[test]
    fn reference_only_colors_are_summarised() {
        let finding = |rgb| Finding::new(FindingKind::Missing, Some(rgb), 9);
        let entries = vec![
            Entry {
                frame: 1,
                layer: 0,
                finding: finding([1, 2, 3]),
            },
            Entry {
                frame: 2,
                layer: 0,
                finding: finding([1, 2, 3]),
            },
            Entry {
                frame: 2,
                layer: 0,
                finding: finding([7, 7, 7]),
            },
        ];
        let report = split_reference_only(entries, 3);
        assert_eq!(report.reference_only, vec![([1, 2, 3], 2, 3)]);
        assert_eq!(report.entries.len(), 1);
    }

    #[test]
    fn mismatch_found_when_both_neighbours_agree() {
        // 枠の中を前後は赤、このコマだけ青で塗った。
        let paint = |inside: [u8; 4]| {
            image(40, 30, move |x, y| {
                let edge = (x == 5 || x == 25) && (5..=20).contains(&y)
                    || (y == 5 || y == 20) && (5..=25).contains(&x);
                if edge {
                    [0, 0, 0, 255]
                } else if (6..25).contains(&x) && (6..20).contains(&y) {
                    inside
                } else {
                    [0; 4]
                }
            })
        };
        let red = paint([220, 30, 30, 255]);
        let blue = paint([30, 30, 220, 255]);
        let cells: Vec<CheckCell<'_>> = [&red, &blue, &red]
            .iter()
            .map(|p| CheckCell::new(p, 40, 30))
            .collect();
        let findings = find_mismatches(&cells[0], &cells[1], &cells[2]).unwrap();
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].rgb, Some([30, 30, 220]));
        assert_eq!(findings[0].expected, Some([220, 30, 30]));
    }
}
