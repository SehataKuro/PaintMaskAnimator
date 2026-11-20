//! 共有変形場で移動した輪郭と内部を照合する。

use crate::tiled_color::{most_frequent, near_black, offset, paintable_rgb, Rect};
use crate::Error;
use std::collections::{HashMap, HashSet};

/// 行優先の矩形と、その中の1画素1バイトのマスク。
pub type MaskWindow<'a> = (Rect, &'a [u8]);

/// 元のマスクの非ゼロ点と同じ行優先順の変形結果。
/// 前4面はネイティブエンディアンの i64、最後は1点1バイト。
pub struct Coordinates<'a> {
    /// 往路の行・列、復路の行・列。
    pub axes: [&'a [u8]; 4],
    /// 経路が画像内に収まった点。
    pub consistent: &'a [u8],
}

impl Coordinates<'_> {
    fn points(&self) -> impl Iterator<Item = ([i64; 4], bool)> + '_ {
        self.consistent.iter().enumerate().map(|(i, &ok)| {
            let xy = self
                .axes
                .map(|axis| i64::from_ne_bytes(axis[i * 8..][..8].try_into().unwrap()));
            (xy, ok != 0)
        })
    }
}

/// 輪郭の照合に使う行優先の窓と座標。入力は検証済みとする。
pub struct Outline<'a> {
    /// 部位を1画素膨張させて求めた外周マスク。
    pub points: MaskWindow<'a>,
    /// 外周と target.boundary の交差に対応する往復座標。
    pub coordinates: Coordinates<'a>,
    /// 往復位置を許容する輪郭帯。
    pub band: MaskWindow<'a>,
    /// 外周と同じ矩形の target.boundary。
    pub target_boundary: &'a [u8],
    /// 外周と同じ矩形の target.black。
    pub target_black: &'a [u8],
    /// 画像内に収まる往路の座標を囲む窓。
    pub source: Rect,
    /// 移動先の wide_boundary、wide_primary。
    pub wide: [&'a [u8]; 2],
    /// 移動先から画像の範囲内で1画素広げた黒マスクの窓。
    pub source_black: MaskWindow<'a>,
}

/// 内部の採色に使う行優先の窓と座標。入力は検証済みとする。
pub struct Interior<'a> {
    /// 元の部位マスク。
    pub local: MaskWindow<'a>,
    /// 部位マスクの非ゼロ点に対応する往復座標。
    pub coordinates: Coordinates<'a>,
    /// 画像内に収まる往路の座標を囲む窓。
    pub source: Rect,
    /// 移動先の source.boundary、guide の open、undecided、black、primary。
    pub excluded: [&'a [u8]; 5],
    /// 移動先の確定マスク。None の場合は確定条件を省く。
    pub confirmed: Option<&'a [u8]>,
    /// 移動先の色。ネイティブエンディアンの i64。
    pub values: &'a [u8],
    /// 移動先の領域ラベル。ネイティブエンディアンの i32。
    /// 部位が4096画素以上なら空でよい。
    pub labels: &'a [u8],
}

/// 照合を通過した部位の内部を集計し、採用色の RGB または採用なしを返す。
/// 入力は検証済みの行優先の矩形窓と往復座標、面積はラベル順の i64 バッファ。
pub fn interior_color(interior: &Interior<'_>, sizes: &[u8]) -> Result<Option<[u8; 3]>, Error> {
    let size = interior.coordinates.consistent.len();
    let mut counts = HashMap::new();
    let mut samples = Vec::new();
    let mut known = 0;
    for ([sy, sx, by, bx], consistent) in interior.coordinates.points() {
        let Some(i) = offset(interior.source, sy, sx) else {
            continue;
        };
        if !consistent
            || !contains(interior.local, by, bx)
            || interior.excluded.iter().any(|mask| mask[i] != 0)
            || interior.confirmed.is_some_and(|mask| mask[i] == 0)
        {
            continue;
        }
        let value = i64::from_ne_bytes(interior.values[i * 8..][..8].try_into().unwrap());
        if value != 0 {
            known += 1;
            *counts.entry(value).or_insert(0) += 1;
            if size < 4096 {
                samples.push((i, value));
            }
        }
    }
    if (known as f64) < size as f64 * 0.7 {
        return Ok(None);
    }
    let Some((value, matched)) = most_frequent(&counts) else {
        return Ok(None);
    };
    if (matched as f64) / (known as f64) < 0.95 {
        return Ok(None);
    }
    let Some(color) = paintable_rgb(value) else {
        return Ok(None);
    };
    if size < 4096 {
        let labels = samples
            .iter()
            .filter(|&&(_, sample)| sample == value)
            .map(|&(i, _)| i32::from_ne_bytes(interior.labels[i * 4..][..4].try_into().unwrap()));
        let mut other = 0i128;
        for label in labels.collect::<HashSet<_>>() {
            let count = (sizes.len() / 8) as i64;
            let index = if label < 0 {
                count + i64::from(label)
            } else {
                i64::from(label)
            };
            if !(0..count).contains(&index) {
                return Err(Error::Invalid("領域ラベルが範囲外".into()));
            }
            let start = index as usize * 8;
            other += i128::from(i64::from_ne_bytes(sizes[start..][..8].try_into().unwrap()));
        }
        if other > size as i128 * 4 || (matched as f64) < other as f64 * 0.3 {
            return Ok(None);
        }
    }
    Ok(Some(color))
}

fn contains((rect, mask): MaskWindow<'_>, y: i64, x: i64) -> bool {
    offset(rect, y, x).is_some_and(|i| mask[i] != 0)
}

/// 輪郭全体と各象限の一致率から、部位の内部を採色するか判定する。
/// 輪郭窓と往復座標、象限の中心を求める部位の矩形は検証済みとする。
pub fn outline_matches(
    outline: &Outline<'_>,
    (left, top, width, height): Rect,
) -> Result<bool, Error> {
    let n = outline.coordinates.consistent.len();
    if n < 8 {
        return Ok(false);
    }
    let mid_y = f64::from(top) + f64::from(height) / 2.0;
    let mid_x = f64::from(left) + f64::from(width) / 2.0;
    let mut hits = [0; 4];
    let mut totals = [0; 4];
    let ((ox, oy, ow, _), mask) = outline.points;
    let indices = mask
        .iter()
        .enumerate()
        .filter(|&(i, &set)| set != 0 && outline.target_boundary[i] != 0);
    for ((i, _), ([sy, sx, by, bx], consistent)) in indices.zip(outline.coordinates.points()) {
        let y = f64::from(oy) + (i / ow as usize) as f64;
        let x = f64::from(ox) + (i % ow as usize) as f64;
        let quadrant = usize::from(y >= mid_y) * 2 + usize::from(x >= mid_x);
        totals[quadrant] += 1;
        if !consistent || !contains(outline.band, by, bx) {
            continue;
        }
        // 窓に含まれない往路の点は、NumPy の inside と同様に不一致。
        let Some(j) = offset(outline.source, sy, sx) else {
            continue;
        };
        if outline.wide[0][j] != 0
            && if outline.target_black[i] != 0 {
                near_black(outline.source_black, sy, sx)
            } else {
                outline.wide[1][j] != 0
            }
        {
            hits[quadrant] += 1;
        }
    }
    Ok(hits.iter().sum::<usize>() as f64 / n as f64 >= 0.5
        && hits
            .into_iter()
            .zip(totals)
            .all(|(hit, total)| total < 4 || hit as f64 / total as f64 >= 0.35))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn axes(points: &[(i64, i64)]) -> [Vec<u8>; 4] {
        std::array::from_fn(|axis| {
            points
                .iter()
                .flat_map(|&(y, x)| if axis % 2 == 0 { y } else { x }.to_ne_bytes())
                .collect()
        })
    }

    #[test]
    fn checks_edge_points_confirmation_quadrants_and_label_area() {
        let rect = (0, 0, 4, 4);
        let local = ((1, 1, 2, 2), &[1; 4][..]);
        let perimeter = [1, 1, 1, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 1, 1, 1];
        let outline_points: Vec<_> = (0..16)
            .filter(|&i| perimeter[i] != 0)
            .map(|i| ((i / 4) as i64, (i % 4) as i64))
            .collect();
        let outline_axes = axes(&outline_points);
        let inside_axes = axes(&[(1, 1), (1, 2), (2, 1), (2, 2)]);
        let mut black = [0; 16];
        black[5] = 1;
        let mut target_black = [0; 16];
        target_black[0] = 1;
        let mut primary = [1; 16];
        primary[0] = 0;
        let wide = [1; 16];
        let mut outline = Outline {
            points: (rect, &perimeter),
            coordinates: Coordinates {
                axes: outline_axes.each_ref().map(Vec::as_slice),
                consistent: &[1; 12],
            },
            band: (rect, &perimeter),
            target_boundary: &perimeter,
            target_black: &target_black,
            source: rect,
            wide: [&wide, &primary],
            source_black: (rect, &black),
        };
        let values = 0xffc0b080_i64.to_ne_bytes().repeat(16);
        let mut inside = Interior {
            local,
            coordinates: Coordinates {
                axes: inside_axes.each_ref().map(Vec::as_slice),
                consistent: &[1; 4],
            },
            source: rect,
            excluded: [&[0; 16]; 5],
            confirmed: None,
            values: &values,
            labels: &[0; 64],
        };
        let rgb = Some([128, 176, 192]);
        assert!(near_black(outline.source_black, 0, 0));
        assert!(outline_matches(&outline, local.0).unwrap());
        assert_eq!(interior_color(&inside, &12i64.to_ne_bytes()).unwrap(), rgb);
        assert_eq!(interior_color(&inside, &14i64.to_ne_bytes()).unwrap(), None);
        assert_eq!(interior_color(&inside, &17i64.to_ne_bytes()).unwrap(), None);
        inside.confirmed = Some(&[0; 16]);
        assert_eq!(interior_color(&inside, &4i64.to_ne_bytes()).unwrap(), None);
        inside.confirmed = Some(&[1; 16]);

        // 往路で範囲外になる2点は、端の色として数えない。
        let out_y = [i64::MIN, -1, 2, 2].map(i64::to_ne_bytes).concat();
        inside.coordinates.axes[0] = &out_y;
        assert_eq!(interior_color(&inside, &4i64.to_ne_bytes()).unwrap(), None);

        // 上側の3点が外れる。通常の四象限では3点なので採用される。
        outline.coordinates.consistent = &[0, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1];
        assert!(outline_matches(&outline, local.0).unwrap());
        // 半画素の中点で4点が同じ象限に入ると、1/4 の一致では落ちる。
        assert!(!outline_matches(&outline, (0, 0, 5, 3)).unwrap());
    }

    #[test]
    fn majority_breaks_ties_by_the_smaller_signed_value() {
        assert_eq!(
            most_frequent(&HashMap::from([(30, 2), (10, 2)])),
            Some((10, 2))
        );
    }
}
