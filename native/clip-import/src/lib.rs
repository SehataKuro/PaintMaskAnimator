//! CLIP STUDIO（.clip）のアニメーションの読み取り。
//!
//! 形式の解釈は clipfile に任せ、ここでは合成に要る値（キャンバスの寸法・タイムライン・
//! レイヤーの行・セルのキー・ラスター）を型のある値で返す。native/src/clip.rs の
//! `ClipAnimationReader` と同じ値を、pyo3 なしで Tauri の画面からも読めるようにする。
//! 合成と読み込みの手順（Qt 版 `read_clip_animation`）は pma-core の `import::clip`。
use std::{fmt, fs::File, io::BufReader, path::Path};

use clipfile::{Animation, ClipFile, Database, Document, Limits, PixelFormat};
use pma_core::import::clip::{read_animation, Progress};
use pma_core::import::image::MAX_IMAGE_DIMENSION;
use pma_core::project::archive::MAX_PROJECT_IMAGE_BYTES;

pub use pma_core::import::clip::{
    CelCurve, ClipAnimation, ClipError, ClipLayerRow, ClipMetadata, ClipRaster, RasterSource,
};

fn error(e: impl fmt::Display) -> ClipError {
    ClipError(e.to_string())
}

/// clipfile の型付きの Layer にまだない、配置と色の列（あるものだけ読む）。
pub const EXTRA_COLUMNS: &[&str] = &[
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

/// 開いた .clip。
pub struct ClipReader {
    clip: ClipFile<BufReader<File>>,
    database: Database,
    document: Document,
    animation: Animation,
}

impl ClipReader {
    /// 開いて検査する（タイムラインがなければ失敗）。
    pub fn open(path: &Path, max_dimension: u32, max_raster_bytes: u64) -> Result<Self, ClipError> {
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

    /// キャンバス・タイムライン・レイヤー・セルのトラック。
    pub fn metadata(&self) -> Result<ClipMetadata, ClipError> {
        let canvas = self
            .document
            .project()
            .primary_canvas_id()
            .and_then(|id| self.document.canvas(id))
            .or_else(|| self.document.canvases().first())
            .ok_or_else(|| error("CLIP document has no canvas"))?;
        let timeline = self.animation.timeline();
        let columns: Vec<&'static str> = EXTRA_COLUMNS
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
        let mut layers = Vec::new();
        while let Some(row) = rows.next().map_err(error)? {
            let id: i64 = row.get(0).map_err(error)?;
            let layer = self
                .document
                .layer(id)
                .ok_or_else(|| error("missing layer"))?;
            let children = self
                .document
                .layer_tree(layer.canvas_id())
                .and_then(|tree| tree.children_of(id))
                .unwrap_or(&[])
                .to_vec();
            let mut values: Vec<(&'static str, i64)> = vec![
                ("LayerOpacity", layer.opacity()),
                ("LayerComposite", layer.blend_mode().raw()),
                ("LayerClip", layer.clip_flags()),
                ("LayerFolder", layer.folder_flags()),
                ("LayerVisibility", layer.visibility_flags()),
                ("LayerFirstChildIndex", layer.first_child_id().unwrap_or(0)),
                ("LayerNextIndex", layer.next_sibling_id().unwrap_or(0)),
                ("LayerRenderMipmap", layer.render_mipmap_id().unwrap_or(0)),
                ("LayerLayerMaskMipmap", layer.mask_mipmap_id().unwrap_or(0)),
            ];
            for (index, name) in columns.iter().enumerate() {
                let value: Option<i64> = row.get(index + 1).map_err(error)?;
                values.push((name, value.unwrap_or(0)));
            }
            layers.push(ClipLayerRow {
                id,
                name: layer.name().unwrap_or("").to_owned(),
                children,
                values,
            });
        }
        let mut tracks = Vec::new();
        for track in self.animation.animation_tracks() {
            if track.kind().raw() != 2000 {
                continue;
            }
            let Some(id) = track.layer_id() else {
                continue;
            };
            let curves = track
                .curves()
                .iter()
                .filter(|curve| curve.kind() == "ImageCelName")
                .map(|curve| {
                    curve
                        .keyframes()
                        .iter()
                        .map(|key| {
                            (
                                f64::from(key.time_60hz()),
                                key.tag().unwrap_or("").to_owned(),
                            )
                        })
                        .collect()
                })
                .collect();
            tracks.push((id, curves));
        }
        Ok(ClipMetadata {
            width: canvas.width(),
            height: canvas.height(),
            fps: timeline.frame_rate(),
            start_frame: timeline.start_frame(),
            end_frame: timeline.end_frame(),
            timeline_name: timeline.name().unwrap_or("").to_owned(),
            layers,
            tracks,
        })
    }

    /// ミップマップのラスターを展開する。
    pub fn raster(&mut self, mipmap_id: i64) -> Result<ClipRaster, ClipError> {
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
        Ok(ClipRaster {
            width: raster.width(),
            height: raster.height(),
            channels,
            alpha_only,
            default_white,
            pixels: raster.pixels().to_vec(),
        })
    }
}

impl RasterSource for ClipReader {
    fn raster(&mut self, mipmap_id: i64) -> Result<ClipRaster, ClipError> {
        ClipReader::raster(self, mipmap_id)
    }
}

/// .clip を読み、アニメーションフォルダーごとのセルの絵とキーにする（Qt 版
/// `read_clip_animation`。元のファイルは変えない）。中止したら Ok(None)。
pub fn read_clip_animation(
    path: &Path,
    progress: &mut Progress<'_>,
) -> Result<Option<ClipAnimation>, ClipError> {
    let is_clip = path
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| e.eq_ignore_ascii_case("clip"));
    if !is_clip {
        return Err(ClipError(
            "CLIP STUDIOの.clipファイルを指定してください。".into(),
        ));
    }
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    if !progress(
        0,
        0,
        format!("CLIP STUDIOアニメーションを解析しています：{name}"),
    ) {
        return Ok(None);
    }
    let mut reader = ClipReader::open(path, MAX_IMAGE_DIMENSION as u32, MAX_PROJECT_IMAGE_BYTES)?;
    let metadata = reader.metadata()?;
    read_animation(&name, &metadata, &mut reader, progress)
}
