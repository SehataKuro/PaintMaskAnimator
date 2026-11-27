"""見本のセルや前後のコマと比べて、塗り残し・塗り間違いの候補を探す（UI 非依存）。

1枚目だけ仕上げたカットを残りのコマへ塗り進めると、次のような取りこぼしが出る。

- 見本で使っている色が、あるコマにだけ無い（星の中心を塗り忘れた、など）。
- 見本に無い色が、あるコマに残っている（色トレス線を塗り色に変え忘れた、など）。
- ある部分の色が、前後のコマの同じ部分と違う（頬を髪の色で塗った、など）。

前の2つはセルの色の集計を見本と比べるだけで分かる。最後の1つは、塗った部分ごとに
前後のコマで同じ部分（線画のずれを推定して動かした位置）の色を数え、前後の両方が
別の同じ色ならそれを候補にする。前後で色が揃わない所（動きが大きい、形が変わった）は
判断できないので挙げない。

numpy 配列だけを扱う純粋な関数にしてある（``docs/design-principles.md``
「将来は Rust へ置き換えていく」）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import fill_tracking
from .numpy_morphology import erode_numpy

try:
    from . import native
except ImportError:
    native = None

Rgb = Tuple[int, int, int]
Bounds = Tuple[int, int, int, int]

#: 見本で使っている色が、このセルに無い。
KIND_MISSING = "missing"
#: 見本に無い色が、このセルに残っている。
KIND_EXTRA = "extra"
#: この部分の色が、前後のコマの同じ部分と違う。
KIND_MISMATCH = "mismatch"
#: 自動塗りで、場所や色を決めきれず塗らなかった部分（透明のまま）。
KIND_UNPAINTED = "unpainted"
#: 自動塗りで、前後のセルと違う色で塗ってあった部分（塗り替えてはいない）。
KIND_SUSPECT = "suspect"
#: 自動塗りで、手本に無い形（開いた口など）なので塗らなかった部分。
KIND_NEW_PART = "new_part"
#: 自動塗りで、残すか塗るかを決める手がかりの無かった色トレス線。
KIND_UNDECIDED_LINE = "undecided_line"

#: 透明を表す値（:func:`fill_tracking._labels` と同じ）。
_TRANSPARENT = 0
#: 色の集計で、これより少ない画素しか無い色は数えない（見本側・セル側とも）。
MIN_COLOR_PIXELS = 5
#: 前後のコマと比べる部分の面積（px）の範囲。小さすぎる部分はずれの推定が
#: 当てにならず、大きすぎる部分（髪全体など）は形が大きく変わる。
MIN_REGION_AREA = 20
MAX_REGION_AREA = 40000
#: 前後のコマで、動かした範囲のうち一番多い色が占める割合の下限。
MIN_AGREEMENT = 0.6
#: この画素数だけ縮めると消える細い部分（色トレス線や斜線）は比べない。
#: 少し動くだけで前後のコマと重ならなくなり、候補として当てにならない。
THIN_RADIUS = 2
#: 256×256 以下では、ランの受け渡しと領域統計の配列化より NumPy 集計が速い。
_NATIVE_REGION_STATS_MIN_PIXELS = 65537


@dataclass(frozen=True)
class Finding:
    """見つかった候補1つ。"""

    kind: str
    #: 問題の色（MISSING は見本の色、EXTRA は残っている色、MISMATCH はこの部分の色。
    #: 透明は ``None``）。
    rgb: Optional[Rgb]
    #: 画素数（MISSING は見本での画素数）。
    pixels: int
    #: 場所 ``(x0, y0, x1, y1)``（x1, y1 は排他）。MISSING には無い。
    bounds: Optional[Bounds] = None
    #: MISMATCH で、前後のコマのその部分の色（透明は ``None``）。
    expected: Optional[Rgb] = None
    #: MISMATCH で、外接矩形の中のこの部分のマスク。
    mask: Optional[np.ndarray] = None


def _to_rgb(value) -> Optional[Rgb]:
    value = int(value)
    if value == _TRANSPARENT:
        return None
    # _labels は RGBA8888 をリトルエンディアンの uint32 として見た値。
    return (value & 255, (value >> 8) & 255, (value >> 16) & 255)


def _is_line(rgb) -> bool:
    return rgb is not None and max(rgb) <= fill_tracking.LINE_MAX


def color_counts(pixels) -> Dict[Optional[Rgb], int]:
    """不透明な色ごとの画素数。白（消しゴム）と透明は数えない。"""
    stats = native.reference_color_stats(pixels) if native is not None else None
    if stats is None:
        return color_counts_numpy(pixels)
    return {_to_rgb(value): count for value, count, _ in stats}


def color_counts_numpy(pixels):
    """透明と白を除き、色ごとの画素数を NumPy で数える。"""
    labels = fill_tracking._labels(pixels)
    values, counts = np.unique(labels, return_counts=True)
    result = {}
    for value, count in zip(values.tolist(), counts.tolist()):
        rgb = _to_rgb(value)
        if rgb is None or rgb == (255, 255, 255):
            continue
        result[rgb] = int(count)
    return result


def compare_colors(reference_pixels, pixels, min_pixels=MIN_COLOR_PIXELS) -> List[Finding]:
    """見本 ``reference_pixels`` とこのセル ``pixels`` の使用色の違い。"""
    reference = color_counts(reference_pixels)
    stats = native.reference_color_stats(pixels) if native is not None else None
    if stats is None:
        return _compare_colors(reference, color_counts_numpy(pixels),
                               lambda: fill_tracking._labels(pixels), min_pixels=min_pixels)
    counts = {_to_rgb(value): count for value, count, _ in stats}
    bounds = {value: box for value, _, box in stats}
    return _compare_colors(reference, counts, bounds=bounds, min_pixels=min_pixels)


def compare_colors_numpy(reference_pixels, pixels, min_pixels=MIN_COLOR_PIXELS):
    """見本とこのセルの使用色の違いを NumPy で求める。"""
    return _compare_colors(color_counts_numpy(reference_pixels), color_counts_numpy(pixels),
                           lambda: fill_tracking._labels(pixels), min_pixels=min_pixels)


def _compare_colors(reference, counts, pack_pixels=None, *, bounds=None, min_pixels=MIN_COLOR_PIXELS):
    """色ごとの画素数を比べ、見本にしかない色とこのセルにしかない色を返す。"""
    findings = []
    for rgb, count in sorted(reference.items(), key=lambda item: -item[1]):
        if count >= min_pixels and counts.get(rgb, 0) == 0:
            findings.append(Finding(KIND_MISSING, rgb, count))
    extra = [
        (rgb, count) for rgb, count in counts.items()
        if count >= min_pixels and rgb not in reference
    ]
    if extra:
        if bounds is None:
            # bounds が無いときは、呼び出し側が必ず pack_pixels を渡す。
            assert pack_pixels is not None
            packed = pack_pixels()
        for rgb, count in sorted(extra, key=lambda item: -item[1]):
            value = _pack(rgb)
            box = _bounds(packed == value) if bounds is None else bounds.get(value)
            findings.append(Finding(KIND_EXTRA, rgb, count, bounds=box))
    return findings


def _pack(rgb) -> int:
    return rgb[0] | (rgb[1] << 8) | (rgb[2] << 16) | (255 << 24)


def color_mask(pixels, rgb):
    """``pixels`` のうち色 ``rgb`` の画素（``None`` なら透明）。"""
    packed = fill_tracking._labels(pixels)
    return packed == (_TRANSPARENT if rgb is None else _pack(rgb))


def _bounds(mask) -> Optional[Bounds]:
    return fill_tracking.mask_bounds(mask)


def label_regions(values):
    """同じ値が4近傍でつながった部分に番号を振る。``(番号の配列, 部分の数)``。"""
    labels, count, _runs = _label_runs(values)
    return labels, count


def _label_runs(values, *, use_native=True):
    """:func:`label_regions` と、ランごとの ``(行, 始まり, 終わり, 番号)``。

    行ごとに同じ値の連続区間（ラン）へ分け、上下の行で重なって値が同じランを
    つなぐ。ランは各行を左から隙間なく覆うので、番号の配列はランの番号を長さ分
    並べるだけで作れる。大きさや外接矩形も、画素ごとではなくランごとに数えれば
    足りる（ランは画素よりずっと少ない）。
    """
    values = np.asarray(values)
    height, width = values.shape
    if height == 0 or width == 0:
        empty = np.zeros(0, dtype=np.int64)
        return np.zeros((height, width), dtype=np.int32), 0, (empty, empty, empty, empty)
    use_native = use_native and native is not None
    labeled = native.label_runs(values) if use_native and native is not None else None
    if labeled is not None:
        return labeled
    change = np.ones((height, width), dtype=bool)
    change[:, 1:] = values[:, 1:] != values[:, :-1]
    run_y, run_x = np.nonzero(change)
    count = run_x.size
    run_end = np.empty(count, dtype=np.int64)
    run_end[:-1] = run_x[1:]
    last_in_row = np.ones(count, dtype=bool)
    last_in_row[:-1] = run_y[1:] != run_y[:-1]
    run_end[last_in_row] = width
    run_value = values[run_y, run_x]
    row_start = np.searchsorted(run_y, np.arange(height + 1))

    connected = (native.connect_runs(run_x, run_end, run_value, row_start)
                 if use_native and native is not None and values.dtype.kind in "biu" else None)
    if connected is not None:
        run_label, regions = connected
        labels = np.repeat(run_label, run_end - run_x).reshape(height, width)
        return labels, regions, (run_y, run_x, run_end, run_label)

    parent = list(range(count))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    starts, ends, kinds = run_x.tolist(), run_end.tolist(), run_value.tolist()
    rows = row_start.tolist()
    for y in range(height - 1):
        upper, upper_end = rows[y], rows[y + 1]
        lower, lower_end = rows[y + 1], rows[y + 2]
        while upper < upper_end and lower < lower_end:
            if (
                kinds[upper] == kinds[lower]
                and starts[upper] < ends[lower]
                and starts[lower] < ends[upper]
            ):
                root_a, root_b = find(upper), find(lower)
                if root_a != root_b:
                    parent[max(root_a, root_b)] = min(root_a, root_b)
            if ends[upper] < ends[lower]:
                upper += 1
            else:
                lower += 1
    roots = np.fromiter((find(index) for index in range(count)), dtype=np.int64, count=count)
    _unique, compact = np.unique(roots, return_inverse=True)
    run_label = compact.astype(np.int32).reshape(count)
    labels = np.repeat(run_label, run_end - run_x).reshape(height, width)
    return labels, int(_unique.size), (run_y, run_x, run_end, run_label)


#: 前後のコマで対応する部分の面積比の上限（どちら向きにも）。
MAX_AREA_RATIO = 2.0
#: 前後のコマで対応する部分の、まわりの色の並びの似ている度合いの下限
#: （:func:`fill_tracking.context_similarity`）。
MIN_CONTEXT = 0.6


class CellRegions:
    """1セル分の、色ごとにつながった部分の情報。前後のコマと比べるときに使い回す。"""

    def __init__(self, pixels, values=None, *, use_native=True):
        """:param values: 部分分けに使う色の値。省略時は ``pixels`` から作る。"""
        self.pixels = pixels
        self.values = fill_tracking._labels(pixels) if values is None else values
        use_native = use_native and native is not None
        lines = (native.reference_line_art(pixels, fill_tracking.LINE_MAX)
                 if use_native and native is not None else None)
        self.lines = fill_tracking.line_art(pixels) if lines is None else lines
        self.height, self.width = self.values.shape
        self.labels, count, runs = _label_runs(self.values, use_native=use_native)
        # 大きさと外接矩形は、画素ごとではなくランごとに数える。
        run_y, run_x, _, run_label = runs
        self._region_values = np.empty(count, dtype=self.values.dtype)
        self._region_values[run_label] = self.values[run_y, run_x]
        stats = (native.reference_region_stats(runs, count, self.width, self.height)
                 if use_native and native is not None
                 and self.values.size >= _NATIVE_REGION_STATS_MIN_PIXELS else None)
        if stats is not None:
            self.sizes = np.array([size for size, _, _ in stats], dtype=np.int64)
            bounds = np.array([box for _, box, _ in stats], dtype=np.int64)
            self.x0, self.y0, self.x1, self.y1 = bounds.T.copy()
            self.x1 -= 1
            self.y1 -= 1
            self.on_edge = {index for index, (_, _, edge) in enumerate(stats) if edge}
        else:
            self._summarize_numpy(count, runs)

    def _summarize_numpy(self, count, runs):
        """ランから領域の画素数、外接矩形、画像の端に接する領域を NumPy で求める。"""
        run_y, run_x, run_end, run_label = runs
        self.sizes = np.bincount(
            run_label, weights=run_end - run_x, minlength=count,
        ).astype(np.int64)
        self.y0 = np.full(count, self.height)
        self.y1 = np.zeros(count, dtype=np.int64)
        self.x0 = np.full(count, self.width)
        self.x1 = np.zeros(count, dtype=np.int64)
        np.minimum.at(self.y0, run_label, run_y)
        np.maximum.at(self.y1, run_label, run_y)
        np.minimum.at(self.x0, run_label, run_x)
        np.maximum.at(self.x1, run_label, run_end - 1)
        self.on_edge = set(np.unique(np.concatenate([
            self.labels[0], self.labels[-1], self.labels[:, 0], self.labels[:, -1]
        ])).tolist())

    def bounds(self, index) -> Bounds:
        return (
            int(self.x0[index]), int(self.y0[index]),
            int(self.x1[index]) + 1, int(self.y1[index]) + 1,
        )

    def local_mask(self, index):
        x0, y0, x1, y1 = self.bounds(index)
        return self.labels[y0:y1, x0:x1] == index

    def mask(self, index):
        x0, y0, x1, y1 = self.bounds(index)
        out = np.zeros((self.height, self.width), dtype=bool)
        out[y0:y1, x0:x1] = self.labels[y0:y1, x0:x1] == index
        return out

    def value(self, index) -> int:
        return int(self._region_values[index])

    def region_under(self, mask):
        """``mask`` の下（主線を除く）で一番多い部分の番号と、その割合。"""
        sample = self.labels[mask & ~self.lines]
        if not sample.size:
            return None, 0.0
        indices, counts = np.unique(sample, return_counts=True)
        top = int(np.argmax(counts))
        return int(indices[top]), float(counts[top]) / sample.size


def _counterpart(current, region, neighbour, min_agreement):
    """``neighbour`` のコマで ``region`` に当たる部分の番号。見つからなければ None。

    線画のずれを推定して動かした位置で一番多い部分を選び、面積が近く、まわりの
    色の並びもよく似ているときだけ「同じ部分」とみなす。
    """
    if native is None:
        shifts = fill_tracking.estimate_shifts_numpy(current.lines, neighbour.lines, region, count=1)
        shift = shifts[0] if shifts else (0, 0)
    else:
        shift = fill_tracking.estimate_shift(current.lines, neighbour.lines, region)
    moved = fill_tracking.shift_mask(region, *shift)
    index, share = neighbour.region_under(moved)
    if index is None or share < min_agreement:
        return None
    area = int(np.count_nonzero(region))
    ratio = int(neighbour.sizes[index]) / max(1, area)
    if not 1.0 / MAX_AREA_RATIO <= ratio <= MAX_AREA_RATIO:
        return None
    compare_context = (fill_tracking.context_similarity_numpy if native is None
                       else fill_tracking.context_similarity)
    similarity = compare_context(
        current.pixels, region, neighbour.pixels, neighbour.mask(index)
    )
    return index if similarity >= MIN_CONTEXT else None


def _counterpart_local(current, local, bounds, neighbour, sample, min_agreement):
    """_counterpart と同じ結果を外接矩形の範囲だけで求める。"""
    # native の sampler があるときだけ呼ばれる。
    assert native is not None
    x0, y0, x1, y1 = bounds
    margin = max(64, 3 * max(x1 - x0, y1 - y0))
    left, top = max(0, x0 - margin), max(0, y0 - margin)
    right, bottom = min(current.width, x1 + margin), min(current.height, y1 + margin)
    window = np.s_[top:bottom, left:right]
    region = np.zeros((bottom - top, right - left), dtype=bool)
    region[y0 - top:y1 - top, x0 - left:x1 - left] = local
    shifts = native.estimate_shifts(current.lines[window], neighbour.lines[window], region, 1, margin)
    dy, dx = shifts[0] if shifts else (0, 0)
    left, top = max(0, x0 + dx), max(0, y0 + dy)
    right, bottom = min(current.width, x1 + dx), min(current.height, y1 + dy)
    if left >= right or top >= bottom:
        return None
    moved = local[top - y0 - dy:bottom - y0 - dy, left - x0 - dx:right - x0 - dx]
    index, share = sample(moved, (left, top, right, bottom))
    if index is None or share < min_agreement:
        return None
    area = int(np.count_nonzero(local))
    ratio = int(neighbour.sizes[index]) / max(1, area)
    if not 1.0 / MAX_AREA_RATIO <= ratio <= MAX_AREA_RATIO:
        return None
    similarity = native.reference_context_similarity(
        current.pixels, local, bounds, neighbour.pixels, neighbour.local_mask(index),
        neighbour.bounds(index), fill_tracking.CONTEXT_RADIUS,
    )
    return index if similarity >= MIN_CONTEXT else None


def find_mismatches(
    previous, current, following,
    min_area=MIN_REGION_AREA, max_area=MAX_REGION_AREA,
    min_agreement=MIN_AGREEMENT,
    *, use_native=True,
) -> List[Finding]:
    """``current`` の部分のうち、前後のコマの同じ部分と色が違うものを挙げる。

    引数はそれぞれのコマの :class:`CellRegions`。前後の両方で同じ部分が見つかり、
    その色が揃っていて、このコマの色と違うものだけを返す。
    """
    if not (
        previous.values.shape == current.values.shape == following.values.shape
    ):
        return []
    findings = []
    candidates = np.nonzero(
        (current.sizes >= min_area) & (current.sizes <= max_area)
    )[0]
    neighbours = (previous, following)
    samplers = None
    erode = erode_numpy if native is None else native.erode
    for index in candidates.tolist():
        value = current.value(index)
        rgb = _to_rgb(value)
        if _is_line(rgb) or rgb == (255, 255, 255):
            continue
        if rgb is None and index in current.on_edge:
            # キャンバスの端につながる透明は、絵の外（背景）。
            continue
        local = current.local_mask(index)
        if not erode(local, THIN_RADIUS).any():
            continue
        if samplers is None:
            samplers = (tuple(native.reference_region_sampler(n.labels, n.lines) for n in neighbours)
                        if use_native and native is not None else (None, None))
        bounds = current.bounds(index)
        region = None if all(samplers) else current.mask(index)
        # 同じ位置でもう同じ色なら、ずれを推定するまでもない。
        consistent = False
        for neighbour, sample in zip(neighbours, samplers):
            same, share = (neighbour.region_under(region) if sample is None
                           else sample(local, bounds))
            if same is not None and neighbour.value(same) == value and share >= min_agreement:
                consistent = True
                break
        if consistent:
            continue
        expected = []
        for neighbour, sample in zip(neighbours, samplers):
            if sample is None:
                other = _counterpart(current, region, neighbour, min_agreement)
            else:
                other = _counterpart_local(current, local, bounds, neighbour, sample, min_agreement)
            if other is None:
                break
            expected.append(neighbour.value(other))
        if len(expected) != 2 or expected[0] != expected[1] or expected[0] == value:
            continue
        expected_rgb = _to_rgb(expected[0])
        if _is_line(expected_rgb):
            continue
        findings.append(Finding(
            KIND_MISMATCH, rgb, int(current.sizes[index]),
            bounds=bounds, expected=expected_rgb, mask=local,
        ))
    findings.sort(key=lambda finding: -finding.pixels)
    return findings


def find_mismatches_numpy(
    previous, current, following,
    min_area=MIN_REGION_AREA, max_area=MAX_REGION_AREA, min_agreement=MIN_AGREEMENT,
):
    """前後のコマと比べて色が異なる部分を NumPy で求める。"""
    return find_mismatches(
        previous, current, following, min_area, max_area, min_agreement, use_native=False,
    )


__all__ = [
    "Finding",
    "KIND_MISSING",
    "KIND_EXTRA",
    "KIND_MISMATCH",
    "color_counts",
    "color_mask",
    "compare_colors",
    "label_regions",
    "CellRegions",
    "find_mismatches",
]
