"""「MCP サーバー設定」ダイアログのテスト。

ここで守りたいのは、ユーザーが手で JSON を書かずに済むこと、そして押した操作が
実際の設定と一致することの2点。診断は別プロセスを起動して時間がかかるので、
差し替えて速く回す。
"""
from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from paintmaskanimator.mcp import setup as mcp_setup
from paintmaskanimator.mcp_dialog import McpSetupDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "claude_desktop_config.json"
    monkeypatch.setattr(mcp_setup, "claude_desktop_config_path", lambda: path)
    # 診断は毎回 python を起動するので、UI テストでは固定値にする。
    monkeypatch.setattr(mcp_setup, "needs_pythonpath", lambda: False)
    monkeypatch.setattr(mcp_setup, "mcp_available", lambda: True)
    monkeypatch.setattr(mcp_setup, "is_frozen", lambda: False)
    monkeypatch.setattr(
        mcp_setup,
        "_probe",
        lambda args, env_extra=None: (True, ""),
    )
    return path


def dialog_for(qapp, config, project_path=None):
    return McpSetupDialog(None, project_path=project_path)


def test_config_is_generated_without_the_user_writing_json(qapp, config):
    dialog = dialog_for(qapp, config)

    payload = json.loads(dialog.config_view.toPlainText())

    assert mcp_setup.SERVER_KEY in payload["mcpServers"]
    assert payload["mcpServers"][mcp_setup.SERVER_KEY]["args"][:2] == [
        "-m",
        "paintmaskanimator.mcp",
    ]


def test_write_permission_is_off_by_default(qapp, config):
    dialog = dialog_for(qapp, config)

    assert dialog.allow_write.isChecked() is False
    assert "--allow-write" not in dialog.config_view.toPlainText()

    dialog.allow_write.setChecked(True)

    assert "--allow-write" in dialog.config_view.toPlainText()


def test_project_checkbox_is_disabled_until_the_project_is_saved(qapp, config):
    unsaved = dialog_for(qapp, config)
    assert unsaved.include_project.isEnabled() is False
    assert unsaved.include_project.isChecked() is False

    saved = dialog_for(qapp, config, project_path="/tmp/cut01.pma")
    assert saved.include_project.isEnabled() is True
    assert saved.include_project.isChecked() is True
    assert "cut01.pma" in saved.config_view.toPlainText()


def test_unchecking_the_project_removes_it_from_the_config(qapp, config):
    dialog = dialog_for(qapp, config, project_path="/tmp/cut01.pma")

    dialog.include_project.setChecked(False)

    assert "cut01.pma" not in dialog.config_view.toPlainText()


def test_install_writes_the_shown_config(qapp, config, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    dialog = dialog_for(qapp, config)
    shown = json.loads(dialog.config_view.toPlainText())

    dialog.install()

    written = json.loads(config.read_text(encoding="utf-8"))
    assert written["mcpServers"] == shown["mcpServers"], (
        "画面に出したものと書いたものが一致する"
    )


def test_install_is_cancellable(qapp, config, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    dialog = dialog_for(qapp, config)

    dialog.install()

    assert not config.exists(), "断ったら何も書かない"


def test_install_button_is_disabled_when_the_environment_is_not_ready(
    qapp, config, monkeypatch
):
    monkeypatch.setattr(mcp_setup, "is_frozen", lambda: True)

    dialog = dialog_for(qapp, config)

    assert dialog.install_button.isEnabled() is False
    # 手で直したい人のために、設定の表示自体は残す。
    assert dialog.config_view.toPlainText().strip()


def test_status_view_shows_what_is_missing(qapp, config, monkeypatch):
    monkeypatch.setattr(mcp_setup, "mcp_available", lambda: False)

    dialog = dialog_for(qapp, config)

    assert "mcp パッケージ" in dialog.status_view.toPlainText()
