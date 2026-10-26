"""見本のセルの塗りを、同じレイヤーのほかのセルへまとめて写す。

塗り終えたセル（見本）の塗ってある部分を1つずつ取り出し、前後のセルへ
串刺し塗りと同じ追跡（:func:`bucket_fill.track_fill_region`）で追いかけて、
見本と同じ色で塗る範囲を決める。画面も Qt も知らない純粋な処理で、実際に
書き込むのは呼び出し側（``main_window_copy_paint``）。

* 自信を持って見つけられた部分だけを塗る。見つからなかったセルは飛ばし、
  その先のセルは最後に見つかったセルから追いかけ続ける。
* もう見本の色で塗ってある部分は塗らない（追跡の手がかりとしては使う）。
* ほかのセルにもう同じくらいある色（下塗りの肌や髪など）は写さない。写すのは
  見本で新しく塗った色だけなので、追いかける部分がぐっと減る。
* 原画の印（範囲線・斜線など、ほかのセルにはあって見本では塗りつぶされた色）は
  塗る範囲の一部とみなす。外側に沿った細い印はその部分の色で、内側に囲まれた
  印は見本の同じ場所の色（斜線の仕上げ色など）で塗る。
* 追跡は部分のまわりの窓の中だけで行う。キャンバス全体で塗りの範囲を
  求めるより桁違いに速い。窓の縁まで届いた結果だけ、全体で求め直す。
"""
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from . import bucket_fill, fill_tracking, reference_check

Rgb = Tuple[int, int, int]
Bounds = Tuple[int, int, int, int]

#: これより小さい部分は写さない（線のふちに残った数画素など）。小さな点は10画素ほど。
MIN_PART_PIXELS = 5
#: まだ塗っていない所（透明）や、もう見本の色の所は、重なりがこれ以上なら塗る。
CONFIDENT_SCORE = 0.5
#: ほかの色の上（下塗りの肌に頬を塗る、など）は、重なりがこれ以上のときだけ塗る。
OVERPAINT_SCORE = 0.6
#: ほかの色の上へ塗るときは、面積がこの倍率の範囲に収まっていること。
OVERPAINT_AREA_RATIO = 2.0
#: 窓は部分の外接矩形を、この画素数か部分の大きさの2倍の広い方だけ広げる。
WINDOW_MARGIN = 96
#: ほかのすべてのセルに、見本のこの割合以上の画素がある色は、もう塗ってあるとみなす。
PRESENT_SHARE = 0.5
#: 新しく塗る範囲の半分以上を、ほかの部分がもう塗ることにしていたら、取り違えとみなす。
MAX_CLAIMED_SHARE = 0.5
#: 続けてこの数のセルで見つけられなかったら、図形が画面から消えたとみなして、
#: その向きの残りのセルは追わない（離れたセルで似た別の場所を拾わないため）。
MAX_MISSES_IN_A_ROW = 2
#: 1つ前のセルで見つけられなかったあと、もう一度拾い直すのに要る重なり。
REACQUIRE_SCORE = 0.6
#: 塗る範囲の外側に沿った印（範囲線）を、この太さまで塗る範囲に含める。
MARK_BAND = 3


@dataclass(frozen=True)
class Part:
    """見本の塗ってある部分1つ。"""

    rgb: Rgb
    mask: np.ndarray


@dataclass
class CopyPlan:
    """塗り写しの計画。セルの番号は呼び出し側が渡した並びの位置。"""

    #: セル → ``[(色, 外接矩形, 矩形の中のマスク), ...]``。
    paints: Dict[int, List[Tuple[Rgb, Bounds, np.ndarray]]] = field(default_factory=dict)
    #: セル → 見つけられなかった部分の数。
    missed: Dict[int, int] = field(default_factory=dict)
    #: 写そうとした見本の部分の数。
    parts: int = 0
    #: 部分ごとの ``(色, 画素数, 見つかったセルの数)``。
    found: List[Tuple[Rgb, int, int]] = field(default_factory=list)
    #: 見本を含めたセルの数。
    cells: int = 0

    @property
    def painted_cells(self) -> int:
        return len(self.paints)


def reference_parts(pixels, min_pixels=MIN_PART_PIXELS) -> List[Part]:
    """見本の塗ってある部分（線・白・透明以外の、同じ色のつながり）。大きい順。"""
    regions = reference_check.CellRegions(pixels)
    parts = []
    for index in np.nonzero(regions.sizes >= min_pixels)[0].tolist():
        rgb = reference_check._to_rgb(regions.value(index))
        if rgb is None or reference_check._is_line(rgb) or rgb == (255, 255, 255):
            continue
        parts.append((int(regions.sizes[index]), Part(rgb, regions.mask(index))))
    parts.sort(key=lambda item: -item[0])
    return [part for _size, part in parts]


def colors_to_copy(counts: Sequence[Dict[Rgb, int]], reference_index, share=PRESENT_SHARE) -> Set[Rgb]:
    """見本の色のうち、ほかのセルのどれかで足りていない（写す必要がある）色。

    :param counts: セルごとの :func:`reference_check.color_counts`。
    """
    reference = counts[reference_index]
    needed = set()
    for index, cell in enumerate(counts):
        if index == reference_index:
            continue
        for rgb, count in reference.items():
            if cell.get(rgb, 0) < count * share:
                needed.add(rgb)
    return needed


def mark_colors(reference: Dict[Rgb, int], cell: Dict[Rgb, int], share=PRESENT_SHARE) -> List[Rgb]:
    """このセルにあって、見本ではほとんど塗りつぶされている色（原画の印）。"""
    return [
        rgb for rgb, count in cell.items()
        if count >= MIN_PART_PIXELS
        and not reference_check._is_line(rgb)
        and reference.get(rgb, 0) < count * share
    ]


def _color_mask(pixels, colors) -> np.ndarray:
    if not colors:
        return np.zeros(pixels.shape[:2], dtype=bool)
    packed = fill_tracking._labels(pixels)
    values = [r | (g << 8) | (b << 16) | (255 << 24) for r, g, b in colors]
    return np.isin(packed, values)


def _holes(mask) -> np.ndarray:
    """``mask`` に囲まれた穴（外へつながらない、マスクの外の画素）。"""
    labels, _count = reference_check.label_regions(~mask)
    border = np.unique(np.concatenate(
        [labels[0], labels[-1], labels[:, 0], labels[:, -1]]
    ))
    return ~mask & ~np.isin(labels, border)


def _shift_between(before, after) -> Tuple[int, int]:
    """重心のずれ ``(dy, dx)``。"""
    by, bx = np.argwhere(before).mean(axis=0)
    ay, ax = np.argwhere(after).mean(axis=0)
    return int(np.floor(ay - by + 0.5)), int(np.floor(ax - bx + 0.5))


def _iou(a, b) -> float:
    union = int(np.count_nonzero(a | b))
    return int(np.count_nonzero(a & b)) / union if union else 0.0


@dataclass
class _Found:
    """1つのセルで見つかった部分。マスクは窓（または全体）の大きさ。"""

    score: float
    target: Optional[Rgb]
    mask: np.ndarray
    # 囲まれた印の画素と、そこを塗る色：((色, マスク), ...)。
    islands: List[Tuple[Rgb, np.ndarray]]


def _islands(pixels, holes_marked, shift, reference_pixels, colors):
    """囲まれた印を、見本の同じ場所（ずれを戻した所）の色ごとに分ける。

    使う色は ``colors``（見本でこの部分の穴の中にある色）に限る。ずれが少し
    外れても、まわりの髪の色などで印を塗らないため。
    """
    if not holes_marked.any() or not colors:
        return []
    dy, dx = shift
    height, width = holes_marked.shape
    ys, xs = np.nonzero(holes_marked)
    sy = np.clip(ys - dy, 0, height - 1)
    sx = np.clip(xs - dx, 0, width - 1)
    labels, _count = reference_check.label_regions(holes_marked)
    source = fill_tracking._labels(reference_pixels)[sy, sx]
    current = fill_tracking._labels(pixels)[ys, xs]
    islands: Dict[Rgb, np.ndarray] = {}
    component = labels[ys, xs]
    allowed = np.array(
        [r | (g << 8) | (b << 16) | (255 << 24) for r, g, b in colors], dtype=np.int64
    )
    for label in np.unique(component).tolist():
        chosen = component == label
        # 印1つごとに、見本のその場所で一番多い（穴の中の）色にそろえる。
        sampled = source[chosen]
        sampled = sampled[np.isin(sampled, allowed)]
        if not sampled.size:
            continue
        values, counts = np.unique(sampled, return_counts=True)
        value = int(values[np.argmax(counts)])
        rgb = reference_check._to_rgb(value)
        assert rgb is not None
        if np.all(current[chosen] == value):
            continue
        mask = islands.setdefault(rgb, np.zeros_like(holes_marked))
        mask[ys[chosen], xs[chosen]] = True
    return list(islands.items())


def _hole_colors(pixels, mask) -> List[Rgb]:
    """見本で、部分 ``mask`` の穴の中にある色（線と透明を除く）。"""
    bounds = bucket_fill.mask_bounds(mask)
    if bounds is None:
        return []
    height, width = mask.shape
    x0, y0, x1, y1 = bounds
    x0, y0, x1, y1 = max(0, x0 - 1), max(0, y0 - 1), min(width, x1 + 1), min(height, y1 + 1)
    holes = _holes(mask[y0:y1, x0:x1])
    colors = []
    for value in np.unique(fill_tracking._labels(pixels[y0:y1, x0:x1])[holes]).tolist():
        rgb = reference_check._to_rgb(value)
        if rgb is not None and not reference_check._is_line(rgb):
            colors.append(rgb)
    return colors


def _outer_parts(parts: List[Part]) -> List[Part]:
    """ほかの部分の穴の中にある部分（頬の中の斜線など）を除く。

    それは外側の部分を写すときに、囲まれた印として一緒に塗る。
    """
    enclosed = np.zeros(parts[0].mask.shape, dtype=bool) if parts else None
    kept = []
    for part in parts:  # 大きい順なので、囲む側が先に来る
        assert enclosed is not None
        area = int(np.count_nonzero(part.mask))
        if int(np.count_nonzero(part.mask & enclosed)) >= area * 0.9:
            continue
        kept.append(part)
        bounds = bucket_fill.mask_bounds(part.mask)
        assert bounds is not None
        height, width = part.mask.shape
        x0, y0, x1, y1 = bounds
        x0, y0, x1, y1 = max(0, x0 - 1), max(0, y0 - 1), min(width, x1 + 1), min(height, y1 + 1)
        enclosed[y0:y1, x0:x1] |= _holes(part.mask[y0:y1, x0:x1])
    return kept


def _find(
    pixels, previous, previous_pixels, color, options, marks, reference_pixels,
    island_colors,
):
    """``previous`` の図形をこのコマで探す。塗ってよいものが無ければ ``None``。"""
    categories = bucket_fill.guess_target_categories(pixels, previous, previous_pixels)
    # 写す先はまだ塗っていない（透明）か、もう見本の色で塗ってあることが多い。
    # 小さい図形はずれの推定が外れると周りの色の上に乗るので、この2つは必ず試す。
    for category in (None, color):
        if category not in categories:
            categories.append(category)
    mark_pixels = _color_mask(pixels, marks)
    best = None
    for category in categories:
        result = bucket_fill.track_fill_region(
            pixels, previous, category, options, previous_pixels=previous_pixels,
        )
        if not result.ok or result.mask is None:
            continue
        mask = result.mask
        holes = _holes(mask)
        # 外側に沿った範囲線は塗る範囲に含める。囲まれた印は別に塗る。
        band = bucket_fill.dilate(mask | holes, MARK_BAND) & mark_pixels & ~holes
        grown = mask | band
        # 場所が合っているかは追跡自身の重なりで決める（重心をそろえた重なりは
        # 形が似ているだけでも高くなり、小さな図形を隣の図形と取り違える）。
        # 範囲線を含めた範囲は、追跡が重ねたのと同じ位置で測り直す。
        score = float(result.score or 0)
        if result.matched is not None and band.any():
            score = max(score, _iou(result.matched, grown))
        if category in marks:
            # 原画の印そのものの上には塗らない（印は範囲線・囲まれた印として扱う）。
            continue
        if category in (None, color):
            if score < CONFIDENT_SCORE:
                continue
        else:
            ratio = np.count_nonzero(grown) / max(1, np.count_nonzero(previous))
            if score < OVERPAINT_SCORE or not (
                1 / OVERPAINT_AREA_RATIO <= ratio <= OVERPAINT_AREA_RATIO
            ):
                continue
        if best is None or score > best.score:
            shift = _shift_between(previous, grown)
            islands = _islands(
                pixels, holes & mark_pixels, shift, reference_pixels, island_colors
            )
            best = _Found(score, result.target, grown, islands)
    return best


def _window(mask, margin) -> Optional[Bounds]:
    bounds = bucket_fill.mask_bounds(mask)
    if bounds is None:
        return None
    height, width = mask.shape
    x0, y0, x1, y1 = bounds
    reach = max(margin, 2 * max(x1 - x0, y1 - y0))
    return (
        max(0, x0 - reach), max(0, y0 - reach),
        min(width, x1 + reach), min(height, y1 + reach),
    )


def _touches_inner_edge(mask, window, shape) -> bool:
    """窓の縁のうち、キャンバスの縁ではない所まで結果が届いているか。"""
    x0, y0, x1, y1 = window
    height, width = shape
    return bool(
        (x0 > 0 and mask[:, 0].any())
        or (y0 > 0 and mask[0, :].any())
        or (x1 < width and mask[:, -1].any())
        or (y1 < height and mask[-1, :].any())
    )


def _place(found: _Found, window, shape) -> _Found:
    """窓の中の結果を、キャンバス全体の大きさへ置き直す。"""
    x0, y0, x1, y1 = window
    crop = (slice(y0, y1), slice(x0, x1))

    def full(local):
        mask = np.zeros(shape, dtype=bool)
        mask[crop] = local
        return mask

    return _Found(
        found.score, found.target, full(found.mask),
        [(rgb, full(local)) for rgb, local in found.islands],
    )


def _track(
    pixels, previous, previous_pixels, color, options, marks, reference_pixels,
    island_colors, margin,
):
    window = _window(previous, margin)
    if window is not None:
        x0, y0, x1, y1 = window
        crop = (slice(y0, y1), slice(x0, x1))
        found = _find(
            pixels[crop], previous[crop], previous_pixels[crop], color, options,
            marks, reference_pixels[crop], island_colors,
        )
        if found is None:
            return None
        if not _touches_inner_edge(found.mask, window, previous.shape):
            return _place(found, window, previous.shape)
    # 窓に収まらない（大きく広がった）ときだけ、キャンバス全体で求め直す。
    return _find(
        pixels, previous, previous_pixels, color, options, marks, reference_pixels,
        island_colors,
    )


def plan_copy(
    cells: List[np.ndarray],
    reference_index: int,
    options: bucket_fill.BucketOptions,
    *,
    progress: Optional[Callable[[int, int], bool]] = None,
    margin: int = WINDOW_MARGIN,
) -> Optional[CopyPlan]:
    """``cells[reference_index]`` を見本に、ほかのセルへ塗り写す範囲を決める。

    :param cells: 同じレイヤーのセルの RGBA 配列。コマの順に並べる。
    :param progress: ``progress(済んだ数, 全体)``。偽を返すと中止して ``None`` を返す。
    """
    # 囲まれた色の置き換えはバケツの機能。塗り写しは見本から色を決める。
    options = replace(options, island_replacements=())
    reference = cells[reference_index]
    counts = [reference_check.color_counts(pixels) for pixels in cells]
    marks = [mark_colors(counts[reference_index], cell) for cell in counts]
    # 原画の印の色（見本に塗り残った範囲線や色指定のメモなど）は写さない。
    needed = colors_to_copy(counts, reference_index) - {
        rgb for index, cell_marks in enumerate(marks) if index != reference_index
        for rgb in cell_marks
    }
    parts = _outer_parts(
        [part for part in reference_parts(reference) if part.rgb in needed]
    )
    plan = CopyPlan(parts=len(parts), cells=len(cells))
    claimed: Dict[int, np.ndarray] = {}
    directions = (
        list(range(reference_index + 1, len(cells))),
        list(range(reference_index - 1, -1, -1)),
    )
    total = len(parts) * (len(cells) - 1)
    done = 0

    def miss(index):
        plan.missed[index] = plan.missed.get(index, 0) + 1

    def add(index, rgb, mask):
        taken = claimed.get(index)
        new = mask if taken is None else mask & ~taken
        if not new.any():
            return
        claimed[index] = new.copy() if taken is None else (taken | new)
        bounds = bucket_fill.mask_bounds(new)
        assert bounds is not None
        x0, y0, x1, y1 = bounds
        plan.paints.setdefault(index, []).append((rgb, bounds, new[y0:y1, x0:x1].copy()))

    for part in parts:
        found_cells = 0
        island_colors = _hole_colors(reference, part.mask)
        for order in directions:
            previous, previous_pixels = part.mask, reference
            misses = 0
            for position, index in enumerate(order):
                if progress is not None and not progress(done, total):
                    return None
                done += 1
                if misses >= MAX_MISSES_IN_A_ROW:
                    # 図形は画面から消えた。残りのセルは見つからなかったとして数える。
                    for rest in order[position:]:
                        miss(rest)
                    done += len(order) - position - 1
                    break
                pixels = cells[index]
                found = None
                if pixels.shape == reference.shape:
                    found = _track(
                        pixels, previous, previous_pixels, part.rgb, options,
                        marks[index], reference, island_colors, margin,
                    )
                if found is not None and misses and found.score < REACQUIRE_SCORE:
                    found = None
                taken = claimed.get(index)
                if found is not None and taken is not None:
                    area = int(np.count_nonzero(found.mask))
                    if int(np.count_nonzero(found.mask & taken)) > area * MAX_CLAIMED_SHARE:
                        # ほかの部分と同じ所を指している。どちらが正しいか分からないので塗らない。
                        found = None
                if found is None:
                    miss(index)
                    misses += 1
                    continue
                misses = 0
                previous, previous_pixels = found.mask, pixels
                found_cells += 1
                if found.target != part.rgb:
                    add(index, part.rgb, found.mask)
                for rgb, island in found.islands:
                    add(index, rgb, island)
        plan.found.append((part.rgb, int(np.count_nonzero(part.mask)), found_cells))
    if progress is not None:
        progress(total, total)
    return plan


__all__ = [
    "CONFIDENT_SCORE",
    "CopyPlan",
    "MIN_PART_PIXELS",
    "OVERPAINT_SCORE",
    "Part",
    "colors_to_copy",
    "mark_colors",
    "plan_copy",
    "reference_parts",
]
