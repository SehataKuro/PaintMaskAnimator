"""サブビューの JPEG の向き（``subview._read_image`` の ``QImageReader.setAutoTransform(True)``）を
pma-core の ``import::orientation`` と比べる。

JPEG の復号は実装ごとに 1〜2 ずれることがあるので、色の差は少しだけ許し、向き（寸法と
四隅の色）が合うかを見る。
"""

import io

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage, QImageReader

from pma_native.pma_native import import_subview_jpeg


def _jpeg(orientation):
    # 四隅で色の違う 8×4 の絵（JPEG の 8×8 の塊に合わせて色を置く）。
    image = Image.new("RGB", (16, 8))
    for x in range(16):
        for y in range(8):
            image.putpixel((x, y), (
                (255, 0, 0) if x < 8 and y < 4 else
                (0, 255, 0) if y < 4 else
                (0, 0, 255) if x < 8 else
                (255, 255, 0)
            ))
    exif = Image.Exif()
    exif[0x0112] = orientation
    out = io.BytesIO()
    image.save(out, "JPEG", quality=100, subsampling=0, exif=exif)
    return out.getvalue()


def _qt(data):
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    image = reader.read().convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = image.width(), image.height()
    pixels = np.frombuffer(image.constBits(), dtype=np.uint8)
    pixels = pixels.reshape(height, image.bytesPerLine())[:, : width * 4].reshape(height, width, 4)
    return width, height, pixels.astype(int)


@pytest.mark.parametrize("orientation", range(1, 9))
def test_subview_jpeg_orientation(orientation):
    data = _jpeg(orientation)
    want_w, want_h, want = _qt(data)
    width, height, got = import_subview_jpeg(data)
    assert (width, height) == (want_w, want_h)
    got = np.frombuffer(got, dtype=np.uint8).reshape(height, width, 4).astype(int)
    assert np.abs(got - want).max() <= 8
