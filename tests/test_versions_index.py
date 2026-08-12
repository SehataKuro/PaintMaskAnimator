"""Tests for scripts/generate_versions_index.build_index."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "generate_versions_index",
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "generate_versions_index.py",
)
assert _spec is not None and _spec.loader is not None
gvi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gvi)

BASE = "https://jokomanato.com/paintmaskanimator"


def test_groups_by_version_newest_first():
    index = gvi.build_index(
        [
            "PaintMaskAnimator-Setup-0.6.0.exe",
            "PaintMaskAnimator-0.6.0-macOS.dmg",
            "PaintMaskAnimator-Setup-0.6.1.exe",
            "PaintMaskAnimator-Setup-0.10.0.exe",
        ],
        BASE,
        "downloads",
    )
    order = [v["version"] for v in index["versions"]]
    # Numeric sort: 0.10.0 > 0.6.1 > 0.6.0 (not lexicographic).
    assert order == ["0.10.0", "0.6.1", "0.6.0"]
    v060 = index["versions"][2]
    assert set(v060["assets"]) == {"windows", "macos"}
    assert v060["assets"]["windows"]["url"] == (
        f"{BASE}/downloads/PaintMaskAnimator-Setup-0.6.0.exe"
    )


def test_setup_installer_wins_and_non_installers_ignored():
    index = gvi.build_index(
        [
            "PaintMaskAnimator-0.6.0.exe",
            "PaintMaskAnimator-Setup-0.6.0.exe",
            "release-notes-0.6.0.txt",
        ],
        BASE,
        "downloads",
    )
    assert len(index["versions"]) == 1
    assert index["versions"][0]["assets"]["windows"]["name"] == (
        "PaintMaskAnimator-Setup-0.6.0.exe"
    )


def test_empty_when_no_installers():
    assert gvi.build_index(["notes.txt"], BASE, "downloads") == {"versions": []}
