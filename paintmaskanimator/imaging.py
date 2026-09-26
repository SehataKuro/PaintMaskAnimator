"""Pure image-conversion helpers (QImage <-> NumPy <-> PIL).

These are stateless functions extracted from PaintCanvas so the numpy/Qt/PIL
buffer-handling can be unit-tested without a widget. PaintCanvas keeps thin
static/class-method wrappers that delegate here, so its call sites are
unchanged.
"""
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from .optional_deps import PILImage
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


def _row_runs(row):
    """1行の True の連続区間を (開始, 終了[排他]) の配列で返す。"""
    padded = np.empty(row.size + 2, dtype=np.int8)
    padded[0] = 0
    padded[-1] = 0
    padded[1:-1] = row
    edges = np.flatnonzero(np.diff(padded))
    return edges[0::2], edges[1::2]


def scanline_connected_region(passable, starts):
    """4近傍の塗りつぶし領域を、行ごとの連続区間（ラン）単位で求める。

    画素ごとにPythonで走査すると大きなキャンバスで秒単位かかるため、
    訪れた行だけランをnumpyで切り出し、ラン同士の重なりでつなぐ。
    """
    passable = np.asarray(passable, dtype=bool)
    height, width = passable.shape
    region = np.zeros((height, width), dtype=bool)
    if isinstance(starts, tuple) and len(starts) == 2 and isinstance(starts[0], (int, np.integer)):
        seeds = np.array([[int(starts[0]), int(starts[1])]], dtype=np.int64)
    else:
        seeds = np.array([(int(x), int(y)) for x, y in starts], dtype=np.int64)
    if seeds.size == 0:
        return region
    seeds = seeds.reshape(-1, 2)
    inside = (
        (seeds[:, 0] >= 0) & (seeds[:, 1] >= 0)
        & (seeds[:, 0] < width) & (seeds[:, 1] < height)
    )
    seeds = seeds[inside]
    if seeds.size == 0:
        return region

    runs = {}

    def runs_of(y):
        cached = runs.get(y)
        if cached is None:
            cached = _row_runs(passable[y])
            runs[y] = cached
        return cached

    visited = set()
    stack = []
    for y in np.unique(seeds[:, 1]).tolist():
        run_starts, run_ends = runs_of(y)
        if run_starts.size == 0:
            continue
        xs = seeds[seeds[:, 1] == y, 0]
        indices = np.searchsorted(run_ends, xs, side="right")
        valid = indices < run_starts.size
        indices = indices[valid]
        xs = xs[valid]
        indices = indices[run_starts[indices] <= xs]
        for index in np.unique(indices).tolist():
            key = (y, index)
            if key not in visited:
                visited.add(key)
                stack.append(key)

    while stack:
        y, index = stack.pop()
        run_starts, run_ends = runs[y]
        left = int(run_starts[index])
        right = int(run_ends[index])
        region[y, left:right] = True
        for next_y in (y - 1, y + 1):
            if next_y < 0 or next_y >= height:
                continue
            next_starts, next_ends = runs_of(next_y)
            if next_starts.size == 0:
                continue
            # 列が重なるラン（4近傍でつながるラン）だけを辿る。
            low = int(np.searchsorted(next_ends, left, side="right"))
            high = int(np.searchsorted(next_starts, right, side="left"))
            for next_index in range(low, high):
                key = (next_y, next_index)
                if key not in visited:
                    visited.add(key)
                    stack.append(key)
    return region


#: RGBA8888 の1画素を uint32 として読んだときのアルファのビット。
#: リトルエンディアンでは R が最下位、A が最上位のバイトになる。
_ALPHA_BITS = np.array([0, 0, 0, 255], dtype=np.uint8).view(np.uint32)[0]
_RGB_BITS = np.array([255, 255, 255, 0], dtype=np.uint8).view(np.uint32)[0]
_LITTLE_ENDIAN_ALPHA = bool(_ALPHA_BITS == np.uint32(0xFF000000))


def rgba_packed_view(pixels):
    """(h, w, 4) の RGBA8888 配列を、コピーせず (h, w) の uint32 として見る。"""
    pixels = np.asarray(pixels)
    height, width = pixels.shape[:2]
    if not pixels.flags.c_contiguous:
        pixels = np.ascontiguousarray(pixels)
    return pixels.reshape(height, width * 4).view(np.uint32)


def pack_rgb(rgb):
    """(r, g, b) を不透明の RGBA8888 uint32 値にする。"""
    r, g, b = (int(channel) for channel in rgb[:3])
    return np.array([r, g, b, 255], dtype=np.uint8).view(np.uint32)[0]


#: 行ブロック単位で処理する行数。巨大な一時配列（10000×10000 で数百MB）を
#: 確保するとページフォールトだけで数百msかかるため、小分けにする。
_MASK_CHUNK_ROWS = 256


def _chunked_mask(packed, compute):
    height = packed.shape[0]
    result = np.empty(packed.shape, dtype=bool)
    for top in range(0, height, _MASK_CHUNK_ROWS):
        bottom = min(height, top + _MASK_CHUNK_ROWS)
        result[top:bottom] = compute(packed[top:bottom])
    return result


def background_mask_packed(packed):
    """透明（alpha=0）または #FFFFFF の画素。バケツの背景判定と同じ。"""
    if _LITTLE_ENDIAN_ALPHA:
        # アルファが最上位バイトなので、alpha=0 は値が 0x01000000 未満と同値。
        def compute(block):
            return (block < np.uint32(0x01000000)) | (
                (block | _ALPHA_BITS) == np.uint32(0xFFFFFFFF)
            )
    else:
        def compute(block):
            return ((block & _ALPHA_BITS) == 0) | (
                (block & _RGB_BITS) == _RGB_BITS
            )
    return _chunked_mask(packed, compute)


def opaque_rgb_mask_packed(packed, rgb):
    """アルファが0でなく、RGB が rgb と一致する画素。"""
    target = pack_rgb(rgb)
    if _LITTLE_ENDIAN_ALPHA:
        target_with_alpha = target | _ALPHA_BITS

        def compute(block):
            return ((block | _ALPHA_BITS) == target_with_alpha) & (
                block >= np.uint32(0x01000000)
            )
    else:
        def compute(block):
            return ((block & _RGB_BITS) == (target & _RGB_BITS)) & (
                (block & _ALPHA_BITS) != 0
            )
    return _chunked_mask(packed, compute)


def mask_bounds(mask):
    """True の外接矩形を (x0, y0, x1, y1)（x1, y1 は排他）で返す。空なら None。"""
    rows = np.flatnonzero(mask.any(axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero(mask[rows[0]:rows[-1] + 1].any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


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


def white_to_transparent_qimage(image):
    """Convert exact #FFFFFF opaque pixels to alpha=0 (QImage in/out).

    Used to migrate legacy layer data where pure white was the pseudo-transparent
    eraser sentinel. Non-destructive to appearance: white was already displayed
    as transparent under the old pseudo-transparency transform.
    """
    if image is None or image.isNull():
        return image
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = rgba.width(), rgba.height()
    if width <= 0 or height <= 0:
        return image
    ptr = qimage_buffer(rgba)
    rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
        (height, rgba.bytesPerLine())
    )
    pixels = rows[:, : width * 4].reshape((height, width, 4))
    white = (
        (pixels[:, :, 3] > 0)
        & (pixels[:, :, 0] == 255)
        & (pixels[:, :, 1] == 255)
        & (pixels[:, :, 2] == 255)
    )
    if not white.any():
        return image.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
    pixels[white] = 0
    return rgba.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


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

