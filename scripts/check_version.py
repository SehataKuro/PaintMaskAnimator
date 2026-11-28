"""Verify that a requested release version matches pyproject.toml.

Also keeps the README release badges honest: they are static shields
(the repository is private, so shields.io cannot read the real version)
and would otherwise silently drift a release behind.

Given a release version, it also requires a non-empty CHANGELOG section for
it. The release notes are built from that section, but only in the final
publish job -- without this check a missing entry is found after both
installers have already been built.

With ``--app`` the version is read from ``app/Cargo.toml`` instead: the Tauri
app (0.9.0 and later) takes its version from there (tauri.conf.json has none,
so the bundle, the installer and the About box all follow Cargo.toml).

``scripts/bump_version.py`` updates all of these in one go.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path


def project_version() -> str:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def app_version() -> str:
    data = tomllib.loads(Path("app/Cargo.toml").read_text(encoding="utf-8"))
    return str(data["package"]["version"])


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


def changelog_problem(version: str) -> str | None:
    """Why CHANGELOG.md cannot supply release notes for *version*, if it can't."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from extract_release_notes import extract_release_notes

    try:
        extract_release_notes(Path("CHANGELOG.md").read_text(encoding="utf-8"), version)
    except (OSError, ValueError) as error:
        return str(error)
    return None


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--app"]
    app = "--app" in sys.argv[1:]
    expected = args[0].removeprefix("v") if args else ""
    actual = app_version() if app else project_version()
    source = "app/Cargo.toml" if app else "pyproject"
    if expected and expected != actual:
        print(f"release version {expected!r} does not match {source} version {actual!r}")
        print(f"run: python scripts/bump_version.py {expected}")
        return 1
    stale = stale_badges(actual)
    if stale:
        for name in stale:
            print(f"{name}: release badge does not show v{actual}")
        print(f"update the badge to `release-v{actual}-blue` in: {', '.join(stale)}")
        return 1
    if expected:
        problem = changelog_problem(expected)
        if problem:
            print(problem)
            print(f"run: python scripts/bump_version.py {expected}")
            return 1
    print(actual)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
