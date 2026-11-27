//! Automatic painting kernels. No image masks or decision thresholds are changed.
//! 自動塗りの見本集計・ランの領域結合・顔色の絞り込みを行う純粋な計算。
use std::collections::{HashMap, HashSet};

/// 見本の1行内で値・領域番号・フラグが等しい半開区間。
/// `Guide::build` が画素列から作り、`native.py::sample_region` の集計に使う。
#[derive(Clone, Copy, PartialEq)]
struct Span {
    start: usize,
    end: usize,
    value: u64,
    label: usize,
    flags: u8,
}

/// 行ごとの同値区間と領域面積を保持する見本。
/// `native.py::sample_region` 経由で `auto_bucket.py::BucketWork._sample` が使う。
pub struct Guide {
    width: usize,
    height: usize,
    rows: Vec<usize>,
    spans: Vec<Span>,
    sizes: Vec<u64>,
}

impl Guide {
    /// 行優先の画素列から圧縮した見本を返す。
    /// values はネイティブエンディアン u64、labels は同 u32、flags は各画素1バイト。
    /// 各バッファは width * height 画素分で、すべての label は sizes.len() 未満とする。
    /// 寸法と積は入口で検査済み。`native.py::sample_region` の GuideSampler 構築から使う。
    pub fn build(
        values: &[u8],
        labels: &[u8],
        flags: &[u8],
        sizes: Vec<u64>,
        width: usize,
        height: usize,
    ) -> Self {
        let mut spans: Vec<Span> = Vec::new();
        let mut rows = Vec::with_capacity(height + 1);
        for y in 0..height {
            rows.push(spans.len());
            for x in 0..width {
                let at = y * width + x;
                let value = u64::from_ne_bytes(values[at * 8..at * 8 + 8].try_into().unwrap());
                let label =
                    u32::from_ne_bytes(labels[at * 4..at * 4 + 4].try_into().unwrap()) as usize;
                if x > 0 {
                    let last = spans.last_mut().unwrap();
                    if (last.value, last.label, last.flags) == (value, label, flags[at]) {
                        last.end = x + 1;
                        continue;
                    }
                }
                spans.push(Span {
                    start: x,
                    end: x + 1,
                    value,
                    label,
                    flags: flags[at],
                });
            }
        }
        rows.push(spans.len());
        Self {
            width,
            height,
            rows,
            spans,
            sizes,
        }
    }

    /// ずらしたランと見本の重なり、最多値、面積、未知フラグ、中央の seed を返す。
    /// runs は (行, 開始, 終端) で開始 < 終端、dy/dx 加算でオーバーフローしない前提。
    /// `native.py::sample_region`、`auto_bucket.py::BucketWork._sample` から使う。
    pub fn sample(
        &self,
        runs: &[(i64, i64, i64)],
        dy: i64,
        dx: i64,
        ignore: u8,
        require: u8,
        by_label: bool,
    ) -> Sample {
        let (mut inside, mut total, mut unknown_any) = (0usize, 0usize, false);
        let mut counts: HashMap<u64, usize> = HashMap::new();
        let mut pieces = Vec::new();
        for &(y, x, end) in runs {
            let y = y + dy;
            if y < 0 || y >= self.height as i64 {
                continue;
            }
            let start = (x + dx).max(0);
            let end = (end + dx).min(self.width as i64);
            if start >= end {
                continue;
            }
            inside += (end - start) as usize;
            let row = &self.spans[self.rows[y as usize]..self.rows[y as usize + 1]];
            let first = row.partition_point(|span| span.end <= start as usize);
            for span in &row[first..] {
                if span.start >= end as usize {
                    break;
                }
                if span.flags & ignore != 0 || span.flags & require != require {
                    continue;
                }
                let left = span.start.max(start as usize);
                let right = span.end.min(end as usize);
                let length = right - left;
                let key = if by_label {
                    span.label as u64
                } else {
                    span.value
                };
                *counts.entry(key).or_default() += length;
                total += length;
                unknown_any |= span.flags & 4 != 0;
                pieces.push((key, span.label, span.flags, y as usize, left, length));
            }
        }
        let Some((&key, &count)) = counts
            .iter()
            .max_by(|(a, ac), (b, bc)| ac.cmp(bc).then_with(|| b.cmp(a)))
        else {
            return (inside, total, None, 0, 0, false, unknown_any, 0, 0);
        };
        let mut touched = HashSet::new();
        let (mut unknown, mut offset, mut seed) = (false, count / 2, None);
        for (value, label, flags, y, x, length) in pieces {
            if value != key {
                continue;
            }
            touched.insert(label);
            unknown |= flags & 4 != 0;
            if seed.is_none() {
                if offset < length {
                    seed = Some((y, x + offset));
                } else {
                    offset -= length;
                }
            }
        }
        let other = touched.iter().map(|label| self.sizes[*label]).sum();
        let (y, x) = seed.unwrap_or((0, 0));
        (
            inside,
            total,
            Some(key),
            count,
            other,
            unknown,
            unknown_any,
            y,
            x,
        )
    }
}

/// 内側数、有効数、最多キーと数、触れた領域面積、未知フラグ2種、seed の行・列。
/// `Guide::sample` が返し、`native.py::sample_region` で Python に渡す。
pub type Sample = (
    usize,
    usize,
    Option<u64>,
    usize,
    u64,
    bool,
    bool,
    usize,
    usize,
);

/// parent 内の有効な番号から根を返し、経路を短縮する。
/// `reference_check.py::_label_runs` からの結合処理で使う。
fn root(parent: &mut [usize], mut index: usize) -> usize {
    while parent[index] != index {
        parent[index] = parent[parent[index]];
        index = parent[index];
    }
    index
}

/// 同種で上下に重なるランの連結番号と領域数を返す。
/// 各配列は同数の有効な半開区間、rows は0からラン数までの単調な行境界とする。
/// `native.py::connect_runs` と label_runs、`reference_check.py::_label_runs` から使う。
pub fn connected(starts: &[u64], ends: &[u64], kinds: &[u64], rows: &[u64]) -> (Vec<u32>, usize) {
    let count = starts.len();
    let mut parent: Vec<usize> = (0..count).collect();
    for row in rows.windows(3) {
        let (mut upper, upper_end) = (row[0] as usize, row[1] as usize);
        let (mut lower, lower_end) = (row[1] as usize, row[2] as usize);
        while upper < upper_end && lower < lower_end {
            if kinds[upper] == kinds[lower]
                && starts[upper] < ends[lower]
                && starts[lower] < ends[upper]
            {
                let a = root(&mut parent, upper);
                let b = root(&mut parent, lower);
                parent[a.max(b)] = a.min(b);
            }
            if ends[upper] < ends[lower] {
                upper += 1;
            } else {
                lower += 1;
            }
        }
    }
    let roots: Vec<usize> = (0..count).map(|i| root(&mut parent, i)).collect();
    let mut compact = vec![u32::MAX; count];
    let mut regions = 0;
    for i in 0..count {
        if roots[i] == i {
            compact[i] = regions;
            regions += 1;
        }
    }
    (
        roots.iter().map(|r| compact[*r]).collect(),
        regions as usize,
    )
}

/// 同値の4近傍領域の番号画像、個数、ランの行・開始・終端・番号を返す。
/// data は行優先で width * height * itemsize バイト、itemsize は 1/2/4/8、
/// 多バイト値はネイティブエンディアン。寸法と積は入口で検査済みとする。
/// `native.py::label_runs`、`reference_check.py::_label_runs`、
/// `bucket_fill.py::_RegionIndex.__init__` から使い、_label_runs の走査・結合と一致する。
#[allow(clippy::type_complexity)]
pub fn label_runs_of(
    data: &[u8],
    width: usize,
    height: usize,
    itemsize: usize,
) -> (Vec<u32>, usize, Vec<u64>, Vec<u64>, Vec<u64>, Vec<u32>) {
    let pixels = width * height;
    let mut starts = Vec::new();
    let mut ends = Vec::new();
    let mut kinds = Vec::new();
    let mut ys: Vec<u64> = Vec::new();
    let mut rows = Vec::with_capacity(height + 1);
    for y in 0..height {
        rows.push(starts.len() as u64);
        let mut previous = None;
        for x in 0..width {
            let at = (y * width + x) * itemsize;
            let value = match itemsize {
                1 => data[at] as u64,
                2 => u16::from_ne_bytes(data[at..at + 2].try_into().unwrap()) as u64,
                4 => u32::from_ne_bytes(data[at..at + 4].try_into().unwrap()) as u64,
                _ => u64::from_ne_bytes(data[at..at + 8].try_into().unwrap()),
            };
            if previous == Some(value) {
                continue;
            }
            if x > 0 {
                ends.push(x as u64);
            }
            ys.push(y as u64);
            starts.push(x as u64);
            kinds.push(value);
            previous = Some(value);
        }
        if width > 0 {
            ends.push(width as u64);
        }
    }
    rows.push(starts.len() as u64);
    let (run_labels, count) = connected(&starts, &ends, &kinds, &rows);
    let mut labels = vec![0u32; pixels];
    for i in 0..starts.len() {
        let base = ys[i] as usize * width;
        labels[base + starts[i] as usize..base + ends[i] as usize].fill(run_labels[i]);
    }
    (labels, count, ys, starts, ends, run_labels)
}

/// 各ずれで面積比などを満たす最多色と、その色の最大適合度を出現順で返す。
/// values/labels/usable/excluded は行優先の width * height 要素、label は sizes 内。
/// radius は 0..=32、points の座標に半径を加減算でき、閾値は正の有限値とする。
/// 寸法と積は入口で検査済み。`native.py::refine_face` 経由で
/// `auto_bucket.py::BucketWork._refine_face_color` の同じずれ探索・採色から使う。
#[allow(clippy::too_many_arguments)]
pub fn refine_face_of(
    values: &[u64],
    labels: &[usize],
    usable: &[u8],
    excluded: &[u8],
    sizes: &[u64],
    points: &[(i64, i64)],
    width: usize,
    height: usize,
    radius: i64,
    thresholds: (f64, f64, f64, f64),
) -> Vec<(u64, f64)> {
    let size = points.len() as f64;
    let mut answers: Vec<(u64, f64)> = Vec::new();
    let mut sample = Vec::with_capacity(points.len());
    let mut counts = HashMap::new();
    let mut touched = HashSet::new();
    for dy in -radius..=radius {
        for dx in -radius..=radius {
            sample.clear();
            counts.clear();
            for &(y, x) in points {
                let (y, x) = (y + dy, x + dx);
                if y < 0 || x < 0 || y >= height as i64 || x >= width as i64 {
                    continue;
                }
                let at = y as usize * width + x as usize;
                if usable[at] == 0 {
                    continue;
                }
                sample.push(at);
                *counts.entry(values[at]).or_insert(0usize) += 1;
            }
            if (sample.len() as f64) < size * thresholds.0 {
                continue;
            }
            let Some((&value, &count)) = counts
                .iter()
                .max_by(|(a, ac), (b, bc)| ac.cmp(bc).then_with(|| b.cmp(a)))
            else {
                continue;
            };
            if (count as f64) / (sample.len() as f64) < thresholds.1 {
                continue;
            }
            touched.clear();
            let mut unknown = false;
            for &at in &sample {
                if values[at] == value {
                    unknown |= excluded[at] != 0;
                    touched.insert(labels[at]);
                }
            }
            if unknown {
                continue;
            }
            let other = touched
                .iter()
                .map(|label| sizes[*label] as f64)
                .sum::<f64>();
            if other < size / thresholds.2
                || other > size * thresholds.2
                || (count as f64) < other * thresholds.3
            {
                continue;
            }
            let fit = 2.0 * count as f64 / (size + other);
            if let Some(answer) = answers.iter_mut().find(|v| v.0 == value) {
                answer.1 = answer.1.max(fit);
            } else {
                answers.push((value, fit));
            }
        }
    }
    answers
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn labels_two_rows_in_first_root_order() {
        // 上段 11|2、下段 1|22。同値で上下に重なる2組が各々つながる。
        let result = label_runs_of(&[1, 1, 2, 1, 2, 2], 3, 2, 1);
        assert_eq!(
            result,
            (
                vec![0, 0, 1, 0, 1, 1],
                2,
                vec![0, 0, 1, 1],
                vec![0, 2, 0, 1],
                vec![2, 3, 1, 3],
                vec![0, 1, 0, 1]
            )
        );
    }

    #[test]
    fn connects_overlap_but_not_touching_endpoints() {
        // [0,2) は下段 [1,2) とだけ重なる。[2,3) は端が接するだけ。
        assert_eq!(
            connected(&[0, 2, 1], &[2, 3, 2], &[7, 7, 7], &[0, 2, 3]),
            (vec![0, 1, 0], 2)
        );
    }

    #[test]
    fn guide_majority_seed_flags_and_label_sampling() {
        let values: Vec<u8> = [9u64, 9, 3, 9]
            .iter()
            .flat_map(|v| v.to_ne_bytes())
            .collect();
        let labels: Vec<u8> = [0u32, 0, 1, 2]
            .iter()
            .flat_map(|v| v.to_ne_bytes())
            .collect();
        let guide = Guide::build(&values, &labels, &[0, 0, 4, 0], vec![2, 1, 1], 4, 1);
        // 9 が3画素。offset=3/2=1 は最初の区間の x=1、面積は領域0+2で3。
        assert_eq!(
            guide.sample(&[(0, 0, 4)], 0, 0, 0, 0, false),
            (4, 4, Some(9), 3, 3, false, true, 0, 1)
        );
        assert_eq!(
            guide.sample(&[(0, 0, 4)], 0, 0, 4, 0, true),
            (4, 3, Some(0), 2, 2, false, false, 0, 1)
        );
        assert_eq!(
            guide.sample(&[(0, 0, 4)], 1, 0, 0, 0, false),
            (0, 0, None, 0, 0, false, false, 0, 0)
        );
        // 9 と3が同数なら小さいキー3。unknown と unknown_any はともに真。
        assert_eq!(
            guide.sample(&[(0, 1, 3)], 0, 0, 0, 0, false),
            (2, 2, Some(3), 1, 1, true, true, 0, 2)
        );
    }

    #[test]
    fn refines_one_point_and_rejects_excluded() {
        // 半径0で1点のみ。count=size=other=1 なので fit=2/(1+1)=1。
        assert_eq!(
            refine_face_of(
                &[7],
                &[0],
                &[1],
                &[0],
                &[1],
                &[(0, 0)],
                1,
                1,
                0,
                (1.0, 1.0, 1.0, 1.0)
            ),
            vec![(7, 1.0)]
        );
        assert!(refine_face_of(
            &[7],
            &[0],
            &[1],
            &[1],
            &[1],
            &[(0, 0)],
            1,
            1,
            0,
            (1.0, 1.0, 1.0, 1.0)
        )
        .is_empty());
    }
}
