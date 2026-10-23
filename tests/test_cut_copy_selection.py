"""コピー・切り取りは選択範囲の形どおり（マスクで持つ選択でもずれない）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.constants import OUTSIDE_MARGIN as M  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402

GREEN = QColor("#40c070")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def canvas(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    canvas = window.canvas
    canvas.ensure_editable_key()
    image = canvas.active_layer.image
    painter = QPainter(image)
    painter.fillRect(image.rect(), GREEN)
    painter.end()
    canvas.active_layer.has_content = True
    try:
        yield canvas
    finally:
        window.deleteLater()


def _square(canvas, left=10, top=10, size=20):
    canvas.selection_polygon = [
        QPointF(M + left, M + top), QPointF(M + left + size, M + top),
        QPointF(M + left + size, M + top + size), QPointF(M + left, M + top + size),
    ]


def test_cut_with_an_inverted_selection_follows_the_mask(canvas):
    _square(canvas)
    assert canvas.invert_selection()
    bounds = canvas.selection_bounds()
    assert canvas.cut_selection()

    image = canvas.active_layer.image
    assert image.pixelColor(M + 20, M + 20) == GREEN, "選択の外（元の四角の中）は残る"
    assert image.pixelColor(M + 100, M + 100).alpha() == 0, "選択の中は消える"

    clip = QApplication.clipboard().image()
    assert clip.size() == bounds.size()
    inner = clip.pixelColor(M + 20 - bounds.x(), M + 20 - bounds.y())
    assert inner.alpha() == 0, "選択の外はコピーに入らない"
    assert clip.pixelColor(M + 100 - bounds.x(), M + 100 - bounds.y()).alpha() == 255

    canvas.undo()
    assert canvas.active_layer.image.pixelColor(M + 100, M + 100) == GREEN


def test_copy_of_a_polygon_selection_is_masked(canvas):
    canvas.selection_polygon = [
        QPointF(M + 10, M + 10), QPointF(M + 50, M + 10), QPointF(M + 10, M + 50),
    ]
    assert canvas.copy_selection()
    clip = QApplication.clipboard().image()
    assert clip.pixelColor(2, 2).alpha() == 255, "三角の中は入る"
    assert clip.pixelColor(36, 36).alpha() == 0, "三角の外（外接矩形の隅）は入らない"
    assert canvas.active_layer.image.pixelColor(M + 12, M + 12) == GREEN, "コピーは消さない"


def test_cut_without_a_selection_cuts_the_whole_layer(canvas):
    canvas.clear_selection()
    assert canvas.cut_selection()
    assert canvas.active_layer.image.pixelColor(M + 50, M + 50).alpha() == 0
    canvas.undo()
    assert canvas.active_layer.image.pixelColor(M + 50, M + 50) == GREEN
