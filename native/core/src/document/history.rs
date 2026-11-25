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

/// 分岐へ切り替えるときに戻す手数。分岐点が今の Undo 列の長さを超える分岐は到達できないので
/// `None`（呼び出し側が捨てる）。`UndoMixin.switch_history_branch_python` と一致する。
pub fn switch_steps(position: i64, undo_len: usize) -> Option<usize> {
    let undo_len = undo_len as i128;
    let position = position as i128;
    if position > undo_len {
        return None;
    }
    Some(usize::try_from(undo_len - position).unwrap_or(usize::MAX))
}

/// 一部の件を除いたあとの分岐点：分岐点より前に残る件の数。
///
/// `keep` は古い順の Undo 列の各件を残すか。数える範囲は Python のスライス
/// `old_stack[:position]` と同じ（負なら末尾から数える）。
/// `canvas_selection._discard_transform_history_python` と一致する。
pub fn retained_positions(keep: &[bool], positions: &[i64]) -> Vec<usize> {
    let len = keep.len() as i128;
    let mut prefix = Vec::with_capacity(keep.len() + 1);
    let mut kept = 0usize;
    prefix.push(kept);
    for &value in keep {
        kept += usize::from(value);
        prefix.push(kept);
    }
    positions
        .iter()
        .map(|&position| {
            let position = position as i128;
            let end = if position < 0 {
                (len + position).max(0)
            } else {
                position.min(len)
            };
            prefix[end as usize]
        })
        .collect()
}

/// Undo 列の1件。論理バイトは上限での切り詰めに使う。
pub trait HistoryItem {
    /// 持っている画像の論理バイト（幅×高さ×4 の和）。
    fn logical_bytes(&self) -> u64;
}

/// 履歴を当てる先（文書と画面）。Python 版の `current_state_for`・`apply_undo_entry` に当たる。
pub trait HistoryTarget<E> {
    /// 戻す（進める）前の今の状態を、逆向きの1件として写す。`None` なら対象が既に無い
    /// 古い履歴として捨て、次の件へ進む。
    fn capture(&mut self, entry: &E) -> Option<E>;
    /// 1件を当てる。
    fn apply(&mut self, entry: E);
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

    /// 1手戻す。対象が既に無い件は捨てて次の件へ進む。戻せたら `true`。
    pub fn undo(&mut self, target: &mut impl HistoryTarget<E>) -> bool {
        while let Some(entry) = self.undo.pop() {
            if let Some(current) = target.capture(&entry) {
                self.redo.push(current);
                target.apply(entry);
                return true;
            }
        }
        false
    }

    /// 1手進める。[`History::undo`] の逆向き（Python 版と同じく切り詰めない）。
    pub fn redo(&mut self, target: &mut impl HistoryTarget<E>) -> bool {
        while let Some(entry) = self.redo.pop() {
            if let Some(current) = target.capture(&entry) {
                self.undo.push(current);
                target.apply(entry);
                return true;
            }
        }
        false
    }

    /// 分岐点まで戻し、その分岐の未来を Redo 列にする。今の Redo 列は入れ替わりに
    /// 分岐として残す。範囲外の添字なら `false`、到達できない分岐は捨てて `false`。
    ///
    /// Python 版 `UndoMixin.switch_history_branch` と同じ順で処理する。
    pub fn switch_branch(&mut self, index: usize, target: &mut impl HistoryTarget<E>) -> bool {
        let Some(branch) = self.branches.get(index) else {
            return false;
        };
        let position = i64::try_from(branch.position).unwrap_or(i64::MAX);
        let Some(steps) = switch_steps(position, self.undo.len()) else {
            self.branches.remove(index);
            return false;
        };
        for _ in 0..steps {
            if self.undo.is_empty() {
                break;
            }
            self.undo(target);
        }
        let branch = self.branches.remove(index);
        self.archive_redo(self.undo.len());
        self.redo = branch.entries;
        true
    }

    /// `keep` が偽の件を Undo 列・Redo 列・分岐から除く（変形の確定・取り消しのとき）。
    ///
    /// 分岐点は分岐点より前に残る件の数に付け替え、空になった分岐は捨てる。
    pub fn retain(&mut self, keep: impl Fn(&E) -> bool) {
        let kept: Vec<bool> = self.undo.iter().map(&keep).collect();
        let positions: Vec<i64> = self
            .branches
            .iter()
            .map(|branch| i64::try_from(branch.position).unwrap_or(i64::MAX))
            .collect();
        for (branch, position) in self
            .branches
            .iter_mut()
            .zip(retained_positions(&kept, &positions))
        {
            branch.position = position;
            branch.entries.retain(&keep);
        }
        self.branches.retain(|branch| !branch.entries.is_empty());
        self.undo.retain(&keep);
        self.redo.retain(&keep);
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

    /// 描いた線の並びの写し（全体の写しで戻す Undo の1件）。
    #[derive(Clone, Debug, PartialEq, Eq)]
    struct Snapshot {
        strokes: Vec<&'static str>,
        bytes: u64,
        transform: bool,
    }

    impl HistoryItem for Snapshot {
        fn logical_bytes(&self) -> u64 {
            self.bytes
        }
    }

    /// 描いた線の並びを持つキャンバスの代わり。
    #[derive(Default)]
    struct Canvas {
        strokes: Vec<&'static str>,
        /// 写せない（対象が既に無い）件の線の数。
        gone: Option<usize>,
    }

    impl Canvas {
        fn snapshot(&self) -> Snapshot {
            Snapshot {
                strokes: self.strokes.clone(),
                bytes: 0,
                transform: false,
            }
        }

        fn paint(&mut self, history: &mut History<Snapshot>, stroke: &'static str) {
            history.push(self.snapshot());
            self.strokes.push(stroke);
        }
    }

    impl HistoryTarget<Snapshot> for Canvas {
        fn capture(&mut self, entry: &Snapshot) -> Option<Snapshot> {
            if self.gone == Some(entry.strokes.len()) {
                return None;
            }
            Some(Snapshot {
                transform: entry.transform,
                ..self.snapshot()
            })
        }

        fn apply(&mut self, entry: Snapshot) {
            self.strokes = entry.strokes;
        }
    }

    fn entry(bytes: u64) -> Snapshot {
        Snapshot {
            strokes: Vec::new(),
            bytes,
            transform: false,
        }
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
    fn switch_steps_rejects_unreachable_branch() {
        assert_eq!(switch_steps(2, 5), Some(3));
        assert_eq!(switch_steps(5, 5), Some(0));
        assert_eq!(switch_steps(6, 5), None);
    }

    #[test]
    fn retained_positions_count_kept_entries_before_the_point() {
        let keep = [true, false, true, false, true];
        assert_eq!(
            retained_positions(&keep, &[0, 1, 2, 4, 5, 9]),
            vec![0, 1, 1, 2, 3, 3]
        );
        // Python の old_stack[:-1] と同じく、負は末尾から数える。
        assert_eq!(retained_positions(&keep, &[-1, -5, -9]), vec![2, 0, 0]);
        assert_eq!(retained_positions(&[], &[0, 3]), vec![0, 0]);
    }

    #[test]
    fn new_edit_after_undo_keeps_old_future_as_branch() {
        let mut history = History::new(LIMITS);
        let mut canvas = Canvas::default();
        canvas.paint(&mut history, "red");
        assert!(history.undo(&mut canvas));
        canvas.paint(&mut history, "blue");
        assert!(history.redo_entries().is_empty());
        assert_eq!(history.branches().len(), 1);
        let branch = &history.branches()[0];
        assert_eq!(branch.position, history.undo_entries().len() - 1);
        assert_eq!(branch.entries.len(), 1);
    }

    #[test]
    fn switch_branch_restores_the_other_future_and_back() {
        let mut history = History::new(LIMITS);
        let mut canvas = Canvas::default();
        canvas.paint(&mut history, "red");
        history.undo(&mut canvas);
        canvas.paint(&mut history, "blue");
        assert_eq!(canvas.strokes, vec!["blue"]);

        assert!(history.switch_branch(0, &mut canvas));
        assert_eq!(canvas.strokes, Vec::<&str>::new());
        assert_eq!(history.branches().len(), 1);
        assert!(history.redo(&mut canvas));
        assert_eq!(canvas.strokes, vec!["red"]);

        assert!(history.switch_branch(0, &mut canvas));
        history.redo(&mut canvas);
        assert_eq!(canvas.strokes, vec!["blue"]);
        assert!(!history.switch_branch(1, &mut canvas));
    }

    #[test]
    fn unreachable_branch_is_dropped() {
        let mut history = History::new(LIMITS);
        let mut canvas = Canvas::default();
        canvas.paint(&mut history, "a");
        canvas.paint(&mut history, "b");
        history.undo(&mut canvas);
        canvas.paint(&mut history, "c"); // 分岐点 1
        history.undo(&mut canvas);
        history.undo(&mut canvas); // Undo 列は空、分岐点 1 には戻せない
        assert!(!history.switch_branch(0, &mut canvas));
        assert!(history.branches().is_empty());
    }

    #[test]
    fn trimming_shifts_and_drops_branches() {
        let limits = HistoryLimits {
            max_entries: 2,
            ..LIMITS
        };
        let mut history = History::new(limits);
        let mut canvas = Canvas::default();
        canvas.paint(&mut history, "a");
        history.undo(&mut canvas);
        canvas.paint(&mut history, "b"); // 分岐点 0
        canvas.paint(&mut history, "c");
        assert_eq!(history.branches()[0].position, 0);
        canvas.paint(&mut history, "d"); // 最古の1件を捨てて分岐点が -1 になる
        assert!(history.branches().is_empty());
        let kept: Vec<_> = history
            .undo_entries()
            .iter()
            .map(|entry| entry.strokes.clone())
            .collect();
        assert_eq!(kept, vec![vec!["b"], vec!["b", "c"]]);
    }

    #[test]
    fn branch_count_is_capped_oldest_first() {
        let limits = HistoryLimits {
            max_branches: 2,
            ..LIMITS
        };
        let mut history = History::new(limits);
        let mut canvas = Canvas::default();
        for stroke in ["a", "b", "c", "d"] {
            canvas.paint(&mut history, stroke);
            history.undo(&mut canvas);
        }
        canvas.paint(&mut history, "e");
        let kept: Vec<_> = history
            .branches()
            .iter()
            .map(|branch| branch.entries[0].strokes.clone())
            .collect();
        assert_eq!(kept, vec![vec!["c"], vec!["d"]]);
    }

    #[test]
    fn undo_skips_entries_whose_target_is_gone() {
        let mut history = History::new(LIMITS);
        let mut canvas = Canvas::default();
        canvas.paint(&mut history, "kept");
        canvas.paint(&mut history, "gone");
        canvas.gone = Some(1);
        assert!(history.undo(&mut canvas));
        assert_eq!(canvas.strokes, Vec::<&str>::new());
        assert!(history.undo_entries().is_empty());
        assert_eq!(history.redo_entries().len(), 1);
        assert!(!history.undo(&mut canvas));
    }

    #[test]
    fn retain_removes_transform_entries_everywhere() {
        let mut history = History::new(LIMITS);
        let transform = Snapshot {
            transform: true,
            ..entry(0)
        };
        history.push(entry(1));
        history.push(transform.clone());
        history.push(entry(2));
        history.redo = vec![transform.clone(), entry(3)];
        history.branches = vec![
            HistoryBranch {
                position: 3,
                entries: vec![entry(4)],
            },
            HistoryBranch {
                position: 1,
                entries: vec![transform.clone()],
            },
        ];
        history.retain(|entry| !entry.transform);
        let bytes = |entries: &[Snapshot]| entries.iter().map(|e| e.bytes).collect::<Vec<_>>();
        assert_eq!(bytes(history.undo_entries()), vec![1, 2]);
        assert_eq!(bytes(history.redo_entries()), vec![3]);
        assert_eq!(history.branches().len(), 1);
        assert_eq!(history.branches()[0].position, 2);
    }
}
