"""TP線の仕上げ色を、線色と周囲の面の組み合わせから学習する。"""
from collections import Counter
from hashlib import blake2b

import numpy as np

from . import bucket_fill, reference_check


LARGE_FACE_RATIO = 3
MIN_LARGE_FACE = 32
MAX_LARGE_FACE_THRESHOLD = 256
MIN_FINISH_EVIDENCE = 6


def finish_palette(guides):
    """人が選んだ見本の元TP画素から、形状照合に依存しない色表を先に作る。"""
    from . import auto_paint as ap
    colors = {}
    for guide in guides:
        geometry = guide.initial
        if geometry is None:
            continue
        known = geometry.primary & ~guide.open & ~guide.undecided
        for original in np.unique(geometry.values[known]).tolist():
            rgb = reference_check._to_rgb(original)
            selected = known & (geometry.values == original)
            values, counts = np.unique(guide.values[selected], return_counts=True)
            votes = colors.setdefault(rgb, Counter())
            for value, count in zip(values.tolist(), counts.tolist()):
                if ap._paintable(value):
                    votes[int(value)] += int(count)
    return {rgb: {value: count for value, count in votes.items()
                  if count >= MIN_FINISH_EVIDENCE} for rgb, votes in colors.items()}


def small_face_finish(geometry, filled, painted, allowed, *, cache=None):
    """囲みの内側が確定していれば、同じ仕上げ色のTP線を形によらず選べる。"""
    from . import auto_paint as ap
    x0, y0, x1, y1 = filled.bounds
    height, width = geometry.values.shape
    ax0, ay0 = max(0, x0 - 2), max(0, y0 - 2)
    ax1, ay1 = min(width, x1 + 2), min(height, y1 + 2)
    local = np.zeros((ay1 - ay0, ax1 - ax0), dtype=bool)
    local[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0] = filled.local
    labels = geometry.regions.labels[ay0:ay1, ax0:ax1][bucket_fill.dilate(local, 2) & ~local]
    answers = set()
    for label in np.unique(labels).tolist():
        if geometry.regions.value(label) != 0 or geometry.regions.sizes[label] < 3:
            continue
        if cache is None or label not in cache:
            bx0, by0, bx1, by1 = geometry.regions.bounds(label)
            sample = painted[by0:by1, bx0:bx1][geometry.regions.local_mask(label)]
            answer = ap._majority(sample)
            if cache is not None:
                cache[label] = answer
        else:
            answer = cache[label]
        value, share = answer
        if value in allowed and share >= ap.HINT_SHARE:
            answers.add(value)
    return reference_check._to_rgb(next(iter(answers))) if len(answers) == 1 else None


def enclosed_face_rules(guides, finishes):
    """見本でTP線と小さな内側の面が同色の用例だけを覚える。"""
    from . import auto_paint as ap
    rules = set()
    for guide in guides:
        geometry = guide.initial
        if geometry is None:
            continue
        faces = {}
        for label in np.unique(geometry.regions.labels[geometry.primary]).tolist():
            rgb = reference_check._to_rgb(geometry.regions.value(label))
            bounds = geometry.regions.bounds(label)
            x0, y0, x1, y1 = bounds
            local = geometry.regions.local_mask(label)
            if (guide.open[y0:y1, x0:x1][local].any()
                    or guide.undecided[y0:y1, x0:x1][local].any()):
                continue
            value, share = ap._majority(guide.values[y0:y1, x0:x1][local])
            if value not in finishes.get(rgb, {}) or share < ap.HINT_SHARE:
                continue
            filled = bucket_fill.CompactFill(bucket_fill.REASON_OK, rgb, bounds, local, (), bounds)
            if value in large_face_colors(geometry, filled, guide.values, cache=faces):
                continue
            if small_face_finish(geometry, filled, guide.values, {value: 1}) is not None:
                rules.add((rgb, value))
    return rules


def enclosed_face_color(geometry, filled, line_finishes, rules):
    """見本と同じ用途のTP境界に囲まれた小面へ、その仕上げ色を引き継ぐ。"""
    x0, y0, x1, y1 = filled.bounds
    height, width = geometry.values.shape
    if (filled.target is not None or int(filled.local.sum()) > 4096
            or x0 == 0 or y0 == 0 or x1 == width or y1 == height):
        return None
    ax0, ay0, ax1, ay1 = x0 - 1, y0 - 1, x1 + 1, y1 + 1
    local = np.zeros((ay1 - ay0, ax1 - ax0), dtype=bool)
    local[1:-1, 1:-1] = filled.local
    ring = bucket_fill.dilate(local, 1) & ~local
    labels, counts = np.unique(geometry.regions.labels[ay0:ay1, ax0:ax1][ring],
                               return_counts=True)
    votes = Counter()
    for label, count in zip(labels.tolist(), counts.tolist()):
        value = line_finishes.get(label)
        original = reference_check._to_rgb(geometry.regions.value(label))
        if value is not None and (original, value) in rules:
            votes[value] += count
    answers = [value for value, count in votes.items() if count >= max(3, int(ring.sum() * 0.35))]
    return reference_check._to_rgb(answers[0]) if len(answers) == 1 else None


def large_face_colors(geometry, filled, painted, *, cache):
    """線に接する大きな面の色。これらは線の仕上げ色として使わない。"""
    from . import auto_paint as ap
    x0, y0, x1, y1 = filled.bounds
    height, width = geometry.values.shape
    ax0, ay0 = max(0, x0 - 2), max(0, y0 - 2)
    ax1, ay1 = min(width, x1 + 2), min(height, y1 + 2)
    local = np.zeros((ay1 - ay0, ax1 - ax0), dtype=bool)
    local[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0] = filled.local
    ring = bucket_fill.dilate(local, 2) & ~local
    ring &= ~geometry.boundary[ay0:ay1, ax0:ax1]
    labels = geometry.regions.labels[ay0:ay1, ax0:ax1][ring]
    minimum = max(MIN_LARGE_FACE, min(MAX_LARGE_FACE_THRESHOLD,
                                    int(filled.local.sum()) * LARGE_FACE_RATIO))
    colors = set()
    neighbours = np.unique(labels).tolist()
    # 小さな囲みの内側（頬など）を、外側の肌と同じ「大きな面」にしない。
    largest = max((int(geometry.regions.sizes[label]) for label in neighbours
                   if geometry.regions.value(label) != ap.GAP_VALUE), default=0)
    minimum = max(minimum, largest * 0.25)
    for label in neighbours:
        if geometry.regions.sizes[label] < minimum:
            continue
        if geometry.regions.value(label) == ap.GAP_VALUE:
            continue
        if label not in cache:
            bx0, by0, bx1, by1 = geometry.regions.bounds(label)
            values = painted[by0:by1, bx0:bx1][geometry.regions.local_mask(label)]
            value, share = ap._majority(values)
            cache[label] = value if value is not None and share >= ap.FILL_SHARE and ap._paintable(value) else None
        if cache[label] is not None:
            colors.add(cache[label])
    return frozenset(colors)


def palette(guide):
    """見本の初期TP線が何色で仕上げられたかを集計する。"""
    from . import auto_paint as ap
    from .auto_bucket import geometry_key
    geometry = guide.initial
    if geometry is None:
        return []
    digest = blake2b(digest_size=16)
    for values in (guide.values, guide.open, guide.undecided):
        digest.update(values.tobytes())
    signature = geometry_key(geometry), digest.digest()
    cached = getattr(guide, "_trace_palette", None)
    if cached is not None and cached[0] == signature:
        return cached[1]
    rules = Counter()
    faces = {}
    for index in np.unique(geometry.regions.labels[geometry.primary]).tolist():
        original = geometry.regions.value(index)
        rgb = reference_check._to_rgb(original)
        if rgb is None or not ap._is_primary(rgb):
            continue
        bounds = geometry.regions.bounds(index)
        x0, y0, x1, y1 = bounds
        local = geometry.regions.local_mask(index)
        if guide.open[y0:y1, x0:x1][local].any() or guide.undecided[y0:y1, x0:x1][local].any():
            continue
        filled = bucket_fill.CompactFill(bucket_fill.REASON_OK, rgb, bounds, local, (), bounds)
        hosts = large_face_colors(geometry, filled, guide.values, cache=faces)
        sample = guide.values[y0:y1, x0:x1][local]
        values, counts = np.unique(sample, return_counts=True)
        candidates = [(int(value), int(count)) for value, count in zip(values, counts)
                      if value not in hosts and ap._paintable(value)]
        if not candidates:
            continue
        color, count = max(candidates, key=lambda item: item[1])
        # 周囲の面を拾った画素を除いた後、線の仕上げ色として十分な根拠があること。
        if count < min(3, len(sample)) or count < len(sample) * 0.2:
            continue
        if count < sum(amount for _value, amount in candidates) * 0.8:
            continue
        rules[(rgb, hosts, color)] += count
    result = [(rgb, hosts, color, count) for (rgb, hosts, color), count in rules.items()]
    guide._trace_palette = (signature, result)
    return result


def candidates(trace_rgb, hosts, palettes, *, minimum=MIN_FINISH_EVIDENCE):
    votes = Counter()
    contextual = []
    for rules in palettes:
        for rgb, context, color, count in rules:
            if rgb != trace_rgb or color in hosts:
                continue
            if hosts and context and not hosts.intersection(context):
                continue
            score = len(hosts.intersection(context)) / len(hosts.union(context)) if hosts else 0.0
            contextual.append((score, color, count))
    # 周囲が一致する用例を優先し、文脈の無い別用途の線を混ぜない。
    best = max((score for score, _color, _count in contextual), default=0.0)
    for score, color, count in contextual:
        if score == best:
            votes[color] += count
    return {color: count for color, count in votes.items() if count >= minimum}


def local_finish(target, guide, filled, margin, alignment, allowed):
    """線全体の形が変わった場合も、同色の初期TP線上の仕上げ色を採る。"""
    from . import auto_paint as ap
    geometry = guide.initial
    if geometry is None or not allowed:
        return None
    shift, score = alignment.shift(target, geometry, filled.bounds, margin)
    if score < ap.MIN_COLOR_LINE_OVERLAP:
        return None
    sy, sx, inside = ap._shifted(target, filled.local, filled.bounds, shift)
    height, width = geometry.values.shape
    code = reference_check._pack(filled.target)
    selected = np.zeros(sy.shape, dtype=np.uint32)
    found = np.zeros(sy.shape, dtype=bool)
    offsets = sorted(((dy, dx) for dy in range(-2, 3) for dx in range(-2, 3)),
                     key=lambda item: (item[0] ** 2 + item[1] ** 2, item))
    for dy, dx in offsets:
        y, x = sy + dy, sx + dx
        at = np.flatnonzero(inside & ~found & (y >= 0) & (y < height) & (x >= 0) & (x < width))
        at = at[geometry.values[y[at], x[at]] == code]
        found[at] = True
        known = ~(guide.open[y[at], x[at]] | guide.undecided[y[at], x[at]])
        selected[at[known]] = guide.values[y[at[known]], x[at[known]]]
    values = selected[np.isin(selected, list(allowed))]
    if len(values) < max(2, int(len(sy) * 0.35)):
        return None
    value, share = ap._majority(values)
    return reference_check._to_rgb(value) if share >= 0.9 else None


def choose(trace_rgb, hosts, palettes, direct, *, finishes=None):
    """近くで照合できた線を優先し、無ければ同じ文脈の仕上げ色を使う。"""
    votes = candidates(trace_rgb, hosts, palettes)
    direct_votes = candidates(trace_rgb, hosts, palettes, minimum=1)
    if finishes is not None:
        direct_votes.update({value: count for value, count in finishes.get(trace_rgb, {}).items()
                             if value not in hosts})
    accepted = [reference_check._pack(rgb) for rgb in direct
                if rgb is not None and reference_check._pack(rgb) in direct_votes]
    if accepted and all(value == accepted[0] for value in accepted):
        return reference_check._to_rgb(accepted[0])
    if not accepted and len(votes) == 1:
        return reference_check._to_rgb(next(iter(votes)))
    if not accepted and finishes is not None:
        remaining = set(finishes.get(trace_rgb, {})) - set(hosts)
        if len(remaining) == 1:
            return reference_check._to_rgb(next(iter(remaining)))
    return None
