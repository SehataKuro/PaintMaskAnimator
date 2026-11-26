//! パネルを別の OS の窓に出す（egui の deferred viewport）。egui_dock の浮いた窓は本体の窓の中だけなので、
//! タブの右クリックの「別の窓に出す」で、そのパネルを DockState から外して自分の viewport に描く。
//!
//! 戻し方は3つ：窓の「メインの窓に戻す」、窓を閉じる、一度メインの窓の外へ出した窓をメインの窓の上まで
//! 動かして止める（止まったらもとの場所へ戻る。動かしている間はメインの窓に戻る先を出す）。
//!
//! `PMA_POPOUT_OUT=<dir>`：台本どおりに操作し、窓ごとに自分の写し（ViewportCommand::Screenshot）と、
//! 画面の上の窓の位置（frames.jsonl）を保存して終わる。動画は compose_popout.py で重ねて作る。

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc, Mutex};

use eframe::egui;
use egui::{Color32, ColorImage, Pos2, Rect, Vec2, ViewportId};
use egui_dock::{DockState, NodePath};

use crate::Panel;

/// 本体と別の窓の両方が触るパネルの中身。
pub struct Shared {
    pub layer_names: Vec<String>,
    pub script: String,
    /// 別の窓の中の部品の位置（画面の座標）。台本が使う。
    pub widgets: HashMap<String, Rect>,
    /// 窓ごとの外枠と中身の位置（画面の座標、point）。
    pub windows: HashMap<ViewportId, (Rect, Rect)>,
    /// 台本が別の窓に入れる事件（画面の座標）。
    pub inject: HashMap<ViewportId, Vec<Injected>>,
}

#[derive(Clone)]
pub enum Injected {
    Move(Pos2),
    Button(Pos2, bool),
    Secondary(Pos2, bool),
    Enter,
    Text(String),
}

pub fn can_pop_out(panel: Panel) -> bool {
    matches!(panel, Panel::Layers | Panel::Script)
}

/// レイヤー・スクリプトの中身（本体のタブでも別の窓でも同じものを描く）。
pub fn small_panel_ui(
    ui: &mut egui::Ui,
    panel: Panel,
    shared: &mut Shared,
    name_rect: Option<&mut Option<Rect>>,
    edit_key: Option<&str>,
) {
    match panel {
        Panel::Layers => {
            ui.label("レイヤー名（日本語入力の確認）");
            let mut first = None;
            for (i, name) in shared.layer_names.iter_mut().enumerate() {
                let response = ui.text_edit_singleline(name);
                if i == 0 {
                    first = Some(response.rect);
                }
            }
            if let Some(slot) = name_rect {
                *slot = first;
            }
        }
        Panel::Script => {
            ui.label("スクリプト（日本語入力の確認）");
            let response = ui.add(
                egui::TextEdit::multiline(&mut shared.script)
                    .desired_rows(6)
                    .code_editor(),
            );
            if let Some(key) = edit_key {
                let offset = ui.ctx().input(|i| i.viewport().inner_rect).map(|r| r.min.to_vec2());
                if let Some(offset) = offset {
                    shared.widgets.insert(key.into(), response.rect.translate(offset));
                }
            }
        }
        _ => {}
    }
}

pub struct Popout {
    pub panel: Panel,
    pub id: ViewportId,
    /// 出す前に同じ葉にいたほかのタブ。戻すときはこのどれかがいる葉へ入れる
    /// （NodeIndex は葉が消えると付け直されるので覚えない）。
    peers: Vec<Panel>,
    start: Pos2,
    back: Arc<AtomicBool>,
    last_outer: Option<Rect>,
    /// 一度メインの窓の外へ完全に出たか（出てからでないと、メインの窓の上で止めても戻さない）。
    was_outside: bool,
    moved: bool,
    still: usize,
}

pub struct Popouts {
    pub list: Vec<Popout>,
    /// メインの窓の上に戻す先を出しているか。
    pub hovering: bool,
}

impl Popouts {
    pub fn new() -> Self {
        Self { list: Vec::new(), hovering: false }
    }

    /// タブを DockState から外して、別の窓にする。
    pub fn pop_out(&mut self, dock: &mut DockState<Panel>, panel: Panel, at: Pos2) {
        let Some(path) = dock.find_tab(&panel) else { return };
        let leaf = NodePath { surface: path.surface, node: path.node };
        let peers = dock
            .leaf(leaf)
            .map(|l| l.tabs.iter().copied().filter(|t| *t != panel).collect())
            .unwrap_or_default();
        if dock.remove_tab(path).is_none() {
            return;
        }
        println!("別の窓に出す：{}", panel.title());
        self.list.push(Popout {
            panel,
            id: ViewportId::from_hash_of(("popout", panel)),
            peers,
            start: at,
            back: Arc::new(AtomicBool::new(false)),
            last_outer: None,
            was_outside: false,
            moved: false,
            still: 0,
        });
    }

    /// 毎フレーム：別の窓を描き、戻すものを戻す。
    pub fn show(
        &mut self,
        ctx: &egui::Context,
        dock: &mut DockState<Panel>,
        shared: &Arc<Mutex<Shared>>,
        main_outer: Option<Rect>,
        main_inner: Option<Rect>,
    ) {
        self.hovering = false;
        let windows = shared.lock().unwrap().windows.clone();
        let mut returning = Vec::new();
        for (index, popout) in self.list.iter_mut().enumerate() {
            let mut over_main = false;
            if let (Some((outer, _)), Some(main)) = (windows.get(&popout.id), main_inner) {
                let outer = *outer;
                if let Some(last) = popout.last_outer {
                    if (last.min - outer.min).length() > 0.5 {
                        popout.moved = true;
                        popout.still = 0;
                    } else {
                        popout.still += 1;
                    }
                }
                popout.last_outer = Some(outer);
                popout.was_outside |= main_outer.is_some_and(|m| !m.intersects(outer));
                over_main = popout.was_outside && main.shrink(40.0).contains(outer.center());
            }
            if popout.moved && over_main {
                if popout.still >= 15 {
                    println!("メインの窓の上で止めたので戻す：{}", popout.panel.title());
                    returning.push(index);
                } else {
                    self.hovering = true;
                }
            }
            if popout.back.load(Ordering::SeqCst) {
                println!("メインの窓に戻す：{}", popout.panel.title());
                returning.push(index);
            }
            let panel = popout.panel;
            let id = popout.id;
            let shared = shared.clone();
            let back = popout.back.clone();
            let builder = egui::ViewportBuilder::default()
                .with_title(format!("{} — PMA 試作（別の窓）", panel.title()))
                .with_position(popout.start)
                .with_inner_size([380.0, 300.0]);
            ctx.show_viewport_deferred(id, builder, move |ui, _class| {
                let ctx = ui.ctx().clone();
                let mut shared = shared.lock().unwrap();
                let (outer, inner) = ctx.input(|i| (i.viewport().outer_rect, i.viewport().inner_rect));
                if let (Some(outer), Some(inner)) = (outer, inner) {
                    shared.windows.insert(id, (outer, inner));
                }
                let offset = inner.map(|r| r.min.to_vec2()).unwrap_or_default();
                egui::Frame::central_panel(ui.style()).show(ui, |ui| {
                    ui.set_min_size(ui.available_size());
                    ui.horizontal(|ui| {
                        ui.heading(panel.title());
                        let response = ui.button("メインの窓に戻す");
                        shared
                            .widgets
                            .insert(format!("back:{}", panel.title()), response.rect.translate(offset));
                        if response.clicked() {
                            back.store(true, Ordering::SeqCst);
                        }
                    });
                    ui.separator();
                    let key = format!("edit:{}", panel.title());
                    small_panel_ui(ui, panel, &mut shared, None, Some(&key));
                });
                if ctx.input(|i| i.viewport().close_requested()) {
                    back.store(true, Ordering::SeqCst);
                }
                ctx.request_repaint();
            });
        }
        returning.sort_unstable();
        returning.dedup();
        for index in returning.into_iter().rev() {
            let popout = self.list.remove(index);
            shared.lock().unwrap().windows.remove(&popout.id);
            let home = popout
                .peers
                .iter()
                .find_map(|peer| dock.find_tab(peer))
                .and_then(|path| dock.leaf_mut(NodePath { surface: path.surface, node: path.node }).ok());
            if let Some(leaf) = home {
                leaf.append_tab(popout.panel);
                let last = leaf.tabs.len() - 1;
                let _ = leaf.set_active_tab(last);
            } else if let Some(canvas) = dock.find_main_surface_tab(&Panel::Canvas) {
                // 同じ葉のタブが残っていない（その葉ごと消えた）ときは、キャンバスの右に列を作る。
                dock.main_surface_mut().split_right(canvas.0, 0.78, vec![popout.panel]);
            } else {
                dock.push_to_first_leaf(popout.panel);
            }
        }
    }

    pub fn id_of(&self, panel: Panel) -> Option<ViewportId> {
        self.list.iter().find(|p| p.panel == panel).map(|p| p.id)
    }
}

/// メインの窓の上に「離すと戻る」を出す。
pub fn paint_drop_hint(ctx: &egui::Context, rect: Rect) {
    let painter = ctx.layer_painter(egui::LayerId::new(egui::Order::Foreground, egui::Id::new("popout_hint")));
    let r = rect.shrink(24.0);
    painter.rect_filled(r, 8.0, Color32::from_rgba_unmultiplied(40, 110, 230, 70));
    painter.rect_stroke(r, 8.0, egui::Stroke::new(3.0, Color32::from_rgb(60, 140, 255)), egui::StrokeKind::Inside);
    painter.text(
        r.center(),
        egui::Align2::CENTER_CENTER,
        "ここで止めると、もとの場所に戻る",
        egui::FontId::proportional(28.0),
        Color32::WHITE,
    );
}

// ---------------------------------------------------------------- 台本

#[derive(Clone, Copy)]
enum Win {
    Main,
    Pop(Panel),
}

#[derive(Clone, Copy)]
enum Spot {
    Tab(Panel),
    Menu,
    Edit(Panel),
    Back(Panel),
}

#[derive(Clone, Copy)]
enum Place {
    /// メインの窓の外。別の画面があればその画面、無ければメインの窓の右（dy はメインの窓の上端から）。
    OtherScreen(f32),
    /// メインの窓の真ん中。
    MainCenter,
}

enum Act {
    Caption(&'static str),
    Wait(usize),
    Click { win: Win, spot: Spot, secondary: bool },
    MoveWindow { panel: Panel, to: Place, frames: usize },
    Type(Panel, &'static str),
    Report,
}

pub struct PopDemo {
    out: PathBuf,
    acts: Vec<Act>,
    index: usize,
    tick: usize,
    /// 指先の位置（画面の座標）。
    pub pointer: Pos2,
    pub dragging: bool,
    caption: &'static str,
    from: Option<Pos2>,
    frame_no: usize,
    saver: Option<mpsc::Sender<(String, Arc<ColorImage>)>>,
    saver_done: mpsc::Receiver<()>,
    pending: Arc<AtomicUsize>,
    log: Vec<String>,
    pub finished: bool,
    pub report: Vec<String>,
    /// 位置が見つからず飛ばした手の数（0 でなければ録画は台本どおりでない）。
    skipped: usize,
}

fn ease(t: f32) -> f32 {
    t * t * (3.0 - 2.0 * t)
}

impl PopDemo {
    pub fn new(out: PathBuf) -> Self {
        std::fs::create_dir_all(&out).unwrap();
        let (tx, rx) = mpsc::channel::<(String, Arc<ColorImage>)>();
        let (done_tx, done_rx) = mpsc::channel();
        let dir = out.clone();
        std::thread::spawn(move || {
            for (name, image) in rx {
                let [w, h] = image.size;
                let mut bytes = Vec::with_capacity(w * h * 4);
                for c in &image.pixels {
                    bytes.extend_from_slice(&c.to_array());
                }
                image::save_buffer(dir.join(name), &bytes, w as u32, h as u32, image::ColorType::Rgba8).unwrap();
            }
            let _ = done_tx.send(());
        });
        use Panel::*;
        let acts = vec![
            Act::Caption("egui：パネルを別の OS の窓に出す。「スクリプト」タブを右クリック"),
            Act::Wait(20),
            Act::Click { win: Win::Main, spot: Spot::Tab(Script), secondary: true },
            Act::Wait(8),
            Act::Caption("「別の窓に出す」→ スクリプトが自分の OS の窓になる"),
            Act::Click { win: Win::Main, spot: Spot::Menu, secondary: false },
            Act::Wait(20),
            Act::Caption("別の窓をメインの窓の外（もう1つの画面）へ動かす（台本は OS の窓を動かす命令で動かす）"),
            Act::MoveWindow { panel: Script, to: Place::OtherScreen(20.0), frames: 45 },
            Act::Wait(10),
            Act::Caption("別の窓の中で入力する（窓の外でもふつうに使える）"),
            Act::Click { win: Win::Pop(Script), spot: Spot::Edit(Script), secondary: false },
            Act::Type(Script, "\nlayer.hide(\"影\")  # 別の窓から"),
            Act::Wait(15),
            Act::Caption("「レイヤー」も別の窓に出して、スクリプトの下に置く"),
            Act::Click { win: Win::Main, spot: Spot::Tab(Layers), secondary: true },
            Act::Wait(8),
            Act::Click { win: Win::Main, spot: Spot::Menu, secondary: false },
            Act::Wait(15),
            Act::MoveWindow { panel: Layers, to: Place::OtherScreen(400.0), frames: 40 },
            Act::Wait(10),
            Act::Report,
            Act::Wait(10),
            Act::Caption("「メインの窓に戻す」→ レイヤーがメインの窓に戻る（もとの列は空いて消えたので、右に列を作る）"),
            Act::Click { win: Win::Pop(Layers), spot: Spot::Back(Layers), secondary: false },
            Act::Wait(25),
            Act::Caption("スクリプトの窓をメインの窓の上まで動かして止める → 一緒にいたレイヤーの隣に戻る"),
            Act::MoveWindow { panel: Script, to: Place::MainCenter, frames: 45 },
            Act::Wait(40),
        ];
        Self {
            out,
            acts,
            index: 0,
            tick: 0,
            pointer: Pos2::new(500.0, 400.0),
            dragging: false,
            caption: "",
            from: None,
            frame_no: 0,
            saver: Some(tx),
            saver_done: done_rx,
            pending: Default::default(),
            log: Vec::new(),
            finished: false,
            report: Vec::new(),
            skipped: 0,
        }
    }

    fn spot(
        spot: Spot,
        main_inner: Rect,
        tabs: &HashMap<Panel, Rect>,
        menu: Option<Rect>,
        shared: &Shared,
    ) -> Option<Pos2> {
        let offset = main_inner.min.to_vec2();
        match spot {
            Spot::Tab(panel) => tabs.get(&panel).map(|r| r.center() + offset),
            Spot::Menu => menu.map(|r| r.center() + offset),
            Spot::Edit(panel) => shared
                .widgets
                .get(&format!("edit:{}", panel.title()))
                .map(|r| Pos2::new(r.max.x - 30.0, r.max.y - 12.0)),
            Spot::Back(panel) => shared.widgets.get(&format!("back:{}", panel.title())).map(|r| r.center()),
        }
    }

    /// メインの窓の1フレームごとに1回呼ぶ。メインの窓に入れる事件（画面の座標）を返す。
    #[allow(clippy::too_many_arguments)]
    pub fn step(
        &mut self,
        ctx: &egui::Context,
        popouts: &Popouts,
        main_outer: Rect,
        main_inner: Rect,
        tabs: &HashMap<Panel, Rect>,
        menu: Option<Rect>,
        shared: &Arc<Mutex<Shared>>,
    ) -> Vec<Injected> {
        let mut main_events = Vec::new();
        loop {
            let Some(act) = self.acts.get(self.index) else {
                self.finished = true;
                return main_events;
            };
            match act {
                Act::Caption(text) => {
                    self.caption = text;
                    self.index += 1;
                    continue;
                }
                Act::Wait(n) => {
                    self.tick += 1;
                    if self.tick >= *n {
                        self.tick = 0;
                        self.index += 1;
                    }
                    return main_events;
                }
                Act::Report => {
                    self.report = window_report(main_outer, shared, popouts);
                    for line in &self.report {
                        println!("{line}");
                    }
                    self.index += 1;
                    return main_events;
                }
                Act::Click { win, spot, secondary } => {
                    let sh = shared.lock().unwrap();
                    let Some(target) = Self::spot(*spot, main_inner, tabs, menu, &sh) else {
                        drop(sh);
                        if self.tick > 60 {
                            eprintln!("台本の位置が見つからないので飛ばす（手 {}）", self.index);
                            self.skipped += 1;
                            self.index += 1;
                            self.tick = 0;
                            continue;
                        }
                        self.tick += 1;
                        return main_events;
                    };
                    drop(sh);
                    let approach = 14;
                    let start = *self.from.get_or_insert(self.pointer);
                    let mut events = Vec::new();
                    if self.tick < approach {
                        let t = ease((self.tick + 1) as f32 / approach as f32);
                        self.pointer = start + (target - start) * t;
                        events.push(Injected::Move(self.pointer));
                    } else if self.tick == approach {
                        self.pointer = target;
                        events.push(Injected::Move(target));
                        events.push(Injected::Button(target, true));
                        self.dragging = true;
                    } else {
                        events.push(Injected::Button(target, false));
                        self.dragging = false;
                    }
                    if *secondary {
                        for e in &mut events {
                            if let Injected::Button(p, d) = *e {
                                *e = Injected::Secondary(p, d);
                            }
                        }
                    }
                    match win {
                        Win::Main => main_events.extend(events),
                        Win::Pop(panel) => {
                            if let Some(id) = popouts.id_of(*panel) {
                                shared.lock().unwrap().inject.entry(id).or_default().extend(events);
                                ctx.request_repaint_of(id);
                            }
                        }
                    }
                    if self.tick > approach {
                        self.tick = 0;
                        self.index += 1;
                        self.from = None;
                    } else {
                        self.tick += 1;
                    }
                    return main_events;
                }
                Act::MoveWindow { panel, to, frames } => {
                    let Some(id) = popouts.id_of(*panel) else {
                        self.index += 1;
                        continue;
                    };
                    let Some((outer, _)) = shared.lock().unwrap().windows.get(&id).copied() else {
                        return main_events;
                    };
                    let grip = Vec2::new(outer.width() / 2.0, 14.0);
                    let start = *self.from.get_or_insert(outer.min);
                    let target = match to {
                        Place::OtherScreen(dy) => Pos2::new(
                            other_screen_left(main_outer).unwrap_or(main_outer.max.x + 30.0),
                            main_outer.min.y + dy,
                        ),
                        Place::MainCenter => main_inner.center() - outer.size() / 2.0,
                    };
                    let approach = 12;
                    if self.tick < approach {
                        let t = ease((self.tick + 1) as f32 / approach as f32);
                        self.pointer = self.pointer + (start + grip - self.pointer) * t;
                    } else if self.tick <= approach + frames {
                        self.dragging = true;
                        let t = ease((self.tick - approach) as f32 / *frames as f32);
                        let pos = start + (target - start) * t;
                        self.pointer = pos + grip;
                        ctx.send_viewport_cmd_to(id, egui::ViewportCommand::OuterPosition(pos));
                    } else {
                        self.dragging = false;
                        self.tick = 0;
                        self.index += 1;
                        self.from = None;
                        return main_events;
                    }
                    self.tick += 1;
                    return main_events;
                }
                Act::Type(panel, text) => {
                    let chars: Vec<char> = text.chars().collect();
                    if let Some(id) = popouts.id_of(*panel) {
                        if self.tick % 2 == 0 {
                            if let Some(c) = chars.get(self.tick / 2) {
                                let event = if *c == '\n' {
                                    Injected::Enter
                                } else {
                                    Injected::Text(c.to_string())
                                };
                                shared.lock().unwrap().inject.entry(id).or_default().push(event);
                                ctx.request_repaint_of(id);
                            }
                        }
                    }
                    self.tick += 1;
                    if self.tick >= chars.len() * 2 {
                        self.tick = 0;
                        self.index += 1;
                    }
                    return main_events;
                }
            }
        }
    }

    /// 窓ごとの写しを頼み、位置を記録する。終わったら true。
    pub fn capture(&mut self, ctx: &egui::Context, popouts: &Popouts, shared: &Arc<Mutex<Shared>>, main: (Rect, Rect), hint: bool) -> bool {
        if self.finished {
            if self.pending.load(Ordering::SeqCst) == 0 {
                if let Some(saver) = self.saver.take() {
                    drop(saver);
                    let _ = self.saver_done.recv();
                    std::fs::write(self.out.join("frames.jsonl"), self.log.join("\n")).unwrap();
                    std::fs::write(self.out.join("report.txt"), self.report.join("\n")).unwrap();
                    println!(
                        "{} フレームを {} に保存（飛ばした手：{}）",
                        self.frame_no,
                        self.out.display(),
                        self.skipped
                    );
                    return true;
                }
            }
            return false;
        }
        let n = self.frame_no;
        self.frame_no += 1;
        let windows = shared.lock().unwrap().windows.clone();
        let mut entries = vec![format!(
            "{{\"label\":\"main\",\"title\":\"PMA 試作：egui + egui_dock\",\"outer\":{},\"inner\":{}}}",
            rect_json(main.0),
            rect_json(main.1)
        )];
        let mut targets = vec![(ViewportId::ROOT, "main".to_string())];
        for popout in &popouts.list {
            if let Some((outer, inner)) = windows.get(&popout.id) {
                let label = format!("pop{:?}", popout.panel);
                entries.push(format!(
                    "{{\"label\":\"{label}\",\"title\":\"{} — PMA 試作（別の窓）\",\"outer\":{},\"inner\":{}}}",
                    popout.panel.title(),
                    rect_json(*outer),
                    rect_json(*inner)
                ));
                targets.push((popout.id, label));
            }
        }
        self.log.push(format!(
            "{{\"n\":{n},\"caption\":{:?},\"pointer\":[{:.1},{:.1}],\"dragging\":{},\"hint\":{hint},\"windows\":[{}]}}",
            self.caption,
            self.pointer.x,
            self.pointer.y,
            self.dragging,
            entries.join(",")
        ));
        for (id, label) in targets {
            let Some(saver) = self.saver.clone() else { continue };
            let pending = self.pending.clone();
            pending.fetch_add(1, Ordering::SeqCst);
            let repaint = ctx.clone();
            let name = format!("{label}_{n:05}.png");
            ctx.send_viewport_cmd_to(
                id,
                egui::ViewportCommand::Screenshot(egui::ScreenshotCallback::new(move |image| {
                    let _ = saver.send((name.clone(), image));
                    pending.fetch_sub(1, Ordering::SeqCst);
                    repaint.request_repaint();
                })),
            );
            ctx.request_repaint_of(id);
        }
        false
    }
}

/// メインの窓の無い画面の左端＋80（画面の座標）。画面が1つなら None。
fn other_screen_left(main_outer: Rect) -> Option<f32> {
    #[cfg(target_os = "macos")]
    {
        use objc2::MainThreadMarker;
        use objc2_app_kit::NSScreen;
        let screens = NSScreen::screens(MainThreadMarker::new()?);
        let cx = main_outer.center().x as f64;
        for screen in screens.iter() {
            let f = screen.frame();
            if cx < f.origin.x || cx > f.origin.x + f.size.width {
                return Some(f.origin.x as f32 + 80.0);
            }
        }
        None
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = main_outer;
        None
    }
}

fn rect_json(r: Rect) -> String {
    format!("[{:.1},{:.1},{:.1},{:.1}]", r.min.x, r.min.y, r.width(), r.height())
}

/// 台本で入れた事件を egui の事件にする（画面の座標 → この窓の座標）。
pub fn to_events(injected: Vec<Injected>, inner_min: Pos2) -> Vec<egui::Event> {
    let local = |p: Pos2| p - inner_min.to_vec2();
    let mut out = Vec::new();
    for e in injected {
        match e {
            Injected::Move(p) => out.push(egui::Event::PointerMoved(local(p))),
            Injected::Button(p, pressed) => out.push(egui::Event::PointerButton {
                pos: local(p),
                button: egui::PointerButton::Primary,
                pressed,
                modifiers: egui::Modifiers::NONE,
            }),
            Injected::Secondary(p, pressed) => out.push(egui::Event::PointerButton {
                pos: local(p),
                button: egui::PointerButton::Secondary,
                pressed,
                modifiers: egui::Modifiers::NONE,
            }),
            Injected::Enter => out.push(egui::Event::Key {
                key: egui::Key::Enter,
                physical_key: None,
                pressed: true,
                repeat: false,
                modifiers: egui::Modifiers::NONE,
            }),
            Injected::Text(t) => out.push(egui::Event::Text(t)),
        }
    }
    out
}

/// OS の窓の一覧（NSApplication.windows）と、別の窓がメインの窓の外にあるか。
fn window_report(main_outer: Rect, shared: &Arc<Mutex<Shared>>, popouts: &Popouts) -> Vec<String> {
    let mut lines = Vec::new();
    #[cfg(target_os = "macos")]
    {
        use objc2::MainThreadMarker;
        use objc2_app_kit::NSApplication;
        let app = NSApplication::sharedApplication(MainThreadMarker::new().unwrap());
        let windows: Vec<_> = app.windows().iter().filter(|w| w.isVisible()).collect();
        lines.push(format!("OS の窓（NSWindow、見えているもの）：{} 枚", windows.len()));
        for w in &windows {
            let f = w.frame();
            let screen = w.screen().map(|s| s.localizedName().to_string()).unwrap_or_default();
            lines.push(format!(
                "  #{} 「{}」 画面「{screen}」 位置 ({:.0}, {:.0}) 大きさ {:.0}×{:.0}（中身の領域 {:.0}×{:.0}）",
                w.windowNumber(),
                w.title(),
                f.origin.x,
                f.origin.y,
                f.size.width,
                f.size.height,
                w.contentLayoutRect().size.width,
                w.contentLayoutRect().size.height
            ));
        }
    }
    let windows = shared.lock().unwrap().windows.clone();
    for popout in &popouts.list {
        if let Some((outer, _)) = windows.get(&popout.id) {
            let outside = !main_outer.intersects(*outer);
            lines.push(format!(
                "別の窓「{}」：外枠 {:?}、メインの窓 {:?} の外に{}",
                popout.panel.title(),
                outer,
                main_outer,
                if outside { "ある" } else { "ない" }
            ));
        }
    }
    lines
}
