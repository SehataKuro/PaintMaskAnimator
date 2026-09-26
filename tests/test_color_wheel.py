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

    # メインとサブはカラーサークルのドック内で左右に並び、選択しても
    # 位置は入れ替わらない。
    assert panel.drawing_color_box.parent() is panel.color_wheel_box
    panel.color_wheel_box.resize(260, 400)
    for box in (panel.color_wheel_box, panel.color_swatch_stack):
        layout = box.layout()
        assert layout is not None
        layout.activate()
    main_x, sub_x = panel.main_btn.x(), panel.sub_btn.x()
    assert main_x < sub_x
    panel.set_color_mode("sub")
    assert (panel.main_btn.x(), panel.sub_btn.x()) == (main_x, sub_x)
    assert panel.sub_hex_label.text() == panel.sub_color.name().upper()
    panel.swap_colors_button.click()
    panel.reset_colors_button.click()
    assert swapped == [True]
    assert reset == [True]
    assert app is QApplication.instance()


def test_color_wheel_switches_background_selection_to_main_color():
    app = _application()
    panel = ToolPanel()
    modes = []
    colors = []
    panel.colorModeChanged.connect(modes.append)
    panel.colorChanged.connect(lambda mode, color: colors.append((mode, QColor(color))))

    panel.set_color_mode("transparent")
    assert panel.hsv_wheel.isEnabled()
    assert panel.hsv_wheel._color == panel.main_color

    selected = QColor("#336699")
    panel.wheel_color_changed(selected)

    assert panel.color_mode == "main"
    assert panel.main_color == selected
    assert modes[-1] == "main"
    assert colors[-1] == ("main", selected)
    assert app is QApplication.instance()
