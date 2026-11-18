//! バケツ塗りの内側の穴と色置換の島。
use super::region::bounds;
use super::session::Bounds;
use crate::regions::{dilate, flood_fill};
use crate::Error;

/// Python の `bucket_fill.ISLAND_MIN_SHARE`。
pub(crate) const ISLAND_MIN_SHARE: f64 = 0.6;

/// `bucket_fill.BucketOptions.island_replacements` の元色と置換色。
pub type Replacement = ([u8; 3], [u8; 3]);

/// `bucket_fill.enclosed_islands_numpy` の色と、島の外接矩形および矩形内マスク。
#[derive(Clone, Debug, PartialEq)]
pub struct Island {
    pub color: [u8; 3],
    pub bounds: Bounds,
    pub mask: Vec<u8>,
}

/// `bucket_fill._enclosed_by_numpy` と同じ、端から4近傍でたどれない穴を返す。
pub fn enclosed_by(region: &[u8], w: usize, h: usize) -> Result<Vec<u8>, Error> {
    if w.checked_mul(h) != Some(region.len()) {
        return Err(Error::Invalid("invalid region dimensions".into()));
    }
    if region.is_empty() {
        return Ok(Vec::new());
    }
    let mut inverse: Vec<u8> = region.iter().map(|&v| u8::from(v == 0)).collect();
    let mut starts = Vec::new();
    let mut add = |x, y| {
        if inverse[y * w + x] != 0 {
            starts.push((x as i64, y as i64));
        }
    };
    for x in 0..w {
        add(x, 0);
        add(x, h - 1);
    }
    for y in 1..h - 1 {
        add(0, y);
        add(w - 1, y);
    }
    if !starts.is_empty() {
        let outside = flood_fill(&inverse, w, h, &starts);
        for (value, visited) in inverse.iter_mut().zip(outside) {
            *value &= 1 ^ visited;
        }
    }
    Ok(inverse)
}

/// `bucket_fill.enclosed_islands_numpy` と同じ島を置換表の順で返す。
/// 領域は `region_bounds` 内の行優先マスクで、その矩形は領域の外接矩形とする。
pub fn enclosed_islands(
    rgba: &[u8],
    (w, h): (usize, usize),
    region: &[u8],
    region_bounds: Bounds,
    replacements: &[Replacement],
    selection: Option<&[u8]>,
    min_share: f64,
) -> Result<Vec<Island>, Error> {
    let (rx0, ry0, rx1, ry1) = region_bounds;
    let area = w.checked_mul(h);
    if area.and_then(|n| n.checked_mul(4)) != Some(rgba.len())
        || selection.is_some_and(|s| Some(s.len()) != area)
        || rx0 > rx1
        || ry0 > ry1
        || rx1 > w
        || ry1 > h
        || (rx1 - rx0).checked_mul(ry1 - ry0) != Some(region.len())
    {
        return Err(Error::Invalid("invalid island dimensions".into()));
    }
    if region.is_empty() || !replacements.iter().any(|(src, dst)| src != dst) {
        return Ok(Vec::new());
    }
    let (x0, y0) = (rx0.saturating_sub(1), ry0.saturating_sub(1));
    let (x1, y1) = ((rx1 + 1).min(w), (ry1 + 1).min(h));
    let (lw, lh) = (x1 - x0, y1 - y0);
    let mut local = vec![0; lw * lh];
    for y in ry0..ry1 {
        let start = (y - y0) * lw + rx0 - x0;
        let src = (y - ry0) * (rx1 - rx0);
        local[start..start + rx1 - rx0].copy_from_slice(&region[src..src + rx1 - rx0]);
    }
    let mut near = dilate(&local, lw, lh, 1);
    for (y, row) in near.chunks_exact_mut(lw).enumerate() {
        for (x, value) in row.iter_mut().enumerate() {
            let i = (y + y0) * w + x + x0;
            *value &= u8::from(local[y * lw + x] == 0 && selection.is_none_or(|s| s[i] != 0));
        }
    }
    let mut islands = Vec::new();
    let mut part = Vec::new();
    let mut border = Vec::new();
    for &(source, destination) in replacements {
        if source == destination {
            continue;
        }
        // 色マスクを作り、候補を種に各4連結成分を一度だけたどる。
        // 0: 異色、1: 未訪問、2: 現在の成分、4: 訪問済み。境界の画素にはビット 3 を付ける。
        let mut color = Vec::with_capacity(local.len());
        for y in y0..y1 {
            color.extend(
                rgba[(y * w + x0) * 4..(y * w + x1) * 4]
                    .chunks_exact(4)
                    .map(|p| u8::from((p[3] != 0) & (p[..3] == source))),
            );
        }
        let mut chosen = vec![0; local.len()];
        for (seed, &candidate) in near.iter().enumerate() {
            if candidate == 0 || color[seed] != 1 {
                continue;
            }
            part.push(seed);
            color[seed] = 2;
            let mut cursor = 0;
            while cursor < part.len() {
                let i = part[cursor];
                cursor += 1;
                let (x, y) = (i % lw, i / lw);
                for j in [
                    (x > 0).then(|| i - 1),
                    (x + 1 < lw).then(|| i + 1),
                    (y > 0).then(|| i - lw),
                    (y + 1 < lh).then(|| i + lw),
                ]
                .into_iter()
                .flatten()
                {
                    if color[j] == 1 {
                        color[j] = 2;
                        part.push(j);
                    }
                }
            }
            // 斜めに接する同色の別成分も、現在の成分の境界には含まれる。
            let mut inside = 0;
            for &i in &part {
                let (x, y) = (i % lw, i / lw);
                for ny in y.saturating_sub(1)..=(y + 1).min(lh - 1) {
                    for nx in x.saturating_sub(1)..=(x + 1).min(lw - 1) {
                        let j = ny * lw + nx;
                        if color[j] < 8 && color[j] != 2 {
                            border.push((j, color[j]));
                            color[j] |= 8;
                            inside += usize::from(local[j] != 0);
                        }
                    }
                }
            }
            if !border.is_empty() && inside as f64 / border.len() as f64 >= min_share {
                for &i in &part {
                    let global = (i / lw + y0) * w + i % lw + x0;
                    chosen[i] |= u8::from(selection.is_none_or(|s| s[global] != 0));
                }
            }
            for (j, value) in border.drain(..) {
                color[j] = value;
            }
            for i in part.drain(..) {
                color[i] = 4;
            }
        }
        if let Some((ax, ay, bx, by)) = bounds(&chosen, lw, lh) {
            let mut mask = Vec::with_capacity((bx - ax) * (by - ay));
            for y in ay..by {
                mask.extend_from_slice(&chosen[y * lw + ax..y * lw + bx]);
            }
            islands.push(Island {
                color: destination,
                bounds: (x0 + ax, y0 + ay, x0 + bx, y0 + by),
                mask,
            });
        }
    }
    Ok(islands)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn holes_use_four_neighbors() {
        let region = [0, 1, 1, 1, 0, 1, 1, 1, 1];
        assert_eq!(
            enclosed_by(&region, 3, 3).unwrap(),
            [0, 0, 0, 0, 1, 0, 0, 0, 0]
        );
        assert!(enclosed_by(&region, 1, 3).is_err());
    }

    #[test]
    fn islands_include_exact_border_share_in_replacement_order() {
        let source = [200, 20, 20];
        let rgba = [[255, 255, 255, 255], [200, 20, 20, 1], [0, 0, 0, 0]].concat();
        let green = [20, 200, 20];
        let blue = [20, 20, 200];
        let replacements = [(source, green), (source, source), (source, blue)];
        let run = |share, selection| {
            enclosed_islands(
                &rgba,
                (3, 1),
                &[1, 0, 1],
                (0, 0, 3, 1),
                &replacements,
                selection,
                share,
            )
            .unwrap()
        };
        let islands = run(1.0, None);
        assert_eq!(
            islands.iter().map(|i| i.color).collect::<Vec<_>>(),
            [green, blue]
        );
        assert_eq!(islands[0].bounds, (1, 0, 2, 1));
        assert_eq!(islands[0].mask, [1]);
        assert!(run(1.0001, None).is_empty());
        assert!(run(0.6, Some(&[1, 0, 1])).is_empty());
    }
}
