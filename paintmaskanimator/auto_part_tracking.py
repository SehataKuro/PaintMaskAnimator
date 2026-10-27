"""確定した部位を保持し、線画の局所的な変形を使って再発見する。"""
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from . import bucket_fill, line_flow, reference_check


MEMORY_BLOCK = 128
MAX_DONORS = 3
MAX_FLOW_PAIRS = 64
MAX_TRACK_STEPS = 12
MAX_TILE_STEPS = 2


@dataclass
class _FlowPair:
    forward: line_flow.Flow
    backward: line_flow.Flow
    x0: int
    y0: int

    def coordinates(self, ys, xs):
        dy, dx = self.forward.at(ys - self.y0, xs - self.x0)
        sy, sx = ys + dy, xs + dx
        by, bx = self.backward.at(sy - self.y0, sx - self.x0)
        return (np.rint(sy).astype(np.int64), np.rint(sx).astype(np.int64),
                np.rint(sy + by).astype(np.int64), np.rint(sx + bx).astype(np.int64),
                np.ones(ys.shape, dtype=bool))


@dataclass
class _FlowPath:
    pairs: list
    shape: tuple

    def coordinates(self, ys, xs):
        sy, sx = ys.astype(np.float64), xs.astype(np.float64)
        valid = np.ones(ys.shape, dtype=bool)
        height, width = self.shape
        for pair in self.pairs:
            dy, dx = pair.forward.at(sy - pair.y0, sx - pair.x0)
            sy, sx = sy + dy, sx + dx
            valid &= (sy >= 0) & (sy < height) & (sx >= 0) & (sx < width)
        by, bx = sy.copy(), sx.copy()
        for pair in reversed(self.pairs):
            dy, dx = pair.backward.at(by - pair.y0, bx - pair.x0)
            by, bx = by + dy, bx + dx
        return (np.rint(sy).astype(np.int64), np.rint(sx).astype(np.int64),
                np.rint(by).astype(np.int64), np.rint(bx).astype(np.int64), valid)


def _in_mask(ys, xs, x0, y0, mask):
    y, x = ys - y0, xs - x0
    inside = (y >= 0) & (y < mask.shape[0]) & (x >= 0) & (x < mask.shape[1])
    result = np.zeros(ys.shape, dtype=bool)
    result[inside] = mask[y[inside], x[inside]]
    return result


def _near_black(cell, ys, xs):
    height, width = cell.black.shape
    result = np.zeros(ys.shape, dtype=bool)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            result |= cell.black[np.clip(ys + dy, 0, height - 1), np.clip(xs + dx, 0, width - 1)]
    return result


class PartTracker:
    """色を確定した場所の索引。途中の未塗りセルで古い確定部分を消さない。"""

    def __init__(self, shape, geometry_for=None, alignment=None):
        from .auto_alignment import AlignmentCache
        self.shape = shape
        self.geometry_for = geometry_for
        self.alignment = alignment if alignment is not None else AlignmentCache(gpu="off")
        self.coverage = {}
        self.pairs = OrderedDict()
        self.flow_solves = 0
        self.matches = 0
        self.long_matches = 0

    def _coverage(self, bounds, mask):
        x0, y0, x1, y1 = bounds
        counts = {}
        for ty in range(y0 // MEMORY_BLOCK, (y1 - 1) // MEMORY_BLOCK + 1):
            for tx in range(x0 // MEMORY_BLOCK, (x1 - 1) // MEMORY_BLOCK + 1):
                ax0, ay0 = max(x0, tx * MEMORY_BLOCK), max(y0, ty * MEMORY_BLOCK)
                ax1, ay1 = min(x1, (tx + 1) * MEMORY_BLOCK), min(y1, (ty + 1) * MEMORY_BLOCK)
                count = int(mask[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0].sum())
                if count >= 8:
                    counts[(ty, tx)] = count
        return counts

    def remember_fixed(self, index, guide):
        known = (guide.values != 0) & ~guide.black & ~guide.primary & ~guide.open & ~guide.undecided
        height, width = self.shape
        self.coverage[index] = self._coverage((0, 0, width, height), known)

    def remember(self, index, paint):
        counts = {}
        for _rgb, bounds, mask in paint.paints:
            for key, count in self._coverage(bounds, mask).items():
                counts[key] = min(MEMORY_BLOCK ** 2, counts.get(key, 0) + count)
        # 更新するのはこのセルの確定部分だけ。他セルの最後の成功は保持する。
        self.coverage[index] = counts

    def donors(self, index, filled):
        query = self._coverage(filled.bounds, filled.local)
        total = sum(query.values())
        if not total:
            return []
        ranked = []
        for other, known in self.coverage.items():
            if other == index or abs(other - index) > MAX_TRACK_STEPS or not known:
                continue
            overlap = 0
            for (ty, tx), count in query.items():
                nearby = max((known.get((ty + dy, tx + dx), 0)
                              for dy in (-1, 0, 1) for dx in (-1, 0, 1)), default=0)
                overlap += min(count, nearby)
            if overlap >= max(8, total * 0.2):
                ranked.append((overlap / total, -abs(other - index), -other, other))
        return [item[-1] for item in sorted(ranked, reverse=True)[:MAX_DONORS]]

    def _pair(self, target, source):
        from .auto_bucket import geometry_key
        key = (geometry_key(target), geometry_key(source))
        if key in self.pairs:
            self.pairs.move_to_end(key)
            return self.pairs[key]
        reverse = key[1], key[0]
        if reverse in self.pairs:
            self.pairs.move_to_end(reverse)
            found = self.pairs[reverse]
            return _FlowPair(found.backward, found.forward, found.x0, found.y0)
        if target.values.shape != self.shape or source.values.shape != self.shape:
            return None
        first, second = bucket_fill.mask_bounds(target.boundary), bucket_fill.mask_bounds(source.boundary)
        if first is None or second is None:
            return None
        height, width = self.shape
        x0, y0 = max(0, min(first[0], second[0]) - 32), max(0, min(first[1], second[1]) - 32)
        x1, y1 = min(width, max(first[2], second[2]) + 32), min(height, max(first[3], second[3]) + 32)
        left, right = target.boundary[y0:y1, x0:x1], source.boundary[y0:y1, x0:x1]
        pair = _FlowPair(line_flow.estimate_flow(left, right), line_flow.estimate_flow(right, left),
                         x0, y0)
        self.flow_solves += 1
        self.pairs[key] = pair
        if len(self.pairs) > MAX_FLOW_PAIRS:
            self.pairs.popitem(last=False)
        return pair

    def _path(self, index, target, guide):
        if self.geometry_for is None or guide.index is None:
            return self._pair(target, guide.initial)
        direction = 1 if guide.index > index else -1
        pairs = []
        for step in range(index, guide.index, direction):
            pair = self._pair(self.geometry_for(step), self.geometry_for(step + direction))
            if pair is None:
                return None
            pairs.append(pair)
        return _FlowPath(pairs, self.shape)

    def _color(self, index, target, filled, guide):
        rgb = self._flow_color(index, target, filled, guide)
        return rgb if rgb is not None else self._tiled_color(index, target, filled, guide)

    def _flow_color(self, index, target, filled, guide):
        from . import auto_paint as ap
        source = guide.initial
        if source is None:
            return None
        pair = self._path(index, target, guide)
        if pair is None:
            return None
        height, width = self.shape
        x0, y0, x1, y1 = filled.bounds
        ax0, ay0 = max(0, x0 - 1), max(0, y0 - 1)
        ax1, ay1 = min(width, x1 + 1), min(height, y1 + 1)
        local = np.zeros((ay1 - ay0, ax1 - ax0), dtype=bool)
        local[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0] = filled.local
        outline = bucket_fill.dilate(local, 1) & ~local
        outline &= target.boundary[ay0:ay1, ax0:ax1]
        oy, ox = np.nonzero(outline)
        if len(oy) < 8:
            return None
        oy, ox = oy + ay0, ox + ax0
        sy, sx, by, bx, consistent = pair.coordinates(oy, ox)
        # 長い無地の輪郭では接線方向の位置は決まらない。同じ部位の輪郭へ戻ればよい。
        outline_band = bucket_fill.dilate(local, 1) & ~bucket_fill.erode(local, 2)
        consistent &= _in_mask(by, bx, ax0, ay0, outline_band)
        inside = (sy >= 0) & (sy < height) & (sx >= 0) & (sx < width)
        sy, sx = np.clip(sy, 0, height - 1), np.clip(sx, 0, width - 1)
        hits = inside & consistent & source.wide_boundary[sy, sx]
        hits &= np.where(target.black[oy, ox], _near_black(source, sy, sx), source.wide_primary[sy, sx])
        if hits.mean() < 0.5:
            return None
        quadrants = (oy >= (y0 + y1) / 2).astype(np.uint8) * 2 + (ox >= (x0 + x1) / 2)
        for quadrant in range(4):
            selected = quadrants == quadrant
            if selected.sum() >= 4 and hits[selected].mean() < 0.35:
                return None  # 一辺だけの一致を、その部位全体の対応と取り違えない。
        ys, xs = np.nonzero(filled.local)
        ys, xs = ys + y0, xs + x0
        sy, sx, by, bx, consistent = pair.coordinates(ys, xs)
        consistent &= _in_mask(by, bx, x0, y0, filled.local)
        known = (sy >= 0) & (sy < height) & (sx >= 0) & (sx < width) & consistent
        sy, sx = np.clip(sy, 0, height - 1), np.clip(sx, 0, width - 1)
        known &= ~source.boundary[sy, sx] & ~guide.open[sy, sx] & ~guide.undecided[sy, sx]
        confirmed = guide.confirmed
        if confirmed is not None:
            known &= confirmed[sy, sx]
        known &= (guide.values[sy, sx] != 0) & ~guide.black[sy, sx] & ~guide.primary[sy, sx]
        size = len(ys)
        if known.sum() < size * 0.7:
            return None
        value, share = ap._majority(guide.values[sy[known], sx[known]])
        if value is None or share < 0.95 or not ap._paintable(value):
            return None
        if size < 4096:
            matched = known & (guide.values[sy, sx] == value)
            labels = np.unique(guide.regions.labels[sy[matched], sx[matched]])
            other = sum(int(guide.regions.sizes[label]) for label in labels)
            if other > size * 4 or matched.sum() < other * 0.3:
                return None
        return reference_check._to_rgb(value)

    def _tiled_color(self, index, target, filled, guide):
        """滑らかな動きで合わない部位は、小区画を独立に追跡して色だけを集計する。"""
        from . import auto_paint as ap
        source = guide.initial
        if source is None:
            return None
        if self.geometry_for is None or guide.index is None:
            steps = [(target, source)]
        else:
            if abs(guide.index - index) > MAX_TILE_STEPS:
                return None  # 長い経路は共有した変形場で追い、同じFFT探索を区画ごとに繰り返さない。
            direction = 1 if guide.index > index else -1
            steps = [(self.geometry_for(step), self.geometry_for(step + direction))
                     for step in range(index, guide.index, direction)]
        if any(left.values.shape != self.shape or right.values.shape != self.shape
               for left, right in steps):
            return None
        height, width = self.shape
        x0, y0, x1, y1 = filled.bounds
        outline = bucket_fill.dilate(filled.local, 1) & ~bucket_fill.erode(filled.local, 1)
        samples, labels = [], set()
        size = int(filled.local.sum())
        tile_size = 64 if size < 262144 else 96
        for ty in range(y0, y1, tile_size):
            for tx in range(x0, x1, tile_size):
                sub = filled.local[ty - y0:ty - y0 + tile_size, tx - x0:tx - x0 + tile_size]
                if sub.sum() < 16:
                    continue
                oy, ox = np.nonzero(outline[ty - y0:ty - y0 + sub.shape[0],
                                           tx - x0:tx - x0 + sub.shape[1]])
                real = target.wide_boundary[oy + ty, ox + tx]
                oy, ox = oy[real], ox[real]
                if len(oy) < 4:
                    continue
                initial = (tx, ty, tx + sub.shape[1], ty + sub.shape[0])
                bounds, dy, dx, valid = initial, 0, 0, True
                for left, right in steps:
                    clipped = (max(0, bounds[0]), max(0, bounds[1]),
                               min(width, bounds[2]), min(height, bounds[3]))
                    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
                        valid = False
                        break
                    shift, score = self.alignment.shift(left, right, clipped, 48)
                    if score < 0.5:
                        valid = False
                        break
                    dy, dx = dy + shift[0], dx + shift[1]
                    bounds = (initial[0] + dx, initial[1] + dy,
                              initial[2] + dx, initial[3] + dy)
                if not valid:
                    continue
                back_y, back_x = 0, 0
                for left, right in reversed(steps):
                    clipped = (max(0, bounds[0]), max(0, bounds[1]),
                               min(width, bounds[2]), min(height, bounds[3]))
                    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
                        valid = False
                        break
                    shift, score = self.alignment.shift(right, left, clipped, 48)
                    if score < 0.5:
                        valid = False
                        break
                    back_y, back_x = back_y + shift[0], back_x + shift[1]
                    bounds = (bounds[0] + shift[1], bounds[1] + shift[0],
                              bounds[2] + shift[1], bounds[3] + shift[0])
                if not valid:
                    continue
                sy, sx = oy + ty + dy, ox + tx + dx
                at = (sy >= 0) & (sy < height) & (sx >= 0) & (sx < width)
                hit = np.zeros(at.shape, dtype=bool)
                hit[at] = source.wide_boundary[sy[at], sx[at]]
                syc, sxc = np.clip(sy, 0, height - 1), np.clip(sx, 0, width - 1)
                hit &= np.where(_near_black(target, oy + ty, ox + tx),
                                _near_black(source, syc, sxc), source.wide_primary[syc, sxc])
                if hit.mean() < 0.5:
                    continue
                ys, xs = np.nonzero(sub)
                ys, xs = ys + ty, xs + tx
                sy, sx = ys + dy, xs + dx
                known = (sy >= 0) & (sy < height) & (sx >= 0) & (sx < width)
                sy, sx = np.clip(sy, 0, height - 1), np.clip(sx, 0, width - 1)
                known &= _in_mask(sy + back_y, sx + back_x, x0, y0, filled.local)
                known &= ~source.boundary[sy, sx] & ~guide.open[sy, sx] & ~guide.undecided[sy, sx]
                known &= (guide.values[sy, sx] != 0) & ~guide.black[sy, sx] & ~guide.primary[sy, sx]
                if guide.confirmed is not None:
                    known &= guide.confirmed[sy, sx]
                if known.sum() < len(ys) * 0.5:
                    continue
                values = guide.values[sy[known], sx[known]]
                value, share = ap._majority(values)
                if value is None or share < 0.9 or not ap._paintable(value):
                    continue
                samples.append(values)
                labels.update(np.unique(guide.regions.labels[sy[known], sx[known]]).tolist())
        if not samples or sum(len(values) for values in samples) < size * 0.5:
            return None
        values = np.concatenate(samples)
        value, share = ap._majority(values)
        if value is None or share < 0.95 or not ap._paintable(value):
            return None
        if size < 4096 and sum(int(guide.regions.sizes[label]) for label in labels) > size * 4:
            return None
        return reference_check._to_rgb(value)

    def color(self, index, target, filled, guide_for):
        if filled.target is not None or int(filled.local.sum()) < 16:
            return None
        # 外側背景を追跡対象にすると、全画像の空白を毎回処理することになる。
        height, width = self.shape
        if filled.bounds == (0, 0, width, height):
            return None
        answers = []
        for other in self.donors(index, filled):
            rgb = self._color(index, target, filled, guide_for(other))
            if rgb is not None:
                answers.append((other, rgb))
        if not answers or any(rgb != answers[0][1] for _other, rgb in answers):
            return None
        self.matches += 1
        self.long_matches += int(min(abs(other - index) for other, _rgb in answers) > 4)
        return answers[0][1]
