//! オニオンスキンをキャンバス上のドラッグで合わせる操作と、クリックした所の
//! オニオンのコマを選ぶ判定。Python の `canvas_onion_interaction.OnionInteractionMixin`
//! に対応する。
//!
//! - 前後のオニオンのずらしを X・Y・XY 方向へドラッグで動かす（`onion_shift`）。
//! - キャンバスの表示位置を動かす・回す（`canvas`）。オニオンは画面上の位置と角度を
//!   保つよう、ずらしと回転を逆に補正する。
//! - クリックした所に見えている前後のコマを選ぶ（#60、`onion_frame_at`）。
//!
//! 画面の状態（パン・回転・表示倍率）は [`CanvasView`]、オニオンの値は
//! [`OnionSettings`] で受け渡しする。

use crate::compose::layers::Sources;
use crate::compose::onion::{onion_frame_indices, OnionSettings, OnionSide};
use crate::compose::pixel::premultiply;
use crate::document::model::Document;
use crate::Error;

/// `geometry.normalized_angle`（-180 以上 180 未満へ寄せる）。
pub fn normalized_angle(angle: f64) -> f64 {
    // Python の % は割る数と同じ符号の余り。
    (angle + 180.0).rem_euclid(360.0) - 180.0
}

/// `geometry.rotated_vector`（度で回したベクトル）。
pub fn rotated_vector(x: f64, y: f64, degrees: f64) -> (f64, f64) {
    let angle = degrees.to_radians();
    (
        x * angle.cos() - y * angle.sin(),
        x * angle.sin() + y * angle.cos(),
    )
}

/// キャンバスの表示（`pan`・`rotation`・`zoom`）。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CanvasView {
    /// パン（画面の画素）。
    pub pan: (f64, f64),
    /// 表示の回転（度）。
    pub rotation: f64,
    /// 表示倍率。
    pub zoom: f64,
}

/// ずらしを動かす向き。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Axis {
    /// X 方向だけ。
    X,
    /// Y 方向だけ。
    Y,
    /// 両方。
    Xy,
}

impl Axis {
    /// "x"・"y"・"xy" の名前から。ほかは XY（`begin_onion_shift_interaction`）。
    pub fn from_name(name: &str) -> Self {
        match name.to_lowercase().as_str() {
            "x" => Self::X,
            "y" => Self::Y,
            _ => Self::Xy,
        }
    }
}

/// 操作の種類。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InteractionMode {
    /// 前（負）・後（正）のオニオンのずらし。
    Shift {
        /// -1 なら前、1 なら後ろ。
        direction: i32,
        /// 動かす向き。
        axis: Axis,
    },
    /// キャンバスの表示位置（移動・回転）。
    Canvas,
}

/// ドラッグの動き。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Operation {
    /// 動かす。
    Move,
    /// 回す（キャンバスのときだけ）。
    Rotate,
}

#[derive(Clone, Debug)]
struct Drag {
    widget: (f64, f64),
    view: CanvasView,
    settings: OnionSettings,
    operation: Operation,
}

/// オニオンを合わせる操作 1 回（始めてから Esc・終了まで）。
#[derive(Clone, Debug)]
pub struct OnionInteraction {
    mode: InteractionMode,
    drag: Option<Drag>,
}

/// ドラッグで変わったもの。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Changed {
    /// 表示（パン・回転）が変わった（`viewChanged`）。
    pub view: bool,
    /// オニオンの値が変わった（`onionInteractionChanged`）。
    pub onion: bool,
}

impl OnionInteraction {
    /// 前（`direction` が負）・後のオニオンのずらしを合わせる
    /// （`begin_onion_shift_interaction`）。
    pub fn shift(direction: i32, axis: Axis) -> Self {
        Self {
            mode: InteractionMode::Shift {
                direction: if direction < 0 { -1 } else { 1 },
                axis,
            },
            drag: None,
        }
    }

    /// キャンバスの表示位置を動かす・回す（`begin_onion_canvas_position_interaction`）。
    pub fn canvas() -> Self {
        Self {
            mode: InteractionMode::Canvas,
            drag: None,
        }
    }

    /// 操作の種類。
    pub fn mode(&self) -> InteractionMode {
        self.mode
    }

    /// ドラッグ中か。
    pub fn dragging(&self) -> bool {
        self.drag.is_some()
    }

    /// ドラッグ中の動き（カーソルの形に使う）。
    pub fn operation(&self) -> Option<Operation> {
        self.drag.as_ref().map(|drag| drag.operation)
    }

    /// ドラッグを始める（`_begin_onion_interaction_drag`）。`rotate` は右ボタンか
    /// Shift を押しているか（キャンバスのときだけ回す）。
    pub fn begin_drag(
        &mut self,
        widget: (f64, f64),
        rotate: bool,
        view: &CanvasView,
        settings: &OnionSettings,
    ) {
        let operation = match self.mode {
            InteractionMode::Canvas if rotate => Operation::Rotate,
            _ => Operation::Move,
        };
        self.drag = Some(Drag {
            widget,
            view: *view,
            settings: settings.clone(),
            operation,
        });
    }

    /// ドラッグで値を変える（`_update_onion_interaction_drag`）。
    pub fn update_drag(
        &self,
        widget: (f64, f64),
        view: &mut CanvasView,
        settings: &mut OnionSettings,
    ) -> Changed {
        let Some(drag) = &self.drag else {
            return Changed::default();
        };
        let widget_delta = (widget.0 - drag.widget.0, widget.1 - drag.widget.1);
        let canvas_delta = widget_delta_to_canvas(widget_delta, view, settings.tu_tb_scale);
        let start = &drag.settings;
        let mut changed = Changed {
            view: false,
            onion: true,
        };
        match self.mode {
            InteractionMode::Shift { direction, axis } => {
                let dx = if axis == Axis::Y { 0.0 } else { canvas_delta.0 };
                let dy = if axis == Axis::X { 0.0 } else { canvas_delta.1 };
                let (from, to) = if direction < 0 {
                    (&start.previous, &mut settings.previous)
                } else {
                    (&start.next, &mut settings.next)
                };
                to.shift_x = from.shift_x + dx;
                to.shift_y = from.shift_y + dy;
            }
            InteractionMode::Canvas => {
                changed.view = true;
                match drag.operation {
                    Operation::Rotate => {
                        let angle = widget_delta.0 * 0.35;
                        view.rotation = normalized_angle(drag.view.rotation + angle);
                        // キャンバスの回転分をずらしとオニオンの回転から差し引き、
                        // オニオンの画面上の位置・角度を保つ。
                        counter_rotate(&start.previous, &mut settings.previous, angle);
                        counter_rotate(&start.next, &mut settings.next, angle);
                    }
                    Operation::Move => {
                        // キャンバスは画面上で動かし、前後のオニオンは画面上の
                        // 位置を保つよう逆向きにずらしを補正する。
                        view.pan = (
                            drag.view.pan.0 + widget_delta.0,
                            drag.view.pan.1 + widget_delta.1,
                        );
                        for (from, to) in [
                            (&start.previous, &mut settings.previous),
                            (&start.next, &mut settings.next),
                        ] {
                            to.shift_x = from.shift_x - canvas_delta.0;
                            to.shift_y = from.shift_y - canvas_delta.1;
                        }
                    }
                }
            }
        }
        changed
    }

    /// 操作を終える（`cancel_onion_interaction`）。`restore` でドラッグ中なら、
    /// 始めたときの表示とオニオンの値へ戻す。戻したら真。
    pub fn cancel(
        self,
        restore: bool,
        view: &mut CanvasView,
        settings: &mut OnionSettings,
    ) -> bool {
        match self.drag {
            Some(drag) if restore => {
                view.pan = drag.view.pan;
                view.rotation = drag.view.rotation;
                for (from, to) in [
                    (&drag.settings.previous, &mut settings.previous),
                    (&drag.settings.next, &mut settings.next),
                ] {
                    to.shift_x = from.shift_x;
                    to.shift_y = from.shift_y;
                    to.rotation = from.rotation;
                    to.scale = from.scale;
                }
                settings.tu_tb_scale = drag.settings.tu_tb_scale;
                true
            }
            _ => false,
        }
    }
}

fn counter_rotate(from: &OnionSide, to: &mut OnionSide, angle: f64) {
    let (x, y) = rotated_vector(from.shift_x, from.shift_y, -angle);
    to.shift_x = x;
    to.shift_y = y;
    to.rotation = normalized_angle(from.rotation - angle);
}

/// 画面上の動きをキャンバスの画素の動きへ（`_onion_widget_delta_to_canvas`）。
pub fn widget_delta_to_canvas(
    delta: (f64, f64),
    view: &CanvasView,
    tu_tb_scale: f64,
) -> (f64, f64) {
    let zoom = (view.zoom * tu_tb_scale).max(0.0001);
    let angle = view.rotation.to_radians();
    let (dx, dy) = delta;
    (
        (dx * angle.cos() + dy * angle.sin()) / zoom,
        (-dx * angle.sin() + dy * angle.cos()) / zoom,
    )
}

/// 表示位置の回転を `value` にし、前後のオニオンの画面上の位置と角度を保つ
/// （`set_onion_canvas_view_rotation`）。変えたら真。
pub fn set_canvas_view_rotation(
    value: f64,
    view: &mut CanvasView,
    settings: &mut OnionSettings,
) -> bool {
    let target = normalized_angle(value);
    let current = normalized_angle(view.rotation);
    let delta = normalized_angle(target - current);
    if delta.abs() < 1e-9 {
        return false;
    }
    for side in [&mut settings.previous, &mut settings.next] {
        let start = side.clone();
        counter_rotate(&start, side, delta);
    }
    view.rotation = target;
    true
}

/// 「キャンバスを中央位置へ」（`center_canvas_between_shifts`）。前後のずらし・回転・
/// 拡大率の `percent`（0〜100）の所をキャンバスの基準へ取り込む。表示の回転は 0° に戻し、
/// 表示倍率（デジタルズーム）は変えない。取り込んだ (中央の回転, 撮影倍率) を返す。
pub fn center_canvas_between(
    percent: f64,
    view: &mut CanvasView,
    settings: &mut OnionSettings,
) -> (f64, f64) {
    let percent = if percent.is_finite() {
        percent.clamp(0.0, 100.0)
    } else {
        50.0
    };
    let ratio = percent / 100.0;
    let (previous, next) = (&settings.previous, &settings.next);
    let previous_scale = (previous.scale / 100.0).max(0.01);
    let next_scale = (next.scale / 100.0).max(0.01);
    let target_x = previous.shift_x + (next.shift_x - previous.shift_x) * ratio;
    let target_y = previous.shift_y + (next.shift_y - previous.shift_y) * ratio;
    // 回転は最短方向、TU/TB拡大率は撮影倍率として線形補間する。
    let rotation_delta = (next.rotation - previous.rotation + 180.0).rem_euclid(360.0) - 180.0;
    let target_rotation = normalized_angle(previous.rotation + rotation_delta * ratio);
    let target_scale = (previous_scale + (next_scale - previous_scale) * ratio).max(0.01);

    let digital_zoom = view.zoom.max(0.01);
    let current_tu_tb = settings.tu_tb_scale.max(0.0001);
    let old_view_rotation = view.rotation;
    let (display_x, display_y) = rotated_vector(
        target_x * digital_zoom * current_tu_tb,
        target_y * digital_zoom * current_tu_tb,
        old_view_rotation,
    );
    view.pan.0 += display_x;
    view.pan.1 += display_y;
    let absorbed_view_rotation = normalized_angle(old_view_rotation + target_rotation);
    settings.tu_tb_scale = (current_tu_tb * target_scale).clamp(0.05, 20.0);

    // H_target^-1 × H_each で相対位置・回転・倍率を取り直す。
    let relative = |x: f64, y: f64| {
        let (rx, ry) = rotated_vector(x - target_x, y - target_y, -target_rotation);
        rotated_vector(rx / target_scale, ry / target_scale, absorbed_view_rotation)
    };
    let previous_shift = relative(previous.shift_x, previous.shift_y);
    let next_shift = relative(next.shift_x, next.shift_y);
    let previous_rotation = normalized_angle(previous.rotation - target_rotation);
    let next_rotation = normalized_angle(next.rotation - target_rotation);
    for (side, shift, rotation, scale) in [
        (
            &mut settings.previous,
            previous_shift,
            previous_rotation,
            previous_scale,
        ),
        (&mut settings.next, next_shift, next_rotation, next_scale),
    ] {
        side.shift_x = shift.0;
        side.shift_y = shift.1;
        side.rotation = rotation;
        side.scale = (scale / target_scale * 100.0).clamp(1.0, 199.0);
    }
    view.rotation = 0.0;
    (target_rotation, target_scale)
}

/// キャンバス上の `point` に重なって見える、前後のオニオンの絵の中の位置
/// （`_onion_source_point`）。オニオンは絵の中心を軸に ずらし → 回転 → 拡大 して
/// 重ねるので、その逆を掛ける。`size` は絵の大きさ。
pub fn onion_source_point(point: (f64, f64), side: &OnionSide, size: (usize, usize)) -> (f64, f64) {
    let (shift_x, shift_y, rotation, scale) = side.transform();
    let (cx, cy) = (size.0 as f64 / 2.0, size.1 as f64 / 2.0);
    let dx = point.0 - cx - shift_x;
    let dy = point.1 - cy - shift_y;
    let angle = (-rotation).to_radians();
    let rx = dx * angle.cos() - dy * angle.sin();
    let ry = dx * angle.sin() + dy * angle.cos();
    let scale = scale.max(0.01);
    (cx + rx / scale, cy + ry / scale)
}

/// 「まわりで一番多い色」を数える窓の半径（`OWNER_RADIUS`）。
pub const OWNER_RADIUS: i64 = 4;

/// 画素の比べ方：乗算済みの値（Qt の QImage で読んだときと同じに見分ける）。
type Pixel = Option<[u8; 4]>;

struct Picker<'a> {
    document: &'a Document,
    sources: Sources,
    size: (usize, usize),
}

impl Picker<'_> {
    /// `_pixel_at`。透明・範囲外・非表示は None。
    fn pixel(&self, frame: usize, point: (f64, f64), layer: usize) -> Pixel {
        let (x, y) = (point.0.floor(), point.1.floor());
        let source = self.sources.layer(self.document, frame, layer)?;
        let image = source.image?;
        if !source.settings.visible
            || x < 0.0
            || y < 0.0
            || x >= image.width() as f64
            || y >= image.height() as f64
        {
            return None;
        }
        let at = (y as usize * image.width() + x as usize) * 4;
        let rgba = &image.rgba()[at..at + 4];
        let pixel = premultiply([rgba[0], rgba[1], rgba[2], rgba[3]]);
        (pixel[3] != 0).then_some(pixel)
    }

    /// `_drawn_layer_at`。今のコマの同じ所と同じ色の画素は見分けられないので飛ばす。
    fn drawn_layer(
        &self,
        frame: usize,
        point: (f64, f64),
        layers: &[usize],
        unlike: usize,
        unlike_point: (f64, f64),
    ) -> Option<usize> {
        layers.iter().copied().find(|&layer| {
            let pixel = self.pixel(frame, point, layer);
            pixel.is_some() && pixel != self.pixel(unlike, unlike_point, layer)
        })
    }

    /// `_current_owns_point`。
    fn current_owns(
        &self,
        current: usize,
        point: (f64, f64),
        layer: usize,
        settings: &OnionSettings,
        previous: &[usize],
        following: &[usize],
    ) -> bool {
        let Some(pixel) = self.pixel(current, point, layer) else {
            return false;
        };
        let others: Vec<Pixel> = [(-1, previous), (1, following)]
            .into_iter()
            .flat_map(|(direction, frames)| {
                let source = onion_source_point(point, settings.side(direction), self.size);
                frames.iter().map(move |&frame| (frame, source))
            })
            .map(|(frame, source)| self.pixel(frame, source, layer))
            .collect();
        if !others.is_empty() && others.iter().all(|other| *other != Some(pixel)) {
            return true;
        }
        // Python の dict と同じく、最初に出た順を保って一番多い色を選ぶ。
        let mut counts: Vec<(Pixel, usize)> = Vec::new();
        for dy in -OWNER_RADIUS..=OWNER_RADIUS {
            for dx in -OWNER_RADIUS..=OWNER_RADIUS {
                let value = self.pixel(current, (point.0 + dx as f64, point.1 + dy as f64), layer);
                match counts.iter_mut().find(|(key, _)| *key == value) {
                    Some((_, count)) => *count += 1,
                    None => counts.push((value, 1)),
                }
            }
        }
        let mut majority = counts[0];
        for &entry in &counts[1..] {
            if entry.1 > majority.1 {
                majority = entry;
            }
        }
        Some(pixel) != majority.0
    }
}

/// クリックした `point`（キャンバスの画素）に見えているオニオンの (コマ, レイヤー)
/// （`onion_frame_at`）。見えていなければ None。
///
/// 近いコマから順に調べ、同じ近さなら後ろのコマを先にする。今のコマの絵の上なら
/// 選ばない。`active` はアクティブレイヤーの表示順の番号、`onion_skin` は
/// オニオンを表示しているか。
pub fn onion_frame_at(
    document: &Document,
    point: (f64, f64),
    current: usize,
    active: usize,
    onion_skin: bool,
    settings: &OnionSettings,
) -> Result<Option<(usize, usize)>, Error> {
    let sources = Sources::new(document)?;
    if !onion_skin || sources.frame_count() == 0 {
        return Ok(None);
    }
    let layers: Vec<usize> = if settings.all_layers {
        // 上のレイヤーほど手前に描かれる。今のレイヤーを先に調べる。
        std::iter::once(active)
            .chain(
                (0..sources.layer_count())
                    .rev()
                    .filter(|&layer| layer != active),
            )
            .collect()
    } else {
        vec![active]
    };
    let all = settings.all_layers;
    let previous =
        onion_frame_indices(document, current, active, all, -1, settings.previous.count)?;
    let following = onion_frame_indices(document, current, active, all, 1, settings.next.count)?;
    let picker = Picker {
        document,
        sources,
        size: document.size(),
    };
    if picker.current_owns(current, point, active, settings, &previous, &following) {
        return Ok(None);
    }
    for distance in 0..previous.len().max(following.len()) {
        for (direction, frames) in [(1, &following), (-1, &previous)] {
            let Some(&frame) = frames.get(distance) else {
                continue;
            };
            let source = onion_source_point(point, settings.side(direction), picker.size);
            if let Some(layer) = picker.drawn_layer(frame, source, &layers, current, point) {
                return Ok(Some((frame, layer)));
            }
        }
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalized_angle_matches_python_modulo() {
        assert_eq!(normalized_angle(190.0), -170.0);
        assert_eq!(normalized_angle(-190.0), 170.0);
        assert_eq!(normalized_angle(180.0), -180.0);
    }

    #[test]
    fn canvas_move_keeps_onion_on_screen() {
        let mut view = CanvasView {
            pan: (10.0, 20.0),
            rotation: 0.0,
            zoom: 2.0,
        };
        let mut settings = OnionSettings::default();
        let mut interaction = OnionInteraction::canvas();
        interaction.begin_drag((100.0, 100.0), false, &view, &settings);
        let changed = interaction.update_drag((110.0, 104.0), &mut view, &mut settings);
        assert!(changed.view && changed.onion);
        assert_eq!(view.pan, (20.0, 24.0));
        assert_eq!(
            (settings.previous.shift_x, settings.previous.shift_y),
            (-5.0, -2.0)
        );
        assert!(interaction.cancel(true, &mut view, &mut settings));
        assert_eq!(view.pan, (10.0, 20.0));
        assert_eq!(settings.previous.shift_x, 0.0);
    }

    #[test]
    fn shift_moves_only_the_chosen_axis() {
        let mut view = CanvasView {
            pan: (0.0, 0.0),
            rotation: 0.0,
            zoom: 1.0,
        };
        let mut settings = OnionSettings::default();
        let mut interaction = OnionInteraction::shift(1, Axis::from_name("Y"));
        interaction.begin_drag((0.0, 0.0), true, &view, &settings);
        assert_eq!(interaction.operation(), Some(Operation::Move));
        interaction.update_drag((7.0, 3.0), &mut view, &mut settings);
        assert_eq!((settings.next.shift_x, settings.next.shift_y), (0.0, 3.0));
        assert_eq!(settings.previous.shift_y, 0.0);
    }

    #[test]
    fn center_canvas_between_takes_the_midpoint_into_the_canvas() {
        let mut settings = OnionSettings::default();
        settings.previous.shift_x = -10.0;
        settings.next.shift_x = 30.0;
        settings.previous.rotation = 170.0;
        settings.next.rotation = -170.0;
        settings.previous.scale = 80.0;
        settings.next.scale = 120.0;
        let mut view = CanvasView {
            pan: (100.0, 50.0),
            rotation: 0.0,
            zoom: 2.0,
        };
        let (rotation, scale) = center_canvas_between(50.0, &mut view, &mut settings);
        // 最短方向で 180°、撮影倍率は 1.0。
        assert!((rotation.abs() - 180.0).abs() < 1e-9);
        assert!((scale - 1.0).abs() < 1e-12);
        assert!((view.pan.0 - 120.0).abs() < 1e-9 && (view.pan.1 - 50.0).abs() < 1e-9);
        assert_eq!(view.rotation, 0.0);
        assert!((settings.tu_tb_scale - 1.0).abs() < 1e-12);
        assert!((settings.previous.scale - 80.0).abs() < 1e-9);
        assert!((settings.next.scale - 120.0).abs() < 1e-9);
        // 中央からの差を -180°（= 180°）で回し、表示の回転 180° で戻すので元と同じ向き。
        assert!(
            (settings.previous.shift_x + 20.0).abs() < 1e-9,
            "{}",
            settings.previous.shift_x
        );
        assert!((settings.next.shift_x - 20.0).abs() < 1e-9);
        assert!(
            (settings.previous.rotation + 10.0).abs() < 1e-9
                || (settings.previous.rotation - 350.0).abs() < 1e-9
        );
    }
}
