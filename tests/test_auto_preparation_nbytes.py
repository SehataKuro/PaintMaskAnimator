"""キャッシュの差分集計を、変更前の全件集計と照合する。"""
from collections import OrderedDict
from copy import copy
from pickle import dumps, loads
from random import Random
from types import SimpleNamespace

import numpy as np
import pytest

from paintmaskanimator import bucket_fill
from paintmaskanimator.auto_preparation import ReferenceRegions


def mask_bytes(result):
    """局所マスクと島だけを数える。"""
    return (0 if result.local is None else result.local.nbytes) + sum(
        local.nbytes for _, _, local in result.islands
    )


def fill_bytes(result):
    """遅延生成された runs も含めて数える。"""
    return mask_bytes(result) + len(getattr(result, "_native_runs", b""))


def session_bytes(session):
    """変更前のセッションと同じ範囲を数える。"""
    total = session.pixels.nbytes
    total += sum(array.nbytes for index in session._indices.values()
                 if not isinstance(index, bucket_fill._NativeRegionIndex)
                 for array in (index.labels, index.x0, index.y0, index.x1, index.y1))
    total += sum(array.nbytes for pair in session._lines.values() for array in pair)
    total += sum(fill_bytes(result) for result in session._results.values())
    if session._native_session is not None:
        total += session._native_session.nbytes()
    return total


class RecountingRegions(ReferenceRegions):
    """変更前と同じ挿入・全件集計・退去で動く比較用キャッシュ。"""

    @property
    def region_nbytes(self):
        return sum(fill_bytes(result) + seedable.nbytes
                   for records in self.regions.values() for result, seedable in records)

    @property
    def nbytes(self):
        return sum(session_bytes(session) for session in self.sessions.values()) + self.region_nbytes

    def _append_region(self, key, result, seedable):
        self.regions.setdefault(key, []).append((result, seedable))
        self.regions.move_to_end(key)

    def trim(self):
        while self.regions and self.region_nbytes > self.max_region_bytes:
            self.regions.popitem(last=False)
        while self.sessions and (len(self.sessions) > self.capacity or self.nbytes > self.max_bytes):
            self.sessions.popitem(last=False)
        while self.regions and self.nbytes > self.max_bytes:
            self.regions.popitem(last=False)


class Evictions(OrderedDict):
    """テストでセッションと領域の退去を時系列に記録する。"""

    def __init__(self, kind, events):
        super().__init__()
        self.kind = kind
        self.events = events

    def popitem(self, last=True):
        key, value = super().popitem(last=last)
        self.events.append((self.kind, key))
        return key, value


@pytest.mark.parametrize("python_index", [False, True])
def test_random_cache_accounting_matches_full_recount(python_index):
    rng = Random(611)
    options = bucket_fill.BucketOptions(extend_lines=True)
    # GPU の有無に依存せず、Python 所有の索引を使う分岐も通す。
    gpu = SimpleNamespace(index=lambda _: None) if python_index else None
    caches = [ReferenceRegions(gpu_regions=gpu), RecountingRegions(gpu_regions=gpu)]
    events = [[], []]
    for cache, log in zip(caches, events):
        cache.sessions = Evictions("session", log)
        cache.regions = Evictions("region", log)

    geometries = []
    for offset in range(5):
        pixels = np.zeros((12, 24, 4), dtype=np.uint8)
        for band in range(6):
            pixels[:, band * 4:band * 4 + 3] = (70 + offset, 100, 30 + band, 255)
        geometries.append(SimpleNamespace(pixels=pixels, _native_immutable=True))

    results = []
    sessions = []
    # 同一セッションを残したまま5色目を準備し、索引と結果の退去を起こす。
    for cache in caches:
        cache.max_bytes = 100_000
    sessions.append(tuple(cache.session(geometries[0], options) for cache in caches))
    evicted_results = []
    for x in (1, 5, 9, 13, 17):
        pair = tuple(cache.compact(geometries[0], x, 4, options) for cache in caches)
        evicted_results.append(pair)
        actual, expected = caches
        assert actual.nbytes == expected.nbytes
    assert len(sessions[0][0]._indices) == 4
    assert all(result is not evicted_results[0][0] for result in sessions[0][0]._results.values())
    # セッションから退去済みでも、領域キャッシュには残っている。
    results.extend(evicted_results)

    for step in range(260):
        geometry = rng.choice(geometries)
        x = 4 * rng.randrange(6) + 1
        action = rng.randrange(7)
        if action <= 2:
            results.append(tuple(cache.compact(geometry, x, 4, options) for cache in caches))
        elif action == 3:
            pair = tuple(cache.session(geometry, options) for cache in caches)
            sessions.append(pair)
            # Rust の前処理と索引は、共有キャッシュに入った後にも増える。
            results.append(tuple(session.compact(x, 4) for session in pair))
        elif action == 4:
            runs = bytes(rng.randrange(1, 220))
            for result in rng.choice(results):
                result._native_runs = runs
        elif action == 5:
            # 同一結果を複数回計上してから、元のキーとは別に退去させる。
            pair = rng.choice(results)
            key = ("duplicate", step % 3)
            for cache, result in zip(caches, pair):
                cache._append_region(key, result, np.ones((2, 3), dtype=bool))
                cache._append_region(key, result, np.ones((1, 2), dtype=bool))
        else:
            capacity, max_bytes = rng.randrange(5), rng.randrange(300, 32_000)
            max_region_bytes = rng.randrange(80, 2_500)
            for cache in caches:
                cache.capacity = capacity
                cache.max_bytes = max_bytes
                cache.max_region_bytes = max_region_bytes
                cache.trim()

        actual, expected = caches
        assert actual.region_nbytes == expected.region_nbytes
        assert actual.nbytes == expected.nbytes
        assert events[0] == events[1]
        assert list(actual.sessions) == list(expected.sessions)
        assert list(actual.regions) == list(expected.regions)
        for a, b in sessions:
            assert a.nbytes == session_bytes(a) == session_bytes(b)
        for a, b in results:
            assert a.nbytes == mask_bytes(a) == mask_bytes(b)
    assert {kind for kind, _ in events[0]} == {"session", "region"}


def test_session_result_limit_counts_masks_only(monkeypatch):
    pixels = np.zeros((2, 8, 4), dtype=np.uint8)
    session = bucket_fill.BucketSession(pixels, bucket_fill.BucketOptions())
    limit = 32 * 1024 * 1024
    sizes = [limit // 2, limit // 2, 1, limit - 1, limit + 1, 2]

    def compact(x, *_):
        # 値のサイズは実配列と同じ方法で数え、元の領域には1バイトだけ確保する。
        local = np.broadcast_to(False, (sizes[x],))
        island = np.ones(1, dtype=bool) if x == 2 else np.empty(0, dtype=bool)
        return bucket_fill.CompactFill("ok", None, None, local, ((None, None, island),), None)

    monkeypatch.setattr(session, "_compact_indexed", compact)
    expected = {}
    previous = []
    for x in range(len(sizes)):
        result = session.compact(x, 0)
        size = mask_bytes(result)
        if sum(mask_bytes(old) for old in expected.values()) + size > limit:
            expected.clear()
        expected[(None, x, 0)] = result
        result._native_runs = bytes(128)
        for retired in previous:
            retired._native_runs = bytes(x)
        previous.append(result)
        assert list(session._results) == list(expected)
        assert session.nbytes == session_bytes(session)

    result.local = np.zeros(3, dtype=bool)
    result.islands = ((None, None, np.zeros(4, dtype=bool)),)
    assert result.nbytes == mask_bytes(result) == 7
    assert session.nbytes == session_bytes(session)

    # キャッシュ内の結果をコピー・復元しても、元の計上先には登録されない。
    for detached in (copy(result), loads(dumps(result))):
        assert detached.nbytes == result.nbytes
        detached._native_runs = bytes(256)
        assert session.nbytes == session_bytes(session)
