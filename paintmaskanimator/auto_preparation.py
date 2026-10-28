"""独立した対象の先行準備と、不変の見本のバケツ前処理の共有。"""
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from hashlib import blake2b
from typing import Callable, Generic, Iterable, TypeVar

import numpy as np

from . import bucket_fill


T = TypeVar("T")


class Preparation(Generic[T]):
    """最大2セルだけ先行する。塗り・採色・見本の更新は主処理に残す。"""

    def __init__(self, order: Iterable[int], build: Callable[[int], T], workers=2):
        self.order = iter(order)
        self.build = build
        self.workers = min(2, max(1, workers))
        self.executor = ThreadPoolExecutor(max_workers=self.workers,
                                           thread_name_prefix="auto-paint-prepare")
        self.pending: dict[int, Future[T]] = {}
        self.prepared = 0

    def __enter__(self) -> "Preparation[T]":
        return self

    def __exit__(self, *exception):
        for future in self.pending.values():
            future.cancel()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def schedule(self, known):
        while len(self.pending) < self.workers:
            index = next(self.order, None)
            if index is None:
                break
            if index not in known:
                self.pending[index] = self.executor.submit(self.build, index)

    def take(self, index) -> T | None:
        future = self.pending.pop(index, None)
        if future is None:
            return None
        result = future.result()
        self.prepared += 1
        return result


class ReferenceRegions:
    """同じ画素と設定のBucketSessionを共有する。1計画内・最大192MiB。"""

    def __init__(self, *, capacity=4, max_bytes=192 * 1024 * 1024, gpu_regions=None):
        self.sessions = OrderedDict()
        self.capacity = capacity
        self.max_bytes = max_bytes
        self.hits = 0
        self.created = 0
        self.regions = OrderedDict()
        self.region_hits = 0
        self.max_region_bytes = 32 * 1024 * 1024
        self.gpu_regions = gpu_regions

    @staticmethod
    def key(geometry, options):
        immutable = getattr(geometry, "_native_immutable", False)
        key = getattr(geometry, "_reference_pixels_key", None) if immutable else None
        if key is None:
            pixels = np.ascontiguousarray(geometry.pixels)
            key = (pixels.shape, pixels.dtype.str,
                   blake2b(pixels.tobytes(), digest_size=16).digest())
            if immutable:
                geometry._reference_pixels_key = key
        return key, repr(options)

    @property
    def nbytes(self):
        return sum(session.nbytes for session in self.sessions.values()) + self.region_nbytes

    @property
    def region_nbytes(self):
        return sum(result.nbytes + seedable.nbytes + len(getattr(result, "_native_runs", b""))
                   for records in self.regions.values() for result, seedable in records)

    def session(self, geometry, options):
        key = self.key(geometry, options)
        if key in self.sessions:
            self.hits += 1
            session = self.sessions.pop(key)
        else:
            self.created += 1
            session = bucket_fill.BucketSession(geometry.pixels, options, indexed=True,
                                                gpu_regions=self.gpu_regions)
        self.sessions[key] = session
        self.trim()
        return session

    def trim(self):
        while self.regions and self.region_nbytes > self.max_region_bytes:
            self.regions.popitem(last=False)
        while self.sessions and (len(self.sessions) > self.capacity or self.nbytes > self.max_bytes):
            self.sessions.popitem(last=False)
        while self.regions and self.nbytes > self.max_bytes:
            self.regions.popitem(last=False)

    def compact(self, geometry, x, y, options, *, active=None):
        key = self.key(geometry, options)
        height, width = geometry.pixels.shape[:2]
        category = (bucket_fill.seed_category(geometry.pixels, x, y)
                    if 0 <= x < width and 0 <= y < height else None)
        for result, seedable in self.regions.get(key, ()):
            x0, y0, x1, y1 = result.bounds
            if (result.target == category and x0 <= x < x1 and y0 <= y < y1
                    and seedable[y - y0, x - x0]):
                self.region_hits += 1
                self.regions.move_to_end(key)
                self.trim()
                return result
        if active is not None and geometry in active:
            session = active[geometry]
        else:
            session = self.session(geometry, options)
            if active is not None:
                active[geometry] = session
        result = session.compact(x, y)
        if (0 <= x < width and 0 <= y < height and options.adjacent
                and options.target_color == bucket_fill.TARGET_ALL and not options.include_masks
                and not options.island_replacements and result.local is not None
                and result.bounds is not None):
            _target, passable, _gaps = session.prepare(category)
            if passable is not None and passable[y, x]:
                # 回収した仮想境界は両側の面に含まれる。そこをクリック可能な
                # 種と取り違えないよう、探索可能な元の連結成分だけを記録する。
                x0, y0, x1, y1 = result.bounds
                seedable = result.local & passable[y0:y1, x0:x1]
                self.regions.setdefault(key, []).append((result, seedable))
                self.regions.move_to_end(key)
        self.trim()
        return result
