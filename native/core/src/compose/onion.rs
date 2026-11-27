//! オニオンスキン（前後のコマの絵を色付きで重ねる表示）。
//!
//! Python の `canvas_onion_render.OnionRenderMixin` に対応する。どのコマを何の濃さで
//! 出すか（[`onion_draws`]）と、色を付けたオニオンの画像（[`tinted_onion_image`]）を返す。
//! ずらし・回転・拡大を掛けて画面へ描くのは画面側（GPU）の仕事で、
//! [`draw_onions`] は変形のない場合だけを CPU で描く。

use crate::display::{tint_onion_of, Rgb};
use crate::document::model::Document;
use crate::Error;

use super::layers::{display_pixels, full_rect, Sources};
use super::pixel::{premultiply, PremulImage};
use super::scale::draw_scaled;
use super::{active_layer, composite_with, Background, DisplayOptions};

/// 前または後ろのオニオンの設定（`onion_previous_*`・`onion_next_*`）。
#[derive(Clone, Debug, PartialEq)]
pub struct OnionSide {
    /// 表示する枚数。
    pub count: usize,
    /// 全体の濃さ（0〜1）。
    pub opacity: f64,
    /// 色を付けるときの色。
    pub color: Rgb,
    /// 色を付けるか。
    pub color_enabled: bool,
    /// コマ別の濃さ（%）。近い順。足りない分は距離で薄める。
    pub levels: Vec<f64>,
    /// ずらし（キャンバスの画素）。
    pub shift_x: f64,
    /// ずらし（キャンバスの画素）。
    pub shift_y: f64,
    /// 回転（度）。
    pub rotation: f64,
    /// 拡大率（%）。
    pub scale: f64,
}

impl OnionSide {
    /// キャンバスの既定値（前は赤、後ろは青、濃さ 22%）。
    pub fn new(color: Rgb) -> Self {
        Self {
            count: 1,
            opacity: 0.22,
            color,
            color_enabled: false,
            levels: vec![100.0],
            shift_x: 0.0,
            shift_y: 0.0,
            rotation: 0.0,
            scale: 100.0,
        }
    }

    /// 画面に描くときの (ずらし x, ずらし y, 回転, 倍率)（`onion_transform_values`）。
    pub fn transform(&self) -> (f64, f64, f64, f64) {
        (
            self.shift_x,
            self.shift_y,
            self.rotation,
            (self.scale / 100.0).max(0.01),
        )
    }

    /// `distance` 枚目（1 始まり）の濃さ（`_draw_onion_range` の draw_opacity）。
    pub fn draw_opacity(&self, distance: usize) -> f64 {
        let level = match self.levels.get(distance - 1) {
            Some(level) => (level / 100.0).clamp(0.0, 1.0),
            None => {
                (1.0 - ((distance - 1) as f64 / self.count.max(1) as f64) * 0.55).clamp(0.0, 1.0)
            }
        };
        (self.opacity * level).clamp(0.0, 1.0)
    }
}

/// オニオンスキンの設定。
#[derive(Clone, Debug, PartialEq)]
pub struct OnionSettings {
    /// 前のコマ。
    pub previous: OnionSide,
    /// 後ろのコマ。
    pub next: OnionSide,
    /// すべてのレイヤーを出すか（偽ならアクティブレイヤーだけ）。
    pub all_layers: bool,
    /// 選んだ使用色だけを出すか。
    pub selected_colors_only: bool,
    /// 選んだ使用色（`selected_used_color_rgbs`）。
    pub selected_colors: Vec<Rgb>,
    /// タップ・トラックブックの拡大率（`onion_tu_tb_scale`）。
    pub tu_tb_scale: f64,
}

impl Default for OnionSettings {
    fn default() -> Self {
        Self {
            previous: OnionSide::new((255, 92, 92)),
            next: OnionSide::new((92, 160, 255)),
            all_layers: true,
            selected_colors_only: false,
            selected_colors: Vec::new(),
            tu_tb_scale: 1.0,
        }
    }
}

impl OnionSettings {
    /// 向き（負なら前）の設定。
    pub fn side(&self, direction: i32) -> &OnionSide {
        if direction < 0 {
            &self.previous
        } else {
            &self.next
        }
    }
}

/// 描くオニオンの1枚。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct OnionDraw {
    /// 絵を出すコマ。
    pub frame: usize,
    /// -1 なら前、1 なら後ろ。
    pub direction: i32,
    /// 現在コマから数えて何枚目か（1 始まり）。
    pub distance: usize,
    /// 描くときの不透明度。
    pub opacity: f64,
}

/// コマに見えている絵の組（`_onion_frame_identity`）。
fn frame_identity(
    document: &Document,
    sources: &Sources,
    frame: usize,
    active: usize,
    all_layers: bool,
) -> Option<Vec<(usize, usize)>> {
    if frame >= sources.frame_count() {
        return None;
    }
    let visible = |layer: usize| {
        let source = sources.layer(document, frame, layer)?;
        source.settings.visible.then_some((layer, source.key_frame))
    };
    if !all_layers {
        return visible(active).map(|entry| vec![entry]);
    }
    let identity: Vec<_> = (0..sources.layer_count()).filter_map(visible).collect();
    (!identity.is_empty()).then_some(identity)
}

fn frame_indices(
    document: &Document,
    sources: &Sources,
    current: usize,
    active: usize,
    all_layers: bool,
    direction: i32,
    count: usize,
) -> Vec<usize> {
    let step: i64 = if direction < 0 { -1 } else { 1 };
    let mut indices = Vec::new();
    let mut previous = frame_identity(document, sources, current, active, all_layers);
    let mut frame = current as i64 + step;
    while 0 <= frame && (frame as usize) < sources.frame_count() && indices.len() < count {
        let identity = frame_identity(document, sources, frame as usize, active, all_layers);
        if identity.is_some() && identity != previous {
            indices.push(frame as usize);
            previous = identity;
        }
        frame += step;
    }
    indices
}

/// 空セルと同じ絵の保持区間を飛ばし、前後の絵のあるコマを近い順に返す
/// （`_onion_frame_indices`）。`active` はアクティブレイヤーの表示順の番号。
pub fn onion_frame_indices(
    document: &Document,
    current: usize,
    active: usize,
    all_layers: bool,
    direction: i32,
    count: usize,
) -> Result<Vec<usize>, Error> {
    let sources = Sources::new(document)?;
    Ok(frame_indices(
        document, &sources, current, active, all_layers, direction, count,
    ))
}

/// 描く順（前の遠い絵から、次に後ろの遠い絵から）のオニオンの一覧。濃さ 0 は省く。
///
/// `paintEvent` が `_draw_onion_range` を前・後ろの順に呼ぶのと同じ順。
pub fn onion_draws(
    document: &Document,
    current: usize,
    options: &DisplayOptions,
    settings: &OnionSettings,
) -> Result<Vec<OnionDraw>, Error> {
    let sources = Sources::new(document)?;
    let active = active_layer(document, options);
    let mut draws = Vec::new();
    for direction in [-1, 1] {
        let side = settings.side(direction);
        let indices = frame_indices(
            document,
            &sources,
            current,
            active,
            settings.all_layers,
            direction,
            side.count,
        );
        for (index, &frame) in indices.iter().enumerate().rev() {
            let distance = index + 1;
            let opacity = side.draw_opacity(distance);
            if opacity > 0.0 {
                draws.push(OnionDraw {
                    frame,
                    direction,
                    distance,
                    opacity,
                });
            }
        }
    }
    Ok(draws)
}

/// 画面用のオニオンの大きさ（`_draw_onion_range` の target_size）。`display` は
/// 再生用の表示の大きさ、`source` は元の絵の大きさ。オニオン独自の拡大があれば
/// 元の大きさまで大きくする。
pub fn onion_target_size(
    display: (usize, usize),
    source: (usize, usize),
    side: &OnionSide,
    tu_tb_scale: f64,
) -> (usize, usize) {
    let (_, _, _, local_scale) = side.transform();
    let preview_scale = (local_scale * tu_tb_scale).max(1.0);
    if preview_scale <= 1.0 {
        return display;
    }
    let grow = |size: usize, limit: usize| {
        let scaled = (size as f64 * preview_scale).round_ties_even() as i64;
        limit.min(scaled.max(1) as usize)
    };
    (grow(display.0, source.0), grow(display.1, source.1))
}

/// コマ `frame` の絵に色を付けたオニオンの画像（`_tinted_onion_image`）。
///
/// `target_size` が None なら元の解像度（書き出しと同じ合成）、Some なら画面用に
/// その大きさへニアレストで描いてから色を付ける。`selected` は選んだ使用色だけを
/// 出すときの色。
#[allow(clippy::too_many_arguments)]
pub fn tinted_onion_image(
    document: &Document,
    frame: usize,
    options: &DisplayOptions,
    color: Rgb,
    color_enabled: bool,
    selected: Option<&[Rgb]>,
    all_layers: bool,
    target_size: Option<(usize, usize)>,
) -> Result<PremulImage, Error> {
    let sources = Sources::new(document)?;
    tinted_with(
        document,
        &sources,
        frame,
        options,
        color,
        color_enabled,
        selected,
        all_layers,
        target_size,
    )
}

#[allow(clippy::too_many_arguments)]
fn tinted_with(
    document: &Document,
    sources: &Sources,
    frame: usize,
    options: &DisplayOptions,
    color: Rgb,
    color_enabled: bool,
    selected: Option<&[Rgb]>,
    all_layers: bool,
    target_size: Option<(usize, usize)>,
) -> Result<PremulImage, Error> {
    let active = active_layer(document, options);
    let (width, height) = document.size();
    let image = match target_size {
        Some((target_width, target_height)) => {
            let mut image = PremulImage::transparent(target_width, target_height)?;
            for layer in 0..sources.layer_count() {
                if !all_layers && layer != active {
                    continue;
                }
                let Some(source) = sources.layer(document, frame, layer) else {
                    continue;
                };
                let opacity = source.settings.opacity;
                if !source.settings.visible || opacity <= 0.0 {
                    continue;
                }
                if let Some(pixels) = source.image {
                    let pixels = display_pixels(
                        &source,
                        pixels,
                        full_rect(pixels),
                        layer == active,
                        options,
                    )?;
                    draw_scaled(&mut image, &pixels, opacity.clamp(0.0, 1.0));
                }
            }
            image
        }
        None if all_layers => composite_with(
            document,
            sources,
            frame,
            options,
            Background::Transparent,
            None,
        )?,
        None => {
            let mut image = PremulImage::transparent(width, height)?;
            if let Some(source) = sources.layer(document, frame, active) {
                if let (true, Some(pixels)) = (source.settings.visible, source.image) {
                    let pixels = display_pixels(&source, pixels, full_rect(pixels), true, options)?;
                    image.draw(&pixels, 0, 0, source.settings.opacity.clamp(0.0, 1.0));
                }
            }
            image
        }
    };
    let straight = image.to_straight_bytes();
    let tinted = tint_onion_of(
        &straight,
        color,
        color_enabled,
        selected.map(<[Rgb]>::to_vec),
    );
    let (width, height) = (image.width(), image.height());
    let mut pixels = image.into_pixels();
    for (out, pixel) in pixels.chunks_exact_mut(4).zip(tinted.chunks_exact(4)) {
        out.copy_from_slice(&premultiply([pixel[0], pixel[1], pixel[2], pixel[3]]));
    }
    PremulImage::from_premultiplied(width, height, pixels)
}

/// 変形（ずらし・回転・拡大）を掛けずに、オニオンを `target` 全体へ重ねる。
///
/// `paintEvent` のオニオンの描画のうち、変形が恒等のときと同じ画素になる。
/// オニオンの画像は `target` と同じ大きさで作り（[`onion_target_size`] の拡大は使わない）、
/// 反転を掛けてから重ねる。外周の枠線は描かない。
#[allow(clippy::too_many_arguments)]
pub fn draw_onions(
    target: &mut PremulImage,
    document: &Document,
    current: usize,
    options: &DisplayOptions,
    settings: &OnionSettings,
    flip_horizontal: bool,
    flip_vertical: bool,
) -> Result<(), Error> {
    let sources = Sources::new(document)?;
    let size = Some((target.width(), target.height()));
    let selected = settings
        .selected_colors_only
        .then_some(settings.selected_colors.as_slice());
    for draw in onion_draws(document, current, options, settings)? {
        let side = settings.side(draw.direction);
        let onion = tinted_with(
            document,
            &sources,
            draw.frame,
            options,
            side.color,
            side.color_enabled,
            selected,
            settings.all_layers,
            size,
        )?;
        let onion = onion.mirrored(flip_horizontal, flip_vertical);
        draw_scaled(target, &onion, draw.opacity);
    }
    Ok(())
}
