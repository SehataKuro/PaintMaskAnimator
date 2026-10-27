from types import SimpleNamespace
from threading import Barrier, get_ident

import numpy as np
import pytest

from paintmaskanimator import auto_alignment, auto_paint, bucket_fill


@pytest.fixture(autouse=True)
def isolate_gpu_preference(monkeypatch):
    monkeypatch.delenv("PMA_AUTO_PAINT_GPU", raising=False)


@pytest.mark.parametrize("threshold", [0.3, 0.4, 0.5, 0.6])
def test_gpu_rounding_near_each_tracking_threshold_uses_cpu(monkeypatch, threshold):
    target, guide = pair()
    problem = auto_paint._prepare_local_shift(target, guide, (80, 80, 112, 112), margin=24)
    cache = auto_alignment.AlignmentCache(gpu="on")
    bucket = (256, 256, 24)
    cache._buckets[bucket] = True
    window = np.zeros((len(problem['dys']), len(problem['dxs'])))
    window[0, 0] = threshold * problem['total']
    monkeypatch.setattr(cache, '_windows_gpu', lambda *args: window[None])
    expected = ((7, 9), 0.123)
    monkeypatch.setattr(auto_paint, '_solve_local_shift', lambda problem: expected)
    cache._batch(bucket, [('threshold', problem)])
    assert cache.results['threshold'] == expected
    assert cache.cpu_solves == 1 and cache.gpu_solves == 0


def test_gpu_region_initialization_error_keeps_cpu_available(monkeypatch):
    from paintmaskanimator import auto_gpu_regions

    cache = auto_alignment.AlignmentCache(gpu="on")
    monkeypatch.setattr(cache, '_load_gpu', lambda: True)

    def fail(*args, **kwargs):
        raise RuntimeError('device unavailable during initialization')

    monkeypatch.setattr(auto_gpu_regions, 'RegionGPU', fail)
    cache.enable_regions(256 * 256)
    assert cache.regions is None
    assert 'device unavailable' in cache.region_error
    target, guide = pair()
    assert cache.shift(target, guide, (80, 80, 112, 112), 24)[0] == (3, -2)
    cache.close()


def test_gpu_cleanup_error_does_not_discard_cpu_results():
    cache = auto_alignment.AlignmentCache(gpu='off')

    def fail():
        raise RuntimeError('lost device while releasing memory')

    cache._resident = SimpleNamespace(close=fail)
    cache.results['known'] = ((1, -2), .9)
    cache.close()
    assert cache.results['known'] == ((1, -2), .9)
    assert 'lost device' in cache.gpu_error


def cell(mask):
    return SimpleNamespace(values=np.zeros(mask.shape, dtype=np.uint32), boundary=mask,
                           wide_boundary=bucket_fill.dilate(mask, 1), initial=None)


def pair():
    rng = np.random.default_rng(21)
    target = rng.random((240, 300)) < 0.16
    return cell(target), cell(np.roll(target, (3, -2), (0, 1)))


def test_parallel_requests_match_serial_and_deduplicate():
    target, guide = pair()
    bounds = [(70 + x * 4, 60, 108 + x * 4, 104) for x in range(8)]
    requests = [(target, guide, bound, 24) for bound in bounds]
    expected = [auto_paint._local_shift(*request[:3], margin=request[3])
                for request in requests]
    cache = auto_alignment.AlignmentCache(gpu="off")
    try:
        cache.solve_many(requests + requests)
        assert 0 < cache.cpu_batches <= len(requests)
        assert cache.cpu_solves == len(requests)
        assert [cache.shift(*request) for request in requests] == expected
        cache.solve_many(requests)
        assert cache.cpu_solves == len(requests)
    finally:
        cache.close()
    assert cache._executor is None


def test_alignment_workers_really_overlap_and_keep_request_order(monkeypatch):
    target, guide = pair()
    barrier = Barrier(4, timeout=5)
    threads = set()

    def solve(problem):
        threads.add(get_ident())
        barrier.wait()
        bounds_x = problem['device_spec'][6]
        return (bounds_x, 24), 0.9

    monkeypatch.setattr(auto_paint, "_solve_local_shift", solve)
    requests = [(target, guide, (x, 0, x + 20, 20), 24) for x in range(4)]
    cache = auto_alignment.AlignmentCache(gpu="off")
    try:
        cache.solve_many(requests)
        assert len(threads) == 4
        assert [cache.shift(*request) for request in requests] == [((44 + x, 24), 0.9) for x in range(4)]
    finally:
        cache.close()


def test_tracking_batches_each_step_and_drops_failed_tiles(monkeypatch):
    cache = auto_alignment.AlignmentCache(gpu="off")
    a, b, c = object(), object(), object()
    batches = []

    def solve_many(requests):
        batches.append(list(requests))

    def shift(left, right, bounds, margin):
        assert margin == 48
        if left is a and bounds[0] == 20:
            return (0, 0), 0.49
        return ((1, 2) if (left, right) in [(a, b), (b, c)] else (-1, -2)), 0.9

    monkeypatch.setattr(cache, "solve_many", solve_many)
    monkeypatch.setattr(cache, "shift", shift)
    paths = cache.track_many([(a, b), (b, c)], [(0, 0, 10, 10), (20, 0, 30, 10)], (100, 100))
    assert paths == [(2, 4, -2, -4), None]
    assert [len(batch) for batch in batches] == [2, 1, 1, 1]
    assert batches[1][0][2] == (2, 1, 12, 11)
    assert batches[2][0][:3] == (c, b, (4, 2, 14, 12))
    assert batches[3][0][:3] == (b, a, (2, 1, 12, 11))


def test_parallel_worker_errors_propagate_and_can_be_closed(monkeypatch):
    target, guide = pair()
    cache = auto_alignment.AlignmentCache(gpu="off")

    def fail(*args, **kwargs):
        raise RuntimeError("alignment failure")

    monkeypatch.setattr(auto_paint, "_solve_local_shift", fail)
    try:
        with pytest.raises(RuntimeError, match="alignment failure"):
            cache.solve_many([(target, guide, (x, 0, x + 20, 20), 24) for x in range(4)])
    finally:
        cache.close()
    assert cache._executor is None


def test_resolve_batches_only_requested_fallbacks_and_preserves_order(monkeypatch):
    target, guide = pair()
    cache = auto_alignment.AlignmentCache(gpu="off")
    batches = []
    original = cache.solve_many

    def record(requests):
        batches.append(list(requests))
        original(requests)

    monkeypatch.setattr(cache, "solve_many", record)

    def choose(index):
        first = cache.shift(target, guide, (60 + index, 60, 100 + index, 100), 24)
        if index == 1:
            return cache.shift(target, guide, (100, 100, 140, 140), 24)
        return first

    try:
        answers = cache.resolve([lambda index=index: choose(index) for index in range(4)])
        assert [len(batch) for batch in batches if batch] == [4, 1]
        assert cache.cpu_solves == 5
        assert answers == [choose(index) for index in range(4)]
        assert cache._requests is None
        with pytest.raises(ValueError, match="colour failure"):
            cache.resolve([lambda: (_ for _ in ()).throw(ValueError("colour failure"))])
        assert cache._requests is None
    finally:
        cache.close()


def test_alignment_reuse_ignores_paint_colours_but_invalidates_changed_lines(monkeypatch):
    target, guide = pair()
    cache = auto_alignment.AlignmentCache(gpu="off")
    bounds = (90, 80, 130, 120)
    expected = cache.shift(target, guide, bounds, 24)
    original = auto_paint._local_shift
    calls = []

    def record(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(auto_paint, "_local_shift", record)
    clone = cell(guide.boundary.copy())
    clone.values[:] = 0xFFF1E8FF
    assert cache.shift(target, clone, bounds, 24) == expected
    assert calls == []
    changed = guide.boundary.copy()
    changed[85, 100] = ~changed[85, 100]
    cache.shift(target, cell(changed), bounds, 24)
    assert len(calls) == 1


def test_small_jobs_do_not_pay_for_gpu_initialization(monkeypatch):
    target, guide = pair()
    cache = auto_alignment.AlignmentCache(gpu="auto", expected_cells=1)

    def unexpected():
        pytest.fail("Do not initialize CUDA for a one-cell job")

    monkeypatch.setattr(cache, "_load_gpu", unexpected)
    cache.prefetch(target, [guide], [(80, 80, 112, 112)], 24)
    assert cache.shift(target, guide, (80, 80, 112, 112), 24)[0] == (3, -2)


def test_small_images_stay_on_cpu_even_with_many_cells(monkeypatch):
    target = cell(np.zeros((64, 64), dtype=bool))
    cache = auto_alignment.AlignmentCache(gpu="auto", expected_cells=100)

    def unexpected():
        pytest.fail("Do not initialize CUDA for tiny images")

    monkeypatch.setattr(cache, "_load_gpu", unexpected)
    cache.prefetch(target, [target], [(8 + x, 8, 32 + x, 32) for x in range(8)], 24)


def test_gpu_failure_continues_with_the_cpu(monkeypatch):
    target, guide = pair()
    cache = auto_alignment.AlignmentCache(gpu="on")
    cache._cupy = object()

    def unavailable(*args):
        raise RuntimeError("device unavailable")

    monkeypatch.setattr(cache, "_windows_gpu", unavailable)
    bounds = [(80 + x, 80, 112 + x, 112) for x in range(8)]
    cache.prefetch(target, [guide], bounds, 24)
    assert cache.gpu_error == "device unavailable"
    assert cache.shift(target, guide, bounds[-1], 24) == auto_paint._local_shift(
        target, guide, bounds[-1], margin=24,
    )


def test_autotuning_leaves_slower_batches_on_cpu(monkeypatch):
    target, guide = pair()
    problem = auto_paint._prepare_local_shift(target, guide, (80, 80, 112, 112), margin=24)
    cache = auto_alignment.AlignmentCache(gpu="on")
    times = iter((0.0, 1.0, 2.0, 4.0, 5.0, 7.0))
    monkeypatch.setattr(auto_alignment, "perf_counter", lambda: next(times))
    calls = []

    def windows(bucket, batch):
        calls.append(True)
        return np.stack([auto_paint._local_shift_window(p) for _key, p in batch])

    monkeypatch.setattr(cache, "_windows_gpu", windows)
    bucket = (256, 256, 24)
    cache._batch(bucket, [(0, problem)])
    assert cache.policy.choose(('alignment', bucket, 1)) is False
    assert cache.results[0] == auto_paint._solve_local_shift(problem)
    cache._batch(bucket, [(1, problem)])
    assert len(calls) == 2


def test_numerical_ties_fall_back_to_cpu(monkeypatch):
    target, guide = pair()
    problem = auto_paint._prepare_local_shift(target, guide, (80, 80, 112, 112), margin=24)
    cache = auto_alignment.AlignmentCache(gpu="on")
    bucket = (256, 256, 24)
    cache._buckets[bucket] = True
    penalty = auto_paint.MOTION_PENALTY * problem["step"] * (
        np.abs(problem["dys"])[:, None] + np.abs(problem["dxs"])[None, :]
    )
    monkeypatch.setattr(cache, "_windows_gpu",
                        lambda *args: np.array([(1 + penalty) * problem["total"]]))
    cache._batch(bucket, [(0, problem)])
    assert cache.results[0] == auto_paint._solve_local_shift(problem)
    assert cache.gpu_solves == 0


def test_batched_gpu_padding_and_transfer_preserve_alignment(monkeypatch, tmp_path):
    monkeypatch.setenv("CUPY_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    pytest.importorskip("cupy")
    cache = auto_alignment.AlignmentCache(gpu="on")
    if not cache._load_gpu():
        pytest.skip("CUDA GPU not available")
    target, guide = pair()
    # GPUの生ポインタは行優先で扱う。入力の配列配置に結果を依存させない。
    for item in (target, guide):
        item.boundary = np.asfortranarray(item.boundary)
        item.wide_boundary = np.asfortranarray(item.wide_boundary)
    batch = []
    for index in range(12):
        bounds = (80 + index, 80, 112 + index + index % 4, 112 + index % 3)
        problem = auto_paint._prepare_local_shift(target, guide, bounds, margin=24, lazy=True)
        batch.append((index, problem))
    bucket = (320, 320, 24)
    windows = cache._windows_gpu(bucket, batch)
    assert all('after' not in problem for _key, problem in batch)
    windows = cache._cupy.asnumpy(windows)
    expected = [auto_paint._solve_local_shift(problem) for _key, problem in batch]
    for (_key, problem), window, answer in zip(batch, windows, expected):
        actual = auto_paint._finish_local_shift(problem, window)
        assert actual[0] == answer[0]
        assert actual[1] == pytest.approx(answer[1], abs=2e-5)
    cache._buckets[bucket] = True
    cache._batch(bucket, batch)
    assert cache.gpu_solves > 0
    for index, answer in enumerate(expected):
        assert cache.results[index][0] == answer[0]
    cache.close()
    assert cache._resident.pool.used_bytes() == 0
    assert cache._resident.pool.total_bytes() == 0

@pytest.mark.parametrize('shape,margin', [((17, 23), 3), ((31, 42), 12), ((96, 127), 24)])
def test_short_fft_padding_matches_full_linear_correlation(shape, margin):
    rng = np.random.default_rng(200)
    before = rng.random(shape).astype(np.float32)
    after = rng.random(shape).astype(np.float32)
    shifts = np.arange(-margin, margin + 1)
    problem = dict(before=before, after=after, limit=margin, dys=shifts, dxs=shifts)
    actual = auto_paint._local_shift_window(problem)
    full_size = tuple(2 * length for length in shape)
    full = np.fft.irfft2(np.fft.rfft2(after, full_size)
                        * np.conj(np.fft.rfft2(before, full_size)), full_size)
    expected = full[np.ix_(shifts % full_size[0], shifts % full_size[1])]
    np.testing.assert_allclose(actual, expected, rtol=2e-6, atol=2e-3)


@pytest.mark.parametrize('support', [(12, 61, 12, 79), (0, 30, 0, 35), (45, 73, 65, 91)])
def test_existing_empty_border_replaces_extra_fft_padding(support):
    rng = np.random.default_rng(310)
    shape, margin = (73, 91), 12
    before = np.zeros(shape, dtype=np.float32)
    y0, y1, x0, x1 = support
    before[y0:y1, x0:x1] = rng.random((y1-y0, x1-x0))
    after = rng.random(shape).astype(np.float32)
    shifts = np.arange(-margin, margin + 1)
    problem = dict(before=before, after=after, limit=margin, dys=shifts, dxs=shifts)
    actual = auto_paint._local_shift_window(problem)
    size = tuple(2 * length for length in shape)
    full = np.fft.irfft2(np.fft.rfft2(after, size) * np.conj(np.fft.rfft2(before, size)), size)
    expected = full[np.ix_(shifts % size[0], shifts % size[1])]
    np.testing.assert_allclose(actual, expected, rtol=2e-6, atol=2e-3)
    if y0 == x0 == margin:
        reduced = auto_paint._correlation_size(before, margin)
        assert reduced[0] < auto_paint._fft_size(shape[0] + margin)
        assert reduced[1] < auto_paint._fft_size(shape[1] + margin)
