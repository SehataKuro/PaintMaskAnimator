//! タイムシートのファイル（XDTS・TDTS）の書き出しと読み込み（`timesheet_file.py`）。
//!
//! どちらも1行の識別文字列と JSON。トラックは指示が変わるコマだけを並べ、
//! 書いていないコマは前の指示が続く。シートは `frame == duration` の
//! `SYMBOL_NULL_CELL` で終わる。文字列は Python の json.dumps と同じ書式で書く。

use crate::project::json::{self, Json};

use super::cut_folder::{SheetFormat, SheetHeader};
use super::py::{self, int, object, string};

/// XDTS の識別文字列。
pub const XDTS_SIGNATURE: &str = "exchangeDigitalTimeSheet Save Data";
/// TDTS の識別文字列。
pub const TDTS_SIGNATURE: &str = "toeiDigitalTimeSheet Save Data";
/// XDTS の版。
pub const XDTS_VERSION: i64 = 5;
/// TDTS の版。
pub const TDTS_VERSION: i64 = 11;
/// 空セル（×）。
pub const NULL_CELL: &str = "SYMBOL_NULL_CELL";
/// 前のコマの続き（｜）。
pub const HYPHEN: &str = "SYMBOL_HYPHEN";
/// タイムシートの拡張子（`.xtds` はよくある綴りの誤り）。
pub const SUFFIXES: [&str; 3] = [".xdts", ".xtds", ".tdts"];

fn frames(values: &[String], sparse: bool) -> Json {
    let mut out = Vec::new();
    let mut previous: Option<&str> = None;
    for (frame, value) in values.iter().enumerate() {
        if sparse && (value == HYPHEN || (value == NULL_CELL && previous == Some(NULL_CELL))) {
            continue;
        }
        let entry = if sparse {
            object([
                ("fontColorId", int(0)),
                ("id", int(0)),
                ("values", Json::Array(vec![string(value.clone())])),
            ])
        } else {
            object([
                ("id", int(0)),
                ("values", Json::Array(vec![string(value.clone())])),
            ])
        };
        out.push(object([
            ("frame", int(frame as i64)),
            ("data", Json::Array(vec![entry])),
        ]));
        if value != HYPHEN {
            previous = Some(value);
        }
    }
    if sparse && previous != Some(NULL_CELL) {
        out.push(object([
            ("frame", int(values.len() as i64)),
            (
                "data",
                Json::Array(vec![object([
                    ("fontColorId", int(0)),
                    ("id", int(0)),
                    ("values", Json::Array(vec![string(NULL_CELL)])),
                ])]),
            ),
        ]));
    }
    Json::Array(out)
}

fn names(layer_names: &[String]) -> Json {
    Json::Array(layer_names.iter().cloned().map(Json::Str).collect())
}

/// XDTS v5 の文字列（`xdts_text`）。tracks はレイヤーごとにコマごとの指示を持つ。
pub fn xdts_text(
    name: &str,
    layer_names: &[String],
    tracks: &[Vec<String>],
    duration: usize,
) -> String {
    let payload = object([
        (
            "timeTables",
            Json::Array(vec![object([
                ("duration", int(duration as i64)),
                ("name", string(name)),
                (
                    "timeTableHeaders",
                    Json::Array(vec![object([
                        ("fieldId", int(0)),
                        ("names", names(layer_names)),
                    ])]),
                ),
                (
                    "fields",
                    Json::Array(vec![object([
                        ("fieldId", int(0)),
                        (
                            "tracks",
                            Json::Array(
                                tracks
                                    .iter()
                                    .enumerate()
                                    .map(|(index, values)| {
                                        object([
                                            ("trackNo", int(index as i64)),
                                            ("frames", frames(values, false)),
                                        ])
                                    })
                                    .collect(),
                            ),
                        ),
                    ])]),
                ),
            ])]),
        ),
        ("version", int(XDTS_VERSION)),
    ]);
    format!(
        "{XDTS_SIGNATURE}\n{}\n",
        payload.dumps(Some(2)).unwrap_or_default()
    )
}

const FONT_COLORS: [[i64; 3]; 6] = [
    [0, 0, 0],
    [224, 0, 0],
    [32, 128, 32],
    [32, 32, 192],
    [192, 32, 192],
    [255, 128, 32],
];
const DUMMY_KOMAS: i64 = 24;

fn work_slip() -> Json {
    let process = || {
        object([
            ("assistant", string("")),
            ("chief", string("")),
            ("director", string("")),
            ("free", string("")),
            ("freeRole", string("")),
        ])
    };
    let track = || object([("artist", string("")), ("numberOfSheets", int(0))]);
    object([
        ("memo", string("")),
        (
            "processes",
            Json::Array((0..3).map(|_| process()).collect()),
        ),
        (
            "works",
            Json::Array(
                (0..2)
                    .map(|_| {
                        object([
                            ("enable", Json::Bool(false)),
                            ("tracks", Json::Array((0..7).map(|_| track()).collect())),
                        ])
                    })
                    .collect(),
            ),
        ),
    ])
}

fn default_headers() -> Vec<Json> {
    let header = |id: i64, names: &[&str]| {
        object([
            ("fieldId", int(id)),
            (
                "names",
                Json::Array(names.iter().map(|n| string(*n)).collect()),
            ),
        ])
    };
    vec![
        header(1, &[""]),
        header(2, &[""]),
        header(3, &["S1", "S2"]),
        header(4, &["a", "b", "c", "d", "e", "f", "g"]),
        header(5, &["1", "2"]),
    ]
}

/// TDTS v11 の文字列（東映のアプリが保存する並び、`tdts_text`）。
pub fn tdts_text(
    name: &str,
    layer_names: &[String],
    tracks: &[Vec<String>],
    duration: usize,
    header: &SheetHeader,
) -> String {
    let mut headers = vec![object([("fieldId", int(0)), ("names", names(layer_names))])];
    headers.extend(default_headers());
    let payload = object([
        (
            "timeSheets",
            Json::Array(vec![object([
                ("free", Json::Array(vec![])),
                (
                    "header",
                    object([
                        ("cut", string(header.cut.clone())),
                        ("episode", string(header.episode.clone())),
                        ("scene", string(header.scene.clone())),
                        ("showHeadDummy", Json::Bool(false)),
                        (
                            "timeTableFontColors",
                            Json::Array(
                                FONT_COLORS
                                    .iter()
                                    .map(|c| Json::Array(c.iter().map(|&v| int(v)).collect()))
                                    .collect(),
                            ),
                        ),
                        ("workSlip", work_slip()),
                    ]),
                ),
                (
                    "timeTables",
                    Json::Array(vec![object([
                        ("color", int(0)),
                        ("direction", string("")),
                        ("duration", int(duration as i64)),
                        (
                            "fields",
                            Json::Array(vec![object([
                                ("fieldId", int(0)),
                                (
                                    "tracks",
                                    Json::Array(
                                        tracks
                                            .iter()
                                            .enumerate()
                                            .map(|(index, values)| {
                                                object([
                                                    ("frames", frames(values, true)),
                                                    ("trackNo", int(index as i64)),
                                                ])
                                            })
                                            .collect(),
                                    ),
                                ),
                            ])]),
                        ),
                        ("footDummykomas", int(DUMMY_KOMAS)),
                        ("headDummykomas", int(DUMMY_KOMAS)),
                        ("name", string(name)),
                        ("operatorName", string("")),
                        ("timeTableHeaders", Json::Array(headers)),
                    ])]),
                ),
            ])]),
        ),
        ("version", int(TDTS_VERSION)),
    ]);
    format!(
        "{TDTS_SIGNATURE}\n{}\n",
        payload.dumps(Some(4)).unwrap_or_default()
    )
}

/// 形式を選んだタイムシートの文字列（`sheet_text`）。header は TDTS だけが使う。
pub fn sheet_text(
    format: SheetFormat,
    name: &str,
    layer_names: &[String],
    tracks: &[Vec<String>],
    duration: usize,
    header: &SheetHeader,
) -> String {
    match format {
        SheetFormat::Tdts => tdts_text(name, layer_names, tracks, duration, header),
        SheetFormat::Xdts => xdts_text(name, layer_names, tracks, duration),
    }
}

/// タイムシートを読めない理由。文言は Python の翻訳前の文字列と同じ。
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SheetError {
    /// 識別文字列が違う。
    Signature,
    /// JSON を解析できない（理由付き）。
    Json(String),
    /// タイムシートの情報がない。
    NoTable,
    /// XDTS の版が 5 でない。
    Version,
    /// セル欄（fieldId 0）がない。
    NoCellField,
}

impl std::fmt::Display for SheetError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SheetError::Signature => write!(f, "XDTS／TDTSの先頭識別文字列が一致しません。"),
            SheetError::Json(reason) => write!(f, "タイムシートのJSONを解析できません。\n{reason}"),
            SheetError::NoTable => write!(f, "タイムシート情報がありません。"),
            SheetError::Version => write!(f, "対応しているXDTSバージョンは5です。"),
            SheetError::NoCellField => write!(f, "XDTSにセル欄（fieldId 0）がありません。"),
        }
    }
}

impl std::error::Error for SheetError {}

/// XDTS か TDTS の最初のタイムテーブル（`load_time_table`）。
pub fn load_time_table(raw_text: &str) -> Result<Json, SheetError> {
    let text = raw_text.trim_start_matches('\u{feff}');
    let (signature, body) = text.split_once('\n').unwrap_or((text, ""));
    let signature = py::strip(signature);
    if signature != XDTS_SIGNATURE && signature != TDTS_SIGNATURE {
        return Err(SheetError::Signature);
    }
    let payload = json::parse(body)
        .map_err(|error| SheetError::Json(error.to_string()))?
        .value;
    if !matches!(payload, Json::Object(_)) {
        return Err(SheetError::NoTable);
    }
    let tables = if signature == XDTS_SIGNATURE {
        let version = payload.get("version").map_or(Some(-1), py::py_int);
        if version != Some(XDTS_VERSION) {
            return Err(SheetError::Version);
        }
        payload.get("timeTables").cloned()
    } else {
        match payload.get("timeSheets") {
            Some(Json::Array(sheets)) => match sheets.first() {
                Some(sheet @ Json::Object(_)) => sheet.get("timeTables").cloned(),
                _ => None,
            },
            _ => None,
        }
    };
    match tables {
        Some(Json::Array(tables)) => match tables.into_iter().next() {
            Some(table @ Json::Object(_)) => Ok(table),
            _ => Err(SheetError::NoTable),
        },
        _ => Err(SheetError::NoTable),
    }
}

/// 表示用の形式名（`format_label`）。
pub fn format_label(raw_text: &str) -> String {
    if raw_text
        .trim_start_matches('\u{feff}')
        .starts_with(TDTS_SIGNATURE)
    {
        format!("TDTS version {TDTS_VERSION}")
    } else {
        format!("XDTS version {XDTS_VERSION}")
    }
}

/// 列の種類（`column_group`）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ColumnGroup {
    /// アクション欄・メモ。
    Action,
    /// セル欄。
    Cell,
    /// カメラ欄。
    Cam,
}

impl ColumnGroup {
    /// Python の文字列。
    pub fn id(self) -> &'static str {
        match self {
            ColumnGroup::Action => "ACTION",
            ColumnGroup::Cell => "CELL",
            ColumnGroup::Cam => "CAM",
        }
    }

    fn order(self) -> u8 {
        match self {
            ColumnGroup::Action => 0,
            ColumnGroup::Cell => 1,
            ColumnGroup::Cam => 2,
        }
    }
}

/// 読み込んだタイムシートの1列（`_parse_xdts_timesheet` の column）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SheetColumn {
    /// `fieldId:fieldの位置:trackNo`。
    pub uid: String,
    /// fieldId。
    pub field_id: i64,
    /// trackNo。
    pub track_no: i64,
    /// 列名。
    pub name: String,
    /// 列の種類。
    pub group: ColumnGroup,
    /// コマごとの絵番号（セル欄だけ。None は空セル）。
    pub states: Vec<Option<i64>>,
    /// コマごとの表示文字列。
    pub display_values: Vec<String>,
    /// 数字でない指示の数。
    pub blank_label_count: usize,
    /// レイヤーに結び付けられる列か。
    pub bindable: bool,
}

/// 読み込んだタイムシート（`_parse_xdts_timesheet` の戻り値）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ParsedSheet {
    /// 形式の表示名。
    pub format: String,
    /// 最後のコマ（0 始まり、duration - 1）。
    pub end_frame: usize,
    /// セル欄の最初の列の絵番号。
    pub states: Vec<Option<i64>>,
    /// セル欄の最初の列の数字でない指示の数。
    pub blank_label_count: usize,
    /// セル欄（fieldId 0）の列。
    pub tracks: Vec<SheetColumn>,
    /// 表示する全部の列（アクション・セル・カメラの順）。
    pub sheet_columns: Vec<SheetColumn>,
}

fn column_group(field_id: i64, name: &str) -> ColumnGroup {
    if field_id == 3 {
        return ColumnGroup::Action;
    }
    if field_id == 5 {
        return ColumnGroup::Cam;
    }
    let normalized = py::casefold(py::strip(name));
    let words = ["memo", "action", "act", "camera", "cam", "pan"];
    if normalized.starts_with(['_', '◆', '-'])
        || normalized == "ts"
        || normalized == "タイムシート"
        || words.iter().any(|w| normalized.contains(w))
    {
        ColumnGroup::Action
    } else {
        ColumnGroup::Cell
    }
}

fn int_or(value: Option<&Json>, default: i64) -> i64 {
    value.and_then(py::py_int).unwrap_or(default)
}

/// XDTS・TDTS の文字列をタイムシートの列にする（`ImportController._parse_xdts_timesheet`）。
pub fn parse_timesheet(raw_text: &str) -> Result<ParsedSheet, SheetError> {
    let table = load_time_table(raw_text)?;
    let duration = int_or(table.get("duration"), 1).max(1) as usize;
    let mut headers: Vec<(i64, Vec<Json>)> = Vec::new();
    if let Some(Json::Array(items)) = table.get("timeTableHeaders") {
        for item in items {
            if !matches!(item, Json::Object(_)) {
                continue;
            }
            let field_id = int_or(item.get("fieldId"), -1);
            if headers.iter().any(|(id, _)| *id == field_id) {
                continue;
            }
            let names = match item.get("names") {
                Some(Json::Array(names)) => names.clone(),
                Some(Json::Str(s)) => s.chars().map(|c| Json::Str(c.to_string())).collect(),
                _ => Vec::new(),
            };
            headers.push((field_id, names));
        }
    }
    let symbol = |token: &str| match token {
        "SYMBOL_HYPHEN" => Some("｜"),
        "SYMBOL_NULL_CELL" => Some("×"),
        "SYMBOL_TICK_1" => Some("○"),
        "SYMBOL_TICK_2" => Some("●"),
        _ => None,
    };
    let mut sheet_columns = Vec::new();
    let mut parsed_tracks = Vec::new();
    let fields = match table.get("fields") {
        Some(Json::Array(fields)) => fields.as_slice(),
        _ => &[],
    };
    for (field_index, field) in fields.iter().enumerate() {
        if !matches!(field, Json::Object(_)) {
            continue;
        }
        let field_id = int_or(field.get("fieldId"), -1);
        if ![0, 3, 5].contains(&field_id) {
            continue;
        }
        let names: &[Json] = headers
            .iter()
            .find(|(id, _)| *id == field_id)
            .map_or(&[], |(_, names)| names.as_slice());
        let action_boundary = if field_id == 0 && !names.is_empty() {
            names[..names.len() - 1].iter().position(|name| {
                let text = py::py_str(name);
                py::casefold(py::strip(&text)).contains("memo") || py::strip(&text).contains("メモ")
            })
        } else {
            None
        };
        let mut tracks: Vec<&Json> = match field.get("tracks") {
            Some(Json::Array(tracks)) => tracks.iter().collect(),
            _ => Vec::new(),
        };
        tracks.sort_by_key(|track| int_or(py::get(track, "trackNo"), 0));
        for track in tracks {
            if !matches!(track, Json::Object(_)) {
                continue;
            }
            let track_no = int_or(track.get("trackNo"), 0);
            let default = match field_id {
                0 => "セル欄",
                3 => "アクション",
                _ => "カメラ",
            };
            let header_name = usize::try_from(track_no)
                .ok()
                .and_then(|i| names.get(i))
                .map(|n| py::strip(&py::py_str(n)).to_string())
                .filter(|n| !n.is_empty());
            let name = header_name.unwrap_or_else(|| format!("{default} {}", track_no + 1));
            let mut entries: Vec<(i64, &Json)> = Vec::new();
            if let Some(Json::Array(items)) = track.get("frames") {
                for item in items {
                    if matches!(item, Json::Object(_)) {
                        let frame = int_or(item.get("frame"), 0);
                        match entries.iter_mut().find(|(f, _)| *f == frame) {
                            Some(entry) => entry.1 = item,
                            None => entries.push((frame, item)),
                        }
                    }
                }
            }
            let mut states = Vec::new();
            let mut display_values = Vec::new();
            let mut current: Option<i64> = None;
            let mut blank_label_count = 0;
            for frame in 0..duration as i64 {
                let item = entries.iter().find(|(f, _)| *f == frame).map(|(_, i)| *i);
                let mut values: Vec<Json> = Vec::new();
                if let Some(item) = item {
                    let instruction = match item.get("data") {
                        Some(Json::Array(data)) => data
                            .iter()
                            .find(|d| matches!(d, Json::Object(_)) && int_or(d.get("id"), -1) == 0),
                        _ => None,
                    };
                    if let Some(instruction) = instruction {
                        values = match instruction.get("values") {
                            Some(Json::Array(raw)) => raw.clone(),
                            Some(raw) => vec![raw.clone()],
                            None => Vec::new(),
                        };
                    }
                }
                let tokens: Vec<String> = values
                    .iter()
                    .map(|v| py::strip(&py::py_str(v)).to_string())
                    .collect();
                let token = tokens.first().map(String::as_str);
                let mut display = if let Some(label) = token.and_then(symbol) {
                    label.to_string()
                } else if field_id == 3 && !tokens.is_empty() {
                    tokens
                        .iter()
                        .take(2)
                        .filter(|v| !v.is_empty())
                        .cloned()
                        .collect::<Vec<_>>()
                        .join("：")
                } else if !tokens.is_empty() {
                    tokens
                        .iter()
                        .filter(|v| !v.is_empty())
                        .cloned()
                        .collect::<Vec<_>>()
                        .join(" / ")
                } else {
                    String::new()
                };
                if field_id == 0 {
                    match token {
                        None | Some("SYMBOL_HYPHEN") => {
                            if current.is_some() {
                                display = "｜".into();
                            }
                        }
                        Some("SYMBOL_NULL_CELL") => current = None,
                        Some("SYMBOL_TICK_1" | "SYMBOL_TICK_2") => {
                            current = None;
                            blank_label_count += 1;
                        }
                        Some(token) if py::all_decimal(token) => {
                            let value = py::decimal_to_i64(token).unwrap_or(i64::MAX).max(1);
                            current = Some(value);
                            display = value.to_string();
                        }
                        Some(_) => {
                            current = None;
                            blank_label_count += 1;
                        }
                    }
                    states.push(current);
                }
                display_values.push(display);
            }
            let mut group = column_group(field_id, &name);
            if field_id == 0 && action_boundary.is_some_and(|b| track_no <= b as i64) {
                group = ColumnGroup::Action;
            }
            let column = SheetColumn {
                uid: format!("{field_id}:{field_index}:{track_no}"),
                field_id,
                track_no,
                name,
                group,
                states,
                display_values,
                blank_label_count,
                bindable: field_id == 0 && group == ColumnGroup::Cell,
            };
            if field_id == 0 {
                parsed_tracks.push(column.clone());
            }
            sheet_columns.push(column);
        }
    }
    let Some(primary) = parsed_tracks.first() else {
        return Err(SheetError::NoCellField);
    };
    sheet_columns.sort_by_key(|c| (c.group.order(), c.field_id, c.track_no));
    Ok(ParsedSheet {
        format: format_label(raw_text),
        end_frame: duration - 1,
        states: primary.states.clone(),
        blank_label_count: primary.blank_label_count,
        tracks: parsed_tracks.clone(),
        sheet_columns,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn values(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn tdts_lists_only_changes_and_ends_with_a_null_cell() {
        let text = tdts_text(
            "c001",
            &values(&["A"]),
            &[values(&["1", HYPHEN, NULL_CELL, NULL_CELL, "2"])],
            5,
            &SheetHeader::default(),
        );
        let table = load_time_table(&text).unwrap();
        let frames = &table.get("fields").unwrap();
        let Json::Array(fields) = frames else {
            panic!()
        };
        let Json::Array(tracks) = fields[0].get("tracks").unwrap() else {
            panic!()
        };
        let Json::Array(frames) = tracks[0].get("frames").unwrap() else {
            panic!()
        };
        let numbers: Vec<_> = frames
            .iter()
            .map(|f| py::py_int(f.get("frame").unwrap()).unwrap())
            .collect();
        assert_eq!(numbers, [0, 2, 4, 5]);
        assert!(text.starts_with("toeiDigitalTimeSheet Save Data\n{\n    \"timeSheets\""));
    }

    #[test]
    fn xdts_keeps_one_entry_per_frame() {
        let text = xdts_text("x", &values(&["A"]), &[values(&["1", HYPHEN])], 2);
        assert_eq!(
            text,
            "exchangeDigitalTimeSheet Save Data\n{\n  \"timeTables\": [\n    {\n      \"duration\": 2,\n      \"name\": \"x\",\n      \"timeTableHeaders\": [\n        {\n          \"fieldId\": 0,\n          \"names\": [\n            \"A\"\n          ]\n        }\n      ],\n      \"fields\": [\n        {\n          \"fieldId\": 0,\n          \"tracks\": [\n            {\n              \"trackNo\": 0,\n              \"frames\": [\n                {\n                  \"frame\": 0,\n                  \"data\": [\n                    {\n                      \"id\": 0,\n                      \"values\": [\n                        \"1\"\n                      ]\n                    }\n                  ]\n                },\n                {\n                  \"frame\": 1,\n                  \"data\": [\n                    {\n                      \"id\": 0,\n                      \"values\": [\n                        \"SYMBOL_HYPHEN\"\n                      ]\n                    }\n                  ]\n                }\n              ]\n            }\n          ]\n        }\n      ]\n    }\n  ],\n  \"version\": 5\n}\n"
        );
    }

    #[test]
    fn parser_holds_a_cell_through_frames_without_an_entry() {
        for format in [SheetFormat::Xdts, SheetFormat::Tdts] {
            let text = sheet_text(
                format,
                "x",
                &values(&["A", "memo"]),
                &[
                    values(&["1", HYPHEN, HYPHEN, NULL_CELL, "3"]),
                    values(&[NULL_CELL; 5]),
                ],
                5,
                &SheetHeader::default(),
            );
            let parsed = parse_timesheet(&text).unwrap();
            assert_eq!(parsed.states, [Some(1), Some(1), Some(1), None, Some(3)]);
            assert_eq!(parsed.end_frame, 4);
            assert_eq!(parsed.tracks[1].group, ColumnGroup::Action);
            assert_eq!(parsed.tracks[0].display_values, ["1", "｜", "｜", "×", "3"]);
        }
    }

    #[test]
    fn unknown_signature_is_rejected() {
        assert_eq!(load_time_table("nope\n{}"), Err(SheetError::Signature));
        assert_eq!(
            load_time_table("exchangeDigitalTimeSheet Save Data\n{\"version\": 4}"),
            Err(SheetError::Version)
        );
    }
}
