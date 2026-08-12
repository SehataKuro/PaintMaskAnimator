"""Unit tests for the updater's pure logic and the config store (no network)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from paintmaskanimator import constants, updater  # noqa: E402


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


def test_pick_installer_asset_selects_platform_and_resolves_relative_url():
    manifest = {
        "version": "0.6.0",
        "assets": {
            "windows": {
                "name": "PaintMaskAnimator-Setup-0.6.0.exe",
                "url": "downloads/PaintMaskAnimator-Setup-0.6.0.exe",
            },
            "macos": {"name": "app.dmg", "url": "downloads/app.dmg"},
        },
    }
    asset = updater.pick_installer_asset(manifest, platform_key="windows")
    assert asset is not None
    assert asset["name"] == "PaintMaskAnimator-Setup-0.6.0.exe"
    # Relative URL resolves against the manifest location.
    assert asset["url"].startswith(constants.UPDATE_BASE_URL)
    assert asset["url"].endswith("/downloads/PaintMaskAnimator-Setup-0.6.0.exe")


def test_pick_installer_asset_absolute_url_preserved():
    manifest = {
        "assets": {"macos": {"name": "a.dmg", "url": "https://cdn.example/a.dmg"}}
    }
    asset = updater.pick_installer_asset(manifest, platform_key="macos")
    assert asset["url"] == "https://cdn.example/a.dmg"


def test_pick_installer_asset_missing_platform_returns_none():
    manifest = {"assets": {"windows": {"url": "downloads/x.exe"}}}
    assert updater.pick_installer_asset(manifest, platform_key="linux") is None


def test_pick_installer_asset_legacy_list_form():
    # Older GitHub-style manifests (a list of *.exe assets) still work.
    manifest = {
        "assets": [
            {"name": "notes.txt", "url": "https://x/notes.txt"},
            {"name": "PaintMaskAnimator.exe", "url": "https://x/app.exe"},
            {"name": "PaintMaskAnimator-Setup-0.6.exe", "url": "https://x/setup.exe"},
        ]
    }
    asset = updater.pick_installer_asset(manifest, platform_key="windows")
    assert asset is not None
    assert asset["url"] == "https://x/setup.exe"


def test_check_for_update_up_to_date(monkeypatch):
    monkeypatch.setattr(
        updater, "fetch_manifest", lambda *a, **k: {"version": "0.5"}
    )
    result = updater.check_for_update(current_version="0.5")
    assert result["status"] == "up_to_date"


def test_check_for_update_available(monkeypatch):
    manifest = {
        "version": "0.9",
        "assets": {"windows": {"name": "s.exe", "url": "downloads/s.exe"}},
    }
    monkeypatch.setattr(updater, "fetch_manifest", lambda *a, **k: manifest)
    monkeypatch.setattr(updater, "current_platform_key", lambda: "windows")
    result = updater.check_for_update(current_version="0.5")
    assert result["status"] == "update_available"
    assert result["latest"] == "0.9"
    assert result["asset"]["url"].endswith("/downloads/s.exe")


def test_check_for_update_needs_no_user_token(monkeypatch):
    # No per-user token: check_for_update takes no token argument and just hits
    # the fixed manifest URL. Credentials (if any) are the baked-in shared ones.
    captured = {}

    def fake_fetch(url=constants.UPDATE_MANIFEST_URL):
        captured["url"] = url
        return {"version": "0.5"}

    monkeypatch.setattr(updater, "fetch_manifest", fake_fetch)
    updater.check_for_update(current_version="0.5")
    assert captured["url"] == constants.UPDATE_MANIFEST_URL


def test_request_sends_shared_basic_auth(monkeypatch):
    import base64

    monkeypatch.setattr(updater, "UPDATE_USERNAME", "guest")
    monkeypatch.setattr(updater, "UPDATE_PASSWORD", "s3cret")
    request = updater._request("https://x/updates.json", "application/json")
    header = request.get_header("Authorization")
    assert header is not None
    scheme, _, value = header.partition(" ")
    assert scheme == "Basic"
    assert base64.b64decode(value).decode() == "guest:s3cret"


def test_request_omits_auth_when_no_password(monkeypatch):
    monkeypatch.setattr(updater, "UPDATE_PASSWORD", "")
    request = updater._request("https://x/updates.json", "application/json")
    assert request.get_header("Authorization") is None


def test_check_for_update_maps_auth_failure(monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(updater, "fetch_manifest", boom)
    result = updater.check_for_update()
    assert result["status"] == "error"
    assert "認証" in result["message"]


def test_check_for_update_maps_http_error(monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    monkeypatch.setattr(updater, "fetch_manifest", boom)
    result = updater.check_for_update()
    assert result["status"] == "error"


def test_check_for_update_maps_network_error(monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(updater, "fetch_manifest", boom)
    result = updater.check_for_update()
    assert result["status"] == "error"
    assert "ネットワーク" in result["message"]


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from paintmaskanimator import config
    config.set_value("some_key", "abc123")
    assert config.get_value("some_key") == "abc123"
    config.set_value("some_key", None)
    assert config.get_value("some_key") is None
    assert config.get_value("missing", "def") == "def"
