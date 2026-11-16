"""ツールの並びのグループ（バケツ系・選択系）：1つのアイコンに重ね、もう一度押すか長押しで切り替える。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from paintmaskanimator.toolpanel import TOOL_GROUPS, ToolSelectorPanel, tool_group_of


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp):
    panel = ToolSelectorPanel()
    panel.resize(60, 400)
    panel.show()
    QApplication.processEvents()
    yield panel
    panel.close()


def centre(panel, group):
    return panel.list.visualItemRect(panel.items[group]).center()


def test_grouped_tools_share_one_icon(panel):
    assert set(panel.items) == {"brush", "shape", "fill", "select", "eyedropper", "dust"}
    assert dict(TOOL_GROUPS)["fill"] == (
        "bucket", "closed_fill", "lasso_fill", "replace_color", "skewer_fill"
    )
    assert tool_group_of("rect_select") == "select" and tool_group_of("brush") == "brush"


def test_selecting_a_member_shows_it_on_the_group_icon(panel):
    changed = []
    panel.toolChanged.connect(changed.append)
    panel.select_tool("closed_fill")
    panel.select_tool("replace_color")
    assert changed == ["closed_fill", "replace_color"], "同じアイコンの中でも切り替わる"
    item = panel.items["fill"]
    assert item.data(Qt.ItemDataRole.UserRole) == "replace_color"
    assert "色置換" in item.toolTip()


def test_clicking_another_group_brings_back_its_last_tool(panel):
    panel.select_tool("skewer_fill")
    panel.select_tool("brush")
    changed = []
    panel.toolChanged.connect(changed.append)
    QTest.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     centre(panel, "fill"))
    QApplication.processEvents()
    assert changed == ["skewer_fill"], "最後に使った串刺し塗りに戻る"


def test_clicking_the_active_group_again_opens_its_menu(panel, monkeypatch):
    opened = []

    def choose(_panel, menu, *_args):
        opened.append([action.text() for action in menu.actions()])
        return menu.actions()[1]  # 閉領域フィル

    monkeypatch.setattr(ToolSelectorPanel, "_exec_menu", choose)
    panel.select_tool("bucket")
    QTest.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     centre(panel, "fill"))
    QApplication.processEvents()
    QApplication.processEvents()
    assert opened and opened[0][:3] == ["バケツ", "閉領域フィル", "投げ縄塗り"]
    assert panel.active_tool == "closed_fill"


def test_first_click_on_a_group_does_not_open_the_menu(panel, monkeypatch):
    monkeypatch.setattr(ToolSelectorPanel, "_exec_menu", lambda *_a: pytest.fail("最初の1回では開かない"))
    panel.select_tool("brush")
    QTest.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     centre(panel, "select"))
    QApplication.processEvents()
    assert panel.active_tool == "lasso"


def test_long_press_opens_the_menu(panel, monkeypatch):
    opened = []
    monkeypatch.setattr(ToolSelectorPanel, "_exec_menu", lambda *_a: opened.append(True) or None)
    panel.select_tool("brush")
    viewport = panel.list.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     centre(panel, "select"))
    QTest.qWait(panel.LONG_PRESS_MS + 150)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       centre(panel, "select"))
    QApplication.processEvents()
    assert opened == [True]


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from paintmaskanimator.main_window import MainWindow

    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def test_the_replace_colour_tool_replaces_in_every_frame(window):
    from paintmaskanimator.document import create_drawing

    canvas = window.canvas
    canvas._ensure_frame_count(2)
    for index in range(2):
        layer = canvas.frames[index].layers[0]
        layer.image.fill(QColor(0, 0, 0, 0))
        for x in range(10, 20):
            layer.image.setPixelColor(x, 10, QColor("#ffe0c0"))
        create_drawing(layer, layer.image.copy(), number=index + 1)
    canvas.current_frame = 0
    canvas.main_color = QColor("#3355ff")
    canvas.color_mode = "main"
    window.tools.select_tool("replace_color")
    assert canvas.tool == "replace_color"
    assert window.tool_selector.active_tool == "replace_color"

    canvas.flood_fill(QPoint(12, 10))

    for index in range(2):
        assert canvas.frames[index].layers[0].image.pixelColor(12, 10) == QColor("#3355ff")


def test_the_tool_options_follow_the_new_tools(window):
    tools = window.tools
    tools.select_tool("skewer_fill")
    assert not tools.bucket_adjacent.isHidden(), "串刺し塗りはバケツと同じ設定"
    assert tools.bucket_all_frames.isHidden() and tools.bucket_replace_color.isHidden()
    tools.select_tool("replace_color")
    assert not tools.replace_color_note.isHidden()
    assert tools.bucket_adjacent.isHidden(), "色置換はバケツの設定を使わない"


def test_the_old_shortcuts_switch_tools(window):
    window.tools.select_tool("bucket")
    window.a_bucket_all_frames.trigger()
    assert window.tools.active_tool == "skewer_fill"
    window.a_bucket_all_frames.trigger()
    assert window.tools.active_tool == "bucket"
    window.a_bucket_replace_color.trigger()
    assert window.tools.active_tool == "replace_color"


def test_a_quick_second_click_also_opens_the_menu(panel, monkeypatch):
    """2回目が「ダブルクリック」として届いても、メニューを開く（実機で見つかった）。"""
    opened = []
    monkeypatch.setattr(ToolSelectorPanel, "_exec_menu", lambda *_a: opened.append(True) or None)
    panel.select_tool("brush")
    viewport = panel.list.viewport()
    point = centre(panel, "fill")
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    # Windows の素早い2回目：押下ではなくダブルクリックが届き、そのあと離す。
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    double = QMouseEvent(
        QEvent.Type.MouseButtonDblClick, QPointF(point), viewport.mapToGlobal(QPointF(point)),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(viewport, double)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QApplication.processEvents()
    QApplication.processEvents()
    assert opened == [True]
