"""先行準備の寿命と、共有バケツ領域の同一性・無効化を確認する。"""
from dataclasses import replace
from threading import Event, Lock, current_thread, enumerate as threads
from types import SimpleNamespace

import numpy as np
import pytest

from paintmaskanimator import auto_bucket, auto_paint, bucket_fill
from paintmaskanimator.auto_preparation import Preparation, ReferenceRegions
from scripts.benchmark_auto_paint import fingerprint


def test_preparation_runs_independently_and_keeps_bounded_lookahead():
    started, release = Event(), Event()
    lock = Lock()
    calls = []

    def build(index):
        with lock:
            calls.append((index, current_thread().name))
            if len(calls) == 2:
                started.set()
        assert release.wait(3)
        return index * 2

    with Preparation(range(6), build, workers=2) as pool:
        try:
            pool.schedule({0})
            assert len(pool.pending) == 2
            assert started.wait(3)
            assert {index for index, _name in calls} == {1, 2}
            assert all(name.startswith('auto-paint-prepare') for _index, name in calls)
        finally:
            release.set()
        assert pool.take(1) == 2
        pool.schedule({0, 1})
        assert len(pool.pending) == 2
        assert pool.take(2) == 4
    with pytest.raises(RuntimeError):
        pool.executor.submit(lambda: None)


def test_preparation_closes_after_worker_failure():
    def build(index):
        raise ValueError('preparation failed')

    with pytest.raises(ValueError, match='preparation failed'):
        with Preparation([0, 1], build) as pool:
            pool.schedule({})
            pool.take(0)
    assert not any(thread.name.startswith('auto-paint-prepare') for thread in threads())


def test_reference_cache_reuses_equal_pixels_and_invalidates_colour_and_options():
    pixels = np.zeros((40, 60, 4), dtype=np.uint8)
    pixels[:, 30] = (0, 0, 0, 255)
    options = bucket_fill.BucketOptions()
    cache = ReferenceRegions()
    first = SimpleNamespace(pixels=pixels, _native_immutable=True)
    clone = SimpleNamespace(pixels=pixels.copy(), _native_immutable=True)
    a = cache.compact(first, 10, 10, options)
    b = cache.compact(clone, 10, 10, options)
    assert a is b
    assert cache.created == 1 and cache.region_hits == 1
    changed = pixels.copy()
    changed[:, :30] = (250, 220, 180, 255)
    cache.compact(SimpleNamespace(pixels=changed), 10, 10, options)
    assert cache.created == 2
    cache.compact(first, 10, 10, replace(options, require_closed=True))
    assert cache.created == 3
    mutable = SimpleNamespace(pixels=pixels.copy())
    cache.compact(mutable, 10, 10, options)
    mutable.pixels[:, :30] = (230, 170, 190, 255)
    cache.compact(mutable, 10, 10, options)
    assert cache.created == 4


def test_reference_cache_limit_and_gap_seeds_preserve_manual_bucket():
    pixels = np.zeros((50, 64, 4), dtype=np.uint8)
    pixels[4:38, 30] = (0, 0, 0, 255)
    source = SimpleNamespace(pixels=pixels, _native_immutable=True)
    options = bucket_fill.BucketOptions(extend_lines=True)
    cache = ReferenceRegions(max_bytes=1)
    for x, y in ((10, 15), (50, 15), (30, 45), (30, 3)):
        actual = cache.compact(source, x, y, options).materialize(pixels.shape[:2])
        expected = bucket_fill.compute_fill_region(pixels, x, y, options)
        assert (actual.reason, actual.bounds, actual.target) == (
            expected.reason, expected.bounds, expected.target,
        )
        np.testing.assert_array_equal(actual.region, expected.region)
        assert cache.nbytes <= cache.max_bytes
    assert cache.created == 4


def test_active_cell_keeps_its_sessions_when_shared_lru_evicts_them():
    class Source:
        def __init__(self, pixels):
            self.pixels = pixels
            self._native_immutable = True

    options = bucket_fill.BucketOptions()
    cache = ReferenceRegions(capacity=1)
    work = auto_bucket.BucketWork(None, options, reference_regions=cache)
    sources = []
    for index in range(3):
        pixels = np.zeros((30, 40, 4), dtype=np.uint8)
        pixels[:, :20] = (230 + index, 210, 180, 255)
        pixels[:, 20] = (0, 0, 0, 255)
        sources.append(Source(pixels))
    sessions = {}
    first = [work._reference(source, 5, 5, sessions) for source in sources]
    assert len(cache.sessions) == 1
    again = [work._reference(source, 5, 5, sessions) for source in sources]
    assert cache.created == 3
    assert all(a is b for a, b in zip(first, again))


@pytest.mark.parametrize('require_closed,close_gap', [(False, False), (True, False), (False, True)])
def test_region_cache_survives_session_eviction_without_treating_recovered_gaps_as_seeds(
    require_closed, close_gap,
):
    pixels = np.zeros((50, 64, 4), dtype=np.uint8)
    pixels[4:38, 30] = (0, 0, 0, 255)
    source = SimpleNamespace(pixels=pixels, _native_immutable=True)
    options = bucket_fill.BucketOptions(extend_lines=True, require_closed=require_closed,
                                       close_gap=close_gap, gap_width=2)
    cache = ReferenceRegions(capacity=0)
    first = cache.compact(source, 10, 15, options)
    again = cache.compact(source, 15, 20, options)
    assert first is again and cache.region_hits == 1
    assert not cache.sessions
    for x, y in ((50, 15), (30, 45), (30, 3), (48, 20)):
        actual = cache.compact(source, x, y, options).materialize(pixels.shape[:2])
        expected = bucket_fill.compute_fill_region(pixels, x, y, options)
        assert (actual.reason, actual.target, actual.bounds) == (
            expected.reason, expected.target, expected.bounds,
        )
        np.testing.assert_array_equal(actual.region, expected.region)


def drawing_sequence():
    raw = np.zeros((256, 256, 4), dtype=np.uint8)
    raw[30, 40:210] = raw[219, 40:210] = (0, 0, 0, 255)
    raw[30:220, 40] = raw[30:220, 209] = (0, 0, 0, 255)
    raw[31:219, 120] = (0, 255, 0, 255)
    guide = raw.copy()
    guide[31:219, 41:120] = (249, 245, 231, 255)
    guide[31:219, 121:209] = (234, 191, 53, 255)
    guide[31:219, 120] = (201, 182, 131, 255)
    cells = [guide] + [np.roll(raw, (i % 2, i), (0, 1)) for i in range(1, 7)]
    lines = [raw] + [cell.copy() for cell in cells[1:]]
    return cells, lines


def test_parallel_plan_matches_serial_masks_colours_clicks_and_notes():
    cells, lines = drawing_sequence()
    original = [cell.copy() for cell in cells]
    options = bucket_fill.BucketOptions(extend_lines=True)
    serial = auto_paint.plan_auto_paint(cells, 0, lines=lines, bucket_options=options,
                                       preparation_workers=0, reuse_reference_regions=False, gpu='off')
    progress = []
    parallel = auto_paint.plan_auto_paint(
        cells, 0, lines=lines, bucket_options=options, preparation_workers=2, gpu='off',
        progress=lambda done, total: progress.append((done, total)) or True,
    )
    assert fingerprint(serial, cells, auto_paint) == fingerprint(parallel, cells, auto_paint)
    assert serial.references == parallel.references and serial.rounds == parallel.rounds
    assert parallel.optimization_stats['prepared_cells'] == 6
    assert (parallel.optimization_stats['reference_cache_hits']
            + parallel.optimization_stats['reference_region_hits']) > 0
    assert progress[-1][0] == progress[-1][1]
    for before, after in zip(original, cells):
        np.testing.assert_array_equal(before, after)
    assert not any(thread.name.startswith('auto-paint-prepare') for thread in threads())


def test_cancelled_plan_waits_for_preparation_and_returns_no_partial_plan():
    cells, lines = drawing_sequence()
    result = auto_paint.plan_auto_paint(
        cells, 0, lines=lines, bucket_options=bucket_fill.BucketOptions(),
        progress=lambda done, total: done < 1, gpu='off',
    )
    assert result is None
    assert not any(thread.name.startswith('auto-paint-prepare') for thread in threads())
