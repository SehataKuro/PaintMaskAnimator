//! 取り消し（Undo）とやり直し（Redo）。
//!
//! 履歴の入れ物と判断は pma-core の `History<E>`（Python 版と同じ上限・分岐の扱い）。
//! 各件は「変える前の文書」をまるごと持つ。`Document` の画像と行は書くときに複製する
//! 共有（Arc）なので、複製しても変えたところしか増えない。
//!
//! 論理バイト（上限の判断に使う量）は、前の件と画素を共有していない画像の大きさの和で数える。
//! Python 版はタイルや範囲だけを持つ件もあるので、件ごとの量は同じにならない。

use pma_core::document::history::{History, HistoryItem, HistoryLimits, HistoryTarget};
use pma_core::document::model::Document;
// 使用色パネル（colors.rs）：件に使用色パネルの状態を添える。
use pma_core::used_colors::{Snapshot, UsedColors};
use serde::Serialize;

/// Python 版の constants.MAX_UNDO / MAX_UNDO_BYTES と canvas_undo.MAX_HISTORY_BRANCHES。
pub const LIMITS: HistoryLimits = HistoryLimits {
    max_entries: 30,
    max_bytes: 768 * 1024 * 1024,
    max_branches: 20,
};

#[derive(Clone)]
pub struct Entry {
    /// ヒストリーパネルに出す名前（「ブラシ」「コマを追加」など）。
    pub label: String,
    pub document: Document,
    pub bytes: u64,
    /// 使用色パネル（colors.rs）：変える前の使用色パネルの状態（並べ替え・親子・表示・統合など）。
    /// 戻したあと、使用色を数え直してから当てる（Qt の PaletteStateUndo・attach_palette_state）。
    pub palette: Option<Snapshot>,
}

impl HistoryItem for Entry {
    fn logical_bytes(&self) -> u64 {
        self.bytes
    }
}

/// 文書を取り替える先。`capture` は今の文書を、`apply` は件の文書を入れる。
pub struct Target<'a> {
    pub document: &'a mut Document,
    /// 使用色パネル（colors.rs）。
    pub palette: &'a mut UsedColors,
}

impl HistoryTarget<Entry> for Target<'_> {
    fn capture(&mut self, entry: &Entry) -> Option<Entry> {
        Some(Entry {
            label: entry.label.clone(),
            document: self.document.clone(),
            bytes: entry.bytes,
            palette: entry.palette.as_ref().map(|_| self.palette.capture()),
        })
    }

    fn apply(&mut self, entry: Entry) {
        *self.document = entry.document;
        if let Some(snapshot) = entry.palette {
            self.palette.restore_after_refresh(snapshot);
        }
    }
}

/// 画面へ渡す履歴の一覧。
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Listing {
    /// 古い順の Undo の名前。
    pub undo: Vec<String>,
    /// 次にやり直す順の Redo の名前。
    pub redo: Vec<String>,
    pub branches: usize,
}

pub struct Undo {
    history: History<Entry>,
}

impl Default for Undo {
    fn default() -> Self {
        Self {
            history: History::new(LIMITS),
        }
    }
}

impl Undo {
    /// 変える前に呼ぶ。今の文書を件として積む。
    pub fn record(&mut self, label: &str, before: &Document) {
        self.record_with_palette(label, before, None);
    }

    /// 使用色パネル（colors.rs）：使用色パネルの変える前の状態も添えて積む。
    pub fn record_with_palette(
        &mut self,
        label: &str,
        before: &Document,
        palette: Option<Snapshot>,
    ) {
        let bytes = image_bytes(before);
        self.history.push(Entry {
            label: label.to_string(),
            document: before.clone(),
            bytes,
            palette,
        });
    }

    pub fn undo(&mut self, document: &mut Document, palette: &mut UsedColors) -> bool {
        self.history.undo(&mut Target { document, palette })
    }

    pub fn redo(&mut self, document: &mut Document, palette: &mut UsedColors) -> bool {
        self.history.redo(&mut Target { document, palette })
    }

    pub fn can_undo(&self) -> bool {
        !self.history.undo_entries().is_empty()
    }

    pub fn can_redo(&self) -> bool {
        !self.history.redo_entries().is_empty()
    }

    pub fn listing(&self) -> Listing {
        Listing {
            undo: self
                .history
                .undo_entries()
                .iter()
                .map(|e| e.label.clone())
                .collect(),
            redo: self
                .history
                .redo_entries()
                .iter()
                .rev()
                .map(|e| e.label.clone())
                .collect(),
            branches: self.history.branches().len(),
        }
    }
}

/// 1枚の層の画像の大きさ（幅×高さ×4）。件の量の目安にする。
fn image_bytes(document: &Document) -> u64 {
    let (w, h) = document.size();
    (w as u64) * (h as u64) * 4
}
