"""Tests for scripts/generate_update_manifest.build_manifest."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "generate_update_manifest",
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "generate_update_manifest.py",
)
gum = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gum)


BASE = "https://jokomanato.com/paintmaskanimator"


def test_maps_platforms_to_public_download_urls():
    manifest = gum.build_manifest(
        "v0.6.0",
        [
            "PaintMaskAnimator-Setup-0.6.0.exe",
            "PaintMaskAnimator-0.6.0-macOS.dmg",
            "release-notes.txt",
        ],
        BASE,
        "downloads",
    )
    assert manifest["version"] == "0.6.0"  # 'v' prefix stripped
    assert manifest["assets"]["windows"] == {
        "name": "PaintMaskAnimator-Setup-0.6.0.exe",
        "url": f"{BASE}/downloads/PaintMaskAnimator-Setup-0.6.0.exe",
    }
    assert manifest["assets"]["macos"]["url"].endswith(
        "/downloads/PaintMaskAnimator-0.6.0-macOS.dmg"
    )
    # Non-installer files are ignored.
    assert set(manifest["assets"]) == {"windows", "macos"}


def test_setup_installer_wins_over_bare_exe():
    manifest = gum.build_manifest(
        "0.6.0",
        ["PaintMaskAnimator.exe", "PaintMaskAnimator-Setup-0.6.0.exe"],
        BASE,
        "downloads",
    )
    assert manifest["assets"]["windows"]["name"] == (
        "PaintMaskAnimator-Setup-0.6.0.exe"
    )


def test_empty_when_no_installers():
    manifest = gum.build_manifest("0.6.0", ["notes.txt"], BASE, "downloads")
    assert manifest["assets"] == {}
