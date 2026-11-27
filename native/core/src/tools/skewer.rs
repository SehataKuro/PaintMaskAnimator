//! 串刺し塗り（バケツの全コマ一括適用）。Python の `canvas_skewer_fill.SkewerFillMixin`。
//!
//! クリックした図形を選択レイヤーの各コマで追いかけて塗る。現在コマから前後へ1コマずつ
//! 進み、直前のコマで塗った領域と重なる図形を選び直す（[`crate::fill::tracking`]）。
//! 追跡の信用度が低いコマでは止まり（[`SkewerStatus::Pending`]）、画面がそのコマを見せて
//! 塗る場所を指定してもらう（クリック・推測の採用・そのコマを飛ばす・その向きの残りを
//! 飛ばす）。すべてのコマが決まってから [`SkewerFill::apply`] で一度に塗る。
//!
//! 既存のセルにだけ塗る。保持セルを新しいキーに分けたり、空セルに絵を作ったりはしない。

use std::collections::{BTreeSet, VecDeque};

use crate::compose::layers::Sources;
use crate::document::access::{ArtworkSlot, ImageField};
use crate::document::changes::ChangeSet;
use crate::document::drawing_commands::DrawingTarget;
use crate::document::model::Document;
use crate::fill::session::{Bounds, Category};
use crate::fill::target::skewer_seed;
use crate::fill::tracking::{track_fill_region, TrackInput};
use crate::image::Rect;
use crate::project::qt_alpha::qt_premultiplied_roundtrip;
use crate::Error;

use super::bucket::{
    bounds_rect, compute_region, crop, crop_rgba, expand, mask_bounds, paint_patch, paste_rgba,
    position_image, position_slot, qt_pixels, reference_pixels, seed_category, truncate,
    BucketSettings, FillResult, PaintSettings, Reason, Rgb,
};

/// 追跡の重なりがこれ未満のコマは、自動では塗らずに指定してもらう。
/// Python の `TRACK_CONFIDENT_IOU`。
pub const TRACK_CONFIDENT_IOU: f64 = 0.5;
/// 手動指定を待つコマで、前のコマの塗り範囲を示す色（ストレート RGBA）。
pub const GUIDE_RGBA: [u8; 4] = [255, 64, 160, 90];
/// 追跡が推測した範囲を示す色（推測を採用できる）。
pub const GUESS_RGBA: [u8; 4] = [40, 140, 255, 110];

/// セル `(コマ, レイヤー)`。コマは露出元（キー）のコマ。
pub type Cell = (usize, usize);

/// 串刺し塗りで見るセル（Python の `frame_scope.resolve_cells` に全コマ×`layer` を渡した値）。
///
/// 保持セルは露出元へ畳み、同じセルは1度だけ、コマ順に返す。
pub fn skewer_cells(document: &Document, layer: usize) -> Result<Vec<Cell>, Error> {
    let sources = Sources::new(document)?;
    let cells: BTreeSet<Cell> = (0..sources.frame_count())
        .filter_map(|frame| sources.key_frame(frame, layer))
        .filter(|_| layer < sources.layer_count())
        .map(|key| (key, layer))
        .collect();
    Ok(cells.into_iter().collect())
}

/// 2つの RGBA の違う画素の外接矩形。同じなら None。
fn changed_rect(before: &[u8], after: &[u8], width: usize, height: usize) -> Option<Rect> {
    let mask: Vec<u8> = before
        .chunks_exact(4)
        .zip(after.chunks_exact(4))
        .map(|(a, b)| u8::from(a != b))
        .collect();
    mask_bounds(&mask, width, height).map(bounds_rect)
}

/// 1コマ分の塗る予定（外接矩形・切り出したマスク・切り出した島）。
#[derive(Clone, Debug)]
struct Planned {
    bounds: Bounds,
    mask: Vec<u8>,
    islands: Vec<(Rgb, Vec<u8>)>,
}

/// 手動指定を待っているコマに重ねる案内。
#[derive(Clone, Copy, Debug)]
pub struct Guide<'a> {
    /// 範囲（キャンバス全体の 0/1）。
    pub mask: &'a [u8],
    /// 範囲を塗る色（ストレート RGBA。推測なら [`GUESS_RGBA`]、前のコマの範囲なら [`GUIDE_RGBA`]）。
    pub rgba: [u8; 4],
    /// 推測（採用できる）か。
    pub is_guess: bool,
}

/// 追跡を進めた結果。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SkewerStatus {
    /// このセルで止まった。画面はこのコマとレイヤーを表示し、指定を待つ。
    Pending(Cell),
    /// すべてのコマが決まった。[`SkewerFill::apply`] で塗る。
    Ready,
}

/// 塗り始めの結果。
pub enum SkewerStart {
    /// 押した所がキャンバスの外（何もしない）。
    Ignored,
    /// このコマには塗れる絵がない（空セルには塗らない）。
    NoArtwork,
    /// 押した所を塗れなかった。
    Failed(Reason),
    /// 始まった。[`SkewerFill::advance`] で追跡を進める。
    Started(Box<SkewerFill>),
}

/// 指定待ちへの操作の結果。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SkewerEvent {
    /// 待っている間に絵やタイムラインが変わった。串刺し塗りを取り消す（画面は捨てる）。
    Invalidated,
    /// 指定待ちでない・推測がないなど、この操作は何もしなかった。
    Ignored,
    /// 別のコマを見ている。待っているセルを表示し直す。
    ShowPending(Cell),
    /// クリックした所を塗れなかった。指定待ちのまま。
    Failed(Reason),
    /// 続きを追いかけた。
    Advanced(SkewerStatus),
}

/// 一度に塗った結果。Undo と再描画に使う。
#[derive(Clone, Debug)]
pub struct SkewerWrite {
    /// 書いたセル（露出元）。同じ絵を共有するセルは `shared` にまとめる。
    pub cell: Cell,
    /// このセルと絵を共有するセル（`cell` を含む）。
    pub shared: Vec<Cell>,
    /// 書き換えた矩形。
    pub rect: Rect,
    /// 書く前の `rect` の画素。
    pub before: Vec<u8>,
}

/// [`SkewerFill::apply`] の結果。
#[derive(Clone, Debug)]
pub enum SkewerApplied {
    /// 待っている間に絵やタイムラインが変わったので塗らなかった。
    Invalidated,
    /// 塗った（`changed_cells` が 0 なら塗れるコマがなかった）。
    Done {
        /// 塗った絵ごとの矩形と書く前の画素。
        writes: Vec<SkewerWrite>,
        /// 塗ったセルの数（共有するセルも数える。Python の `changed_cells`）。
        changed_cells: usize,
        /// 対象外として飛ばしたコマの数。
        skipped: usize,
        /// 書き込んだ RGB（使用色への登録用）。昇順。
        written_colors: Vec<Rgb>,
        /// 塗り始めたときに表示していたコマ（画面はここへ戻す）。
        origin_frame: usize,
        /// 文書の変更通知。
        changes: ChangeSet,
    },
}

/// 串刺し塗り1回分の途中経過（Python の `SkewerFillSession`）。
pub struct SkewerFill {
    layer: usize,
    reference: Category,
    settings: BucketSettings,
    paint: PaintSettings,
    selection: Option<Vec<u8>>,
    origin_frame: usize,
    width: usize,
    height: usize,
    queues: [VecDeque<Cell>; 2],
    previous: [Vec<u8>; 2],
    previous_pixels: [Vec<u8>; 2],
    cells: Vec<Cell>,
    direction: usize,
    regions: Vec<(ArtworkSlot, Planned)>,
    images: Vec<(ArtworkSlot, u64)>,
    skipped: usize,
    pending: Option<Cell>,
    guess: Option<(FillResult, Vec<u8>)>,
}

impl SkewerFill {
    /// 串刺し塗りのクリック（指定待ちでないとき）。今のコマ `frame` の `layer` の
    /// 露出元で押した所を塗る範囲を決め、追跡の準備をする。
    pub fn start(
        document: &Document,
        frame: usize,
        layer: usize,
        settings: BucketSettings,
        paint: PaintSettings,
        selection: Option<Vec<u8>>,
        point: (f64, f64),
    ) -> Result<SkewerStart, Error> {
        let (x, y) = (truncate(point.0), truncate(point.1));
        let current = position_image(document, frame, layer)?;
        if x < 0
            || y < 0
            || x as u64 >= current.width() as u64
            || y as u64 >= current.height() as u64
        {
            return Ok(SkewerStart::Ignored);
        }
        let cells = skewer_cells(document, layer)?;
        let Some(key) = Sources::new(document)?.key_frame(frame, layer) else {
            return Ok(SkewerStart::NoArtwork);
        };
        let Some(pivot) = cells.iter().position(|cell| *cell == (key, layer)) else {
            return Ok(SkewerStart::NoArtwork);
        };
        let image = position_image(document, key, layer)?;
        let (width, height) = (image.width(), image.height());
        let layer_pixels = qt_pixels(&image);
        let pixels = reference_pixels(
            document,
            key,
            layer,
            settings.reference,
            &layer_pixels,
            width,
            height,
        )?
        .into_owned();
        let result = compute_region(
            &pixels,
            width,
            height,
            x,
            y,
            &settings,
            selection.as_deref(),
            None,
        )?;
        if !result.ok() {
            return Ok(SkewerStart::Failed(result.reason));
        }
        let mask = result.region.clone().unwrap_or_default();
        let mut session = Self {
            layer,
            reference: seed_category(&pixels, width, x as usize, y as usize),
            settings,
            paint,
            selection,
            origin_frame: frame,
            width,
            height,
            queues: [
                cells[pivot + 1..].iter().copied().collect(),
                cells[..pivot].iter().rev().copied().collect(),
            ],
            previous: [mask.clone(), mask],
            previous_pixels: [pixels.clone(), pixels],
            cells,
            direction: 0,
            regions: Vec::new(),
            images: Vec::new(),
            skipped: 0,
            pending: None,
            guess: None,
        };
        session.remember(document, key, &result)?;
        Ok(SkewerStart::Started(Box::new(session)))
    }

    /// 指定を待っているセル。
    pub fn pending(&self) -> Option<Cell> {
        self.pending
    }

    /// 指定待ちのコマに、推測した範囲があるか（推測を採用できるか）。
    pub fn has_guess(&self) -> bool {
        self.pending.is_some() && self.guess.is_some()
    }

    /// 指定待ちのコマに重ねる案内（推測、無ければ前のコマの塗り範囲）。
    pub fn guide(&self) -> Option<Guide<'_>> {
        self.pending?;
        Some(match &self.guess {
            Some((result, _)) => Guide {
                mask: result.region.as_deref().unwrap_or_default(),
                rgba: GUESS_RGBA,
                is_guess: true,
            },
            None => Guide {
                mask: &self.previous[self.direction],
                rgba: GUIDE_RGBA,
                is_guess: false,
            },
        })
    }

    /// 塗り始めたときに表示していたコマ。
    pub fn origin_frame(&self) -> usize {
        self.origin_frame
    }

    /// これまでに飛ばしたコマの数。
    pub fn skipped(&self) -> usize {
        self.skipped
    }

    /// 追跡で塗る範囲を探す。`None` なのは「追跡しない設定で塗れなかった」とき。
    fn locate(&self, pixels: &[u8]) -> Result<Option<FillResult>, Error> {
        let previous = &self.previous[self.direction];
        if pixels.len() != previous.len() * 4 {
            return Ok(None);
        }
        let (width, height) = (self.width, self.height);
        if !self.settings.adjacent {
            let Some((x, y)) = skewer_seed(pixels, width, height, self.reference)? else {
                return Ok(None);
            };
            let result = compute_region(
                pixels,
                width,
                height,
                x as i64,
                y as i64,
                &self.settings,
                self.selection.as_deref(),
                None,
            )?;
            return Ok(result.ok().then_some(result));
        }
        if !previous.iter().any(|v| *v != 0) {
            return Ok(Some(FillResult::failed(Reason::Empty, self.reference)));
        }
        let previous_pixels = &self.previous_pixels[self.direction];
        let outcome = track_fill_region(
            TrackInput {
                rgba: pixels,
                previous,
                previous_rgba: (previous_pixels.len() == pixels.len())
                    .then_some(previous_pixels.as_slice()),
                previous_lines: None,
                selection: self.selection.as_deref(),
                dimensions: (width, height),
                reference: self.reference,
            },
            self.settings.prepare_options(),
        )?;
        let mut members = outcome.members.into_iter();
        let mut result = match (members.next(), members.len()) {
            (None, _) => FillResult::failed(Reason::Empty, self.reference),
            (Some(single), 0) => FillResult::from_local(single, width, height),
            (Some(first), _) => {
                // 分かれた領域をまとめる。矩形は領域だけから測る（島は含めない。Python と同じ）。
                let mut union = vec![0u8; width * height];
                let mut islands = Vec::new();
                for member in std::iter::once(first).chain(members) {
                    if let (Some(local), Some(bounds)) = (&member.region, member.bounds) {
                        for (u, v) in union.iter_mut().zip(expand(local, bounds, width, height)) {
                            *u |= v;
                        }
                    }
                    for island in member.islands {
                        islands.push((
                            island.color,
                            expand(&island.mask, island.bounds, width, height),
                        ));
                    }
                }
                let bounds = mask_bounds(&union, width, height);
                FillResult {
                    reason: Reason::Ok,
                    category: self.reference,
                    region: Some(union),
                    bounds,
                    islands,
                    score: None,
                }
            }
        };
        result.score = outcome.score;
        Ok(Some(result))
    }

    fn remember(
        &mut self,
        document: &Document,
        frame: usize,
        result: &FillResult,
    ) -> Result<(), Error> {
        let bounds = result
            .bounds
            .ok_or_else(|| Error::Invalid("塗る範囲がありません".into()))?;
        let region = result.region.as_deref().unwrap_or_default();
        let planned = Planned {
            bounds,
            mask: crop(region, bounds, self.width),
            islands: result
                .islands
                .iter()
                .map(|(rgb, island)| (*rgb, crop(island, bounds, self.width)))
                .collect(),
        };
        let slot = position_slot(document, frame, self.layer)
            .ok_or_else(|| Error::Invalid("対象の絵がありません".into()))?;
        let generation = document
            .artwork_image(slot, ImageField::Image)
            .map_or(0, |image| image.generation());
        match self.regions.iter_mut().find(|(s, _)| *s == slot) {
            Some(entry) => entry.1 = planned,
            None => self.regions.push((slot, planned)),
        }
        match self.images.iter_mut().find(|(s, _)| *s == slot) {
            Some(entry) => entry.1 = generation,
            None => self.images.push((slot, generation)),
        }
        Ok(())
    }

    fn cell_pixels(&self, document: &Document, (frame, layer): Cell) -> Result<Vec<u8>, Error> {
        let image = position_image(document, frame, layer)?;
        let layer_pixels = qt_pixels(&image);
        Ok(reference_pixels(
            document,
            frame,
            layer,
            self.settings.reference,
            &layer_pixels,
            image.width(),
            image.height(),
        )?
        .into_owned())
    }

    /// 次のコマから追跡を続ける。信用度が低いコマに来たら止まる。
    pub fn advance(&mut self, document: &Document) -> Result<SkewerStatus, Error> {
        while self.direction < self.queues.len() {
            while let Some(&cell) = self.queues[self.direction].front() {
                let slot = position_slot(document, cell.0, cell.1);
                if slot.is_some_and(|slot| self.regions.iter().any(|(s, _)| *s == slot)) {
                    self.queues[self.direction].pop_front();
                    continue;
                }
                let pixels = self.cell_pixels(document, cell)?;
                let result = self.locate(&pixels)?;
                self.queues[self.direction].pop_front();
                match result {
                    Some(result)
                        if result.ok()
                            && result
                                .score
                                .is_none_or(|score| score >= TRACK_CONFIDENT_IOU) =>
                    {
                        self.remember(document, cell.0, &result)?;
                        self.previous[self.direction] = result.region.unwrap_or_default();
                        self.previous_pixels[self.direction] = pixels;
                    }
                    None => {
                        // 追跡しない設定（「隣接」OFF）で塗れなかったコマは飛ばす。
                        self.skipped += 1;
                    }
                    Some(result) => {
                        // 自信がないので、このコマは指定してもらう。推測があれば見せる。
                        if result.ok() {
                            self.guess = Some((result, pixels));
                        }
                        self.pending = Some(cell);
                        return Ok(SkewerStatus::Pending(cell));
                    }
                }
            }
            self.direction += 1;
        }
        Ok(SkewerStatus::Ready)
    }

    /// 待っている間にタイムラインや絵が変わっていないか（Python の `_skewer_session_valid`）。
    pub fn is_valid(&self, document: &Document) -> bool {
        let Ok(cells) = skewer_cells(document, self.layer) else {
            return false;
        };
        if cells != self.cells {
            return false;
        }
        let live: Vec<ArtworkSlot> = cells
            .iter()
            .filter_map(|&(frame, layer)| position_slot(document, frame, layer))
            .collect();
        self.images.iter().all(|(slot, generation)| {
            live.contains(slot)
                && document
                    .artwork_image(*slot, ImageField::Image)
                    .map_or(0, |image| image.generation())
                    == *generation
        })
    }

    fn clear_pending(&mut self) {
        self.pending = None;
        self.guess = None;
    }

    /// 指定待ちのコマで押した（Python の `_skewer_resume_with_click`）。`frame` は画面が
    /// 今表示しているコマ。待っているセルの絵を見ていなければ、そのセルを表示し直させる。
    pub fn resume_click(
        &mut self,
        document: &Document,
        frame: usize,
        point: (f64, f64),
    ) -> Result<SkewerEvent, Error> {
        if !self.is_valid(document) {
            return Ok(SkewerEvent::Invalidated);
        }
        let Some(cell) = self.pending else {
            return Ok(SkewerEvent::Ignored);
        };
        let pending_slot = position_slot(document, cell.0, cell.1);
        let shown = Sources::new(document)?
            .key_frame(frame, cell.1)
            .and_then(|key| position_slot(document, key, cell.1));
        if shown.is_none() || shown != pending_slot {
            return Ok(SkewerEvent::ShowPending(cell));
        }
        let (x, y) = (truncate(point.0), truncate(point.1));
        let image = position_image(document, cell.0, cell.1)?;
        if x < 0 || y < 0 || x as u64 >= image.width() as u64 || y as u64 >= image.height() as u64 {
            return Ok(SkewerEvent::Ignored);
        }
        let pixels = self.cell_pixels(document, cell)?;
        let result = compute_region(
            &pixels,
            image.width(),
            image.height(),
            x,
            y,
            &self.settings,
            self.selection.as_deref(),
            None,
        )?;
        if !result.ok() {
            return Ok(SkewerEvent::Failed(result.reason));
        }
        self.remember(document, cell.0, &result)?;
        self.previous[self.direction] = result.region.unwrap_or_default();
        self.previous_pixels[self.direction] = pixels;
        self.clear_pending();
        Ok(SkewerEvent::Advanced(self.advance(document)?))
    }

    /// 指定待ちのコマで、追跡の推測をそのまま採用する（Enter）。
    pub fn accept_guess(&mut self, document: &Document) -> Result<SkewerEvent, Error> {
        let (Some(cell), true) = (self.pending, self.guess.is_some()) else {
            return Ok(SkewerEvent::Ignored);
        };
        if !self.is_valid(document) {
            return Ok(SkewerEvent::Invalidated);
        }
        let (result, pixels) = self.guess.take().expect("推測があることは確かめた");
        self.remember(document, cell.0, &result)?;
        self.previous[self.direction] = result.region.unwrap_or_default();
        self.previous_pixels[self.direction] = pixels;
        self.clear_pending();
        Ok(SkewerEvent::Advanced(self.advance(document)?))
    }

    /// 指定待ちのコマを塗らずに飛ばし、続きを追いかける（Esc）。
    pub fn skip_frame(&mut self, document: &Document) -> Result<SkewerEvent, Error> {
        if self.pending.is_none() {
            return Ok(SkewerEvent::Ignored);
        }
        if !self.is_valid(document) {
            return Ok(SkewerEvent::Invalidated);
        }
        self.skipped += 1;
        self.clear_pending();
        Ok(SkewerEvent::Advanced(self.advance(document)?))
    }

    /// 指定待ちのコマと、同じ向きの残りのコマを塗らずに飛ばす（Shift+Esc）。
    pub fn skip_direction(&mut self, document: &Document) -> Result<SkewerEvent, Error> {
        if self.pending.is_none() {
            return Ok(SkewerEvent::Ignored);
        }
        if !self.is_valid(document) {
            return Ok(SkewerEvent::Invalidated);
        }
        let queue = &mut self.queues[self.direction];
        self.skipped += 1 + queue.len();
        queue.clear();
        self.clear_pending();
        Ok(SkewerEvent::Advanced(self.advance(document)?))
    }

    /// すべてのコマが決まったあとに一度に塗る（Python の `_skewer_apply`）。
    ///
    /// 同じ絵を共有するセルは1回だけ書く。書く前の画素を返すので、画面は Undo 1回で
    /// 全コマを戻せる。
    pub fn apply(self, document: &mut Document) -> Result<SkewerApplied, Error> {
        if !self.is_valid(document) {
            return Ok(SkewerApplied::Invalidated);
        }
        // 同じ絵（窓口）を指すセルをまとめる。先頭のセルで書く。
        let mut groups: Vec<(ArtworkSlot, Vec<Cell>)> = Vec::new();
        for &(frame, layer) in &self.cells {
            let Some(slot) = position_slot(document, frame, layer) else {
                continue;
            };
            match groups.iter_mut().find(|(s, _)| *s == slot) {
                Some((_, cells)) => cells.push((frame, layer)),
                None => groups.push((slot, vec![(frame, layer)])),
            }
        }
        let mut writes = Vec::new();
        let mut written = BTreeSet::new();
        let mut changes = ChangeSet::new();
        let mut changed_cells = 0;
        for (slot, shared) in groups {
            let Some((_, planned)) = self.regions.iter().find(|(s, _)| *s == slot) else {
                continue;
            };
            let (frame, layer) = shared[0];
            let image = position_image(document, frame, layer)?;
            let width = image.width();
            // Python は絵全体を Qt で読み（往復1回）、塗って Qt の画像として差し替える
            // （往復もう1回）。半透明の画素は塗った矩形の外でも変わりうる。
            let mut pixels = qt_pixels(&image);
            let plan = bounds_rect(planned.bounds);
            let mut patch = crop_rgba(&pixels, plan, width);
            written.extend(paint_patch(&mut patch, &planned.mask, self.paint));
            for (rgb, island) in &planned.islands {
                written.extend(paint_patch(
                    &mut patch,
                    island,
                    PaintSettings {
                        color: *rgb,
                        ..self.paint
                    },
                ));
            }
            paste_rgba(&mut pixels, &patch, plan, width);
            qt_premultiplied_roundtrip(&mut pixels, width);
            if let Some(rect) = changed_rect(image.rgba(), &pixels, width, image.height()) {
                let before = image.copy_rgba_rect(rect)?;
                changes.extend(document.write_drawing_patch(
                    layer,
                    DrawingTarget::Frame(frame),
                    rect,
                    &crop_rgba(&pixels, rect, width),
                )?);
                writes.push(SkewerWrite {
                    cell: (frame, layer),
                    shared: shared.clone(),
                    rect,
                    before,
                });
            }
            changed_cells += shared.len();
        }
        Ok(SkewerApplied::Done {
            writes,
            changed_cells,
            skipped: self.skipped,
            written_colors: written.into_iter().collect(),
            origin_frame: self.origin_frame,
            changes,
        })
    }
}
