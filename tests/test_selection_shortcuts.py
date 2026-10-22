"""範囲選択のショートカット：Ctrl+A 全選択／Ctrl+D 解除／Ctrl+Shift+I 反転。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import constants  # noqa: E402
from paintmaskanimator.constants import OUTSIDE_MARGIN  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def _mask(window):
    image = window.canvas.active_layer.image
    return window.canvas.selection_mask_bool(image.width(), image.height())


def test_shortcut_keys(window):
    assert window.a_selection_all.shortcut() == QKeySequence("Ctrl+A")
    assert window.a_selection_invert.shortcut() == QKeySequence("Ctrl+Shift+I")
    keys = window.a_selection_clear.shortcuts()
    assert QKeySequence("Ctrl+D") in keys
    assert QKeySequence("Ctrl+Shift+A") in keys, "以前のキーでも解除できる"


def test_select_all_covers_the_canvas_but_not_the_margin(window):
    assert window.canvas.select_all()
    mask = _mask(window)
    m = OUTSIDE_MARGIN
    assert mask[m:m + constants.CANVAS_HEIGHT, m:m + constants.CANVAS_WIDTH].all()
    if m:
        assert not mask[: m].any() and not mask[:, : m].any()

    window.a_selection_clear.trigger()
    assert not window.canvas.selection_polygon


def test_invert_swaps_inside_and_outside_within_the_canvas(window):
    canvas = window.canvas
    m = OUTSIDE_MARGIN
    canvas.selection_polygon = [
        QPointF(m + 10, m + 10), QPointF(m + 50, m + 10),
        QPointF(m + 50, m + 40), QPointF(m + 10, m + 40),
    ]
    before = _mask(window)
    assert canvas.invert_selection()
    after = _mask(window)
    inside = (slice(m, m + constants.CANVAS_HEIGHT), slice(m, m + constants.CANVAS_WIDTH))
    assert (after[inside] == ~before[inside]).all()
    assert not after[m + 20, m + 20], "元の選択の内側は外れる"
    assert after[m + 100, m + 100], "外側が選ばれる"
    if m:
        assert not after[: m].any(), "キャンバスの外の余白は選ばない"

    assert canvas.invert_selection()
    again = _mask(window)
    assert (again[inside] == before[inside]).all(), "2回反転すると元に戻る"


def test_invert_without_a_selection_does_nothing(window):
    window.canvas.clear_selection()
    assert not window.canvas.invert_selection()
    assert not window.canvas.selection_polygon
