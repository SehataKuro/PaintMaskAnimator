"""Generate ``versions.json`` — the full download history for the site.

Scans the site's ``downloads/`` directory (which accumulates every published
installer) and emits one entry per version, newest first, so the download page
can list past versions alongside the latest. Because it is derived from the
files actually present, it never drifts from what is really hosted.

Usage::

    python scripts/generate_versions_index.py \
        --downloads-dir website/public/paintmaskanimator/downloads \
        --base-url https://jokomanato.com/paintmaskanimator \
        --download-subdir downloads \
        --output website/public/paintmaskanimator/versions.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _classify(name: str) -> str | None:
    lower = name.lower()
    if lower.endswith(".exe"):
        return "windows"
    if lower.endswith(".dmg"):
        return "macos"
    if lower.endswith((".appimage", ".tar.gz")):
        return "linux"
    return None


def _version_of(name: str) -> str | None:
    """Extract a dotted version (e.g. '0.6.1') from an installer file name."""
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", name)
    return match.group(1) if match else None


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version))


def build_index(
    file_names: list[str],
    base_url: str,
    download_subdir: str,
) -> dict:
    base = base_url.rstrip("/")
    subdir = download_subdir.strip("/")
    prefix = f"{base}/{subdir}" if subdir else base

    by_version: dict[str, dict[str, dict[str, str]]] = {}
    for name in file_names:
        platform = _classify(name)
        version = _version_of(name)
        if platform is None or version is None:
            continue
        assets = by_version.setdefault(version, {})
        # A Setup installer wins over a bare app of the same platform/version.
        if platform in assets and "setup" not in name.lower():
            continue
        assets[platform] = {"name": name, "url": f"{prefix}/{name}"}

    versions = [
        {"version": version, "assets": by_version[version]}
        for version in sorted(by_version, key=_version_key, reverse=True)
    ]
    return {"versions": versions}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--downloads-dir", required=True)
    parser.add_argument(
        "--base-url", default="https://jokomanato.com/paintmaskanimator"
    )
    parser.add_argument("--download-subdir", default="downloads")
    parser.add_argument("--output", default="versions.json")
    args = parser.parse_args()

    downloads_dir = Path(args.downloads_dir)
    names = [p.name for p in downloads_dir.iterdir() if p.is_file()]
    index = build_index(names, args.base_url, args.download_subdir)
    if not index["versions"]:
        print(f"warning: no installer files found in {downloads_dir}")

    Path(args.output).write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(index, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
