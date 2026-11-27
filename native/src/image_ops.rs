//! 線・色のメニューの操作（pma_core::image_ops）の窓口。比較テストから呼ぶ。
use crate::image::PyRgbaImage;
use pma_core::image::Rect;
use pma_core::image_ops::{
    antialias, color_focus, color_replace, despeckle, dust, hsv, line_split, line_tidy,
    palette_build, reduce, soft_blur, MaskRef, PixelEdit,
};
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};

type PyRect = (usize, usize, usize, usize);
type PyEdit = (PyRgbaImage, Option<PyRect>, usize);
type Rgb = (u8, u8, u8);
/// `(画像, 切り出し, 拡大率, 足す数, 消す数)`。
type PyPreview = (PyRgbaImage, PyRect, usize, usize, usize);

fn core_error(error: pma_core::Error) -> PyErr {
    PyValueError::new_err(error.to_string())
}

fn rect(rect: Rect) -> PyRect {
    (rect.x, rect.y, rect.width, rect.height)
}

fn edit(edit: PixelEdit) -> PyEdit {
    (
        PyRgbaImage::from(edit.image),
        edit.changed.map(rect),
        edit.pixels,
    )
}

fn rgbs(values: &[Rgb]) -> Vec<[u8; 3]> {
    values.iter().map(|&(r, g, b)| [r, g, b]).collect()
}

fn mask<'a>(
    data: &'a Option<Vec<u8>>,
    size: Option<(usize, usize)>,
) -> PyResult<Option<MaskRef<'a>>> {
    match (data, size) {
        (Some(data), Some((width, height))) => {
            if width.checked_mul(height) != Some(data.len()) {
                return Err(PyValueError::new_err("mask size does not match its length"));
            }
            Ok(Some(MaskRef {
                data,
                width,
                height,
            }))
        }
        (None, _) => Ok(None),
        (Some(_), None) => Err(PyValueError::new_err("mask needs its (width, height)")),
    }
}

fn diagonal(value: Option<&str>) -> PyResult<line_tidy::Diagonal> {
    match value {
        Some("add") => Ok(line_tidy::Diagonal::Add),
        Some("remove") => Ok(line_tidy::Diagonal::Remove),
        None => Ok(line_tidy::Diagonal::Keep),
        Some(other) => Err(PyValueError::new_err(format!(
            "unknown diagonal mode: {other}"
        ))),
    }
}

/// ゴミ取りツール（despeckle・fill_holes・line_noise）の 1 セル分。
#[pyfunction(signature = (image, mode, max_area, selected=None))]
pub fn image_ops_remove_dust(
    py: Python<'_>,
    image: PyRef<'_, PyRgbaImage>,
    mode: &str,
    max_area: usize,
    selected: Option<Vec<Rgb>>,
) -> PyResult<Option<PyEdit>> {
    let mode = match mode {
        "despeckle" => dust::DustMode::Despeckle,
        "fill_holes" => dust::DustMode::FillHoles,
        "line_noise" => dust::DustMode::LineNoise,
        other => return Err(PyValueError::new_err(format!("unknown dust mode: {other}"))),
    };
    let image = image.share();
    let selected = selected.map(|values| rgbs(&values));
    let result = py.detach(|| {
        dust::remove_dust(
            &image,
            &dust::DustOptions {
                mode,
                max_area,
                selected: selected.as_deref(),
            },
        )
    });
    Ok(result.map_err(core_error)?.map(edit))
}

/// 線整えの 1 セル分。`(結果, 足した画素数, 消した画素数)`。
#[pyfunction(signature = (image, colours, diagonal_mode=Some("add"), region=None, region_size=None))]
pub fn image_ops_tidy_lines(
    py: Python<'_>,
    image: PyRef<'_, PyRgbaImage>,
    colours: Vec<Rgb>,
    diagonal_mode: Option<&str>,
    region: Option<Vec<u8>>,
    region_size: Option<(usize, usize)>,
) -> PyResult<(Option<PyEdit>, usize, usize)> {
    let diagonal = diagonal(diagonal_mode)?;
    let region = mask(&region, region_size)?;
    let image = image.share();
    let colours = rgbs(&colours);
    let result = py
        .detach(|| line_tidy::tidy_lines(&image, &colours, diagonal, region))
        .map_err(core_error)?;
    Ok((result.edit.map(edit), result.added, result.removed))
}

/// 線整えのプレビュー。`(画像, 切り出し, 拡大率, 足す数, 消す数)`。
#[pyfunction(signature = (image, colours, diagonal_mode=Some("add"), region=None, region_size=None, scale=8, margin=6, limit=480))]
#[allow(clippy::too_many_arguments)]
pub fn image_ops_tidy_preview(
    py: Python<'_>,
    image: PyRef<'_, PyRgbaImage>,
    colours: Vec<Rgb>,
    diagonal_mode: Option<&str>,
    region: Option<Vec<u8>>,
    region_size: Option<(usize, usize)>,
    scale: usize,
    margin: usize,
    limit: usize,
) -> PyResult<Option<PyPreview>> {
    let diagonal = diagonal(diagonal_mode)?;
    let region = mask(&region, region_size)?;
    let image = image.share();
    let colours = rgbs(&colours);
    let options = line_tidy::PreviewOptions {
        scale,
        margin,
        limit,
    };
    let preview = py
        .detach(|| line_tidy::tidy_preview(&image, &colours, diagonal, region, options))
        .map_err(core_error)?;
    Ok(preview.map(|p| {
        (
            PyRgbaImage::from(p.image),
            rect(p.crop),
            p.scale,
            p.added,
            p.removed,
        )
    }))
}

/// 線整えの対象色。
#[pyfunction]
pub fn image_ops_tidy_line_colours(
    py: Python<'_>,
    image: PyRef<'_, PyRgbaImage>,
    selected: Vec<Rgb>,
) -> Vec<Rgb> {
    let image = image.share();
    let selected = rgbs(&selected);
    py.detach(|| line_tidy::tidy_line_colours(&image, &selected))
        .into_iter()
        .map(|[r, g, b]| (r, g, b))
        .collect()
}

/// 使用色の置換（置換・削除・統合）の 1 セル分。
#[pyfunction(signature = (image, mapping, selection=None, selection_size=None))]
pub fn image_ops_replace_colors(
    py: Python<'_>,
    image: PyRef<'_, PyRgbaImage>,
    mapping: Vec<(Rgb, Rgb)>,
    selection: Option<Vec<u8>>,
    selection_size: Option<(usize, usize)>,
) -> PyResult<Option<PyEdit>> {
    let selection = mask(&selection, selection_size)?;
    let image = image.share();
    let mapping: Vec<([u8; 3], [u8; 3])> = mapping
        .into_iter()
        .map(|((r, g, b), (r2, g2, b2))| ([r, g, b], [r2, g2, b2]))
        .collect();
    let result = py.detach(|| color_replace::replace_colors(&image, &mapping, selection));
    Ok(result.map_err(core_error)?.map(edit))
}

fn pairs(mapping: Vec<([u8; 3], [u8; 3])>) -> Vec<(Rgb, Rgb)> {
    mapping
        .into_iter()
        .map(|([r, g, b], [r2, g2, b2])| ((r, g, b), (r2, g2, b2)))
        .collect()
}

/// 色削除の置換表。
#[pyfunction]
pub fn image_ops_delete_mapping(selected: Vec<Rgb>) -> Vec<(Rgb, Rgb)> {
    pairs(color_replace::delete_mapping(&rgbs(&selected)))
}

/// 色統合の置換表（親が白なら None）。
#[pyfunction]
pub fn image_ops_merge_mapping(parent: Rgb, selected: Vec<Rgb>) -> Option<Vec<(Rgb, Rgb)>> {
    color_replace::merge_mapping([parent.0, parent.1, parent.2], &rgbs(&selected)).map(pairs)
}

/// 同じ形の 2 枚から求めた置換表。
#[pyfunction]
pub fn image_ops_aligned_mapping(
    source: PyRef<'_, PyRgbaImage>,
    target: PyRef<'_, PyRgbaImage>,
) -> Option<Vec<(Rgb, Rgb)>> {
    color_replace::aligned_replacement_mapping(&source.share(), &target.share()).map(pairs)
}

/// HSV でずらした色。
#[pyfunction]
pub fn hsv_shifted_color(rgb: Rgb, hue: i32, saturation: i32, value: i32) -> Rgb {
    let [r, g, b] = hsv::shifted_color([rgb.0, rgb.1, rgb.2], hue, saturation, value);
    (r, g, b)
}

/// HSV でずらした置換表。
#[pyfunction]
pub fn hsv_shifted_mapping(
    rgbs_in: Vec<Rgb>,
    hue: i32,
    saturation: i32,
    value: i32,
) -> Vec<(Rgb, Rgb)> {
    pairs(hsv::shifted_mapping(
        &rgbs(&rgbs_in),
        hue,
        saturation,
        value,
    ))
}

type Contour = Vec<(i64, i64)>;

/// マスクの輪郭。
#[pyfunction]
pub fn image_ops_mask_contours(
    mask: Vec<u8>,
    width: usize,
    height: usize,
) -> PyResult<Vec<Contour>> {
    if width.checked_mul(height) != Some(mask.len()) {
        return Err(PyValueError::new_err("mask size does not match its length"));
    }
    Ok(color_focus::mask_contours(&mask, width, height))
}

/// 色を選択範囲にするときの形：`(マスク, 輪郭, いちばん大きい輪郭, 囲む矩形)`。
#[pyfunction]
pub fn image_ops_color_selection<'py>(
    py: Python<'py>,
    image: PyRef<'_, PyRgbaImage>,
    rgb: Rgb,
) -> Option<(Bound<'py, PyBytes>, Vec<Contour>, usize, PyRect)> {
    let image = image.share();
    let selection = py.detach(|| color_focus::color_selection(&image, [rgb.0, rgb.1, rgb.2]))?;
    Some((
        PyBytes::new(py, &selection.mask),
        selection.contours,
        selection.largest,
        rect(selection.bounds),
    ))
}

/// 色があるか。
#[pyfunction]
pub fn image_ops_contains_rgb(image: PyRef<'_, PyRgbaImage>, rgb: Rgb) -> bool {
    color_focus::contains_rgb(&image.share(), [rgb.0, rgb.1, rgb.2])
}

/// MainLineRepaint の 1 枚分：`(LINE, Paint, 線があるか, 塗りが残ったか)`。
#[pyfunction]
pub fn image_ops_split_line(
    py: Python<'_>,
    image: PyRef<'_, PyRgbaImage>,
    selected: Vec<Rgb>,
) -> PyResult<(PyRgbaImage, PyRgbaImage, bool, bool)> {
    let image = image.share();
    let selected = rgbs(&selected);
    let split = py
        .detach(|| line_split::split_line(&image, &selected))
        .map_err(core_error)?;
    Ok((
        PyRgbaImage::from(split.line),
        PyRgbaImage::from(split.paint),
        split.has_line,
        split.has_paint,
    ))
}

fn rgba_size(rgba: &[u8], width: usize, height: usize) -> PyResult<()> {
    if width.checked_mul(height).and_then(|n| n.checked_mul(4)) != Some(rgba.len()) {
        return Err(PyValueError::new_err("rgba size does not match its length"));
    }
    Ok(())
}

/// デジタル TP のアンチエイリアスの画素数。
#[pyfunction]
pub fn image_ops_count_antialias(
    py: Python<'_>,
    rgba: Vec<u8>,
    width: usize,
    height: usize,
) -> PyResult<usize> {
    rgba_size(&rgba, width, height)?;
    Ok(py.detach(|| antialias::count_antialias(&rgba, width, height)))
}

/// デジタル TP のアンチエイリアスを取り除いた RGBA と画素数。
#[pyfunction]
pub fn image_ops_remove_antialias<'py>(
    py: Python<'py>,
    rgba: Vec<u8>,
    width: usize,
    height: usize,
) -> PyResult<(Bound<'py, PyBytes>, usize)> {
    rgba_size(&rgba, width, height)?;
    let (out, changed) = py.detach(|| antialias::remove_antialias(&rgba, width, height));
    Ok((PyBytes::new(py, &out), changed))
}

/// 離れた小さな点を透明にした RGBA と画素数。
#[pyfunction]
pub fn image_ops_remove_isolated_specks<'py>(
    py: Python<'py>,
    rgba: Vec<u8>,
    width: usize,
    height: usize,
    max_pixels: usize,
) -> PyResult<(Bound<'py, PyBytes>, usize)> {
    rgba_size(&rgba, width, height)?;
    let (out, removed) =
        py.detach(|| despeckle::remove_isolated_specks(&rgba, width, height, max_pixels));
    Ok((PyBytes::new(py, &out), removed))
}

/// 弱いぼかし。
#[pyfunction]
pub fn image_ops_soft_blur<'py>(
    py: Python<'py>,
    rgba: Vec<u8>,
    width: usize,
    height: usize,
    sigma: f64,
) -> PyResult<Bound<'py, PyBytes>> {
    rgba_size(&rgba, width, height)?;
    let out = py.detach(|| soft_blur::soft_blur(&rgba, width, height, sigma));
    Ok(PyBytes::new(py, &out))
}

/// 色相の特徴量（単精度 4 つずつの bytes）。
#[pyfunction]
pub fn image_ops_rgb_hsv_features<'py>(py: Python<'py>, colours: Vec<Rgb>) -> Bound<'py, PyBytes> {
    let bytes: Vec<u8> = colours
        .into_iter()
        .flat_map(|(r, g, b)| palette_build::rgb_hsv_features([r, g, b]))
        .flat_map(f32::to_ne_bytes)
        .collect();
    PyBytes::new(py, &bytes)
}

fn triples(values: Vec<[u8; 3]>) -> Vec<Rgb> {
    values.into_iter().map(|[r, g, b]| (r, g, b)).collect()
}

/// 少面積でも残したい優先色。
#[pyfunction]
pub fn image_ops_priority_palette_colors(sample: Vec<Rgb>, maximum: usize) -> Vec<Rgb> {
    triples(palette_build::priority_palette_colors(
        &rgbs(&sample),
        maximum,
    ))
}

/// 色相でまとめたパレット。
#[pyfunction]
pub fn image_ops_hue_cluster_palette(sample: Vec<Rgb>, color_count: usize) -> PyResult<Vec<Rgb>> {
    palette_build::hue_cluster_palette(&rgbs(&sample), color_count)
        .map(triples)
        .map_err(core_error)
}

/// 共通パレット（`build_color_reduction_palette`）。
#[pyfunction(signature = (images, color_count, alpha_threshold=128, max_samples=262144, opaque_background=false, background=None, line=false))]
#[allow(clippy::too_many_arguments)]
pub fn image_ops_build_palette(
    py: Python<'_>,
    images: Vec<PyRef<'_, PyRgbaImage>>,
    color_count: usize,
    alpha_threshold: i64,
    max_samples: usize,
    opaque_background: bool,
    background: Option<Rgb>,
    line: bool,
) -> PyResult<Vec<Rgb>> {
    let images: Vec<_> = images.iter().map(|image| image.share()).collect();
    let options = palette_build::PaletteOptions {
        color_count,
        alpha_threshold,
        max_samples,
        opaque_background,
        background: background.map(|(r, g, b)| [r, g, b]),
        line,
    };
    py.detach(|| {
        let refs: Vec<_> = images.iter().collect();
        palette_build::build_color_reduction_palette(&refs, &options)
    })
    .map(triples)
    .map_err(core_error)
}

/// 2 値化の 1 セル分。
#[pyfunction(signature = (image, mode, palette=None, blur=0.0, despeckle_pixels=0, alpha_threshold=128, opaque_background=false, background=None, tone_curve_points=None, flatten_paper=false))]
#[allow(clippy::too_many_arguments)]
pub fn image_ops_binarize(
    py: Python<'_>,
    image: PyRef<'_, PyRgbaImage>,
    mode: &str,
    palette: Option<Vec<Rgb>>,
    blur: f64,
    despeckle_pixels: usize,
    alpha_threshold: i64,
    opaque_background: bool,
    background: Option<Rgb>,
    tone_curve_points: Option<Vec<(f64, f64)>>,
    flatten_paper: bool,
) -> PyResult<Option<PyEdit>> {
    let mode = match mode {
        "surface" => reduce::ExtractionMode::Surface,
        "line" => reduce::ExtractionMode::Line,
        "digital" => reduce::ExtractionMode::Digital,
        other => {
            return Err(PyValueError::new_err(format!(
                "unknown extraction mode: {other}"
            )))
        }
    };
    let settings = reduce::ReduceSettings {
        mode,
        palette: palette.map(|values| rgbs(&values)),
        flatten_paper,
        blur,
        despeckle: despeckle_pixels,
        alpha_threshold,
        opaque_background,
        background: background.map(|(r, g, b)| [r, g, b]),
        tone_curve_points: tone_curve_points.unwrap_or_else(|| vec![(0.0, 0.0), (1.0, 1.0)]),
    };
    let image = image.share();
    let result = py.detach(|| reduce::binarize(&image, &settings));
    Ok(result.map_err(core_error)?.map(edit))
}

/// 外周からつながる明るい背景を透明にした RGBA（背景が無ければ None）。
#[pyfunction(signature = (rgba, width, height, tolerance=24))]
pub fn image_ops_border_background_transparent<'py>(
    py: Python<'py>,
    rgba: Vec<u8>,
    width: usize,
    height: usize,
    tolerance: i64,
) -> PyResult<Option<Bound<'py, PyBytes>>> {
    rgba_size(&rgba, width, height)?;
    let out = py
        .detach(|| reduce::border_background_transparent(&rgba, width, height, tolerance))
        .map_err(core_error)?;
    Ok(out.map(|out| PyBytes::new(py, &out)))
}

/// この窓口の関数を登録する。
pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(image_ops_remove_dust, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_tidy_lines, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_tidy_preview, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_tidy_line_colours, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_replace_colors, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_delete_mapping, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_merge_mapping, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_aligned_mapping, module)?)?;
    module.add_function(wrap_pyfunction!(hsv_shifted_color, module)?)?;
    module.add_function(wrap_pyfunction!(hsv_shifted_mapping, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_mask_contours, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_color_selection, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_contains_rgb, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_split_line, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_count_antialias, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_remove_antialias, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_remove_isolated_specks, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_soft_blur, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_rgb_hsv_features, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_priority_palette_colors, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_hue_cluster_palette, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_build_palette, module)?)?;
    module.add_function(wrap_pyfunction!(image_ops_binarize, module)?)?;
    module.add_function(wrap_pyfunction!(
        image_ops_border_background_transparent,
        module
    )?)?;
    Ok(())
}
