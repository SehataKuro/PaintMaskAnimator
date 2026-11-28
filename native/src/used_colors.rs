//! 使用色パネル（pma-core の `used_colors`）を Python のテストから呼ぶ口。
//!
//! 0.9.0 の画面（app/src/colors.rs）が使う計算を、今の Qt 版（main_window_used_color.py・
//! hsv_adjust_dialog.py・color_chart.py・replacement_sets.py）と突き合わせるためのもの。
//! カラーチャートは JSON の文字列でやり取りする（Python は json.dumps / json.loads）。

use std::collections::BTreeMap;

use pma_core::image::RgbaImage;
use pma_core::project::json::{self, Json};
use pma_core::used_colors::chart::{self, Chart, Hit, Role};
use pma_core::used_colors::{pixels, Rgb};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

fn parse_json(text: &str) -> PyResult<Json> {
    json::parse(text)
        .map(|parsed| parsed.value)
        .map_err(|e| PyValueError::new_err(e.to_string()))
}

fn dump(chart: &Chart) -> PyResult<String> {
    chart
        .to_json()
        .dumps(None)
        .map_err(|e| PyValueError::new_err(e.to_string()))
}

/// Python の hit（`{"group_id": ..., "role": "parent"|"child", "index": n}`）を
/// `(group_id, role, index)` で受け取る。
fn hit((group_id, role, index): (String, String, usize)) -> PyResult<Hit> {
    let role = match role.as_str() {
        "parent" => Role::Parent,
        "child" => Role::Child(index),
        other => return Err(PyValueError::new_err(format!("unknown role {other:?}"))),
    };
    Ok(Hit { group_id, role })
}

/// 画素の色を使用色の行の色にした、使われている色（値の昇順、`limit` 色まで）。
#[pyfunction]
fn used_colors_image_colors(rgba: &[u8], limit: usize) -> PyResult<Vec<Rgb>> {
    if !rgba.len().is_multiple_of(4) {
        return Err(PyValueError::new_err("rgba length must be a multiple of 4"));
    }
    Ok(pixels::image_colors(rgba, limit))
}

/// 1枚の絵の色を置き換える（統合・色置換・色削除）。置き換え先が白なら透明にする。
/// `(置き換えた画素, 置き換えた数)`。何も変わらなければ元の画素と 0。
#[pyfunction]
#[pyo3(signature = (rgba, width, height, mapping, mask=None))]
fn used_colors_remap_image<'py>(
    py: Python<'py>,
    rgba: &[u8],
    width: usize,
    height: usize,
    mapping: Vec<(Rgb, Rgb)>,
    mask: Option<&[u8]>,
) -> PyResult<(Bound<'py, PyBytes>, usize)> {
    let image = RgbaImage::from_rgba(width, height, rgba)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    let mapping: BTreeMap<Rgb, Rgb> = mapping.into_iter().collect();
    let mapping = pixels::effective_mapping(&mapping);
    let result = py.detach(|| pixels::remap_image(&image, &mapping, mask));
    Ok(match result {
        Some((image, count)) => (PyBytes::new(py, image.rgba()), count),
        None => (PyBytes::new(py, rgba), 0),
    })
}

/// 「色相・彩度・明度」の `{元の色: ずらした色}`（hsv_adjust_dialog.shifted_mapping）。
#[pyfunction]
fn used_colors_shifted_mapping(
    rgbs: Vec<Rgb>,
    hue: i32,
    saturation: i32,
    value: i32,
) -> Vec<(Rgb, Rgb)> {
    pixels::shifted_mapping(&rgbs, hue, saturation, value)
        .into_iter()
        .collect()
}

/// 置換色の組を .pmarep の JSON にする（replacement_sets.to_data）。
#[pyfunction]
fn replacement_set_to_data(mapping: Vec<(Rgb, Rgb)>) -> PyResult<String> {
    let mapping: BTreeMap<Rgb, Rgb> = mapping.into_iter().collect();
    pixels::replacement_set_to_json(&mapping)
        .dumps(None)
        .map_err(|e| PyValueError::new_err(e.to_string()))
}

/// .pmarep の JSON を置換色の組にする（replacement_sets.from_data）。
#[pyfunction]
fn replacement_set_from_data(text: &str) -> PyResult<Vec<(Rgb, Rgb)>> {
    let data = parse_json(text)?;
    pixels::replacement_set_from_json(&data)
        .map(|m| m.into_iter().collect())
        .map_err(PyValueError::new_err)
}

/// color_chart.normalize_color_chart。
#[pyfunction]
fn color_chart_normalize(text: &str) -> PyResult<String> {
    dump(&chart::normalize(&parse_json(text)?))
}

/// color_chart.merge_color_charts。
#[pyfunction]
fn color_chart_merge(existing: &str, current: &str) -> PyResult<String> {
    let existing = chart::normalize(&parse_json(existing)?);
    let current = chart::normalize(&parse_json(current)?);
    dump(&existing.merge(&current))
}

/// color_chart.move_chart_color。動かせなければ None。
#[pyfunction]
#[pyo3(signature = (text, source, target=None))]
fn color_chart_move(
    text: &str,
    source: (String, String, usize),
    target: Option<(String, String, usize)>,
) -> PyResult<Option<String>> {
    let chart = chart::normalize(&parse_json(text)?);
    let target = target.map(hit).transpose()?;
    chart
        .move_color(&hit(source)?, target.as_ref())
        .map(|c| dump(&c))
        .transpose()
}

/// color_chart.assign_chart_tag。付けられなければ None。
#[pyfunction]
fn color_chart_assign_tag(
    text: &str,
    target: (String, String, usize),
    tag: &str,
) -> PyResult<Option<String>> {
    let chart = chart::normalize(&parse_json(text)?);
    chart
        .assign_tag(&hit(target)?, tag)
        .map(|c| dump(&c))
        .transpose()
}

/// PMAG（PaintMaskGroup）の文字列を読む（color_chart.read_pmag）。
#[pyfunction]
fn color_chart_read_pmag(text: &str) -> PyResult<String> {
    let chart = chart::read_pmag(text).map_err(PyValueError::new_err)?;
    dump(&chart)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(used_colors_image_colors, module)?)?;
    module.add_function(wrap_pyfunction!(used_colors_remap_image, module)?)?;
    module.add_function(wrap_pyfunction!(used_colors_shifted_mapping, module)?)?;
    module.add_function(wrap_pyfunction!(replacement_set_to_data, module)?)?;
    module.add_function(wrap_pyfunction!(replacement_set_from_data, module)?)?;
    module.add_function(wrap_pyfunction!(color_chart_normalize, module)?)?;
    module.add_function(wrap_pyfunction!(color_chart_merge, module)?)?;
    module.add_function(wrap_pyfunction!(color_chart_move, module)?)?;
    module.add_function(wrap_pyfunction!(color_chart_assign_tag, module)?)?;
    module.add_function(wrap_pyfunction!(color_chart_read_pmag, module)?)?;
    Ok(())
}
