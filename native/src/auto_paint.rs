//! Automatic painting kernels. No image masks or decision thresholds are changed.
use pma_core::auto_paint::{connected, label_runs_of, refine_face_of, Guide, Sample};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// `native.py::sample_region` が保持する不変の見本の Python 用入口。
#[pyclass(frozen)]
pub struct GuideSampler {
    guide: Guide,
}

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
            for label in labels.chunks_exact(4) {
                let label = u32::from_ne_bytes(label.try_into().unwrap()) as usize;
                if label >= sizes.len() {
                    return Err(PyValueError::new_err("invalid guide label"));
                }
            }
            Ok(Self {
                guide: Guide::build(values, labels, flags, sizes, width, height),
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
        Ok(py.detach(|| self.guide.sample(&runs, dy, dx, ignore, require, by_label)))
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
        let (labels, count, ys, starts, ends, run_labels) =
            label_runs_of(data, width, height, itemsize);
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
        refine_face_of(
            &values, &labels, usable, excluded, &sizes, &points, width, height, radius, thresholds,
        )
    }))
}

#[pyfunction]
#[allow(clippy::too_many_arguments, clippy::type_complexity)]
pub fn solve_local_shift(
    py: Python<'_>,
    before: &[u8],
    exact: &[u8],
    wide: &[u8],
    width: usize,
    height: usize,
    fast_size: (usize, usize),
    dys: Vec<i64>,
    dxs: Vec<i64>,
    total: f64,
    step: i64,
    motion_penalty: f64,
    near_line_weight: f64,
    share_motion: bool,
) -> PyResult<((i64, i64), f64, f64)> {
    py.detach(|| {
        pma_core::local_shift::solve_local_shift(
            before,
            exact,
            wide,
            width,
            height,
            fast_size,
            &dys,
            &dxs,
            total,
            step,
            motion_penalty,
            near_line_weight,
            share_motion,
        )
    })
    .map_err(|error| PyValueError::new_err(error.to_string()))
}
