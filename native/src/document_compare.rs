use pma_core::document::{comparison, v2};
use pma_core::image::RgbaImage;
use pma_core::project::payload::decode_v2_plan;
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyBytes, PyDict},
};

type ProjectedImage<'py> = (String, usize, usize, Bound<'py, PyBytes>);
type Comparison<'py> = (
    String,
    Vec<ProjectedImage<'py>>,
    Vec<i64>,
    String,
    (usize, usize),
);

/// 比較テスト用に、復号済みの画像から Rust 文書を読み直して投影する。
#[pyfunction]
#[pyo3(signature = (payload_json, images, operations_json="[]"))]
pub(crate) fn _compare_document_v2<'py>(
    py: Python<'py>,
    payload_json: &str,
    images: &Bound<'py, PyDict>,
    operations_json: &str,
) -> PyResult<Comparison<'py>> {
    let input = v2::parse_json(payload_json).map_err(value_error)?;
    let names = images.keys().extract::<Vec<String>>()?;
    let plan = decode_v2_plan(&input, &names).map_err(|error| value_error(error.reason))?;
    let artwork = plan
        .images
        .iter()
        .map(|entry| {
            let path = entry[0]
                .as_str()
                .ok_or_else(|| value_error("画像パスがありません"))?;
            let record = images.get_item(path)?.ok_or_else(|| value_error(path))?;
            let (width, height, bytes): (usize, usize, Bound<'_, PyBytes>) = record.extract()?;
            RgbaImage::from_rgba(width, height, bytes.as_bytes()).map_err(value_error)
        })
        .collect::<PyResult<Vec<_>>>()?;
    let operations = v2::parse_json(operations_json).map_err(value_error)?;
    let (projection, resolved, changes, selection) = py
        .detach(|| {
            let mut document = v2::from_plan(&input, &plan, &artwork)?;
            let changes = comparison::apply_operations(&mut document, &operations)?;
            let (frame, layer) = document.selection();
            let selection = (
                document
                    .frame_order()
                    .iter()
                    .position(|&id| id == frame)
                    .ok_or(pma_core::Error::TimelineSizeOverflow)?,
                document
                    .track_order()
                    .iter()
                    .position(|&id| id == layer)
                    .ok_or(pma_core::Error::TimelineSizeOverflow)?,
            );
            Ok::<_, pma_core::Error>((
                v2::project(&document, &input)?,
                document.resolve_sources()?.1,
                changes,
                selection,
            ))
        })
        .map_err(value_error)?;
    let json = projection.ordered_json(payload_json).map_err(value_error)?;
    let pngs = projection
        .pngs
        .into_iter()
        .map(|entry| {
            let image = entry.image;
            (
                entry.path,
                image.width(),
                image.height(),
                PyBytes::new(py, image.rgba()),
            )
        })
        .collect();
    Ok((json, pngs, resolved, changes, selection))
}

fn value_error(error: impl ToString) -> PyErr {
    PyValueError::new_err(error.to_string())
}
