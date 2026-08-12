"""Generate the public ``updates.json`` consumed by the in-app updater.

Given a version and a directory of built installer files, emit a manifest that
maps each platform to its installer's public URL under the project site's
download path. The download path is intentionally *outside* the site's
viewing-page password, so the updater needs no credentials.

Usage::

    python scripts/generate_update_manifest.py \
        --version 0.6.0 \
        --assets-dir release-assets \
        --base-url https://jokomanato.com/paintmaskanimator \
        --download-subdir downloads \
        --output updates.json
"""
from __future__ import annotations

import argparse
import json
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


def build_manifest(
    version: str,
    asset_names: list[str],
    base_url: str,
    download_subdir: str,
) -> dict:
    base = base_url.rstrip("/")
    subdir = download_subdir.strip("/")
    prefix = f"{base}/{subdir}" if subdir else base

    assets: dict[str, dict[str, str]] = {}
    for name in sorted(asset_names):
        platform = _classify(name)
        if platform is None:
            continue
        # A Setup installer wins over a bare app of the same platform.
        if platform in assets and "setup" not in name.lower():
            continue
        assets[platform] = {"name": name, "url": f"{prefix}/{name}"}

    return {"version": version.removeprefix("v"), "assets": assets}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--assets-dir", required=True)
    parser.add_argument(
        "--base-url", default="https://jokomanato.com/paintmaskanimator"
    )
    parser.add_argument("--download-subdir", default="downloads")
    parser.add_argument("--output", default="updates.json")
    args = parser.parse_args()

    assets_dir = Path(args.assets_dir)
    names = [p.name for p in assets_dir.iterdir() if p.is_file()]
    manifest = build_manifest(
        args.version, names, args.base_url, args.download_subdir
    )
    if not manifest["assets"]:
        print(f"warning: no installer assets found in {assets_dir}")

    Path(args.output).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
