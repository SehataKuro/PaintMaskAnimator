"""自動塗りの連結成分と、ラン圧縮した見本の採色をGPUで処理する。"""
from collections import OrderedDict, defaultdict
from contextlib import contextmanager
from threading import RLock
from time import perf_counter
from typing import Any

import numpy as np

from .logging_setup import get_logger

log = get_logger(__name__)


_SOURCE = r'''
extern "C" __global__ void count_runs(
    const long long* runs, const int* jobs, const int* params, int nruns,
    const long long* rows, const int* starts, const int* ends,
    const int* keys, const int* labels, const unsigned char* flags,
    int width, int height, int nk, int* counts, long long* out) {
    int r = blockDim.x * blockIdx.x + threadIdx.x;
    if (r >= nruns) return;
    int j = jobs[r]; const int* p = params + j*4;
    long long y = runs[r*3] + p[0];
    long long x = runs[r*3+1] + p[1], end = runs[r*3+2] + p[1];
    if (y < 0 || y >= height) return;
    x = x < 0 ? 0 : x; end = end > width ? width : end;
    if (x >= end) return;
    atomicAdd((unsigned long long*)(out+j*9), (unsigned long long)(end-x));
    long long lo = rows[y], hi = rows[y+1];
    while (lo < hi) { long long m=(lo+hi)/2; if(ends[m]<=x) lo=m+1; else hi=m; }
    for(long long s=lo; s<rows[y+1] && starts[s]<end; ++s) {
        if ((flags[s]&p[2]) || (flags[s]&p[3])!=p[3]) continue;
        int n = (ends[s]<end ? ends[s]:end) - (starts[s]>x ? starts[s]:x);
        atomicAdd(counts+j*nk+keys[s], n);
        atomicAdd((unsigned long long*)(out+j*9+1), (unsigned long long)n);
        if(flags[s]&4) atomicExch((unsigned long long*)(out+j*9+6), 1ULL);
    }
}
extern "C" __global__ void winners(const int* counts, const long long* palette,
    int nk, int nj, int* best, long long* out) {
    int j=blockDim.x*blockIdx.x+threadIdx.x; if(j>=nj) return;
    int key=-1, n=0;
    for(int k=0; k<nk; ++k) if(counts[j*nk+k]>n) { n=counts[j*nk+k]; key=k; }
    best[j]=key; out[j*9+2]=key<0 ? -1 : palette[key]; out[j*9+3]=n;
}
extern "C" __global__ void matched_runs(
    const long long* runs, const int* jobs, const int* params, int nruns,
    const long long* rows, const int* starts, const int* ends,
    const int* keys, const int* labels, const unsigned char* flags,
    int width, int height, int nl, const int* best, int* touched,
    long long* lengths, long long* out) {
    int r=blockDim.x*blockIdx.x+threadIdx.x; if(r>=nruns) return;
    int j=jobs[r]; const int* p=params+j*4;
    long long y=runs[r*3]+p[0], x=runs[r*3+1]+p[1], end=runs[r*3+2]+p[1];
    if(y<0 || y>=height || best[j]<0) return;
    x=x<0 ? 0:x; end=end>width ? width:end; if(x>=end) return;
    long long lo=rows[y], hi=rows[y+1];
    while(lo<hi) { long long m=(lo+hi)/2; if(ends[m]<=x) lo=m+1; else hi=m; }
    long long n=0;
    for(long long s=lo; s<rows[y+1] && starts[s]<end; ++s) {
        if(keys[s]!=best[j] || (flags[s]&p[2]) || (flags[s]&p[3])!=p[3]) continue;
        n+=(ends[s]<end ? ends[s]:end)-(starts[s]>x ? starts[s]:x);
        atomicExch(touched+j*nl+labels[s], 1);
        if(flags[s]&4) atomicExch((unsigned long long*)(out+j*9+5), 1ULL);
    }
    lengths[r]=n;
}
extern "C" __global__ void finish_samples(
    const long long* runs, const int* offsets, const int* params, const long long* prefix,
    const long long* rows, const int* starts, const int* ends, const int* keys,
    const unsigned char* flags, const long long* sizes, const int* touched,
    int nl, int nj, const int* best, long long* out) {
    int j=blockDim.x*blockIdx.x+threadIdx.x; if(j>=nj || best[j]<0) return;
    long long area=0; for(int k=0; k<nl; ++k) if(touched[j*nl+k]) area+=sizes[k];
    out[j*9+4]=area;
    int begin=offsets[j], end=offsets[j+1];
    long long base=begin ? prefix[begin-1]:0, at=base+out[j*9+3]/2;
    int lo=begin, hi=end;
    while(lo<hi) { int m=(lo+hi)/2; if(prefix[m]<=at) lo=m+1; else hi=m; }
    if(lo>=end) return;
    int r=lo; const int* p=params+j*4;
    long long skip=at-(r ? prefix[r-1]:0);
    long long y=runs[r*3]+p[0], x=runs[r*3+1]+p[1], stop=runs[r*3+2]+p[1];
    x=x<0 ? 0:x;
    lo=rows[y]; hi=rows[y+1];
    while(lo<hi) { int m=(lo+hi)/2; if(ends[m]<=x) lo=m+1; else hi=m; }
    for(long long s=lo; s<rows[y+1] && starts[s]<stop; ++s) {
        if(keys[s]!=best[j] || (flags[s]&p[2]) || (flags[s]&p[3])!=p[3]) continue;
        long long left=starts[s]>x ? starts[s]:x;
        long long n=(ends[s]<stop ? ends[s]:stop)-left;
        if(skip<n) { out[j*9+7]=y; out[j*9+8]=left+skip; return; }
        skip-=n;
    }
}
'''


def mask_runs(filled):
    runs = getattr(filled, "_native_runs", None)
    if runs is None:
        padded = np.pad(filled.local, ((0, 0), (1, 1)))
        ys, xs = np.nonzero(padded[:, 1:] != padded[:, :-1])
        runs = np.ascontiguousarray(np.column_stack((
            ys[::2] + filled.bounds[1], xs[::2] + filled.bounds[0], xs[1::2] + filled.bounds[0],
        )), dtype=np.int64).tobytes()
        filled._native_runs = runs
    return np.frombuffer(runs, dtype=np.int64).reshape(-1, 3)


class RegionGPU:
    """計画内だけのGPUキャッシュ。採色結果のみCPUへ戻し、不変の見本を再利用する。"""

    def __init__(self, cp, *, max_bytes=128 * 1024 * 1024, capacity=16):
        self.cp = cp
        self.device = cp.cuda.Device().id
        self.pool = cp.cuda.MemoryPool()
        self.lock = RLock()
        self.guides = OrderedDict()
        self.results = OrderedDict()
        self.max_bytes = max_bytes
        self.capacity = capacity
        self.failed = False
        self.error = None
        self.sample_count = self.sample_batches = self.index_count = 0
        self.sample_seconds = self.index_seconds = 0.0
        self.guide_uploads = 0
        self.result_capacity = 4096
        self._pinned = 0
        self.kernels = {name: cp.RawKernel(_SOURCE, name) for name in (
            "count_runs", "winners", "matched_runs", "finish_samples",
        )}

    def close(self):
        with self.lock:
            self.guides.clear()
            self.results.clear()
            try:
                with self.cp.cuda.Device(self.device):
                    self.pool.free_all_blocks()
            except Exception as error:
                self._failure(error)

    def _failure(self, error):
        self.error = str(error)
        self.failed = True
        log.warning("GPU region processing failed; continuing on CPU: %s", self.error)

    def index(self, passable):
        """4近傍の連結成分と外接矩形。背景番号0は種として使わない。"""
        if self.failed or passable.size == 0:
            return None
        try:
            with self.lock, self.cp.cuda.Device(self.device), self.cp.cuda.using_allocator(self.pool.malloc):
                from cupyx.scipy.ndimage import label
                started = perf_counter()
                cp = self.cp
                labeled: Any = label(cp.asarray(passable))
                labels, count = labeled
                height, width = passable.shape
                change = cp.ones(labels.shape, dtype=bool)
                change[:, 1:] = labels[:, 1:] != labels[:, :-1]
                ys, xs = cp.nonzero(change)
                ends = cp.r_[xs[1:], width]
                ends[cp.r_[ys[1:] != ys[:-1], True]] = width
                kinds = labels[ys, xs]
                x0, y0 = cp.full(count + 1, width, dtype=cp.int64), cp.full(count + 1, height, dtype=cp.int64)
                x1, y1 = cp.zeros(count + 1, dtype=cp.int64), cp.zeros(count + 1, dtype=cp.int64)
                cp.minimum.at(x0, kinds, xs)
                cp.minimum.at(y0, kinds, ys)
                cp.maximum.at(x1, kinds, ends)
                cp.maximum.at(y1, kinds, ys + 1)
                result = tuple(cp.asnumpy(array) for array in (labels, x0, y0, x1, y1))
                self.index_count += 1
                self.index_seconds += perf_counter() - started
                return result
        except Exception as error:
            self._failure(error)
            return None

    def _guide(self, guide):
        if guide in self.guides:
            result = self.guides.pop(guide)
            self.guides[guide] = result
            return result
        cp = self.cp
        initial = guide.initial if guide.initial is not None else guide
        flags = (guide.boundary.astype(np.uint8) | initial.boundary.astype(np.uint8) * 2
                 | (guide.open | guide.undecided).astype(np.uint8) * 4)
        confirmed = getattr(guide, "confirmed", None)
        flags |= 8 if confirmed is None else confirmed.astype(np.uint8) * 8
        change: np.ndarray = np.ones(flags.shape, dtype=bool)
        change[:, 1:] = ((guide.values[:, 1:] != guide.values[:, :-1])
                         | (guide.regions.labels[:, 1:] != guide.regions.labels[:, :-1])
                         | (flags[:, 1:] != flags[:, :-1]))
        ys, xs = np.nonzero(change)
        ends = np.r_[xs[1:], flags.shape[1]]
        ends[np.r_[ys[1:] != ys[:-1], True]] = flags.shape[1]
        values = guide.values[ys, xs]
        palette, keys = np.unique(values, return_inverse=True)
        arrays = (np.searchsorted(ys, np.arange(flags.shape[0] + 1)).astype(np.int64),
                  xs.astype(np.int32), ends.astype(np.int32), keys.astype(np.int32),
                  guide.regions.labels[ys, xs].astype(np.int32), flags[ys, xs],
                  guide.regions.sizes.astype(np.int64), palette.astype(np.int64))
        result = tuple(cp.asarray(array) for array in arrays)
        self.guide_uploads += 1
        size = sum(array.nbytes for array in result)
        while self.guides and (len(self.guides) >= self.capacity or
                               sum(sum(a.nbytes for a in item) for item in self.guides.values()) + size > self.max_bytes):
            self.guides.popitem(last=False)
        if size <= self.max_bytes:
            self.guides[guide] = result
        return result

    @staticmethod
    def key(guide, filled, shift, ignore, require, by_label):
        return guide, id(filled), tuple(shift), ignore, require, by_label

    def cached(self, key):
        result = self.results.get(key)
        return result[1] if result is not None else None

    @contextmanager
    def phase(self):
        # 再試行中の依存結果をLRUで失うと、要求が入れ替わり続ける場合がある。
        # 1段階の採色がすべて完了するまで保持し、完了・例外時に上限へ戻す。
        self._pinned += 1
        try:
            yield
        finally:
            self._pinned -= 1
            if not self._pinned:
                while len(self.results) > self.result_capacity:
                    self.results.popitem(last=False)

    def sample_many(self, requests):
        """同じ見本の採色をまとめ、画素ではなくランの重なりをGPUで集計する。"""
        if self.failed:
            return
        try:
            with self.lock, self.cp.cuda.Device(self.device), self.cp.cuda.using_allocator(self.pool.malloc):
                started = perf_counter()
                groups = defaultdict(list)
                for key, request in requests.items():
                    groups[(request[0], request[-1])].append((key, request))
                for (guide, by_label), items in groups.items():
                    device = self._guide(guide)
                    nk = len(device[6]) if by_label else len(device[7])
                    capacity = min(64, max(1, 16 * 1024 * 1024 // (4 * (nk + len(device[6])))))
                    batches, batch, run_count = [], [], 0
                    for item in items:
                        count = len(mask_runs(item[1][1]))
                        if batch and (len(batch) >= capacity or run_count + count > 262144):
                            batches.append(batch)
                            batch, run_count = [], 0
                        batch.append(item)
                        run_count += count
                    if batch:
                        batches.append(batch)
                    for batch in batches:
                        answers = self._sample_batch(guide, device, batch, by_label)
                        for (key, request), answer in zip(batch, answers):
                            # マスクも保持し、id再利用で別の要求と衝突するのを防ぐ。
                            self.results[key] = (request[1], answer)
                            while not self._pinned and len(self.results) > max(self.result_capacity, len(requests)):
                                self.results.popitem(last=False)
                        self.sample_count += len(batch)
                        self.sample_batches += 1
                self.sample_seconds += perf_counter() - started
        except Exception as error:
            self._failure(error)

    def _sample_batch(self, guide, device, batch, by_label):
        cp = self.cp
        rows, starts, ends, color_keys, labels, flags, sizes, palette = device
        keys = labels if by_label else color_keys
        palette = cp.arange(len(sizes), dtype=cp.int64) if by_label else palette
        masks = [mask_runs(request[1]) for _key, request in batch]
        offsets = np.r_[0, np.cumsum([len(mask) for mask in masks])].astype(np.int32)
        runs = cp.asarray(np.concatenate(masks))
        jobs = cp.asarray(np.repeat(np.arange(len(batch), dtype=np.int32), np.diff(offsets)))
        params = cp.asarray(np.array([(r[2][0], r[2][1], r[3], r[4]) for _key, r in batch], dtype=np.int32))
        nj, nr, nk, nl = len(batch), len(runs), len(palette), len(sizes)
        counts = cp.zeros((nj, nk), dtype=cp.int32)
        output = cp.zeros((nj, 9), dtype=cp.int64)
        best = cp.empty(nj, dtype=cp.int32)
        touched = cp.zeros((nj, nl), dtype=cp.int32)
        lengths = cp.zeros(nr, dtype=cp.int64)
        height, width = guide.values.shape
        scalar = lambda value: np.int32(value)
        common = (runs, jobs, params, scalar(nr), rows, starts, ends, keys, labels, flags,
                  scalar(width), scalar(height))
        if nr:
            self.kernels["count_runs"](((nr + 255) // 256,), (256,),
                                       (*common, scalar(nk), counts, output))
        self.kernels["winners"](((nj + 127) // 128,), (128,),
                                (counts, palette, scalar(nk), scalar(nj), best, output))
        if nr:
            self.kernels["matched_runs"](((nr + 255) // 256,), (256,),
                                         (*common, scalar(nl), best, touched, lengths, output))
        prefix = cp.cumsum(lengths)
        self.kernels["finish_samples"](((nj + 127) // 128,), (128,), (
            runs, cp.asarray(offsets), params, prefix, rows, starts, ends, keys, flags, sizes,
            touched, scalar(nl), scalar(nj), best, output,
        ))
        answers = cp.asnumpy(output)
        return [(int(a), int(b), None if c < 0 else int(c), int(d), int(e), bool(f), bool(g), int(h), int(i))
                for a, b, c, d, e, f, g, h, i in answers]
