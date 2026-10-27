"""Automatic clicks use the real bucket, including TP lines and bucket options."""
import numpy as np
import pytest

from paintmaskanimator import auto_bucket, auto_paint, bucket_fill


CREAM = (249, 245, 231)
YELLOW = (234, 191, 53)
RED = (255, 0, 0)
FINISH = (160, 110, 75)


def drawing():
    raw = np.zeros((50, 60, 4), dtype=np.uint8)
    raw[5, 5:55] = raw[44, 5:55] = (0, 0, 0, 255)
    raw[5:45, 5] = raw[5:45, 54] = (0, 0, 0, 255)
    raw[6:44, 30] = (*RED, 255)
    guide = raw.copy()
    guide[6:44, 6:31] = (*CREAM, 255)
    guide[6:44, 31:54] = (*YELLOW, 255)
    guide[6:44, 30] = (*FINISH, 255)
    return raw, guide


@pytest.mark.parametrize("options", [
    bucket_fill.BucketOptions(),
    bucket_fill.BucketOptions(extend_lines=True),
    bucket_fill.BucketOptions(close_gap=True, gap_width=2),
    bucket_fill.BucketOptions(include_masks=True, mask_colors=(RED,)),
])
def test_automatic_click_is_identical_to_manual_bucket(options):
    raw, _guide = drawing()
    expected = bucket_fill.compute_fill_region(raw, 15, 20, options)
    filled, paints = auto_bucket.execute_click(
        raw, auto_bucket.BucketClick(15, 20, CREAM), options,
    )
    actual = auto_paint.apply_paint(raw, auto_paint.CellPaint(paints=paints))
    assert np.array_equal(filled.region, expected.region)
    assert np.all(actual[expected.region] == (*CREAM, 255))
    assert np.array_equal(actual[~expected.region], raw[~expected.region])


def test_real_auto_plan_uses_bucket_and_does_not_keep_a_previous_wrong_colour():
    raw, guide = drawing()
    wrong = raw.copy()
    wrong[6:44, 6:30] = (*YELLOW, 255)
    calls = []
    original = bucket_fill.compute_fill_region

    plan = auto_paint.plan_auto_paint(
        [guide, raw], 0, lines=[raw, raw], hints=[None, wrong],
        bucket_options=bucket_fill.BucketOptions(),
    )
    result = plan.cells[1]
    after = auto_paint.apply_paint(raw, result)
    assert np.all(after[6:44, 6:30] == (*CREAM, 255))
    assert np.all(after[6:44, 31:54] == (*YELLOW, 255))
    assert result.clicks
    replay = raw.copy()
    for x, y, rgb in result.clicks:
        filled = original(raw, x, y, bucket_fill.BucketOptions())
        calls.append(filled.region)
        replay[filled.region] = (*rgb, 255)
    assert np.array_equal(after, replay)
    for _rgb, (x0, y0, x1, y1), mask in result.paints:
        whole = np.zeros(raw.shape[:2], dtype=bool)
        whole[y0:y1, x0:x1] = mask
        assert any(np.array_equal(whole, region) for region in calls)


def test_reference_colour_edges_cannot_cut_a_bucket_region():
    raw, guide = drawing()
    raw[6:44, 30] = 0  # 対象には区切り線が無い。
    plan = auto_paint.plan_auto_paint(
        [guide, raw], 0, lines=[raw, raw],
        bucket_options=bucket_fill.BucketOptions(),
    )
    after = auto_paint.apply_paint(raw, plan.cells[1])
    assert not after[6:44, 6:54, 3].any()
    assert plan.cells[1].unsure > 0


def test_a_dominant_finished_colour_cannot_replace_missing_line_evidence():
    raw, guide = drawing()
    raw[6:44, 30] = 0
    guide[6:44, 6:44] = (*CREAM, 255)
    guide[6:44, 44:54] = (*YELLOW, 255)
    plan = auto_paint.plan_auto_paint(
        [guide, raw], 0, lines=[raw, raw], bucket_options=bucket_fill.BucketOptions(), gpu="off",
    )
    assert not auto_paint.apply_paint(raw, plan.cells[1])[6:44, 6:54, 3].any()


def test_deformed_faces_match_the_original_lines_without_copying_reference_edges(monkeypatch):
    raw, guide = drawing()
    target = raw.copy()
    target[6:44, 30] = 0
    target[6:44, 36] = (*RED, 255)
    monkeypatch.setattr(auto_bucket.AlignmentCache, "shift", lambda *args: ((0, 0), 1.0))
    plan = auto_paint.plan_auto_paint(
        [guide, target], 0, lines=[raw, target],
        bucket_options=bucket_fill.BucketOptions(), gpu="off",
    )
    after = auto_paint.apply_paint(target, plan.cells[1])
    assert np.all(after[6:44, 6:36] == (*CREAM, 255))
    assert np.all(after[6:44, 37:54] == (*YELLOW, 255))
    assert np.array_equal(after[5, :], target[5, :])


def test_a_one_pixel_trace_offset_uses_the_same_original_trace_colour(monkeypatch):
    raw, guide = drawing()
    target = raw.copy()
    target[6:44, 30] = 0
    target[6:44, 31] = (*RED, 255)
    monkeypatch.setattr(auto_bucket.AlignmentCache, "shift", lambda *args: ((0, 0), 1.0))
    plan = auto_paint.plan_auto_paint(
        [guide, target], 0, lines=[raw, target],
        bucket_options=bucket_fill.BucketOptions(), gpu="off",
    )
    after = auto_paint.apply_paint(target, plan.cells[1])
    assert np.all(after[6:44, 31] == (*FINISH, 255))


def test_ambiguous_tp_line_stays_instead_of_getting_reference_fragments():
    raw, guide = drawing()
    guide[6:25, 30] = (*CREAM, 255)
    guide[25:44, 30] = (*YELLOW, 255)
    plan = auto_paint.plan_auto_paint(
        [guide, raw], 0, lines=[raw, raw],
        bucket_options=bucket_fill.BucketOptions(),
    )
    after = auto_paint.apply_paint(raw, plan.cells[1])
    assert np.array_equal(after[6:44, 30], raw[6:44, 30])


def test_rounds_reuse_regions_and_reconsider_only_changed_reference_faces(monkeypatch):
    raw, colors = drawing()
    target = auto_paint._Cell(raw, target=True)
    work = auto_bucket.BucketWork(target, bucket_fill.BucketOptions())

    def guide(image):
        result = auto_paint._Cell(image)
        result.initial = auto_paint._Cell(raw)
        return result

    def paint(reference):
        return auto_bucket.paint_cell(raw, [reference], work.options, target=target,
                                      margin=48, work=work)

    first = paint(guide(colors))
    solves = work.decision_solves
    alignments = work.alignment.cpu_solves
    regions = work.entries
    second = paint(guide(colors.copy()))
    assert work.entries is regions
    assert work.decision_solves == solves
    assert work.reused_decisions > 0
    assert work.alignment.cpu_solves == alignments
    np.testing.assert_array_equal(auto_paint.apply_paint(raw, first),
                                  auto_paint.apply_paint(raw, second))
    changed = colors.copy()
    changed[6:44, 31:54] = (*CREAM, 255)
    third = paint(guide(changed))
    after = auto_paint.apply_paint(raw, third)
    assert np.all(after[6:44, 31:54] == (*CREAM, 255))
    assert work.decision_solves == solves + 1

    pending = guide(changed)
    pending.open[20, 40] = True
    fourth = paint(pending)
    assert not auto_paint.apply_paint(raw, fourth)[20, 40, 3]


def test_multi_round_plan_matches_recomputing_every_region(monkeypatch):
    raw, colors = drawing()
    new_part = raw.copy()
    new_part[15, 10:19] = new_part[24, 10:19] = (0, 0, 0, 255)
    new_part[15:25, 10] = new_part[15:25, 18] = (0, 0, 0, 255)
    cells = [colors, raw, new_part]
    monkeypatch.setattr(auto_paint, "CONFIDENT_UNSURE_SHARE", 0.3)
    kwargs = dict(lines=[raw, raw, new_part], bucket_options=bucket_fill.BucketOptions(),
                  max_rounds=3, gpu="off")
    cached = auto_paint.plan_auto_paint(cells, 0, **kwargs)
    original = auto_bucket.paint_cell

    def recompute(*args, **options):
        options.pop("work", None)
        return original(*args, **options)

    monkeypatch.setattr(auto_bucket, "paint_cell", recompute)
    uncached = auto_paint.plan_auto_paint(cells, 0, **kwargs)
    assert cached.rounds >= 2
    assert cached.rounds == uncached.rounds
    assert cached.optimization_stats["reused_decisions"] > 0
    assert not auto_paint.apply_paint(cells[2], cached.cells[2])[20, 14, 3]
    for index in cached.cells:
        assert cached.cells[index].clicks == uncached.cells[index].clicks
        np.testing.assert_array_equal(auto_paint.apply_paint(cells[index], cached.cells[index]),
                                      auto_paint.apply_paint(cells[index], uncached.cells[index]))
def test_finished_colour_can_match_a_face_split_by_original_hatching():
    from paintmaskanimator import auto_bucket, auto_paint, bucket_fill
    raw = np.zeros((60, 80, 4), dtype=np.uint8)
    raw[10, 10:70] = raw[49, 10:70] = (0, 0, 0, 255)
    raw[10:50, 10] = raw[10:50, 69] = (0, 0, 0, 255)
    initial = raw.copy()
    initial[11:49, 40] = (255, 0, 0, 255)
    guide_pixels = raw.copy()
    pink = (255, 223, 212)
    guide_pixels[11:49, 11:69] = (*pink, 255)
    target = auto_paint._Cell(raw, target=True)
    guide = auto_paint._Cell(guide_pixels)
    guide.initial = auto_paint._Cell(initial)
    work = auto_bucket.BucketWork(target, bucket_fill.BucketOptions())
    result = auto_bucket.paint_cell(raw, [guide], work.options, target=target,
                                    margin=48, work=work)
    after = auto_paint.apply_paint(raw, result)
    assert np.all(after[11:49, 11:69] == (*pink, 255))


def test_small_target_cannot_sample_an_unrelated_large_finished_face():
    from paintmaskanimator import auto_bucket, auto_paint, bucket_fill
    raw = np.zeros((60, 80, 4), dtype=np.uint8)
    raw[10, 10:70] = raw[49, 10:70] = (0, 0, 0, 255)
    raw[10:50, 10] = raw[10:50, 69] = (0, 0, 0, 255)
    target_pixels = raw.copy()
    target_pixels[25, 30:41] = target_pixels[35, 30:41] = (0, 0, 0, 255)
    target_pixels[25:36, 30] = target_pixels[25:36, 40] = (0, 0, 0, 255)
    guide_pixels = raw.copy()
    guide_pixels[11:49, 11:69] = (255, 223, 212, 255)
    guide = auto_paint._Cell(guide_pixels)
    guide.initial = auto_paint._Cell(raw)
    target = auto_paint._Cell(target_pixels, target=True)
    work = auto_bucket.BucketWork(target, bucket_fill.BucketOptions())
    entry = next(e for e in work.collect() if e[3].bounds == (31, 26, 40, 35))
    assert work._sample_face_color(guide, entry[3], 48) is None


def test_small_finished_face_can_refine_a_four_pixel_alignment_error(monkeypatch):
    raw = np.zeros((60, 80, 4), dtype=np.uint8)
    raw[15, 15:35] = raw[34, 15:35] = (0, 0, 0, 255)
    raw[15:35, 15] = raw[15:35, 34] = (0, 0, 0, 255)
    initial = np.roll(raw, 4, axis=1)
    colors = initial.copy()
    colors[16:34, 20:38] = (*CREAM, 255)
    target = auto_paint._Cell(raw, target=True)
    guide = auto_paint._Cell(colors)
    guide.initial = auto_paint._Cell(initial)
    work = auto_bucket.BucketWork(target, bucket_fill.BucketOptions())
    filled = next(e[3] for e in work.collect() if e[3].bounds == (16, 16, 34, 34))
    monkeypatch.setattr(work.alignment, "shift", lambda *args: ((0, 0), 0.7))
    assert work._sample_face_color(guide, filled, 48) is None
    assert work._refine_face_color(guide, filled, 48) == CREAM
    guide.open[20, 25] = True
    assert work._refine_face_color(guide, filled, 48) is None


def test_large_split_face_can_use_part_of_one_uniform_reference_face(monkeypatch):
    raw = np.zeros((220, 260, 4), dtype=np.uint8)
    raw[10, 10:250] = raw[209, 10:250] = (0, 0, 0, 255)
    raw[10:210, 10] = raw[10:210, 249] = (0, 0, 0, 255)
    initial = raw.copy()
    raw[11:209, 120] = (255, 0, 0, 255)
    colors = initial.copy()
    colors[11:209, 11:249] = (*CREAM, 255)
    target = auto_paint._Cell(raw, target=True)
    guide = auto_paint._Cell(colors)
    guide.initial = auto_paint._Cell(initial)
    work = auto_bucket.BucketWork(target, bucket_fill.BucketOptions())
    filled = next(e[3] for e in work.collect() if e[3].bounds == (11, 11, 120, 209))
    assert work._sample_face_color(guide, filled, 48) == CREAM
    guide.open[20, 20] = True
    assert work._sample_face_color(guide, filled, 48) is None
    guide.open[:] = False
    monkeypatch.setattr(work.alignment, "shift", lambda *args: ((0, 0), 0.1))
    assert work._sample_face_color(guide, filled, 48) is None


def test_confirmed_parts_can_skip_an_unfinished_neighbour(monkeypatch):
    raw, colors = drawing()
    original = auto_bucket.paint_cell
    calls = []

    def paint(pixels, guides, options, **kwargs):
        calls.append(None)
        if len(calls) == 2:
            # 中間セルで対応を見失っても、以前に確定した部位を捨てない。
            guides = []
            kwargs["part_guides"] = []
            kwargs["tracked_color"] = None
        return original(pixels, guides, options, **kwargs)

    monkeypatch.setattr(auto_bucket, "paint_cell", paint)
    plan = auto_paint.plan_auto_paint([colors, raw, raw, raw], 0, lines=[raw] * 4,
                                     bucket_options=bucket_fill.BucketOptions(),
                                     gpu="off", max_rounds=1)
    middle = auto_paint.apply_paint(raw, plan.cells[2])
    last = auto_paint.apply_paint(raw, plan.cells[3])
    assert not middle[20, 15, 3]
    assert tuple(last[20, 15]) == (*CREAM, 255)
    assert tuple(last[20, 40]) == (*YELLOW, 255)


def test_part_inheritance_uses_only_enough_confirmed_pixels(monkeypatch):
    raw, colors = drawing()
    target = auto_paint._Cell(raw, target=True)
    guide = auto_paint._Cell(colors)
    guide.initial = auto_paint._Cell(raw)
    guide.confirmed = np.zeros(raw.shape[:2], dtype=bool)
    guide.confirmed[6:34, 6:30] = True
    work = auto_bucket.BucketWork(target, bucket_fill.BucketOptions())
    filled = next(e[3] for e in work.collect() if e[3].bounds == (6, 6, 30, 44))
    assert work.part_color(guide, filled, 48) == CREAM
    guide.confirmed[20:] = False
    assert work.part_color(guide, filled, 48) is None


def test_large_part_can_grow_from_confirmed_uniform_majority(monkeypatch):
    raw = np.zeros((180, 180, 4), dtype=np.uint8)
    raw[10, 10:170] = raw[169, 10:170] = (0, 0, 0, 255)
    raw[10:170, 10] = raw[10:170, 169] = (0, 0, 0, 255)
    colors = raw.copy()
    colors[11:169, 11:169] = (*CREAM, 255)
    guide = auto_paint._Cell(colors)
    guide.initial = auto_paint._Cell(raw)
    guide.confirmed = np.zeros(raw.shape[:2], dtype=bool)
    guide.confirmed[11:142, 11:169] = True
    guide.open[142:169, 11:169] = True
    target = auto_paint._Cell(raw, target=True)
    work = auto_bucket.BucketWork(target, bucket_fill.BucketOptions())
    filled = next(e[3] for e in work.collect() if e[3].bounds == (11, 11, 169, 169))
    monkeypatch.setattr(work.alignment, "shift", lambda *args: ((0, 0), 0.5))
    assert work.part_color(guide, filled, 48) == CREAM
    guide.confirmed[120:] = False
    assert work.part_color(guide, filled, 48) is None
    guide.confirmed[:] = True
    monkeypatch.setattr(work.alignment, "shift", lambda *args: ((0, 0), 0.3))
    assert work.part_color(guide, filled, 48) is None


