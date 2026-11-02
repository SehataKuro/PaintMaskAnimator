"""含み塗りの色ごと ON/OFF と、Shift での一時無効化（Issue #43）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

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


def test_turning_a_slot_off_keeps_the_colour_but_drops_it_from_the_fill(window):
    slots = window.tools.bucket_include_colors
    slots.set_color(0, (255, 0, 0))
    slots.set_color(1, (0, 255, 0))
    assert slots.colors() == ((255, 0, 0), (0, 255, 0))

    slots.set_enabled(0, False)

    assert slots.colors() == ((0, 255, 0),)
    # 色は消えない。ON に戻せばまた使える。
    assert slots.registered_colors() == ((255, 0, 0), (0, 255, 0))
    assert not slots.is_enabled(0)
    slots.set_enabled(0, True)
    assert slots.colors() == ((255, 0, 0), (0, 255, 0))


def test_registering_a_colour_turns_its_slot_back_on(window):
    slots = window.tools.bucket_include_colors
    slots.set_color(0, (1, 2, 3))
    slots.set_enabled(0, False)

    slots.set_color(0, (4, 5, 6))

    assert slots.is_enabled(0)


def test_the_on_off_state_is_remembered(window, qapp, tmp_path, monkeypatch):
    window.tools.bucket_include_colors.set_color(2, (9, 9, 9))
    window.tools.bucket_include_colors.set_enabled(2, False)

    other = MainWindow()
    try:
        assert not other.tools.bucket_include_colors.is_enabled(2)
    finally:
        other.deleteLater()


def test_holding_shift_fills_without_the_include_colours(window, qapp):
    window.tools.bucket_include_sub.setChecked(True)
    window.tools.bucket_include_colors.set_color(0, (0, 0, 0))

    assert window.canvas.bucket_options().include_masks

    QTest.keyPress(window.canvas, Qt.Key.Key_Shift, Qt.KeyboardModifier.ShiftModifier)
    try:
        assert not window.canvas.bucket_options().include_masks
        # 自動塗りなど、人の操作と切り離して呼ぶところは影響を受けない。
        assert window.canvas.bucket_options(respect_modifiers=False).include_masks
    finally:
        QTest.keyRelease(window.canvas, Qt.Key.Key_Shift, Qt.KeyboardModifier.NoModifier)

    assert window.canvas.bucket_options().include_masks
