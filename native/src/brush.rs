//! ブラシの線（`pma_core::brush`）の薄い Python 入口。Qt の経路との突き合わせに使う。
use crate::image::PyRgbaImage;
use pma_core::brush::{
    line_tool, pressure, raster, segment, shapes, stabilizer::Stabilizer, BrushSettings,
    BrushStroke, FillRule, Mask, MaskColorFilter, Point, PointerSample, Selection,
};
use pma_core::image::{Rect, RgbaImage};
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};

type PyRect = (usize, usize, usize, usize);
type PyUndo<'py> = (PyRect, Bound<'py, PyBytes>, Bound<'py, PyBytes>);
type PySegment = (f64, f64, f64, f64, f64);
type PyFinish<'py> = (PyRgbaImage, Option<PyUndo<'py>>, Vec<(u8, u8, u8)>);

fn core_error(error: pma_core::Error) -> PyErr {
    PyValueError::new_err(error.to_string())
}

fn mask_bytes<'py>(py: Python<'py>, mask: &Mask) -> Bound<'py, PyBytes> {
    let bytes: Vec<u8> = mask.bits().iter().map(|&bit| u8::from(bit)).collect();
    PyBytes::new(py, &bytes)
}

fn rects(rects: Vec<Rect>) -> Vec<PyRect> {
    rects
        .into_iter()
        .map(|rect| (rect.x, rect.y, rect.width, rect.height))
        .collect()
}

fn points(values: &[(f64, f64)]) -> Vec<Point> {
    values.iter().map(|&(x, y)| Point::new(x, y)).collect()
}

fn selection(
    image: &RgbaImage,
    selection_mask: Option<Vec<u8>>,
    selection_polygon: Option<Vec<(f64, f64)>>,
) -> PyResult<Option<Selection>> {
    Ok(match (selection_mask, selection_polygon) {
        (Some(bits), _) => Some(Selection::Mask(
            Mask::from_bits(
                0,
                0,
                image.width(),
                image.height(),
                bits.into_iter().map(|bit| bit != 0).collect(),
            )
            .ok_or_else(|| PyValueError::new_err("selection mask size"))?,
        )),
        (None, Some(polygon)) => Some(Selection::Polygon(points(&polygon))),
        (None, None) => None,
    })
}

fn finish_result<'py>(py: Python<'py>, result: line_tool::ToolResult) -> PyFinish<'py> {
    let undo = result.undo.map(|undo| {
        (
            (undo.rect.x, undo.rect.y, undo.rect.width, undo.rect.height),
            PyBytes::new(py, &undo.before),
            PyBytes::new(py, &undo.after),
        )
    });
    let colors = result
        .colors
        .into_iter()
        .map(|[r, g, b]| (r, g, b))
        .collect();
    (PyRgbaImage::from(result.image), undo, colors)
}

fn line_options(
    start: (f64, f64),
    end: (f64, f64),
    control: Option<(f64, f64)>,
    pen_size: f64,
    shift_line: bool,
    taper_in: Option<(f64, f64)>,
    taper_out: Option<(f64, f64)>,
) -> (raster::Path, line_tool::LineOptions) {
    let path = line_tool::line_path(
        Point::new(start.0, start.1),
        Point::new(end.0, end.1),
        control.map(|(x, y)| Point::new(x, y)),
    );
    let options = line_tool::LineOptions::from_panel(pen_size, shift_line, taper_in, taper_out);
    (path, options)
}

/// 直線ツールの線の画素（確定前の見た目と確定は同じ画素、#117）。
/// `taper_in`・`taper_out` はパネルの (大きさ, カーブ %)、使わなければ None。
#[pyfunction]
#[pyo3(signature = (
    start, end, control, width, height, *, pen_size, shift_line=false, taper_in=None,
    taper_out=None,
))]
#[allow(clippy::too_many_arguments)]
pub fn line_tool_mask<'py>(
    py: Python<'py>,
    start: (f64, f64),
    end: (f64, f64),
    control: Option<(f64, f64)>,
    width: usize,
    height: usize,
    pen_size: f64,
    shift_line: bool,
    taper_in: Option<(f64, f64)>,
    taper_out: Option<(f64, f64)>,
) -> Bound<'py, PyBytes> {
    let (path, options) = line_options(
        start, end, control, pen_size, shift_line, taper_in, taper_out,
    );
    mask_bytes(py, &line_tool::line_mask(&path, &options, width, height))
}

/// 直線ツールを確定する（`commit_line_tool`）。`(画像, (矩形, 前, 後) | None, 塗った色)`。
#[pyfunction]
#[pyo3(signature = (
    layer, start, end, control, *, pen_size, color, shift_line=false, taper_in=None,
    taper_out=None, selection_polygon=None, selection_mask=None,
))]
#[allow(clippy::too_many_arguments)]
pub fn line_tool_commit<'py>(
    py: Python<'py>,
    layer: PyRef<'_, PyRgbaImage>,
    start: (f64, f64),
    end: (f64, f64),
    control: Option<(f64, f64)>,
    pen_size: f64,
    color: (u8, u8, u8),
    shift_line: bool,
    taper_in: Option<(f64, f64)>,
    taper_out: Option<(f64, f64)>,
    selection_polygon: Option<Vec<(f64, f64)>>,
    selection_mask: Option<Vec<u8>>,
) -> PyResult<PyFinish<'py>> {
    let image = layer.share();
    let selection = selection(&image, selection_mask, selection_polygon)?;
    let (path, options) = line_options(
        start, end, control, pen_size, shift_line, taper_in, taper_out,
    );
    let rgb = [color.0, color.1, color.2];
    let overlay = line_tool::line_overlay(&path, &options, rgb, image.width(), image.height());
    let result = line_tool::paint_overlay(&image, &overlay, &[rgb], selection.as_ref())
        .map_err(core_error)?;
    Ok(finish_result(py, result))
}

fn shape(
    start: (f64, f64),
    end: (f64, f64),
    lock_ratio: bool,
    kind: &str,
    corners: usize,
) -> PyResult<Option<raster::Path>> {
    let kind = match kind {
        "ellipse" => line_tool::ShapeKind::Ellipse,
        "polygon" => line_tool::ShapeKind::Polygon(corners),
        _ => {
            return Err(PyValueError::new_err(
                "shape must be 'ellipse' or 'polygon'",
            ))
        }
    };
    let rect = line_tool::shape_rect(
        Point::new(start.0, start.1),
        Point::new(end.0, end.1),
        lock_ratio,
    );
    Ok(line_tool::shape_path(rect, kind))
}

/// 図形ツールの確定前の見た目（#117）：確定で書く色の RGBA（`width × height`）。
/// 書かない画素は透明、消しゴムの画素は白。範囲が空なら None。
#[pyfunction]
#[pyo3(signature = (
    start, end, width, height, *, outline_color, fill_color, shape_type="polygon", corners=4,
    lock_ratio=false, outline_width=2.0, fill_inside=false,
))]
#[allow(clippy::too_many_arguments)]
pub fn shape_tool_preview<'py>(
    py: Python<'py>,
    start: (f64, f64),
    end: (f64, f64),
    width: usize,
    height: usize,
    outline_color: (u8, u8, u8),
    fill_color: (u8, u8, u8),
    shape_type: &str,
    corners: usize,
    lock_ratio: bool,
    outline_width: f64,
    fill_inside: bool,
) -> PyResult<Option<Bound<'py, PyBytes>>> {
    let Some(path) = shape(start, end, lock_ratio, shape_type, corners)? else {
        return Ok(None);
    };
    let fill = fill_inside.then_some([fill_color.0, fill_color.1, fill_color.2]);
    let outline = [outline_color.0, outline_color.1, outline_color.2];
    let overlay =
        line_tool::shape_overlay(&path, outline_width / 2.0, fill, outline, width, height);
    let palette = line_tool::shape_palette(fill, outline);
    Ok(Some(PyBytes::new(py, &overlay.preview_rgba(&palette))))
}

/// 図形ツールを確定する（`commit_shape_tool`）。範囲が空なら None。
/// `outline_color`・`fill_color` は塗る色（色を分けないときは同じ色）。
#[pyfunction]
#[pyo3(signature = (
    layer, start, end, *, outline_color, fill_color, shape_type="polygon", corners=4,
    lock_ratio=false, outline_width=2.0, fill_inside=false, selection_polygon=None,
    selection_mask=None,
))]
#[allow(clippy::too_many_arguments)]
pub fn shape_tool_commit<'py>(
    py: Python<'py>,
    layer: PyRef<'_, PyRgbaImage>,
    start: (f64, f64),
    end: (f64, f64),
    outline_color: (u8, u8, u8),
    fill_color: (u8, u8, u8),
    shape_type: &str,
    corners: usize,
    lock_ratio: bool,
    outline_width: f64,
    fill_inside: bool,
    selection_polygon: Option<Vec<(f64, f64)>>,
    selection_mask: Option<Vec<u8>>,
) -> PyResult<Option<PyFinish<'py>>> {
    let image = layer.share();
    let selection = selection(&image, selection_mask, selection_polygon)?;
    let Some(path) = shape(start, end, lock_ratio, shape_type, corners)? else {
        return Ok(None);
    };
    let fill = fill_inside.then_some([fill_color.0, fill_color.1, fill_color.2]);
    let outline = [outline_color.0, outline_color.1, outline_color.2];
    let overlay = line_tool::shape_overlay(
        &path,
        outline_width / 2.0,
        fill,
        outline,
        image.width(),
        image.height(),
    );
    let palette = line_tool::shape_palette(fill, outline);
    let result = line_tool::paint_overlay(&image, &overlay, &palette, selection.as_ref())
        .map_err(core_error)?;
    Ok(Some(finish_result(py, result)))
}

/// `paint_segment` の画素（`width × height`、行優先の 0/1）。
#[pyfunction]
pub fn brush_segment_mask<'py>(
    py: Python<'py>,
    start: (f64, f64),
    end: (f64, f64),
    pen_width: f64,
    width: usize,
    height: usize,
) -> Bound<'py, PyBytes> {
    let mask = py.detach(|| {
        segment::segment_mask(
            Point::new(start.0, start.1),
            Point::new(end.0, end.1),
            pen_width,
            width,
            height,
        )
    });
    mask_bytes(py, &mask)
}

/// `stamp_mask` の画素。`(x, y, size, 0/1 の並び)` を返す。
#[pyfunction]
pub fn brush_stamp_mask(
    py: Python<'_>,
    center: (f64, f64),
    width: f64,
) -> (i64, i64, usize, Bound<'_, PyBytes>) {
    let mask = segment::stamp_mask(Point::new(center.0, center.1), width);
    (mask.x, mask.y, mask.width, mask_bytes(py, &mask))
}

/// `QPainter::drawPolygon`（ブラシだけ）の画素。
#[pyfunction(signature = (polygon, width, height, winding=false))]
pub fn fill_polygon_mask<'py>(
    py: Python<'py>,
    polygon: Vec<(f64, f64)>,
    width: usize,
    height: usize,
    winding: bool,
) -> Bound<'py, PyBytes> {
    let rule = if winding {
        FillRule::Winding
    } else {
        FillRule::OddEven
    };
    let mask = py.detach(|| shapes::fill_polygon(&points(&polygon), rule, width, height));
    mask_bytes(py, &mask)
}

/// `QPainter::drawPath`（丸い端・丸い角のペン、ブラシなし）の画素。`path` は
/// `[0, x, y]`（moveTo）・`[1, x, y]`（lineTo）・`[2, x1, y1, x2, y2, x, y]`（cubicTo）・
/// `[3]`（closeSubpath）の並び。
#[pyfunction]
pub fn stroke_path_mask<'py>(
    py: Python<'py>,
    path: Vec<Vec<f64>>,
    pen_width: f64,
    width: usize,
    height: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    let path = core_path(&path)?;
    let mask = py.detach(|| shapes::stroke_path(&path, pen_width, width, height));
    Ok(mask_bytes(py, &mask))
}

/// `QPainter::drawPath`（ペンなし、ブラシで塗る）の画素。
#[pyfunction(signature = (path, width, height, winding=false))]
pub fn fill_path_mask<'py>(
    py: Python<'py>,
    path: Vec<Vec<f64>>,
    width: usize,
    height: usize,
    winding: bool,
) -> PyResult<Bound<'py, PyBytes>> {
    let path = core_path(&path)?;
    let rule = if winding {
        FillRule::Winding
    } else {
        FillRule::OddEven
    };
    let mask = py.detach(|| raster::fill_path(&path, rule, width, height));
    Ok(mask_bytes(py, &mask))
}

/// `QPainterPath::addEllipse` の経路を `stroke_path_mask` と同じ形の並びで返す。
#[pyfunction]
pub fn ellipse_path(rect: (f64, f64, f64, f64)) -> Vec<Vec<f64>> {
    python_path(&shapes::ellipse_path(rect))
}

/// 経路を幅 `pen_width` の丸い端・丸い角の線にした輪郭（`QPainterPathStroker::createStroke`
/// と同じ要素の並び）。
#[pyfunction]
pub fn stroke_outline_path(path: Vec<Vec<f64>>, pen_width: f64) -> PyResult<Vec<Vec<f64>>> {
    let path = core_path(&path)?;
    Ok(python_path(&pma_core::brush::stroker::stroke_outline(
        &path, pen_width,
    )))
}

fn core_path(elements: &[Vec<f64>]) -> PyResult<raster::Path> {
    let mut path = raster::Path::new();
    for element in elements {
        let (&kind, values) = element
            .split_first()
            .ok_or_else(|| PyValueError::new_err("empty path element"))?;
        match (kind as i64, values) {
            (0, [x, y]) => path.move_to(Point::new(*x, *y)),
            (1, [x, y]) => path.line_to(Point::new(*x, *y)),
            (2, [x1, y1, x2, y2, x, y]) => path.cubic_to(
                Point::new(*x1, *y1),
                Point::new(*x2, *y2),
                Point::new(*x, *y),
            ),
            (3, []) => path.close(),
            _ => return Err(PyValueError::new_err("bad path element")),
        }
    }
    Ok(path)
}

fn python_path(path: &raster::Path) -> Vec<Vec<f64>> {
    path.elements()
        .iter()
        .map(|element| match *element {
            raster::PathElement::MoveTo(p) => vec![0.0, p.x, p.y],
            raster::PathElement::LineTo(p) => vec![1.0, p.x, p.y],
            raster::PathElement::CubicTo(c1, c2, p) => vec![2.0, c1.x, c1.y, c2.x, c2.y, p.x, p.y],
        })
        .collect()
}

/// `_pressure_bezier_at`。
#[pyfunction]
pub fn pressure_bezier_at(points: Vec<(f64, f64)>, value: f64) -> f64 {
    pressure::pressure_bezier_at(&points, value)
}

/// `_pressure_bezier_segments`。
#[pyfunction]
pub fn pressure_bezier_segments(points: Vec<(f64, f64)>) -> Vec<pressure::CurveSegment> {
    pressure::pressure_bezier_segments(&points)
}

/// 手ぶれ補正に入力の並びを通し、描く線分 `(x1, y1, x2, y2, 筆圧)` を返す。
/// 入力は `("press"|"move"|"tick"|"release", x, y, pressure)`。マウスは筆圧に `None`。
#[pyfunction]
pub fn stabilizer_segments(
    strength: i64,
    zoom: f64,
    events: Vec<(String, f64, f64, Option<f64>)>,
) -> PyResult<Vec<PySegment>> {
    let mut stabilizer = Stabilizer::new(strength, zoom);
    let mut output = Vec::new();
    let mut pen = false;
    for (kind, x, y, value) in events {
        let point = Point::new(x, y);
        let segments = match kind.as_str() {
            "press" => {
                stabilizer.reset(point);
                pen = value.is_some();
                let pressure = value.map_or(1.0, |v| stabilizer.smooth_pressure(v.max(0.001)));
                vec![pma_core::brush::stabilizer::Segment {
                    start: point,
                    end: point,
                    pressure,
                }]
            }
            "move" => {
                let pressure = value.map_or(1.0, |v| stabilizer.smooth_pressure(v.max(0.001)));
                stabilizer.move_to(point, pressure)
            }
            "tick" => stabilizer.tick(),
            "release" => {
                let pressure = if pen {
                    stabilizer.last_pressure().max(0.001)
                } else {
                    1.0
                };
                stabilizer.finish(point, pressure)
            }
            _ => return Err(PyValueError::new_err("unknown event")),
        };
        output.extend(
            segments
                .into_iter()
                .map(|s| (s.start.x, s.start.y, s.end.x, s.end.y, s.pressure)),
        );
    }
    Ok(output)
}

/// 押してから離すまでのブラシ・消しゴムのストローク。
#[pyclass(module = "pma_native", name = "BrushStroke")]
pub struct PyBrushStroke(Option<BrushStroke>);

fn sample(x: f64, y: f64, pressure: Option<f64>, time_ms: f64) -> PointerSample {
    PointerSample {
        x,
        y,
        pressure,
        time_ms,
    }
}

impl PyBrushStroke {
    fn stroke(&mut self) -> PyResult<&mut BrushStroke> {
        self.0
            .as_mut()
            .ok_or_else(|| PyValueError::new_err("stroke is finished"))
    }
}

#[pymethods]
impl PyBrushStroke {
    /// 押した点からストロークを始め、`(ストローク, 画素が変わりうる矩形)` を返す。
    /// `selection_mask` は画像と同じ大きさの 0/1 の並び。
    #[staticmethod]
    #[pyo3(signature = (
        layer, x, y, pressure=None, time_ms=0.0, *, size, color, eraser=false,
        pressure_enabled=false, pressure_min=0.1, pressure_max=1.0, pressure_points=Vec::new(),
        pressure_exponent=1.0, stabilizer=0, zoom=1.0, alpha_lock=false, mask_colors=None,
        mask_background=false, selection_polygon=None, selection_mask=None,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn begin(
        layer: PyRef<'_, PyRgbaImage>,
        x: f64,
        y: f64,
        pressure: Option<f64>,
        time_ms: f64,
        size: f64,
        color: (u8, u8, u8),
        eraser: bool,
        pressure_enabled: bool,
        pressure_min: f64,
        pressure_max: f64,
        pressure_points: Vec<(f64, f64)>,
        pressure_exponent: f64,
        stabilizer: i64,
        zoom: f64,
        alpha_lock: bool,
        mask_colors: Option<Vec<(u8, u8, u8)>>,
        mask_background: bool,
        selection_polygon: Option<Vec<(f64, f64)>>,
        selection_mask: Option<Vec<u8>>,
    ) -> PyResult<(Self, Vec<PyRect>)> {
        let image = layer.share();
        let selection = selection(&image, selection_mask, selection_polygon)?;
        let settings = BrushSettings {
            size,
            color: [color.0, color.1, color.2],
            eraser,
            pressure: pressure::PressureSettings {
                enabled: pressure_enabled,
                minimum: pressure_min,
                maximum: pressure_max,
                points: pressure_points,
                exponent: pressure_exponent,
            },
            stabilizer,
            zoom,
            alpha_lock,
            mask_colors: mask_colors.map(|colors| MaskColorFilter {
                colors: colors.into_iter().map(|(r, g, b)| [r, g, b]).collect(),
                background: mask_background,
            }),
            selection,
        };
        let (stroke, changed) =
            BrushStroke::begin(&image, settings, sample(x, y, pressure, time_ms))
                .map_err(core_error)?;
        Ok((Self(Some(stroke)), rects(changed)))
    }

    /// 動かした点まで描き、画素が変わりうる矩形を返す。
    #[pyo3(signature = (x, y, pressure=None, time_ms=0.0))]
    fn move_to(
        &mut self,
        x: f64,
        y: f64,
        pressure: Option<f64>,
        time_ms: f64,
    ) -> PyResult<Vec<PyRect>> {
        let stroke = self.stroke()?;
        stroke
            .move_to(sample(x, y, pressure, time_ms))
            .map(rects)
            .map_err(core_error)
    }

    /// 手ぶれ補正の追従を 1 回進める。
    fn tick(&mut self) -> PyResult<Vec<PyRect>> {
        self.stroke()?.tick().map(rects).map_err(core_error)
    }

    /// 時刻までの追従をまとめて進める。
    fn advance_time(&mut self, now_ms: f64) -> PyResult<Vec<PyRect>> {
        self.stroke()?
            .advance_time(now_ms)
            .map(rects)
            .map_err(core_error)
    }

    /// 途中の見た目（層にこれまでの線を描いた画像）。
    fn image(&mut self) -> PyResult<PyRgbaImage> {
        Ok(PyRgbaImage::from(self.stroke()?.image().clone()))
    }

    /// これまでに変わりうる範囲。
    #[getter]
    fn dirty_rect(&mut self) -> PyResult<Option<PyRect>> {
        Ok(self
            .stroke()?
            .dirty_rect()
            .map(|rect| (rect.x, rect.y, rect.width, rect.height)))
    }

    /// 離した点まで描いて終える。`(画像, (矩形, 前, 後) | None, 塗った色)` を返す。
    #[pyo3(signature = (x=None, y=None, pressure=None, time_ms=0.0))]
    fn finish<'py>(
        &mut self,
        py: Python<'py>,
        x: Option<f64>,
        y: Option<f64>,
        pressure: Option<f64>,
        time_ms: f64,
    ) -> PyResult<PyFinish<'py>> {
        let stroke = self
            .0
            .take()
            .ok_or_else(|| PyValueError::new_err("stroke is finished"))?;
        let release = match (x, y) {
            (Some(x), Some(y)) => Some(sample(x, y, pressure, time_ms)),
            _ => None,
        };
        let result = stroke.finish(release).map_err(core_error)?;
        Ok(finish_result(
            py,
            line_tool::ToolResult {
                image: result.image,
                undo: result.undo,
                colors: result.colors,
            },
        ))
    }
}
