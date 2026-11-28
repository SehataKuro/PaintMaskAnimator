//! 変形ツール（pma-core の tools::transform）と、変形を掛けた QPainter::drawImage
//! （compose::painter）を Python から呼ぶ薄い入口。
//!
//! 比較テスト（tests/test_core_transform_compare.py）と、将来の画面のための窓口。
//! 矩形は (x, y, 幅, 高さ)。乗算済みの画素は Qt の ARGB32_Premultiplied と同じ値を
//! R, G, B, A の順に並べる。

use pma_core::compose::painter::{draw_image, Device, RectF, Transform};
use pma_core::compose::PremulImage;
use pma_core::image::Rect;
use pma_core::selection::{Selection, SelectionMask};
use pma_core::tools::transform::{DragKind, Mode, TransformOptions, TransformSession};
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};

use crate::image::PyRgbaImage;

type RectTuple = (usize, usize, usize, usize);
type Point = (f64, f64);

fn value_error(error: impl ToString) -> PyErr {
    PyValueError::new_err(error.to_string())
}

fn rect_tuple(rect: Rect) -> RectTuple {
    (rect.x, rect.y, rect.width, rect.height)
}

fn premul(pixels: &[u8], width: usize, height: usize) -> PyResult<PremulImage> {
    PremulImage::from_premultiplied(width, height, pixels.to_vec()).map_err(value_error)
}

/// 変形を掛けた `QPainter::drawImage(rect, image, source_rect)` を乗算済みの画素で描く。
///
/// `matrix` は `QTransform(m11, m12, m13, m21, m22, m23, m31, m32, m33)` の 9 値。
/// `target` が None なら透明な `width × height` へ描く。
#[pyfunction]
#[pyo3(signature = (image, image_width, image_height, matrix, rect, source_rect, width, height, opacity=1.0, target=None))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn painter_draw_image<'py>(
    py: Python<'py>,
    image: &[u8],
    image_width: usize,
    image_height: usize,
    matrix: [f64; 9],
    rect: (f64, f64, f64, f64),
    source_rect: (f64, f64, f64, f64),
    width: usize,
    height: usize,
    opacity: f64,
    target: Option<&[u8]>,
) -> PyResult<Bound<'py, PyBytes>> {
    let source = premul(image, image_width, image_height)?;
    let mut out = match target {
        Some(pixels) => premul(pixels, width, height)?,
        None => PremulImage::transparent(width, height).map_err(value_error)?,
    };
    let m = matrix;
    let transform = Transform::new(m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8]);
    draw_image(
        &mut Device::new(&mut out),
        &transform,
        RectF::new(rect.0, rect.1, rect.2, rect.3),
        &source,
        RectF::new(source_rect.0, source_rect.1, source_rect.2, source_rect.3),
        opacity,
    );
    Ok(PyBytes::new(py, out.pixels()))
}

fn selection_from(
    polygon: Option<Vec<Point>>,
    mask: Option<(&[u8], usize, usize)>,
) -> PyResult<Option<Selection>> {
    if let Some((bits, width, height)) = mask {
        let mask = SelectionMask::from_bits(width, height, bits).map_err(value_error)?;
        return Ok(Selection::from_mask(mask));
    }
    Ok(polygon.and_then(Selection::polygon))
}

/// 1 回の変形（始めてから確定・取り消しまで）。
#[pyclass(module = "pma_native", name = "TransformSession")]
pub(crate) struct PyTransformSession(Option<TransformSession>);

impl PyTransformSession {
    fn session(&self) -> PyResult<&TransformSession> {
        self.0
            .as_ref()
            .ok_or_else(|| value_error("変形は終わっています"))
    }

    fn session_mut(&mut self) -> PyResult<&mut TransformSession> {
        self.0
            .as_mut()
            .ok_or_else(|| value_error("変形は終わっています"))
    }
}

type Begun<'py> = (
    PyTransformSession,
    PyRgbaImage,
    RectTuple,
    Bound<'py, PyBytes>,
);

#[pymethods]
impl PyTransformSession {
    /// 変形を始める。選択が無ければ描いてある範囲。切り出せなければ None。
    /// 戻り値は (変形, 選択の中を消したレイヤー, 消した矩形, その前の画素)。
    #[staticmethod]
    #[pyo3(signature = (layer, mode, polygon=None, mask=None, quality=false, line_colors=Vec::new(), line_threshold=96, mesh_cols=4, mesh_rows=4))]
    #[allow(clippy::too_many_arguments)]
    fn begin<'py>(
        py: Python<'py>,
        layer: &PyRgbaImage,
        mode: &str,
        polygon: Option<Vec<Point>>,
        mask: Option<(Vec<u8>, usize, usize)>,
        quality: bool,
        line_colors: Vec<[u8; 3]>,
        line_threshold: u8,
        mesh_cols: usize,
        mesh_rows: usize,
    ) -> PyResult<Option<Begun<'py>>> {
        let selection = selection_from(
            polygon,
            mask.as_ref().map(|(b, w, h)| (b.as_slice(), *w, *h)),
        )?;
        let options = TransformOptions {
            quality,
            line_colors,
            line_threshold,
            mesh_cols,
            mesh_rows,
        };
        let begun = TransformSession::begin(
            &layer.share(),
            selection.as_ref(),
            Mode::from_name(mode),
            options,
        )
        .map_err(value_error)?;
        Ok(begun.map(|begun| {
            (
                PyTransformSession(Some(begun.session)),
                PyRgbaImage::from(begun.layer),
                rect_tuple(begun.edit.rect),
                PyBytes::new(py, &begun.edit.before),
            )
        }))
    }

    /// 変形の点。
    fn points(&self) -> PyResult<Vec<Point>> {
        Ok(self.session()?.points().to_vec())
    }

    /// 点をまとめて置き換える。
    fn set_points(&mut self, points: Vec<Point>) -> PyResult<bool> {
        Ok(self.session_mut()?.set_points(points))
    }

    /// メッシュの (横, 縦) の点の数。
    fn mesh_size(&self) -> PyResult<(usize, usize)> {
        Ok(self.session()?.mesh_size())
    }

    /// クオリティ変形で描くか。
    fn quality(&self) -> PyResult<bool> {
        Ok(self.session()?.quality())
    }

    /// 切り出した矩形。
    fn source_rect(&self) -> PyResult<RectTuple> {
        Ok(rect_tuple(self.session()?.source_rect()))
    }

    /// 外周の点。
    fn outer_polygon(&self) -> PyResult<Vec<Point>> {
        Ok(self.session()?.outer_polygon())
    }

    /// 回転のつまみ。
    fn rotation_handle(&self, zoom: f64) -> PyResult<Option<Point>> {
        Ok(self.session()?.rotation_handle(zoom))
    }

    /// 選択の輪郭を今の形へ写す。
    fn transformed_outline(&self, outline: Vec<Point>) -> PyResult<Vec<Point>> {
        Ok(self.session()?.transformed_outline(&outline))
    }

    /// ドラッグを始める。"move"・"rotate"・("handle", 番号)の名前、つかめなければ None。
    fn begin_drag(&mut self, point: Point, zoom: f64) -> PyResult<Option<(String, i64)>> {
        Ok(self
            .session_mut()?
            .begin_drag(point, zoom)
            .map(|kind| match kind {
                DragKind::Move => ("move".to_string(), -1),
                DragKind::Rotate => ("rotate".to_string(), -1),
                DragKind::Handle(index) => ("handle".to_string(), index as i64),
            }))
    }

    /// ドラッグで形を変える。
    fn update_drag(&mut self, point: Point) -> PyResult<()> {
        self.session_mut()?.update_drag(point);
        Ok(())
    }

    /// ドラッグを終える。履歴を積んだら真。
    fn end_drag(&mut self) -> PyResult<bool> {
        Ok(self.session_mut()?.end_drag())
    }

    /// 回す（度）。
    fn rotate(&mut self, degrees: f64) -> PyResult<()> {
        self.session_mut()?.rotate(degrees);
        Ok(())
    }

    /// 反転する。
    fn flip(&mut self, horizontal: bool) -> PyResult<()> {
        self.session_mut()?.flip(horizontal);
        Ok(())
    }

    /// メッシュの格子数を変える。
    fn set_mesh_grid(&mut self, cols: usize, rows: usize) -> PyResult<()> {
        self.session_mut()?.set_mesh_grid(cols, rows);
        Ok(())
    }

    /// 線の太さのしきい値を変える。
    fn set_line_threshold(&mut self, value: u8) -> PyResult<()> {
        self.session_mut()?.set_line_threshold(value);
        Ok(())
    }

    /// 形の履歴を戻す。
    fn undo(&mut self) -> PyResult<bool> {
        Ok(self.session_mut()?.undo())
    }

    /// 形の履歴をやり直す。
    fn redo(&mut self) -> PyResult<bool> {
        Ok(self.session_mut()?.redo())
    }

    /// 戻せる形の数。
    fn history_len(&self) -> PyResult<usize> {
        Ok(self.session()?.history_len())
    }

    /// 今の形で描いた画素。(範囲, 乗算済みの画素)。
    fn preview<'py>(&self, py: Python<'py>) -> PyResult<(RectTuple, Bound<'py, PyBytes>)> {
        let rendered = self.session()?.preview().map_err(value_error)?;
        Ok((
            rect_tuple(rendered.window),
            PyBytes::new(py, rendered.pixels.pixels()),
        ))
    }

    /// `original` に今の形で変形を掛けた画像（すべてのコマに掛けるとき）。
    fn apply_to(&self, original: &PyRgbaImage) -> PyResult<PyRgbaImage> {
        let image = self
            .session()?
            .apply_to(&original.share())
            .map_err(value_error)?;
        Ok(PyRgbaImage::from(image))
    }

    /// クオリティ変形と実線の色を変える。クオリティで描くか。
    fn set_quality(&mut self, quality: bool, line_colors: Vec<[u8; 3]>) -> PyResult<bool> {
        Ok(self.session_mut()?.set_quality(quality, line_colors))
    }

    /// トゥイーンの各コマの画像（`start` は始めたときの点）。点の数が合わなければ None。
    fn tween_images(
        &self,
        start: Vec<Point>,
        exposure: usize,
        reverse: bool,
    ) -> PyResult<Option<Vec<PyRgbaImage>>> {
        let images = self
            .session()?
            .tween_images(&start, exposure, reverse)
            .map_err(value_error)?;
        Ok(images.map(|images| images.into_iter().map(PyRgbaImage::from).collect()))
    }

    /// トゥイーンのキーに持たせる設定の JSON（`tween_groups.make_spec`）。
    fn tween_spec(
        &self,
        id: &str,
        length: usize,
        reverse: bool,
        start: Vec<Point>,
    ) -> PyResult<String> {
        let session = self.session()?;
        let spec = pma_core::tools::transform::tween_spec(
            id,
            length,
            session.mode(),
            reverse,
            &start,
            session.points(),
            session.mesh_size(),
            session.mesh_reference(),
        );
        Ok(spec.to_string())
    }

    /// 確定する。(画像, 範囲, 前の画素, あとの画素)。
    fn commit<'py>(
        &mut self,
        py: Python<'py>,
    ) -> PyResult<(
        PyRgbaImage,
        RectTuple,
        Bound<'py, PyBytes>,
        Bound<'py, PyBytes>,
    )> {
        let session = self
            .0
            .take()
            .ok_or_else(|| value_error("変形は終わっています"))?;
        let committed = session.commit().map_err(value_error)?;
        Ok((
            PyRgbaImage::from(committed.image),
            rect_tuple(committed.undo.rect),
            PyBytes::new(py, &committed.undo.before),
            PyBytes::new(py, &committed.undo.after),
        ))
    }

    /// 取り消す。変形を始める前のレイヤーの画像。
    fn cancel(&mut self) -> PyResult<PyRgbaImage> {
        let session = self
            .0
            .take()
            .ok_or_else(|| value_error("変形は終わっています"))?;
        Ok(PyRgbaImage::from(session.cancel()))
    }
}

/// モジュールへ登録する。
pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(painter_draw_image, module)?)?;
    module.add_class::<PyTransformSession>()?;
    Ok(())
}
