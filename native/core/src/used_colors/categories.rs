//! 使用色フォルダー（1色につき1フォルダー、1階層）と、行の並び・保存の形。
//!
//! Qt の `color_panel.py` のフォルダーの部分（`create_category`・`rename_category`・
//! `delete_category`・`_move_colors_to_category`・`serialize_categories`・
//! `restore_categories`）と、行を並べる `_reapply_row_order`。

use std::collections::BTreeSet;

use crate::project::json::Json;

use super::{hex, Recorded, Rgb, UsedColors, BACKGROUND};

/// 一覧の1行（描く順）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Row {
    /// 色の行。`in_category` ならフォルダーの中（字下げして薄い面に置く）。
    Color { rgb: Rgb, in_category: bool },
    /// フォルダーの見出し。
    Folder { name: String },
}

/// `#RRGGBB`・`#RGB` などを読む（QColor が受け付ける16進の形）。読めなければ None。
pub fn parse_hex(text: &str) -> Option<Rgb> {
    let digits = text.trim().strip_prefix('#')?;
    if !digits.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    let value = |s: &str| u32::from_str_radix(s, 16).ok();
    match digits.len() {
        3 => {
            let v: Vec<u8> = digits
                .chars()
                .map(|c| (c.to_digit(16).unwrap() * 17) as u8)
                .collect();
            Some((v[0], v[1], v[2]))
        }
        6 => Some((
            value(&digits[0..2])? as u8,
            value(&digits[2..4])? as u8,
            value(&digits[4..6])? as u8,
        )),
        // #AARRGGBB（透明度は使わない）。
        8 => Some((
            value(&digits[2..4])? as u8,
            value(&digits[4..6])? as u8,
            value(&digits[6..8])? as u8,
        )),
        // #RRRGGGBBB：12 ビットを 16 ビットへ広げてから 8 ビットへ丸める（QColor と同じ）。
        9 => {
            let c = |s: &str| value(s).map(|v| div_257((v << 4) | (v >> 8)));
            Some((c(&digits[0..3])?, c(&digits[3..6])?, c(&digits[6..9])?))
        }
        12 => {
            let c = |s: &str| value(s).map(div_257);
            Some((c(&digits[0..4])?, c(&digits[4..8])?, c(&digits[8..12])?))
        }
        _ => None,
    }
}

/// Qt の `qt_div_257`（16 ビットの色を 8 ビットへ。Qt 6.11 は四捨五入の `(x + 128) / 257`。
/// 0..65535 のすべてで PySide6 6.11 の QColor と同じになることを確かめた）。
pub(crate) fn div_257(x: u32) -> u8 {
    ((x + 128) / 257) as u8
}

impl UsedColors {
    /// フォルダーの中の色（今ある色だけ。並び順）。
    pub fn category_members(&self, name: &str) -> Vec<Rgb> {
        self.colors
            .iter()
            .copied()
            .filter(|&rgb| {
                rgb != BACKGROUND
                    && self.category_colors.get(&rgb).map(String::as_str) == Some(name)
            })
            .collect()
    }

    /// フォルダーの目の状態：全部表示なら Some(true)、全部非表示なら Some(false)、混在なら None。
    /// 空のフォルダーは全部表示として扱う。
    pub fn category_visibility(&self, name: &str) -> Option<bool> {
        let members = self.category_members(name);
        let shown = members.iter().filter(|&&rgb| self.is_enabled(rgb)).count();
        if shown == members.len() {
            Some(true)
        } else if shown == 0 {
            Some(false)
        } else {
            None
        }
    }

    /// 同じ名前（大文字小文字を区別しない）があれば「名前 2」…にする（`_unique_category_name`）。
    pub fn unique_category_name(&self, requested: &str) -> String {
        let base = match requested.trim() {
            "" => "新規フォルダー".to_string(),
            name => name.to_string(),
        };
        let existing: BTreeSet<String> = self
            .category_order
            .iter()
            .map(|n| n.to_lowercase())
            .collect();
        let mut name = base.clone();
        let mut suffix = 2;
        while existing.contains(&name.to_lowercase()) {
            name = format!("{base} {suffix}");
            suffix += 1;
        }
        name
    }

    fn create_category_raw(&mut self, name: &str) -> String {
        let name = self.unique_category_name(name);
        self.category_order.push(name.clone());
        self.category_collapsed.insert(name.clone(), false);
        name
    }

    /// フォルダーを作る（「使用色フォルダーを作成」）。作った名前も返す。
    pub fn create_category(&mut self, name: &str) -> (String, Option<Recorded>) {
        self.edit("使用色フォルダーを作成", |s| {
            s.create_category_raw(name)
        })
    }

    /// フォルダーの名前を変える（「使用色フォルダー名を変更」）。
    pub fn rename_category(&mut self, old: &str, new: &str) -> Option<Recorded> {
        let index = self.category_order.iter().position(|n| n == old)?;
        let new = new.trim();
        if new.is_empty() || new == old {
            return None;
        }
        let new = if new.to_lowercase() != old.to_lowercase() {
            self.unique_category_name(new)
        } else {
            new.to_string()
        };
        self.edit("使用色フォルダー名を変更", |s| {
            s.category_order[index] = new.clone();
            for category in s.category_colors.values_mut() {
                if category == old {
                    *category = new.clone();
                }
            }
            let collapsed = s.category_collapsed.remove(old).unwrap_or(false);
            s.category_collapsed.insert(new, collapsed);
        })
        .1
    }

    /// フォルダーを消す（中の色は未分類へ戻る）。
    pub fn delete_category(&mut self, name: &str) -> Option<Recorded> {
        if !self.category_order.iter().any(|n| n == name) {
            return None;
        }
        self.edit("使用色フォルダーを削除", |s| {
            s.category_order.retain(|n| n != name);
            s.category_collapsed.remove(name);
            s.category_colors.retain(|_, c| c != name);
        })
        .1
    }

    /// フォルダーを折りたたむ・開く（履歴には積まない）。
    pub fn set_category_collapsed(&mut self, name: &str, collapsed: bool) {
        if self.category_order.iter().any(|n| n == name) {
            self.category_collapsed.insert(name.to_string(), collapsed);
        }
    }

    /// 色をフォルダーへ入れる（`_move_colors_to_category`）。つかんだ色が選択中なら、選択した色を
    /// まとめて入れる。
    pub fn move_to_category(&mut self, dragged: Rgb, category: &str) -> Option<Recorded> {
        if !self.category_order.iter().any(|n| n == category) {
            return None;
        }
        let sources = self.delete_sources(dragged);
        if sources.is_empty() {
            return None;
        }
        self.edit("使用色をフォルダーへ移動", |s| {
            for rgb in sources {
                s.category_colors.insert(rgb, category.to_string());
            }
            s.category_collapsed.insert(category.to_string(), false);
        })
        .1
    }

    /// 新しいフォルダーを作って入れる（右クリックの「新規フォルダー…」）。Qt と同じく作成と
    /// 移動の2件になる。
    pub fn move_to_new_category(&mut self, dragged: Rgb, name: &str) -> Vec<Recorded> {
        let (category, created) = self.create_category(name);
        let moved = self.move_to_category(dragged, &category);
        created.into_iter().chain(moved).collect()
    }

    /// 色をフォルダーから出す（右クリックの「未分類」）。
    pub fn move_to_uncategorized(&mut self, clicked: Rgb) -> Option<Recorded> {
        let sources = self.delete_sources(clicked);
        self.edit("使用色をフォルダーから移動", |s| {
            for rgb in sources {
                s.category_colors.remove(&rgb);
            }
        })
        .1
    }

    /// 描く順の行：背景、フォルダー（見出しと中の色。たたんだフォルダーの色は出さない）、
    /// 未分類の色（`_reapply_row_order`）。
    pub fn rows(&self) -> Vec<Row> {
        let mut rows = vec![Row::Color {
            rgb: BACKGROUND,
            in_category: false,
        }];
        for name in &self.category_order {
            rows.push(Row::Folder { name: name.clone() });
            if self.is_collapsed(name) {
                continue;
            }
            for rgb in self.category_members(name) {
                rows.push(Row::Color {
                    rgb,
                    in_category: true,
                });
            }
        }
        for &rgb in &self.colors {
            if rgb == BACKGROUND || self.category_colors.contains_key(&rgb) {
                continue;
            }
            rows.push(Row::Color {
                rgb,
                in_category: false,
            });
        }
        rows
    }

    /// プロジェクトに保存する形（`serialize_categories`）。
    pub fn serialize_categories(&self) -> Json {
        let folders = self
            .category_order
            .iter()
            .map(|name| {
                Json::Object(vec![
                    ("name".into(), Json::Str(name.clone())),
                    ("collapsed".into(), Json::Bool(self.is_collapsed(name))),
                ])
            })
            .collect();
        let assignments = self
            .category_colors
            .iter()
            .filter(|(_, c)| self.category_order.contains(c))
            .map(|(&rgb, c)| (hex(rgb), Json::Str(c.clone())))
            .collect();
        let enabled = self
            .enabled
            .iter()
            .map(|(&rgb, &on)| (hex(rgb), Json::Bool(on)))
            .collect();
        Json::Object(vec![
            ("folders".into(), Json::Array(folders)),
            ("assignments".into(), Json::Object(assignments)),
            ("enabled".into(), Json::Object(enabled)),
        ])
    }

    /// 保存した形から戻す（`restore_categories`）。表示は、いまある色だけに当てる。
    pub fn restore_categories(&mut self, payload: Option<&Json>) {
        self.category_order.clear();
        self.category_colors.clear();
        self.category_collapsed.clear();
        let Some(payload @ Json::Object(_)) = payload else {
            return;
        };
        if let Some(Json::Array(folders)) = payload.get("folders") {
            for entry in folders {
                if !matches!(entry, Json::Object(_)) {
                    continue;
                }
                let name = entry.get("name").map(py_str).unwrap_or_default();
                let name = name.trim();
                if name.is_empty() {
                    continue;
                }
                let name = self.create_category_raw(name);
                let collapsed = entry.get("collapsed").is_some_and(truthy);
                self.category_collapsed.insert(name, collapsed);
            }
        }
        if let Some(Json::Object(assignments)) = payload.get("assignments") {
            for (key, category) in assignments {
                let category = py_str(category);
                if let Some(rgb) = parse_hex(key) {
                    if self.category_order.contains(&category) {
                        self.category_colors.insert(rgb, category);
                    }
                }
            }
        }
        if let Some(Json::Object(enabled)) = payload.get("enabled") {
            for (key, visible) in enabled {
                if let Some(rgb) = parse_hex(key) {
                    if self.contains(rgb) {
                        self.enabled.insert(rgb, truthy(visible));
                    }
                }
            }
        }
    }
}

/// Python の `bool(value)`。
pub(crate) fn truthy(value: &Json) -> bool {
    match value {
        Json::Null => false,
        Json::Bool(b) => *b,
        Json::Int(digits) => !digits
            .trim_start_matches('-')
            .trim_start_matches('0')
            .is_empty(),
        Json::Float(f) => *f != 0.0,
        Json::Str(s) => !s.is_empty(),
        Json::Array(items) => !items.is_empty(),
        Json::Object(fields) => !fields.is_empty(),
    }
}

/// Python の `str(value)`（入れ子の配列・オブジェクトは JSON の形で近似する）。
pub(crate) fn py_str(value: &Json) -> String {
    match value {
        Json::Null => "None".into(),
        Json::Bool(true) => "True".into(),
        Json::Bool(false) => "False".into(),
        Json::Int(digits) => digits.clone(),
        Json::Float(f) => crate::project::json::float_repr(*f).unwrap_or_default(),
        Json::Str(s) => s.clone(),
        other => other.dumps(None).unwrap_or_default(),
    }
}

/// Python の `str(value or "")`。
pub(crate) fn py_str_or_empty(value: Option<&Json>) -> String {
    match value {
        Some(v) if truthy(v) => py_str(v),
        _ => String::new(),
    }
}
