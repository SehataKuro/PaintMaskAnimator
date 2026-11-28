//! PSD の読み込み（pma-core の import::psd）を Python から呼ぶ。比べる試験用。

use pma_core::import::psd;
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};

type Track<'py> = (String, bool, f64, Vec<Bound<'py, PyBytes>>);

/// PSD のバイト列を (幅, 高さ, [(名前, 表示, 不透明度, [RGBA])], 捨てた数) にする。
#[pyfunction]
pub fn import_read_psd<'py>(
    py: Python<'py>,
    data: &[u8],
) -> PyResult<(usize, usize, Vec<Track<'py>>, usize)> {
    let read = py
        .detach(|| psd::read_psd(data, &mut |_, _| true))
        .map_err(|e| PyValueError::new_err(e.to_string()))?
        .expect("not cancelled");
    let tracks = read
        .tracks
        .into_iter()
        .map(|t| {
            let images = t.images.iter().map(|i| PyBytes::new(py, i)).collect();
            (t.name, t.visible, t.opacity, images)
        })
        .collect();
    Ok((read.width, read.height, tracks, read.skipped))
}

/// PSD の全体の見た目（サブビュー、`PSDImage.composite()`）を (幅, 高さ, RGBA) にする。
#[pyfunction]
pub fn import_composite_psd<'py>(
    py: Python<'py>,
    data: &[u8],
) -> PyResult<(usize, usize, Bound<'py, PyBytes>)> {
    let (width, height, rgba) = py
        .detach(|| psd::composite_psd(data))
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok((width, height, PyBytes::new(py, &rgba)))
}

/// 【Group C】サブビューの JPEG（EXIF の向きに従う、`QImageReader.setAutoTransform(True)`）を
/// (幅, 高さ, RGBA) にする。
#[pyfunction]
pub fn import_subview_jpeg<'py>(
    py: Python<'py>,
    data: &[u8],
) -> PyResult<(usize, usize, Bound<'py, PyBytes>)> {
    use pma_core::import::{image, orientation};
    let (w, h, rgba) =
        image::decode_image(data).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let (w, h, rgba) =
        orientation::apply_orientation(orientation::jpeg_orientation(data), w, h, &rgba);
    Ok((w, h, PyBytes::new(py, &rgba)))
}
