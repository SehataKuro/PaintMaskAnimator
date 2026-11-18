use pma_core::tone_curve as core;
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};

#[pyfunction]
pub fn tone_curve_samples<'py>(
    py: Python<'py>,
    points: Vec<(f64, f64)>,
    values: &[u8],
) -> PyResult<Option<Bound<'py, PyBytes>>> {
    let (values, tail) = values.as_chunks::<8>();
    if !tail.is_empty() {
        return Err(PyValueError::new_err("expected packed float64 samples"));
    }
    let output = py.detach(|| {
        let values: Vec<_> = values.iter().copied().map(f64::from_ne_bytes).collect();
        // NumPy 側で浮動小数点の警告・例外まで再現する必要がある入力。
        if values.iter().any(|&value| {
            let signaling_nan = value.is_nan() && value.to_bits() & (1 << 51) == 0;
            signaling_nan || (value.is_finite() && value.abs() > 1e300)
        }) {
            return None;
        }
        Some(
            core::tone_curve_samples(points, &values)
                .into_iter()
                .flat_map(f64::to_ne_bytes)
                .collect::<Vec<_>>(),
        )
    });
    Ok(output.map(|bytes| PyBytes::new(py, &bytes)))
}

#[pyfunction]
pub fn tone_curve_lut(py: Python<'_>, points: Vec<(f64, f64)>) -> Bound<'_, PyBytes> {
    let lut = py.detach(|| core::tone_curve_lut(points));
    PyBytes::new(py, &lut)
}

#[pyfunction(signature = (rgba, width, height, points, background=None))]
pub fn apply_tone_curve<'py>(
    py: Python<'py>,
    rgba: &[u8],
    width: usize,
    height: usize,
    points: Vec<(f64, f64)>,
    background: Option<(u8, u8, u8)>,
) -> PyResult<Bound<'py, PyBytes>> {
    PyBytes::new_with(py, rgba.len(), |output| {
        py.detach(|| {
            core::apply_tone_curve(
                rgba,
                width,
                height,
                points,
                background.map(|(r, g, b)| [r, g, b]),
                output,
            )
        })
        .map_err(|error| PyValueError::new_err(error.to_string()))
    })
}
