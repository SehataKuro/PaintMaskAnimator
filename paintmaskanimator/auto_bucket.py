"""色とクリック位置の選択を、通常バケツの領域判定から分離する。"""
from dataclasses import dataclass, replace
from hashlib import blake2b

import numpy as np

from . import bucket_fill, native, reference_check
from .auto_alignment import AlignmentCache


# 面の中に重なった見本の線は色の候補に数えない。形の伸縮は両方向の
# 重なりで確かめるが、90%の完全一致は求めない。
FACE_MATCH_SHARE = 0.72
FACE_REVERSE_SHARE = 0.50
FACE_SAMPLE_SHARE = 0.55
TRACE_MATCH_SHARE = 0.75
TRACE_REVERSE_SHARE = 0.45
TRACE_RADIUS = 2
FACE_REFINE_RADIUS = 5


@dataclass(frozen=True)
class BucketClick:
    x: int
    y: int
    rgb: tuple[int, int, int]


def execute_click(pixels, click, options, *, line_cache=None):
    filled = bucket_fill.compute_fill_region(
        pixels, click.x, click.y, options, line_cache=line_cache,
    )
    if not filled.ok:
        return filled, []
    return filled, _paints(bucket_fill.CompactFill.from_result(filled), click.rgb)


def _paints(filled, color):
    # 含み塗りの島で外接矩形が広がっている場合も、通常バケツと同じ範囲。
    x0, y0, x1, y1 = filled.bounds
    local_bounds = bucket_fill.mask_bounds(filled.local)
    if local_bounds is None:
        return []
    lx0, ly0, lx1, ly1 = local_bounds
    paints = [(color, (x0 + lx0, y0 + ly0, x0 + lx1, y0 + ly1),
               filled.local[ly0:ly1, lx0:lx1].copy())]
    paints.extend((rgb, bounds, local.copy()) for rgb, bounds, local in filled.islands)
    return paints


def geometry_key(cell):
    key = getattr(cell, "_bucket_geometry_key", None)
    if key is None:
        key = (cell.values.shape, type(cell.regions).__name__,
               blake2b(cell.regions.values.tobytes(), digest_size=16).digest())
        cell._bucket_geometry_key = key
    return key


class BucketWork:
    """対象の領域一覧・位置合わせ・色の判断を、塗り直しの回をまたいで保持する。"""

    def __init__(self, target, options, *, alignment=None, reference_regions=None):
        self.target = target
        # 面の含み塗りでTP線まで同色にしない。手動バケツの設定は変更しない。
        self.options = replace(options, adjacent=True, target_color=bucket_fill.TARGET_ALL,
                               include_masks=False, island_replacements=())
        self.alignment = alignment if alignment is not None else AlignmentCache(gpu="off")
        self.entries = None
        self.matches = {}
        self.decisions = {}
        self.reused_decisions = 0
        self.decision_solves = 0
        self.reference_regions = reference_regions

    def _reference(self, geometry, x, y, sessions):
        if self.reference_regions is not None and getattr(geometry, "_native_immutable", False):
            return self.reference_regions.compact(geometry, x, y, self.options, active=sessions)
        if geometry not in sessions:
            sessions[geometry] = bucket_fill.BucketSession(geometry.pixels, self.options, indexed=True)
        result = sessions[geometry].compact(x, y)
        return result

    def collect(self):
        if self.entries is not None:
            return self.entries
        from . import auto_paint as ap
        target = self.target
        visited = np.zeros(target.values.shape, dtype=bool)
        session = bucket_fill.BucketSession(target.pixels, self.options, indexed=True)
        entries = []
        regions = target.regions
        trace_labels = np.unique(regions.labels[target.primary])
        eligible = (regions.sizes >= ap.MIN_REGION_PIXELS) | np.isin(
            np.arange(len(regions.sizes)), trace_labels,
        )
        for index in np.nonzero(eligible)[0].tolist():
            value = regions.value(index)
            if value == ap.GAP_VALUE:
                continue
            x0, y0, x1, y1 = regions.bounds(index)
            ys, xs = np.nonzero(regions.local_mask(index) & ~visited[y0:y1, x0:x1])
            if not len(xs):
                continue
            middle = len(xs) // 2
            x, y = int(xs[middle] + x0), int(ys[middle] + y0)
            if target.black[y, x]:
                continue
            filled = session.compact(x, y)
            if filled.local is not None and filled.bounds is not None:
                bx0, by0, bx1, by1 = filled.bounds
                visited[by0:by1, bx0:bx1] |= filled.local
            if filled.ok:
                entries.append((x, y, value, filled))
        # 全画像の前処理・索引はここで解放し、小さな矩形の一覧だけを残す。
        self.entries = entries
        return entries

    def choose_color(self, guide, ordinal, entry, margin, sessions):
        geometries = [guide.initial, guide] if guide.initial is not None else [guide]
        for geometry in geometries:
            original_lines = geometry is guide.initial
            key = (ordinal, geometry_key(geometry), margin, original_lines)
            if key not in self.matches:
                self.matches[key] = self._match(
                    geometry, entry[3], margin, sessions, original_lines=original_lines,
                )
            reference = self.matches[key]
            if reference is None:
                continue
            sampled = native.sample_region(guide, reference)
            if sampled is not None:
                from . import auto_paint as ap
                _inside, total, value, count, _other, _bad, unknown, _y, _x = sampled
                decision_key = ("native", ordinal, reference.bounds, total, value, count, unknown)
                if decision_key in self.decisions:
                    self.reused_decisions += 1
                    rgb = self.decisions[decision_key]
                else:
                    rgb = None
                    if (not unknown and value is not None and total
                            and count / total >= ap.HINT_SHARE and ap._paintable(value)):
                        rgb = reference_check._to_rgb(value)
                    self.decisions[decision_key] = rgb
                    self.decision_solves += 1
                if rgb is not None:
                    return rgb
                continue
            x0, y0, x1, y1 = reference.bounds
            sample = guide.values[y0:y1, x0:x1][reference.local]
            opened = guide.open[y0:y1, x0:x1][reference.local]
            undecided = guide.undecided[y0:y1, x0:x1][reference.local]
            digest = blake2b(digest_size=16)
            for values in (sample, opened, undecided):
                digest.update(values.tobytes())
            mask_key = getattr(reference, "_sample_mask_key", None)
            if mask_key is None:
                mask_key = (reference.bounds,
                            blake2b(reference.local.tobytes(), digest_size=16).digest())
                reference._sample_mask_key = mask_key
            # 対応する面を確認した後の色判定は、その面の画素だけに依存する。
            # 別の面の色が変わって見本全体の領域番号が変わっても再集計しない。
            decision_key = (ordinal, mask_key, digest.digest())
            if decision_key in self.decisions:
                self.reused_decisions += 1
                rgb = self.decisions[decision_key]
            else:
                from . import auto_paint as ap
                rgb = None
                if not opened.any() and not undecided.any():
                    value, share = ap._majority(sample)
                    if value is not None and share >= ap.HINT_SHARE and ap._paintable(value):
                        rgb = reference_check._to_rgb(value)
                self.decisions[decision_key] = rgb
                self.decision_solves += 1
            if rgb is not None:
                return rgb
        rgb = self._sample_face_color(guide, entry[3], margin)
        if rgb is not None:
            return rgb
        rgb = self._refine_face_color(guide, entry[3], margin)
        return rgb if rgb is not None else self.part_color(guide, entry[3], margin)

    def part_color(self, guide, filled, margin):
        """未完成セルの確定した画素だけを使い、部位単位で引き継ぐ。"""
        from . import auto_paint as ap
        confirmed = getattr(guide, "confirmed", None)
        if confirmed is None or guide.initial is None or filled.target is not None:
            return None
        size = int(filled.local.sum())
        large = size >= 8192
        shift, score = self.alignment.shift(self.target, guide.initial, filled.bounds, margin)
        if score < (0.4 if large else 0.6):
            return None
        sampled = native.sample_region(guide, filled, shift, ignore=2 | 4, require=8)
        if sampled is not None:
            _inside, known, value, count, other, _bad, _any, _y, _x = sampled
            if (known < size * (0.8 if large else 0.65) or value is None or not known
                    or count / known < (0.98 if large else 0.95) or not ap._paintable(value)):
                return None
            if not large and (not size / ap.MAX_AREA_RATIO <= other <= size * ap.MAX_AREA_RATIO
                              or count < other * FACE_REVERSE_SHARE):
                return None
            return reference_check._to_rgb(value)
        sy, sx, inside = ap._shifted(self.target, filled.local, filled.bounds, shift)
        known = inside & confirmed[sy, sx] & ~guide.initial.boundary[sy, sx]
        known &= ~guide.open[sy, sx] & ~guide.undecided[sy, sx]
        if known.sum() < size * (0.8 if large else 0.65):
            return None
        value, share = ap._majority(guide.values[sy[known], sx[known]])
        if value is None or share < (0.98 if large else 0.95) or not ap._paintable(value):
            return None
        if large:
            return reference_check._to_rgb(value)
        matched = known & (guide.values[sy, sx] == value)
        labels = np.unique(guide.regions.labels[sy[matched], sx[matched]])
        other = sum(int(guide.regions.sizes[label]) for label in labels)
        if not size / ap.MAX_AREA_RATIO <= other <= size * ap.MAX_AREA_RATIO:
            return None
        if matched.sum() < other * FACE_REVERSE_SHARE:
            return None
        return reference_check._to_rgb(value)

    def _refine_face_color(self, guide, filled, margin):
        """局所的な伸縮で線の位置合わせがずれた小面を、完成領域でも照合する。"""
        from . import auto_paint as ap
        if guide.initial is None or filled.target is not None:
            return None
        size = int(filled.local.sum())
        if not 8 <= size <= 4096:
            return None
        shift, score = self.alignment.shift(self.target, guide.initial, filled.bounds, margin)
        if score < ap.MIN_LINE_OVERLAP:
            return None
        x0, y0, x1, y1 = filled.bounds
        height, width = guide.values.shape
        radius = FACE_REFINE_RADIUS
        ax0, ay0 = max(0, x0 + shift[1] - radius), max(0, y0 + shift[0] - radius)
        ax1, ay1 = min(width, x1 + shift[1] + radius), min(height, y1 + shift[0] + radius)
        labels = guide.regions.labels[ay0:ay1, ax0:ax1]
        if not np.any(guide.regions.sizes[labels] <= size * ap.MAX_AREA_RATIO):
            return None  # どのずれでも面積比を満たす仕上げ領域がない。
        refined = native.refine_face(
            guide, filled, shift, (ax0, ay0, ax1, ay1), FACE_REFINE_RADIUS,
            (FACE_SAMPLE_SHARE, ap.HINT_SHARE, ap.MAX_AREA_RATIO, FACE_REVERSE_SHARE),
        )
        if refined is not None:
            answers = {int(value): fit for value, fit in refined if ap._paintable(value)}
            return self._refined_answer(answers)
        answers = {}
        for dy in range(-FACE_REFINE_RADIUS, FACE_REFINE_RADIUS + 1):
            for dx in range(-FACE_REFINE_RADIUS, FACE_REFINE_RADIUS + 1):
                sy, sx, inside = ap._shifted(self.target, filled.local, filled.bounds,
                                            (shift[0] + dy, shift[1] + dx))
                usable = inside & ~guide.initial.boundary[sy, sx]
                if usable.sum() < size * FACE_SAMPLE_SHARE:
                    continue
                value, share = ap._majority(guide.values[sy[usable], sx[usable]])
                if value is None or share < ap.HINT_SHARE or not ap._paintable(value):
                    continue
                matched = usable & (guide.values[sy, sx] == value)
                if (guide.open[sy[matched], sx[matched]].any()
                        or guide.undecided[sy[matched], sx[matched]].any()):
                    continue
                labels = np.unique(guide.regions.labels[sy[matched], sx[matched]])
                other = sum(int(guide.regions.sizes[label]) for label in labels)
                if not size / ap.MAX_AREA_RATIO <= other <= size * ap.MAX_AREA_RATIO:
                    continue
                if matched.sum() < other * FACE_REVERSE_SHARE:
                    continue
                fit = 2 * int(matched.sum()) / (size + other)
                answers[value] = max(answers.get(value, 0), fit)
        return self._refined_answer(answers)

    @staticmethod
    def _refined_answer(answers):
        ranked = sorted(answers, key=lambda value: answers[value], reverse=True)
        if not ranked or (len(ranked) > 1 and answers[ranked[0]] - answers[ranked[1]] < 0.05):
            return None
        return reference_check._to_rgb(ranked[0])

    def _sample_face_color(self, guide, filled, margin):
        """初期線で面が分断・開放されても、仕上げの同色部分との対応を確かめる。"""
        from . import auto_paint as ap
        if guide.initial is None or filled.target is not None:
            return None
        shift, score = self.alignment.shift(self.target, guide.initial, filled.bounds, margin)
        size = int(filled.local.sum())
        large = size >= 8192
        if score < (0.3 if large else ap.MIN_LINE_OVERLAP):
            return None
        sampled = native.sample_region(guide, filled, shift, ignore=2)
        if sampled is not None:
            _inside, usable, value, count, other, bad, _any, _y, _x = sampled
            if (usable < size * FACE_SAMPLE_SHARE or not usable or value is None
                    or count / usable < ap.HINT_SHARE or not ap._paintable(value) or bad):
                return None
            if large and count / usable >= 0.98 and count >= size * 0.9:
                return reference_check._to_rgb(value)
            if (not size / ap.MAX_AREA_RATIO <= other <= size * ap.MAX_AREA_RATIO
                    or count < other * FACE_REVERSE_SHARE):
                return None
            return reference_check._to_rgb(value)
        sy, sx, inside = ap._shifted(self.target, filled.local, filled.bounds, shift)
        usable = inside & ~guide.initial.boundary[sy, sx]
        if usable.sum() < size * FACE_SAMPLE_SHARE:
            return None
        values = guide.values[sy[usable], sx[usable]]
        value, share = ap._majority(values)
        if value is None or share < ap.HINT_SHARE or not ap._paintable(value):
            return None
        matched = usable & (guide.values[sy, sx] == value)
        if (guide.open[sy[matched], sx[matched]].any()
                or guide.undecided[sy[matched], sx[matched]].any()):
            return None
        # しわやTP線で分割された大面は、同じ仕上げ面の一部分に対応してよい。
        if large and share >= 0.98 and matched.sum() >= size * 0.9:
            return reference_check._to_rgb(value)
        labels = np.unique(guide.regions.labels[sy[matched], sx[matched]])
        other_size = sum(int(guide.regions.sizes[label]) for label in labels)
        if not size / ap.MAX_AREA_RATIO <= other_size <= size * ap.MAX_AREA_RATIO:
            return None
        if matched.sum() < other_size * FACE_REVERSE_SHARE:
            return None
        return reference_check._to_rgb(value)

    def _match(self, geometry, filled, margin, sessions, *, original_lines=False):
        from . import auto_paint as ap
        shift, overlap = self.alignment.shift(self.target, geometry, filled.bounds, margin)
        if overlap < ap.MIN_LINE_OVERLAP:
            return None
        is_trace = filled.target is not None and ap._is_primary(filled.target)
        if not is_trace:
            sampled = native.sample_region(geometry, filled, shift, ignore=1 if original_lines else 0,
                                           by_label=True)
            if sampled is not None:
                inside, usable, index, count, other_size, _bad, _any, sy, sx = sampled
                size = int(filled.local.sum())
                if (inside < size * ap.HINT_SHARE or usable < size * FACE_SAMPLE_SHARE
                        or not usable or index is None
                        or count / usable < (FACE_MATCH_SHARE if original_lines else ap.HINT_SHARE)
                        or not size / ap.MAX_AREA_RATIO <= other_size <= size * ap.MAX_AREA_RATIO
                        or count < other_size * (FACE_REVERSE_SHARE if original_lines else ap.FILL_SHARE)):
                    return None
                reference = self._reference(geometry, sx, sy, sessions)
                return reference if reference.ok else None
        local = filled.local
        sy, sx, inside = ap._shifted(self.target, local, filled.bounds, shift)
        size = int(local.sum())
        if inside.sum() < size * ap.HINT_SHARE:
            return None
        if is_trace and original_lines:
            return self._match_trace(geometry, filled, sy, sx, inside, sessions)
        # 形の変化を許容するのは、元の線画で対応を確かめる場合だけ。
        # 仕上げ色の境目を領域として使う代替経路まで緩めると、誤塗りを増幅する。
        usable = inside & ~geometry.boundary[sy, sx] if original_lines else inside
        if usable.sum() < size * FACE_SAMPLE_SHARE:
            return None
        labels = geometry.regions.labels[sy[usable], sx[usable]]
        index, share = ap._majority(labels)
        required = FACE_MATCH_SHARE if original_lines else ap.HINT_SHARE
        if index is None or share < required:
            return None
        other_size = int(geometry.regions.sizes[index])
        if not size / ap.MAX_AREA_RATIO <= other_size <= size * ap.MAX_AREA_RATIO:
            return None
        matched = usable & (geometry.regions.labels[sy, sx] == index)
        reverse = FACE_REVERSE_SHARE if original_lines else ap.FILL_SHARE
        if matched.sum() < other_size * reverse:
            return None
        positions = np.flatnonzero(matched)
        at = int(positions[len(positions) // 2])
        reference = self._reference(geometry, int(sx[at]), int(sy[at]), sessions)
        return reference if reference.ok else None

    def _match_trace(self, geometry, filled, sy, sx, inside, sessions):
        """同じTP色の線を近傍で照合する。塗る範囲自体はバケツから変えない。"""
        from . import auto_paint as ap
        height, width = geometry.values.shape
        code = reference_check._pack(filled.target)
        selected = np.full(sy.shape, -1, dtype=np.int32)
        selected_y, selected_x = sy.copy(), sx.copy()
        offsets = sorted(
            ((dy, dx) for dy in range(-TRACE_RADIUS, TRACE_RADIUS + 1)
             for dx in range(-TRACE_RADIUS, TRACE_RADIUS + 1)),
            key=lambda item: (item[0] ** 2 + item[1] ** 2, item),
        )
        for dy, dx in offsets:
            y, x = sy + dy, sx + dx
            valid = inside & (selected < 0) & (y >= 0) & (y < height) & (x >= 0) & (x < width)
            at = np.flatnonzero(valid)
            at = at[geometry.values[y[at], x[at]] == code]
            selected[at] = geometry.regions.labels[y[at], x[at]]
            selected_y[at], selected_x[at] = y[at], x[at]
        matched = selected >= 0
        if matched.sum() < len(sy) * TRACE_MATCH_SHARE:
            return None
        index, share = ap._majority(selected[matched])
        if index is None or share < ap.HINT_SHARE:
            return None
        points = np.flatnonzero(selected == index)
        other_size = int(geometry.regions.sizes[index])
        size = int(filled.local.sum())
        if not size / ap.MAX_AREA_RATIO <= other_size <= size * ap.MAX_AREA_RATIO:
            return None
        unique = np.unique(selected_y[points] * width + selected_x[points]).size
        if unique < other_size * TRACE_REVERSE_SHARE:
            return None
        at = int(points[len(points) // 2])
        reference = self._reference(geometry, int(selected_x[at]), int(selected_y[at]), sessions)
        return reference if reference.ok else None


def paint_cell(pixels, guides, options, *, target, margin, work=None, trace_guides=(),
               trace_palette=None, part_guides=(), trace_face_rules=None, trace_contexts=None,
               tracked_color=None):
    from . import auto_paint as ap, auto_trace
    if work is None:
        work = BucketWork(target, options)
    result = ap.CellPaint()
    entries = work.collect()
    work.alignment.prefetch(target, guides, [entry[3].bounds for entry in entries], margin)
    sessions = {}
    def record(entry, choices, *, trace=False):
        x, y, value, filled = entry
        result.checked += 1
        choices = [rgb for rgb in choices if rgb is not None]
        if not choices or any(rgb != choices[0] for rgb in choices[1:]):
            local = filled.local
            result.unsure += 1
            if trace:
                result.undecided.append((filled.bounds, local))
                kind = reference_check.KIND_UNDECIDED_LINE
            else:
                result.unpainted.append((filled.bounds, local))
                kind = reference_check.KIND_UNPAINTED
            if local.sum() >= ap.REPORT_MIN_PIXELS:
                result.notes.append(reference_check.Finding(
                    kind, reference_check._to_rgb(value), int(local.sum()),
                    bounds=filled.bounds, mask=local,
                ))
            return
        click = BucketClick(x, y, choices[0])
        result.clicks.append((click.x, click.y, click.rgb))
        result.paints.extend(_paints(filled, click.rgb))
        if trace:
            result.line_pixels += int(filled.local.sum())
        else:
            result.filled += 1

    traces = []
    pending_faces = []
    for ordinal, entry in enumerate(entries):
        rgb = entry[3].target
        if rgb is not None and ap._is_primary(rgb):
            traces.append((ordinal, entry))
        else:
            choices = [work.choose_color(guide, ordinal, entry, margin, sessions)
                       for guide in guides]
            if not any(rgb is not None for rgb in choices):
                choices = [work.part_color(guide, entry[3], margin) for guide in part_guides]
            if not any(rgb is not None for rgb in choices) and tracked_color is not None:
                choices = [tracked_color(entry[3])]
            if not any(rgb is not None for rgb in choices):
                pending_faces.append(entry)
            else:
                record(entry, choices)
    # 面の色を先に確定し、その大きな面の色は線の候補から除く。
    painted = target.values.copy()
    for rgb, (x0, y0, x1, y1), mask in result.paints:
        painted[y0:y1, x0:x1][mask] = reference_check._pack(rgb)
    palette_guides = list(dict.fromkeys(trace_guides or guides))
    palettes = (trace_contexts if trace_contexts is not None else
                [auto_trace.palette(guide) for guide in palette_guides] if traces else [])
    if trace_palette is None:
        trace_palette = auto_trace.finish_palette(trace_guides or guides)
    if trace_face_rules is None:
        trace_face_rules = auto_trace.enclosed_face_rules(trace_guides or guides, trace_palette)
    faces = {}
    face_samples = {}
    line_finishes = {}
    for ordinal, entry in traces:
        filled = entry[3]
        hosts = auto_trace.large_face_colors(target, filled, painted, cache=faces)
        allowed = auto_trace.candidates(filled.target, hosts, palettes)
        direct = []
        all_candidates = auto_trace.candidates(filled.target, hosts, palettes, minimum=1)
        finish_colors = {value: count for value, count in trace_palette.get(filled.target, {}).items()
                         if value not in hosts}
        if len(allowed) != 1 or len(all_candidates) != 1:
            direct = [work.choose_color(guide, ordinal, entry, margin, sessions) for guide in guides]
            for guide in guides:
                direct.append(auto_trace.local_finish(
                    target, guide, filled, margin, work.alignment, finish_colors or allowed,
                ))
        direct.append(auto_trace.small_face_finish(
            target, filled, painted, finish_colors, cache=face_samples,
        ))
        rgb = auto_trace.choose(filled.target, hosts, palettes, direct, finishes=trace_palette)
        record(entry, [rgb], trace=True)
        if rgb is not None:
            x0, y0, x1, y1 = filled.bounds
            for label in np.unique(target.regions.labels[y0:y1, x0:x1][filled.local]).tolist():
                line_finishes[label] = reference_check._pack(rgb)
    for entry in pending_faces:
        rgb = auto_trace.enclosed_face_color(target, entry[3], line_finishes, trace_face_rules)
        record(entry, [rgb])
    return result
