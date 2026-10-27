"""実GPUの整数集計・連結領域をCPUの画素単位の判定と突き合わせる。"""
from concurrent.futures import ThreadPoolExecutor
import sys
import tempfile

import numpy as np
import pytest

from paintmaskanimator import auto_alignment, auto_paint, bucket_fill, native
from paintmaskanimator.auto_gpu_regions import RegionGPU


@pytest.fixture
def backend(tmp_path, monkeypatch, request):
    if sys.platform == "win32" and request.config.getoption("capture") != "no":
        pytest.skip("Run CUDA tests with -s: NVRTC fails with Windows output capture")
    monkeypatch.setenv("TMPDIR", str(tmp_path.parent))
    monkeypatch.setenv("CUPY_CACHE_DIR", str(tmp_path.parent / "cupy-cache"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path.parent))
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() == 0:
            pytest.skip("CUDA device unavailable")
    except cp.cuda.runtime.CUDARuntimeError:
        pytest.skip("CUDA driver unavailable")
    result = RegionGPU(cp)
    yield result
    result.close()
    assert not result.guides
    assert result.pool.used_bytes() == 0


def specimen(seed=413):
    rng = np.random.default_rng(seed)
    palette = np.array([(0, 0, 0, 0), (0, 0, 0, 255), (255, 0, 0, 255),
                        (249, 245, 231, 255), (255, 223, 212, 255)], dtype=np.uint8)
    guide = auto_paint._Cell(palette[rng.integers(0, len(palette), (23, 31))])
    guide.initial = auto_paint._Cell(palette[rng.integers(0, len(palette), (23, 31))])
    guide.open[:] = rng.random((23, 31)) < 0.07
    guide.undecided[:] = rng.random((23, 31)) < 0.08
    guide.confirmed = rng.random((23, 31)) < 0.8
    guide._native_immutable = True
    local = rng.random((12, 15)) < 0.6
    filled = bucket_fill.CompactFill('ok', None, (4, 5, 19, 17), local, (), (4, 5, 19, 17))
    return guide, filled


def oracle(guide, filled, shift, ignore, require, by_label):
    sy, sx, inside = auto_paint._shifted(guide, filled.local, filled.bounds, shift)
    initial = guide.initial if guide.initial is not None else guide
    flags = (guide.boundary.astype(np.uint8) | initial.boundary.astype(np.uint8) * 2
             | (guide.open | guide.undecided).astype(np.uint8) * 4)
    flags |= 8 if guide.confirmed is None else guide.confirmed.astype(np.uint8) * 8
    used = inside & ((flags[sy, sx] & ignore) == 0) & ((flags[sy, sx] & require) == require)
    values = guide.regions.labels if by_label else guide.values
    value, _share = auto_paint._majority(values[sy[used], sx[used]])
    unknown_any = bool(((flags[sy[used], sx[used]] & 4) != 0).any())
    if value is None:
        return int(inside.sum()), 0, None, 0, 0, False, unknown_any, 0, 0
    matched = used & (values[sy, sx] == value)
    labels = np.unique(guide.regions.labels[sy[matched], sx[matched]])
    positions = np.flatnonzero(matched)
    at = positions[len(positions) // 2]
    return (int(inside.sum()), int(used.sum()), value, int(matched.sum()),
            int(guide.regions.sizes[labels].sum()), bool(((flags[sy[matched], sx[matched]] & 4) != 0).any()),
            unknown_any, int(sy[at]), int(sx[at]))


@pytest.mark.parametrize("ignore,require,by_label", [(0, 0, False), (1, 0, True),
                                                   (2, 0, False), (6, 8, False)])
def test_gpu_counts_flags_ties_and_middle_seed_match_pixels(backend, ignore, require, by_label):
    guide, filled = specimen()
    requests = {}
    for shift in ((0, 0), (3, -5), (-9, 7), (50, 50), (-50, -50)):
        request = (guide, filled, shift, ignore, require, by_label)
        requests[backend.key(*request)] = request
    backend.sample_many(requests)
    assert backend.error is None
    assert backend.sample_count == len(requests)
    for key, request in requests.items():
        assert backend.cached(key) == oracle(*request)


def test_empty_mask_and_multi_request_prefixes(backend):
    guide, filled = specimen()
    empty = bucket_fill.CompactFill('ok', None, filled.bounds, np.zeros_like(filled.local), (), filled.bounds)
    masks = [filled, empty, filled, filled]
    requests = {}
    for mask, shift in zip(masks, [(0, 0), (0, 0), (-3, 7), (2, -1)]):
        request = (guide, mask, shift, 0, 0, False)
        requests[backend.key(*request)] = request
    backend.sample_many(requests)
    assert backend.error is None
    for key, request in requests.items():
        assert backend.cached(key) == oracle(*request)


def test_gpu_sampling_uses_the_resident_alignment_result(backend):
    from paintmaskanimator.auto_gpu_alignment import DeviceShift
    guide, filled = specimen()
    shift = DeviceShift(3, -5, backend.cp.asarray([3., -5.]))
    request = guide, filled, shift, 0, 0, False
    key = backend.key(*request)
    backend.sample_many({key: request})
    assert backend.cached(key) == oracle(*request)
    assert backend.device_shift_samples == 1


def test_cpu_route_does_not_launch_gpu_sample_kernels(backend, monkeypatch):
    from paintmaskanimator.auto_gpu_regions import mask_runs
    guide, filled = specimen()
    request = guide, filled, (0, 0), 0, 0, False
    expected = oracle(*request)
    backend.mode = 'auto'
    key = ('sample', 1, len(mask_runs(filled)).bit_length(), False, False)
    backend.policy.record(key, 1., 2.)
    monkeypatch.setattr(native, 'sample_region', lambda *args, **kwargs: expected)

    def fail(*args, **kwargs):
        pytest.fail('CPU route must not launch a GPU kernel')

    monkeypatch.setattr(backend, '_sample_batch', fail)
    cache_key = backend.key(*request)
    backend.sample_many({cache_key: request})
    assert backend.cached(cache_key) == expected
    assert backend.cpu_samples == 1 and backend.sample_count == 0


def test_cpu_route_does_not_launch_gpu_bucket_labeling(backend, monkeypatch):
    import cupyx.scipy.ndimage as ndi
    mask = np.random.default_rng(19).random((20, 24)) < .5
    key = ('bucket', mask.size.bit_length(),
           int(np.count_nonzero(mask[:, 1:] != mask[:, :-1])).bit_length())
    backend.mode = 'auto'
    backend.policy.record(key, 1., 2.)

    def fail(*args, **kwargs):
        pytest.fail('CPU route must not launch GPU labeling')

    monkeypatch.setattr(ndi, 'label', fail)
    result = backend.index(mask)
    np.testing.assert_array_equal(result[0], bucket_fill._RegionIndex(mask).labels)
    assert backend.cpu_indices == 1 and backend.index_count == 0


def test_resolve_keeps_dependencies_until_all_calls_finish(backend):
    guide, filled = specimen()
    backend.result_capacity = 1
    cache = auto_alignment.AlignmentCache(gpu='off')
    cache.regions = backend

    def choose():
        first = cache.sample_region(guide, filled, (0, 0))
        second = cache.sample_region(guide, filled, (1, 2))
        return first, second

    assert cache.resolve([choose]) == [(oracle(guide, filled, (0, 0), 0, 0, False),
                                       oracle(guide, filled, (1, 2), 0, 0, False))]
    assert len(backend.results) == 1 and backend._pinned == 0


@pytest.mark.parametrize("kind", ["empty", "full", "diagonal", "noise", "long"])
def test_gpu_bucket_connected_components_match_cpu(backend, kind):
    mask = np.zeros((27, 31), dtype=bool)
    if kind == "full":
        mask[:] = True
    elif kind == "diagonal":
        np.fill_diagonal(mask, True)
    elif kind == "noise":
        mask[:] = np.random.default_rng(67).random(mask.shape) < 0.55
    elif kind == "long":
        mask[::2] = True
        mask[:, 0] = True
    expected = bucket_fill._RegionIndex(mask)
    actual = bucket_fill._RegionIndex(mask, backend)
    assert backend.error is None
    assert backend.index_count == 1
    for y, x in np.argwhere(mask):
        np.testing.assert_array_equal(actual.region(x, y), expected.region(x, y))


def test_gpu_bucket_session_preserves_virtual_gaps_and_closed_region(backend):
    pixels = np.zeros((40, 50, 4), dtype=np.uint8)
    pixels[8, 10:31] = pixels[29, 10:31] = (0, 0, 0, 255)
    pixels[8:30, 10] = pixels[8:30, 30] = (0, 0, 0, 255)
    pixels[8, 20] = 0
    options = bucket_fill.BucketOptions(close_gap=True, gap_width=1, require_closed=True)
    cpu = bucket_fill.BucketSession(pixels, options, indexed=True)
    gpu = bucket_fill.BucketSession(pixels, options, indexed=True, gpu_regions=backend)
    for x, y in [(15, 15), (20, 8), (0, 0), (30, 8)]:
        a, b = cpu.compact(x, y), gpu.compact(x, y)
        assert (a.reason, a.target, a.bounds) == (b.reason, b.target, b.bounds)
        np.testing.assert_array_equal(a.materialize(pixels.shape[:2]).mask,
                                      b.materialize(pixels.shape[:2]).mask)
    assert backend.index_count > 0


def test_gpu_sampling_failure_returns_to_cpu_without_stalling_resolve(backend, monkeypatch):
    guide, filled = specimen()
    cache = auto_alignment.AlignmentCache(gpu="off")
    cache.regions = backend

    def fail(*args):
        raise RuntimeError("lost device")

    monkeypatch.setitem(backend.kernels, "count_runs", fail)
    expected = native.sample_region(guide, filled)
    answers = cache.resolve([lambda: cache.sample_region(guide, filled)])
    assert answers == [expected]
    assert backend.failed and "lost device" in backend.error
    assert cache._samples is None


def test_gpu_index_failure_preserves_cpu_bucket_regions(backend, monkeypatch):
    import cupyx.scipy.ndimage as ndi

    def fail(*args, **kwargs):
        raise RuntimeError("device memory exhausted")

    monkeypatch.setattr(ndi, 'label', fail)
    mask = np.random.default_rng(67).random((15, 20)) < 0.55
    expected = bucket_fill._RegionIndex(mask)
    actual = bucket_fill._RegionIndex(mask, backend)
    assert backend.failed and 'memory exhausted' in backend.error
    np.testing.assert_array_equal(actual.labels, expected.labels)
    for y, x in np.argwhere(mask)[::7]:
        np.testing.assert_array_equal(actual.region(x, y), expected.region(x, y))


def test_gpu_device_context_works_in_preparation_threads(backend):
    masks = [np.random.default_rng(i).random((60, 80)) < 0.4 for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(backend.index, masks))
    assert backend.error is None
    assert backend.index_count == 2
    for mask, result in zip(masks, results):
        actual = result[0]
        expected = bucket_fill._RegionIndex(mask)
        for y, x in np.argwhere(mask)[::19]:
            np.testing.assert_array_equal(actual == actual[y, x], expected.region(x, y))


def test_complete_gpu_plan_matches_cpu_and_releases_its_backend(backend, monkeypatch):
    from scripts.benchmark_auto_paint import fingerprint

    raw = np.zeros((128, 160, 4), dtype=np.uint8)
    raw[20, 20:140] = raw[109, 20:140] = (0, 0, 0, 255)
    raw[20:110, 20] = raw[20:110, 139] = (0, 0, 0, 255)
    raw[21:109, 80] = (0, 255, 0, 255)
    guide = raw.copy()
    guide[21:109, 21:80] = (249, 245, 231, 255)
    guide[21:109, 81:139] = (234, 191, 53, 255)
    guide[21:109, 80] = (201, 182, 131, 255)
    cells = [guide] + [np.roll(raw, i, axis=1) for i in range(1, 5)]
    original = [cell.copy() for cell in cells]
    lines = [raw] + [cell.copy() for cell in cells[1:]]
    options = bucket_fill.BucketOptions()
    cpu = auto_paint.plan_auto_paint(cells, 0, lines=lines, bucket_options=options, gpu='off')

    def enable(cache, pixels):
        cache.regions = backend

    monkeypatch.setattr(auto_alignment.AlignmentCache, 'enable_regions', enable)
    gpu = auto_paint.plan_auto_paint(cells, 0, lines=lines, bucket_options=options, gpu='off')
    assert fingerprint(gpu, cells, auto_paint) == fingerprint(cpu, cells, auto_paint)
    assert gpu.optimization_stats['gpu_region_samples'] > 0
    assert gpu.optimization_stats['gpu_bucket_indices'] > 0
    assert gpu.optimization_stats['gpu_region_error'] is None
    assert not backend.guides and not backend.results
    assert backend.pool.used_bytes() == 0
    for a, b in zip(original, cells):
        np.testing.assert_array_equal(a, b)
