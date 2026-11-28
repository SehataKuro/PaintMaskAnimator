//! Read-only CLIP bridge. Format parsing belongs to clipfile; PMA only adapts
//! its models and the extra layer placement/color columns needed by the compositor.
use std::{fs::File, io::BufReader, path::PathBuf};

use clipfile::{Animation, ClipFile, Database, Document, Limits, PixelFormat};
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyBytes, PyDict, PyList},
};

fn error(e: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(e.to_string())
}

#[pyclass(unsendable)]
pub struct ClipAnimationReader {
    clip: ClipFile<BufReader<File>>,
    database: Database,
    document: Document,
    animation: Animation,
}

#[pymethods]
impl ClipAnimationReader {
    #[new]
    fn new(path: PathBuf, max_dimension: u32, max_raster_bytes: u64) -> PyResult<Self> {
        let limits = Limits::default()
            .with_max_canvas_dimension(max_dimension)
            .with_max_raster_bytes(max_raster_bytes)
            .with_max_animation_bytes(128 * 1024 * 1024)
            .with_max_layer_tree_depth(256);
        let mut clip =
            ClipFile::open_with_limits(BufReader::new(File::open(path).map_err(error)?), limits)
                .map_err(error)?;
        clip.validate().map_err(error)?;
        let database = clip.open_database().map_err(error)?;
        database.quick_check().map_err(error)?;
        clip.validate_external_index(&database).map_err(error)?;
        let document = Document::load(&database, limits).map_err(error)?;
        let animation = clip
            .read_animation(&database, limits)
            .map_err(error)?
            .ok_or_else(|| error("CLIP document has no animation timeline"))?;
        Ok(Self {
            clip,
            database,
            document,
            animation,
        })
    }

    fn metadata<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let result = PyDict::new(py);
        let canvas = self
            .document
            .project()
            .primary_canvas_id()
            .and_then(|id| self.document.canvas(id))
            .or_else(|| self.document.canvases().first())
            .ok_or_else(|| error("CLIP document has no canvas"))?;
        result.set_item("width", canvas.width())?;
        result.set_item("height", canvas.height())?;
        let timeline = self.animation.timeline();
        result.set_item("fps", timeline.frame_rate())?;
        result.set_item("start_frame", timeline.start_frame())?;
        result.set_item("end_frame", timeline.end_frame())?;
        result.set_item("timeline_name", timeline.name().unwrap_or(""))?;
        let layers = PyList::empty(py);
        // These numeric placement/color fields are not in clipfile's typed Layer
        // yet. Use its read-only connection and schema, never a second parser.
        const EXTRA: &[&str] = &[
            "AnimationFolder",
            "LayerOffsetX",
            "LayerOffsetY",
            "LayerRenderOffscrOffsetX",
            "LayerRenderOffscrOffsetY",
            "LayerMaskOffsetX",
            "LayerMaskOffsetY",
            "LayerMaskOffscrOffsetX",
            "LayerMaskOffscrOffsetY",
            "DrawColorEnable",
            "DrawColorMainRed",
            "DrawColorMainGreen",
            "DrawColorMainBlue",
            "DrawColorSubRed",
            "DrawColorSubGreen",
            "DrawColorSubBlue",
            "LayerUsePaletteColor",
            "LayerPaletteRed",
            "LayerPaletteGreen",
            "LayerPaletteBlue",
        ];
        let columns: Vec<_> = EXTRA
            .iter()
            .copied()
            .filter(|name| self.database.schema().has_column("Layer", name))
            .collect();
        let mut statement = self
            .database
            .connection()
            .prepare(&format!(
                "SELECT MainId{} FROM Layer ORDER BY MainId",
                columns
                    .iter()
                    .map(|name| format!(", {name}"))
                    .collect::<String>()
            ))
            .map_err(error)?;
        let mut rows = statement.query([]).map_err(error)?;
        while let Some(row) = rows.next().map_err(error)? {
            let id: i64 = row.get(0).map_err(error)?;
            let layer = self
                .document
                .layer(id)
                .ok_or_else(|| error("missing layer"))?;
            let data = PyDict::new(py);
            data.set_item("MainId", id)?;
            data.set_item("LayerName", layer.name().unwrap_or(""))?;
            let children = self
                .document
                .layer_tree(layer.canvas_id())
                .and_then(|tree| tree.children_of(id))
                .unwrap_or(&[]);
            data.set_item("children", children)?;
            for (name, value) in [
                ("LayerOpacity", layer.opacity()),
                ("LayerComposite", layer.blend_mode().raw()),
                ("LayerClip", layer.clip_flags()),
                ("LayerFolder", layer.folder_flags()),
                ("LayerVisibility", layer.visibility_flags()),
                ("LayerFirstChildIndex", layer.first_child_id().unwrap_or(0)),
                ("LayerNextIndex", layer.next_sibling_id().unwrap_or(0)),
                ("LayerRenderMipmap", layer.render_mipmap_id().unwrap_or(0)),
                ("LayerLayerMaskMipmap", layer.mask_mipmap_id().unwrap_or(0)),
            ] {
                data.set_item(name, value)?;
            }
            for (index, name) in columns.iter().enumerate() {
                let value: Option<i64> = row.get(index + 1).map_err(error)?;
                data.set_item(name, value.unwrap_or(0))?;
            }
            layers.append(data)?;
        }
        result.set_item("layers", layers)?;
        let tracks = PyList::empty(py);
        for track in self.animation.animation_tracks() {
            if track.kind().raw() != 2000 {
                continue;
            }
            let Some(id) = track.layer_id() else {
                continue;
            };
            let curves: Vec<Vec<(f64, &str)>> = track
                .curves()
                .iter()
                .filter(|curve| curve.kind() == "ImageCelName")
                .map(|curve| {
                    curve
                        .keyframes()
                        .iter()
                        .map(|key| (f64::from(key.time_60hz()), key.tag().unwrap_or("")))
                        .collect()
                })
                .collect();
            tracks.append((id, curves))?;
        }
        result.set_item("tracks", tracks)?;
        Ok(result)
    }

    fn raster<'py>(
        &mut self,
        py: Python<'py>,
        mipmap_id: i64,
    ) -> PyResult<(u32, u32, u8, bool, bool, Bound<'py, PyBytes>)> {
        let source = self
            .database
            .raster_source(mipmap_id)
            .map_err(error)?
            .ok_or_else(|| error(format!("missing raster mipmap {mipmap_id}")))?;
        let packing = source.attributes().packing();
        let alpha_only = packing.alpha_channels() == 1 && packing.buffer_channels() == 0;
        let default_white = source.attributes().default_fill() != 0;
        let raster = self
            .clip
            .decode_raster(&self.database, &source)
            .map_err(error)?;
        let channels = match raster.format() {
            PixelFormat::Rgba8 => 4,
            PixelFormat::Gray8 => 1,
            PixelFormat::GrayAlpha8 => 2,
            _ => return Err(error("unsupported CLIP pixel format")),
        };
        Ok((
            raster.width(),
            raster.height(),
            channels,
            alpha_only,
            default_white,
            PyBytes::new(py, raster.pixels()),
        ))
    }
}

// ---------------------------------------------------------------------------
// pma-core の CLIP の読み込み（Tauri の画面と同じ実装、比較テスト用）
// ---------------------------------------------------------------------------

use crate::document::PyCoreDocument;
use crate::image::PyRgbaImage;
use pma_core::import::clip::{self as core_clip, ClipLayerRow, ClipRaster, RasterSource};
use std::collections::HashMap;

/// 行の列名（pma-core の行は &'static str で持つ）。
const ROW_COLUMNS: &[&str] = &[
    "LayerOpacity",
    "LayerComposite",
    "LayerClip",
    "LayerFolder",
    "LayerVisibility",
    "LayerFirstChildIndex",
    "LayerNextIndex",
    "LayerRenderMipmap",
    "LayerLayerMaskMipmap",
    "AnimationFolder",
    "LayerOffsetX",
    "LayerOffsetY",
    "LayerRenderOffscrOffsetX",
    "LayerRenderOffscrOffsetY",
    "LayerMaskOffsetX",
    "LayerMaskOffsetY",
    "LayerMaskOffscrOffsetX",
    "LayerMaskOffscrOffsetY",
    "DrawColorEnable",
    "DrawColorMainRed",
    "DrawColorMainGreen",
    "DrawColorMainBlue",
    "DrawColorSubRed",
    "DrawColorSubGreen",
    "DrawColorSubBlue",
    "LayerUsePaletteColor",
    "LayerPaletteRed",
    "LayerPaletteGreen",
    "LayerPaletteBlue",
];

/// 渡されたラスター（ミップマップの番号 → `reader.raster` の値）。
struct GivenRasters(HashMap<i64, ClipRaster>);

impl RasterSource for GivenRasters {
    fn raster(&mut self, mipmap: i64) -> Result<ClipRaster, core_clip::ClipError> {
        self.0
            .get(&mipmap)
            .cloned()
            .ok_or_else(|| core_clip::ClipError(format!("missing raster mipmap {mipmap}")))
    }
}

type PyRow = (i64, String, Vec<i64>, Vec<(String, i64)>);
type PyRaster = (u32, u32, u8, bool, bool, Vec<u8>);

/// pma-core でセル1枚を合成する（`_CelRenderer.render`）。`layers` は
/// (MainId, LayerName, children, [(列名, 値)])、`rasters` は番号 → `reader.raster` の値。
#[pyfunction]
pub fn clip_render_cel(
    width: usize,
    height: usize,
    layers: Vec<PyRow>,
    rasters: HashMap<i64, PyRaster>,
    cel: i64,
) -> PyResult<PyRgbaImage> {
    let rows: HashMap<i64, ClipLayerRow> = layers
        .into_iter()
        .map(|(id, name, children, values)| {
            let values = values
                .into_iter()
                .filter_map(|(key, value)| {
                    ROW_COLUMNS.iter().find(|c| **c == key).map(|c| (*c, value))
                })
                .collect();
            (
                id,
                ClipLayerRow {
                    id,
                    name,
                    children,
                    values,
                },
            )
        })
        .collect();
    let mut source = GivenRasters(
        rasters
            .into_iter()
            .map(
                |(id, (w, h, channels, alpha_only, default_white, pixels))| {
                    (
                        id,
                        ClipRaster {
                            width: w,
                            height: h,
                            channels,
                            alpha_only,
                            default_white,
                            pixels,
                        },
                    )
                },
            )
            .collect(),
    );
    let row = rows.get(&cel).ok_or_else(|| error("missing cel"))?.clone();
    let image = core_clip_render(&mut source, &rows, width, height, &row).map_err(error)?;
    Ok(PyRgbaImage::from(image))
}

fn core_clip_render(
    source: &mut dyn RasterSource,
    rows: &HashMap<i64, ClipLayerRow>,
    width: usize,
    height: usize,
    cel: &ClipLayerRow,
) -> Result<pma_core::image::RgbaImage, String> {
    let image = pma_core::import::clip_render::CelRenderer::new(source, rows, width, height)
        .render(cel)
        .map_err(|e| e.0)?;
    pma_core::import::image::to_document_image(&image).map_err(|e| e.to_string())
}

type PyAnimationLayer = (String, Vec<(i64, String)>, Vec<(String, PyRgbaImage)>);

/// pma-core で .clip を読む（`read_clip_animation`）。
/// (ファイル名, タイムライン名, 幅, 高さ, fps, 始め, 終わり, [(名前, キー, [(セル名, 絵)])])。
#[pyfunction]
#[allow(clippy::type_complexity)]
pub fn clip_read_animation(
    py: Python<'_>,
    path: PathBuf,
) -> PyResult<(
    String,
    String,
    usize,
    usize,
    f64,
    i64,
    i64,
    Vec<PyAnimationLayer>,
)> {
    let read = py
        .detach(|| pma_clip_import::read_clip_animation(&path, &mut |_, _, _| true))
        .map_err(error)?
        .ok_or_else(|| error("cancelled"))?;
    let layers = read
        .layers
        .into_iter()
        .map(|layer| {
            (
                layer.name,
                layer.keys.into_iter().map(|k| (k.frame, k.tag)).collect(),
                layer
                    .cels
                    .into_iter()
                    .map(|(tag, image)| (tag, PyRgbaImage::from(image)))
                    .collect(),
            )
        })
        .collect();
    Ok((
        read.source_name,
        read.timeline_name,
        read.width,
        read.height,
        read.fps,
        read.start_frame,
        read.end_frame,
        layers,
    ))
}

/// pma-core で .clip の線画を文書の初期状態に合わせる（`import_clip_line_art`）。
/// (差し替えた絵の数, 対応する線画のなかった絵の数)。
#[pyfunction]
pub fn clip_line_art(
    py: Python<'_>,
    mut document: PyRefMut<'_, PyCoreDocument>,
    path: PathBuf,
) -> PyResult<(usize, usize)> {
    let read = py
        .detach(|| pma_clip_import::read_clip_animation(&path, &mut |_, _, _| true))
        .map_err(error)?
        .ok_or_else(|| error("cancelled"))?;
    let doc = document.inner_mut();
    let art = doc.match_clip_line_art(&read).map_err(error)?;
    let missing = art.missing;
    let count = doc.set_line_art_initial_images(art.images).map_err(error)?;
    Ok((count, missing))
}

/// pma-core で .clip から新しい文書を作る（`_replace_document` まで）。`keep` なら
/// 置いた絵を色塗り前の初期状態にする（`initial_state.keep`）。
/// `replaced` は置き換える前の文書の大きさ。(文書, 絵のあるキーの数, 連番に残したセルの数)。
#[pyfunction]
pub fn clip_animation_document(
    py: Python<'_>,
    path: PathBuf,
    keep: bool,
    replaced: (usize, usize),
) -> PyResult<(PyCoreDocument, usize, usize)> {
    py.detach(|| {
        let read = pma_clip_import::read_clip_animation(&path, &mut |_, _, _| true)?
            .ok_or_else(|| core_clip::ClipError("cancelled".into()))?;
        core_clip::check_frames(
            read.width,
            read.height,
            read.start_frame,
            read.end_frame,
            &read.layers,
        )?;
        let mut built = pma_core::document::model::Document::from_animation(
            read.width,
            read.height,
            read.start_frame,
            read.end_frame,
            &read.layers,
            replaced,
        )
        .map_err(|e| core_clip::ClipError(e.to_string()))?;
        if keep {
            built.document.keep_initial_images(None);
        }
        Ok::<_, core_clip::ClipError>(built)
    })
    .map(|built| {
        (
            PyCoreDocument::wrap(built.document),
            built.content_keys,
            built.archived,
        )
    })
    .map_err(error)
}
