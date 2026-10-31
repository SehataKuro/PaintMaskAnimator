"""線のまわりの色ノイズ除去（Issue #19）。

線の近くには、まわりがほとんど黒なのに1ドットだけ黄色、といった孤立した異色
ドットが残る。取り込んだ素材の圧縮ノイズや、減色でどちらとも付かなかった画素が
元になる。これを近傍の多数決でまわりの色へ寄せる。

意図して置いた色面を壊さないよう、対象は「線の近く」かつ「まわりと色が違う」
画素だけに限る。Qt には依存しない素の numpy 配列として扱う。
"""
import numpy as np

#: 黒い線とみなす明るさの上限（0-255）。色トレス線も拾えるよう少し高め。
DEFAULT_LINE_LUMA = 110
#: 線からこの距離（画素）までを「線の近く」とする。
DEFAULT_RADIUS = 2
#: 8近傍のうち、この数以上が同じ色なら多数派とみなす。
DEFAULT_MAJORITY = 5
#: 8近傍のうち、自分と同じ色がこの数以下なら「孤立している」とみなす。
#: 0 にすると、まわりに同じ色が1つも無い本当の1ドットだけが対象になる。
#: 色面のふちは必ず同じ色の隣をもつので、これで塗り分けを壊さずに済む。
DEFAULT_ISOLATION = 0

#: 8近傍のずらし方（dy, dx）。
_OFFSETS = (
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1), (0, 1),
    (1, -1), (1, 0), (1, 1),
)


def _packed(rgba):
    """RGB を 24bit に詰め、透明画素は「色なし」の番兵にする。"""
    packed = (
        (rgba[:, :, 0].astype(np.uint32) << 16)
        | (rgba[:, :, 1].astype(np.uint32) << 8)
        | rgba[:, :, 2].astype(np.uint32)
    )
    # 透明は 24bit に収まらない値にして、どの色とも一致しないようにする。
    return np.where(rgba[:, :, 3] == 0, np.uint32(0x1000000), packed)


def _shifted(values, dy, dx, fill):
    """``values`` を (dy, dx) だけずらした配列。外側は ``fill`` で埋める。"""
    result = np.full_like(values, fill)
    height, width = values.shape
    source_y = slice(max(0, -dy), height - max(0, dy))
    source_x = slice(max(0, -dx), width - max(0, dx))
    target_y = slice(max(0, dy), height - max(0, -dy))
    target_x = slice(max(0, dx), width - max(0, -dx))
    result[target_y, target_x] = values[source_y, source_x]
    return result


def _dilate(mask, radius):
    """``mask`` を上下左右へ ``radius`` 回広げる（チェビシェフ距離）。"""
    grown = np.asarray(mask, dtype=bool)
    for _ in range(max(0, int(radius))):
        padded = grown.copy()
        padded[:-1, :] |= grown[1:, :]
        padded[1:, :] |= grown[:-1, :]
        padded[:, :-1] |= grown[:, 1:]
        padded[:, 1:] |= grown[:, :-1]
        padded[:-1, :-1] |= grown[1:, 1:]
        padded[1:, 1:] |= grown[:-1, :-1]
        padded[:-1, 1:] |= grown[1:, :-1]
        padded[1:, :-1] |= grown[:-1, 1:]
        grown = padded
    return grown


def line_mask(rgba, line_luma=DEFAULT_LINE_LUMA):
    """線とみなす画素。不透明で、じゅうぶん暗いもの。"""
    luma = rgba[:, :, :3].astype(np.uint16).sum(axis=2) // 3
    return (rgba[:, :, 3] > 0) & (luma <= int(line_luma))


def remove_line_noise(
    rgba,
    *,
    line_luma=DEFAULT_LINE_LUMA,
    radius=DEFAULT_RADIUS,
    majority=DEFAULT_MAJORITY,
    isolation=DEFAULT_ISOLATION,
):
    """線の近くの孤立した異色ドットを、8近傍の多数決で塗り替える。

    対象は「線の近く」かつ「まわりに自分と同じ色が ``isolation`` 個以下」の画素
    だけ。色面のふちは同じ色の隣を必ず持つので、塗り分けは壊れない。

    :param rgba: ``(H, W, 4)`` uint8。書き換えず、新しい配列を返す。
    :returns: ``(塗り替えた配列, 塗り替えた画素数)``。
    """
    rgba = np.asarray(rgba, dtype=np.uint8)
    if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.shape[0] < 3 or rgba.shape[1] < 3:
        return rgba, 0

    outside = np.uint32(0x2000000)
    packed = _packed(rgba)
    neighbours = [_shifted(packed, dy, dx, outside) for dy, dx in _OFFSETS]

    # 近傍どうしを突き合わせ、同じ色の数が最も多い近傍を選ぶ（多数決）。
    counts = np.zeros((len(neighbours),) + packed.shape, dtype=np.uint8)
    for index, value in enumerate(neighbours):
        valid = value != outside
        for other in neighbours:
            counts[index] += (valid & (other == value)).astype(np.uint8)

    best = counts.argmax(axis=0)
    best_count = counts.max(axis=0)
    stacked = np.stack(neighbours, axis=0)
    winner = np.take_along_axis(stacked, best[None, :, :], axis=0)[0]

    own_neighbours = np.zeros(packed.shape, dtype=np.uint8)
    for value in neighbours:
        own_neighbours += ((value != outside) & (value == packed)).astype(np.uint8)

    noisy = (
        (rgba[:, :, 3] > 0)
        & (own_neighbours <= int(isolation))
        & (best_count >= int(majority))
        & (winner != packed)
        & (winner != outside)
        # 多数派が透明なら消さない。線まわりの色だけを寄せる。
        & (winner != np.uint32(0x1000000))
        # 線そのものは動かさない。
        & ~line_mask(rgba, line_luma)
        & _dilate(line_mask(rgba, line_luma), radius)
    )
    changed = int(np.count_nonzero(noisy))
    if not changed:
        return rgba, 0

    result = rgba.copy()
    result[:, :, 0][noisy] = ((winner >> 16) & 0xFF).astype(np.uint8)[noisy]
    result[:, :, 1][noisy] = ((winner >> 8) & 0xFF).astype(np.uint8)[noisy]
    result[:, :, 2][noisy] = (winner & 0xFF).astype(np.uint8)[noisy]
    result[:, :, 3][noisy] = 255
    return result, changed
