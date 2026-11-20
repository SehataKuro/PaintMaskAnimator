"""Qt と Rust の間でプロジェクトの PNG を読み書きする。"""

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QImageReader

from . import native
from .imaging import without_color_space
from .pma_image import PmaImage, as_qimage


def encode_png(image):
    """Rust（pma_core::project::png）で書き、GIL を手放す。キャンバスの形式以外は Qt に回す。

    PNG のバイト列を返し、失敗したら None。
    """
    if isinstance(image, PmaImage) and image.width and image.height:
        pixels, dots_per_meter = image._png_source()
        if pixels is not None:
            # モデルの画素は Qt で正規化したストレート RGBA なので、Qt 画像を経由せずに渡す。
            try:
                return native.encode_png(pixels, image.width, image.height, *dots_per_meter)
            except ValueError:
                pass
    image = without_color_space(as_qimage(image))
    if image.format() != QImage.Format.Format_ARGB32_Premultiplied or image.textKeys():
        return _encode_png_qt(image)
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    # 不変 bytes を1回作り、GILを手放している間も安全に借用する。
    pixels = bytes(rgba.constBits())
    try:
        return native.encode_png(pixels, rgba.width(), rgba.height(),
                                 image.dotsPerMeterX(), image.dotsPerMeterY())
    except ValueError:
        return _encode_png_qt(image)


def _encode_png_qt(image):
    """Rust で書けない形式と、テストでの突き合わせ用。失敗したら None。"""
    image = without_color_space(as_qimage(image))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    try:
        if not image.save(buffer, "PNG"):
            return None
    finally:
        buffer.close()
    return bytes(data.data())


def _decode_png(data):
    """Rust（pma_core::project::png）で読み、GIL を手放す。読めないものは Qt に回す。"""
    limit_mb = QImageReader.allocationLimit()
    max_bytes = limit_mb * 1024 * 1024 if limit_mb > 0 else (1 << 64) - 1
    try:
        width, height, pixels = native.decode_png(data, max_bytes)
    except ValueError:
        return _decode_png_qt(data)
    image = QImage(pixels, width, height, width * 4, QImage.Format.Format_RGBA8888)
    return without_color_space(image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied))


def _decode_png_qt(data):
    """PNG を読み、キャンバスと同じ形式（乗算済み ARGB）にする。失敗なら null。

    Rust で読めない PNG と、テストでの突き合わせ用。

    QImage.fromData は読み込み中も GIL を握ったままなので、スレッドに分けても
    速くならない。QImageReader.read は GIL を手放すので、こちらを使う。
    """
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    try:
        image = QImageReader(buffer, b"png").read()
    finally:
        buffer.close()
    if image.isNull():
        return image
    # Projects saved before profiles were stripped still carry them.
    return without_color_space(image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied))


def thumbnail_png(image, max_size):
    """``image`` を長辺 ``max_size`` 以下に縮めた PNG のバイト列。"""
    image = as_qimage(image)
    if image is None or image.isNull():
        return None
    if max(image.width(), image.height()) > max_size:
        image = image.scaled(
            max_size, max_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return _encode_png_qt(image)
