"""Build ``versions.json`` and ``updates.json`` from GitHub Releases metadata.

Installers are stored in R2 and served at ``<base>/downloads/<file>``, so the
indexes are derived from the *list of releases* (the authoritative record of
which versions exist) rather than from files in the repo. Feed the releases
JSON from ``gh``/the API on stdin::

    gh api repos/OWNER/REPO/releases --paginate \
        | python scripts/generate_download_indexes.py \
            --base-url https://jokomanato.com/paintmaskanimator \
            --download-subdir downloads \
            --versions-output versions.json \
            --updates-output updates.json

``versions.json`` lists every release (newest first); ``updates.json`` points
at the latest non-draft, non-prerelease version for the in-app updater.
"""
from __future__ import annotations

import argparse
import json
import re
import sys


def _classify(name: str) -> str | None:
    lower = name.lower()
    if lower.endswith(".exe"):
        return "windows"
    if lower.endswith(".dmg"):
        return "macos"
    if lower.endswith((".appimage", ".tar.gz")):
        return "linux"
    return None


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version))


def _assets_for(release: dict, prefix: str) -> dict[str, dict[str, str]]:
    assets: dict[str, dict[str, str]] = {}
    for asset in release.get("assets") or []:
        name = str(asset.get("name", ""))
        platform = _classify(name)
        if platform is None:
            continue
        if platform in assets and "setup" not in name.lower():
            continue
        assets[platform] = {"name": name, "url": f"{prefix}/{name}"}
    return assets


def build_indexes(
    releases: list[dict],
    base_url: str,
    download_subdir: str,
) -> tuple[dict, dict]:
    base = base_url.rstrip("/")
    subdir = download_subdir.strip("/")
    prefix = f"{base}/{subdir}" if subdir else base

    entries = []
    for release in releases:
        if release.get("draft"):
            continue
        tag = str(release.get("tag_name") or release.get("name") or "")
        version = tag.lstrip("vV")
        assets = _assets_for(release, prefix)
        if not version or not assets:
            continue
        entries.append(
            {
                "version": version,
                "prerelease": bool(release.get("prerelease")),
                "notes": str(release.get("body") or "").strip(),
                "assets": assets,
            }
        )

    entries.sort(key=lambda e: _version_key(e["version"]), reverse=True)

    versions = {
        "versions": [
            {
                "version": e["version"],
                "notes": e["notes"],
                "assets": e["assets"],
            }
            for e in entries
        ]
    }

    latest = next((e for e in entries if not e["prerelease"]), None)
    updates = (
        {"version": latest["version"], "assets": latest["assets"]}
        if latest
        else {"version": "", "assets": {}}
    )
    return versions, updates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default="https://jokomanato.com/paintmaskanimator"
    )
    parser.add_argument("--download-subdir", default="downloads")
    parser.add_argument("--versions-output", default="versions.json")
    parser.add_argument("--updates-output", default="updates.json")
    parser.add_argument(
        "--input",
        help="Releases JSON file (default: stdin).",
    )
    args = parser.parse_args()

    raw = (
        open(args.input, encoding="utf-8").read()
        if args.input
        else sys.stdin.read()
    )
    releases = json.loads(raw)
    versions, updates = build_indexes(
        releases, args.base_url, args.download_subdir
    )

    with open(args.versions_output, "w", encoding="utf-8") as handle:
        json.dump(versions, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with open(args.updates_output, "w", encoding="utf-8") as handle:
        json.dump(updates, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"versions: {[v['version'] for v in versions['versions']]}")
    print(f"latest:   {updates['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
