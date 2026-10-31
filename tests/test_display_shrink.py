"""縮小表示で線を残す（Issue #46）。"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import display_shrink, imaging, preferences  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def image_with_thin_lines(size=64, step=8):
    """透明な地に、1画素の黒い縦線を等間隔で引いた画像。"""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    for x in range(0, size, step):
        for y in range(size):
            image.setPixelColor(x, y, QColor(0, 0, 0, 255))
    return image


def opaque_count(image):
    return int(np.count_nonzero(imaging.qimage_rgba_array(image)[:, :, 3]))


def test_keeping_lines_does_not_drop_them(qapp):
    source = image_with_thin_lines()

    nearest = source.scaled(8, 8)
    kept = display_shrink.shrink_keeping_lines(source, 8, 8)

    assert (kept.width(), kept.height()) == (8, 8)
    # 1/8 に縮めるとニアレストでは線がほとんど落ちる。
    assert opaque_count(kept) > opaque_count(nearest)
    # 線を太らせない：8列のうち線の列だけが残る。
    assert opaque_count(kept) <= 8 * 8


def test_enlarging_is_left_untouched(qapp):
    source = image_with_thin_lines(size=4, step=2)
    assert display_shrink.shrink_keeping_lines(source, 16, 16) is source


def test_the_more_opaque_pixel_wins(qapp):
    block = np.zeros((2, 2, 4), dtype=np.uint8)
    block[1, 1] = (10, 20, 30, 255)

    halved = display_shrink.halve_keeping_lines(block)

    assert halved.shape == (1, 1, 4)
    assert tuple(int(value) for value in halved[0, 0]) == (10, 20, 30, 255)


def test_the_setting_round_trips(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    preferences.set_display_shrink_mode(display_shrink.MODE_KEEP_LINES)
    assert preferences.display_shrink_mode() == display_shrink.MODE_KEEP_LINES
    preferences.set_display_shrink_mode(display_shrink.MODE_NEAREST)
    assert preferences.display_shrink_mode() == display_shrink.MODE_NEAREST
    with pytest.raises(ValueError):
        preferences.set_display_shrink_mode("smooth")
