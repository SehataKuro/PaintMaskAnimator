import numpy as np
import pytest

from paintmaskanimator import auto_paint, auto_trace, bucket_fill, reference_check


SKIN = (255, 241, 232)
LEAF = (197, 174, 103)
RED = (255, 0, 0)
FINISH = (160, 110, 75)


def sample():
    raw = np.zeros((50, 60, 4), dtype=np.uint8)
    raw[5, 5:55] = raw[44, 5:55] = (0, 0, 0, 255)
    raw[5:45, 5] = raw[5:45, 54] = (0, 0, 0, 255)
    raw[6:44, 30] = (*RED, 255)
    guide = raw.copy()
    guide[6:44, 6:30] = (*SKIN, 255)
    guide[6:44, 31:54] = (*LEAF, 255)
    guide[6:44, 30] = (*FINISH, 255)
    return raw, guide


def run(raw, guide, target=None, options=None):
    target = raw if target is None else target
    plan = auto_paint.plan_auto_paint(
        [guide, target], 0, lines=[raw, target], gpu="off",
        bucket_options=options or bucket_fill.BucketOptions(),
    )
    return auto_paint.apply_paint(target, plan.cells[1]), plan.cells[1]


def test_trace_does_not_take_the_colour_of_a_large_adjacent_face():
    raw, guide = sample()
    guide[6:44, 30] = (*SKIN, 255)
    after, result = run(raw, guide)
    assert not np.any(np.all(after[6:44, 30, :3] == SKIN, axis=1))
    assert result.undecided  # 見本に有効な置換色が無ければ、周囲の色を捏造しない。


def test_enclosed_cheek_colour_is_not_forbidden_as_the_large_outer_skin():
    raw = np.zeros((80, 100, 4), dtype=np.uint8)
    raw[1, 1:99] = raw[78, 1:99] = (0, 0, 0, 255)
    raw[1:79, 1] = raw[1:79, 98] = (0, 0, 0, 255)
    green = (0, 255, 0, 255)
    raw[30, 40:61] = raw[50, 40:61] = green
    raw[30:51, 40] = raw[30:51, 60] = green
    raw[10:21, 10] = green
    guide = raw.copy()
    guide[2:78, 2:98] = (*SKIN, 255)
    pink = (255, 223, 212, 255)
    guide[30:51, 40:61] = pink
    guide[10:21, 10] = (*FINISH, 255)
    after, _result = run(raw, guide)
    assert np.all(after[30:51, 40:61] == pink)
    assert tuple(after[15, 10]) == (*FINISH, 255)


def test_learned_finish_replaces_a_trace_without_a_matching_reference_shape():
    raw, guide = sample()
    target = raw.copy()
    target[20:26, 12] = (*RED, 255)
    after, _result = run(raw, guide, target)
    assert np.all(after[20:26, 12] == (*FINISH, 255))
    assert np.all(after[6:44, 30] == (*FINISH, 255))
    assert not auto_paint.primary_line_mask(after).any()


def test_even_a_one_pixel_trace_is_finished():
    raw, guide = sample()
    target = raw.copy()
    target[20, 12] = (*RED, 255)
    after, _result = run(raw, guide, target)
    assert tuple(after[20, 12]) == (*FINISH, 255)
    assert not auto_paint.primary_line_mask(after).any()


def test_human_reference_trace_rules_reach_cells_outside_direct_shape_matching(monkeypatch):
    raw, guide = sample()
    original = auto_trace.palette

    def human_only(reference):
        return original(reference) if reference.index == 0 else []

    monkeypatch.setattr(auto_trace, "palette", human_only)
    cells = [guide, raw.copy(), raw.copy(), raw.copy(), raw.copy()]
    plan = auto_paint.plan_auto_paint(
        cells, 0, lines=[raw] * len(cells), gpu="off", max_rounds=1,
        bucket_options=bucket_fill.BucketOptions(),
    )
    after = auto_paint.apply_paint(cells[-1], plan.cells[len(cells) - 1])
    assert np.all(after[6:44, 30] == (*FINISH, 255))
    assert not auto_paint.primary_line_mask(after).any()


def test_automatic_inclusive_fill_cannot_merge_trace_into_the_large_face():
    raw, guide = sample()
    options = bucket_fill.BucketOptions(include_masks=True, mask_colors=(RED,),
                                       island_replacements=((RED, SKIN),))
    after, _result = run(raw, guide, options=options)
    assert options.include_masks  # 手動ツールの設定自体は変えない。
    assert np.all(after[6:44, 6:30] == (*SKIN, 255))
    assert np.all(after[6:44, 30] == (*FINISH, 255))
    assert np.all(after[6:44, 31:54] == (*LEAF, 255))


def test_broad_manual_target_cannot_remove_solid_boundaries_in_auto_paint():
    raw, guide = sample()
    options = bucket_fill.BucketOptions(
        adjacent=False, target_color=bucket_fill.TARGET_OPAQUE_AND_INNER_TRANSPARENT,
    )
    after, _result = run(raw, guide, options=options)
    black = np.all(raw[:, :, :3] == 0, axis=2) & (raw[:, :, 3] > 0)
    np.testing.assert_array_equal(after[black], raw[black])
    assert np.all(after[6:44, 30] == (*FINISH, 255))


def test_specific_context_outweighs_a_context_free_trace_rule():
    skin, finish = reference_check._pack(SKIN), reference_check._pack(FINISH)
    unrelated = reference_check._pack(LEAF)
    palettes = [[(RED, frozenset([skin]), finish, 20),
                 (RED, frozenset(), unrelated, 1000)]]
    assert auto_trace.choose(RED, frozenset([skin]), palettes, []) == FINISH


def test_conflicting_finish_rules_remain_undecided():
    host = frozenset([reference_check._pack(SKIN)])
    palettes = [[(RED, host, reference_check._pack(FINISH), 20),
                 (RED, host, reference_check._pack(LEAF), 20)]]
    assert auto_trace.choose(RED, host, palettes, []) is None
    assert auto_trace.choose(RED, host, palettes, [FINISH]) == FINISH


def test_small_exactly_matched_trace_does_not_need_a_large_learning_sample():
    host = frozenset([reference_check._pack(SKIN)])
    palettes = [[(RED, host, reference_check._pack(FINISH), 2)]]
    assert auto_trace.choose(RED, host, palettes, [FINISH]) == FINISH
    assert auto_trace.choose(RED, host, palettes, []) is None


def test_source_recolour_and_undecided_flags_invalidate_learned_finishes():
    raw, colors = sample()
    guide = auto_paint._Cell(colors)
    guide.initial = auto_paint._Cell(raw)
    first = auto_trace.palette(guide)
    assert any(rule[2] == reference_check._pack(FINISH) for rule in first)
    replacement = (120, 90, 110)
    guide.values[6:44, 30] = reference_check._pack(replacement)
    second = auto_trace.palette(guide)
    assert any(rule[2] == reference_check._pack(replacement) for rule in second)
    assert not any(rule[2] == reference_check._pack(FINISH) for rule in second)
    guide.undecided[20, 30] = True
    assert not auto_trace.palette(guide)


def test_finish_palette_keeps_multiple_colours_on_one_original_trace():
    raw, colors = sample()
    other = (240, 167, 160)
    colors[6:25, 30] = (*other, 255)
    guide = auto_paint._Cell(colors)
    guide.initial = auto_paint._Cell(raw)
    table = auto_trace.finish_palette([guide])
    assert table[RED] == {reference_check._pack(FINISH): 19,
                          reference_check._pack(other): 19}
    # 形の照合先で確認できた色を、粗い周辺文脈だけで落とさない。
    host = frozenset([reference_check._pack(SKIN)])
    rules = [[(RED, host, reference_check._pack(other), 19)]]
    assert auto_trace.choose(RED, host, rules, [FINISH], finishes=table) == FINISH


def test_finish_palette_ignores_noise_pending_and_original_colours():
    raw, colors = sample()
    colors[6, 30] = (*LEAF, 255)
    colors[7, 30] = (*RED, 255)
    guide = auto_paint._Cell(colors)
    guide.initial = auto_paint._Cell(raw)
    guide.undecided[8, 30] = True
    assert auto_trace.finish_palette([guide])[RED] == {reference_check._pack(FINISH): 35}


def test_plan_builds_human_finish_palette_once(monkeypatch):
    raw, colors = sample()
    original = auto_trace.finish_palette
    calls = []
    contexts = []
    original_context = auto_trace.palette

    def build(guides):
        items = list(guides)
        calls.append([guide.index for guide in items])
        return original(items)

    monkeypatch.setattr(auto_trace, "finish_palette", build)
    def context(guide):
        contexts.append(guide.index)
        return original_context(guide)

    monkeypatch.setattr(auto_trace, "palette", context)
    plan = auto_paint.plan_auto_paint([colors, raw, raw, raw], 0, lines=[raw] * 4,
                                     bucket_options=bucket_fill.BucketOptions(), gpu="off")
    assert calls == [[0]]
    assert contexts == [0]
    assert plan.trace_palette[RED] == {reference_check._pack(FINISH): 38}


def test_unique_palette_finish_does_not_require_a_matching_shape_or_context():
    table = {RED: {reference_check._pack(FINISH): 20}}
    assert auto_trace.choose(RED, frozenset(), [], [], finishes=table) == FINISH
    assert auto_trace.choose(RED, frozenset([reference_check._pack(FINISH)]),
                             [], [], finishes=table) is None


def test_small_enclosed_face_selects_one_of_multiple_palette_colours():
    raw = np.zeros((80, 100, 4), dtype=np.uint8)
    raw[1, 1:99] = raw[78, 1:99] = (0, 0, 0, 255)
    raw[1:79, 1] = raw[1:79, 98] = (0, 0, 0, 255)
    green = (0, 255, 0, 255)
    raw[30, 40:61] = raw[50, 40:61] = green
    raw[30:51, 40] = raw[30:51, 60] = green
    geometry = auto_paint._Cell(raw, target=True)
    from paintmaskanimator.auto_bucket import BucketWork
    work = BucketWork(geometry, bucket_fill.BucketOptions())
    filled = next(e[3] for e in work.collect() if e[3].target == green[:3])
    painted = geometry.values.copy()
    pink = (255, 223, 212)
    painted[31:50, 41:60] = reference_check._pack(pink)
    allowed = {reference_check._pack(pink): 20, reference_check._pack(FINISH): 30}
    assert auto_trace.small_face_finish(geometry, filled, painted, allowed) == pink


@pytest.mark.parametrize("source_enclosed", [True, False])
def test_palette_can_fill_changed_enclosed_face_only_with_a_learned_example(monkeypatch,
                                                                          source_enclosed):
    from paintmaskanimator import auto_bucket
    raw = np.zeros((80, 100, 4), dtype=np.uint8)
    raw[1, 1:99] = raw[78, 1:99] = (0, 0, 0, 255)
    raw[1:79, 1] = raw[1:79, 98] = (0, 0, 0, 255)
    initial = raw.copy()
    green = (0, 255, 0, 255)
    pink = (255, 223, 212, 255)
    colors = raw.copy()
    colors[2:78, 2:98] = (*SKIN, 255)
    if source_enclosed:
        initial[30, 40:61] = initial[50, 40:61] = green
        initial[30:51, 40] = initial[30:51, 60] = green
        colors[30:51, 40:61] = pink
    else:
        initial[10:26, 40] = green
        colors[10:26, 40] = pink
    target = raw.copy()
    target[25, 30:46] = target[55, 30:46] = green
    target[25:56, 30] = target[25:56, 45] = green
    monkeypatch.setattr(auto_bucket.AlignmentCache, "shift", lambda *args: ((0, 0), 0.0))
    plan = auto_paint.plan_auto_paint([colors, target], 0, lines=[initial, target],
                                     bucket_options=bucket_fill.BucketOptions(), gpu="off")
    after = auto_paint.apply_paint(target, plan.cells[1])
    assert np.all(after[25, 30:46] == pink)  # パレットが一意なので形の照合なしで選べる。
    if source_enclosed:
        assert np.all(after[26:55, 31:45] == pink)
    else:
        assert not after[26:55, 31:45, 3].any()  # 斜線の色を囲みの中へ拡大しない。


def test_automatic_result_cannot_add_a_new_finish_to_the_human_palette(monkeypatch):
    from paintmaskanimator import auto_bucket
    raw, colors = sample()
    original = auto_bucket.paint_cell
    count = 0

    def paint(*args, **kwargs):
        nonlocal count
        result = original(*args, **kwargs)
        count += 1
        if count == 1:
            result.paints = [(LEAF if rgb == FINISH else rgb, bounds, mask)
                             for rgb, bounds, mask in result.paints]
        return result

    monkeypatch.setattr(auto_bucket, "paint_cell", paint)
    plan = auto_paint.plan_auto_paint([colors, raw, raw], 0, lines=[raw] * 3,
                                     bucket_options=bucket_fill.BucketOptions(),
                                     gpu="off", max_rounds=1)
    assert plan.trace_palette[RED] == {reference_check._pack(FINISH): 38}
    after = auto_paint.apply_paint(raw, plan.cells[2])
    assert np.all(after[6:44, 30] == (*FINISH, 255))
