"""2値の線の形を、仕上げで正しいとされる形へ整える（#64）。

Issue #64 の4パターンを扱う。

1. **角だけで接している段をつなぐ（足す）**：階段状の線で、段（2画素以上の横か
   縦の並び）どうしが斜めの角1点だけで接しているとき、角の1画素を足して上下左右で
   つながった線にする。1画素幅の45°の線は段を持たないので変えない。
2. **線から突き出た1画素を消す（ヒゲ）**：線の側面に、根元の3画素だけで付いている
   1画素。1画素幅の線の先端（となりが1画素だけ）や、分岐・交差（となりが4画素以上）
   とは形で見分けられる。

3. **段の重なりが厚くなった所の1画素を消す**：1画素幅の階段で、となり合う段（横の
   並び）どうしが2画素ぶん重なっていると、その所だけ線が太く見える。片方の段の端を
   1画素消して、重なりを1画素にする。長い方の段から消し、同じなら下（先）の段の
   始まりを消す。

4. **2×2の塊が角でつながった45°の線をそろえる**：直し方は2通りから選ぶ。
   * 足す（``"add"``）：塊と塊（や段）が角だけで接している所の、角の両側に1画素ずつ
     足す。各行が同じ数の画素で1列ずつずれる、太さのそろった階段になる。
   * 消す（``"remove"``）：塊の、つながる向きから外れた2画素を消す。1画素幅の45°の
     線になる。
   規則1（角に1画素足す）とぶつかる所は、こちらを先に当ててから規則1を見る。

マスク（線の画素が真）を受け取り、足す画素と消す画素のマスクを返す純粋な処理。
すべての画素を同時に判定するので、結果は走査の順番によらない。
"""
from typing import Tuple

import numpy as np


def _shift(mask: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """``out[y, x] = mask[y + dy, x + dx]``（外は偽）。"""
    height, width = mask.shape
    out = np.zeros_like(mask)
    ys = slice(max(0, -dy), min(height, height - dy))
    xs = slice(max(0, -dx), min(width, width - dx))
    yd = slice(max(0, dy), min(height, height + dy))
    xd = slice(max(0, dx), min(width, width + dx))
    out[ys, xs] = mask[yd, xd]
    return out


def _neighbour_count(mask: np.ndarray) -> np.ndarray:
    count = np.zeros(mask.shape, dtype=np.int8)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                count += _shift(mask, dy, dx)
    return count


def corner_joins(mask: np.ndarray) -> np.ndarray:
    """パターン1：足す画素。段と段が角だけで接している所の、角の1画素。

    ``p`` と ``p + (dy, dx)``（斜め）が線で、間の2画素 ``a = p + (0, dx)``・
    ``b = p + (dy, 0)`` がどちらも線でなく、``p`` の側も相手の側も段の端（逆向きに
    横か縦のとなりがある）であるとき、``a`` か ``b`` を足す。まわりに線の多い方
    （階段の内側）を選び、同じなら横の段を延ばす ``a`` にする。
    """
    mask = mask.astype(bool)
    neighbours = _neighbour_count(mask)
    add = np.zeros_like(mask)
    for dy, dx in ((1, 1), (1, -1)):
        q = _shift(mask, dy, dx)
        a = _shift(mask, 0, dx)
        b = _shift(mask, dy, 0)
        # p の側の段：p の左右（相手と逆向き）か上下（相手と逆向き）が線。
        p_run = _shift(mask, 0, -dx) | _shift(mask, -dy, 0)
        # 相手の側の段：q の先（同じ向き）に横か縦のとなりがある。
        q_run = _shift(mask, dy, 2 * dx) | _shift(mask, 2 * dy, dx)
        found = mask & q & ~a & ~b & p_run & q_run
        if not found.any():
            continue
        ys, xs = np.nonzero(found)
        ay, ax = ys, xs + dx
        by, bx = ys + dy, xs
        prefer_b = neighbours[by, bx] > neighbours[ay, ax]
        add[np.where(prefer_b, by, ay), np.where(prefer_b, bx, ax)] = True
    return add & ~mask


def spurs(mask: np.ndarray) -> np.ndarray:
    """パターン2：消す画素。線の側面から1画素だけ突き出たもの。

    となりの8画素のうち線なのが、ある1辺に並ぶ3画素（根元）だけで、ほかの5画素が
    線でない画素。先端（となり1画素）・角（2画素）・分岐（4画素以上）は当たらない。
    """
    mask = mask.astype(bool)
    neighbours = _neighbour_count(mask)
    out = np.zeros_like(mask)
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        # 根元の3画素：向き (dy, dx) の辺に並ぶ3画素。
        py, px = dx, dy  # 辺に沿う向き
        root = (
            _shift(mask, dy, dx)
            & _shift(mask, dy + py, dx + px)
            & _shift(mask, dy - py, dx - px)
        )
        out |= mask & root & (neighbours == 3)
    return out


def _run_lengths(mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """各画素から左へ／右へ、同じ行で続く線の画素数（自分を含む）。"""
    height, width = mask.shape
    left = np.zeros((height, width), dtype=np.int32)
    right = np.zeros((height, width), dtype=np.int32)
    running = np.zeros(height, dtype=np.int32)
    for x in range(width):
        running = np.where(mask[:, x], running + 1, 0)
        left[:, x] = running
    running[:] = 0
    for x in range(width - 1, -1, -1):
        running = np.where(mask[:, x], running + 1, 0)
        right[:, x] = running
    return left, right


def _thick_steps_canonical(mask: np.ndarray) -> np.ndarray:
    """右下がりの横の段で、上の段の終わり e と下の段が2画素重なる所の、消す画素。

    上の段：(y, e-2)〜(y, e) が線、(y, e+1) は線でない。
    下の段：(y+1, e-1)〜(y+1, e+1) が線、(y+1, e-2) は線でない（ここから始まる）。
    どちらの段も1画素幅：(y-1, e-1)・(y-1, e)・(y+2, e-1)・(y+2, e) は線でない。
    """
    s = _shift
    # 2×2 の塊（パターン4）と見分けるため、上の段は重なりより左へ、下の段は右へ続く。
    upper_end = mask & s(mask, 0, -1) & s(mask, 0, -2) & ~s(mask, 0, 1)
    lower = s(mask, 1, 0) & s(mask, 1, -1) & s(mask, 1, 1) & ~s(mask, 1, -2)
    thin = ~s(mask, -1, 0) & ~s(mask, -1, -1) & ~s(mask, 2, 0) & ~s(mask, 2, -1)
    found = upper_end & lower & thin
    remove = np.zeros_like(mask)
    if not found.any():
        return remove
    left, right = _run_lengths(mask)
    ys, xs = np.nonzero(found)
    upper_length = left[ys, xs]
    lower_length = right[ys + 1, xs - 1]
    take_upper = upper_length > lower_length
    remove[np.where(take_upper, ys, ys + 1), np.where(take_upper, xs, xs - 1)] = True
    return remove


def _orientations():
    """(向きを変える関数, 戻す関数) の8通り（90°回転と左右反転の組み合わせ）。"""
    for k in range(4):
        yield (lambda m, k=k: np.rot90(m, k)), (lambda m, k=k: np.rot90(m, -k))
        yield (lambda m, k=k: np.fliplr(np.rot90(m, k))), (lambda m, k=k: np.rot90(np.fliplr(m), -k))


def thick_steps(mask: np.ndarray) -> np.ndarray:
    """パターン3：消す画素。段の重なりが2画素ある所の、片方の段の端。

    8つの向きで順に調べ、見つけた所はその場で消してから次の向きへ進む（同じ重なりを
    向きを変えて二度直さない）。
    """
    current = mask.astype(bool).copy()
    removed = np.zeros_like(current)
    for forward, back in _orientations():
        found = back(_thick_steps_canonical(np.ascontiguousarray(forward(current))))
        found &= current
        current &= ~found
        removed |= found
    return removed


#: パターン4の直し方。
DIAGONAL_MODES = ("add", "remove", None)


def _diagonal_blocks_canonical(mask: np.ndarray, mode: str) -> Tuple[np.ndarray, np.ndarray]:
    """右下がりに角でつながった 2×2 の塊（左上の画素の位置で見る）を直す。

    塊 ``(y, x)``〜``(y+1, x+1)`` がすべて線で、そのまわりの8画素（上下左右の辺に
    接する画素）が線でなく、左上 ``(y-1, x-1)`` か右下 ``(y+2, x+2)`` の少なくとも
    片方が線（角でつながっている）とき、パターン4とみなす。
    """
    s = _shift
    block = mask & s(mask, 0, 1) & s(mask, 1, 0) & s(mask, 1, 1)
    sides = (
        s(mask, -1, 0) | s(mask, -1, 1) | s(mask, 0, -1) | s(mask, 1, -1)
        | s(mask, 2, 0) | s(mask, 2, 1) | s(mask, 0, 2) | s(mask, 1, 2)
    )
    before = s(mask, -1, -1)
    after = s(mask, 2, 2)
    found = block & ~sides & (before | after)
    add = np.zeros_like(mask)
    remove = np.zeros_like(mask)
    height, width = mask.shape
    for y, x in zip(*np.nonzero(found)):
        y, x = int(y), int(x)
        if mode == "remove":
            remove[y, x + 1] = True
            remove[y + 1, x] = True
            continue
        if before[y, x]:
            add[y - 1, x] = True
            add[y, x - 1] = True
        if after[y, x]:
            if x + 2 < width:
                add[y + 1, x + 2] = True
            if y + 2 < height:
                add[y + 2, x + 1] = True
    return add & ~mask, remove


def diagonal_blocks(mask: np.ndarray, mode: str | None = "add") -> Tuple[np.ndarray, np.ndarray]:
    """パターン4：``(足す画素, 消す画素)``。右下がりと右上がりの両方を見る。"""
    current = mask.astype(bool).copy()
    added = np.zeros_like(current)
    removed = np.zeros_like(current)
    if mode not in ("add", "remove"):
        return added, removed
    for flip in (False, True):
        view = np.fliplr(current) if flip else current
        add, remove = _diagonal_blocks_canonical(np.ascontiguousarray(view), mode)
        if flip:
            add, remove = np.fliplr(add), np.fliplr(remove)
        remove &= current
        current = (current | add) & ~remove
        added |= add
        removed |= remove
    return added & ~removed, removed


def tidy_mask(mask: np.ndarray, diagonal: str | None = "add") -> Tuple[np.ndarray, np.ndarray]:
    """``(足す画素, 消す画素)``。両方を当てた線は ``(mask | add) & ~remove``。

    ``diagonal`` はパターン4の直し方（``"add"`` / ``"remove"`` / ``None`` で当てない）。
    """
    mask = mask.astype(bool)
    remove = spurs(mask)
    add, diagonal_remove = diagonal_blocks(mask & ~remove, diagonal)
    remove |= diagonal_remove
    remove |= thick_steps((mask | add) & ~remove)
    current = (mask | add) & ~remove
    add |= corner_joins(current)
    return add & ~mask, remove


def _boundary_colour(rgba: np.ndarray, y: int, x: int, exclude) -> Tuple[int, ...]:
    """``(y, x)`` の上下左右で、線の色 ``exclude`` ではない色のうち一番多い色。"""
    height, width = rgba.shape[:2]
    found = {}
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        ny, nx = y + dy, x + dx
        if 0 <= ny < height and 0 <= nx < width:
            pixel = tuple(int(v) for v in rgba[ny, nx])
            if pixel[3] == 0:
                pixel = (0, 0, 0, 0)
            elif pixel[:3] == tuple(exclude):
                continue
            found[pixel] = found.get(pixel, 0) + 1
    if not found:
        return (0, 0, 0, 0)
    return max(found.items(), key=lambda item: item[1])[0]


def tidy_lines(
    rgba: np.ndarray, colours, diagonal: str | None = "add",
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``colours`` の線を整えた RGBA と、足した画素・消した画素のマスク。

    足した画素は線の色になり、消した画素は、となりにある線以外の色（無ければ透明）に
    なる。色ごとに別々に判定する。
    """
    out = rgba.copy()
    added = np.zeros(rgba.shape[:2], dtype=bool)
    removed = np.zeros(rgba.shape[:2], dtype=bool)
    opaque = rgba[:, :, 3] > 0
    for colour in colours:
        rgb = tuple(int(v) for v in colour[:3])
        mask = opaque & (rgba[:, :, :3] == np.array(rgb, dtype=np.uint8)).all(axis=2)
        if not mask.any():
            continue
        add, remove = tidy_mask(mask, diagonal)
        # ほかの線の色の上には足さない（色トレス線と主線が接する所など）。
        others = np.zeros_like(mask)
        for other in colours:
            other_rgb = tuple(int(v) for v in other[:3])
            if other_rgb != rgb:
                others |= opaque & (rgba[:, :, :3] == np.array(other_rgb, dtype=np.uint8)).all(axis=2)
        add &= ~others
        for y, x in zip(*np.nonzero(remove)):
            out[y, x] = _boundary_colour(rgba, int(y), int(x), rgb)
        out[add, :3] = rgb
        out[add, 3] = 255
        added |= add
        removed |= remove
    return out, added, removed
