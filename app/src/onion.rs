//! オニオンスキン（前後のコマの絵を色付きで重ねる表示）の設定・描画・合わせる操作。
//!
//! Qt 版の `canvas_onion_render`（描画）・`canvas_onion_interaction`（ドラッグで合わせる・
//! Ctrl＋クリックで前後のコマへ切り替える #60）・`main_window_onion`／`onion.py`（設定の窓）に
//! 当たる。計算は pma-core の `compose::onion`（`onion_draws`・`tinted_onion_image`・
//! `draw_onion`・`draw_onion_outline`）と `tools::onion`（`OnionInteraction`・`onion_frame_at`・
//! `set_canvas_view_rotation`）。
//!
//! - オニオンは画像の座標（表示倍率 1・回転なし）で `draw_onion` に描き、今のコマの合成の
//!   上へ重ねて表示の画像にする（Qt も現在コマより手前に重ねる）。画面の拡大・回転は
//!   キャンバスの表示（viewer.js）が掛ける。
//! - 設定は作品の付帯情報の `display`（Qt と同じ鍵）に読み書きする。ON／OFF はタイムラインの
//!   `onion`（TimelineUi）。

use crate::session::{current_index, Changed, Session};
use pma_core::compose::onion::{
    draw_onion, draw_onion_outline, onion_draws, onion_frame_indices, onion_target_size,
    tinted_onion_image, OnionSettings, OnionSide,
};
use pma_core::compose::painter::{Device, RectF, Transform};
use pma_core::compose::{DisplayOptions, PremulImage};
use pma_core::image::Rect;
use pma_core::project::json::Json;
use pma_core::tools::onion::{
    center_canvas_between, onion_frame_at, set_canvas_view_rotation, Axis, CanvasView,
    InteractionMode, OnionInteraction,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::Emitter;

/// オニオンの状態（設定・合わせる操作の途中・描いたオニオンの控え）。
pub struct OnionUi {
    pub settings: OnionSettings,
    /// 「キャンバスを中央位置へ」の中央％（`onion_center_percent`）。
    pub center_percent: f64,
    interaction: Option<OnionInteraction>,
    cache: Option<(CacheKey, Option<PremulImage>)>,
}

impl Default for OnionUi {
    fn default() -> Self {
        Self {
            settings: OnionSettings::default(),
            center_percent: 50.0,
            interaction: None,
            cache: None,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
struct CacheKey {
    frame: usize,
    layer: usize,
    revision: (u64, u64),
    settings: OnionSettings,
    size: (usize, usize),
}

/// 前または後ろの設定（画面とのやり取りの形）。
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct SideValues {
    pub count: usize,
    /// 全体の濃さ（0〜1）。
    pub opacity: f64,
    pub color: [u8; 3],
    pub color_enabled: bool,
    /// コマ別の濃さ（%）。
    pub levels: Vec<f64>,
    pub shift_x: f64,
    pub shift_y: f64,
    pub rotation: f64,
    /// 拡大率（%）。
    pub scale: f64,
}

/// 画面へ渡すオニオンの状態。設定を変えるときは、この形の一部を送る。
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct OnionView {
    pub enabled: bool,
    pub previous: SideValues,
    pub next: SideValues,
    pub selected_colors_only: bool,
    pub center_percent: f64,
    /// 合わせる操作の途中：`shift:-1:xy`・`canvas` など（無ければ None）。
    #[serde(default)]
    pub interaction: Option<String>,
}

fn side_values(side: &OnionSide) -> SideValues {
    SideValues {
        count: side.count,
        opacity: side.opacity,
        color: [side.color.0, side.color.1, side.color.2],
        color_enabled: side.color_enabled,
        levels: side.levels.clone(),
        shift_x: side.shift_x,
        shift_y: side.shift_y,
        rotation: side.rotation,
        scale: side.scale,
    }
}

/// `normalized_onion_levels`：足りない分は近い順に 100% から 45% へ下げる。
fn normalized_levels(raw: &[f64], count: usize) -> Vec<f64> {
    let count = count.min(12);
    let mut values: Vec<f64> = raw
        .iter()
        .take(12)
        .map(|v| {
            if v.is_finite() {
                v.trunc().clamp(0.0, 100.0)
            } else {
                100.0
            }
        })
        .collect();
    while values.len() < count {
        let index = values.len();
        let value = if count <= 1 {
            100.0
        } else {
            (100.0 - 55.0 * index as f64 / (count - 1).max(1) as f64).round_ties_even()
        };
        values.push(value.clamp(10.0, 100.0));
    }
    values.truncate(count);
    values
}

fn normalized_angle(value: f64) -> f64 {
    pma_core::tools::onion::normalized_angle(value)
}

/// 作品を開いたときの値の直し方（`_prepare_ui_state`）。
fn apply_side(side: &mut OnionSide, v: &SideValues) {
    let finite = |x: f64, d: f64| if x.is_finite() { x } else { d };
    side.count = v.count.min(12);
    side.opacity = finite(v.opacity, 0.22).clamp(0.01, 1.0);
    side.color = (v.color[0], v.color[1], v.color[2]);
    side.color_enabled = v.color_enabled;
    side.levels = normalized_levels(&v.levels, side.count);
    side.shift_x = finite(v.shift_x, 0.0).clamp(-10000.0, 10000.0);
    side.shift_y = finite(v.shift_y, 0.0).clamp(-10000.0, 10000.0);
    side.rotation = normalized_angle(finite(v.rotation, 0.0));
    side.scale = finite(v.scale, 100.0).clamp(1.0, 199.0);
}

impl OnionUi {
    pub fn view(&self, enabled: bool) -> OnionView {
        OnionView {
            enabled,
            previous: side_values(&self.settings.previous),
            next: side_values(&self.settings.next),
            selected_colors_only: self.settings.selected_colors_only,
            center_percent: self.center_percent,
            interaction: self.interaction.as_ref().map(|i| match i.mode() {
                InteractionMode::Shift { direction, axis } => format!(
                    "shift:{direction}:{}",
                    match axis {
                        Axis::X => "x",
                        Axis::Y => "y",
                        Axis::Xy => "xy",
                    }
                ),
                InteractionMode::Canvas => "canvas".into(),
            }),
        }
    }

    fn apply_view(&mut self, v: &OnionView) {
        apply_side(&mut self.settings.previous, &v.previous);
        apply_side(&mut self.settings.next, &v.next);
        self.settings.selected_colors_only = v.selected_colors_only;
        self.center_percent = if v.center_percent.is_finite() {
            v.center_percent.clamp(0.0, 100.0)
        } else {
            50.0
        };
    }
}

// ---------------------------------------------------------------------------
// 付帯情報（project.json の display）
// ---------------------------------------------------------------------------

fn json_f64(value: &Json) -> Option<f64> {
    match value {
        Json::Int(text) => text.parse().ok(),
        Json::Float(f) => Some(*f),
        Json::Bool(b) => Some(f64::from(u8::from(*b))),
        _ => None,
    }
}

fn hex_color(text: &str) -> Option<[u8; 3]> {
    let t = text.strip_prefix('#')?;
    if t.len() < 6 {
        return None;
    }
    let p = |i: usize| u8::from_str_radix(&t[i..i + 2], 16).ok();
    // #AARRGGBB（Qt の名前）にも合わせる。
    let off = if t.len() >= 8 { 2 } else { 0 };
    Some([p(off)?, p(off + 2)?, p(off + 4)?])
}

/// 作品を開いたとき（project_meta の load_into_timeline から）：`display` の鍵を読む。
pub fn load(s: &mut Session) {
    let mut ui = OnionUi::default();
    let mut enabled = false;
    if let Some(display) = s
        .project
        .as_ref()
        .and_then(|p| p.metadata.get("display").cloned())
    {
        let num = |key: &str| display.get(key).and_then(json_f64);
        let flag = |key: &str| matches!(display.get(key), Some(Json::Bool(true)));
        let list = |key: &str| match display.get(key) {
            Some(Json::Array(items)) => {
                items.iter().map(|v| json_f64(v).unwrap_or(100.0)).collect()
            }
            _ => Vec::new(),
        };
        let color = |key: &str, d: [u8; 3]| match display.get(key) {
            Some(Json::Str(t)) => hex_color(t).unwrap_or(d),
            _ => d,
        };
        enabled = flag("onion_skin");
        let mut view = ui.view(enabled);
        for (name, side, default_color) in [
            ("previous", &mut view.previous, [255, 92, 92]),
            ("next", &mut view.next, [92, 160, 255]),
        ] {
            let key = |k: &str| format!("onion_{name}_{k}");
            side.count = num(&key("count")).unwrap_or(1.0).clamp(0.0, 12.0) as usize;
            side.opacity = num(&key("opacity")).unwrap_or(0.22);
            side.levels = list(&key("levels"));
            side.color = color(&key("color"), default_color);
            side.color_enabled = flag(&key("color_enabled"));
            side.shift_x = num(&key("shift_x")).unwrap_or(0.0);
            side.shift_y = num(&key("shift_y")).unwrap_or(0.0);
            side.rotation = num(&key("rotation")).unwrap_or(0.0);
            side.scale = num(&key("scale")).unwrap_or(100.0);
        }
        view.selected_colors_only = flag("onion_selected_colors_only");
        view.center_percent = num("onion_center_percent").unwrap_or(50.0);
        ui.apply_view(&view);
        // 【Group C】TU/TB 拡大率（main_window_project_io の onion_tu_tb_scale）。
        let scale = num("onion_tu_tb_scale").unwrap_or(1.0);
        ui.settings.tu_tb_scale = if scale.is_finite() {
            scale.clamp(0.05, 20.0)
        } else {
            1.0
        };
    }
    s.tools.onion = ui;
    s.timeline.onion = enabled;
}

/// 保存の前（project_meta の store_from_session から）：`display` の鍵へ書く。
pub fn store(s: &mut Session) {
    let enabled = s.timeline.onion;
    let view = s.tools.onion.view(enabled);
    let Some(p) = s.project.as_mut() else { return };
    let mut display = match p.metadata.get("display") {
        Some(d @ Json::Object(_)) => d.clone(),
        _ => Json::Object(Vec::new()),
    };
    let float = |v: f64| Json::Float(v);
    let int = |v: i64| Json::Int(v.to_string());
    let hex = |c: [u8; 3]| Json::Str(format!("#{:02X}{:02X}{:02X}", c[0], c[1], c[2]));
    display.set("onion_skin", Json::Bool(enabled));
    for (name, side) in [("previous", &view.previous), ("next", &view.next)] {
        let key = |k: &str| format!("onion_{name}_{k}");
        display.set(&key("count"), int(side.count as i64));
        display.set(&key("opacity"), float(side.opacity));
        display.set(
            &key("levels"),
            Json::Array(side.levels.iter().map(|v| int(*v as i64)).collect()),
        );
        display.set(&key("color"), hex(side.color));
        display.set(&key("color_enabled"), Json::Bool(side.color_enabled));
        display.set(&key("shift_x"), float(side.shift_x));
        display.set(&key("shift_y"), float(side.shift_y));
        display.set(&key("rotation"), float(side.rotation));
        display.set(&key("scale"), float(side.scale));
    }
    display.set(
        "onion_selected_colors_only",
        Json::Bool(view.selected_colors_only),
    );
    display.set("onion_center_percent", float(view.center_percent));
    display.set(
        "onion_tu_tb_scale",
        float(s.tools.onion.settings.tu_tb_scale),
    );
    p.metadata.set("display", display);
}

// ---------------------------------------------------------------------------
// 描画
// ---------------------------------------------------------------------------

/// 今のコマの上に重ねるオニオン（画像の座標、乗算済み）。描くものが無ければ None。
///
/// `stale_ok` なら、絵の版だけが違う控えも使う（線を描いている途中の範囲の合成。
/// 描いているのは今のコマなので、前後のオニオンは変わらない）。
fn onion_layer(s: &mut Session, stale_ok: bool) -> Result<Option<PremulImage>, String> {
    let Some(p) = s.project.as_ref() else {
        return Ok(None);
    };
    let doc = &p.document;
    let (frame, layer) = current_index(doc);
    let mut settings = s.tools.onion.settings.clone();
    settings.selected_colors = s
        .tools
        .paint
        .used_selection
        .iter()
        .map(|c| (c[0], c[1], c[2]))
        .collect();
    let key = CacheKey {
        frame,
        layer,
        revision: s.content_revision(),
        settings: settings.clone(),
        size: doc.size(),
    };
    if let Some((cached, image)) = &s.tools.onion.cache {
        let same = *cached == key
            || (stale_ok
                && CacheKey {
                    revision: key.revision,
                    ..cached.clone()
                } == key);
        if same {
            return Ok(image.clone());
        }
    }
    let err = |e: pma_core::Error| e.to_string();
    let options = DisplayOptions::default();
    let (w, h) = doc.size();
    let draws = onion_draws(doc, frame, &options, &settings).map_err(err)?;
    let mut image = PremulImage::transparent(w, h).map_err(err)?;
    let mut drew = false;
    {
        let mut device = Device::new(&mut image);
        let base = Transform::identity();
        let work = RectF::new(0.0, 0.0, w as f64, h as f64);
        let selected = settings
            .selected_colors_only
            .then_some(settings.selected_colors.as_slice());
        // Qt の paintEvent：前の範囲（遠い絵から）と外周、次に後ろの範囲と外周。
        for direction in [-1, 1] {
            let side = settings.side(direction);
            for draw in draws.iter().filter(|d| d.direction == direction) {
                let target = onion_target_size((w, h), (w, h), side, settings.tu_tb_scale);
                let onion = tinted_onion_image(
                    doc,
                    draw.frame,
                    &options,
                    side.color,
                    side.color_enabled,
                    selected,
                    settings.all_layers,
                    Some(target),
                )
                .map_err(err)?;
                draw_onion(&mut device, &base, work, side, 1.0, &onion, draw.opacity);
                drew = true;
            }
            let frames = onion_frame_indices(
                doc,
                frame,
                layer,
                settings.all_layers,
                direction,
                side.count,
            )
            .map_err(err)?;
            if !frames.is_empty() {
                draw_onion_outline(&mut device, &base, work, work, side, 1.0);
                drew = true;
            }
        }
    }
    let out = drew.then_some(image);
    s.tools.onion.cache = Some((key, out.clone()));
    Ok(out)
}

/// 表示の画像（今のコマの合成）にオニオンを重ねる。`rect` があればその範囲だけ
/// （線を描いている途中に合成し直した範囲）。session.rs の recompose から呼ぶ。
pub fn apply(s: &mut Session, rect: Option<Rect>) -> Result<(), String> {
    if !s.timeline.onion || s.project.is_none() {
        return Ok(());
    }
    let Some(layer) = onion_layer(s, rect.is_some())? else {
        return Ok(());
    };
    let (w, h) = (s.display.width(), s.display.height());
    if (layer.width(), layer.height()) != (w, h) {
        return Ok(());
    }
    let rect = rect.unwrap_or(Rect {
        x: 0,
        y: 0,
        width: w,
        height: h,
    });
    let mut pixels = s.display.copy_rgba_rect(rect).map_err(|e| e.to_string())?;
    let src = layer.pixels();
    for row in 0..rect.height {
        for column in 0..rect.width {
            let at = ((rect.y + row) * w + rect.x + column) * 4;
            let o = &src[at..at + 4];
            let a = u32::from(o[3]);
            if a == 0 {
                continue;
            }
            let d = &mut pixels[(row * rect.width + column) * 4..][..4];
            // 表示は不透明（白の下地）。乗算済みの色を SourceOver で重ねる。
            for c in 0..3 {
                let v = u32::from(o[c]) + (u32::from(d[c]) * (255 - a) + 127) / 255;
                d[c] = v.min(255) as u8;
            }
            d[3] = 255;
        }
    }
    s.display
        .write_rgba(rect, &pixels)
        .map_err(|e| e.to_string())
}

// ---------------------------------------------------------------------------
// 操作
// ---------------------------------------------------------------------------

/// 設定を変えたので表示を作り直し、画面に知らせる。
pub fn changed(app: &tauri::AppHandle, s: &mut Session) {
    let _ = s.recompose();
    let _ = app.emit(
        "document-changed",
        Changed {
            frame: false,
            structure: false,
            pixels: true,
            source: "onion",
            playhead: None,
        },
    );
}

fn emit_view(app: &tauri::AppHandle, view: &OnionView) {
    let _ = app.emit("onion-changed", view.clone());
}

fn merge(base: &mut Value, patch: &Value) {
    match (base, patch) {
        (Value::Object(base), Value::Object(patch)) => {
            for (k, v) in patch {
                match base.get_mut(k) {
                    Some(slot) if slot.is_object() && v.is_object() => merge(slot, v),
                    _ => {
                        base.insert(k.clone(), v.clone());
                    }
                }
            }
        }
        (base, patch) => *base = patch.clone(),
    }
}

/// 設定の一部を変える（`patch` は [`OnionView`] の一部）。`enabled` は表示の ON／OFF。
pub fn set(s: &mut Session, patch: &Value) -> Result<OnionView, String> {
    let mut current =
        serde_json::to_value(s.tools.onion.view(s.timeline.onion)).map_err(|e| e.to_string())?;
    merge(&mut current, patch);
    let next: OnionView = serde_json::from_value(current).map_err(|e| e.to_string())?;
    s.tools.onion.apply_view(&next);
    s.timeline.onion = next.enabled;
    Ok(s.tools.onion.view(s.timeline.onion))
}

/// 前後の表示（ずらし・回転・拡大）をリセット（`_reset_shift`）。`both` なら前後とも。
pub fn reset(s: &mut Session, direction: i32, both: bool) {
    let settings = &mut s.tools.onion.settings;
    for (dir, side) in [(-1, &mut settings.previous), (1, &mut settings.next)] {
        if both || dir == direction {
            side.shift_x = 0.0;
            side.shift_y = 0.0;
            side.rotation = 0.0;
            side.scale = 100.0;
        }
    }
}

/// 画面の表示（viewer.js の pan・rotation・zoom）。pan は画像の中心が来る画面の位置。
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
pub struct ViewState {
    pub pan: [f64; 2],
    pub rotation: f64,
    pub zoom: f64,
}

fn canvas_view(v: &ViewState) -> CanvasView {
    CanvasView {
        pan: (v.pan[0], v.pan[1]),
        rotation: v.rotation,
        zoom: v.zoom,
    }
}

fn view_state(v: &CanvasView) -> ViewState {
    ViewState {
        pan: [v.pan.0, v.pan.1],
        rotation: v.rotation,
        zoom: v.zoom,
    }
}

/// 合わせる操作を始める（`begin_onion_shift_interaction`・
/// `begin_onion_canvas_position_interaction`）。`mode` は shift / canvas。知らせを返す。
pub fn begin_interaction(s: &mut Session, mode: &str, direction: i32, axis: &str) -> String {
    if mode == "canvas" {
        s.tools.onion.interaction = Some(OnionInteraction::canvas());
        return "表示位置：左ドラッグでキャンバス移動、Shift＋左ドラッグまたは右ドラッグで回転します。\
                TU／TB拡大率は維持され、前後オニオンの相対値へ反映されます。Escで解除します。"
            .into();
    }
    let axis = Axis::from_name(axis);
    s.tools.onion.interaction = Some(OnionInteraction::shift(direction, axis));
    let label = if direction < 0 { "前" } else { "後" };
    let axis_label = match axis {
        Axis::X => "X方向",
        Axis::Y => "Y方向",
        Axis::Xy => "XY方向",
    };
    format!("{label}のオニオンスキン：ドラッグで{axis_label}へ移動します。Escで解除します。")
}

/// 合わせる操作をやめる（`cancel_onion_interaction`）。`restore` でドラッグ中なら元へ戻す。
/// 戻した表示を返す。
pub fn cancel_interaction(s: &mut Session, restore: bool, view: &ViewState) -> Option<ViewState> {
    let interaction = s.tools.onion.interaction.take()?;
    let mut v = canvas_view(view);
    interaction
        .cancel(restore, &mut v, &mut s.tools.onion.settings)
        .then(|| view_state(&v))
}

/// キャンバス上のドラッグ（`_begin_onion_interaction_drag`・`_update_onion_interaction_drag`）。
/// `widget` は画面の座標。`rotate` は右ボタンか Shift。新しい表示を返す（操作の途中で
/// なければ None）。
pub fn drag(
    s: &mut Session,
    phase: &str,
    widget: [f64; 2],
    rotate: bool,
    view: &ViewState,
) -> Option<ViewState> {
    let ui = &mut s.tools.onion;
    let interaction = ui.interaction.as_mut()?;
    let mut v = canvas_view(view);
    match phase {
        "begin" => interaction.begin_drag((widget[0], widget[1]), rotate, &v, &ui.settings),
        _ => {
            interaction.update_drag((widget[0], widget[1]), &mut v, &mut ui.settings);
        }
    }
    if phase == "end" {
        // 離したら操作を終える（Qt の mouseReleaseEvent の cancel_onion_interaction(False)）。
        ui.interaction = None;
    }
    Some(view_state(&v))
}

/// 表示位置の回転（`set_onion_canvas_view_rotation`）。変えたら新しい表示を返す。
pub fn set_rotation(s: &mut Session, value: f64, view: &ViewState) -> Option<ViewState> {
    let mut v = canvas_view(view);
    set_canvas_view_rotation(value, &mut v, &mut s.tools.onion.settings).then(|| view_state(&v))
}

/// 【Group C】「キャンバスを中央位置へ」（`center_canvas_between_shifts`）。新しい表示と知らせを返す。
pub fn center(s: &mut Session, percent: f64, view: &ViewState) -> (ViewState, String) {
    let percent = if percent.is_finite() {
        percent.clamp(0.0, 100.0)
    } else {
        50.0
    };
    s.tools.onion.center_percent = percent;
    let mut v = canvas_view(view);
    let (rotation, scale) = center_canvas_between(percent, &mut v, &mut s.tools.onion.settings);
    let message = format!(
        "前後の位置・回転・TU/TB拡大率間の{percent}%をキャンバス基準へ移しました。\
         （中央回転 {rotation:.1}°を基準として取得／撮影倍率 {:.1}%／キャンバス回転 0°／デジタルズーム変更なし）",
        scale * 100.0
    );
    (view_state(&v), message)
}

/// Ctrl＋クリックで、見えている前後のコマへ切り替える（`pick_onion_frame`、#60）。
pub fn pick(s: &mut Session, x: f64, y: f64) -> Result<String, String> {
    let p = s.project.as_ref().ok_or("作品を開いていません")?;
    let doc = &p.document;
    let (frame, layer) = current_index(doc);
    let found = onion_frame_at(
        doc,
        (x, y),
        frame,
        layer,
        s.timeline.onion,
        &s.tools.onion.settings,
    )
    .map_err(|e| e.to_string())?;
    let Some((frame, layer)) = found else {
        return Ok("オニオン：クリックした所に前後のコマの絵がありません。".into());
    };
    let p = s.project.as_mut().ok_or("作品を開いていません")?;
    p.document
        .set_selection_index(frame, layer)
        .map_err(|e| e.to_string())?;
    s.timeline
        .select_current(&s.project.as_ref().unwrap().document);
    s.recompose()?;
    Ok(format!("オニオン：{}コマ目を編集します。", frame + 1))
}

// ---------------------------------------------------------------------------
// 画面との窓口
// ---------------------------------------------------------------------------

type SessionArg<'a> = tauri::State<'a, crate::session::Shared>;

#[tauri::command]
pub fn onion_state(state: SessionArg) -> OnionView {
    let s = state.lock().unwrap();
    s.tools.onion.view(s.timeline.onion)
}

/// 設定の一部を変える。表示を作り直す。
#[tauri::command]
pub fn onion_set(
    app: tauri::AppHandle,
    state: SessionArg,
    patch: Value,
) -> Result<OnionView, String> {
    let mut s = state.lock().unwrap();
    let view = set(&mut s, &patch)?;
    changed(&app, &mut s);
    drop(s);
    emit_view(&app, &view);
    Ok(view)
}

/// 前後の表示をリセット。
#[tauri::command]
pub fn onion_reset(
    app: tauri::AppHandle,
    state: SessionArg,
    direction: i32,
    both: bool,
) -> OnionView {
    let mut s = state.lock().unwrap();
    reset(&mut s, direction, both);
    let view = s.tools.onion.view(s.timeline.onion);
    changed(&app, &mut s);
    drop(s);
    emit_view(&app, &view);
    view
}

/// 合わせる操作の始め（`mode` は shift / canvas）・やめ（`mode` が none）。
/// 知らせと、戻した表示（やめたとき）を返す。
#[derive(Serialize)]
pub struct InteractionReply {
    pub message: Option<String>,
    pub view: Option<ViewState>,
    pub onion: OnionView,
}

#[tauri::command]
pub fn onion_interaction(
    app: tauri::AppHandle,
    state: SessionArg,
    mode: String,
    direction: Option<i32>,
    axis: Option<String>,
    restore: Option<bool>,
    view: Option<ViewState>,
) -> InteractionReply {
    let mut s = state.lock().unwrap();
    let (message, restored) = if mode == "none" {
        let v = view.unwrap_or(ViewState {
            pan: [0.0, 0.0],
            rotation: 0.0,
            zoom: 1.0,
        });
        (
            None,
            cancel_interaction(&mut s, restore.unwrap_or(false), &v),
        )
    } else {
        let m = begin_interaction(
            &mut s,
            &mode,
            direction.unwrap_or(1),
            axis.as_deref().unwrap_or("xy"),
        );
        (Some(m), None)
    };
    if restored.is_some() {
        changed(&app, &mut s);
    }
    let onion = s.tools.onion.view(s.timeline.onion);
    drop(s);
    emit_view(&app, &onion);
    InteractionReply {
        message,
        view: restored,
        onion,
    }
}

/// 合わせる操作のドラッグ。新しい表示を返す（操作の途中でなければ None）。
#[tauri::command]
pub fn onion_drag(
    app: tauri::AppHandle,
    state: SessionArg,
    phase: String,
    x: f64,
    y: f64,
    rotate: bool,
    view: ViewState,
) -> Option<ViewState> {
    let mut s = state.lock().unwrap();
    let out = drag(&mut s, &phase, [x, y], rotate, &view)?;
    if phase != "begin" {
        changed(&app, &mut s);
        let onion = s.tools.onion.view(s.timeline.onion);
        drop(s);
        emit_view(&app, &onion);
    }
    Some(out)
}

/// 表示位置の回転（設定の窓の「回転」）。新しい表示を返す。
#[tauri::command]
pub fn onion_set_rotation(
    app: tauri::AppHandle,
    state: SessionArg,
    value: f64,
    view: ViewState,
) -> Option<ViewState> {
    let mut s = state.lock().unwrap();
    let out = set_rotation(&mut s, value, &view)?;
    changed(&app, &mut s);
    let onion = s.tools.onion.view(s.timeline.onion);
    drop(s);
    emit_view(&app, &onion);
    Some(out)
}

/// 【Group C】「キャンバスを中央位置へ」。新しい表示と知らせを返す。
#[derive(Serialize)]
pub struct CenterReply {
    pub view: ViewState,
    pub message: String,
}

#[tauri::command]
pub fn onion_center(
    app: tauri::AppHandle,
    state: SessionArg,
    percent: f64,
    view: ViewState,
) -> CenterReply {
    let mut s = state.lock().unwrap();
    let (view, message) = center(&mut s, percent, &view);
    changed(&app, &mut s);
    let onion = s.tools.onion.view(s.timeline.onion);
    drop(s);
    emit_view(&app, &onion);
    CenterReply { view, message }
}

/// Ctrl＋クリック（#60）。知らせを返す。
#[tauri::command]
pub fn onion_pick(app: tauri::AppHandle, x: f64, y: f64) -> Result<String, String> {
    crate::with_session(&app, "frame", |s| pick(s, x, y))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Session {
        let mut s = Session::empty(8, 8);
        s.open_document(crate::timeline::sample_document(12, (64, 48)).unwrap())
            .unwrap();
        s
    }

    #[test]
    fn levels_fill_like_python() {
        assert_eq!(normalized_levels(&[], 1), vec![100.0]);
        assert_eq!(normalized_levels(&[], 3), vec![100.0, 72.0, 45.0]);
        assert_eq!(normalized_levels(&[150.0, 20.0, 5.0], 2), vec![100.0, 20.0]);
    }

    #[test]
    fn metadata_round_trip_and_clamps() {
        let mut s = sample();
        s.timeline.onion = true;
        set(
            &mut s,
            &serde_json::json!({"previous": {"count": 3, "shift_x": 12.5, "scale": 500.0,
                                             "color": [1, 2, 3], "rotation": 190.0}}),
        )
        .unwrap();
        let v = s.tools.onion.view(true);
        assert_eq!(v.previous.count, 3);
        assert_eq!(v.previous.levels.len(), 3);
        assert_eq!(v.previous.scale, 199.0);
        assert_eq!(v.previous.rotation, -170.0);
        store(&mut s);
        let saved = v.clone();
        s.tools.onion = OnionUi::default();
        s.timeline.onion = false;
        load(&mut s);
        assert!(s.timeline.onion);
        assert_eq!(s.tools.onion.view(true), saved);
    }

    #[test]
    fn onion_is_drawn_over_the_display_and_follows_shift() {
        let mut s = sample();
        // 2 コマ目（前のコマの絵がある）にして表示を作る。
        s.set_frame(6).unwrap();
        let plain = s.display.rgba().to_vec();
        s.timeline.onion = true;
        s.recompose().unwrap();
        let with = s.display.rgba().to_vec();
        let layer = onion_layer(&mut s, false).unwrap();
        assert!(layer.is_some(), "前のコマのオニオンが無い");
        {
            assert_ne!(plain, with);
            // ずらすと変わる。
            set(&mut s, &serde_json::json!({"previous": {"shift_x": 7.0}})).unwrap();
            s.recompose().unwrap();
            assert_ne!(s.display.rgba(), &with[..]);
        }
        // 消すと元に戻る。
        s.timeline.onion = false;
        s.recompose().unwrap();
        assert_eq!(s.display.rgba(), &plain[..]);
    }

    #[test]
    fn shift_interaction_moves_previous_and_escape_restores() {
        let mut s = sample();
        let view = ViewState {
            pan: [100.0, 100.0],
            rotation: 0.0,
            zoom: 2.0,
        };
        begin_interaction(&mut s, "shift", -1, "xy");
        drag(&mut s, "begin", [10.0, 10.0], false, &view).unwrap();
        drag(&mut s, "move", [30.0, 14.0], false, &view).unwrap();
        let p = &s.tools.onion.settings.previous;
        assert_eq!((p.shift_x, p.shift_y), (10.0, 2.0));
        assert!(cancel_interaction(&mut s, true, &view).is_some());
        assert_eq!(s.tools.onion.settings.previous.shift_x, 0.0);
        // キャンバスの回転：オニオンは画面上の角度を保つ。
        let turned = set_rotation(&mut s, 30.0, &view).unwrap();
        assert_eq!(turned.rotation, 30.0);
        assert_eq!(s.tools.onion.settings.previous.rotation, -30.0);
    }
}
