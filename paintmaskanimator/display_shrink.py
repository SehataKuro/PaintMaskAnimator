"""縮小表示のしかた（Issue #46）。

キャンバス全体が見えるところまで縮めると、線が目に見えて荒れる。表示は常に
ニアレストネイバー（``FastTransformation``）で縮めており、1画素の線は縮小率
しだいで拾われたり落ちたりするためで、線が途切れて見えるのも同じ理由による。

ここでは「線を残す」縮小を用意する。半分ずつ縮めながら、2×2 のうち最も不透明
（同じ不透明さなら最も暗い）画素を残す。細い線が消えず、太りもしない。ソース
の画素は触らず、表示のためだけに使う。
"""
import numpy as np
from PySide6.QtGui import QImage

from . import imaging

#: 設定値。``nearest`` が従来どおりの表示。
MODE_NEAREST = "nearest"
MODE_KEEP_LINES = "keep_lines"
MODES = (MODE_NEAREST, MODE_KEEP_LINES)


def _winners(a, b):
    """2枚の RGBA 配列から、画素ごとに「線らしい方」を選ぶマスクを返す。

    不透明な方を優先し、同じ不透明さなら暗い方（線は背景より暗い）を選ぶ。
    """
    alpha_a = a[:, :, 3].astype(np.int16)
    alpha_b = b[:, :, 3].astype(np.int16)
    luma_a = a[:, :, :3].astype(np.int16).sum(axis=2)
    luma_b = b[:, :, :3].astype(np.int16).sum(axis=2)
    return (alpha_a > alpha_b) | ((alpha_a == alpha_b) & (luma_a <= luma_b))


def _pick(a, b):
    keep = _winners(a, b)[:, :, None]
    return np.where(keep, a, b)


def halve_keeping_lines(rgba):
    """RGBA 配列を縦横半分にする。2×2 のうち線らしい画素を残す。"""
    height, width = rgba.shape[:2]
    height -= height % 2
    width -= width % 2
    if height < 2 or width < 2:
        return rgba
    block = rgba[:height, :width]
    return _pick(
        _pick(block[0::2, 0::2], block[0::2, 1::2]),
        _pick(block[1::2, 0::2], block[1::2, 1::2]),
    )


def shrink_keeping_lines(image: QImage, width: int, height: int) -> QImage:
    """``image`` を ``width`` × ``height`` へ、線を残したまま縮める。

    半分ずつ縮めて目標の2倍以内まで寄せ、最後の端数だけニアレストで合わせる。
    拡大や等倍は呼び出し側で扱う（ここでは縮小だけを受け持つ）。
    """
    width = max(1, int(width))
    height = max(1, int(height))
    if image.width() <= width and image.height() <= height:
        return image

    rgba = imaging.qimage_rgba_array(image)
    while rgba.shape[0] >= height * 2 and rgba.shape[1] >= width * 2:
        halved = halve_keeping_lines(rgba)
        if halved.shape == rgba.shape:
            break
        rgba = halved

    reduced = imaging.rgba_array_to_qimage(np.ascontiguousarray(rgba))
    if (reduced.width(), reduced.height()) == (width, height):
        return reduced
    from PySide6.QtCore import Qt

    return reduced.scaled(
        width,
        height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
