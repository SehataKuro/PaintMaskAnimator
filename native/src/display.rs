//! Display computations without Qt dependencies, reusable by a future Rust UI.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::collections::HashSet;

#[pyfunction]
pub fn resolve_exposures<'py>(
    py: Python<'py>,
    states: &[u8],
    exposures: &[u8],
    frames: usize,
    layers: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    let cells = frames
        .checked_mul(layers)
        .filter(|n| n.checked_mul(8) == Some(exposures.len()) && *n == states.len())
        .ok_or_else(|| PyValueError::new_err("invalid exposure dimensions"))?;
    let output = py.detach(|| {
        let mut result = vec![-1i64; cells];
        for layer in 0..layers {
            let (mut key, mut end) = (-1i64, 0usize);
            for frame in 0..frames {
                let offset = frame * layers + layer;
                match states[offset] {
                    1 => {
                        key = frame as i64;
                        let exposure = i64::from_ne_bytes(
                            exposures[offset * 8..offset * 8 + 8].try_into().unwrap(),
                        )
                        .max(1) as usize;
                        end = frame.saturating_add(exposure);
                    }
                    2 => {
                        key = -1;
                        end = 0;
                    }
                    _ => {}
                }
                if frame < end {
                    result[offset] = key;
                }
            }
        }
        result
            .iter()
            .flat_map(|v| v.to_ne_bytes())
            .collect::<Vec<u8>>()
    });
    Ok(PyBytes::new(py, &output))
}

#[pyfunction]
pub fn tint_onion<'py>(
    py: Python<'py>,
    rgba: &[u8],
    color: (u8, u8, u8),
    enabled: bool,
    selected: Option<Vec<(u8, u8, u8)>>,
) -> PyResult<Bound<'py, PyBytes>> {
    if !rgba.len().is_multiple_of(4) {
        return Err(PyValueError::new_err("expected packed RGBA pixels"));
    }
    let output = py.detach(|| {
        let selected: Option<HashSet<_>> = selected.map(|values| values.into_iter().collect());
        let mut result = rgba.to_vec();
        for pixel in result.chunks_exact_mut(4) {
            if selected
                .as_ref()
                .is_some_and(|set| !set.contains(&(pixel[0], pixel[1], pixel[2])))
            {
                pixel[3] = 0;
            }
            if enabled {
                // Keep NumPy float32 operation order, including truncation.
                let luminance = (pixel[0] as f32 * 0.299f32
                    + pixel[1] as f32 * 0.587f32
                    + pixel[2] as f32 * 0.114f32)
                    / 255.0f32;
                for (channel, base) in pixel[..3].iter_mut().zip([color.0, color.1, color.2]) {
                    *channel = (base as f32 * (1.0f32 - luminance) + 255.0f32 * luminance)
                        .clamp(0.0, 255.0) as u8;
                }
            }
        }
        result
    });
    Ok(PyBytes::new(py, &output))
}
