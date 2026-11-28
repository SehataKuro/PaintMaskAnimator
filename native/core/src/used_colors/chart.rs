//! カラーチャート（Paint Mask Group、`.pmag`）のデータ。
//!
//! Qt の `color_chart.py` の関数（`normalize_color_chart`・`merge_color_charts`・
//! `move_chart_color`・`assign_chart_tag`）と、`main_window_color_chart.py` の
//! 「現在の親子付けを登録」（`_current_palette`）・「チャートを使用色へ適用」（`apply`）。
//!
//! チャートは使用色の一覧とは別に、予定している親子とタグ（役割）を持つ。使用色へ当てる
//! ときは、いまの絵にある色だけを使う。JSON はプロジェクトのメタデータと同じ
//! [`Json`]（キーの順を保つ）で読み書きし、Python の dict と同じ順で書く。

use std::collections::{BTreeMap, BTreeSet};

use crate::project::json::Json;

use super::categories::{parse_hex, py_str, py_str_or_empty, truthy};
use super::{hex, Rgb, UsedColors, BACKGROUND};

pub const PMAG_FORMAT: &str = "PaintMaskGroup";
pub const PMAG_VERSION: i64 = 1;
const MAX_CHART_TILES: usize = 1024;
const MAX_CHART_CHILDREN: usize = 4096;
const MAX_TAGS: usize = 1024;
/// タグの既定の色。
pub const DEFAULT_TAG_COLOR: &str = "#58667A";

/// チャートの1色（子）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ChartColor {
    pub color: String,
    pub tag: String,
}

/// チャートの1タイル（親1色と子の色）。親が無いタイル（`placeholder`）もある。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Tile {
    pub id: String,
    pub name: String,
    /// 親の色（`#RRGGBB`）。placeholder なら None。
    pub parent: Option<String>,
    pub parent_tag: String,
    pub placeholder: bool,
    pub children: Vec<ChartColor>,
}

/// 正規化したチャート。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Chart {
    pub tiles: Vec<Tile>,
    pub tag_library: Vec<String>,
    /// タグ名と色（`tag_library` の順）。
    pub tag_colors: Vec<(String, String)>,
    pub tag_order: Vec<String>,
}

impl Default for Chart {
    fn default() -> Self {
        normalize(&Json::Null)
    }
}

/// タイルの中の1色の位置（`move_chart_color` の hit）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Hit {
    pub group_id: String,
    pub role: Role,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Role {
    Parent,
    /// 子（`children` の元の番号）。
    Child(usize),
}

fn hex_color(value: Option<&Json>) -> Option<String> {
    parse_hex(&py_str_or_empty(value)).map(hex)
}

fn tag_of(value: Option<&Json>) -> String {
    py_str_or_empty(value).trim().chars().take(32).collect()
}

fn tag_text(value: &str) -> String {
    value.trim().chars().take(32).collect()
}

fn unique_id(value: Option<&Json>, used: &mut BTreeSet<String>, fallback: usize) -> String {
    let raw = py_str_or_empty(value);
    let mut base = String::new();
    let mut in_run = false;
    for c in raw.chars() {
        if c.is_ascii_alphanumeric() || c == '_' || c == '-' {
            base.push(c);
            in_run = false;
        } else if !in_run {
            base.push('_');
            in_run = true;
        }
    }
    let base: String = base.trim_matches('_').chars().take(64).collect();
    let base = if base.is_empty() {
        format!("group_{fallback}")
    } else {
        base
    };
    let mut candidate = base.clone();
    let mut suffix = 2;
    while used.contains(&candidate) {
        candidate = format!("{base}_{suffix}");
        suffix += 1;
    }
    used.insert(candidate.clone());
    candidate
}

fn casefold(text: &str) -> String {
    text.to_lowercase()
}

fn list(value: Option<&Json>) -> &[Json] {
    match value {
        Some(Json::Array(items)) => items,
        _ => &[],
    }
}

/// `normalize_color_chart`：古い PMAG の形も含め、上限つきの正規の形にする。
pub fn normalize(data: &Json) -> Chart {
    let source = if matches!(data, Json::Object(_)) {
        data.clone()
    } else {
        Json::Object(Vec::new())
    };
    let mut tag_colors: Vec<(String, String)> = vec![("Base".into(), "#FFFFFF".into())];
    let set_color = |colors: &mut Vec<(String, String)>, name: String, color: String| match colors
        .iter_mut()
        .find(|(n, _)| *n == name)
    {
        Some(entry) => entry.1 = color,
        None => colors.push((name, color)),
    };
    if let Some(Json::Object(raw)) = source.get("tag_colors") {
        for (raw_name, raw_color) in raw.iter().take(MAX_TAGS) {
            let name = tag_text(raw_name);
            let color = hex_color(Some(raw_color));
            if let (false, Some(color)) = (name.is_empty(), color) {
                set_color(&mut tag_colors, name, color);
            }
        }
    }
    let mut library: Vec<String> = Vec::new();
    for value in list(source.get("tag_library")).iter().take(MAX_TAGS) {
        let name = tag_of(Some(value));
        if !name.is_empty() && !library.contains(&name) {
            library.push(name);
        }
    }
    library.retain(|n| n != "Base");
    library.insert(0, "Base".into());

    let mut order: Vec<String> = Vec::new();
    for value in list(source.get("tag_order")).iter().take(MAX_TAGS) {
        let name = tag_of(Some(value));
        if !name.is_empty() && casefold(&name) != "base" && !order.contains(&name) {
            order.push(name);
        }
    }

    let mut tiles: Vec<Tile> = Vec::new();
    let mut used_ids = BTreeSet::new();
    if let Some(Json::Array(raw_tiles)) = source.get("tiles") {
        let mut total_children = 0;
        for (index, raw_tile) in raw_tiles.iter().take(MAX_CHART_TILES).enumerate() {
            let index = index + 1;
            if !matches!(raw_tile, Json::Object(_)) {
                continue;
            }
            let raw_parent = match raw_tile.get("parent") {
                None | Some(Json::Null) => Json::Object(Vec::new()),
                Some(p @ Json::Object(_)) => p.clone(),
                Some(_) => continue,
            };
            let parent_color = hex_color(raw_parent.get("color"));
            let placeholder =
                raw_tile.get("placeholder").is_some_and(truthy) || parent_color.is_none();
            let parent_tag = if placeholder {
                String::new()
            } else {
                match tag_of(raw_parent.get("tag")) {
                    t if t.is_empty() => "Base".into(),
                    t => t,
                }
            };
            let mut children = Vec::new();
            let mut seen = BTreeSet::new();
            for raw_child in list(raw_tile.get("children")) {
                if total_children >= MAX_CHART_CHILDREN {
                    break;
                }
                if !matches!(raw_child, Json::Object(_)) {
                    continue;
                }
                let Some(color) = hex_color(raw_child.get("color")) else {
                    continue;
                };
                if seen.contains(&color) {
                    continue;
                }
                if !placeholder && Some(&color) == parent_color.as_ref() {
                    continue;
                }
                let tag = tag_of(raw_child.get("tag"));
                seen.insert(color.clone());
                children.push(ChartColor { color, tag });
                total_children += 1;
            }
            if placeholder && children.is_empty() {
                continue;
            }
            let id = unique_id(raw_tile.get("id"), &mut used_ids, index);
            let name: String = raw_tile
                .get("name")
                .map(py_str)
                .unwrap_or_default()
                .trim()
                .chars()
                .take(64)
                .collect();
            tiles.push(Tile {
                id,
                name,
                parent: if placeholder { None } else { parent_color },
                parent_tag,
                placeholder,
                children,
            });
        }
    } else {
        // 最初の平らな形（groups・parent_tags・standalone）を読み替える。
        let mut parent_tags: BTreeMap<String, String> = BTreeMap::new();
        for entry in list(source.get("parent_tags")).iter().take(MAX_CHART_TILES) {
            if !matches!(entry, Json::Object(_)) {
                continue;
            }
            let parent = hex_color(entry.get("parent"));
            let name = tag_of(entry.get("tag"));
            if let (Some(parent), false) = (parent, name.is_empty()) {
                parent_tags.insert(parent, name);
            }
        }
        let group_names = source.get("group_names").cloned();
        let mut by_parent: Vec<(String, Vec<ChartColor>)> = Vec::new();
        for entry in list(source.get("groups")).iter().take(MAX_CHART_CHILDREN) {
            if !matches!(entry, Json::Object(_)) {
                continue;
            }
            let (Some(parent), Some(child)) = (
                hex_color(entry.get("parent")),
                hex_color(entry.get("child")),
            ) else {
                continue;
            };
            if parent == child {
                continue;
            }
            let slot = match by_parent.iter().position(|(p, _)| *p == parent) {
                Some(i) => i,
                None => {
                    by_parent.push((parent.clone(), Vec::new()));
                    by_parent.len() - 1
                }
            };
            if !by_parent[slot].1.iter().any(|c| c.color == child) {
                by_parent[slot].1.push(ChartColor {
                    color: child,
                    tag: tag_of(entry.get("tag")),
                });
            }
        }
        for (parent, children) in by_parent {
            let id = unique_id(None, &mut used_ids, tiles.len() + 1);
            let name: String = match &group_names {
                Some(names @ Json::Object(_)) => names.get(&parent).map(py_str).unwrap_or_default(),
                _ => String::new(),
            }
            .trim()
            .chars()
            .take(64)
            .collect();
            tiles.push(Tile {
                id,
                name,
                parent_tag: parent_tags
                    .get(&parent)
                    .cloned()
                    .unwrap_or_else(|| "Base".into()),
                parent: Some(parent),
                placeholder: false,
                children,
            });
        }
        let room = MAX_CHART_TILES.saturating_sub(tiles.len());
        for entry in list(source.get("standalone")).iter().take(room) {
            if !matches!(entry, Json::Object(_)) {
                continue;
            }
            let Some(color) = hex_color(entry.get("color")) else {
                continue;
            };
            let id = unique_id(None, &mut used_ids, tiles.len() + 1);
            let tag = match tag_of(entry.get("tag")) {
                t if t.is_empty() => "Base".into(),
                t => t,
            };
            tiles.push(Tile {
                id,
                name: String::new(),
                parent: Some(color),
                parent_tag: tag,
                placeholder: false,
                children: Vec::new(),
            });
        }
    }

    for tile in &tiles {
        if !tile.parent_tag.is_empty() && !library.contains(&tile.parent_tag) {
            library.push(tile.parent_tag.clone());
        }
        for child in &tile.children {
            if !child.tag.is_empty() && !library.contains(&child.tag) {
                library.push(child.tag.clone());
            }
        }
    }
    for (name, _) in &tag_colors {
        if !library.contains(name) {
            library.push(name.clone());
        }
    }
    for name in &library {
        if !tag_colors.iter().any(|(n, _)| n == name) {
            let color = if name == "Base" {
                "#FFFFFF"
            } else {
                DEFAULT_TAG_COLOR
            };
            tag_colors.push((name.clone(), color.into()));
        }
    }
    for name in &library {
        if casefold(name) != "base" && !order.contains(name) {
            order.push(name.clone());
        }
    }
    library.truncate(MAX_TAGS);
    order.truncate(MAX_TAGS);
    let tag_colors = library
        .iter()
        .map(|name| {
            let color = tag_colors
                .iter()
                .find(|(n, _)| n == name)
                .map(|(_, c)| c.clone())
                .unwrap_or_default();
            (name.clone(), color)
        })
        .collect();
    Chart {
        tiles,
        tag_library: library,
        tag_colors,
        tag_order: order,
    }
}

impl Chart {
    /// Python の `normalize_color_chart` の戻り値と同じ形・同じキーの順の JSON。
    pub fn to_json(&self) -> Json {
        let mut groups = Vec::new();
        let mut parent_tags = Vec::new();
        let mut group_names = Vec::new();
        let mut seen = BTreeSet::new();
        for tile in &self.tiles {
            let Some(parent) = &tile.parent else { continue };
            if !tile.name.is_empty() {
                match group_names
                    .iter_mut()
                    .find(|(p, _): &&mut (String, Json)| p == parent)
                {
                    Some(entry) => entry.1 = Json::Str(tile.name.clone()),
                    None => group_names.push((parent.clone(), Json::Str(tile.name.clone()))),
                }
            }
            if !tile.parent_tag.is_empty() && seen.insert(parent.clone()) {
                parent_tags.push(Json::Object(vec![
                    ("parent".into(), Json::Str(parent.clone())),
                    ("tag".into(), Json::Str(tile.parent_tag.clone())),
                ]));
            }
            for child in &tile.children {
                groups.push(Json::Object(vec![
                    ("child".into(), Json::Str(child.color.clone())),
                    ("parent".into(), Json::Str(parent.clone())),
                    ("tag".into(), Json::Str(child.tag.clone())),
                ]));
            }
        }
        let strings =
            |items: &[String]| Json::Array(items.iter().cloned().map(Json::Str).collect());
        let tiles = self
            .tiles
            .iter()
            .map(|tile| {
                Json::Object(vec![
                    ("id".into(), Json::Str(tile.id.clone())),
                    ("name".into(), Json::Str(tile.name.clone())),
                    (
                        "parent".into(),
                        Json::Object(vec![
                            (
                                "color".into(),
                                tile.parent.clone().map_or(Json::Null, Json::Str),
                            ),
                            ("tag".into(), Json::Str(tile.parent_tag.clone())),
                        ]),
                    ),
                    ("placeholder".into(), Json::Bool(tile.placeholder)),
                    (
                        "children".into(),
                        Json::Array(
                            tile.children
                                .iter()
                                .map(|c| {
                                    Json::Object(vec![
                                        ("color".into(), Json::Str(c.color.clone())),
                                        ("tag".into(), Json::Str(c.tag.clone())),
                                    ])
                                })
                                .collect(),
                        ),
                    ),
                ])
            })
            .collect();
        Json::Object(vec![
            ("format".into(), Json::Str(PMAG_FORMAT.into())),
            ("format_version".into(), Json::Int(PMAG_VERSION.to_string())),
            ("groups".into(), Json::Array(groups)),
            ("parent_tags".into(), Json::Array(parent_tags)),
            ("tag_library".into(), strings(&self.tag_library)),
            (
                "tag_colors".into(),
                Json::Object(
                    self.tag_colors
                        .iter()
                        .map(|(n, c)| (n.clone(), Json::Str(c.clone())))
                        .collect(),
                ),
            ),
            ("group_names".into(), Json::Object(group_names)),
            ("tag_order".into(), strings(&self.tag_order)),
            ("tiles".into(), Json::Array(tiles)),
        ])
    }

    /// 正規化し直す（Python は編集のたびに normalize_color_chart を通す）。
    fn renormalized(&self) -> Chart {
        normalize(&self.to_json())
    }

    fn tile_index(&self, id: &str) -> Option<usize> {
        self.tiles.iter().position(|t| t.id == id)
    }

    pub fn tag_color(&self, name: &str) -> String {
        self.tag_colors
            .iter()
            .find(|(n, _)| n == name)
            .map_or_else(|| DEFAULT_TAG_COLOR.into(), |(_, c)| c.clone())
    }

    /// 親子の組の数（Python の `chart["groups"]` の長さ）。
    pub fn group_count(&self) -> usize {
        self.tiles
            .iter()
            .filter(|t| t.parent.is_some())
            .map(|t| t.children.len())
            .sum()
    }

    /// 「{n}グループ・{m}子色」か「未登録」。
    pub fn summary(&self) -> String {
        if self.tiles.is_empty() {
            return "未登録".into();
        }
        let children: usize = self.tiles.iter().map(|t| t.children.len()).sum();
        format!("{}グループ・{}子色", self.tiles.len(), children)
    }

    /// PMAG の文字列（`write_pmag`：json.dumps(ensure_ascii=False, indent=2)）。
    pub fn to_pmag(&self) -> String {
        self.to_json().dumps(Some(2)).unwrap_or_default()
    }

    /// `merge_color_charts`：前の登録を残したまま、新しい親子を足す。
    pub fn merge(&self, current: &Chart) -> Chart {
        let mut merged = self.clone();
        for source in &current.tiles {
            let target = source.parent.as_ref().and_then(|p| {
                merged
                    .tiles
                    .iter()
                    .rposition(|t| !t.placeholder && t.parent.as_ref() == Some(p))
            });
            let Some(target) = target else {
                let mut added = source.clone();
                let mut used: BTreeSet<String> =
                    merged.tiles.iter().map(|t| t.id.clone()).collect();
                added.id = unique_id(
                    Some(&Json::Str(added.id.clone())),
                    &mut used,
                    merged.tiles.len() + 1,
                );
                merged.tiles.push(added);
                continue;
            };
            let tile = &mut merged.tiles[target];
            for child in &source.children {
                if !tile.children.iter().any(|c| c.color == child.color) {
                    tile.children.push(child.clone());
                }
            }
            if !source.parent_tag.is_empty() {
                tile.parent_tag = source.parent_tag.clone();
            }
            if !source.name.is_empty() {
                tile.name = source.name.clone();
            }
        }
        let mut library = self.tag_library.clone();
        for name in &current.tag_library {
            if !library.contains(name) {
                library.push(name.clone());
            }
        }
        merged.tag_library = library;
        let mut colors = self.tag_colors.clone();
        for (name, color) in &current.tag_colors {
            match colors.iter_mut().find(|(n, _)| n == name) {
                Some(entry) => entry.1 = color.clone(),
                None => colors.push((name.clone(), color.clone())),
            }
        }
        merged.tag_colors = colors;
        let mut order = self.tag_order.clone();
        for name in &current.tag_order {
            if !order.contains(name) {
                order.push(name.clone());
            }
        }
        merged.tag_order = order;
        merged.renormalized()
    }

    /// `move_chart_color`：1色をタイルの間で動かす。`target` が None なら新しいタイルにする。
    pub fn move_color(&self, source: &Hit, target: Option<&Hit>) -> Option<Chart> {
        let mut chart = self.clone();
        let index = chart.tile_index(&source.group_id)?;
        let moved = match source.role {
            Role::Parent => {
                let tile = &mut chart.tiles[index];
                if tile.placeholder {
                    return None;
                }
                let moved = ChartColor {
                    color: tile.parent.clone().unwrap_or_default(),
                    tag: tile.parent_tag.clone(),
                };
                if tile.children.is_empty() {
                    chart.tiles.remove(index);
                } else {
                    tile.parent = None;
                    tile.parent_tag = String::new();
                    tile.placeholder = true;
                }
                moved
            }
            Role::Child(child) => {
                let tile = &mut chart.tiles[index];
                if child >= tile.children.len() {
                    return None;
                }
                let moved = tile.children.remove(child);
                if tile.placeholder && tile.children.is_empty() {
                    chart.tiles.remove(index);
                }
                moved
            }
        };
        let target_index = target.and_then(|t| chart.tile_index(&t.group_id));
        let (Some(target), Some(target_index)) = (target, target_index) else {
            let mut used: BTreeSet<String> = chart.tiles.iter().map(|t| t.id.clone()).collect();
            let id = unique_id(None, &mut used, chart.tiles.len() + 1);
            chart.tiles.push(Tile {
                id,
                name: String::new(),
                parent: Some(moved.color),
                parent_tag: if moved.tag.is_empty() {
                    "Base".into()
                } else {
                    moved.tag
                },
                placeholder: false,
                children: Vec::new(),
            });
            return Some(chart.renormalized());
        };
        let tile = &mut chart.tiles[target_index];
        if target.role == Role::Parent && tile.placeholder {
            tile.parent = Some(moved.color);
            tile.parent_tag = if moved.tag.is_empty() {
                "Base".into()
            } else {
                moved.tag
            };
            tile.placeholder = false;
        } else {
            // 親を別のタイルへ落とすと子になる。Base は親の役割なので子には持ち込まない。
            let child = ChartColor {
                tag: if moved.tag == "Base" {
                    String::new()
                } else {
                    moved.tag
                },
                color: moved.color,
            };
            tile.children.retain(|c| c.color != child.color);
            match target.role {
                Role::Child(at) => {
                    let at = at.min(tile.children.len());
                    tile.children.insert(at, child);
                }
                Role::Parent => tile.children.push(child),
            }
        }
        Some(chart.renormalized())
    }

    /// `assign_chart_tag`：1色にタグを付ける。
    pub fn assign_tag(&self, hit: &Hit, tag: &str) -> Option<Chart> {
        let mut chart = self.clone();
        let index = chart.tile_index(&hit.group_id)?;
        let name = tag_text(tag);
        let tile = &mut chart.tiles[index];
        match hit.role {
            Role::Parent => {
                if tile.placeholder {
                    return None;
                }
                tile.parent_tag = if name.is_empty() {
                    "Base".into()
                } else {
                    name.clone()
                };
            }
            Role::Child(child) => {
                tile.children.get_mut(child)?.tag = name.clone();
            }
        }
        if !name.is_empty() && !chart.tag_library.contains(&name) {
            chart.tag_library.push(name.clone());
            chart.tag_colors.push((name, DEFAULT_TAG_COLOR.into()));
        }
        Some(chart.renormalized())
    }

    /// グループ名を変える（ダブルクリックで見出しを編集）。
    pub fn rename_group(&self, group_id: &str, name: &str) -> Option<Chart> {
        let mut chart = self.clone();
        let index = chart.tile_index(group_id)?;
        let name: String = name.trim().chars().take(64).collect();
        if chart.tiles[index].name == name {
            return None;
        }
        chart.tiles[index].name = name;
        Some(chart.renormalized())
    }

    /// タグの並び（Base は先頭のまま）。
    pub fn set_tag_order(&self, order: &[String]) -> Chart {
        let mut chart = self.clone();
        chart.tag_order = order.iter().filter(|n| *n != "Base").cloned().collect();
        chart.renormalized()
    }

    /// タグを足す（同じ名前があれば色だけ変える）。
    pub fn add_tag(&self, name: &str, color: &str) -> Option<Chart> {
        let name = tag_text(name);
        if name.is_empty() {
            return None;
        }
        let mut chart = self.clone();
        if !chart.tag_library.contains(&name) {
            chart.tag_library.push(name.clone());
            if name != "Base" {
                chart.tag_order.push(name.clone());
            }
        }
        match chart.tag_colors.iter_mut().find(|(n, _)| *n == name) {
            Some(entry) => entry.1 = color.to_string(),
            None => chart.tag_colors.push((name, color.to_string())),
        }
        Some(chart.renormalized())
    }

    /// タグの名前と色を変える。同じ名前のタグがすでにあれば Err（「同じ名前のタグが既にあります。」）。
    pub fn edit_tag(
        &self,
        old: &str,
        new: &str,
        color: &str,
    ) -> Result<Option<Chart>, &'static str> {
        let old = tag_text(old);
        if old.is_empty() {
            return Ok(None);
        }
        let new = if old == "Base" {
            "Base".to_string()
        } else {
            tag_text(new)
        };
        if new.is_empty() {
            return Ok(None);
        }
        let mut chart = self.clone();
        if new != old && chart.tag_library.contains(&new) {
            return Err("同じ名前のタグが既にあります。");
        }
        for tile in &mut chart.tiles {
            if tile.parent_tag == old {
                tile.parent_tag = new.clone();
            }
            for child in &mut tile.children {
                if child.tag == old {
                    child.tag = new.clone();
                }
            }
        }
        for name in chart
            .tag_library
            .iter_mut()
            .chain(chart.tag_order.iter_mut())
        {
            if *name == old {
                *name = new.clone();
            }
        }
        chart.tag_colors.retain(|(n, _)| *n != old);
        match chart.tag_colors.iter_mut().find(|(n, _)| *n == new) {
            Some(entry) => entry.1 = color.to_string(),
            None => chart.tag_colors.push((new, color.to_string())),
        }
        Ok(Some(chart.renormalized()))
    }

    /// タグを消す（Base は消せない）。親のタグは Base、子のタグは空に戻す。
    pub fn remove_tag(&self, name: &str) -> Option<Chart> {
        if name == "Base" || !self.tag_library.iter().any(|n| n == name) {
            return None;
        }
        let mut chart = self.clone();
        for tile in &mut chart.tiles {
            if tile.parent_tag == name {
                tile.parent_tag = "Base".into();
            }
            for child in &mut tile.children {
                if child.tag == name {
                    child.tag.clear();
                }
            }
        }
        chart.tag_library.retain(|n| n != name);
        chart.tag_order.retain(|n| n != name);
        chart.tag_colors.retain(|(n, _)| n != name);
        Some(chart.renormalized())
    }

    /// 画面に出す子の順（タグの並び→タグ名→色）。元の番号と組で返す（`ColorChartCanvas.set_chart`）。
    pub fn display_children(&self, tile: &Tile) -> Vec<(usize, ChartColor)> {
        let rank = |tag: &str| {
            self.tag_order
                .iter()
                .position(|n| n == tag)
                .unwrap_or(self.tag_order.len() + 1)
        };
        let mut children: Vec<(usize, ChartColor)> =
            tile.children.iter().cloned().enumerate().collect();
        children.sort_by(|(_, a), (_, b)| {
            (rank(&a.tag), casefold(&a.tag), &a.color).cmp(&(
                rank(&b.tag),
                casefold(&b.tag),
                &b.color,
            ))
        });
        children
    }
}

/// PMAG のファイルの中身を読む（`read_pmag`）。
pub fn read_pmag(text: &str) -> Result<Chart, &'static str> {
    if text.len() > 16 * 1024 * 1024 {
        return Err("PMAGファイルが大きすぎます。");
    }
    let parsed =
        crate::project::json::parse(text).map_err(|_| "PaintMaskGroup形式ではありません。")?;
    let value = parsed.value;
    if !matches!(&value, Json::Object(_))
        || value.get("format") != Some(&Json::Str(PMAG_FORMAT.into()))
    {
        return Err("PaintMaskGroup形式ではありません。");
    }
    Ok(normalize(&value))
}

impl UsedColors {
    /// 「現在の親子付けを登録」の元になるチャート（`ColorChartController._current_palette`）。
    pub fn chart_from_groups(&self) -> Chart {
        let order = self.ordered_non_background();
        let mut parents: Vec<Rgb> = Vec::new();
        let parent_set: BTreeSet<Rgb> = self.child_to_parent.values().copied().collect();
        for &rgb in &order {
            if parent_set.contains(&rgb) && !parents.contains(&rgb) {
                parents.push(rgb);
            }
        }
        for &parent in self.child_to_parent.values() {
            if !parents.contains(&parent) {
                parents.push(parent);
            }
        }
        let mut tiles = Vec::new();
        for (index, &parent) in parents.iter().enumerate() {
            let mut children: Vec<Rgb> = order
                .iter()
                .copied()
                .filter(|c| self.child_to_parent.get(c) == Some(&parent))
                .collect();
            for (&child, &p) in &self.child_to_parent {
                if p == parent && !children.contains(&child) {
                    children.push(child);
                }
            }
            tiles.push(tile_json(
                format!("group_{}", index + 1),
                parent,
                self.parent_tags
                    .get(&parent)
                    .filter(|t| !t.is_empty())
                    .cloned()
                    .unwrap_or_else(|| "Base".into()),
                children
                    .iter()
                    .map(|c| (*c, self.child_tags.get(c).cloned().unwrap_or_default()))
                    .collect(),
            ));
        }
        let mut grouped: BTreeSet<Rgb> = self.child_to_parent.keys().copied().collect();
        grouped.extend(parent_set);
        for &rgb in &order {
            let tag = self.parent_tags.get(&rgb).cloned().unwrap_or_default();
            if grouped.contains(&rgb) || tag.is_empty() {
                continue;
            }
            tiles.push(tile_json(
                format!("group_{}", tiles.len() + 1),
                rgb,
                tag,
                Vec::new(),
            ));
        }
        let strings =
            |items: &[String]| Json::Array(items.iter().cloned().map(Json::Str).collect());
        normalize(&Json::Object(vec![
            ("format".into(), Json::Str(PMAG_FORMAT.into())),
            ("format_version".into(), Json::Int("1".into())),
            ("tiles".into(), Json::Array(tiles)),
            ("tag_library".into(), strings(&self.tag_library)),
            (
                "tag_colors".into(),
                Json::Object(
                    self.tag_colors
                        .iter()
                        .map(|(n, c)| (n.clone(), Json::Str(c.clone())))
                        .collect(),
                ),
            ),
            ("tag_order".into(), strings(&self.tag_order)),
        ]))
    }

    /// 「チャートを使用色へ適用」（`ColorChartController.apply`）。いまの絵にある色だけに、
    /// チャートの親子・タグ・並びを当てる。チャートが空なら false。
    pub fn apply_chart(&mut self, chart: &Chart) -> bool {
        if chart.tiles.is_empty() {
            return false;
        }
        let available: BTreeSet<Rgb> = self.ordered_non_background().into_iter().collect();
        let mut groups = BTreeMap::new();
        let mut child_tags = BTreeMap::new();
        let mut parent_tags = BTreeMap::new();
        let mut desired: Vec<Rgb> = Vec::new();
        let append = |desired: &mut Vec<Rgb>, rgb: Rgb| {
            desired.retain(|&c| c != rgb);
            desired.push(rgb);
        };
        let rank = |tag: &str| {
            chart
                .tag_order
                .iter()
                .position(|n| n == tag)
                .unwrap_or(chart.tag_order.len() + 1)
        };
        for tile in &chart.tiles {
            let parent = tile.parent.as_deref().and_then(parse_hex);
            let parent_available =
                !tile.placeholder && parent.is_some_and(|p| available.contains(&p));
            if parent_available {
                let p = parent.unwrap();
                append(&mut desired, p);
                if !tile.parent_tag.is_empty() {
                    parent_tags.insert(p, tile.parent_tag.clone());
                }
            }
            let mut children = tile.children.clone();
            children.sort_by(|a, b| {
                (rank(&a.tag), casefold(&a.tag), &a.color).cmp(&(
                    rank(&b.tag),
                    casefold(&b.tag),
                    &b.color,
                ))
            });
            for child in children {
                let Some(rgb) = parse_hex(&child.color).filter(|c| available.contains(c)) else {
                    continue;
                };
                append(&mut desired, rgb);
                if parent_available && Some(rgb) != parent {
                    groups.insert(rgb, parent.unwrap());
                    if !child.tag.is_empty() {
                        child_tags.insert(rgb, child.tag.clone());
                    }
                } else if !child.tag.is_empty() {
                    // 親が無いタイルの色は単独のまま、役割だけ残す。
                    parent_tags.insert(rgb, child.tag.clone());
                }
            }
        }
        for rgb in self.ordered_non_background() {
            if !desired.contains(&rgb) {
                desired.push(rgb);
            }
        }
        self.child_tags = child_tags;
        self.parent_tags = parent_tags;
        self.tag_library = chart.tag_library.clone();
        self.tag_colors = chart.tag_colors.clone();
        self.tag_order = chart.tag_order.clone();
        self.set_order_and_groups(desired, groups);
        true
    }
}

fn tile_json(id: String, parent: Rgb, tag: String, children: Vec<(Rgb, String)>) -> Json {
    Json::Object(vec![
        ("id".into(), Json::Str(id)),
        ("name".into(), Json::Str(String::new())),
        (
            "parent".into(),
            Json::Object(vec![
                ("color".into(), Json::Str(hex(parent))),
                ("tag".into(), Json::Str(tag)),
            ]),
        ),
        ("placeholder".into(), Json::Bool(false)),
        (
            "children".into(),
            Json::Array(
                children
                    .into_iter()
                    .map(|(c, t)| {
                        Json::Object(vec![
                            ("color".into(), Json::Str(hex(c))),
                            ("tag".into(), Json::Str(t)),
                        ])
                    })
                    .collect(),
            ),
        ),
    ])
}

/// 背景の色はチャートの対象にしない。
pub fn is_chart_color(rgb: Rgb) -> bool {
    rgb != BACKGROUND
}
