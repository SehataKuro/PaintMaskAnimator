"""前のセルの塗りを手がかりに、今のセルをまとめて塗る計画（UI 非依存）。

塗り済みのセル（参照）の塗り領域を1つずつ取り出し、串刺し塗りと同じ追跡
（:func:`bucket_fill.track_fill_region`）で今のセルの対応する領域を探して、
参照と同じ色を割り当てる。参照の領域に「含まれる」と言える今の領域だけを
塗り、自信のない領域は塗らずに残して数を返す。

線画は2値（アンチエイリアスなし）を前提にする。今のセルに出てくる不透明な色を
線の色とみなし、参照のその色の画素は塗り領域として扱わない。このため、線と
同じ色で塗った領域（黒髪を線と同じ黒で塗るなど）は写さない。

numpy 配列だけを扱うので、GUI なしで単体テストできる。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, List, Optional, Tuple

import numpy as np

from . import bucket_fill
from .imaging import (
    background_mask_packed,
    mask_bounds,
    rgba_packed_view,
    scanline_connected_region,
)

#: これより小さい参照の塗り領域（ゴミ・塗り残しの点など）は写さない。
MIN_REGION_AREA = 16
#: 1回で扱う参照の塗り領域の数の上限。時間がかかりすぎないように。
MAX_REGIONS = 400
#: 追跡の重なり（IoU）がこれ以上なら塗る（串刺し塗りの自動採用と同じ基準）。
CONFIDENT_IOU = 0.5
#: 追跡はキャンバス全体ではなく、参照の領域のまわりの窓で行う（速さのため）。
#: 窓は領域の大きさのこの倍率だけ外へ広げる（最低 :data:`MIN_WINDOW_MARGIN` px）。
WINDOW_MARGIN_RATIO = 2
MIN_WINDOW_MARGIN = 64

RGB = Tuple[int, int, int]
#: 外接矩形 ``(x0, y0, x1, y1)``（x1, y1 は排他）。
Bounds = Tuple[int, int, int, int]


@dataclass
class AutoFillPlan:
    """:func:`plan_auto_fill` の結果。"""

    #: ``(色, 外接矩形, 外接矩形の中の塗る画素の真偽マスク)`` の列。
    #: 大きなキャンバスで領域の数だけ全体のマスクを持たないよう、切り出して持つ。
    #: 塗る画素どうしは重ならない。
    fills: List[Tuple[RGB, Bounds, np.ndarray]] = field(default_factory=list)
    #: 今のセルで対応する領域が見つからないか、自信がなくて写さなかった
    #: 参照の領域の数。
    unmatched: int = 0
    #: 途中で取り消された。
    cancelled: bool = False

    @property
    def filled(self) -> int:
        return len(self.fills)


def _rgb_codes(pixels):
    """不透明な画素の RGB を 24bit の整数にした配列（透明は -1）。"""
    codes = (
        pixels[:, :, 0].astype(np.int32) << 16
        | pixels[:, :, 1].astype(np.int32) << 8
        | pixels[:, :, 2].astype(np.int32)
    )
    codes[pixels[:, :, 3] == 0] = -1
    return codes


def _isin(codes, colors):
    return np.isin(codes, np.asarray(sorted(colors), dtype=np.int32))


def reference_regions(reference_pixels, line_colors):
    """参照の塗り領域を、大きい順に ``(色, 外接矩形, 切り出したマスク)`` で返す。

    背景（擬似透明）と ``line_colors``（24bit の RGB）の画素を除いた、同じ色の
    4近傍の連結部分。
    """
    painted = ~background_mask_packed(rgba_packed_view(reference_pixels))
    codes = _rgb_codes(reference_pixels)
    if line_colors:
        painted &= ~_isin(codes, line_colors)
    regions = []
    values, counts = np.unique(codes[painted], return_counts=True)
    for value, count in zip(values.tolist(), counts.tolist()):
        if count < MIN_REGION_AREA:
            continue
        rgb = ((value >> 16) & 255, (value >> 8) & 255, value & 255)
        remaining = painted & (codes == value)
        flat = remaining.reshape(-1)
        while len(regions) < MAX_REGIONS * 4:
            first = int(flat.argmax())
            if not flat[first]:
                break
            y, x = divmod(first, remaining.shape[1])
            component = scanline_connected_region(remaining, (x, y))
            remaining &= ~component
            bounds = mask_bounds(component)
            if bounds is None or int(np.count_nonzero(component)) < MIN_REGION_AREA:
                continue
            x0, y0, x1, y1 = bounds
            regions.append((rgb, bounds, component[y0:y1, x0:x1].copy()))
    regions.sort(key=lambda item: -int(np.count_nonzero(item[2])))
    return regions[:MAX_REGIONS]


def _window(bounds, shape):
    """領域の外接矩形を広げた窓 ``(y0, y1, x0, x1)``。"""
    x0, y0, x1, y1 = bounds
    margin = max(MIN_WINDOW_MARGIN, WINDOW_MARGIN_RATIO * max(x1 - x0, y1 - y0))
    height, width = shape
    return (
        max(0, y0 - margin), min(height, y1 + margin),
        max(0, x0 - margin), min(width, x1 + margin),
    )


def _touches_inner_edge(mask, window, shape):
    """``mask``（窓の中の座標）が、キャンバスの端ではない窓の縁に触れているか。

    触れていれば、窓の外まで続く領域を窓で切ってしまっている。
    """
    y0, y1, x0, x1 = window
    height, width = shape
    return bool(
        (y0 > 0 and mask[0, :].any())
        or (y1 < height and mask[-1, :].any())
        or (x0 > 0 and mask[:, 0].any())
        or (x1 < width and mask[:, -1].any())
    )


def _track(pixels, bounds, crop, options, available, lines):
    """参照の領域に対応する今の領域を探す。``(窓, 窓の中のマスク, 重なり)`` か None。

    まず領域のまわりの窓の中だけで追跡し、見つかった領域が窓で切れていたら
    キャンバス全体でやり直す。
    """
    shape = available.shape
    x0, y0, x1, y1 = bounds
    for window in (_window(bounds, shape), (0, shape[0], 0, shape[1])):
        wy0, wy1, wx0, wx1 = window
        region = np.zeros((wy1 - wy0, wx1 - wx0), dtype=bool)
        region[y0 - wy0:y1 - wy0, x0 - wx0:x1 - wx0] = crop
        result = bucket_fill.track_fill_region(
            pixels[wy0:wy1, wx0:wx1], region, None, options,
            selection_mask=available[wy0:wy1, wx0:wx1],
            previous_lines=lines[wy0:wy1, wx0:wx1],
        )
        if not result.ok:
            return None
        if not _touches_inner_edge(result.mask, window, shape):
            return window, result.mask, result.score
    return None


def plan_auto_fill(
    reference_pixels,
    pixels,
    options: bucket_fill.BucketOptions,
    selection_mask=None,
    progress: Optional[Callable[[int, int], bool]] = None,
) -> AutoFillPlan:
    """参照 ``reference_pixels`` の塗りを、今の ``pixels`` へ写す計画を立てる。

    今のセルで塗るのは背景（まだ塗っていない部分）だけ。線の隙間閉じ・線の延長
    などは ``options`` の設定どおりに使う。

    :param progress: ``progress(済んだ数, 全体)`` を領域ごとに呼ぶ。偽を返すと取り消す。
    """
    plan = AutoFillPlan()
    if reference_pixels.shape != pixels.shape:
        return plan
    current_background = background_mask_packed(rgba_packed_view(pixels))
    line_colors = set(np.unique(_rgb_codes(pixels)[~current_background]).tolist())
    regions = reference_regions(reference_pixels, line_colors)

    reference_lines = np.zeros(pixels.shape[:2], dtype=bool)
    if line_colors:
        reference_lines = _isin(_rgb_codes(reference_pixels), line_colors)
        reference_lines &= ~background_mask_packed(rgba_packed_view(reference_pixels))
    # 今のセルで塗る（背景をたどる）。含み塗りは使わない。
    track_options = replace(
        options, adjacent=True, include_masks=False,
        target_color=bucket_fill.TARGET_ALL,
    )
    available = current_background.copy()
    if selection_mask is not None:
        available &= selection_mask

    total = len(regions)
    for index, (rgb, bounds, crop) in enumerate(regions):
        if progress is not None and not progress(index, total):
            plan.cancelled = True
            return plan
        found = _track(pixels, bounds, crop, track_options, available, reference_lines)
        if found is None or found[2] is None or found[2] < CONFIDENT_IOU:
            plan.unmatched += 1
            continue
        (wy0, wy1, wx0, wx1), mask, _score = found
        mask = mask & available[wy0:wy1, wx0:wx1]
        local = mask_bounds(mask)
        if local is None:
            plan.unmatched += 1
            continue
        lx0, ly0, lx1, ly1 = local
        mask = mask[ly0:ly1, lx0:lx1].copy()
        fill_bounds = (wx0 + lx0, wy0 + ly0, wx0 + lx1, wy0 + ly1)
        fx0, fy0, fx1, fy1 = fill_bounds
        available[fy0:fy1, fx0:fx1] &= ~mask
        plan.fills.append((rgb, fill_bounds, mask))
    if progress is not None:
        progress(total, total)
    return plan


__all__ = ["AutoFillPlan", "plan_auto_fill", "reference_regions"]
