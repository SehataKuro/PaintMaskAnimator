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
