"""Verify that a requested release version matches pyproject.toml."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path


def project_version() -> str:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def main() -> int:
    expected = sys.argv[1].removeprefix("v") if len(sys.argv) > 1 else ""
    actual = project_version()
    if expected and expected != actual:
        print(f"release version {expected!r} does not match pyproject version {actual!r}")
        return 1
    print(actual)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
