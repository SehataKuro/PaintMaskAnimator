//! Undo の履歴の判断（`pma_core::document::history`）の Python 入口。

use pma_core::document::history as core;
use pyo3::prelude::*;

/// Undo 列の古い側から捨てる件数（`trim_undo_stack_python` と一致）。
#[pyfunction]
pub fn history_trim_count(entry_bytes: Vec<u64>, max_entries: usize, max_bytes: u64) -> usize {
    core::trim_count(&entry_bytes, max_entries, max_bytes)
}

/// 古い側が `removed` 件削られたあとの分岐点。捨てる分岐は None。
#[pyfunction]
pub fn history_shift_branch_positions(positions: Vec<i64>, removed: u64) -> Vec<Option<i64>> {
    core::shift_branch_positions(&positions, removed)
}

/// 分岐を足したあと、上限を超えて古い側から捨てる本数。
#[pyfunction]
pub fn history_branch_overflow(count: usize, max_branches: usize) -> usize {
    core::branch_overflow(count, max_branches)
}

/// 分岐へ切り替えるときに戻す手数。到達できない分岐は None。
#[pyfunction]
pub fn history_switch_steps(position: i64, undo_len: usize) -> Option<usize> {
    core::switch_steps(position, undo_len)
}

/// 一部の件を除いたあとの分岐点（分岐点より前に残る件の数）。
#[pyfunction]
pub fn history_retained_positions(keep: Vec<bool>, positions: Vec<i64>) -> Vec<usize> {
    core::retained_positions(&keep, &positions)
}
