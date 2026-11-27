//! 変形場による採色の Python 用入口。

use crate::tiled_color::area;
use pma_core::flow_color::{self, Coordinates, Interior, MaskWindow, Outline};
use pma_core::tiled_color::Rect;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

type Bytes<'a> = Bound<'a, PyBytes>;
type Window<'a> = (Rect, Bytes<'a>);
type Points<'a> = ([Bytes<'a>; 4], Bytes<'a>);
type OutlineInput<'a> = (
    Window<'a>,
    Points<'a>,
    Window<'a>,
    [Bytes<'a>; 2],
    Rect,
    [Bytes<'a>; 2],
    Window<'a>,
);
type InteriorInput<'a> = (
    Window<'a>,
    Points<'a>,
    Rect,
    [Bytes<'a>; 5],
    Option<Bytes<'a>>,
    Bytes<'a>,
    Bytes<'a>,
);

fn window<'a>(input: &'a Window<'_>) -> MaskWindow<'a> {
    (input.0, input.1.as_bytes())
}

fn coordinates<'a>(input: &'a Points<'_>) -> Coordinates<'a> {
    Coordinates {
        axes: input.0.each_ref().map(PyBytesMethods::as_bytes),
        consistent: input.1.as_bytes(),
    }
}

fn valid_points(count: usize, points: &Coordinates<'_>) -> bool {
    count == points.consistent.len()
        && points
            .axes
            .iter()
            .all(|axis| count.checked_mul(8) == Some(axis.len()))
}

fn invalid_input() -> PyErr {
    PyValueError::new_err("変形場の窓、座標または領域面積の長さが不正")
}

/// 輪郭照合用の入力を検証し、内部の採色に進むかを返す。
#[pyfunction]
pub fn flow_outline_matches(
    py: Python<'_>,
    outline: OutlineInput<'_>,
    part: Rect,
) -> PyResult<bool> {
    let outline = Outline {
        points: window(&outline.0),
        coordinates: coordinates(&outline.1),
        band: window(&outline.2),
        target_boundary: outline.3[0].as_bytes(),
        target_black: outline.3[1].as_bytes(),
        source: outline.4,
        wide: outline.5.each_ref().map(PyBytesMethods::as_bytes),
        source_black: window(&outline.6),
    };
    let source_area = area(outline.source).ok_or_else(invalid_input)?;
    if area(part).is_none()
        || [outline.points, outline.band, outline.source_black]
            .iter()
            .any(|&(rect, mask)| area(rect) != Some(mask.len()))
        || outline.target_boundary.len() != outline.points.1.len()
        || outline.target_black.len() != outline.points.1.len()
        || outline.wide.iter().any(|mask| mask.len() != source_area)
        || !valid_points(
            outline
                .points
                .1
                .iter()
                .zip(outline.target_boundary)
                .filter(|&(&point, &boundary)| point != 0 && boundary != 0)
                .count(),
            &outline.coordinates,
        )
    {
        return Err(invalid_input());
    }
    py.detach(|| flow_color::outline_matches(&outline, part))
        .map_err(|error| PyValueError::new_err(error.to_string()))
}

/// 内部採色用の入力を検証し、RGB または採用なしを返す。
#[pyfunction]
pub fn flow_part_color(
    py: Python<'_>,
    interior: InteriorInput<'_>,
    sizes: &[u8],
) -> PyResult<Option<(u8, u8, u8)>> {
    let interior = Interior {
        local: window(&interior.0),
        coordinates: coordinates(&interior.1),
        source: interior.2,
        excluded: interior.3.each_ref().map(PyBytesMethods::as_bytes),
        confirmed: interior.4.as_ref().map(PyBytesMethods::as_bytes),
        values: interior.5.as_bytes(),
        labels: interior.6.as_bytes(),
    };
    let source_area = area(interior.source).ok_or_else(invalid_input)?;
    let labels_len = if interior.coordinates.consistent.len() < 4096 {
        source_area.checked_mul(4)
    } else {
        Some(0)
    };
    if area(interior.local.0) != Some(interior.local.1.len())
        || interior
            .excluded
            .iter()
            .any(|mask| mask.len() != source_area)
        || interior
            .confirmed
            .is_some_and(|mask| mask.len() != source_area)
        || source_area.checked_mul(8) != Some(interior.values.len())
        || labels_len != Some(interior.labels.len())
        || !sizes.len().is_multiple_of(8)
        || !valid_points(
            interior.local.1.iter().filter(|&&value| value != 0).count(),
            &interior.coordinates,
        )
    {
        return Err(invalid_input());
    }
    py.detach(|| flow_color::interior_color(&interior, sizes))
        .map(|rgb| rgb.map(|[r, g, b]| (r, g, b)))
        .map_err(|error| PyValueError::new_err(error.to_string()))
}
