import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.actionpanel import (  # noqa: E402
    BUILTIN_SCRIPTS,
    NEW_SCRIPT_TEMPLATE,
    ActionPanel,
    ScriptEditorDialog,
)
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
        swatch = selector.color_swatch
        assert swatch._main_btn.x() == swatch._sub_btn.x() == swatch._bg_btn.x()
        assert (
            swatch._main_btn.y(), swatch._sub_btn.y(), swatch._bg_btn.y()
        ) == (0, 27, 54)

        swatch.set_colors(QColor("black"), QColor("red"), "sub", QColor("white"))
        assert (swatch._sub_btn.y(), swatch._main_btn.y()) == (0, 27)

        selector.resize(selector.width_for_columns(3), 600)
        qapp.processEvents()
        assert selector._column_count == 3
        assert selector.color_swatch.width() == 84
        first = selector.list.visualItemRect(selector.list.item(0))
        second = selector.list.visualItemRect(selector.list.item(1))
        third = selector.list.visualItemRect(selector.list.item(2))
        fourth = selector.list.visualItemRect(selector.list.item(3))
        assert (first.x(), second.x(), third.x()) == (0, 30, 60)
        assert fourth.y() == selector.CELL_SIZE

        selector.resize(selector.width_for_columns(2), 600)
        qapp.processEvents()
        assert selector.displayed_column_count() == 2
        assert selector._column_count == 2
        assert selector.color_swatch.width() == 54
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


def test_action_examples_are_copyable_python():
    for filename, source in BUILTIN_SCRIPTS.items():
        compile(source, filename, "exec")

    compile(NEW_SCRIPT_TEMPLATE, "new_action.py", "exec")
    assert "def run()" in NEW_SCRIPT_TEMPLATE
    assert "def toggle(checked)" in NEW_SCRIPT_TEMPLATE
    assert "lambda" not in "\n".join(BUILTIN_SCRIPTS.values())


def test_monaco_editor_is_configured_for_python_spaces():
    html = (
        Path(__file__).parents[1]
        / "paintmaskanimator/assets/monaco_editor.html"
    ).read_text(encoding="utf-8")

    assert 'language: "python"' in html
    assert "insertSpaces: true" in html
    assert "tabSize: 4" in html
    assert "detectIndentation: false" in html


def test_open_in_vscode_uses_goto(tmp_path, monkeypatch):
    script = tmp_path / "sample.py"
    script.write_text("pass\n", encoding="utf-8")
    launched = []
    monkeypatch.setattr("shutil.which", lambda command: "Code.exe")
    monkeypatch.setattr("subprocess.Popen", lambda command: launched.append(command))

    dialog = SimpleNamespace(_current_path=script, _dirty=False)
    ScriptEditorDialog._open_in_vscode(dialog)

    assert launched == [["Code.exe", "--goto", str(script.resolve())]]


def test_seed_does_not_overwrite_edited_builtin(qapp, tmp_path, monkeypatch):
    name = next(iter(BUILTIN_SCRIPTS))
    edited = "# 自分で編集したアクション\n"
    (tmp_path / name).write_text(edited, encoding="utf-8")
    (tmp_path / ".builtin_seeded.json").write_text(
        f'["{name}"]', encoding="utf-8"
    )

    monkeypatch.setattr(ActionPanel, "actions_dir", staticmethod(lambda: tmp_path))
    panel = ActionPanel(object())
    panel._seed_builtin_scripts()

    assert (tmp_path / name).read_text(encoding="utf-8") == edited
