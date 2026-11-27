//! 画素の選択範囲（pma-core の selection）を Python から呼ぶ薄い入口。
//!
//! 比較テスト（tests/test_core_selection_compare.py）と、将来の画面のための窓口。
//! マスクは 1 画素 1 バイト（0 か 1）、矩形は (x, y, 幅, 高さ) でやり取りする。
//! 画像を書き換える関数は、元の画像を変えずに書き換えた写しを返す。

use pma_core::image::Rect;
use pma_core::selection::{
    self, CombineMode, ContiguousOptions, ContiguousResult, SelectionEdit, SelectionMask,
};
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};

use crate::image::PyRgbaImage;

type RectTuple = (usize, usize, usize, usize);
type Edited<'py> = (PyRgbaImage, RectTuple, Bound<'py, PyBytes>);
type LiftedTuple<'py> = (PyRgbaImage, PyRgbaImage, RectTuple, Bound<'py, PyBytes>);
type CutTuple<'py> = (
    PyRgbaImage,
    RectTuple,
    PyRgbaImage,
    Option<(RectTuple, Bound<'py, PyBytes>)>,
);

fn value_error(error: impl ToString) -> PyErr {
    PyValueError::new_err(error.to_string())
}

fn rect_tuple(rect: Rect) -> RectTuple {
    (rect.x, rect.y, rect.width, rect.height)
}

fn rect_from((x, y, width, height): RectTuple) -> Rect {
    Rect {
        x,
        y,
        width,
        height,
    }
}

fn mask_from(bits: &[u8], width: usize, height: usize) -> PyResult<SelectionMask> {
    SelectionMask::from_bits(width, height, bits).map_err(value_error)
}

fn edited<'py>(
    py: Python<'py>,
    image: pma_core::image::RgbaImage,
    edit: SelectionEdit,
) -> Edited<'py> {
    (
        PyRgbaImage::from(image),
        rect_tuple(edit.rect),
        PyBytes::new(py, &edit.before),
    )
}

/// 1つの選択範囲（多角形かマスク）。
#[pyclass(module = "pma_native", name = "Selection", frozen)]
#[derive(Clone)]
pub(crate) struct PySelection(selection::Selection);

#[pymethods]
impl PySelection {
    /// 多角形の選択。頂点が無ければ None。
    #[staticmethod]
    fn polygon(points: Vec<(f64, f64)>) -> Option<Self> {
        selection::Selection::polygon(points).map(Self)
    }

    /// 矩形選択（ドラッグの始点と終点）。
    #[staticmethod]
    fn rect(start: (f64, f64), end: (f64, f64)) -> Self {
        Self(selection::Selection::rect(start, end))
    }

    /// 整数の矩形の選択。
    #[staticmethod]
    fn from_rect(rect: RectTuple) -> Self {
        Self(selection::Selection::from_rect(rect_from(rect)))
    }

    /// マスクの選択。空なら None。
    #[staticmethod]
    fn from_mask(
        py: Python<'_>,
        mask: &[u8],
        width: usize,
        height: usize,
    ) -> PyResult<Option<Self>> {
        let mask = mask_from(mask, width, height)?;
        Ok(py
            .detach(|| selection::Selection::from_mask(mask))
            .map(Self))
    }

    /// 自動選択の結果を今の選択と足し引きする。mode は "replace"・"add"・"subtract"。
    #[staticmethod]
    fn combine(
        py: Python<'_>,
        current: Option<&Self>,
        region: &[u8],
        width: usize,
        height: usize,
        mode: &str,
    ) -> PyResult<Option<Self>> {
        let mode = match mode {
            "replace" => CombineMode::Replace,
            "add" => CombineMode::Add,
            "subtract" => CombineMode::Subtract,
            _ => return Err(value_error(format!("unknown combine mode: {mode}"))),
        };
        let region = mask_from(region, width, height)?;
        let current = current.map(|selection| &selection.0);
        py.detach(|| selection::Selection::combine(current, &region, mode))
            .map(|selection| selection.map(Self))
            .map_err(value_error)
    }

    /// マスクで持つ選択なら真。
    #[getter]
    fn is_mask(&self) -> bool {
        self.0.is_mask()
    }

    /// 左上を (offset_x, offset_y) にした選択マスク（selection_mask_bool）。
    #[pyo3(signature = (width, height, offset_x=0, offset_y=0))]
    fn mask<'py>(
        &self,
        py: Python<'py>,
        width: usize,
        height: usize,
        offset_x: i64,
        offset_y: i64,
    ) -> Bound<'py, PyBytes> {
        let mask = py.detach(|| self.0.mask(width, height, offset_x, offset_y));
        PyBytes::new(py, mask.bits())
    }

    /// 描画ツールのクリップ（apply_selection_clip）。クリップしないなら None。
    #[pyo3(signature = (width, height, offset_x=0.0, offset_y=0.0))]
    fn clip_mask<'py>(
        &self,
        py: Python<'py>,
        width: usize,
        height: usize,
        offset_x: f64,
        offset_y: f64,
    ) -> Option<Bound<'py, PyBytes>> {
        py.detach(|| self.0.clip_mask(width, height, (offset_x, offset_y)))
            .map(|mask| PyBytes::new(py, mask.bits()))
    }

    /// 外接矩形（selection_bounds）。負の座標もある。
    fn bounds(&self) -> (i64, i64, i64, i64) {
        let rect = self.0.bounds();
        (rect.x, rect.y, rect.width, rect.height)
    }

    /// 点線を引く閉じた折れ線（画像の座標）。
    fn outlines(&self) -> Vec<Vec<(f64, f64)>> {
        self.0.outlines()
    }

    /// area の中で反転する。空になれば None。
    fn inverted(
        &self,
        py: Python<'_>,
        width: usize,
        height: usize,
        area: RectTuple,
    ) -> Option<Self> {
        py.detach(|| self.0.inverted(width, height, rect_from(area)))
            .map(Self)
    }
}

/// 多角形を Qt のアンチエイリアスなしの塗りと同じ画素で二値にする。
#[pyfunction]
#[pyo3(signature = (points, width, height, offset_x=0.0, offset_y=0.0))]
pub(crate) fn selection_polygon_mask<'py>(
    py: Python<'py>,
    points: Vec<(f64, f64)>,
    width: usize,
    height: usize,
    offset_x: f64,
    offset_y: f64,
) -> Bound<'py, PyBytes> {
    let mask =
        py.detach(|| SelectionMask::from_polygon(&points, width, height, (offset_x, offset_y)));
    PyBytes::new(py, mask.bits())
}

/// 二値マスクの輪郭（_mask_contours と同じ順・同じ点）。
#[pyfunction]
pub(crate) fn selection_mask_contours(
    py: Python<'_>,
    mask: &[u8],
    width: usize,
    height: usize,
) -> PyResult<Vec<Vec<(i64, i64)>>> {
    let mask = mask_from(mask, width, height)?;
    Ok(py.detach(|| mask.contours()))
}

/// 面積が最大の輪郭の番号（_largest_contour）。輪郭が無ければ None。
#[pyfunction]
pub(crate) fn selection_largest_contour(contours: Vec<Vec<(i64, i64)>>) -> Option<usize> {
    selection::largest_contour(&contours)
}

/// 不透明で RGB が rgb の画素のマスク（この色を選択範囲にする）。
#[pyfunction]
pub(crate) fn selection_color_mask<'py>(
    py: Python<'py>,
    image: &PyRgbaImage,
    rgb: (u8, u8, u8),
) -> Bound<'py, PyBytes> {
    let image = image.share();
    let mask = py.detach(|| selection::color_mask(&image, [rgb.0, rgb.1, rgb.2]));
    PyBytes::new(py, mask.bits())
}

/// 自動選択の領域（auto_select_region）。("region", マスク)・("out_of_bounds", None)・
/// ("open_to_edge", None) のどれかを返す。
#[pyfunction]
#[pyo3(signature = (image, x, y, adjacent=true, close_gap=None, require_closed=false))]
pub(crate) fn selection_contiguous_region<'py>(
    py: Python<'py>,
    image: &PyRgbaImage,
    x: i64,
    y: i64,
    adjacent: bool,
    close_gap: Option<usize>,
    require_closed: bool,
) -> (&'static str, Option<Bound<'py, PyBytes>>) {
    let image = image.share();
    let options = ContiguousOptions {
        adjacent,
        close_gap,
        require_closed,
    };
    match py.detach(|| selection::contiguous_region(&image, x, y, options)) {
        ContiguousResult::OutOfBounds => ("out_of_bounds", None),
        ContiguousResult::OpenToEdge => ("open_to_edge", None),
        ContiguousResult::Region(mask) => ("region", Some(PyBytes::new(py, mask.bits()))),
    }
}

/// 不透明な画素を囲む矩形の選択（auto_select_used_area）。無ければ None。
#[pyfunction]
pub(crate) fn selection_used_area(py: Python<'_>, image: &PyRgbaImage) -> Option<PySelection> {
    let image = image.share();
    py.detach(|| selection::used_area(&image)).map(PySelection)
}

/// コピー。(矩形, 選択の形に切り抜いた画像) か None。
#[pyfunction]
#[pyo3(signature = (image, selection=None))]
pub(crate) fn selection_copy(
    py: Python<'_>,
    image: &PyRgbaImage,
    selection: Option<&PySelection>,
) -> PyResult<Option<(RectTuple, PyRgbaImage)>> {
    let image = image.share();
    let selection = selection.map(|selection| &selection.0);
    let copied = py
        .detach(|| selection::copy_selection(&image, selection))
        .map_err(value_error)?;
    Ok(copied.map(|(rect, clip)| (rect_tuple(rect), PyRgbaImage::from(clip))))
}

/// 選択の中を消す。(消した画像, 矩形, 消す前の矩形の画素) か None。
#[pyfunction]
pub(crate) fn selection_delete<'py>(
    py: Python<'py>,
    image: &PyRgbaImage,
    selection: &PySelection,
) -> PyResult<Option<Edited<'py>>> {
    let mut image = image.share();
    let edit = py
        .detach(|| selection::delete_selection(&mut image, &selection.0))
        .map_err(value_error)?;
    Ok(edit.map(|edit| edited(py, image, edit)))
}

/// 切り取り。(クリップボードの画像, コピーした矩形, 消した画像, (矩形, 消す前の画素) か None)
/// か None。
#[pyfunction]
#[pyo3(signature = (image, selection=None))]
pub(crate) fn selection_cut<'py>(
    py: Python<'py>,
    image: &PyRgbaImage,
    selection: Option<&PySelection>,
) -> PyResult<Option<CutTuple<'py>>> {
    let mut image = image.share();
    let selection = selection.map(|selection| &selection.0);
    let cut = py
        .detach(|| selection::cut_selection(&mut image, selection))
        .map_err(value_error)?;
    Ok(cut.map(|cut| {
        (
            PyRgbaImage::from(cut.clipboard),
            rect_tuple(cut.rect),
            PyRgbaImage::from(image),
            cut.edit
                .map(|edit| (rect_tuple(edit.rect), PyBytes::new(py, &edit.before))),
        )
    }))
}

/// 貼り付け。(貼った画像, 矩形, 貼る前の矩形の画素) か None。
#[pyfunction]
#[pyo3(signature = (image, clip, selection=None, origin=(0, 0)))]
pub(crate) fn selection_paste<'py>(
    py: Python<'py>,
    image: &PyRgbaImage,
    clip: &PyRgbaImage,
    selection: Option<&PySelection>,
    origin: (i64, i64),
) -> PyResult<Option<Edited<'py>>> {
    let mut image = image.share();
    let clip = clip.share();
    let selection = selection.map(|selection| &selection.0);
    let edit = py
        .detach(|| selection::paste(&mut image, &clip, selection, origin))
        .map_err(value_error)?;
    Ok(edit.map(|edit| edited(py, image, edit)))
}

/// 描画の書き込みを選択でクリップする。(書いた画像, 矩形, 書く前の画素) か None。
#[pyfunction]
#[pyo3(signature = (image, rect, pixels, clip=None))]
pub(crate) fn selection_clip_write<'py>(
    py: Python<'py>,
    image: &PyRgbaImage,
    rect: RectTuple,
    pixels: &[u8],
    clip: Option<&[u8]>,
) -> PyResult<Option<Edited<'py>>> {
    let mut image = image.share();
    let rect = rect_from(rect);
    let clip = clip
        .map(|bits| mask_from(bits, rect.width, rect.height))
        .transpose()?;
    let edit = py
        .detach(|| selection::clip_write(&mut image, rect, pixels, clip.as_ref()))
        .map_err(value_error)?;
    Ok(edit.map(|edit| edited(py, image, edit)))
}

/// 変形で切り出す矩形。切り出せなければ None。
#[pyfunction]
pub(crate) fn selection_transform_source_rect(
    selection: &PySelection,
    width: usize,
    height: usize,
) -> Option<RectTuple> {
    selection::transform_source_rect(&selection.0, width, height).map(rect_tuple)
}

/// 変形の開始。(選択の中を消した画像, 変形の元, 矩形, 消す前の画素) か None。
#[pyfunction]
pub(crate) fn selection_lift<'py>(
    py: Python<'py>,
    image: &PyRgbaImage,
    selection: &PySelection,
) -> PyResult<Option<LiftedTuple<'py>>> {
    let mut image = image.share();
    let lifted = py
        .detach(|| selection::lift_selection(&mut image, &selection.0))
        .map_err(value_error)?;
    Ok(lifted.map(|lifted| {
        (
            PyRgbaImage::from(image),
            PyRgbaImage::from(lifted.source),
            rect_tuple(lifted.edit.rect),
            PyBytes::new(py, &lifted.edit.before),
        )
    }))
}

/// 選択の中を透明にした画像全体の写し（_cleared_selection_base）。
#[pyfunction]
pub(crate) fn selection_cleared(
    py: Python<'_>,
    image: &PyRgbaImage,
    selection: &PySelection,
) -> PyResult<PyRgbaImage> {
    let image = image.share();
    py.detach(|| selection::cleared_selection(&image, &selection.0))
        .map(PyRgbaImage::from)
        .map_err(value_error)
}

/// モジュールに選択範囲の窓口を足す。
pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PySelection>()?;
    module.add_function(wrap_pyfunction!(selection_polygon_mask, module)?)?;
    module.add_function(wrap_pyfunction!(selection_mask_contours, module)?)?;
    module.add_function(wrap_pyfunction!(selection_largest_contour, module)?)?;
    module.add_function(wrap_pyfunction!(selection_color_mask, module)?)?;
    module.add_function(wrap_pyfunction!(selection_contiguous_region, module)?)?;
    module.add_function(wrap_pyfunction!(selection_used_area, module)?)?;
    module.add_function(wrap_pyfunction!(selection_copy, module)?)?;
    module.add_function(wrap_pyfunction!(selection_delete, module)?)?;
    module.add_function(wrap_pyfunction!(selection_cut, module)?)?;
    module.add_function(wrap_pyfunction!(selection_paste, module)?)?;
    module.add_function(wrap_pyfunction!(selection_clip_write, module)?)?;
    module.add_function(wrap_pyfunction!(selection_transform_source_rect, module)?)?;
    module.add_function(wrap_pyfunction!(selection_lift, module)?)?;
    module.add_function(wrap_pyfunction!(selection_cleared, module)?)?;
    Ok(())
}
