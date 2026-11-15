"""取り込みの段階としてのゴミ取り：まわりから離れた小さな点を消す（#10）。

スキャンや圧縮で入る小さな点（ゴミ）は、2値化したあとも1〜数画素の塊として残る。
ここでは、ほかの絵に8近傍で1画素も触れていない、``max_pixels`` 画素以下の塊だけを
透明にする。線や色面にくっついた点（瞳のハイライト、線の先の点など）は触らない。

塊は不透明な画素の4近傍のつながりで分ける（:func:`native.label_regions`）。純粋な
numpy の処理（ラベル付けだけ Rust）。
"""
from typing import Tuple

import numpy as np

from . import native

#: 既定で消す塊の大きさ（画素）。
DEFAULT_MAX_PIXELS = 3

_NEIGHBOURS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def _shifted(labels: np.ndarray, dy: int, dx: int) -> np.ndarray:
    height, width = labels.shape
    out = np.zeros_like(labels)
    out[max(0, -dy):height - max(0, dy), max(0, -dx):width - max(0, dx)] = labels[
        max(0, dy):height - max(0, -dy), max(0, dx):width - max(0, -dx)
    ]
    return out


def isolated_specks(rgba: np.ndarray, max_pixels: int = DEFAULT_MAX_PIXELS) -> np.ndarray:
    """消す画素のマスク：ほかの絵から離れた ``max_pixels`` 画素以下の塊。"""
    opaque = rgba[:, :, 3] > 0
    if max_pixels <= 0 or not opaque.any():
        return np.zeros(opaque.shape, dtype=bool)
    codes = np.where(opaque, np.uint32(1), np.uint32(native.INVALID_CODE)).astype(np.uint32)
    labels, count = native.label_regions(codes)
    sizes = np.bincount(labels.ravel(), minlength=count + 1)
    small = sizes <= int(max_pixels)
    small[0] = False
    if not small.any():
        return np.zeros(opaque.shape, dtype=bool)
    # 斜めにだけ触れている別の塊があれば、離れていない（線の先の点など）。
    touching = np.zeros(count + 1, dtype=bool)
    for dy, dx in _NEIGHBOURS:
        other = _shifted(labels, dy, dx)
        contact = (labels > 0) & (other > 0) & (other != labels)
        touching[labels[contact]] = True
    remove = small & ~touching
    return remove[labels]


def remove_isolated_specks(
    rgba: np.ndarray, max_pixels: int = DEFAULT_MAX_PIXELS,
) -> Tuple[np.ndarray, int]:
    """離れた小さな点を透明にした RGBA と、消した画素の数。"""
    mask = isolated_specks(rgba, max_pixels)
    out = rgba.copy()
    out[mask] = 0
    return out, int(np.count_nonzero(mask))
