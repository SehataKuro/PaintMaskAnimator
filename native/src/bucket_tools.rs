//! バケツ・串刺し塗り（pma-core の tools）を Python から呼ぶ薄い入口。
//!
//! 比較テスト（tests/test_core_bucket_tools_compare.py）と、将来の画面のための窓口。
//! 設定は辞書で受け取る。キーは `bucket_fill.BucketOptions` の欄の名前と、塗る色
//! `color`（RGB）・不透明度 `opacity`（pma_native.pyi に書く）。矩形は (x, y, 幅, 高さ)。

use pma_core::image::Rect;
use pma_core::tools::bucket::{
    self, BucketDrag, BucketSettings, DragStep, FillOutcome, PaintSettings, ReferenceSource,
    ReplaceRefusal, Rgb, TargetKind,
};
use pma_core::tools::skewer::{
    self, SkewerApplied, SkewerEvent, SkewerFill, SkewerStart, SkewerStatus,
};
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyBytes, PyDict},
};

use crate::document::PyCoreDocument;

type PyRect = (usize, usize, usize, usize);
/// Python へ返す RGB（[u8; 3] は bytes になるのでタプルにする）。
type PyRgb = (u8, u8, u8);
type Cell = (usize, usize);
/// 色置換の組 (元の RGB, 置き換える RGB, 線の色か)。
type ReplaceOutput = (PyRgb, PyRgb, bool);
/// なぞり塗りの Undo (コマ, レイヤー, 矩形, 押す前の画素)。
type DragUndoOutput<'py> = (usize, usize, PyRect, Bound<'py, PyBytes>);
/// 串刺しの案内: (マスク, RGBA, 推測か)。
type GuideOutput<'py> = (Bound<'py, PyBytes>, (u8, u8, u8, u8), bool);

fn value_error(error: impl ToString) -> PyErr {
    PyValueError::new_err(error.to_string())
}

fn get<'py, T: FromPyObjectOwned<'py>>(
    dict: &Bound<'py, PyDict>,
    key: &str,
) -> PyResult<Option<T>> {
    match dict.get_item(key)? {
        Some(value) if !value.is_none() => Ok(Some(value.extract().map_err(Into::into)?)),
        _ => Ok(None),
    }
}

fn settings(dict: &Bound<'_, PyDict>) -> PyResult<(BucketSettings, PaintSettings)> {
    let d = BucketSettings::default();
    let target = get::<String>(dict, "target_color")?
        .and_then(|name| TargetKind::from_name(&name))
        .unwrap_or_default();
    let reference = get::<String>(dict, "reference")?
        .and_then(|name| ReferenceSource::from_name(&name))
        .unwrap_or_default();
    let bucket = BucketSettings {
        adjacent: get(dict, "adjacent")?.unwrap_or(d.adjacent),
        island_max_width: get(dict, "island_max_width")?.unwrap_or(d.island_max_width),
        include_masks: get(dict, "include_masks")?.unwrap_or(d.include_masks),
        mask_colors: get(dict, "mask_colors")?.unwrap_or_default(),
        include_background_mask: get(dict, "include_background_mask")?
            .unwrap_or(d.include_background_mask),
        close_gap: get(dict, "close_gap")?.unwrap_or(d.close_gap),
        gap_width: get(dict, "gap_width")?.unwrap_or(d.gap_width),
        extend_lines: get(dict, "extend_lines")?.unwrap_or(d.extend_lines),
        extend_length: get(dict, "extend_length")?.unwrap_or(d.extend_length),
        require_closed: get(dict, "require_closed")?.unwrap_or(d.require_closed),
        target,
        reference,
        island_replacements: get(dict, "island_replacements")?.unwrap_or_default(),
    };
    let p = PaintSettings::default();
    let paint = PaintSettings {
        color: get(dict, "color")?.unwrap_or(p.color),
        opacity: get(dict, "opacity")?.unwrap_or(p.opacity),
    };
    Ok((bucket, paint))
}

fn rgb([r, g, b]: Rgb) -> PyRgb {
    (r, g, b)
}

fn rgbs(colors: Vec<Rgb>) -> Vec<PyRgb> {
    colors.into_iter().map(rgb).collect()
}

fn py_rect(rect: Rect) -> PyRect {
    (rect.x, rect.y, rect.width, rect.height)
}

fn selection_bytes(selection: Option<&[u8]>) -> Option<Vec<u8>> {
    selection.map(|s| s.iter().map(|v| u8::from(*v != 0)).collect())
}

/// (塗れなかった理由か None, [(矩形, 書き込んだ RGB の列), ...])。
type PyStep = (Option<&'static str>, Vec<(PyRect, Vec<PyRgb>)>);

fn py_step(step: DragStep) -> PyStep {
    (
        step.failure.map(|reason| reason.as_str()),
        step.edits
            .into_iter()
            .map(|edit| (py_rect(edit.rect), rgbs(edit.written_colors)))
            .collect(),
    )
}

/// 1か所を塗る（bucket::fill_at）。(理由, 書いた矩形か None, 書き込んだ RGB の列)。
#[pyfunction]
#[pyo3(signature = (document, frame, layer, settings, x, y, selection=None))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn bucket_fill_at(
    py: Python<'_>,
    mut document: PyRefMut<'_, PyCoreDocument>,
    frame: usize,
    layer: usize,
    settings: &Bound<'_, PyDict>,
    x: f64,
    y: f64,
    selection: Option<&[u8]>,
) -> PyResult<(&'static str, Option<PyRect>, Vec<PyRgb>)> {
    let (bucket, paint) = self::settings(settings)?;
    let selection = selection_bytes(selection);
    let document = document.document_mut();
    let outcome = py
        .detach(|| {
            bucket::fill_at(
                document,
                frame,
                layer,
                &bucket,
                paint,
                selection.as_deref(),
                (x, y),
            )
        })
        .map_err(value_error)?;
    Ok(match outcome {
        FillOutcome::Filled(edit) => ("ok", Some(py_rect(edit.rect)), rgbs(edit.written_colors)),
        FillOutcome::Skipped(result) => (result.reason.as_str(), None, Vec::new()),
    })
}

/// 判定に使う絵（bucket::reference_pixels）のストレート RGBA。
#[pyfunction]
pub(crate) fn bucket_reference_pixels<'py>(
    py: Python<'py>,
    document: PyRef<'py, PyCoreDocument>,
    frame: usize,
    layer: usize,
    reference: &str,
) -> PyResult<Bound<'py, PyBytes>> {
    let reference = ReferenceSource::from_name(reference)
        .ok_or_else(|| value_error(format!("不明な参照: {reference}")))?;
    let document = document.document();
    let image = bucket::position_image(document, frame, layer).map_err(value_error)?;
    let pixels = bucket::reference_pixels(
        document,
        frame,
        layer,
        reference,
        image.rgba(),
        image.width(),
        image.height(),
    )
    .map_err(value_error)?;
    Ok(PyBytes::new(py, &pixels))
}

/// 色置換の判定（bucket::replace_color_request）。
/// ("ok", (元の RGB, 置き換える RGB, 線の色か)) か (断った理由, None)。
#[pyfunction]
pub(crate) fn bucket_replace_color_request(
    document: PyRef<'_, PyCoreDocument>,
    frame: usize,
    layer: usize,
    color: Rgb,
    x: f64,
    y: f64,
) -> PyResult<(&'static str, Option<ReplaceOutput>)> {
    let result = bucket::replace_color_request(document.document(), frame, layer, color, (x, y))
        .map_err(value_error)?;
    Ok(match result {
        Ok(request) => (
            "ok",
            Some((rgb(request.source), rgb(request.target), request.is_line)),
        ),
        Err(ReplaceRefusal::OutOfBounds) => ("out_of_bounds", None),
        Err(ReplaceRefusal::NoArtwork) => ("no_artwork", None),
        Err(ReplaceRefusal::Transparent) => ("transparent", None),
        Err(ReplaceRefusal::SameColor) => ("same_color", None),
    })
}

/// 串刺し塗りで見るセル（skewer::skewer_cells）。
#[pyfunction]
pub(crate) fn skewer_cells(
    document: PyRef<'_, PyCoreDocument>,
    layer: usize,
) -> PyResult<Vec<Cell>> {
    skewer::skewer_cells(document.document(), layer).map_err(value_error)
}

/// なぞり塗り（bucket::BucketDrag）。
#[pyclass(module = "pma_native", name = "_BucketDrag", unsendable)]
pub(crate) struct PyBucketDrag(Option<BucketDrag>);

#[pymethods]
impl PyBucketDrag {
    /// 押した。押した所がキャンバスの外なら None、そうでなければ (なぞり, 塗った分)。
    #[staticmethod]
    #[pyo3(signature = (document, frame, layer, settings, x, y, selection=None))]
    #[allow(clippy::too_many_arguments)]
    fn begin(
        py: Python<'_>,
        mut document: PyRefMut<'_, PyCoreDocument>,
        frame: usize,
        layer: usize,
        settings: &Bound<'_, PyDict>,
        x: f64,
        y: f64,
        selection: Option<&[u8]>,
    ) -> PyResult<Option<(Self, PyStep)>> {
        let (bucket, paint) = self::settings(settings)?;
        let selection = selection_bytes(selection);
        let document = document.document_mut();
        let begun = py
            .detach(|| BucketDrag::begin(document, frame, layer, bucket, paint, selection, (x, y)))
            .map_err(value_error)?;
        Ok(begun.map(|(drag, step)| (Self(Some(drag)), py_step(step))))
    }

    /// なぞった。
    fn extend(
        &mut self,
        py: Python<'_>,
        mut document: PyRefMut<'_, PyCoreDocument>,
        x: f64,
        y: f64,
    ) -> PyResult<PyStep> {
        let drag = self
            .0
            .as_mut()
            .ok_or_else(|| value_error("なぞり塗りは終わっています"))?;
        let document = document.document_mut();
        let step = py
            .detach(|| drag.extend(document, (x, y)))
            .map_err(value_error)?;
        Ok(py_step(step))
    }

    /// 離した。(コマ, レイヤー, 矩形, 押す前の画素) か None。
    fn finish<'py>(
        &mut self,
        py: Python<'py>,
        document: PyRef<'py, PyCoreDocument>,
    ) -> PyResult<Option<DragUndoOutput<'py>>> {
        let Some(drag) = self.0.take() else {
            return Ok(None);
        };
        let undo = drag.finish(document.document()).map_err(value_error)?;
        Ok(undo.map(|undo| {
            (
                undo.frame,
                undo.layer,
                py_rect(undo.rect),
                PyBytes::new(py, &undo.before),
            )
        }))
    }
}

fn status(status: SkewerStatus) -> (&'static str, Option<Cell>) {
    match status {
        SkewerStatus::Pending(cell) => ("pending", Some(cell)),
        SkewerStatus::Ready => ("ready", None),
    }
}

/// (種類, 値)。種類は invalidated・ignored・show_pending（値はセル）・failed（値は理由）・
/// advanced（値は (pending か ready, セルか None)）。
type PyEvent = (&'static str, Option<Py<PyAny>>);

fn event(py: Python<'_>, event: SkewerEvent) -> PyResult<PyEvent> {
    Ok(match event {
        SkewerEvent::Invalidated => ("invalidated", None),
        SkewerEvent::Ignored => ("ignored", None),
        SkewerEvent::ShowPending(cell) => (
            "show_pending",
            Some(cell.into_pyobject(py)?.into_any().unbind()),
        ),
        SkewerEvent::Failed(reason) => (
            "failed",
            Some(reason.as_str().into_pyobject(py)?.into_any().unbind()),
        ),
        SkewerEvent::Advanced(s) => (
            "advanced",
            Some(status(s).into_pyobject(py)?.into_any().unbind()),
        ),
    })
}

/// 串刺し塗り（skewer::SkewerFill）。
#[pyclass(module = "pma_native", name = "_SkewerFill", unsendable)]
pub(crate) struct PySkewerFill(Option<Box<SkewerFill>>);

impl PySkewerFill {
    fn session(&mut self) -> PyResult<&mut SkewerFill> {
        self.0
            .as_deref_mut()
            .ok_or_else(|| value_error("串刺し塗りは終わっています"))
    }
}

#[pymethods]
impl PySkewerFill {
    /// 塗り始める。(種類, 串刺し塗りか None, 塗れなかった理由か None)。
    /// 種類は ignored・no_artwork・failed・started。
    #[staticmethod]
    #[pyo3(signature = (document, frame, layer, settings, x, y, selection=None))]
    #[allow(clippy::too_many_arguments)]
    fn start(
        py: Python<'_>,
        document: PyRef<'_, PyCoreDocument>,
        frame: usize,
        layer: usize,
        settings: &Bound<'_, PyDict>,
        x: f64,
        y: f64,
        selection: Option<&[u8]>,
    ) -> PyResult<(&'static str, Option<Self>, Option<&'static str>)> {
        let (bucket, paint) = self::settings(settings)?;
        let selection = selection_bytes(selection);
        let document = document.document();
        let start = py
            .detach(|| SkewerFill::start(document, frame, layer, bucket, paint, selection, (x, y)))
            .map_err(value_error)?;
        Ok(match start {
            SkewerStart::Ignored => ("ignored", None, None),
            SkewerStart::NoArtwork => ("no_artwork", None, None),
            SkewerStart::Failed(reason) => ("failed", None, Some(reason.as_str())),
            SkewerStart::Started(session) => ("started", Some(Self(Some(session))), None),
        })
    }

    /// 追跡を進める。("pending", セル) か ("ready", None)。
    fn advance(
        &mut self,
        py: Python<'_>,
        document: PyRef<'_, PyCoreDocument>,
    ) -> PyResult<(&'static str, Option<Cell>)> {
        let session = self.session()?;
        let document = document.document();
        let result = py
            .detach(|| session.advance(document))
            .map_err(value_error)?;
        Ok(status(result))
    }

    /// 指定を待っているセル。
    fn pending(&self) -> Option<Cell> {
        self.0.as_ref().and_then(|s| s.pending())
    }

    /// 推測があるか。
    fn has_guess(&self) -> bool {
        self.0.as_ref().is_some_and(|s| s.has_guess())
    }

    /// 案内 (マスクの bytes, RGBA, 推測か) か None。
    fn guide<'py>(&self, py: Python<'py>) -> Option<GuideOutput<'py>> {
        let guide = self.0.as_ref()?.guide()?;
        let [r, g, b, a] = guide.rgba;
        Some((PyBytes::new(py, guide.mask), (r, g, b, a), guide.is_guess))
    }

    /// 指定待ちのコマで押した。`frame` は表示しているコマ。
    fn resume_click(
        &mut self,
        py: Python<'_>,
        document: PyRef<'_, PyCoreDocument>,
        frame: usize,
        x: f64,
        y: f64,
    ) -> PyResult<PyEvent> {
        let session = self.session()?;
        let result = session
            .resume_click(document.document(), frame, (x, y))
            .map_err(value_error)?;
        event(py, result)
    }

    /// 推測を採用する（Enter）。
    fn accept_guess(
        &mut self,
        py: Python<'_>,
        document: PyRef<'_, PyCoreDocument>,
    ) -> PyResult<PyEvent> {
        let result = self
            .session()?
            .accept_guess(document.document())
            .map_err(value_error)?;
        event(py, result)
    }

    /// そのコマを飛ばす（Esc）。
    fn skip_frame(
        &mut self,
        py: Python<'_>,
        document: PyRef<'_, PyCoreDocument>,
    ) -> PyResult<PyEvent> {
        let result = self
            .session()?
            .skip_frame(document.document())
            .map_err(value_error)?;
        event(py, result)
    }

    /// その向きの残りを飛ばす（Shift+Esc）。
    fn skip_direction(
        &mut self,
        py: Python<'_>,
        document: PyRef<'_, PyCoreDocument>,
    ) -> PyResult<PyEvent> {
        let result = self
            .session()?
            .skip_direction(document.document())
            .map_err(value_error)?;
        event(py, result)
    }

    /// 一度に塗る。("invalidated", None) か ("done", (塗ったセル数, 飛ばしたコマ数,
    /// 書き込んだ RGB, 元のコマ, [(セル, 共有するセル, 矩形), ...]))。
    #[allow(clippy::type_complexity)]
    fn apply(
        &mut self,
        py: Python<'_>,
        mut document: PyRefMut<'_, PyCoreDocument>,
    ) -> PyResult<(
        &'static str,
        Option<(
            usize,
            usize,
            Vec<PyRgb>,
            usize,
            Vec<(Cell, Vec<Cell>, PyRect)>,
        )>,
    )> {
        let session = self
            .0
            .take()
            .ok_or_else(|| value_error("串刺し塗りは終わっています"))?;
        let document = document.document_mut();
        let applied = py.detach(|| session.apply(document)).map_err(value_error)?;
        Ok(match applied {
            SkewerApplied::Invalidated => ("invalidated", None),
            SkewerApplied::Done {
                writes,
                changed_cells,
                skipped,
                written_colors,
                origin_frame,
                ..
            } => (
                "done",
                Some((
                    changed_cells,
                    skipped,
                    rgbs(written_colors),
                    origin_frame,
                    writes
                        .into_iter()
                        .map(|w| (w.cell, w.shared, py_rect(w.rect)))
                        .collect(),
                )),
            ),
        })
    }
}
