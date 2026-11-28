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
mod auto_plan;
mod brush;
mod bucket_tools;
mod clip;
mod color_stats;
mod colors;
mod compose;
mod display;
mod document;
mod document_compare;
mod export;
mod fill_decisions;
mod fill_session;
mod fill_tracking;
mod flow_color;
mod history;
mod image;
mod image_ops;
mod map_colors;
mod mask_transform;
mod onion;
mod palette;
mod palette_labels;
mod project_archive;
mod project_payload;
mod project_zip;
// PSD の読み込み（Tauri 移行の読み込み担当が追加）。
mod psd_import;
mod reference_check;
mod reference_scan;
mod selection;
mod tiled_color;
mod time_remap;
mod timeline;
mod tone_curve;
mod transform;
mod used_colors;

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

#[pyfunction(name = "alpha_runs")]
fn py_alpha_runs(
    py: Python<'_>,
    data: &[u8],
    width: usize,
    height: usize,
    stride: usize,
    alpha_offset: usize,
) -> PyResult<Vec<(usize, usize, usize)>> {
    py.detach(|| pma_core::alpha_runs::alpha_runs(data, width, height, stride, alpha_offset))
        .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))
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
    module.add_function(wrap_pyfunction!(clip::clip_render_cel, module)?)?;
    module.add_function(wrap_pyfunction!(clip::clip_read_animation, module)?)?;
    module.add_function(wrap_pyfunction!(clip::clip_animation_document, module)?)?;
    module.add_function(wrap_pyfunction!(clip::clip_line_art, module)?)?;
    module.add_function(wrap_pyfunction!(clip::cut_folder_document, module)?)?;
    module.add_class::<image::PyRgbaImage>()?;
    module.add_class::<brush::PyBrushStroke>()?;
    module.add_function(wrap_pyfunction!(brush::brush_segment_mask, module)?)?;
    module.add_function(wrap_pyfunction!(brush::brush_stamp_mask, module)?)?;
    module.add_function(wrap_pyfunction!(brush::fill_polygon_mask, module)?)?;
    module.add_function(wrap_pyfunction!(brush::fill_path_mask, module)?)?;
    module.add_function(wrap_pyfunction!(brush::stroke_path_mask, module)?)?;
    module.add_function(wrap_pyfunction!(brush::ellipse_path, module)?)?;
    module.add_function(wrap_pyfunction!(brush::stroke_outline_path, module)?)?;
    module.add_function(wrap_pyfunction!(brush::line_tool_mask, module)?)?;
    module.add_function(wrap_pyfunction!(brush::line_tool_commit, module)?)?;
    module.add_function(wrap_pyfunction!(brush::shape_tool_preview, module)?)?;
    module.add_function(wrap_pyfunction!(brush::shape_tool_commit, module)?)?;
    module.add_function(wrap_pyfunction!(brush::pressure_bezier_at, module)?)?;
    module.add_function(wrap_pyfunction!(brush::pressure_bezier_segments, module)?)?;
    module.add_function(wrap_pyfunction!(brush::stabilizer_segments, module)?)?;
    module.add_class::<document::PyCoreDocument>()?;
    module.add_class::<document::PyCellImage>()?;
    module.add_class::<project_zip::ZipReader>()?;
    module.add_class::<project_zip::ZipWriter>()?;
    selection::register(module)?;
    used_colors::register(module)?;
    transform::register(module)?;
    time_remap::register(module)?;
    onion::register(module)?;
    module.add_function(wrap_pyfunction!(project_payload::decode_v2_plan, module)?)?;
    module.add_function(wrap_pyfunction!(project_archive::_open_project, module)?)?;
    module.add_function(wrap_pyfunction!(project_archive::_save_project, module)?)?;
    module.add_function(wrap_pyfunction!(
        document_compare::_compare_document_v2,
        module
    )?)?;
    module.add_class::<timeline::TimelineCell>()?;
    module.add_class::<timeline::SheetEntry>()?;
    module.add_function(wrap_pyfunction!(timeline::sheet_entries, module)?)?;
    module.add_function(wrap_pyfunction!(timeline::resolve_timeline, module)?)?;
    module.add("TIMELINE_API_VERSION", timeline::TIMELINE_API_VERSION)?;
    module.add_function(wrap_pyfunction!(display::tint_onion, module)?)?;
    module.add_function(wrap_pyfunction!(display::filter_palette, module)?)?;
    module.add_class::<auto_paint::GuideSampler>()?;
    module.add_function(wrap_pyfunction!(auto_paint::label_runs, module)?)?;
    module.add_function(wrap_pyfunction!(
        reference_check::reference_color_stats,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        reference_check::reference_line_art,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        reference_check::reference_region_stats,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        reference_check::reference_region_under,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(auto_paint::connect_runs, module)?)?;
    module.add_function(wrap_pyfunction!(auto_paint::refine_face, module)?)?;
    module.add_function(wrap_pyfunction!(tiled_color::tiled_part_color, module)?)?;
    module.add_function(wrap_pyfunction!(flow_color::flow_outline_matches, module)?)?;
    module.add_function(wrap_pyfunction!(flow_color::flow_part_color, module)?)?;
    module.add_function(wrap_pyfunction!(auto_paint::solve_local_shift, module)?)?;
    module.add_function(wrap_pyfunction!(auto_paint::prepare_local_shift, module)?)?;
    module.add_function(wrap_pyfunction!(
        mask_transform::py_render_quad_labels,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        mask_transform::py_render_mesh_labels,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        mask_transform::py_splat_mask_labels,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        mask_transform::py_mask_labels_to_rgba,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        palette_labels::py_surface_guided_palette_labels,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(compose::compose_frame, module)?)?;
    module.add_function(wrap_pyfunction!(compose::compose_layer, module)?)?;
    module.add_function(wrap_pyfunction!(compose::compose_view_layers, module)?)?;
    module.add_function(wrap_pyfunction!(compose::compose_view, module)?)?;
    module.add_function(wrap_pyfunction!(compose::compose_display, module)?)?;
    module.add_function(wrap_pyfunction!(compose::compose_onion_draws, module)?)?;
    module.add_function(wrap_pyfunction!(compose::compose_tinted_onion, module)?)?;
    module.add_function(wrap_pyfunction!(
        compose::compose_scale_for_display,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(bucket_tools::bucket_fill_at, module)?)?;
    module.add_function(wrap_pyfunction!(
        reference_scan::reference_check_layer,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(auto_plan::auto_paint_layer, module)?)?;
    module.add_function(wrap_pyfunction!(
        bucket_tools::bucket_reference_pixels,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        bucket_tools::bucket_replace_color_request,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(bucket_tools::skewer_cells, module)?)?;
    module.add_class::<bucket_tools::PyBucketDrag>()?;
    module.add_class::<bucket_tools::PySkewerFill>()?;
    module.add_function(wrap_pyfunction!(map_colors::py_map_known_colors, module)?)?;
    module.add_function(wrap_pyfunction!(map_colors::py_region_neighbors, module)?)?;
    module.add_function(wrap_pyfunction!(
        color_stats::image_alpha_statistics,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        color_stats::binarize_alpha_for_pixel_art,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        color_stats::opaque_rgb_color_count,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(color_stats::border_background, module)?)?;
    module.add_function(wrap_pyfunction!(tone_curve::tone_curve_samples, module)?)?;
    module.add_function(wrap_pyfunction!(tone_curve::tone_curve_lut, module)?)?;
    module.add_function(wrap_pyfunction!(tone_curve::apply_tone_curve, module)?)?;
    module.add_function(wrap_pyfunction!(
        tone_curve::tone_preview_background_mask,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        tone_curve::apply_tone_preview_lut,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        palette::apply_color_reduction_palette,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(palette::line_palette_labels, module)?)?;
    module.add_function(wrap_pyfunction!(palette::select_palette_colors, module)?)?;
    module.add_function(wrap_pyfunction!(palette::palette_training_samples, module)?)?;
    module.add_function(wrap_pyfunction!(palette::hue_cluster_palette, module)?)?;
    module.add_function(wrap_pyfunction!(palette::palette_color_counts, module)?)?;
    module.add_function(wrap_pyfunction!(
        color_stats::estimate_mixed_boundary_pixels,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        color_stats::snap_overlay_to_exact_rgbs,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(colors::unique_colors, module)?)?;
    module.add_function(wrap_pyfunction!(colors::local_color_variation, module)?)?;
    module.add_function(wrap_pyfunction!(
        colors::normalize_tone_curve_points,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        colors::median_cut_palette_from_samples,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(history::history_trim_count, module)?)?;
    module.add_function(wrap_pyfunction!(
        history::history_shift_branch_positions,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(history::history_branch_overflow, module)?)?;
    module.add_function(wrap_pyfunction!(history::history_switch_steps, module)?)?;
    module.add_function(wrap_pyfunction!(history::history_branch_columns, module)?)?;
    module.add_function(wrap_pyfunction!(
        history::history_retained_positions,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(py_alpha_runs, module)?)?;
    module.add_function(wrap_pyfunction!(py_flood_fill, module)?)?;
    module.add_function(wrap_pyfunction!(py_remove_line_noise, module)?)?;
    module.add_function(wrap_pyfunction!(py_dilate, module)?)?;
    module.add_function(wrap_pyfunction!(py_erode, module)?)?;
    module.add_function(wrap_pyfunction!(py_label_regions, module)?)?;
    module.add_function(wrap_pyfunction!(py_line_flow_scores, module)?)?;
    module.add_function(wrap_pyfunction!(py_line_flow_interpolate, module)?)?;
    module.add_function(wrap_pyfunction!(py_thin, module)?)?;
    module.add_function(wrap_pyfunction!(py_fill_target_mask, module)?)?;
    module.add_function(wrap_pyfunction!(fill_tracking::estimate_shifts, module)?)?;
    module.add_function(wrap_pyfunction!(fill_tracking::warp_region, module)?)?;
    module.add_function(wrap_pyfunction!(
        fill_tracking::estimate_color_shifts,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        fill_tracking::tracking_region_center,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(fill_tracking::context_similarity, module)?)?;
    module.add_class::<fill_session::FillSession>()?;
    module.add_class::<fill_session::LineCache>()?;
    module.add_function(wrap_pyfunction!(
        fill_session::compute_fill_region_once,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(fill_decisions::enclosed_by, module)?)?;
    module.add_function(wrap_pyfunction!(fill_decisions::enclosed_islands, module)?)?;
    module.add_function(wrap_pyfunction!(
        fill_decisions::guess_target_categories,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(fill_decisions::skewer_seed, module)?)?;
    module.add_function(wrap_pyfunction!(fill_session::track_fill_region, module)?)?;
    module.add_function(wrap_pyfunction!(py_extend_line_ends, module)?)?;
    image_ops::register(module)?;
    module.add_function(wrap_pyfunction!(psd_import::import_read_psd, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_cut_folder_cels, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_plan_cut_folder, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_write_cut_folder, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_sheet_text, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_sheet_duration, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_write_key_sequence, module)?)?;
    module.add_function(wrap_pyfunction!(
        export::export_write_shadow_sequence,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(export::export_thumbnail_png, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_thumbnail_scale, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_frame_image, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_after_effects_text, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_guess_cut_number, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_psd_bytes, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_movie_frames, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_movie_frame, module)?)?;
    module.add_function(wrap_pyfunction!(export::export_encode_tga, module)?)?;
    module.add_function(wrap_pyfunction!(export::import_read_image, module)?)?;
    module.add_function(wrap_pyfunction!(export::import_image_file, module)?)?;
    module.add_function(wrap_pyfunction!(export::import_parse_timesheet, module)?)?;
    module.add_function(wrap_pyfunction!(export::import_scan_cut_folder, module)?)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
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

#[pyfunction(name = "line_flow_interpolate")]
#[allow(clippy::too_many_arguments, clippy::type_complexity)]
fn py_line_flow_interpolate<'py>(
    py: Python<'py>,
    dy: &[u8],
    dx: &[u8],
    width: usize,
    height: usize,
    block: usize,
    ys: &[u8],
    xs: &[u8],
) -> PyResult<Option<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)>> {
    let cells = width
        .checked_mul(height)
        .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("invalid flow grid dimensions"))?;
    check_size(dy, cells, 8)?;
    check_size(dx, cells, 8)?;
    check_size(ys, ys.len() / 8, 8)?;
    check_size(xs, ys.len() / 8, 8)?;
    let decode = |data: &[u8]| -> Vec<f64> {
        data.chunks_exact(8)
            .map(|chunk| f64::from_le_bytes(chunk.try_into().unwrap()))
            .collect()
    };
    let interpolate = || {
        pma_core::line_flow::interpolate_flow(
            &decode(dy),
            &decode(dx),
            (width, height),
            block,
            &decode(ys),
            &decode(xs),
        )
    };
    let result = if ys.len() >= 8192 * 8 {
        py.detach(interpolate)
    } else {
        interpolate()
    };
    match result {
        Ok((dy, dx)) => {
            let encode = |values: &[f64]| {
                PyBytes::new_with(py, values.len() * 8, |buffer| {
                    for (slot, value) in buffer.chunks_exact_mut(8).zip(values) {
                        slot.copy_from_slice(&value.to_le_bytes());
                    }
                    Ok(())
                })
            };
            Ok(Some((encode(&dy)?, encode(&dx)?)))
        }
        Err(pma_core::Error::Unsupported(_)) => Ok(None),
        Err(error) => Err(pyo3::exceptions::PyValueError::new_err(error.to_string())),
    }
}

#[pyfunction(name = "extend_line_ends")]
#[allow(clippy::type_complexity)]
fn py_extend_line_ends<'py>(
    py: Python<'py>,
    boundary: &[u8],
    width: usize,
    height: usize,
    strips: Vec<(usize, usize, usize, usize, Vec<u8>)>,
    max_length: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    check_size(boundary, width, height)?;
    if max_length == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "max_length must be positive",
        ));
    }
    for (y0, y1, x0, x1, skeleton) in &strips {
        if y0 > y1 || x0 > x1 || *y1 > height || *x1 > width {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "invalid strip bounds",
            ));
        }
        check_size(skeleton, x1 - x0, y1 - y0)?;
    }
    let result = py.detach(|| {
        let strips: Vec<_> = strips
            .iter()
            .map(
                |(y0, y1, x0, x1, skeleton)| pma_core::fill::line_ends::Strip {
                    y0: *y0,
                    y1: *y1,
                    x0: *x0,
                    x1: *x1,
                    skeleton,
                },
            )
            .collect();
        pma_core::fill::line_ends::extend_line_ends(boundary, width, height, &strips, max_length)
    });
    Ok(PyBytes::new(py, &result))
}

#[pyfunction(name = "fill_target_mask")]
#[pyo3(signature = (rgba, width, height, target_color, category=None))]
fn py_fill_target_mask<'py>(
    py: Python<'py>,
    rgba: &[u8],
    width: usize,
    height: usize,
    target_color: &str,
    category: Option<[u8; 3]>,
) -> PyResult<Bound<'py, PyBytes>> {
    use pma_core::fill::target::{fill_target_mask, TargetColor};
    if width.checked_mul(height).and_then(|n| n.checked_mul(4)) != Some(rgba.len()) {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "invalid RGBA dimensions or length",
        ));
    }
    let target = match target_color {
        "all" => TargetColor::All(category),
        "transparent" => TargetColor::Transparent,
        "surrounded_by_transparent" => TargetColor::SurroundedByTransparent,
        "black" => TargetColor::Black,
        "surrounded_by_black" => TargetColor::SurroundedByBlack,
        "white_transparent" => TargetColor::WhiteTransparent,
        "faint_as_transparent" => TargetColor::FaintAsTransparent,
        "opaque_and_inner_transparent" => TargetColor::OpaqueAndInnerTransparent,
        "transparent_and_closed_opaque" => TargetColor::TransparentAndClosedOpaque,
        _ => TargetColor::SurroundedByWhiteTransparent,
    };
    let result = py.detach(|| fill_target_mask(rgba, width, height, target));
    Ok(PyBytes::new(py, &result))
}
