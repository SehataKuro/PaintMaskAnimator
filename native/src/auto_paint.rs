//! Automatic painting kernels. No image masks or decision thresholds are changed.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::collections::{HashMap, HashSet};

#[derive(Clone, Copy, PartialEq)]
struct Span {
    start: usize,
    end: usize,
    value: u64,
    label: usize,
    flags: u8,
}

/// Immutable run-compressed guide. Large flat faces are counted by interval
/// lengths instead of constructing shifted coordinate arrays for every pixel.
#[pyclass(frozen)]
pub struct GuideSampler {
    width: usize,
    height: usize,
    rows: Vec<usize>,
    spans: Vec<Span>,
    sizes: Vec<u64>,
}

type Sample = (
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

#[pymethods]
impl GuideSampler {
    #[new]
    fn new(
        py: Python<'_>,
        values: &[u8],
        labels: &[u8],
        flags: &[u8],
        sizes: &[u8],
        width: usize,
        height: usize,
    ) -> PyResult<Self> {
        let pixels = width
            .checked_mul(height)
            .ok_or_else(|| PyValueError::new_err("image too large"))?;
        if pixels.checked_mul(8) != Some(values.len())
            || pixels.checked_mul(4) != Some(labels.len())
            || pixels != flags.len()
            || width > i64::MAX as usize
            || height > i64::MAX as usize
        {
            return Err(PyValueError::new_err("invalid guide image buffers"));
        }
        let sizes = u64s(sizes)?;
        py.detach(|| {
            let mut spans: Vec<Span> = Vec::new();
            let mut rows = Vec::with_capacity(height + 1);
            for y in 0..height {
                rows.push(spans.len());
                for x in 0..width {
                    let at = y * width + x;
                    let value = u64::from_ne_bytes(values[at * 8..at * 8 + 8].try_into().unwrap());
                    let label =
                        u32::from_ne_bytes(labels[at * 4..at * 4 + 4].try_into().unwrap()) as usize;
                    if label >= sizes.len() {
                        return Err(PyValueError::new_err("invalid guide label"));
                    }
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
            Ok(Self {
                width,
                height,
                rows,
                spans,
                sizes,
            })
        })
    }

    #[allow(clippy::too_many_arguments)]
    fn sample(
        &self,
        py: Python<'_>,
        runs: &[u8],
        dy: i64,
        dx: i64,
        ignore: u8,
        require: u8,
        by_label: bool,
    ) -> PyResult<Sample> {
        if !runs.chunks_exact(24).remainder().is_empty() {
            return Err(PyValueError::new_err("invalid mask runs"));
        }
        let runs: Vec<(i64, i64, i64)> = runs
            .chunks_exact(24)
            .map(|v| {
                (
                    i64::from_ne_bytes(v[..8].try_into().unwrap()),
                    i64::from_ne_bytes(v[8..16].try_into().unwrap()),
                    i64::from_ne_bytes(v[16..].try_into().unwrap()),
                )
            })
            .collect();
        if runs.iter().any(|(y, x, end)| {
            x >= end
                || y.checked_add(dy).is_none()
                || x.checked_add(dx).is_none()
                || end.checked_add(dx).is_none()
        }) {
            return Err(PyValueError::new_err("invalid shifted mask coordinates"));
        }
        Ok(py.detach(|| {
            let (mut inside, mut total, mut unknown_any) = (0usize, 0usize, false);
            let mut counts: HashMap<u64, usize> = HashMap::new();
            let mut pieces = Vec::new();
            for (y, x, end) in runs {
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
        }))
    }
}

fn u64s(data: &[u8]) -> PyResult<Vec<u64>> {
    if data.len() & 7 != 0 {
        return Err(PyValueError::new_err("expected packed 64-bit values"));
    }
    Ok(data
        .chunks_exact(8)
        .map(|v| u64::from_ne_bytes(v.try_into().unwrap()))
        .collect())
}

fn root(parent: &mut [usize], mut index: usize) -> usize {
    while parent[index] != index {
        parent[index] = parent[parent[index]];
        index = parent[index];
    }
    index
}

fn connected(starts: &[u64], ends: &[u64], kinds: &[u64], rows: &[u64]) -> (Vec<u32>, usize) {
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

type RunBuffers<'py> = (
    Bound<'py, PyBytes>,
    usize,
    Bound<'py, PyBytes>,
    Bound<'py, PyBytes>,
    Bound<'py, PyBytes>,
    Bound<'py, PyBytes>,
);

#[pyfunction]
pub fn label_runs<'py>(
    py: Python<'py>,
    data: &[u8],
    width: usize,
    height: usize,
    itemsize: usize,
) -> PyResult<RunBuffers<'py>> {
    let pixels = width
        .checked_mul(height)
        .ok_or_else(|| PyValueError::new_err("image too large"))?;
    if ![1, 2, 4, 8].contains(&itemsize)
        || pixels.checked_mul(itemsize) != Some(data.len())
        || height == usize::MAX
        || pixels > i64::MAX as usize
    {
        return Err(PyValueError::new_err("invalid region image"));
    }
    let (labels, count, ys, starts, ends, run_labels) = py.detach(|| {
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
        let bytes64 =
            |values: Vec<u64>| -> Vec<u8> { values.iter().flat_map(|v| v.to_ne_bytes()).collect() };
        let bytes32 =
            |values: Vec<u32>| -> Vec<u8> { values.iter().flat_map(|v| v.to_ne_bytes()).collect() };
        (
            bytes32(labels),
            count,
            bytes64(ys),
            bytes64(starts),
            bytes64(ends),
            bytes32(run_labels),
        )
    });
    Ok((
        PyBytes::new(py, &labels),
        count,
        PyBytes::new(py, &ys),
        PyBytes::new(py, &starts),
        PyBytes::new(py, &ends),
        PyBytes::new(py, &run_labels),
    ))
}

#[pyfunction]
pub fn connect_runs<'py>(
    py: Python<'py>,
    starts: &[u8],
    ends: &[u8],
    kinds: &[u8],
    rows: &[u8],
) -> PyResult<(Bound<'py, PyBytes>, usize)> {
    let starts = u64s(starts)?;
    let ends = u64s(ends)?;
    let kinds = u64s(kinds)?;
    let rows = u64s(rows)?;
    let count = starts.len();
    if ends.len() != count
        || kinds.len() != count
        || rows.first() != Some(&0)
        || rows.last() != Some(&(count as u64))
        || rows.windows(2).any(|w| w[0] > w[1])
        || starts.iter().zip(&ends).any(|(s, e)| s >= e)
    {
        return Err(PyValueError::new_err("invalid scanline runs"));
    }
    let (labels, regions) = py.detach(|| {
        let (labels, regions) = connected(&starts, &ends, &kinds, &rows);
        (
            labels
                .iter()
                .flat_map(|r| r.to_ne_bytes())
                .collect::<Vec<u8>>(),
            regions,
        )
    });
    Ok((PyBytes::new(py, &labels), regions))
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn refine_face(
    py: Python<'_>,
    values: &[u8],
    labels: &[u8],
    usable: &[u8],
    excluded: &[u8],
    sizes: &[u8],
    points: &[u8],
    width: usize,
    height: usize,
    radius: i64,
    thresholds: (f64, f64, f64, f64),
) -> PyResult<Vec<(u64, f64)>> {
    let pixels = width
        .checked_mul(height)
        .ok_or_else(|| PyValueError::new_err("ROI too large"))?;
    if pixels.checked_mul(8) != Some(values.len())
        || pixels.checked_mul(4) != Some(labels.len())
        || usable.len() != pixels
        || excluded.len() != pixels
        || points.len() & 15 != 0
        || !(0..=32).contains(&radius)
        || width > i64::MAX as usize
        || height > i64::MAX as usize
        || ![thresholds.0, thresholds.1, thresholds.2, thresholds.3]
            .iter()
            .all(|v| v.is_finite() && *v > 0.0)
    {
        return Err(PyValueError::new_err("invalid face refinement inputs"));
    }
    let values = u64s(values)?;
    let sizes = u64s(sizes)?;
    let labels: Vec<usize> = labels
        .chunks_exact(4)
        .map(|v| u32::from_ne_bytes(v.try_into().unwrap()) as usize)
        .collect();
    if labels.iter().any(|v| *v >= sizes.len()) {
        return Err(PyValueError::new_err("region label outside sizes"));
    }
    let points: Vec<(i64, i64)> = points
        .chunks_exact(16)
        .map(|v| {
            (
                i64::from_ne_bytes(v[..8].try_into().unwrap()),
                i64::from_ne_bytes(v[8..].try_into().unwrap()),
            )
        })
        .collect();
    if points.iter().any(|(y, x)| {
        y.checked_add(radius).is_none()
            || y.checked_sub(radius).is_none()
            || x.checked_add(radius).is_none()
            || x.checked_sub(radius).is_none()
    }) {
        return Err(PyValueError::new_err("point coordinate overflow"));
    }
    Ok(py.detach(|| {
        let size = points.len() as f64;
        let mut answers: Vec<(u64, f64)> = Vec::new();
        let mut sample = Vec::with_capacity(points.len());
        let mut counts = HashMap::new();
        let mut touched = HashSet::new();
        for dy in -radius..=radius {
            for dx in -radius..=radius {
                sample.clear();
                counts.clear();
                for &(y, x) in &points {
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
    }))
}
