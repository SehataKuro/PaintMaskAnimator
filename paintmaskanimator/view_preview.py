"""キャンバスの見え方だけを変えるプレビュー（UI 非依存）。

After Effects のコンポジションパネルにある「露光量の調整」と「チャンネルを表示」、
それに塗り漏れを探すための「隣接色分け」を、レイヤー画像の表示用の複製に掛ける。
画素データ・書き出し・サムネイルには一切影響しない。

* **露光量**: RGB を ``2 ** 段数`` 倍する（AE と同じく段数で指定）。暗い所の
  わずかな色の違いや、白飛びしかけた所を見つけやすくする。
* **チャンネル**: 赤・緑・青のどれか1つをグレーで、またはアルファを白黒で見せる。
* **隣接色分け**: 同じ色でつながった部分を1つの領域とみなし、隣り合う領域が
  必ず違う色になるよう、見分けやすい仮の色で塗り分ける（地図の塗り分けと同じ）。
  元の色が同じ領域は、主線で分かれていても同じ仮の色になるので、
  どこが同じ色なのかがそのまま読める。
  ノーマルとほんの少し違う影色の点のような、目では区別しにくい塗り漏れが
  はっきり浮き上がる。主線（黒に近い色）は元の色のまま残す。

numpy 配列だけを扱うので、GUI なしで単体テストできる。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import numpy as np

from . import native

#: チャンネル表示の選択肢。
CHANNEL_RGB = "rgb"
CHANNEL_RED = "red"
CHANNEL_GREEN = "green"
CHANNEL_BLUE = "blue"
CHANNEL_ALPHA = "alpha"
CHANNELS = (CHANNEL_RGB, CHANNEL_RED, CHANNEL_GREEN, CHANNEL_BLUE, CHANNEL_ALPHA)

#: 露光量（段）の範囲。AE と同じ。
EXPOSURE_MIN = -10.0
EXPOSURE_MAX = 10.0

#: 隣接色分けで、この距離（px）以内にある別の領域は隣り合うとみなす。
#: 主線をはさんで向かい合う領域も、隣どうしとして違う色にするため。
MAP_NEIGHBOR_GAP = 4
#: 隣接色分けで元の色のまま残す「主線」とみなす明るさの上限（各チャンネル）。
MAP_LINE_MAX = 48

#: 隣接色分けの仮の色。黒（主線）・白（背景）・灰色を避け、互いに見分けやすい色。
MAP_PALETTE: Tuple[Tuple[int, int, int], ...] = (
    (230, 25, 75),
    (60, 180, 75),
    (255, 225, 25),
    (0, 130, 200),
    (245, 130, 48),
    (145, 30, 180),
    (70, 240, 240),
    (240, 50, 230),
    (210, 245, 60),
    (250, 190, 212),
    (0, 128, 128),
    (170, 110, 40),
)


@dataclass(frozen=True)
class ViewPreview:
    """表示プレビューの設定。既定値はプレビューなし。"""

    #: 露光量（段）。0 で変えない。
    exposure: float = 0.0
    #: どのチャンネルを見せるか（``CHANNEL_*``）。
    channel: str = CHANNEL_RGB
    #: 隣接色分け（アクティブレイヤーだけに掛ける）。
    map_colors: bool = False
    #: 隣接色分けの仮の色（環境設定で変えられる）。
    palette: Tuple[Tuple[int, int, int], ...] = MAP_PALETTE

    @property
    def active(self) -> bool:
        return self.adjusts_pixels or self.map_colors

    @property
    def adjusts_pixels(self) -> bool:
        """露光量かチャンネルで、すべてのレイヤーの色を変えるか。"""
        return abs(self.exposure) > 1e-9 or self.channel != CHANNEL_RGB

    def key(self):
        return (
            round(float(self.exposure), 3), self.channel, bool(self.map_colors),
            self.palette if self.map_colors else None,
        )


def clamp_exposure(value) -> float:
    return max(EXPOSURE_MIN, min(EXPOSURE_MAX, float(value)))


def exposure_lut(stops) -> np.ndarray:
    """0〜255 の値を ``2 ** stops`` 倍した対応表（uint8）。"""
    scale = 2.0 ** clamp_exposure(stops)
    values = np.arange(256, dtype=np.float64) * scale
    return np.clip(np.rint(values), 0, 255).astype(np.uint8)


def adjust_pixels(pixels, preview: ViewPreview):
    """``pixels``（RGBA8888、非プレマルチプライ）に露光量とチャンネルを掛ける（その場で）。"""
    if abs(preview.exposure) > 1e-9:
        lut = exposure_lut(preview.exposure)
        pixels[:, :, :3] = lut[pixels[:, :, :3]]
    channel = preview.channel
    if channel in (CHANNEL_RED, CHANNEL_GREEN, CHANNEL_BLUE):
        index = (CHANNEL_RED, CHANNEL_GREEN, CHANNEL_BLUE).index(channel)
        gray = pixels[:, :, index].copy()
        pixels[:, :, 0] = gray
        pixels[:, :, 1] = gray
        pixels[:, :, 2] = gray
    elif channel == CHANNEL_ALPHA:
        # 不透明度を白の濃さで見せる。黒い背景（:func:`background_color`）の上に
        # 重ねるので、透明は黒、不透明は白になる。
        pixels[:, :, :3] = 255
    return pixels


def background_color(rgb, preview: ViewPreview) -> Tuple[int, int, int]:
    """キャンバスの背景（擬似透明の表示色）に同じプレビューを掛けた色。"""
    if preview.channel == CHANNEL_ALPHA:
        return (0, 0, 0)
    pixel = np.array([[[rgb[0], rgb[1], rgb[2], 255]]], dtype=np.uint8)
    adjust_pixels(pixel, preview)
    return int(pixel[0, 0, 0]), int(pixel[0, 0, 1]), int(pixel[0, 0, 2])


def label_regions(codes, valid) -> Tuple[np.ndarray, int]:
    """同じ値で4近傍につながった ``valid`` の画素に、1から始まる番号を付ける。

    番号は、各領域の最初の画素（行優先）が現れた順。Rust の部品があればそちらで。

    :param codes: 0〜0xFFFFFF の値の2次元配列（RGB をまとめた値など）。
    :returns: ``(番号の int32 配列, 領域の数)``。``valid`` でない画素は 0。
    """
    marked = np.where(valid, codes.astype(np.uint32), np.uint32(native.INVALID_CODE))
    result = native.label_regions(marked)
    if result is not None:
        return result
    return label_regions_numpy(codes, valid)


def label_regions_numpy(codes, valid) -> Tuple[np.ndarray, int]:
    """:func:`label_regions` の numpy 版。"""
    height, width = codes.shape
    labels = np.zeros((height, width), dtype=np.int64)
    if not valid.any():
        return labels.astype(np.int32), 0
    # 行ごとの「同じ値が続く区間（ラン）」に番号を振る。行をまたぐランはない。
    starts = valid.copy()
    starts[:, 1:] &= ~(valid[:, :-1] & (codes[:, 1:] == codes[:, :-1]))
    run_ids = np.cumsum(starts.reshape(-1), dtype=np.int64).reshape(height, width)
    run_ids[~valid] = 0
    run_count = int(run_ids.max())
    # 上下に同じ値で接するランどうしをつなぐ。
    joined = valid[:-1, :] & valid[1:, :] & (codes[:-1, :] == codes[1:, :])
    upper = run_ids[:-1, :][joined]
    lower = run_ids[1:, :][joined]
    parent = np.arange(run_count + 1, dtype=np.int64)
    if upper.size:
        pairs = np.unique(upper * (run_count + 1) + lower)
        upper, lower = pairs // (run_count + 1), pairs % (run_count + 1)
        # 小さい番号へ寄せることと、親をたどって縮めることを、変わらなくなるまで繰り返す。
        while True:
            root_upper, root_lower = parent[upper], parent[lower]
            low = np.minimum(root_upper, root_lower)
            before = parent.copy()
            np.minimum.at(parent, root_upper, low)
            np.minimum.at(parent, root_lower, low)
            while True:
                jumped = parent[parent]
                if np.array_equal(jumped, parent):
                    break
                parent = jumped
            if np.array_equal(parent, before):
                break
    # 根の番号を 1 から詰め直す。
    roots, compact = np.unique(parent[1:], return_inverse=True)
    mapping = np.zeros(run_count + 1, dtype=np.int32)
    mapping[1:] = compact.astype(np.int32) + 1
    return mapping[run_ids], int(roots.size)


def region_neighbors(labels, count, gap=MAP_NEIGHBOR_GAP):
    """``gap`` px 以内に接する領域の組 ``(a, b)``（a < b）の配列。"""
    pairs = []
    height, width = labels.shape
    for distance in range(1, max(1, int(gap)) + 1):
        for dy, dx in ((0, distance), (distance, 0), (distance, distance), (distance, -distance)):
            if dy >= height or abs(dx) >= width:
                continue
            if dx >= 0:
                a = labels[:height - dy, :width - dx]
                b = labels[dy:, dx:]
            else:
                a = labels[:height - dy, -dx:]
                b = labels[dy:, :width + dx]
            touching = (a > 0) & (b > 0) & (a != b)
            if touching.any():
                first, second = a[touching], b[touching]
                low = np.minimum(first, second).astype(np.int64)
                high = np.maximum(first, second).astype(np.int64)
                pairs.append(np.unique(low * (count + 1) + high))
    if not pairs:
        return np.zeros((0, 2), dtype=np.int64)
    merged = np.unique(np.concatenate(pairs))
    return np.stack((merged // (count + 1), merged % (count + 1)), axis=1)


def assign_map_colors(count, neighbors, areas, palette_size=None, groups=None):
    """隣り合う領域が違う番号になるよう、各領域に色の番号を割り当てる。

    大きい領域から順に、隣でまだ使っていない一番若い番号を選ぶ（貪欲法）。
    使える番号が尽きたら、隣で使っている回数が一番少ない番号にする。

    :param areas: 領域ごとの面積（``areas[番号]``、0 番は使わない）。
    :param groups: 領域ごとの組の番号（``groups[番号]``）。同じ組の領域には
        同じ色の番号を割り当てる（元の色が同じ領域をまとめるため）。
        ``None`` なら領域ごとに別々に割り当てる。
    :returns: ``colors[番号]`` が色の番号の配列。0 番は -1。
    """
    if palette_size is None:
        palette_size = len(MAP_PALETTE)
    areas = np.asarray(areas, dtype=np.int64)
    if groups is None:
        group_of = np.arange(count + 1, dtype=np.int64)
        group_count = count
    else:
        group_of = np.asarray(groups, dtype=np.int64)
        group_count = int(group_of[1:].max()) if count else 0
    # 組どうしの隣り合いに言い換える（同じ組どうしは色をそろえるので無視）。
    adjacency = [set() for _ in range(group_count + 1)]
    for a, b in neighbors.tolist():
        ga, gb = int(group_of[a]), int(group_of[b])
        if ga != gb:
            adjacency[ga].add(gb)
            adjacency[gb].add(ga)
    # 組の面積（合計）が大きい順に決める。
    group_areas = np.bincount(group_of[1:], weights=areas[1:], minlength=group_count + 1)
    group_colors = np.full(group_count + 1, -1, dtype=np.int32)
    order = np.argsort(-group_areas[1:], kind="stable") + 1
    for group in order.tolist():
        used = [0] * palette_size
        for other in adjacency[group]:
            color = group_colors[other]
            if color >= 0:
                used[color] += 1
        free = [index for index, value in enumerate(used) if value == 0]
        group_colors[group] = free[0] if free else int(np.argmin(used))
    colors = group_colors[group_of]
    colors[0] = -1
    return colors


def map_color_pixels(pixels, gap=MAP_NEIGHBOR_GAP, palette=MAP_PALETTE, color_mapping=None):
    """隣り合う領域が違う色になるよう、``pixels`` を仮の色で塗り分ける（その場で）。

    不透明で、白（擬似透明）でも主線（黒に近い色）でもない画素が対象。

    :param palette: 仮の色（RGB のタプル列）。先頭の色ほど多く使われる。
    :param color_mapping: 元の RGB 値からパレット番号への対応表。コマ間で共有すると、
        一度割り当てた色を維持する。新しい色の対応だけを追加する。
    """
    if not palette:
        palette = MAP_PALETTE
    rgb = pixels[:, :, :3]
    codes = (
        rgb[:, :, 0].astype(np.int32) << 16
        | rgb[:, :, 1].astype(np.int32) << 8
        | rgb[:, :, 2].astype(np.int32)
    )
    line = (
        (rgb[:, :, 0] <= MAP_LINE_MAX)
        & (rgb[:, :, 1] <= MAP_LINE_MAX)
        & (rgb[:, :, 2] <= MAP_LINE_MAX)
    )
    valid = (pixels[:, :, 3] > 0) & (codes != 0xFFFFFF) & ~line
    if not valid.any():
        return pixels
    labels, count = label_regions(codes, valid)
    areas = np.bincount(labels.reshape(-1), minlength=count + 1)
    neighbors = region_neighbors(labels, count, gap)
    # 元の色が同じ領域は同じ組にして、同じ仮の色で塗る。
    region_codes = np.zeros(count + 1, dtype=np.int64)
    region_codes[labels[valid]] = codes[valid]
    groups = np.zeros(count + 1, dtype=np.int64)
    if count:
        groups[1:] = np.unique(region_codes[1:], return_inverse=True)[1].reshape(-1).astype(np.int64) + 1
    if color_mapping is None:
        colors = assign_map_colors(count, neighbors, areas, len(palette), groups)
    else:
        # 面積や隣接関係が変わっても、既存の色は割り当て直さない。
        unique_codes = np.unique(region_codes[1:])
        group_count = len(unique_codes)
        adjacency = [set() for _ in range(group_count + 1)]
        for a, b in neighbors.tolist():
            ga, gb = int(groups[a]), int(groups[b])
            if ga != gb:
                adjacency[ga].add(gb)
                adjacency[gb].add(ga)
        group_colors = np.full(group_count + 1, -1, dtype=np.int32)
        for group, code in enumerate(unique_codes, 1):
            if int(code) in color_mapping:
                group_colors[group] = color_mapping[int(code)]
        group_areas = np.bincount(groups[1:], weights=areas[1:], minlength=group_count + 1)
        for group in (np.argsort(-group_areas[1:], kind="stable") + 1).tolist():
            if group_colors[group] >= 0:
                continue
            used = np.zeros(len(palette), dtype=np.int64)
            for other in adjacency[group]:
                if group_colors[other] >= 0:
                    used[group_colors[other]] += 1
            # 同じ隣接条件なら、過去に使った色の少ない番号を優先する。
            usage = np.bincount(list(color_mapping.values()), minlength=len(palette))
            chosen = min(range(len(palette)), key=lambda index: (used[index], usage[index], index))
            group_colors[group] = chosen
            color_mapping[int(unique_codes[group - 1])] = chosen
        colors = group_colors[groups]
    table = np.asarray(palette, dtype=np.uint8)
    pixels[valid, :3] = table[colors[labels[valid]]]
    return pixels


__all__ = [
    "CHANNELS",
    "ViewPreview",
    "adjust_pixels",
    "background_color",
    "assign_map_colors",
    "exposure_lut",
    "label_regions",
    "label_regions_numpy",
    "map_color_pixels",
]
