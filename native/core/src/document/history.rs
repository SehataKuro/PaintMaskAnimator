//! Undo の履歴の判断：上限での切り詰めと、捨てられた未来（分岐）の記録。
//!
//! 履歴の入れ物（Undo 列・Redo 列・分岐の一覧）は呼び出し側が持つ。ここは
//! 「何件捨てるか・分岐点をどう付け替えるか」だけを返す。Python 版
//! （`canvas_undo.trim_undo_stack_python` など）と同じ結果を返す。
//!
//! [`History`] は同じ判断で動く入れ物で、Undo の各件を型引数で持つ。
//! 画面を Rust へ移すときの履歴の本体になる。

/// 履歴の上限。0 の項目は「その上限で捨てない」（Python 版の `del xs[:-0]` と同じ）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct HistoryLimits {
    /// Undo 列に残す件数の上限。
    pub max_entries: usize,
    /// Undo 列に残す論理バイト（画像の幅×高さ×4 の和）の上限。
    pub max_bytes: u64,
    /// 残す分岐の本数の上限。
    pub max_branches: usize,
}

/// Undo 列の古い側から捨てる件数。
///
/// まず件数を `max_entries` までに、次に残りの論理バイトの和が `max_bytes` 以下になるまで
/// 古いものから捨てる。最低1件は残す（1回の大きな編集も Undo できるように）。
/// `entry_bytes` は古い順の各件の論理バイト。`canvas_undo.trim_undo_stack_python` と一致する。
pub fn trim_count(entry_bytes: &[u64], max_entries: usize, max_bytes: u64) -> usize {
    let mut drop = 0;
    if max_entries > 0 && entry_bytes.len() > max_entries {
        drop = entry_bytes.len() - max_entries;
    }
    let mut total: u128 = entry_bytes[drop..].iter().map(|&bytes| bytes as u128).sum();
    while entry_bytes.len() - drop > 1 && total > max_bytes as u128 {
        total -= entry_bytes[drop] as u128;
        drop += 1;
    }
    drop
}

/// Undo 列の古い側が `removed` 件削られたあとの分岐点。
///
/// 分岐点（分岐が適用できる状態の Undo 列の長さ）を `removed` だけ前へずらし、
/// 負になる分岐は `None`（捨てる）にする。`canvas_undo._shift_history_branches_python` と一致する。
pub fn shift_branch_positions(positions: &[i64], removed: u64) -> Vec<Option<i64>> {
    positions
        .iter()
        .map(|&position| {
            let shifted = position as i128 - removed as i128;
            if shifted >= 0 {
                Some(i64::try_from(shifted).unwrap_or(i64::MAX))
            } else {
                None
            }
        })
        .collect()
}

/// 分岐を足したあと、上限を超えて古い側から捨てる本数。`max_branches` が 0 なら捨てない。
pub fn branch_overflow(count: usize, max_branches: usize) -> usize {
    if max_branches > 0 && count > max_branches {
        count - max_branches
    } else {
        0
    }
}

/// Undo 列の1件。論理バイトは上限での切り詰めに使う。
pub trait HistoryItem {
    /// 持っている画像の論理バイト（幅×高さ×4 の和）。
    fn logical_bytes(&self) -> u64;
}

/// 捨てられた「もう一つの未来」1本分。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HistoryBranch<E> {
    /// 分岐点：この分岐の Redo 列が適用できる状態の Undo 列の長さ。
    pub position: usize,
    /// 捨てられた Redo 列の写し。末尾が分岐点の直後に進む1手。
    pub entries: Vec<E>,
}

/// Undo 列・Redo 列・分岐の一覧を持つ履歴。判断はこのモジュールの関数で行う。
#[derive(Clone, Debug)]
pub struct History<E> {
    limits: HistoryLimits,
    undo: Vec<E>,
    redo: Vec<E>,
    branches: Vec<HistoryBranch<E>>,
}

impl<E: HistoryItem> History<E> {
    /// 空の履歴。
    pub fn new(limits: HistoryLimits) -> Self {
        Self {
            limits,
            undo: Vec::new(),
            redo: Vec::new(),
            branches: Vec::new(),
        }
    }

    /// 上限を変える。今ある列は次に積んだときに切り詰める。
    pub fn set_limits(&mut self, limits: HistoryLimits) {
        self.limits = limits;
    }

    /// Undo 列（古い順）。
    pub fn undo_entries(&self) -> &[E] {
        &self.undo
    }

    /// Redo 列（末尾が次に進む1手）。
    pub fn redo_entries(&self) -> &[E] {
        &self.redo
    }

    /// 分岐の一覧（古い順）。
    pub fn branches(&self) -> &[HistoryBranch<E>] {
        &self.branches
    }

    /// 履歴を空にする（新規作成・ファイルを開いたとき）。
    pub fn clear(&mut self) {
        self.undo.clear();
        self.redo.clear();
        self.branches.clear();
    }

    /// 1件積み、上限まで切り詰め、Redo 列があれば分岐として残す。
    ///
    /// Python 版 `UndoMixin.push_undo` と同じ順で処理する。
    pub fn push(&mut self, entry: E) {
        self.undo.push(entry);
        let bytes: Vec<u64> = self.undo.iter().map(HistoryItem::logical_bytes).collect();
        let removed = trim_count(&bytes, self.limits.max_entries, self.limits.max_bytes);
        if removed > 0 {
            self.undo.drain(..removed);
            self.shift_branches(removed);
        }
        if !self.redo.is_empty() {
            // 分岐点は「今の編集を積む直前の状態」＝新しい1件の手前。
            self.archive_redo(self.undo.len() - 1);
        }
    }

    /// 1手戻す。`capture` は戻す前の今の状態（Redo 用の逆向きの1件）を返し、
    /// `None` なら既に無い対象の古い履歴として捨てて次の件へ進む。
    /// 戻す1件（呼び出し側が当てる）を返す。
    pub fn undo(&mut self, mut capture: impl FnMut(&E) -> Option<E>) -> Option<E> {
        while let Some(entry) = self.undo.pop() {
            if let Some(current) = capture(&entry) {
                self.redo.push(current);
                return Some(entry);
            }
        }
        None
    }

    /// 1手進める。[`History::undo`] の逆向き（Python 版と同じく切り詰めない）。
    pub fn redo(&mut self, mut capture: impl FnMut(&E) -> Option<E>) -> Option<E> {
        while let Some(entry) = self.redo.pop() {
            if let Some(current) = capture(&entry) {
                self.undo.push(current);
                return Some(entry);
            }
        }
        None
    }

    fn shift_branches(&mut self, removed: usize) {
        let positions: Vec<i64> = self
            .branches
            .iter()
            .map(|branch| i64::try_from(branch.position).unwrap_or(i64::MAX))
            .collect();
        let shifted = shift_branch_positions(&positions, removed as u64);
        let mut kept = Vec::with_capacity(self.branches.len());
        for (mut branch, position) in self.branches.drain(..).zip(shifted) {
            if let Some(position) = position {
                branch.position = position as usize;
                kept.push(branch);
            }
        }
        self.branches = kept;
    }

    fn archive_redo(&mut self, position: usize) {
        let entries = std::mem::take(&mut self.redo);
        if entries.is_empty() {
            return;
        }
        self.branches.push(HistoryBranch { position, entries });
        let overflow = branch_overflow(self.branches.len(), self.limits.max_branches);
        self.branches.drain(..overflow);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Clone, Debug, PartialEq, Eq)]
    struct Edit {
        name: &'static str,
        bytes: u64,
    }

    impl HistoryItem for Edit {
        fn logical_bytes(&self) -> u64 {
            self.bytes
        }
    }

    fn edit(name: &'static str) -> Edit {
        Edit { name, bytes: 0 }
    }

    const LIMITS: HistoryLimits = HistoryLimits {
        max_entries: 30,
        max_bytes: 768 * 1024 * 1024,
        max_branches: 20,
    };

    #[test]
    fn trim_count_cuts_count_then_bytes() {
        assert_eq!(trim_count(&[1; 12], 5, 1_000_000_000), 7);
        // 50×50×4 = 10000 バイトずつなら、上限 10000 で最新の1件だけ残る。
        assert_eq!(trim_count(&[10_000; 5], 30, 10_000), 4);
        assert_eq!(trim_count(&[3, 3, 3], 30, 6), 1);
        assert_eq!(trim_count(&[], 30, 1), 0);
    }

    #[test]
    fn trim_count_keeps_one_huge_entry() {
        assert_eq!(trim_count(&[64 * 64 * 4], 30, 1), 0);
        assert_eq!(trim_count(&[5, 64 * 64 * 4], 30, 1), 1);
    }

    #[test]
    fn zero_limits_do_not_trim() {
        // Python 版の del xs[:-0] は何も消さない。
        assert_eq!(trim_count(&[0; 40], 0, 1), 0);
        assert_eq!(branch_overflow(25, 0), 0);
        assert_eq!(branch_overflow(21, 20), 1);
        assert_eq!(branch_overflow(20, 20), 0);
    }

    #[test]
    fn shift_drops_branches_before_the_kept_history() {
        assert_eq!(
            shift_branch_positions(&[0, 2, 5], 2),
            vec![None, Some(0), Some(3)]
        );
        assert_eq!(shift_branch_positions(&[3], 0), vec![Some(3)]);
    }

    #[test]
    fn new_edit_after_undo_keeps_old_future_as_branch() {
        let mut history = History::new(LIMITS);
        history.push(edit("red"));
        assert_eq!(
            history.undo(|_| Some(edit("before red"))),
            Some(edit("red"))
        );
        history.push(edit("blue"));
        assert!(history.redo_entries().is_empty());
        assert_eq!(history.branches().len(), 1);
        let branch = &history.branches()[0];
        assert_eq!(branch.position, history.undo_entries().len() - 1);
        assert_eq!(branch.entries, vec![edit("before red")]);
    }

    #[test]
    fn trimming_shifts_and_drops_branches() {
        let limits = HistoryLimits {
            max_entries: 2,
            ..LIMITS
        };
        let mut history = History::new(limits);
        history.push(edit("a"));
        history.undo(|_| Some(edit("not a")));
        history.push(edit("b")); // 分岐点 0
        history.push(edit("c"));
        assert_eq!(history.branches()[0].position, 0);
        history.push(edit("d")); // b を捨てて分岐点が -1 になる
        assert!(history.branches().is_empty());
        let names: Vec<_> = history.undo_entries().iter().map(|e| e.name).collect();
        assert_eq!(names, vec!["c", "d"]);
    }

    #[test]
    fn branch_count_is_capped_oldest_first() {
        let limits = HistoryLimits {
            max_branches: 2,
            ..LIMITS
        };
        let mut history = History::new(limits);
        for name in ["a", "b", "c", "d"] {
            history.push(edit(name));
            history.undo(|entry| Some(entry.clone()));
        }
        history.push(edit("e"));
        let kept: Vec<_> = history
            .branches()
            .iter()
            .map(|branch| branch.entries[0].name)
            .collect();
        assert_eq!(kept, vec!["c", "d"]);
    }

    #[test]
    fn undo_skips_entries_whose_target_is_gone() {
        let mut history = History::new(LIMITS);
        history.push(edit("kept"));
        history.push(edit("gone"));
        let undone = history.undo(|entry| (entry.name == "kept").then(|| edit("inverse")));
        assert_eq!(undone, Some(edit("kept")));
        assert!(history.undo_entries().is_empty());
        assert_eq!(history.redo_entries(), &[edit("inverse")]);
        assert_eq!(history.redo(|_| Some(edit("again"))), Some(edit("inverse")));
        assert_eq!(history.undo_entries(), &[edit("again")]);
    }
}
