"""色分けの線を手がかりに、見本のセルから同じレイヤーのほかのセルを自動で塗る。

原画では、黒の主線と、原色の赤・緑・青の色トレス線（範囲線・斜線・影の境目など）
が色分けの境界になる。そこで各セルを、この4色の線で区切られた「部分」（同じ色で
つながった画素）に分け、部分ごとに、もう塗り終えた隣のセルの同じ場所の色を写す。

* 見本から前後へ1セルずつ進み、塗り終えたセルを次のセルの手本にする。隣どうしは
  動きが小さいので、遠い見本と直接比べるより取り違えが少ない。
* 部分の場所は、部分のまわりの線画のずれから求める（:func:`fill_tracking.estimate_shifts`）。
  ずらした先の手本の色が一色にそろっていなければ、塗らずに残す。
* 色トレス線そのものも、手本で同じ場所が何色かを見て決める。手本でも同じ線の色なら
  （影の境目の青など）そのまま残し、塗りの色になっていれば（頬の範囲線、斜線の
  仕上げ色など）その色で塗る。
* もう塗ってある部分を塗り替えるのは、手本の色がほぼ完全にそろっているときだけ。
  手本と大きく違う色で塗ってあっても、形が合わなければ塗り替えず「塗り間違いかも」
  として知らせる。
* 色トレス線の小さな切れ目（数画素）は閉じたものとして部分を分ける。範囲線が
  少し切れていても、頬と顔が1つの部分にならない。
* 見本で斜線などが仕上げ色（頬の中の赤い斜線なら、頬より濃い色）になっていれば、
  「どの色の線が、どの色の中にあると、何色になるか」を覚えて、見本から遠いセルでも
  その色にする。
* 一通り塗ったら、迷った所の少ないセルを見本に加えて、残りのセルを塗り直す。
  手本が増えるので、向きの変わる所でも反対側から手がかりが得られる。

画面も Qt も知らない純粋な処理で、書き込むのは呼び出し側（``main_window_auto_paint``）。
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from . import bucket_fill, fill_tracking, reference_check

Rgb = Tuple[int, int, int]
Bounds = Tuple[int, int, int, int]

#: 色トレス線とみなす原色。各チャンネルがこれだけ離れていてよい。
PRIMARY_TOLERANCE = 40
#: これより小さい部分は扱わない（線のふちの数画素など）。
MIN_REGION_PIXELS = 3
#: まだ塗っていない部分は、手本の色がこの割合以上そろっていれば塗る。
FILL_SHARE = 0.75
#: もう塗ってある部分は、手本の色がこの割合以上そろっていれば塗り替える。
REPAINT_SHARE = 0.8
#: 色トレス線は、手本の色がこの割合以上そろっていれば1本まるごとその色にする。
#: そろわなければ画素ごとに手本の色を写す。
LINE_SHARE = 0.75
#: 色トレス線は、手本でもこの割合以上が線の色なら残す。
KEEP_LINE_SHARE = 0.5
#: ずらした先のうち、手本で線でない画素がこの割合以上あること。
MIN_SAMPLED_SHARE = 0.5
#: 重ねた先の手本の部分と、大きさがこれ以上違えば塗らない。
MAX_AREA_RATIO = 3.0
#: 色トレス線の「となり」とみなす幅。
LINE_NEIGHBOUR = 2
#: ずれを探す範囲（隣のセルとのあいだの動きの最大）。
SEARCH_MARGIN = 48
#: ずれを求めるときに重ねる、部分のまわりの幅。
CONTEXT = 24
#: ずらして重ねた境界線が、この割合以上重ならなければ場所が分からないとみなす。
MIN_LINE_OVERLAP = 0.4
#: 色トレス線は細く取り違えが目立つので、重なりをもう少し求める。
MIN_COLOR_LINE_OVERLAP = 0.5
#: 人が塗った見本を直接の手本にするのは、見本からこのセル数以内だけ（遠いと
#: 動きが大きく、似た線へずれて重なりやすい）。
TEACHER_REACH = 2
#: 1画素ずれて重なった線を、ぴったり重なった線のどれだけに数えるか。
NEAR_LINE_WEIGHT = 0.8
#: ずれ1画素あたりの割り引き（重なった割合から引く）。
MOTION_PENALTY = 0.003
#: 色トレス線の、これだけの幅までの切れ目を閉じたものとして部分を分ける。
GAP_RADIUS = 2
#: 切れ目を閉じた画素を表す値（:func:`fill_tracking._labels` の値には現れない）。
GAP_VALUE = 1
#: 塗り残しを一覧に載せる大きさ（これより小さい隙間は数えるだけ）。
REPORT_MIN_PIXELS = 30
#: 「塗り間違いかも」とするのは、手本の色がこの割合以上そろっているとき。
SUSPECT_SHARE = 0.9
#: これより小さな部分は「塗り間違いかも」にしない（髪のハイライトなどの小さな形は、
#: セルごとに形や場所が変わる）。
SUSPECT_MIN_PIXELS = 100
#: 迷った部分がこの割合以下のセルは、次の回の見本に加える。
CONFIDENT_UNSURE_SHARE = 0.1
#: 見本を増やして塗り直す回数の上限（最初の1回を含む）。
MAX_ROUNDS = 3
#: 覚えた仕上げ色を使うのは、線のとなりがこの割合以上、まわりの色か仕上げ色のとき。
FINISH_ENCLOSED = 0.9
#: 仕上げ色を覚えるのに要る画素数。
MIN_FINISH_PIXELS = 6
_MAX_WINDOW = 384


@dataclass
class CellPaint:
    """1セル分の塗り替え。``(色, 外接矩形, 矩形の中のマスク)`` の並び。"""

    paints: List[Tuple[Rgb, Bounds, np.ndarray]] = field(default_factory=list)
    #: 塗った部分の数、塗るか決められなかった部分の数。
    filled: int = 0
    unsure: int = 0
    #: 塗り替えた色トレス線の画素数。
    line_pixels: int = 0
    #: 調べた部分の数（迷った割合を出すのに使う）。
    checked: int = 0
    #: 人が確かめたほうがよい所（塗り残し・塗り間違いかも）。
    notes: List[reference_check.Finding] = field(default_factory=list)

    @property
    def unsure_share(self) -> float:
        return self.unsure / self.checked if self.checked else 0.0


@dataclass
class AutoPlan:
    """自動塗りの計画。セルの番号は呼び出し側が渡した並びの位置。"""

    cells: Dict[int, CellPaint] = field(default_factory=dict)
    #: 見本に使ったセル（最初の見本と、途中で加えたセル）。
    references: List[int] = field(default_factory=list)
    #: 覚えた仕上げ色：``(線の色, まわりの色) -> 仕上げ色``。
    finishes: Dict[Tuple[Rgb, Rgb], Rgb] = field(default_factory=dict)
    #: 塗った回数（見本を増やして塗り直した回数 + 1）。
    rounds: int = 0

    @property
    def painted_cells(self) -> int:
        return sum(1 for cell in self.cells.values() if cell.paints)


def primary_line_mask(pixels, tolerance=PRIMARY_TOLERANCE) -> np.ndarray:
    """原色の赤・緑・青の画素（色トレス線）。"""
    rgb = pixels[:, :, :3].astype(np.int16)
    opaque = pixels[:, :, 3] > 0
    high = rgb >= 255 - tolerance
    low = rgb <= tolerance
    red = high[:, :, 0] & low[:, :, 1] & low[:, :, 2]
    green = low[:, :, 0] & high[:, :, 1] & low[:, :, 2]
    blue = low[:, :, 0] & low[:, :, 1] & high[:, :, 2]
    return opaque & (red | green | blue)


def _is_primary(rgb) -> bool:
    pixel = np.array([[[*rgb, 255]]], dtype=np.uint8)
    return bool(primary_line_mask(pixel)[0, 0])


def boundary_mask(pixels) -> np.ndarray:
    """色分けの境界：黒の主線と、原色の色トレス線。"""
    return fill_tracking.line_art(pixels) | primary_line_mask(pixels)


def gap_mask(boundary, primary, radius=GAP_RADIUS) -> np.ndarray:
    """色トレス線の近くで、``radius`` の幅までの切れ目をふさぐ画素。"""
    closed = bucket_fill.erode(bucket_fill.dilate(boundary, radius), radius)
    return closed & ~boundary & bucket_fill.dilate(primary, radius + 1)


class _Cell:
    """1セル分の、部分分けと境界。"""

    def __init__(self, pixels):
        self.pixels = pixels
        self.values = fill_tracking._labels(pixels)
        self.black = fill_tracking.line_art(pixels)
        self.primary = primary_line_mask(pixels)
        self.boundary = self.black | self.primary
        self.gaps = gap_mask(self.boundary, self.primary)
        region_values = self.values
        if self.gaps.any():
            region_values = self.values.copy()
            region_values[self.gaps] = GAP_VALUE
        self.regions = reference_check.CellRegions(pixels, values=region_values)
        # 中割りの線は1画素ずれることが多いので、ずれを求めるときは1画素太らせた
        # 線にも重なりを認める（ぴったり重なるよりは少なく数える）。
        self.wide_boundary = bucket_fill.dilate(self.boundary, 1)
        self.wide_primary = bucket_fill.dilate(self.primary, 1)


def _majority(values) -> Tuple[Optional[int], float]:
    if not values.size:
        return None, 0.0
    found, counts = np.unique(values, return_counts=True)
    best = int(np.argmax(counts))
    return int(found[best]), float(counts[best]) / float(values.size)


def _shifted(target: _Cell, local, bounds, shift):
    """``local`` の画素を ``shift`` だけずらした座標。はみ出した画素は ``inside`` が偽。"""
    height, width = target.values.shape
    x0, y0 = bounds[0], bounds[1]
    ys, xs = np.nonzero(local)
    sy, sx = ys + y0 + shift[0], xs + x0 + shift[1]
    inside = (sy >= 0) & (sy < height) & (sx >= 0) & (sx < width)
    return np.clip(sy, 0, height - 1), np.clip(sx, 0, width - 1), inside


def _sample(target: _Cell, guide: _Cell, local, bounds, shift):
    """``local``（``bounds`` の中のマスク）をずらして手本へ重ね、一番多い色を返す。

    手本の黒い線に重なった画素は数えない。
    :returns: ``(色の値, そろっている割合)``。決められなければ ``(None, 0)``。
    """
    sy, sx, inside = _shifted(target, local, bounds, shift)
    keep = inside & ~guide.black[sy, sx]
    if keep.sum() < keep.size * MIN_SAMPLED_SHARE:
        return None, 0.0
    return _majority(guide.values[sy[keep], sx[keep]])


def _similar_size(target: _Cell, guide: _Cell, index, local, bounds, shift) -> bool:
    """部分を手本へずらして重ねた先の部分が、大きさの近い部分か。

    動きの大きいセルでは、小さな部分（頬など）がとなりの大きな部分（髪など）へ
    ずれて重なることがある。大きさが ``MAX_AREA_RATIO`` 倍より違えば別の部分とみなす。
    """
    sy, sx, inside = _shifted(target, local, bounds, shift)
    keep = inside & ~guide.black[sy, sx]
    if not keep.any():
        return False
    labels = guide.regions.labels[sy[keep], sx[keep]]
    found, counts = np.unique(labels, return_counts=True)
    size = float(guide.regions.sizes[int(found[int(np.argmax(counts))])])
    own = float(target.regions.sizes[index])
    return own / MAX_AREA_RATIO <= size <= own * MAX_AREA_RATIO


def _line_paints(target: _Cell, guide: _Cell, local, bounds, shift, painted, value,
                 finishes, learned):
    """色トレス線1本を、手本で同じ場所の色にする塗り。手本でも線なら空。

    線は細く、中割りでは1画素ずれやすいので、一色にそろわなければ画素ごとに
    手本の色を写す（頬の範囲線なら、内側は頬の色、外側は肌の色になる）。

    使う色は、このセルで線のとなりにある色（``painted`` は部分を塗ったあとの色の
    値）か、手本で線の幅ほどの小さな部分の色（斜線の仕上げ色など）に限る。
    ずれて重なった先の、となりに無い大きな部分の色（髪など）では塗らない。

    :param learned: 仕上げ色を覚えるための集計。小さな部分の色に重なった画素数を足す。
    :returns: 塗り（空なら線のまま残す）。この手本では決められなければ ``None``。
    """
    sy, sx, inside = _shifted(target, local, bounds, shift)
    on_line = inside & guide.wide_primary[sy, sx]
    if on_line.sum() >= inside.sum() * KEEP_LINE_SHARE:
        return []  # 手本でも線のまま（影の境目など）
    values = guide.values[sy, sx]
    colored = inside & ~guide.black[sy, sx] & ~guide.primary[sy, sx] & (values != 0)
    if colored.sum() < inside.sum() * MIN_SAMPLED_SHARE:
        return None  # この手本では決められない
    majority, share = _majority(values[colored])
    if majority is None:
        return None
    neighbours, host, _around = _line_neighbours(target, local, bounds, painted)
    small = _small_colors(guide, sy[colored], sx[colored], int(local.sum()))
    small -= neighbours
    if host is not None:
        sampled = values[colored]
        for color in small:
            count = int((sampled == color).sum())
            if count:
                counts = learned.setdefault((value, host), {})
                counts[color] = counts.get(color, 0) + count
    allowed = neighbours | small
    chosen = np.full(values.shape, majority, dtype=values.dtype)
    if share < LINE_SHARE:
        chosen[colored] = values[colored]
    if majority not in allowed:
        majority = 0
    chosen[~np.isin(chosen, list(allowed))] = majority
    return _paints_by_value(local, bounds, chosen)


def _finish_paints(target: _Cell, local, bounds, painted, value, finishes):
    """覚えた仕上げ色 ``(線の値, まわりの値) -> 色の値`` が当てはまれば、その塗り。

    見本で頬の中の斜線などが仕上げ色になっていた組み合わせ。線のとなりが、まわりの
    色か仕上げ色だけで囲まれていれば、手本に線が残っていても（手本の塗り残し）、
    手本で場所が決められなくても仕上げ色にする。服と肌のあいだの線のように、
    別の部分との境目にある線には使わない。
    """
    if not finishes:
        return None
    _neighbours, host, around = _line_neighbours(target, local, bounds, painted)
    finish = finishes.get((value, host))
    if finish is None or np.isin(around, (host, finish)).mean() < FINISH_ENCLOSED:
        return None
    return _paints_by_value(local, bounds, np.full(int(local.sum()), finish))


def _paints_by_value(local, bounds, chosen):
    """線の画素ごとの色の値 ``chosen`` を、色ごとの塗りにまとめる。"""
    ys, xs = np.nonzero(local)
    paints = []
    for value in np.unique(chosen).tolist():
        rgb = reference_check._to_rgb(value)
        if rgb is None or reference_check._is_line(rgb) or _is_primary(rgb):
            continue
        mask = np.zeros(local.shape, dtype=bool)
        pick = chosen == value
        mask[ys[pick], xs[pick]] = True
        paints.append((rgb, bounds, mask))
    return paints


def _line_neighbours(target: _Cell, local, bounds, painted):
    """線のとなりにある色の値の集まり、一番多い色（無ければ ``None``）、
    となりの画素の色の値（透明も含む）。"""
    height, width = painted.shape
    x0, y0, x1, y1 = bounds
    ax0, ay0 = max(0, x0 - LINE_NEIGHBOUR), max(0, y0 - LINE_NEIGHBOUR)
    ax1, ay1 = min(width, x1 + LINE_NEIGHBOUR), min(height, y1 + LINE_NEIGHBOUR)
    around = np.zeros((ay1 - ay0, ax1 - ax0), dtype=bool)
    around[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0] = local
    around = bucket_fill.dilate(around, LINE_NEIGHBOUR) & ~target.boundary[ay0:ay1, ax0:ax1]
    around &= ~target.gaps[ay0:ay1, ax0:ax1]
    every = painted[ay0:ay1, ax0:ax1][around]
    found = every[every != 0]
    host, _share = _majority(found)
    return set(np.unique(found).tolist()), host, every


def _small_colors(guide: _Cell, sy, sx, line_size):
    """手本で、線の幅ほどの小さな部分の色（斜線の仕上げ色など）。"""
    colors = set()
    labels = guide.regions.labels[sy, sx]
    for label in np.unique(labels).tolist():
        if guide.regions.sizes[label] <= line_size * MAX_AREA_RATIO:
            value = guide.regions.value(label)
            if value not in (0, GAP_VALUE):
                colors.add(value)
    return colors


def _local_shift(target: _Cell, guide: _Cell, bounds, context=CONTEXT, margin=SEARCH_MARGIN):
    """部分のまわり（``bounds`` を ``context`` だけ広げた矩形）の境界線が、
    手本でどれだけずれた所にあるか。

    小さな部分は自分の縁だけでは場所を決めにくいので、まわりの線もいっしょに
    重ねる。線が一番多く重なるずれ ``(dy, dx)`` と、重なった割合を返す。
    """
    height, width = target.values.shape
    x0, y0, x1, y1 = bounds
    tx0, ty0 = max(0, x0 - context), max(0, y0 - context)
    tx1, ty1 = min(width, x1 + context), min(height, y1 + context)
    wx0, wy0 = max(0, tx0 - margin), max(0, ty0 - margin)
    wx1, wy1 = min(width, tx1 + margin), min(height, ty1 + margin)
    step = max(1, int(np.ceil(max(wx1 - wx0, wy1 - wy0) / _MAX_WINDOW)))
    before = np.zeros((wy1 - wy0, wx1 - wx0), dtype=np.float32)
    before[ty0 - wy0:ty1 - wy0, tx0 - wx0:tx1 - wx0] = target.boundary[ty0:ty1, tx0:tx1]
    before = before[::step, ::step]
    # ぴったり重なる線は1、1画素ずれて重なる線は ``NEAR_LINE_WEIGHT`` に数える。
    exact = guide.boundary[wy0:wy1:step, wx0:wx1:step].astype(np.float32)
    near_line = guide.wide_boundary[wy0:wy1:step, wx0:wx1:step].astype(np.float32)
    after = exact * (1.0 - NEAR_LINE_WEIGHT) + near_line * NEAR_LINE_WEIGHT
    total = float(before.sum())
    if total == 0 or not after.any():
        return (0, 0), 0.0
    rows, cols = before.shape
    size = (2 * rows, 2 * cols)
    correlation = np.fft.irfft2(
        np.fft.rfft2(after, size) * np.conj(np.fft.rfft2(before, size)), size
    )
    limit = margin // step
    ys = np.r_[0:limit + 1, size[0] - limit:size[0]]
    xs = np.r_[0:limit + 1, size[1] - limit:size[1]]
    near = correlation[np.ix_(ys, xs)] / total
    # 隣のセルどうしの動きは小さいので、大きく動くずれほど割り引く。似た形の線が
    # 離れた所にあっても（髪の筋、斜線など）、そちらへ飛びにくくなる。
    dys = np.where(ys >= size[0] // 2, ys - size[0], ys)
    dxs = np.where(xs >= size[1] // 2, xs - size[1], xs)
    near = near - MOTION_PENALTY * step * (np.abs(dys)[:, None] + np.abs(dxs)[None, :])
    peak_y, peak_x = np.unravel_index(int(np.argmax(near)), near.shape)
    share = float(correlation[ys[peak_y], xs[peak_x]]) / total
    return (int(dys[peak_y]) * step, int(dxs[peak_x]) * step), share


def _region_outcome(target: _Cell, guide: _Cell, index, value, rgb, local, bounds, margin):
    """部分1つを ``guide`` で決める。``(種類, 色)``。

    種類は ``"paint"``（その色で塗る）、``"keep"``（今のままでよい）、
    ``"suspect"``（違う色で塗ってあるが、形が合わず塗り替えない）、``"unsure"``。
    """
    shift, overlap = _local_shift(target, guide, bounds, margin=margin)
    if overlap < MIN_LINE_OVERLAP:
        return "unsure", None
    sampled, share = _sample(target, guide, local, bounds, shift)
    if sampled is None:
        return "unsure", None
    if sampled == value:
        return "keep", None
    new_rgb = reference_check._to_rgb(sampled)
    if rgb is not None and not _similar_size(target, guide, index, local, bounds, shift):
        # もう塗ってある部分が、大きさの違う部分へずれて重なった。塗り残しの
        # 透明な所は、手本ではまわりとつながって大きな部分になっているので比べない。
        if new_rgb is not None and share >= SUSPECT_SHARE:
            return "suspect", new_rgb
        return "unsure", None
    if share < (FILL_SHARE if rgb is None else REPAINT_SHARE):
        return "unsure", None
    if new_rgb is None:
        return "keep", None  # 手本では透明（塗らない所）。塗ってある色も消さない。
    if reference_check._is_line(new_rgb) or _is_primary(new_rgb):
        return "keep", None  # 線の色では塗らない
    return "paint", new_rgb


def _region_mask(target: _Cell, local, bounds, value):
    """塗る範囲。部分分けのために閉じた切れ目のうち、同じ色でとなり合う画素も含める。"""
    height, width = target.values.shape
    x0, y0, x1, y1 = bounds
    gx0, gy0 = max(0, x0 - GAP_RADIUS), max(0, y0 - GAP_RADIUS)
    gx1, gy1 = min(width, x1 + GAP_RADIUS), min(height, y1 + GAP_RADIUS)
    mask = np.zeros((gy1 - gy0, gx1 - gx0), dtype=bool)
    mask[y0 - gy0:y1 - gy0, x0 - gx0:x1 - gx0] = local
    gaps = target.gaps[gy0:gy1, gx0:gx1] & (target.values[gy0:gy1, gx0:gx1] == value)
    if gaps.any():
        mask |= bucket_fill.dilate(mask, GAP_RADIUS) & gaps
    return (gx0, gy0, gx1, gy1), mask


def paint_cell(pixels, guides: List[_Cell], *, finishes=None, learned=None,
               teacher: Optional[_Cell] = None, margin=SEARCH_MARGIN) -> CellPaint:
    """塗り終えたセル ``guides`` を手本に、``pixels`` を塗る範囲と色を決める。

    部分ごとに ``guides`` を順に試し、最初に決まった手本に従う。仕上げ色を覚える
    （``learned`` へ足す）のは、人が塗った見本 ``teacher`` が一番目の手本のときだけ。
    """
    finishes = {} if finishes is None else finishes
    learned = {} if learned is None else learned
    target = _Cell(pixels)
    result = CellPaint()
    regions = target.regions
    lines = []
    for index in np.nonzero(regions.sizes >= MIN_REGION_PIXELS)[0].tolist():
        value = regions.value(index)
        if value == GAP_VALUE:
            continue
        rgb = reference_check._to_rgb(value)
        bounds = regions.bounds(index)
        x0, y0, x1, y1 = bounds
        local = regions.local_mask(index)
        if rgb is None and index in regions.on_edge:
            continue  # キャンバスの端につながる透明は、絵の外
        if rgb is not None and reference_check._is_line(rgb):
            continue  # 黒の主線はそのまま
        result.checked += 1
        if target.primary[y0:y1, x0:x1][local].any():
            lines.append((local, bounds, value))
            continue
        outcome, suspect = None, None
        for guide in guides:
            kind, new_rgb = _region_outcome(target, guide, index, value, rgb, local, bounds,
                                            margin)
            if kind == "suspect":
                suspect = suspect or new_rgb
            elif kind != "unsure":
                outcome = (kind, new_rgb)
                break
        if outcome is None:
            size = int(regions.sizes[index])
            if suspect is not None and size >= SUSPECT_MIN_PIXELS:
                result.notes.append(reference_check.Finding(
                    reference_check.KIND_SUSPECT, rgb, size, bounds=bounds,
                    expected=suspect, mask=local.copy(),
                ))
            elif suspect is None:
                result.unsure += 1
                if rgb is None and size >= REPORT_MIN_PIXELS:
                    result.notes.append(reference_check.Finding(
                        reference_check.KIND_UNPAINTED, None, size, bounds=bounds,
                        mask=local.copy(),
                    ))
            continue
        kind, new_rgb = outcome
        if kind == "paint" and new_rgb is not None:
            paint_bounds, mask = _region_mask(target, local, bounds, value)
            result.paints.append((new_rgb, paint_bounds, mask))
            result.filled += 1
    # 色トレス線は、まわりの部分を塗ってから、となりの色を見て決める。線は1本の
    # 取り違えが目立つので、決められた手本みんなで「残す／塗る」を多数決にする
    # （同数なら先の手本に従う）。塗るときの色は、塗ると答えた最初の手本に従う。
    painted = fill_tracking._labels(apply_paint(pixels, result))
    for local, bounds, value in lines:
        paints = _finish_paints(target, local, bounds, painted, value, finishes)
        if paints is not None:
            result.paints.extend(paints)
            result.line_pixels += int(local.sum())
            continue
        answers = []
        # 仕上げ色は、見本のすぐ隣のセル（見本が一番目の手本）からだけ覚える。
        learn = bool(guides) and guides[0] is teacher
        for guide in guides:
            shift, overlap = _local_shift(target, guide, bounds, margin=margin)
            if overlap < MIN_COLOR_LINE_OVERLAP:
                continue
            paints = _line_paints(target, guide, local, bounds, shift, painted, value,
                                  finishes, learned if learn and guide is teacher else {})
            if paints is not None:
                answers.append(paints)
        if not answers:
            continue
        keep = sum(1 for paints in answers if not paints)
        if keep * 2 > len(answers) or (keep * 2 == len(answers) and not answers[0]):
            continue
        paints = next(paints for paints in answers if paints)
        result.paints.extend(paints)
        result.line_pixels += sum(int(mask.sum()) for _rgb, _bounds, mask in paints)
    return result


def paint_from_guide(pixels, guide_pixels, margin=SEARCH_MARGIN) -> CellPaint:
    """塗り終えたセル ``guide_pixels`` 1枚を手本に、``pixels`` を塗る範囲と色を決める。"""
    return paint_cell(pixels, [_Cell(guide_pixels)], margin=margin)


def apply_paint(pixels, paint: CellPaint) -> np.ndarray:
    """``paint`` を適用した複製を返す（次のセルの手本にする）。"""
    out = pixels.copy()
    for rgb, (x0, y0, x1, y1), mask in paint.paints:
        patch = out[y0:y1, x0:x1]
        patch[mask, :3] = rgb
        patch[mask, 3] = 255
    return out


def _opaque_rgb(value) -> Rgb:
    rgb = reference_check._to_rgb(value)
    assert rgb is not None, "仕上げ色の集計に透明は入らない"
    return rgb


def _finishes(learned) -> Dict[Tuple[int, int], int]:
    """集計から、覚えた仕上げ色を選ぶ。"""
    finishes = {}
    for key, counts in learned.items():
        color, count = max(counts.items(), key=lambda item: item[1])
        if count >= MIN_FINISH_PIXELS:
            finishes[key] = color
    return finishes


def _order(count, reference_index):
    """見本から近い順に。``(セル, 見本の側の隣)``。"""
    order = sorted(
        (abs(index - reference_index), index) for index in range(count) if index != reference_index
    )
    return [
        (index, index - 1 if index > reference_index else index + 1) for _distance, index in order
    ]


def plan_auto_paint(
    cells: List[np.ndarray],
    reference_index: int,
    *,
    progress: Optional[Callable[[int, int], bool]] = None,
    margin: int = SEARCH_MARGIN,
    max_rounds: int = MAX_ROUNDS,
) -> Optional[AutoPlan]:
    """``cells[reference_index]`` を見本に、前後へ1セルずつ塗り進める。

    一通り塗ったら、迷った部分の少ないセルを見本に加えて、見本以外のセルを
    もう一度塗る（``max_rounds`` 回まで、加えるセルが無くなったら終わり）。
    加えた見本は、前の回の塗りを手本として使う。隣のセルで決められない部分も、
    少し離れた見本や、反対側の隣（前の回の塗り）から決められることがある。

    :param progress: ``progress(済んだ数, 全体)``。偽を返すと中止して ``None`` を返す。
    """
    plan = AutoPlan()
    shape = cells[reference_index].shape
    usable = {index for index, pixels in enumerate(cells) if pixels.shape == shape}
    work = [item for item in _order(len(cells), reference_index) if item[0] in usable]
    previous = {reference_index: cells[reference_index]}
    references = {reference_index}
    learned: Dict[Tuple[int, int], Dict[int, int]] = {}
    total = len(work) * max_rounds
    done = 0
    for _round in range(max_rounds):
        plan.rounds += 1
        current = {reference_index: cells[reference_index]}
        made: Dict[Tuple[str, int], _Cell] = {}
        sources = {"now": current, "before": previous}

        def guide(source, index, made=made, sources=sources):
            key = (source, index)
            if key not in made:
                made[key] = _Cell(sources[source][index])
            return made[key]

        teacher = guide("now", reference_index)
        for index, toward in work:
            if progress is not None and not progress(done, total):
                return None
            done += 1
            guides = [guide("now", toward)] if toward in current else []
            # 前の回の塗り：反対側の隣と、近くの見本（両側）。
            side = 2 * index - toward
            extra = [side] if side in previous else []
            for direction in (-1, 1):
                near = [ref for ref in references if (ref - index) * direction > 0]
                if near:
                    extra.append(min(near, key=lambda ref: abs(ref - index)))
            for other in dict.fromkeys(extra):
                if other == reference_index:
                    if abs(index - reference_index) <= TEACHER_REACH:
                        guides.append(teacher)
                elif other != toward:
                    guides.append(guide("before", other))
            if teacher not in guides and abs(index - reference_index) <= TEACHER_REACH:
                guides.append(teacher)
            paint = paint_cell(cells[index], guides, finishes=_finishes(learned),
                               learned=learned, teacher=teacher, margin=margin)
            plan.cells[index] = paint
            current[index] = apply_paint(cells[index], paint)
        previous = current
        confident = {
            index for index, paint in plan.cells.items()
            if paint.unsure_share <= CONFIDENT_UNSURE_SHARE
        }
        added = confident - references
        references |= added
        if not added or len(references) == len(usable):
            break  # 増やす見本が無い、またはみんな見本になった
    plan.references = sorted(references)
    plan.finishes = {
        (_opaque_rgb(line), _opaque_rgb(host)): _opaque_rgb(color)
        for (line, host), color in _finishes(learned).items()
    }
    if progress is not None:
        progress(total, total)
    return plan


__all__ = [
    "AutoPlan",
    "CellPaint",
    "apply_paint",
    "boundary_mask",
    "gap_mask",
    "paint_cell",
    "paint_from_guide",
    "plan_auto_paint",
    "primary_line_mask",
]
