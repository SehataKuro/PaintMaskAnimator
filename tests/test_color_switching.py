"""Main/sub colour switching and colour sampling.

Both controllers take the window as a constructor argument, so a stub window is
all these tests need -- no real ``MainWindow``, and no grafting of unbound mixin
methods onto a fake ``self``.
"""
from types import SimpleNamespace

from PySide6.QtGui import QColor

from paintmaskanimator.main_window_color_interaction import ColorInteractionController
from paintmaskanimator.main_window_line_ops import LineOpsController


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


def _stub_window(**overrides):
    window = SimpleNamespace(
        canvas=SimpleNamespace(
            main_color=QColor("#112233"),
            sub_color=QColor("#445566"),
            color_mode="main",
            transparent_display_color=QColor("#ffffff"),
            update=lambda: None,
        ),
        tools=_Tools(),
        palette=_Palette(),
        statusBar=lambda: _StatusBar(),
    )
    for key, value in overrides.items():
        setattr(window, key, value)
    return window


def test_x_color_switch_changes_active_swatch_without_swapping_colors():
    window = _stub_window()
    window.canvas.sub_color = QColor("#aabbcc")
    main, sub = QColor(window.canvas.main_color), QColor(window.canvas.sub_color)
    synced = []
    window.colors = SimpleNamespace(_sync_tool_selector_swatch=lambda: synced.append(True))
    line_ops = LineOpsController(window)

    line_ops.swap_main_sub()

    assert window.canvas.color_mode == "sub"
    assert window.canvas.main_color == main
    assert window.canvas.sub_color == sub
    assert window.tools.calls[-1][2] == "sub"
    assert synced == [True]

    line_ops.swap_main_sub()
    assert window.canvas.color_mode == "main"


def test_sampled_color_updates_bottom_toolbar_swatch():
    window = _stub_window()
    colors = ColorInteractionController(window)
    synced = []
    colors._sync_tool_selector_swatch = lambda: synced.append(True)
    window.colors = colors

    colors.apply_sampled_color(QColor("#abcdef"))

    assert window.canvas.main_color == QColor("#abcdef")
    assert window.canvas.color_mode == "main"
    assert window.tools.calls[-1][0] == QColor("#abcdef")
    assert window.tools.calls[-1][2] == "main"
    assert synced == [True]
    assert window.palette.selected == [QColor("#abcdef")]
