"""MCP の導入補助（``paintmaskanimator.mcp.setup``）のテスト。

ユーザーの設定ファイルを書き換える層なので、「壊さないこと」を重点的に固定する。
他の MCP サーバーの項目やトップレベルのキーを消さないこと、上書き前に控えを取る
こと、壊れた JSON を黙って作り直さないこと。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from paintmaskanimator.mcp import setup


@pytest.fixture
def config(tmp_path):
    return tmp_path / "claude_desktop_config.json"


def write(config, payload):
    config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ 設定の生成


def test_entry_uses_the_absolute_interpreter_path():
    """MCP クライアントはシェルの PATH を引き継がない。"python" では動かない。"""
    entry = setup.build_server_entry(with_pythonpath=False)

    assert entry["command"] == sys.executable
    assert entry["args"][:2] == ["-m", "paintmaskanimator.mcp"]


def test_entry_is_read_only_unless_asked(tmp_path):
    plain = setup.build_server_entry(with_pythonpath=False)
    writable = setup.build_server_entry(allow_write=True, with_pythonpath=False)

    assert "--allow-write" not in plain["args"]
    assert "--allow-write" in writable["args"]


def test_project_path_is_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.pma").touch()

    entry = setup.build_server_entry("a.pma", with_pythonpath=False)

    assert entry["args"][-1] == str(tmp_path / "a.pma")


def test_pythonpath_is_added_only_when_needed():
    without = setup.build_server_entry(with_pythonpath=False)
    with_path = setup.build_server_entry(with_pythonpath=True)

    assert "env" not in without
    assert with_path["env"]["PYTHONPATH"] == str(setup.package_root())


def test_claude_code_command_quotes_paths_with_spaces(monkeypatch):
    monkeypatch.setattr(setup, "_python_executable", lambda: "/opt/my python/bin/python")

    command = setup.claude_code_command()

    assert '"/opt/my python/bin/python"' in command
    assert command.startswith("claude mcp add paintmaskanimator")


# ------------------------------------------------------------------ 書き込み


def test_install_creates_the_file_when_missing(config):
    report = setup.install_into_claude_desktop(config_path=config)

    assert report["backup_path"] is None
    assert report["replaced"] is False
    written = json.loads(config.read_text(encoding="utf-8"))
    assert setup.SERVER_KEY in written["mcpServers"]


def test_install_preserves_other_servers_and_top_level_keys(config):
    write(
        config,
        {
            "mcpServers": {"filesystem": {"command": "npx", "args": ["-y", "x"]}},
            "globalShortcut": "Alt+Space",
        },
    )

    report = setup.install_into_claude_desktop(config_path=config)

    written = json.loads(config.read_text(encoding="utf-8"))
    assert written["mcpServers"]["filesystem"]["command"] == "npx"
    assert written["globalShortcut"] == "Alt+Space", "無関係のキーを消さない"
    assert report["other_servers"] == ["filesystem"]


def test_install_backs_up_before_overwriting(config):
    write(config, {"mcpServers": {"filesystem": {"command": "npx"}}})
    original = config.read_text(encoding="utf-8")

    report = setup.install_into_claude_desktop(config_path=config)

    backup = config.with_suffix(config.suffix + ".bak")
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == original
    assert report["backup_path"] == str(backup)


def test_reinstall_replaces_our_entry_in_place(config):
    setup.install_into_claude_desktop(config_path=config)

    report = setup.install_into_claude_desktop(config_path=config, allow_write=True)

    assert report["replaced"] is True
    written = json.loads(config.read_text(encoding="utf-8"))
    entry = written["mcpServers"][setup.SERVER_KEY]
    assert "--allow-write" in entry["args"]
    assert len(written["mcpServers"]) == 1, "重複した項目を増やさない"


def test_broken_config_aborts_instead_of_being_rebuilt(config):
    """壊れた JSON を作り直すと、ユーザーの他の設定が黙って消える。"""
    config.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(ValueError, match="壊れている"):
        setup.install_into_claude_desktop(config_path=config)

    assert config.read_text(encoding="utf-8") == "{ this is not json", "原文は残す"


def test_non_object_config_aborts(config):
    config.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ValueError, match="形式"):
        setup.install_into_claude_desktop(config_path=config)


def test_uninstall_removes_only_our_entry(config):
    write(config, {"mcpServers": {"filesystem": {"command": "npx"}}})
    setup.install_into_claude_desktop(config_path=config)

    report = setup.uninstall_from_claude_desktop(config_path=config)

    assert report["removed"] is True
    written = json.loads(config.read_text(encoding="utf-8"))
    assert list(written["mcpServers"]) == ["filesystem"]


def test_uninstall_is_a_no_op_when_not_registered(config):
    write(config, {"mcpServers": {"filesystem": {"command": "npx"}}})

    report = setup.uninstall_from_claude_desktop(config_path=config)

    assert report["removed"] is False


def test_is_registered_tolerates_a_missing_or_broken_file(config):
    assert setup.is_registered(config) is False
    config.write_text("nonsense", encoding="utf-8")
    assert setup.is_registered(config) is False


# ------------------------------------------------------------------ 場所と診断


def test_config_path_is_platform_specific(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert "Application Support" in str(setup.claude_desktop_config_path())

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert setup.claude_desktop_config_path() == (
        tmp_path / "Claude" / "claude_desktop_config.json"
    )


def test_diagnose_reports_registration_without_blocking(config):
    """未登録でも、Claude Code など他の入口が残るので致命ではない。"""
    diagnosis = setup.diagnose(config_path=config)

    registration = next(
        check for check in diagnosis.checks if "登録" in check.name
    )
    assert registration.ok is False
    assert registration.blocking is False
    assert diagnosis.ok is True, "登録の有無だけで駄目とは判定しない"


def test_diagnose_text_shows_the_next_step_for_failures(config, monkeypatch):
    monkeypatch.setattr(setup, "mcp_available", lambda: False)

    text = setup.diagnose(config_path=config).as_text()

    assert "mcp パッケージ" in text
    assert 'pip install -e ".[mcp]"' in text


def test_frozen_build_is_reported_as_blocking(config, monkeypatch):
    """配布版バイナリは -m を受け付けない。黙って壊れた設定を出さない。"""
    monkeypatch.setattr(setup, "is_frozen", lambda: True)

    diagnosis = setup.diagnose(config_path=config)

    python_check = next(check for check in diagnosis.checks if check.name == "Python")
    assert python_check.ok is False
    assert diagnosis.ok is False


# ------------------------------------------------------------------ Codex


@pytest.fixture
def codex_config(tmp_path):
    return tmp_path / "config.toml"


def test_codex_config_path_follows_codex_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert setup.codex_config_path() == tmp_path / "config.toml"

    monkeypatch.delenv("CODEX_HOME")
    # Claude Desktop と違い、Windows でもホーム直下（%APPDATA% ではない）。
    assert setup.codex_config_path().parent.name == ".codex"


def test_codex_toml_is_valid_and_carries_the_arguments(tmp_path):
    tomllib = pytest.importorskip("tomllib")

    text = setup.build_codex_toml(
        tmp_path / "a.pma", allow_write=True, with_pythonpath=True
    )
    parsed = tomllib.loads(text)

    entry = parsed["mcp_servers"][setup.SERVER_KEY]
    assert entry["command"] == sys.executable
    assert "--allow-write" in entry["args"]
    assert entry["args"][-1] == str(tmp_path / "a.pma")
    assert entry["env"]["PYTHONPATH"] == str(setup.package_root())


def test_codex_toml_escapes_windows_style_paths(monkeypatch):
    """バックスラッシュを素で書くと TOML のエスケープとして解釈されて壊れる。"""
    tomllib = pytest.importorskip("tomllib")
    monkeypatch.setattr(
        setup, "_python_executable", lambda: r"C:\Python311\python.exe"
    )

    parsed = tomllib.loads(setup.build_codex_toml(with_pythonpath=False))

    assert parsed["mcp_servers"][setup.SERVER_KEY]["command"] == (
        r"C:\Python311\python.exe"
    )


def test_codex_install_preserves_comments_and_other_settings(codex_config):
    tomllib = pytest.importorskip("tomllib")
    codex_config.write_text(
        '# 自分用の設定。触らないこと。\n'
        'model = "gpt-5-codex"\n\n'
        '[mcp_servers.docs]\ncommand = "npx"\n\n'
        '[shell_environment_policy]\ninherit = "all"\n',
        encoding="utf-8",
    )

    report = setup.install_into_codex(config_path=codex_config)

    text = codex_config.read_text(encoding="utf-8")
    assert "# 自分用の設定。触らないこと。" in text, (
        "1節だけ差し替えるので、ユーザーのコメントは残る"
    )
    parsed = tomllib.loads(text)
    assert parsed["model"] == "gpt-5-codex"
    assert parsed["shell_environment_policy"] == {"inherit": "all"}
    assert set(parsed["mcp_servers"]) == {"docs", setup.SERVER_KEY}
    assert report["other_servers"] == ["docs"]
    assert report["replaced"] is False


def test_codex_reinstall_replaces_the_section_in_place(codex_config):
    tomllib = pytest.importorskip("tomllib")
    codex_config.write_text(
        'model = "x"\n\n'
        f'[mcp_servers.{setup.SERVER_KEY}]\ncommand = "old"\nargs = ["stale"]\n\n'
        '[mcp_servers.docs]\ncommand = "npx"\n',
        encoding="utf-8",
    )

    report = setup.install_into_codex(config_path=codex_config)

    parsed = tomllib.loads(codex_config.read_text(encoding="utf-8"))
    assert report["replaced"] is True
    assert parsed["mcp_servers"][setup.SERVER_KEY]["command"] == sys.executable
    assert parsed["mcp_servers"]["docs"] == {"command": "npx"}, (
        "後続の節を巻き込んで消さない"
    )
    assert parsed["model"] == "x"


def test_codex_install_backs_up_and_creates_when_missing(codex_config):
    first = setup.install_into_codex(config_path=codex_config)
    assert first["backup_path"] is None

    second = setup.install_into_codex(config_path=codex_config)

    assert second["backup_path"] is not None
    assert codex_config.with_suffix(".toml.bak").exists()


def test_codex_broken_toml_aborts_instead_of_being_rebuilt(codex_config):
    pytest.importorskip("tomllib")
    codex_config.write_text('model = "unterminated\n', encoding="utf-8")

    with pytest.raises(ValueError, match="壊れている"):
        setup.install_into_codex(config_path=codex_config)

    assert codex_config.read_text(encoding="utf-8") == 'model = "unterminated\n'


def test_codex_uninstall_removes_only_our_section(codex_config):
    tomllib = pytest.importorskip("tomllib")
    codex_config.write_text('model = "x"\n\n[mcp_servers.docs]\ncommand = "npx"\n', "utf-8")
    setup.install_into_codex(config_path=codex_config)

    report = setup.uninstall_from_codex(config_path=codex_config)

    parsed = tomllib.loads(codex_config.read_text(encoding="utf-8"))
    assert report["removed"] is True
    assert list(parsed["mcp_servers"]) == ["docs"]
    assert parsed["model"] == "x"


def test_codex_command_uses_the_double_dash_form():
    """codex mcp add [OPTIONS] <NAME> (--url <URL> | -- <COMMAND>...)"""
    command = setup.codex_command()

    assert command.startswith(f"codex mcp add {setup.SERVER_KEY} -- ")
    assert "-m paintmaskanimator.mcp" in command


# ------------------------------------------------------------------ 登録先の抽象


def test_every_client_is_reachable_through_the_same_interface(tmp_path):
    for key, target in setup.CLIENTS.items():
        config = tmp_path / f"{key}{Path(str(target.config_path())).suffix}"

        assert target.registered(config) is False
        target.install(config_path=config)
        assert target.registered(config) is True, key
        assert target.render(), key
        assert target.cli_command().startswith(("claude ", "codex ")), key
        target.uninstall(config_path=config)
        assert target.registered(config) is False, key


def test_unknown_client_is_rejected():
    with pytest.raises(ValueError, match="未対応"):
        setup.client_target("emacs")


def test_diagnose_reports_every_client(codex_config, config):
    text = setup.diagnose(config_path=config).as_text()

    assert "Claude Desktop への登録" in text
    assert "Codex CLI への登録" in text
