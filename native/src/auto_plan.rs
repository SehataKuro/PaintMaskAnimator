//! 自動塗り（pma_core::auto_plan）の Python 入口。
use pma_core::auto_plan::{self, AutoBucketOptions, LayerOutcome};
use pma_core::reference_scan::{Entry, Progress};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::document::PyCoreDocument;
use crate::reference_scan::{py_entry, PyEntry};

type PyRgb = (u8, u8, u8);
type PyWrite<'py> = (
    usize,
    usize,
    (usize, usize, usize, usize),
    Bound<'py, PyBytes>,
);
type PyPaint<'py> = (
    &'static str,
    Vec<PyWrite<'py>>,
    Vec<PyEntry<'py>>,
    Vec<PyRgb>,
    Vec<(usize, usize)>,
    usize,
);

/// レイヤーの絵のあるセルを、`references`（セルの並びの位置）を見本に自動で塗る
/// （auto_plan::paint_layer）。ドキュメントは変えない。
/// `(状態, 書き込み, 確かめたい所, 書いた色, 見本のセル, 塗った回数)`。状態は ok・too_few・cancelled。
/// 書き込みは `(コマ, レイヤー, (x, y, 幅, 高さ), ストレート RGBA)`。
#[pyfunction]
#[pyo3(signature = (
    document, layer, references, *, island_max_width=0, close_gap=false, gap_width=4,
    extend_lines=false, extend_length=150, require_closed=false,
))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn auto_paint_layer<'py>(
    py: Python<'py>,
    document: PyRef<'_, PyCoreDocument>,
    layer: usize,
    references: Vec<usize>,
    island_max_width: usize,
    close_gap: bool,
    gap_width: usize,
    extend_lines: bool,
    extend_length: usize,
    require_closed: bool,
) -> PyResult<PyPaint<'py>> {
    let options = AutoBucketOptions {
        island_max_width,
        close_gap,
        gap_width,
        extend_lines,
        extend_length,
        require_closed,
    };
    let document = document.document();
    let outcome = py
        .detach(|| {
            auto_plan::paint_layer(
                document,
                layer,
                &references,
                &options,
                &mut Progress::default(),
            )
        })
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let empty = |status| (status, Vec::new(), Vec::new(), Vec::new(), Vec::new(), 0);
    Ok(match outcome {
        LayerOutcome::TooFewCells => empty("too_few"),
        LayerOutcome::Cancelled => empty("cancelled"),
        LayerOutcome::Done {
            writes,
            notes,
            written,
            references,
            rounds,
        } => (
            "ok",
            writes
                .into_iter()
                .map(|w| {
                    (
                        w.frame,
                        w.layer,
                        (w.rect.x, w.rect.y, w.rect.width, w.rect.height),
                        PyBytes::new(py, &w.pixels),
                    )
                })
                .collect(),
            notes
                .into_iter()
                .map(|(frame, layer, finding)| {
                    py_entry(
                        py,
                        Entry {
                            frame,
                            layer,
                            finding,
                        },
                    )
                })
                .collect(),
            written.into_iter().map(|[r, g, b]| (r, g, b)).collect(),
            references,
            rounds,
        ),
    })
}
