"""MCP クライアントへの登録を、ユーザーが手で JSON を書かずに済ませるための層。

MCP サーバー自体は動いても、**設定ファイルを自力で書けるかどうか**が実際の導入の
壁になる。作画のユーザーに「``%APPDATA%\\Claude`` の JSON を開いて、Windows の
バックスラッシュを二重にして、絶対パスを書いて、再起動して」と頼むのは現実的では
ない。ここはその手順をアプリ側に肩代わりさせるための部品を置く。

対応するクライアントは Claude Desktop（JSON）と Codex CLI（TOML）。設定の形式も
場所も違うが、ユーザーから見れば「登録する」の1操作なので、差は
:class:`ClientTarget` に閉じ込めて呼び出し側からは同じに見せる。

UI も ``mcp`` パッケージも要らない純粋な層なので、GUI なしで単体テストできる。
実際に効く設定を出すために、Python が本当にこのパッケージを import できるかを
**部分プロセスで確かめてから**構成を組み立てる（推測で書いた設定は静かに動かない）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 のみ
    try:
        import tomli as tomllib  # pyright: ignore[reportMissingImports]
    except ModuleNotFoundError:
        # 3.10 で tomli も無い場合。読み取り検証だけを諦め、書き込みは行える。
        tomllib = None

from ..logging_setup import get_logger

__all__ = [
    "CLIENTS",
    "DEFAULT_CLIENT",
    "SERVER_KEY",
    "Check",
    "ClientTarget",
    "Diagnosis",
    "build_server_entry",
    "claude_code_command",
    "claude_desktop_config_path",
    "client_target",
    "codex_command",
    "codex_config_path",
    "diagnose",
    "install_into_claude_desktop",
    "install_into_codex",
    "package_root",
    "read_config",
]

log = get_logger(__name__)

#: ``mcpServers`` の中で使う名前。再登録時に同じ項目を差し替えるための鍵。
SERVER_KEY = "paintmaskanimator"

#: サーバーが応答しないときに待つ秒数。環境チェックは一瞬で終わるはず。
PROBE_TIMEOUT = 20


def package_root() -> Path:
    """``paintmaskanimator`` パッケージを含むディレクトリ。

    ソースから直接動かしている場合、``PYTHONPATH`` にこれを入れないと
    別プロセスの Python が import できない。
    """
    return Path(__file__).resolve().parent.parent.parent


def _python_executable() -> str:
    return sys.executable or "python"


def is_frozen() -> bool:
    """PyInstaller などで固めた実行ファイルから動いているか。

    固めた実行ファイルは ``-m`` を受け付けないので、そのままでは MCP サーバーを
    起動できない。黙って壊れた設定を出さないよう、ここで判定して伝える。
    """
    return bool(getattr(sys, "frozen", False))


def _probe(args: List[str], *, env_extra: Optional[Dict[str, str]] = None):
    """``sys.executable`` で短いチェックを走らせ、成功したかを返す。"""
    env = dict(os.environ)
    # 呼び出し元の PYTHONPATH に引きずられると、判定が実際の起動時と食い違う。
    env.pop("PYTHONPATH", None)
    if env_extra:
        env.update(env_extra)
    try:
        completed = subprocess.run(
            [_python_executable(), *args],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            env=env,
            cwd=str(Path.home()),
        )
    except (OSError, subprocess.SubprocessError) as error:  # pragma: no cover
        return False, str(error)
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode == 0, output


def needs_pythonpath() -> bool:
    """クリーンな環境で ``paintmaskanimator`` を import できないか。

    ``pip install`` 済みなら不要。ソースを clone しただけの場合は必要になる。
    """
    ok, _output = _probe(["-c", "import paintmaskanimator"])
    return not ok


def mcp_available() -> bool:
    ok, _output = _probe(["-c", "import mcp.server.mcpserver"])
    return ok


def build_server_entry(
    project: Optional[str] = None,
    *,
    allow_write: bool = False,
    with_pythonpath: Optional[bool] = None,
) -> Dict[str, Any]:
    """MCP クライアントの設定に入れる1項目を組み立てる。

    ``command`` には ``sys.executable`` の絶対パスを入れる。MCP クライアントは
    ユーザーのシェルの ``PATH`` を引き継がないことがあり、``"python"`` と書くと
    「手元では動くのに Claude からは起動しない」になりやすい。
    """
    args = ["-m", "paintmaskanimator.mcp"]
    if allow_write:
        args.append("--allow-write")
    if project:
        args.append(str(Path(project).resolve()))

    entry: Dict[str, Any] = {"command": _python_executable(), "args": args}
    if with_pythonpath is None:
        with_pythonpath = needs_pythonpath()
    if with_pythonpath:
        entry["env"] = {"PYTHONPATH": str(package_root())}
    return entry


def build_config(project=None, *, allow_write=False, with_pythonpath=None) -> Dict[str, Any]:
    """``mcpServers`` 1件だけを含む、貼り付け用の設定。"""
    return {
        "mcpServers": {
            SERVER_KEY: build_server_entry(
                project, allow_write=allow_write, with_pythonpath=with_pythonpath
            )
        }
    }


def claude_code_command(project=None, *, allow_write=False) -> str:
    """Claude Code の ``claude mcp add`` 用の1行コマンド。

    Claude Code のユーザーは JSON を触る必要がない。こちらのほうが導入は速い。
    """
    entry = build_server_entry(project, allow_write=allow_write)
    parts = ["claude", "mcp", "add", SERVER_KEY]
    for name, value in (entry.get("env") or {}).items():
        parts += ["--env", f"{name}={value}"]
    parts += ["--", entry["command"], *entry["args"]]
    return " ".join(_quote(part) for part in parts)


def _quote(value: str) -> str:
    return f'"{value}"' if " " in value else value


def codex_command(project=None, *, allow_write=False) -> str:
    """Codex CLI の ``codex mcp add`` 用の1行コマンド。

    書式は ``codex mcp add [OPTIONS] <NAME> (--url <URL> | -- <COMMAND>...)``。
    Claude Code と同じく ``--`` の後ろが起動コマンドになる。
    """
    entry = build_server_entry(project, allow_write=allow_write)
    parts = ["codex", "mcp", "add", SERVER_KEY]
    for name, value in (entry.get("env") or {}).items():
        parts += ["--env", f"{name}={value}"]
    parts += ["--", entry["command"], *entry["args"]]
    return " ".join(_quote(part) for part in parts)


def codex_config_path() -> Path:
    """Codex の ``config.toml``。

    Codex は ``CODEX_HOME`` があればそこ、無ければ ``~/.codex`` を使う。
    Claude Desktop と違い、Windows でも ``%APPDATA%`` ではなくホーム直下。
    """
    home = os.environ.get("CODEX_HOME")
    root = Path(home) if home else Path.home() / ".codex"
    return root / "config.toml"


def _toml_string(value: str) -> str:
    """TOML の基本文字列。Windows のパスに入るバックスラッシュを潰さない。"""
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )
    return f'"{escaped}"'


def build_codex_toml(project=None, *, allow_write=False, with_pythonpath=None) -> str:
    """Codex の ``config.toml`` に入れる ``[mcp_servers.…]`` 節を組み立てる。"""
    entry = build_server_entry(
        project, allow_write=allow_write, with_pythonpath=with_pythonpath
    )
    lines = [f"[mcp_servers.{SERVER_KEY}]"]
    lines.append(f"command = {_toml_string(entry['command'])}")
    lines.append(
        "args = [" + ", ".join(_toml_string(arg) for arg in entry["args"]) + "]"
    )
    env = entry.get("env")
    if env:
        pairs = ", ".join(
            f"{name} = {_toml_string(value)}" for name, value in env.items()
        )
        lines.append("env = { " + pairs + " }")
    return "\n".join(lines) + "\n"


#: ``[mcp_servers.paintmaskanimator]`` の節見出し（引用符つきの表記も拾う）。
_CODEX_SECTION = re.compile(
    r'^\[mcp_servers\.(?:%s|"%s")\]\s*$' % (re.escape(SERVER_KEY), re.escape(SERVER_KEY)),
    re.MULTILINE,
)
#: 次の節の見出し。差し替える範囲の終わりを見つけるのに使う。
_ANY_SECTION = re.compile(r"^\[", re.MULTILINE)


def _parse_toml(text: str, path):
    """検証のためだけに読む。``tomllib`` が無い環境では検証を諦める。"""
    if tomllib is None:  # pragma: no cover - 3.10 で tomli も無い場合
        return None
    try:
        return tomllib.loads(text)
    except Exception as error:
        raise ValueError(
            f"設定ファイルが壊れているため書き込みを中止しました: {path}（{error}）"
        ) from error


def _replace_codex_section(text: str, block: str):
    """既存の節を差し替える。無ければ末尾に足す。

    TOML 全体を書き直さずに1節だけを入れ替えるのは、**ユーザーのコメントと
    書式を残す**ため。読み込んで書き戻す方式だと、設定ファイルに書かれた注記が
    黙って消える。
    """
    match = _CODEX_SECTION.search(text)
    if match is None:
        separator = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return text + separator + block, False

    start = match.start()
    following = _ANY_SECTION.search(text, match.end())
    end = following.start() if following else len(text)
    return text[:start] + block + ("\n" if following else "") + text[end:], True


def is_registered_codex(config_path=None) -> bool:
    path = Path(config_path) if config_path is not None else codex_config_path()
    if not path.exists():
        return False
    try:
        return _CODEX_SECTION.search(path.read_text(encoding="utf-8")) is not None
    except OSError:
        return False


def install_into_codex(
    project=None,
    *,
    allow_write: bool = False,
    config_path=None,
    with_pythonpath: Optional[bool] = None,
) -> Dict[str, Any]:
    """Codex の ``config.toml`` へ登録する。

    Claude Desktop 側と同じ約束で扱う――マージし、控えを取り、壊れていたら中断する。
    """
    path = Path(config_path) if config_path is not None else codex_config_path()

    text = ""
    backup: Optional[Path] = None
    parsed: Optional[Dict[str, Any]] = None
    if path.exists():
        text = path.read_text(encoding="utf-8")
        parsed = _parse_toml(text, path)
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)

    block = build_codex_toml(
        project, allow_write=allow_write, with_pythonpath=with_pythonpath
    )
    updated, replaced = _replace_codex_section(text, block)
    _parse_toml(updated, path)  # 書く前に、自分が壊していないことを確かめる。

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(updated, encoding="utf-8")
    os.replace(temporary, path)

    others = sorted(
        name
        for name in ((parsed or {}).get("mcp_servers") or {})
        if name != SERVER_KEY
    )
    return {
        "config_path": str(path),
        "backup_path": str(backup) if backup else None,
        "replaced": replaced,
        "other_servers": others,
    }


def uninstall_from_codex(*, config_path=None) -> Dict[str, Any]:
    path = Path(config_path) if config_path is not None else codex_config_path()
    if not path.exists():
        return {"config_path": str(path), "removed": False}
    text = path.read_text(encoding="utf-8")
    match = _CODEX_SECTION.search(text)
    if match is None:
        return {"config_path": str(path), "removed": False}
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    following = _ANY_SECTION.search(text, match.end())
    end = following.start() if following else len(text)
    updated = (text[:match.start()] + text[end:]).lstrip("\n")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(updated, encoding="utf-8")
    os.replace(temporary, path)
    return {"config_path": str(path), "removed": True, "backup_path": str(backup)}


def claude_desktop_config_path() -> Path:
    """Claude Desktop の設定ファイルの場所（プラットフォーム別）。

    ファイルが存在するとは限らない。初回は自分で作る。
    """
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / "Claude" / "claude_desktop_config.json"
    return Path(
        os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    ) / "Claude" / "claude_desktop_config.json"


def read_config(path=None) -> Dict[str, Any]:
    """設定ファイルを読む。無い/壊れているときは空の設定として扱う。"""
    path = Path(path) if path is not None else claude_desktop_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        log.warning("Claude Desktop の設定を読めませんでした: %s", error)
        return {}
    return data if isinstance(data, dict) else {}


def is_registered(path=None) -> bool:
    servers = read_config(path).get("mcpServers")
    return isinstance(servers, dict) and SERVER_KEY in servers


# ---------------------------------------------------------------- 診断


@dataclass(frozen=True)
class Check:
    """1つの確認項目。``ok`` が偽なら ``hint`` が次の一手を示す。"""

    name: str
    ok: bool
    detail: str = ""
    hint: str = ""
    #: 偽なら、これが駄目でも他の手段（別クライアント等）は残る。
    blocking: bool = True


@dataclass
class Diagnosis:
    checks: List[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks if check.blocking)

    def as_text(self) -> str:
        lines = []
        for check in self.checks:
            mark = "OK  " if check.ok else ("NG  " if check.blocking else "--  ")
            lines.append(f"{mark}{check.name}: {check.detail}")
            if not check.ok and check.hint:
                lines.append(f"      → {check.hint}")
        return "\n".join(lines)


def diagnose(*, config_path=None, client=None) -> Diagnosis:
    """導入がどこで止まっているかを、実際に試して確かめる。

    「入れたのに動かない」の原因はほぼこの4つ（固めた実行ファイル・`mcp` 未導入・
    import できない・未登録）なので、推測ではなく部分プロセスで確認する。

    登録の有無は対応クライアントすべてについて報告する。どれか1つで使えれば
    目的は達せられるので、未登録は失敗として扱わない。
    """
    checks: List[Check] = []

    checks.append(
        Check(
            name="Python",
            ok=not is_frozen(),
            detail=_python_executable(),
            hint=(
                "配布版の実行ファイルからは MCP サーバーを起動できません。"
                "Python 環境に pip でインストールしてください。"
            ),
        )
    )

    importable, import_output = _probe(["-c", "import paintmaskanimator"])
    if importable:
        checks.append(
            Check(name="paintmaskanimator", ok=True, detail="import できます")
        )
    else:
        with_path, _ = _probe(
            ["-c", "import paintmaskanimator"],
            env_extra={"PYTHONPATH": str(package_root())},
        )
        checks.append(
            Check(
                name="paintmaskanimator",
                ok=with_path,
                detail=(
                    f"PYTHONPATH={package_root()} を付ければ import できます"
                    if with_path
                    else (import_output.splitlines() or ["import できません"])[-1]
                ),
                hint="pip install -e . でインストールしてください。",
            )
        )

    has_mcp = mcp_available()
    checks.append(
        Check(
            name="mcp パッケージ",
            ok=has_mcp,
            detail="導入済み" if has_mcp else "未導入",
            hint='pip install -e ".[mcp]" を実行してください。',
        )
    )

    for key, target in CLIENTS.items():
        # 明示された設定ファイルは、そのクライアントの分だけに効かせる。
        path = (
            Path(config_path)
            if config_path is not None and key == (client or DEFAULT_CLIENT)
            else target.config_path()
        )
        registered = target.registered(path)
        checks.append(
            Check(
                name=f"{target.label} への登録",
                ok=registered,
                detail=(
                    f"登録済み: {path}"
                    if registered
                    else f"未登録（{path}{'' if path.exists() else ' は未作成'}）"
                ),
                hint=(
                    f"--install --client {key} を実行するか、"
                    "アプリの「MCP サーバー設定…」から登録してください。"
                ),
                # どれか1つのクライアントで使えれば目的は達せられる。未登録という
                # だけで「駄目」とは言わない。
                blocking=False,
            )
        )

    for command, label in (("claude", "Claude Code"), ("codex", "Codex CLI")):
        found = shutil.which(command)
        checks.append(
            Check(
                name=f"{command} コマンド",
                ok=found is not None,
                detail=(
                    f"見つかりました（{command} mcp add が使えます）"
                    if found
                    else "見つかりません"
                ),
                hint=f"{label} を使う場合のみ必要です。",
                blocking=False,
            )
        )

    return Diagnosis(checks)


# ---------------------------------------------------------------- 書き込み


def install_into_claude_desktop(
    project=None,
    *,
    allow_write: bool = False,
    config_path=None,
    with_pythonpath: Optional[bool] = None,
) -> Dict[str, Any]:
    """Claude Desktop の設定へ登録する。

    ユーザーの設定ファイルを触るので、壊さないことを最優先にする。

    - 既存の設定は**マージする**。他の MCP サーバーの項目は残す。
    - 上書き前に ``.bak`` を作る。
    - 壊れた JSON は読み飛ばさず、**中断して** そう伝える（黙って作り直すと
      ユーザーの他の設定が消える）。
    """
    path = Path(config_path) if config_path is not None else claude_desktop_config_path()

    existing: Dict[str, Any] = {}
    backup: Optional[Path] = None
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ValueError(
                f"設定ファイルが壊れているため書き込みを中止しました: {path}（{error}）"
            ) from error
        if not isinstance(loaded, dict):
            raise ValueError(
                f"設定ファイルの形式が想定と違うため書き込みを中止しました: {path}"
            )
        existing = loaded
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)

    servers = existing.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    replaced = SERVER_KEY in servers
    servers[SERVER_KEY] = build_server_entry(
        project, allow_write=allow_write, with_pythonpath=with_pythonpath
    )
    existing["mcpServers"] = servers

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)

    return {
        "config_path": str(path),
        "backup_path": str(backup) if backup else None,
        "replaced": replaced,
        "other_servers": sorted(name for name in servers if name != SERVER_KEY),
    }


def uninstall_from_claude_desktop(*, config_path=None) -> Dict[str, Any]:
    """登録を取り消す。他のサーバーの項目には触らない。"""
    path = Path(config_path) if config_path is not None else claude_desktop_config_path()
    if not path.exists():
        return {"config_path": str(path), "removed": False}
    existing = read_config(path)
    servers = existing.get("mcpServers")
    if not isinstance(servers, dict) or SERVER_KEY not in servers:
        return {"config_path": str(path), "removed": False}
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    del servers[SERVER_KEY]
    existing["mcpServers"] = servers
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)
    return {"config_path": str(path), "removed": True, "backup_path": str(backup)}


# ---------------------------------------------------------------- クライアント


@dataclass(frozen=True)
class ClientTarget:
    """登録先のクライアント1つ分。

    Claude Desktop は JSON、Codex は TOML と形式も場所も違うが、ユーザーから見れば
    「登録する」という同じ1操作である。差をここに閉じ込めて、ダイアログや CLI が
    クライアントごとに分岐しなくて済むようにする。
    """

    key: str
    label: str
    #: 設定ファイルの記法。画面に「JSON」「TOML」と出すためだけに持つ。
    format_name: str
    #: このクライアントの CLI で登録するときのコマンドの説明。
    cli_label: str
    config_path: Callable[[], Path]
    render: Callable[..., str]
    cli_command: Callable[..., str]
    install: Callable[..., Dict[str, Any]]
    uninstall: Callable[..., Dict[str, Any]]
    registered: Callable[[Any], bool]
    #: 再起動が要るか。CLI で登録する Codex は不要。
    needs_restart: bool = True


def _render_claude_desktop(project=None, *, allow_write=False, with_pythonpath=None) -> str:
    return json.dumps(
        build_config(
            project, allow_write=allow_write, with_pythonpath=with_pythonpath
        ),
        ensure_ascii=False,
        indent=2,
    )


CLIENTS: Dict[str, ClientTarget] = {
    "claude-desktop": ClientTarget(
        key="claude-desktop",
        label="Claude Desktop",
        format_name="JSON",
        cli_label="Claude Code",
        config_path=claude_desktop_config_path,
        render=_render_claude_desktop,
        cli_command=claude_code_command,
        install=install_into_claude_desktop,
        uninstall=uninstall_from_claude_desktop,
        registered=is_registered,
    ),
    "codex": ClientTarget(
        key="codex",
        label="Codex CLI",
        format_name="TOML",
        cli_label="Codex CLI",
        config_path=codex_config_path,
        render=build_codex_toml,
        cli_command=codex_command,
        install=install_into_codex,
        uninstall=uninstall_from_codex,
        registered=is_registered_codex,
        # config.toml は起動時に読まれる。次に codex を起動すれば反映される。
        needs_restart=False,
    ),
}

DEFAULT_CLIENT = "claude-desktop"


def client_target(key=None) -> ClientTarget:
    try:
        return CLIENTS[key or DEFAULT_CLIENT]
    except KeyError:
        raise ValueError(
            f"未対応のクライアントです: {key}（{', '.join(CLIENTS)}）"
        ) from None
