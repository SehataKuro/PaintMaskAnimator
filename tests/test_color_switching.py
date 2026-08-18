from types import SimpleNamespace

from PySide6.QtGui import QColor

from paintmaskanimator.main_window_line_ops import LineOpsMixin


class _Tools:
    def __init__(self):
        self.calls = []

    def set_colors(self, *args):
        self.calls.append(args)


class _StatusBar:
    def showMessage(self, *_args):
        pass


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

