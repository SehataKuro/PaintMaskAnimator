"""デジタル TP のアンチエイリアスを、いまある色面の色だけを残して取り除く（#63）。

ペイントソフトで作った TP は本来 2 値だが、書き出し設定などでアンチエイリアスが
混ざることがある。色数を決めてパレットを作り直す 2 値化（:mod:`color_ops`）では、
TP で決まっている色が別の色にまとめられたり、細い色トレス線が消えたりする。

ここでは色数を決めない。

* **残す色**：どこかに 3×3 で同じ色が並ぶ「平らな」色（色面の色）と、平らな所が
  なくても、ほかの残す色 2 つを混ぜた色ではない色（1〜2px の線の色）。混ぜた色かは、
  2 色を結ぶ線分の途中に乗るかで決める。
* **寄せる画素**：残す色ではない不透明な画素と、半透明の画素。まわり 8 画素に
  ある残す色のうち、自分の色にいちばん近い色へ寄せる。同じくらい近ければ線の色
  より塗りの色を選び、線を太らせない。半分より薄い半透明の画素は透明にする。

純粋な numpy の処理で、Qt は画像の出し入れにだけ使う。
"""
from typing import Tuple

import numpy as np

#: 平らな所がない色を「線の色」として残すのに要る画素数。
MIN_LINE_PIXELS = 4
#: 線分の途中とみなす範囲（両端からこの割合より内側）。
MIX_MARGIN = 0.08
#: 線分からこれだけ離れていなければ「2色を混ぜた色」とみなす（RGB の距離）。
MIX_DISTANCE = 8.0
#: 平らでない色の候補は、多い順にこの数まで調べる（3DCG のように色が多い絵の対策）。
MAX_CANDIDATES = 256
#: 半透明の画素は、この alpha 以上なら不透明にして色を寄せ、未満なら透明にする。
ALPHA_THRESHOLD = 128
#: 線の色（暗い色・原色）は、同じくらい近い塗りの色に負ける（距離の上乗せ）。
LINE_PENALTY = 1.0

_NEIGHBOURS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def _pack(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb.astype(np.int32)
    return (rgb[..., 0] << 16) | (rgb[..., 1] << 8) | rgb[..., 2]


def _unpack(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.int64)
    return np.stack([(values >> 16) & 255, (values >> 8) & 255, values & 255], axis=-1)


def _flat_mask(packed: np.ndarray, opaque: np.ndarray) -> np.ndarray:
    """3×3 のすべてが同じ色で不透明な画素（キャンバスの縁は数えない）。"""
    height, width = packed.shape
    flat = np.zeros((height, width), dtype=bool)
    if height < 3 or width < 3:
        return flat
    centre = packed[1:-1, 1:-1]
    inner = opaque[1:-1, 1:-1].copy()
    for dy, dx in _NEIGHBOURS:
        inner &= packed[1 + dy:height - 1 + dy, 1 + dx:width - 1 + dx] == centre
        inner &= opaque[1 + dy:height - 1 + dy, 1 + dx:width - 1 + dx]
    flat[1:-1, 1:-1] = inner
    return flat


def _is_mix(colour: np.ndarray, palette: np.ndarray) -> bool:
    """``colour`` が ``palette`` のどれか 2 色を結ぶ線分の途中に乗るか。"""
    if len(palette) < 2:
        return False
    points = palette.astype(np.float64)
    c = colour.astype(np.float64)
    a = points[:, None, :]
    b = points[None, :, :]
    direction = b - a
    length2 = np.sum(direction * direction, axis=2)
    with np.errstate(invalid="ignore", divide="ignore"):
        t = np.sum((c - a) * direction, axis=2) / length2
    inside = (length2 > 0) & (t > MIX_MARGIN) & (t < 1 - MIX_MARGIN)
    if not inside.any():
        return False
    nearest = a + np.nan_to_num(t)[:, :, None] * direction
    distance = np.sqrt(np.sum((c - nearest) ** 2, axis=2))
    return bool((inside & (distance <= MIX_DISTANCE)).any())


def surface_palette(rgba: np.ndarray) -> np.ndarray:
    """残す色（``r<<16|g<<8|b`` の整数の配列）。決め方はモジュールの説明を参照。"""
    opaque = rgba[:, :, 3] == 255
    packed = _pack(rgba[:, :, :3])
    flat_values = np.unique(packed[_flat_mask(packed, opaque)])
    values, counts = np.unique(packed[opaque], return_counts=True)
    keep = ~np.isin(values, flat_values) & (counts >= MIN_LINE_PIXELS)
    candidates = values[keep][np.argsort(-counts[keep], kind="stable")][:MAX_CANDIDATES]
    if not candidates.size:
        return flat_values
    pool = _unpack(np.concatenate([flat_values, candidates]))
    accepted = list(flat_values.tolist())
    for index, value in enumerate(candidates.tolist()):
        others = np.delete(pool, len(flat_values) + index, axis=0)
        if not _is_mix(_unpack(np.array([value]))[0], others):
            accepted.append(value)
    return np.array(sorted(set(accepted)), dtype=np.int64)


def antialias_mask(rgba: np.ndarray, palette: np.ndarray = None) -> np.ndarray:
    """寄せる画素：半透明の画素と、残す色ではない不透明な画素。"""
    if palette is None:
        palette = surface_palette(rgba)
    alpha = rgba[:, :, 3]
    semi = (alpha > 0) & (alpha < 255)
    stray = (alpha == 255) & ~np.isin(_pack(rgba[:, :, :3]), palette)
    return semi | stray


def count_antialias(rgba: np.ndarray) -> int:
    """アンチエイリアスとみなす画素の数（0 なら 2 値）。"""
    return int(np.count_nonzero(antialias_mask(rgba)))


def _is_line_colour(rgb: np.ndarray) -> np.ndarray:
    dark = (rgb <= 48).all(axis=-1)
    strong = (rgb >= 215).sum(axis=-1)
    weak = (rgb <= 60).sum(axis=-1)
    primary = (strong >= 1) & (strong <= 2) & (strong + weak == 3)
    return dark | primary


def remove_antialias(rgba: np.ndarray) -> Tuple[np.ndarray, int]:
    """アンチエイリアスを取り除いた RGBA と、書き換えた画素の数。

    なければ同じ内容のコピーと 0 を返す。出力の色は :func:`surface_palette` の色だけ
    （と透明）になる。
    """
    palette = surface_palette(rgba)
    mask = antialias_mask(rgba, palette)
    out = rgba.copy()
    changed = int(np.count_nonzero(mask))
    if not changed:
        return out, 0
    height, width = mask.shape
    faint = mask & (rgba[:, :, 3] < ALPHA_THRESHOLD)
    out[faint] = 0
    pending = mask & ~faint
    # 決まった画素の色（-1 は未定・透明）。
    labels = np.where(rgba[:, :, 3] == 255, _pack(rgba[:, :, :3]), -1).astype(np.int64)
    labels[mask] = -1
    own = rgba[:, :, :3].astype(np.float64)
    while pending.any():
        best = np.full((height, width), np.inf)
        choice = np.full((height, width), -1, dtype=np.int64)
        padded = np.pad(labels, 1, constant_values=-1)
        for dy, dx in _NEIGHBOURS:
            neighbour = padded[1 + dy:1 + dy + height, 1 + dx:1 + dx + width]
            usable = pending & (neighbour >= 0)
            if not usable.any():
                continue
            colour = _unpack(neighbour[usable]).astype(np.float64)
            distance = np.sqrt(np.sum((own[usable] - colour) ** 2, axis=1))
            distance += LINE_PENALTY * _is_line_colour(colour.astype(np.int64))
            current = best[usable]
            better = distance < current
            rows, cols = np.nonzero(usable)
            best[rows[better], cols[better]] = distance[better]
            choice[rows[better], cols[better]] = neighbour[usable][better]
        decided = pending & (choice >= 0)
        if not decided.any():
            break
        labels[decided] = choice[decided]
        pending &= ~decided
    if pending.any() and palette.size:
        # まわりに残す色が1つもない画素（半透明だけの塊など）は、いちばん近い色へ。
        colours = _unpack(palette).astype(np.float64)
        rows, cols = np.nonzero(pending)
        distance = np.sum((own[rows, cols][:, None, :] - colours[None, :, :]) ** 2, axis=2)
        labels[rows, cols] = palette[np.argmin(distance, axis=1)]
        pending[:] = False
    assigned = mask & ~faint & (labels >= 0)
    out[assigned, :3] = _unpack(labels[assigned]).astype(np.uint8)
    out[assigned, 3] = 255
    return out, changed
