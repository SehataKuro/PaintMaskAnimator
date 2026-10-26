"""串刺し塗りの追跡で使う、コマ間のずれの推定（UI 非依存）。

前のコマで塗った範囲と今のコマの図形が重なっていることを前提にすると、
図形がその幅ほど動いたコマで追跡が切れる。そこで、塗った範囲のまわりの
線画（塗る対象でない画素）を前のコマと今のコマで比べ、どれだけずれたかを
位相相関で求める。前の範囲をそのぶん動かしてから比べれば、大きく動いても
重なりが保てる。

図形は平行移動するだけでなく、回転したり一部だけ動いたりもする（腕を振る、
髪がなびくなど）。全体を1つのずれで動かすと片側しか合わないので、
:func:`warp_region` で範囲を区画に分け、区画ごとのずれで動かした範囲も作る
（動きの推定を画素ごとに行う方法の、軽い近似）。

numpy 配列だけを扱う純粋な関数にしてあるので、あとで Rust の部品に置き換えやすい
（``docs/design-principles.md``「将来は Rust へ置き換えていく」）。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

#: ずれを数える窓の一辺の上限。これより大きい窓は間引いて計算する。
#: ずれは粗くてよい（最後は実際に塗って重なりで選ぶ）ので、速さを優先する。
_MAX_WINDOW = 256

#: :func:`warp_region` の区画の一辺の目安（px）。範囲がこれより小さい向きは分けない。
LOCAL_TILE = 32
#: 1つの向きに分ける区画の数の上限。
LOCAL_MAX_TILES = 6
#: 区画ごとに探す、全体のずれからの追加のずれの範囲（px）。範囲の大きさの
#: 1/4 を目安に、この間に収める。全体のずれは範囲の片側に合っていることが
#: あるので、反対側の区画はそのぶん大きくずれている。
LOCAL_MIN_RADIUS = 6
LOCAL_MAX_RADIUS = 64
#: 区画の縁の線がこれより少ないと、ずれを測れないので全体のずれを使う。
LOCAL_MIN_TEMPLATE = 6
#: 相関の山の高さが一番高い山のこの割合以上なら同じくらいそれらしいとみなし、
#: ずれの小さいものを選ぶ。まっすぐな線は線に沿ったずれが決まらず、同じ形が
#: 並んでいると同じ高さの山がいくつも出るため。
PEAK_TOLERANCE = 0.9


def mask_bounds(mask) -> Optional[Tuple[int, int, int, int]]:
    """真の画素を囲む ``(x0, y0, x1, y1)``（x1, y1 は排他）。無ければ None。"""
    rows = np.flatnonzero(mask.any(axis=1))
    if not rows.size:
        return None
    cols = np.flatnonzero(mask.any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def shift_mask(mask, dy, dx):
    """``mask`` を (dy, dx) だけ動かす。はみ出した分は捨て、空いた所は偽。"""
    height, width = mask.shape
    out = np.zeros_like(mask)
    if abs(dy) >= height or abs(dx) >= width:
        return out
    src_y = slice(max(0, -dy), height - max(0, dy))
    dst_y = slice(max(0, dy), height - max(0, -dy))
    src_x = slice(max(0, -dx), width - max(0, dx))
    dst_x = slice(max(0, dx), width - max(0, -dx))
    out[dst_y, dst_x] = mask[src_y, src_x]
    return out


def _outline(region, lines, width=3):
    """``region`` の縁に接する線画だけを残す（遠くの動かない線を比べないため）。"""
    from .bucket_fill import dilate

    return lines & dilate(region, width) & ~region


def estimate_shifts(previous_lines, current_lines, region, count=3, margin=None):
    """``region`` の縁の線画が、前のコマから今のコマへどれだけ動いたかの候補。

    相関の山の高い順に ``count`` 個まで ``(dy, dx)`` を返す（一番高い山とほぼ同じ
    高さの山どうしは、ずれの小さい順）。図形の縁の線
    （塗った範囲に接する線）だけを型にするので、近くにある動かない線に
    引きずられにくい。どれが正しいかは、呼び出し側が実際に塗って比べて決める。

    :param previous_lines: 前のコマの線画（塗る対象でない画素）の真偽マスク。
    :param current_lines: 今のコマの線画の真偽マスク。
    :param region: 前のコマで塗った範囲。
    :param margin: 探すずれの最大。省略時は範囲の大きさの3倍。
    """
    bounds = mask_bounds(region)
    if bounds is None:
        return []
    height, width = region.shape
    x0, y0, x1, y1 = bounds
    if margin is None:
        margin = max(64, int(max(x1 - x0, y1 - y0) * 3))
    wx0, wy0 = max(0, x0 - margin), max(0, y0 - margin)
    wx1, wy1 = min(width, x1 + margin), min(height, y1 + margin)
    if wx1 - wx0 < 8 or wy1 - wy0 < 8:
        return []
    template = _outline(region, previous_lines)
    step = max(1, int(np.ceil(max(wx1 - wx0, wy1 - wy0) / _MAX_WINDOW)))
    before = _downsample(template[wy0:wy1, wx0:wx1], step)
    after = _downsample(current_lines[wy0:wy1, wx0:wx1], step)
    if not before.any() or not after.any():
        return []
    correlation, size = _correlate(before, after)
    limit_y, limit_x = margin // step, margin // step
    # (山の高さ, ずれ)。同じ形が並んでいると同じ高さの山がいくつも出るので、
    # 多めに集めてから選ぶ。
    peaks = []
    for _ in range(count * 4):
        peak_y, peak_x = np.unravel_index(int(np.argmax(correlation)), correlation.shape)
        height_value = float(correlation[peak_y, peak_x])
        if height_value <= 0:
            break
        dy = int(peak_y) - size[0] if peak_y >= size[0] // 2 else int(peak_y)
        dx = int(peak_x) - size[1] if peak_x >= size[1] // 2 else int(peak_x)
        # 同じ山の近くを次の候補にしないよう、まわりを消しておく。
        for y in range(peak_y - 2, peak_y + 3):
            for x in range(peak_x - 2, peak_x + 3):
                correlation[y % size[0], x % size[1]] = -np.inf
        if abs(dy) <= limit_y and abs(dx) <= limit_x:
            peaks.append((height_value, (dy * step, dx * step)))
    if not peaks:
        return []
    # 一番高い山とほぼ同じ高さの山（同じ形の隣の図形など）が並ぶときは、
    # 動きの小さいものを先にする。コマ間の動きは小さいほうがそれらしい。
    top = peaks[0][0]
    near = [shift for value, shift in peaks if value >= top * PEAK_TOLERANCE]
    rest = [shift for value, shift in peaks if value < top * PEAK_TOLERANCE]
    near.sort(key=lambda shift: shift[0] ** 2 + shift[1] ** 2)
    return (near + rest)[:count]


def _downsample(mask, step):
    """``step`` 画素四方ごとに、1つでも真なら真にして縮める（float32）。

    1画素おきに間引くと、1画素幅の線が消えたり残ったりしてずれが測れなくなる。
    """
    if step <= 1:
        return mask.astype(np.float32)
    rows, cols = mask.shape
    padded = np.zeros((-(-rows // step) * step, -(-cols // step) * step), dtype=bool)
    padded[:rows, :cols] = mask
    blocks = padded.reshape(padded.shape[0] // step, step, padded.shape[1] // step, step)
    return blocks.any(axis=(1, 3)).astype(np.float32)


def _fast_length(n):
    """``n`` 以上で、素因数が 2・3・5 だけの長さ（FFT が速い長さ）。"""
    n = max(1, int(n))
    while True:
        m = n
        for factor in (2, 3, 5):
            while m % factor == 0:
                m //= factor
        if m == 1:
            return n
        n += 1


def _correlate(before, after, reach=None):
    """``after`` が ``before`` からどれだけずれると一番重なるかの相関の表。

    線画は「線か、線でないか」の2値なので、輪郭と今のコマの線が一番多く
    重なるずれを数える（相互相関）。ゼロで埋めて広げ、回り込みを防ぐ。
    表の添字 ``(i, j)`` は、サイズの半分以上なら負のずれを表す。

    :param reach: 調べるずれの最大。指定すると、そのぶんだけ広げる（速い）。
    """
    rows, cols = before.shape
    if reach is None:
        size = (2 * rows, 2 * cols)
    else:
        size = (_fast_length(rows + 2 * reach + 1), _fast_length(cols + 2 * reach + 1))
    correlation = np.fft.irfft2(
        np.fft.rfft2(after, size) * np.conj(np.fft.rfft2(before, size)), size
    )
    return correlation, size


def _crop(mask, y0, y1, x0, x1):
    """``mask[y0:y1, x0:x1]`` を切り出す。キャンバスの外は偽で埋める。"""
    height, width = mask.shape
    out = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    sy0, sy1 = max(0, y0), min(height, y1)
    sx0, sx1 = max(0, x0), min(width, x1)
    if sy0 < sy1 and sx0 < sx1:
        out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = mask[sy0:sy1, sx0:sx1]
    return out


def _paste(out, part, y, x):
    """``part`` を ``out`` の ``(y, x)`` を左上にして重ねる（はみ出しは捨てる）。"""
    height, width = out.shape
    rows, cols = part.shape
    dy0, dy1 = max(0, y), min(height, y + rows)
    dx0, dx1 = max(0, x), min(width, x + cols)
    if dy0 < dy1 and dx0 < dx1:
        out[dy0:dy1, dx0:dx1] |= part[dy0 - y:dy1 - y, dx0 - x:dx1 - x]


def _best_offset(template, target, limit):
    """``template`` を ``target`` に重ねる、``limit`` 以内のずれ ``(dy, dx)``。

    同じくらい重なるずれが並ぶとき（まっすぐな線の一部など）は、一番小さい
    ずれを選ぶ。測れなければ ``(0, 0)``。
    """
    if int(np.count_nonzero(template)) < LOCAL_MIN_TEMPLATE or not target.any():
        return 0, 0
    step = max(1, int(np.ceil(max(template.shape) / _MAX_WINDOW)))
    before = _downsample(template, step)
    after = _downsample(target, step)
    reach = limit // step
    correlation, size = _correlate(before, after, reach)
    offset_y = np.arange(size[0])
    offset_y = np.where(offset_y >= size[0] // 2, offset_y - size[0], offset_y)
    offset_x = np.arange(size[1])
    offset_x = np.where(offset_x >= size[1] // 2, offset_x - size[1], offset_x)
    allowed = (np.abs(offset_y)[:, None] <= reach) & (np.abs(offset_x)[None, :] <= reach)
    correlation = np.where(allowed, correlation, -np.inf)
    peak = float(correlation.max())
    if peak <= 0:
        return 0, 0
    ys, xs = np.nonzero(correlation >= peak * PEAK_TOLERANCE)
    distance = offset_y[ys] ** 2 + offset_x[xs] ** 2
    nearest = int(np.argmin(distance))
    return int(offset_y[ys[nearest]]) * step, int(offset_x[xs[nearest]]) * step


def warp_region(previous_lines, current_lines, region, shift=(0, 0)):
    """``region`` を区画に分け、区画ごとに今のコマの線へ合わせて動かした範囲。

    まず全体を ``shift`` だけ動かしたとみなし、そこから区画ごとに、その区画の
    縁の線が一番よく重なる追加のずれを探す。回転や一部だけの動きでも、範囲の
    両端がそれぞれ合う。区画の境目には小さな隙間や重なりができるが、追跡では
    塗り直す領域を選ぶ手がかりにしか使わないので問題ない。

    :param shift: 全体のずれ ``(dy, dx)``（:func:`estimate_shifts` の結果など）。
    :returns: 動かした範囲の真偽マスク。範囲が小さく分けられないときは None。
    """
    from .bucket_fill import dilate

    bounds = mask_bounds(region)
    if bounds is None:
        return None
    x0, y0, x1, y1 = bounds
    columns = min(LOCAL_MAX_TILES, max(1, (x1 - x0) // LOCAL_TILE))
    rows = min(LOCAL_MAX_TILES, max(1, (y1 - y0) // LOCAL_TILE))
    if columns * rows < 2:
        return None
    base_y, base_x = (int(value) for value in shift)
    xs = np.linspace(x0, x1, columns + 1).round().astype(int)
    ys = np.linspace(y0, y1, rows + 1).round().astype(int)
    radius = max(
        LOCAL_MIN_RADIUS, min(LOCAL_MAX_RADIUS, max(x1 - x0, y1 - y0) // 4)
    )
    out = np.zeros_like(region)
    for ty0, ty1 in zip(ys[:-1].tolist(), ys[1:].tolist()):
        for tx0, tx1 in zip(xs[:-1].tolist(), xs[1:].tolist()):
            part = region[ty0:ty1, tx0:tx1]
            if not part.any():
                continue
            wy0, wy1 = ty0 - radius, ty1 + radius
            wx0, wx1 = tx0 - radius, tx1 + radius
            # 区画の部分だけを窓に置き、その縁に接する前のコマの線を型にする。
            # 区画の切れ目（塗った範囲の内側）には線が無いので型に入らない。
            local = np.zeros((wy1 - wy0, wx1 - wx0), dtype=bool)
            local[radius:radius + part.shape[0], radius:radius + part.shape[1]] = part
            template = _crop(previous_lines, wy0, wy1, wx0, wx1)
            template &= dilate(local, 3) & ~local
            target = _crop(
                current_lines, wy0 + base_y, wy1 + base_y, wx0 + base_x, wx1 + base_x
            )
            dy, dx = _best_offset(template, target, radius)
            _paste(out, part, ty0 + base_y + dy, tx0 + base_x + dx)
    return out


def estimate_shift(previous_lines, current_lines, region, margin=None):
    """一番それらしいずれ ``(dy, dx)``。推定できなければ ``(0, 0)``。"""
    shifts = estimate_shifts(previous_lines, current_lines, region, 1, margin)
    return shifts[0] if shifts else (0, 0)
