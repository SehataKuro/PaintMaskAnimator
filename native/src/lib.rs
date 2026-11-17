//! PaintMaskAnimator の重い画像計算を Rust で行う部品。
//!
//! 方針（docs/design-principles.md「将来は Rust へ置き換えていく」）に沿って、
//! 画面や Qt に触れない計算と状態管理を置く。Python 側の numpy 版と
//! 同じ結果になることを、Python のテストで突き合わせる。
//!
//! マスクは 1 画素 1 バイト（0 か 1）の行優先の並びでやり取りする。
//! numpy の bool 配列をそのまま `tobytes()` した形と同じ。

use pyo3::prelude::*;
use pyo3::types::PyBytes;

mod auto_paint;
mod clip;
mod display;
mod map_colors;
mod mask_transform;
mod palette_labels;
mod project_zip;
mod timeline;

fn check_size(data: &[u8], width: usize, height: usize) -> PyResult<()> {
    if width.checked_mul(height) != Some(data.len()) {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "mask has {} bytes, expected {} x {}",
            data.len(),
            width,
            height
        )));
    }
    Ok(())
}

#[pyfunction(name = "encode_png")]
fn py_encode_png<'py>(
    py: Python<'py>,
    rgba: &[u8],
    width: usize,
    height: usize,
    dots_per_meter_x: i32,
    dots_per_meter_y: i32,
) -> PyResult<Bound<'py, PyBytes>> {
    let dimensions = (dots_per_meter_x > 0 || dots_per_meter_y > 0)
        .then_some((dots_per_meter_x as u32, dots_per_meter_y as u32));
    let data = py
        .detach(|| pma_core::project::png::encode_png(width, height, rgba, dimensions))
        .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
    Ok(PyBytes::new(py, &data))
}

#[pyfunction(name = "decode_png")]
fn py_decode_png<'py>(
    py: Python<'py>,
    data: &[u8],
    max_bytes: u64,
) -> PyResult<(usize, usize, Bound<'py, PyBytes>)> {
    let (width, height) = py
        .detach(|| pma_core::project::png::png_size(data, max_bytes))
        .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
    let pixels = PyBytes::new_with(py, width * height * 4, |buf| {
        py.detach(|| pma_core::project::png::decode_png_into(data, max_bytes, buf))
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))
    })?;
    Ok((width, height, pixels))
}

#[pyfunction(name = "remove_line_noise")]
#[allow(clippy::too_many_arguments)]
fn py_remove_line_noise<'py>(
    py: Python<'py>,
    rgba: &[u8],
    width: usize,
    height: usize,
    line_luma: i64,
    radius: usize,
    majority: i64,
    isolation: i64,
) -> PyResult<(Bound<'py, PyBytes>, usize)> {
    if width.checked_mul(height).and_then(|n| n.checked_mul(4)) != Some(rgba.len()) {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "rgba has {} bytes, expected {} x {} x 4",
            rgba.len(),
            width,
            height
        )));
    }
    let (result, changed) = py.detach(|| {
        pma_core::line_noise::remove_line_noise(
            rgba, width, height, line_luma, radius, majority, isolation,
        )
    });
    Ok((PyBytes::new(py, &result), changed))
}

#[pyfunction(name = "flood_fill")]
fn py_flood_fill<'py>(
    py: Python<'py>,
    passable: &[u8],
    width: usize,
    height: usize,
    seeds: Vec<(i64, i64)>,
) -> PyResult<Bound<'py, PyBytes>> {
    check_size(passable, width, height)?;
    let region = py.detach(|| pma_core::regions::flood_fill(passable, width, height, &seeds));
    Ok(PyBytes::new(py, &region))
}

#[pyfunction(name = "dilate")]
fn py_dilate<'py>(
    py: Python<'py>,
    mask: &[u8],
    width: usize,
    height: usize,
    iterations: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    check_size(mask, width, height)?;
    let result = py.detach(|| pma_core::regions::dilate(mask, width, height, iterations));
    Ok(PyBytes::new(py, &result))
}

#[pyfunction(name = "erode")]
fn py_erode<'py>(
    py: Python<'py>,
    mask: &[u8],
    width: usize,
    height: usize,
    iterations: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    check_size(mask, width, height)?;
    let result = py.detach(|| pma_core::regions::erode(mask, width, height, iterations));
    Ok(PyBytes::new(py, &result))
}

#[pyfunction(name = "label_regions")]
fn py_label_regions<'py>(
    py: Python<'py>,
    codes: &[u8],
    width: usize,
    height: usize,
) -> PyResult<(Bound<'py, PyBytes>, u32)> {
    check_size(codes, width * 4, height)?;
    let values: Vec<u32> = codes
        .chunks_exact(4)
        .map(|chunk| u32::from_ne_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]))
        .collect();
    let (labels, count) = py.detach(|| pma_core::regions::label_regions(&values, width, height));
    let bytes: Vec<u8> = labels
        .iter()
        .flat_map(|value| value.to_ne_bytes())
        .collect();
    Ok((PyBytes::new(py, &bytes), count))
}

#[pymodule]
fn pma_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(py_decode_png, module)?)?;
    module.add_function(wrap_pyfunction!(py_encode_png, module)?)?;
    module.add_class::<clip::ClipAnimationReader>()?;
    module.add_class::<project_zip::ZipReader>()?;
    module.add_class::<timeline::TimelineCell>()?;
    module.add_class::<timeline::SheetEntry>()?;
    module.add_function(wrap_pyfunction!(timeline::sheet_entries, module)?)?;
    module.add_function(wrap_pyfunction!(timeline::resolve_timeline, module)?)?;
    module.add("TIMELINE_API_VERSION", timeline::TIMELINE_API_VERSION)?;
    module.add_function(wrap_pyfunction!(display::tint_onion, module)?)?;
    module.add_function(wrap_pyfunction!(display::filter_palette, module)?)?;
    module.add_class::<auto_paint::GuideSampler>()?;
    module.add_function(wrap_pyfunction!(auto_paint::label_runs, module)?)?;
    module.add_function(wrap_pyfunction!(auto_paint::connect_runs, module)?)?;
    module.add_function(wrap_pyfunction!(auto_paint::refine_face, module)?)?;
    module.add_function(wrap_pyfunction!(
        mask_transform::py_render_quad_labels,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        mask_transform::py_render_mesh_labels,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        palette_labels::py_surface_guided_palette_labels,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(map_colors::py_map_known_colors, module)?)?;
    module.add_function(wrap_pyfunction!(map_colors::py_region_neighbors, module)?)?;
    module.add_function(wrap_pyfunction!(py_unique_colors, module)?)?;
    module.add_function(wrap_pyfunction!(py_flood_fill, module)?)?;
    module.add_function(wrap_pyfunction!(py_remove_line_noise, module)?)?;
    module.add_function(wrap_pyfunction!(py_dilate, module)?)?;
    module.add_function(wrap_pyfunction!(py_erode, module)?)?;
    module.add_function(wrap_pyfunction!(py_label_regions, module)?)?;
    module.add_function(wrap_pyfunction!(py_line_flow_scores, module)?)?;
    module.add_function(wrap_pyfunction!(py_thin, module)?)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

/// Bound palette memory even for photographic layers. Keep the same smallest
/// sorted colors as numpy.unique(...)[..limit], without sorting every pixel.
#[pyfunction(name = "unique_colors")]
fn py_unique_colors(py: Python<'_>, pixels: &[u8], limit: usize) -> PyResult<Vec<u32>> {
    if !pixels.len().is_multiple_of(4) {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "expected packed u32 pixels",
        ));
    }
    py.detach(|| {
        if limit == 0 {
            return Ok(Vec::new());
        }
        // RGB has a fixed 24-bit domain: a 2 MiB bitmap avoids a tree lookup
        // for every repeated pixel in flat animation artwork.
        let mut seen = vec![0u64; (1 << 24) / 64];
        for chunk in pixels.chunks_exact(4) {
            let value = u32::from_ne_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
            if value > 0xffffff {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "expected 24-bit RGB",
                ));
            }
            seen[value as usize / 64] |= 1u64 << (value % 64);
        }
        let mut colors = Vec::new();
        for (index, mut word) in seen.into_iter().enumerate() {
            while word != 0 {
                colors.push((index * 64 + word.trailing_zeros() as usize) as u32);
                if colors.len() == limit {
                    return Ok(colors);
                }
                word &= word - 1;
            }
        }
        Ok(colors)
    })
}

#[pyfunction(name = "thin")]
fn py_thin<'py>(
    py: Python<'py>,
    mask: &[u8],
    width: usize,
    height: usize,
    iterations: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    check_size(mask, width, height)?;
    // 細線化では上下左右に1画素ずつの余白を作る。
    if width
        .checked_add(2)
        .and_then(|w| height.checked_add(2).and_then(|h| w.checked_mul(h)))
        .is_none()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "mask dimensions are too large",
        ));
    }
    let result = py.detach(|| pma_core::thin::thin(mask, width, height, iterations));
    Ok(PyBytes::new(py, &result))
}

#[pyfunction(name = "line_flow_scores")]
#[allow(clippy::too_many_arguments)]
fn py_line_flow_scores<'py>(
    py: Python<'py>,
    target: &[u8],
    guide: &[u8],
    width: usize,
    height: usize,
    block: usize,
    base_y: &[u8],
    base_x: &[u8],
    reach: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    check_size(target, width, height)?;
    check_size(guide, width, height)?;
    if block == 0
        || block > 32
        || width.checked_rem(block) != Some(0)
        || height.checked_rem(block) != Some(0)
        || reach > 128
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "invalid flow grid or search reach",
        ));
    }
    let blocks = (width / block) * (height / block);
    check_size(base_y, blocks, 8)?;
    check_size(base_x, blocks, 8)?;
    let side = 2 * reach + 1;
    if blocks.checked_mul(side * side * 4).is_none() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "flow grid is too large",
        ));
    }
    let decode = |data: &[u8]| -> Vec<i64> {
        data.chunks_exact(8)
            .map(|chunk| i64::from_ne_bytes(chunk.try_into().unwrap()))
            .collect()
    };
    let base_y = decode(base_y);
    let base_x = decode(base_x);
    let bytes: Vec<u8> = py.detach(|| {
        pma_core::line_flow::score_shifts(
            target,
            guide,
            (width, height),
            block,
            &base_y,
            &base_x,
            reach,
        )
        .iter()
        .flat_map(|value| value.to_ne_bytes())
        .collect()
    });
    Ok(PyBytes::new(py, &bytes))
}
