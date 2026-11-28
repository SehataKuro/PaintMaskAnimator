//! タイムリマップの貼り付け（Qt 版の `TimeRemapController` と `widgets.TimeRemapPasteDialog`）。
//!
//! - 貼り付けた文字を、After Effects の Time Remap・ToeiDigitalTimeSheet のコピー情報・
//!   XDTS／TDTS のどれかとして読む（`parse_text`）。
//! - 読んだ結果を、レイヤーへ紐づけられる列の一覧にし（`sheet_columns_from_parsed`）、
//!   既定の紐づけを決め（`default_layer_bindings`）、使う行だけを残す（`parsed_result`）。
//! - 選択レイヤーか紐づけたレイヤーへ当てる前の確かめ（`apply_to_active_layer`・
//!   `apply_xdts_layer_bindings` の前半）。当てるのは `Document::apply_sheet_remap`。
//!
//! 文言は Qt の翻訳前の文字列と差し込みの値（[`Text`]）で返し、画面が訳す。

use super::py;
use super::timesheet::{self, ColumnGroup, SheetColumn, TDTS_SIGNATURE, XDTS_SIGNATURE};
use crate::document::model::{CellNumber, Document};
use crate::image::RgbaImage;
use crate::project::json::{self, Json};

/// 翻訳前の文言と差し込みの値（Qt の `tr(template).format(**values)`）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Text {
    /// 翻訳前の文言（`{name}` の形の差し込みを含む）。
    pub template: String,
    /// 差し込みの値。
    pub values: Vec<(String, String)>,
}

impl Text {
    /// 差し込みのない文言。
    pub fn new(template: impl Into<String>) -> Self {
        Self {
            template: template.into(),
            values: Vec::new(),
        }
    }

    /// 差し込みを1つ足す。
    pub fn with(mut self, key: &str, value: impl ToString) -> Self {
        self.values.push((key.into(), value.to_string()));
        self
    }

    /// 訳さずに埋めた文字列。
    pub fn format(&self) -> String {
        let mut out = self.template.clone();
        for (key, value) in &self.values {
            out = out.replace(&format!("{{{key}}}"), value);
        }
        out
    }
}

impl std::fmt::Display for Text {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.format())
    }
}

/// 読んだタイムシート（Python の parse_text の dict）。
#[derive(Clone, Debug, PartialEq)]
pub struct RemapSheet {
    /// 形式の表示名（"Adobe After Effects"・"ToeiDigitalTimeSheet"・"XDTS version 5" など）。
    pub format: String,
    /// After Effects の Units Per Second（ほかは None）。
    pub fps: Option<f64>,
    /// 最初のコマ（0 始まり）。
    pub start_frame: usize,
    /// 最後のコマ（0 始まり）。
    pub end_frame: usize,
    /// コマごとの絵番号（None は空フレーム）。
    pub states: Vec<Option<i64>>,
    /// 中割・記号ラベルの数。
    pub blank_label_count: usize,
    /// XDTS のセル欄の列。
    pub tracks: Vec<SheetColumn>,
    /// XDTS の表示する全部の列。
    pub sheet_columns: Vec<SheetColumn>,
}

impl RemapSheet {
    fn single(format: &str, fps: Option<f64>, start: usize, end: usize) -> Self {
        Self {
            format: format.into(),
            fps,
            start_frame: start,
            end_frame: end,
            states: Vec::new(),
            blank_label_count: 0,
            tracks: Vec::new(),
            sheet_columns: Vec::new(),
        }
    }
}

/// 読めるコマ数の上限。Python に上限はない（大きすぎるとメモリが尽きる）が、
/// 1 つのタイムシートとしてありえない長さは読まない。
pub const MAX_FRAMES: usize = 1 << 20;

fn too_long() -> Text {
    Text::new("解析したフレーム範囲が不正です。")
}

/// ToeiDigitalTimeSheet のコピー情報（`_parse_toei_timesheet`）。
pub fn parse_toei_timesheet(raw_text: &str) -> Result<RemapSheet, Text> {
    let text = py::strip(raw_text);
    let Some(start) = text.find('{') else {
        return Err(Text::new("JSONデータが見つかりません。"));
    };
    let payload = json::parse(&text[start..])
        .map_err(|e| {
            Text::new("ToeiDigitalTimeSheetのJSONを解析できません。\n{exc}").with("exc", e)
        })?
        .value;
    let layers = match payload.get("layers") {
        Some(Json::Array(layers)) if !layers.is_empty() => layers,
        _ => return Err(Text::new("layersデータが見つかりません。")),
    };
    let selected = layers
        .iter()
        .find_map(|layer| match py::get(layer, "frames") {
            Some(Json::Array(frames)) if !frames.is_empty() => Some(frames),
            _ => None,
        });
    let Some(frames) = selected else {
        return Err(Text::new("framesデータが見つかりません。"));
    };
    // コマ → 最初の空でない値（後に書かれたコマが勝つ）。
    let mut entries: std::collections::BTreeMap<i64, Option<String>> = Default::default();
    for entry in frames {
        if !matches!(entry, Json::Object(_)) {
            continue;
        }
        let Some(frame) = entry.get("frame").and_then(py::py_int) else {
            continue;
        };
        if frame < 0 {
            continue;
        }
        let mut values = Vec::new();
        if let Some(Json::Array(items)) = entry.get("data") {
            for item in items {
                if !matches!(item, Json::Object(_)) {
                    continue;
                }
                match item.get("values") {
                    None => {}
                    Some(Json::Array(list)) => values.extend(list.iter().cloned()),
                    Some(Json::Null) => {}
                    Some(Json::Str(s)) if s.is_empty() => {}
                    Some(value) => values.push(value.clone()),
                }
            }
        }
        let token = values
            .iter()
            .map(|v| py::strip(&py::py_str(v)).to_string())
            .find(|s| !s.is_empty());
        entries.insert(frame, token);
    }
    let (Some(&first), Some(&last)) = (entries.keys().next(), entries.keys().next_back()) else {
        return Err(Text::new(
            "有効なToeiDigitalTimeSheetフレームがありません。",
        ));
    };
    if (last - first) as u64 >= MAX_FRAMES as u64 {
        return Err(too_long());
    }
    let mut sheet = RemapSheet::single("ToeiDigitalTimeSheet", None, first as usize, last as usize);
    let mut current = None;
    for frame in first..=last {
        if let Some(token) = entries.get(&frame) {
            match token {
                None => {}
                // Python の re.fullmatch(r"[0-9]+")：ASCII の数字だけ。
                Some(token) if !token.is_empty() && token.bytes().all(|b| b.is_ascii_digit()) => {
                    current = Some(parse_digits(token).max(1));
                }
                Some(_) => {
                    current = None;
                    sheet.blank_label_count += 1;
                }
            }
        }
        sheet.states.push(current);
    }
    Ok(sheet)
}

/// ASCII の数字の並び。i64 を超えれば i64::MAX（Python は桁の上限がない）。
fn parse_digits(token: &str) -> i64 {
    token.parse().unwrap_or(i64::MAX)
}

/// Python の `str.splitlines()`。
fn splitlines(text: &str) -> Vec<&str> {
    let breaks = [
        '\n', '\r', '\u{0b}', '\u{0c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}',
        '\u{2029}',
    ];
    let mut out = Vec::new();
    let mut rest = text;
    while !rest.is_empty() {
        match rest.find(|c| breaks.contains(&c)) {
            Some(at) => {
                out.push(&rest[..at]);
                let c = rest[at..].chars().next().unwrap();
                let mut next = at + c.len_utf8();
                if c == '\r' && rest[next..].starts_with('\n') {
                    next += 1;
                }
                rest = &rest[next..];
            }
            None => {
                out.push(rest);
                break;
            }
        }
    }
    out
}

/// 数字（Python の `\d` のうち ASCII と全角）を ASCII にした文字列。
fn ascii_digits(s: &str) -> String {
    s.chars()
        .map(|c| match py::decimal_value(c) {
            Some(d) => char::from(b'0' + d as u8),
            None => c,
        })
        .collect()
}

/// `\d+` の長さ（バイト）。
fn digits_len(s: &str) -> usize {
    s.char_indices()
        .find(|(_, c)| !py::is_decimal(*c))
        .map_or(s.len(), |(i, _)| i)
}

fn spaces_len(s: &str) -> usize {
    s.char_indices()
        .find(|(_, c)| !py::is_space(*c))
        .map_or(s.len(), |(i, _)| i)
}

/// 行の頭の `^\s*(-?\d+(?:\.\d+)?)\s+(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)`。
fn key_line(line: &str) -> Option<(String, String)> {
    let mut at = spaces_len(line);
    let first_start = at;
    if line[at..].starts_with('-') {
        at += 1;
    }
    let n = digits_len(&line[at..]);
    if n == 0 {
        return None;
    }
    at += n;
    if line[at..].starts_with('.') {
        let m = digits_len(&line[at + 1..]);
        if m > 0 {
            at += 1 + m;
        }
    }
    let first = &line[first_start..at];
    let gap = spaces_len(&line[at..]);
    if gap == 0 {
        return None;
    }
    at += gap;
    let second_start = at;
    if line[at..].starts_with('-') {
        at += 1;
    }
    let n = digits_len(&line[at..]);
    if n > 0 {
        at += n;
        if line[at..].starts_with('.') {
            at += 1 + digits_len(&line[at + 1..]);
        }
    } else if line[at..].starts_with('.') && digits_len(&line[at + 1..]) > 0 {
        at += 1 + digits_len(&line[at + 1..]);
    } else {
        return None;
    }
    if line[at..].starts_with(['e', 'E']) {
        let mut exp = at + 1;
        if line[exp..].starts_with(['+', '-']) {
            exp += 1;
        }
        let n = digits_len(&line[exp..]);
        if n > 0 {
            at = exp + n;
        }
    }
    Some((ascii_digits(first), ascii_digits(&line[second_start..at])))
}

/// `Units\s+Per\s+Second\s+([0-9]+(?:\.[0-9]+)?)`（大文字小文字を区別しない）。
fn units_per_second(text: &str) -> Option<f64> {
    let lower: Vec<(usize, char)> = text.char_indices().collect();
    'start: for start in 0..lower.len() {
        let mut i = start;
        for (index, word) in ["units", "per", "second"].iter().enumerate() {
            if index > 0 {
                let from = i;
                while i < lower.len() && py::is_space(lower[i].1) {
                    i += 1;
                }
                if i == from {
                    continue 'start;
                }
            }
            for expected in word.chars() {
                match lower.get(i) {
                    Some((_, c)) if c.to_ascii_lowercase() == expected => i += 1,
                    _ => continue 'start,
                }
            }
        }
        let from = i;
        while i < lower.len() && py::is_space(lower[i].1) {
            i += 1;
        }
        if i == from {
            continue;
        }
        let begin = i;
        while i < lower.len() && lower[i].1.is_ascii_digit() {
            i += 1;
        }
        if i == begin {
            continue;
        }
        if i + 1 < lower.len() && lower[i].1 == '.' && lower[i + 1].1.is_ascii_digit() {
            i += 1;
            while i < lower.len() && lower[i].1.is_ascii_digit() {
                i += 1;
            }
        }
        let end = lower.get(i).map_or(text.len(), |(at, _)| *at);
        return text[lower[begin].0..end].parse().ok();
    }
    None
}

/// After Effects の Time Remap のキーフレームデータ（`_parse_after_effects_time_remap`）。
pub fn parse_after_effects_time_remap(raw_text: &str) -> Result<RemapSheet, Text> {
    let text = raw_text.replace('\r', "");
    let fps = units_per_second(&text).filter(|f| *f > 0.0).unwrap_or(24.0);
    #[derive(PartialEq)]
    enum Section {
        Time,
        Opacity,
    }
    let mut section = None;
    let mut time_map = std::collections::BTreeMap::new();
    let mut opacity_map = std::collections::BTreeMap::new();
    for line in splitlines(&text) {
        let lowered = py::strip(line).to_lowercase();
        if lowered.starts_with("time remap") {
            section = Some(Section::Time);
            continue;
        }
        if lowered.starts_with("transform") && lowered.contains("opacity") {
            section = Some(Section::Opacity);
            continue;
        }
        if lowered.starts_with("end of keyframe data") {
            section = None;
            continue;
        }
        let (Some((frame, value)), Some(target)) = (key_line(line), section.as_ref()) else {
            continue;
        };
        let (Ok(frame), Ok(value)) = (frame.parse::<f64>(), value.parse::<f64>()) else {
            continue;
        };
        let frame = frame.round_ties_even();
        if frame < 0.0 {
            continue;
        }
        if frame >= MAX_FRAMES as f64 {
            return Err(too_long());
        }
        let frame = frame as i64;
        if *target == Section::Time {
            time_map.insert(frame, value);
        } else {
            opacity_map.insert(frame, value);
        }
    }
    let time: Vec<(i64, f64)> = time_map.into_iter().collect();
    let opacity: Vec<(i64, f64)> = opacity_map.into_iter().collect();
    if time.is_empty() {
        return Err(Text::new(
            "Time RemapのFrame／secondsデータが見つかりません。",
        ));
    }
    let frames = time.iter().chain(&opacity).map(|(f, _)| *f);
    let start = frames.clone().min().unwrap_or(0).max(0);
    let end = frames.max().unwrap_or(0);
    let mut sheet = RemapSheet::single(
        "Adobe After Effects",
        Some(fps),
        start as usize,
        end as usize,
    );
    let (mut time_index, mut opacity_index) = (0, 0);
    let mut seconds = time[0].1;
    let mut current_opacity = opacity.first().map_or(100.0, |(_, v)| *v);
    for frame in start..=end {
        while time_index + 1 < time.len() && time[time_index + 1].0 <= frame {
            time_index += 1;
            seconds = time[time_index].1;
        }
        while opacity_index + 1 < opacity.len() && opacity[opacity_index + 1].0 <= frame {
            opacity_index += 1;
            current_opacity = opacity[opacity_index].1;
        }
        if seconds < 0.0 || current_opacity <= 0.0 {
            sheet.states.push(None);
        } else {
            // AE の 0 秒は連番 1 番、1/fps 秒は連番 2 番。
            let index = (seconds * fps + 0.5).floor();
            if !index.is_finite() {
                return Err(too_long());
            }
            let index = (index as i64).saturating_add(1);
            sheet.states.push(Some(index.max(1)));
        }
    }
    Ok(sheet)
}

fn xdts(raw_text: &str) -> Result<RemapSheet, Text> {
    let parsed = timesheet::parse_timesheet(raw_text).map_err(|e| match e {
        timesheet::SheetError::Json(reason) => {
            Text::new("タイムシートのJSONを解析できません。\n{exc}").with("exc", reason)
        }
        other => Text::new(other.to_string()),
    })?;
    Ok(RemapSheet {
        format: parsed.format,
        fps: None,
        start_frame: 0,
        end_frame: parsed.end_frame,
        states: parsed.states,
        blank_label_count: parsed.blank_label_count,
        tracks: parsed.tracks,
        sheet_columns: parsed.sheet_columns,
    })
}

/// 貼り付けた文字の形式を見分けて読む（`TimeRemapController.parse_text`）。
pub fn parse_text(raw_text: &str) -> Result<RemapSheet, Text> {
    let text = py::strip(raw_text.trim_start_matches('\u{feff}'));
    if text.is_empty() {
        return Err(Text::new("貼り付けデータが空です。"));
    }
    let lowered = text.to_lowercase();
    if text.starts_with(XDTS_SIGNATURE) || text.starts_with(TDTS_SIGNATURE) {
        return xdts(text);
    }
    if lowered.contains("toeidigitaltimesheet copy data")
        || (text.starts_with('{') && text.contains("\"layers\"") && text.contains("\"frames\""))
    {
        return parse_toei_timesheet(text);
    }
    if lowered.contains("adobe after effects") || lowered.contains("time remap") {
        return parse_after_effects_time_remap(text);
    }
    Err(Text::new(
        "Adobe After Effects、ToeiDigitalTimeSheet、XDTS形式を判別できませんでした。",
    ))
}

/// 1つしか列のない形式の列の表示（`×` か番号）。
fn display_of(states: &[Option<i64>]) -> Vec<String> {
    states
        .iter()
        .map(|s| s.map_or("×".into(), |n| n.to_string()))
        .collect()
}

/// レイヤーへ紐づけられる列の一覧（`sheet_columns_from_parsed`）。XDTS は列をそのまま、
/// 1列だけの形式（After Effects・東映）は「セル」の1列。`cell_name` は「セル」の訳。
pub fn sheet_columns_from_parsed(parsed: &RemapSheet, cell_name: &str) -> Vec<SheetColumn> {
    if !parsed.sheet_columns.is_empty() {
        return parsed.sheet_columns.clone();
    }
    if !parsed.tracks.is_empty() {
        return parsed
            .tracks
            .iter()
            .map(|track| SheetColumn {
                uid: format!("cell:{}", track.track_no),
                field_id: 0,
                group: ColumnGroup::Cell,
                bindable: true,
                display_values: display_of(&track.states),
                ..track.clone()
            })
            .collect();
    }
    if parsed.states.is_empty() {
        return Vec::new();
    }
    vec![SheetColumn {
        uid: "cell:0".into(),
        field_id: 0,
        track_no: 0,
        name: cell_name.into(),
        group: ColumnGroup::Cell,
        states: parsed.states.clone(),
        display_values: display_of(&parsed.states),
        blank_label_count: parsed.blank_label_count,
        bindable: true,
    }]
}

/// 列 → レイヤーの既定の紐づけ（`default_layer_bindings`）。列名と同じ名前のレイヤーへ、
/// 1つも合わなければ最初の列を選択中のレイヤーへ。`layers` は (レイヤー番号, 名前)。
pub fn default_layer_bindings(
    columns: &[SheetColumn],
    layers: &[(usize, String)],
    active_layer: usize,
) -> Vec<(String, usize)> {
    let mut bindings: Vec<(String, usize)> = Vec::new();
    let mut used: Vec<usize> = Vec::new();
    let bindable: Vec<&SheetColumn> = columns.iter().filter(|c| c.bindable).collect();
    for column in &bindable {
        let name = py::casefold(py::strip(&column.name));
        let matched = layers
            .iter()
            .find(|(index, layer)| !used.contains(index) && py::casefold(py::strip(layer)) == name);
        if let Some((index, _)) = matched {
            used.push(*index);
            match bindings.iter_mut().find(|(uid, _)| *uid == column.uid) {
                Some(entry) => entry.1 = *index,
                None => bindings.push((column.uid.clone(), *index)),
            }
        }
    }
    if bindings.is_empty() && layers.iter().any(|(index, _)| *index == active_layer) {
        if let Some(first) = bindable.first() {
            bindings.push((first.uid.clone(), active_layer));
        }
    }
    bindings
}

/// 紐づけを1つ変える（`_set_column_layer_binding`）。同じレイヤーに紐づいていた別の列は外す。
pub fn set_layer_binding(bindings: &mut Vec<(String, usize)>, uid: &str, layer: Option<usize>) {
    match layer {
        None => bindings.retain(|(key, _)| key != uid),
        Some(layer) => {
            bindings.retain(|(key, index)| key == uid || *index != layer);
            match bindings.iter_mut().find(|(key, _)| key == uid) {
                Some(entry) => entry.1 = layer,
                None => bindings.push((uid.into(), layer)),
            }
        }
    }
}

/// 使う行だけを残した結果（`TimeRemapPasteDialog.parsed_result`）。使う行が無ければ None。
#[derive(Clone, Debug, PartialEq)]
pub struct RemapChoice {
    /// 元の形式名。
    pub format: String,
    /// 最初のコマ。
    pub start_frame: usize,
    /// 最後のコマ（start + 使う行の数 - 1）。
    pub end_frame: usize,
    /// 主の列の絵番号（使う行だけ）。
    pub states: Vec<Option<i64>>,
    /// 主の列の中割・記号ラベルの数（行を除く前の数。Qt と同じ）。
    pub blank_label_count: usize,
    /// 使う行だけにした列。
    pub sheet_columns: Vec<SheetColumn>,
    /// 列 uid → レイヤー番号（紐づけた順）。
    pub layer_bindings: Vec<(String, usize)>,
}

impl RemapChoice {
    /// XDTS の紐づけで当てるか（`format.startswith("XDTS") and sheet_columns`）。
    pub fn uses_bindings(&self) -> bool {
        self.format.starts_with("XDTS") && !self.sheet_columns.is_empty()
    }
}

/// 使う行（`included`、長さが列の長さと違えば全部使う）と紐づけで結果を作る。
pub fn parsed_result(
    parsed: &RemapSheet,
    cell_name: &str,
    included: &[bool],
    bindings: &[(String, usize)],
) -> Option<RemapChoice> {
    let columns = sheet_columns_from_parsed(parsed, cell_name);
    if columns.is_empty() {
        return None;
    }
    let duration = columns
        .iter()
        .map(|c| c.display_values.len())
        .max()
        .unwrap_or(0);
    let all = vec![true; duration];
    let included = if included.len() == duration {
        included
    } else {
        &all[..]
    };
    if !included.iter().any(|&keep| keep) {
        return None;
    }
    let keep = |index: usize| included.get(index).copied().unwrap_or(false);
    let filtered: Vec<SheetColumn> = columns
        .into_iter()
        .map(|column| SheetColumn {
            states: if column.states.is_empty() {
                column.states.clone()
            } else {
                column
                    .states
                    .iter()
                    .enumerate()
                    .filter(|(i, _)| keep(*i))
                    .map(|(_, s)| *s)
                    .collect()
            },
            display_values: column
                .display_values
                .iter()
                .enumerate()
                .filter(|(i, _)| keep(*i))
                .map(|(_, v)| v.clone())
                .collect(),
            ..column
        })
        .collect();
    let bound = |c: &&SheetColumn| bindings.iter().any(|(uid, _)| *uid == c.uid);
    let primary = filtered
        .iter()
        .find(|c| bound(c) && !c.states.is_empty())
        .or_else(|| filtered.iter().find(|c| !c.states.is_empty()));
    let (states, blank_label_count) = match primary {
        Some(column) => (column.states.clone(), column.blank_label_count),
        None => (parsed.states.clone(), parsed.blank_label_count),
    };
    let used = included.iter().filter(|&&keep| keep).count();
    Some(RemapChoice {
        format: parsed.format.clone(),
        start_frame: parsed.start_frame,
        end_frame: parsed.start_frame + used - 1,
        states,
        blank_label_count,
        sheet_columns: filtered,
        layer_bindings: bindings.to_vec(),
    })
}

/// 選択レイヤーの連番素材（`_time_remap_source_bank` の接続済みの列）：1 番から一番大きい
/// 番号までの絵。抜けた番号は None（参照されたら「存在しない絵番号」として止める）。
pub fn source_bank(document: &Document, layer: usize) -> Vec<Option<RgbaImage>> {
    let Some(track) = document
        .track_order()
        .get(layer)
        .and_then(|id| document.track(*id))
    else {
        return Vec::new();
    };
    let last = track.cells.keys().next_back().map_or(0, |n| n.get());
    (1..=last)
        .map(|n| {
            CellNumber::new(n)
                .ok()
                .and_then(|n| track.cells.get(&n))
                .map(|cell| cell.image.clone())
        })
        .collect()
}

/// 素材に絵の無い番号（範囲の外・抜け、`missing_numbers`）。
pub fn missing_numbers(bank: &[Option<RgbaImage>], numbers: &[i64]) -> Vec<i64> {
    numbers
        .iter()
        .copied()
        .filter(|&n| {
            n < 1
                || n as u64 > bank.len() as u64
                || bank.get(n as usize - 1).is_none_or(Option::is_none)
        })
        .collect()
}

fn preview(missing: &[i64]) -> String {
    let mut text = missing
        .iter()
        .take(12)
        .map(i64::to_string)
        .collect::<Vec<_>>()
        .join(", ");
    if missing.len() > 12 {
        text.push('…');
    }
    text
}

fn referenced(states: &[Option<i64>]) -> Vec<i64> {
    let mut numbers: Vec<i64> = states.iter().flatten().copied().collect();
    numbers.sort_unstable();
    numbers.dedup();
    numbers
}

/// 当てる1列。
#[derive(Clone, Debug)]
pub struct PlannedColumn {
    /// 当てるレイヤー。
    pub layer_index: usize,
    /// コマごとの絵番号（None は空キー）。
    pub states: Vec<Option<CellNumber>>,
    /// 1 番からの素材。
    pub bank: Vec<Option<RgbaImage>>,
}

/// 当てる前の確かめ（Qt の確かめの窓の題と文、当てたあとの知らせ）と、当てる中身。
#[derive(Clone, Debug)]
pub struct RemapPlan {
    /// 最初のコマ。
    pub start_frame: usize,
    /// 最後のコマ。
    pub end_frame: usize,
    /// 当てる列。
    pub columns: Vec<PlannedColumn>,
    /// 確かめの窓の題。
    pub title: Text,
    /// 確かめの窓の文（訳してからつなぐ部分の並び）。
    pub message: Vec<Text>,
    /// XDTS の紐づけの行（1行ずつ訳して改行でつなぎ、`message[0]` の `{links}` へ入れる）。
    pub links: Vec<Text>,
    /// 当てたあとのステータスの知らせ。
    pub status: Text,
}

fn numbers(states: &[Option<i64>]) -> Vec<Option<CellNumber>> {
    // 素材にある番号だけが残る（確かめのあと）ので、番号は 1 以上。
    states
        .iter()
        .map(|s| s.and_then(|n| CellNumber::new(n).ok()))
        .collect()
}

/// 選択レイヤーへ当てる前の確かめ（`apply_to_active_layer` の前半）。
pub fn plan_active_layer(
    document: &Document,
    choice: &RemapChoice,
    layer: usize,
) -> Result<RemapPlan, Text> {
    let (start, end, states) = (choice.start_frame, choice.end_frame, &choice.states);
    if states.is_empty() || end < start || states.len() != end - start + 1 {
        return Err(Text::new("解析したフレーム範囲が不正です。"));
    }
    if document.frame_order().is_empty() {
        return Err(Text::new("タイムラインがありません。"));
    }
    if layer >= document.track_order().len() {
        return Err(Text::new("対象レイヤーを選択してください。"));
    }
    let bank = source_bank(document, layer);
    if bank.is_empty() {
        return Err(Text::new(
            "選択レイヤーに連番画像がありません。\n先に画像連番を読み込んでください。",
        ));
    }
    let missing = missing_numbers(&bank, &referenced(states));
    if !missing.is_empty() {
        return Err(Text::new(
            "連番画像は{len}枚ですが、存在しない絵番号が参照されています。\n{preview}",
        )
        .with("len", bank.len())
        .with("preview", preview(&missing)));
    }
    let blanks = states.iter().filter(|s| s.is_none()).count();
    let mut message = vec![Text::new(
        "形式：{name}\n反映範囲：{value}～{value2}フレーム\n連番画像：{len}枚\n空フレーム：{count}フレーム",
    )
    .with("name", &choice.format)
    .with("value", start + 1)
    .with("value2", end + 1)
    .with("len", bank.len())
    .with("count", blanks)];
    if choice.blank_label_count > 0 {
        message.push(
            Text::new("\n中割・記号ラベル：{blanks}セル").with("blanks", choice.blank_label_count),
        );
    }
    // Qt はこの1文を tr に通していない。
    message.push(Text::new("\n\n選択レイヤーの対象範囲を置き換えます。"));
    Ok(RemapPlan {
        start_frame: start,
        end_frame: end,
        columns: vec![PlannedColumn {
            layer_index: layer,
            states: numbers(states),
            bank,
        }],
        title: Text::new("タイムリマップを反映"),
        message,
        links: Vec::new(),
        status: Text::new("{name}を{value}～{value2}フレームへ反映しました。")
            .with("name", &choice.format)
            .with("value", start + 1)
            .with("value2", end + 1),
    })
}

/// XDTS の紐づけで当てる前の確かめ（`apply_xdts_layer_bindings` の前半）。
/// `layer_names` は今のコマのレイヤー名。
pub fn plan_xdts_bindings(
    document: &Document,
    choice: &RemapChoice,
    layer_names: &[String],
) -> Result<RemapPlan, Text> {
    let (start, end) = (choice.start_frame, choice.end_frame);
    let duration = end as i64 - start as i64 + 1;
    if duration <= 0 || choice.layer_bindings.is_empty() {
        return Err(Text::new("読み込むCELLとレイヤーの紐づけがありません。"));
    }
    if document.frame_order().is_empty() {
        return Err(Text::new("タイムラインがありません。"));
    }
    let mut prepared: Vec<(&SheetColumn, PlannedColumn)> = Vec::new();
    for (uid, layer) in &choice.layer_bindings {
        // 同じ uid の列は後のものが勝つ（Python の dict）。
        let Some(column) = choice.sheet_columns.iter().rev().find(|c| c.uid == *uid) else {
            continue;
        };
        if !column.bindable {
            continue;
        }
        if column.states.len() as i64 != duration {
            return Err(
                Text::new("CELL「{get}」のフレーム数が不正です。").with("get", &column.name)
            );
        }
        let Some(layer_name) = layer_names.get(*layer) else {
            return Err(Text::new("CELL「{get}」の紐づけ先レイヤーがありません。")
                .with("get", &column.name));
        };
        let bank = source_bank(document, *layer);
        if bank.is_empty() {
            return Err(
                Text::new("レイヤー「{name}」に連番画像がありません。").with("name", layer_name)
            );
        }
        let missing = missing_numbers(&bank, &referenced(&column.states));
        if !missing.is_empty() {
            return Err(Text::new(
                "CELL「{get}」は存在しない絵番号を参照しています（レイヤー画像 {len}枚）。\n{preview}",
            )
            .with("get", &column.name)
            .with("len", bank.len())
            .with("preview", preview(&missing)));
        }
        prepared.push((
            column,
            PlannedColumn {
                layer_index: *layer,
                states: numbers(&column.states),
                bank,
            },
        ));
    }
    if prepared.is_empty() {
        return Err(Text::new("読み込めるCELLの紐づけがありません。"));
    }
    let message = vec![Text::new(
        "反映範囲：{value}～{value2}フレーム\n紐づけ：\n{links}\n\n紐づけたレイヤーの対象範囲を置き換えます。",
    )
    .with("value", start + 1)
    .with("value2", end + 1)];
    let mut links = Vec::new();
    for (column, planned) in &prepared {
        links.push(
            Text::new("・{get} → {name}")
                .with("get", &column.name)
                .with("name", &layer_names[planned.layer_index]),
        );
    }
    Ok(RemapPlan {
        start_frame: start,
        end_frame: end,
        title: Text::new("XDTSタイムシートを反映"),
        message,
        links,
        status: Text::new("XDTSの{len}個のCELLを{value}～{value2}フレームへ反映しました。")
            .with("len", prepared.len())
            .with("value", start + 1)
            .with("value2", end + 1),
        columns: prepared.into_iter().map(|(_, c)| c).collect(),
    })
}

/// 確かめた中身を文書へ当てる（`Document::apply_sheet_remap`）。
pub fn apply_plan(
    document: &mut Document,
    plan: &RemapPlan,
    current_frame: usize,
) -> Result<crate::document::changes::ChangeSet, crate::Error> {
    let columns: Vec<_> = plan
        .columns
        .iter()
        .map(|c| crate::document::frame_commands::RemapColumn {
            layer_index: c.layer_index,
            states: &c.states,
            bank: &c.bank,
        })
        .collect();
    document.apply_sheet_remap(
        plan.start_frame..plan.end_frame + 1,
        &columns,
        current_frame,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn after_effects_keys_hold_until_the_next_key() {
        let text = "Adobe After Effects 8.0 Keyframe Data\n\n\tUnits Per Second\t12\n\nTime Remap\n\tFrame\tseconds\n\t0\t0\n\t2\t0.25\n\nTransform\tOpacity\n\tFrame\tpercent\n\t0\t100\n\t3\t0\n\nEnd of Keyframe Data\n";
        let sheet = parse_text(text).unwrap();
        assert_eq!(sheet.format, "Adobe After Effects");
        assert_eq!(sheet.fps, Some(12.0));
        assert_eq!((sheet.start_frame, sheet.end_frame), (0, 3));
        assert_eq!(sheet.states, [Some(1), Some(1), Some(4), None]);
    }

    #[test]
    fn toei_labels_become_blank_frames() {
        let text = r#"ToeiDigitalTimeSheet Copy Data {"layers":[{"frames":[]},{"frames":[{"frame":1,"data":[{"values":["2"]}]},{"frame":3,"data":[{"values":["中"]}]},{"frame":4,"data":[{"values":[""]}]}]}]}"#;
        let sheet = parse_text(text).unwrap();
        assert_eq!((sheet.start_frame, sheet.end_frame), (1, 4));
        assert_eq!(sheet.states, [Some(2), Some(2), None, None]);
        assert_eq!(sheet.blank_label_count, 1);
        let columns = sheet_columns_from_parsed(&sheet, "セル");
        assert_eq!(columns[0].display_values, ["2", "2", "×", "×"]);
        let choice = parsed_result(&sheet, "セル", &[true, false, true, true], &[]).unwrap();
        assert_eq!(choice.states, [Some(2), None, None]);
        assert_eq!(choice.end_frame, 3);
    }

    #[test]
    fn unknown_text_is_rejected() {
        assert_eq!(
            parse_text("  ").unwrap_err().template,
            "貼り付けデータが空です。"
        );
        assert!(parse_text("hello").is_err());
    }

    #[test]
    fn bindings_prefer_matching_names_then_the_active_layer() {
        let column = |uid: &str, name: &str| SheetColumn {
            uid: uid.into(),
            field_id: 0,
            track_no: 0,
            name: name.into(),
            group: ColumnGroup::Cell,
            states: vec![Some(1)],
            display_values: vec!["1".into()],
            blank_label_count: 0,
            bindable: true,
        };
        let layers = vec![(0, "A".to_string()), (1, "b".to_string())];
        let columns = [column("x", "B"), column("y", "C")];
        assert_eq!(
            default_layer_bindings(&columns, &layers, 0),
            [("x".to_string(), 1)]
        );
        assert_eq!(
            default_layer_bindings(&columns[1..], &layers, 0),
            [("y".to_string(), 0)]
        );
        let mut bindings = vec![("x".to_string(), 1)];
        set_layer_binding(&mut bindings, "y", Some(1));
        assert_eq!(bindings, [("y".to_string(), 1)]);
    }
}
