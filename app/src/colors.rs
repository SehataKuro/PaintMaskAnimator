//! 描画色・カラーサークル・カラースライダー・使用色・カラーチャートの窓口。
//!
//! Qt 版の `color_panel.py`（使用色パネル）・`toolpanel.py`（描画色・サークル・スライダー）・
//! `main_window_used_color.py`（統合・色置換・削除・置換色のファイル）・
//! `main_window_color_chart.py`（カラーチャート）にあたる。状態と判断は pma-core の
//! `used_colors`（並び・親子・選択・表示・鍵・フォルダー・置換色・チャート）にあり、ここは
//! 作品（`Session`）とのつなぎと、画面へ渡す形だけを持つ。
//!
//! - 使用色は、`with_session` のたびに [`sync`] で選択レイヤーの絵から数え直す（絵の世代で
//!   控えるので、変わっていなければ数えない）。
//! - 表示の絞り込み（目）と置き換えのプレビューは `DisplayOptions` にしてキャンバスの合成へ渡す。
//!   変わったら `display-changed` の事件で画面へ知らせる。
//! - 使用色パネルの編集は、Qt と同じ名前で作品の履歴に積む（件に使用色パネルの状態を添える）。
//! - 描画色（メイン・サブ・背景・描く色の選び）の正本はツールの設定（tools.rs の `Settings`）。
//!   カラーサークル・スライダー・ツールのパネルの色の欄・ブラシはみなそこを読み書きし、
//!   `tools-changed` の事件で合わせる。作品の metadata の `colors` はそこへ読み書きする。
//! - 描ける範囲（#104：選択・鍵）は [`sync`] でツールのマスク色の絞り込み
//!   （`ToolState::mask_filter`）にして、ブラシ・投げ縄塗りなどの塗りと同じ1か所で絞る。

use crate::session::Session;
use crate::tools::Settings;
use crate::with_session;
use pma_core::brush::stroke::MaskColorFilter;
use pma_core::compose::{Background, DisplayOptions};
use pma_core::project::json::Json;
use pma_core::used_colors::chart::{self, Chart, Hit, Role};
use pma_core::used_colors::pixels::{self, ScanCache};
use pma_core::used_colors::{
    code, hex, parse_hex, Column, DropMode, Modifiers, Recorded, Rgb, Row, Snapshot, UsedColors,
    BACKGROUND,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::PathBuf;
use tauri::{Emitter, Manager, State};

/// 描画色と使用色パネルの状態（作品ごと）。
pub struct ColorUi {
    pub palette: UsedColors,
    pub chart: Chart,
    /// キャンバスの表示の絞り込み・置き換えのプレビュー。
    pub display: DisplayOptions,
    scan_cache: ScanCache,
    scanned: Option<(bool, u64, u64, usize)>,
    /// 作品から読んだ使用色フォルダー。最初に数えたあとに当てる（Qt の `_pending_palette_categories`）。
    pending_categories: Option<Json>,
    /// 置換色のカラーピッカーを開いた時点の状態（閉じたら1件の Undo にする）。
    replacement_edit: Option<Snapshot>,
    /// 「色相・彩度・明度」のプレビュー（ダイアログを開いているあいだ）。
    hsv_preview: Option<Vec<(Rgb, Rgb)>>,
    /// 使用色パネルを変えた（次の [`sync`] で `palette-changed` を出す）。
    touched: bool,
    /// いまの表示を合成したときの背景色の表示色（変わったら [`sync`] が合成し直す）。
    shown_background: Rgb,
}

impl Default for ColorUi {
    fn default() -> Self {
        Self {
            palette: UsedColors::new(),
            chart: Chart::default(),
            display: DisplayOptions::default(),
            scan_cache: ScanCache::default(),
            scanned: None,
            pending_categories: None,
            replacement_edit: None,
            hsv_preview: None,
            touched: false,
            shown_background: BACKGROUND,
        }
    }
}

fn tup(c: [u8; 3]) -> Rgb {
    (c[0], c[1], c[2])
}

/// 作品の metadata の描画色（`colors`）をツールの設定へ読む。無い欄は今の設定のまま。
pub fn drawing_from_metadata(metadata: &Json, settings: &mut Settings) {
    let Some(colors) = metadata
        .get("colors")
        .filter(|c| matches!(c, Json::Object(_)))
    else {
        return;
    };
    settings.color_main = rgb3(color_field(Some(colors), "main", tup(settings.color_main)));
    settings.color_sub = rgb3(color_field(Some(colors), "sub", tup(settings.color_sub)));
    settings.background_color = rgb3(color_field(
        Some(colors),
        "background",
        tup(settings.background_color),
    ));
    if let Some(Json::Str(mode)) = colors.get("mode") {
        if ["main", "sub", "transparent"].contains(&mode.as_str()) {
            settings.color_mode = mode.clone();
        }
    }
}

/// キャンバスの下地。背景色の表示色が白でなければ、その色で見せる。
pub fn canvas_background(settings: &Settings) -> Background {
    if tup(settings.background_color) == BACKGROUND {
        Background::White
    } else {
        Background::Color(settings.background_color)
    }
}

/// 描ける範囲（#104：選択した色だけ・鍵の色を除く）を、ツールのマスク色の絞り込みにする。
/// 絞らないなら None。
pub fn edit_filter(palette: &UsedColors) -> Option<MaskColorFilter> {
    let range = palette.edit_range();
    if range.is_unrestricted() {
        return None;
    }
    let colors = palette
        .ordered_non_background()
        .into_iter()
        .filter(|&c| range.permits(c))
        .map(rgb3)
        .collect();
    Some(MaskColorFilter {
        colors,
        background: range.permits(BACKGROUND),
    })
}

fn color_field(colors: Option<&Json>, key: &str, default: Rgb) -> Rgb {
    match colors.and_then(|c| c.get(key)) {
        Some(Json::Str(text)) => parse_hex(text).unwrap_or(default),
        _ => default,
    }
}

impl ColorUi {
    /// 作品の metadata から読む（Qt の `_prepare_ui_state` と `restore_categories`・`set_data`）。
    pub fn from_metadata(metadata: &Json) -> Self {
        Self {
            chart: chart::normalize(metadata.get("color_chart").unwrap_or(&Json::Null)),
            pending_categories: metadata.get("used_color_categories").cloned(),
            ..Self::default()
        }
    }

    /// 作品の metadata へ書く（Qt の `MainWindow._project_metadata` の該当部分）。
    pub fn store(&self, settings: &Settings, metadata: &mut Json) {
        if !matches!(metadata, Json::Object(_)) {
            return;
        }
        let colors = Json::Object(vec![
            ("main".into(), Json::Str(hex(tup(settings.color_main)))),
            ("sub".into(), Json::Str(hex(tup(settings.color_sub)))),
            ("mode".into(), Json::Str(settings.color_mode.clone())),
            (
                "background".into(),
                Json::Str(hex(tup(settings.background_color))),
            ),
        ]);
        metadata.set("colors", colors);
        // まだ数えていない（フォルダーを当てていない）なら、読んだままを書き戻す。
        let categories = match &self.pending_categories {
            Some(pending) => pending.clone(),
            None => self.palette.serialize_categories(),
        };
        metadata.set("used_color_categories", categories);
        metadata.set("color_chart", self.chart.to_json());
    }

    fn wanted_display(&self) -> DisplayOptions {
        DisplayOptions {
            visible_colors: self.palette.visible_filter(),
            color_remap: self
                .hsv_preview
                .clone()
                .unwrap_or_else(|| self.palette.replacement_preview_mapping()),
            ..self.display.clone()
        }
    }
}

/// [`sync`] の結果。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Synced {
    /// 使用色を数え直した（または使用色パネルを当て直した）。
    pub palette: bool,
    /// キャンバスの表示の設定が変わり、合成し直した。
    pub display: bool,
}

/// 文書を変えたあとに呼ぶ（`with_session`）。絵か選択レイヤーが変わっていれば使用色を数え直し、
/// 取り消しで戻す使用色パネルの状態があれば当て、表示の設定をそろえる。
pub fn sync(s: &mut Session) -> Synced {
    let mut out = Synced::default();
    let stamp = s.scan_stamp();
    if s.colors.scanned != Some(stamp) || s.colors.palette.has_pending_restore() {
        let scan = match &s.project {
            Some(p) => pixels::scan_layer(&p.document, stamp.3, &mut s.colors.scan_cache)
                .unwrap_or_default(),
            None => pixels::Scan::default(),
        };
        let c = &mut s.colors;
        c.palette.set_colors(&scan.colors, scan.exceeded);
        if let Some(pending) = c.pending_categories.take() {
            c.palette.restore_categories(Some(&pending));
        }
        c.scanned = Some(stamp);
        out.palette = true;
    }
    out.palette |= std::mem::take(&mut s.colors.touched);
    // 描ける範囲は、ブラシ・塗りと同じマスク色の絞り込みで効かせる（tools.rs）。
    s.tools.mask_filter = edit_filter(&s.colors.palette);
    // 選んでいる色と登録した置換色を、塗りの計算（paint_ops.rs）へ渡す（ゴミ取りの
    // 「選択色を対象」・線整え・バケツの「置換色を塗り替え」）。
    let palette = &s.colors.palette;
    s.tools.paint.used_selection = palette.selected().iter().map(|&c| rgb3(c)).collect();
    s.tools.paint.replacements = palette
        .replacements_to_apply()
        .into_iter()
        .map(|(a, b)| (rgb3(a), rgb3(b)))
        .collect();
    let background = tup(s.tools.settings.background_color);
    let wanted = s.colors.wanted_display();
    // 絞り込みは選択レイヤーにだけ掛けるので、選択レイヤーが変わったときも合成し直す。
    let layer_moved = out.palette && wanted != DisplayOptions::default();
    if wanted != s.colors.display || layer_moved || background != s.colors.shown_background {
        s.colors.display = wanted;
        s.colors.shown_background = background;
        let _ = s.redisplay();
        out.display = true;
    }
    out
}

/// [`sync`] の結果を画面へ知らせる（`with_session` が錠を外してから呼ぶ）。
pub fn emit_synced(app: &tauri::AppHandle, synced: Synced, view: Option<PaletteView>) {
    if let Some(view) = view {
        let _ = app.emit("palette-changed", view);
    }
    if synced.display {
        let _ = app.emit("display-changed", ());
    }
}

/// 使用色パネルの編集を履歴に積む（文書は変えないので、今の文書を「前」として積む）。
fn record(s: &mut Session, recorded: Option<Recorded>) -> bool {
    let (Some(recorded), Some(p)) = (recorded, s.project.as_mut()) else {
        return false;
    };
    p.undo
        .record_with_palette(recorded.label, &p.document, Some(recorded.before));
    true
}

// ---- 画面へ渡す形 ---------------------------------------------------------

/// 使用色の1行。
#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum RowView {
    Color {
        hex: String,
        code: String,
        rgb: [u8; 3],
        background: bool,
        in_category: bool,
        enabled: bool,
        locked: bool,
        selected: bool,
        focus: bool,
        /// 置換色（置き換えモードで出す）。
        replacement: Option<String>,
        /// 親子のグループの縦線の色（ルートの親の色）。
        line: Option<String>,
        /// 子なら親の色。
        parent: Option<String>,
        /// 親なら子の数。
        children: usize,
    },
    Folder {
        name: String,
        collapsed: bool,
        /// 中の色が全部表示なら true、全部非表示なら false、混在なら null。
        visible: Option<bool>,
        count: usize,
    },
}

/// 使用色パネルの全体（`palette-changed` の事件と `palette_view`）。
#[derive(Clone, Debug, Serialize)]
pub struct PaletteView {
    pub loaded: bool,
    pub rows: Vec<RowView>,
    pub count_text: String,
    pub selected_count: usize,
    pub focus: Option<String>,
    pub replace_mode: bool,
    pub replacement_preview: bool,
    /// 統合できるか（統合先＝フォーカス、統合元＝それ以外の選択した色）。
    pub can_merge: bool,
    pub replacement_count: usize,
    /// 置き換えの対象の数（選択した色。無ければフォーカス）。
    pub replace_target_count: usize,
    /// 描ける範囲を絞っているか（選択か鍵がある）。
    pub restricted: bool,
    pub chart_summary: String,
    /// 使用色フォルダーと、入っている色（並びの順。影の書き出しなどが使う）。
    pub folders: Vec<FolderView>,
}

/// 使用色フォルダー1つ。
#[derive(Clone, Debug, Serialize)]
pub struct FolderView {
    pub name: String,
    pub colors: Vec<[u8; 3]>,
}

fn rgb3(rgb: Rgb) -> [u8; 3] {
    [rgb.0, rgb.1, rgb.2]
}

impl Session {
    pub fn palette_view(&self) -> PaletteView {
        let c = &self.colors;
        let palette = &c.palette;
        let rows = palette
            .rows()
            .into_iter()
            .map(|row| match row {
                Row::Color { rgb, in_category } => RowView::Color {
                    hex: hex(rgb),
                    code: code(rgb),
                    rgb: rgb3(rgb),
                    background: rgb == BACKGROUND,
                    in_category,
                    enabled: palette.is_enabled(rgb),
                    locked: palette.is_locked(rgb),
                    selected: palette.is_selected(rgb),
                    focus: palette.focus() == Some(rgb),
                    replacement: palette.replacement(rgb).map(hex),
                    line: palette.group_line_color(rgb).map(hex),
                    parent: palette.parent_of(rgb).map(hex),
                    children: palette.child_count(rgb),
                },
                Row::Folder { name } => RowView::Folder {
                    collapsed: palette.is_collapsed(&name),
                    visible: palette.category_visibility(&name),
                    count: palette.category_members(&name).len(),
                    name,
                },
            })
            .collect();
        PaletteView {
            loaded: self.project.is_some(),
            rows,
            count_text: palette.count_text(),
            selected_count: palette.selected().len(),
            focus: palette.focus().map(hex),
            replace_mode: palette.replace_mode,
            replacement_preview: palette.replacement_preview,
            can_merge: palette.merge_plan().is_some(),
            replacement_count: palette.replacements_to_apply().len(),
            replace_target_count: replace_targets(palette).len(),
            restricted: !palette.edit_range().is_unrestricted(),
            chart_summary: c.chart.summary(),
            folders: palette
                .categories()
                .iter()
                .map(|name| FolderView {
                    name: name.clone(),
                    colors: palette
                        .category_members(name)
                        .into_iter()
                        .map(rgb3)
                        .collect(),
                })
                .collect(),
        }
    }
}

/// 置き換えの対象：選択した色（背景以外）。無ければフォーカスの色（#104：フォーカスは
/// 選択と別なので、1色だけ決めたいときに選択しなくてよい）。
fn replace_targets(palette: &UsedColors) -> Vec<Rgb> {
    let targets = palette.replace_targets();
    if !targets.is_empty() {
        return targets;
    }
    palette
        .focus()
        .filter(|&rgb| rgb != BACKGROUND)
        .into_iter()
        .collect()
}

// ---- 使用色パネルの操作 ---------------------------------------------------

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ColumnArg {
    Visibility,
    Lock,
    Selection,
}

impl From<ColumnArg> for Column {
    fn from(c: ColumnArg) -> Self {
        match c {
            ColumnArg::Visibility => Column::Visibility,
            ColumnArg::Lock => Column::Lock,
            ColumnArg::Selection => Column::Selection,
        }
    }
}

/// 画面からの使用色パネルの操作。色は `[r, g, b]`。
#[derive(Clone, Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum PaletteOp {
    /// 行のクリック（フォーカス。Shift＝範囲選択、Ctrl/⌘＝選択に追加・解除）。
    Click {
        rgb: [u8; 3],
        #[serde(default)]
        shift: bool,
        #[serde(default)]
        ctrl: bool,
    },
    /// フォーカスを外す・動かす。
    Focus {
        rgb: Option<[u8; 3]>,
    },
    /// スポイトで拾った色へフォーカスを動かす（選択は変えない）。
    FocusMatching {
        rgb: [u8; 3],
    },
    SweepBegin {
        column: ColumnArg,
        rgb: [u8; 3],
        checked: bool,
    },
    SweepMove {
        rgb: [u8; 3],
    },
    SweepEnd,
    ClearSelection,
    SelectOnly {
        rgb: [u8; 3],
    },
    ShowAll,
    ShowOnly {
        rgb: [u8; 3],
    },
    Isolate {
        rgb: [u8; 3],
    },
    CategoryVisible {
        name: String,
        visible: bool,
    },
    UnlockAll,
    /// 【Group C】鍵の列のメニュー（Qt の _show_mask_context_menu）。ON は描ける（鍵を外す）。
    SetLocked {
        rgb: [u8; 3],
        locked: bool,
    },
    LockOthers {
        rgb: [u8; 3],
    },
    /// 色を色へ落とす。`ratio` は行の高さに対する位置（0..1）。
    Drop {
        dragged: [u8; 3],
        target: [u8; 3],
        ratio: f64,
    },
    DropToCategory {
        dragged: [u8; 3],
        name: String,
    },
    ToUncategorized {
        rgb: [u8; 3],
    },
    ToNewCategory {
        rgb: [u8; 3],
        name: String,
    },
    Ungroup {
        rgb: [u8; 3],
    },
    ClearGroups,
    CreateCategory {
        name: String,
    },
    RenameCategory {
        old: String,
        new: String,
    },
    DeleteCategory {
        name: String,
    },
    Collapse {
        name: String,
        collapsed: bool,
    },
    ReplaceMode {
        on: bool,
    },
    ReplacementPreview {
        on: bool,
    },
    /// 置換色を決める。`targets` が無ければ選択した色（無ければフォーカス）。
    SetReplacement {
        #[serde(default)]
        targets: Vec<[u8; 3]>,
        color: [u8; 3],
    },
    ClearReplacements {
        #[serde(default)]
        targets: Vec<[u8; 3]>,
    },
    ClearAllReplacements,
    /// 置換色のカラーピッカーを開いた（閉じるまでの変更を1件の Undo にする）。
    ReplacementEditBegin,
    ReplacementEditPreview {
        rgb: [u8; 3],
        color: [u8; 3],
    },
    /// 閉じた。`commit` が偽（キャンセル）なら開く前に戻す。`clear` なら外す（「外す」）。
    ReplacementEditEnd {
        commit: bool,
        #[serde(default)]
        clear: Option<[u8; 3]>,
    },
    /// 選んだ色の置換色を、いまの描画色にする。
    ReplaceWithDrawingColor,
    /// 統合（選択した色をフォーカスの色へ）。
    Merge,
    /// 右クリックの「この色を消す」（選択中なら選択した色すべて）。
    Delete {
        rgb: [u8; 3],
    },
    /// 「全コマを置き換え」。
    ApplyReplacements,
    /// 「色相・彩度・明度」のプレビュー（None で終わる）。
    HsvPreview {
        #[serde(default)]
        rgbs: Vec<[u8; 3]>,
        shift: Option<[i32; 3]>,
    },
    /// 「色相・彩度・明度」の OK。
    HsvApply {
        rgbs: Vec<[u8; 3]>,
        shift: [i32; 3],
    },
    /// 「この色を選択範囲にする」（Qt の `focus_used_color`）：その色が使われている最初のコマへ
    /// 移り、その色の画素を画素の選択範囲にする。`rgb` が無ければ、フォーカス・最後に選んだ色・
    /// 描画色の順に決める（`focus_selected_used_color`、メニューの `a_focus_used_color`）。
    SelectPixels {
        #[serde(default)]
        rgb: Option<[u8; 3]>,
    },
    /// 「選択色だけ表示」（Qt の `isolate_selected_color`）：選択レイヤーを描画色だけ表示する
    /// （レイヤーの色フィルタ。絵は変えない）。`rgb` が無ければ描画色。
    IsolateLayerColor {
        #[serde(default)]
        rgb: Option<[u8; 3]>,
    },
    /// 「特定色表示を解除」（Qt の `clear_selected_color_filter`）。
    ClearLayerColorFilter,
}

fn rgb(v: [u8; 3]) -> Rgb {
    (v[0], v[1], v[2])
}

fn rgbs(v: &[[u8; 3]]) -> Vec<Rgb> {
    v.iter().copied().map(rgb).collect()
}

/// 操作の結果。`message` はステータスバーへ出す。
#[derive(Clone, Debug, Default, Serialize)]
pub struct Outcome {
    pub message: Option<String>,
    /// 履歴に積んだか・画像を変えたか。
    pub changed: bool,
}

impl Outcome {
    fn changed(changed: bool) -> Self {
        Self {
            message: None,
            changed,
        }
    }

    fn message(message: impl Into<String>, changed: bool) -> Self {
        Self {
            message: Some(message.into()),
            changed,
        }
    }
}

/// 統合・色置換・削除の名前（Qt の `OPERATION_LABELS`）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Remap {
    Replace,
    Delete,
    Merge,
}

impl Remap {
    fn label(self) -> &'static str {
        match self {
            Self::Replace => "色置換",
            Self::Delete => "色削除",
            Self::Merge => "色統合",
        }
    }
}

/// 3桁ごとにカンマ（Python の `{:,}`）。
fn thousands(n: usize) -> String {
    let digits = n.to_string();
    let mut out = String::new();
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(ch);
    }
    out
}

impl Session {
    /// 使用色パネルの操作を行う。
    pub fn palette_op(&mut self, op: PaletteOp) -> Result<Outcome, String> {
        let p = &mut self.colors.palette;
        let recorded = match op {
            PaletteOp::Click {
                rgb: c,
                shift,
                ctrl,
            } => {
                p.click_row(rgb(c), Modifiers { shift, ctrl });
                None
            }
            PaletteOp::Focus { rgb: c } => {
                p.set_focus(c.map(rgb));
                None
            }
            PaletteOp::FocusMatching { rgb: c } => {
                p.focus_matching(rgb(c));
                None
            }
            PaletteOp::SweepBegin {
                column,
                rgb: c,
                checked,
            } => {
                p.sweep_begin(column.into(), rgb(c), checked);
                None
            }
            PaletteOp::SweepMove { rgb: c } => {
                p.sweep_move(rgb(c));
                None
            }
            PaletteOp::SweepEnd => p.sweep_end(),
            PaletteOp::ClearSelection => {
                p.clear_selection();
                None
            }
            PaletteOp::SelectOnly { rgb: c } => {
                p.select_only(rgb(c));
                None
            }
            PaletteOp::ShowAll => p.show_all(),
            PaletteOp::ShowOnly { rgb: c } => p.show_only(rgb(c)),
            PaletteOp::Isolate { rgb: c } => p.isolate_visible(rgb(c)),
            PaletteOp::CategoryVisible { name, visible } => p.set_category_visible(&name, visible),
            PaletteOp::UnlockAll => p.unlock_all(),
            PaletteOp::SetLocked { rgb: c, locked } => p.set_locked(rgb(c), locked),
            PaletteOp::LockOthers { rgb: c } => p.lock_others(rgb(c)),
            PaletteOp::Drop {
                dragged,
                target,
                ratio,
            } => p.drop_color(rgb(dragged), rgb(target), DropMode::from_ratio(ratio)),
            PaletteOp::DropToCategory { dragged, name } => p.move_to_category(rgb(dragged), &name),
            PaletteOp::ToUncategorized { rgb: c } => p.move_to_uncategorized(rgb(c)),
            PaletteOp::ToNewCategory { rgb: c, name } => {
                let records = p.move_to_new_category(rgb(c), &name);
                let changed = !records.is_empty();
                for r in records {
                    record(self, Some(r));
                }
                return Ok(Outcome::changed(changed));
            }
            PaletteOp::Ungroup { rgb: c } => p.ungroup(rgb(c)),
            PaletteOp::ClearGroups => p.clear_groups(),
            PaletteOp::CreateCategory { name } => p.create_category(&name).1,
            PaletteOp::RenameCategory { old, new } => p.rename_category(&old, &new),
            PaletteOp::DeleteCategory { name } => p.delete_category(&name),
            PaletteOp::Collapse { name, collapsed } => {
                p.set_category_collapsed(&name, collapsed);
                None
            }
            PaletteOp::ReplaceMode { on } => {
                p.replace_mode = on;
                None
            }
            PaletteOp::ReplacementPreview { on } => {
                p.replacement_preview = on;
                None
            }
            PaletteOp::SetReplacement { targets, color } => {
                let targets = if targets.is_empty() {
                    replace_targets(p)
                } else {
                    rgbs(&targets)
                };
                if targets.is_empty() {
                    return Ok(Outcome::message("置き換える色を選んでください。", false));
                }
                p.set_replacements(&targets, rgb(color))
            }
            PaletteOp::ClearReplacements { targets } => {
                let targets = if targets.is_empty() {
                    replace_targets(p)
                } else {
                    rgbs(&targets)
                };
                if targets.is_empty() {
                    return Ok(Outcome::default());
                }
                p.clear_replacements(&targets)
            }
            PaletteOp::ClearAllReplacements => p.clear_all_replacements().1,
            PaletteOp::ReplacementEditBegin => {
                self.colors.replacement_edit = Some(p.capture());
                None
            }
            PaletteOp::ReplacementEditPreview { rgb: c, color } => {
                p.set_replacement_raw(rgb(c), rgb(color));
                None
            }
            PaletteOp::ReplacementEditEnd { commit, clear } => {
                let Some(before) = self.colors.replacement_edit.take() else {
                    return Ok(Outcome::default());
                };
                let p = &mut self.colors.palette;
                if let Some(c) = clear {
                    p.restore(&before);
                    p.clear_replacements(&[rgb(c)])
                } else if commit {
                    p.record_since("置換色を設定", before)
                } else {
                    p.restore(&before);
                    None
                }
            }
            PaletteOp::ReplaceWithDrawingColor => {
                // 描く色（背景色のモードは消しゴムの白＝透明にする）。
                let current = self.tools.settings.paint_rgb();
                let p = &mut self.colors.palette;
                let targets = replace_targets(p);
                if targets.is_empty() {
                    return Ok(Outcome::message("置き換える色を選んでください。", false));
                }
                p.set_replacements(&targets, rgb(current))
            }
            PaletteOp::Merge => return self.palette_merge(),
            PaletteOp::Delete { rgb: c } => return self.palette_delete(rgb(c)),
            PaletteOp::ApplyReplacements => {
                let mapping = self.colors.palette.replacements_to_apply();
                return self.palette_remap(mapping, Remap::Replace);
            }
            PaletteOp::SelectPixels { rgb: c } => return self.palette_select_pixels(c),
            PaletteOp::IsolateLayerColor { rgb: c } => return self.layer_color_filter(Some(c)),
            PaletteOp::ClearLayerColorFilter => return self.layer_color_filter(None),
            PaletteOp::HsvPreview { rgbs: list, shift } => {
                self.colors.hsv_preview = shift.map(|[h, s, v]| {
                    pixels::shifted_mapping(&rgbs(&list), h, s, v)
                        .into_iter()
                        .collect()
                });
                None
            }
            PaletteOp::HsvApply { rgbs: list, shift } => {
                self.colors.hsv_preview = None;
                let [h, s, v] = shift;
                let mapping = pixels::shifted_mapping(&rgbs(&list), h, s, v);
                if pixels::effective_mapping(&mapping).is_empty() {
                    return Ok(Outcome::message("色は変わりませんでした。", false));
                }
                return self.palette_remap(mapping, Remap::Replace);
            }
        };
        Ok(Outcome::changed(record(self, recorded)))
    }

    /// 統合：選択した色をフォーカスの色へ塗り替える（#104）。
    fn palette_merge(&mut self) -> Result<Outcome, String> {
        let Some((target, sources)) = self.colors.palette.merge_plan() else {
            let message = if self.colors.palette.focus().is_none() {
                "統合先の色（枠で囲んだ行）を選んでください。"
            } else {
                "統合する使用色が選択されていません。"
            };
            return Ok(Outcome::message(message, false));
        };
        let mapping = sources.into_iter().map(|s| (s, target)).collect();
        let out = self.palette_remap(mapping, Remap::Merge)?;
        if out.changed {
            // 統合元は消えるので選択を外す。統合先はフォーカスのまま（Qt の `_retain_parent_selection`）。
            self.colors.palette.clear_selection();
            self.colors.palette.set_focus(Some(target));
        }
        Ok(out)
    }

    /// 「この色を消す」：#FFFFFF（透明）へ統合する。
    fn palette_delete(&mut self, clicked: Rgb) -> Result<Outcome, String> {
        let selected = self.colors.palette.delete_sources(clicked);
        if selected.is_empty() {
            return Ok(Outcome::message(
                "削除する使用色が選択されていません。",
                false,
            ));
        }
        let mapping = selected.iter().map(|&s| (s, BACKGROUND)).collect();
        let out = self.palette_remap(mapping, Remap::Delete)?;
        if !out.changed {
            return Ok(out);
        }
        self.colors.palette.clear_selection_after_delete();
        Ok(Outcome::message(
            format!("{}色を #FFFFFF へ統合しました。", selected.len()),
            true,
        ))
    }

    fn palette_select_pixels(&mut self, c: Option<[u8; 3]>) -> Result<Outcome, String> {
        let palette = &self.colors.palette;
        let settings = &self.tools.settings;
        let target = c
            .map(rgb)
            .or_else(|| palette.focus())
            .or_else(|| palette.selected().last().copied());
        let target = target.unwrap_or_else(|| {
            tup(if settings.color_mode == "sub" {
                settings.color_sub
            } else {
                settings.color_main
            })
        });
        if target == BACKGROUND {
            return Ok(Outcome::message(
                "背景色は選択範囲にできません。使用色を選んでください。",
                false,
            ));
        }
        let p = self.project.as_mut().ok_or("作品を開いていません")?;
        let (_, layer) = crate::session::current_index(&p.document);
        let frames = p.document.frame_order().len();
        let want = rgb3(target);
        let mut found = None;
        for frame in 0..frames {
            let layers =
                pma_core::compose::frame_layers(&p.document, frame).map_err(|e| e.to_string())?;
            let Some(Some(image)) = layers.get(layer).map(|l| l.as_ref().and_then(|l| l.image))
            else {
                continue;
            };
            if image
                .rgba()
                .chunks_exact(4)
                .any(|px| px[3] > 0 && px[..3] == want)
            {
                found = Some(frame);
                break;
            }
        }
        let Some(frame) = found else {
            return Ok(Outcome::message(
                "現在のレイヤー内に、この色が使われているコマはありません。",
                false,
            ));
        };
        p.document
            .set_selection_index(frame, layer)
            .map_err(|e| e.to_string())?;
        self.timeline.select_current(&p.document);
        self.touch_structure();
        self.recompose()?;
        self.colors.palette.select_only(target);
        self.colors.palette.set_focus(Some(target));
        crate::tools::select_color(self, want)?;
        Ok(Outcome::message(
            format!("#{} を選択範囲にしました。", code(target)),
            false,
        ))
    }

    /// 選択レイヤーの色フィルタ（`Some(rgb)` で表示する色、`None` で解除）。
    fn layer_color_filter(&mut self, c: Option<Option<[u8; 3]>>) -> Result<Outcome, String> {
        use pma_core::document::commands::TrackProperty;
        let settings = &self.tools.settings;
        let color = match c {
            None => None,
            Some(Some(v)) => Some(v),
            Some(None) => {
                if settings.color_mode == "transparent" {
                    return Ok(Outcome::message(
                        "背景色では特定色表示を設定できません。メイン色またはサブ色を選択してください。",
                        false,
                    ));
                }
                Some(if settings.color_mode == "sub" {
                    settings.color_sub
                } else {
                    settings.color_main
                })
            }
        };
        let changed = self.edit_with(None, |doc, _| {
            let (_, layer) = crate::session::current_index(doc);
            let changes = doc
                .set_track_properties(
                    &[layer],
                    &[
                        TrackProperty::ColorFilterEnabled(color.is_some()),
                        TrackProperty::ColorFilterRgb(color),
                    ],
                    false,
                )
                .map_err(|e| e.to_string())?;
            let changed = !changes.is_empty();
            Ok((changed, changed))
        })?;
        let message = match color {
            Some(c) => format!(
                "選択レイヤーを #{} だけ表示しています。元画像は変更されません。",
                code(tup(c))
            ),
            None if changed => "選択レイヤーの特定色表示を解除しました。".into(),
            None => "選択レイヤーには特定色表示が設定されていません。".into(),
        };
        Ok(Outcome::message(message, changed))
    }

    /// 選択レイヤーの全コマの絵の色を置き換える（Qt の `apply_palette_replacements`）。
    /// 履歴の件には、操作前の使用色パネルの状態を添える（取り消しで並びやフォルダーも戻す）。
    pub fn palette_remap(
        &mut self,
        mapping: BTreeMap<Rgb, Rgb>,
        remap: Remap,
    ) -> Result<Outcome, String> {
        if mapping.is_empty() {
            let message = match remap {
                Remap::Replace => "置換色が登録されていません。",
                Remap::Delete => "削除する使用色が選択されていません。",
                Remap::Merge => "統合する使用色が選択されていません。",
            };
            return Ok(Outcome::message(message, false));
        }
        let mapping = pixels::effective_mapping(&mapping);
        if mapping.is_empty() {
            let message = match remap {
                Remap::Replace => "置換前と置換後が同じ色です。",
                Remap::Delete => "削除できる使用色が選択されていません。",
                Remap::Merge => "親以外の使用色を選択してください。",
            };
            return Ok(Outcome::message(message, false));
        }
        let palette_before = self.colors.palette.capture();
        let Some(p) = &self.project else {
            return Err("作品を開いていません".into());
        };
        let before = p.document.clone();
        let (_, layer) = crate::session::current_index(&p.document);
        // 画素の選択範囲があれば、その中だけを置き換える（Qt の `selection_mask_bool`。
        // 髪の赤は残して頬の赤だけ、など）。選択は tools.rs が持つ。
        let (w, h) = p.document.size();
        let mask = self
            .tools
            .selection
            .selection()
            .and_then(|sel| sel.clip_mask(w, h, (0.0, 0.0)))
            .map(|m| m.bits().to_vec());
        // 画像を変えるところは edit_with を通す（描いている途中は断り、失敗したら戻す）。
        // 履歴は使用色パネルの状態を添えて、ここで積む。
        let result = self.edit_with(None, |doc, _| {
            let result = pixels::remap_layer(doc, layer, &mapping, mask.as_deref())
                .map_err(|e| e.to_string())?;
            let changed = result.pixels > 0;
            Ok((result, changed))
        })?;
        if result.pixels == 0 {
            return Ok(Outcome::message(
                if mask.is_some() {
                    "選択範囲の中に、選択した使用色はありませんでした。"
                } else {
                    "選択した使用色は画像内にありませんでした。"
                },
                false,
            ));
        }
        if let Some(p) = &mut self.project {
            p.undo
                .record_with_palette(remap.label(), &before, Some(palette_before));
        }
        // 数え直しで古い色の行が消える前に、整理の情報を新しい色へ移す。
        self.colors.palette.remap_saved_color_metadata(&mapping);
        self.touch_pixels();
        self.recompose()?;
        Ok(Outcome::message(
            format!(
                "{}{}セル・{}ピクセルへ{}を適用しました。",
                if mask.is_some() {
                    "選択範囲の中で、"
                } else {
                    ""
                },
                result.cells,
                thousands(result.pixels),
                remap.label()
            ),
            true,
        ))
    }
}

// ---- カラーチャート -------------------------------------------------------

/// チャートの中の1色の位置（画面から）。
#[derive(Clone, Debug, Deserialize)]
pub struct HitArg {
    pub group_id: String,
    /// "parent" か "child"。
    pub role: String,
    #[serde(default)]
    pub index: usize,
}

impl HitArg {
    fn hit(&self) -> Hit {
        Hit {
            group_id: self.group_id.clone(),
            role: if self.role == "parent" {
                Role::Parent
            } else {
                Role::Child(self.index)
            },
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum ChartOp {
    /// 現在の親子付けを登録。
    Capture,
    /// チャートを使用色へ適用。
    Apply,
    Move {
        source: HitArg,
        target: Option<HitArg>,
    },
    AssignTag {
        hit: HitArg,
        tag: String,
    },
    Rename {
        group_id: String,
        name: String,
    },
    TagOrder {
        order: Vec<String>,
    },
    AddTag {
        name: String,
        color: String,
    },
    EditTag {
        old: String,
        new: String,
        color: String,
    },
    RemoveTag {
        name: String,
    },
}

/// チャートの1タイル（画面用）。
#[derive(Clone, Debug, Serialize)]
pub struct TileView {
    pub id: String,
    pub name: String,
    pub parent: Option<String>,
    pub parent_tag: String,
    pub placeholder: bool,
    /// タグの順に並べた子（`index` は元の番号）。
    pub children: Vec<ChildView>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ChildView {
    pub index: usize,
    pub color: String,
    pub tag: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct ChartView {
    pub summary: String,
    pub tiles: Vec<TileView>,
    /// タグの一覧（Base、tag_order、残りの順）と色。
    pub tags: Vec<(String, String)>,
}

impl ColorUi {
    pub fn chart_view(&self) -> ChartView {
        let chart = &self.chart;
        let tiles = chart
            .tiles
            .iter()
            .map(|tile| TileView {
                id: tile.id.clone(),
                name: tile.name.clone(),
                parent: tile.parent.clone(),
                parent_tag: if tile.placeholder {
                    "未設定".into()
                } else {
                    tile.parent_tag.clone()
                },
                placeholder: tile.placeholder,
                children: chart
                    .display_children(tile)
                    .into_iter()
                    .map(|(index, c)| ChildView {
                        index,
                        color: c.color,
                        tag: c.tag,
                    })
                    .collect(),
            })
            .collect();
        let mut order: Vec<String> = vec!["Base".into()];
        order.extend(chart.tag_order.iter().cloned());
        for name in &chart.tag_library {
            if !order.contains(name) {
                order.push(name.clone());
            }
        }
        let tags = order
            .into_iter()
            .filter(|n| chart.tag_library.contains(n))
            .map(|n| {
                let color = chart.tag_color(&n);
                (n, color)
            })
            .collect();
        ChartView {
            summary: chart.summary(),
            tiles,
            tags,
        }
    }

    /// チャートの編集（Qt の `ColorChartPanel` の操作と `ColorChartController`）。
    /// チャートは履歴に積まない（Qt と同じ）。
    pub fn chart_op(&mut self, op: ChartOp) -> Result<Outcome, String> {
        let changed = |next: Option<Chart>, chart: &mut Chart| match next {
            Some(next) => {
                *chart = next;
                Outcome::changed(true)
            }
            None => Outcome::default(),
        };
        Ok(match op {
            ChartOp::Capture => {
                let current = self.palette.chart_from_groups();
                if current.tiles.is_empty() {
                    return Err("親子付けされた使用色がありません。\n使用色を別の色の中央へドロップして親子を作成してください。".into());
                }
                let before = self.chart.group_count();
                self.chart = self.chart.merge(&current);
                let added = self.chart.group_count().saturating_sub(before);
                Outcome::message(
                    format!("現在の親子付けをカラーチャートへ登録しました（新規 {added}組）。"),
                    true,
                )
            }
            ChartOp::Apply => {
                if !self.palette.apply_chart(&self.chart) {
                    return Err("適用するチャートがありません。".into());
                }
                Outcome::message(
                    "現在の画像に存在する色へカラーチャートを適用しました。",
                    true,
                )
            }
            ChartOp::Move { source, target } => {
                let next = self
                    .chart
                    .move_color(&source.hit(), target.as_ref().map(HitArg::hit).as_ref());
                changed(next, &mut self.chart)
            }
            ChartOp::AssignTag { hit, tag } => {
                let next = self.chart.assign_tag(&hit.hit(), &tag);
                changed(next, &mut self.chart)
            }
            ChartOp::Rename { group_id, name } => {
                let next = self.chart.rename_group(&group_id, &name);
                changed(next, &mut self.chart)
            }
            ChartOp::TagOrder { order } => {
                self.chart = self.chart.set_tag_order(&order);
                Outcome::changed(true)
            }
            ChartOp::AddTag { name, color } => {
                let next = self.chart.add_tag(&name, &color);
                changed(next, &mut self.chart)
            }
            ChartOp::EditTag { old, new, color } => {
                let next = self.chart.edit_tag(&old, &new, &color)?;
                changed(next, &mut self.chart)
            }
            ChartOp::RemoveTag { name } => {
                let next = self.chart.remove_tag(&name);
                changed(next, &mut self.chart)
            }
        })
    }
}

// ---- コマンド -------------------------------------------------------------

/// 使用色パネルの全体。まだ数えていなければ数える。
#[tauri::command]
pub fn palette_view(app: tauri::AppHandle) -> PaletteView {
    // with_session の sync で数える（表示が変われば知らせる）。
    with_session(&app, "colors", |_| ());
    let state = app.state::<crate::session::Shared>();
    let view = state.lock().unwrap().palette_view();
    view
}

/// 使用色パネルの操作。変わったら `palette-changed`（と表示が変われば `display-changed`）で知らせる。
#[tauri::command]
pub fn palette_op(app: tauri::AppHandle, op: PaletteOp) -> Result<Outcome, String> {
    // 「この色を選択範囲にする」は画素の選択範囲（tools.rs）を変えるので、ツールにも知らせる。
    let selects = matches!(op, PaletteOp::SelectPixels { .. });
    let mut tools_view = None;
    let out = with_session(&app, "colors", |s| {
        // 文書を変えない操作でも、使用色パネルと表示を知らせる（with_session の sync）。
        s.colors.touched = true;
        let out = s.palette_op(op);
        if selects {
            tools_view = Some(s.tools.view());
        }
        out
    });
    if let Some(view) = tools_view {
        let _ = app.emit("tools-changed", view);
    }
    out
}

#[tauri::command]
pub fn chart_view(session: State<crate::session::Shared>) -> ChartView {
    session.lock().unwrap().colors.chart_view()
}

/// チャートの操作。変わったら `chart-changed` で知らせる。「適用」は使用色パネルも変える。
#[tauri::command]
pub fn chart_op(app: tauri::AppHandle, op: ChartOp) -> Result<Outcome, String> {
    with_session(&app, "colors", |s| {
        let applied = matches!(op, ChartOp::Apply);
        let out = s.colors.chart_op(op)?;
        if out.changed {
            let _ = app.emit("chart-changed", s.colors.chart_view());
        }
        s.colors.touched |= applied;
        Ok(out)
    })
}

/// ファイルを選ぶ窓（主スレッドで開く）。
fn pick_file(title: &str, filter: (&str, &str), save_name: Option<&str>) -> Option<PathBuf> {
    let dialog = rfd::FileDialog::new()
        .set_title(title)
        .add_filter(filter.0, &[filter.1]);
    match save_name {
        Some(name) => dialog.set_file_name(name).save_file(),
        None => dialog.pick_file(),
    }
}

fn file_name(path: &std::path::Path) -> String {
    path.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// 「PMAG保存」：チャートを .pmag へ書く。チャートが空なら先に親子付けを登録する。
#[tauri::command]
pub fn chart_save_pmag(app: tauri::AppHandle) -> Result<Outcome, String> {
    let state = app.state::<crate::session::Shared>();
    {
        let mut s = state.lock().unwrap();
        if s.colors.chart.tiles.is_empty() {
            s.colors.chart_op(ChartOp::Capture)?;
            let _ = app.emit("chart-changed", s.colors.chart_view());
        }
    }
    let Some(mut path) = pick_file(
        "カラーチャートを保存",
        ("Paint Mask Group", "pmag"),
        Some("color_chart.pmag"),
    ) else {
        return Ok(Outcome::default());
    };
    if path
        .extension()
        .is_none_or(|e| !e.eq_ignore_ascii_case("pmag"))
    {
        path.set_extension("pmag");
    }
    let text = state.lock().unwrap().colors.chart.to_pmag();
    std::fs::write(&path, text).map_err(|e| format!("保存できませんでした。\n\n{e}"))?;
    Ok(Outcome::message(
        format!("PMAGを保存しました：{}", file_name(&path)),
        false,
    ))
}

/// 「PMAG読込」。
#[tauri::command]
pub fn chart_load_pmag(app: tauri::AppHandle) -> Result<Outcome, String> {
    let Some(path) = pick_file(
        "カラーチャートを読み込み",
        ("Paint Mask Group", "pmag"),
        None,
    ) else {
        return Ok(Outcome::default());
    };
    let meta = std::fs::metadata(&path).map_err(|e| format!("読み込めませんでした。\n\n{e}"))?;
    if meta.len() > 16 * 1024 * 1024 {
        return Err("読み込めませんでした。\n\nPMAGファイルが大きすぎます。".into());
    }
    let text =
        std::fs::read_to_string(&path).map_err(|e| format!("読み込めませんでした。\n\n{e}"))?;
    let chart = chart::read_pmag(&text).map_err(|e| format!("読み込めませんでした。\n\n{e}"))?;
    let state = app.state::<crate::session::Shared>();
    let view = {
        let mut s = state.lock().unwrap();
        s.colors.chart = chart;
        s.colors.chart_view()
    };
    let _ = app.emit("chart-changed", view);
    Ok(Outcome::message(
        format!("PMAGを読み込みました：{}", file_name(&path)),
        true,
    ))
}

/// 「置換色を保存…」。
#[tauri::command]
pub fn replacement_set_save(app: tauri::AppHandle) -> Result<Outcome, String> {
    let state = app.state::<crate::session::Shared>();
    let mapping = state.lock().unwrap().colors.palette.current_replacements();
    if mapping.is_empty() {
        return Ok(Outcome::message(
            "保存できる置換色が登録されていません。",
            false,
        ));
    }
    let suffix = pixels::REPLACEMENT_SUFFIX.trim_start_matches('.');
    let Some(mut path) = pick_file(
        "置換色を保存",
        ("置換色", suffix),
        Some(&format!("replacements{}", pixels::REPLACEMENT_SUFFIX)),
    ) else {
        return Ok(Outcome::default());
    };
    if path.extension().is_none_or(|e| e != suffix) {
        path.set_extension(suffix);
    }
    let text = pixels::replacement_set_to_json(&mapping)
        .dumps(Some(2))
        .map_err(|e| format!("保存できませんでした。\n\n{e}"))?;
    std::fs::write(&path, text + "\n").map_err(|e| format!("保存できませんでした。\n\n{e}"))?;
    Ok(Outcome::message(
        format!("置換色を保存しました：{}", file_name(&path)),
        false,
    ))
}

/// 「置換色を読み込む…」。使用色にある組だけ登録する。
#[tauri::command]
pub fn replacement_set_load(app: tauri::AppHandle) -> Result<Outcome, String> {
    let suffix = pixels::REPLACEMENT_SUFFIX.trim_start_matches('.');
    let Some(path) = pick_file("置換色を読み込み", ("置換色", suffix), None) else {
        return Ok(Outcome::default());
    };
    let text =
        std::fs::read_to_string(&path).map_err(|e| format!("読み込めませんでした。\n\n{e}"))?;
    let parsed = pma_core::project::json::parse(&text)
        .map_err(|e| format!("読み込めませんでした。\n\n{}", e))?;
    let mapping = pixels::replacement_set_from_json(&parsed.value)
        .map_err(|e| format!("読み込めませんでした。\n\n{e}"))?;
    let name = file_name(&path);
    with_session(&app, "colors", |s| {
        let (registered, recorded) = s.colors.palette.register_replacements(&mapping);
        record(s, recorded);
        s.colors.touched = true;
        let message = if registered < mapping.len() {
            format!(
                "置換色 {}組のうち {registered}組を登録しました（残りはこの絵で使われていない色です）：{name}",
                mapping.len()
            )
        } else {
            format!("置換色 {registered}組を登録しました：{name}")
        };
        Ok(Outcome::message(message, registered > 0))
    })
}

#[cfg(test)]
mod tests;
