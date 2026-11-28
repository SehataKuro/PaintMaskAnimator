"""Compare pma-core's JPEG decode (zune-jpeg) with Qt's (``QImage``, libjpeg).

Tauri の画面は JPEG を pma-core の ``import::image::read_image``（zune-jpeg）で読み、
Qt は ``QImage``（Qt の jpeg プラグイン＝libjpeg-turbo）で読む。どちらも JPEG の規格の
範囲で正しいが、色差の補間（4:2:0・4:2:2 の拡大）と逆 DCT の丸めが実装ごとに違うので、
画素が完全には一致しない。ここでは

- 色差を間引かない（4:4:4）・グレースケールの JPEG は、差が 2 以下（ずれる画素は 2% ほど）
- 間引いた（4:2:0・4:2:2）・プログレッシブの JPEG でも、差が 4 以下で平均はほぼ 0
  （測ると最大 3、ずれる画素は 4:2:0 で 2 割ほど）

を確かめる。読み込みの寸法・不透明（α=255）は完全に一致する。
"""

import io
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from PIL import Image
from PySide6.QtGui import QImage

from pma_native import pma_native as core

pytestmark = pytest.mark.skipif(
    not hasattr(core, "import_read_image"), reason="pma_native has no pma-core image reader"
)


def _qt_decode(data):
    image = QImage.fromData(data)
    assert not image.isNull()
    image = image.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    width, height = image.width(), image.height()
    raw = np.frombuffer(image.constBits(), dtype=np.uint8, count=image.sizeInBytes())
    rows = raw.reshape(height, image.bytesPerLine())[:, : width * 4]
    return width, height, rows.reshape(height, width, 4).astype(int)


def _rust_decode(data):
    width, height, pixels = core.import_read_image(data)
    return width, height, np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 4).astype(int)


def _picture(seed, width, height):
    """線画らしい絵（白地・黒い線・なめらかな色）と雑音を混ぜる。"""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:height, 0:width]
    base = np.stack([
        128 + 100 * np.sin(x / 7.0 + seed),
        128 + 100 * np.cos(y / 5.0),
        128 + 100 * np.sin((x + y) / 9.0),
    ], axis=-1)
    base += rng.normal(0, 12, size=base.shape)
    for _ in range(6):
        cx, cy = rng.integers(0, width), rng.integers(0, height)
        base[max(0, cy - 1):cy + 1, :] = 0
        base[:, max(0, cx - 1):cx + 1] = 0
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB")


def _jpeg(image, **options):
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", **options)
    return buffer.getvalue()


def _diff(data):
    qw, qh, qt = _qt_decode(data)
    rw, rh, rust = _rust_decode(data)
    assert (rw, rh) == (qw, qh)
    assert (rust[..., 3] == 255).all() and (qt[..., 3] == 255).all()
    return rust[..., :3] - qt[..., :3]


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("quality", [60, 95])
def test_full_chroma_jpeg_matches_qt_closely(seed, quality):
    data = _jpeg(_picture(seed, 37, 29), quality=quality, subsampling=0)
    diff = _diff(data)
    assert np.abs(diff).max() <= 2
    assert np.abs(diff).mean() < 0.5


@pytest.mark.parametrize("seed", range(4))
def test_grayscale_jpeg_matches_qt_closely(seed):
    data = _jpeg(_picture(seed, 33, 21).convert("L"), quality=90)
    diff = _diff(data)
    assert np.abs(diff).max() <= 2
    assert np.abs(diff).mean() < 0.5


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("options", [
    {"subsampling": 2},  # 4:2:0（Qt・多くのソフトの既定）
    {"subsampling": 1},  # 4:2:2
    {"subsampling": 2, "progressive": True},
])
def test_subsampled_jpeg_stays_within_tolerance(seed, options):
    data = _jpeg(_picture(seed, 41, 27), quality=90, **options)
    diff = _diff(data)
    # 色差の拡大の丸めの違い：画素の2割ほどが 1〜3 ずれる（測った最大は 3、平均 0.3）。
    assert np.abs(diff).max() <= 4
    assert np.abs(diff).mean() < 0.5
    assert abs(diff.mean()) < 0.2
