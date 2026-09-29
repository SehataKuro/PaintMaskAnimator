import hashlib
import os
from typing import Any
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.actionpanel import (  # noqa: E402
    BUILTIN_SCRIPTS,
    LEGACY_BUILTIN_HASHES,
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
        assert swatch._main_btn.size() == swatch._sub_btn.size()
        assert swatch._bg_btn.height() == swatch._main_btn.height() // 2

        # サブを選んでもメインが上・サブが下のまま（位置は入れ替えない）。
        swatch.set_colors(QColor("black"), QColor("red"), "sub", QColor("white"))
        assert (swatch._main_btn.y(), swatch._sub_btn.y()) == (0, 27)
        background_geometry = swatch._bg_btn.geometry()
        swatch.set_colors(
            QColor("black"), QColor("red"), "transparent", QColor("white")
        )
        assert swatch._bg_btn.geometry() == background_geometry
        assert swatch._bg_btn.y() > swatch._main_btn.y()
        assert swatch._bg_btn.y() > swatch._sub_btn.y()

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

    dialog: Any = SimpleNamespace(_current_path=script, _dirty=False)
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


def test_seed_upgrades_untouched_legacy_builtin(qapp, tmp_path, monkeypatch):
    name = "builtin_main_line_repaint.py"
    legacy = (
        '"""処理を関数に分ける例: MainLineRepaint。"""\n\n\n'
        "def register_actions(panel, window):\n"
        "    def repaint_main_line():\n"
        "        # 通常ボタンのコールバックには引数が渡りません。\n"
        "        window.main_line_repaint()\n\n"
        "    panel.add_action(\n"
        '        "main_line_repaint",\n'
        '        "MainLineRepaint",\n'
        "        repaint_main_line,\n"
        "        tooltip=(\n"
        '            "メイン色・サブ色を線レイヤーへ分離し、"\n'
        '            "抜けた面を周囲の最多色で埋めます。"\n'
        "        ),\n"
        "    )\n"
    )
    digest = hashlib.sha256(legacy.encode()).hexdigest()
    assert digest in LEGACY_BUILTIN_HASHES[name]

    (tmp_path / name).write_text(legacy, encoding="utf-8")
    (tmp_path / ".builtin_seeded.json").write_text(
        f'["{name}"]', encoding="utf-8"
    )

    monkeypatch.setattr(ActionPanel, "actions_dir", staticmethod(lambda: tmp_path))
    panel = ActionPanel(object())
    panel._seed_builtin_scripts()

    assert (tmp_path / name).read_text(encoding="utf-8") == BUILTIN_SCRIPTS[name]


def test_macos_vscode_cli_found_inside_app_bundle(tmp_path, monkeypatch):
    from paintmaskanimator.actionpanel import ScriptEditorDialog

    monkeypatch.setattr(
        ScriptEditorDialog, "_macos_app_roots", staticmethod(lambda: (tmp_path,))
    )
    assert ScriptEditorDialog._macos_vscode_cli() is None
    cli = tmp_path / "Visual Studio Code.app/Contents/Resources/app/bin/code"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n")
    assert ScriptEditorDialog._macos_vscode_cli() == str(cli)
