"""セル間で共有する動きの候補と、全探索を省略できる場合の厳密な上界検査。"""
from collections import OrderedDict
from math import isfinite

import numpy as np

from . import native


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
    step, total = problem['step'], problem['total']
    if not hint or total < 4:
        return None
    if not native.available():
        return certify_numpy(problem, hint)
    before_bytes = problem.get('before_bytes')
    if before_bytes is None:
        before = problem['before']
        if not np.all((before == 0) | (before == 1)):
            return certify_numpy(problem, hint)
        before_bytes = before.astype(np.uint8).tobytes()
    dy, dx = hint
    # Python 固有の除算や大きな整数では、f64 への事前変換で丸めが変わりうる。
    if any(type(value) not in (int, float) or abs(value) > 2**53 or not isfinite(value)
           for value in (dy, dx)) or step > 2**53:
        return certify_numpy(problem, (dy, dx))
    exact, wide = problem['boundaries']
    if exact.dtype != np.bool_ or wide.dtype != np.bool_:
        if not all((mask.dtype == np.bool_ or np.issubdtype(mask.dtype, np.integer))
                   and np.all((mask == 0) | (mask == 1)) for mask in (exact, wide)):
            return certify_numpy(problem, (dy, dx))
        exact, wide = exact.astype(np.uint8), wide.astype(np.uint8)
    height, width = exact.shape
    result = native.certify_local_shift(
        before_bytes, exact.tobytes(), wide.tobytes(), width, height, problem['size'],
        list(map(int, problem['dys'])), list(map(int, problem['dxs'])), total, step,
        ap.MOTION_PENALTY, ap.NEAR_LINE_WEIGHT, (dy, dx))
    if result is None:
        return None
    shift, gap, share = result
    return LocalShift(*shift, gap), share


def certify_numpy(problem, hint):
    """候補の重なりと未検査候補の上限を NumPy で計算する。"""
    from . import auto_paint as ap
    if hint is None:
        return None
    before = problem['before']
    step, total = problem['step'], problem['total']
    if not hint or total < 4 or not np.all((before == 0) | (before == 1)):
        return None
    ys, xs = np.nonzero(before)
    dys, dxs = problem['dys'], problem['dxs']
    dy, dx = (int(np.rint(value / step)) for value in hint)
    if dy not in dys or dx not in dxs:
        return None
    rows, cols = problem['size']
    exact, wide = problem['boundaries']
    height, width = exact.shape
    max_overlap = float(ap._weighted_overlap(int(exact.any()), int(wide.any())))

    def overlaps(shifts):
        shifts = np.asarray(shifts, dtype=np.int64)
        y = (ys[None] + shifts[:, 0, None]) % rows
        x = (xs[None] + shifts[:, 1, None]) % cols
        valid = (y < height) & (x < width)
        np.minimum(y, height - 1, out=y)
        np.minimum(x, width - 1, out=x)
        exact_count = np.count_nonzero(valid & exact[y, x], axis=1)
        wide_count = np.count_nonzero(valid & wide[y, x], axis=1)
        return ap._weighted_overlap(exact_count, wide_count) / total

    penalty = ap.MOTION_PENALTY * step
    score = float(overlaps([(dy, dx)])[0]) - penalty * (abs(dy) + abs(dx))
    # 大きな証明表が必要なら、従来のFFTを使う方が速い。
    radius = int(np.floor((max_overlap - score + 2e-5) / penalty))
    if radius < 0 or radius > 8:
        return None
    shifts = [(int(y), int(x)) for y in dys for x in dxs if abs(y) + abs(x) <= radius]
    if not shifts or len(shifts) * len(ys) > 32768:
        return None
    near = overlaps(shifts)
    scores = near - penalty * np.array([abs(y) + abs(x) for y, x in shifts])
    best = int(np.argmax(scores))
    runner = np.partition(scores, -2)[-2] if len(scores) > 1 else -np.inf
    unseen = max_overlap - penalty * (radius + 1) if len(shifts) < len(dys) * len(dxs) else -np.inf
    if scores[best] - max(runner, unseen) <= 2e-5 or runner < unseen:
        return None
    # gap も全探索と一致させるため、未検査候補が次点を超えない場合だけ返す。
    return LocalShift(shifts[best][0] * step, shifts[best][1] * step,
                      scores[best] - runner), float(near[best])
