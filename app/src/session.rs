//! 開いている作品の状態と、画面へ画素を渡す窓口。
//!
//! 文書は pma-core の `Document`。表示は `pma_core::compose` で今のコマを合成した
//! 画像（ストレート RGBA）を持ち、変わった範囲だけを画面へ渡す。
//!
//! 線は pma-core のブラシ（`brush::BrushStroke`）で、選択中のレイヤーの今のコマの絵へ描く。
//! 描いている間も変わった範囲を文書へ書き、その範囲だけ合成し直して画面へ返す。
//! 作品を開いていないときは、表示の画像に丸い点を並べるだけの仮の描き方をする。
//!
//! 文書と履歴はこの `Session` の `project` に一つだけ持つ。タイムラインのパネル
//! （timeline.rs）も同じ文書を読み書きし、編集は `edit_with` を通して同じ履歴に積む。
//! タイムラインの画面の状態（表示の種類・fps・再生範囲・選択など）は `timeline` に持つ。
//!
//! 合成した表示はコマごとに控える（`FrameCache`）。再生ではコマを変えるたびに控えから
//! 取り出すだけにし、文書の中身が変わったら控えを捨てる。

// 使用色パネル（colors.rs）。
use crate::colors::ColorUi;
use crate::history::{Listing, Undo};
use crate::timeline::{Playhead, TimelineUi};
use pma_core::brush::stroke::{BrushSettings, BrushStroke, PointerSample};
use pma_core::compose;
use pma_core::document::model::Document;
use pma_core::image::{Rect, RgbaImage};
use pma_core::project::archive::{self, OpenedProject, PngCache, SaveOptions};
use pma_core::project::json::Json;
use pma_core::project::zip::ZipTime;
use pma_core::used_colors::EditRange;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub struct Project {
    pub document: Document,
    pub metadata: Json,
    pub path: Option<PathBuf>,
    pub png_cache: PngCache,
    pub undo: Undo,
}

/// 描いている途中の線。
struct ActiveStroke {
    stroke: BrushStroke,
    /// 押す前の文書（取り消しに積む）。
    before: Document,
    frame: usize,
    layer: usize,
    label: &'static str,
    /// 使用色パネル（colors.rs）：描ける範囲（#104）と、描き始めの絵。範囲の外は元の絵へ戻して書く。
    range: EditRange,
    base: RgbaImage,
}

pub struct Session {
    pub project: Option<Project>,
    /// タイムラインのパネルの状態（文書と履歴は持たない）。
    pub timeline: TimelineUi,
    active: Option<ActiveStroke>,
    /// 今のコマを合成した表示の画像（ストレート RGBA）。
    pub display: RgbaImage,
    last: Option<Point>,
    cache: FrameCache,
    /// 文書の形（コマ・レイヤー・露出・設定）が変わるたびに増える。
    structure_revision: u64,
    /// 絵が変わるたびに増える。
    pixels_revision: u64,
    /// 使用色パネル（colors.rs）：描画色・使用色パネル・カラーチャート・キャンバスの表示の絞り込み。
    pub colors: ColorUi,
}

/// 合成した表示の控え。コマの番号で引き、同じ絵の重なりのコマは画素を共有する。
#[derive(Default)]
struct FrameCache {
    by_frame: HashMap<usize, RgbaImage>,
    /// 各レイヤーの絵の世代（絵が無ければ None）の並びで引く。
    by_key: HashMap<Vec<Option<u64>>, RgbaImage>,
    bytes: usize,
}

/// 控えの上限（1920×1080 で 64 枚ほど）。超えたら控えを捨てて数え直す。
const CACHE_BYTES: usize = 512 * 1024 * 1024;

impl FrameCache {
    fn clear(&mut self) {
        if !self.by_frame.is_empty() || !self.by_key.is_empty() {
            *self = Self::default();
        }
    }

    fn insert(&mut self, frame: usize, key: Vec<Option<u64>>, image: RgbaImage) {
        if !self.by_key.contains_key(&key) {
            let size = image.width() * image.height() * 4;
            if self.bytes + size > CACHE_BYTES {
                self.clear();
            }
            self.bytes += size;
            self.by_key.insert(key, image.clone());
        }
        self.by_frame.insert(frame, image);
    }
}

/// 変わったかどうかを比べるための、文書の今の印。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Stamp {
    loaded: bool,
    structure: u64,
    pixels: u64,
    frame: usize,
    layer: usize,
}

/// 画面への「文書が変わった」の知らせ（`document-changed` の事件）。
/// 画面のキャンバスとタイムラインは、これを受けて表示を合わせる。
#[derive(Clone, Debug, Serialize)]
pub struct Changed {
    /// 今のコマか選択レイヤーが変わった。
    pub frame: bool,
    /// 文書の形（コマ・レイヤー・露出・設定）が変わった、または作品を開いた。
    pub structure: bool,
    /// 絵が変わった。
    pub pixels: bool,
    /// 知らせのもと：`timeline`・`canvas`・`history`・`playback`・`open` など。
    pub source: &'static str,
    /// 再生中の現在コマ（再生の刻みのときだけ）。
    pub playhead: Option<Playhead>,
}

pub type Shared = Mutex<Session>;

#[derive(Clone, Copy, Debug, Deserialize)]
pub struct Point {
    /// 画像の座標（画素）。
    pub x: f64,
    pub y: f64,
    pub pressure: f64,
    /// ペンなら真（筆圧を使う）。マウスは偽。
    #[serde(default)]
    pub pen: bool,
    /// 入力の時刻（ミリ秒）。
    #[serde(default)]
    pub time: f64,
}

impl Point {
    fn sample(&self) -> PointerSample {
        PointerSample {
            x: self.x,
            y: self.y,
            pressure: self.pen.then_some(self.pressure),
            time_ms: self.time,
        }
    }
}

/// 線の描き方（ツールの設定）。
#[derive(Clone, Debug, Deserialize)]
pub struct StrokeTool {
    pub size: f64,
    pub color: [u8; 3],
    pub eraser: bool,
    pub stabilizer: i64,
    pub zoom: f64,
}

/// 画面へ渡す作品の概要。
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Summary {
    pub width: usize,
    pub height: usize,
    pub frames: usize,
    pub layers: usize,
    pub frame: usize,
    pub layer: usize,
    pub path: Option<String>,
}

impl Session {
    /// 作品を開いていない状態（白い画像だけを見せる）。
    pub fn empty(width: usize, height: usize) -> Self {
        Self {
            project: None,
            timeline: TimelineUi::default(),
            active: None,
            display: white(width, height),
            last: None,
            cache: FrameCache::default(),
            structure_revision: 0,
            pixels_revision: 0,
            colors: ColorUi::default(),
        }
    }

    pub fn open(&mut self, path: &Path) -> Result<(), String> {
        let bytes = std::fs::read(path).map_err(|e| format!("ファイルを読めませんでした：{e}"))?;
        let OpenedProject {
            document,
            metadata,
            png_cache,
            ..
        } = archive::open_project(&bytes).map_err(|e| e.message())?;
        self.install(Project {
            document,
            metadata,
            path: Some(path.to_path_buf()),
            png_cache,
            undo: Undo::default(),
        })
    }

    /// 保存先の無い作品として文書を開く（確かめ用の見本・試験）。
    pub fn open_document(&mut self, document: Document) -> Result<(), String> {
        self.install(Project {
            document,
            metadata: Json::Object(vec![
                (
                    "format".into(),
                    Json::Str("PaintMaskAnimatorProject".into()),
                ),
                ("format_version".into(), Json::Int("2".into())),
            ]),
            path: None,
            png_cache: PngCache::new(),
            undo: Undo::default(),
        })
    }

    /// 作品を入れ替える。描きかけの線・再生・タイムラインの選択は捨てる。
    fn install(&mut self, project: Project) -> Result<(), String> {
        self.active = None;
        self.last = None;
        // 使用色パネル（colors.rs）：描画色・使用色フォルダー・カラーチャートを作品から読む。
        self.colors = ColorUi::from_metadata(&project.metadata);
        self.project = Some(project);
        self.timeline.reset();
        self.touch_structure();
        self.touch_pixels();
        if let Some(p) = &self.project {
            self.timeline.select_current(&p.document);
        }
        self.recompose()
    }

    /// 文書の形が変わった（控えた合成を捨てる）。
    pub fn touch_structure(&mut self) {
        self.structure_revision += 1;
        self.cache.clear();
    }

    /// 絵が変わった（控えた合成を捨てる）。
    pub fn touch_pixels(&mut self) {
        self.pixels_revision += 1;
        self.cache.clear();
    }

    pub fn mark(&self) -> Stamp {
        let (frame, layer) = self
            .project
            .as_ref()
            .map_or((0, 0), |p| current_index(&p.document));
        Stamp {
            loaded: self.project.is_some(),
            structure: self.structure_revision,
            pixels: self.pixels_revision,
            frame,
            layer,
        }
    }

    /// `before` からの変わりよう。何も変わっていなければ None。
    pub fn changes_since(&self, before: Stamp, source: &'static str) -> Option<Changed> {
        let now = self.mark();
        let changed = Changed {
            frame: (now.frame, now.layer) != (before.frame, before.layer),
            structure: now.structure != before.structure || now.loaded != before.loaded,
            pixels: now.pixels != before.pixels,
            source,
            playhead: None,
        };
        (changed.frame || changed.structure || changed.pixels).then_some(changed)
    }

    pub fn save(&mut self, path: Option<&Path>) -> Result<PathBuf, String> {
        let project = self.project.as_mut().ok_or("作品を開いていません")?;
        let path = path
            .map(Path::to_path_buf)
            .or_else(|| project.path.clone())
            .ok_or("保存先がありません")?;
        // 使用色パネル（colors.rs）：描画色・使用色フォルダー・カラーチャートを作品へ書く。
        self.colors.store(&mut project.metadata);
        // サムネイルは合成の画像から作る予定（project_io._thumbnail_png と同じ大きさ）。今は入れない。
        let options = SaveOptions::new(zip_time_now());
        let bytes = archive::save_project(
            &project.document,
            &project.metadata,
            &options,
            Some(&mut project.png_cache),
        )
        .map_err(|e| e.message())?;
        // 途中で失敗しても元のファイルを壊さないよう、隣に書いてから置き換える。
        let tmp = path.with_extension("pman.tmp");
        std::fs::write(&tmp, &bytes).map_err(|e| format!("保存できませんでした：{e}"))?;
        std::fs::rename(&tmp, &path).map_err(|e| format!("保存できませんでした：{e}"))?;
        project.path = Some(path.clone());
        Ok(path)
    }

    pub fn summary(&self) -> Summary {
        let (width, height) = (self.display.width(), self.display.height());
        let Some(p) = &self.project else {
            return Summary {
                width,
                height,
                frames: 0,
                layers: 0,
                frame: 0,
                layer: 0,
                path: None,
            };
        };
        let doc = &p.document;
        let (frame_id, track_id) = doc.selection();
        Summary {
            width,
            height,
            frames: doc.frame_order().len(),
            layers: doc.track_order().len(),
            frame: doc.frame_index(frame_id).unwrap_or(0),
            layer: doc.track_index(track_id).unwrap_or(0),
            path: p.path.as_ref().map(|p| p.display().to_string()),
        }
    }

    /// 今のコマを変える（範囲の外は端に寄せる）。
    pub fn set_frame(&mut self, frame: usize) -> Result<(), String> {
        let Some(p) = &mut self.project else {
            return Ok(());
        };
        let count = p.document.frame_order().len();
        if count == 0 {
            return Ok(());
        }
        let (_, track) = p.document.selection();
        let layer = p.document.track_index(track).unwrap_or(0);
        p.document
            .set_selection_index(frame.min(count - 1), layer)
            .map_err(|e| e.to_string())?;
        // タイムラインの選択も今のコマに合わせる（Qt の select_current）。
        self.timeline.select_current(&p.document);
        self.recompose()
    }

    /// 文書を変える操作をまとめて行う。変える前の文書を履歴に積み、失敗したら元に戻す。
    /// （タイムラインの外の編集の窓口。今はレイヤーのパネルなどがまだ無いので試験だけが使う。）
    #[cfg_attr(not(test), allow(dead_code))]
    pub fn edit<T>(
        &mut self,
        label: &str,
        f: impl FnOnce(&mut Document) -> Result<T, String>,
    ) -> Result<T, String> {
        self.edit_with(Some(label), |doc, _| f(doc).map(|v| (v, true)))
    }

    /// [`Session::edit`] の元。`f` は文書とタイムラインの状態を変え、値と「文書を変えたか」を返す。
    ///
    /// - 変えたなら、`label` があれば変える前の文書を履歴に積み、合成の控えを捨てる。
    ///   `label` が None の操作（表示・不透明度など、Qt でも履歴に積まないもの）は積まない。
    /// - 変えなかったなら（動かす先が同じなど）、履歴には何も積まない。
    /// - 失敗したら、文書もタイムラインの選択も元に戻し、履歴には何も積まない。
    pub fn edit_with<T>(
        &mut self,
        label: Option<&str>,
        f: impl FnOnce(&mut Document, &mut TimelineUi) -> Result<(T, bool), String>,
    ) -> Result<T, String> {
        if self.active.is_some() {
            return Err("線を描いている途中です".into());
        }
        let p = self.project.as_mut().ok_or("作品を開いていません")?;
        let before = p.document.clone();
        let ui = self.timeline.save();
        match f(&mut p.document, &mut self.timeline) {
            Ok((v, changed)) => {
                if changed {
                    if let Some(label) = label {
                        p.undo.record(label, &before);
                    }
                }
                self.timeline.prune_selection(&p.document);
                if changed {
                    self.touch_structure();
                }
                self.recompose()?;
                Ok(v)
            }
            Err(e) => {
                p.document = before;
                self.timeline.restore(ui);
                Err(e)
            }
        }
    }

    pub fn undo(&mut self) -> Result<bool, String> {
        self.step_history(true)
    }

    pub fn redo(&mut self) -> Result<bool, String> {
        self.step_history(false)
    }

    fn step_history(&mut self, back: bool) -> Result<bool, String> {
        if self.active.is_some() {
            // 描いている途中は取り消さない（線の写しと文書がずれる）。
            return Ok(false);
        }
        let Some(p) = &mut self.project else {
            return Ok(false);
        };
        // 使用色パネル（colors.rs）の状態も同じ件で戻す。
        let palette = &mut self.colors.palette;
        let done = if back {
            p.undo.undo(&mut p.document, palette)
        } else {
            p.undo.redo(&mut p.document, palette)
        };
        if done {
            self.timeline.prune_selection(&p.document);
            self.touch_structure();
            self.touch_pixels();
            self.recompose()?;
        }
        Ok(done)
    }

    /// 取り消せるか・やり直せるか。
    pub fn history_state(&self) -> (bool, bool) {
        self.project
            .as_ref()
            .map_or((false, false), |p| (p.undo.can_undo(), p.undo.can_redo()))
    }

    pub fn history(&self) -> Listing {
        match &self.project {
            Some(p) => p.undo.listing(),
            None => Listing {
                undo: vec![],
                redo: vec![],
                branches: 0,
            },
        }
    }

    /// 今のコマの表示を用意する。控えにあればそれを使い、無ければ合成して控える。
    pub fn recompose(&mut self) -> Result<(), String> {
        let Some(p) = &self.project else {
            return Ok(());
        };
        let doc = &p.document;
        let (frame, _) = current_index(doc);
        if let Some(image) = self.cache.by_frame.get(&frame) {
            self.display = image.clone();
            return Ok(());
        }
        // 同じ絵の重なり（保持のコマなど）は、合成し直さない。
        let key: Vec<Option<u64>> = compose::frame_layers(doc, frame)
            .map_err(|e| e.to_string())?
            .iter()
            .map(|layer| {
                layer
                    .as_ref()
                    .and_then(|l| l.image)
                    .map(RgbaImage::generation)
            })
            .collect();
        let image = match self.cache.by_key.get(&key) {
            Some(image) => image.clone(),
            None => {
                // 使用色パネル（colors.rs）の表示の絞り込み・置換プレビューと背景の表示色。
                let (options, background) = (&self.colors.display, self.colors.canvas_background());
                let image = compose::composite(doc, frame, options, background)
                    .map_err(|e| e.to_string())?;
                let (w, h) = (image.width(), image.height());
                RgbaImage::from_rgba(w, h, &image.to_straight_bytes()).map_err(|e| e.to_string())?
            }
        };
        self.cache.insert(frame, key, image.clone());
        self.display = image;
        Ok(())
    }

    /// 控えた合成の数（試験用）。
    #[cfg(test)]
    pub fn cached_frames(&self) -> usize {
        self.cache.by_frame.len()
    }

    /// 線を始める。変わった範囲を返す。
    pub fn stroke_begin(
        &mut self,
        tool: &StrokeTool,
        point: Point,
    ) -> Result<Option<Rect>, String> {
        self.active = None;
        let Some(p) = &self.project else {
            self.last = None;
            return Ok(self.placeholder(&[point]));
        };
        let doc = &p.document;
        let (frame_id, track_id) = doc.selection();
        let frame = doc.frame_index(frame_id).unwrap_or(0);
        let layer = doc.track_index(track_id).unwrap_or(0);
        let layers = compose::frame_layers(doc, frame).map_err(|e| e.to_string())?;
        let (w, h) = doc.size();
        let base = match layers
            .get(layer)
            .and_then(|l| l.as_ref())
            .and_then(|l| l.image)
        {
            Some(image) => image.clone(),
            None => RgbaImage::blank(w, h).map_err(|e| e.to_string())?,
        };
        let settings = BrushSettings {
            size: tool.size,
            color: tool.color,
            eraser: tool.eraser,
            stabilizer: tool.stabilizer,
            zoom: tool.zoom,
            ..BrushSettings::default()
        };
        let before = doc.clone();
        // 描いているあいだは表示をその場で書き換えるので、控えた合成は使えなくなる。
        self.cache.clear();
        let (stroke, rects) =
            BrushStroke::begin(&base, settings, point.sample()).map_err(|e| e.to_string())?;
        let label = if tool.eraser {
            "消しゴム"
        } else {
            "ブラシ"
        };
        self.active = Some(ActiveStroke {
            stroke,
            before,
            frame,
            layer,
            label,
            range: self.colors.palette.edit_range(),
            base,
        });
        self.apply_stroke_rects(&rects)
    }

    /// 線の続きの点。変わった範囲を返す。
    pub fn stroke_move(&mut self, points: &[Point]) -> Result<Option<Rect>, String> {
        let Some(active) = &mut self.active else {
            return Ok(self.placeholder(points));
        };
        let mut rects = Vec::new();
        for point in points {
            rects.extend(
                active
                    .stroke
                    .move_to(point.sample())
                    .map_err(|e| e.to_string())?,
            );
        }
        if let Some(last) = points.last() {
            rects.extend(
                active
                    .stroke
                    .advance_time(last.time)
                    .map_err(|e| e.to_string())?,
            );
        }
        self.apply_stroke_rects(&rects)
    }

    /// 線を終える。文書に何か描いたら取り消しに積む。変わった範囲を返す。
    pub fn stroke_end(&mut self, release: Option<Point>) -> Result<Option<Rect>, String> {
        let Some(active) = self.active.take() else {
            return Ok(None);
        };
        let result = active
            .stroke
            .finish(release.map(|p| p.sample()))
            .map_err(|e| e.to_string())?;
        let ActiveStroke {
            before,
            frame,
            layer,
            label,
            range,
            base,
            ..
        } = active;
        let p = self.project.as_mut().ok_or("作品を開いていません")?;
        let Some(undo) = result.undo else {
            // どこにも描かなかった：文書を押す前に戻す。
            p.document = before;
            self.cache.clear();
            self.recompose()?;
            return Ok(Some(full_rect(&self.display)));
        };
        let mut pixels = result
            .image
            .copy_rgba_rect(undo.rect)
            .map_err(|e| e.to_string())?;
        clip_to_range(&range, &base, undo.rect, &mut pixels)?;
        p.document
            .write_drawing_patch_with_content(frame, layer, undo.rect, &pixels, true)
            .map_err(|e| e.to_string())?;
        p.undo.record(label, &before);
        self.touch_pixels();
        self.recompose_rect(undo.rect)?;
        Ok(Some(undo.rect))
    }

    /// 描いた範囲を文書へ書き、合成し直す。
    fn apply_stroke_rects(&mut self, rects: &[Rect]) -> Result<Option<Rect>, String> {
        let Some(active) = &self.active else {
            return Ok(None);
        };
        let Some(p) = &mut self.project else {
            return Ok(None);
        };
        let mut dirty: Option<Rect> = None;
        for &rect in rects {
            let Some(rect) = clip(rect, &self.display) else {
                continue;
            };
            let mut pixels = active
                .stroke
                .image()
                .copy_rgba_rect(rect)
                .map_err(|e| e.to_string())?;
            clip_to_range(&active.range, &active.base, rect, &mut pixels)?;
            p.document
                .write_drawing_patch_with_content(active.frame, active.layer, rect, &pixels, true)
                .map_err(|e| e.to_string())?;
            dirty = Some(union(dirty, rect));
        }
        if let Some(rect) = dirty {
            self.cache.clear();
            self.recompose_rect(rect)?;
        }
        Ok(dirty)
    }

    /// 範囲だけ合成し直す。
    fn recompose_rect(&mut self, rect: Rect) -> Result<(), String> {
        let Some(p) = &self.project else {
            return Ok(());
        };
        let doc = &p.document;
        let (frame_id, _) = doc.selection();
        let frame = doc.frame_index(frame_id).unwrap_or(0);
        // 使用色パネル（colors.rs）の表示の絞り込み・置換プレビューと背景の表示色。
        let background = self.colors.canvas_background();
        let image = compose::composite_rect(doc, frame, &self.colors.display, background, rect)
            .map_err(|e| e.to_string())?;
        self.display
            .write_rgba(rect, &image.to_straight_bytes())
            .map_err(|e| e.to_string())
    }

    /// 仮の線：前の点から丸い点を並べる（作品を開いていないとき）。
    fn placeholder(&mut self, points: &[Point]) -> Option<Rect> {
        const MAX_RADIUS: f64 = 12.0;
        let mut dirty: Option<Rect> = None;
        for &p in points {
            let from = self.last.unwrap_or(p);
            let steps = (p.x - from.x).hypot(p.y - from.y).ceil().max(1.0) as usize;
            for i in 0..=steps {
                let t = i as f64 / steps as f64;
                let x = from.x + (p.x - from.x) * t;
                let y = from.y + (p.y - from.y) * t;
                let pressure = from.pressure + (p.pressure - from.pressure) * t;
                let r = (MAX_RADIUS * pressure).max(0.5);
                if let Some(rect) = self.stamp(x, y, r) {
                    dirty = Some(union(dirty, rect));
                }
            }
            self.last = Some(p);
        }
        dirty
    }

    fn stamp(&mut self, cx: f64, cy: f64, r: f64) -> Option<Rect> {
        let (w, h) = (self.display.width() as i64, self.display.height() as i64);
        let x0 = ((cx - r).floor() as i64).clamp(0, w);
        let y0 = ((cy - r).floor() as i64).clamp(0, h);
        let x1 = ((cx + r).ceil() as i64 + 1).clamp(0, w);
        let y1 = ((cy + r).ceil() as i64 + 1).clamp(0, h);
        if x0 >= x1 || y0 >= y1 {
            return None;
        }
        let rect = Rect {
            x: x0 as usize,
            y: y0 as usize,
            width: (x1 - x0) as usize,
            height: (y1 - y0) as usize,
        };
        let mut px = self.display.copy_rgba_rect(rect).ok()?;
        for y in y0..y1 {
            for x in x0..x1 {
                let (dx, dy) = (x as f64 + 0.5 - cx, y as f64 + 0.5 - cy);
                if dx * dx + dy * dy <= r * r {
                    let i = (((y - y0) * (x1 - x0) + (x - x0)) * 4) as usize;
                    px[i..i + 4].copy_from_slice(&[0, 0, 0, 255]);
                }
            }
        }
        self.display.write_rgba(rect, &px).ok()?;
        Some(rect)
    }
}

/// 使用色パネル（colors.rs）：描ける範囲の外の画素を、描き始めの絵の画素へ戻す（#104。
/// 選択・鍵の判断は描き始めの絵の色で行い、書くところはここ1か所）。
fn clip_to_range(
    range: &EditRange,
    base: &RgbaImage,
    rect: Rect,
    pixels: &mut [u8],
) -> Result<(), String> {
    if range.is_unrestricted() {
        return Ok(());
    }
    let original = base.copy_rgba_rect(rect).map_err(|e| e.to_string())?;
    pma_core::used_colors::pixels::clip_to_edit_range(&original, pixels, range);
    Ok(())
}

impl Session {
    /// 使用色パネル（colors.rs）：表示の設定が変わったので、控えた合成を捨てて合成し直す。
    pub fn redisplay(&mut self) -> Result<(), String> {
        self.cache.clear();
        self.recompose()
    }

    /// 使用色パネル（colors.rs）：使用色を数え直すかの印（作品・形・絵・選択レイヤー）。
    pub fn scan_stamp(&self) -> (bool, u64, u64, usize) {
        let m = self.mark();
        (m.loaded, m.structure, m.pixels, m.layer)
    }
}

/// 今のコマと選択レイヤーの番号。
pub fn current_index(doc: &Document) -> (usize, usize) {
    let (frame, track) = doc.selection();
    (
        doc.frame_index(frame).unwrap_or(0),
        doc.track_index(track).unwrap_or(0),
    )
}

fn white(width: usize, height: usize) -> RgbaImage {
    RgbaImage::from_rgba(width, height, &vec![255u8; width * height * 4])
        .expect("画像を作れなかった")
}

/// ZIP に書く時刻（手元の時計の UTC。ZIP の時刻は 1980 年より前を持てない）。
fn zip_time_now() -> ZipTime {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (y, m, d) = civil_from_days(days);
    ZipTime(
        y.max(1980) as u16,
        m as u8,
        d as u8,
        (rem / 3600) as u8,
        (rem / 60 % 60) as u8,
        (rem % 60) as u8,
    )
}

/// 1970-01-01 からの日数を年月日にする（Howard Hinnant の方法）。
fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn full_rect(image: &RgbaImage) -> Rect {
    Rect {
        x: 0,
        y: 0,
        width: image.width(),
        height: image.height(),
    }
}

/// 画像の外にはみ出した分を切る。重ならなければ None。
fn clip(rect: Rect, image: &RgbaImage) -> Option<Rect> {
    let x1 = (rect.x + rect.width).min(image.width());
    let y1 = (rect.y + rect.height).min(image.height());
    (rect.x < x1 && rect.y < y1).then(|| Rect {
        x: rect.x,
        y: rect.y,
        width: x1 - rect.x,
        height: y1 - rect.y,
    })
}

pub fn union(a: Option<Rect>, b: Rect) -> Rect {
    let Some(a) = a else { return b };
    let x0 = a.x.min(b.x);
    let y0 = a.y.min(b.y);
    let x1 = (a.x + a.width).max(b.x + b.width);
    let y1 = (a.y + a.height).max(b.y + b.height);
    Rect {
        x: x0,
        y: y0,
        width: x1 - x0,
        height: y1 - y0,
    }
}

/// 範囲と画素を、画面へ渡す1つのバイト列にする：先頭に x・y・幅・高さ（u32、リトル
/// エンディアン）、続けてストレート RGBA の画素。
pub fn pack_rect(image: &RgbaImage, rect: Option<Rect>) -> Vec<u8> {
    let Some(rect) = rect else { return vec![0; 16] };
    let pixels = image.copy_rgba_rect(rect).unwrap_or_default();
    let mut out = Vec::with_capacity(16 + pixels.len());
    for v in [rect.x, rect.y, rect.width, rect.height] {
        out.extend_from_slice(&(v as u32).to_le_bytes());
    }
    out.extend_from_slice(&pixels);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(name: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../tests/fixtures/projects")
            .join(name)
    }

    fn tool() -> StrokeTool {
        StrokeTool {
            size: 5.0,
            color: [0, 0, 0],
            eraser: false,
            stabilizer: 0,
            zoom: 1.0,
        }
    }

    fn pt(x: f64, y: f64) -> Point {
        Point {
            x,
            y,
            pressure: 0.5,
            pen: false,
            time: 0.0,
        }
    }

    #[test]
    fn placeholder_stroke_without_project_paints_black() {
        let mut s = Session::empty(64, 64);
        let first = s.stroke_begin(&tool(), pt(10.0, 10.0)).unwrap().unwrap();
        let rect = union(
            Some(first),
            s.stroke_move(&[pt(30.0, 10.0)]).unwrap().unwrap(),
        );
        assert!(rect.x <= 4 && rect.x + rect.width >= 36);
        let px = s
            .display
            .copy_rgba_rect(Rect {
                x: 20,
                y: 10,
                width: 1,
                height: 1,
            })
            .unwrap();
        assert_eq!(px, vec![0, 0, 0, 255]);
    }

    #[test]
    fn brush_draws_into_document_and_undo_restores_it() {
        let mut s = Session::empty(8, 8);
        s.open(&fixture("v2").join("basic-bank-sheet.pman"))
            .unwrap();
        let (w, h) = (s.display.width() as f64, s.display.height() as f64);
        let before = s.display.rgba().to_vec();
        let (a, b) = (pt(w * 0.2, h * 0.5), pt(w * 0.8, h * 0.5));
        s.stroke_begin(&tool(), a).unwrap();
        s.stroke_move(&[b]).unwrap();
        let rect = s.stroke_end(Some(b)).unwrap().unwrap();
        assert!(rect.width > 0);
        assert_ne!(s.display.rgba(), &before[..]);
        assert_eq!(s.history().undo, vec!["ブラシ".to_string()]);
        // 保存して開き直しても線が残る。
        let drawn = s.display.rgba().to_vec();
        assert!(s.undo().unwrap());
        assert_eq!(s.display.rgba(), &before[..]);
        assert!(s.redo().unwrap());
        assert_eq!(s.display.rgba(), &drawn[..]);
    }

    #[test]
    fn packs_header_then_pixels() {
        let s = Session::empty(8, 8);
        let bytes = pack_rect(
            &s.display,
            Some(Rect {
                x: 1,
                y: 2,
                width: 3,
                height: 4,
            }),
        );
        assert_eq!(&bytes[0..4], &1u32.to_le_bytes());
        assert_eq!(&bytes[12..16], &4u32.to_le_bytes());
        assert_eq!(bytes.len(), 16 + 3 * 4 * 4);
        assert_eq!(pack_rect(&s.display, None), vec![0; 16]);
    }

    #[test]
    fn opens_composes_steps_and_saves_a_v2_project() {
        let dir = fixture("v2");
        let path = std::fs::read_dir(&dir)
            .unwrap()
            .filter_map(|e| e.ok().map(|e| e.path()))
            .find(|p| p.extension().is_some_and(|x| x == "pman"))
            .expect("v2 の見本が無い");
        let mut s = Session::empty(8, 8);
        s.open(&path).unwrap();
        let sum = s.summary();
        assert!(sum.frames > 0 && sum.layers > 0);
        assert_eq!(
            (s.display.width(), s.display.height()),
            (sum.width, sum.height)
        );
        s.set_frame(sum.frames + 5).unwrap();
        assert_eq!(s.summary().frame, sum.frames - 1);
        let out = std::env::temp_dir().join(format!("pma-app-test-{}.pman", std::process::id()));
        s.save(Some(&out)).unwrap();
        let mut again = Session::empty(8, 8);
        again.open(&out).unwrap();
        assert_eq!(again.summary().frames, sum.frames);
        let _ = std::fs::remove_file(out);
    }

    #[test]
    fn edit_records_undo_and_failed_edit_rolls_back() {
        let dir = fixture("v2");
        let mut s = Session::empty(8, 8);
        s.open(&dir.join("basic-bank-sheet.pman")).unwrap();
        let layers = s.summary().layers;
        s.edit("レイヤーを追加", |doc| {
            doc.add_track().map(|_| ()).map_err(|e| e.to_string())
        })
        .unwrap();
        assert_eq!(s.summary().layers, layers + 1);
        assert_eq!(s.history().undo, vec!["レイヤーを追加".to_string()]);
        assert!(s.undo().unwrap());
        assert_eq!(s.summary().layers, layers);
        assert_eq!(s.history().redo, vec!["レイヤーを追加".to_string()]);
        assert!(s.redo().unwrap());
        assert_eq!(s.summary().layers, layers + 1);
        let err = s.edit("失敗", |doc| {
            doc.add_track().map_err(|e| e.to_string())?;
            Err::<(), _>("わざと".to_string())
        });
        assert!(err.is_err());
        assert_eq!(s.summary().layers, layers + 1);
        assert_eq!(s.history().undo.len(), 1);
    }

    #[test]
    fn zip_time_is_a_plausible_date() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(20_000), (2024, 10, 4));
        assert!(zip_time_now().0 >= 2024);
    }
}
