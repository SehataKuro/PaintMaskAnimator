//! ツール・ツールプロパティ・アクションのパネルの中身（Qt 版の toolpanel.py・actionpanel.py・
//! canvas_input_events.py・canvas_paint_tools.py・canvas_selection.py の判断）。
//!
//! - ツールの設定（[`Settings`]）は Rust に一つだけ持ち、画面のパネルとキャンバスはそれを
//!   読み書きする。変わったら `tools-changed` の事件で知らせる。設定はアプリの設定の
//!   フォルダーの `config.json` に保存する（Qt 版の config と同じ名前の項目は同じ名前で持つ）。
//! - 画素の選択範囲（`pma_core::selection::SelectionState`）もここに持つ。解除はツールに
//!   関係なく行える（issue #110）。
//! - 文書を変えるツールの操作（投げ縄塗り・図形・直線・選択範囲の中を消去）は、
//!   `Session::edit_with` を通して Qt と同じ名前（「描画（A の 1番）」）で履歴に積む。
//! - バケツ・串刺し塗り・色置換・ゴミ取りは paint_ops.rs（計算は pma-core）。
//!   変形・アクションは、計算を pma-core へ移しているところなので、[`pending`] の窓口
//!   （HOOK と書いた関数）だけを置く。

use crate::session::{pack_rect, Session, Shared, StrokeTool};
use pma_core::brush::pressure::{pressure_bezier_at, PressureSettings};
use pma_core::brush::raster::fill_path;
use pma_core::brush::shapes::{ellipse_path, fill_polygon, stroke_path};
use pma_core::brush::{self, BrushSettings, FillRule, MaskColorFilter, Path, PathElement};
use pma_core::compose::{self, Background, DisplayOptions};
use pma_core::document::model::Document;
use pma_core::fill::islands::enclosed_by;
use pma_core::fill::target::{fill_target_mask, TargetColor};
use pma_core::image::{Rect, RgbaImage};
use pma_core::selection::{
    color_mask, contiguous_region, delete_selection, used_area, CombineMode, ContiguousOptions,
    ContiguousResult, Selection, SelectionState,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::PathBuf;
use tauri::ipc::Response;
use tauri::{Emitter, State};

/// ツールの並び（Qt の TOOL_DEFINITIONS と同じ順・同じ名前）。
pub const TOOLS: &[(&str, &str)] = &[
    ("brush", "ブラシ"),
    ("shape", "図形"),
    ("bucket", "バケツ"),
    ("lasso_fill", "投げ縄塗り"),
    ("replace_color", "色置換"),
    ("skewer_fill", "串刺し塗り"),
    ("lasso", "投げ縄選択"),
    ("rect_select", "長方形選択"),
    ("auto_select", "自動選択"),
    ("eyedropper", "スポイト"),
    ("dust", "ゴミ取り"),
];

/// 1つのアイコンに重ねるツール（Qt の TOOL_GROUPS）。環境設定で「ツールのアイコンを
/// まとめる」を入れたときだけ使う（issue #109。既定はまとめない）。
pub const TOOL_GROUPS: &[(&str, &[&str])] = &[
    ("fill", &["bucket", "lasso_fill", "skewer_fill"]),
    ("select", &["lasso", "rect_select", "auto_select"]),
];

// ツールのショートカットの正本は、外枠の操作の一覧（actions.rs の `tool:<id>`）と
// ショートカット設定（shortcuts.rs）。ここでは持たない。

/// 計算をまだ pma-core へ移していない操作の知らせ。
pub fn pending(name: &str) -> String {
    format!("{name}は、計算を pma-core へ移しているところです（まだ使えません）")
}

/// 筆圧の設定 1 つ（Qt の pressure_settings の settings の dict）。
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Pressure {
    pub enabled: bool,
    pub minimum: f64,
    pub maximum: f64,
    pub points: Vec<[f64; 2]>,
}

impl Default for Pressure {
    fn default() -> Self {
        Self {
            enabled: true,
            minimum: 0.1,
            maximum: 1.0,
            points: vec![[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]],
        }
    }
}

impl Pressure {
    /// 範囲の外の値を直す（`pressure_settings.normalize`）。
    fn normalized(&self) -> Self {
        let mut points: Vec<[f64; 2]> = self
            .points
            .iter()
            .filter(|p| p[0].is_finite() && p[1].is_finite())
            .map(|p| [p[0].clamp(0.0, 1.0), p[1].clamp(0.0, 1.0)])
            .collect();
        points.sort_by(|a, b| a[0].total_cmp(&b[0]));
        if points.len() < 2 {
            points = Self::default().points;
        }
        let clamp =
            |v: f64, lo: f64, hi: f64, d: f64| if v.is_finite() { v.clamp(lo, hi) } else { d };
        Self {
            enabled: self.enabled,
            minimum: clamp(self.minimum, 0.01, 1.0, 0.1),
            maximum: clamp(self.maximum, 0.1, 3.0, 1.0),
            points,
        }
    }

    fn to_core(&self) -> PressureSettings {
        PressureSettings {
            enabled: self.enabled,
            minimum: self.minimum,
            maximum: self.maximum,
            points: self.points.iter().map(|p| (p[0], p[1])).collect(),
            exponent: 1.0,
        }
    }
}

/// 名前つきの筆圧（環境設定の「筆圧」のプリセット）。
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct PressurePreset {
    pub name: String,
    #[serde(flatten)]
    pub settings: Pressure,
}

/// ツールの設定。画面のパネルの項目と同じ並び。数値は Qt のスライダーの値のまま持つ
/// （図形の線の太さ 2 = 1.0 px など）。
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Settings {
    /// 使っているツール。
    pub tool: String,
    /// ツールの並び（アイコンをドラッグで並べ替えた順）。
    pub tool_order: Vec<String>,
    /// グループごとに表に出ているツール。
    pub group_current: BTreeMap<String, String>,

    // 描画色（メイン・サブ・背景）。color_mode は main / sub / transparent。
    pub color_main: [u8; 3],
    pub color_sub: [u8; 3],
    pub color_mode: String,
    /// 背景色の表示色（描画には白＝消しゴムを使う）。
    pub background_color: [u8; 3],

    // ブラシ・ライン
    pub brush_size: f64,
    pub stabilizer: i64,
    /// 筆圧：全体のプリセットを使うか（`brush_pressure_uses_global`）。
    pub pressure_uses_global: bool,
    pub pressure_active_preset: String,
    pub pressure_presets: Vec<PressurePreset>,
    /// このブラシの筆圧（`brush_pressure`）。
    pub brush_pressure: Pressure,

    pub line_type: String,
    pub line_taper_in: bool,
    pub line_taper_in_size: i64,
    pub line_taper_in_curve: i64,
    pub line_taper_out: bool,
    pub line_taper_out_size: i64,
    pub line_taper_out_curve: i64,

    // 図形
    pub shape_type: String,
    pub shape_corners: i64,
    pub shape_lock_ratio: bool,
    pub shape_fill_inside: bool,
    pub shape_sub_outline_main_fill: bool,
    pub shape_outline_width: i64,

    // 投げ縄塗り
    pub lasso_inside_boundary: bool,
    pub lasso_main_outline_sub_fill: bool,
    pub lasso_outline_width: i64,

    // バケツ（串刺し塗り・自動選択・投げ縄塗りも使う）
    pub bucket_reference: String,
    pub bucket_target_color: String,
    pub bucket_adjacent: bool,
    pub bucket_include_sub: bool,
    pub bucket_include_colors: [Option<[u8; 3]>; 3],
    pub bucket_include_colors_enabled: [bool; 3],
    pub bucket_island_replace: bool,
    pub bucket_island_fill: bool,
    pub bucket_island_fill_width: i64,
    pub bucket_close_gap: bool,
    pub bucket_gap_width: i64,
    pub bucket_extend_lines: bool,
    pub bucket_extend_length: i64,
    pub bucket_require_closed: bool,
    pub fill_opacity: i64,

    // ゴミ取り
    pub dust_mode: String,
    pub dust_size: i64,
    pub dust_selected_only: bool,
    pub dust_all_frames: bool,
    pub line_tidy_shape: String,
    pub line_tidy_brush_size: i64,
    pub line_tidy_diagonal: Option<String>,

    // 選択範囲の変形
    pub transform_mesh_x: i64,
    pub transform_mesh_y: i64,
    pub transform_quality: bool,
    pub transform_line_width: i64,
    pub selection_all_frames: bool,
    pub transform_as_tween: bool,

    // 環境設定
    /// ツールのアイコンをまとめる（issue #109。既定はまとめない）。
    pub tool_groups_enabled: bool,
    /// ツールのキーを長押ししたら、離したときに元のツールへ戻す（一時切り替え）。
    pub tool_hold_enabled: bool,
    pub tool_hold_ms: i64,
    /// 背景色のショートカット（E）を描画ツール以外で押したときのツール（brush / bucket）。
    pub background_color_tool: String,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            tool: "brush".into(),
            tool_order: TOOLS.iter().map(|(id, _)| id.to_string()).collect(),
            group_current: TOOL_GROUPS
                .iter()
                .map(|(g, m)| (g.to_string(), m[0].to_string()))
                .collect(),
            color_main: [0, 0, 0],
            color_sub: [255, 0, 0],
            color_mode: "main".into(),
            background_color: [255, 255, 255],
            brush_size: 8.0,
            stabilizer: 0,
            pressure_uses_global: true,
            pressure_active_preset: "標準".into(),
            pressure_presets: vec![PressurePreset {
                name: "標準".into(),
                settings: Pressure::default(),
            }],
            brush_pressure: Pressure::default(),
            line_type: "straight".into(),
            line_taper_in: false,
            line_taper_in_size: 1,
            line_taper_in_curve: 100,
            line_taper_out: false,
            line_taper_out_size: 1,
            line_taper_out_curve: 100,
            shape_type: "polygon".into(),
            shape_corners: 4,
            shape_lock_ratio: false,
            shape_fill_inside: false,
            shape_sub_outline_main_fill: false,
            shape_outline_width: 2,
            lasso_inside_boundary: false,
            lasso_main_outline_sub_fill: false,
            lasso_outline_width: 2,
            bucket_reference: "layer".into(),
            bucket_target_color: "all".into(),
            bucket_adjacent: true,
            bucket_include_sub: false,
            bucket_include_colors: [None; 3],
            bucket_include_colors_enabled: [true; 3],
            bucket_island_replace: false,
            bucket_island_fill: true,
            bucket_island_fill_width: 1,
            bucket_close_gap: false,
            bucket_gap_width: 4,
            bucket_extend_lines: true,
            bucket_extend_length: 150,
            bucket_require_closed: false,
            fill_opacity: 100,
            dust_mode: "despeckle".into(),
            dust_size: 3,
            dust_selected_only: false,
            dust_all_frames: false,
            line_tidy_shape: "brush".into(),
            line_tidy_brush_size: 24,
            line_tidy_diagonal: Some("add".into()),
            transform_mesh_x: 4,
            transform_mesh_y: 4,
            transform_quality: true,
            transform_line_width: 96,
            selection_all_frames: false,
            transform_as_tween: false,
            tool_groups_enabled: false,
            tool_hold_enabled: false,
            tool_hold_ms: 0,
            background_color_tool: "brush".into(),
        }
    }
}

impl Settings {
    /// 範囲の外の値を Qt のスライダーの範囲へ寄せ、知らないツールは既定に戻す。
    fn normalize(&mut self) {
        // 閉領域フィルはなくした（Qt の select_tool と同じくバケツにする）。
        if self.tool == "closed_fill" {
            self.tool = "bucket".into();
        }
        // ライン はアイコンを持たないツール（図形の「直線」「曲線」と同じ設定）。
        if self.tool != "line" && !TOOLS.iter().any(|(id, _)| *id == self.tool) {
            self.tool = "brush".into();
        }
        let mut order: Vec<String> = self
            .tool_order
            .iter()
            .filter(|t| TOOLS.iter().any(|(id, _)| id == t))
            .cloned()
            .collect();
        order.dedup();
        for (id, _) in TOOLS {
            if !order.iter().any(|t| t == id) {
                order.push(id.to_string());
            }
        }
        self.tool_order = order;
        for (group, members) in TOOL_GROUPS {
            let ok = self
                .group_current
                .get(*group)
                .is_some_and(|t| members.contains(&t.as_str()));
            if !ok {
                self.group_current
                    .insert(group.to_string(), members[0].to_string());
            }
        }
        if !["main", "sub", "transparent"].contains(&self.color_mode.as_str()) {
            self.color_mode = "main".into();
        }
        if !self.brush_size.is_finite() {
            self.brush_size = 8.0;
        }
        self.brush_size = (self.brush_size * 2.0).round().clamp(1.0, 800.0) / 2.0;
        self.stabilizer = self.stabilizer.clamp(0, 300);
        self.brush_pressure = self.brush_pressure.normalized();
        self.pressure_presets.retain(|p| !p.name.trim().is_empty());
        for preset in &mut self.pressure_presets {
            preset.settings = preset.settings.normalized();
        }
        if self.pressure_presets.is_empty() {
            self.pressure_presets = Settings::default().pressure_presets;
        }
        if !self
            .pressure_presets
            .iter()
            .any(|p| p.name == self.pressure_active_preset)
        {
            self.pressure_active_preset = self.pressure_presets[0].name.clone();
        }
        for v in [&mut self.line_taper_in_size, &mut self.line_taper_out_size] {
            *v = (*v).clamp(1, 800);
        }
        for v in [
            &mut self.line_taper_in_curve,
            &mut self.line_taper_out_curve,
        ] {
            *v = (*v).clamp(20, 400);
        }
        self.shape_corners = self.shape_corners.clamp(3, 32);
        self.shape_outline_width = self.shape_outline_width.clamp(1, 80);
        self.lasso_outline_width = self.lasso_outline_width.clamp(1, 40);
        self.bucket_island_fill_width = self.bucket_island_fill_width.clamp(1, 20);
        self.bucket_gap_width = self.bucket_gap_width.clamp(1, 20);
        self.bucket_extend_length = self.bucket_extend_length.clamp(10, 1000);
        self.fill_opacity = self.fill_opacity.clamp(1, 100);
        self.dust_size = self.dust_size.clamp(1, 100);
        self.line_tidy_brush_size = self.line_tidy_brush_size.clamp(2, 200);
        self.transform_mesh_x = self.transform_mesh_x.clamp(2, 12);
        self.transform_mesh_y = self.transform_mesh_y.clamp(2, 12);
        self.transform_line_width = self.transform_line_width.clamp(1, 254);
        self.tool_hold_ms = self.tool_hold_ms.clamp(0, 3000);
        if !["brush", "bucket"].contains(&self.background_color_tool.as_str()) {
            self.background_color_tool = "brush".into();
        }
        if !["straight", "curve"].contains(&self.line_type.as_str()) {
            self.line_type = "straight".into();
        }
        if !["straight", "curve", "polygon", "ellipse"].contains(&self.shape_type.as_str()) {
            self.shape_type = "polygon".into();
        }
    }

    /// ブラシが今使う筆圧（`pressure_settings.effective_settings`）。
    pub fn effective_pressure(&self) -> Pressure {
        if self.pressure_uses_global {
            if let Some(p) = self
                .pressure_presets
                .iter()
                .find(|p| p.name == self.pressure_active_preset)
            {
                return p.settings.clone();
            }
        }
        self.brush_pressure.clone()
    }

    /// 描く色（`paint_source_color`）。背景色のモードは擬似透明の白（消しゴム）。
    pub fn paint_rgb(&self) -> [u8; 3] {
        match self.color_mode.as_str() {
            "sub" => self.color_sub,
            "transparent" => brush::ERASER_RGB,
            _ => self.color_main,
        }
    }

    /// `base`（main / sub）の色を、背景色のモードでなくてもその色で描く。
    fn source_rgb(&self, sub: bool) -> [u8; 3] {
        if sub {
            self.color_sub
        } else {
            self.color_main
        }
    }
}

/// ツールの状態：設定・選択範囲・マスク色の絞り込み・保存先。
#[derive(Default)]
pub struct ToolState {
    pub settings: Settings,
    pub selection: SelectionState,
    /// 「選んだマスク色の上だけに描く」（使用色パネルが決める。None は全体に描く）。
    pub mask_filter: Option<MaskColorFilter>,
    /// 設定を保存する `config.json`。None なら保存しない（試験）。
    pub config: Option<PathBuf>,
    /// 塗りの途中の状態（なぞり塗り・串刺し塗り・使用色の選択。paint_ops.rs）。
    pub paint: crate::paint_ops::PaintState,
}

/// 画面へ渡す選択範囲。
#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct SelectionView {
    /// 点線を引く閉じた折れ線（画像の座標）。
    pub outlines: Vec<Vec<[f64; 2]>>,
    /// 外接矩形（x, y, 幅, 高さ）。
    pub bounds: Option<[i64; 4]>,
    pub is_mask: bool,
}

/// 画面へ渡すツールの状態（`tools-changed` の事件の中身）。
#[derive(Clone, Debug, Serialize)]
pub struct View {
    pub settings: Settings,
    pub tools: Vec<(String, String)>,
    pub groups: Vec<(String, Vec<String>)>,
    pub selection: Option<SelectionView>,
    /// 「選択範囲を解除」を押せるか（ツールに関係なく、選択があれば真。issue #110）。
    pub can_clear_selection: bool,
    pub pen_pressure: Pressure,
}

impl ToolState {
    pub fn view(&self) -> View {
        let selection = self.selection.selection().map(|s| {
            let b = s.bounds();
            SelectionView {
                outlines: s
                    .outlines()
                    .into_iter()
                    .map(|o| o.into_iter().map(|(x, y)| [x, y]).collect())
                    .collect(),
                bounds: (!b.is_empty()).then_some([b.x, b.y, b.width, b.height]),
                is_mask: s.is_mask(),
            }
        });
        View {
            settings: self.settings.clone(),
            tools: TOOLS
                .iter()
                .map(|(a, b)| (a.to_string(), b.to_string()))
                .collect(),
            groups: TOOL_GROUPS
                .iter()
                .map(|(g, m)| (g.to_string(), m.iter().map(|t| t.to_string()).collect()))
                .collect(),
            selection,
            can_clear_selection: self.selection.can_clear(),
            pen_pressure: self.settings.effective_pressure(),
        }
    }

    /// 設定の一部を JSON で受け取り、今の設定に重ねる。
    pub fn apply_patch(&mut self, patch: &Value) -> Result<(), String> {
        let mut current = serde_json::to_value(&self.settings).map_err(|e| e.to_string())?;
        merge(&mut current, patch);
        let mut next: Settings = serde_json::from_value(current).map_err(|e| e.to_string())?;
        next.normalize();
        self.settings = next;
        Ok(())
    }

    /// ブラシ・消しゴムの設定（筆圧・手ぶれ補正・マスク色・透明部分の保護・選択範囲）。
    /// `tool` の値（自己確認などが渡す）があればそちらを使う。
    pub fn brush_settings(
        &self,
        tool: &StrokeTool,
        doc: &Document,
        layer: usize,
        size: (usize, usize),
    ) -> BrushSettings {
        let s = &self.settings;
        let alpha_lock = doc
            .track_order()
            .get(layer)
            .and_then(|t| doc.track(*t))
            .is_some_and(|t| t.settings.alpha_locked);
        BrushSettings {
            size: tool.size.unwrap_or(s.brush_size),
            color: tool.color.unwrap_or_else(|| s.paint_rgb()),
            eraser: tool.eraser,
            pressure: s.effective_pressure().to_core(),
            stabilizer: tool.stabilizer.unwrap_or(s.stabilizer),
            zoom: tool.zoom,
            alpha_lock,
            mask_colors: self.mask_filter.clone(),
            selection: self
                .selection
                .selection()
                .map(|sel| brush_selection(sel, size.0, size.1)),
        }
    }

    /// 設定を読む。無い・読めない項目は既定のまま。
    pub fn load(&mut self) {
        let Some(path) = &self.config else { return };
        let root = read_config(path);
        let mut settings = root
            .get("tool_settings")
            .cloned()
            .and_then(|v| serde_json::from_value::<Settings>(v).ok())
            .unwrap_or_default();
        // Qt 版と同じ名前で持つ項目。
        let get = |key: &str| root.get(key).cloned();
        if let Some(v) = get("last_paint_session")
            .and_then(|v| v.get("brush_size").cloned())
            .and_then(|v| v.as_f64())
        {
            settings.brush_size = v;
        }
        if let Some(v) = get("bucket_include_colors").and_then(|v| serde_json::from_value(v).ok()) {
            settings.bucket_include_colors = v;
        }
        if let Some(v) =
            get("bucket_include_colors_enabled").and_then(|v| serde_json::from_value(v).ok())
        {
            settings.bucket_include_colors_enabled = v;
        }
        if let Some(v) = get("pressure_presets").and_then(|v| serde_json::from_value(v).ok()) {
            settings.pressure_presets = v;
        }
        if let Some(Value::String(v)) = get("pressure_active_preset") {
            settings.pressure_active_preset = v;
        }
        if let Some(v) = get("brush_pressure").and_then(|v| serde_json::from_value(v).ok()) {
            settings.brush_pressure = v;
        }
        if let Some(Value::Bool(v)) = get("brush_pressure_uses_global") {
            settings.pressure_uses_global = v;
        }
        if let Some(Value::Bool(v)) = get("tool_hold_switch_enabled") {
            settings.tool_hold_enabled = v;
        }
        if let Some(v) = get("tool_hold_switch_ms").and_then(|v| v.as_i64()) {
            settings.tool_hold_ms = v;
        }
        if let Some(Value::Bool(v)) = get("tool_groups_enabled") {
            settings.tool_groups_enabled = v;
        }
        if let Some(Value::String(v)) = get("background_color_tool") {
            settings.background_color_tool = v;
        }
        settings.normalize();
        self.settings = settings;
    }

    /// 設定を保存する。ほかの項目（ほかのパネルの設定）は残す。
    pub fn save(&self) -> Result<(), String> {
        let Some(path) = &self.config else {
            return Ok(());
        };
        let mut root = read_config(path);
        if !root.is_object() {
            root = Value::Object(Default::default());
        }
        let s = &self.settings;
        let obj = root.as_object_mut().expect("object");
        let mut session = obj
            .get("last_paint_session")
            .cloned()
            .filter(Value::is_object)
            .unwrap_or_else(|| Value::Object(Default::default()));
        session["brush_size"] = serde_json::json!(s.brush_size);
        obj.insert("last_paint_session".into(), session);
        let json = |v: &dyn erased::Ser| v.to_value();
        obj.insert(
            "bucket_include_colors".into(),
            json(&s.bucket_include_colors),
        );
        obj.insert(
            "bucket_include_colors_enabled".into(),
            json(&s.bucket_include_colors_enabled),
        );
        obj.insert("pressure_presets".into(), json(&s.pressure_presets));
        obj.insert(
            "pressure_active_preset".into(),
            json(&s.pressure_active_preset),
        );
        obj.insert("brush_pressure".into(), json(&s.brush_pressure));
        obj.insert(
            "brush_pressure_uses_global".into(),
            json(&s.pressure_uses_global),
        );
        obj.insert(
            "tool_hold_switch_enabled".into(),
            json(&s.tool_hold_enabled),
        );
        obj.insert("tool_hold_switch_ms".into(), json(&s.tool_hold_ms));
        obj.insert("tool_groups_enabled".into(), json(&s.tool_groups_enabled));
        obj.insert(
            "background_color_tool".into(),
            json(&s.background_color_tool),
        );
        obj.insert("tool_settings".into(), json(s));
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        let text = serde_json::to_string_pretty(&root).map_err(|e| e.to_string())?;
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, text).map_err(|e| e.to_string())?;
        std::fs::rename(&tmp, path).map_err(|e| e.to_string())
    }
}

/// `serde_json::to_value` を、型の違う値の表で書けるようにする小さな仕掛け。
mod erased {
    pub trait Ser {
        fn to_value(&self) -> serde_json::Value;
    }
    impl<T: serde::Serialize> Ser for T {
        fn to_value(&self) -> serde_json::Value {
            serde_json::to_value(self).unwrap_or(serde_json::Value::Null)
        }
    }
}

fn read_config(path: &std::path::Path) -> Value {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_else(|| Value::Object(Default::default()))
}

/// `patch` の項目を `base` に重ねる（入れ子の表は項目ごとに重ねる）。
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

/// 選択範囲を、ブラシのクリップの形にする。
fn brush_selection(selection: &Selection, width: usize, height: usize) -> brush::Selection {
    match selection.polygon_points() {
        Some(points) => brush::Selection::Polygon(
            points
                .iter()
                .map(|&(x, y)| brush::Point::new(x, y))
                .collect(),
        ),
        None => {
            let mask = selection.mask(width, height, 0, 0);
            let bits = mask.bits().iter().map(|&b| b != 0).collect();
            brush::Selection::Mask(
                brush::Mask::from_bits(0, 0, width, height, bits)
                    .unwrap_or_else(|| brush::Mask::new(0, 0, width, height)),
            )
        }
    }
}

/// 履歴の名前（Qt の LayerRegionUndo / LayerTilesUndo の history_label）。
/// 絵に番号があれば「描画（レイヤー名 の 番号番）」、無ければ「描画」。
pub fn drawing_label(doc: &Document, frame: usize, layer: usize) -> String {
    let Some(&track) = doc.track_order().get(layer) else {
        return "描画".into();
    };
    // 保持のコマでは露出元（絵を持つコマ）の番号（Qt は ensure_editable_key で露出元を編集する）。
    let key_frame = compose::frame_layers(doc, frame)
        .ok()
        .and_then(|layers| layers.get(layer).cloned().flatten().map(|l| l.key_frame))
        .unwrap_or(frame);
    let number = doc
        .frame_order()
        .get(key_frame)
        .and_then(|f| doc.frame(*f))
        .and_then(|f| f.positions.get(&track))
        .and_then(|p| p.number());
    match (number, doc.track(track)) {
        (Some(n), Some(t)) => format!("描画（{} の {}番）", t.settings.name, n.get()),
        _ => "描画".into(),
    }
}

// ---------------------------------------------------------------------------
// 画素の面（全体の大きさの 0/1 の並び）
// ---------------------------------------------------------------------------

/// 画像と同じ大きさの 0/1 の面。
struct Bits {
    width: usize,
    height: usize,
    bits: Vec<u8>,
}

impl Bits {
    fn new(width: usize, height: usize) -> Self {
        Self {
            width,
            height,
            bits: vec![0; width * height],
        }
    }

    /// ブラシの面（原点つき）を重ねる。
    fn or_mask(&mut self, mask: &brush::Mask) {
        for (row, left, right) in mask.runs() {
            let y = mask.y + row as i64;
            if y < 0 || y >= self.height as i64 {
                continue;
            }
            let l = (mask.x + left as i64).clamp(0, self.width as i64) as usize;
            let r = (mask.x + right as i64).clamp(0, self.width as i64) as usize;
            let base = y as usize * self.width;
            self.bits[base + l..base + r].fill(1);
        }
    }

    fn and(&mut self, other: &[u8]) {
        for (a, b) in self.bits.iter_mut().zip(other) {
            *a &= u8::from(*b != 0);
        }
    }

    fn bounds(&self) -> Option<Rect> {
        let (mut x0, mut y0, mut x1, mut y1) = (usize::MAX, usize::MAX, 0, 0);
        for y in 0..self.height {
            let row = &self.bits[y * self.width..(y + 1) * self.width];
            if let Some(first) = row.iter().position(|&b| b != 0) {
                let last = row.iter().rposition(|&b| b != 0).unwrap_or(first);
                x0 = x0.min(first);
                x1 = x1.max(last + 1);
                y0 = y0.min(y);
                y1 = y + 1;
            }
        }
        (x0 < x1).then(|| Rect {
            x: x0,
            y: y0,
            width: x1 - x0,
            height: y1 - y0,
        })
    }
}

/// 経路を、曲線を細かい直線にした経路にする（Qt の `QBezier::shifted` がまだ無いので、
/// 曲線の線は折れ線で近づける。画素は Qt と完全には一致しない）。
fn flattened(path: &Path) -> Path {
    let mut out = Path::new();
    let mut last = brush::Point::new(0.0, 0.0);
    for element in path.elements() {
        match *element {
            PathElement::MoveTo(p) => {
                out.move_to(p);
                last = p;
            }
            PathElement::LineTo(p) => {
                out.line_to(p);
                last = p;
            }
            PathElement::CubicTo(c1, c2, end) => {
                let length = (c1.x - last.x).hypot(c1.y - last.y)
                    + (c2.x - c1.x).hypot(c2.y - c1.y)
                    + (end.x - c2.x).hypot(end.y - c2.y);
                let steps = (length / 2.0).ceil().clamp(4.0, 256.0) as usize;
                for i in 1..=steps {
                    let t = i as f64 / steps as f64;
                    let u = 1.0 - t;
                    let (a, b, c, d) = (u * u * u, 3.0 * u * u * t, 3.0 * u * t * t, t * t * t);
                    out.line_to(brush::Point::new(
                        a * last.x + b * c1.x + c * c2.x + d * end.x,
                        a * last.y + b * c1.y + c * c2.y + d * end.y,
                    ));
                }
                last = end;
            }
        }
    }
    out
}

/// `QPainter::drawPath`（丸い端・丸い角、幅 `pen` のペン）の画素を `out` に重ねる。
/// 経路の周りだけの小さな面で計算し、整数ずらして重ねる（画素は同じ）。
fn stroke_into(out: &mut Bits, path: &Path, pen: f64) {
    let Some((l, t, r, b)) = path.control_point_bounds() else {
        return;
    };
    let pad = pen.ceil() + 2.0;
    let x0 = (l - pad).floor().max(0.0);
    let y0 = (t - pad).floor().max(0.0);
    let x1 = (r + pad).ceil().min(out.width as f64);
    let y1 = (b + pad).ceil().min(out.height as f64);
    if x1 <= x0 || y1 <= y0 {
        return;
    }
    let (w, h) = ((x1 - x0) as usize, (y1 - y0) as usize);
    let local = path.translated(-x0, -y0);
    // pma-core の stroke_path は曲線・幅 1 以下の折れ線もそのまま描ける。
    let mut mask = stroke_path(&local, pen, w, h);
    mask.x += x0 as i64;
    mask.y += y0 as i64;
    out.or_mask(&mask);
}

/// 線整えでなぞった範囲（`line_tidy_region.region_mask`）：投げ縄は囲んだ中、ブラシは
/// 太さ `brush_width` の丸い端の線。キャンバス全体の 0/1。
pub fn tidy_region(
    points: &[[f64; 2]],
    shape: &str,
    brush_width: f64,
    width: usize,
    height: usize,
) -> Vec<u8> {
    let mut out = Bits::new(width, height);
    if width == 0 || height == 0 || points.is_empty() {
        return out.bits;
    }
    let poly: Vec<brush::Point> = points
        .iter()
        .map(|p| brush::Point::new(p[0], p[1]))
        .collect();
    if shape == "lasso" {
        if poly.len() >= 3 {
            out.or_mask(&fill_polygon(&poly, FillRule::OddEven, width, height));
        }
        return out.bits;
    }
    let mut path = Path::new();
    path.move_to(poly[0]);
    if poly.len() == 1 {
        // drawPoint：丸い端のペンの点。
        path.line_to(brush::Point::new(poly[0].x + 1e-3, poly[0].y));
    }
    for p in &poly[1..] {
        path.line_to(*p);
    }
    stroke_into(&mut out, &path, brush_width.max(1.0));
    out.bits
}

fn fill_into(out: &mut Bits, path: &Path) {
    out.or_mask(&fill_path(path, FillRule::OddEven, out.width, out.height));
}

/// 選択範囲のクリップ（`apply_selection_clip`）。選択が無ければ None。
fn clip_bits(state: &ToolState, width: usize, height: usize) -> Option<Vec<u8>> {
    let selection = state.selection.selection()?;
    selection
        .clip_mask(width, height, (0.0, 0.0))
        .map(|m| m.bits().to_vec())
}

/// 1 画素に重ねる色（白は消しゴム）。
#[derive(Clone, Copy)]
struct Paint {
    rgb: [u8; 3],
}

/// `_blend_overlay_into_active_layer`：面の画素を正規の RGB で直書きする。白は透明に抜く。
/// `opacity` が 1 未満なら、下地（透明は白とみなす）と混ぜる。
fn blend_into(base: &[u8], overlay: &[Option<Paint>], opacity: f64) -> Vec<u8> {
    let amount = opacity.clamp(0.0, 1.0);
    let blended = amount < 0.999_999;
    let mut out = base.to_vec();
    for (i, paint) in overlay.iter().enumerate() {
        let Some(paint) = paint else { continue };
        let px = &mut out[i * 4..i * 4 + 4];
        let mut rgb = paint.rgb;
        if blended && rgb != brush::ERASER_RGB {
            let b = if base[i * 4 + 3] == 0 {
                [255.0; 3]
            } else {
                [
                    base[i * 4] as f64,
                    base[i * 4 + 1] as f64,
                    base[i * 4 + 2] as f64,
                ]
            };
            for c in 0..3 {
                let v = (b[c] * (1.0 - amount) + rgb[c] as f64 * amount).round_ties_even();
                rgb[c] = v.clamp(0.0, 255.0) as u8;
            }
            if rgb == brush::ERASER_RGB {
                // 混ぜた結果がたまたま白になった画素は消しゴム扱いにしない。
                rgb = [254, 254, 254];
            }
        }
        if rgb == brush::ERASER_RGB {
            px.copy_from_slice(&[0, 0, 0, 0]);
        } else {
            px.copy_from_slice(&[rgb[0], rgb[1], rgb[2], 255]);
        }
    }
    out
}

/// 今のコマの選択レイヤーの絵（無ければ透明の画像）と、コマ・レイヤーの番号。
fn active_image(s: &Session) -> Result<(RgbaImage, usize, usize), String> {
    let p = s.project.as_ref().ok_or("作品を開いていません")?;
    let doc = &p.document;
    let (frame, layer) = crate::session::current_index(doc);
    let layers = compose::frame_layers(doc, frame).map_err(|e| e.to_string())?;
    let (w, h) = doc.size();
    let image = match layers
        .get(layer)
        .and_then(|l| l.as_ref())
        .and_then(|l| l.image)
    {
        Some(image) => image.clone(),
        None => RgbaImage::blank(w, h).map_err(|e| e.to_string())?,
    };
    Ok((image, frame, layer))
}

/// 塗る範囲の判定に使う絵（バケツの「参照」）。
fn reference_image(s: &Session, layer_image: &RgbaImage) -> Result<RgbaImage, String> {
    let p = s.project.as_ref().ok_or("作品を開いていません")?;
    let doc = &p.document;
    let (frame, layer) = crate::session::current_index(doc);
    match s.tools.settings.bucket_reference.as_str() {
        "initial" => {
            let layers = compose::frame_layers(doc, frame).map_err(|e| e.to_string())?;
            let initial = layers
                .get(layer)
                .and_then(|l| l.as_ref())
                .and_then(|l| l.initial_image)
                .filter(|i| (i.width(), i.height()) == (layer_image.width(), layer_image.height()));
            Ok(initial.cloned().unwrap_or_else(|| layer_image.clone()))
        }
        "canvas" => {
            // 表示しているレイヤー（用紙を除く）を不透明度つきで重ねた絵。
            let image = compose::composite(
                doc,
                frame,
                &DisplayOptions::default(),
                Background::Transparent,
            )
            .map_err(|e| e.to_string())?;
            RgbaImage::from_rgba(image.width(), image.height(), &image.to_straight_bytes())
                .map_err(|e| e.to_string())
        }
        _ => Ok(layer_image.clone()),
    }
}

fn target_color(name: &str) -> TargetColor {
    match name {
        "transparent" => TargetColor::Transparent,
        "surrounded_by_transparent" => TargetColor::SurroundedByTransparent,
        "black" => TargetColor::Black,
        "surrounded_by_black" => TargetColor::SurroundedByBlack,
        "white_transparent" => TargetColor::WhiteTransparent,
        "surrounded_by_white_transparent" => TargetColor::SurroundedByWhiteTransparent,
        "faint_as_transparent" => TargetColor::FaintAsTransparent,
        "opaque_and_inner_transparent" => TargetColor::OpaqueAndInnerTransparent,
        "transparent_and_closed_opaque" => TargetColor::TransparentAndClosedOpaque,
        _ => TargetColor::All(None),
    }
}

fn is_background(px: &[u8]) -> bool {
    px[3] == 0 || (px[0] == 255 && px[1] == 255 && px[2] == 255)
}

/// 選択レイヤーの今のコマに、計算した画素を書いて履歴に積む（Session::edit_with を通す）。
fn write_active(
    s: &mut Session,
    frame: usize,
    layer: usize,
    rect: Rect,
    pixels: Vec<u8>,
) -> Result<Option<Rect>, String> {
    let label = {
        let p = s.project.as_ref().ok_or("作品を開いていません")?;
        drawing_label(&p.document, frame, layer)
    };
    s.edit_with(Some(&label), |doc, _| {
        doc.write_drawing_patch_with_content(frame, layer, rect, &pixels, true)
            .map_err(|e| e.to_string())?;
        Ok(((), true))
    })?;
    s.touch_pixels();
    Ok(Some(rect))
}

/// 面 `paint`（画素ごとの色）を選択レイヤーへ書く。
fn commit_overlay(
    s: &mut Session,
    image: &RgbaImage,
    frame: usize,
    layer: usize,
    active: &Bits,
    paint: impl Fn(usize) -> Paint,
    opacity: f64,
) -> Result<Option<Rect>, String> {
    let Some(rect) = active.bounds() else {
        return Ok(None);
    };
    let base = image.copy_rgba_rect(rect).map_err(|e| e.to_string())?;
    let mut overlay = Vec::with_capacity(rect.width * rect.height);
    for y in rect.y..rect.y + rect.height {
        for x in rect.x..rect.x + rect.width {
            let i = y * active.width + x;
            overlay.push((active.bits[i] != 0).then(|| paint(i)));
        }
    }
    let pixels = blend_into(&base, &overlay, opacity);
    write_active(s, frame, layer, rect, pixels)
}

// ---------------------------------------------------------------------------
// 文書を変えるツール
// ---------------------------------------------------------------------------

/// 投げ縄塗り（`fill_lasso_polygon`）。囲んだ範囲を塗る。変わった範囲を返す。
pub fn lasso_fill(s: &mut Session, points: &[[f64; 2]]) -> Result<Option<Rect>, String> {
    if points.len() < 3 {
        return Ok(None);
    }
    let (image, frame, layer) = active_image(s)?;
    let (w, h) = (image.width(), image.height());
    let settings = s.tools.settings.clone();
    let outline_and_fill = settings.lasso_main_outline_sub_fill;
    let outline_width = if outline_and_fill {
        settings.lasso_outline_width as f64 / 2.0
    } else {
        0.0
    };
    let poly: Vec<brush::Point> = points
        .iter()
        .map(|p| brush::Point::new(p[0], p[1]))
        .collect();
    let mut fill = Bits::new(w, h);
    fill.or_mask(&fill_polygon(&poly, FillRule::OddEven, w, h));
    let mut line = Bits::new(w, h);
    if outline_and_fill {
        let mut path = Path::from_polygon(&poly);
        path.close();
        stroke_into(&mut line, &path, outline_width.max(0.5));
    }
    // 塗る範囲は、投げ縄の外接矩形を線の太さだけ広げた中（Qt と同じ）。
    let margin = (outline_width / 2.0).ceil() + 2.0;
    let (l, t, r, b) = poly.iter().fold(
        (
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ),
        |(l, t, r, b), p| (l.min(p.x), t.min(p.y), r.max(p.x), b.max(p.y)),
    );
    let area = Rect {
        x: (l - margin).floor().max(0.0) as usize,
        y: (t - margin).floor().max(0.0) as usize,
        width: 0,
        height: 0,
    };
    let x1 = ((r + margin).ceil().min(w as f64)).max(0.0) as usize;
    let y1 = ((b + margin).ceil().min(h as f64)).max(0.0) as usize;
    if x1 <= area.x || y1 <= area.y {
        return Ok(None);
    }
    let mut active = Bits::new(w, h);
    for y in area.y..y1 {
        for x in area.x..x1 {
            let i = y * w + x;
            active.bits[i] = fill.bits[i] | line.bits[i];
        }
    }
    if let Some(clip) = clip_bits(&s.tools, w, h) {
        active.and(&clip);
    }
    let reference = reference_image(s, &image)?;
    let rgba = reference.rgba();
    // 対象色：たどれる画素だけを塗る。
    if settings.bucket_target_color != "all" {
        let target = target_color(&settings.bucket_target_color);
        let enclosed = matches!(
            target,
            TargetColor::OpaqueAndInnerTransparent | TargetColor::TransparentAndClosedOpaque
        );
        let mut eligible = fill_target_mask(rgba, w, h, target);
        if enclosed {
            // 内部の穴は投げ縄の外側まで含む元の参照画像で判断する。
            let holes = enclosed_by(&eligible, w, h).map_err(|e| e.to_string())?;
            for (e, hole) in eligible.iter_mut().zip(holes) {
                *e |= hole;
            }
        }
        active.and(&eligible);
    }
    // マスク色：選んだ色の上だけ。
    if let Some(filter) = &s.tools.mask_filter {
        let allowed: Vec<u8> = rgba
            .chunks_exact(4)
            .map(|px| {
                let rgb = [px[0], px[1], px[2]];
                u8::from(
                    !filter.colors.is_empty()
                        && ((px[3] > 0 && filter.colors.contains(&rgb))
                            || (filter.background && is_background(px))),
                )
            })
            .collect();
        active.and(&allowed);
    }
    // 境界線の内側だけ：投げ縄の重心に近い画素からつながる所だけ。
    if settings.lasso_inside_boundary {
        let mut passable = active.bits.clone();
        if settings.bucket_target_color == "all" {
            for (i, cell) in passable.iter_mut().enumerate() {
                *cell &= u8::from(is_background(&rgba[i * 4..i * 4 + 4]));
            }
        }
        let n = points.len() as f64;
        let cx = points.iter().map(|p| p[0]).sum::<f64>() / n;
        let cy = points.iter().map(|p| p[1]).sum::<f64>() / n;
        let seed = passable
            .iter()
            .enumerate()
            .filter(|(_, &v)| v != 0)
            .map(|(i, _)| ((i % w) as f64, (i / w) as f64))
            .min_by(|a, b| {
                let da = (a.0 - cx).powi(2) + (a.1 - cy).powi(2);
                let db = (b.0 - cx).powi(2) + (b.1 - cy).powi(2);
                da.total_cmp(&db)
            });
        match seed {
            Some((x, y)) => {
                let region =
                    pma_core::regions::flood_fill(&passable, w, h, &[(x as i64, y as i64)]);
                active.and(&region);
            }
            None => active.bits.fill(0),
        }
    }
    let fill_rgb = if outline_and_fill {
        settings.source_rgb(false)
    } else {
        settings.paint_rgb()
    };
    let line_rgb = settings.source_rgb(true);
    let opacity = settings.fill_opacity as f64 / 100.0;
    commit_overlay(
        s,
        &image,
        frame,
        layer,
        &active,
        |i| Paint {
            rgb: if line.bits[i] != 0 {
                line_rgb
            } else {
                fill_rgb
            },
        },
        opacity,
    )
}

/// 図形ツールのドラッグの範囲（`_shape_rect`）。比率固定なら正方形にする。
fn shape_rect(settings: &Settings, start: [f64; 2], end: [f64; 2]) -> (f64, f64, f64, f64) {
    let mut end = end;
    if settings.shape_lock_ratio {
        let (dx, dy) = (end[0] - start[0], end[1] - start[1]);
        let size = dx.abs().max(dy.abs());
        end = [
            start[0] + if dx >= 0.0 { size } else { -size },
            start[1] + if dy >= 0.0 { size } else { -size },
        ];
    }
    let (x0, x1) = (start[0].min(end[0]), start[0].max(end[0]));
    let (y0, y1) = (start[1].min(end[1]), start[1].max(end[1]));
    (x0, y0, x1 - x0, y1 - y0)
}

/// 図形の経路（`_shape_path`）。範囲が空なら None。
pub fn shape_path(settings: &Settings, start: [f64; 2], end: [f64; 2]) -> Option<Path> {
    let (x, y, w, h) = shape_rect(settings, start, end);
    if w <= 0.0 || h <= 0.0 {
        return None;
    }
    if settings.shape_type == "ellipse" {
        return Some(ellipse_path((x, y, w, h)));
    }
    let corners = settings.shape_corners.max(3) as usize;
    let points: Vec<brush::Point> = if corners == 4 {
        // 4角はひし形ではなく、ドラッグ範囲に沿う長方形にする。QRectF の right は x + w。
        vec![
            brush::Point::new(x, y),
            brush::Point::new(x + w, y),
            brush::Point::new(x + w, y + h),
            brush::Point::new(x, y + h),
        ]
    } else {
        let (cx, cy) = (x + w / 2.0, y + h / 2.0);
        (0..corners)
            .map(|i| {
                let a = -std::f64::consts::FRAC_PI_2
                    + 2.0 * std::f64::consts::PI * i as f64 / corners as f64;
                brush::Point::new(cx + a.cos() * w / 2.0, cy + a.sin() * h / 2.0)
            })
            .collect()
    };
    let mut path = Path::new();
    path.move_to(points[0]);
    for p in &points[1..] {
        path.line_to(*p);
    }
    path.close();
    Some(path)
}

/// 図形ツール（`commit_shape_tool`）。直線・曲線はライン（[`line`]）で描く。
pub fn shape(s: &mut Session, start: [f64; 2], end: [f64; 2]) -> Result<Option<Rect>, String> {
    let settings = s.tools.settings.clone();
    let Some(path) = shape_path(&settings, start, end) else {
        return Ok(None);
    };
    let (image, frame, layer) = active_image(s)?;
    let (w, h) = (image.width(), image.height());
    let width = (settings.shape_outline_width as f64 / 2.0).max(0.5);
    let mut fill = Bits::new(w, h);
    if settings.shape_fill_inside {
        fill_into(&mut fill, &path);
    }
    let mut outline = Bits::new(w, h);
    stroke_into(&mut outline, &path, width);
    let mut active = Bits::new(w, h);
    for i in 0..w * h {
        active.bits[i] = fill.bits[i] | outline.bits[i];
    }
    if let Some(clip) = clip_bits(&s.tools, w, h) {
        active.and(&clip);
    }
    let (outline_rgb, fill_rgb) = if settings.shape_sub_outline_main_fill {
        (settings.source_rgb(true), settings.source_rgb(false))
    } else {
        (settings.paint_rgb(), settings.paint_rgb())
    };
    commit_overlay(
        s,
        &image,
        frame,
        layer,
        &active,
        |i| Paint {
            rgb: if outline.bits[i] != 0 {
                outline_rgb
            } else {
                fill_rgb
            },
        },
        1.0,
    )
}

/// 直線・曲線（`commit_line_tool`）。`control` があれば 2 次曲線。`brush_line` は
/// ブラシの Shift＋クリックの直線（入り抜きを使わない）。
pub fn line(
    s: &mut Session,
    start: [f64; 2],
    end: [f64; 2],
    control: Option<[f64; 2]>,
    brush_line: bool,
) -> Result<Option<Rect>, String> {
    let settings = s.tools.settings.clone();
    let (image, frame, layer) = active_image(s)?;
    let (w, h) = (image.width(), image.height());
    let p0 = brush::Point::new(start[0], start[1]);
    let p1 = brush::Point::new(end[0], end[1]);
    let mut path = Path::new();
    path.move_to(p0);
    match control {
        Some(c) => {
            // 2 次曲線を 3 次の形にする（QPainterPath::quadTo と同じ制御点）。
            let c = brush::Point::new(c[0], c[1]);
            let c1 = brush::Point::new(
                p0.x + 2.0 / 3.0 * (c.x - p0.x),
                p0.y + 2.0 / 3.0 * (c.y - p0.y),
            );
            let c2 = brush::Point::new(
                p1.x + 2.0 / 3.0 * (c.x - p1.x),
                p1.y + 2.0 / 3.0 * (c.y - p1.y),
            );
            path.cubic_to(c1, c2, p1);
        }
        None => path.line_to(p1),
    }
    let base = settings.brush_size.max(0.5);
    let (mut start_w, mut end_w) = (None, None);
    let (mut start_curve, mut end_curve) = (1.0, 1.0);
    if !brush_line {
        if settings.line_taper_in {
            start_w = Some(settings.line_taper_in_size as f64 / 2.0);
            start_curve = settings.line_taper_in_curve as f64 / 100.0;
        }
        if settings.line_taper_out {
            end_w = Some(settings.line_taper_out_size as f64 / 2.0);
            end_curve = 1.0 / (settings.line_taper_out_curve as f64 / 100.0).max(0.05);
        }
    }
    let mut active = Bits::new(w, h);
    if start_w.is_none() && end_w.is_none() {
        stroke_into(&mut active, &path, base);
    } else {
        // 入り抜き（`_draw_tapered_path`）：長さに沿って幅を変えた短い線を重ねる。
        let flat = flattened(&path);
        let pts: Vec<brush::Point> = flat
            .elements()
            .iter()
            .filter_map(|e| match *e {
                PathElement::MoveTo(p) | PathElement::LineTo(p) => Some(p),
                PathElement::CubicTo(..) => None,
            })
            .collect();
        let mut cumulative = vec![0.0];
        for pair in pts.windows(2) {
            let d = (pair[1].x - pair[0].x).hypot(pair[1].y - pair[0].y);
            cumulative.push(cumulative.last().copied().unwrap_or(0.0) + d);
        }
        let total = cumulative.last().copied().unwrap_or(0.0);
        let at = |ratio: f64| -> brush::Point {
            let target = ratio * total;
            let i = cumulative
                .iter()
                .position(|&c| c >= target)
                .unwrap_or(cumulative.len() - 1)
                .max(1)
                .min(pts.len().saturating_sub(1));
            if pts.len() < 2 {
                return pts.first().copied().unwrap_or(p0);
            }
            let (a, b) = (pts[i - 1], pts[i]);
            let span = (cumulative[i] - cumulative[i - 1]).max(1e-12);
            let t = ((target - cumulative[i - 1]) / span).clamp(0.0, 1.0);
            brush::Point::new(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)
        };
        let length = total.max(1.0);
        let steps = ((length * 1.5).ceil() as usize).clamp(2, 4096);
        let mut previous = at(0.0);
        for index in 1..=steps {
            let ratio = index as f64 / steps as f64;
            let point = at(ratio);
            let mut width = base;
            if let (Some(sw), true) = (start_w, ratio <= 0.5) {
                let eased = (ratio * 2.0).clamp(0.0, 1.0).powf(start_curve.max(0.05));
                width = sw + (base - sw) * eased;
            } else if let (Some(ew), true) = (end_w, ratio >= 0.5) {
                let eased = ((ratio - 0.5) * 2.0)
                    .clamp(0.0, 1.0)
                    .powf(end_curve.max(0.05));
                width = base + (ew - base) * eased;
            }
            let mut segment = Path::new();
            segment.move_to(previous);
            segment.line_to(point);
            stroke_into(&mut active, &segment, width.max(0.5));
            previous = point;
        }
    }
    if let Some(clip) = clip_bits(&s.tools, w, h) {
        active.and(&clip);
    }
    let rgb = settings.paint_rgb();
    commit_overlay(s, &image, frame, layer, &active, |_| Paint { rgb }, 1.0)
}

/// 選択範囲の中を消去（`delete_selection_contents`）。
pub fn delete_selection_contents(s: &mut Session) -> Result<Option<Rect>, String> {
    let Some(selection) = s.tools.selection.selection().cloned() else {
        return Ok(None);
    };
    if s.tools.selection.transform_active() {
        return Ok(None);
    }
    let (mut image, frame, layer) = active_image(s)?;
    let Some(edit) = delete_selection(&mut image, &selection).map_err(|e| e.to_string())? else {
        return Ok(None);
    };
    let pixels = image.copy_rgba_rect(edit.rect).map_err(|e| e.to_string())?;
    write_active(s, frame, layer, edit.rect, pixels)
}

// ---------------------------------------------------------------------------
// HOOK：計算を pma-core へ移しているところの操作。ここで呼ぶだけにしてある。
// ---------------------------------------------------------------------------

/// バケツ（押して離す）。なぞり塗りは paint_ops.rs の `paint_bucket_*`。
pub fn bucket_fill(s: &mut Session, x: f64, y: f64) -> Result<Option<Rect>, String> {
    let feedback = crate::paint_ops::bucket_click(s, x, y, false)?;
    match (feedback.rect, feedback.message) {
        (None, Some(message)) => Err(message),
        (rect, _) => Ok(rect),
    }
}

/// 串刺し塗り（選択レイヤーのすべてのコマ）。指定待ちのキーは paint_ops.rs。
pub fn skewer_fill(s: &mut Session, x: f64, y: f64) -> Result<Option<Rect>, String> {
    let view = crate::paint_ops::skewer_click(s, x, y, false, &Default::default())?;
    match view.message {
        Some(message) if view.pending => Err(message),
        _ => Ok(crate::paint_ops::full_rect_of(s)),
    }
}

/// 色置換（全コマ、`replace_color_at`）。線の色を尋ねるのは paint_ops.rs の
/// `paint_replace_color`。ここは尋ねずに置き換える。
pub fn replace_color(s: &mut Session, x: f64, y: f64) -> Result<Option<Rect>, String> {
    let (_, feedback) = crate::paint_ops::replace_color(s, x, y, true)?;
    match (feedback.rect, feedback.message) {
        (None, Some(message)) => Err(message),
        (rect, _) => Ok(rect),
    }
}

/// HOOK(transform)：選択範囲の変形（自由変形・拡大縮小・メッシュ変形・回転・反転・確定・
/// キャンセル）。`kind` はボタンの名前（free / scale / mesh / rotate_left / rotate_right /
/// commit / cancel）。
pub fn transform(_s: &mut Session, kind: &str) -> Result<(), String> {
    let name = match kind {
        "free" => "自由変形",
        "scale" => "拡大縮小",
        "mesh" => "メッシュ変形",
        "rotate_left" => "左へ90°回転",
        "rotate_right" => "右へ90°回転",
        "commit" => "変形を確定",
        "cancel" => "変形をキャンセル",
        _ => "変形",
    };
    Err(pending(name))
}

/// ゴミ取り・塗り抜け・線周りのノイズ・線整えを適用（paint_ops.rs）。
pub fn dust_apply(s: &mut Session) -> Result<Option<Rect>, String> {
    let feedback = crate::paint_ops::dust_apply(s, None)?;
    match (feedback.rect, feedback.message) {
        (None, Some(message)) => Err(message),
        (rect, _) => Ok(rect),
    }
}

/// HOOK(action)：アクションのパネルのボタン（Qt は actions フォルダーの Python）。
pub fn run_action(_s: &mut Session, id: &str) -> Result<(), String> {
    let name = match id {
        "same_image_replacement" => "同一画像から色置換",
        "main_line_repaint" => "MainLineRepaint",
        other => other,
    };
    Err(pending(name))
}

// ---------------------------------------------------------------------------
// 選択範囲・スポイト
// ---------------------------------------------------------------------------

fn image_area(s: &Session) -> Option<Rect> {
    let p = s.project.as_ref()?;
    let (w, h) = p.document.size();
    Some(Rect {
        x: 0,
        y: 0,
        width: w,
        height: h,
    })
}

/// 投げ縄選択を確定する。頂点が 3 つ未満なら何もしないで偽。
pub fn select_lasso(s: &mut Session, points: &[[f64; 2]]) -> bool {
    let Some(first) = points.first() else {
        return false;
    };
    let sel = &mut s.tools.selection;
    sel.begin_lasso((first[0], first[1]));
    for p in &points[1..] {
        sel.extend_lasso((p[0], p[1]));
    }
    sel.finish_lasso()
}

/// 長方形選択を確定する。
pub fn select_rect(s: &mut Session, start: [f64; 2], end: [f64; 2]) -> bool {
    let sel = &mut s.tools.selection;
    sel.begin_rect((start[0], start[1]));
    sel.update_rect((end[0], end[1]));
    sel.finish_rect()
}

/// 自動選択（`auto_select_region`）。`mode` は replace / add / subtract。
pub fn select_auto(s: &mut Session, x: f64, y: f64, mode: &str) -> Result<bool, String> {
    let (image, _, _) = active_image(s)?;
    let settings = &s.tools.settings;
    let options = ContiguousOptions {
        adjacent: settings.bucket_adjacent,
        close_gap: (settings.bucket_adjacent && settings.bucket_close_gap)
            .then_some(settings.bucket_gap_width.max(1) as usize),
        require_closed: settings.bucket_require_closed,
    };
    match contiguous_region(&image, x.floor() as i64, y.floor() as i64, options) {
        ContiguousResult::OutOfBounds => Ok(false),
        ContiguousResult::OpenToEdge => {
            Err("領域がキャンバス端まで開いているため選択しませんでした。".into())
        }
        ContiguousResult::Region(region) => {
            let mode = match mode {
                "add" => CombineMode::Add,
                "subtract" => CombineMode::Subtract,
                _ => CombineMode::Replace,
            };
            s.tools
                .selection
                .combine(&region, mode)
                .map_err(|e| e.to_string())?;
            Ok(true)
        }
    }
}

/// 色で選択（「この色を選択範囲にする」）。使用色パネルから呼ぶ。
pub fn select_color(s: &mut Session, rgb: [u8; 3]) -> Result<bool, String> {
    let (image, _, _) = active_image(s)?;
    s.tools.selection.set_mask(color_mask(&image, rgb));
    Ok(s.tools.selection.has_selection())
}

/// 描画範囲の自動選択（`auto_select_used_area`）。不透明な画素が無ければ偽。
pub fn select_used_area(s: &mut Session) -> Result<bool, String> {
    let (image, _, _) = active_image(s)?;
    match used_area(&image) {
        Some(selection) => {
            s.tools.selection.set_selection(Some(selection));
            Ok(true)
        }
        None => Ok(false),
    }
}

pub fn select_all(s: &mut Session) -> bool {
    match image_area(s) {
        Some(area) => s.tools.selection.select_all(area),
        None => false,
    }
}

pub fn select_invert(s: &mut Session) -> bool {
    match image_area(s) {
        Some(area) => s.tools.selection.invert(area.width, area.height, area),
        None => false,
    }
}

/// スポイトの結果。
#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct Picked {
    pub rgb: [u8; 3],
    /// 透明（背景）を拾った。描画色は背景色のモードになる。
    pub background: bool,
}

/// スポイト（`sample_color`）。`source` が `display` なら上のレイヤーから順に、表示して
/// いるレイヤーの不透明な画素の元の色を拾う（表示不透明度は混ぜない）。`layer` なら選択
/// レイヤーの画素だけを見る。拾った色を今の描画色（メイン・サブ）にする。
pub fn eyedropper(s: &mut Session, x: f64, y: f64, source: &str) -> Result<Option<Picked>, String> {
    let Some(picked) = sample_color(s, x, y, source)? else {
        return Ok(None);
    };
    let (rgb, background) = (picked.rgb, picked.background);
    let settings = &mut s.tools.settings;
    if background {
        settings.color_mode = "transparent".into();
    } else if settings.color_mode == "sub" {
        settings.color_sub = rgb;
    } else {
        settings.color_main = rgb;
        settings.color_mode = "main".into();
    }
    Ok(Some(picked))
}

// 【色のパネル】スポイトの拾い方だけ（描画色は変えない）。カラーピッカー・置換色のスポイトも使う。
pub fn sample_color(s: &Session, x: f64, y: f64, source: &str) -> Result<Option<Picked>, String> {
    let p = s.project.as_ref().ok_or("作品を開いていません")?;
    let doc = &p.document;
    let (w, h) = doc.size();
    if x < 0.0 || y < 0.0 || x >= w as f64 || y >= h as f64 {
        return Ok(None);
    }
    let (px, py) = (x as usize, y as usize);
    let (frame, active) = crate::session::current_index(doc);
    let layers = compose::frame_layers(doc, frame).map_err(|e| e.to_string())?;
    let pixel = |image: &RgbaImage| -> Option<[u8; 4]> {
        if px >= image.width() || py >= image.height() {
            return None;
        }
        let i = (py * image.width() + px) * 4;
        let v = &image.rgba()[i..i + 4];
        Some([v[0], v[1], v[2], v[3]])
    };
    let mut found = None;
    let order: Vec<usize> = if source == "layer" {
        vec![active]
    } else {
        (0..layers.len()).rev().collect()
    };
    for index in order {
        let Some(Some(layer)) = layers.get(index) else {
            continue;
        };
        if source != "layer" && (!layer.settings.visible || layer.settings.opacity <= 0.0) {
            continue;
        }
        if let Some(v) = layer.image.and_then(pixel) {
            if v[3] > 0 {
                found = Some([v[0], v[1], v[2]]);
                break;
            }
        }
    }
    let rgb = found.unwrap_or([255, 255, 255]);
    let background = rgb == [255, 255, 255];
    Ok(Some(Picked { rgb, background }))
}

/// 筆圧カーブの値（カーブの表示用。`pressure_bezier_at`）。
fn curve_samples(points: &[[f64; 2]], count: usize) -> Vec<f64> {
    let anchors: Vec<(f64, f64)> = points.iter().map(|p| (p[0], p[1])).collect();
    (0..=count)
        .map(|i| pressure_bezier_at(&anchors, i as f64 / count as f64))
        .collect()
}

// ---------------------------------------------------------------------------
// 画面との窓口（Tauri のコマンド）
// ---------------------------------------------------------------------------

/// 設定の保存先を決めて読む（起動のとき）。
pub fn install(app: &tauri::AppHandle) {
    use tauri::Manager;
    let path = app
        .path()
        .app_config_dir()
        .ok()
        .map(|dir| dir.join("config.json"));
    let state = app.state::<Shared>();
    let mut s = state.lock().unwrap();
    s.tools.config = path;
    s.tools.load();
}

/// ツールの状態を変える操作をし、`tools-changed` の事件で画面へ知らせる。
/// 文書が変わったら `document-changed`（with_session）も送る。
fn with_tools<T>(
    app: &tauri::AppHandle,
    save: bool,
    f: impl FnOnce(&mut Session) -> Result<T, String>,
) -> Result<T, String> {
    let mut view = None;
    let out = crate::with_session(app, "canvas", |s| {
        let out = f(s);
        if save {
            if let Err(e) = s.tools.save() {
                eprintln!("ツールの設定を保存できなかった：{e}");
            }
        }
        view = Some(s.tools.view());
        out
    });
    if let Some(view) = view {
        let _ = app.emit("tools-changed", view);
    }
    out
}

fn rect_response(s: &Session, rect: Option<Rect>) -> Response {
    Response::new(pack_rect(&s.display, rect))
}

#[tauri::command]
pub fn tools_state(session: State<Shared>) -> View {
    session.lock().unwrap().tools.view()
}

/// 設定の一部を変える（`patch` は設定の項目名と値の表）。
#[tauri::command]
pub fn tools_set(app: tauri::AppHandle, patch: Value) -> Result<View, String> {
    with_tools(&app, true, |s| {
        s.tools.apply_patch(&patch)?;
        Ok(s.tools.view())
    })
}

/// HOOK(mask_colors)：使用色パネルの「選んだマスク色の上だけに描く」。None は全体に描く。
#[tauri::command]
pub fn tools_set_mask_colors(
    app: tauri::AppHandle,
    colors: Option<Vec<[u8; 3]>>,
    background: bool,
) -> Result<View, String> {
    with_tools(&app, false, |s| {
        s.tools.mask_filter = colors.map(|colors| MaskColorFilter { colors, background });
        Ok(s.tools.view())
    })
}

#[tauri::command]
pub fn tools_pressure_curve(points: Vec<[f64; 2]>, count: usize) -> Vec<f64> {
    curve_samples(&points, count.clamp(2, 1000))
}

#[tauri::command]
pub fn tool_lasso_fill(app: tauri::AppHandle, points: Vec<[f64; 2]>) -> Result<Response, String> {
    with_tools(&app, false, |s| {
        let r = lasso_fill(s, &points)?;
        Ok(rect_response(s, r))
    })
}

#[tauri::command]
pub fn tool_shape(
    app: tauri::AppHandle,
    start: [f64; 2],
    end: [f64; 2],
) -> Result<Response, String> {
    with_tools(&app, false, |s| {
        let r = shape(s, start, end)?;
        Ok(rect_response(s, r))
    })
}

#[tauri::command]
pub fn tool_line(
    app: tauri::AppHandle,
    start: [f64; 2],
    end: [f64; 2],
    control: Option<[f64; 2]>,
    brush_line: bool,
) -> Result<Response, String> {
    with_tools(&app, false, |s| {
        let r = line(s, start, end, control, brush_line)?;
        Ok(rect_response(s, r))
    })
}

/// バケツ系（bucket / skewer_fill / replace_color）のクリック。HOOK（[`bucket_fill`] など）。
#[tauri::command]
pub fn tool_fill(app: tauri::AppHandle, tool: String, x: f64, y: f64) -> Result<Response, String> {
    with_tools(&app, false, |s| {
        let r = match tool.as_str() {
            "skewer_fill" => skewer_fill(s, x, y)?,
            "replace_color" => replace_color(s, x, y)?,
            _ => bucket_fill(s, x, y)?,
        };
        Ok(rect_response(s, r))
    })
}

#[tauri::command]
pub fn tool_transform(app: tauri::AppHandle, kind: String) -> Result<View, String> {
    with_tools(&app, false, |s| {
        transform(s, &kind)?;
        Ok(s.tools.view())
    })
}

#[tauri::command]
pub fn tool_dust_apply(app: tauri::AppHandle) -> Result<Response, String> {
    with_tools(&app, false, |s| {
        let r = dust_apply(s)?;
        Ok(rect_response(s, r))
    })
}

#[tauri::command]
pub fn action_run(app: tauri::AppHandle, id: String) -> Result<(), String> {
    with_tools(&app, false, |s| run_action(s, &id))
}

#[tauri::command]
pub fn tool_eyedropper(
    app: tauri::AppHandle,
    x: f64,
    y: f64,
    source: Option<String>,
) -> Result<Option<Picked>, String> {
    with_tools(&app, true, |s| {
        eyedropper(s, x, y, source.as_deref().unwrap_or("display"))
    })
}

// 【色のパネル】スポイトの拾い方だけ（描画色は変えない。color_state.js の eyedrop）。
#[tauri::command]
pub fn tool_sample_color(
    session: State<Shared>,
    x: f64,
    y: f64,
    source: Option<String>,
) -> Result<Option<Picked>, String> {
    let s = session.lock().unwrap();
    sample_color(&s, x, y, source.as_deref().unwrap_or("display"))
}

/// 選択範囲の操作。`kind` は lasso / rect / auto / color / used_area / all / invert / clear。
/// どのツールからでも呼べる（解除は issue #110）。
#[derive(Deserialize)]
pub struct SelectArgs {
    pub kind: String,
    #[serde(default)]
    pub points: Vec<[f64; 2]>,
    #[serde(default)]
    pub mode: Option<String>,
    #[serde(default)]
    pub rgb: Option<[u8; 3]>,
}

pub fn select(s: &mut Session, args: &SelectArgs) -> Result<bool, String> {
    let p = |i: usize| args.points.get(i).copied().unwrap_or([0.0, 0.0]);
    match args.kind.as_str() {
        "lasso" => Ok(select_lasso(s, &args.points)),
        "rect" => Ok(select_rect(s, p(0), p(1))),
        "auto" => select_auto(
            s,
            p(0)[0],
            p(0)[1],
            args.mode.as_deref().unwrap_or("replace"),
        ),
        "color" => select_color(s, args.rgb.unwrap_or([0, 0, 0])),
        "used_area" => select_used_area(s),
        "all" => Ok(select_all(s)),
        "invert" => Ok(select_invert(s)),
        "clear" => Ok(s.tools.selection.clear().changed),
        other => Err(format!("知らない選択の操作：{other}")),
    }
}

#[tauri::command]
pub fn selection_edit(app: tauri::AppHandle, args: SelectArgs) -> Result<View, String> {
    with_tools(&app, false, |s| {
        select(s, &args)?;
        Ok(s.tools.view())
    })
}

/// 選択範囲の中を消去（Delete）。
#[tauri::command]
pub fn selection_delete(app: tauri::AppHandle) -> Result<Response, String> {
    with_tools(&app, false, |s| {
        let r = delete_selection_contents(s)?;
        Ok(rect_response(s, r))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::session::Point;
    use std::path::Path as FsPath;

    fn open(name: &str) -> Session {
        let mut s = Session::empty(8, 8);
        s.open(
            &FsPath::new(env!("CARGO_MANIFEST_DIR"))
                .join("../tests/fixtures/projects/v2")
                .join(name),
        )
        .unwrap();
        s
    }

    /// 図形の画素を確かめる大きめの文書（試験用の見本の文書、64×48）。
    fn big() -> Session {
        let mut s = Session::empty(8, 8);
        s.open_document(crate::timeline::sample_document(12, (64, 48)).unwrap())
            .unwrap();
        s
    }

    const FIXTURES: [&str; 4] = [
        "basic-bank-sheet.pman",
        "paper-draft.pman",
        "pending-compat.pman",
        "tween-initial-display.pman",
    ];

    fn size(s: &Session) -> (f64, f64) {
        let (w, h) = s.project.as_ref().unwrap().document.size();
        (w as f64, h as f64)
    }

    fn pixel(s: &Session, x: usize, y: usize) -> [u8; 4] {
        let (image, _, _) = active_image(s).unwrap();
        let i = (y * image.width() + x) * 4;
        let v = &image.rgba()[i..i + 4];
        [v[0], v[1], v[2], v[3]]
    }

    fn expected_label(s: &Session) -> String {
        let doc = &s.project.as_ref().unwrap().document;
        let (f, l) = crate::session::current_index(doc);
        drawing_label(doc, f, l)
    }

    #[test]
    fn drawing_label_names_layer_and_number_like_qt() {
        let s = open("basic-bank-sheet.pman");
        let label = expected_label(&s);
        assert!(
            label == "描画" || (label.starts_with("描画（") && label.ends_with("番）")),
            "{label}"
        );
    }

    #[test]
    fn lasso_fill_paints_inside_and_undoes() {
        let mut s = big();
        let (w, h) = size(&s);
        s.tools.settings.color_main = [10, 20, 30];
        let label = expected_label(&s);
        let poly = [
            [w * 0.3, h * 0.3],
            [w * 0.7, h * 0.3],
            [w * 0.7, h * 0.7],
            [w * 0.3, h * 0.7],
        ];
        let rect = lasso_fill(&mut s, &poly).unwrap().unwrap();
        assert!(rect.width > 0);
        let (cx, cy) = ((w * 0.5) as usize, (h * 0.5) as usize);
        assert_eq!(pixel(&s, cx, cy), [10, 20, 30, 255]);
        assert_eq!(s.history().undo, vec![label]);
        s.undo().unwrap();
        assert_ne!(pixel(&s, cx, cy), [10, 20, 30, 255]);
        // 3 点未満は何もしない。
        assert_eq!(lasso_fill(&mut s, &poly[..2]).unwrap(), None);
    }

    #[test]
    fn lasso_fill_respects_selection_clip_opacity_and_eraser() {
        let mut s = big();
        let (w, h) = size(&s);
        s.tools.settings.color_main = [0, 0, 255];
        // 左半分だけを選ぶ。
        assert!(select_rect(&mut s, [0.0, 0.0], [w * 0.5, h]));
        let poly = [
            [w * 0.2, h * 0.2],
            [w * 0.8, h * 0.2],
            [w * 0.8, h * 0.8],
            [w * 0.2, h * 0.8],
        ];
        lasso_fill(&mut s, &poly).unwrap();
        assert_eq!(
            pixel(&s, (w * 0.3) as usize, (h * 0.5) as usize),
            [0, 0, 255, 255]
        );
        assert_ne!(
            pixel(&s, (w * 0.7) as usize, (h * 0.5) as usize),
            [0, 0, 255, 255]
        );
        // 背景色（消しゴム）で塗ると透明になる。
        s.tools.selection.clear();
        s.tools.settings.color_mode = "transparent".into();
        lasso_fill(&mut s, &poly).unwrap();
        assert_eq!(pixel(&s, (w * 0.5) as usize, (h * 0.5) as usize)[3], 0);
        // 不透明度 50%：透明の上は白と混ぜる。
        s.tools.settings.color_mode = "main".into();
        s.tools.settings.color_main = [0, 0, 0];
        s.tools.settings.fill_opacity = 50;
        lasso_fill(&mut s, &poly).unwrap();
        assert_eq!(
            pixel(&s, (w * 0.5) as usize, (h * 0.5) as usize),
            [128, 128, 128, 255]
        );
    }

    #[test]
    fn lasso_fill_outline_uses_sub_and_fill_main() {
        let mut s = big();
        let (w, h) = size(&s);
        s.tools.settings.color_main = [1, 2, 3];
        s.tools.settings.color_sub = [200, 0, 0];
        s.tools.settings.lasso_main_outline_sub_fill = true;
        s.tools.settings.lasso_outline_width = 6;
        let (x0, y0, x1, y1) = (
            (w * 0.25).round(),
            (h * 0.25).round(),
            (w * 0.75).round(),
            (h * 0.75).round(),
        );
        lasso_fill(&mut s, &[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]).unwrap();
        assert_eq!(
            pixel(&s, x0 as usize, ((y0 + y1) / 2.0) as usize),
            [200, 0, 0, 255]
        );
        assert_eq!(
            pixel(&s, ((x0 + x1) / 2.0) as usize, ((y0 + y1) / 2.0) as usize),
            [1, 2, 3, 255]
        );
    }

    #[test]
    fn mask_color_filter_blocks_lasso_fill() {
        let mut s = big();
        let (w, h) = size(&s);
        s.tools.mask_filter = Some(MaskColorFilter {
            colors: vec![],
            background: false,
        });
        let poly = [[w * 0.2, h * 0.2], [w * 0.8, h * 0.2], [w * 0.5, h * 0.8]];
        assert_eq!(lasso_fill(&mut s, &poly).unwrap(), None);
        assert!(s.history().undo.is_empty());
    }

    #[test]
    fn shapes_draw_rectangle_outline_fill_and_ellipse() {
        let mut s = big();
        let (w, h) = size(&s);
        s.tools.settings.color_main = [5, 6, 7];
        let (a, b) = (
            [(w * 0.2).round(), (h * 0.2).round()],
            [(w * 0.8).round(), (h * 0.8).round()],
        );
        let label = expected_label(&s);
        shape(&mut s, a, b).unwrap().unwrap();
        // 長方形の輪郭だけ：辺は塗り、中は塗らない。
        assert_eq!(
            pixel(&s, a[0] as usize, ((a[1] + b[1]) / 2.0) as usize),
            [5, 6, 7, 255]
        );
        assert_ne!(
            pixel(
                &s,
                ((a[0] + b[0]) / 2.0) as usize,
                ((a[1] + b[1]) / 2.0) as usize
            ),
            [5, 6, 7, 255]
        );
        assert_eq!(s.history().undo, vec![label]);
        // 内側を塗る・サブ色を実線。
        s.tools.settings.shape_fill_inside = true;
        s.tools.settings.shape_sub_outline_main_fill = true;
        s.tools.settings.color_sub = [9, 9, 9];
        shape(&mut s, a, b).unwrap();
        assert_eq!(
            pixel(
                &s,
                ((a[0] + b[0]) / 2.0) as usize,
                ((a[1] + b[1]) / 2.0) as usize
            ),
            [5, 6, 7, 255]
        );
        assert_eq!(
            pixel(&s, a[0] as usize, ((a[1] + b[1]) / 2.0) as usize),
            [9, 9, 9, 255]
        );
        // 楕円（輪郭は折れ線で近づける）と多角形。
        s.tools.settings.shape_type = "ellipse".into();
        s.tools.settings.shape_fill_inside = false;
        s.tools.settings.shape_sub_outline_main_fill = false;
        s.tools.settings.color_main = [50, 60, 70];
        shape(&mut s, a, b).unwrap().unwrap();
        assert_eq!(
            pixel(&s, ((a[0] + b[0]) / 2.0) as usize, a[1] as usize),
            [50, 60, 70, 255]
        );
        s.tools.settings.shape_type = "polygon".into();
        s.tools.settings.shape_corners = 5;
        assert!(shape(&mut s, a, b).unwrap().is_some());
        // 範囲が空なら何もしない。
        assert_eq!(shape(&mut s, a, [a[0], b[1]]).unwrap(), None);
        assert_eq!(s.history().undo.len(), 4);
    }

    #[test]
    fn lock_ratio_makes_a_square() {
        let settings = Settings {
            shape_lock_ratio: true,
            ..Settings::default()
        };
        assert_eq!(
            shape_rect(&settings, [10.0, 10.0], [30.0, 15.0]),
            (10.0, 10.0, 20.0, 20.0)
        );
        assert_eq!(
            shape_rect(&settings, [10.0, 10.0], [0.0, 15.0]),
            (0.0, 10.0, 10.0, 10.0)
        );
    }

    #[test]
    fn straight_curved_and_tapered_lines() {
        let mut s = big();
        let (w, h) = size(&s);
        s.tools.settings.brush_size = 4.0;
        let (a, b) = ([w * 0.1, h * 0.5], [w * 0.9, h * 0.5]);
        line(&mut s, a, b, None, false).unwrap().unwrap();
        assert_eq!(
            pixel(&s, (w * 0.5) as usize, (h * 0.5) as usize),
            [0, 0, 0, 255]
        );
        s.tools.settings.color_main = [3, 3, 3];
        line(
            &mut s,
            [w * 0.1, h * 0.2],
            [w * 0.9, h * 0.2],
            Some([w * 0.5, h * 0.6]),
            false,
        )
        .unwrap()
        .unwrap();
        // 2 次曲線の頂点（t = 0.5）は、両端と制御点の中ほど（h * 0.4）。
        assert_eq!(
            pixel(&s, (w * 0.5) as usize, (h * 0.4) as usize),
            [3, 3, 3, 255]
        );
        s.tools.settings.line_taper_in = true;
        s.tools.settings.line_taper_out = true;
        s.tools.settings.color_main = [4, 4, 4];
        line(&mut s, [w * 0.1, h * 0.8], [w * 0.9, h * 0.8], None, false)
            .unwrap()
            .unwrap();
        assert_eq!(
            pixel(&s, (w * 0.5) as usize, (h * 0.8) as usize),
            [4, 4, 4, 255]
        );
        assert_eq!(s.history().undo.len(), 3);
    }

    #[test]
    fn selections_lasso_rect_auto_color_used_invert_and_clear_from_any_tool() {
        let mut s = big();
        let (w, h) = size(&s);
        assert!(!select_lasso(&mut s, &[[0.0, 0.0], [1.0, 1.0]]));
        assert!(s.tools.view().selection.is_none());
        assert!(select_lasso(
            &mut s,
            &[[1.0, 1.0], [w * 0.5, 1.0], [1.0, h * 0.5]]
        ));
        assert!(s.tools.view().can_clear_selection);
        // ブラシに切り替えても解除できる（#110）。
        s.tools
            .apply_patch(&serde_json::json!({"tool": "brush"}))
            .unwrap();
        assert!(s.tools.view().can_clear_selection);
        assert!(select(
            &mut s,
            &SelectArgs {
                kind: "clear".into(),
                points: vec![],
                mode: None,
                rgb: None
            }
        )
        .unwrap());
        assert!(s.tools.view().selection.is_none());
        assert!(select_rect(&mut s, [2.0, 2.0], [10.0, 12.0]));
        assert_eq!(
            s.tools.view().selection.unwrap().bounds,
            Some([2, 2, 8, 10])
        );
        assert!(select_invert(&mut s));
        assert!(s.tools.view().selection.unwrap().is_mask);
        assert!(select_all(&mut s));
        let _ = select_used_area(&mut s).unwrap();
        // 自動選択：透明の所を押すと背景がまとまって選ばれる。足す・引く。
        s.tools.selection.clear();
        let _ = select_auto(&mut s, 0.0, 0.0, "replace");
        let _ = select_auto(&mut s, 0.0, 0.0, "subtract");
        assert!(s.tools.view().selection.is_none());
        let _ = select_color(&mut s, [1, 2, 3]).unwrap();
        // 選択範囲の操作は文書を変えない。
        assert!(s.history().undo.is_empty());
    }

    #[test]
    fn delete_selection_clears_pixels_with_drawing_label() {
        let mut s = big();
        let (w, h) = size(&s);
        s.tools.settings.color_main = [7, 7, 7];
        let poly = [
            [w * 0.1, h * 0.1],
            [w * 0.9, h * 0.1],
            [w * 0.9, h * 0.9],
            [w * 0.1, h * 0.9],
        ];
        lasso_fill(&mut s, &poly).unwrap();
        select_rect(&mut s, [w * 0.4, h * 0.4], [w * 0.6, h * 0.6]);
        let label = expected_label(&s);
        delete_selection_contents(&mut s).unwrap().unwrap();
        assert_eq!(pixel(&s, (w * 0.5) as usize, (h * 0.5) as usize)[3], 0);
        assert_eq!(s.history().undo, vec![label.clone(), label]);
    }

    #[test]
    fn eyedropper_picks_display_and_layer_and_background() {
        let mut s = big();
        let (w, h) = size(&s);
        s.tools.settings.color_main = [11, 22, 33];
        let poly = [
            [w * 0.1, h * 0.1],
            [w * 0.9, h * 0.1],
            [w * 0.9, h * 0.9],
            [w * 0.1, h * 0.9],
        ];
        lasso_fill(&mut s, &poly).unwrap();
        s.tools.settings.color_main = [0, 0, 0];
        let got = eyedropper(&mut s, w * 0.5, h * 0.5, "layer")
            .unwrap()
            .unwrap();
        assert_eq!(
            got,
            Picked {
                rgb: [11, 22, 33],
                background: false
            }
        );
        assert_eq!(s.tools.settings.color_main, [11, 22, 33]);
        // サブを選んでいればサブへ。
        s.tools.settings.color_mode = "sub".into();
        let got = eyedropper(&mut s, w * 0.5, h * 0.5, "display")
            .unwrap()
            .unwrap();
        assert!(!got.background);
        assert_eq!(s.tools.settings.color_sub, got.rgb);
        // 透明を拾うと背景色のモード。
        select_rect(&mut s, [0.0, 0.0], [w, h]);
        delete_selection_contents(&mut s).unwrap();
        let got = eyedropper(&mut s, w * 0.5, h * 0.5, "layer")
            .unwrap()
            .unwrap();
        assert!(got.background);
        assert_eq!(s.tools.settings.color_mode, "transparent");
        assert_eq!(eyedropper(&mut s, -1.0, 0.0, "layer").unwrap(), None);
    }

    #[test]
    fn brush_settings_carry_pressure_stabilizer_mask_and_selection() {
        let mut s = big();
        s.tools.settings.brush_size = 12.5;
        s.tools.settings.stabilizer = 40;
        s.tools.settings.pressure_uses_global = false;
        s.tools.settings.brush_pressure = Pressure {
            enabled: true,
            minimum: 0.3,
            maximum: 2.0,
            points: vec![[0.0, 0.0], [1.0, 1.0]],
        };
        s.tools.mask_filter = Some(MaskColorFilter {
            colors: vec![[1, 1, 1]],
            background: true,
        });
        select_rect(&mut s, [0.0, 0.0], [5.0, 5.0]);
        let tool = StrokeTool {
            size: None,
            color: None,
            eraser: false,
            stabilizer: None,
            zoom: 2.0,
        };
        let doc = s.project.as_ref().unwrap().document.clone();
        let b = s.tools.brush_settings(&tool, &doc, 0, doc.size());
        assert_eq!(b.size, 12.5);
        assert_eq!(b.stabilizer, 40);
        assert_eq!(b.zoom, 2.0);
        assert_eq!(b.pressure.minimum, 0.3);
        assert_eq!(b.pressure.maximum, 2.0);
        assert!(b.mask_colors.is_some());
        assert!(matches!(b.selection, Some(brush::Selection::Polygon(_))));
        // 背景色のモードは白（消しゴム）。
        s.tools.settings.color_mode = "transparent".into();
        assert_eq!(
            s.tools.brush_settings(&tool, &doc, 0, doc.size()).color,
            brush::ERASER_RGB
        );
    }

    #[test]
    fn brush_stroke_uses_tool_state_and_qt_label() {
        let mut s = big();
        let (w, h) = size(&s);
        s.tools.settings.color_main = [90, 80, 70];
        let label = expected_label(&s);
        let pt = |x: f64, y: f64| Point {
            x,
            y,
            pressure: 1.0,
            pen: false,
            time: 0.0,
        };
        let tool = StrokeTool {
            size: None,
            color: None,
            eraser: false,
            stabilizer: None,
            zoom: 1.0,
        };
        s.stroke_begin(&tool, pt(w * 0.2, h * 0.5)).unwrap();
        s.stroke_move(&[pt(w * 0.8, h * 0.5)]).unwrap();
        s.stroke_end(Some(pt(w * 0.8, h * 0.5))).unwrap();
        assert_eq!(
            pixel(&s, (w * 0.5) as usize, (h * 0.5) as usize),
            [90, 80, 70, 255]
        );
        assert_eq!(s.history().undo, vec![label]);
    }

    #[test]
    fn patch_merges_normalizes_and_persists() {
        let dir = std::env::temp_dir().join(format!("pma-tools-test-{}", std::process::id()));
        let path = dir.join("config.json");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            &path,
            r#"{"other_panel": 1, "last_paint_session": {"subview": {"zoom": 3}}}"#,
        )
        .unwrap();
        let mut t = ToolState {
            config: Some(path.clone()),
            ..ToolState::default()
        };
        t.apply_patch(&serde_json::json!({
            "tool": "closed_fill", "brush_size": 9999, "tool_groups_enabled": true,
            "bucket_include_colors": [[1, 2, 3], null, null]
        }))
        .unwrap();
        assert_eq!(t.settings.tool, "bucket");
        assert_eq!(t.settings.brush_size, 400.0);
        t.save().unwrap();
        let root: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(root["other_panel"], 1);
        assert_eq!(root["last_paint_session"]["brush_size"], 400.0);
        assert_eq!(root["last_paint_session"]["subview"]["zoom"], 3);
        assert_eq!(root["tool_groups_enabled"], true);
        assert_eq!(
            root["bucket_include_colors"][0],
            serde_json::json!([1, 2, 3])
        );
        let mut again = ToolState {
            config: Some(path.clone()),
            ..ToolState::default()
        };
        again.load();
        assert_eq!(again.settings, t.settings);
        // 既定ではまとめない（#109）。
        assert!(!Settings::default().tool_groups_enabled);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn hooks_report_pending_without_changing_the_document() {
        let mut s = big();
        assert!(transform(&mut s, "mesh")
            .unwrap_err()
            .contains("メッシュ変形"));
        assert!(run_action(&mut s, "main_line_repaint")
            .unwrap_err()
            .contains("MainLineRepaint"));
        assert!(s.history().undo.is_empty());
    }

    #[test]
    fn opening_another_project_clears_the_selection() {
        let mut s = open("basic-bank-sheet.pman");
        select_rect(&mut s, [0.0, 0.0], [4.0, 4.0]);
        s.open(
            &FsPath::new(env!("CARGO_MANIFEST_DIR"))
                .join("../tests/fixtures/projects/v2/paper-draft.pman"),
        )
        .unwrap();
        assert!(s.tools.view().selection.is_none());
    }

    #[test]
    fn pressure_curve_samples_follow_anchors() {
        let v = curve_samples(&[[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]], 4);
        assert_eq!(v.len(), 5);
        assert!((v[2] - 0.5).abs() < 1e-6);
    }

    #[test]
    fn every_tool_runs_on_every_fixture() {
        for name in FIXTURES {
            let mut s = open(name);
            let (w, h) = size(&s);
            let poly = [[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]];
            // 書けない位置（未確定の互換データなど）はエラーで返り、文書は変わらない。
            for result in [
                lasso_fill(&mut s, &poly),
                shape(&mut s, [0.0, 0.0], [w, h]),
                line(&mut s, [0.0, 0.0], [w, h], None, false),
                line(&mut s, [0.0, 0.0], [w, h], Some([w, 0.0]), true),
            ] {
                if let Err(e) = result {
                    assert!(!e.is_empty(), "{name}");
                }
            }
            let undo = s.history().undo;
            assert!(
                undo.iter().all(|l| l.starts_with("描画")),
                "{name}: {undo:?}"
            );
            assert!(select_all(&mut s), "{name}");
            assert!(select_invert(&mut s) || s.tools.view().selection.is_none());
            let _ = select_used_area(&mut s).unwrap();
            let _ = select_auto(&mut s, 0.0, 0.0, "add");
            let _ = eyedropper(&mut s, 0.0, 0.0, "display").unwrap();
            let _ = eyedropper(&mut s, 0.0, 0.0, "layer").unwrap();
            select_all(&mut s);
            let _ = delete_selection_contents(&mut s);
            assert!(s.tools.selection.clear().changed, "{name}");
        }
    }

    #[test]
    fn drawing_label_counts_hold_frames_by_key_number() {
        for name in FIXTURES {
            let s = open(name);
            let doc = &s.project.as_ref().unwrap().document;
            for frame in 0..doc.frame_order().len() {
                for layer in 0..doc.track_order().len() {
                    let label = drawing_label(doc, frame, layer);
                    assert!(label == "描画" || label.ends_with("番）"), "{label}");
                }
            }
        }
    }
}
