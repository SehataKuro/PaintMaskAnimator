"""明示コマ列の移動と番号列の圧縮を Qt なしで確認する。"""
import pytest

from paintmaskanimator.sheet import runs_from_states, shift_slots_left, shift_slots_right


@pytest.mark.parametrize("shift, slots, start, amount, expected", [
    (shift_slots_right, ["A", "B", None], 0, 1, [None, "A", "B"]),
    (shift_slots_right, ["A", None, "B"], 0, 3,
     [None, None, None, "A", None, "B"]),
    (shift_slots_right, ["A", None, "B"], 2, 1, ["A", None, None, "B"]),
    (shift_slots_right, [0, False], -2, 0, [None, 0, False]),
    (shift_slots_right, [], 2, 0, [None, None, None]),
    (shift_slots_left, ["A", "B", "C"], 1, 1, ["B", "C", None]),
    (shift_slots_left, ["A", None, "B", None, "C"], 2, 3,
     ["A", "C", None, None, None]),
    (shift_slots_left, ["A", "B"], -1, -2, ["B", None]),
    (shift_slots_left, [], 0, 1, []),
    (shift_slots_left, ["A", None], 5, 2, ["A", None]),
])
def test_shift_slots(shift, slots, start, amount, expected):
    before = list(slots)
    result = shift(slots, start, amount)
    assert result == expected
    assert slots == before
    assert result is not slots


@pytest.mark.parametrize("states, expected", [
    ([], []),
    ([3], [(0, 1, 3)]),
    ([None, None, None], [(0, 3, None)]),
    ([1, None, 1, None], [(0, 1, 1), (1, 1, None), (2, 1, 1), (3, 1, None)]),
    ([2, 2, None, None, 3], [(0, 2, 2), (2, 2, None), (4, 1, 3)]),
])
def test_runs_from_states(states, expected):
    before = list(states)
    assert runs_from_states(states) == expected
    assert states == before
