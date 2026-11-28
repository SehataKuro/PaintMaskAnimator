//! CLIP STUDIO（.clip）のアニメーションの読み込み（Qt 版 `clip_animation.py` の
//! `read_clip_animation`・`cell_sequence_numbers`・`_layer_runs` と、`build_pma_frames` の検査）。
//!
//! ファイルの形式（コンテナ・SQLite・曲線・ラスターの展開）は clipfile が読む
//! （`native/clip-import`）。ここはその値（[`ClipMetadata`] と [`RasterSource`]）から、
//! アニメーションフォルダーごとのセルの絵とキーを作る。合成は [`super::clip_render`]。
//! 言葉は Qt と同じ。

use std::collections::{BTreeSet, HashMap, HashSet};
use std::fmt;

use super::clip_render::{apply_layer_color, CelRenderer};
use crate::export::py;
use crate::image::RgbaImage;
use crate::import::image::{to_document_image, MAX_IMAGE_DIMENSION};
use crate::project::archive::{
    MAX_PROJECT_DECODED_PIXELS, MAX_PROJECT_FRAMES, MAX_PROJECT_LAYERS, MAX_PROJECT_LAYER_CELLS,
};

/// 読めなかった理由（利用者に見せる文言）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ClipError(pub String);

impl fmt::Display for ClipError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ClipError {}

impl From<String> for ClipError {
    fn from(value: String) -> Self {
        Self(value)
    }
}

fn fail<T>(text: &str) -> Result<T, ClipError> {
    Err(ClipError(text.to_string()))
}

/// Layer 表の1行。`values` は列名と値（Python の辞書と同じ名前、無い列は入れない）。
#[derive(Clone, Debug, PartialEq)]
pub struct ClipLayerRow {
    pub id: i64,
    pub name: String,
    /// 子のレイヤー（CLIP の兄弟のつながりの順、下から上）。
    pub children: Vec<i64>,
    pub values: Vec<(&'static str, i64)>,
}

impl ClipLayerRow {
    /// 列の値。列が無ければ None。
    pub fn get(&self, name: &str) -> Option<i64> {
        self.values
            .iter()
            .find(|(key, _)| *key == name)
            .map(|(_, value)| *value)
    }

    /// 列の値（無い列は 0、Python の `_row_number` の既定と同じ）。
    pub fn number(&self, name: &str) -> i64 {
        self.number_or(name, 0)
    }

    /// 列の値（無い列は `default`）。
    pub fn number_or(&self, name: &str, default: i64) -> i64 {
        self.get(name).unwrap_or(default)
    }
}

/// セル名の曲線のキー（60Hz の時刻とセル名）。
pub type CelCurve = Vec<(f64, String)>;

/// アニメーションの値（clipfile が読んだもの）。
#[derive(Clone, Debug, PartialEq)]
pub struct ClipMetadata {
    pub width: f64,
    pub height: f64,
    pub fps: f64,
    pub start_frame: f64,
    pub end_frame: f64,
    pub timeline_name: String,
    /// Layer 表の行（MainId の順）。
    pub layers: Vec<ClipLayerRow>,
    /// セルを並べるトラック（レイヤーの MainId とセル名の曲線）。
    pub tracks: Vec<(i64, Vec<CelCurve>)>,
}

/// 展開したラスター。`channels` は 4（RGBA）・1（グレー）・2（グレー＋アルファ）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ClipRaster {
    pub width: u32,
    pub height: u32,
    pub channels: u8,
    pub alpha_only: bool,
    pub default_white: bool,
    pub pixels: Vec<u8>,
}

/// ミップマップの番号からラスターを展開するもの（`reader.raster`）。
pub trait RasterSource {
    fn raster(&mut self, mipmap: i64) -> Result<ClipRaster, ClipError>;
}

/// セルのキー（コマとセル名、空のセル名は空セル）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CelKey {
    pub frame: i64,
    pub tag: String,
}

/// アニメーションフォルダー1つ（PMA の1列）。
#[derive(Clone, Debug)]
pub struct AnimationLayer {
    pub name: String,
    pub keys: Vec<CelKey>,
    /// セル名と絵（フォルダーの上から下の順、Python の辞書の順）。
    pub cels: Vec<(String, RgbaImage)>,
}

/// 読み込んだアニメーション（`ClipAnimationDocument`）。
#[derive(Clone, Debug)]
pub struct ClipAnimation {
    pub source_name: String,
    pub timeline_name: String,
    pub width: usize,
    pub height: usize,
    pub fps: f64,
    pub start_frame: i64,
    pub end_frame: i64,
    pub layers: Vec<AnimationLayer>,
}

impl ClipAnimation {
    /// フォルダーの数。
    pub fn folder_count(&self) -> usize {
        self.layers.len()
    }

    /// フレームの数。
    pub fn frame_count(&self) -> usize {
        (self.end_frame - self.start_frame + 1).max(0) as usize
    }
}

/// 進み具合の知らせ（終わった数, 全体, 文言）。false を返すと中止する。
pub type Progress<'a> = dyn FnMut(usize, usize, String) -> bool + 'a;

/// Python の `round()`（偶数への丸め）を整数にする。
fn py_round(value: f64) -> i64 {
    value.round_ties_even() as i64
}

/// `_numeric_cell_name`：1〜上限の数字だけのセル名ならその番号。
pub fn numeric_cell_name(name: &str) -> Option<i64> {
    let stripped = py::strip(name);
    if !py::all_decimal(stripped) {
        return None;
    }
    let value = py::decimal_to_i64(stripped)?;
    (1..=MAX_PROJECT_FRAMES as i64)
        .contains(&value)
        .then_some(value)
}

/// `cell_sequence_numbers`：フォルダーの全セルの PMA の絵番号（`cels` と同じ順）。
pub fn cell_sequence_numbers<'t>(
    tags: impl IntoIterator<Item = &'t str>,
) -> Result<Vec<i64>, ClipError> {
    let tags: Vec<&str> = tags.into_iter().collect();
    let mut result = vec![0i64; tags.len()];
    let mut used = HashSet::new();
    let mut deferred = Vec::new();
    for (index, tag) in tags.iter().enumerate() {
        match numeric_cell_name(tag) {
            Some(number) if !used.contains(&number) => {
                result[index] = number;
                used.insert(number);
            }
            _ => deferred.push(index),
        }
    }
    let mut next = 1;
    for index in deferred {
        while used.contains(&next) {
            next += 1;
        }
        if next > MAX_PROJECT_FRAMES as i64 {
            return fail("アニメーションセルの絵番号がPMAの上限外です。");
        }
        result[index] = next;
        used.insert(next);
    }
    Ok(result)
}

/// `_layer_runs`：キーを (始まり, 長さ, セル名) の並びにする（範囲の外のキーは前へ寄せる）。
pub fn layer_runs(
    keys: &[CelKey],
    start_frame: i64,
    end_frame: i64,
) -> Vec<(usize, usize, String)> {
    let duration = (end_frame - start_frame + 1) as usize;
    let mut active = String::new();
    let mut events: std::collections::BTreeMap<usize, String> = Default::default();
    for key in keys {
        if key.frame <= start_frame {
            active.clone_from(&key.tag);
        } else if key.frame <= end_frame {
            events.insert((key.frame - start_frame) as usize, key.tag.clone());
        }
    }
    events.insert(0, active);
    let mut runs = Vec::new();
    let mut current_start = 0;
    let mut current_tag = events[&0].clone();
    for (&position, tag) in events.range(1..) {
        if *tag == current_tag {
            continue;
        }
        runs.push((current_start, position - current_start, current_tag));
        current_start = position;
        current_tag = tag.clone();
    }
    runs.push((current_start, duration - current_start, current_tag));
    runs
}

/// `build_pma_frames` の上限の検査（絵を並べる前）。
pub fn check_frames(
    width: usize,
    height: usize,
    start_frame: i64,
    end_frame: i64,
    layers: &[AnimationLayer],
) -> Result<(), ClipError> {
    let duration = end_frame - start_frame + 1;
    if !(1..=MAX_PROJECT_FRAMES as i64).contains(&duration) {
        return fail("タイムラインのフレーム数がPMAの上限外です。");
    }
    if !(1..=MAX_PROJECT_LAYERS).contains(&layers.len()) {
        return fail("アニメーションフォルダー数がPMAの上限外です。");
    }
    if duration as usize * layers.len() > MAX_PROJECT_LAYER_CELLS {
        return fail("タイムラインのレイヤーセル数がPMAの上限を超えています。");
    }
    let images = 1 + layers.iter().map(|l| l.cels.len()).sum::<usize>();
    if (width * height) as u64 * images as u64 > MAX_PROJECT_DECODED_PIXELS {
        return fail("タイムライン展開後の画像サイズがPMAの上限を超えています。");
    }
    for layer in layers {
        for (_, _, tag) in layer_runs(&layer.keys, start_frame, end_frame) {
            if !tag.is_empty() && !layer.cels.iter().any(|(t, _)| *t == tag) {
                return Err(ClipError(format!(
                    "アニメーションフォルダー「{}」のセル「{tag}」を画像化できません。",
                    layer.name
                )));
            }
        }
    }
    cell_numbers_of(layers).map(|_| ())
}

/// 列ごとの `cell_sequence_numbers`。
pub fn cell_numbers_of(layers: &[AnimationLayer]) -> Result<Vec<Vec<i64>>, ClipError> {
    layers
        .iter()
        .map(|layer| cell_sequence_numbers(layer.cels.iter().map(|(t, _)| t.as_str())))
        .collect()
}

fn visible(layer: &ClipLayerRow) -> bool {
    layer.number_or("LayerVisibility", 1) & 1 != 0
}

const CYCLE: &str = "セル内のレイヤー構造が循環しています。";

/// `read_clip_animation` の、ファイルを開いたあとの部分。`source_name` はファイル名。
/// 中止したら Ok(None)。
pub fn read_animation(
    source_name: &str,
    metadata: &ClipMetadata,
    source: &mut dyn RasterSource,
    progress: &mut Progress<'_>,
) -> Result<Option<ClipAnimation>, ClipError> {
    let (width, height) = (py_round(metadata.width), py_round(metadata.height));
    if !(1..=MAX_IMAGE_DIMENSION as i64).contains(&width)
        || !(1..=MAX_IMAGE_DIMENSION as i64).contains(&height)
    {
        return fail(".clipのキャンバスサイズがPMAの上限外です。");
    }
    let (width, height) = (width as usize, height as usize);
    let fps = metadata.fps;
    let start_frame = py_round(metadata.start_frame).max(0);
    let end_frame = py_round(metadata.end_frame).max(0);
    let layers_by_id: HashMap<i64, ClipLayerRow> = metadata
        .layers
        .iter()
        .map(|row| (row.id, row.clone()))
        .collect();
    let lookup = |id: i64| {
        layers_by_id
            .get(&id)
            .ok_or_else(|| ClipError(id.to_string()))
    };
    let mut parents: HashMap<i64, i64> = HashMap::new();
    for row in &metadata.layers {
        for &child in &row.children {
            parents.insert(child, row.id);
        }
    }

    let folder_visible = |mut id: i64| -> Result<bool, ClipError> {
        let mut seen = HashSet::new();
        while let Some(layer) = layers_by_id.get(&id) {
            if !seen.insert(id) {
                return fail(CYCLE);
            }
            if !visible(layer) {
                return Ok(false);
            }
            match parents.get(&id) {
                Some(&parent) => id = parent,
                None => break,
            }
        }
        Ok(true)
    };

    // PMA も下から上へ並べる。木の重なりの順（トラック・レイヤーの ID とは別）を保つ。
    let mut stack_order: HashMap<i64, usize> = HashMap::new();
    let mut pending: Vec<i64> = metadata
        .layers
        .iter()
        .map(|row| row.id)
        .filter(|id| !parents.contains_key(id))
        .collect();
    pending.reverse();
    while let Some(id) = pending.pop() {
        if stack_order.contains_key(&id) {
            return fail(CYCLE);
        }
        let order = stack_order.len();
        stack_order.insert(id, order);
        pending.extend(lookup(id)?.children.iter().rev());
    }

    let mut tracks: Vec<(&ClipLayerRow, &Vec<CelCurve>)> = Vec::new();
    for (id, curves) in &metadata.tracks {
        let row = lookup(*id)?;
        let animation = row.get("AnimationFolder").is_none_or(|v| v != 0);
        if animation && folder_visible(*id)? {
            tracks.push((row, curves));
        }
    }
    let unordered = stack_order.len();
    tracks.sort_by_key(|(row, _)| stack_order.get(&row.id).copied().unwrap_or(unordered));
    if tracks.is_empty() {
        return fail("表示中のアニメーションフォルダーがありません。");
    }
    // 絵を展開する前に確保の上限を確かめる。
    let duration = end_frame - start_frame + 1;
    if !(1..=MAX_PROJECT_FRAMES as i64).contains(&duration) {
        return fail("タイムラインのフレーム数がPMAの上限外です。");
    }
    if !(1..=MAX_PROJECT_LAYERS).contains(&tracks.len()) {
        return fail("アニメーションフォルダー数がPMAの上限外です。");
    }
    if duration as usize * tracks.len() > MAX_PROJECT_LAYER_CELLS {
        return fail("タイムラインのレイヤーセル数がPMAの上限を超えています。");
    }
    let pixels = (width * height) as u64;
    if pixels > MAX_PROJECT_DECODED_PIXELS {
        return fail("タイムライン展開後の画像サイズがPMAの上限を超えています。");
    }
    let mut folder_children = Vec::with_capacity(tracks.len());
    for (folder, _) in &tracks {
        let mut children: Vec<(String, &ClipLayerRow)> = Vec::new();
        for id in folder.children.iter().rev() {
            let child = lookup(*id)?;
            if children.iter().any(|(name, _)| *name == child.name) {
                return Err(ClipError(format!(
                    "アニメーションフォルダー内でセル名「{}」が重複しています。",
                    child.name
                )));
            }
            children.push((child.name.clone(), child));
        }
        folder_children.push(children);
    }
    let total_cels: usize = folder_children.iter().map(Vec::len).sum();
    if total_cels > MAX_PROJECT_LAYER_CELLS
        || pixels * (total_cels as u64 + 1) > MAX_PROJECT_DECODED_PIXELS
    {
        return fail("タイムライン展開後の画像サイズがPMAの上限を超えています。");
    }

    let mut renderer = CelRenderer::new(source, &layers_by_id, width, height);
    let mut parsed: Vec<AnimationLayer> = Vec::new();
    let mut completed = 0;
    for ((folder, candidates), children) in tracks.iter().zip(&folder_children) {
        let has = |tag: &str| children.iter().any(|(name, _)| name == tag);
        let mut raw_keys: &[(f64, String)] = candidates.first().map_or(&[], |c| c.as_slice());
        let mut best: Option<usize> = None;
        for curve in candidates.iter() {
            if curve.iter().all(|(_, tag)| tag.is_empty() || has(tag))
                && best.is_none_or(|len| curve.len() > len)
            {
                best = Some(curve.len());
                raw_keys = curve;
            }
        }
        // 負のキーは 0 コマへ持ち越さない（絵は下の連番として残る）。
        let keys: Vec<CelKey> = raw_keys
            .iter()
            .map(|(time, tag)| CelKey {
                frame: py_round(time * fps / 60.0),
                tag: tag.clone(),
            })
            .filter(|key| key.frame >= 0)
            .collect();
        let missing: BTreeSet<&str> = keys
            .iter()
            .filter(|key| {
                !key.tag.is_empty()
                    && (start_frame..=end_frame).contains(&key.frame)
                    && !has(&key.tag)
            })
            .map(|key| key.tag.as_str())
            .collect();
        if let Some(missing) = missing.first() {
            return Err(ClipError(format!(
                "アニメーションフォルダー「{}」にセル「{missing}」がありません。",
                folder.name
            )));
        }
        // 使わないセル・負のキーだけのセルも描き、PMA の連番として残す。
        let mut cels = Vec::with_capacity(children.len());
        for (tag, child) in children {
            let label = format!("セル画像を読み込み中：{} / {tag}", folder.name);
            if !progress(completed, total_cels, label.clone()) {
                return Ok(None);
            }
            let mut image = renderer.render(child)?;
            let mut ancestor = folder.id;
            while let Some(layer) = layers_by_id.get(&ancestor) {
                image = apply_layer_color(image, layer);
                match parents.get(&ancestor) {
                    Some(&parent) => ancestor = parent,
                    None => break,
                }
            }
            completed += 1;
            if !progress(completed, total_cels, label) {
                return Ok(None);
            }
            let image = to_document_image(&image).map_err(|e| ClipError(e.to_string()))?;
            cels.push((tag.clone(), image));
        }
        let name = if folder.name.is_empty() {
            format!("Animation {}", parsed.len() + 1)
        } else {
            folder.name.clone()
        };
        parsed.push(AnimationLayer { name, keys, cels });
    }
    if !progress(
        total_cels,
        total_cels,
        "タイムラインを構築しています…".into(),
    ) {
        return Ok(None);
    }
    check_frames(width, height, start_frame, end_frame, &parsed)?;
    Ok(Some(ClipAnimation {
        source_name: source_name.to_string(),
        timeline_name: metadata.timeline_name.clone(),
        width,
        height,
        fps,
        start_frame,
        end_frame,
        layers: parsed,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(frame: i64, tag: &str) -> CelKey {
        CelKey {
            frame,
            tag: tag.into(),
        }
    }

    #[test]
    fn sequence_numbers_keep_numeric_names_and_fill_the_rest() {
        let numbers = cell_sequence_numbers(["3", "A", "1", "3", " 2 ", "０４", "0"]).unwrap();
        assert_eq!(numbers, vec![3, 5, 1, 6, 2, 4, 7]);
    }

    #[test]
    fn runs_merge_same_tags_and_carry_keys_before_the_start() {
        let keys = [
            key(0, "1"),
            key(2, "1"),
            key(3, ""),
            key(5, "2"),
            key(9, "3"),
        ];
        assert_eq!(
            layer_runs(&keys, 1, 6),
            vec![
                (0, 2, "1".into()),
                (2, 2, String::new()),
                (4, 2, "2".into())
            ]
        );
        assert_eq!(layer_runs(&[], 0, 2), vec![(0, 3, String::new())]);
    }
}
