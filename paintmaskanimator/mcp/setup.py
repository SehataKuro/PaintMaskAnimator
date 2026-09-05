"""MCP クライアントへの登録を、ユーザーが手で JSON を書かずに済ませるための層。

MCP サーバー自体は動いても、**設定ファイルを自力で書けるかどうか**が実際の導入の
壁になる。作画のユーザーに「``%APPDATA%\\Claude`` の JSON を開いて、Windows の
バックスラッシュを二重にして、絶対パスを書いて、再起動して」と頼むのは現実的では
ない。ここはその手順をアプリ側に肩代わりさせるための部品を置く。

UI も ``mcp`` パッケージも要らない純粋な層なので、GUI なしで単体テストできる。
実際に効く設定を出すために、Python が本当にこのパッケージを import できるかを
**部分プロセスで確かめてから**構成を組み立てる（推測で書いた設定は静かに動かない）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..logging_setup import get_logger

__all__ = [
    "SERVER_KEY",
    "Check",
    "Diagnosis",
    "build_server_entry",
    "claude_code_command",
    "claude_desktop_config_path",
    "diagnose",
    "install_into_claude_desktop",
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


def diagnose(*, config_path=None) -> Diagnosis:
    """導入がどこで止まっているかを、実際に試して確かめる。

    「入れたのに動かない」の原因はほぼこの4つ（固めた実行ファイル・`mcp` 未導入・
    import できない・未登録）なので、推測ではなく部分プロセスで確認する。
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

    path = Path(config_path) if config_path is not None else claude_desktop_config_path()
    registered = is_registered(path)
    checks.append(
        Check(
            name="Claude Desktop への登録",
            ok=registered,
            detail=(
                f"登録済み: {path}"
                if registered
                else f"未登録（{path}{'' if path.exists() else ' は未作成'}）"
            ),
            hint="--install を実行するか、アプリの「MCP サーバー設定…」から登録してください。",
            # 登録は Claude Code など他のクライアントでも代替できるので、
            # これだけを理由に「駄目」とは言わない。
            blocking=False,
        )
    )

    checks.append(
        Check(
            name="claude コマンド",
            ok=shutil.which("claude") is not None,
            detail=(
                "見つかりました（claude mcp add が使えます）"
                if shutil.which("claude")
                else "見つかりません"
            ),
            hint="Claude Code を使う場合のみ必要です。",
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
