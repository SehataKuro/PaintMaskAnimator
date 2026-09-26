"""Small per-user JSON config store (Qt-independent).

Holds lightweight preferences (theme, workspace layout, log level, action-panel
settings). Stored under the OS user-config directory so it survives reinstalls.
No secret is kept here; the file is still written owner-only in case one is
added later.
"""
import json
import os
from pathlib import Path

from .constants import APP_NAME


def config_dir():
    """Return the per-user config directory for the app (created on demand)."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path():
    return config_dir() / "config.json"


def load_config():
    """Load the config dict; return {} if missing or unreadable."""
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(data):
    """Persist the config dict atomically, owner-readable only."""
    path = config_path()
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


def get_value(key, default=None):
    return load_config().get(key, default)


def set_value(key, value):
    data = load_config()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    save_config(data)
    return data
