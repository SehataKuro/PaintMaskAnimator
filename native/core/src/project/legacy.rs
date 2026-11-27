//! 旧形式（format_version 1）の .pman を読む。
//!
//! Python の project_io.read_project_archive の v1 の経路と load_project に対応する。
//! 検証の順序・上限・エラーの文言を同じにし、文書は
//! [`Document::install_legacy_v1`]（Python の install_legacy_frames）で組み立てる。
//!
//! Python と異なる点:
//! - Python の値の変換（int()・float()・str()）で起きる TypeError・ValueError など
//!   （OperationError でないもの）は [`ProjectError::Invalid`] にする。
//!   str() は文字列・整数・小数・真偽値・null と、それらの入れ子の配列・オブジェクトを Python の
//!   表記にする。int()・float() の文字列は ASCII の数字だけを読む（Python は全角数字も読む）。
//! - 大きすぎる整数は i64 に丸めてから範囲を調べる（同じ範囲のエラーになる）。
use std::collections::BTreeMap;

use super::archive::PngCache;
use super::archive::{
    Archive, ProjectError, CURRENT_MASK_FORMAT, MAX_PROJECT_DECODED_PIXELS, MAX_PROJECT_FRAMES,
    MAX_PROJECT_IMAGE_BYTES, MAX_PROJECT_LAYERS, MAX_PROJECT_LAYER_CELLS, PNG_ALLOCATION_LIMIT,
};
use super::json::{float_repr, Json};
use super::png::decode_png;
use super::qt_alpha::{qt_premultiplied_roundtrip, white_to_transparent};
use crate::document::legacy_v1::{LegacyArchiveInput, LegacyFrameInput, LegacyLayerInput};
use crate::document::model::{CellNumber, Document};
use crate::document::v2::sanitize_tween;
use crate::image::RgbaImage;

/// v1 で開いた結果（metadata は呼び出し側が持つ）。
pub(super) struct LegacyOpened {
    pub document: Document,
    pub width: usize,
    pub height: usize,
    pub png_cache: PngCache,
}

fn fail(message: &str) -> ProjectError {
    ProjectError::Legacy(message.into())
}

fn fail_path(message: &str, path: &str) -> ProjectError {
    ProjectError::Legacy(message.replace("{path}", path))
}

/// Python の例外（TypeError・ValueError など）。
fn python_error(reason: &str) -> ProjectError {
    ProjectError::Invalid(format!("旧形式のプロジェクトの値: {reason}"))
}

fn get<'a>(value: &'a Json, key: &str) -> Option<&'a Json> {
    value.get(key)
}

/// Python の bool(value)。
fn truthy(value: &Json) -> bool {
    match value {
        Json::Null => false,
        Json::Bool(b) => *b,
        Json::Int(digits) => digits != "0",
        Json::Float(f) => *f != 0.0,
        Json::Str(s) => !s.is_empty(),
        Json::Array(items) => !items.is_empty(),
        Json::Object(fields) => !fields.is_empty(),
    }
}

/// bool(data.get(key, default))。
fn flag(data: &Json, key: &str, default: bool) -> bool {
    get(data, key).map_or(default, truthy)
}

fn saturate(digits: &str) -> i64 {
    digits.parse::<i64>().unwrap_or(if digits.starts_with('-') {
        i64::MIN
    } else {
        i64::MAX
    })
}

/// Python の int(str)：前後の空白、符号、数字の間の1つの "_" を許す。
fn parse_int_text(text: &str) -> Option<i64> {
    let text = text.trim();
    let (negative, body) = match text.as_bytes().first() {
        Some(b'-') => (true, &text[1..]),
        Some(b'+') => (false, &text[1..]),
        _ => (false, text),
    };
    if body.is_empty()
        || body.starts_with('_')
        || body.ends_with('_')
        || body.contains("__")
        || !body.bytes().all(|b| b.is_ascii_digit() || b == b'_')
    {
        return None;
    }
    let digits: String = body.chars().filter(|c| *c != '_').collect();
    let digits = if negative {
        format!("-{digits}")
    } else {
        digits
    };
    Some(saturate(&digits))
}

/// Python の int(value)。
fn int(value: &Json) -> Result<i64, ProjectError> {
    match value {
        Json::Int(digits) => Ok(saturate(digits)),
        Json::Bool(b) => Ok(i64::from(*b)),
        Json::Float(f) if f.is_nan() => Err(python_error("NaN を整数にできません")),
        Json::Float(f) if f.is_infinite() => Err(python_error("無限大を整数にできません")),
        // as は飽和する（範囲の検査は呼び出し側）。
        Json::Float(f) => Ok(f.trunc() as i64),
        Json::Str(s) => parse_int_text(s).ok_or_else(|| python_error("整数ではない文字列")),
        _ => Err(python_error("整数にできない値")),
    }
}

/// int(data.get(key, default))。
fn int_field(data: &Json, key: &str, default: i64) -> Result<i64, ProjectError> {
    get(data, key).map_or(Ok(default), int)
}

/// Python の float(value)。
fn float(value: &Json) -> Result<f64, ProjectError> {
    match value {
        Json::Int(digits) => digits
            .parse::<f64>()
            .map_err(|_| python_error("小数にできない整数")),
        Json::Bool(b) => Ok(f64::from(u8::from(*b))),
        Json::Float(f) => Ok(*f),
        Json::Str(s) => {
            let text = s.trim();
            let plain = text.trim_start_matches(['+', '-']);
            // Rust の parse は "infinity" など Python と同じ綴りを大文字小文字を問わず読む。
            // Python が読まない "inf" 以外の英字（"e" の指数を除く）は parse が拒む。
            if plain.is_empty() || plain.contains('_') {
                return Err(python_error("小数ではない文字列"));
            }
            text.parse::<f64>()
                .map_err(|_| python_error("小数ではない文字列"))
        }
        _ => Err(python_error("小数にできない値")),
    }
}

/// Python の repr(str)。
fn string_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::new();
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

fn repr(value: &Json) -> String {
    match value {
        Json::Str(s) => string_repr(s),
        other => py_str(other),
    }
}

/// Python の str(value)。
fn py_str(value: &Json) -> String {
    match value {
        Json::Null => "None".into(),
        Json::Bool(true) => "True".into(),
        Json::Bool(false) => "False".into(),
        Json::Int(digits) => digits.clone(),
        Json::Float(f) if f.is_nan() => "nan".into(),
        Json::Float(f) if f.is_infinite() => if *f > 0.0 { "inf" } else { "-inf" }.into(),
        Json::Float(f) => float_repr(*f).unwrap_or_default(),
        Json::Str(s) => s.clone(),
        Json::Array(items) => format!(
            "[{}]",
            items.iter().map(repr).collect::<Vec<_>>().join(", ")
        ),
        Json::Object(fields) => format!(
            "{{{}}}",
            fields
                .iter()
                .map(|(key, value)| format!("{}: {}", string_repr(key), repr(value)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

/// str(data.get(key)) if data.get(key) is not None else None。
fn optional_str(data: &Json, key: &str) -> Option<String> {
    match get(data, key) {
        None | Some(Json::Null) => None,
        Some(value) => Some(py_str(value)),
    }
}

/// PurePosixPath の検査（絶対パス・".." を含む・拡張子が .png でない）を通るか。
fn safe_png_path(path: &str) -> bool {
    if path.starts_with('/') || path.split('/').any(|part| part == "..") {
        return false;
    }
    let name = path
        .split('/')
        .rev()
        .find(|part| !part.is_empty() && *part != ".")
        .unwrap_or("");
    name.rsplit_once('.')
        .filter(|(stem, suffix)| !stem.is_empty() && !suffix.is_empty())
        .is_some_and(|(_, suffix)| suffix.to_lowercase() == "png")
}

/// tuple(int(v) for v in rgb[:3])（None なら None）。
fn filter_rgb(data: &Json) -> Result<Option<Vec<i64>>, ProjectError> {
    match get(data, "color_filter_rgb") {
        None | Some(Json::Null) => Ok(None),
        Some(Json::Array(items)) => items
            .iter()
            .take(3)
            .map(int)
            .collect::<Result<_, _>>()
            .map(Some),
        Some(Json::Str(s)) => s
            .chars()
            .take(3)
            .map(|c| int(&Json::Str(c.to_string())))
            .collect::<Result<_, _>>()
            .map(Some),
        Some(_) => Err(python_error("色フィルタの色を切り出せません")),
    }
}

/// 展開した PNG（Python の _decode_png。null 画像は None）。
#[derive(Clone)]
enum Decoded {
    Image(RgbaImage),
    Null,
}

fn decode(blob: &[u8]) -> Result<Decoded, ProjectError> {
    match decode_png(blob, PNG_ALLOCATION_LIMIT) {
        Ok(mut png) => {
            qt_premultiplied_roundtrip(&mut png.pixels, png.width.max(1));
            RgbaImage::from_rgba(png.width, png.height, &png.pixels)
                .map(Decoded::Image)
                .map_err(|error| ProjectError::Invalid(error.to_string()))
        }
        Err(crate::Error::Unsupported(reason)) => Err(ProjectError::UnsupportedPng(reason)),
        Err(_) => Ok(Decoded::Null),
    }
}

struct Reader<'a, 'b> {
    archive: &'a mut Archive<'b>,
    width: usize,
    height: usize,
    /// {PNG のバイト列: 画像}（呼び出しをまたいで共有する）。
    decoded: BTreeMap<Vec<u8>, Decoded>,
    /// 旧形式の白消しを画像の世代ごとに1回だけ行う。
    migrated: BTreeMap<u64, RgbaImage>,
    /// 白消しをしていない画像の元の PNG（Python の seeded）。
    seeded: PngCache,
}

impl Reader<'_, '_> {
    fn entry(&self, path: &str) -> Option<usize> {
        self.archive.index.get(path).copied()
    }

    /// _prefetch_pngs：参照が正しい PNG を先に読み、中身ごとに1回だけ展開する。
    fn prefetch(
        &mut self,
        entries: &[Json],
    ) -> Result<BTreeMap<String, (Vec<u8>, Decoded)>, ProjectError> {
        let mut paths: Vec<(String, usize)> = Vec::new();
        for entry in entries {
            if !matches!(entry, Json::Object(_)) {
                continue;
            }
            let path = match get(entry, "image") {
                Some(value) if truthy(value) => py_str(value),
                _ => String::new(),
            };
            if path.is_empty() || paths.iter().any(|(p, _)| *p == path) || !safe_png_path(&path) {
                continue;
            }
            let Some(index) = self.entry(&path) else {
                continue;
            };
            if self.archive.entries[index].file_size > MAX_PROJECT_IMAGE_BYTES {
                continue;
            }
            paths.push((path, index));
        }
        let mut blobs = Vec::with_capacity(paths.len());
        for (_, index) in &paths {
            blobs.push(self.archive.read(*index)?);
        }
        let mut pending: Vec<&Vec<u8>> = Vec::new();
        for blob in &blobs {
            if !self.decoded.contains_key(blob) && !pending.contains(&blob) {
                pending.push(blob);
            }
        }
        let area = (self.width as u64) * (self.height as u64);
        if (self.decoded.len() + pending.len()) as u64 * area > MAX_PROJECT_DECODED_PIXELS {
            return Err(fail("プロジェクトの展開後画像サイズが大きすぎます。"));
        }
        for blob in pending {
            let image = decode(blob)?;
            self.decoded.insert(blob.clone(), image);
        }
        Ok(paths
            .into_iter()
            .zip(blobs)
            .map(|((path, _), blob)| {
                let image = self.decoded[&blob].clone();
                (path, (blob, image))
            })
            .collect())
    }

    fn migrate(&mut self, image: &RgbaImage) -> Result<RgbaImage, ProjectError> {
        if let Some(done) = self.migrated.get(&image.generation()) {
            return Ok(done.clone());
        }
        let mut pixels = image.rgba().to_vec();
        let result = if white_to_transparent(&mut pixels) {
            qt_premultiplied_roundtrip(&mut pixels, self.width);
            RgbaImage::from_rgba(self.width, self.height, &pixels)
                .map_err(|error| ProjectError::Invalid(error.to_string()))?
        } else {
            // 変える画素がなければ Qt の convertToFormat は同じ画像を返す。
            image.clone()
        };
        self.migrated.insert(image.generation(), result.clone());
        Ok(result)
    }

    /// load_initial：保持した色塗り前の初期状態。無ければ None。
    fn initial(&mut self, data: &Json) -> Result<Option<RgbaImage>, ProjectError> {
        let path = match get(data, "initial") {
            Some(value) if truthy(value) => py_str(value),
            _ => return Ok(None),
        };
        if !safe_png_path(&path) {
            return Err(fail("初期状態の画像の参照パスが不正です。"));
        }
        let index = self
            .entry(&path)
            .ok_or_else(|| fail_path("初期状態の画像がありません: {path}", &path))?;
        if self.archive.entries[index].file_size > MAX_PROJECT_IMAGE_BYTES {
            return Err(fail_path("初期状態の画像が大きすぎます: {path}", &path));
        }
        let blob = self.archive.read(index)?;
        // 初期状態は中身が同じでも別々に展開する（Python も共有しない）。
        match decode(&blob)? {
            Decoded::Image(image)
                if (image.width(), image.height()) == (self.width, self.height) =>
            {
                self.seeded.remember(image.generation(), blob);
                Ok(Some(image))
            }
            _ => Err(fail_path("初期状態の画像を復元できません: {path}", &path)),
        }
    }

    fn image(
        &mut self,
        path: &str,
        prefetched: &BTreeMap<String, (Vec<u8>, Decoded)>,
        index: usize,
    ) -> Result<(Vec<u8>, Decoded), ProjectError> {
        if let Some(found) = prefetched.get(path) {
            return Ok(found.clone());
        }
        let blob = self.archive.read(index)?;
        let image = decode(&blob)?;
        Ok((blob, image))
    }

    fn layer(
        &mut self,
        data: &Json,
        prefetched: &BTreeMap<String, (Vec<u8>, Decoded)>,
        needs_white_migration: bool,
    ) -> Result<LegacyLayerInput, ProjectError> {
        if !matches!(data, Json::Object(_)) {
            return Err(fail("レイヤー情報が不正です。"));
        }
        let path = match get(data, "image") {
            Some(value) if truthy(value) => py_str(value),
            _ => return Err(fail("レイヤー画像の参照がありません。")),
        };
        if !safe_png_path(&path) {
            return Err(fail("レイヤー画像の参照パスが不正です。"));
        }
        let index = self
            .entry(&path)
            .ok_or_else(|| fail_path("レイヤー画像がありません: {path}", &path))?;
        if self.archive.entries[index].file_size > MAX_PROJECT_IMAGE_BYTES {
            return Err(fail_path("レイヤー画像が大きすぎます: {path}", &path));
        }
        let (blob, image) = self.image(&path, prefetched, index)?;
        let Decoded::Image(image) = image else {
            return Err(fail_path("レイヤー画像を復元できません: {path}", &path));
        };
        if (image.width(), image.height()) != (self.width, self.height) {
            return Err(fail_path("レイヤー画像のサイズが不正です: {path}", &path));
        }
        let is_draft = flag(data, "is_draft", false);
        let is_paper = flag(data, "is_paper", false);
        // 下書きレイヤーは読み込んだ画素をそのまま保持するため、用紙と同じく白消ししない。
        let image = if needs_white_migration && !is_paper && !is_draft {
            self.migrate(&image)?
        } else {
            self.seeded.remember(image.generation(), blob);
            image
        };
        let exposure = int_field(data, "exposure", 1)?;
        if !(1..=MAX_PROJECT_FRAMES as i64).contains(&exposure) {
            return Err(fail("レイヤーの露出フレーム数が不正です。"));
        }
        let sequence_number = match get(data, "sequence_number") {
            None | Some(Json::Null) => None,
            Some(value) => {
                let number = int(value)?;
                if !(1..=MAX_PROJECT_FRAMES as i64).contains(&number) {
                    return Err(fail("絵番号が範囲外です。"));
                }
                Some(CellNumber::new(number).map_err(|e| ProjectError::Invalid(e.to_string()))?)
            }
        };
        let opacity = get(data, "opacity").map_or(Ok(1.0), float)?;
        if !opacity.is_finite() {
            return Err(fail("レイヤー不透明度が不正です。"));
        }
        let name = get(data, "name").map_or_else(|| "Layer".into(), py_str);
        let visible = flag(data, "visible", true);
        let has_content = flag(data, "has_content", false);
        let color_filter_enabled = flag(data, "color_filter_enabled", false);
        let color_filter_rgb = filter_rgb(data)?;
        // is_blank_key がなければ、絵がなく尺が 2 以上の位置を空キーとみなす。
        let is_blank_key = match get(data, "is_blank_key") {
            Some(value) => truthy(value),
            None => !has_content && exposure.max(1) > 1,
        };
        let sequence_only = flag(data, "sequence_only", false);
        let cell_name = optional_str(data, "cell_name");
        let tween = get(data, "tween").and_then(|value| sanitize_tween(&value.to_value()));
        let tween_member = optional_str(data, "tween_member");
        let initial_image = self.initial(data)?;
        Ok(LegacyLayerInput {
            name,
            image,
            visible,
            opacity: opacity.clamp(0.0, 1.0),
            is_paper,
            has_content,
            exposure: exposure as usize,
            color_filter_enabled,
            color_filter_rgb,
            is_blank_key,
            sequence_number,
            sequence_only,
            is_draft,
            cell_name,
            tween,
            tween_member,
            initial_image,
        })
    }

    fn archived(
        &mut self,
        data: &Json,
        prefetched: &BTreeMap<String, (Vec<u8>, Decoded)>,
        needs_white_migration: bool,
        layer_count: usize,
        seen: &mut Vec<(usize, i64)>,
    ) -> Result<LegacyArchiveInput, ProjectError> {
        if !matches!(data, Json::Object(_)) {
            return Err(fail("連番保管セル情報が不正です。"));
        }
        let layer_index = int_field(data, "layer_index", -1)?;
        let number = int_field(data, "sequence_number", 0)?;
        if !(0 <= layer_index
            && layer_index < layer_count as i64
            && (1..=MAX_PROJECT_FRAMES as i64).contains(&number))
        {
            return Err(fail("連番保管セルのレイヤーまたは絵番号が不正です。"));
        }
        let key = (layer_index as usize, number);
        if seen.contains(&key) {
            return Err(fail("連番保管セルの絵番号が重複しています。"));
        }
        seen.push(key);
        let path = get(data, "image").map_or_else(String::new, py_str);
        if path.is_empty() || !safe_png_path(&path) {
            return Err(fail("連番保管セル画像の参照パスが不正です。"));
        }
        let index = self
            .entry(&path)
            .ok_or_else(|| fail_path("連番保管セル画像がありません: {path}", &path))?;
        if self.archive.entries[index].file_size > MAX_PROJECT_IMAGE_BYTES {
            return Err(fail_path("連番保管セル画像が大きすぎます: {path}", &path));
        }
        let (blob, image) = self.image(&path, prefetched, index)?;
        let image = match image {
            Decoded::Image(image)
                if (image.width(), image.height()) == (self.width, self.height) =>
            {
                image
            }
            _ => return Err(fail_path("連番保管セル画像を復元できません: {path}", &path)),
        };
        let is_draft = flag(data, "is_draft", false);
        let image = if needs_white_migration && !is_draft {
            self.migrate(&image)?
        } else {
            self.seeded.remember(image.generation(), blob);
            image
        };
        let opacity = get(data, "opacity").map_or(Ok(1.0), float)?;
        if !opacity.is_finite() {
            return Err(fail("連番保管セルの不透明度が不正です。"));
        }
        let name = get(data, "name").map_or_else(|| "Layer".into(), py_str);
        let visible = flag(data, "visible", true);
        let has_content = flag(data, "has_content", true);
        let color_filter_enabled = flag(data, "color_filter_enabled", false);
        let color_filter_rgb = filter_rgb(data)?;
        let is_blank_key = flag(data, "is_blank_key", false);
        let cell_name = optional_str(data, "cell_name");
        let initial_image = self.initial(data)?;
        let number = CellNumber::new(number).map_err(|e| ProjectError::Invalid(e.to_string()))?;
        Ok(LegacyArchiveInput {
            layer_index: key.0,
            number,
            layer: LegacyLayerInput {
                name,
                image,
                visible,
                opacity: opacity.clamp(0.0, 1.0),
                is_paper: false,
                has_content,
                exposure: 1,
                color_filter_enabled,
                color_filter_rgb,
                is_blank_key,
                sequence_number: Some(number),
                sequence_only: false,
                is_draft,
                cell_name,
                tween: None,
                tween_member: None,
                initial_image,
            },
        })
    }
}

/// format_version 1 の metadata と ZIP から文書を作る（read_project_archive と load_project）。
pub(super) fn decode_v1(
    metadata: &Json,
    archive: &mut Archive<'_>,
) -> Result<LegacyOpened, ProjectError> {
    let mask_format = match get(metadata, "mask_format") {
        None => 1,
        // int() の TypeError・ValueError は 1 とみなす（OverflowError は Python でも例外）。
        Some(Json::Float(f)) if f.is_infinite() => {
            return Err(python_error("無限大を整数にできません"))
        }
        Some(value) => int(value).unwrap_or(1),
    };
    // 旧形式は消した画素を不透明な白で保存していたので、本当の透明にする。
    let needs_white_migration = mask_format < CURRENT_MASK_FORMAT;

    let canvas = get(metadata, "canvas");
    let (width, height) = match canvas {
        None => (1280, 720),
        Some(canvas @ Json::Object(_)) => (
            int_field(canvas, "width", 1280)?,
            int_field(canvas, "height", 720)?,
        ),
        Some(_) => return Err(python_error("canvas が辞書ではありません")),
    };
    if !((1..=16384).contains(&width) && (1..=16384).contains(&height)) {
        return Err(fail("キャンバスサイズが不正です。"));
    }
    let (width, height) = (width as usize, height as usize);

    let frame_entries = match get(metadata, "frames") {
        Some(Json::Array(items)) if !items.is_empty() => items.as_slice(),
        _ => return Err(fail("フレーム情報がありません。")),
    };
    if frame_entries.len() > MAX_PROJECT_FRAMES {
        return Err(fail("フレーム数が上限を超えています。"));
    }
    let mut expected_layer_count: Option<usize> = None;
    let mut layer_cell_count = 0usize;
    for frame in frame_entries {
        if !matches!(frame, Json::Object(_)) {
            return Err(fail("フレーム情報が不正です。"));
        }
        let layers = match get(frame, "layers") {
            None => &[][..],
            Some(Json::Array(items)) => items.as_slice(),
            Some(_) => return Err(fail("レイヤー情報がありません。")),
        };
        if layers.is_empty() {
            return Err(fail("レイヤー情報がありません。"));
        }
        if layers.len() > MAX_PROJECT_LAYERS {
            return Err(fail("レイヤー数が上限を超えています。"));
        }
        match expected_layer_count {
            None => expected_layer_count = Some(layers.len()),
            Some(count) if count != layers.len() => {
                return Err(fail("フレームごとのレイヤー数が一致していません。"))
            }
            Some(_) => {}
        }
        layer_cell_count += layers.len();
    }
    if layer_cell_count > MAX_PROJECT_LAYER_CELLS {
        return Err(fail("プロジェクトのセル数が上限を超えています。"));
    }
    let layer_count = expected_layer_count.unwrap_or(0);
    let layers_of = |frame: &'_ Json| -> Vec<Json> {
        match get(frame, "layers") {
            Some(Json::Array(items)) => items.clone(),
            _ => Vec::new(),
        }
    };

    let mut reader = Reader {
        archive,
        width,
        height,
        decoded: BTreeMap::new(),
        migrated: BTreeMap::new(),
        seeded: PngCache::new(),
    };
    let all_layers: Vec<Json> = frame_entries.iter().flat_map(layers_of).collect();
    let prefetched = reader.prefetch(&all_layers)?;
    let mut frames = Vec::with_capacity(frame_entries.len());
    for frame in frame_entries {
        let mut layers = Vec::new();
        for data in layers_of(frame) {
            layers.push(reader.layer(&data, &prefetched, needs_white_migration)?);
        }
        let duration = int_field(frame, "duration", 1)?.clamp(1, MAX_PROJECT_FRAMES as i64);
        frames.push(LegacyFrameInput { duration, layers });
    }

    let archive_entries = match get(metadata, "sequence_archive") {
        None => &[][..],
        Some(Json::Array(items)) => items.as_slice(),
        Some(_) => return Err(fail("連番保管セル情報が不正です。")),
    };
    if layer_cell_count + archive_entries.len() > MAX_PROJECT_LAYER_CELLS {
        return Err(fail("プロジェクトのセル数が上限を超えています。"));
    }
    let prefetched = reader.prefetch(archive_entries)?;
    let mut archived = Vec::with_capacity(archive_entries.len());
    let mut seen = Vec::new();
    for data in archive_entries {
        archived.push(reader.archived(
            data,
            &prefetched,
            needs_white_migration,
            layer_count,
            &mut seen,
        )?);
    }

    // load_project：選択位置を範囲に丸める。
    let selection = |key: &str, count: usize| -> Result<usize, ProjectError> {
        let value =
            int_field(metadata, key, 0).map_err(|_| fail("プロジェクトの選択位置が不正です。"))?;
        Ok(value.clamp(0, count as i64 - 1).max(0) as usize)
    };
    let current_frame = selection("current_frame", frames.len())?;
    let active_layer = selection("active_layer_index", frames[current_frame].layers.len())?;
    let document = Document::install_legacy_v1(
        width,
        height,
        &frames,
        &archived,
        current_frame,
        active_layer,
    )
    .map_err(|error| ProjectError::Invalid(error.to_string()))?;
    Ok(LegacyOpened {
        document,
        width,
        height,
        png_cache: reader.seeded,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_conversions() {
        assert_eq!(parse_int_text(" +1_0 "), Some(10));
        assert_eq!(parse_int_text("1__0"), None);
        assert_eq!(parse_int_text("1.5"), None);
        assert_eq!(int(&Json::Float(-2.7)).unwrap(), -2);
        assert!(int(&Json::Null).is_err());
        assert_eq!(
            float(&Json::Str(" Infinity ".into())).unwrap(),
            f64::INFINITY
        );
        assert!(float(&Json::Str("1_0".into())).is_err());
        assert_eq!(py_str(&Json::Float(1.0)), "1.0");
        assert_eq!(
            py_str(&Json::Array(vec![
                Json::Str("a'".into()),
                Json::Bool(true),
                Json::Null
            ])),
            "[\"a'\", True, None]"
        );
        assert!(safe_png_path("a/./b.PNG"));
        assert!(!safe_png_path("/a.png"));
        assert!(!safe_png_path("a/../b.png"));
        assert!(!safe_png_path(".png"));
    }
}
