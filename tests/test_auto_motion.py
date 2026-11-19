import numpy as np
import pytest

from paintmaskanimator import auto_alignment, auto_paint


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
        assert cache.shared_support_hits == 1
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
    cache.support_checks = 300
    cache.solve_many([request])
    assert not cache._support_enabled
    assert cache.shift(*request)[0] == (0, 0)
    assert not cache._supports and not cache._shared
