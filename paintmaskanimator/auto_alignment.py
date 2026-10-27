"""線画の位置合わせを再利用し、十分まとまったFFTだけをGPUで処理する。"""
from collections import defaultdict
from hashlib import blake2b
import logging
import os
from time import perf_counter

import numpy as np


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

    def key(self, target, guide, bounds, margin):
        return boundary_key(target), boundary_key(guide), tuple(bounds), margin

    def shift(self, target, guide, bounds, margin):
        from . import auto_paint as ap
        key = self.key(target, guide, bounds, margin)
        if key not in self.results:
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
                if gap < 2e-5 or abs(result[1] - ap.MIN_LINE_OVERLAP) < 2e-5:
                    result = ap._solve_local_shift(problem)
                    self.cpu_solves += 1
                else:
                    self.gpu_solves += 1
                self.results[key] = result
        except Exception as error:
            self._gpu_failed = True
            self.gpu_error = str(error)
            log.warning("Batched GPU alignment failed; continuing on CPU: %s", error)
