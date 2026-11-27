//! 親子付けと並べ替え（Qt の `color_panel_grouping.py`）。
//!
//! 親子は整理のためだけに使い、キャンバスの色は変えない。子は常にルートの親へ直に付け
//! （一段だけ）、並びでは親のすぐ後ろへまとめる。

use std::collections::BTreeSet;

use super::{DropMode, Recorded, Rgb, UsedColors, BACKGROUND};

impl UsedColors {
    fn color_index(&self, rgb: Rgb) -> Option<usize> {
        self.colors.iter().position(|&c| c == rgb)
    }

    /// 親子をたどった最後の親（`_group_root`）。
    pub fn group_root(&self, rgb: Rgb) -> Rgb {
        let mut seen = BTreeSet::new();
        let mut current = rgb;
        while let Some(&parent) = self.child_to_parent.get(&current) {
            if !seen.insert(current) {
                break;
            }
            current = parent;
        }
        current
    }

    /// 親子を束ねる縦の線の色（`_group_line_color`）。親子に属さなければ None。
    pub fn group_line_color(&self, rgb: Rgb) -> Option<Rgb> {
        if self.child_to_parent.contains_key(&rgb) {
            return Some(self.group_root(rgb));
        }
        self.child_to_parent
            .values()
            .any(|&p| p == rgb)
            .then_some(rgb)
    }

    /// その色が親でも子でも、関わる親子をすべて外す（`_drop_group_links_for`）。
    pub(crate) fn drop_group_links_for(&mut self, rgb: Rgb) -> bool {
        let mut changed = self.child_to_parent.remove(&rgb).is_some();
        let children: Vec<Rgb> = self
            .child_to_parent
            .iter()
            .filter(|(_, &p)| p == rgb)
            .map(|(&c, _)| c)
            .collect();
        for child in children {
            self.child_to_parent.remove(&child);
            changed = true;
        }
        changed
    }

    /// 全部の親子をルート直付けにし、背景と自分自身への親子を除く（`_normalize_groups`）。
    pub(crate) fn normalize_groups(&mut self) {
        let mut cleaned = std::collections::BTreeMap::new();
        for (&child, &parent) in &self.child_to_parent {
            if child == BACKGROUND || child == parent {
                continue;
            }
            let root = self.group_root(parent);
            if root == child || root == BACKGROUND {
                continue;
            }
            cleaned.insert(child, root);
        }
        self.child_to_parent = cleaned;
    }

    /// 子を親のすぐ後ろへまとめる（`_reorder_children_under_parents`）。
    pub(crate) fn reorder_children_under_parents(&mut self) {
        if self.child_to_parent.is_empty() {
            return;
        }
        let order = self.colors.clone();
        let mut children_by_root: std::collections::BTreeMap<Rgb, Vec<Rgb>> = Default::default();
        for &rgb in &order {
            if self.child_to_parent.contains_key(&rgb) {
                children_by_root
                    .entry(self.group_root(rgb))
                    .or_default()
                    .push(rgb);
            }
        }
        let mut result = Vec::with_capacity(order.len());
        for &rgb in &order {
            if self.child_to_parent.contains_key(&rgb) {
                continue;
            }
            result.push(rgb);
            if let Some(children) = children_by_root.get(&rgb) {
                result.extend(children);
            }
        }
        for &rgb in &order {
            if self.child_to_parent.contains_key(&rgb) && !result.contains(&rgb) {
                result.push(rgb);
            }
        }
        self.colors = result;
    }

    /// 行へ色を落とした（`_handle_color_drop`）。中ほどなら子にし、上下の端なら並べ替える。
    /// 選択した色をつかんだときは、選択した色をまとめて動かす。
    pub fn drop_color(&mut self, dragged: Rgb, target: Rgb, mode: DropMode) -> Option<Recorded> {
        if dragged == target || dragged == BACKGROUND || target == BACKGROUND {
            return None;
        }
        let label = if mode == DropMode::Child {
            "親子付け"
        } else {
            "使用色の並べ替え"
        };
        self.edit(label, |s| s.apply_drop(dragged, target, mode)).1
    }

    fn selected_without_background(&self) -> BTreeSet<Rgb> {
        self.selected
            .iter()
            .copied()
            .filter(|&rgb| rgb != BACKGROUND)
            .collect()
    }

    fn apply_drop(&mut self, dragged: Rgb, target: Rgb, mode: DropMode) {
        let selected = self.selected_without_background();
        if mode == DropMode::Child {
            let children: Vec<Rgb> = if selected.contains(&dragged) {
                self.colors
                    .iter()
                    .copied()
                    .filter(|rgb| selected.contains(rgb) && *rgb != target)
                    .collect()
            } else {
                vec![dragged]
            };
            for child in children {
                self.make_child_of(child, target);
            }
            return;
        }
        // 親をつかんだときは、子ごと1つのかたまりとして動かす（親子を崩さない）。
        if self.child_to_parent.values().any(|&p| p == dragged) {
            self.reorder_parent_group(dragged, target, mode);
            return;
        }
        let block = selected.contains(&dragged) && selected.len() > 1;
        let moved: BTreeSet<Rgb> = if block {
            selected.clone()
        } else {
            [dragged].into()
        };
        if block {
            self.reorder_color_block(&selected, target, mode);
        } else {
            self.reorder_color(dragged, target, mode);
        }
        // 子を親の下から動かしたときだけ、その子の親子を外す。
        for rgb in moved {
            self.child_to_parent.remove(&rgb);
        }
    }

    /// `child` を `parent` の子にする（`_make_child_of`）。循環になるなら何もしない。
    pub(crate) fn make_child_of(&mut self, child: Rgb, parent: Rgb) {
        let mut probe = parent;
        let mut guard = 0;
        while let Some(&next) = self.child_to_parent.get(&probe) {
            if guard > self.child_to_parent.len() {
                break;
            }
            if probe == child {
                return;
            }
            probe = next;
            guard += 1;
        }
        let root = self.group_root(parent);
        if root == child {
            return;
        }
        let grandchildren: Vec<Rgb> = self
            .child_to_parent
            .iter()
            .filter(|(_, &p)| p == child)
            .map(|(&c, _)| c)
            .collect();
        for grandchild in grandchildren {
            self.child_to_parent.insert(grandchild, root);
        }
        self.child_to_parent.insert(child, root);
        self.normalize_groups();
        self.reorder_children_under_parents();
    }

    fn reorder_color(&mut self, dragged: Rgb, target: Rgb, mode: DropMode) {
        let Some(drag_index) = self.color_index(dragged) else {
            return;
        };
        let moved = self.colors.remove(drag_index);
        let Some(mut target_index) = self.color_index(target) else {
            self.colors.insert(drag_index, moved);
            return;
        };
        if mode == DropMode::After {
            target_index += 1;
        }
        self.colors.insert(target_index.max(1), moved);
    }

    fn reorder_color_block(&mut self, selected: &BTreeSet<Rgb>, target: Rgb, mode: DropMode) {
        let mut selected = selected.clone();
        selected.remove(&BACKGROUND);
        if selected.is_empty() || selected.contains(&target) {
            return;
        }
        let moved: Vec<Rgb> = self
            .colors
            .iter()
            .copied()
            .filter(|rgb| selected.contains(rgb))
            .collect();
        if moved.is_empty() {
            return;
        }
        let remaining: Vec<Rgb> = self
            .colors
            .iter()
            .copied()
            .filter(|rgb| !selected.contains(rgb))
            .collect();
        let Some(mut index) = remaining.iter().position(|&rgb| rgb == target) else {
            return;
        };
        if mode == DropMode::After {
            index += 1;
        }
        let index = index.max(1);
        let mut colors = remaining[..index].to_vec();
        colors.extend(moved);
        colors.extend_from_slice(&remaining[index..]);
        self.colors = colors;
    }

    fn reorder_parent_group(&mut self, parent: Rgb, target: Rgb, mode: DropMode) {
        let mut group = vec![parent];
        group.extend(
            self.colors
                .iter()
                .copied()
                .filter(|rgb| self.child_to_parent.get(rgb) == Some(&parent)),
        );
        let group_set: BTreeSet<Rgb> = group.iter().copied().collect();
        if group_set.contains(&target) {
            return;
        }
        let moved: Vec<Rgb> = group
            .iter()
            .copied()
            .filter(|rgb| self.colors.contains(rgb))
            .collect();
        let remaining: Vec<Rgb> = self
            .colors
            .iter()
            .copied()
            .filter(|rgb| !group_set.contains(rgb))
            .collect();
        if !remaining.contains(&target) {
            return;
        }
        // 落とす先も親子なら、その途中へは割り込ませない。
        let mut target_parent = self.child_to_parent.get(&target).copied();
        if target_parent.is_none() && self.child_to_parent.values().any(|&p| p == target) {
            target_parent = Some(target);
        }
        let target_group: BTreeSet<Rgb> = match target_parent {
            Some(tp) => remaining
                .iter()
                .copied()
                .filter(|rgb| *rgb == tp || self.child_to_parent.get(rgb) == Some(&tp))
                .collect(),
            None => [target].into(),
        };
        let indexes: Vec<usize> = remaining
            .iter()
            .enumerate()
            .filter(|(_, rgb)| target_group.contains(rgb))
            .map(|(i, _)| i)
            .collect();
        let index = if mode == DropMode::After {
            indexes.iter().max().map_or(0, |i| i + 1)
        } else {
            indexes.iter().copied().min().unwrap_or(0)
        }
        .max(1);
        let mut colors = remaining[..index].to_vec();
        colors.extend(moved);
        colors.extend_from_slice(&remaining[index..]);
        self.colors = colors;
    }

    /// 右クリックの「親子を解除」。
    pub fn ungroup(&mut self, rgb: Rgb) -> Option<Recorded> {
        self.edit("親子を解除", |s| {
            if s.drop_group_links_for(rgb) {
                s.normalize_groups();
            }
        })
        .1
    }

    /// 「親子をすべて解除」。
    pub fn clear_groups(&mut self) -> Option<Recorded> {
        if self.child_to_parent.is_empty() {
            return None;
        }
        self.edit("親子をすべて解除", |s| s.child_to_parent.clear())
            .1
    }

    /// カラーチャートを当てたあとの並び・親子（`ColorChartController.apply` の後半）。
    pub(crate) fn set_order_and_groups(
        &mut self,
        order: Vec<Rgb>,
        groups: std::collections::BTreeMap<Rgb, Rgb>,
    ) {
        let mut colors = vec![BACKGROUND];
        for rgb in order {
            if self.colors.contains(&rgb) && !colors.contains(&rgb) {
                colors.push(rgb);
            }
        }
        self.colors = colors;
        self.child_to_parent = groups;
        self.normalize_groups();
        self.reorder_children_under_parents();
    }
}
