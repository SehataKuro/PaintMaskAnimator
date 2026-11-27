//! 行のクリック・選択・表示・鍵・置換色（Qt の `color_panel_selection`・
//! `color_panel_visibility`・`color_panel_mask` と `color_panel.py` の置換色）。
//!
//! #104 で、行のクリックとスポイトは**フォーカス**だけを動かし、右のチェック（**選択**）は
//! 編集範囲になった。Shift／Ctrl のクリックは、0.8.0 と同じく範囲・追加の選択に使う。

use std::collections::{BTreeMap, BTreeSet};

use super::{Column, Modifiers, Recorded, Rgb, Sweep, UsedColors, BACKGROUND};

impl UsedColors {
    // ---- フォーカス --------------------------------------------------------------

    /// 行をクリックした（`_select_used_color`）。修飾なしはフォーカスだけを動かす。
    /// Shift は直前にクリックした色からの範囲を選択し、Ctrl はその色の選択を切り替える。
    /// 背景の行はクリックしても何もしない（背景の選択は右のチェックで行う）。
    pub fn click_row(&mut self, rgb: Rgb, modifiers: Modifiers) {
        if rgb == BACKGROUND || !self.contains(rgb) {
            return;
        }
        if modifiers.shift {
            if let Some(anchor) = self.anchor {
                let ordered = self.ordered_non_background();
                if let (Some(start), Some(end)) = (
                    ordered.iter().position(|&c| c == anchor),
                    ordered.iter().position(|&c| c == rgb),
                ) {
                    let (lo, hi) = (start.min(end), start.max(end));
                    self.selected = ordered[lo..=hi].to_vec();
                    self.focus = Some(rgb);
                    return;
                }
            }
        }
        self.anchor = Some(rgb);
        self.focus = Some(rgb);
        if modifiers.ctrl {
            self.toggle_selection(rgb);
        }
    }

    /// スポイトで拾った色の行へフォーカスを移す（`select_matching_color`）。選択には触れない
    /// （#104）。使用色に無い色や背景ならフォーカスを外し、false を返す。
    pub fn focus_matching(&mut self, rgb: Rgb) -> bool {
        if rgb == BACKGROUND || !self.contains(rgb) {
            self.focus = None;
            return false;
        }
        self.focus = Some(rgb);
        self.anchor = Some(rgb);
        true
    }

    /// フォーカスを移す（「この色を選択範囲にする」など）。
    pub fn set_focus(&mut self, rgb: Option<Rgb>) {
        self.focus = rgb.filter(|&c| c != BACKGROUND && self.contains(c));
    }

    // ---- 選択（編集範囲） --------------------------------------------------------

    fn apply_selection_state(&mut self, rgb: Rgb, selected: bool) -> bool {
        if !self.contains(rgb) {
            return false;
        }
        let was = self.selected.contains(&rgb);
        if selected == was {
            return false;
        }
        if selected {
            self.selected.push(rgb);
        } else {
            self.selected.retain(|&c| c != rgb);
        }
        self.anchor = Some(rgb);
        true
    }

    /// 選択を1色ぶん切り替える（`_toggle_used_color_selection`）。
    pub fn toggle_selection(&mut self, rgb: Rgb) {
        let on = !self.selected.contains(&rgb);
        self.apply_selection_state(rgb, on);
    }

    /// 選択のチェックを付ける・外す（`_on_selection_check_toggled`）。
    pub fn set_selected(&mut self, rgb: Rgb, selected: bool) {
        self.apply_selection_state(rgb, selected);
    }

    /// その色だけを選択する（選択の Alt＋クリック、`_select_single_used_color`）。
    pub fn select_only(&mut self, rgb: Rgb) {
        if !self.contains(rgb) {
            return;
        }
        self.selected = vec![rgb];
        self.anchor = Some(rgb);
    }

    /// 選択をすべて外す（`_clear_used_color_selection`）。フォーカスも外す。
    pub fn clear_selection(&mut self) {
        self.selected.clear();
        self.focus = None;
        self.anchor = None;
    }

    // ---- なぞり（3つの列で共通） -------------------------------------------------

    /// 列のチェックを押した。押した行を `checked` にし、なぞりを始める。1行だけのクリックも
    /// なぞりとして扱う（Qt と同じ。表示と鍵は「一括切り替え」の名前で履歴に積む）。
    pub fn sweep_begin(&mut self, column: Column, rgb: Rgb, checked: bool) {
        let before = self.capture();
        if column == Column::Visibility {
            self.isolated = None;
            self.pre_isolate = None;
        }
        let changed = self.sweep_apply(column, rgb, checked);
        self.sweep = Some(Sweep {
            column,
            state: checked,
            touched: [rgb].into(),
            changed,
            before,
        });
    }

    /// なぞって別の行に入った。
    pub fn sweep_move(&mut self, rgb: Rgb) {
        let Some(mut sweep) = self.sweep.take() else {
            return;
        };
        if sweep.touched.insert(rgb) {
            sweep.changed |= self.sweep_apply(sweep.column, rgb, sweep.state);
        }
        self.sweep = Some(sweep);
    }

    /// なぞり終えた。変えた行があれば履歴の1件を返す。
    pub fn sweep_end(&mut self) -> Option<Recorded> {
        let sweep = self.sweep.take()?;
        if !sweep.changed {
            return None;
        }
        let label = match sweep.column {
            Column::Visibility => "表示の一括切り替え",
            Column::Lock => "マスクの一括切り替え",
            Column::Selection => return None,
        };
        self.record(label, sweep.before)
    }

    fn sweep_apply(&mut self, column: Column, rgb: Rgb, state: bool) -> bool {
        if !self.contains(rgb) {
            return false;
        }
        match column {
            Column::Visibility => {
                let old = self.is_enabled(rgb);
                self.enabled.insert(rgb, state);
                old != state
            }
            // 鍵の列は「鍵をかける」が true。
            Column::Lock => {
                if state {
                    self.locked.insert(rgb)
                } else {
                    self.locked.remove(&rgb)
                }
            }
            Column::Selection => self.apply_selection_state(rgb, state),
        }
    }

    // ---- 表示 ---------------------------------------------------------------

    /// 表示メニューの ON／OFF（`_set_visibility_state`）。
    pub fn set_visible(&mut self, rgb: Rgb, visible: bool) -> Option<Recorded> {
        self.edit("表示の切り替え", |s| {
            s.isolated = None;
            s.pre_isolate = None;
            if s.contains(rgb) {
                s.enabled.insert(rgb, visible);
            }
        })
        .1
    }

    /// 「全表示」（`_show_all_colors`）。
    pub fn show_all(&mut self) -> Option<Recorded> {
        self.edit("全表示", |s| {
            s.isolated = None;
            s.pre_isolate = None;
            for rgb in s.colors.clone() {
                s.enabled.insert(rgb, true);
            }
        })
        .1
    }

    /// 「対象以外をOFF」（`_disable_other_visible_colors`）。
    pub fn show_only(&mut self, rgb: Rgb) -> Option<Recorded> {
        self.edit("対象以外を非表示", |s| {
            s.isolated = None;
            s.pre_isolate = None;
            for c in s.colors.clone() {
                s.enabled.insert(c, c == rgb);
            }
        })
        .1
    }

    /// 目の Alt＋クリック：この色だけ表示。もう一度で元に戻す（`_isolate_visible_color`）。
    pub fn isolate_visible(&mut self, rgb: Rgb) -> Option<Recorded> {
        self.edit("この色だけ表示", |s| {
            if s.isolated == Some(rgb) {
                if let Some(restore) = s.pre_isolate.take() {
                    s.isolated = None;
                    for c in s.colors.clone() {
                        s.enabled
                            .insert(c, restore.get(&c).copied().unwrap_or(true));
                    }
                    return;
                }
            }
            s.pre_isolate = Some(s.enabled.clone());
            s.isolated = Some(rgb);
            for c in s.colors.clone() {
                s.enabled.insert(c, c == rgb);
            }
        })
        .1
    }

    /// フォルダーの目（`_set_category_visibility`）。
    pub fn set_category_visible(&mut self, name: &str, visible: bool) -> Option<Recorded> {
        let members = self.category_members(name);
        if members.is_empty() {
            return None;
        }
        self.edit("使用色フォルダーの表示切り替え", |s| {
            s.isolated = None;
            s.pre_isolate = None;
            for rgb in members {
                s.enabled.insert(rgb, visible);
            }
        })
        .1
    }

    // ---- 鍵（保護） -------------------------------------------------------------

    /// 鍵メニューの ON／OFF（`_set_single_mask_state`）。`locked` が true で鍵をかける。
    pub fn set_locked(&mut self, rgb: Rgb, locked: bool) -> Option<Recorded> {
        self.edit("マスクの切り替え", |s| {
            if !s.contains(rgb) {
                return;
            }
            if locked {
                s.locked.insert(rgb);
            } else {
                s.locked.remove(&rgb);
            }
        })
        .1
    }

    /// 「すべての色の保護を外す」（`_set_all_masks_on`）。
    pub fn unlock_all(&mut self) -> Option<Recorded> {
        self.edit("マスクを全体ON", |s| s.locked.clear()).1
    }

    // ---- 置換色 -------------------------------------------------------------

    /// 置き換えの対象（`_replace_targets`）：選択した色のうち背景以外。
    pub fn replace_targets(&self) -> Vec<Rgb> {
        self.selected
            .iter()
            .copied()
            .filter(|&rgb| rgb != BACKGROUND && self.contains(rgb))
            .collect()
    }

    /// 置換色を決める（Undo の名前は「置換色を設定」）。`targets` が空なら選択した色。
    pub fn set_replacements(&mut self, targets: &[Rgb], color: Rgb) -> Option<Recorded> {
        let targets: Vec<Rgb> = if targets.is_empty() {
            self.replace_targets()
        } else {
            targets.to_vec()
        };
        self.edit("置換色を設定", |s| {
            for rgb in targets {
                s.set_replacement_raw(rgb, color);
            }
        })
        .1
    }

    /// 置換色を履歴に積まずに変える（カラーピッカーを動かしているあいだ）。閉じたときに
    /// 開く前の写しと比べて1件にする（`_finish_replacement_edit`）。
    pub fn set_replacement_raw(&mut self, rgb: Rgb, color: Rgb) {
        if rgb != BACKGROUND && self.contains(rgb) {
            self.replacements.insert(rgb, color);
        }
    }

    /// 写しと比べて履歴の1件にする（`_record_history`）。
    pub fn record_since(&self, label: &'static str, before: super::Snapshot) -> Option<Recorded> {
        self.record(label, before)
    }

    /// 置換色を外す。`targets` が空なら選択した色（「解除」）。
    pub fn clear_replacements(&mut self, targets: &[Rgb]) -> Option<Recorded> {
        let targets: Vec<Rgb> = if targets.is_empty() {
            self.replace_targets()
        } else {
            targets.to_vec()
        };
        self.edit("置換色を外す", |s| {
            for rgb in targets {
                s.replacements.remove(&rgb);
            }
        })
        .1
    }

    /// 「置換色をすべて外す」。外した色の数も返す。
    pub fn clear_all_replacements(&mut self) -> (usize, Option<Recorded>) {
        let count = self.current_replacements().len();
        if count == 0 {
            return (0, None);
        }
        let (_, recorded) = self.edit("置換色をすべて外す", |s| s.replacements.clear());
        (count, recorded)
    }

    /// 置換色のセットを読み込む（`register_replacements`）。使用色にある組だけ登録し、その数を返す。
    pub fn register_replacements(
        &mut self,
        mapping: &BTreeMap<Rgb, Rgb>,
    ) -> (usize, Option<Recorded>) {
        self.edit("置換色を読み込む", |s| {
            let mut registered = 0;
            for (&source, &target) in mapping {
                if source == BACKGROUND || !s.contains(source) {
                    continue;
                }
                s.replacements.insert(source, target);
                registered += 1;
            }
            registered
        })
    }

    /// 削除の対象（右クリックした色が選択中なら選択した色すべて、そうでなければその色だけ）。
    pub fn delete_sources(&self, clicked: Rgb) -> BTreeSet<Rgb> {
        let selected: BTreeSet<Rgb> = self
            .selected
            .iter()
            .copied()
            .filter(|&rgb| rgb != BACKGROUND)
            .collect();
        let mut sources = if selected.contains(&clicked) {
            selected
        } else {
            [clicked].into()
        };
        sources.remove(&BACKGROUND);
        sources
    }
}
