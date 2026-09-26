"""In-app updater against the project's GitHub Releases.

The latest published release is read from the public REST API at
:data:`~paintmaskanimator.constants.UPDATE_RELEASE_API_URL` -- drafts and
pre-releases are excluded by that endpoint -- and the installer for the running
platform is downloaded from the release's assets. No credentials are involved.

Only the fields used here are read from the release::

    {
      "tag_name": "v0.6.6",
      "assets": [
        {"name": "PaintMaskAnimator-Setup-0.6.6.exe",
         "browser_download_url": "https://github.com/.../PaintMaskAnimator-Setup-0.6.6.exe"},
        {"name": "PaintMaskAnimator-0.6.6-macOS.dmg",
         "browser_download_url": "https://github.com/.../PaintMaskAnimator-0.6.6-macOS.dmg"}
      ]
    }

The pure helpers (version parsing/compare, asset picking) are unit tested; the
network functions are thin wrappers over urllib.
"""
import json
import re
import sys
import urllib.error
import urllib.request

from .constants import APP_VERSION, UPDATE_RELEASE_API_URL
from .i18n import tr
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
    """The installer platform of the running app: windows / macos / linux."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


_INSTALLER_SUFFIX = {"windows": ".exe", "macos": ".dmg"}


def pick_installer_asset(release, platform_key=None):
    """The installer asset of *release* for ``platform_key``, or ``None``.

    Returns ``{"name": ..., "url": ...}``. On Windows a ``Setup`` installer is
    preferred should a release ever carry more than one ``.exe``.
    """
    suffix = _INSTALLER_SUFFIX.get(platform_key or current_platform_key())
    assets = release.get("assets")
    if not suffix or not isinstance(assets, list):
        return None
    candidates = [
        asset for asset in assets
        if isinstance(asset, dict)
        and str(asset.get("name", "")).lower().endswith(suffix)
        and asset.get("browser_download_url")
    ]
    if not candidates:
        return None
    chosen = next(
        (a for a in candidates if "setup" in str(a["name"]).lower()),
        candidates[0],
    )
    return {"name": str(chosen["name"]), "url": str(chosen["browser_download_url"])}


def _request(url, accept):
    return urllib.request.Request(
        url, headers={"Accept": accept, "User-Agent": "PaintMaskAnimator-Updater"}
    )


def fetch_latest_release(url=UPDATE_RELEASE_API_URL):
    """Fetch the latest release as a dict. Raises urllib errors on failure."""
    request = _request(url, "application/vnd.github+json")
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def download_asset(asset, dest_path, progress=None):
    """Download an installer asset to ``dest_path``.

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


def check_for_update(release_url=UPDATE_RELEASE_API_URL, current_version=APP_VERSION):
    """Compare the latest release with ``current_version``. Returns a dict:

    {"status": "up_to_date" | "update_available" | "error",
     "latest": <version>, "release": <json>, "asset": <asset|None>,
     "message": <str for error>}
    """
    try:
        release = fetch_latest_release(release_url)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {"status": "error", "message": tr("公開されているリリースが見つかりませんでした。")}
        if error.code in (403, 429):
            # Unauthenticated API calls are limited to 60 per hour per address.
            return {
                "status": "error",
                "message": tr("GitHubへの問い合わせが多すぎます。しばらくしてから再度お試しください。"),
            }
        return {"status": "error", "message": tr("HTTPエラー: {code}").format(code=error.code)}
    except urllib.error.URLError as error:
        return {"status": "error", "message": tr("ネットワークエラー: {reason}").format(reason=error.reason)}
    except (ValueError, OSError) as error:
        log.warning("update check failed: %s", error, exc_info=True)
        return {"status": "error", "message": str(error)}
    if not isinstance(release, dict):
        return {"status": "error", "message": tr("リリース情報の形式が不正です。")}

    latest = str(release.get("tag_name") or "").lstrip("vV")
    if not is_newer(latest, current_version):
        return {"status": "up_to_date", "latest": latest, "release": release}
    return {
        "status": "update_available",
        "latest": latest,
        "release": release,
        "asset": pick_installer_asset(release),
    }
