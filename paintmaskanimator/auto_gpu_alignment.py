"""線画をGPUに保持し、FFTの候補表を戻さず最良位置だけを返す。"""
from collections import OrderedDict
from typing import Any

import numpy as np


class DeviceShift(tuple):
    device: Any
    gap: float
    def __new__(cls, dy, dx, device):
        result = super().__new__(cls, (int(dy), int(dx)))
        result.device = device
        return result


_PACK = r'''
extern "C" __global__ void pack(
 const unsigned char* target, const unsigned char* guide, const unsigned char* wide,
 int width, int height, int rows, int cols, int capacity, int job,
 int wx, int wy, int step, int tx0, int ty0, int tx1, int ty1,
 int h, int w, float weight, float* out) {
 int at=blockDim.x*blockIdx.x+threadIdx.x; if(at>=h*w) return;
 int y=at/w, x=at%w, sy=wy+y*step, sx=wx+x*step;
 int source=sy*width+sx, to=y*cols+x;
 out[job*rows*cols+to]=float(guide[source])*(1.0f-weight)+float(wide[source])*weight;
 out[(job+capacity)*rows*cols+to]=
   (sy>=ty0 && sy<ty1 && sx>=tx0 && sx<tx1) ? float(target[source]):0.0f;
}
'''


class ResidentAlignment:
    def __init__(self, cp):
        self.cp = cp
        self.frames = OrderedDict()
        self.pool = cp.cuda.MemoryPool()
        self.device = cp.cuda.Device().id
        self.pack_kernel = cp.RawKernel(_PACK, 'pack')
        self.uploads = 0
        self.returned_bytes = 0

    def close(self):
        self.frames.clear()
        with self.cp.cuda.Device(self.device):
            self.pool.free_all_blocks()

    def frame(self, cell):
        from .auto_alignment import boundary_key
        key = boundary_key(cell)
        if key in self.frames:
            result = self.frames.pop(key)
            self.frames[key] = result
            return result
        result = (self.cp.asarray(np.ascontiguousarray(cell.boundary)),
                  self.cp.asarray(np.ascontiguousarray(cell.wide_boundary)))
        size = sum(a.nbytes for a in result)
        while self.frames and (len(self.frames) >= 12 or
                sum(sum(a.nbytes for a in item) for item in self.frames.values()) + size > 128 * 1024 * 1024):
            self.frames.popitem(last=False)
        self.frames[key] = result
        self.uploads += 1
        return result

    def windows(self, bucket, batch, capacity):
        from . import auto_paint as ap
        cp = self.cp
        rows, cols, _limit = bucket
        with cp.cuda.Device(self.device), cp.cuda.using_allocator(self.pool.malloc):
            packed = cp.zeros((2 * capacity, rows, cols), dtype=cp.float32)
            for index, (_key, problem) in enumerate(batch):
                spec = problem.get('device_spec')
                h, w = problem['before'].shape
                if spec is None:
                    packed[index, :h, :w] = cp.asarray(problem['after'])
                    packed[index + capacity, :h, :w] = cp.asarray(problem['before'])
                    continue
                target, guide, wx, wy, tx0, ty0, tx1, ty1 = spec
                first, _unused = self.frame(target)
                second, wide = self.frame(guide)
                height, width = target.values.shape
                values = [width, height, rows, cols, capacity, index, wx, wy,
                          problem['step'], tx0, ty0, tx1, ty1, h, w]
                self.pack_kernel(((h*w+255)//256,), (256,), (
                    first, second, wide, *(np.int32(value) for value in values),
                    np.float32(ap.NEAR_LINE_WEIGHT), packed,
                ))
            spectra = cp.fft.rfft2(packed, axes=(-2, -1))
            correlation = cp.fft.irfft2(
                spectra[:capacity] * cp.conj(spectra[capacity:]), s=(rows, cols), axes=(-2, -1),
            )
            ys = cp.asarray(np.array([problem['dys'] for _key, problem in batch])) % rows
            xs = cp.asarray(np.array([problem['dxs'] for _key, problem in batch])) % cols
            return correlation[cp.arange(len(batch))[:, None, None], ys[:, :, None], xs[:, None, :]]

    def reduce(self, batch, windows):
        from . import auto_paint as ap
        cp = self.cp
        with cp.cuda.Device(self.device), cp.cuda.using_allocator(self.pool.malloc):
            # NumPyと同じfloat32の除算後、動きの罰則をfloat64で引く。
            total = cp.asarray(np.array([p['total'] for _key, p in batch], dtype=np.float32))
            step = cp.asarray(np.array([p['step'] for _key, p in batch], dtype=np.int64))
            dys = cp.asarray(np.array([p['dys'] for _key, p in batch], dtype=np.int64))
            dxs = cp.asarray(np.array([p['dxs'] for _key, p in batch], dtype=np.int64))
            scores = windows / total[:, None, None] - ap.MOTION_PENALTY * step[:, None, None] * (
                cp.abs(dys)[:, :, None] + cp.abs(dxs)[:, None, :])
            flat = scores.reshape(len(batch), -1)
            best = cp.argmax(flat, axis=1)
            at = cp.arange(len(batch))
            peak = flat[at, best].copy()
            flat[at, best] = -cp.inf
            gap = peak - cp.max(flat, axis=1)
            y, x = best // windows.shape[2], best % windows.shape[2]
            result = cp.stack((dys[at, y] * step, dxs[at, x] * step,
                               windows[at, y, x] / total, gap), axis=1)
            host = cp.asnumpy(result)
            self.returned_bytes += host.nbytes
            return host, result
