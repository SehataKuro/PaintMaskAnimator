import numpy as np
import pytest

from paintmaskanimator import auto_alignment, auto_motion, auto_paint, bucket_fill, line_flow


def problem(dy, dx, seed=14):
    rng = np.random.default_rng(seed)
    before = np.zeros((56, 72), dtype=np.float32)
    before[18:35, 20:50] = rng.random((17, 30)) < 0.12
    exact = np.roll(before, (dy, dx), (0, 1))
    after = exact * (1 - auto_paint.NEAR_LINE_WEIGHT) + bucket_fill.dilate(
        exact.astype(bool), 1).astype(np.float32) * auto_paint.NEAR_LINE_WEIGHT
    size = auto_paint._correlation_size(before, 8)
    shifts = np.r_[0:9, -8:0]
    return dict(before=before, after=after, total=float(before.sum()), step=1,
                size=size, limit=8, dys=shifts, dxs=shifts)


@pytest.mark.parametrize('shift', [(0, 0), (1, -1), (3, -2), (-2, 3)])
def test_certified_motion_matches_full_fft(shift):
    for seed in range(8):
        candidate = problem(*shift, seed)
        actual = auto_motion.certify(candidate, shift)
        expected = auto_paint._solve_local_shift(candidate)
        assert actual is not None
        assert actual[0] == expected[0]
        assert actual[1] == pytest.approx(expected[1], abs=2e-6)


def test_incorrect_hint_or_tied_motion_cannot_skip_full_search():
    candidate = problem(3, -2)
    assert auto_motion.certify(candidate, (-8, 8)) is None
    candidate['before'][:] = 0
    candidate['before'][25, 35] = 1
    candidate['after'][:] = 0
    candidate['after'][25, [33, 37]] = 1
    candidate['total'] = 1.0
    assert auto_motion.certify(candidate, (0, 2)) is None


def test_shared_field_reuses_motion_but_is_bounded():
    memory = auto_motion.MotionMemory(capacity=2)
    flow = line_flow.Flow(np.ones((2, 2)), np.full((2, 2), 2), np.ones((2, 2)),
                          np.full((2, 2), 30), (64, 64))
    memory.register('pair', flow, 10, 20)
    assert memory.predict('pair', (15, 25, 30, 40)) == (1, 2)
    memory.learn('other', (0, 0, 20, 20), ((2, -1), 1.0))
    assert memory.predict('other', (10, 10, 40, 40)) == (2, -1)
    assert memory.predict('other', (1000, 1000, 1100, 1100)) is None
    for index in range(5):
        memory.register(index, flow, 0, 0)
        memory.learn(index, (0, 0, 1, 1), ((0, 0), 1.0))
    assert len(memory.fields) == len(memory.samples) == 2


def test_neighboring_regions_can_share_one_fft_without_changing_answers():
    raw = np.zeros((128, 160, 4), dtype=np.uint8)
    raw[50:65:3, 50:65:4] = (0, 0, 0, 255)
    target = auto_paint._Cell(raw, target=True)
    source = auto_paint._Cell(np.roll(raw, (3, -2), (0, 1)), target=True)
    cache = auto_alignment.AlignmentCache(gpu='off')
    first, second = (45, 45, 70, 70), (46, 45, 71, 70)
    try:
        cache.solve_many([(target, source, first, 24)])
        cache.solve_many([(target, source, second, 24)])
        assert cache.motion_certified + cache.shared_support_hits == 1
        assert cache.cpu_solves == 1
        for bounds in (first, second):
            actual = cache.shift(target, source, bounds, 24)
            expected = auto_paint._local_shift(target, source, bounds, margin=24)
            assert actual[0] == expected[0]
            assert actual[1] == pytest.approx(expected[1], abs=2e-6)
    finally:
        cache.close()


def test_pipeline_policy_uses_complete_timings_and_distinguishes_batch_sizes():
    from paintmaskanimator.auto_routing import PipelinePolicy
    policy = PipelinePolicy()
    assert policy.choose(('fft', 1)) is None
    assert not policy.record(('fft', 1), 0.001, 0.005)
    assert policy.record(('fft', 32), 0.032, 0.005)
    assert not policy.record(('sample', 32), 0.032, 0.001, valid=False)
    assert policy.choose(('fft', 1)) is False
    assert policy.choose(('fft', 32)) is True


def test_pipeline_calibration_is_bounded_and_unknown_work_stays_on_cpu():
    from paintmaskanimator.auto_routing import PipelinePolicy
    policy = PipelinePolicy(budget=1)
    key = ('alignment', (256, 256, 24), 4)
    policy.record(key, 1., .2)
    assert policy.choose(('alignment', (256, 256, 24), 5)) is True
    assert policy.choose(('alignment', (512, 512, 24), 5)) is False
    assert len(policy.records) == 1
    policy.record(('sample', 3, 10, False, True), 1., 2.)
    assert policy.choose(('sample', 3, 11, False, True)) is False
    assert policy.choose(('sample', 3, 10, True, True)) is False


def test_unprofitable_shared_search_is_disabled_without_changing_answers():
    cache = auto_alignment.AlignmentCache(gpu='off')
    raw = np.zeros((64, 80, 4), dtype=np.uint8)
    raw[20:40, 30] = (0, 0, 0, 255)
    target = auto_paint._Cell(raw, target=True)
    request = target, target, (20, 15, 40, 45), 8
    cache.support_checks, cache.motion_trials = 300, 150
    cache.solve_many([request])
    assert not cache._support_enabled and not cache._certify_enabled
    assert cache.shift(*request)[0] == (0, 0)
    assert not cache._supports and not cache._shared
