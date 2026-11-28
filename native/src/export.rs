//! 書き出し・読み込み（pma_core::export・pma_core::import と pma-io）を Python から呼ぶ薄い入口。
//!
//! Python 版の書き出し・読み込みと同じファイル・画素になるかを突き合わせるための窓口
//! （tests/test_core_export_compare.py）。構成は `CutFolderLayout.to_json()` の JSON 文字列、
//! 入力欄の値は辞書で受け取る。

use std::path::PathBuf;

use pma_core::compose::{DisplayOptions, PremulImage};
use pma_core::export::cut_folder::{
    guess_cut_number, plan_export, CelSource, CutFolderLayout, ExportPlan, ImageFormat, Problem,
    SheetFormat, SheetHeader, Values,
};
use pma_core::export::{self, sheet, thumbnail, time_remap, timesheet};
use pma_core::import::image as import_image;
use pma_core::project::json;
use pma_io::{CutFolderWrite, WriteError};
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};

use crate::document::PyCoreDocument;

type Cel = (usize, String, i64, usize);

fn value_error(error: impl ToString) -> PyErr {
    PyValueError::new_err(error.to_string())
}

fn write_error(error: WriteError) -> PyErr {
    match error {
        WriteError::Core(error) => value_error(error),
        other => PyOSError::new_err(other.to_string()),
    }
}

fn get<'py, T: FromPyObjectOwned<'py>>(
    dict: Option<&Bound<'py, PyDict>>,
    key: &str,
) -> PyResult<Option<T>> {
    match dict {
        Some(dict) => match dict.get_item(key)? {
            Some(value) if !value.is_none() => Ok(Some(value.extract().map_err(Into::into)?)),
            _ => Ok(None),
        },
        None => Ok(None),
    }
}

fn display_options(options: Option<&Bound<'_, PyDict>>) -> PyResult<DisplayOptions> {
    Ok(DisplayOptions {
        active_layer: get(options, "active_layer")?,
        visible_colors: get(options, "visible_colors")?,
        color_remap: get(options, "color_remap")?.unwrap_or_default(),
        silhouette: get(options, "silhouette")?.unwrap_or(false),
    })
}

fn layout(layout_json: &str) -> PyResult<CutFolderLayout> {
    let parsed = json::parse(layout_json).map_err(value_error)?.value;
    CutFolderLayout::from_json(&parsed).map_err(value_error)
}

fn values(values: &Bound<'_, PyDict>) -> PyResult<Values> {
    let text = |key: &str| -> PyResult<Option<String>> {
        match values.get_item(key)? {
            Some(value) if !value.is_none() => Ok(Some(value.str()?.to_string())),
            _ => Ok(None),
        }
    };
    Ok(Values {
        title: text("title")?,
        scene: text("scene")?,
        episode: text("episode")?,
        cut: text("cut")?,
        ..Values::default()
    })
}

fn cels(items: Vec<Cel>) -> Vec<CelSource> {
    items
        .into_iter()
        .map(|(layer_index, cell, number, frame)| CelSource {
            layer_index,
            cell,
            number,
            frame,
        })
        .collect()
}

fn image_format(name: &str) -> PyResult<ImageFormat> {
    match name.to_ascii_lowercase().as_str() {
        "png" => Ok(ImageFormat::Png),
        "tga" => Ok(ImageFormat::Tga),
        _ => Err(value_error(format!("不明な画像形式: {name}"))),
    }
}

fn problem_items(problem: &Problem) -> Vec<String> {
    match problem {
        Problem::MissingValue(field) => vec!["missing_value".into(), field.id().into()],
        Problem::BadName(name, place) => vec!["bad_name".into(), name.clone(), place.id().into()],
        Problem::NoNumber => vec!["no_number".into(), "cell_file".into()],
        Problem::Duplicate(path) => vec!["duplicate".into(), path.clone()],
    }
}

fn plan_dict<'py>(py: Python<'py>, plan: &ExportPlan) -> PyResult<Bound<'py, PyDict>> {
    let result = PyDict::new(py);
    result.set_item("folder_name", &plan.folder_name)?;
    let files: Vec<(String, usize, String, i64)> = plan
        .files
        .iter()
        .map(|(path, cel)| (path.clone(), cel.layer_index, cel.cell.clone(), cel.number))
        .collect();
    result.set_item("files", files)?;
    result.set_item("timesheet_path", plan.timesheet_path.clone())?;
    result.set_item("folders", plan.folders.clone())?;
    result.set_item(
        "problems",
        plan.problems.iter().map(problem_items).collect::<Vec<_>>(),
    )?;
    result.set_item("timesheet_format", plan.timesheet_format.extension())?;
    let header = PyDict::new(py);
    header.set_item("cut", &plan.sheet_header.cut)?;
    header.set_item("episode", &plan.sheet_header.episode)?;
    header.set_item("scene", &plan.sheet_header.scene)?;
    result.set_item("sheet_header", header)?;
    Ok(result)
}

/// 書き出す絵（列・セル名・セル番号・コマ）とセル番号のない絵の数（`cut_folder_cels`）。
#[pyfunction]
pub fn export_cut_folder_cels(document: PyRef<'_, PyCoreDocument>) -> (Vec<Cel>, usize) {
    let (cels, skipped) = sheet::cut_folder_cels(document.inner());
    (
        cels.into_iter()
            .map(|cel| (cel.layer_index, cel.cell, cel.number, cel.frame))
            .collect(),
        skipped,
    )
}

/// カットフォルダーの書き出しの計画（`plan_export`）。
#[pyfunction]
pub fn export_plan_cut_folder<'py>(
    py: Python<'py>,
    layout_json: &str,
    values_dict: &Bound<'py, PyDict>,
    cel_list: Vec<Cel>,
) -> PyResult<Bound<'py, PyDict>> {
    let plan = plan_export(
        &layout(layout_json)?,
        &values(values_dict)?,
        &cels(cel_list),
    );
    plan_dict(py, &plan)
}

/// カットフォルダーを parent の下に書き出し、そのパスを返す（`write_cut_folder`）。
/// 計画に問題があれば ValueError。
#[pyfunction]
#[pyo3(signature = (document, parent, layout_json, values_dict, memo="", playback_range_end=None))]
pub fn export_write_cut_folder(
    py: Python<'_>,
    document: PyRef<'_, PyCoreDocument>,
    parent: PathBuf,
    layout_json: &str,
    values_dict: &Bound<'_, PyDict>,
    memo: &str,
    playback_range_end: Option<i64>,
) -> PyResult<PathBuf> {
    let layout = layout(layout_json)?;
    let document = document.inner();
    let (cels, _) = sheet::cut_folder_cels(document);
    let plan = plan_export(&layout, &values(values_dict)?, &cels);
    if !plan.ok() {
        return Err(value_error(format!("{:?}", plan.problems)));
    }
    let job = CutFolderWrite {
        document,
        plan: &plan,
        image_format: layout.image_format,
        memo,
        playback_range_end,
    };
    py.detach(|| pma_io::write_cut_folder(&parent, &job, &mut |_, _| true))
        .map_err(write_error)
}

/// 今のシートのタイムシートの文字列（`sheet_text`）。
#[pyfunction]
#[pyo3(signature = (document, format, sheet_name, cut="", episode="", scene="", playback_range_end=None))]
#[allow(clippy::too_many_arguments)]
pub fn export_sheet_text(
    document: PyRef<'_, PyCoreDocument>,
    format: &str,
    sheet_name: &str,
    cut: &str,
    episode: &str,
    scene: &str,
    playback_range_end: Option<i64>,
) -> PyResult<String> {
    let format = match format {
        "tdts" => SheetFormat::Tdts,
        _ => SheetFormat::Xdts,
    };
    let header = SheetHeader {
        cut: cut.into(),
        episode: episode.into(),
        scene: scene.into(),
    };
    sheet::sheet_text(
        document.inner(),
        format,
        sheet_name,
        &header,
        playback_range_end,
    )
    .map_err(value_error)
}

/// タイムシートの長さ（`MainWindow._sheet_duration`）。
#[pyfunction]
#[pyo3(signature = (document, playback_range_end=None))]
pub fn export_sheet_duration(
    document: PyRef<'_, PyCoreDocument>,
    playback_range_end: Option<i64>,
) -> PyResult<usize> {
    document
        .inner()
        .sheet_duration(playback_range_end)
        .map_err(value_error)
}

/// 連番＋TS.csv を destination に書き出し、枚数を返す（キーがなければ None）。
#[pyfunction]
pub fn export_write_key_sequence(
    py: Python<'_>,
    document: PyRef<'_, PyCoreDocument>,
    destination: PathBuf,
    format: &str,
) -> PyResult<Option<usize>> {
    let format = image_format(format)?;
    let document = document.inner();
    py.detach(|| pma_io::write_key_sequence(&destination, document, format, &mut |_, _| true))
        .map_err(write_error)
}

/// 影素材を destination に書き出し、枚数を返す（書ける絵がなければ None）。
#[pyfunction]
pub fn export_write_shadow_sequence(
    py: Python<'_>,
    document: PyRef<'_, PyCoreDocument>,
    destination: PathBuf,
    shadow_rgbs: Vec<[u8; 3]>,
) -> PyResult<Option<usize>> {
    let document = document.inner();
    py.detach(|| {
        pma_io::write_shadow_sequence(&destination, document, &shadow_rgbs, &mut |_, _| true)
    })
    .map_err(write_error)
}

/// .pman のサムネイルの PNG（白地の合成を長辺 512 以下、`_thumbnail_png`）。
#[pyfunction]
#[pyo3(signature = (document, frame, options=None))]
pub fn export_thumbnail_png<'py>(
    py: Python<'py>,
    document: PyRef<'py, PyCoreDocument>,
    frame: usize,
    options: Option<&Bound<'py, PyDict>>,
) -> PyResult<Bound<'py, PyBytes>> {
    let options = display_options(options)?;
    let document = document.inner();
    let data = py
        .detach(|| thumbnail::project_thumbnail(document, frame, &options))
        .map_err(value_error)?;
    Ok(PyBytes::new(py, &data))
}

/// 乗算済み RGBA を Qt の SmoothTransformation と同じく長辺 max 以下へ縮め、
/// (幅, 高さ, 乗算済み RGBA) を返す。
#[pyfunction]
pub fn export_thumbnail_scale<'py>(
    py: Python<'py>,
    width: usize,
    height: usize,
    premultiplied: Vec<u8>,
    max_size: usize,
) -> PyResult<(usize, usize, Bound<'py, PyBytes>)> {
    let image =
        PremulImage::from_premultiplied(width, height, premultiplied).map_err(value_error)?;
    let small = py
        .detach(|| thumbnail::thumbnail_image(&image, max_size))
        .map_err(value_error)?;
    Ok((
        small.width(),
        small.height(),
        PyBytes::new(py, small.pixels()),
    ))
}

/// 今のコマの白地の合成の PNG か TGA（カメラボタン・PNG／TGA 保存）。
#[pyfunction]
#[pyo3(signature = (document, frame, format="png", options=None))]
pub fn export_frame_image<'py>(
    py: Python<'py>,
    document: PyRef<'py, PyCoreDocument>,
    frame: usize,
    format: &str,
    options: Option<&Bound<'py, PyDict>>,
) -> PyResult<Bound<'py, PyBytes>> {
    let options = display_options(options)?;
    let format = image_format(format)?;
    let document = document.inner();
    let data = py
        .detach(|| match format {
            ImageFormat::Png => export::frame_png(document, frame, &options),
            ImageFormat::Tga => export::frame_tga(document, frame, &options),
        })
        .map_err(value_error)?;
    Ok(PyBytes::new(py, &data))
}

/// レイヤーのタイムリマップのキーフレームデータ（書けるセルがなければ None）。
#[pyfunction]
pub fn export_after_effects_text(
    document: PyRef<'_, PyCoreDocument>,
    layer: usize,
    fps: f64,
) -> PyResult<Option<String>> {
    let states = sheet::sheet_numbers(document.inner(), layer).map_err(value_error)?;
    Ok(time_remap::after_effects_text(&states, fps))
}

/// 文書の PSD（`psd_dialog`）。書けるキーがなければ None。(バイト列, キーの数)。
#[pyfunction]
pub fn export_psd_bytes<'py>(
    py: Python<'py>,
    document: PyRef<'py, PyCoreDocument>,
) -> PyResult<Option<(Bound<'py, PyBytes>, usize)>> {
    let document = document.inner();
    let result = py
        .detach(|| export::psd::document_psd(document, &mut |_, _| true))
        .map_err(value_error)?;
    Ok(result.map(|(data, keys)| (PyBytes::new(py, &data), keys)))
}

/// MP4 のコマごとに使うコマの番号（尺の数だけ繰り返す）。
#[pyfunction]
pub fn export_movie_frames(document: PyRef<'_, PyCoreDocument>) -> Vec<usize> {
    export::movie::output_frames(document.inner())
}

/// MP4 の index 枚目の絵（ストレート RGBA）。overlay は乗算済み RGBA のカット名。
#[pyfunction]
#[pyo3(signature = (document, frame, index, overlay=None, options=None))]
pub fn export_movie_frame<'py>(
    py: Python<'py>,
    document: PyRef<'py, PyCoreDocument>,
    frame: usize,
    index: usize,
    overlay: Option<&[u8]>,
    options: Option<&Bound<'py, PyDict>>,
) -> PyResult<Bound<'py, PyBytes>> {
    let options = display_options(options)?;
    let document = document.inner();
    let data = py
        .detach(|| export::movie::output_frame(document, frame, index, &options, overlay))
        .map_err(value_error)?;
    Ok(PyBytes::new(py, &data))
}

/// プロジェクト名のカット番号（`guess_cut_number`）。
#[pyfunction]
pub fn export_guess_cut_number(name: &str) -> String {
    guess_cut_number(name)
}

/// ストレート RGBA を Pillow と同じ RLE の TGA にする。
#[pyfunction]
pub fn export_encode_tga<'py>(
    py: Python<'py>,
    width: usize,
    height: usize,
    rgba: &[u8],
) -> PyResult<Bound<'py, PyBytes>> {
    let data = export::tga::encode_tga_rle(width, height, rgba).map_err(value_error)?;
    Ok(PyBytes::new(py, &data))
}

/// 画像ファイルのバイト列を Qt と同じ乗算済み RGBA にする（`_read_image_file`）。
/// 読めなければ ValueError（文言は Python と同じ）。
#[pyfunction]
pub fn import_read_image<'py>(
    py: Python<'py>,
    data: &[u8],
) -> PyResult<(usize, usize, Bound<'py, PyBytes>)> {
    let image = py
        .detach(|| import_image::read_image(data))
        .map_err(value_error)?;
    Ok((
        image.width(),
        image.height(),
        PyBytes::new(py, image.pixels()),
    ))
}

/// 画像ファイルを読み、変換してキャンバスの中央に置いた文書の絵
/// （ストレート RGBA、`import_image_sequence` の1枚分・2値化の設定なし）。
#[pyfunction]
#[pyo3(signature = (path, canvas_width, canvas_height, draft=false))]
pub fn import_image_file<'py>(
    py: Python<'py>,
    path: PathBuf,
    canvas_width: usize,
    canvas_height: usize,
    draft: bool,
) -> PyResult<Bound<'py, PyBytes>> {
    let data = std::fs::read(&path).map_err(|error| PyOSError::new_err(error.to_string()))?;
    let name = path.to_string_lossy().into_owned();
    let image = py
        .detach(|| {
            import_image::import_image_file(&data, &name, canvas_width, canvas_height, draft)
        })
        .map_err(value_error)?;
    Ok(PyBytes::new(py, image.rgba()))
}

/// XDTS・TDTS の文字列をタイムシートの列にする（`_parse_xdts_timesheet`）。
#[pyfunction]
pub fn import_parse_timesheet<'py>(
    py: Python<'py>,
    raw_text: &str,
) -> PyResult<Bound<'py, PyDict>> {
    let parsed = timesheet::parse_timesheet(raw_text).map_err(value_error)?;
    let column = |column: &timesheet::SheetColumn| -> PyResult<Bound<'py, PyDict>> {
        let item = PyDict::new(py);
        item.set_item("uid", &column.uid)?;
        item.set_item("field_id", column.field_id)?;
        item.set_item("track_no", column.track_no)?;
        item.set_item("name", &column.name)?;
        item.set_item("group", column.group.id())?;
        item.set_item("start_frame", 0)?;
        item.set_item("end_frame", parsed.end_frame)?;
        item.set_item("states", column.states.clone())?;
        item.set_item("display_values", column.display_values.clone())?;
        item.set_item("blank_label_count", column.blank_label_count)?;
        item.set_item("bindable", column.bindable)?;
        Ok(item)
    };
    let result = PyDict::new(py);
    result.set_item("format", &parsed.format)?;
    result.set_item("fps", py.None())?;
    result.set_item("start_frame", 0)?;
    result.set_item("end_frame", parsed.end_frame)?;
    result.set_item("states", parsed.states.clone())?;
    result.set_item("blank_label_count", parsed.blank_label_count)?;
    let tracks = PyList::empty(py);
    for track in &parsed.tracks {
        tracks.append(column(track)?)?;
    }
    result.set_item("tracks", tracks)?;
    let columns = PyList::empty(py);
    for item in &parsed.sheet_columns {
        columns.append(column(item)?)?;
    }
    result.set_item("sheet_columns", columns)?;
    Ok(result)
}

/// カットフォルダーのタイムシートとセル画像（パスは root からの相対、`scan_cut_folder`）。
#[pyfunction]
pub fn import_scan_cut_folder<'py>(py: Python<'py>, root: PathBuf) -> PyResult<Bound<'py, PyDict>> {
    let contents = pma_io::scan_cut_folder(&root).map_err(|e| PyOSError::new_err(e.to_string()))?;
    let result = PyDict::new(py);
    result.set_item("timesheets", contents.timesheets.clone())?;
    let cels = PyDict::new(py);
    for (key, drawings) in &contents.cels {
        cels.set_item(key, drawings.clone())?;
    }
    result.set_item("cels", cels)?;
    let names = PyDict::new(py);
    for (key, name) in &contents.names {
        names.set_item(key, name)?;
    }
    result.set_item("names", names)?;
    Ok(result)
}
