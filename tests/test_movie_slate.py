"""ムービー書き出しの頭に出すカット名（Issue #26）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import movie_slate  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def blank(width=240, height=160, color="black"):
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(color))
    return image


def test_the_name_is_drawn_on_the_frame(qapp):
    source = blank()

    result = movie_slate.draw_cut_name(source, "c012")

    assert result != source
    # 元の画像は書き換えない。
    assert source == blank()


def test_a_blank_name_leaves_the_frame_alone(qapp):
    source = blank()

    assert movie_slate.draw_cut_name(source, "") is source
    assert movie_slate.draw_cut_name(source, "   ") is source


def test_the_name_reads_on_a_light_frame_too(qapp):
    """白地でも読めるよう、黒い縁取りが入る。"""
    light = blank(color="white")

    result = movie_slate.draw_cut_name(light, "c012")

    dark_pixels = sum(
        result.pixelColor(x, y).value() < 64
        for x in range(result.width())
        for y in range(result.height())
    )
    assert dark_pixels > 0


def test_the_cut_name_comes_from_the_project_file():
    assert movie_slate.cut_name_for("/work/PMA_01_c012.pman") == "PMA_01_c012"
    assert movie_slate.cut_name_for(None) == ""
    assert movie_slate.cut_name_for(None, fallback="c001") == "c001"


def test_only_the_first_frames_carry_the_name():
    assert movie_slate.LEADING_FRAMES == 8
