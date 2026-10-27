"""セル間で共有する動きの候補と、全探索を省略できる場合の厳密な上界検査。"""
from collections import OrderedDict

import numpy as np


class LocalShift(tuple):
    gap: float

    def __new__(cls, dy, dx, gap):
        result = super().__new__(cls, (int(dy), int(dx)))
        result.gap = float(gap)
        return result


class MotionMemory:
    def __init__(self, capacity=64):
        self.fields = OrderedDict()
        self.samples = OrderedDict()
        self.capacity = capacity

    def register(self, key, flow, x0, y0):
        self.fields[key] = flow, x0, y0
        self.fields.move_to_end(key)
        while len(self.fields) > self.capacity:
            self.fields.popitem(last=False)

    def learn(self, key, bounds, result):
        shift, score = result
        if score < 0.95:
            return
        x0, y0, x1, y1 = bounds
        grid = ((x0 + x1) // 256, (y0 + y1) // 256)
        cells = self.samples.setdefault(key, {})
        cells[grid] = tuple(shift)
        if len(cells) > 512:
            cells.pop(next(iter(cells)))
        self.samples.move_to_end(key)
        while len(self.samples) > self.capacity:
            self.samples.popitem(last=False)

    def predict(self, key, bounds):
        x0, y0, x1, y1 = bounds
        x, y = (x0 + x1) // 2, (y0 + y1) // 2
        if key in self.fields:
            flow, ox, oy = self.fields[key]
            if float(flow.score_at(y - oy, x - ox)) >= 0.8:
                dy, dx = flow.at(y - oy, x - ox)
                return int(np.rint(dy)), int(np.rint(dx))
        cells = self.samples.get(key)
        if cells:
            grid = (x // 128, y // 128)
            nearby = min(cells, key=lambda at: abs(at[0] - grid[0]) + abs(at[1] - grid[1]))
            if abs(nearby[0] - grid[0]) + abs(nearby[1] - grid[1]) <= 2:
                return cells[nearby]
        return None


def certify(problem, hint):
    """未検査候補の理論上限を超えた場合だけ、FFTと同じ最良位置を返す。"""
    from . import auto_paint as ap
    if hint is None:
        return None
    before, after = problem['before'], problem['after']
    step, total = problem['step'], problem['total']
    if not hint or total < 4 or not np.all((before == 0) | (before == 1)):
        return None
    ys, xs = np.nonzero(before)
    dys, dxs = problem['dys'], problem['dxs']
    dy, dx = (int(np.rint(value / step)) for value in hint)
    if dy not in dys or dx not in dxs:
        return None
    rows, cols = problem['size']

    def overlaps(shifts):
        shifts = np.asarray(shifts, dtype=np.int64)
        y = (ys[None] + shifts[:, 0, None]) % rows
        x = (xs[None] + shifts[:, 1, None]) % cols
        valid = (y < after.shape[0]) & (x < after.shape[1])
        values = after[np.minimum(y, after.shape[0] - 1), np.minimum(x, after.shape[1] - 1)]
        return np.where(valid, values, 0).sum(axis=1, dtype=np.float64) / total

    penalty = ap.MOTION_PENALTY * step
    score = float(overlaps([(dy, dx)])[0]) - penalty * (abs(dy) + abs(dx))
    # 大きな証明表が必要なら、従来のFFTを使う方が速い。
    radius = int(np.floor((float(after.max()) - score + 2e-5) / penalty))
    if radius < 0 or radius > 8:
        return None
    shifts = [(int(y), int(x)) for y in dys for x in dxs if abs(y) + abs(x) <= radius]
    if not shifts or len(shifts) * len(ys) > 32768:
        return None
    near = overlaps(shifts)
    scores = near - penalty * np.array([abs(y) + abs(x) for y, x in shifts])
    best = int(np.argmax(scores))
    runner = np.partition(scores, -2)[-2] if len(scores) > 1 else -np.inf
    unseen = float(after.max()) - penalty * (radius + 1)
    if scores[best] - max(runner, unseen) <= 2e-5:
        return None
    return LocalShift(shifts[best][0] * step, shifts[best][1] * step,
                      scores[best] - max(runner, unseen)), float(near[best])
