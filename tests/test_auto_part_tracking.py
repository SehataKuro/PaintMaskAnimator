import numpy as np

from paintmaskanimator import auto_bucket, auto_paint, bucket_fill, line_flow
from paintmaskanimator.auto_part_tracking import PartTracker, _FlowPair, _FlowPath, _in_mask


CREAM = (249, 245, 231, 255)
YELLOW = (234, 191, 53, 255)


def drawing():
    raw = np.zeros((160, 160, 4), dtype=np.uint8)
    raw[20, 20:140] = raw[139, 20:140] = (0, 0, 0, 255)
    raw[20:140, 20] = raw[20:140, 139] = (0, 0, 0, 255)
    colors = raw.copy()
    colors[21:139, 21:139] = CREAM
    return raw, colors


def guide(raw, colors):
    cell = auto_paint._Cell(colors)
    cell.initial = auto_paint._Cell(raw)
    return cell


def inside(raw):
    target = auto_paint._Cell(raw, target=True)
    work = auto_bucket.BucketWork(target, bucket_fill.BucketOptions())
    filled = next(entry[3] for entry in work.collect() if entry[3].bounds == (21, 21, 139, 139))
    return target, work, filled


def test_memory_keeps_an_older_success_across_empty_cells():
    raw, colors = drawing()
    tracker = PartTracker(raw.shape[:2])
    tracker.remember_fixed(0, guide(raw, colors))
    for index in range(1, 8):
        tracker.remember(index, auto_paint.CellPaint())
    _target, _work, filled = inside(raw)
    assert tracker.donors(8, filled) == [0]


def test_tracks_a_confirmed_part_across_more_than_four_missing_cells(monkeypatch):
    raw, colors = drawing()
    original = auto_bucket.paint_cell
    calls = 0

    def paint(pixels, guides, options, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 7:
            guides = []
            kwargs.update(part_guides=[], tracked_color=None)
        return original(pixels, guides, options, **kwargs)

    monkeypatch.setattr(auto_bucket, "paint_cell", paint)
    plan = auto_paint.plan_auto_paint([colors] + [raw] * 7, 0, lines=[raw] * 8,
                                     bucket_options=bucket_fill.BucketOptions(),
                                     gpu="off", max_rounds=1)
    assert not auto_paint.apply_paint(raw, plan.cells[6])[80, 80, 3]
    after = auto_paint.apply_paint(raw, plan.cells[7])
    assert np.all(after[21:139, 21:139] == CREAM)
    assert plan.optimization_stats["long_memory_parts"] > 0
    expected = bucket_fill.compute_fill_region(raw, 80, 80, bucket_fill.BucketOptions()).region
    np.testing.assert_array_equal(after[~expected], raw[~expected])


def test_deformation_recovers_a_sheared_part_without_a_rigid_match(monkeypatch):
    raw = np.zeros((192, 256, 4), dtype=np.uint8)
    raw[20, 80:161] = raw[170, 80:161] = (0, 0, 0, 255)
    raw[20:171, 80] = raw[20:171, 160] = (0, 0, 0, 255)
    colors = raw.copy()
    colors[21:170, 81:160] = CREAM
    moved = np.zeros_like(raw)
    for y in range(20, 171):
        dx = int(round((y - 95) * 0.25))
        moved[y, 80 + dx:161 + dx] = raw[y, 80:161]
    target = auto_paint._Cell(moved, target=True)
    work = auto_bucket.BucketWork(target, bucket_fill.BucketOptions())
    entry = next(e for e in work.collect() if 0 < e[3].bounds[0] < 100 and e[3].bounds[1] == 21)
    reference = guide(raw, colors)
    monkeypatch.setattr(work.alignment, "shift", lambda *args: ((0, 0), 0.2))
    assert work.choose_color(reference, 0, entry, 48, {}) is None
    tracker = PartTracker(raw.shape[:2])
    assert tracker._color(0, target, entry[3], reference) == CREAM[:3]


def test_tracking_rejects_mixed_or_unconfirmed_reference_colours():
    raw, colors = drawing()
    target, _work, filled = inside(raw)
    tracker = PartTracker(raw.shape[:2])
    assert tracker._tiled_color(0, target, filled, guide(raw, colors)) == CREAM[:3]
    mixed = colors.copy()
    mixed[21:139, 80:139] = YELLOW
    assert tracker._color(0, target, filled, guide(raw, mixed)) is None
    assert tracker._tiled_color(0, target, filled, guide(raw, mixed)) is None
    partial = guide(raw, colors)
    partial.confirmed = np.zeros(raw.shape[:2], dtype=bool)
    partial.confirmed[21:60, 21:139] = True
    assert tracker._color(0, target, filled, partial) is None
    assert tracker._tiled_color(0, target, filled, partial) is None


def test_adjacent_flow_pairs_are_reused_in_both_directions(monkeypatch):
    raw, colors = drawing()
    cell = guide(raw, colors)
    shifted = guide(np.roll(raw, 2, axis=1), np.roll(colors, 2, axis=1))
    tracker = PartTracker(raw.shape[:2])
    original = line_flow.estimate_flow
    calls = []

    def estimate(*args):
        calls.append(None)
        return original(*args)

    monkeypatch.setattr(line_flow, "estimate_flow", estimate)
    forward = tracker._pair(cell.initial, shifted.initial)
    backward = tracker._pair(shifted.initial, cell.initial)
    assert len(calls) == 2
    assert forward.forward is backward.backward


def test_composed_flow_rejects_an_inconsistent_return_path():
    def constant(dx):
        grid = np.ones((6, 6))
        return line_flow.Flow(grid * 0, grid * dx, grid, grid * 10, (192, 192))

    valid = _FlowPath([_FlowPair(constant(3), constant(-3), 0, 0),
                       _FlowPair(constant(4), constant(-4), 0, 0)], (192, 192))
    ys, xs = np.array([80, 90]), np.array([70, 90])
    sy, sx, by, bx, inside = valid.coordinates(ys, xs)
    np.testing.assert_array_equal(sy, ys)
    np.testing.assert_array_equal(sx, xs + 7)
    np.testing.assert_array_equal(by, ys)
    np.testing.assert_array_equal(bx, xs)
    assert inside.all()
    invalid = _FlowPath([_FlowPair(constant(57), constant(0), 0, 0)], (192, 192))
    _sy, _sx, by, bx, _inside = invalid.coordinates(ys, xs)
    region = np.ones((30, 40), dtype=bool)
    assert not _in_mask(by, bx, 60, 70, region).any()
