use pyo3::prelude::*;
use pyo3::types::PyBytes;

fn invalid(message: &str) -> PyErr {
    pyo3::exceptions::PyValueError::new_err(message.to_string())
}

/// 既存の対応で塗り直した RGBA バイト列を返し、未対応の色があれば `None` を返す。
/// `view_preview.map_color_pixels` の既存の対応による色置換と一致する。
///
/// 配布版は abi3（限定API）で作るので、numpy の配列をその場で書き換える
/// バッファ protocol は使えない。画素はバイト列で受け取って返す。
#[pyfunction(name = "map_known_colors")]
pub fn py_map_known_colors<'py>(
    py: Python<'py>,
    rgba: &[u8],
    codes: Vec<u32>,
    indices: Vec<u8>,
    palette: Vec<(u8, u8, u8)>,
    line_max: u8,
) -> PyResult<Option<Bound<'py, PyBytes>>> {
    if !rgba.len().is_multiple_of(4) {
        return Err(invalid("expected packed rgba pixels"));
    }
    if codes.len() != indices.len() {
        return Err(invalid("the color mapping needs one index per color"));
    }
    if codes.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(invalid("the color mapping must be sorted and distinct"));
    }
    if palette.is_empty() {
        return Err(invalid("the palette must not be empty"));
    }
    let palette: Vec<[u8; 3]> = palette.iter().map(|(r, g, b)| [*r, *g, *b]).collect();
    let mapping = pma_core::map_colors::ColorMapping {
        codes: &codes,
        indices: &indices,
    };
    let mut pixels = rgba.to_vec();
    let known = py.detach(|| {
        pma_core::map_colors::map_known_colors(&mut pixels, &mapping, &palette, line_max)
    });
    Ok(if known {
        Some(PyBytes::new(py, &pixels))
    } else {
        None
    })
}

/// `view_preview.region_neighbors_numpy` と同じ、番号順に並べた隣接領域の組を返す。
#[pyfunction(name = "region_neighbors")]
pub fn py_region_neighbors(
    py: Python<'_>,
    labels: &[u8],
    width: usize,
    height: usize,
    gap: usize,
) -> PyResult<Vec<(i64, i64)>> {
    if width.checked_mul(height).map(|total| total * 4) != Some(labels.len()) {
        return Err(invalid("label size does not match width x height"));
    }
    if gap > 64 {
        return Err(invalid("the neighbour gap is too large"));
    }
    let values: Vec<i32> = labels
        .chunks_exact(4)
        .map(|chunk| i32::from_ne_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]))
        .collect();
    Ok(py.detach(|| pma_core::map_colors::region_neighbors(&values, width, height, gap)))
}
