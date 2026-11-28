//! 1セル分の部分分けと境界（Python の `auto_paint._Cell` と、その下の線のマスク）。
use std::cell::{OnceCell, RefCell};
use std::collections::hash_map::DefaultHasher;
use std::collections::HashMap;
use std::hash::Hasher;
use std::rc::Rc;

use crate::auto_paint::Guide;
use crate::fill::session::LineCache;
use crate::reference_check::{line_art, Bounds};
use crate::reference_scan::{pixel_values, to_rgb, CellRegions, LINE_MAX};
use crate::regions::{dilate, erode};
use crate::tools::bucket::mask_bounds;

/// 切れ目を閉じた画素を表す値（`auto_paint.GAP_VALUE`）。
pub const GAP_VALUE: u32 = 1;
/// 色トレス線とみなす原色の許容幅。
const PRIMARY_TOLERANCE: u8 = 40;
const PRIMARY_LOW_TOLERANCE: u8 = 60;
/// しるしの色とみなす面積の上限（キャンバスに対する割合）と太さ。
const MARK_MAX_SHARE: f64 = 0.02;
const MARK_THICKNESS: usize = 2;
/// 色トレス線の切れ目を閉じる幅。
const GAP_RADIUS: usize = 2;
/// 「手本でも線」とみなす距離。
const KEEP_LINE_RADIUS: usize = 2;
/// 画面端で途切れた線を延ばす長さ（`BucketOptions.extend_length` の既定値）。
const EXTEND_LENGTH: usize = 150;

/// 内容から作る照合用の鍵（Python の blake2b の要約に当たる）。
pub(crate) type Key = u128;

/// バイト列の並びから鍵を作る。
pub(crate) fn content_key(tag: u8, parts: &[&[u8]]) -> Key {
    let mut low = DefaultHasher::new();
    let mut high = DefaultHasher::new();
    low.write_u8(tag);
    high.write_u8(tag ^ 0x5a);
    high.write_u64(0x9e37_79b9_7f4a_7c15);
    for part in parts {
        low.write_usize(part.len());
        low.write(part);
        high.write_usize(part.len());
        high.write(part);
    }
    (u128::from(high.finish()) << 64) | u128::from(low.finish())
}

/// u32 の並びの鍵。
pub(crate) fn values_key(tag: u8, values: &[u32]) -> Key {
    let bytes: Vec<u8> = values.iter().flat_map(|v| v.to_le_bytes()).collect();
    content_key(tag, &[&bytes])
}

/// 0/1 マスクのどれかが立っているか。
pub(crate) fn any(mask: &[u8]) -> bool {
    mask.iter().any(|&v| v != 0)
}

/// 0/1 マスクの画素数。
pub(crate) fn count(mask: &[u8]) -> usize {
    mask.iter().filter(|&&v| v != 0).count()
}

/// 矩形の中を切り出す。
pub(crate) fn crop<T: Copy>(data: &[T], width: usize, (x0, y0, x1, y1): Bounds) -> Vec<T> {
    let mut out = Vec::with_capacity((x1 - x0) * (y1 - y0));
    for y in y0..y1 {
        out.extend_from_slice(&data[y * width + x0..y * width + x1]);
    }
    out
}

/// 色トレス線の画素（`primary_line_mask`）：原色の赤・緑・青と、2つを重ねた色。
pub fn primary_line_mask(rgba: &[u8]) -> Vec<u8> {
    let floor = 255 - PRIMARY_TOLERANCE;
    let ceiling = PRIMARY_LOW_TOLERANCE;
    rgba.chunks_exact(4)
        .map(|p| {
            let strong = p[..3].iter().filter(|&&c| c >= floor).count();
            let either = p[..3].iter().all(|&c| c >= floor || c <= ceiling);
            u8::from(either && p[3] > 0 && (1..=2).contains(&strong))
        })
        .collect()
}

/// 1色が色トレス線の色か（`_is_primary`）。
pub fn is_primary(rgb: [u8; 3]) -> bool {
    primary_line_mask(&[rgb[0], rgb[1], rgb[2], 255])[0] != 0
}

/// 塗りの色にしてよい値か（`_paintable`）。
pub fn paintable(value: u32) -> bool {
    to_rgb(value).is_some_and(|rgb| !rgb.iter().all(|&c| c <= LINE_MAX) && !is_primary(rgb))
}

/// 色分けの境界：黒の主線と色トレス線（`boundary_mask`）。
pub fn boundary_mask(rgba: &[u8]) -> Vec<u8> {
    let black = line_art(rgba, LINE_MAX);
    let primary = primary_line_mask(rgba);
    black.iter().zip(&primary).map(|(a, b)| a | b).collect()
}

/// 原画のしるしの色の画素（`mark_mask`）。
pub fn mark_mask(
    rgba: &[u8],
    width: usize,
    height: usize,
    black: &[u8],
    primary: &[u8],
) -> Vec<u8> {
    let candidate: Vec<u8> = rgba
        .chunks_exact(4)
        .zip(black.iter().zip(primary))
        .map(|(p, (b, q))| u8::from(p[3] > 0 && *b == 0 && *q == 0))
        .collect();
    let mut out = vec![0u8; candidate.len()];
    if !any(&candidate) {
        return out;
    }
    let values = pixel_values(rgba);
    let limit = candidate.len() as f64 * MARK_MAX_SHARE;
    let mut counts: HashMap<u32, usize> = HashMap::new();
    for (value, &c) in values.iter().zip(&candidate) {
        if c != 0 {
            *counts.entry(*value).or_default() += 1;
        }
    }
    let mut found: Vec<(u32, usize)> = counts.into_iter().collect();
    found.sort_unstable();
    for (value, total) in found {
        if total as f64 > limit {
            continue; // 広い面はしるしではない
        }
        let same: Vec<u8> = values
            .iter()
            .zip(&candidate)
            .map(|(v, c)| u8::from(*c != 0 && *v == value))
            .collect();
        let Some(bounds) = mask_bounds(&same, width, height) else {
            continue;
        };
        let local = crop(&same, width, bounds);
        let (lw, lh) = (bounds.2 - bounds.0, bounds.3 - bounds.1);
        if any(&erode(&local, lw, lh, MARK_THICKNESS)) {
            continue; // 太い所がある（塗り色）
        }
        for (o, s) in out.iter_mut().zip(&same) {
            *o |= s;
        }
    }
    out
}

/// 黒の主線・色トレス線・しるしの色だけを残した絵（`line_only`）。
pub fn line_only(rgba: &[u8], width: usize, height: usize) -> Vec<u8> {
    let black = line_art(rgba, LINE_MAX);
    let primary = primary_line_mask(rgba);
    let mark = mark_mask(rgba, width, height, &black, &primary);
    let mut out = vec![0u8; rgba.len()];
    for (i, pixel) in rgba.chunks_exact(4).enumerate() {
        if black[i] | primary[i] | mark[i] != 0 {
            out[i * 4..i * 4 + 4].copy_from_slice(pixel);
        }
    }
    out
}

/// 不透明な白を透明にした絵（`white_as_unpainted`）。
pub fn white_as_unpainted(rgba: &[u8]) -> Vec<u8> {
    let mut out = rgba.to_vec();
    for pixel in out.chunks_exact_mut(4) {
        if pixel[3] > 0 && pixel[..3] == [255; 3] {
            pixel.fill(0);
        }
    }
    out
}

/// 色トレス線の近くで、`radius` の幅までの切れ目をふさぐ画素（`gap_mask`）。
fn gap_mask(boundary: &[u8], primary: &[u8], width: usize, height: usize) -> Vec<u8> {
    let closed = erode(
        &dilate(boundary, width, height, GAP_RADIUS),
        width,
        height,
        GAP_RADIUS,
    );
    let near = dilate(primary, width, height, GAP_RADIUS + 1);
    closed
        .iter()
        .zip(boundary)
        .zip(&near)
        .map(|((c, b), n)| c & (1 - b) & n)
        .collect()
}

/// 1セル分の部分分けと境界。
pub struct Cell {
    pub width: usize,
    pub height: usize,
    pub pixels: Rc<Vec<u8>>,
    /// 画素の色の値（透明は 0）。
    pub values: Vec<u32>,
    pub black: Vec<u8>,
    pub primary: Vec<u8>,
    pub boundary: Vec<u8>,
    pub regions: CellRegions,
    /// 塗るセルの部分分け（既存の塗り色を境界にしない）。
    pub target: bool,
    pub wide_boundary: Vec<u8>,
    pub wide_primary: Vec<u8>,
    /// 決められずに残した色トレス線と、透明のまま残した部分。
    pub undecided: Vec<u8>,
    pub open: Vec<u8>,
    /// 塗り進める並びでの位置。
    pub index: Option<usize>,
    /// 元の線画の部分分け。
    pub initial: Option<Rc<Cell>>,
    /// 今回色を決めた画素（自動結果を部位の手本にするとき）。
    pub confirmed: Option<Vec<u8>>,
    colors: RefCell<HashMap<usize, u32>>,
    boundary_key: OnceCell<Key>,
    geometry_key: OnceCell<Key>,
    pixels_key: OnceCell<Key>,
    sampler: OnceCell<Guide>,
}

impl Cell {
    /// `_Cell(pixels, target=target)`。
    pub fn new(pixels: Rc<Vec<u8>>, width: usize, height: usize, target: bool) -> Self {
        let values = pixel_values(&pixels);
        let black = line_art(&pixels, LINE_MAX);
        let primary = primary_line_mask(&pixels);
        let boundary: Vec<u8> = black.iter().zip(&primary).map(|(a, b)| a | b).collect();
        let mut gaps = gap_mask(&boundary, &primary, width, height);
        // 通常のバケツと同じく、画面端で途切れた線を仮想延長して区切る。
        let extension = LineCache::default()
            .extend(&boundary, width, height, EXTEND_LENGTH)
            .unwrap_or_else(|_| vec![0; width * height]);
        for i in 0..gaps.len() {
            let allowed = if target {
                boundary[i] == 0
            } else {
                values[i] == 0
            };
            gaps[i] |= extension[i] & u8::from(allowed);
        }
        let regions = if target {
            let mark_pixels = mark_mask(&pixels, width, height, &black, &primary);
            let topology: Vec<u32> = (0..values.len())
                .map(|i| {
                    if gaps[i] != 0 {
                        GAP_VALUE
                    } else if boundary[i] | mark_pixels[i] != 0 {
                        values[i]
                    } else {
                        0
                    }
                })
                .collect();
            CellRegions::new(&pixels, width, height, Some(topology))
        } else {
            let region_values: Vec<u32> = values
                .iter()
                .zip(&gaps)
                .map(|(v, g)| if *g != 0 { GAP_VALUE } else { *v })
                .collect();
            CellRegions::new(&pixels, width, height, Some(region_values))
        };
        let wide_boundary = dilate(&boundary, width, height, 1);
        let wide_primary = dilate(&primary, width, height, KEEP_LINE_RADIUS);
        let size = width * height;
        Self {
            width,
            height,
            pixels,
            values,
            black,
            primary,
            boundary,
            regions,
            target,
            wide_boundary,
            wide_primary,
            undecided: vec![0; size],
            open: vec![0; size],
            index: None,
            initial: None,
            confirmed: None,
            colors: RefCell::new(HashMap::new()),
            boundary_key: OnceCell::new(),
            geometry_key: OnceCell::new(),
            pixels_key: OnceCell::new(),
            sampler: OnceCell::new(),
        }
    }

    /// 決められなかった線と透明のまま残した部分を記録する（`mark_undecided`）。
    pub fn mark_undecided(&mut self, items: &[(Bounds, Vec<u8>)], unpainted: &[(Bounds, Vec<u8>)]) {
        for (bounds, mask) in items {
            or_into(&mut self.undecided, self.width, *bounds, mask);
        }
        for (bounds, mask) in unpainted {
            or_into(&mut self.open, self.width, *bounds, mask);
        }
    }

    /// 部分の値。塗るセルでは、一色の既存塗りの部分はその色（`_TargetRegions.value`）。
    pub fn region_value(&self, index: usize) -> u32 {
        let value = self.regions.value(index);
        if !self.target || value != 0 {
            return value;
        }
        if let Some(value) = self.colors.borrow().get(&index) {
            return *value;
        }
        let bounds = self.regions.bounds[index];
        let local = self.regions.local_mask(index);
        let sample = crop(&self.values, self.width, bounds);
        let mut picked = sample
            .iter()
            .zip(&local)
            .filter(|(_, l)| **l != 0)
            .map(|(v, _)| *v);
        let first = picked.next().unwrap_or(0);
        let value = if picked.all(|v| v == first) { first } else { 0 };
        self.colors.borrow_mut().insert(index, value);
        value
    }

    /// `alignment.boundary_key`。
    pub(crate) fn boundary_key(&self) -> Key {
        *self.boundary_key.get_or_init(|| {
            let shape = [self.width.to_le_bytes(), self.height.to_le_bytes()].concat();
            content_key(1, &[&shape, &self.boundary])
        })
    }

    /// `auto_bucket.geometry_key`。
    pub(crate) fn geometry_key(&self) -> Key {
        *self.geometry_key.get_or_init(|| {
            values_key(if self.target { 2 } else { 3 }, &self.regions.values)
                ^ (self.width as u128) << 1
                ^ (self.height as u128) << 33
        })
    }

    /// `ReferenceRegions.key` の画素の部分。
    pub(crate) fn pixels_key(&self) -> Key {
        *self.pixels_key.get_or_init(|| {
            let shape = [self.width.to_le_bytes(), self.height.to_le_bytes()].concat();
            content_key(4, &[&shape, &self.pixels])
        })
    }

    /// `native.sample_region` が使う圧縮した見本。
    pub(crate) fn sampler(&self) -> &Guide {
        self.sampler.get_or_init(|| {
            let initial = self.initial.as_deref().unwrap_or(self);
            let flags: Vec<u8> = (0..self.values.len())
                .map(|i| {
                    let confirmed = self.confirmed.as_ref().map_or(1, |c| c[i]);
                    self.boundary[i]
                        | (initial.boundary[i] << 1)
                        | ((self.open[i] | self.undecided[i]) << 2)
                        | (confirmed << 3)
                })
                .collect();
            let values: Vec<u8> = self
                .values
                .iter()
                .flat_map(|v| u64::from(*v).to_ne_bytes())
                .collect();
            let labels: Vec<u8> = self
                .regions
                .labels
                .iter()
                .flat_map(|v| v.to_ne_bytes())
                .collect();
            let sizes = self.regions.sizes.iter().map(|&s| s as u64).collect();
            Guide::build(&values, &labels, &flags, sizes, self.width, self.height)
        })
    }
}

fn or_into(target: &mut [u8], width: usize, (x0, y0, x1, y1): Bounds, mask: &[u8]) {
    let w = x1 - x0;
    for y in y0..y1 {
        for x in x0..x1 {
            target[y * width + x] |= mask[(y - y0) * w + (x - x0)];
        }
    }
}

/// 最多の値と割合（`_majority`）。同数なら小さい値。
pub(crate) fn majority(values: impl IntoIterator<Item = u32>) -> (Option<u32>, f64) {
    let mut counts: HashMap<u32, usize> = HashMap::new();
    let mut total = 0usize;
    for value in values {
        *counts.entry(value).or_default() += 1;
        total += 1;
    }
    counts
        .into_iter()
        .max_by(|(a, ac), (b, bc)| ac.cmp(bc).then_with(|| b.cmp(a)))
        .map_or((None, 0.0), |(value, n)| {
            (Some(value), n as f64 / total as f64)
        })
}

/// `local` の画素を `shift` だけずらした座標（`_shifted`）。はみ出しは画像の端へ寄せ、
/// `inside` を偽にする。
pub(crate) struct Shifted {
    pub sy: Vec<usize>,
    pub sx: Vec<usize>,
    pub inside: Vec<bool>,
}

impl Shifted {
    pub(crate) fn new(
        width: usize,
        height: usize,
        local: &[u8],
        bounds: Bounds,
        (dy, dx): (i64, i64),
    ) -> Self {
        let lw = bounds.2 - bounds.0;
        let (mut sy, mut sx, mut inside) = (Vec::new(), Vec::new(), Vec::new());
        for (i, &v) in local.iter().enumerate() {
            if v == 0 {
                continue;
            }
            let y = (i / lw + bounds.1) as i64 + dy;
            let x = (i % lw + bounds.0) as i64 + dx;
            inside.push(y >= 0 && y < height as i64 && x >= 0 && x < width as i64);
            sy.push(y.clamp(0, height as i64 - 1) as usize);
            sx.push(x.clamp(0, width as i64 - 1) as usize);
        }
        Self { sy, sx, inside }
    }

    pub(crate) fn at(&self, i: usize, width: usize) -> usize {
        self.sy[i] * width + self.sx[i]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn primary_colors_and_mixtures() {
        assert!(is_primary([255, 0, 0]));
        assert!(is_primary([255, 255, 50]));
        assert!(!is_primary([255, 255, 255]));
        assert!(!is_primary([0, 0, 0]));
        assert!(!is_primary([200, 100, 0]));
        assert!(paintable(u32::from_le_bytes([200, 180, 220, 255])));
        assert!(!paintable(u32::from_le_bytes([255, 0, 0, 255])));
        assert!(!paintable(0));
    }

    #[test]
    fn majority_prefers_the_smaller_value_on_ties() {
        assert_eq!(majority([3, 5, 5, 3]), (Some(3), 0.5));
        assert_eq!(majority([]), (None, 0.0));
    }

    #[test]
    fn white_becomes_transparent() {
        assert_eq!(
            white_as_unpainted(&[255, 255, 255, 255, 1, 2, 3, 4]),
            vec![0, 0, 0, 0, 1, 2, 3, 4]
        );
    }
}
