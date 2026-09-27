"""バケツ塗りの領域判定（UI非依存）。

``PaintCanvas.flood_fill`` が抱えていた「どの画素を塗るか」の計算だけを切り出す。

切り出した理由は串刺し塗りにある。串刺し塗りは同じ判定を別のコマの画像に対して
繰り返すだけの操作なので、判定が UI 側の後始末（Undo・ステータス表示・シグナル
発行）と混ざったままだと、同じ 200 行を二度書くことになる。ここには判定だけを
置き、「塗れなかった理由」は :class:`FillResult` の ``reason`` で返して、
どう見せるかは呼び出し側に任せる。

QImage も QWidget も触らず numpy 配列だけを扱うので、GUI なしで単体テストできる。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

from .imaging import (
    background_mask_packed,
    mask_bounds,
    opaque_rgb_mask_packed,
    rgba_packed_view,
    scanline_connected_region,
)

__all__ = [
    "BucketOptions",
    "FillResult",
    "compute_fill_region",
    "seed_category",
    "dilate",
    "erode",
    "REASON_OK",
    "REASON_OUT_OF_BOUNDS",
    "REASON_OUTSIDE_SELECTION",
    "REASON_EMPTY",
    "REASON_OPEN_REGION",
]

#: 塗れた。
REASON_OK = "ok"
#: 開始点がキャンバスの外。
REASON_OUT_OF_BOUNDS = "out_of_bounds"
#: 開始点が選択範囲の外。
REASON_OUTSIDE_SELECTION = "outside_selection"
#: 判定の結果、塗る画素が 1 つも残らなかった。
REASON_EMPTY = "empty"
#: 「開いている領域は塗らない」設定で、領域がキャンバス端に達していた。
REASON_OPEN_REGION = "open_region"


@dataclass(frozen=True)
class BucketOptions:
    """ツールパネルのバケツ設定を、UI から切り離して持ち回るための値。

    ``PaintCanvas`` と串刺し塗りの両方が同じ設定で判定するように、
    チェックボックスの読み出しは :meth:`from_tools` の 1 箇所へ集める。
    """

    #: クリック位置につながる領域だけを塗る（OFF ならレイヤー内の同色を一括）。
    adjacent: bool = True
    #: 含み塗り（「含む色」に接する部分も塗りに含める）。
    include_masks: bool = False
    #: 含み塗りの対象色（RGB のタプル列）。「含む色」の登録色、未登録ならサブカラー。
    mask_colors: Tuple[Tuple[int, int, int], ...] = ()
    #: 含み塗りの対象に背景（擬似透明）が含まれるか。
    include_background_mask: bool = False
    #: 線の隙間を仮想的に閉じてから塗る。
    close_gap: bool = False
    #: 隙間閉じの半径（px）。
    gap_width: int = 4
    #: 領域がキャンバス端まで開いている場合は塗らない。
    require_closed: bool = False

    @classmethod
    def from_tools(cls, tools, *, mask_colors=(), background_selected=False):
        """ツールパネルのウィジェットから設定を読み出す。

        ``tools`` が ``None``（ウィンドウを持たないテスト用のキャンバスなど）の
        ときは ``flood_fill`` の従来の既定値と同じになるようにする。
        """
        if tools is None:
            return cls(
                mask_colors=tuple(mask_colors),
                include_background_mask=bool(background_selected),
            )
        return cls(
            adjacent=bool(tools.bucket_adjacent.isChecked()),
            include_masks=bool(tools.bucket_include_sub.isChecked()),
            mask_colors=tuple(mask_colors),
            include_background_mask=bool(background_selected),
            close_gap=bool(tools.bucket_close_gap.isChecked()),
            gap_width=max(1, int(tools.bucket_gap_width.value())),
            require_closed=bool(tools.bucket_require_closed.isChecked()),
        )


@dataclass
class FillResult:
    """:func:`compute_fill_region` の結果。

    ``reason`` が :data:`REASON_OK` のときだけ ``region`` が入る。呼び出し側は
    真偽値として扱える（``if result:``）。
    """

    reason: str = REASON_EMPTY
    region: Optional[np.ndarray] = None
    #: 開始点の色種別。``None`` は背景（擬似透明）を表す。
    target: Optional[Tuple[int, int, int]] = field(default=None)
    #: ``region`` の外接矩形 ``(x0, y0, x1, y1)``（x1, y1 は排他）。
    bounds: Optional[Tuple[int, int, int, int]] = None

    @property
    def ok(self) -> bool:
        return self.reason == REASON_OK and self.region is not None

    @property
    def mask(self) -> np.ndarray:
        """塗る画素のマスク。``ok`` が偽のときに読むのは呼び出し側の誤り。"""
        if self.region is None:
            raise ValueError(f"塗る領域がない（reason={self.reason}）")
        return self.region

    def __bool__(self) -> bool:
        return self.ok


def dilate(mask, iterations):
    """8 近傍で ``iterations`` 回膨張させる。"""
    result = mask.copy()
    for _ in range(iterations):
        source = result.copy()
        result[1:, :] |= source[:-1, :]
        result[:-1, :] |= source[1:, :]
        result[:, 1:] |= source[:, :-1]
        result[:, :-1] |= source[:, 1:]
        result[1:, 1:] |= source[:-1, :-1]
        result[:-1, :-1] |= source[1:, 1:]
        result[1:, :-1] |= source[:-1, 1:]
        result[:-1, 1:] |= source[1:, :-1]
    return result


def erode(mask, iterations):
    """8 近傍で ``iterations`` 回収縮させる。"""
    result = mask.copy()
    for _ in range(iterations):
        padded = np.pad(
            result, ((1, 1), (1, 1)), mode="constant", constant_values=False
        )
        result = (
            padded[0:-2, 0:-2]
            & padded[0:-2, 1:-1]
            & padded[0:-2, 2:]
            & padded[1:-1, 0:-2]
            & padded[1:-1, 1:-1]
            & padded[1:-1, 2:]
            & padded[2:, 0:-2]
            & padded[2:, 1:-1]
            & padded[2:, 2:]
        )
    return result


def background_mask(pixels):
    """擬似透明（alpha=0 または純白）の画素。"""
    return background_mask_packed(rgba_packed_view(pixels))


def seed_category(pixels, start_x, start_y):
    """開始点の「色種別」を返す。背景なら ``None``、それ以外は RGB のタプル。

    串刺し塗りが「このコマは対象外」と判断するための鍵になる。コマごとに絵は
    動くので、同じ座標が別のコマでは線の上に来ることがある。種別が一致する
    コマだけを塗ることで、線を塗り潰す事故を防ぐ。
    """
    pixel = pixels[start_y, start_x]
    if int(pixel[3]) == 0 or (
        int(pixel[0]) == 255 and int(pixel[1]) == 255 and int(pixel[2]) == 255
    ):
        return None
    return (int(pixel[0]), int(pixel[1]), int(pixel[2]))


def compute_fill_region(
    pixels,
    start_x,
    start_y,
    options: BucketOptions,
    selection_mask=None,
) -> FillResult:
    """``(start_x, start_y)`` から塗る画素の真偽マスクを求める。

    :param pixels: ``(height, width, 4)`` の RGBA8888 配列。読み取りのみ。
    :param selection_mask: 選択範囲の真偽マスク。``None`` なら制限なし。
    """
    height, width = pixels.shape[:2]
    if not (0 <= start_x < width and 0 <= start_y < height):
        return FillResult(REASON_OUT_OF_BOUNDS)
    if selection_mask is not None and not selection_mask[start_y, start_x]:
        return FillResult(REASON_OUTSIDE_SELECTION)

    category = seed_category(pixels, start_x, start_y)
    # RGBA を uint32 にまとめて比較する（チャンネルごとの np.all より
    # 1桁以上速く、一時配列も小さい）。
    packed = rgba_packed_view(pixels)
    pseudo_background = background_mask_packed(packed)

    if category is None:
        target_mask = pseudo_background.copy()
    else:
        target_mask = opaque_rgb_mask_packed(packed, category)
    if selection_mask is not None:
        target_mask &= selection_mask

    if options.adjacent:
        boundary = ~target_mask
        gap_recovery_mask = None
        if options.close_gap:
            gap_radius = max(1, int(options.gap_width))
            virtual_boundary = erode(dilate(boundary, gap_radius), gap_radius)
            # 仮想境界で漏れだけを止め、元画像では塗れる細い領域を
            # 最終描画時に回収する。線そのものは target_mask 外なので
            # 回収対象にはならない。
            gap_recovery_mask = virtual_boundary & target_mask
        else:
            virtual_boundary = boundary

        passable = target_mask & ~virtual_boundary
        if selection_mask is not None:
            passable &= selection_mask
        passable[start_y, start_x] = bool(target_mask[start_y, start_x])
        region = scanline_connected_region(passable, (start_x, start_y))

        if not region[start_y, start_x]:
            return FillResult(REASON_EMPTY, target=category)

        # 選択範囲そのものが閉じた境界として働くので、そのときは判定しない。
        if selection_mask is None and options.require_closed and (
            np.any(region[0, :])
            or np.any(region[-1, :])
            or np.any(region[:, 0])
            or np.any(region[:, -1])
        ):
            return FillResult(REASON_OPEN_REGION, target=category)

        if options.close_gap and gap_recovery_mask is not None:
            touching = dilate(region, 1) & gap_recovery_mask
            start_y_values, start_x_values = np.nonzero(touching)
            if start_x_values.size:
                recovered = scanline_connected_region(
                    gap_recovery_mask,
                    zip(start_x_values.tolist(), start_y_values.tolist()),
                )
                region |= recovered
    else:
        # 「隣接」OFFでは、クリック位置と同じRGBAを持つ全ピクセルを対象にする。
        region = target_mask

    final_region = region.copy()
    if options.include_masks:
        opaque_pixels = pixels[:, :, 3] > 0
        mask_family = np.zeros((height, width), dtype=bool)
        for mask_color in options.mask_colors:
            # (h, w, 3) の int32 配列を作らず、チャンネルごとに距離を足す。
            distance = np.zeros((height, width), dtype=np.int32)
            for channel, value in enumerate(mask_color):
                delta = pixels[:, :, channel].astype(np.int32)
                delta -= int(value)
                delta *= delta
                distance += delta
            mask_family |= opaque_pixels & (distance <= 56 * 56)
        if options.include_background_mask:
            mask_family |= pseudo_background
        if selection_mask is not None:
            mask_family &= selection_mask

        if options.adjacent:
            adjacent = np.zeros_like(region)
            adjacent[1:, :] |= region[:-1, :]
            adjacent[:-1, :] |= region[1:, :]
            adjacent[:, 1:] |= region[:, :-1]
            adjacent[:, :-1] |= region[:, 1:]
            seed_points = np.argwhere(mask_family & adjacent)
            if seed_points.size:
                starts = [
                    (int(point[1]), int(point[0])) for point in seed_points
                ]
                final_region |= scanline_connected_region(mask_family, starts)
        else:
            # 非隣接モードでは、含み塗りの対象色もレイヤー全体から一括対象にする。
            final_region |= mask_family

    if selection_mask is not None:
        final_region &= selection_mask
    bounds = mask_bounds(final_region)
    if bounds is None:
        return FillResult(REASON_EMPTY, target=category)
    return FillResult(REASON_OK, final_region, target=category, bounds=bounds)
