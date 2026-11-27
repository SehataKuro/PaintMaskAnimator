//! CLIP STUDIO（.clip）のアニメーションの読み取り。
//!
//! 形式の解釈は clipfile に任せ、ここでは合成に要る値（キャンバスの寸法・タイムライン・
//! レイヤーの行・セルのキー・ラスター）を型のある値で返す。native/src/clip.rs の
//! `ClipAnimationReader` と同じ値を、pyo3 なしで Tauri の画面からも読めるようにする。
use std::{fmt, fs::File, io::BufReader, path::Path};

use clipfile::{Animation, ClipFile, Database, Document, Limits, PixelFormat};

/// 読めなかった理由（文言は clipfile・SQLite のもの）。
#[derive(Debug)]
pub struct ClipError(pub String);

impl fmt::Display for ClipError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ClipError {}

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

/// Layer 表の1行。`values` は列名と値（Python の辞書と同じ名前・並び）。
#[derive(Clone, Debug, PartialEq)]
pub struct ClipLayerRow {
    pub id: i64,
    pub name: String,
    pub children: Vec<i64>,
    pub values: Vec<(&'static str, i64)>,
}

impl ClipLayerRow {
    /// 列の値（ない列は 0、Python の `_row_number` の既定と同じ）。
    pub fn number(&self, name: &str) -> i64 {
        self.values
            .iter()
            .find(|(key, _)| *key == name)
            .map_or(0, |(_, value)| *value)
    }
}

/// セル名の曲線のキー（60Hz の時刻とセル名）。
pub type CelCurve = Vec<(f64, String)>;

/// アニメーションの値。
#[derive(Clone, Debug, PartialEq)]
pub struct ClipMetadata {
    pub width: f64,
    pub height: f64,
    pub fps: f64,
    pub start_frame: f64,
    pub end_frame: f64,
    pub timeline_name: String,
    pub layers: Vec<ClipLayerRow>,
    /// セルを並べるトラック（レイヤーの MainId とセル名の曲線）。
    pub tracks: Vec<(i64, Vec<CelCurve>)>,
}

/// 展開したラスター。`channels` は 4（RGBA）・1（グレー）・2（グレー＋アルファ）。
#[derive(Clone, Debug, PartialEq)]
pub struct ClipRaster {
    pub width: u32,
    pub height: u32,
    pub channels: u8,
    pub alpha_only: bool,
    pub default_white: bool,
    pub pixels: Vec<u8>,
}

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
