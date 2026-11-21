use pma_core::image::{Rect, RgbaImage};
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};

/// ストレート RGBA8888 の画素を保持する画像。
#[pyclass(module = "pma_native", name = "RgbaImage")]
pub(crate) struct PyRgbaImage(RgbaImage);

impl PyRgbaImage {
    /// 画素と世代を共有するコア画像を返す。
    pub(crate) fn share(&self) -> RgbaImage {
        self.0.clone()
    }
}

impl From<RgbaImage> for PyRgbaImage {
    fn from(image: RgbaImage) -> Self {
        Self(image)
    }
}

fn image_error(error: pma_core::Error) -> PyErr {
    PyValueError::new_err(error.to_string())
}

#[pymethods]
impl PyRgbaImage {
    /// 行優先の RGBA バイト列から画像を作る。
    #[new]
    fn new(
        py: Python<'_>,
        width: usize,
        height: usize,
        pixels: &Bound<'_, PyBytes>,
    ) -> PyResult<Self> {
        let pixels = pixels.as_bytes();
        py.detach(|| RgbaImage::from_rgba(width, height, pixels))
            .map(Self)
            .map_err(image_error)
    }

    /// 全チャンネルが 0 の画像を作る。
    #[staticmethod]
    fn blank(py: Python<'_>, width: usize, height: usize) -> PyResult<Self> {
        py.detach(|| RgbaImage::blank(width, height))
            .map(Self)
            .map_err(image_error)
    }

    /// 画像の幅を返す。
    #[getter]
    fn width(&self) -> usize {
        self.0.width()
    }

    /// 画像の高さを返す。
    #[getter]
    fn height(&self) -> usize {
        self.0.height()
    }

    /// 画像の現在の世代を返す。
    #[getter]
    fn generation(&self) -> u64 {
        self.0.generation()
    }

    /// 画素を共有する複製を返す。
    fn copy(&self) -> Self {
        Self(self.0.clone())
    }

    /// 呼び出し側が添える札（Qt 属性の表の番号）を返す。
    #[getter]
    fn tag(&self) -> u32 {
        self.0.tag()
    }

    /// 画素と世代を共有し、札だけを変えた複製を返す。
    fn with_tag(&self, tag: u32) -> Self {
        Self(self.0.with_tag(tag))
    }

    /// 全画素を不変の bytes で返す。
    fn rgba_bytes<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, self.0.rgba())
    }

    /// 矩形内の画素を行優先の bytes で返す。
    fn read_rect<'py>(
        &self,
        py: Python<'py>,
        x: usize,
        y: usize,
        width: usize,
        height: usize,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let rect = Rect {
            x,
            y,
            width,
            height,
        };
        let pixels = py
            .detach(|| self.0.copy_rgba_rect(rect))
            .map_err(image_error)?;
        Ok(PyBytes::new(py, &pixels))
    }

    /// 矩形内の画素を行優先の bytes で上書きする。
    fn write_rect(
        &mut self,
        py: Python<'_>,
        x: usize,
        y: usize,
        width: usize,
        height: usize,
        pixels: &Bound<'_, PyBytes>,
    ) -> PyResult<()> {
        let rect = Rect {
            x,
            y,
            width,
            height,
        };
        let pixels = pixels.as_bytes();
        py.detach(|| self.0.write_rgba(rect, pixels))
            .map_err(image_error)
    }
}
