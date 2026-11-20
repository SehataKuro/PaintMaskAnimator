//! 区画採色の Python 用入口。

use pma_core::tiled_color::{self, Rect, Tile};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

type Bytes<'a> = Bound<'a, PyBytes>;
type Window<'a> = (Rect, Bytes<'a>);
type TileInput<'a> = (
    Rect,
    Bytes<'a>,
    Bytes<'a>,
    Rect,
    (i32, i32, i32, i32),
    Window<'a>,
    Window<'a>,
    [Bytes<'a>; 2],
    [Bytes<'a>; 5],
    Option<Bytes<'a>>,
    Bytes<'a>,
    Bytes<'a>,
);

fn area((left, top, width, height): Rect) -> Option<usize> {
    if left < 0 || top < 0 || width < 0 || height < 0 {
        return None;
    }
    (width as usize).checked_mul(height as usize)
}

fn validate(tile: &Tile<'_>) -> bool {
    let Some(target_area) = area(tile.target) else {
        return false;
    };
    let Some(source_area) = area(tile.source) else {
        return false;
    };
    tile.filled.len() == target_area
        && tile.outline.len() == target_area
        && area(tile.target_black.0) == Some(tile.target_black.1.len())
        && area(tile.source_black.0) == Some(tile.source_black.1.len())
        && source_area.checked_mul(8) == Some(tile.values.len())
        && source_area.checked_mul(4) == Some(tile.labels.len())
        && tile
            .wide
            .iter()
            .chain(&tile.excluded)
            .all(|mask| mask.len() == source_area)
        && tile.confirmed.is_none_or(|mask| mask.len() == source_area)
}

/// `PartTracker._tiled_color` 用に、行優先の区画窓を受け取って RGB または None を返す。
#[pyfunction]
pub fn tiled_part_color(
    py: Python<'_>,
    tiles: Vec<TileInput<'_>>,
    local: Window<'_>,
    size: usize,
    sizes: &[u8],
) -> PyResult<Option<(u8, u8, u8)>> {
    let local = (local.0, local.1.as_bytes());
    if area(local.0) != Some(local.1.len())
        || size > local.1.len()
        || !sizes.len().is_multiple_of(8)
    {
        return Err(PyValueError::new_err(
            "部位マスクまたは領域面積の長さが不正",
        ));
    }
    let tiles: Vec<_> = tiles
        .iter()
        .map(
            |(
                target,
                filled,
                outline,
                source,
                shift,
                target_black,
                source_black,
                wide,
                excluded,
                confirmed,
                values,
                labels,
            )| {
                Tile {
                    target: *target,
                    filled: filled.as_bytes(),
                    outline: outline.as_bytes(),
                    source: *source,
                    shift: *shift,
                    target_black: (target_black.0, target_black.1.as_bytes()),
                    source_black: (source_black.0, source_black.1.as_bytes()),
                    wide: wide.each_ref().map(PyBytesMethods::as_bytes),
                    excluded: excluded.each_ref().map(PyBytesMethods::as_bytes),
                    confirmed: confirmed.as_ref().map(PyBytesMethods::as_bytes),
                    values: values.as_bytes(),
                    labels: labels.as_bytes(),
                }
            },
        )
        .collect();
    if !tiles.iter().all(validate) {
        return Err(PyValueError::new_err("区画窓の寸法または長さが不正"));
    }
    py.detach(|| {
        let sizes: Vec<_> = sizes
            .chunks_exact(8)
            .map(|data| u64::from_ne_bytes(data.try_into().unwrap()))
            .collect();
        tiled_color::tiled_color(&tiles, local, size, &sizes)
            .map(|rgb| rgb.map(|[r, g, b]| (r, g, b)))
            .map_err(|error| PyValueError::new_err(error.to_string()))
    })
}
