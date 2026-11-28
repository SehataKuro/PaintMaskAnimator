//! .pman のバイト列を純 Rust で開く・保存する入口（pma_core::project::archive）。
//!
//! Tauri 版と同じ経路を Python の読み書きと突き合わせるためのテスト用の窓口。
use std::collections::BTreeMap;

use pma_core::project::archive::{self, PngCache, ProjectError, SaveOptions};
use pma_core::project::json;
use pma_core::project::zip::ZipTime;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::document::PyCoreDocument;

fn project_error(error: ProjectError) -> PyErr {
    PyValueError::new_err((error.kind(), error.message()))
}

fn cache_to_python<'py>(py: Python<'py>, cache: &PngCache) -> BTreeMap<u64, Bound<'py, PyBytes>> {
    cache
        .iter()
        .map(|(generation, data)| (generation, PyBytes::new(py, data)))
        .collect()
}

/// .pman のバイト列を開き、(文書, metadata の JSON, 幅, 高さ, 形式, {世代: PNG}) を返す。
/// 失敗は ValueError((種類, 文言))。
#[pyfunction]
#[allow(clippy::type_complexity)]
pub fn _open_project<'py>(
    py: Python<'py>,
    data: &[u8],
) -> PyResult<(
    PyCoreDocument,
    String,
    usize,
    usize,
    i64,
    BTreeMap<u64, Bound<'py, PyBytes>>,
)> {
    let opened = py
        .detach(|| archive::open_project(data))
        .map_err(project_error)?;
    let metadata = opened.metadata.dumps(None).map_err(PyValueError::new_err)?;
    let cache = cache_to_python(py, &opened.png_cache);
    Ok((
        PyCoreDocument::wrap(opened.document),
        metadata,
        opened.width,
        opened.height,
        opened.format_version,
        cache,
    ))
}

/// 文書を .pman のバイト列にし、(バイト列, 残した {世代: PNG}) を返す。
/// 失敗は ValueError((種類, 文言))。
#[pyfunction]
#[pyo3(signature = (document, metadata_json, date_time, thumbnail=None, dots_per_meter=None, cache=None))]
#[allow(clippy::type_complexity)]
pub fn _save_project<'py>(
    py: Python<'py>,
    document: PyRef<'_, PyCoreDocument>,
    metadata_json: &str,
    date_time: (u16, u8, u8, u8, u8, u8),
    thumbnail: Option<Vec<u8>>,
    dots_per_meter: Option<(u32, u32)>,
    cache: Option<BTreeMap<u64, Vec<u8>>>,
) -> PyResult<(Bound<'py, PyBytes>, BTreeMap<u64, Bound<'py, PyBytes>>)> {
    let metadata = json::parse(metadata_json)
        .map_err(|error| PyValueError::new_err(("Json", error.to_string())))?
        .value;
    let mut png_cache = PngCache::new();
    for (generation, data) in cache.unwrap_or_default() {
        png_cache.remember(generation, data);
    }
    let (y, m, d, h, min, sec) = date_time;
    let mut options = SaveOptions::new(ZipTime(y, m, d, h, min, sec));
    options.thumbnail_png = thumbnail.as_deref();
    if let Some(dots) = dots_per_meter {
        options.dots_per_meter = dots;
    }
    let document = document.inner();
    let bytes = py
        .detach(|| archive::save_project(document, &metadata, &options, Some(&mut png_cache)))
        .map_err(project_error)?;
    Ok((PyBytes::new(py, &bytes), cache_to_python(py, &png_cache)))
}
