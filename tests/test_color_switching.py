from types import SimpleNamespace

from PySide6.QtGui import QColor

from paintmaskanimator.main_window_line_ops import LineOpsMixin
from paintmaskanimator.main_window_color_interaction import ColorInteractionMixin


class _Tools:
    def __init__(self):
        self.calls = []

    def set_colors(self, *args):
        self.calls.append(args)


class _StatusBar:
    def showMessage(self, *_args):
        pass


class _Palette:
    def __init__(self):
        self.selected = []

    def select_matching_color(self, color):
        self.selected.append(QColor(color))


class _ColorWindow:
    _is_background_sample = staticmethod(
        ColorInteractionMixin._is_background_sample
    )
    _apply_sampled_drawing_color = (
        ColorInteractionMixin._apply_sampled_drawing_color
    )
    apply_sampled_color = ColorInteractionMixin.apply_sampled_color

    def __init__(self):
        self.canvas = SimpleNamespace(
            main_color=QColor("#112233"),
            sub_color=QColor("#445566"),
            color_mode="main",
            transparent_display_color=QColor("#ffffff"),
        )
        self.tools = _Tools()
        self.palette = _Palette()
        self.synced = []

    def _sync_tool_selector_swatch(self):
        self.synced.append(True)


def test_x_color_switch_changes_active_swatch_without_swapping_colors():
    main = QColor("#112233")
    sub = QColor("#aabbcc")
    canvas = SimpleNamespace(
        main_color=main,
        sub_color=sub,
        color_mode="main",
        transparent_display_color=QColor("white"),
        update=lambda: None,
    )
    tools = _Tools()
    synced = []
    window = SimpleNamespace(
        canvas=canvas,
        tools=tools,
        _sync_tool_selector_swatch=lambda: synced.append(True),
        statusBar=lambda: _StatusBar(),
    )

    LineOpsMixin.swap_main_sub(window)

    assert canvas.color_mode == "sub"
    assert canvas.main_color == main
    assert canvas.sub_color == sub
    assert tools.calls[-1][2] == "sub"
    assert synced == [True]

    LineOpsMixin.swap_main_sub(window)
    assert canvas.color_mode == "main"


def test_sampled_color_updates_bottom_toolbar_swatch():
    window = _ColorWindow()

    window.apply_sampled_color(QColor("#abcdef"))

    assert window.canvas.main_color == QColor("#abcdef")
    assert window.canvas.color_mode == "main"
    assert window.tools.calls[-1][0] == QColor("#abcdef")
    assert window.tools.calls[-1][2] == "main"
    assert window.synced == [True]
    assert window.palette.selected == [QColor("#abcdef")]


def test_sampled_white_selects_background_without_overwriting_draw_colors():
    window = _ColorWindow()
    original_main = QColor(window.canvas.main_color)
    original_sub = QColor(window.canvas.sub_color)

    window.apply_sampled_color(QColor("#ffffff"))

    assert window.canvas.main_color == original_main
    assert window.canvas.sub_color == original_sub
    assert window.canvas.color_mode == "transparent"
    assert window.tools.calls[-1][2] == "transparent"
    assert window.synced == [True]
    assert window.palette.selected == []
