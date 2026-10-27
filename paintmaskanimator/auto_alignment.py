"""位置合わせを依存段階ごとにまとめ、結果を再利用する。"""
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from hashlib import blake2b
import logging
import os
from time import perf_counter
from typing import Any, Callable, TypeVar

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
        from .auto_motion import MotionMemory
        self.motion = MotionMemory()
        self.motion_certified = 0
        self.shared_support_hits = 0
        self._supports = {}
        self._shared = OrderedDict()
        self.support_checks = self.motion_trials = 0
        self._support_enabled = self._certify_enabled = True
        self._resident = None
        from .auto_routing import PipelinePolicy
        self.policy = PipelinePolicy()

    def register_motion(self, target, source, flow, x0=0, y0=0):
        self.motion.register((boundary_key(target), boundary_key(source)), flow, x0, y0)

    def _learn_motion(self, pending):
        if not self._support_enabled and not self._certify_enabled:
            return
        for key, (target, guide, bounds, _margin) in pending.items():
            if key in self.results:
                self.motion.learn((boundary_key(target), boundary_key(guide)), bounds, self.results[key])
                result = self.results[key]
                if self._support_enabled and getattr(result[0], 'gap', 0) > 2e-5 and min(
                        abs(result[1] - threshold) for threshold in (0.3, 0.4, 0.5, 0.6)) > 2e-5:
                    support = self._support_key(target, bounds, _margin)
                    if support is not None:
                        self._shared[(boundary_key(guide), support)] = result
                        while len(self._shared) > 16384:
                            self._shared.popitem(last=False)
        if self.support_checks >= 256 and self.shared_support_hits * 100 < self.support_checks:
            self._support_enabled = False
            self._shared.clear()
            self._supports.clear()
        if self.motion_trials >= 128 and self.motion_certified * 16 < self.motion_trials:
            self._certify_enabled = False

    def _support_key(self, target, bounds, margin):
        from . import auto_paint as ap
        key = boundary_key(target), tuple(bounds), margin
        if key in self._supports:
            return self._supports[key]
        height, width = target.values.shape
        x0, y0, x1, y1 = bounds
        tx0, ty0 = max(0, x0-ap.CONTEXT), max(0, y0-ap.CONTEXT)
        tx1, ty1 = min(width, x1+ap.CONTEXT), min(height, y1+ap.CONTEXT)
        wx0, wy0 = max(0, tx0-margin), max(0, ty0-margin)
        wx1, wy1 = min(width, tx1+margin), min(height, ty1+margin)
        step = max(1, int(np.ceil(max(wx1-wx0, wy1-wy0)/ap._MAX_WINDOW)))
        limit = margin // step
        if min((wx1-wx0+step-1)//step, (wy1-wy0+step-1)//step) <= 2*limit:
            self._supports[key] = None
            return None
        sx, sy = tx0+(wx0-tx0)%step, ty0+(wy0-ty0)%step
        ys, xs = np.nonzero(target.boundary[sy:ty1:step, sx:tx1:step])
        points = np.ascontiguousarray((ys*step+sy)*width + xs*step+sx, dtype=np.int64)
        result = (target.values.shape, step, limit, blake2b(points, digest_size=16).digest())
        self._supports[key] = result
        return result

    def enable_regions(self, pixels):
        if (self.gpu in ("off", "0") or
                (self.gpu == "auto" and (self.expected_cells < 4 or pixels < 256 * 256))):
            return
        if os.environ.get("PMA_AUTO_PAINT_GPU_REGIONS") == "0":
            return
        if self._load_gpu():
            from .auto_gpu_regions import RegionGPU
            try:
                self.regions = RegionGPU(self._cupy, mode=self.gpu)
            except Exception as error:
                self.region_error = str(error)
                log.warning("GPU region initialization failed; continuing on CPU: %s", self.region_error)

    def close(self):
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
        if self.regions is not None:
            self.regions.close()
        self._shared.clear()
        if self._resident is not None:
            # 結果をCPUのタプルへ戻してから、GPUの小さな結果バッファも解放する。
            self.results.update({key: (tuple(result[0]), result[1])
                                 for key, result in self.results.items()})
            try:
                self._resident.close()
            except Exception as error:
                self.gpu_error = str(error)
                log.warning("GPU alignment cleanup failed: %s", self.gpu_error)

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
        for key, (target, guide, bounds, margin) in list(pending.items()):
            if not self._support_enabled:
                break
            self.support_checks += 1
            support = self._support_key(target, bounds, margin)
            shared = self._shared.get((boundary_key(guide), support)) if support is not None else None
            if shared is not None:
                self.results[key] = shared
                self.shared_support_hits += 1
                del pending[key]
        if not pending:
            return

        hints = {key: self.motion.predict((boundary_key(target), boundary_key(guide)), bounds) if self._certify_enabled else None
                 for key, (target, guide, bounds, _margin) in pending.items()}
        self.motion_trials += sum(hint is not None for hint in hints.values())

        use_gpu = (self.gpu not in ("off", "0") and not self._gpu_failed and len(pending) >= 4
                   and (self.gpu != "auto" or self.expected_cells >= 4))
        if use_gpu and self._load_gpu():
            problems = {}
            groups = defaultdict(list)
            for key, (target, guide, bounds, margin) in pending.items():
                problem = ap._prepare_local_shift(target, guide, bounds, margin=margin, lazy=True)
                if problem is None:
                    self.results[key] = ((0, 0), 0.0)
                    self.cpu_solves += 1
                    continue
                problem['share_motion'] = self._support_enabled
                from .auto_motion import certify
                certified = certify(problem, hints[key])
                if certified is not None:
                    self.results[key] = certified
                    self.motion_certified += 1
                    continue
                problems[key] = problem
                rows, cols = problem["size"]
                if min(rows, cols) <= 2 * problem['limit']:
                    continue  # 小窓の循環相関はCPUの従来寸法で厳密に保持する。
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
            self._learn_motion(pending)
            return

        def solve(item):
            key, request = item
            target, guide, bounds, margin = request
            problem = ap._prepare_local_shift(target, guide, bounds, margin=margin)
            if problem is None:
                return ((0, 0), 0.0), False
            problem['share_motion'] = self._support_enabled
            if hints[key] is not None:
                from .auto_motion import certify
                certified = certify(problem, hints[key])
                if certified is not None:
                    return certified, True
            return ap._solve_local_shift(problem), False

        if len(pending) < 4:
            results = map(solve, pending.items())
        else:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="alignment")
            results = self._executor.map(solve, pending.items())
            self.cpu_batches += 1
        for key, (result, certified) in zip(pending, results):
            self.results[key] = result
            self.cpu_solves += not certified
            self.motion_certified += certified
            self.batched_solves += 1
        self._learn_motion(pending)

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
        if self._resident is None:
            from .auto_gpu_alignment import ResidentAlignment
            self._resident = ResidentAlignment(cp)
        return self._resident.windows(bucket, batch, _batch_capacity(*bucket[:2]))

    def _compact_gpu(self, bucket, batch) -> tuple[np.ndarray, Any]:
        from . import auto_paint as ap
        windows = self._windows_gpu(bucket, batch)
        if isinstance(windows, np.ndarray):
            # CPU配列を返す検証用バックエンドも同じ縮約規則を使う。
            rows = []
            for (_key, problem), window in zip(batch, windows):
                shift, overlap = ap._finish_local_shift(problem, window)
                scores = window / problem['total'] - ap.MOTION_PENALTY * problem['step'] * (
                    np.abs(problem['dys'])[:, None] + np.abs(problem['dxs'])[None, :])
                best = np.partition(scores.ravel(), -2)[-2:]
                rows.append((*shift, overlap, best[-1] - best[-2]))
            return np.asarray(rows), None
        assert self._resident is not None
        return self._resident.reduce(batch, windows)

    def _batch(self, bucket, batch):
        from . import auto_paint as ap
        if self._gpu_failed or self._buckets.get(bucket) is False:
            return
        policy_key = ('alignment', bucket, len(batch).bit_length())
        route = self.policy.choose(policy_key)
        if bucket not in self._buckets and route is False:
            return
        try:
            if bucket not in self._buckets and route is None:
                # 最初のグループはCPUで解いて検証・計測する。初期化と転送を済ませて
                # 再計測し、実際に速いサイズだけを以後GPUへ回す。
                started = perf_counter()
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="alignment")
                cpu = list(self._executor.map(ap._solve_local_shift, [p for _key, p in batch]))
                cpu_seconds = perf_counter() - started
                started = perf_counter()
                self._compact_gpu(bucket, batch)
                cold_seconds = perf_counter() - started
                started = perf_counter()
                compact, _device = self._compact_gpu(bucket, batch)
                warm_seconds = perf_counter() - started
                gpu_seconds = warm_seconds + max(0.0, cold_seconds - warm_seconds) / max(1, self.expected_cells)
                same_shifts = all(
                    (int(row[0]), int(row[1])) == expected[0]
                    for row, expected in zip(compact, cpu)
                )
                self.policy.record(policy_key, cpu_seconds, gpu_seconds, valid=same_shifts)
                for (key, _problem), result in zip(batch, cpu):
                    self.results[key] = result
                self.cpu_solves += len(batch)
                return
            compact, device = self._compact_gpu(bucket, batch)
            for index, ((key, problem), row) in enumerate(zip(batch, compact)):
                result = ((int(row[0]), int(row[1])), float(row[2]))
                gap = float(row[3])
                near_threshold = min(abs(result[1] - threshold)
                                     for threshold in (ap.MIN_LINE_OVERLAP, ap.MIN_COLOR_LINE_OVERLAP,
                                                       0.3, 0.5, 0.6)) < 2e-5
                if gap < 2e-5 or near_threshold:
                    result = ap._solve_local_shift(problem)
                    self.cpu_solves += 1
                else:
                    self.gpu_solves += 1
                    if device is not None:
                        from .auto_gpu_alignment import DeviceShift
                        result = (DeviceShift(*result[0], device[index, :2]), result[1])
                        result[0].gap = gap
                self.results[key] = result
        except Exception as error:
            self._gpu_failed = True
            self.gpu_error = str(error)
            log.warning("Batched GPU alignment failed; continuing on CPU: %s", error)
