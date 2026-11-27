//! 使用色パネルの状態（Python・Qt に依存しない）。
//!
//! Qt 版の `color_panel.py`（`UsedColorPanel`）と、その mixin（`color_panel_grouping`・
//! `color_panel_selection`・`color_panel_visibility`・`color_panel_mask`・
//! `color_panel_history`）が持っていた状態と判断を、画面の部品から切り離して持つ。
//! 画面はこの状態を読んで行を描き、操作をここへ渡すだけにする。
//!
//! 0.8.0 からの変更（#104）：
//! - **フォーカス**（枠で囲む行）と**選択**（右のチェック＝編集範囲）を分ける。スポイトと
//!   行のクリックはフォーカスだけを動かす。
//! - 鍵は「描ける色の一覧」（`mask_rgbs`）ではなく「守る色の一覧」（[`UsedColors::locked`]）。
//!   描ける範囲＝（選択した色。選択がなければ全色）−（鍵のかかった色）。鍵が常に優先する
//!   （[`EditRange`]）。背景（透明・白）も選択できる。
//!
//! 取り消し：編集の前後の [`Snapshot`] を比べ、意味のある差（並び・親子・表示・鍵・フォルダー・
//! 置換色）があれば [`Recorded`]（変える前の状態と、履歴の名前）を返す。Qt の
//! `_history_edit` と同じ判断。選択・フォーカス・折りたたみだけの変化は履歴に積まない。

mod categories;
pub mod chart;
mod grouping;
pub mod pixels;
mod toggles;

use std::collections::{BTreeMap, BTreeSet};

pub use crate::display::Rgb;
pub use categories::{parse_hex, Row};

/// 背景色（透明と白）。行の先頭に固定し、並べ替え・親子付けの対象にしない。
pub const BACKGROUND: Rgb = (255, 255, 255);

/// 使用色の一覧に出す色の上限（Qt と同じ）。これを超えたら「100色以上」。
pub const MAX_COLORS: usize = 100;

/// `#RRGGBB`（大文字）。
pub fn hex(rgb: Rgb) -> String {
    format!("#{:02X}{:02X}{:02X}", rgb.0, rgb.1, rgb.2)
}

/// `RRGGBB`（大文字、# なし）。
pub fn code(rgb: Rgb) -> String {
    format!("{:02X}{:02X}{:02X}", rgb.0, rgb.1, rgb.2)
}

/// 行の中ほどへ落としたか、上・下の端へ落としたか（`ColorSelectionArea._mode_for_pos`）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DropMode {
    Before,
    Child,
    After,
}

impl DropMode {
    /// 行の高さに対する落とした位置（0..1）から決める。
    pub fn from_ratio(ratio: f64) -> Self {
        let ratio = ratio.clamp(0.0, 1.0);
        if ratio < 0.28 {
            Self::Before
        } else if ratio > 0.72 {
            Self::After
        } else {
            Self::Child
        }
    }
}

/// なぞって一括で切り替える列。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Column {
    /// 目（表示）。
    Visibility,
    /// 鍵（保護）。
    Lock,
    /// 選択（編集範囲）。
    Selection,
}

/// 行をクリックしたときの修飾キー。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Modifiers {
    pub shift: bool,
    pub ctrl: bool,
}

/// 取り消しに使う状態の写し（Qt の `capture_history_state`）。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Snapshot {
    pub order: Vec<Rgb>,
    pub groups: BTreeMap<Rgb, Rgb>,
    pub enabled: BTreeMap<Rgb, bool>,
    pub locked: BTreeSet<Rgb>,
    pub selected: Vec<Rgb>,
    pub focus: Option<Rgb>,
    pub category_order: Vec<String>,
    pub category_colors: BTreeMap<Rgb, String>,
    pub category_collapsed: BTreeMap<String, bool>,
    pub replacements: BTreeMap<Rgb, Rgb>,
}

impl Snapshot {
    /// 履歴に積むほどの差があるか（`_history_significant`）。選択・フォーカス・折りたたみは見ない。
    pub fn significantly_differs(&self, other: &Snapshot) -> bool {
        self.order != other.order
            || self.groups != other.groups
            || self.enabled != other.enabled
            || self.locked != other.locked
            || self.category_order != other.category_order
            || self.category_colors != other.category_colors
            || self.replacements != other.replacements
    }
}

/// 履歴に積む1件：変える前の状態と、ヒストリーパネルに出す名前。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Recorded {
    pub label: &'static str,
    pub before: Snapshot,
}

/// 描ける範囲（#104）。`None` の `allowed` は「選択なし＝全色」。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct EditRange {
    /// 選択した色（背景を含みうる）。None なら全色。
    pub allowed: Option<BTreeSet<Rgb>>,
    /// 鍵のかかった色。選択より優先する。
    pub locked: BTreeSet<Rgb>,
}

impl EditRange {
    /// 何も絞らないか（全色に描ける）。
    pub fn is_unrestricted(&self) -> bool {
        self.allowed.is_none() && self.locked.is_empty()
    }

    /// その色の上に描けるか。`rgb` は画素の色を [`pixels::pixel_key`] で背景にまとめたもの。
    pub fn permits(&self, rgb: Rgb) -> bool {
        !self.locked.contains(&rgb) && self.allowed.as_ref().is_none_or(|set| set.contains(&rgb))
    }
}

/// なぞりの途中の状態。
#[derive(Clone, Debug)]
struct Sweep {
    column: Column,
    state: bool,
    touched: BTreeSet<Rgb>,
    changed: bool,
    before: Snapshot,
}

/// 使用色パネルの状態。
#[derive(Clone, Debug)]
pub struct UsedColors {
    /// 並び。先頭は必ず背景。
    colors: Vec<Rgb>,
    /// 100色を超えたか。
    pub exceeded: bool,
    enabled: BTreeMap<Rgb, bool>,
    locked: BTreeSet<Rgb>,
    selected: Vec<Rgb>,
    focus: Option<Rgb>,
    anchor: Option<Rgb>,
    /// {子: 親（ルート）}。親自身は含めない。色の見た目は変えない（整理専用）。
    child_to_parent: BTreeMap<Rgb, Rgb>,
    /// カラーチャートの役割（再登録・PMAG 用）。一覧には描かない。
    pub child_tags: BTreeMap<Rgb, String>,
    pub parent_tags: BTreeMap<Rgb, String>,
    pub tag_library: Vec<String>,
    pub tag_colors: Vec<(String, String)>,
    pub tag_order: Vec<String>,
    category_order: Vec<String>,
    category_colors: BTreeMap<Rgb, String>,
    category_collapsed: BTreeMap<String, bool>,
    replacements: BTreeMap<Rgb, Rgb>,
    isolated: Option<Rgb>,
    pre_isolate: Option<BTreeMap<Rgb, bool>>,
    sweep: Option<Sweep>,
    /// 置き換えモード（色ごとに置き換え後の色を決める）。
    pub replace_mode: bool,
    /// 置き換えた見た目をキャンバスで見せるか（置き換えモードのときだけ効く）。
    pub replacement_preview: bool,
    /// 取り消しで戻す状態。次に使用色をそろえたあと（[`UsedColors::set_colors`]）に戻す。
    pending_restore: Option<Snapshot>,
}

impl Default for UsedColors {
    fn default() -> Self {
        let mut enabled = BTreeMap::new();
        enabled.insert(BACKGROUND, true);
        Self {
            colors: vec![BACKGROUND],
            exceeded: false,
            enabled,
            locked: BTreeSet::new(),
            selected: Vec::new(),
            focus: None,
            anchor: None,
            child_to_parent: BTreeMap::new(),
            child_tags: BTreeMap::new(),
            parent_tags: BTreeMap::new(),
            tag_library: vec!["Base".into()],
            tag_colors: vec![("Base".into(), "#FFFFFF".into())],
            tag_order: Vec::new(),
            category_order: Vec::new(),
            category_colors: BTreeMap::new(),
            category_collapsed: BTreeMap::new(),
            replacements: BTreeMap::new(),
            isolated: None,
            pre_isolate: None,
            sweep: None,
            replace_mode: false,
            replacement_preview: true,
            pending_restore: None,
        }
    }
}

impl UsedColors {
    pub fn new() -> Self {
        Self::default()
    }

    // ---- 読む ---------------------------------------------------------------

    /// 並び（先頭は背景）。
    pub fn colors(&self) -> &[Rgb] {
        &self.colors
    }

    pub fn contains(&self, rgb: Rgb) -> bool {
        self.colors.contains(&rgb)
    }

    pub fn is_enabled(&self, rgb: Rgb) -> bool {
        self.enabled.get(&rgb).copied().unwrap_or(true)
    }

    pub fn is_locked(&self, rgb: Rgb) -> bool {
        self.locked.contains(&rgb)
    }

    pub fn is_selected(&self, rgb: Rgb) -> bool {
        self.selected.contains(&rgb)
    }

    /// 選択した色（選んだ順）。
    pub fn selected(&self) -> &[Rgb] {
        &self.selected
    }

    /// フォーカス（枠で囲む行）。統合先・「この色を選択範囲にする」の対象。
    pub fn focus(&self) -> Option<Rgb> {
        self.focus
    }

    pub fn parent_of(&self, rgb: Rgb) -> Option<Rgb> {
        self.child_to_parent.get(&rgb).copied()
    }

    pub fn groups(&self) -> &BTreeMap<Rgb, Rgb> {
        &self.child_to_parent
    }

    pub fn child_count(&self, rgb: Rgb) -> usize {
        self.child_to_parent.values().filter(|&&p| p == rgb).count()
    }

    pub fn replacement(&self, rgb: Rgb) -> Option<Rgb> {
        self.replacements.get(&rgb).copied()
    }

    pub fn categories(&self) -> &[String] {
        &self.category_order
    }

    pub fn category_of(&self, rgb: Rgb) -> Option<&str> {
        self.category_colors.get(&rgb).map(String::as_str)
    }

    pub fn is_collapsed(&self, name: &str) -> bool {
        self.category_collapsed.get(name).copied().unwrap_or(false)
    }

    /// 表示中の色（`enabled_rgb_set`）。
    pub fn enabled_set(&self) -> BTreeSet<Rgb> {
        self.enabled
            .iter()
            .filter(|(_, &on)| on)
            .map(|(&rgb, _)| rgb)
            .collect()
    }

    /// キャンバスに掛ける使用色の絞り込み。全色を表示しているなら None（絞り込まない。
    /// Qt の `_apply_pending_visible_colors`）。
    pub fn visible_filter(&self) -> Option<Vec<Rgb>> {
        let enabled = self.enabled_set();
        if self.colors.iter().all(|rgb| enabled.contains(rgb)) {
            return None;
        }
        Some(enabled.into_iter().collect())
    }

    /// いま登録している置換色（`current_replacements`）。
    pub fn current_replacements(&self) -> BTreeMap<Rgb, Rgb> {
        self.replacements
            .iter()
            .filter(|(&source, _)| source != BACKGROUND)
            .map(|(&s, &d)| (s, d))
            .collect()
    }

    /// キャンバスで見せる置換（`replacement_preview_mapping`）。OFF なら空。
    pub fn replacement_preview_mapping(&self) -> Vec<(Rgb, Rgb)> {
        if !self.replace_mode || !self.replacement_preview {
            return Vec::new();
        }
        self.current_replacements()
            .into_iter()
            .filter(|(source, target)| self.contains(*source) && source != target)
            .collect()
    }

    /// 「全コマを置き換え」で使う置換（`_emit_replacements`）。
    pub fn replacements_to_apply(&self) -> BTreeMap<Rgb, Rgb> {
        self.current_replacements()
            .into_iter()
            .filter(|(source, _)| self.contains(*source))
            .collect()
    }

    /// 描ける範囲（#104）。
    pub fn edit_range(&self) -> EditRange {
        EditRange {
            allowed: (!self.selected.is_empty()).then(|| self.selected.iter().copied().collect()),
            locked: self.locked.clone(),
        }
    }

    /// 背景以外の色の並び（`_ordered_non_background_rgbs`）。
    pub fn ordered_non_background(&self) -> Vec<Rgb> {
        self.colors
            .iter()
            .copied()
            .filter(|&rgb| rgb != BACKGROUND)
            .collect()
    }

    /// 統合の対象（統合先＝フォーカス、統合元＝選択した色のうちフォーカス以外）。#104。
    pub fn merge_plan(&self) -> Option<(Rgb, Vec<Rgb>)> {
        let target = self.focus.filter(|&rgb| rgb != BACKGROUND)?;
        let sources: Vec<Rgb> = self
            .selected
            .iter()
            .copied()
            .filter(|&rgb| rgb != BACKGROUND && rgb != target)
            .collect();
        (!sources.is_empty()).then_some((target, sources))
    }

    // ---- 取り消し -------------------------------------------------------------

    /// 取り消しで戻す状態の写し。
    pub fn capture(&self) -> Snapshot {
        Snapshot {
            order: self.colors.clone(),
            groups: self.child_to_parent.clone(),
            enabled: self.enabled.clone(),
            locked: self.locked.clone(),
            selected: self.selected.clone(),
            focus: self.focus,
            category_order: self.category_order.clone(),
            category_colors: self.category_colors.clone(),
            category_collapsed: self.category_collapsed.clone(),
            replacements: self.replacements.clone(),
        }
    }

    /// `f` で変え、意味のある差があれば履歴の1件を返す（Qt の `_history_edit`）。
    pub(crate) fn edit<T>(
        &mut self,
        label: &'static str,
        f: impl FnOnce(&mut Self) -> T,
    ) -> (T, Option<Recorded>) {
        let before = self.capture();
        let out = f(self);
        let recorded = self.record(label, before);
        (out, recorded)
    }

    fn record(&self, label: &'static str, before: Snapshot) -> Option<Recorded> {
        self.capture()
            .significantly_differs(&before)
            .then_some(Recorded { label, before })
    }

    /// 次に [`UsedColors::set_colors`] で色をそろえたとき、`snapshot` へ戻す
    /// （`restore_history_state_after_refresh`）。統合を取り消すと、画像は戻っても
    /// 消えた色の行はまだ無いので、数え直してから並び・親子・フォルダーを戻す。
    pub fn restore_after_refresh(&mut self, snapshot: Snapshot) {
        self.pending_restore = Some(snapshot);
    }

    /// 戻すのを待っている状態があるか。
    pub fn has_pending_restore(&self) -> bool {
        self.pending_restore.is_some()
    }

    /// 写しへ戻す（`restore_history_state`）。いまある色だけを戻し、無い色は捨てる。
    pub fn restore(&mut self, snapshot: &Snapshot) {
        let existing: BTreeSet<Rgb> = self.colors.iter().copied().collect();
        let mut ordered = Vec::with_capacity(self.colors.len());
        if existing.contains(&BACKGROUND) {
            ordered.push(BACKGROUND);
        }
        for &rgb in &snapshot.order {
            if existing.contains(&rgb) && !ordered.contains(&rgb) {
                ordered.push(rgb);
            }
        }
        for &rgb in &self.colors {
            if !ordered.contains(&rgb) {
                ordered.push(rgb);
            }
        }
        self.colors = ordered;

        // フォルダーは、色が一時的に別レイヤーで無くても割り当てを残す。
        self.category_order = snapshot
            .category_order
            .iter()
            .filter(|name| !name.trim().is_empty())
            .cloned()
            .collect();
        self.category_colors = snapshot
            .category_colors
            .iter()
            .filter(|(_, category)| self.category_order.contains(category))
            .map(|(&rgb, c)| (rgb, c.clone()))
            .collect();
        self.category_collapsed = self
            .category_order
            .iter()
            .map(|name| {
                let collapsed = snapshot
                    .category_collapsed
                    .get(name)
                    .copied()
                    .unwrap_or(false);
                (name.clone(), collapsed)
            })
            .collect();

        self.child_to_parent = snapshot
            .groups
            .iter()
            .filter(|(c, p)| existing.contains(c) && existing.contains(p))
            .map(|(&c, &p)| (c, p))
            .collect();
        self.normalize_groups();
        self.reorder_children_under_parents();

        self.isolated = None;
        self.pre_isolate = None;
        for &rgb in &self.colors {
            let on = snapshot.enabled.get(&rgb).copied().unwrap_or(true);
            self.enabled.insert(rgb, on);
        }

        self.locked = snapshot
            .locked
            .iter()
            .copied()
            .filter(|rgb| existing.contains(rgb) || *rgb == BACKGROUND)
            .collect();

        self.replacements = snapshot
            .replacements
            .iter()
            .filter(|(&rgb, _)| rgb != BACKGROUND && existing.contains(&rgb))
            .map(|(&s, &d)| (s, d))
            .collect();

        self.selected = snapshot
            .selected
            .iter()
            .copied()
            .filter(|rgb| existing.contains(rgb))
            .collect();
        self.focus = snapshot
            .focus
            .filter(|rgb| existing.contains(rgb) && *rgb != BACKGROUND);
        self.sweep = None;
    }

    // ---- 使用色をそろえる --------------------------------------------------------

    /// 数え直した使用色で行をそろえる（`set_colors`）。今の並びは保ち、新しい色だけを末尾へ足す。
    /// 消えた色の表示・鍵・親子・置換色は捨てる（フォルダーの割り当ては残す）。
    /// 戻すのを待っている状態があれば、そろえたあとで戻す。
    pub fn set_colors(&mut self, colors: &[Rgb], exceeded: bool) {
        let mut incoming: Vec<Rgb> = Vec::new();
        for &rgb in colors {
            if rgb != BACKGROUND && !incoming.contains(&rgb) {
                incoming.push(rgb);
            }
        }
        let mut keys: BTreeSet<Rgb> = incoming.iter().copied().collect();
        keys.insert(BACKGROUND);

        self.replacements
            .retain(|rgb, _| keys.contains(rgb) && *rgb != BACKGROUND);
        let removed: Vec<Rgb> = self
            .colors
            .iter()
            .copied()
            .filter(|rgb| !keys.contains(rgb))
            .collect();
        for rgb in &removed {
            self.enabled.remove(rgb);
            self.locked.remove(rgb);
            self.drop_group_links_for(*rgb);
        }
        self.selected.retain(|rgb| keys.contains(rgb));
        if self.focus.is_some_and(|rgb| !keys.contains(&rgb)) {
            self.focus = None;
        }
        if self.anchor.is_some_and(|rgb| !keys.contains(&rgb)) {
            self.anchor = None;
        }

        let mut next = vec![BACKGROUND];
        next.extend(
            self.colors
                .iter()
                .copied()
                .filter(|rgb| keys.contains(rgb) && *rgb != BACKGROUND),
        );
        for rgb in incoming {
            if !next.contains(&rgb) {
                next.push(rgb);
                self.enabled.insert(rgb, true);
            }
        }
        self.colors = next;
        self.enabled = keys
            .iter()
            .map(|&rgb| (rgb, self.enabled.get(&rgb).copied().unwrap_or(true)))
            .collect();

        self.normalize_groups();
        self.reorder_children_under_parents();
        if self.isolated.is_some_and(|rgb| !keys.contains(&rgb)) {
            self.isolated = None;
            self.pre_isolate = None;
        }
        self.exceeded = exceeded;
        if let Some(snapshot) = self.pending_restore.take() {
            self.restore(&snapshot);
        }
    }

    /// 色数の表示（「{n}色」か「100色以上」）。
    pub fn count_text(&self) -> String {
        if self.exceeded {
            "100色以上".into()
        } else {
            format!("{}色", self.colors.len())
        }
    }

    /// 色置換のあとも、親子・タグ・フォルダー・表示・鍵・選択を新しい色へ移す
    /// （`remap_saved_color_metadata`）。次に数え直したとき、古い色の行が消えても失わない。
    pub fn remap_saved_color_metadata(&mut self, mapping: &BTreeMap<Rgb, Rgb>) {
        let remap = |rgb: Rgb| mapping.get(&rgb).copied().unwrap_or(rgb);
        let mut groups = BTreeMap::new();
        for (&child, &parent) in &self.child_to_parent {
            let (c, p) = (remap(child), remap(parent));
            if c != BACKGROUND && p != BACKGROUND && c != p {
                groups.insert(c, p);
            }
        }
        self.child_to_parent = groups;
        let remap_tags = |tags: &BTreeMap<Rgb, String>| {
            tags.iter()
                .filter(|(&rgb, _)| remap(rgb) != BACKGROUND)
                .map(|(&rgb, tag)| (remap(rgb), tag.clone()))
                .collect::<BTreeMap<_, _>>()
        };
        self.child_tags = remap_tags(&self.child_tags);
        self.parent_tags = remap_tags(&self.parent_tags);
        self.category_colors = remap_tags(&self.category_colors);
        self.enabled = self
            .enabled
            .iter()
            .map(|(&rgb, &on)| (remap(rgb), on))
            .collect();
        self.enabled.insert(BACKGROUND, true);
        self.locked = self.locked.iter().map(|&rgb| remap(rgb)).collect();
        let mut selected = Vec::new();
        for &rgb in &self.selected {
            let rgb = remap(rgb);
            if !selected.contains(&rgb) {
                selected.push(rgb);
            }
        }
        self.selected = selected;
        self.focus = self.focus.map(remap).filter(|&rgb| rgb != BACKGROUND);
    }

    /// 統合・削除のあとの選択（統合で消えた色は次に数え直したときに外れる）。
    /// 削除のあとは選択を外す（Qt の `apply_palette_delete`）。
    pub fn clear_selection_after_delete(&mut self) {
        self.selected.clear();
        self.focus = None;
        self.anchor = None;
    }
}

#[cfg(test)]
mod tests;
