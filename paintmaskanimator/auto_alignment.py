"""位置合わせを依存段階ごとにまとめ、結果を再利用する。"""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from hashlib import blake2b
import logging
import os
from time import perf_counter
from typing import Callable, TypeVar

import numpy as np

_Answer = TypeVar("_Answer")

class _DeferredShift(Exception):
    """採色を保留し、同じ段階の位置合わせを集める。"""


@dataclass
class _TrackState:
    bounds: tuple
    dy: int = 0
    dx: int = 0
    back_y: int = 0
    back_x: int = 0


log = logging.getLogger(__name__)


def _batch_capacity(rows, cols):
    limit = max(4, min(32, 16 * 1024 * 1024 // (rows * cols * 8)))
    return 1 << (limit.bit_length() - 1)


def boundary_key(cell):
    key = getattr(cell, "_alignment_key", None)
    if key is None:
        key = (cell.boundary.shape, blake2b(cell.boundary.tobytes(), digest_size=16).digest())
        cell._alignment_key = key
    return key


class AlignmentCache:
    """塗り色が変わっても、同じ境界・矩形のFFTを繰り返さない。"""

    def __init__(self, *, gpu="auto", expected_cells=1):
        self.results = {}
        self.hits = 0
        self.cpu_solves = 0
        self.gpu_solves = 0
        self.gpu_error = None
        self.gpu = os.environ.get("PMA_AUTO_PAINT_GPU", gpu).lower()
        self.expected_cells = expected_cells
        self._cupy = None
        self._gpu_failed = False
        self._buckets = {}
        self._executor = None
        self.cpu_batches = 0
        self.batched_solves = 0
        self._requests = None
        self.regions = None
        self.region_error = None
        self._samples = None

    def enable_regions(self, pixels):
        if (self.gpu in ("off", "0") or
                (self.gpu == "auto" and (self.expected_cells < 4 or pixels < 256 * 256))):
            return
        if os.environ.get("PMA_AUTO_PAINT_GPU_REGIONS") == "0":
            return
        if self._load_gpu():
            from .auto_gpu_regions import RegionGPU
            try:
                self.regions = RegionGPU(self._cupy)
            except Exception as error:
                self.region_error = str(error)
                log.warning("GPU region initialization failed; continuing on CPU: %s", self.region_error)

    def close(self):
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
        if self.regions is not None:
            self.regions.close()

    def sample_region(self, guide, filled, shift=(0, 0), *, ignore=0, require=0, by_label=False):
        from . import native
        backend = self.regions
        if backend is not None and not backend.failed and getattr(guide, "_native_immutable", False):
            key = backend.key(guide, filled, shift, ignore, require, by_label)
            cached = backend.cached(key)
            if cached is not None:
                return cached
            request = (guide, filled, shift, ignore, require, by_label)
            if self._samples is not None:
                self._samples[key] = request
                raise _DeferredShift
            backend.sample_many({key: request})
            cached = backend.cached(key)
            if cached is not None:
                return cached
        return native.sample_region(guide, filled, shift, ignore=ignore, require=require, by_label=by_label)

    def solve_many(self, requests):
        """必要と確定した探索だけをまとめ、キャッシュへの書き込みは主スレッドで行う。"""
        from . import auto_paint as ap
        pending = {}
        for target, guide, bounds, margin in requests:
            key = self.key(target, guide, bounds, margin)
            if key not in self.results and key not in pending:
                pending[key] = (target, guide, bounds, margin)
        if not pending:
            return

        use_gpu = (self.gpu not in ("off", "0") and not self._gpu_failed and len(pending) >= 4
                   and (self.gpu != "auto" or self.expected_cells >= 4))
        if use_gpu and self._load_gpu():
            problems = {}
            groups = defaultdict(list)
            for key, (target, guide, bounds, margin) in pending.items():
                problem = ap._prepare_local_shift(target, guide, bounds, margin=margin)
                if problem is None:
                    self.results[key] = ((0, 0), 0.0)
                    self.cpu_solves += 1
                    continue
                problems[key] = problem
                rows, cols = problem["size"]
                bucket = ((rows + 63) // 64 * 64, (cols + 63) // 64 * 64, problem["limit"])
                groups[bucket].append((key, problem))
            for bucket, items in groups.items():
                if len(items) < 4:
                    continue
                capacity = _batch_capacity(*bucket[:2])
                for start in range(0, len(items), capacity):
                    self._batch(bucket, items[start:start + capacity])
            remaining = {key: problem for key, problem in problems.items() if key not in self.results}
            if remaining:
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="alignment")
                results = self._executor.map(ap._solve_local_shift, remaining.values())
                for key, result in zip(remaining, results):
                    self.results[key] = result
                    self.cpu_solves += 1
                self.cpu_batches += 1
            self.batched_solves += len(pending)
            return

        def solve(request):
            target, guide, bounds, margin = request
            return ap._local_shift(target, guide, bounds, margin=margin)

        if len(pending) < 4:
            results = map(solve, pending.values())
        else:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="alignment")
            results = self._executor.map(solve, pending.values())
            self.cpu_batches += 1
        for key, result in zip(pending, results):
            self.results[key] = result
            self.cpu_solves += 1
            self.batched_solves += 1

    def resolve(self, calls: list[Callable[[], _Answer]]) -> list[_Answer]:
        """必要な探索を一括実行する。callsは再実行されるため確定結果の書き込みを含めない。"""
        with self.regions.phase() if self.regions is not None else nullcontext():
            answers: dict[int, _Answer] = {}
            pending = list(enumerate(calls))
            while pending:
                unresolved = []
                self._requests = {}
                self._samples = {}
                try:
                    for index, call in pending:
                        try:
                            answers[index] = call()
                        except _DeferredShift:
                            unresolved.append((index, call))
                    requests = list(self._requests.values())
                    samples = self._samples
                finally:
                    self._requests = None
                    self._samples = None
                self.solve_many(requests)
                if samples and self.regions is not None:
                    self.regions.sample_many(samples)
                pending = unresolved
            return [answers[index] for index in range(len(calls))]

    def track_many(self, steps, bounds_list, shape):
        """全区画を一段ずつ進め、復路の探索も往路が成功した区画だけでまとめる。"""
        height, width = shape
        states: list[_TrackState | None] = [_TrackState(bounds) for bounds in bounds_list]
        for backward, pairs in ((False, steps), (True, list(reversed(steps)))):
            for left, right in pairs:
                requests, indices = [], []
                for index, state in enumerate(states):
                    if state is None:
                        continue
                    bounds = state.bounds
                    clipped = (max(0, bounds[0]), max(0, bounds[1]),
                               min(width, bounds[2]), min(height, bounds[3]))
                    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
                        states[index] = None
                        continue
                    requests.append((right, left, clipped, 48) if backward else
                                    (left, right, clipped, 48))
                    indices.append(index)
                self.solve_many(requests)
                for index, request in zip(indices, requests):
                    shift, score = self.shift(*request)
                    if score < 0.5:
                        states[index] = None
                        continue
                    state = states[index]
                    assert state is not None
                    if backward:
                        state.back_y += shift[0]
                        state.back_x += shift[1]
                        bounds = state.bounds
                        state.bounds = (bounds[0] + shift[1], bounds[1] + shift[0],
                                        bounds[2] + shift[1], bounds[3] + shift[0])
                    else:
                        state.dy += shift[0]
                        state.dx += shift[1]
                        initial = bounds_list[index]
                        state.bounds = (initial[0] + state.dx, initial[1] + state.dy,
                                        initial[2] + state.dx, initial[3] + state.dy)
        return [(state.dy, state.dx, state.back_y, state.back_x) if state is not None else None
                for state in states]

    def key(self, target, guide, bounds, margin):
        return boundary_key(target), boundary_key(guide), tuple(bounds), margin

    def shift(self, target, guide, bounds, margin):
        from . import auto_paint as ap
        key = self.key(target, guide, bounds, margin)
        if key not in self.results:
            if self._requests is not None:
                self._requests[key] = (target, guide, bounds, margin)
                raise _DeferredShift
            self.results[key] = ap._local_shift(target, guide, bounds, margin=margin)
            self.cpu_solves += 1
        else:
            self.hits += 1
        return self.results[key]

    def prefetch(self, target, guides, bounds_list, margin):
        """GPU用の問題をサイズごとにまとめる。CPU経路では必要なときだけ解く。"""
        if self.gpu in ("off", "0") or self._gpu_failed:
            return
        if self.gpu == "auto" and (self.expected_cells < 4 or target.values.size < 256 * 256):
            return
        if len(bounds_list) < 4:
            return
        if not self._load_gpu():
            return
        from . import auto_paint as ap
        pending = {}
        for guide in guides:
            for geometry in ([guide.initial, guide] if guide.initial is not None else [guide]):
                for bounds in bounds_list:
                    key = self.key(target, geometry, bounds, margin)
                    if key in self.results or key in pending:
                        continue
                    problem = ap._prepare_local_shift(target, geometry, bounds, margin=margin)
                    if problem is None:
                        self.results[key] = ((0, 0), 0.0)
                    else:
                        pending[key] = problem
        groups = defaultdict(list)
        for key, problem in pending.items():
            # 同じ演算を呼ぶたびにFFTプランを作らず、64画素単位でまとめる。
            rows, cols = problem["size"]
            bucket = ((rows + 63) // 64 * 64, (cols + 63) // 64 * 64, problem["limit"])
            groups[bucket].append((key, problem))
        for bucket, items in groups.items():
            if len(items) < 4:
                continue  # 小さなグループは転送せず、通常のCPU経路へ。
            rows, cols, _limit = bucket
            batch_size = _batch_capacity(rows, cols)
            for start in range(0, len(items), batch_size):
                batch = items[start:start + batch_size]
                self._batch(bucket, batch)

    def _load_gpu(self):
        if self._cupy is not None:
            return True
        try:
            import cupy
            if cupy.cuda.runtime.getDeviceCount() == 0:
                self._gpu_failed = True
                return False
            cache = cupy.fft.config.get_plan_cache()
            cache.set_size(128)
            _free, total = cupy.cuda.runtime.memGetInfo()
            cache.set_memsize(min(512 * 1024 * 1024, total // 16))
            self._cupy = cupy
            return True
        except Exception as error:
            self.gpu_error = str(error)
            self._gpu_failed = True
            log.debug("Automatic paint uses CPU: %s", error)
            return False

    def _windows_gpu(self, bucket, batch):
        cp = self._cupy
        assert cp is not None
        rows, cols, limit = bucket
        count = len(batch)
        capacity = _batch_capacity(rows, cols)
        # 端数のバッチも同じサイズで実行し、件数が変わるたびのプラン作成を防ぐ。
        packed = np.zeros((2 * capacity, rows, cols), dtype=np.float32)
        for index, (_key, problem) in enumerate(batch):
            height, width = problem["before"].shape
            packed[index, :height, :width] = problem["after"]
            packed[index + capacity, :height, :width] = problem["before"]
        spectra = cp.fft.rfft2(cp.asarray(packed), axes=(-2, -1))
        correlation = cp.fft.irfft2(
            spectra[:capacity] * cp.conj(spectra[capacity:]), s=(rows, cols), axes=(-2, -1),
        )
        ys = cp.asarray(np.r_[0:limit + 1, rows - limit:rows])
        xs = cp.asarray(np.r_[0:limit + 1, cols - limit:cols])
        # GPUには画像をまとめて渡し、必要な候補位置の小さな表だけを戻す。
        return cp.asnumpy(correlation[:count, ys[:, None], xs])

    def _batch(self, bucket, batch):
        from . import auto_paint as ap
        if self._gpu_failed or self._buckets.get(bucket) is False:
            return
        try:
            if bucket not in self._buckets:
                # 最初のグループはCPUで解いて検証・計測する。初期化と転送を済ませて
                # 再計測し、実際に速いサイズだけを以後GPUへ回す。
                started = perf_counter()
                cpu = [ap._solve_local_shift(problem) for _key, problem in batch]
                cpu_seconds = perf_counter() - started
                self._windows_gpu(bucket, batch)
                started = perf_counter()
                windows = self._windows_gpu(bucket, batch)
                gpu_seconds = perf_counter() - started
                same_shifts = all(
                    ap._finish_local_shift(problem, window)[0] == expected[0]
                    for (_key, problem), window, expected in zip(batch, windows, cpu)
                )
                self._buckets[bucket] = same_shifts and gpu_seconds < cpu_seconds * 0.85
                for (key, _problem), result in zip(batch, cpu):
                    self.results[key] = result
                self.cpu_solves += len(batch)
                return
            windows = self._windows_gpu(bucket, batch)
            for (key, problem), window in zip(batch, windows):
                result = ap._finish_local_shift(problem, window)
                # 丸め誤差で同点の順位や閾値を変えない。際どい判断はCPUへ戻す。
                scores = window / problem["total"] - ap.MOTION_PENALTY * problem["step"] * (
                    np.abs(problem["dys"])[:, None] + np.abs(problem["dxs"])[None, :]
                )
                gap = float("inf")
                if scores.size > 1:
                    best = np.partition(scores.ravel(), -2)[-2:]
                    gap = best[-1] - best[-2]
                near_threshold = min(abs(result[1] - threshold)
                                     for threshold in (ap.MIN_LINE_OVERLAP, ap.MIN_COLOR_LINE_OVERLAP,
                                                       0.3, 0.5, 0.6)) < 2e-5
                if gap < 2e-5 or near_threshold:
                    result = ap._solve_local_shift(problem)
                    self.cpu_solves += 1
                else:
                    self.gpu_solves += 1
                self.results[key] = result
        except Exception as error:
            self._gpu_failed = True
            self.gpu_error = str(error)
            log.warning("Batched GPU alignment failed; continuing on CPU: %s", error)
