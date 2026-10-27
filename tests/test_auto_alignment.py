from types import SimpleNamespace

import numpy as np
import pytest

from paintmaskanimator import auto_alignment, auto_paint, bucket_fill


@pytest.fixture(autouse=True)
def isolate_gpu_preference(monkeypatch):
    monkeypatch.delenv("PMA_AUTO_PAINT_GPU", raising=False)


def cell(mask):
    return SimpleNamespace(values=np.zeros(mask.shape, dtype=np.uint32), boundary=mask,
                           wide_boundary=bucket_fill.dilate(mask, 1), initial=None)


def pair():
    rng = np.random.default_rng(21)
    target = rng.random((240, 300)) < 0.16
    return cell(target), cell(np.roll(target, (3, -2), (0, 1)))


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
    times = iter((0.0, 1.0, 2.0, 4.0))
    monkeypatch.setattr(auto_alignment, "perf_counter", lambda: next(times))
    calls = []

    def windows(bucket, batch):
        calls.append(True)
        return np.stack([auto_paint._local_shift_window(p) for _key, p in batch])

    monkeypatch.setattr(cache, "_windows_gpu", windows)
    bucket = (256, 256, 24)
    cache._batch(bucket, [(0, problem)])
    assert cache._buckets[bucket] is False
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
    batch = []
    for index in range(12):
        bounds = (80 + index, 80, 112 + index + index % 4, 112 + index % 3)
        problem = auto_paint._prepare_local_shift(target, guide, bounds, margin=24)
        batch.append((index, problem))
    bucket = (320, 320, 24)
    windows = cache._windows_gpu(bucket, batch)
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
