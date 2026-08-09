"""Pure image-conversion helpers (QImage <-> NumPy <-> PIL).

These are stateless functions extracted from PaintCanvas so the numpy/Qt/PIL
buffer-handling can be unit-tested without a widget. PaintCanvas keeps thin
static/class-method wrappers that delegate here, so its call sites are
unchanged.
"""
from .common import *  # noqa: F401,F403


def qimage_rgba_array(image):
    """PySide6の版差に依存しないQImage→NumPy変換。"""
    converted = image.convertToFormat(
        QImage.Format.Format_RGBA8888
    )
    width, height = converted.width(), converted.height()
    if width <= 0 or height <= 0:
        return np.zeros((0, 0, 4), dtype=np.uint8)

    byte_count = int(converted.sizeInBytes())
    ptr = converted.constBits()
    try:
        ptr.setsize(byte_count)
    except (AttributeError, TypeError):
        pass

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
    ptr = gray.bits()
    try:
        ptr.setsize(gray.sizeInBytes())
    except AttributeError:
        pass
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
