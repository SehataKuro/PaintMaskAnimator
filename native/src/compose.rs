//! キャンバスのレイヤー合成（pma-core の compose）を Python から呼ぶ薄い入口。
//!
//! 比較テスト（tests/test_core_compose_compare.py）と、将来の画面のための窓口。
//! 画素は既定で乗算済みの RGBA（R, G, B, A の順）で返し、`straight=True` なら
//! ストレートに戻して返す。設定は辞書で受け取る（キーは pma_native.pyi に書く）。

use std::collections::BTreeMap;

use pma_core::compose::{
    self, Background, Channel, DisplayOptions, DisplayTarget, LayerImage, MapColorMapping,
    OnionSettings, OnionSide, PremulImage, ShrinkMode, ViewOptions, ViewPreview,
};
use pma_core::display::Rgb;
use pma_core::image::Rect;
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyBytes, PyDict},
};

use crate::document::PyCoreDocument;

type Rendered<'py> = (usize, usize, Bound<'py, PyBytes>);
type RenderedLayer<'py> = (
    usize,
    usize,
    f64,
    usize,
    usize,
    usize,
    usize,
    Bound<'py, PyBytes>,
);

fn value_error(error: impl ToString) -> PyErr {
    PyValueError::new_err(error.to_string())
}

fn get<'py, T: FromPyObjectOwned<'py>>(
    dict: Option<&Bound<'py, PyDict>>,
    key: &str,
) -> PyResult<Option<T>> {
    match dict {
        Some(dict) => match dict.get_item(key)? {
            Some(value) if !value.is_none() => Ok(Some(value.extract().map_err(Into::into)?)),
            _ => Ok(None),
        },
        None => Ok(None),
    }
}

fn display_options(options: Option<&Bound<'_, PyDict>>) -> PyResult<DisplayOptions> {
    Ok(DisplayOptions {
        active_layer: get(options, "active_layer")?,
        visible_colors: get(options, "visible_colors")?,
        color_remap: get(options, "color_remap")?.unwrap_or_default(),
        silhouette: get(options, "silhouette")?.unwrap_or(false),
    })
}

fn channel(name: &str) -> PyResult<Channel> {
    Ok(match name {
        "rgb" => Channel::Rgb,
        "red" => Channel::Red,
        "green" => Channel::Green,
        "blue" => Channel::Blue,
        "alpha" => Channel::Alpha,
        _ => return Err(value_error(format!("不明なチャンネル: {name}"))),
    })
}

fn view_options(view: Option<&Bound<'_, PyDict>>) -> PyResult<ViewOptions> {
    let defaults = ViewPreview::default();
    Ok(ViewOptions {
        show_initial_state: get(view, "show_initial_state")?.unwrap_or(false),
        preview: ViewPreview {
            exposure: get(view, "exposure")?.unwrap_or(0.0),
            channel: channel(&get::<String>(view, "channel")?.unwrap_or("rgb".into()))?,
            map_colors: get(view, "map_colors")?.unwrap_or(false),
            palette: get(view, "palette")?.unwrap_or(defaults.palette),
        },
    })
}

pub(crate) fn onion_side(side: Option<Bound<'_, PyDict>>, color: Rgb) -> PyResult<OnionSide> {
    let side = side.as_ref();
    let defaults = OnionSide::new(color);
    Ok(OnionSide {
        count: get(side, "count")?.unwrap_or(defaults.count),
        opacity: get(side, "opacity")?.unwrap_or(defaults.opacity),
        color: get(side, "color")?.unwrap_or(defaults.color),
        color_enabled: get(side, "color_enabled")?.unwrap_or(defaults.color_enabled),
        levels: get(side, "levels")?.unwrap_or(defaults.levels),
        shift_x: get(side, "shift_x")?.unwrap_or(0.0),
        shift_y: get(side, "shift_y")?.unwrap_or(0.0),
        rotation: get(side, "rotation")?.unwrap_or(0.0),
        scale: get(side, "scale")?.unwrap_or(100.0),
    })
}

pub(crate) fn onion_settings(onion: &Bound<'_, PyDict>) -> PyResult<OnionSettings> {
    let defaults = OnionSettings::default();
    let onion = Some(onion);
    Ok(OnionSettings {
        previous: onion_side(get(onion, "previous")?, defaults.previous.color)?,
        next: onion_side(get(onion, "next")?, defaults.next.color)?,
        all_layers: get(onion, "all_layers")?.unwrap_or(defaults.all_layers),
        selected_colors_only: get(onion, "selected_colors_only")?.unwrap_or(false),
        selected_colors: get(onion, "selected_colors")?.unwrap_or_default(),
        tu_tb_scale: get(onion, "tu_tb_scale")?.unwrap_or(1.0),
    })
}

fn background(value: Option<&Bound<'_, PyAny>>) -> PyResult<Background> {
    let Some(value) = value else {
        return Ok(Background::Transparent);
    };
    if let Ok(name) = value.extract::<String>() {
        return match name.as_str() {
            "transparent" => Ok(Background::Transparent),
            "white" => Ok(Background::White),
            _ => Err(value_error(format!("不明な下地: {name}"))),
        };
    }
    let (r, g, b): (u8, u8, u8) = value.extract()?;
    Ok(Background::Color([r, g, b]))
}

fn shrink_mode(name: &str) -> PyResult<ShrinkMode> {
    match name {
        "nearest" => Ok(ShrinkMode::Nearest),
        "keep_lines" => Ok(ShrinkMode::KeepLines),
        _ => Err(value_error(format!("不明な縮小のしかた: {name}"))),
    }
}

fn rect(value: Option<(usize, usize, usize, usize)>) -> Option<Rect> {
    value.map(|(x, y, width, height)| Rect {
        x,
        y,
        width,
        height,
    })
}

fn read_mapping(mapping: Option<&Bound<'_, PyDict>>) -> PyResult<MapColorMapping> {
    match mapping {
        Some(mapping) => mapping.extract::<BTreeMap<u32, usize>>(),
        None => Ok(MapColorMapping::new()),
    }
}

/// 対応表に増えた分を Python の辞書へ書き戻す（キャンバスの対応表と同じく使い回せる）。
fn write_mapping(target: Option<&Bound<'_, PyDict>>, mapping: &MapColorMapping) -> PyResult<()> {
    if let Some(target) = target {
        for (code, index) in mapping {
            target.set_item(code, index)?;
        }
    }
    Ok(())
}

fn rendered<'py>(py: Python<'py>, image: PremulImage, straight: bool) -> Rendered<'py> {
    let (width, height) = (image.width(), image.height());
    let bytes = if straight {
        PyBytes::new(py, &image.to_straight_bytes())
    } else {
        PyBytes::new(py, image.pixels())
    };
    (width, height, bytes)
}

fn rendered_layer<'py>(py: Python<'py>, layer: LayerImage, straight: bool) -> RenderedLayer<'py> {
    let (layer_index, key_frame, opacity, x, y) = (
        layer.layer,
        layer.key_frame,
        layer.opacity,
        layer.x,
        layer.y,
    );
    let (width, height, bytes) = rendered(py, layer.image, straight);
    (layer_index, key_frame, opacity, x, y, width, height, bytes)
}

/// 書き出しと同じ合成（PaintCanvas.composite）。rect は (x, y, 幅, 高さ)。
#[pyfunction]
#[pyo3(signature = (document, frame, options=None, background=None, rect=None, straight=false))]
pub(crate) fn compose_frame<'py>(
    py: Python<'py>,
    document: PyRef<'py, PyCoreDocument>,
    frame: usize,
    options: Option<&Bound<'py, PyDict>>,
    background: Option<Bound<'py, PyAny>>,
    rect: Option<(usize, usize, usize, usize)>,
    straight: bool,
) -> PyResult<Rendered<'py>> {
    let options = display_options(options)?;
    let background = self::background(background.as_ref())?;
    let rect = self::rect(rect);
    let document = document.document();
    let image = py
        .detach(|| match rect {
            Some(rect) => compose::composite_rect(document, frame, &options, background, rect),
            None => compose::composite(document, frame, &options, background),
        })
        .map_err(value_error)?;
    Ok(rendered(py, image, straight))
}

/// レイヤーごとの書き出しと同じ表示用画像（_display_layer_image）。
/// (レイヤー, 実体のコマ, 不透明度, x, y, 幅, 高さ, 画素) か None。
#[pyfunction]
#[pyo3(signature = (document, frame, layer, options=None, straight=false))]
pub(crate) fn compose_layer<'py>(
    py: Python<'py>,
    document: PyRef<'py, PyCoreDocument>,
    frame: usize,
    layer: usize,
    options: Option<&Bound<'py, PyDict>>,
    straight: bool,
) -> PyResult<Option<RenderedLayer<'py>>> {
    let options = display_options(options)?;
    let document = document.document();
    let image = py
        .detach(|| compose::layer_image(document, frame, layer, &options))
        .map_err(value_error)?;
    Ok(image.map(|image| rendered_layer(py, image, straight)))
}

/// 編集画面のレイヤーごとの表示用画像（初期画像・表示プレビュー）を下から順に返す。
#[pyfunction]
#[pyo3(signature = (document, frame, options=None, view=None, mapping=None, straight=false))]
pub(crate) fn compose_view_layers<'py>(
    py: Python<'py>,
    document: PyRef<'py, PyCoreDocument>,
    frame: usize,
    options: Option<&Bound<'py, PyDict>>,
    view: Option<&Bound<'py, PyDict>>,
    mapping: Option<&Bound<'py, PyDict>>,
    straight: bool,
) -> PyResult<Vec<RenderedLayer<'py>>> {
    let (options, view_options) = (display_options(options)?, view_options(view)?);
    let mut table = read_mapping(mapping)?;
    let document = document.document();
    let images = py
        .detach(|| compose::view_layer_images(document, frame, &options, &view_options, &mut table))
        .map_err(value_error)?;
    write_mapping(mapping, &table)?;
    Ok(images
        .into_iter()
        .map(|image| rendered_layer(py, image, straight))
        .collect())
}

/// 編集画面の表示を元の解像度で重ねた画像（縮小なし）。
#[pyfunction]
#[pyo3(signature = (document, frame, options=None, view=None, mapping=None, background=None, rect=None, straight=false))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn compose_view<'py>(
    py: Python<'py>,
    document: PyRef<'py, PyCoreDocument>,
    frame: usize,
    options: Option<&Bound<'py, PyDict>>,
    view: Option<&Bound<'py, PyDict>>,
    mapping: Option<&Bound<'py, PyDict>>,
    background: Option<Bound<'py, PyAny>>,
    rect: Option<(usize, usize, usize, usize)>,
    straight: bool,
) -> PyResult<Rendered<'py>> {
    let (options, view_options) = (display_options(options)?, view_options(view)?);
    let background = self::background(background.as_ref())?;
    let rect = self::rect(rect);
    let mut table = read_mapping(mapping)?;
    let document = document.document();
    let image = py
        .detach(|| {
            compose::view_composite(
                document,
                frame,
                &options,
                &view_options,
                &mut table,
                background,
                rect,
            )
        })
        .map_err(value_error)?;
    write_mapping(mapping, &table)?;
    Ok(rendered(py, image, straight))
}

/// 編集画面の1コマの表示（縮小・反転・オニオン）を width × height で返す。
#[pyfunction]
#[pyo3(signature = (document, frame, width, height, options=None, view=None, mapping=None, background=None, mode="nearest", flip_horizontal=false, flip_vertical=false, onion=None, straight=false))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn compose_display<'py>(
    py: Python<'py>,
    document: PyRef<'py, PyCoreDocument>,
    frame: usize,
    width: usize,
    height: usize,
    options: Option<&Bound<'py, PyDict>>,
    view: Option<&Bound<'py, PyDict>>,
    mapping: Option<&Bound<'py, PyDict>>,
    background: Option<Bound<'py, PyAny>>,
    mode: &str,
    flip_horizontal: bool,
    flip_vertical: bool,
    onion: Option<&Bound<'py, PyDict>>,
    straight: bool,
) -> PyResult<Rendered<'py>> {
    let (options, view_options) = (display_options(options)?, view_options(view)?);
    let background = self::background(background.as_ref())?;
    let target = DisplayTarget {
        width,
        height,
        mode: shrink_mode(mode)?,
        flip_horizontal,
        flip_vertical,
    };
    let onion = onion.map(onion_settings).transpose()?;
    let mut table = read_mapping(mapping)?;
    let document = document.document();
    let image = py
        .detach(|| {
            compose::display_frame(
                document,
                frame,
                &options,
                &view_options,
                &mut table,
                background,
                target,
                onion.as_ref(),
            )
        })
        .map_err(value_error)?;
    write_mapping(mapping, &table)?;
    Ok(rendered(py, image, straight))
}

/// 描く順のオニオンの一覧 [(コマ, 向き, 何枚目, 不透明度)]。
#[pyfunction]
#[pyo3(signature = (document, current, onion, options=None))]
pub(crate) fn compose_onion_draws(
    py: Python<'_>,
    document: PyRef<'_, PyCoreDocument>,
    current: usize,
    onion: &Bound<'_, PyDict>,
    options: Option<&Bound<'_, PyDict>>,
) -> PyResult<Vec<(usize, i32, usize, f64)>> {
    let (options, settings) = (display_options(options)?, onion_settings(onion)?);
    let document = document.document();
    let draws = py
        .detach(|| compose::onion_draws(document, current, &options, &settings))
        .map_err(value_error)?;
    Ok(draws
        .into_iter()
        .map(|draw| (draw.frame, draw.direction, draw.distance, draw.opacity))
        .collect())
}

/// 色を付けたオニオンの画像（_tinted_onion_image）。
#[pyfunction]
#[pyo3(signature = (document, frame, color, color_enabled, selected=None, all_layers=true, target_size=None, options=None, straight=false))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn compose_tinted_onion<'py>(
    py: Python<'py>,
    document: PyRef<'py, PyCoreDocument>,
    frame: usize,
    color: Rgb,
    color_enabled: bool,
    selected: Option<Vec<Rgb>>,
    all_layers: bool,
    target_size: Option<(usize, usize)>,
    options: Option<&Bound<'py, PyDict>>,
    straight: bool,
) -> PyResult<Rendered<'py>> {
    let options = display_options(options)?;
    let document = document.document();
    let image = py
        .detach(|| {
            compose::tinted_onion_image(
                document,
                frame,
                &options,
                color,
                color_enabled,
                selected.as_deref(),
                all_layers,
                target_size,
            )
        })
        .map_err(value_error)?;
    Ok(rendered(py, image, straight))
}

/// 乗算済みの画像の縮小表示（_display_image_for_rect の縮小と反転）。
#[pyfunction]
#[pyo3(signature = (width, height, pixels, target_width, target_height, mode="nearest", flip_horizontal=false, flip_vertical=false, straight=false))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn compose_scale_for_display<'py>(
    py: Python<'py>,
    width: usize,
    height: usize,
    pixels: &[u8],
    target_width: usize,
    target_height: usize,
    mode: &str,
    flip_horizontal: bool,
    flip_vertical: bool,
    straight: bool,
) -> PyResult<Rendered<'py>> {
    let mode = shrink_mode(mode)?;
    let image =
        PremulImage::from_premultiplied(width, height, pixels.to_vec()).map_err(value_error)?;
    let scaled = py
        .detach(|| {
            compose::display_image_for_size(
                &image,
                target_width,
                target_height,
                mode,
                flip_horizontal,
                flip_vertical,
            )
        })
        .map_err(value_error)?;
    Ok(rendered(py, scaled, straight))
}
