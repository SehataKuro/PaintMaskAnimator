//! 取り消し（Undo）とやり直し（Redo）。
//!
//! 履歴の入れ物と判断は pma-core の `History<E>`（Python 版と同じ上限・分岐の扱い）。
//! 各件は「変える前の文書」をまるごと持つ。`Document` の画像と行は書くときに複製する
//! 共有（Arc）なので、複製しても変えたところしか増えない。
//!
//! 論理バイト（上限の判断に使う量）は、前の件と画素を共有していない画像の大きさの和で数える。
//! Python 版はタイルや範囲だけを持つ件もあるので、件ごとの量は同じにならない。

use pma_core::document::history::{
    branch_columns, History, HistoryItem, HistoryLimits, HistoryTarget,
};
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

    /// 上限を変える（環境設定の「取り消せる回数」「取り消し履歴のメモリ」）。
    pub fn set_limits(&mut self, limits: HistoryLimits) {
        self.history.set_limits(limits);
    }

    /// ヒストリーパネルのクリック：負なら戻す、正なら進める回数だけ動く
    /// （Qt の UsedColorController.jump_history）。動いた回数を返す。
    /// 使用色パネル（colors.rs）の状態も件ごとに戻す・進める。
    pub fn jump(&mut self, delta: i64, document: &mut Document, palette: &mut UsedColors) -> usize {
        let mut moved = 0;
        for _ in 0..delta.unsigned_abs() {
            let done = if delta < 0 {
                self.undo(document, palette)
            } else {
                self.redo(document, palette)
            };
            if !done {
                break;
            }
            moved += 1;
        }
        moved
    }

    /// 分岐へ切り替え、その未来を `steps` 手進める（Qt の switch_history_branch）。
    /// 到達できない分岐なら false（分岐は捨てる）。
    pub fn switch_branch(
        &mut self,
        index: usize,
        steps: usize,
        document: &mut Document,
        palette: &mut UsedColors,
    ) -> bool {
        if !self
            .history
            .switch_branch(index, &mut Target { document, palette })
        {
            return false;
        }
        for _ in 0..steps {
            if !self.redo(document, palette) {
                break;
            }
        }
        true
    }

    /// ヒストリーパネルのブロックの並び（Qt の HistoryPanel.refresh と同じ配置）。
    pub fn graph(&self) -> Graph {
        let undo = self.history.undo_entries();
        let redo = self.history.redo_entries();
        let n = undo.len();
        let mut nodes = Vec::new();
        let mut main = |label: &str, delta: i64, position: usize, future: bool| {
            nodes.push(Node {
                kind: "main",
                value: delta,
                steps: 0,
                label: label.to_string(),
                note: if position == n { "current" } else { "" },
                total: 0,
                col: 0,
                row: position,
                is_future: future,
                from: (position > 0).then(|| (0, position - 1)),
                curve: false,
            });
        };
        // 何も積まれていない最初の状態（全部戻せばここへ戻る）。
        main("開始状態", -(n as i64), 0, false);
        for (i, entry) in undo.iter().enumerate() {
            main(&entry.label, i as i64 - (n as i64 - 1), i + 1, false);
        }
        for k in 0..redo.len() {
            let entry = &redo[redo.len() - 1 - k];
            main(&entry.label, k as i64 + 1, n + k + 1, true);
        }
        let branches = self.history.branches();
        let spans: Vec<(i64, usize)> = branches
            .iter()
            .map(|b| (b.position as i64, b.entries.len()))
            .collect();
        for (index, col) in branch_columns(&spans) {
            let branch = &branches[index];
            let total = branch.entries.len();
            let mut prev = (0, branch.position);
            for k in 0..total {
                let entry = &branch.entries[total - 1 - k];
                let row = branch.position + 1 + k;
                nodes.push(Node {
                    kind: "branch",
                    value: index as i64,
                    steps: k + 1,
                    label: entry.label.clone(),
                    note: match (k, total) {
                        (0, 1) => "branch",
                        (0, _) => "branch_many",
                        _ => "",
                    },
                    total,
                    col,
                    row,
                    is_future: true,
                    from: Some(prev),
                    curve: k == 0,
                });
                prev = (col, row);
            }
        }
        Graph { nodes, current: n }
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

/// ヒストリーパネルのブロック1つ（Qt の _HistoryGraph のノード）。
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Node {
    /// "main"（本線）か "branch"（分岐）。
    pub kind: &'static str,
    /// 本線：動く回数（負なら戻す）。分岐：分岐の添字。
    pub value: i64,
    /// 分岐：切り替えたあと進める回数。
    pub steps: usize,
    pub label: String,
    /// 名前に添える言葉："current"（現在）・"branch"（分岐）・"branch_many"（分岐 N件）。
    pub note: &'static str,
    /// 分岐の件数（note が branch_many のとき）。
    pub total: usize,
    pub col: usize,
    pub row: usize,
    pub is_future: bool,
    /// つなぐ元のブロック（列, 行）。
    pub from: Option<(usize, usize)>,
    /// 別の列へ枝分かれする曲線か。
    pub curve: bool,
}

/// ヒストリーパネルの全体。`current` は現在のブロックの行（＝本線の添字）。
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Graph {
    pub nodes: Vec<Node>,
    pub current: usize,
}

/// 環境設定の上限（回数と MB）から履歴の上限を作る（preferences_dialog.apply_undo_preferences）。
pub fn limits(steps: i64, memory_mb: i64) -> HistoryLimits {
    HistoryLimits {
        max_entries: steps.max(1) as usize,
        max_bytes: (memory_mb.max(1) as u64) * 1024 * 1024,
        max_branches: LIMITS.max_branches,
    }
}

/// 1枚の層の画像の大きさ（幅×高さ×4）。件の量の目安にする。
fn image_bytes(document: &Document) -> u64 {
    let (w, h) = document.size();
    (w as u64) * (h as u64) * 4
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc(width: usize) -> Document {
        Document::new_blank(width, 4).unwrap()
    }

    #[test]
    fn jump_and_switch_branch_like_qt() {
        let mut undo = Undo::default();
        let mut current = doc(1);
        let mut palette = UsedColors::new();
        // A → B → C と積む（各件は変える前の文書）。
        for (label, width) in [("A", 2), ("B", 3), ("C", 4)] {
            undo.record(label, &current);
            current = doc(width);
        }
        assert_eq!(undo.jump(-2, &mut current, &mut palette), 2);
        assert_eq!(current.size().0, 2);
        // 戻ってから別の編集：残りの未来（B, C）は分岐になる。
        undo.record("D", &current);
        current = doc(9);
        let graph = undo.graph();
        assert_eq!(graph.current, 2);
        let branch: Vec<_> = graph.nodes.iter().filter(|n| n.kind == "branch").collect();
        assert_eq!(branch.len(), 2);
        assert_eq!(branch[0].label, "B");
        assert_eq!(branch[0].note, "branch_many");
        assert_eq!((branch[0].col, branch[0].row), (1, 2));
        assert_eq!(branch[0].from, Some((0, 1)));
        assert!(branch[0].curve);
        // 分岐の2つ目（C）まで進める。
        assert!(undo.switch_branch(0, 2, &mut current, &mut palette));
        assert_eq!(current.size().0, 4);
        let listing = undo.listing();
        assert_eq!(listing.undo, vec!["A", "B", "C"]);
        assert_eq!(listing.branches, 1);
        assert!(!undo.switch_branch(5, 0, &mut current, &mut palette));
        // 本線のノード：開始状態・A・B・C（現在）。
        let graph = undo.graph();
        let main: Vec<_> = graph.nodes.iter().filter(|n| n.kind == "main").collect();
        assert_eq!(main[0].label, "開始状態");
        assert_eq!(main[0].value, -3);
        assert_eq!(main[3].note, "current");
    }
}
