//! オニオンのずらし・回転・拡大を掛けた描画と外周の枠（pma-core の compose::onion）、
//! ドラッグで合わせる操作とクリックしたコマの判定（tools::onion）を Python から呼ぶ
//! 薄い入口。
//!
//! 比較テスト（tests/test_core_onion_transform_compare.py）と、将来の画面のための窓口。
//! オニオンの設定は compose_onion_draws と同じ辞書。画面の表示は
//! (パン x, パン y, 回転, 表示倍率)。行列は QTransform の 9 値。

use pma_core::compose::onion::{draw_onion, draw_onion_outline, OnionSettings, OnionSide};
use pma_core::compose::painter::{Device, RectF, Transform};
use pma_core::compose::PremulImage;
use pma_core::tools::onion::{
    onion_frame_at, onion_source_point, set_canvas_view_rotation, Axis, CanvasView,
    OnionInteraction, Operation,
};
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyBytes, PyDict},
};

use crate::compose::{onion_settings, onion_side};
use crate::document::PyCoreDocument;

type View = (f64, f64, f64, f64);
type Rect4 = (f64, f64, f64, f64);
type SideValues = (f64, f64, f64, f64);
/// (前の (ずらし x, ずらし y, 回転, 拡大 %), 後ろの同じ値, TU／TB の拡大率)。
type OnionValues = (SideValues, SideValues, f64);

fn value_error(error: impl ToString) -> PyErr {
    PyValueError::new_err(error.to_string())
}

fn matrix(m: [f64; 9]) -> Transform {
    Transform::new(m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8])
}

fn rect(r: Rect4) -> RectF {
    RectF::new(r.0, r.1, r.2, r.3)
}

fn canvas_view(view: View) -> CanvasView {
    CanvasView {
        pan: (view.0, view.1),
        rotation: view.2,
        zoom: view.3,
    }
}

fn view_tuple(view: &CanvasView) -> View {
    (view.pan.0, view.pan.1, view.rotation, view.zoom)
}

fn side_values(side: &OnionSide) -> SideValues {
    (side.shift_x, side.shift_y, side.rotation, side.scale)
}

fn onion_values(settings: &OnionSettings) -> OnionValues {
    (
        side_values(&settings.previous),
        side_values(&settings.next),
        settings.tu_tb_scale,
    )
}

fn side_from(side: &Bound<'_, PyDict>) -> PyResult<OnionSide> {
    let color = OnionSide::new((255, 92, 92)).color;
    onion_side(Some(side.clone()), color)
}

fn premul(pixels: &[u8], width: usize, height: usize) -> PyResult<PremulImage> {
    PremulImage::from_premultiplied(width, height, pixels.to_vec()).map_err(value_error)
}

/// 乗算済みの `target` へ、オニオンの画像を変形して重ねる（_draw_onion_range の 1 枚）。
#[pyfunction]
#[pyo3(signature = (target, width, height, base, work_rect, side, zoom, image, image_width, image_height, opacity))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn onion_draw<'py>(
    py: Python<'py>,
    target: &[u8],
    width: usize,
    height: usize,
    base: [f64; 9],
    work_rect: Rect4,
    side: &Bound<'py, PyDict>,
    zoom: f64,
    image: &[u8],
    image_width: usize,
    image_height: usize,
    opacity: f64,
) -> PyResult<Bound<'py, PyBytes>> {
    let side = side_from(side)?;
    let mut out = premul(target, width, height)?;
    let onion = premul(image, image_width, image_height)?;
    draw_onion(
        &mut Device::new(&mut out),
        &matrix(base),
        rect(work_rect),
        &side,
        zoom,
        &onion,
        opacity,
    );
    Ok(PyBytes::new(py, out.pixels()))
}

/// 乗算済みの `target` へ、オニオンの外周の枠を描く（_draw_onion_range の drawRect）。
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub(crate) fn onion_draw_outline<'py>(
    py: Python<'py>,
    target: &[u8],
    width: usize,
    height: usize,
    base: [f64; 9],
    work_rect: Rect4,
    canvas_rect: Rect4,
    side: &Bound<'py, PyDict>,
    zoom: f64,
) -> PyResult<Bound<'py, PyBytes>> {
    let side = side_from(side)?;
    let mut out = premul(target, width, height)?;
    draw_onion_outline(
        &mut Device::new(&mut out),
        &matrix(base),
        rect(work_rect),
        rect(canvas_rect),
        &side,
        zoom,
    );
    Ok(PyBytes::new(py, out.pixels()))
}

/// キャンバス上の点に重なって見える、オニオンの絵の中の位置（_onion_source_point）。
#[pyfunction]
pub(crate) fn onion_source_position(
    point: (f64, f64),
    side: &Bound<'_, PyDict>,
    size: (usize, usize),
) -> PyResult<(f64, f64)> {
    Ok(onion_source_point(point, &side_from(side)?, size))
}

/// クリックした点に見えているオニオンの (コマ, レイヤー)（onion_frame_at）。
#[pyfunction]
pub(crate) fn onion_frame_at_point(
    document: PyRef<'_, PyCoreDocument>,
    point: (f64, f64),
    current: usize,
    active: usize,
    onion_skin: bool,
    onion: &Bound<'_, PyDict>,
) -> PyResult<Option<(usize, usize)>> {
    let settings = onion_settings(onion)?;
    onion_frame_at(
        document.document(),
        point,
        current,
        active,
        onion_skin,
        &settings,
    )
    .map_err(value_error)
}

/// 表示位置の回転を変え、前後のオニオンを補正する（set_onion_canvas_view_rotation）。
/// (変えたか, 表示, オニオンの値)。
#[pyfunction]
pub(crate) fn onion_set_view_rotation(
    value: f64,
    view: View,
    onion: &Bound<'_, PyDict>,
) -> PyResult<(bool, View, OnionValues)> {
    let mut view = canvas_view(view);
    let mut settings = onion_settings(onion)?;
    let changed = set_canvas_view_rotation(value, &mut view, &mut settings);
    Ok((changed, view_tuple(&view), onion_values(&settings)))
}

/// オニオンを合わせる操作 1 回。
#[pyclass(module = "pma_native", name = "OnionInteraction")]
pub(crate) struct PyOnionInteraction(Option<OnionInteraction>);

impl PyOnionInteraction {
    fn inner(&self) -> PyResult<&OnionInteraction> {
        self.0
            .as_ref()
            .ok_or_else(|| value_error("操作は終わっています"))
    }
}

#[pymethods]
impl PyOnionInteraction {
    /// 前（負）・後ろのオニオンのずらしを "x"・"y"・"xy" 方向へ合わせる。
    #[staticmethod]
    #[pyo3(signature = (direction, axis="x"))]
    fn shift(direction: i32, axis: &str) -> Self {
        Self(Some(OnionInteraction::shift(
            direction,
            Axis::from_name(axis),
        )))
    }

    /// キャンバスの表示位置を動かす・回す。
    #[staticmethod]
    fn canvas() -> Self {
        Self(Some(OnionInteraction::canvas()))
    }

    /// ドラッグ中か。
    fn dragging(&self) -> PyResult<bool> {
        Ok(self.inner()?.dragging())
    }

    /// ドラッグ中の動き（"move"・"rotate"、ドラッグ中でなければ None）。
    fn operation(&self) -> PyResult<Option<&'static str>> {
        Ok(self.inner()?.operation().map(|operation| match operation {
            Operation::Move => "move",
            Operation::Rotate => "rotate",
        }))
    }

    /// ドラッグを始める。`rotate` は右ボタンか Shift。
    fn begin_drag(
        &mut self,
        widget: (f64, f64),
        rotate: bool,
        view: View,
        onion: &Bound<'_, PyDict>,
    ) -> PyResult<()> {
        let settings = onion_settings(onion)?;
        self.0
            .as_mut()
            .ok_or_else(|| value_error("操作は終わっています"))?
            .begin_drag(widget, rotate, &canvas_view(view), &settings);
        Ok(())
    }

    /// ドラッグで値を変える。(表示, オニオンの値, 表示が変わったか, オニオンが変わったか)。
    fn update_drag(
        &self,
        widget: (f64, f64),
        view: View,
        onion: &Bound<'_, PyDict>,
    ) -> PyResult<(View, OnionValues, bool, bool)> {
        let mut view = canvas_view(view);
        let mut settings = onion_settings(onion)?;
        let changed = self.inner()?.update_drag(widget, &mut view, &mut settings);
        Ok((
            view_tuple(&view),
            onion_values(&settings),
            changed.view,
            changed.onion,
        ))
    }

    /// 操作を終える。`restore` なら始めたときへ戻す。(戻したか, 表示, オニオンの値)。
    fn cancel(
        &mut self,
        restore: bool,
        view: View,
        onion: &Bound<'_, PyDict>,
    ) -> PyResult<(bool, View, OnionValues)> {
        let interaction = self
            .0
            .take()
            .ok_or_else(|| value_error("操作は終わっています"))?;
        let mut view = canvas_view(view);
        let mut settings = onion_settings(onion)?;
        let restored = interaction.cancel(restore, &mut view, &mut settings);
        Ok((restored, view_tuple(&view), onion_values(&settings)))
    }
}

/// モジュールへ登録する。
pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(onion_draw, module)?)?;
    module.add_function(wrap_pyfunction!(onion_draw_outline, module)?)?;
    module.add_function(wrap_pyfunction!(onion_source_position, module)?)?;
    module.add_function(wrap_pyfunction!(onion_frame_at_point, module)?)?;
    module.add_function(wrap_pyfunction!(onion_set_view_rotation, module)?)?;
    module.add_class::<PyOnionInteraction>()?;
    Ok(())
}
