"""Unit tests for the updater's pure logic and the config store (no network)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from paintmaskanimator import updater  # noqa: E402


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


def test_pick_installer_asset_prefers_setup():
    release = {
        "assets": [
            {"name": "notes.txt", "url": "u0"},
            {"name": "PaintMaskAnimator.exe", "url": "u1"},
            {"name": "PaintMaskAnimator-Setup-0.6.exe", "url": "u2"},
        ]
    }
    assert updater.pick_installer_asset(release)["url"] == "u2"


def test_pick_installer_asset_falls_back_to_exe():
    release = {"assets": [{"name": "app.exe", "url": "u1"}]}
    assert updater.pick_installer_asset(release)["url"] == "u1"


def test_pick_installer_asset_none():
    assert updater.pick_installer_asset({"assets": [{"name": "x.txt"}]}) is None
    assert updater.pick_installer_asset({}) is None


def test_check_for_update_maps_auth_error(monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

    monkeypatch.setattr(updater, "fetch_latest_release", boom)
    result = updater.check_for_update("bad-token")
    assert result["status"] == "error"
    assert result.get("auth_error") is True


def test_check_for_update_404_not_auth(monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    monkeypatch.setattr(updater, "fetch_latest_release", boom)
    result = updater.check_for_update("tok")
    assert result["status"] == "error"
    assert not result.get("auth_error")


def test_check_for_update_up_to_date(monkeypatch):
    monkeypatch.setattr(
        updater, "fetch_latest_release", lambda *a, **k: {"tag_name": "v0.5"}
    )
    result = updater.check_for_update("tok", current_version="0.5")
    assert result["status"] == "up_to_date"


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from paintmaskanimator import config
    config.set_value("github_token", "abc123")
    assert config.get_value("github_token") == "abc123"
    config.set_value("github_token", None)
    assert config.get_value("github_token") is None
    assert config.get_value("missing", "def") == "def"
