//! .pman（ZIP）のバイト列を開く・保存する。ファイル・スレッド・Qt を使わない。
//!
//! Python の project_io.read_project_archive（v2 の経路）・project_v2._decode_payload と
//! project_v2.write_snapshot に対応する。検証の順序・上限・エラーの文言を同じにする。
//!
//! Python と異なる点:
//! - format_version 1（旧形式）は開けない（[`ProjectError::LegacyFormat`]）。
//! - Rust の ZipReader で開けない ZIP（zipfile だけが読める LZMA・BZIP2 や特殊な拡張など）は
//!   [`ProjectError::Archive`]。ファイル数・暗号化・展開後サイズ・重複名の検査は
//!   中央ディレクトリを直接読んで Python と同じエラーにする。
//! - Rust で展開できない PNG（16bit・APNG など。Python は Qt に回す）は
//!   [`ProjectError::UnsupportedPng`]。
//! - サムネイルは Qt で縮小して描くため、呼び出し側が PNG のバイト列を渡す。
use std::collections::{BTreeMap, BTreeSet};
use std::io::Cursor;

use serde_json::Value;

use super::json::{self, Json, JsonError};
use super::payload::{decode_v2_plan, Map};
use super::png::{decode_png, encode_png};
use super::qt_alpha::{qt_premultiplied_roundtrip, white_to_transparent};
use super::zip::{CompressionMethod, ZipBuilder, ZipEntry, ZipReader, ZipTime};
use crate::document::{model::Document, v2};
use crate::image::RgbaImage;

/// constants.MAX_PROJECT_METADATA_BYTES
pub const MAX_PROJECT_METADATA_BYTES: u64 = 16 * 1024 * 1024;
/// constants.MAX_PROJECT_FRAMES
pub const MAX_PROJECT_FRAMES: usize = 10_000;
/// constants.MAX_PROJECT_LAYERS
pub const MAX_PROJECT_LAYERS: usize = 256;
/// constants.MAX_PROJECT_LAYER_CELLS
pub const MAX_PROJECT_LAYER_CELLS: usize = 100_000;
/// constants.MAX_PROJECT_ARCHIVE_BYTES
pub const MAX_PROJECT_ARCHIVE_BYTES: u64 = 8 * 1024 * 1024 * 1024;
/// constants.MAX_PROJECT_IMAGE_BYTES
pub const MAX_PROJECT_IMAGE_BYTES: u64 = 512 * 1024 * 1024;
/// constants.MAX_PROJECT_DECODED_PIXELS
pub const MAX_PROJECT_DECODED_PIXELS: u64 = 2 * 1024 * 1024 * 1024;
/// Qt の QImageReader.allocationLimit() の既定（256 MB）。Python の _decode_png の上限。
pub const PNG_ALLOCATION_LIMIT: u64 = 256 * 1024 * 1024;
/// project_io.SUPPORTED_FORMAT_VERSION
pub const SUPPORTED_FORMAT_VERSION: i64 = 2;
/// project_io.CURRENT_MASK_FORMAT
pub const CURRENT_MASK_FORMAT: i64 = 2;
/// project_io.THUMBNAIL_NAME
pub const THUMBNAIL_NAME: &str = "thumbnail.png";
/// 既定の PNG 解像度（96 dpi）。Python は Qt の画面の解像度（macOS は 72 dpi = 2835）。
pub const DEFAULT_DOTS_PER_METER: u32 = 3780;

/// 開く・保存の失敗。[`ProjectError::message`] が Python の OperationError の文言（翻訳前）。
#[derive(Debug, Clone, PartialEq)]
pub enum ProjectError {
    /// プロジェクト内のファイル数が多すぎます。
    TooManyFiles,
    /// 暗号化されたプロジェクトには対応していません。
    Encrypted,
    /// プロジェクトの展開後サイズが大きすぎます。
    ExpandedTooLarge,
    /// プロジェクト内に重複したファイル名があります。
    DuplicateFileNames,
    /// project.jsonがありません。
    MissingMetadata,
    /// プロジェクト情報が大きすぎます。（開く・保存の両方）
    MetadataTooLarge,
    /// 対応していないプロジェクト形式です。
    UnsupportedFormat,
    /// 新しいバージョンのアプリで保存されたファイル。値は saved_app_version。
    NewerVersion(Option<String>),
    /// プロジェクト情報に重複したキーがあります。
    DuplicateKeys,
    /// プロジェクトの構造が不正です。
    InvalidStructure,
    /// プロジェクト内の画像がありません。
    MissingImage,
    /// format_version 1（旧形式）。Rust では未対応。
    LegacyFormat,
    /// project.json が UTF-8 でない（Python は UnicodeDecodeError）。
    Encoding(String),
    /// project.json が JSON でない（Python は JSONDecodeError）。
    Json(JsonError),
    /// ZIP の破損、または Rust で読めない ZIP（Python は BadZipFile か zipfile で読む）。
    Archive(String),
    /// Rust で展開できない PNG（Python は Qt で読む）。
    UnsupportedPng(String),
    /// プロジェクト内のセル数が多すぎます。（保存）
    TooManyCells,
    /// プロジェクト内の画像サイズがキャンバスと一致しません。（保存）
    ImageSizeMismatch,
    /// 画像を書き出せませんでした（initial が true なら初期状態の画像）。（保存）
    EncodeFailed { path: String, initial: bool },
    /// メタデータや文書が保存できない値（Python は ValueError 等）。（保存）
    Invalid(String),
    /// 書いたアーカイブを開き直す検証に失敗した。（保存）
    Verify(Box<ProjectError>),
}

impl ProjectError {
    /// 種類の名前（列挙子の名前。窓口やテストで種類を比べるのに使う）。
    pub fn kind(&self) -> &'static str {
        match self {
            Self::TooManyFiles => "TooManyFiles",
            Self::Encrypted => "Encrypted",
            Self::ExpandedTooLarge => "ExpandedTooLarge",
            Self::DuplicateFileNames => "DuplicateFileNames",
            Self::MissingMetadata => "MissingMetadata",
            Self::MetadataTooLarge => "MetadataTooLarge",
            Self::UnsupportedFormat => "UnsupportedFormat",
            Self::NewerVersion(_) => "NewerVersion",
            Self::DuplicateKeys => "DuplicateKeys",
            Self::InvalidStructure => "InvalidStructure",
            Self::MissingImage => "MissingImage",
            Self::LegacyFormat => "LegacyFormat",
            Self::Encoding(_) => "Encoding",
            Self::Json(_) => "Json",
            Self::Archive(_) => "Archive",
            Self::UnsupportedPng(_) => "UnsupportedPng",
            Self::TooManyCells => "TooManyCells",
            Self::ImageSizeMismatch => "ImageSizeMismatch",
            Self::EncodeFailed { .. } => "EncodeFailed",
            Self::Invalid(_) => "Invalid",
            Self::Verify(_) => "Verify",
        }
    }

    /// Python の tr() に渡す元の文言と同じ文字列。Python で OperationError にならないものは診断文。
    pub fn message(&self) -> String {
        match self {
            Self::TooManyFiles => "プロジェクト内のファイル数が多すぎます。".into(),
            Self::Encrypted => "暗号化されたプロジェクトには対応していません。".into(),
            Self::ExpandedTooLarge => "プロジェクトの展開後サイズが大きすぎます。".into(),
            Self::DuplicateFileNames => "プロジェクト内に重複したファイル名があります。".into(),
            Self::MissingMetadata => "project.jsonがありません。".into(),
            Self::MetadataTooLarge => "プロジェクト情報が大きすぎます。".into(),
            Self::UnsupportedFormat => "対応していないプロジェクト形式です。".into(),
            Self::NewerVersion(Some(version)) => format!(
                "このファイルは新しいバージョンのアプリ（v{version}）で保存されています。アプリを更新してから開いてください。"
            ),
            Self::NewerVersion(None) => "このファイルは新しいバージョンのアプリで保存されています。アプリを更新してから開いてください。".into(),
            Self::DuplicateKeys => "プロジェクト情報に重複したキーがあります。".into(),
            Self::InvalidStructure => "プロジェクトの構造が不正です。".into(),
            Self::MissingImage => "プロジェクト内の画像がありません。".into(),
            Self::LegacyFormat => "旧形式（format_version 1）のプロジェクトには対応していません。".into(),
            Self::Encoding(reason) => format!("project.json が UTF-8 ではありません: {reason}"),
            Self::Json(error) => format!("project.json を読めません: {error}"),
            Self::Archive(reason) => format!("ZIP を読めません: {reason}"),
            Self::UnsupportedPng(reason) => format!("読めない PNG です: {reason}"),
            Self::TooManyCells => "プロジェクト内のセル数が多すぎます。".into(),
            Self::ImageSizeMismatch => "プロジェクト内の画像サイズがキャンバスと一致しません。".into(),
            Self::EncodeFailed { path, initial: false } => {
                format!("画像を書き出せませんでした: {path}")
            }
            Self::EncodeFailed { path, initial: true } => {
                format!("初期状態の画像を書き出せませんでした: {path}")
            }
            Self::Invalid(reason) => reason.clone(),
            Self::Verify(error) => error.message(),
        }
    }
}

impl std::fmt::Display for ProjectError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message())
    }
}

impl std::error::Error for ProjectError {}

/// 変更のない画像の PNG を保存のたびに符号化しないためのキャッシュ（project_io.PngCache）。
/// 画像の世代番号（[`RgbaImage::generation`]）で引く。
#[derive(Clone, Debug, Default)]
pub struct PngCache {
    entries: BTreeMap<u64, Vec<u8>>,
}

impl PngCache {
    /// 空のキャッシュ。
    pub fn new() -> Self {
        Self::default()
    }

    /// 項目数。
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// 空か。
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// 世代の PNG を返す。
    pub fn get(&self, generation: u64) -> Option<&[u8]> {
        self.entries.get(&generation).map(Vec::as_slice)
    }

    /// 世代の PNG を覚える。
    pub fn remember(&mut self, generation: u64, png: Vec<u8>) {
        self.entries.insert(generation, png);
    }

    /// 全項目（世代の昇順）。
    pub fn iter(&self) -> impl Iterator<Item = (u64, &[u8])> {
        self.entries.iter().map(|(k, v)| (*k, v.as_slice()))
    }

    /// 指定した世代だけを残す。
    pub fn retain_only(&mut self, generations: &BTreeSet<u64>) {
        self.entries.retain(|key, _| generations.contains(key));
    }
}

/// 開いたプロジェクト。Python の LoadedProject と、読み込み時に種をまいた PngCache。
#[derive(Debug)]
pub struct OpenedProject {
    /// 文書。
    pub document: Document,
    /// project.json から tracks と frame_durations を除いた値（Python の metadata と同じ順序）。
    /// created_app_version・saved_app_version は文字列でなければ null。
    pub metadata: Json,
    /// キャンバスの幅。
    pub width: usize,
    /// キャンバスの高さ。
    pub height: usize,
    /// 読んだ形式（常に 2）。
    pub format_version: i64,
    /// 白消しをしていない画像の、元の PNG のバイト列（世代で引く）。
    pub png_cache: PngCache,
}

impl OpenedProject {
    /// メタデータを serde_json の値で返す（キーの順序は保たない）。
    pub fn metadata_value(&self) -> Value {
        self.metadata.to_value()
    }
}

fn check_entries<'a>(
    entries: impl ExactSizeIterator<Item = (&'a [u8], u64, u16)> + Clone,
) -> Result<(), ProjectError> {
    // セルの画像と初期状態で最大2ファイル、ほかに project.json とサムネイル。
    if entries.len() > 2 * MAX_PROJECT_LAYER_CELLS + 2 {
        return Err(ProjectError::TooManyFiles);
    }
    if entries.clone().any(|(_, _, flags)| flags & 1 != 0) {
        return Err(ProjectError::Encrypted);
    }
    let total = entries
        .clone()
        .try_fold(0u64, |sum, (_, size, _)| sum.checked_add(size));
    if total.is_none_or(|total| total > MAX_PROJECT_ARCHIVE_BYTES) {
        return Err(ProjectError::ExpandedTooLarge);
    }
    let mut names = BTreeSet::new();
    if !entries.clone().all(|(name, _, _)| names.insert(name)) {
        return Err(ProjectError::DuplicateFileNames);
    }
    Ok(())
}

/// ZipReader で開けない ZIP の中央ディレクトリを読み、Python が zipfile で開いてから
/// 返すエラー（ファイル数・暗号化・サイズ・重複名）を探す。ZIP64 などは調べない。
fn fallback_error(bytes: &[u8]) -> Option<ProjectError> {
    let search = bytes.len().saturating_sub(22 + 65535);
    let eocd = (search..bytes.len().checked_sub(21)?)
        .rev()
        .find(|&i| bytes[i..].starts_with(b"PK\x05\x06"))?;
    let word = |i: usize| -> Option<u16> {
        Some(u16::from_le_bytes(bytes.get(i..i + 2)?.try_into().ok()?))
    };
    let long = |i: usize| -> Option<u32> {
        Some(u32::from_le_bytes(bytes.get(i..i + 4)?.try_into().ok()?))
    };
    let count = usize::from(word(eocd + 10)?);
    let size = long(eocd + 12)? as usize;
    if count == 0xffff || size == 0xffff_ffff {
        return None;
    }
    let mut at = eocd.checked_sub(size)?;
    let mut entries = Vec::with_capacity(count);
    for _ in 0..count {
        if bytes.get(at..at + 4)? != b"PK\x01\x02" {
            return None;
        }
        let flags = word(at + 8)?;
        let file_size = long(at + 24)?;
        if file_size == 0xffff_ffff {
            return None;
        }
        let name_len = usize::from(word(at + 28)?);
        let extra = usize::from(word(at + 30)?);
        let comment = usize::from(word(at + 32)?);
        let name = bytes.get(at + 46..at + 46 + name_len)?;
        entries.push((name, u64::from(file_size), flags));
        at += 46 + name_len + extra + comment;
    }
    check_entries(entries.iter().copied()).err()
}

struct Archive<'a> {
    zip: ZipReader<Cursor<&'a [u8]>>,
    entries: Vec<ZipEntry>,
    index: BTreeMap<String, usize>,
}

impl Archive<'_> {
    fn read(&mut self, index: usize) -> Result<Vec<u8>, ProjectError> {
        let size = usize::try_from(self.entries[index].file_size)
            .map_err(|_| ProjectError::Archive("展開サイズ".into()))?;
        let mut out = Vec::new();
        out.try_reserve_exact(size)
            .map_err(|_| ProjectError::Archive("展開サイズ".into()))?;
        out.resize(size, 0);
        self.zip
            .read_into(index, &mut out)
            .map_err(|error| ProjectError::Archive(error.to_string()))?;
        Ok(out)
    }
}

/// format_version が int（bool でない）で 1 以上なら、その値（大きすぎる値は i64::MAX）。
fn format_version(value: Option<&Json>) -> Option<i64> {
    match value {
        None => Some(1),
        Some(Json::Int(digits)) if !digits.starts_with('-') && digits != "0" => {
            Some(digits.parse().unwrap_or(i64::MAX))
        }
        _ => None,
    }
}

/// .pman のバイト列を開く（read_project_archive と project_v2.decode_payload）。
pub fn open_project(bytes: &[u8]) -> Result<OpenedProject, ProjectError> {
    let zip = match ZipReader::open(Cursor::new(bytes)) {
        Ok(zip) => zip,
        Err(error) => {
            return Err(fallback_error(bytes).unwrap_or(ProjectError::Archive(error.to_string())))
        }
    };
    let entries = zip.entries().to_vec();
    check_entries(
        entries
            .iter()
            .map(|e| (e.name.as_bytes(), e.file_size, e.flag_bits)),
    )?;
    let index = entries
        .iter()
        .enumerate()
        .map(|(i, e)| (e.name.clone(), i))
        .collect();
    let mut archive = Archive {
        zip,
        entries,
        index,
    };
    let metadata_index = *archive
        .index
        .get("project.json")
        .ok_or(ProjectError::MissingMetadata)?;
    if archive.entries[metadata_index].file_size > MAX_PROJECT_METADATA_BYTES {
        return Err(ProjectError::MetadataTooLarge);
    }
    let text = archive.read(metadata_index)?;
    let text =
        std::str::from_utf8(&text).map_err(|error| ProjectError::Encoding(error.to_string()))?;
    let parsed = json::parse(text).map_err(ProjectError::Json)?;
    let mut metadata = parsed.value;
    if !matches!(metadata, Json::Object(_))
        || metadata.get("format") != Some(&Json::Str("PaintMaskAnimatorProject".into()))
    {
        return Err(ProjectError::UnsupportedFormat);
    }
    let version =
        format_version(metadata.get("format_version")).ok_or(ProjectError::UnsupportedFormat)?;
    for key in ["created_app_version", "saved_app_version"] {
        if !matches!(metadata.get(key), Some(Json::Str(_))) {
            metadata.set(key, Json::Null);
        }
    }
    if version > SUPPORTED_FORMAT_VERSION {
        return Err(ProjectError::NewerVersion(
            match metadata.get("saved_app_version") {
                Some(Json::Str(s)) if !s.is_empty() => Some(s.clone()),
                _ => None,
            },
        ));
    }
    if version != 2 {
        return Err(ProjectError::LegacyFormat);
    }
    if parsed.duplicates > 0 {
        return Err(ProjectError::DuplicateKeys);
    }
    decode_v2(metadata, &mut archive)
}

// native/src/project_payload.rs の KEYS と同じ。Python の窓口は既知のキーだけを計画に渡す。
const PLAN_KEYS: &[&str] = &[
    "mask_format",
    "canvas",
    "width",
    "height",
    "frame_durations",
    "tracks",
    "cells",
    "compatibility",
    "local_states",
    "name",
    "visible",
    "opacity",
    "is_paper",
    "is_draft",
    "color_filter_enabled",
    "color_filter_rgb",
    "import_sources",
    "number",
    "image",
    "initial",
    "cell_name",
    "genga",
    "sheet",
    "positions",
    "pending",
    "drawing_states",
    "resolved_overrides",
    "length_overrides",
    "placement_keys",
    "column",
    "blank_start",
    "kind",
    "length",
    "has_content",
    "is_blank_key",
    "tween_member",
    "retained_number",
    "guard",
    "imported",
    "input_length",
    "value",
    "source_column",
    "current_frame",
    "active_layer_index",
];

fn plan_invalid() -> Value {
    Value::Array(vec![Value::Object(Map::new())])
}

/// native/src/project_payload.rs の convert と同じ投影（Python の dict から計画の入力へ）。
fn plan_value(value: &Json, depth: usize, field: &str) -> Value {
    if depth > 32 {
        return plan_invalid();
    }
    match value {
        Json::Null => Value::Null,
        Json::Bool(b) => Value::Bool(*b),
        Json::Int(digits) => digits
            .parse::<i64>()
            .map(Value::from)
            .or_else(|_| digits.parse::<u64>().map(Value::from))
            .unwrap_or_else(|_| {
                // 大きな整数も opacity だけは Python の math.isfinite と clamp を通る。
                match digits.parse::<f64>() {
                    Ok(n) if field == "opacity" && n.is_finite() => Value::from(n),
                    _ => plan_invalid(),
                }
            }),
        Json::Float(f) => serde_json::Number::from_f64(*f).map_or_else(plan_invalid, Value::Number),
        Json::Str(s) => Value::String(s.clone()),
        Json::Array(items) => Value::Array(
            items
                .iter()
                .map(|item| plan_value(item, depth + 1, ""))
                .collect(),
        ),
        Json::Object(fields) => Value::Object(
            fields
                .iter()
                .filter(|(key, _)| PLAN_KEYS.contains(&key.as_str()))
                .map(|(key, child)| (key.clone(), plan_value(child, depth + 1, key)))
                .collect(),
        ),
    }
}

fn decode_v2(mut payload: Json, archive: &mut Archive<'_>) -> Result<OpenedProject, ProjectError> {
    use ProjectError::InvalidStructure;
    if payload.has_non_finite() {
        return Err(InvalidStructure);
    }
    let names: Vec<String> = archive.entries.iter().map(|e| e.name.clone()).collect();
    let input = plan_value(&payload, 0, "");
    let plan = decode_v2_plan(&input, &names);
    let (requests, width, height, mask) = match &plan {
        Ok(plan) => (
            plan.images.as_slice(),
            plan.data["width"].as_i64().unwrap_or(0),
            plan.data["height"].as_i64().unwrap_or(0),
            plan.data["mask_format"].as_i64().unwrap_or(1),
        ),
        Err(error) => (
            error.images.as_slice(),
            error.width,
            error.height,
            error.mask_format,
        ),
    };
    let width = usize::try_from(width).map_err(|_| InvalidStructure)?;
    let height = usize::try_from(height).map_err(|_| InvalidStructure)?;
    let mut decoded: BTreeMap<Vec<u8>, RgbaImage> = BTreeMap::new();
    let mut cache = PngCache::new();
    let mut images = Vec::with_capacity(requests.len());
    for request in requests {
        let path = request[0].as_str().ok_or(InvalidStructure)?;
        let paper = request[1].as_bool().unwrap_or(false);
        let draft = request[2].as_bool().unwrap_or(false);
        let index = *archive.index.get(path).ok_or(ProjectError::MissingImage)?;
        if archive.entries[index].file_size > MAX_PROJECT_IMAGE_BYTES {
            return Err(InvalidStructure);
        }
        let blob = archive.read(index)?;
        if blob.len() < 24 || !blob.starts_with(b"\x89PNG\r\n\x1a\n") || &blob[12..16] != b"IHDR" {
            return Err(InvalidStructure);
        }
        let header =
            |i: usize| u32::from_be_bytes([blob[i], blob[i + 1], blob[i + 2], blob[i + 3]]);
        if (header(16) as usize, header(20) as usize) != (width, height) {
            return Err(InvalidStructure);
        }
        let image = match decoded.get(&blob) {
            Some(image) => image.clone(),
            None => {
                let area = (width as u64) * (height as u64);
                if (decoded.len() as u64 + 1) * area > MAX_PROJECT_DECODED_PIXELS {
                    return Err(InvalidStructure);
                }
                let mut png = match decode_png(&blob, PNG_ALLOCATION_LIMIT) {
                    Ok(png) => png,
                    Err(crate::Error::Unsupported(reason)) => {
                        return Err(ProjectError::UnsupportedPng(reason))
                    }
                    Err(_) => return Err(InvalidStructure),
                };
                if (png.width, png.height) != (width, height) {
                    return Err(InvalidStructure);
                }
                qt_premultiplied_roundtrip(&mut png.pixels, width);
                let image = RgbaImage::from_rgba(width, height, &png.pixels)
                    .map_err(|error| ProjectError::Invalid(error.to_string()))?;
                decoded.insert(blob.clone(), image.clone());
                image
            }
        };
        let image = if mask < CURRENT_MASK_FORMAT && !(paper || draft) {
            // 旧形式の白消し。変わる画素がなければ Python と同じく展開した画像を共有する。
            let mut pixels = image.rgba().to_vec();
            if white_to_transparent(&mut pixels) {
                qt_premultiplied_roundtrip(&mut pixels, width);
                RgbaImage::from_rgba(width, height, &pixels)
                    .map_err(|error| ProjectError::Invalid(error.to_string()))?
            } else {
                image
            }
        } else {
            cache.remember(image.generation(), blob);
            image
        };
        images.push(image);
    }
    let plan = plan.map_err(|_| InvalidStructure)?;
    let document = v2::from_plan(&payload.to_value(), &plan, &images)
        .map_err(|error| ProjectError::Invalid(error.to_string()))?;
    for key in ["tracks", "frame_durations"] {
        payload.remove(key);
    }
    Ok(OpenedProject {
        document,
        metadata: payload,
        width,
        height,
        format_version: 2,
        png_cache: cache,
    })
}

/// 保存の設定。
#[derive(Clone, Copy, Debug)]
pub struct SaveOptions<'a> {
    /// アーカイブの先頭に無圧縮で入れるサムネイルの PNG（長辺 512 以下）。
    /// Python は Qt で縮小した画像（project_io._thumbnail_png）。None なら入れない。
    pub thumbnail_png: Option<&'a [u8]>,
    /// 全項目の ZIP の日時（Python は保存時の localtime）。
    pub time: ZipTime,
    /// 符号化する PNG の pHYs（メートルあたりの点）。
    pub dots_per_meter: (u32, u32),
    /// 書いたアーカイブを開き直して検証するか（Python は常に load_project で検証する）。
    pub verify: bool,
}

impl SaveOptions<'_> {
    /// サムネイルなし・既定の解像度・検証ありの設定。
    pub fn new(time: ZipTime) -> Self {
        Self {
            thumbnail_png: None,
            time,
            dots_per_meter: (DEFAULT_DOTS_PER_METER, DEFAULT_DOTS_PER_METER),
            verify: true,
        }
    }
}

/// 文書を v2 の .pman のバイト列にする（project_v2.write_snapshot）。
///
/// metadata は Python の snapshot.metadata（アプリの設定・選択位置などを持つオブジェクト）で、
/// キーの順序を保って project.json に書く。cache があれば同じ世代の PNG を再利用し、
/// 成功したら今回使った世代だけを残す。
pub fn save_project(
    document: &Document,
    metadata: &Json,
    options: &SaveOptions<'_>,
    cache: Option<&mut PngCache>,
) -> Result<Vec<u8>, ProjectError> {
    let invalid = |error: crate::Error| ProjectError::Invalid(error.to_string());
    if !matches!(metadata, Json::Object(_)) {
        return Err(ProjectError::Invalid("metadata はオブジェクトです".into()));
    }
    let projection = v2::project(document, &metadata.to_value()).map_err(invalid)?;
    let payload = &projection.payload;
    let tracks = payload["tracks"].as_array().map_or(&[][..], Vec::as_slice);
    let frames = payload["frame_durations"].as_array().map_or(0, Vec::len);
    let cells: usize = tracks
        .iter()
        .map(|track| {
            track["cells"].as_array().map_or(0, Vec::len)
                + track["compatibility"]["local_states"]
                    .as_array()
                    .map_or(0, Vec::len)
        })
        .sum();
    if tracks.is_empty()
        || tracks.len() > MAX_PROJECT_LAYERS
        || frames == 0
        || frames > MAX_PROJECT_FRAMES
        || tracks.len() * frames > MAX_PROJECT_LAYER_CELLS
        || cells > MAX_PROJECT_LAYER_CELLS
    {
        return Err(ProjectError::TooManyCells);
    }
    let size = document.size();
    if projection.pngs.iter().any(|png| {
        let image = &png.image;
        image.width() == 0 || image.height() == 0 || (image.width(), image.height()) != size
    }) {
        return Err(ProjectError::ImageSizeMismatch);
    }
    let text = metadata
        .dumps(None)
        .map_err(|reason| ProjectError::Invalid(reason.into()))?;
    let ordered = projection.ordered_json(&text).map_err(invalid)?;
    let ordered =
        json::parse(&ordered).map_err(|error| ProjectError::Invalid(error.to_string()))?;
    let metadata_bytes = ordered
        .value
        .dumps(Some(2))
        .map_err(|reason| ProjectError::Invalid(reason.into()))?
        .into_bytes();
    if metadata_bytes.len() as u64 > MAX_PROJECT_METADATA_BYTES {
        return Err(ProjectError::MetadataTooLarge);
    }

    // PngCache.encode_many: 世代ごとに1回だけ符号化する。失敗は書くときに順に報告する。
    let mut own = PngCache::new();
    let cache = cache.unwrap_or(&mut own);
    let mut used = BTreeSet::new();
    let mut encoded: BTreeMap<u64, Option<Vec<u8>>> = BTreeMap::new();
    for png in &projection.pngs {
        let generation = png.image.generation();
        used.insert(generation);
        if encoded.contains_key(&generation) {
            continue;
        }
        let data = match cache.get(generation) {
            Some(data) => Some(data.to_vec()),
            None => {
                let image = &png.image;
                let data = encode_png(
                    image.width(),
                    image.height(),
                    image.rgba(),
                    Some(options.dots_per_meter),
                )
                .ok();
                if let Some(data) = &data {
                    cache.remember(generation, data.clone());
                }
                data
            }
        };
        encoded.insert(generation, data);
    }

    let zip_error = |error: super::zip::ZipError| ProjectError::Archive(error.to_string());
    let mut builder = ZipBuilder::new(Cursor::new(Vec::new()));
    if let Some(thumbnail) = options.thumbnail_png {
        builder
            .add(
                THUMBNAIL_NAME,
                thumbnail,
                CompressionMethod::Stored,
                options.time,
            )
            .map_err(zip_error)?;
    }
    for png in &projection.pngs {
        let data = encoded[&png.image.generation()].as_deref().ok_or_else(|| {
            ProjectError::EncodeFailed {
                path: png.path.clone(),
                initial: png.path.starts_with("initial/"),
            }
        })?;
        builder
            .add(&png.path, data, CompressionMethod::Stored, options.time)
            .map_err(zip_error)?;
    }
    builder
        .add(
            "project.json",
            &metadata_bytes,
            CompressionMethod::Deflated,
            options.time,
        )
        .map_err(zip_error)?;
    let bytes = builder.finish().map_err(zip_error)?.into_inner();
    if options.verify {
        // Python は置き換える前に同じ ZIP・PNG・文書の上限で読み直す。
        open_project(&bytes).map_err(|error| ProjectError::Verify(Box::new(error)))?;
    }
    cache.retain_only(&used);
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    macro_rules! golden {
        ($($name:literal),*) => {
            &[$(($name, include_bytes!(concat!(
                env!("CARGO_MANIFEST_DIR"), "/../../tests/fixtures/projects/v2/", $name, ".pman"
            )))),*]
        };
    }

    const GOLDEN: &[(&str, &[u8])] = golden!(
        "basic-bank-sheet",
        "pending-compat",
        "paper-draft",
        "tween-initial-display"
    );

    fn fixture(name: &str) -> Vec<u8> {
        GOLDEN.iter().find(|(n, _)| *n == name).unwrap().1.to_vec()
    }

    fn time() -> ZipTime {
        ZipTime(2026, 11, 28, 12, 34, 56)
    }

    fn zip(entries: &[(&str, &[u8])]) -> Vec<u8> {
        let mut builder = ZipBuilder::new(Cursor::new(Vec::new()));
        for (name, data) in entries {
            builder
                .add(name, data, CompressionMethod::Deflated, time())
                .unwrap();
        }
        builder.finish().unwrap().into_inner()
    }

    fn red_png(width: usize, height: usize) -> Vec<u8> {
        let pixels = [255, 0, 0, 255].repeat(width * height);
        encode_png(width, height, &pixels, None).unwrap()
    }

    #[test]
    fn golden_roundtrip_is_stable() {
        for (name, bytes) in GOLDEN {
            let mut opened = open_project(bytes).unwrap();
            assert_eq!(opened.format_version, 2);
            assert!(!opened.png_cache.is_empty());
            let options = SaveOptions::new(time());
            let saved = save_project(
                &opened.document,
                &opened.metadata,
                &options,
                Some(&mut opened.png_cache),
            )
            .unwrap();
            // 保存したものを開いて保存し直すと同じバイト列になる。
            let mut again = open_project(&saved).unwrap();
            assert_eq!(again.metadata, opened.metadata);
            let resaved = save_project(
                &again.document,
                &again.metadata,
                &options,
                Some(&mut again.png_cache),
            )
            .unwrap();
            assert_eq!(saved, resaved, "{name}");
            // キャッシュなしでも同じ文書になる。
            let fresh = save_project(&opened.document, &opened.metadata, &options, None).unwrap();
            let reopened = open_project(&fresh).unwrap();
            let a = v2::project(&reopened.document, &reopened.metadata_value()).unwrap();
            let b = v2::project(&opened.document, &opened.metadata_value()).unwrap();
            assert_eq!(a.payload, b.payload);
            assert_eq!(
                a.pngs
                    .iter()
                    .map(|p| (&p.path, p.image.rgba()))
                    .collect::<Vec<_>>(),
                b.pngs
                    .iter()
                    .map(|p| (&p.path, p.image.rgba()))
                    .collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn thumbnail_comes_first_and_stored() {
        let opened = open_project(&fixture("paper-draft")).unwrap();
        let thumbnail = red_png(2, 2);
        let options = SaveOptions {
            thumbnail_png: Some(&thumbnail),
            ..SaveOptions::new(time())
        };
        let saved = save_project(&opened.document, &opened.metadata, &options, None).unwrap();
        let reader = ZipReader::open(Cursor::new(&saved[..])).unwrap();
        let entries = reader.entries();
        assert_eq!(entries[0].name, THUMBNAIL_NAME);
        assert_eq!(entries[0].compression, CompressionMethod::Stored);
        let last = entries.last().unwrap();
        assert_eq!(last.name, "project.json");
        assert_eq!(last.compression, CompressionMethod::Deflated);
    }

    fn project(json: &str, images: &[(&str, Vec<u8>)]) -> Vec<u8> {
        let mut entries: Vec<(&str, &[u8])> = vec![("project.json", json.as_bytes())];
        entries.extend(images.iter().map(|(n, d)| (*n, d.as_slice())));
        zip(&entries)
    }

    const MINIMAL: &str = r#"{"format": "PaintMaskAnimatorProject", "format_version": 2,
        "canvas": {"width": 2, "height": 1}, "frame_durations": [1],
        "tracks": [{"cells": [{"number": 1, "image": "a.png"}], "sheet": [1]}]}"#;

    #[test]
    fn validation_errors() {
        let ok = project(MINIMAL, &[("a.png", red_png(2, 1))]);
        let opened = open_project(&ok).unwrap();
        assert_eq!(opened.metadata.get("saved_app_version"), Some(&Json::Null));
        assert!(opened.metadata.get("tracks").is_none());
        let cases: Vec<(Vec<u8>, ProjectError)> = vec![
            (zip(&[("x", b"")]), ProjectError::MissingMetadata),
            (project("[]", &[]), ProjectError::UnsupportedFormat),
            (
                project(&MINIMAL.replace("\"format_version\": 2", "\"format_version\": true"), &[]),
                ProjectError::UnsupportedFormat,
            ),
            (
                project(&MINIMAL.replace("\"format_version\": 2", "\"format_version\": 1"), &[]),
                ProjectError::LegacyFormat,
            ),
            (
                project(
                    &MINIMAL.replace(
                        "\"format_version\": 2",
                        "\"format_version\": 99999999999999999999999, \"saved_app_version\": \"9.0\"",
                    ),
                    &[],
                ),
                ProjectError::NewerVersion(Some("9.0".into())),
            ),
            (
                project(&MINIMAL.replace("\"sheet\": [1]", "\"sheet\": [1], \"sheet\": [1]"), &[]),
                ProjectError::DuplicateKeys,
            ),
            (project(MINIMAL, &[]), ProjectError::MissingImage),
            (
                project(MINIMAL, &[("a.png", red_png(1, 1))]),
                ProjectError::InvalidStructure,
            ),
            (
                project(&MINIMAL.replace("[1]}", "[1], \"x\": NaN}"), &[("a.png", red_png(2, 1))]),
                ProjectError::InvalidStructure,
            ),
            (
                project(&MINIMAL.replace("\"sheet\": [1]", "\"sheet\": [2]"), &[("a.png", red_png(2, 1))]),
                ProjectError::InvalidStructure,
            ),
        ];
        for (bytes, expected) in cases {
            assert_eq!(open_project(&bytes).unwrap_err(), expected);
        }
        assert!(matches!(
            open_project(&project("{", &[])),
            Err(ProjectError::Json(_))
        ));
        assert!(matches!(
            open_project(b"not a zip"),
            Err(ProjectError::Archive(_))
        ));
    }

    #[test]
    fn duplicate_names_and_encryption_use_python_errors() {
        // zip クレートは重複名を書けないので、2番目の名前を書き換える。
        let mut bytes = zip(&[("project.json", b"{}"), ("projecu.json", b"{}")]);
        let mut at = 0;
        while let Some(i) = bytes[at..].windows(12).position(|w| w == b"projecu.json") {
            bytes[at + i + 6] = b't';
            at += i + 1;
        }
        assert_eq!(
            open_project(&bytes).unwrap_err(),
            ProjectError::DuplicateFileNames
        );
        let mut bytes = zip(&[("project.json", b"{}")]);
        let central = bytes.windows(4).position(|w| w == b"PK\x01\x02").unwrap();
        bytes[central + 8] |= 1;
        bytes[6] |= 1;
        assert_eq!(open_project(&bytes).unwrap_err(), ProjectError::Encrypted);
    }

    #[test]
    fn legacy_mask_migrates_white() {
        let json = MINIMAL.replace(
            "\"format_version\": 2",
            "\"format_version\": 2, \"mask_format\": 1",
        );
        let pixels = [255, 255, 255, 255, 10, 20, 30, 128];
        let png = encode_png(2, 1, &pixels, None).unwrap();
        let opened = open_project(&project(&json, &[("a.png", png)])).unwrap();
        let projection = v2::project(&opened.document, &opened.metadata_value()).unwrap();
        let mut expected = vec![0, 0, 0, 0, 10, 20, 30, 128];
        qt_premultiplied_roundtrip(&mut expected, 2);
        assert_eq!(projection.pngs[0].image.rgba(), expected);
        assert!(opened.png_cache.is_empty());
    }

    #[test]
    fn save_limits() {
        let opened = open_project(&project(MINIMAL, &[("a.png", red_png(2, 1))])).unwrap();
        let options = SaveOptions::new(time());
        let mut metadata = opened.metadata.clone();
        metadata.set(
            "big",
            Json::Str("x".repeat(MAX_PROJECT_METADATA_BYTES as usize)),
        );
        assert_eq!(
            save_project(&opened.document, &metadata, &options, None).unwrap_err(),
            ProjectError::MetadataTooLarge
        );
        metadata.set("big", Json::Float(f64::NAN));
        assert!(matches!(
            save_project(&opened.document, &metadata, &options, None),
            Err(ProjectError::Invalid(_))
        ));
        let mut cache = PngCache::new();
        cache.remember(u64::MAX, vec![1]);
        let saved = save_project(
            &opened.document,
            &opened.metadata,
            &options,
            Some(&mut cache),
        )
        .unwrap();
        assert_eq!(cache.len(), 1);
        assert!(cache.get(u64::MAX).is_none());
        let text = {
            let mut reader = ZipReader::open(Cursor::new(&saved[..])).unwrap();
            let i = reader
                .entries()
                .iter()
                .position(|e| e.name == "project.json")
                .unwrap();
            let mut out = vec![0; reader.entries()[i].file_size as usize];
            reader.read_into(i, &mut out).unwrap();
            String::from_utf8(out).unwrap()
        };
        assert!(text.starts_with(
            "{\n  \"format\": \"PaintMaskAnimatorProject\",\n  \"format_version\": 2,"
        ));
    }
}
