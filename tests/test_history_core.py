"""Undo の履歴の判断（Rust の pma_core::document::history）と Python 版の突き合わせ。

履歴の入れ物（undo_stack・redo_stack・history_branches）は Python のリストのまま。
上限での切り詰め件数と分岐点の付け替えを Rust が決めるので、Python 版
（``*_python``）と同じ結果になることを確かめる。
"""
from __future__ import annotations

import random

import pytest

from paintmaskanimator import canvas_undo, native
from paintmaskanimator.canvas_selection import SelectionMixin
from paintmaskanimator.canvas_undo import HistoryBranch, UndoMixin
from paintmaskanimator.undo_entries import TransformUndo


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


class _Transform(TransformUndo):
    """変形の履歴の1件（名前つき）。"""

    def __init__(self, name):
        super().__init__((), 1, 1, ())
        self.name = name


class _Canvas(_History):
    """本物の undo・redo・分岐の切り替え・変形の履歴の除去を動かすキャンバスの代わり。

    戻すときの「今の状態」は名前に ``~`` を付けた写し。``stale`` の名前の件は対象が
    既に無い古い履歴として捨てられる。
    """

    transform_active = False

    def __init__(self, stale=()):
        super().__init__()
        self.stale = set(stale)

    def _commit_brush_stroke(self):
        pass

    def _invalidate_display_caches(self):
        pass

    def current_state_for(self, entry):
        if entry.name in self.stale:
            return None
        name = f"~{entry.name}"
        return _Transform(name) if isinstance(entry, TransformUndo) else _Entry(name, entry.nbytes)

    def apply_undo_entry(self, entry):
        pass


def _replay_all(history, steps):
    """手順を当て、最後の履歴の形と、分岐の切り替えが返した値の並びを返す。"""
    switched = []
    for kind, value in steps:
        if kind == "push":
            history.push_undo(value)
        elif kind == "undo":
            history.undo()
        elif kind == "redo":
            history.redo()
        elif kind == "switch":
            switched.append(history.switch_history_branch(value))
        elif kind == "switch_object":
            branches = history.history_branches
            if branches:
                switched.append(history.switch_history_branch(branches[value % len(branches)]))
        else:
            SelectionMixin._discard_transform_history(history)
    return history.shape(), switched


@pytest.mark.parametrize("seed", range(12))
def test_switch_and_discard_sequence_matches_python(monkeypatch, restore_limits, seed):
    """分岐の切り替えと変形の履歴の除去を混ぜても、Rust の判断と Python 版の結果が同じ。"""
    rng = random.Random(100 + seed)
    canvas_undo.MAX_UNDO = rng.choice([3, 6, 30])
    canvas_undo.MAX_UNDO_BYTES = rng.choice([60, 10**9])
    canvas_undo.MAX_HISTORY_BRANCHES = rng.choice([2, 20])
    stale = {index for index in range(300) if rng.random() < 0.05}
    steps = []
    for index in range(300):
        kind = rng.choice(["push", "push", "push", "undo", "undo", "redo",
                           "switch", "switch_object", "discard"])
        if kind == "push":
            value = _Transform(index) if rng.random() < 0.3 else _Entry(index, rng.choice([0, 10, 40]))
        elif kind == "switch":
            value = rng.randrange(-1, 4)
        else:
            value = rng.randrange(0, 10)
        steps.append((kind, value))

    rust = _replay_all(_Canvas(stale), steps)

    monkeypatch.setattr(UndoMixin, "switch_history_branch", UndoMixin.switch_history_branch_python)
    monkeypatch.setattr(SelectionMixin, "_discard_transform_history",
                        SelectionMixin._discard_transform_history_python)
    python = _replay_all(_Canvas(stale), steps)
    assert rust == python


def test_switch_history_branch_drops_unreachable_branch():
    history = _Canvas()
    for name in ("a", "b"):
        history.push_undo(_Entry(name, 0))
    history.undo()
    history.push_undo(_Entry("c", 0))  # 分岐点 1
    history.undo()
    history.undo()
    assert history.switch_history_branch(0) is False
    assert history.history_branches == []


@pytest.mark.parametrize("undo_len", [0, 1, 5])
@pytest.mark.parametrize("position", [0, 1, 3, 5, 6, 40])
def test_switch_steps_matches_python(position, undo_len):
    expected = None if position > undo_len else undo_len - position
    assert native.history_switch_steps(position, undo_len) == expected


def test_retained_positions_follow_python_slices():
    rng = random.Random(64)
    for _ in range(200):
        keep = [rng.random() < 0.6 for _ in range(rng.randrange(0, 15))]
        positions = [rng.randrange(-20, 20) for _ in range(rng.randrange(0, 6))]
        expected = [sum(keep[:position]) for position in positions]
        assert native.history_retained_positions(keep, positions) == expected
