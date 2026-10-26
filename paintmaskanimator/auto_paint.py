"""色分けの線を手がかりに、見本のセルから同じレイヤーのほかのセルを自動で塗る。

原画では、黒の主線と、原色の赤・緑・青の色トレス線（範囲線・斜線・影の境目など）
が色分けの境界になる。黄・水色・赤紫（原色2つを重ねた色）の線も同じに扱う。
そこで各セルを、これらの線で区切られた「部分」（同じ色でつながった画素）に分け、
部分ごとに、もう塗り終えた隣のセルの同じ場所の色を写す。

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
* 細い部分は1〜2画素のずれで手本の線に重なりやすいので、色がそろわなければ
  位置を少しだけ動かして試す。
* 手本に無い形（開いた口、新しく見えてきたフリルなど）は、前のセルのその場所の
  色で塗ると間違えるので、塗らずに「新しく出てきた部分」として知らせる。
* 手がかりの無い色トレス線（見本に写っていない所の線）は残し、「決められなかった
  線」として知らせる。

画面も Qt も知らない純粋な処理で、書き込むのは呼び出し側（``main_window_auto_paint``）。
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple, cast

import numpy as np

from . import bucket_fill, fill_tracking, line_flow, reference_check

Rgb = Tuple[int, int, int]
Bounds = Tuple[int, int, int, int]

#: 色トレス線とみなす原色。強いチャンネルは 255 からこれだけ離れていてよい。
PRIMARY_TOLERANCE = 40
#: 弱いチャンネルは 0 からこれだけ離れていてよい（黄の線 ``#FFFF32`` など）。
PRIMARY_LOW_TOLERANCE = 60
#: これより小さい部分は手本と比べない（線のふちの数画素など）。透明のままの穴は、
#: まわりが1色なら最後にその色で埋める（:func:`_tiny_holes`）。
MIN_REGION_PIXELS = 3
#: 塗り終えたあとに残った透明の穴は、この大きさ（画素）まで、まわりの色で埋める。
TINY_HOLE_PIXELS = 4
#: 穴を埋めるのは、となりの色がこの割合以上そろっているとき。
TINY_HOLE_SHARE = 0.6
#: これだけ小さい穴は、となりで一番多い色がどれだけ少なくても埋める。2つの塗りの
#: 境目にできた1画素は、どちらの色にしても見た目は変わらないが、透けると必ず目立つ。
TINY_HOLE_ANY_PIXELS = 2
#: 原画のしるしの色（原色でない色トレス線）とみなす、1つの色の画素数の上限
#: （キャンバス全体に対する割合）。
MARK_MAX_SHARE = 0.02
#: しるしの色は細い：これだけ収縮させて残る所があれば、塗り色とみなす。
MARK_THICKNESS = 2
#: まだ塗っていない部分は、手本の色がこの割合以上そろっていれば塗る。
FILL_SHARE = 0.75
#: もう塗ってある部分は、手本の色がこの割合以上そろっていれば塗り替える。
REPAINT_SHARE = 0.8
#: 色トレス線は、手本の色がこの割合以上そろっていれば1本まるごとその色にする。
#: そろわなければ画素ごとに手本の色を写す。
LINE_SHARE = 0.75
#: 色トレス線は、手本でもこの割合以上が線の色なら残す。
KEEP_LINE_SHARE = 0.5
#: 「手本でも線」とみなす距離。中割りの線は1〜2画素ずれる。
KEEP_LINE_RADIUS = 2
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
#: 細い部分の色がそろわないとき、位置をこの画素数まで動かして試す。
REFINE_RADIUS = 2
#: 手本で重なった部分がこの倍より大きく、
NEW_PART_RATIO = 6.0
#: 部分を囲む線のうち手本にもある割合がこれ未満なら、手本に無い新しい形とみなす。
NEW_PART_OUTLINE = 0.35
#: 区画ごとの動きで決めるのは、動きの確かさ（重なった線の割合）がこれ以上のとき。
FLOW_MIN_SCORE = 0.35
#: 一番の色がこの割合以上で、二番の色のこの倍以上あれば、FILL_SHARE に届かなくても決める。
FLOW_LEAD_SHARE = 0.6
FLOW_LEAD = 2.5
FLOW_AREA_CHECK = False
#: 線の重なりがこれより弱いときは、透明な部分でも重ねた先と大きさを比べる。
WEAK_OVERLAP = 0.5
#: 手本で透明だった所のうち、この割合以上が「決められずに透明のまま」なら答えにしない。
OPEN_SHARE = 0.5
#: 元の線画から塗るとき、線の切れ目で2つ以上の部分（顔と髪など）がつながって1つに
#: なっていることがある。動きで重ねた手本の色が、それぞれこの割合以上の色に分かれ、
#: 合わせてほぼ全部（``SPLIT_TOTAL``）なら、画素ごとに重なった色で塗り分ける。
SPLIT_SHARE = 0.15
#: 部分を囲む線が、動きで重ねた手本の線にこの割合も重ならなければ、動きで決めない。
FLOW_OUTLINE = 0.45
#: 囲む線を確かめるのは、この大きさ（画素）までの部分。大きな部分は囲む線が長く、
#: 場所ごとに動きが違うので重なりにくい（髪の前を動く手のような部分は小さい）。
FLOW_OUTLINE_MAX_PIXELS = 6000
#: 部分だけをずらしたほうが、囲む線がこれだけ多く重なるなら、まわりと違う動きとみなす。
FLOW_OUTLINE_GAIN = 0.2
#: 絵の外とつながった部分を塗るとき：はみ出しとして除く細さ（画素）、塊の大きさの
#: 下限（画素）、塊のまわりのうちこちらの線である割合の下限。
OUTSIDE_RADIUS = 3
OUTSIDE_MIN_PIXELS = 300
OUTSIDE_OUTLINE = 0.45
SPLIT_TOTAL = 0.9
#: 塗り分けを考える部分の大きさの下限（画素）。小さな部分は1色で決める。
SPLIT_MIN_PIXELS = 2000
#: 塗り分ける境目のうち、こちらの線から ``SPLIT_CUT_REACH`` 画素以内にある割合の下限。
SPLIT_CUT_REACH = 3
SPLIT_CUT_LINE = 0.5
#: 形で探す範囲、似ているとみなす重なりの割合、ほかの色の候補との差、大きさの範囲（倍）。
SHAPE_REACH = 64
SHAPE_MATCH = 0.6
SHAPE_MARGIN = 0.1
SHAPE_SIZE_RATIO = 1.5
#: 決められなかった部分を、今の絵（塗りかけ）の色に従わせるときの、一色にそろう割合。
HINT_SHARE = 0.9
#: 手で塗った部分を答えにするとき、囲む線のうち同じ色で塗りつぶされている割合の上限。
#: これより多ければ、線を越えてはみ出した塗り（塗りかけの失敗）とみなして使わない。
HAND_CROSSED = 0.25
#: 手で塗ったセルを、何セル先までの手本にするか。
HAND_REACH = 6
#: 大きな部分は、この大きさの区画ごとにずれを求めて色を数える。
TILE = 64
#: 区画の中の部分の画素がこれより少なければ数えない。
MIN_TILE_PIXELS = 200
#: 決められなかった色トレス線を一覧に載せる大きさ。
LINE_REPORT_MIN_PIXELS = 20
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
    #: 人が確かめたほうがよい所（塗り残し・塗り間違いかも・新しく出てきた部分・
    #: 決められなかった線）。
    notes: List[reference_check.Finding] = field(default_factory=list)
    #: 決められずに残した色トレス線 ``(外接矩形, マスク)``。次のセルの手本になったとき、
    #: 「手本でも線のまま」を「残すと決めた」と取り違えないために使う。
    undecided: List[Tuple[Bounds, np.ndarray]] = field(default_factory=list)
    #: 決められずに透明のまま残した部分 ``(外接矩形, マスク)``。次のセルの手本に
    #: なったとき、「手本でも透明」を「塗らないと決めた」と取り違えないために使う。
    #: 元の線画から塗ると、取り違えた透明が次々のセルへ伝わってしまう。
    unpainted: List[Tuple[Bounds, np.ndarray]] = field(default_factory=list)

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


def primary_line_mask(pixels, tolerance=PRIMARY_TOLERANCE,
                      low_tolerance=PRIMARY_LOW_TOLERANCE) -> np.ndarray:
    """色トレス線の画素：原色の赤・緑・青と、2つを重ねた黄・水色・赤紫。

    どのチャンネルも「強い」か「弱い」のどちらかで、強いチャンネルが1つか2つの色。
    """
    # チャンネルごとに比べる（uint8 のまま。int16 に直すとキャンバス3枚ぶんの
    # 複製と、飛び飛びの並びの畳み込みが要る）。
    floor = np.uint8(255 - tolerance)
    ceiling = np.uint8(low_tolerance)
    red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    high_red, high_green, high_blue = red >= floor, green >= floor, blue >= floor
    strong = high_red.astype(np.uint8)
    strong += high_green
    strong += high_blue
    either = high_red | (red <= ceiling)
    either &= high_green | (green <= ceiling)
    either &= high_blue | (blue <= ceiling)
    either &= pixels[:, :, 3] > 0
    either &= strong >= 1
    either &= strong <= 2
    return either


def _is_primary(rgb) -> bool:
    pixel = np.array([[[*rgb, 255]]], dtype=np.uint8)
    return bool(primary_line_mask(pixel)[0, 0])


def line_only(pixels):
    """黒の主線・色トレス線・しるしの色だけを残した絵（ほかは透明）。元の線画として保持する。"""
    black = fill_tracking.line_art(pixels)
    primary = primary_line_mask(pixels)
    keep = black | primary | mark_mask(pixels, black, primary)
    out = np.zeros_like(pixels)
    out[keep] = pixels[keep]
    return out


def boundary_mask(pixels) -> np.ndarray:
    """色分けの境界：黒の主線と、色トレス線。"""
    return fill_tracking.line_art(pixels) | primary_line_mask(pixels)


def mark_mask(pixels, black=None, primary=None, max_share=MARK_MAX_SHARE,
              thickness=MARK_THICKNESS) -> np.ndarray:
    """原画のしるしの色の画素：原色ではないのに、細い線としてだけ置かれている色。

    頭のチャームのピンクのように、原色でない色で描き足されたしるしがある。
    わざわざ違う色で描いてあるのは「まわりと色が違う」という指示なので、まわりの色で
    塗りつぶしてはいけない。色トレス線と同じに扱い、手本で同じ場所が何色かを見て決める。

    塗り色（髪や肌のような広い面）と見分けるため、その色がこのセルのどこにも太い塊
    として現れず（``thickness`` 画素ぶん収縮させると消える）、面積も ``max_share``
    までのときだけ、しるしとみなす。
    """
    black = fill_tracking.line_art(pixels) if black is None else black
    primary = primary_line_mask(pixels) if primary is None else primary
    candidate = (pixels[:, :, 3] > 0) & ~black & ~primary
    out = np.zeros(candidate.shape, dtype=bool)
    if not candidate.any():
        return out
    values = fill_tracking._labels(pixels)
    limit = candidate.size * max_share
    found, counts = np.unique(values[candidate], return_counts=True)
    for value, count in zip(found.tolist(), counts.tolist()):
        if count > limit:
            continue  # 広い面はしるしではない
        same = candidate & (values == value)
        bounds = fill_tracking.mask_bounds(same)
        if bounds is None:
            continue
        x0, y0, x1, y1 = bounds
        if bucket_fill.erode(same[y0:y1, x0:x1], thickness).any():
            continue  # 太い所がある（塗り色）
        out |= same
    return out


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
        self.wide_primary = bucket_fill.dilate(self.primary, KEEP_LINE_RADIUS)
        #: 決められずに残した色トレス線と、透明のまま残した部分（このセルが手本のとき）。
        self.undecided = np.zeros(self.values.shape, dtype=bool)
        self.open = np.zeros(self.values.shape, dtype=bool)
        #: 元の線画から塗るセルか。塗りかけの色の手がかりが無いので、大きな部分の
        #: 区画ごとの位置合わせや形での照合など、追加の手がかりを使う。
        self.from_lines = False
        #: 塗り進める並びでの位置（動きの推定を使い回すのに使う）。
        self.index: Optional[int] = None
        self._mark: Optional[np.ndarray] = None

    @property
    def mark(self) -> np.ndarray:
        """原画のしるしの色（:func:`mark_mask`）。塗るセルでだけ使うので、要るときに作る。"""
        if self._mark is None:
            self._mark = mark_mask(self.pixels, self.black, self.primary)
        return self._mark

    def mark_undecided(self, items, unpainted=()) -> "_Cell":
        for (x0, y0, x1, y1), mask in items:
            self.undecided[y0:y1, x0:x1] |= mask
        for (x0, y0, x1, y1), mask in unpainted:
            self.open[y0:y1, x0:x1] |= mask
        return self


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
        if (on_line & guide.undecided[sy, sx]).sum() * 2 > on_line.sum():
            return None  # 手本でも決められずに残した線。手がかりにならない
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
    if finish is None or np.isin(around, np.array([host, finish])).mean() < FINISH_ENCLOSED:
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


def _refine(target: _Cell, guide: _Cell, local, bounds, shift):
    """``shift`` のまわり ``REFINE_RADIUS`` 画素で、色が一番そろうずれ。

    :returns: ``(ずれ, 色の値, そろっている割合)``。
    """
    value, share = _sample(target, guide, local, bounds, shift)
    best: Tuple[Tuple[int, int], Optional[int], float] = (shift, value, share)
    for dy in range(-REFINE_RADIUS, REFINE_RADIUS + 1):
        for dx in range(-REFINE_RADIUS, REFINE_RADIUS + 1):
            if dy == 0 and dx == 0:
                continue
            moved = (shift[0] + dy, shift[1] + dx)
            value, share = _sample(target, guide, local, bounds, moved)
            if value is not None and share > best[2]:
                best = (moved, value, share)
    return best


def _is_new_part(target: _Cell, guide: _Cell, index, local, bounds, shift) -> bool:
    """手本に無い形か（開いた口、新しく見えてきたフリルなど）。

    手本で重なった部分がずっと大きく（口なら顔全体の肌）、部分を囲む線も手本に
    ほとんど無いとき。塗り残しの隙間なら、囲む線は手本にもある。
    """
    sy, sx, inside = _shifted(target, local, bounds, shift)
    keep = inside & ~guide.black[sy, sx]
    if not keep.any():
        return False
    labels = guide.regions.labels[sy[keep], sx[keep]]
    found, counts = np.unique(labels, return_counts=True)
    size = float(guide.regions.sizes[int(found[int(np.argmax(counts))])])
    if size <= float(target.regions.sizes[index]) * NEW_PART_RATIO:
        return False
    height, width = target.values.shape
    x0, y0, x1, y1 = bounds
    ax0, ay0 = max(0, x0 - 2), max(0, y0 - 2)
    ax1, ay1 = min(width, x1 + 2), min(height, y1 + 2)
    area = np.zeros((ay1 - ay0, ax1 - ax0), dtype=bool)
    area[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0] = local
    ring = bucket_fill.dilate(area, 2) & ~area & target.boundary[ay0:ay1, ax0:ax1]
    ys, xs = np.nonzero(ring)
    if not ys.size:
        return False
    ry = np.clip(ys + ay0 + shift[0], 0, height - 1)
    rx = np.clip(xs + ax0 + shift[1], 0, width - 1)
    return float(guide.wide_boundary[ry, rx].mean()) < NEW_PART_OUTLINE


def _open_share(target: _Cell, guide: _Cell, local, bounds, shift) -> float:
    """重ねた先のうち、手本で決められずに透明のまま残した画素の割合。"""
    sy, sx, inside = _shifted(target, local, bounds, shift)
    if not inside.any():
        return 0.0
    return float(guide.open[sy[inside], sx[inside]].mean())


def _hint_color(hint, local, bounds) -> Optional[Rgb]:
    """今の絵（塗りかけ）で、部分が一色に塗ってあればその色。"""
    x0, y0, x1, y1 = bounds
    values = fill_tracking._labels(hint[y0:y1, x0:x1])[local]
    value, share = _majority(values)
    if value is None or share < HINT_SHARE:
        return None
    rgb = reference_check._to_rgb(value)
    if rgb is None or reference_check._is_line(rgb) or _is_primary(rgb):
        return None
    return rgb


def _hand_color(target: _Cell, hint, local, bounds) -> Optional[Rgb]:
    """元の線画の部分を、今の絵で人が一色に塗ってあれば、その色（確かな答え）。

    囲む線まで同じ色で塗りつぶしてあるのは、線を越えたはみ出しなので使わない。
    """
    rgb = _hint_color(hint, local, bounds)
    if rgb is None:
        return None
    ry, rx = _outline_pixels(target, local, bounds)
    if ry.size:
        ring = hint[ry, rx]
        crossed = (ring[:, 3] > 0) & (ring[:, :3] == np.array(rgb)).all(axis=1)
        if float(crossed.mean()) > HAND_CROSSED:
            return None
    return rgb


def _hand_paint(pixels, hint) -> CellPaint:
    """元の線画 ``pixels`` のうち、今の絵 ``hint`` で人が塗ってある部分。

    確かでない透明な部分は ``unpainted`` に入れる（手本にしたとき、決めていない所）。
    """
    target = _Cell(pixels)
    regions = target.regions
    result = CellPaint()
    for index in np.nonzero(regions.sizes >= MIN_REGION_PIXELS)[0].tolist():
        value = regions.value(index)
        if value == GAP_VALUE or reference_check._to_rgb(value) is not None:
            continue
        if index in regions.on_edge:
            continue
        bounds = regions.bounds(index)
        local = regions.local_mask(index)
        rgb = _hand_color(target, hint, local, bounds)
        if rgb is None:
            result.unpainted.append((bounds, local))
            continue
        paint_bounds, mask = _region_mask(target, local, bounds, value)
        result.paints.append((rgb, paint_bounds, mask))
        result.filled += 1
    return result


def _shape_match(target: _Cell, guide: _Cell, index, local, bounds) -> Optional[Rgb]:
    """手本の近くで、大きさと形がよく似た塗ってある部分の色。無ければ None。

    大きく動いたり回ったりした部分（手など）は、線の重なりでは位置が決まらない。
    どの手本でも決められなかった透明な部分にだけ、最後の手がかりとして使う。
    """
    own = int(target.regions.sizes[index])
    regions = guide.regions
    height, width = guide.values.shape
    x0, y0, x1, y1 = bounds
    wx0, wy0 = max(0, x0 - SHAPE_REACH), max(0, y0 - SHAPE_REACH)
    wx1, wy1 = min(width, x1 + SHAPE_REACH), min(height, y1 + SHAPE_REACH)
    cy, cx = np.nonzero(local)
    centre = (cy.mean() + y0, cx.mean() + x0)
    scores: Dict[Rgb, float] = {}
    for other in np.unique(regions.labels[wy0:wy1, wx0:wx1]).tolist():
        size = int(regions.sizes[other])
        if not own / SHAPE_SIZE_RATIO <= size <= own * SHAPE_SIZE_RATIO:
            continue
        rgb = reference_check._to_rgb(regions.value(other))
        if rgb is None or reference_check._is_line(rgb) or _is_primary(rgb):
            continue
        gx0, gy0, gx1, gy1 = regions.bounds(other)
        mask = regions.local_mask(other)
        gy, gx = np.nonzero(mask)
        base_y = int(round(gy.mean() + gy0 - centre[0]))
        base_x = int(round(gx.mean() + gx0 - centre[1]))
        best = 0.0
        for oy in range(base_y - 2, base_y + 3):
            for ox in range(base_x - 2, base_x + 3):
                # 両方の形を同じ座標へ置いて、重なりの割合（共通 / 合わせた範囲）を出す。
                ay0, ax0 = min(y0, gy0 - oy), min(x0, gx0 - ox)
                ay1, ax1 = max(y1, gy1 - oy), max(x1, gx1 - ox)
                a = np.zeros((ay1 - ay0, ax1 - ax0), dtype=bool)
                b = np.zeros_like(a)
                a[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0] = local
                b[gy0 - oy - ay0:gy1 - oy - ay0, gx0 - ox - ax0:gx1 - ox - ax0] = mask
                best = max(best, float((a & b).sum()) / float((a | b).sum()))
        scores[rgb] = max(scores.get(rgb, 0.0), best)
    ranked = sorted(scores.items(), key=lambda item: -item[1])
    if not ranked or ranked[0][1] < SHAPE_MATCH:
        return None
    if len(ranked) > 1 and ranked[1][1] > ranked[0][1] - SHAPE_MARGIN:
        return None  # 違う色の部分にも同じくらい似ている
    return ranked[0][0]


def _tiled_outcome(target: _Cell, guide: _Cell, local, bounds, margin):
    """大きな部分を ``TILE`` 画素の区画に分け、区画ごとにずれを求めて色を数える。

    元の線画だけから塗ると、髪の片側のような大きな部分が一つになる。場所によって
    動きが違うので、部分全体で一つのずれでは手本と重ならない。透明な部分にだけ使う。
    """
    x0, y0, x1, y1 = bounds
    votes: Dict[int, int] = {}
    open_votes = 0
    for ty in range(y0, y1, TILE):
        for tx in range(x0, x1, TILE):
            sub = local[ty - y0:ty - y0 + TILE, tx - x0:tx - x0 + TILE]
            if int(sub.sum()) < MIN_TILE_PIXELS:
                continue
            tile = (tx, ty, tx + sub.shape[1], ty + sub.shape[0])
            shift, overlap = _local_shift(target, guide, tile, margin=margin)
            if overlap < MIN_LINE_OVERLAP:
                continue
            sy, sx, inside = _shifted(target, sub, tile, shift)
            keep = inside & ~guide.black[sy, sx]
            found, counts = np.unique(guide.values[sy[keep], sx[keep]], return_counts=True)
            for value, count in zip(found.tolist(), counts.tolist()):
                votes[value] = votes.get(value, 0) + count
            open_votes += int(guide.open[sy[keep], sx[keep]].sum())
    total = sum(votes.values())
    if total < int(local.sum()) * MIN_SAMPLED_SHARE:
        return "unsure", None
    best = max(votes, key=lambda value: votes[value])
    if votes[best] < total * FILL_SHARE:
        return "unsure", None
    new_rgb = reference_check._to_rgb(best)
    if new_rgb is None and open_votes >= votes[best] * OPEN_SHARE:
        return "unsure", None  # 手本でも決められずに透明のままだった
    if new_rgb is None or reference_check._is_line(new_rgb) or _is_primary(new_rgb):
        return "keep", None
    return "paint", new_rgb


def _flow_votes(target: _Cell, guide: _Cell, flow: line_flow.Flow, local, bounds):
    """透明な部分1つを、区画ごとの動き ``flow`` で手本へ重ねたときの色の割合。

    手本で決められずに透明のまま残した所（``guide.open``）と線は数えない。
    :returns: ``(色の値ごとの割合, 重み, 重ねた先の座標)``。動きが確かでなければ None。
        重なった先の多くが手本でも決められなかった所なら ``"open"``（この手本では
        決めない。まわりの色で塗ると、前のセルの迷いが広がるため）。
    """
    x0, y0 = bounds[0], bounds[1]
    ys, xs = np.nonzero(local)
    ys = ys + y0
    xs = xs + x0
    score = float(flow.score_at(ys, xs).mean())
    if score < FLOW_MIN_SCORE:
        return None
    dy, dx = flow.at(ys, xs)
    height, width = target.values.shape
    gy = np.round(ys + dy).astype(np.int64)
    gx = np.round(xs + dx).astype(np.int64)
    inside = (gy >= 0) & (gy < height) & (gx >= 0) & (gx < width)
    gy, gx = gy[inside], gx[inside]
    inner = ~guide.boundary[gy, gx]
    # 手本で絵の外（キャンバスの端につながる透明）に当たる所も、決められなかった所と
    # 同じに扱う。線の切れ目から絵の外とつながって塗られなかっただけのことがある。
    outside = (guide.values[gy, gx] == 0) & np.isin(guide.regions.labels[gy, gx],
                                                    list(guide.regions.on_edge))
    keep = inner & ~guide.open[gy, gx] & ~outside
    if not keep.any() or (inner & ~keep).sum() >= inner.sum() * OPEN_SHARE:
        return "open"
    gy, gx = gy[keep], gx[keep]
    found, counts = np.unique(guide.values[gy, gx], return_counts=True)
    shares = {int(value): float(count) / float(keep.sum())
              for value, count in zip(found.tolist(), counts.tolist())}
    return shares, score * float(keep.sum()) / float(ys.size), (gy, gx)


def _flow_decision(target: _Cell, votes, index, local, bounds, near=True):
    """手本ごとの色の割合 ``votes`` ``[(手本, 動き, 割合, 重み, 座標)]`` を重みで足して決める。

    :returns: ``_region_outcome`` と同じ ``(種類, 色)``。
    """
    total: Dict[int, float] = {}
    weight = 0.0
    for _guide, _flow, shares, vote_weight, _where in votes:
        weight += vote_weight
        for value, share in shares.items():
            total[value] = total.get(value, 0.0) + share * vote_weight
    ranked = sorted(total.items(), key=lambda item: -item[1])
    sampled = ranked[0][0]
    share = ranked[0][1] / weight
    second = ranked[1][1] / weight if len(ranked) > 1 else 0.0
    if near and share < FILL_SHARE and int(target.regions.sizes[index]) >= SPLIT_MIN_PIXELS:
        colours = [value for value, amount in ranked if amount >= weight * SPLIT_SHARE]
        if (len(colours) >= 2 and sum(total[value] for value in colours) >= weight * SPLIT_TOTAL
                and all(_paintable(value) for value in colours)):
            return "split", colours
    # 大きな部分は、端の方が動きの違うとなりの部分へずれて重なりやすい。一番の色が
    # 二番よりずっと多ければ決めてよい。
    if share < FILL_SHARE and not (
        near and share >= FLOW_LEAD_SHARE and share >= second * FLOW_LEAD
    ):
        return "unsure", None
    guide, flow, _shares, _weight, (gy, gx) = max(
        votes, key=lambda vote: vote[2].get(sampled, 0.0) * vote[3]
    )
    outline = _flow_outline(target, guide, flow, local, bounds)
    new_rgb = reference_check._to_rgb(sampled)
    if new_rgb is None or reference_check._is_line(new_rgb) or _is_primary(new_rgb):
        # 透明のままにするのも、囲む線が手本と重なるときだけ（髪が背景へずれて重なる
        # ことがある）。
        return ("keep", None) if outline >= FLOW_OUTLINE else ("unsure", None)
    # 手本で重なった部分がずっと大きく、囲む線も手本に無ければ、新しく出てきた部分。
    labels = guide.regions.labels[gy, gx]
    found, counts = np.unique(labels, return_counts=True)
    size = float(guide.regions.sizes[int(found[int(np.argmax(counts))])])
    if size > float(target.regions.sizes[index]) * NEW_PART_RATIO and outline < NEW_PART_OUTLINE:
        return "new", None
    if (outline < FLOW_OUTLINE and int(target.regions.sizes[index]) <= FLOW_OUTLINE_MAX_PIXELS
            and _own_outline(target, guide, local, bounds) >= outline + FLOW_OUTLINE_GAIN):
        # 囲む線が手本の線に重ならず、部分だけをずらすとよく重なる。手や顔が髪の前を
        # 動くときのように、この部分だけまわりと違う動きをしている。部分ごとのずれで
        # 決め直す。
        return "unsure", None
    return "paint", new_rgb


def _outline_pixels(target: _Cell, local, bounds):
    """部分を囲む線の画素の座標 ``(ys, xs)``。"""
    height, width = target.values.shape
    x0, y0, x1, y1 = bounds
    ax0, ay0 = max(0, x0 - 2), max(0, y0 - 2)
    ax1, ay1 = min(width, x1 + 2), min(height, y1 + 2)
    area = np.zeros((ay1 - ay0, ax1 - ax0), dtype=bool)
    area[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0] = local
    ring = bucket_fill.dilate(area, 2) & ~area & target.boundary[ay0:ay1, ax0:ax1]
    ry, rx = np.nonzero(ring)
    return ry + ay0, rx + ax0


def _flow_outline(target: _Cell, guide: _Cell, flow: line_flow.Flow, local, bounds) -> float:
    """部分を囲む線を、動き ``flow`` で手本へ重ねたとき、手本の線に重なる割合。"""
    height, width = target.values.shape
    ry, rx = _outline_pixels(target, local, bounds)
    if not ry.size:
        return 1.0
    rdy, rdx = flow.at(ry, rx)
    my = np.clip(np.round(ry + rdy).astype(np.int64), 0, height - 1)
    mx = np.clip(np.round(rx + rdx).astype(np.int64), 0, width - 1)
    return float(guide.wide_boundary[my, mx].mean())


def _own_outline(target: _Cell, guide: _Cell, local, bounds) -> float:
    """部分を囲む線だけを、一番よく重なるようにずらしたとき、手本の線に重なる割合。"""
    height, width = target.values.shape
    ry, rx = _outline_pixels(target, local, bounds)
    if not ry.size:
        return 0.0

    def overlap(dy, dx):
        my = np.clip(ry + dy, 0, height - 1)
        mx = np.clip(rx + dx, 0, width - 1)
        return float(guide.wide_boundary[my, mx].mean())

    reach = line_flow.REACH // 2
    step = 2
    best, best_dy, best_dx = 0.0, 0, 0
    for dy in range(-reach, reach + 1, step):
        for dx in range(-reach, reach + 1, step):
            found = overlap(dy, dx)
            if found > best:
                best, best_dy, best_dx = found, dy, dx
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            best = max(best, overlap(best_dy + dy, best_dx + dx))
    return best


def _flow_outside(target: _Cell, guide: _Cell, flow: line_flow.Flow, local, bounds):
    """絵の外 ``local`` のうち、線の切れ目から絵の外とつながっただけの部分を塗る。

    動き ``flow`` で手本へ重ねて、手本で塗ってある画素の色を写す。細い所（動きの
    ずれで輪郭の外側にはみ出した所）は除き、残った塊のうち大きく、まわりの多くを
    こちらの線が囲む塊だけを塗る。
    :returns: ``CellPaint.paints`` と同じ ``[(色, 範囲, マスク)]``。
    """
    x0, y0, x1, y1 = bounds
    ys, xs = np.nonzero(local)
    dy, dx = flow.at(ys + y0, xs + x0)
    height, width = target.values.shape
    gy = np.clip(np.round(ys + y0 + dy).astype(np.int64), 0, height - 1)
    gx = np.clip(np.round(xs + x0 + dx).astype(np.int64), 0, width - 1)
    sampled = guide.values[gy, gx]
    usable = (sampled != 0) & ~guide.boundary[gy, gx] & ~guide.open[gy, gx]
    if int(usable.sum()) < OUTSIDE_MIN_PIXELS:
        return []
    colour = np.zeros(local.shape, dtype=np.int64)
    colour[ys[usable], xs[usable]] = sampled[usable]
    mask = colour != 0
    mask &= bucket_fill.dilate(bucket_fill.erode(mask, OUTSIDE_RADIUS), OUTSIDE_RADIUS)
    labels, _count = reference_check.label_regions(np.where(mask, colour, 0))
    found, counts = np.unique(labels[mask], return_counts=True)
    lines = target.boundary[y0:y1, x0:x1]
    paints = []
    for label in found[counts >= OUTSIDE_MIN_PIXELS].tolist():
        chunk = labels == label
        value = int(colour[chunk][0])
        if not _paintable(value):
            continue
        cy, cx = np.nonzero(chunk)
        cy0, cy1 = max(0, int(cy.min()) - 2), min(chunk.shape[0], int(cy.max()) + 3)
        cx0, cx1 = max(0, int(cx.min()) - 2), min(chunk.shape[1], int(cx.max()) + 3)
        part = chunk[cy0:cy1, cx0:cx1]
        ring = bucket_fill.dilate(part, 2) & ~part
        if float(lines[cy0:cy1, cx0:cx1][ring].mean()) < OUTSIDE_OUTLINE:
            continue
        paints.append((_opaque_rgb(value),
                       (x0 + cx0, y0 + cy0, x0 + cx1, y0 + cy1), part))
    return paints


def _paintable(value) -> bool:
    rgb = reference_check._to_rgb(value)
    return rgb is not None and not reference_check._is_line(rgb) and not _is_primary(rgb)


def _flow_split(target: _Cell, guide: _Cell, flow: line_flow.Flow, local, bounds, value,
                colours):
    """部分を、動きで重ねた手本の色 ``colours`` で画素ごとに塗り分ける。

    手本で線や別の色に重なった画素は、近い画素の色に合わせる。
    :returns: ``CellPaint.paints`` と同じ ``[(色, 範囲, マスク)]``。
    """
    x0, y0 = bounds[0], bounds[1]
    ys, xs = np.nonzero(local)
    dy, dx = flow.at(ys + y0, xs + x0)
    height, width = target.values.shape
    gy = np.clip(np.round(ys + y0 + dy).astype(np.int64), 0, height - 1)
    gx = np.clip(np.round(xs + x0 + dx).astype(np.int64), 0, width - 1)
    sampled = guide.values[gy, gx]
    masks = []
    for colour in colours:
        mask = np.zeros_like(local)
        hit = sampled == colour
        mask[ys[hit], xs[hit]] = True
        masks.append(mask)
    claimed = np.logical_or.reduce(masks)
    # 決まっていない画素は、となりから少しずつ広げて決める（先の色を優先）。
    for _ in range(max(local.shape)):
        if not (local & ~claimed).any():
            break
        grown = False
        for mask in masks:
            more = bucket_fill.dilate(mask, 1) & local & ~claimed
            if more.any():
                mask |= more
                claimed |= more
                grown = True
        if not grown:
            break
    masks[0] |= local & ~claimed
    # 線の切れ目でつながった部分なら、色の境目はこちらの線に沿う。何も無い所を
    # 横切るなら、手などが前を通っただけ（隣のセルの色が分かれて見えるだけ）。
    cut = np.zeros_like(local)
    for first, mask in enumerate(masks):
        for other in masks[first + 1:]:
            cut |= mask & bucket_fill.dilate(other, 1)
    if cut.any():
        near = bucket_fill.dilate(target.boundary[y0:y0 + local.shape[0], x0:x0 + local.shape[1]],
                                  SPLIT_CUT_REACH)
        if float(near[cut].mean()) < SPLIT_CUT_LINE:
            return []
    paint_bounds, whole = _region_mask(target, local, bounds, value)
    px0, py0 = paint_bounds[0], paint_bounds[1]
    oy, ox = y0 - py0, x0 - px0
    paints = []
    taken = np.zeros_like(whole)
    for colour, mask in zip(colours, masks):
        placed = np.zeros_like(whole)
        placed[oy:oy + local.shape[0], ox:ox + local.shape[1]] = mask
        taken |= placed
        paints.append([colour, placed])
    rest = whole & ~taken
    for entry in paints:
        more = bucket_fill.dilate(entry[1], GAP_RADIUS) & rest
        entry[1] = entry[1] | more
        rest &= ~more
    return [(_opaque_rgb(colour), paint_bounds, mask) for colour, mask in paints if mask.any()]


def _region_outcome(target: _Cell, guide: _Cell, index, value, rgb, local, bounds, margin):
    """部分1つを ``guide`` で決める。``(種類, 色)``。

    種類は ``"paint"``（その色で塗る）、``"keep"``（今のままでよい）、
    ``"suspect"``（違う色で塗ってあるが、形が合わず塗り替えない）、
    ``"new"``（手本に無い形なので塗らない）、``"unsure"``。
    """
    shift, overlap = _local_shift(target, guide, bounds, margin=margin)
    large = target.from_lines and max(bounds[2] - bounds[0], bounds[3] - bounds[1]) > TILE * 3 // 2
    if overlap < MIN_LINE_OVERLAP:
        if rgb is None and large:
            return _tiled_outcome(target, guide, local, bounds, margin)
        return "unsure", None
    sampled, share = _sample(target, guide, local, bounds, shift)
    if rgb is None and (sampled is None or share < FILL_SHARE):
        if large:
            return _tiled_outcome(target, guide, local, bounds, margin)
        # 細い部分は1〜2画素のずれで手本の線に重なりやすい。少し動かして試す。
        shift, sampled, share = _refine(target, guide, local, bounds, shift)
    if sampled is None:
        return "unsure", None
    if sampled == value:
        if (rgb is None and target.from_lines
                and _open_share(target, guide, local, bounds, shift) >= OPEN_SHARE):
            return "unsure", None  # 手本でも決められずに透明のままだった
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
    if (rgb is None and target.from_lines and new_rgb is not None and overlap < WEAK_OVERLAP
            and not _similar_size(target, guide, index, local, bounds, shift)):
        # 線の重なりが弱いうえ、重ねた先がずっと大きな部分（顔が髪へずれたなど）。
        return "unsure", None
    if new_rgb is None:
        return "keep", None  # 手本では透明（塗らない所）。塗ってある色も消さない。
    if reference_check._is_line(new_rgb) or _is_primary(new_rgb):
        return "keep", None  # 線の色では塗らない
    if rgb is None and _is_new_part(target, guide, index, local, bounds, shift):
        return "new", None
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


def _neighbour_color(target: _Cell, painted, local, window):
    """穴に接している色の値のうち一番多いもの、その割合、それが線の色か。

    接している画素だけを見る。少し離れた窓で多数決を取ると、2つの塗りの境目に
    できた穴では、関わりのない色まで数に入ってしまう。接している所がぜんぶ線なら
    線の色を返す（主線の中にできた1画素の穴は、背景が透けるより線の色が正しい）。
    """
    wy0, wx0, wy1, wx1 = window
    values = painted[wy0:wy1, wx0:wx1]
    ring = bucket_fill.dilate(local, 1) & ~local
    every = values[ring & ~target.boundary[wy0:wy1, wx0:wx1]]
    every = every[every != 0]
    if every.size:
        host, share = _majority(every)
        return host, share, False
    every = values[ring]
    host, share = _majority(every[every != 0])
    return host, share, True


def _tiny_holes(target: _Cell, painted):
    """塗り終えたあとに残った透明の画素を、となりの色で埋める塗り。

    線のきわや頬の中には、数画素だけ透明の穴が残る。部分として扱うには小さすぎて
    手本と比べられなかった画素や、部分分けのために閉じた切れ目の画素などで、
    仕上げでは背景が透けて見えてしまう。埋めるのは次の2つ。

    * ``TINY_HOLE_PIXELS`` までの小さな穴。``TINY_HOLE_ANY_PIXELS`` 以下なら、
      となりで一番多い色がどれだけ少なくても埋める（1画素の点は、どちらの塗りの
      色にしても見た目は変わらないが、透けると必ず目立つ）。
    * 部分分けのために閉じた色トレス線の切れ目（``_Cell.gaps``）。こちらで足した
      画素で、絵の一部ではないので、大きさによらず埋める。

    大きな塗り残しは埋めず、これまでどおり「確かめたい所」で知らせる。

    :param painted: ほかの部分と色トレス線を塗ったあとの、画素ごとの色の値。
    """
    height, width = painted.shape
    holes = (painted == 0) & ~target.boundary & ~target.mark
    if not holes.any():
        return []
    labels, _count = reference_check.label_regions(holes.astype(np.uint8))
    ys, xs = np.nonzero(holes)
    ids = labels[ys, xs]
    order = np.argsort(ids, kind="stable")
    ys, xs, ids = ys[order], xs[order], ids[order]
    _found, starts, sizes = np.unique(ids, return_index=True, return_counts=True)
    paints = []
    for start, size in zip(starts.tolist(), sizes.tolist()):
        hy, hx = ys[start:start + size], xs[start:start + size]
        # 切れ目をふさいだ画素でできた穴は、大きさによらず埋める。
        gap = bool(target.gaps[hy, hx].mean() >= 0.5)
        if size > TINY_HOLE_PIXELS and not gap:
            continue  # 大きな塗り残しは、塗らずに知らせてある
        y0, y1 = int(hy.min()), int(hy.max()) + 1
        x0, x1 = int(hx.min()), int(hx.max()) + 1
        wy0, wx0 = max(0, y0 - 1), max(0, x0 - 1)
        wy1, wx1 = min(height, y1 + 1), min(width, x1 + 1)
        local = np.zeros((wy1 - wy0, wx1 - wx0), dtype=bool)
        local[hy - wy0, hx - wx0] = True
        host, share, on_line = _neighbour_color(
            target, painted, local, (wy0, wx0, wy1, wx1),
        )
        if host is None:
            continue
        if share < TINY_HOLE_SHARE and size > TINY_HOLE_ANY_PIXELS and not gap:
            continue
        rgb = reference_check._to_rgb(host)
        if rgb is None or _is_primary(rgb):
            continue
        if reference_check._is_line(rgb) and not on_line:
            continue  # 線の色で塗るのは、線の中にできた穴だけ
        mask = np.zeros((y1 - y0, x1 - x0), dtype=bool)
        mask[hy - y0, hx - x0] = True
        paints.append((rgb, (x0, y0, x1, y1), mask))
    return paints


def paint_cell(pixels, guides: List[_Cell], *, finishes=None, learned=None,
               teacher: Optional[_Cell] = None, margin=SEARCH_MARGIN,
               hint=None, flow_for=None, hand_guides=(), hand_first=0,
               target: Optional[_Cell] = None) -> CellPaint:
    """塗り終えたセル ``guides`` を手本に、``pixels`` を塗る範囲と色を決める。

    部分ごとに ``guides`` を順に試し、最初に決まった手本に従う。仕上げ色を覚える
    （``learned`` へ足す）のは、人が塗った見本 ``teacher`` が一番目の手本のときだけ。

    :param hint: ``pixels`` が元の線画のとき、今の絵（塗りかけ）。どの手本でも決められ
        なかった部分だけ、今の絵で一色に塗ってあればその色に従う。渡したときは、元の
        線画から塗るための追加の手がかり（区画ごとの位置合わせ、形での照合）も使う。
    :param flow_for: ``flow_for(手本)`` で、このセルから手本への区画ごとの動き
        （:class:`line_flow.Flow`）。元の線画から塗るとき、透明な部分はまずこれで決める。
    :param hand_guides: 前後の、人が手で塗ったセル（手で塗った部分だけを塗り、ほかは
        決めていない所にした手本）。近い順。動きで重ねて、はっきり1色のときだけ使う。
    :param hand_first: ``hand_guides`` のうち、``guides`` の一番目より先に試す数
        （すぐ隣のセル）。ほかは一番目の手本の次に試す。
    :param target: ``pixels`` の部分分け。見本を増やして塗り直すとき、入力は同じ
        なので作り直さずに渡す（部分分けはセル1枚ぶんの計算で一番重い）。
    """
    finishes = {} if finishes is None else finishes
    learned = {} if learned is None else learned
    target = _Cell(pixels) if target is None else target
    target.from_lines = hint is not None
    result = CellPaint()
    # 手本を試す順：すぐ隣の手塗りのセル、一番目の手本、ほかの手塗りのセル、残りの手本。
    hand_set = set(hand_guides)
    order = list(hand_guides[:hand_first]) + guides[:1] + list(hand_guides[hand_first:]) \
        + guides[1:]
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
            # キャンバスの端につながる透明は、絵の外。ただ元の線画では、線の切れ目
            # から髪などが絵の外とつながることがある。
            if flow_for is not None and guides:
                flow = flow_for(guides[0])
                if flow is not None:
                    result.paints.extend(_flow_outside(target, guides[0], flow, local, bounds))
            continue
        if rgb is not None and reference_check._is_line(rgb):
            continue  # 黒の主線はそのまま
        result.checked += 1
        if (target.primary[y0:y1, x0:x1][local].any()
                or target.mark[y0:y1, x0:x1][local].any()):
            # 色トレス線と、原色でないしるしの色。どちらも手本の色に置き換える。
            lines.append((local, bounds, value))
            continue
        outcome, suspect, new_part = None, None, False
        split_paints: List[Tuple[Rgb, Bounds, np.ndarray]] = []
        if hint is not None and rgb is None:
            # 人が手で塗った部分は確かな答え。そのまま残し、次のセルの手本にもする。
            hand = _hand_color(target, hint, local, bounds)
            if hand is not None:
                paint_bounds, mask = _region_mask(target, local, bounds, value)
                result.paints.append((hand, paint_bounds, mask))
                result.filled += 1
                continue
        for guide in order:
            if guide in hand_set:
                if rgb is not None:
                    continue
                # 手で塗ったセルは、手で塗った部分だけを、はっきり1色のときに使う。
                flow = flow_for(guide) if flow_for is not None else None
                found = _flow_votes(target, guide, flow, local, bounds) if flow else None
                if flow is None or found is None or found == "open":
                    continue
                kind, decided = _flow_decision(target, [(guide, flow, *found)], index, local,
                                               bounds, near=False)
                if kind == "paint":
                    outcome = (kind, cast(Optional[Rgb], decided))
                    break
                continue
            kind = None
            flow = flow_for(guide) if flow_for is not None and rgb is None else None
            if flow is not None:
                # 元の線画から塗るとき、透明な部分は区画ごとの動きで手本へ重ねて決める。
                found = _flow_votes(target, guide, flow, local, bounds)
                if found == "open":
                    continue
                if found is not None:
                    # 塗り分けと、一番の色が多いだけで決めるのは、動きの小さいすぐ隣の
                    # セル（一番目の手本）でだけ。離れたセルは動きの見積もりが粗い。
                    kind, decided = _flow_decision(target, [(guide, flow, *found)], index,
                                                   local, bounds, near=guide is guides[0])
                    new_rgb = None
                    if kind == "split":
                        split_paints = _flow_split(target, guide, flow, local, bounds, value,
                                                   cast(List[int], decided))
                        kind = "split" if split_paints else "unsure"
                    else:
                        new_rgb = cast(Optional[Rgb], decided)
            if kind is None or kind == "unsure":
                kind, new_rgb = _region_outcome(target, guide, index, value, rgb, local,
                                                bounds, margin)
            if kind == "suspect":
                suspect = suspect or new_rgb
            elif kind == "new":
                new_part = True
            elif kind != "unsure":
                outcome = (kind, new_rgb)
                break
        size = int(regions.sizes[index])
        if outcome is None and rgb is None and suspect is None and guides and target.from_lines:
            matched = _shape_match(target, guides[0], index, local, bounds)
            if matched is not None:
                outcome = ("paint", matched)
        if outcome is None and rgb is None and suspect is None and hint is not None:
            kept = _hint_color(hint, local, bounds)
            if kept is not None:
                outcome = ("paint", kept)
        if new_part and (outcome is None or outcome[0] not in ("paint", "split")):
            # どの手本でも塗れず、手本に無い形と分かった（手本で透明だった場合も、
            # 前のセルで同じく決められなかっただけなので知らせる）。
            if size >= REPORT_MIN_PIXELS:
                result.notes.append(reference_check.Finding(
                    reference_check.KIND_NEW_PART, None, size, bounds=bounds,
                    mask=local.copy(),
                ))
            if rgb is None:
                result.unpainted.append((bounds, local))
            continue
        if outcome is None:
            if suspect is not None and size >= SUSPECT_MIN_PIXELS:
                result.notes.append(reference_check.Finding(
                    reference_check.KIND_SUSPECT, rgb, size, bounds=bounds,
                    expected=suspect, mask=local.copy(),
                ))
            elif suspect is None:
                result.unsure += 1
                if rgb is None:
                    result.unpainted.append((bounds, local))
                if rgb is None and size >= REPORT_MIN_PIXELS:
                    result.notes.append(reference_check.Finding(
                        reference_check.KIND_UNPAINTED, None, size, bounds=bounds,
                        mask=local.copy(),
                    ))
            continue
        kind, new_rgb = outcome
        if kind == "split":
            result.paints.extend(split_paints)
            result.filled += 1
        elif kind == "paint" and new_rgb is not None:
            paint_bounds, mask = _region_mask(target, local, bounds, value)
            result.paints.append((new_rgb, paint_bounds, mask))
            result.filled += 1
    # 色トレス線は、まわりの部分を塗ってから、となりの色を見て決める。線は1本の
    # 取り違えが目立つので、決められた手本みんなで「残す／塗る」を多数決にする
    # （同数なら先の手本に従う）。塗るときの色は、塗ると答えた最初の手本に従う。
    painted = fill_tracking._labels(apply_paint(pixels, result))
    line_start = len(result.paints)
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
            # 手がかりが無い（見本に写っていない所の線など）。色トレス線は置き換える
            # 前提で置いてあるので、最後の手段として、このセルで線のとなりにある色で
            # 塗る。残すと原色の線がそのまま仕上げに出てしまう（シルエットの外側に
            # 沿った線は、ずらした先の半分が絵の外で、手本から決められない）。
            _neighbours, host, _around = _line_neighbours(target, local, bounds, painted)
            fallback = (
                _paints_by_value(local, bounds, np.full(int(local.sum()), host))
                if host is not None else []
            )
            if fallback:
                result.paints.extend(fallback)
                result.line_pixels += sum(int(mask.sum()) for _rgb, _b, mask in fallback)
            else:
                result.undecided.append((bounds, local))
            size = int(local.sum())
            if size >= LINE_REPORT_MIN_PIXELS:
                result.notes.append(reference_check.Finding(
                    reference_check.KIND_UNDECIDED_LINE, reference_check._to_rgb(value), size,
                    bounds=bounds, mask=local.copy(),
                ))
            continue
        keep = sum(1 for paints in answers if not paints)
        if keep * 2 > len(answers) or (keep * 2 == len(answers) and not answers[0]):
            continue
        paints = next(paints for paints in answers if paints)
        result.paints.extend(paints)
        result.line_pixels += sum(int(mask.sum()) for _rgb, _bounds, mask in paints)
    # 小さすぎて手本と比べられなかった穴は、塗り終えたまわりの色で埋める。色トレス線の
    # ぶんだけ ``painted`` へ書き足す（もう一度セル全体に塗り直すより軽い）。
    for rgb, (x0, y0, x1, y1), mask in result.paints[line_start:]:
        painted[y0:y1, x0:x1][mask] = reference_check._pack(rgb)
    result.paints.extend(_tiny_holes(target, painted))
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
    hints: Optional[List[Optional[np.ndarray]]] = None,
    lines: Optional[List[Optional[np.ndarray]]] = None,
) -> Optional[AutoPlan]:
    """``cells[reference_index]`` を見本に、前後へ1セルずつ塗り進める。

    一通り塗ったら、迷った部分の少ないセルを見本に加えて、見本以外のセルを
    もう一度塗る（``max_rounds`` 回まで、加えるセルが無くなったら終わり）。
    加えた見本は、前の回の塗りを手本として使う。隣のセルで決められない部分も、
    少し離れた見本や、反対側の隣（前の回の塗り）から決められることがある。

    :param progress: ``progress(済んだ数, 全体)``。偽を返すと中止して ``None`` を返す。
    :param hints: ``cells`` が元の線画のとき、セルごとの今の絵（塗りかけ）。無いセルは None。
    :param lines: セルごとの元の線画（動きの推定に使う）。無いセルは ``cells`` の線を使う。
        見本は色トレス線を塗りつぶしてあることが多いので、元の線画があると動きが確かになる。
    """
    plan = AutoPlan()
    shape = cells[reference_index].shape
    usable = {index for index, pixels in enumerate(cells) if pixels.shape == shape}
    work = [item for item in _order(len(cells), reference_index) if item[0] in usable]
    previous = {reference_index: cells[reference_index]}
    references = {reference_index}
    learned: Dict[Tuple[int, int], Dict[int, int]] = {}
    # 区画ごとの動きは線だけで決まるので、セルの組ごとに一度だけ求めて使い回す。
    # 塗るセルの部分分けも、入力が変わらないので回をまたいで使い回す。
    targets: Dict[int, _Cell] = {}
    boundaries: Dict[int, np.ndarray] = {}
    flows: Dict[Tuple[int, int], line_flow.Flow] = {}

    def target_for(index):
        if index not in targets:
            targets[index] = _Cell(cells[index])
        return targets[index]

    def boundary(index):
        if index not in boundaries:
            source = lines[index] if lines is not None and lines[index] is not None else None
            boundaries[index] = boundary_mask(source if source is not None else cells[index])
        return boundaries[index]

    def flow_between(index, guide_cell):
        if guide_cell.index is None:
            return None
        key = (index, guide_cell.index)
        if key not in flows:
            flows[key] = line_flow.estimate_flow(boundary(index), boundary(guide_cell.index))
        return flows[key]

    # 人が手で塗ったセル：手で塗った部分だけを塗った手本（ほかは決めていない所）。
    hand_cells: Dict[int, _Cell] = {}
    for index, hint in enumerate(hints or []):
        if hint is None or index == reference_index or index not in usable:
            continue
        hand = _hand_paint(cells[index], hint)
        if not hand.paints:
            continue
        hand_cell = _Cell(apply_paint(cells[index], hand)).mark_undecided([], hand.unpainted)
        hand_cell.index = index
        hand_cells[index] = hand_cell

    def hand_guides_for(index):
        # 前後それぞれ、一番近い手塗りのセルだけ（多く使うと動きの推定に時間がかかる）。
        near = []
        for direction in (-1, 1):
            side = [other for other in hand_cells
                    if 0 < (other - index) * direction <= HAND_REACH]
            if side:
                near.append(min(side, key=lambda other: abs(other - index)))
        near.sort(key=lambda other: abs(other - index))
        return ([hand_cells[other] for other in near],
                sum(1 for other in near if abs(other - index) == 1))

    total = len(work) * max_rounds
    done = 0
    for _round in range(max_rounds):
        plan.rounds += 1
        current = {reference_index: cells[reference_index]}
        made: Dict[Tuple[str, int], _Cell] = {}
        sources = {"now": current, "before": previous}
        # 決められずに残した線：この回の分と、前の回の分。
        undecided_before = {
            index: (paint.undecided, paint.unpainted) for index, paint in plan.cells.items()
        }
        undecided_now: Dict[int, tuple] = {}

        def guide(source, index, made=made, sources=sources,
                  undecided_now=undecided_now, undecided_before=undecided_before):
            key = (source, index)
            if key not in made:
                cell = _Cell(sources[source][index])
                undecided = (undecided_now if source == "now" else undecided_before)
                made[key] = cell.mark_undecided(*undecided.get(index, ([], [])))
                made[key].index = index
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
            hint = hints[index] if hints is not None else None
            hand_guides, hand_first = hand_guides_for(index)
            paint = paint_cell(
                cells[index], guides, finishes=_finishes(learned), learned=learned,
                teacher=teacher, margin=margin, hint=hint, hand_guides=hand_guides,
                hand_first=hand_first,
                flow_for=(lambda guide_cell, index=index: flow_between(index, guide_cell))
                if hint is not None else None,
                target=target_for(index),
            )
            plan.cells[index] = paint
            current[index] = apply_paint(cells[index], paint)
            undecided_now[index] = (paint.undecided, paint.unpainted)
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
    "line_only",
    "mark_mask",
    "paint_cell",
    "paint_from_guide",
    "plan_auto_paint",
    "primary_line_mask",
]
