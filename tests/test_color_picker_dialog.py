"""AE 風カラーピッカーと、色見本・使用色のダブルクリックで開く経路。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from paintmaskanimator.color_picker_dialog import ColorPickerDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_fields_follow_each_other(qapp):
    dialog = ColorPickerDialog(QColor("#336699"))
    changes = []
    dialog.colorChanged.connect(changes.append)
    try:
        assert dialog.hex_edit.text() == "#336699"
        assert (dialog.r_spin.value(), dialog.g_spin.value(), dialog.blue_spin.value()) == (0x33, 0x66, 0x99)

        dialog.r_spin.setValue(255)
        assert dialog.color().red() == 255
        assert dialog.hex_edit.text().startswith("#FF")

        dialog.hex_edit.setText("#00ff00")
        dialog.hex_edit.editingFinished.emit()
        assert dialog.color() == QColor("#00FF00")
        assert dialog.h_spin.value() == 120
        assert changes and changes[-1] == QColor("#00FF00")
    finally:
        dialog.deleteLater()


def test_hue_survives_zero_saturation(qapp):
    dialog = ColorPickerDialog(QColor.fromHsv(200, 255, 255))
    try:
        dialog.s_spin.setValue(0)
        assert dialog.h_spin.value() == 200, "無彩色にしても色相を失わない"
        dialog.s_spin.setValue(100)
        assert dialog.color().hsvHue() == 200
    finally:
        dialog.deleteLater()


def test_clicking_the_field_and_strip_sets_saturation_value_and_hue(qapp):
    dialog = ColorPickerDialog(QColor("red"))
    try:
        field = dialog.field
        QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(field.width() - 1, 0))
        assert dialog.s_spin.value() == 100 and dialog.b_spin.value() == 100
        QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(0, field.height() - 1))
        assert dialog.color() == QColor("black")

        QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(field.width() - 1, 0))
        strip = dialog.hue_strip
        QTest.mouseClick(strip, Qt.MouseButton.LeftButton, pos=QPoint(strip.width() // 2, strip.height() // 2))
        assert abs(dialog.h_spin.value() - 180) <= 1
    finally:
        dialog.deleteLater()


def test_original_swatch_restores_and_get_color_cancel_is_invalid(qapp, monkeypatch):
    dialog = ColorPickerDialog(QColor("#123456"))
    try:
        dialog.set_color(QColor("#abcdef"))
        dialog.original_swatch.click()
        assert dialog.color() == QColor("#123456")
    finally:
        dialog.deleteLater()

    monkeypatch.setattr(ColorPickerDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert not ColorPickerDialog.get_color(QColor("red")).isValid()
    monkeypatch.setattr(ColorPickerDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    assert ColorPickerDialog.get_color(QColor("red")) == QColor("red")


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from paintmaskanimator.main_window import MainWindow

    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def test_double_clicking_swatches_opens_the_picker(window, monkeypatch):
    requests = []

    def fake_get_color(initial=None, parent=None, title=None, preview=None):
        requests.append(title)
        return QColor("#10a020")

    monkeypatch.setattr(ColorPickerDialog, "get_color", staticmethod(fake_get_color))

    QTest.mouseDClick(window.tools.main_btn, Qt.MouseButton.LeftButton)
    assert window.canvas.main_color == QColor("#10a020")
    QTest.mouseDClick(window.tools.sub_btn, Qt.MouseButton.LeftButton)
    assert window.canvas.sub_color == QColor("#10a020")
    assert window.canvas.color_mode == "sub"

    QTest.mouseDClick(window.tools.background_btn, Qt.MouseButton.LeftButton)
    assert window.canvas.transparent_display_color == QColor("#10a020")
    swatch = window.tool_selector.color_swatch
    assert swatch._bg == QColor("#10a020"), "ツールバーの背景色見本も同期する"

    QTest.mouseDClick(swatch._main_btn, Qt.MouseButton.LeftButton)
    QTest.mouseDClick(window.tools.hsv_wheel, Qt.MouseButton.LeftButton)
    assert len(requests) == 5


def test_double_clicking_a_used_color_opens_its_replacement_picker(window):
    panel = window.palette
    panel.set_colors([QColor("#112233")])
    rgb = (0x11, 0x22, 0x33)
    QTest.mouseDClick(panel.source_wrappers[rgb], Qt.MouseButton.LeftButton)
    popup = panel._replacement_popup
    try:
        assert popup is not None and popup.isVisible()
        assert panel._replacement_popup_rgb == rgb
        popup.set_color(QColor("#ff8800"))
        assert panel.replacements[rgb] == QColor("#ff8800"), "編集はそのまま置換色に入る"
        popup.reject()
        assert panel.replacements[rgb] != QColor("#ff8800"), "キャンセルで元に戻す"
    finally:
        panel._close_replacement_editor()
