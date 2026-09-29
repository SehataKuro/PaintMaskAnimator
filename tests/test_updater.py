"""Unit tests for the updater's pure logic (no network)."""
import os
import urllib.error

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from paintmaskanimator import constants, updater  # noqa: E402

RELEASE = {
    "tag_name": "v0.9.0",
    "assets": [
        {"name": "notes.txt", "browser_download_url": "https://x/notes.txt"},
        {"name": "PaintMaskAnimator-0.9.0-macOS.dmg", "browser_download_url": "https://x/app.dmg"},
        {"name": "PaintMaskAnimator.exe", "browser_download_url": "https://x/app.exe"},
        {"name": "PaintMaskAnimator-Setup-0.9.0.exe", "browser_download_url": "https://x/setup.exe"},
    ],
}


def test_parse_version():
    assert updater.parse_version("v0.5.0") == (0, 5, 0)
    assert updater.parse_version("0.5") == (0, 5)
    assert updater.parse_version("V1.2.3-beta") == (1, 2, 3)
    assert updater.parse_version("") == ()
    assert updater.parse_version(None) == ()


def test_is_newer():
    assert updater.is_newer("0.6", "0.5") is True
    assert updater.is_newer("v0.5.1", "0.5") is True
    assert updater.is_newer("0.5", "0.5") is False
    assert updater.is_newer("0.5", "0.5.0") is False
    assert updater.is_newer("0.4.9", "0.5") is False
    assert updater.is_newer("1.0", "0.9.9") is True
    assert updater.is_newer("", "0.5") is False


def test_pick_installer_asset_prefers_the_setup_exe_on_windows():
    asset = updater.pick_installer_asset(RELEASE, platform_key="windows")
    assert asset == {"name": "PaintMaskAnimator-Setup-0.9.0.exe", "url": "https://x/setup.exe"}


def test_pick_installer_asset_picks_the_dmg_on_macos():
    asset = updater.pick_installer_asset(RELEASE, platform_key="macos")
    assert asset == {"name": "PaintMaskAnimator-0.9.0-macOS.dmg", "url": "https://x/app.dmg"}


def test_pick_installer_asset_without_a_match_returns_none():
    assert updater.pick_installer_asset(RELEASE, platform_key="linux") is None
    assert updater.pick_installer_asset({"assets": []}, platform_key="windows") is None
    assert updater.pick_installer_asset({}, platform_key="windows") is None
    no_url = {"assets": [{"name": "Setup.exe"}]}
    assert updater.pick_installer_asset(no_url, platform_key="windows") is None


def test_check_for_update_up_to_date(monkeypatch):
    monkeypatch.setattr(updater, "fetch_latest_release", lambda *a, **k: {"tag_name": "v0.5"})
    result = updater.check_for_update(current_version="0.5")
    assert result["status"] == "up_to_date"


def test_check_for_update_available(monkeypatch):
    monkeypatch.setattr(updater, "fetch_latest_release", lambda *a, **k: RELEASE)
    monkeypatch.setattr(updater, "current_platform_key", lambda: "windows")
    result = updater.check_for_update(current_version="0.5")
    assert result["status"] == "update_available"
    assert result["latest"] == "0.9.0"
    asset = result["asset"]
    assert isinstance(asset, dict)
    assert asset["url"] == "https://x/setup.exe"


def test_check_for_update_reads_the_public_latest_release(monkeypatch):
    captured = {}

    def fake_fetch(url=constants.UPDATE_RELEASE_API_URL):
        captured["url"] = url
        return {"tag_name": "v0.5"}

    monkeypatch.setattr(updater, "fetch_latest_release", fake_fetch)
    updater.check_for_update(current_version="0.5")
    assert captured["url"] == (
        f"https://api.github.com/repos/{constants.GITHUB_REPO}/releases/latest"
    )


def test_request_sends_no_credentials():
    request = updater._request(constants.UPDATE_RELEASE_API_URL, "application/json")
    assert request.get_header("Authorization") is None


def _raise(code):
    def boom(*_a, **_k):
        raise urllib.error.HTTPError("u", code, "error", {}, None)  # type: ignore[arg-type]
    return boom


def test_check_for_update_without_a_release(monkeypatch):
    monkeypatch.setattr(updater, "fetch_latest_release", _raise(404))
    result = updater.check_for_update()
    assert result["status"] == "error"
    assert "リリース" in result["message"]


def test_check_for_update_rate_limited(monkeypatch):
    monkeypatch.setattr(updater, "fetch_latest_release", _raise(403))
    result = updater.check_for_update()
    assert result["status"] == "error"
    assert "しばらく" in result["message"]


def test_check_for_update_maps_other_http_errors(monkeypatch):
    monkeypatch.setattr(updater, "fetch_latest_release", _raise(500))
    result = updater.check_for_update()
    assert result["status"] == "error"
    assert "500" in result["message"]
