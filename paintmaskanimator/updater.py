"""In-app updater against the public update feed on the project site.

The whole ``/paintmaskanimator/`` path — installers and the manifest at
:data:`~paintmaskanimator.constants.UPDATE_MANIFEST_URL` included — sits behind
the site's shared Basic-auth password. The updater authenticates automatically
with the shared credentials baked into :mod:`~paintmaskanimator.constants`
(``UPDATE_USERNAME``/``UPDATE_PASSWORD``), so the user never enters anything and
no per-user GitHub token is involved. A baked-in shared password is discoverable
in the distributed binary — this is casual-visitor deterrence, not a strong
secret.

Manifest shape (all URLs may be absolute or relative to the manifest)::

    {
      "version": "0.6.0",
      "assets": {
        "windows": {"name": "PaintMaskAnimator-Setup-0.6.0.exe",
                    "url": "downloads/PaintMaskAnimator-Setup-0.6.0.exe"},
        "macos":   {"name": "PaintMaskAnimator-0.6.0-macOS.dmg",
                    "url": "downloads/PaintMaskAnimator-0.6.0-macOS.dmg"}
      }
    }

The pure helpers (version parsing/compare, manifest asset picking) are unit
tested; the network functions are thin wrappers over urllib.
"""
import base64
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from .constants import (
    APP_VERSION,
    UPDATE_MANIFEST_URL,
    UPDATE_PASSWORD,
    UPDATE_USERNAME,
)
from .logging_setup import get_logger

log = get_logger(__name__)

_TIMEOUT = 15


def parse_version(text):
    """Parse a version string like 'v0.5.0' / '0.5' into a tuple of ints.

    Non-numeric suffixes (e.g. '1.2.0-beta') are ignored for the numeric part.
    Returns an empty tuple if nothing numeric is found.
    """
    if not text:
        return ()
    text = str(text).strip().lstrip("vV")
    numbers = re.findall(r"\d+", text)
    return tuple(int(n) for n in numbers)


def is_newer(latest, current):
    """True if version string ``latest`` is strictly newer than ``current``."""
    a = parse_version(latest)
    b = parse_version(current)
    if not a:
        return False
    length = max(len(a), len(b))
    a += (0,) * (length - len(a))
    b += (0,) * (length - len(b))
    return a > b


def current_platform_key():
    """Return the manifest asset key for the running platform."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def pick_installer_asset(manifest, platform_key=None):
    """Choose the installer asset for ``platform_key`` from a manifest dict.

    Returns an asset dict with at least a ``url`` (resolved to absolute against
    :data:`UPDATE_MANIFEST_URL`), or ``None`` when nothing matches. Falls back
    to a legacy GitHub-style ``assets`` list of ``*.exe`` entries so older
    manifests keep working.
    """
    platform_key = platform_key or current_platform_key()
    assets = manifest.get("assets")

    if isinstance(assets, dict):
        asset = assets.get(platform_key)
        if not asset:
            return None
        asset = dict(asset)
        asset["url"] = _resolve_url(asset.get("url"))
        return asset if asset["url"] else None

    # Legacy list form (GitHub release assets): pick a Windows installer.
    if isinstance(assets, list):
        exes = [
            a for a in assets
            if str(a.get("name", "")).lower().endswith(".exe")
        ]
        if not exes:
            return None
        chosen = next(
            (a for a in exes if "setup" in str(a.get("name", "")).lower()),
            exes[0],
        )
        chosen = dict(chosen)
        chosen["url"] = _resolve_url(chosen.get("url"))
        return chosen if chosen["url"] else None

    return None


def _resolve_url(url, base=UPDATE_MANIFEST_URL):
    """Resolve a possibly-relative manifest URL against the manifest location."""
    if not url:
        return ""
    return urllib.parse.urljoin(base, str(url))


def _auth_header():
    """Build the shared Basic-auth header, or {} when no password is set.

    Reads the module-level ``UPDATE_USERNAME``/``UPDATE_PASSWORD`` at call time.
    """
    if not UPDATE_PASSWORD:
        return {}
    token = base64.b64encode(f"{UPDATE_USERNAME}:{UPDATE_PASSWORD}".encode("utf-8"))
    return {"Authorization": f"Basic {token.decode('ascii')}"}


def _request(url, accept):
    headers = {"Accept": accept, "User-Agent": "PaintMaskAnimator-Updater"}
    headers.update(_auth_header())
    return urllib.request.Request(url, headers=headers)


def fetch_manifest(url=UPDATE_MANIFEST_URL):
    """Fetch and parse the update manifest. Raises urllib errors on failure."""
    request = _request(url, "application/json")
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def download_asset(asset, dest_path, progress=None):
    """Download an installer asset to ``dest_path`` (no credentials needed).

    ``progress`` (optional) is called with (downloaded_bytes, total_bytes).
    """
    request = _request(asset["url"], "application/octet-stream")
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        total = int(response.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest_path, "wb") as out:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, total)
    return dest_path


def check_for_update(
    manifest_url=UPDATE_MANIFEST_URL, current_version=APP_VERSION
):
    """High-level check against the public feed. Returns a dict:

    {"status": "up_to_date" | "update_available" | "error",
     "latest": <version>, "manifest": <json>, "asset": <asset|None>,
     "message": <str for error>}
    """
    try:
        manifest = fetch_manifest(manifest_url)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            return {
                "status": "error",
                "message": "更新サーバーの認証に失敗しました。アプリの更新用"
                "パスワードがサイト側の設定と一致していない可能性があります。",
            }
        if error.code == 404:
            return {
                "status": "error",
                "message": "更新情報が見つかりませんでした（updates.json 未公開）。",
            }
        return {"status": "error", "message": f"HTTPエラー: {error.code}"}
    except urllib.error.URLError as error:
        return {"status": "error", "message": f"ネットワークエラー: {error.reason}"}
    except (ValueError, KeyError, OSError) as error:
        # Malformed manifest JSON (ValueError/KeyError) or other I/O issues;
        # surface the message to the UI.
        log.warning("update check failed: %s", error, exc_info=True)
        return {"status": "error", "message": str(error)}

    latest = manifest.get("version") or manifest.get("tag_name") or ""
    if not is_newer(latest, current_version):
        return {"status": "up_to_date", "latest": latest, "manifest": manifest}
    return {
        "status": "update_available",
        "latest": latest,
        "manifest": manifest,
        "asset": pick_installer_asset(manifest),
    }
