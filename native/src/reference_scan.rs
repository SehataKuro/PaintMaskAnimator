//! 見本との違いチェック（pma_core::reference_scan）の Python 入口。
use pma_core::reference_scan::{self, CheckOutcome, Entry, Progress};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::document::PyCoreDocument;

type PyRgb = (u8, u8, u8);
type PyBounds = (usize, usize, usize, usize);
pub(crate) type PyEntry<'py> = (
    usize,
    usize,
    &'static str,
    Option<PyRgb>,
    usize,
    Option<PyBounds>,
    Option<PyRgb>,
    Option<Bound<'py, PyBytes>>,
);
type PyCheck<'py> = (
    &'static str,
    Option<usize>,
    Vec<PyEntry<'py>>,
    Vec<(PyRgb, usize, usize)>,
);

fn rgb([r, g, b]: [u8; 3]) -> PyRgb {
    (r, g, b)
}

/// 一覧の1件を `(コマ, レイヤー, 種類, 色, 画素数, 矩形, 前後の色, マスク)` にする。
pub(crate) fn py_entry<'py>(py: Python<'py>, entry: Entry) -> PyEntry<'py> {
    let finding = entry.finding;
    (
        entry.frame,
        entry.layer,
        finding.kind.as_str(),
        finding.rgb.map(rgb),
        finding.pixels,
        finding.bounds,
        finding.expected.map(rgb),
        finding.mask.map(|mask| PyBytes::new(py, &mask)),
    )
}

/// 今のセルを見本にレイヤーの全セルを調べる（reference_scan::check_layer）。
/// `(状態, 見本のコマ, 候補の列, 見本だけの色)`。状態は ok・no_reference・too_few・cancelled。
#[pyfunction]
pub(crate) fn reference_check_layer<'py>(
    py: Python<'py>,
    document: PyRef<'_, PyCoreDocument>,
    current_frame: usize,
    layer: usize,
) -> PyResult<PyCheck<'py>> {
    let document = document.document();
    let outcome = py
        .detach(|| {
            reference_scan::check_layer(document, current_frame, layer, &mut Progress::default())
        })
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    Ok(match outcome {
        CheckOutcome::NoReference => ("no_reference", None, Vec::new(), Vec::new()),
        CheckOutcome::TooFewCells => ("too_few", None, Vec::new(), Vec::new()),
        CheckOutcome::Cancelled => ("cancelled", None, Vec::new(), Vec::new()),
        CheckOutcome::Done {
            reference_frame,
            report,
        } => (
            "ok",
            Some(reference_frame),
            report
                .entries
                .into_iter()
                .map(|entry| py_entry(py, entry))
                .collect(),
            report
                .reference_only
                .into_iter()
                .map(|(color, missing, compared)| (rgb(color), missing, compared))
                .collect(),
        ),
    })
}
