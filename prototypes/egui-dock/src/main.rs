//! 画面を Rust にする判断のための試作（egui + egui_dock）。アプリには組み込まない。
//!
//! 確かめること（docs/design-principles.md「画面まで Rust にするかの判断」）：
//! - パネルのフロート・ドッキング・タブ分割（egui_dock）
//! - 1920×1080 のキャンバスでのブラシの遅れ（描いた区間を画像に反映し、テクスチャを部分更新するまで）
//! - 日本語入力（TextEdit に IME の事件を流す）
//! - 筆圧（winit 0.30 は macOS・Windows のペンの筆圧を渡さないので、macOS では NSEvent を直接見る）
//!
//! 使い方：
//! - `cargo run --release`：普通に操作する
//! - `PMA_DEMO_OUT=<dir> cargo run --release`：ドッキングの操作を台本どおりに入力し、毎フレームを PNG で保存して終わる
//! - `cargo run --release -- --bench`：ブラシの計算の時間を測って終わる（画面は出さない）

#![allow(deprecated)] // egui 0.37 で Id::new が非推奨になったが、試作なのでそのまま使う。

mod ime_native;
mod pen_selftest;
mod popout;
mod pressure;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use eframe::egui;
use egui::{Color32, ColorImage, Pos2, Rect, TextureHandle, TextureOptions, Vec2};
use egui_dock::{DockArea, DockState, NodeIndex, Style, TabViewer};

const CANVAS_W: usize = 1920;
const CANVAS_H: usize = 1080;

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
enum Panel {
    Canvas,
    Tools,
    Color,
    Layers,
    Timeline,
    Script,
}

impl Panel {
    fn title(self) -> &'static str {
        match self {
            Panel::Canvas => "キャンバス",
            Panel::Tools => "ツール",
            Panel::Color => "カラー",
            Panel::Layers => "レイヤー",
            Panel::Timeline => "タイムライン",
            Panel::Script => "スクリプト",
        }
    }
}

/// 1920×1080 の描画先。描いた範囲だけテクスチャを更新する。
struct Canvas {
    pixels: Vec<Color32>,
    texture: Option<TextureHandle>,
    dirty: Option<[usize; 4]>,
    last: Option<(Pos2, f32)>,
    color: Color32,
    radius: f32,
    /// 今の線で使った筆圧（線の終わりに範囲を表示する）。
    stroke_pressures: Vec<f32>,
}

impl Canvas {
    fn new() -> Self {
        Self {
            pixels: vec![Color32::WHITE; CANVAS_W * CANVAS_H],
            texture: None,
            dirty: None,
            last: None,
            color: Color32::from_rgb(40, 40, 40),
            radius: 6.0,
            stroke_pressures: Vec::new(),
        }
    }

    fn mark(&mut self, x0: usize, y0: usize, x1: usize, y1: usize) {
        self.dirty = Some(match self.dirty {
            None => [x0, y0, x1, y1],
            Some([a, b, c, d]) => [a.min(x0), b.min(y0), c.max(x1), d.max(y1)],
        });
    }

    fn stamp(&mut self, center: Pos2, radius: f32) {
        let r = radius.max(0.5);
        let x0 = (center.x - r).floor().max(0.0) as usize;
        let y0 = (center.y - r).floor().max(0.0) as usize;
        let x1 = ((center.x + r).ceil() as usize + 1).min(CANVAS_W);
        let y1 = ((center.y + r).ceil() as usize + 1).min(CANVAS_H);
        if x0 >= x1 || y0 >= y1 {
            return;
        }
        let rr = r * r;
        for y in y0..y1 {
            let dy = y as f32 + 0.5 - center.y;
            let row = y * CANVAS_W;
            for x in x0..x1 {
                let dx = x as f32 + 0.5 - center.x;
                if dx * dx + dy * dy <= rr {
                    self.pixels[row + x] = self.color;
                }
            }
        }
        self.mark(x0, y0, x1, y1);
    }

    /// 前の点から今の点まで、筆圧で太さを変えながら円を並べる。
    fn line_to(&mut self, point: Pos2, pressure: f32) {
        let radius = self.radius * pressure.clamp(0.05, 1.0);
        match self.last {
            None => self.stamp(point, radius),
            Some((prev, prev_r)) => {
                let length = (point - prev).length();
                let step = (radius.min(prev_r) * 0.35).max(0.5);
                let count = (length / step).ceil().max(1.0) as usize;
                for i in 1..=count {
                    let t = i as f32 / count as f32;
                    self.stamp(prev + (point - prev) * t, prev_r + (radius - prev_r) * t);
                }
            }
        }
        self.last = Some((point, radius));
    }

    /// 描いた範囲をテクスチャへ送る。最初の1回だけ全体を送る。
    fn upload(&mut self, ctx: &egui::Context) {
        let options = TextureOptions::LINEAR;
        match &mut self.texture {
            None => {
                let image = ColorImage::new([CANVAS_W, CANVAS_H], self.pixels.clone());
                self.texture = Some(ctx.load_texture("canvas", image, options));
                self.dirty = None;
            }
            Some(texture) => {
                if let Some([x0, y0, x1, y1]) = self.dirty.take() {
                    let (w, h) = (x1 - x0, y1 - y0);
                    let mut region = Vec::with_capacity(w * h);
                    for y in y0..y1 {
                        region.extend_from_slice(&self.pixels[y * CANVAS_W + x0..y * CANVAS_W + x1]);
                    }
                    texture.set_partial([x0, y0], ColorImage::new([w, h], region), options);
                }
            }
        }
    }
}

/// ブラシの遅れの記録（描いた区間ごとに、事件を受けてからテクスチャの更新を頼むまで）。
#[derive(Default)]
struct Latency {
    segment_us: Vec<f64>,
    frame_ms: Vec<f64>,
    last_frame: Option<Instant>,
}

impl Latency {
    fn summary(&self) -> String {
        fn pct(values: &[f64], p: f64) -> f64 {
            if values.is_empty() {
                return 0.0;
            }
            let mut v = values.to_vec();
            v.sort_by(|a, b| a.partial_cmp(b).unwrap());
            v[((v.len() - 1) as f64 * p).round() as usize]
        }
        format!(
            "区間 {} 件：中央値 {:.0}µs／95% {:.0}µs　フレーム間隔：中央値 {:.1}ms／95% {:.1}ms",
            self.segment_us.len(),
            pct(&self.segment_us, 0.5),
            pct(&self.segment_us, 0.95),
            pct(&self.frame_ms, 0.5),
            pct(&self.frame_ms, 0.95),
        )
    }
}

struct Viewer<'a> {
    canvas: &'a mut Canvas,
    latency: &'a mut Latency,
    shared: &'a mut popout::Shared,
    popout_request: &'a mut Option<Panel>,
    menu_rect: &'a mut Option<Rect>,
    tab_rects: &'a mut HashMap<Panel, Rect>,
    body_rects: &'a mut HashMap<Panel, Rect>,
    pressure: f32,
    name_rect: &'a mut Option<Rect>,
}

impl TabViewer for Viewer<'_> {
    type Tab = Panel;

    fn id(&mut self, tab: &mut Panel) -> egui::Id {
        egui::Id::new(*tab)
    }

    fn title(&mut self, tab: &mut Panel) -> egui::WidgetText {
        tab.title().into()
    }

    fn on_tab_button(&mut self, tab: &mut Panel, response: &egui::Response) {
        self.tab_rects.insert(*tab, response.rect);
    }

    fn context_menu(&mut self, ui: &mut egui::Ui, tab: &mut Panel, _path: egui_dock::NodePath) {
        if popout::can_pop_out(*tab) {
            let response = ui.button("別の窓に出す");
            *self.menu_rect = Some(response.rect);
            if response.clicked() {
                *self.popout_request = Some(*tab);
                ui.close();
            }
        }
    }

    fn is_closeable(&self, tab: &Panel) -> bool {
        *tab != Panel::Canvas
    }

    fn allowed_in_windows(&self, tab: &mut Panel) -> bool {
        *tab != Panel::Canvas
    }

    fn scroll_bars(&self, tab: &Panel) -> [bool; 2] {
        if *tab == Panel::Canvas {
            [false, false]
        } else {
            [true, true]
        }
    }

    fn ui(&mut self, ui: &mut egui::Ui, tab: &mut Panel) {
        self.body_rects.insert(*tab, ui.max_rect());
        match tab {
            Panel::Canvas => self.canvas_ui(ui),
            Panel::Tools => {
                ui.label("ブラシの太さ");
                ui.add(egui::Slider::new(&mut self.canvas.radius, 1.0..=60.0));
                ui.label(format!("筆圧：{:.2}", self.pressure));
                ui.label(pressure::status());
            }
            Panel::Color => {
                ui.color_edit_button_srgba(&mut self.canvas.color);
                for row in 0..3 {
                    ui.horizontal(|ui| {
                        for col in 0..6 {
                            let hue = (row * 6 + col) as f32 / 18.0;
                            let c: Color32 = egui::ecolor::Hsva::new(hue, 0.7, 0.9, 1.0).into();
                            let (rect, resp) =
                                ui.allocate_exact_size(Vec2::splat(22.0), egui::Sense::click());
                            ui.painter().rect_filled(rect, 3.0, c);
                            if resp.clicked() {
                                self.canvas.color = c;
                            }
                        }
                    });
                }
            }
            Panel::Layers | Panel::Script => {
                popout::small_panel_ui(ui, *tab, self.shared, Some(self.name_rect), None)
            }
            Panel::Timeline => {
                ui.label(self.latency.summary());
                ui.horizontal_wrapped(|ui| {
                    for frame in 1..=48 {
                        let _ = ui.selectable_label(frame == 1, format!("{frame}"));
                    }
                });
            }
        }
    }
}

impl Viewer<'_> {
    fn canvas_ui(&mut self, ui: &mut egui::Ui) {
        self.canvas.upload(ui.ctx());
        let avail = ui.available_size();
        let scale = (avail.x / CANVAS_W as f32).min(avail.y / CANVAS_H as f32).max(0.01);
        let size = Vec2::new(CANVAS_W as f32, CANVAS_H as f32) * scale;
        let (rect, response) = ui.allocate_exact_size(avail, egui::Sense::drag());
        let image_rect = Rect::from_center_size(rect.center(), size);
        ui.painter().rect_filled(rect, 0.0, Color32::from_gray(60));
        if let Some(texture) = &self.canvas.texture {
            ui.painter().image(
                texture.id(),
                image_rect,
                Rect::from_min_max(Pos2::ZERO, Pos2::new(1.0, 1.0)),
                Color32::WHITE,
            );
        }
        if response.dragged() || response.drag_started() {
            // フレーム内のすべての移動を描く（取りこぼすと線が角ばる）。
            let moves: Vec<Pos2> = ui.input(|i| {
                i.events
                    .iter()
                    .filter_map(|e| match e {
                        egui::Event::PointerMoved(p) => Some(*p),
                        _ => None,
                    })
                    .collect()
            });
            let points = if moves.is_empty() {
                response.interact_pointer_pos().into_iter().collect()
            } else {
                moves
            };
            for p in points {
                let started = Instant::now();
                let local = (p - image_rect.min) / scale;
                self.canvas.line_to(Pos2::new(local.x, local.y), self.pressure);
                self.canvas.stroke_pressures.push(self.pressure);
                self.canvas.upload(ui.ctx());
                self.latency
                    .segment_us
                    .push(started.elapsed().as_secs_f64() * 1e6);
            }
        }
        if response.drag_stopped() {
            self.canvas.last = None;
            let p = std::mem::take(&mut self.canvas.stroke_pressures);
            if !p.is_empty() {
                let min = p.iter().cloned().fold(f32::MAX, f32::min);
                let max = p.iter().cloned().fold(f32::MIN, f32::max);
                println!("線：{} 区間、筆圧 {:.2}〜{:.2}（{}）", p.len(), min, max, pressure::status());
            }
        }
    }
}

/// 台本の1手。位置はその時点のタブやパネルの位置から決める。
enum Step {
    Wait(usize),
    /// タブを掴んで、目標まで動かして離す。
    DragTab { tab: Panel, to: Target, frames: usize },
    /// 浮いた窓の題名の帯を掴んで動かす。
    DragWindow { tab: Panel, by: Vec2, frames: usize },
    /// 本体の区切りを掴んで動かす。
    DragFrom { from: Target, by: Vec2, frames: usize },
    Caption(&'static str),
}

#[derive(Clone, Copy)]
enum Target {
    /// パネルの本体の中の割合の位置（0..1）。
    Body(Panel, f32, f32),
    /// パネルの上に出る十字のドロップボタン（中央が 0,0、右が 1,0、下が 0,1）。
    Cross(Panel, f32, f32),
    /// パネルの左の区切りの上。
    LeftEdge(Panel),
}

struct Demo {
    out: PathBuf,
    steps: Vec<Step>,
    index: usize,
    tick: usize,
    pointer: Pos2,
    drag: Option<(Pos2, Pos2)>,
    caption: &'static str,
    frame_no: usize,
    saver: Option<mpsc::Sender<(usize, std::sync::Arc<ColorImage>)>>,
    saver_done: mpsc::Receiver<()>,
    pending_shots: std::sync::Arc<std::sync::atomic::AtomicUsize>,
    finished: bool,
}

fn ease(t: f32) -> f32 {
    t * t * (3.0 - 2.0 * t)
}

impl Demo {
    fn new(out: PathBuf) -> Self {
        std::fs::create_dir_all(&out).unwrap();
        let (tx, rx) = mpsc::channel::<(usize, std::sync::Arc<ColorImage>)>();
        let (done_tx, done_rx) = mpsc::channel();
        let dir = out.clone();
        std::thread::spawn(move || {
            for (n, image) in rx {
                let [w, h] = image.size;
                let mut bytes = Vec::with_capacity(w * h * 4);
                for c in &image.pixels {
                    bytes.extend_from_slice(&c.to_array());
                }
                let path = dir.join(format!("frame_{n:05}.png"));
                image::save_buffer(&path, &bytes, w as u32, h as u32, image::ColorType::Rgba8)
                    .unwrap();
            }
            let _ = done_tx.send(());
        });
        use Panel::*;
        let steps = vec![
            Step::Caption("egui + egui_dock：初期配置。キャンバス（1920×1080）に描く"),
            Step::Wait(15),
            Step::DragFrom { from: Target::Body(Canvas, 0.25, 0.35), by: Vec2::new(120.0, 90.0), frames: 14 },
            Step::DragFrom { from: Target::Body(Canvas, 0.45, 0.35), by: Vec2::new(-60.0, 110.0), frames: 14 },
            Step::DragFrom { from: Target::Body(Canvas, 0.55, 0.38), by: Vec2::new(140.0, 20.0), frames: 14 },
            Step::Wait(15),
            Step::Caption("「カラー」タブをキャンバスの右へドラッグ → 右に分割"),
            Step::DragTab { tab: Color, to: Target::Cross(Canvas, 1.0, 0.0), frames: 45 },
            Step::Wait(25),
            Step::Caption("「タイムライン」タブを「レイヤー」の中央へ → タブとしてまとめる"),
            Step::DragTab { tab: Timeline, to: Target::Cross(Layers, 0.0, 0.0), frames: 45 },
            Step::Wait(25),
            Step::Caption("「スクリプト」タブを十字のボタンの外で離す → フロート（浮いた窓）"),
            Step::DragTab { tab: Script, to: Target::Body(Canvas, 0.2, 0.2), frames: 45 },
            Step::Wait(20),
            Step::Caption("浮いた窓を動かす"),
            Step::DragWindow { tab: Script, by: Vec2::new(420.0, 60.0), frames: 35 },
            Step::Wait(20),
            Step::Caption("浮いた窓のタブをキャンバスの下へ戻す → 下に分割してドッキング"),
            Step::DragTab { tab: Script, to: Target::Cross(Canvas, 0.0, 1.0), frames: 45 },
            Step::Wait(25),
            Step::Caption("「ツール」タブをキャンバスの左へ → 左に分割"),
            Step::DragTab { tab: Tools, to: Target::Cross(Canvas, -1.0, 0.0), frames: 45 },
            Step::Wait(25),
            Step::Caption("区切りを動かして大きさを変える"),
            Step::DragFrom { from: Target::LeftEdge(Color), by: Vec2::new(220.0, 0.0), frames: 35 },
            Step::Wait(40),
        ];
        Self {
            out,
            steps,
            index: 0,
            tick: 0,
            pointer: Pos2::new(640.0, 400.0),
            drag: None,
            caption: "",
            frame_no: 0,
            saver: Some(tx),
            saver_done: done_rx,
            pending_shots: Default::default(),
            finished: false,
        }
    }

    fn resolve(target: Target, bodies: &HashMap<Panel, Rect>, _screen: Rect) -> Option<Pos2> {
        match target {
            Target::Body(panel, fx, fy) => bodies.get(&panel).map(|r| {
                Pos2::new(r.min.x + r.width() * fx, r.min.y + r.height() * fy)
            }),
            Target::Cross(panel, dx, dy) => bodies.get(&panel).map(|r| {
                // egui_dock の resolve_icon_based と同じ寸法（Style::from_egui の間隔は icon_spacing）。
                let spacing = 4.0;
                let r = r.shrink(spacing);
                let side = ((r.width() - 2.0 * spacing) / 3.0)
                    .min((r.height() - 2.0 * spacing) / 3.0)
                    .min(100.0);
                r.center() + Vec2::new(dx, dy) * (side + spacing)
            }),
            Target::LeftEdge(panel) => bodies
                .get(&panel)
                .map(|r| Pos2::new(r.min.x - 4.0, r.center().y)),
        }
    }

    /// この手で入れる事件を返す。
    fn events(
        &mut self,
        tabs: &HashMap<Panel, Rect>,
        bodies: &HashMap<Panel, Rect>,
        screen: Rect,
    ) -> Vec<egui::Event> {
        let mut events = Vec::new();
        let press = |pos: Pos2, pressed: bool| egui::Event::PointerButton {
            pos,
            button: egui::PointerButton::Primary,
            pressed,
            modifiers: egui::Modifiers::NONE,
        };
        loop {
            let Some(step) = self.steps.get(self.index) else {
                self.finished = true;
                return events;
            };
            let (frames, start, end) = match step {
                Step::Caption(text) => {
                    self.caption = text;
                    self.index += 1;
                    continue;
                }
                Step::Wait(n) => {
                    self.tick += 1;
                    if self.tick >= *n {
                        self.tick = 0;
                        self.index += 1;
                    }
                    return events;
                }
                Step::DragTab { tab, to, frames } => {
                    let start = self.drag.map(|d| d.0).or_else(|| tabs.get(tab).map(|r| r.center()));
                    let end = self.drag.map(|d| d.1).or_else(|| Self::resolve(*to, bodies, screen));
                    (*frames, start, end)
                }
                Step::DragWindow { tab, by, frames } => {
                    // egui_dock の浮いた窓は題名の帯を持たず、タブの帯の空いた所を掴んで動かす。
                    let start = self.drag.map(|d| d.0).or_else(|| {
                        tabs.get(tab).map(|r| Pos2::new(r.max.x + 16.0, r.center().y))
                    });
                    let end = self.drag.map(|d| d.1).or_else(|| start.map(|s| s + *by));
                    (*frames, start, end)
                }
                Step::DragFrom { from, by, frames } => {
                    let start = self.drag.map(|d| d.0).or_else(|| Self::resolve(*from, bodies, screen));
                    let end = self.drag.map(|d| d.1).or_else(|| start.map(|s| s + *by));
                    (*frames, start, end)
                }
            };
            let (Some(start), Some(end)) = (start, end) else {
                eprintln!("台本の位置が見つからないので飛ばす（手 {}）", self.index);
                self.index += 1;
                self.tick = 0;
                continue;
            };
            self.drag = Some((start, end));
            // 0..6：掴みに行く、6：押す、7..7+frames：動かす、最後：離す
            let approach = 6;
            if self.tick < approach {
                let t = ease((self.tick + 1) as f32 / approach as f32);
                self.pointer = self.pointer + (start - self.pointer) * t;
                events.push(egui::Event::PointerMoved(self.pointer));
            } else if self.tick == approach {
                self.pointer = start;
                events.push(egui::Event::PointerMoved(start));
                events.push(press(start, true));
            } else if self.tick <= approach + frames {
                let t = ease((self.tick - approach) as f32 / frames as f32);
                self.pointer = start + (end - start) * t;
                events.push(egui::Event::PointerMoved(self.pointer));
            } else {
                self.pointer = end;
                events.push(press(end, false));
                self.drag = None;
                self.tick = 0;
                self.index += 1;
                return events;
            }
            self.tick += 1;
            return events;
        }
    }
}

struct App {
    dock: DockState<Panel>,
    canvas: Canvas,
    latency: Latency,
    shared: Arc<Mutex<popout::Shared>>,
    popouts: popout::Popouts,
    popout_request: Option<Panel>,
    menu_rect: Option<Rect>,
    /// PMA_POPOUT_OUT：別の窓に出す台本。
    pop_demo: Option<popout::PopDemo>,
    tab_rects: HashMap<Panel, Rect>,
    body_rects: HashMap<Panel, Rect>,
    demo: Option<Demo>,
    /// --latency：1秒に240回のペンの移動（1フレームに4回）を入れ続け、フレームの間隔と CPU 時間を測る。
    latency_run: Option<usize>,
    cpu_ms: Vec<f64>,
    /// --pen-selftest：自分の事件の列に筆圧つきのペンの事件を入れ、線の筆圧を表示して終わる。
    pen_selftest: Option<usize>,
    /// --ime-selftest：レイヤー名の欄に、IME の事件（変換中→確定）を入れて結果を表示する。
    ime_selftest: Option<usize>,
    name_rect: Option<Rect>,
    ime_native: bool,
}

impl App {
    fn new(cc: &eframe::CreationContext<'_>, demo: Option<PathBuf>, latency_run: bool) -> Self {
        install_japanese_font(&cc.egui_ctx);
        pressure::install();
        use Panel::*;
        let mut dock = DockState::new(vec![Canvas]);
        let tree = dock.main_surface_mut();
        let [center, _left] = tree.split_left(NodeIndex::root(), 0.18, vec![Tools, Color]);
        let [center, _right] = tree.split_right(center, 0.78, vec![Layers, Script]);
        let [_center, _bottom] = tree.split_below(center, 0.75, vec![Timeline]);
        Self {
            dock,
            canvas: self::Canvas::new(),
            latency: Latency::default(),
            shared: Arc::new(Mutex::new(popout::Shared {
                layer_names: vec!["線画".into(), "影".into(), "Layer 3".into()],
                script: "# ここに日本語を入力して確かめる\n".into(),
                widgets: HashMap::new(),
                windows: HashMap::new(),
                inject: HashMap::new(),
            })),
            popouts: popout::Popouts::new(),
            popout_request: None,
            menu_rect: None,
            pop_demo: std::env::var_os("PMA_POPOUT_OUT").map(|d| popout::PopDemo::new(PathBuf::from(d))),
            tab_rects: HashMap::new(),
            body_rects: HashMap::new(),
            demo: demo.map(Demo::new),
            latency_run: latency_run.then_some(0),
            cpu_ms: Vec::new(),
            pen_selftest: std::env::args().any(|a| a == "--pen-selftest").then_some(0),
            ime_selftest: std::env::args()
                .any(|a| a == "--ime-selftest" || a == "--ime-native")
                .then_some(0),
            ime_native: std::env::args().any(|a| a == "--ime-native"),
            name_rect: None,
        }
    }
}

impl eframe::App for App {
    fn raw_input_hook(&mut self, ctx: &egui::Context, raw_input: &mut egui::RawInput) {
        // eframe はすべての窓（別の窓も）でこれを呼ぶ。別の窓は台本の事件を入れるだけにする。
        if raw_input.viewport_id != egui::ViewportId::ROOT {
            if self.pop_demo.is_some() {
                raw_input.events.retain(|e| {
                    !matches!(
                        e,
                        egui::Event::PointerMoved(_)
                            | egui::Event::PointerButton { .. }
                            | egui::Event::PointerGone
                            | egui::Event::MouseMoved(_)
                    )
                });
                let id = raw_input.viewport_id;
                if let Some(inner) = raw_input.viewports.get(&id).and_then(|v| v.inner_rect) {
                    let injected = self.shared.lock().unwrap().inject.remove(&id).unwrap_or_default();
                    raw_input.events.extend(popout::to_events(injected, inner.min));
                }
            }
            return;
        }
        if self.pen_selftest.is_some() && std::env::var_os("PMA_DEBUG").is_some() {
            for e in &raw_input.events {
                if matches!(e, egui::Event::PointerButton { .. }) {
                    eprintln!("egui 事件：{e:?}　筆圧 {:?}", pressure::current());
                }
            }
        }
        let now = Instant::now();
        if let Some(last) = self.latency.last_frame.replace(now) {
            if self.canvas.last.is_some() {
                self.latency
                    .frame_ms
                    .push(now.duration_since(last).as_secs_f64() * 1e3);
            }
        }
        if let Some(frame) = self.latency_run.as_mut() {
            if let Some(body) = self.body_rects.get(&Panel::Canvas).copied() {
                *frame += 1;
                let f = *frame;
                let stroke_len = 60;
                let k = f % stroke_len;
                let stroke = f / stroke_len;
                let at = |i: usize| {
                    let t = i as f32 / (stroke_len * 4) as f32;
                    Pos2::new(
                        body.min.x + body.width() * (0.1 + 0.8 * t),
                        body.min.y + body.height() * (0.15 + 0.03 * (stroke % 20) as f32)
                            + 40.0 * (t * 12.0).sin(),
                    )
                };
                let button = |pos, pressed| egui::Event::PointerButton {
                    pos,
                    button: egui::PointerButton::Primary,
                    pressed,
                    modifiers: egui::Modifiers::NONE,
                };
                raw_input.events.clear();
                if k == 0 {
                    raw_input.events.push(egui::Event::PointerMoved(at(0)));
                    raw_input.events.push(button(at(0), true));
                } else if k == stroke_len - 1 {
                    raw_input.events.push(button(at(k * 4), false));
                } else {
                    for j in 1..=4 {
                        raw_input.events.push(egui::Event::PointerMoved(at((k - 1) * 4 + j)));
                    }
                }
            }
        }
        if let (Some(n), Some(rect)) = (self.ime_selftest.as_mut(), self.name_rect) {
            *n += 1;
            let at = rect.right_center() - Vec2::new(6.0, 0.0);
            let button = |pressed| egui::Event::PointerButton {
                pos: at,
                button: egui::PointerButton::Primary,
                pressed,
                modifiers: egui::Modifiers::NONE,
            };
            let preedit = |text: &str, range: std::ops::Range<usize>| {
                egui::Event::Ime(egui::ImeEvent::Preedit {
                    text: text.into(),
                    active_range_chars: Some(range),
                })
            };
            match *n {
                10 => raw_input.events.extend([egui::Event::PointerMoved(at), button(true)]),
                11 => raw_input.events.push(button(false)),
                // --ime-selftest：egui の事件として直接入れる。--ime-native：OS の窓口から入れる（ui で）。
                14 if !self.ime_native => raw_input.events.push(preedit("せんが", 3..3)),
                16 if !self.ime_native => raw_input.events.push(preedit("線画", 0..2)),
                18 if !self.ime_native => {
                    raw_input.events.push(egui::Event::Ime(egui::ImeEvent::Commit("線画".into())))
                }
                20 if !self.ime_native => raw_input.events.push(preedit("", 0..0)),
                _ => {}
            }
        }
        if self.pop_demo.is_some() {
            raw_input.events.retain(|e| {
                !matches!(
                    e,
                    egui::Event::PointerMoved(_)
                        | egui::Event::PointerButton { .. }
                        | egui::Event::PointerGone
                        | egui::Event::MouseMoved(_)
                )
            });
            let id = raw_input.viewport_id;
            let inner = |id| raw_input.viewports.get(&id).and_then(|v| v.inner_rect);
            let outer = raw_input.viewports.get(&egui::ViewportId::ROOT).and_then(|v| v.outer_rect);
            if id == egui::ViewportId::ROOT {
                if let (Some(main_inner), Some(main_outer)) = (inner(id), outer) {
                    let demo = self.pop_demo.as_mut().unwrap();
                    let injected = demo.step(
                        ctx,
                        &self.popouts,
                        main_outer,
                        main_inner,
                        &self.tab_rects,
                        self.menu_rect,
                        &self.shared,
                    );
                    raw_input.events.extend(popout::to_events(injected, main_inner.min));
                }
            }
        }
        if self.demo.is_some() {
            let screen = ctx.content_rect();
            let demo = self.demo.as_mut().unwrap();
            // 本物のマウスの事件は捨てて、台本の事件だけを入れる。
            raw_input.events.retain(|e| {
                !matches!(
                    e,
                    egui::Event::PointerMoved(_)
                        | egui::Event::PointerButton { .. }
                        | egui::Event::PointerGone
                        | egui::Event::MouseMoved(_)
                )
            });
            let events = demo.events(&self.tab_rects, &self.body_rects, screen);
            raw_input.events.extend(events);
        }
    }

    fn ui(&mut self, ui: &mut egui::Ui, frame: &mut eframe::Frame) {
        let ctx = ui.ctx().clone();
        if let Some(n) = self.ime_selftest {
            #[cfg(target_os = "macos")]
            if self.ime_native && (n == 14 || n == 16 || n == 18) {
                use objc2::MainThreadMarker;
                use objc2_app_kit::NSApplication;
                let app = NSApplication::sharedApplication(MainThreadMarker::new().unwrap());
                let window = app
                    .windows()
                    .iter()
                    .filter(|w| w.isVisible())
                    .max_by(|a, b| {
                        let (fa, fb) = (a.frame(), b.frame());
                        (fa.size.width * fa.size.height).total_cmp(&(fb.size.width * fb.size.height))
                    })
                    .unwrap();
                let view = window.contentView().unwrap();
                match n {
                    14 => ime_native::marked(&view, "せんが"),
                    16 => ime_native::marked(&view, "線画"),
                    _ => ime_native::insert(&view, "線画"),
                }
            }
            if n == 15 || n == 17 {
                println!("IME 変換中（{n}）：{:?}", self.shared.lock().unwrap().layer_names[0]);
            }
            if n >= 24 {
                println!("IME 確定後：{:?}", self.shared.lock().unwrap().layer_names[0]);
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
            ctx.request_repaint();
        }
        if let Some(n) = self.pen_selftest.as_mut() {
            *n += 1;
            // 窓が出て配置が決まるまで少し待つ。キャンバスの中（窓の 35%, 35% あたり）から描く。
            if *n > 30 {
                match pen_selftest::step(*n - 30, 0.35, 0.35) {
                    Some(true) => {}
                    Some(false) => println!("ペンの事件を入れられない"),
                    None if *n > 120 => ctx.send_viewport_cmd(egui::ViewportCommand::Close),
                    None => {}
                }
            }
            ctx.request_repaint();
        }
        if let Some(n) = self.latency_run {
            if let Some(cpu) = frame.info().cpu_usage {
                if n > 0 {
                    self.cpu_ms.push(cpu as f64 * 1e3);
                }
            }
            if n >= 1200 {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
            ctx.request_repaint();
        }
        let pressure = pressure::current().unwrap_or(1.0);
        self.tab_rects.clear();
        let mut shared = self.shared.lock().unwrap();
        let mut viewer = Viewer {
            canvas: &mut self.canvas,
            latency: &mut self.latency,
            shared: &mut shared,
            popout_request: &mut self.popout_request,
            menu_rect: &mut self.menu_rect,
            tab_rects: &mut self.tab_rects,
            body_rects: &mut self.body_rects,
            pressure,
            name_rect: &mut self.name_rect,
        };
        DockArea::new(&mut self.dock)
            .style(Style::from_egui(ui.style().as_ref()))
            .show_leaf_collapse_buttons(false)
            .show_inside(ui, &mut viewer);
        drop(shared);

        let (main_outer, main_inner) = ctx.input(|i| (i.viewport().outer_rect, i.viewport().inner_rect));
        if let Some(panel) = self.popout_request.take() {
            let offset = main_inner.map(|r| r.min.to_vec2()).unwrap_or_default();
            let at = self.tab_rects.get(&panel).map(|r| r.left_bottom() + offset).unwrap_or(Pos2::new(200.0, 200.0));
            self.popouts.pop_out(&mut self.dock, panel, at);
            self.menu_rect = None;
        }
        self.popouts.show(&ctx, &mut self.dock, &self.shared, main_outer, main_inner);
        if self.popouts.hovering {
            popout::paint_drop_hint(&ctx, ctx.content_rect());
        }
        if let (Some(demo), Some(outer), Some(inner)) = (&mut self.pop_demo, main_outer, main_inner) {
            if demo.capture(&ctx, &self.popouts, &self.shared, (outer, inner), self.popouts.hovering) {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
            ctx.request_repaint();
        }

        if let Some(demo) = &mut self.demo {
            // 台本の指先と説明を一番上に描く。
            let painter = ctx.layer_painter(egui::LayerId::new(
                egui::Order::Debug,
                egui::Id::new("demo_overlay"),
            ));
            let p = demo.pointer;
            let arrow = vec![
                p,
                p + Vec2::new(0.0, 18.0),
                p + Vec2::new(4.5, 14.0),
                p + Vec2::new(8.0, 21.0),
                p + Vec2::new(10.5, 20.0),
                p + Vec2::new(7.0, 13.0),
                p + Vec2::new(12.5, 13.0),
            ];
            painter.add(egui::Shape::convex_polygon(
                arrow,
                if demo.drag.is_some() { Color32::from_rgb(255, 210, 60) } else { Color32::WHITE },
                egui::Stroke::new(1.2, Color32::BLACK),
            ));
            let screen = ctx.content_rect();
            let band = Rect::from_min_size(
                Pos2::new(screen.min.x, screen.max.y - 34.0),
                Vec2::new(screen.width(), 34.0),
            );
            painter.rect_filled(band, 0.0, Color32::from_black_alpha(200));
            painter.text(
                band.left_center() + Vec2::new(12.0, 0.0),
                egui::Align2::LEFT_CENTER,
                demo.caption,
                egui::FontId::proportional(17.0),
                Color32::WHITE,
            );

            use std::sync::atomic::Ordering;
            if demo.finished {
                if demo.pending_shots.load(Ordering::SeqCst) == 0 {
                    if let Some(saver) = demo.saver.take() {
                        drop(saver);
                        let _ = demo.saver_done.recv();
                        println!("{} フレームを {} に保存", demo.frame_no, demo.out.display());
                        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                    }
                }
            } else if let Some(saver) = demo.saver.clone() {
                let n = demo.frame_no;
                demo.frame_no += 1;
                let pending = demo.pending_shots.clone();
                pending.fetch_add(1, Ordering::SeqCst);
                let repaint = ctx.clone();
                ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(
                    egui::ScreenshotCallback::new(move |image| {
                        let _ = saver.send((n, image));
                        pending.fetch_sub(1, Ordering::SeqCst);
                        repaint.request_repaint();
                    }),
                ));
            }
            ctx.request_repaint();
        } else if self.canvas.last.is_some() {
            ctx.request_repaint();
        }
    }

    fn on_exit(&mut self) {
        if !self.latency.segment_us.is_empty() {
            println!("ブラシ：{}", self.latency.summary());
        }
        if !self.cpu_ms.is_empty() {
            let mut v = self.cpu_ms.clone();
            v.sort_by(|a, b| a.partial_cmp(b).unwrap());
            println!(
                "1フレームの CPU 時間（{} フレーム）：中央値 {:.2}ms／95% {:.2}ms",
                v.len(),
                v[v.len() / 2],
                v[v.len() * 95 / 100]
            );
        }
    }
}

fn install_japanese_font(ctx: &egui::Context) {
    let candidates = [
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "C:\\Windows\\Fonts\\YuGothM.ttc",
        "C:\\Windows\\Fonts\\meiryo.ttc",
    ];
    let Some(bytes) = candidates.iter().find_map(|p| std::fs::read(p).ok()) else {
        eprintln!("日本語のフォントが見つからない");
        return;
    };
    let mut fonts = egui::FontDefinitions::default();
    fonts
        .font_data
        .insert("jp".into(), std::sync::Arc::new(egui::FontData::from_owned(bytes)));
    for family in [egui::FontFamily::Proportional, egui::FontFamily::Monospace] {
        fonts.families.entry(family).or_default().push("jp".into());
    }
    ctx.set_fonts(fonts);
}

/// 画面を出さずに、ブラシの計算（円を並べる＋テクスチャへ送る部分の写し）の時間を測る。
fn bench() {
    let mut canvas = Canvas::new();
    let mut times = Vec::new();
    for stroke in 0..20 {
        canvas.last = None;
        for i in 0..200 {
            let t = i as f32 / 200.0;
            let p = Pos2::new(100.0 + 1700.0 * t, 200.0 + 40.0 * stroke as f32 + 120.0 * (t * 12.0).sin());
            let started = Instant::now();
            canvas.line_to(p, 0.3 + 0.7 * (t * 3.0).sin().abs());
            // 部分更新に送る領域の写しまで（GPU への送信は除く）。
            if let Some([x0, y0, x1, y1]) = canvas.dirty.take() {
                let mut region = Vec::with_capacity((x1 - x0) * (y1 - y0));
                for y in y0..y1 {
                    region.extend_from_slice(&canvas.pixels[y * CANVAS_W + x0..y * CANVAS_W + x1]);
                }
                std::hint::black_box(region);
            }
            times.push(started.elapsed().as_secs_f64() * 1e6);
        }
    }
    times.sort_by(|a, b| a.partial_cmp(b).unwrap());
    println!(
        "egui 試作のブラシ（1920×1080、太さ6、区間 {} 件）：中央値 {:.1}µs／95% {:.1}µs／最大 {:.1}µs",
        times.len(),
        times[times.len() / 2],
        times[times.len() * 95 / 100],
        times[times.len() - 1]
    );
}

fn main() -> eframe::Result {
    if std::env::args().any(|a| a == "--bench") {
        bench();
        return Ok(());
    }
    let demo = std::env::var_os("PMA_DEMO_OUT").map(PathBuf::from);
    let latency_run = std::env::args().any(|a| a == "--latency");
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1280.0, 800.0])
            .with_title("PMA 試作：egui + egui_dock"),
        ..Default::default()
    };
    eframe::run_native(
        "pma-proto-egui-dock",
        options,
        Box::new(move |cc| Ok(Box::new(App::new(cc, demo, latency_run)))),
    )
}
