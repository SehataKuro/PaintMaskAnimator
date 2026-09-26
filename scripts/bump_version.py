"""Bump the release version everywhere it is written down.

The version lives in pyproject.toml, the static README badges and CHANGELOG.md.
Editing them by hand means one is always forgotten, and the release workflow
only notices after the tag is pushed. This rewrites all of them at once::

    python scripts/bump_version.py 0.6.5              # dated today
    python scripts/bump_version.py 0.6.5 --date 2026-09-30

The CHANGELOG's 「未リリース」 section becomes the new version's section, a
fresh empty 「未リリース」 heading is left above it, and the tag link is added
to the link list at the bottom. An empty 「未リリース」 section is refused:
the release notes are generated from it.
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
READMES = ("README.md", "README.en.md")
UNRELEASED = "## [未リリース]"
RELEASE_URL = "https://github.com/SehataKuro/PaintMaskAnimator/releases/tag/v{version}"
_VERSION = re.compile(r"^\d+(?:\.\d+){1,2}$")


def bump_pyproject(text: str, version: str) -> str:
    new, count = re.subn(
        r'^(\[project\][^\[]*?^version\s*=\s*)"[^"]*"',
        rf'\g<1>"{version}"',
        text,
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )
    if count != 1:
        raise ValueError("pyproject.toml: [project] version not found")
    return new


def bump_badge(text: str, version: str) -> str:
    new, count = re.subn(r"release-v[0-9][0-9.]*-blue", f"release-v{version}-blue", text)
    if count == 0:
        raise ValueError("release badge not found")
    return new


def bump_changelog(text: str, version: str, date: str) -> str:
    start = text.find(UNRELEASED)
    if start < 0:
        raise ValueError(f"CHANGELOG.md: {UNRELEASED} heading not found")
    body_start = start + len(UNRELEASED)
    next_heading = re.compile(r"^## \[", re.MULTILINE).search(text, body_start)
    body_end = next_heading.start() if next_heading else len(text)
    if not text[body_start:body_end].strip():
        raise ValueError(f"CHANGELOG.md: {UNRELEASED} is empty; nothing to release")
    if re.search(rf"^## \[{re.escape(version)}\]", text, re.MULTILINE):
        raise ValueError(f"CHANGELOG.md: version {version} already exists")

    heading = f"{UNRELEASED}\n\n## [{version}] - {date}"
    text = text[:start] + heading + text[body_start:]

    link = f"[{version}]: {RELEASE_URL.format(version=version)}"
    first_link = re.compile(r"^\[[^\]]+\]:\s", re.MULTILINE).search(text)
    if first_link:
        text = text[: first_link.start()] + link + "\n" + text[first_link.start() :]
    else:
        text = text.rstrip("\n") + "\n\n" + link + "\n"
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", help="new version, e.g. 0.6.5")
    parser.add_argument("--date", default=datetime.date.today().isoformat(),
                        help="release date for the CHANGELOG heading (default: today)")
    args = parser.parse_args()
    version = args.version.removeprefix("v")
    if not _VERSION.match(version):
        parser.error(f"not a version number: {args.version!r}")

    edits = {}
    try:
        pyproject = ROOT / "pyproject.toml"
        edits[pyproject] = bump_pyproject(pyproject.read_text(encoding="utf-8"), version)
        for name in READMES:
            path = ROOT / name
            edits[path] = bump_badge(path.read_text(encoding="utf-8"), version)
        changelog = ROOT / "CHANGELOG.md"
        edits[changelog] = bump_changelog(changelog.read_text(encoding="utf-8"), version, args.date)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1

    # Nothing is written until every file has been rewritten successfully.
    for path, text in edits.items():
        path.write_text(text, encoding="utf-8")
        print(f"updated {path.relative_to(ROOT)}")
    print(
        "\nReinstall so the installed metadata matches (a test compares them):\n"
        "  pip install -e . --no-deps\n"
        f"Then: python scripts/preflight.py --release {version}, commit, "
        f"`git tag v{version}` and push the tag."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
