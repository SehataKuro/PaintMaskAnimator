"""Tests for scripts/generate_download_indexes.build_indexes."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "generate_download_indexes",
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "generate_download_indexes.py",
)
assert _spec is not None and _spec.loader is not None
gdi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gdi)

BASE = "https://jokomanato.com/paintmaskanimator"


def _release(tag, names, prerelease=False, draft=False):
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": draft,
        "assets": [{"name": n} for n in names],
    }


def test_versions_newest_first_and_latest_stable():
    releases = [
        _release("v0.6", ["PaintMaskAnimator-Setup-0.6.exe"]),
        _release(
            "v0.6.3",
            ["PaintMaskAnimator-Setup-0.6.3.exe", "PaintMaskAnimator-0.6.3-macOS.dmg"],
        ),
        _release("v0.6.10", ["PaintMaskAnimator-Setup-0.6.10.exe"]),
    ]
    versions, updates = gdi.build_indexes(releases, BASE, "downloads")
    order = [v["version"] for v in versions["versions"]]
    assert order == ["0.6.10", "0.6.3", "0.6"]  # numeric, not lexicographic
    assert updates["version"] == "0.6.10"
    assert updates["assets"]["windows"]["url"] == (
        f"{BASE}/downloads/PaintMaskAnimator-Setup-0.6.10.exe"
    )


def test_prerelease_excluded_from_updates_but_listed():
    releases = [
        _release("v0.7.0", ["PaintMaskAnimator-Setup-0.7.0.exe"], prerelease=True),
        _release("v0.6.3", ["PaintMaskAnimator-Setup-0.6.3.exe"]),
    ]
    versions, updates = gdi.build_indexes(releases, BASE, "downloads")
    assert [v["version"] for v in versions["versions"]] == ["0.7.0", "0.6.3"]
    # Updater must not offer a prerelease as the latest stable.
    assert updates["version"] == "0.6.3"


def test_draft_and_assetless_releases_skipped():
    releases = [
        _release("v0.9.0", ["PaintMaskAnimator-Setup-0.9.0.exe"], draft=True),
        _release("v0.8.0", ["release-notes.txt"]),
        _release("v0.6.3", ["PaintMaskAnimator-Setup-0.6.3.exe"]),
    ]
    versions, updates = gdi.build_indexes(releases, BASE, "downloads")
    assert [v["version"] for v in versions["versions"]] == ["0.6.3"]
    assert updates["version"] == "0.6.3"


def test_empty_when_no_releases():
    versions, updates = gdi.build_indexes([], BASE, "downloads")
    assert versions == {"versions": []}
    assert updates == {"version": "", "assets": {}}
