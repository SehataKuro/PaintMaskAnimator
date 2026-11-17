//! コマ間のずれと、領域周辺の色の一致を求める。
use crate::{regions::dilate, Error};
use std::collections::HashMap;

mod fft;

type Shift = (i64, i64);
const PEAK_TOLERANCE: f64 = 0.9;

fn invalid() -> Error {
    Error::Invalid("tracking buffer dimensions or window are invalid".into())
}

#[derive(Clone, Copy)]
struct Size {
    w: usize,
    h: usize,
}

impl Size {
    fn check(self, bytes: &[u8], channels: usize) -> Result<(), Error> {
        if self.w <= i32::MAX as usize
            && self.h <= i32::MAX as usize
            && self
                .w
                .checked_mul(self.h)
                .and_then(|n| n.checked_mul(channels))
                == Some(bytes.len())
        {
            Ok(())
        } else {
            Err(invalid())
        }
    }

    fn rect(self) -> Rect {
        Rect {
            x0: 0,
            y0: 0,
            x1: self.w as i64,
            y1: self.h as i64,
        }
    }
}

#[derive(Clone, Copy)]
struct Rect {
    x0: i64,
    y0: i64,
    x1: i64,
    y1: i64,
}

impl Rect {
    fn size(self) -> Size {
        Size {
            w: self.x1.saturating_sub(self.x0).max(0) as usize,
            h: self.y1.saturating_sub(self.y0).max(0) as usize,
        }
    }

    fn intersect(self, other: Self) -> Self {
        Self {
            x0: self.x0.max(other.x0),
            y0: self.y0.max(other.y0),
            x1: self.x1.min(other.x1),
            y1: self.y1.min(other.y1),
        }
    }

    fn expand(self, radius: i64) -> Self {
        Self {
            x0: self.x0.saturating_sub(radius),
            y0: self.y0.saturating_sub(radius),
            x1: self.x1.saturating_add(radius),
            y1: self.y1.saturating_add(radius),
        }
    }

    fn translate(self, (dy, dx): Shift) -> Self {
        Self {
            x0: self.x0.saturating_add(dx),
            y0: self.y0.saturating_add(dy),
            x1: self.x1.saturating_add(dx),
            y1: self.y1.saturating_add(dy),
        }
    }
}

fn bounds(region: &[u8], size: Size) -> Option<Rect> {
    if size.w == 0 {
        return None;
    }
    let mut b = Rect {
        x0: size.w as i64,
        y0: size.h as i64,
        x1: 0,
        y1: 0,
    };
    for (y, row) in region.chunks_exact(size.w).enumerate() {
        if row.contains(&1) {
            if let Some(x) = row[..b.x0 as usize].iter().position(|&v| v == 1) {
                b.x0 = x as i64;
            }
            if let Some(x) = row[b.x1 as usize..].iter().rposition(|&v| v == 1) {
                b.x1 += x as i64 + 1;
            }
            b.y0 = b.y0.min(y as i64);
            b.y1 = y as i64 + 1;
        }
    }
    (b.y1 != 0).then_some(b)
}

fn copy_rect(src: &[u8], source: Size, dst: &mut [u8], dest: Size, (dy, dx): Shift) {
    let from = source.rect();
    let overlap = from.translate((dy, dx)).intersect(dest.rect());
    let width = overlap.size().w;
    if width == 0 {
        return;
    }
    for y in overlap.y0..overlap.y1 {
        let i = (y - dy) as usize * source.w + (overlap.x0 - dx) as usize;
        let j = y as usize * dest.w + overlap.x0 as usize;
        dst[j..j + width].copy_from_slice(&src[i..i + width]);
    }
}

fn crop(mask: &[u8], size: Size, rect: Rect) -> Vec<u8> {
    let dest = rect.size();
    let mut out = vec![0; dest.w * dest.h];
    copy_rect(mask, size, &mut out, dest, (-rect.y0, -rect.x0));
    out
}

fn outline(region: &[u8], lines: &mut [u8], size: Size) {
    let expanded = dilate(region, size.w, size.h, 3);
    for ((line, filled), expanded) in lines.iter_mut().zip(region).zip(expanded) {
        *line &= expanded & (1 ^ filled);
    }
}

#[derive(Clone, Copy)]
struct Run {
    y: usize,
    x: usize,
    end: usize,
}

fn mask_runs(mask: &[u8], size: Size, step: usize) -> Vec<Run> {
    let width = size.w.div_ceil(step);
    if step == 1 {
        let mut runs = Vec::new();
        for (y, row) in mask.chunks_exact(width).enumerate() {
            let mut x = 0;
            while let Some(start) = row[x..].iter().position(|&v| v != 0) {
                x += start;
                let end = x + row[x..].iter().position(|&v| v == 0).unwrap_or(width - x);
                runs.push(Run { y, x, end });
                x = end;
            }
        }
        return runs;
    }
    let mut small = vec![0; width * size.h.div_ceil(step)];
    for (y, rows) in mask.chunks(size.w * step).enumerate() {
        let row = &mut small[y * width..][..width];
        for src in rows.chunks_exact(size.w) {
            for (out, block) in row.iter_mut().zip(src.chunks(step)) {
                *out |= u8::from(block.contains(&1));
            }
        }
    }
    mask_runs(
        &small,
        Size {
            w: width,
            h: size.h.div_ceil(step),
        },
        1,
    )
}

struct Correlation {
    size: Size,
    // 横方向の重なりの二階差分。負のずれの端を起点に復元する。
    values: Vec<i64>,
    origin: usize,
    pitch: usize,
    differences: bool,
}

impl Correlation {
    fn new(size: Size, before_width: usize) -> Result<Self, Error> {
        let pitch = size.w.checked_add(2).ok_or_else(invalid)?;
        let n = pitch.checked_mul(size.h).ok_or_else(invalid)?;
        let mut values = Vec::new();
        values.try_reserve_exact(n).map_err(|_| invalid())?;
        values.resize(n, 0);
        Ok(Self {
            size,
            values,
            origin: before_width - 1,
            pitch,
            differences: false,
        })
    }

    fn add(&mut self, before: &[Run], after: &[Run], reach: Option<usize>) {
        if before.is_empty() || after.is_empty() {
            return;
        }
        let differences = before.iter().chain(after).any(|p| p.end != p.x + 1);
        if differences && !self.differences {
            for row in self.values.chunks_exact_mut(self.pitch) {
                for x in (0..row.len()).rev() {
                    row[x] = row[x] - 2 * row.get(x.wrapping_sub(1)).copied().unwrap_or(0)
                        + row.get(x.wrapping_sub(2)).copied().unwrap_or(0);
                }
            }
            self.differences = true;
        }
        let h = self.size.h;
        for p in before {
            let x_base = self.origin - p.x;
            let candidates = if let Some(reach) = reach {
                let first = after.partition_point(|q| q.y < p.y.saturating_sub(reach));
                let last = after.partition_point(|q| q.y <= p.y + reach);
                &after[first..last]
            } else {
                after
            };
            for q in candidates {
                let y = if q.y >= p.y { q.y - p.y } else { h + q.y - p.y };
                let i = y * self.pitch + x_base + q.x;
                if differences {
                    let start = i + p.x + 1 - p.end;
                    let p_len = p.end - p.x;
                    let q_len = q.end - q.x;
                    self.values[start] += 1;
                    self.values[start + p_len] -= 1;
                    self.values[start + q_len] -= 1;
                    self.values[start + p_len + q_len] += 1;
                } else if self.differences {
                    self.values[i] += 1;
                    self.values[i + 1] -= 2;
                    self.values[i + 2] += 1;
                } else {
                    self.values[i] += 1;
                }
            }
        }
    }

    fn finish(mut self) -> Vec<i64> {
        let w = self.size.w;
        for y in 0..self.size.h {
            let row = &mut self.values[y * self.pitch..][..w];
            if self.differences {
                let (mut slope, mut value) = (0, 0);
                for v in row.iter_mut() {
                    slope += *v;
                    value += slope;
                    *v = value;
                }
            }
            row.rotate_left(self.origin);
            self.values
                .copy_within(y * self.pitch..y * self.pitch + w, y * w);
        }
        self.values.truncate(w * self.size.h);
        self.values
    }
}

fn run_pair_count(before: &[Run], after: &[Run], reach: Option<usize>) -> usize {
    if let Some(reach) = reach {
        before.iter().fold(0_usize, |total, p| {
            let first = after.partition_point(|q| q.y < p.y.saturating_sub(reach));
            let last = after.partition_point(|q| q.y <= p.y + reach);
            total.saturating_add(last - first)
        })
    } else {
        before.len().saturating_mul(after.len())
    }
}

fn mask_correlation(
    before: &[Run],
    after: &[Run],
    mask: Size,
    reach: Option<usize>,
) -> Result<Vec<i64>, Error> {
    let size = Size {
        w: 2 * mask.w,
        h: 2 * mask.h,
    };
    let padded = fft::dimensions(size);
    if run_pair_count(before, after, reach) > fft::work(padded, 1) {
        return Ok(fft::correlate([(before, after)], padded, size, mask, mask, reach)?.0);
    }
    let mut table = Correlation::new(size, mask.w)?;
    table.add(before, after, reach);
    Ok(table.finish())
}

fn signed(index: usize, size: usize) -> i64 {
    if index < size / 2 {
        index as i64
    } else {
        index as i64 - size as i64
    }
}

fn distance((dy, dx): Shift) -> i64 {
    dy * dy + dx * dx
}

fn peak(values: &[i64], size: Size) -> Option<(i64, usize, usize)> {
    let mut top = 0;
    let mut first = 0;
    for (i, &v) in values.iter().enumerate() {
        if v > top {
            top = v;
            first = i;
        }
    }
    (top > 0).then(|| (top, first / size.w, first % size.w))
}

fn suppress(values: &mut [i64], size: Size, y: usize, x: usize, wrap: bool) {
    for dy in -2..=2 {
        for dx in -2..=2 {
            let (mut y, mut x) = (y as i64 + dy, x as i64 + dx);
            if wrap {
                y = y.rem_euclid(size.h as i64);
                x = x.rem_euclid(size.w as i64);
            }
            if y >= 0 && y < size.h as i64 && x >= 0 && x < size.w as i64 {
                values[y as usize * size.w + x as usize] = 0;
            }
        }
    }
}

/// 輪郭の相関からずれの候補を返す。numpy 版は `fill_tracking.estimate_shifts_numpy`。
pub fn estimate_shifts(
    previous: &[u8],
    current: &[u8],
    region: &[u8],
    width: usize,
    height: usize,
    count: usize,
    margin: Option<i64>,
) -> Result<Vec<Shift>, Error> {
    let size = Size {
        w: width,
        h: height,
    };
    for mask in [previous, current, region] {
        size.check(mask, 1)?;
    }
    let Some(b) = bounds(region, size) else {
        return Ok(vec![]);
    };
    let margin = margin.unwrap_or_else(|| 64.max(3 * (b.x1 - b.x0).max(b.y1 - b.y0)));
    let window = b.expand(margin).intersect(size.rect());
    let ws = window.size();
    if count == 0 || ws.w < 8 || ws.h < 8 {
        return Ok(vec![]);
    }
    let template_window = b.expand(3).intersect(size.rect());
    let mut template = crop(previous, size, template_window);
    outline(
        &crop(region, size, template_window),
        &mut template,
        template_window.size(),
    );
    let mut before = vec![0; ws.w * ws.h];
    copy_rect(
        &template,
        template_window.size(),
        &mut before,
        ws,
        (
            template_window.y0 - window.y0,
            template_window.x0 - window.x0,
        ),
    );
    let step = ws.w.max(ws.h).div_ceil(256);
    let before = mask_runs(&before, ws, step);
    let after = mask_runs(&crop(current, size, window), ws, step);
    if before.is_empty() || after.is_empty() {
        return Ok(vec![]);
    }
    let cs = Size {
        w: 2 * ws.w.div_ceil(step),
        h: 2 * ws.h.div_ceil(step),
    };
    let mask = Size {
        w: cs.w / 2,
        h: cs.h / 2,
    };
    let mut values = mask_correlation(&before, &after, mask, None)?;
    let mut peaks = Vec::new();
    let limit = margin.div_euclid(step as i64);
    for _ in 0..count.saturating_mul(4) {
        let Some((v, y, x)) = peak(&values, cs) else {
            break;
        };
        suppress(&mut values, cs, y, x, true);
        let (dy, dx) = (signed(y, cs.h), signed(x, cs.w));
        if dy.abs() <= limit && dx.abs() <= limit {
            peaks.push((v, (dy * step as i64, dx * step as i64)));
        }
    }
    if let Some(&(top, _)) = peaks.first() {
        let near = peaks.partition_point(|&(v, _)| v as f64 >= top as f64 * PEAK_TOLERANCE);
        peaks[..near].sort_by_key(|&(_, shift)| distance(shift));
    }
    Ok(peaks
        .into_iter()
        .take(count)
        .map(|(_, shift)| shift)
        .collect())
}

fn best_offset(before: &[u8], after: &[u8], size: Size, radius: usize) -> Result<Shift, Error> {
    if before.iter().filter(|&&v| v != 0).take(6).count() < 6 {
        return Ok((0, 0));
    }
    let step = size.w.max(size.h).div_ceil(256);
    let before = mask_runs(before, size, step);
    let after = mask_runs(after, size, step);
    if after.is_empty() {
        return Ok((0, 0));
    }
    let reach = (radius / step) as i64;
    let cs = Size {
        w: 2 * size.w.div_ceil(step),
        h: 2 * size.h.div_ceil(step),
    };
    let mask = Size {
        w: cs.w / 2,
        h: cs.h / 2,
    };
    let values = mask_correlation(&before, &after, mask, Some(reach as usize))?;
    // 正の添字、次に負の添字。numpy のパディング長でもこの順になる。
    let offsets = || (0..=reach).chain(-reach..0);
    let index = |dy: i64, dx: i64| {
        let y = if dy < 0 { cs.h as i64 + dy } else { dy };
        let x = if dx < 0 { cs.w as i64 + dx } else { dx };
        y as usize * cs.w + x as usize
    };
    let mut top = 0;
    for dy in offsets() {
        for dx in offsets() {
            top = top.max(values[index(dy, dx)]);
        }
    }
    if top == 0 {
        return Ok((0, 0));
    }
    let mut best = (0, 0);
    let mut nearest = i64::MAX;
    for dy in offsets() {
        for dx in offsets() {
            let d = distance((dy, dx));
            if d < nearest && values[index(dy, dx)] as f64 >= top as f64 * PEAK_TOLERANCE {
                best = (dy * step as i64, dx * step as i64);
                nearest = d;
            }
        }
    }
    Ok(best)
}

fn tile_edges(start: i64, end: i64, tiles: usize) -> Vec<i64> {
    let step = (end - start) as f64 / tiles as f64;
    (0..tiles)
        .map(|i| (i as f64 * step + start as f64).round_ties_even() as i64)
        .chain(std::iter::once(end))
        .collect()
}

/// 区画ごとにずらしたマスクを返す。numpy 版は `fill_tracking.warp_region_numpy`。
pub fn warp_region(
    previous: &[u8],
    current: &[u8],
    region: &[u8],
    width: usize,
    height: usize,
    shift: Shift,
    origin: Shift,
) -> Result<Option<Vec<u8>>, Error> {
    let size = Size {
        w: width,
        h: height,
    };
    for mask in [previous, current, region] {
        size.check(mask, 1)?;
    }
    let Some(b) = bounds(region, size) else {
        return Ok(None);
    };
    if origin.0 < 0
        || origin.1 < 0
        || origin.0 > i32::MAX as i64 - height as i64
        || origin.1 > i32::MAX as i64 - width as i64
    {
        return Err(invalid());
    }
    let bs = b.size();
    let (columns, rows) = ((bs.w / 32).clamp(1, 6), (bs.h / 32).clamp(1, 6));
    if columns * rows < 2 {
        return Ok(None);
    }
    // タイル境界の偶数丸めはキャンバス座標で行う。
    let edges = |start, end, n, offset| {
        tile_edges(start + offset, end + offset, n)
            .into_iter()
            .map(|edge| edge - offset)
            .collect::<Vec<_>>()
    };
    let xs = edges(b.x0, b.x1, columns, origin.1);
    let ys = edges(b.y0, b.y1, rows, origin.0);
    let radius = (bs.w.max(bs.h) / 4).clamp(6, 64);
    let mut out = vec![0; region.len()];
    for y in ys.windows(2) {
        for x in xs.windows(2) {
            let tile = Rect {
                x0: x[0],
                y0: y[0],
                x1: x[1],
                y1: y[1],
            };
            let part = crop(region, size, tile);
            if !part.contains(&1) {
                continue;
            }
            let window = tile.expand(radius as i64);
            let ws = window.size();
            let mut local = vec![0; ws.w * ws.h];
            copy_rect(
                &part,
                tile.size(),
                &mut local,
                ws,
                (radius as i64, radius as i64),
            );
            let mut template = crop(previous, size, window);
            outline(&local, &mut template, ws);
            let target_window = window.translate(shift);
            let (dy, dx) = if target_window.size().w == ws.w && target_window.size().h == ws.h {
                best_offset(&template, &crop(current, size, target_window), ws, radius)?
            } else {
                (0, 0)
            };
            let dest = tile.translate(shift).translate((dy, dx));
            let overlap = dest.intersect(size.rect());
            let w = overlap.size().w;
            if w == 0 {
                continue;
            }
            for y in overlap.y0..overlap.y1 {
                let i = (y - dest.y0) as usize * tile.size().w + (overlap.x0 - dest.x0) as usize;
                let j = y as usize * width + overlap.x0 as usize;
                for (a, b) in out[j..j + w].iter_mut().zip(&part[i..i + w]) {
                    *a |= *b;
                }
            }
        }
    }
    Ok(Some(out))
}

fn rgba(pixel: &[u8]) -> u32 {
    if pixel[3] == 0 {
        0
    } else {
        u32::from_le_bytes([pixel[0], pixel[1], pixel[2], pixel[3]])
    }
}

struct Colors {
    label: u32,
    count: usize,
    before: Vec<Run>,
    after: Vec<Run>,
}

fn color_runs(
    pixels: &[u8],
    size: Size,
    region: Option<&[u8]>,
    colors: &mut [Colors],
    before: bool,
) {
    let indices: HashMap<_, _> = colors
        .iter()
        .enumerate()
        .map(|(i, c)| (c.label, i))
        .collect();
    for (y, row) in pixels.chunks_exact(size.w * 4).enumerate() {
        let labels = row.chunks_exact(4).enumerate().map(|(x, pixel)| {
            let keep = region.is_none_or(|mask| mask[y * size.w + x] == 0);
            keep.then(|| rgba(pixel))
        });
        let mut labels = labels.enumerate().peekable();
        while let Some((x, label)) = labels.next() {
            let mut end = x + 1;
            while labels.next_if(|&(_, next)| next == label).is_some() {
                end += 1;
            }
            if let Some(i) = label.and_then(|v| indices.get(&v)) {
                let runs = if before {
                    &mut colors[*i].before
                } else {
                    &mut colors[*i].after
                };
                runs.push(Run { y, x, end });
            }
        }
    }
}

#[derive(Clone, Copy)]
struct RunRect {
    x: usize,
    y: usize,
    w: usize,
    h: usize,
}

fn rectangle_count(mut runs: &[Run]) -> usize {
    let mut count = 0;
    let mut previous: &[Run] = &[];
    while let Some(first) = runs.first() {
        let end = runs.partition_point(|r| r.y == first.y);
        let (row, remaining) = runs.split_at(end);
        for run in row {
            while previous.first().is_some_and(|p| p.x < run.x) {
                previous = &previous[1..];
            }
            count += usize::from(
                !previous
                    .first()
                    .is_some_and(|p| p.y + 1 == run.y && p.x == run.x && p.end == run.end),
            );
        }
        previous = row;
        runs = remaining;
    }
    count
}

fn merge_rows(runs: &[Run]) -> Vec<RunRect> {
    let mut runs = runs.to_vec();
    runs.sort_unstable_by_key(|r| (r.x, r.end, r.y));
    let mut rectangles: Vec<RunRect> = Vec::new();
    for &Run { x, y, end } in runs.iter() {
        let w = end - x;
        if let Some(last) = rectangles.last_mut() {
            if last.x == x && last.w == w && last.y + last.h == y {
                last.h += 1;
                continue;
            }
        }
        rectangles.push(RunRect { x, y, w, h: 1 });
    }
    rectangles
}

fn color_correlation(colors: &[Colors], before: Size, after: Size) -> Result<Vec<i64>, Error> {
    let size = Size {
        w: before.w + after.w,
        h: before.h + after.h,
    };
    let mut active = 0;
    let pairs = colors.iter().fold(0_usize, |n, color| {
        let pairs = run_pair_count(&color.before, &color.after, None);
        active += usize::from(pairs != 0);
        n.saturating_add(pairs)
    });
    // 長方形対は16点、横区間対は最大4点の差分を書き込む。
    let rectangle_work = if pairs > size.w.saturating_mul(size.h) {
        colors
            .iter()
            .filter(|c| !c.before.is_empty() && !c.after.is_empty())
            .fold(0_usize, |n, c| {
                n.saturating_add(
                    rectangle_count(&c.before)
                        .saturating_mul(rectangle_count(&c.after))
                        .saturating_mul(4),
                )
            })
    } else {
        usize::MAX
    };
    let padded = fft::dimensions(size);
    if pairs.min(rectangle_work) > fft::work(padded, active) {
        let pairs = colors
            .iter()
            .map(|c| (c.before.as_slice(), c.after.as_slice()));
        return Ok(fft::correlate(pairs, padded, size, before, after, None)?.0);
    }
    let mut table = Correlation::new(size, before.w)?;
    if rectangle_work >= pairs {
        for color in colors {
            table.add(&color.before, &color.after, None);
        }
        return Ok(table.finish());
    }

    let pitch = table.pitch;
    let origin_y = before.h - 1;
    // 各軸の区間の重なりは台形。二階差分は4点で、長方形の積は16点になる。
    // 表の末尾を越える差分は、復元する範囲の外なので書く必要がない。
    for color in colors
        .iter()
        .filter(|c| !c.before.is_empty() && !c.after.is_empty())
    {
        let a = merge_rows(&color.before);
        let b = merge_rows(&color.after);
        for p in &a {
            let x_base = table.origin + 1 - p.x - p.w;
            let y_base = origin_y + 1 - p.y - p.h;
            for q in &b {
                let x = x_base + q.x;
                let y = y_base + q.y;
                for (y, weight) in [(y, 1), (y + p.h, -1), (y + q.h, -1), (y + p.h + q.h, 1)] {
                    if y < size.h {
                        let i = y * pitch + x;
                        table.values[i] += weight;
                        table.values[i + p.w] -= weight;
                        table.values[i + q.w] -= weight;
                        table.values[i + p.w + q.w] += weight;
                    }
                }
            }
        }
    }
    let mut slopes = vec![0; size.w];
    let mut values = vec![0; size.w];
    for row in table.values.chunks_exact_mut(pitch) {
        for ((v, slope), value) in row.iter_mut().zip(&mut slopes).zip(&mut values) {
            *slope += *v;
            *value += *slope;
            *v = *value;
        }
    }
    table.differences = true;
    let mut result = table.finish();
    result.rotate_left(origin_y * size.w);
    Ok(result)
}

/// 切り出した色とマスクからずれの候補を返す。numpy 版は `fill_tracking.estimate_color_shifts_numpy`。
#[allow(clippy::too_many_arguments)]
pub fn estimate_color_shifts(
    previous: &[u8],
    current: &[u8],
    region: &[u8],
    before_width: usize,
    before_height: usize,
    after_width: usize,
    after_height: usize,
    offset: Shift,
    count: usize,
) -> Result<Vec<Shift>, Error> {
    let bs = Size {
        w: before_width,
        h: before_height,
    };
    let ts = Size {
        w: after_width,
        h: after_height,
    };
    bs.check(previous, 4)?;
    ts.check(current, 4)?;
    bs.check(region, 1)?;
    if count == 0 || previous.is_empty() || current.is_empty() {
        return Ok(vec![]);
    }
    let mut frequencies = HashMap::new();
    for (pixel, &inside) in previous.chunks_exact(4).zip(region) {
        if inside == 0 {
            *frequencies.entry(rgba(pixel)).or_insert(0) += 1;
        }
    }
    let mut colors: Vec<_> = frequencies
        .into_iter()
        .map(|(label, count)| Colors {
            label,
            count,
            before: Vec::new(),
            after: Vec::new(),
        })
        .collect();
    colors.sort_unstable_by_key(|c| (std::cmp::Reverse(c.count), c.label));
    colors.truncate(6);
    if colors.is_empty() {
        return Ok(vec![]);
    }
    color_runs(previous, bs, Some(region), &mut colors, true);
    color_runs(current, ts, None, &mut colors, false);
    let cs = Size {
        w: bs.w + ts.w,
        h: bs.h + ts.h,
    };
    let mut values = color_correlation(&colors, bs, ts)?;
    let mut shifts = Vec::new();
    for _ in 0..count {
        let Some((_, y, x)) = peak(&values, cs) else {
            break;
        };
        shifts.push((
            offset.0.saturating_add(signed(y, cs.h)),
            offset.1.saturating_add(signed(x, cs.w)),
        ));
        suppress(&mut values, cs, y, x, false);
    }
    Ok(shifts)
}

fn center(mask: &[u8], size: Size) -> Option<Shift> {
    let b = bounds(mask, size)?;
    let (mut sx, mut sy, mut n) = (0_u64, 0_u64, 0_u64);
    for y in b.y0..b.y1 {
        let start = y as usize * size.w + b.x0 as usize;
        let row = &mask[start..][..b.size().w];
        let mut row_count = 0;
        for (x, &v) in row.iter().enumerate() {
            let v = u64::from(v != 0);
            row_count += v;
            sx += x as u64 * v;
        }
        sx += b.x0 as u64 * row_count;
        sy += y as u64 * row_count;
        n += row_count;
    }
    let round = |sum| (sum as f64 / n as f64 + 0.5).floor() as i64;
    Some((round(sy), round(sx)))
}

/// マスクの重心を返す。numpy 版は `fill_tracking._center`。
pub fn region_center(region: &[u8], width: usize, height: usize) -> Result<Option<Shift>, Error> {
    let size = Size {
        w: width,
        h: height,
    };
    size.check(region, 1)?;
    Ok(center(region, size))
}

/// 重心をそろえた窓の色と形の一致度を返す。numpy 版は `fill_tracking.context_similarity_numpy`。
#[allow(clippy::too_many_arguments)]
pub fn context_similarity(
    previous: &[u8],
    previous_region: &[u8],
    before_dimensions: (usize, usize),
    before_origin: Shift,
    current: &[u8],
    current_region: &[u8],
    after_dimensions: (usize, usize),
    after_origin: Shift,
) -> Result<f64, Error> {
    let (bw, bh) = before_dimensions;
    let (aw, ah) = after_dimensions;
    let bs = Size { w: bw, h: bh };
    let ts = Size { w: aw, h: ah };
    bs.check(previous, 4)?;
    ts.check(current, 4)?;
    bs.check(previous_region, 1)?;
    ts.check(current_region, 1)?;
    let shared = bs
        .rect()
        .translate(before_origin)
        .intersect(ts.rect().translate(after_origin));
    let mut frequencies = HashMap::<u32, (u64, u64)>::new();
    let (mut compared, mut same, mut intersection) = (0_u64, 0_u64, 0_u64);
    for y in shared.y0..shared.y1 {
        for x in shared.x0..shared.x1 {
            let i = (y - before_origin.0) as usize * bw + (x - before_origin.1) as usize;
            let j = (y - after_origin.0) as usize * aw + (x - after_origin.1) as usize;
            match (previous_region[i] != 0, current_region[j] != 0) {
                (false, false) => {
                    let a = rgba(&previous[i * 4..][..4]);
                    let b = rgba(&current[j * 4..][..4]);
                    frequencies.entry(a).or_default().0 += 1;
                    frequencies.entry(b).or_default().1 += 1;
                    compared += 1;
                    same += u64::from(a == b);
                }
                (true, true) => intersection += 1,
                _ => {}
            }
        }
    }
    let union = previous_region
        .iter()
        .chain(current_region)
        .map(|&v| u64::from(v != 0))
        .sum::<u64>()
        - intersection;
    let expected = frequencies
        .values()
        .map(|&(a, b)| u128::from(a) * u128::from(b))
        .sum::<u128>();
    let surroundings =
        if compared == 0 || expected as f64 / (u128::from(compared).pow(2) as f64) > 0.9 {
            0.0
        } else {
            same as f64 / compared as f64
        };
    let shape = intersection as f64 / union.max(1) as f64;
    Ok(0.7 * surroundings + 0.3 * shape)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn run_correlation_matches_pixel_pairs() {
        let before = [
            vec![Run { y: 1, x: 0, end: 1 }],
            vec![Run { y: 0, x: 1, end: 4 }, Run { y: 1, x: 2, end: 4 }],
            vec![Run { y: 1, x: 3, end: 4 }],
        ];
        let after = [
            vec![Run { y: 0, x: 4, end: 5 }],
            vec![Run { y: 0, x: 0, end: 4 }, Run { y: 2, x: 3, end: 5 }],
            vec![Run { y: 2, x: 0, end: 1 }],
        ];
        let size = Size { w: 9, h: 5 };
        let mut correlation = Correlation::new(size, 4).unwrap();
        let mut expected = vec![0; size.w * size.h];
        for (before, after) in before.iter().zip(&after) {
            correlation.add(before, after, None);
            for p in before {
                for q in after {
                    for x in p.x..p.end {
                        for xx in q.x..q.end {
                            let y = (size.h + q.y - p.y) % size.h;
                            expected[y * size.w + (size.w + xx - x) % size.w] += 1;
                        }
                    }
                }
            }
        }
        assert_eq!(correlation.finish(), expected);
    }

    #[test]
    fn rectangle_correlation_matches_row_correlation() {
        let before = Size { w: 7, h: 29 };
        let size = Size { w: 15, h: 63 };
        let colors = [Colors {
            label: 0,
            count: 0,
            before: (0..before.h)
                .flat_map(|y| [Run { y, x: 0, end: 3 }, Run { y, x: 4, end: 7 }])
                .collect(),
            after: (5..33)
                .flat_map(|y| [Run { y, x: 1, end: 5 }, Run { y, x: 7, end: 8 }])
                .collect(),
        }];
        let mut expected = Correlation::new(size, before.w).unwrap();
        expected.add(&colors[0].before, &colors[0].after, None);
        assert_eq!(
            color_correlation(
                &colors,
                before,
                Size {
                    w: size.w - before.w,
                    h: size.h - before.h
                }
            )
            .unwrap(),
            expected.finish()
        );
    }

    fn check_fft_table(before: Size, after: Size) {
        let output = Size {
            w: before.w + after.w,
            h: before.h + after.h,
        };
        let mut random = 937_u32;
        let mut next = || {
            random ^= random << 13;
            random ^= random >> 17;
            random ^= random << 5;
            random
        };
        let mut masks = |size: Size| {
            let mut patterned = vec![0; size.w * size.h];
            let mut noise = patterned.clone();
            for y in 0..size.h {
                for x in 0..size.w {
                    // 大きな窓の全域に分布させ、直接法の検証時間も抑える。
                    let i = y * size.w + x;
                    patterned[i] = u8::from(y % 19 < 2 && x % 17 < 2);
                    noise[i] = u8::from(patterned[i] == 0 && next().is_multiple_of(127));
                }
            }
            [mask_runs(&patterned, size, 1), mask_runs(&noise, size, 1)]
        };
        let a = masks(before);
        let b = masks(after);
        let pairs = || a.iter().zip(&b).map(|(p, q)| (p.as_slice(), q.as_slice()));
        let padded = fft::dimensions(output);
        let (actual, error) = fft::correlate(pairs(), padded, output, before, after, None).unwrap();
        eprintln!("{}×{}: maximum rounding error {error:e}", after.w, after.h);
        assert!(error < 0.5);
        let mut expected = Correlation::new(output, before.w).unwrap();
        for (p, q) in pairs() {
            expected.add(p, q, None);
        }
        assert_eq!(actual, expected.finish());
        if after.w < 100 {
            let mut separate = vec![0; actual.len()];
            for pair in pairs() {
                if pair.0.is_empty() || pair.1.is_empty() {
                    continue;
                }
                let (one, _) = fft::correlate([pair], padded, output, before, after, None).unwrap();
                for (v, w) in separate.iter_mut().zip(one) {
                    *v += w;
                }
            }
            assert_eq!(actual, separate);
            let reach = Some(7);
            let mut expected = Correlation::new(output, before.w).unwrap();
            for (p, q) in pairs() {
                expected.add(p, q, reach);
            }
            let (limited, _) =
                fft::correlate(pairs(), padded, output, before, after, reach).unwrap();
            assert_eq!(limited, expected.finish());
        }
    }

    #[test]
    fn fft_sum_matches_direct_and_separately_rounded_colors() {
        check_fft_table(Size { w: 27, h: 33 }, Size { w: 56, h: 48 });
    }

    #[test]
    #[ignore = "full HD and 4K search windows"]
    fn fft_rounding_on_full_canvas_windows() {
        for after in [Size { w: 1920, h: 1080 }, Size { w: 3840, h: 2160 }] {
            check_fft_table(Size { w: 103, h: 69 }, after);
        }
    }

    #[test]
    fn equal_peaks_are_found_in_row_order() {
        let size = Size { w: 7, h: 6 };
        let mut table = vec![0; size.w * size.h];
        table[4] = 7;
        table[7 + 1] = 7;
        assert_eq!(peak(&table, size), Some((7, 0, 4)));
        suppress(&mut table, size, 0, 4, true);
        assert_eq!(peak(&table, size), Some((7, 1, 1)));

        let size = Size { w: 9, h: 9 };
        let mut template = vec![0; 81];
        let mut target = vec![0; 81];
        for y in 1..=6 {
            template[y * 9 + 4] = 1;
            target[y * 9 + 2] = 1;
            target[y * 9 + 6] = 1;
        }
        assert_eq!(best_offset(&template, &target, size, 2).unwrap(), (0, 2));
    }

    #[test]
    fn centroid_rounds_half_up_and_tiles_round_ties_to_even() {
        assert_eq!(region_center(&[1, 1, 0, 0], 2, 2).unwrap(), Some((0, 1)));
        assert_eq!(tile_edges(0, 65, 2), [0, 32, 65]);
        assert_eq!(tile_edges(1, 66, 2), [1, 34, 66]);
        assert!(region_center(&[1], 2, 2).is_err());
    }
}
