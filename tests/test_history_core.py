"""Undo の履歴の判断（Rust の pma_core::document::history）と Python 版の突き合わせ。

履歴の入れ物（undo_stack・redo_stack・history_branches）は Python のリストのまま。
上限での切り詰め件数と分岐点の付け替えを Rust が決めるので、Python 版
（``*_python``）と同じ結果になることを確かめる。
"""
from __future__ import annotations

import random

import pytest

from paintmaskanimator import canvas_undo, native
from paintmaskanimator.canvas_undo import HistoryBranch, UndoMixin


class _Entry:
    __slots__ = ("name", "nbytes")

    def __init__(self, name, nbytes):
        self.name = name
        self.nbytes = nbytes

    def __repr__(self):  # pragma: no cover - 失敗時の表示用
        return f"_Entry({self.name!r}, {self.nbytes})"


class _History(UndoMixin):
    """push_undo と分岐の記録だけを動かす、キャンバスの代わり。"""

    def __init__(self):
        self.undo_stack = []
        self.redo_stack = []

    def undo_one(self):
        if self.undo_stack:
            self.redo_stack.append(self.undo_stack.pop())

    def redo_one(self):
        if self.redo_stack:
            self.undo_stack.append(self.redo_stack.pop())

    def shape(self):
        return (
            [entry.name for entry in self.undo_stack],
            [entry.name for entry in self.redo_stack],
            [(branch.position, [entry.name for entry in branch.entries], branch.label)
             for branch in self.history_branches],
        )


@pytest.fixture
def restore_limits():
    saved = (canvas_undo.MAX_UNDO, canvas_undo.MAX_UNDO_BYTES, canvas_undo.MAX_HISTORY_BRANCHES)
    yield
    canvas_undo.MAX_UNDO, canvas_undo.MAX_UNDO_BYTES, canvas_undo.MAX_HISTORY_BRANCHES = saved


@pytest.mark.parametrize("max_entries,max_bytes", [
    (30, 768 * 1024 * 1024), (5, 10**9), (30, 10_000), (1, 1), (3, 25), (0, 40), (0, 10**12),
])
def test_trim_undo_stack_matches_python(restore_limits, max_entries, max_bytes):
    canvas_undo.MAX_UNDO = max_entries
    canvas_undo.MAX_UNDO_BYTES = max_bytes
    rng = random.Random(61)
    for _ in range(300):
        stack = [_Entry(index, rng.choice([0, 0, 1, 7, 10_000, 64 * 64 * 4, 2**40]))
                 for index in range(rng.randrange(0, 45))]
        expected = canvas_undo.trim_undo_stack_python(list(stack))
        assert canvas_undo.trim_undo_stack(list(stack)) == expected


def test_trim_undo_stack_edge_cases(restore_limits):
    canvas_undo.MAX_UNDO = 30
    canvas_undo.MAX_UNDO_BYTES = 1
    assert canvas_undo.trim_undo_stack([]) == []
    huge = _Entry("huge", 2**62)
    assert canvas_undo.trim_undo_stack([huge]) == [huge]
    # 論理バイトの和が u64 を超えても、最新の1件だけ残る。
    stack = [_Entry(index, 2**63) for index in range(4)]
    assert canvas_undo.trim_undo_stack(list(stack)) == stack[-1:]


@pytest.mark.parametrize("delta", [0, -1, -2, -5, -40])
def test_shift_history_branches_matches_python(delta):
    rng = random.Random(62)
    for _ in range(100):
        positions = [rng.randrange(0, 40) for _ in range(rng.randrange(0, 22))]
        rust, python = _History(), _History()
        for history in (rust, python):
            history.history_branches.extend(
                HistoryBranch(position, [_Entry(index, 0)], f"b{index}")
                for index, position in enumerate(positions)
            )
        rust._shift_history_branches(delta)
        python._shift_history_branches_python(delta)
        assert rust.shape() == python.shape()


@pytest.mark.parametrize("count", range(0, 25))
@pytest.mark.parametrize("max_branches", [0, 1, 2, 20])
def test_branch_overflow_matches_python_slice(count, max_branches):
    branches = list(range(count))
    if len(branches) > max_branches:
        del branches[:-max_branches]
    assert native.history_branch_overflow(count, max_branches) == count - len(branches)


def _replay(history, steps):
    for kind, value in steps:
        if kind == "push":
            history.push_undo(value)
        elif kind == "undo":
            history.undo_one()
        else:
            history.redo_one()
    return history.shape()


@pytest.mark.parametrize("seed", range(8))
def test_push_undo_sequence_matches_python(monkeypatch, restore_limits, seed):
    """積む・戻す・進めるを乱数で混ぜても、Rust の判断と Python 版の結果が同じ。"""
    rng = random.Random(seed)
    canvas_undo.MAX_UNDO = rng.choice([3, 5, 30])
    canvas_undo.MAX_UNDO_BYTES = rng.choice([50, 200, 10**9])
    canvas_undo.MAX_HISTORY_BRANCHES = rng.choice([1, 3, 20])
    steps = []
    for index in range(250):
        kind = rng.choice(["push", "push", "undo", "undo", "redo"])
        steps.append((kind, _Entry(index, rng.choice([0, 10, 40, 120])) if kind == "push" else None))

    rust = _replay(_History(), steps)

    def python_cap(self, position):
        entries = list(self.redo_stack)
        if not entries:
            return None
        branch = HistoryBranch(max(0, int(position)), entries,
                               canvas_undo.history_label_for(entries[-1]))
        branches = self.history_branches
        branches.append(branch)
        if len(branches) > canvas_undo.MAX_HISTORY_BRANCHES:
            del branches[:-canvas_undo.MAX_HISTORY_BRANCHES]
        return branch

    monkeypatch.setattr(canvas_undo, "trim_undo_stack", canvas_undo.trim_undo_stack_python)
    monkeypatch.setattr(UndoMixin, "_shift_history_branches", UndoMixin._shift_history_branches_python)
    monkeypatch.setattr(UndoMixin, "_archive_redo_branch", python_cap)
    python = _replay(_History(), steps)
    assert rust == python
