//! タイムリマップの貼り付け（pma_core::export::time_remap_paste）を Python から呼ぶ窓口。
//!
//! Qt 版の `TimeRemapController.parse_text`・`sheet_columns_from_parsed`・
//! `default_layer_bindings`・`TimeRemapPasteDialog.parsed_result`・`apply_to_active_layer`・
//! `apply_xdts_layer_bindings` と同じ結果になるかを突き合わせる
//! （tests/test_core_time_remap_compare.py）。

use pma_core::document::comparison;
use pma_core::export::time_remap_paste::{self as paste, RemapSheet, Text};
use pma_core::export::timesheet::SheetColumn;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::document::PyCoreDocument;

fn text_error(text: Text) -> PyErr {
    PyValueError::new_err(text.format())
}

fn column_dict<'py>(
    py: Python<'py>,
    column: &SheetColumn,
    end_frame: Option<usize>,
) -> PyResult<Bound<'py, PyDict>> {
    let item = PyDict::new(py);
    item.set_item("uid", &column.uid)?;
    item.set_item("field_id", column.field_id)?;
    item.set_item("track_no", column.track_no)?;
    item.set_item("name", &column.name)?;
    item.set_item("group", column.group.id())?;
    if let Some(end) = end_frame {
        item.set_item("start_frame", 0)?;
        item.set_item("end_frame", end)?;
    }
    item.set_item("states", column.states.clone())?;
    item.set_item("display_values", column.display_values.clone())?;
    item.set_item("blank_label_count", column.blank_label_count)?;
    item.set_item("bindable", column.bindable)?;
    Ok(item)
}

fn sheet_dict<'py>(py: Python<'py>, sheet: &RemapSheet) -> PyResult<Bound<'py, PyDict>> {
    let result = PyDict::new(py);
    result.set_item("format", &sheet.format)?;
    result.set_item("fps", sheet.fps)?;
    result.set_item("start_frame", sheet.start_frame)?;
    result.set_item("end_frame", sheet.end_frame)?;
    result.set_item("states", sheet.states.clone())?;
    result.set_item("blank_label_count", sheet.blank_label_count)?;
    if !sheet.sheet_columns.is_empty() {
        for (key, columns) in [
            ("tracks", &sheet.tracks),
            ("sheet_columns", &sheet.sheet_columns),
        ] {
            let list = PyList::empty(py);
            for column in columns {
                list.append(column_dict(py, column, Some(sheet.end_frame))?)?;
            }
            result.set_item(key, list)?;
        }
    }
    Ok(result)
}

/// 貼り付けた文字を読む（`parse_text`）。読めなければ ValueError（訳す前の文）。
#[pyfunction]
fn time_remap_parse<'py>(py: Python<'py>, raw_text: &str) -> PyResult<Bound<'py, PyDict>> {
    let sheet = paste::parse_text(raw_text).map_err(text_error)?;
    sheet_dict(py, &sheet)
}

type Columns<'py> = (Bound<'py, PyList>, Vec<(String, usize)>);

/// レイヤーへ紐づけられる列（`sheet_columns_from_parsed`）と既定の紐づけ。
#[pyfunction]
fn time_remap_columns<'py>(
    py: Python<'py>,
    raw_text: &str,
    cell_name: &str,
    layers: Vec<(usize, String)>,
    active_layer: usize,
) -> PyResult<Columns<'py>> {
    let sheet = paste::parse_text(raw_text).map_err(text_error)?;
    let columns = paste::sheet_columns_from_parsed(&sheet, cell_name);
    let list = PyList::empty(py);
    let end = (!sheet.sheet_columns.is_empty()).then_some(sheet.end_frame);
    for column in &columns {
        list.append(column_dict(py, column, end)?)?;
    }
    Ok((
        list,
        paste::default_layer_bindings(&columns, &layers, active_layer),
    ))
}

/// 使う行と紐づけで当てる（`parsed_result` から `apply_*` まで）。確かめの文と
/// 知らせと変更の JSON を返す。当てられなければ ValueError（訳す前の文）。
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn time_remap_apply(
    mut document: PyRefMut<'_, PyCoreDocument>,
    raw_text: &str,
    cell_name: &str,
    included: Vec<bool>,
    bindings: Vec<(String, usize)>,
    active_layer: usize,
    current_frame: usize,
    layer_names: Vec<String>,
) -> PyResult<Option<(String, String, String)>> {
    let sheet = paste::parse_text(raw_text).map_err(text_error)?;
    let Some(choice) = paste::parsed_result(&sheet, cell_name, &included, &bindings) else {
        return Ok(None);
    };
    let plan = if choice.uses_bindings() {
        paste::plan_xdts_bindings(document.inner(), &choice, &layer_names)
    } else {
        paste::plan_active_layer(document.inner(), &choice, active_layer)
    }
    .map_err(text_error)?;
    let mut message: String = plan.message.iter().map(Text::format).collect();
    if !plan.links.is_empty() {
        let links: Vec<String> = plan.links.iter().map(Text::format).collect();
        message = message.replace("{links}", &links.join("\n"));
    }
    let changes = paste::apply_plan(document.inner_mut(), &plan, current_frame)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(Some((
        message,
        plan.status.format(),
        comparison::command_notices(changes),
    )))
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(time_remap_parse, module)?)?;
    module.add_function(wrap_pyfunction!(time_remap_columns, module)?)?;
    module.add_function(wrap_pyfunction!(time_remap_apply, module)?)?;
    Ok(())
}
