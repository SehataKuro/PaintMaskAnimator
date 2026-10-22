"""Small per-user JSON config store (Qt-independent).

Holds lightweight preferences (theme, workspace layout, log level, action-panel
settings). Stored under the OS user-config directory so it survives reinstalls.
No secret is kept here; the file is still written owner-only in case one is
added later.
"""
import copy
import json
import os
from pathlib import Path
from typing import Any

from .constants import APP_NAME


def config_dir():
    """Return the per-user config directory for the app (created on demand)."""
    return _config_paths()[0]


def config_path():
    return _config_paths()[1]


def _config_paths():
    # 描画のたびに呼ばれるので、環境変数が同じなら組み立て済みのパスを使う。
    env = os.environ.get("APPDATA" if os.name == "nt" else "XDG_CONFIG_HOME")
    cached = _paths_by_env.get(env)
    if cached is None:
        if os.name == "nt":
            base = env or (Path.home() / "AppData" / "Roaming")
        else:
            base = env or (Path.home() / ".config")
        path = Path(base) / APP_NAME
        cached = (path, path / "config.json")
        _paths_by_env[env] = cached
    directory = cached[0]
    if directory not in _created_dirs:
        directory.mkdir(parents=True, exist_ok=True)
        _created_dirs.add(directory)
    return cached


# 設定はテーマの色などで描画のたびに読まれる（タイムラインはセル1つごと）。
# 毎回ファイルを開いて JSON を解析すると重いので、解析結果を覚えておく。
# ファイルの場所・更新時刻・サイズが変わったら読み直すので、外から書き換えても
# 反映される。
_created_dirs = set()
_paths_by_env = {}
_cache = {"key": None, "data": {}}


def _cached_config():
    path = config_path()
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if _cache["key"] != key:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _cache["key"] = key
        _cache["data"] = data
    return _cache["data"]


def load_config() -> Any:
    """Load the config dict; return {} if missing or unreadable."""
    # 呼び出し側が書き換えてもキャッシュを汚さないよう、複製を返す。
    return copy.deepcopy(_cached_config())


def save_config(data):
    """Persist the config dict atomically, owner-readable only."""
    path = config_path()
    # 起動中にフォルダーを消されても書き込めるように作り直す。
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # chmod is a no-op for practical purposes on Windows (and can fail on odd
    # filesystems); a failure here must not lose the user's settings.
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    _cache["key"] = None


def get_value(key, default=None) -> Any:
    value = _cached_config().get(key, default)
    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    return value


def set_value(key, value):
    data = load_config()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    save_config(data)
    return data
