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

from . import native
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
    "fill_target_mask",
    "track_fill_region",
    "TARGET_COLORS",
    "seed_category",
    "dilate",
    "erode",
    "extend_line_ends",
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
#: 「対象色」の設定で、クリックした画素が塗る対象ではなかった。
REASON_NOT_TARGET = "not_target"

# 対象色（CLIP STUDIO の「対象色」に合わせた選択肢）。
#: クリックした色の領域（これまでどおり）。
TARGET_ALL = "all"
#: 透明な画素だけをたどる。
TARGET_TRANSPARENT = "transparent"
#: 透明以外の画素をたどる（透明に囲まれた部分）。
TARGET_SURROUNDED_BY_TRANSPARENT = "surrounded_by_transparent"
#: 黒い画素だけをたどる。
TARGET_BLACK = "black"
#: 黒以外の画素をたどる（黒い線に囲まれた部分）。
TARGET_SURROUNDED_BY_BLACK = "surrounded_by_black"
#: 白と透明の画素だけをたどる。
TARGET_WHITE_TRANSPARENT = "white_transparent"
#: 白と透明以外の画素をたどる。
TARGET_SURROUNDED_BY_WHITE_TRANSPARENT = "surrounded_by_white_transparent"
#: 透明だけをたどるが、ごく薄い半透明も透明とみなす。
TARGET_FAINT_AS_TRANSPARENT = "faint_as_transparent"
#: 透明以外をたどり、その内側に閉じ込められた透明の穴も塗る。
TARGET_OPAQUE_AND_INNER_TRANSPARENT = "opaque_and_inner_transparent"
#: 透明をたどり、その中に閉じ込められた透明以外の島も塗る。
TARGET_TRANSPARENT_AND_CLOSED_OPAQUE = "transparent_and_closed_opaque"

TARGET_COLORS = (
    TARGET_ALL,
    TARGET_TRANSPARENT,
    TARGET_SURROUNDED_BY_TRANSPARENT,
    TARGET_BLACK,
    TARGET_SURROUNDED_BY_BLACK,
    TARGET_WHITE_TRANSPARENT,
    TARGET_SURROUNDED_BY_WHITE_TRANSPARENT,
    TARGET_FAINT_AS_TRANSPARENT,
    TARGET_OPAQUE_AND_INNER_TRANSPARENT,
    TARGET_TRANSPARENT_AND_CLOSED_OPAQUE,
)

#: 「黒」とみなす RGB の上限（各チャンネル）。線のアンチエイリアスの濃い部分も含める。
BLACK_MAX = 48
#: 「白」とみなす RGB の下限（各チャンネル）。
WHITE_MIN = 240
#: 「薄い半透明」とみなすアルファの上限（約20%）。
FAINT_ALPHA_MAX = 50


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
    #: キャンバス端の手前で途切れた線を、端まで仮想的に延長してから塗る。
    extend_lines: bool = False
    #: 線を延長する最大の長さ（px）。端からこれより遠い線の端は延長しない。
    extend_length: int = 150
    #: 領域がキャンバス端まで開いている場合は塗らない。
    require_closed: bool = False
    #: どの画素をたどって塗るか（``TARGET_*``）。
    target_color: str = TARGET_ALL

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
            extend_lines=bool(_checked(tools, "bucket_extend_lines")),
            extend_length=_extend_length_from_tools(tools),
            require_closed=bool(tools.bucket_require_closed.isChecked()),
            target_color=_target_from_tools(tools),
        )


def _checked(tools, name):
    widget = getattr(tools, name, None)
    return widget is not None and widget.isChecked()


def _extend_length_from_tools(tools):
    widget = getattr(tools, "bucket_extend_length", None)
    if widget is None:
        return BucketOptions.extend_length
    return max(1, int(widget.value()))


def _target_from_tools(tools):
    combo = getattr(tools, "bucket_target_color", None)
    value = combo.currentData() if combo is not None else None
    return value if value in TARGET_COLORS else TARGET_ALL


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
    #: :func:`track_fill_region` が前のコマの領域との重なり（IoU）を入れる。
    #: 追跡でない塗り（クリックした種から塗った場合）は ``None``。
    score: Optional[float] = None

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
    """8 近傍で ``iterations`` 回膨張させる（Rust の部品があればそちらで）。"""
    result = native.dilate(mask, iterations)
    if result is not None:
        return result
    return dilate_numpy(mask, iterations)


def dilate_numpy(mask, iterations):
    """8 近傍で ``iterations`` 回膨張させる（numpy 版）。"""
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
    """8 近傍で ``iterations`` 回収縮させる（Rust の部品があればそちらで）。"""
    result = native.erode(mask, iterations)
    if result is not None:
        return result
    return erode_numpy(mask, iterations)


def erode_numpy(mask, iterations):
    """8 近傍で ``iterations`` 回収縮させる（numpy 版）。"""
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


#: 線の向きを測るために、端からたどる骨格の画素数。
EXTEND_TRACE_LENGTH = 12
#: これより短い骨格（点・ごく短いハネ）は向きが定まらないので延長しない。
EXTEND_MIN_TRACE = 6
#: 細線化の最大反復回数。線の太さのおよそ半分あれば足りる。
EXTEND_THIN_ITERATIONS = 8


def _thin(mask, max_iterations):
    """Zhang-Suen の細線化。``max_iterations`` で打ち切る。

    太い塊は細り切らないが、延長に使うのは細い線の端点だけなので問題ない。
    """
    image = mask.copy()
    for _ in range(max_iterations):
        changed = False
        for step in (0, 1):
            p = np.pad(image, 1, mode="constant", constant_values=False)
            # P2..P9：上から時計回り。
            ring = (
                p[:-2, 1:-1], p[:-2, 2:], p[1:-1, 2:], p[2:, 2:],
                p[2:, 1:-1], p[2:, :-2], p[1:-1, :-2], p[:-2, :-2],
            )
            count = np.zeros(image.shape, dtype=np.uint8)
            for neighbor in ring:
                count += neighbor
            transitions = np.zeros(image.shape, dtype=np.uint8)
            for index, neighbor in enumerate(ring):
                transitions += ~neighbor & ring[(index + 1) % 8]
            p2, p4, p6, p8 = ring[0], ring[2], ring[4], ring[6]
            if step == 0:
                keep = (p2 & p4 & p6) | (p4 & p6 & p8)
            else:
                keep = (p2 & p4 & p8) | (p2 & p6 & p8)
            remove = (
                image & (count >= 2) & (count <= 6) & (transitions == 1) & ~keep
            )
            if remove.any():
                image &= ~remove
                changed = True
        if not changed:
            break
    return image


def _trace_from_end(skeleton, x, y, length):
    """骨格の端点 ``(x, y)`` から線に沿って ``length`` 画素たどった座標列。

    分岐に当たったらそこで止める。細線化の階段部分では次の候補が隣り合って
    2つ出るので、そのときは遠い（斜め）方へ進む。
    """
    height, width = skeleton.shape
    path = [(x, y)]
    visited = {(x, y)}
    while len(path) < length:
        cx, cy = path[-1]
        candidates = [
            (nx, ny)
            for ny in range(cy - 1, cy + 2)
            for nx in range(cx - 1, cx + 2)
            if 0 <= nx < width and 0 <= ny < height
            and (nx, ny) not in visited and skeleton[ny, nx]
        ]
        if not candidates:
            break
        if len(candidates) > 1 and any(
            max(abs(ax - bx), abs(ay - by)) > 1
            for ax, ay in candidates for bx, by in candidates
        ):
            break
        visited.update(candidates)
        path.append(max(
            candidates, key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2
        ))
    return path


def _cast_ray(boundary, x, y, dx, dy, max_length):
    """端点から ``(dx, dy)`` 方向へ線を延ばした画素。届かなければ ``None``。

    自分の線の内側にいる間の境界は無視し、いったん外へ出てから別の境界に
    当たるか、キャンバスの外へ出たら止める。1 ステップで主軸方向へ 1 画素
    進むので、延長線は 8 連結になり 4 近傍の塗りを確実に止める。
    """
    height, width = boundary.shape
    major = max(abs(dx), abs(dy))
    step_x, step_y = dx / major, dy / major
    pixels = []
    left_own_line = False
    for t in range(1, max_length * 2 + 1):
        px = int(round(x + step_x * t))
        py = int(round(y + step_y * t))
        if not (0 <= px < width and 0 <= py < height):
            return pixels
        if boundary[py, px]:
            if left_own_line:
                return pixels
            continue
        left_own_line = True
        pixels.append((px, py))
        if len(pixels) > max_length:
            return None
    return None


def extend_line_ends(boundary, max_length):
    """キャンバス端の手前で途切れた線を、端まで延ばした画素の真偽マスク。

    作画では画面に映る範囲（スタンダードフレーム）の外まで素材を用意するが、
    フレームの外では線を端まで引かないことが多い。そのまま塗ると線の切れ目から
    隣の領域へ漏れるので、端点の向きのまま線を延ばした仮想の境界を返す。

    延ばすのは「その向きでキャンバス端まで ``max_length`` 以内」の端点だけで、
    途中で別の線に当たればそこで止める。絵の内側の端点（髪のハネなど）は
    延長しないので、ふつうの塗りには影響しない。

    :param boundary: 塗りを止める画素（線など）の真偽マスク。
    :returns: 延長線の画素。``boundary`` の画素は含まない。
    """
    height, width = boundary.shape
    extension = np.zeros((height, width), dtype=bool)
    max_length = max(1, int(max_length))
    # 端から max_length 以内の帯だけを細線化する（全体を細線化すると大きな
    # キャンバスで遅い）。向きを測るために少し内側まで含める。
    band = max_length + EXTEND_TRACE_LENGTH + EXTEND_THIN_ITERATIONS + 2
    strips = (
        (0, min(height, band), 0, width),
        (max(0, height - band), height, 0, width),
        (0, height, 0, min(width, band)),
        (0, height, max(0, width - band), width),
    )
    for y0, y1, x0, x1 in strips:
        part = boundary[y0:y1, x0:x1]
        if not part.any():
            continue
        skeleton = _thin(part, EXTEND_THIN_ITERATIONS)
        padded = np.pad(skeleton, 1, mode="constant", constant_values=False)
        neighbors = np.zeros(skeleton.shape, dtype=np.uint8)
        for oy in range(3):
            for ox in range(3):
                if ox != 1 or oy != 1:
                    neighbors += padded[
                        oy:oy + skeleton.shape[0], ox:ox + skeleton.shape[1]
                    ]
        end_ys, end_xs = np.nonzero(skeleton & (neighbors == 1))
        for local_x, local_y in zip(end_xs.tolist(), end_ys.tolist()):
            x, y = local_x + x0, local_y + y0
            if min(x, y, width - 1 - x, height - 1 - y) > max_length:
                continue
            path = _trace_from_end(
                skeleton, local_x, local_y, EXTEND_TRACE_LENGTH
            )
            if len(path) < EXTEND_MIN_TRACE:
                continue
            dx = float(path[0][0] - path[-1][0])
            dy = float(path[0][1] - path[-1][1])
            if dx == 0.0 and dy == 0.0:
                continue
            # その向きで端まで max_length 以内の端点だけを延ばす。届く前に
            # 別の線へ当たった場合はそこで止める。
            norm = (dx * dx + dy * dy) ** 0.5
            ux, uy = dx / norm, dy / norm
            to_edge = min(
                (width - 1 - x) / ux if ux > 0 else (x / -ux if ux < 0 else np.inf),
                (height - 1 - y) / uy if uy > 0 else (y / -uy if uy < 0 else np.inf),
            )
            if to_edge > max_length:
                continue
            ray = _cast_ray(boundary, x, y, dx, dy, max_length)
            if ray is None:
                continue
            for px, py in ray:
                extension[py, px] = True
    return extension


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


def fill_target_mask(pixels, target_color, category, packed=None):
    """``target_color`` の設定で、たどってよい画素の真偽マスクを返す。

    ``TARGET_ALL`` は従来どおり、開始点の色種別 ``category``（背景なら None）と
    同じ画素。それ以外は開始点の色に関係なく、設定で決まる画素。
    """
    if packed is None:
        packed = rgba_packed_view(pixels)
    if target_color == TARGET_ALL:
        if category is None:
            return background_mask_packed(packed)
        return opaque_rgb_mask_packed(packed, category)
    alpha = pixels[:, :, 3]
    rgb = pixels[:, :, :3]
    transparent = alpha == 0
    if target_color == TARGET_FAINT_AS_TRANSPARENT:
        return alpha <= FAINT_ALPHA_MAX
    if target_color in (TARGET_TRANSPARENT, TARGET_TRANSPARENT_AND_CLOSED_OPAQUE):
        return transparent
    if target_color in (
        TARGET_SURROUNDED_BY_TRANSPARENT, TARGET_OPAQUE_AND_INNER_TRANSPARENT,
    ):
        return ~transparent
    if target_color in (TARGET_BLACK, TARGET_SURROUNDED_BY_BLACK):
        black = ~transparent & (rgb.max(axis=2) <= BLACK_MAX)
        return black if target_color == TARGET_BLACK else ~black
    white_or_clear = transparent | (rgb.min(axis=2) >= WHITE_MIN)
    if target_color == TARGET_WHITE_TRANSPARENT:
        return white_or_clear
    return ~white_or_clear


def _enclosed_by(region):
    """``region`` の外側（キャンバス端につながる部分）以外の、region でない画素。"""
    outside_candidates = ~region
    edge = np.zeros_like(region)
    edge[0, :] = edge[-1, :] = True
    edge[:, 0] = edge[:, -1] = True
    ys, xs = np.nonzero(outside_candidates & edge)
    if not xs.size:
        return outside_candidates
    outside = scanline_connected_region(
        outside_candidates, zip(xs.tolist(), ys.tolist())
    )
    return outside_candidates & ~outside


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

    target_color = options.target_color
    if target_color == TARGET_ALL:
        target_mask = (
            pseudo_background.copy()
            if category is None
            else opaque_rgb_mask_packed(packed, category)
        )
    else:
        target_mask = fill_target_mask(pixels, target_color, category, packed)
        if not target_mask[start_y, start_x]:
            return FillResult(REASON_NOT_TARGET, target=category)
    if selection_mask is not None:
        target_mask &= selection_mask

    if options.adjacent:
        boundary = ~target_mask
        gap_recovery_mask = None
        if options.extend_lines:
            # 延長線は対象色の上に引く仮想の線なので、塗った後に回収する。
            extension = extend_line_ends(boundary, options.extend_length)
            if extension.any():
                boundary = boundary | extension
                gap_recovery_mask = extension
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
            # 塗らないが、どこまで開いていたかは返す（ドラッグ塗りが同じ領域を
            # 何度も判定しないため）。ok は偽のまま。
            return FillResult(REASON_OPEN_REGION, region=region, target=category)

        if gap_recovery_mask is not None:
            touching = dilate(region, 1) & gap_recovery_mask
            start_y_values, start_x_values = np.nonzero(touching)
            if start_x_values.size:
                recovered = scanline_connected_region(
                    gap_recovery_mask,
                    zip(start_x_values.tolist(), start_y_values.tolist()),
                )
                region |= recovered

        # 閉じ込められた穴・島も一緒に塗る対象色。
        if target_color == TARGET_OPAQUE_AND_INNER_TRANSPARENT:
            region |= _enclosed_by(region) & (pixels[:, :, 3] == 0)
        elif target_color == TARGET_TRANSPARENT_AND_CLOSED_OPAQUE:
            region |= _enclosed_by(region) & (pixels[:, :, 3] != 0)
    else:
        # 「隣接」OFFでは、対象色の画素（既定はクリック位置と同じ色）を一括で塗る。
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


#: 追跡で試す開始点の数の上限（1回ごとに領域判定が1回走る）。
TRACK_MAX_TRIES = 8
#: 前のコマの領域との重なり（IoU）がこれ未満なら、推測としても出さない。
TRACK_MIN_IOU = 0.1
#: これ以上重なっていれば、残りの候補を試さずに採用する。
TRACK_GOOD_IOU = 0.75
#: 前のコマの領域との面積比がこの倍率を超えたら別物（背景への漏れなど）とみなす。
TRACK_MAX_AREA_RATIO = 3.0
#: 候補の面積のうち、前のコマの範囲に収まっている割合がこれ以上なら、
#: 前のコマの領域が分かれた一部とみなしてまとめて塗る。
TRACK_INCLUDE_RATIO = 0.7


def track_fill_region(
    pixels,
    previous,
    reference,
    options: BucketOptions,
    selection_mask=None,
    previous_pixels=None,
    previous_lines=None,
) -> FillResult:
    """前のコマで塗った領域 ``previous`` を手がかりに、このコマで同じ図形を塗る。

    1. ``previous_pixels``（前のコマの画素）があれば、塗った範囲のまわりの線画を
       比べて、図形がどれだけ動いたかを推定し（:mod:`fill_tracking`）、前の範囲を
       そのぶん動かす。区画ごとにずらした範囲（回転や一部だけの動き用）も作る。
    2. 動かした範囲（と、推定が外れたときのために元の位置）と重なる閉じた領域を
       候補にして、塗ってみる。
    3. 重なり（IoU）が一番大きく、面積も大きく変わらない領域を選ぶ。背景へ漏れた
       領域は面積が跳ね上がるので弾かれる。
    4. シワや重なりで前のコマの1つの領域が今のコマでは何個かに分かれていると、
       どの1つも重なりが小さい。そこで、ほとんどが前の範囲に収まっている
       （前の範囲に「含まれる」）候補をまとめた範囲も比べ、重なりが大きければ
       そちらを採る。

    :param previous: 前のコマ（追跡の1つ手前）で塗った画素の真偽マスク。
    :param reference: 塗り始めたコマの開始点の色種別（:func:`seed_category`）。
    :param previous_pixels: ``previous`` を作ったコマの RGBA 配列。省略時はずれを推定しない。
    :param previous_lines: 前のコマの線画（塗りを止める画素）の真偽マスク。
        省略時は ``previous_pixels`` から対象色の設定で求める。
    :returns: 結果の ``score`` に重なりを入れる。自信がなくても候補があれば ok で返し、
        採用するかは呼び出し側が ``score`` で決める。候補が無ければ ``REASON_EMPTY``。
    """
    from . import fill_tracking

    previous_area = int(np.count_nonzero(previous))
    if previous_area == 0:
        return FillResult(REASON_EMPTY, target=reference)
    # 種の候補は、対象色の設定でたどれる画素（既定は開始点と同じ色）に限る。
    target = fill_target_mask(pixels, options.target_color, reference)
    if selection_mask is not None:
        target &= selection_mask

    references = [previous]
    if (
        previous_lines is None
        and previous_pixels is not None
        and previous_pixels.shape == pixels.shape
    ):
        previous_lines = ~fill_target_mask(
            previous_pixels, options.target_color, reference
        )
    if previous_lines is not None and previous_lines.shape == previous.shape:
        current_lines = ~target
        shifts = fill_tracking.estimate_shifts(previous_lines, current_lines, previous)
        # 動かした位置を先に試す。推定が外れたときのために元の位置も残す。
        moved = [
            fill_tracking.shift_mask(previous, dy, dx)
            for dy, dx in shifts if dy or dx
        ]
        warped = fill_tracking.warp_region(
            previous_lines, current_lines, previous,
            shifts[0] if shifts else (0, 0),
        )
        references = ([warped] if warped is not None else []) + moved + references

    eligible = np.zeros(target.shape, dtype=bool)
    for reference_mask in references:
        eligible |= target & reference_mask
    # 動かした範囲がキャンバスの外へ出てしまったときは、元の範囲の重心を使う。
    lead = references[0] if references[0].any() else previous
    center_y, center_x = (float(value) for value in np.argwhere(lead).mean(axis=0))
    reference_areas = [int(np.count_nonzero(mask)) for mask in references]

    def iou_with(mask, area, index):
        overlap = int(np.count_nonzero(mask & references[index]))
        return overlap / max(1, area + reference_areas[index] - overlap)

    # (結果, 面積, 手がかりごとの重なりの画素数)
    candidates = []
    best: Optional[FillResult] = None
    best_iou = 0.0
    for _attempt in range(TRACK_MAX_TRIES):
        points = np.argwhere(eligible)
        if not len(points):
            break
        ys, xs = points[:, 0], points[:, 1]
        # 動かした範囲の重心に近い点から試す。
        nearest = int(np.argmin((ys - center_y) ** 2 + (xs - center_x) ** 2))
        start_x, start_y = int(xs[nearest]), int(ys[nearest])
        result = compute_fill_region(
            pixels, start_x, start_y, options, selection_mask
        )
        if not result.ok:
            # 同じ領域から何度も試さないよう、その連続部分ごと候補から外す。
            eligible &= ~scanline_connected_region(
                eligible.copy(), (start_x, start_y)
            )
            continue
        region = result.mask
        eligible &= ~region
        area = int(np.count_nonzero(region))
        ratio = area / previous_area
        if ratio > TRACK_MAX_AREA_RATIO:
            continue
        overlaps = [
            int(np.count_nonzero(region & reference_mask))
            for reference_mask in references
        ]
        candidates.append((result, area, overlaps))
        if ratio < 1.0 / TRACK_MAX_AREA_RATIO:
            # 1つだけでは小さすぎるが、分かれた一部としてはまとめる候補に残す。
            continue
        iou = max(
            overlap / max(1, area + reference_area - overlap)
            for overlap, reference_area in zip(overlaps, reference_areas)
        )
        if iou > best_iou:
            best, best_iou = result, iou
            if iou >= TRACK_GOOD_IOU:
                break

    # 分かれた領域をまとめた範囲。手がかり（動かし方）ごとに作って比べる。
    if best_iou < TRACK_GOOD_IOU:
        for index in range(len(references)):
            members = [
                result for result, area, overlaps in candidates
                if overlaps[index] >= area * TRACK_INCLUDE_RATIO
            ]
            if len(members) < 2:
                continue
            union = np.zeros(target.shape, dtype=bool)
            for result in members:
                union |= result.mask
            iou = iou_with(union, int(np.count_nonzero(union)), index)
            if iou > best_iou:
                best_iou = iou
                best = FillResult(
                    REASON_OK, union, target=reference, bounds=mask_bounds(union)
                )
    if best is None or best_iou < TRACK_MIN_IOU:
        return FillResult(REASON_EMPTY, target=reference, score=best_iou)
    best.score = best_iou
    return best
