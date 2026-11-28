//! カットフォルダーの構成（ブロックのテンプレート）と書き出しの計画（`cut_folder.py`）。
//!
//! テンプレートは文字とブロック（作品名・話数・カット名・セル名・セル番号…）の並びで、
//! 書き出しではファイル名を作り、読み込みでは同じ構成のフォルダーを読み戻す。
//! ここはファイルに触れない。フォルダーの一覧は呼び出し側が [`ListedEntry`] で渡す。

use crate::document::timeline_panel::default_layer_name;
use crate::project::json::Json;

use super::py;

/// ブロックの種類。id は保存するプリセットの値。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Field {
    /// 作品名。
    Title,
    /// シーン。
    Scene,
    /// 話数。
    Episode,
    /// カット名。
    Cut,
    /// セル名。
    Cell,
    /// セル番号。
    Number,
}

impl Field {
    /// 候補の順（`FIELD_IDS`）。
    pub const ALL: [Field; 6] = [
        Field::Title,
        Field::Scene,
        Field::Episode,
        Field::Cut,
        Field::Cell,
        Field::Number,
    ];
    /// 入力欄から値が来るブロック（`VALUE_FIELDS`）。
    pub const VALUES: [Field; 4] = [Field::Title, Field::Scene, Field::Episode, Field::Cut];

    /// 保存に使う id。
    pub fn id(self) -> &'static str {
        match self {
            Field::Title => "title",
            Field::Scene => "scene",
            Field::Episode => "episode",
            Field::Cut => "cut",
            Field::Cell => "cell",
            Field::Number => "number",
        }
    }

    /// id からブロックの種類。
    pub fn from_id(id: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|field| field.id() == id)
    }

    /// 桁数を取る数のブロックか（`NUMERIC_FIELDS`）。
    pub fn is_numeric(self) -> bool {
        matches!(self, Field::Episode | Field::Cut | Field::Number)
    }

    /// セルごとにしか決まらないブロックか（`CEL_FIELDS`）。
    pub fn is_cel(self) -> bool {
        matches!(self, Field::Cell | Field::Number)
    }
}

/// 大文字・小文字の変換。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub enum Case {
    /// そのまま。
    #[default]
    Keep,
    /// 大文字。
    Upper,
    /// 小文字。
    Lower,
}

impl Case {
    fn id(self) -> &'static str {
        match self {
            Case::Keep => "keep",
            Case::Upper => "upper",
            Case::Lower => "lower",
        }
    }
}

/// フォルダー名に使えない文字（Windows の禁止文字）。
pub const INVALID_NAME_CHARS: &str = "<>:\"\\|?*";
/// メモのファイル名（`memo_window.MEMO_FILE_NAME`）。タイムシートの隣に置く。
pub const MEMO_FILE_NAME: &str = "_memo.txt";

/// テンプレートの1要素。文字の並びかブロック。
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub struct Token {
    /// 文字の並び（ブロックなら None）。
    pub text: Option<String>,
    /// ブロックの種類（文字なら None）。
    pub field: Option<Field>,
    /// 0 詰めの桁数。
    pub digits: Option<i64>,
    /// 大文字・小文字。
    pub case: Case,
    /// 値の前に付ける文字。
    pub prefix: String,
    /// 値の後に付ける文字。
    pub suffix: String,
}

impl Token {
    /// 文字の並び（`text()`）。
    pub fn text(value: &str) -> Self {
        Self {
            text: Some(value.to_string()),
            ..Self::default()
        }
    }

    /// ブロック（`block()`）。
    pub fn block(field: Field) -> Self {
        Self {
            field: Some(field),
            ..Self::default()
        }
    }

    /// 桁数を付けたブロック。
    pub fn digits(mut self, digits: i64) -> Self {
        self.digits = Some(digits);
        self
    }

    /// 前置きを付けたブロック。
    pub fn prefix(mut self, prefix: &str) -> Self {
        self.prefix = prefix.to_string();
        self
    }

    /// ブロックか。
    pub fn is_field(&self) -> bool {
        self.field.is_some()
    }

    /// 保存の形（`Token.to_json`）。
    pub fn to_json(&self) -> Json {
        let Some(field) = self.field else {
            return py::object([("text", Json::Str(self.text.clone().unwrap_or_default()))]);
        };
        let mut fields = vec![("field".to_string(), py::string(field.id()))];
        if let Some(digits) = self.digits {
            fields.push(("digits".into(), py::int(digits)));
        }
        if self.case != Case::Keep {
            fields.push(("case".into(), py::string(self.case.id())));
        }
        if !self.prefix.is_empty() {
            fields.push(("prefix".into(), py::string(self.prefix.clone())));
        }
        if !self.suffix.is_empty() {
            fields.push(("suffix".into(), py::string(self.suffix.clone())));
        }
        Json::Object(fields)
    }

    /// 保存の形から（`Token.from_json`）。オブジェクトでない・知らないブロックは Err。
    pub fn from_json(data: &Json) -> Result<Self, String> {
        if !matches!(data, Json::Object(_)) {
            return Err("テンプレートの要素がオブジェクトではありません".into());
        }
        if let Some(name) = data.get("field") {
            let name = py::py_str(name);
            let field = Field::from_id(&name).ok_or_else(|| format!("unknown field: {name}"))?;
            let digits = match data.get("digits") {
                None | Some(Json::Null) => None,
                Some(value) => Some(py::py_int(value).ok_or("digits")?),
            };
            let case = match data.get("case").map(py::py_str).as_deref() {
                Some("upper") => Case::Upper,
                Some("lower") => Case::Lower,
                _ => Case::Keep,
            };
            return Ok(Self {
                text: None,
                field: Some(field),
                digits,
                case,
                prefix: data.get("prefix").map(py::py_str).unwrap_or_default(),
                suffix: data.get("suffix").map(py::py_str).unwrap_or_default(),
            });
        }
        Ok(Self::text(
            &data.get("text").map(py::py_str).unwrap_or_default(),
        ))
    }
}

/// テンプレート。
pub type Template = Vec<Token>;

/// 文字の並びを1文字ずつに分ける（編集の単位、`explode`）。
pub fn explode(template: &[Token]) -> Template {
    let mut out = Vec::new();
    for token in template {
        if token.is_field() {
            out.push(token.clone());
        } else {
            out.extend(
                token
                    .text
                    .as_deref()
                    .unwrap_or("")
                    .chars()
                    .map(|c| Token::text(&c.to_string())),
            );
        }
    }
    out
}

/// 隣り合う文字の並びをまとめる（保存の形、`compact`）。
pub fn compact<'a>(template: impl IntoIterator<Item = &'a Token>) -> Template {
    let mut out: Template = Vec::new();
    for token in template {
        if !token.is_field() && out.last().is_some_and(|last| !last.is_field()) {
            let last = out.last_mut().unwrap();
            let mut text = last.text.clone().unwrap_or_default();
            text.push_str(token.text.as_deref().unwrap_or(""));
            *last = Token::text(&text);
        } else if token.is_field() || token.text.as_deref().is_some_and(|t| !t.is_empty()) {
            out.push(token.clone());
        }
    }
    out
}

/// `template_to_json`。
pub fn template_to_json(template: &[Token]) -> Json {
    Json::Array(compact(template).iter().map(Token::to_json).collect())
}

/// `template_from_json`。None・null・空はテンプレートなし。
pub fn template_from_json(data: Option<&Json>) -> Result<Template, String> {
    let items = match data {
        None => return Ok(Vec::new()),
        Some(value) if !py::truthy(value) => return Ok(Vec::new()),
        Some(Json::Array(items)) => items,
        Some(_) => return Err("テンプレートが配列ではありません".into()),
    };
    let tokens = items
        .iter()
        .map(Token::from_json)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(compact(&tokens))
}

/// ブロックの値。入力欄の4つはカット全体、セル名・セル番号はセルごと。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Values {
    /// 作品名・シーン・話数・カット名（Python の values 辞書。無いキーは None）。
    pub title: Option<String>,
    /// シーン。
    pub scene: Option<String>,
    /// 話数。
    pub episode: Option<String>,
    /// カット名。
    pub cut: Option<String>,
    /// セル名。
    pub cell: Option<String>,
    /// セル番号。
    pub number: Option<i64>,
}

impl Values {
    /// ブロックの値を文字列で（`str(value)`、None は空）。
    pub fn get(&self, field: Field) -> Option<String> {
        match field {
            Field::Title => self.title.clone(),
            Field::Scene => self.scene.clone(),
            Field::Episode => self.episode.clone(),
            Field::Cut => self.cut.clone(),
            Field::Cell => self.cell.clone(),
            Field::Number => self.number.map(|n| n.to_string()),
        }
    }

    fn base(&self) -> Self {
        Self {
            cell: None,
            number: None,
            ..self.clone()
        }
    }
}

/// ブロック1つを文字にする（`format_field`）。空の値は前置き・後置きも出さない。
pub fn format_field(token: &Token, value: Option<&str>) -> String {
    let raw = value.unwrap_or("");
    if raw.is_empty() {
        return String::new();
    }
    let mut raw = raw.to_string();
    if let (Some(field), Some(digits)) = (token.field, token.digits) {
        if field.is_numeric() && digits != 0 && py::all_decimal(&raw) {
            let count = raw.chars().count() as i64;
            if digits > count {
                raw = "0".repeat((digits - count) as usize) + &raw;
            }
        }
    }
    match token.case {
        Case::Upper => raw = raw.to_uppercase(),
        Case::Lower => raw = raw.to_lowercase(),
        Case::Keep => {}
    }
    format!("{}{raw}{}", token.prefix, token.suffix)
}

/// テンプレートを文字にする（`render`）。
pub fn render(template: &[Token], values: &Values) -> String {
    template
        .iter()
        .map(|token| match token.field {
            Some(field) => format_field(token, values.get(field).as_deref()),
            None => token.text.clone().unwrap_or_default(),
        })
        .collect()
}

/// テンプレートに含まれるブロック（`fields_in`）。
pub fn fields_in(template: &[Token]) -> Vec<Field> {
    let mut fields: Vec<Field> = template.iter().filter_map(|t| t.field).collect();
    fields.sort();
    fields.dedup();
    fields
}

/// セル画像の形式。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ImageFormat {
    /// PNG。
    #[default]
    Png,
    /// TGA（RLE）。
    Tga,
}

impl ImageFormat {
    /// 拡張子。
    pub fn extension(self) -> &'static str {
        match self {
            ImageFormat::Png => "png",
            ImageFormat::Tga => "tga",
        }
    }
}

/// タイムシートの形式。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum SheetFormat {
    /// XDTS（交換用）。
    #[default]
    Xdts,
    /// TDTS（東映デジタルタイムシート）。
    Tdts,
}

impl SheetFormat {
    /// 拡張子。
    pub fn extension(self) -> &'static str {
        match self {
            SheetFormat::Xdts => "xdts",
            SheetFormat::Tdts => "tdts",
        }
    }
}

/// 書き出しダイアログで決める構成（カットごとの値を除く、`CutFolderLayout`）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CutFolderLayout {
    /// カットフォルダーの名前。
    pub folder: Template,
    /// セルのフォルダー。
    pub cell_folder: Template,
    /// セル画像のファイル名。
    pub cell_file: Template,
    /// タイムシートのファイル名。
    pub timesheet: Template,
    /// タイムシートを置くサブフォルダー。空ならカットフォルダーの直下。
    pub timesheet_folder: Template,
    /// 空のフォルダー。
    pub extra_folders: Vec<Template>,
    /// セル画像の形式。
    pub image_format: ImageFormat,
    /// タイムシートの形式。
    pub timesheet_format: SheetFormat,
    /// タイムシートを書くか（#115）。既定は書く（今までの動き）。
    /// 書かないときは、タイムシートとそのフォルダーを作らず、メモはカットフォルダーの直下に置く。
    pub write_timesheet: bool,
}

impl Default for CutFolderLayout {
    fn default() -> Self {
        Self {
            folder: Vec::new(),
            cell_folder: Vec::new(),
            cell_file: Vec::new(),
            timesheet: Vec::new(),
            timesheet_folder: Vec::new(),
            extra_folders: Vec::new(),
            image_format: ImageFormat::Png,
            timesheet_format: SheetFormat::Xdts,
            write_timesheet: true,
        }
    }
}

impl CutFolderLayout {
    /// 保存の形（`CutFolderLayout.to_json`）。write_timesheet は書かないときだけ書く。
    pub fn to_json(&self) -> Json {
        let mut fields = vec![
            ("folder".to_string(), template_to_json(&self.folder)),
            ("cell_folder".into(), template_to_json(&self.cell_folder)),
            ("cell_file".into(), template_to_json(&self.cell_file)),
            ("timesheet".into(), template_to_json(&self.timesheet)),
            (
                "timesheet_folder".into(),
                template_to_json(&self.timesheet_folder),
            ),
            (
                "extra_folders".into(),
                Json::Array(
                    self.extra_folders
                        .iter()
                        .map(|t| template_to_json(t))
                        .collect(),
                ),
            ),
            (
                "image_format".into(),
                py::string(self.image_format.extension()),
            ),
            (
                "timesheet_format".into(),
                py::string(self.timesheet_format.extension()),
            ),
        ];
        if !self.write_timesheet {
            fields.push(("write_timesheet".into(), Json::Bool(false)));
        }
        Json::Object(fields)
    }

    /// 保存の形から（`CutFolderLayout.from_json`）。
    pub fn from_json(data: &Json) -> Result<Self, String> {
        if !matches!(data, Json::Object(_)) {
            return Err("構成がオブジェクトではありません".into());
        }
        let lower = |key: &str, default: &str| {
            data.get(key)
                .map(py::py_str)
                .unwrap_or_else(|| default.to_string())
                .to_lowercase()
        };
        let extra_folders = match data.get("extra_folders") {
            Some(Json::Array(items)) => items
                .iter()
                .map(|item| template_from_json(Some(item)))
                .collect::<Result<Vec<_>, _>>()?,
            Some(value) if py::truthy(value) => return Err("extra_folders".into()),
            _ => Vec::new(),
        };
        Ok(Self {
            folder: template_from_json(data.get("folder"))?,
            cell_folder: template_from_json(data.get("cell_folder"))?,
            cell_file: template_from_json(data.get("cell_file"))?,
            timesheet: template_from_json(data.get("timesheet"))?,
            timesheet_folder: template_from_json(data.get("timesheet_folder"))?,
            extra_folders,
            image_format: if lower("image_format", "png") == "tga" {
                ImageFormat::Tga
            } else {
                ImageFormat::Png
            },
            timesheet_format: if lower("timesheet_format", "xdts") == "tdts" {
                SheetFormat::Tdts
            } else {
                SheetFormat::Xdts
            },
            write_timesheet: !matches!(data.get("write_timesheet"), Some(value) if !py::truthy(value)),
        })
    }

    fn all_templates(&self) -> impl Iterator<Item = &Template> {
        [
            &self.folder,
            &self.cell_folder,
            &self.cell_file,
            &self.timesheet,
            &self.timesheet_folder,
        ]
        .into_iter()
        .chain(&self.extra_folders)
    }
}

/// PMA標準：作品名_話数_c012 のフォルダーに A/A0001.png と XDTS（`pma_standard_layout`）。
pub fn pma_standard_layout() -> CutFolderLayout {
    let cut_name = vec![
        Token::block(Field::Title),
        Token::text("_"),
        Token::block(Field::Episode).digits(2),
        Token::text("_"),
        Token::block(Field::Cut).digits(3).prefix("c"),
    ];
    CutFolderLayout {
        folder: cut_name.clone(),
        cell_folder: vec![Token::block(Field::Cell)],
        cell_file: vec![
            Token::block(Field::Cell),
            Token::block(Field::Number).digits(4),
        ],
        timesheet: cut_name,
        ..CutFolderLayout::default()
    }
}

/// _ts・_pool：撮影へ渡す構成（`ts_pool_layout`）。
pub fn ts_pool_layout() -> CutFolderLayout {
    CutFolderLayout {
        folder: vec![
            Token::block(Field::Title),
            Token::text("_"),
            Token::block(Field::Cut).digits(3).prefix("C"),
        ],
        cell_folder: vec![Token::block(Field::Cell)],
        cell_file: vec![
            Token::block(Field::Cell),
            Token::text("_"),
            Token::block(Field::Number).digits(4),
        ],
        timesheet: vec![Token::block(Field::Cut).digits(3).prefix("c")],
        timesheet_folder: vec![Token::text("_ts")],
        extra_folders: vec![vec![Token::text("_pool")]],
        image_format: ImageFormat::Png,
        timesheet_format: SheetFormat::Tdts,
        write_timesheet: true,
    }
}

/// 組み込みのプリセット（名前と構成、`BUILTIN_PRESETS` の順）。
pub fn builtin_presets() -> Vec<(&'static str, CutFolderLayout)> {
    vec![
        ("PMA標準", pma_standard_layout()),
        ("_ts・_pool", ts_pool_layout()),
    ]
}

/// 書き出す1枚の絵（列とセル番号、`CelSource`）。
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct CelSource {
    /// レイヤーの表示順の番号。
    pub layer_index: usize,
    /// セル名（フォルダー名に使えるよう整えたレイヤー名）。
    pub cell: String,
    /// セル番号。
    pub number: i64,
    /// 絵を最初に見つけたコマ（画像を引くのに使う）。
    pub frame: usize,
}

/// 名前が使えない理由。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum NameReason {
    /// 空。
    Empty,
    /// "." か ".."。
    Dots,
    /// 使えない文字。
    Chars,
    /// 末尾の空白・ピリオド。
    Trailing,
    /// スラッシュ（フォルダー名だけ）。
    Slash,
}

/// 名前の問題がある場所。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum NamePlace {
    /// カットフォルダー。
    Folder,
    /// セル画像。
    CellFile,
    /// タイムシート。
    Timesheet,
    /// タイムシートのフォルダー。
    TimesheetFolder,
    /// 空のフォルダー。
    ExtraFolder,
}

impl NamePlace {
    /// Python の問題の3つ目の値。
    pub fn id(self) -> &'static str {
        match self {
            NamePlace::Folder => "folder",
            NamePlace::CellFile => "cell_file",
            NamePlace::Timesheet => "timesheet",
            NamePlace::TimesheetFolder => "timesheet_folder",
            NamePlace::ExtraFolder => "extra_folder",
        }
    }
}

/// 書き出しの計画の問題（Python の `(kind, detail)`）。
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum Problem {
    /// 入力欄が空（`missing_value`）。
    MissingValue(Field),
    /// 使えない名前（`bad_name`）。
    BadName(String, NamePlace),
    /// セル画像にセル番号のブロックがない（`no_number`）。
    NoNumber,
    /// 同じ名前のファイル（`duplicate`）。
    Duplicate(String),
}

/// 書き出しの計画（`ExportPlan`）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExportPlan {
    /// カットフォルダーの名前。
    pub folder_name: String,
    /// カットフォルダーからの相対パスと絵（書く順）。
    pub files: Vec<(String, CelSource)>,
    /// タイムシートの相対パス。書かない構成なら None。
    pub timesheet_path: Option<String>,
    /// 作るフォルダー（カットフォルダーからの相対）。
    pub folders: Vec<String>,
    /// 問題。空なら書き出せる。
    pub problems: Vec<Problem>,
    /// タイムシートの形式。
    pub timesheet_format: SheetFormat,
    /// TDTS の作品情報（cut・episode・scene）。
    pub sheet_header: SheetHeader,
}

impl ExportPlan {
    /// 問題がないか。
    pub fn ok(&self) -> bool {
        self.problems.is_empty()
    }

    /// タイムシートの名前（拡張子なし、`Path(timesheet_path).stem`）。
    pub fn timesheet_name(&self) -> Option<String> {
        let path = self.timesheet_path.as_deref()?;
        let name = path.rsplit('/').next().unwrap_or(path);
        Some(path_stem(name).to_string())
    }

    /// メモ（`_memo.txt`）の相対パス。タイムシートの隣、書かない構成ならカットフォルダーの直下。
    pub fn memo_path(&self) -> String {
        match self
            .timesheet_path
            .as_deref()
            .and_then(|p| p.rsplit_once('/'))
        {
            Some((directory, _)) => format!("{directory}/{MEMO_FILE_NAME}"),
            None => MEMO_FILE_NAME.to_string(),
        }
    }
}

/// TDTS のシートの頭に書く作品情報。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SheetHeader {
    /// カット。
    pub cut: String,
    /// 話数。
    pub episode: String,
    /// シーン。
    pub scene: String,
}

/// 1つのファイル・フォルダー名として使えない理由（`invalid_name_reason`）。
pub fn invalid_name_reason(name: &str) -> Option<NameReason> {
    if py::strip(name).is_empty() {
        return Some(NameReason::Empty);
    }
    if name == "." || name == ".." {
        return Some(NameReason::Dots);
    }
    if name
        .chars()
        .any(|c| INVALID_NAME_CHARS.contains(c) || (c as u32) < 32)
    {
        return Some(NameReason::Chars);
    }
    if name != py::rstrip_chars(name, " .") {
        return Some(NameReason::Trailing);
    }
    None
}

/// 書き出すすべてのパスを決め、問題を集める（`plan_export`）。
///
/// 問題は例外にせず全部返す（画面がプレビューの横に並べる）。
pub fn plan_export(layout: &CutFolderLayout, values: &Values, cels: &[CelSource]) -> ExportPlan {
    let mut problems = Vec::new();
    let base = values.base();

    for key in Field::VALUES {
        let used = layout.all_templates().any(|t| fields_in(t).contains(&key));
        // Python の str(values.get(key) or "").strip()。
        let value = base.get(key).unwrap_or_default();
        if used && py::strip(&value).is_empty() {
            problems.push(Problem::MissingValue(key));
        }
    }

    let folder_name = render(&layout.folder, &base);
    if invalid_name_reason(&folder_name).is_some() || folder_name.contains('/') {
        problems.push(Problem::BadName(folder_name.clone(), NamePlace::Folder));
    }

    if !fields_in(&layout.cell_file).contains(&Field::Number) {
        problems.push(Problem::NoNumber);
    }

    let extension = layout.image_format.extension();
    let mut files: Vec<(String, CelSource)> = Vec::new();
    let mut folders: Vec<String> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for cel in cels {
        let cel_values = Values {
            cell: Some(cel.cell.clone()),
            number: Some(cel.number),
            ..base.clone()
        };
        let directory = render(&layout.cell_folder, &cel_values);
        let name = render(&layout.cell_file, &cel_values);
        if name.is_empty() {
            problems.push(Problem::BadName(String::new(), NamePlace::CellFile));
            break;
        }
        let relative = if directory.is_empty() {
            format!("{name}.{extension}")
        } else {
            format!("{directory}/{name}.{extension}")
        };
        if relative
            .split('/')
            .any(|part| invalid_name_reason(part).is_some())
        {
            problems.push(Problem::BadName(relative.clone(), NamePlace::CellFile));
        }
        if !seen.insert(py::casefold(&relative)) {
            problems.push(Problem::Duplicate(relative));
            continue;
        }
        if !directory.is_empty() && !folders.contains(&directory) {
            folders.push(directory);
        }
        files.push((relative, cel.clone()));
    }

    for template in &layout.extra_folders {
        let extra = py::strip_chars(&render(template, &base), "/").to_string();
        if extra.is_empty() {
            continue;
        }
        if extra
            .split('/')
            .any(|part| invalid_name_reason(part).is_some())
        {
            problems.push(Problem::BadName(extra, NamePlace::ExtraFolder));
            continue;
        }
        if !folders.contains(&extra) {
            folders.push(extra);
        }
    }

    let timesheet_path = if layout.write_timesheet {
        let timesheet_name = render(&layout.timesheet, &base);
        if invalid_name_reason(&timesheet_name).is_some() || timesheet_name.contains('/') {
            problems.push(Problem::BadName(
                timesheet_name.clone(),
                NamePlace::Timesheet,
            ));
        }
        let mut path = format!("{timesheet_name}.{}", layout.timesheet_format.extension());
        let sheet_folder =
            py::strip_chars(&render(&layout.timesheet_folder, &base), "/").to_string();
        if !sheet_folder.is_empty() {
            if sheet_folder
                .split('/')
                .any(|part| invalid_name_reason(part).is_some())
            {
                problems.push(Problem::BadName(sheet_folder, NamePlace::TimesheetFolder));
            } else {
                if !folders.contains(&sheet_folder) {
                    folders.push(sheet_folder.clone());
                }
                path = format!("{sheet_folder}/{path}");
            }
        }
        Some(path)
    } else {
        None
    };

    let mut unique: Vec<Problem> = Vec::new();
    for problem in problems {
        if !unique.contains(&problem) {
            unique.push(problem);
        }
    }
    let header = |value: &Option<String>| py::strip(value.as_deref().unwrap_or("")).to_string();
    ExportPlan {
        folder_name,
        files,
        timesheet_path,
        folders,
        problems: unique,
        timesheet_format: layout.timesheet_format,
        sheet_header: SheetHeader {
            cut: header(&values.cut),
            episode: header(&values.episode),
            scene: header(&values.scene),
        },
    }
}

/// レイヤー名をセル名（フォルダー名）に整える（`cut_folder_cels` の名前）。
///
/// 使えない文字と "/" を "_" にし、前後の空白・ピリオドを落とす。空なら既定の名前。
pub fn safe_cell_name(name: &str, layer_index: usize) -> String {
    let fallback = default_layer_name(layer_index);
    let stripped = py::strip(name);
    let source = if stripped.is_empty() {
        fallback.as_str()
    } else {
        stripped
    };
    let replaced: String = source
        .chars()
        .map(|c| {
            if INVALID_NAME_CHARS.contains(c) || c == '/' {
                '_'
            } else {
                c
            }
        })
        .collect();
    let trimmed = py::strip_chars(&replaced, " .");
    if trimmed.is_empty() {
        fallback
    } else {
        trimmed.to_string()
    }
}

fn is_ascii_letter(c: char) -> bool {
    c.is_ascii_alphabetic()
}

fn digits_at(chars: &[char], at: usize) -> Option<usize> {
    let end = chars[at..]
        .iter()
        .position(|&c| !py::is_decimal(c))
        .map_or(chars.len(), |n| at + n);
    (end > at).then_some(end)
}

/// `(?:cut|c)[_-]?(\d+)` が at から始まれば数字の範囲。
fn cut_number_at(chars: &[char], at: usize) -> Option<(usize, usize)> {
    let lower = |i: usize| chars.get(i).map(|c| c.to_ascii_lowercase());
    let mut starts = Vec::new();
    if lower(at) == Some('c') && lower(at + 1) == Some('u') && lower(at + 2) == Some('t') {
        starts.push(at + 3);
    }
    if lower(at) == Some('c') {
        starts.push(at + 1);
    }
    for start in starts {
        if matches!(chars.get(start), Some('_' | '-')) {
            if let Some(end) = digits_at(chars, start + 1) {
                return Some((start + 1, end));
            }
        }
        if start < chars.len() {
            if let Some(end) = digits_at(chars, start) {
                return Some((start, end));
            }
        }
    }
    None
}

/// プロジェクト名のカット番号（カット名の初期値、`guess_cut_number`）。
///
/// c・C・cut の後の数字（`s03_c012_v2` → `012`）を優先し、なければ最後の数字の並び。
pub fn guess_cut_number(name: &str) -> String {
    let chars: Vec<char> = name.chars().collect();
    if chars.is_empty() {
        return String::new();
    }
    for p in 0..=chars.len() {
        if p == 0 {
            if let Some((start, end)) = cut_number_at(&chars, 0) {
                return chars[start..end].iter().collect();
            }
        }
        if p < chars.len() && !is_ascii_letter(chars[p]) {
            if let Some((start, end)) = cut_number_at(&chars, p + 1) {
                return chars[start..end].iter().collect();
            }
        }
    }
    last_digit_run(name).map_or(String::new(), |(start, end)| name[start..end].to_string())
}

/// 最後の数字の並び（バイト位置、`(\d+)(?!.*\d)`）。
fn last_digit_run(stem: &str) -> Option<(usize, usize)> {
    let mut end = None;
    let mut start = None;
    for (index, c) in stem.char_indices().rev() {
        if py::is_decimal(c) {
            if end.is_none() {
                end = Some(index + c.len_utf8());
            }
            start = Some(index);
        } else if end.is_some() {
            break;
        }
    }
    Some((start?, end?))
}

/// セル画像のセル番号（ファイル名の最後の数字、`_cel_number`）。1 未満は None。
pub fn cel_number(stem: &str) -> Option<i64> {
    let (start, end) = last_digit_run(stem)?;
    let number = py::decimal_to_i64(&stem[start..end])?;
    (number >= 1).then_some(number)
}

/// カットフォルダーの直下にあるセル画像の列名（`A_0001` → `A`、`_column_from_stem`）。
pub fn column_from_stem(stem: &str) -> String {
    let head = last_digit_run(stem).map_or(stem, |(start, _)| &stem[..start]);
    py::rstrip_chars(head, "_- .").to_string()
}

/// `Path(name).suffix`。
pub fn path_suffix(name: &str) -> &str {
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => &name[i..],
        _ => "",
    }
}

/// `Path(name).stem`。
pub fn path_stem(name: &str) -> &str {
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => &name[..i],
        _ => name,
    }
}

/// フォルダーの一覧の1項目（呼び出し側がファイルシステムから作る）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ListedEntry {
    /// 名前。
    pub name: String,
    /// ファイルか（リンク先で判断）。
    pub is_file: bool,
    /// フォルダーか（リンク先で判断）。
    pub is_dir: bool,
    /// フォルダーなら中身（1段だけ）。
    pub children: Vec<ListedEntry>,
}

/// [`scan_cut_folder`] が見つけたもの（`CutFolderContents`）。パスはカットフォルダーからの相対。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CutFolderContents {
    /// タイムシート（見つけた順）。
    pub timesheets: Vec<String>,
    /// 列（casefold した名前、見つけた順）ごとの {セル番号: パス}。
    pub cels: Vec<(String, std::collections::BTreeMap<i64, String>)>,
    /// 列の casefold した名前と、見つけたときの綴り。
    pub names: Vec<(String, String)>,
}

impl CutFolderContents {
    /// タイムシートの列名の絵（`column`）。
    pub fn column(&self, name: &str) -> Option<&std::collections::BTreeMap<i64, String>> {
        let key = py::casefold(py::strip(name));
        self.cels.iter().find(|(k, _)| *k == key).map(|(_, d)| d)
    }

    /// 列の見つけたときの綴り。
    pub fn name_of(&self, key: &str) -> Option<&str> {
        self.names
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, n)| n.as_str())
    }

    fn add(&mut self, column: &str, path: String, name: &str) {
        let Some(number) = cel_number(path_stem(name)) else {
            return;
        };
        let column = py::strip(column);
        if column.is_empty() {
            return;
        }
        let key = py::casefold(column);
        if !self.names.iter().any(|(k, _)| *k == key) {
            self.names.push((key.clone(), column.to_string()));
        }
        let index = match self.cels.iter().position(|(k, _)| *k == key) {
            Some(index) => index,
            None => {
                self.cels.push((key, Default::default()));
                self.cels.len() - 1
            }
        };
        let drawings = &mut self.cels[index].1;
        // 同じ番号の PNG と TGA があれば PNG。
        if !drawings.contains_key(&number) || path_suffix(name).to_lowercase() == ".png" {
            drawings.insert(number, path);
        }
    }
}

const SHEET_SUFFIXES: [&str; 3] = [".xdts", ".xtds", ".tdts"];
const IMAGE_SUFFIXES: [&str; 2] = [".png", ".tga"];

/// カットフォルダーのタイムシートとセル画像を一覧から探す（`scan_cut_folder`）。
///
/// セルは直下のフォルダー（列ごと）と、直下に置かれた画像から読む。列はフォルダー名、
/// セル番号はファイル名の最後の数字。タイムシートは直下と1段下を探す。
/// `_` か `.` で始まるフォルダーの画像は読まない（`_ts`・`_pool`）。
pub fn scan_cut_folder(entries: &[ListedEntry]) -> CutFolderContents {
    let mut contents = CutFolderContents::default();
    let mut sorted: Vec<&ListedEntry> = entries.iter().collect();
    sorted.sort_by_key(|entry| py::casefold(&entry.name));
    for entry in sorted {
        if entry.name.starts_with('.') {
            continue;
        }
        if entry.is_file {
            let suffix = path_suffix(&entry.name).to_lowercase();
            if SHEET_SUFFIXES.contains(&suffix.as_str()) {
                contents.timesheets.push(entry.name.clone());
            } else if IMAGE_SUFFIXES.contains(&suffix.as_str()) {
                let column = column_from_stem(path_stem(&entry.name));
                contents.add(&column, entry.name.clone(), &entry.name);
            }
            continue;
        }
        if !entry.is_dir {
            continue;
        }
        let skipped = entry.name.starts_with('_') || entry.name.starts_with('.');
        let mut children: Vec<&ListedEntry> = entry.children.iter().collect();
        children.sort_by_key(|child| py::casefold(&child.name));
        for child in children {
            if !child.is_file || child.name.starts_with('.') {
                continue;
            }
            let suffix = path_suffix(&child.name).to_lowercase();
            let path = format!("{}/{}", entry.name, child.name);
            if SHEET_SUFFIXES.contains(&suffix.as_str()) {
                contents.timesheets.push(path);
            } else if IMAGE_SUFFIXES.contains(&suffix.as_str()) && !skipped {
                contents.add(&entry.name, path, &child.name);
            }
        }
    }
    contents
}

/// タイムシートの1列の変わり目を (コマ, 番号) で返す（`exposure_keys`）。None は空セル。
pub fn exposure_keys(states: &[Option<i64>]) -> Vec<(usize, Option<i64>)> {
    let mut keys: Vec<(usize, Option<i64>)> = Vec::new();
    for (frame, &state) in states.iter().enumerate() {
        if keys.last().is_none_or(|&(_, previous)| previous != state) {
            keys.push((frame, state));
        }
    }
    keys
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cel(cell: &str, number: i64) -> CelSource {
        CelSource {
            layer_index: 0,
            cell: cell.into(),
            number,
            frame: 0,
        }
    }

    fn values(title: &str, episode: &str, cut: &str) -> Values {
        Values {
            title: Some(title.into()),
            scene: Some(String::new()),
            episode: Some(episode.into()),
            cut: Some(cut.into()),
            ..Values::default()
        }
    }

    #[test]
    fn standard_layout_names_folder_cels_and_sheet() {
        let plan = plan_export(
            &pma_standard_layout(),
            &values("PMA", "1", "12"),
            &[cel("A", 1), cel("A", 2), cel("B", 1)],
        );
        assert!(plan.ok(), "{:?}", plan.problems);
        assert_eq!(plan.folder_name, "PMA_01_c012");
        let files: Vec<_> = plan.files.iter().map(|(p, _)| p.as_str()).collect();
        assert_eq!(files, ["A/A0001.png", "A/A0002.png", "B/B0001.png"]);
        assert_eq!(plan.folders, ["A", "B"]);
        assert_eq!(plan.timesheet_path.as_deref(), Some("PMA_01_c012.xdts"));
        assert_eq!(plan.memo_path(), "_memo.txt");
    }

    #[test]
    fn ts_pool_layout_puts_the_sheet_in_ts() {
        let plan = plan_export(&ts_pool_layout(), &values("PMA", "", "2"), &[cel("A", 1)]);
        assert!(plan.ok(), "{:?}", plan.problems);
        assert_eq!(plan.folder_name, "PMA_C002");
        assert_eq!(plan.files[0].0, "A/A_0001.png");
        assert_eq!(plan.folders, ["A", "_pool", "_ts"]);
        assert_eq!(plan.timesheet_path.as_deref(), Some("_ts/c002.tdts"));
        assert_eq!(plan.timesheet_name().as_deref(), Some("c002"));
        assert_eq!(plan.memo_path(), "_ts/_memo.txt");
    }

    #[test]
    fn problems_are_collected_once() {
        let mut layout = pma_standard_layout();
        layout.cell_file = vec![Token::block(Field::Cell)];
        let plan = plan_export(&layout, &values("", "1", "1"), &[cel("A", 1), cel("A", 2)]);
        assert_eq!(
            plan.problems,
            [
                Problem::MissingValue(Field::Title),
                Problem::NoNumber,
                Problem::Duplicate("A/A.png".into()),
            ]
        );
        let plan = plan_export(&pma_standard_layout(), &values("a:b", "1", "1"), &[]);
        assert!(plan
            .problems
            .contains(&Problem::BadName("a:b_01_c001".into(), NamePlace::Folder)));
    }

    #[test]
    fn timesheet_can_be_turned_off() {
        let mut layout = ts_pool_layout();
        layout.write_timesheet = false;
        let plan = plan_export(&layout, &values("PMA", "", "2"), &[cel("A", 1)]);
        assert_eq!(plan.timesheet_path, None);
        assert_eq!(plan.folders, ["A", "_pool"]);
        assert_eq!(plan.memo_path(), "_memo.txt");
        let json = layout.to_json();
        assert_eq!(json.get("write_timesheet"), Some(&Json::Bool(false)));
        assert_eq!(CutFolderLayout::from_json(&json).unwrap(), layout);
        let json = pma_standard_layout().to_json();
        assert!(json.get("write_timesheet").is_none());
        assert_eq!(
            CutFolderLayout::from_json(&json).unwrap(),
            pma_standard_layout()
        );
    }

    #[test]
    fn layout_json_matches_python() {
        let text = ts_pool_layout().to_json().dumps(None).unwrap();
        assert_eq!(
            text,
            r#"{"folder": [{"field": "title"}, {"text": "_"}, {"field": "cut", "digits": 3, "prefix": "C"}], "cell_folder": [{"field": "cell"}], "cell_file": [{"field": "cell"}, {"text": "_"}, {"field": "number", "digits": 4}], "timesheet": [{"field": "cut", "digits": 3, "prefix": "c"}], "timesheet_folder": [{"text": "_ts"}], "extra_folders": [[{"text": "_pool"}]], "image_format": "png", "timesheet_format": "tdts"}"#
        );
    }

    #[test]
    fn field_format_pads_and_cases() {
        let token = Token {
            case: Case::Upper,
            suffix: "x".into(),
            ..Token::block(Field::Cut).digits(3).prefix("c")
        };
        assert_eq!(format_field(&token, Some("7")), "c007x");
        assert_eq!(format_field(&token, Some("7a")), "c7Ax");
        assert_eq!(format_field(&token, Some("")), "");
        assert_eq!(
            format_field(&Token::block(Field::Title).digits(4), Some("1")),
            "1"
        );
    }

    #[test]
    fn guesses_cut_numbers_like_python() {
        for (name, expected) in [
            ("s03_c012_v2", "012"),
            ("shot7", "7"),
            ("CUT_45", "45"),
            ("abc12", "12"),
            ("c-3x9", "3"),
            ("cu5", "5"),
            ("", ""),
            ("none", ""),
            ("ac12_c3", "3"),
        ] {
            assert_eq!(guess_cut_number(name), expected, "{name}");
        }
    }

    #[test]
    fn cel_numbers_and_columns() {
        assert_eq!(cel_number("A0001"), Some(1));
        assert_eq!(cel_number("作品_c012_A_0003"), Some(3));
        assert_eq!(cel_number("A0"), None);
        assert_eq!(cel_number("A"), None);
        assert_eq!(column_from_stem("A_0001"), "A");
        assert_eq!(column_from_stem("0001"), "");
        assert_eq!(path_suffix("a.b.PNG"), ".PNG");
        assert_eq!(path_suffix("a."), "");
        assert_eq!(path_stem(".png"), ".png");
    }

    #[test]
    fn scans_columns_sheets_and_skips_pool() {
        let file = |name: &str| ListedEntry {
            name: name.into(),
            is_file: true,
            is_dir: false,
            children: vec![],
        };
        let dir = |name: &str, children: Vec<ListedEntry>| ListedEntry {
            name: name.into(),
            is_file: false,
            is_dir: true,
            children,
        };
        let contents = scan_cut_folder(&[
            dir(
                "b",
                vec![file("b0001.tga"), file("b0001.png"), file(".x0002.png")],
            ),
            dir("_ts", vec![file("c001.tdts"), file("x0001.png")]),
            file("C_0002.png"),
            file("sheet.xdts"),
            dir(
                "A",
                vec![file("A0002.png"), file("A0001.png"), file("note.txt")],
            ),
        ]);
        assert_eq!(contents.timesheets, ["_ts/c001.tdts", "sheet.xdts"]);
        assert_eq!(
            contents.column("a").unwrap().values().collect::<Vec<_>>(),
            ["A/A0001.png", "A/A0002.png"]
        );
        assert_eq!(contents.column("B").unwrap()[&1], "b/b0001.png");
        assert_eq!(contents.column("c").unwrap()[&2], "C_0002.png");
        assert_eq!(contents.name_of("b"), Some("b"));
        assert!(contents.column("x").is_none());
    }

    #[test]
    fn exposure_keys_list_changes() {
        assert_eq!(
            exposure_keys(&[Some(1), Some(1), None, Some(2), Some(2)]),
            [(0, Some(1)), (2, None), (3, Some(2))]
        );
        assert_eq!(exposure_keys(&[None]), [(0, None)]);
    }

    #[test]
    fn safe_cell_names() {
        assert_eq!(safe_cell_name(" a/b:c. ", 0), "a_b_c");
        assert_eq!(safe_cell_name("  ", 1), "B");
        assert_eq!(safe_cell_name("..", 2), "C");
    }
}
