"""Verify that a requested release version matches pyproject.toml.

Also keeps the README release badges honest: they are static shields
(the repository is private, so shields.io cannot read the real version)
and would otherwise silently drift a release behind.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path


def project_version() -> str:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


READMES = ("README.md", "README.en.md")


def stale_badges(version: str) -> list[str]:
    """READMEs whose release badge does not name *version*."""
    badge = f"release-v{version}-blue"
    stale = []
    for name in READMES:
        path = Path(name)
        if path.exists() and badge not in path.read_text(encoding="utf-8"):
            stale.append(name)
    return stale


def main() -> int:
    expected = sys.argv[1].removeprefix("v") if len(sys.argv) > 1 else ""
    actual = project_version()
    if expected and expected != actual:
        print(f"release version {expected!r} does not match pyproject version {actual!r}")
        return 1
    stale = stale_badges(actual)
    if stale:
        for name in stale:
            print(f"{name}: release badge does not show v{actual}")
        print(f"update the badge to `release-v{actual}-blue` in: {', '.join(stale)}")
        return 1
    print(actual)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
