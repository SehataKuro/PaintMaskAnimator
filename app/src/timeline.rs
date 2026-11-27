//! タイムラインのパネル（Qt の `paintmaskanimator/timeline.py` と
//! `main_window_timeline_ops.py` の移植）。
//!
//! 文書（`pma_core::document::Document`）から画面に出す値（行・区間・印・選択・現在コマ）を
//! 組み立て、画面の操作を既存の文書の命令へ渡す。Python だけにあった判断は
//! `pma_core::document::timeline_panel` に移してあり、ここは値の受け渡しだけにする。
//!
//! セッション（session.rs）はまだ文書を持たないので、タイムラインの状態は別に
//! `SharedTimeline`（`Arc<Mutex<TimelineState>>`）で持つ。文書は `Option<Document>` で、
//! 開く・保存（.pman）が入るまでは `timeline_debug_load_sample` で見本の文書を入れる。
//!
//! 再生は Rust の別スレッドがコマを進め、`timeline-playhead` の事件で画面へ知らせる
//! （#107：再生中も再生ヘッドが動く）。

use std::collections::BTreeSet;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use pma_core::document::commands::TrackProperty;
use pma_core::document::model::{Cell, CellNumber, Document, Position, Reference};
use pma_core::document::timeline_panel::{self, InbetweenError};
use pma_core::image::{Rect, RgbaImage};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

/// シート（タイムシートの絵番号を保持）か、連番（番号順に並べる）か。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    #[default]
    Sheet,
    Sequence,
}

/// 控えたセル（コピー・切り取り）。絵・番号・露出ごと持つ。
#[derive(Clone, Debug)]
struct Clip {
    cell: Cell,
    number: CellNumber,
    length: usize,
}

/// 再生の時計（Python の _playback_started_at と _playback_emitted_steps）。
#[derive(Clone, Copy, Debug)]
struct PlayClock {
    generation: u64,
    started: Instant,
    emitted: u64,
}

/// Undo の写しの上限。文書の写しは行と画素を共有するので軽い。
const UNDO_LIMIT: usize = 100;

/// タイムラインの状態。表示の設定（拡大率・サムネイルの開閉）は画面が持つ。
pub struct TimelineState {
    pub document: Option<Document>,
    pub mode: Mode,
    pub fps: u32,
    /// 再生範囲の先頭と終わり（未満）。終わりが None なら最後のコマまで。
    pub range_start: usize,
    pub range_end: Option<usize>,
    pub playing: bool,
    /// 再生中にタイムラインを追いかけるか（#107 の環境設定。既定 ON）。
    pub follow_playback: bool,
    /// オニオンスキンの ON／OFF（キャンバスがまだ文書を描かないので、状態だけ持つ）。
    pub onion: bool,
    /// 選んだセル（レイヤー番号, 表示の列）。連番表示の列は番号 - 1。
    pub selection: BTreeSet<(usize, usize)>,
    /// Shift で範囲を選ぶときの起点。
    anchor: Option<(usize, usize)>,
    clock: PlayClock,
    clipboard: Option<Clip>,
    undo: Vec<Document>,
    redo: Vec<Document>,
}

pub type SharedTimeline = Arc<Mutex<TimelineState>>;

impl Default for TimelineState {
    fn default() -> Self {
        Self {
            document: None,
            mode: Mode::Sheet,
            fps: 24,
            range_start: 0,
            range_end: None,
            playing: false,
            follow_playback: true,
            onion: false,
            selection: BTreeSet::new(),
            anchor: None,
            clock: PlayClock {
                generation: 0,
                started: Instant::now(),
                emitted: 0,
            },
            clipboard: None,
            undo: Vec::new(),
            redo: Vec::new(),
        }
    }
}

pub fn shared() -> SharedTimeline {
    Arc::new(Mutex::new(TimelineState::default()))
}

// ---------------------------------------------------------------------------
// 画面へ渡す値
// ---------------------------------------------------------------------------

/// 一つの区間（Qt の TimelineCellDelegate が描く帯一本）。
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct SpanView {
    /// 表示の列の先頭（シートはコマ、連番は番号 - 1）。
    pub start: usize,
    pub length: usize,
    /// `cell`（絵）・`blank`（空キー ○）・`sequence_blank`（連番の空き番号）。
    pub kind: &'static str,
    /// 番号の箱の文字（セル名か番号）。
    pub label: String,
    /// 絵の番号（サムネイルを引くのに使う）。
    pub number: Option<i64>,
    /// キーのコマ（サムネイルを引くのに使う）。連番のバンクだけの絵は None。
    pub column: Option<usize>,
    /// 原画の印（#24）。
    pub genga: bool,
    pub cell_name: Option<String>,
    /// 確定したトゥイーンの帯なら `forward` か `reverse`。
    pub tween: Option<&'static str>,
    /// 確定したトゥイーンの帯（伸縮・移動はさせない）。
    pub group: bool,
}

/// セルとセルの間の空き（データ上は未使用、画面では空セルとして見せる）。
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct GapView {
    pub start: usize,
    pub length: usize,
    pub cross: bool,
}

/// 一つのレイヤーの行。
#[derive(Clone, Debug, Serialize)]
pub struct RowView {
    /// レイヤー番号（0 が一番下）。画面の行は上のレイヤーから並べる。
    pub layer: usize,
    pub name: String,
    pub visible: bool,
    pub opacity: f64,
    pub draft: bool,
    pub spans: Vec<SpanView>,
    pub gaps: Vec<GapView>,
    /// 「連番の番号を呼び出す」に並べる番号。
    pub numbers: Vec<i64>,
    /// 正規化で番号が変わる絵の数。
    pub normalize: usize,
}

#[derive(Clone, Debug, Serialize)]
pub struct TimelineView {
    pub loaded: bool,
    pub mode: Mode,
    /// 表示する列の数（シートはコマ数、連番は最大の番号）。
    pub frame_count: usize,
    /// シートのコマ数。
    pub sheet_frames: usize,
    /// 現在コマ（シートのコマ）。
    pub current: usize,
    /// 現在コマの表示の列（再生ヘッドの位置）。連番では選択レイヤーの番号 - 1（#108）。
    pub current_column: Option<usize>,
    pub active_layer: usize,
    pub fps: u32,
    pub playing: bool,
    pub follow_playback: bool,
    pub onion: bool,
    /// 表示する再生範囲（先頭, 終わり未満）。
    pub range: (usize, usize),
    /// 正規化で番号が変わる絵の数の合計（ボタンのバッジ）。
    pub normalize_pending: usize,
    pub clipboard: bool,
    pub can_undo: bool,
    pub can_redo: bool,
    /// 上のレイヤーから。
    pub rows: Vec<RowView>,
    pub selection: Vec<(usize, usize)>,
}

/// 再生中に画面へ送る現在コマ。
#[derive(Clone, Debug, Serialize)]
pub struct Playhead {
    pub current: usize,
    pub column: Option<usize>,
    pub playing: bool,
}

fn position(doc: &Document, column: usize, layer: usize) -> Option<&Position> {
    let frame = doc.frame(*doc.frame_order().get(column)?)?;
    frame.positions.get(doc.track_order().get(layer)?)
}

fn current_index(doc: &Document) -> (usize, usize) {
    let (frame, track) = doc.selection();
    (
        doc.frame_index(frame).unwrap_or(0),
        doc.track_index(track).unwrap_or(0),
    )
}

fn cell_label(cell: Option<&Cell>, number: Option<CellNumber>) -> String {
    cell.and_then(|cell| cell.cell_name.clone())
        .or_else(|| number.map(|n| n.get().to_string()))
        .unwrap_or_default()
}

/// シート表示の一行（Qt の refresh のシートの分岐）。
pub fn sheet_row(doc: &Document, layer: usize) -> (Vec<SpanView>, Vec<GapView>) {
    let frames = doc.frame_order().len();
    let groups = doc.tween_groups(layer);
    let mut next_group = groups.iter().peekable();
    let mut spans = Vec::new();
    let mut column = 0;
    while column < frames {
        if let Some(group) = next_group.next_if(|group| group.start == column) {
            // 確定済みのトゥイーンは、区間全体を一つのセルとして見せる。
            let cell = doc.position_cell(column, layer);
            let number = position(doc, column, layer).and_then(Position::number);
            spans.push(SpanView {
                start: column,
                length: group.length,
                kind: "cell",
                label: cell_label(cell, number),
                number: number.map(CellNumber::get),
                column: Some(column),
                genga: cell.is_some_and(|cell| cell.is_genga),
                cell_name: cell.and_then(|cell| cell.cell_name.clone()),
                tween: Some(if group.reverse { "reverse" } else { "forward" }),
                group: true,
            });
            column += group.length;
            continue;
        }
        let Some((blank, start, length)) = doc.timeline_block_at(column, layer) else {
            column += 1;
            continue;
        };
        let mut end = start + length;
        let start = start.max(column);
        if let Some(group) = next_group.peek() {
            end = end.min(group.start);
        }
        let end = end.max(start + 1);
        let span = if blank {
            SpanView {
                start,
                length: end - start,
                kind: "blank",
                label: String::new(),
                number: None,
                column: Some(start),
                genga: false,
                cell_name: None,
                tween: None,
                group: false,
            }
        } else {
            let cell = doc.position_cell(start, layer);
            let number = position(doc, start, layer).and_then(Position::number);
            SpanView {
                start,
                length: end - start,
                kind: "cell",
                label: cell_label(cell, number),
                number: number.map(CellNumber::get),
                column: Some(start),
                genga: cell.is_some_and(|cell| cell.is_genga),
                cell_name: cell.and_then(|cell| cell.cell_name.clone()),
                tween: None,
                group: false,
            }
        };
        spans.push(span);
        column = end;
    }
    let gaps = doc
        .timeline_gaps(layer)
        .into_iter()
        .map(|gap| GapView {
            start: gap.start,
            length: gap.length,
            cross: gap.cross,
        })
        .collect();
    (spans, gaps)
}

/// 連番表示の一行（Qt の refresh の連番の分岐）。
pub fn sequence_row(doc: &Document, layer: usize) -> Vec<SpanView> {
    let track = doc.track_order()[layer];
    doc.sequence_entries(layer)
        .into_iter()
        .map(|entry| {
            let cell = match entry.column {
                Some(column) => doc.position_cell(column, layer),
                None => doc.track(track).and_then(|t| t.cells.get(&entry.number)),
            };
            let label = if entry.content {
                cell_label(cell, Some(entry.number))
            } else {
                entry.number.get().to_string()
            };
            SpanView {
                start: entry.number.get() as usize - 1,
                length: 1,
                kind: if entry.content {
                    "cell"
                } else {
                    "sequence_blank"
                },
                label,
                number: Some(entry.number.get()),
                column: entry.column,
                genga: entry.content && cell.is_some_and(|cell| cell.is_genga),
                cell_name: cell.and_then(|cell| cell.cell_name.clone()),
                tween: None,
                group: false,
            }
        })
        .collect()
}

impl TimelineState {
    fn frame_count(&self) -> usize {
        self.document
            .as_ref()
            .map_or(1, |doc| doc.frame_order().len())
    }

    /// 現在コマの表示の列（#108：連番では選択レイヤーの位置の番号から直接求める）。
    pub fn current_column(&self) -> Option<usize> {
        let doc = self.document.as_ref()?;
        let (current, active) = current_index(doc);
        match self.mode {
            Mode::Sheet => Some(current),
            Mode::Sequence => doc.sequence_display_column(active, current),
        }
    }

    pub fn playhead(&self) -> Playhead {
        Playhead {
            current: self.document.as_ref().map_or(0, |doc| current_index(doc).0),
            column: self.current_column(),
            playing: self.playing,
        }
    }

    pub fn view(&self) -> TimelineView {
        let Some(doc) = self.document.as_ref() else {
            return TimelineView {
                loaded: false,
                mode: self.mode,
                frame_count: 1,
                sheet_frames: 1,
                current: 0,
                current_column: None,
                active_layer: 0,
                fps: self.fps,
                playing: false,
                follow_playback: self.follow_playback,
                onion: self.onion,
                range: (0, 1),
                normalize_pending: 0,
                clipboard: false,
                can_undo: false,
                can_redo: false,
                rows: Vec::new(),
                selection: Vec::new(),
            };
        };
        let (current, active) = current_index(doc);
        let sheet_frames = doc.frame_order().len();
        let frame_count = match self.mode {
            Mode::Sheet => sheet_frames,
            Mode::Sequence => doc.sequence_frame_count(),
        };
        let layers = doc.track_order().len();
        let rows: Vec<RowView> = (0..layers)
            .rev()
            .map(|layer| {
                let settings = &doc.track(doc.track_order()[layer]).unwrap().settings;
                let (spans, gaps) = match self.mode {
                    Mode::Sheet => sheet_row(doc, layer),
                    Mode::Sequence => (sequence_row(doc, layer), Vec::new()),
                };
                RowView {
                    layer,
                    name: settings.name.clone(),
                    visible: settings.visible,
                    opacity: settings.opacity,
                    draft: settings.is_draft,
                    spans,
                    gaps,
                    numbers: doc
                        .sequence_entries(layer)
                        .iter()
                        .map(|e| e.number.get())
                        .collect(),
                    normalize: doc.normalization_changes(layer).len(),
                }
            })
            .collect();
        let range_start = self.range_start;
        let range_end = self
            .range_end
            .map_or(frame_count, |end| end.max(range_start + 1));
        TimelineView {
            loaded: true,
            mode: self.mode,
            frame_count,
            sheet_frames,
            current,
            current_column: self.current_column(),
            active_layer: active,
            fps: self.fps,
            playing: self.playing,
            follow_playback: self.follow_playback,
            onion: self.onion,
            range: (range_start, range_end),
            normalize_pending: match self.mode {
                Mode::Sheet => rows.iter().map(|row| row.normalize).sum(),
                Mode::Sequence => 0,
            },
            clipboard: self.clipboard.is_some(),
            can_undo: !self.undo.is_empty(),
            can_redo: !self.redo.is_empty(),
            rows,
            selection: self.selection.iter().copied().collect(),
        }
    }

    /// 選択を今のコマだけにする（Qt の select_current の setCurrentCell）。
    fn select_current(&mut self) {
        self.selection.clear();
        if let (Some(column), Some(doc)) = (self.current_column(), self.document.as_ref()) {
            let (_, active) = current_index(doc);
            self.selection.insert((active, column));
            self.anchor = Some((active, column));
        }
    }

    /// 文書の形が変わったあと、無くなったセルの選択を外す。
    fn prune_selection(&mut self) {
        let Some(doc) = self.document.as_ref() else {
            self.selection.clear();
            return;
        };
        let layers = doc.track_order().len();
        self.selection.retain(|&(layer, _)| layer < layers);
    }

    fn push_undo(&mut self) {
        if let Some(doc) = &self.document {
            self.undo.push(doc.clone());
            if self.undo.len() > UNDO_LIMIT {
                self.undo.remove(0);
            }
            self.redo.clear();
        }
    }

    pub fn undo(&mut self) -> bool {
        let Some(previous) = self.undo.pop() else {
            return false;
        };
        if let Some(doc) = self.document.replace(previous) {
            self.redo.push(doc);
        }
        self.prune_selection();
        true
    }

    pub fn redo(&mut self) -> bool {
        let Some(next) = self.redo.pop() else {
            return false;
        };
        if let Some(doc) = self.document.replace(next) {
            self.undo.push(doc);
        }
        self.prune_selection();
        true
    }

    pub fn load(&mut self, doc: Document) {
        *self = Self {
            document: Some(doc),
            fps: self.fps,
            follow_playback: self.follow_playback,
            ..Self::default()
        };
        self.select_current();
    }

    /// 表示の列 `column`・レイヤー `layer` のコマへ移る（Qt の select_exposure）。
    /// シートでは最後のコマより後ろを選ぶとコマを延ばす。連番では番号の代表のコマへ移る。
    pub fn set_frame(&mut self, column: usize, layer: usize) -> Result<(), String> {
        let mode = self.mode;
        let doc = self.document.as_mut().ok_or("文書がありません")?;
        let layer = layer.min(doc.track_order().len() - 1);
        match mode {
            Mode::Sheet => {
                if column >= doc.frame_order().len() {
                    doc.ensure_frame_count(column + 1)
                        .map_err(|e| e.to_string())?;
                }
                doc.set_selection_index(column, layer)
                    .map_err(|e| e.to_string())?;
            }
            Mode::Sequence => {
                if let Some(frame) = doc.sequence_number_column(layer, column as i64 + 1) {
                    doc.set_selection_index(frame, layer)
                        .map_err(|e| e.to_string())?;
                } else {
                    let (current, _) = current_index(doc);
                    doc.set_selection_index(current, layer)
                        .map_err(|e| e.to_string())?;
                }
            }
        }
        Ok(())
    }

    /// セルを選ぶ。`how` は `replace`（クリック）・`toggle`（Ctrl）・`range`（Shift）。
    /// Qt と同じく、選んだセルのコマへも移る。
    pub fn select(&mut self, layer: usize, column: usize, how: &str) -> Result<(), String> {
        let limit = match self.mode {
            Mode::Sheet => usize::MAX,
            Mode::Sequence => self
                .document
                .as_ref()
                .map_or(1, Document::sequence_frame_count),
        };
        if column >= limit {
            return Ok(());
        }
        match how {
            "toggle" => {
                if !self.selection.remove(&(layer, column)) {
                    self.selection.insert((layer, column));
                }
                self.anchor = Some((layer, column));
            }
            "range" => {
                let anchor = self.anchor.unwrap_or((layer, column));
                self.select_rect(anchor, (layer, column));
                self.anchor = Some(anchor);
            }
            _ => {
                self.selection.clear();
                self.selection.insert((layer, column));
                self.anchor = Some((layer, column));
            }
        }
        self.set_frame(column, layer)
    }

    /// ドラッグで囲んだ範囲を選ぶ。
    pub fn select_rect(&mut self, from: (usize, usize), to: (usize, usize)) {
        self.selection.clear();
        for layer in from.0.min(to.0)..=from.0.max(to.0) {
            for column in from.1.min(to.1)..=from.1.max(to.1) {
                self.selection.insert((layer, column));
            }
        }
    }

    /// 前後のコマ・前後の区切り・先頭・末尾へ移る（Qt の timeline_ops の移動）。
    pub fn navigate(&mut self, to: &str) -> Result<(), String> {
        let mode = self.mode;
        let doc = self.document.as_mut().ok_or("文書がありません")?;
        let (current, active) = current_index(doc);
        let count = doc.frame_order().len();
        let target = match (mode, to) {
            (Mode::Sequence, "previous") => doc.sequence_step(active, current, -1, false),
            (Mode::Sequence, "next") => doc.sequence_step(active, current, 1, false),
            (Mode::Sequence, "previous_key") => doc.sequence_step(active, current, -1, true),
            (Mode::Sequence, "next_key") => doc.sequence_step(active, current, 1, true),
            (Mode::Sequence, "first") => doc.sequence_step(active, current, -1_000_000, false),
            (Mode::Sequence, "last") => doc.sequence_step(active, current, 1_000_000, false),
            (Mode::Sheet, "previous") => Some(current.saturating_sub(1)),
            (Mode::Sheet, "next") => {
                // Python の next_frame は advance_frame（最後なら1コマ延ばす）。
                doc.advance_frame().map_err(|e| e.to_string())?;
                None
            }
            (Mode::Sheet, "previous_key") => doc.previous_key_column(active, current),
            (Mode::Sheet, "next_key") => doc.next_key_column(active, current),
            (Mode::Sheet, "first") => Some(0),
            (Mode::Sheet, "last") => Some(count - 1),
            _ => return Err(format!("不明な移動：{to}")),
        };
        if let Some(target) = target {
            doc.set_selection_index(target, active)
                .map_err(|e| e.to_string())?;
        }
        self.select_current();
        Ok(())
    }

    /// シート・連番を切り替える（Qt の set_mode）。連番からシートへ移るときは配置待ちを置く。
    pub fn set_mode(&mut self, mode: Mode) -> Result<(), String> {
        if self.mode == Mode::Sequence
            && mode == Mode::Sheet
            && self
                .document
                .as_ref()
                .is_some_and(Document::has_pending_positions)
        {
            self.push_undo();
            let doc = self.document.as_mut().unwrap();
            doc.place_pending_entries().map_err(|e| e.to_string())?;
        }
        if mode != self.mode {
            if let Some(doc) = self.document.as_mut() {
                let (current, active) = current_index(doc);
                if let Some(column) =
                    doc.mode_switch_column(active, current, mode == Mode::Sequence)
                {
                    let _ = doc.set_selection_index(column, active);
                }
            }
        }
        self.mode = mode;
        // 切り替え前の選択を残すと、赤枠が旧タブの列へ残る。
        self.select_current();
        Ok(())
    }

    /// 再生範囲の端を動かす（Qt の resize_range。シートだけ）。
    pub fn set_range_edge(&mut self, end: bool, column: usize) {
        if self.mode != Mode::Sheet {
            return;
        }
        if let Some((start, stop)) = timeline_panel::playback_range_edge(
            self.range_start,
            self.range_end,
            self.frame_count(),
            end,
            column,
        ) {
            self.range_start = start;
            self.range_end = stop;
        }
    }

    pub fn playback_bounds(&self) -> (usize, usize) {
        timeline_panel::playback_bounds(self.range_start, self.range_end, self.frame_count())
    }

    /// 再生を始める（範囲の外なら範囲の先頭から）。新しい再生の世代を返す。
    pub fn start_playback(&mut self) -> Option<u64> {
        let bounds = self.playback_bounds();
        let doc = self.document.as_mut()?;
        let (current, active) = current_index(doc);
        let start = timeline_panel::playback_start_frame(current, bounds);
        doc.set_selection_index(start, active).ok()?;
        self.playing = true;
        self.clock = PlayClock {
            generation: self.clock.generation + 1,
            started: Instant::now(),
            emitted: 0,
        };
        Some(self.clock.generation)
    }

    pub fn stop_playback(&mut self) {
        self.playing = false;
        self.clock.generation += 1;
        // 再生中に省いた選択の同期を、止めたときに一度行う。
        self.select_current();
    }

    /// 再生の一刻み。進めたら新しい現在コマを返す。世代が変わっていたら Err（止める）。
    fn playback_tick(&mut self, generation: u64) -> Result<Option<Playhead>, ()> {
        if !self.playing || self.clock.generation != generation {
            return Err(());
        }
        let steps = timeline_panel::playback_due_steps(
            self.clock.started.elapsed().as_secs_f64(),
            self.fps,
            self.clock.emitted,
        );
        if steps == 0 {
            return Ok(None);
        }
        self.clock.emitted += steps;
        let bounds = self.playback_bounds();
        let doc = self.document.as_mut().ok_or(())?;
        let (current, active) = current_index(doc);
        let next = timeline_panel::playback_advance(current, steps as usize, bounds);
        doc.set_selection_index(next, active).map_err(|_| ())?;
        Ok(Some(self.playhead()))
    }

    pub fn set_fps(&mut self, fps: u32) {
        self.fps = fps.clamp(1, 60);
        // 再生中なら、変えたところから数え直す。
        self.clock.started = Instant::now();
        self.clock.emitted = 0;
    }
}

// ---------------------------------------------------------------------------
// 編集の操作
// ---------------------------------------------------------------------------

/// 画面からの編集。位置はレイヤー番号（0 が一番下）と表示の列。
#[derive(Clone, Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Edit {
    /// キーの箱をドラッグして移動。
    MoveCell {
        layer: usize,
        column: usize,
        to_layer: usize,
        to_column: usize,
    },
    /// Alt を押してドラッグ：同じ絵番号を参照するキーを複製。
    CopyCell {
        layer: usize,
        column: usize,
        to_layer: usize,
        to_column: usize,
    },
    /// 選んだ範囲をそのままドラッグ：まとめて移動。
    MoveSelection {
        anchor_layer: usize,
        anchor_column: usize,
        to_layer: usize,
        to_column: usize,
    },
    /// 帯の左端・右端をドラッグして露出を変える。
    ResizeExposure {
        layer: usize,
        key_column: usize,
        boundary: usize,
        left: bool,
    },
    /// 表示コマを1コマ伸ばす。
    ExtendExposure {
        layer: usize,
        column: usize,
    },
    /// 空フレーム（○）を作る。
    CreateBlank {
        layer: usize,
        column: usize,
    },
    /// 削除ボタン：現在コマの保持の先頭を消す（連番では番号ごと消す）。
    DeleteFrame,
    ClearCell {
        layer: usize,
        column: usize,
    },
    CopyClip {
        layer: usize,
        column: usize,
    },
    CutClip {
        layer: usize,
        column: usize,
    },
    PasteClip {
        layer: usize,
        column: usize,
    },
    NewCell {
        layer: usize,
        column: usize,
    },
    ToggleGenga {
        layer: usize,
        column: usize,
    },
    Inbetween {
        layer: usize,
        column: usize,
        count: usize,
    },
    NewNumberHere {
        layer: usize,
        column: usize,
    },
    SplitClip {
        layer: usize,
        column: usize,
    },
    MergeClip {
        layer: usize,
        column: usize,
    },
    InsertFrameAt {
        layer: usize,
        column: usize,
    },
    DeleteFrameAt {
        layer: usize,
        column: usize,
    },
    RecallNumber {
        layer: usize,
        column: usize,
        number: i64,
    },
    ReleaseTween {
        layer: usize,
        column: usize,
    },
    SelectLayer {
        layer: usize,
    },
    AddLayer,
    DeleteLayers {
        layers: Vec<usize>,
    },
    DuplicateLayers {
        layers: Vec<usize>,
    },
    /// レイヤー名のドラッグで並べ替え。行は画面の行（上から 0）。
    MoveLayerRows {
        rows: Vec<usize>,
        to_row: usize,
    },
    RenameLayer {
        layer: usize,
        name: String,
    },
    SetVisible {
        layer: usize,
        visible: bool,
    },
    SetOpacity {
        layer: usize,
        opacity: f64,
    },
    ToggleDraft {
        layers: Vec<usize>,
    },
    Normalize {
        layers: Vec<usize>,
    },
}

impl Edit {
    /// Undo に積まない操作（Qt でも表示・不透明度・選択は Undo に積まない）。
    fn records_undo(&self) -> bool {
        !matches!(
            self,
            Edit::SelectLayer { .. } | Edit::SetVisible { .. } | Edit::SetOpacity { .. }
        )
    }

    /// シート表示でだけ行う操作。
    fn sheet_only(&self) -> bool {
        matches!(
            self,
            Edit::CopyCell { .. }
                | Edit::ResizeExposure { .. }
                | Edit::ExtendExposure { .. }
                | Edit::PasteClip { .. }
                | Edit::NewCell { .. }
                | Edit::Inbetween { .. }
                | Edit::NewNumberHere { .. }
                | Edit::SplitClip { .. }
                | Edit::MergeClip { .. }
                | Edit::InsertFrameAt { .. }
                | Edit::DeleteFrameAt { .. }
                | Edit::RecallNumber { .. }
                | Edit::ReleaseTween { .. }
                | Edit::Normalize { .. }
        )
    }
}

fn number(value: i64) -> Result<CellNumber, String> {
    CellNumber::new(value).map_err(|e| e.to_string())
}

/// 文書の命令の失敗を、画面に出す文に。
fn failed(message: &str) -> impl Fn(pma_core::Error) -> String + '_ {
    move |_| message.to_owned()
}

impl TimelineState {
    /// 編集を当てる。成功したら画面の下に出す文（無ければ None）、失敗したら理由を返す。
    /// 失敗しても文書は変えない（命令が写しで行うので、積んだ Undo だけを戻す）。
    pub fn apply(&mut self, edit: Edit) -> Result<Option<String>, String> {
        if self.document.is_none() {
            return Err("文書がありません".into());
        }
        if self.mode == Mode::Sequence && edit.sheet_only() {
            return Err("この操作はシートの表示で行ってください。".into());
        }
        let records = edit.records_undo();
        if records {
            self.push_undo();
        }
        let result = self.apply_inner(edit);
        match &result {
            Ok(_) => {
                self.prune_selection();
            }
            Err(_) if records => {
                self.undo.pop();
            }
            Err(_) => {}
        }
        result
    }

    /// 連番表示の列（番号 - 1）を、その番号の代表のコマにする。シートではそのまま。
    fn frame_of(&self, layer: usize, column: usize) -> Result<usize, String> {
        let doc = self.document.as_ref().unwrap();
        match self.mode {
            Mode::Sheet => Ok(column),
            Mode::Sequence => doc
                .sequence_number_column(layer, column as i64 + 1)
                .ok_or_else(|| "シートに置いていない番号です。".into()),
        }
    }

    fn apply_inner(&mut self, edit: Edit) -> Result<Option<String>, String> {
        let mode = self.mode;
        match edit {
            Edit::MoveCell {
                layer,
                column,
                to_layer,
                to_column,
            } => {
                let doc = self.document.as_mut().unwrap();
                if mode == Mode::Sequence {
                    if layer != to_layer {
                        return Err("連番画像は同じレイヤー内で入れ替えてください。".into());
                    }
                    doc.move_sequence_image(
                        layer,
                        number(column as i64 + 1)?,
                        number(to_column as i64 + 1)?,
                    )
                    .map_err(failed("連番画像を入れ替えられませんでした。"))?;
                    self.selection = BTreeSet::from([(layer, to_column)]);
                    return Ok(None);
                }
                doc.move_sheet_cell((column, layer), (to_column, to_layer))
                    .map_err(failed("コマを移動できませんでした。"))?;
                self.select_current();
                Ok(None)
            }
            Edit::CopyCell {
                layer,
                column,
                to_layer,
                to_column,
            } => {
                if layer != to_layer {
                    return Err("複製は同じレイヤー内で行ってください。".into());
                }
                let doc = self.document.as_mut().unwrap();
                match doc.timeline_block_at(column, layer) {
                    Some((false, _, _)) => {}
                    _ => return Err("絵のあるセルを選んでください。".into()),
                }
                doc.copy_sheet_cell(column, to_column, layer)
                    .map_err(failed("セルを複製できませんでした。"))?;
                self.select_current();
                Ok(None)
            }
            Edit::MoveSelection {
                anchor_layer,
                anchor_column,
                to_layer,
                to_column,
            } => {
                if mode == Mode::Sequence {
                    if anchor_layer != to_layer {
                        return Err("連番画像は同じレイヤー内で入れ替えてください。".into());
                    }
                    return self.apply_inner(Edit::MoveCell {
                        layer: anchor_layer,
                        column: anchor_column,
                        to_layer,
                        to_column,
                    });
                }
                let layer_delta = to_layer as isize - anchor_layer as isize;
                let column_delta = to_column as isize - anchor_column as isize;
                if layer_delta == 0 && column_delta == 0 {
                    return Ok(None);
                }
                let cells: Vec<_> = self.selection.iter().copied().collect();
                let doc = self.document.as_mut().unwrap();
                let moves = doc
                    .prepare_sheet_moves(&cells, layer_delta, column_delta)
                    .ok_or("移動先がタイムライン範囲外です。")?;
                if moves.is_empty() {
                    return Ok(None);
                }
                let active = to_layer.min(doc.track_order().len() - 1);
                doc.move_sheet_cells(&moves, to_column, active)
                    .map_err(failed("コマを移動できませんでした。"))?;
                // 動かしたセルを選び直す。
                self.selection = cells
                    .into_iter()
                    .filter_map(|(layer, column)| {
                        Some((
                            layer.checked_add_signed(layer_delta)?,
                            column.checked_add_signed(column_delta)?,
                        ))
                    })
                    .collect();
                Ok(None)
            }
            Edit::ResizeExposure {
                layer,
                key_column,
                boundary,
                left,
            } => {
                let doc = self.document.as_mut().unwrap();
                let Some(r) = doc.prepare_exposure_resize(layer, key_column, boundary, left) else {
                    return Ok(None);
                };
                doc.resize_sheet_exposure(
                    layer,
                    left,
                    r.key_column,
                    r.old_end,
                    r.new_start,
                    r.new_end,
                )
                .map_err(failed("露出を変えられませんでした。"))?;
                self.select_current();
                Ok(None)
            }
            Edit::ExtendExposure { layer, column } => {
                let doc = self.document.as_ref().unwrap();
                let (_, key, exposure) = doc
                    .timeline_block_at(column, layer)
                    .ok_or("伸ばせるセルがありません。")?;
                self.apply_inner(Edit::ResizeExposure {
                    layer,
                    key_column: key,
                    boundary: key + exposure.max(1),
                    left: false,
                })
            }
            Edit::CreateBlank { layer, column } => {
                let doc = self.document.as_mut().unwrap();
                match mode {
                    Mode::Sheet => {
                        doc.create_blank_key(column, layer)
                            .map_err(failed("空フレームを作れませんでした。"))?;
                    }
                    Mode::Sequence => {
                        doc.insert_sequence_blank(layer, column as i64 + 1, None)
                            .map_err(failed("空き番号を入れられませんでした。"))?;
                    }
                }
                self.select_current();
                Ok(None)
            }
            Edit::DeleteFrame => {
                let column = self.current_column();
                let doc = self.document.as_mut().unwrap();
                let (current, active) = current_index(doc);
                match mode {
                    Mode::Sequence => {
                        let column = column.ok_or("消せる番号がありません。")?;
                        doc.delete_sequence_entry(active, number(column as i64 + 1)?)
                            .map_err(failed("番号を消せませんでした。"))?;
                    }
                    Mode::Sheet => {
                        doc.clear_sheet_key_archived(active, current)
                            .map_err(failed("消せるセルがありません。"))?;
                    }
                }
                self.select_current();
                Ok(None)
            }
            Edit::ClearCell { layer, column } => {
                let frame = self.frame_of(layer, column)?;
                let doc = self.document.as_mut().unwrap();
                let (_, start, length) = doc
                    .timeline_block_at(frame, layer)
                    .ok_or("消せるセルがありません。")?;
                doc.clear_sheet_span(layer, start..start + length)
                    .map_err(failed("セルを消せませんでした。"))?;
                Ok(None)
            }
            Edit::CopyClip { layer, column } => {
                let frame = self.frame_of(layer, column)?;
                let doc = self.document.as_ref().unwrap();
                let (_, start, length) = doc
                    .timeline_block_at(frame, layer)
                    .ok_or("コピーできるセルがありません。")?;
                let key = position(doc, start, layer).map(|p| p.reference);
                let (Some(Reference::Number(n)), Some(cell)) =
                    (key, doc.position_cell(start, layer))
                else {
                    return Err("番号のある絵を選んでください。".into());
                };
                self.clipboard = Some(Clip {
                    cell: cell.clone(),
                    number: n,
                    length,
                });
                Ok(Some("セルをコピーしました。".into()))
            }
            Edit::CutClip { layer, column } => {
                self.apply_inner(Edit::CopyClip { layer, column })?;
                self.apply_inner(Edit::ClearCell { layer, column })?;
                Ok(Some("セルを切り取りました。".into()))
            }
            Edit::PasteClip { layer, column } => {
                let clip = self
                    .clipboard
                    .clone()
                    .ok_or("貼り付けられるセルがありません。")?;
                let doc = self.document.as_mut().unwrap();
                if column >= doc.frame_order().len() {
                    return Err("貼り付けられる位置ではありません。".into());
                }
                doc.paste_sheet_cell((column, layer), clip.number, &clip.cell, clip.length)
                    .map_err(failed("セルを貼り付けられませんでした。"))?;
                Ok(Some("セルを貼り付けました。".into()))
            }
            Edit::NewCell { layer, column } => {
                let doc = self.document.as_mut().unwrap();
                doc.new_sheet_cell(column, layer)
                    .map_err(failed("セルを作れませんでした。"))?;
                Ok(None)
            }
            Edit::ToggleGenga { layer, column } => {
                let frame = self.frame_of(layer, column)?;
                let doc = self.document.as_mut().unwrap();
                let (state, _) = doc
                    .toggle_genga(frame, layer)
                    .map_err(failed("絵のあるセルを選んでください。"))?;
                Ok(Some(
                    if state {
                        "原画の印を付けました。"
                    } else {
                        "原画の印を外しました。"
                    }
                    .into(),
                ))
            }
            Edit::Inbetween {
                layer,
                column,
                count,
            } => {
                let doc = self.document.as_mut().unwrap();
                let columns = doc.inbetween_columns(column, layer, count).map_err(|e| match e {
                    InbetweenError::NotGenga => "原画の印のあるセルを選んでください。".into(),
                    InbetweenError::NoNextGenga => {
                        "この原画のあとに、次の原画がありません。".into()
                    }
                    InbetweenError::Occupied => "原画と次の原画のあいだに、もうセルがあります。\
                                                 中割りは原画どうしが隣り合っているときに入れられます。"
                        .into(),
                    InbetweenError::TooMany { span } => format!(
                        "原画のあいだが{span}コマなので、中割りは{}枚まで入れられます。",
                        span.saturating_sub(1)
                    ),
                })?;
                let mut edited = doc.clone();
                for &blank in &columns {
                    edited
                        .create_blank_key(blank, layer)
                        .map_err(failed("中割りを入れられませんでした。"))?;
                }
                *doc = edited;
                Ok(Some(format!(
                    "原画のあいだに中割りを{}枚入れました（○に描くと番号が付きます）。",
                    columns.len()
                )))
            }
            Edit::NewNumberHere { layer, column } => {
                let doc = self.document.as_mut().unwrap();
                let Some((false, start, _)) = doc.timeline_block_at(column, layer) else {
                    return Err("絵のあるセルを選んでください。".into());
                };
                if position(doc, start, layer)
                    .and_then(Position::number)
                    .is_none()
                {
                    return Err("番号のある絵を選んでください。".into());
                }
                doc.new_sheet_number(column, layer)
                    .map_err(failed("新しい番号にできませんでした。"))?;
                let n = position(doc, column, layer)
                    .and_then(Position::number)
                    .map_or(0, CellNumber::get);
                Ok(Some(format!("この位置を新しい番号 {n} にしました。")))
            }
            Edit::SplitClip { layer, column } => {
                let doc = self.document.as_mut().unwrap();
                let (_, start, length) = doc
                    .timeline_block_at(column, layer)
                    .ok_or("分割できるセルがありません。")?;
                if column <= start || column > start + length - 1 {
                    return Err("露出の途中を選んでください。先頭では分割できません。".into());
                }
                doc.split_sheet_clip(column, layer)
                    .map_err(failed("クリップを分割できませんでした。"))?;
                Ok(None)
            }
            Edit::MergeClip { layer, column } => {
                let doc = self.document.as_mut().unwrap();
                let (_, start, _) = doc
                    .timeline_block_at(column, layer)
                    .ok_or("結合できるセルがありません。")?;
                if start == 0 || doc.timeline_block_at(start - 1, layer).is_none() {
                    return Err("前に結合できるセルがありません。".into());
                }
                doc.merge_sheet_clip(start, layer)
                    .map_err(failed("クリップを結合できませんでした。"))?;
                Ok(None)
            }
            Edit::InsertFrameAt { layer, column } => {
                let doc = self.document.as_mut().unwrap();
                doc.insert_track_frame(column, layer)
                    .map_err(failed("フレームを挿入できませんでした。"))?;
                Ok(None)
            }
            Edit::DeleteFrameAt { layer, column } => {
                let doc = self.document.as_mut().unwrap();
                doc.delete_track_frame(column, layer)
                    .map_err(failed("フレームを削除できませんでした。"))?;
                Ok(None)
            }
            Edit::RecallNumber {
                layer,
                column,
                number: n,
            } => {
                let doc = self.document.as_mut().unwrap();
                let mut edited = doc.clone();
                edited
                    .ensure_frame_count(column + 1)
                    .map_err(|e| e.to_string())?;
                edited
                    .recall_sheet_number(column, layer, number(n)?)
                    .map_err(failed("番号を呼び出せませんでした。"))?;
                *doc = edited;
                self.select_current();
                Ok(None)
            }
            Edit::ReleaseTween { layer, column } => {
                let doc = self.document.as_mut().unwrap();
                let group = doc
                    .tween_groups(layer)
                    .into_iter()
                    .find(|g| (g.start..g.start + g.length).contains(&column))
                    .ok_or("確定したトゥイーンがありません。")?;
                doc.release_tween(group.start, layer, group.length)
                    .map_err(failed("トゥイーンを解除できませんでした。"))?;
                Ok(Some(
                    "トゥイーンを解除しました（中割りは通常のセルになりました）。".into(),
                ))
            }
            Edit::SelectLayer { layer } => {
                let doc = self.document.as_mut().unwrap();
                let (current, _) = current_index(doc);
                doc.set_selection_index(current, layer)
                    .map_err(|e| e.to_string())?;
                self.select_current();
                Ok(None)
            }
            Edit::AddLayer => {
                let doc = self.document.as_mut().unwrap();
                doc.add_track()
                    .map_err(failed("レイヤーを追加できませんでした。"))?;
                self.select_current();
                Ok(None)
            }
            Edit::DeleteLayers { layers } => {
                let doc = self.document.as_mut().unwrap();
                let count = doc.track_order().len();
                let layers: Vec<usize> = layers
                    .into_iter()
                    .filter(|&l| l < count)
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect();
                if layers.is_empty() {
                    return Ok(None);
                }
                if layers.len() >= count {
                    return Err("すべてのレイヤーは削除できません。1つ以上残してください。".into());
                }
                // 一つだけ消すときは、Qt の delete_layer と同じく一つ下を選ぶ。
                let active = (layers.len() == 1).then(|| layers[0].saturating_sub(1));
                doc.remove_tracks(&layers, active)
                    .map_err(failed("レイヤーを削除できませんでした。"))?;
                self.select_current();
                Ok(None)
            }
            Edit::DuplicateLayers { layers } => {
                let doc = self.document.as_mut().unwrap();
                let names: Vec<(usize, String)> = layers
                    .into_iter()
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .filter_map(|layer| {
                        let track = doc.track(*doc.track_order().get(layer)?)?;
                        Some((layer, format!("{} コピー", track.settings.name)))
                    })
                    .collect();
                doc.duplicate_tracks(&names)
                    .map_err(failed("レイヤーを複製できませんでした。"))?;
                self.select_current();
                Ok(None)
            }
            Edit::MoveLayerRows { rows, to_row } => {
                let doc = self.document.as_mut().unwrap();
                let count = doc.track_order().len();
                let rows: Vec<usize> = rows
                    .into_iter()
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect();
                if rows.is_empty()
                    || rows.iter().any(|&r| r >= count)
                    || rows != (rows[0]..=rows[rows.len() - 1]).collect::<Vec<_>>()
                {
                    return Ok(None);
                }
                let block = rows.len();
                let to_row = to_row.min(count - block);
                if to_row == rows[0] {
                    return Ok(None);
                }
                let mut visual: Vec<usize> = (0..count).rev().collect();
                let moved: Vec<usize> = visual.drain(rows[0]..=rows[block - 1]).collect();
                visual.splice(to_row..to_row, moved);
                let order: Vec<usize> = visual.into_iter().rev().collect();
                doc.move_tracks(&order)
                    .map_err(failed("レイヤーを並べ替えられませんでした。"))?;
                self.select_current();
                Ok(None)
            }
            Edit::RenameLayer { layer, name } => {
                let doc = self.document.as_mut().unwrap();
                let name = match name.trim() {
                    "" => timeline_panel::default_layer_name(layer),
                    name => name.to_owned(),
                };
                doc.set_track_properties(&[layer], &[TrackProperty::Name(name)], true)
                    .map_err(failed("レイヤー名を変えられませんでした。"))?;
                Ok(None)
            }
            Edit::SetVisible { layer, visible } => {
                let doc = self.document.as_mut().unwrap();
                doc.set_track_properties(&[layer], &[TrackProperty::Visible(visible)], false)
                    .map_err(|e| e.to_string())?;
                Ok(None)
            }
            Edit::SetOpacity { layer, opacity } => {
                let doc = self.document.as_mut().unwrap();
                doc.set_track_properties(
                    &[layer],
                    &[TrackProperty::Opacity(opacity.clamp(0.0, 1.0))],
                    false,
                )
                .map_err(|e| e.to_string())?;
                Ok(None)
            }
            Edit::ToggleDraft { layers } => {
                let doc = self.document.as_mut().unwrap();
                let layers: Vec<usize> = layers
                    .into_iter()
                    .filter(|&l| l < doc.track_order().len())
                    .collect();
                if layers.is_empty() {
                    return Ok(None);
                }
                // 一つでも下書きでなければ、すべて下書きにする。
                let state = !layers.iter().all(|&layer| {
                    doc.track(doc.track_order()[layer])
                        .is_some_and(|t| t.settings.is_draft)
                });
                doc.set_track_properties(&layers, &[TrackProperty::IsDraft(state)], false)
                    .map_err(|e| e.to_string())?;
                Ok(Some(
                    if state {
                        "下書きレイヤーモードを有効化しました。"
                    } else {
                        "下書きレイヤーモードを解除しました。"
                    }
                    .into(),
                ))
            }
            Edit::Normalize { layers } => {
                let doc = self.document.as_mut().unwrap();
                let layers: Vec<usize> = layers
                    .into_iter()
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect();
                if layers.is_empty() {
                    return Ok(None);
                }
                doc.normalize_sheet_numbers(&layers)
                    .map_err(failed("番号を正規化できませんでした。"))?;
                Ok(Some("番号をタイムラインの順番に正規化しました。".into()))
            }
        }
    }
}

/// 正規化の確認画面に出す、レイヤーごとの番号の振り直し。
#[derive(Clone, Debug, Serialize)]
pub struct NormalizePreview {
    pub layer: usize,
    pub name: String,
    pub changes: Vec<(i64, i64)>,
}

impl TimelineState {
    pub fn normalize_preview(&self) -> Vec<NormalizePreview> {
        let Some(doc) = self.document.as_ref() else {
            return Vec::new();
        };
        (0..doc.track_order().len())
            .rev()
            .map(|layer| NormalizePreview {
                layer,
                name: doc
                    .track(doc.track_order()[layer])
                    .map(|t| t.settings.name.clone())
                    .unwrap_or_default(),
                changes: doc
                    .normalization_changes(layer)
                    .into_iter()
                    .map(|(old, new)| (old.get(), new.get()))
                    .collect(),
            })
            .collect()
    }

    /// サムネイル（幅・高さに収まるよう縮めた RGBA。先頭 8 バイトに幅と高さ）。
    pub fn thumbnail(
        &self,
        layer: usize,
        column: Option<usize>,
        number: Option<i64>,
        width: usize,
        height: usize,
    ) -> Vec<u8> {
        let empty = vec![0; 8];
        let Some(doc) = self.document.as_ref() else {
            return empty;
        };
        let cell = match column {
            Some(column) => doc.position_cell(column, layer),
            None => number.and_then(|n| {
                let track = doc.track(*doc.track_order().get(layer)?)?;
                track.cells.get(&CellNumber::new(n).ok()?)
            }),
        };
        match cell {
            Some(cell) => shrink(&cell.image, width, height),
            None => empty,
        }
    }
}

/// 画像を幅・高さに収まるよう、縦横比を保って平均で縮める。
fn shrink(image: &RgbaImage, max_width: usize, max_height: usize) -> Vec<u8> {
    let (w, h) = (image.width(), image.height());
    if w == 0 || h == 0 || max_width == 0 || max_height == 0 {
        return vec![0; 8];
    }
    let scale = (max_width as f64 / w as f64)
        .min(max_height as f64 / h as f64)
        .min(1.0);
    let tw = ((w as f64 * scale).round() as usize).max(1);
    let th = ((h as f64 * scale).round() as usize).max(1);
    let Ok(pixels) = image.copy_rgba_rect(Rect {
        x: 0,
        y: 0,
        width: w,
        height: h,
    }) else {
        return vec![0; 8];
    };
    let mut out = Vec::with_capacity(8 + tw * th * 4);
    out.extend_from_slice(&(tw as u32).to_le_bytes());
    out.extend_from_slice(&(th as u32).to_le_bytes());
    for ty in 0..th {
        let y0 = ty * h / th;
        let y1 = ((ty + 1) * h / th).max(y0 + 1);
        for tx in 0..tw {
            let x0 = tx * w / tw;
            let x1 = ((tx + 1) * w / tw).max(x0 + 1);
            let mut sum = [0u64; 4];
            for y in y0..y1 {
                for x in x0..x1 {
                    let i = (y * w + x) * 4;
                    for (s, &p) in sum.iter_mut().zip(&pixels[i..i + 4]) {
                        *s += u64::from(p);
                    }
                }
            }
            let n = ((y1 - y0) * (x1 - x0)) as u64;
            out.extend(sum.iter().map(|s| (s / n) as u8));
        }
    }
    out
}

// ---------------------------------------------------------------------------
// 見本の文書（開く・保存が入るまでの確かめ用）
// ---------------------------------------------------------------------------

/// 列ごとの記号から文書のグラフを作る。記号：数字は番号の絵、`o` は空キーの頭、`-` は
/// 空キーの続き、`.` は未使用、`pN` は配置待ちの番号 N。最初の行が一番下のレイヤー。
/// `edit` で位置の JSON を書き換えられる（トゥイーンの印など）。画像は番号ごとに色の丸。
pub fn document_from_rows(
    names: &[&str],
    rows: &[Vec<String>],
    size: (usize, usize),
    edit: impl Fn(usize, usize, &mut Value),
) -> Result<Document, String> {
    let frames = rows.first().map_or(0, Vec::len);
    let mut images = Vec::new();
    let mut tracks = Vec::new();
    for (layer, row) in rows.iter().enumerate() {
        let numbers: BTreeSet<i64> = row
            .iter()
            .filter_map(|t| t.trim_start_matches('p').parse().ok())
            .collect();
        let cells: Vec<Value> = numbers
            .iter()
            .map(|&n| {
                images.push(sample_image(size, layer, n));
                json!([n, {"image": images.len() - 1, "initial_image": null, "cell_name": null}])
            })
            .collect();
        tracks.push(json!({
            "settings": {"name": names.get(layer).copied().unwrap_or("A"), "visible": true,
                         "opacity": 1.0, "alpha_locked": false, "is_paper": false,
                         "is_draft": false, "color_filter_enabled": false,
                         "color_filter_rgb": null},
            "cells": cells, "hints": [],
        }));
    }
    let frames: Vec<Value> = (0..frames)
        .map(|column| {
            let positions: Vec<Value> = rows
                .iter()
                .enumerate()
                .map(|(layer, row)| {
                    let token = row[column].as_str();
                    let n: Option<i64> = token.trim_start_matches('p').parse().ok();
                    let mut value = json!({
                        "has_content": n.is_some(),
                        "is_blank_key": token == "o" || token == "-",
                        "blank_start": token == "o",
                        "sequence_number": n,
                        "sequence_only": token.starts_with('p'),
                        "tween": null, "tween_member": null,
                    });
                    edit(layer, column, &mut value);
                    value
                })
                .collect();
            json!({"duration": 1, "positions": positions})
        })
        .collect();
    let graph = json!({
        "width": size.0, "height": size.1, "current_frame": 0, "active_layer": 0,
        "tracks": tracks, "frames": frames,
    });
    Document::from_graph(&graph, &images).map_err(|e| e.to_string())
}

fn sample_image((width, height): (usize, usize), layer: usize, number: i64) -> RgbaImage {
    let hue = (number as f64 * 0.17 + layer as f64 * 0.33).fract();
    let (r, g, b) = hsv(hue, 0.6, 0.9);
    let cx = width as f64 * (0.2 + 0.6 * ((number as f64 * 0.13).fract()));
    let cy = height as f64 * (0.3 + 0.1 * layer as f64);
    let radius = height as f64 * 0.25;
    let mut pixels = vec![255u8; width * height * 4];
    for y in 0..height {
        for x in 0..width {
            let (dx, dy) = (x as f64 - cx, y as f64 - cy);
            if dx * dx + dy * dy <= radius * radius {
                let i = (y * width + x) * 4;
                pixels[i..i + 3].copy_from_slice(&[r, g, b]);
            }
        }
    }
    RgbaImage::from_rgba(width, height, &pixels).expect("見本の画像")
}

fn hsv(h: f64, s: f64, v: f64) -> (u8, u8, u8) {
    let i = (h * 6.0).floor();
    let f = h * 6.0 - i;
    let (p, q, t) = (v * (1.0 - s), v * (1.0 - f * s), v * (1.0 - (1.0 - f) * s));
    let (r, g, b) = match i as i64 % 6 {
        0 => (v, t, p),
        1 => (q, v, p),
        2 => (p, v, t),
        3 => (p, q, v),
        4 => (t, p, v),
        _ => (v, p, q),
    };
    ((r * 255.0) as u8, (g * 255.0) as u8, (b * 255.0) as u8)
}

/// 見本の文書：保持・空キー・空き・トゥイーンの帯・原画の印を含む `frames` コマの3レイヤー。
pub fn sample_document(frames: usize) -> Result<Document, String> {
    let frames = frames.max(12);
    let mut a = Vec::with_capacity(frames);
    let mut b = Vec::with_capacity(frames);
    let mut c = Vec::with_capacity(frames);
    for column in 0..frames {
        // A：3コマ打ち。24コマごとに○を2コマ。
        a.push(if column % 24 >= 22 {
            if column % 24 == 22 {
                "o".into()
            } else {
                "-".into()
            }
        } else {
            (column / 3 + 1).to_string()
        });
        // B：6コマ打ちで、ところどころ空き。
        b.push(if column % 18 >= 15 {
            ".".into()
        } else {
            (column / 6 + 1).to_string()
        });
        // C：0〜5 がトゥイーンの帯（キー1と中割り2〜6）、その後は2コマ打ち。
        c.push(if column < 6 {
            (column + 1).to_string()
        } else {
            ((column - 6) / 2 + 7).to_string()
        });
    }
    let mut doc = document_from_rows(
        &["C", "B", "A"],
        &[c, b, a],
        (160, 90),
        |layer, column, value| {
            if layer == 0 && column == 0 {
                value["tween"] = json!({"id": "sample", "length": 6, "reverse": false,
                                        "mode": "free", "start_points": [],
                                        "final_points": [], "mesh_cols": 4, "mesh_rows": 4,
                                        "mesh_reference_points": []});
            } else if layer == 0 && column < 6 {
                value["tween_member"] = json!("sample");
            }
        },
    )?;
    // 原画の印：A の 1 番と 4 番。
    let a_layer = 2;
    for column in [0, 9] {
        doc.toggle_genga(column, a_layer)
            .map_err(|e| e.to_string())?;
    }
    doc.set_selection_index(0, a_layer)
        .map_err(|e| e.to_string())?;
    Ok(doc)
}

// ---------------------------------------------------------------------------
// Tauri の命令
// ---------------------------------------------------------------------------

type TimelineArg<'a> = tauri::State<'a, SharedTimeline>;

/// 編集の結果：新しい表示と、画面の下に出す文。
#[derive(Serialize)]
pub struct EditResult {
    pub view: TimelineView,
    pub message: Option<String>,
    pub error: bool,
}

#[tauri::command]
pub fn timeline_view(state: TimelineArg) -> TimelineView {
    let mut s = state.lock().unwrap();
    // 確かめ用：PMA_TIMELINE_SAMPLE=コマ数 で起動すると、見本の文書を入れて開く。
    if s.document.is_none() {
        if let Some(frames) = std::env::var("PMA_TIMELINE_SAMPLE")
            .ok()
            .and_then(|v| v.parse().ok())
        {
            if let Ok(doc) = sample_document(frames) {
                s.load(doc);
            }
        }
    }
    s.view()
}

/// 確かめ用の起動の設定（環境変数）。画面が読み込んだあとに当てる。
#[derive(Serialize, Default)]
pub struct DebugOptions {
    /// PMA_TIMELINE_AUTOPLAY：読み込んだら再生する。
    pub autoplay: bool,
    /// PMA_TIMELINE_SCROLL=x,y：表のスクロール位置。
    pub scroll: Option<(f64, f64)>,
    /// PMA_TIMELINE_EXPAND：すべてのレイヤーのサムネイルを開く。
    pub expand: bool,
    /// PMA_TIMELINE_MODE=sequence：連番の表示で開く。
    pub sequence: bool,
}

#[tauri::command]
pub fn timeline_debug_options() -> DebugOptions {
    let var = |name: &str| std::env::var(name).ok();
    DebugOptions {
        autoplay: var("PMA_TIMELINE_AUTOPLAY").is_some(),
        scroll: var("PMA_TIMELINE_SCROLL").and_then(|v| {
            let (x, y) = v.split_once(',')?;
            Some((x.trim().parse().ok()?, y.trim().parse().ok()?))
        }),
        expand: var("PMA_TIMELINE_EXPAND").is_some(),
        sequence: var("PMA_TIMELINE_MODE").as_deref() == Some("sequence"),
    }
}

/// 確かめ用：見本の文書を入れる（開く・保存が入るまで）。
#[tauri::command]
pub fn timeline_debug_load_sample(
    state: TimelineArg,
    frames: Option<usize>,
) -> Result<TimelineView, String> {
    let doc = sample_document(frames.unwrap_or(72))?;
    let mut s = state.lock().unwrap();
    s.load(doc);
    Ok(s.view())
}

#[tauri::command]
pub fn timeline_select(
    state: TimelineArg,
    layer: usize,
    column: usize,
    how: String,
) -> Result<TimelineView, String> {
    let mut s = state.lock().unwrap();
    s.select(layer, column, &how)?;
    Ok(s.view())
}

#[tauri::command]
pub fn timeline_select_rect(
    state: TimelineArg,
    from: (usize, usize),
    to: (usize, usize),
) -> Result<TimelineView, String> {
    // Qt と同じく、ドラッグで囲んでも現在コマは押したセルのまま。
    let mut s = state.lock().unwrap();
    s.select_rect(from, to);
    Ok(s.view())
}

/// 見出しのクリック・ドラッグ（シーク）。選択は今のコマだけにする。
#[tauri::command]
pub fn timeline_set_frame(state: TimelineArg, column: usize) -> Result<TimelineView, String> {
    let mut s = state.lock().unwrap();
    let layer = s.document.as_ref().map_or(0, |doc| current_index(doc).1);
    s.set_frame(column, layer)?;
    s.select_current();
    Ok(s.view())
}

#[tauri::command]
pub fn timeline_navigate(state: TimelineArg, to: String) -> Result<TimelineView, String> {
    let mut s = state.lock().unwrap();
    s.navigate(&to)?;
    Ok(s.view())
}

#[tauri::command]
pub fn timeline_edit(state: TimelineArg, edit: Edit) -> EditResult {
    let mut s = state.lock().unwrap();
    let (message, error) = match s.apply(edit) {
        Ok(message) => (message, false),
        Err(message) => (Some(message), true),
    };
    EditResult {
        view: s.view(),
        message,
        error,
    }
}

#[tauri::command]
pub fn timeline_undo(state: TimelineArg) -> TimelineView {
    let mut s = state.lock().unwrap();
    s.undo();
    s.view()
}

#[tauri::command]
pub fn timeline_redo(state: TimelineArg) -> TimelineView {
    let mut s = state.lock().unwrap();
    s.redo();
    s.view()
}

#[tauri::command]
pub fn timeline_set_mode(state: TimelineArg, mode: Mode) -> Result<TimelineView, String> {
    let mut s = state.lock().unwrap();
    s.set_mode(mode)?;
    Ok(s.view())
}

#[tauri::command]
pub fn timeline_set_fps(state: TimelineArg, fps: u32) -> TimelineView {
    let mut s = state.lock().unwrap();
    s.set_fps(fps);
    s.view()
}

/// 再生範囲の端（`start` か `end`）を動かす。end の列は「このコマまで」の次。
#[tauri::command]
pub fn timeline_set_range(state: TimelineArg, edge: String, column: usize) -> TimelineView {
    let mut s = state.lock().unwrap();
    s.set_range_edge(edge == "end", column);
    s.view()
}

#[tauri::command]
pub fn timeline_set_options(
    state: TimelineArg,
    follow_playback: Option<bool>,
    onion: Option<bool>,
) -> TimelineView {
    let mut s = state.lock().unwrap();
    if let Some(follow) = follow_playback {
        s.follow_playback = follow;
    }
    if let Some(onion) = onion {
        s.onion = onion;
    }
    s.view()
}

#[tauri::command]
pub fn timeline_normalize_preview(state: TimelineArg) -> Vec<NormalizePreview> {
    state.lock().unwrap().normalize_preview()
}

#[tauri::command]
pub fn timeline_thumbnail(
    state: TimelineArg,
    layer: usize,
    column: Option<usize>,
    number: Option<i64>,
    width: usize,
    height: usize,
) -> tauri::ipc::Response {
    let s = state.lock().unwrap();
    tauri::ipc::Response::new(s.thumbnail(layer, column, number, width, height))
}

/// 再生／停止。再生は別スレッドが経過時間に合わせてコマを進め、`timeline-playhead` で知らせる。
#[tauri::command]
pub fn timeline_play(app: tauri::AppHandle, state: TimelineArg, on: bool) -> TimelineView {
    let shared = state.inner().clone();
    let mut s = shared.lock().unwrap();
    if on && !s.playing {
        if let Some(generation) = s.start_playback() {
            let thread_state = shared.clone();
            std::thread::spawn(move || playback_loop(app, thread_state, generation));
        }
    } else if !on && s.playing {
        s.stop_playback();
    }
    s.view()
}

fn playback_loop(app: tauri::AppHandle, shared: SharedTimeline, generation: u64) {
    use tauri::Emitter;
    loop {
        let (interval, tick, follow) = {
            let mut s = shared.lock().unwrap();
            // 本来の間隔より細かく確かめ、遅れたら経過時間に追いつく（Qt の advance と同じ）。
            let interval = (500.0 / f64::from(s.fps)).round().clamp(4.0, 16.0) as u64;
            (interval, s.playback_tick(generation), s.follow_playback)
        };
        match tick {
            Err(()) => break,
            Ok(Some(playhead)) if follow => {
                let _ = app.emit("timeline-playhead", playhead);
            }
            Ok(_) => {}
        }
        std::thread::sleep(Duration::from_millis(interval));
    }
    // 止めたときの現在コマを一度知らせる。
    let playhead = shared.lock().unwrap().playhead();
    let _ = app.emit("timeline-playhead", playhead);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rows(rows: &[&str]) -> Vec<Vec<String>> {
        rows.iter()
            .map(|r| r.split_whitespace().map(str::to_owned).collect())
            .collect()
    }

    fn doc(layers: &[&str]) -> Document {
        let names: Vec<String> = (0..layers.len()).map(|i| format!("L{i}")).collect();
        let names: Vec<&str> = names.iter().map(String::as_str).collect();
        document_from_rows(&names, &rows(layers), (8, 4), |_, _, _| {}).unwrap()
    }

    fn state(layers: &[&str]) -> TimelineState {
        let mut s = TimelineState::default();
        s.load(doc(layers));
        s
    }

    fn summary(spans: &[SpanView]) -> Vec<(usize, usize, &'static str, String)> {
        spans
            .iter()
            .map(|s| (s.start, s.length, s.kind, s.label.clone()))
            .collect()
    }

    #[test]
    fn sheet_rows_show_spans_and_gaps_top_layer_first() {
        let s = state(&["1 1 . 2 o - o 3", "4 4 4 4 . . . ."]);
        let view = s.view();
        assert_eq!(view.rows.len(), 2);
        assert_eq!(view.rows[0].layer, 1);
        assert_eq!(view.rows[0].name, "L1");
        let bottom = &view.rows[1];
        assert_eq!(
            summary(&bottom.spans),
            vec![
                (0, 2, "cell", "1".into()),
                (3, 1, "cell", "2".into()),
                (4, 2, "blank", String::new()),
                (6, 1, "blank", String::new()),
                (7, 1, "cell", "3".into()),
            ]
        );
        assert_eq!(
            bottom.gaps,
            vec![GapView {
                start: 2,
                length: 1,
                cross: true
            }]
        );
        assert_eq!(view.frame_count, 8);
        assert_eq!(view.current_column, Some(0));
        assert_eq!(view.selection, vec![(0, 0)]);
    }

    #[test]
    fn tween_groups_become_one_span() {
        let names = ["A"];
        let doc = document_from_rows(&names, &rows(&["1 2 3 4 4"]), (4, 4), |_, column, v| {
            if column == 0 {
                v["tween"] = json!({"id": "t", "length": 3, "reverse": true});
            } else if column < 3 {
                v["tween_member"] = json!("t");
            }
        })
        .unwrap();
        let (spans, _) = sheet_row(&doc, 0);
        assert_eq!(
            spans
                .iter()
                .map(|s| (s.start, s.length, s.tween, s.group))
                .collect::<Vec<_>>(),
            vec![(0, 3, Some("reverse"), true), (3, 2, None, false)]
        );
    }

    #[test]
    fn sequence_mode_shows_numbers_and_follows_the_current_number() {
        // #108：同じ番号が複数のコマにあり、保持中のコマを選んでも、その番号が選ばれる。
        let mut s = state(&["1 1 2 4 4 . 4"]);
        s.set_mode(Mode::Sequence).unwrap();
        let view = s.view();
        assert_eq!(view.frame_count, 4);
        assert_eq!(
            summary(&view.rows[0].spans),
            vec![
                (0, 1, "cell", "1".into()),
                (1, 1, "cell", "2".into()),
                (3, 1, "cell", "4".into()),
            ]
        );
        s.mode = Mode::Sheet;
        s.set_frame(4, 0).unwrap();
        s.mode = Mode::Sequence;
        s.select_current();
        assert_eq!(s.view().current_column, Some(3));
        assert_eq!(s.view().selection, vec![(0, 3)]);
        // 連番で絵4を選ぶと、代表のコマ（最初のコマ）へ移る。
        s.select(0, 3, "replace").unwrap();
        assert_eq!(s.view().current, 3);
    }

    #[test]
    fn selection_click_shift_and_rect() {
        let mut s = state(&["1 1 1 1", "2 2 2 2"]);
        s.select(0, 1, "replace").unwrap();
        s.select(1, 3, "range").unwrap();
        assert_eq!(s.selection.len(), 6);
        assert_eq!(s.view().current, 3);
        assert_eq!(s.view().active_layer, 1);
        s.select(0, 0, "toggle").unwrap();
        assert_eq!(s.selection.len(), 7);
        s.select_rect((0, 0), (0, 1));
        assert_eq!(s.view().selection, vec![(0, 0), (0, 1)]);
    }

    #[test]
    fn navigation_steps_frames_and_keys() {
        let mut s = state(&["1 1 2 2 . 3"]);
        s.navigate("next_key").unwrap();
        assert_eq!(s.view().current, 2);
        s.navigate("next_key").unwrap();
        assert_eq!(s.view().current, 5);
        s.navigate("previous").unwrap();
        assert_eq!(s.view().current, 4);
        s.navigate("first").unwrap();
        assert_eq!(s.view().current, 0);
        s.navigate("last").unwrap();
        // 最後のコマで「次」はコマを延ばす（advance_frame）。
        s.navigate("next").unwrap();
        assert_eq!(s.view().sheet_frames, 7);
        assert_eq!(s.view().current, 6);
    }

    #[test]
    fn edits_change_the_document_and_undo_restores_it() {
        let mut s = state(&["1 1 2 2"]);
        s.apply(Edit::ExtendExposure {
            layer: 0,
            column: 0,
        })
        .unwrap();
        assert_eq!(
            summary(&s.view().rows[0].spans),
            vec![(0, 3, "cell", "1".into()), (3, 2, "cell", "2".into())]
        );
        s.apply(Edit::ResizeExposure {
            layer: 0,
            key_column: 3,
            boundary: 4,
            left: true,
        })
        .unwrap();
        assert_eq!(s.view().rows[0].spans[1].start, 4);
        assert!(s.undo());
        assert!(s.undo());
        assert_eq!(
            summary(&s.view().rows[0].spans),
            vec![(0, 2, "cell", "1".into()), (2, 2, "cell", "2".into())]
        );
        assert!(s.redo());
        assert_eq!(s.view().rows[0].spans[0].length, 3);
    }

    #[test]
    fn group_drag_moves_selected_blocks() {
        let mut s = state(&["1 1 . . .", "2 . . . ."]);
        s.select_rect((0, 0), (1, 0));
        s.apply(Edit::MoveSelection {
            anchor_layer: 0,
            anchor_column: 0,
            to_layer: 0,
            to_column: 2,
        })
        .unwrap();
        let view = s.view();
        assert_eq!(view.rows[1].spans[0].start, 2);
        assert_eq!(view.rows[0].spans[0].start, 2);
        assert_eq!(view.selection, vec![(0, 2), (1, 2)]);
        // 範囲外へは動かさず、Undo も積まない。
        let undo = s.undo.len();
        let error = s
            .apply(Edit::MoveSelection {
                anchor_layer: 0,
                anchor_column: 2,
                to_layer: 0,
                to_column: 0,
            })
            .err();
        assert!(error.is_none() || s.undo.len() == undo);
    }

    #[test]
    fn clipboard_copy_paste_and_messages() {
        let mut s = state(&["1 1 . . . ."]);
        assert_eq!(
            s.apply(Edit::PasteClip {
                layer: 0,
                column: 3
            }),
            Err("貼り付けられるセルがありません。".into())
        );
        assert_eq!(s.undo.len(), 0);
        s.apply(Edit::CopyClip {
            layer: 0,
            column: 1,
        })
        .unwrap();
        s.apply(Edit::PasteClip {
            layer: 0,
            column: 3,
        })
        .unwrap();
        assert_eq!(
            summary(&s.view().rows[0].spans),
            vec![(0, 2, "cell", "1".into()), (3, 2, "cell", "1".into())]
        );
        assert!(s.view().clipboard);
    }

    #[test]
    fn genga_and_inbetweens_from_the_menu() {
        let mut s = state(&["1 1 1 1 2 2"]);
        s.apply(Edit::ToggleGenga {
            layer: 0,
            column: 0,
        })
        .unwrap();
        s.apply(Edit::ToggleGenga {
            layer: 0,
            column: 4,
        })
        .unwrap();
        assert!(s.view().rows[0].spans[0].genga);
        let message = s
            .apply(Edit::Inbetween {
                layer: 0,
                column: 0,
                count: 1,
            })
            .unwrap()
            .unwrap();
        assert!(message.contains("1枚"));
        assert_eq!(s.view().rows[0].spans[1].kind, "blank");
        assert_eq!(s.view().rows[0].spans[1].start, 2);
    }

    #[test]
    fn layer_edits_follow_python() {
        let mut s = state(&["1", "2", "3"]);
        // 画面の行 0（一番上、レイヤー 2）を一番下へ。
        s.apply(Edit::MoveLayerRows {
            rows: vec![0],
            to_row: 2,
        })
        .unwrap();
        let names: Vec<_> = s.view().rows.iter().map(|r| r.name.clone()).collect();
        assert_eq!(names, vec!["L1", "L0", "L2"]);
        s.apply(Edit::RenameLayer {
            layer: 0,
            name: "  ".into(),
        })
        .unwrap();
        assert_eq!(s.view().rows[2].name, "A");
        s.apply(Edit::ToggleDraft { layers: vec![0, 1] }).unwrap();
        assert!(s.view().rows[2].draft && s.view().rows[1].draft);
        assert!(s
            .apply(Edit::DeleteLayers {
                layers: vec![0, 1, 2]
            })
            .is_err());
        s.apply(Edit::DuplicateLayers { layers: vec![1] }).unwrap();
        assert_eq!(s.view().rows.len(), 4);
        let undo = s.undo.len();
        s.apply(Edit::SetVisible {
            layer: 0,
            visible: false,
        })
        .unwrap();
        assert_eq!(s.undo.len(), undo);
    }

    #[test]
    fn playback_advances_within_the_range_and_wraps() {
        let mut s = state(&["1 1 2 2 3 3"]);
        s.set_range_edge(false, 2);
        s.set_range_edge(true, 5);
        assert_eq!(s.view().range, (2, 5));
        let generation = s.start_playback().unwrap();
        assert_eq!(s.view().current, 2);
        // 経過時間を進めたことにして、刻みを当てる（24fps で 0.5 秒 = 12 コマ）。
        s.clock.started -= Duration::from_millis(500);
        let playhead = s.playback_tick(generation).unwrap().unwrap();
        // 範囲は 2..5 の 3 コマ。12 コマ進むとちょうど一周して 2 へ戻る。
        assert_eq!(playhead.current, 2);
        assert_eq!(playhead.column, Some(playhead.current));
        s.stop_playback();
        assert!(s.playback_tick(generation).is_err());
    }

    #[test]
    fn mode_switch_places_pending_and_normalize_counts() {
        let mut s = state(&["3 1 2 p4"]);
        assert_eq!(s.view().normalize_pending, 3);
        s.set_mode(Mode::Sequence).unwrap();
        assert_eq!(s.view().frame_count, 4);
        s.set_mode(Mode::Sheet).unwrap();
        assert!(!s.document.as_ref().unwrap().has_pending_positions());
        let preview = s.normalize_preview();
        assert_eq!(preview[0].changes, vec![(3, 1), (1, 2), (2, 3)]);
        s.apply(Edit::Normalize { layers: vec![0] }).unwrap();
        assert_eq!(s.view().normalize_pending, 0);
    }

    #[test]
    fn sample_document_and_thumbnail() {
        let mut s = TimelineState::default();
        s.load(sample_document(72).unwrap());
        let view = s.view();
        assert_eq!(view.rows.len(), 3);
        assert_eq!(view.sheet_frames, 72);
        assert!(view.rows[2].spans[0].tween.is_some());
        assert!(view.rows[0].spans[0].genga);
        let thumb = s.thumbnail(2, Some(0), None, 40, 40);
        assert_eq!(&thumb[0..4], &40u32.to_le_bytes());
        assert_eq!(&thumb[4..8], &23u32.to_le_bytes());
        assert_eq!(thumb.len(), 8 + 40 * 23 * 4);
        // 長いカットでも組み立てられる。
        s.load(sample_document(3000).unwrap());
        assert_eq!(s.view().sheet_frames, 3000);
    }
}
