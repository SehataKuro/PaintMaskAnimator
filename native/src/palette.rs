use pma_core::palette as core;
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};

type Bytes<'py> = Bound<'py, PyBytes>;
type Rgb = (u8, u8, u8);

fn invalid(error: pma_core::Error) -> PyErr {
    PyValueError::new_err(error.to_string())
}

fn rgb((r, g, b): Rgb) -> [u8; 3] {
    [r, g, b]
}

fn floats<const N: usize>(bytes: &[u8]) -> PyResult<&[[u8; N]]> {
    let (chunks, tail) = bytes.as_chunks::<N>();
    if !tail.is_empty() {
        return Err(PyValueError::new_err("invalid packed float array"));
    }
    Ok(chunks)
}

#[pyfunction]
pub fn palette_color_counts<'py>(
    py: Python<'py>,
    sample: &[u8],
) -> PyResult<(Bytes<'py>, Bytes<'py>)> {
    let (colors, weights) = py
        .detach(|| {
            let (colors, weights) = core::palette_color_counts(sample)?;
            let weights: Vec<_> = weights
                .into_iter()
                .flat_map(|weight| (weight as f64).to_ne_bytes())
                .collect();
            Ok((colors, weights))
        })
        .map_err(invalid)?;
    Ok((PyBytes::new(py, &colors), PyBytes::new(py, &weights)))
}

#[pyfunction]
pub fn hue_cluster_palette<'py>(
    py: Python<'py>,
    colors: &[u8],
    features: &[u8],
    weights: &[u8],
    log_weights: &[u8],
    color_count: usize,
) -> PyResult<Bytes<'py>> {
    let features = floats::<16>(features)?;
    let weights = floats::<8>(weights)?;
    let log_weights = floats::<8>(log_weights)?;
    let colors = py
        .detach(|| {
            let features: Vec<_> = features
                .iter()
                .map(|packed| {
                    let (values, _) = packed.as_chunks::<4>();
                    std::array::from_fn(|i| f32::from_ne_bytes(values[i]))
                })
                .collect();
            let weights: Vec<_> = weights.iter().copied().map(f64::from_ne_bytes).collect();
            let log_weights: Vec<_> = log_weights
                .iter()
                .copied()
                .map(f64::from_ne_bytes)
                .collect();
            core::hue_cluster_palette(colors, &features, &weights, &log_weights, color_count)
        })
        .map_err(invalid)?;
    Ok(PyBytes::new(py, &colors))
}

#[pyfunction]
pub fn palette_training_samples<'py>(
    py: Python<'py>,
    rgba: &[u8],
    width: usize,
    height: usize,
    threshold: u8,
    opaque: bool,
    background: Option<Rgb>,
) -> PyResult<Bytes<'py>> {
    let data = py
        .detach(|| {
            core::palette_training_samples(
                rgba,
                width,
                height,
                threshold,
                opaque,
                background.map(rgb),
            )
        })
        .map_err(invalid)?;
    Ok(PyBytes::new(py, &data))
}

#[pyfunction]
pub fn select_palette_colors<'py>(
    py: Python<'py>,
    training: &[u8],
    priority: &[u8],
    clustered: &[u8],
    color_count: usize,
    line: bool,
    background: Option<Rgb>,
) -> PyResult<Bytes<'py>> {
    let data = py
        .detach(|| {
            core::select_palette_colors(
                training,
                priority,
                clustered,
                color_count,
                line,
                background.map(rgb),
            )
        })
        .map_err(invalid)?;
    Ok(PyBytes::new(py, &data))
}

#[pyfunction]
pub fn line_palette_labels<'py>(
    py: Python<'py>,
    rgb: &[u8],
    width: usize,
    height: usize,
    visible: &[u8],
    palette: &[u8],
) -> PyResult<Bytes<'py>> {
    let labels = py
        .detach(|| {
            core::line_palette_labels(rgb, width, height, visible, palette).map(|labels| {
                labels
                    .into_iter()
                    .flat_map(i16::to_ne_bytes)
                    .collect::<Vec<_>>()
            })
        })
        .map_err(invalid)?;
    Ok(PyBytes::new(py, &labels))
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn apply_color_reduction_palette<'py>(
    py: Python<'py>,
    rgba: &[u8],
    width: usize,
    height: usize,
    palette: &[u8],
    threshold: u8,
    opaque: bool,
    background: Option<Rgb>,
    points: Option<Vec<(f64, f64)>>,
    line: bool,
) -> PyResult<Bytes<'py>> {
    let surface = (!line).then_some(|rgb: &[u8], visible: &[u8]| {
        let threads = std::thread::available_parallelism().map_or(1, |count| count.get());
        crate::palette_labels::surface_guided_labels(
            rgb, width, height, visible, palette, true, threads,
        )
    });
    PyBytes::new_with(py, rgba.len(), |output| {
        py.detach(|| {
            core::apply_color_reduction_palette(
                rgba,
                width,
                height,
                palette,
                threshold,
                opaque,
                background.map(rgb),
                points,
                surface,
                output,
            )
        })
        .map_err(invalid)
    })
}
