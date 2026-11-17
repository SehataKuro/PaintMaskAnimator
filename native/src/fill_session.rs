//! バケツ状態の Python 入口。計算とキャッシュは core に置く。
use pma_core::fill::region::{Enclosed, FillOptions};
use pma_core::fill::session::{Bounds, BucketState, Category, LocalOutcome, PrepareOptions};
use pma_core::fill::target::TargetColor;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::pybacked::PyBackedBytes;
use pyo3::types::PyBytes;
use std::sync::Mutex;

fn store_mask(data: Vec<u8>) -> PyBackedBytes {
    Python::attach(|py| PyBytes::new(py, &data).extract().expect("bytes storage"))
}

fn share_mask(mask: &PyBackedBytes) -> PyBackedBytes {
    Python::attach(|py| mask.clone_ref(py))
}

type Options = (
    String,
    bool,
    usize,
    bool,
    usize,
    bool,
    bool,
    usize,
    bool,
    bool,
    Vec<[u8; 3]>,
);
fn options(o: Options) -> PyResult<PrepareOptions> {
    let (
        color,
        extend_lines,
        extend_length,
        close_gap,
        gap_width,
        adjacent,
        require_closed,
        island_max_width,
        include_masks,
        include_background_mask,
        mask_colors,
    ) = o;
    let target_color = match color.as_str() {
        "all" => TargetColor::All(None),
        "transparent" => TargetColor::Transparent,
        "surrounded_by_transparent" => TargetColor::SurroundedByTransparent,
        "black" => TargetColor::Black,
        "surrounded_by_black" => TargetColor::SurroundedByBlack,
        "white_transparent" => TargetColor::WhiteTransparent,
        "surrounded_by_white_transparent" => TargetColor::SurroundedByWhiteTransparent,
        "faint_as_transparent" => TargetColor::FaintAsTransparent,
        "opaque_and_inner_transparent" => TargetColor::OpaqueAndInnerTransparent,
        "transparent_and_closed_opaque" => TargetColor::TransparentAndClosedOpaque,
        _ => TargetColor::SurroundedByWhiteTransparent,
    };
    let enclosed = match target_color {
        TargetColor::OpaqueAndInnerTransparent => Enclosed::InnerTransparent,
        TargetColor::TransparentAndClosedOpaque => Enclosed::ClosedOpaque,
        _ => Enclosed::None,
    };
    Ok(PrepareOptions {
        target_color,
        extend_lines,
        extend_length,
        close_gap,
        gap_width,
        fill: FillOptions {
            adjacent,
            require_closed,
            island_max_width,
            include_masks,
            include_background_mask,
            mask_colors,
            enclosed,
        },
    })
}
#[pyclass]
pub struct FillSession {
    state: BucketState<PyBackedBytes, PyBackedBytes>,
}
type Output<'py> = (
    &'static str,
    Option<Bounds>,
    Option<Bound<'py, PyBytes>>,
    Option<(u8, u8, u8)>,
);
fn output(py: Python<'_>, r: LocalOutcome) -> Output<'_> {
    (
        r.reason,
        r.bounds,
        r.region.as_ref().map(|r| PyBytes::new(py, r)),
        r.category.map(|[r, g, b]| (r, g, b)),
    )
}
#[pymethods]
impl FillSession {
    #[new]
    #[pyo3(signature=(rgba,width,height,options,indexed=false))]
    fn new(
        rgba: PyBackedBytes,
        width: usize,
        height: usize,
        options: Options,
        indexed: bool,
    ) -> PyResult<Self> {
        Ok(Self {
            state: BucketState::with_storage(
                rgba,
                width,
                height,
                self::options(options)?,
                indexed,
                store_mask,
                share_mask,
            )
            .map_err(PyValueError::new_err)?,
        })
    }
    fn compute<'py>(&mut self, py: Python<'py>, x: i64, y: i64) -> PyResult<Output<'py>> {
        let (x, y) = coordinates(x, y)?;
        let r = py
            .detach(|| self.state.compute(x, y, None))
            .map_err(PyValueError::new_err)?;
        Ok(output(py, r))
    }
    #[allow(clippy::type_complexity)]
    fn prepared<'py>(
        &mut self,
        py: Python<'py>,
        category: Category,
    ) -> PyResult<(
        Bound<'py, PyBytes>,
        Option<Bound<'py, PyBytes>>,
        Option<Bound<'py, PyBytes>>,
    )> {
        let p = py
            .detach(|| self.state.prepare(category))
            .map_err(PyValueError::new_err)?;
        Ok((
            (&p.target).into_pyobject(py).unwrap(),
            p.passable.as_ref().map(|m| m.into_pyobject(py).unwrap()),
            p.gap.as_ref().map(|m| m.into_pyobject(py).unwrap()),
        ))
    }
    fn index<'py>(
        &mut self,
        py: Python<'py>,
        category: Category,
    ) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
        let index = py
            .detach(|| self.state.index(category))
            .map_err(PyValueError::new_err)?;
        Ok((
            (&index.labels).into_pyobject(py).unwrap(),
            (&index.boxes).into_pyobject(py).unwrap(),
        ))
    }
    fn background<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyBytes> {
        self.state.background().into_pyobject(py).unwrap()
    }
    fn categories(&self) -> Vec<Category> {
        self.state.categories()
    }
    fn nbytes(&self) -> usize {
        self.state.nbytes()
    }
}
fn coordinates(x: i64, y: i64) -> PyResult<(usize, usize)> {
    Ok((
        usize::try_from(x).map_err(|_| PyValueError::new_err("invalid fill coordinates"))?,
        usize::try_from(y).map_err(|_| PyValueError::new_err("invalid fill coordinates"))?,
    ))
}
#[pyclass]
#[derive(Default)]
pub struct LineCache {
    cache: Mutex<pma_core::fill::session::LineCache>,
}
#[pymethods]
impl LineCache {
    #[new]
    fn new() -> Self {
        Self::default()
    }
}
#[pyfunction]
#[pyo3(signature=(rgba,width,height,x,y,options,selection=None,line_cache=None))]
#[allow(clippy::too_many_arguments)]
pub fn compute_fill_region_once<'py>(
    py: Python<'py>,
    rgba: &[u8],
    width: usize,
    height: usize,
    x: i64,
    y: i64,
    options: Options,
    selection: Option<&[u8]>,
    line_cache: Option<PyRef<'py, LineCache>>,
) -> PyResult<Output<'py>> {
    let (x, y) = coordinates(x, y)?;
    let mut state = BucketState::new(rgba, width, height, self::options(options)?, false)
        .map_err(PyValueError::new_err)?;
    let mut cache = line_cache
        .as_ref()
        .map(|c| {
            c.cache
                .lock()
                .map_err(|_| PyValueError::new_err("line cache lock poisoned"))
        })
        .transpose()?;
    if let Some(c) = cache.as_mut() {
        std::mem::swap(&mut state.line_cache, c);
    }
    let r = py.detach(|| state.compute(x, y, selection));
    if let Some(c) = cache.as_mut() {
        std::mem::swap(&mut state.line_cache, c);
    }
    Ok(output(py, r.map_err(PyValueError::new_err)?))
}
