import numpy as np
import pytest

from paintmaskanimator import auto_alignment, auto_motion, auto_paint, bucket_fill, line_flow


def problem(dy, dx, seed=14, *, nearby_runner=False):
    rng = np.random.default_rng(seed)
    before = np.zeros((56, 72), dtype=np.float32)
    before[18:35, 20:50] = rng.random((17, 30)) < 0.12
    exact = np.roll(before.astype(bool), (dy, dx), (0, 1))
    if nearby_runner:
        exact |= np.roll(exact, 1 if dx > 0 else -1, axis=1)
    wide = bucket_fill.dilate(exact, 1)
    size = auto_paint._correlation_size(before, 8)
    shifts = np.r_[0:9, -8:0]
    return dict(before=before, boundaries=(exact, wide), total=float(before.sum()), step=1,
                size=size, limit=8, dys=shifts, dxs=shifts, share_motion=True)


@pytest.mark.parametrize('shift', [(0, 0), (1, -1), (3, -2), (-2, 3)])
def test_certified_motion_matches_full_fft(shift):
    for seed in range(8):
        candidate = problem(*shift, seed, nearby_runner=True)
        dy, dx = shift
        hint = (dy, dx + (1 if dx > 0 else -1))
        actual = auto_motion.certify(candidate, hint)
        expected = auto_paint._solve_local_shift(candidate)
        assert actual is not None
        assert actual[0] == expected[0]
        assert actual[0].gap == expected[0].gap
        assert actual[1] == expected[1]


def test_incorrect_hint_or_tied_motion_cannot_skip_full_search():
    candidate = problem(3, -2)
    assert auto_motion.certify(candidate, (-8, 8)) is None
    candidate['before'][:] = 0
    candidate['before'][25, 35] = 1
    exact, wide = candidate['boundaries']
    exact[:] = False
    exact[25, [33, 37]] = True
    wide[:] = bucket_fill.dilate(exact, 1)
    candidate['total'] = 1.0
    assert auto_motion.certify(candidate, (0, 2)) is None


def test_uncertain_runner_up_cannot_skip_full_search():
    candidate = problem(0, 0)
    assert auto_paint._solve_local_shift(candidate)[0] == (0, 0)
    assert auto_motion.certify(candidate, (0, 0)) is None


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


@pytest.mark.parametrize('share_motion', [False, True])
def test_native_certify_matches_numpy_on_random_windows(share_motion, monkeypatch):
    from paintmaskanimator import native

    rng = np.random.default_rng(602)
    original = native.certify_local_shift
    native_calls = 0

    def counted(*args):
        nonlocal native_calls
        native_calls += 1
        return original(*args)

    monkeypatch.setattr(native, 'certify_local_shift', counted)
    accepted = 0
    trials = 0
    for seed in range(80):
        step = 1 + seed % 4
        dy, dx = [(0, 0), (1, -1), (-1, 1), (2, 0)][seed % 4]
        if seed % 2:
            candidate = problem(dy, dx, seed, nearby_runner=True)
        else:
            # 窓と周期が同じ小窓で端の点が反対側に回り込む。
            shape = (int(rng.integers(4, 8)), int(rng.integers(4, 8)))
            before = (rng.random(shape) < 0.5).astype(np.float32)
            exact = np.roll(before.astype(bool), (dy, dx), (0, 1))
            wide = exact | (rng.random(shape) < 0.2)
            shifts = np.array([0, 1, 2, -2, -1])
            candidate = dict(before=before, boundaries=(exact, wide),
                             total=float(before.sum()), size=shape, dys=shifts, dxs=shifts)
        if candidate['total'] < 4:
            candidate['before'].flat[:4] = 1
            candidate['total'] = float(candidate['before'].sum())
        candidate.update(step=step, share_motion=share_motion)
        if seed % 3:
            candidate['before_bytes'] = candidate['before'].astype(np.uint8).tobytes()

        # 最良・次点・遠方・範囲外のヒントと、間引きでちょうど半分になるヒント。
        full = dict(candidate, share_motion=True)
        window = auto_paint._local_shift_window(full) / full['total']
        dys, dxs = full['dys'], full['dxs']
        scores = window - auto_paint.MOTION_PENALTY * step * (
            np.abs(dys)[:, None] + np.abs(dxs)[None, :])
        best, runner = np.argsort(-scores.ravel(), kind='stable')[:2]

        def pixel_shift(index):
            y, x = divmod(int(index), len(dxs))
            return int(dys[y]) * step, int(dxs[x]) * step

        full_shift, full_share = auto_paint._solve_local_shift(full)
        for hint in (pixel_shift(best), pixel_shift(runner), (int(dys[-1]) * step, 2 * step),
                     (99 * step, 0), (step / 2, -1.5 * step)):
            expected = auto_motion.certify_numpy(candidate, hint)
            actual = auto_motion.certify(candidate, hint)
            assert actual == expected
            trials += 1
            if actual is not None:
                shift, share = actual
                assert shift.gap == expected[0].gap == full_shift.gap
                assert shift == full_shift and share == full_share
                accepted += 1
    assert native_calls == trials == 400
    assert 0 < accepted < trials


def test_certify_keeps_empty_hints_and_numpy_fallback(monkeypatch):
    from paintmaskanimator import native

    candidate = problem(0, 0, nearby_runner=True)
    hint = (0, -1)
    expected = auto_motion.certify_numpy(candidate, hint)
    assert expected is not None

    def unexpected(*_args):
        pytest.fail('Rust was called for a NumPy fallback')

    monkeypatch.setattr(native, 'certify_local_shift', unexpected)
    assert auto_motion.certify(candidate, None) is None
    assert auto_motion.certify(candidate, ()) is None
    assert auto_motion.certify(dict(candidate, total=3.0), hint) is None
    before = candidate['before'].copy()
    before[0, 0] = 0.5
    assert auto_motion.certify(dict(candidate, before=before), hint) is None
    monkeypatch.setattr(native, 'available', lambda: False)
    actual = auto_motion.certify(candidate, hint)
    assert actual == expected
    assert actual[0].gap == expected[0].gap
