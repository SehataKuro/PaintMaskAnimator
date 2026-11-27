//! 見本チェックで使う色と連結領域の集計。
use crate::Error;
use std::collections::HashMap;

/// 外接矩形 `(x0, y0, x1, y1)`。`x1` と `y1` は範囲に含まない。
pub type Bounds = (usize, usize, usize, usize);

/// 色または連結領域の画素数と外接矩形。
#[derive(Clone, Copy)]
pub struct RegionStats {
    /// 色または領域に含まれる画素数。
    pub size: usize,
    /// 画素を囲む外接矩形。
    pub bounds: Bounds,
}

impl RegionStats {
    fn add_run(&mut self, y: usize, x0: usize, x1: usize) {
        let (left, top, right, bottom) = self.bounds;
        self.size += x1 - x0;
        self.bounds = (left.min(x0), top.min(y), right.max(x1), bottom.max(y + 1));
    }
}

/// `reference_check.color_counts_numpy` と同じように透明と白を除き、色ごとの画素数と外接矩形を返す。
/// 入力は行優先の RGBA8888。色はリトルエンディアンの u32 で、`np.unique` と同じ昇順。
pub fn color_stats(rgba: &[u8], width: usize) -> Vec<(u32, RegionStats)> {
    let mut stats = HashMap::new();
    if width == 0 {
        return Vec::new();
    }
    for (y, row) in rgba.chunks_exact(width * 4).enumerate() {
        let mut x0 = 0;
        for run in row.as_chunks::<4>().0.chunk_by(|a, b| a == b) {
            let x1 = x0 + run.len();
            let pixel = run[0];
            if pixel[3] != 0 && pixel[..3] != [255; 3] {
                stats
                    .entry(u32::from_le_bytes(pixel))
                    .or_insert(RegionStats {
                        size: 0,
                        bounds: (x0, y, x1, y + 1),
                    })
                    .add_run(y, x0, x1);
            }
            x0 = x1;
        }
    }
    let mut sorted: Vec<_> = stats.into_iter().collect();
    sorted.sort_unstable_by_key(|&(value, _)| value);
    sorted
}

/// `fill_tracking.line_art` と同じように、透明でなく RGB がすべて `line_max` 以下の画素を 1 にする。
/// 入力は行優先の RGBA8888。出力は行優先で 1 画素 1 バイトのマスク。
pub fn line_art(rgba: &[u8], line_max: u8) -> Vec<u8> {
    rgba.as_chunks::<4>()
        .0
        .iter()
        .map(|&[r, g, b, a]| {
            u8::from((r <= line_max) & (g <= line_max) & (b <= line_max) & (a != 0))
        })
        .collect()
}

/// `reference_check.CellRegions._summarize_numpy` と同じ領域ごとの画素数と外接矩形をラベル順で返す。
/// 各ランは `(y, x0, x1, label)` で、`x1` は排他。座標やラベルが範囲外、ランが空、
/// または画素数がオーバーフローする場合は `None` を返す。
pub fn summarize_runs(
    count: usize,
    width: usize,
    height: usize,
    runs: impl IntoIterator<Item = (usize, usize, usize, usize)>,
) -> Option<Vec<RegionStats>> {
    let mut stats = vec![
        RegionStats {
            size: 0,
            bounds: (width, height, 0, 0)
        };
        count
    ];
    for (y, x0, x1, label) in runs {
        if y >= height || x0 >= x1 || x1 > width {
            return None;
        }
        let stats = stats.get_mut(label)?;
        stats.size.checked_add(x1 - x0)?;
        stats.add_run(y, x0, x1);
    }
    Some(stats)
}

/// 外接矩形が画像の端に接していれば真を返す。`reference_check.CellRegions._summarize_numpy` の
/// `on_edge` にその領域のラベルが含まれるかどうかと同じ。
pub fn on_edge(bounds: Bounds, width: usize, height: usize) -> bool {
    let (x0, y0, x1, y1) = bounds;
    x0 == 0 || y0 == 0 || x1 == width || y1 == height
}

/// `reference_check.CellRegions.region_under` と同じ、マスク内で主線を除いて最も多いラベルとその割合を返す。
/// 同数なら `np.unique` の昇順で最初に現れるラベル（`np.argmax` の結果）を選ぶ。
/// 対象画素がなければ `(None, 0.0)` を返す。
///
/// `labels` はネイティブエンディアンの i32 のバイト列、`lines` と `mask` は 1 画素 1 バイト。
/// すべて行優先で、`mask` は画像内の `bounds` の範囲だけを含む。
///
/// # エラー
///
/// バッファの長さが画像や外接矩形の寸法と合わない場合、または外接矩形が不正なら `Error::Invalid` を返す。
pub fn region_under(
    labels: &[u8],
    lines: &[u8],
    image_width: usize,
    mask: &[u8],
    bounds: Bounds,
) -> Result<(Option<i32>, f64), Error> {
    let (x0, y0, x1, y1) = bounds;
    if !lines.len().is_multiple_of(image_width)
        || lines.len().checked_mul(4) != Some(labels.len())
        || x0 > x1
        || y0 > y1
        || x1 > image_width
        || lines
            .len()
            .checked_div(image_width)
            .is_some_and(|height| y1 > height)
        || (x1 - x0).checked_mul(y1 - y0) != Some(mask.len())
    {
        return Err(Error::Invalid("invalid reference check buffers".into()));
    }
    if x0 == x1 {
        return Ok((None, 0.0));
    }
    let (labels, _) = labels.as_chunks::<4>();
    let mut counts = HashMap::<i32, usize>::new();
    let mut total = 0;
    for (y, row) in (y0..y1).zip(mask.chunks_exact(x1 - x0)) {
        let start = y * image_width + x0;
        for (x, &included) in row.iter().enumerate() {
            if included != 0 && lines[start + x] == 0 {
                let label = i32::from_ne_bytes(labels[start + x]);
                *counts.entry(label).or_default() += 1;
                total += 1;
            }
        }
    }
    use std::cmp::Reverse;
    Ok(counts
        .into_iter()
        .max_by_key(|&(label, count)| (count, Reverse(label)))
        .map_or((None, 0.0), |(label, count)| {
            (Some(label), count as f64 / total as f64)
        }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn line_mask_includes_nonzero_alpha_and_threshold_boundary() {
        assert_eq!(
            line_art(
                &[48, 0, 48, 1, 0, 0, 0, 0, 49, 0, 0, 255, 0, 0, 49, 255],
                48
            ),
            [1, 0, 0, 0]
        );
    }

    #[test]
    fn votes_use_smallest_label_on_ties_and_exclude_lines() {
        let labels: Vec<_> = [7i32, 4, 3, 3]
            .into_iter()
            .flat_map(i32::to_ne_bytes)
            .collect();
        let lines = [0, 0, 1, 1];
        assert_eq!(
            region_under(&labels, &lines, 4, &[1, 1, 1, 1], (0, 0, 4, 1)).unwrap(),
            (Some(4), 0.5)
        );
        assert_eq!(
            region_under(&labels, &lines, 4, &[1], (0, 0, 1, 1)).unwrap(),
            (Some(7), 1.0)
        );
        assert_eq!(
            region_under(&labels, &lines, 4, &[0], (0, 0, 1, 1)).unwrap(),
            (None, 0.0)
        );
        assert_eq!(
            region_under(&labels, &lines, 4, &[], (0, 0, 0, 0)).unwrap(),
            (None, 0.0)
        );
    }

    #[test]
    fn region_under_rejects_short_labels() {
        assert!(matches!(
            region_under(&[0; 3], &[0], 1, &[1], (0, 0, 1, 1)),
            Err(Error::Invalid(_))
        ));
    }

    #[test]
    fn runs_capture_thin_canvas_and_edge_connections() {
        let stats = summarize_runs(2, 1, 3, [(0, 0, 1, 0), (1, 0, 1, 0), (2, 0, 1, 1)]).unwrap();
        assert_eq!((stats[0].size, stats[0].bounds), (2, (0, 0, 1, 2)));
        assert!(on_edge(stats[0].bounds, 1, 3));
        assert!(on_edge((0, 0, 1, 1), 1, 1));
        assert!(!on_edge((1, 1, 2, 2), 3, 3));
    }
}
