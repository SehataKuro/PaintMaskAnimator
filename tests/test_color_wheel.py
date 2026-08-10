from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QSpinBox

from paintmaskanimator.widgets import HSVColorWheel
from paintmaskanimator.toolpanel import ToolPanel


def _application():
    return QApplication.instance() or QApplication([])


def test_hue_ring_is_default_and_hit_testable():
    app = _application()
    wheel = HSVColorWheel()
    wheel.resize(200, 220)
    wheel.show()
    app.processEvents()

    assert wheel.mode() == "HSV"
    assert wheel.hueMode() == "RING"
    assert wheel.findChildren(QSpinBox) == []
    wheel.setColor(QColor("#12abef"))
    wheel.color_copy_button.click()
    assert wheel.color_code_edit.text() == "#12ABEF"
    assert QApplication.clipboard().text() == "#12ABEF"
    assert wheel.color_copy_button.x() + wheel.color_copy_button.width() == 198
    assert wheel.color_code_edit.x() < wheel.color_copy_button.x()
    wheel.color_code_edit.setText("336699")
    wheel.color_code_edit.editingFinished.emit()
    assert wheel.color_code_edit.text() == "#336699"
    outer, square = wheel._wheel_geometry()
    center, outer_radius, inner_radius = wheel._ring_metrics()
    ring_radius = (outer_radius + inner_radius) / 2.0

    assert wheel._part_at(
        QPointF(center.x(), center.y() - ring_radius)
    ) == "hue"
    assert wheel._part_at(square.center()) == "sv"
    assert wheel._ring_hue(
        center, QPointF(center.x(), center.y() - outer_radius)
    ) == 0
    assert abs(
        wheel._ring_hue(
            center, QPointF(center.x() + outer_radius, center.y())
        ) - 90
    ) <= 1
    assert outer.contains(square.center())
    assert app is QApplication.instance()


def test_all_color_wheel_modes_render():
    app = _application()
    wheel = HSVColorWheel()
    wheel.resize(200, 220)
    wheel.setColor(QColor.fromHsv(180, 200, 220))

    for hue_mode in HSVColorWheel.HUE_MODES:
        wheel.setHueMode(hue_mode)
        for mode in HSVColorWheel.MODES:
            wheel.setMode(mode)
            assert wheel.mode() == mode
            assert wheel.hueMode() == hue_mode
            assert not wheel._wheel_image().isNull()
    assert app is QApplication.instance()


def test_hls_slider_hue_value_wraps():
    app = _application()
    panel = ToolPanel()
    panel.set_slider_mode("HLS")
    hue_value = panel.color_value_labels[0]

    assert hue_value.wrapping()
    hue_value.setValue(359)
    hue_value.stepUp()
    assert hue_value.value() == 0
    hue_value.stepDown()
    assert hue_value.value() == 359
    assert app is QApplication.instance()


def test_photoshop_style_swatch_controls():
    app = _application()
    panel = ToolPanel()
    swapped = []
    reset = []
    panel.swapMainSubRequested.connect(lambda: swapped.append(True))
    panel.resetMainSubRequested.connect(lambda: reset.append(True))

    assert panel.main_btn.geometry().intersects(panel.sub_btn.geometry())
    panel.swap_colors_button.click()
    panel.reset_colors_button.click()
    assert swapped == [True]
    assert reset == [True]
    assert app is QApplication.instance()
