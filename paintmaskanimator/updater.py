"""In-app updater against a (private) GitHub repository's Releases.

The repo is private, so the GitHub API and release-asset downloads require a
Personal Access Token with read access. The user registers that token in the
app (Help > 更新を確認); it is stored via :mod:`config`. Nothing here is
hardcoded — no token ships with the app.

The pure helpers (version parsing/compare, release-JSON picking) are unit
tested; the network functions are thin wrappers over urllib.
"""
import json
import re
import urllib.error
import urllib.request

from .constants import APP_VERSION, GITHUB_REPO
from .logging_setup import get_logger

log = get_logger(__name__)

API_ROOT = "https://api.github.com"
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


def pick_installer_asset(release):
    """Choose the Windows installer asset from a release JSON dict.

    Prefers a '*Setup*.exe' asset, else the first '.exe', else None. Returns the
    asset dict (with 'name' and 'url' the API asset URL).
    """
    assets = release.get("assets") or []
    exes = [a for a in assets if str(a.get("name", "")).lower().endswith(".exe")]
    if not exes:
        return None
    for asset in exes:
        if "setup" in str(asset.get("name", "")).lower():
            return asset
    return exes[0]


def _request(url, token, accept):
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "PaintMaskAnimator-Updater",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def fetch_latest_release(token, repo=GITHUB_REPO):
    """Fetch the latest release JSON. Raises urllib errors on failure."""
    url = f"{API_ROOT}/repos/{repo}/releases/latest"
    request = _request(url, token, "application/vnd.github+json")
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def download_asset(asset, token, dest_path, progress=None):
    """Download a release asset (by its API url) to ``dest_path``.

    ``progress`` (optional) is called with (downloaded_bytes, total_bytes).
    """
    request = _request(asset["url"], token, "application/octet-stream")
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


def check_for_update(token, repo=GITHUB_REPO, current_version=APP_VERSION):
    """High-level check. Returns a dict describing the result:

    {"status": "up_to_date" | "update_available" | "error",
     "latest": <tag>, "release": <json>, "asset": <asset|None>,
     "message": <str for error>}
    """
    try:
        release = fetch_latest_release(token, repo)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            return {"status": "error", "message": "トークンが無効か、権限がありません。"}
        if error.code == 404:
            return {"status": "error", "message": "リリースが見つかりません。"}
        return {"status": "error", "message": f"HTTPエラー: {error.code}"}
    except urllib.error.URLError as error:
        return {"status": "error", "message": f"ネットワークエラー: {error.reason}"}
    except (ValueError, KeyError, OSError) as error:
        # Malformed release JSON (ValueError/KeyError) or other I/O issues not
        # already handled above; surface the message to the UI.
        log.warning("update check failed: %s", error, exc_info=True)
        return {"status": "error", "message": str(error)}

    latest = release.get("tag_name") or release.get("name") or ""
    if not is_newer(latest, current_version):
        return {"status": "up_to_date", "latest": latest, "release": release}
    return {
        "status": "update_available",
        "latest": latest,
        "release": release,
        "asset": pick_installer_asset(release),
    }
