"""Pure image-conversion helpers (QImage <-> NumPy <-> PIL).

These are stateless functions extracted from PaintCanvas so the numpy/Qt/PIL
buffer-handling can be unit-tested without a widget. PaintCanvas keeps thin
static/class-method wrappers that delegate here, so its call sites are
unchanged.
"""
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from .common import PILImage
from .constants import TP_MASK_PROXY_THRESHOLD


def qimage_buffer(image: QImage, *, const: bool = False) -> Any:
    """Return a sized Python buffer for a QImage across PySide6 versions.

    Older Shiboken bindings expose ``setsize`` while newer bindings already
    return a correctly sized memoryview.  Keeping the compatibility probe here
    prevents every image operation from duplicating an exception-based check.
    """
    pointer: Any = image.constBits() if const else image.bits()
    setsize = getattr(pointer, "setsize", None)
    if callable(setsize):
        setsize(int(image.sizeInBytes()))
    return pointer


def qimage_rgba_array(image):
    """PySide6の版差に依存しないQImage→NumPy変換。"""
    converted = image.convertToFormat(
        QImage.Format.Format_RGBA8888
    )
    width, height = converted.width(), converted.height()
    if width <= 0 or height <= 0:
        return np.zeros((0, 0, 4), dtype=np.uint8)

    byte_count = int(converted.sizeInBytes())
    ptr = qimage_buffer(converted, const=True)

    try:
        flat = np.frombuffer(
            ptr,
            dtype=np.uint8,
            count=byte_count,
        )
    except (TypeError, BufferError, ValueError):
        # Python 3.14／一部のPySide6でShibokenのバッファ公開形式が
        #異なる場合に、bytesへ固定して読み取る。
        flat = np.frombuffer(
            bytes(ptr),
            dtype=np.uint8,
            count=byte_count,
        )

    bytes_per_line = int(converted.bytesPerLine())
    expected = height * bytes_per_line
    if flat.size < expected:
        raise ValueError(
            "画像バッファのサイズが不足しています。"
        )

    rows = flat[:expected].reshape(
        (height, bytes_per_line)
    )
    return rows[:, :width * 4].reshape(
        (height, width, 4)
    ).copy()


def rgba_array_to_qimage(rgba):
    """NumPyの寿命やbuffer仕様に依存しないQImage変換。"""
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError(
            "RGBA配列は高さ×幅×4である必要があります。"
        )
    height, width = rgba.shape[:2]
    if width <= 0 or height <= 0:
        return QImage()

    stride = int(rgba.strides[0])
    raw = rgba.tobytes(order="C")
    image = QImage(
        raw,
        width,
        height,
        stride,
        QImage.Format.Format_RGBA8888,
    )
    if image.isNull():
        raise ValueError(
            "階調化画像をQImageへ変換できませんでした。"
        )
    return image.copy().convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied
    )


def pil_l_to_qimage(mask):
    gray = mask.convert("L")
    width, height = gray.size
    raw = gray.tobytes()
    return QImage(
        raw, width, height, width, QImage.Format.Format_Grayscale8
    ).copy()


def qimage_gray_array(image):
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    width, height = gray.width(), gray.height()
    if width <= 0 or height <= 0:
        return np.zeros((0, 0), dtype=np.uint8)
    ptr = qimage_buffer(gray)
    rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
        (height, gray.bytesPerLine())
    )
    return rows[:, :width].copy()


def qimage_to_pil_rgba(image):
    if PILImage is None:
        return None
    rgba = qimage_rgba_array(image)
    if rgba.size == 0:
        return PILImage.new("RGBA", (1, 1), (0, 0, 0, 0))
    return PILImage.fromarray(rgba, "RGBA")


def pil_rgba_to_qimage(image):
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    return rgba_array_to_qimage(rgba)


def tp_uses_proxy(target_width, target_height):
    return max(int(target_width), int(target_height)) >= TP_MASK_PROXY_THRESHOLD


def scanline_connected_region(passable, starts):
    """Fast 4-connected flood fill using horizontal runs instead of per-pixel stacks."""
    height, width = passable.shape
    region = np.zeros((height, width), dtype=bool)
    if isinstance(starts, tuple) and len(starts) == 2 and isinstance(starts[0], (int, np.integer)):
        stack = [(int(starts[0]), int(starts[1]))]
    else:
        stack = [(int(x), int(y)) for x, y in starts]
    while stack:
        x, y = stack.pop()
        if (
            x < 0 or y < 0 or x >= width or y >= height
            or region[y, x] or not passable[y, x]
        ):
            continue
        left = x
        while left > 0 and passable[y, left - 1] and not region[y, left - 1]:
            left -= 1
        right = x
        while right + 1 < width and passable[y, right + 1] and not region[y, right + 1]:
            right += 1
        region[y, left:right + 1] = True
        for next_y in (y - 1, y + 1):
            if next_y < 0 or next_y >= height:
                continue
            scan_x = left
            while scan_x <= right:
                if passable[next_y, scan_x] and not region[next_y, scan_x]:
                    stack.append((scan_x, next_y))
                    scan_x += 1
                    while (
                        scan_x <= right
                        and passable[next_y, scan_x]
                        and not region[next_y, scan_x]
                    ):
                        scan_x += 1
                scan_x += 1
    return region


def bool_mask_image(mask):
    mask = np.asarray(mask, dtype=bool)
    height, width = mask.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[:, :, :3] = 255
    rgba[:, :, 3] = mask.astype(np.uint8) * 255
    return QImage(
        rgba.data,
        width,
        height,
        rgba.strides[0],
        QImage.Format.Format_RGBA8888,
    ).copy()


def tp_transparent_to_white(image):
    """v0.7 rule: transparent source pixels become opaque #FFFFFF masks."""
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    transparent = rgba[:, :, 3] == 0
    rgba[transparent] = (255, 255, 255, 255)
    return PILImage.fromarray(rgba, "RGBA")


def tp_white_to_transparent(image):
    """v0.7 rule: exact #FFFFFF is transparent in the TP result."""
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    white = np.all(rgba[:, :, :3] == 255, axis=2)
    rgba[white, 3] = 0
    return PILImage.fromarray(rgba, "RGBA")


def tp_prepare_palette_image(image, max_colors=64):
    """Port of prepare_palette_image() from the v0.7 prototype."""
    source = image.convert("RGBA")
    colors = source.getcolors(maxcolors=max_colors + 1)
    opaque = (
        [(count, rgba) for count, rgba in colors if rgba[3] > 0]
        if colors is not None
        else []
    )
    if colors is not None and len(opaque) <= max_colors:
        palette = [rgba for _count, rgba in sorted(opaque, reverse=True)]
        return source, palette

    alpha = source.getchannel("A")
    rgb = source.convert("RGB").quantize(
        colors=max_colors,
        method=PILImage.Quantize.MEDIANCUT,
        dither=PILImage.Dither.NONE,
    ).convert("RGBA")
    rgb.putalpha(alpha.point(lambda value: 255 if value >= 96 else 0))
    colors = rgb.getcolors(maxcolors=1_000_000) or []
    palette = [
        rgba for _count, rgba in sorted(
            ((count, rgba) for count, rgba in colors if rgba[3] > 0),
            reverse=True,
        )
    ]
    return rgb, palette

