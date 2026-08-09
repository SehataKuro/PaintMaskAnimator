import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.actionpanel import ActionPanel  # noqa: E402
from paintmaskanimator.toolpanel import ToolSelectorPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_tool_selector_reflows_with_width(qapp):
    selector = ToolSelectorPanel()
    selector.show()
    try:
        selector.resize(selector.width_for_columns(1), 600)
        qapp.processEvents()
        assert selector._column_count == 1

        selector.resize(selector.width_for_columns(3), 600)
        qapp.processEvents()
        assert selector._column_count == 3
        first = selector.list.visualItemRect(selector.list.item(0))
        second = selector.list.visualItemRect(selector.list.item(1))
        third = selector.list.visualItemRect(selector.list.item(2))
        fourth = selector.list.visualItemRect(selector.list.item(3))
        assert (first.x(), second.x(), third.x()) == (0, 30, 60)
        assert fourth.y() == selector.CELL_SIZE
    finally:
        selector.close()


def test_python_action_registration(qapp, tmp_path, monkeypatch):
    script = tmp_path / "sample.py"
    script.write_text(
        "def register_actions(panel, window):\n"
        "    panel.add_action('sample.run', 'Run sample', window.record)\n",
        encoding="utf-8",
    )

    class Window:
        def __init__(self):
            self.calls = 0

        def record(self):
            self.calls += 1

    window = Window()
    monkeypatch.setattr(ActionPanel, "actions_dir", staticmethod(lambda: tmp_path))
    panel = ActionPanel(window)
    panel.reload_python_actions()

    button = panel.button("sample.run")
    assert button is not None
    button.click()
    assert window.calls == 1
