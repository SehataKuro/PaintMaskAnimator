//! 位置合わせ済みの区画から部位の色を選ぶ。

use crate::error::Error;
use std::collections::{HashMap, HashSet};

/// 行優先の矩形窓の位置と大きさ。`(左, 上, 幅, 高さ)` を画素単位で表す。
pub type Rect = (i32, i32, i32, i32);

fn offset((left, top, width, height): Rect, y: i64, x: i64) -> Option<usize> {
    let (y, x) = (y - i64::from(top), x - i64::from(left));
    (y >= 0 && y < i64::from(height) && x >= 0 && x < i64::from(width))
        .then(|| y as usize * width as usize + x as usize)
}

/// 区画の採色に必要な行優先のバッファ。値とラベルはそれぞれネイティブエンディアンの i64 と i32。
pub struct Tile<'a> {
    /// 移動前の区画と、その中の filled.local および実際の輪郭のマスク。
    pub target: Rect,
    /// 区画内の部位マスク。
    pub filled: &'a [u8],
    /// 区画内の輪郭マスク。
    pub outline: &'a [u8],
    /// 画像内に切り詰めた移動先の区画。
    pub source: Rect,
    /// 往路と復路の平行移動 `(dy, dx, back_y, back_x)`。
    pub shift: (i32, i32, i32, i32),
    /// 移動前の黒マスクの窓とデータ。近傍を調べるため区画から1画素広げる。
    pub target_black: (Rect, &'a [u8]),
    /// 移動先の黒マスクの窓とデータ。近傍を調べるため区画から1画素広げる。
    pub source_black: (Rect, &'a [u8]),
    /// 移動先の wide_boundary と wide_primary。
    pub wide: [&'a [u8]; 2],
    /// 移動先の source.boundary、guide の open、undecided、black、primary。
    pub excluded: [&'a [u8]; 5],
    /// 移動先の確定マスク。未指定なら確定条件を省く。
    pub confirmed: Option<&'a [u8]>,
    /// 移動先の色。
    pub values: &'a [u8],
    /// 移動先の領域ラベル。
    pub labels: &'a [u8],
}

fn near_black((rect, black): (Rect, &[u8]), y: i64, x: i64) -> bool {
    // 窓は画像端で切ってある。端の画素を繰り返し参照しても OR の結果は同じ。
    (y - 1..=y + 1)
        .any(|ny| (x - 1..=x + 1).any(|nx| offset(rect, ny, nx).is_some_and(|i| black[i] != 0)))
}

impl Tile<'_> {
    fn points(&self, filled: bool) -> impl Iterator<Item = (i64, i64)> + '_ {
        let (left, top, width, _) = self.target;
        let mask = if filled { self.filled } else { self.outline };
        mask.iter()
            .enumerate()
            .filter(|&(_, &set)| set != 0)
            .map(move |(i, _)| {
                (
                    i64::from(top) + (i / width as usize) as i64,
                    i64::from(left) + (i % width as usize) as i64,
                )
            })
    }

    fn aligned(&self) -> bool {
        let (dy, dx, _, _) = self.shift;
        let (mut hits, mut total) = (0, 0);
        for (y, x) in self.points(false) {
            total += 1;
            let (sy, sx) = (y + i64::from(dy), x + i64::from(dx));
            let Some(i) = offset(self.source, sy, sx) else {
                continue;
            };
            if self.wide[0][i] != 0
                && if near_black(self.target_black, y, x) {
                    near_black(self.source_black, sy, sx)
                } else {
                    self.wide[1][i] != 0
                }
            {
                hits += 1;
            }
        }
        total != 0 && hits * 2 >= total
    }
}

fn majority(counts: &HashMap<i64, usize>, total: usize, threshold: f64) -> Option<[u8; 3]> {
    let (&value, &count) = counts
        .iter()
        .max_by_key(|&(value, count)| (*count, std::cmp::Reverse(*value)))?;
    if (count as f64) / (total as f64) < threshold {
        return None;
    }
    let [r, g, b, ..] = value.to_le_bytes();
    let rgb = [r, g, b];
    let strong = rgb.iter().filter(|&&v| v >= 215).count();
    // _paintable と同じ黒線および色トレス線の色を除く。
    (value != 0
        && rgb.iter().any(|&v| v > 48)
        && !((1..=2).contains(&strong) && rgb.iter().all(|&v| v >= 215 || v <= 60)))
    .then_some(rgb)
}

/// `PartTracker._tiled_color` の位置合わせ後と同じ RGB または採用なしを返す。
/// 区画は行優先の窓、local は部位の外接矩形とマスク、sizes はラベルごとの面積。
pub fn tiled_color(
    tiles: &[Tile<'_>],
    local: (Rect, &[u8]),
    size: usize,
    sizes: &[u64],
) -> Result<Option<[u8; 3]>, Error> {
    let mut counts = HashMap::new();
    let mut labels = HashSet::new();
    let mut sampled = 0;
    let mut tile_counts = HashMap::new();
    let mut samples = Vec::new();
    for tile in tiles.iter().filter(|tile| tile.aligned()) {
        let (dy, dx, back_y, back_x) = tile.shift;
        let mut total = 0;
        tile_counts.clear();
        samples.clear();
        for (y, x) in tile.points(true) {
            total += 1;
            let (sy, sx) = (y + i64::from(dy), x + i64::from(dx));
            let Some(i) = offset(tile.source, sy, sx) else {
                continue;
            };
            let inside = offset(local.0, sy + i64::from(back_y), sx + i64::from(back_x))
                .is_some_and(|index| local.1[index] != 0);
            if !inside
                || tile.excluded.iter().any(|mask| mask[i] != 0)
                || tile.confirmed.is_some_and(|mask| mask[i] == 0)
            {
                continue;
            }
            let value = i64::from_ne_bytes(tile.values[i * 8..][..8].try_into().unwrap());
            if value != 0 {
                *tile_counts.entry(value).or_insert(0) += 1;
                samples.push(i);
            }
        }
        if samples.len() * 2 < total || majority(&tile_counts, samples.len(), 0.9).is_none() {
            continue;
        }
        sampled += samples.len();
        for (&value, &count) in &tile_counts {
            *counts.entry(value).or_insert(0) += count;
        }
        if size < 4096 {
            labels.extend(samples.iter().map(|&i| {
                i32::from_ne_bytes(tile.labels[i * 4..][..4].try_into().unwrap()) as usize
            }));
        }
    }
    if sampled < size.div_ceil(2) {
        return Ok(None);
    }
    let color = majority(&counts, sampled, 0.95);
    if color.is_some() && size < 4096 {
        let mut area = 0u64;
        for label in labels {
            let label_size = sizes
                .get(label)
                .ok_or_else(|| Error::Invalid("領域ラベルが範囲外".into()))?;
            area = area.saturating_add(*label_size);
        }
        if area > size as u64 * 4 {
            return Ok(None);
        }
    }
    Ok(color)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn single_pixel_at_edge_uses_black_and_confirmation() {
        let rect = (0, 0, 1, 1);
        let local = (rect, &[1][..]);
        let value = 0xffc0b080_i64.to_ne_bytes();
        let mut tile = Tile {
            target: rect,
            filled: &[1],
            outline: &[1],
            source: rect,
            shift: (0, 0, 0, 0),
            target_black: local,
            source_black: local,
            wide: [&[1], &[0]],
            excluded: [&[0]; 5],
            confirmed: None,
            values: &value,
            labels: &[0; 4],
        };
        let check = |tile, sizes| tiled_color(&[tile], local, 1, sizes).unwrap();
        assert_eq!(check(Tile { ..tile }, &[4]), Some([128, 176, 192]));
        assert_eq!(check(Tile { ..tile }, &[5]), None);
        tile.confirmed = Some(&[0]);
        assert_eq!(check(Tile { ..tile }, &[1]), None);
        tile.confirmed = Some(&[1]);
        tile.shift = (0, -1, 0, 1);
        assert_eq!(check(tile, &[1]), None);
    }

    #[test]
    fn majority_uses_smallest_value_on_ties() {
        assert_eq!(
            majority(&HashMap::from([(0xc0b081, 2), (0xc0b080, 2)]), 4, 0.5),
            Some([128, 176, 192])
        );
    }
}
