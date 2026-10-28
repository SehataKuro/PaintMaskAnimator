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
