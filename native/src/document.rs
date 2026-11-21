use crate::image::PyRgbaImage;
use pma_core::document::{model::Document, v2};
use pma_core::project::payload::decode_v2_plan;
use pyo3::{exceptions::PyValueError, prelude::*};

/// Python の文書窓口が所有するコア文書。
#[pyclass(module = "pma_native", name = "_CoreDocument")]
pub(crate) struct PyCoreDocument(Document);

fn value_error(error: impl ToString) -> PyErr {
    PyValueError::new_err(error.to_string())
}

#[pymethods]
impl PyCoreDocument {
    /// v2 の JSON と復号済み画像から文書を作る。画素バッファは共有する。
    #[staticmethod]
    fn from_v2(
        py: Python<'_>,
        payload_json: &str,
        names: Vec<String>,
        images: Vec<PyRef<'_, PyRgbaImage>>,
    ) -> PyResult<Self> {
        let artwork: Vec<_> = images.iter().map(|image| image.share()).collect();
        py.detach(|| {
            let payload = v2::parse_json(payload_json).map_err(value_error)?;
            let plan =
                decode_v2_plan(&payload, &names).map_err(|error| value_error(error.reason))?;
            v2::from_plan(&payload, &plan, &artwork)
                .map(Self)
                .map_err(value_error)
        })
    }

    /// 保存用 JSON と、ZIP に書く順のパス・共有画像を返す。
    fn project_v2(
        &self,
        py: Python<'_>,
        metadata_json: &str,
    ) -> PyResult<(String, Vec<(String, PyRgbaImage)>)> {
        let (json, pngs) = py
            .detach(|| {
                let metadata = v2::parse_json(metadata_json)?;
                let projection = v2::project(&self.0, &metadata)?;
                let json = projection.ordered_json(metadata_json)?;
                Ok::<_, pma_core::Error>((json, projection.pngs))
            })
            .map_err(value_error)?;
        Ok((
            json,
            pngs.into_iter()
                .map(|entry| (entry.path, entry.image.into()))
                .collect(),
        ))
    }
}
